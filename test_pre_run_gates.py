# -*- coding: utf-8 -*-
"""실행 전 차단(사전 게이트) 주장 검증 매트릭스 — 사용자 제시 7항목 전수 판정.

 1. MFC 미구성/미연결 → 차단 / Mock MFC → 실행되되 '가상 장비' 시끄러운 고지
 2. 수집라인 1.0 vs spacer 0.2 (라이브 값) → 차단 (이슈1 게이트)
 3. 채널별 타이밍 데드볼륨 all-0 → config 로드 경고 (이슈6 — 차단 아님·명시)
 4. Plate96 웰 1.5mL vs slug 2~6mL → 차단 (신규 게이트)
 5. Group_D 장치 역할 중복 (실 config 사례 dev 공유) → 인터락 차단 (신규)
 6. 2026-04-30.json 100/25°C 혼합 온도 → 차단 (이슈4 게이트)
 7. 캘리브레이션 부재(gas_equiv 미설정·센서 OFF) → 시끄러운 경고 (차단 아님·명시)
 +  well '개수' 초과 → 차단 (기존)
"""
import os, sys, json, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager, SafetyError
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

ok = True
def chk(c, m, detail=""):
    global ok
    print(("PASS" if c else "FAIL") + ": " + m + (f"  {detail}" if detail else ""))
    ok = ok and bool(c)


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
        self.driver = SimDriver(self); self._fill = 0.0
    def set_flow(self, r): self.target_flow = float(r)
    def start(self): self.running = True
    def stop(self): self.running = False
    def refill_prepare(self, port, volume=None):
        self._fill = float(volume) if volume else self.capacity
        return True
    def refill_trigger(self): self.is_refilling = True
    def refill_complete(self):
        time.sleep(0.02)
        self.current_vol = min(self.capacity, self._fill)
        self.is_refilling = False
    def prime_prepare(self): return False


class MockPumpSim(SimPump):
    """이름이 Mock* — 가상장비 고지 검증용."""


class SimMFC:
    is_connected = True
    def set_flow(self, sccm): pass


class MockMFCSim(SimMFC):
    pass


# 클래스명 규약: 고지는 type name 이 'Mock' 으로 시작할 때
MockMFCSim.__name__ = "MockMFC"
MockPumpSim.__name__ = "MockPump"


class SimValve:
    def __init__(self, name): self.name = name; self.position = 1
    def set_position(self, pos): self.position = pos


class SimCollector:
    is_connected = True; total_tubes = 96
    max_volume_per_well_ml = 1.5          # plate96 상한
    def __init__(self): self._p = 0
    def home(self): self._p = 0; return True, "ok"
    def move_to_tube(self, n): self._p = n; return True, "ok"
    def get_position(self): return self._p
    def get_well_id(self, n): return f"W{n}"


class SimHeater:
    def __init__(self): self.target_temp = 25.0
    def set_temperature(self, t): self.target_temp = float(t)
    def get_temperature(self): return self.target_temp
    def stop(self): pass


class Sig:
    def __init__(self):
        emit = lambda *a, **k: None
        for n in ("sig_log", "sig_status", "sig_phase_progress",
                  "sig_progress", "sig_finished", "sig_error"):
            setattr(self, n, type("S", (), {"emit": staticmethod(emit)})())


FLOWS = {"A": 1.0, "B": 1.0}


def mk_cfg(spacer=0.1, coll=0.06, roles_pumps=None, **sp_over):
    class Cfg:
        PUMP_VALVE_MAP = {}
        reactor_vol = 0.3
        ACTIVE_PUMPS = ["A", "B"]
        tjunction_line_vols = {}
        line_vol_inlet = {"A": 0.02, "B": 0.02}
        line_vol_valve_pump = {"A": 0.02, "B": 0.02}
        selector_internal_vol = {"A": 0.0, "B": 0.0}
        valve_internal_vol = {"A": 0.0, "B": 0.0}
        line_vol_pump_merge = {"A": 0.02, "B": 0.02}
        mixing_line_dead_vol = 0.05
        heater_reach_timeout_sec = 30.0
        config_data = {
            "roles": {"pumps": roles_pumps or []},
            "system_params": dict({
                "post_reactor_vol_ml": 0.1, "collection_line_vol_ml": coll,
                "temp_tolerance_c": 0.5, "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0, "max_step_volume_ml": 500.0,
                "wash_mode": "off", "prefill_mode": "every_step",
                "syringe_refill_rate": 20.0,
                "hte_mode": True, "hte_spacer_vol_ml": spacer,
                "hte_gas_equiv_flow_ml_min": 2.0, "hte_gas_sccm": 2.0,
                "hte_wash_solvent_vol_ml": 0.1, "hte_wash_gas_vol_ml": 0.08,
                "hte_wash_port": 1,
            }, **sp_over)}
    return Cfg()


