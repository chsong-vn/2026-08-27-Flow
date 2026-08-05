import serial
import time
import threading
import binascii

class RunzeSV07Valve:
    """
    Runze Fluidics 12-Way Selector Valve Driver (SV-07/SV-08 Series)
    통신 프로토콜: RS-485 (HEX Mode) - V1.4 매뉴얼 기준
    기본 설정: 9600 bps, 8, N, 1

    [Protocol Structure - RS-485 Sum Check]
    B0(STX) | B1(Addr) | B2(Func) | B3(P1) | B4(P2) | B5(ETX) | B6(SumL) | B7(SumH)
    0xCC    | 0x00     | 0x44     | Port   | 0x00   | 0xDD    | LowByte  | HighByte

    체크섬: B0~B5의 누적합 (2바이트, Little-Endian)

    [주요 명령어]
    0x44: 포트 이동 (자동 최단경로)
    0x45: 리셋 (원점 복귀)
    0x3E: 현재 위치 조회
    0x4A: 모터 상태 조회

    [RS-485 Daisy Chain 지원]
    같은 COM 포트에 여러 밸브 연결 시 address로 구분
    """

    # 시리얼 포트 공유를 위한 클래스 레벨 레지스트리 (Chemyx 방식)
    _serial_registry = {}  # {port: serial.Serial}
    _lock_registry = {}    # {port: threading.Lock}

    # 프로토콜 상수
    STX = 0xCC  # Frame Header
    ETX = 0xDD  # End of Frame

    # 명령어 코드
    CMD_MOVE_PORT = 0x44      # 포트 이동 (자동 최단경로)
    CMD_RESET = 0x45          # 리셋
    CMD_ORIGIN_RESET = 0x4F   # 원점 리셋
    CMD_QUERY_POS = 0x3E      # 현재 위치 조회
    CMD_QUERY_STATUS = 0x4A   # 모터 상태 조회
    CMD_STOP = 0x49           # 강제 정지

    # 응답 상태 코드
    STATUS_OK = 0x00
    STATUS_FRAME_ERROR = 0x01
    STATUS_PARAM_ERROR = 0x02
    STATUS_OPTO_ERROR = 0x03
    STATUS_MOTOR_BUSY = 0x04
    STATUS_MOTOR_STALL = 0x05
    STATUS_UNKNOWN_POS = 0x06
    STATUS_EXECUTING = 0xFE
    STATUS_UNKNOWN_ERROR = 0xFF

    def __init__(self, port, name="RunzeValve", baudrate=9600, address=0x00):
        self.port = port
        self.name = name
        self.baudrate = baudrate
        self.address = address  # RS-485 장비 주소 (0x00 ~ 0x7F)
        self.position = 1  # 현재 위치 캐싱 (기본값 1)

        # 실제 장비 연결 여부 확인용
        self.is_connected = False

    def _get_serial(self):
        """공유 시리얼 객체 반환"""
        return RunzeSV07Valve._serial_registry.get(self.port)

    def _get_lock(self):
        """공유 락 객체 반환"""
        return RunzeSV07Valve._lock_registry.get(self.port)

    def connect(self):
        """시리얼 포트 연결 (RS-485 Daisy Chain 지원)"""
        # 테스트용 가상 포트 처리
        if not self.port or "Mock" in self.port or "COM_Mock" in self.port:
            print(f"   [{self.name}] Virtual Device (Mock) initialized on {self.port}")
            self.is_connected = True
            return True

        try:
            # 이미 열린 포트가 있으면 공유
            if self.port in RunzeSV07Valve._serial_registry:
                print(f"   [{self.name}] Sharing existing port {self.port} (Addr: 0x{self.address:02X})")
                self.is_connected = True
                time.sleep(0.3)
                self._query_position()
                return True

            # 새 포트 열기
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            )

            RunzeSV07Valve._serial_registry[self.port] = ser
            RunzeSV07Valve._lock_registry[self.port] = threading.Lock()

            time.sleep(0.5)  # 초기화 대기

            if ser.is_open:
                print(f"   [{self.name}] RS-485 Connected: {self.port} (Addr: 0x{self.address:02X}) [Master]")
                self.is_connected = True
                self._query_position()
                return True
        except Exception as e:
            print(f"   [{self.name}] Connection Failed: {e}")
            self.is_connected = False
            return False

    def disconnect(self):
        """연결 해제 (포트 공유 시 닫지 않음)"""
        self.is_connected = False
        # 포트 공유 방식에서는 마지막 사용자가 닫도록 함 (또는 수동으로)

    def set_position(self, port_num):
        """
        밸브 포트 변경 명령 전송 (RS-485 프로토콜)
        :param port_num: 1 ~ 12 (모델에 따라 다름)

        명령어 0x44: 자동 최단경로로 포트 이동
        파라미터: B3=포트번호, B4=0x00
        """
        target = int(port_num)
        if not (1 <= target <= 12):
            print(f"   [{self.name}] Error: Port {target} is out of range (1-12)")
            return

        # 공유 시리얼/락 가져오기
        ser = self._get_serial()
        lock = self._get_lock()

        # 가상 장비 처리
        if not ser or not ser.is_open:
            self.position = target
            print(f"   [{self.name}] (Virtual) Valve moved to Port {target}")
            return

        # RS-485 명령어 생성: 0x44 (자동 최단경로 포트 이동)
        cmd_bytes = self._build_command(self.CMD_MOVE_PORT, target, 0x00)

        with lock:
            try:
                # 버퍼 비우기
                ser.reset_input_buffer()

                # 명령 전송
                ser.write(cmd_bytes)
                print(f"   [{self.name}] TX: {binascii.hexlify(cmd_bytes).decode()}")

                # 응답 대기 (8바이트)
                response = ser.read(8)

                if len(response) == 8:
                    status = self._parse_response(response)

                    if status == self.STATUS_OK:
                        self.position = target
                        print(f"   [{self.name}] Switched to Port {target}")
                    elif status == self.STATUS_EXECUTING:
                        # 동작 중 - 완료 대기
                        print(f"   [{self.name}] Moving to Port {target}...")
                        self._wait_for_completion()
                        self.position = target
                        print(f"   [{self.name}] Switched to Port {target}")
                    else:
                        print(f"   [{self.name}] Error: Status 0x{status:02X}")
                else:
                    print(f"   [{self.name}] Warning: No Response or incomplete packet (got {len(response)} bytes)")

            except Exception as e:
                print(f"   [{self.name}] Communication Error: {e}")

    def _build_command(self, func_code, p1, p2):
        """
        RS-485 프로토콜에 맞춰 HEX 커맨드 바이트 배열 생성
        Format: [STX, Addr, Func, P1, P2, ETX, SumLow, SumHigh]

        체크섬: B0~B5의 누적합 (2바이트, Little-Endian)
        """
        # 프레임 구성 (B0 ~ B5)
        frame = [
            self.STX,       # B0: Frame Header
            self.address,   # B1: Device Address
            func_code,      # B2: Function Code
            p1,             # B3: Parameter 1 (Low byte)
            p2,             # B4: Parameter 2 (High byte)
            self.ETX        # B5: End of Frame
        ]

        # 체크섬 계산: B0 ~ B5의 누적합
        checksum = sum(frame) & 0xFFFF  # 16비트 마스크
        sum_low = checksum & 0xFF
        sum_high = (checksum >> 8) & 0xFF

        # 완성된 패킷 (8바이트)
        packet = frame + [sum_low, sum_high]
        return bytearray(packet)

    def _parse_response(self, response):
        """
        응답 패킷 파싱
        Format: [STX, Addr, Status, P1, P2, ETX, SumLow, SumHigh]
        :return: 상태 코드 (B2)
        """
        if len(response) != 8:
            return self.STATUS_UNKNOWN_ERROR

        # 프레임 검증
        if response[0] != self.STX or response[5] != self.ETX:
            print(f"   [{self.name}] Invalid frame: {binascii.hexlify(response).decode()}")
            return self.STATUS_FRAME_ERROR

        # 체크섬 검증
        expected_sum = sum(response[:6]) & 0xFFFF
        received_sum = response[6] | (response[7] << 8)
        if expected_sum != received_sum:
            print(f"   [{self.name}] Checksum mismatch: expected 0x{expected_sum:04X}, got 0x{received_sum:04X}")
            return self.STATUS_FRAME_ERROR

        # 상태 코드 반환
        return response[2]

    def _wait_for_completion(self, timeout=5.0):
        """
        모터 동작 완료 대기
        0x4A 명령으로 모터 상태를 폴링하여 STATUS_OK가 될 때까지 대기
        """
        ser = self._get_serial()
        if not ser:
            return False

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            cmd_bytes = self._build_command(self.CMD_QUERY_STATUS, 0x00, 0x00)
            ser.write(cmd_bytes)
            response = ser.read(8)

            if len(response) == 8:
                status = response[2]
                if status == self.STATUS_OK:
                    return True
                elif status == self.STATUS_EXECUTING or status == self.STATUS_MOTOR_BUSY:
                    time.sleep(0.1)
                    continue
                else:
                    print(f"   [{self.name}] Motor error: Status 0x{status:02X}")
                    return False
            time.sleep(0.1)

        print(f"   [{self.name}] Timeout waiting for motor completion")
        return False

    def _query_position(self):
        """
        현재 밸브 위치 조회 (0x3E 명령)
        """
        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open:
            return self.position

        cmd_bytes = self._build_command(self.CMD_QUERY_POS, 0x00, 0x00)

        with lock:
            try:
                ser.reset_input_buffer()
                ser.write(cmd_bytes)
                response = ser.read(8)

                if len(response) == 8:
                    status = response[2]
                    if status == self.STATUS_OK:
                        # B3에 현재 포트 번호가 담겨있음
                        self.position = response[3]
                        print(f"   [{self.name}] Current position: Port {self.position}")
                    else:
                        print(f"   [{self.name}] Query position error: Status 0x{status:02X}")
            except Exception as e:
                print(f"   [{self.name}] Query position error: {e}")

        return self.position

    def reset(self):
        """
        밸브 리셋 (원점 복귀)
        리셋 후 포트 1과 최대 포트 사이에 위치함 (모든 포트 차단)
        """
        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open:
            self.position = 0  # 0 = 리셋 위치
            print(f"   [{self.name}] (Virtual) Valve reset to origin")
            return

        cmd_bytes = self._build_command(self.CMD_RESET, 0x00, 0x00)

        with lock:
            try:
                ser.reset_input_buffer()
                ser.write(cmd_bytes)
                print(f"   [{self.name}] TX: {binascii.hexlify(cmd_bytes).decode()}")

                response = ser.read(8)

                if len(response) == 8:
                    status = self._parse_response(response)
                    if status == self.STATUS_OK:
                        self.position = 0
                        print(f"   [{self.name}] Reset complete")
                    elif status == self.STATUS_EXECUTING:
                        self._wait_for_completion()
                        self.position = 0
                        print(f"   [{self.name}] Reset complete")
                    else:
                        print(f"   [{self.name}] Reset error: Status 0x{status:02X}")
            except Exception as e:
                print(f"   [{self.name}] Reset error: {e}")

    def stop(self):
        """
        강제 정지 (0x49 명령)
        """
        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open:
            print(f"   [{self.name}] (Virtual) Valve stopped")
            return

        cmd_bytes = self._build_command(self.CMD_STOP, 0x00, 0x00)

        with lock:
            try:
                ser.reset_input_buffer()
                ser.write(cmd_bytes)
                _ = ser.read(8)  # 응답 무시 (긴급 정지)
                print(f"   [{self.name}] Emergency stop sent")
            except Exception as e:
                print(f"   [{self.name}] Stop error: {e}")

    def close(self):
        self.disconnect()

    def get_position(self):
        """현재 저장된 위치 반환 (캐시된 값)"""
        return self.position

    def query_position(self):
        """장비에서 현재 위치 조회 후 반환"""
        return self._query_position()

    # =========================================================================
    # Factory Commands (주소 설정 등)
    # =========================================================================
    def _build_factory_command(self, func_code, new_value):
        """
        Factory Command 생성 (14바이트)
        Format: [STX, Addr, Func, PWD(4), Param(4), ETX, SumL, SumH]
        Password: 0xFF, 0xEE, 0xBB, 0xAA
        """
        frame = [
            self.STX,           # B0
            self.address,       # B1: 현재 주소
            func_code,          # B2: Function Code
            0xFF, 0xEE, 0xBB, 0xAA,  # B3-B6: Password
            new_value, 0x00, 0x00, 0x00,  # B7-B10: Parameter (little-endian)
            self.ETX            # B11
        ]

        checksum = sum(frame) & 0xFFFF
        sum_low = checksum & 0xFF
        sum_high = (checksum >> 8) & 0xFF

        packet = frame + [sum_low, sum_high]
        return bytearray(packet)

    def set_device_address(self, new_address):
        """
        장비 RS-485 주소 변경 (Factory Command 0x00)

        주의:
        - 이 명령은 장비를 개별로 연결한 상태에서 실행해야 함
        - 변경 후 장비 재시작 필요

        :param new_address: 새 주소 (0x00 ~ 0x7F)
        """
        if not (0x00 <= new_address <= 0x7F):
            print(f"   [{self.name}] Error: Address must be 0x00 ~ 0x7F")
            return False

        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open:
            print(f"   [{self.name}] (Virtual) Address would be set to 0x{new_address:02X}")
            self.address = new_address
            return True

        cmd_bytes = self._build_factory_command(0x00, new_address)

        with lock:
            try:
                ser.reset_input_buffer()
                ser.write(cmd_bytes)
                print(f"   [{self.name}] TX (Set Address): {binascii.hexlify(cmd_bytes).decode()}")

                response = ser.read(8)

                if len(response) == 8:
                    status = self._parse_response(response)
                    if status == self.STATUS_OK:
                        print(f"   [{self.name}] Address changed: 0x{self.address:02X} -> 0x{new_address:02X}")
                        print(f"   [{self.name}] 장비 전원을 재시작하세요.")
                        self.address = new_address
                        return True
                    else:
                        print(f"   [{self.name}] Set address error: Status 0x{status:02X}")
                        return False
                else:
                    print(f"   [{self.name}] No response to set address command")
                    return False

            except Exception as e:
                print(f"   [{self.name}] Set address error: {e}")
                return False

    def set_baudrate(self, baudrate_code, comm_type="RS485"):
        """
        통신 속도 변경 (Factory Command)

        :param baudrate_code: 0=9600, 1=19200, 2=38400, 3=57600, 4=115200
        :param comm_type: "RS232" or "RS485"
        """
        func_code = 0x02 if comm_type == "RS485" else 0x01

        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open:
            print(f"   [{self.name}] (Virtual) Baudrate would be set to code {baudrate_code}")
            return True

        cmd_bytes = self._build_factory_command(func_code, baudrate_code)

        with lock:
            try:
                ser.reset_input_buffer()
                ser.write(cmd_bytes)
                response = ser.read(8)

                if len(response) == 8 and response[2] == self.STATUS_OK:
                    baud_map = {0: 9600, 1: 19200, 2: 38400, 3: 57600, 4: 115200}
                    print(f"   [{self.name}] Baudrate changed to {baud_map.get(baudrate_code, 'Unknown')}")
                    return True
                return False
            except Exception as e:
                print(f"   [{self.name}] Set baudrate error: {e}")
                return False