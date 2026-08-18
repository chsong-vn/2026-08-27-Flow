"""push 병행 세척 + Prime Phase-0/1 게이트 검증 (2026-08-15 용어/역할 확정판)

실제 StrictSequenceEngine 을 mock 하드웨어로 구동해 검증한다:
  1) HPLC(PUSH)는 push 전용 — prime 관여 없음, 기동은 스텝당 1회(주입 후),
     유속 = total_flow. post-injection prime(잔량→reactor)도 폐지 상태.
  2) Prime Phase-0(분기 데드볼륨, port1) = 매 스텝 / Phase-1(본류 반응기
     용매 충전, port1, 시린지 균등 분담) = 스텝1 전용.
  3) 주입 잔량 폐기 + 내부 세척(wash_infuse/withdraw)이 push 창 안에서 병행.
  4) 시약 장전량 = inject 분담 + 편도 src (왕복 레그 제외 산식).
     스텝2는 inlet 라인 primed → valve_pump(편도)만 보정.
  5) 인터록: roles.push_pump 설정 + push_pump=None(강등) → 시퀀스 시작 차단.

실행: py -3.14 tests\\test_push_parallel_wash.py  (루트에서)
"""
import os
import sys
import time
import threading
import math

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

T0 = time.time()
EVENTS = []          # (t, kind, name, data)
LOGS = []
ERRORS = []
LOCK = threading.Lock()


def rec(kind, name, data=None):
    with LOCK:
        EVENTS.append((time.time() - T0, kind, name, data))


class SimDriver:
    def __init__(self, pump):
        self.pump = pump

    def is_stopped(self):
        return not self.pump.running


class SimPump(ChemyxSmartPump):
    """통신 없는 Chemyx 시뮬레이터 — dosing/refill/prime/wash 프리미티브 기록"""

    def __init__(self, name, capacity=6.0):
        self.name = name
        self.capacity = capacity
        self.current_vol = 0.0
        self.running = False
        self.target_flow = 0.0
        self.status = "Idle"
        self.is_refilling = False
        self._abort_refill = False
        self.wash_volume = 3.0
        self.wash_count = 1
        self.prime_rate = 8.0
        self.driver = SimDriver(self)
        self._pending_fill = 0.0
        self._pending_fill_port = None
        self._pending_prime = 0.0
        self._pending_wash = 0.0

    # ── dosing ──
    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True
        rec("pump_start", self.name, self.target_flow)

    def stop(self):
        self.running = False
        rec("pump_stop", self.name)

    # ── refill (Phase-1 시약 흡입 / 4.5b 용매충전 겸용) ──
    def refill_prepare(self, port, volume=None):
        self._pending_fill = float(volume) if volume else self.capacity
        self._pending_fill_port = port
        return True

    def refill_trigger(self):
        self.is_refilling = True

    def refill_complete(self):
        time.sleep(0.1)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_fill)
        self.is_refilling = False
        self.status = f"Refilled {self.current_vol:.3f}mL"
        rec("refill", self.name, (self._pending_fill_port, self._pending_fill))

    # ── Phase-0 정량 리필 (blocking) ──
    def refill(self, port, volume=None):
        v = float(volume or 0.0)
        self.current_vol = min(self.capacity, self.current_vol + v)
        rec("phase0_refill", self.name, (port, v))
        return True

    # ── prime (Phase-0 / legacy post-inject 공용 프리미티브) ──
    def prime_prepare(self):
        if self.current_vol > 0.0:
            self._pending_prime = float(self.current_vol)
            return True
        return False

    def prime_trigger(self):
        pass

    def prime_complete(self):
        time.sleep(0.1)
        rec("prime", self.name, self._pending_prime)
        self.current_vol = 0.0
        self.status = "Primed"

    # ── wash (push 병행 세척이 사용하는 프리미티브) ──
    def wash_infuse_prepare(self, waste_port=12):
        if self.current_vol < 0.05:
            return False
        self._pending_wash = float(self.current_vol)
        return True

    def wash_infuse_trigger(self):
        pass

    def wash_infuse_complete(self):
        time.sleep(0.1)
        rec("wash_infuse", self.name, self._pending_wash)
        self.current_vol = 0.0
        self.status = "Washed(infuse)"
        return True

    def wash_withdraw_prepare(self, solvent_port=1):
        avail = max(0.0, self.capacity - self.current_vol)
        self._pending_wash = min(self.wash_volume, avail)
        return self._pending_wash >= 0.05

    def wash_withdraw_trigger(self):
        pass

    def wash_withdraw_complete(self):
        time.sleep(0.1)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_wash)
        rec("wash_withdraw", self.name, self._pending_wash)


