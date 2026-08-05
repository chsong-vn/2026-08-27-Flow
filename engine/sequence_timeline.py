"""
Comprehensive Sequence Timeline Simulation

@codesyncer-decision: 실험 파라미터(농도, 몰비, RT) 기반 전체 시퀀스 타이밍 시뮬레이션
- 펌프: Infuse/Withdraw 상태, 유속
- 밸브: 12-way/3-way 전환 타이밍
- Outlet 밸브: 전환 타이밍
- 분취기: 튜브 이동 타이밍
"""

import simpy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 상태 및 데이터 정의
# ═══════════════════════════════════════════════════════════════════════════════

class PumpState(Enum):
    IDLE = "IDLE"
    INFUSING = "INFUSING"
    WITHDRAWING = "WITHDRAWING"


class ValveState(Enum):
    WASTE = "WASTE"
    REACTOR = "REACTOR"


@dataclass
class ReagentConfig:
    """시약 설정"""
    name: str
    port: int           # 12-way 밸브 포트 번호
    concentration: float  # M (mol/L)
    equivalents: float   # 몰비


@dataclass
class InletConfig:
    """Inlet 펌프 그룹 설정"""
    name: str
    reagent: ReagentConfig
    syringe_volume: float = 10.0  # mL
    refill_rate: float = 20.0     # mL/min (Withdraw 속도 - syringe_refill_rate)
    prime_rate: float = 10.0      # mL/min (Prime 속도 - priming_rate_ml_min)
    wash_speed: float = 15.0      # mL/min (세척 속도 - per-pump)
    wash_count: int = 2           # 세척 반복 횟수 - per-pump
    wash_volume: float = 5.0      # 세척 부피 (mL) - per-pump


@dataclass
class SystemConfig:
    """시스템 설정"""
    # Zone 부피 (mL)
    reactor_volume: float = 7.854    # 반응기 부피
    post_reactor_volume: float = 2.0  # 반응기 후 공통 구간
    collection_line_volume: float = 1.0  # 수거 라인

    # 밸브 전환 시간 (초)
    switch_time_12way_per_port: float = 0.3
    switch_time_12way_max: float = 1.5
    switch_time_3way: float = 0.5
    switch_time_outlet: float = 0.5

    # 분취기 설정
    collector_move_time_per_tube: float = 1.0  # 초/튜브
    collector_home_time: float = 3.0

    # 펌프 설정
    pump_start_delay: float = 0.2  # 펌프 시작 지연
    pump_stop_delay: float = 0.1   # 펌프 정지 지연

    # 온도 허용 오차
    temp_tolerance: float = 0.3  # °C
    # 합류 라인 dead volume (mL)
    mixing_line_dead_vol: float = 0.265


@dataclass
class ExperimentConfig:
    """실험 설정"""
    temperature: float          # 반응 온도 (°C)
    residence_time: float       # 체류 시간 (min)
    reaction_volume: float      # 반응량 (mL)
    volume_per_tube: float      # 튜브당 부피 (mL)
    start_tube: int = 1         # 시작 튜브 번호


@dataclass
class TimelineEvent:
    """타임라인 이벤트"""
    time: float
    component: str      # 컴포넌트 이름
    event_type: str     # 이벤트 타입
    details: str        # 상세 정보
    duration: float = 0.0


# 전역 이벤트 로그
timeline_events: List[TimelineEvent] = []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 유속 계산기
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_pump_flows(
    inlets: List[InletConfig],
    residence_time: float,
    reactor_volume: float
) -> Dict[str, float]:
    """
    농도와 몰비 기반 펌프별 유속 계산

    @codesyncer-decision: FlowCalculator 로직 통합
    - 유속 비율 = 몰비 / 농도
    - 총 유속 = 반응기 부피 / 체류 시간
    - 각 펌프 유속 = 총 유속 × (개별 비율 / 총 비율)

    Args:
        inlets: Inlet 설정 리스트
        residence_time: 체류 시간 (min)
        reactor_volume: 반응기 부피 (mL)

    Returns:
        Dict[펌프명, 유속(mL/min)]
    """
    if residence_time <= 0:
        raise ValueError("체류 시간은 0보다 커야 합니다.")

    # 각 펌프의 유속 비율 계산
    ratios = {}
    for inlet in inlets:
        conc = inlet.reagent.concentration
        eq = inlet.reagent.equivalents

        if conc <= 0:
            # 농도 0 = 세척용매: stoichiometry에 기여 안함
            ratios[inlet.name] = 0.0
        else:
            ratios[inlet.name] = eq / conc

    total_ratio = sum(ratios.values())
    if total_ratio <= 0:
        raise ValueError("유효한 시약이 없습니다 (모든 농도가 0).")

    # 목표 총 유속
    target_total_flow = reactor_volume / residence_time

    # 각 펌프의 실제 유속 계산
    flows = {}
    for inlet in inlets:
        if total_ratio > 0:
            flows[inlet.name] = target_total_flow * (ratios[inlet.name] / total_ratio)
        else:
            flows[inlet.name] = 0.0

    return flows


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SimPy 컴포넌트
# ═══════════════════════════════════════════════════════════════════════════════

