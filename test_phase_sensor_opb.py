# -*- coding: utf-8 -*-
"""OPB 2ch ADC 위상센서 드라이버 검증 — 벤더 실측표(Photo_Interrupt README) 기반.

페이크 시리얼로 CSV 스트림을 재생해 분류/디바운스/이벤트/wait_edge/단선감지/
설정 임계값/팩토리 매핑을 오프라인 검증. 실제 pyserial/장비 불필요.
"""
import sys
import time
import threading

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from hardware.sensors.phase_sensor_opb import PhaseSensorOPBADC, DEFAULT_THRESHOLDS
from hardware.sensors.phase_sensor_array import PhaseSensorError

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


class FakeStream:
    """UNO CSV 스트림 시뮬 — 큐에 넣은 라인을 readline 마다 하나씩 반환."""

    def __init__(self):
        self._lines = []
        self._lock = threading.Lock()

    def feed(self, *lines):
        with self._lock:
            self._lines.extend(lines)

    def readline(self):
        with self._lock:
            if self._lines:
                return self._lines.pop(0)
        time.sleep(0.005)
        return b""

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def wait_until(cond, timeout=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


print("== 1. 벤더 실측표 분류 (S1 thr=440, S2 thr=717) ==")
fs = FakeStream()
ps = PhaseSensorOPBADC("COM_FAKE", sensors={"collect": 0, "reactor_in": 1},
                       name="OPB-T", debounce_n=2)
ps.connect(serial_override=fs)
check("기본 임계값 = 벤더 실측(440/717)",
      ps.thresholds[0] == 440 and ps.thresholds[1] == 717, str(ps.thresholds))

# README 예시: S1=82(공기), S2=976(물) — 디바운스 2회 위해 2줄
fs.feed(b"82,976\r\n", b"82,976\r\n")
ok = wait_until(lambda: ps._state.get(0) is not None and ps._state.get(1) is not None)
check("스트림 수신·확정", ok)
check("S1 82 → GAS", ps.read_phase("collect") == "GAS", ps.read_phase("collect"))
check("S2 976 → 액체(CLEAR_LIQUID)", ps.read_phase("reactor_in") == "CLEAR_LIQUID")
check("is_liquid 정합", ps.is_liquid("reactor_in") and not ps.is_liquid("collect"))
check("analog = 최근 ADC", ps.analog("collect") == 82 and ps.analog("reactor_in") == 976)

print("== 2. 디바운스 — 1회 스파이크는 무시 ==")
fs.feed(b"801,976\r\n")          # S1 물 스파이크 1회 (debounce_n=2 미달)
time.sleep(0.1)
check("스파이크 1회 → 상태 유지(GAS)", ps.read_phase("collect") == "GAS")

print("== 3. 이벤트/monitor — 액체 경계 push ==")
ps.monitor("collect", "always")
fs.feed(b"801,976\r\n", b"805,976\r\n")   # 2연속 물 → 확정 + 이벤트
ok = wait_until(lambda: ps.read_phase("collect") == "CLEAR_LIQUID")
check("2연속 → 액체 확정", ok)
ev = ps.read_event("collect")
check("이벤트 push = CLEAR_LIQUID", ev == "CLEAR_LIQUID", str(ev))
check("이벤트 소진 후 None", ps.read_event("collect") is None)

print("== 4. wait_edge — 기체 도달 대기 ==")
def _feed_gas():
    time.sleep(0.15)
    fs.feed(b"80,976\r\n", b"79,976\r\n")
threading.Thread(target=_feed_gas, daemon=True).start()
t0 = time.time()
ok = ps.wait_edge("collect", want_gas=True, timeout=3.0)
check("wait_edge 기체 True", ok, f"{time.time()-t0:.2f}s")
check("wait_edge 즉시 RETURN(이미 기체)", ps.wait_edge("collect", want_gas=True, timeout=0.5))

print("== 5. 단선 감지 (stale) ==")
ps.stale_sec = 0.2
time.sleep(0.35)                  # 새 라인 없음
try:
    ps.read_phase("collect")
    check("스트림 두절 → 예외", False, "예외 안 남")
except PhaseSensorError:
    check("스트림 두절 → PhaseSensorError", True)
ps.disconnect()

print("== 6. settings 임계값/논리명 키 오버라이드 ==")
ps2 = PhaseSensorOPBADC("COM_FAKE", sensors={"collect": 0},
                        thresholds={"collect": 300, "1": 600}, name="OPB-C")
check("논리명 키 → 채널 매핑(300)", ps2.thresholds[0] == 300, str(ps2.thresholds))
check("문자 인덱스 키(600)", ps2.thresholds[1] == 600)

print("== 7. Mock 경로 + 팩토리 매핑 ==")
pm = PhaseSensorOPBADC("Mock_Port", sensors={"collect": 0}, name="OPB-M")
pm.connect()
check("Mock 기본 액체", pm.read_phase("collect") == "CLEAR_LIQUID")
pm.monitor("collect", "once")
pm.sim_set_phase("collect", "GAS")
check("Mock 이벤트 GAS", pm.read_event("collect") == "GAS")

from hardware.factory import HardwareFactory
check("factory 라벨 매핑",
      HardwareFactory.get_driver_type("위상센서 (OPB ADC 2ch)") == "PhaseSensorOPBADC",
      HardwareFactory.get_driver_type("위상센서 (OPB ADC 2ch)"))

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
