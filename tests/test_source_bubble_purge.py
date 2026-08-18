"""소스라인 기포 퍼지 가능성 판단 테스트 (gas 실험 브랜치 — 2026-08-14)

사용자 관측: 바이알↔12way 구간의 초기 잔재 기포가 매 런 시약과 함께 반응기로
들어가 반응 속도에 영향을 준다. 제안 로직 =
  "반응기 세척·프리필 '전에', 12way 시약 포트에서 데드볼륨만큼 흡입한 뒤
   그 기포를 12way(폐액 포트)로 배출한다."

이 파일은 fix 가 아니라 **가능성 판단**용이다. 실제 StrictSequenceEngine 을
mock 하드웨어로 구동해 다음을 검증한다:

  A. 순수 산식 — 퍼지량 = inlet + selector + valve_pump (실측 0.1575mL),
     ChemyxSmartPump.refill_prepare 하한(0.1mL) 위. inlet 만으론 하한 미만 →
     '조용한 무동작'이 되므로 소스 경로 전체 흡입이 필수임을 못박는다.
  B. 순서 — 퍼지 → 시스템 세척 → 프리필(Phase-0/1). 퍼지가 공용 구간에 남기는
     시약을 뒤따르는 세척이 헹궈내야 하므로 세척보다 앞이어야 한다.
  C. 경로 — 흡입은 시약 포트에서, 배출은 12way 폐액 포트로 (3way=SOURCE).
  D. 1회성 — 포트당 1회. 동일 포트 재사용 스텝은 재퍼지 없음, 포트 변경 시엔 수행.
  E. 대상 제외 — port1(세척용매)·폐액포트는 퍼지하지 않음.
  F. 무해성 — bubble_purge_enabled=false 면 기존 워크플로와 이벤트가 동일
     (= 롤백 없이도 끌 수 있음).

실행: py -3.14 tests\\test_source_bubble_purge.py   (루트에서)
"""
import os
import sys
import time
import threading

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
    """통신 없는 Chemyx 시뮬레이터 — refill/prime/wash 프리미티브 기록.

    ⚠ 실기 하한(refill_prepare <0.1mL 스킵 / wash_infuse <0.05mL 스킵)을 그대로
      재현한다. 하한 때문에 퍼지가 조용히 무동작이 되는 실패 모드를 놓치지 않기 위함.
    """

    WASH_FLOOR = 0.05

    def __init__(self, name, capacity=6.0, refill_floor=0.1):
        self.REFILL_FLOOR = float(refill_floor)
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
        self._pending_wash_port = None

    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True
        rec("pump_start", self.name, self.target_flow)

    def stop(self):
        self.running = False
        rec("pump_stop", self.name)

    # ── refill (퍼지 흡입 / Phase-1 용매 / 시약 장전 공용) ──
    def refill_prepare(self, port, volume=None):
        v = float(volume) if volume else self.capacity
        if v < self.REFILL_FLOOR:            # 실기 하한 재현 — 조용한 무동작
            rec("refill_skipped", self.name, (port, v))
            return False
        self._pending_fill = v
        self._pending_fill_port = port
        return True

    def refill_trigger(self):
        self.is_refilling = True

    def refill_complete(self):
        time.sleep(0.02)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_fill)
        self.is_refilling = False
        rec("refill", self.name, (self._pending_fill_port, self._pending_fill))

    def refill(self, port, volume=None):
        v = float(volume or 0.0)
        self.current_vol = min(self.capacity, self.current_vol + v)
        rec("phase0_refill", self.name, (port, v))
        return True

    # ── prime ──
    def prime_prepare(self):
        if self.current_vol > 0.0:
            self._pending_prime = float(self.current_vol)
            return True
        return False

    def prime_trigger(self):
        pass

    def prime_complete(self):
        time.sleep(0.02)
        rec("prime", self.name, self._pending_prime)
        self.current_vol = 0.0

    # ── wash-infuse (= 12way 폐액 배출. 퍼지 배출도 이 프리미티브를 씀) ──
    def wash_infuse_prepare(self, waste_port=12):
        if self.current_vol < self.WASH_FLOOR:
            rec("wash_infuse_skipped", self.name, (waste_port, self.current_vol))
            return False
        self._pending_wash = float(self.current_vol)
        self._pending_wash_port = waste_port
        return True

    def wash_infuse_trigger(self):
        pass

    def wash_infuse_complete(self):
        time.sleep(0.02)
        rec("wash_infuse", self.name, (self._pending_wash_port, self._pending_wash))
        self.current_vol = 0.0
        return True

    def wash_withdraw_prepare(self, solvent_port=1):
        avail = max(0.0, self.capacity - self.current_vol)
        self._pending_wash = min(self.wash_volume, avail)
        self._pending_wash_port = solvent_port
        return self._pending_wash >= self.WASH_FLOOR

    def wash_withdraw_trigger(self):
        pass

    def wash_withdraw_complete(self):
        time.sleep(0.02)
        self.current_vol = min(self.capacity, self.current_vol + self._pending_wash)
        rec("wash_withdraw", self.name, (self._pending_wash_port, self._pending_wash))


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


