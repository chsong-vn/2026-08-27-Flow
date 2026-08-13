# -*- coding: utf-8 -*-
"""데드볼륨 실측 캘리브레이션 — 센서 브레이크스루 역산 + 랜드마크 차분 + N2 blow-out.

목적 (사용자 지시 2026-08-12):
 1. 유체가 원하는 데드볼륨 위치(OPB 위상센서)까지 '정확히' 도달하는지 확인하고,
    다음 실험을 위해 N2 로 반응기 내부를 완전히 blow-out.
 2. 각 배관 구간의 실제 부피를 실측 — A 배관, A+B 배관처럼 유체 선단이 어디까지
    도달하는지 누적으로 재고, 차분해서 구간별 부피를 얻는다.

원리 (개루프 — 시린지 위치센서 없음):
  정속 주입이면 펌프 토출 부피 = rate(mL/min) × 경과(s) / 60.
  N2 로 라인을 '가스 충전(퍼지)'한 상태에서 주입을 시작하면, 유체 선단이
  랜드마크(센서/육안 지점)에 도달하는 순간의 누적 토출 부피 = 주입점→그 지점의
  '실제 데드볼륨'. 여러 랜드마크를 누적으로 찍으면 (A, A+B, A+B+C ...) 인접 차분이
  각 구간(B, C ...) 부피가 된다. clock 은 monotonic (NTP 무관, 엔진 P2 와 동일).

모드:
  breakthrough : OPB 센서 가스→액체 전이 자동 감지 → 주입점→센서 데드볼륨 1점.
  marks        : 육안 랜드마크마다 ENTER → 누적 부피 기록, 인접 차분=구간 부피.
                 (센서가 못 보는 QUAD-1/QUAD-2 같은 중간 지점을 눈으로 찍을 때)
  blowout      : 주입 후(또는 단독) N2 정속 퍼지 → 센서가 GAS 로 '안정 복귀'할
                 때까지 = 반응기/라인 완전 배출 검증.

실행:
  py -3.14 tools/calibrate_deadvol.py --sim                 # 무하드웨어 자체검증(+회귀)
  py -3.14 tools/calibrate_deadvol.py --channel "Group A" --prime-cycles 2 \
        --blow-at-end                                       # 첫 측정: 프라임+자동충전+누적1패스
  py -3.14 tools/calibrate_deadvol.py --channel "Group_B" --blow-at-end
  py -3.14 tools/calibrate_deadvol.py --channel "Group A" --source-port 2 \
        --rate 0.5 --mode breakthrough --sensor collect --blowout
  py -3.14 tools/calibrate_deadvol.py --channel "Group A" --source-port 2 \
        --rate 0.3 --mode marks --labels "QUAD1,QUAD2,SENSOR,REACTOR"

시린지 준비 (segment 모드 — 기존 운전 로직 재사용, 2026-08-13):
  WITHDRAW 충전은 ChemyxSmartPump.refill() 그대로 — 12way→소스포트+3way→SOURCE 로
  흡인 후 3way→REACTOR 복원. --prime-cycles N 은 [withdraw→포트12 배출] 사이클로
  소스라인(~1.98mL)의 가스/구용액 제거(측정 0점 전제 = 시린지·커먼라인 기포 없는
  측정액). --charge auto 는 추정합×2+0.5 충전, 잔량 추적·소진 경고, 측정 중 'r' 로
  재충전(3-way 격리라 선단·누적 불변). 시작 시 플런저 '빈' 상태 확인 후 0점.

산출: logs/DEADVOL_<타임스탬프>.csv (연속 로그) + 콘솔 리포트(구간별 mL + config 대조).
안전: 조회/정속주입만. 실측 전 라인을 N2 로 퍼지(가스 충전)해 두어야 브레이크스루가
     깨끗하다. abort=Ctrl-C → 펌프·MFC 정지.
"""
import argparse
import csv
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════
# 순수 계산 — 하드웨어 무의존 (자체검증 대상)
# ══════════════════════════════════════════════════════════════════════
def volume_ml(rate_ml_min, elapsed_s):
    """정속 주입 누적 부피 (개루프): rate × 경과 / 60."""
    return float(rate_ml_min) * float(elapsed_s) / 60.0


def segments_from_marks(marks):
    """누적 랜드마크 부피 리스트 → 구간별 차분.
    marks = [(label, cum_ml), ...] (누적 오름차순). 반환 [(label, seg_ml, cum_ml)]."""
    out = []
    prev = 0.0
    for label, cum in marks:
        seg = max(0.0, float(cum) - prev)
        out.append((label, seg, float(cum)))
        prev = float(cum)
    return out


def breakthrough_volume(rate_ml_min, t_start, t_detect):
    """가스→액체 전이 시각에서 주입점→센서 데드볼륨(mL)."""
    return volume_ml(rate_ml_min, max(0.0, t_detect - t_start))


def push_bounded(rate, target_ml, pump=None, sim=None, writer=None,
                 read_phase=None, t0_ref=None):
    """정확히 target_ml 만큼 정속 주입 후 정지 (동기 — 밀고 멈춤). 반환 실제 주입량.

    마지막 스텝은 남은 부피의 소요시간만큼만 sleep 해 목표에 근접 착지(과주입 최소).
    개루프라 목표 초과는 poll 한 틱 이내로 억제된다."""
    target_ml = float(target_ml)
    if target_ml <= 0:
        return 0.0
    if pump is not None:
        pump.set_flow(rate)
        pump.start()
    injected = 0.0
    last = time.monotonic()
    t0 = t0_ref if t0_ref is not None else last
    try:
        while injected < target_ml - 1e-9:
            rem_s = (target_ml - injected) / max(rate, 1e-6) * 60.0
            time.sleep(min(0.05, max(0.0, rem_s)))
            now = time.monotonic()
            dv = volume_ml(rate, now - last)
            last = now
            injected += dv
            if sim is not None:
                sim.add_pump(dv)
            if writer is not None:
                try:
                    writer.writerow([round(now - t0, 3), round(injected, 5), "push",
                                     (read_phase() if read_phase else "?")])
                except Exception:
                    pass
    finally:
        if pump is not None:
            try:
                pump.stop()
            except Exception:
                pass
    return injected


