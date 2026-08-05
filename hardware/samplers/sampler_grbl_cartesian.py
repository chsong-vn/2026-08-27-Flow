"""
Grbl Cartesian Sampler (RoboChem-Flex) — robochem_devices 백엔드
================================================================
3축 직교 (NEMA17 ×3 + limit switch ×3) 시약 샘플러.
시린지 needle 을 vial 위치로 이동시키고, 보조 needle (servo) 로 압력 보상.

- 통신: USB Serial 9600 bps, Grbl 0.9j 표준 G-code
- 좌표: vial_positions.json (실측 보정 좌표)
- 시약 흡인은 별도 NRG syringe pump 가 담당 (이 클래스는 위치 제어만)

# @codesyncer-decision: 모션 전송/타이밍/에러 처리는 vendored robochem_devices.Sampler
#   (원저자 Noël Research Group, Apache-2.0)에 위임한다. 흡수하는 것 —
#   `$$` grbl 설정 introspection(소프트/하드 리밋·피드·가속 자동 산정), 이동시간 물리
#   추정 기반 정확한 ack 타임아웃, 타입드 통신에러(SoftLimit/HardLimit/AlarmLock/
#   ForbiddenMove), 웰컴 버전 검사.
# @codesyncer-decision: 반대로 "설정 의존적" 부분은 기존 계약을 그대로 보존한다 —
#   (1) vial_positions.json 평면 좌표 모델(A1→(x,y))과 injection_ports (robochem 의
#       locations/InjectionPort 모델을 쓰지 않음), (2) 설정형 aux needle M-code
#       (hardware_config.json 의 aux_needle_on_code/off_code, 기본 M3/M5). robochem 은
#       aux 를 M8/M9 로 고정하지만 실제 핀맵은 펌웨어 빌드별로 달라 설정값을 신뢰한다.
#       → aux 코드는 _send_raw() 로 raw 전송(robochem 시리얼/락 재사용).
# @codesyncer-decision: connect() 는 "무이동 연결" 계약 보존 — robochem Sampler.initialize()
#   는 $H 호밍(이동)을 유발하므로 호출하지 않고, open()+setup()+read_grbl_settings()
#   (모두 비이동) 후 _initialized 플래그만 세운다. 호밍은 home() 로 명시적 트리거.
# @codesyncer-decision: robochem 내부 safe_z 인터록은 ignore_safe_z=True 로 끄고, 이
#   래퍼가 검증해온 "Z-up → XY → Z-down" 시퀀싱과 aux-ON XY 금지를 그대로 유지한다
#   (테스트된 모션 동작 보존; 좌표계 실기 검증 후 robochem 인터록으로 이관 가능).
# @codesyncer-inference: aux M-code 매핑·Grbl 0.9j WPos 상태 포맷·작업좌표 프레임은
#   하드웨어 실기 검증 필요 항목.
"""
import os
import json
import time
import threading

from robochem_devices import Sampler, GrblPosition
from robochem_devices import errors as rc_errors


