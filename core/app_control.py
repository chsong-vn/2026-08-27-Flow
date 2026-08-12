"""운전 제어 — Pause/Resume, Emergency Stop

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

import threading

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
        """전역 비상정지 — 유일한 E-STOP 진입점.

        @codesyncer-decision(2026-08-12, 사용자 지시): Manual 탭에 따로 있던
          E-STOP(`_estop_all`, 2026-07 MFC 작업 때 신설)을 폐지하고 이 함수로
          단일화했다. 상단바 Control 카드는 4개 페이지 위에 상시 노출되므로
          Manual 탭 중복 버튼은 '상시 노출' 근거를 이미 충족한 상태였고, 두 핸들러가
          서로 다른 범위를 커버해(이쪽=히터·분취기 / 저쪽=N2·Outlet) **어느 쪽을
          눌러도 안전상태가 안 되는** 결함이 있었다. 여기서 양쪽 범위를 합친다.

        범위: 엔진 abort(+pause 해제) · 리필워커 즉시탈출 · 히터 정지 ·
          그룹/수동/푸시 펌프 정지 · **N2(MFC) 0 sccm** · 샘플러 비상정지 ·
          분취기 이동정지 · Outlet→WASTE · Deep Wash 중단.

        @codesyncer-decision: 2단 분리 — (A) 플래그 계열은 GUI 스레드에서 즉시
          (순수 속성 쓰기, 시리얼 없음 → 리필/도징 워커가 지연 없이 탈출),
          (B) 시리얼 일괄 호출은 데몬 스레드(폐지된 Manual 핸들러의 설계를 계승).
          범위를 합치면서 장비 수가 늘어 GUI 스레드 동기 순회로는 비상 시
          수 초 프리즈가 되기 때문이다. 모달은 GUI 스레드에 그대로 두어 즉시 뜨고,
          모달이 입력을 잡는 동안 연타가 차단된다(별도 busy 플래그 불필요).
        """
        # ── (A) 즉시 반영 — 시리얼 없는 플래그 계열 ──────────────────
        if self.engine:
            if hasattr(self.engine, "abort_flag"):
                self.engine.abort_flag = True
            # @codesyncer: pause_event.set() 없으면 일시정지 중 E-STOP 시 엔진이
            #   pause_event.wait() 에 영원히 블록돼 cleanup(히터 OFF)에 못 간다.
            if hasattr(self.engine, "pause_event"):
                self.engine.pause_event.set()
        for p in (self.pumps or {}).values():
            # 리필 대기 워커의 블라인드 슬립 즉시 탈출 (교차 런 오발사 차단)
            if hasattr(p, "_abort_refill"):
                p._abort_refill = True

        # ── (B) 시리얼 일괄 정지 — 데몬 스레드 (GUI 무블록) ───────────
        threading.Thread(target=self._estop_devices, daemon=True).start()

        # ── (C) UI 상태 — GUI 스레드 ─────────────────────────────────
        self.btn_p.setChecked(False)
        self.btn_p.setText("Pause")
        self._update_status_bar("Emergency Stop")
        self.lbl_phase.setText("")
        QMessageBox.critical(self, "Emergency Stop", "시스템이 비상 정지되었습니다.")

    def _estop_devices(self):
        """E-STOP 장비 정지 본체 — 데몬 스레드에서 실행. 장비별 예외 격리."""
        # Deep Wash 가 돌고 있으면 함께 중단 (엔진과 같은 펌프·밸브를 쓴다)
        try:
            dw = getattr(getattr(self, "man_tab", None), "_dw_engine", None)
            if dw is not None and getattr(dw, "running", False):
                dw.stop()
        except Exception:
            pass

        if self.heater and hasattr(self.heater, "stop"):
            try:
                self.heater.stop()
            except Exception:
                pass
        for p in (self.pumps or {}).values():
            try:
                if hasattr(p, "stop"):
                    p.stop()
            except Exception:
                pass
        if self.push_pump is not None and hasattr(self.push_pump, "stop"):
            try:
                self.push_pump.stop()
            except Exception:
                pass
        for mp in (getattr(self, "manual_pumps", {}) or {}).values():
            try:
                if hasattr(mp, "stop"):
                    mp.stop()
            except Exception:
                pass
        # @codesyncer-decision: N2(MFC) 차단은 비상정지의 필수 범위 —
        #   유량이 멎은 라인에 가스가 계속 들어가면 잔압/시약 역압출이 남는다.
        #   기존엔 Manual 핸들러에만 있었다(단일화 시 유실 방지).
        m = getattr(self, "mfc", None)
        if m is not None and hasattr(m, "set_flow"):
            try:
                m.set_flow(0.0)
            except Exception:
                pass
        # @codesyncer-decision: RoboChem 수동 장비도 비상정지 범위에 포함 —
        #   샘플러는 니들이 vial 에 꽂힌 채 움직일 수 있는 장비인데 기존 E-Stop 이
        #   건드리지 않았음. emergency_stop 은 락 우회 raw 0x18 이라 이동 중에도 즉시.
        for s in (getattr(self, "samplers", {}) or {}).values():
            try:
                if hasattr(s, "emergency_stop"):
                    s.emergency_stop()
            except Exception:
                pass
        if self.collector and hasattr(self.collector, "stop_motion"):
            try:
                self.collector.stop_motion()
            except Exception:
                pass
        # Outlet→WASTE = 이 엔진이 선언한 안전 종단 상태 (제품 라인 격리)
        v = (getattr(self, "valves", {}) or {}).get("Outlet")
        if v is not None and hasattr(v, "set_position"):
            try:
                v.set_position(1)
            except Exception:
                pass
        try:
            self.signals.sig_log.emit(
                "[E-STOP] 비상정지 실행 — 히터 OFF · 전 펌프 정지 · N2 0 · "
                "Outlet WASTE · 분취기/샘플러 정지")
        except Exception:
            pass
