# -*- coding: utf-8 -*-
"""N2 브래킷 마커 + MarkerCollectGate 검증 — 모의 엔진 E2E (2026-08-18).

test_engine_liquid_front 하네스(deadvol_hplc = HPLC push 경로)를 재사용해
스크립트된 위상센서로 슬러그 경계를 연출하고, 다음을 검사한다:

  T1(fallback) : 에지 없음 → 선두 미검출 로그, shift 0, 후단 절단 없음
                 (시간제 폴백 — 밸브/웰 타이밍은 계획 그대로)
  T2(late +6)  : 선두가 예상보다 6s 늦게 도착 → 재앵커 +6 적용,
                 HEAD 밸브는 이미 발화(문서화된 지연 의미론 = 선용매 희석),
                 terminal WASTE 는 +6 지연, 후단 절단이 terminal 보다 먼저 발생,
                 브래킷 MFC 펄스 2회, HeadProbe 강등 로그
  T3(early -11): 선두 조기 도착 → max_early=10 클램프 로그,
                 HEAD 밸브(COLLECT)가 T1 대비 ~10s 앞당겨 발화 (완전 센서 트리거)
  T4(t0 호환)  : inj_marker_enabled=true 하위호환 → front(t0) 즉시 발사

실행: 루트에서  py -3.14 tests\\test_marker_bracket_gate.py   (~4분, 실시간 모의)
"""
import os
import re
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "test_engine_liquid_front.py")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))


class ScriptedPhase:
    """타임라인 기반 위상센서 모의 — read_event 는 상태 변화를 1회 에지로 합성."""
    sensors = {"reactor_in": 0, "collect": 1}
    thresholds = {"reactor_in": 690, "collect": 750}

    def __init__(self, get_el):
        self.get_el = get_el
        self.timeline = [(0.0, "LIQ")]     # [(펌핑경과, "LIQ"|"GAS")], 컨트롤러가 교체
        self._last = None

    def _phase_at(self, el):
        ph = "LIQ"
        for t, p in self.timeline:
            if el >= t:
                ph = p
        return ph

    def monitor(self, key, mode):
        pass

    def read_phase(self, key):
        return "CLEAR_LIQUID" if self._phase_at(self.get_el()) == "LIQ" else "GAS"

    def read_event(self, key):
        cur = self._phase_at(self.get_el())
        if self._last is None:
            self._last = cur
            return None
        if cur != self._last:
            self._last = cur
            return "GAS" if cur == "GAS" else "CLEAR_LIQUID"
        return None

    def analog(self, key):
        return 500 if self._phase_at(self.get_el()) == "GAS" else 980


class MockMFC:
    is_connected = True

    def __init__(self):
        self.calls = []                    # (wall, sccm)

    def set_flow(self, s):
        self.calls.append((time.time(), float(s)))

    def get_flow(self):
        return self.calls[-1][1] if self.calls else 0.0