class SimPushPump:
    def __init__(self):
        self.running = False
        self.target_flow = 0.0

    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True
        rec("pump_start", "PUSH", self.target_flow)

    def stop(self):
        if self.running:
            rec("pump_stop", "PUSH")
        self.running = False


class SimValve:
    def __init__(self, name):
        self.name = name
        self.position = 1

    def set_position(self, pos):
        self.position = pos
        rec("valve", self.name, pos)


class SimCollector:
    is_connected = True
    total_tubes = 88

    def __init__(self):
        self._pos = 0

    def home(self):
        self._pos = 0
        return True, "homed"

    def move_to_tube(self, n):
        self._pos = n
        rec("tube", "collector", n)
        return True, "ok"

    def get_position(self):
        return self._pos

    def get_well_id(self, n):
        return f"T{n}"


class SimHeater:
    def __init__(self):
        self.target_temp = 25.0

    def set_temperature(self, t):
        self.target_temp = float(t)

    def get_temperature(self):
        return self.target_temp

    def stop(self):
        self.target_temp = 0.0


class Sig:
    def __init__(self):
        none = lambda *a, **k: None
        for n in ("sig_status", "sig_phase_progress", "sig_progress", "sig_finished"):
            setattr(self, n, type("S", (), {"emit": staticmethod(none)})())
        self.sig_log = type("S", (), {"emit": staticmethod(
            lambda m: LOGS.append(m))})()
        self.sig_error = type("S", (), {"emit": staticmethod(
            lambda m: ERRORS.append(m))})()


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = 0.6
    ACTIVE_PUMPS = []
    tjunction_line_vols = {}
    line_vol_inlet = {}
    line_vol_valve_pump = {}
    line_vol_pump_merge = {}
    valve_internal_vol = {}
    selector_internal_vol = {}
    mixing_line_dead_vol = 0.0

    def __init__(self):
        self.config_data = {
            "system_params": {
                "post_reactor_vol_ml": 0.2,
                "collection_line_vol_ml": 0.06,
                "temp_tolerance_c": 0.5,
                "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0,
                "max_step_volume_ml": 500.0,
                "wash_mode": "off",
                "prefill_mode": "port_change",
                "purge_order": "lifo",
                "priming_rate_ml_min": 20.0,
                "syringe_refill_rate": 20.0,
            },
            "roles": {"push_pump": {"driver_id": "dev_reaxus_1"}},
        }


# ── 시나리오: 2-step 동일 포트, HPLC push 활성 ──
FLOWS = {"A": 1.0, "B": 1.0}
F = sum(FLOWS.values())
TARGET, TUBE = 0.45, 0.15
LINE_INLET = {"A": 0.075, "B": 0.075}   # 시약 인렛 (첫 사용만 src 가산)
LINE_VP = {"A": 0.06, "B": 0.06}        # 12way→3way 편도 (레그 제외 후 스케일)
LINE_PM = {"A": 0.05, "B": 0.05}
VALVE_INT = {"A": 0.02, "B": 0.02}

step = {"temp": 25.0, "vol_ml": TARGET, "residence_time": 0.6 / F * 60,
        "inlet_ports": {k: 2 for k in FLOWS}, "flows": dict(FLOWS),
        "collect_volume_per_tube": TUBE, "meta": {}}
