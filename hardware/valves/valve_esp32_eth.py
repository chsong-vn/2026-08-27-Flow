import re
import socket
import time
import threading

import serial

DEFAULT_TCP_PORT = 5000


class _SerialLink:
    """ESP32 보드와의 USB CDC 시리얼 링크 — _TcpLink 와 동일 계약.

    @codesyncer-decision(2026-07-31): 펌웨어가 TCP(5000)와 USB 시리얼 양쪽에
      동일 ASCII 프로토콜을 열어두므로, PC(Wi-Fi)→공유기→보드 무선 홉 대신
      USB-C 직결을 표준 운용으로 채택(사용자 결정 — 이더넷 미사용).
      is_open/open/close/drain/request 계약을 _TcpLink 와 맞춰 ESP32EthValve
      본체·hw_manager cleanup 이 전송계층을 구분하지 않게 한다.
    @codesyncer-risk: 앱이 COM 포트를 점유하는 동안 ESP32 재플래시 불가 —
      펌웨어 업데이트 시 앱 종료 필수. USB 절전(선택적 일시 중단)이 CDC 를
      끊을 수 있어 허브 절전 해제 권장.
    """

    def __init__(self, com_port, baudrate=115200, connect_timeout=2.0):
        self.com_port = com_port
        self.baudrate = baudrate          # CDC 라 실효 무관 — 관례값
        self.connect_timeout = connect_timeout
        self.ser = None

    @property
    def is_open(self):
        return self.ser is not None and bool(getattr(self.ser, "is_open", False))

    def open(self):
        self.close()
        s = serial.Serial(self.com_port, self.baudrate, timeout=0.2,
                          write_timeout=self.connect_timeout)
        self.ser = s
        time.sleep(0.3)   # CDC 오픈 직후 안정화 (S3 USB-Serial-JTAG — DTR 리셋 없음)
        self.drain()

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def drain(self):
        if self.ser is None:
            return
        try:
            self.ser.reset_input_buffer()
        except Exception:
            self.close()

    def request(self, msg, expected, timeout=1.0):
        try:
            self.ser.write(msg.encode())
            deadline = time.monotonic() + timeout
            buf = ""
            while time.monotonic() < deadline:
                chunk = self.ser.read(256)
                if chunk:
                    buf += chunk.decode(errors="replace")
                    if expected in buf:
                        break
            return buf.strip()
        except (OSError, AttributeError, serial.SerialException):
            self.close()
            raise