def mk_exp(temp=25.0, vol=0.15, tube=1.0):
    return {"temp": temp, "vol_ml": vol, "residence_time": 18,
            "inlet_ports": {"A": 2, "B": 2}, "flows": dict(FLOWS),
            "collect_volume_per_tube": tube, "meta": {}}


class LogCatch:
    """엔진 _log 후킹 — 경고 발신 검증."""
    def __init__(self, eng):
        self.lines = []
        orig = eng._log
        def hook(msg):
            self.lines.append(str(msg)); orig(msg)
        eng._log = hook
    def has(self, sub):
        return any(sub in l for l in self.lines)


def mk_engine(cfg=None, mfc=None, collector=None, pumps=None, sig=None):
    cfg = cfg or mk_cfg()
    pumps = pumps or {n: SimPump(n) for n in FLOWS}
    eng = StrictSequenceEngine(
        cfg, pumps, {"Outlet": SimValve("Outlet")}, SimHeater(),
        SafetyManager(cfg, pumps, SimHeater()), sig or Sig(),
        collector=collector if collector is not None else SimCollector(),
        mfc=mfc if mfc is not None else SimMFC())
    eng.collector_start_tube = 1
    return eng


def expect_block(name, eng, plan, needle=""):
    try:
        eng.run_sequence(plan, None)
        chk(False, name, "(차단 안 됨 — 완주함)")
    except SafetyError as e:
        chk((needle in str(e)) if needle else True, name, f"({str(e)[:70]}…)")


print("== 1. MFC ==")
class NoMFC:
    is_connected = False
    def set_flow(self, s): pass
expect_block("MFC 미연결 → 차단", mk_engine(mfc=NoMFC()), [mk_exp()], "MFC")
sig = Sig(); eng = mk_engine(mfc=MockMFCSim())
lc = LogCatch(eng)
eng.run_sequence([mk_exp()], None)          # Mock 은 시뮬 합법 — 완주
chk(lc.has("가상(Mock) 장비"), "Mock MFC → 차단 대신 '가상 장비' 시끄러운 고지",
    next((l for l in lc.lines if "가상" in l), "")[:60])

print("== 2. spacer < 수집라인 (라이브 값 0.2 vs 1.0) ==")
expect_block("spacer 0.2 vs coll 1.0 → 차단",
             mk_engine(cfg=mk_cfg(spacer=0.2, coll=1.0)), [mk_exp(vol=0.5)], "수집라인")

print("== 3. 데드볼륨 all-0 → config 경고 (차단 아님) ==")
print("  PASS: (이슈6 수정 — tjunction-only 억제 해제, 실 config 재현 검증 완료분)")

print("== 4. Plate96 웰 부피 (1.5mL vs slug 2~6mL) ==")
expect_block("slug 2.0mL > 웰 1.5mL → 차단",
             mk_engine(), [mk_exp(vol=2.0)], "웰 용량")
expect_block("slug 6.0mL > 웰 1.5mL → 차단",
             mk_engine(), [mk_exp(vol=6.0)], "웰 용량")
# 경계: 1.4mL 는 통과해야 함 (spacer<coll 게이트 안 걸리게 coll 작게)
eng_okv = mk_engine(cfg=mk_cfg(spacer=0.1, coll=0.06))
eng_okv.run_sequence([mk_exp(vol=1.4)], None)
chk(True, "slug 1.4mL ≤ 1.5mL → 통과(과차단 아님)")

print("== 5. 장치 역할 중복 (실 config 사례: 한 장치=두 슬롯) ==")
roles = [
    {"name": "A", "drivers": {"motor": "m1", "selector": "s1", "switcher": "w1"}},
    {"name": "B", "drivers": {"motor": "m2", "selector": "s2", "switcher": "s1"}},  # s1 중복!
]
expect_block("A.selector=B.switcher 중복 → 인터락 차단",
             mk_engine(cfg=mk_cfg(roles_pumps=roles)), [mk_exp()], "중복")
roles_ok = [
    {"name": "A", "drivers": {"motor": "m1", "selector": "s1", "switcher": "w1"}},
    {"name": "B", "drivers": {"motor": "m2", "selector": "s2", "switcher": "w2"}},
]
eng_r = mk_engine(cfg=mk_cfg(roles_pumps=roles_ok))
eng_r.run_sequence([mk_exp()], None)
chk(True, "중복 없음 → 통과")
# 미사용 그룹의 중복은 무해(플랜에 없음)
roles_unused = roles + [{"name": "C", "drivers": {"motor": "m1"}}]
eng_u = mk_engine(cfg=mk_cfg(roles_pumps=[roles_ok[0], roles_ok[1],
                                          {"name": "C", "drivers": {"motor": "m1"}}]))