plan = [step, dict(step)]

pumps = {n: SimPump(n) for n in FLOWS}
push_pump = SimPushPump()
cfg = Cfg()
cfg.line_vol_inlet = dict(LINE_INLET)
cfg.line_vol_valve_pump = dict(LINE_VP)
cfg.line_vol_pump_merge = dict(LINE_PM)
cfg.valve_internal_vol = dict(VALVE_INT)
cfg.ACTIVE_PUMPS = list(FLOWS.keys())

eng = StrictSequenceEngine(cfg, pumps, {"Outlet": SimValve("Outlet")}, SimHeater(),
                           SafetyManager(cfg, pumps, SimHeater()), Sig(),
                           collector=SimCollector(), push_pump=push_pump)
eng.collector_start_tube = 1

print(f"=== push 병행 세척 검증: 2-step 동일포트, F={F} ===")
t0 = time.time()
eng.run_sequence(plan, None)
print(f"run time: {time.time() - t0:.1f}s\n")

# ── 검증 ──
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


push_evs = [(t, d) for t, k, n, d in EVENTS if k == "pump_start" and n == "PUSH"]
push_stops = [t for t, k, n, d in EVENTS if k == "pump_stop" and n == "PUSH"]
main_starts = [t for t, d in push_evs]
dosing_starts = sorted(t for t, k, n, d in EVENTS
                       if k == "pump_start" and n in FLOWS)  # A/B start = 주입 dosing 뿐
primes = [(t, n, d) for t, k, n, d in EVENTS if k == "prime"]
p0_refills = [(t, n, d) for t, k, n, d in EVENTS if k == "phase0_refill"]
refills = [(t, n, d) for t, k, n, d in EVENTS if k == "refill"]
w_inf = [(t, n, d) for t, k, n, d in EVENTS if k == "wash_infuse"]
w_wd = [(t, n, d) for t, k, n, d in EVENTS if k == "wash_withdraw"]


def stop_after(t):
    later = [s for s in push_stops if s > t]
    return min(later) if later else None


# 0) HPLC = push 전용: 스텝당 1회 기동(주입 후), 유속 = total_flow, prime 관여 없음
check("PUSH 기동 2회 (push 전용 — prime 관여 없음)", len(push_evs) == 2,
      f"({len(push_evs)}회)")
check("PUSH 유속 = total_flow", bool(push_evs)
      and all(abs(d - F) < 1e-9 for _, d in push_evs),
      f"({[d for _, d in push_evs]} vs F={F})")
if push_evs and dosing_starts:
    check("PUSH 기동은 전부 주입 이후", all(t > dosing_starts[0] for t, _ in push_evs))

# 1) Prime 구성 (동일 포트 2-step): Phase-0 = 스텝1만 / Phase-1 = 스텝1만
#    (포트변경 스텝이면 Phase-0 재실행 — 이 시나리오엔 없음)
P1_SHARE = (0.6 + 0.0 + 0.2) * 1.1 / len(FLOWS)   # (reactor+mixing+post)×1.1 균등
p0_primes = [d for _, _, d in primes if d < 0.2]
p1_primes = [d for _, _, d in primes if d >= 0.2]
check("Phase-0 prime = 스텝1만 (2건)", len(p0_primes) == 2,
      f"({len(p0_primes)}건: {[round(v, 3) for v in p0_primes]})")
check(f"Phase-1 prime = 스텝1만 (2건 ≈{P1_SHARE:.2f})",
      len(p1_primes) == 2 and all(abs(v - P1_SHARE) < 0.02 for v in p1_primes),
      f"({[round(v, 3) for v in p1_primes]})")
check("Phase-0 정량리필 = 스텝1만 (2건)", len(p0_refills) == 2,
      f"({len(p0_refills)}건)")