class GrblCartesianSampler:
    """3축 직교 시약 샘플러 (robochem_devices.Sampler 모션 백엔드)."""

    # 공유 시리얼 레지스트리 (hw_manager cleanup 패턴 호환)
    _serial_registry = {}
    _lock_registry = {}

    SUPPORTED_GRBL = ["0.9j"]

    def __init__(
        self,
        name="Sampler",
        positions_file=None,
        safe_z=-2.0,
        vials_top_z=-25.0,
        needle_depth_mm=20.0,
        feedrate_xy=2000.0,
        feedrate_z=600.0,
        aux_needle_on_code="M3",
        aux_needle_off_code="M5",
    ):
        self.name = name
        self.port = None
        self.is_connected = False

        self.safe_z = float(safe_z)
        self.vials_top_z = float(vials_top_z)
        self.needle_depth_mm = float(needle_depth_mm)
        self.feedrate_xy = float(feedrate_xy)
        self.feedrate_z = float(feedrate_z)
        self.aux_needle_on_code = aux_needle_on_code
        self.aux_needle_off_code = aux_needle_off_code

        # 상태
        self.aux_needle_state = "OFF"
        self.current_position = {"x": None, "y": None, "z": None}
        self.grbl_version = None

        # robochem 모션 백엔드 (아직 open 안 함)
        self.device = Sampler()
        self.device.specific_name = name
        self.ser = None  # 호환용 별칭 (연결 후 robochem _serial_iface 를 가리킴)

        # vial 좌표 (기존 평면 JSON 모델 유지)
        if positions_file is None:
            here = os.path.dirname(os.path.abspath(__file__))
            positions_file = os.path.join(here, "data", "vial_positions.json")
        self.positions_file = positions_file
        self.vial_positions = {}   # {"A1": (x, y), ...}
        self.injection_ports = {}  # {"injection_flow": {...}, ...}
        self._load_positions()

    # ───────────────────────────────────────────────────────────────────
    # 좌표 로드 (기존 모델 그대로)
    # ───────────────────────────────────────────────────────────────────
    def _load_positions(self):
        if not os.path.exists(self.positions_file):
            print(f"[{self.name}] WARN: positions file not found: {self.positions_file}")
            return
        try:
            with open(self.positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[{self.name}] positions load failed: {e}")
            return
        self.vial_positions = {
            k: (v["x"], v["y"]) for k, v in data.get("vials", {}).items()
        }
        self.injection_ports = data.get("injection_ports", {}) or {}
        if "safe_z" in data:
            self.safe_z = float(data["safe_z"])
        if "vials_top_z" in data:
            self.vials_top_z = float(data["vials_top_z"])
        print(
            f"[{self.name}] Loaded {len(self.vial_positions)} vials, "
            f"{len(self.injection_ports)} injection ports"
        )

    def reload_positions(self):
        """런타임에 좌표 다시 읽기 (캘리브레이션 후 사용)."""
        self._load_positions()

    # ───────────────────────────────────────────────────────────────────
    # 연결/해제
    # ───────────────────────────────────────────────────────────────────
    def connect(self, port):
        """COM 포트 연결 + Grbl 설정 introspection (무이동). ($H 호밍은 home() 로)."""
        self.port = port
        try:
            if port in GrblCartesianSampler._serial_registry:
                print(f"[{self.name}] Sharing existing port {port}")
                self.ser = GrblCartesianSampler._serial_registry[port]
                self.device._serial_iface = self.ser
                self.is_connected = True
                return True, "Shared port"

            # 1) open (robochem 이 아두이노 리셋 2초 대기 + 웰컴 버전 검사)
            self.device.open(port)

            # 2) setup — 소프트 설정. robochem safe_z 인터록은 끄고(우리 시퀀싱 사용)
            self.device.setup(
                safe_z=f"{self.safe_z}mm",
                vial_top_z=f"{self.vials_top_z}mm",
                needle_length="51mm",
                ignore_safe_z=True,
            )

            # 3) grbl 설정 읽기 ($$) — 소프트/하드 리밋·피드·가속 산정 (비이동, 이동타이밍 필수)
            self.device.read_grbl_settings()

            # 4) 무이동 ready: initialize()($H 호밍) 건너뜀
            self.device._initialized = True

            # 5) 기본 상태: unlock → mm → absolute → feed, aux up
            self.device["unlock"] = "RUN"
            self.device["units_mm"] = "RUN"
            self.device["absolute"] = "RUN"
            self.device["feed"] = self._clamp_feed(self.feedrate_xy)
            self._send_raw(self.aux_needle_off_code, wait=1.0)
            self.aux_needle_state = "OFF"

            # 6) cleanup 계약을 위해 시리얼/락 등록
            GrblCartesianSampler._serial_registry[port] = self.device._serial_iface
            GrblCartesianSampler._lock_registry[port] = self.device._serial_thread_lock
            self.ser = self.device._serial_iface

            self.is_connected = True
            print(f"[{self.name}] Connected on {port} (robochem backend)")
            return True, "Connected (robochem backend)"

        except Exception as e:
            self.is_connected = False
            print(f"[{self.name}] Connection Error: {e}")
            try:
                if self.device.is_open():
                    self.device._serial_iface.close()
            except Exception:
                pass
            return False, str(e)

    def disconnect(self):
        """시리얼만 안전하게 닫는다 (무이동 — cleanup 안전성 위해 robochem return_to_home 생략)."""
        port = self.port
        ser = GrblCartesianSampler._serial_registry.pop(port, None)
        GrblCartesianSampler._lock_registry.pop(port, None)
        try:
            s = ser or (self.device._serial_iface if self.device else None)
            if s is not None and s.is_open:
                s.close()
        except Exception:
            pass
        self.ser = None
        self.is_connected = False

    # ───────────────────────────────────────────────────────────────────
    # 모션 명령 (robochem move/home 위임)
    # ───────────────────────────────────────────────────────────────────
    def home(self):
        """Grbl $H — 3축 호밍 후 작업원점 설정."""
        if not self.is_connected:
            return False, "Not Connected"
        print(f"[{self.name}] Homing ($H)...")
        try:
            self.device["home"] = "RUN"                          # $H (ack 120s)
            self.device["offset"] = GrblPosition(x=0.0, y=0.0, z=0.0)  # G92 원점
            self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
            self.current_vial = None   # 배관도 v3: 니들 파킹 = 바이알 해제
            return True, "Homed"
        except rc_errors.DeviceError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    def return_home(self):
        """전체 호밍보다 가벼운 복귀 — Z 안전높이로, XY (0,0)."""
        if not self.is_connected:
            return False, "Not Connected"
        try:
            if self.aux_needle_state == "ON":
                self.retract_aux_needle()
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=self.safe_z)
            self.device["feed"] = self._clamp_feed(self.feedrate_xy)
            self.device["move"] = GrblPosition(x=0.0, y=0.0)
            self.current_position = {"x": 0.0, "y": 0.0, "z": self.safe_z}
            return True, "Home"
        except Exception as e:
            return False, str(e)

    def move_to_vial(self, vial_id: str, depth_override_mm=None):
        """vial 위치로 needle 이동 후 vials_top_z + depth 만큼 하강. (Z-up→XY→Z-down)"""
        if not self.is_connected:
            return False, "Not Connected"
        if vial_id not in self.vial_positions:
            return False, f"Unknown vial '{vial_id}'"
        if self.aux_needle_state == "ON":
            return False, "aux_needle is ON, refusing XY move"

        x, y = self.vial_positions[vial_id]
        depth = self.needle_depth_mm if depth_override_mm is None else float(depth_override_mm)
        target_z = self.vials_top_z - depth   # 더 깊이 = Z 더 작게 (음수가 깊음)
        try:
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=self.safe_z)         # Z up
            self.device["feed"] = self._clamp_feed(self.feedrate_xy)
            self.device["move"] = GrblPosition(x=x, y=y)              # XY
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=target_z)           # Z down
            self.current_position = {"x": x, "y": y, "z": target_z}
            self.current_vial = vial_id   # 배관도 v3: 현재 바이알 맵핑용
            print(f"[{self.name}] move_to_vial {vial_id} @ ({x},{y},Z={target_z:.2f})")
            return True, vial_id
        except Exception as e:
            print(f"[{self.name}] move_to_vial error: {e}")
            return False, str(e)

    def move_to_injection_port(self, port_name: str):
        """등록된 injection port 로 이동. port 데이터의 'valve' 는 호출자가 별도 처리."""
        if not self.is_connected:
            return False, "Not Connected"
        if port_name not in self.injection_ports:
            return False, f"Unknown injection port '{port_name}'"
        if self.aux_needle_state == "ON":
            return False, "aux_needle is ON, refusing XY move"

        port_data = self.injection_ports[port_name]
        x = port_data["x"]
        y = port_data["y"]
        z = port_data.get("z", self.vials_top_z - self.needle_depth_mm)
        try:
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=self.safe_z)
            self.device["feed"] = self._clamp_feed(self.feedrate_xy)
            self.device["move"] = GrblPosition(x=x, y=y)
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=z)
            self.current_position = {"x": x, "y": y, "z": z}
            suggested_valve = port_data.get("valve")
            print(f"[{self.name}] move_to_injection_port {port_name} (valve={suggested_valve})")
            return True, suggested_valve
        except Exception as e:
            print(f"[{self.name}] move_to_injection_port error: {e}")
            return False, str(e)

    def lift_needle(self):
        """Z 를 safe_z 위로 올림 (다음 가로 이동 전 사용)."""
        if not self.is_connected:
            return False
        try:
            self.device["feed"] = self._clamp_feed(self.feedrate_z)
            self.device["move"] = GrblPosition(z=self.safe_z)
            if self.current_position.get("z") is not None:
                self.current_position["z"] = self.safe_z
            return True
        except Exception as e:
            print(f"[{self.name}] lift_needle error: {e}")
            return False

    def insert_aux_needle(self):
        """보조 needle 하강 (압력 보상). vial 안에서만 호출 가능. 설정형 M-code raw 전송."""
        if not self.is_connected:
            return False
        self._send_raw(self.aux_needle_on_code, wait=1.0)
        self.aux_needle_state = "ON"
        time.sleep(0.5)  # servo 안정화
        return True

    def retract_aux_needle(self):
        """보조 needle 후퇴."""
        if not self.is_connected:
            return False
        self._send_raw(self.aux_needle_off_code, wait=1.0)
        self.aux_needle_state = "OFF"
        time.sleep(0.3)
        return True

    def emergency_stop(self):
        """0x18 (soft reset) — Grbl 즉시 중지 (연결 유지).

        @codesyncer-decision: robochem __setitem__ 경유 금지 — 이동 중에는 워커
        스레드가 _serial_thread_lock 을 ack 대기 내내 점유하므로 락 경유 E-stop 은
        이동이 끝날 때까지 블록된다. 원본 드라이버처럼 락을 우회해 raw 1바이트를
        직송 (Grbl 은 실시간 명령으로 즉시 처리, 진행 중 move 는 알람/에러로 풀림)."""
        try:
            ser = self.device._serial_iface if self.device else None
            if ser is not None and ser.is_open:
                ser.write(bytes([0x18]))
                ser.flush()
                print(f"[{self.name}] Emergency stop sent (0x18, lock-bypass)")
        except Exception as e:
            print(f"[{self.name}] emergency_stop error: {e}")

    def get_position(self):
        """현재 위치 캐시 반환. 정확한 값은 query_position() 사용."""
        return dict(self.current_position)

    def query_position(self, timeout=1.5):
        """robochem 'position'(?) 조회 → {x,y,z}."""
        if not self.is_connected:
            return None
        try:
            pos = self.device["position"]   # GrblPosition
            d = {"x": pos.x, "y": pos.y, "z": pos.z}
            self.current_position = d
            return d
        except Exception as e:
            print(f"[{self.name}] query_position error: {e}")
            return None

    # 호환용 (collectors 인터페이스 흉내)
    def stop_motion(self):
        self.emergency_stop()

    # ───────────────────────────────────────────────────────────────────
    # 저수준 raw 전송 (설정형 aux M-code 전용 — robochem 시리얼/락 재사용)
    # ───────────────────────────────────────────────────────────────────
    def _clamp_feed(self, rate):
        try:
            fmax = self.device._parameter_feed.max_value
            if fmax is not None:
                return min(float(rate), float(fmax))
        except Exception:
            pass
        return float(rate)

    def _send_raw(self, cmd, wait=2.0):
        """raw G-code 한 줄 전송 후 'ok' 대기 (robochem 스레드락/버퍼 재사용)."""
        dev = self.device
        try:
            if not dev.is_open():
                return ""
        except Exception:
            return ""
        with dev._serial_thread_lock:
            try:
                dev.flush_buffer_in()  # 잔류 비우기
                dev._serial_iface.write((cmd + "\n").encode("ascii"))
                deadline = time.time() + wait
                while time.time() < deadline:
                    line = dev.flush_buffer_in(whole_line=True)
                    if line == "":
                        time.sleep(0.02)
                        continue
                    if line.strip().lower() == "ok":
                        return "ok"
                return ""
            except Exception as e:
                print(f"[{self.name}] raw send error: {e}")
                return ""

    def __repr__(self):
        return (f"<GrblCartesianSampler name={self.name} port={self.port} "
                f"vials={len(self.vial_positions)} backend=robochem>")
