# -*- coding: utf-8 -*-
"""Chemyx RS-485 실시간 통신 감시 — 접촉불량 사냥용.

용법:  py -3.14 pump_watch.py [COM포트]   (기본 COM5)
1초마다 ID1~4에 pump status를 물어 O(정상)/x(깨짐)/·(무응답)을 한 줄씩 찍는다.
선/단자를 움직이면서 화면을 보면, 어느 접점을 만질 때 살아나는지 즉시 보인다.
전부 O가 안정적으로 유지되면 Ctrl+C 로 종료하고 캘리브레이션 진행.
"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"


def readfull(ser, t=0.45):
    t0 = time.time()
    buf = b""
    while time.time() - t0 < t:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.02)
    return buf


def clean(r):
    body = r.replace(b"\r", b"").replace(b"\n", b"").replace(b">", b"")
    if not body:
        return 0.0
    return sum(1 for x in body if 32 <= x <= 126) / len(body) * 100


def main():
    print(f"=== 펌프 통신 실시간 감시 @ {PORT} (Ctrl+C 종료) ===")
    print("선/단자를 움직이면서 보세요. 목표: 4칸 전부 O 가 계속 유지")
    ser = None
    streak = 0          # 연속 all-O 횟수
    while True:
        try:
            if ser is None or not ser.is_open:
                ser = serial.Serial(PORT, 9600, timeout=0.2)
                time.sleep(1.5)
            row = []
            for pid in (1, 2, 3, 4):
                ser.reset_input_buffer()
                ser.write(f"{pid} pump status\r".encode())
                r = readfull(ser)
                c = clean(r)
                row.append("O" if (r and c > 90) else ("x" if r else "·"))
            allok = all(s == "O" for s in row)
            streak = streak + 1 if allok else 0
            mark = "  <<<< 전부 정상!" + (f" (연속 {streak})" if streak > 1 else "") if allok else ""
            print(f"{time.strftime('%H:%M:%S')}  ID1={row[0]} ID2={row[1]} ID3={row[2]} ID4={row[3]}{mark}",
                  flush=True)
        except serial.SerialException as e:
            print(f"{time.strftime('%H:%M:%S')}  포트 오류({e.__class__.__name__}) — 재연결 시도", flush=True)
            try:
                if ser:
                    ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(1.5)
        except KeyboardInterrupt:
            break
    try:
        if ser:
            ser.close()
    except Exception:
        pass
    print("종료")


if __name__ == "__main__":
    main()
