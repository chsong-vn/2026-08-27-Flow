# -*- coding: utf-8 -*-
"""_startup_level_reconcile (초음파 레벨센서 startup 잔량 정합) 계약 테스트.

하드웨어/PyQt 없이 엔진 루틴을 직접 검증 (StrictSequenceEngine.__new__ 로
__init__ 우회 → 루틴이 참조하는 속성만 수동 주입).

@codesyncer-decision (적대검증 반영): 가짜 펌프의 wash_infuse 가 '플런저를 빈
  위치로' = 링크된 mock 센서를 0 으로 만든다(물리 충실 모델). 이렇게 해야
  '배출 후 empty 로 끝나지 않는' 원시연산(예: wash_cycle=용매 재흡입→full)을
  쓰면 테스트가 통과하지 못한다(과거 no-op mock 이 치명 버그를 가렸던 재발 방지).

실행:  py -3.14 test_level_reconcile.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
import types

from engine.strict_engine import StrictSequenceEngine, _is_smart_pump
from engine.safety_manager import SafetyError
from hardware.sensors.ultrasonic_level import MockUltrasonicLevelSensor


# ── 테스트 더블 ───────────────────────────────────────────────
class FakeSmartPump:
    """_is_smart_pump 계약(15속성) 충족 + wash_infuse/prime 물리 모델(빈 위치 종료)."""
    _API = ("refill_prepare", "refill_trigger", "refill_complete",
            "wash_withdraw_prepare", "wash_infuse_prepare",
            "set_flow", "start", "stop")

    def __init__(self, sensor=None, ch=None, empties=True, capacity=6.0,
                 wash_volume=3.0, infuse_ok=True, abort_refill=False):
        self.current_vol = 0.0
        self.capacity = capacity
        self.is_refilling = False
        self.driver = object()
        self.wash_calls = 0
        self.wash_volume = wash_volume     # 엔진이 sizing 하며 임시 변경/복원
        self.wash_speed = 600.0            # 모니터드 창 단축 (t_need 0.2s — 테스트 속도)
        self.prime_rate = 8.0              # 엔진이 rate 오버라이드 시 임시 변경/복원
        self.prime_calls = 0
        self.last_prime_vol = None         # prime 시점의 current_vol (sizing 검증)
        self.last_prime_rate = None        # prime 시점의 prime_rate (오버라이드 검증)
        self._sensor = sensor              # 링크된 mock 센서
        self._ch = ch                      # 이 펌프의 센서 채널명
        self._empties = empties            # False = 클로그/무토출(밀어도 안 빠짐) 모델
        self._infuse_ok = infuse_ok        # False = HW 실패(무토출) 모델
        self._abort_refill = abort_refill  # 진짜 abort 근거(HW실패와 구분 검증용)
        self.sized_wv = None               # begin 시점의 wash_volume (sizing 검증)
        for m in self._API:
            setattr(self, m, (lambda *a, **k: None))

    # @codesyncer(2026-07-29): 엔진이 '밀면서 측정'(complete 미경유)으로 바뀜 —
    #   물리 모델을 begin/trigger 시점에 적용 (모니터 루프가 센서로 판정).
    def wash_infuse_begin(self, waste_port=12):
        self.sized_wv = self.wash_volume   # 엔진이 측정잔량 기반으로 sizing 한 값
        self.wash_calls += 1
        # 정상 + 비클로그일 때만 토출 → 센서 0 (실패/클로그 = 센서 안 줄어듦)
        if self._infuse_ok and self._empties and self._sensor is not None:
            self._sensor.set_value(self._ch, 0.0)
        return True

    def wash_infuse_complete(self):
        return self._infuse_ok             # 레거시 호환 (엔진 미사용)

    # prime = 리액터 방향 토출 (discharge="reactor" 경로)
    def prime_prepare(self):
        self.last_prime_vol = float(self.current_vol)
        self.last_prime_rate = float(self.prime_rate)
        return True

    def prime_trigger(self):
        self.prime_calls += 1
        if self._empties and self._infuse_ok and self._sensor is not None:
            self._sensor.set_value(self._ch, 0.0)

    def prime_complete(self):
        pass                               # 레거시 호환 (엔진 미사용)


def _sensor(pumps):
    s = MockUltrasonicLevelSensor(pumps=pumps)
    s.connect()
    return s


def _make_engine(pumps, level_cfg, routing, sensor, max_iter=3, plan=None):
    eng = StrictSequenceEngine.__new__(StrictSequenceEngine)
    eng.pumps = pumps
    eng.level_sensor = sensor
    eng.level_purge_max_iter = max_iter
    eng.abort_flag = False
    eng._plan = plan
    eng.cfg = types.SimpleNamespace(PUMP_LEVEL_CFG=level_cfg, PUMP_ROUTING=routing)
    emitted = []
    eng.signals = types.SimpleNamespace(
        sig_level_data=types.SimpleNamespace(emit=lambda d: emitted.append(d)))
    eng._emitted = emitted
    eng._logs = []
    eng._log = lambda m, **k: eng._logs.append(m)
    return eng


def _cfg(policy, gate=500.0, dev="dev_lvl"):
    return {"device_id": dev, "policy": policy, "gate_ul": gate,
            "slope": 1.0, "intercept": 0.0, "samples": 20}


# ── 시나리오 ─────────────────────────────────────────────────
def test_reagent_leftover_purge_verify_reset():
    """잔량>gate → 리액터 방향(prime) 배출 → 재측정 empty → current_vol=0 (핵심 경로).
    2026-07-29 개정: ①배출=리액터 경유(12-way 폐액 배관 의존 제거, 사용자 지시)
    ②토출량 sizing = '센서 실측치 그대로'(마진 없음 — 하드스톱 과주행 시 펌프
    에러→시스템 정지) ③prime_rate 복원."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True, wash_volume=9.0)
    pump.current_vol = 4.2   # 오염된 카운터
    s.set_value("Group A", 2000.0)   # 2 mL 잔량
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()
    assert pump.prime_calls == 1, f"리액터 배출 1회 기대, 실제 {pump.prime_calls}"
    assert pump.wash_calls == 0, "12-way 폐액 경유 금지 (리액터 방향)"
    assert pump.current_vol == 0.0, f"검증 empty 후 0 기대, 실제 {pump.current_vol}"
    assert abs(pump.last_prime_vol - 2.0) < 1e-9, \
        f"sizing=실측 2mL 그대로(마진 금지) 기대, 실제 {pump.last_prime_vol}"
    assert abs(pump.prime_rate - 8.0) < 1e-9, f"prime_rate 복원(8.0) 기대, 실제 {pump.prime_rate}"
    assert eng._emitted, "sig_level_data emit 기대"


