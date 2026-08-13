# --- START OF FILE engine/simpy_engine.py ---
"""
SimPy 기반 Flow Chemistry 시퀀스 엔진

@codesyncer-decision: 기존 StrictSequenceEngine의 time.sleep() 기반 제어를
SimPy 이산 이벤트 기반으로 리팩토링. 병렬 펌프 제어 및 시뮬레이션 테스트 지원.

@codesyncer-context:
- realtime=True: 실제 하드웨어 제어 (PyQt5와 별도 스레드에서 실행)
- realtime=False: 시뮬레이션 모드 (빠른 테스트/검증용)
"""

import time
import math
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

try:
    import simpy
    import simpy.rt
    HAS_SIMPY = True
except ImportError:
    HAS_SIMPY = False
    print("!!! SimPy가 설치되지 않았습니다. pip install simpy 실행 필요")

from .flow_engine import FlowEngine
from .safety_manager import SafetyError


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 상태 및 데이터 정의
# ═══════════════════════════════════════════════════════════════════════════════

class ValvePosition(Enum):
    """밸브 위치 상태"""
    WASTE = 1       # 폐기물 / Source / Refill
    REACTOR = 2     # 반응기 / Infuse / Collect


class PumpState(Enum):
    """펌프 상태"""
    IDLE = "idle"
    INFUSING = "infusing"
    REFILLING = "refilling"


@dataclass
class FlowStep:
    """
    단일 실험 단계 정의

    @codesyncer-context: 기존 sequence_plan의 exp dict와 매핑
    - step_id: idx + 1
    - temp_target: exp['temp']
    - volume_ml: exp['vol_ml']
    - flows: exp['flows']
    - inlet_ports: exp['inlet_ports']
    - collect_per_tube: exp.get('collect_volume_per_tube', 1.5)
    """
    step_id: int
    temp_target: float              # 목표 온도 (°C)
    volume_ml: float                # 총 주입량 (mL)
    flows: Dict[str, float]         # 펌프별 유속 {펌프명: mL/min}
    inlet_ports: Dict[str, int] = field(default_factory=dict)  # 시약 포트 맵
    collect_per_tube: float = 1.5   # 튜브당 수거량 (mL)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 하드웨어 SimPy 래퍼
# ═══════════════════════════════════════════════════════════════════════════════

