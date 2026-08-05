import sys
import os
import time
import traceback

# ─── Python bytecode 캐시 관리 (stale .pyc 방지) ───────────────────────
# Python 3.14 + debugpy 런처 조합에서 소스 변경 후에도 옛 .pyc가 그대로
# 실행되는 문제 발생 가능. 프로젝트 디렉토리의 __pycache__를 시작 시 정리.
# @codesyncer-risk: shutil.rmtree로 디렉토리 삭제 — 비프로젝트 경로 실수로
#   건드리지 않도록 __file__ 기준 프로젝트 루트로 제한
sys.dont_write_bytecode = True
try:
    import shutil
    _PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    _removed = 0
    for _dirpath, _dirnames, _ in os.walk(_PROJECT_ROOT):
        for _name in list(_dirnames):
            if _name == "__pycache__":
                _target = os.path.join(_dirpath, _name)
                try:
                    shutil.rmtree(_target, ignore_errors=True)
                    _removed += 1
                except Exception:
                    pass
                _dirnames.remove(_name)  # 중복 방문 방지
    if _removed:
        print(f">>> Cleared {_removed} stale __pycache__ dir(s)")
except Exception as _e:
    print(f">>> __pycache__ cleanup warning (무시): {_e}")
# ───────────────────────────────────────────────────────────────────

# PyQt5 Modules — 부트스트랩에 필요한 것만 (UI 위젯 import는 ui/main_window_ui.py로 이동)
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox, QStyleFactory
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QPixmap, QIcon

# Engine & Core
from engine.config import SystemConfig
from core.utils import SystemMapManager
from core.worker import WorkerSignals
from core.hw_manager import HardwareManager
from core.reagent_excel import ReagentExcelManager
from core.method_io import MethodIO
from ui.colors import (
    install_global_stylesheet_color_patch,
    set_active_dark_mode,
)
from ui.theme_manager import ThemeManager

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# @codesyncer-decision: main.py 책임 분리 — 부트스트랩만 남기고 믹스인으로 분산
# - ui/main_window_ui.py    : UI 조립 (init_ui, 네비게이션, 테마 레지스트리)
# - core/app_monitoring.py  : 센서 모니터링/상태바/Phase 표시
# - core/app_control.py     : Pause/E-Stop 운전 제어
# - core/app_hot_reload.py  : 하드웨어 설정 핫 리로드
# - core/app_remote.py      : 원격 명령 폴링
# 믹스인은 self 네임스페이스를 공유하므로 기존 `app.속성` 참조가 전부 유지됨.
from ui.main_window_ui import MainWindowUIMixin
from core.app_monitoring import MonitoringMixin
from core.app_control import RunControlMixin
from core.app_hot_reload import HotReloadMixin
from core.app_remote import RemoteCmdMixin


class AutoPairingGUI(MainWindowUIMixin, MonitoringMixin, RunControlMixin,
                     HotReloadMixin, RemoteCmdMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.is_dark_mode = True
        set_active_dark_mode(True)
        install_global_stylesheet_color_patch(lambda: getattr(self, "is_dark_mode", True))

        self.setWindowTitle("VORONOI Flowchemistry Platform")

        # 앱 아이콘 설정
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as f:
                icon_data = f.read()
            icon_pixmap = QPixmap()
            icon_pixmap.loadFromData(icon_data)
            if not icon_pixmap.isNull():
                self.setWindowIcon(QIcon(icon_pixmap))

        # 테마 관리자 초기화
        self.theme_mgr = ThemeManager(self)
        self.theme_mgr.setup_stylesheet()
        
        print(">>> 시스템 부팅 시작...")
        
        self.cfg = SystemConfig()
        self.signals = WorkerSignals()
        
        # 데이터/맵 관련 참조
        self.reagent_tables = {}
        self.map_mgr = None

        # Excel 시약 관리
        self.excel_mgr = ReagentExcelManager(self)
        # Method 저장/로드
        self.method_io = MethodIO(self)

        # 그래프 버퍼 초기화
        self.st = time.time()
        self.dh = {"t":[], "temp":[]}
        for p in self.cfg.ACTIVE_PUMPS:
            self.dh[p] = []
        # 위상센서 0/1 트랙 버퍼 — dh 와 분리 (센서는 나중에 붙을 수 있어 시계열
        # 길이가 달라짐; dh 공용 트림 루프에 섞이면 pop desync).
        # 구조 = {채널: {"t": [...], "v": [...]}} — 다중 센서(collect/reactor_in) 지원
        self.dh_phase = {}

        # 1) 하드웨어 초기화
        self.hw_mgr = HardwareManager(self.cfg, self.signals)
        try:
            print(">>> 하드웨어 드라이버 로드 중...")
            self.hw_mgr.init_hw()
            print(">>> 하드웨어 초기화 완료")
        except Exception as e:
            print(f"!!! 하드웨어 초기화 치명적 오류: {e}")
            traceback.print_exc()
        self._sync_hw_refs()

        # 2) 맵 매니저
        if self.cfg:
            self.map_mgr = SystemMapManager(self.cfg.ACTIVE_PUMPS)
        
        # 3) UI 초기화
        try:
            self.init_ui()
            print(">>> UI 구성 완료")
        except Exception as e:
            print(f"!!! UI 초기화 오류: {e}")
            traceback.print_exc()
            
        # 시그널 연결
        if hasattr(self, 'log_browser'):
            self.signals.sig_log.connect(self.log_browser.append)
        
        self.signals.sig_status.connect(self._update_status_bar)
        self.signals.sig_error.connect(lambda e: QMessageBox.critical(self, "시스템 오류", e))

        # 대시보드 진행률 시그널 연결 + 상태바 phase 표시
        self.signals.sig_phase_progress.connect(self.dash_tab.flow_viz.set_progress)
        self.signals.sig_phase_progress.connect(self._update_phase_label)
        self.signals.sig_finished.connect(lambda: self.dash_tab.flow_viz.set_progress("", -1))
        self.signals.sig_finished.connect(lambda: self._update_phase_label("", -1))

        # 모니터링 시작
        self.start_monitoring()

        # 원격 명령 감시
        self._remote_cmd_file = os.path.join(os.path.dirname(__file__), "remote_cmd.txt")
        self._remote_cmd_timer = QTimer(self)
        self._remote_cmd_timer.timeout.connect(self._poll_remote_cmd)
        self._remote_cmd_timer.start(500)

        print(">>> 메인 루프 진입")

    def closeEvent(self, event):
        # 모니터링 스레드 정리
        if hasattr(self, 'mon_worker') and self.mon_worker:
            self.mon_worker.stop()
            self.mon_worker.wait(2000)
        self.hw_mgr.cleanup_hardware()
        event.accept()

if __name__ == "__main__":
    # @codesyncer-decision: WebGrid(QtWebEngine, 계산기 Tabulator 그리드)는 QApplication
    #   생성 전에 AA_ShareOpenGLContexts 설정이 필요하다.
    from PyQt5.QtCore import Qt as _Qt
    QApplication.setAttribute(_Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    w = AutoPairingGUI()
    w.showMaximized()  # 최대화 상태로 시작
    sys.exit(app.exec_())
