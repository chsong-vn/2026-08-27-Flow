# -*- coding: utf-8 -*-
"""CollectionTimer v2 (P1 레인 분리) 계약 테스트.

2026-07-28 도입분:
  - 레인 분리: 느린 collector 액션이 valve 이벤트를 밀어내지 않음 (병렬 집행)
  - 같은 레인 안에서는 FIFO 순서/직렬성 유지
  - lane_leads 선행 발화 (이동 도착=경계 정렬용 튜닝 노브)
  - waste_guard: 이동 중 Outlet→WASTE / 종료(terminal) 후 COLLECT 복원 금지
  - 3튜플 이벤트 하위호환 (단일 default 레인 = 기존 직렬 실행, HTE 경로)

실행:  py -3.14 test_timer_lanes.py
(기존 pause/resume/예산 계약은 test_collection_timing_fix.py 가 커버 — 함께 실행)
"""
import sys
import time
import types
import threading

from engine.strict_engine import CollectionTimer


def _engine():
    eng = types.SimpleNamespace()
    eng.abort_flag = False
    eng._logs = []
    eng._log = lambda m, **k: eng._logs.append(m)
    return eng


def _run_timer(eng, events, timeout=6.0, **kw):
    t = CollectionTimer(eng, events, **kw)
    t.start()
    t.wait_finish(timeout=timeout)
    return t


# ═════════════════════════════════════════════════════════════
# 레인 분리
# ═════════════════════════════════════════════════════════════
def test_lane_isolation_valve_not_blocked():
    """collector 액션이 1초 블로킹해도 valve 이벤트는 제 시각에 발화."""
    eng = _engine()
    t0 = time.time()
    rec = {}

    def slow_move():
        rec["move_start"] = time.time() - t0
        time.sleep(1.0)

    def valve():
        rec["valve"] = time.time() - t0

    _run_timer(eng, [
        (0.05, "Move", slow_move, "collector", {"kind": "move"}),
        (0.30, "Outlet", valve, "valve", {"kind": "collect"}),
    ])
    assert "valve" in rec and "move_start" in rec, f"미실행: {rec}"
    assert rec["valve"] < 0.8, \
        f"valve 가 collector 에 블로킹됨 (fired @ {rec['valve']:.2f}s — 직렬이면 ≥1.05s)"


def test_three_tuple_backcompat_serial():
    """3튜플(default 단일 레인) = 기존 직렬 실행 그대로 (HTE/구 테스트 호환)."""
    eng = _engine()
    t0 = time.time()
    rec = {}

    def slow():
        time.sleep(0.8)

    def later():
        rec["later"] = time.time() - t0

    _run_timer(eng, [(0.05, "Slow", slow), (0.30, "Later", later)])
    assert rec["later"] >= 0.80, \
        f"default 레인은 직렬이어야 함 (fired @ {rec['later']:.2f}s < 0.80s)"


def test_same_lane_fifo_order():
    """같은 레인은 FIFO — 앞 이벤트 완료 전에 뒤 이벤트가 실행되지 않음."""
    eng = _engine()
    order = []

    def m1():
        order.append("m1_start")
        time.sleep(0.4)
        order.append("m1_end")

    def m2():
        order.append("m2_start")

    _run_timer(eng, [
        (0.05, "M1", m1, "collector", {}),
        (0.10, "M2", m2, "collector", {}),
    ])
    assert order == ["m1_start", "m1_end", "m2_start"], f"FIFO 위반: {order}"


# ═════════════════════════════════════════════════════════════
# 선행 발화 (lead)
# ═════════════════════════════════════════════════════════════
def test_lead_time_fires_early():
    """lane_leads=0.5 → t=0.8 이벤트가 ~0.3s 에 발화 (도착=경계 정렬용)."""
    eng = _engine()
    t0 = time.time()
    rec = {}

    def move():
        rec["move"] = time.time() - t0

    _run_timer(eng, [(0.8, "Move", move, "collector", {"kind": "move"})],
               lane_leads={"collector": 0.5})
    assert rec["move"] < 0.65, f"선행 발화 안 됨 (fired @ {rec['move']:.2f}s, 기대 ~0.3s)"


