# -*- coding: utf-8 -*-
"""Valve Path SOURCE/REACTOR 강조 동기화 수정 검증 (offscreen).

 사용자 보고: "방향에 따라 강조표시가 바뀌어야 하는데 안 바뀜".
 원인: ①Manual INFUSE 가 3-way 를 REACTOR 로 정렬하지 않음(모터만 구동 —
   물리적으로 소스 방향 역류) ②동작 후 재동기가 1.5s 타이머 대기.
 수정: INFUSE→REACTOR(2)/raw WITHDRAW→SOURCE(1) 정렬, _switch_path 스레드화,
   _threaded 완료 시 _post_action_sync(즉시 강조 재동기).
"""
import os, sys, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, QEventLoop

app = QApplication(sys.argv)
from ui.widgets.channel_column import ChannelColumnCard

ok = True
def chk(c, m, detail=""):
    global ok
    print(("PASS" if c else "FAIL") + ": " + m + (f"  {detail}" if detail else ""))
    ok = ok and bool(c)

def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec_()


class FakeSwitcher:
    def __init__(self): self.position = 1; self.calls = []
    def set_position(self, pos):
        self.calls.append(pos); self.position = pos


class FakeDriver:
    def __init__(self): self.started = 0; self.vol = None
    def set_diameter(self, d): pass
    def set_rate(self, r): pass
    def set_volume(self, v): self.vol = v
    def start(self): self.started += 1
    def stop(self): pass
    def is_stopped(self): return True


class FakePump:
    def __init__(self):
        self.driver = FakeDriver(); self.diameter = 12.0; self.capacity = 6.0
        self.running = False; self.is_refilling = False; self.current_vol = 3.0


sw = FakeSwitcher()
pump = FakePump()
card = ChannelColumnCard("Group A", pump, selector_obj=None, switcher_obj=sw,
                         routing="external_valve")
card.show(); app.processEvents()

# 초기: switcher pos 1 → SOURCE 체크
chk(card.btn_path_src.isChecked(), "초기 강조 = SOURCE(pos 1)")

# ── 1) INFUSE → 밸브 REACTOR(2) 정렬 + 강조 즉시 추적 ──
card._do_infuse()
for _ in range(40):
    if not card._busy: break
    wait(50)
wait(120)   # queued _post_action_sync 처리
chk(sw.position == 2 and 2 in sw.calls, "INFUSE → 3-way REACTOR(2) 정렬", str(sw.calls))
chk(pump.driver.started == 1, "INFUSE → 모터 구동")
chk(card.btn_path_rct.isChecked(), "강조 = REACTOR 로 즉시 갱신 (타이머 불요)")

# ── 2) raw WITHDRAW(스마트 refill 없음) → SOURCE(1) 정렬 + 강조 추적 ──
card._do_withdraw()
for _ in range(40):
    if not card._busy: break
    wait(50)
wait(120)
chk(sw.position == 1, "WITHDRAW → 3-way SOURCE(1) 정렬", str(sw.calls))
chk(pump.driver.vol is not None and pump.driver.vol < 0, "WITHDRAW → 음수 볼륨 흡입")
chk(card.btn_path_src.isChecked(), "강조 = SOURCE 로 즉시 갱신")

# ── 3) 세그먼트 클릭(_switch_path) — 스레드화 + 성공 유지 ──
card._switch_path(2)
for _ in range(40):
    if not card._busy: break
    wait(50)
wait(120)
chk(sw.position == 2 and card.btn_path_rct.isChecked(), "세그먼트 REACTOR 클릭 → 이동+강조")

# ── 4) 전환 실패 시 강조 원복 ──
class FailSwitcher(FakeSwitcher):
    def set_position(self, pos):
        raise RuntimeError("sim: no ACK")
fs = FailSwitcher(); fs.position = 2
card2 = ChannelColumnCard("Group B", FakePump(), switcher_obj=fs,
                          routing="external_valve")
card2.show(); app.processEvents()
chk(card2.btn_path_rct.isChecked(), "카드2 초기 REACTOR")
card2.btn_path_src.setChecked(True)      # 사용자가 SOURCE 클릭(체크 선반영)
card2._switch_path(1)
for _ in range(40):
    if not card2._busy: break
    wait(50)
wait(150)
chk(card2.btn_path_rct.isChecked(), "전환 실패 → 강조가 실제 위치(REACTOR)로 원복")

# ── 5) INFUSE 재호출 시 이미 REACTOR 면 재전송 안 함 ──
sw.calls.clear()
card._do_infuse()
for _ in range(40):
    if not card._busy: break
    wait(50)
chk(2 not in sw.calls, "이미 REACTOR → set_position 재전송 생략", str(sw.calls))

print()
print("=== " + ("ALL PASS" if ok else "SOME FAIL") + " ===")
sys.exit(0 if ok else 1)
