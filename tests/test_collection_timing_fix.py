"""분취 타이밍 버그 fix 검증 테스트 (하드웨어 불필요)

검증 항목:
1. dosing 중 pause → CollectionTimer도 함께 멈추는가
2. pause 후 resume → 펌프가 실제로 재시작되는가 (start 재전송)
3. pause 시간만큼 end_time이 연장되어 주입 볼륨이 잘리지 않는가
4. on_pumps_started 콜백이 펌프 start 직후 1회만 발동하는가
5. remaining_sec()이 pumping-elapsed 기준으로 계산되는가
"""
import sys, time, threading, types

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔/파이프 출력 방어
except Exception:
    pass
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.strict_engine import StrictSequenceEngine, CollectionTimer
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump


class FakeDriver:
    """표준 드라이버 계약: is_stopped = 모터 상태 미러 (E2E SimDriver 와 동일).
    구버전 '무조건 True' 스텁은 #12 기동검증 게이트와 충돌."""
    def __init__(self, pump):
        self.pump = pump
    def is_stopped(self):
        return not self.pump.running


class FakePump(ChemyxSmartPump):
    """ChemyxSmartPump의 통신 없이 상태만 추적"""
    def __init__(self, name):
        # __init__ 우회 (시리얼 연결 없음)
        self.name = name
        self.capacity = 10.0
        self.current_vol = 5.0
        self.running = False
        self.target_flow = 0.0
        self.status = "Idle"
        self.is_refilling = False
        self._abort_refill = False
        self.driver = FakeDriver(self)
        self.start_calls = 0
        self.stop_calls = 0

    def set_flow(self, rate):
        self.target_flow = rate

    def start(self):
        self.start_calls += 1
        self.running = True

    def stop(self):
        self.stop_calls += 1
        self.running = False


def make_engine():
    eng = StrictSequenceEngine.__new__(StrictSequenceEngine)
    eng.pumps = {"A": FakePump("A")}
    eng.pause_event = threading.Event()
    eng.pause_event.set()
    eng.abort_flag = False
    eng._collection_timer = None
    eng.signals = None
    eng.injection_start_ts = None
    eng._log = lambda msg: print(f"  LOG: {msg}")
    return eng


def test_pause_resume_during_dosing():
    print("\n[TEST 1-4] pause 중 timer 동기화 + 펌프 재시작 + end_time 연장 + 콜백")
    eng = make_engine()
    pump = eng.pumps["A"]

    fired = []
    events = [(2.0, "Outlet→COLLECT", lambda: fired.append(("collect", time.time()))),
              (4.0, "Outlet→WASTE", lambda: fired.append(("waste", time.time())))]
    timer = CollectionTimer(eng, events)
    eng._collection_timer = timer
    timer.start()
    timer.pause()  # 엔진과 동일: 생성 직후 pause

    cb_count = []

    def on_started():
        cb_count.append(time.time())
        timer.resume()

    # 0.7초 후 pause, 1.5초 pause 유지 후 resume
    # @codesyncer: 기존 1.0s 는 도징 루프 폴 주기(dt≤1.0s)의 '경계'와 정확히 겹쳐
    #   감지가 다음 폴(2.0s)로 밀리면 wall=4.5 로 간헐 FAIL(레이스). 폴 창 중앙(0.7s)에
    #   걸어 감지=1.0s 폴로 결정화 → stopped≈1.2s, wall≈5.2s 안정.
    def pauser():
        time.sleep(0.7)
        eng.pause_event.clear()
        time.sleep(1.5)
        eng.pause_event.set()

    threading.Thread(target=pauser, daemon=True).start()

    t0 = time.time()
    eng._execute_smart_dosing(
        {"A": 60.0},  # 60 mL/min → 1 mL/s
        duration_sec=4.0,
        step_name="TEST-Injection",
        allow_refill=False,
        on_pumps_started=on_started,
    )
    wall = time.time() - t0

    timer.wait_finish(timeout=8.0)
    assert not timer.is_alive(), "timer가 완료되지 않음"

    # 검증 3: 실제 정지시간만큼 dosing wall time 연장 (엔진은 '감지 후 멈춘 시간'만
    # 연장 = 체적 보존. 폴 지연 감안 stopped∈[1.0,1.5] → wall 4.0+that. 미수정 엔진=4.0)
    assert 4.7 < wall < 6.6, f"end_time 연장 실패: wall={wall:.2f}s (기대 ~5.2s)"
    # 검증 2: 펌프 start가 2회 (최초 + resume 후 재시작)
    assert pump.start_calls >= 2, f"pause 후 펌프 재시작 안 됨 (start_calls={pump.start_calls})"
    # 검증 4: 콜백 1회만
    assert len(cb_count) == 1, f"on_pumps_started {len(cb_count)}회 발동"
    # 검증 1: timer 이벤트가 pumping-elapsed 기준으로 발동
    #   collect는 pumping 2.0초 시점 = wall 기준 콜백 후 약 2.0 + (pause가 1.0s 시점에 끼면 +1.5)
    collect_wall = fired[0][1] - cb_count[0]
    waste_wall = fired[1][1] - cb_count[0]
    assert 3.2 < collect_wall < 4.3, f"collect 이벤트 타이밍 오류: {collect_wall:.2f}s (기대 ~3.5s = 2.0 + pause 1.5)"
    assert 5.2 < waste_wall < 6.5, f"waste 이벤트 타이밍 오류: {waste_wall:.2f}s (기대 ~5.5s = 4.0 + pause 1.5)"
    print(f"  OK: wall={wall:.2f}s, start_calls={pump.start_calls}, "
          f"collect@+{collect_wall:.2f}s, waste@+{waste_wall:.2f}s")


def test_remaining_sec():
    print("\n[TEST 5] remaining_sec — pumping-elapsed 기준")
    eng = make_engine()
    timer = CollectionTimer(eng, [(10.0, "X", lambda: None)])
    timer.start()
    timer.pause()
    time.sleep(0.5)  # paused 동안 wall은 흐르지만 pumping은 정지
    rem = timer.remaining_sec()
    assert 9.8 < rem <= 10.0, f"paused 중 remaining이 줄어듦: {rem:.2f}"
    timer.resume()
    time.sleep(0.5)
    rem2 = timer.remaining_sec()
    assert 9.2 < rem2 < 9.7, f"resume 후 remaining 비정상: {rem2:.2f}"
    timer.stop()
    print(f"  OK: paused rem={rem:.2f}s, after 0.5s pumping rem={rem2:.2f}s")


def test_orphan_guard():
    print("\n[TEST 6] orphan timer 강제 정지 (is_alive + stop)")
    eng = make_engine()
    timer = CollectionTimer(eng, [(999.0, "FAR", lambda: None)])
    timer.start()
    timer.wait_finish(timeout=0.3)
    assert timer.is_alive(), "테스트 전제 실패"
    timer.stop(timeout=2.0)
    assert not timer.is_alive(), "stop 후에도 thread 생존"
    print("  OK: timeout 후 강제 stop으로 orphan thread 제거")


if __name__ == "__main__":
    test_pause_resume_during_dosing()
    test_remaining_sec()
    test_orphan_guard()
    print("\n✅ ALL TESTS PASSED")