class SimPump:
    """시린지 펌프 시뮬레이션"""

    VALVE_SWITCH_SETTLE = 1.0  # 밸브 전환 후 안정화 시간 (초)

    def __init__(self, env: simpy.Environment, name: str, config: SystemConfig,
                 syringe_volume: float, refill_rate: float,
                 prime_rate: float = 10.0, wash_speed: float = 15.0,
                 wash_count: int = 2, wash_volume: float = 5.0):
        self.env = env
        self.name = name
        self.config = config
        self.syringe_volume = syringe_volume
        self.refill_rate = refill_rate
        self.prime_rate = prime_rate
        self.wash_speed = wash_speed
        self.wash_count = wash_count
        self.wash_volume = wash_volume
        self.current_volume = syringe_volume
        self.state = PumpState.IDLE
        self.current_rate = 0.0
        self.resource = simpy.Resource(env, capacity=1)

    def log_event(self, event_type: str, details: str, duration: float = 0.0):
        timeline_events.append(TimelineEvent(
            time=self.env.now,
            component=self.name,
            event_type=event_type,
            details=details,
            duration=duration
        ))

    def infuse(self, volume: float, rate: float, source_port: int = 1):
        """Infuse 프로세스"""
        return self.env.process(self._infuse_process(volume, rate, source_port))

    def _infuse_process(self, volume: float, rate: float, source_port: int):
        with self.resource.request() as req:
            yield req

            remaining = volume
            while remaining > 0.01:
                # 리필 필요 체크
                if self.current_volume <= 0.1:
                    yield self.env.process(self._refill_process(source_port))

                # 주입량 계산
                inject_amount = min(remaining, self.current_volume)
                inject_time = (inject_amount / rate) * 60  # 초

                # 상태 변경: INFUSING
                self.state = PumpState.INFUSING
                self.current_rate = rate

                self.log_event(
                    "INFUSE_START",
                    f"Volume: {inject_amount:.3f}mL, Rate: {rate:.3f}mL/min, Duration: {inject_time:.1f}s",
                    inject_time
                )

                # 시작 지연 + 주입 시간 + 정지 지연
                main_time = max(0, inject_time - self.config.pump_start_delay - self.config.pump_stop_delay)
                yield self.env.timeout(self.config.pump_start_delay)
                yield self.env.timeout(main_time)
                yield self.env.timeout(self.config.pump_stop_delay)

                # 상태 업데이트
                self.current_volume -= inject_amount
                remaining -= inject_amount

                self.log_event("INFUSE_END", f"Remaining syringe: {self.current_volume:.2f}mL")

            self.state = PumpState.IDLE
            self.current_rate = 0.0

    def _refill_process(self, source_port: int):
        """리필 (Withdraw) 프로세스"""
        self.state = PumpState.WITHDRAWING

        # 밸브 전환 시간
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)

        refill_time = (self.syringe_volume / self.refill_rate) * 60

        self.log_event(
            "WITHDRAW_START",
            f"Volume: {self.syringe_volume:.1f}mL, Rate: {self.refill_rate:.1f}mL/min, Port: {source_port}",
            refill_time
        )

        yield self.env.timeout(refill_time)

        self.current_volume = self.syringe_volume
        self.state = PumpState.IDLE

        self.log_event("WITHDRAW_END", f"Syringe refilled to {self.syringe_volume:.1f}mL")

    def wash_cycle(self, solvent_port: int = 1, waste_port: int = 12):
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
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)
        self.state = PumpState.INFUSING
        self.log_event(
            "WASH_INFUSE_START",
            f"INFUSE {actual_vol:.1f}mL → waste(port {waste_port}) @ {self.wash_speed:.1f}mL/min",
            duration_calc
        )
        yield self.env.timeout(duration_calc)
        self.current_volume = max(0, self.current_volume - actual_vol)
        self.log_event("WASH_INFUSE_END", f"Syringe: {self.current_volume:.1f}mL")

        # Phase 2: WITHDRAW from solvent — 12-way→솔벤트, 3-way→SOURCE
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)
        self.state = PumpState.WITHDRAWING
        self.log_event(
            "WASH_WITHDRAW_START",
            f"WITHDRAW {actual_vol:.1f}mL ← solvent(port {solvent_port}) @ {self.wash_speed:.1f}mL/min",
            duration_calc
        )
        yield self.env.timeout(duration_calc)
        self.current_volume = min(self.current_volume + actual_vol, self.syringe_volume)
        self.state = PumpState.IDLE
        self.log_event("WASH_WITHDRAW_END", f"Syringe: {self.current_volume:.1f}mL")

    def prime_reactor(self):
        """반응기 프라이밍 프로세스
        @codesyncer-decision: pump_chemyx_smart.py prime_reactor()과 동일 로직
        - volume = wash_volume, rate = prime_rate
        """
        return self.env.process(self._prime_reactor_process())

    def _prime_reactor_process(self):
        """내부 반응기 프라이밍 프로세스"""
        prime_vol = self.wash_volume
        prime_time = (prime_vol / self.prime_rate) * 60.0

        # 3-way → REACTOR 전환
        yield self.env.timeout(self.VALVE_SWITCH_SETTLE)

        self.state = PumpState.INFUSING
        self.log_event(
            "PRIME_START",
            f"INFUSE {prime_vol:.1f}mL → reactor @ {self.prime_rate:.1f}mL/min",
            prime_time
        )

        yield self.env.timeout(prime_time)
        self.current_volume = max(0, self.current_volume - prime_vol)
        self.state = PumpState.IDLE
        self.log_event("PRIME_END", f"Remaining: {self.current_volume:.1f}mL")


