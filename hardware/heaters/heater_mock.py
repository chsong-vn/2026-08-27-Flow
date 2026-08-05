
import random
import threading

class MockHeater:
    def __init__(self, port="Heater"):
        self.port = port
        self.target_temp = 0.0
        self.current_temp = 25.0

    def connect(self):
        print(f"   [MockHeater] Connected")

    def set_temperature(self, t):
        self.target_temp = float(t)

    def get_temperature(self):
        diff = self.target_temp - self.current_temp
        if abs(diff) > 0.5:
            if diff > 0:
                self.current_temp += random.uniform(0.5, 1.5)
            else:
                self.current_temp += random.uniform(-1.0, -0.5)
        else:
            self.current_temp += random.uniform(-0.1, 0.1)
            
        return round(self.current_temp, 2)

    def stop(self):
        self.target_temp = 0.0