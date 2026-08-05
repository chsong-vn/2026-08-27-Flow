"""
NRG (Noël Research Group) Syringe Pump Driver — robochem_devices 백엔드
=====================================================================
RoboChem-Flex 의 시린지펌프 (Arduino UNO + NEMA17 + TB6600 + Runze Mrv-01B ×2).

- 통신: USB Serial 9600 bps, ASCII 텍스트 프로토콜 'Sx=y' / 'Rx'
- 펌웨어 변수 #1~#23 으로 전 기능 제어 (직경/유량/체적/엔코더/메인밸브/aux밸브 등)

# @codesyncer-decision: 이 드라이버는 이제 얇은 자체구현이 아니라 vendored
#   `robochem_devices.SyringePump`(원저자 Noël Research Group, Apache-2.0)를 백엔드로
#   위임한다. 공개 API 표면(connect/disconnect/set_rate/set_volume/start/stop/read_*/
#   is_running/get_dispensed_volume/get_pressure + set_main_valve/pump_volume/zero_*)과
#   `_serial_registry`/`_lock_registry` 클래스 속성은 이전과 100% 동일하게 유지되어
#   hw_manager·strict_engine·cleanup 코드가 한 줄도 바뀌지 않는다.
# @codesyncer-decision: robochem 로부터 흡수하는 것 — 타입드 에러(MotorError/ValveError),
#   옵티컬 엔코더 막힘 자동 감지, 가속 반영 물리 타이밍(pumping_time) 기반 정확한 ack
#   타임아웃, 시린지 용량/잔량 사전 검증(빈 시린지 토출 차단), stop Event set→clear
#   라이프사이클.
# @codesyncer-decision: connect() 는 "무이동 연결" 계약을 보존한다. robochem
#   SyringePump.initialize() 는 실제 구동(제로잉 FILL)을 유발하므로 호출하지 않고,
#   open()+setup() 후 _initialized 플래그만 세운다. 물리 이동은 사용자가 zero_fill()/
#   zero_empty()/start() 로 명시적으로 트리거한다. (빈 시린지에서 dispense 는 robochem 이
#   용량검증으로 거부하므로, 먼저 zero_fill()/withdraw() 로 채워야 한다.)
# @codesyncer-inference: 펌프 부호 규칙은 robochem(펌웨어 원저자) 규약을 따른다 —
#   양수 pump = 토출(infuse, plunger 전진, 시린지 잔량 감소), 음수 pump = 흡인(withdraw,
#   plunger 후퇴, 시린지 채움). 과거 자체구현은 반대 부호를 추정했으나(@codesyncer-inference
#   플래그됨) 원저자 라이브러리를 신뢰한다. 실기 검증에서 방향이 반대면 아래 _signed_ul()
#   의 부호만 뒤집으면 된다.
"""
import time

# robochem 이 없거나 깨지면 ImportError → hw_manager 의 try/except 가 잡아
# MockNRGSyringePump 로 폴백한다 (기존 동작 유지).
from robochem_devices import SyringePump
from robochem_devices import errors as rc_errors


