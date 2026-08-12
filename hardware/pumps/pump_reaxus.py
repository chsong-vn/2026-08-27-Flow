import serial
import time
import threading

class ReaxusPump:
    # [수정] name 인자 추가
    def __init__(self, port, name="Reaxus", baudrate=9600):
        self.name = name  # [수정] UI에서 참조할 이름 저장
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        self.lock = threading.Lock()

    def connect(self):
        try:
            self.ser = serial.Serial(
                self.port, 
                self.baudrate, 
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0, 
            )
            print(f"[{self.name}] Connected to {self.port}")
            return True
        except Exception as e:
            print(f"[{self.name}] Error: {e}")
            return False

    def _send_command(self, cmd_str):
        if not self.ser or not self.ser.is_open:
            return ""

        with self.lock:
            try:
                full_cmd = f"{cmd_str}\r"
                self.ser.write(full_cmd.encode())

                response = ""
                start_time = time.monotonic()
                timeout = 2.0 

                while True:
                    if time.monotonic() - start_time > timeout:
                        break
                    
                    if self.ser.in_waiting > 0:
                        char = self.ser.read().decode(errors='ignore')
                        if not char: break
                        response += char
                        if char == '/': 
                            break
                    else:
                        time.sleep(0.01)

                return response.strip()
            except Exception as e:
                print(f"[{self.name}] Comm Error: {e}")
                return ""

    def set_flow(self, flow_rate):
        try:
            flow_int = int(float(flow_rate) * 100)
            self._send_command(f"FI{flow_int}")
        except Exception as e:
            print(f"[{self.name}] Flow Set Error: {e}")

    def start(self):
        self.running = True
        self._send_command("RU")

    def stop(self):
        self.running = False
        self._send_command("ST")

    def get_pressure(self):
        if self.ser and self.ser.is_open:
            try:
                resp = self._send_command("PR")
                if resp.startswith("OK,"):
                    val_str = resp.split(',')[1].replace('/', '')
                    return float(val_str)
            except Exception:
                pass
        return 0.0