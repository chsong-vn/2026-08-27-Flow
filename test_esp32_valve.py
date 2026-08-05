"""
ESP32EthValve 드라이버 자동 검증 (하드웨어 불필요 — 로컬 mock TCP 서버)

실행: py -3.14 test_esp32_valve.py

검증 항목:
  T1  연결 + ACK 전환 → position 갱신, TX 프레임 형식
  T2  이름 포지션 매핑 (SOURCE/WASTE→1, REACTOR/COLLECT→2)
  T3  무응답(silent) → RuntimeError + position 불변  (fault-masking F1 의미론)
  T4  1회 오응답 후 ACK → 재시도로 성공
  T5  링크 공유 — 같은 endpoint 2채널 = 소켓 1개 (connections==1)
  T6  Mock 포트 → 가상 동작 (소켓 미사용)
  T7  서버측 연결 끊김 → 자동 재수립 후 성공 (TCP stale 회복)
  T8  cleanup 계약 — 레지스트리 값이 is_open/close 를 제공 (hw_manager 3-1 호환)
  T9  잘못된 포지션 → 조용히 무시 (ArduinoValve 동일)
  T10 엔드포인트 파서 (host:port / 기본포트 / tcp:// 접두)
"""

import socket
import threading
import time
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hardware.valves.valve_esp32_eth import ESP32EthValve, _TcpLink, DEFAULT_TCP_PORT


