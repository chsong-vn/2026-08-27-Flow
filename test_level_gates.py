# -*- coding: utf-8 -*-
"""잔량생성 차단(도징 자동정지 유예) + 잔량제거(센서 게이트 ②③④⑤) 계약 테스트.

2026-07-28 도입분:
  - ultrasonic_level._read_raw 4채널 CSV(index) 파싱 + NA 처리 + 단일값 하위호환
  - StrictSequenceEngine._verify_pump_empty (log/warn/purge) — reconcile 추출 공용 루틴
  - StrictSequenceEngine._level_gate (지점별 action, purge 강등, force_action)
  - _execute_smart_dosing 종료부 '자동정지 유예' (dosing_autostop_grace_sec)

실행:  py -3.14 test_level_gates.py
(기존 reconcile 경로 회귀는 test_level_reconcile.py 가 커버 — 함께 실행할 것)
"""
import sys
import time
import types
import threading

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyError
from hardware.sensors.ultrasonic_level import (
    MockUltrasonicLevelSensor, LevelSensorError)
from test_level_reconcile import FakeSmartPump, _FakeSerial, _cfg, _sensor


# ── 공용 빌더 ─────────────────────────────────────────────────
def _make_engine(pumps, level_cfg, routing, sensor,
                 verify_points=None, max_iter=3):
    eng = StrictSequenceEngine.__new__(StrictSequenceEngine)
    eng.pumps = pumps
    eng.level_sensor = sensor
    eng.level_purge_max_iter = max_iter
    eng.level_verify_points = verify_points or {}
    eng.abort_flag = False
    eng.cfg = types.SimpleNamespace(PUMP_LEVEL_CFG=level_cfg, PUMP_ROUTING=routing)
    emitted = []
    eng.signals = types.SimpleNamespace(
        sig_level_data=types.SimpleNamespace(emit=lambda d: emitted.append(d)))
    eng._emitted = emitted
    eng._logs = []
    eng._log = lambda m, **k: eng._logs.append(m)
    return eng


# ═════════════════════════════════════════════════════════════
# 1) 드라이버 — 4채널 CSV index 파싱
# ═════════════════════════════════════════════════════════════
def _raw_channel_sensor(index, lines, slope=1.0, intercept=0.0, samples=5):
    s = MockUltrasonicLevelSensor(pumps=["P"])
    s.connect()
    ch = s.channels["P"]
    ch["mock"] = False          # 실기 경로 강제 (_read_raw 사용)
    ch["index"] = index
    ch["slope"] = slope
    ch["intercept"] = intercept
    ch["samples"] = samples
    s._serials["Mock_Port"] = _FakeSerial(lines)
    return s


def test_csv_index_parses_own_column():
    """CSV "d0,d1,NA,d3" 에서 index=1 열만 취해 median → mL 환산."""
    s = _raw_channel_sensor(1, [b"12.5,7.7,NA,3.2\n"] * 20)
    vol = s.get_volume("P")
    assert abs(vol - 7700.0) < 1e-6, f"7.7mL=7700uL 기대, 실제 {vol}"


def test_csv_na_column_fails_loud():
    """전 표본 NA(무반사) → 조용한 0 이 아니라 LevelSensorError."""
    s = _raw_channel_sensor(2, [b"12.5,7.7,NA,3.2\n"] * 20)
    raised = False
    try:
        s.get_volume("P")
    except LevelSensorError:
        raised = True
    assert raised, "NA 열은 시끄럽게 실패해야 함 (거짓 empty 금지)"


def test_csv_index0_backcompat_single_value():
    """index=0 은 구 단일값 펌웨어("13.42\\n")와도 호환 (split[0])."""
    s = _raw_channel_sensor(0, [b"13.42\n"] * 20)
    vol = s.get_volume("P")
    assert abs(vol - 13420.0) < 1e-6, f"13.42mL 기대, 실제 {vol}"


def test_no_index_legacy_line_mode():
    """index 미지정(-1) → 기존 단일 float 라인 파싱 그대로."""
    s = _raw_channel_sensor(-1, [b"2.5\n"] * 20)
    vol = s.get_volume("P")
    assert abs(vol - 2500.0) < 1e-6, f"2.5mL 기대, 실제 {vol}"


