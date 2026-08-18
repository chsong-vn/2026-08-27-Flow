"""시퀀스 엔진 end-to-end 검증 — 액체 전선(liquid front) 시뮬레이션

실제 StrictSequenceEngine을 mock 하드웨어로 통째로 구동하고,
펌프가 '실제로 보낸 누적 부피 V(t)'를 적분하여 타이머 이벤트의 정합을 검사한다.

제어 목적 (legacy syringe push 경로):
  V(Outlet→COLLECT)   == vol_reactor              (HEAD 도달)
  V(i번째 well 이동)   == vol_reactor + i×tube_vol  (분획 경계)
  V(wash well 이동)    == vol_reactor + target_vol
  V(Outlet→WASTE)     == vol_reactor + target_vol + vol_collection
  주입량(펌프별)        == (flow_i/F)×target_vol     (prefill 충전량과 일치)
  수집창 펌핑량         == target_vol + vol_collection

시나리오: balanced | unbalanced | pause | twostep
"""
import os, sys, time, threading, math

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔/파이프 출력 방어
except Exception:
    pass
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCEN = sys.argv[1] if len(sys.argv) > 1 else "balanced"

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

T0 = time.time()
EVENTS = []  # (t, kind, name, data)
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
    """통신 없는 Chemyx 시뮬레이터 — 흐름 구간과 가용부피를 기록"""

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
        self.prime_rate = 8.0
        self.driver = SimDriver(self)
        self.segments = []   # [t_start, t_end|None, rate, vol_at_start]
        self.primes = []     # (t0, t1, vol)
        self._pending_fill = 0.0
        self._pending_prime = 0.0

    # ── dosing 인터페이스 ──
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

    # ── refill / prime 인터페이스 ──
    def refill_prepare(self, port, volume=None):
        self._pending_fill = float(volume) if volume else self.capacity
        return True

    def refill_trigger(self):
        self.is_refilling = True

    def refill_complete(self):
        time.sleep(0.15)
        self.current_vol = min(self.capacity, self._pending_fill)
        self.is_refilling = False
        self.status = f"Refilled {self.current_vol:.3f}mL"
        rec("refill", self.name, self.current_vol)

    def prime_prepare(self):
        if self.current_vol > 0.0:
            self._pending_prime = float(self.current_vol)
            return True
        return False

    def prime_trigger(self):
        pass

    def prime_complete(self):
        t0 = time.time() - T0
        time.sleep(0.12)
        t1 = time.time() - T0
        self.primes.append((t0, t1, self._pending_prime))
        rec("prime", self.name, self._pending_prime)
        self.current_vol = 0.0
        self.status = "Primed"

    # ── 적분기: [a, b] 동안 실제 디스펜스 부피 (가용부피 자동정지 모사) ──
    def dispensed(self, a, b):
        v = 0.0
        for t0, t1, rate, avail in self.segments:
            if rate <= 0:
                continue
            end = t1 if t1 is not None else (time.time() - T0)
            # Chemyx 자동정지: 가용부피 소진 시점 이후 flow=0
            exhaust = t0 + (avail / rate) * 60.0
            end = min(end, exhaust)
            lo, hi = max(a, t0), min(b, end)
            if hi > lo:
                v += rate * (hi - lo) / 60.0
        for p0, p1, pv in self.primes:
            lo, hi = max(a, p0), min(b, p1)
            if hi > lo and p1 > p0:
                v += pv * (hi - lo) / (p1 - p0)
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
        return self.target_temp  # 즉시 도달

    def stop(self):
        self.target_temp = 0.0


class Sig:
    def __init__(self):
        emit = lambda *a, **k: None
        for n in ("sig_log", "sig_status", "sig_phase_progress",
                  "sig_progress", "sig_finished", "sig_error"):
            setattr(self, n, type("S", (), {"emit": staticmethod(emit)})())


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = 0.6
    ACTIVE_PUMPS = []
    tjunction_line_vols = {}
    line_vol_inlet = {}
    line_vol_valve_pump = {}
    line_vol_pump_merge = {}
    mixing_line_dead_vol = 0.0
    config_data = {"system_params": {
        "post_reactor_vol_ml": 0.2,
        "collection_line_vol_ml": 0.15,
        "temp_tolerance_c": 0.5,
        "heater_reach_timeout_sec": 30.0,
        "max_total_flow_ml_min": 100.0,
        "max_step_volume_ml": 500.0,
        "wash_mode": "off",
        "prefill_mode": "every_step",
        "syringe_refill_rate": 20.0,
    }}