class Sim12WayValve:
    """12-way 셀렉터 밸브"""

    def __init__(self, env: simpy.Environment, name: str, config: SystemConfig):
        self.env = env
        self.name = name
        self.config = config
        self.current_port = 1

    def log_event(self, event_type: str, details: str, duration: float = 0.0):
        timeline_events.append(TimelineEvent(
            time=self.env.now,
            component=self.name,
            event_type=event_type,
            details=details,
            duration=duration
        ))

    def set_port(self, target_port: int):
        return self.env.process(self._switch(target_port))

    def _switch(self, target_port: int):
        if self.current_port == target_port:
            return

        distance = abs(target_port - self.current_port)
        switch_time = min(
            self.config.switch_time_12way_max,
            distance * self.config.switch_time_12way_per_port
        )

        self.log_event(
            "SWITCH",
            f"Port {self.current_port} → Port {target_port} (distance: {distance})",
            switch_time
        )

        yield self.env.timeout(switch_time)
        self.current_port = target_port


class Sim3WayValve:
    """3-way 스위처 밸브"""

    def __init__(self, env: simpy.Environment, name: str, config: SystemConfig):
        self.env = env
        self.name = name
        self.config = config
        self.state = ValveState.WASTE

    def log_event(self, event_type: str, details: str, duration: float = 0.0):
        timeline_events.append(TimelineEvent(
            time=self.env.now,
            component=self.name,
            event_type=event_type,
            details=details,
            duration=duration
        ))

    def set_position(self, state: ValveState):
        return self.env.process(self._switch(state))

    def _switch(self, state: ValveState):
        if self.state == state:
            return

        self.log_event(
            "SWITCH",
            f"{self.state.value} → {state.value}",
            self.config.switch_time_3way
        )

        yield self.env.timeout(self.config.switch_time_3way)
        self.state = state


