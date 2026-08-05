"""실시간 모니터링 — 센서 폴링, 상태바 의미색 칩, Phase 진행률

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

import time

from PyQt5.QtCore import QTimer

from core.worker import StatusWorker
from ui.colors import T


class MonitoringMixin:
    """센서 모니터링 스레드 + 상태바/Phase 라벨/대시보드 실시간 갱신"""

    def start_monitoring(self):
        self.mon_worker = StatusWorker(self.pumps, self.valves, self.heater, self.cfg,
                                       interval=1.0,
                                       phase_sensor=getattr(self, "phase_sensor", None))
        self.mon_worker.signals.sig_mon_data.connect(self.update_monitor_data)
        self.mon_worker.signals.sig_phase_data.connect(self.update_phase_data)
        self.mon_worker.start()
        # 위상 트랙 표시 동기화 (부팅·핫리로드 공용 진입점)
        if hasattr(self, "dash_tab") and hasattr(self.dash_tab, "set_phase_track_visible"):
            self.dash_tab.set_phase_track_visible(
                getattr(self, "phase_sensor", None) is not None)

    def update_phase_data(self, phases: dict):
        """위상센서 0/1 스냅(전 채널) → 채널별 버퍼 + 대시보드 멀티레인 트랙.

        @codesyncer: dh 공용 트림과 분리된 dh_phase 사용 (시계열 길이 독립).
        구조 = {채널: {"t": [...], "v": [...]}} — 1~4센서 동시 지원.
        마지막 스냅은 배관도 센서 도트용으로 캐시(_last_sensor_phases)."""
        if not hasattr(self, 'dash_tab') or not phases:
            return
        t = time.time() - self.st
        for key, v in phases.items():
            s = self.dh_phase.setdefault(str(key), {"t": [], "v": []})
            s["t"].append(t)
            s["v"].append(int(v))
            if len(s["t"]) > 300:
                s["t"].pop(0)
                s["v"].pop(0)
        self._last_sensor_phases = {str(k): int(v) for k, v in phases.items()}
        if hasattr(self.dash_tab, "update_phase"):
            self.dash_tab.update_phase(self.dh_phase)

    def update_monitor_data(self, temp, pressures, p_status, v_status):
        if not hasattr(self, 'dash_tab'): return
        d = self.dash_tab
        t = time.time() - self.st
        self.dh["t"].append(t)
        self.dh["temp"].append(temp)
        self.lcd_t.display(temp)
        d.crv_t.setData(self.dh["t"], self.dh["temp"])

        # Manual 탭 반응기 온도 실시간 표시
        if hasattr(self, 'lbl_reactor_temp'):
            self.lbl_reactor_temp.setText(f"{temp:.1f} °C")

        for p_name, val in pressures.items():
            if p_name in self.dh:
                self.dh[p_name].append(val)
                if p_name in self.lcds: self.lcds[p_name].display(val)
                if p_name in d.crv_p: d.crv_p[p_name].setData(self.dh["t"], self.dh[p_name])

        if len(self.dh["t"]) > 300:
            for k in self.dh: self.dh[k].pop(0)

        o_pos = "WASTE"
        if "Outlet" in self.valves and hasattr(self.valves["Outlet"], "position"):
            o_pos = self.valves["Outlet"].position

        # 대시보드 공정 상태 실시간 갱신
        # collector 위치 / push pump 상태도 배관도에 전달
        col_pos, col_well = None, ""
        col = getattr(self, "collector", None)
        if col is not None and hasattr(col, "get_position"):
            try:
                col_pos = col.get_position()
                if col_pos and hasattr(col, "get_well_id"):
                    w = col.get_well_id(col_pos)
                    if w and w not in ("?",):
                        col_well = str(w)
            except Exception:
                pass
        push_running = bool(getattr(getattr(self, "push_pump", None), "running", False))

        kw = dict(
            pumps_status=p_status,
            valves_status=None,
            selector_ports=v_status,
            outlet_valve_pos=o_pos,
            reactor_temp=temp,
            collector_pos=col_pos,
            collector_well=col_well,
            push_running=push_running,
        )
        # Diagram v3 확장: 흡입 방향/3-way 실위치/AS 현재 바이알 (V3 광고 시에만)
        if getattr(d.flow_viz, "V3", False):
            p_aug = {}
            for _n, _st in (p_status or {}).items():
                _d = dict(_st) if isinstance(_st, dict) else {"running": bool(_st)}
                _p = (getattr(self, "pumps", {}) or {}).get(_n)
                _d["refilling"] = bool(getattr(_p, "is_refilling", False))
                p_aug[_n] = _d
            kw["pumps_status"] = p_aug
            kw["switcher_pos"] = {
                _n[:-9]: getattr(_v, "position", None)
                for _n, _v in (getattr(self, "valves", {}) or {}).items()
                if _n.endswith("_Switcher")}
            _samp = {}
            for _n, _s in (getattr(getattr(self, "hw_mgr", None),
                                   "sampler_by_pump", {}) or {}).items():
                _v = getattr(_s, "current_vial", None)
                if _v is not None:
                    _samp[_n] = _v
            kw["sampler_vials"] = _samp
            _mfc = getattr(self, "mfc", None) or getattr(
                getattr(self, "hw_mgr", None), "mfc", None)
            kw["gas_flowing"] = bool(_mfc is not None
                                     and float(getattr(_mfc, "_sp", 0.0) or 0.0) > 0)
            # 위상센서 도트 (1Hz sig_phase_data 캐시 — 없으면 None=뮤트)
            kw["sensor_phases"] = getattr(self, "_last_sensor_phases", None)
        d.flow_viz.update_realtime(**kw)
        if hasattr(d, "update_metrics"):
            d.update_metrics(temp, pressures, p_status, o_pos)

    def _update_status_bar(self, msg: str):
        """상태 메시지 갱신 + 의미색 칩 (ISA-101: 색은 상태 전달에만 사용)

        @codesyncer-decision: 상태 키워드 기반 자동 색상 — 가동=녹,
        일시정지/대기=황, 오류/중단=적, 그 외=중립. 색맹 대응을 위해
        색뿐 아니라 텍스트 자체가 항상 상태를 서술함.
        """
        self.lbl_status.setText(msg)
        from ui.colors import get_palette
        P = get_palette(getattr(self, 'is_dark_mode', True))
        low = (msg or "").lower()
        base = (f"font-size: {T.FS_MD}; font-weight: {T.FW_SEMI}; "
                f"border-radius: {T.R_SM}; padding: 2px 10px;")
        if any(k in low for k in ("error", "abort", "fail", "오류", "중단", "emergency")):
            fg, bg = P.STATE_FAULT, P.STATE_FAULT_BG
        elif any(k in low for k in ("pause", "일시정지", "wait", "대기")):
            fg, bg = P.STATE_WARN, P.STATE_WARN_BG
        elif any(k in low for k in ("run", "start", "inject", "push", "collect",
                                    "heat", "wash", "prefill", "실행", "진행", "수집", "주입")):
            fg, bg = P.STATE_RUN, P.STATE_RUN_BG
        else:
            fg, bg = P.TEXT_PRIMARY, "transparent"
        self.lbl_status.setStyleSheet(f"color: {fg}; background: {bg}; {base}")

    def _update_phase_label(self, name: str, pct: float):
        """Phase 진행률 + 경과 시간을 상태바 오른쪽에 표시.
        Phase가 바뀌면 elapsed 타이머 리셋, 매초 재표시는 _phase_tick으로 수행."""
        if not name or pct < 0:
            self.lbl_phase.setText("")
            if hasattr(self, "_phase_tick_timer") and self._phase_tick_timer:
                self._phase_tick_timer.stop()
            self._phase_start_ts = None
            self._phase_name = ""
            self._phase_last_pct = 0.0
            return

        if not hasattr(self, "_phase_tick_timer") or self._phase_tick_timer is None:
            self._phase_tick_timer = QTimer(self)
            self._phase_tick_timer.timeout.connect(self._render_phase_label)

        if name != getattr(self, "_phase_name", ""):
            self._phase_name = name
            self._phase_start_ts = time.time()
            self._phase_tick_timer.start(1000)

        self._phase_last_pct = pct
        self._render_phase_label()

    def _render_phase_label(self):
        """타이머 tick 또는 phase progress 갱신 시 실제 텍스트 재구성."""
        start = getattr(self, "_phase_start_ts", None)
        if start is None:
            return
        elapsed = int(time.time() - start)
        name = getattr(self, "_phase_name", "")
        pct = getattr(self, "_phase_last_pct", 0.0)
        self.lbl_phase.setText(f"{name}  {pct:.0f}%  {elapsed}s")
