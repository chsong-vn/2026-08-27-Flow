"""
Runze SV07 밸브 RS-485 주소 스캔
모든 주소(0x00~0x0F)로 통신 시도하여 응답하는 밸브 찾기
"""

import serial
import time

COM_PORT = "COM9"
BAUDRATES = [9600, 19200, 38400, 57600, 115200]  # 시도할 baud rate 목록

STX = 0xCC
ETX = 0xDD

def build_query_position(addr):
    """현재 위치 조회 명령"""
    frame = [STX, addr, 0x3E, 0x00, 0x00, ETX]
    checksum = sum(frame) & 0xFFFF
    return bytearray(frame + [checksum & 0xFF, (checksum >> 8) & 0xFF])

def scan():
    print(f"COM 포트: {COM_PORT}")
    print("=" * 50)

    for baud in BAUDRATES:
        print(f"\n[Baud rate: {baud}]")
        try:
            ser = serial.Serial(
                port=COM_PORT,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.3
            )
            time.sleep(0.3)

            found = False
            for addr in range(0x10):  # 0x00 ~ 0x0F
                cmd = build_query_position(addr)
                ser.reset_input_buffer()
                ser.write(cmd)
                time.sleep(0.1)
                response = ser.read(8)

                if len(response) > 0:
                    print(f"  주소 0x{addr:02X}: 응답 {response.hex()} (포트위치: {response[3] if len(response) > 3 else '?'})")
                    found = True

            if not found:
                print("  응답 없음")

            ser.close()

        except Exception as e:
            print(f"  오류: {e}")

def scan_ascii():
    """ASCII 프로토콜 테스트"""
    print("\n" + "=" * 50)
    print("[ASCII 프로토콜 테스트]")
    print("=" * 50)

    for baud in [9600, 19200]:
        print(f"\n[Baud: {baud}]")
        try:
            ser = serial.Serial(COM_PORT, baud, timeout=0.5)
            time.sleep(0.3)

            # 다양한 ASCII 명령 시도
            commands = [
                "/1?R\r",      # 일반적인 ASCII 프로토콜
                "/1ZR\r",      # 위치 조회
                "1H\r",        # 홈
                "*IDN?\r",     # 장비 ID
                "?R\r",
            ]

            for cmd in commands:
                ser.reset_input_buffer()
                ser.write(cmd.encode())
                time.sleep(0.2)
                resp = ser.read(100)
                if resp:
                    print(f"  '{cmd.strip()}' -> {resp}")

            ser.close()
        except Exception as e:
            print(f"  오류: {e}")

if __name__ == "__main__":
    scan()
    scan_ascii()