class SimOutletValve:
    """Outlet 밸브"""

    def __init__(self, env: simpy.Environment, config: SystemConfig):
        self.env = env
        self.name = "Outlet"
        self.config = config
        self.state = ValveState.WASTE

    def log_event(self, event_type: str, details: str, duration: float = 0.0):
        timeline_events.append(TimelineEvent(
            time=self.env.now,
            component=self.name,
            event_type=event_type,
            details=details,
            duration=duration
        ))

    def set_position(self, state: ValveState):
        return self.env.process(self._switch(state))

    def _switch(self, state: ValveState):
        if self.state == state:
            return

        self.log_event(
            "SWITCH",
            f"{self.state.value} → {state.value}",
            self.config.switch_time_outlet
        )

        yield self.env.timeout(self.config.switch_time_outlet)
        self.state = state


class SimCollector:
    """분취기"""

    def __init__(self, env: simpy.Environment, config: SystemConfig, total_tubes: int = 88):
        self.env = env
        self.name = "Collector"
        self.config = config
        self.total_tubes = total_tubes
        self.current_tube = 0  # 0 = home

    def log_event(self, event_type: str, details: str, duration: float = 0.0):
        timeline_events.append(TimelineEvent(
            time=self.env.now,
            component=self.name,
            event_type=event_type,
            details=details,
            duration=duration
        ))

    def home(self):
        return self.env.process(self._home())

    def _home(self):
        self.log_event("HOME_START", f"From tube {self.current_tube}", self.config.collector_home_time)
        yield self.env.timeout(self.config.collector_home_time)
        self.current_tube = 0
        self.log_event("HOME_END", "At home position")

    def move_to_tube(self, tube_num: int):
        return self.env.process(self._move(tube_num))

    def _move(self, tube_num: int):
        if self.current_tube == tube_num:
            return

        distance = abs(tube_num - self.current_tube)
        move_time = distance * self.config.collector_move_time_per_tube

        self.log_event(
            "MOVE",
            f"Tube {self.current_tube} → Tube {tube_num} (distance: {distance})",
            move_time
        )

        yield self.env.timeout(move_time)
        self.current_tube = tube_num


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 시퀀스 시뮬레이션
# ═══════════════════════════════════════════════════════════════════════════════

