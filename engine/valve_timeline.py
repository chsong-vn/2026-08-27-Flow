"""
Valve Switching Timeline Visualization

@codesyncer-decision: 각 Inlet (A/B/C)별 12-way/3-way 밸브 및 Outlet 밸브의
전환 타이밍을 타임라인 형태로 표시

밸브 전환 시간 기준:
- 12-way (Runze SV07): 포트 이동 거리에 비례 (~0.3초/포트, 최대 ~1.5초)
- 3-way (Arduino): 고정 ~0.5초
- Outlet 밸브: ~0.5초
"""

import simpy
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from enum import Enum


class ValveType(Enum):
    """밸브 타입"""
    SELECTOR_12WAY = "12-way"
    SWITCHER_3WAY = "3-way"
    OUTLET = "Outlet"


class ValveState(Enum):
    """밸브 상태"""
    WASTE = "WASTE"
    REACTOR = "REACTOR"
    PORT = "PORT"  # 12-way 포트 번호용


@dataclass
class ValveEvent:
    """밸브 이벤트 기록"""
    time: float
    inlet_name: str
    valve_type: ValveType
    from_state: str
    to_state: str
    duration: float


@dataclass
class TimelineConfig:
    """타임라인 설정"""
    # 밸브 전환 시간 (초)
    switch_time_12way_per_port: float = 0.3   # 12-way: 포트당 시간
    switch_time_12way_min: float = 0.3        # 12-way: 최소 시간
    switch_time_12way_max: float = 1.5        # 12-way: 최대 시간 (1→12)
    switch_time_3way: float = 0.5             # 3-way: 고정 시간
    switch_time_outlet: float = 0.5           # Outlet: 고정 시간

    # Zone 부피 (mL)
    vol_reactor: float = 5.0
    vol_post_common: float = 2.0
    vol_collection: float = 1.0


# 전역 이벤트 로그
timeline_events: List[ValveEvent] = []


class TrackedValve12Way:
    """12-way 밸브 (포트 기반 전환 시간)"""

    def __init__(self, env: simpy.Environment, inlet_name: str, config: TimelineConfig):
        self.env = env
        self.inlet_name = inlet_name
        self.config = config
        self.current_port = 1  # 기본: 세척용매 포트

    def set_port(self, target_port: int):
        """포트 변경"""
        return self.env.process(self._switch(target_port))

    def _switch(self, target_port: int):
        if self.current_port == target_port:
            return

        # 포트 이동 거리에 따른 전환 시간 계산
        distance = abs(target_port - self.current_port)
        switch_time = min(
            self.config.switch_time_12way_max,
            max(self.config.switch_time_12way_min, distance * self.config.switch_time_12way_per_port)
        )

        event = ValveEvent(
            time=self.env.now,
            inlet_name=self.inlet_name,
            valve_type=ValveType.SELECTOR_12WAY,
            from_state=f"Port {self.current_port}",
            to_state=f"Port {target_port}",
            duration=switch_time
        )
        timeline_events.append(event)

        yield self.env.timeout(switch_time)
        self.current_port = target_port


class TrackedValve3Way:
    """3-way 밸브 (WASTE/REACTOR 전환)"""

    def __init__(self, env: simpy.Environment, inlet_name: str, config: TimelineConfig):
        self.env = env
        self.inlet_name = inlet_name
        self.config = config
        self.state = ValveState.WASTE

    def set_position(self, state: ValveState):
        """위치 변경"""
        return self.env.process(self._switch(state))

    def _switch(self, state: ValveState):
        if self.state == state:
            return

        event = ValveEvent(
            time=self.env.now,
            inlet_name=self.inlet_name,
            valve_type=ValveType.SWITCHER_3WAY,
            from_state=self.state.value,
            to_state=state.value,
            duration=self.config.switch_time_3way
        )
        timeline_events.append(event)

        yield self.env.timeout(self.config.switch_time_3way)
        self.state = state


class TrackedOutletValve:
    """Outlet 밸브"""

    def __init__(self, env: simpy.Environment, config: TimelineConfig):
        self.env = env
        self.config = config
        self.state = ValveState.WASTE

    def set_position(self, state: ValveState):
        """위치 변경"""
        return self.env.process(self._switch(state))

    def _switch(self, state: ValveState):
        if self.state == state:
            return

        event = ValveEvent(
            time=self.env.now,
            inlet_name="Outlet",
            valve_type=ValveType.OUTLET,
            from_state=self.state.value,
            to_state=state.value,
            duration=self.config.switch_time_outlet
        )
        timeline_events.append(event)

        yield self.env.timeout(self.config.switch_time_outlet)
        self.state = state