check("스텝2 Phase-0 생략 로그", any("Prime Phase-0 생략" in m for m in LOGS))
p1_fills = [d[1] for _, _, d in refills if d[0] == 1]
check(f"Phase-1 port1 흡입 = 2건 ≈{P1_SHARE:.2f}",
      len(p1_fills) == 2 and all(abs(v - P1_SHARE) < 0.02 for v in p1_fills),
      f"({[round(v, 3) for v in p1_fills]})")
check("스텝2 Phase-1 생략 로그", any("Prime Phase-1 생략" in m for m in LOGS))
check("HPLC 경로 post-inject prime 생략 로그",
      any("Post-injection prime 생략" in m for m in LOGS))
# 주입~push 창 안에 prime 없음 — post-injection prime(사고 원인) 부활 감지
if len(main_starts) == 2 and len(dosing_starts) >= 4:
    inj_windows = [(dosing_starts[0], stop_after(main_starts[0]) or 1e9),
                   (dosing_starts[2], stop_after(main_starts[1]) or 1e9)]
    check("주입~push 창 내 prime 없음",
          not any(lo <= t <= hi for lo, hi in inj_windows for t, _, _ in primes))

# 2) 세척이 push 창 안에서 실행 — 잔량 폐기 선행 단계 없음 (2026-08-15 사용자
#    확정: 주입=시약 전량 토출 가정. 미세 잔량은 첫 사이클 infuse 전량배출에 합산)
check("세척 infuse = 4건 (스텝당 사이클1 ×2펌프, 선행 폐기 없음)",
      len(w_inf) == 4, f"({len(w_inf)}건)")
check("세척 withdraw = 4건 (스텝당 사이클1 ×2펌프)",
      len(w_wd) == 4, f"({len(w_wd)}건)")
if len(main_starts) == 2:
    windows = [(s, (stop_after(s) or s) + 2.0) for s in main_starts]
    ok_in_window = all(
        any(lo <= t <= hi for lo, hi in windows)
        for t, _, _ in (w_inf + w_wd))
    check("세척 이벤트 전부 push 창 내", ok_in_window)

# 3) 세척 배출량 = wash_volume(3.0) + 주입 후 미세 잔량 — 잔량이 사이클에
#    합산 폐기됨을 검증. 스텝1 잔량=src(0.135) / 스텝2=vp만(0.060, inlet primed)
inj_share = TARGET / F  # 0.225/펌프
exp_res_s1 = LINE_INLET["A"] + LINE_VP["A"]
exp_res_s2 = LINE_VP["A"]
_s1_end = (stop_after(main_starts[0]) or 0.0) + 2.0 if main_starts else 0.0
s1_inf = [d for t, n, d in w_inf if t <= _s1_end]
s2_inf = [d for t, n, d in w_inf if t > _s1_end]
check(f"스텝1 세척배출 ≈ 3.0+src({3.0 + exp_res_s1:.3f})",
      len(s1_inf) == 2 and all(abs(v - (3.0 + exp_res_s1)) < 0.03 for v in s1_inf),
      f"({[round(v, 3) for v in s1_inf]})")
check(f"스텝2 세척배출 ≈ 3.0+vp({3.0 + exp_res_s2:.3f})",
      len(s2_inf) == 2 and all(abs(v - (3.0 + exp_res_s2)) < 0.03 for v in s2_inf),
      f"({[round(v, 3) for v in s2_inf]})")

# 4) 시약 흡입(Phase-1): 매 스텝 수행 + 산식 (inject 분담 + src)
reagent_fills = [(t, n, d) for t, n, d in refills if d[0] == 2]
check("시약 흡입 = 스텝당 2펌프 ×2스텝", len(reagent_fills) == 4,
      f"({len(reagent_fills)}건)")
if len(reagent_fills) == 4:
    s1_f = [d[1] for _, _, d in reagent_fills[:2]]
    s2_f = [d[1] for _, _, d in reagent_fills[2:]]
    check("스텝1 fill ≈ 0.225+0.135", all(abs(v - (inj_share + exp_res_s1)) < 0.01 for v in s1_f),
          f"({[round(v, 3) for v in s1_f]})")
    check("스텝2 fill ≈ 0.225+0.060", all(abs(v - (inj_share + exp_res_s2)) < 0.01 for v in s2_f),
          f"({[round(v, 3) for v in s2_f]})")

