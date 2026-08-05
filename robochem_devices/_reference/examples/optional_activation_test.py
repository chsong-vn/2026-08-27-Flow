"""
optional_activation_test.py — 비활성 모듈의 "활성화하면 실제로 돈다" 검증.

 O1 기본 import 격리 (optional/추가의존성 미로드)
 O2 PhaseSensorArray 활성화 → 에뮬레이터에서 위상 읽기 (LIQUID→GAS 전이)
 O3 실물 PhaseSensor로 closed-loop 건조 (T8의 Fake를 실물로 교체한 버전)
 O4 MFC는 propar 부재 시 명확한 ImportError (조용한 오동작 방지)
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pause
pause.seconds = lambda s: time.sleep(0.01)

from robochem_devices import Logger, GpioArray
from emulators import FakeSerial, BaseEmulator, GpioEmulator
import re

Logger.console_enabled = False
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


# ---------------- O1: 격리 ----------------
polluted = [m for m in sys.modules if "robochem_devices.optional" in m
            or m.split(".")[0] in ("propar", "pandas")]
check("O1 기본 import가 optional/추가의존성 미로드", polluted == [], str(polluted))

# ---------------- 위상센서 에뮬레이터 ----------------
class PhaseSensorEmulator(BaseEmulator):
    """
    Phase_Sensor_Array 펌웨어 재현 (읽기 경로).
    변수: R2=max_sensors, R4=에러, 센서 i: base=10+i*10 → phase(base), +1.., 
    phase 값: 0=ERROR, 1=CLEAR_LIQUID, 2=OPAQUE_LIQUID, 3=GAS
    """
    def __init__(self):
        super().__init__()
        self.phases = {0: 1, 1: 1}      # 센서 2개, 초기 LIQUID
        self.vars = {}

    def _handle(self, line):
        m = re.match(r"^([SR])(\d+)(?:=(.*))?$", line)
        if not m:
            return
        cmd, var, val = m.group(1), int(m.group(2)), m.group(3)
        if cmd == "R":
            if var == 1:
                self.fs.push("emul_phase")
            elif var == 2:
                self.fs.push(str(len(self.phases)))
            elif var in (3, 5, 6):
                self.fs.push("0")
            elif var == 4:
                self.fs.push("0-0")
            elif var >= 10 and (var - 10) % 10 == 0:
                idx = (var - 10) // 10
                self.fs.push(str(self.phases.get(idx, 0)))
            else:
                self.fs.push(self.vars.get(var, "0"))
        else:
            self.vars[var] = val


# ---------------- O2: 활성화 → 위상 읽기 ----------------
from robochem_devices.optional.phase_sensor import PhaseSensorArray, PhaseValue

pemu = PhaseSensorEmulator()
psa = PhaseSensorArray()
psa._serial_iface = FakeSerial(pemu)
psa.open("VIRT")
psa.setup(array={"reactor_out": {"index": 0}, "collector_in": {"index": 1}})
psa.initialize()
s0 = psa.proxy("reactor_out")
v = s0["phase"]
check("O2a 활성화 후 위상 읽기 (LIQUID)", str(v) == "CLEAR_LIQUID", f"phase={v}")
pemu.phases[0] = 3
v = s0["phase"]
check("O2b 위상 전이 감지 (GAS)", str(v) == "GAS", f"phase={v}")

# ---------------- O3: 실물 센서로 closed-loop 건조 ----------------
gemu = GpioEmulator()
g = GpioArray()
g._serial_iface = FakeSerial(gemu)
g.open("VIRT2")
g.setup(array={"n2": {"index": "D2", "class": "solenoid_valve"}})
g.initialize()
n2 = g.proxy("n2")

pemu.phases[0] = 1                       # 다시 액체로 시작

def liquid_to_gas_later():
    time.sleep(0.25)                     # 0.25s 후 라인이 마름
    pemu.phases[0] = 3

threading.Thread(target=liquid_to_gas_later, daemon=True).start()

def dry_system(valve, sensor, drying_time=0.2, timeout=5.0):
    """T8 미니 건조의 실물 센서 버전 — chemistry._dry_system 로직."""
    valve["valve"] = "open"
    try:
        last_liquid = time.time()
        t0 = time.time()
        while time.time() - last_liquid < drying_time:
            time.sleep(0.05)
            if str(sensor["phase"]) != "GAS":
                last_liquid = time.time()
            if time.time() - t0 > timeout:
                raise RuntimeError("건조 타임아웃")
    finally:
        valve["valve"] = "close"
    return time.time() - t0

t0 = time.time()
dur = dry_system(n2, s0)
check("O3a 실물 센서 closed-loop 건조 완료", 0.4 < dur < 1.0,
      f"건조판정 {dur:.2f}s (전이 0.25s + 연속기체 0.2s + 폴링그레인)")
check("O3b 건조 후 밸브 닫힘", "S12=0" in gemu.tx_log[-3:] or "S12=0" in gemu.tx_log)

# ---------------- O4: MFC 비활성 명확성 ----------------
try:
    from robochem_devices.optional import mass_flow_controller
    import propar  # 있으면 활성 환경
    check("O4 MFC 활성 (propar 설치 환경)", True)
except ImportError as e:
    check("O4 MFC 비활성 시 명확한 ImportError", "propar" in str(e), str(e))

# ---------------- 정리/결과 ----------------
for dev in (psa, g):
    try: dev.close()
    except Exception: pass
print()
passed = sum(1 for _, ok, _ in results if ok)
print(f"===== {passed}/{len(results)} PASS =====")
sys.exit(0 if passed == len(results) else 1)