# ══════════════════════════════════════════════════════════════════════
# 시뮬 모델 — 유체 선단이 config 데드볼륨을 지나 센서에 '도달'하는 합성 물리
# ══════════════════════════════════════════════════════════════════════
class _SimRig:
    """무하드웨어 검증용: 주입 부피가 true_dv 를 넘으면 센서=액체, N2 퍼지가
    purge_dv 를 넘으면 센서=가스. 도구의 역산이 true_dv 를 회복하는지 자체검증."""

    def __init__(self, true_dv_ml, purge_dv_ml=None):
        self.true_dv = float(true_dv_ml)
        self.purge_dv = float(purge_dv_ml if purge_dv_ml is not None else true_dv_ml)
        self._pumped = 0.0      # 액체 주입 누적 (mL)
        self._purged = 0.0      # N2 퍼지 누적 (mL 등가)
        self._front_liquid = False

    def add_pump(self, dv):
        self._pumped += dv
        self._purged = 0.0
        if self._pumped >= self.true_dv:
            self._front_liquid = True

    def add_purge(self, dv):
        self._purged += dv
        if self._purged >= self.purge_dv:
            self._front_liquid = False
            self._pumped = 0.0

    def read_phase(self, which=None):
        return "CLEAR_LIQUID" if self._front_liquid else "GAS"


# ══════════════════════════════════════════════════════════════════════
# 라이브 하드웨어 래핑 (best-effort — 리그 안정 시)
# ══════════════════════════════════════════════════════════════════════
def _load_live(channel, source_port):
    """SystemConfig+HardwareManager 로 펌프/센서/MFC/밸브 확보. 실패 시 예외."""
    from engine.config import SystemConfig
    from core.hw_manager import HardwareManager
    cfg = SystemConfig()
    hw = HardwareManager(cfg)
    hw.init_hw()
    pump = (hw.pumps or {}).get(channel)
    if pump is None:
        raise RuntimeError(f"펌프 '{channel}' 없음 — 채널명 확인 (있는 것: {list((hw.pumps or {}))})")
    sensor = getattr(hw, "phase_sensor", None)
    mfc = getattr(hw, "mfc", None)
    return cfg, hw, pump, sensor, mfc


def _expected_dv_to_sensor(cfg, channel):
    """config 상 주입점→센서 예상 데드볼륨(참고): line_inj + tj(진입~마지막) +
    mixing 의 센서 앞단. 정확 분해는 원장이 담당 — 여기선 총합 근사만."""
    try:
        li = float(getattr(cfg, "line_vol_pump_merge", {}).get(channel, 0.0) or 0.0)
        li += float(getattr(cfg, "valve_internal_vol", {}).get(channel, 0.0) or 0.0)
        tj = sum(float(v or 0.0) for v in (getattr(cfg, "tjunction_line_vols", {}) or {}).values())
        mix = float(getattr(cfg, "mixing_line_dead_vol", 0.0) or 0.0)
        # 센서는 mixing 앞단(가스T→센서)에 있으므로 mixing 전체가 아니라 일부.
        return li + tj + mix, {"line_inj": li, "tjunction합": tj, "mixing(전체)": mix}
    except Exception:
        return None, {}


# ══════════════════════════════════════════════════════════════════════
# 주입 엔진 (라이브/시뮬 공용) — 배경 스레드로 정속 주입, 부피 카운터 공유
# ══════════════════════════════════════════════════════════════════════
class Injector:
    def __init__(self, rate_ml_min, pump=None, sim=None, writer=None, log_phase=None):
        self.rate = float(rate_ml_min)
        self.pump = pump
        self.sim = sim
        self.writer = writer
        self.log_phase = log_phase or (lambda: "?")
        self._t0 = None
        self._last = None
        self._vol = 0.0
        self._stop = threading.Event()
        self._thr = None

    @property
    def volume(self):
        return self._vol

    def start(self):
        if self.pump is not None:
            self.pump.set_flow(self.rate)
            self.pump.start()
        self._t0 = time.monotonic()
        self._last = self._t0
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()

    def _run(self):
        while not self._stop.is_set():
            now = time.monotonic()
            dv = volume_ml(self.rate, now - self._last)
            self._last = now
            self._vol += dv
            if self.sim is not None:
                self.sim.add_pump(dv)
            if self.writer is not None:
                try:
                    self.writer.writerow([round(now - self._t0, 3), round(self._vol, 5),
                                          "inject", self.log_phase()])
                except Exception:
                    pass
            time.sleep(0.05)

    def stop(self):
        self._stop.set()
        if self._thr:
            self._thr.join(timeout=1.0)
        if self.pump is not None:
            try:
                self.pump.stop()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
# 모드 실행
# ══════════════════════════════════════════════════════════════════════
def run_breakthrough(inj, read_phase, timeout_s, poll=0.05):
    """정속 주입하며 가스→액체 전이 대기 → 브레이크스루 부피 반환(None=타임아웃)."""
    t0 = time.monotonic()
    inj.start()
    try:
        while time.monotonic() - t0 < timeout_s:
            ph = read_phase()
            if ph != "GAS":
                return inj.volume
            time.sleep(poll)
        return None
    finally:
        inj.stop()


