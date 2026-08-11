# -*- coding: utf-8 -*-
"""위상센서 어레이(OCB350) 하드웨어 어댑터 계약 검증 — 하드웨어 불필요.

검증축:
 M Mock 계약: 위상읽기/monitor+이벤트/wait_edge(즉시RETURN·엣지·타임아웃)/오류
 P 실 프로토콜: robochem PhaseSensorArray + FakeSerial(펌웨어 에뮬) 경유
   — Rx/Sx=y 프레임, 이벤트 push "i=phase", 캘리브레이션 S{base+3}
 F factory 매핑
"""
import os, sys, time, threading

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔/파이프 출력 방어
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "robochem_devices", "_reference", "examples"))

import pause
pause.seconds = lambda s: time.sleep(0.01)   # robochem 폴링 가속 (원본 예제 관례)

from hardware.sensors.phase_sensor_array import (
    PhaseSensorArrayHW, MockPhaseSensor, PhaseSensorError)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# ════════════════ M: Mock 계약 ════════════════
ps = MockPhaseSensor(sensors={"collect": 0, "reactor_out": 1})
ps.connect()
check("M1 connect(Mock)", ps.is_connected)
check("M2 기본 위상 = CLEAR_LIQUID", ps.read_phase("collect") == "CLEAR_LIQUID")
check("M3 is_liquid True", ps.is_liquid("collect") is True)
check("M4 read_all 2센서", set(ps.read_all()) == {"collect", "reactor_out"})

# monitor 무장 전 위상 변화 → 이벤트 없음 (실기 의미론)
ps.sim_set_phase("collect", "GAS")
check("M5 monitor 전 이벤트 없음", ps.read_event("collect") is None)
check("M5b 위상은 GAS 반영", ps.read_phase("collect") == "GAS")

# wait_edge: 이미 목표 위상이면 즉시 True (RoboChem RETURN 정책)
check("M6 wait_edge 즉시 RETURN(GAS)", ps.wait_edge("collect", want_gas=True, timeout=1) is True)

# 엣지 대기: 0.15s 후 액체 도착
ps.sim_set_phase("collect", "GAS")  # no-op (이미 GAS)
threading.Timer(0.15, lambda: ps.sim_set_phase("collect", "CLEAR_LIQUID")).start()
t0 = time.time()
ok = ps.wait_edge("collect", want_gas=False, timeout=3)
check("M7 wait_edge 액체 엣지 검출", ok and (time.time() - t0) < 1.0,
      f"({time.time()-t0:.2f}s)")

# 타임아웃 → False (예외 아님 — 하이브리드 폴백 분기)
ok = ps.wait_edge("collect", want_gas=True, timeout=0.3)
check("M8 wait_edge 타임아웃 False", ok is False)

# OPAQUE_LIQUID 도 액체로 분류
ps.sim_set_phase("reactor_out", "OPAQUE_LIQUID")
check("M9 OPAQUE=액체", ps.is_liquid("reactor_out") is True)

# 미등록 센서 / 단선(ERROR) → 시끄러운 예외
try:
    ps.read_phase("nope")
    check("M10 미등록 센서 예외", False)
except PhaseSensorError:
    check("M10 미등록 센서 예외", True)
ps.sim_set_phase("collect", 0)
try:
    ps.read_phase("collect")
    check("M11 단선(ERROR) 예외", False)
except PhaseSensorError:
    check("M11 단선(ERROR) 예외", True)
ps.calibrate("reactor_out")   # no-crash
ps.disconnect()
check("M12 disconnect", ps.is_connected is False)

# ════════════════ P: 실 프로토콜 (FakeSerial + 펌웨어 에뮬) ════════════════
from emulators import FakeSerial, BaseEmulator
import re


class PhaseFwEmulator(BaseEmulator):
    """Phase_Sensor_Array.ino 재현: R1 id/R2 개수/R4 에러/센서 base=10+10i
    (+0 phase, +1 analog, +2 monitor RW, +3 calibrate) + 위상변화 push 'i=phase'."""

    def __init__(self):
        super().__init__()
        self.phases = {0: 1, 1: 3}
        self.monitors = {0: 0, 1: 0}
        self.calibrated = []

    def _handle(self, line):
        m = re.match(r"^([SR])(\d+)(?:=(.*))?$", line)
        if not m:
            return
        cmd, var, val = m.group(1), int(m.group(2)), m.group(3)
        if cmd == "R":
            if var == 1:
                self.fs.push("PHASE_ARRAY_0")
            elif var == 2:
                self.fs.push("4")
            elif var == 4:
                self.fs.push("0-0")
            elif var >= 10:
                idx, sub = (var - 10) // 10, (var - 10) % 10
                if sub == 0:
                    self.fs.push(str(self.phases.get(idx, 0)))
                elif sub == 1:
                    self.fs.push("512")
                elif sub == 2:
                    self.fs.push(str(self.monitors.get(idx, 0)))
        else:
            if var >= 10:
                idx, sub = (var - 10) // 10, (var - 10) % 10
                if sub == 2:
                    self.monitors[idx] = int(float(val))
                elif sub == 3:
                    self.calibrated.append(idx)

    def fire_edge(self, idx, phase):
        """펌웨어의 has_changed() push 재현."""
        self.phases[idx] = phase
        if self.monitors.get(idx, 0) != 0:
            self.fs.push(f"{idx}={phase}")
            if self.monitors[idx] == 1:
                self.monitors[idx] = 0


emu = PhaseFwEmulator()
hw = PhaseSensorArrayHW("VIRT", sensors={"collect": 0}, name="P-TEST")
hw._serial_iface_override = FakeSerial(emu)
hw.connect()
check("P1 실 프로토콜 connect", hw.is_connected)
check("P2 위상 읽기(R10)", hw.read_phase("collect") == "CLEAR_LIQUID")
check("P3 analog 읽기(R11)", hw.analog("collect") == 512)
hw.calibrate("collect")
check("P4 캘리브레이션 명령(S13)", emu.calibrated == [0])

# monitor + 이벤트 push 경로
hw.monitor("collect", "once")
check("P5 monitor 무장(S12=1)", emu.monitors[0] == 1)
emu.fire_edge(0, 3)   # 액체→기체 push "0=3"
ev = None
for _ in range(20):
    ev = hw.read_event("collect")
    if ev:
        break
    time.sleep(0.02)
check("P6 이벤트 push 수신(GAS)", ev == "GAS", f"(ev={ev})")

# wait_edge 실 프로토콜: 0.1s 후 기체→액체
emu.fire_edge(0, 3)
threading.Timer(0.1, lambda: emu.fire_edge(0, 1)).start()
threading.Timer(0.05, lambda: hw.monitor is not None).start()
ok = hw.wait_edge("collect", want_gas=False, timeout=3)
check("P7 wait_edge 실 프로토콜", ok is True)
hw.disconnect()
check("P8 disconnect", hw.is_connected is False)

# ════════════════ F: factory 매핑 ════════════════
from hardware.factory import HardwareFactory
check("F1 factory OCB350", HardwareFactory.get_driver_type("위상센서 어레이 (OCB350)") == "PhaseSensorArrayHW")
check("F2 factory 가상", HardwareFactory.get_driver_type("가상 위상센서") == "MockPhaseSensor")
check("F3 드라이버 목록 포함", "위상센서 어레이 (OCB350)" in HardwareFactory.get_available_drivers()
      if hasattr(HardwareFactory, "get_available_drivers") else True)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
