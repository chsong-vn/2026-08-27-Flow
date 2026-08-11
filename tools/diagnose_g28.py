# -*- coding: utf-8 -*-
"""
G28 호밍 진단 — 분취기(Marlin) 펌웨어/엔드스톱/호밍 판별
=========================================================
⚠ 앱을 먼저 종료할 것 (COM 포트 점유 충돌).

사용:
  py -3.14 diagnose_g28.py                  # 모터 이동 없음: 부팅배너 + M115 + M119 라이브
  py -3.14 diagnose_g28.py --home           # + G28 실행 (풀 로그, 180초, 절단 없음)
  py -3.14 diagnose_g28.py --port COM11     # 포트 지정 (기본 COM11)

판독 기준:
  [M115]  MACHINE_TYPE 에 "FlowChem Fraction Collector" 가 없으면
          → 교체 기계에 커스텀 펌웨어가 안 구워진 것 (★호밍 이상의 최우선 용의자)
  [M119]  각 엔드스톱을 손으로 눌렀다 떼며 open ↔ TRIGGERED 토글 확인.
          안 바뀌는 축 = 배선 탈락/커넥터 오배선 → G28 시 그 축이 끝까지 갈림
  [G28]   풀 로그에 Error:Homing Failed / kill() 이 보이면 물리 실패(보드 리셋 필요),
          없이 늦게라도 ok 가 오면 단순 타임아웃(드라이버 wait 상향으로 해결)
"""
import sys
import time
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import serial


def open_board(port):
    ser = serial.Serial(port=port, baudrate=250000, timeout=0.2)
    # DTR 리셋 (드라이버 connect() 와 동일 절차)
    ser.dtr = False
    time.sleep(0.2)
    ser.dtr = True
    print(f"[{port}] DTR 리셋 — 부팅 배너 수신 대기 (6초)...")
    deadline = time.time() + 6.0
    banner = b""
    while time.time() < deadline:
        if ser.in_waiting:
            banner += ser.read(ser.in_waiting)
        else:
            time.sleep(0.05)
    text = banner.decode("utf-8", errors="replace")
    print("── 부팅 배너 ──────────────────────────────")
    print(text.strip() or "(없음 — DTR 리셋이 안 먹는 보드일 수 있음, 계속 진행)")
    print("──────────────────────────────────────────")
    ser.reset_input_buffer()
    return ser


def send_stream(ser, cmd, timeout, quiet=False):
    """명령 전송 후 응답을 실시간 스트리밍 (절단 없음). ok/Error/kill 에서 종료."""
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    ser.flush()
    if not quiet:
        print(f"\n>>> {cmd}   (timeout {timeout:.0f}s)")
    t0 = time.time()
    buf = b""
    lines_done = 0
    result = "timeout"
    while time.time() - t0 < timeout:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
        else:
            time.sleep(0.03)
            continue
        text = buf.decode("utf-8", errors="replace")
        lines = text.splitlines()
        # 완결된 라인만 출력 (마지막 조각은 보류)
        complete = lines[:-1] if not text.endswith("\n") else lines
        for l in complete[lines_done:]:
            if l.strip():
                print(f"  [{time.time()-t0:6.1f}s] {l}")
        lines_done = len(complete)
        low = text.lower()
        if "kill" in low or "error" in low:
            result = "ERROR"
            # 에러 후 꼬리 로그 1초 더 수집
            tail_end = time.time() + 1.0
            while time.time() < tail_end:
                if ser.in_waiting:
                    buf += ser.read(ser.in_waiting)
                time.sleep(0.05)
            for l in buf.decode("utf-8", errors="replace").splitlines()[lines_done:]:
                if l.strip():
                    print(f"  [{time.time()-t0:6.1f}s] {l}")
            break
        stripped = [l.strip().lower() for l in complete if l.strip()]
        if stripped and stripped[-1].startswith("ok"):
            result = "ok"
            break
    print(f"<<< 결과: {result}  (소요 {time.time()-t0:.1f}s)")
    return result, buf.decode("utf-8", errors="replace")


def live_endstop_watch(ser, seconds=25):
    print(f"\n── M119 엔드스톱 라이브 ({seconds}초) ──")
    print("   각 축 엔드스톱(그리고 BLTouch)을 손으로 눌렀다 떼세요.")
    print("   눌렀는데 TRIGGERED 로 안 바뀌는 축 = 배선 문제.\n")
    t_end = time.time() + seconds
    last = ""
    while time.time() < t_end:
        ser.reset_input_buffer()
        ser.write(b"M119\n")
        ser.flush()
        time.sleep(0.35)
        raw = ser.read(ser.in_waiting or 1).decode("utf-8", errors="replace")
        states = [l.strip() for l in raw.splitlines()
                  if ":" in l and ("_min" in l or "_max" in l or "probe" in l.lower())]
        snap = " | ".join(states)
        if snap and snap != last:
            print(f"  [{seconds - (t_end - time.time()):4.0f}s] {snap}")
            last = snap
        time.sleep(0.4)
    print("── M119 종료 ──")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM11")
    ap.add_argument("--home", action="store_true", help="G28 실행 (모터 이동!)")
    ap.add_argument("--watch", type=int, default=25, help="M119 라이브 시간(초)")
    args = ap.parse_args()

    ser = open_board(args.port)
    try:
        # 1) 펌웨어 신원 — 커스텀 펌웨어 여부가 최우선 판별점
        _, m115 = send_stream(ser, "M115", 5.0)
        if "FlowChem" in m115:
            print("  ✔ 커스텀 펌웨어(FlowChem Fraction Collector) 확인")
        else:
            print("  ✘ 커스텀 펌웨어 문자열 없음 — 교체 기계에 순정/다른 펌웨어일 가능성 ★")

        send_stream(ser, "G90", 3.0)
        # 2) 현재 논리좌표 (리셋 직후면 0,0,0 이 정상 — 물리 위치와 무관)
        send_stream(ser, "M114", 5.0)
        # 3) 엔드스톱 실시간
        live_endstop_watch(ser, args.watch)

        # 4) G28 (옵션)
        if args.home:
            print("\n⚠ 5초 후 G28 실행 — 축이 갈리는 소리가 나면 즉시 전원 차단!")
            print("  관찰 포인트: 각 축이 '센서가 있는 쪽'으로 움직이는가?")
            time.sleep(5)
            res, _ = send_stream(ser, "G28", 180.0)
            if res == "ok":
                send_stream(ser, "M114", 5.0)
                print("\n판정: G28 자체는 성공. 이전 경고는 드라이버 60초 타임아웃이 원인")
                print("      → collector_plate96.py home() 의 wait=60.0 상향으로 해결 가능")
            elif res == "ERROR":
                print("\n판정: 펌웨어가 호밍 실패 선언 (엔드스톱 미접촉/프로브 실패)")
                print("      → 위 M119 결과와 이동 방향 관찰로 축 특정. 보드 전원 재투입 필요")
            else:
                print("\n판정: 180초에도 미완료 — 축이 물리적으로 막혔거나 통신 유실")
    finally:
        ser.close()
        print("\n포트 닫음.")


if __name__ == "__main__":
    main()