def run_sequence_simulation(
    inlets: List[InletConfig],
    experiment: ExperimentConfig,
    system: SystemConfig = None
) -> Tuple[List[TimelineEvent], float, Dict[str, float]]:
    """
    전체 시퀀스 시뮬레이션 실행

    Args:
        inlets: Inlet 설정 리스트
        experiment: 실험 설정
        system: 시스템 설정 (None이면 기본값)

    Returns:
        (이벤트 리스트, 총 시간, 펌프별 유속)
    """
    global timeline_events
    timeline_events = []

    if system is None:
        system = SystemConfig()

    env = simpy.Environment()

    # 유속 계산
    flows = calculate_pump_flows(inlets, experiment.residence_time, system.reactor_volume)
    total_flow = sum(flows.values())

    # Zone 부피
    zone_vol = system.reactor_volume + system.post_reactor_volume

    # 컴포넌트 생성
    pumps = {}
    valves_12way = {}
    valves_3way = {}

    for inlet in inlets:
        pumps[inlet.name] = SimPump(
            env, f"{inlet.name}_Pump", system,
            inlet.syringe_volume, inlet.refill_rate,
            prime_rate=inlet.prime_rate, wash_speed=inlet.wash_speed,
            wash_count=inlet.wash_count, wash_volume=inlet.wash_volume
        )
        valves_12way[inlet.name] = Sim12WayValve(env, f"{inlet.name}_12way", system)
        valves_3way[inlet.name] = Sim3WayValve(env, f"{inlet.name}_3way", system)

    outlet = SimOutletValve(env, system)
    collector = SimCollector(env, system)

    def log_phase(phase_name: str, details: str = ""):
        timeline_events.append(TimelineEvent(
            time=env.now,
            component="SYSTEM",
            event_type="PHASE",
            details=f"[{phase_name}] {details}"
        ))

    def sequence_process():
        """메인 시퀀스 프로세스"""

        # ══════════════════════════════════════════════════════════════════════
        # Phase 0: 초기화
        # ══════════════════════════════════════════════════════════════════════
        log_phase("INITIALIZATION", "All valves to WASTE, Port 1")

        init_procs = []
        for inlet in inlets:
            init_procs.append(valves_12way[inlet.name].set_port(1))
            init_procs.append(valves_3way[inlet.name].set_position(ValveState.WASTE))
        init_procs.append(outlet.set_position(ValveState.WASTE))
        yield simpy.AllOf(env, init_procs)

        # 분취기 홈 + 시작 튜브 이동
        yield collector.home()
        yield collector.move_to_tube(experiment.start_tube)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 1: 시린지 초기 리필 (WITHDRAW from solvent)
        # @codesyncer-decision: strict_engine Step 1.5 — 온도 도달 후 솔벤트 초기 리필
        # ══════════════════════════════════════════════════════════════════════
        log_phase("INITIAL_REFILL", "Syringe pumps WITHDRAW from solvent port")

        refill_procs = []
        for inlet in inlets:
            pump = pumps[inlet.name]
            if pump.current_volume < pump.syringe_volume * 0.9:
                refill_procs.append(env.process(pump._refill_process(1)))  # Port 1 = Solvent

        if refill_procs:
            yield simpy.AllOf(env, refill_procs)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 2: 시스템 세척 (Per-Pump INFUSE→WITHDRAW × wash_count)
        # @codesyncer-decision: strict_engine _execute_system_wash()와 동일 로직
        # - 기존 INFUSE-only priming 제거
        # - Stabilization 10초 제거 (strict_engine에서도 제거됨)
        # ══════════════════════════════════════════════════════════════════════
        max_wash_count = max(inlet.wash_count for inlet in inlets)
        log_phase("SYSTEM_WASH", f"Per-pump wash cycles: {max_wash_count} max")

        for cycle in range(max_wash_count):
            log_phase("WASH_CYCLE", f"Cycle {cycle + 1}/{max_wash_count}")

            wash_procs = []
            for inlet in inlets:
                if cycle < inlet.wash_count:
                    wash_procs.append(pumps[inlet.name].wash_cycle(solvent_port=1, waste_port=12))

            if wash_procs:
                yield simpy.AllOf(env, wash_procs)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 3: 시약 프리필 (반응기 프라이밍 → 시약 리필 → 합류 라인 플러시)
        # @codesyncer-decision: strict_engine _smart_prefill_logic()와 동일 3-phase
        # ══════════════════════════════════════════════════════════════════════

        # Phase 3-0: 반응기 프라이밍 (INFUSE → REACTOR)
        # @codesyncer-decision: 세척 후 시린지 솔벤트를 반응기로 밀어냄
        log_phase("PREFILL_PRIME", "Prime reactor: INFUSE syringe → reactor")

        # 3-way: REACTOR로 전환
        reactor_procs = []
        for inlet in inlets:
            reactor_procs.append(valves_3way[inlet.name].set_position(ValveState.REACTOR))
        yield simpy.AllOf(env, reactor_procs)

        prime_procs = []
        for inlet in inlets:
            pump = pumps[inlet.name]
            if pump.current_volume > 0.1:
                prime_procs.append(pump.prime_reactor())
        if prime_procs:
            yield simpy.AllOf(env, prime_procs)

        # Phase 3-1: 시약 포트에서 리필 (WITHDRAW)
        log_phase("PREFILL_REAGENT", "Reagent refill: WITHDRAW from reagent ports")

        # 12-way: 시약 포트로 전환
        port_procs = []
        for inlet in inlets:
            port_procs.append(valves_12way[inlet.name].set_port(inlet.reagent.port))
        yield simpy.AllOf(env, port_procs)

        # 3-way: SOURCE (WASTE) 방향 (리필용)
        source_procs = []
        for inlet in inlets:
            source_procs.append(valves_3way[inlet.name].set_position(ValveState.WASTE))
        yield simpy.AllOf(env, source_procs)

        reagent_refill_procs = []
        for inlet in inlets:
            reagent_refill_procs.append(env.process(pumps[inlet.name]._refill_process(inlet.reagent.port)))
        if reagent_refill_procs:
            yield simpy.AllOf(env, reagent_refill_procs)

        # Phase 3-2: 합류 라인 플러시 (INFUSE)
        if system.mixing_line_dead_vol > 0 and len(inlets) > 0:
            vol_per_pump = system.mixing_line_dead_vol / len(inlets)
            log_phase("PREFILL_FLUSH", f"Mixing line flush: {system.mixing_line_dead_vol:.2f}mL ({vol_per_pump:.2f}mL/pump)")

            # 3-way: REACTOR로 전환
            reactor_procs2 = []
            for inlet in inlets:
                reactor_procs2.append(valves_3way[inlet.name].set_position(ValveState.REACTOR))
            yield simpy.AllOf(env, reactor_procs2)

            flush_procs = []
            for inlet in inlets:
                flow = flows[inlet.name]
                if flow > 0:
                    # @codesyncer-decision: 프리필 속도 = refill_rate (고속)
                    flush_procs.append(pumps[inlet.name].infuse(vol_per_pump, inlet.refill_rate, inlet.reagent.port))
            if flush_procs:
                yield simpy.AllOf(env, flush_procs)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 5: 시약 주입 (Injection)
        # ══════════════════════════════════════════════════════════════════════
        injection_time = (experiment.reaction_volume / total_flow) * 60
        log_phase("INJECTION", f"Reagent injection: {experiment.reaction_volume}mL @ {total_flow:.3f}mL/min ({injection_time:.1f}s)")

        inject_procs = []
        for inlet in inlets:
            flow = flows[inlet.name]
            if flow > 0:
                pump_vol = experiment.reaction_volume * (flow / total_flow)
                inject_procs.append(pumps[inlet.name].infuse(pump_vol, flow, inlet.reagent.port))

        if inject_procs:
            yield simpy.AllOf(env, inject_procs)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 6: 이송 (Transit)
        # ══════════════════════════════════════════════════════════════════════
        # 3-way: WASTE로 전환 (세척용매로 밀어내기)
        waste_procs = []
        for inlet in inlets:
            waste_procs.append(valves_3way[inlet.name].set_position(ValveState.WASTE))
        yield simpy.AllOf(env, waste_procs)

        # 12-way: 세척 포트로 복귀
        solvent_procs = []
        for inlet in inlets:
            solvent_procs.append(valves_12way[inlet.name].set_port(1))
        yield simpy.AllOf(env, solvent_procs)

        # @codesyncer-decision: transit_vol = zone_vol - reaction_vol (strict_engine과 동일)
        # 기존 zone_vol * 0.95 → 반응물이 reactor+post_reactor를 점유한 나머지만 밀어냄
        transit_vol = max(0, zone_vol - experiment.reaction_volume)
        transit_time = (transit_vol / total_flow) * 60
        log_phase("TRANSIT", f"Push with solvent: {transit_vol:.2f}mL ({transit_time:.1f}s)")

        transit_procs = []
        for inlet in inlets:
            flow = flows[inlet.name]
            if flow > 0:
                pump_vol = transit_vol * (flow / total_flow)
                transit_procs.append(pumps[inlet.name].infuse(pump_vol, flow, source_port=1))

        if transit_procs:
            yield simpy.AllOf(env, transit_procs)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 7: 분취 (Collection)
        # ══════════════════════════════════════════════════════════════════════
        num_tubes = math.ceil(experiment.reaction_volume / experiment.volume_per_tube)
        log_phase("COLLECTION", f"Collecting to {num_tubes} tubes (starting Tube {experiment.start_tube})")

        # Outlet: REACTOR(수거)로 전환
        yield outlet.set_position(ValveState.REACTOR)

        # @codesyncer-decision: transit이 정확한 front 도달이므로 여유분 불필요 (strict_engine과 동일)

        current_tube = experiment.start_tube
        for tube_idx in range(num_tubes):
            # 수거량 계산
            if tube_idx == num_tubes - 1:
                remaining = experiment.reaction_volume - (tube_idx * experiment.volume_per_tube)
                tube_vol = min(remaining, experiment.volume_per_tube)
            else:
                tube_vol = experiment.volume_per_tube

            tube_time = (tube_vol / total_flow) * 60

            timeline_events.append(TimelineEvent(
                time=env.now, component="SYSTEM", event_type="COLLECT_TUBE",
                details=f"Tube {current_tube}: {tube_vol:.2f}mL ({tube_time:.1f}s)",
                duration=tube_time
            ))

            # 분취기 이동 (첫 튜브 제외)
            if tube_idx > 0:
                yield collector.move_to_tube(current_tube)

            # 수거
            collect_procs = []
            for inlet in inlets:
                flow = flows[inlet.name]
                if flow > 0:
                    pump_vol = tube_vol * (flow / total_flow)
                    collect_procs.append(pumps[inlet.name].infuse(pump_vol, flow, source_port=1))

            if collect_procs:
                yield simpy.AllOf(env, collect_procs)

            current_tube += 1

        # ══════════════════════════════════════════════════════════════════════
        # Phase 8: 수거 라인 세척
        # ══════════════════════════════════════════════════════════════════════
        wash_tube_vol = system.collection_line_volume * 1.0
        wash_tube_time = (wash_tube_vol / total_flow) * 60
        log_phase("LINE_WASH", f"Collection line wash to Tube {current_tube}: {wash_tube_vol:.2f}mL")

        yield collector.move_to_tube(current_tube)

        wash_procs = []
        for inlet in inlets:
            flow = flows[inlet.name]
            if flow > 0:
                pump_vol = wash_tube_vol * (flow / total_flow)
                wash_procs.append(pumps[inlet.name].infuse(pump_vol, flow, source_port=1))

        if wash_procs:
            yield simpy.AllOf(env, wash_procs)

        # Outlet: WASTE로 복귀
        yield outlet.set_position(ValveState.WASTE)

        # ══════════════════════════════════════════════════════════════════════
        # Phase 9: 분취기 홈 복귀
        # ══════════════════════════════════════════════════════════════════════
        log_phase("FINALIZATION", "Collector returning home")
        yield collector.home()

        log_phase("COMPLETE", f"Total time: {env.now:.1f}s")

    # 시뮬레이션 실행
    env.process(sequence_process())
    env.run()

    return timeline_events, env.now, flows


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 출력 함수
# ═══════════════════════════════════════════════════════════════════════════════