# 5) 인터록: push_pump 강등 상태 차단 (SafetyError 는 워커가 잡는 구조 — 여기선 직접)
from engine.safety_manager import SafetyError

pumps2 = {n: SimPump(n) for n in FLOWS}
eng2 = StrictSequenceEngine(cfg, pumps2, {"Outlet": SimValve("Outlet")}, SimHeater(),
                            SafetyManager(cfg, pumps2, SimHeater()), Sig(),
                            collector=SimCollector(), push_pump=None)
blocked_msg = ""
_ev_mark2 = len(EVENTS)
try:
    eng2.run_sequence([dict(step)], None)
except SafetyError as e:
    blocked_msg = str(e)
started = [1 for t, k, n, d in EVENTS[_ev_mark2:] if k == "pump_start"]
check("강등 차단: 펌프 무기동", len(started) == 0, f"({len(started)})")
check("강등 차단: SafetyError 발생 + 안내 메시지",
      "push_pump(HPLC)" in blocked_msg, f"({blocked_msg[:40]}...)")

# 6) 수집라인 선헹굼(collect_preflush_vol_ml) E2E — compensated + WASH 포트에서
#    Outlet→COLLECT 만 앞당겨지고 니들 이벤트는 불변임을 확인 (1-step 런)
PF_VOL = 0.2                       # 선헹굼 부피 (mL)
PF_SEC = (PF_VOL / F) * 60.0       # = 6.0s @F=2.0
LINE_DELAY = (0.06 / F) * 60.0     # collection_line_vol_ml 0.06 → 1.8s


class SimCollectorWash(SimCollector):
    """WASH 좌표 지원 — compensated 모드 활성 조건"""

    def move_to_wash(self):
        rec("wash_move", "collector", None)
        return True, "ok"


# @codesyncer: 이전 런의 데몬 스레드가 clear() 이후에도 늦게 append 할 수 있어
#   list.clear() 대신 '시작 인덱스 스냅샷'으로 격리한다 (교차오염 FAIL 재발 방지).
cfg3 = Cfg()
cfg3.line_vol_inlet = dict(LINE_INLET)
cfg3.line_vol_valve_pump = dict(LINE_VP)
cfg3.line_vol_pump_merge = dict(LINE_PM)
cfg3.valve_internal_vol = dict(VALVE_INT)
cfg3.ACTIVE_PUMPS = list(FLOWS.keys())
cfg3.config_data["system_params"]["collect_line_mode"] = "compensated"
cfg3.config_data["system_params"]["collect_preflush_vol_ml"] = PF_VOL
# 스텝1 push 라인 프라임 (기포 제거) — 세척과 병행, 프리필 전 완료
PLP_VOL, PLP_RATE = 1.0, 10.0
cfg3.config_data["system_params"]["push_line_prime_vol_ml"] = PLP_VOL
cfg3.config_data["system_params"]["push_line_prime_rate_ml_min"] = PLP_RATE
pumps3 = {n: SimPump(n) for n in FLOWS}
eng3 = StrictSequenceEngine(cfg3, pumps3, {"Outlet": SimValve("Outlet")}, SimHeater(),
                            SafetyManager(cfg3, pumps3, SimHeater()), Sig(),
                            collector=SimCollectorWash(), push_pump=SimPushPump())
eng3.collector_start_tube = 1
_ev_mark, _log_mark = len(EVENTS), len(LOGS)
eng3.run_sequence([dict(step)], None)
EV3, LOG3 = EVENTS[_ev_mark:], LOGS[_log_mark:]

if os.environ.get("PF_DEBUG"):
    print(f"  [dbg] ev_mark={_ev_mark} total={len(EVENTS)}")
    for _t, _k, _n, _d in EV3:
        if _k in ("valve", "tube", "wash_move", "pump_start", "pump_stop"):
            print(f"  [dbg] {_t:8.2f} {_k:11s} {_n:8s} {_d}")

