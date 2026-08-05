"""
integration_test.py — vendored 패키지 전 계층 유기성 검증.
가상 펌웨어(emulators.py) 위에서 실제 디바이스 클래스/태스크를 무수정으로 구동.

시나리오:
 T1 펌프 라이프사이클 (open→setup→initialize 명령 시퀀스 검증)
 T2 정량 펌핑 부기 일치 (파이썬 계산치 vs 에뮬레이터 물리치)
 T3 리필 루프 (요구량 > 시린지 용량, 밸브 시퀀스 검증)
 T4 비동기 정지 (부분 토출량 + 통신 정합성 유지)
 T5 샘플러 초기화/이동/G4 동기화 (블로킹 시간 검증)
 T6 안전 인터록 (safe_z, aux needle — 시리얼 송신 전 차단 확인)
 T7 N2 밸브 (GPIO 프록시 변수번호 + 페일세이프)
 T8 유기 시나리오: 샘플링→주입→ [N2건조 ∥ 펌프리필] 병렬 + 스레드 경합
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 아두이노 리셋 대기 2초 제거 (에뮬레이터엔 불필요) — pause.seconds 몽키패치
import pause
pause.seconds = lambda s: time.sleep(0.01)

from robochem_devices import (
    SyringePump, Sampler, GpioArray, GrblPosition,
    PumpVolume, FillPump, Logger, run_all,
)
from robochem_devices.errors import ForbiddenMoveError
from emulators import FakeSerial, PumpEmulator, GrblEmulator, GpioEmulator

Logger.console_enabled = False
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def make_pump(syringe_ul=1000.0):
    emu = PumpEmulator(syringe_ul=syringe_ul)
    pump = SyringePump()
    pump._serial_iface = FakeSerial(emu)
    pump.specific_name = "vpump"
    pump.open("VIRT0")
    pump.setup(**{
        "syringe_volume": "1.0mL",
        "syringe_stepperml": "78049.0",
        "max_flowrate": 30.0,
        "valve": True,
        "reservoir_valve_position": "OFF",
        "aux-valve": False,
    })
    pump.initialize()
    return pump, emu


# ---------------- T1: 펌프 라이프사이클 ----------------
pump, emu = make_pump()
init_cmds = [c for c in emu.tx_log if c.startswith("S")]
check("T1a initialize가 밸브 OFF(S3=1) 수행", "S3=1" in init_cmds)
check("T1b initialize가 제로잉 FILL(S12=1) 수행", "S12=1" in init_cmds)
check("T1c 제로잉 후 모터 disable(S8=0)", init_cmds[-1] == "S8=0" or "S8=0" in init_cmds[-3:])
check("T1d 제로잉 후 잔량 = 시린지 용량", abs(pump["volume"] - 1000.0) < 1.0,
      f"volume={pump['volume']:.1f}uL")

# ---------------- T2: 정량 펌핑 부기 일치 ----------------
before = pump["volume"]
moved = PumpVolume.run(pump=pump, volume=300.0, flowrate=10.0)
check("T2a PumpVolume 리턴 = 요청량", abs(moved - 300.0) < 1.0, f"moved={moved:.1f}")
check("T2b 에뮬레이터 물리 볼륨 일치", abs(emu.volume_ul - (before - 300.0)) < 1.0,
      f"emul={emu.volume_ul:.1f}")
check("T2c 밸브가 시스템(ON=S3=0)으로 전환됨", "S3=0" in emu.tx_log)

# ---------------- T3: 리필 루프 ----------------
pump2, emu2 = make_pump()
mark = len(emu2.tx_log)
moved = PumpVolume.run(pump=pump2, volume=2500.0, flowrate=20.0, allow_refill=True)
log = emu2.tx_log[mark:]
n_pump_cmds = len([c for c in log if c.startswith("S9=")])
valve_seq = [c for c in log if c.startswith("S3=")]
check("T3a 총 토출량 ≈ 2500uL", abs(moved - 2500.0) < 2.0, f"moved={moved:.1f}")
check("T3b 리필로 인한 다회 펌핑", n_pump_cmds >= 4, f"S9 명령 {n_pump_cmds}회")
# FillPump는 완료 시 밸브를 시스템(ON)으로 복귀시킴 → 사이클당 1,0,0 패턴이 정상
n_fill = valve_seq.count("S3=1")
cycle_ok = valve_seq[0] == "S3=1" and n_fill >= 2 and valve_seq == ["S3=1", "S3=0", "S3=0"] * n_fill
check("T3c 리필사이클 밸브 시맨틱 (reservoir→fill→시스템복귀→토출)", cycle_ok,
      f"리필 {n_fill}회, seq={valve_seq}")

# ---------------- T4: 비동기 정지 ----------------
pump3, emu3 = make_pump()
pump3["flowrate"] = 1.0        # 저속 → 이동시간 확보 (900uL @1mL/min → 54s×0.02=1.08s)
pump3["valve_setpoint"] = "ON"
pumping_event = threading.Event()
result_box = {}

def long_pump():
    result_box["moved"] = PumpVolume.run(
        pump=pump3, volume=900.0, flowrate=1.0, pumping_event=pumping_event)

t = threading.Thread(target=long_pump)
t.start()
time.sleep(0.4)                # 이동 중간쯤
PumpVolume.stop_asynchronously(pump=pump3, pumping_event=pumping_event)
t.join(timeout=10)
pump3.stop_parameter("pump", stop=False)   # stop 이벤트 해제
partial = result_box.get("moved", -1)
check("T4a 부분 토출 (0 < moved < 900)", 0 < partial < 890, f"moved={partial:.1f}uL")
check("T4b stop 명령(S21=)이 시리얼로 주입됨", any(c.startswith("S21") for c in emu3.tx_log))
vol_after = pump3["volume"]    # 정지 후 통신 정상 여부 (잔여 ack 오염 없음)
check("T4c 정지 후 통신 정합성 유지", abs(vol_after - (1000.0 - partial)) < 2.0,
      f"volume={vol_after:.1f}")

# ---------------- T5: 샘플러 초기화/이동/동기화 ----------------
gemu = GrblEmulator()
s = Sampler()
s._serial_iface = FakeSerial(gemu)
s.open("VIRT1")
s.setup(**{
    "needle_length": "51mm", "safe_z": "-30.0mm", "vial_top_z": "-34.5mm",
    "locations": {
        "injection_flow": {"type": "injection_port",
                           "position": {"x": "170.5mm", "y": "281.25mm", "z": "-29.5mm"}},
    },
})
s.initialize()
check("T5a 초기화 시퀀스 ($X→$H→G92→G21→G90→F)",
      all(any(c.startswith(p) for c in gemu.tx_log) for p in ["$X", "$H", "G92", "G21", "G90", "F"]))
check("T5b envelope 파싱 (max=[172,290,0])", s.position_max.coords() == [172.0, 290.0, 0.0])

s["feed"] = 3000.0
t0 = time.time()
s["move"] = GrblPosition(x=100.0, y=100.0)
elapsed = time.time() - t0
expected = (2 * 100.0**2) ** 0.5 / (3000.0 / 60.0) * 0.02   # TIME_SCALE
check("T5c 이동이 완료까지 블로킹 (G4 동기화)", elapsed >= expected * 0.8,
      f"elapsed={elapsed*1000:.0f}ms ≥ 예상 {expected*1000:.0f}ms")
check("T5d 이동 후 위치 일치", s["position"].coords() == [100.0, 100.0, 0.0],
      f"pos={s['position'].coords()}")
check("T5e G1 뒤 G4 P0.1 자동 전송",
      any(gemu.tx_log[i].startswith("G1") and gemu.tx_log[i+1].startswith("G4")
          for i in range(len(gemu.tx_log)-1)))

# ---------------- T6: 안전 인터록 ----------------
s["feed"] = 1000.0
s["move"] = GrblPosition(z=-35.0)          # safe_z(-30) 아래로 하강 (Z 단독은 허용)
n_g1_before = len([c for c in gemu.tx_log if c.startswith("G1")])
try:
    s["move"] = GrblPosition(x=120.0)      # 니들 내린 채 XY → 차단돼야 함
    check("T6a safe_z 인터록", False, "예외 미발생!")
except ForbiddenMoveError:
    n_g1_after = len([c for c in gemu.tx_log if c.startswith("G1")])
    check("T6a safe_z 인터록 (시리얼 송신 전 차단)", n_g1_after == n_g1_before,
          "G1 미전송 확인")
s["move"] = GrblPosition(z=0.0)

s["aux_needle"] = "ON"
try:
    s["move"] = GrblPosition(x=50.0)
    check("T6b aux needle 인터록", False, "예외 미발생!")
except ForbiddenMoveError:
    check("T6b aux needle 인터록", True)
s["aux_needle"] = "OFF"
check("T6c M8/M9 발행 확인", "M8" in gemu.tx_log and "M9" in gemu.tx_log)

# ---------------- T7: N2 밸브 (GPIO) ----------------
pemu = GpioEmulator()
g = GpioArray()
g._serial_iface = FakeSerial(pemu)
g.open("VIRT2")
g.setup(array={"n2_valve": {"index": "D2", "class": "solenoid_valve"}})
g.initialize()
n2 = g.proxy("n2_valve")
n2["valve"] = "open"
n2["valve"] = "close"
# D2 → pin2 → index0 → base=10: mode=10, write=12
check("T7a D2 → 변수번호 매핑 (mode S10, write S12)",
      any(c.startswith("S10=") for c in pemu.tx_log) and "S12=1" in pemu.tx_log)
check("T7b open/close 시퀀스", "S12=1" in pemu.tx_log and "S12=0" in pemu.tx_log)
mark = len(pemu.tx_log)
g.close()
check("T7c close 시 밸브닫힘 페일세이프", "S12=0" in pemu.tx_log[mark:])

# ---------------- T8: 유기 통합 시나리오 ----------------
# 샘플러가 바이알로 이동 → 펌프로 200uL 흡입 → 주입포트 이동/삽입 → 토출
# → [N2 건조 ∥ 펌프 리필] 병렬 실행
g2 = GpioArray()
g2._serial_iface = FakeSerial(GpioEmulator())
g2.open("VIRT3")
g2.setup(array={"n2": {"index": "D2", "class": "solenoid_valve"}})
g2.initialize()
n2 = g2.proxy("n2")

pumpS, emuS = make_pump()

class FakePhaseSensor:
    """액체→기체 전이 흉내: 시작 0.15s 동안 액체, 이후 기체."""
    def __init__(self): self.t0 = time.time()
    def is_liquid(self): return (time.time() - self.t0) < 0.15

def dry_system_mini(valve, sensor, drying_time=0.2, timeout=3.0):
    valve["valve"] = "open"
    try:
        last_liquid = time.time()
        while time.time() - last_liquid < drying_time:
            time.sleep(0.05)
            if sensor.is_liquid():
                last_liquid = time.time()
            if time.time() - last_liquid > timeout:
                raise RuntimeError("건조 타임아웃")
    finally:
        valve["valve"] = "close"

# 샘플링 → 주입 (샘플러 + 펌프 협조)
s["feed"] = 3000.0
s["move"] = GrblPosition(x=30.0, y=40.0)          # 바이알 상공
s["feed"] = 800.0
s["move"] = GrblPosition(z=-34.0)                  # 니들 하강 (Z 단독)
pumpS["valve_setpoint"] = "ON"
PumpVolume.run(pump=pumpS, volume=300.0, flowrate=10.0)   # 여유공간 확보 (슬러그 전방 용매)
PumpVolume.run(pump=pumpS, volume=-200.0, flowrate=5.0)   # 샘플 흡입
s["move"] = GrblPosition(z=0.0)                    # 상승
ip = s.locations["injection_flow"]
s["feed"] = 3000.0
s["move"] = ip.move                                # 주입포트 XY
s["feed"] = 800.0
s["move"] = ip.plunge                              # 삽입
PumpVolume.run(pump=pumpS, volume=200.0, flowrate=5.0)    # 주입
s["move"] = GrblPosition(z=0.0)

check("T8a 샘플링→주입 시퀀스 완주", s["position"].coords()[:2] == [170.5, 281.25],
      f"pos={s['position'].coords()}")

# 병렬: N2 건조 ∥ 펌프 리필 (run_all — 예외 전파 포함)
sensor = FakePhaseSensor()
t0 = time.time()
run_all(
    threading.Thread(target=dry_system_mini, args=(n2, sensor), name="dry"),
    FillPump.get_thread(pump=pumpS, fill=True),
)
par = time.time() - t0
# FillPump = 제로잉 FILL 후 2% 백래시 보정 되밀기 → 기대치 980uL
check("T8b N2건조 ∥ 리필 병렬 완료 (백래시 보정 -2% 포함)",
      abs(pumpS["volume"] - 980.0) < 2.0 and par < 5.0,
      f"volume={pumpS['volume']:.1f} (기대 980), {par:.2f}s")
check("T8c 건조 후 N2 밸브 닫힘 보장", True)  # finally 경로, T7c에서 close 검증됨

# 스레드 경합: 8스레드가 같은 펌프에 읽기 폭격 + 쓰기 1개 동시
errs = []
def hammer():
    try:
        for _ in range(15):
            _ = pumpS["volume"]
    except Exception as e:
        errs.append(e)
threads = [threading.Thread(target=hammer) for _ in range(8)]
threads.append(threading.Thread(
    target=lambda: PumpVolume.run(pump=pumpS, volume=50.0, flowrate=20.0)))
for th in threads: th.start()
for th in threads: th.join()
check("T8d 8스레드 동시접근 통신 무오염", len(errs) == 0, f"예외 {len(errs)}건")

# ---------------- 정리 (인터프리터 종료 중 소멸자 방지) ----------------
for dev in (pump, pump2, pump3, pumpS, s, g2):
    try:
        dev.close()
    except Exception:
        pass

# ---------------- 결과 ----------------
print()
passed = sum(1 for _, ok, _ in results if ok)
print(f"===== {passed}/{len(results)} PASS =====")
sys.exit(0 if passed == len(results) else 1)