def print_experiment_parameters(
    inlets: List[InletConfig],
    experiment: ExperimentConfig,
    system: SystemConfig,
    flows: Dict[str, float]
):
    """실험 파라미터 요약 출력"""
    total_flow = sum(flows.values())

    print("\n" + "=" * 80)
    print("EXPERIMENT PARAMETERS")
    print("=" * 80)

    print(f"\n[Reaction Conditions]")
    print(f"  Temperature:     {experiment.temperature}°C")
    print(f"  Residence Time:  {experiment.residence_time} min")
    print(f"  Reaction Volume: {experiment.reaction_volume} mL")
    print(f"  Volume/Tube:     {experiment.volume_per_tube} mL")
    print(f"  Start Tube:      #{experiment.start_tube}")

    print(f"\n[System Volumes]")
    print(f"  Reactor Volume:      {system.reactor_volume:.3f} mL")
    print(f"  Post-Reactor Volume: {system.post_reactor_volume:.1f} mL")
    print(f"  Collection Line:     {system.collection_line_volume:.1f} mL")

    print(f"\n[Inlet Configurations]")
    print(f"  {'Inlet':<12} {'Reagent':<15} {'Port':<6} {'Conc (M)':<10} {'Eq':<8} {'Flow (mL/min)':<15} {'Syringe (mL)':<12}")
    print("  " + "-" * 78)
    for inlet in inlets:
        flow = flows.get(inlet.name, 0)
        print(f"  {inlet.name:<12} {inlet.reagent.name:<15} {inlet.reagent.port:<6} "
              f"{inlet.reagent.concentration:<10.3f} {inlet.reagent.equivalents:<8.2f} "
              f"{flow:<15.4f} {inlet.syringe_volume:<12.1f}")

    print(f"\n  Total Flow Rate: {total_flow:.4f} mL/min")
    print(f"  Injection Time:  {(experiment.reaction_volume / total_flow) * 60:.1f} s")

    num_tubes = math.ceil(experiment.reaction_volume / experiment.volume_per_tube)
    print(f"  Expected Tubes:  {num_tubes} (Tube {experiment.start_tube}~{experiment.start_tube + num_tubes})")