# 6-a) push 라인 프라임: 스텝1에서 프라임 유속으로 1회 기동 + 주입 전 완료
_plp_starts = [(t, d) for t, k, n, d in EV3
               if k == "pump_start" and n == "PUSH" and abs(d - PLP_RATE) < 1e-9]
_dose0 = min([t for t, k, n, d in EV3 if k == "pump_start" and n in FLOWS] or [0.0])
check("라인 프라임 기동 1회 (프라임 유속)", len(_plp_starts) == 1,
      f"({[d for _, d in _plp_starts]} vs rate={PLP_RATE})")
check("라인 프라임 로그", any("[PushLinePrime]" in m and "라인 충전" in m for m in LOG3))
if _plp_starts:
    _plp_stop = min([t for t, k, n, d in EV3
                     if k == "pump_stop" and n == "PUSH" and t > _plp_starts[0][0]]
                    or [1e9])
    check("라인 프라임이 첫 주입 전 완료",
          _plp_stop <= _dose0 + 0.5,
          f"(prime stop {_plp_stop:.1f}s vs 주입 {_dose0:.1f}s)")
    check("라인 프라임 지속시간 ≈ vol/rate",
          abs((_plp_stop - _plp_starts[0][0]) - (PLP_VOL / PLP_RATE * 60.0)) < 1.5,
          f"({_plp_stop - _plp_starts[0][0]:.1f}s vs {PLP_VOL / PLP_RATE * 60.0:.1f}s)")

_pf_log = [m for m in LOG3 if "선헹굼" in m]
check("선헹굼 로그 발생", bool(_pf_log), f"({_pf_log[:1]})")
check(f"선헹굼 조기전환 {PF_SEC:.1f}s 계산",
      any(f"{PF_SEC:.1f}s 조기 전환" in m for m in _pf_log), f"({_pf_log[:1]})")
check("스케줄 로그에 조기전환 표기",
      any("Outlet 조기전환" in m for m in LOG3))
# 이벤트 순서: Outlet→COLLECT(2) 가 첫 '수집' 웰 이동보다 먼저
# (시퀀스 시작 호밍의 pre-move 는 주입 전에 일어나므로 제외)
_inj0 = min([t for t, k, n, d in EV3 if k == "pump_start" and n in FLOWS]
            or [0.0])
_v_collect = [t for t, k, n, d in EV3 if k == "valve" and d == 2 and t > _inj0]
_t_moves = [t for t, k, n, d in EV3 if k == "tube" and t > _inj0]
# 본 push(실험 유속) 는 라인 프라임과 구분 — 주입 이후 1회
_main_push3 = [t for t, k, n, d in EV3
               if k == "pump_start" and n == "PUSH" and abs(d - F) < 1e-9]
check("본 push 는 주입 이후 1회 (프라임과 구분)",
      len(_main_push3) == 1 and _main_push3[0] > _inj0,
      f"({len(_main_push3)}회)")
check("Outlet→COLLECT 가 첫 웰 이동보다 선행",
      bool(_v_collect) and bool(_t_moves) and _v_collect[0] < _t_moves[0],
      f"(valve {_v_collect[:1]}, move {_t_moves[:1]})")
# 간격 ≈ 선헹굼 + 라인지연 (둘 다 흐름 중 발화 — 사이에 pause 없음)
if _v_collect and _t_moves:
    _gap = _t_moves[0] - _v_collect[0]
    check(f"간격 ≈ 선헹굼+라인지연 ({PF_SEC + LINE_DELAY:.1f}s)",
          abs(_gap - (PF_SEC + LINE_DELAY)) < 2.0, f"(실측 {_gap:.1f}s)")

print()
if fails:
    print(f"RESULT: {len(fails)} FAIL: {fails}")
    sys.exit(1)
print("RESULT: ALL PASS")
