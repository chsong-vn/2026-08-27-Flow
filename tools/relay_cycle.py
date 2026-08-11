"""
ESP32 3-Way 릴레이 비대화형 OFF/ON 사이클 (test_esp32_valve_live.py 와 동일 프로토콜)

사용법 (TCP — 정상 운용 경로):
  py -3.14 relay_cycle.py                      # 전체 채널, 1회 OFF→ON→OFF
  py -3.14 relay_cycle.py --ch 5               # CH5(Outlet)만
  py -3.14 relay_cycle.py --ch 1 --cycles 3 --dwell 1.5
  py -3.14 relay_cycle.py --host 192.168.0.60  # 다른 IP

사용법 (USB 시리얼 — 랜선 없을 때 폴백. 펌웨어가 양쪽 동일 명령 수신):
  py -3.14 relay_cycle.py --serial COM7 --sweep

  --sweep : CH1~5 를 한 채널씩 B(ON)→A(OFF) 순차 검증 (채널↔밸브 배선 확인용)

프로토콜 (펌웨어 valve_relay_eth_8ch.ino):
  "<ch> 1" = 포트 A / 릴레이 OFF (fail-safe)   "<ch> 2" = 포트 B / 릴레이 ON
  "1" / "2" = 전체 채널 동시 전환,  "STATE" = 실측 상태,  "ID" = 장치 식별
  "NET" = 링크/IP 진단 (TCP 가 안 될 때 원인 구분)

항상 포트 A(릴레이 OFF)로 복귀한 뒤 종료한다.
"""

import argparse
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_HOST = "192.168.10.60"
DEFAULT_PORT = 5000

CH_ROLE = {1: "Group A", 2: "Group B", 3: "Group C", 4: "Group D",
           5: "Outlet", 6: "(예비)", 7: "(예비)", 8: "(예비)"}


class TcpLink:
    """TCP 5000 — 정상 운용 경로"""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=3.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(1.0)
        try:
            greet = self.sock.recv(256).decode(errors="replace").strip()
            if greet:
                print(f"Board: {greet}")
        except socket.timeout:
            pass

    def send_cmd(self, msg, timeout=1.0):
        self.sock.settimeout(0.05)
        try:
            while self.sock.recv(1024):
                pass
        except (socket.timeout, OSError):
            pass
        self.sock.sendall(f"{msg}\n".encode())
        self.sock.settimeout(timeout)
        buf = ""
        try:
            while "\n" not in buf:
                chunk = self.sock.recv(256)
                if not chunk:
                    break
                buf += chunk.decode(errors="replace")
        except socket.timeout:
            pass
        return buf.strip()

    def close(self):
        self.sock.close()


class SerialLink:
    """USB 시리얼 115200 — 랜선 없을 때 폴백 (펌웨어가 동일 명령 수신)"""

    def __init__(self, port, baud=115200):
        import serial  # 시리얼 경로에서만 필요
        self.ser = serial.Serial(port, baud, timeout=1.5)
        time.sleep(2.0)  # USB CDC 재열림/부팅 대기
        self.ser.read_all()

    def send_cmd(self, msg, timeout=1.2):
        self.ser.reset_input_buffer()
        self.ser.write(f"{msg}\n".encode())
        self.ser.flush()
        deadline = time.time() + timeout
        buf = ""
        while time.time() < deadline and "\n" not in buf:
            buf += self.ser.read_all().decode(errors="replace")
            if "\n" not in buf:
                time.sleep(0.05)
        return buf.strip()

    def close(self):
        self.ser.close()


def show_status(link):
    resp = link.send_cmd("STATE")
    parts = resp.split()
    if len(parts) != 9 or parts[0] != "STATE":
        print(f"  STATE 응답 이상: '{resp}'")
        return None
    states = [int(x) for x in parts[1:]]
    line = "  ".join(
        f"CH{ch}={'A/OFF' if states[ch - 1] == 1 else 'B/ON'}" for ch in range(1, 9)
    )
    print(f"  STATE  {line}")
    return states


def apply(link, ch, port):
    """ch=None 이면 전체. port 'a'|'b'"""
    pos = "1" if port == "a" else "2"
    if ch is None:
        resp = link.send_cmd(pos)
        label, expect = "ALL", f"OK ALL {pos}"
    else:
        resp = link.send_cmd(f"{ch} {pos}")
        label, expect = f"CH{ch}({CH_ROLE[ch]})", f"OK {ch} {pos}"
    mark = "OK" if expect in resp else "⚠ ACK 불일치"
    print(f"  {label} → 포트 {port.upper()} "
          f"(릴레이 {'OFF' if port == 'a' else 'ON'})  RX: {resp}  [{mark}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--ch", type=int, default=None, choices=range(1, 9),
                    help="대상 채널 (생략 시 전체 채널)")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--dwell", type=float, default=1.0, help="각 상태 유지 시간(초)")
    ap.add_argument("--serial", metavar="COMn",
                    help="TCP 대신 USB 시리얼로 접속 (랜선 없을 때)")
    ap.add_argument("--sweep", action="store_true",
                    help="CH1~5 한 채널씩 순차 검증 (--ch/--cycles 무시)")
    args = ap.parse_args()

    target = f"CH{args.ch}({CH_ROLE[args.ch]})" if args.ch else "전체 채널(CH1~8)"
    if args.sweep:
        target = "CH1~5 순차 스윕"
    where = f"시리얼 {args.serial}" if args.serial else f"{args.host}:{args.port}"
    print(f"=== 3-Way 릴레이 OFF/ON 사이클 — {where} / {target} ===")

    try:
        link = SerialLink(args.serial) if args.serial else TcpLink(args.host, args.port)
    except Exception as e:
        print(f"접속 실패: {type(e).__name__}: {e}")
        if args.serial:
            print("USB 케이블 / COM 번호 / 다른 프로그램의 포트 점유를 확인하세요.")
        else:
            print("보드 전원 / 이더넷 링크(LED) / 같은 서브넷 여부를 확인하세요.")
            print("USB 만 연결돼 있으면: --serial COM7 로 폴백 가능")
        sys.exit(1)

    print(f"Device: {link.send_cmd('ID')}")
    print(f"Net   : {link.send_cmd('NET')}")

    print("\n[사이클 전 상태]")
    show_status(link)

    try:
        if args.sweep:
            for ch in range(1, 6):
                print(f"\n--- CH{ch} ({CH_ROLE[ch]}) ---")
                apply(link, ch, "b")           # ON
                time.sleep(args.dwell)
                apply(link, ch, "a")           # OFF
                time.sleep(args.dwell)
        else:
            for i in range(1, args.cycles + 1):
                print(f"\n--- 사이클 {i}/{args.cycles} ---")
                apply(link, args.ch, "a")      # OFF
                time.sleep(args.dwell)
                apply(link, args.ch, "b")      # ON
                time.sleep(args.dwell)
                apply(link, args.ch, "a")      # OFF 복귀
                time.sleep(args.dwell)
                show_status(link)
    except KeyboardInterrupt:
        print("\n중단됨 — 포트 A 복귀 중...")

    print("\n[종료 처리] 전체 포트 A(릴레이 OFF) 복귀")
    link.send_cmd("1")
    show_status(link)
    link.close()
    print("완료")


if __name__ == "__main__":
    main()
