# -*- coding: utf-8 -*-
"""분취기 도착-타이밍 동적 검증 — 실 엔진 + 에러 주입.

test_engine_liquid_front(정상 경로 12시나리오)를 보완하는 이상 경로/모델 분기 검증:
  S1 fifo vs S2 lifo  — purge_order 에 따라 Outlet→COLLECT '도착 부피'가 모델대로
                        이동하는가 (밸브 방향이 도착시간 모델을 따라가는지)
  S3 valve_fail       — Outlet→COLLECT 전환이 시리얼 예외로 실패해도
                        시퀀스 비중단 + 후속 이벤트(웰 이동·WASTE) 정시 발동
  S4 collector_fail   — 웰 이동 예외 시 밸브 스케줄 무영향
  S5 slow_collector   — 웰 이동 1.2s 블로킹: 경계 오차가 '비누적'(각 이벤트
                        자신의 지연만)으로 유지되는가
검증 축은 전부 '누적 펌핑 부피 V' (시간이 아니라 부피가 물리 진실).
"""
import os, sys, time, threading, math

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔/파이프 출력 방어
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# ── 공통 상수 (liquid_front 하네스와 동일 스케일) ──
R, POST, COLL, MIXING = 0.6, 0.2, 0.15, 0.10
TARGET, TUBE = 0.45, 0.15          # 3 tubes
FLOWS = {"A": 1.0, "B": 1.0}
F = sum(FLOWS.values())
LINE_INLET = {"A": 0.03, "B": 0.02}
LINE_VP = {"A": 0.03, "B": 0.02}
LINE_PM = {"A": 0.05, "B": 0.05}
VALVE_INT = {"A": 0.02, "B": 0.02}  # 3-way 내부볼륨 (tube_vol_switcher)

SRC = {k: LINE_INLET[k] + LINE_VP[k] for k in FLOWS}
INJ = {k: LINE_PM[k] + VALVE_INT[k] for k in FLOWS}
PURGE_SEC = max(SRC[k] / f * 60.0 for k, f in FLOWS.items())
INJPATH_SEC = max(INJ[k] / f * 60.0 for k, f in FLOWS.items())
V_PRE_FIFO = F * (PURGE_SEC + INJPATH_SEC) / 60.0
V_PRE_LIFO = F * INJPATH_SEC / 60.0
R_BASE = R + MIXING + POST