# ── 시나리오 구성 ──
R, POST, COLL = 0.6, 0.2, 0.15
TARGET, TUBE = 0.45, 0.15  # 3 tubes

if SCEN == "unbalanced":
    FLOWS = {"A": 1.2, "B": 0.8}
elif SCEN == "extreme":
    FLOWS = {"A": 1.5, "B": 0.5}
elif SCEN == "deadvol1":
    FLOWS = {"A": 2.0}          # 단일 펌프 + 데드볼륨
elif SCEN == "deadvol_unbal":
    FLOWS = {"A": 1.2, "B": 0.8}  # 비대칭 유속 + 비대칭 라인 (max식 검증)
elif SCEN == "deadvol_tj":
    FLOWS = {"A": 0.8, "B": 0.7, "C": 0.5}  # 3펌프 T-junction 캐스케이드
else:
    FLOWS = {"A": 1.0, "B": 1.0}
F = sum(FLOWS.values())

# 채널 구간 데드볼륨 (deadvol* 시나리오에서만 설정)
if SCEN in ("deadvol", "deadvol_hplc", "deadvol_repeat", "deadvol_compress"):
    LINE_INLET = {"A": 0.03, "B": 0.02}
    LINE_VP = {"A": 0.03, "B": 0.02}
    LINE_PM = {"A": 0.05, "B": 0.05}
    MIXING = 0.10
elif SCEN == "deadvol1":
    LINE_INLET = {"A": 0.04}
    LINE_VP = {"A": 0.02}
    LINE_PM = {"A": 0.05}
    MIXING = 0.10
elif SCEN == "deadvol_unbal":
    LINE_INLET = {"A": 0.05, "B": 0.01}
    LINE_VP = {"A": 0.03, "B": 0.01}
    LINE_PM = {"A": 0.06, "B": 0.03}
    MIXING = 0.08
elif SCEN == "deadvol_tj":
    LINE_INLET = {"A": 0.02, "B": 0.015, "C": 0.01}
    LINE_VP = {"A": 0.02, "B": 0.015, "C": 0.01}
    LINE_PM = {"A": 0.05, "B": 0.04, "C": 0.03}
    MIXING = 0.08
elif SCEN == "deadvol_partial":
    LINE_INLET = {"A": 0.06}   # A만 설정, B는 미설정(0) — 부분 구성
    LINE_VP = {}
    LINE_PM = {"A": 0.05}
    MIXING = 0.0
    FLOWS = {"A": 1.0, "B": 1.0}
else:
    LINE_INLET, LINE_VP, LINE_PM, MIXING = {}, {}, {}, 0.0
SRC = {k: LINE_INLET.get(k, 0) + LINE_VP.get(k, 0) for k in FLOWS}
INJ = {k: LINE_PM.get(k, 0) for k in FLOWS}
TJ = {1: 0.06} if SCEN == "deadvol_tj" else {}
# 유체 압축성(시스템 컴플라이언스): 가압 저장 부피 V_c, 시정수 τ
COMPLIANCE_VC = 0.08 if SCEN == "deadvol_compress" else 0.0
COMPLIANCE_TAU = 1.5

# pre-plug 시간 (엔진과 동일한 T-junction 캐스케이드 식)
_ordered = list(FLOWS.keys())
_n = len(_ordered)
PRE_SEC = 0.0
for _idx, _k in enumerate(_ordered):
    _f = FLOWS[_k]
    if _f <= 0:
        continue
    _t = (INJ[_k] / _f) * 60.0
    _j_enter = 1 if _idx <= 1 else _idx
    for _j in range(_j_enter, _n - 1):
        _Fj = sum(FLOWS[_ordered[_q]] for _q in range(0, _j + 1))
        _t += (TJ.get(_j, 0.0) / _Fj) * 60.0 if _Fj > 0 else 0.0
    PRE_SEC = max(PRE_SEC, _t)
