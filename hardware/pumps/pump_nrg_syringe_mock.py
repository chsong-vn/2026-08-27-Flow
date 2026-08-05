"""
MockNRGSyringePump - NRGSyringePump 의 가상 폴백.
실제 하드웨어 없이 GUI/엔진 동작을 검증할 때 사용.

# @codesyncer-decision: pump_mock.py 패턴 (간결, 상태 출력만) + NRG 고유 메서드 stub.
"""
import time
import random


class MockNRGSyringePump:
    _serial_registry = {}  # cleanup_hardware 호환용 (항상 비어있음)
    _lock_registry = {}

    def __init__(self, name="NRG_Mock", **kwargs):
        self.name = name
        self.port = kwargs.get("port", "COM_Mock")
        self.pump_id = kwargs.get("pump_id", 0)
        self.diameter = kwargs.get("syringe_diameter_mm", 4.6066)
        self.syringe_volume_ul = kwargs.get("syringe_volume_ul", 1000.0)
        self.max_flowrate = kwargs.get("max_flowrate_ml_min", 5.0)
        self.main_valve_enabled = kwargs.get("main_valve_enabled", False)
        self.aux_valve_enabled = kwargs.get("aux_valve_enabled", False)

        self.mode = "INFUSE"
        self.running = False
        self._pending_volume_ul = 0.0
        self._current_rate = 1.0
        self._volume_in_syringe_ul = self.syringe_volume_ul  # 초기엔 가득 차있다고 가정
        self._last_pumped_ul = 0.0

    # 연결/해제
    def connect(self):
        print(f"   [System] {self.name} (NRG Mock) Connected.")
        return True

    def disconnect(self):
        print(f"   [System] {self.name} (NRG Mock) Disconnected.")

    # Chemyx-호환
    def set_units(self, units): pass

    def set_diameter(self, mm):
        self.diameter = float(mm)
        return "ok"

    def set_rate(self, rate):
        self._current_rate = min(float(rate), self.max_flowrate)
        return "ok"

    def set_volume(self, volume_ml):
        self._pending_volume_ul = float(volume_ml) * 1000.0
        return "ok"

    def set_delay(self, m): pass

    def set_mode(self, mode_str):
        self.mode = "WITHDRAW" if "withdraw" in str(mode_str).lower() else "INFUSE"

    def start(self, mode=None):
        if mode == 0:
            self.set_mode("infuse")
        elif mode == 1:
            self.set_mode("withdraw")
        self.running = True
        ul = self._pending_volume_ul
        self._last_pumped_ul = ul
        # 가상 펌프 동작 시간
        ml = ul / 1000.0
        sec = ml / max(self._current_rate, 0.01) * 60.0
        sec = min(sec, 0.5)  # mock 은 빠르게
        time.sleep(sec)
        if self.mode == "WITHDRAW":
            self._volume_in_syringe_ul = min(self.syringe_volume_ul, self._volume_in_syringe_ul + ul)
        else:
            self._volume_in_syringe_ul = max(0.0, self._volume_in_syringe_ul - ul)
        self.running = False
        print(f"   [System] {self.name} PUMP ({self.mode}, {ul:.0f} uL) → vol={self._volume_in_syringe_ul:.0f} uL")
        return "OK"

    def stop(self):
        self.running = False
        return "OK"

    def pause(self):
        return self.stop()

    def read_rate(self):
        return self._current_rate

    def read_volume(self):
        return self._volume_in_syringe_ul / 1000.0

    def is_running(self):
        return self.running

    def is_stopped(self):
        return not self.running

    def get_dispensed_volume(self):
        return self._last_pumped_ul / 1000.0

    def get_pressure(self):
        return 0.0

    # NRG-only
    def pump_volume(self, ul, direction="infuse"):
        self.set_volume(ul / 1000.0)
        self.set_mode(direction)
        return self.start()

    def withdraw(self, ul):
        return self.pump_volume(ul, direction="withdraw")

    def dispense(self, ul):
        return self.pump_volume(ul, direction="infuse")

    def zero_fill(self):
        self._volume_in_syringe_ul = self.syringe_volume_ul
        return "ok"

    def zero_empty(self):
        self._volume_in_syringe_ul = 0.0
        return "ok"

    def zero_in_place(self):
        return "ok"

    def set_main_valve(self, p): return "ok"
    def set_aux_valve(self, p): return "ok"
    def get_main_valve_actual(self): return 0
    def get_aux_valve_actual(self): return 0
    def set_encoder(self, e): return "ok"
    def get_error(self): return (0, 0)
    def save_defaults(self): return "ok"
    def factory_reset(self): return "ok"

    def __repr__(self):
        return f"<MockNRGSyringePump name={self.name} port={self.port}>"