def print_timeline_ascii(events: List[ValveEvent], total_time: float):
    """ASCII 아트로 타임라인 출력"""

    # 밸브별 이벤트 분류
    valve_groups = {}
    for e in events:
        key = f"{e.inlet_name}_{e.valve_type.value}"
        if key not in valve_groups:
            valve_groups[key] = []
        valve_groups[key].append(e)

    # 타임라인 폭 (문자 수)
    width = 80
    scale = width / total_time if total_time > 0 else 1

    print("\n" + "=" * 90)
    print("VALVE SWITCHING TIMELINE")
    print("=" * 90)
    print(f"Total sequence time: {total_time:.1f}s")
    print(f"Scale: 1 char = {total_time/width:.2f}s")
    print("-" * 90)

    # 시간 눈금
    print("Time (s): ", end="")
    for i in range(0, width + 1, 10):
        t = i / scale if scale > 0 else 0
        print(f"{t:<10.0f}", end="")
    print()
    print("          " + "|" + "-" * 9 + "|" * (width // 10))

    # 각 밸브 그룹별 타임라인
    ordered_keys = sorted(valve_groups.keys(), key=lambda x: (
        0 if "Inlet A" in x else 1 if "Inlet B" in x else 2 if "Inlet C" in x else 3,
        0 if "12-way" in x else 1
    ))

    for key in ordered_keys:
        events_for_valve = valve_groups[key]

        # 타임라인 문자열 생성
        line = [" "] * width

        for e in events_for_valve:
            start_pos = int(e.time * scale)
            end_pos = int((e.time + e.duration) * scale)

            if start_pos < width:
                line[start_pos] = "▼"

            for i in range(start_pos + 1, min(end_pos, width)):
                line[i] = "█"

        # 밸브 이름 (고정 폭)
        label = f"{key:25s}"
        print(f"{label}|{''.join(line)}|")

    print("-" * 90)

    # 상세 이벤트 목록
    print("\n[DETAILED EVENTS]")
    print(f"{'Time':>8s} {'Duration':>8s} {'Inlet':>10s} {'Valve':>10s} {'From':>12s} → {'To':<12s}")
    print("-" * 70)

    for e in sorted(events, key=lambda x: x.time):
        print(f"{e.time:8.2f}s {e.duration:8.2f}s {e.inlet_name:>10s} {e.valve_type.value:>10s} {e.from_state:>12s} → {e.to_state:<12s}")


def run_valve_timeline_simulation(
    inlet_names: List[str] = None,
    reagent_ports: Dict[str, int] = None,
    flows: Dict[str, float] = None,
    step_vol: float = 3.0,
    collect_per_tube: float = 1.0
):
    """
    밸브 전환 타임라인 시뮬레이션 실행

    Args:
        inlet_names: Inlet 이름 리스트 (기본: ["Inlet A", "Inlet B", "Inlet C"])
        reagent_ports: 각 Inlet의 시약 포트 번호 (기본: 2)
        flows: 각 Inlet의 유속 (mL/min)
        step_vol: 주입량 (mL)
        collect_per_tube: 튜브당 수거량 (mL)
    """
    global timeline_events
    timeline_events = []

    if inlet_names is None:
        inlet_names = ["Inlet A", "Inlet B", "Inlet C"]
    if reagent_ports is None:
        reagent_ports = {name: 2 for name in inlet_names}
    if flows is None:
        flows = {"Inlet A": 0.5, "Inlet B": 0.3, "Inlet C": 0.2}

    config = TimelineConfig()
    env = simpy.Environment()

    # 밸브 생성
    valves_12way = {name: TrackedValve12Way(env, name, config) for name in inlet_names}
    valves_3way = {name: TrackedValve3Way(env, name, config) for name in inlet_names}
    outlet_valve = TrackedOutletValve(env, config)

    total_flow = sum(flows.get(name, 0) for name in inlet_names)
    zone_vol = config.vol_reactor + config.vol_post_common

    def sequence():
        """시퀀스 프로세스"""

        # === Phase 0: 초기화 (모두 WASTE, Port 1) ===
        print("\n[Phase 0] Initialization")
        init_procs = []
        for name in inlet_names:
            init_procs.append(valves_12way[name].set_port(1))
            init_procs.append(valves_3way[name].set_position(ValveState.WASTE))
        init_procs.append(outlet_valve.set_position(ValveState.WASTE))
        yield simpy.AllOf(env, init_procs)

        # === Phase 1: Priming (세척) ===
        print(f"\n[Phase 1] Priming @ {env.now:.1f}s")
        wash_vol = zone_vol * 1.5
        priming_time = (wash_vol / 5.0) * 60  # 5 mL/min
        yield env.timeout(priming_time)

        # === Phase 2: 안정화 ===
        print(f"\n[Phase 2] Stabilization @ {env.now:.1f}s")
        yield env.timeout(10.0)

        # === Phase 3: 시약 포트 전환 (12-way) + REACTOR 전환 (3-way) ===
        print(f"\n[Phase 3] Reagent Injection Start @ {env.now:.1f}s")

        # 12-way: 시약 포트로 전환
        port_procs = []
        for name in inlet_names:
            target_port = reagent_ports.get(name, 2)
            port_procs.append(valves_12way[name].set_port(target_port))
        yield simpy.AllOf(env, port_procs)

        # 3-way: REACTOR로 전환
        reactor_procs = []
        for name in inlet_names:
            reactor_procs.append(valves_3way[name].set_position(ValveState.REACTOR))
        yield simpy.AllOf(env, reactor_procs)

        # 주입 시간
        injection_time = (step_vol / total_flow) * 60
        yield env.timeout(injection_time)

        # === Phase 4: Transit ===
        print(f"\n[Phase 4] Transit @ {env.now:.1f}s")

        # 3-way: WASTE로 복귀 (세척용매로 밀어내기)
        waste_procs = []
        for name in inlet_names:
            waste_procs.append(valves_3way[name].set_position(ValveState.WASTE))
        yield simpy.AllOf(env, waste_procs)

        # 12-way: 세척 포트(1)로 복귀
        solvent_procs = []
        for name in inlet_names:
            solvent_procs.append(valves_12way[name].set_port(1))
        yield simpy.AllOf(env, solvent_procs)

        transit_vol = zone_vol * 0.95
        transit_time = (transit_vol / total_flow) * 60
        yield env.timeout(transit_time)

        # === Phase 5: Collection ===
        print(f"\n[Phase 5] Collection @ {env.now:.1f}s")

        # Outlet: REACTOR(수거)로 전환
        yield outlet_valve.set_position(ValveState.REACTOR)

        import math
        num_tubes = math.ceil(step_vol / collect_per_tube)
        extra_vol = zone_vol * 0.05

        for i in range(num_tubes):
            if i == 0:
                tube_vol = collect_per_tube + extra_vol
            elif i == num_tubes - 1:
                remaining = step_vol - (i * collect_per_tube)
                tube_vol = min(remaining, collect_per_tube)
            else:
                tube_vol = collect_per_tube

            tube_time = (tube_vol / total_flow) * 60
            yield env.timeout(tube_time)

        # Wash tube
        wash_tube_vol = config.vol_collection * 1.0
        wash_tube_time = (wash_tube_vol / total_flow) * 60
        yield env.timeout(wash_tube_time)

        # Outlet: WASTE로 복귀
        yield outlet_valve.set_position(ValveState.WASTE)

        print(f"\n[Sequence Complete] @ {env.now:.1f}s")

    env.process(sequence())
    env.run()

    # 타임라인 출력
    print_timeline_ascii(timeline_events, env.now)

    return timeline_events, env.now


def print_valve_timing_summary():
    """밸브 전환 시간 기준값 출력"""
    config = TimelineConfig()

    print("\n" + "=" * 60)
    print("VALVE SWITCHING TIME REFERENCE")
    print("=" * 60)
    print(f"\n[12-way Selector Valve (Runze SV07)]")
    print(f"  - 시간/포트: {config.switch_time_12way_per_port:.1f}s")
    print(f"  - 최소 시간: {config.switch_time_12way_min:.1f}s")
    print(f"  - 최대 시간: {config.switch_time_12way_max:.1f}s (Port 1 ↔ Port 12)")
    print(f"\n[3-way Switcher Valve (Arduino)]")
    print(f"  - 전환 시간: {config.switch_time_3way:.1f}s (WASTE ↔ REACTOR)")
    print(f"\n[Outlet Valve]")
    print(f"  - 전환 시간: {config.switch_time_outlet:.1f}s (WASTE ↔ COLLECT)")
    print("\n" + "-" * 60)
    print("예상 전환 시간 예시:")
    print(f"  Port 1 → Port 2:  {config.switch_time_12way_per_port:.1f}s")
    print(f"  Port 1 → Port 6:  {5 * config.switch_time_12way_per_port:.1f}s")
    print(f"  Port 1 → Port 12: {config.switch_time_12way_max:.1f}s (최대)")
    print("=" * 60)


if __name__ == "__main__":
    # 기준값 출력
    print_valve_timing_summary()

    # 시뮬레이션 실행
    print("\n\n")
    run_valve_timeline_simulation(
        inlet_names=["Inlet A", "Inlet B", "Inlet C"],
        reagent_ports={"Inlet A": 3, "Inlet B": 5, "Inlet C": 7},
        flows={"Inlet A": 0.5, "Inlet B": 0.3, "Inlet C": 0.2},
        step_vol=3.0,
        collect_per_tube=1.0
    )
