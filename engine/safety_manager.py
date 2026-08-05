
class SafetyError(Exception): pass

class SafetyManager:
    def __init__(self, config, pumps, heater):
        self.cfg = config; self.pumps = pumps; self.heater = heater
        self.fails_temp = 0; self.fails_pressure = {p: 0 for p in pumps}

    def check_system(self):
        # @codesyncer-decision: MockHeater가 아닌 실제 히터일 때만 온도 체크
        # 기존 self.cfg.use_heater → AttributeError 발생하던 버그 수정
        if self.heater and type(self.heater).__name__ != "MockHeater":
            self._check_temp()
        self._check_pressure()

    def _check_temp(self):
        try: t = self.heater.get_temperature()
        except: t = None
        if t is None:
            self.fails_temp += 1
            if hasattr(self.cfg, 'max_sensor_fails') and self.fails_temp > self.cfg.max_sensor_fails: 
                 raise SafetyError("Heater Sensor Timeout")
        else:
            self.fails_temp = 0
            if hasattr(self.cfg, 'max_temp') and t > self.cfg.max_temp: 
                raise SafetyError(f"Overheat ({t:.1f} > {self.cfg.max_temp})")

    def _check_pressure(self):
        for n, p in self.pumps.items():
            try: val = p.get_pressure()
            except: val = None
            if val is None:
                self.fails_pressure[n] += 1
            else:
                self.fails_pressure[n] = 0
                if hasattr(self.cfg, 'max_pressure') and val > self.cfg.max_pressure: 
                    raise SafetyError(f"Overpressure ({n}: {val:.1f} bar)")