PRE_SEC += max([(SRC[k] / f) * 60.0 for k, f in FLOWS.items() if f > 0] or [0.0])
V_PRE = F * PRE_SEC / 60.0
# 물리 임계값: 헤드 도달 시점의 누적 펌핑 부피 = pre-plug + mixing + reactor + post
# (post = 반응기 출구→아웃렛밸브 구간 — Outlet 전환은 헤드가 '밸브'에 도달할 때.
#  2026-07-06 반영: 엔진 기본식에 post_reactor 포함)
# (엔진의 deficit 시간 보정은 비대칭 유속저하를 메우기 위한 것으로, 잔차는
#  +방향(수집 시작이 수십 µL 늦음 = waste 쪽)이라 제품 유실이 없는 안전 방향)
R_EFF = R + MIXING + POST + V_PRE


def step_expect(si):
    """step별 유효 src(라인 상태 추적 반영)와 HEAD 임계값.
    같은 포트 재사용 step(si>=1)은 inlet 라인이 시약으로 차 있어 valve_pump만 보정.
    (repeat 외 시나리오는 전역 R_EFF — tj 캐스케이드 포함 — 그대로 사용)"""
    if SCEN == "deadvol_repeat" and si >= 1:
        src_eff = {k: LINE_VP.get(k, 0.0) for k in FLOWS}
        pre = (max([(src_eff[k] / f) * 60.0 for k, f in FLOWS.items() if f > 0] or [0.0])
               + max([(INJ[k] / f) * 60.0 for k, f in FLOWS.items() if f > 0] or [0.0]))
        return src_eff, R + MIXING + POST + F * pre / 60.0
    return dict(SRC), R_EFF

step = {"temp": 25.0, "vol_ml": TARGET, "residence_time": R / F * 60,
        "inlet_ports": {k: 2 for k in FLOWS}, "flows": dict(FLOWS),
        "collect_volume_per_tube": TUBE, "meta": {}}
plan = [step, dict(step)] if SCEN in ("twostep", "deadvol_repeat") else [step]

pumps = {n: SimPump(n) for n in FLOWS}

class SimPushPump:
    """HPLC push 시뮬레이터 — 흐름 구간 기록 (가용부피 무제한)"""
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

push_pump = SimPushPump() if SCEN == "deadvol_hplc" else None
valves = {"Outlet": SimValve("Outlet")}
heater = SimHeater()
collector = SimCollector()
cfg = Cfg()
cfg.line_vol_inlet = dict(LINE_INLET)
cfg.line_vol_valve_pump = dict(LINE_VP)
cfg.line_vol_pump_merge = dict(LINE_PM)
cfg.mixing_line_dead_vol = MIXING
cfg.ACTIVE_PUMPS = list(FLOWS.keys())
cfg.tjunction_line_vols = dict(TJ)
if COMPLIANCE_VC > 0:
    cfg.config_data["system_params"]["system_compliance_vol_ml"] = COMPLIANCE_VC
eng = StrictSequenceEngine(cfg, pumps, valves, heater,
                           SafetyManager(cfg, pumps, heater), Sig(),
                           collector=collector, push_pump=push_pump)
eng.collector_start_tube = 1

# pause 시나리오: injection 시작 4초 후 5초간 pause
if SCEN == "pause":
    def pauser():
        while eng.injection_start_ts is None:
            time.sleep(0.1)
        time.sleep(4.0)
        eng.pause_event.clear()
        rec("pause", "user", "clear")
        time.sleep(5.0)
        eng.pause_event.set()
        rec("pause", "user", "set")
    threading.Thread(target=pauser, daemon=True).start()

print(f"=== SCENARIO: {SCEN} | flows={FLOWS} F={F} ===")
t_run0 = time.time()
eng.run_sequence(plan, None)
print(f"run time: {time.time() - t_run0:.1f}s")

# ── 검증 ──
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# step별 injection 시작 시각: 첫 pump_start 직전의 valve/refill 패턴 대신
# injection 시작 = 해당 step의 '첫 dosing pump_start' (refill 직후)
pump_starts = [(t, n, d) for t, k, n, d in EVENTS if k == "pump_start"]
valve_evs = [(t, d) for t, k, n, d in EVENTS if k == "valve"]
tube_evs = [(t, d) for t, k, n, d in EVENTS if k == "tube"]
prime_evs = [(t, n, d) for t, k, n, d in EVENTS if k == "prime"]


