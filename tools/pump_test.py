"""
Chemyx 시린지펌프 RS-485 단독 테스트 (읽기 전용 — 펌프 미동작)
VS Code 에서 그냥 F5 / "Run Python File" 하면 됩니다. 인자 필요 없음.

┌─ 여기만 고치면 됩니다 ────────────────────────────┐
"""
PORT = "COM6"      # RS-485 어댑터 (CH340)
PUMP_ID = 1        # 지금 연결된 펌프 주소
BAUD = 9600        # 메모 기준값. 안 되면 아래 baud 스윕이 자동 실행됨
"""
└──────────────────────────────────────────────────┘

동작 순서:
  1) 지정 baud 로 `<id> pump status` 조회
  2) 무응답/깨짐이면 baud 자동 스윕 (9600~115200)
  3) 받은 데이터가 "우리가 보낸 명령의 에코"인지 자동 판별
  4) 끝나면 대화형 프롬프트 — 명령 직접 쳐볼 수 있음 (q = 종료)

⚠ 기계를 움직이는 명령(start 등)은 이 스크립트가 자동으로 보내지 않습니다.
   대화형 모드에서 직접 치면 나갑니다 — start 는 주의.
"""

import sys
import time

import serial

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BAUD_SWEEP = [9600, 19200, 38400, 57600, 115200]
READ_ONLY_CMDS = ["pump status", "dispensed volume", "view parameter"]


def send(ser, text, wait=0.7):
    """명령 전송 후 원시 응답 반환."""
    ser.reset_input_buffer()
    ser.write((text + "\r").encode())
    ser.flush()
    time.sleep(wait)
    return ser.read(ser.in_waiting or 1)