def test_csv_short_row_skipped():
    """열 수 부족 라인은 무효 표본 → 유효 라인만으로 판독."""
    lines = [b"1.0\n"] * 3 + [b"9.9,4.4\n"] * 20   # index=1: 앞 3줄은 열 부족
    s = _raw_channel_sensor(1, lines)
    vol = s.get_volume("P")
    assert abs(vol - 4400.0) < 1e-6, f"4.4mL 기대, 실제 {vol}"


# ═════════════════════════════════════════════════════════════
# 2) _verify_pump_empty / _level_gate — 게이트 정책
# ═════════════════════════════════════════════════════════════
def test_gate_wash_purge_empties():
    """게이트②(purge): 잔량>gate → wash_infuse 배출 → 검증 → current_vol=0."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    pump.current_vol = 3.3
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"wash": "purge"})
    out = eng._level_gate(["Group A"], "wash", "세척후")
    assert pump.wash_calls == 1, f"배출 1회 기대, 실제 {pump.wash_calls}"
    assert pump.current_vol == 0.0, "검증 empty 후 current_vol=0"
    assert out["Group A"][0] is True
    assert eng._emitted, "sig_level_data emit 기대"


def test_gate_warn_no_actuation():
    """warn: 잔량 검출 시 경고만 — 액추에이션/카운터 변경 금지."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    pump.current_vol = 3.3
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"post_inject": "warn"})
    out = eng._level_gate(["Group A"], "post_inject", "주입후")
    ok, vol = out["Group A"]
    assert pump.wash_calls == 0, "warn 은 배출 금지"
    assert pump.current_vol == 3.3, "warn 은 카운터 불변"
    assert ok is False and abs(vol - 2000.0) < 1e-9
    assert any("의심" in m for m in eng._logs), "경고 로그 기대"


def test_gate_off_noop():
    """off: 측정 자체를 안 함 (센서 판독 비용/로그 없음)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"wash": "off"})
    out = eng._level_gate(["Group A"], "wash", "세척후")
    assert out == {} and pump.wash_calls == 0 and not eng._logs


def test_gate_purge_demoted_for_internal_valve():
    """purge 인데 external_valve 아님(NRG 등) → warn 강등 (폐액 경로 없음)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "internal_valve"}, s,
                       verify_points={"wash": "purge"})
    out = eng._level_gate(["Group A"], "wash", "세척후")
    assert pump.wash_calls == 0, "internal_valve 는 배출 강등"
    assert out["Group A"][0] is False, "잔량 검출은 보고돼야 함"


def test_gate_force_action_log_overrides_purge():
    """⑤cleanup 경로: force_action='log' 가 config purge 를 강등 (액추에이션 금지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"seq_end": "purge"})
    eng._level_gate(["Group A"], "seq_end", "시퀀스종료", force_action="log")
    assert pump.wash_calls == 0, "log 강제 시 배출 금지"
    assert any("기록" in m for m in eng._logs)


def test_gate_no_sensor_noop():
    """센서 None → 무동작 {} (기존 폴백)."""
    pump = FakeSmartPump()
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, None,
                       verify_points={"wash": "purge"})
    assert eng._level_gate(["Group A"], "wash", "세척후") == {}


def test_gate_unregistered_pump_skipped():
    """PUMP_LEVEL_CFG 미등록 펌프는 제외 (캘리브 없는 채널 오판 방지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A")
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group_B": pump}, {}, {}, s,
                       verify_points={"wash": "purge"})
    assert eng._level_gate(["Group_B"], "wash", "세척후") == {}
    assert pump.wash_calls == 0


def test_gate_purge_clog_raises_safety():
    """게이트② purge 도 reconcile 과 동일하게 max_iter 초과 시 SafetyError."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False)   # 클로그
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"wash": "purge"}, max_iter=2)
    raised = False
    try:
        eng._level_gate(["Group A"], "wash", "세척후")
    except SafetyError:
        raised = True
    assert raised and pump.wash_calls == 2


def test_gate_reactor_purge_uses_prime():
    """discharge='reactor': wash_infuse 대신 prime 경로(리액터 방향)로 배출.
    (②세척후/④푸시후 — 내용물=용매, 다운스트림 플러시 겸)"""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"push_end": "purge"})
    out = eng._level_gate(["Group A"], "push_end", "푸시후", discharge="reactor")
    assert pump.prime_calls == 1, f"prime 1회 기대, 실제 {pump.prime_calls}"
    assert pump.wash_calls == 0, "reactor 배출은 12-way 폐액 경유 금지"
    assert abs(pump.last_prime_vol - 2.0) < 1e-9, \
        f"토출량=실측 2.0mL 그대로(마진 금지), 실제 {pump.last_prime_vol}"
    assert out["Group A"][0] is True and pump.current_vol == 0.0


def test_gate_reactor_rate_override_and_restore():
    """④푸시후: rates={펌프: 스텝유속} → prime_rate 임시 오버라이드 후 복원."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    s.set_value("Group A", 1500.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"push_end": "purge"})
    eng._level_gate(["Group A"], "push_end", "푸시후",
                    discharge="reactor", rates={"Group A": 2.5})
    assert abs(pump.last_prime_rate - 2.5) < 1e-9, \
        f"스텝 유속 2.5 로 토출 기대, 실제 {pump.last_prime_rate}"
    assert abs(pump.prime_rate - 8.0) < 1e-9, \
        f"prime_rate 복원(8.0) 기대, 실제 {pump.prime_rate}"


