import serial
import time
import threading

class ArduinoValve:
    """
    Arduino 4채널 릴레이 밸브 드라이버 (3-Way Valve)

    프로토콜: "<channel> <position>\n"
    같은 COM 포트에 1대의 Arduino가 4채널 릴레이를 제어

    @codesyncer-decision: RunzeSV07Valve와 동일한 _serial_registry 패턴으로 포트 공유
    """

    # 시리얼 포트 공유를 위한 클래스 레벨 레지스트리
    _serial_registry = {}  # {port: serial.Serial}
    _lock_registry = {}    # {port: threading.Lock}

    # @codesyncer-decision: 논리 채널 → 실제 릴레이 채널 매핑 (배선 순서 보정)
    # 물리 배선이 역순으로 연결되어 있어 소프트웨어에서 보정
    _CH_MAP = {1: 4, 2: 3, 3: 2, 4: 1}

    def __init__(self, port, baudrate=9600, channel=1):
        self.port = port
        self.baudrate = baudrate
        self.channel = channel  # 릴레이 채널 번호 (1~4)
        self.position = 1
        self.is_connected = False

    def _get_serial(self):
        return ArduinoValve._serial_registry.get(self.port)

    def _get_lock(self):
        return ArduinoValve._lock_registry.get(self.port)

    def connect(self):
        # 가상 포트 처리
        if not self.port or "Mock" in self.port or "COM_Mock" in self.port:
            print(f"   [ArduinoValve ch{self.channel}] Virtual Device (Mock)")
            self.is_connected = True
            return True

        try:
            # 이미 열린 포트가 있으면 공유
            if self.port in ArduinoValve._serial_registry:
                ser = ArduinoValve._serial_registry[self.port]
                if ser and ser.is_open:
                    print(f"   [ArduinoValve ch{self.channel}] Sharing existing port {self.port}")
                    self.is_connected = True
                    return True

            # 새 포트 열기
            ser = serial.Serial(self.port, self.baudrate, timeout=1)
            ArduinoValve._serial_registry[self.port] = ser
            ArduinoValve._lock_registry[self.port] = threading.Lock()
            time.sleep(2.0)  # Arduino 리셋 대기

            if ser.is_open:
                print(f"   [ArduinoValve ch{self.channel}] Connected: {self.port} [Master]")
                self.is_connected = True
                return True
        except Exception as e:
            print(f"   [ArduinoValve ch{self.channel}] Connection Failed: {e}")
            self.is_connected = False
            return False

    def set_position(self, pos):
        # @codesyncer-decision: fault-masking 제거 (2026-07-06 감사 F1)
        # - 기존: self.position 을 통신 '전에' 의도값으로 대입 + ACK 3회 실패 시
        #   raise 없이 조용히 반환 → Timer 는 FIRED 성공 로그, get_position 은
        #   거짓 보고 → 분획 전량 waste 유실이 무증상으로 발생 가능했음
        # - 수정: position 은 ACK 확인 후에만 갱신, 재시도 소진 시 RuntimeError.
        #   (CollectionTimer 액션 래퍼가 잡아 'FAILED' 로그 = 관측 가능;
        #    엔진 워커 경로는 상위 try 가 잡아 시퀀스 정지 = 조용한 유실보다 안전)
        target = str(pos).upper()
        if target in ["1", "SOURCE", "REFILL", "WASTE"]:
            cmd, new_pos = "1", 1
        elif target in ["2", "REACTOR", "INFUSE", "COLLECT"]:
            cmd, new_pos = "2", 2
        else: return

        ser = self._get_serial()
        lock = self._get_lock()

        # 가상 장비 처리 — 실제 COM 포트 설정에서 연결 실패 시 silent fail 방지
        if not ser or not ser.is_open:
            is_mock = (not self.port) or ("Mock" in self.port) or ("COM_Mock" in self.port)
            if is_mock:
                print(f"   [ArduinoValve ch{self.channel}] (Virtual) -> position {cmd}")
                self.position = new_pos
                return
            # 실제 포트인데 연결 안 됨 → 재연결 시도
            print(f"   [ArduinoValve ch{self.channel}] ⚠ port not open ({self.port}). Retrying connect...")
            if not self.connect():
                raise RuntimeError(
                    f"[ArduinoValve ch{self.channel}] port {self.port} 연결 실패 — "
                    f"set_position({cmd}) 명령을 보낼 수 없음. "
                    f"다른 프로세스가 포트를 점유 중인지 확인하세요."
                )
            ser = self._get_serial()
            lock = self._get_lock()
            if not ser or not ser.is_open:
                raise RuntimeError(
                    f"[ArduinoValve ch{self.channel}] 재연결 후에도 port {self.port} 열림 실패"
                )

        last_resp = ""
        with lock:
            real_ch = ArduinoValve._CH_MAP.get(self.channel, self.channel)
            msg = f"{real_ch} {cmd}\n"
            expected = f"OK {real_ch} {cmd}"
            for attempt in range(3):
                try:
                    ser.reset_input_buffer()
                    ser.write(msg.encode())
                    time.sleep(0.3)
                    resp = ser.read_all().decode(errors='replace').strip()
                    last_resp = resp
                    print(f"   [ArduinoValve ch{self.channel}] TX: {msg.strip()} → RX: {resp}")
                    if expected in resp:
                        self.position = new_pos   # ACK 확인 후에만 진실 갱신
                        return
                    # 응답 불일치 → 재시도
                    if attempt < 2:
                        print(f"   [ArduinoValve ch{self.channel}] Retry {attempt+1}/2...")
                        time.sleep(0.3)
                except Exception as e:
                    last_resp = f"(exception: {e})"
                    print(f"   [ArduinoValve ch{self.channel}] Error: {e}")
                    if attempt < 2:
                        time.sleep(0.3)

        raise RuntimeError(
            f"[ArduinoValve ch{self.channel}] set_position({cmd}) ACK 실패 (3회 재시도) — "
            f"마지막 응답: '{last_resp[:80]}'. 밸브가 이전 위치({self.position})에 남아있을 수 있음."
        )

    def close(self):
        # 포트 공유 방식: 직접 닫지 않음
        self.is_connected = False

    def disconnect(self):
        self.close()

    def get_position(self):
        return self.position
