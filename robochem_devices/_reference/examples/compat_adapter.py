"""
compat_adapter.py — RoboChem SyringePump을 기존 분취 제어 엔진의 펌프 규약에 맞추는 어댑터.

기존 엔진 전제 (Chemyx RS-485 기반):
  - 논블로킹 시작: start를 걸면 즉시 리턴, 펌프는 배경에서 돎 (스태거 타이밍의 전제)
  - 진행률 조회 가능: 데드볼륨 모델/유체 트래커가 시간축으로 라인 상태 추적
  - 정지 가능 + 정지 후 실토출량 확인

RoboChem 측 제약 (어댑터가 흡수하는 것):
  - write가 완료까지 블로킹 + 그동안 시리얼 락 점유 → 이동 중 실측 조회 불가
    → 진행률은 시간모델 추정치 제공, 완료 시 실측으로 스냅 (기존 엔진도 시간모델 기반이라 정합)
  - 유한 시린지 + 리필 갭 → pumping_event로 갭 구간 노출 (유체 트래커가 타임라인 보정용)
  - 단위 µL ↔ 기존 엔진 mL

사용:
    ad = RoboChemPumpAdapter(pump, syringe_ml=1.0)
    ad.start_dispense(volume_ml=2.5, flowrate_ml_min=20.0, allow_refill=True)
    ... ad.busy / ad.progress_ml / ad.refill_gaps ...
    actual = ad.wait()        # 또는 ad.stop() 후 wait()
"""

import time
import threading

from robochem_devices import SyringePump, PumpVolume


class RoboChemPumpAdapter:
    def __init__(self, pump: SyringePump, syringe_ml: float, time_scale: float = 1.0):
        """
        @param time_scale: 에뮬레이터 배속 (실기=1.0, 시뮬레이션=emulators.TIME_SCALE).
                           진행률 시간모델 추정에만 사용.
        """
        self.pump = pump
        self.syringe_ml = syringe_ml
        self.time_scale = time_scale
        self._thread: threading.Thread | None = None
        self._pumping_event = threading.Event()
        self._lock = threading.Lock()
        self._target_ml = 0.0
        self._flowrate = 0.0
        self._actual_ml: float | None = None
        self._error: Exception | None = None
        self._pumped_seconds = 0.0     # pumping_event가 set인 누적 시간 (스케일 보정 후)
        self._sampler: threading.Thread | None = None
        self._stop_requested = False
        self.refill_gaps: list[tuple[float, float]] = []   # (시작, 끝) — 시작시점 기준 상대초

    # ---------- 기존 엔진 규약 API ----------

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_dispense(self, volume_ml: float, flowrate_ml_min: float,
                       allow_refill: bool = True) -> None:
        """논블로킹 시작. 즉시 리턴."""
        if self.busy:
            raise RuntimeError("Pump adapter busy.")
        with self._lock:
            self._target_ml = volume_ml
            self._flowrate = flowrate_ml_min
            self._actual_ml = None
            self._error = None
            self._pumped_seconds = 0.0
            self._stop_requested = False
            self.refill_gaps = []
        self._pumping_event.clear()

        def job():
            try:
                moved_ul = PumpVolume.run(
                    pump=self.pump,
                    volume=volume_ml * 1000.0,
                    flowrate=flowrate_ml_min,
                    allow_refill=allow_refill,
                    pumping_event=self._pumping_event,
                )
                self._actual_ml = moved_ul / 1000.0
            except Exception as e:
                self._error = e
            finally:
                if self._stop_requested:
                    # stop 이벤트 라이프사이클 마감 (clear) — 재사용 가능 상태 복원
                    self.pump.stop_parameter("pump", stop=False)

        self._thread = threading.Thread(target=job, name="pump_adapter", daemon=True)
        self._thread.start()
        self._sampler = threading.Thread(target=self._sample_loop, daemon=True)
        self._sampler.start()

    def start_withdraw(self, volume_ml: float, flowrate_ml_min: float) -> None:
        """흡입 (리필 불가 — 시린지 여유공간 내에서만)."""
        self.start_dispense(-volume_ml, flowrate_ml_min, allow_refill=False)

    @property
    def progress_ml(self) -> float:
        """
        시간모델 추정 진행률. 완료 시 실측으로 스냅.
        리필 갭(pumping_event=False) 구간은 진행 정지로 집계 → 기존 유체 트래커와 정합.
        """
        if self._actual_ml is not None:
            return abs(self._actual_ml)
        est = self._flowrate * (self._pumped_seconds / 60.0)
        return min(est, abs(self._target_ml))

    def stop(self) -> None:
        """비동기 정지 요청. wait()으로 실토출량 확인."""
        if not self.busy:
            return
        self._stop_requested = True
        PumpVolume.stop_asynchronously(pump=self.pump, pumping_event=self._pumping_event)

    def wait(self, timeout: float | None = None) -> float:
        """완료 대기 → 실토출량(mL) 리턴. 태스크 예외는 여기서 재발생."""
        if self._thread is not None:
            self._thread.join(timeout)
        if self._error is not None:
            raise self._error
        return abs(self._actual_ml) if self._actual_ml is not None else 0.0

    def set_valve(self, where: str) -> None:
        """'system' | 'reservoir' — 기존 라인상태 추적기의 밸브 모델에 대응."""
        self.pump["valve_setpoint"] = {"system": "ON", "reservoir": "OFF"}[where]

    def emergency(self) -> None:
        self.pump.emergency_close()

    # ---------- 내부 ----------

    def _sample_loop(self):
        """pumping_event 상태를 20ms 간격 샘플링 → 누적 펌핑시간 + 리필 갭 기록."""
        t_start = time.time()
        gap_start = None
        last = time.time()
        while self.busy:
            time.sleep(0.02)
            now = time.time()
            if self._pumping_event.is_set():
                self._pumped_seconds += (now - last) / self.time_scale
                if gap_start is not None:
                    self.refill_gaps.append((gap_start - t_start, now - t_start))
                    gap_start = None
            else:
                if gap_start is None and self._pumped_seconds > 0:
                    gap_start = now
            last = now
