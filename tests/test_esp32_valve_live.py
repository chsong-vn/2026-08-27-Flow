"""
ESP32-S3-ETH-8DI-8RO 3-Way 밸브 실기 테스트 도구 (8채널, TCP)

사용법: py -3.14 test_esp32_valve_live.py [host[:port]]
  기본 접속: 192.168.0.60:5000  (펌웨어 valve_relay_eth_8ch.ino 의 정적 IP)

채널 배정 (2026-07-29):
  CH1=Group A, CH2=Group B, CH3=Group C, CH4=Group D, CH5=Outlet, CH6~8=예비

3-Way 밸브 상태:
  Position A (릴레이 OFF) = 포트 A / SOURCE·WASTE (기본, fail-safe)
  Position B (릴레이 ON)  = 포트 B / REACTOR·COLLECT

명령어:
  [1-8] a/b       — 개별 채널 포트 A/B 전환
  all a / all b   — 전체 채널 동시 전환
  seq             — 순차 테스트 (각 채널 B→A, 1초 간격)
  toggle [1-8]    — 해당 채널 상태 반전
  status          — 보드 실측 상태 조회 (STATE 명령 — 캐시 아님)
  id              — 장치 식별 (ID 명령)
  q               — 종료 (전체 A 복귀)
"""

import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_ENDPOINT = "192.168.0.60:5000"

CH_ROLE = {1: "Group A", 2: "Group B", 3: "Group C", 4: "Group D",
           5: "Outlet", 6: "(예비)", 7: "(예비)", 8: "(예비)"}


def parse_endpoint(s):
    if ":" in s:
        host, _, p = s.rpartition(":")
        return host, int(p)
    return s, 5000


def send_cmd(sock, msg, timeout=1.0):
    """명령 전송 후 응답 한 줄 반환"""
    # 잔류 수신 비우기
    sock.settimeout(0.05)
    try:
        while sock.recv(1024):
            pass
    except socket.timeout:
        pass
    sock.sendall(f"{msg}\n".encode())
    sock.settimeout(timeout)
    buf = ""
    try:
        while "\n" not in buf:
            chunk = sock.recv(256)
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
    except socket.timeout:
        pass
    return buf.strip()


def set_valve(sock, ch, port):
    pos = "1" if port == "a" else "2"
    resp = send_cmd(sock, f"{ch} {pos}")
    mark = "OK" if f"OK {ch} {pos}" in resp else "⚠ ACK 불일치"
    print(f"  CH{ch}({CH_ROLE[ch]}) → 포트 {port.upper()}  |  TX: {ch} {pos}  RX: {resp}  [{mark}]")


def set_all(sock, port):
    pos = "1" if port == "a" else "2"
    resp = send_cmd(sock, pos)
    print(f"  ALL → 포트 {port.upper()}  |  TX: {pos}  RX: {resp}")


def show_status(sock):
    """보드 실측 상태 (STATE) — PC측 캐시가 아니라 릴레이 shadow 값"""
    resp = send_cmd(sock, "STATE")
    parts = resp.split()
    if len(parts) != 9 or parts[0] != "STATE":
        print(f"  STATE 응답 이상: '{resp}'")
        return None
    states = [int(x) for x in parts[1:]]
    print("\n  ┌──────┬──────────┬────────┐")
    print("  │  CH  │   역할   │  상태  │")
    print("  ├──────┼──────────┼────────┤")
    for ch in range(1, 9):
        port = "A" if states[ch - 1] == 1 else "B"
        relay = "OFF" if port == "A" else "ON "
        print(f"  │  {ch}   │ {CH_ROLE[ch]:<8} │ {port} ({relay}) │")
    print("  └──────┴──────────┴────────┘\n")
    return states


def sequential_test(sock):
    print("\n  === 순차 테스트 시작 (CH1~8, B→A) ===")
    set_all(sock, "a")
    time.sleep(0.5)
    for ch in range(1, 9):
        print(f"\n  -- CH{ch} ({CH_ROLE[ch]}) 테스트 --")
        set_valve(sock, ch, "b")
        time.sleep(1.0)
        set_valve(sock, ch, "a")
        time.sleep(0.5)
    print("\n  === 순차 테스트 완료 ===\n")


def main():
    endpoint = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENDPOINT
    host, port = parse_endpoint(endpoint)
    print(f"=== ESP32 3-Way 밸브 실기 테스트 ({host}:{port}) ===")
    print("연결 중...")

    try:
        sock = socket.create_connection((host, port), timeout=3.0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        print(f"접속 실패: {e}")
        print("보드 전원/이더넷 케이블/PC측 IP 대역(같은 서브넷) 확인 후 재시도하세요.")
        sys.exit(1)

    # 인사말 수신
    sock.settimeout(1.0)
    try:
        ready = sock.recv(256).decode(errors="replace").strip()
        if ready:
            print(f"Board: {ready}")
    except socket.timeout:
        pass

    print(f"Device: {send_cmd(sock, 'ID')}")
    print()
    print("명령어: [1-8] a/b · all a/b · seq · toggle [1-8] · status · id · q")
    print("-" * 55)
    show_status(sock)

    valid_ch = [str(c) for c in range(1, 9)]

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
            sequential_test(sock)
            continue
        if cmd == "status":
            show_status(sock)
            continue
        if cmd == "id":
            print(f"  {send_cmd(sock, 'ID')}")
            continue

        parts = cmd.split()

        if parts[0] == "toggle" and len(parts) == 2 and parts[1] in valid_ch:
            ch = int(parts[1])
            states = show_status(sock)
            if states:
                new_port = "b" if states[ch - 1] == 1 else "a"
                set_valve(sock, ch, new_port)
            continue

        if len(parts) != 2:
            print("형식: [1-8|all] [a|b] / seq / toggle [1-8] / status / id / q")
            continue

        ch_str, port_ab = parts
        if port_ab not in ("a", "b"):
            print("포트는 a 또는 b만 가능")
            continue

        if ch_str == "all":
            set_all(sock, port_ab)
        elif ch_str in valid_ch:
            set_valve(sock, int(ch_str), port_ab)
        else:
            print("채널: 1~8 또는 all")

    print("\n전체 밸브 포트 A(릴레이 OFF)로 복귀 후 종료...")
    send_cmd(sock, "1")
    sock.close()
    print("완료")


if __name__ == "__main__":
    main()
