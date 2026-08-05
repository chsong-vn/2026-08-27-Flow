"""
NRGSmartPump — NRG 시린지펌프의 시퀀스(스마트) 어댑터, valve 모드
=================================================================
strict_engine 이 요구하는 스마트펌프 계약(ChemyxSmartPump 표면)을 robochem_devices
태스크(PumpVolume / zero FILL·EMPTY)로 구현한다. 라우팅 모드 = "internal_valve":
외부 12-way selector 없이 NRG 내장 main valve(2-way) 만으로 reservoir↔system 라우팅.
1 펌프 = 1 소스(시약 또는 용매) 고정.

# @codesyncer-decision: ChemyxSmartPump 클래스 재사용 금지 — 부호규약(Chemyx 음수
#   volume=흡인 vs robochem 음수 pump=흡인이지만 set_volume/read_volume 의미가 다름),
#   read-back 검증(set_volume↔read_volume 이 NRG 에선 무관한 값), 실행모델(블로킹)이
#   전부 달라 어댑터 신규 작성. 대신 엔진 계약 표면(메서드/속성 이름·시그니처)을
#   ChemyxSmartPump 와 동일하게 맞춰 strict_engine `_is_smart_pump` 덕타이핑을 통과.
# @codesyncer-decision: begin(prepare)/trigger/complete 3단계 매핑 —
#   prepare = 검증+플랜 기록(시리얼 최소), trigger = 백그라운드 스레드에서 robochem
#   블로킹 태스크 시작(즉시 리턴 → 엔진의 순차 trigger 간격과 정합), complete =
#   thread.join + 펌웨어 volume 을 current_vol 로 동기화. NRG 는 포트별 독립 USB
#   시리얼이라 RS-485 버스 경쟁이 없고, trigger 가 스레드 spawn 이므로 0.35s 간격은
#   자연 흡수됨.
# @codesyncer-decision: 부피 단일 소스 = 펌웨어 `device['volume']`(µL). 각 complete
#   에서 current_vol(mL) 로 스냅한다. 엔진은 dosing 중 current_vol 을 시간모델로
#   감산하는데(strict_engine dosing loop), 이는 Chemyx 와 동일한 소프트 추정이고
#   다음 refill/prime complete 에서 실측으로 재동기화된다.
# @codesyncer-decision: 최초 refill 전 zero EMPTY 로 플런저 위치 확정 — NRG 저수준
#   드라이버의 connect() 는 무이동 계약이라 펌웨어 volume 이 미확정 상태. 첫 refill
#   에서 valve→reservoir 후 EMPTY(잔류물은 소스병으로 되밀림)로 0점을 만들고 정확한
#   부피 흡인. 이후 refill 은 zero 생략.
# @codesyncer-decision: wash = reservoir 위치에서 FILL→EMPTY (기포 퍼지). 단일 소스
#   토폴로지에는 폐액 포트가 없어 Chemyx 식 '폐액 배출 세척'이 대응되지 않음 —
#   solvent_port/waste_port 인자는 수신하되 무시. wash_count 기본 0 (옵트인).
# @codesyncer-decision: 에러 전파 — complete 계열에서 robochem 타입드 에러
#   (MotorError=엔코더 막힘/endstop, ValveError, Timeout)를 RuntimeError 로 승격 →
#   엔진 _run_complete_threads 의 Emergency Stop 인터락과 정합. NRG 라인에는 압력
#   센서가 없어(get_pressure=0.0) SafetyManager 과압 감시가 사실상 배제되므로, 이
#   엔코더 stall 전파가 유일한 하드웨어 보호 경로다.
# @codesyncer-risk: set_flow 가 max_flowrate 초과 요청을 받으면 클램프하지 않고
#   해당 dosing start 를 거부(무토출)한다 — 클램프는 당량비를 조용히 왜곡하므로
#   더 위험. status/log 로 크게 표시.
"""
import threading
import time

from robochem_devices.tasks_pumps import PumpVolume
from robochem_devices import errors as rc_errors