def V(a, b):
    v = sum(p.dispensed(a, b) for p in pumps.values())
    if push_pump is not None:
        v += push_pump.dispensed(a, b)
    return v


_V_disp = V  # 펌프 변위 기반(원본)
if COMPLIANCE_VC > 0:
    # 1차 지연 컴플라이언스 모델: dVs/dt = (목표−Vs)/τ, 목표=Vc(흐름)·0(정지)
    # outlet 전선 O(t) = 변위 D(t) − 저장 Vs(t)
    _t_end = max(t for t, k, n, d in EVENTS) + 12.0
    _dtg = 0.05
    _N = int(_t_end / _dtg) + 2
    _O = [0.0] * _N
    _vs = 0.0
    _D_prev = 0.0
    for _i in range(1, _N):
        _tt = _i * _dtg
        _Dn = _V_disp(0.0, _tt)
        _flowing = (_Dn - _D_prev) / _dtg > 0.005
        _target = COMPLIANCE_VC if _flowing else 0.0
        _vs += (_target - _vs) * (_dtg / COMPLIANCE_TAU)
        _O[_i] = _Dn - _vs
        _D_prev = _Dn

    def _O_at(t):
        _i = max(0, min(_N - 1, int(t / _dtg)))
        return _O[_i]

    def V(a, b):  # noqa: F811 — outlet 기준으로 재정의
        return _O_at(b) - _O_at(a)

    _info_stored = COMPLIANCE_VC


def flow_at(t, dt=0.4):
    """t 시점 순간 유속 (mL/min) — dt 창의 부피로 근사"""
    return V(t - dt / 2, t + dt / 2) / dt * 60.0


n_tubes = math.ceil(TARGET / TUBE)
n_steps = len(plan)
# 이벤트를 step 단위로 분할: Outlet→2 (COLLECT) 발생 횟수 기준
collects = [t for t, d in valve_evs if d == 2]
wastes_all = [t for t, d in valve_evs if d == 1]
check("step수만큼 COLLECT", len(collects) == n_steps, f"({len(collects)})")

# 펌핑 적분 기준점: step별 injection dosing의 첫 pump_start
# (refill 이벤트 직후 두 펌프가 연속 start하는 첫 시각)
refills = [t for t, k, n, d in EVENTS if k == "refill"]

