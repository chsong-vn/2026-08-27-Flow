"""
error_test.py — 에러 경로 fault injection 검증.
에뮬레이터에 고장을 주입하고, vendored 코드의 예외/복구 경로가 실제로 작동하는지 확인.

 E1 ack 타임아웃 → ParameterTimeoutError
 E2 모터 블로킹 (bad ack 'n' + 에러레지스터 3-x) → 예외 + 레지스터 디코드 + 복구
 E3 GRBL soft limit 알람 → SoftLimitError → emergency_close 복구 → 재사용
 E4 범위 위반 값 → 시리얼 송신 전 검증 차단
 E5 케이블 단선 (SerialException) → DeviceCommunicationError
 E6 stop 이벤트 미해제 함정 → 다음 명령 즉시 정지 (clear 필수성 실증)
 E7 시리얼 노이즈 라인 → ParameterCommError (통신 오염 감지)
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pause
pause.seconds = lambda s: time.sleep(0.01)

from robochem_devices import SyringePump, Sampler, GrblPosition, Logger
from robochem_devices import errors as E
from emulators import FakeSerial, PumpEmulator, GrblEmulator

Logger.console_enabled = False
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def make_pump(emu_cls=PumpEmulator):
    emu = emu_cls()
    pump = SyringePump()
    pump._serial_iface = FakeSerial(emu)
    pump.specific_name = "epump"
    pump.open("VIRT")
    pump.setup(**{"syringe_volume": "1.0mL", "syringe_stepperml": "78049.0",
                  "max_flowrate": 30.0, "valve": True,
                  "reservoir_valve_position": "OFF", "aux-valve": False})
    pump.initialize()
    return pump, emu


# ---------------- E1: ack 타임아웃 ----------------
class SwallowAckPump(PumpEmulator):
    """밸브 ack 'v'를 삼켜버리는 고장 (배선 단선/펌웨어 행 시뮬레이션)."""
    swallow = False
    def _handle(self, line):
        if self.swallow and line.startswith("S3="):
            self.valve_sp = int(line.split("=")[1])
            return                     # ack 미전송
        super()._handle(line)

pump, emu = make_pump(SwallowAckPump)
# 밸브 ack 타임아웃은 짧게 (기본값 확인 대신 강제로 3s → 폴링 0.1s)
pump.parameter_by_name("valve_setpoint").acknowledge_timeout = 1.0
emu.swallow = True
t0 = time.time()
try:
    pump["valve_setpoint"] = "ON"
    check("E1a 타임아웃 예외 발생", False, "예외 미발생!")
except E.ParameterTimeoutError:
    check("E1a ParameterTimeoutError 발생", True, f"{time.time()-t0:.1f}s 대기 후")
except E.DeviceError as ex:
    check("E1a 타임아웃 예외 발생", False, f"다른 예외: {type(ex).__name__}")
emu.swallow = False
pump["valve_setpoint"] = "OFF"        # 고장 해제 후 정상 동작 복귀
check("E1b 타임아웃 후 통신 복구", pump["valve_actual"] == "OFF")

# ---------------- E2: 모터 블로킹 ----------------
class BlockagePump(PumpEmulator):
    """S9 도중 엔코더 블로킹 감지 시뮬레이션: bad ack 'n' + 에러 3-5."""
    blockage = False
    def _run_move(self, ul):
        if self.blockage:
            time.sleep(0.05)           # 조금 움직이다가
            self.volume_ul += ul * 0.2
            self._moving = False
            self.error = (3, 5)        # ERROR_PUMP_MOTOR - blockage
            self.fs.push("n")          # BAD_ACK_PUMPING
            return
        super()._run_move(ul)

pump2, emu2 = make_pump(BlockagePump)
pump2["flowrate"] = 10.0
pump2["valve_setpoint"] = "ON"
pump2["enable"] = "ON"     # 원시 pump 쓰기는 enable 선행 필수 (태스크는 자동 관리)
emu2.blockage = True
raised = None
try:
    pump2["pump"] = 200.0
except E.DeviceError as ex:
    raised = ex
check("E2a 블로킹 시 DeviceError 계열 예외", raised is not None,
      f"type={type(raised).__name__}")
check("E2b 에러레지스터가 원인 식별 (모터 에러)", raised is not None and
      ("otor" in str(raised) or "3-5" in str(raised) or "lock" in str(raised).lower()),
      f"msg='{str(raised)[:80]}'")
emu2.blockage = False
# 블로킹으로 이동이 중단되면 파이썬측 볼륨 부기와 물리 위치가 어긋남
# → 올바른 복구 = 재제로잉으로 기준 재확립 (화학적으로도 막힘 후 필수 절차)
from robochem_devices import FillPump
desync = False
try:
    pump2["pump"] = -50.0
except E.ParameterError:
    desync = True                      # 부기 어긋남을 용량검증이 잡아냄
check("E2c 블로킹 후 부기 desync 감지 (재사용 차단)", desync)
FillPump.run(pump=pump2)               # 재제로잉 (엔드스톱 기준 재확립)
pump2["enable"] = "ON"
pump2["pump"] = 100.0
check("E2d 재제로잉 복구 프로토콜 후 정상 동작",
      abs(pump2["volume"] - (980.0 - 100.0)) < 15.0, f"volume={pump2['volume']:.0f}")

# ---------------- E3: GRBL 알람 → 비상해제 → 재사용 ----------------
class AlarmGrbl(GrblEmulator):
    alarm_next_move = False
    locked = False
    def _handle(self, line):
        if self.alarm_next_move and line.startswith(("G1", "G0")):
            self.alarm_next_move = False
            self.locked = True
            self.fs.push("ALARM: Soft limit")   # 이동 거부 + 알람 잠금
            return
        if self.locked and line.startswith(("G1", "G0")):
            self.fs.push("error: Alarm lock")
            return
        if line and ord(line[0]) == 24:          # soft reset
            self._motion_done.set()
            self.fs.push("Grbl 0.9j ['$' for help]")
            self.fs.push("['$H'|'$X' to unlock]")  # 알람 상태 리셋 → unlock 필요
            return
        if line.startswith("$X"):
            self.locked = False
        super()._handle(line)

gemu = AlarmGrbl()
s = Sampler()
s._serial_iface = FakeSerial(gemu)
s.open("VIRT")
s.setup(**{"needle_length": "51mm", "safe_z": "-30.0mm", "vial_top_z": "-34.5mm"})
s.initialize()
s["feed"] = 3000.0
gemu.alarm_next_move = True
try:
    s["move"] = GrblPosition(x=50.0)
    check("E3a 소프트리밋 알람 → SoftLimitError", False, "예외 미발생!")
except E.SoftLimitError:
    check("E3a 소프트리밋 알람 → SoftLimitError", True)
except E.DeviceError as ex:
    check("E3a 소프트리밋 알람 → SoftLimitError", False, f"다른 예외 {type(ex).__name__}")

s.emergency_close()                    # soft reset → unlock 판별 → close
check("E3b emergency_close 완료 (unlock 경로)", not s.is_open() and not gemu.locked)
s.open("VIRT")                         # 재접속 → 재초기화 → 재사용
s.initialize()
s["feed"] = 3000.0
s["move"] = GrblPosition(x=20.0)
check("E3c 알람 복구 후 정상 이동", s["position"].coords()[0] == 20.0)

# ---------------- E4: 범위 위반 사전 차단 ----------------
pump3, emu3 = make_pump()
n_tx = len(emu3.tx_log)
try:
    pump3["flowrate"] = 999.0          # max 30 초과
    check("E4a 범위위반 예외", False, "예외 미발생!")
except E.ParameterError:
    check("E4a 범위위반 → ParameterError", True)
check("E4b 시리얼 송신 전 차단", len(emu3.tx_log) == n_tx, "송신 0건")
try:
    pump3["valve_setpoint"] = "SIDEWAYS"
    check("E4c 잘못된 밸브값 차단", False)
except (E.ParameterError, ValueError, KeyError):
    check("E4c 잘못된 밸브값 차단", True)

# ---------------- E5: 케이블 단선 ----------------
import serial as pyserial
class DeadSerial(FakeSerial):
    dead = False
    def write(self, data):
        if self.dead:
            raise pyserial.SerialException("device disconnected")
        return super().write(data)

emu5 = PumpEmulator()
pump5 = SyringePump()
pump5._serial_iface = DeadSerial(emu5)
pump5.specific_name = "dpump"
pump5.open("VIRT")
pump5.setup(**{"syringe_volume": "1.0mL", "syringe_stepperml": "78049.0",
               "max_flowrate": 30.0, "valve": True,
               "reservoir_valve_position": "OFF", "aux-valve": False})
pump5.initialize()
pump5._serial_iface.dead = True
try:
    pump5["flowrate"] = 5.0
    check("E5a 단선 → DeviceCommunicationError", False, "예외 미발생!")
except E.DeviceCommunicationError:
    check("E5a 단선 → DeviceCommunicationError", True)
except E.DeviceError as ex:
    check("E5a 단선 → DeviceCommunicationError", False, f"{type(ex).__name__}")
pump5._serial_iface.dead = False

# ---------------- E6: stop 미해제 함정 ----------------
pump6, emu6 = make_pump()
pump6["flowrate"] = 2.0
pump6["valve_setpoint"] = "ON"
pump6["enable"] = "ON"
pump6.stop_parameter("pump")           # set만 하고 해제 안 함
t0 = time.time()
try:
    pump6["pump"] = 500.0              # 500uL@2mL/min → 정상이면 0.3s(스케일)
except E.DeviceError:
    pass
elapsed = time.time() - t0
vol = pump6["volume"]
check("E6a stop 미해제 → 다음 명령 즉시 정지", vol > 950.0 and elapsed < 0.5,
      f"토출 {1000-vol:.0f}uL, {elapsed:.2f}s (의도된 함정 실증)")
pump6.stop_parameter("pump", stop=False)
pump6["pump"] = 100.0
check("E6b clear 후 정상 펌핑", abs(pump6["volume"] - 900.0) < 15.0,
      f"volume={pump6['volume']:.0f}")

# ---------------- E7: 시리얼 노이즈 ----------------
pump7, emu7 = make_pump()
emu7.fs.push("$#!GARBAGE_NOISE")       # EMI/부팅잔여물 시뮬레이션
try:
    _ = pump7["volume"]
    check("E7a 노이즈 감지", False, "무증상 통과 (오염 미감지)")
except E.ParameterCommError:
    check("E7a 노이즈 → ParameterCommError 감지", True)
_ = pump7["volume"]                    # 노이즈 소진 후 정상
check("E7b 노이즈 소진 후 통신 정상", True)

# ---------------- 정리/결과 ----------------
for dev in (pump, pump2, pump3, pump5, pump6, pump7, s):
    try: dev.close()
    except Exception: pass
print()
passed = sum(1 for _, ok, _ in results if ok)
print(f"===== {passed}/{len(results)} PASS =====")
sys.exit(0 if passed == len(results) else 1)
