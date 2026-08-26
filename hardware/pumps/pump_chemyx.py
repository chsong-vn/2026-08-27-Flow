import serial
import time
import threading

class ChemyxPump:
    """
    Chemyx Fusion Series Driver supporting RS-485 Daisy Chain.
    """
    
    _serial_registry = {}  
    _lock_registry = {}    

    def __init__(self, port, baudrate=9600, diameter=14.5, pump_id=0):
        self.port = port
        self.baudrate = baudrate
        self.diameter = float(diameter)
        self.pump_id = int(pump_id)
        self.mode = "INFUSE" 

    def connect(self):
        try:
            if self.port in ChemyxPump._serial_registry:
                print(f"   [Chemyx ID:{self.pump_id}] Sharing existing port {self.port}")
                time.sleep(0.5)
                # @codesyncer-decision: 공유 포트 펌프도 반드시 초기화 명령 전송
                # 이전에는 set_mode만 했으나, diameter/units가 미설정되어
                # 펌프 내부에 남아있던 이전 값(예: 28mm)이 사용되는 버그 있었음
                self._send_cmd("stop")  # @codesyncer-decision: 이전 세션 잔류 동작 정지
                self.set_diameter(self.diameter)
                self.set_units("mL/min")
                self.set_mode("infuse")
                return True

            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5
            )
            
            ChemyxPump._serial_registry[self.port] = ser
            ChemyxPump._lock_registry[self.port] = threading.RLock()
            
            time.sleep(2)
            
            if ser.is_open:
                print(f"   [Chemyx] Opened new port {self.port} (Master)")
                self._send_cmd("stop")  # @codesyncer-decision: 이전 세션 잔류 동작 정지
                self.set_diameter(self.diameter)
                self.set_units("mL/min") 
                self.set_mode("infuse")
                return True
            return False

        except Exception as e:
            print(f"   [Chemyx ID:{self.pump_id}] Connection Error: {e}")
            return False

    def disconnect(self):
        try:
            self.stop()
        except Exception:
            pass
        ser = ChemyxPump._serial_registry.pop(self.port, None)
        ChemyxPump._lock_registry.pop(self.port, None)
        if ser is not None:
            try:
                if ser.is_open:
                    ser.close()
            except Exception:
                pass

    def _get_serial(self):
        return ChemyxPump._serial_registry.get(self.port)

    def _get_lock(self):
        return ChemyxPump._lock_registry.get(self.port)

    def _send_cmd(self, command_str, wait_response=True, retries=2):
        """
        RS485 명령 전송 (재시도 로직 포함)
        @codesyncer-decision: 응답 없으면 최대 retries회 재시도 (0.5초 간격)
        - RS-485 버스 충돌/노이즈로 명령 누락 시 자동 복구
        """
        ser = self._get_serial()
        lock = self._get_lock()

        if not ser or not ser.is_open or not lock:
            return None

        _io_error = False
        for attempt in range(retries + 1):
            with lock:
                try:
                    ser.reset_input_buffer()
                    full_cmd = f"{self.pump_id} {command_str}\r"
                    # @codesyncer-decision: RS-485 명령 로그 — set rate/volume/start만 출력 (디버그용)
                    if any(k in command_str for k in ("set rate", "set volume", "start", "stop")):
                        print(f"   [RS485 TX] ID:{self.pump_id} → '{command_str}'")
                    ser.write(full_cmd.encode('ascii'))

                    if not wait_response:
                        time.sleep(0.08)
                        return None

                    # 응답 읽기 (최대 1초 대기)
                    response = b''
                    start_time = time.monotonic()
                    while (time.monotonic() - start_time) < 1.0:
                        if ser.in_waiting > 0:
                            response += ser.read(ser.in_waiting)
                            time.sleep(0.05)
                        else:
                            if response:
                                break
                            time.sleep(0.1)

                    time.sleep(0.08)  # 펌프 간 딜레이 (9600bps: 20byte≈21ms + 내부 처리 → 80ms 안전 마진)

                    if response:
                        try:
                            decoded = response.decode('ascii').strip()
                        except:
                            decoded = f"[HEX] {response.hex()}"
                        if any(k in command_str for k in ("set rate", "set volume")):
                            print(f"   [RS485 RX] ID:{self.pump_id} ← '{decoded}'")
                        self.is_connected = True   # 통신 성공 = 연결 상태 자가 회복
                        return decoded

                except (serial.SerialException, OSError) as e:
                    # 핸들 무효류(USB 순단 등) — 재시도 후에도 남으면 OFFLINE 자백
                    _io_error = True
                    print(f"   [Chemyx ID:{self.pump_id}] Send Error(IO): {e}")
                except Exception as e:
                    print(f"   [Chemyx ID:{self.pump_id}] Send Error: {e}")

            # 응답 없음 → 재시도
            if attempt < retries:
                print(f"   [Chemyx ID:{self.pump_id}] No response for '{command_str}', retry {attempt+1}/{retries}")
                time.sleep(0.5)

        # @codesyncer-decision(2026-08-25, 끊김 가시화): I/O 예외(핸들 무효)로 최종
        #   실패한 경우에만 is_connected=False — 단순 무응답(RS-485 경합)은 일시적일
        #   수 있어 플래그를 건드리지 않는다. 대시보드 상태 패널이 1초 주기로 이
        #   플래그를 읽어 OFFLINE(적색)을 표시한다.
        if _io_error:
            self.is_connected = False
        print(f"   [Chemyx ID:{self.pump_id}] FAILED after {retries+1} attempts: '{command_str}'")
        return None

    # --- API Methods ---

    def set_units(self, units):
        unit_map = {'mL/min': 0, 'mL/hr': 1, 'μL/min': 2, 'μL/hr': 3}
        val = unit_map.get(str(units), 0)
        self._send_cmd(f"set units {val}")

    def set_diameter(self, diameter_mm):
        self._send_cmd(f"set diameter {diameter_mm}")

    def set_rate(self, flow_rate):
        return self._send_cmd(f"set rate {flow_rate}")

    def set_volume(self, volume_ml):
        return self._send_cmd(f"set volume {volume_ml}")
        
    def set_delay(self, delay_min):
        self._send_cmd(f"set delay {delay_min}")

    def set_mode(self, mode_str):
        """
        Set Pump Mode explicitly
        """
        mode = mode_str.lower()
        if "infuse" in mode:
            resp = self._send_cmd("set mode 0")
            self.mode = "INFUSE"
            print(f"   [Chemyx ID:{self.pump_id}] SET MODE → INFUSE {'✓' if resp else '✗'}")
        elif "withdraw" in mode:
            resp = self._send_cmd("set mode 1")
            self.mode = "WITHDRAW"
            print(f"   [Chemyx ID:{self.pump_id}] SET MODE → WITHDRAW {'✓' if resp else '✗'}")

    def start(self, mode=None):
        """
        펌프 시작.
        - mode=None: 현재 내부 모드로 실행
        - mode=0: infuse
        - mode=1: withdraw

        @codesyncer-decision: "start {mode}" → "set mode + start" 분리 전송
        - 이유: "start 1" 구문을 무시하는 펌웨어 존재
        - set_mode()로 방향 명시 설정 후 "start"만 전송 → 안정적
        @codesyncer-decision: start는 retries=0 (재시도 안함)
        - 이유: 첫 시도가 도달했는데 응답만 누락된 경우, 재시도 시 volume counter 리셋 위험
        @codesyncer-decision: RLock 기반 atomic 실행
        - set_mode + start를 하나의 lock 안에서 실행 → 중간 명령 개입 방지
        - Lock → RLock 변경으로 _send_cmd 내부 재진입 허용
        """
        lock = self._get_lock()
        if not lock:
            return None

        with lock:
            if mode is not None:
                if mode == 1:
                    self.set_mode("withdraw")
                else:
                    self.set_mode("infuse")
            resp = self._send_cmd("start", retries=0)

        print(f"   [Chemyx ID:{self.pump_id}] START ({self.mode}) → {'OK' if resp else 'NO RESP'}")
        return resp

    def stop(self):
        return self._send_cmd("stop")
        
    def pause(self):
        self._send_cmd("pause")
        
    def read_rate(self):
        """현재 설정된 유속(rate) 값을 읽어옴 (Read-back 검증용)"""
        resp = self._send_cmd("view rate")
        if resp:
            try:
                return float(resp)
            except (ValueError, TypeError):
                pass
        return None

    def read_volume(self):
        """현재 설정된 볼륨(volume) 값을 읽어옴 (Read-back 검증용)"""
        resp = self._send_cmd("view volume")
        if resp:
            try:
                return float(resp)
            except (ValueError, TypeError):
                pass
        return None

    def is_running(self):
        """펌프가 현재 동작 중인지 확인 (dispensed volume 변화 감지)
        @return: True=동작 중, False=정지, None=조회 불가
        """
        v1 = self.get_dispensed_volume()
        if v1 is None:
            return None
        time.sleep(0.3)
        v2 = self.get_dispensed_volume()
        if v2 is None:
            return None
        return abs(v2 - v1) > 0.005

    def get_dispensed_volume(self):
        """펌프가 현재까지 토출/흡입한 양을 조회"""
        resp = self._send_cmd("dispensed volume")
        if resp:
            try:
                return abs(float(resp))
            except (ValueError, TypeError):
                pass
        return None

    def is_stopped(self):
        """펌프 동작 완료 여부 확인 (dispensed volume이 연속 2회 동일하면 정지)"""
        v1 = self.get_dispensed_volume()
        if v1 is None:
            return None  # 조회 불가
        time.sleep(0.3)
        v2 = self.get_dispensed_volume()
        if v2 is None:
            return None
        return abs(v2 - v1) < 0.01  # 0.3초간 변화 없으면 정지

    def get_pressure(self):
        return 0.0