def run_marks(inj, labels, read_input=input):
    """정속 주입하며 육안 랜드마크마다 마크. labels 순서대로 ENTER 를 받는다.
    반환 [(label, cum_ml)]. 빈 입력=마크, 'q'=조기 종료."""
    marks = []
    inj.start()
    try:
        for lab in labels:
            s = read_input(f"  [{lab}] 도달하면 ENTER (q=종료) ... ")
            if str(s).strip().lower() == "q":
                break
            marks.append((lab, inj.volume))
            print(f"    → {lab}: 누적 {inj.volume:.4f} mL")
    finally:
        inj.stop()
    return marks


def run_blowout(mfc, read_phase, sccm, max_s, stable_s=3.0, sim=None, poll=0.2,
                sim_purge_rate=None):
    """N2 정속 퍼지 → 센서가 GAS 로 stable_s 연속 유지되면 '완전 배출' 판정.
    반환 (성공?, 소요s). sim 모드는 sim_purge_rate(mL/s 등가)로 선단을 되민다."""
    if mfc is not None:
        try:
            mfc.set_flow(float(sccm))
        except Exception as e:
            print(f"  ⚠ MFC set_flow 실패: {e}")
    t0 = time.monotonic()
    gas_since = None
    last = t0
    try:
        while time.monotonic() - t0 < max_s:
            now = time.monotonic()
            if sim is not None:
                sim.add_purge((sim_purge_rate or 0.0) * (now - last))
            last = now
            ph = read_phase()
            if ph == "GAS":
                if gas_since is None:
                    gas_since = now
                elif now - gas_since >= stable_s:
                    return True, now - t0
            else:
                gas_since = None
            time.sleep(poll)
        return False, time.monotonic() - t0
    finally:
        if mfc is not None:
            try:
                mfc.stop()
            except Exception:
                try:
                    mfc.set_flow(0.0)
                except Exception:
                    pass


def _manual_and_blow(mfc, read_phase, blow_cfg, sim, read_input, manual_clear):
    """세그먼트 확정 후: 수동 데드레그 배출(손) → N2 blow-out. 다음 세그먼트 준비."""
    if manual_clear:
        read_input("  N2 로 못 미는 부분(데드레그)을 손으로 비우고 ENTER ... ")
    print("  N2 blow-out ...")
    ok, dur = run_blowout(mfc, read_phase, blow_cfg.get("sccm", 20.0),
                          blow_cfg.get("max_s", 90.0), stable_s=blow_cfg.get("stable_s", 3.0),
                          sim=sim, sim_purge_rate=blow_cfg.get("sim_rate"))
    print(f"  {'✓ GAS 안정(완전 배출)' if ok else '✗ 미완 — 잔존 확인'} ({dur:.1f}s)")
    return ok


def charge_syringe(pump, source_port, vol_ml, read_input=input):
    """시린지 충전(WITHDRAW) — 엔진과 동일한 운전 로직(ChemyxSmartPump.refill 재사용):
    12-way→소스포트 + 3-way→SOURCE 정렬 → refill_rate 로 withdraw → 완료 대기 →
    3-way→REACTOR 복원까지 refill() 이 전부 수행. 반환 실제 충전량(mL)."""
    vol_ml = float(vol_ml)
    if vol_ml <= 0:
        return 0.0
    if pump is None:                                   # sim
        return vol_ml
    if hasattr(pump, "refill"):
        cap = float(getattr(pump, "capacity", 6.0) or 6.0)
        cur = float(getattr(pump, "current_vol", 0.0) or 0.0)
        vol_ml = min(vol_ml, max(0.0, cap - cur))
        if vol_ml <= 0:
            print(f"  ⚠ 충전 불가 — current_vol {cur:.2f}/{cap:.1f} mL (여유 없음)")
            return 0.0
        pump.refill(source_port_idx=int(source_port), volume=vol_ml)
        return vol_ml
    print("  ⚠ 스마트펌프 아님 — 수동 충전: 3-way→SOURCE, withdraw, 3-way→REACTOR")
    read_input(f"  {vol_ml:.2f} mL 수동 충전 완료 후 ENTER ... ")
    return vol_ml


def expel_to_waste(pump, vol_ml, rate, waste_port=12, writer=None, read_phase=None):
    """시린지 내용물(기포 섞인 프라임분)을 12-way 폐기 포트로 INFUSE 배출 —
    엔진 Wash-Infuse 와 동일 경로(3-way→SOURCE + 포트12). 반환 배출량."""
    if pump is None:
        return float(vol_ml)
    if hasattr(pump, "set_valves_safe"):
        pump.set_valves_safe(selector_port=int(waste_port),
                             switcher_pos=int(getattr(pump, "POS_SOURCE", 1)))
    out = push_bounded(rate, vol_ml, pump, None, writer, read_phase)
    if hasattr(pump, "current_vol"):
        pump.current_vol = max(0.0, float(pump.current_vol) - out)
    return out


def prime_source_line(pump, source_port, prime_ml, cycles, expel_rate,
                      writer=None, read_phase=None):
    """첫 사용 프라임: 소스라인(inlet+12way+12way→3way→시린지 ≈ 1.98 mL)의 가스/
    구용액을 [withdraw(소스) → 포트12 배출] 사이클로 제거 — 시린지·커먼라인이 기포
    없는 측정액으로 차야 개루프 0점이 성립한다. 마지막 사이클 후 시린지는 빈 상태."""
    for i in range(int(cycles)):
        print(f"  ── 프라임 {i + 1}/{cycles}: withdraw {prime_ml:.2f} mL → 포트12 배출 ──")
        got = charge_syringe(pump, source_port, prime_ml)
        expel_to_waste(pump, got, expel_rate, writer=writer, read_phase=read_phase)


