"""
emulators.py — 하드웨어 없이 전체 스택 검증용 가상 펌웨어.

FakeSerial이 pyserial의 serial.Serial 인터페이스를 흉내내고,
그 뒤에 실제 아두이노 펌웨어/GRBL의 시리얼 프로토콜을 재현하는 에뮬레이터가 붙는다.
디바이스 클래스에는 serial_interface 주입 지점이 이미 있으므로 (ArduinoDevice.__init__),
파이썬 코드 무수정으로 전 계층을 돌릴 수 있다.

시간 스케일: TIME_SCALE=0.02 (실제 대비 50배속). 파이썬 쪽 타임아웃은
실시간 기준으로 계산되므로 ack는 항상 타임아웃 훨씬 안에 도착한다.
"""

import re
import threading
import time

TIME_SCALE = 0.02


class FakeSerial:
    """serial.Serial 대체. ArduinoDevice가 쓰는 API만 구현."""

    def __init__(self, emulator):
        self.emulator = emulator
        self.port = None
        self.baudrate = 9600
        self.timeout = 1
        self.write_timeout = 1
        self.is_open = False
        self._rx = bytearray()
        self._rx_cond = threading.Condition()
        self._tx_line_buf = ""
        emulator.attach(self)

    # --- 디바이스 → 에뮬레이터 ---
    def open(self):
        self.is_open = True
        self.emulator.on_open()

    def close(self):
        self.is_open = False

    def write(self, data: bytes):
        self._tx_line_buf += data.decode("ascii")
        while "\n" in self._tx_line_buf:
            line, self._tx_line_buf = self._tx_line_buf.split("\n", 1)
            self.emulator.handle(line.rstrip("\r"))
        return len(data)

    # --- 에뮬레이터 → 디바이스 ---
    def push(self, line: str):
        with self._rx_cond:
            self._rx += (line + "\r\n").encode("ascii")
            self._rx_cond.notify_all()

    @property
    def in_waiting(self) -> int:
        with self._rx_cond:
            return len(self._rx)

    def read(self, n: int) -> bytes:
        with self._rx_cond:
            data, self._rx = bytes(self._rx[:n]), self._rx[n:]
            return data

    def readline(self) -> bytes:
        deadline = time.time() + (self.timeout or 1)
        with self._rx_cond:
            while b"\n" not in self._rx:
                remaining = deadline - time.time()
                if remaining <= 0:
                    data, self._rx = bytes(self._rx), bytearray()
                    return data
                self._rx_cond.wait(remaining)
            idx = self._rx.index(b"\n") + 1
            data, self._rx = bytes(self._rx[:idx]), self._rx[idx:]
            return data


class BaseEmulator:
    def __init__(self):
        self.fs: FakeSerial | None = None
        self.tx_log: list[str] = []   # 디바이스가 보낸 명령 전부 기록 (검증용)

    def attach(self, fake_serial: FakeSerial):
        self.fs = fake_serial

    def on_open(self):
        pass

    def handle(self, line: str):
        self.tx_log.append(line)
        self._handle(line)

    def _handle(self, line: str):
        raise NotImplementedError