for si in range(n_steps):
    t_collect = collects[si]
    # 이 step의 injection 시작: collect보다 앞이며 가장 가까운 'A' start 중
    # 시약 refill 직후의 start (injection) — collect-30s 이내 첫 start
    inj_candidates = [t for t, n, d in pump_starts if t < t_collect]
    # injection start = collect 이전 start들 중, t_head(=R/F*60) 근방 과거
    t_head_sec = R / F * 60.0
    inj0 = None
    for t in sorted(inj_candidates):
        if t_collect - t <= t_head_sec + 30:  # pause 여유
            inj0 = t
            break
    check(f"S{si+1}.injection시작 식별", inj0 is not None)
    if inj0 is None:
        continue
    SRC_EFF, R_EFF_SI = step_expect(si)

    v_head = V(inj0, t_collect)
    check(f"S{si+1}.HEAD 정합 V≈R_eff", abs(v_head - R_EFF_SI) < 0.06,
          f"(V={v_head:.3f} vs R_eff={R_EFF_SI:.3f})")

    # 이 step의 well 이동들 (collect 시각 이후 wash까지)
    step_tubes = [(t, n) for t, n in tube_evs if t_collect - 1 <= t]
    step_tubes = step_tubes[:n_tubes + 1]  # collect tubes + wash
    for i in range(1, n_tubes):
        if i < len(step_tubes):
            tt, tn = step_tubes[i]
            v_t = V(inj0, tt)
            check(f"S{si+1}.tube{i + 1}경계 V≈Reff+{i}×tube",
                  abs(v_t - (R_EFF_SI + i * TUBE)) < 0.06,
                  f"(V={v_t:.3f} vs {R_EFF_SI + i * TUBE:.3f}, well={tn})")
    # wash well (HPLC 경로는 wash tube 생략)
    if SCEN != "deadvol_hplc" and len(step_tubes) >= n_tubes + 1:
        tw, wn = step_tubes[n_tubes]
        v_w = V(inj0, tw)
        check(f"S{si+1}.wash경계 V≈Reff+target",
              abs(v_w - (R_EFF_SI + TARGET)) < 0.06, f"(V={v_w:.3f}, well={wn})")

    # WASTE: collect 이후 첫 pos=1
    t_waste = next((t for t in wastes_all if t > t_collect), None)
    check(f"S{si+1}.WASTE 존재", t_waste is not None)
    if t_waste:
        v_e = V(inj0, t_waste)
        if SCEN == "deadvol_hplc":
            expect_e = R_EFF_SI + TARGET + 0.1 * R   # HPLC: collect창이 0.1R 여유 포함, flush 없음
        else:
            expect_e = R_EFF_SI + TARGET + COLL
        check(f"S{si+1}.WASTE 정합 V≈R+target+flush",
              abs(v_e - expect_e) < 0.07, f"(V={v_e:.3f} vs {expect_e:.3f})")
        v_collected = V(t_collect, t_waste)
        _expect_cw = (TARGET + 0.1 * R) if SCEN == "deadvol_hplc" else (TARGET + COLL)
        check(f"S{si+1}.수집창 부피≈기대",
              abs(v_collected - _expect_cw) < 0.07,
              f"(collected={v_collected:.3f} vs {_expect_cw:.3f})")

    # ── push 구동 분취 타이밍: 튜브당 부피 + 이동 시점 흐름 활성 ──
    # 분취의 본질 검증: 컬렉터가 '이동한 순간들 사이'에 실제로 펌핑된
    # 부피가 분획부피(tube_vol)와 일치해야 각 튜브에 올바른 양이 담김.
    if t_waste and len(step_tubes) >= 2:
        seq_pts = [t for t, n in step_tubes]
        # collect 창 = target (+ HPLC면 0.1R 여유) → 이동 수 = ceil(창/tube)
        collect_total = TARGET + (0.1 * R if SCEN == "deadvol_hplc" else 0.0)
        n_moves = min(math.ceil(collect_total / TUBE), len(seq_pts))
        for i in range(n_moves - 1):
            v_tube = V(seq_pts[i], seq_pts[i + 1])
            check(f"S{si+1}.튜브{i+1} 담긴부피≈{TUBE:.2f}",
                  abs(v_tube - TUBE) < 0.05, f"(={v_tube:.3f})")
        # 마지막 collect 튜브: 다음 이벤트(wash 이동 또는 WASTE)까지 잔여분
        t_next = seq_pts[n_moves] if n_moves < len(seq_pts) else t_waste
        v_last = V(seq_pts[n_moves - 1], t_next)
        expect_last = collect_total - (n_moves - 1) * TUBE
        check(f"S{si+1}.마지막튜브 담긴부피≈{expect_last:.2f}",
              abs(v_last - expect_last) < 0.06, f"(={v_last:.3f})")
        # 모든 이동 시점에 흐름 활성 (펌프가 밀고 있는 동안 이동해야 함)
        flows_at_moves = [flow_at(t) for t, n in step_tubes]
        check(f"S{si+1}.이동시점 흐름활성",
              all(f > 0.3 * F for f in flows_at_moves),
              f"(min={min(flows_at_moves):.2f} vs F={F})")

    # step별 prefill 충전량 (라인 상태 추적 검증)
    if SCEN == "deadvol_repeat":
        t_lo = collects[si - 1] if si > 0 else -1.0
        for pn, fl in FLOWS.items():
            fills_si = [d for t, n, d in [(t, n, d) for t, k2, n, d in EVENTS if k2 == "refill"]
                        if n == pn and t_lo < t < inj0]
            last_fill = fills_si[-1] if fills_si else None
            expect_fill = fl / F * TARGET + SRC_EFF[pn]
            check(f"S{si+1}.{pn} 충전={expect_fill:.3f}",
                  last_fill is not None and abs(last_fill - expect_fill) < 0.01,
                  f"(={last_fill})")

    # ── 시약 전선 정렬 (스태거 검증): 채널별 시약이 시린지를 떠나는
    # 시작/종료 시각의 스프레드가 작아야 당량 왜곡 구간이 없음
    if SCEN.startswith("deadvol") and len(FLOWS) >= 2:
        rs, re_ = [], []
        for pn, fl in FLOWS.items():
            segs = [g for g in pumps[pn].segments if g[0] >= inj0 - 0.5]
            if not segs:
                continue
            g = segs[0]
            t0g, t1g, rate, avail = g
            rs.append(t0g + (SRC_EFF[pn] / rate) * 60.0 if rate > 0 else t0g)
            re_.append(min(t1g if t1g else 1e9, t0g + (avail / rate) * 60.0 if rate > 0 else 1e9))
        if len(rs) >= 2:
            check(f"S{si+1}.시약전선 시작정렬", max(rs) - min(rs) < 1.2,
                  f"(spread={max(rs) - min(rs):.2f}s)")
            check(f"S{si+1}.시약전선 종료정렬", max(re_) - min(re_) < 1.2,
                  f"(spread={max(re_) - min(re_):.2f}s)")

    # 주입 부피 보존 (펌프별): injection dosing 구간 = inj0 ~ inj0+inject_sec(+pause)
    purge_sec = max([(SRC_EFF[k] / f) * 60.0 for k, f in FLOWS.items() if f > 0] or [0.0])
    inject_sec = TARGET / F * 60.0 + purge_sec
    pause_extra = 5.0 if SCEN == "pause" else 0.0
    for pn, fl in FLOWS.items():
        vi = pumps[pn].dispensed(inj0 - 0.1, inj0 + inject_sec + pause_extra + 1.5)
        expect_vi = fl / F * TARGET + SRC_EFF[pn]   # 시약 + 유효 보정분 전부 토출
        check(f"S{si+1}.{pn} 주입량≈{expect_vi:.3f}",
              abs(vi - expect_vi) < 0.035, f"(={vi:.3f})")

