# -*- coding: utf-8 -*-
"""Manual 탭 버튼-그룹 논리 배치 검증 (오프스크린).

검증 항목:
 A. 모듈러 그리드 — Feed 카드 균일폭(CARD_W)·상단선(Y) 정렬
 B. 채널 카드 내부 그룹핑 — Valve Path 트랙 / 소스(입력→아래 MOVE) /
    유속 필드 / [INFUSE|WITHDRAW] 행 / STOP 전폭 분리 행
 C. 존 소속 — 히터·차트·Push 위젯은 PROCESS(g_reactor) 하위,
    Outlet 트랙·수집기 컨트롤은 COLLECT(g_outlet) 하위
 D. STOP 식별 — 적색 아웃라인(ACCENT_RED) vs INFUSE 중립, E-Stop 과 별개
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, QEventLoop

ACTIVE = ["Group_A", "Group_B", "Group_C"]


class Sig:
    def emit(self, *a): pass


class Signals:
    def __init__(self): self.sig_log = Sig()


class MockPump:
    def __init__(self):
        self.running = False; self.is_refilling = False; self.current_vol = 0.0
        self.diameter = 20.0; self.capacity = 10.0
    def stop(self): pass
    def set_flow(self, r): pass
    def start(self): pass
    def refill(self, *a, **k): pass
    def wash_cycle(self, *a, **k): pass
    def prime_reactor(self, *a, **k): pass


class MockValve:
    def __init__(self, pos=1): self.position = pos
    def set_position(self, p): self.position = p


class MockHeater:
    target_temp = 25.0
    def set_temperature(self, t): pass
    def stop(self): pass


class MockPush:
    running = False
    def set_flow(self, r): pass
    def start(self): pass
    def stop(self): pass
    def get_pressure(self): return 0.0


class MockCollector:
    total_tubes = 88
    is_connected = True
    def get_position(self): return 0
    def home(self): return True, "ok"
    def move_to_tube(self, n): return True, "ok"


class MockHW:
    sampler_by_pump = {}


class MockMap:
    def get_inlet(self, pump, port): return {"name": ""}


class MockCfg:
    ACTIVE_PUMPS = ACTIVE
    PUMP_ROUTING = {n: "external_valve" for n in ACTIVE}
    reactor_vol = 1.98
    config_data = {"system_params": {}, "roles": {}, "inventory": []}
    def save_config(self, *a, **k): pass


class FakeApp:
    def __init__(self):
        self.is_dark_mode = True
        self.cfg = MockCfg(); self.signals = Signals(); self.map_mgr = MockMap()
        self.hw_mgr = MockHW(); self.heater = MockHeater(); self.push_pump = MockPush()
        self.collector = MockCollector(); self.manual_pumps = {}; self.samplers = {}
        self.pumps = {n: MockPump() for n in ACTIVE}
        self.valves = {"Outlet": MockValve(1)}
        for n in ACTIVE:
            self.valves[f"{n}_Selector"] = MockValve(4)
            self.valves[f"{n}_Switcher"] = MockValve(2)
        self.dh = {"t": [], "temp": []}
        self.log_browser = None


def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec_()


def is_ancestor(anc, w):
    p = w
    while p is not None:
        if p is anc:
            return True
        p = p.parentWidget()
    return False


app = QApplication(sys.argv)
from ui.tab_manual import ManualTab
from ui.widgets.channel_column import ChannelColumnCard, CARD_W
from ui.colors import DarkPalette as P

tab = ManualTab(FakeApp())
tab.resize(1400, 800)
tab.show()
wait(150)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


cols = [w for w in tab.pump_card_widgets if isinstance(w, ChannelColumnCard)]
check("A1 채널 카드 3장", len(cols) == 3, f"{len(cols)}")
check("A2 카드 균일폭", all(c.width() == CARD_W for c in cols),
      str([c.width() for c in cols]))
tops = [c.mapTo(tab, c.rect().topLeft()).y() for c in cols]
check("A3 상단선(Y) 정렬", len(set(tops)) == 1, str(tops))

c = cols[0]
# B. 카드 내부 그룹핑
check("B1 Valve Path = 트랙 안 SOURCE/REACTOR 2세그",
      c.btn_path_src is not None and c.btn_path_rct is not None
      and c.btn_path_src.parentWidget() is c.path_track
      and c.btn_path_rct.parentWidget() is c.path_track
      and not hasattr(c, "btn_path_waste"))
check("B2 소스: 입력 위 · MOVE 아래(전폭)",
      c.sp_port is not None and c.btn_port_go is not None
      and c.btn_port_go.y() > c.sp_port.y()
      and abs(c.btn_port_go.width() - c.sp_port.width()) <= 2,
      f"spin y={c.sp_port.y()} w={c.sp_port.width()} / MOVE y={c.btn_port_go.y()} w={c.btn_port_go.width()}")
check("B3 유속 필드 2개(Infuse/Withdraw) 나란히",
      abs(c.sp_infuse.y() - c.sp_withdraw.y()) <= 2)
check("B4 INFUSE|WITHDRAW 같은 행 · 균등폭",
      abs(c.btn_infuse.y() - c.btn_withdraw.y()) <= 2
      and abs(c.btn_infuse.width() - c.btn_withdraw.width()) <= 4)
check("B5 STOP = 아래 분리 행 · 전폭(오조작 방지)",
      c.btn_stop.y() > c.btn_infuse.y()
      and c.btn_stop.width() > c.btn_infuse.width() * 1.7,
      f"stop y={c.btn_stop.y()} w={c.btn_stop.width()} vs infuse y={c.btn_infuse.y()} w={c.btn_infuse.width()}")
# 감사 2026-07-13: 상태배지 푸터→헤더 승격(멀티채널 스캔성), 푸터=Vol+잔량게이지
check("B6 헤더=상태배지 · 푸터=Vol+게이지",
      c.lbl_vol is not None and c.lbl_state is not None
      and c.lbl_state.y() < c.btn_stop.y()
      and getattr(c, "bar_vol", None) is not None
      and c.bar_vol.y() > c.btn_stop.y(),
      f"state y={c.lbl_state.y()} bar y={getattr(getattr(c, 'bar_vol', None), 'y', lambda: -1)()} stop y={c.btn_stop.y()}")

# C. 존 소속
for name, w in [("lbl_heater", tab.lbl_heater), ("sh", tab.sh),
                ("btn_h_set", tab.btn_h_set), ("btn_h_off", tab.btn_h_off),
                ("push flow", tab.sp_push_flow), ("push start", tab.btn_push_start),
                ("push stop", tab.btn_push_stop)]:
    check(f"C1 PROCESS 하위: {name}", is_ancestor(tab.g_reactor, w))
if tab._temp_plot is not None:
    check("C2 온도차트 PROCESS 하위 + 높이>=120",
          is_ancestor(tab.g_reactor, tab._temp_plot) and tab._temp_plot.height() >= 120,
          f"h={tab._temp_plot.height()}")
for name, w in [("outlet track", tab.outlet_track), ("btn_waste", tab.btn_waste),
                ("btn_coll", tab.btn_coll), ("collector move", tab.btn_collector_move),
                ("plate96 box", tab.plate96_ctrl_box)]:
    check(f"C3 COLLECT 하위: {name}", is_ancestor(tab.g_outlet, w))
check("C4 Waste/Collect = 같은 트랙 세그먼트",
      tab.btn_waste.parentWidget() is tab.outlet_track
      and tab.btn_coll.parentWidget() is tab.outlet_track)

# D. STOP 식별 + 임의 버튼 부재
ss_stop = c.btn_stop.styleSheet()
ss_inf = c.btn_infuse.styleSheet()
check("D1 STOP=적색 아웃라인, INFUSE=중립(다름)",
      P.ACCENT_RED in ss_stop and P.ACCENT_RED not in ss_inf)
check("D2 히터 OFF·Push STOP 도 동일 STOP 규격",
      P.ACCENT_RED in tab.btn_h_off.styleSheet()
      and P.ACCENT_RED in tab.btn_push_stop.styleSheet())
check("D3 폐기된 임의 버튼 없음 (모두토출/모두정지/safety바)",
      not hasattr(tab, "btn_infuse_all") and not hasattr(tab, "btn_stop_all")
      and not hasattr(tab, "quick_frame"))

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
