# -*- coding: utf-8 -*-
"""fault-masking 수정 3건 검증 (2026-07-06 감사 F1/F2/F3).

F1 ArduinoValve: ACK 실패 = RuntimeError + position 미갱신(거짓보고 제거),
                 ACK 성공 = position 갱신
F2 Plate96: M400 무응답 = (False, timeout) + 위치 미확인 플래그 →
            다음 이동 same_row 여도 강제 풀 Z리프트 (긁힘 방지)
F3 MIN_TUBE_SEC: system_params.min_tube_sec 로 상향 가능 (기본 2.0 유지)
"""
import sys, os, threading, time

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔/파이프 출력 방어
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# ══ F1: ArduinoValve ══════════════════════════════════════════
print("=== F1 ArduinoValve ACK fault-masking ===")
from hardware.valves.valve_arduino import ArduinoValve


class FakeSerial:
    def __init__(self, ack=True):
        self.is_open = True
        self.ack = ack
        self.last_msg = b""
    def reset_input_buffer(self): pass
    def write(self, data): self.last_msg = data
    def read_all(self):
        if not self.ack:
            return b""            # 보드 무응답
        # 채널 1 → real_ch 4 (_CH_MAP)
        msg = self.last_msg.decode().strip()   # "4 2"
        return f"OK {msg}\r\n".encode()


PORT = "COM_TEST_F1"
v = ArduinoValve(PORT, channel=1)
ArduinoValve._serial_registry[PORT] = FakeSerial(ack=False)
ArduinoValve._lock_registry[PORT] = threading.Lock()

# (a) 무응답 → RuntimeError + position 불변(진실 유지)
t0 = time.time()
raised = False
try:
    v.set_position(2)
except RuntimeError as e:
    raised = True
    print(f"  (raise: {str(e)[:70]}...)")
check("F1a 무ACK → RuntimeError", raised)
check("F1a position 미갱신(거짓보고 제거)", v.get_position() == 1,
      f"(pos={v.get_position()})")
check("F1a 3회 재시도 수행(≈1.5s+)", time.time() - t0 > 1.2)

# (b) ACK 정상 → position 갱신
ArduinoValve._serial_registry[PORT] = FakeSerial(ack=True)
v.set_position(2)
check("F1b ACK → position=2", v.get_position() == 2)
v.set_position("WASTE")
check("F1b 문자열 별칭 → position=1", v.get_position() == 1)
del ArduinoValve._serial_registry[PORT]
del ArduinoValve._lock_registry[PORT]

# ══ F2: Plate96 M400 미확인 → 풀 Z리프트 ══════════════════════
print("=== F2 Plate96 위치 미확인 → 풀 Z리프트 ===")
from hardware.collectors.collector_plate96 import Plate96Collector

c = Plate96Collector.__new__(Plate96Collector)
c.is_connected = True
c.current_position = 0
c._motion_confirmed = True
c.total_tubes = 3
c.well_sequence = [
    {'plate': 'A', 'well': 'A1', 'row_idx': 0, 'machine_X': 0.0, 'machine_Y': 0.0},
    {'plate': 'A', 'well': 'A2', 'row_idx': 0, 'machine_X': 9.0, 'machine_Y': 0.0},
    {'plate': 'A', 'well': 'B1', 'row_idx': 1, 'machine_X': 0.0, 'machine_Y': 9.0},
]
c.z_travel, c.z_dispense = 29.0, 27.0
c.feedrate_xy, c.feedrate_z = 6000, 1200

SENT = []
M400_FAIL = {"on": False}


def fake_send(cmd, wait=1.0):
    SENT.append(cmd)
    if cmd == "M400" and M400_FAIL["on"]:
        return ""
    return "ok"


c._send_wait = fake_send

# (a) 정상 이동 tube1 (홈에서 → 풀 리프트) : True
ok, msg = c.move_to_tube(1)
check("F2a 정상 이동 True", ok, f"({msg})")
check("F2a 플래그 확정", c._motion_confirmed is True)

# (b) tube2 (같은 행) 이동인데 M400 무응답 → False + 플래그 해제, 위치는 목표 추정
SENT.clear()
M400_FAIL["on"] = True
ok, msg = c.move_to_tube(2)
check("F2b M400 timeout → False", not ok, f"({msg})")
check("F2b timeout 사유 명시", "timeout" in msg.lower())
check("F2b 위치=목표 추정(절대좌표)", c.current_position == 2)
check("F2b 미확인 플래그", c._motion_confirmed is False)
check("F2b 같은 행이라 Z리프트 생략(정상 최적화)",
      not any(cmd.startswith("G1 Z29") for cmd in SENT), str(SENT))

# (c) 다음 이동 tube1 — 명목상 같은 행이지만 위치 미확인 → 강제 풀 Z리프트
SENT.clear()
M400_FAIL["on"] = False
ok, msg = c.move_to_tube(1)
z_cmds = [cmd for cmd in SENT if cmd.startswith("G1 Z")]
check("F2c 미확인 후 이동 → 풀 Z리프트 강제",
      any(f"Z{c.z_travel}" in cmd for cmd in z_cmds), str(SENT))
