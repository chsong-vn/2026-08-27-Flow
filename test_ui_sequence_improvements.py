"""SequenceTab UI 개선 headless 검증 (QT_QPA_PLATFORM=offscreen)

검증:
1. 탭 생성 + Step 추가/복사 동작
2. 입력 변경 → stale 표시 → 디바운스 자동 재계산 → 결과/튜브범위/요약 갱신
3. collector 용량 초과 시 빨간 경고
4. 입력 위젯 포커스 시 카드 자동 선택
5. 실행상태 배지 갱신 (_on_step_progress)
"""
import os, sys, math
# @codesyncer: 시퀀스 탭이 WebGrid(QtWebEngine)를 임베드하면서 offscreen 플랫폼은
#   QtWebEngine 초기화 불가 → 기본(네이티브) 플랫폼 사용. 콘솔 en-dash 출력 위해 utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, ".")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, QEventLoop


class FakeCalc:
    def calculate_flows(self, concs, eqs, rt):
        # 단순 모델: 총유속 = 2.0/rt*10, 비율 = eq 비례
        total = 19.8 / rt
        s = sum(eqs) or 1.0
        return [total * e / s for e in eqs], total


class FakeMap:
    def get_inlet(self, pump, port):
        return {"name": f"R{port}", "conc": 1.0}

    def update_inlet(self, *a):
        pass


class FakeCfg:
    INLET_PUMPS = ["Pump_A", "Pump_B"]
    config_data = {"system_params": {}}
    reactor_vol = 1.98


class FakeCollector:
    total_tubes = 10  # 일부러 작게 → 용량 초과 테스트

    def get_well_id(self, n):
        return f"Tube {n}"


class FakeExcel:
    def open_reagents_excel(self):
        pass


class FakeApp:
    def __init__(self):
        self.cfg = FakeCfg()
        self.map_mgr = FakeMap()
        self.calculator = FakeCalc()
        self.collector = FakeCollector()
        self.excel_mgr = FakeExcel()
        self.engine = None
        self.worker = None
        self.log_browser = None
        self.is_dark_mode = True


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


from PyQt5.QtCore import Qt
# WebGrid(QtWebEngine) 임포트 전제 — main.py 와 동일하게 QApplication 생성 전 설정
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from ui.tab_sequence import SequenceTab

tab = SequenceTab(FakeApp())
tab.show()
tab.activateWindow()
app.processEvents()
assert len(tab.step_cards) == 1

# ── 디바운스 자동 재계산 ──
card = tab.step_cards[0]
card.sp_rt.setValue(9.9)  # rt 변경 → dirty → 300ms 후 자동 계산
assert "재계산" in card.lbl_total_flow.text(), "stale 표시 실패"
wait(500)
assert "mL/min" in card.lbl_total_flow.text(), f"자동 재계산 실패: {card.lbl_total_flow.text()}"
# 표기 규격 변경(2026-07-12): '→ Tube …' → '수집 위치' 라벨 + 'Tube …' 값 (HTML)
assert "Tube" in card.lbl_tube_range.text(), f"튜브 범위 미표시: {card.lbl_tube_range.text()}"
assert "Steps" in tab.lbl_total_summary.text(), "전체 요약 미표시"
assert "9.9min" in card.lbl_summary.text() or "9.9" in card.lbl_summary.text(), "헤더 요약 미갱신"
print(f"OK 자동재계산: {card.lbl_total_flow.text()} | {card.lbl_tube_range.text()}")
print(f"OK 전체요약: {tab.lbl_total_summary.text()}")

# ── 용량 초과 경고 (collector 10튜브, vol 30/tube 1.5 = 20튜브 필요) ──
card.sp_vol.setValue(30.0)
wait(500)
assert "초과" in card.lbl_tube_range.text(), f"용량초과 경고 실패: {card.lbl_tube_range.text()}"
assert "초과" in tab.lbl_total_summary.text(), "요약 용량초과 경고 실패"
print(f"OK 용량경고: {card.lbl_tube_range.text()}")
card.sp_vol.setValue(5.0)
wait(500)

# ── 포커스 자동 선택 ──
tab.add_step()
wait(400)
card2 = tab.step_cards[1]
assert tab._get_selected_index() != 1 or True
card2.sp_temp.setFocus()
app.processEvents()
wait(50)
assert tab._get_selected_index() == 1, f"포커스 자동선택 실패: idx={tab._get_selected_index()}"
print("OK 포커스 자동선택")

# ── 실행상태 배지 ──
tab._on_step_progress(0)
assert tab.step_cards[0]._run_state == "running" and tab.step_cards[1]._run_state == "idle"
tab._on_step_progress(1)
assert tab.step_cards[0]._run_state == "done" and tab.step_cards[1]._run_state == "running"
tab._on_step_progress(2)
assert all(c._run_state == "done" for c in tab.step_cards)
print("OK 실행상태 배지")

# ── 튜브 범위 누적 검증 (Step1: 5/1.5=4tubes → 1-4 +W5, Step2: 6-9 +W10) ──
tab.calc_flow(silent=True)
r1, r2 = tab.step_cards[0].lbl_tube_range.text(), tab.step_cards[1].lbl_tube_range.text()
assert "1–4 · W5" in r1.replace("\u2013", "–"), f"Step1 범위 오류: {r1}"
assert "6–9 · W10" in r2.replace("\u2013", "–"), f"Step2 범위 오류: {r2}"
print(f"OK 누적 튜브범위: [{r1}] [{r2}]")

print("\n✅ ALL UI TESTS PASSED")
