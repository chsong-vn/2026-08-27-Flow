"""
MockCartesianSampler - GrblCartesianSampler 의 가상 폴백.

# @codesyncer-decision: 실제 좌표 없이도 시퀀스 흐름 검증 가능. positions 파일 없으면
#   메모리에 더미 좌표 자동 생성.
"""
import time


class MockCartesianSampler:
    _serial_registry = {}
    _lock_registry = {}

    def __init__(self, name="Sampler_Mock", **kwargs):
        self.name = name
        self.port = kwargs.get("port", "COM_Mock")
        self.is_connected = False

        self.safe_z = kwargs.get("safe_z", -2.0)
        self.vials_top_z = kwargs.get("vials_top_z", -25.0)
        self.needle_depth_mm = kwargs.get("needle_depth_mm", 20.0)
        self.feedrate_xy = kwargs.get("feedrate_xy", 2000.0)
        self.feedrate_z = kwargs.get("feedrate_z", 600.0)

        self.aux_needle_state = "OFF"
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.grbl_version = "0.9j-mock"

        # 더미 vial 좌표
        self.vial_positions = {f"A{i}": (10.0 * i, 10.0) for i in range(1, 13)}
        self.vial_positions.update({f"B{i}": (10.0 * i, 30.0) for i in range(1, 13)})
        self.vial_positions["waste"] = (120.0, 10.0)
        self.vial_positions["rinse"] = (120.0, 30.0)
        self.injection_ports = {
            "injection_flow": {"x": 150.0, "y": 10.0, "z": -35.0, "valve": "ON"},
            "injection_waste": {"x": 150.0, "y": 30.0, "z": -35.0, "valve": "OFF"},
        }

    def connect(self, port=None):
        if port:
            self.port = port
        self.is_connected = True
        print(f"   [System] {self.name} (Sampler Mock) Connected on {self.port}.")
        return True, "Connected (mock)"

    def disconnect(self):
        self.is_connected = False
        print(f"   [System] {self.name} (Sampler Mock) Disconnected.")

    def reload_positions(self):
        pass

    def home(self):
        time.sleep(0.1)
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        print(f"[{self.name}] (mock) Homed")
        return True, "ok"

    def return_home(self):
        return self.home()

    def move_to_vial(self, vial_id, depth_override_mm=None):
        if vial_id not in self.vial_positions:
            return False, f"Unknown vial '{vial_id}'"
        if self.aux_needle_state == "ON":
            return False, "aux_needle is ON"
        x, y = self.vial_positions[vial_id]
        depth = self.needle_depth_mm if depth_override_mm is None else depth_override_mm
        self.current_position = {"x": x, "y": y, "z": self.vials_top_z - depth}
        time.sleep(0.05)
        print(f"[{self.name}] (mock) move_to_vial {vial_id}")
        return True, vial_id

    def move_to_injection_port(self, port_name):
        if port_name not in self.injection_ports:
            return False, f"Unknown injection port '{port_name}'"
        if self.aux_needle_state == "ON":
            return False, "aux_needle is ON"
        p = self.injection_ports[port_name]
        self.current_position = {"x": p["x"], "y": p["y"], "z": p.get("z", -35.0)}
        time.sleep(0.05)
        return True, p.get("valve")

    def lift_needle(self):
        self.current_position["z"] = self.safe_z
        return True

    def insert_aux_needle(self):
        self.aux_needle_state = "ON"
        time.sleep(0.1)
        return True

    def retract_aux_needle(self):
        self.aux_needle_state = "OFF"
        time.sleep(0.1)
        return True

    def emergency_stop(self):
        print(f"[{self.name}] (mock) Emergency stop")

    def stop_motion(self):
        self.emergency_stop()

    def get_position(self):
        return dict(self.current_position)

    def query_position(self, timeout=1.0):
        return dict(self.current_position)

    def __repr__(self):
        return f"<MockCartesianSampler name={self.name} port={self.port}>"