check("F2c 성공 후 플래그 복구", ok and c._motion_confirmed is True)

# (d) 확인된 같은 행 이동은 여전히 Z 생략 (최적화 보존)
SENT.clear()
ok, _ = c.move_to_tube(2)
check("F2d 확인된 same-row → Z 생략 유지",
      ok and not any(cmd.startswith("G1 Z") for cmd in SENT), str(SENT))

# ══ F2 확장 (2026-07-29 적대검증): home/stop/safe_height 응답·상태 규약 ══
print("=== F2 확장: G28 검증 + 상태 무효화 ===")
G28_RESP = {"v": "X:0.00 Y:0.00 Z:0.00\nok"}


def fake_send2(cmd, wait=1.0):
    SENT.append(cmd)
    if cmd == "G28":
        return G28_RESP["v"]
    return "ok"


c._send_wait = fake_send2

# (e) G28 무응답 → False + 미확인 (성공 가장 금지)
G28_RESP["v"] = ""
ok, msg = c.home()
check("F2e G28 무응답 → False", not ok, f"({msg})")
check("F2e 미확인 플래그", c._motion_confirmed is False)

# (f) G28 에러 텍스트(kill) → False — 비어있지 않아도 성공 아님
G28_RESP["v"] = "Error:Printer halted. kill() called!"
ok, msg = c.home()
check("F2f G28 kill 텍스트 → False", not ok, f"({msg})")

# (g) G28 정상(ok 수신) → True + 위치 0 확정
G28_RESP["v"] = "X:0.00 Y:0.00 Z:0.00\nok"
ok, msg = c.home()
check("F2g G28 정상 → True + 확정",
      ok and c._motion_confirmed is True and c.current_position == 0, f"({msg})")


# (h) stop_motion(M410 quickstop) → 위치 미확인 (중단된 이동 = 물리 위치 미지)
class _FakeSer:
    is_open = True
    def write(self, b): pass
    def flush(self): pass


c.ser = _FakeSer()
c._motion_confirmed = True
c.stop_motion()
check("F2h stop_motion → 위치 미확인", c._motion_confirmed is False)

# (i) goto_safe_height → 미확인 (Z=travel 이탈, 다음 same-row Z-생략 금지 = 6mm 상공 분주 방지)
c._motion_confirmed = True
ok, _ = c.goto_safe_height()
check("F2i goto_safe_height → 위치 미확인", ok and c._motion_confirmed is False)

# ══ F3: MIN_TUBE_SEC 설정 노출 ════════════════════════════════
print("=== F3 min_tube_sec 설정 ===")
from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager, SafetyError


class _Sig:
    def __init__(self):
        emit = lambda *a, **k: None
        for n in ("sig_log", "sig_status", "sig_phase_progress",
                  "sig_progress", "sig_finished", "sig_error"):
            setattr(self, n, type("S", (), {"emit": staticmethod(emit)})())


class _Pump:  # non-smart (용량 분기 스킵)
    running = False
    def stop(self): pass


class _Heater:
    target_temp = 25.0
    def set_temperature(self, t): pass
    def get_temperature(self): return 25.0
    def stop(self): pass


class _Coll:
    is_connected = True
    total_tubes = 88
    def get_position(self): return 0


class _Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = 0.6
    ACTIVE_PUMPS = ["A", "B"]
    tjunction_line_vols = {}
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
        "syringe_refill_rate": 20.0,
    }}


def make_engine(min_tube_sec=None):
    cfg = _Cfg()
    cfg.config_data = {"system_params": dict(_Cfg.config_data["system_params"])}
    if min_tube_sec is not None:
        cfg.config_data["system_params"]["min_tube_sec"] = min_tube_sec
    pumps = {"A": _Pump(), "B": _Pump()}
    return StrictSequenceEngine(cfg, pumps, {}, _Heater(),
                                SafetyManager(cfg, pumps, _Heater()), _Sig(),
                                collector=_Coll())


step = {"temp": 25.0, "vol_ml": 0.45, "residence_time": 18.0,
        "inlet_ports": {"A": 2, "B": 2}, "flows": {"A": 1.0, "B": 1.0},
        "collect_volume_per_tube": 0.15, "meta": {}}
frac = {"enabled": True}
# tube_sec = 0.15/2.0*60 = 4.5s
eng = make_engine()                    # 기본 2.0 → 통과
try:
    eng._validate_step_inputs(1, dict(step), dict(frac))
    ok_default = True
except SafetyError as e:
    ok_default = False
    print(f"  (unexpected: {e})")
check("F3a 기본 2.0s: tube_sec 4.5s 통과", ok_default)

eng = make_engine(min_tube_sec=10.0)   # 상향 → 차단
try:
    eng._validate_step_inputs(1, dict(step), dict(frac))
    blocked = False
except SafetyError as e:
    blocked = True
    print(f"  (SafetyError: {str(e)[:70]}...)")
check("F3b min_tube_sec=10: 4.5s 사전 차단", blocked)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