def test_gate_reactor_clog_raises_safety():
    """reactor 배출도 max_iter 초과 시 SafetyError (클로그 — 밀어도 안 빠짐)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=False)
    pump.prime_rate = 600.0   # 모니터드 창 단축 (테스트 속도)
    s.set_value("Group A", 2000.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"push_end": "purge"}, max_iter=2)
    raised = False
    try:
        eng._level_gate(["Group A"], "push_end", "푸시후", discharge="reactor")
    except SafetyError:
        raised = True
    assert raised and pump.prime_calls == 2


def test_gate_waste_sizing_no_margin():
    """waste 배출도 sizing=실측치 그대로 (하드스톱 과주행→펌프 에러 방지)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True, wash_volume=9.0)
    s.set_value("Group A", 1200.0)
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"wash": "purge"})
    eng._level_gate(["Group A"], "wash", "세척후")   # discharge 기본 waste
    assert abs(pump.sized_wv - 1.2) < 1e-9, \
        f"sizing=실측 1.2mL 그대로 기대, 실제 {pump.sized_wv}"
    assert pump.wash_volume == 9.0, "wash_volume 복원"


def test_verify_empty_measure_fail_fallback():
    """측정 실패 → (True, None) 무판정 폴백 — 거짓 경보/거짓 empty 금지."""
    s = _sensor(["Group A"])
    s.channels["Group A"]["mock"] = False
    s._serials["Mock_Port"] = _FakeSerial([b"garbage\n"] * 30)   # 유효표본 0
    pump = FakeSmartPump(sensor=s, ch="Group A")
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent")},
                       {"Group A": "external_valve"}, s,
                       verify_points={"wash": "purge"})
    ok, vol = eng._verify_pump_empty("Group A", pump, gate=500.0, action="purge")
    assert ok is True and vol is None
    assert any("측정 실패" in m for m in eng._logs)


# ═════════════════════════════════════════════════════════════
# 3) 도징 자동정지 유예 (잔량생성 차단)
# ═════════════════════════════════════════════════════════════
class _ScriptDrv:
    """is_stopped 가 지정 시각 이후 True 가 되는 드라이버 스텁 (자동정지 모델)."""
    def __init__(self, stop_after_sec=None):
        self._t0 = time.time()
        self._stop_after = stop_after_sec   # None = 영원히 구동(고착 모델)
        self.polls = 0

    def is_stopped(self):
        self.polls += 1
        if self._stop_after is None:
            return False
        return (time.time() - self._t0) >= self._stop_after


class FakeDosingPump:
    """_is_smart_pump 계약 + set_flow/start/stop 기록 (도징 루프용)."""
    _API = ("refill_prepare", "refill_trigger", "refill_complete",
            "prime_prepare", "prime_trigger", "prime_complete",
            "wash_withdraw_prepare", "wash_infuse_prepare")

    def __init__(self, driver, current_vol=5.0):
        self.driver = driver
        self.current_vol = current_vol
        self.capacity = 6.0
        self.is_refilling = False
        self.target_flow = -1.0
        self.running = False
        self.stop_calls = 0
        for m in self._API:
            setattr(self, m, (lambda *a, **k: None))

    def set_flow(self, rate):
        self.target_flow = float(rate)

    def start(self):
        self.running = True

    def stop(self):
        self.stop_calls += 1
        self.running = False


