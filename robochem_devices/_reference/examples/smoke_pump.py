"""
시린지 펌프 단독 브링업 테스트.
주의: initialize()는 실제 제로잉(시린지 완전 배출) 구동 발생. 시린지/배관 상태 확인 후 실행.
사용: python smoke_pump.py COM7   (리눅스: /dev/ttyUSB0)
"""
import sys
import time
from threading import Thread
from robochem_devices import SyringePump, PumpVolume, Logger

port = sys.argv[1] if len(sys.argv) > 1 else "COM7"

pump = SyringePump()
pump.specific_name = "test_pump"
pump.open(port)                      # UNO 리셋 2초 대기 내장
print("ID:", pump["ID"])             # custom ID 확인 (S1=이름, S14= 로 저장 가능)

pump.setup(**{
    "syringe_volume": "1.0mL",
    "syringe_stepperml": "78049.0",  # 실측 캘리브레이션 값으로 교체할 것
    "max_flowrate": 6.0,
    "valve": True,                   # 메인 스위치밸브 장착 시
    "reservoir_valve_position": "OFF",  # C=시린지, ON=시스템, OFF=저장용기 배관 규약
    "aux-valve": False,              # 옵션명에 하이픈 → dict 언패킹 필수
})
pump.initialize()                    # 제로잉 (EMPTY 방향, 엔드스톱까지)

print("잔량:", pump["volume"], "uL")

# 1) 단순 흡입/토출 100 uL
pump["flowrate"] = 1.0
pump["valve_setpoint"] = "OFF"       # reservoir에서
pump["pump"] = -100.0                # 흡입 (블로킹, 완료 ack 'k' 대기)
print("흡입 후 잔량:", pump["volume"])
pump["valve_setpoint"] = "ON"
pump["pump"] = 100.0                 # 토출
print("토출 후 잔량:", pump["volume"])

# 2) 비동기 정지 테스트: 큰 볼륨 걸고 2초 뒤 stop
def stopper():
    time.sleep(2.0)
    pump.stop_parameter("pump")      # ack 대기 중 S21= 주입됨
    print(">>> stop 전송됨")

pump["valve_setpoint"] = "OFF"
Thread(target=stopper).start()
try:
    pump["pump"] = -800.0
finally:
    pump.stop_parameter("pump", stop=False)  # stop 이벤트 해제 필수 (재사용 위해)
print("정지 후 잔량:", pump["volume"])

# 3) 리필 루프 태스크 (요구량 > 시린지 용량)
moved = PumpVolume.run(pump=pump, volume=2500.0, flowrate=3.0, allow_refill=True)
print("PumpVolume 실토출:", moved, "uL")

pump.close()
