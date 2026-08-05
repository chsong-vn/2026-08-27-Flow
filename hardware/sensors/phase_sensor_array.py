# -*- coding: utf-8 -*-
"""OCB350 위상센서 어레이 어댑터 — robochem_devices 벤더 백엔드.

@codesyncer-decision: RoboChem(Science 2024) 원본의 슬러그 경계 검출 스택을 그대로 이식.
  - 하드웨어: TT Electronics OCB350 튜브 클램프온 광센서(+캘리브레이션 보드),
    Arduino UNO 1대 = 최대 4센서. 펌웨어 = robochem_devices/_reference/firmware/
    Phase_Sensor_Array/ (Sx=y/Rx ASCII 9600bps, 위상변화 이벤트 push "i=phase",
    디바운스 10ms, 캘리브레이션 = 빈 튜브 상태에서 실행).
  - 백엔드: robochem_devices.optional.phase_sensor.PhaseSensorArray (원본 무수정).
  - 용도: HTE droplet 수집 경계의 '실측 트리거' (데드레코닝 마크 → 예상창+폴백).

계약 (엔진/UI 공용 — MFCKoreaMKP 와 동일 스타일):
    ps = PhaseSensorArrayHW(port, sensors={"collect": 0}, name="...")
    ps.connect() / ps.disconnect() / ps.is_connected / ps.stop()
    ps.read_phase("collect")  -> "CLEAR_LIQUID" | "OPAQUE_LIQUID" | "GAS"  (ERROR=예외)
    ps.is_liquid("collect")   -> bool
    ps.analog("collect")      -> 0~1023 (임계 튜닝/진단용)
    ps.calibrate("collect")   — ★빈 튜브(기체) 상태에서 실행해야 의미가 맞음
    ps.monitor("collect", "once"|"always"|"never")
    ps.read_event("collect")  -> phase str | None   (push 이벤트 논블로킹 폴)
    ps.wait_edge("collect", want_gas=True, timeout=30) -> bool
        — RoboChem WaitForPhaseChange 관례: 목표 위상이 이미 있으면 즉시 True(RETURN),
          아니면 monitor(once) + 이벤트 폴링. 타임아웃 시 False (예외 아님 — 호출부가
          폴백 경로를 갖는 하이브리드 트리거 전제).
port 에 "Mock" 포함 시 내장 시뮬 — sim_set_phase() 로 위상 주입(이벤트 push 재현).
"""

import time
import threading
from collections import deque

# 펌웨어 Phase enum (OCB350.h) 과 동일
PHASE_NAMES = {0: "ERROR", 1: "CLEAR_LIQUID", 2: "OPAQUE_LIQUID", 3: "GAS"}


class PhaseSensorError(RuntimeError):
    """센서 단선/프로토콜 오류 — 조용한 실패 금지 (MFCProtocolError 관례)."""