# 순서 불변식
order_ok = all(collects[i] < (next((t for t in wastes_all if t > collects[i]), 1e9))
               for i in range(len(collects)))
check("순서 COLLECT<WASTE", order_ok)

if SCEN.startswith("deadvol"):
    # prefill 충전 = inject + src 보정 (펌프별 첫 refill 기록)
    refill_evs = [(t, n, d) for t, k, n, d in EVENTS if k == "refill"]
    # injection 시작 = 시린지 dosing 첫 start — PUSH(HPLC)는 제외
    # (2026-08-14: 스텝1 다운스트림 프라임이 시약 충전보다 이른 PUSH start 를 만듦)
    t_inj0 = min(t for t, n, d in pump_starts if n != "PUSH")
    for pn, fl in FLOWS.items():
        # injection 직전의 마지막 refill = 시약 충전 (initial wash refill 제외)
        fills = [d for t, n, d in refill_evs if n == pn and t < t_inj0]
        last = fills[-1] if fills else None
        expect_fill = fl / F * TARGET + SRC[pn]
        check(f"deadvol.{pn} 충전={expect_fill:.3f}",
              last is not None and abs(last - expect_fill) < 0.01, f"(={last})")

if SCEN == "twostep":
    # 튜브 연속성: step2 첫 tube == 1 + n_tubes + 1(wash) + ... = n+2
    s2_first = next((n for t, n in tube_evs if t > collects[1] - 1), None)
    check("twostep.튜브연속", s2_first == 1 + n_tubes + 1, f"(s2 first={s2_first})")

if SCEN in ("unbalanced", "extreme"):
    # 빠른 펌프(A)가 push 중 고갈되면 안 됨 (유속 비례 충전 검증)
    pA = pumps["A"]
    seg = pA.segments[-1]
    t0s, t1s, rate, avail = seg
    exhaust = t0s + avail / rate * 60.0 if rate > 0 else 1e9
    starved = t1s is not None and exhaust < t1s - 0.5
    check(f"{SCEN}.push중 A 고갈없음", not starved,
          f"(avail={avail:.3f}mL exhaust@{exhaust:.1f}s end@{t1s:.1f}s)")

print(f"\n[{SCEN}] {'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
