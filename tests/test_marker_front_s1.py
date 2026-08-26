# -*- coding: utf-8 -*-
"""전단 마커 센서1 판독 검증 (2026-08-24 — marker_gate_front_sensor_key).

실런 2회에서 전단 마커가 반응기 코일 통과 중 파편화되어 센서2 검출이 전부
기각(244.7s 재현)됨에 따라, 전단 감시를 센서1(reactor_in)로 이관한 변경 검증:

  S1-1: 무장 로그가 전단 센서 'reactor_in' 표기 + t_exp 가 센서1 기준(작은 값)
  S1-2: 센서1 마커(기체 3s)로 선두 검출 → 재앵커 +5s 적용
        (센서2 collect 는 전 구간 액체 = 파편화로 아무 것도 못 보는 상황 재현)
  S1-3: 후단 절단이 센서1 실측 + '모델 수송'(S1→S2) 기반으로 예약됨
        (front_el−offset ≈ 수 초를 수송으로 오용하는 결함 회귀 방지)
  S1-4: COLLECT(2) 발생 + 후단 절단/terminal 의 우아한 종료 (크래시 없음)
  S1-5: 전단 확정 전 rear 소비기가 센서1 큐를 드레인하지 않음
        (이중 소비 가드 — 검출 자체의 성공이 곧 증거)

실행: 루트에서  py -3.14 tests\\test_marker_front_s1.py   (~1분, 실시간 모의)
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
    sensors = {"reactor_in": 0, "collect": 1}
    thresholds = {"reactor_in": 690, "collect": 750}

    def __init__(self, get_el):
        self.get_el = get_el
        self.timelines = {"collect": [(0.0, "LIQ")], "reactor_in": [(0.0, "LIQ")]}
        self._last = {}

    def _phase_at(self, key, el):
        ph = "LIQ"
        for t, p in self.timelines.get(key, [(0.0, "LIQ")]):
            if el >= t:
                ph = p
        return ph

    def monitor(self, key, mode):
        pass

    def read_phase(self, key):
        return ("CLEAR_LIQUID"
                if self._phase_at(key, self.get_el()) == "LIQ" else "GAS")

    def read_event(self, key):
        cur = self._phase_at(key, self.get_el())
        if key not in self._last:
            self._last[key] = cur
            return None
        if cur != self._last[key]:
            self._last[key] = cur
            return "GAS" if cur == "GAS" else "CLEAR_LIQUID"
        return None

    def analog(self, key):
        return 500 if self._phase_at(key, self.get_el()) == "GAS" else 980


class MockMFC:
    is_connected = True

    def __init__(self):
        self.calls = []

    def set_flow(self, s):
        self.calls.append((time.time(), float(s)))

    def get_flow(self):
        return self.calls[-1][1] if self.calls else 0.0


DELTA = +5.0

src = open(HARNESS, encoding="utf-8").read()
cut = src.index("eng.run_sequence(plan, None)")
g = {"__name__": "harness", "__file__": HARNESS}
sys.argv = ["harness", "deadvol_hplc"]
exec(compile(src[:cut], HARNESS, "exec"), g)
eng, plan, T0 = g["eng"], g["plan"], g["T0"]

_tref = {"t": None}


def get_el():
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
sp.update({
    "inj_marker_mode": "parked", "marker_gate_mode": "gate",
    "inj_marker_sec": 0.3, "marker_gate_window_sec": 12.0,
    "marker_gate_max_early_sec": 10.0, "head_probe_confirm_sec": 0.3,
    "purge_order": "lifo",
    "marker_gate_front_sensor_key": "reactor_in",     # ★ 검증 대상
    "tjunction_to_sensor1_vol_ml": 0.0583,
})

LOGS = []
_orig = eng._log


def spy(msg, *a, **k):
    LOGS.append((time.time(), str(msg)))
    return _orig(msg, *a, **k)


eng._log = spy


def controller():
    t_exp = off = None
    slug = 13.5   # deadvol_hplc lifo inject_sec (기존 스위트와 동일 상수)
    while t_exp is None:
        time.sleep(0.1)
        for _, m in list(LOGS):
            mm = re.search(r"\[MarkerGate\].*선두 예상 ([0-9.]+)s", m)
            if mm:
                t_exp = float(mm.group(1))
                mo = re.search(r"오프셋 ([0-9.]+)s", m)
                off = float(mo.group(1)) if mo else 0.0
        if any("Step 1 Complete" in m for _, m in LOGS):
            return
    f_tail = t_exp + DELTA - off          # 센서1 검출 에지(전단마커 꼬리)
    # 센서1: 전단마커(기체 3s) + 후단마커(발사 직후 통과, 기체 3s)
    phase.timelines["reactor_in"] = [
        (0.0, "LIQ"),
        (max(0.1, f_tail - 3.0), "GAS"), (f_tail, "LIQ"),
        (f_tail + slug + 1.0, "GAS"), (f_tail + slug + 4.0, "LIQ"),
    ]
    # 센서2: 전 구간 액체 — 파편화로 마커를 전혀 못 보는 실기 상황 재현
    phase.timelines["collect"] = [(0.0, "LIQ")]


threading.Thread(target=controller, daemon=True).start()
eng.run_sequence(plan, None)

logs = [m for _, m in LOGS]

print("=== 판정 ===")
check("S1-1 무장 로그: 전단마커 감시=전단센서",
      any("전단마커 감시=전단센서" in m for m in logs))
check("S1-2 선두 검출 (센서1, 센서2 무신호 상태)",
      any("① 선두 도달 예측" in m for m in logs))
_sh = None
for m in logs:
    mm = re.search(r"타이머 재앵커 \+([0-9.]+)s", m)
    if mm:
        _sh = float(mm.group(1))
check("S1-2 재앵커 ≈ +5s", _sh is not None and abs(_sh - DELTA) <= 1.5,
      f"shift={_sh}")
_cut = next((m for m in logs if "후단 절단 예약 @" in m), None)
check("S1-3 후단 절단 예약(센서1 기반)", _cut is not None,
      (_cut or "")[:80])
_tr = None
if _cut:
    mm = re.search(r"전단실측수송 ([0-9.]+)", _cut)
    if mm:
        _tr = float(mm.group(1))
check("S1-3 수송 = 모델값(>오프셋 오용치)", _tr is not None and _tr > 5.0,
      f"transit={_tr}s — front_el−offset(수 초) 오용이면 FAIL")
check("S1-4 COLLECT 발생",
      any(kind == "valve" and name == "Outlet" and data == 2
          for (t, kind, name, data) in g["EVENTS"]))
check("S1-4 후단 절단 or terminal 우아한 종료",
      any(("후단 절단" in m) or ("terminal" in m and "MarkerGate" in m)
          for m in logs))
check("S1-5 선두 미검출 아님 (rear 드레인이 전단 에지를 안 삼킴)",
      not any("선두 미검출" in m for m in logs))

n_ok = sum(1 for _, ok in RESULTS if ok)
print(f"\n{n_ok}/{len(RESULTS)} passed")
print("[marker-front-s1] " + ("ALL PASS" if n_ok == len(RESULTS) else "FAIL"))
sys.exit(0 if n_ok == len(RESULTS) else 1)
