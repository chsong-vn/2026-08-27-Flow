
import time, random

class MockPump:
    def __init__(self, name):
        self.name = name; self.running = False; self.target_flow = 1.0; self.current_pressure = 0.0
    def connect(self): print(f"   [System] {self.name} Connected.")
    def set_flow(self, flow_rate): self.target_flow = float(flow_rate)
    def start(self): 
        self.running = True; self.current_pressure = random.uniform(2.0, 3.0)
        print(f"   [System] {self.name} STARTED.")
    def stop(self):
        self.running = False; self.current_pressure = 0.0
        print(f"   [System] {self.name} STOPPED.")
    def get_pressure(self):
        if self.running:
            self.current_pressure = max(0.5, 2.0 + (self.target_flow * 0.5) + random.uniform(-0.1, 0.1))
            return round(self.current_pressure, 2)
        return 0.0
    def get_flow(self): return self.target_flow if self.running else 0.0