def is_echo(sent, raw):
    """
    받은 데이터가 우리가 보낸 명령의 되돌이(에코)인지 판별.
    RS-485 어댑터가 232 모드거나 A/B 가 물려 있으면 송신이 그대로 되돌아온다.
    비트오류로 깨져 있어도 길이와 글자 위치가 겹치므로 그걸로 잡는다.
    """
    if not raw:
        return False
    s = sent.encode()
    if abs(len(raw) - len(s)) > 3:
        return False
    match = sum(1 for a, b in zip(raw, s) if a == b)
    return match >= max(2, len(s) // 4)


def looks_clean(raw):
    """깨끗한 ASCII 응답인지."""
    return bool(raw) and all(0x20 <= b <= 0x7E or b in (0x0D, 0x0A) for b in raw)


def show(sent, raw):
    txt = raw.decode("ascii", "replace").replace("\r", "\\r").replace("\n", "\\n")
    if not raw:
        verdict = "무응답"
    elif is_echo(sent, raw):
        verdict = "★에코(우리 송신 되돌이) — 펌프 응답 아님"
    elif looks_clean(raw):
        verdict = "정상 응답"
    else:
        verdict = "깨짐"
    print(f"    TX {sent!r}")
    print(f"    RX {txt!r}   → {verdict}")
    return verdict


def try_baud(baud):
    print(f"\n[baud {baud}] 시도")
    try:
        ser = serial.Serial(PORT, baud, timeout=0.5)
    except Exception as e:
        print(f"    포트 열기 실패: {type(e).__name__}: {e}")
        return None, None
    time.sleep(1.5)
    ser.read_all()
    cmd = f"{PUMP_ID} pump status"
    raw = send(ser, cmd)
    verdict = show(cmd, raw)
    if verdict == "정상 응답":
        return ser, baud
    ser.close()
    return None, None


def interactive(ser, baud):
    print("\n" + "=" * 55)
    print(f"대화형 모드 (COM={PORT}, baud={baud}, ID={PUMP_ID})")
    print("  그냥 명령만 치세요 — ID 는 자동으로 앞에 붙습니다.")
    print(f"  예: pump status / view parameter / dispensed volume")
    print("  raw:<문자열>  → ID 안 붙이고 그대로 전송")
    print("  q → 종료")
    print("=" * 55)
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line or line.lower() in ("q", "quit", "exit"):
            break
        if line.startswith("raw:"):
            cmd = line[4:]
        else:
            cmd = f"{PUMP_ID} {line}"
        show(cmd, send(ser, cmd, wait=0.8))


def diagnose_bus():
    """
    baud/주소를 따지기 전에 '버스에 살아있는 게 있는가'부터 판정한다.
      1) 송신 없이 듣기 → 바이트가 오면 누군가 말하고 있음
      2) 특징적 패턴 송신 → 받은 게 페이로드를 따라 변하면 그건 내 송신의 되돌이
    2026-07-31 실측: 전 baud 에서 (1)=0바이트, (2)=완전 되돌이 → 응답 펌프 0대.
    반환값 True = 버스에 응답하는 장치 있음(계속 진행), False = 배선 문제 확정.
    """
    print("\n[0단계] 버스 생존 확인 (baud 무관)")
    alive = False
    for baud in (9600, 38400):
        try:
            ser = serial.Serial(PORT, baud, timeout=0.5)
        except Exception as e:
            print(f"  baud {baud}: 포트 열기 실패 {type(e).__name__}: {e}")
            return False
        time.sleep(1.2)
        ser.read_all()

        time.sleep(1.2)
        quiet = ser.read(ser.in_waiting) if ser.in_waiting else b""
        # 0xFF/0x00 만 오는 건 라인이 뜬(플로팅) 상태의 아이들 노이즈 — 장치 아님
        idle_noise = quiet and all(b in (0x00, 0xFF) for b in quiet)
        note = "  ← 플로팅 라인 노이즈(장치 아님)" if idle_noise else ""
        print(f"  baud {baud} 무송신 청취: {len(quiet)}바이트 {quiet!r}{note}")
        if quiet and not idle_noise:
            alive = True

        # 페이로드를 바꿔서 보내고, 받은 게 그걸 따라가는지 본다
        seen = {}
        for payload in (b"UUUU\r", b"AAAA\r"):
            ser.reset_input_buffer()
            ser.write(payload)
            ser.flush()
            time.sleep(0.5)
            seen[payload] = ser.read(ser.in_waiting or 1)
        ser.close()

        u, a = seen[b"UUUU\r"], seen[b"AAAA\r"]
        print(f"  baud {baud} TX 'UUUU' → {u!r}")
        print(f"  baud {baud} TX 'AAAA' → {a!r}")

        def is_line_artifact(raw):
            """송신 길이와 같고 상수충전(0x00/0xFF)이면 라인이 물린 것 — 장치 아님."""
            return raw and len(raw) == 5 and all(b in (0x00, 0xFF) for b in raw)

        if is_line_artifact(u) and is_line_artifact(a):
            print("    → 상수충전 수신 = 라인이 눌린 상태(접촉/플로팅). 장치 응답 아님")
        elif u and a and u != a and len(u) == len(a) == 5:
            print("    → 응답이 송신 페이로드를 그대로 따라감 = 되돌이(에코). 장치 응답 아님")
        elif u or a:
            alive = True
            print("    → 페이로드와 무관한 데이터 수신 = 장치 응답 가능성 있음")

    return alive


def main():
    print("=" * 55)
    print(f"Chemyx RS-485 테스트 — {PORT} / 펌프 ID {PUMP_ID}")
    print("=" * 55)

    if not diagnose_bus():
        print("\n" + "!" * 55)
        print("버스에 응답하는 장치가 없습니다 (에코만 돌아옴).")
        print("→ baud/주소/명령 문제가 아닙니다. 소프트웨어는 배제됨.")
        print("→ 물리 배선만 확인하면 됩니다:")
        print()
        print("  1. ★A/B 방향 스왑해보기 — 가장 흔한 원인, 10초면 확인됨")
        print("     정상: 어댑터 A → DB9 6&9,  어댑터 B → DB9 1&4")
        print("     반대로 꽂으면 완전 침묵 (지금 증상과 일치)")
        print("  2. 펌프 전원 ON 인지, 앞판넬 주소가 1로 저장됐는지")
        print("  3. 어댑터가 485 모드인지 (232 모드면 펌프까지 안 감)")
        print("  4. DB9 1&4 브리지, 6&9 브리지 됐는지 (단독 연결이어도 필요)")
        print("  5. GND(5번)는 연결하지 말 것")
        print()
        print("  → 하나 바꿀 때마다 이 스크립트 다시 실행하세요.")
        print("!" * 55)
        return

    ser, baud = try_baud(BAUD)

    if ser is None:
        print("\n지정 baud 실패 → 자동 스윕 시작")
        for b in BAUD_SWEEP:
            if b == BAUD:
                continue
            ser, baud = try_baud(b)
            if ser:
                break

    if ser is None:
        print("\n" + "!" * 55)
        print("모든 baud 에서 정상 응답 없음.")
        print("확인 순서 (Chemyx_RS485_핀아웃_배선메모.md):")
        print("  1. 어댑터가 485 모드인가 (232 모드면 송신이 에코로 돌아옴)")
        print("  2. A/B 방향 — A→DB9 6&9, B→DB9 1&4. 반대면 완전 침묵")
        print("  3. 펌프 전원 ON, 앞판넬 주소가 실제로 저장됐는지")
        print("  4. 접촉 — DuPont 점퍼면 흔들어보며 재스캔")
        print("  5. GND(5번)는 연결하지 말 것 (그라운드 루프로 비트오류)")
        print("!" * 55)
        return

    print(f"\n✅ 통신 성공 — baud {baud}")
    print("\n[읽기 전용 명령 확인]")
    for c in READ_ONLY_CMDS:
        cmd = f"{PUMP_ID} {c}"
        show(cmd, send(ser, cmd, wait=0.8))

    interactive(ser, baud)
    ser.close()
    print("\n종료 (펌프 미동작)")


if __name__ == "__main__":
    main()
