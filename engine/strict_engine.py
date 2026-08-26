import math
import queue
import threading
import time
from typing import Dict, List, Optional, Tuple, Callable

from engine.flow_engine import FlowEngine
from engine.safety_manager import SafetyError
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

# @codesyncer-decision: 시퀀스 스마트펌프 판별을 isinstance(ChemyxSmartPump) 에서
#   계약 기반 덕타이핑으로 일반화 — NRGSmartPump 등 다른 드라이버 계열의 스마트
#   어댑터가 같은 시퀀스 흐름(refill/wash/prime/dosing 부기)을 탈 수 있게 한다.
#   요구 표면 = 엔진이 이 파일에서 실제로 호출/대입하는 것 전부:
#   prepare/trigger/complete 3계열 + set_flow/start/stop + driver(is_stopped) +
#   current_vol/capacity/is_refilling(및 target_flow/running/status/_abort_refill 대입).
_SMART_PUMP_API = (
    "refill_prepare", "refill_trigger", "refill_complete",
    "prime_prepare", "prime_trigger", "prime_complete",
    "wash_withdraw_prepare", "wash_infuse_prepare",
    "set_flow", "start", "stop", "driver",
    "current_vol", "capacity", "is_refilling",
)


def _is_smart_pump(pump) -> bool:
    """시퀀스 스마트펌프 계약 구현 여부 (클래스 무관)."""
    if isinstance(pump, ChemyxSmartPump):
        return True
    return all(hasattr(pump, attr) for attr in _SMART_PUMP_API)


class CollectionTimer:
    """Injection START 기준 타이밍으로 Outlet valve + Collector를 독립적으로 조작.

    Main thread의 펌프 로직과 독립 실행되어 injection 중에도 valve 전환이 가능.
    서로 다른 COM 포트를 사용하는 장치들이므로 pump 명령과 병렬 실행해도 bus 충돌 없음.

    @codesyncer-decision(P1 레인 분리, 2026-07-28): 스케줄러(클록 감시)와 집행(레인
      워커)을 분리 — '계획과 집행의 분리' (HPLC/Bluesky 관례의 호스트판).
      - 이벤트는 lane 별 FIFO 큐로 디스패치: 레인 안에서만 순서/직렬 보장, 레인끼리
        병렬. 느린 collector 이동(M400)이 Outlet 전환을 밀어내던 '소프트웨어 직렬화'
        제거 (포트가 다른 장치는 병렬 안전 — 같은 포트는 드라이버 락이 직렬화).
      - lane_leads: 레인별 선행 발화(초). 이동을 경계보다 lead 만큼 미리 시작해
        도착을 경계에 맞춤. 임의값 시드 → '지각' 로그로 수렴시키는 튜닝 노브.
      - waste_guard: 이동 중 Outlet→WASTE 가드(상용 분취기 move-while-waste).
        meta.guard_waste/guard_restore 콜러블이 있는 이동 이벤트에만, Outlet 이
        COLLECT 국면일 때만 발동. 종료(terminal WASTE) 후엔 복원 금지 + 복원 직후
        종료 레이스 재확인(되WASTE) — COLLECT 로 끝나는 사고 차단.
      - 이벤트 튜플 (t, name, action[, lane[, meta]]): 3튜플은 lane="default" 단일
        레인 = 기존과 동일한 직렬 실행 (HTE 경로/기존 테스트 하위호환).
    """

    def __init__(self, engine, events, lane_leads=None, waste_guard=False):
        """
        Args:
            engine: StrictSequenceEngine 인스턴스 (abort_flag 공유)
            events: [(elapsed_sec, name, action[, lane[, meta]]), ...]
                    — injection_start부터의 경과 시간 (pumping time 기준)
            lane_leads: {lane: 선행발화 초} (해당 레인 이벤트를 t-lead 에 디스패치)
            waste_guard: 이동가드 활성화 (meta.guard_* 있는 이벤트에만 적용)
        """
        self.engine = engine
        norm = []
        for ev in events:
            if len(ev) >= 5:
                t, name, action, lane, meta = ev[0], ev[1], ev[2], ev[3], ev[4]
            elif len(ev) == 4:
                t, name, action, lane = ev
                meta = {}
            else:
                t, name, action = ev
                lane, meta = "default", {}
            norm.append((float(t), str(name), action,
                         str(lane or "default"), dict(meta or {})))
        self.events = sorted(norm, key=lambda e: e[0])
        self.lane_leads = {str(k): max(0.0, float(v or 0.0))
                           for k, v in (lane_leads or {}).items()}
        self.waste_guard = bool(waste_guard)
        # 디스패치 순서 = 발화 시각(fire = t − lead, 0 클램프). t 는 '의도 경계'로 유지.
        self._sched = sorted(
            ((max(0.0, t - self.lane_leads.get(lane, 0.0)), t, name, action, lane, meta)
             for t, name, action, lane, meta in self.events),
            key=lambda e: e[0])
        self.start_time: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause_lock = threading.Lock()
        self._pause_start: Optional[float] = None
        self._total_paused: float = 0.0
        self._queues: Dict[str, "queue.Queue"] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._valve_phase: Optional[str] = None      # meta.kind 로 갱신 (collect/waste)
        self._terminal_fired = threading.Event()     # 종료 WASTE 발화됨 → 가드 복원 금지

    def start(self):
        # 트레이스: 계획된 이벤트를 예정 시각(무일시정지 가정)에 instant 로 미리 기록
        # → 실측 발화(TIMER 트랙)와 위아래로 놓고 편차를 눈으로 비교하는 PLAN 트랙
        # @codesyncer(검증 2026-08-11): PLAN 기록은 반드시 start_time 앵커 캡처 '전'.
        #   앵커 후에 두면 이벤트당 디스크 쓰기 소요가 pumping-elapsed 로 계산돼
        #   전 이벤트가 조기 발화 — fix #4(앵커 갭)가 막은 제품 유실 버그의 재개방.
        _plan_t0 = time.time()
        tr = getattr(self.engine, "trace", None)
        if tr:
            for t, name, _action, lane, _meta in self.events:
                tr.instant("TIMER PLAN", name, ts=_plan_t0 + t,
                           args={"t_sec": round(t, 1), "lane": lane})
        # @codesyncer-decision(P2, 2026-08-12): 타이머 클록 = time.monotonic —
        #   NTP 동기화/수동 시계 조정이 wall-clock 을 점프시키면 분취 경계 전체가
        #   그만큼 이동(조기 WASTE=제품 유실)하던 경로 차단. 트레이스 절대 ts 만
        #   wall-clock(time.time) 유지 (TraceLogger._ts_us 계약).
        self.start_time = time.monotonic()
        for lane in {e[3] for e in self.events}:
            q: "queue.Queue" = queue.Queue()
            self._queues[lane] = q
            w = threading.Thread(target=self._worker, args=(lane, q),
                                 daemon=True, name=f"CT-{lane}")
            self._workers[lane] = w
            w.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="CollectionTimer")
        self._thread.start()

    def wait_finish(self, timeout: Optional[float] = None):
        """모든 스케줄된 이벤트가 자연 완료될 때까지 대기 (정상 경로)."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def stop(self, timeout: float = 2.0):
        """대기 중인 이벤트를 즉시 취소 (abort 경로)."""
        self._stop.set()
        self._end_workers()   # 큐 대기 중 워커 해제 (이후 아이템은 실행 없이 드레인)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        for w in self._workers.values():
            if w.is_alive():
                w.join(timeout=max(0.1, timeout / max(1, len(self._workers))))

    def total_duration_sec(self) -> float:
        """마지막 이벤트의 예정 시각 반환 (대기 시간 계산용)."""
        if not self.events:
            return 0.0
        return max(e[0] for e in self.events)

    def remaining_sec(self) -> float:
        """마지막 이벤트까지 남은 pumping 기준 시간.
        @codesyncer-decision: wait_finish 예산 계산은 반드시 이 값을 사용한다.
        - 기존 버그: wall-clock(inject_start 기준) 경과와 pumping-elapsed(pause 제외)를
          섞어서 빼는 바람에 prime/solvent refill이 길면 예산이 5초 바닥으로 붕괴
          → 타이머가 살아있는 채로 참조만 끊겨 orphan thread가 다음 step의
          Outlet/Collector를 마음대로 조작하는 사고 가능성.
        """
        if self.start_time is None:
            return self.total_duration_sec()
        return self.total_duration_sec() - self._pumping_elapsed()

    def is_alive(self) -> bool:
        return bool((self._thread and self._thread.is_alive())
                    or any(w.is_alive() for w in self._workers.values()))

    def shift(self, delta_sec: float):
        """센서 재앵커(하이브리드 트리거): 잔여 이벤트를 delta 만큼 일괄 지연(+)/앞당김(-).

        @codesyncer: 위상센서 엣지가 예상보다 늦으면(가스 압축 드리프트) delta>0 —
        _total_paused 에 가산하면 pumping-elapsed 가 그만큼 줄어 이후 이벤트가
        일괄 지연된다. 조기 엣지는 음수(경과가 점프, 기한 지난 이벤트는 순서대로
        즉시 발화). 이벤트 개별 시각이 아닌 '클록'을 움직이므로 마크 간 간격
        (엣지 이후 데드레코닝 구간)은 이상 프로파일 그대로 유지된다."""
        with self._pause_lock:
            self._total_paused += float(delta_sec)

    def pause(self):
        """Pump이 멈춤 시 호출. Timer는 이 시간만큼 이벤트 발동 미룸."""
        with self._pause_lock:
            if self._pause_start is None:
                self._pause_start = time.monotonic()

    def resume(self):
        """Pump이 재가동 시 호출. 누적 pause 시간에 반영."""
        with self._pause_lock:
            if self._pause_start is not None:
                self._total_paused += time.monotonic() - self._pause_start
                self._pause_start = None

    def _pumping_elapsed(self) -> float:
        """Pump 실제 동작 시간 기준 경과 시간 (pause 제외)."""
        with self._pause_lock:
            if self._pause_start is not None:
                # 현재 pause 중 → pause 시작 시점까지의 pumping 경과
                base = self._pause_start - self.start_time
                return base - self._total_paused
            return (time.monotonic() - self.start_time) - self._total_paused

    def _end_workers(self):
        """레인 큐에 센티널 삽입 — 워커 스레드 자연 종료 유도."""
        for q in self._queues.values():
            try:
                q.put_nowait(None)
            except Exception:
                pass

    def _run(self):
        """스케줄러: 클록만 감시하고, 시각 도달 시 레인 큐에 '던지기만' 한다.
        블로킹 액션은 절대 여기서 실행하지 않는다 (계획과 집행의 분리)."""
        try:
            if self._sched:
                _f0, _t0 = self._sched[0][0], self._sched[0][1]
                msg = (f"  [Timer] thread started. {len(self._sched)} events scheduled. "
                       f"first @ {_t0:.1f}s")
                if _f0 < _t0:
                    msg += f" (선행발화 {_f0:.1f}s)"
                if len(self._queues) > 1:
                    msg += f" | lanes: {', '.join(sorted(self._queues))}"
                self.engine._log(msg)
            else:
                self.engine._log("  [Timer] WARNING: no events scheduled!")
        except Exception:
            pass
        last_log_decile = -1
        for fire_sec, target_sec, name, action, lane, meta in self._sched:
            while True:
                if self._stop.is_set() or getattr(self.engine, "abort_flag", False):
                    try:
                        self.engine._log(
                            f"  [Timer] ABORTED before '{name}' @ {target_sec:.1f}s"
                        )
                    except Exception:
                        pass
                    self._end_workers()
                    return
                try:
                    elapsed = self._pumping_elapsed()
                except Exception as exc:
                    try:
                        self.engine._log(f"  [Timer] elapsed calc FAILED: {exc}")
                    except Exception:
                        pass
                    self._end_workers()
                    return
                if elapsed >= fire_sec:
                    break
                # 10초마다 진단 로그 (남은 시간 카운트다운)
                decile = int(elapsed) // 10
                if decile > last_log_decile and elapsed > 0:
                    last_log_decile = decile
                    remaining = max(0.0, target_sec - elapsed)
                    try:
                        self.engine._log(
                            f"  [Timer] → {name} | 남은 시간 {remaining:.0f}s "
                            f"(경과 {elapsed:.0f}s / 목표 {target_sec:.0f}s)"
                        )
                    except Exception:
                        pass
                time.sleep(0.05)
            self._queues[lane].put((target_sec, name, action, meta))
        # 전 이벤트 디스패치 완료 → 레인 자연 드레인 대기 후 종료
        self._end_workers()
        for w in self._workers.values():
            w.join()
        try:
            self.engine._log("  [Timer] thread completed — all events fired")
        except Exception:
            pass

    def _worker(self, lane, q):
        """레인 집행기: 자기 레인의 이벤트만 FIFO 로 실행. 다른 레인을 절대 막지 않는다.

        @codesyncer(C3a): 지각(이전 '같은 레인' 액션 블로킹)·액션 소요를 기록 —
          이벤트 간격 < 이동시간인 설정 문제를 사후 진단 가능하게. 레인 분리 후
          지각은 같은 레인 안에서만 발생하므로 로그의 지각 = 순수 장치 지연이다.
        """
        while True:
            item = q.get()
            if item is None:
                return
            if self._stop.is_set() or getattr(self.engine, "abort_flag", False):
                continue   # abort — 실행 없이 드레인
            target_sec, name, action, meta = item
            kind = meta.get("kind")
            # move-while-waste 가드: COLLECT 국면의 이동에만, 종료 후엔 금지
            guard_on = (self.waste_guard
                        and callable(meta.get("guard_waste"))
                        and callable(meta.get("guard_restore"))
                        and self._valve_phase == "collect"
                        and not self._terminal_fired.is_set())
            try:
                elapsed = self._pumping_elapsed()
            except Exception:
                elapsed = target_sec
            fire_late = max(0.0, elapsed - target_sec)
            # 종료(terminal WASTE)는 액션 '전'에 플래그 — 가드 복원 레이스를 닫는 방향
            if kind == "waste" and meta.get("terminal"):
                self._terminal_fired.set()
            t_act = time.time()        # 트레이스 절대 ts (wall-clock 계약)
            m_act = time.monotonic()   # 액션 소요 측정 (NTP 무관)
            try:
                if guard_on:
                    try:
                        meta["guard_waste"]()
                        self.engine._log(f"  [Timer] {name}: 이동가드 Outlet→WASTE")
                    except Exception as exc:
                        self.engine._log(f"  [Timer] {name}: 이동가드 WASTE 실패(무시): {exc}")
                action()
                if kind in ("collect", "waste"):
                    self._valve_phase = kind
                act_dur = time.monotonic() - m_act
                tr = getattr(self.engine, "trace", None)
                if tr:
                    tr.complete(f"TIMER {lane}", name, t_act, act_dur,
                                args={"target_sec": round(target_sec, 1),
                                      "late_sec": round(fire_late, 1)})
                try:
                    msg = f"  [Timer] {name} @ pump-elapsed {target_sec:.1f}s FIRED"
                    if fire_late > 0.5:
                        msg += f" | ⚠ {fire_late:.1f}s 지각"
                    if act_dur > 0.5:
                        msg += f" | 액션 {act_dur:.1f}s 소요"
                    if lane != "default":
                        msg += f" | lane={lane}"
                    self.engine._log(msg)
                except Exception:
                    pass
            except Exception as exc:
                tr = getattr(self.engine, "trace", None)
                if tr:
                    tr.instant(f"TIMER {lane}", f"FAILED: {name}",
                               args={"error": str(exc)[:200]})
                try:
                    self.engine._log(f"  [Timer] {name} FAILED: {exc}")
                except Exception:
                    pass
            if guard_on:
                if self._terminal_fired.is_set() or self._stop.is_set() \
                        or getattr(self.engine, "abort_flag", False):
                    pass   # 종료/중단 후 COLLECT 복원 금지 (WASTE 유지가 안전상태)
                else:
                    try:
                        meta["guard_restore"]()
                        if self._terminal_fired.is_set():
                            # 복원 직후 종료 WASTE 가 발화된 레이스 → 되돌림
                            meta["guard_waste"]()
                        else:
                            self.engine._log(f"  [Timer] {name}: 이동가드 해제 Outlet→COLLECT")
                    except Exception as exc:
                        try:
                            self.engine._log(f"  [Timer] {name}: 이동가드 해제 실패: {exc}")
                        except Exception:
                            pass


class _HteSensorSync(threading.Thread):
    """위상센서 엣지 ↔ 타이밍 예상창 하이브리드 동기화 (HTE droplet).

    @codesyncer-decision: 부피→시간 마크(데드레코닝)는 가스 압축성 드리프트가 누적됨.
    센서 엣지가 예상창 안에 오면 CollectionTimer '클록'을 shift(Δ) 재앵커 — 이후
    모든 마크가 실측 경계 기준으로 이동하고, 엣지 사이 구간(퍼지/v_push)만
    데드레코닝으로 남는다. 예상창이 게이트라 잡기포(창 밖/kind 불일치)로 인한
    카운트 desync 가 구조적으로 불가능. 실패는 전부 '타이밍 폴백'으로 강등:
      · 창 경과 무엣지 → missed++, 경고 (그 경계는 기존 타이밍 그대로)
      · 센서 예외(단선 등) → 동기화 스레드만 종료, 트레인은 계속
    RoboChem 관례(센서=트리거, 시간=타임아웃×여유)의 하이브리드 변형.
    """

    def __init__(self, engine, sensor, key, timer, expected, window_sec):
        super().__init__(daemon=True, name="HteSensorSync")
        self.engine = engine
        self.sensor = sensor
        self.key = key
        self.timer = timer
        self.expected = list(expected)   # [(t_exp_sec, 'G2L'|'L2G', label)] 정렬
        self.w = float(window_sec)
        self.matched = 0
        self.missed = 0
        self.spurious = 0
        self.total_shift = 0.0
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def run(self):
        try:
            self.sensor.monitor(self.key, "always")
        except Exception as e:
            self.engine._log(f"[HTE-Sensor] 모니터 무장 실패 — 타이밍 폴백: {e}")
            return
        idx = 0
        try:
            while not self._stop_evt.is_set() and idx < len(self.expected):
                try:
                    el = (self.timer._pumping_elapsed()
                          if self.timer.start_time is not None else 0.0)
                except Exception:
                    el = 0.0
                # 창 마감 — 엣지 미검출은 그 경계만 타이밍 폴백 (이후 엣지로 재동기)
                while idx < len(self.expected) and el > self.expected[idx][0] + self.w:
                    t_exp, _kind, label = self.expected[idx]
                    self.engine._log(f"[HTE-Sensor] ⚠ {label} 엣지 미검출 "
                                     f"(창 {t_exp:.1f}±{self.w:.1f}s) — 타이밍 폴백")
                    self.missed += 1
                    idx += 1
                try:
                    ev = self.sensor.read_event(self.key)
                except Exception as e:
                    self.engine._log(f"[HTE-Sensor] ⚠ 센서 오류 — 동기화 중단"
                                     f"(트레인은 타이밍으로 계속): {e}")
                    return
                if ev is not None and idx < len(self.expected):
                    # @codesyncer(검증 2026-08-12, C2): 일시정지 중 매칭 금지 — 정지 중
                    #   가스 감압으로 계면이 센서를 왕복하는 엣지가 창 안에 들면 클록을
                    #   오앵커하고 idx 를 소모해 진짜 엣지를 버림 ('타이머는 구동 중에만'
                    #   불변식의 센서판). 스퓨리어스로 계수하고 넘어간다.
                    with self.timer._pause_lock:
                        _paused = self.timer._pause_start is not None
                    if _paused:
                        self.spurious += 1
                        self.engine._log(f"[HTE-Sensor] 일시정지 중 엣지 무시 ({ev})")
                        time.sleep(0.05)
                        continue
                    kind = "L2G" if ev == "GAS" else "G2L"
                    t_exp, k_exp, label = self.expected[idx]
                    if kind == k_exp and abs(el - t_exp) <= self.w:
                        delta = el - t_exp
                        self.timer.shift(delta)
                        self.total_shift += delta
                        self.matched += 1
                        self.engine._log(f"[HTE-Sensor] {label} 엣지 검출 → "
                                         f"Δ{delta:+.2f}s 재앵커")
                        idx += 1
                    else:
                        self.spurious += 1
                        self.engine._log(f"[HTE-Sensor] 창 밖/불일치 엣지 무시 "
                                         f"({ev} @ {el:.1f}s, 다음 예상 {k_exp} "
                                         f"{t_exp:.1f}s) — 잡기포 방어")
                elif ev is not None:
                    self.spurious += 1
                time.sleep(0.05)
        finally:
            try:
                self.sensor.monitor(self.key, "never")
            except Exception:
                pass

    def summary(self):
        return (f"재앵커 {self.matched}/{len(self.expected)} · 미검출 {self.missed} · "
                f"잡음무시 {self.spurious} · 누적보정 {self.total_shift:+.1f}s")


class HeadArrivalProbe(threading.Thread):
    """표준(연속류) 경로 HEAD 도달 실측 프로브 — RoboChem 센서구동 로직 이식.

    @codesyncer-context(2026-08-15): 표준 경로는 전 구간 개루프였다. t_head 는
      배관 실측값에서 해석적으로 계산될 뿐 '실제로 언제 도달했는지'를 아무도 안
      본다. 그래서 반응기 암부 0.3 mL 미계상(=37.5s @0.48) 같은 오차가 실런에서
      '분취기가 먼저 전환'으로만 나타나고 로그엔 흔적이 남지 않았다(2026-08-14).
      반응기 출구~아웃렛 밸브 사이에 OPB 위상센서가 이미 물려 있는데도 표준
      경로는 이를 전혀 소비하지 않았다(HTE 드롭 모드만 HteSensorSync 로 사용).

    @codesyncer-decision: RoboChem `WaitForPhaseChange`/`MonitorPhase` 의 계약을
      그대로 가져온다 — 센서=트리거, 시간=타임아웃×여유, 실패는 항상 타이밍 폴백.
      1. 예상창(t_exp ± window) 게이트 — 창 밖 엣지는 잡기포로 보고 무시
      2. 타임아웃(창 마감) 도달 시 예외가 아니라 '미검출' 로그 후 종료
      3. 일시정지 중 엣지 무시 (HteSensorSync 와 동일 불변식)
      4. finally 에서 반드시 monitor("never") — 센서 상태 누수 금지
      RoboChem 과 다른 점: 저쪽은 가스 분절 슬러그라 상전이가 항상 존재하지만
      이 시스템의 표준 경로는 연속 액체다. 그래서 검출기를 2개 둔다.
        · 상전이 엣지(read_event) — 가스 스페이서가 있을 때
        · ADC 스텝(analog) — 유색 시약 선단. 베이스라인 대비 |Δ| >= adc_delta 가
          confirm_n 회 연속이면 선단으로 인정 (단발 노이즈 방어)
      둘 다 무신호면 아무 일도 일어나지 않고 기존 타이밍이 그대로 쓰인다.

    @codesyncer-risk: mode="anchor" 는 실측 엣지로 CollectionTimer 클록을 이동시켜
      수집 경계 전체를 옮긴다. 센서 오검출이 곧 분획 어긋남이므로 기본값은 "off",
      권장 도입 순서는 off → observe(관측·로깅만, 제어 무영향) → anchor.
      observe 로 몇 런 돌려 Δ 가 재현되면 그 값을 outlet_switch_delay_sec 로
      고정하는 것도 anchor 없이 쓰는 유효한 출구다.

    @param t_expected_sec: 센서 위치 기준 예상 도달 시각 (pumping-elapsed).
      t_head 는 '아웃렛 밸브' 기준이므로 호출부가 (sensor→valve 부피)/F 를 빼서 넘긴다.
    """

    MODES = ("off", "observe", "anchor")
    MIN_BASE = 5      # ADC 베이스라인 확정에 필요한 최소 표본 수

    def __init__(self, engine, sensor, key, timer, t_expected_sec, window_sec,
                 mode="observe", adc_delta=0.0, confirm_n=3, poll=0.1,
                 valve_lag_sec=0.0, confirm_sec=1.0):
        super().__init__(daemon=True, name="HeadArrivalProbe")
        self.engine = engine
        self.sensor = sensor
        self.key = key
        self.timer = timer
        self.t_exp = float(t_expected_sec)
        self.w = float(window_sec)
        self.mode = str(mode or "off").lower()
        self.adc_delta = float(adc_delta or 0.0)
        self.confirm_n = max(1, int(confirm_n))
        self.poll = float(poll)
        self.valve_lag = float(valve_lag_sec or 0.0)
        # @codesyncer(2026-08-18, 적대검증 P0 수정): 지속성 확인 창 — 후보(상전이/
        #   ADC 스텝)가 이 시간 동안 유지돼야 발화. 기포는 지나가고 되돌아오므로
        #   탈락, 진짜 선단은 유지. 발화 시각은 '첫 후보 시각'으로 소급(지연 무보정
        #   문제 회피). 부피 환산: V = F × confirm_sec (0.48mL/min·1s ≈ 8µL).
        self.confirm_sec = max(0.0, float(confirm_sec))
        # P0: GAS 분류 배제용 — 이 키의 채널 임계(드라이버와 동일 판정 기준)
        _smap = getattr(sensor, "sensors", {}) or {}
        self._ch = _smap.get(self.key)
        try:
            _t = (getattr(sensor, "thresholds", {}) or {}).get(self._ch)
            self._thr = float(_t) if _t is not None else None
        except Exception:
            self._thr = None
        # 결과 (실측 리포트/트레이스용)
        self.detected_sec = None      # 센서에서 선단이 잡힌 pumping-elapsed
        self.delta_sec = None         # 실측 − 예상 (양수 = 예상보다 늦게 도달)
        self.detector = None          # "phase" | "adc"
        self.baseline_adc = None
        self.applied_shift = 0.0
        self.missed = False
        self.spurious = 0
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def _elapsed(self):
        try:
            return (self.timer._pumping_elapsed()
                    if self.timer is not None and self.timer.start_time is not None
                    else 0.0)
        except Exception:
            return 0.0

    def _paused(self):
        try:
            with self.timer._pause_lock:
                return self.timer._pause_start is not None
        except Exception:
            return False

    def _read_adc(self):
        try:
            v = self.sensor.analog(self.key)
            return None if v is None or v < 0 else float(v)
        except Exception:
            return None

    def run(self):
        if self.mode not in ("observe", "anchor"):
            return
        try:
            self.sensor.monitor(self.key, "always")
        except Exception as e:
            self.engine._log(f"  [HeadProbe] 모니터 무장 실패 — 타이밍 폴백: {e}")
            return

        t_open, t_close = self.t_exp - self.w, self.t_exp + self.w
        self.engine._log(
            f"  [HeadProbe] {self.mode} 무장 — 센서 '{self.key}' 예상 {self.t_exp:.1f}s "
            f"(창 ±{self.w:.0f}s), 검출기 "
            f"{'상전이+ADC(Δ≥' + format(self.adc_delta, '.0f') + ')' if self.adc_delta > 0 else '상전이'}")

        base_samples, hits, drained = [], 0, False
        hit_t0 = None          # ADC 후보 첫 히트 시각 — 소급 발화용
        pend = None            # 지속성 확인 중 후보: (kind, t_first, want_liquid)
        try:
            while not self._stop_evt.is_set():
                if getattr(self.engine, "abort_flag", False):
                    return
                if self._stop_evt.wait(self.poll):
                    return
                # @codesyncer-decision: 경과는 반드시 폴 대기 '뒤'에 읽는다. 대기 전에
                #   읽은 값을 검출 시각으로 쓰면 엣지가 대기 중에 도착했을 때 최대 poll
                #   만큼 과거로 기록되고, anchor 모드에선 그 오차가 그대로 재앵커된다.
                el = self._elapsed()
                if el > t_close and pend is None:
                    self.missed = True
                    self.engine._log(
                        f"  [HeadProbe] ⚠ 창 {self.t_exp:.1f}±{self.w:.0f}s 안에 선단 미검출 "
                        f"— 타이밍 그대로 사용(폴백). 시약에 광학 대비가 없거나 "
                        f"실제 도달이 창 밖일 수 있음")
                    return
                # 일시정지 중엔 유량이 없다 — 진행 중이던 후보도 신뢰 불가
                # (감압으로 계면이 왕복하는 구간 — P0 지속성 확인의 전제 붕괴)
                if self._paused():
                    if pend is not None or hits:
                        self.spurious += 1
                    pend, hits, hit_t0 = None, 0, None
                    continue

                adc = self._read_adc()
                # @codesyncer(P0-1/P0-2, 적대검증 공격 1·2 수정): GAS 로 분류되는
                #   표본(= 기포)은 베이스라인 축적과 ADC 검출 양쪽에서 배제한다.
                #   드라이버와 동일 판정(adc <= 채널 임계 = 기체). 기포가 평균을
                #   무너뜨리거나(공격1) 그 자체가 선단으로 오인되는(공격2) 경로 차단.
                #   ⚠ 임계 아래로 읽히는 진한 유색 시약은 이 검출기로 못 본다 —
                #   모드 A 의 문서화된 한계(배선메모 §유색), 임계 하향으로 대응.
                is_gas = (self._thr is not None and adc is not None
                          and adc <= self._thr)
                # 베이스라인 = 선단 이전(선행 용매)의 광학값. 창 열리기 전 구간에서
                # 모으는 게 정석이지만, 창이 이미 열린 채 프로브가 무장되는 경우
                # (window 가 t_exp 보다 커서 t_open<0)에도 반드시 잡혀야 하므로
                # '표본 MIN_BASE 개 확보'를 확정 조건으로 둔다.
                if self.baseline_adc is None:
                    if adc is not None and not is_gas:
                        base_samples.append(adc)
                        if len(base_samples) > 200:
                            base_samples.pop(0)
                    if el >= t_open and len(base_samples) >= self.MIN_BASE:
                        # P0-1: 평균 → 중앙값. 배제를 뚫고 남은 소수 오염에도 강건.
                        _srt = sorted(base_samples)
                        self.baseline_adc = float(_srt[len(_srt) // 2])
                if el < t_open:
                    continue

                # @codesyncer-decision: 창 진입 시 스테일 엣지 배수 — monitor 를 프로브
                #   시작(주입 시작)부터 걸어두므로 창 이전의 엣지(프라임·세척·기포)가
                #   드라이버 큐에 쌓인다. 그대로 두면 창 진입 첫 read_event 가 그 과거
                #   엣지를 반환해 '선단 도달'로 오인한다 — 창 게이트를 무력화하는 경로.
                #   RoboChem 은 대기 직전에 monitor 를 켜고 if_already_present 로 같은
                #   상황을 막는다. 여기선 큐를 비우고 '창 진입 이후 신선한 엣지'만 인정.
                if not drained:
                    drained = True
                    try:
                        for _ in range(100):
                            if self.sensor.read_event(self.key) is None:
                                break
                            self.spurious += 1
                    except Exception:
                        pass
                    if self.spurious:
                        self.engine._log(f"  [HeadProbe] 창 진입 전 스테일 엣지 "
                                         f"{self.spurious}건 폐기")
                    continue

                # ── 진행 중 후보의 지속성 확인 (P0-2/P0-3 공통, 적대검증 수정) ──
                # 후보 확정 후 confirm_sec 동안 같은 상태가 유지돼야 발화. 기포는
                # 지나가고 원상 복귀 → 여기서 탈락. 발화 시각은 t_first 로 소급.
                if pend is not None:
                    kind, t_first, want_liquid = pend
                    if kind == "phase":
                        try:
                            _sustained = ((self.sensor.read_phase(self.key) != "GAS")
                                          == want_liquid)
                        except Exception:
                            _sustained = False
                    else:                          # "adc"
                        _sustained = (adc is not None and not is_gas
                                      and self.baseline_adc is not None
                                      and abs(adc - self.baseline_adc) >= self.adc_delta)
                    if not _sustained:
                        self.spurious += 1         # 기포/채터 — 후보 기각
                        pend, hits, hit_t0 = None, 0, None
                        continue
                    if el - t_first >= self.confirm_sec:
                        if kind == "phase":
                            self._fire(t_first, "phase",
                                       f"상전이 {'G→L' if want_liquid else 'L→G'} "
                                       f"(지속 {self.confirm_sec:.1f}s 확인)")
                        else:
                            self._fire(t_first, "adc",
                                       f"ADC {self.baseline_adc:.0f}→{adc:.0f} "
                                       f"(지속 {self.confirm_sec:.1f}s 확인)")
                        return
                    continue

                # ① 상전이 엣지 — 즉시 발화 금지(P0-3), 지속성 후보로 등록
                try:
                    ev = self.sensor.read_event(self.key)
                except Exception as e:
                    self.engine._log(f"  [HeadProbe] ⚠ 센서 오류 — 프로브 종료"
                                     f"(런은 타이밍으로 계속): {e}")
                    return
                if ev is not None:
                    pend = ("phase", el, ev != "GAS")
                    continue

                # ② ADC 스텝 (유색 시약 선단) — GAS 배제(P0-2) + confirm_n 연속
                #    → 지속성 후보 등록 (첫 히트 시각 소급)
                if self.adc_delta > 0 and adc is not None and not is_gas \
                        and self.baseline_adc is not None:
                    if abs(adc - self.baseline_adc) >= self.adc_delta:
                        if hits == 0:
                            hit_t0 = el
                        hits += 1
                        if hits >= self.confirm_n:
                            pend = ("adc", hit_t0, None)
                    elif hits:
                        self.spurious += 1
                        hits, hit_t0 = 0, None
        finally:
            try:
                self.sensor.monitor(self.key, "never")
            except Exception:
                pass

    def _fire(self, el, detector, detail):
        self.detected_sec = el
        self.detector = detector
        self.delta_sec = el - self.t_exp
        msg = (f"  [HeadProbe] 선단 검출 @ {el:.1f}s ({detail}) — "
               f"예상 {self.t_exp:.1f}s 대비 Δ{self.delta_sec:+.1f}s")
        if self.mode == "anchor":
            try:
                self.timer.shift(self.delta_sec)
                self.applied_shift = self.delta_sec
                msg += " → 타이머 재앵커 적용"
            except Exception as e:
                msg += f" → 재앵커 실패({e}), 타이밍 폴백"
        else:
            msg += " → 관측만(observe), 제어 무영향"
        self.engine._log(msg)
        if abs(self.delta_sec) > 5.0:
            self.engine._log(
                f"  [HeadProbe] ⓘ 실측 HEAD(밸브 기준) ≈ "
                f"{el + self.valve_lag:.1f}s — 재현되면 "
                f"system_params.outlet_switch_delay_sec 에 고정 가능")

    def summary(self):
        if self.detected_sec is None:
            return "선단 미검출(타이밍 폴백)" if self.missed else "미실행"
        return (f"검출 {self.detected_sec:.1f}s / 예상 {self.t_exp:.1f}s / "
                f"Δ{self.delta_sec:+.1f}s / 검출기={self.detector} / "
                f"재앵커 {self.applied_shift:+.1f}s / 노이즈기각 {self.spurious}")


class MarkerCollectGate(threading.Thread):
    """N2 브래킷 마커 기반 분취 게이트 — RoboChem 슬러그-트리거 로직의 표준경로판.

    @codesyncer-decision(2026-08-18 사용자 확정): 흐름
        [용매][N2 전단마커][화합물 슬러그][N2 후단마커][push 용매]
      ① 전단마커 '꼬리'(G→L 복귀 = 화합물 선두)를 예상창에서 검출 →
         CollectionTimer.shift() 재앵커. 밸브를 직접 만지지 않고 클록만 옮긴다 —
         기존 검증된 집행 경로(HEAD 이벤트의 Outlet→COLLECT·preflush·웰 이동·
         waste_guard 전 기계) 재사용.
         · 조기 도달(Δ<0): HEAD 미발화 상태 → shift 로 클록이 점프해 HEAD 가
           즉시 발화 = 검출 순간 COLLECT (완전 센서 트리거).
         · 지연 도달(Δ>0): HEAD 는 이미 t_exp 에 발화(밸브 COLLECT 선개방 —
           선단 도착까지 Δ×F 만큼 선용매가 첫 웰에 들어감 = 희석, 유실 아님).
           shift 는 웰 경계·terminal WASTE 등 잔여 이벤트만 지연 보정.
           Δ>0 이 재현되면 t_head 모델(반응기 부피/컴플라이언스)을 재보정할 것.
      ② 후단마커 '머리'(L→G = 화합물 꼬리 통과)를 검출 → valve_lag 경과 후
         Outlet→WASTE 직접 전환 + 슬러그 통과시간(①~②) 실측 기록.
         타이머의 terminal WASTE 는 폴백/이중 안전으로 그대로 두어 뒤에 재발화
         (idempotent — 같은 위치 재설정).
    - 검출 실패(마커 미시인/유색 크루드 무대비)는 언제나 시간제 폴백: 아무 것도
      옮기지 않고 기존 타이머가 그대로 동작한다.
    - mode: "observe"=검출·기록만(제어 무영향) / "gate"=재앵커+후단 절단
    - P1 반영: ①조기(음수) 재앵커는 max_early_sec 클램프(분취기 이동 8.3s 보호)
      ②본 게이트 무장 시 호출부가 HeadArrivalProbe 를 강등한다 — read_event 는
      소비형 큐라 두 소비자가 서로의 엣지를 훔친다(소유권 단일화).
    - 후단 절단은 t_open2 = front + 0.5×slug_sec 이전엔 무장하지 않는다 —
      슬러그 초반의 잡기포가 수집을 반토막 내는 사고 방지.

    ── "parked"(정지선 발사) 스타일 (2026-08-19 사용자 확정) ────────────
    브래킷의 공류(co-flow) 주입은 세그멘터 효과로 마커가 쪼개질 수 있다
    (특히 후단 = Reaxus 맥동 캐리어). parked 는 마커를 '흐름이 없는 순간'에
    주입해 가스T 에 주차시킨다 — 전단 = 시약 장전 완료 후(전 펌프 정지),
    후단 = 주입종료~push 사이 무유량 갭. 흐름 재개 시 마커가 T+0 에 출발하고
    화합물 선두/꼬리는 주입경로(pre_sec) 뒤에 따라온다:
        화합물 경계(센서) = 마커 '꼬리' 에지(G→L) + offset_sec(=pre_sec)
      · 전단마커: 앞뒤가 모두 무색 용매 스페이서 → 유색 크루드여도 서명 선명
      · 후단마커: 주입경로에 남은 pre_sec 분량의 화합물이 마커 '뒤'에 따라오므로
        WASTE 절단은 반드시 꼬리에지+offset 후 — 안 그러면 그 분량이 유실된다.
      · 마커 인정 = 기체 지속 ≥ min_gas_sec (잡기포와 지속시간으로 구분 —
        2026-08-19 사용자 지시, 마커 2~3초 발사 전제)
      · 주차 주입이 밸브측 액체를 마커 부피만큼 선배출하지만 선두의 기하 경로
        (마개+본류)는 불변 — 선두 시각 무영향 (2026-08-20 정정: 구 '변위 조기화'
        서술은 오류). 실재하는 상수 편향은 **기포 슬립**(Taylor 액막 위로 기포가
        액체보다 ~10% 빨리 감) → '꼬리+pre' 판독이 선두를 이르게 예측(안전 방향,
        observe Δ 평균으로 실측해 상수 보정 or gate 재앵커가 흡수).

    @param t_exp_front_sec: 화합물 선두의 센서 도달 예상(펌핑경과) —
        호출부가 t_head(밸브 기준)에서 (sensor→valve)/F 를 뺀 값.
    @param slug_sec: 슬러그 통과 예상시간 = 도징 시간 (부피 보존: 일정 유량에서
        꼬리는 머리보다 정확히 도징 시간만큼 늦게 같은 지점을 지난다).
    @param style: "bracket"(밀착 발사 — 경계 에지 직독) / "parked"(정지선 발사
        — 마커꼬리 에지 + offset_sec 판독)
    @param offset_sec: parked 전용 — 마커꼬리→화합물 경계 오프셋 (= pre_sec)
    @param min_gas_sec: parked 전용 — 마커 인정 최소 기체 지속시간
    """

    MODES = ("off", "observe", "gate")
    # 표시 용어 (2026-08-24 사용자 확정): 위치 기준 전단센서=반응기 앞(INLET),
    # 후단센서=반응기 뒤(OUTLET). 내부 키(reactor_in/collect)는 불변 — 표시만.
    SENSOR_DISP = {"reactor_in": "전단센서", "collect": "후단센서"}

    def __init__(self, engine, sensor, key, timer, t_exp_front_sec, slug_sec,
                 window_sec, mode="observe", valve_lag_sec=0.0,
                 confirm_sec=1.0, max_early_sec=10.0, poll=0.1,
                 style="bracket", offset_sec=0.0, min_gas_sec=2.0,
                 rear_key=None, front_key=None, s1s2_transit_sec=0.0):
        super().__init__(daemon=True, name="MarkerCollectGate")
        self.engine = engine
        self.sensor = sensor
        self.key = key
        # @codesyncer-decision(2026-08-24 사용자 확정 — 전단도 센서1): 실런 2회에서
        #   전단 마커가 센서1에선 온전(기체 2.3s)했으나 반응기 코일 2.4mL 통과 중
        #   0.1s 파편 열차로 쪼개져 센서2 검출이 전부 기각됨(두 런 모두 244.7s 재현,
        #   모델 304.5s 대비 -59.8s = 기포 슬립 ~20%). front_key 로 전단 감시 센서를
        #   분리 — 센서1(reactor_in)이면 파편화·슬립 구간이 트리거 경로에서 빠진다.
        #   호출부 t_exp 도 해당 센서 기준으로 계산해 전달. 미지정 = key(기존 동작).
        self.front_key = str(front_key) if front_key else key
        # front 를 센서1에서 실측하면 front_el−offset 은 가스T→센서1(~수 초)이라
        #   후단 절단의 S2 수송 근거로 쓸 수 없다 → 호출부가 준 모델 수송(액체 기준,
        #   S1→S2 부피/F)으로 폴백. 방향은 '수 초~수십 초 늦은 절단'(기포 슬립분)
        #   = push 용매 소량 포함, 제품 유실 없음 (docstring 정책과 동일 방향).
        self.s1s2_transit = max(0.0, float(s1s2_transit_sec or 0.0))
        # parked 후단 1차 센서 (2026-08-19 사용자 확정): 후단 마커는 push(맥동)
        # 캐리어로 반응기를 5분간 통과하며 쪼개질 수 있으므로, 반응기 진입 '전' =
        # 센서1(가스T 직후)에서 갓 형성된 단일 플러그를 감지한다. 센서2는 크로스체크.
        self.rear_key = (str(rear_key) if rear_key else None) \
            if str(style).lower() == "parked" else None
        self.timer = timer
        self.t_exp = float(t_exp_front_sec)
        self.slug_sec = max(0.0, float(slug_sec))
        self.w = float(window_sec)
        self.mode = str(mode or "observe").lower()
        self.valve_lag = max(0.0, float(valve_lag_sec or 0.0))
        self.confirm_sec = max(0.0, float(confirm_sec))
        self.max_early = max(0.0, float(max_early_sec))
        self.poll = float(poll)
        self.style = str(style or "bracket").lower()
        self.offset = max(0.0, float(offset_sec or 0.0)) \
            if self.style == "parked" else 0.0
        self.min_gas = max(0.0, float(min_gas_sec or 0.0))
        # 결과 (결산/트레이스)
        self.front_el = None          # 화합물 선두 실측 (펌핑경과)
        self.rear_el = None           # 화합물 꼬리 실측
        self.slug_transit_sec = None  # ①~② 실측 간격
        self.applied_shift = 0.0
        self.marker_head_el = None    # 전단마커 진입(L→G) 관측 시각
        self.missed_front = False
        self.missed_rear = False
        self.rear_cut = False         # 후단 절단(WASTE) 실제 수행 여부
        self.spurious = 0
        # parked 센서1 기반 후단 (2026-08-19)
        self.rear_depart_el = None    # 후단 마커 발사 시점 (엔진이 통지)
        self._depart_shift = 0.0      # 통지 시점의 applied_shift 스냅샷
        self.transit_s2 = None        # 전단마커 실측 가스T→센서2 수송시간(듀레이션)
        self.r1_el = None             # 센서1 후단마커 꼬리 실측
        self.s2x_el = None            # 센서2 후단마커 크로스체크 실측
        self.rear_source = None       # "S1" | "S2크로스" | None
        self._r1_gas = None           # 센서1 기체 구간 머신
        self._r1_shift = 0.0
        self.cut_el = None            # 후단 절단 예약 시각 (S1/S2 확정 후)
        self._stop_evt = threading.Event()

    def note_rear_departed(self):
        """엔진 훅 — parked 후단 마커 발사 직후 호출. 창 추측 대신 발사 시점을
        직접 앵커로 쓴다 (타이머 pause 중이라 elapsed 는 도징종료 값으로 동결)."""
        try:
            self.rear_depart_el = self._elapsed()
            self._depart_shift = self.applied_shift
        except Exception:
            pass

    def _consume_rear_s1(self, el):
        """센서1(rear_key) 큐 소비 — 후단마커 조기 통과 대응.

        @codesyncer-decision(2026-08-19): 후단 마커는 가스T 직후의 센서1을
        '발사 수 초 뒤'에 지나간다 — 슬러그가 짧으면 전단이 센서2에 닿기도 전이다.
        read_event 큐에는 타임스탬프가 없어 늦게 소비하면 시각이 통째로 어긋나므로,
        전단 감시 루프에서도 매 폴마다 이 소비기를 돌려 실시각으로 기록한다."""
        if not self.rear_key:
            return
        # 전단을 같은 센서(센서1)에서 감시 중이면 전단 확정 전엔 큐를 건드리지
        # 않는다 — 드레인이 전단 마커 에지를 삼키는 이중 소비 방지 (2026-08-24).
        if self.rear_key == self.front_key and self.front_el is None:
            return
        try:
            if self.r1_el is not None or self.rear_depart_el is None:
                # 확보 완료 or 발사 전 — 큐만 비움 (스테일 방지)
                while self.sensor.read_event(self.rear_key) is not None:
                    pass
                self._r1_gas = None
                return
            while True:
                ev = self.sensor.read_event(self.rear_key)
                if ev is None:
                    return
                if ev == "GAS":
                    self._r1_gas = el
                    continue
                if self._r1_gas is None:
                    continue
                dur = el - self._r1_gas
                self._r1_gas = None
                if dur < self.min_gas:
                    self.spurious += 1
                    self.engine._log(
                        f"  [MarkerGate] 전단센서 잡기포 기각 @ {el:.1f}s "
                        f"(기체 {dur:.1f}s < {self.min_gas:.1f}s)")
                    continue
                self.r1_el = el
                self._r1_shift = self.applied_shift
                self.engine._log(
                    f"  [MarkerGate] 후단마커 전단센서 실측 @ {el:.1f}s "
                    f"(기체 {dur:.1f}s) — 반응기 진입 전 단일 플러그")
        except Exception:
            pass

    def stop(self):
        # 결산(stop)이 절단 예약보다 먼저 오는 지오메트리(수집 여유 < 절단
        # 바이어스): terminal WASTE 폴백이 절단을 이미 수행 — 실측만 기록.
        if (self.style == "parked" and self.cut_el is not None
                and self.rear_el is None):
            try:
                self.rear_el = self.cut_el - self.valve_lag
                if self.front_el is not None:
                    self.slug_transit_sec = (
                        self.rear_el - (self.front_el - self.applied_shift))
                self.rear_source = (self.rear_source or "S1") + "/terminal선행"
                self.engine._log(
                    f"  [MarkerGate] 후단 절단 예약({self.cut_el:.1f}s)보다 "
                    f"terminal WASTE 가 선행 — 절단은 시간제 폴백이 수행, 실측만 기록")
            except Exception:
                pass
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def _elapsed(self):
        try:
            return (self.timer._pumping_elapsed()
                    if self.timer is not None and self.timer.start_time is not None
                    else 0.0)
        except Exception:
            return 0.0

    def _paused(self):
        try:
            with self.timer._pause_lock:
                return self.timer._pause_start is not None
        except Exception:
            return False

    def _is_liquid(self, key=None):
        try:
            return self.sensor.read_phase(key or self.key) != "GAS"
        except Exception:
            return False

    def run(self):
        if self.mode not in ("observe", "gate"):
            return
        try:
            self.sensor.monitor(self.key, "always")
            if self.front_key != self.key:
                self.sensor.monitor(self.front_key, "always")
        except Exception as e:
            self.engine._log(f"  [MarkerGate] 모니터 무장 실패 — 시간제 폴백: {e}")
            return
        if self.rear_key:
            try:
                self.sensor.monitor(self.rear_key, "always")
            except Exception as e:
                self.engine._log(f"  [MarkerGate] 전단센서 무장 실패({e}) — "
                                 "후단은 후단센서 단독으로 진행")
                self.rear_key = None
        _st = (f", 오프셋 {self.offset:.1f}s, 마커 최소기체 {self.min_gas:.1f}s"
               + (f", 후단마커 1차={self.SENSOR_DISP.get(self.rear_key, self.rear_key)}"
                  if self.rear_key else "")
               if self.style == "parked" else "")
        self.engine._log(
            f"  [MarkerGate] {self.mode}/{self.style} 무장 — "
            f"전단마커 감시={self.SENSOR_DISP.get(self.front_key, self.front_key)}, "
            f"선두 예상 {self.t_exp:.1f}s (창 ±{self.w:.0f}s), "
            f"슬러그 {self.slug_sec:.1f}s, 조기클램프 {self.max_early:.0f}s{_st}")
        try:
            if not self._front_stage():
                return
            if self.style == "parked" and self.rear_key:
                self._rear_stage_parked_s1()
            else:
                self._rear_stage()
        finally:
            for k in (self.key, self.rear_key, self.front_key):
                if k:
                    try:
                        self.sensor.monitor(k, "never")
                    except Exception:
                        pass

    # ── ① 전단: 마커 꼬리(G→L) → 재앵커 ────────────────────────────
    #   bracket: 꼬리 에지 = 화합물 선두 그 자체 (밀착)
    #   parked : 꼬리 에지 + offset = 화합물 선두 (마커가 pre_sec 앞서감)
    #            + 마커 인정 = 기체 지속 ≥ min_gas (잡기포 기각)
    def _front_stage(self):
        t_c = self.t_exp - self.offset       # 검출 에지(마커 꼬리)의 예상 시각
        t_open, t_close = t_c - self.w, t_c + self.w
        drained = False
        pend = None                    # 후보 꼬리 시각 (액체 복귀 지속성 확인 중)
        gas_start = None               # parked: 진행 중인 기체 구간 시작
        while not self._stop_evt.is_set():
            if getattr(self.engine, "abort_flag", False):
                return False
            if self._stop_evt.wait(self.poll):
                return False
            el = self._elapsed()
            if el > t_close and pend is None:
                self.missed_front = True
                extra = (f" (마커 진입은 {self.marker_head_el:.1f}s 에 보임 — "
                         f"크루드 광학 대비/지속시간 미달 의심)"
                         if self.marker_head_el is not None else "")
                self.engine._log(
                    f"  [MarkerGate] ⚠ 선두 미검출 (에지 예상 {t_c:.1f}±{self.w:.0f}s)"
                    f"{extra} — 시간제 폴백")
                return False
            self._consume_rear_s1(el)      # 센서1 후단마커 조기 통과 실시각 기록
            if self._paused():
                if pend is not None or gas_start is not None:
                    self.spurious += 1
                pend = gas_start = None
                continue
            if el < t_open:
                continue
            if not drained:
                drained = True
                try:
                    for _ in range(100):
                        if self.sensor.read_event(self.front_key) is None:
                            break
                        self.spurious += 1
                except Exception:
                    pass
                continue
            if pend is not None:
                if not self._is_liquid(self.front_key):
                    self.spurious += 1       # 꼬리 직후 재기체 = 채터 — 기각
                    pend = None
                    continue
                if el - pend >= self.confirm_sec:
                    self._fire_front(pend + self.offset)
                    return True
                continue
            try:
                ev = self.sensor.read_event(self.front_key)
            except Exception as e:
                self.engine._log(f"  [MarkerGate] ⚠ 센서 오류 — 시간제 폴백: {e}")
                return False
            if ev is None:
                continue
            if ev == "GAS":
                gas_start = el
                if self.marker_head_el is None:
                    self.marker_head_el = el
                    self.engine._log(
                        f"  [MarkerGate] 마커 진입(L→G) @ {el:.1f}s — 꼬리 대기")
            else:                            # G→L (기체 → 액체 복귀)
                if self.style == "parked":
                    if gas_start is None:
                        continue             # 창 진입 전 시작된 기체 — 판정 불가
                    dur = el - gas_start
                    gas_start = None
                    if dur < self.min_gas:
                        self.spurious += 1   # 잡기포 — 지속시간 미달 기각
                        self.engine._log(
                            f"  [MarkerGate] 잡기포 기각 @ {el:.1f}s "
                            f"(기체 {dur:.1f}s < 마커 최소 {self.min_gas:.1f}s)")
                        continue
                    pend = el                # 마커 꼬리 확정 후보
                else:
                    pend = el                # bracket: 화합물 선두 후보
        return False

    def _fire_front(self, el):
        self.front_el = el
        delta = el - self.t_exp
        clamped = max(delta, -self.max_early)
        note = ""
        if clamped != delta:
            note = f" (조기 {delta:+.1f}s → 클램프 {clamped:+.1f}s — 분취기 이동 보호)"
        if self.mode == "gate":
            # @codesyncer-decision(2026-08-19 사용자 확정 — 2단 전환):
            #   마커 도달 '즉시' Outlet→COLLECT — 스페이서 용매가 수집 라인
            #   (0.25mL)을 선충전·선세척하고, 니들은 compensated WASH(폐기)
            #   좌표에서 수취. 웰 진입(니들 이동)은 재앵커된 타이머가 화합물
            #   도달 시각(+라인 18.7s 보정)에 수행. 타이머 HEAD 의 밸브 재발화는
            #   같은 위치 재설정이라 무해(idempotent).
            _pre_sw = ""
            # 센서1 전단(2026-08-24): 검출 시점이 도달보다 반응기 수송(~5분)만큼
            # 앞선다 — 도달 임박(60s 이내)일 때만 선전환하고, 원거리 검출은
            # 재앵커된 타이머의 선헹굼/HEAD 이벤트에 밸브를 위임한다.
            try:
                if (el - self._elapsed()) <= 60.0:
                    if self.engine._outlet_set_safe(2, "MarkerGate 마커도달 선전환"):
                        _pre_sw = " | Outlet→COLLECT 선전환(라인 선충전, 니들=폐기좌표)"
            except Exception:
                pass
            try:
                self.timer.shift(clamped)
                self.applied_shift = clamped
                act = f"→ 타이머 재앵커 {clamped:+.1f}s{note}{_pre_sw}"
            except Exception as e:
                act = f"→ 재앵커 실패({e}), 시간제 폴백{_pre_sw}"
        else:
            act = f"→ 관측만(observe), Δ{delta:+.1f}s 기록"
        if self.offset > 0:      # parked: el 은 '도달 예측'(마커꼬리+pre) — 문구 구분
            _what = (f"① 선두 도달 예측 {el:.1f}s "
                     f"(마커꼬리 {el - self.offset:.1f} + pre {self.offset:.1f})")
        else:                    # bracket: 에지 = 선두 그 자체 (실측)
            _what = f"① 화합물 선두 실측 @ {el:.1f}s"
        self.engine._log(
            f"  [MarkerGate] {_what} "
            f"(모델 예상 {self.t_exp:.1f}s, Δ{delta:+.1f}s) {act}")
        try:
            self.engine.trace.instant("MarkerGate", "front", args={
                "el": round(el, 1), "delta": round(delta, 1),
                "shift": round(self.applied_shift, 1)})
        except Exception:
            pass

    # ── ②' parked 후단 (센서1 1차, 2026-08-19 사용자 확정) ──────────
    #   절단 시각 = 센서1 마커꼬리(실측) + pre_sec(원장) + 가스T→센서2
    #   수송시간(전단마커 실측) + valve_lag — 반응기 모델 불사용.
    #   센서2는 크로스체크(Δ 로그) 병행, 센서1 실패 시 승격, 최후 terminal 폴백.
    #   (가스T→센서1 소구간은 무시 = 수 초 늦은 절단 → push 용매 소량 포함, 유실 없음)
    def _rear_stage_parked_s1(self):
        _anchor = self.front_el - self.applied_shift
        if self.front_key != self.key and self.s1s2_transit > 0:
            # 전단을 센서1에서 실측(2026-08-24) — front_el−offset 은 가스T→센서1
            # (~수 초)이라 S2 수송 근거가 못 됨 → 모델 수송(S1→S2 부피/F, 액체
            # 기준)으로 대체. 기포 슬립분(~20%)만큼 늦은 절단 = push 용매 소량
            # 포함 방향이라 제품 유실 없음.
            self.transit_s2 = self.s1s2_transit
        else:
            self.transit_s2 = self.front_el - self.offset   # 전단: 출발 el=0 → 꼬리 el=수송
        # 전단이 센서1 실측이면 anchor 는 S1 프레임 — 센서2 크로스체크/백업 창은
        # S1→S2 수송을 더해 S2 프레임으로 환산 (안 하면 창이 ~수송시간만큼 일찍
        # 열려 수집 중 잡슬러그가 '백업 승격'으로 조기 절단할 수 있음, 2026-08-24).
        _s2f = (self.s1s2_transit
                if (self.front_key != self.key and self.s1s2_transit > 0) else 0.0)
        t_exp2 = _anchor + _s2f + self.slug_sec
        t_c2 = t_exp2 - self.offset
        t_open2 = max(0.0, _anchor + _s2f + 0.5 * self.slug_sec - self.offset)
        t_close2 = t_c2 + self.w
        self.cut_el = None
        gas2 = None
        while not self._stop_evt.is_set():
            if getattr(self.engine, "abort_flag", False):
                return
            if self._stop_evt.wait(self.poll):
                return
            el = self._elapsed()
            self._consume_rear_s1(el)
            if self.cut_el is None and self.r1_el is not None:
                r1_cur = self.r1_el - (self.applied_shift - self._r1_shift)
                self.cut_el = (r1_cur + self.offset + self.transit_s2 + self.valve_lag)
                self.rear_source = "S1"
                self.engine._log(
                    f"  [MarkerGate] 후단 절단 예약 @ {self.cut_el:.1f}s = "
                    f"전단센서 {self.r1_el:.1f} + pre {self.offset:.1f} + "
                    f"전단실측수송 {self.transit_s2:.1f} + 밸브 {self.valve_lag:.1f}")
            if self._paused():
                gas2 = None
                continue
            # 센서2 크로스체크 / (센서1 실패 시) 백업 승격
            if el >= t_open2:
                try:
                    while True:
                        ev = self.sensor.read_event(self.key)
                        if ev is None:
                            break
                        if self.s2x_el is not None:
                            continue                     # 이미 확보 — 드레인만
                        if ev == "GAS":
                            gas2 = el
                            continue
                        if gas2 is None:
                            continue
                        dur = el - gas2
                        gas2 = None
                        if dur < self.min_gas:
                            self.spurious += 1
                            continue
                        self.s2x_el = el
                        if self.cut_el is not None:
                            _d = (el + self.offset + self.valve_lag) - self.cut_el
                            self.engine._log(
                                f"  [MarkerGate] 후단센서 크로스체크 @ {el:.1f}s — "
                                f"S1 계산 대비 Δ{_d:+.1f}s"
                                + (" ⚠ 재현 시 수송/오프셋 재보정"
                                   if abs(_d) > 5.0 else ""))
                        else:
                            self.cut_el = el + self.offset + self.valve_lag
                            self.rear_source = "S2크로스"
                            self.engine._log(
                                f"  [MarkerGate] 전단센서 미확보 — 후단센서 백업 승격, "
                                f"절단 예약 @ {self.cut_el:.1f}s")
                except Exception as e:
                    self.engine._log(f"  [MarkerGate] ⚠ 센서 오류 — terminal 폴백: {e}")
                    return
            # terminal WASTE 가 먼저 끝난 경우 — 절단은 시간제 폴백이 이미 수행.
            # (수집 여유가 절단 바이어스보다 짧은 지오메트리 — 기록만 남기고 종료)
            if (self.cut_el is not None and el < self.cut_el
                    and not self.timer.is_alive()):
                self.rear_el = self.cut_el - self.valve_lag
                self.slug_transit_sec = self.rear_el - _anchor
                self.rear_source = (self.rear_source or "S1") + "/terminal선행"
                self.engine._log(
                    f"  [MarkerGate] 후단 절단 예약({self.cut_el:.1f}s)보다 terminal "
                    f"WASTE 가 선행 종료 — 절단은 시간제 폴백이 수행, 실측만 기록")
                return
            # 절단 실행 (관측 모드는 기록만)
            if self.cut_el is not None and el >= self.cut_el:
                self.rear_el = self.cut_el - self.valve_lag
                self.slug_transit_sec = self.rear_el - _anchor
                try:
                    self.engine.trace.instant("MarkerGate", "rear", args={
                        "el": round(self.rear_el, 1), "source": self.rear_source,
                        "transit": round(self.slug_transit_sec, 1)})
                except Exception:
                    pass
                if self.mode == "gate":
                    try:
                        if self.engine._outlet_set_safe(1, "MarkerGate rear cut"):
                            self.rear_cut = True
                            self.engine._log(
                                f"  [MarkerGate] Outlet→WASTE (후단 절단, {self.rear_source} "
                                f"기반) — terminal WASTE 는 폴백 유지")
                    except Exception as e:
                        self.engine._log(f"  [MarkerGate] ⚠ 절단 실패 — terminal 폴백: {e}")
                else:
                    self.engine._log(
                        f"  [MarkerGate] ② 화합물 꼬리 실측 @ {self.rear_el:.1f}s "
                        f"({self.rear_source}) — 슬러그 통과 {self.slug_transit_sec:.1f}s "
                        f"(도징 {self.slug_sec:.1f}s 대비 "
                        f"Δ{self.slug_transit_sec - self.slug_sec:+.1f}s) — 관측만")
                return
            if self.cut_el is None and el > t_close2:
                self.missed_rear = True
                self.engine._log(
                    f"  [MarkerGate] ⚠ 후단 미검출 (전단·후단센서 모두, 에지 예상 "
                    f"{t_c2:.1f}±{self.w:.0f}s) — terminal WASTE 시간제 폴백")
                return
        return

    # ── ② 후단: 마커 머리(L→G) = 화합물 꼬리 → WASTE 절단 ──────────
    def _rear_stage(self):
        # @codesyncer(검증 2026-08-18, 모의 E2E 에서 발견): front_el 은 shift '이전'
        #   클록의 값 — gate 모드에서 shift(Δ) 적용 후 _elapsed() 는 Δ만큼 이동한
        #   클록을 읽으므로, 후단 앵커는 신클록으로 환산해야 한다:
        #   front(신클록) = front_el − applied_shift. 이걸 빼먹으면 후단 창이
        #   재앵커량만큼 늦게 열려 꼬리를 놓친다(관측된 결함).
        _anchor = self.front_el - self.applied_shift
        t_exp2 = _anchor + self.slug_sec                # 화합물 꼬리(경계) 예상
        t_c2 = t_exp2 - self.offset                     # 검출 에지 예상 시각
        t_open2 = _anchor + 0.5 * self.slug_sec - self.offset  # 반토막 절단 금지 가드
        t_close2 = t_c2 + self.w
        pend = None
        gas_start = None
        while not self._stop_evt.is_set():
            if getattr(self.engine, "abort_flag", False):
                return
            if self._stop_evt.wait(self.poll):
                return
            el = self._elapsed()
            if el > t_close2 and pend is None:
                self.missed_rear = True
                self.engine._log(
                    f"  [MarkerGate] ⚠ 꼬리 미검출 (에지 예상 {t_c2:.1f}±{self.w:.0f}s) "
                    f"— terminal WASTE 시간제 폴백")
                return
            if self._paused():
                if pend is not None or gas_start is not None:
                    self.spurious += 1
                pend = gas_start = None
                continue
            if el < t_open2:
                # 이 구간의 엣지는 소비만 하고 버린다 (스테일 방지)
                try:
                    if self.sensor.read_event(self.key) is not None:
                        self.spurious += 1
                except Exception:
                    pass
                continue
            if pend is not None:
                if self.style == "parked":
                    _ok = self._is_liquid()          # 꼬리 뒤 = push 용매 (액체 유지)
                else:
                    _ok = not self._is_liquid()      # bracket: 기체 지속이 확정 조건
                if not _ok:
                    self.spurious += 1
                    pend = None
                    continue
                if el - pend >= self.confirm_sec:
                    self._fire_rear(pend + self.offset)
                    return
                continue
            try:
                ev = self.sensor.read_event(self.key)
            except Exception as e:
                self.engine._log(f"  [MarkerGate] ⚠ 센서 오류 — terminal 폴백: {e}")
                return
            if ev is None:
                continue
            if self.style == "parked":
                if ev == "GAS":
                    gas_start = el
                else:                        # G→L = 마커 꼬리 후보 (지속시간 검증)
                    if gas_start is None:
                        continue
                    dur = el - gas_start
                    gas_start = None
                    if dur < self.min_gas:
                        self.spurious += 1
                        self.engine._log(
                            f"  [MarkerGate] 잡기포 기각 @ {el:.1f}s "
                            f"(기체 {dur:.1f}s < 마커 최소 {self.min_gas:.1f}s)")
                        continue
                    pend = el
            elif ev == "GAS":
                pend = el                    # bracket: L→G = 화합물 꼬리 후보
        return

    def _fire_rear(self, el):
        self.rear_el = el
        # 통과시간은 동일 클록끼리: front(신클록) = front_el − applied_shift
        self.slug_transit_sec = el - (self.front_el - self.applied_shift)
        self.engine._log(
            f"  [MarkerGate] ② 화합물 꼬리 실측 @ {el:.1f}s — 슬러그 통과 "
            f"{self.slug_transit_sec:.1f}s (도징 {self.slug_sec:.1f}s 대비 "
            f"Δ{self.slug_transit_sec - self.slug_sec:+.1f}s)")
        try:
            self.engine.trace.instant("MarkerGate", "rear", args={
                "el": round(el, 1),
                "transit": round(self.slug_transit_sec, 1)})
        except Exception:
            pass
        if self.mode != "gate":
            return
        # 꼬리가 '밸브'에 닿는 시각까지 대기 (sensor→valve 이송) 후 절단
        while not self._stop_evt.is_set():
            if getattr(self.engine, "abort_flag", False):
                return
            if self._elapsed() >= el + self.valve_lag:
                break
            if self._stop_evt.wait(self.poll):
                return
        try:
            if self.engine._outlet_set_safe(1, "MarkerGate rear cut"):
                self.rear_cut = True
                self.engine._log(
                    f"  [MarkerGate] Outlet→WASTE (후단 절단, 밸브지연 "
                    f"{self.valve_lag:.1f}s 반영) — terminal WASTE 는 폴백 유지")
        except Exception as e:
            self.engine._log(f"  [MarkerGate] ⚠ 후단 절단 실패 — terminal 폴백: {e}")

    def summary(self):
        if self.front_el is None:
            return ("선두 미검출(시간제 폴백)" if self.missed_front else "미실행")
        s = (f"선두 {self.front_el:.1f}s(Δ{self.front_el - self.t_exp:+.1f}s, "
             f"재앵커 {self.applied_shift:+.1f}s)")
        if self.rear_el is not None:
            s += (f" / 꼬리 {self.rear_el:.1f}s / 슬러그 {self.slug_transit_sec:.1f}s"
                  f" / 절단 {'수행' if self.rear_cut else '관측만'}")
        elif self.missed_rear:
            s += " / 꼬리 미검출(terminal 폴백)"
        s += f" / 노이즈기각 {self.spurious}"
        return s


class StrictSequenceEngine(FlowEngine):
    """Time-driven strict sequence engine for real hardware execution."""

    VALID_MODE = {"off", "first_step", "port_change", "every_step"}

    def __init__(self, config, pumps, valves, heater, safety_mgr, signals, collector=None, push_pump=None,
                 samplers=None, mfc=None, phase_sensor=None, level_sensor=None):
        super().__init__(config, pumps, valves, heater, safety_mgr)
        self.signals = signals
        self.collector = collector
        # @codesyncer-decision: push_pump는 optional. None이면 legacy syringe push 경로로 폴백.
        #   - push_pump 활성 시: Step 4.5b solvent refill 제거, Step 5 push는 HPLC가 담당
        #   - push_vol = 1.1 × reactor_vol (라인 세척 10% 여유 포함)
        self.push_pump = push_pump
        # HTE droplet 모드: 질소 스페이서용 MFC (hardware/gas/mfc_korea_mkp)
        self.mfc = mfc
        # 위상센서(OCB350) — 하이브리드 트리거(선택). None 이면 순수 타이밍.
        self.phase_sensor = phase_sensor
        # @codesyncer(2026-08-18, 사용자 요청): OPB 상전이(0↔1)를 시스템 로그에
        #   포함 — 버그 리포트에서 타이머/밸브/분취 사건과 센서 사건을 같은
        #   타임라인(CSV Time_s·[T+]·Perfetto)으로 대조하기 위함. 드라이버
        #   debounce 확정(50ms 표본×2) 기준이라 1Hz 대시보드 폴보다 정밀.
        if phase_sensor is not None:
            try:
                phase_sensor.on_transition = self._log_phase_transition
            except Exception:
                pass
        # 초음파 레벨센서(HC-SR04) — startup 잔량 실측/퍼지(선택). None 이면 기존 '가정 empty'.
        self.level_sensor = level_sensor
        self._hte_sensor_sync = None
        self.tab_collection = None

        # @codesyncer-decision: 오토샘플러 조율 (Phase C v0) — samplers 는
        #   hw_manager 가 해석한 {pump_name: sampler_obj}. 니들=NRG 저장조 라인
        #   토폴로지이므로 조율은 withdraw(리필) 구간에만 개입하고, 주입/분취
        #   타이머 구간에는 절대 개입하지 않는다 (니들 이동은 타이머 밖 원칙).
        from engine.sampler_coordinator import SamplerCoordinator
        self.sampler_by_pump = dict(samplers or {})
        self._sampler_coords = {
            p: SamplerCoordinator(s, signals=signals, group_name=p)
            for p, s in self.sampler_by_pump.items()
        }

        self.pause_event = threading.Event()
        self.pause_event.set()
        self.abort_flag = False
        self._cleanup_done = False
        # @codesyncer-decision: run_sequence 재진입 방지 플래그
        #   UI 더블클릭/원격명령/단축키 등 어떤 경로로도 두 번째 호출이 들어오면
        #   즉시 무시한다. 두 워커가 동시에 같은 펌프를 조작하면 RS-485 버스 경쟁
        #   + is_refilling 플래그 race condition으로 시린지 부피가 꼬임.
        self._running = False
        self._run_lock = threading.Lock()
        self._collection_timer: Optional[CollectionTimer] = None
        # @codesyncer-decision: injection 시작 기준 경과 타이머 — _log()가 모든 메시지에
        #   [T+MM:SS] prefix를 자동 부착하여 step 내부 타이밍 추적이 쉬워짐.
        #   None이면 prefix 미부착 (시퀀스 시작 전/cleanup 후 상태).
        self.injection_start_ts: Optional[float] = None

        sp = config.config_data.get("system_params", {}) if hasattr(config, "config_data") else {}
        self.vol_reactor = float(getattr(config, "reactor_vol", 0.0))
        self.vol_post_common = float(sp.get("post_reactor_vol_ml", 2.0))
        self.vol_collection = float(sp.get("collection_line_vol_ml", 1.0))
        self.temp_tolerance = float(sp.get("temp_tolerance_c", 0.3))
        self.heater_reach_timeout_sec = float(
            sp.get("heater_reach_timeout_sec", getattr(config, "heater_reach_timeout_sec", 900.0))
        )
        self.max_total_flow = float(sp.get("max_total_flow_ml_min", 100.0))
        self.max_step_volume_ml = float(sp.get("max_step_volume_ml", 500.0))
        # @codesyncer-decision: reagent 라인 잔량 배출 재시도 상한 — 초과 시 클로그/스톨
        #   의심으로 SafetyError(빈 채로 미는 상황 사전 차단). 최소 1 강제(0/음수 무효화 방지).
        self.level_purge_max_iter = max(1, int(sp.get("level_purge_max_iter", 3)))

        # @codesyncer-decision(잔량생성 fix, 2026-07-28): 도징 종료 자동정지 유예(초).
        #   Chemyx 는 set volume 소진 시 스스로 정지(부피 구동)하는데 창은 여유 0이라,
        #   순차 트리거/RS-485 왕복만큼 늦게 출발한 펌프를 시간 만료 즉시 stop 하면
        #   매 스텝 rate×지연 만큼 미토출이 시린지에 누적된다. 0 = 기존 동작(즉시 stop).
        #   기본 0(옵트인) — 자동정지를 모델하지 않는 시뮬(동적 회귀 SimPump 류)에서
        #   풀타임 대기로 타이밍 검증이 깨지는 것 방지. 실기는 hardware_config.json
        #   system_params.dosing_autostop_grace_sec=6.0 으로 활성(2026-07-28 등록).
        try:
            self.dosing_autostop_grace_sec = max(
                0.0, float(sp.get("dosing_autostop_grace_sec", 0.0) or 0.0))
        except (TypeError, ValueError):
            self.dosing_autostop_grace_sec = 0.0

        # @codesyncer-decision(잔량제거, 2026-07-28): 센서 게이트 지점별 action.
        #   off=무동작 | log=측정 기록만 | warn=gate 초과 시 경고+리포트 플래그 |
        #   purge=폐액 배출 반복→empty 검증(실패 시 SafetyError).
        #   ③post_inject/④push_end 는 실기 신뢰 축적 전까지 warn 기본(자동 배출이
        #   화학/타이밍에 개입하는 것 방지), ⑤seq_end 는 cleanup 이 abort 경로 공용이라
        #   호출부에서 log 로 강제 강등된다. 오설정 값은 안전측 log 로.
        _lvp = sp.get("level_verify_points", {}) or {}
        # push_end/post_inject 기본 purge (2026-07-29): 유예 후 잔량 = 센서 폐루프로
        # 0까지 리액터 방향 추가 토출. post_inject purge 는 기존 post-inject prime
        # (잔량을 리액터로 = 늦게라도 전달)의 센서판 — 용매 리필 혼입 차단.
        _lvp_defaults = {"wash": "purge", "post_inject": "purge",
                         "push_end": "purge", "seq_end": "log"}
        self.level_verify_points = {}
        for _k, _dv in _lvp_defaults.items():
            _v = str(_lvp.get(_k, _dv) or _dv).strip().lower()
            self.level_verify_points[_k] = _v if _v in ("off", "log", "warn", "purge") else "log"

        # @codesyncer-decision(P1 타이머 레인, 2026-07-28): 분취 타이머 튜닝 노브.
        #   lead: collector 이동을 경계보다 미리 발화(도착=경계 정렬) — 임의값 시드
        #   후 '지각' 로그로 수렴. guard: 이동 중 Outlet→WASTE(move-while-waste,
        #   상용 분취기 관례) — 경계 스미어를 웰 오염 대신 소량 waste 손실로 치환.
        #   둘 다 기본 비활성(0/false) = 기존 동작.
        try:
            self.collector_move_lead_sec = max(
                0.0, float(sp.get("collector_move_lead_sec", 0.0) or 0.0))
        except (TypeError, ValueError):
            self.collector_move_lead_sec = 0.0
        self.collector_move_waste_guard = bool(sp.get("collector_move_waste_guard", False))

        # @codesyncer-decision(P1.5 수집라인 매핑, 2026-07-28): collect_line_mode.
        #   legacy      — 기존 동작 그대로 (니들 이벤트 = 밸브 시각 기준).
        #   compensated — 니들(웰) 이벤트를 수집라인 통과 지연 Δ=vol_collection/F 만큼
        #     시프트 + 평시/초기 라인 내용물은 WASH 좌표에서 수취 + HPLC 분기에도
        #     라인 flush 부여 + 세척 배출 웰 소모 제거. 수락 기준/결함 정량은
        #     test_collect_line_mapping.py (legacy: 제품 회수 50~65%·웰1~2 용매/stale·
        #     꼬리 좌초 순환오염 — 실측 '타이밍 심각' 오차의 몸통 후보).
        #   기본 legacy — 분획 매핑이 바뀌는 동작 변경이라 실기 검증 후 승격.
        _clm = str(sp.get("collect_line_mode", "legacy") or "legacy").strip().lower()
        self.collect_line_mode = _clm if _clm in ("legacy", "compensated") else "legacy"

        self.wash_mode = self._normalize_mode(sp.get("wash_mode", "port_change"), "port_change")
        self.prefill_mode = self._normalize_mode(sp.get("prefill_mode", "port_change"), "port_change")

        # Legacy bool compatibility for older JSONs
        if bool(sp.get("wash_every_step", False)):
            self.wash_mode = "every_step"
        if bool(sp.get("prefill_every_step", False)) or bool(sp.get("prefill_each_step", False)):
            self.prefill_mode = "every_step"

        self.collector_start_tube = 1
        self.current_tube = 1

    # ---------------------------------------------------------------------
    # Utility
    # ---------------------------------------------------------------------
    def _normalize_mode(self, mode, fallback: str) -> str:
        value = str(mode).strip().lower()
        if value not in self.VALID_MODE:
            return fallback
        return value

    def _should_run_mode(self, mode: str, step_idx: int, ports_changed: bool) -> bool:
        if mode == "off":
            return False
        if mode == "every_step":
            return True
        if mode == "first_step":
            return step_idx == 0
        if mode == "port_change":
            return ports_changed or step_idx == 0
        return False

    def _ports_changed(self, prev_ports: Optional[Dict[str, int]], curr_ports: Dict[str, int]) -> bool:
        if prev_ports is None:
            return True
        all_keys = set(prev_ports.keys()) | set(curr_ports.keys())
        for key in all_keys:
            if int(prev_ports.get(key, 1)) != int(curr_ports.get(key, 1)):
                return True
        return False

    def _emit_status(self, msg: str):
        if self.signals:
            self.signals.sig_status.emit(msg)

    def _emit_phase(self, name: str, pct: float):
        # 트레이스: 국면 이름이 바뀔 때 PHASE 트랙의 스팬을 전환 (UI 신호에 무영향)
        prev = getattr(self, "_trace_phase_name", None)
        if name != prev:
            if prev is not None:
                self.trace.end("PHASE")
            self.trace.begin("PHASE", name)
            self._trace_phase_name = name
        if self.signals:
            self.signals.sig_phase_progress.emit(name, pct)

    def _check_abort(self):
        if self.abort_flag:
            raise SafetyError("Sequence aborted by user")

    def _wait_pause_or_abort(self, context: str):
        self._check_abort()
        if not self.pause_event.is_set():
            self._emit_status(f"Paused ({context})")
            self.pause_event.wait()
        self._check_abort()

    def _pump_routing(self, p_name: str) -> str:
        """config 가 유도한 라우팅 모드. 미지정/구버전 config 는 external_valve 로 간주
        (기존 Chemyx 동작과 완전 동일 — 하위호환)."""
        return getattr(self.cfg, "PUMP_ROUTING", {}).get(p_name, "external_valve")

    def _abort_refill_workers(self, pump_names):
        for p_name in pump_names:
            pump = self.pumps.get(p_name)
            if hasattr(pump, "_abort_refill"):
                pump._abort_refill = True

    def _emergency_stop_all(self, reason="Unknown"):
        """모든 펌프 긴급 정지 — 펌프 미동작/파라미터 불일치 등 치명적 에러 시"""
        self._log(f"🚨 EMERGENCY STOP: {reason}")
        for p_name, pump in self.pumps.items():
            try:
                pump.stop()
                if hasattr(pump, '_abort_refill'):
                    pump._abort_refill = True
                self._log(f"  [{p_name}] stopped")
            except Exception as e:
                self._log(f"  [{p_name}] stop failed: {e}")
        if self.push_pump is not None:
            try:
                self.push_pump.stop()
                self._log("  [PushPump] stopped")
            except Exception as e:
                self._log(f"  [PushPump] stop failed: {e}")

    def _sequential_trigger(self, pump_names, trigger_fn_name, interval=0.35):
        """순차 trigger: 각 펌프에 start 명령을 interval 간격으로 전송
        - RS-485 버스 안정성 확보 (9600bps 기준 0.35s면 충분한 마진)
        - 첫 펌프는 즉시, 이후 펌프부터 interval 대기
        """
        for i, p_name in enumerate(pump_names):
            if i > 0:
                time.sleep(interval)
            pump = self.pumps[p_name]
            getattr(pump, trigger_fn_name)()

    def _run_complete_threads(self, pump_names, complete_fn_name, phase_name,
                              log_prefix="", extra_names=None):
        """complete 스레드 실행 + Phase 3 모니터링 + Emergency Stop 인터락
        @param pump_names: 펌프 이름 리스트
        @param complete_fn_name: complete 메서드 이름 (예: "refill_complete")
        @param phase_name: 로그용 단계 이름
        @param log_prefix: 상태 로그 접두사
        @param extra_names: 로그 모니터링할 추가 펌프 이름 (기본: pump_names)
        @raises RuntimeError: 펌프 미동작/파라미터 실패 → Emergency Stop 후 re-raise
        """
        log_names = extra_names if extra_names is not None else pump_names
        # @codesyncer(검증 2026-08-11): 스팬을 try/finally 로 보장 — RuntimeError
        #   (Emergency Stop 인터락)·abort 경로에서 end 누락 시 사고 트레이스의
        #   실패 구간이 런 끝까지 이어진 것처럼 렌더링되던 결함 수정 (동작 불변 추출).
        self.trace.begin("PUMP OPS", phase_name, args={"pumps": list(pump_names)})
        try:
            self._run_complete_threads_impl(
                pump_names, complete_fn_name, phase_name, log_prefix, log_names)
        finally:
            self.trace.end("PUMP OPS")

    def _run_complete_threads_impl(self, pump_names, complete_fn_name, phase_name,
                                   log_prefix, log_names):
        errors = []  # (pump_name, exception) 저장용

        def _safe_complete(p_name):
            try:
                pump = self.pumps[p_name]
                getattr(pump, complete_fn_name)()
            except Exception as e:
                errors.append((p_name, e))

        complete_threads = []
        for p_name in pump_names:
            t = threading.Thread(target=_safe_complete, args=(p_name,), daemon=True)
            t.start()
            complete_threads.append(t)

        prev_log = {}
        for t in complete_threads:
            while t.is_alive():
                # 에러 감지 시 즉시 Emergency Stop
                if errors:
                    err_name, err = errors[0]
                    self._emergency_stop_all(f"{err_name}: {err}")
                    # 나머지 스레드 종료 대기
                    for t2 in complete_threads:
                        t2.join(timeout=3.0)
                    raise RuntimeError(f"Emergency Stop: {err_name} — {err}")

                self._wait_pause_or_abort(phase_name)
                for p_name in log_names:
                    pump = self.pumps.get(p_name)
                    st = getattr(pump, 'status', '')
                    if st and st != prev_log.get(p_name):
                        self._log(f"  [{p_name}] {log_prefix}{st}")
                        prev_log[p_name] = st
                t.join(timeout=0.5)

        # 스레드 종료 후 최종 에러 체크
        if errors:
            err_name, err = errors[0]
            self._emergency_stop_all(f"{err_name}: {err}")
            raise RuntimeError(f"Emergency Stop: {err_name} — {err}")

        # 최종 상태 로그
        for p_name in log_names:
            pump = self.pumps.get(p_name)
            st = getattr(pump, 'status', '')
            if st and st != prev_log.get(p_name):
                self._log(f"  [{p_name}] {log_prefix}{st}")

    def _max_collector_tubes(self) -> int:
        if self.collector and hasattr(self.collector, "total_tubes"):
            try:
                return int(getattr(self.collector, "total_tubes"))
            except Exception:
                pass
        return 88

    def _fraction_settings(self) -> Dict[str, float]:
        result = {"enabled": True, "volume": 0.0}
        if self.tab_collection and hasattr(self.tab_collection, "get_fraction_settings"):
            try:
                loaded = self.tab_collection.get_fraction_settings() or {}
                result.update(loaded)
            except Exception:
                pass
        return result

    def _validate_step_inputs(self, exp_id: int, exp: dict, frac_settings: Dict[str, float]):
        temp = float(exp.get("temp", 25.0))
        target_vol = float(exp.get("vol_ml", 0.0))
        tube_vol = float(exp.get("collect_volume_per_tube", 1.5))

        # @codesyncer-decision: NaN/inf 가드 (파라미터 경계 결함 fix #1)
        # - NaN은 모든 비교(<, >, <=)가 False라 기존 범위 검증을 전부 우회함
        #   → temp=NaN이면 가열 루프 무한 대기(타임아웃까지 행),
        #     vol/flow=NaN이면 inject_sec=NaN → 타이머/도징 전체 붕괴
        for label, value in (("temperature", temp), ("target volume", target_vol),
                             ("tube volume", tube_vol)):
            if not math.isfinite(value):
                raise SafetyError(f"Step {exp_id}: {label} is not a finite number ({value})")

        if target_vol <= 0:
            raise SafetyError(f"Step {exp_id}: target volume must be > 0")
        if target_vol > self.max_step_volume_ml:
            raise SafetyError(
                f"Step {exp_id}: target volume {target_vol:.3f} mL exceeds max_step_volume_ml {self.max_step_volume_ml:.3f}"
            )
        if tube_vol <= 0:
            raise SafetyError(f"Step {exp_id}: collect volume per tube must be > 0")

        # Plate96 well 용량 상한 (있을 때만) — 분주 시 오버플로 방지
        max_per_well = getattr(self.collector, "max_volume_per_well_ml", None)
        if max_per_well and tube_vol > float(max_per_well):
            raise SafetyError(
                f"Step {exp_id}: tube_vol {tube_vol:.3f} mL exceeds well capacity {max_per_well:.3f} mL"
            )

        max_temp = float(getattr(self.cfg, "max_temp", 9999.0))
        if temp > max_temp:
            raise SafetyError(f"Step {exp_id}: target temperature {temp:.1f}C exceeds max_temp {max_temp:.1f}C")

        flows = exp.get("flows", {})
        if not flows:
            raise SafetyError(f"Step {exp_id}: no flow data")

        total_flow = 0.0
        for p_name, flow in flows.items():
            flow_v = float(flow)
            if not math.isfinite(flow_v):
                raise SafetyError(f"Step {exp_id}: flow for {p_name} is not finite ({flow_v})")
            if flow_v < 0:
                raise SafetyError(f"Step {exp_id}: negative flow for {p_name}")
            total_flow += flow_v

        if total_flow <= 0:
            raise SafetyError(f"Step {exp_id}: total flow must be > 0")
        if total_flow > self.max_total_flow:
            raise SafetyError(
                f"Step {exp_id}: total flow {total_flow:.3f} mL/min exceeds max_total_flow_ml_min {self.max_total_flow:.3f}"
            )

        inlet_ports = exp.get("inlet_ports", {})
        # @codesyncer-decision: flows의 모든 펌프는 inlet_ports에 포트가 있어야 함 (fix #2)
        # - 기존: 누락 펌프는 reagent_sources에서 port 1(세척용매)로 디폴트
        #   → 시약 대신 '용매'를 주입하고도 실험은 정상 진행되는 silent failure
        # - UI는 항상 채우지만, method JSON 수동 편집/원격 명령 경로에서 발생 가능
        for p_name in flows.keys():
            if p_name not in inlet_ports:
                raise SafetyError(
                    f"Step {exp_id}: pump {p_name} has flow but no inlet port assigned "
                    f"(would default to Port 1 = solvent)"
                )
        for p_name, port in inlet_ports.items():
            p = int(port)
            if p < 1 or p > 12:
                raise SafetyError(f"Step {exp_id}: invalid inlet port for {p_name}: {p}")

        # @codesyncer-decision: 극초단 시간 가드 (fix #3)
        # - injection이 순차 start 격차(0.35s/펌프) 대비 너무 짧으면
        #   펌프별 주입 비율(=당량비)이 무의미하게 붕괴됨
        # - 분획당 시간이 collector 이동 시간보다 짧으면 well 이동이 물리적으로
        #   분획 경계를 추종할 수 없음
        MIN_INJECT_SEC = 2.0
        # F3(2026-07-06): plate96 '행 전환'(Z상승+XY+Z하강+M400)은 2s 를 넘을 수
        # 있음 — 실측 이동시간에 맞춰 system_params.min_tube_sec 로 상향 가능.
        try:
            MIN_TUBE_SEC = float(self.cfg.config_data.get("system_params", {})
                                 .get("min_tube_sec", 2.0) or 2.0)
        except (TypeError, ValueError):
            MIN_TUBE_SEC = 2.0
        MIN_TUBE_SEC = max(0.5, MIN_TUBE_SEC)
        inject_sec = (target_vol / total_flow) * 60.0
        if inject_sec < MIN_INJECT_SEC:
            raise SafetyError(
                f"Step {exp_id}: injection time {inject_sec:.2f}s < {MIN_INJECT_SEC:.0f}s — "
                f"순차 펌프 시작 격차 대비 너무 짧아 당량비가 붕괴됩니다 (부피↑ 또는 유속↓)"
            )
        _collector_on = (frac_settings.get("enabled", True) and self.collector
                         and getattr(self.collector, "is_connected", False))
        tube_sec = (tube_vol / total_flow) * 60.0
        if _collector_on and tube_sec < MIN_TUBE_SEC:
            raise SafetyError(
                f"Step {exp_id}: per-tube time {tube_sec:.2f}s < {MIN_TUBE_SEC:.0f}s — "
                f"collector 이동이 분획 경계를 추종할 수 없습니다 (분획부피↑ 또는 유속↓)"
            )

        # @codesyncer-decision: 시린지 용량 대비 주입량 검증
        # - inject_vol > capacity이고 allow_refill=False면 주입 불완전
        # - 시퀀스 시작 전 사전 차단하여 시약 낭비 방지
        for p_name, flow in flows.items():
            pump = self.pumps.get(p_name)
            if _is_smart_pump(pump):
                # @codesyncer-decision: 펌프별 max_flowrate 사전 차단 — NRG 어댑터는
                #   초과 유량을 클램프하지 않고 start 를 거부하는데(당량비 왜곡 방지),
                #   dosing 경로에는 complete-스레드 인터락이 없어 그 거부가 조용한
                #   무토출 채널이 된다. 시퀀스 시작 전 여기서 SafetyError 로 관측 가능하게.
                _pmax = getattr(getattr(pump, "driver", None), "max_flowrate", None)
                if _pmax and float(flow) > float(_pmax) + 1e-9:
                    raise SafetyError(
                        f"Step {exp_id}: {p_name} flow {float(flow):.3f} mL/min exceeds "
                        f"pump max_flowrate {float(_pmax):.3f} mL/min"
                    )
                inject_vol = (float(flow) / total_flow) * target_vol
                # 소스 라인 보정 충전(src)을 포함한 용량 검증
                _pf = max(1.0, min(3.0, float(self.cfg.config_data.get("system_params", {})
                                              .get("line_purge_factor", 1.0) or 1.0)))
                src_vol = (float(getattr(self.cfg, "line_vol_inlet", {}).get(p_name, 0.0) or 0.0)
                           + float(getattr(self.cfg, "line_vol_valve_pump", {}).get(p_name, 0.0) or 0.0)) * _pf
                if inject_vol + src_vol > float(pump.capacity):
                    raise SafetyError(
                        f"Step {exp_id}: {p_name} inject {inject_vol:.1f}mL + 라인보정 {src_vol:.2f}mL "
                        f"exceeds syringe capacity {pump.capacity:.1f}mL"
                    )
                # @codesyncer-decision: legacy push 용매 필요량 검증 (fix #4)
                # - push는 시간 구동이므로 펌프별 용매 소비 = (flow_i/F)×push_total.
                #   capacity 초과분은 조용히 클램프되어 push 도중 고갈(자동정지)
                #   → 총유속 추락 → Timer는 명목 유속 가정 → 분획 어긋남/제품 유실
                if self.push_pump is None:
                    push_total = (float(self.vol_reactor) + float(self.vol_post_common)
                                  + float(self.vol_collection))
                    solvent_need = (float(flow) / total_flow) * push_total
                    if solvent_need > float(pump.capacity):
                        raise SafetyError(
                            f"Step {exp_id}: {p_name} push solvent {solvent_need:.1f}mL "
                            f"exceeds syringe capacity {pump.capacity:.1f}mL "
                            f"(push_total={push_total:.1f}mL × flow share)"
                        )

        num_tubes = max(1, math.ceil(target_vol / tube_vol))
        if frac_settings.get("enabled", True) and self.collector and getattr(self.collector, "is_connected", False):
            max_tubes = self._max_collector_tubes()
            required_last_tube = self.current_tube + num_tubes  # +1 wash tube
            if required_last_tube > max_tubes:
                raise SafetyError(
                    f"Step {exp_id}: collector capacity exceeded (need tube {required_last_tube}, max {max_tubes})"
                )

        return temp, target_vol, tube_vol, flows, total_flow, num_tubes

    # ---------------------------------------------------------------------
    # Dead-volume plug timing (pure function — test_deadvol_timing.py 검증 대상)
    # ---------------------------------------------------------------------
    @staticmethod
    def _compute_plug_timing(flows, ordered, line_src, line_inj, tj_vols,
                             purge_order="fifo", entry_map=None):
        """채널별 데드볼륨 → (purge_sec, pre_sec, deficit_vol, stagger_offsets).

        물리 모델 (다중 펌프 = '단순 합산'이 아님):
        - 채널 i 의 플러그 도달시간 = 자기 주입경로 line_inj_i / f_i (자기 유속만 흐름)
          + Σ_j (V_j / F_j)  — 정션 공유 구간엔 '누적 유속' F_j 만 흐름
        - pre_sec = max_i(도달시간): 전 채널 합류 완료 시점 (max, sum 아님)
        - line_src(12way·12way→3way 앞단)는 여기 시간엔 안 들어가고 purge 창에만.
        - entry_map: {펌프명: 진입 구간번호}. 명시하면 그 매핑으로 통과 구간을
          결정 (예: QUAD 합류 토폴로지 A/B→1, C/D→2 — 2026-08-12 배관 재구성).
          F_j = '구간 j 이전에 진입한' 전 채널 유속 합. 미기재 펌프는 1(보수적).
          None(기본)이면 레거시 페어와이즈 캐스케이드(P1,P2→T1, P_m→T_{m-1},
          구간 j=1..n-2)를 유도해 기존 동작과 100% 동일.
        - purge_order:
            fifo (기본, 기존 동작) — 과충전 구내용물이 먼저 토출된다고 가정.
              pre_sec 에 purge_sec 선행 가산 + 스태거/deficit 보정.
            lifo (이상적 시린지 — 마지막 흡입분=시약이 먼저 나옴) — 헤드는 퍼지
              지연 없이 즉시 출발, 구내용물은 플러그 '꼬리'로 배출.
              스태거·deficit 불필요(전 채널 t=0 동시 전선).
          실측(염료) 캘리브레이션 후 outlet_switch_delay_sec 가 최우선 override.
        """
        lifo = (str(purge_order or "fifo").lower() == "lifo")
        purge_sec = 0.0
        inj_path_sec = 0.0
        n = len(ordered)
        if entry_map:
            _entry = {p: max(1, int(entry_map.get(p, 1) or 1)) for p in ordered}
            # 통과 상한 = 부피가 정의된 마지막 공유 구간 (레거시의 n-2 대신 실배관 기준)
            _max_seg = 0
            for k, v in (tj_vols or {}).items():
                try:
                    if float(v or 0.0) > 0:
                        _max_seg = max(_max_seg, int(k))
                except Exception:
                    continue
        else:
            # 레거시 유도 맵: P1,P2→T1, P_m→T_{m-1} — F_j(첫 j+1 펌프 합)와
            # j_enter..n-2 통과 범위가 기존 식과 항등 (test_deadvol_timing 회귀 보증)
            _entry = {p: (1 if idx <= 1 else idx) for idx, p in enumerate(ordered)}
            _max_seg = n - 2
        for p in ordered:
            f = float(flows.get(p, 0.0))
            if f <= 0:
                continue
            purge_sec = max(purge_sec, (line_src.get(p, 0.0) / f) * 60.0)
            t_i = (line_inj.get(p, 0.0) / f) * 60.0
            for j in range(_entry[p], _max_seg + 1):  # 진입 구간부터 마지막 공유 구간까지
                Fj = sum(float(flows.get(q, 0.0)) for q in ordered if _entry[q] <= j)
                Vj = float(tj_vols.get(j, 0.0) or 0.0)
                if Fj > 0 and Vj > 0:
                    t_i += (Vj / Fj) * 60.0
            inj_path_sec = max(inj_path_sec, t_i)

        pre_sec = inj_path_sec if lifo else purge_sec + inj_path_sec

        # 비대칭 라인 유속저하(deficit) + 스태거 오프셋 — FIFO 전용 보정
        deficit_vol = 0.0
        stagger_offsets = {}
        for p, f in flows.items():
            f = float(f)
            if f <= 0:
                continue
            purge_i = (line_src.get(p, 0.0) / f) * 60.0
            if not lifo:
                deficit_vol += f * (purge_sec - purge_i) / 60.0
            stagger_offsets[p] = 0.0 if lifo else max(0.0, purge_sec - purge_i)
        return purge_sec, pre_sec, deficit_vol, stagger_offsets

    # ---------------------------------------------------------------------
    # Valve and interlock
    # ---------------------------------------------------------------------
    def _switch_all(self, position: int):
        threads = []

        def _switch(v_name, valve, pos):
            t0 = time.time()
            try:
                valve.set_position(pos)
                self.trace.complete(f"VALVE {v_name}", f"→{pos}", t0, time.time() - t0)
            except Exception as exc:
                self.trace.instant(f"VALVE {v_name}", f"FAILED →{pos}",
                                   args={"error": str(exc)[:200]})
                print(f"[Warning] Valve switch failed ({v_name} -> {pos}): {exc}")

        for v_name, valve in self.valves.items():
            if v_name == "Outlet":
                continue
            t = threading.Thread(target=_switch, args=(v_name, valve, position), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    def _switch_valves_for_phase(self, inlet_ports: Dict[str, int]):
        threads = []

        def _set_selector(v_name, valve, port):
            t0 = time.time()
            try:
                valve.set_position(int(port))
                self.trace.complete(f"VALVE {v_name}", f"→port{int(port)}", t0, time.time() - t0)
            except Exception as exc:
                self.trace.instant(f"VALVE {v_name}", f"FAILED →port{port}",
                                   args={"error": str(exc)[:200]})
                print(f"[Warning] Selector switch failed ({v_name} -> {port}): {exc}")

        def _set_switcher(v_name, valve):
            t0 = time.time()
            try:
                valve.set_position(2)  # Reactor direction
                self.trace.complete(f"VALVE {v_name}", "→REACTOR(2)", t0, time.time() - t0)
            except Exception as exc:
                self.trace.instant(f"VALVE {v_name}", "FAILED →2",
                                   args={"error": str(exc)[:200]})
                print(f"[Warning] Switcher switch failed ({v_name} -> 2): {exc}")

        for p_name, port in inlet_ports.items():
            selector_name = self.cfg.PUMP_VALVE_MAP.get(p_name)
            if selector_name and selector_name in self.valves:
                t = threading.Thread(
                    target=_set_selector,
                    args=(selector_name, self.valves[selector_name], int(port)),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            switcher_name = f"{p_name}_Switcher"
            if switcher_name in self.valves:
                t = threading.Thread(
                    target=_set_switcher,
                    args=(switcher_name, self.valves[switcher_name]),
                    daemon=True,
                )
                t.start()
                threads.append(t)

        for t in threads:
            t.join()
        time.sleep(0.3)

    def _stop_momentary(self):
        for p_obj in self.pumps.values():
            try:
                if _is_smart_pump(p_obj):
                    stopped = p_obj.driver.is_stopped()
                    if stopped is True:
                        p_obj.running = False
                        continue
                p_obj.stop()
            except Exception:
                pass

    # ── 오토샘플러 헬퍼 (Phase C v0) ──────────────────────────────
    def _autosampler_coord(self, p_name):
        """autosampler 라우팅이고 조율기가 배선된 경우에만 반환."""
        if self._pump_routing(p_name) != "autosampler":
            return None
        return self._sampler_coords.get(p_name)

    def _autosampler_source_vial(self, p_name, default="A1"):
        """그룹 설정의 소스 vial (v0: 그룹당 고정 — per-step vial 은 Phase A/B).

        설정 위치: roles.pumps[].settings.source_vial (미지정 시 'A1')
        """
        try:
            for pc in self.cfg.config_data.get("roles", {}).get("pumps", []):
                if pc.get("name") == p_name:
                    return str((pc.get("settings") or {}).get("source_vial", default))
        except Exception:
            pass
        return default

    def _check_interlock(self, sequence_plan):
        # @codesyncer(감사 2026-07-13): 장치 역할 중복 사전 차단 — 같은 물리 장치
        #   (driver_id)가 두 펌프 그룹의 selector/switcher 등에 동시 배정되면
        #   (실 config 사례: dev_4715d181 = Group_C.selector 이자 Group_D.switcher)
        #   한 그룹의 밸브 전환이 다른 그룹 유로를 물리적으로 바꿔버림 + 시리얼 경합.
        #   플랜이 실제 사용하는 그룹들 사이에서만 검사(미사용 그룹의 설정 잔재는 무해).
        used_pumps = set()
        for exp in sequence_plan:
            used_pumps.update(exp.get("flows", {}).keys())
        dev_users = {}
        for role in (self.cfg.config_data.get("roles", {}).get("pumps", [])
                     if hasattr(self.cfg, "config_data") else []):
            if role.get("name") not in used_pumps:
                continue
            for slot, dev_id in (role.get("drivers") or {}).items():
                if dev_id:
                    dev_users.setdefault(dev_id, []).append(f"{role['name']}.{slot}")
        dups = {d: u for d, u in dev_users.items() if len(u) > 1}
        if dups:
            detail = " / ".join(f"{d}: {', '.join(u)}" for d, u in dups.items())
            raise SafetyError(
                f"Interlock error: 장치 역할 중복 — 같은 장치가 여러 슬롯에 배정됨 ({detail}). "
                "하드웨어 설정에서 그룹별로 별개 장치를 지정하세요")

        for exp in sequence_plan:
            for p_name in exp.get("flows", {}).keys():
                if p_name not in self.pumps:
                    raise SafetyError(f"Interlock error: pump not found: {p_name}")
                selector_name = self.cfg.PUMP_VALVE_MAP.get(p_name)
                if selector_name and selector_name not in self.valves:
                    raise SafetyError(f"Interlock error: valve not found: {selector_name}")
                # autosampler 그룹: 샘플러 연결 + 소스 vial 좌표 사전 검증
                # (시퀀스 중단보다 사전 차단 — 기존 인터락 원칙)
                if self._pump_routing(p_name) == "autosampler":
                    coord = self._sampler_coords.get(p_name)
                    if coord is None:
                        raise SafetyError(
                            f"Interlock error: [{p_name}] autosampler 라우팅이지만 "
                            f"샘플러가 배선되지 않음 (roles.samplers 확인)")
                    # per-step vial(플랜) + 그룹 폴백 vial 좌표 전부 사전 검증
                    step_vial = (exp.get("inlet_vials") or {}).get(p_name)
                    required = [step_vial or self._autosampler_source_vial(p_name)]
                    ok, msg = coord.ensure_ready(required)
                    if not ok:
                        raise SafetyError(
                            f"Interlock error: [{p_name}] sampler not ready — {msg}")
        # push_pump이 설정되어 있으면 필수 메서드 존재 검증
        if self.push_pump is not None:
            for method_name in ("set_flow", "start", "stop"):
                if not hasattr(self.push_pump, method_name):
                    raise SafetyError(
                        f"Interlock error: push_pump missing required method '{method_name}'"
                    )
        else:
            # @codesyncer-decision(2026-08-14, 유령 폴백 차단 — 사용자 확정):
            #   roles.push_pump 가 구성돼 있는데 연결 실패로 None 강등된 채 시퀀스를
            #   시작하면 조용히 레거시 시린지 푸시로 폴백해 워크플로(push 병행 세척,
            #   스텝2+ 시간단축)가 무효화된다 — 2026-08-14 실런에서 사용자가 로그를
            #   보고서야 인지. 시작 전에 시끄럽게 차단한다. 레거시 푸시를 원하면
            #   장치설정에서 push_pump 역할을 해제하면 된다.
            _pp_role = ((self.cfg.config_data.get("roles", {}) or {})
                        .get("push_pump", {}) or {})
            if _pp_role.get("driver_id"):
                raise SafetyError(
                    "Interlock error: push_pump(HPLC) 역할이 설정돼 있으나 미연결 상태 "
                    "(초기화 시 강등됨). Reaxus 전원 ON + USB 연결 확인 후 "
                    "Setting→저장(하드웨어 Hot Reload) 또는 앱 재시작으로 재연결하세요. "
                    "다른 프로그램이 해당 COM 포트를 점유 중이면 닫아야 합니다. "
                    "레거시 시린지 푸시로 실행하려면 장치설정에서 push_pump 역할을 "
                    "해제하세요.")

    # ---------------------------------------------------------------------
    # Main run loop
    # ---------------------------------------------------------------------
    def run_sequence(self, sequence_plan, map_mgr):
        # @codesyncer-decision: 재진입 가드 — 이미 실행 중이면 즉시 반환
        #   UI 가드를 우회하는 경로(원격 명령, 단축키 등)에 대비한 마지막 방어선.
        #   atomic check-and-set을 위해 lock 사용.
        with self._run_lock:
            if self._running:
                self._log("[GUARD] run_sequence 이미 실행 중 — 중복 호출 무시")
                return
            self._running = True

        try:
            self._run_sequence_impl(sequence_plan, map_mgr)
        finally:
            self._running = False
            # 트레이스 마감 (열린 스팬 자동 종료 + 유효한 JSON 으로 close)
            try:
                self._trace_phase_name = None
                self.trace.close()
            except Exception:
                pass

    def _startup_level_reconcile(self, sequence_plan=None):
        """@codesyncer-decision: 콜드스타트/재시작 시 시린지 물리 잔량을 초음파(HC-SR04)로
          실측해 소프트웨어 카운터를 진실화 (RoboChem SI 3.3.1 이식).
          - reagent 정책(external_valve 한정): 잔량>gate 면 폐액 '배출'(wash_infuse: 12-way
            waste 로 토출 → 플런저 빈 위치로 종료) 반복 → 센서로 empty 검증 → current_vol=0.
            max_iter 초과 잔류 시 SafetyError(클로그/스톨 의심 — run-dry·교차오염 사전 차단).
            ※ wash_cycle(infuse→withdraw)은 용매를 재흡입해 '가득 찬 상태'로 끝나 센서가
              항상 full 로 읽음 → 사용 금지. 반드시 wash_infuse(빈 상태 종료)로 배출.
              배럴 벽 필름 세척은 이후 wash 단계(withdraw→infuse)가 담당.
          - solvent 정책: 잔량 그대로 채택(홈/퍼지 생략, 재사용 라인 빠른 재개). capacity 클램프.
          - 센서 미장착/미연결/측정실패: 무동작 → current_vol 리셋 '가정 empty' 유지(폴백,
            거짓 empty 인증 금지).
          - 이번 플랜에서 실제 쓰는 펌프만 대상(_check_interlock 범위 일치, off-plan 금지).
          current_vol 리셋 이후, HTE 분기 이전에 1회 호출(표준/HTE 공통)."""
        sensor = getattr(self, "level_sensor", None)
        if sensor is None or not getattr(sensor, "is_connected", False):
            return
        level_cfg = getattr(self.cfg, "PUMP_LEVEL_CFG", {}) or {}
        if not level_cfg:
            return
        max_iter = max(1, int(getattr(self, "level_purge_max_iter", 3)))
        routing_map = getattr(self.cfg, "PUMP_ROUTING", {}) or {}

        # @codesyncer-decision: 이번 플랜에서 실제 쓰는 펌프만 정합 — _check_interlock 범위와
        #   동일. off-plan 펌프 액추에이션(밸브 공유 충돌·용매 낭비) 금지.
        #   fail-safe: 플랜이 주어졌는데 flows 를 못 찾으면 전체 처리(fail-open)가 아니라
        #   '정합 생략' — 플랜 구조 이상 시 off-plan 액추에이션 방지. 플랜 미지정(직접 호출)만 전체.
        plan_scoped = sequence_plan is not None
        used = set()
        for exp in (sequence_plan or []):
            fl = exp.get("flows") if isinstance(exp, dict) else None
            if isinstance(fl, dict):
                used.update(fl.keys())
        if plan_scoped and not used:
            self._log("[레벨센서] 플랜에서 flows 를 찾지 못함 — 잔량 정합 생략(fail-safe)")
            return

        # @codesyncer(적대검증 F1, 2026-07-29): 배출 경로가 리액터→Outlet 이므로 퍼지 전
        #   Outlet=WASTE 를 명시 확보. 직전 런이 수집(COLLECT) 중 abort 되면 밸브가
        #   COLLECT 로 남는데(글로벌 호밍은 이 함수 '이후' 실행, cleanup 은 Outlet 미조작,
        #   _switch_all 도 Outlet 제외) 배출 치환분이 웰로 넘친다(웰 상한 1.5mL).
        #   전환 실패 = 배출 안전 미보장 → 시끄럽게 중단(가열/도징 전이라 안전한 정지점).
        if "Outlet" in getattr(self, "valves", {}):
            try:
                self.valves["Outlet"].set_position(1)
            except Exception as e:
                raise SafetyError(
                    f"[레벨센서] Outlet WASTE 전환 실패 — 잔량 배출 불가: {e}")

        def _cap_ml(pump):
            # capacity 가 0/None/음수(오설정)면 클램프 비활성(1e9) — 음수/거짓 empty 방지.
            cap = getattr(pump, "capacity", None)
            return float(cap) if (cap and cap > 0) else 1.0e9

        def _adopt_ml(pump, vol_ul):
            # @codesyncer-decision: capacity 클램프 — 오판독(과대 raw)이 run-dry 가드를
            #   무력화(current_vol 과대 → 리필 안 함 → 빈 채로 밀기)하는 것을 차단.
            return round(min(float(vol_ul) / 1000.0, _cap_ml(pump)), 4)

        measured = {}
        for name, lc in level_cfg.items():
            if plan_scoped and name not in used:
                continue
            pump = self.pumps.get(name)
            if pump is None or not _is_smart_pump(pump):
                continue
            policy = lc.get("policy", "reagent")
            # gate: NaN/inf/≤0 (오설정) 모두 기본 500 으로 — NaN 은 모든 비교가 False 라
            #   '무배출 + 검증 empty' 거짓 통과를 만들 수 있으므로 반드시 걸러낸다.
            gate = float(lc.get("gate_ul", 500.0))
            if not (math.isfinite(gate) and gate > 0):
                gate = 500.0
            try:
                vol_ul = sensor.get_volume(name)
            except Exception as e:
                # @codesyncer-decision: 측정 실패는 조용한 오판보다 '가정 empty' 폴백이 안전
                self._log(f"[레벨센서] {name} 측정 실패 — 폴백(가정 empty 유지): {e}")
                continue
            if vol_ul is None:
                continue
            measured[name] = vol_ul

            if policy == "solvent":
                # 재사용 라인: 잔량 그대로 채택 (RoboChem no_fill식, 홈/퍼지 생략)
                pump.current_vol = _adopt_ml(pump, vol_ul)
                self._log(f"[레벨센서] {name}: 잔량 {vol_ul:.0f}µL 채택 "
                          f"(solvent 정책 → current_vol={pump.current_vol:.3f}mL)")
                continue

            # reagent 정책: 잔량 폐액 퍼지 → 검증 → 리셋
            routing = routing_map.get(name, "external_valve")
            if routing != "external_valve":
                # @codesyncer-inference: NRG(internal_valve)/autosampler 의 wash 는 외부 폐액
                #   덤프가 아니라 펌웨어 버블퍼지(자기 리저버 순환) — 교차오염 제거 불가.
                #   여기선 잔량을 카운터에 반영(run-dry 방지)만 하고 퍼지는 생략+경고.
                #   검증: 실기에서 NRG 라인 오염이 문제되면 별도 waste 라인/정책 필요.
                pump.current_vol = _adopt_ml(pump, vol_ul)
                self._log(f"[레벨센서] ⚠ {name}: routing={routing} 는 외부 폐액 퍼지 미지원 "
                          f"— 잔량 {vol_ul:.0f}µL 카운터 반영만(오염 퍼지 생략). "
                          f"reagent 퍼지는 external_valve 라인에서만 유효.")
                continue

            # @codesyncer-decision(finding5): gate 가 capacity 의 큰 비율이면 '검증 empty' 는
            #   거친 근사(최대 gate 만큼 잔류 가능) — 정직성 경고. 상한을 강제하진 않되 알린다.
            cap_ml = _cap_ml(pump)
            if gate > 0.3 * cap_ml * 1000.0:
                self._log(f"[레벨센서] ⚠ {name}: gate {gate:.0f}µL 가 capacity 의 30% 초과 "
                          f"— 'empty 확인'은 거친 근사(최대 gate µL 잔류 가능).")
            # @codesyncer-decision(잔량제거, 2026-07-28): 배출-검증 루프를
            #   _verify_pump_empty(action="purge") 로 추출 — 시퀀스 중간 게이트
            #   (②세척후/③주입후/④푸시후/⑤종료)와 공용.
            # @codesyncer(2026-07-29, 사용자 지시): 게이트①도 배출 방향 = 리액터
            #   (prime 경로 → Outlet=WASTE → 폐액병) — 12-way 폐액 포트 배관 의존
            #   제거. 잔류 시약이 리액터 경로를 지나가지만 이후 세척/프리필이 치환.
            ok, vol_end = self._verify_pump_empty(
                name, pump, gate=gate, max_iter=max_iter,
                action="purge", initial_vol=vol_ul, discharge="reactor")
            if vol_end is not None:
                measured[name] = vol_end
            if not ok:
                # abort 중단 — 빈 상태 미보장이므로 current_vol 리셋 없이 정합 종료
                return

        # 대시보드 표시용(선택) — 슬롯에 연결된 UI 없으면 no-op
        if measured and hasattr(self.signals, "sig_level_data"):
            try:
                self.signals.sig_level_data.emit(dict(measured))
            except Exception:
                pass

    def _verify_pump_empty(self, name, pump, *, gate, max_iter=None, context="",
                           action="purge", initial_vol=None,
                           discharge="waste", rate=None):
        """시린지 '빈 상태'(잔량 ≤ gate µL)를 초음파 센서로 확인/달성하는 공용 루틴.

        @codesyncer-decision(잔량제거, 2026-07-28): _startup_level_reconcile 의 배출-검증
          루프를 추출 — 시퀀스 중간 게이트(②세척후/③주입후/④푸시후/⑤종료)가 재사용.
          개루프 상대변위 펌프(절대 0점 없음)에선 센서만이 물리 잔량의 진실원이므로,
          '비어 있어야 하는' 시점마다 이 루틴으로 0점 도달을 확인한다.
        @codesyncer-decision(2026-07-29, 사용자 실기 지시): 토출 sizing = '센서 실측치
          그대로' (기존 +1mL 마진 제거) — 하드스톱 과주행 시 펌프가 에러를 반환하며
          시스템이 멈추는 실기 거동 때문. 0점 앵커 캘리브(0점 노이즈 ±27µL)라 실측치만
          밀면 눈금 0 근방에서 멈추고, 잔차는 재측정→추가 토출로 수렴(폐루프).
        action:
          log   — 측정+기록만 (액추에이션 없음).
          warn  — gate 초과 시 경고 로그(미토출/이월 잔량 의심). 액추에이션 없음.
          purge — 실측치 배출 반복 → 재측정 ≤ gate → current_vol=0.
                  max_iter 초과/HW 실패/재측정 실패 = SafetyError.
        discharge (purge 전용):
          waste   — wash_infuse → 12-way 폐액 포트 (시약 잔량: 리액터 오염 금지 경로)
          reactor — prime 경로 → 리액터 (용매 잔량: 다운스트림 플러시 겸, Outlet=WASTE
                    상태라 폐액병으로 배출). rate(mL/min) 지정 시 prime_rate 임시
                    오버라이드 — ④푸시후 게이트는 스텝 유속 그대로 추가 토출.
        @return (ok, vol_ul): ok=빈 상태 확인(측정불가 폴백도 True — 거짓 경보 금지),
          vol_ul=마지막 실측(µL, None=측정불가). purge 중 abort 는 (False, vol) 반환
          — 미검증 상태이므로 호출부는 current_vol 리셋 금지.
        """
        sensor = getattr(self, "level_sensor", None)
        if sensor is None or not getattr(sensor, "is_connected", False):
            return True, None
        tag = f"[레벨센서{('·' + context) if context else ''}]"
        vol_ul = initial_vol
        if vol_ul is None:
            try:
                vol_ul = sensor.get_volume(name)
            except Exception as e:
                # 측정 실패 = 조용한 오판보다 무판정 폴백이 안전 (거짓 empty/경보 금지)
                self._log(f"{tag} {name} 측정 실패 — 폴백(무판정): {e}")
                return True, None
            if vol_ul is None:
                return True, None

        if action in ("log", "warn"):
            if vol_ul > gate:
                if action == "warn":
                    self._log(f"{tag} ⚠ {name}: 잔량 {vol_ul:.0f}µL > gate {gate:.0f}µL "
                              f"— 미토출/이월 잔량 의심")
                else:
                    self._log(f"{tag} {name}: 잔량 {vol_ul:.0f}µL (기록)")
                return False, vol_ul
            self._log(f"{tag} {name}: empty 확인 (잔량 {vol_ul:.0f}µL ≤ gate {gate:.0f}µL)")
            return True, vol_ul

        # ── action == "purge": 폐루프 배출-검증 (실측치 토출 → 재측정 → 수렴) ──
        max_iter = max(1, int(max_iter if max_iter is not None
                              else getattr(self, "level_purge_max_iter", 3)))
        cap = getattr(pump, "capacity", None)
        cap_ml = float(cap) if (cap and cap > 0) else 1.0e9
        _dis_label = "리액터 배출(prime)" if discharge == "reactor" else "폐액 배출(wash_infuse)"

        def _monitored_discharge(target_ml, rate_eff):
            """'밀면서 측정' (2026-07-29 사용자 지시 — 사실상 동시 폐루프).

            토출 중 센서를 고속(5표본 ≈ 0.5s) 폴링해 gate 도달 즉시 정지.
            이중 안전: ①펌프 set volume=실측치 → 자체 자동정지가 과주행 상한
            (하드스톱 도달 불가 → 펌프 에러/시스템 정지 방지) ②센서 조기정지가
            그 안에서 먼저 끊음. 고속 판독 실패는 무시 — 최종 판정은 루프 뒤
            풀샘플 검증 측정이 담당(조용한 오판 금지)."""
            t_need = (target_ml / rate_eff) * 60.0 if rate_eff > 0 else 0.0
            deadline = time.monotonic() + t_need + 2.0
            while time.monotonic() < deadline:
                if self.abort_flag or getattr(pump, "_abort_refill", False):
                    break
                try:
                    fast = sensor.get_volume(name, samples=5)
                except Exception:
                    fast = None
                if fast is not None and fast <= gate:
                    break
                time.sleep(0.05)
            # 정지 — 이미 자동정지면 stop 스킵 (무ACK 재시도의 버스 점유 방지)
            try:
                drv = getattr(pump, "driver", None)
                if not (drv is not None and hasattr(drv, "is_stopped")
                        and drv.is_stopped() is True):
                    pump.stop()
            except Exception:
                pass

        attempts = 0
        while vol_ul > gate and attempts < max_iter:
            # 매 반복 abort 확인 (E-Stop/정지 반응). 중단 시 빈 상태 미보장.
            if self.abort_flag:
                self._log(f"{tag} {name}: abort 감지 — 잔량 정합 중단")
                return False, vol_ul
            attempts += 1
            self._log(f"{tag} {name}: 잔량 {vol_ul:.0f}µL > gate {gate:.0f}µL "
                      f"— {_dis_label}(실측 폐루프) {attempts}/{max_iter}"
                      + (f" @{float(rate):.2f}mL/min" if (discharge == "reactor" and rate) else ""))
            # @codesyncer-decision(NEW-A 개정, 2026-07-29): 토출량 = 실측치 그대로
            #   (마진 없음) — 하드스톱 과주행은 펌프 에러→시스템 정지 유발(실기 확인).
            #   부족분은 재측정 후 다음 반복이 마저 민다 (폐루프 수렴).
            target_ml = min(float(vol_ul) / 1000.0, cap_ml)
            if discharge == "reactor":
                # prime 경로 = 3-way POS_REACTOR → 리액터 → Outlet(WASTE) → 폐액병.
                # prime 은 counter(current_vol) 기반이므로 센서 실측으로 진실화 후 호출.
                saved_pr = getattr(pump, "prime_rate", None)
                try:
                    pump.current_vol = target_ml
                    if rate and saved_pr is not None:
                        pump.prime_rate = float(rate)
                    rate_eff = float(getattr(pump, "prime_rate", 8.0) or 8.0)
                    if not pump.prime_prepare():
                        raise SafetyError(
                            f"{tag} {name}: 리액터 배출 시작 실패(is_refilling 잔류?)")
                    pump.prime_trigger()
                    _monitored_discharge(target_ml, rate_eff)
                except SafetyError:
                    raise
                except Exception as e:
                    raise SafetyError(f"{tag} {name}: 리액터 배출(prime) 실패 — {e}")
                finally:
                    pump.is_refilling = False   # complete 미경유 — 상태 정리
                    if saved_pr is not None:
                        pump.prime_rate = saved_pr
            else:
                saved_wv = getattr(pump, "wash_volume", None)
                try:
                    if saved_wv is not None:
                        pump.wash_volume = target_ml
                    # wash_infuse = 12-way waste 로 토출 (begin=prepare+trigger).
                    #   @codesyncer(NEW-C 개정): complete 미경유 — HW 실패/미기동은
                    #   '센서가 안 줄어듦'으로 드러나 max_iter 후 SafetyError 로 수렴
                    #   (silent false-clean 여전히 불가: 성공 판정은 센서 실측뿐).
                    if not pump.wash_infuse_begin(waste_port=12):
                        raise SafetyError(
                            f"{tag} {name}: 폐액 배출 시작 실패(is_refilling 잔류?)")
                    rate_eff = float(getattr(pump, "wash_speed", 8.0) or 8.0)
                    _monitored_discharge(target_ml, rate_eff)
                except SafetyError:
                    raise
                except Exception as e:
                    raise SafetyError(f"{tag} {name}: 폐액 배출(wash_infuse) 실패 — {e}")
                finally:
                    pump.is_refilling = False   # complete 미경유 — 상태 정리
                    if saved_wv is not None:
                        pump.wash_volume = saved_wv
            if self.abort_flag or getattr(pump, "_abort_refill", False):
                self._log(f"{tag} {name}: 배출 중 abort — 정합 중단")
                return False, vol_ul
            try:
                vol_ul = sensor.get_volume(name)   # 풀샘플 검증 측정 (최종 판정)
            except Exception as e:
                raise SafetyError(f"{tag} {name}: 배출 후 재측정 실패 — {e}")
        if vol_ul > gate:
            raise SafetyError(
                f"{tag} {name}: {max_iter}회 배출 후에도 잔량 {vol_ul:.0f}µL "
                f"(gate {gate:.0f}µL) — 클로그/스톨/펌프 미기동/센서 이상 의심. 시퀀스 중단.")
        # @codesyncer(고정에코 방어, 2026-07-29 적대검증): empty 인증(current_vol=0)은
        #   '연속 2회' 풀샘플 판독이 모두 ≤ gate 여야 한다 — ch0_sweep 실측에서 0점
        #   고정에코(raw 8.61cm 락온) 버스트 ~2.7s 가 관측됨(진짜 잔량 ~500µL 를 0 으로
        #   오판). 풀판독 1회 ≈ 3.8s 라 연속 2회(>7s)를 한 버스트가 모두 속일 수 없음.
        #   확인판독 실패/불일치 = 지금 이 순간 센서를 신뢰할 수 없다는 뜻 → 거짓 empty
        #   인증 대신 시끄럽게 중단(루프 내 재측정 실패와 동일 정책).
        try:
            vol_confirm = sensor.get_volume(name)
        except Exception as e:
            raise SafetyError(f"{tag} {name}: empty 확인판독 실패 — {e}")
        if vol_confirm > gate:
            raise SafetyError(
                f"{tag} {name}: empty 확인판독 불일치 ({vol_ul:.0f}→{vol_confirm:.0f}µL "
                f"> gate {gate:.0f}µL) — 고정에코/노이즈 의심. 시퀀스 중단.")
        vol_ul = vol_confirm
        pump.current_vol = 0.0
        self._log(f"{tag} {name}: empty 확인 (잔량 {vol_ul:.0f}µL ≤ gate {gate:.0f}µL, "
                  f"±gate 근사) → current_vol=0")
        return True, vol_ul

    def _level_gate(self, pump_names, point, context, force_action=None,
                    discharge="waste", rates=None):
        """시퀀스 중간 센서 게이트 — system_params.level_verify_points[point] 정책으로
        PUMP_LEVEL_CFG 등록 펌프의 빈 상태를 검증. 미등록/센서 미연결은 무동작.

        @codesyncer-decision(잔량제거, 2026-07-28): purge 는 외부 밸브 경로
          (external_valve)에서만 유효 — 아니면 warn 강등. cleanup(⑤) 호출부는
          force_action="log" 로 강등(abort 경로 공용 — 액추에이션 금지).
        discharge/rates(2026-07-29): purge 배출 방향/유속 — ②세척후·④푸시후는
          discharge="reactor"(용매→리액터 경유 폐액병, 다운스트림 플러시 겸),
          rates={펌프명: mL/min}(④=스텝 유속으로 추가 토출).
        @return {펌프명: (ok, vol_ul)} — ok=False 이고 vol_ul 이 있으면 잔량 검출.
        """
        out = {}
        sensor = getattr(self, "level_sensor", None)
        if sensor is None or not getattr(sensor, "is_connected", False):
            return out
        level_cfg = getattr(self.cfg, "PUMP_LEVEL_CFG", {}) or {}
        action = str(force_action
                     or (getattr(self, "level_verify_points", {}) or {}).get(point, "off")
                     or "off").strip().lower()
        if action in ("off", "none", ""):
            return out
        routing_map = getattr(self.cfg, "PUMP_ROUTING", {}) or {}
        measured = {}
        for p_name in pump_names:
            lc = level_cfg.get(p_name)
            pump = self.pumps.get(p_name)
            if lc is None or pump is None or not _is_smart_pump(pump):
                continue
            act = action
            if act == "purge" and routing_map.get(p_name, "external_valve") != "external_valve":
                act = "warn"
            gate = float(lc.get("gate_ul", 500.0))
            if not (math.isfinite(gate) and gate > 0):
                gate = 500.0
            ok, vol_ul = self._verify_pump_empty(
                p_name, pump, gate=gate, context=context, action=act,
                discharge=discharge,
                rate=(rates or {}).get(p_name))
            out[p_name] = (ok, vol_ul)
            if vol_ul is not None:
                measured[p_name] = vol_ul
            if self.abort_flag:
                break
        if measured and hasattr(self.signals, "sig_level_data"):
            try:
                self.signals.sig_level_data.emit(dict(measured))
            except Exception:
                pass
        return out

    def _run_sequence_impl(self, sequence_plan, map_mgr):
        self.abort_flag = False
        self._cleanup_done = False
        # @codesyncer(검증 2026-08-12): pause_event 재장전 — 이전 런이 '일시정지 중
        #   예외'로 끝나면 cleared 로 남아, 다음 런이 히터 가열 ON 상태로 첫
        #   체크포인트("Paused (heating)")에서 조용히 멈추던 버그. 새 런은 항상
        #   Running 으로 시작한다.
        self.pause_event.set()

        # @codesyncer-decision: 시퀀스 시작 시 모든 펌프 current_vol 리셋
        # - 이전 시퀀스의 잔량이 남아있으면 initial refill의 fill_vol이 달라짐
        # - 시린지 위치 센서가 없으므로 매 시퀀스 시작 시 "비어있다"로 가정
        # @codesyncer-decision: is_refilling도 리셋 — 이전 시퀀스의 abort 시
        #   refill_complete의 finally 블록까지 도달 못한 펌프가 True로 남아
        #   다음 시퀀스에서 refill_prepare가 즉시 return False → "refill 시작 실패" 버그 유발
        # @codesyncer-inference: _abort_refill도 같이 리셋해야 함 — True가 남아있으면
        #   다음 시퀀스의 refill_complete에서 즉시 abort 처리됨
        for p_obj in self.pumps.values():
            if _is_smart_pump(p_obj):
                p_obj.current_vol = 0.0
                p_obj.target_flow = 0.0
                p_obj.running = False
                p_obj.status = "Idle"
                p_obj.is_refilling = False
                p_obj._abort_refill = False

        total_steps = len(sequence_plan)
        self._init_log("Sequence")
        self._log("Sequence Start")

        self._check_interlock(sequence_plan)

        # @codesyncer-decision: 콜드스타트 잔량 정합(초음파 실측→퍼지→검증 리셋).
        #   HTE 분기(아래) 이전에 실행해 표준/HTE 공통 적용. 센서 없으면 무동작.
        #   SafetyError(퍼지 실패) 는 SequenceWorker 가 Stopped 로 처리(아직 가열/도징 전).
        self._startup_level_reconcile(sequence_plan)

        sp = self.cfg.config_data.get("system_params", {}) if hasattr(self.cfg, "config_data") else {}
        f_prefill = float(sp.get("syringe_refill_rate", 20.0))

        # @codesyncer(다중 실험 이월 정책): 시퀀스(=실험) 시작 시 라인 프라이밍 상태 리셋 —
        #   current_vol 리셋(884행)과 동일 논리. 위치센서가 없어 라인 상태를 알 수 없으므로
        #   안전하게 '미프라임'(첫 스텝 인렛관 전량 퍼지) 가정 → 실험 간 시약병 교체·드레인·
        #   장기 유휴 시 오염/저퍼지 방지. 표준·HTE 공통(분기 이전). 연속 캠페인(라인 유지
        #   확실)엔 system_params.persist_primed_lines=true 로 실험 간 이월 유지 가능.
        if not bool(sp.get("persist_primed_lines", False)):
            self._primed_ports = {}

        # ── HTE droplet 모드 분기 (질소 스페이서 드롭 트레인) ──
        if bool(sp.get("hte_mode", False)):
            return self._run_hte_droplet(sequence_plan, sp, f_prefill)

        v_zone2_total = float(self.vol_reactor) + float(self.vol_post_common)

        try:
            # Global homing
            if "Outlet" in self.valves:
                self.valves["Outlet"].set_position(1)
            self._switch_all(1)

            self.current_tube = int(getattr(self, "collector_start_tube", 1))
            # @codesyncer-decision: collector homing을 가열 대기와 병렬 수행
            # Plate96 (Marlin G28)은 최대 60초 소요 → 순차 실행 시 시퀀스 지연
            # 백그라운드 스레드로 분리 후 수집 직전 pre-move에서 join하여 동기화
            # @codesyncer-risk: 연속 run_sequence 시 이전 호밍 스레드가 살아있으면
            #   새 스레드와 동일 collector 객체에 동시 접근 → 시리얼 경합 발생 가능.
            #   진입 시 기존 스레드를 먼저 정리한다.
            # @codesyncer-decision: abort-aware 폴링 — 2초 단위로 나눠 대기해
            #   대기 중 사용자의 estop(abort_flag)에 즉시 반응할 수 있게 한다.
            # @codesyncer-decision: 이전 호밍 스레드 정리 — 시리얼 경합 방지
            #   1) 이전 스레드 10초 대기
            #   2) 아직 살아있으면 stop_motion으로 Marlin 즉시 정지 후 join
            #   3) 깨끗한 상태에서 새 호밍 시작
            prev_homing = getattr(self, "_homing_thread", None)
            if prev_homing is not None and prev_homing.is_alive():
                self._log("[Collector] waiting for previous homing thread...")
                prev_homing.join(timeout=10.0)
                if prev_homing.is_alive():
                    self._log("[Collector] previous homing timeout → stop_motion")
                    try:
                        if hasattr(self.collector, 'stop_motion'):
                            self.collector.stop_motion()
                    except Exception:
                        pass
                    prev_homing.join(timeout=5.0)
            self._homing_thread = None

            if self.collector and getattr(self.collector, "is_connected", False):
                start_tube_local = self.current_tube

                def _home_then_move():
                    try:
                        ok, msg = self.collector.home()
                        if not ok:
                            self._log(f"[Collector] home failed: {msg}")
                            return
                        ok, msg = self.collector.move_to_tube(start_tube_local)
                        if not ok:
                            self._log(f"[Collector] move_to_tube({start_tube_local}) failed: {msg}")
                    except Exception as exc:
                        self._log(f"[Collector] homing failed: {exc}")

                self._homing_thread = threading.Thread(
                    target=_home_then_move, daemon=True, name="CollectorHoming"
                )
                self._homing_thread.start()
                _start_well = self.collector.get_well_id(self.current_tube) if hasattr(self.collector, 'get_well_id') else f"Tube {self.current_tube}"
                self._log(f"Collector homing started (parallel) -> {_start_well}")

            prev_inlet_ports = None

            for idx, exp in enumerate(sequence_plan):
                self._check_abort()

                # @codesyncer-decision: 매 step 시작 시 current_vol 리셋
                # 이전 step의 추적 오차가 다음 step에 전파되지 않도록 함
                # prefill에서 새로 채우므로 이전 잔량은 무의미
                for p_obj in self.pumps.values():
                    if _is_smart_pump(p_obj):
                        p_obj.current_vol = 0.0

                exp_id = idx + 1
                inlet_ports = exp.get("inlet_ports", {})
                ports_changed = self._ports_changed(prev_inlet_ports, inlet_ports)
                prev_inlet_ports = dict(inlet_ports)

                frac_settings = self._fraction_settings()
                target_temp, target_vol, vol_per_tube, flows, total_flow, num_tubes = self._validate_step_inputs(
                    exp_id, exp, frac_settings
                )

                inject_sec = (target_vol / total_flow) * 60.0

                # ── 채널 구간별 데드볼륨 모델 ─────────────────────────
                # @codesyncer-decision: 배관 데드볼륨의 시퀀스 반영 (백업 '데드볼륨 제거'의
                # 실패 원인이던 '주입 후 잔량'을 만들지 않는 방식으로 재설계)
                # - src_i (시약~12way~3way~시린지): 충전 시 라인의 구내용물이 시린지에
                #   먼저 들어옴 → fill = inject_vol + src_i 과충전으로 순수 시약량 보장
                # - 주입 창을 purge_sec만큼 연장해 과충전분(구내용물)을 '전부' 토출
                #   → 시린지 잔량 0 → 볼륨 추적 꼬임 없음 (각 펌프는 자기 fill 소진 시
                #     부피 추적으로 정확히 정지하므로 채널별 시약량 = inject_vol_i 유지)
                # - inj_i (시린지~합류) + mixing(합류~반응기): 플러그 헤드 도달이 그만큼
                #   늦어짐 → t_head에 pre-plug 시간 가산
                # - 모든 구간이 0이면 기존 동작과 완전 동일 (하위호환)
                # @codesyncer-decision: 연속 실험의 라인 상태 추적 (carryover 결함 fix)
                # 물리적 사실:
                #  - inlet 라인(바이알→12way)은 '포트 전용'이며, 한 번 흡입하면
                #    그 포트의 시약으로 차서 남음 (다음 step에서 구내용물이 아님!)
                #  - valve_pump 구간(12way→3way→시린지)은 '공용'이며, 매 step의
                #    push 용매 충전이 이 구간을 용매로 치환함
                # 따라서 보정량(=시린지에 과충전할 '비시약' 부피)은:
                #    첫 사용 포트:   inlet + valve_pump
                #    재사용 포트:    valve_pump 만
                # 기존(항상 inlet+valve_pump 보정)은 재사용 step에서 inlet만큼
                # 시약 과주입 + pre-plug 과대평가(플러그 헤드를 타이머가 놓침)였음.
                if not hasattr(self, "_primed_ports"):
                    self._primed_ports = {}
                # @codesyncer-decision: purge 안전계수 (계면 혼합 희석 가드)
                # 시린지 안에서 '구내용물'과 시약의 계면이 섞이므로, 정확히 src만
                # 퍼지하면 플러그 머리가 혼합대만큼 희석됨. factor>1.0이면 그만큼
                # 시약을 추가 희생(waste행)하여 수집창 시작 시점의 full-strength 보장.
                _purge_factor = float(self.cfg.config_data.get("system_params", {})
                                      .get("line_purge_factor", 1.0) or 1.0)
                _purge_factor = max(1.0, min(3.0, _purge_factor))
                line_src = {}
                line_inj = {}
                for _p in flows.keys():
                    _l1 = float(getattr(self.cfg, "line_vol_inlet", {}).get(_p, 0.0) or 0.0)
                    _l2 = (float(getattr(self.cfg, "line_vol_valve_pump", {}).get(_p, 0.0) or 0.0)
                           + float(getattr(self.cfg, "selector_internal_vol", {}).get(_p, 0.0) or 0.0))
                    _port = int(inlet_ports.get(_p, 2))
                    _primed = self._primed_ports.setdefault(_p, set())
                    line_src[_p] = (_l2 + (0.0 if _port in _primed else _l1)) * _purge_factor
                    # @codesyncer-decision(2026-08-19 사용자 확인 — 3way 내부 재분류):
                    #   주입 타이밍 데드볼륨 = 3way→합류 배관(tube_vol_pump_merge)만.
                    #   3way '자체' 내부(51µL)는 Phase-0 가 용매로 채워도, 그 뒤의
                    #   시약 장전(흡인)이 같은 공통 통로를 시약으로 재충전한 채 주입을
                    #   맞는다(장전이 프리필 마지막 단계, 재치환 프라임은 희석 때문에
                    #   불가). 즉 시약 출발선 = 3way 출구 → 마개는 pump_merge 뿐.
                    #   구 회계(내부 포함)는 선두를 펌프당 51µL/f 만큼 늦게 예측
                    #   (18:34 실런 31s 조기의 ~5s 성분). 앞단(line_src)은 종전대로
                    #   리필 과충전/퍼지 전용 (사용자 물리 요구, 2026-07-06).
                    line_inj[_p] = float(
                        getattr(self.cfg, "line_vol_pump_merge", {}).get(_p, 0.0) or 0.0)
                mixing_vol = float(getattr(self.cfg, "mixing_line_dead_vol", 0.0) or 0.0)

                # @codesyncer-decision: T-junction 캐스케이드 + 퍼지 순서 모델은
                #   _compute_plug_timing(순수 함수, test_deadvol_timing.py 검증)으로 추출.
                #   purge_order: fifo(기본·기존 동작) | lifo(이상적 시린지 후입선출) —
                #   염료 캘리브레이션 결과에 따라 system_params 로 선택.
                _purge_order = str(self.cfg.config_data.get("system_params", {})
                                   .get("purge_order", "fifo") or "fifo").lower()
                _ordered = [p for p in getattr(self.cfg, "ACTIVE_PUMPS", list(flows.keys()))
                            if p in flows]
                for _p in flows.keys():
                    if _p not in _ordered:
                        _ordered.append(_p)
                _tj_vols = getattr(self.cfg, "tjunction_line_vols", {}) or {}
                _tj_entry = getattr(self.cfg, "tjunction_entry_map", {}) or None
                purge_sec, pre_sec, deficit_vol, stagger_offsets = self._compute_plug_timing(
                    flows, _ordered, line_src, line_inj, _tj_vols, _purge_order,
                    entry_map=_tj_entry)
                deficit_sec = (deficit_vol / total_flow) * 60.0 if total_flow > 0 else 0.0
                # @codesyncer-decision(2026-08-13, 사용자 확정 — 소스퍼지 무효화):
                #   구 모델의 도징 창 연장(inject+purge)은 '리필 과충전분(세척액)이
                #   시약보다 먼저 토출된다'(FIFO)는 가정의 배출 시간이었다. 신 워크플로
                #   (배럴 비움 + 시약 딱 흡입 + 정량 Phase-0)에선 과충전이 존재하지
                #   않아, 연장분은 빈 시린지 공회전 → 자동정지 게이트 강제정지의 원인
                #   (2026-08-13 실런 로그). lifo(=흡입/주입 왕복 물리: 마지막 흡입분
                #   =시약이 3way 앞에서 먼저 출발)에서는 연장 없이 inject_sec 만 —
                #   시약 꼬리는 정확히 3way 에 착지하고 이후 수송은 푸시가 담당.
                #   fifo(레거시 설정)는 기존 동작 유지.
                _lifo = str(_purge_order or "fifo").lower() == "lifo"
                dosing_sec = inject_sec + (0.0 if _lifo else purge_sec)
                if purge_sec > 0 or mixing_vol > 0:
                    self._log(
                        f"  [DeadVol] order={_purge_order}, src purge "
                        f"{'미적용(lifo)' if _lifo else f'+{purge_sec:.1f}s'}, "
                        f"pre-plug {pre_sec:.1f}s, mixing {mixing_vol:.3f}mL"
                    )
                self._emit_status(f"Step {exp_id}/{total_steps}: preparing")
                self._log(
                    f"Step {exp_id} Start | temp={target_temp:.1f}C vol={target_vol:.3f}mL "
                    f"flow={total_flow:.3f}mL/min tubes={num_tubes}"
                )

                # Collector pre-move
                if frac_settings.get("enabled", True) and self.collector and getattr(self.collector, "is_connected", False):
                    # 병렬 호밍이 아직 진행 중이면 완료 대기 (가열이 끝난 시점이라 이미 완료됐을 확률 높음)
                    if getattr(self, "_homing_thread", None) and self._homing_thread.is_alive():
                        self._emit_status(f"Step {exp_id}: waiting for collector homing")
                        self._homing_thread.join(timeout=90)
                        if self._homing_thread.is_alive():
                            raise SafetyError("Collector homing timeout (90s)")
                        self._homing_thread = None
                    # @codesyncer(P1.5): compensated 모드 평시 파킹 = WASH 좌표 —
                    #   수집 전 니들이 첫 웰 위에서 대기하지 않고(드립/오염 방지),
                    #   HEAD 후 첫 Δ 구간의 '이전 라인 내용물'도 WASH 가 수취한다.
                    #   첫 웰 진입은 타이머의 shifted 이벤트가 담당. 실패 시 legacy 폴백.
                    _park_wash = (getattr(self, "collect_line_mode", "legacy") == "compensated"
                                  and hasattr(self.collector, "move_to_wash"))
                    if _park_wash:
                        try:
                            ret = self.collector.move_to_wash()
                            ok = ret[0] if isinstance(ret, tuple) else bool(ret)
                            if not ok:
                                self._log(f"  [Collector] WASH 파킹 실패 — legacy pre-move 폴백: "
                                          f"{ret[1] if isinstance(ret, tuple) and len(ret) > 1 else ret}")
                                _park_wash = False
                            else:
                                self._log("  [Collector] pre-move → WASH (compensated 파킹)")
                        except Exception as exc:
                            self._log(f"  [Collector] WASH 파킹 error — legacy pre-move 폴백: {exc}")
                            _park_wash = False
                    if not _park_wash:
                        try:
                            pos = self.collector.get_position()
                        except Exception:
                            pos = None
                        if pos != self.current_tube:
                            try:
                                self.collector.move_to_tube(self.current_tube)
                            except Exception as exc:
                                raise SafetyError(f"Collector pre-move failed: {exc}")
                    if self.tab_collection:
                        try:
                            self.tab_collection.update_position_display()
                        except Exception:
                            pass

                # Step 1: heating
                self._emit_status(f"Step {exp_id}: heating")
                self.heater.set_temperature(target_temp)
                self._log(f"Heat to {target_temp:.1f}C")

                self._stop_momentary()

                heat_start = time.monotonic()
                while True:
                    self._wait_pause_or_abort("heating")
                    curr_temp = self.heater.get_temperature()
                    if curr_temp is None:
                        curr_temp = 0.0

                    self._emit_status(f"Heating: {curr_temp:.1f}/{target_temp:.1f}C")
                    self._log(f"Heating {curr_temp:.2f}C")

                    if abs(curr_temp - target_temp) <= self.temp_tolerance:
                        break

                    if self.heater_reach_timeout_sec > 0:
                        if (time.monotonic() - heat_start) > self.heater_reach_timeout_sec:
                            raise SafetyError(
                                f"Heater timeout: target {target_temp:.1f}C not reached within "
                                f"{self.heater_reach_timeout_sec:.0f}s"
                            )
                    time.sleep(1.0)

                # @codesyncer-decision(2026-08-13, 사용자 워크플로 개편): Step 1.5
                #   '초기 리필' 삭제 — 구 토폴로지(푸시펌프 없음)에서 Phase-0 프라임용
                #   세척액 3mL 를 미리 받아두던 단계. 신 배관(Solvent→QUAD-1)에선
                #   하류 충전은 푸시가 전담하고, 시약펌프 자기 분기는 프리필 Phase-0 가
                #   '딱 데드볼륨만' 즉석 정량 리필→전량 push 로 채운다(여유분 금지 —
                #   사용자 확정). 리필→세척 순서가 만들던 시린지 피크 6mL(실물 5mL
                #   초과 위험)도 함께 제거. 이제 첫 액체 단계 = 시스템 세척(빈 시린지
                #   가정 성립). 구 동작은 git 이력의 _initial_refill 호출 참조.

                # @codesyncer-decision(2026-08-15, 사용자 확정 — 용어/역할 재정의):
                #   HPLC(Reaxus)는 '반응 후 push' 전용 — prime 관여/용어 금지.
                #   본류 용매 충전은 Prime Phase-1(시린지 port1, 프리필 내부, 스텝1
                #   전용)이 담당한다. 구 'HPLC 다운스트림 프라임'(2026-08-14 하루
                #   존재)은 이 결정으로 폐지 — git 이력 참조.

                # Step 1.8: Push 라인 프라임 (스텝1 전용 — 2026-08-15 사용자 제안)
                # @codesyncer-decision: 실기에서 push 시작 후 유체가 20s 넘게 안
                #   밀리는 증상(= 0.16mL @0.481mL/min)의 원인이 push 라인·헤드의
                #   공기/미충전으로 지목됨. 스텝1에 미리 라인을 용매로 채우고 기포를
                #   Outlet(WASTE)로 밀어낸다. 시린지 세척은 12way 경로(3way=SOURCE)라
                #   본류와 분리 → 병행 스레드로 돌려 추가 시간 0. 프리필(Prime Ph1)이
                #   같은 본류를 쓰므로 그 직전에 join 한다.
                _plp_errs: List[str] = []
                _n2cal_errs: List[str] = []
                _plp_thread = None
                _do_plp = (idx == 0 and self.push_pump is not None)
                # @codesyncer-decision(2026-08-17, 사용자 지시): N2 사전 캘리브 —
                #   호밍·가열과 병행해 ①N2 로 본류 액체 배기 ②센서 공기 원점
                #   캡처(RoboChem OCB350 캘리브 계약의 PC측 등가). PushLinePrime 과
                #   '동시' 실행 금지: 둘 다 본류에 흘리는 작업이라 가스T 에서
                #   용매/가스가 교대로 지나가 '가스 안정' 판정이 영원히 안 남.
                #   → 한 스레드에서 순차: 용매 프라임 먼저, N2 배기·원점을 마지막
                #   (원점 캡처 시점의 관로가 가장 깨끗). 프리필이 본류를 쓰므로
                #   기존 join 지점(프리필 직전)은 그대로.
                _do_n2 = (idx == 0 and bool(
                    (self.cfg.config_data.get("system_params", {})
                     if hasattr(self, "cfg") else {}).get("n2_precal_enabled", False)))
                if _do_plp or _do_n2:
                    def _precal_chain(_pl=_plp_errs, _n2=_n2cal_errs,
                                      _p=_do_plp, _n=_do_n2):
                        if _p:
                            self._push_line_prime(_pl)
                        if _n:
                            self._n2_precal_purge(_n2)
                    _plp_thread = threading.Thread(
                        target=_precal_chain, daemon=True, name="PrecalChain")
                    _plp_thread.start()

                # Step 1.9: 소스라인 기포 퍼지 (gas 브랜치 이식 2026-08-17 — 원작 08-14)
                #   세척·프리필보다 '앞' — 퍼지가 공용 구간(12way→3way→시린지)에
                #   남기는 시약을 뒤따르는 세척이 헹궈낸다. 프리캘 체인(본류·push)
                #   과는 경로 분리(시린지·12way)라 병행 안전 — COM7 밸브 동시사용은
                #   push 병행세척에서 검증된 패턴. bubble_purge_enabled=false 면 무동작.
                self._source_bubble_purge(inlet_ports, flows)

                # Step 2: wash
                if self._should_run_mode(self.wash_mode, idx, ports_changed):
                    self._emit_status(f"Step {exp_id}/{total_steps}: washing")
                    # 세척 분리(2026-08-20): 스텝1 = 초기 세척(initial_wash_*),
                    # 스텝2+ 포트변경 세척 = 스텝간(interstep_wash_*) 파라미터
                    self._execute_system_wash(flows, initial=(idx == 0))
                else:
                    self._log(f"Step {exp_id}: wash skipped (mode={self.wash_mode})")

                # Step 3.5: prefill (Prime Phase-0/1 + 시약 장전)
                # @codesyncer-decision(2026-08-15, 사용자 확정 — 용어/게이트 재정의):
                #   Prime = 시린지가 port 1 용매로 채우는 것. 두 종류:
                #     Phase-0 = 분기 데드볼륨만 — 스텝1·포트변경 시만
                #       (동일 포트 연속 스텝은 분기가 직전 스텝 상태 그대로라 불필요)
                #     Phase-1 = 본류(반응기) total 볼륨 충전, 압력 안정성 — 스텝1 전용
                #   시약 장전은 매 스텝 필수 ('딱 흡입' — 구 prefill 전체 스킵은
                #   스텝2+ 빈 시린지 주입 지뢰였음). HPLC 는 push 전용 — prime 불관여.
                # 프리캘 체인 join — Prime Ph1 과 본류가 겹치므로 반드시 선행 완료
                if _plp_thread is not None:
                    while _plp_thread.is_alive():
                        self._check_abort()
                        _plp_thread.join(timeout=1.0)
                    if _plp_errs:
                        self._log(f"  [PushLinePrime] ⚠ 실패: {'; '.join(_plp_errs)} — "
                                  f"push 시작 지연(기포) 가능성 남음")
                    if _n2cal_errs:
                        self._log(f"  [N2Precal] ⚠ 미완: {'; '.join(_n2cal_errs)} — "
                                  f"원점값 없이 진행 (런 타이밍 무영향)")

                _run_phase0 = (idx == 0) or ports_changed
                _run_phase1 = (idx == 0)
                self._smart_prefill_logic(inlet_ports, flows, f_prefill, target_vol, total_flow, line_src,
                                          inlet_vials=exp.get("inlet_vials", {}),
                                          run_phase0=_run_phase0,
                                          run_phase1=_run_phase1)
                # 흡입이 실제 수행됨 → 해당 포트의 inlet 라인은 이제 시약으로 충전됨
                for _p in flows.keys():
                    self._primed_ports.setdefault(_p, set()).add(int(inlet_ports.get(_p, 2)))

                # Step 4: injection (reagent ports)
                # @codesyncer-decision: Outlet valve + Collector 제어를 별도 Timer thread로 위임.
                # Main thread의 펌프 로직은 그대로 유지. Timer는 injection_start 기준
                # monotonic 경과 타이밍에 valve/collector 이벤트 발동 (서로 다른 COM 포트).
                inject_start = time.monotonic()
                # _log()의 [T+] prefix 기준점 — 매 step의 injection 시작 시 리셋
                self.injection_start_ts = inject_start
                self._log(f"Step {exp_id}: injection START")
                self._emit_status(f"Step {exp_id}/{total_steps}: injection")
                reagent_sources = {p_name: int(inlet_ports.get(p_name, 1)) for p_name in flows.keys()}
                self._switch_valves_for_phase(reagent_sources)

                # ── 타이머 이벤트 빌드 ─────────────────────────────
                # @codesyncer-decision: t_head_sec 결정 우선순위 (주석 현행화 2026-08-13
                #   — 구 주석 'reactor/F만'은 코드와 불일치했음)
                #   1. system_params.outlet_switch_delay_sec — 실측값(색소 테스트 등) 최우선
                #   2. 기본 = (reactor + mixing + post)/총유량 + pre_sec(주입경로 도달,
                #      lifo=분기+정션만) + deficit (+compliance) — 전 구간 실측 반영
                #      (2026-08-12~13: reactor 2.4002 / mixing 0.0954 / post 0.2066)
                sp_cfg = self.cfg.config_data.get("system_params", {}) if hasattr(self, "cfg") else {}
                override_delay = sp_cfg.get("outlet_switch_delay_sec")
                if override_delay is not None and float(override_delay) > 0:
                    t_head_sec = float(override_delay)
                    self._log(f"  [Timer] HEAD delay from config: {t_head_sec:.1f}s")
                else:
                    # @codesyncer-decision: HEAD 기본식에 post-reactor(반응기 출구→
                    #   아웃렛밸브) 구간 포함 — Outlet 전환은 헤드가 '밸브'에 도달할 때
                    #   발동해야 하므로 물리적으로 필수 (2026-07-06 반영). 값이 과대한
                    #   구성은 post_reactor_vol_ml 캘리브레이션 또는 실측 override 사용.
                    _post_vol = float(getattr(self, "vol_post_common", 0.0) or 0.0)
                    t_head_sec = (((self.vol_reactor + mixing_vol + _post_vol) / total_flow) * 60.0
                                  if total_flow > 0 else 0.0)
                    t_head_sec += pre_sec + deficit_sec  # pre-plug 지연 + 비대칭 유속저하 보정
                    # @codesyncer-decision: 유체 압축성/시스템 컴플라이언스 보정
                    # 펌프 시작 시 초기 토출분 V_c가 가압(배관 팽창·씰 압축·기포 압축)에
                    # 저장되어 액체 전선이 V_c만큼 지연됨 → HEAD를 등가 시간만큼 늦춤.
                    # (정지 시 감압 유출은 재가압과 상쇄되어 1차 자기상쇄 — pause/refill
                    #  전이는 추가 보정 불필요. outlet_switch_delay_sec 실측 사용 시
                    #  실측값에 이미 포함되므로 이 분기에서만 가산)
                    _vc = float(self.cfg.config_data.get("system_params", {})
                                .get("system_compliance_vol_ml", 0.0) or 0.0)
                    if _vc > 0 and total_flow > 0:
                        t_head_sec += (_vc / total_flow) * 60.0
                        self._log(f"  [Timer] compliance +{(_vc / total_flow) * 60.0:.1f}s (V_c={_vc:.3f}mL)")
                    self._log(
                        f"  [Timer] HEAD delay {t_head_sec:.1f}s = "
                        f"(reactor {self.vol_reactor:.3f} + mixing {mixing_vol:.3f} "
                        f"+ post {_post_vol:.3f} mL)/{total_flow:.3f}mL/min "
                        f"+ pre {pre_sec:.1f}s + deficit {deficit_sec:.1f}s"
                    )

                # @codesyncer-decision: push_pump(HPLC) 활성 시 수집 스케줄이 달라짐
                #   - push_vol = 1.1 × reactor_vol (라인 세척 10% 여유 포함)
                #   - HEAD 이후 흐르는 총 volume = target_vol + 0.1 × reactor_vol
                #   - wash tube 별도 이동 없음 (마지막 tube가 10% 여유분 흡수)
                push_pump_active = self.push_pump is not None
                if push_pump_active:
                    # @codesyncer-decision(2026-08-24 사용자 관찰 — 실런 16:16에서
                    #   수집 종료 후 생성물 꼬리가 소량 잔류): 분산 꼬리 여유를
                    #   하드코딩 10%×reactor 에 더해 config 로 가감할 수 있게 함.
                    #   collect_sec 가 늘면 push_sec(일반식)·terminal WASTE 도
                    #   자동 연장 — 부피수지 항등 유지. ceil 로 웰이 1개 늘 수 있음.
                    _tail_extra = float(sp_cfg.get("collect_tail_extra_ml", 0.0) or 0.0)
                    _collect_vol_total = (target_vol + 0.1 * float(self.vol_reactor)
                                          + _tail_extra)
                    if _tail_extra > 0:
                        self._log(f"  [Timer] 수집 꼬리 여유 +{_tail_extra:.2f}mL "
                                  f"(collect_tail_extra_ml) — 분산 꼬리 회수용")
                    _num_collect_tubes = max(1, math.ceil(_collect_vol_total / vol_per_tube))
                    _collect_flush_vol = 0.0  # 별도 wash tube 없음
                else:
                    _collect_flush_vol = float(self.vol_collection)
                    _num_collect_tubes = max(1, math.ceil(target_vol / vol_per_tube))
                tube_sec = vol_per_tube / total_flow * 60.0 if total_flow > 0 else 0.0
                if push_pump_active:
                    # Timer 기준 collection 지속 시간 (pumping-elapsed)
                    collect_sec = _collect_vol_total / total_flow * 60.0 if total_flow > 0 else 0.0
                    flush_sec = 0.0
                else:
                    collect_sec = target_vol / total_flow * 60.0 if total_flow > 0 else 0.0
                    flush_sec = _collect_flush_vol / total_flow * 60.0 if total_flow > 0 else 0.0
                _first_tube = int(self.current_tube)
                _wash_tube = _first_tube + _num_collect_tubes
                _collector_enabled = (
                    frac_settings.get("enabled", True)
                    and self.collector
                    and getattr(self.collector, "is_connected", False)
                )

                # ── P1.5 수집라인 매핑 모드 ─────────────────────────
                # compensated: 니들 이벤트 +Δ(라인 통과 지연) / HPLC 분기 flush 부여 /
                # 세척 배출 WASH 좌표 (수락 기준: test_collect_line_mapping.py)
                _line_comp = (getattr(self, "collect_line_mode", "legacy") == "compensated")
                _has_wash_port = bool(_collector_enabled
                                      and hasattr(self.collector, "move_to_wash"))
                if _line_comp and push_pump_active and total_flow > 0:
                    # HPLC 경로는 flush_sec=0(라인 세척 부재 → stale 순환오염)이었음 —
                    # flush 를 vol_collection 로 부여하면 push_sec/WASTE/대기예산이
                    # flush_sec 경유로 자동 연장된다.
                    flush_sec = (float(self.vol_collection) / total_flow) * 60.0
                _line_delay_sec = ((float(self.vol_collection) / total_flow) * 60.0
                                   if (_line_comp and total_flow > 0) else 0.0)
                if _line_delay_sec > 0:
                    self._log(f"  [CollectLine] compensated: 니들 이벤트 +{_line_delay_sec:.1f}s "
                              f"(라인 {self.vol_collection:.2f}mL / {total_flow:.3f}mL/min), "
                              f"배출={'WASH포트' if _has_wash_port else 'wash tube(폴백)'}")
                # legacy 경로 wash tube 오버플로 경고 — flush(=라인 1볼륨)가 웰 용량 초과
                if (not push_pump_active and flush_sec > 0
                        and not (_line_comp and _has_wash_port)
                        and _collect_flush_vol > vol_per_tube):
                    self._log(f"  ⚠ wash tube 오버플로 위험: flush {_collect_flush_vol:.2f}mL > "
                              f"웰 용량 {vol_per_tube:.2f}mL — 스플래시/교차오염 가능. "
                              f"collect_line_mode=compensated(WASH포트 배출) 권장")

                # @codesyncer(검증 2026-08-12, C1): 무보호 단발 set_position → 재시도+
                #   에러 승격 헬퍼로 교체 (fault-masking 규약: Outlet 은 시끄럽게)
                def _valve_to_collect():
                    self._outlet_set_safe(2, "collect")

                def _valve_to_waste():
                    self._outlet_set_safe(1, "terminal waste")

                def _well_name(num):
                    """tube 번호 → well ID 문자열 (Plate96이면 A_A1 등, 아니면 Tube N)"""
                    if self.collector and hasattr(self.collector, 'get_well_id'):
                        return self.collector.get_well_id(num)
                    return f"Tube {num}"

                def _move_tube(num):
                    if not _collector_enabled:
                        return
                    try:
                        ret = self.collector.move_to_tube(num)
                        # (ok, msg) 관례 — ok=False(M400 timeout 등)도 소리내어 기록
                        ok = ret[0] if isinstance(ret, tuple) else bool(ret)
                        if not ok:
                            msg = ret[1] if isinstance(ret, tuple) and len(ret) > 1 else ret
                            self._log(f"  [Timer] ⚠ collector move {_well_name(num)} 미확인: {msg}")
                            self._collector_alarm(f"웰 이동({_well_name(num)}) 미확인", msg)
                        if self.tab_collection:
                            try:
                                self.tab_collection.update_position_display()
                            except Exception:
                                pass
                    except Exception as exc:
                        self._log(f"  [Timer] collector move {_well_name(num)} error: {exc}")
                        self._collector_alarm(f"웰 이동({_well_name(num)})", exc)

                def _move_wash():
                    """전용 WASH 좌표로 이동 (compensated 라인 flush 배출/파킹)."""
                    if not _collector_enabled:
                        return
                    try:
                        if hasattr(self.collector, "move_to_wash"):
                            ret = self.collector.move_to_wash()
                            ok = ret[0] if isinstance(ret, tuple) else bool(ret)
                            if not ok:
                                msg = ret[1] if isinstance(ret, tuple) and len(ret) > 1 else ret
                                self._log(f"  [Timer] ⚠ WASH 이동 미확인: {msg}")
                                self._collector_alarm("WASH 이동 미확인", msg)
                        else:
                            self._log("  [Timer] ⚠ move_to_wash 미지원 — WASH 이동 생략")
                    except Exception as exc:
                        self._log(f"  [Timer] WASH 이동 error: {exc}")
                        self._collector_alarm("WASH 이동", exc)

                # @codesyncer(P1): 이벤트에 lane/meta 부여 — valve(Outlet, COM7)와
                #   collector(plate96, COM15)는 물리 버스가 달라 병렬 집행이 안전.
                #   이동 이벤트의 guard_* 콜러블은 move-while-waste 가드용(옵션).
                timer_events: List[tuple] = []
                _move_meta = {"kind": "move",
                              "guard_waste": _valve_to_waste,
                              "guard_restore": _valve_to_collect}
                # @codesyncer-decision(2026-08-15, 사용자 확정 — 수집라인 선헹굼):
                #   Outlet→COLLECT 를 선단 도달(t_head)보다 collect_preflush_vol_ml
                #   만큼 앞당긴다. 선단 '앞' 신선한 용매가 수집라인을 통과해
                #   WASH(수집라인 폐기 좌표)로 빠지므로, 제품이 도착하기 전에
                #   라인이 세정된다. 제품 손실 없음(어차피 니들이 WASH 위).
                # - WASH 포트가 있는 compensated 모드에서만 유효 — legacy(니들이
                #   첫 웰에 파킹)에서 앞당기면 선헹굼 용매가 첫 웰로 들어가므로
                #   적용하지 않고 경고만 남긴다.
                # - t_head 를 넘어서 앞당길 수 없도록 클램프(음수 시각 방지).
                # - 니들 이동/수집 종료/WASTE/push 시간은 전부 불변 — 밸브 전환
                #   시각만 이동한다(수집 창 정의는 t_head 기준 유지).
                _preflush_vol = float(sp_cfg.get("collect_preflush_vol_ml", 0.0) or 0.0)
                _preflush_sec = 0.0
                if _preflush_vol > 0 and total_flow > 0:
                    if _line_comp and _has_wash_port:
                        _preflush_sec = min((_preflush_vol / total_flow) * 60.0,
                                            float(t_head_sec))
                        self._log(
                            f"  [CollectLine] 선헹굼 {_preflush_vol:.3f}mL → "
                            f"Outlet→COLLECT {_preflush_sec:.1f}s 조기 전환 "
                            f"(@ {t_head_sec - _preflush_sec:.1f}s, 배출=WASH포트)")
                    else:
                        self._log(
                            f"  ⚠ 선헹굼 {_preflush_vol:.3f}mL 미적용 — "
                            f"collect_line_mode=compensated + WASH 포트 필요 "
                            f"(legacy 는 선헹굼 용매가 첫 웰로 유입)")
                _t_collect_sec = max(0.0, float(t_head_sec) - _preflush_sec)

                # HEAD 도달 — Outlet 전환(밸브 기준 시각, 선헹굼만큼 조기). 니들(웰)
                # 이벤트는 compensated 모드에서 +Δ(_line_delay_sec) 시프트 — 니들 유출이
                # 수집라인 부피만큼 늦게 시작하는 물리 반영 (legacy 는 Δ=0 동일).
                timer_events.append((_t_collect_sec, "Outlet→COLLECT", _valve_to_collect,
                                     "valve", {"kind": "collect"}))
                if _collector_enabled:
                    timer_events.append(
                        (t_head_sec + _line_delay_sec, f"Move → {_well_name(_first_tube)}",
                         lambda n=_first_tube: _move_tube(n), "collector", _move_meta)
                    )
                    # 후속 수집 well 이동
                    for i in range(1, _num_collect_tubes):
                        tube_num = _first_tube + i
                        timer_events.append(
                            (t_head_sec + _line_delay_sec + i * tube_sec,
                             f"Move → {_well_name(tube_num)}",
                             lambda n=tube_num: _move_tube(n), "collector", _move_meta)
                        )
                    # 라인 flush 배출 위치 — compensated+WASH포트: 전용 좌표(웰 소모 0,
                    # 제품 꼬리는 shifted 웰 창이 이미 수취). 그 외: legacy wash tube
                    # (compensated 폴백 포함 — 웰 1개 소모 유지, HPLC legacy 는 flush 없음)
                    if _line_comp and _has_wash_port and flush_sec > 0:
                        timer_events.append(
                            (t_head_sec + _line_delay_sec + collect_sec,
                             "Move → WASH (line flush)",
                             _move_wash, "collector", _move_meta)
                        )
                    elif flush_sec > 0 and not push_pump_active:
                        timer_events.append(
                            (t_head_sec + _line_delay_sec + collect_sec,
                             f"Move → {_well_name(_wash_tube)} (wash)",
                             lambda n=_wash_tube: _move_tube(n), "collector", _move_meta)
                        )
                # 수집 종료 → Outlet WASTE (밸브 기준 시각 유지, terminal — 가드 복원 금지)
                timer_events.append(
                    (t_head_sec + collect_sec + flush_sec, "Outlet→WASTE", _valve_to_waste,
                     "valve", {"kind": "waste", "terminal": True})
                )

                # ── N2 마커 스케줄 (inj_marker_mode) ──────────────────────
                # @codesyncer-decision(2026-08-18 사용자 확정 — 브래킷):
                #   "bracket" = 전단마커를 펌핑경과 pre_sec(화합물 선두가 가스T 도달),
                #   후단마커를 dosing_sec+pre_sec(꼬리 도달)에 발사 — 마커가 슬러그
                #   양끝에 밀착해 센서2가 경계를 직접 본다(RoboChem 브래킷 등가).
                #   타이머 이벤트(lane "marker")로 스케줄 → pause 중 발사 금지가
                #   공짜로 성립(펌핑경과 클록). 레인 워커가 발사 동안(≈inj_marker_sec)
                #   블로킹되지만 marker 전용 레인이라 다른 레인 무영향.
                #   "t0" = 구 방식(도징 개시 동시 발사, 기록 전용) — 갭=pre_sec 를
                #   분석에서 더해 판독. inj_marker_enabled=true 하위호환 매핑.
                _mk_errs: List[str] = []
                _mk_mode = str(sp_cfg.get("inj_marker_mode", "") or "").lower()
                if not _mk_mode:
                    _mk_mode = ("t0" if bool(sp_cfg.get("inj_marker_enabled", False))
                                else "off")
                if _mk_mode not in ("off", "t0", "bracket", "parked"):
                    self._log(f"  [InjMarker] ⚠ 알 수 없는 inj_marker_mode "
                              f"'{_mk_mode}' — off 처리")
                    _mk_mode = "off"
                if _mk_mode != "off" and self.mfc is None:
                    self._log("  [InjMarker] ⚠ MFC 미배정 — 마커 비활성")
                    _mk_mode = "off"
                if _mk_mode == "bracket":
                    timer_events.append(
                        (pre_sec, "N2 marker FRONT",
                         lambda _e=_mk_errs: self._fire_n2_marker(
                             _e, "front(선단 밀착)"),
                         "marker", {"kind": "marker"}))
                    timer_events.append(
                        (dosing_sec + pre_sec, "N2 marker REAR",
                         lambda _e=_mk_errs: self._fire_n2_marker(
                             _e, "rear(꼬리 밀착)", rear=True),
                         "marker", {"kind": "marker"}))
                    self._log(f"  [InjMarker] bracket 스케줄 — front @ {pre_sec:.1f}s, "
                              f"rear @ {dosing_sec + pre_sec:.1f}s (펌핑경과)")
                elif _mk_mode == "parked":
                    # @codesyncer-decision(2026-08-19 사용자 확정 — 정지선 발사):
                    #   공류 주입의 세그멘터 쪼개짐 회피. 발사는 타이머가 아니라
                    #   '정지 국면' 두 곳에서 동기 호출: 전단=주입 직전(장전 완료,
                    #   전 펌프 정지), 후단=주입종료~push 무유량 갭. 마커는 가스T에
                    #   주차됐다가 흐름 재개와 함께 출발 — 게이트 판독은
                    #   '마커 꼬리 에지 + pre_sec'.
                    self._log(f"  [InjMarker] parked 스케줄 — front=주입 직전 정지선, "
                              f"rear=주입종료 갭 (판독 = 마커꼬리 + pre {pre_sec:.1f}s)")

                # 이전 step의 타이머가 남아있다면 정리
                if self._collection_timer is not None:
                    self._collection_timer.stop(timeout=0.5)
                self._collection_timer = CollectionTimer(
                    self, timer_events,
                    lane_leads={"collector": getattr(self, "collector_move_lead_sec", 0.0)},
                    waste_guard=getattr(self, "collector_move_waste_guard", False),
                )
                self._collection_timer.start()
                # @codesyncer-decision: 생성 직후 pause → injection dosing의
                #   on_pumps_started 콜백에서 resume (분취 타이밍 버그 fix #4)
                # - 기존: timer.start()가 펌프 start보다 수 초 앞서 기준점을 잡아
                #   HEAD/well 이동/Outlet→WASTE가 전부 조기 발동 (제품 일부 waste 유실)
                self._collection_timer.pause()

                # ── HEAD 도달 실측 프로브 (RoboChem 센서구동 이식) ──────────────
                # @codesyncer-decision(2026-08-15): 표준 경로에 개루프 교차검증을
                #   붙인다. 반응기 출구~아웃렛 밸브 사이 OPB 센서가 t_head 를 실측
                #   대조하고, mode="anchor" 면 실측 엣지로 타이머를 재앵커한다.
                #   기본 "off" — 켜지 않으면 동작·성능 모두 종전과 동일.
                _probe_prev = getattr(self, "_head_probe", None)
                if _probe_prev is not None:
                    try:
                        _probe_prev.stop()
                    except Exception:
                        pass
                self._head_probe = None

                # ── 마커 브래킷 분취 게이트 (marker_gate_mode, 2026-08-18) ──
                # 전단마커 꼬리(G→L)=화합물 선두 → 타이머 재앵커(클램프),
                # 후단마커 머리(L→G)=꼬리 → WASTE 절단. 실패는 전부 시간제 폴백.
                _gate_prev = getattr(self, "_marker_gate", None)
                if _gate_prev is not None:
                    try:
                        _gate_prev.stop()
                    except Exception:
                        pass
                self._marker_gate = None
                _gm = str(sp_cfg.get("marker_gate_mode", "off") or "off").lower()
                if _gm in ("observe", "gate"):
                    if _mk_mode not in ("bracket", "parked"):
                        self._log("  [MarkerGate] ⚠ inj_marker_mode 가 bracket/parked "
                                  "아님 — 게이트 비활성 (마커 없이는 경계를 볼 수 없음)")
                        _gm = "off"
                    elif self.phase_sensor is None:
                        self._log("  [MarkerGate] ⚠ 위상센서 없음 — 게이트 비활성")
                        _gm = "off"
                    elif total_flow <= 0:
                        _gm = "off"
                if _gm in ("observe", "gate"):
                    _g_s2o = float(sp_cfg.get("sensor_to_outlet_vol_ml", 0.0) or 0.0)
                    _g_vlag = (_g_s2o / total_flow) * 60.0
                    # @codesyncer-decision(2026-08-24 사용자 확정 — 전단도 센서1):
                    #   실런 2회에서 전단 마커가 반응기 통과 중 파편화(센서2 전부
                    #   기각, 244.7s 재현·슬립 -59.8s) — 전단 감시를 센서1
                    #   (reactor_in, 반응기 진입 전 온전 플러그)로 이동. 'collect'
                    #   설정 시 구(센서2 직측) 동작으로 복귀.
                    #   (코드 기본 = 'collect' 하위호환 — 실기 JSON 이 'reactor_in' 지정)
                    _g_front = str(sp_cfg.get("marker_gate_front_sensor_key",
                                              "collect") or "collect")
                    _g_tjs1 = float(sp_cfg.get("tjunction_to_sensor1_vol_ml", 0.0583) or 0.0)
                    if _g_front == "collect":
                        _g_texp = max(0.0, float(t_head_sec) - _g_vlag)
                        _g_s1s2 = 0.0
                    else:
                        # 센서1 기준 선두 예상 = 주입경로 pre + 가스T→센서1 수송
                        _g_texp = float(pre_sec) + (_g_tjs1 / total_flow) * 60.0
                        # S1→S2 모델 수송(액체 기준) = (t_head − pre) − (tjS1+s2o)/F
                        _g_s1s2 = max(0.0, (float(t_head_sec) - float(pre_sec))
                                      - ((_g_tjs1 + _g_s2o) / total_flow) * 60.0)
                    try:
                        self._marker_gate = MarkerCollectGate(
                            self, self.phase_sensor,
                            str(sp_cfg.get("head_probe_sensor_key", "collect")
                                or "collect"),
                            self._collection_timer, _g_texp, dosing_sec,
                            window_sec=float(
                                sp_cfg.get("marker_gate_window_sec", 0.0)
                                or sp_cfg.get("head_probe_window_sec", 0.0) or 60.0),
                            mode=_gm,
                            valve_lag_sec=_g_vlag,
                            confirm_sec=float(
                                sp_cfg.get("head_probe_confirm_sec", 1.0) or 0.0),
                            max_early_sec=float(
                                sp_cfg.get("marker_gate_max_early_sec", 10.0) or 0.0),
                            style=_mk_mode,
                            offset_sec=pre_sec,
                            min_gas_sec=float(
                                sp_cfg.get("marker_gate_min_gas_sec", 2.0) or 0.0),
                            rear_key=str(
                                sp_cfg.get("marker_gate_rear_sensor_key",
                                           "reactor_in") or "reactor_in"),
                            front_key=_g_front,
                            s1s2_transit_sec=_g_s1s2,
                        )
                        self._marker_gate.start()
                    except Exception as _ge:
                        self._marker_gate = None
                        self._log(f"  [MarkerGate] 기동 실패 — 시간제 폴백: {_ge}")

                _pm = str(sp_cfg.get("head_probe_mode", "off") or "off").lower()
                # P1(센서 소유권): read_event 는 소비형 큐 — 게이트와 프로브가 동시에
                # 읽으면 서로의 엣지를 훔친다. 게이트 무장 시 프로브는 강등.
                if self._marker_gate is not None and _pm in ("observe", "anchor"):
                    self._log("  [HeadProbe] MarkerGate 무장 중 — read_event 이중 소비 "
                              "방지를 위해 이 스텝에서는 비활성")
                    _pm = "off"
                # @codesyncer-risk: anchor 는 수집 창 전체를 이동시키므로 '밀어주는 쪽'도
                #   같이 늘어나야 한다. HPLC push 는 루프에서 applied_shift 를 읽어 창을
                #   연장하지만, 레거시(시린지) push 는 _execute_smart_dosing 에 고정
                #   duration 으로 들어가 중간 연장이 불가능하다 → 재앵커 시 수집 꼬리가
                #   무유량이 된다. 그 조합에서는 anchor 를 observe 로 강등한다(측정은 유지).
                if _pm == "anchor" and not push_pump_active:
                    _pm = "observe"
                    self._log("  [HeadProbe] ⚠ 레거시(시린지) push 경로에서는 push 창을 "
                              "중간 연장할 수 없어 anchor → observe 로 강등합니다 "
                              "(측정은 계속, 제어는 기존 타이밍 유지)")
                if _pm in ("observe", "anchor") and self.phase_sensor is not None and total_flow > 0:
                    # 센서는 아웃렛 밸브보다 (sensor→outlet 부피)/F 만큼 앞선다
                    _s2o = float(sp_cfg.get("sensor_to_outlet_vol_ml", 0.0) or 0.0)
                    _valve_lag = (_s2o / total_flow) * 60.0
                    _t_exp_sensor = max(0.0, float(t_head_sec) - _valve_lag)
                    try:
                        self._head_probe = HeadArrivalProbe(
                            self, self.phase_sensor,
                            str(sp_cfg.get("head_probe_sensor_key", "collect") or "collect"),
                            self._collection_timer, _t_exp_sensor,
                            window_sec=float(sp_cfg.get("head_probe_window_sec", 150.0) or 150.0),
                            mode=_pm,
                            adc_delta=float(sp_cfg.get("head_probe_adc_delta", 0.0) or 0.0),
                            valve_lag_sec=_valve_lag,
                            confirm_sec=float(sp_cfg.get("head_probe_confirm_sec", 1.0) or 0.0),
                        )
                        self._head_probe.start()
                    except Exception as _pe:
                        self._head_probe = None
                        self._log(f"  [HeadProbe] 기동 실패 — 타이밍 폴백: {_pe}")
                elif _pm in ("observe", "anchor") and self.phase_sensor is None:
                    self._log("  [HeadProbe] ⚠ head_probe_mode 설정됐으나 위상센서 없음 — 건너뜀")

                _wash_label = ("WASH포트" if (_line_comp and _has_wash_port and flush_sec > 0)
                               else (_well_name(_wash_tube)
                                     if (flush_sec > 0 and not push_pump_active) else "없음"))
                _pf_label = (f", Outlet 조기전환 @ {_t_collect_sec:.1f}s "
                             f"(선헹굼 {_preflush_sec:.1f}s)" if _preflush_sec > 0 else "")
                if push_pump_active:
                    self._log(
                        f"  [Timer] scheduled (HPLC push): HEAD @ {t_head_sec:.1f}s{_pf_label}, "
                        f"wells {_well_name(_first_tube)}..{_well_name(_first_tube + _num_collect_tubes - 1)}"
                        f"{f' (니들 +{_line_delay_sec:.1f}s)' if _line_delay_sec > 0 else ''}, "
                        f"wash {_wash_label}, end @ {t_head_sec + collect_sec + flush_sec:.1f}s "
                        f"(collect_vol={target_vol + 0.1 * self.vol_reactor:.3f}mL, {_num_collect_tubes} tubes)"
                    )
                else:
                    self._log(
                        f"  [Timer] scheduled: HEAD @ {t_head_sec:.1f}s{_pf_label}, "
                        f"wells {_well_name(_first_tube)}..{_well_name(_first_tube + _num_collect_tubes - 1)}"
                        f"{f' (니들 +{_line_delay_sec:.1f}s)' if _line_delay_sec > 0 else ''}, "
                        f"wash {_wash_label}, end @ {t_head_sec + collect_sec + flush_sec:.1f}s"
                    )
                # ────────────────────────────────────────────────

                # @codesyncer-decision: injection은 allow_refill=False
                # - prefill에서 정확히 계산된 시약을 전량 주입
                # - 중간 리필하면 시약 희석, 시린지에 남은 시약이 용매와 섞임
                # - 주입 완료 후 transit에서 용매로 밀어줌

                # N2 마커 t0 모드 발사 — 도징 개시(펌프 스타트 확인)와 동시.
                #   '마커 꼬리~선단 갭 = pre_sec' 판독은 t0 모드 전용. bracket 모드는
                #   위 타이머 이벤트(lane "marker")가 발사를 전담하므로 여기선 무동작.
                #   콜백은 도징 모니터 루프 안 — 블로킹 금지, 데몬 스레드 위임.
                def _on_inject_started(_resume=self._collection_timer.resume,
                                       _errs=_mk_errs, _pre=pre_sec,
                                       _fire=(_mk_mode == "t0")):
                    _resume()
                    if _fire:
                        threading.Thread(
                            target=self._fire_n2_marker,
                            args=(_errs, "front(t0)",
                                  f"후단센서 마커꼬리 + pre {_pre:.1f}s = 시약 선단"),
                            daemon=True, name="InjMarker").start()

                # parked 전단마커 — 주입 직전 '정지선' 발사: 장전 완료로 전 펌프
                # 정지, 본류 무유량 → 마커가 가스T 에 단일 플러그로 주차된다.
                # 동기 호출(2~3s 블로킹)은 정지 국면이라 무해. 타이머도 아직 pause.
                if _mk_mode == "parked":
                    self._fire_n2_marker(
                        _mk_errs, "front(parked)",
                        f"정지선 주차 — 게이트 판독 = 마커꼬리 + pre {pre_sec:.1f}s")

                self._execute_smart_dosing(
                    flows,
                    total_vol_ml=None,
                    duration_sec=dosing_sec,
                    source_port_map=reagent_sources,
                    step_name=f"S{exp_id}-Injection",
                    allow_refill=False,
                    on_pumps_started=_on_inject_started,
                    start_offsets=stagger_offsets,
                )
                if _mk_errs:
                    self._log(f"  [InjMarker] ⚠ 마커 미완({'; '.join(_mk_errs)}) — "
                              "이 스텝의 마커 기반 경계 실측은 신뢰 불가")

                # Step 4.5a: 주입 후 처리 — 경로별 분기 (2026-08-14 사용자 확정)
                # Timer pause — 이 구간 pump 정지로 flow 없음 → Timer 발동 시간 미뤄줌
                if self._collection_timer is not None:
                    self._collection_timer.pause()
                self._stop_momentary()

                # parked 후단마커 — 주입종료~push 무유량 갭에서 '정지선' 발사.
                # 마커는 가스T 에 주차, 주입경로에 남은 pre_sec 분량의 화합물이
                # 마커 '뒤'에 따라온다 → 게이트 절단은 마커꼬리+pre (유실 방지).
                if _mk_mode == "parked":
                    self._fire_n2_marker(
                        _mk_errs, "rear(parked)",
                        f"갭 주차 — 1차 판독 = 전단센서 + pre {pre_sec:.1f}s + 전단실측수송",
                        rear=True)
                    _mg_now = getattr(self, "_marker_gate", None)
                    if _mg_now is not None:
                        _mg_now.note_rear_departed()   # 발사 시점 앵커 통지
                if not push_pump_active:
                    # legacy(시린지 push) 경로: 잔량을 reactor로 prime (기존 동작).
                    # line_src 편도 축소(2026-08-14)로 잔량은 ~0.15mL 수준 → 미소 바이어스.
                    prime_names = []
                    for p_name in flows.keys():
                        pump = self.pumps.get(p_name)
                        if _is_smart_pump(pump):
                            if float(getattr(pump, "current_vol", 0.0)) > 0.1 and hasattr(pump, "prime_prepare"):
                                self._wait_pause_or_abort("post-injection prime")
                                if pump.prime_prepare():
                                    prime_names.append(p_name)
                    if prime_names:
                        self._log(f"  Post-injection prime: {', '.join(prime_names)}")
                        self._sequential_trigger(prime_names, "prime_trigger")
                        self._run_complete_threads(prime_names, "prime_complete", "post-injection prime")

                    # 게이트③(잔량제거): 주입+prime 후 시약 잔량 실측 — 카운터(>0.1 게이트)가
                    #   못 보는 물리 잔량(=시약 미토출 → 수율 영향 + 용매 리필 오염원) 검출.
                    #   기본 purge(2026-07-29) = 리액터 방향 — 기존 post-inject prime 과 동일
                    #   의미론(미토출 시약을 늦게라도 반응 스트림에 전달)의 센서 폐루프판.
                    #   주의: 타이머 pause 중 소량 전진(유예 후 잔량은 통상 수십 µL)이
                    #   pumping-elapsed 에 미계상 — 기존 prime 과 동일 클래스의 미소 바이어스.
                    _g3 = self._level_gate(list(flows.keys()), "post_inject",
                                           f"S{exp_id} 주입후", discharge="reactor")
                    _g3_bad = {p: v for p, (ok, v) in _g3.items() if (v is not None and not ok)}
                    if _g3_bad:
                        exp["level_residual_post_inject_ul"] = {
                            p: round(v, 1) for p, v in _g3_bad.items()}
                else:
                    # @codesyncer-decision(2026-08-14, 사용자 확정): HPLC push 경로에서
                    #   구 post-injection prime(잔량→reactor) 폐지 — 타이머 pause 중
                    #   무계상 전진(2026-08-14 실런: 2.0mL×2펌프=4.0mL, 수송부피 3.5mL
                    #   초과)으로 제품 전량이 Outlet=WASTE 상태에서 유실되던 원인.
                    #   잔량(라인 구내용물)은 push 병행 세척의 첫 배출(전량→12way 폐기,
                    #   _push_parallel_wash)이 처리한다. 게이트③(reactor 방향 purge)도
                    #   같은 이유로 이 경로에선 생략 — 잔량은 '기대되는' 상태이며
                    #   폐기 배출이 곧 잔량 제거다.
                    self._log("  Post-injection prime 생략 (HPLC push) — "
                              "잔량은 push 병행 세척이 12way 폐기로 배출")

                # @codesyncer-decision: push_pump(HPLC) 활성 시 Step 4.5b(solvent refill) 제거.
                #   Syringe는 post-inject prime 이후 정지 상태 유지, HPLC가 push 담당.
                #   Check valve가 역류 방지 → syringe 앞단 밸브 전환 불필요.
                if push_pump_active:
                    # @codesyncer-decision: HPLC push 시간을 '타이머 종점' 일반식으로 산출
                    # - 기존: push_vol = 1.1×R 하드코딩 — 데드볼륨 보정(pre-plug, mixing)으로
                    #   t_head가 커지면 push가 수집 종료 전에 끝나 흐름 정지 → 분획 어긋남
                    # - 일반식: push_sec = (t_head + collect + flush) − dosing_sec
                    #   (펌핑 총량이 타이머 종점 부피와 항등) — 보정이 0이면 기존 1.1R/F와
                    #   정확히 동일한 값으로 환원됨 (하위호환)
                    push_sec_hplc = max(0.0, (float(t_head_sec) + float(collect_sec)
                                              + float(flush_sec)) - float(dosing_sec))
                    push_vol_hplc = total_flow * push_sec_hplc / 60.0
                    num_collect_tubes = _num_collect_tubes

                    self._log(
                        f"Step {exp_id}: HPLC push | vol={push_vol_hplc:.3f}mL @ {total_flow:.3f}mL/min "
                        f"= {push_sec_hplc:.1f}s, collection={num_collect_tubes} tubes"
                    )
                    self._emit_status(f"Step {exp_id}/{total_steps}: pushing (HPLC)")

                    try:
                        self.push_pump.set_flow(total_flow)
                        self.push_pump.start()
                    except Exception as exc:
                        self._log(f"  [PushPump] start failed: {exc}")
                        raise

                    # Timer resume — HPLC start 명령이 실제 전송된 직후에 재개
                    # @codesyncer-decision: 기존엔 set_flow/start보다 먼저 resume해
                    #   start 실패·통신 지연 동안 가짜 pumping 시간이 누적됐음
                    if self._collection_timer is not None:
                        self._collection_timer.resume()

                    # @codesyncer-decision(2026-08-14, 사용자 확정 워크플로): push 가
                    #   반응기를 미는 동안 시린지는 병행 세척(잔량 폐기+내부 세척) —
                    #   다음 스텝의 시스템 세척/prime 시간이 통째로 제거된다.
                    #   Chemyx RS-485(COM9)와 Reaxus 는 별도 버스라 동시 통신 안전.
                    _wash_errs: List[str] = []
                    _wash_thread = threading.Thread(
                        target=self._push_parallel_wash, args=(flows, _wash_errs),
                        daemon=True, name=f"S{exp_id}-PushWash")
                    _wash_thread.start()

                    # pause/abort를 감시하면서 push 지속
                    # @codesyncer-decision: pause 시 Timer도 함께 pause — HPLC가 멈춘 동안
                    #   Timer가 계속 카운트하면 Outlet→WASTE가 잘못된 시점에 발동됨
                    push_start = time.monotonic()
                    _push_ext_logged = False
                    while True:
                        elapsed = time.monotonic() - push_start
                        # @codesyncer-decision(2026-08-15): HEAD 프로브 재앵커와 push 창의
                        #   결합. push 는 CollectionTimer 와 다른 시계(wall)로 도는 별개
                        #   루프다. anchor 가 타이머를 +Δ 뒤로 밀면 수집 종료도 +Δ 밀리는데
                        #   push 를 연장하지 않으면 마지막 Δ 구간이 '무유량'이 되어 제품이
                        #   정지한 채 웰 경계만 지나간다(분획 어긋남). 매 루프에서 적용된
                        #   shift 를 읽어 push 창을 같이 늘린다. Δ<0(조기 도달)은 늘리지
                        #   않는다 — 수집은 앞당겨져 이미 끝나므로 여분 push 는 무해하다.
                        _hp = getattr(self, "_head_probe", None)
                        _mg = getattr(self, "_marker_gate", None)
                        # MarkerGate 재앵커도 push 연장에 반영 (probe 와 동일 결합)
                        _ext = max(
                            (max(0.0, float(getattr(_hp, "applied_shift", 0.0) or 0.0))
                             if _hp else 0.0),
                            (max(0.0, float(getattr(_mg, "applied_shift", 0.0) or 0.0))
                             if _mg else 0.0))
                        if _ext > 0 and not _push_ext_logged:
                            _push_ext_logged = True
                            self._log(f"  [Push] HEAD 재앵커 +{_ext:.1f}s 반영 — "
                                      f"push {push_sec_hplc:.1f}s → {push_sec_hplc + _ext:.1f}s "
                                      f"(수집 끝까지 유량 유지)")
                        if elapsed >= push_sec_hplc + _ext:
                            break
                        self._check_abort()
                        if not self.pause_event.is_set():
                            try:
                                self.push_pump.stop()
                            except Exception:
                                pass
                            if self._collection_timer is not None:
                                self._collection_timer.pause()
                            self._emit_status(f"S{exp_id}-Push: paused")
                            self.pause_event.wait()
                            self._check_abort()
                            # resume — 남은 시간 기준으로 start 재개
                            # @codesyncer(검증 2026-08-12): 재시작 실패를 삼키고 타이머를
                            #   재개하면 정지된 액체 기둥에 대해 모든 경계(터미널 WASTE
                            #   포함)가 발화 — 무유량 제품 유실이 '정상 완료'로 보고되는
                            #   경로. 1회 재시도, 실패 시 sig_error + 타이머 pause 유지.
                            _push_restarted = False
                            for _att in (1, 2):
                                try:
                                    self.push_pump.set_flow(total_flow)
                                    self.push_pump.start()
                                    _push_restarted = True
                                    break
                                except Exception as _pe:
                                    self._log(f"  ⚠ Push 재시작 실패({_att}/2): {_pe}")
                                    if _att == 1:
                                        time.sleep(0.3)
                                    else:
                                        try:
                                            self.signals.sig_error.emit(
                                                f"S{exp_id}-Push 재시작 실패 — 흐름 정지 상태, "
                                                f"타이머 일시정지 유지. 펌프 확인 필요: {_pe}")
                                        except Exception:
                                            pass
                            if _push_restarted and self._collection_timer is not None:
                                self._collection_timer.resume()
                            push_start = time.monotonic() - elapsed  # 경과 유지
                        # 진행률 송출
                        pct = min(100.0, (elapsed / push_sec_hplc) * 100.0) if push_sec_hplc > 0 else 100.0
                        self._emit_phase(f"S{exp_id}-Push", pct)
                        time.sleep(min(0.5, max(0.0, push_sec_hplc - elapsed)))

                    try:
                        self.push_pump.stop()
                    except Exception as exc:
                        self._log(f"  [PushPump] stop failed: {exc}")
                    self._emit_phase(f"S{exp_id}-Push", 100.0)

                    # 병행 세척 완료 대기 — 통상 push 창(수 분)보다 훨씬 짧아 즉시 반환.
                    # 미완이면 abort 감시하며 대기 (다음 스텝 시약 흡입 전 완료 보장).
                    while _wash_thread.is_alive():
                        self._check_abort()
                        _wash_thread.join(timeout=1.0)
                    if _wash_errs:
                        self._log(f"  [PushWash] ⚠ 병행 세척 미완/실패: "
                                  f"{'; '.join(_wash_errs)} — 다음 스텝 전 확인 필요")
                        try:
                            self.signals.sig_error.emit(
                                f"S{exp_id} push 병행 세척 실패: {'; '.join(_wash_errs)}")
                        except Exception:
                            pass

                else:
                    # Step 4.5b: 용매 충전 — transit+collection+LineWash 총 볼륨을 균등 분배
                    #   total_push_vol = v_zone2_total + collect_flush_vol
                    #   → 한 번 리필 후 연속 dosing (중간 리필/stop 없음)
                    transit_vol = max(0.0, v_zone2_total - target_vol)
                    collect_flush_vol = float(self.vol_collection) * 1.0
                    solvent_sources = {p: 1 for p in flows.keys()}

                    num_pumps = max(1, len([p for p in flows.keys()
                                            if _is_smart_pump(self.pumps.get(p))]))
                    # @codesyncer-decision: push 충전을 '실제 push 시간' 기준으로 산출
                    # - t_head가 데드볼륨 보정으로 커지면 transit이 min_transit으로
                    #   연장되는데, 기존 충전(zone2+flush 고정)은 그걸 모름 → 고갈
                    # - 여기서 transit을 선계산해 총 push 부피 = F × push_sec/60로 충전
                    _base_transit = (transit_vol / total_flow) * 60.0 if total_flow > 0 else 0.0
                    _min_transit = max(0.0, float(t_head_sec) - float(dosing_sec))
                    _planned_transit = max(_base_transit, _min_transit)
                    _planned_push_sec = (_planned_transit
                                         + (target_vol / total_flow) * 60.0
                                         + (collect_flush_vol / total_flow) * 60.0) if total_flow > 0 else 0.0
                    total_push_vol = total_flow * _planned_push_sec / 60.0

                    smart_refill_names = []
                    for p_name in flows.keys():
                        pump = self.pumps.get(p_name)
                        if _is_smart_pump(pump):
                            self._wait_pause_or_abort("solvent refill")
                            # @codesyncer-decision: 용매 충전을 '유속 비례'로 분배 (push 고갈 버그 fix)
                            # - 기존: total_push_vol/num_pumps 균등 분배 → push는 시간 구동이라
                            #   펌프별 실제 소비량 = flow_i × push_sec. 유속이 비균등하면
                            #   빠른 펌프가 push 도중 고갈(자동정지) → 총유속이 명목값 아래로
                            #   추락하는데 Timer는 명목 유속 가정 → 분획 경계 어긋남/제품 유실.
                            # - 수정: fill_i = (flow_i/total_flow) × total_push_vol — prefill의
                            #   inject_vol 분배와 동일 원리. 모든 펌프가 push 종료와 동시 소진.
                            #   (순차 start 지연만큼 미세 잔량이 남는 쪽이 고갈보다 안전)
                            pump_flow = float(flows.get(p_name, 0.0))
                            share = (pump_flow / total_flow) if total_flow > 0 else (1.0 / num_pumps)
                            fill_vol = min(total_push_vol * share, pump.capacity)
                            self._log(
                                f"  {p_name}: solvent refill → {fill_vol:.2f}mL "
                                f"(flow비례 {share:.2f} × push_total {total_push_vol:.2f}mL)"
                            )
                            if pump.refill_prepare(1, volume=fill_vol):
                                smart_refill_names.append(p_name)
                    self._sequential_trigger(smart_refill_names, "refill_trigger")

                    self._run_complete_threads(
                        smart_refill_names, "refill_complete", "solvent refill",
                        log_prefix="Solvent: ")

                    # @codesyncer-decision: timer resume를 여기서 하지 않음 —
                    #   아래 Push dosing의 on_pumps_started 콜백에서 재개.
                    #   (기존: refill 직후 resume → 밸브 전환 0.3s+ 순차 start 격차가
                    #    가짜 pumping 시간으로 누적 → step마다 이벤트 조기 발동)

                    if self.abort_flag:
                        self._abort_refill_workers(smart_refill_names)
                        raise SafetyError("Sequence aborted by user")

                    # Step 5: transit + collection + line wash — 하나의 연속 dosing
                    # @codesyncer-decision: 3단계를 통합하여 연속 pumping
                    #   - Timer thread가 wall-clock 기준으로 valve 전환 + well 이동 담당
                    #   - 펌프는 전체 push 구간을 한 번에 연속 주입 (stop/start 없음)
                    #   - allow_refill=False: 시린지 빈 펌프는 Chemyx 자동 정지, 나머지 계속
                    #   - 중간 리필 시 Timer 동기화 깨짐 방지
                    collect_target_vol = target_vol
                    num_collect_tubes = max(1, math.ceil(collect_target_vol / vol_per_tube))

                    base_transit_sec = (transit_vol / total_flow) * 60.0 if total_flow > 0 else 0.0
                    # 주입 창이 purge로 연장됐으므로 timer 기준점도 dosing_sec
                    min_transit_sec = max(0.0, float(t_head_sec) - float(dosing_sec))
                    transit_sec = max(base_transit_sec, min_transit_sec)
                    collect_sec = (collect_target_vol / total_flow) * 60.0 if total_flow > 0 else 0.0
                    flush_sec_push = (collect_flush_vol / total_flow) * 60.0 if total_flow > 0 else 0.0

                    total_push_sec = transit_sec + collect_sec + flush_sec_push

                    if min_transit_sec > base_transit_sec:
                        self._log(
                            f"  [Transit] extended {base_transit_sec:.1f}s → {transit_sec:.1f}s "
                            f"(Timer HEAD @ {t_head_sec:.1f}s > inject_sec {inject_sec:.1f}s)"
                        )

                    self._log(
                        f"Step {exp_id}: continuous push | "
                        f"transit={transit_sec:.1f}s + collect={collect_sec:.1f}s ({num_collect_tubes} tubes) "
                        f"+ lineWash={flush_sec_push:.1f}s = total {total_push_sec:.1f}s"
                    )

                    self._switch_valves_for_phase(solvent_sources)
                    self._emit_status(f"Step {exp_id}/{total_steps}: pushing (transit+collect+wash)")

                    self._execute_smart_dosing(
                        flows,
                        total_vol_ml=None,
                        duration_sec=total_push_sec,
                        source_port_map=solvent_sources,
                        step_name=f"S{exp_id}-Push",
                        allow_refill=False,
                        on_pumps_started=(
                            self._collection_timer.resume
                            if self._collection_timer is not None else None
                        ),
                    )

                elapsed_min = (time.monotonic() - inject_start) / 60.0
                self._log(f"Step {exp_id}: push complete | total {elapsed_min:.2f} min after injection")

                # 5-2c: Timer의 모든 이벤트 자연 완료 대기 (Outlet→WASTE 포함)
                # - Main pump가 Timer보다 늦게 끝나면 Timer는 이미 완료됨 → 즉시 반환
                # - Main pump가 먼저 끝나면 남은 이벤트(예: Outlet→WASTE) 발동까지 대기
                if self._collection_timer is not None:
                    # @codesyncer-decision: 예산은 timer 자체의 pumping-elapsed 기준
                    #   remaining_sec()으로 계산 (분취 타이밍 버그 fix #5)
                    # - 기존: wall-clock(inject_start) 경과 − pumping-elapsed 종점을
                    #   섞어서 빼는 바람에 prime/refill이 길수록 예산이 5초로 붕괴
                    remaining = self._collection_timer.remaining_sec()
                    wait_budget = max(5.0, remaining + 10.0)
                    if remaining > 0.5:
                        self._emit_status(
                            f"Step {exp_id}: collection 마무리 대기 ({remaining:.0f}s)"
                        )
                    self._collection_timer.wait_finish(timeout=wait_budget)
                    # @codesyncer-risk: timeout으로 join 실패 시 참조만 끊으면
                    #   orphan thread가 다음 step 도중 Outlet/Collector를 조작
                    #   (시리얼 경합 + 분획 오염). 반드시 강제 stop 후 해제.
                    if self._collection_timer.is_alive():
                        self._log(
                            f"  [Timer] WARNING: 이벤트 미완료 상태로 {wait_budget:.0f}s 초과 "
                            f"→ 강제 정지 (orphan thread 방지)"
                        )
                        self._collection_timer.stop(timeout=2.0)
                    self._collection_timer = None

                # HEAD 실측 프로브 결산 — 예측 대비 편차를 스텝 로그에 남긴다.
                # @codesyncer-decision: 여기서만 요약을 찍는다(스텝당 1줄). 이 Δ가
                #   여러 런에서 재현되면 outlet_switch_delay_sec 실측 override 또는
                #   system_compliance_vol_ml 로 고정하는 것이 정석 출구.
                _hp = getattr(self, "_head_probe", None)
                if _hp is not None:
                    try:
                        _hp.stop()
                        self._log(f"  [HeadProbe] 결산 — {_hp.summary()}")
                        self.trace.instant("HeadProbe", "summary", args={
                            "expected_sec": round(_hp.t_exp, 2),
                            "detected_sec": (round(_hp.detected_sec, 2)
                                             if _hp.detected_sec is not None else None),
                            "delta_sec": (round(_hp.delta_sec, 2)
                                          if _hp.delta_sec is not None else None),
                            "detector": _hp.detector, "mode": _hp.mode,
                            "applied_shift_sec": round(_hp.applied_shift, 2),
                        })
                    except Exception:
                        pass
                    self._head_probe = None

                # 마커 게이트 결산 — 슬러그 경계 실측(선두/꼬리/통과시간) 스텝당 1줄
                _mg = getattr(self, "_marker_gate", None)
                if _mg is not None:
                    try:
                        _mg.stop()
                        self._log(f"  [MarkerGate] 결산 — {_mg.summary()}")
                        self.trace.instant("MarkerGate", "summary", args={
                            "expected_sec": round(_mg.t_exp, 2),
                            "front_el": (round(_mg.front_el, 2)
                                         if _mg.front_el is not None else None),
                            "rear_el": (round(_mg.rear_el, 2)
                                        if _mg.rear_el is not None else None),
                            "slug_transit_sec": (round(_mg.slug_transit_sec, 2)
                                                 if _mg.slug_transit_sec is not None
                                                 else None),
                            "applied_shift_sec": round(_mg.applied_shift, 2),
                            "rear_cut": _mg.rear_cut, "mode": _mg.mode,
                        })
                        exp["marker_gate"] = _mg.summary()
                    except Exception:
                        pass
                    self._marker_gate = None

                # @codesyncer(검증 2026-08-12, C1): 게이트④의 'Outlet=WASTE' 전제를 먼저
                #   강제 — 터미널 WASTE 이벤트가 1회 통신 장애로 조용히 실패(워커가 예외
                #   삼킴)하거나 타이머 강제종료로 드레인되면 전제가 거짓이 되어 purge 가
                #   제품 웰로 유입(오버플로/오염). 게이트① F1 수정과 동일 패턴.
                self._outlet_set_safe(1, "step-end")

                # 게이트④(잔량제거): 푸시 종료 후 용매 잔량 실측. 기본 purge —
                #   자동정지 유예로 기다린 뒤에도 남았으면 '같은 스텝 유속'으로 센서 0
                #   될 때까지 리액터 방향 추가 토출(2026-07-29 사용자 지시). 타이머
                #   정리 후 + Outlet=WASTE(위에서 강제)라 추가 토출은 폐액병행, 분획 무영향.
                _g4 = self._level_gate(list(flows.keys()), "push_end",
                                       f"S{exp_id} 푸시후",
                                       discharge="reactor", rates=flows)
                _g4_bad = {p: v for p, (ok, v) in _g4.items() if (v is not None and not ok)}
                if _g4_bad:
                    exp["level_residual_push_end_ul"] = {
                        p: round(v, 1) for p, v in _g4_bad.items()}

                # tube 카운트 업데이트
                # - legacy syringe push: 수집 튜브 + 별도 세척 튜브 1개
                # - HPLC push: 마지막 collect tube가 10% 여유분 흡수 → 세척 튜브 별도 없음
                if frac_settings.get("enabled", True) and self.collector and getattr(self.collector, "is_connected", False):
                    # @codesyncer(P1.5): compensated+WASH포트는 세척 배출이 전용 좌표로
                    #   가므로 플레이트 웰 소모 0 (legacy syringe 경로만 +1 유지)
                    if push_pump_active or (_line_comp and _has_wash_port):
                        self.current_tube += num_collect_tubes
                    else:
                        self.current_tube += num_collect_tubes + 1
                else:
                    self.current_tube += num_collect_tubes

                # (Outlet→WASTE 는 게이트④ 직전으로 이동 — 2026-08-12 C1)
                self._log(f"Step {exp_id} Complete")

                if self.signals:
                    self.signals.sig_progress.emit(exp_id)

            self._sequence_cleanup(sequence_plan)

        except Exception:
            self._sequence_cleanup()
            raise

    def _initial_refill(self, flows: Dict[str, float]):
        # ⚠ 미사용(2026-08-13 워크플로 개편) — 시퀀스 Step 1.5 호출 삭제됨.
        #   Phase-0 정량 리필(_smart_prefill_logic)이 역할을 대체. 레거시 복원용 보존.
        # @codesyncer-decision: prepare(셋업) → trigger(start) → 병렬 대기
        # - Phase 1a: 각 펌프에 순차적으로 밸브+stop+rate+vol 전송 (기계 미동작, 격차 무관)
        # - Phase 1b: START만 연속 전송 (~0.1s/pump, A-C 격차 ≤ 0.3s)
        # - Phase 2: 모든 펌프의 기계 동작 완료를 병렬 대기
        smart_pumps = []

        # Phase 1: prepare(셋업) → trigger(start) 분리
        for p_name in flows.keys():
            pump = self.pumps.get(p_name)
            if _is_smart_pump(pump):
                # @codesyncer-decision: initial refill 은 "port 1 = 세척용매" 라는
                #   외부 12-way 전제의 용매 플러시다. 1-소스 라우팅(NRG internal_valve /
                #   autosampler)은 port 인자를 무시하고 자기 소스(대개 '시약')를 흡인하므로
                #   그대로 두면 매 시퀀스 시약 1시린지가 waste 로 버려진다 → 스킵.
                if self._pump_routing(p_name) != "external_valve":
                    self._log(f"{p_name}: initial refill 생략 (1-소스 라우팅)")
                    continue
                self._wait_pause_or_abort("initial refill")
                init_vol = min(
                    float(getattr(pump, 'wash_volume', pump.capacity)),
                    pump.capacity
                )
                self._log(f"{p_name} initial refill ({init_vol:.1f}mL)")
                if pump.refill_prepare(1, volume=init_vol):
                    smart_pumps.append(p_name)
                else:
                    self._log(f"  [{p_name}] refill 시작 실패")
        self._sequential_trigger(smart_pumps, "refill_trigger")

        # Phase 2+3: 병렬 대기 + 동작 모니터링 (Emergency Stop 인터락 포함)
        self._run_complete_threads(smart_pumps, "refill_complete", "initial refill")

        if self.abort_flag:
            self._abort_refill_workers(smart_pumps)
            raise SafetyError("Sequence aborted by user")

    # ══════════════════════════════════════════════════════════════
    # HTE droplet 모드 — 질소 스페이서 드롭 트레인 (2026-07-06)
    # ══════════════════════════════════════════════════════════════
    # 배관: 펌프들 → T캐스케이드(조성 합류) → [N2 티=MFC] → 리액터 → 아웃렛
    # 원리(RoboChem 스페이서 관례): 스텝 i = 슬러그(조성_i, V_slug) 도징 후
    #   질소 스페이서가 경계를 형성 — 한 실험에서 조성을 바꿔가며 드롭 트레인.
    # 세척 프로토콜: 마지막 슬러그 뒤 [질소 → 용매 플러그 → 질소] (질소 티
    #   상류(합류~티)는 용매 플러그가, 티 하류는 질소+용매가 세척; N2 분지는
    #   기체 전용이라 세척 불요).
    # ── 타이밍 불변식: CollectionTimer 는 '구동 중'(액체 도징 or 질소 주입)
    #   에만 진행 — 프리필/서비스 갭은 열차 정지=타이머 정지. 이벤트 시각은
    #   구동 프로파일 [(rate mL/min, dur s)] 위 부피→시간 변환으로 산출되어
    #   유량이 세그먼트마다 달라도 정확.
    # config(system_params): hte_mode / hte_spacer_vol_ml(0.2) /
    #   hte_gas_equiv_flow_ml_min(스페이서 액체 치환 등가유량; 기본=스텝 총유량,
    #   ★실측 캘리브레이션 항목 — sccm→치환유량은 압력 의존) /
    #   hte_gas_sccm(MFC 설정치, 기본=equiv 값) / hte_wash_solvent_vol_ml(0.5) /
    #   hte_wash_gas_vol_ml(0.3) / hte_wash_port(1=공용매)

    def _collector_alarm(self, what, detail):
        """분취기 이동 실패 경보 (2026-08-26) — 강조 로그 + 경고음 3회.

        @codesyncer-decision: EXP_008 사고(08-25)에서 이동 실패가 로그 한 줄로만
        남아 3.4h 수집 전량이 폐기로 유실됨. 무인 런에서도 인지되도록 가청 경보
        추가. 시퀀스는 중단하지 않음(타이머는 폴백으로 계속) — 밸브 수집 자체는
        정상일 수 있고, 여기서 멈추면 이후 웰까지 전부 잃는다.
        """
        self._log(f"  ⚠⚠⚠ [분취기 경보] {what} 실패 — 수집이 잘못된 위치(폐기/이전 웰)로 갈 수 있음! ({str(detail)[:100]})")

        def _beep():
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1500, 400)
                    time.sleep(0.15)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True, name="collector-alarm").start()

    def _log_phase_transition(self, key, old, new, adc):
        """OPB 상전이(0↔1) 로그 훅 — 드라이버 리더 스레드에서 호출됨.

        @codesyncer(2026-08-18, 사용자 요청): 전이 시각을 시스템 로그(런 중엔
        CSV Time_s + [T+] prefix 포함)와 Perfetto('PhaseEdge' 트랙)에 남긴다.
        예외는 전부 삼킨다 — 로그 훅이 센서 리더를 죽이면 안 됨."""
        try:
            name = {"reactor_in": "전단센서 INLET",
                    "collect": "후단센서 OUTLET"}.get(key, key)
            desc = "0→1 (기체→액체)" if new == 1 else "1→0 (액체→기체)"
            self._log(f"  [PhaseEdge] {name} {desc}  ADC={int(adc)}")
            self.trace.instant("PhaseEdge", f"{key} {int(old)}→{int(new)}",
                               args={"adc": int(adc), "sensor": key})
        except Exception:
            pass

    def _fire_n2_marker(self, errors: list, label: str, note: str = "",
                        rear: bool = False):
        """N2 마커 1펄스 — 가스T 주입 (2026-08-18, 5단계 센서제어 비전 3번).

        @codesyncer-decision: OPB 는 용매/시약(둘 다 맑은 액체)을 구별 못 한다 —
          보이는 경계는 기/액뿐. N2 펄스가 그 경계를 만든다. 발사 시점은 호출부가
          결정하는 두 모드:
          · bracket(사용자 확정): 타이머 이벤트가 펌핑경과 pre_sec(전단)/
            dosing+pre_sec(후단)에 호출 — 마커가 슬러그 양끝에 밀착, 센서2가
            경계를 직접 봄 → MarkerCollectGate 가 분취 트리거로 소비.
          · t0(구 방식, 기록 전용): 도징 개시 동시 발사 — 갭=pre_sec 를 분석에서
            더해 판독 (마커는 선단보다 pre_sec 앞서 폐기 경로로 지나감).
        - 마커 크기: 10 sccm × 1s ≈ 170 µL(표준환산), 0.3s ≈ 50 µL(RoboChem
          bubble_volume 등가) — 센서 통과폭은 압력 의존이나 '보이면' 충분.
        - 실패 = 경고·스킵(측정 보조일 뿐). MFC OFF 는 finally 보장.
        - 설정: inj_marker_mode(off/t0/bracket, 구 inj_marker_enabled=true→t0)
                / inj_marker_sec(기본 1.0) / inj_marker_sccm(0 = n2_precal 폴백).
          hte_mode 는 제외 (자체 스페이서 체계 보유).
        """
        sp = (self.cfg.config_data.get("system_params", {})
              if hasattr(self, "cfg") else {})
        sccm = (float(sp.get("inj_marker_sccm", 0.0) or 0.0)
                or float(sp.get("n2_precal_sccm", 10.0) or 10.0))
        dur = float(sp.get("inj_marker_sec", 1.0) or 0.0)
        if rear:
            # @codesyncer(2026-08-19 실런): rear 발사 시점은 도징 직후라 라인이
            #   컴플라이언스로 가압 상태 — 저유량(3sccm)이 배압을 못 이겨 마커가
            #   통째로 실종된 사례(15:39 런, 센서1·2 모두 미검출). rear 전용
            #   세기/시간으로 배압 돌파 (0 = 전단과 동일값 폴백).
            sccm = float(sp.get("inj_marker_rear_sccm", 0.0) or 0.0) or sccm
            dur = float(sp.get("inj_marker_rear_sec", 0.0) or 0.0) or dur
        if dur <= 0 or self.mfc is None:
            return
        gas_on = False
        try:
            self._log(f"  [InjMarker] N2 마커 {label} — {sccm:.0f} sccm × {dur:.1f}s"
                      + (f" | {note}" if note else ""))
            self.mfc.set_flow(sccm)
            gas_on = True
            # @codesyncer-decision(2026-08-19 실런, 사용자 승인 — 램프 인지 발사):
            #   18:34 런 실측 — 3sccm 설정인데 펄스 중 실유량 0.01 = MFC 램프업이
            #   펄스(2.5s)보다 느려 마커가 '아예 형성되지 않음'(양 센서 미검출,
            #   선두 예측 틀어짐의 원인). 설정치 80% 도달을 확인한 '뒤부터' dur 를
            #   세면 MFC 응답 특성과 무관하게 마커 부피가 보장된다.
            #   4s 내 미도달 = 미형성 가능성 확정 로그(배압/공급) 후 최선 진행.
            _t_ramp = time.monotonic()
            _ramped = False
            while time.monotonic() - _t_ramp < 4.0:
                self._check_abort()
                if not self.pause_event.is_set():
                    self.mfc.set_flow(0.0)
                    self.pause_event.wait()
                    self._check_abort()
                    self.mfc.set_flow(sccm)
                    _t_ramp = time.monotonic()   # 정지 중 램프 무효 — 재시작
                try:
                    if float(self.mfc.get_flow()) >= sccm * 0.8:
                        _ramped = True
                        break
                except Exception:
                    break
                time.sleep(0.1)
            if _ramped:
                self._log(f"  [InjMarker] 램프 완료 ({time.monotonic() - _t_ramp:.1f}s)"
                          f" — 펄스 {dur:.1f}s 카운트 시작")
            else:
                self._log("  [InjMarker] ⚠ 램프 4s 내 설정치 미도달 — "
                          "마커 미형성 가능 (배압/공급 확인). 펄스는 그대로 진행")
            t0 = time.monotonic()
            _flow_checked = False
            while time.monotonic() - t0 < dur:
                self._check_abort()
                if not self.pause_event.is_set():
                    self.mfc.set_flow(0.0)
                    self.pause_event.wait()
                    self._check_abort()
                    self.mfc.set_flow(sccm)
                # 펄스 중반 실유량 1회 — 배압/공급 문제로 '주입 자체가 안 된'
                # 실종 사례를 발사 시점에 즉시 판정 (15:39 런 교훈)
                if not _flow_checked and time.monotonic() - t0 >= dur * 0.4:
                    _flow_checked = True
                    try:
                        _f = float(self.mfc.get_flow())
                        _mk = ("✓" if _f >= sccm * 0.5 else
                               "⚠ 배압/공급 의심 — 마커 미주입 가능")
                        self._log(f"  [InjMarker] 실유량 {_f:.2f}/{sccm:.1f} sccm  {_mk}")
                    except Exception:
                        pass
                time.sleep(0.05)
            self.mfc.set_flow(0.0)
            gas_on = False
            self._log(f"  [InjMarker] 마커 {label} 주입 완료")
            try:
                self.trace.instant("InjMarker", label, args={
                    "sccm": sccm, "sec": dur})
            except Exception:
                pass
        except SafetyError as e:
            errors.append(f"abort: {e}")
        except Exception as e:
            errors.append(str(e))
            self._log(f"  [InjMarker] ⚠ 마커 {label} 오류: {e}")
        finally:
            if gas_on:
                try:
                    self.mfc.set_flow(0.0)     # 페일세이프 — 가스 방치 금지
                except Exception:
                    pass

    def _outlet_set_safe(self, pos, ctx=""):
        """표준 경로 Outlet 전환 — 1회 재시도, 최종 실패 시 sig_error 승격.

        @codesyncer(검증 2026-08-12, C1): 표준 타이머 이벤트의 Outlet 전환이 무보호
        단발 set_position 이라 1회 통신 장애로 분획 경계가 조용히 소실됐다
        (_hte_outlet 의 C2 수정과 동일 결함). 반환 True=성공."""
        if "Outlet" not in self.valves:
            return False
        for attempt in (1, 2):
            t0 = time.time()
            try:
                self.valves["Outlet"].set_position(pos)
                self.trace.complete("VALVE Outlet", f"→{pos} {ctx}".strip(), t0, time.time() - t0)
                return True
            except Exception as e:
                self.trace.instant("VALVE Outlet", f"FAILED({attempt}/2) →{pos} {ctx}".strip(),
                                   args={"error": str(e)[:200]})
                self._log(f"  [Timer] ⚠ Outlet→{pos} 실패({attempt}/2) {ctx}: {e}")
                if attempt == 1:
                    time.sleep(0.2)
                else:
                    try:
                        self.signals.sig_error.emit(
                            f"Outlet 전환 실패({ctx}) — 분획 경계 이상, 웰 확인 필요: {e}")
                    except Exception:
                        pass
        return False

    def _hte_outlet(self, pos, ctx=""):
        """타이머 스레드용 Outlet 전환 — 1회 재시도, 최종 실패 시 sig_error 승격.

        @codesyncer(C2): 기존엔 collect 액션에서 collector 이동만 try 로 감싸고
        Outlet set_position 은 무보호 — CollectionTimer 가 액션 예외를 로그로만
        삼켜 ACK 실패 = 분획 경계 이벤트가 '조용히' 소실(제품 waste 유실/웰 오염).
        fault-masking 감사 규약(Outlet 은 시끄럽게)에 맞춰 재시도+에러 승격."""
        if "Outlet" not in self.valves:
            return
        for attempt in (1, 2):
            t0 = time.time()
            try:
                self.valves["Outlet"].set_position(pos)
                self.trace.complete("VALVE Outlet", f"→{pos} {ctx}".strip(), t0, time.time() - t0)
                return
            except Exception as e:
                self.trace.instant("VALVE Outlet", f"FAILED({attempt}/2) →{pos} {ctx}".strip(),
                                   args={"error": str(e)[:200]})
                self._log(f"  [HTE-Timer] ⚠ Outlet→{pos} 실패({attempt}/2) {ctx}: {e}")
                if attempt == 1:
                    time.sleep(0.2)
                else:
                    # @codesyncer(감사 2026-07-13 이슈3): sig_error 만으로는 트레인 끝의
                    #   '완주=성공' 판정이 못 봄 → 결함 리스트에 기록해 sig_finished/
                    #   "HTE complete" 를 억제(성공·실패 신호 동시 발신 모순 제거).
                    if not hasattr(self, "_hte_faults"):
                        self._hte_faults = []
                    self._hte_faults.append(f"Outlet 전환 실패({ctx})")
                    try:
                        self.signals.sig_error.emit(
                            f"HTE: Outlet 전환 실패({ctx}) — 분획 경계 이상, 웰 확인 필요: {e}")
                    except Exception:
                        pass

    def _hte_gas_segment(self, sccm, dur_sec, label="N2"):
        """질소 구동 세그먼트 — 시작 시 timer resume, 끝나면 pause.
        pause/abort 대응: 일시정지 시 가스 OFF+타이머 정지(열차 정지 정합)."""
        if dur_sec <= 0:
            return
        self._log(f"  [HTE] {label}: {sccm:.1f} sccm × {dur_sec:.1f}s")
        self.mfc.set_flow(sccm)
        if self._collection_timer is not None:
            self._collection_timer.resume()
        t_left = float(dur_sec)
        # 스팬은 set_flow 성공 후 개시 — set_flow 예외 시 미개시 스팬 누수 방지 (검증 반영)
        self.trace.begin("MFC", label, args={"sccm": round(float(sccm), 2),
                                             "dur_sec": round(float(dur_sec), 1)})
        try:
            while t_left > 0:
                self._check_abort()
                if not self.pause_event.is_set():
                    self.mfc.set_flow(0.0)
                    if self._collection_timer is not None:
                        self._collection_timer.pause()
                    self._wait_pause_or_abort(f"HTE {label}")
                    self.mfc.set_flow(sccm)
                    if self._collection_timer is not None:
                        self._collection_timer.resume()
                t0 = time.monotonic()
                time.sleep(min(0.2, t_left))
                t_left -= time.monotonic() - t0
        finally:
            self.mfc.set_flow(0.0)
            self.trace.end("MFC")
            if self._collection_timer is not None:
                self._collection_timer.pause()

    def _run_hte_droplet(self, plan, sp, f_prefill):
        """HTE droplet 트레인 실행 — 스텝=슬러그, 웰 1개/슬러그."""
        if not plan:
            # @codesyncer(C7): 빈 시퀀스는 hte_build_profile 의 steps[0] IndexError 로
            #   스택트레이스를 내며 죽음 → 명시적 SafetyError 사전 차단.
            raise SafetyError("HTE 모드: 시퀀스가 비어 있음 — 스텝을 추가하세요")
        if self.mfc is None or not getattr(self.mfc, "is_connected", False):
            raise SafetyError("HTE 모드: MFC(질소) 미구성/미연결 — roles.gas 확인")
        if self.push_pump is not None:
            self._log("[HTE] ⚠ push HPLC 는 droplet 모드에서 미사용 (세그멘테이션 보존)")

        v_spacer = float(sp.get("hte_spacer_vol_ml", 0.2) or 0.2)
        v_wash_sol = float(sp.get("hte_wash_solvent_vol_ml", 0.5) or 0.5)
        v_wash_gas = float(sp.get("hte_wash_gas_vol_ml", 0.3) or 0.3)
        wash_port = int(sp.get("hte_wash_port", 1) or 1)
        # 슬러그간 세척 (0=끔): [슬러그|N2|용매|N2|슬러그] — 교차오염→희석 강등
        v_interwash = float(sp.get("hte_interwash_vol_ml", 0.0) or 0.0)
        frac = self._fraction_settings()
        _purge_factor = max(1.0, min(3.0, float(sp.get("line_purge_factor", 1.0) or 1.0)))
        if not hasattr(self, "_primed_ports"):
            self._primed_ports = {}
        # @codesyncer(감사 2026-07-13 이슈3): 트레인 결함 수집기 — 타이머 스레드의
        #   Outlet 전환 최종실패 등이 기록되면 트레인 끝에서 sig_finished 억제.
        self._hte_faults = []

        # ── 스텝 검증 + 구동 프로파일/이벤트 부피 마크 구성 ──
        steps = []
        for idx, exp in enumerate(plan):
            temp, v_slug, _tube, flows, F, _n = self._validate_step_inputs(
                idx + 1, exp, {"enabled": False})   # 슬러그=웰1개, tube 검증 무관
            q_equiv = float(sp.get("hte_gas_equiv_flow_ml_min", 0.0) or 0.0) or F
            steps.append(dict(exp=exp, temp=temp, v_slug=v_slug, flows=flows,
                              F=F, q_equiv=q_equiv,
                              ports={p: int(exp.get("inlet_ports", {}).get(p, 2))
                                     for p in flows}))
        # @codesyncer(#2, 2026-07-13): 스페이서 MFC setpoint 를 '스텝별' q_equiv 에 정렬 —
        #   hte_gas_sccm 미설정 시 기존엔 steps[0] 고정이라 스텝 유량이 다르면
        #   치환 가정(sccm=등가유량)이 그 스텝에서 깨졌음. 명시 설정값은 전역 유지.
        _sccm_fixed = float(sp.get("hte_gas_sccm", 0.0) or 0.0)
        gas_sccm = _sccm_fixed or steps[0]["q_equiv"]

        def _sccm_for(st):
            return _sccm_fixed or st["q_equiv"]

        # @codesyncer(#1 확장, 2026-07-13): 수치 입력 사전 거부 — q_equiv/gas_sccm/
        #   데드볼륨/T-junction 이 NaN·Inf·음수면 프로파일 t_of/마크가 조용히 붕괴.
        for _i, _st in enumerate(steps):
            if not (math.isfinite(_st["q_equiv"]) and _st["q_equiv"] > 0):
                raise SafetyError(
                    f"HTE Step {_i + 1}: gas 등가유량(q_equiv)이 유효하지 않음 ({_st['q_equiv']})")
        if not (math.isfinite(gas_sccm) and gas_sccm > 0):
            raise SafetyError(f"HTE: hte_gas_sccm 이 유효하지 않음 ({gas_sccm})")
        for _label, _dd in (("inlet", getattr(self.cfg, "line_vol_inlet", {})),
                            ("valve_pump", getattr(self.cfg, "line_vol_valve_pump", {})),
                            ("selector", getattr(self.cfg, "selector_internal_vol", {})),
                            ("switcher", getattr(self.cfg, "valve_internal_vol", {})),
                            ("pump_merge", getattr(self.cfg, "line_vol_pump_merge", {}))):
            for _p, _v in (_dd or {}).items():
                _f = float(_v or 0.0)
                if not math.isfinite(_f) or _f < 0:
                    raise SafetyError(f"HTE: 데드볼륨 {_label}[{_p}] 값이 유효하지 않음 ({_v})")
        for _j, _v in (getattr(self.cfg, "tjunction_line_vols", {}) or {}).items():
            _f = float(_v or 0.0)
            if not math.isfinite(_f) or _f < 0:
                raise SafetyError(f"HTE: T-junction[{_j}] 값이 유효하지 않음 ({_v})")

        # @codesyncer(감사 2026-07-13 이슈4): 드롭 트레인은 전 슬러그가 같은 리액터/
        #   히터를 공유 — 기존엔 steps[0] 온도만 조용히 적용(스텝별 온도 무시, 무경고
        #   오작동). 허용오차(temp_tolerance_c) 초과 불일치는 사전 차단해 잘못된
        #   조건의 스크리닝 데이터 생성을 막는다.
        _tol_t = float(sp.get("temp_tolerance_c", 0.3) or 0.3)
        _temps = [st["temp"] for st in steps]
        if max(_temps) - min(_temps) > _tol_t:
            raise SafetyError(
                f"HTE 모드: 스텝 온도 불일치 {sorted(set(round(t, 1) for t in _temps))}°C — "
                "드롭 트레인은 단일 리액터 공유로 스텝별 온도를 지원하지 않음. "
                "모든 스텝을 동일 온도로 설정하세요")

        # @codesyncer(감사 2026-07-13): 웰 '부피' 사전검증 — droplet 모드는 슬러그
        #   전량이 웰 1개로 들어감(분할 수집 없음). 기존 tube_vol 검증은 비-HTE 용 —
        #   plate96 상한(1.5mL) 대비 슬러그 2~6mL 가 조용히 오버플로되던 갭.
        _max_well = getattr(self.collector, "max_volume_per_well_ml", None)
        if _max_well:
            _bad = [(i + 1, st["v_slug"]) for i, st in enumerate(steps)
                    if st["v_slug"] > float(_max_well)]
            if _bad:
                raise SafetyError(
                    "HTE: 슬러그 부피가 웰 용량("
                    f"{float(_max_well):.2f} mL)을 초과 — "
                    + ", ".join(f"Step {i}={v:.2f}mL" for i, v in _bad)
                    + ". droplet 모드는 슬러그=웰 1개(분할 없음) — 반응부피를 줄이세요")

        # @codesyncer(감사 2026-07-13): 가상(Mock) 장비 시끄러운 고지 — 시뮬은 합법이라
        #   차단하지 않되, 실기로 오인한 채 '정상 완료'되는 것을 막기 위해 명시 로그.
        _virt = [n for n, o in (("MFC", self.mfc), ("Collector", self.collector))
                 if o is not None and type(o).__name__.startswith("Mock")]
        _virt += [p for p, o in self.pumps.items()
                  if type(o).__name__.startswith("Mock")]
        if _virt:
            # #11 절충: 기본=시끄러운 고지(시뮬 합법), hte_require_real_hw=true 면 차단
            if bool(sp.get("hte_require_real_hw", False)):
                raise SafetyError(
                    f"HTE: 가상(Mock) 장비 감지 — {', '.join(_virt)}. "
                    "실기 강제 모드(hte_require_real_hw)에서 실행 불가")
            self._log(f"[HTE] ⚠ 가상(Mock) 장비로 실행: {', '.join(_virt)} — "
                      "실기 아님. 실제 수집/토출 없음")
        # C8: gas_equiv 폴백 단위경고 + 센서 미사용(타이밍-only) 고지
        if not float(sp.get("hte_gas_equiv_flow_ml_min", 0.0) or 0.0):
            self._log("[HTE] ⚠ hte_gas_equiv_flow_ml_min 미설정 — 스텝 총유량을 "
                      "치환 등가유량으로 가정 (sccm→액체치환은 압력 의존, 실측 캘리브레이션 권장)")
        if not bool(sp.get("hte_sensor_trigger", False)):
            self._log("[HTE] 타이밍-only 운전(위상센서 미사용) — 가스 구간 등가유량 "
                      "오차가 누적될 수 있음 (hte_sensor_trigger 권장)")

        # @codesyncer(감사 2026-07-13): HTE 수집 마크는 FIFO 배출(퍼지 선행) 가정으로
        #   산출 — lifo 설정 시 슬러그 머리 유실+꼬리 퍼지 오염 가능(미검증 조합).
        if str(sp.get("purge_order", "fifo") or "fifo").lower() == "lifo":
            self._log("[HTE] ⚠ purge_order=lifo — HTE 수집 마크는 FIFO(퍼지 선행) "
                      "가정으로 계산됨. LIFO 실측 검증 전엔 fifo 권장")

        # 프로파일/마크/헤드부피 — 엔진·시뮬 공유 순수 함수(동일성 보장). steps 에
        # v_purge 를 주입한다. dv=구간별 데드볼륨 dict.
        dv = dict(
            inlet=getattr(self.cfg, "line_vol_inlet", {}) or {},
            valve_pump=getattr(self.cfg, "line_vol_valve_pump", {}) or {},
            selector=getattr(self.cfg, "selector_internal_vol", {}) or {},
            switcher=getattr(self.cfg, "valve_internal_vol", {}) or {},
            pump_merge=getattr(self.cfg, "line_vol_pump_merge", {}) or {},
        )
        _plan = hte_build_profile(
            steps, reactor_vol=float(self.vol_reactor),
            mixing=float(getattr(self.cfg, "mixing_line_dead_vol", 0.0) or 0.0),
            post=float(self.vol_post_common), vol_collection=float(self.vol_collection),
            deadvols=dv, active_pumps=list(getattr(self.cfg, "ACTIVE_PUMPS", [])),
            tj=getattr(self.cfg, "tjunction_line_vols", {}) or {},
            tj_entry=getattr(self.cfg, "tjunction_entry_map", {}) or None,
            purge_factor=_purge_factor,
            purge_order=str(sp.get("purge_order", "fifo") or "fifo"),
            override_delay=sp.get("outlet_switch_delay_sec"),
            v_spacer=v_spacer, v_wash_sol=v_wash_sol, v_wash_gas=v_wash_gas,
            primed=self._primed_ports, v_interwash=v_interwash)
        v_head, profile, marks, v_gasB = (
            _plan["v_head"], _plan["profile"], _plan["marks"], _plan["v_gasB"])
        v_push = float(_plan.get("v_push", 0.0))
        qg, Fw = steps[-1]["q_equiv"], steps[-1]["F"]
        # @codesyncer(감사 2026-07-13 이슈1, 정책 c 승인): 스페이서 < 수집라인이면
        #   waste 전환 시 슬러그 꼬리 (수집라인 − v_push)가 밸브→웰 라인에 '좌초'
        #   (WASTE 중 수집분지는 무흐름 스텁) → 다음 COLLECT 때 다음 슬러그가 밀어
        #   다음 웰로 이월(교차오염+회수부족). 기존 경고 로그만으론 정상완료+리포트
        #   저장이라 조용한 데이터 오염 — 2단계 게이트로 교체:
        #   ① 웰이 자기 슬러그를 전혀 못 받는 구성(v_slug+v_push ≤ 수집라인)은
        #      물리적 무의미 데이터(플레이트 한 칸 밀림) — 플래그 무관 무조건 차단.
        #   ② 부분 이월은 기본 차단, system_params.hte_allow_spacer_carryover=true
        #      명시 승인 시에만 진행 + 이월량을 스텝 meta 에 기록(리포트 JSON 포함,
        #      분석 보정용). 근본해결 = hte_spacer_vol_ml ≥ 수집라인 (또는 라인 재실측).
        _coll = float(self.vol_collection)
        _carry = [max(0.0, min(st["v_slug"], _coll - v_push)) for st in steps]
        _full_strand = [i + 1 for i, st in enumerate(steps)
                        if st["v_slug"] + v_push <= _coll]
        if _full_strand:
            raise SafetyError(
                f"HTE: 수집라인({_coll:.2f}mL) ≥ 슬러그+푸시 — Step {_full_strand} 의 웰이 "
                f"자기 슬러그를 전혀 받지 못하고 전량 좌초→다음 웰로 이월됨(플레이트 밀림). "
                f"hte_spacer_vol_ml 을 수집라인({_coll:.2f}mL) 이상으로 증량하거나 "
                f"수집라인 단축/재실측 필요")
        if max(_carry, default=0.0) > 0:
            if not bool(sp.get("hte_allow_spacer_carryover", False)):
                raise SafetyError(
                    f"HTE: 수집라인({_coll:.2f}mL) > 스페이서({v_spacer:.2f}mL) — 슬러그당 "
                    f"최대 {max(_carry):.2f}mL 꼬리가 좌초되어 다음 웰로 이월(교차오염). "
                    f"hte_spacer_vol_ml ≥ {_coll:.2f} 증량 권장. 이월을 감수하고 진행하려면 "
                    f"system_params.hte_allow_spacer_carryover=true 설정")
            for _st, _c in zip(steps, _carry):
                _st["exp"].setdefault("meta", {})["hte_spacer_carryover_ml"] = round(_c, 4)
            self._log(f"[HTE] ⚠ 승인된 꼬리 이월 모드(hte_allow_spacer_carryover): "
                      f"슬러그당 최대 {max(_carry):.2f}mL 가 다음 웰로 이월될 수 있음 — "
                      f"이월량은 각 스텝 meta.hte_spacer_carryover_ml 에 기록(리포트 포함). "
                      f"hte_spacer_vol_ml 증량 권장")

        def t_of(V):
            """구동 프로파일 위 누적부피 V 의 pumping-elapsed 시각."""
            t, v = 0.0, 0.0
            for rate, dur, _k, _i in profile:
                dv = rate * dur / 60.0
                if v + dv >= V:
                    return t + (V - v) / rate * 60.0
                v += dv
                t += dur
            return t

        # 수집기 용량 검증
        if self.collector is not None and getattr(self.collector, "is_connected", False):
            need_last = int(getattr(self, "collector_start_tube", 1)) + len(steps) - 1
            if need_last > self._max_collector_tubes():
                raise SafetyError(f"HTE: 웰 부족 (필요 {need_last})")

        self._log(f"[HTE] {len(steps)} 슬러그 | spacer {v_spacer}mL | v_push {v_push:.2f}mL "
                  f"| interwash {v_interwash}mL "
                  f"| v_head {v_head:.3f}mL | wash N2 {v_wash_gas}→SOL {v_wash_sol}→N2 {v_gasB:.2f}mL")

        try:
            # 호밍/가열 (첫 스텝 온도로 1회)
            if "Outlet" in self.valves:
                self.valves["Outlet"].set_position(1)
            self._switch_all(1)
            self.current_tube = int(getattr(self, "collector_start_tube", 1))
            if self.collector is not None and getattr(self.collector, "is_connected", False):
                self.collector.home()
                self.collector.move_to_tube(self.current_tube)
            self.heater.set_temperature(steps[0]["temp"])
            t0h = time.monotonic()
            tol = float(sp.get("temp_tolerance_c", 0.3) or 0.3)
            while True:
                self._wait_pause_or_abort("HTE heating")
                cur = self.heater.get_temperature() or 0.0
                if abs(cur - steps[0]["temp"]) <= tol:
                    break
                if time.monotonic() - t0h > float(self.cfg.heater_reach_timeout_sec):
                    raise SafetyError("HTE: 가열 타임아웃")
                time.sleep(0.5)

            # 타이머 이벤트 (전 트레인 스케줄 — 결정적)
            events = []
            first_tube = self.current_tube

            def _move_well(tube, ctx):
                """수집기 웰 이동 (타이머 스레드) — 실패는 로그 (outlet 과 달리 이동
                실패는 collect 안전망이 한 번 더 시도)."""
                if self.collector is None or not getattr(self.collector, "is_connected", False):
                    return
                try:
                    ret = self.collector.move_to_tube(tube)
                    ok = ret[0] if isinstance(ret, tuple) else bool(ret)
                    if not ok:
                        self._log(f"  [HTE-Timer] ⚠ 웰 이동 미확인({ctx}): {ret}")
                except Exception as e:
                    self._log(f"  [HTE-Timer] 웰 이동 오류({ctx}): {e}")

            def _mk_collect(i):
                def act():
                    # @codesyncer(C4→순서/C5): ①웰 위치 확인(선이동 실패 시 안전망 이동)
                    #   → ②Outlet=COLLECT. 기존 '밸브 먼저→이동 나중'은 이동 중 드립이
                    #   웰 사이에 뿌려짐. 선이동이 성공했으면 위치확인은 즉시 통과라
                    #   밸브 전환 시각 불변. current_tube 갱신(C5: UI/리포트 스테일 방지).
                    self.current_tube = first_tube + i
                    if self.collector is not None and getattr(self.collector, "is_connected", False):
                        cur = None
                        try:
                            cur = self.collector.get_position()
                        except Exception:
                            pass
                        if cur != first_tube + i:
                            _move_well(first_tube + i, f"collect{i+1} 안전망")
                    self._hte_outlet(2, f"collect well{first_tube+i}")
                return act

            def _mk_waste(next_i):
                def act():
                    self._hte_outlet(1, "waste")
                    # @codesyncer(C3): 다음 웰 '선이동' — 수집이 없는 스페이서 구간에
                    #   이동을 배치해, collect 시점의 블로킹(후속 이벤트 지각)과
                    #   이동 중 드립을 제거. 실패해도 collect 안전망이 재시도.
                    if next_i is not None:
                        _move_well(first_tube + next_i, f"well{first_tube+next_i} 선이동")
                return act

            n_steps = len(steps)
            for V, kind, i in marks:
                if kind == "collect":
                    events.append((t_of(V), f"Slug{i+1}→COLLECT well{first_tube+i}",
                                   _mk_collect(i)))
                else:
                    nxt = i + 1 if (i + 1) < n_steps else None
                    events.append((t_of(V), f"Slug{i+1} 종료→WASTE", _mk_waste(nxt)))

            if self._collection_timer is not None:
                self._collection_timer.stop(timeout=0.5)
            self._collection_timer = CollectionTimer(self, events)
            self._collection_timer.start()
            self._collection_timer.pause()   # 구동 시작 전 정지
            self.injection_start_ts = time.monotonic()

            # ── 하이브리드 트리거 (hte_sensor_trigger): 센서 엣지로 마크 재앵커 ──
            # config: hte_sensor_trigger(bool) / hte_sensor_key("collect") /
            #   hte_sensor_window_sec(0=auto: 최소 스페이서 통과시간의 절반, 1~10s 클램프)
            self._hte_sensor_sync = None
            if bool(sp.get("hte_sensor_trigger", False)):
                s_key = str(sp.get("hte_sensor_key", "collect") or "collect")
                ps = self.phase_sensor
                if ps is None or not getattr(ps, "is_connected", False) \
                        or s_key not in getattr(ps, "sensors", {}):
                    self._log("[HTE-Sensor] ⚠ hte_sensor_trigger 설정됐으나 위상센서 "
                              f"미구성('{s_key}') — 순수 타이밍으로 진행")
                else:
                    t_sp_min = min(v_spacer / st["q_equiv"] * 60.0 for st in steps)
                    w = float(sp.get("hte_sensor_window_sec", 0.0) or 0.0)
                    if w <= 0:
                        w = max(1.0, min(10.0, 0.5 * t_sp_min))
                    exp_edges = sorted(
                        (t_of(V), kind, label)
                        for V, kind, label in _plan.get("edges", []))
                    self._hte_sensor_sync = _HteSensorSync(
                        self, ps, s_key, self._collection_timer, exp_edges, w)
                    self._hte_sensor_sync.start()
                    self._log(f"[HTE-Sensor] 하이브리드 트리거 활성 — 엣지 "
                              f"{len(exp_edges)}개, 창 ±{w:.1f}s")

            # ── 트레인 실행: [프리필(정지)] → 슬러그(구동) → 질소(구동) 반복 ──
            for i, st in enumerate(steps):
                self._emit_status(f"HTE slug {i+1}/{len(steps)}: prefill")
                line_src = {}
                for _p in st["flows"]:
                    _l1 = float(getattr(self.cfg, "line_vol_inlet", {}).get(_p, 0.0) or 0.0)
                    _l2 = (float(getattr(self.cfg, "line_vol_valve_pump", {}).get(_p, 0.0) or 0.0)
                           + float(getattr(self.cfg, "selector_internal_vol", {}).get(_p, 0.0) or 0.0))
                    primed = self._primed_ports.setdefault(_p, set())
                    line_src[_p] = (_l2 + (0.0 if st["ports"][_p] in primed else _l1)) * _purge_factor
                self._smart_prefill_logic(st["ports"], st["flows"], f_prefill,
                                          st["v_slug"], st["F"], line_src,
                                          inlet_vials=st["exp"].get("inlet_vials", {}))
                for _p in st["flows"]:
                    self._primed_ports.setdefault(_p, set()).add(st["ports"][_p])

                self._emit_status(f"HTE slug {i+1}/{len(steps)}: dosing")
                self._switch_valves_for_phase(st["ports"])
                self._execute_smart_dosing(
                    st["flows"], total_vol_ml=None,
                    duration_sec=(st["v_slug"] + st["v_purge"]) / st["F"] * 60.0,
                    source_port_map=st["ports"],
                    step_name=f"HTE-Slug{i+1}", allow_refill=False,
                    on_pumps_started=self._collection_timer.resume,
                )
                self._collection_timer.pause()
                self._emit_status(f"HTE slug {i+1}/{len(steps)}: N2 spacer")
                self._hte_gas_segment(_sccm_for(st), v_spacer / st["q_equiv"] * 60.0,
                                      label=f"spacer{i+1}")

                # ── 슬러그간 세척: [용매 플러그 + N2] (마지막 슬러그 뒤는 트레인-엔드가 담당) ──
                if v_interwash > 0 and (i + 1) < len(steps):
                    self._emit_status(f"HTE slug {i+1}: inter-wash")
                    iw_ports = {p: wash_port for p in st["flows"]}
                    self._smart_prefill_logic(iw_ports, st["flows"], f_prefill,
                                              v_interwash, st["F"],
                                              {p: 0.0 for p in iw_ports})
                    self._switch_valves_for_phase(iw_ports)
                    self._execute_smart_dosing(
                        st["flows"], total_vol_ml=v_interwash,
                        source_port_map=iw_ports,
                        step_name=f"HTE-InterWash{i+1}", allow_refill=False,
                        on_pumps_started=self._collection_timer.resume,
                    )
                    self._collection_timer.pause()
                    self._hte_gas_segment(_sccm_for(st), v_spacer / st["q_equiv"] * 60.0,
                                          label=f"spacer{i+1}b")

            # ── 세척 프로토콜: 질소 → 용매 플러그 → 질소 ──
            self._emit_status("HTE wash: N2")
            self._hte_gas_segment(_sccm_fixed or qg, v_wash_gas / qg * 60.0, label="washN2-A")
            self._emit_status("HTE wash: solvent plug")
            wash_ports = {p: wash_port for p in steps[-1]["flows"]}
            self._smart_prefill_logic(wash_ports, steps[-1]["flows"], f_prefill,
                                      v_wash_sol, Fw, {p: 0.0 for p in wash_ports})
            self._switch_valves_for_phase(wash_ports)
            self._execute_smart_dosing(
                steps[-1]["flows"], total_vol_ml=v_wash_sol,
                source_port_map=wash_ports, step_name="HTE-WashSolvent",
                allow_refill=False,
                on_pumps_started=self._collection_timer.resume,
            )
            self._collection_timer.pause()
            self._emit_status("HTE wash: N2 (final push)")
            self._hte_gas_segment(_sccm_fixed or qg, v_gasB / qg * 60.0, label="washN2-B")

            # 잔여 이벤트 자연 완료 대기
            rem = self._collection_timer.remaining_sec()
            if rem > 0:
                self._log(f"[HTE] ⚠ 이벤트 {rem:.1f}s 잔여 — 최종 질소 연장")
                self._collection_timer.resume()
                self.mfc.set_flow(_sccm_fixed or qg)
                try:
                    self._collection_timer.wait_finish(timeout=rem + 10)
                finally:
                    self.mfc.set_flow(0.0)
            # @codesyncer(C4): 기존엔 wait_finish 타임아웃(블로킹 이동 등으로 이벤트
            #   잔존)이어도 무조건 "complete"+sig_finished — 조용한 부분실패.
            #   잔존 시 sig_error 승격, 완료 신호는 완주 시에만.
            # @codesyncer(감사 2026-07-13 이슈3): 타이머 완주뿐 아니라 트레인 중 기록된
            #   결함(_hte_faults — Outlet 전환 최종실패 등)도 성공 판정에 반영.
            #   기존엔 sig_error(실패) 직후에도 sig_finished(성공)가 나가는 모순.
            self._collection_timer.wait_finish(timeout=5.0)
            _faults = list(getattr(self, "_hte_faults", []))
            if self._collection_timer.is_alive():
                _faults.append("분획 이벤트 미완주(타이머 잔존) — 마지막 웰 경계 어긋남 가능")
            if _faults:
                msg = "HTE: 결함 있는 종료 — " + " / ".join(_faults) + " — 분획 확인 필요"
                self._log(f"[HTE] ⚠ {msg}")
                self._emit_status("HTE finished with faults")
                try:
                    self.signals.sig_error.emit(msg)
                except Exception:
                    pass
            else:
                self._log("[HTE] Train complete")
                self._emit_status("HTE complete")
                self.signals.sig_finished.emit()
        except SafetyError:
            raise
        finally:
            if self._hte_sensor_sync is not None:
                try:
                    self._hte_sensor_sync.stop()
                    self._log(f"[HTE-Sensor] 요약: {self._hte_sensor_sync.summary()}")
                except Exception:
                    pass
            try:
                self.mfc.set_flow(0.0)
            except Exception:
                pass
            self._sequence_cleanup(plan)

    def _sequence_cleanup(self, sequence_plan=None):
        if self._cleanup_done:
            return
        self._cleanup_done = True

        # _log() T+ prefix 비활성화 — 시퀀스 종료 후 후속 메시지에 stale 타이머 안 찍히도록
        self.injection_start_ts = None

        # @codesyncer(감사 2026-07-13 이슈5): abort 는 프리필/도징 도중 라인을 불확정
        #   상태로 남김 — persist_primed_lines=true 캠페인에서 '프라임됨'으로 이월되면
        #   다음 실험이 inlet 퍼지 보정을 건너뛰어 저퍼지/교차오염. abort 시엔 무조건
        #   리셋(정상 완료 이월 정책은 run_sequence 시작부 로직 그대로 유지).
        if getattr(self, "abort_flag", False):
            self._primed_ports = {}

        # 오토샘플러 니들 파킹 (원본 HomeSampler-in-cleanup 관례) —
        # 니들이 vial 에 담긴 채 시퀀스가 끝나지 않도록 보장
        for p_name, coord in (self._sampler_coords or {}).items():
            try:
                coord.park()
            except Exception as e:
                self._log(f"[AS·{p_name}] 파킹 실패 (무시): {e}")

        # Stop collection timer (if still running)
        if self._collection_timer is not None:
            try:
                self._collection_timer.stop(timeout=2.0)
            except Exception:
                pass
            self._collection_timer = None

        # @codesyncer(검증 2026-08-12): abort 시 터미널 WASTE 이벤트가 실행 없이
        #   드레인되어 Outlet 이 COLLECT 로 방치되던 결함 — WASTE 는 이 엔진이 선언한
        #   안전 종단 상태(타이머 도크스트링·워커 주석)인데 Manual E-STOP 외 어떤
        #   경로도 보장하지 않았다. cleanup 은 abort/에러/정상 공용이므로 여기서 강제.
        if "Outlet" in self.valves:
            try:
                self.valves["Outlet"].set_position(1)
                self._log("Cleanup: Outlet→WASTE (안전상태 복귀)")
            except Exception as e:
                self._log(f"⚠ Cleanup: Outlet→WASTE 실패 — 수동 확인 필요: {e}")

        # 호밍 스레드 정리 (abort 시 아직 살아있을 수 있음)
        homing = getattr(self, "_homing_thread", None)
        if homing is not None and homing.is_alive():
            try:
                if self.collector and hasattr(self.collector, 'stop_motion'):
                    self.collector.stop_motion()
            except Exception:
                pass
            homing.join(timeout=5.0)
        self._homing_thread = None

        # Stop all pumps
        for p_obj in self.pumps.values():
            try:
                p_obj.stop()
            except Exception:
                pass

        # @codesyncer(검증 2026-08-12): abort 로 refill_complete 의 finally 에 도달 못한
        #   펌프는 is_refilling=True 로 잔류 → 다음 런 시작 전까지 Manual infuse 가
        #   조용히 무동작(start() 가 is_refilling 이면 return)하고 Deep Wash 는 busy
        #   거부. 펌프 정지 직후인 여기서 해제. _dosing_started 잔류도 같은 취지
        #   (다음 런 재시작 판정이 우연한 target_flow 리셋에 기대던 결합 제거).
        for p_obj in self.pumps.values():
            try:
                if getattr(p_obj, "is_refilling", False):
                    p_obj.is_refilling = False
            except Exception:
                pass
        if hasattr(self, "_dosing_started"):
            try:
                self._dosing_started.clear()
            except Exception:
                pass

        # 게이트⑤(잔량제거): 종료 시 잔량 '기록만' — cleanup 은 abort/에러 경로 공용이라
        #   액추에이션 금지(밸브 상태 불확정, force_action="log"). "시약 X µL 남은 채
        #   종료" 를 로그에 남겨 다음 시퀀스 게이트①(startup 배출)의 근거로 삼는다.
        if (getattr(self, "level_verify_points", {}) or {}).get("seq_end", "log") != "off":
            try:
                _g5 = self._level_gate(list(self.pumps.keys()), "seq_end", "시퀀스종료",
                                       force_action="log")
                _g5_bad = {p: v for p, (ok, v) in (_g5 or {}).items()
                           if (v is not None and not ok)}
                if _g5_bad and getattr(self, "abort_flag", False):
                    # abort 밤샘 보관 경고 (수정 #7): 자동 배출은 금지 경로이므로 안내만
                    self._log("⚠ [레벨센서] abort 종료 — 시린지에 잔량 보관됨: "
                              + ", ".join(f"{p} {v:.0f}µL" for p, v in _g5_bad.items())
                              + " | 장기 방치 시 결정화/부식 주의. 다음 시퀀스 시작 시 "
                                "게이트①이 자동 배출하며, 즉시 비우려면 Manual 탭 세척 사용.")
            except Exception:
                pass

        # Stop push pump (HPLC) if present
        if self.push_pump is not None:
            try:
                self.push_pump.stop()
            except Exception:
                pass

        # Stop heater
        try:
            self.heater.stop()
        except Exception:
            pass

        # Collector: 안전 높이로 상승만 (home은 다음 시퀀스 시작 시 수행)
        if self.collector and getattr(self.collector, "is_connected", False):
            try:
                if hasattr(self.collector, 'goto_safe_height'):
                    self.collector.goto_safe_height()
                    self._log("Collector: Z safe height")
                if self.tab_collection:
                    self.tab_collection.update_position_display()
            except Exception:
                pass

        # Save report only on normal completion
        if sequence_plan and not self.abort_flag:
            try:
                self.save_experiment_report(sequence_plan)
            except Exception as exc:
                print(f"[Error] Report save failed: {exc}")

        # Close csv log (writer 도 함께 무효화 — 닫힌 파일에의 writerow 무음 유실 방지)
        if self.csv_file:
            try:
                self.csv_file.close()
            except Exception:
                pass
            self.csv_file = None
            self.writer = None

        self._emit_status("All steps complete" if not self.abort_flag else "Sequence aborted")

    # ---------------------------------------------------------------------
    @staticmethod
    def compute_bubble_purge_vol(inlet, selector, factor=2.0, refill_floor=0.1):
        """퍼지 1회 흡입량 [mL] — 순수 함수(테스트 대상).

        @codesyncer-decision(2026-08-14, 사용자 지적으로 정정): 요건은 '기포가
          12way 로터를 통과하는 것'이지 시린지 배럴까지 갈 필요는 없다. 되밀 때
          로터 하류(valve_pump·배럴)에 있는 것은 전부 port 12 로 빠지고, 로터
          상류(=시약 포트 인렛)에 남은 것만 갇힌다. 따라서 최소 흡입량 =
              inlet(기포 최대 길이) + selector(로터 통로) = 실측 0.0978 mL
          valve_pump(0.0597)는 불필요 — 초기 구현은 이를 과하게 잡았다.

        factor: 안전 여유. 기본 **2.0** → 0.196mL.
          @codesyncer-decision(2026-08-14, 사용자 실기 관측): 계산상 최소량(1.0)은
          물론 1.5 로도 기포가 완전히 빠지지 않았다. 이론 최소량이 부족한 이유:
            - 기포는 압축성이 있어 흡입 중 팽창 → 되밀 때 수축, 실제 이동거리가
              명목 부피보다 짧다
            - 잔재 공기는 깔끔한 슬러그 하나가 아니라 여러 작은 기포로 분산돼
              이론 길이(=inlet 부피)보다 훨씬 긴 구간에 퍼진다
            - 배관 벽면에 붙은 기포는 전단이 약하면 슬러그와 같이 안 움직인다
          2.0 이면 기포 뒷단이 로터에서 0.098mL 떨어져 valve_pump(0.0597)를 넘어
          배럴 안까지 들어오므로, 되밀기 전 구간에 여유가 생긴다.

        refill_floor: ChemyxSmartPump 의 최소 리필량(기본 0.1mL, system_params
          refill_min_vol_ml 로 조정). 미만이면 **로그 없이 스킵**되어 '고쳤다고
          믿는데 무동작'이 되므로 하한까지 올린다.
        """
        v = (float(inlet) + float(selector)) * max(1.0, float(factor))
        if 0 < v < float(refill_floor):
            v = float(refill_floor)
        return round(v, 4)

    def _source_bubble_purge(self, inlet_ports: Dict[str, int], flows: Dict[str, float]):
        """세척·프리필 '앞'에서 시약 소스라인의 초기 잔재 기포를 12way 로 배출.

        @codesyncer-context(2026-08-14, 사용자 관측 / 2026-08-17 gas 브랜치에서
          활성 폴더로 이식): 바이알↔12way 구간의 초기 공기가 매 런 시약과 함께
          반응기로 들어가 반응 속도에 영향을 준다. `line_src` 는 타이밍 계산에만
          쓰이고(커밋 d572719 로 소스퍼지 동작 무효화), 왕복레그 제외로 흡입량에서도
          빠져 — 이 공기를 실제로 빼주는 주체가 없었다.

        @codesyncer-decision: 순서 = 퍼지 → 시스템 세척 → 프리필.
          퍼지가 끝나면 valve_pump(12way→3way→시린지) 공용 구간에 시약이 남는데,
          뒤따르는 세척(port1 흡입 → port12 배출)이 그 구간을 헹궈낸다.
          퍼지를 세척 뒤에 두면 그 잔류 시약이 그대로 Phase-0 용매와 섞인다.

        @codesyncer-inference: 포트당 1회면 충분하다고 가정 — 배출 후 그 라인은
          액으로 채워져 공기가 재유입될 경로가 없다(바이알 교체 시는 예외).
          검증: 실기에서 2스텝 연속 동일 포트 런의 전환 지연/수율 재현성.

        @codesyncer-risk: 퍼지량만큼 바이알 시약이 폐액으로 나간다(현 실측
          0.158mL/포트). 극소량 시약 실험에선 bubble_purge_enabled=false 로 끌 것.
        """
        sp = (self.cfg.config_data.get("system_params", {})
              if hasattr(self, "cfg") else {})
        if not bool(sp.get("bubble_purge_enabled", False)):
            return []

        waste_port = int(sp.get("bubble_purge_waste_port", 12) or 12)
        factor = max(1.0, min(4.0, float(sp.get("bubble_purge_factor", 2.0) or 2.0)))
        refill_floor = float(sp.get("refill_min_vol_ml", 0.1) or 0.1)

        if not hasattr(self, "_purged_ports"):
            self._purged_ports = {}
        if not hasattr(self, "_primed_ports"):
            self._primed_ports = {}

        plan = []
        for p_name in flows.keys():
            pump = self.pumps.get(p_name)
            if not _is_smart_pump(pump):
                continue
            # 1-소스 라우팅(NRG)은 12way 가 없어 '되밀어 폐기'가 성립하지 않음
            if self._pump_routing(p_name) != "external_valve":
                continue
            port = int(inlet_ports.get(p_name, 0) or 0)
            if port <= 1 or port == waste_port:
                continue  # port1=세척용매, 폐액포트는 퍼지 대상이 아님
            if port in self._purged_ports.setdefault(p_name, set()):
                continue
            if port in self._primed_ports.setdefault(p_name, set()):
                continue  # 이미 시약이 지나간 라인 = 기포 없음
            v = self.compute_bubble_purge_vol(
                float((getattr(self.cfg, "line_vol_inlet", {}) or {}).get(p_name, 0.0) or 0.0),
                float((getattr(self.cfg, "selector_internal_vol", {}) or {}).get(p_name, 0.0) or 0.0),
                factor, refill_floor)
            if v <= 0:
                self._log(f"  [BubblePurge] {p_name} 스킵 — 소스 배관 볼륨 미설정(0). "
                          f"배관도 칩/원장으로 tube_vol_inlet·tube_vol_selector 입력 필요")
                continue
            cur = float(getattr(pump, "current_vol", 0.0) or 0.0)
            if cur > 0.05:
                self._log(f"  [BubblePurge] ⚠ {p_name} 시린지 잔량 {cur:.3f}mL — "
                          f"퍼지 배출이 잔량까지 함께 폐기함(전량 배출 규약)")
            plan.append((p_name, port, v))

        if not plan:
            return []

        self._emit_status("Source line bubble purge")
        self._log("소스라인 기포 퍼지 — 시약 포트에서 데드볼륨 흡입 → 12way 폐기")

        # ── Phase A: 시약 포트에서 소스 경로 전체를 흡입 (기포를 시린지로) ──
        started = []
        for p_name, port, v in plan:
            pump = self.pumps[p_name]
            self._wait_pause_or_abort("bubble purge withdraw")
            self._log(f"  [{p_name}] 기포 흡입 {v:.3f}mL (Port {port} — "
                      f"(inlet+selector)×{factor:.1f}, 12way 로터 통과분)")
            if pump.refill_prepare(port, volume=v):
                started.append(p_name)
        if started:
            self._sequential_trigger(started, "refill_trigger")
            self._run_complete_threads(started, "refill_complete",
                                       "bubble purge withdraw",
                                       log_prefix="BubblePurge: ")

        # ── Phase B: 12way 폐액 포트로 되밀어 기포 배출 (3way=SOURCE 유지) ──
        expel = []
        for p_name, _port, _v in plan:
            pump = self.pumps[p_name]
            self._wait_pause_or_abort("bubble purge expel")
            if pump.wash_infuse_prepare(waste_port):
                expel.append(p_name)
        if expel:
            self._sequential_trigger(expel, "wash_infuse_trigger")
            self._run_complete_threads(expel, "wash_infuse_complete",
                                       "bubble purge expel",
                                       log_prefix="BubblePurge: ")

        done = []
        for p_name, port, v in plan:
            self._purged_ports.setdefault(p_name, set()).add(port)
            done.append((p_name, port, v))
        self._log(f"  [BubblePurge] 완료 — {len(done)}개 소스라인 액 충전 상태 "
                  f"(총 {sum(v for _, _, v in done):.3f}mL 폐기)")
        return done

    # ---------------------------------------------------------------------
    # Wash / dosing / prefill
    # ---------------------------------------------------------------------
    def _push_line_prime(self, errors: list):
        """스텝1 전용: push(HPLC) 라인·헤드를 용매로 채우고 기포를 배출.

        @codesyncer-decision(2026-08-15, 사용자 제안): push 시작 직후 유체가 즉시
          밀리지 않는 실기 증상(20s 이상 = 라인/헤드의 공기)을 스텝1에서 미리
          해소한다. Outlet=WASTE 상태에서 push 펌프를 '프라임 유속'(실험 유속보다
          빠르게)으로 설정 부피만큼 돌려 반응기 방향으로 흘려보낸다.
        - 설정: system_params.push_line_prime_vol_ml (0=끔) /
                push_line_prime_rate_ml_min (0=priming_rate_ml_min 폴백)
        - 시스템 세척(시린지·12way 경로)과 병행 스레드 — 추가 시간 대개 0.
          프리필(Prime Ph1)은 같은 본류를 쓰므로 호출부가 그 전에 join 한다.
        - 실패는 errors 로만 보고(경고) — 프라임 실패가 런 자체를 막지는 않는다.
        """
        try:
            sp = (self.cfg.config_data.get("system_params", {})
                  if hasattr(self, "cfg") else {})
            vol = float(sp.get("push_line_prime_vol_ml", 0.0) or 0.0)
            if vol <= 0:
                return
            rate = float(sp.get("push_line_prime_rate_ml_min", 0.0) or 0.0)
            if rate <= 0:
                rate = float(sp.get("priming_rate_ml_min", 5.0) or 5.0)
            if rate <= 0:
                errors.append("prime rate 0")
                return
            dur = (vol / rate) * 60.0
            # Outlet=WASTE 강제 — 프라임 배출액/기포가 수집 웰로 가면 안 됨
            self._outlet_set_safe(1, "push line prime")
            self._log(f"  [PushLinePrime] 라인 충전·기포 배출 {vol:.2f}mL "
                      f"@{rate:.2f}mL/min ({dur:.0f}s) — 세척과 병행, Outlet=WASTE")
            self.push_pump.set_flow(rate)
            self.push_pump.start()
            t0 = time.monotonic()
            while True:
                elapsed = time.monotonic() - t0
                if elapsed >= dur:
                    break
                self._check_abort()
                if not self.pause_event.is_set():
                    try:
                        self.push_pump.stop()
                    except Exception:
                        pass
                    self.pause_event.wait()
                    self._check_abort()
                    self.push_pump.set_flow(rate)
                    self.push_pump.start()
                    t0 = time.monotonic() - elapsed
                time.sleep(min(0.5, max(0.0, dur - elapsed)))
            self.push_pump.stop()
            # 프라임 직후 압력 기록 — 라인이 찼는지의 실측 근거(있으면)
            _p = None
            try:
                _p = self.push_pump.get_pressure()
            except Exception:
                pass
            self._log("  [PushLinePrime] 완료 — push 라인 용매 충전 상태"
                      + (f" (압력 {float(_p):.1f} bar)" if _p not in (None, 0.0) else ""))
        except SafetyError as e:
            try:
                self.push_pump.stop()
            except Exception:
                pass
            errors.append(f"abort: {e}")
        except Exception as e:
            try:
                self.push_pump.stop()
            except Exception:
                pass
            errors.append(str(e))
            self._log(f"  [PushLinePrime] ⚠ 오류: {e}")

    # ------------------------------------------------------------------
    def _n2_precal_purge(self, errors: list):
        """시퀀스 시작(스텝1) N2 사전 캘리브레이션 — RoboChem OCB350 캘리브 계약의
        소프트웨어 이식 (2026-08-17 사용자 지시).

        RoboChem 원본 계약: 캘리브레이션은 '튜브에 액체가 없을 때'만 유효
        (OCB350.h:77 "tube should have no liquid" / Phase_Sensor_Array.ino:63 —
        액체가 있으면 출력 의미가 뒤집힘). OCB350 은 캘리브 핀 펄스로 하드웨어가
        자체 재영점하지만 OPB ADC 리그엔 그 핀이 없으므로 PC측 등가를 수행한다:
          ① Outlet=WASTE 확보 후 MFC N2 로 가스T 하류(믹싱→반응기→post)의
             기존 액체를 배기 — 분취기 호밍·가열·세척과 병행이라 추가 시간 0
          ② 전 센서가 '가스'를 settle_sec 연속 보고하면 배기 완료로 판정
             (RoboChem OpenValveUntilPhaseChange 의 wait_for_gas 등가 + 안정창)
          ③ 채널별 공기 ADC 원점을 표집(mean/σ)하고 벤더 기준과 대조:
             튜브 미장착(없음값 근접) / 공기 원점 드리프트 / 임계 역전 을
             시퀀스 시작 시점에 검출 — '튜브 빠짐=액체' fail-unsafe 를 자동으로
             잡는 유일한 지점이다 (가스 상태에서만 구별 가능).
        실패는 전부 경고+스킵(런 무영향) — 본류는 프리필 Prime-P1 이 어차피
        용매로 재충전하므로 배기 실패가 런을 막을 이유가 없다. 단 Outlet=WASTE
        확보 실패 시에는 즉시 중단한다(배선반전 invert 리그에서 배기물이 웰로
        갈 수 있음 — 아웃렛_배선반전_주의.md).
        설정: system_params.n2_precal_enabled(기본 false)/sccm/timeout_sec/
              settle_sec/sample_sec/auto_cal(기본 false — HW CAL 은 수동 절차,
              2026-08-18 정책 반전, ② 블록 주석 참조).
        결과: self._n2_air_baseline + 로그 + 트레이스.
        """
        sp = (self.cfg.config_data.get("system_params", {})
              if hasattr(self, "cfg") else {})
        if not bool(sp.get("n2_precal_enabled", False)):
            return
        if self.mfc is None:
            self._log("  [N2Precal] ⚠ MFC 미배정 — 건너뜀 (roles.gas 를 실물 MFC 로)")
            return
        ps = self.phase_sensor
        if ps is None:
            self._log("  [N2Precal] ⚠ 위상센서 미배정 — 건너뜀")
            return
        keys = list(getattr(ps, "sensors", {}) or {})
        if not keys:
            self._log("  [N2Precal] ⚠ 센서 채널 매핑 없음 — 건너뜀")
            return
        sccm = float(sp.get("n2_precal_sccm", 20.0) or 20.0)
        timeout = float(sp.get("n2_precal_timeout_sec", 120.0) or 120.0)
        settle = float(sp.get("n2_precal_settle_sec", 3.0) or 3.0)
        sample = float(sp.get("n2_precal_sample_sec", 2.0) or 2.0)

        gas_on = False
        try:
            # ① 배기 — 배출물이 웰로 가면 안 됨 (invert 리그 특히)
            if not self._outlet_set_safe(1, "N2 precal"):
                errors.append("Outlet=WASTE 확보 실패")
                self._log("  [N2Precal] ⚠ Outlet=WASTE 확보 실패 — 배기 중단"
                          "(방향 미상 상태로 가스 주입 금지)")
                return
            self._log(f"  [N2Precal] N2 배기 시작 {sccm:.0f} sccm — 센서 "
                      f"{'/'.join(keys)} 가스 안정 {settle:.0f}s 대기 (호밍·가열 병행)")
            self.mfc.set_flow(sccm)
            gas_on = True
            t0 = time.monotonic()
            gas_since = None
            _last_flow = None
            _next_flow_log = 5.0               # 실유량 진단 로그 주기(초)
            while True:
                self._check_abort()
                if not self.pause_event.is_set():      # 일시정지 — 가스도 정지
                    self.mfc.set_flow(0.0)
                    self.pause_event.wait()
                    self._check_abort()
                    self.mfc.set_flow(sccm)
                    gas_since = None                   # 정지 중 상태는 불신
                _el_purge = time.monotonic() - t0
                # @codesyncer(2026-08-18, 실기 — '질소가 약해서 안 밀림'): 실유량을
                #   주기 로그. MFC 는 유량 제어기라 미는 힘의 상한 = 공급 레귤레이터
                #   압력. 실측 < setpoint 이면 공급압 부족(굶주림)이 원인 —
                #   레귤레이터를 올려야지 sccm 설정은 이미 풀스케일이라 무의미.
                if _el_purge >= _next_flow_log:
                    _next_flow_log += 5.0
                    try:
                        _last_flow = float(self.mfc.get_flow())
                        _mark = ("✓" if _last_flow >= sccm * 0.9 else
                                 "⚠ 공급압 부족 의심(레귤레이터 확인)")
                        self._log(f"  [N2Precal] 실유량 {_last_flow:.2f}/{sccm:.1f} "
                                  f"sccm @ {_el_purge:.0f}s  {_mark}")
                    except Exception:
                        pass
                if _el_purge > timeout:
                    errors.append(f"배기 타임아웃 {timeout:.0f}s")
                    _fl = (f"마지막 실유량 {_last_flow:.2f}/{sccm:.1f} sccm — "
                           if _last_flow is not None else "")
                    self._log(f"  [N2Precal] ⚠ {timeout:.0f}s 안에 전 센서 가스 안정 "
                              f"실패 — 원점 캡처 생략. {_fl}"
                              f"실유량<설정 이면 N2 레귤레이터 압력 부족, "
                              f"실유량 정상인데 정체면 하류 막힘/튜브 장착 확인 "
                              f"(튜브 빠짐은 '액체'로 읽혀 타임아웃으로 나타남)")
                    return
                try:
                    all_gas = all(ps.read_phase(k) == "GAS" for k in keys)
                except Exception as e:
                    errors.append(f"센서 오류: {e}")
                    self._log(f"  [N2Precal] ⚠ 센서 판독 실패 — 중단: {e}")
                    return
                if all_gas:
                    if gas_since is None:
                        gas_since = time.monotonic()
                    elif time.monotonic() - gas_since >= settle:
                        break                          # 배기 완료
                else:
                    gas_since = None                   # 액체 재출현 — 안정창 리셋
                time.sleep(0.1)
            purge_sec = time.monotonic() - t0

            # ② 하드웨어 캘리브 훅 — 기본 OFF (2026-08-18 사용자 확정, 정책 반전).
            # @codesyncer-decision: RoboChem 원본을 재확인한 결과 매런 자동 CAL 은
            #   우리의 확장이었다 — OmniPlatypus 전체에서 위상센서 calibrate 호출은
            #   수동 대화형 스크립트(platform_calibration.py, "사람이 라인 빈 것
            #   확인 후 엔터") 단 한 곳뿐, 실험 시퀀스에는 0건. 우리 자동판은
            #   '비었음'을 캘리브 대상 센서 자신의 판정(GAS 안정)으로 보증하는
            #   순환 논리라, 유색액이 GAS 로 오판되면 액체 위에서 CAL 이 발사되어
            #   보드 기준이 오염된다(2026-08-18 센서2 상시-0 사고의 유력 원인 —
            #   매런 재발사로 오염이 재생산됨). CAL 은 수동 절차로 격하:
            #   tools/opb_manual_cal.py (육안 확인 → CAL → 재실측).
            #   n2_precal_auto_cal=true 로만 구버전 자동 CAL 복원 가능.
            if bool(sp.get("n2_precal_auto_cal", False)):
                for k in keys:
                    try:
                        ps.calibrate(k)
                    except Exception as e:
                        self._log(f"  [N2Precal] calibrate({k}) 실패(무시): {e}")
                time.sleep(0.3)                        # 캘리브 후 기준 안정
            else:
                self._log("  [N2Precal] HW 캘리브 스킵 (자동 CAL off — 수동 절차: "
                          "tools/opb_manual_cal.py)")

            # ③ 공기 원점 표집 (가스 유지 상태에서 — 캘리브 '후' 기준의 원점)
            acc = {k: [] for k in keys}
            t1 = time.monotonic()
            while time.monotonic() - t1 < sample:
                self._check_abort()
                for k in keys:
                    try:
                        v = ps.analog(k)
                        if v is not None and int(v) >= 0:
                            acc[k].append(int(v))
                    except Exception:
                        pass
                time.sleep(0.1)
            self.mfc.set_flow(0.0)
            gas_on = False

            # 실측 기준표와 대조 — 🔴2026-08-18 재배선 후 실측으로 갱신 (구 벤더표
            #   2026-08-05 는 정렬 변경으로 폐기: ch0 공기 80→522). ch0 은
            #   '튜브 없음'(553)과 공기(522)가 Δ31 로 분리 불가 → 미장착 검사 스킵
            #   (none=None). CAL 펄스는 디지털 판정만 재영점, 아날로그 무영향(실측).
            REF = {0: {"air": 522, "none": None, "water": 866},
                   1: {"air": 519, "none": 960, "water": 980}}
            baseline = {}
            for k in keys:
                vals = acc[k]
                if not vals:
                    self._log(f"  [N2Precal] ⚠ {k}: 표본 없음")
                    continue
                mean = sum(vals) / len(vals)
                sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
                ch = int((getattr(ps, "sensors", {}) or {}).get(k, -1))
                # 모드 B(OCB350 어레이)는 PC 임계가 없음(판정=보드 하드웨어) —
                # thresholds 미보유 드라이버에선 임계 역전 검사를 건너뛴다.
                thr = (getattr(ps, "thresholds", {}) or {}).get(ch)
                ref = REF.get(ch)
                flags = []
                if ref:
                    if (ref.get("none") is not None
                            and abs(mean - ref["none"]) < 60):
                        flags.append("튜브 미장착 의심")
                    elif abs(mean - ref["air"]) > 100:
                        flags.append(f"공기 원점 드리프트(기준 {ref['air']})")
                if thr is not None and mean > float(thr):
                    flags.append(f"가스인데 임계({float(thr):.0f}) 초과 — 임계 재설정 필요")
                baseline[k] = {"air_adc": round(mean, 1), "sd": round(sd, 1),
                               "n": len(vals), "ch": ch, "flags": flags}
                mark = ("  ⚠ " + " / ".join(flags)) if flags else "  ✓"
                _thr_s = f"{float(thr):.0f}" if thr is not None else "—"
                self._log(f"  [N2Precal] {k}(ch{ch}) 공기 원점 {mean:.0f}±{sd:.0f} "
                          f"(n={len(vals)}, thr={_thr_s}){mark}")
            self._n2_air_baseline = baseline
            self._log(f"  [N2Precal] 완료 — 배기 {purge_sec:.0f}s, 본류=N2 상태 "
                      f"(Prime-P1 이 용매로 재충전)")
            try:
                self.trace.instant("N2Precal", "baseline", args=baseline)
            except Exception:
                pass
        except SafetyError as e:
            errors.append(f"abort: {e}")
        except Exception as e:
            errors.append(str(e))
            self._log(f"  [N2Precal] ⚠ 오류: {e}")
        finally:
            if gas_on:
                try:
                    self.mfc.set_flow(0.0)             # 페일세이프 — 가스 방치 금지
                except Exception:
                    pass

    def _push_parallel_wash(self, flows: Dict[str, float], errors: list):
        # 세척 분리(2026-08-20): push 병행 세척은 매 스텝 = 스텝간 세척으로 분류
        # → interstep_wash_count/_volume 오버라이드 적용 (미설정 시 공통값).
        # 이 래퍼는 push 스레드 안에서 실행 — 시스템 세척과 시간상 비중첩.
        saved = self._wash_override(flows, "interstep")
        try:
            self._push_parallel_wash_run(flows, errors)
        finally:
            self._wash_restore(saved)

    def _push_parallel_wash_run(self, flows: Dict[str, float], errors: list):
        """HPLC push 병행 시린지 세척 — push 창 동안 백그라운드 스레드로 실행.

        @codesyncer-decision(2026-08-15, 사용자 확정): push(Reaxus)가 반응기를
          미는 동안 시린지는 세척 사이클(port 1 세척액 흡입 → port 12 배출)
          ×wash_count 를 병행 수행. 효과: 구 post-injection prime 의 무계상
          reactor 전진 제거 + 다음 스텝의 시스템 세척 시간 제거.
        - 별도 '잔량 폐기' 선행 단계 없음(사용자 확정: 주입은 시약 전량 토출
          가정으로 종료). 미세 잔량(편도 라인보정 ~0.15mL)이 있어도
          wash_infuse 가 '전량 배출'이라 첫 사이클에 합산 폐기된다.
        - 경로: 3way POS_SOURCE + 12way — 반응 스트림(체크밸브 하류)과 물리 분리,
          push 흐름과 충돌 없음.
        - external_valve 라우팅만 대상 (internal_valve/AS 는 12way 경로 없음).
        - 레벨게이트·_emit_phase 는 메인 스레드와의 상태 충돌(트레이스 PHASE 트랙,
          센서 직렬 접근)을 피해 미사용 — 로그만 남긴다. 세척 종료 = 빈 시린지
          (마지막 동작이 infuse 전량 배출) → 다음 스텝 프리필 전제 성립.
        - 예외는 errors 리스트에 수집 — 메인 스레드가 push 종료 후 보고.
        """
        try:
            smart = [(p, self.pumps.get(p)) for p in flows.keys()
                     if _is_smart_pump(self.pumps.get(p))
                     and self._pump_routing(p) == "external_valve"]
            if not smart:
                self._log("  [PushWash] 대상 펌프 없음 — 생략")
                return

            # 세척 사이클 (시스템 세척과 동일 프리미티브: withdraw→infuse)
            max_count = max((int(getattr(pu, "wash_count", 0)) for _, pu in smart),
                            default=0)
            for cycle in range(max_count):
                w_names = []
                for p_name, pump in smart:
                    if cycle >= int(getattr(pump, "wash_count", 0)):
                        continue
                    self._wait_pause_or_abort("push wash")
                    if pump.wash_withdraw_prepare(solvent_port=1):
                        w_names.append(p_name)
                if not w_names:
                    continue
                self._sequential_trigger(w_names, "wash_withdraw_trigger")
                self._run_complete_threads(
                    w_names, "wash_withdraw_complete", "push wash",
                    log_prefix=f"PushWash {cycle + 1}/{max_count}: ")
                i_names = []
                for p_name, pump in smart:
                    if p_name not in w_names:
                        continue
                    self._wait_pause_or_abort("push wash")
                    if pump.wash_infuse_prepare(waste_port=12):
                        i_names.append(p_name)
                self._sequential_trigger(i_names, "wash_infuse_trigger")
                self._run_complete_threads(
                    i_names, "wash_infuse_complete", "push wash",
                    log_prefix=f"PushWash {cycle + 1}/{max_count}: ")
            self._log("  [PushWash] 병행 세척 완료 — 시린지 빈 상태")
        except SafetyError as e:
            errors.append(f"abort/safety: {e}")
        except Exception as e:
            errors.append(str(e))
            self._log(f"  [PushWash] ⚠ 오류: {e}")

    # ── 세척 파라미터 분리 (2026-08-20 사용자 요청) ────────────────────
    #   초기(시퀀스 시작=스텝1) 세척과 스텝간 세척(스텝2+ 시스템 세척, push 병행
    #   세척)의 횟수/용량을 그룹 설정으로 분리:
    #     initial_wash_count / initial_wash_volume     — 스텝1 시스템 세척
    #     interstep_wash_count / interstep_wash_volume — 그 외 전부
    #   미설정(None) = 기존 공통값(wash_count/wash_volume) 폴백 → 완전 하위호환.
    #   용량은 스마트펌프가 wash_volume 속성으로 소비하므로 일시 교체 후 복원.
    def _wash_override(self, flows: Dict[str, float], kind: str):
        saved = []
        applied = []
        for p_name in flows.keys():
            pump = self.pumps.get(p_name)
            if not _is_smart_pump(pump):
                continue
            c = getattr(pump, f"{kind}_wash_count", None)
            v = getattr(pump, f"{kind}_wash_volume", None)
            if c is None and v is None:
                continue
            saved.append((pump, getattr(pump, "wash_count", 0),
                          getattr(pump, "wash_volume", None)))
            if c is not None:
                pump.wash_count = int(c)
            if v is not None:
                pump.wash_volume = float(v)
            applied.append(f"{p_name}={int(pump.wash_count)}회"
                           f"×{float(pump.wash_volume or 0):.1f}mL")
        if applied:
            self._log(f"  [Wash] {'초기(스텝1)' if kind == 'initial' else '스텝간'} "
                      f"세척 파라미터: {', '.join(applied)}")
        return saved

    @staticmethod
    def _wash_restore(saved):
        for pump, c, v in saved:
            pump.wash_count = c
            if v is not None:
                pump.wash_volume = v

    def _execute_system_wash(self, flows: Dict[str, float],
                             initial: bool = False):
        saved = self._wash_override(flows, "initial" if initial else "interstep")
        try:
            self._system_wash_run(flows)
        finally:
            self._wash_restore(saved)

    def _system_wash_run(self, flows: Dict[str, float]):
        # @codesyncer-decision: 순차 명령 + 병렬 대기 — RS-485 버스 경쟁 방지
        # wash_cycle은 infuse+withdraw 2단계 → 각 단계를 순차begin + 병렬complete
        max_count = 0
        smart_pumps = []
        for p_name in flows.keys():
            pump = self.pumps.get(p_name)
            if _is_smart_pump(pump):
                smart_pumps.append((p_name, pump))
                max_count = max(max_count, int(getattr(pump, "wash_count", 0)))

        if max_count <= 0:
            return

        self._log(f"System wash start ({max_count} cycles)")

        for cycle in range(max_count):
            self._wait_pause_or_abort("system wash")

            cycle_pumps = []
            for p_name, pump in smart_pumps:
                if cycle < int(getattr(pump, "wash_count", 0)):
                    cycle_pumps.append((p_name, pump))

            # @codesyncer-decision: 세척 순서 = 흡입(withdraw) → 배출(infuse)
            # 이 시스템의 세척은 시린지 배럴/소스 라인을 헹궈 12-way 폐액 포트로
            # 배출하는 구조 (반응부 용매 충전은 prime이 전담).
            # 빈 시린지를 가정하므로 먼저 용매를 흡입(POS_SOURCE, solvent_port)한 뒤
            # 12-way 폐액 포트로 밀어냄(infuse, POS_SOURCE + waste_port).
            # 기존 순서(infuse 먼저)는 빈 시린지로 토출이라 배출할 용매가 없어 무효였음.

            # ── 오토샘플러 그룹 분리 — 니들 세척은 직렬 (니들 1개) ──
            # @codesyncer-decision: 내장밸브식 '제자리 기포퍼지' 대신 원본
            #   RoboChem CleanNeedle 등가 세척: NRG 의 FILL/EMPTY 는 니들
            #   라인에서 일어나므로 [니들→rinse 에서 FILL(깨끗한 용매 흡입) →
            #   니들→waste 에서 EMPTY(오염분 배출)] = 진짜 세척이 된다.
            #   rinse/waste 위치가 positions 에 없으면 기존 제자리 퍼지 폴백.
            as_cycle = [(p, pu) for p, pu in cycle_pumps
                        if self._autosampler_coord(p) is not None]
            par_cycle = [(p, pu) for p, pu in cycle_pumps
                         if self._autosampler_coord(p) is None]

            # --- Withdraw phase: 용매 흡입 (prepare → trigger → 병렬 complete) ---
            withdraw_started = []
            for p_name, pump in par_cycle:
                self._wait_pause_or_abort("system wash")
                if pump.wash_withdraw_prepare(solvent_port=1):
                    withdraw_started.append((p_name, pump))
            withdraw_names = [p for p, _ in withdraw_started]
            self._sequential_trigger(withdraw_names, "wash_withdraw_trigger")
            self._run_complete_threads(
                withdraw_names, "wash_withdraw_complete", "system wash",
                log_prefix=f"Wash {cycle+1}/{max_count}: ")

            # --- Infuse phase: 다운스트림 토출 (prepare → trigger → 병렬 complete) ---
            infuse_started = []
            for p_name, pump in par_cycle:
                self._wait_pause_or_abort("system wash")
                if pump.wash_infuse_prepare(waste_port=12):
                    infuse_started.append((p_name, pump))
            infuse_names = [p for p, _ in infuse_started]
            self._sequential_trigger(infuse_names, "wash_infuse_trigger")
            self._run_complete_threads(
                infuse_names, "wash_infuse_complete", "system wash",
                log_prefix=f"Wash {cycle+1}/{max_count}: ")

            # --- AS 그룹: 니들 세척 직렬 사이클 ---
            for p_name, pump in as_cycle:
                coord = self._autosampler_coord(p_name)
                rinse = coord.service_vial("rinse", "wash", "cleaning")
                waste = coord.service_vial("waste")
                if rinse is None or waste is None:
                    self._log(f"  [{p_name}] rinse/waste 위치 미정의 — 제자리 기포퍼지 세척")
                try:
                    # Withdraw @ rinse (깨끗한 용매 흡입)
                    self._wait_pause_or_abort("system wash")
                    if rinse is not None:
                        ok, msg = coord.position_for_withdraw(rinse)
                        if not ok:
                            raise SafetyError(f"[{p_name}] 세척 니들 이동 실패: {msg}")
                    if pump.wash_withdraw_prepare(solvent_port=1):
                        self._sequential_trigger([p_name], "wash_withdraw_trigger")
                        self._run_complete_threads(
                            [p_name], "wash_withdraw_complete", "system wash",
                            log_prefix=f"Wash {cycle+1}/{max_count}(AS): ")
                    # Infuse @ waste (오염분 배출)
                    self._wait_pause_or_abort("system wash")
                    if waste is not None:
                        ok, msg = coord.position_for_withdraw(waste)
                        if not ok:
                            raise SafetyError(f"[{p_name}] 세척 니들 이동 실패: {msg}")
                    if pump.wash_infuse_prepare(waste_port=12):
                        self._sequential_trigger([p_name], "wash_infuse_trigger")
                        self._run_complete_threads(
                            [p_name], "wash_infuse_complete", "system wash",
                            log_prefix=f"Wash {cycle+1}/{max_count}(AS): ")
                finally:
                    coord.after_withdraw()

            pct = ((cycle + 1) / max_count) * 100.0
            self._emit_phase("System Wash", pct)

        self._emit_phase("System Wash", 100.0)
        # 게이트②(잔량제거): 세척은 withdraw→infuse 로 '빈 상태 종료'가 설계 전제.
        #   net-zero 세척은 기존 잔량을 비우지 못하므로, 여기서 센서로 empty 검증
        #   (기본 purge — 잔류 용매 위에 시약이 리필되는 희석 사고 차단).
        # @codesyncer(2026-07-29, 사용자 지시): 배출 방향 = 리액터(prime 경로) —
        #   내용물이 세척용매라 다운스트림 플러시를 겸하고 Outlet=WASTE 로 폐액병행.
        #   12-way 폐액 포트 경유 안 함.
        self._level_gate([p for p, _ in smart_pumps], "wash", "세척후",
                         discharge="reactor")
        self._log("System wash complete")

    def _execute_smart_dosing(
        self,
        flow_map: Dict[str, float],
        total_vol_ml: Optional[float] = None,
        duration_sec: Optional[float] = None,
        source_port_map: Optional[Dict[str, int]] = None,
        step_name: str = "",
        allow_refill: bool = True,
        on_pumps_started: Optional[Callable] = None,
        start_offsets: Optional[Dict[str, float]] = None,
    ):
        # @codesyncer(검증 2026-08-11): DOSING 스팬 try/finally 보장 래퍼 (동작 불변 추출)
        #   — abort/SafetyError/RuntimeError 경로에서 end 누락 시 사고 트레이스의
        #   도징 스팬이 런 끝까지 이어져 보이던 결함 수정. args 구성도 방어적으로.
        try:
            _args = {"flows": {k: round(float(v), 3) for k, v in flow_map.items()},
                     "allow_refill": bool(allow_refill)}
        except Exception:
            _args = None
        self.trace.begin("DOSING", step_name or "dosing", args=_args)
        try:
            return self._execute_smart_dosing_impl(
                flow_map, total_vol_ml=total_vol_ml, duration_sec=duration_sec,
                source_port_map=source_port_map, step_name=step_name,
                allow_refill=allow_refill, on_pumps_started=on_pumps_started,
                start_offsets=start_offsets)
        finally:
            self.trace.end("DOSING")

    def _execute_smart_dosing_impl(
        self,
        flow_map: Dict[str, float],
        total_vol_ml: Optional[float] = None,
        duration_sec: Optional[float] = None,
        source_port_map: Optional[Dict[str, int]] = None,
        step_name: str = "",
        allow_refill: bool = True,
        on_pumps_started: Optional[Callable] = None,
        start_offsets: Optional[Dict[str, float]] = None,
    ):
        """@param on_pumps_started: 첫 펌프 start 명령 전송 직후 1회 호출되는 콜백.
        CollectionTimer.resume를 넘기면 '실제 flow 시작 시점'과 타이머 기준점이
        일치하게 됨 (밸브 전환·순차 start 격차 동안의 가짜 pumping 시간 제거)."""
        total_rate = sum(float(v) for v in flow_map.values())
        flow_str = ", ".join(f"{k}={float(v):.3f}" for k, v in flow_map.items())
        print(f"   [DOSING] {step_name} | flows: {flow_str} | total={total_rate:.3f} mL/min")
        if total_rate <= 0:
            return

        if duration_sec is None and total_vol_ml is not None:
            duration_sec = (float(total_vol_ml) / total_rate) * 60.0
        if duration_sec is None or duration_sec <= 0:
            return

        if source_port_map is None:
            source_port_map = {}

        # @codesyncer-decision(P2, 2026-08-12): 도징 창 클록 = monotonic — NTP 점프가
        #   창을 늘리거나(과주입) 줄이는(미달) 경로 차단. CollectionTimer 와 동일 결정.
        start_time = time.monotonic()
        end_time = start_time + duration_sec
        # @codesyncer-decision: 스태거 시작 기준점 — 펌프별 시작 시각 = anchor + offset
        stagger_anchor = start_time
        total_refill_time = 0.0
        started_cb_fired = False
        timer_resume_pending = False
        _start_verified = set()   # #12: 기동 확인 완료 펌프 (pause/refill 재시작 시 리셋)
        _start_suspect = {}       # #12: 연속 '정지' 판정 카운트 (2회 확정 시 실패)

        if step_name:
            self._log(f"[{step_name}] start")

        while time.monotonic() < end_time:
            self._check_abort()

            # Pause handling
            if not self.pause_event.is_set():
                # @codesyncer-decision: pause 시 CollectionTimer도 함께 pause (분취 타이밍 버그 fix #1)
                # - 기존: pump만 정지, timer는 계속 카운트 → Outlet 전환·well 이동이
                #   실제 flow 위치보다 일찍 발동 → 분획 경계 전체가 어긋나고
                #   마지막 Outlet→WASTE가 조기 발동되어 제품이 waste로 유실
                # - 수정: pump 정지 직전 timer.pause(), 펌프 재시작 직후 resume
                if self._collection_timer is not None:
                    self._collection_timer.pause()
                    timer_resume_pending = True
                for p_name in flow_map:
                    try:
                        self.pumps[p_name].stop()
                    except Exception:
                        pass
                self._emit_status(f"{step_name}: paused")
                pause_start = time.monotonic()
                self.pause_event.wait()
                self._check_abort()
                paused_dur = time.monotonic() - pause_start
                stagger_anchor += paused_dur
                # @codesyncer-decision: pause 시간만큼 end_time 연장 (분취 타이밍 버그 fix #2)
                # - 기존: end_time 고정 → pause 동안 wall-clock이 흘러가 dosing이
                #   조기 종료 → 주입/푸시 볼륨 미달 (시간 구동 시스템에서 치명적)
                end_time += paused_dur
                # @codesyncer-decision: 펌프 재시작 강제 (분취 타이밍 버그 fix #3)
                # - 기존: stop()은 target_flow를 바꾸지 않으므로
                #   "_dosing_started에 있고 target_flow == rate" 조건에 걸려
                #   resume 후 start가 영원히 재전송되지 않음 → 펌프 정지 상태로
                #   end_time까지 빈 대기 (flow 없음, timer만 진행)
                # - 수정: set 비움 → 다음 루프에서 set_flow+start 재전송
                if hasattr(self, '_dosing_started'):
                    self._dosing_started.clear()
                _start_verified.clear()   # 재시작 펌프 재검증
                _start_suspect.clear()

            dt = min(1.0, max(0.0, end_time - time.monotonic()))
            if start_offsets:
                _nw = time.monotonic()
                _started_set = getattr(self, "_dosing_started", set())
                for _pn, _of in start_offsets.items():
                    if _pn not in _started_set:
                        _rem = (stagger_anchor + float(_of)) - _nw
                        if _rem > 0:
                            dt = min(dt, max(0.05, _rem))
            if dt <= 0:
                break

            # Refill check
            # @codesyncer-decision: threshold=0 — 시린지가 완전히 비었을 때만 리필
            # - 기존 5% capacity threshold는 시약 잔량이 있어도 리필 트리거 → 시약 희석/반응 오염
            # - 밀폐 액체 시스템이므로 시린지를 완전히 비워도 공기 유입 없음
            # - allow_refill=False(injection)에서는 리필 자체를 차단
            refill_needed = False
            low_pumps = []
            for p_name, rate in flow_map.items():
                pump = self.pumps[p_name]
                if _is_smart_pump(pump):
                    if float(pump.current_vol) <= 0:
                        refill_needed = True
                        low_pumps.append(p_name)

            if refill_needed and allow_refill:
                # @codesyncer-decision: refill 동안 CollectionTimer pause
                # - refill 중 flow가 없으므로 timer를 함께 멈춰야 이벤트가
                #   실제 액체 위치와 동기 유지됨 (현재 timer 활성 step은 모두
                #   allow_refill=False라 방어적 코드지만, 향후 조합 변경 대비)
                if self._collection_timer is not None:
                    self._collection_timer.pause()
                    timer_resume_pending = True
                for p_name in flow_map:
                    try:
                        self.pumps[p_name].stop()
                    except Exception:
                        pass

                refill_start = time.monotonic()
                # @codesyncer-decision: prepare → trigger 분리 — 펌프 간 시작 격차 최소화
                refill_started = []
                for p_name in low_pumps:
                    pump = self.pumps[p_name]
                    if _is_smart_pump(pump):
                        source = int(source_port_map.get(p_name, 1))
                        if pump.refill_prepare(source, volume=pump.capacity):
                            refill_started.append(p_name)
                self._sequential_trigger(refill_started, "refill_trigger")

                self._run_complete_threads(
                    refill_started, "refill_complete", f"{step_name} refill")

                refill_elapsed = time.monotonic() - refill_start
                end_time += refill_elapsed
                total_refill_time += refill_elapsed

                # refill 후 펌프 재시작 필요 → dosing_started 초기화
                self._dosing_started.clear()
                _start_verified.clear()   # 재시작 펌프 재검증
                _start_suspect.clear()
                continue

            # Pump start/update
            # @codesyncer-decision: 최초 1회만 start, 이후 재전송 금지
            # - 기존: 매 루프마다 running 체크 → RS-485 응답 누락 시 매초 9개 명령 반복
            # - 수정: dosing_started set으로 이미 시작된 펌프는 재전송하지 않음
            if not hasattr(self, '_dosing_started'):
                self._dosing_started = set()
            first_pump = True
            # @codesyncer-decision: 스태거 시작 — 시약 전선 정렬 (당량 왜곡 fix)
            # 라인이 비대칭이면 purge 시간이 달라 시약 스트림이 시간축으로
            # 어긋남 → 플러그 머리/꼬리에 단일 시약 구간(당량 깨짐) 발생.
            # 각 펌프를 (purge_max − purge_i)만큼 늦게 출발시키면 모든 채널의
            # 시약이 '동시에' 합류 진입을 시작하고 '동시에' 끝남 (fill_i 소진과
            # dosing 창 종료가 일치). offsets 미지정 시 기존 동작과 동일.
            _now = time.monotonic()
            for p_name, rate in flow_map.items():
                pump = self.pumps[p_name]
                rate = float(rate)
                if p_name not in self._dosing_started or float(getattr(pump, "target_flow", -1.0)) != rate:
                    _off = float((start_offsets or {}).get(p_name, 0.0) or 0.0)
                    if (_now - stagger_anchor) + 0.02 < _off:
                        continue  # 이 펌프의 시작 시각 미도래
                    if not first_pump:
                        time.sleep(0.35)
                    pump.set_flow(rate)
                    pump.start()
                    self._dosing_started.add(p_name)
                    first_pump = False

            # @codesyncer-decision: 펌프 start 명령이 실제로 나간 '직후'에
            #   타이머 기준점을 동기화 (분취 타이밍 버그 fix #4)
            # - on_pumps_started: 호출자가 timer.resume를 넘김 (injection/push 시작점)
            # - timer_resume_pending: 이 루프 내부 pause/refill로 멈췄던 timer 재개
            # → 밸브 전환·순차 start 격차(~1-3초)가 pumping 시간으로 잘못
            #   집계되어 모든 이벤트가 조기 발동되던 systematic early-bias 제거
            # @codesyncer(#12, 2026-07-13): 시작 '명령' ≠ '기동' — 무ACK 정지 펌프 검출.
            #   start 후 다음 폴(스태거 오프셋+1s 유예)에 driver.is_stopped()가 확정
            #   True 면 즉시 SafetyError — 타이머만 돌고 액체는 안 움직이는 '조용한
            #   빈 도징'을 1~2s 안에 차단. None(판별 불가 드라이버)은 통과(과차단 방지),
            #   빈 시린지 자동정지는 current_vol 가드로 오탐 제외.
            _nw2 = time.monotonic()
            for _pn in list(getattr(self, "_dosing_started", set())):
                if _pn in _start_verified:
                    continue
                _of2 = float((start_offsets or {}).get(_pn, 0.0) or 0.0)
                if _nw2 - stagger_anchor < _of2 + 1.0:
                    continue
                _pp = self.pumps.get(_pn)
                _drv = getattr(_pp, "driver", None)
                try:
                    _st = (_drv.is_stopped()
                           if (_drv is not None and hasattr(_drv, "is_stopped"))
                           else None)
                except Exception:
                    _st = None
                if _st is True and float(getattr(_pp, "current_vol", 1.0) or 0.0) > 0:
                    # 상태폴 지연/일시 오독 보호 — 연속 2회 확정 시에만 실패
                    _start_suspect[_pn] = _start_suspect.get(_pn, 0) + 1
                    if _start_suspect[_pn] >= 2:
                        raise SafetyError(
                            f"{step_name}: 펌프 '{_pn}' 시작 미확인 — start 명령 후에도 "
                            "정지 상태 (전원/RS-485/과전류 확인). 빈 도징 방지 위해 중단")
                else:
                    _start_suspect.pop(_pn, None)
                    _start_verified.add(_pn)

            if not started_cb_fired and on_pumps_started is not None:
                try:
                    on_pumps_started()
                except Exception as exc:
                    self._log(f"  [{step_name}] on_pumps_started callback error: {exc}")
                started_cb_fired = True
            if timer_resume_pending and self._collection_timer is not None:
                self._collection_timer.resume()
                timer_resume_pending = False

            # @codesyncer-decision: 실제 경과 시간 기반 volume tracking
            # - 기존: dt(예정값)로 차감 → loop overhead/RS-485 지연으로 drift 누적
            # - 수정: sleep 전후 시간 측정 → 실제 경과 시간으로 정확한 차감
            loop_start = time.monotonic()
            time.sleep(dt)
            actual_dt = time.monotonic() - loop_start

            for p_name, rate in flow_map.items():
                pump = self.pumps[p_name]
                if _is_smart_pump(pump):
                    if p_name not in self._dosing_started:
                        continue  # 스태거로 아직 시작 전인 펌프는 차감하지 않음
                    pump.current_vol = max(0.0, float(pump.current_vol) - (float(rate) * (actual_dt / 60.0)))

            elapsed = time.monotonic() - start_time
            pct = min(100.0, (elapsed / duration_sec) * 100.0)
            if step_name:
                self._emit_phase(step_name, pct)

        # @codesyncer-decision(잔량생성 fix, 2026-07-28): 시간 만료 → '자동정지 유예' 대기
        #   펌프는 set volume(=fill) 소진 시 스스로 정지한다(부피 구동). 도징 창은
        #   fill 소진 시간과 여유 0으로 항등이라, 순차 트리거 0.35s + set rate/volume/
        #   start 왕복 + 재시도만큼 늦게 출발한 펌프를 즉시 stop 하면 rate×지연 만큼
        #   미토출 → 개루프 상대변위 회계에선 이후 어떤 단계도 이 잔량을 밀지 않아
        #   스텝마다 누적된다. 유예창 동안 is_stopped 폴링으로 자연 자동정지를 기다리고,
        #   시한 초과 펌프만 강제 stop + '미토출 의심' 경고(진단 신호).
        _grace = float(getattr(self, "dosing_autostop_grace_sec", 0.0) or 0.0)
        _pending = [p for p in flow_map
                    if p in getattr(self, "_dosing_started", set())
                    and _is_smart_pump(self.pumps.get(p))
                    and hasattr(getattr(self.pumps.get(p), "driver", None), "is_stopped")]
        if _grace > 0 and _pending:
            _deadline = time.monotonic() + _grace
            _grace_t0 = time.monotonic()
            while _pending and time.monotonic() < _deadline:
                self._check_abort()
                _still = []
                for p_name in _pending:
                    try:
                        if self.pumps[p_name].driver.is_stopped() is True:
                            self.pumps[p_name].running = False
                            continue
                    except Exception:
                        pass
                    _still.append(p_name)
                _pending = _still
                if _pending:
                    time.sleep(0.5)
            _waited = time.monotonic() - _grace_t0
            if _pending:
                self._log(f"[{step_name}] ⚠ 자동정지 유예 {_grace:.0f}s 초과 — 강제 정지: "
                          f"{', '.join(_pending)} (미토출 의심)")
            elif _waited > 0.6:
                self._log(f"[{step_name}] 자동정지 유예 {_waited:.1f}s — 전 펌프 volume 소진 확인")

        # @codesyncer-decision: 이미 정지된 펌프는 stop 스킵
        #   - Chemyx는 set_volume 소진 시 자동 정지 → 이 상태에서 stop 응답 안 함
        #   - 무응답 재시도(3회 × 3펌프)가 RS-485 버스를 ~18초 점유 → 연쇄 통신 마비
        #   - is_stopped() 체크 후 이미 정지면 skip, 실패 시에만 stop 전송
        for p_name in flow_map:
            pump = self.pumps[p_name]
            try:
                if _is_smart_pump(pump):
                    stopped = pump.driver.is_stopped()
                    if stopped is True:
                        pump.running = False
                        continue  # 이미 정지 → stop 스킵
                pump.stop()
            except Exception:
                pass

        # dosing 종료 → 다음 dosing step을 위해 초기화
        if hasattr(self, '_dosing_started'):
            self._dosing_started.clear()

        # @codesyncer-risk: 루프가 pause 직후 종료된 경우 timer가 paused 상태로
        #   남으면 영원히 진행 안 됨 → wait_finish 타임아웃 유발. 안전망으로 resume.
        if timer_resume_pending and self._collection_timer is not None:
            self._collection_timer.resume()

        if step_name:
            elapsed = time.monotonic() - start_time
            self._emit_phase(step_name, 100.0)
            self._log(f"[{step_name}] done (elapsed={elapsed:.1f}s refill={total_refill_time:.1f}s)")

    def _smart_prefill_logic(self, inlet_ports: Dict[str, int], flows: Dict[str, float], fast_rate: float,
                             target_vol: float = None, total_flow: float = None,
                             line_src: Dict[str, float] = None,
                             inlet_vials: Dict[str, str] = None,
                             run_phase0: bool = True,
                             run_phase1: bool = True):
        self._stop_momentary()
        self._log("Pre-fill start")

        smart_names = [p_name for p_name in flows.keys() if _is_smart_pump(self.pumps.get(p_name))]
        if not smart_names:
            self._log("Pre-fill skipped: no syringe pumps")
            return

        # Prime Phase 0: 자기 분기 '딱 데드볼륨' 정량 프라임 — 스텝1·포트변경 시만
        # (2026-08-15 사용자 확정: "3a도 포트변경시만")
        # @codesyncer-decision(사용자 확정): 포트1 세척액을 자기 분기 데드볼륨
        #   (3way 내부 + pump_merge)만큼 '정확히' 리필한 뒤 전량 push — 용매 선단이
        #   QUAD 진입점에 딱 착지하고 시린지 잔량 0(시약과 혼합 없음). 여유분 금지
        #   (하류 공용부 침범 = 회계 불일치).
        #   전제: 직전 세척(스텝1=시스템 세척, 스텝2+=push 병행 세척)이 시린지를
        #   비움. 잔량이 남아 있으면 부족분만 채우고, 데드볼륨 미설정(0)이면 스킵.
        # @codesyncer-decision: 1-소스 라우팅은 Phase 0 스킵 — Chemyx 에선 '용매
        #   플러시'지만 1-소스에선 순수 시약을 waste 로 방출하는 동작이 됨.
        if run_phase0:
            self._emit_status("Pre-fill phase 0: prime own branch (exact dead volume)")
            prime_started = []
            for p_name in smart_names:
                pump = self.pumps[p_name]
                if self._pump_routing(p_name) != "external_valve":
                    continue
                line_inj = (float((getattr(self.cfg, "valve_internal_vol", {}) or {})
                                  .get(p_name, 0.0) or 0.0)
                            + float((getattr(self.cfg, "line_vol_pump_merge", {}) or {})
                                    .get(p_name, 0.0) or 0.0))
                if line_inj <= 0:
                    self._log(f"  [{p_name}] Phase-0 스킵 — 분기 데드볼륨 미설정(0). "
                              f"배관도 칩/원장으로 tube_vol_switcher·pump_merge 입력 필요")
                    continue
                cur = float(getattr(pump, "current_vol", 0.0) or 0.0)
                need = max(0.0, line_inj - cur)
                if cur > line_inj + 0.02:
                    self._log(f"  [{p_name}] ⚠ Phase-0: 잔량 {cur:.3f} > 분기 데드볼륨 "
                              f"{line_inj:.3f} — 전량 push 로 과량 진입(세척 배출 확인 필요)")
                if need > 0.005 and hasattr(pump, "refill"):
                    self._wait_pause_or_abort("prefill prime refill")
                    self._log(f"  [{p_name}] Phase-0 정량 리필 {need:.3f}mL "
                              f"(분기 데드볼륨 {line_inj:.3f})")
                    pump.refill(1, volume=need)
                if (float(getattr(pump, "current_vol", 0.0) or 0.0) > 0.005
                        and hasattr(pump, "prime_prepare")):
                    self._wait_pause_or_abort("prefill prime")
                    if pump.prime_prepare():
                        prime_started.append(p_name)
            self._sequential_trigger(prime_started, "prime_trigger")

            self._run_complete_threads(
                prime_started, "prime_complete", "prefill prime",
                extra_names=smart_names)
        else:
            self._log("  Prime Phase-0 생략 (스텝1·포트변경 시에만 수행)")

        # Prime Phase 1: 본류(반응기) total 볼륨 용매 충전 — 스텝1 전용
        # @codesyncer-decision(2026-08-15, 사용자 확정): 시린지가 port 1 용매를
        #   흡입해 반응기로 밀어 본류를 가득 채운다(압력 안정성). HPLC 는 push
        #   전용 — prime 불관여(용어 포함, 구 [HPLC-Prime] 폐지).
        #   부피 = system_params.prime_phase1_vol_ml (0=자동 (mixing+reactor+post)×1.1)
        #   유속 = 펌프의 prime_rate (= priming_rate_ml_min, Prime Rate 단일 설정)
        #     @codesyncer(2026-08-15, 사용자 지시): Ph1 전용 rate 폐지 — prime 속도
        #     구분이 실익 없이 UI/설정만 복잡하게 했다. Phase-0/1 동일 속도.
        #   활성 시린지(external_valve) 균등 분담, 시린지 용량 초과 시 라운드 반복.
        #   Outlet=WASTE(호밍 상태) 전제 — 충전 배출액은 폐기로 빠진다.
        if run_phase1:
            sp_cfg = (self.cfg.config_data.get("system_params", {})
                      if hasattr(self, "cfg") else {})
            v1 = float(sp_cfg.get("prime_phase1_vol_ml", 0.0) or 0.0)
            if v1 <= 0:
                mixing = float(getattr(self.cfg, "mixing_line_dead_vol", 0.0) or 0.0)
                v1 = (float(getattr(self, "vol_reactor", 0.0)) + mixing
                      + float(getattr(self, "vol_post_common", 0.0))) * 1.1
            ext_names = [p for p in smart_names
                         if self._pump_routing(p) == "external_valve"]
            if v1 > 0.005 and ext_names:
                share = v1 / len(ext_names)
                self._emit_status("Pre-fill phase 1: reactor solvent prime")
                self._log(f"  [Prime-P1] 본류 용매 충전 {v1:.2f}mL "
                          f"(펌프당 {share:.2f}mL, port 1 → Reactor, Outlet=WASTE)")
                remaining = {p: share for p in ext_names}
                while any(v > 0.005 for v in remaining.values()):
                    batch = []
                    for p in ext_names:
                        if remaining[p] <= 0.005:
                            continue
                        pump = self.pumps[p]
                        take = min(remaining[p],
                                   float(getattr(pump, "capacity", share) or share))
                        self._wait_pause_or_abort("prime phase1 refill")
                        if pump.refill_prepare(1, volume=take):
                            batch.append((p, take))
                    if not batch:
                        self._log("  [Prime-P1] ⚠ 리필 시작 실패 — 잔여 충전 중단")
                        break
                    names = [p for p, _ in batch]
                    self._sequential_trigger(names, "refill_trigger")
                    self._run_complete_threads(
                        names, "refill_complete", "prime phase1 refill",
                        log_prefix="Prime-P1: ")
                    infuse_names = []
                    for p, _take in batch:
                        pump = self.pumps[p]
                        self._wait_pause_or_abort("prime phase1 infuse")
                        if hasattr(pump, "prime_prepare") and pump.prime_prepare():
                            infuse_names.append(p)
                    self._sequential_trigger(infuse_names, "prime_trigger")
                    self._run_complete_threads(
                        infuse_names, "prime_complete", "prime phase1",
                        log_prefix="Prime-P1: ")
                    for p, take in batch:
                        remaining[p] -= take
                self._log("  [Prime-P1] 본류 용매 충전 완료 — 압력 안정 상태")
        else:
            self._log("  Prime Phase-1 생략 (스텝1 전용 — 본류는 이미 충전 상태)")

        # 시약 장전(reagent charge): inject_vol만 정확히 채움 — 매 스텝
        # (2026-08-15 명칭 정리: 구 'Phase 1' — 사용자 정의 Prime Phase-1(본류
        #  용매 충전)과 이름이 겹쳐 개명. 동작 불변.)
        # @codesyncer-decision: flush_vol/Phase 2/Phase 1b 전부 제거 (2026-04-10)
        #   inject_vol = (pump_flow / total_flow) × target_vol
        #   → 3펌프 동시 소진 보장, 잔량 최소화, 로직 단순화
        #
        # ──── 데드볼륨 보정 복원 가이드 ────────────────────────────
        # 복원 시 이 블록에서 fill_vol 계산을 아래처럼 변경:
        #
        #   mixing_vol = float(getattr(self.cfg, "mixing_line_dead_vol", 0.0))
        #   flush_vol = (pump_flow / total_flow) * mixing_vol
        #   fill_vol = min(inject_vol + flush_vol, pump.capacity)
        #
        # 그리고 Phase 1 이후에 Phase 2 (flush mixing line) 추가:
        #   → mixing_vol만큼 시약을 반응 유속 비율로 밀어서 mixing line 치환
        #   → 백업 참조: "2026-04-10 백업(데드볼륨 제거)/" 폴더의 strict_engine.py
        #
        # 제거 이유: flush_vol 과충전 → injection 후 잔량 → 용매 혼합 → 볼륨 추적 꼬임
        # config.py의 mixing_line_dead_vol, dead_vol_solvent/reagent 계산은 유지됨
        # ─────────────────────────────────────────────────────────
        self._emit_status("Pre-fill: reagent refill")

        def _calc_fill(p_name, pump):
            if target_vol is not None and total_flow is not None and total_flow > 0:
                pump_flow = float(flows.get(p_name, 0))
                inject_vol = (pump_flow / total_flow) * target_vol
                # @codesyncer-decision: 소스 라인(src) 데드볼륨 보정 충전
                # 라인의 구내용물이 시린지에 먼저 들어오므로 src만큼 과충전해야
                # 순수 시약 inject_vol이 확보됨. 과충전분은 주입 창 연장(purge)으로
                # 전부 토출되어 잔량 0 유지 (validation에서 capacity 사전 검증됨)
                src_vol = float((line_src or {}).get(p_name, 0.0) or 0.0)
                return min(inject_vol + src_vol, pump.capacity)
            return None

        # ── 니들 직렬화: autosampler 그룹은 병렬 리필에서 분리 ──
        # @codesyncer-decision: 니들 1개는 물리적으로 두 vial 에 동시에 담글 수
        #   없음 — 원본 RoboChem 도 별도 프리미티브 없이 "한 스레드 순차 블로킹
        #   호출"로 직렬화한다(PrepareReactionSlug). 그룹별로
        #   [니들 이동(ack) → 흡입 완료(기존 complete 스레드 재사용) → 리트랙트]
        #   를 순차 수행하고, 나머지 펌프는 기존 병렬 경로 그대로.
        as_names = [p for p in smart_names if self._autosampler_coord(p) is not None]
        par_names = [p for p in smart_names if p not in as_names]

        refill_started = []
        for p_name in par_names:
            pump = self.pumps[p_name]
            reagent_port = int(inlet_ports.get(p_name, 2))
            fill_vol = _calc_fill(p_name, pump)
            if fill_vol is not None:
                self._log(f"  {p_name}: fill {fill_vol:.2f}mL (port {reagent_port})")
            else:
                self._log(f"  {p_name}: full capacity (port {reagent_port})")
            self._wait_pause_or_abort("prefill refill")
            if pump.refill_prepare(reagent_port, volume=fill_vol):
                refill_started.append(p_name)
        self._sequential_trigger(refill_started, "refill_trigger")

        self._run_complete_threads(
            refill_started, "refill_complete", "prefill refill",
            extra_names=smart_names)

        for p_name in as_names:
            pump = self.pumps[p_name]
            coord = self._autosampler_coord(p_name)
            # per-step vial(플랜) 우선, 없으면 그룹 설정 폴백 (Phase A)
            vial = (inlet_vials or {}).get(p_name) or self._autosampler_source_vial(p_name)
            fill_vol = _calc_fill(p_name, pump)
            self._log(f"  {p_name}: fill "
                      f"{'full capacity' if fill_vol is None else f'{fill_vol:.2f}mL'}"
                      f" (vial {vial})")
            self._wait_pause_or_abort("prefill needle")
            ok, msg = coord.position_for_withdraw(vial)
            if not ok:
                raise SafetyError(f"[{p_name}] 니들 위치 실패: {msg}")
            try:
                self._wait_pause_or_abort("prefill refill(AS)")
                # NRG 어댑터 refill 은 내장밸브 OFF(=니들 라인)에서 흡입 —
                # port 인자는 무시되므로 1 전달 (기존 규약)
                if pump.refill_prepare(1, volume=fill_vol):
                    self._sequential_trigger([p_name], "refill_trigger")
                    # 기존 complete 스레드 경로 재사용 → E-Stop 인터락/모니터링 동일
                    self._run_complete_threads(
                        [p_name], "refill_complete", "prefill refill(AS)",
                        extra_names=[p_name])
                else:
                    self._log(f"  [{p_name}] refill 시작 실패")
            finally:
                # 흡입 종료(성공/중단 무관) 후 니들은 반드시 액면 밖으로
                coord.after_withdraw()

        self._log("Pre-fill complete")


# ══════════════════════════════════════════════════════════════════════
# HTE droplet — 프로파일/타임라인 순수 함수 (엔진·시뮬 공유, 동일성 보장)
# ══════════════════════════════════════════════════════════════════════
def hte_build_profile(steps, *, reactor_vol, mixing, post, vol_collection,
                      deadvols, active_pumps, tj, purge_factor, purge_order,
                      override_delay, v_spacer, v_wash_sol, v_wash_gas, primed=None,
                      v_interwash=0.0, tj_entry=None):
    """구동 프로파일·이벤트 부피마크·헤드부피 계산 (하드웨어/시리얼 무의존).

    steps: [dict(flows, F, v_slug, q_equiv, ports), ...] — v_purge 를 채워 넣는다.
    deadvols: {inlet, valve_pump, selector, switcher, pump_merge} 각 {pump: mL}.
    v_interwash>0 이면 슬러그 '사이'에 [세척용매 플러그 + N2] 삽입 —
      [슬러그|N2|용매|N2|슬러그] 트레인. 제품 이웃이 전부 N2/깨끗한 용매가 되어
      타이밍 오차의 피해가 교차오염→희석으로 강등(벽면 액막 캐리오버도 완화).
      마지막 슬러그 뒤는 기존 트레인-엔드 세척(N2→용매→N2)이 담당하므로 미삽입.
    반환: dict(v_head, profile[(rate,dur,kind,i)], marks[(V,kind,i)], v_gasB).
    """
    di, dvp, dsel = deadvols["inlet"], deadvols["valve_pump"], deadvols["selector"]
    dsw, dpm = deadvols["switcher"], deadvols["pump_merge"]
    s0 = steps[0]
    F0 = s0["F"]
    line_src0, line_inj0 = {}, {}
    for p in s0["flows"]:
        l1 = float(di.get(p, 0.0) or 0.0)
        l2 = float(dvp.get(p, 0.0) or 0.0) + float(dsel.get(p, 0.0) or 0.0)
        line_src0[p] = (l2 + l1) * purge_factor
        line_inj0[p] = float(dpm.get(p, 0.0) or 0.0) + float(dsw.get(p, 0.0) or 0.0)
    ordered = [p for p in active_pumps if p in s0["flows"]]
    # @codesyncer(감사 2026-07-13 이슈2): v_head 에서 소스 퍼지(purge_sec)·deficit 제거.
    #   기존: FIFO pre_sec(=purge_sec+inj_path_sec)를 부피화해 v_head 에 넣었는데,
    #   collect 마크가 슬러그별 v_purge(동일한 퍼지 부피)를 '또' 가산 → 소스 퍼지가
    #   정확히 2중 계상되어 Outlet=COLLECT 가 v_purge 만큼 늦게 열림(슬러그 순수
    #   머리 유실 + v_spacer<v_purge 구성에선 꼬리쪽 오염). 퍼지 회계는 마크의
    #   v_purge 가 전담(프라이밍 상태 반영까지 정확) — v_head 는 주입경로+믹싱+
    #   리액터+포스트 순수 수송분만. purge_order="lifo" 호출은 pre_sec 에서
    #   inj_path_sec 만 추출하는 의도적 사용(lifo 분기: pre_sec=inj, deficit=0).
    _, inj_path_sec, _, _ = StrictSequenceEngine._compute_plug_timing(
        s0["flows"], ordered, line_src0, line_inj0, tj or {}, "lifo",
        entry_map=tj_entry or None)
    if override_delay is not None and float(override_delay) > 0:
        v_head = F0 * float(override_delay) / 60.0
    else:
        v_head = (float(reactor_vol) + float(mixing) + float(post)
                  + F0 * inj_path_sec / 60.0)

    # 슬러그별 퍼지(FIFO 과충전 선행배출) — primed 진행 시뮬
    # @codesyncer(2026-08-13, 소스퍼지 무효화와 동일 물리): lifo(정확 흡입 신워크플로)
    #   에선 슬러그별 과충전이 존재하지 않음 — v_purge=0 (마크 = v_head+슬러그+스페이서
    #   누적만). primed 장부는 order 무관하게 진행(포트 재사용 판정용). fifo 레거시 유지.
    _pv_lifo = str(purge_order or "fifo").lower() == "lifo"
    plan_primed = {p: set(ps) for p, ps in (primed or {}).items()}
    for st in steps:
        pv = 0.0
        for p, f in st["flows"].items():
            if f <= 0:
                continue
            l1 = float(di.get(p, 0.0) or 0.0)
            l2 = float(dvp.get(p, 0.0) or 0.0) + float(dsel.get(p, 0.0) or 0.0)
            pr = plan_primed.setdefault(p, set())
            src = (l2 + (0.0 if st["ports"][p] in pr else l1)) * purge_factor
            if not _pv_lifo:
                pv = max(pv, (src / f) * st["F"])
            pr.add(st["ports"][p])
        st["v_purge"] = pv

    profile, marks, v_cum = [], [], 0.0
    # @codesyncer(C1): waste 마크 보정 — 기존엔 '슬러그 꼬리가 밸브 통과' 시점에 WASTE
    #   전환 → 밸브→웰 수집라인(vol_collection)에 꼬리가 잔류, 다음 collect 때 다음 웰로
    #   이월(교차오염)+웰당 회수부족. 수정: 스페이서 가스가 라인 속 꼬리를 웰까지 밀어낸
    #   뒤(+v_push) 전환. 스페이서보다 큰 라인은 다 못 밀므로 min 클램프(잔여 이월은
    #   엔진이 경고 로그). 이벤트 순서 보장: v_push ≤ v_spacer ≤ 다음 collect 간격.
    v_push = min(float(vol_collection), float(v_spacer))
    # @codesyncer(하이브리드): 센서(아웃렛 직전)가 '관측 가능한' 기체↔액체 엣지 목록.
    #   물리 제약 — ①퍼지는 액체라 G→L 은 슬러그 머리가 아닌 '퍼지 머리'(=스페이서 끝)
    #   에서 발생 → collect 마크는 엣지 + 퍼지부피 데드레코닝 ②슬러그1 머리는 앞이
    #   기존 라인 액체(연속)라 엣지가 없음 → i==0 은 L2G(꼬리)만 관측 가능.
    edges = []
    for i, st in enumerate(steps):
        if i > 0:
            edges.append((v_head + v_cum, "G2L", f"slug{i+1}-head"))
        v_cum += st["v_purge"]
        marks.append((v_head + v_cum, "collect", i))
        profile.append((st["F"], (st["v_slug"] + st["v_purge"]) / st["F"] * 60.0, "slug", i))
        v_cum += st["v_slug"]
        edges.append((v_head + v_cum, "L2G", f"slug{i+1}-tail"))
        marks.append((v_head + v_cum + v_push, "waste", i))
        profile.append((st["q_equiv"], v_spacer / st["q_equiv"] * 60.0, "gas", i))
        v_cum += v_spacer
        # 슬러그간 세척: [용매 플러그 + N2] — 전부 WASTE 구간(마크 불변)
        if v_interwash > 0 and (i + 1) < len(steps):
            edges.append((v_head + v_cum, "G2L", f"interwash{i+1}-head"))
            profile.append((st["F"], v_interwash / st["F"] * 60.0, "interwash", i))
            v_cum += v_interwash
            edges.append((v_head + v_cum, "L2G", f"interwash{i+1}-tail"))
            profile.append((st["q_equiv"], v_spacer / st["q_equiv"] * 60.0, "gas2", i))
            v_cum += v_spacer
    qg, Fw = steps[-1]["q_equiv"], steps[-1]["F"]
    profile.append((qg, v_wash_gas / qg * 60.0, "washgasA", -1))
    v_cum += v_wash_gas
    if v_wash_sol > 0:
        edges.append((v_head + v_cum, "G2L", "wash-sol-head"))
    profile.append((Fw, v_wash_sol / Fw * 60.0, "washsol", -1))
    v_cum += v_wash_sol
    if v_wash_sol > 0:
        edges.append((v_head + v_cum, "L2G", "wash-sol-tail"))
    need_v = marks[-1][0] + float(vol_collection)
    v_gasB = max(v_wash_gas, need_v - v_cum + 0.05)
    profile.append((qg, v_gasB / qg * 60.0, "washgasB", -1))
    # 도달 불가능 엣지 제거 — 최종 가스(v_gasB)는 '마지막 슬러그가 수집라인을 지나는'
    # 만큼만 설계되므로, v_head 가 크면 세척용매 경계가 트레인 종료 전에 센서에
    # 도달하지 않음 → 예상 목록에 두면 매 트레인 거짓 '미검출' 경고.
    total_v = v_cum + v_gasB
    edges = [e for e in edges if e[0] <= total_v]
    return dict(v_head=v_head, profile=profile, marks=marks, v_gasB=v_gasB,
                v_push=v_push, edges=edges)


def hte_rollout(steps, plan, *, first_tube=1):
    """프로파일 → 재생용 타임라인. (배관도 시뮬 시각화 전용)

    반환 dict(frames, valve_events, total_t):
      frames=[dict(t0,t1,kind,step_i,flows,gas,label,comp)]  # '도징' 관점
      valve_events=[(t, pos, well|None)]                     # '수집' 관점
    @codesyncer-decision: outlet/well 을 프레임에 굽지 않는다 — 도징과 수집은
      리액터 transit(v_head)으로 시간축 분리되므로, 재생 시점의 밸브 상태는
      valve_events 를 sim_t 까지 스캔해 독립 계산한다 (마지막 wash 세그먼트
      중 수집이 일어나는 경우도 정확 — 프레임 경계 스냅 버그 제거).
    """
    profile, marks = plan["profile"], plan["marks"]

    def t_of(V):
        t, v = 0.0, 0.0
        for rate, dur, _k, _i in profile:
            dv = rate * dur / 60.0
            if v + dv >= V and rate > 0:
                return t + (V - v) / rate * 60.0
            v += dv
            t += dur
        return t

    valve_events = sorted(
        ((t_of(V), 2 if kind == "collect" else 1,
          (first_tube + i) if kind == "collect" else None)
         for V, kind, i in marks), key=lambda e: e[0])

    frames, t = [], 0.0
    for rate, dur, kind, i in profile:
        t0, t1 = t, t + dur
        if kind == "slug":
            comp = {p: round(f / steps[i]["F"], 3) for p, f in steps[i]["flows"].items()}
            label = f"Slug {i+1}: dosing " + " / ".join(f"{p}={c:.0%}" for p, c in comp.items())
            flows, gas = dict(steps[i]["flows"]), False
        elif kind == "gas":
            comp, flows, gas = None, {}, True
            label = f"Slug {i+1}: N2 spacer"
        elif kind == "washsol":
            comp, flows, gas = None, dict(steps[-1]["flows"]), False
            label = "Wash: solvent plug"
        else:
            comp, flows, gas = None, {}, True
            label = "Wash: N2"
        frames.append(dict(t0=t0, t1=t1, kind=kind, step_i=i, flows=flows,
                           gas=gas, label=label, comp=comp))
        t = t1
    return dict(frames=frames, valve_events=valve_events, total_t=t)