class PumpEmulator(BaseEmulator):
    """syringe_pump_nrg.ino 프로토콜 재현. 볼륨 부기 + 이동시간 + stop 시맨틱."""

    def __init__(self, device_id="emul_pump", syringe_ul=1000.0):
        super().__init__()
        self.id = device_id
        self.syringe_ul = syringe_ul
        self.volume_ul = 0.0          # R11 (스텝카운트 환산치와 등가)
        self.steps_per_ml = 78049.0
        self.diameter = 4.61
        self.flowrate = 1.0           # mL/min
        self.enabled = False
        self.valve_sp = 1             # OFF
        self.aux_sp = 1
        self.pump_ack = True
        self.valve_ack = True
        self.aux_ack = True
        self.error = (0, 0)
        self._move_lock = threading.Lock()
        self._moving = False
        self._stop_flag = threading.Event()
        self.moves: list[float] = []  # 요청된 S9 볼륨 이력

    def _duration_s(self, ul: float) -> float:
        return abs(ul) / 1000.0 / self.flowrate * 60.0 * TIME_SCALE

    def _run_move(self, target_ul_change: float):
        """target_ul_change: 시린지 볼륨 변화량 (S9=x → -x)."""
        steps = 50
        dt = max(self._duration_s(abs(target_ul_change)) / steps, 1e-4)
        per_step = target_ul_change / steps
        for _ in range(steps):
            if self._stop_flag.is_set():
                break
            time.sleep(dt)
            self.volume_ul += per_step
        self._moving = False
        if self.pump_ack:
            self.fs.push("k")

    def _start_move(self, ul_change: float):
        self._stop_flag.clear()
        self._moving = True
        threading.Thread(target=self._run_move, args=(ul_change,), daemon=True).start()

    def _handle(self, line: str):
        m = re.match(r"^([SR])(\d+)(?:=(.*))?$", line)
        if not m:
            return
        cmd, var, val = m.group(1), int(m.group(2)), m.group(3)
        if cmd == "R":
            table = {
                1: self.id, 2: int(self.pump_ack), 3: self.valve_sp,
                4: self.valve_sp,  # actual = setpoint (즉시 도달 가정)
                5: self.diameter, 6: self.steps_per_ml,
                7: round(self.flowrate, 4), 8: int(self.enabled),
                10: 0.0, 11: round(self.volume_ul, 3),
                13: f"{self.error[0]}-{self.error[1]}",
                16: self.aux_sp, 17: self.aux_sp,
                18: 1, 19: int(self.valve_ack), 20: int(self.aux_ack),
                22: 60.0e6 / (300 * self.steps_per_ml),   # MIN_DELAY_NO_ACC=300
                23: 50.0,
            }
            if var in table:
                self.fs.push(str(table[var]))
                if var == 13:
                    self.error = (0, 0)
            return
        # --- S (쓰기) ---
        if var == 1:
            self.id = val
        elif var == 2:
            self.pump_ack = val == "1"
        elif var == 3:
            self.valve_sp = int(val)
            time.sleep(0.01)
            if self.valve_ack:
                self.fs.push("v")
        elif var == 16:
            self.aux_sp = int(val)
            time.sleep(0.01)
            if self.aux_ack:
                self.fs.push("a")
        elif var == 5:
            self.diameter = float(val)
        elif var == 6:
            self.steps_per_ml = float(val)
        elif var == 7:
            self.flowrate = float(val)
        elif var == 8:
            self.enabled = val == "1"
        elif var == 9:
            if not self.enabled:
                self.error = (3, 1)   # ERROR_PUMP_MOTOR / DISABLED
                self.fs.push("n")
                return
            ul = float(val)
            self.moves.append(ul)
            self._start_move(-ul)          # push x → 볼륨 -x
        elif var == 11:
            self.volume_ul = float(val)
        elif var == 12:
            if not self.enabled:
                self.fs.push("n")
                return
            mode = int(val)
            if mode == 0:      # EMPTY
                self._start_move(-self.volume_ul)
            elif mode == 1:    # FILL
                self._start_move(self.syringe_ul - self.volume_ul)
            else:              # IN_PLACE
                self.volume_ul = 0.0
                if self.pump_ack:
                    self.fs.push("k")
        elif var == 19:
            self.valve_ack = val == "1"
        elif var == 20:
            self.aux_ack = val == "1"
        elif var == 21:
            if not self._moving:
                if self.pump_ack:
                    self.fs.push("k")   # 정지상태 stop → 즉시 k (펌웨어와 동일)
            else:
                self._stop_flag.set()   # 이동 중 stop → 이동 스레드가 k 1회 전송


