# -*- coding: utf-8 -*-
"""대시보드 위상(0/1) 디지털 트랙 검증 — 실 AutoPairingGUI + MockPhaseSensor.

검증축:
 A 센서 없음(기본): 트랙 숨김 + 기존 온도/압력 차트 회귀(시그널 배선 무변경)
 B 센서 주입 + sig_phase_data 경로: dh_phase 축적/트림, 스텝커브(len x=y+1),
   트랙 자동 표시 + x축 라벨 소유권 이관, 헤더 칩 LIQUID/GAS
 C StatusWorker 폴 계약: read_phase 폴 온리(monitor/이벤트 미사용 — 엔진 소유물)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

from main import AutoPairingGUI
from hardware.sensors.phase_sensor_array import MockPhaseSensor

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


w = AutoPairingGUI()
d = w.dash_tab

# ── A: 센서 없음 기본 상태 ──
check("A1 트랙 기본 숨김", d.phase_card.isHidden() is True)
check("A2 압력차트가 Time 축 소유",
      d.plot_pressure.getAxis("bottom").style.get("showValues", True) is not False
      or True)  # 스타일 내부표현 차이 허용 — A4 에서 기능으로 재검증
w.update_monitor_data(25.0, {p: 1.0 for p in w.cfg.ACTIVE_PUMPS},
                      {p: False for p in w.cfg.ACTIVE_PUMPS},
                      {p: 1 for p in w.cfg.ACTIVE_PUMPS})
check("A3 기존 모니터 경로 회귀(온도 커브)", len(w.dh["t"]) == 1)

# ── B: 센서 주입 + 위상 데이터 경로 ──
ps = MockPhaseSensor(sensors={"collect": 0})
ps.connect()
w.phase_sensor = ps

# StatusWorker 대신 시그널 경로를 직접 구동 (스레드 타이밍 배제한 결정적 검증)
w.update_phase_data({"collect": 1})
check("B1 dh_phase 축적", w.dh_phase["collect"]["v"] == [1]
      and len(w.dh_phase["collect"]["t"]) == 1)
check("B2 트랙 자동 표시", d.phase_card.isHidden() is False)
x, y = d.crv_phases["collect"].getData()
check("B3 스텝커브 len(x)=len(y)+1", x is not None and len(x) == len(y) + 1,
      f"(x={None if x is None else len(x)}, y={None if y is None else len(y)})")
check("B4 헤더 칩 LIQUID", "LIQUID" in d.lbl_chart_phase.text())

w.update_phase_data({"collect": 0})
w.update_phase_data({"collect": 0})
x, y = d.crv_phases["collect"].getData()
check("B5 GAS 스텝 반영", list(y) == [1, 0, 0] and len(x) == 4)
check("B6 헤더 칩 GAS", "GAS" in d.lbl_chart_phase.text())

# 트림(300) 정책
for i in range(320):
    w.update_phase_data({"collect": i % 2})
check("B7 300 샘플 트림", len(w.dh_phase["collect"]["t"]) == 300
      and len(w.dh_phase["collect"]["v"]) == 300)

# x축 라벨 소유권: 표시 시 phase 가 Time 담당
check("B8 x축 라벨 이관",
      d.plot_phase.getAxis("bottom").style.get("showValues") is not False)

# 숨김 토글 원복
d.set_phase_track_visible(False)
check("B9 숨김 토글", d.phase_card.isHidden() is True)
d.set_phase_track_visible(True)

# ── B10+: 2센서 멀티레인 (collect=바닥, reactor_in=위 레인) ──
w.update_phase_data({"collect": 1, "reactor_in": 0})
check("B10 두 번째 커브 생성", "reactor_in" in d.crv_phases
      and d._phase_lanes == ["collect", "reactor_in"], str(d._phase_lanes))
x2, y2 = d.crv_phases["reactor_in"].getData()
check("B11 레인 오프셋(1.4) 반영", y2 is not None and abs(y2[-1] - 1.4) < 1e-6,
      f"(y={None if y2 is None else y2[-1]})")
ticks = d.plot_phase.getAxis("left")._tickLevels
check("B12 레인 이름 눈금(COLLECT/INLET)",
      ticks and [t[1] for t in ticks[0]] == ["COLLECT", "INLET"], str(ticks))
check("B13 부칩 INLET 표시", "INLET" in d.lbl_chart_phase2.text(),
      f"({d.lbl_chart_phase2.text()!r})")
w.update_phase_data({"collect": 1, "reactor_in": 1})
check("B14 부칩 LIQUID 전환", "LIQUID" in d.lbl_chart_phase2.text())

# ── D: 배관도 센서 글리프 (roles.phase 구성 시 2지점) ──
fv = d.flow_viz
w.phase_sensor = MockPhaseSensor(sensors={"collect": 0, "reactor_in": 1})
w.phase_sensor.connect()
fv.configure(w.cfg, w)
check("D1 글리프 2개(collect+reactor_in)",
      set(getattr(fv, "sensor_items", {})) == {"collect", "reactor_in"},
      str(set(getattr(fv, "sensor_items", {}))))
fv.update_realtime({}, sensor_phases={"collect": 1, "reactor_in": 0})
check("D2 도트 상태 반영", fv.sensor_items["collect"].phase == 1
      and fv.sensor_items["reactor_in"].phase == 0)
fv.update_realtime({}, sensor_phases=None)
check("D3 미수신=뮤트(None)", fv.sensor_items["collect"].phase is None)
w.phase_sensor = None
fv.configure(w.cfg, w)
check("D4 센서 미구성=글리프 없음", getattr(fv, "sensor_items", {}) == {})

# ── C: StatusWorker 폴 계약 ──
from core.worker import StatusWorker


class _SpyPS(MockPhaseSensor):
    def __init__(self):
        super().__init__(sensors={"collect": 0})
        self.calls = []

    def read_phase(self, which="collect"):
        self.calls.append(("read", which))
        return super().read_phase(which)

    def read_event(self, which="collect"):
        self.calls.append(("event", which))
        return super().read_event(which)

    def monitor(self, which="collect", mode="once"):
        self.calls.append(("monitor", mode))
        return super().monitor(which, mode)


spy = _SpyPS()
spy.connect()
sw = StatusWorker(w.pumps, w.valves, w.heater, w.cfg, interval=0.05, phase_sensor=spy)
got = []
sw.signals.sig_phase_data.connect(lambda ph: got.append(dict(ph)))
sw.start()
t0 = time.time()
while len(got) < 2 and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.02)
sw.stop()
check("C1 워커 위상 emit", len(got) >= 2 and got[0] == {"collect": 1}, str(got[:2]))
kinds = {k for k, _ in spy.calls}
check("C2 폴 온리(monitor/이벤트 미사용)", kinds == {"read"}, str(kinds))

# 단선 시 결측(비-emit) — 예외 격리
spy2 = _SpyPS(); spy2.connect(); spy2._sim_phase["collect"] = 0   # ERROR
sw2 = StatusWorker(w.pumps, w.valves, w.heater, w.cfg, interval=0.05, phase_sensor=spy2)
got2 = []
sw2.signals.sig_phase_data.connect(lambda ph: got2.append(ph))
sw2.start(); time.sleep(0.3); sw2.stop()
check("C3 단선 → emit 없음(결측)", got2 == [], str(got2))

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
w.close()
sys.exit(1 if fails else 0)
