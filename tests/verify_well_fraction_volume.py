# -*- coding: utf-8 -*-
"""웰 분취량 실측 검증 — "1.5 mL 씩 분취" 설정이 실제로 웰당 1.5 mL 인가?

실제 StrictSequenceEngine 을 mock 하드웨어로 구동하고, 니들(웰) 이벤트 사이에
펌프가 실제로 토출한 부피를 적분해 웰별 실제 담긴 양을 측정한다.
(타이머는 시간 구동이므로 '설정 부피 → 시간 → 실제 부피' 왕복이 성립하는지가 핵심)

검증 항목
  1. 중간 웰들은 정확히 vol_per_tube (1.5 mL)
  2. 마지막 웰 = 총 수집부피 − (n−1)×1.5  (나머지)
  3. HPLC push 경로의 +0.1×reactor 여유가 웰 수/마지막 웰 양에 미치는 영향
     (target 이 1.5 의 배수면 '여유분 전용 웰'이 하나 더 생김)
  4. 수집 창 총량 = target + 0.1×reactor

실행:  py -3.14 tests\\verify_well_fraction_volume.py   (루트에서)
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
EVENTS = []
LOGS = []
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
    """통신 없는 Chemyx 시뮬레이터 — 흐름 구간 기록 + 적분"""

    def __init__(self, name, capacity=10.0):
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
        self.prime_rate = 30.0
        self.driver = SimDriver(self)
        self.segments = []          # [t0, t1|None, rate, avail_at_start]
        self._pending_fill = 0.0
        self._pending_wash = 0.0

    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True
        self.segments.append([time.time() - T0, None, self.target_flow,
                              float(self.current_vol)])
        rec("pump_start", self.name, self.target_flow)

    def stop(self):
        if self.running and self.segments and self.segments[-1][1] is None:
            self.segments[-1][1] = time.time() - T0
        self.running = False
        rec("pump_stop", self.name)

    def refill_prepare(self, port, volume=None):
        self._pending_fill = float(volume) if volume else self.capacity
        return True

    def refill_trigger(self):
        self.is_refilling = True

    def refill_complete(self):
        time.sleep(0.05)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_fill)
        self.is_refilling = False

    def refill(self, port, volume=None):
        self.current_vol = min(self.capacity, self.current_vol + float(volume or 0.0))
        return True

    def prime_prepare(self):
        return self.current_vol > 0.0

    def prime_trigger(self):
        pass

    def prime_complete(self):
        time.sleep(0.05)
        self.current_vol = 0.0

    # wash (push 병행 세척)
    def wash_infuse_prepare(self, waste_port=12):
        if self.current_vol < 0.05:
            return False
        self._pending_wash = float(self.current_vol)
        return True

    def wash_infuse_trigger(self):
        pass

    def wash_infuse_complete(self):
        time.sleep(0.05)
        self.current_vol = 0.0
        return True

    def wash_withdraw_prepare(self, solvent_port=1):
        self._pending_wash = min(self.wash_volume,
                                 max(0.0, self.capacity - self.current_vol))
        return self._pending_wash >= 0.05

    def wash_withdraw_trigger(self):
        pass

    def wash_withdraw_complete(self):
        time.sleep(0.05)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_wash)

    def dispensed(self, a, b):
        """[a,b] 구간 실제 토출량 — 가용부피 소진 시 자동정지 모사"""
        v = 0.0
        for t0, t1, rate, avail in self.segments:
            if rate <= 0:
                continue
            end = t1 if t1 is not None else (time.time() - T0)
            end = min(end, t0 + (avail / rate) * 60.0)   # 자동정지
            lo, hi = max(a, t0), min(b, end)
            if hi > lo:
                v += rate * (hi - lo) / 60.0
        return v


class SimPushPump:
    """HPLC push — 가용부피 무제한"""

    def __init__(self):
        self.running = False
        self.target_flow = 0.0
        self.segments = []

    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True
        self.segments.append([time.time() - T0, None, self.target_flow])
        rec("pump_start", "PUSH", self.target_flow)

    def stop(self):
        if self.running and self.segments and self.segments[-1][1] is None:
            self.segments[-1][1] = time.time() - T0
        self.running = False
        rec("pump_stop", "PUSH")

    def dispensed(self, a, b):
        v = 0.0
        for t0, t1, rate in self.segments:
            end = t1 if t1 is not None else (time.time() - T0)
            lo, hi = max(a, t0), min(b, end)
            if hi > lo:
                v += rate * (hi - lo) / 60.0
        return v


class SimValve:
    def __init__(self, name):
        self.name = name
        self.position = 1

    def set_position(self, pos):
        self.position = pos
        rec("valve", self.name, pos)


class SimCollector:
    is_connected = True
    total_tubes = 96
    max_volume_per_well_ml = 1.5      # collector_plate96 기본값과 동일

    def __init__(self):
        self._pos = 0

    def home(self):
        self._pos = 0
        return True, "homed"

    def move_to_tube(self, n):
        self._pos = n
        rec("tube", "collector", n)
        return True, "ok"

    def move_to_wash(self):
        rec("wash_move", "collector", None)
        return True, "ok"

    def get_position(self):
        return self._pos

    def get_well_id(self, n):
        return f"W{n}"


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
        for n in ("sig_status", "sig_phase_progress", "sig_progress",
                  "sig_finished", "sig_error"):
            setattr(self, n, type("S", (), {"emit": staticmethod(none)})())
        self.sig_log = type("S", (), {"emit": staticmethod(
            lambda m: LOGS.append(m))})()


REACTOR = 2.4      # 실기 반응기 실측 (mL)
POST = 0.2066
MIXING = 0.0954
V_LINE = 0.25      # collection_line_vol_ml (실측)


class Cfg:
    PUMP_VALVE_MAP = {}
    tjunction_line_vols = {}
    line_vol_inlet = {}
    line_vol_valve_pump = {}
    line_vol_pump_merge = {}
    valve_internal_vol = {}
    selector_internal_vol = {}
    mixing_line_dead_vol = MIXING

    def __init__(self):
        self.reactor_vol = REACTOR
        self.ACTIVE_PUMPS = []
        self.config_data = {
            "system_params": {
                "post_reactor_vol_ml": POST,
                "collection_line_vol_ml": V_LINE,
                "temp_tolerance_c": 0.5,
                "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0,
                "max_step_volume_ml": 500.0,
                "wash_mode": "off",
                "prefill_mode": "port_change",
                "purge_order": "lifo",
                "priming_rate_ml_min": 30.0,
                "syringe_refill_rate": 30.0,
                "collect_line_mode": "compensated",
            },
            "roles": {},
        }


# ── 시나리오 실행기 ───────────────────────────────────────────
VPT = 1.5          # ★ 검증 대상: 웰당 1.5 mL 설정
F_TOTAL = 20.0     # 시뮬 단축용 고유속 (검증 논리는 유속 무관)


def run_case(target_vol, label):
    """1-step 런 → 웰별 실제 담긴 부피 측정"""
    global EVENTS
    flows = {"A": F_TOTAL / 2, "B": F_TOTAL / 2}
    step = {"temp": 25.0, "vol_ml": target_vol,
            "residence_time": REACTOR / F_TOTAL * 60,
            "inlet_ports": {k: 2 for k in flows}, "flows": dict(flows),
            "collect_volume_per_tube": VPT, "meta": {}}

    pumps = {n: SimPump(n) for n in flows}
    push = SimPushPump()
    cfg = Cfg()
    cfg.ACTIVE_PUMPS = list(flows)
    cfg.line_vol_inlet = {k: 0.0754 for k in flows}
    cfg.line_vol_valve_pump = {k: 0.0597 for k in flows}
    cfg.line_vol_pump_merge = {k: 0.2262 for k in flows}
    cfg.valve_internal_vol = {k: 0.0507 for k in flows}
    cfg.selector_internal_vol = {k: 0.0224 for k in flows}
    collector = SimCollector()
    eng = StrictSequenceEngine(cfg, pumps, {"Outlet": SimValve("Outlet")},
                               SimHeater(), SafetyManager(cfg, pumps, SimHeater()),
                               Sig(), collector=collector, push_pump=push)
    eng.collector_start_tube = 1

    mark = len(EVENTS)
    eng.run_sequence([step], None)
    ev = EVENTS[mark:]

    # 주입 시작 이후의 니들 이벤트만 (시작 호밍 pre-move 제외)
    inj0 = min([t for t, k, n, d in ev if k == "pump_start" and n in flows] or [0.0])
    moves = [(t, d) for t, k, n, d in ev if k == "tube" and t > inj0]
    wash_moves = [t for t, k, n, d in ev if k == "wash_move" and t > inj0]
    t_end = min(wash_moves) if wash_moves else (
        max([t for t, k, n, d in ev if k == "valve" and d == 1 and t > inj0]
            or [time.time() - T0]))

    def V(a, b):
        return sum(p.dispensed(a, b) for p in pumps.values()) + push.dispensed(a, b)

    wells = []
    for i, (t, tube) in enumerate(moves):
        t_next = moves[i + 1][0] if i + 1 < len(moves) else t_end
        wells.append((tube, V(t, t_next)))
    # 타이머 지연(lag) = 마지막 니들 이벤트 − push 정지 시각.
    # 타이머는 '펌핑 경과' 기준인데 주입 도징 창 시작과 타이머 resume 사이의
    # 순차 트리거 간격(0.35s/펌프)+명령 오버헤드만큼 뒤처진다 → push 가 먼저
    # 멈추고 마지막 웰이 그만큼 덜 받는다(무유량 구간에서 종료 이벤트 발화).
    # push 의 '첫' 정지(마지막 웰 이동 이후) — cleanup 의 중복 stop 은 제외
    _after = moves[-1][0] if moves else 0.0
    push_stops = sorted(t for t, k, n, d in ev
                        if k == "pump_stop" and n == "PUSH" and t > _after)
    push_stop = push_stops[0] if push_stops else t_end
    lag = max(0.0, t_end - push_stop)
    return wells, (V(moves[0][0], t_end) if moves else 0.0), lag


fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


print(f"=== 웰 분취량 검증: 설정 {VPT} mL/well, F={F_TOTAL} mL/min, "
      f"reactor={REACTOR} mL (여유 0.1R={0.1 * REACTOR:.2f} mL) ===\n")

def report(case, target, wells, total, lag):
    exp_total = target + 0.1 * REACTOR
    exp_n = max(1, math.ceil(exp_total / VPT))
    exp_last = exp_total - (exp_n - 1) * VPT
    lag_vol = lag * F_TOTAL / 60.0        # 타이머 지연이 삼킨 부피
    print(f"\n[CASE {case}] target={target} mL → 수집총량 기대 {exp_total:.2f} mL "
          f"({exp_n} wells), 타이머 지연 {lag:.2f}s (= {lag_vol * 1000:.0f} µL @F={F_TOTAL})")
    for tube, vol in wells:
        print(f"    W{tube}: {vol:.3f} mL")
    check(f"C{case} 웰 개수", len(wells) == exp_n, f"({len(wells)} vs {exp_n})")
    check(f"C{case} 중간 웰 전부 = {VPT} mL (설정대로)",
          all(abs(v - VPT) < 0.05 for _, v in wells[:-1]),
          f"({[round(v, 3) for _, v in wells[:-1]]})")
    # 마지막 웰만 '타이머 지연 × 유속' 만큼 덜 받는다 (push 가 먼저 정지)
    check(f"C{case} 마지막 웰 = 나머지 {exp_last:.2f} − 지연분 "
          f"{lag_vol:.2f} = {exp_last - lag_vol:.2f} mL",
          bool(wells) and abs(wells[-1][1] - (exp_last - lag_vol)) < 0.06,
          f"({wells[-1][1]:.3f})")
    check(f"C{case} 수집 총량 = target+0.1R − 지연분 ({exp_total - lag_vol:.2f})",
          abs(total - (exp_total - lag_vol)) < 0.08, f"({total:.3f})")
    return lag


# ── CASE 1: target 5.0 (1.5 의 배수가 아님) ──────────────────
lag1 = report(1, 5.0, *run_case(5.0, "A"))
# ── CASE 2: target 4.5 (1.5 의 정확한 배수) ─────────────────
lag2 = report(2, 4.5, *run_case(4.5, "B"))

# ── 실기 영향 환산 ────────────────────────────────────────────
LAG = (lag1 + lag2) / 2.0
print(f"\n[실기 환산] 타이머 지연 {LAG:.2f}s (유속 무관 — 순차 트리거/명령 오버헤드)")
print("  마지막 웰 부족분 = 지연 × 총유속:")
for f_real in (0.481, 1.0, 2.0, 20.0):
    print(f"    F={f_real:5.3f} mL/min → {LAG * f_real / 60 * 1000:7.1f} µL "
          f"({LAG * f_real / 60 / VPT * 100:5.2f}% of {VPT} mL)")
print("\n[참고] 웰 이동 중 유출량 = 총유속 × 이동시간 "
      "(시뮬은 이동 0초 가정 — 실기 손실은 아래 계산값)")
for f_real, t_move in ((0.481, 1.5), (0.481, 3.0), (2.0, 1.5)):
    print(f"    F={f_real} mL/min, 이동 {t_move}s → {f_real * t_move / 60 * 1000:.1f} µL "
          f"({f_real * t_move / 60 / VPT * 100:.2f}% of {VPT} mL)")

print()
if fails:
    print(f"RESULT: {len(fails)} FAIL: {fails}")
    sys.exit(1)
print("RESULT: ALL PASS")