def _make_dosing_engine(pump, grace):
    eng = StrictSequenceEngine.__new__(StrictSequenceEngine)
    eng.pumps = {"P": pump}
    eng.abort_flag = False
    eng.pause_event = threading.Event()
    eng.pause_event.set()
    eng._collection_timer = None
    eng.dosing_autostop_grace_sec = grace
    eng._check_abort = lambda: None
    eng._emit_status = lambda *a, **k: None
    eng._emit_phase = lambda *a, **k: None
    eng._logs = []
    eng._log = lambda m, **k: eng._logs.append(m)
    return eng


def test_dosing_grace_waits_for_autostop():
    """유예창 안에 자동정지 도달 → 강제 stop 없음 (미토출 0)."""
    drv = _ScriptDrv(stop_after_sec=1.0)          # 창(0.6s) 종료 0.4s 뒤 자동정지
    pump = FakeDosingPump(drv)
    eng = _make_dosing_engine(pump, grace=3.0)
    eng._execute_smart_dosing({"P": 1.0}, duration_sec=0.6,
                              step_name="T", allow_refill=False)
    assert pump.stop_calls == 0, f"자동정지 존중 — 강제 stop 금지, 실제 {pump.stop_calls}회"
    assert not any("강제 정지" in m for m in eng._logs)


def test_dosing_grace_timeout_forces_stop():
    """유예 초과에도 구동 중(고착) → 강제 stop + '미토출 의심' 경고."""
    drv = _ScriptDrv(stop_after_sec=None)         # 영원히 구동
    pump = FakeDosingPump(drv)
    eng = _make_dosing_engine(pump, grace=1.0)
    eng._execute_smart_dosing({"P": 1.0}, duration_sec=0.6,
                              step_name="T", allow_refill=False)
    assert pump.stop_calls == 1, "유예 초과 시 강제 stop"
    assert any("미토출 의심" in m for m in eng._logs), "경고 로그 기대"


def test_dosing_grace_zero_legacy():
    """grace=0 → 기존 동작(유예 없이 즉시 stop 경로)."""
    drv = _ScriptDrv(stop_after_sec=None)
    pump = FakeDosingPump(drv)
    eng = _make_dosing_engine(pump, grace=0.0)
    t0 = time.time()
    eng._execute_smart_dosing({"P": 1.0}, duration_sec=0.6,
                              step_name="T", allow_refill=False)
    elapsed = time.time() - t0
    assert pump.stop_calls == 1
    assert elapsed < 1.6, f"유예 대기 없어야 함 (elapsed={elapsed:.2f}s)"
    assert not any("자동정지 유예" in m for m in eng._logs)


# ═════════════════════════════════════════════════════════════
# 4) 고정에코 방어 — empty 인증 연속 2회 판독 (2026-07-29 적대검증)
# ═════════════════════════════════════════════════════════════
def test_empty_cert_confirm_read_catches_echo():
    """첫 판독이 고정에코로 ≤gate 를 속여도, 확인판독이 잔량을 보면 SafetyError.
    (ch0_sweep 실측: raw 8.61cm 락온 버스트가 진짜 잔량 ~500µL 를 0 으로 오판)"""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    pump.current_vol = 0.8
    # 판독1 = 40µL(고정에코 거짓 empty) → 확인판독 = 800µL(진짜 잔량)
    s.set_sequence("Group A", [40.0, 800.0])
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent", gate=100.0)},
                       {"Group A": "external_valve"}, s)
    raised = False
    try:
        eng._verify_pump_empty("Group A", pump, gate=100.0, action="purge")
    except SafetyError as e:
        raised = True
        assert "확인판독" in str(e)
    assert raised, "확인판독 불일치 시 SafetyError 기대 (거짓 empty 인증 금지)"
    assert pump.current_vol != 0.0, "미검증 상태에서 current_vol=0 금지"


def test_empty_cert_confirm_read_passes():
    """연속 2회 모두 ≤gate 면 정상 인증 (current_vol=0)."""
    s = _sensor(["Group A"])
    pump = FakeSmartPump(sensor=s, ch="Group A", empties=True)
    pump.current_vol = 0.8
    s.set_sequence("Group A", [40.0, 30.0])
    eng = _make_engine({"Group A": pump}, {"Group A": _cfg("reagent", gate=100.0)},
                       {"Group A": "external_valve"}, s)
    ok, vol = eng._verify_pump_empty("Group A", pump, gate=100.0, action="purge")
    assert ok is True and pump.current_vol == 0.0
    assert vol is not None and vol <= 100.0


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