def test_reagent_already_empty_no_purge():
    """잔량 ≤ gate → 배출 없이 current_vol=0."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 100.0)   # gate(500) 미만
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()
    assert pump.wash_calls == 0, "이미 비었으면 배출 없음"
    assert pump.current_vol == 0.0


def test_outlet_waste_before_purge():
    """게이트①(적대검증 F1): 퍼지 배출 전 Outlet=WASTE 강제.
    직전 런이 수집(COLLECT) 중 abort → 밸브 COLLECT 잔류 시나리오에서
    리액터 방향 배출 치환분이 웰로 가지 않도록, 첫 액션이 Outlet→1 이어야 한다."""
    calls = []

    class _Outlet:
        def set_position(self, pos):
            calls.append(("outlet", pos))

    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    _orig_pp = pump.prime_prepare

    def _pp():
        calls.append(("prime_prepare",))
        return _orig_pp()
    pump.prime_prepare = _pp
    pump.current_vol = 4.2
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng.valves = {"Outlet": _Outlet()}
    eng._startup_level_reconcile()
    assert calls and calls[0] == ("outlet", 1), \
        f"Outlet→WASTE 가 첫 액션이어야 함, 실제 {calls}"
    assert ("prime_prepare",) in calls, "배출은 Outlet 확보 후 실행"
    assert pump.current_vol == 0.0


def test_outlet_waste_failure_raises():
    """게이트①: Outlet WASTE 전환 실패 = 배출 안전 미보장 → SafetyError (액추에이션 전)."""
    class _BadOutlet:
        def set_position(self, pos):
            raise RuntimeError("no ACK")

    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    pump.current_vol = 4.2
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng.valves = {"Outlet": _BadOutlet()}
    raised = False
    try:
        eng._startup_level_reconcile()
    except SafetyError:
        raised = True
    assert raised, "Outlet 전환 실패 시 SafetyError 기대"
    assert pump.prime_calls == 0, "밸브 미확보 상태에서 배출 금지"


def test_reagent_clog_raises_safety():
    """배출해도 안 빠짐(클로그 모델) → max_iter 후 SafetyError."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False)  # 클로그
    pump.prime_rate = 600.0   # 모니터드 창 단축 (테스트 속도)
    pump.current_vol = 4.2    # 거짓-0 금지 검증용 센티넬
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s, max_iter=3)
    raised = False
    try:
        eng._startup_level_reconcile()
    except SafetyError:
        raised = True
    assert raised, "배출 실패 시 SafetyError 기대"
    assert pump.prime_calls == 3, f"max_iter(3)회 배출 기대, 실제 {pump.prime_calls}"
    # 개정: 엔진이 배출 전 counter 를 센서 실측(2.0)으로 진실화 — '거짓 0' 만 금지
    assert pump.current_vol != 0.0, "미검증(SafetyError) 상태에서 current_vol=0 리셋 금지"
    assert abs(pump.current_vol - 2.0) < 1e-9, \
        f"counter=센서 실측(2.0mL) 진실화 기대, 실제 {pump.current_vol}"


