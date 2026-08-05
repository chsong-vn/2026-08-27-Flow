"""하드웨어 Hot Reload — 설정 변경의 무중단 반영

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

import time
import traceback

from PyQt5.QtWidgets import QMessageBox

from engine.config import SystemConfig
from core.hw_manager import HardwareManager
from core.utils import SystemMapManager


class HotReloadMixin:
    """Setting 다이얼로그 → 재시작 없는 하드웨어 재구성"""

    def _open_hardware_config(self):
        """Setting 버튼 클릭 시 HardwareConfigDialog 팝업 열기"""
        try:
            from ui.dialogs import HardwareConfigDialog  # type: ignore[import]
        except ImportError:
            return
        from PyQt5.QtWidgets import QDialog
        dialog = HardwareConfigDialog(self.cfg, self)
        if hasattr(dialog, "apply_theme"):
            dialog.apply_theme(getattr(self, "is_dark_mode", True))
        if dialog.exec_() == QDialog.Accepted:
            self.reload_hardware()

    def reload_hardware(self):
        """하드웨어 설정 변경사항을 재시작 없이 즉시 반영한다."""
        print(">>> 하드웨어 Hot Reload 시작...")

        # 1) 모니터링 스레드 중지
        if hasattr(self, 'mon_worker') and self.mon_worker:
            self.mon_worker.stop()
            self.mon_worker.wait(2000)

        # 2) 기존 하드웨어 정리
        self.hw_mgr.cleanup_hardware()

        # 3) 설정 재로드
        self.cfg = SystemConfig()

        # 4) 그래프 버퍼 재초기화
        self.st = time.time()
        self.dh = {"t": [], "temp": []}
        for p in self.cfg.ACTIVE_PUMPS:
            self.dh[p] = []

        # 5) 하드웨어 재초기화
        self.hw_mgr = HardwareManager(self.cfg, self.signals)
        try:
            print(">>> 하드웨어 드라이버 재로드 중...")
            self.hw_mgr.init_hw()
            print(">>> 하드웨어 재초기화 완료")
        except Exception as e:
            print(f"!!! 하드웨어 재초기화 오류: {e}")
            traceback.print_exc()
            QMessageBox.critical(self, "재로드 오류", f"하드웨어 초기화 실패:\n{e}")
            return False
        self._sync_hw_refs()

        # 6) 맵 매니저 재생성
        if self.cfg:
            self.map_mgr = SystemMapManager(self.cfg.ACTIVE_PUMPS)

        # 7) 대시보드 펌프 구성 갱신
        if hasattr(self, 'dash_tab') and self.dash_tab.flow_viz:
            # 시스템 전체 구성(push/collector/데드볼륨 포함)으로 배관도 재구성
            self.dash_tab.flow_viz.configure(self.cfg, self)
            if hasattr(self.dash_tab, "metric_labels") and "reactor_volume" in self.dash_tab.metric_labels:
                self.dash_tab.metric_labels["reactor_volume"].setText(f"{self.cfg.reactor_vol:.2f} mL")
            print(">>> 대시보드 펌프 구성을 갱신했습니다.")

        # 8) Manual 탭 펌프 위젯 재구성
        self.man_tab.rebuild_pump_widgets()
        self._register_manual_theme_widgets()
        print(">>> Manual 제어 위젯 재구성 및 테마 레지스트리 갱신 완료")

        # 9) Sequence 탭 재빌드
        self.seq_tab.rebuild()
        self._sync_seq_refs()
        self.theme_mgr.register_widgets('sequence', {
            'lbl_start_tube_seq': self.seq_tab.lbl_start_tube_seq,
            'seq_tab': self.seq_tab,
        })
        print(">>> Sequence 테이블 재구성 완료")

        # 10) 모니터링 재시작
        self.start_monitoring()

        # 11) 상태 텍스트 갱신
        num_pumps = len([p for p in self.cfg.ACTIVE_PUMPS if p in self.pumps])
        num_valves = len([v for v in self.valves.keys() if v != "Outlet"])
        has_heater = 1 if self.heater and type(self.heater).__name__ != "MockHeater" else 0

        if hasattr(self, 'lbl_manual_status'):
            self.lbl_manual_status.setText(
                f"연결 장치: 펌프 {num_pumps} | 밸브 {num_valves} | 히터 {has_heater}"
            )

        self.signals.sig_log.emit(">>> 하드웨어 Hot Reload 완료")
        print(">>> 하드웨어 Hot Reload 완료")
        return True

    def _sync_hw_refs(self):
        """HardwareManager에서 만든 장치 참조를 메인 객체에 동기화한다."""
        self.pumps = self.hw_mgr.pumps
        self.valves = self.hw_mgr.valves
        self.heater = self.hw_mgr.heater
        self.collector = self.hw_mgr.collector
        self.push_pump = self.hw_mgr.push_pump
        self.engine = self.hw_mgr.engine
        self.calculator = self.hw_mgr.calculator
        # @codesyncer-decision: robochem 계열 수동 장비도 앱 네임스페이스에 노출 —
        #   tab_manual 카드 렌더링용. 부팅(main)과 핫리로드가 같은 함수를 타므로
        #   여기 한 곳으로 두 경로 모두 해결.
        self.manual_pumps = self.hw_mgr.manual_pumps
        self.samplers = self.hw_mgr.samplers
        self.mfc = getattr(self.hw_mgr, "mfc", None)   # N2 MFC (droplet HTE)
        # 위상센서 어레이 (OCB350 — 슬러그 경계 실측, roles.phase)
        self.phase_sensor = getattr(self.hw_mgr, "phase_sensor", None)
