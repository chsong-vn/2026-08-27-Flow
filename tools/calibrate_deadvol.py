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
  py -3.14 tools/calibrate_deadvol.py --channel "Group A" --source-port 2 \
        --rate 0.5 --mode breakthrough --sensor collect --blowout
  py -3.14 tools/calibrate_deadvol.py --channel "Group A" --source-port 2 \
        --rate 0.3 --mode marks --labels "QUAD1,QUAD2,SENSOR,REACTOR"

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
    sys.stdout.reconfigure(encoding="utf-8")

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


# ══════════════════════════════════════════════════════════════════════
# 자체검증 (--sim) — 순수계산 + 각 모드가 참값을 회복하는지
# ══════════════════════════════════════════════════════════════════════
def self_test():
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

    print("[2] breakthrough — 참 데드볼륨 0.20 mL 회복 (0.5mL/min, poll 0.02)")
    sim = _SimRig(true_dv_ml=0.20)
    inj = Injector(0.5, sim=sim)
    dv = run_breakthrough(inj, lambda: sim.read_phase(), timeout_s=60, poll=0.02)
    ck("브레이크스루 감지", dv is not None)
    # 오차 = poll×rate/60 ≈ 0.02×0.5/60 ≈ 0.00017 mL + 스레드 지연. 여유 0.01 mL.
    ck("데드볼륨 0.20±0.01 회복", dv is not None and abs(dv - 0.20) < 0.01,
       f"측정 {dv:.4f} mL" if dv else "None")

    print("[3] marks — 스크립트 입력으로 3랜드마크 (참 0.09/0.18/0.30 근처)")
    sim3 = _SimRig(true_dv_ml=999)   # 센서 무관, 부피만
    inj3 = Injector(1.0, sim=sim3)   # 1 mL/min = 0.01667 mL/s
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

    print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    return 1 if fails else 0


# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="데드볼륨 실측 캘리브레이션")
    ap.add_argument("--sim", action="store_true", help="무하드웨어 자체검증(+회귀)")
    ap.add_argument("--channel", default="Group A", help="주입 펌프(채널)명")
    ap.add_argument("--source-port", type=int, default=2, help="12-way 소스 포트(염료/용매)")
    ap.add_argument("--rate", type=float, default=0.5, help="주입 유량 mL/min (정속)")
    ap.add_argument("--mode", choices=["breakthrough", "marks", "blowout"],
                    default="breakthrough")
    ap.add_argument("--sensor", default="collect", help="OPB 센서 키 (config sensors)")
    ap.add_argument("--labels", default="QUAD1,QUAD2,SENSOR,REACTOR",
                    help="marks 모드 랜드마크 (콤마 구분)")
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
    try:
        if args.mode == "breakthrough":
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
