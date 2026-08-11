"""
Arduino 4CH 릴레이 테스트 도구
사용법: python test_relay.py

명령어:
  1 on / 1 off  — ch1 켜기/끄기
  2 on / 2 off  — ch2 켜기/끄기
  3 on / 3 off  — ch3 켜기/끄기
  4 on / 4 off  — ch4 켜기/끄기
  all on / all off — 전체 켜기/끄기
  q — 종료
"""

import serial
import time
import sys

PORT = "COM7"
BAUD = 9600

# 논리 채널 → 실제 릴레이 채널 매핑 (배선 순서 보정)
CH_MAP = {"1": "4", "2": "3", "3": "2", "4": "1"}

def main():
    print(f"=== 릴레이 테스트 (포트: {PORT}) ===")
    print("연결 중...")

    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except Exception as e:
        print(f"포트 열기 실패: {e}")
        sys.exit(1)

    time.sleep(2.0)  # Arduino 리셋 대기

    # READY 메시지 읽기
    ready = ser.read_all().decode(errors='replace').strip()
    if ready:
        print(f"Arduino: {ready}")

    print()
    print("명령어: [1~4] on/off | all on/off | q 종료")
    print("예: 1 on, 4 off, all on")
    print("-" * 40)

    while True:
        try:
            cmd = input("> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if not cmd:
            continue
        if cmd == "q":
            break

        # 파싱
        parts = cmd.split()
        if len(parts) != 2:
            print("형식: [1-4|all] [on|off]")
            continue

        ch_str, action = parts

        if action == "on":
            pos = "2"  # HIGH = position 2
        elif action == "off":
            pos = "1"  # LOW = position 1
        else:
            print("on 또는 off만 가능")
            continue

        if ch_str == "all":
            # 하위호환 모드: 전체 동시 제어
            msg = f"{pos}\n"
        elif ch_str in ["1", "2", "3", "4"]:
            real_ch = CH_MAP[ch_str]
            msg = f"{real_ch} {pos}\n"
        else:
            print("채널: 1~4 또는 all")
            continue

        ser.reset_input_buffer()
        ser.write(msg.encode())
        time.sleep(0.3)
        resp = ser.read_all().decode(errors='replace').strip()
        print(f"  TX: {msg.strip()} → RX: {resp}")

    # 종료 시 전체 OFF
    print("\n전체 릴레이 OFF 후 종료...")
    ser.write(b"1\n")
    time.sleep(0.3)
    ser.read_all()
    ser.close()
    print("완료")

if __name__ == "__main__":
    main()
