"""
3-Way 밸브 릴레이 테스트 도구 (4채널: D2, D3, D4, D5)

사용법: python test_3way_valve.py

핀 매핑:
  CH1 → D2 (릴레이1)
  CH2 → D3 (릴레이2)
  CH3 → D4 (릴레이3)
  CH4 → D5 (릴레이4)

3-Way 밸브 상태:
  Position A (LOW)  = 포트 A (기본)
  Position B (HIGH) = 포트 B

명령어:
  1 a / 1 b       — CH1 포트 A/B 전환
  2 a / 2 b       — CH2 포트 A/B 전환
  3 a / 3 b       — CH3 포트 A/B 전환
  4 a / 4 b       — CH4 포트 A/B 전환
  all a / all b   — 전체 포트 A/B 전환
  seq             — 순차 테스트 (각 채널 B→A 순서로 1초 간격)
  toggle <ch>     — 해당 채널 현재 상태 반전
  status          — 전체 채널 상태 표시
  q               — 종료
"""

import serial
import time
import sys

PORT = "COM7"
BAUD = 9600

# 채널별 현재 상태 추적 (a=pos1, b=pos2)
ch_state = {1: "a", 2: "a", 3: "a", 4: "a"}

PIN_MAP = {1: "D2", 2: "D3", 3: "D4", 4: "D5"}


def send_cmd(ser, msg):
    """명령 전송 후 응답 반환"""
    ser.reset_input_buffer()
    ser.write(f"{msg}\n".encode())
    time.sleep(0.3)
    resp = ser.read_all().decode(errors="replace").strip()
    return resp


def set_valve(ser, ch, port):
    """밸브 설정: ch=1~4, port='a' or 'b'"""
    pos = "1" if port == "a" else "2"
    msg = f"{ch} {pos}"
    resp = send_cmd(ser, msg)
    ch_state[ch] = port
    print(f"  CH{ch}({PIN_MAP[ch]}) → 포트 {port.upper()}  |  TX: {msg}  RX: {resp}")


def set_all(ser, port):
    """전체 밸브 설정"""
    pos = "1" if port == "a" else "2"
    resp = send_cmd(ser, pos)
    for ch in ch_state:
        ch_state[ch] = port
    print(f"  ALL → 포트 {port.upper()}  |  TX: {pos}  RX: {resp}")


def show_status():
    """현재 상태 표시"""
    print("\n  ┌──────┬──────┬────────┐")
    print("  │  CH  │  핀  │  상태  │")
    print("  ├──────┼──────┼────────┤")
    for ch in range(1, 5):
        port = ch_state[ch].upper()
        pin = PIN_MAP[ch]
        relay = "LOW " if port == "A" else "HIGH"
        print(f"  │  {ch}   │  {pin}  │  {port} ({relay}) │")
    print("  └──────┴──────┴────────┘\n")


def sequential_test(ser):
    """순차 테스트: 각 채널을 하나씩 B로 전환 후 다시 A로 복귀"""
    print("\n  === 순차 테스트 시작 ===")
    delay = 1.0

    # 전체 A로 초기화
    set_all(ser, "a")
    time.sleep(0.5)

    for ch in range(1, 5):
        print(f"\n  -- CH{ch} ({PIN_MAP[ch]}) 테스트 --")
        set_valve(ser, ch, "b")
        time.sleep(delay)
        set_valve(ser, ch, "a")
        time.sleep(0.5)

    print("\n  === 순차 테스트 완료 ===\n")


def main():
    print(f"=== 3-Way 밸브 릴레이 테스트 (포트: {PORT}) ===")
    print(f"핀 매핑: CH1→D2, CH2→D3, CH3→D4, CH4→D5")
    print("연결 중...")

    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except Exception as e:
        print(f"포트 열기 실패: {e}")
        sys.exit(1)

    time.sleep(2.0)  # Arduino 리셋 대기

    ready = ser.read_all().decode(errors="replace").strip()
    if ready:
        print(f"Arduino: {ready}")

    print()
    print("명령어:")
    print("  [1-4] a/b     — 개별 채널 포트 전환")
    print("  all a/b       — 전체 채널 포트 전환")
    print("  seq           — 순차 테스트 (각 채널 B→A)")
    print("  toggle [1-4]  — 채널 상태 반전")
    print("  status        — 전체 상태 표시")
    print("  q             — 종료")
    print("-" * 45)

    show_status()

    while True:
        try:
            cmd = input("> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if not cmd:
            continue
        if cmd == "q":
            break
        if cmd == "seq":
            sequential_test(ser)
            continue
        if cmd == "status":
            show_status()
            continue

        parts = cmd.split()

        # toggle 명령
        if parts[0] == "toggle" and len(parts) == 2 and parts[1] in ["1", "2", "3", "4"]:
            ch = int(parts[1])
            new_port = "b" if ch_state[ch] == "a" else "a"
            set_valve(ser, ch, new_port)
            continue

        # 일반 명령: [ch|all] [a|b]
        if len(parts) != 2:
            print("형식: [1-4|all] [a|b] / seq / toggle [1-4] / status / q")
            continue

        ch_str, port = parts

        if port not in ("a", "b"):
            print("포트는 a 또는 b만 가능")
            continue

        if ch_str == "all":
            set_all(ser, port)
        elif ch_str in ["1", "2", "3", "4"]:
            set_valve(ser, int(ch_str), port)
        else:
            print("채널: 1~4 또는 all")
            continue

    # 종료 시 전체 포트 A(LOW)로 복귀
    print("\n전체 밸브 포트 A(LOW)로 복귀 후 종료...")
    send_cmd(ser, "1")
    ser.close()
    print("완료")


if __name__ == "__main__":
    main()