class SimPyPumpWrapper:
    """
    SimPy 기반 펌프 제어 래퍼

    @codesyncer-decision: 시린지 펌프(ChemyxSmartPump)와 연속 펌프(Vapourtec) 모두 지원
    - 시린지 펌프: 자동 리필 로직 포함
    - 연속 펌프: 리필 없이 연속 주입
    """

    # @codesyncer-inference: 타이밍 파라미터 - 실측 필요
    DEFAULT_START_DELAY = 0.2       # 펌프 시작 지연 (초)
    DEFAULT_STOP_DELAY = 0.1        # 펌프 정지 지연 (초)
    VALVE_SWITCH_SETTLE = 1.0       # 밸브 전환 후 안정화 시간 (초)

    def __init__(self, env: 'simpy.Environment', name: str,
                 hw_driver=None, syringe_volume: float = 10.0,
                 is_syringe: bool = True,
                 refill_rate: float = 20.0, prime_rate: float = 10.0,
                 wash_speed: float = 15.0, wash_count: int = 2,
                 wash_volume: float = 5.0):
        """
        Args:
            env: SimPy 환경
            name: 펌프 이름 (예: "PumpA")
            hw_driver: 실제 하드웨어 드라이버 (MockPump, ChemyxSmartPump 등)
            syringe_volume: 시린지 용량 (mL), 연속펌프는 무시됨
            is_syringe: True면 시린지 펌프 (리필 필요)
            refill_rate: 시린지 리필 속도 (mL/min) - syringe_refill_rate
            prime_rate: 반응기 프라이밍 속도 (mL/min) - priming_rate_ml_min
            wash_speed: 세척 속도 (mL/min) - per-pump 설정
            wash_count: 세척 반복 횟수 - per-pump 설정
            wash_volume: 세척 부피 (mL) - per-pump 설정
        """
        self.env = env
        self.name = name
        self.hw = hw_driver
        self.syringe_volume = syringe_volume
        self.is_syringe = is_syringe

        # @codesyncer-decision: per-pump 세척/리필 설정 — hw_manager.py spec에서 전달
        self.refill_rate = refill_rate
        self.prime_rate = prime_rate
        self.wash_speed = wash_speed
        self.wash_count = wash_count
        self.wash_volume = wash_volume

        # 시린지 잔량 추적
        self.current_volume = syringe_volume if is_syringe else float('inf')

        self.state = PumpState.IDLE
        self.resource = simpy.Resource(env, capacity=1)  # 동시 작업 방지

    def infuse(self, volume_ml: float, flow_rate: float, source_port: int = 1):
        """
        주입 프로세스 시작

        Args:
            volume_ml: 주입량 (mL)
            flow_rate: 유속 (mL/min)
            source_port: 시약 포트 (시린지 펌프용)

        Returns:
            SimPy Process
        """
        return self.env.process(self._infuse_process(volume_ml, flow_rate, source_port))

    def _infuse_process(self, volume_ml: float, flow_rate: float, source_port: int):
        """내부 주입 프로세스"""
        with self.resource.request() as req:
            yield req

            self.state = PumpState.INFUSING
            remaining = volume_ml

            while remaining > 0.01:  # 0.01mL 이하는 무시
                # 시린지 펌프: 잔량 확인 및 리필
                if self.is_syringe and self.current_volume <= 0.1:
                    print(f"  [{self.env.now:.1f}s] {self.name}: Refill needed (remaining {self.current_volume:.2f}mL)")
                    yield self.env.process(self._refill_process(source_port))

                # 이번에 주입할 양 계산
                if self.is_syringe:
                    inject_amount = min(remaining, self.current_volume)
                else:
                    inject_amount = remaining

                # 주입 시간 계산 (분 -> 초)
                inject_time = (inject_amount / flow_rate) * 60

                # 실제 하드웨어 명령
                if self.hw:
                    try:
                        self.hw.set_rate(flow_rate)
                        self.hw.start()
                    except Exception as e:
                        print(f"  [!] {self.name} 하드웨어 오류: {e}")

                print(f"  [{self.env.now:.1f}s] {self.name}: Infuse {inject_amount:.2f}mL @ {flow_rate}mL/min ({inject_time:.1f}s)")

                # 시작 지연
                yield self.env.timeout(self.DEFAULT_START_DELAY)

                # 주입 시간 대기
                yield self.env.timeout(inject_time - self.DEFAULT_START_DELAY - self.DEFAULT_STOP_DELAY)

                # 정지 지연
                yield self.env.timeout(self.DEFAULT_STOP_DELAY)

                # 상태 업데이트
                if self.is_syringe:
                    self.current_volume -= inject_amount
                remaining -= inject_amount

                # 하드웨어 정지
                if self.hw:
                    try:
                        self.hw.stop()
                    except Exception as e:
                        print(f"  [!] {self.name} 정지 오류: {e}")

            self.state = PumpState.IDLE
            print(f"  [{self.env.now:.1f}s] {self.name}: Infuse complete")

    def _refill_process(self, port: int = 1, volume: float = None):
        """시린지 리필 프로세스 (WITHDRAW)
        @codesyncer-decision: volume 파라미터 추가 — inject_vol만 정확히 충전 가능
        - volume=None: 시린지 전체 용량 충전 (기존 동작, initial_refill용)
        - volume=값: 지정량만 충전 (prefill용)
        """
        self.state = PumpState.REFILLING

        fill_vol = volume if volume is not None else self.syringe_volume
        available = max(0, self.syringe_volume - self.current_volume)
        fill_vol = min(fill_vol, available)

        if fill_vol < 0.1:
            self.state = PumpState.IDLE
            return

        # 밸브 전환 시간 (12-way→port, 3-way→SOURCE)
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)

        refill_time = (fill_vol / self.refill_rate) * 60.0
        print(f"  [{self.env.now:.1f}s] {self.name}: Refill start (port {port}, {fill_vol:.2f}mL, {refill_time:.1f}s @ {self.refill_rate}mL/min)...")

        if self.hw and hasattr(self.hw, 'refill'):
            try:
                self.hw.refill(port, volume=fill_vol)
            except Exception as e:
                print(f"  [!] {self.name} refill error: {e}")

        yield self.env.timeout(refill_time)

        self.current_volume = min(self.current_volume + fill_vol, self.syringe_volume)
        self.state = PumpState.IDLE
        print(f"  [{self.env.now:.1f}s] {self.name}: Refill complete ({fill_vol:.2f}mL, total={self.current_volume:.2f}mL)")

    def wash_cycle(self, solvent_port=1, waste_port=12):
        """1회 세척 사이클 프로세스
        @codesyncer-decision: pump_chemyx_smart.py wash_cycle()과 동일 로직
        - Phase 1: INFUSE to waste (시린지 내용물 waste로 배출)
        - Phase 2: WITHDRAW from solvent (신선한 솔벤트 흡입)
        """
        return self.env.process(self._wash_cycle_process(solvent_port, waste_port))

    def _wash_cycle_process(self, solvent_port: int, waste_port: int):
        """내부 세척 사이클 프로세스"""
        actual_vol = min(self.wash_volume, self.syringe_volume)
        duration_calc = (actual_vol / self.wash_speed) * 60.0

        # Phase 1: INFUSE to waste — 12-way→waste, 3-way→SOURCE
        # @codesyncer-decision: INFUSE 먼저 — 초기 리필로 시린지가 가득 찬 상태
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)  # 밸브 전환
        print(f"  [{self.env.now:.1f}s] {self.name}: Wash INFUSE {actual_vol:.1f}mL → waste(port {waste_port}) @ {self.wash_speed}mL/min")
        self.state = PumpState.INFUSING

        yield self.env.timeout(duration_calc)
        self.current_volume = max(0, self.current_volume - actual_vol)

        # Phase 2: WITHDRAW from solvent — 12-way→솔벤트, 3-way→SOURCE
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)  # 밸브 전환
        print(f"  [{self.env.now:.1f}s] {self.name}: Wash WITHDRAW {actual_vol:.1f}mL ← solvent(port {solvent_port}) @ {self.wash_speed}mL/min")
        self.state = PumpState.REFILLING

        # @codesyncer-decision: Withdraw는 Infuse보다 느릴 수 있음 → 3배 시간 가정은 안전 마진
        # 시뮬레이션에서는 계산 시간 그대로 사용 (하드웨어는 자동 정지)
        yield self.env.timeout(duration_calc)
        self.current_volume = min(self.current_volume + actual_vol, self.syringe_volume)

        self.state = PumpState.IDLE
        print(f"  [{self.env.now:.1f}s] {self.name}: Wash cycle done ({actual_vol:.1f}mL @ {self.wash_speed}mL/min)")

    def prime_reactor(self):
        """반응기 프라이밍 프로세스
        @codesyncer-decision: pump_chemyx_smart.py prime_reactor()과 동일 로직
        - 시린지 잔량을 반응기 방향으로 INFUSE (프라이밍)
        - volume = wash_volume, rate = prime_rate
        """
        return self.env.process(self._prime_reactor_process())

    def _prime_reactor_process(self):
        """내부 반응기 프라이밍 프로세스"""
        prime_vol = self.wash_volume
        prime_time = (prime_vol / self.prime_rate) * 60.0

        # 3-way → REACTOR 전환
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)
        print(f"  [{self.env.now:.1f}s] {self.name}: Prime reactor INFUSE {prime_vol:.1f}mL @ {self.prime_rate}mL/min")
        self.state = PumpState.INFUSING

        if self.hw and hasattr(self.hw, 'prime_reactor'):
            try:
                self.hw.prime_reactor()
            except Exception as e:
                print(f"  [!] {self.name} prime error: {e}")

        yield self.env.timeout(prime_time)
        self.current_volume = max(0, self.current_volume - prime_vol)

        self.state = PumpState.IDLE
        print(f"  [{self.env.now:.1f}s] {self.name}: Prime complete (remaining={self.current_volume:.1f}mL)")

    def stop(self):
        """펌프 정지"""
        if self.hw:
            try:
                self.hw.stop()
            except:
                pass
        self.state = PumpState.IDLE