class Harness:
    """시나리오 1회 실행 — 독립 엔진/목/이벤트 기록."""

    def __init__(self, purge_order="fifo", valve_fail=False,
                 collector_fail=False, collector_delay=0.0):
        self.T0 = time.time()
        self.EVENTS = []
        self.LOCK = threading.Lock()
        h = self

        def rec(kind, name, data=None):
            with h.LOCK:
                h.EVENTS.append((time.time() - h.T0, kind, name, data))

        class SimDriver:
            def __init__(self, pump): self.pump = pump
            def is_stopped(self): return not self.pump.running

        class SimPump(ChemyxSmartPump):
            def __init__(self, name, capacity=6.0):
                self.name = name; self.capacity = capacity
                self.current_vol = 0.0; self.running = False
                self.target_flow = 0.0; self.status = "Idle"
                self.is_refilling = False; self._abort_refill = False
                self.wash_volume = 3.0; self.prime_rate = 8.0
                self.driver = SimDriver(self)
                self.segments = []; self.primes = []
                self._pending_fill = 0.0; self._pending_prime = 0.0
            def set_flow(self, rate): self.target_flow = float(rate)
            def start(self):
                self.running = True
                self.segments.append([time.time() - h.T0, None,
                                      self.target_flow, float(self.current_vol)])
                rec("pump_start", self.name, self.target_flow)
            def stop(self):
                if self.running and self.segments and self.segments[-1][1] is None:
                    self.segments[-1][1] = time.time() - h.T0
                self.running = False
            def refill_prepare(self, port, volume=None):
                self._pending_fill = float(volume) if volume else self.capacity
                return True
            def refill_trigger(self): self.is_refilling = True
            def refill_complete(self):
                time.sleep(0.1)
                self.current_vol = min(self.capacity, self._pending_fill)
                self.is_refilling = False
            def prime_prepare(self):
                if self.current_vol > 0.0:
                    self._pending_prime = float(self.current_vol)
                    return True
                return False
            def prime_trigger(self): pass
            def prime_complete(self):
                t0 = time.time() - h.T0; time.sleep(0.1); t1 = time.time() - h.T0
                self.primes.append((t0, t1, self._pending_prime))
                self.current_vol = 0.0
            def dispensed(self, a, b):
                v = 0.0
                for t0, t1, rate, avail in self.segments:
                    if rate <= 0: continue
                    end = t1 if t1 is not None else (time.time() - h.T0)
                    end = min(end, t0 + (avail / rate) * 60.0)
                    lo, hi = max(a, t0), min(b, end)
                    if hi > lo: v += rate * (hi - lo) / 60.0
                for p0, p1, pv in self.primes:
                    lo, hi = max(a, p0), min(b, p1)
                    if hi > lo and p1 > p0: v += pv * (hi - lo) / (p1 - p0)
                return v

        class SimOutlet:
            def __init__(self):
                self.position = 1
                self.fail_next_collect = valve_fail
            def set_position(self, pos):
                if pos == 2 and self.fail_next_collect:
                    self.fail_next_collect = False
                    rec("valve_fail", "Outlet", pos)
                    raise IOError("serial write timeout (injected)")
                self.position = pos
                rec("valve", "Outlet", pos)

        class SimCollector:
            is_connected = True; total_tubes = 88
            def __init__(self): self._pos = 0; self._moves = 0
            def home(self): self._pos = 0; return True, "homed"
            def move_to_tube(self, n):
                self._moves += 1
                if collector_fail and self._moves == 2:  # 2번째 이동(첫 경계) 실패
                    rec("tube_fail", "collector", n)
                    raise IOError("stepper stall (injected)")
                if collector_delay > 0:
                    time.sleep(collector_delay)
                self._pos = n
                rec("tube", "collector", n)
                return True, "ok"
            def get_position(self): return self._pos
            def get_well_id(self, n): return f"T{n}"

        class SimHeater:
            def __init__(self): self.target_temp = 25.0
            def set_temperature(self, t): self.target_temp = float(t)
            def get_temperature(self): return self.target_temp
            def stop(self): self.target_temp = 0.0

        class Sig:
            def __init__(self):
                emit = lambda *a, **k: None
                for n in ("sig_log", "sig_status", "sig_phase_progress",
                          "sig_progress", "sig_finished", "sig_error"):
                    setattr(self, n, type("S", (), {"emit": staticmethod(emit)})())

        class Cfg:
            PUMP_VALVE_MAP = {}
            reactor_vol = R
            ACTIVE_PUMPS = list(FLOWS.keys())
            tjunction_line_vols = {}
            line_vol_inlet = dict(LINE_INLET)
            line_vol_valve_pump = dict(LINE_VP)
            line_vol_pump_merge = dict(LINE_PM)
            valve_internal_vol = dict(VALVE_INT)
            mixing_line_dead_vol = MIXING
            config_data = {"system_params": {
                "post_reactor_vol_ml": POST,
                "collection_line_vol_ml": COLL,
                "temp_tolerance_c": 0.5,
                "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0,
                "max_step_volume_ml": 500.0,
                "wash_mode": "off",
                "prefill_mode": "every_step",
                "syringe_refill_rate": 20.0,
                "purge_order": purge_order,
                # @codesyncer(2026-07-28): SimPump 은 자동정지(volume 소진) 미모델
                #   (running 은 engine stop 까지 True) — 자동정지 유예가 매 도징마다
                #   풀타임 대기해 밸브/도착 타이밍 검증이 어긋난다. 이 테스트는
                #   '레거시 stop 타이밍' 회귀이므로 유예 0 으로 핀. 유예 기능은
                #   test_level_gates.py 의 dosing_grace 3종이 전용 커버.
                "dosing_autostop_grace_sec": 0.0,
            }}

        self.pumps = {n: SimPump(n) for n in FLOWS}
        self.valves = {"Outlet": SimOutlet()}
        self.collector = SimCollector()
        cfg = Cfg()
        self.eng = StrictSequenceEngine(cfg, self.pumps, self.valves, SimHeater(),
                                        SafetyManager(cfg, self.pumps, SimHeater()),
                                        Sig(), collector=self.collector)
        self.eng.collector_start_tube = 1
        self.logs = []
        self.eng._log = lambda m, _l=self.logs: _l.append(str(m))

    def run(self):
        step = {"temp": 25.0, "vol_ml": TARGET, "residence_time": R / F * 60,
                "inlet_ports": {k: 2 for k in FLOWS}, "flows": dict(FLOWS),
                "collect_volume_per_tube": TUBE, "meta": {}}
        self.eng.run_sequence([step], None)
        return self

    # 누적 펌핑 부피
    def V(self, a, b):
        return sum(p.dispensed(a, b) for p in self.pumps.values())

    def ev(self, kind, data=None):
        return [(t, d) for t, k, n, d in self.EVENTS
                if k == kind and (data is None or d == data)]

    def inj0(self, t_collect):
        starts = sorted(t for t, k, n, d in self.EVENTS if k == "pump_start")
        for t in starts:
            if t < t_collect and t_collect - t <= 120:
                return t
        return None


TOL = 0.06

