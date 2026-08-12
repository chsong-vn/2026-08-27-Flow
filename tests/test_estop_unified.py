# -*- coding: utf-8 -*-
"""E-STOP 단일화 검증 (2026-08-12)

배경: Manual 탭 E-STOP(`_estop_all`)과 상단바 `estop()` 두 핸들러가 서로 다른
범위를 커버해 **어느 쪽을 눌러도 안전상태가 되지 않는** 결함이 있었다
(Manual=N2·Outlet 커버/히터 누락, 상단바=히터·분취기 커버/N2·Outlet 누락).
Manual 버튼을 폐지하고 `RunControlMixin.estop()` 하나로 합쳤다.

검증 항목:
  A. 흡수된 범위 전부 실행 (히터·펌프·푸시·수동펌프·N2·샘플러·분취기·Outlet·DeepWash)
  B. 플래그 계열은 '즉시'(GUI 스레드) — 시리얼이 느려도 지연되지 않음
  C. 장비 하나가 예외를 던져도 나머지가 전부 정지 (예외 격리)
  D. Manual 탭에 전역 E-STOP 위젯이 남아있지 않음
  E. Pause 래치 해제 — 런 시작 시 btn_p 가 초기화됨
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_control import RunControlMixin

FAILS = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'} {label} {detail}")
    if not ok:
        FAILS.append(label)


# ── 스텁 장비 ────────────────────────────────────────────────────
class Rec:
    """호출 기록기 — 느린 시리얼/예외를 흉내낼 수 있다."""

    def __init__(self, delay=0.0, raises=False):
        self.calls = []
        self.delay = delay
        self.raises = raises

    def _hit(self, name, *a):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((name, a))
        if self.raises:
            raise RuntimeError(f"{name} 통신 실패(주입)")


class Heater(Rec):
    def stop(self):
        self._hit("stop")


class Pump(Rec):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._abort_refill = False

    def stop(self):
        self._hit("stop")


class MFC(Rec):
    def set_flow(self, v):
        self._hit("set_flow", v)


class Sampler(Rec):
    def emergency_stop(self):
        self._hit("emergency_stop")


class Collector(Rec):
    def stop_motion(self):
        self._hit("stop_motion")


class Valve(Rec):
    def set_position(self, p):
        self._hit("set_position", p)


class DeepWash(Rec):
    def __init__(self, running=True):
        super().__init__()
        self.running = running

    def stop(self):
        self._hit("stop")


class Engine:
    def __init__(self):
        self.abort_flag = False
        self.pause_event = threading.Event()
        self.pause_event.clear()          # 일시정지 중 E-STOP 시나리오


class Btn:
    def __init__(self):
        self.checked = True              # 래치된 상태로 출발
        self.text = "Resume"

    def setChecked(self, v):
        self.checked = v

    def setText(self, t):
        self.text = t

    def isChecked(self):
        return self.checked


class Lbl:
    def __init__(self):
        self.t = "S1-Inject"

    def setText(self, t):
        self.t = t


class Sig:
    def __init__(self):
        self.logs = []

    class _L:
        def __init__(self, o):
            self.o = o

        def emit(self, m):
            self.o.logs.append(m)

    @property
    def sig_log(self):
        return Sig._L(self)


class ManTab:
    def __init__(self, dw):
        self._dw_engine = dw


class App(RunControlMixin):
    """RunControlMixin 만 얹은 최소 앱 — QMainWindow 없이 estop 계약 검증."""

    def __init__(self, slow=0.0, break_heater=False):
        self.engine = Engine()
        self.heater = Heater(delay=slow, raises=break_heater)
        self.pumps = {"Group_A": Pump(delay=slow), "Group_B": Pump(delay=slow)}
        self.push_pump = Pump(delay=slow)
        self.manual_pumps = {"M1": Pump(delay=slow)}
        self.mfc = MFC(delay=slow)
        self.samplers = {"S1": Sampler(delay=slow)}
        self.collector = Collector(delay=slow)
        self.valves = {"Outlet": Valve(delay=slow)}
        self.dw = DeepWash(running=True)
        self.man_tab = ManTab(self.dw)
        self.signals = Sig()
        self.btn_p = Btn()
        self.lbl_phase = Lbl()
        self.status_msgs = []
        self.modal_shown = False

    def _update_status_bar(self, m):
        self.status_msgs.append(m)


def run_estop(app, timeout=5.0):
    """estop() 실행 후 장비 스레드 종료까지 대기 (모달은 패치로 우회)."""
    import core.app_control as ac
    orig = ac.QMessageBox

    class FakeMB:
        @staticmethod
        def critical(*a, **k):
            app.modal_shown = True

    ac.QMessageBox = FakeMB
    try:
        before = set(threading.enumerate())
        app.estop()
        deadline = time.time() + timeout
        while time.time() < deadline:
            extra = [t for t in threading.enumerate()
                     if t not in before and t.is_alive()]
            if not extra:
                break
            time.sleep(0.02)
    finally:
        ac.QMessageBox = orig


# ── A. 흡수 범위 전수 ────────────────────────────────────────────
print("[A] 흡수된 범위 전부 실행")
app = App()
run_estop(app)

check("A1 엔진 abort_flag", app.engine.abort_flag is True)
check("A2 pause_event 해제(일시정지 중 E-STOP → cleanup 도달)",
      app.engine.pause_event.is_set() is True)
check("A3 히터 정지", [c[0] for c in app.heater.calls] == ["stop"])
check("A4 그룹 펌프 전부 정지",
      all(("stop", ()) in p.calls for p in app.pumps.values()))
check("A5 리필 워커 탈출 플래그",
      all(p._abort_refill is True for p in app.pumps.values()))
check("A6 푸시 펌프 정지", ("stop", ()) in app.push_pump.calls)
check("A7 수동 펌프 정지",
      all(("stop", ()) in p.calls for p in app.manual_pumps.values()))
check("A8 N2(MFC) 0 sccm ★", ("set_flow", (0.0,)) in app.mfc.calls,
      f"{app.mfc.calls}")
check("A9 샘플러 비상정지",
      all(("emergency_stop", ()) in s.calls for s in app.samplers.values()))
check("A10 분취기 이동정지", ("stop_motion", ()) in app.collector.calls)
check("A11 Outlet→WASTE(1) ★", ("set_position", (1,)) in app.valves["Outlet"].calls,
      f"{app.valves['Outlet'].calls}")
check("A12 Deep Wash 중단", ("stop", ()) in app.dw.calls)
check("A13 UI 상태 반영", app.status_msgs == ["Emergency Stop"]
      and app.lbl_phase.t == "" and app.modal_shown is True)
check("A14 로그에 범위 명시", any("N2" in m and "WASTE" in m
                             for m in app.signals.logs), app.signals.logs)

# ── B. 플래그는 즉시 (느린 시리얼과 분리) ─────────────────────────
print("\n[B] 시리얼이 느려도 플래그/UI 는 즉시 (데몬 스레드 분리)")
slow = App(slow=0.25)   # 장비 9종 × 0.25s ≈ 2.5s 직렬
import core.app_control as _ac
_orig_mb = _ac.QMessageBox


class _FakeMB:
    @staticmethod
    def critical(*a, **k):
        slow.modal_shown = True


_ac.QMessageBox = _FakeMB
try:
    t0 = time.time()
    slow.estop()
    elapsed = time.time() - t0
finally:
    _ac.QMessageBox = _orig_mb

check("B1 estop() 반환이 0.2s 미만 (GUI 무블록)", elapsed < 0.2, f"{elapsed:.3f}s")
check("B2 반환 시점에 abort_flag 이미 True", slow.engine.abort_flag is True)
check("B3 반환 시점에 _abort_refill 이미 True",
      all(p._abort_refill is True for p in slow.pumps.values()))
check("B4 반환 시점에 모달/상태바 이미 반영",
      slow.modal_shown is True and slow.status_msgs == ["Emergency Stop"])
# 백그라운드 완료 대기 후 장비도 결국 전부 정지
deadline = time.time() + 10
while time.time() < deadline and not (
        ("set_flow", (0.0,)) in slow.mfc.calls
        and ("set_position", (1,)) in slow.valves["Outlet"].calls):
    time.sleep(0.05)
check("B5 백그라운드에서 N2·Outlet 도 결국 완료",
      ("set_flow", (0.0,)) in slow.mfc.calls
      and ("set_position", (1,)) in slow.valves["Outlet"].calls)

# ── C. 예외 격리 ─────────────────────────────────────────────────
print("\n[C] 히터가 통신 실패해도 나머지 장비는 전부 정지")
brk = App(break_heater=True)
run_estop(brk)
check("C1 히터 예외가 전파되지 않음(estop 완주)", True)
check("C2 N2 차단은 그대로 수행 ★", ("set_flow", (0.0,)) in brk.mfc.calls)
check("C3 Outlet→WASTE 그대로 수행 ★",
      ("set_position", (1,)) in brk.valves["Outlet"].calls)
check("C4 펌프 정지 그대로 수행",
      all(("stop", ()) in p.calls for p in brk.pumps.values()))

# ── D. Manual 탭 전역 E-STOP 제거 확인 ───────────────────────────
print("\n[D] Manual 탭 전역 E-STOP 흔적 없음")
_tm = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ui", "tab_manual.py")
src = open(_tm, encoding="utf-8").read()
check("D1 btn_estop 위젯 없음", "self.btn_estop" not in src)
check("D2 _estop_all 핸들러 없음", "_estop_all" not in src)
check("D3 폐지 근거 주석 존재", "app_control" in src and "E-STOP" in src)

# ── E. Pause 래치 해제 ───────────────────────────────────────────
print("\n[E] Pause 래치 — 런 시작 시 버튼 초기화")
_ts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ui", "tab_sequence.py")
tsrc = open(_ts, encoding="utf-8").read()
check("E1 run_seq 가 btn_p 를 초기화", 'btn_p.setChecked(False)' in tsrc)
# estop 도 래치를 해제해야 한다 (기존 계약 유지)
lat = App()
lat.btn_p.checked = True
lat.btn_p.text = "Resume"
run_estop(lat)
check("E2 estop 도 래치 해제 유지",
      lat.btn_p.isChecked() is False and lat.btn_p.text == "Pause")

print("\nRESULT: " + ("ALL PASS" if not FAILS
                      else f"{len(FAILS)} FAIL → {FAILS}"))
sys.exit(1 if FAILS else 0)
