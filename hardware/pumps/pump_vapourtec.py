# --- START OF FILE hardware/pumps/pump_vapourtec.py ---
import serial
import threading
import time

class VapourtecPump:
    """
    Vapourtec SF-10 Pump Driver
    기능: Flow, Regulator, Dose, Gas, Ramp, Oscillation 모드 지원                                                                                    
    """
    def __init__(self, port, baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        self.lock = threading.Lock()
        self.name = "Vapourtec SF-10"

    def connect(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=True,
                timeout=1.0
            )
            print(f"[Vapourtec] Connected on {self.port}")
            # 초기화 커맨드
            self._send_cmd("REMOTEEN") # 리모트 활성화
            return True
        except Exception as e:
            print(f"[Vapourtec] Connection Error: {e}")
            return False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.stop()
            self.ser.close()

    def _send_cmd(self, cmd):
        if not self.ser or not self.ser.is_open:
            return "Error: Not Connected"
        
        with self.lock:
            try:
                full_cmd = f"{cmd}\r"
                self.ser.write(full_cmd.encode('ascii'))
                
                # 응답 읽기 (간단한 구현)
                response = ""
                # Vapourtec은 응답이 즉시 오지 않을 수 있으므로 약간의 대기 혹은 loop 필요할 수 있음
                # 여기서는 간단히 버퍼 확인
                time.sleep(0.05) 
                if self.ser.in_waiting:
                    response = self.ser.read_until(b'\r').decode('ascii', errors='ignore').strip()
                return response
            except Exception as e:
                print(f"[Vapourtec] Send Error: {e}")
                return str(e)

    # --- Basic Control ---
    def start(self):
        self.running = True
        return self._send_cmd("START")

    def stop(self):
        self.running = False
        return self._send_cmd("STOP")

    def set_valve(self, position):
        # position: "A" or "B"
        return self._send_cmd(f"VALVE {position}")

    # --- Mode Settings ---
    def set_mode_flow(self, rate):
        # 1.0 미만은 소수점 3자리, 이상은 2자리 권장이나 3자리로 통일해도 펌웨어가 처리함
        self._send_cmd("MODE FLOW")
        return self._send_cmd(f"SETFLOW {rate:.3f}")

    def set_mode_regulator(self, pressure_bar):
        self._send_cmd("MODE REG")
        return self._send_cmd(f"SETREG {pressure_bar:.1f}")
    
    def purge(self):
        return self._send_cmd("PURGE")

    def set_mode_dose(self, rate, volume_ml):
        self._send_cmd("MODE DOSE")
        self._send_cmd(f"SETFLOW {rate:.3f}")
        return self._send_cmd(f"SETDOSE {volume_ml:.1f}")

    def set_mode_gas(self, sccm):
        self._send_cmd("MODE GAS")
        return self._send_cmd(f"SETGASFLOW {sccm:.1f}")

    def set_mode_ramp(self, start_rate, end_rate, time_min):
        self._send_cmd("MODE RAMP")
        return self._send_cmd(f"SETRAMP {start_rate:.2f} {end_rate:.2f} {time_min:.1f}")

    def set_mode_oscillation(self, speed, disp_ul):
        self._send_cmd("MODE OSC")
        return self._send_cmd(f"SETOSC {speed:.2f} {disp_ul}")

    # --- Compatibility for General Engine ---
    def set_flow(self, flow_rate):
        """Standard interface for FlowEngine"""
        self.set_mode_flow(flow_rate)
        
    def get_pressure(self):
        # SF-10은 주기적으로 압력을 보내주거나 질의해야 함.
        # 여기서는 단순화를 위해 0 반환 혹은 PR 명령 구현 필요
        return 0.0