def test_no_sensor_fallback_noop():
    """센서 None → 무동작 (기존 '가정 empty' 유지)."""
    pump = FakeSmartPump()
    pump.current_vol = 1.23
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, None)
    eng._startup_level_reconcile()   # 예외 없이 즉시 리턴
    assert pump.wash_calls == 0
    assert pump.current_vol == 1.23, "센서 없으면 current_vol 불변(리셋이 진실원)"


def test_solvent_policy_adopts_volume():
    """solvent 정책 → 잔량 그대로 채택(µL→mL), 배출 없음."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 3000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("solvent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()
    assert pump.wash_calls == 0, "solvent 정책은 배출 안 함"
    assert abs(pump.current_vol - 3.0) < 1e-9, f"3.0mL 채택 기대, 실제 {pump.current_vol}"


def test_adopt_clamps_to_capacity():
    """과대 판독 → adopt 시 capacity 클램프 (run-dry 가드 무력화 방지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", capacity=6.0)
    s.set_value("Group A", 50000.0)   # 50 mL — 6 mL 시린지엔 물리 불가(오판독)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("solvent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()
    assert abs(pump.current_vol - 6.0) < 1e-9, f"capacity(6.0) 클램프 기대, 실제 {pump.current_vol}"


def test_reagent_nrg_routing_skips_purge():
    """reagent 인데 external_valve 아님(NRG) → 배출 생략, 잔량 카운터 반영만 + 경고."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "internal_valve"}, s)
    eng._startup_level_reconcile()
    assert pump.wash_calls == 0, "internal_valve 는 외부 폐액 퍼지 미지원 → 생략"
    assert abs(pump.current_vol - 2.0) < 1e-9, "잔량 카운터 반영(run-dry 방지)"
    assert any("퍼지 미지원" in m for m in eng._logs), "경고 로그 기대"


def test_offplan_pump_skipped():
    """이번 플랜에 없는 펌프는 정합 대상에서 제외 (off-plan 액추에이션 금지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    s.set_value("Group A", 2000.0)
    plan = [{"flows": {"Group B": {}}}]   # Group A 는 이번 플랜에 없음
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s, plan=plan)
    eng._startup_level_reconcile(sequence_plan=plan)
    assert pump.wash_calls == 0, "off-plan 펌프는 배출/정합 안 함"


def test_abort_stops_purge():
    """abort_flag True → 배출 루프 즉시 중단 (미검증 리셋 금지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False)
    pump.current_vol = 4.2   # 리셋 금지 검증용 센티넬
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng.abort_flag = True
    eng._startup_level_reconcile()   # SafetyError 아니라 조용히 return
    assert pump.wash_calls == 0, "abort 면 배출 시작 전 중단"
    assert pump.current_vol == 4.2, "미검증(abort) 상태 current_vol=0 리셋 금지"


def test_bad_calibration_negative_raises_not_false_empty():
    """음수 환산(마운트/부호 오류) → get_volume 예외 → 측정실패 폴백(거짓 empty 금지)."""
    s = _sensor(["Group A"])
    # 부호 뒤집힘 시뮬: mock 은 set_value 우회이므로, 캘리브 자체를 실기 채널로 강제
    s.channels["Group A"]["mock"] = False        # 실기 경로 강제
    s.channels["Group A"]["slope"] = -1.0
    s.channels["Group A"]["intercept"] = 0.0
    s._serials["Mock_Port"] = _FakeSerial([b"100\n"] * 25)  # raw=100 → -100mL

    pump = FakeSmartPump(sensor=s, ch="Group A")
    pump.current_vol = 0.0
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()   # 측정 실패 → 스킵(폴백), 예외 전파 안 함
    assert pump.wash_calls == 0
    assert pump.current_vol == 0.0, "거짓 empty 인증이 아니라 폴백(리셋값 유지)"
    assert any("측정 실패" in m for m in eng._logs), "측정 실패 로그 기대"


def test_infuse_hw_failure_raises_not_silent():
    """[NEW-C 개정] HW 실패(무토출) → 센서가 안 줄어듦 → max_iter 후 SafetyError
    (조용한 false-clean 금지 — 성공 판정은 오직 센서 실측)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False, infuse_ok=False)
    pump.prime_rate = 600.0   # 모니터드 창 단축
    pump.current_vol = 4.2
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    raised = False
    try:
        eng._startup_level_reconcile()
    except SafetyError:
        raised = True
    assert raised, "HW 실패(무토출)는 SafetyError 로 승격돼야 함"
    assert pump.current_vol != 0.0, "실패 시 current_vol=0 리셋 금지(거짓 clean 금지)"