# ══ S1/S2: purge_order 에 따른 도착 부피 → 밸브 전환 시점 이동 ══
print("=== S1 arrival_fifo / S2 arrival_lifo ===")
arrivals = {}
for order, v_pre in (("fifo", V_PRE_FIFO), ("lifo", V_PRE_LIFO)):
    h = Harness(purge_order=order).run()
    collects = h.ev("valve", 2)
    wastes = h.ev("valve", 1)
    check(f"[{order}] COLLECT 1회", len(collects) == 1, f"({len(collects)})")
    t_c = collects[0][0]
    i0 = h.inj0(t_c)
    v_head = h.V(i0, t_c)
    expect = R_BASE + v_pre
    arrivals[order] = v_head
    check(f"[{order}] HEAD 도착부피 V≈{expect:.3f}", abs(v_head - expect) < TOL,
          f"(V={v_head:.3f})")
    t_w = next((t for t, d in wastes if t > t_c), None)
    check(f"[{order}] 수집종료 WASTE 존재+정합", t_w is not None
          and abs(h.V(i0, t_w) - (expect + TARGET + COLL)) < TOL + 0.01,
          f"(V={h.V(i0, t_w):.3f} vs {expect + TARGET + COLL:.3f})" if t_w else "")
    check(f"[{order}] 수집창 부피=target+flush",
          t_w is not None and abs(h.V(t_c, t_w) - (TARGET + COLL)) < TOL + 0.01,
          f"(={h.V(t_c, t_w):.3f})" if t_w else "")
# lifo 는 fifo 보다 정확히 퍼지 부피만큼 일찍 (밸브 방향이 도착모델 추종)
d_expect = V_PRE_FIFO - V_PRE_LIFO
check("S1-2 fifo−lifo 도착부피 차 = 퍼지부피",
      abs((arrivals["fifo"] - arrivals["lifo"]) - d_expect) < TOL,
      f"(차={arrivals['fifo'] - arrivals['lifo']:.3f} vs {d_expect:.3f})")

# ══ S3: Outlet→COLLECT 전환 실패 (시리얼 예외 주입) ══
print("=== S3 valve_fail(Outlet COLLECT 예외) ===")
h = Harness(valve_fail=True)
try:
    h.run()
    crashed = False
except Exception as e:
    crashed = True
    print(f"  (엔진 예외: {e})")
check("S3 시퀀스 비중단(예외 격리)", not crashed)
check("S3 실패 주입 발생", len(h.ev("valve_fail")) == 1)
check("S3 실패 로그 기록", any("FAILED" in m for m in h.logs))
# 후속 이벤트는 계속: 웰 이동 + 수집종료 WASTE 발동
tubes = h.ev("tube")
wastes = h.ev("valve", 1)
check("S3 후속 웰 이동 계속", len(tubes) >= 3, f"({len(tubes)})")
check("S3 수집종료 WASTE 발동", len(wastes) >= 1)
# ⚠ 밸브는 물리적으로 WASTE 에 남음 — 제품 유실이지만 '안전측' (문서화 목적 확인)
check("S3 밸브 최종 WASTE(안전측)", h.valves["Outlet"].position == 1)

# ══ S4: 웰 이동 실패 → 밸브 스케줄 무영향 ══
print("=== S4 collector_fail(웰 이동 예외) ===")
h = Harness(collector_fail=True).run()
collects = h.ev("valve", 2)
t_c = collects[0][0]
i0 = h.inj0(t_c)
check("S4 이동실패 주입 발생", len(h.ev("tube_fail")) == 1)
check("S4 HEAD 정시", abs(h.V(i0, t_c) - (R_BASE + V_PRE_FIFO)) < TOL,
      f"(V={h.V(i0, t_c):.3f})")
t_w = next((t for t, d in h.ev("valve", 1) if t > t_c), None)
check("S4 WASTE 정시(밸브 스케줄 무영향)",
      t_w is not None and abs(h.V(i0, t_w) - (R_BASE + V_PRE_FIFO + TARGET + COLL)) < TOL + 0.01)
check("S4 실패 로그", any("FAILED" in m or "error" in m for m in h.logs))

# ══ S5: 느린 웰 이동(1.2s) → 경계 오차 비누적 ══
print("=== S5 slow_collector(이동 1.2s 블로킹) ===")
DELAY = 1.2
h = Harness(collector_delay=DELAY).run()
collects = h.ev("valve", 2)
t_c = collects[0][0]
i0 = h.inj0(t_c)
tubes = [t for t, d in h.ev("tube")][-4:]  # 이 step 의 이동들 (수집 3 + wash)
n_tubes = math.ceil(TARGET / TUBE)
# 각 경계의 부피 오차 ≤ '직전 이동 1회의 블로킹 지연' 등가부피 + 허용오차 (비누적)
bound = DELAY * F / 60.0 + TOL
ok_bounds = True
detail = []
for i in range(1, n_tubes):
    v_t = h.V(i0, tubes[i])
    err = v_t - (R_BASE + V_PRE_FIFO + i * TUBE)
    detail.append(f"t{i + 1}:+{err:.3f}")
    if not (-TOL < err < bound):
        ok_bounds = False
check(f"S5 경계 오차 비누적(각 ≤{bound:.3f}mL)", ok_bounds, " ".join(detail))
t_w = next((t for t, d in h.ev("valve", 1) if t > t_c), None)
err_w = h.V(i0, t_w) - (R_BASE + V_PRE_FIFO + TARGET + COLL)
check("S5 WASTE 오차 ≤ 1회 지연 등가", -TOL < err_w < bound, f"(+{err_w:.3f}mL)")

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