def print_timeline(events: List[TimelineEvent], total_time: float):
    """타임라인 출력"""

    print("\n" + "=" * 100)
    print("SEQUENCE TIMELINE")
    print("=" * 100)
    print(f"Total Sequence Time: {total_time:.1f}s ({total_time/60:.2f} min)")
    print("-" * 100)

    print(f"\n{'Time (s)':<12} {'Component':<20} {'Event':<18} {'Details':<45} {'Duration':<10}")
    print("-" * 100)

    for e in sorted(events, key=lambda x: x.time):
        duration_str = f"{e.duration:.1f}s" if e.duration > 0 else ""
        print(f"{e.time:<12.2f} {e.component:<20} {e.event_type:<18} {e.details:<45} {duration_str:<10}")


def print_pump_timeline(events: List[TimelineEvent]):
    """펌프 상태 타임라인 출력"""

    print("\n" + "=" * 80)
    print("PUMP STATE TIMELINE")
    print("=" * 80)

    pump_events = [e for e in events if "_Pump" in e.component]

    pumps = sorted(set(e.component for e in pump_events))
    for pump in pumps:
        print(f"\n[{pump}]")
        pump_specific = [e for e in pump_events if e.component == pump]
        for e in sorted(pump_specific, key=lambda x: x.time):
            duration_str = f"({e.duration:.1f}s)" if e.duration > 0 else ""
            print(f"  {e.time:8.2f}s  {e.event_type:<16} {e.details} {duration_str}")