class _TcpLink:
    """ESP32 보드와의 TCP 연결 래퍼.

    @codesyncer-decision: hw_manager cleanup 루프(3-1)가 기대하는 시리얼 호환
      계약(is_open / close())을 제공 — registry_classes 에 ESP32EthValve 만
      추가하면 기존 정리 코드가 무수정으로 동작한다.
    """

    def __init__(self, host, tcp_port, connect_timeout=2.0):
        self.host = host
        self.tcp_port = tcp_port
        self.connect_timeout = connect_timeout
        self.sock = None

    @property
    def is_open(self):
        return self.sock is not None

    def open(self):
        self.close()
        s = socket.create_connection((self.host, self.tcp_port),
                                     timeout=self.connect_timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        self.drain()  # 펌웨어 인사말(READY ...) 등 잔류 수신 비우기

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def drain(self):
        """수신 버퍼의 잔류 데이터를 버린다 (시리얼 reset_input_buffer 대응)."""
        if self.sock is None:
            return
        try:
            self.sock.settimeout(0.05)
            while True:
                chunk = self.sock.recv(1024)
                if not chunk:      # 상대측 종료 → 링크 사망
                    self.close()
                    return
        except socket.timeout:
            pass
        except Exception:
            self.close()

    def request(self, msg, expected, timeout=1.0):
        """한 줄 전송 후 expected 가 나타날 때까지 수신 (타임아웃까지).

        소켓 오류 시 링크를 닫고 예외를 올린다 — 호출측 재시도 루프에서
        재연결하도록.
        """
        try:
            self.sock.settimeout(0.2)
            self.sock.sendall(msg.encode())
            deadline = time.monotonic() + timeout
            buf = ""
            while time.monotonic() < deadline:
                try:
                    chunk = self.sock.recv(256)
                except socket.timeout:
                    continue
                if not chunk:      # 상대측 종료
                    self.close()
                    break
                buf += chunk.decode(errors="replace")
                if expected in buf:
                    break
            return buf.strip()
        except (OSError, AttributeError):
            self.close()
            raise


class ESP32EthValve:
    """ESP32-S3-ETH-8DI-8RO 8채널 릴레이 밸브 드라이버 (3-Way Valve, TCP)

    프로토콜: "<channel> <position>\\n" → ACK "OK <channel> <position>"
    valve_arduino.ArduinoValve 의 ASCII 프로토콜을 TCP 로 포팅 — 같은
    host:port 의 보드 1대를 최대 8채널이 공유한다.
    펌웨어: hardware/arduino/valve_relay_eth_8ch/valve_relay_eth_8ch.ino

    port 표기: "192.168.0.60:5000" / "192.168.0.60"(기본 5000) / "tcp://host:port"
              / **"COM7" (USB-C CDC 직결 — 2026-07-31 표준 운용)** — COM 이면
              _SerialLink, 아니면 _TcpLink 로 자동 판별 (프로토콜·의미론 동일)

    @codesyncer-decision: ArduinoValve 와 동일 의미론 유지 (2026-07-06
      fault-masking 감사 F1 보존) — position 은 ACK 확인 후에만 갱신,
      3회 재시도 소진 시 RuntimeError. 재시도마다 링크 재수립을 시도해
      TCP 특유의 stale 연결(케이블 재삽입 등)에서도 회복한다.
    @codesyncer-decision: 배선은 채널 순차(RO1=Group A, RO2=B, RO3=C,
      RO4=Group D, RO5=Outlet) — 구형 _CH_MAP 역순 보정은 두지 않는다.
    """

    # TCP 링크 공유 레지스트리 — ArduinoValve._serial_registry 와 동일 패턴
    _serial_registry = {}  # {"host:port": _TcpLink}
    _lock_registry = {}    # {"host:port": threading.Lock}

    def __init__(self, port, baudrate=None, channel=1):
        # baudrate 는 ArduinoValve 시그니처 호환용 (미사용)
        self.port = (port or "").strip()
        self.channel = channel
        self.position = 1
        self.is_connected = False
        self.is_serial = bool(re.match(r"(?i)^com\d+$", self.port))
        if self.is_serial:
            self.host, self.tcp_port = None, None
        else:
            self.host, self.tcp_port = self._parse_endpoint(self.port)

    # ── 엔드포인트 ────────────────────────────────────────────────
    @staticmethod
    def _parse_endpoint(port_str):
        s = (port_str or "").strip()
        if s.lower().startswith("tcp://"):
            s = s[6:]
        if ":" in s:
            host, _, p = s.rpartition(":")
            try:
                return host, int(p)
            except ValueError:
                return s, DEFAULT_TCP_PORT
        return s, DEFAULT_TCP_PORT

    def _is_mock(self):
        return (not self.port) or ("Mock" in self.port)

    def _key(self):
        return self.port.upper() if self.is_serial else f"{self.host}:{self.tcp_port}"

    def _get_link(self):
        return ESP32EthValve._serial_registry.get(self._key())

    def _get_lock(self):
        return ESP32EthValve._lock_registry.get(self._key())

    # ── 연결 ─────────────────────────────────────────────────────
    def connect(self):
        if self._is_mock():
            print(f"   [ESP32EthValve ch{self.channel}] Virtual Device (Mock)")
            self.is_connected = True
            return True

        key = self._key()
        try:
            link = ESP32EthValve._serial_registry.get(key)
            if link and link.is_open:
                print(f"   [ESP32EthValve ch{self.channel}] Sharing existing link {key}")
                self.is_connected = True
                return True

            if link is None:
                link = (_SerialLink(self._key()) if self.is_serial
                        else _TcpLink(self.host, self.tcp_port))
                ESP32EthValve._serial_registry[key] = link
                ESP32EthValve._lock_registry[key] = threading.Lock()

            link.open()
            print(f"   [ESP32EthValve ch{self.channel}] Connected: {key} [Master]")
            self.is_connected = True
            return True
        except Exception as e:
            print(f"   [ESP32EthValve ch{self.channel}] Connection Failed: {e}")
            self.is_connected = False
            return False

    # ── 밸브 전환 ─────────────────────────────────────────────────
    def set_position(self, pos):
        target = str(pos).upper()
        if target in ["1", "SOURCE", "REFILL", "WASTE"]:
            cmd, new_pos = "1", 1
        elif target in ["2", "REACTOR", "INFUSE", "COLLECT"]:
            cmd, new_pos = "2", 2
        else:
            return

        if self._is_mock():
            print(f"   [ESP32EthValve ch{self.channel}] (Virtual) -> position {cmd}")
            self.position = new_pos
            return

        link = self._get_link()
        lock = self._get_lock()

        if not link or not link.is_open:
            # 실제 엔드포인트인데 연결 안 됨 → 재연결 시도 (silent fail 방지)
            print(f"   [ESP32EthValve ch{self.channel}] ⚠ link not open ({self._key()}). "
                  f"Retrying connect...")
            if not self.connect():
                hint = ("USB-C 케이블/COM 포트 번호를 확인하세요." if self.is_serial
                        else "보드 전원/이더넷 케이블/IP 설정을 확인하세요.")
                raise RuntimeError(
                    f"[ESP32EthValve ch{self.channel}] {self._key()} 연결 실패 — "
                    f"set_position({cmd}) 명령을 보낼 수 없음. {hint}"
                )
            link = self._get_link()
            lock = self._get_lock()
            if not link or not link.is_open:
                raise RuntimeError(
                    f"[ESP32EthValve ch{self.channel}] 재연결 후에도 {self._key()} 열림 실패"
                )

        last_resp = ""
        with lock:
            msg = f"{self.channel} {cmd}\n"
            expected = f"OK {self.channel} {cmd}"
            for attempt in range(3):
                try:
                    if not link.is_open:
                        link.open()   # stale TCP 회복 — 시리얼과 달리 재수립 필요
                    link.drain()
                    resp = link.request(msg, expected)
                    last_resp = resp
                    print(f"   [ESP32EthValve ch{self.channel}] TX: {msg.strip()} → RX: {resp}")
                    if expected in resp:
                        self.position = new_pos   # ACK 확인 후에만 진실 갱신
                        return
                    if attempt < 2:
                        print(f"   [ESP32EthValve ch{self.channel}] Retry {attempt+1}/2...")
                        time.sleep(0.3)
                except Exception as e:
                    last_resp = f"(exception: {e})"
                    print(f"   [ESP32EthValve ch{self.channel}] Error: {e}")
                    if attempt < 2:
                        time.sleep(0.3)

        raise RuntimeError(
            f"[ESP32EthValve ch{self.channel}] set_position({cmd}) ACK 실패 (3회 재시도) — "
            f"마지막 응답: '{last_resp[:80]}'. 밸브가 이전 위치({self.position})에 남아있을 수 있음."
        )

    # ── 상태 조회 ─────────────────────────────────────────────────
    def get_position(self):
        return self.position

    def query_state(self):
        """보드 실측 릴레이 상태 [pos1..pos8] 조회 (디버깅/검증용, 엔진 미사용).

        실패 시 None — 호출측이 캐시(position)로 폴백.
        """
        if self._is_mock():
            return None
        link = self._get_link()
        lock = self._get_lock()
        if not link or not link.is_open or lock is None:
            return None
        try:
            with lock:
                link.drain()
                resp = link.request("STATE\n", "STATE")
            for line in resp.splitlines():
                parts = line.strip().split()
                if len(parts) == 9 and parts[0] == "STATE":
                    return [int(x) for x in parts[1:]]
        except Exception:
            pass
        return None

    # ── 해제 ─────────────────────────────────────────────────────
    def close(self):
        # 링크 공유 방식: 직접 닫지 않음 (hw_manager cleanup 이 레지스트리 정리)
        self.is_connected = False

    def disconnect(self):
        self.close()
