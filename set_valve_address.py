"""
Runze SV07 밸브 RS-485 주소 설정 스크립트

현재 하드웨어 주소 (재설정 불필요 - 분리 불가):
  12-way1: 0x00  (config address=0)
  12-way2: 0x01  (config address=1)
  12-way3: 0x03  (config address=3)

사용법:
1. 아래 CURRENT_ADDRESS / NEW_ADDRESS 설정
2. 해당 밸브 1대만 RS-485에 연결
3. 스크립트 실행
4. 밸브 전원 재시작
5. 다음 밸브로 반복

주의: 한 번에 1대만 연결해야 함!
"""

import serial
import time

# ============ 설정 (밸브마다 변경) ============
COM_PORT = "COM9"
BAUDRATE = 9600

CURRENT_ADDRESS = 0x00   # 현재 주소
NEW_ADDRESS = 0x00       # 새 주소 (같으면 검증만 수행)
# 현재 매핑: 밸브1=0x00, 밸브2=0x01, 밸브3=0x03
# =============================================

STX = 0xCC
ETX = 0xDD

def build_set_address_command(current_addr, new_addr):
    """Factory Command: 주소 변경 (14바이트)"""
    frame = [
        STX, current_addr, 0x00,
        0xFF, 0xEE, 0xBB, 0xAA,       # Password
        new_addr, 0x00, 0x00, 0x00,
        ETX
    ]
    checksum = sum(frame) & 0xFFFF
    return bytearray(frame + [checksum & 0xFF, (checksum >> 8) & 0xFF])


def build_query_position(addr):
    """현재 위치 조회 명령 (연결 확인용)"""
    frame = [STX, addr, 0x3E, 0x00, 0x00, ETX]
    checksum = sum(frame) & 0xFFFF
    return bytearray(frame + [checksum & 0xFF, (checksum >> 8) & 0xFF])


def main():
    print("=" * 50)
    print("Runze SV07 밸브 주소 설정")
    print("=" * 50)
    print(f"포트: {COM_PORT}")
    print(f"현재 주소: 0x{CURRENT_ADDRESS:02X}")
    print(f"새 주소:   0x{NEW_ADDRESS:02X}")

    if CURRENT_ADDRESS == NEW_ADDRESS:
        print("\n현재 주소와 새 주소가 같습니다. 연결 확인만 수행합니다.")

    try:
        ser = serial.Serial(
            port=COM_PORT, baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=1.0
        )
        print(f"포트 열림: {COM_PORT}")
        time.sleep(0.5)

        # 1. 연결 확인
        print("\n[1] 연결 확인 중...")
        cmd = build_query_position(CURRENT_ADDRESS)
        ser.reset_input_buffer()
        ser.write(cmd)
        print(f"    TX: {cmd.hex()}")

        response = ser.read(8)
        if len(response) == 8:
            print(f"    RX: {response.hex()}")
            print(f"    현재 포트 위치: {response[3]}")
        else:
            print(f"    응답 없음 ({len(response)} bytes)")
            print("    밸브 연결을 확인하세요.")
            ser.close()
            return

        # 2. 주소 변경
        if CURRENT_ADDRESS != NEW_ADDRESS:
            print(f"\n[2] 주소 변경: 0x{CURRENT_ADDRESS:02X} → 0x{NEW_ADDRESS:02X}")
            cmd = build_set_address_command(CURRENT_ADDRESS, NEW_ADDRESS)
            ser.reset_input_buffer()
            ser.write(cmd)
            print(f"    TX: {cmd.hex()}")

            response = ser.read(8)
            if len(response) >= 3:
                print(f"    RX: {response.hex()}")
                if response[2] == 0x00:
                    print(f"\n성공! 주소가 0x{NEW_ADDRESS:02X}로 변경되었습니다.")
                    print("\n*** 밸브 전원을 껐다 켜세요 (재시작 필요) ***")
                else:
                    print(f"    오류 상태: 0x{response[2]:02X}")
            else:
                print(f"    응답 없음 ({len(response)} bytes)")
        else:
            print("\n[2] 주소 변경 불필요 - 이미 올바른 주소입니다.")

        ser.close()

    except Exception as e:
        print(f"오류: {e}")


if __name__ == "__main__":
    main()