class PhaseSensorArrayHW:
    def __init__(self, port, sensors=None, name="PhaseSensors"):
        self.name = name
        self.port = port
        # 논리명 → 어레이 인덱스 (기본: 아웃렛 직전 'collect' 센서 1개 = index 0)
        self.sensors = {str(k): int(v) for k, v in (sensors or {"collect": 0}).items()}
        self.is_connected = False
        self._mock = (not port) or ("Mock" in str(port))
        self._arr = None
        self._proxies = {}
        self._lock = threading.Lock()   # Manual/엔진 동시 접근 직렬화
        # 테스트 훅: robochem FakeSerial 주입용 (실기 경로엔 미사용)
        self._serial_iface_override = None
        # ── Mock 상태 (실기와 동일 의미론: monitor 무장 시에만 이벤트 발생) ──
        self._sim_phase = {k: 1 for k in self.sensors}          # 기본 CLEAR_LIQUID
        self._sim_events = {k: deque() for k in self.sensors}
        self._sim_monitor = {k: "never" for k in self.sensors}
        # @codesyncer(하이브리드): monitor("always") 중엔 이벤트 스트림이 위상의 진실원 —
        #   read_phase 를 '캐시'로 응답해 ①시리얼 요청-응답과 push 라인의 인터리브 방지
        #   ②대시보드 폴(StatusWorker)이 엔진의 이벤트를 뺏지 않음. 이벤트=변화 그 자체라
        #   캐시는 디바운스(10ms) 내에서 정확.
        self._last_phase = {k: None for k in self.sensors}
        self._monitor_on = {k: False for k in self.sensors}

    # ── 연결 ─────────────────────────────────────────────────
    def connect(self):
        if self._mock:
            print(f"[{self.name}] Virtual PhaseSensor (Mock) — OCB350 시뮬")
            self.is_connected = True
            return True
        from robochem_devices.optional.phase_sensor import PhaseSensorArray
        arr = PhaseSensorArray()
        if self._serial_iface_override is not None:
            arr._serial_iface = self._serial_iface_override
        arr.open(self.port)
        arr.setup(array={k: {"index": v} for k, v in self.sensors.items()})
        arr.initialize()
        self._arr = arr
        self._proxies = {k: arr.proxy(k) for k in self.sensors}
        self.is_connected = True
        print(f"[{self.name}] OCB350 어레이 연결 @ {self.port} "
              f"(센서 {len(self.sensors)}: {', '.join(self.sensors)})")
        return True

    def disconnect(self):
        with self._lock:
            if self._arr is not None:
                try:
                    self._arr.close()
                except Exception:
                    pass
                self._arr = None
                self._proxies = {}
            self.is_connected = False

    def stop(self):
        """계약 균일성용 no-op (센서는 능동 동작 없음)."""

    # ── 내부 ─────────────────────────────────────────────────
    def _key(self, which):
        k = str(which)
        if k not in self.sensors:
            raise PhaseSensorError(
                f"[{self.name}] 미등록 센서 '{which}' (등록: {list(self.sensors)})")
        return k

    def _proxy(self, k):
        if self._proxies.get(k) is None:
            raise PhaseSensorError(f"[{self.name}] 미연결 상태에서 접근: '{k}'")
        return self._proxies[k]

    # ── 판독 ─────────────────────────────────────────────────
    def read_phase(self, which="collect"):
        k = self._key(which)
        with self._lock:
            if self._mock:
                v = self._sim_phase[k]
                if v == 0:
                    raise PhaseSensorError(f"[{self.name}] 센서 '{k}' 단선(ERROR)")
                self._last_phase[k] = PHASE_NAMES[v]
                return self._last_phase[k]
            # 모니터 활성 중 = 이벤트 스트림이 진실원 → 캐시 응답 (시리얼 인터리브 방지)
            if self._monitor_on.get(k) and self._last_phase.get(k) is not None:
                return self._last_phase[k]
            # robochem 이 ERROR(0) 이면 SensorError raise — 그대로 전파(시끄럽게)
            val = str(self._proxy(k)["phase"])
            self._last_phase[k] = val
            return val

    def is_liquid(self, which="collect"):
        return self.read_phase(which) != "GAS"

    def read_all(self):
        return {k: self.read_phase(k) for k in self.sensors}

    def analog(self, which="collect"):
        k = self._key(which)
        with self._lock:
            if self._mock:
                # 시뮬: 위상별 대표값 (기체 밝음 → 큰 값)
                return {1: 300, 2: 150, 3: 900}.get(self._sim_phase[k], 0)
            return int(self._proxy(k)["analog"])

    # ── 캘리브레이션/모니터 ──────────────────────────────────
    def calibrate(self, which="collect"):
        """★빈 튜브(기체만) 상태에서 실행 — 펌웨어 관례. 액체가 있으면 분류가 뒤집힘."""
        k = self._key(which)
        with self._lock:
            if self._mock:
                print(f"[{self.name}] (Mock) calibrate '{k}'")
                return
            self._proxy(k)["calibrate"] = "run"

    def monitor(self, which="collect", mode="once"):
        k = self._key(which)
        mode = str(mode).lower()
        if mode not in ("never", "once", "always"):
            raise PhaseSensorError(f"monitor mode '{mode}' (never|once|always)")
        with self._lock:
            if self._mock:
                self._sim_monitor[k] = mode
                self._monitor_on[k] = (mode != "never")
                if mode == "never":
                    self._sim_events[k].clear()
                return
            if mode != "never" and self._last_phase.get(k) is None:
                # 캐시 시드 — 무장 직후 read_phase 캐시응답이 유효하도록 1회 실판독
                self._last_phase[k] = str(self._proxy(k)["phase"])
            self._proxy(k)["monitor"] = mode
            self._monitor_on[k] = (mode != "never")

    def read_event(self, which="collect"):
        """monitor 중 위상변화 push 를 논블로킹 폴. 변화 없으면 None."""
        k = self._key(which)
        with self._lock:
            if self._mock:
                ev = self._sim_events[k].popleft() if self._sim_events[k] else None
                if ev == "ERROR":
                    # 실 펌웨어 계약: 단선 시 'i=0' push → robochem 이 SensorError raise
                    raise PhaseSensorError(f"[{self.name}] 센서 '{k}' 단선(ERROR 이벤트)")
            else:
                ev = self._proxy(k).read_event()
                ev = None if ev is None else str(ev)
            if ev is not None:
                self._last_phase[k] = ev
            return ev

    def wait_edge(self, which="collect", want_gas=True, timeout=30.0, poll=0.05):
        """목표 위상(기체/액체) 도달 대기 — RoboChem WaitForPhaseChange 의 RETURN 정책.

        이미 목표 위상이면 즉시 True. 아니면 monitor(once)+이벤트 폴링.
        timeout 초과 시 False (하이브리드 트리거의 폴백 분기용 — 예외 아님)."""
        k = self._key(which)
        want_liquid = not want_gas
        cur = self.read_phase(k)
        if (cur != "GAS") == want_liquid:
            return True
        self.monitor(k, "once")
        t_left = float(timeout)
        try:
            while t_left > 0:
                ev = self.read_event(k)
                if ev is not None and ((ev != "GAS") == want_liquid):
                    return True
                time.sleep(poll)
                t_left -= poll
        finally:
            try:
                self.monitor(k, "never")
            except Exception:
                pass
        return False

    # ── Mock 전용 (테스트/시뮬) ──────────────────────────────
    def sim_set_phase(self, which, phase):
        """Mock 위상 주입 — 실기 펌웨어의 디바운스드 push 재현 (monitor 무장 시 이벤트)."""
        if not self._mock:
            raise PhaseSensorError("sim_set_phase 는 Mock 전용")
        k = self._key(which)
        v = int(phase) if not isinstance(phase, str) else \
            {n: i for i, n in PHASE_NAMES.items()}[phase]
        with self._lock:
            if v != self._sim_phase[k]:
                self._sim_phase[k] = v
                # 실 펌웨어 계약: 단선(ERROR=0)도 위상 '변화'라 push 됨 — read_event 가 raise
                if self._sim_monitor[k] in ("once", "always"):
                    self._sim_events[k].append(PHASE_NAMES[v])
                    if self._sim_monitor[k] == "once":
                        self._sim_monitor[k] = "never"


class MockPhaseSensor(PhaseSensorArrayHW):
    """가상 위상센서 — 포트와 무관하게 항상 Mock 경로 (factory '가상 위상센서')."""

    def __init__(self, port="Mock_Port", sensors=None, name="MockPhaseSensor"):
        super().__init__("Mock_Port", sensors=sensors, name=name)