def run_scenario(scen, sp_extra, delta=None, script=True):
    """하네스 1회 구동. 반환: dict(logs, events, mfc, anchors)"""
    src = open(HARNESS, encoding="utf-8").read()
    cut = src.index("eng.run_sequence(plan, None)")
    g = {"__name__": "harness", "__file__": HARNESS}
    sys.argv = ["harness", scen]
    exec(compile(src[:cut], HARNESS, "exec"), g)
    eng, plan, T0 = g["eng"], g["plan"], g["T0"]

    _tref = {"t": None}

    def get_el():
        # 물리(무-shift) 펌핑경과 재구성: 실제 유체는 재앵커(shift)와 무관하게
        # 흐른다 — 타이머 el 은 shift 로 이동하므로 applied_shift 를 되돌려준다.
        t = getattr(eng, "_collection_timer", None) or _tref["t"]
        if getattr(eng, "_collection_timer", None) is not None:
            _tref["t"] = eng._collection_timer
        try:
            el = (t._pumping_elapsed()
                  if t is not None and t.start_time is not None else 0.0)
        except Exception:
            return 0.0
        gate = getattr(eng, "_marker_gate", None)
        return el + (float(getattr(gate, "applied_shift", 0.0) or 0.0)
                     if gate else 0.0)

    phase = ScriptedPhase(get_el)
    eng.phase_sensor = phase
    eng.mfc = MockMFC()
    sp = eng.cfg.config_data["system_params"]
    sp.update(sp_extra)

    LOGS = []
    _orig = eng._log

    def spy(msg, *a, **k):
        LOGS.append((time.time(), str(msg)))
        return _orig(msg, *a, **k)
    eng._log = spy

    parsed = {}
    if script and delta is not None:
        def controller():
            t_exp = slug = None
            while t_exp is None or slug is None:
                time.sleep(0.1)
                for _, m in list(LOGS):
                    mm = re.search(r"\[MarkerGate\].*선두 예상 ([0-9.]+)s", m)
                    if mm:
                        t_exp = float(mm.group(1))
                    mm = re.search(
                        r"bracket 스케줄 — front @ ([0-9.]+)s, rear @ ([0-9.]+)s", m)
                    if mm:
                        slug = float(mm.group(2)) - float(mm.group(1))
                if any("Step 1 Complete" in m for _, m in LOGS):
                    return
            parsed["t_exp"], parsed["slug"] = t_exp, slug
            f = t_exp + delta
            phase.timeline = [
                (0.0, "LIQ"),
                (f - 4.0, "GAS"),          # 전단마커 진입
                (f, "LIQ"),                # 마커 꼬리 = 화합물 선두
                (f + slug, "GAS"),         # 후단마커 진입 = 꼬리 통과
                (f + slug + 5.0, "LIQ"),   # push 용매 복귀
            ]
        threading.Thread(target=controller, daemon=True).start()

    eng.run_sequence(plan, None)
    return {"logs": LOGS, "events": g["EVENTS"], "mfc": eng.mfc.calls,
            "parsed": parsed, "T0": T0}


def wall_of(logs, substr):
    for w, m in logs:
        if substr in m:
            return w
    return None


def valve_times(events, pos):
    return [t for (t, kind, name, data) in events
            if kind == "valve" and name == "Outlet" and data == pos]


def rel_metrics(res):
    """FRONT 마커 발사(공통 앵커) 기준 밸브 이벤트 상대 시각."""
    logs, ev = res["logs"], res["events"]
    a = wall_of(logs, "N2 marker FRONT")            # 타이머 FIRED 로그
    if a is None:
        return None
    t0 = res["T0"]
    a_rel = a - (t0 + 0)                            # wall 앵커
    v2 = [t for t in valve_times(ev, 2)]
    v1 = [t for t in valve_times(ev, 1)]
    v2_rel = (v2[0] + t0 * 0 - a) if v2 else None   # 첫 COLLECT − 앵커
    # EVENTS t 는 T0 기준, LOGS 는 wall — 통일: EVENTS wall = T0 + t
    v2_rel = (t0 + v2[0]) - a if v2 else None
    v1_after_v2 = [t0 + t - a for t in v1 if (t0 + t) > (a + (v2_rel or 0))] \
        if v2 else []
    return {"v2": v2_rel, "v1_list": v1_after_v2}


print("=== T1: fallback (에지 없음) ===")
sp_common = {
    "inj_marker_mode": "bracket", "marker_gate_mode": "gate",
    "inj_marker_sec": 0.3, "marker_gate_window_sec": 12.0,
    "marker_gate_max_early_sec": 10.0, "head_probe_confirm_sec": 0.3,
    "purge_order": "lifo",
}
r1 = run_scenario("deadvol_hplc", dict(sp_common), delta=None, script=False)
l1 = [m for _, m in r1["logs"]]
check("T1 선두 미검출 로그", any("선두 미검출" in m for m in l1))
check("T1 재앵커 없음", not any("타이머 재앵커" in m for m in l1))
check("T1 후단 절단 없음", not any("후단 절단" in m for m in l1))
check("T1 브래킷 마커 2발", sum(1 for m in l1 if "주입 완료" in m) == 2)
m1 = rel_metrics(r1)
check("T1 COLLECT 발생(폴백 타이밍)", m1 is not None and m1["v2"] is not None)