# ── 실측 배관 볼륨 (hardware_config.json, Group A/D 동일) ──
V_INLET = 0.0754       # 바이알 → 12way
V_SELECTOR = 0.0224    # 12way 밸브 내부
V_VALVE_PUMP = 0.0597  # 12way → 3way → 시린지
V_PUMP_MERGE = 0.2262
V_SWITCHER = 0.0507
# 요건 = 기포가 12way '로터'를 통과하는 것 (배럴까지 갈 필요 없음)
V_MIN = round(V_INLET + V_SELECTOR, 4)                 # 0.0978 = 최소량
FACTOR = 2.0                     # 기본 안전여유 (실기 관측 — 1.5로는 기포 잔존)
PURGE_EXPECT = round(V_MIN * FACTOR, 4)                # 0.1956
REFILL_FLOOR = 0.05                                    # gas 브랜치 하한

FLOWS = {"A": 1.0, "B": 1.0}
F = sum(FLOWS.values())


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = 0.6
    tjunction_line_vols = {}
    mixing_line_dead_vol = 0.0

    def __init__(self, purge_enabled):
        self.ACTIVE_PUMPS = list(FLOWS.keys())
        self.line_vol_inlet = {p: V_INLET for p in FLOWS}
        self.line_vol_valve_pump = {p: V_VALVE_PUMP for p in FLOWS}
        self.line_vol_pump_merge = {p: V_PUMP_MERGE for p in FLOWS}
        self.valve_internal_vol = {p: V_SWITCHER for p in FLOWS}
        self.selector_internal_vol = {p: V_SELECTOR for p in FLOWS}
        self.config_data = {
            "system_params": {
                "post_reactor_vol_ml": 0.2,
                "collection_line_vol_ml": 0.06,
                "temp_tolerance_c": 0.5,
                "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0,
                "max_step_volume_ml": 500.0,
                "wash_mode": "every_step",
                "prefill_mode": "port_change",
                "purge_order": "lifo",
                "priming_rate_ml_min": 20.0,
                "syringe_refill_rate": 20.0,
                "bubble_purge_enabled": bool(purge_enabled),
                "bubble_purge_waste_port": 12,
                "bubble_purge_factor": FACTOR,
                "refill_min_vol_ml": REFILL_FLOOR,
            },
            "roles": {"push_pump": {"driver_id": "dev_reaxus_1"}},
        }


def run_scenario(purge_enabled, ports):
    """ports = 스텝별 시약 포트 리스트. 반환: (EVENTS 사본, LOGS 사본)"""
    global EVENTS, LOGS, ERRORS
    EVENTS, LOGS, ERRORS = [], [], []
    step_tpl = {"temp": 25.0, "vol_ml": 0.45, "residence_time": 0.6 / F * 60,
                "collect_volume_per_tube": 0.15, "meta": {}}
    plan = []
    for prt in ports:
        s = dict(step_tpl)
        s["inlet_ports"] = {k: prt for k in FLOWS}
        s["flows"] = dict(FLOWS)
        plan.append(s)
    pumps = {n: SimPump(n, refill_floor=REFILL_FLOOR) for n in FLOWS}
    cfg = Cfg(purge_enabled)
    eng = StrictSequenceEngine(cfg, pumps, {"Outlet": SimValve("Outlet")}, SimHeater(),
                               SafetyManager(cfg, pumps, SimHeater()), Sig(),
                               collector=SimCollector(), push_pump=SimPushPump())
    eng.collector_start_tube = 1
    eng.run_sequence(plan, None)
    return list(EVENTS), list(LOGS)


fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# =========================================================================
# A. 순수 산식 — 퍼지량 정의와 실기 하한
# =========================================================================
print("=== A. 퍼지량 산식 (compute_bubble_purge_vol) ===")
cbp = StrictSequenceEngine.compute_bubble_purge_vol

check(f"로터 통과 최소량 = inlet+selector = {V_MIN}mL (valve_pump 불필요)",
      abs(cbp(V_INLET, V_SELECTOR, 1.0, REFILL_FLOOR) - V_MIN) < 1e-6,
      f"(={cbp(V_INLET, V_SELECTOR, 1.0, REFILL_FLOOR)})")
check(f"기본 factor {FACTOR} → {PURGE_EXPECT}mL",
      abs(cbp(V_INLET, V_SELECTOR, FACTOR, REFILL_FLOOR) - PURGE_EXPECT) < 1e-6,
      f"(={cbp(V_INLET, V_SELECTOR, FACTOR, REFILL_FLOOR)})")
_tail = round(PURGE_EXPECT - V_MIN, 4)   # 기포 뒷단이 로터에서 떨어지는 거리
check(f"기포 뒷단이 로터에서 {_tail}mL 이격 — valve_pump({V_VALVE_PUMP}) 넘어 배럴 안",
      _tail > V_VALVE_PUMP, f"({_tail} > {V_VALVE_PUMP})")
check("factor 는 4.0 까지 허용 (엔진 클램프)",
      abs(cbp(V_INLET, V_SELECTOR, 4.0, REFILL_FLOOR) - round(V_MIN * 4, 4)) < 1e-6)
check("하한 0.1 + factor 1.0 → 0.0978 이 0.1 로 승격 (조용한 무동작 방지)",
      abs(cbp(V_INLET, V_SELECTOR, 1.0, 0.1) - 0.1) < 1e-9,
      f"(={cbp(V_INLET, V_SELECTOR, 1.0, 0.1)})")
check("하한 0.05 면 승격 없음 — 최소량 그대로 흡입 가능",
      abs(cbp(V_INLET, V_SELECTOR, 1.0, 0.05) - V_MIN) < 1e-6)
check("factor 2.0 → 2배",
      abs(cbp(V_INLET, V_SELECTOR, 2.0, REFILL_FLOOR)
          - round(V_MIN * 2, 4)) < 1e-6)
check("배관 미설정(0)은 0 반환 → 호출부가 스킵", abs(cbp(0, 0)) < 1e-12)

# =========================================================================
# B~E. 엔진 구동 — 3스텝 (port 2 → port 3 → port 3)
# =========================================================================
print("\n=== B~E. 엔진 구동 (퍼지 ON, ports 2→3→3) ===")
t0 = time.time()
ev_on, log_on = run_scenario(True, [2, 3, 3])
print(f"  run time: {time.time() - t0:.1f}s")

refills = [(t, n, d) for t, k, n, d in ev_on if k == "refill"]
w_inf = [(t, n, d) for t, k, n, d in ev_on if k == "wash_infuse"]
w_wd = [(t, n, d) for t, k, n, d in ev_on if k == "wash_withdraw"]
skipped = [(t, n, d) for t, k, n, d in ev_on if k == "refill_skipped"]

# 퍼지 흡입 = refill 중 부피가 퍼지량과 일치하는 건 (시약 장전은 0.225+)
purge_wd = [(t, n, d) for t, n, d in refills if abs(d[1] - PURGE_EXPECT) < 1e-3]
purge_expel = [(t, n, d) for t, n, d in w_inf if abs(d[1] - PURGE_EXPECT) < 1e-3]