class NRGSmartPump:
    """NRG 시린지펌프 시퀀스 어댑터 (valve 모드, robochem 백엔드)."""

    ROUTING_MODE = "internal_valve"

    def __init__(self, name, low_driver, config_dict):
        """
        @param name: 그룹 이름 (예: 'Group A').
        @param low_driver: 연결 전의 NRGSyringePump 인스턴스 (main_valve_enabled=True 필수).
        @param config_dict: capacity/refill_rate/prime_rate/wash_* /pump_id.
        """
        self.name = name
        self.driver = low_driver              # 엔진이 pump.driver.is_stopped() 로 접근
        self.device = low_driver.device       # robochem SyringePump

        syringe_ml = float(low_driver.syringe_volume_ul) / 1000.0
        cap = config_dict.get('capacity')
        # 물리 시린지 용량을 넘는 설정은 무의미 — 작은 쪽으로 클램프
        self.capacity = min(float(cap), syringe_ml) if cap else syringe_ml
        self.refill_rate = min(float(config_dict.get('refill_rate', 2.0)), low_driver.max_flowrate)
        self.prime_rate = min(float(config_dict.get('prime_rate', 2.0)), low_driver.max_flowrate)
        self.wash_speed = min(float(config_dict.get('wash_speed', self.refill_rate)), low_driver.max_flowrate)
        self.wash_count = int(config_dict.get('wash_count', 0))
        self.wash_volume = float(config_dict.get('wash_volume', self.capacity))
        self.dead_vol_solvent = float(config_dict.get('dead_vol_solvent', 0.0))
        self.pump_id = int(config_dict.get('pump_id', low_driver.pump_id))

        # 엔진이 직접 읽고/쓰는 계약 속성 (ChemyxSmartPump 와 동일 이름)
        self.current_vol = 0.0
        self.target_flow = 0.0
        self.running = False
        self.status = "Idle"
        self.is_refilling = False
        self._abort_refill = False

        # 외부 밸브 없음 — 계약 표면 유지용 (엔진은 self.valves 로만 밸브 구동)
        self.selector = None
        self.switcher = None
        self.POS_SOURCE = 1
        self.POS_REACTOR = 2

        self._zeroed = False                  # 최초 refill 에서 EMPTY 0점 수행 여부
        self._bg = None                       # 진행 중 백그라운드 태스크 스레드
        self._bg_error = None
        self._pumping_event = threading.Event()
        self._pending = {}

        print(f"   [NRGSmart INIT] {self.name} ID:{self.pump_id} | "
              f"capacity={self.capacity:.2f}mL, refill_rate={self.refill_rate:.1f}, "
              f"prime_rate={self.prime_rate:.1f}, wash_count={self.wash_count} "
              f"(routing={self.ROUTING_MODE})")

    # ─────────────────────────────────────────────────────────────────
    # 연결 수명주기 (hw_manager 계약)
    # ─────────────────────────────────────────────────────────────────
    def connect(self):
        return self.driver.connect()

    def disconnect(self):
        self.driver.disconnect()

    def get_pressure(self):
        """압력 센서 없음 — SafetyManager 호환 0.0 (과압 감시 배제 라인, 헤더 참조)."""
        return 0.0

    # ─────────────────────────────────────────────────────────────────
    # 내부 유틸
    # ─────────────────────────────────────────────────────────────────
    def _fw_volume_ml(self):
        """펌웨어 volume(µL) → mL. 실패 시 None."""
        try:
            return float(self.device["volume"]) / 1000.0
        except Exception:
            return None

    def _valve_pos(self):
        """마지막으로 '쓰인' 밸브 위치 — robochem parameter.last_known_value 가 진실원.

        @codesyncer-decision: 로컬 캐시 금지 — PumpVolume/FillPump 태스크도 밸브를
        직접 쓰므로(reverse 규약) 별도 캐시는 반드시 desync 된다. 실제 desync 시
        도징이 reservoir 방향으로 나가는 치명 경로가 에뮬레이터 테스트에서 확인됨."""
        try:
            v = self.device.parameter_by_name("valve_setpoint").last_known_value
            return str(v) if v is not None else None
        except Exception:
            return None

    def _ensure_valve(self, pos: str):
        """main valve 를 'ON'(system)/'OFF'(reservoir) 로. 이미 그 위치면 시리얼 생략
        (Mrv-01B 이동 ack 는 최대 4초 — 불필요 전송이 스태거를 깨지 않도록)."""
        if self._valve_pos() == pos:
            return
        self.device["valve_setpoint"] = pos

    def _clear_stops(self):
        for pname in ("pump", "zero"):
            try:
                self.device.stop_parameter(pname, stop=False)
            except Exception:
                pass

    def _spawn(self, job, label):
        self._bg_error = None
        self._pumping_event = threading.Event()
        # @codesyncer-decision: stop 이벤트 clear 는 스레드 시작 '전' 호출 스레드에서
        #   동기 수행 — wrapped() 안에서 하면 start() 직후 도착한 stop() 의 이벤트를
        #   늦게 깬 bg 가 clear 해버리는 TOCTOU 로 정지 요청이 유실될 수 있다.
        self._clear_stops()

        def wrapped():
            try:
                job()
            except Exception as e:
                self._bg_error = e
                print(f"❌ [{self.name}] {label} error: {e}")
            finally:
                self.driver._is_running_flag = False

        self.driver._is_running_flag = True
        self._bg = threading.Thread(target=wrapped, daemon=True,
                                    name=f"nrg_{label}_{self.name}")
        self._bg.start()

    def _join_bg(self, label, timeout=None):
        """bg 종료 대기 + 에러 승격. _abort_refill 이 서면 stop 주입 후 join."""
        t = self._bg
        while t is not None and t.is_alive():
            if self._abort_refill:
                try:
                    self.device.stop_parameter("pump")
                    self.device.stop_parameter("zero")
                except Exception:
                    pass
                t.join(timeout=15.0)
                self._clear_stops()
                break
            t.join(timeout=0.2)
            if timeout is not None:
                timeout -= 0.2
                if timeout <= 0:
                    break
        if t is not None and t.is_alive():
            # 정지 미반영 상태로 참조 해제 — 다음 동작과 시리얼 공유 위험을 크게 알림
            print(f"❌ [{self.name}] {label}: 백그라운드 태스크가 정지에 응답하지 않음 "
                  f"(orphan thread) — 하드웨어/펌웨어 점검 필요")
        self._bg = None
        if self._bg_error is not None:
            err, self._bg_error = self._bg_error, None
            # 타입드 에러(MotorError=막힘 등)를 엔진 Emergency Stop 경로로 승격
            raise RuntimeError(f"[{self.name}] {label}: {err}")

    # ─────────────────────────────────────────────────────────────────
    # Dosing (엔진 dosing 루프 계약: set_flow → start 논블로킹 → stop)
    # ─────────────────────────────────────────────────────────────────
    def set_flow(self, rate):
        """유속 목표값 기록만 — 하드웨어 전송은 start() 의 태스크가 수행."""
        self.target_flow = float(rate)

    def start(self):
        """논블로킹 토출 시작 — 시린지 가용량만큼 백그라운드 PumpVolume."""
        if self.is_refilling or self.running:
            return
        rate = float(self.target_flow)
        if rate <= 0:
            return
        if rate > self.driver.max_flowrate + 1e-9:
            # 클램프 금지(당량비 왜곡) — 거부하고 크게 알림
            self.status = f"ERROR flow {rate:.2f} > max {self.driver.max_flowrate:.2f}"
            print(f"❌ [{self.name}] dosing rate {rate:.2f} mL/min > NRG max "
                  f"{self.driver.max_flowrate:.2f} — start 거부 (유속을 낮추세요)")
            return

        self.running = True

        def job():
            try:
                avail = self._fw_volume_ml()
                vol_ml = min(avail if avail is not None else self.current_vol,
                             max(float(self.current_vol), 0.0))
                if avail is not None and self.current_vol <= 0:
                    vol_ml = avail  # 엔진 부기 0 이어도 실물 있으면 실물 기준
                if vol_ml is None or vol_ml <= 0.001:
                    return
                self._ensure_valve("ON")
                self.status = f"Dosing @{rate:.2f}mL/min"
                PumpVolume.run(pump=self.device, volume=vol_ml * 1000.0,
                               flowrate=rate, allow_refill=False,
                               pumping_event=self._pumping_event)
            finally:
                self.running = False

        self._spawn(job, "dosing")

    def stop(self):
        """진행 중 이동(펌프/제로) 비동기 정지 + stop 이벤트 라이프사이클 마감."""
        self.running = False
        t = self._bg
        if t is not None and t.is_alive():
            try:
                self.device.stop_parameter("pump")
                self.device.stop_parameter("zero")
            except Exception:
                pass
            t.join(timeout=10.0)
        self._clear_stops()
        self.driver._is_running_flag = False

    # ─────────────────────────────────────────────────────────────────
    # Refill — prepare / trigger / complete
    # ─────────────────────────────────────────────────────────────────
    def refill_prepare(self, source_port_idx=1, volume=None):
        """단일 소스 흡인 준비. source_port_idx 는 수신만 하고 무시(내장밸브 라우팅).
        @return: True=trigger 대기, False=스킵."""
        if self.is_refilling:
            return False
        self.is_refilling = True
        self._abort_refill = False

        # @codesyncer-decision: 여유량은 펌웨어 실측 부피 기준 — 엔진이 step 시작마다
        #   current_vol=0 으로 리셋해도 물리 잔류물이 있으면 소프트 부기 기준 과충전이
        #   robochem 용량 가드(ParameterError)에 걸린다. 실측 headroom 으로 잔류물을
        #   자연 흡수 (1-소스라 잔류물=같은 액체, 화학적으로 무해).
        #   단 최초 리필(0점 전)은 펌웨어 값이 무의미 → trigger 의 zero EMPTY 가
        #   0점을 만들므로 basis=0 (과소충전 방지).
        if not self._zeroed:
            basis = 0.0
        else:
            fw = self._fw_volume_ml()
            basis = fw if fw is not None else max(self.current_vol, 0.0)
        available = max(0.0, self.capacity - basis)
        requested = min(float(volume), self.capacity) if volume is not None else self.capacity
        fill_vol = min(requested, available)
        if fill_vol < 0.01:
            print(f"   [{self.name}] Refill skipped "
                  f"(syringe {self.current_vol:.2f}/{self.capacity:.2f}mL)")
            self.is_refilling = False
            return False

        self._pending = {"fill_ml": fill_vol}
        self.status = f"Refill: {fill_vol:.2f}mL @{self.refill_rate:.1f}mL/min (reservoir)"
        print(f"⚡ [{self.name} ID:{self.pump_id}] REFILL {fill_vol:.2f}mL from reservoir "
              f"(port {source_port_idx} 인자는 internal_valve 모드에서 무시)")
        return True

    def refill_trigger(self):
        fill_ml = float(self._pending.get("fill_ml", 0.0))

        def job():
            self._ensure_valve("OFF")            # reservoir 측
            if not self._zeroed:
                # 플런저 0점 확정 — 잔류물은 reservoir 로 되밀림
                self.status = "Refill: zero(EMPTY)"
                self.device["enable"] = "ON"
                self.device["zero"] = "EMPTY"
                self._zeroed = True
            self.status = f"Withdrawing {fill_ml:.2f}mL"
            # reverse=True = reservoir 위치에서 펌핑 (robochem 규약: 기본은 system 측)
            PumpVolume.run(pump=self.device, volume=-fill_ml * 1000.0,
                           flowrate=self.refill_rate, allow_refill=False,
                           reverse=True,
                           pumping_event=self._pumping_event)
            self._ensure_valve("ON")             # system 복귀 (FillPump 규약과 동일)

        self._spawn(job, "refill")

    def refill_complete(self):
        try:
            self._join_bg("refill")
            fw = self._fw_volume_ml()
            if fw is not None:
                self.current_vol = min(fw, self.capacity)
            elif not self._abort_refill:
                self.current_vol = min(self.current_vol + float(self._pending.get("fill_ml", 0.0)),
                                       self.capacity)
            if self._abort_refill:
                self.status = f"Refill Aborted (~{self.current_vol:.2f}mL)"
                print(f"   [{self.name}] Refill aborted (~{self.current_vol:.2f}mL)")
            else:
                self.status = f"Refill Done ({self.current_vol:.2f}mL)"
                print(f"✅ [{self.name}] Refill Complete ({self.current_vol:.2f}mL).")
        finally:
            self.is_refilling = False

    def refill(self, source_port_idx=1, volume=None):
        """단독 실행용 (prepare+trigger+complete 순차)."""
        if self.refill_prepare(source_port_idx, volume):
            self.refill_trigger()
            self.refill_complete()

    # ─────────────────────────────────────────────────────────────────
    # Prime — 시린지 내용물을 system(반응기 방향)으로 토출
    # ─────────────────────────────────────────────────────────────────
    def prime_prepare(self):
        if self.is_refilling:
            return False
        self.is_refilling = True
        self._abort_refill = False
        fw = self._fw_volume_ml()
        prime_ml = fw if fw is not None else max(self.current_vol, 0.0)
        if prime_ml <= 0.005:
            self.is_refilling = False
            return False
        self._pending = {"prime_ml": prime_ml}
        self.status = f"Prime: {prime_ml:.2f}mL → system @{self.prime_rate:.1f}mL/min"
        print(f"   [{self.name}] PRIME {prime_ml:.2f}mL → system")
        return True

    def prime_trigger(self):
        prime_ml = float(self._pending.get("prime_ml", 0.0))

        def job():
            self._ensure_valve("ON")
            self.status = f"Priming {prime_ml:.2f}mL"
            PumpVolume.run(pump=self.device, volume=prime_ml * 1000.0,
                           flowrate=self.prime_rate, allow_refill=False,
                           pumping_event=self._pumping_event)

        self._spawn(job, "prime")

    def prime_complete(self):
        try:
            self._join_bg("prime")
            fw = self._fw_volume_ml()
            self.current_vol = max(0.0, fw) if fw is not None else 0.0
            self.status = f"Prime Done (remaining={self.current_vol:.2f}mL)"
            print(f"   [{self.name}] Prime complete (remaining={self.current_vol:.2f}mL)")
        finally:
            self.is_refilling = False

    def prime_reactor(self):
        if self.prime_prepare():
            self.prime_trigger()
            self.prime_complete()

    # ─────────────────────────────────────────────────────────────────
    # Wash — reservoir 위치 FILL→EMPTY (기포 퍼지). 폐액 포트 없음 → 인자 무시
    # ─────────────────────────────────────────────────────────────────
    def wash_withdraw_prepare(self, solvent_port=1):
        if self.is_refilling:
            return False
        self.is_refilling = True
        self._abort_refill = False
        self.status = "Wash: zero(FILL) @reservoir"
        return True

    def wash_withdraw_trigger(self):
        def job():
            self._ensure_valve("OFF")
            self.device["enable"] = "ON"
            self.device["zero"] = "FILL"

        self._spawn(job, "wash_fill")

    def wash_withdraw_complete(self):
        try:
            self._join_bg("wash(FILL)")
            self._zeroed = True
            fw = self._fw_volume_ml()
            self.current_vol = min(fw, self.capacity) if fw is not None else self.capacity
            self.status = f"Wash: filled {self.current_vol:.2f}mL"
        finally:
            self.is_refilling = False

    def wash_infuse_prepare(self, waste_port=12):
        if self.is_refilling:
            return False
        self.is_refilling = True
        self._abort_refill = False
        self.status = "Wash: zero(EMPTY) @reservoir"
        return True

    def wash_infuse_trigger(self):
        def job():
            self._ensure_valve("OFF")
            self.device["enable"] = "ON"
            self.device["zero"] = "EMPTY"

        self._spawn(job, "wash_empty")

    def wash_infuse_complete(self):
        try:
            self._join_bg("wash(EMPTY)")
            self._zeroed = True
            self.current_vol = 0.0
            self.status = "Wash cycle done (bubble purge)"
        finally:
            self.is_refilling = False

    def wash_cycle(self, solvent_port=1, waste_port=12):
        """단독 실행용. 엔진 순서(withdraw→infuse)와 동일하게 FILL→EMPTY."""
        if self.wash_withdraw_prepare(solvent_port):
            self.wash_withdraw_trigger()
            self.wash_withdraw_complete()
        if self.wash_infuse_prepare(waste_port):
            self.wash_infuse_trigger()
            self.wash_infuse_complete()

    def __repr__(self):
        return (f"<NRGSmartPump name={self.name} id={self.pump_id} "
                f"cap={self.capacity:.2f}mL routing={self.ROUTING_MODE}>")
