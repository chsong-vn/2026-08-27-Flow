"""OPB 상전이(0↔1) 시스템 로그 훅 검증 — 2026-08-18 사용자 요청.

루트에서 실행:  py -3.14 tests\test_phase_edge_log.py

계약:
  · 전이는 debounce 확정 시점에 (논리키, old, new, adc) 로 훅 호출
  · 최초 확정(부팅 첫 상태)은 전이 아님 — 훅 미호출
  · 채터(단발 반전)는 debounce 가 흡수 — 훅 미호출
  · 훅은 드라이버 락 '해제 후' 호출 (훅 안에서 드라이버 API 재진입 가능 = 무교착)
  · 훅 예외는 리더를 죽이지 않음
  · monitor 이벤트 기구와 독립 (monitor never 여도 로그 훅은 발화)
  · 엔진: phase_sensor 주입 시 on_transition 자동 배선 + [PhaseEdge] 로그 포맷
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware.sensors.phase_sensor_opb import PhaseSensorOPBADC
from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


class FakeSerial:
    """readline 계약 페이크 — 준비된 라인 소진 후 b'' (타임아웃 시맨틱)."""

    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        time.sleep(0.02)
        return b""

    def close(self):
        pass


def mk_lines(seq):
    """[(adc1, adc2), ...] → 스트림 바이트 라인 (라벨 포맷)."""
    return [f"S1:{a},0 | S2:{b},0\r\n".encode() for a, b in seq]


def run_driver(lines, hook, thresholds=None):
    s = PhaseSensorOPBADC("COMX",
                          sensors={"reactor_in": 0, "collect": 1},
                          thresholds=thresholds or {"reactor_in": 440, "collect": 717})
    s.on_transition = hook
    s.connect(serial_override=FakeSerial(lines))
    time.sleep(0.6)                    # 리더가 라인 소진할 시간
    s.disconnect()
    return s


print("=" * 70)
print("[1] 정상 전이 — 액체→기체 1회 = 훅 1회")
print("=" * 70)
calls = []
# ch0: 800(액체)×3 → 80(기체)×3   / ch1: 내내 100(기체, 최초확정만)
run_driver(mk_lines([(800, 100)] * 3 + [(80, 100)] * 3),
           lambda k, o, n, a: calls.append((k, o, n, a)))
check("훅 정확히 1회", len(calls) == 1, calls)
check("내용 = 센서1 1→0", calls and calls[0][:3] == ("reactor_in", 1, 0), calls)
check("ADC 동반", calls and calls[0][3] == 80, calls)

print("=" * 70)
print("[2] 최초 확정은 전이 아님")
print("=" * 70)
calls = []
run_driver(mk_lines([(800, 100)] * 4),
           lambda k, o, n, a: calls.append((k, o, n, a)))
check("훅 0회 (부팅 첫 상태)", not calls, calls)

print("=" * 70)
print("[3] 채터 흡수 — 단발 반전은 debounce 가 걸러냄")
print("=" * 70)
calls = []
# 액체 안정 후 기체 '1표본'만 (debounce_n=2 미달) → 다시 액체
run_driver(mk_lines([(800, 100)] * 3 + [(80, 100)] + [(800, 100)] * 3),
           lambda k, o, n, a: calls.append((k, o, n, a)))
check("훅 0회 (채터)", not calls, calls)

print("=" * 70)
print("[4] 양 채널 동시 전이 — 각각 1회씩")
print("=" * 70)
calls = []
run_driver(mk_lines([(800, 900)] * 3 + [(80, 100)] * 3),
           lambda k, o, n, a: calls.append((k, o, n, a)))
keys = sorted(c[0] for c in calls)
check("훅 2회 (센서1·2)", keys == ["collect", "reactor_in"], calls)

print("=" * 70)
print("[5] 훅 예외 = 리더 생존 (다음 전이도 발화)")
print("=" * 70)
calls = []


def bad_hook(k, o, n, a):
    calls.append((k, o, n))
    raise RuntimeError("hook boom")


run_driver(mk_lines([(800, 100)] * 3 + [(80, 100)] * 3 + [(800, 100)] * 3),
           bad_hook)
check("예외에도 전이 2회 모두 수신", len(calls) == 2, calls)

print("=" * 70)
print("[6] 훅 안에서 드라이버 API 재진입 (락 해제 후 호출 증명)")
print("=" * 70)
result = {}


def reentrant_hook(k, o, n, a):
    # 락 보유 중이면 여기서 교착 — 락 해제 후 호출이므로 즉시 반환돼야 함
    result["adc"] = None
    s2 = result["sensor"]
    result["adc"] = s2.analog(k)


s = PhaseSensorOPBADC("COMX", sensors={"reactor_in": 0, "collect": 1},
                      thresholds={"reactor_in": 440, "collect": 717})
result["sensor"] = s
s.on_transition = reentrant_hook
s.connect(serial_override=FakeSerial(mk_lines([(800, 100)] * 3 + [(80, 100)] * 3)))
time.sleep(0.6)
s.disconnect()
check("재진입 성공 (무교착)", result.get("adc") == 80, result.get("adc"))

print("=" * 70)
print("[7] 엔진 배선 — 주입 시 자동 등록 + [PhaseEdge] 로그 포맷")
print("=" * 70)


class _H:
    def get_temp(self):
        return 25.0


class _Sig:
    def __getattr__(self, _):
        class _E:
            @staticmethod
            def emit(*a, **k):
                pass
        return _E()


class _Cfg:
    PUMP_VALVE_MAP = {}
    PUMP_ROUTING = {}
    ACTIVE_PUMPS = []
    reactor_vol = 2.7
    reactor_vol_illuminated = 2.4
    tjunction_line_vols = {}
    tjunction_entry_map = {}
    line_vol_inlet = {}
    line_vol_valve_pump = {}
    line_vol_pump_merge = {}
    valve_internal_vol = {}
    mixing_line_dead_vol = 0.0
    config_data = {"system_params": {
        "post_reactor_vol_ml": 0.2, "collection_line_vol_ml": 0.15,
        "temp_tolerance_c": 0.5, "heater_reach_timeout_sec": 30.0,
        "max_total_flow_ml_min": 100.0, "max_step_volume_ml": 500.0,
        "wash_mode": "off", "prefill_mode": "off",
        "syringe_refill_rate": 20.0}}


class _Ps:
    sensors = {"reactor_in": 0, "collect": 1}
    thresholds = {0: 440, 1: 717}
    on_transition = None


ps = _Ps()
cfg = _Cfg()
eng = StrictSequenceEngine(cfg, {}, {}, _H(),
                           SafetyManager(cfg, {}, _H()), _Sig(),
                           phase_sensor=ps)
check("엔진이 on_transition 자동 배선",
      ps.on_transition == eng._log_phase_transition)
# 로그 포맷 스모크 — 예외 없이 콘솔/trace 로 흘러야 함
eng._log_phase_transition("collect", 0, 1, 985)
eng._log_phase_transition("reactor_in", 1, 0, 82)
check("훅 스모크 (예외 없음)", True)

print()
print("RESULT:", "ALL PASS" if not FAILED else f"{len(FAILED)} FAIL: {FAILED}")
sys.exit(1 if FAILED else 0)