def _default_estimates(cfg, channel, labels):
    """config 기반 시작 추정 = 기대값의 90% — 일부러 모자라게 도착시켜 '+' 로 수렴
    (누적 모드 과주입 x=전체 재시작 방지). 기본 라벨(QUAD1/QUAD2/SENSOR/REACTOR) 전용.
    SENSOR/REACTOR 분할비 0.61/0.39 = 원장 mixing 분할(58.3/37.2 µL) 근사."""
    try:
        li = float(getattr(cfg, "line_vol_pump_merge", {}).get(channel, 0.0) or 0.0) \
            + float(getattr(cfg, "valve_internal_vol", {}).get(channel, 0.0) or 0.0)
        tj = {int(k): float(v or 0.0) for k, v in
              (getattr(cfg, "tjunction_line_vols", {}) or {}).items()}
        mix = float(getattr(cfg, "mixing_line_dead_vol", 0.0) or 0.0)
        cand = {"QUAD1": li, "QUAD2": tj.get(1, 0.0),
                "SENSOR": tj.get(2, 0.0) + mix * 0.61, "REACTOR": mix * 0.39}
        return {lab: round(cand[lab.upper()] * 0.9, 4) for lab in labels
                if cand.get(lab.upper(), 0.0) > 0}
    except Exception:
        return {}


def run_segment_calibration(rate, segments, estimates, pump, sim, mfc, read_phase,
                            writer, blow_cfg, read_input=input, manual_clear=True,
                            blow_at_end=False, charge_ml=None, recharge=None):
    """배관 구간별 캘리브. 반환 {label: 누적 mL}(주입점→그 랜드마크). 구간 부피는
    호출측이 인접 차분으로 얻는다 — 유체는 펌프 한쪽에서만 들어와 모든 측정이 누적.

    blow_at_end=False (구간별 검증): 랜드마크마다 확정 후 손배출+N2블로우, 다음
      랜드마크는 다시 주입점부터(신선한 선단). 과주입=x → 블로우 후 그 랜드마크 재시작.
    blow_at_end=True (누적 1패스, 권장): 중간 blow 없이 선단을 계속 전진시키며
      랜드마크마다 확정(총 누적 기록). 끝에서 손배출+N2블로우 1회. 과주입=x →
      누적이라 전체 패스 재시작(블로우 후 처음부터). 낭비 없이 빠름.
    charge_ml: 시린지 최초 충전량 — 지정 시 잔량을 추적해 소진 전에 경고하고,
      부족하면 요청 밀기량을 잔량으로 클램프한다(과인출 방지 — Chemyx 는 빈
      시린지에서 infuse 시 조용한 무토출이 될 수 있음).
    recharge: 호출하면 시린지를 재충전하고 '추가된 mL' 반환하는 콜백('r' 입력).
      재충전 동안 3-way 가 SOURCE 로 반응기측을 격리하므로 선단(누적 0점)은
      움직이지 않는다 — 패스 중간 삽입이 안전 (엔진 refill 로직 그대로).
    """
    t0 = time.monotonic()
    budget = {"rem": (float(charge_ml) if charge_ml is not None else None)}

    def _push(target_ml):
        """예산 추적 밀기 — 잔량 클램프 + 차감."""
        target_ml = float(target_ml)
        if budget["rem"] is not None and target_ml > budget["rem"] + 1e-9:
            print(f"  ⚠ 시린지 잔량 {budget['rem']:.3f} mL < 요청 {target_ml:.3f} mL — "
                  + ("'r' 재충전 후 계속하세요" if recharge is not None else "잔량으로 클램프"))
            target_ml = max(0.0, budget["rem"])
        dv = push_bounded(rate, target_ml, pump, sim, writer, read_phase, t0)
        if budget["rem"] is not None:
            budget["rem"] = max(0.0, budget["rem"] - dv)
        return dv

    def _budget_note(next_need):
        if budget["rem"] is not None and budget["rem"] < float(next_need) * 1.5 + 0.05:
            print(f"  ⚠ 시린지 잔량 {budget['rem']:.3f} mL (다음 예상 {next_need:.3f}) — "
                  + ("'r' 재충전 권장" if recharge is not None else "잔량 주의"))

    def _do_recharge():
        if recharge is None:
            print("    재충전 수단 없음 (--charge 0 이거나 스마트펌프 아님)")
            return
        added = float(recharge() or 0.0)
        if budget["rem"] is not None:
            budget["rem"] += added
            print(f"  ✚ 재충전 +{added:.3f} mL — 잔량 {budget['rem']:.3f} mL "
                  f"(3-way 격리로 선단·누적 불변)")

    def confirm_loop(lab, cum, is_cumulative):
        """한 랜드마크 확인 루프. 반환 (action, cum). action∈{'ok','skip','restart'}."""
        while True:
            ans = read_input(
                f"  [{lab}] o=딱도달(확정) / +<mL>=조금더 / r=시린지 재충전 / "
                f"x=과주입({'전체재시작' if is_cumulative else '블로우후재시작'}) "
                f"/ s=스킵 : ").strip().lower()
            if ans == "o":
                return "ok", cum
            if ans == "s":
                print(f"  – {lab} 스킵")
                return "skip", cum
            if ans == "x":
                return "restart", cum
            if ans == "r":
                _do_recharge()
                continue
            if ans.startswith("+"):
                try:
                    add = float(ans[1:])
                except Exception:
                    print("    형식: +0.02")
                    continue
                dv = _push(add)
                cum += dv
                print(f"  누적 {cum:.4f} mL." + (
                    f"  (잔량 {budget['rem']:.3f})" if budget["rem"] is not None else ""))
            else:
                print("    입력: o / +<mL> / r / x / s")

    # ── 누적 1패스 (blow_at_end=True) ─────────────────────────────
    if blow_at_end:
        while True:   # 과주입 시 전체 패스 재시작
            results, total, restart = {}, 0.0, False
            print("\n  [누적 모드] 중간 blow 없이 선단을 계속 전진 — 랜드마크마다 확정.")
            for lab in segments:
                inc = float(estimates.get(lab, 0.1))
                _budget_note(inc)
                pushed = _push(inc)
                total += pushed
                print(f"\n=== [{lab}] +{pushed:.4f} → 누적 {total:.4f} mL. "
                      f"선단이 '{lab}' 끝점 도달했는지 확인.")
                act, total = confirm_loop(lab, total, is_cumulative=True)
                if act == "restart":
                    print("  ⚠ 누적 모드 과주입 — 전체 배출 후 처음부터 다시.")
                    restart = True
                    break
                if act == "ok":
                    results[lab] = total
                    print(f"  ✔ {lab} 누적 = {total:.4f} mL")
            if restart:
                _manual_and_blow(mfc, read_phase, blow_cfg, sim, read_input, manual_clear)
                continue
            break
        print("\n  ── 패스 완료 — 최종 배출 ──")
        _manual_and_blow(mfc, read_phase, blow_cfg, sim, read_input, manual_clear)
        return results

    # ── 구간별 검증 (blow_at_end=False) — 랜드마크마다 blow ──────────
    results = {}
    for lab in segments:
        est = float(estimates.get(lab, 0.1))
        print(f"\n=== [{lab}] 시작 추정 {est:.4f} mL ({rate} mL/min 정속) ===")
        _budget_note(est)
        cum = _push(est)
        print(f"  주입 {cum:.4f} mL — 유체 선단이 '{lab}' 끝점에 도달했는지 확인.")
        while True:
            act, cum = confirm_loop(lab, cum, is_cumulative=False)
            if act == "ok":
                results[lab] = cum
                print(f"  ✔ {lab} 누적 = {cum:.4f} mL")
                break
            if act == "skip":
                break
            # restart: 블로우 후 더 작은 추정으로 이 랜드마크 재시작
            _manual_and_blow(mfc, read_phase, blow_cfg, sim, read_input, manual_clear)
            nw = read_input(f"    재시작 추정 mL (직전 {cum:.4f} 보다 작게) : ").strip()
            try:
                est2 = float(nw)
            except Exception:
                est2 = cum * 0.7
            cum = _push(est2)
            print(f"  재주입 {cum:.4f} mL.")
        # 다음 랜드마크 위해 손배출 + N2 블로우
        _manual_and_blow(mfc, read_phase, blow_cfg, sim, read_input, manual_clear)
    return results


