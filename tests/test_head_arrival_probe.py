"""HeadArrivalProbe — 표준 경로 HEAD 도달 실측 프로브 (RoboChem 센서구동 이식) 검증.

루트에서 실행:  py -3.14 tests\test_head_arrival_probe.py

검증 대상 계약 (RoboChem WaitForPhaseChange/MonitorPhase 에서 가져온 것):
  · 예상창 게이트 — 창 밖 엣지는 잡기포로 기각
  · 타임아웃(창 마감) = 예외가 아니라 '미검출' 폴백
  · 일시정지 중 엣지 무시 (무유량 구간의 계면 왕복 방어)
  · finally 에서 monitor("never") — 센서 상태 누수 금지
  · observe = 제어 무영향 / anchor 만 타이머 재앵커
  · ADC 스텝은 confirm_n 연속이어야 인정 (단발 노이즈 기각)
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import HeadArrivalProbe   # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


class FakeSensor:
    """OPB 드라이버의 프로브 소비 API만 흉내 — monitor/read_event/analog.

    edge_at 를 주면 '타이머 경과가 그 값에 도달한 뒤 1회' 엣지를 반환한다
    (폴 횟수에 의존하지 않는 결정론적 시나리오). queued 는 드라이버 큐에 미리
    쌓여 있는 스테일 엣지를 흉내낸다.
    """

    def __init__(self, events=None, adc_series=None, raise_on_read=False,
                 edge_at=None, timer=None, queued=0):
        self.monitor_calls = []
        self._events = list(events or [])       # read_event 가 순서대로 반환
        self._adc = list(adc_series or [])      # analog 가 순서대로 반환(마지막 값 유지)
        self.raise_on_read = raise_on_read
        self.edge_at = edge_at
        self.timer = timer
        self._queued = int(queued)
        self._edge_fired = False
        # P0 지원: 프로브가 GAS 배제·지속성 확인에 쓰는 채널맵/임계/현재상
        self.sensors = {"collect": 0}
        self.thresholds = {0: 440}

    def read_phase(self, k="collect"):
        """엣지 발화 후 = 액체 유지 (지속성 확인 통과용). 그 전 = 기체."""
        return "CLEAR_LIQUID" if self._edge_fired else "GAS"

    def monitor(self, which, mode):
        self.monitor_calls.append((which, mode))

    def read_event(self, which="collect"):
        if self.raise_on_read:
            raise RuntimeError("serial down")
        if self._queued > 0:                    # 큐에 남은 과거 엣지 먼저
            self._queued -= 1
            return "CLEAR_LIQUID"
        if self.edge_at is not None:
            if (not self._edge_fired and self.timer is not None
                    and self.timer._pumping_elapsed() >= self.edge_at):
                self._edge_fired = True
                return "CLEAR_LIQUID"
            return None
        return self._events.pop(0) if self._events else None

    def analog(self, which="collect"):
        if not self._adc:
            return -1
        return self._adc.pop(0) if len(self._adc) > 1 else self._adc[0]


class FakeTimer:
    """CollectionTimer 중 프로브가 만지는 표면만 — 경과/일시정지/shift."""

    def __init__(self, elapsed=0.0, paused=False):
        self.start_time = 1.0
        self._elapsed = float(elapsed)
        self._pause_lock = threading.Lock()
        self._pause_start = 1.0 if paused else None
        self.shifts = []

    def _pumping_elapsed(self):
        return self._elapsed

    def shift(self, d):
        self.shifts.append(d)


class FakeEngine:
    def __init__(self):
        self.logs = []
        self.abort_flag = False

    def _log(self, m):
        self.logs.append(m)


def run_probe(sensor, timer, mode="observe", t_exp=100.0, window=20.0,
              adc_delta=0.0, drive=None, timeout=3.0, confirm_sec=0.05):
    """프로브를 띄우고 drive(timer) 로 시간을 진행시킨 뒤 종료를 기다린다.
    P0 지속성 창은 테스트 속도를 위해 짧게(0.05s) — 계약(소급 발화)은 동일."""
    eng = FakeEngine()
    p = HeadArrivalProbe(eng, sensor, "collect", timer, t_exp, window,
                         mode=mode, adc_delta=adc_delta, poll=0.01,
                         confirm_sec=confirm_sec)
    p.start()
    if drive:
        drive(timer)
    p.join(timeout=timeout)
    if p.is_alive():
        p.stop()
    return eng, p


def advance(timer, seq, dt=0.03):
    def _d(_t):
        for v in seq:
            timer._elapsed = v
            time.sleep(dt)
    return _d


print("=" * 72)
print("[1] 모드 게이트")
print("=" * 72)
s = FakeSensor(events=["CLEAR_LIQUID"])
t = FakeTimer(elapsed=100.0)
eng, p = run_probe(s, t, mode="off", drive=advance(t, [100.0]))
check("off 모드는 아무것도 하지 않음", p.detected_sec is None and not s.monitor_calls
      and not t.shifts, f"monitor호출={len(s.monitor_calls)}")

print()
print("=" * 72)
print("[2] observe — 검출하되 제어는 건드리지 않음")
print("=" * 72)
t = FakeTimer(elapsed=70.0)
s = FakeSensor(edge_at=105.0, timer=t)
eng, p = run_probe(s, t, mode="observe", t_exp=100.0, window=20.0,
                   drive=advance(t, [70.0, 85.0, 105.0, 106.0, 107.0]))
check("창 안 엣지 검출", p.detected_sec is not None, f"detected={p.detected_sec}")
check("Δ 계산 = 실측 − 예상", p.delta_sec is not None and abs(p.delta_sec - (p.detected_sec - 100.0)) < 1e-9,
      f"Δ={p.delta_sec:+.1f}s")
check("observe 는 타이머 재앵커 안 함", not t.shifts and p.applied_shift == 0.0)
check("검출기 = phase", p.detector == "phase")
check("monitor 해제됨(상태 누수 없음)", ("collect", "never") in s.monitor_calls,
      str(s.monitor_calls))

print()
print("=" * 72)
print("[3] anchor — 실측 엣지로 타이머 재앵커")
print("=" * 72)
t = FakeTimer(elapsed=70.0)
s = FakeSensor(edge_at=118.0, timer=t)
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=30.0,
                   drive=advance(t, [70.0, 90.0, 118.0, 119.0]))
check("anchor 는 shift 호출", len(t.shifts) == 1, f"shifts={t.shifts}")
check("shift 값 = Δ", t.shifts and abs(t.shifts[0] - p.delta_sec) < 1e-9)
check("늦은 도달 → 양수 Δ (이벤트 지연)", p.delta_sec is not None and p.delta_sec > 0,
      f"Δ={p.delta_sec:+.1f}s")

print()
print("=" * 72)
print("[4] 창 게이트 — 창보다 이른 엣지는 무시")
print("=" * 72)
t = FakeTimer(elapsed=10.0)
s = FakeSensor(edge_at=20.0, timer=t)     # 창(80s) 한참 전에 발생한 엣지
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=20.0,
                   drive=advance(t, [10.0, 30.0, 50.0, 85.0, 90.0]))
check("창 이전 엣지는 선단으로 인정 안 함", p.detected_sec is None and not t.shifts,
      f"detected={p.detected_sec}")

print()
print("=" * 72)
print("[4b] 스테일 엣지 배수 — 창 진입 순간 과거 엣지로 오검출 금지")
print("=" * 72)
t = FakeTimer(elapsed=70.0)
s = FakeSensor(queued=3, edge_at=115.0, timer=t)   # 큐에 과거 엣지 3건 + 진짜 선단
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=25.0,
                   drive=advance(t, [70.0, 78.0, 80.0, 90.0, 115.0, 116.0, 117.0]))
check("스테일 엣지 폐기됨", p.spurious >= 3, f"spurious={p.spurious}")
check("진짜 선단만 검출", p.detected_sec is not None and p.detected_sec >= 115.0,
      f"detected={p.detected_sec}")
check("재앵커는 진짜 선단 기준", len(t.shifts) == 1 and t.shifts[0] > 0,
      f"shifts={t.shifts}")
check("배수 로그 남김", any("스테일" in m for m in eng.logs))

print()
print("=" * 72)
print("[5] 타임아웃 — 예외가 아니라 미검출 폴백")
print("=" * 72)
s = FakeSensor(events=[])
t = FakeTimer(elapsed=100.0)
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=10.0,
                   drive=advance(t, [100.0, 105.0, 130.0]))
check("창 마감 후 missed", p.missed is True)
check("미검출 시 재앵커 없음", not t.shifts)
check("예외 없이 폴백 로그", any("미검출" in m for m in eng.logs))

print()
print("=" * 72)
print("[6] 일시정지 중 엣지 무시 (무유량 구간)")
print("=" * 72)
s = FakeSensor(events=["CLEAR_LIQUID"] * 5)
t = FakeTimer(elapsed=100.0, paused=True)
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=8.0,
                   drive=advance(t, [100.0, 101.0, 115.0]))
check("pause 중엔 검출 안 함", p.detected_sec is None and not t.shifts)

print()
print("=" * 72)
print("[7] ADC 스텝 검출 — 유색 시약 선단")
print("=" * 72)
# 베이스라인 800 축적 → 창 안에서 600 이 연속 → 검출
# (P0: 임계 440 아래 값은 GAS=기포로 배제되므로 시약 레벨은 임계 위여야 함)
adc = [800.0] * 6 + [600.0] * 10
s = FakeSensor(events=[None] * 40, adc_series=adc)
t = FakeTimer(elapsed=80.0)
eng, p = run_probe(s, t, mode="observe", t_exp=100.0, window=25.0, adc_delta=150.0,
                   drive=advance(t, [80.0, 82.0, 84.0, 86.0, 90.0, 95.0,
                                     100.0, 101.0, 102.0, 103.0, 104.0], dt=0.04))
check("ADC 스텝으로 선단 검출", p.detector == "adc", f"detector={p.detector}")
check("베이스라인이 창 전 값으로 잡힘", p.baseline_adc is not None and abs(p.baseline_adc - 800.0) < 1.0,
      f"baseline={p.baseline_adc}")

# 단발 노이즈는 기각 (confirm_n=3 미만)
adc = [800.0] * 6 + [600.0, 800.0, 600.0, 800.0] + [800.0] * 10
s = FakeSensor(events=[None] * 40, adc_series=adc)
t = FakeTimer(elapsed=80.0)
eng, p = run_probe(s, t, mode="observe", t_exp=100.0, window=15.0, adc_delta=150.0,
                   drive=advance(t, [80.0, 84.0, 88.0, 92.0, 96.0, 100.0,
                                     101.0, 102.0, 103.0, 104.0, 130.0], dt=0.04))
check("단발 ADC 노이즈는 기각", p.detected_sec is None, f"detected={p.detected_sec}")
check("노이즈 기각 카운트됨", p.spurious >= 1, f"spurious={p.spurious}")

# P0-2: 임계 아래(=GAS 분류) 수준은 아무리 지속돼도 선단 아님 — 기포 열차 방어
adc = [800.0] * 6 + [80.0] * 12
s = FakeSensor(events=[None] * 40, adc_series=adc)
t = FakeTimer(elapsed=80.0)
eng, p = run_probe(s, t, mode="observe", t_exp=100.0, window=15.0, adc_delta=150.0,
                   drive=advance(t, [80.0, 84.0, 88.0, 92.0, 96.0, 100.0,
                                     102.0, 104.0, 106.0, 120.0], dt=0.04))
check("GAS 수준 지속은 배제 (기포 열차)", p.detected_sec is None,
      f"detected={p.detected_sec}")

print()
print("=" * 72)
print("[8] 센서 예외 — 프로브만 죽고 런은 계속")
print("=" * 72)
s = FakeSensor(raise_on_read=True)
t = FakeTimer(elapsed=100.0)
eng, p = run_probe(s, t, mode="anchor", t_exp=100.0, window=20.0,
                   drive=advance(t, [100.0, 101.0]))
check("예외 전파 없이 종료", not p.is_alive())
check("재앵커 없음", not t.shifts)
check("센서 오류 로그", any("센서 오류" in m for m in eng.logs))
check("예외 경로도 monitor 해제", ("collect", "never") in s.monitor_calls)

print()
print("=" * 72)
print("[9] abort 즉시 종료")
print("=" * 72)
s = FakeSensor(events=[None] * 50)
t = FakeTimer(elapsed=95.0)
eng = FakeEngine()
p = HeadArrivalProbe(eng, s, "collect", t, 100.0, 20.0, mode="anchor", poll=0.01)
p.start()
time.sleep(0.05)
eng.abort_flag = True
p.join(timeout=2.0)
check("abort 시 스레드 종료", not p.is_alive())
check("abort 시 재앵커 없음", not t.shifts)

print()
print("=" * 72)
print(f"RESULT: {'ALL PASS' if not FAILED else 'FAIL — ' + ', '.join(FAILED)}")
print("=" * 72)
sys.exit(1 if FAILED else 0)
