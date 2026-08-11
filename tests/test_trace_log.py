# -*- coding: utf-8 -*-
"""TraceLogger (engine/trace_log.py) 검증 — Perfetto 트레이스 기록.

검증 항목:
 1. 이벤트 종류별 기록 + close 후 엄격한 JSON 파싱
 2. 크래시 내성 — close 없이 죽어도 파일이 열림 (Perfetto 관용 파싱)
 3. 멀티스레드 동시 기록 무손실
 4. 열린 스팬 자동 종료 (close 시 E 보충)
 5. FLOWCHEM_TRACE=0 게이트 — 파일 미생성
 6. CollectionTimer 통합 — PLAN instant + 발화 X 이벤트
 7. FlowEngine._log 통합 — LOG instant + Temp/Pressure 카운터

실행: 프로젝트 루트에서  py -3.14 tests\\test_trace_log.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.trace_log import TraceLogger, NullTracer, NULL_TRACER

fails = []


def check(name, cond, detail=""):
    print(f"{'  PASS' if cond else 'FAIL'}: {name} {detail}")
    if not cond:
        fails.append(name)


def parse_strict(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_lenient(path):
    """Perfetto 처럼 닫히지 않은 배열(트레일링 콤마) 허용."""
    with open(path, encoding="utf-8") as f:
        s = f.read().strip()
    if s.endswith(","):
        s = s[:-1]
    if not s.endswith("]"):
        s += "]"
    return json.loads(s)


def _tn(evs, e):
    """이벤트 e 가 속한 트랙 이름 (thread_name 메타데이터에서 tid 역참조)."""
    tid_map = {ev["tid"]: ev["args"]["name"]
               for ev in evs if ev["ph"] == "M" and ev["name"] == "thread_name"}
    return tid_map.get(e.get("tid"), "?")


tmp = tempfile.mkdtemp(prefix="trace_test_")
try:
    # ══ 1. 이벤트 종류 + 엄격 JSON ══
    p1 = os.path.join(tmp, "t1.json")
    tr = TraceLogger(p1)
    tr.instant("LOG", "메시지 한글 테스트")
    tr.begin("PHASE", "가열 대기", args={"pct": 0})
    tr.end("PHASE")
    tr.complete("VALVE A", "→2", time.time() - 0.5, 0.5, args={"pos": 2})
    tr.counter("Temp(C)", {"temp": 25.1})
    tr.close()
    evs = parse_strict(p1)
    phs = [e["ph"] for e in evs]
    check("종류별 기록 (i/B/E/X/C)", all(k in phs for k in ("i", "B", "E", "X", "C")), str(phs))
    check("close 후 엄격 JSON 파싱", isinstance(evs, list) and len(evs) >= 7)
    names = {e["args"]["name"] for e in evs if e["ph"] == "M" and e["name"] == "thread_name"}
    check("트랙 메타데이터 (thread_name)", {"LOG", "PHASE", "VALVE A"} <= names, str(names))
    check("close 이후 호출 no-op", tr.instant("LOG", "after") is None and not os.path.getsize(p1) == 0)

    # ══ 2. 크래시 내성 (close 없음) ══
    # flush 는 0.5초 스로틀 계약 — 크래시 시 최대 0.5초 분량만 유실.
    # 0.6초 간격의 두 번째 기록이 인터벌 경과로 앞 이벤트까지 flush 함을 검증.
    p2 = os.path.join(tmp, "t2.json")
    tr2 = TraceLogger(p2)
    tr2.instant("LOG", "a")
    time.sleep(0.6)
    tr2.instant("LOG", "b")
    # close 하지 않음 — 크래시 시뮬레이션
    evs2 = parse_lenient(p2)
    check("미마감 파일 관용 파싱 (0.5s 스로틀 계약)",
          len([e for e in evs2 if e["ph"] == "i"]) == 2)
    tr2.close()

    # ══ 3. 멀티스레드 무손실 ══
    p3 = os.path.join(tmp, "t3.json")
    tr3 = TraceLogger(p3)
    N, T = 200, 4

    def hammer(i):
        for j in range(N):
            tr3.instant(f"TH{i}", f"ev{j}")

    ths = [threading.Thread(target=hammer, args=(i,)) for i in range(T)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    tr3.close()
    evs3 = parse_strict(p3)
    n_inst = len([e for e in evs3 if e["ph"] == "i"])
    check(f"멀티스레드 {T}x{N} 무손실", n_inst == N * T, f"{n_inst}/{N*T}")

    # ══ 4. 열린 스팬 자동 종료 ══
    p4 = os.path.join(tmp, "t4.json")
    tr4 = TraceLogger(p4)
    tr4.begin("PHASE", "중첩1")
    tr4.begin("PHASE", "중첩2")
    tr4.begin("DOSING", "슬러그1")
    tr4.close()
    evs4 = parse_strict(p4)
    n_b = len([e for e in evs4 if e["ph"] == "B"])
    n_e = len([e for e in evs4 if e["ph"] == "E"])
    check("자동 종료 B==E", n_b == 3 and n_e == 3, f"B={n_b} E={n_e}")

    # ══ 5. env 게이트 ══
    os.environ["FLOWCHEM_TRACE"] = "0"
    p5 = os.path.join(tmp, "t5.json")
    tr5 = TraceLogger(p5)
    tr5.instant("LOG", "x"); tr5.close()
    check("FLOWCHEM_TRACE=0 → 파일 미생성", not os.path.exists(p5))
    del os.environ["FLOWCHEM_TRACE"]

    # ══ 6. CollectionTimer 통합 ══
    from engine.strict_engine import CollectionTimer

    class FakeEngine:
        abort_flag = False
        def __init__(self, trace):
            self.trace = trace
        def _log(self, msg):
            pass

    p6 = os.path.join(tmp, "t6.json")
    tr6 = TraceLogger(p6)
    eng = FakeEngine(tr6)
    fired = []
    timer = CollectionTimer(eng, [
        (0.0, "Outlet→COLLECT", lambda: fired.append("a")),
        (0.3, "Outlet→WASTE", lambda: fired.append("b"), "laneB"),
    ])
    timer.start()
    timer.wait_finish(timeout=10)
    tr6.close()
    evs6 = parse_strict(p6)
    plan = [e for e in evs6 if e["ph"] == "i" and "PLAN" in _tn(evs6, e)]
    fired_x = [e for e in evs6 if e["ph"] == "X"]
    check("타이머 실행 완료", fired == ["a", "b"], str(fired))
    check("PLAN instant 2건", len(plan) == 2, f"{len(plan)}건")
    check("발화 X 이벤트 2건 (레인 트랙 분리)", len(fired_x) == 2
          and {_tn(evs6, e) for e in fired_x} == {"TIMER default", "TIMER laneB"},
          str({_tn(evs6, e) for e in fired_x}))

    # ══ 7. FlowEngine._log 통합 ══
    from engine.flow_engine import FlowEngine

    class _Heater:
        def get_temperature(self): return 25.5
    class _Pump:
        def get_pressure(self): return 1.5
    class _Safety:
        def check_system(self): pass

    fe = FlowEngine(config=None, pumps={"Group A": _Pump()}, valves={},
                    heater=_Heater(), safety_mgr=_Safety())
    fe.log_dir = os.path.join(tmp, "logs")
    fe._init_log("TraceTest")
    fe._log("통합 테스트 메시지")
    fe.trace.close()
    fe.csv_file.close()
    tfiles = [f for f in os.listdir(fe.log_dir) if f.startswith("TRACE_")]
    check("TRACE 파일 생성 (CSV 와 동일 스탬프)", len(tfiles) == 1, str(tfiles))
    evs7 = parse_strict(os.path.join(fe.log_dir, tfiles[0]))
    logs = [e for e in evs7 if e["ph"] == "i" and "통합 테스트" in e["name"]]
    cnts = {_tn(evs7, e) for e in evs7 if e["ph"] == "C"}
    check("LOG instant 기록", len(logs) == 1)
    check("Temp/Pressure 카운터", {"Temp(C)", "Pressure(bar)"} <= cnts, str(cnts))

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("RESULT: " + ("ALL PASS" if not fails else f"SOME FAIL — {fails}"))
sys.exit(1 if fails else 0)