def print_valve_timeline(events: List[TimelineEvent]):
    """밸브 전환 타임라인 출력"""

    print("\n" + "=" * 80)
    print("VALVE SWITCHING TIMELINE")
    print("=" * 80)

    valve_events = [e for e in events if ("12way" in e.component or "3way" in e.component or e.component == "Outlet")]

    valves = sorted(set(e.component for e in valve_events))
    for valve in valves:
        print(f"\n[{valve}]")
        valve_specific = [e for e in valve_events if e.component == valve]
        for e in sorted(valve_specific, key=lambda x: x.time):
            print(f"  {e.time:8.2f}s  {e.details:<35} ({e.duration:.2f}s)")


def print_collector_timeline(events: List[TimelineEvent]):
    """분취기 타임라인 출력"""

    print("\n" + "=" * 80)
    print("COLLECTOR TIMELINE")
    print("=" * 80)

    collector_events = [e for e in events if e.component == "Collector"]
    for e in sorted(collector_events, key=lambda x: x.time):
        duration_str = f"({e.duration:.1f}s)" if e.duration > 0 else ""
        print(f"  {e.time:8.2f}s  {e.event_type:<12} {e.details} {duration_str}")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 메인 실행
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 예제 설정
    inlets = [
        InletConfig(
            name="Inlet A",
            reagent=ReagentConfig(name="Reagent A", port=3, concentration=1.0, equivalents=1.0),
            syringe_volume=10.0,
            refill_rate=20.0, prime_rate=10.0,
            wash_speed=15.0, wash_count=2, wash_volume=5.0
        ),
        InletConfig(
            name="Inlet B",
            reagent=ReagentConfig(name="Reagent B", port=5, concentration=0.5, equivalents=1.2),
            syringe_volume=10.0,
            refill_rate=20.0, prime_rate=10.0,
            wash_speed=15.0, wash_count=2, wash_volume=5.0
        ),
        InletConfig(
            name="Inlet C",
            reagent=ReagentConfig(name="Catalyst", port=7, concentration=0.1, equivalents=0.05),
            syringe_volume=5.0,
            refill_rate=20.0, prime_rate=10.0,
            wash_speed=15.0, wash_count=1, wash_volume=3.0
        ),
    ]

    experiment = ExperimentConfig(
        temperature=80.0,
        residence_time=10.0,  # min
        reaction_volume=5.0,  # mL
        volume_per_tube=1.5,  # mL
        start_tube=1
    )

    system = SystemConfig(
        reactor_volume=7.854,
        post_reactor_volume=2.0,
        collection_line_volume=1.0,
        mixing_line_dead_vol=0.265
    )

    # 시뮬레이션 실행
    events, total_time, flows = run_sequence_simulation(inlets, experiment, system)

    # 결과 출력
    print_experiment_parameters(inlets, experiment, system, flows)
    print_timeline(events, total_time)
    print_pump_timeline(events)
    print_valve_timeline(events)
    print_collector_timeline(events)