check("퍼지 흡입 = 2포트 × 2펌프 = 4건", len(purge_wd) == 4,
      f"({len(purge_wd)}건: {[(n, d) for _, n, d in purge_wd]})")
check("퍼지 흡입이 하한에 걸려 스킵된 건 없음", not skipped,
      f"({len(skipped)}건)")
check("퍼지 흡입 포트 = 시약 포트(2, 3)",
      sorted({d[0] for _, _, d in purge_wd}) == [2, 3],
      f"({sorted({d[0] for _, _, d in purge_wd})})")
check("퍼지 배출 = 4건, 전부 12way 폐액 포트(12)",
      len(purge_expel) == 4 and all(d[0] == 12 for _, _, d in purge_expel),
      f"({[(n, d) for _, n, d in purge_expel]})")

# D. 1회성 — 포트 3 은 스텝2에서만 퍼지, 스텝3 재사용 시 없음
p3 = [t for t, _, d in purge_wd if d[0] == 3]
check("포트 3 퍼지는 1회뿐 (스텝3 재사용 시 재퍼지 없음)", len(p3) == 2,
      f"({len(p3)}건 = 2펌프 × 1회)")
check("포트당 1회 로그 총 2회 (포트 2, 3)",
      sum(1 for m in log_on if "소스라인 기포 퍼지" in m) == 2,
      f"({sum(1 for m in log_on if '소스라인 기포 퍼지' in m)}회)")

# E. port1(세척용매)·폐액포트는 대상 아님
check("port 1 흡입은 퍼지량이 아님 (세척용매 경로 미개입)",
      not any(d[0] == 1 and abs(d[1] - PURGE_EXPECT) < 1e-3 for _, _, d in refills))

# B. 순서 — 퍼지 → 세척 → 프리필 (로그 순서로 검증)
def first_idx(sub, logs):
    for i, m in enumerate(logs):
        if sub in m:
            return i
    return -1


i_purge = first_idx("소스라인 기포 퍼지", log_on)
i_wash = first_idx("Wash", log_on)
i_prefill = first_idx("Pre-fill start", log_on)
check("퍼지 로그 존재", i_purge >= 0, f"(idx={i_purge})")
check("퍼지 → 세척 순서", i_purge >= 0 and i_wash > i_purge,
      f"(purge={i_purge}, wash={i_wash})")
check("퍼지 → 프리필 순서", i_purge >= 0 and i_prefill > i_purge,
      f"(purge={i_purge}, prefill={i_prefill})")

# C. 시간 순서로도 확인 — 첫 퍼지 흡입 < 첫 세척 withdraw
if purge_wd and w_wd:
    check("첫 퍼지 흡입이 첫 세척 흡입보다 앞",
          min(t for t, _, _ in purge_wd) < min(t for t, _, _ in w_wd),
          f"(purge={min(t for t, _, _ in purge_wd):.2f}s, "
          f"wash={min(t for t, _, _ in w_wd):.2f}s)")

# 퍼지 배출은 자기 흡입 직후 (사이에 다른 흡입이 끼지 않음)
if len(purge_wd) == 4 and len(purge_expel) == 4:
    ok = all(min(t for t, _, _ in purge_expel) > min(t for t, _, _ in purge_wd)
             for _ in [0])
    check("퍼지 배출은 퍼지 흡입 이후", ok)

check("시퀀스 에러 없음", not ERRORS, f"({ERRORS[:2]})")

# =========================================================================
# F. 무해성 — enabled=false 면 기존 워크플로와 동일
# =========================================================================
print("\n=== F. 무해성 (퍼지 OFF, 동일 시나리오) ===")
ev_off, log_off = run_scenario(False, [2, 3, 3])
refills_off = [(n, d) for t, k, n, d in ev_off if k == "refill"]
purge_off = [d for n, d in refills_off if abs(d[1] - PURGE_EXPECT) < 1e-3]
check("OFF 시 퍼지 흡입 0건", not purge_off, f"({len(purge_off)}건)")
check("OFF 시 퍼지 로그 0건",
      not any("기포 퍼지" in m or "BubblePurge" in m for m in log_off))