class MockValveServer:
    """valve_relay_eth_8ch.ino 의 프로토콜을 흉내내는 로컬 TCP 서버."""

    def __init__(self, mode="ok", fail_count=1):
        self.mode = mode            # ok | silent | garbage | fail_first
        self.fail_count = fail_count
        self.connections = 0
        self.received = []
        self._cmd_no = 0
        self._conn = None
        self._running = True
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        self._th = threading.Thread(target=self._serve, daemon=True)
        self._th.start()

    @property
    def endpoint(self):
        return f"127.0.0.1:{self.port}"

    def _serve(self):
        while self._running:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.connections += 1
            self._conn = conn
            try:
                conn.sendall(b"READY 8CH ETH\n")
                buf = ""
                conn.settimeout(0.2)
                while self._running:
                    try:
                        chunk = conn.recv(256)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk.decode(errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line:
                            self._handle(conn, line)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle(self, conn, line):
        self.received.append(line)
        self._cmd_no += 1
        if line == "STATE":
            conn.sendall(b"STATE 1 1 1 1 1 1 1 1\n")
            return
        if self.mode == "silent":
            return
        if self.mode == "garbage":
            conn.sendall(b"ERR NOISE\n")
            return
        if self.mode == "fail_first" and self._cmd_no <= self.fail_count:
            conn.sendall(b"ERR TRANSIENT\n")
            return
        parts = line.split()
        if len(parts) == 2:
            conn.sendall(f"OK {parts[0]} {parts[1]}\n".encode())
        elif len(parts) == 1 and parts[0] in ("1", "2"):
            conn.sendall(f"OK ALL {parts[0]}\n".encode())
        else:
            conn.sendall(b"ERR INVALID_CMD\n")

    def drop_client(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass

    def stop(self):
        self._running = False
        try:
            self.srv.close()
        except OSError:
            pass


def _reset_registry():
    for link in ESP32EthValve._serial_registry.values():
        try:
            link.close()
        except Exception:
            pass
    ESP32EthValve._serial_registry.clear()
    ESP32EthValve._lock_registry.clear()


results = []


def check(tid, desc, cond):
    results.append((tid, desc, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {tid}: {desc}")


def main():
    print("=== ESP32EthValve 자동 검증 ===\n")

    # ── T1 / T2: 정상 ACK + 이름 매핑 ────────────────────────────
    _reset_registry()
    srv = MockValveServer(mode="ok")
    v1 = ESP32EthValve(srv.endpoint, channel=1)
    check("T1a", "connect 성공", v1.connect())
    v1.set_position(2)
    check("T1b", "ACK 후 position=2", v1.position == 2)
    check("T1c", "TX 프레임 '1 2'", srv.received[-1] == "1 2")

    v1.set_position("SOURCE")
    check("T2a", "SOURCE → position 1", v1.position == 1)
    v1.set_position("REACTOR")
    check("T2b", "REACTOR → position 2", v1.position == 2)
    v1.set_position("WASTE")
    check("T2c", "WASTE → position 1", v1.position == 1)
    v1.set_position("COLLECT")
    check("T2d", "COLLECT → position 2", v1.position == 2)

    # ── T9: 잘못된 포지션은 조용히 무시 ──────────────────────────
    before = v1.position
    n_rx = len(srv.received)
    v1.set_position("BOGUS")
    check("T9", "잘못된 pos 무시 (전송 없음·상태 불변)",
          v1.position == before and len(srv.received) == n_rx)

    # ── T5: 링크 공유 ────────────────────────────────────────────
    v2 = ESP32EthValve(srv.endpoint, channel=5)
    v2.connect()
    v2.set_position(2)
    check("T5a", "2번째 채널 TX '5 2'", srv.received[-1] == "5 2")
    check("T5b", "레지스트리 링크 1개", len(ESP32EthValve._serial_registry) == 1)
    check("T5c", "TCP 접속 1회 (소켓 공유)", srv.connections == 1)

    # ── T7: 서버측 끊김 → 재수립 ─────────────────────────────────
    srv.drop_client()
    time.sleep(0.1)
    v1.set_position(2)
    check("T7a", "링크 재수립 후 ACK 성공", v1.position == 2)
    check("T7b", "재접속 발생 (connections≥2)", srv.connections >= 2)

    # ── T8: cleanup 계약 (hw_manager 3-1 루프와 동일 패턴) ───────
    ok8 = True
    for port, ser in list(ESP32EthValve._serial_registry.items()):
        if not hasattr(ser, "is_open") or not callable(getattr(ser, "close", None)):
            ok8 = False
        if ser.is_open:
            ser.close()
        if ser.is_open:
            ok8 = False
    ESP32EthValve._serial_registry.clear()
    ESP32EthValve._lock_registry.clear()
    check("T8", "레지스트리 is_open/close 계약 + 정리", ok8)
    srv.stop()

    # ── T3: 무응답 → RuntimeError + position 불변 ────────────────
    _reset_registry()
    srv3 = MockValveServer(mode="silent")
    v3 = ESP32EthValve(srv3.endpoint, channel=2)
    v3.connect()
    raised = False
    try:
        v3.set_position(2)
    except RuntimeError:
        raised = True
    check("T3a", "무응답 시 RuntimeError", raised)
    check("T3b", "position 불변 (=1)", v3.position == 1)
    check("T3c", "3회 재시도 수행", srv3.received.count("2 2") == 3)
    srv3.stop()

    # ── T4: 1회 오응답 후 ACK → 재시도 성공 ──────────────────────
    _reset_registry()
    srv4 = MockValveServer(mode="fail_first", fail_count=1)
    v4 = ESP32EthValve(srv4.endpoint, channel=3)
    v4.connect()
    v4.set_position(2)
    check("T4", "재시도 후 성공 (position=2)", v4.position == 2)
    srv4.stop()

    # ── T6: Mock 포트 ────────────────────────────────────────────
    _reset_registry()
    v6 = ESP32EthValve("Mock_Port", channel=1)
    check("T6a", "Mock connect", v6.connect())
    v6.set_position(2)
    check("T6b", "Mock position=2 (소켓 미사용)",
          v6.position == 2 and len(ESP32EthValve._serial_registry) == 0)

    # ── T10: 엔드포인트 파서 ─────────────────────────────────────
    check("T10a", "host:port", ESP32EthValve._parse_endpoint("192.168.0.60:5000") == ("192.168.0.60", 5000))
    check("T10b", "기본 포트", ESP32EthValve._parse_endpoint("192.168.0.60") == ("192.168.0.60", DEFAULT_TCP_PORT))
    check("T10c", "tcp:// 접두", ESP32EthValve._parse_endpoint("tcp://10.0.0.5:1234") == ("10.0.0.5", 1234))

    # ── 결과 ─────────────────────────────────────────────────────
    n_fail = sum(1 for _, _, ok in results if not ok)
    print(f"\n=== 결과: {len(results) - n_fail}/{len(results)} PASS ===")
    if n_fail == 0:
        print("ALL PASS")
        return 0
    for tid, desc, ok in results:
        if not ok:
            print(f"  FAIL → {tid}: {desc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