class SimPyValveWrapper:
    """
    SimPy 기반 밸브 제어 래퍼

    @codesyncer-inference: switch_time은 밸브 종류에 따라 다름
    - Arduino 2-way: ~0.5초
    - Runze SV07: 포트 수에 비례 (~0.3초/포트)
    """

    DEFAULT_SWITCH_TIME = 0.5  # 밸브 전환 시간 (초)

    def __init__(self, env: 'simpy.Environment', name: str, hw_driver=None):
        self.env = env
        self.name = name
        self.hw = hw_driver
        self.position = ValvePosition.WASTE

    def set_position(self, pos: ValvePosition):
        """밸브 위치 변경 프로세스"""
        return self.env.process(self._switch_process(pos))

    def _switch_process(self, pos: ValvePosition):
        """내부 전환 프로세스"""
        if self.position == pos:
            return

        print(f"  [{self.env.now:.1f}s] {self.name}: {self.position.name} -> {pos.name}")

        if self.hw:
            try:
                self.hw.set_position(pos.value)
            except Exception as e:
                print(f"  [!] {self.name} 전환 오류: {e}")

        yield self.env.timeout(self.DEFAULT_SWITCH_TIME)
        self.position = pos


class SimPyCollectorWrapper:
    """
    SimPy 기반 분취기 제어 래퍼

    @codesyncer-inference: move_time_per_tube는 분취기 모델에 따라 다름
    - Colosseum: ~1.0초/튜브 (원형 이동)
    """

    DEFAULT_MOVE_TIME_PER_TUBE = 1.0  # 튜브당 이동 시간 (초)
    DEFAULT_HOME_TIME = 3.0           # 홈 이동 시간 (초)

    def __init__(self, env: 'simpy.Environment', hw_driver=None, total_tubes: int = 40):
        self.env = env
        self.hw = hw_driver
        self.total_tubes = total_tubes
        self.current_tube = 1

        self.resource = simpy.Resource(env, capacity=1)

    @property
    def is_connected(self) -> bool:
        if self.hw:
            return getattr(self.hw, 'is_connected', False)
        return True  # 시뮬레이션 모드

    def home(self):
        """홈 위치로 이동"""
        return self.env.process(self._home_process())

    def _home_process(self):
        with self.resource.request() as req:
            yield req

            print(f"  [{self.env.now:.1f}s] Collector: Homing...")

            if self.hw and self.is_connected:
                try:
                    self.hw.home()
                except Exception as e:
                    print(f"  [!] Collector home 오류: {e}")

            yield self.env.timeout(self.DEFAULT_HOME_TIME)
            self.current_tube = 0  # 홈 위치
            print(f"  [{self.env.now:.1f}s] Collector: Home complete")

    def move_to_tube(self, tube_num: int):
        """특정 튜브로 이동"""
        return self.env.process(self._move_process(tube_num))

    def _move_process(self, tube_num: int):
        with self.resource.request() as req:
            yield req

            if tube_num == self.current_tube:
                return

            if tube_num > self.total_tubes:
                raise RuntimeError(f"Collector capacity exceeded! (requested: {tube_num}, max: {self.total_tubes})")

            distance = abs(tube_num - self.current_tube)
            move_time = distance * self.DEFAULT_MOVE_TIME_PER_TUBE

            print(f"  [{self.env.now:.1f}s] Collector: Tube {self.current_tube} -> {tube_num} ({move_time:.1f}s)")

            if self.hw and self.is_connected:
                try:
                    self.hw.move_to_tube(tube_num)
                except Exception as e:
                    print(f"  [!] Collector 이동 오류: {e}")

            yield self.env.timeout(move_time)
            self.current_tube = tube_num

    def advance(self):
        """다음 튜브로 이동"""
        return self.move_to_tube(self.current_tube + 1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SimPy 시퀀스 엔진
# ═══════════════════════════════════════════════════════════════════════════════

class SimPyFlowEngine(FlowEngine):
    """
    SimPy 기반 Flow Chemistry 시퀀스 엔진

    @codesyncer-decision: FlowEngine 상속으로 기존 로깅/안전 기능 유지

    사용법:
        # 시뮬레이션 모드 (테스트용)
        engine = SimPyFlowEngine(config, pumps, valves, heater, safety, realtime=False)

        # 실시간 모드 (실제 실험)
        engine = SimPyFlowEngine(config, pumps, valves, heater, safety, realtime=True)
    """

    def __init__(self, config, pumps, valves, heater, safety_mgr,
                 signals=None, collector=None,
                 realtime: bool = True, time_factor: float = 1.0):
        """
        Args:
            config: SystemConfig
            pumps: Dict[str, PumpDriver]
            valves: Dict[str, ValveDriver]
            heater: HeaterDriver
            safety_mgr: SafetyManager
            signals: WorkerSignals (Qt 시그널)
            collector: CollectorDriver
            realtime: True면 실시간 실행, False면 시뮬레이션
            time_factor: 실시간 모드 시간 배율 (1.0 = 실시간)
        """
        super().__init__(config, pumps, valves, heater, safety_mgr)

        if not HAS_SIMPY:
            raise ImportError("SimPy가 설치되지 않았습니다. pip install simpy")

        self.signals = signals
        self.collector_hw = collector
        self.realtime = realtime
        self.time_factor = time_factor

        # SimPy 환경 (run_sequence에서 생성)
        self.env: Optional[simpy.Environment] = None

        # SimPy 래퍼들 (run_sequence에서 초기화)
        self.simpy_pumps: Dict[str, SimPyPumpWrapper] = {}
        self.simpy_valves: Dict[str, SimPyValveWrapper] = {}
        self.simpy_collector: Optional[SimPyCollectorWrapper] = None

        # 시퀀스 제어
        self.abort_flag = False
        self.pause_event: Optional[threading.Event] = None

        # Zone 파라미터
        # @codesyncer-decision: system_params에서 직접 읽기 — 하드코딩 제거
        # strict_engine.py __init__과 동일 방식
        if hasattr(config, 'config_data'):
            sp = config.config_data.get("system_params", {})
            self.vol_reactor = config.reactor_vol
            self.vol_post_common = float(sp.get("post_reactor_vol_ml", 2.0))
            self.vol_collection = float(sp.get("collection_line_vol_ml", 1.0))
        else:
            # Mock/Test config fallback
            self.vol_reactor = 0.0
            self.vol_post_common = 2.0
            self.vol_collection = 1.0
        self.temp_tolerance = 0.3

        # 분취기 상태
        self.collector_start_tube = 1
        self.current_tube = 1

        # UI 참조 (Collection 탭)
        self.tab_collection = None

        # 현재 온도 (시뮬레이션용)
        self._sim_temp = 25.0

    def _create_simpy_env(self):
        """SimPy 환경 생성"""
        if self.realtime:
            self.env = simpy.rt.RealtimeEnvironment(factor=self.time_factor)
        else:
            self.env = simpy.Environment()

    def _init_simpy_wrappers(self):
        """SimPy 하드웨어 래퍼 초기화"""
        # @codesyncer-decision: per-pump 설정을 하드웨어 드라이버에서 읽어 SimPy 래퍼에 전달
        sp = self.cfg.config_data.get("system_params", {}) if hasattr(self.cfg, 'config_data') else {}
        default_refill_rate = float(sp.get("syringe_refill_rate", 20.0))
        default_prime_rate = float(sp.get("priming_rate_ml_min", 10.0))

        # 펌프 래퍼
        for p_name, p_obj in self.pumps.items():
            # 시린지 펌프 여부 판단
            is_syringe = hasattr(p_obj, 'refill')
            # @codesyncer-decision: ChemyxSmartPump는 capacity, MockPump는 syringe_volume
            syringe_vol = getattr(p_obj, 'capacity', getattr(p_obj, 'syringe_volume', 10.0))

            # per-pump 설정 읽기 (ChemyxSmartPump에서)
            refill_rate = getattr(p_obj, 'refill_rate', default_refill_rate)
            prime_rate = getattr(p_obj, 'prime_rate', default_prime_rate)
            wash_speed = getattr(p_obj, 'wash_speed', 15.0)
            wash_count = getattr(p_obj, 'wash_count', 2)
            wash_volume = getattr(p_obj, 'wash_volume', 5.0)

            self.simpy_pumps[p_name] = SimPyPumpWrapper(
                self.env, p_name, hw_driver=p_obj,
                syringe_volume=syringe_vol, is_syringe=is_syringe,
                refill_rate=refill_rate, prime_rate=prime_rate,
                wash_speed=wash_speed, wash_count=wash_count,
                wash_volume=wash_volume
            )

        # 밸브 래퍼
        for v_name, v_obj in self.valves.items():
            self.simpy_valves[v_name] = SimPyValveWrapper(
                self.env, v_name, hw_driver=v_obj
            )

        # 분취기 래퍼
        if self.collector_hw:
            total_tubes = getattr(self.collector_hw, 'total_tubes', 40)
            self.simpy_collector = SimPyCollectorWrapper(
                self.env, hw_driver=self.collector_hw, total_tubes=total_tubes
            )

    def _emit_log(self, msg: str):
        """로그 출력 및 시그널 전송"""
        full_msg = f"[{self.env.now:.1f}s] {msg}"
        print(full_msg)
        if self.signals:
            self.signals.sig_log.emit(full_msg)
        self._log(msg)  # CSV 로깅

    def _emit_status(self, msg: str):
        """상태 업데이트 시그널"""
        if self.signals:
            self.signals.sig_status.emit(msg)

    # ─────────────────────────────────────────────────────────────────────────
    # 시퀀스 프로세스
    # ─────────────────────────────────────────────────────────────────────────

    def _heating_process(self, target_temp: float):
        """가열 대기 프로세스"""
        self._emit_log(f"Heating: target {target_temp}C")

        if self.heater:
            self.heater.set_temperature(target_temp)

        while True:
            if self.abort_flag:
                raise SafetyError("User abort")

            # 실제 온도 읽기
            curr_temp = None
            if self.heater:
                try:
                    curr_temp = self.heater.get_temperature()
                except:
                    pass

            # 온도를 읽지 못하면 시뮬레이션 모드
            if curr_temp is None:
                # 시뮬레이션: 간단한 1차 응답
                self._sim_temp += (target_temp - self._sim_temp) * 0.15
                curr_temp = self._sim_temp

            self._emit_status(f"Heating... {curr_temp:.1f} / {target_temp}C")

            if abs(curr_temp - target_temp) <= self.temp_tolerance:
                self._emit_log(f"Temp reached: {curr_temp:.1f}C")
                break

            yield self.env.timeout(1.0)

    def _switch_all_valves(self, pos: ValvePosition):
        """모든 밸브를 지정 위치로 전환"""
        switch_procs = []
        for v_name, v_wrapper in self.simpy_valves.items():
            if v_name != "Outlet":  # Outlet은 별도 제어
                switch_procs.append(v_wrapper.set_position(pos))

        if switch_procs:
            yield simpy.AllOf(self.env, switch_procs)

    def _initial_refill_process(self):
        """초기 리필: 모든 시린지 펌프를 솔벤트로 채움 (WITHDRAW)
        @codesyncer-decision: strict_engine Step 1.5 — 첫 실험에서만 실행
        - 온도 도달 후 초기 리필
        - 시린지 잔량 90% 미만인 펌프만 리필
        """
        self._emit_log("Initial Refill: All pumps WITHDRAW from solvent")

        refill_procs = []
        for p_name, p_wrapper in self.simpy_pumps.items():
            if p_wrapper.is_syringe and p_wrapper.current_volume < p_wrapper.syringe_volume * 0.9:
                refill_procs.append(self.env.process(p_wrapper._refill_process(1)))

        if refill_procs:
            yield simpy.AllOf(self.env, refill_procs)

        self._emit_log("Initial Refill complete")

    def _system_wash_process(self, flows):
        """시스템 세척: 각 펌프 wash_cycle (INFUSE→WITHDRAW) × wash_count 회
        @codesyncer-decision: strict_engine _execute_system_wash()과 동일 로직
        - wash_speed, wash_count, wash_volume: 펌프별 개별 설정값 사용
        - 모든 펌프 동시 wash_cycle (SimPy AllOf)
        """
        max_count = 0
        for p_name in flows:
            if p_name in self.simpy_pumps:
                p_wrapper = self.simpy_pumps[p_name]
                if p_wrapper.is_syringe and p_wrapper.wash_count > max_count:
                    max_count = p_wrapper.wash_count

        if max_count == 0:
            return

        self._emit_log(f"System Wash: {max_count} cycles")
        for p_name in flows:
            if p_name in self.simpy_pumps:
                pw = self.simpy_pumps[p_name]
                if pw.is_syringe:
                    self._emit_log(f"  {p_name}: wash_vol={pw.wash_volume:.1f}mL, speed={pw.wash_speed:.1f}mL/min, count={pw.wash_count}")

        for cycle in range(max_count):
            if self.abort_flag:
                break

            self._emit_log(f"Wash cycle {cycle + 1}/{max_count}")
            self._emit_status(f"시스템 세척 ({cycle + 1}/{max_count})")

            # 각 펌프 wash_cycle 병렬 실행
            wash_procs = []
            for p_name in flows:
                if p_name in self.simpy_pumps:
                    p_wrapper = self.simpy_pumps[p_name]
                    if p_wrapper.is_syringe and cycle < p_wrapper.wash_count:
                        wash_procs.append(p_wrapper.wash_cycle(solvent_port=1, waste_port=12))

            if wash_procs:
                yield simpy.AllOf(self.env, wash_procs)

        self._emit_log("System Wash complete")

    def _prefill_process(self, step: FlowStep):
        """
        시약 프리필: 반응기 프라이밍 → inject_vol만 시약 충전

        @codesyncer-decision: strict_engine과 동기화 (2026-04-10)
        - Phase 0: Prime reactor (세척 후 시린지 잔량을 반응기로 INFUSE)
        - Phase 1: inject_vol만 정확히 충전 (데드볼륨 보정 없음)
        - Phase 2, Phase 1b: 제거됨
        """
        total_flow = sum(step.flows.values())

        # ── Phase 0: 반응기 프라이밍 (INFUSE → REACTOR) ──
        self._emit_log("Pre-fill Phase 0: Prime reactor (INFUSE → REACTOR)")

        prime_procs = []
        for p_name in step.flows:
            if p_name in self.simpy_pumps:
                p_wrapper = self.simpy_pumps[p_name]
                if p_wrapper.is_syringe and p_wrapper.current_volume > 0.1:
                    prime_procs.append(p_wrapper.prime_reactor())

        if prime_procs:
            yield simpy.AllOf(self.env, prime_procs)

        if self.abort_flag:
            return

        # ── Phase 1: inject_vol만 정확히 충전 (WITHDRAW) ──
        # @codesyncer-decision: flush_vol/Phase 2/Phase 1b 전부 제거
        #   inject_vol = (pump_flow / total_flow) × target_vol
        #   → 3펌프 동시 소진 보장, 잔량 최소화
        #
        # ──── 데드볼륨 보정 복원 가이드 ────────────────────────────
        # 복원 시 fill_vol 계산을 아래처럼 변경:
        #   mixing_vol = float(getattr(self.cfg, "mixing_line_dead_vol", 0.0))
        #   flush_vol = (pump_flow / total_flow) * mixing_vol
        #   fill_vol = inject_vol + flush_vol
        # 그리고 Phase 2 (flush mixing line) 추가 필요
        # 백업 참조: "2026-04-10 백업(데드볼륨 제거)/" 폴더의 simpy_engine.py
        # ─────────────────────────────────────────────────────────
        self._emit_log("Pre-fill Phase 1: Reagent refill (inject_vol only)")

        refill_procs = []
        for p_name, flow_rate in step.flows.items():
            if p_name in self.simpy_pumps:
                p_wrapper = self.simpy_pumps[p_name]
                if p_wrapper.is_syringe and total_flow > 0:
                    source_port = step.inlet_ports.get(p_name, 2)
                    inject_vol = (flow_rate / total_flow) * step.volume_ml
                    fill_vol = min(inject_vol, p_wrapper.syringe_volume)
                    self._emit_log(f"  {p_name}: fill {fill_vol:.2f}mL (port {source_port})")
                    refill_procs.append(self.env.process(
                        p_wrapper._refill_process(source_port, volume=fill_vol)))

        if refill_procs:
            yield simpy.AllOf(self.env, refill_procs)

        self._emit_log("Pre-fill complete")

    def _injection_process(self, step: FlowStep):
        """시약 주입 프로세스"""
        total_flow = sum(step.flows.values())
        inject_time = (step.volume_ml / total_flow) * 60

        self._emit_log(f"Reagent injection: {step.volume_ml}mL ({inject_time:.0f}s)")

        # 밸브를 REACTOR로
        yield self.env.process(self._switch_all_valves(ValvePosition.REACTOR))

        # 모든 펌프 동시 주입
        inject_procs = []
        for p_name, flow_rate in step.flows.items():
            if p_name in self.simpy_pumps and flow_rate > 0:
                # 비율에 따른 부피 계산
                pump_vol = step.volume_ml * (flow_rate / total_flow)
                source_port = step.inlet_ports.get(p_name, 1)
                inject_procs.append(
                    self.simpy_pumps[p_name].infuse(pump_vol, flow_rate, source_port)
                )

        if inject_procs:
            yield simpy.AllOf(self.env, inject_procs)

        self._emit_log("Reagent injection complete")

    def _transit_process(self, step: FlowStep):
        """이송 프로세스 (반응물을 Outlet으로 밀어내기)"""
        total_flow = sum(step.flows.values())
        v_zone2_total = self.vol_reactor + self.vol_post_common

        # @codesyncer-decision: transit은 반응물 front가 outlet에 도달하도록 밀어냄
        # 이전 로직(v_zone2_total * 0.95)은 volume_ml(반응량) 미고려 → 대량 유실
        # 수정: v_zone2_total - volume_ml = front가 outlet 정확 도달
        transit_vol = max(0, v_zone2_total - step.volume_ml)
        transit_time = (transit_vol / total_flow) * 60

        self._emit_log(f"Transit: {transit_vol:.1f}mL ({transit_time:.0f}s)")

        # Solvent로 밀어내기
        transit_procs = []
        for p_name, flow_rate in step.flows.items():
            if p_name in self.simpy_pumps and flow_rate > 0:
                pump_vol = transit_vol * (flow_rate / total_flow)
                transit_procs.append(
                    self.simpy_pumps[p_name].infuse(pump_vol, flow_rate, source_port=1)
                )

        if transit_procs:
            yield simpy.AllOf(self.env, transit_procs)

        self._emit_log("Transit complete")

    def _collection_process(self, step: FlowStep):
        """
        다중 튜브 분취 프로세스

        @codesyncer-decision: 기존 StrictSequenceEngine의 다중 튜브 로직 유지
        - 마지막 튜브: 남은 부피만 수거
        - 수거 후 세척 튜브 1개 추가
        """
        if not self.simpy_collector:
            return

        total_flow = sum(step.flows.values())
        num_tubes = math.ceil(step.volume_ml / step.collect_per_tube)

        self._emit_log(f"Collection start: {num_tubes} tubes (Tube {self.current_tube}~)")

        # Outlet 밸브를 COLLECT로
        if "Outlet" in self.simpy_valves:
            yield self.simpy_valves["Outlet"].set_position(ValvePosition.REACTOR)

        # 각 튜브별 수거
        for tube_idx in range(num_tubes):
            if self.abort_flag:
                break

            current_tube_num = self.current_tube + tube_idx

            # @codesyncer-decision: transit에서 front가 이미 outlet까지 도달하므로 여유분 불필요
            # 이전 로직(v_zone2_total * 0.05)은 0.95배 transit과 쌍이었으나 제거
            if tube_idx == num_tubes - 1:
                # 마지막 튜브: 남은 부피
                remaining = step.volume_ml - (tube_idx * step.collect_per_tube)
                collect_vol = min(remaining, step.collect_per_tube)
            else:
                collect_vol = step.collect_per_tube

            self._emit_status(f"Collecting: Tube {current_tube_num} ({tube_idx+1}/{num_tubes})")
            self._emit_log(f"Tube {current_tube_num}: {collect_vol:.2f}mL")

            # 분취기 이동 (첫 튜브는 이미 위치)
            if tube_idx > 0:
                yield self.simpy_collector.move_to_tube(current_tube_num)
                if self.tab_collection:
                    self.tab_collection.update_position_display()

            # 수거 (펌핑)
            collect_time = (collect_vol / total_flow) * 60
            collect_procs = []
            for p_name, flow_rate in step.flows.items():
                if p_name in self.simpy_pumps and flow_rate > 0:
                    pump_vol = collect_vol * (flow_rate / total_flow)
                    collect_procs.append(
                        self.simpy_pumps[p_name].infuse(pump_vol, flow_rate, source_port=1)
                    )

            if collect_procs:
                yield simpy.AllOf(self.env, collect_procs)

        # Collection 라인 세척 (세척 튜브)
        wash_tube = self.current_tube + num_tubes
        self._emit_log(f"Collection line wash: Tube {wash_tube}")

        yield self.simpy_collector.move_to_tube(wash_tube)
        if self.tab_collection:
            self.tab_collection.update_position_display()

        wash_vol = self.vol_collection * 1.0
        wash_time = (wash_vol / total_flow) * 60

        wash_procs = []
        for p_name, flow_rate in step.flows.items():
            if p_name in self.simpy_pumps and flow_rate > 0:
                pump_vol = wash_vol * (flow_rate / total_flow)
                wash_procs.append(
                    self.simpy_pumps[p_name].infuse(pump_vol, flow_rate, source_port=1)
                )

        if wash_procs:
            yield simpy.AllOf(self.env, wash_procs)

        # 현재 튜브 위치 업데이트
        self.current_tube += num_tubes + 1  # 반응물 튜브 + 세척 튜브

        # Outlet 밸브 복귀
        if "Outlet" in self.simpy_valves:
            yield self.simpy_valves["Outlet"].set_position(ValvePosition.WASTE)

        self._emit_log("Collection complete")

    def _post_injection_prime_process(self, step: FlowStep):
        """injection 후 시약 잔량을 reactor로 밀어냄
        @codesyncer-decision: strict_engine Step 4.5a와 동일
        빈 시린지 상태에서 용매를 채워야 시약/용매 혼합 방지
        """
        prime_procs = []
        for p_name in step.flows:
            if p_name in self.simpy_pumps:
                p_wrapper = self.simpy_pumps[p_name]
                if p_wrapper.is_syringe and p_wrapper.current_volume > 0.1:
                    prime_procs.append(p_wrapper.prime_reactor())

        if prime_procs:
            self._emit_log(f"  Post-injection prime: pushing residual to reactor")
            yield simpy.AllOf(self.env, prime_procs)

    def _solvent_refill_process(self, step: FlowStep):
        """용매 충전: v_zone2_total / 시린지 갯수로 분배
        @codesyncer-decision: strict_engine Step 4.5b와 동일
        transit+collection에 필요한 총 용매량을 사전 계산하여 중간 리필 방지
        """
        v_zone2_total = self.vol_reactor + self.vol_post_common
        syringe_pumps = [p for p in step.flows if p in self.simpy_pumps
                         and self.simpy_pumps[p].is_syringe]
        num_pumps = max(1, len(syringe_pumps))
        per_pump_solvent = v_zone2_total / num_pumps

        self._emit_log(f"  Solvent refill: {per_pump_solvent:.2f}mL/pump (total_push={v_zone2_total:.2f}/{num_pumps}pumps)")

        refill_procs = []
        for p_name in syringe_pumps:
            p_wrapper = self.simpy_pumps[p_name]
            fill_vol = min(per_pump_solvent, p_wrapper.syringe_volume)
            refill_procs.append(self.env.process(
                p_wrapper._refill_process(port=1, volume=fill_vol)))

        if refill_procs:
            yield simpy.AllOf(self.env, refill_procs)

    def _run_step_process(self, step: FlowStep):
        """단일 실험 단계 프로세스"""
        self._emit_log(f"=== Step {step.step_id} Start ===")
        self._emit_status(f"Step {step.step_id} 준비 중...")

        # @codesyncer-decision: 매 step 시작 시 current_vol 리셋 (strict_engine과 동기화)
        # 이전 step의 추적 오차가 다음 step에 전파되지 않도록 함
        for p_wrapper in self.simpy_pumps.values():
            if p_wrapper.is_syringe:
                p_wrapper.current_volume = 0.0

        # 0. 분취기 사전 이동
        if self.simpy_collector and self.simpy_collector.is_connected:
            yield self.simpy_collector.move_to_tube(self.current_tube)
            if self.tab_collection:
                self.tab_collection.update_position_display()

        # 1. 가열
        yield self.env.process(self._heating_process(step.temp_target))
        if self.abort_flag:
            return

        # 1.5. 초기 리필 (첫 실험에서만)
        if not self._initial_refill_done:
            yield self.env.process(self._initial_refill_process())
            self._initial_refill_done = True
            if self.abort_flag:
                return

        # 2. 시스템 세척
        yield self.env.process(self._system_wash_process(step.flows))
        if self.abort_flag:
            return

        # 3. 시약 프리필 (inject_vol만 충전, 데드볼륨 보정 없음)
        yield self.env.process(self._prefill_process(step))
        if self.abort_flag:
            return

        # 4. 시약 주입
        yield self.env.process(self._injection_process(step))
        if self.abort_flag:
            return

        # 4.5a. injection 후 잔량 prime (시린지 비우기)
        yield self.env.process(self._post_injection_prime_process(step))
        if self.abort_flag:
            return

        # 4.5b. 용매 충전 (v_zone2_total / 시린지 수)
        yield self.env.process(self._solvent_refill_process(step))
        if self.abort_flag:
            return

        # 5. 이송
        yield self.env.process(self._transit_process(step))
        if self.abort_flag:
            return

        # 6. 분취
        yield self.env.process(self._collection_process(step))

        self._emit_log(f"=== Step {step.step_id} Complete ===")

        if self.signals:
            self.signals.sig_progress.emit(step.step_id)

    def _sequence_process(self, steps: List[FlowStep]):
        """전체 시퀀스 프로세스"""
        self._emit_log("Sequence start")
        # @codesyncer(검증 2026-08-11): trace=False — SimPy 는 가상시간(env.now)으로
        #   진행하므로 wall-clock 기반 트레이스는 타임라인이 무의미하고, 이 경로엔
        #   trace.close() 도 없어 실행마다 파일 핸들이 누수됐다 (Windows 파일 락).
        self._init_log("SimPy_Sequence", trace=False)
        self._initial_refill_done = False  # 초기 리필 플래그

        # 초기화: 모든 밸브 WASTE
        yield self.env.process(self._switch_all_valves(ValvePosition.WASTE))
        if "Outlet" in self.simpy_valves:
            yield self.simpy_valves["Outlet"].set_position(ValvePosition.WASTE)

        # 분취기 홈 및 시작 위치 이동
        self.current_tube = self.collector_start_tube
        if self.simpy_collector and self.simpy_collector.is_connected:
            yield self.simpy_collector.home()
            yield self.simpy_collector.move_to_tube(self.current_tube)

        # 각 단계 실행
        for step in steps:
            if self.abort_flag:
                break
            yield self.env.process(self._run_step_process(step))

        # 종료 정리
        for p_wrapper in self.simpy_pumps.values():
            p_wrapper.stop()

        if self.heater:
            self.heater.stop()

        # 분취기 홈 복귀
        if self.simpy_collector and self.simpy_collector.is_connected:
            yield self.simpy_collector.home()
            if self.tab_collection:
                self.tab_collection.update_position_display()

        if self.csv_file:
            self.csv_file.close()

        self._emit_status("All experiments complete" if not self.abort_flag else "Sequence aborted")
        self._emit_log("Sequence end")

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def run_sequence(self, sequence_plan: List[dict], map_mgr=None):
        """
        시퀀스 실행 (기존 StrictSequenceEngine과 동일한 인터페이스)

        Args:
            sequence_plan: 기존 형식의 실험 계획 리스트
            map_mgr: SystemMapManager (호환성용, 사용 안함)
        """
        self.abort_flag = False

        # sequence_plan을 FlowStep으로 변환
        steps = []
        for idx, exp in enumerate(sequence_plan):
            step = FlowStep(
                step_id=idx + 1,
                temp_target=exp['temp'],
                volume_ml=exp['vol_ml'],
                flows=exp['flows'],
                inlet_ports=exp.get('inlet_ports', {}),
                collect_per_tube=exp.get('collect_volume_per_tube', 1.5)
            )
            steps.append(step)

        # SimPy 환경 생성
        self._create_simpy_env()
        self._init_simpy_wrappers()

        # 시퀀스 프로세스 시작
        self.env.process(self._sequence_process(steps))

        # 실행
        try:
            self.env.run()
        except Exception as e:
            self._emit_log(f"Sequence error: {e}")
            if self.signals:
                self.signals.sig_error.emit(str(e))

    def run_sequence_threaded(self, sequence_plan: List[dict], map_mgr=None):
        """
        별도 스레드에서 시퀀스 실행 (PyQt5 GUI와 함께 사용)

        @codesyncer-risk: Qt 이벤트 루프와 SimPy 실시간 모드 충돌 방지
        """
        thread = threading.Thread(
            target=self.run_sequence,
            args=(sequence_plan, map_mgr),
            daemon=True
        )
        thread.start()
        return thread

    def abort(self):
        """시퀀스 중단"""
        self.abort_flag = True
        self._emit_log("사용자 중단 요청")

        # 모든 펌프 정지
        for p_wrapper in self.simpy_pumps.values():
            p_wrapper.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 테스트/시뮬레이션 유틸리티
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation_test():
    """
    Mock 드라이버로 시뮬레이션 테스트

    Usage:
        python -m engine.simpy_engine
    """
    if not HAS_SIMPY:
        print("SimPy가 설치되지 않았습니다.")
        return

    print("=" * 60)
    print("SimPy Flow Chemistry 시뮬레이션 테스트")
    print("=" * 60)

    # Mock 설정
    class MockConfig:
        ACTIVE_PUMPS = ["PumpA", "PumpB"]
        PUMP_VALVE_MAP = {"PumpA": "ValveA", "PumpB": "ValveB"}
        # 데드볼륨: 튜빙 100cm × ID 1mm = π × (0.05)² × 100 ≈ 0.785 mL
        dead_vol_lines = {"PumpA": 0.785, "PumpB": 0.785}
        mixing_line_dead_vol = 0.265  # 합류 구간 dead volume
        reactor_vol = 5.0

    class MockPump:
        def set_rate(self, rate): pass
        def start(self): pass
        def stop(self): pass
        def refill(self, port): pass
        def prime_reactor(self): pass
        def wash_cycle(self, solvent_port=1, waste_port=12): pass
        # @codesyncer-decision: per-pump 설정 — ChemyxSmartPump와 동일 속성명
        capacity = 10.0
        syringe_volume = 10.0
        refill_rate = 20.0
        prime_rate = 10.0
        wash_speed = 15.0
        wash_count = 2
        wash_volume = 5.0

    class MockValve:
        def set_position(self, pos): pass

    class MockHeater:
        def set_temperature(self, t): pass
        def get_temperature(self): return None
        def stop(self): pass

    class MockSafety:
        def check_system(self): pass

    # 엔진 생성 (시뮬레이션 모드)
    engine = SimPyFlowEngine(
        config=MockConfig(),
        pumps={"PumpA": MockPump(), "PumpB": MockPump()},
        valves={"ValveA": MockValve(), "ValveB": MockValve(), "Outlet": MockValve()},
        heater=MockHeater(),
        safety_mgr=MockSafety(),
        realtime=False  # 시뮬레이션 모드
    )

    # Zone 파라미터 설정 (MockConfig에 config_data가 없으므로 수동 설정)
    engine.vol_reactor = 5.0
    engine.vol_post_common = 2.0
    engine.vol_collection = 1.0

    # 테스트 시퀀스
    test_sequence = [
        {
            'temp': 80.0,
            'vol_ml': 3.0,
            'flows': {'PumpA': 0.5, 'PumpB': 0.3},
            'inlet_ports': {'PumpA': 2, 'PumpB': 2},
            'collect_volume_per_tube': 1.0
        },
        {
            'temp': 100.0,
            'vol_ml': 2.0,
            'flows': {'PumpA': 0.8, 'PumpB': 0.2},
            'inlet_ports': {'PumpA': 3, 'PumpB': 3},
            'collect_volume_per_tube': 1.0
        }
    ]

    # 실행
    engine.run_sequence(test_sequence)

    print("=" * 60)
    print(f"Simulation complete. Total time: {engine.env.now:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    run_simulation_test()

# --- END OF FILE engine/simpy_engine.py ---