def test_infuse_genuine_abort_returns():
    """[NEW-C 개정] 배출 중 _abort_refill=True → 조용히 return (SafetyError 아님)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False,
                         infuse_ok=False, abort_refill=True)
    pump.current_vol = 4.2
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()   # 진짜 abort → return, 예외 없음
    assert pump.current_vol != 0.0, "abort 시 미검증 0 리셋 금지"


def test_adopt_negative_capacity_no_negative_volume():
    """[NEW-E] capacity 음수(오설정) → 클램프 비활성(1e9), current_vol 음수 방지."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", capacity=-5.0)
    s.set_value("Group A", 3000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("solvent")},
                       {"Group A": "external_valve"}, s)
    eng._startup_level_reconcile()
    assert pump.current_vol == 3.0, f"음수 capacity → 클램프 무효(3.0mL), 실제 {pump.current_vol}"


def test_plan_without_flows_skips_all():
    """[NEW-D] 플랜 주어졌으나 flows 없음 → 정합 생략(fail-safe, off-plan 액추에이션 방지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    pump.current_vol = 4.2
    s.set_value("Group A", 2000.0)
    plan = [{"temp": 25}]   # flows 키 없음(구조 이상)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s, plan=plan)
    eng._startup_level_reconcile(sequence_plan=plan)
    assert pump.wash_calls == 0, "flows 없는 플랜 → 정합/배출 생략"
    assert pump.current_vol == 4.2


class _FakeSerial:
    """ultrasonic_level._read_raw 용 최소 시리얼 스텁."""
    def __init__(self, lines):
        self._lines = list(lines)
        self.is_open = True
    def reset_input_buffer(self):
        pass
    def readline(self):
        return self._lines.pop(0) if self._lines else b""
    def close(self):
        self.is_open = False


# ── 러너 ─────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