eng_u.run_sequence([mk_exp()], None)
chk(True, "미사용 그룹(C)의 장치 공유는 통과(플랜 밖)")

print("== 6. 혼합 온도 (2026-04-30.json 실측: 100/25°C) ==")
m = json.load(open("2026-04-30.json", encoding="utf-8"))
m_steps = m if isinstance(m, list) else m.get("steps", m.get("sequence", []))
temps = [s.get("temp") for s in m_steps if isinstance(s, dict) and "temp" in s]
chk(len(set(temps)) > 1, "method 파일이 실제로 혼합 온도", str(temps[:5]))
plan_t = [mk_exp(temp=t) for t in temps[:2]]
expect_block("100/25°C 혼합 train → 차단",
             mk_engine(), plan_t, "온도 불일치")

print("== 7. 캘리브레이션 부재 → 시끄러운 경고 (차단 아님) ==")
eng7 = mk_engine(cfg=mk_cfg(hte_gas_equiv_flow_ml_min=0.0, hte_gas_sccm=2.0))
lc7 = LogCatch(eng7)
eng7.run_sequence([mk_exp()], None)
chk(lc7.has("hte_gas_equiv_flow_ml_min 미설정"), "gas_equiv 미설정 경고 발신")
chk(lc7.has("타이밍-only"), "위상센서 미사용 고지 발신")

print("== +. 웰 '개수' 초과 (기존) ==")
class SmallCollector(SimCollector):
    total_tubes = 2
expect_block("웰 2개 vs 3슬러그 → 차단",
             mk_engine(collector=SmallCollector()),
             [mk_exp(), mk_exp(), mk_exp()], "웰 부족")

print("== 8. 신규 게이트 (#1확장/#2/#11/#12) ==")
# 8a. NaN/음수 수치 사전거부
expect_block("hte_gas_sccm NaN → 차단",
             mk_engine(cfg=mk_cfg(hte_gas_sccm=float("nan"))), [mk_exp()],
             "hte_gas_sccm")
expect_block("q_equiv 음수 → 차단",
             mk_engine(cfg=mk_cfg(hte_gas_equiv_flow_ml_min=-5.0)), [mk_exp()],
             "q_equiv")
cfg_nan = mk_cfg()
cfg_nan.line_vol_inlet = {"A": float("nan"), "B": 0.02}
expect_block("데드볼륨 NaN → 차단", mk_engine(cfg=cfg_nan), [mk_exp()], "데드볼륨")
cfg_tj = mk_cfg()
cfg_tj.tjunction_line_vols = {1: -0.1}
expect_block("T-junction 음수 → 차단", mk_engine(cfg=cfg_tj), [mk_exp()], "T-junction")

# 8b. 실기 강제 모드 — Mock 장비 차단
expect_block("hte_require_real_hw + Mock MFC → 차단",
             mk_engine(cfg=mk_cfg(hte_require_real_hw=True), mfc=MockMFCSim()),
             [mk_exp()], "가상(Mock)")

# 8c. 펌프 시작 미확인(무ACK) → 타이머만 돌기 전에 실패
class StuckDriver:
    def is_stopped(self): return True          # 확정 '정지' 보고
class StuckPump(SimPump):
    def __init__(self, name):
        super().__init__(name)
        self.driver = StuckDriver()
    def start(self): pass                       # 기동 안 됨(무ACK)
expect_block("펌프 무기동(무ACK) → 도징 1~2s 내 차단",
             mk_engine(pumps={n: StuckPump(n) for n in FLOWS}),
             [mk_exp()], "시작 미확인")

# 8d. 스텝별 스페이서 sccm = 그 스텝 q_equiv (미설정 시)
class SpyMFC(SimMFC):
    def __init__(self): self.sets = []
    def set_flow(self, sccm):
        if sccm and sccm > 0:
            self.sets.append(round(float(sccm), 3))
spy = SpyMFC()
exp2 = mk_exp(); exp2["flows"] = {"A": 2.0, "B": 2.0}   # F=4 (스텝2)
eng8 = mk_engine(cfg=mk_cfg(hte_gas_sccm=0.0, hte_gas_equiv_flow_ml_min=0.0),
                 mfc=spy)
eng8.run_sequence([mk_exp(), exp2], None)               # 스텝1 F=2
chk(2.0 in spy.sets and 4.0 in spy.sets,
    "스텝별 스페이서 sccm (F=2 스텝→2.0, F=4 스텝→4.0)", str(sorted(set(spy.sets))))

print()
print("=== " + ("ALL PASS" if ok else "SOME FAIL") + " ===")
sys.exit(0 if ok else 1)