class GrblEmulator(BaseEmulator):
    """GRBL 0.9j 재현: $$, $H, $X(2줄 ack), G0/G1 + G4 동기화, ?, M8/M9."""

    SETTINGS = [
        "$0=10 (step pulse, usec)", "$1=25 (step idle delay, msec)",
        "$2=0 (step port invert mask:00000000)", "$3=0 (dir port invert mask:00000000)",
        "$4=0 (step enable invert, bool)", "$5=0 (limit pins invert, bool)",
        "$6=0 (probe pin invert, bool)", "$10=3 (status report mask:00000011)",
        "$11=0.010 (junction deviation, mm)", "$12=0.002 (arc tolerance, mm)",
        "$13=0 (report inches, bool)", "$20=1 (soft limits, bool)",
        "$21=1 (hard limits, bool)", "$22=1 (homing cycle, bool)",
        "$23=3 (homing dir invert mask:00000000)", "$24=25.000 (homing feed, mm/min)",
        "$25=2000.000 (homing seek, mm/min)", "$26=250 (homing debounce, msec)",
        "$27=1.000 (homing pull-off, mm)",
        "$100=400.000 (x, step/mm)", "$101=400.000 (y, step/mm)", "$102=400.000 (z, step/mm)",
        "$110=6000.000 (x max rate, mm/min)", "$111=6000.000 (y max rate, mm/min)",
        "$112=1000.000 (z max rate, mm/min)",
        "$120=100.000 (x accel, mm/sec^2)", "$121=100.000 (y accel, mm/sec^2)",
        "$122=50.000 (z accel, mm/sec^2)",
        "$130=173.000 (x max travel, mm)", "$131=291.000 (y max travel, mm)",
        "$132=77.500 (z max travel, mm)",
    ]

    def __init__(self):
        super().__init__()
        self.pos = [0.0, 0.0, 0.0]
        self.feed = 1000.0
        self.aux_on = False
        self.homed = False
        self._motion = None  # (thread)
        self._motion_done = threading.Event()
        self._motion_done.set()

    def on_open(self):
        self.fs.push("Grbl 0.9j ['$' for help]")

    def _move_to(self, target):
        start = list(self.pos)
        dist = max(
            (sum((t - s) ** 2 for s, t in zip(start, target))) ** 0.5, 0.01
        )
        duration = dist / (self.feed / 60.0) * TIME_SCALE
        time.sleep(duration)
        self.pos = target
        self._motion_done.set()

    def _handle(self, line: str):
        if line == "$$":
            for s in self.SETTINGS:
                self.fs.push(s)
            self.fs.push("ok")
        elif line.startswith("$H"):
            time.sleep(0.05)
            self.pos = [0.0, 0.0, 0.0]
            self.homed = True
            self.fs.push("ok")
        elif line.startswith("$X"):
            self.fs.push("[Caution: Unlocked]")
            self.fs.push("ok")
        elif line.startswith("G92"):
            coords = dict(re.findall(r"([XYZ])(-?\d+\.?\d*)", line))
            for i, ax in enumerate("XYZ"):
                if ax in coords:
                    self.pos[i] = float(coords[ax])
            self.fs.push("ok")
        elif line.startswith("F"):
            self.feed = float(line[1:])
            self.fs.push("ok")
        elif line in ("G21", "G90"):
            self.fs.push("ok")
        elif line.startswith(("G1", "G0")):
            coords = dict(re.findall(r"([XYZ])(-?\d+\.?\d*)", line))
            target = list(self.pos)
            for i, ax in enumerate("XYZ"):
                if ax in coords:
                    target[i] = float(coords[ax])
            self.fs.push("ok")                       # 플래너 수락
            self._motion_done.clear()
            threading.Thread(target=self._move_to, args=(target,), daemon=True).start()
        elif line.startswith("G4"):
            p = float(re.search(r"P(\d+\.?\d*)", line).group(1))
            def sync():
                self._motion_done.wait()             # 버퍼 드레인 = 이동 완료 대기
                time.sleep(p * TIME_SCALE)
                self.fs.push("ok")
            threading.Thread(target=sync, daemon=True).start()
        elif line == "?":
            state = "Idle" if self._motion_done.is_set() else "Run"
            p = ",".join(f"{v:.3f}" for v in self.pos)
            self.fs.push(f"<{state},MPos:{p},WPos:{p}>")
            self.fs.push("ok")
        elif line.startswith("M8"):
            self.aux_on = True
            self.fs.push("ok")
        elif line.startswith("M9"):
            self.aux_on = False
            self.fs.push("ok")
        elif line and ord(line[0]) == 24:            # soft reset
            self._motion_done.set()
            self.fs.push("ok")


class GpioEmulator(BaseEmulator):
    """GPIO_Array 재현: 변수 10+idx*10 (mode/read/write/pwm), R4 에러."""

    def __init__(self, device_id="emul_gpio"):
        super().__init__()
        self.id = device_id
        self.vars: dict[int, str] = {}

    def _handle(self, line: str):
        m = re.match(r"^([SR])(\d+)(?:=(.*))?$", line)
        if not m:
            return
        cmd, var, val = m.group(1), int(m.group(2)), m.group(3)
        if cmd == "R":
            if var == 1:
                self.fs.push(self.id)
            elif var == 2:
                self.fs.push("10")   # max_digital (UNO: D2~D12)
            elif var == 3:
                self.fs.push("6")    # max_analog
            elif var == 4:
                self.fs.push("0-0")
            else:
                self.fs.push(self.vars.get(var, "0"))
        else:
            if var == 1:
                self.id = val
            else:
                self.vars[var] = val
