"""운전 제어 — Pause/Resume, Emergency Stop

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

from PyQt5.QtWidgets import QMessageBox


class RunControlMixin:
    """일시정지/비상정지 — 엔진·펌프·히터·컬렉터 일괄 제어"""

    def tog_pause(self, checked):
        if not self.engine:
            return
        if checked:
            self.engine.pause_event.clear()
            for p in self.pumps.values():
                if hasattr(p, "stop"):
                    p.stop()
            if self.push_pump is not None and hasattr(self.push_pump, "stop"):
                try:
                    self.push_pump.stop()
                except Exception:
                    pass
            self.btn_p.setText("Resume")
            self._update_status_bar("Paused")
        else:
            self.engine.pause_event.set()
            self.btn_p.setText("Pause")
            self._update_status_bar("Running")

    def estop(self):
        if self.engine:
            if hasattr(self.engine, "abort_flag"):
                self.engine.abort_flag = True
            if hasattr(self.engine, "pause_event"):
                self.engine.pause_event.set()
        if self.heater and hasattr(self.heater, "stop"):
            self.heater.stop()
        for p in self.pumps.values():
            if hasattr(p, "_abort_refill"):
                p._abort_refill = True
            if hasattr(p, "stop"):
                p.stop()
        if self.push_pump is not None and hasattr(self.push_pump, "stop"):
            try:
                self.push_pump.stop()
            except Exception:
                pass
        if self.collector and hasattr(self.collector, "stop_motion"):
            self.collector.stop_motion()
        # @codesyncer-decision: RoboChem 수동 장비도 비상정지 범위에 포함 —
        #   샘플러는 니들이 vial 에 꽂힌 채 움직일 수 있는 장비인데 기존 E-Stop 이
        #   건드리지 않았음. emergency_stop 은 락 우회 raw 0x18 이라 이동 중에도 즉시.
        for s in (getattr(self, "samplers", {}) or {}).values():
            try:
                if hasattr(s, "emergency_stop"):
                    s.emergency_stop()
            except Exception:
                pass
        for mp in (getattr(self, "manual_pumps", {}) or {}).values():
            try:
                if hasattr(mp, "stop"):
                    mp.stop()
            except Exception:
                pass
        self.btn_p.setChecked(False)
        self.btn_p.setText("Pause")
        self._update_status_bar("Emergency Stop")
        self.lbl_phase.setText("")
        QMessageBox.critical(self, "Emergency Stop", "시스템이 비상 정지되었습니다.")