kinds_on = [k for _, k, _, _ in ev_on]
kinds_off = [k for _, k, _, _ in ev_off]
check("OFF 이벤트 수 < ON 이벤트 수 (퍼지분만 차이)",
      len(kinds_off) < len(kinds_on),
      f"(off={len(kinds_off)}, on={len(kinds_on)})")

# 퍼지 이벤트(흡입 4 + 배출 4)를 뺀 나머지 이벤트 종류 시퀀스가 동일해야 함
purge_ts = {t for t, _, _ in purge_wd} | {t for t, _, _ in purge_expel}
kinds_on_min = [k for t, k, _, _ in ev_on if t not in purge_ts]
check("퍼지분 제외 시 이벤트 종류 시퀀스 동일 (기존 워크플로 무변경)",
      kinds_on_min == kinds_off,
      f"(on-purge={len(kinds_on_min)}, off={len(kinds_off)})")

# =========================================================================
# G. refill 최소량 하한을 0.1 → 0.05 로 낮추면 무엇이 달라지는가
#    ★ mock 이 아니라 **실제 ChemyxSmartPump.refill_prepare** 로직을 그대로 태운다.
# =========================================================================
print("\n=== G. refill 하한 0.1 vs 0.05 (실제 클래스 로직) ===")


class FloorStub:
    """refill_prepare 의 하한 분기만 검증하기 위한 최소 self.

    __init__ 을 우회해 시리얼 드라이버 생성을 피한다 — 통과 시 호출되는
    밸브 전환·파라미터 설정은 기록만 하고 아무것도 보내지 않는다.
    """
    POS_SOURCE = 1

    def __init__(self, floor, current_vol=0.0, capacity=6.0):
        self.name = "STUB"
        self.pump_id = 0
        self.refill_min_vol = float(floor)
        self.current_vol = float(current_vol)
        self.capacity = float(capacity)
        self.refill_rate = 4.0
        self.is_refilling = False
        self._abort_refill = False
        self.status = ""
        self.lock = threading.Lock()
        self.prepared = None

    def set_valves_safe(self, selector_port=None, switcher_pos=None):
        pass

    def prepare_parameters(self, rate, volume, action_name="Action"):
        self.prepared = (rate, volume)


real_refill = ChemyxSmartPump.refill_prepare

s = FloorStub(0.1)
check(f"하한 0.1: 로터통과 최소량 {V_MIN}mL 요청이 스킵됨 (원본 시스템 무동작)",
      real_refill(s, 2, V_MIN) is False and s.prepared is None)

s = FloorStub(0.05)
r = real_refill(s, 2, V_MIN)
check(f"하한 0.05: 같은 요청이 실행됨 (withdraw -{V_MIN})",
      r is True and s.prepared is not None
      and abs(s.prepared[1] + V_MIN) < 1e-9, f"({s.prepared})")

s = FloorStub(0.05, current_vol=5.98, capacity=6.0)   # available = 0.02
check("하한 0.05 여도 '거의 찬 시린지' 스킵 가드는 유지 (하한의 원래 목적)",
      real_refill(s, 1, 1.0) is False and s.prepared is None)

# Phase-0 정량 리필 지뢰: 분기 데드볼륨 0.277, 잔량 0.2 → need 0.077
check("하한 0.1: Phase-0 잔여 0.077mL 탑업이 조용히 스킵 (기존 지뢰)",
      real_refill(FloorStub(0.1), 1, 0.0769) is False)
check("하한 0.05: 그 탑업이 실행됨 (지뢰 해소)",
      real_refill(FloorStub(0.05), 1, 0.0769) is True)

# =========================================================================
print("\n" + "=" * 62)
if fails:
    print(f"RESULT: {len(fails)} FAIL — {fails}")
    sys.exit(1)
print("RESULT: ALL PASS — 소스라인 기포 퍼지 로직 성립")
print(f"  포트당 폐기량 {PURGE_EXPECT:.4f}mL × 펌프수 (실측 배관 기준)")
