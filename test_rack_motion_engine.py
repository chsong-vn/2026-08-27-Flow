# -*- coding: utf-8 -*-
"""에펜 랙 이동 로직 + 엔진 용량/포트 검증 (오프라인, G-code 레벨).

 A. 이동 로직 — 실제 G-code 가 랙 좌표대로 나가는지, 서펜타인 순서, 같은 행
    Z-생략, 행 전환 시 travel(49)→dispense(43.5), 범위 밖 거부, WASH/미확인 처리
 B. 엔진 검증 — 웰 용량 1.5 mL 상한, 튜브 수 초과 차단(50 튜브 랙), HTE 슬러그
 C. 96-well 하위호환 — 동일 검증이 192 튜브/기존 Z 로 동작
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from hardware.collectors.collector_plate96 import Plate96Collector
from engine.strict_engine import StrictSequenceEngine, SafetyError

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


class FakeMarlin:
    """Marlin 응답 시뮬 — 보낸 명령을 전부 기록."""

    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok
        self.is_open = True

    def write(self, data):
        self.sent.append(data.decode().strip())

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def readline(self):
        return b"ok\n" if self.ok else b""

    def read(self, n=1):
        return b"ok\n" if self.ok else b""

    @property
    def in_waiting(self):
        return 3 if self.ok else 0

    def close(self):
        pass


def make_collector(coords_file=None, ok=True):
    c = Plate96Collector(coords_file=coords_file)
    c.ser = FakeMarlin(ok=ok)
    c.is_connected = True
    return c


def gcodes(c):
    return [s for s in c.ser.sent if s.startswith(("G1", "G28", "M400"))]


RACK = "well_coordinates_eppendorf_5x5.json"
DATA = os.path.join("hardware", "collectors", "data")
rack_json = json.load(open(os.path.join(DATA, RACK), encoding="utf-8"))
by_id = {(w["plate"], w["well"]): w for w in rack_json["wells"]}

print("== A. 이동 로직 (에펜 5×5 랙) ==")
c = make_collector(RACK)
check("50 튜브 로드", c.total_tubes == 50, str(c.total_tubes))

# A-1. tube 1 = A_A1 → JSON 좌표와 정확히 일치하는 G1 이 나가야 함
c.ser.sent.clear()
ok, msg = c.move_to_tube(1)
w = by_id[("A", "A1")]
g = gcodes(c)
check("tube1 이동 성공", ok, msg)
check("tube1 = A_A1 좌표 G1",
      f"G1 X{w['machine_X']} Y{w['machine_Y']} F{c.feedrate_xy}" in g, str(g))
check("행 전환: travel 49 상승 후 dispense 43.5 하강",
      f"G1 Z{c.z_travel} F{c.feedrate_z}" in g and f"G1 Z{c.z_dispense} F{c.feedrate_z}" in g,
      str(g))
check("M400 동기 대기", "M400" in g)
check("LCD 랙 라벨", any("M117 Eppendorf 5x5 A_A1 (1/50)" in s for s in c.ser.sent))

# A-2. 같은 행 이동(1→2)은 Z 명령 없이 XY 만
c.ser.sent.clear()
c.move_to_tube(2)
g = gcodes(c)
w2 = by_id[("A", "A2")]
check("같은 행 Z 생략 (XY만)",
      not any(s.startswith("G1 Z") for s in g)
      and f"G1 X{w2['machine_X']} Y{w2['machine_Y']} F{c.feedrate_xy}" in g, str(g))

# A-3. 서펜타인: tube 6 = B5 (역방향 행), tube 26 = Plate B A1
c.ser.sent.clear()
c.move_to_tube(6)
w6 = by_id[("A", "B5")]
check("tube6 = A_B5 (서펜타인 역행)",
      f"G1 X{w6['machine_X']} Y{w6['machine_Y']} F{c.feedrate_xy}" in gcodes(c),
      c.get_well_id(6))
c.ser.sent.clear()
c.move_to_tube(26)
w26 = by_id[("B", "A1")]
check("tube26 = B_A1 (우측 랙 진입)",
      f"G1 X{w26['machine_X']} Y{w26['machine_Y']} F{c.feedrate_xy}" in gcodes(c),
      c.get_well_id(26))
check("plate 전환 시 Z 리프트 수반",
      any(s.startswith(f"G1 Z{c.z_travel}") for s in gcodes(c)))

# A-4. 범위 밖 인덱스 거부 (51 이상)
c.ser.sent.clear()
ok51, msg51 = c.move_to_tube(51)
check("tube51 거부 (50 튜브 랙)", ok51 is False and "Invalid index" in msg51, msg51)
check("거부 시 모션 명령 없음", gcodes(c) == [], str(gcodes(c)))

# A-5. M400 미확인 → 실패 보고 + 다음 이동 강제 풀 리프트
c2 = make_collector(RACK, ok=False)
okf, msgf = c2.move_to_tube(1)
check("M400 무응답 → 실패 보고", okf is False and "unconfirmed" in msgf, msgf)
check("_motion_confirmed=False", c2._motion_confirmed is False)
c2.ser.ok = True
c2.ser.sent.clear()
c2.move_to_tube(2)   # 같은 행이지만 위치 미확인 → Z 리프트 강제
check("미확인 후 같은 행도 풀 Z 리프트",
      any(s.startswith(f"G1 Z{c2.z_travel}") for s in gcodes(c2)), str(gcodes(c2)))

# A-6. move_to_well 직접 지정
c.ser.sent.clear()
okw, _ = c.move_to_well("B", "E5")
wl = by_id[("B", "E5")]
check("move_to_well(B,E5)",
      okw and f"G1 X{wl['machine_X']} Y{wl['machine_Y']} F{c.feedrate_xy}" in gcodes(c))

print("== B. 엔진 검증 (웰 용량 1.5 mL / 튜브 수) ==")


class _Cfg:
    max_temp = 120.0
    max_pressure = 20.0
    max_total_flow = 100.0
    max_step_volume_ml = 500.0
    ACTIVE_PUMPS = ["Group A"]
    PUMP_ROUTING = {"Group A": "external_valve"}
    config_data = {"system_params": {}, "roles": {}, "inventory": []}
    reactor_vol = 1.98
    dead_vol_solvent = {"Group A": 0.0}
    dead_vol_reagent = {"Group A": 0.0}
    mixing_line_dead_vol = 0.0

    def collect_wash_tubes(self):
        return 0


def make_engine(collector):
    e = StrictSequenceEngine.__new__(StrictSequenceEngine)
    e.cfg = _Cfg()
    e.collector = collector
    e.pumps = {}
    e.max_step_volume_ml = 500.0
    e.max_total_flow = 100.0
    e.current_tube = 1
    e.vol_reactor = 1.98
    e.vol_post_common = 2.0
    e.vol_collection = 0.24
    return e


eng = make_engine(c)
frac = {"enabled": True, "volume": 0.0}
step_ok = {"collect_volume_per_tube": 1.5, "vol_ml": 3.0, "temp": 25.0,
           "flows": {"Group A": 1.0}, "ports": {"Group A": 2},
           "inlet_ports": {"Group A": 2}}
try:
    r = eng._validate_step_inputs(1, step_ok, frac)
    check("tube_vol 1.5 = 상한 통과", r[2] == 1.5)
except SafetyError as e:
    check("tube_vol 1.5 = 상한 통과", False, str(e))

step_over = dict(step_ok, collect_volume_per_tube=2.0)
try:
    eng._validate_step_inputs(1, step_over, frac)
    check("tube_vol 2.0 > 1.5 차단", False, "예외 안 남")
except SafetyError as e:
    check("tube_vol 2.0 > 1.5 차단", "well capacity" in str(e), str(e)[:70])

# 튜브 수 초과: 시작 1, 3.0mL/well 로 60 웰 필요 → 50 튜브 랙 초과
step_many = dict(step_ok, vol_ml=90.0)   # 1.5 mL/well × 60 웰
try:
    eng._validate_step_inputs(1, step_many, frac)
    check("튜브 수 초과 차단 (60 > 50)", False, "예외 안 남")
except SafetyError as e:
    check("튜브 수 초과 차단 (60 > 50)", "collector capacity" in str(e), str(e)[:70])

# 시작 튜브가 뒤쪽이면 더 빨리 걸림
eng.current_tube = 45
try:
    eng._validate_step_inputs(1, dict(step_ok, vol_ml=12.0), frac)   # 8 웰 필요
    check("시작 45 + 8웰 > 50 차단", False, "예외 안 남")
except SafetyError as e:
    check("시작 45 + 8웰 > 50 차단", "collector capacity" in str(e), str(e)[:70])
eng.current_tube = 1

print("== C. 96-well 하위호환 ==")
c96 = make_collector()
check("192 튜브", c96.total_tubes == 192)
base = json.load(open(os.path.join(DATA, "well_coordinates.json"), encoding="utf-8"))
b96 = {(w["plate"], w["well"]): w for w in base["wells"]}
c96.ser.sent.clear()
c96.move_to_tube(13)   # 서펜타인 2행 첫 튜브 = B12
w13 = b96[("A", "B12")]
check("96 tube13 = A_B12 좌표",
      f"G1 X{w13['machine_X']} Y{w13['machine_Y']} F{c96.feedrate_xy}" in gcodes(c96),
      c96.get_well_id(13))
eng96 = make_engine(c96)
try:
    eng96._validate_step_inputs(1, dict(step_ok, collect_volume_per_tube=2.0), frac)
    check("96도 1.5 상한 적용", False, "예외 안 남")
except SafetyError as e:
    check("96도 1.5 상한 적용", "well capacity" in str(e))

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