# ══════════════════════════════════════════════════════════════════════
# 자체검증 (--sim) — 순수계산 + 각 모드가 참값을 회복하는지
# ══════════════════════════════════════════════════════════════════════
def self_test():
    # 실시간 sleep 누적을 줄이려 sim 은 고유량(짧은 실측시간)으로 — 정확도는
    # poll×rate/60 오차로 결정되므로 유량↑ 시 tolerance 도 함께 완화.
    RATE = 12.0   # mL/min (12/60=0.2 mL/s → 0.2 mL 를 ~1s 에 도달)
    fails = []

    def ck(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
        if not cond:
            fails.append(name)

    print("[1] 순수 계산")
    ck("volume_ml 0.5mL/min×120s=1.0mL", abs(volume_ml(0.5, 120) - 1.0) < 1e-9)
    segs = segments_from_marks([("A", 0.09), ("A+B", 0.18), ("A+B+C", 0.30)])
    ck("marks 차분 B=0.09", abs(segs[1][1] - 0.09) < 1e-9, str(segs))
    ck("marks 차분 C=0.12", abs(segs[2][1] - 0.12) < 1e-9)

    print(f"[2] breakthrough — 참 데드볼륨 0.20 mL 회복 ({RATE}mL/min)")
    sim = _SimRig(true_dv_ml=0.20)
    inj = Injector(RATE, sim=sim)
    dv = run_breakthrough(inj, lambda: sim.read_phase(), timeout_s=60, poll=0.02)
    ck("브레이크스루 감지", dv is not None)
    # 오차 ≈ Injector 틱(0.05s)×rate/60 = 한 스텝 부피. 여유 0.02 mL.
    ck("데드볼륨 0.20±0.02 회복", dv is not None and abs(dv - 0.20) < 0.02,
       f"측정 {dv:.4f} mL" if dv else "None")

    print("[3] marks — 스크립트 입력으로 3랜드마크 (참 0.09/0.18/0.30 근처)")
    sim3 = _SimRig(true_dv_ml=999)   # 센서 무관, 부피만
    inj3 = Injector(RATE, sim=sim3)
    # 가짜 입력: 각 목표 누적부피에 도달하면 ENTER. 부피는 배경스레드가 키우므로
    # 시간으로 근사 대기 후 마크.
    targets = [0.09, 0.18, 0.30]
    seq = iter(targets)

    def fake_input(_prompt):
        tgt = next(seq, None)
        if tgt is None:
            return "q"
        while inj3.volume < tgt:
            time.sleep(0.005)
        return ""
    marks = run_marks(inj3, ["A", "A+B", "A+B+C"], read_input=fake_input)
    segs3 = segments_from_marks(marks)
    ck("marks 3점 기록", len(marks) == 3, str([(l, round(v, 3)) for l, v in marks]))
    ck("구간 A≈0.09", abs(segs3[0][1] - 0.09) < 0.02, f"{segs3[0][1]:.3f}")
    ck("구간 B≈0.09", abs(segs3[1][1] - 0.09) < 0.02, f"{segs3[1][1]:.3f}")

    print("[4] blowout — N2 퍼지로 센서 GAS 안정 복귀")
    simB = _SimRig(true_dv_ml=0.05)
    simB.add_pump(0.10)                     # 라인에 액체 참 (선단 통과)
    ck("퍼지 전 액체 상태", simB.read_phase() != "GAS")
    ok, dur = run_blowout(None, lambda: simB.read_phase(), sccm=20, max_s=30,
                          stable_s=1.0, sim=simB, poll=0.02, sim_purge_rate=0.5)
    ck("blow-out 후 GAS 안정 복귀", ok, f"{dur:.1f}s")

    print("[5] push_bounded — 목표 0.12 mL 딱 밀고 정지 (과주입 억제)")
    simP = _SimRig(true_dv_ml=999)
    got = push_bounded(0.6, 0.12, sim=simP)
    ck("0.12±0.005 착지", abs(got - 0.12) < 0.005, f"{got:.4f} mL")

    print("[6] segment 캘리브 — 참 끝점 0.15 mL 를 +넉지로 수렴 후 확정")
    simS = _SimRig(true_dv_ml=999, purge_dv_ml=0.02)
    _SEG_RATE = RATE
    TRUE_END = 0.15

    def seg_input(prompt):
        p = str(prompt)
        if "손으로" in p:            # 수동배출 프롬프트
            return ""
        if "재시작 추정" in p:        # 과주입 재시작 추정 입력 프롬프트만 (확인 프롬프트의
            return "0.05"            #   '블로우후재시작' 문구와 구분)
        # 확정 프롬프트: 누적(simS._pumped)이 참 끝점 이상이면 'o', 아니면 조금 더
        if simS._pumped >= TRUE_END - 1e-9:
            return "o"
        return "+0.01"
    res = run_segment_calibration(
        _SEG_RATE, ["SEG"], {"SEG": 0.10}, None, simS, None,
        lambda: simS.read_phase(), None,
        {"sccm": 20, "max_s": 10, "stable_s": 0.5, "sim_rate": 2.0},
        read_input=seg_input, manual_clear=True)
    ck("segment 참끝점 0.15±0.02 수렴", abs(res.get("SEG", 0) - TRUE_END) < 0.02,
       f"확정 {res.get('SEG')} mL")

    print("[7] segment blow_at_end — 누적 2랜드마크(참 0.10/0.22), 중간 blow 없음")
    simE = _SimRig(true_dv_ml=999, purge_dv_ml=0.02)
    TRUE_CUM = {"L1": 0.10, "L2": 0.22}   # 누적 참값
    order = ["L1", "L2"]
    blows = {"n": 0}
    _orig_blow = globals()["run_blowout"]

    def _counting_blow(*a, **k):
        blows["n"] += 1
        return _orig_blow(*a, **k)
    globals()["run_blowout"] = _counting_blow

    def end_input(prompt):
        p = str(prompt)
        if "손으로" in p:
            return ""
        # 확정: 현재 누적(simE._pumped)이 '아직 확정 안 된 첫 랜드마크'의 참값 이상이면 o
        nxt = next((lab for lab in order if lab not in end_input.done), None)
        if nxt is None:
            return "o"
        if simE._pumped >= TRUE_CUM[nxt] - 1e-9:
            end_input.done.add(nxt)
            return "o"
        return "+0.01"
    end_input.done = set()
    try:
        resE = run_segment_calibration(
            RATE, order, {"L1": 0.08, "L2": 0.10}, None, simE, None,
            lambda: simE.read_phase(), None,
            {"sccm": 20, "max_s": 10, "stable_s": 0.5, "sim_rate": 5.0},
            read_input=end_input, manual_clear=True, blow_at_end=True)
    finally:
        globals()["run_blowout"] = _orig_blow
    ck("L1 누적 0.10±0.02", abs(resE.get("L1", 0) - 0.10) < 0.02, f"{resE.get('L1')}")
    ck("L2 누적 0.22±0.02", abs(resE.get("L2", 0) - 0.22) < 0.02, f"{resE.get('L2')}")
    segE = segments_from_marks([(l, resE[l]) for l in order if l in resE])
    ck("구간 L1→L2 ≈ 0.12", abs(segE[1][1] - 0.12) < 0.03, f"{segE[1][1]:.3f}")
    ck("중간 blow 없음 — 끝에서 1회만", blows["n"] == 1, f"blow {blows['n']}회")

    print("[8] 충전 예산 — 잔량 소진 시 클램프 → 'r' 재충전 → 완주 (누적 0점 불변)")
    simR = _SimRig(true_dv_ml=999, purge_dv_ml=0.02)
    TRUE_R = 0.15
    rc = {"n": 0}

    def _recharge():
        rc["n"] += 1
        return 0.5          # 시린지 +0.5 mL (선단은 안 움직임 — 3-way 격리 모사: no-op)

    state = {"prev": -1.0, "last": ""}

    def rc_input(prompt):
        p = str(prompt)
        if "손으로" in p:
            return ""
        if simR._pumped >= TRUE_R - 1e-9:
            return "o"
        if state["last"] != "r" and abs(simR._pumped - state["prev"]) < 1e-12:
            state["last"] = "r"      # 예산 소진 — 밀리지 않음 → 재충전
            return "r"
        state["prev"] = simR._pumped
        state["last"] = "+"
        return "+0.01"
    resR = run_segment_calibration(
        RATE, ["SEG"], {"SEG": 0.10}, None, simR, None,
        lambda: simR.read_phase(), None,
        {"sccm": 20, "max_s": 10, "stable_s": 0.5, "sim_rate": 2.0},
        read_input=rc_input, manual_clear=True, blow_at_end=True,
        charge_ml=0.12, recharge=_recharge)
    ck("예산 소진 → 재충전 발생", rc["n"] >= 1, f"재충전 {rc['n']}회")
    ck("재충전 후 참끝점 0.15±0.02 수렴", abs(resR.get("SEG", 0) - TRUE_R) < 0.02,
       f"확정 {resR.get('SEG')}")

    print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    return 1 if fails else 0


# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="데드볼륨 실측 캘리브레이션")
    ap.add_argument("--sim", action="store_true", help="무하드웨어 자체검증(+회귀)")
    ap.add_argument("--channel", default="Group A", help="주입 펌프(채널)명")
    ap.add_argument("--source-port", type=int, default=2, help="12-way 소스 포트(염료/용매)")
    ap.add_argument("--rate", type=float, default=0.5, help="주입 유량 mL/min (정속)")
    ap.add_argument("--mode", choices=["segment", "breakthrough", "marks", "blowout"],
                    default="segment")
    ap.add_argument("--sensor", default="collect", help="OPB 센서 키 (config sensors)")
    ap.add_argument("--labels", default="QUAD1,QUAD2,SENSOR,REACTOR",
                    help="segment/marks 구간 라벨 (콤마 구분)")
    ap.add_argument("--estimates", default="",
                    help="segment 시작 추정 (예: 'QUAD1=0.09,QUAD2=0.045') — 없으면 0.1")
    ap.add_argument("--no-manual-clear", action="store_true",
                    help="segment 확정 후 '손으로 데드레그 배출' 프롬프트 생략")
    ap.add_argument("--blow-at-end", action="store_true",
                    help="segment 모드에서 중간 blow 없이 누적 1패스로 밀고 끝에서만 배출(권장)")
    ap.add_argument("--charge", default="auto",
                    help="측정 전 시린지 충전량 mL — 엔진 refill 로직으로 withdraw "
                         "(auto=추정합×2+0.5, 0=충전 생략·수동 준비)")
    ap.add_argument("--prime-cycles", type=int, default=0,
                    help="첫 사용 프라임 [withdraw→포트12 배출] 반복 (채널 첫 측정 권장 2 — "
                         "소스라인 ~1.98mL 의 가스/구용액 제거)")
    ap.add_argument("--prime-ml", type=float, default=None,
                    help="프라임 1회 withdraw 량 (기본: config 소스라인 합+0.3)")
    ap.add_argument("--expel-rate", type=float, default=2.0,
                    help="프라임 폐기 배출 유량 mL/min")
    ap.add_argument("--timeout", type=float, default=300.0, help="breakthrough 타임아웃 s")
    ap.add_argument("--blowout", action="store_true", help="측정 후 N2 blow-out 수행")
    ap.add_argument("--n2-sccm", type=float, default=20.0)
    ap.add_argument("--n2-sec", type=float, default=90.0, help="blow-out 최대 대기 s")
    args = ap.parse_args()

    if args.sim:
        return self_test()

    # ── 라이브 ──────────────────────────────────────────────────────
    print(f"=== 데드볼륨 캘리브레이션 (라이브) — 채널 {args.channel} @ {args.rate} mL/min ===")
    print("⚠ 시작 전 라인을 N2 로 퍼지(가스 충전)했는지 확인하세요 — 깨끗한 브레이크스루 전제.")
    cfg, hw, pump, sensor, mfc = _load_live(args.channel, args.source_port)
    if sensor is None and args.mode == "breakthrough":
        print("  ⚠ OPB 위상센서 미구성 — breakthrough 불가. marks 모드를 쓰거나 센서를 config 에 등록하세요.")
        return 1

    exp, parts = _expected_dv_to_sensor(cfg, args.channel)
    if exp is not None:
        print(f"  [참고] config 상 주입점→센서 예상 총합 ≈ {exp:.4f} mL  {parts}")

    # 주입 경로 밸브 정렬 (best-effort — 스마트펌프의 selector/switcher)
    try:
        sel = getattr(pump, "selector_valve", None) or getattr(pump, "selector", None)
        sw = getattr(pump, "switching_valve", None) or getattr(pump, "switcher", None)
        if sel is not None:
            sel.set_position(int(args.source_port))
            print(f"  12-way → port {args.source_port}")
        if sw is not None:
            sw.set_position(2)   # 반응기 방향
            print("  3-way → REACTOR(2)")
    except Exception as e:
        print(f"  ⚠ 밸브 정렬 실패(수동 정렬 필요): {e}")

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ROOT, "logs", f"DEADVOL_{stamp}.csv")
    f = open(path, "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["elapsed_s", "volume_ml", "event", "phase"])

    def read_phase():
        try:
            return sensor.read_phase(args.sensor) if sensor is not None else "GAS"
        except Exception:
            return "?"

    inj = Injector(args.rate, pump=pump, writer=w, log_phase=read_phase)
    blow_cfg = {"sccm": args.n2_sccm, "max_s": args.n2_sec, "stable_s": 3.0}
    try:
        if args.mode == "segment":
            labels = [s.strip() for s in args.labels.split(",") if s.strip()]
            ests = {}
            for kv in args.estimates.split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    try:
                        ests[k.strip()] = float(v)
                    except Exception:
                        pass
            if not ests:
                ests = _default_estimates(cfg, args.channel, labels)
                if ests:
                    print(f"  [자동 시작추정 = config 기대값×0.9] {ests}")
            print(f"  배관 랜드마크 캘리브 — {labels}")

            # ── 시린지 충전 오케스트레이션 (기존 운전 로직: refill/wash 경로 재사용) ──
            need = sum(float(ests.get(lab, 0.1)) for lab in labels)
            smart = hasattr(pump, "refill") and hasattr(pump, "current_vol")
            cap = float(getattr(pump, "capacity", 6.0) or 6.0)
            charge_ml, recharge_cb = None, None
            if str(args.charge).strip().lower() not in ("0", "0.0", "off", "none"):
                target = (min(cap * 0.8, max(1.5, 2.0 * need + 0.5))
                          if str(args.charge).strip().lower() == "auto"
                          else float(args.charge))
                print(f"  [충전 계획] 패스 예상 {need:.3f} mL → 충전 {target:.2f} mL "
                      f"(시린지 용량 {cap:.1f} mL, 측정 중 'r'=재충전)")
                if smart:
                    # 개루프 부피 추적 0점 — 플런저 실위치가 진실 (엔진 시퀀스 시작 리셋과 동일 사상)
                    input("  시린지 플런저가 '빈(최하단)' 상태인지 확인 후 ENTER ... ")
                    pump.current_vol = 0.0
                if args.prime_cycles > 0:
                    pml = args.prime_ml
                    if pml is None:
                        pml = (float(getattr(cfg, "line_vol_valve_pump", {}).get(args.channel, 0.0) or 0.0)
                               + float(getattr(cfg, "selector_internal_vol", {}).get(args.channel, 0.0) or 0.0)
                               + float(getattr(cfg, "line_vol_inlet", {}).get(args.channel, 0.0) or 0.0)
                               + 0.3) or 2.3
                    pml = min(pml, cap * 0.9)
                    print(f"  [프라임] {args.prime_cycles}회 × {pml:.2f} mL "
                          f"(withdraw @refill_rate → 포트12 배출 @{args.expel_rate} mL/min)")
                    prime_source_line(pump, args.source_port, pml, args.prime_cycles,
                                      args.expel_rate, writer=w, read_phase=read_phase)
                charge_ml = charge_syringe(pump, args.source_port, target)
                print(f"  ✚ 충전 완료 {charge_ml:.2f} mL — 3-way=REACTOR (측정 준비)")
                if smart:
                    def recharge_cb(_tgt=target):
                        avail = max(0.0, cap - float(getattr(pump, "current_vol", 0.0) or 0.0))
                        return charge_syringe(pump, args.source_port, min(_tgt, avail))
            else:
                print("  [충전 생략] 시린지에 측정액이 이미 충전·프라임돼 있다고 가정 (수동 준비)")
            if args.blow_at_end:
                print("  [누적 1패스] 중간 blow 없이 선단 계속 전진 → 랜드마크마다 확인 "
                      "→ 끝에서 손배출+N2블로우 1회")
            else:
                print("  [구간별 검증] 랜드마크마다: 밀기 → 확인 → 확정 → 손배출 → N2블로우 → 다음")
            res = run_segment_calibration(
                args.rate, labels, ests, pump, None, mfc, read_phase, w, blow_cfg,
                manual_clear=not args.no_manual_clear, blow_at_end=args.blow_at_end,
                charge_ml=charge_ml, recharge=recharge_cb)
            # 기록값 = 주입점→랜드마크 누적. 구간 부피 = 인접 차분.
            print("\n  ══ 실측 요약 (누적 = 주입점→랜드마크, 구간 = 인접 차분) ══")
            ordered = [(lab, res[lab]) for lab in labels if lab in res]
            for lab, seg, cum in segments_from_marks(ordered):
                print(f"    {lab:16s} 구간 {seg:.4f} mL   (누적 {cum:.4f} mL)")
            print("  → 구간 부피를 tubing_measurements.json 의 해당 tube_vol_* 키에 반영"
                  " (Claude 에게 주면 매핑해 드립니다).")
        elif args.mode == "breakthrough":
            print("  주입 시작 — 센서 가스→액체 대기...")
            dv = run_breakthrough(inj, read_phase, args.timeout)
            if dv is None:
                print(f"  ✗ 타임아웃 {args.timeout:.0f}s — 브레이크스루 미검출 (막힘? 유량? 퍼지?)")
            else:
                print(f"\n  ★ 주입점→센서 실측 데드볼륨 = {dv:.4f} mL")
                if exp:
                    print(f"    (config 예상 {exp:.4f} mL / 편차 {dv-exp:+.4f} mL)")
        elif args.mode == "marks":
            labels = [s.strip() for s in args.labels.split(",") if s.strip()]
            print(f"  주입 시작 — 랜드마크 {labels} 도달 시마다 ENTER")
            marks = run_marks(inj, labels)
            print("\n  ── 구간별 실측 부피 (누적 차분) ──")
            for lab, seg, cum in segments_from_marks(marks):
                print(f"    {lab:12s} 구간 {seg:.4f} mL  (누적 {cum:.4f} mL)")
        elif args.mode == "blowout":
            args.blowout = False  # 단독 모드 — 아래 중복 실행 방지
            print("  N2 blow-out 단독 실행 (주입 없음)")
            ok, dur = run_blowout(mfc, read_phase, args.n2_sccm, args.n2_sec)
            print(f"  {'✓ 완전 배출(GAS 안정)' if ok else '✗ 미완(액체 잔존?)'} — {dur:.1f}s")

        if args.blowout:
            print(f"\n  N2 blow-out — {args.n2_sccm} sccm, 최대 {args.n2_sec:.0f}s")
            # 아웃렛 waste 로 (있으면)
            try:
                (hw.valves or {}).get("Outlet") and hw.valves["Outlet"].set_position(1)
            except Exception:
                pass
            ok, dur = run_blowout(mfc, read_phase, args.n2_sccm, args.n2_sec)
            print(f"  {'✓ 완전 배출(GAS 안정)' if ok else '✗ 미완(액체 잔존?)'} — {dur:.1f}s")
    except KeyboardInterrupt:
        print("\n  중단 — 펌프/MFC 정지")
        inj.stop()
        try:
            mfc and mfc.stop()
        except Exception:
            pass
    finally:
        inj.stop()
        f.close()
        print(f"\n  로그: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