print("=== T2: late +6s (gate) ===")
sp2 = dict(sp_common)
sp2["head_probe_mode"] = "observe"        # 강등 검증용
r2 = run_scenario("deadvol_hplc", sp2, delta=+6.0)
l2 = [m for _, m in r2["logs"]]
check("T2 선두 실측 로그", any("① 화합물 선두 실측" in m for m in l2))
_sh = next((re.search(r"재앵커 \+([0-9.]+)s", m) for m in l2 if "재앵커 +" in m), None)
check("T2 재앵커 ≈ +6s", _sh is not None and abs(float(_sh.group(1)) - 6.0) <= 1.5,
      _sh.group(0) if _sh else "없음")
check("T2 후단 절단 수행", any("후단 절단" in m for m in l2))
check("T2 슬러그 통과 실측", any("② 화합물 꼬리 실측" in m for m in l2))
check("T2 HeadProbe 강등", any("read_event 이중 소비" in m for m in l2))
m2 = rel_metrics(r2)
# HEAD 밸브(COLLECT)는 지연 도착에서 이미 발화 — T1 과 동일 시각 (±1.5s)
check("T2 COLLECT 무이동(지연 의미론)",
      m1 and m2 and abs(m2["v2"] - m1["v2"]) <= 1.5,
      f"T1 {m1['v2']:.1f}s vs T2 {m2['v2']:.1f}s" if (m1 and m2) else "")
# terminal WASTE 는 +6 지연 (마지막 WASTE 비교)
_w1 = max(m1["v1_list"]) if m1 and m1["v1_list"] else None
_w2 = max(m2["v1_list"]) if m2 and m2["v1_list"] else None
check("T2 terminal WASTE +6s 지연",
      _w1 is not None and _w2 is not None and abs((_w2 - _w1) - 6.0) <= 2.0,
      f"Δ={_w2 - _w1:.1f}s" if (_w1 and _w2) else "")
# 후단 절단은 terminal 보다 먼저 → T2 의 WASTE 가 2회 이상
check("T2 WASTE 2회(절단+terminal)", m2 and len(m2["v1_list"]) >= 2,
      f"{len(m2['v1_list']) if m2 else 0}회")
_on = [s for _, s in r2["mfc"] if s > 0]
check("T2 MFC 펄스 2회", len(_on) == 2, f"{len(_on)}회")

print("=== T3: early -11s → 클램프 -10 (gate) ===")
r3 = run_scenario("deadvol_hplc", dict(sp_common), delta=-11.0)
l3 = [m for _, m in r3["logs"]]
check("T3 클램프 로그", any("클램프 -10.0s" in m for m in l3))
m3 = rel_metrics(r3)
# 조기 도착: shift(-10) → HEAD 즉시 발화 = COLLECT 가 T1 대비 ~10s 앞당겨짐
check("T3 COLLECT 조기 발화 ≈ -10s",
      m1 and m3 and abs((m1["v2"] - m3["v2"]) - 10.0) <= 2.5,
      f"T1 {m1['v2']:.1f}s vs T3 {m3['v2']:.1f}s" if (m1 and m3) else "")

print("=== T4: t0 하위호환 (inj_marker_enabled) ===")
r4 = run_scenario("balanced", {"inj_marker_enabled": True, "inj_marker_sec": 0.3},
                  delta=None, script=False)
l4 = [m for _, m in r4["logs"]]
check("T4 front(t0) 발사", any("front(t0)" in m for m in l4))
check("T4 게이트 미무장", not any("[MarkerGate]" in m and "무장" in m for m in l4))

n_ok = sum(1 for _, ok in RESULTS if ok)
print(f"\n{n_ok}/{len(RESULTS)} passed")
print("[marker-bracket-gate] " + ("ALL PASS" if n_ok == len(RESULTS) else "FAIL"))
sys.exit(0 if n_ok == len(RESULTS) else 1)
