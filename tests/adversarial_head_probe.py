"""HeadArrivalProbe 적대적 검증 — 방어가 아니라 '뚫기'가 목적인 테스트.

루트에서 실행:  py -3.14 tests\adversarial_head_probe.py

각 공격은 통과/실패가 아니라 **뚫렸는지**를 보고한다.
  DEFENDED = 공격 실패 (프로브가 막음)
  BREACHED = 공격 성공 (결함 확인) → 수정 대상

이 파일은 회귀 테스트가 아니다. 결함이 고쳐지면 해당 공격은 DEFENDED 로 바뀐다.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import HeadArrivalProbe   # noqa: E402

RESULTS = []


def report(attack, breached, detail=""):
    tag = "BREACHED" if breached else "DEFENDED"
    print(f"  [{tag}] {attack}" + (f"\n             {detail}" if detail else ""))
    RESULTS.append((attack, breached))


class Sensor:
    """adc_fn(elapsed) 로 광학 신호를 모델링. phase 이벤트는 edge_fn(elapsed).
    P0 반영(2026-08-18): 프로브가 소비하는 sensors/thresholds/read_phase 제공."""

    def __init__(self, timer, adc_fn=None, edge_fn=None):
        self.timer = timer
        self.adc_fn = adc_fn or (lambda e: 800.0)
        self.edge_fn = edge_fn
        self.monitor_calls = []
        self._fired = set()
        self.sensors = {"collect": 0}
        self.thresholds = {0: 440}

    def monitor(self, which, mode):
        self.monitor_calls.append((which, mode))

    def analog(self, which="collect"):
        return self.adc_fn(self.timer._pumping_elapsed())

    def read_phase(self, which="collect"):
        adc = self.adc_fn(self.timer._pumping_elapsed())
        return "CLEAR_LIQUID" if adc > self.thresholds[0] else "GAS"

    def read_event(self, which="collect"):
        if self.edge_fn is None:
            return None
        return self.edge_fn(self.timer._pumping_elapsed(), self._fired)


class Timer:
    def __init__(self, elapsed=0.0):
        self.start_time = 1.0
        self._elapsed = float(elapsed)
        self._pause_lock = threading.Lock()
        self._pause_start = None
        self.shifts = []
        self.dead = False

    def _pumping_elapsed(self):
        return self._elapsed

    def shift(self, d):
        if self.dead:
            raise RuntimeError("timer already stopped")
        self.shifts.append(d)


class Engine:
    def __init__(self):
        self.logs = []
        self.abort_flag = False

    def _log(self, m):
        self.logs.append(m)


def drive(probe, timer, seq, dt=0.05, timeout=6.0):
    for v in seq:
        timer._elapsed = v
        time.sleep(dt)
    probe.join(timeout=timeout)
    if probe.is_alive():
        probe.stop()


def run(sensor, timer, seq, **kw):
    eng = Engine()
    kw.setdefault("poll", 0.01)
    p = HeadArrivalProbe(eng, sensor, "collect", timer, kw.pop("t_exp", 100.0),
                         kw.pop("window", 30.0), **kw)
    p.start()
    drive(p, timer, seq)
    return eng, p


print("=" * 78)
print("공격 1 — 베이스라인 기포 오염 (평균이 무너지는가)")
print("=" * 78)
# 창 전(<70s) 구간에 기포가 섞임: 용매 800 / 기포 80 교대
# 시약 선단은 990 (임계 440 위 = 액체로 읽히는 시약 — 아래로 깎이는 진한
# 유색은 모드 A 의 문서화된 별도 한계). adc_delta=150.
def adc_bubbly(e):
    if e < 70.0:
        return 80.0 if int(e) % 2 == 0 else 800.0     # 기포 섞인 베이스라인
    return 990.0 if e >= 100.0 else 800.0             # 100s 에 시약 선단
t = Timer(50.0)
s = Sensor(t, adc_fn=adc_bubbly)
eng, p = run(s, t, [50.0, 52.0, 54.0, 56.0, 58.0, 60.0, 62.0, 71.0, 80.0, 90.0,
                    100.0, 101.0, 102.0, 103.0, 110.0, 125.0],
             t_exp=100.0, window=30.0, adc_delta=150.0)
base = p.baseline_adc
mis = (p.detected_sec is None) or (p.detected_sec < 100.0)
report("베이스라인에 기포가 섞이면 검출이 무너진다", mis,
       f"baseline={base} (깨끗하면 800이어야 — GAS배제+중앙값) / "
       f"detected={p.detected_sec} (정답 100.0)")

print()
print("=" * 78)
print("공격 2 — 창 안 기포를 시약 선단으로 오인")
print("=" * 78)
# 깨끗한 베이스라인 800, 창 안에서 기포(80) 가 3폴 이상 지속, 시약은 안 옴
def adc_bubble_in_window(e):
    if 95.0 <= e < 99.0:
        return 80.0        # 기포 통과
    return 800.0           # 나머지는 용매
t = Timer(60.0)
s = Sensor(t, adc_fn=adc_bubble_in_window)
eng, p = run(s, t, [60.0, 62.0, 64.0, 66.0, 68.0, 71.0, 80.0, 90.0,
                    95.0, 96.0, 97.0, 98.0, 105.0, 125.0],
             t_exp=100.0, window=30.0, adc_delta=200.0)
report("기포가 시약 선단으로 검출된다", p.detected_sec is not None,
       f"detected={p.detected_sec} (시약은 오지도 않았음) / detector={p.detector}")

print()
print("=" * 78)
print("공격 3 — 분산된 선단: 검출 시각이 임계값의 함수가 된다")
print("=" * 78)
# Taylor 분산으로 800 → 1000 이 20초에 걸쳐 완만히 상승 (90~110s)
# (하강 램프는 임계 440 아래로 떨어져 GAS 배제에 걸림 — 유색 한계와 얽히므로
#  상승 램프로 순수 '분산 경사' 효과만 분리)
def adc_ramp(e):
    if e <= 90.0:
        return 800.0
    if e >= 110.0:
        return 1000.0
    return 800.0 + 200.0 * (e - 90.0) / 20.0
det = {}
for delta in (50.0, 100.0, 150.0):
    t = Timer(60.0)
    s = Sensor(t, adc_fn=adc_ramp)
    eng, p = run(s, t, [60.0, 65.0, 70.0, 71.0, 80.0, 90.0, 93.0, 96.0, 99.0,
                        102.0, 105.0, 108.0, 111.0, 114.0, 117.0, 120.0, 130.0],
                 t_exp=100.0, window=35.0, adc_delta=delta)
    det[delta] = p.detected_sec
spread = [v for v in det.values() if v is not None]
report("동일 물리현상인데 임계값에 따라 검출 시각이 달라진다",
       len(spread) >= 2 and (max(spread) - min(spread)) > 3.0,
       f"adc_delta 50/100/150 → 검출 {det[50.0]}/{det[100.0]}/{det[150.0]}s "
       f"(퍼짐 {max(spread) - min(spread) if len(spread) >= 2 else 0:.1f}s). "
       f"t_head 는 '플러그 중심'인데 임계 검출은 '선단'이라 물리량이 다름 (P2)")

print()
print("=" * 78)
print("공격 4 — off 모드에 부작용이 있는가")
print("=" * 78)
t = Timer(100.0)
s = Sensor(t, adc_fn=lambda e: 100.0, edge_fn=lambda e, f: "CLEAR_LIQUID")
eng, p = run(s, t, [100.0, 101.0], t_exp=100.0, window=30.0, mode="off",
             adc_delta=50.0)
report("off 모드가 하드웨어/타이머를 건드린다",
       bool(s.monitor_calls) or bool(t.shifts) or p.detected_sec is not None,
       f"monitor={len(s.monitor_calls)}회 shift={len(t.shifts)}회 로그={len(eng.logs)}줄")

print()
print("=" * 78)
print("공격 5 — 같은 센서를 두 프로브가 동시에 잡는다 (소유권 미강제)")
print("=" * 78)
t = Timer(60.0)
fired = {"n": 0}
def edge_once(e, f):
    if e >= 100.0 and fired["n"] < 1:
        fired["n"] += 1
        return "CLEAR_LIQUID"
    return None
s = Sensor(t, edge_fn=edge_once)
e1, e2 = Engine(), Engine()
p1 = HeadArrivalProbe(e1, s, "collect", t, 100.0, 30.0, mode="anchor", poll=0.01)
p2 = HeadArrivalProbe(e2, s, "collect", t, 100.0, 30.0, mode="anchor", poll=0.01)
p1.start(); p2.start()
for v in [60.0, 71.0, 80.0, 90.0, 100.0, 101.0, 105.0, 125.0]:
    t._elapsed = v
    time.sleep(0.05)
p1.join(timeout=3.0); p2.join(timeout=3.0)
p1.stop(); p2.stop()
one_lost = (p1.detected_sec is None) != (p2.detected_sec is None)
report("두 소비자가 이벤트를 서로 훔친다 (한쪽만 검출)", one_lost,
       f"p1={p1.detected_sec} p2={p2.detected_sec} — 엣지는 1회뿐인데 "
       f"소비자가 2명이면 한쪽은 영구 미검출")

print()
print("=" * 78)
print("공격 6 — 이미 정지된 타이머에 재앵커")
print("=" * 78)
t = Timer(60.0)
s = Sensor(t, edge_fn=lambda e, f: "CLEAR_LIQUID" if e >= 100.0 else None)
eng = Engine()
p = HeadArrivalProbe(eng, s, "collect", t, 100.0, 30.0, mode="anchor", poll=0.01)
p.start()
t._elapsed = 71.0; time.sleep(0.05)
t.dead = True                      # 스텝 종료로 타이머 정지
t._elapsed = 100.0
time.sleep(0.2)
p.join(timeout=3.0); p.stop()
crashed = any("재앵커 실패" not in m and "Traceback" in m for m in eng.logs)
report("죽은 타이머 재앵커가 예외로 터진다", crashed,
       f"로그: {[m for m in eng.logs if '재앵커' in m or '오류' in m][:1]}")

print()
print("=" * 78)
print("공격 7 — 음수 재앵커 폭주 (분취기 이동시간 무시)")
print("=" * 78)
t = Timer(60.0)
s = Sensor(t, edge_fn=lambda e, f: "CLEAR_LIQUID" if e >= 71.0 else None)
eng, p = run(s, t, [60.0, 71.0, 72.0, 90.0], t_exp=130.0, window=70.0,
             mode="anchor")
big_neg = bool(t.shifts) and t.shifts[0] < -8.3
report("분취기 이동시간(8.3s)보다 큰 음수 shift 가 그대로 적용된다", big_neg,
       f"shift={t.shifts} — 음수 = 기한 지난 이벤트 즉시 발화. "
       f"밸브는 ms 라 무해하나 분취기는 이동에 0.7~8.3s 소요 → 니들이 못 따라감")

print()
print("=" * 78)
print("공격 8 — 연속류에서 상전이 검출기 = 기포 단발 발화")
print("=" * 78)
t = Timer(60.0)
s = Sensor(t, edge_fn=lambda e, f: "GAS" if 95.0 <= e < 96.0 else None)
eng, p = run(s, t, [60.0, 71.0, 80.0, 90.0, 95.0, 96.0, 105.0, 125.0],
             mode="anchor", t_exp=100.0, window=30.0)
report("기포 1개(단발 엣지)로 즉시 재앵커된다", p.detected_sec is not None,
       f"detected={p.detected_sec} detector={p.detector} shift={t.shifts} — "
       f"ADC 는 3회 연속을 요구하는데 상전이는 확인 없이 발화")

print()
print("=" * 78)
n_br = sum(1 for _, b in RESULTS if b)
print(f"적대적 검증 결과: {len(RESULTS)}건 중 뚫림 {n_br}건 / 방어 {len(RESULTS) - n_br}건")
for a, b in RESULTS:
    print(f"  {'❌ BREACHED' if b else '✅ DEFENDED'}  {a}")
print("=" * 78)