# ═════════════════════════════════════════════════════════════
# move-while-waste 가드
# ═════════════════════════════════════════════════════════════
def _guard_env():
    """공유 밸브 상태 + 시퀀스 기록이 있는 가드 테스트 환경."""
    state = {"valve": None}
    seq = []

    def v_collect():
        state["valve"] = "collect"
        seq.append("collect")

    def v_waste():
        state["valve"] = "waste"
        seq.append("waste")

    meta_move = {"kind": "move", "guard_waste": v_waste, "guard_restore": v_collect}
    return state, seq, v_collect, v_waste, meta_move


def test_waste_guard_wraps_move():
    """COLLECT 국면의 이동: WASTE → move → COLLECT 복원 순서."""
    eng = _engine()
    state, seq, v_collect, v_waste, meta_move = _guard_env()
    _run_timer(eng, [
        (0.05, "Outlet→COLLECT", v_collect, "valve", {"kind": "collect"}),
        (0.30, "Move", lambda: seq.append("move"), "collector", meta_move),
    ], waste_guard=True)
    assert seq == ["collect", "waste", "move", "collect"], f"가드 순서 오류: {seq}"
    assert state["valve"] == "collect"


def test_waste_guard_skipped_before_collect():
    """COLLECT 이전(국면 미확립) 이동에는 가드 미적용."""
    eng = _engine()
    state, seq, v_collect, v_waste, meta_move = _guard_env()
    _run_timer(eng, [(0.05, "Move", lambda: seq.append("move"), "collector", meta_move)],
               waste_guard=True)
    assert seq == ["move"], f"가드가 잘못 발동: {seq}"


def test_waste_guard_off_by_default():
    """waste_guard 미지정(기본 False) → 가드 없음 (기존 동작)."""
    eng = _engine()
    state, seq, v_collect, v_waste, meta_move = _guard_env()
    _run_timer(eng, [
        (0.05, "Outlet→COLLECT", v_collect, "valve", {"kind": "collect"}),
        (0.30, "Move", lambda: seq.append("move"), "collector", meta_move),
    ])
    assert seq == ["collect", "move"], f"기본값에서 가드 발동: {seq}"


def test_terminal_blocks_restore():
    """이동 중 종료 WASTE 발화 → 이동 끝나도 COLLECT 로 끝나지 않음 (안전상태)."""
    eng = _engine()
    state, seq, v_collect, v_waste, meta_move = _guard_env()

    def long_move():
        seq.append("move_start")
        time.sleep(0.6)
        seq.append("move_end")

    _run_timer(eng, [
        (0.05, "Outlet→COLLECT", v_collect, "valve", {"kind": "collect"}),
        (0.25, "Move", long_move, "collector", meta_move),
        (0.45, "Outlet→WASTE", v_waste, "valve", {"kind": "waste", "terminal": True}),
    ], waste_guard=True)
    assert state["valve"] == "waste", \
        f"종료 후 밸브가 {state['valve']} — WASTE 로 끝나야 함 (seq={seq})"


# ═════════════════════════════════════════════════════════════
# stop / 드레인
# ═════════════════════════════════════════════════════════════
def test_stop_cancels_pending():
    """stop() → 미발화 이벤트 실행 없이 종료, is_alive False."""
    eng = _engine()
    fired = []
    t = CollectionTimer(eng, [
        (999.0, "FAR", lambda: fired.append("far"), "valve", {}),
        (999.0, "FAR2", lambda: fired.append("far2"), "collector", {}),
    ])
    t.start()
    time.sleep(0.2)
    t.stop(timeout=2.0)
    assert fired == [], f"취소된 이벤트가 실행됨: {fired}"
    assert not t.is_alive(), "stop 후 스레드 잔존"


def test_abort_drains_without_execute():
    """엔진 abort_flag → 디스패치 중단 + 워커 드레인."""
    eng = _engine()
    fired = []

    def first():
        fired.append("first")
        eng.abort_flag = True   # 첫 이벤트가 abort 유발

    t = CollectionTimer(eng, [
        (0.05, "First", first, "valve", {}),
        (0.30, "Second", lambda: fired.append("second"), "collector", {}),
    ])
    t.start()
    t.wait_finish(timeout=4.0)
    t.stop(timeout=1.0)
    assert fired == ["first"], f"abort 후 이벤트 실행됨: {fired}"


# ── 러너 ─────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