class NRGSyringePump:
    """RoboChem-Flex NRG 시린지펌프 (robochem_devices.SyringePump 백엔드)."""

    # 공유 시리얼 레지스트리 — hw_manager.cleanup_hardware() 가 hot-reload 시 사용.
    # robochem 은 인스턴스 단위로 시리얼을 소유하므로, connect() 에서 _serial_iface 를
    # 여기에 등록해 기존 cleanup 계약(ser.is_open / ser.close())을 그대로 만족시킨다.
    _serial_registry = {}
    _lock_registry = {}

    def __init__(
        self,
        port,
        baudrate=9600,
        syringe_diameter_mm=4.6066,
        syringe_volume_ul=1000.0,
        max_flowrate_ml_min=5.0,
        pump_id=0,
        use_encoder=True,
        main_valve_enabled=False,
        aux_valve_enabled=False,
    ):
        """
        @param port: COM 포트 (예: 'COM22').
        @param baudrate: 9600 고정 (펌웨어 기본값, robochem 도 9600).
        @param syringe_diameter_mm: 시린지 내경 (1 mL = 4.6066, 10 mL ~ 14.5).
        @param syringe_volume_ul: 시린지 명목 용량 (소프트 리밋 산정용).
        @param max_flowrate_ml_min: 안전 최대 유량 (이 이상 set_rate 거부).
        @param pump_id: 로그 표시용.
        @param use_encoder: 옵티컬 엔코더 활성화 — 시린지 막힘 자동 감지 (강력 권장).
        @param main_valve_enabled: 펌프 PCB 의 메인 Mrv-01B 사용 여부.
        @param aux_valve_enabled: 펌프 PCB 의 aux Mrv-01B 사용 여부.
        """
        self.port = port
        self.baudrate = baudrate
        self.diameter = float(syringe_diameter_mm)
        self.syringe_volume_ul = float(syringe_volume_ul)
        self.max_flowrate = float(max_flowrate_ml_min)
        self.pump_id = int(pump_id)
        self.use_encoder = bool(use_encoder)
        self.main_valve_enabled = bool(main_valve_enabled)
        self.aux_valve_enabled = bool(aux_valve_enabled)

        # 내부 상태
        self.mode = "INFUSE"                  # "INFUSE"(토출) | "WITHDRAW"(흡인)
        self._pending_volume_ul = 0.0
        self._current_rate = 0.0
        self._last_pumped_ul = 0.0
        self._is_running_flag = False

        # robochem 백엔드 (아직 open 하지 않음). 후속 Phase(태스크 계층)에서
        # FillPump/PumpVolume/PrimePump 에 넘길 수 있도록 public 으로 노출.
        self.device = SyringePump()
        self.device.specific_name = f"NRG-{self.pump_id}"

    # ───────────────────────────────────────────────────────────────────
    # 연결/해제
    # ───────────────────────────────────────────────────────────────────
    def connect(self) -> bool:
        try:
            if self.port in NRGSyringePump._serial_registry:
                print(f"   [NRG ID:{self.pump_id}] Sharing existing port {self.port}")
                return True

            # 1) 시리얼 open (robochem 이 아두이노 리셋 2초 대기 내장)
            self.device.open(self.port)

            # 2) setup — 소프트 설정 + 비이동 파라미터 쓰기 (직경/엔코더/밸브 활성화).
            #    robochem setup() 은 내부적으로 _initialized 를 잠깐 True 로 두어
            #    이동 없이 파라미터를 쓴다.
            setup_opts = {
                "syringe_volume": f"{self.syringe_volume_ul}uL",
                "syringe_diameter": f"{self.diameter}mm",
                "max_flowrate": float(self.max_flowrate),
                "valve": bool(self.main_valve_enabled),
                "aux-valve": bool(self.aux_valve_enabled),   # 옵션명에 하이픈 → dict 전개
                "encoder": bool(self.use_encoder),
            }
            if self.main_valve_enabled:
                # 배관 규약 고정: C=시린지, ON=시스템, OFF=reservoir (README §주의)
                # robochem 태스크(PumpVolume/FillPump)가 밸브 방향을 스스로 유도할 때 필수.
                setup_opts["reservoir_valve_position"] = "OFF"
            self.device.setup(**setup_opts)

            # 3) 무이동 ready: initialize()(제로잉 FILL) 를 건너뛰고 플래그만 세운다.
            self.device._initialized = True

            # 4) ack_pump 를 ON 으로 '강제' (읽고 맞추기 금지)
            # @codesyncer-decision: 블로킹 실행 + 비동기 정지 계약 전부가 펌프 완료
            #   ack('k') 에 의존한다 — robochem 은 _receive_acknowledge 폴링 안에서만
            #   stop 명령을 주입하므로, 펌웨어가 ack OFF 로 부팅돼 있으면 pump 쓰기가
            #   fire-and-forget 이 되어 정지 불능 + 완료감지 불능 + 엔코더 stall 에러
            #   전파(무압력센서 라인의 유일 보호)까지 무력화된다. 쓰기 성공 시
            #   SyringePump._write 가 use_pump_acknowledge(True) 까지 자동 동기화.
            try:
                self.device["ack_pump"] = "ON"
            except Exception as e:
                print(f"   [NRG ID:{self.pump_id}] ⚠ ack_pump=ON 강제 실패({e}) — "
                      f"정지/완료 감지 불가 위험, 펌웨어 상태 점검 필요")

            # 5) 초기 안전 상태 (모터 disable, 유량 세팅) — 이동 없음
            try:
                self.device["flowrate"] = min(self._current_rate or 1.0, self.max_flowrate)
            except Exception:
                pass
            try:
                self.device["enable"] = "OFF"
            except Exception:
                pass

            # 6) hw_manager cleanup 계약을 위해 시리얼/락 등록
            NRGSyringePump._serial_registry[self.port] = self.device._serial_iface
            NRGSyringePump._lock_registry[self.port] = self.device._serial_thread_lock

            print(f"   [NRG ID:{self.pump_id}] Connected on {self.port} (robochem backend)")
            return True

        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] Connection Error: {e}")
            try:
                self.device.close()
            except Exception:
                pass
            return False

    def disconnect(self):
        try:
            self.stop()
        except Exception:
            pass
        try:
            if self.device.is_open():
                self.device["enable"] = "OFF"     # 코일 발열 방지
        except Exception:
            pass
        NRGSyringePump._serial_registry.pop(self.port, None)
        NRGSyringePump._lock_registry.pop(self.port, None)
        try:
            self.device.close()
        except Exception:
            pass

    # ───────────────────────────────────────────────────────────────────
    # ChemyxPump 호환 API (strict_engine 와의 swap-in 지원)
    # ───────────────────────────────────────────────────────────────────
    def set_units(self, units):
        """NRG 는 항상 mL/min — Chemyx 호환용 no-op."""
        pass

    def set_diameter(self, diameter_mm):
        self.diameter = float(diameter_mm)
        try:
            self.device["diameter"] = float(diameter_mm)
            return "OK"
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] set_diameter error: {e}")
            return None

    def set_rate(self, flow_rate_ml_min):
        """S7=<mL/min>. max_flowrate 초과는 clamp."""
        rate = float(flow_rate_ml_min)
        if rate <= 0:
            return None
        if rate > self.max_flowrate:
            print(f"   [NRG ID:{self.pump_id}] WARN: rate {rate} > max {self.max_flowrate}, clamping")
            rate = self.max_flowrate
        self._current_rate = rate
        try:
            self.device["flowrate"] = rate
            return "OK"
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] set_rate error: {e}")
            return None

    def set_volume(self, volume_ml):
        """다음 start() 에서 사용할 체적(µL, 크기만) 예약. Chemyx 호환용 non-None 반환."""
        self._pending_volume_ul = abs(float(volume_ml)) * 1000.0
        return f"set volume {volume_ml}"

    def set_delay(self, delay_min):
        """NRG 미지원 — no-op."""
        pass

    def set_mode(self, mode_str):
        m = str(mode_str).lower()
        self.mode = "WITHDRAW" if "withdraw" in m else "INFUSE"

    def _signed_ul(self, ul):
        """robochem 부호 규약: +토출(infuse) / −흡인(withdraw). 방향이 반대면 여기만 반전."""
        return ul if self.mode == "INFUSE" else -ul

    def start(self, mode=None):
        """
        예약된 _pending_volume_ul 만큼 펌프 실행 (블로킹, ack 대기).

        @param mode: 0=INFUSE, 1=WITHDRAW, None=현재 self.mode 유지.
        """
        if mode == 0:
            self.set_mode("infuse")
        elif mode == 1:
            self.set_mode("withdraw")

        ul = self._pending_volume_ul
        if ul <= 0.0:
            print(f"   [NRG ID:{self.pump_id}] WARN: start() called with no pending volume")
            return None

        self._last_pumped_ul = ul
        self._is_running_flag = True
        try:
            self.device.stop_parameter("pump", stop=False)   # 이전 정지 해제 (재사용)
            self.device["enable"] = "ON"
            self.device["pump"] = self._signed_ul(ul)        # 블로킹·acked·타입드에러·엔코더검사
            print(f"   [NRG ID:{self.pump_id}] PUMP OK ({self.mode}, {ul:.1f} uL)")
            return "OK"
        except rc_errors.DeviceError as e:
            print(f"   [NRG ID:{self.pump_id}] PUMP FAIL ({self.mode}, {ul:.1f} uL) — {e}")
            return None
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] PUMP error: {e}")
            return None
        finally:
            self._is_running_flag = False

    def stop(self):
        """진행 중 pump/zero 이동을 비동기 정지 (stop Event set)."""
        self._is_running_flag = False
        try:
            self.device.stop_parameter("pump")
            self.device.stop_parameter("zero")
            return "OK"
        except Exception:
            return None

    def pause(self):
        """NRG 미지원 — stop 으로 대체."""
        return self.stop()

    def read_rate(self):
        try:
            v = self.device["flowrate"]
            return float(v) if v is not None else None
        except Exception:
            return None

    def read_volume(self):
        """시린지에 현재 들어있는 액체량 (mL). robochem 'volume'(µL) → mL."""
        try:
            v = self.device["volume"]
            return float(v) / 1000.0 if v is not None else None
        except Exception:
            return None

    def is_running(self):
        return self._is_running_flag

    def is_stopped(self):
        return not self._is_running_flag

    def get_dispensed_volume(self):
        """마지막 펌프 명령에서 토출/흡입한 양 (mL). Chemyx 호환용."""
        return self._last_pumped_ul / 1000.0

    def get_pressure(self):
        """NRG 펌프는 압력 센서 없음."""
        return 0.0

    # ───────────────────────────────────────────────────────────────────
    # NRG 전용 API
    # ───────────────────────────────────────────────────────────────────
    def pump_volume(self, ul: float, direction: str = "infuse"):
        """지정 부피만큼 즉시 펌프 (ack 대기). infuse=토출, withdraw=흡인."""
        self.set_volume(ul / 1000.0)
        self.set_mode(direction)
        return self.start()

    def withdraw(self, ul: float):
        """시린지 채우기 (plunger 후퇴)."""
        return self.pump_volume(ul, direction="withdraw")

    def dispense(self, ul: float):
        """시린지 비우기 (plunger 전진)."""
        return self.pump_volume(ul, direction="infuse")

    def _zero(self, direction, timeout_hint=120.0):
        try:
            self.device.stop_parameter("zero", stop=False)
            self.device["enable"] = "ON"
            self.device["zero"] = direction     # "FILL" | "EMPTY" | "IN_PLACE"
            return "OK"
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] zero({direction}) error: {e}")
            return None

    def zero_fill(self):
        """양의 endstop 까지 후퇴 (시린지 완전 채움, volume counter=max)."""
        return self._zero("FILL")

    def zero_empty(self):
        """음의 endstop 까지 전진 (시린지 완전 비움)."""
        return self._zero("EMPTY")

    def zero_in_place(self):
        """이동 없이 카운터만 0 으로."""
        return self._zero("IN_PLACE")

    def set_main_valve(self, position):
        """
        메인 Mrv-01B 위치 설정.
            ON: 'C' ↔ 'ON' (시스템 측) / OFF: 'C' ↔ 'OFF' (reservoir 측)
        """
        if not self.device.has_valve():
            print(f"   [NRG ID:{self.pump_id}] WARN: main_valve not enabled")
            return None
        val = "ON" if (str(position).upper() == "ON" or position == 1) else "OFF"
        try:
            self.device["valve_setpoint"] = val
            return "OK"
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] set_main_valve error: {e}")
            return None

    def set_aux_valve(self, position):
        """aux Mrv-01B (인젝션 포트 분기용)."""
        if not self.device.has_aux_valve():
            print(f"   [NRG ID:{self.pump_id}] WARN: aux_valve not enabled")
            return None
        val = "ON" if (str(position).upper() == "ON" or position == 1) else "OFF"
        try:
            self.device["aux_valve_setpoint"] = val
            return "OK"
        except Exception as e:
            print(f"   [NRG ID:{self.pump_id}] set_aux_valve error: {e}")
            return None

    def get_main_valve_actual(self):
        """센서 피드백 (0=ON, 1=OFF, 2=ERROR)."""
        try:
            return int(self.device["valve_actual"].value)
        except Exception:
            return None

    def get_aux_valve_actual(self):
        try:
            return int(self.device["aux_valve_actual"].value)
        except Exception:
            return None

    def set_encoder(self, enabled: bool):
        """옵티컬 엔코더 활성/비활성. 끄면 시린지 막힘 보호 없음."""
        self.use_encoder = bool(enabled)
        try:
            self.device["encoder"] = "ON" if enabled else "OFF"
            return "OK"
        except Exception:
            return None

    def get_error(self):
        """에러 레지스터 → (type, value) 튜플. robochem 은 write 마다 자동 검사도 수행."""
        try:
            err = self.device["error"]
            v = getattr(err, "value", None)
            if isinstance(v, list) and len(v) == 2:
                return (v[0], v[1])
            return (0, 0)
        except Exception:
            return (None, None)

    def save_defaults(self):
        """현재 직경/유량/ID 를 EEPROM 에 영구 저장."""
        try:
            self.device["save"] = "RUN"
            return "OK"
        except Exception:
            return None

    def factory_reset(self):
        """EEPROM 공장 초기화."""
        try:
            self.device["reset"] = "RUN"
            return "OK"
        except Exception:
            return None

    def __repr__(self):
        return (
            f"<NRGSyringePump port={self.port} id={self.pump_id} "
            f"diameter={self.diameter:.2f}mm cap={self.syringe_volume_ul:.0f}uL "
            f"backend=robochem>"
        )
