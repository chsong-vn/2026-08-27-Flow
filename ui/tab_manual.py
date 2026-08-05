"""
Manual Tab - Feed Zone / Reactor Zone / Outlet Zone 3단 레이아웃
@codesyncer-decision: main.py에서 분리 - 매뉴얼 제어 UI 단일 책임

사용법:
    man_tab = ManualTab(app)
    tabs.addTab(man_tab, " MANUAL ")
"""

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
                             QLCDNumber, QScrollArea, QFrame, QGridLayout,
                             QButtonGroup, QSplitter, QSizePolicy,
                             QComboBox, QLineEdit, QToolButton, QCheckBox)
from PyQt5.QtCore import Qt, QTimer, QMetaObject, pyqtSlot
from PyQt5.QtGui import QColor
import re
import threading

from ui.widgets.pump_controls import PumpWidgetFactory, SamplerManualWidget, WashOpsWidget
from ui.widgets.channel_column import ChannelColumnCard
from ui.colors import DarkPalette as Dark, LightPalette as Light, T
from ui.toggle_switch import ToggleSwitch
from ui.theme import (get_main_control_button_style, get_primary_button_style,
                      get_secondary_button_style, get_action_state_style,
                      get_badge_style, get_segment_track_style,
                      get_segment_button_style, get_outlet_valve_button_style,
                      get_stop_button_style, get_caption_style)


class WashOpsBand(QWidget):
    """WashOpsWidget 아코디언 래퍼 — 기본 접힘.

    @codesyncer-decision: Feed 존이 채널당 카드 2장 적층으로 스크롤되던 문제(F8)
    해결 — 세척/수동 작업은 저빈도 작업이라 접어두고, 헤더 QToolButton 으로 펼침.
    반드시 _create_feed_cards_for_group 경유로 생성 (setup_ui/rebuild 대칭 유지).
    """

    def __init__(self, group_name, ops_widget, parent=None):
        super().__init__(parent)
        self._ops = ops_widget
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.btn_head = QToolButton()
        self.btn_head.setCheckable(True)
        self.btn_head.setChecked(False)
        self.btn_head.setArrowType(Qt.RightArrow)
        self.btn_head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_head.setText(f"세척 / 수동 작업 · {group_name}   (리필 · 세척 · 프라임)")
        self.btn_head.setMinimumHeight(26)
        self.btn_head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_head.toggled.connect(self._on_toggle)
        lay.addWidget(self.btn_head)

        self._ops.setVisible(False)
        lay.addWidget(self._ops)
        self.apply_theme(True)

    def _on_toggle(self, open_):
        self.btn_head.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self._ops.setVisible(open_)

    def apply_theme(self, is_dark=True):
        P = Dark if is_dark else Light
        self.btn_head.setStyleSheet(
            f"QToolButton {{ text-align: left; padding: 3px 10px; color: {P.TEXT_SECONDARY}; "
            f"background: transparent; border: 1px dashed {P.BORDER_PRIMARY}; "
            f"border-radius: {T.R_MD}; font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; }}"
            f"QToolButton:hover {{ color: {P.TEXT_PRIMARY}; border-color: {P.BORDER_LIGHT}; }}"
            f"QToolButton:checked {{ border-style: solid; background: {P.BG_SECONDARY}; }}")
        if hasattr(self._ops, "apply_theme"):
            self._ops.apply_theme(is_dark)

    def refresh_port_labels(self):
        if hasattr(self._ops, "refresh_port_labels"):
            self._ops.refresh_port_labels()


class ManualTab(QWidget):
    """매뉴얼 탭: Feed Zone / Reactor Zone / Outlet Zone 3단 제어"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pump_card_widgets = []
        self.setup_ui()

    def _pal(self):
        from ui.colors import LightPalette as Light
        return Dark if getattr(self.app, "is_dark_mode", True) else Light

    # ─── FlowStrip: 장비 배치 미니맵 ──────────────────────────────

    def _build_flow_strip(self, num_pumps):
        """액체 경로 순서의 실시간 상태 노드 스트립을 생성한다."""
        P = self._pal()
        self._fs_frame = QFrame()
        self._fs_frame.setStyleSheet(
            f"QFrame {{ background: {P.BG_MONITOR}; border: 1px solid {P.BORDER_SECONDARY}; "
            f"border-radius: {T.R_LG}; }}"
        )
        lay = QHBoxLayout(self._fs_frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        # \ub178\ub4dc \uc544\uc774\ucf58 \u2014 \ud504\ub85c\uc138\uc2a4 \ud750\ub984 \uc2a4\ud14c\uc774\uc9c0 (\uc544\uc774\ucf58 \uce69 = \uc561\uc13c\ud2b8 \ubc30\uacbd \ub77c\uc6b4\ub4dc)
        glyphs = {"feed": "\u2261", "reactor": "\u2668", "push": "\u2191",
                  "outlet": "\u22b3", "collector": "\u25a6"}

        def make_node(key, title):
            # \uce74\ub4dc\ud615 \ub178\ub4dc: [\uc544\uc774\ucf58 \uce69] + (\ud0c0\uc774\ud2c0 / \uac12)
            node = QFrame()
            node.setObjectName("FSNode")
            node.setCursor(Qt.PointingHandCursor)
            node.setToolTip("Click to focus this zone")
            h = QHBoxLayout(node)
            h.setContentsMargins(9, 5, 12, 5)
            h.setSpacing(9)
            g = QLabel(glyphs.get(key, "\u25cf"))
            g.setObjectName("FSIcon")
            g.setFixedSize(28, 28)
            g.setAlignment(Qt.AlignCenter)
            col = QVBoxLayout()
            col.setSpacing(0)
            t = QLabel(title)
            v = QLabel("--")
            col.addWidget(t)
            col.addWidget(v)
            h.addWidget(g)
            h.addLayout(col)
            node.mousePressEvent = lambda e, k=key: self._fs_node_clicked(k)
            return node, g, t, v

        def make_arrow():
            a = QLabel("\u203a")   # single chevron \u2014 \uc587\uace0 \uc815\ub3c8\ub41c \uc5f0\uacb0\uc790
            a.setAlignment(Qt.AlignCenter)
            return a

        self._fs_nodes = {}
        self._fs_glyphs = {}
        self._fs_frames = {}
        order = [("feed", "FEED"), ("reactor", "REACTOR")]
        if getattr(self.app, "push_pump", None) is not None:
            order.append(("push", "PUSH"))
        order += [("outlet", "OUTLET"), ("collector", "COLLECTOR")]

        self._fs_arrows = []
        for i, (key, title) in enumerate(order):
            w, g, t, v = make_node(key, title)
            self._fs_nodes[key] = (t, v)
            self._fs_glyphs[key] = g
            self._fs_frames[key] = w
            lay.addWidget(w)
            if i < len(order) - 1:
                ar = make_arrow()
                self._fs_arrows.append(ar)
                lay.addWidget(ar)
        lay.addStretch()

        # ── 전역 E-STOP (감사 2026-07-13): 계측 SW 필수 관례 — 상시 노출 비상정지.
        #   전 펌프(그룹/수동/푸시) 정지 + N2 0 + Outlet→WASTE + 시퀀스 abort.
        #   비상정지는 확인창 금지(즉시), 시리얼 일괄 호출은 데몬 스레드(GUI 무블록).
        #   채널별 STOP(적색 아웃라인)과 구분되는 '채움 적색' = HMI E-stop 관례.
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setMinimumHeight(40)
        self.btn_estop.setMinimumWidth(104)
        self.btn_estop.setCursor(Qt.PointingHandCursor)
        self.btn_estop.setToolTip(
            "비상 정지 — 모든 펌프 정지 · N2 차단 · Outlet→WASTE · 실행 중 시퀀스 중단")
        self.btn_estop.clicked.connect(self._estop_all)
        lay.addWidget(self.btn_estop)

        # 초기값
        self._fs_set("feed", f"{num_pumps} PUMPS")
        self._style_flow_strip()

        # 1.5초 주기 갱신 (rebuild 대비 기존 타이머 정리)
        if getattr(self, "_fs_timer", None):
            self._fs_timer.stop()
        self._fs_timer = QTimer(self)
        self._fs_timer.timeout.connect(self._update_flow_strip)
        self._fs_timer.start(1500)

        return self._fs_frame

    def _estop_all(self):
        """전역 비상정지 — 확인창 없이 즉시, 장비 일괄 안전상태로.

        범위: 그룹/수동/푸시 펌프 stop · MFC 0 sccm · 샘플러 emergency_stop ·
        Outlet→WASTE(1) · 엔진 abort_flag(실행 중 시퀀스 중단). 장비별 예외 격리."""
        app = self.app

        def _run():
            try:
                if getattr(app, "engine", None) is not None:
                    app.engine.abort_flag = True
            except Exception:
                pass
            for p in (getattr(app, "pumps", {}) or {}).values():
                try:
                    p.stop()
                except Exception:
                    pass
            for mp in (getattr(app, "manual_pumps", {}) or {}).values():
                try:
                    mp.stop()
                except Exception:
                    pass
            pp = getattr(app, "push_pump", None)
            if pp is not None:
                try:
                    pp.stop()
                except Exception:
                    pass
            m = getattr(app, "mfc", None)
            if m is not None:
                try:
                    m.set_flow(0.0)
                except Exception:
                    pass
            for sp in (getattr(app, "samplers", {}) or {}).values():
                try:
                    if hasattr(sp, "emergency_stop"):
                        sp.emergency_stop()
                    elif hasattr(sp, "stop"):
                        sp.stop()
                except Exception:
                    pass
            v = (getattr(app, "valves", {}) or {}).get("Outlet")
            if v is not None:
                try:
                    v.set_position(1)
                except Exception:
                    pass
            try:
                app.signals.sig_log.emit(
                    "[E-STOP] 비상정지 실행 — 전 펌프 정지 · N2 0 · Outlet WASTE"
                    + (" · 시퀀스 중단" if getattr(app, "engine", None) else ""))
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _fs_set(self, key, text, state="idle"):
        """노드 값 갱신 + 의미색 적용 (run=녹 / warn=황 / idle=중립)"""
        if key not in self._fs_nodes:
            return
        P = self._pal()
        color = {"run": P.STATE_RUN, "warn": P.STATE_WARN,
                 "fault": P.STATE_FAULT}.get(state, P.TEXT_PRIMARY)
        t, v = self._fs_nodes[key]
        v.setText(text)
        v.setStyleSheet(
            f"color: {color}; font-family: {T.FONT_MONO}; font-size: {T.FS_SM}; "
            f"font-weight: {T.FW_BOLD}; background: transparent; border: none;"
        )
        # 가동 상태면 아이콘 칩도 점등 (녹), 아니면 액센트
        g = self._fs_glyphs.get(key)
        if g is not None:
            chip = P.STATE_RUN if state == "run" else (
                P.STATE_WARN if state == "warn" else P.ACCENT_BLUE)
            g.setStyleSheet(
                f"QLabel#FSIcon {{ background: {chip}; color: #ffffff; "
                f"border-radius: 8px; font-size: {T.FS_MD}; font-weight: {T.FW_BOLD}; }}")

    def _style_flow_strip(self):
        """노드 카드/아이콘칩/타이틀/셰브론/프레임 팔레트 재적용 (테마 전환 대응)"""
        P = self._pal()
        if getattr(self, "btn_estop", None) is not None:
            # 채움 적색 = HMI E-stop 관례 (채널 STOP 아웃라인과 시각 구분)
            self.btn_estop.setStyleSheet(
                f"QPushButton {{ background: {P.ACCENT_RED}; color: white; "
                f"font-weight: {T.FW_BOLD}; font-size: {T.FS_SM}; "
                f"letter-spacing: 1px; border: none; border-radius: {T.R_MD}; "
                f"padding: 0 14px; }}"
                f"QPushButton:hover {{ background: #e5534b; }}"
                f"QPushButton:pressed {{ background: #b62324; }}")
        if getattr(self, "_fs_frame", None):
            self._fs_frame.setStyleSheet(
                f"QFrame {{ background: {P.BG_MONITOR}; border: 1px solid {P.BORDER_SECONDARY}; "
                f"border-radius: {T.R_LG}; }}"
                f"QFrame#FSNode {{ background: {P.BG_SECONDARY}; border: 1px solid "
                f"{P.BORDER_PRIMARY}; border-radius: {T.R_MD}; }}"
                f"QFrame#FSNode:hover {{ border-color: {P.ACCENT_BLUE}; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
        for t, _v in self._fs_nodes.values():
            t.setStyleSheet(
                f"color: {P.TEXT_SECONDARY}; font-size: {T.FS_XS}; "
                f"font-weight: {T.FW_SEMI}; letter-spacing: 1px; "
                f"background: transparent; border: none;"
            )
        for g in getattr(self, "_fs_glyphs", {}).values():
            g.setStyleSheet(
                f"QLabel#FSIcon {{ background: {P.ACCENT_BLUE}; color: #ffffff; "
                f"border-radius: 8px; font-size: {T.FS_MD}; font-weight: {T.FW_BOLD}; }}"
            )
        for a in getattr(self, "_fs_arrows", []):
            a.setStyleSheet(
                f"color: {P.TEXT_DISABLED}; font-size: {T.FS_XL}; font-weight: {T.FW_BOLD}; "
                f"background: transparent; border: none;"
            )

    def _fs_node_clicked(self, key):
        """플로우 노드 클릭 → 해당 제어 존으로 포커스/스크롤."""
        try:
            if key == "feed" and hasattr(self, "feed_scroll"):
                self.feed_scroll.verticalScrollBar().setValue(0)
                self.feed_scroll.setFocus()
            elif key in ("reactor", "push") and hasattr(self, "g_reactor"):
                self.g_reactor.setFocus()
            elif key in ("outlet", "collector") and hasattr(self, "g_outlet"):
                self.g_outlet.setFocus()
        except Exception:
            pass

    def _style_manual_buttons(self):
        """레퍼런스 버튼 언어(primary/secondary/segment/badge)를 매뉴얼탭 소유
        버튼에 일괄 적용. setup_ui 말미 + 테마 토글(apply_theme)에서 호출."""
        P = self._pal()
        if getattr(self, "lbl_feed_title", None) is not None:
            self.lbl_feed_title.setStyleSheet(
                f"font-size: {T.FS_LG}; font-weight: {T.FW_BOLD}; color: {P.TEXT_PRIMARY};")

        # 섹션 캡션 통일 규격 — 우레일 전 그룹 동일 서체/크기/자간
        cap = get_caption_style(P)
        for nm in ("lbl_heater", "lbl_push_flow", "lbl_valve", "lbl_collector",
                   "lbl_target", "lbl_phase_cap", "lbl_push_sub"):
            w = getattr(self, nm, None)
            if w is not None:
                w.setStyleSheet(cap)

        # N2 MFC 블록
        if getattr(self, "lbl_mfc_cap", None) is not None:
            self.lbl_mfc_cap.setStyleSheet(cap)
        if getattr(self, "btn_mfc_set", None) is not None:
            self.btn_mfc_set.setStyleSheet(get_primary_button_style(P))
        if getattr(self, "btn_mfc_off", None) is not None:
            self.btn_mfc_off.setStyleSheet(get_stop_button_style(P))
        if getattr(self, "lbl_mfc_act", None) is not None:
            self.lbl_mfc_act.setStyleSheet(get_badge_style(P, "info"))

        # Push 제어 (제어 유지 + 리스타일)
        if getattr(self, "btn_push_start", None) is not None:
            self.btn_push_start.setStyleSheet(get_primary_button_style(P))
        if getattr(self, "btn_push_stop", None) is not None:
            self.btn_push_stop.setStyleSheet(get_stop_button_style(P))
        if getattr(self, "lbl_push_pressure", None) is not None:
            self.lbl_push_pressure.setStyleSheet(get_badge_style(P, "info"))
        # Outlet Waste|Collect = 트랙 + 선택 pill
        if getattr(self, "outlet_track", None) is not None:
            self.outlet_track.setStyleSheet(get_segment_track_style(P))
        if getattr(self, "btn_waste", None) is not None:
            self.btn_waste.setStyleSheet(get_outlet_valve_button_style(P, "waste"))
        if getattr(self, "btn_coll", None) is not None:
            self.btn_coll.setStyleSheet(get_outlet_valve_button_style(P, "collection"))

        # ── 우레일 나머지: Thermal + Collect (inlet 외 '다른 부분') ──
        # primary=강조 실행 / secondary=보조 / stop=정지·끄기(슬레이트, 기존 통합 유지)
        prim = get_primary_button_style(P)
        sec = get_secondary_button_style(P)
        stop = get_action_state_style(P, "stop", False)  # 중립 — 정지가 혼자 안 튀게
        _primary = ("btn_h_set", "btn_collector_move", "btn_go_well")
        _secondary = ("btn_collector_back", "btn_collector_forward",
                      "btn_collector_home", "btn_plate96_view", "btn_home_p96",
                      "btn_wash_p96")
        for nm in _primary:
            b = getattr(self, nm, None)
            if b is not None:
                b.setStyleSheet(prim)
        for nm in _secondary:
            b = getattr(self, nm, None)
            if b is not None:
                b.setStyleSheet(sec)
        # 히터 OFF = 정지 계열(적색 아웃라인) — STOP 언어와 통일
        if getattr(self, "btn_h_off", None) is not None:
            self.btn_h_off.setStyleSheet(get_stop_button_style(P))
        if getattr(self, "sep_proc", None) is not None:
            self.sep_proc.setStyleSheet(
                f"background-color: {P.SEPARATOR}; border: none;")
        # Plate A/B = 세그먼트 pill(트랙 안), 체크형
        if getattr(self, "plate_track", None) is not None:
            self.plate_track.setStyleSheet(get_segment_track_style(P))
        for nm in ("btn_plate_a", "btn_plate_b"):
            b = getattr(self, nm, None)
            if b is not None:
                b.setStyleSheet(get_segment_button_style(P, "neutral"))

    def apply_theme(self, is_dark=True):
        """ThemeManager 훅 — 매뉴얼탭 소유 인라인 스타일을 현재 팔레트로 재적용.
        (채널 칼럼 카드는 theme_manager 가 pump_card_widgets 루프로 별도 처리)"""
        self._style_flow_strip()      # 플로우 스트립 + _style_readout(안전바 포함)
        self._style_manual_buttons()

    def showEvent(self, event):
        """첫 표시 시 우레일 스플리터를 실측 높이로 시드 —
        PROCESS = 필요분 우선(COLLECT 최소만), 스크롤 없이 기본 상태가 들어가게."""
        super().showEvent(event)
        if getattr(self, "_split_seeded", False) or getattr(self, "_right_col", None) is None:
            return
        self._split_seeded = True
        QTimer.singleShot(0, self._reseed_right_split)

    def _reseed_right_split(self):
        """우레일 스플리터 재배분 — PROCESS(스크롤 존)에 내용 필요분을 우선 배정하고
        COLLECT 는 자기 최소만. HTE 그리드 토글로 PROCESS 필요높이가 바뀔 때 재호출.
        @codesyncer-decision: COLLECT +12 버퍼 제거 — PROCESS 기본 내용(≈353px)이
        16px 초과로 스크롤바가 뜨며 SLUG PHASE 가 잘리던 문제(사용자 "여전히 겹치는데").
        총높이가 둘 다 못 담으면 PROCESS 는 스크롤(겹침 없음), COLLECT 최소 보장."""
        rc = getattr(self, "_right_col", None)
        if rc is None:
            return
        total = sum(rc.sizes()) or rc.height()
        if total <= 0:
            return
        coll_min = self.g_outlet.minimumSizeHint().height()
        if total <= coll_min + 240:
            return  # 공간 부족 — 스플리터 기본 배분 유지(스크롤이 겹침 방지)
        need = self._proc_scroll.widget().sizeHint().height() + 40  # 그룹박스 크롬
        proc = min(need, total - coll_min)          # 필요분, 단 COLLECT 최소 보장
        proc = max(proc, total - coll_min - 200)    # PROCESS 가 과도히 작아지지 않게
        rc.setSizes([int(proc), int(total - proc)])

    def _update_process_status(self):
        """그래프 대체 경량 상태 갱신 (1s): 히터 ON/OFF 배지 + 센서 액체/기체 배지."""
        app = self.app
        P = self._pal()
        # 히터 ON/OFF — target_temp>0 이면 가열 중(ON)
        if hasattr(self, "lbl_heater_state"):
            on = False
            try:
                on = bool(app.heater and float(getattr(app.heater, "target_temp", 0) or 0) > 0)
            except Exception:
                pass
            if on:
                self.lbl_heater_state.setText("● ON")
                col = P.STATE_RUN
            else:
                self.lbl_heater_state.setText("○ OFF")
                col = P.TEXT_DISABLED
            self.lbl_heater_state.setStyleSheet(
                f"color: {col}; font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")
        # 센서 위상 — app._last_sensor_phases(1: 액체 / 0: 기체 / 없음: --)
        badges = getattr(self, "_phase_badges", {})
        if badges:
            has = getattr(app, "phase_sensor", None) is not None
            if hasattr(self, "phase_status_row"):
                self.phase_status_row.setVisible(has)
            phases = getattr(app, "_last_sensor_phases", None) or {}
            for k, b in badges.items():
                v = phases.get(k)
                if v == 1:
                    b.setText("● 액체"); c = P.ACCENT_CYAN
                elif v == 0:
                    b.setText("○ 기체"); c = P.TEXT_SECONDARY
                else:
                    b.setText("○ --"); c = P.TEXT_DISABLED
                b.setStyleSheet(f"color: {c}; font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")

    # ── 위상센서 캘리브레이션 (Manual CAL 버튼) ──────────────────
    # @codesyncer-decision: 시작 시 자동 캘리 금지(사용자 결정 2026-08-01) — 부팅 시점엔
    #   튜브가 비어있다는 보장이 없고, 액체 상태 캘리는 분류 반전의 조용한 고장.
    #   캘리는 이 버튼(튜브 교체 시)뿐. 직후 GAS 판독 검증으로 반전 캘리를 즉시 잡는다.
    def _calibrate_phase_sensors(self):
        ps = getattr(self.app, "phase_sensor", None)
        if ps is None:
            return
        from PyQt5.QtWidgets import QMessageBox
        ret = QMessageBox.warning(
            self, "위상센서 캘리브레이션",
            "튜브가 완전히 비어(기체만) 있어야 합니다.\n\n"
            "액체가 있는 상태로 캘리브레이션하면 에러 없이\n"
            "액체/기체 분류가 반전된 채 동작합니다 (조용한 오동작).\n"
            "시퀀스 실행 중에는 절대 실행하지 마세요.\n\n"
            "빈 튜브 상태입니까?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.btn_phase_cal.setEnabled(False)
        self.btn_phase_cal.setText("...")

        def job():
            import time as _t
            results = {}
            try:
                for k in list(ps.sensors):
                    ps.calibrate(k)
                _t.sleep(1.5)   # OCB350 보드 자체 캘리 루틴(~1초) 대기
                for k in list(ps.sensors):
                    results[k] = ps.read_phase(k)
            except Exception as e:
                results["__error__"] = str(e)
            self._phase_cal_result = results
            QMetaObject.invokeMethod(self, "_phase_cal_done", Qt.QueuedConnection)
        threading.Thread(target=job, daemon=True).start()

    @pyqtSlot()
    def _phase_cal_done(self):
        """캘리 완료 (GUI 스레드) — 버튼 복원 + GAS 검증 결과 안내."""
        from PyQt5.QtWidgets import QMessageBox
        self.btn_phase_cal.setEnabled(True)
        self.btn_phase_cal.setText("CAL")
        res = dict(getattr(self, "_phase_cal_result", {}) or {})
        log = self.app.signals.sig_log.emit
        err = res.pop("__error__", None)
        if err:
            log(f"[PhaseSensor] 캘리브레이션 실패: {err}")
            QMessageBox.critical(self, "위상센서", f"캘리브레이션 실패:\n{err}")
            return
        bad = {k: v for k, v in res.items() if v != "GAS"}
        if bad:
            log(f"[PhaseSensor] 캘리 직후 검증 실패 — GAS 아님: {bad}")
            QMessageBox.warning(
                self, "위상센서",
                "캘리브레이션 직후 판독이 GAS 가 아닙니다:\n"
                + "\n".join(f"  {k}: {v}" for k, v in bad.items())
                + "\n\n튜브에 액체가 남아 있었을 가능성이 큽니다.\n"
                  "튜브를 비운 뒤(N2 퍼지) 다시 캘리브레이션하세요.")
        else:
            log(f"[PhaseSensor] 캘리브레이션 완료 + GAS 검증 OK "
                f"({', '.join(res) or '-'})")

    def _update_flow_strip(self):
        """장비 실시간 상태 → 노드 반영 (라벨 미러링: 추가 시리얼 통신 없음)"""
        app = self.app
        try:
            self._style_flow_strip()  # 테마 전환 추종
            # Reactor: 모니터링이 갱신하는 현재온도 라벨 미러링
            temp_txt = self.lbl_reactor_temp.text() if hasattr(self, "lbl_reactor_temp") else "--"
            heating = False
            try:
                heating = bool(app.heater and float(getattr(app.heater, "target_temp", 0) or 0) > 30)
            except Exception:
                pass
            self._fs_set("reactor", temp_txt, "warn" if heating else "idle")

            # Push pump 압력 미러링
            if "push" in self._fs_nodes and hasattr(self, "lbl_push_pressure"):
                running = bool(getattr(getattr(app, "push_pump", None), "running", False))
                self._fs_set("push", self.lbl_push_pressure.text(),
                             "run" if running else "idle")

            # Outlet 밸브 방향
            outlet = app.valves.get("Outlet") if getattr(app, "valves", None) else None
            pos = getattr(outlet, "position", None)
            if pos == 2:
                self._fs_set("outlet", "COLLECT", "run")
            elif pos == 1:
                self._fs_set("outlet", "WASTE")
            else:
                self._fs_set("outlet", "--")

            # Collector 위치
            col = getattr(app, "collector", None)
            if col and hasattr(col, "get_position"):
                p = col.get_position()
                if p == 0:
                    self._fs_set("collector", "HOME")
                else:
                    wid = ""
                    if hasattr(col, "get_well_id"):
                        try:
                            w = col.get_well_id(p)
                            if w and w not in ("?", "HOME"):
                                wid = f" {w}"
                        except Exception:
                            pass
                    self._fs_set("collector", f"Tube {p}{wid}")
            else:
                self._fs_set("collector", "--")

            # Feed: 가동 중 펌프 수
            pumps = [app.pumps[n] for n in app.cfg.ACTIVE_PUMPS if n in app.pumps]
            n_run = sum(1 for p in pumps if getattr(p, "running", False))
            if n_run > 0:
                self._fs_set("feed", f"{n_run}/{len(pumps)} RUN", "run")
            else:
                self._fs_set("feed", f"{len(pumps)} PUMPS")

            if hasattr(self, "lbl_mfc_act"):
                _m = getattr(app, "mfc", None)
                if _m is not None:
                    self.lbl_mfc_act.setText(f"{float(getattr(_m, '_sp', 0.0)):.1f} sccm")
            # 채널 칼럼 카드 상태 미러 (Vol/상태 도트 — 추가 시리얼 없음)
            for w in getattr(self, "pump_card_widgets", []):
                if isinstance(w, ChannelColumnCard):
                    w.refresh_status()
        except Exception:
            pass

    def _inlet_provider(self, p_name):
        """포트→시약맵 정보 콜백 — StepCard 와 동일한 map_mgr.get_inlet 재사용."""
        def prov(port):
            mm = getattr(self.app, 'map_mgr', None)
            try:
                return mm.get_inlet(p_name, port) if mm else None
            except Exception:
                return None
        return prov

    def _create_feed_cards_for_group(self, p_name):
        """시퀀스 그룹 1개의 Feed 칼럼 카드 (Manual v3 — 세로 칼럼).

        세척/수동 작업(WashOps)은 카드의 ⚙ 팝업으로 이동 — 칼럼이 홀쭉해지고
        아코디언이 사라진다. 반환은 [ChannelColumnCard] 1장 (헬퍼 대칭 유지).
        """
        app = self.app
        p_obj = app.pumps.get(p_name)
        routing = getattr(app.cfg, "PUMP_ROUTING", {}).get(p_name, "external_valve")
        provider = self._inlet_provider(p_name)
        # autosampler 그룹: hw_manager 가 해석한 일체형 샘플러
        sampler_obj = (getattr(getattr(app, "hw_mgr", None),
                               "sampler_by_pump", {}) or {}).get(p_name)

        # ⚙ 세척/수동 작업 — 스마트펌프에만 (덕타이핑). 팝업에서 새로 생성.
        wash_factory = None
        if all(hasattr(p_obj, m) for m in ("refill", "wash_cycle", "prime_reactor")):
            def wash_factory(pn=p_name, po=p_obj, rt=routing, so=sampler_obj):
                return WashOpsWidget(pn, po, routing=rt, inlet_provider=provider,
                                     sampler=so)

        card = ChannelColumnCard(
            p_name, p_obj,
            selector_obj=app.valves.get(f"{p_name}_Selector"),
            switcher_obj=app.valves.get(f"{p_name}_Switcher"),
            sampler_obj=sampler_obj,
            routing=routing,
            inlet_provider=provider,
            wash_ops_factory=wash_factory,
        )
        return [card]

    @staticmethod
    def _mark_offline(w, obj, name):
        """미연결 장비 카드 = 표시하되 정직하게 — OFFLINE 표기 + 제어 비활성.

        @codesyncer(2026-07-13, 사용자 "연결 안 됐는데 왜 제어에 있냐"): 카드는
        hardware_config 의 역할(manual_pumps/samplers)에 등록돼 있어 뜨는 것 —
        구성은 보이는 게 맞지만(전문 계측 SW 관례: 숨기지 않고 OFFLINE), 기존엔
        연결 실패(_safe_connect 무시) 후에도 버튼이 살아 있어 '제어되는 척' 했다.
        가상(Mock) 드라이버는 is_connected=True 라 영향 없음(의도된 시뮬).
        재연결 = 설정 저장(핫리로드)/앱 재시작 시 카드 재생성으로 자동 반영."""
        if getattr(obj, "is_connected", True):
            return w
        try:
            if hasattr(w, "lbl_title"):
                w.lbl_title.setText(w.lbl_title.text() + "  · OFFLINE")
            w.setEnabled(False)
            w.setToolTip(
                f"{name}: 연결 안 됨 (포트/전원 확인).\n"
                "hardware_config 역할에 등록되어 있어 표시됩니다 — 사용하지 않으면 "
                "설정에서 역할을 해제하세요.\n"
                "재연결은 설정 저장(핫리로드) 또는 앱 재시작 시 다시 시도됩니다.")
        except Exception:
            pass
        return w

    def _create_robochem_cards(self):
        """RoboChem 수동 장비 카드 (roles.manual_pumps / 미배정 samplers).

        일체형: 그룹에 배정된 샘플러(sampler_by_pump)는 채널 카드에 이미
        통합되므로 여기서 중복 카드를 만들지 않는다.
        """
        from ui.widgets.channel_column import CARD_W
        app = self.app
        cards = []
        for mp_name, mp_obj in (getattr(app, 'manual_pumps', {}) or {}).items():
            # @codesyncer-decision: IntegratedChannelGroup(부유 타이틀 그룹박스)
            #   래핑 제거 — 채널 칼럼과 상단선이 어긋나고 타이틀이 이중이던 원인.
            #   모터 카드 단독 + 내부 타이틀에 장비명 표기, 그리드 균일폭.
            w = PumpWidgetFactory.create_motor(mp_obj)
            if hasattr(w, "lbl_title"):
                w.lbl_title.setText(f"{mp_name}  (MANUAL)")
            # 채널 카드와 동일 규격: 균일폭 + '내용 높이'(상단 정렬용)
            w.setMinimumWidth(CARD_W)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            cards.append(self._mark_offline(w, mp_obj, mp_name))
        bound = set((getattr(getattr(app, "hw_mgr", None), "sampler_by_pump", {})
                     or {}).values())
        for sp_name, sp_obj in (getattr(app, 'samplers', {}) or {}).items():
            if sp_obj in bound:
                continue   # 그룹 채널 카드에 이미 통합됨
            w = SamplerManualWidget(sp_name, sp_obj)
            # 채널 카드와 동일 규격: 균일폭 + '내용 높이'(상단 정렬용)
            w.setMinimumWidth(CARD_W)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            cards.append(self._mark_offline(w, sp_obj, sp_name))
        return cards

    def setup_ui(self):
        # @codesyncer-decision: Manual Tab을 Left-to-Right Process Flow로 재설계
        # @codesyncer-context: Feed Zone → Reactor Zone → Outlet Zone (액체 흐름 순서)
        """Manual Tab - 3단 Left-to-Right Process Flow"""
        if self.layout():
            QWidget().setLayout(self.layout())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        main_layout.setSpacing(T.SP_SM)

        app = self.app

        # 상단 헤더: 장비 배치 미니맵 (FlowStrip) + 연결 요약
        # @codesyncer-decision: 액체의 물리 경로를 따라가는 실시간 상태 스트립
        # - Feed(펌프) → Reactor(온도) → [Push(압력)] → Outlet(방향) → Collector(위치)
        # - 3존 패널은 '제어'를, FlowStrip은 '현재 장비 배치/상태'를 담당
        #   → 어떤 존을 조작하든 전체 시스템이 지금 어떤 상태인지 항상 보임
        header = QHBoxLayout()
        header.setSpacing(10)

        num_pumps = len([p for p in app.cfg.ACTIVE_PUMPS if p in app.pumps])
        num_valves = len([v for v in app.valves.keys() if v != "Outlet"])
        has_heater = 1 if app.heater and type(app.heater).__name__ != "MockHeater" else 0

        header.addWidget(self._build_flow_strip(num_pumps), 1)

        self.lbl_manual_status = QLabel(
            f"Pumps {num_pumps} · Valves {num_valves} · Heater {has_heater}")
        self.lbl_manual_status.setStyleSheet(f"font-size: {T.FS_XS};")
        self.lbl_manual_status.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        header.addWidget(self.lbl_manual_status, 0)
        main_layout.addLayout(header)

        # ========================================
        # 존 구성: Feed(좌) | 우측 세로스택(Process 위 / Collect 아래)
        # @codesyncer-decision: 반쯤 비던 우측 2개 존을 세로 1열로 접어
        #   addStretch 빈 공간을 구조적으로 제거 (3안 심사 채택안)
        # ========================================

        # --- [Zone 1] Feed Channels (좌측, 채널 = 세로 칼럼) ---
        # @codesyncer-decision: Manual v3 — 채널을 가로 행이 아니라 세로 칼럼으로
        #   나란히 (GROUP A/B/C 기둥). 채널 간 상태 비교가 한눈에 됨.
        g_feed = QGroupBox()
        g_feed.setObjectName("ZoneCard")
        g_feed.setMinimumWidth(640)
        g_feed.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        feed_layout = QVBoxLayout()
        feed_layout.setContentsMargins(T.SP_SM, T.SP_MD, T.SP_SM, T.SP_SM)
        feed_layout.setSpacing(T.SP_SM)

        # Feed Channels 헤더 + 모두 토출 / 모두 정지
        # @codesyncer-decision: 모두 토출/모두 정지(임의 추가분) 폐기 — 개별 채널
        #   버튼으로 충분, 전체 정지는 상단 글로벌 STOP 이 담당. 헤더는 타이틀만.
        feed_head = QHBoxLayout()
        self.lbl_feed_title = QLabel("Feed Channels")
        self.lbl_feed_title.setStyleSheet(f"font-size: {T.FS_LG}; font-weight: {T.FW_BOLD};")
        feed_head.addWidget(self.lbl_feed_title)
        feed_head.addStretch()
        feed_layout.addLayout(feed_head)

        # 펌프 제어 스크롤 영역 — 세로 스크롤(줄바꿈 후 넘칠 때만)
        # @codesyncer-decision: 가로 스크롤(전 채널 한눈에 못 봄) → FlowLayout wrap +
        #   세로 스크롤. 전 채널이 폭에 맞춰 여러 줄로 배치돼 한 화면에 보인다.
        self.feed_scroll = QScrollArea()
        self.feed_scroll.setWidgetResizable(True)
        # 맥시마이즈 시 4열이 다 들어가 가로 스크롤 없음. 좁은 창에선 안전망으로 AsNeeded.
        self.feed_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.feed_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.feed_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        feed_scroll_widget = QWidget()
        feed_scroll_widget.setStyleSheet("background: transparent;")

        # @codesyncer-decision: 채널 카드를 N열 그리드로 wrap (4열: 그룹 A~D 한 줄,
        #   NRG/SAMPLER 다음 줄) → 전 채널 한눈에, 가로 스크롤 없음.
        feed_scroll_layout = QGridLayout(feed_scroll_widget)
        feed_scroll_layout.setHorizontalSpacing(T.SP_SM)
        feed_scroll_layout.setVerticalSpacing(T.SP_SM)
        feed_scroll_layout.setContentsMargins(2, 2, 2, 2)
        self._feed_ncols = 4
        # 4열 균등 확장 → 카드가 폭을 나눠 채움(우측 빈공간 제거). 카드 max 로 과확대 방지.
        for _c in range(self._feed_ncols):
            feed_scroll_layout.setColumnStretch(_c, 1)

        # 펌프 카드 + 그룹별 세척/수동 작업 패널 + RoboChem 수동 장비 카드
        # @codesyncer-decision: setup 과 rebuild(핫리로드)가 같은 헬퍼를 사용 —
        #   기존엔 rebuild 가 manual_pumps/샘플러 카드를 삭제 후 재생성하지 않아
        #   핫리로드 시 카드가 사라지는 결함이 있었음.
        # @codesyncer-decision: 카드별 AlignTop 명시 — QHBoxLayout 에서 세로
        #   sizePolicy Maximum 인 짧은 카드는 (레이아웃 정렬이 아닌) 아이템
        #   정렬이 없으면 tall 카드 옆에서 수직 중앙에 떠 보임(사용자 지적).
        self.pump_card_widgets = []
        self._place_feed_cards(feed_scroll_layout)

        self.feed_scroll.setWidget(feed_scroll_widget)
        feed_layout.addWidget(self.feed_scroll)
        g_feed.setLayout(feed_layout)

        # --- [Zone 2] Process Zone (우측 상단) ---
        # @codesyncer-decision: "② PROCESS — 반응기 히터 · Push(HPLC)" 식
        #   번호·나열 타이틀은 전문장비 관례에 어긋남(사용자 지적) → 단어 하나.
        g_reactor = QGroupBox("PROCESS")
        g_reactor.setObjectName("ZoneCard")
        g_reactor.setMinimumWidth(320)
        g_reactor.setMinimumHeight(340)
        g_reactor.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.g_reactor = g_reactor
        reactor_layout = QVBoxLayout()
        reactor_layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        reactor_layout.setSpacing(4)

        # ── 상단 2열: REACTOR HEATER(좌) │ PUSH PUMP(우) ──────────────
        # @codesyncer-decision: 세로 적층 → 가로 2열 (공간배치 계획, 2026-07-06)
        #   PROCESS 존 최소높이 ~490px 가 스플리터 배분을 초과해 차트/Push 가
        #   잘리던 근본 원인. 히터·Push 는 같은 구조(캡션+칩/입력/버튼행)라
        #   나란히 두면 수직 예산 ~100px 이 차트로 돌아간다.
        proc_row = QHBoxLayout()
        proc_row.setSpacing(T.SP_MD)

        heater_col = QVBoxLayout()
        # @codesyncer(높이 정렬): PUSH PUMP 열(pp_layout)은 마진 0, 히터 열은 QVBoxLayout
        #   기본 마진(~11px)이라 헤드/입력/버튼 행이 전부 아래로 밀렸음 — 마진 통일.
        heater_col.setContentsMargins(0, 0, 0, 0)
        heater_col.setSpacing(6)
        heater_head = QHBoxLayout()
        heater_head.setSpacing(T.SP_SM)
        # @codesyncer-decision: 타이틀 "REACTOR HEATER"→"HEATER" — 반폭 컬럼에서
        #   타이틀+ACT칩+ON배지가 겹쳐 "REACTOR I…"로 잘리던 문제. ACT 칩이 이미
        #   반응기 온도임을 함의하므로 단어 하나로 축약(사용자 "여전히 겹치는데").
        self.lbl_heater = QLabel("HEATER")
        heater_head.addWidget(self.lbl_heater)
        heater_head.addStretch()

        # ACT 칩 — 속성명(lbl_reactor_temp 등)은 FlowStrip 미러링 계약상 유지
        self.monitor_frame = QFrame()
        # 칩 최소폭 — mono bold "250.0 C" 가 잘리지 않게(반폭 컬럼서 압축돼 "AC 0."
        #   으로 잘리던 문제, 사용자 "여전히 겹치는데").
        self.monitor_frame.setMinimumWidth(102)
        # @codesyncer(줄 정렬): 히터 헤드(칩 배지)가 PUSH PUMP 헤드(평문 라벨)보다
        #   높아 두 열의 입력/버튼 행이 어긋났음 — 헤드 행 높이를 26px 로 통일.
        self.monitor_frame.setFixedHeight(26)
        mf = QHBoxLayout(self.monitor_frame)
        mf.setContentsMargins(8, 1, 8, 1)
        mf.setSpacing(5)
        # @codesyncer-decision: 칩 안 "ACT" 프리픽스 숨김 — mono bold 값과 함께면
        #   "ACT 25.0°C" 가 반폭 컬럼 폭을 초과해 잘림. 칩=현재온도 값만, 상태는
        #   옆 ON/OFF 배지가 표시(사용자 "간단하게 현재온도와 on off여부").
        #   속성은 유지 — main_window_ui/theme_manager 안전참조(getattr/w()) 계약.
        self.lbl_current_temp_title = QLabel("ACT", self.monitor_frame)
        self.lbl_current_temp_title.hide()
        self.lbl_reactor_temp = QLabel("25.0 °C")
        mf.addWidget(self.lbl_reactor_temp)
        heater_head.addWidget(self.monitor_frame)
        # 히터 ON/OFF 상태 배지 (그래프 대신 — 사용자 요청) — 1s 타이머로 갱신
        self.lbl_heater_state = QLabel("○ OFF")
        self.lbl_heater_state.setFixedHeight(26)   # 헤드 행 높이 통일 (PUSH PUMP 열과 정렬)
        heater_head.addWidget(self.lbl_heater_state)
        heater_col.addLayout(heater_head)

        # 입력 전폭 + [SET | OFF] 와이드 행 (입력 옆 버튼 배치 지양 — 포인팅 정확도)
        # @codesyncer(사용자 "부제·부연설명 정리"): 단위 괄호 제거 — 입력칸 suffix
        #   가 이미 "°C" 표시(중복 부연설명). 전문 UI 관례(단위는 입력에 1회).
        self.lbl_target = QLabel("Set Temp")

        self.sh = QDoubleSpinBox()
        self.sh.setRange(0.0, 250.0)
        self.sh.setValue(25.0)
        self.sh.setSuffix(" °C")
        self.sh.setMinimumHeight(32)

        self.btn_h_set = QPushButton("SET")
        self.btn_h_set.setMinimumHeight(T.H_BTN)
        self.btn_h_set.clicked.connect(lambda: app.heater.set_temperature(self.sh.value()) if app.heater else None)

        self.btn_h_off = QPushButton("OFF")
        self.btn_h_off.setMinimumHeight(T.H_BTN)
        self.btn_h_off.clicked.connect(lambda: app.heater.stop() if app.heater else None)

        heater_col.addWidget(self.lbl_target)
        heater_col.addWidget(self.sh)
        heater_btns = QHBoxLayout()
        heater_btns.setSpacing(T.SP_SM)
        heater_btns.addWidget(self.btn_h_set, 1)
        heater_btns.addWidget(self.btn_h_off, 1)
        heater_col.addLayout(heater_btns)

        heater_holder = QWidget()
        heater_holder.setStyleSheet("background: transparent;")
        heater_holder.setLayout(heater_col)
        # @codesyncer-decision: 후행 addStretch 제거(proc_row 높이 41px 비대→기본
        #   상태 스크롤 유발) 대신 AlignTop 으로 두 열 상단 정렬 — 컴팩트+정렬 양립.
        proc_row.addWidget(heater_holder, 1, Qt.AlignTop)

        # 열 구분선
        self.sep_proc = QFrame()
        self.sep_proc.setFrameShape(QFrame.VLine)
        self.sep_proc.setFixedWidth(1)
        proc_row.addWidget(self.sep_proc)

        # Push Pump (HPLC) — 우열. push_pump 없으면 자동 숨김(히터가 전폭)
        self._setup_push_pump_section(proc_row)
        proc_row.setStretch(0, 1)
        proc_row.setStretch(2, 1)
        reactor_layout.addLayout(proc_row)

        # ── N2 MFC 블록 (roles.gas 배정 시에만 표시 — push 패턴) ──
        self.mfc_group = QWidget()
        self.mfc_group.setStyleSheet("background: transparent;")
        mg = QVBoxLayout(self.mfc_group)
        mg.setContentsMargins(0, T.SP_SM, 0, 0)
        mg.setSpacing(6)
        mfc_head = QHBoxLayout()
        mfc_head.setSpacing(T.SP_SM)
        self.lbl_mfc_cap = QLabel("N2 MFC")
        mfc_head.addWidget(self.lbl_mfc_cap)
        mfc_head.addStretch()
        self.lbl_mfc_act_title = QLabel("SET")
        self.lbl_mfc_act_title.setStyleSheet(
            f"font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; letter-spacing: 1px;")
        self.lbl_mfc_act = QLabel("0.0 sccm")
        mfc_head.addWidget(self.lbl_mfc_act_title)
        mfc_head.addWidget(self.lbl_mfc_act)
        mg.addLayout(mfc_head)
        mfc_row = QHBoxLayout()
        mfc_row.setSpacing(T.SP_SM)
        self.sp_mfc = QDoubleSpinBox()
        self.sp_mfc.setRange(0.0, float(getattr(getattr(app, "mfc", None),
                                                "max_sccm", 100.0) or 100.0))
        self.sp_mfc.setDecimals(1)
        self.sp_mfc.setSuffix(" sccm")
        self.sp_mfc.setMinimumHeight(32)
        self.btn_mfc_set = QPushButton("SET")
        self.btn_mfc_set.setMinimumHeight(T.H_BTN)
        self.btn_mfc_set.setCursor(Qt.PointingHandCursor)
        self.btn_mfc_set.clicked.connect(self._mfc_set)
        self.btn_mfc_off = QPushButton("OFF")
        self.btn_mfc_off.setMinimumHeight(T.H_BTN)
        self.btn_mfc_off.setCursor(Qt.PointingHandCursor)
        self.btn_mfc_off.clicked.connect(self._mfc_off)
        mfc_row.addWidget(self.sp_mfc, 1)
        mfc_row.addWidget(self.btn_mfc_set)
        mfc_row.addWidget(self.btn_mfc_off)
        mg.addLayout(mfc_row)

        # ── HTE droplet 파라미터 (Manual 에서 설정 → 엔진 HTE 가 그대로 사용) ──
        # @codesyncer-decision: HTE 시뮬 다이얼로그 폐기 후, droplet 파라미터를
        #   MFC 카드에 통합. 여기 값이 system_params 에 저장돼 _run_hte_droplet 이
        #   읽는다(스페이서/세척 부피·유량). "Manual 설정 = HTE 실행값" 단일 소스.
        _sp0 = (app.cfg.config_data.get("system_params", {})
                if hasattr(app, "cfg") else {})
        # @codesyncer: HTE 모드 체크박스 → 슬라이딩 스위치(사용자 요청, 다크모드
        #   토글과 동일 패턴). icon_mode="plain"(초록=ON). 라벨 + 우측 스위치 행.
        #   isChecked()/toggled(bool) 계약은 QCheckBox 와 동일 → 참조부 무변경.
        hte_head = QHBoxLayout()
        hte_head.setSpacing(T.SP_SM)
        # 부연설명 "(질소 스페이서)" → 툴팁으로 이동(라벨 간결화, 사용자 요청).
        self.lbl_hte = QLabel("HTE Droplet Mode")
        self.lbl_hte.setToolTip(
            "질소(N₂) 스페이서로 슬러그를 분리해 한 반응기에서 여러 실험을\n"
            "연속 진행하는 드롭릿 모드. ON 시 아래 파라미터가 펼쳐집니다.")
        hte_head.addWidget(self.lbl_hte)
        hte_head.addStretch()
        self.chk_hte = ToggleSwitch(checked=bool(_sp0.get("hte_mode", False)),
                                    width=46, height=24, icon_mode="plain")
        self.chk_hte.toggled.connect(self._save_hte_params)
        self.chk_hte.toggled.connect(self._toggle_hte_params)
        hte_head.addWidget(self.chk_hte)
        mg.addLayout(hte_head)

        # @codesyncer-decision: HTE 5-파라미터 그리드는 모드 ON 일 때만 표시(접이식).
        #   항상 펼쳐두면 좁은 PROCESS 존에서 mfc_group 이 높이 부족→그리드 압축→
        #   라벨↔입력 겹침(사용자 "여전히 겹치는데"). 전문 장비 관례상 모드 전용
        #   파라미터는 모드 활성 전까지 숨김. 기본 OFF → 숨김 → 겹침 원천 제거.
        self.hte_param_box = QWidget()
        self.hte_param_box.setStyleSheet("background: transparent;")
        hte_grid = QGridLayout(self.hte_param_box)
        hte_grid.setContentsMargins(0, 0, 0, 0)
        hte_grid.setSpacing(T.SP_SM)
        self._hte_params = {}   # key -> spinbox

        def _hte_spin(r, c, label, key, default, mx, dec=2, suffix=" mL"):
            hte_grid.addWidget(QLabel(label), r, c * 2)
            w = QDoubleSpinBox()
            w.setRange(0.0, mx); w.setDecimals(dec); w.setSuffix(suffix)
            w.setValue(float(_sp0.get(key, default) or default))
            w.setMinimumHeight(T.H_INPUT_SM)
            w.editingFinished.connect(self._save_hte_params)
            hte_grid.addWidget(w, r, c * 2 + 1)
            self._hte_params[key] = w
            return w

        _hte_spin(0, 0, "Spacer", "hte_spacer_vol_ml", 0.2, 50.0)
        _hte_spin(0, 1, "Gas", "hte_gas_sccm", 20.0, 500.0, 1, " sccm")
        _hte_spin(1, 0, "Wash sol", "hte_wash_solvent_vol_ml", 0.5, 50.0)
        _hte_spin(1, 1, "Wash N2", "hte_wash_gas_vol_ml", 0.3, 50.0)
        # 슬러그간 세척(0=끔): [슬러그|N2|용매|N2|슬러그] — 교차오염→희석 강등
        _iw = _hte_spin(2, 0, "Inter-wash", "hte_interwash_vol_ml", 0.0, 50.0)
        _iw.setToolTip("슬러그 사이 [세척용매 + N2] 삽입 부피 (0 = 끔).\n"
                       "제품 슬러그의 이웃이 전부 N2/깨끗한 용매가 되어\n"
                       "타이밍 오차 피해가 교차오염 대신 희석으로 강등됩니다.")
        mg.addWidget(self.hte_param_box)
        self.hte_param_box.setVisible(self.chk_hte.isChecked())

        reactor_layout.addWidget(self.mfc_group)
        self.sync_mfc_section()

        # @codesyncer(사용자 요청): TEMPERATURE TREND 차트 제거 — 좁은 PROCESS 존에서
        #   REACTOR/HTE 텍스트와 겹쳐 지저분했음. 현재온도는 이미 ACT 칩(lbl_reactor_temp),
        #   히터 ON/OFF 는 lbl_heater_state 배지가 대체. 센서는 그래프 없이 '액체/기체' 배지만.
        self._temp_plot = None
        self._temp_curve = None
        self._temp_setline = None

        # ── 슬러그 위상센서 상태 (액체/기체 배지만 — 그래프 없음) ──
        #   app.phase_sensor 있을 때만 노출. app._last_sensor_phases(1Hz 캐시) 미러.
        self.phase_status_row = QWidget()
        self.phase_status_row.setStyleSheet("background: transparent;")
        _ps_l = QHBoxLayout(self.phase_status_row)
        _ps_l.setContentsMargins(0, 4, 0, 0)
        _ps_l.setSpacing(T.SP_MD)
        self.lbl_phase_cap = QLabel("SLUG PHASE")
        self.lbl_phase_cap.setStyleSheet(
            f"font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; letter-spacing: 1px;")
        _ps_l.addWidget(self.lbl_phase_cap)
        self._phase_badges = {}   # 논리명 -> QLabel
        _ps_keys = list(getattr(getattr(app, "phase_sensor", None), "sensors", {}) or {})
        _lbl_map = {"collect": "COLLECT", "reactor_in": "INLET"}
        for _k in (_ps_keys or ["collect"]):
            b = QLabel("○ --")
            self._phase_badges[_k] = b
            cap = QLabel(_lbl_map.get(_k, _k[:6].upper()))
            cap.setStyleSheet(f"font-size: {T.FS_XS}; color: {Dark.TEXT_TERTIARY};")
            _ps_l.addWidget(cap)
            _ps_l.addWidget(b)
        # @codesyncer-decision: 위상센서 CAL 버튼 — 캘리는 자동 실행이 어디에도 없고
        #   명시 명령(S{10*id+3} → Cal핀 100ms LOW 펄스)뿐. OCB350 보드가 값을 비휘발
        #   저장하므로 튜브 교체/재클램핑 때만 누르면 된다. ★빈 튜브(기체) 필수 —
        #   액체 상태 캘리는 에러 없이 분류가 반전되는 조용한 고장이라
        #   확인 다이얼로그 + 캘리 직후 GAS 판독 검증을 강제한다.
        self.btn_phase_cal = QPushButton("CAL")
        self.btn_phase_cal.setFixedHeight(24)
        self.btn_phase_cal.setMinimumWidth(48)
        self.btn_phase_cal.setToolTip(
            "위상센서 캘리브레이션 — 빈 튜브(기체만) 상태에서만 실행하세요.\n"
            "튜브 교체/재클램핑 후 1회면 충분 (OCB350 보드가 전원 꺼도 값 유지).")
        self.btn_phase_cal.clicked.connect(self._calibrate_phase_sensors)
        _ps_l.addWidget(self.btn_phase_cal)
        _ps_l.addStretch()
        reactor_layout.addWidget(self.phase_status_row)
        self.phase_status_row.setVisible(getattr(app, "phase_sensor", None) is not None)
        reactor_layout.addStretch(1)

        # 히터 ON/OFF + 센서 위상 배지 갱신 (1s) — 그래프 대체 경량 상태 타이머
        self._proc_status_timer = QTimer(self)
        self._proc_status_timer.timeout.connect(self._update_process_status)
        self._proc_status_timer.start(1000)
        self._update_process_status()

        # (Push Pump 는 위 proc_row 우열에 배치됨 — 세로 적층 폐기)

        # @codesyncer-decision: 대형 리드아웃 타일 제거 — ACT 칩/압력 배지와 정보
        #   중복 + 좁은 우레일에서 차트·Push 패널과 겹쳐 그려지던 원인(사용자
        #   스크린샷).

        # @codesyncer-decision: PROCESS 존 내용을 QScrollArea 로 래핑 — HTE 모드 ON 시
        #   5-파라미터 그리드가 추가되면 스플리터 고정 높이를 초과해 그리드가 압축→
        #   라벨 겹침(사용자 "여전히 겹치는데"). 스크롤 래퍼는 창 크기·모드 무관하게
        #   내용이 넘치면 세로 스크롤바만 띄우고 절대 겹치지 않게 보장. 기본(HTE OFF)
        #   은 전부 들어가 스크롤바 미표시. 가로 스크롤은 끔(폭은 fit).
        proc_content = QWidget()
        proc_content.setObjectName("ProcScrollBody")
        proc_content.setStyleSheet("#ProcScrollBody { background: transparent; }")
        proc_content.setLayout(reactor_layout)
        self._proc_scroll = QScrollArea()
        self._proc_scroll.setWidgetResizable(True)
        self._proc_scroll.setFrameShape(QFrame.NoFrame)
        self._proc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._proc_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._proc_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._proc_scroll.setWidget(proc_content)
        _gr_wrap = QVBoxLayout(g_reactor)
        _gr_wrap.setContentsMargins(0, 0, 0, 0)
        _gr_wrap.addWidget(self._proc_scroll)

        # --- [Zone 3] Collect Zone (우측 하단) ---
        g_outlet = QGroupBox("COLLECT")
        g_outlet.setObjectName("ZoneCard")
        g_outlet.setMinimumWidth(320)
        g_outlet.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.g_outlet = g_outlet
        outlet_layout = QVBoxLayout()
        outlet_layout.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        outlet_layout.setSpacing(T.SP_SM)

        # Waste / Collect 대형 토글
        self.lbl_valve = QLabel("OUTLET VALVE")
        outlet_layout.addWidget(self.lbl_valve)

        # 레퍼런스 Waste|Collect = 트랙(QFrame#SegTrack) + 선택 pill
        self.outlet_track = QFrame()
        self.outlet_track.setObjectName("SegTrack")
        h_valves = QHBoxLayout(self.outlet_track)
        h_valves.setContentsMargins(3, 3, 3, 3)
        h_valves.setSpacing(2)

        self.btn_waste = QPushButton("WASTE")
        self.btn_waste.setCheckable(True)
        self.btn_waste.setChecked(True)
        self.btn_waste.setMinimumHeight(T.H_BTN)
        self.btn_waste.setCursor(Qt.PointingHandCursor)

        self.btn_coll = QPushButton("COLLECT")
        self.btn_coll.setCheckable(True)
        self.btn_coll.setMinimumHeight(T.H_BTN)
        self.btn_coll.setCursor(Qt.PointingHandCursor)

        btn_group = QButtonGroup(self)
        btn_group.addButton(self.btn_waste)
        btn_group.addButton(self.btn_coll)

        def switch_outlet(btn):
            outlet = app.valves.get("Outlet")
            if not hasattr(outlet, 'set_position'):
                return
            target = 1 if btn == self.btn_waste else 2

            # @codesyncer(A): 밸브 전환(제품 분기 Waste↔Collect)을 스레드화 — 느린 링크에서
            #   GUI 프리즈 방지. ACK 실패 시 pill 을 실제 위치로 되돌리는 건 GUI 스레드로 마샬.
            def _run():
                try:
                    outlet.set_position(target)
                except Exception as e:
                    app.signals.sig_log.emit(f"[Outlet] 전환 실패: {e}")
                    QMetaObject.invokeMethod(self, "_outlet_resync", Qt.QueuedConnection)
            self._bg_simple(_run, "[Outlet] 오류", disable=[])

        btn_group.buttonClicked.connect(switch_outlet)

        h_valves.addWidget(self.btn_waste, 1)
        h_valves.addWidget(self.btn_coll, 1)
        outlet_layout.addWidget(self.outlet_track)

        # @codesyncer-decision: Fraction Collector Manual Control 통합
        self.separator_outlet = QFrame()
        self.separator_outlet.setFrameShape(QFrame.HLine)
        self.separator_outlet.setFrameShadow(QFrame.Sunken)
        outlet_layout.addWidget(self.separator_outlet)

        # 캡션 행: FRACTION COLLECTOR ——— Position 칩 (히터 ACT 칩과 동일 패턴,
        # 세로 1행 절약 — 우레일 수직 예산 확보)
        coll_head = QHBoxLayout()
        coll_head.setSpacing(T.SP_SM)
        self.lbl_collector = QLabel("FRACTION COLLECTOR")
        coll_head.addWidget(self.lbl_collector)
        coll_head.addStretch()
        self.lbl_collector_pos = QLabel("Position: HOME (0)")
        self.lbl_collector_pos.setStyleSheet(f"font-size: {T.FS_SM};")
        coll_head.addWidget(self.lbl_collector_pos)
        outlet_layout.addLayout(coll_head)

        # @codesyncer-decision: Plate96일 때만 노출되는 "Plate View" 버튼 (최상단 배치)
        self.btn_plate96_view = QPushButton("96-Well Plate View")
        self.btn_plate96_view.setMinimumHeight(T.H_BTN_LG)
        self.btn_plate96_view.clicked.connect(self.open_plate96_dialog)
        outlet_layout.addWidget(self.btn_plate96_view)

        # 누적 스텝 표시
        step_row = QHBoxLayout()
        step_row.setSpacing(T.SP_SM)
        step_row.setContentsMargins(0, T.SP_XS, 0, T.SP_XS)

        self.lbl_step = QLabel("Steps:")
        self.lbl_step.setStyleSheet(f"font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")
        self.lbl_step.setFixedWidth(65)

        self.lcd_collector_step = QLCDNumber()
        self.lcd_collector_step.setSegmentStyle(QLCDNumber.Flat)
        self.lcd_collector_step.setFixedHeight(32)

        step_row.addWidget(self.lbl_step)
        step_row.addWidget(self.lcd_collector_step, 1)
        outlet_layout.addLayout(step_row)

        # 목표 튜브 + 이동 버튼
        tube_row = QHBoxLayout()
        tube_row.setSpacing(T.SP_SM)
        tube_row.setContentsMargins(0, T.SP_XS, 0, T.SP_SM)

        self.lbl_tube = QLabel("Target:")
        self.lbl_tube.setStyleSheet(f"font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")
        self.lbl_tube.setFixedWidth(65)

        self.sp_collector_tube = QSpinBox()
        # 상한을 실제 collector 용량으로 클램프 (최초 부팅 포함)
        _ct = int(getattr(getattr(app, "collector", None), "total_tubes", 0) or 0)
        self.sp_collector_tube.setRange(1, _ct if _ct > 0 else 192)
        self.sp_collector_tube.setPrefix("Tube ")
        self.sp_collector_tube.setMinimumHeight(32)
        self.sp_collector_tube.valueChanged.connect(self.update_collector_step_preview)

        self.btn_collector_move = QPushButton("MOVE")
        self.btn_collector_move.setMinimumHeight(T.H_BTN_LG)
        self.btn_collector_move.setMinimumWidth(70)
        self.btn_collector_move.clicked.connect(self.manual_collector_move)

        tube_row.addWidget(self.lbl_tube)
        tube_row.addWidget(self.sp_collector_tube, 1)
        tube_row.addWidget(self.btn_collector_move)
        outlet_layout.addLayout(tube_row)

        # 이전/다음 버튼
        nav_btn_layout = QHBoxLayout()
        nav_btn_layout.setSpacing(T.SP_SM)
        nav_btn_layout.setContentsMargins(0, 0, 0, T.SP_SM)

        self.btn_collector_back = QPushButton("PREV (−1)")
        self.btn_collector_back.setMinimumHeight(T.H_BTN)
        self.btn_collector_back.clicked.connect(self.collector_move_back)

        self.btn_collector_forward = QPushButton("NEXT (+1)")
        self.btn_collector_forward.setMinimumHeight(T.H_BTN)
        self.btn_collector_forward.clicked.connect(self.collector_move_forward)

        # HOME 을 같은 행에 (Colosseum 전용 3-up — 세로 1행 절약)
        self.btn_collector_home = QPushButton("HOME")
        self.btn_collector_home.setMinimumHeight(T.H_BTN)
        self.btn_collector_home.clicked.connect(self.collector_go_home)

        nav_btn_layout.addWidget(self.btn_collector_back, 1)
        nav_btn_layout.addWidget(self.btn_collector_forward, 1)
        nav_btn_layout.addWidget(self.btn_collector_home, 1)
        outlet_layout.addLayout(nav_btn_layout)

        # ───────────────────────────────────────────────
        # Plate96 전용 좌표 입력 UI (Colosseum 시에는 숨김)
        # 구성: [Plate 토글] → [Well 텍스트 입력] → [Go]
        # ───────────────────────────────────────────────
        self.plate96_ctrl_box = QWidget()
        p96 = QVBoxLayout(self.plate96_ctrl_box)
        p96.setContentsMargins(0, T.SP_XS, 0, T.SP_SM)
        p96.setSpacing(6)

        # Row 1: Plate 토글 버튼 (A / B)
        pr1 = QHBoxLayout()
        pr1.setSpacing(6)
        lbl_pl = QLabel("Plate:")
        lbl_pl.setStyleSheet(f"font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")
        lbl_pl.setFixedWidth(45)
        self.btn_plate_a = QPushButton("A")
        self.btn_plate_a.setCheckable(True)
        self.btn_plate_a.setChecked(True)
        self.btn_plate_a.setMinimumHeight(30)
        self.btn_plate_b = QPushButton("B")
        self.btn_plate_b.setCheckable(True)
        self.btn_plate_b.setMinimumHeight(30)
        self.plate_group = QButtonGroup(self)
        self.plate_group.setExclusive(True)
        self.plate_group.addButton(self.btn_plate_a, 0)
        self.plate_group.addButton(self.btn_plate_b, 1)
        self.plate_group.buttonClicked.connect(self._update_selected_label)
        # A|B = 세그먼트 트랙+pill
        self.plate_track = QFrame()
        self.plate_track.setObjectName("SegTrack")
        pt_ab = QHBoxLayout(self.plate_track)
        pt_ab.setContentsMargins(3, 3, 3, 3)
        pt_ab.setSpacing(2)
        pt_ab.addWidget(self.btn_plate_a, 1)
        pt_ab.addWidget(self.btn_plate_b, 1)
        pr1.addWidget(lbl_pl)
        pr1.addWidget(self.plate_track, 1)
        p96.addLayout(pr1)

        # Row 2: Well 텍스트 입력 ("A12")
        pr2 = QHBoxLayout()
        pr2.setSpacing(6)
        lbl_w = QLabel("Well:")
        lbl_w.setStyleSheet(f"font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};")
        lbl_w.setFixedWidth(45)
        self.le_well = QLineEdit("A1")
        self.le_well.setPlaceholderText("e.g. A12, H7")
        self.le_well.setMinimumHeight(30)
        self.le_well.textEdited.connect(self._update_selected_label)
        pr2.addWidget(lbl_w)
        pr2.addWidget(self.le_well, 1)
        p96.addLayout(pr2)

        # Row 3: [이동] + [원점 복귀] 같은 줄
        pr_action = QHBoxLayout()
        pr_action.setSpacing(6)
        self.btn_go_well = QPushButton("MOVE")
        self.btn_go_well.setMinimumHeight(T.H_BTN)
        self.btn_go_well.clicked.connect(self.manual_go_well)
        self.btn_home_p96 = QPushButton("HOME")
        self.btn_home_p96.setMinimumHeight(T.H_BTN)
        self.btn_home_p96.clicked.connect(self.collector_go_home)
        self.btn_wash_p96 = QPushButton("WASH")
        self.btn_wash_p96.setMinimumHeight(T.H_BTN)
        self.btn_wash_p96.clicked.connect(self.manual_go_wash)
        pr_action.addWidget(self.btn_go_well, 1)
        pr_action.addWidget(self.btn_home_p96, 1)
        pr_action.addWidget(self.btn_wash_p96, 1)
        p96.addLayout(pr_action)

        outlet_layout.addWidget(self.plate96_ctrl_box)

        # (btn_collector_home 은 PREV/NEXT 행에 통합 — Colosseum 전용,
        #  Plate96 에선 btn_home_p96 가 대체)
        outlet_layout.addStretch()

        # @codesyncer-decision: 안전 퀵스트립(폐기/모두정지) 제거 — 상단 글로벌
        #   E-Stop 과 중복. 폐기 전환은 위 Waste|Collect 토글이 상시 담당.

        g_outlet.setLayout(outlet_layout)

        # ── 최종 조립: Feed(좌) | V-스플리터(Process 위 / Collect 아래) ──
        # @codesyncer-decision: 물리 흐름 멘탈모델은 두 겹으로 보존 —
        #   FlowStrip 이 좌→우로 읽히고, 존은 Feed(좌)→Process(우상)→Collect(우하)
        #   시계방향으로 하류가 이어진다. 우측 세로스택 + 리드아웃 타일이
        #   구 3단의 addStretch 빈 공간(~50-65%)을 구조적으로 제거.
        right_col = QSplitter(Qt.Vertical)
        right_col.setChildrenCollapsible(False)
        right_col.setHandleWidth(6)
        right_col.addWidget(g_reactor)
        right_col.addWidget(g_outlet)
        # 공간배치: PROCESS 가 확장 요소(차트) 보유 → 여유 높이 우선 배분(2:1).
        # COLLECT 는 고정 높이 폼이라 stretch 를 줄 이유가 없음.
        # (정확한 초기 배분은 showEvent 에서 실측 높이 기준으로 시드 —
        #  show 전 setSizes 는 첫 레이아웃에서 sizeHint 비율로 무시되던 버그)
        right_col.setStretchFactor(0, 2)
        right_col.setStretchFactor(1, 1)
        self._right_col = right_col
        self._split_seeded = False
        right_col.setMinimumWidth(320)

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.setHandleWidth(6)
        main_split.addWidget(g_feed)
        main_split.addWidget(right_col)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([1060, 460])
        main_layout.addWidget(main_split, 1)

        # 초기 collector 타입에 맞게 Plate96 UI 토글
        self.sync_plate96_ui()

        # 레퍼런스 버튼 언어 일괄 적용 (모든 버튼 생성 후 1회)
        self._style_manual_buttons()

    def _setup_push_pump_section(self, parent_layout):
        """Push Pump(HPLC) 제어 섹션. push_pump 존재하지 않으면 섹션 전체를 숨긴다.

        @codesyncer-decision: 중첩 QGroupBox(부유 타이틀) 제거 — 좁은 우레일에서
        타이틀이 차트 위로 그려지던 겹침 원인. 히터와 동일 규격(캡션 행 + 입력
        전폭 + 와이드 버튼 행)으로 평탄화 = 그룹별 양식 통일.
        """
        app = self.app
        self.push_pump_group = QWidget()
        self.push_pump_group.setStyleSheet("background: transparent;")
        pp_layout = QVBoxLayout(self.push_pump_group)
        pp_layout.setContentsMargins(0, 0, 0, 0)
        pp_layout.setSpacing(6)

        # 캡션 행: PUSH PUMP (HPLC) + ACT 압력 배지
        pp_head = QHBoxLayout()
        pp_head.setSpacing(T.SP_SM)
        # 타이틀 축약: "(HPLC)" 제거 — 반폭 컬럼서 "PUSH PUMP (HP…" 로 잘리던 문제.
        self.lbl_push_flow = QLabel("PUSH PUMP")
        self.lbl_push_flow.setFixedHeight(26)   # 헤드 행 높이 통일 (HEATER 열과 정렬)
        pp_head.addWidget(self.lbl_push_flow)
        pp_head.addStretch()
        self.lbl_push_pressure_title = QLabel("ACT")
        self.lbl_push_pressure_title.setStyleSheet(
            f"font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; letter-spacing: 1px;")
        self.lbl_push_pressure = QLabel("-- bar")
        self.lbl_push_pressure.setFixedHeight(26)   # 헤드 행 높이 통일 (HEATER 열과 정렬)
        pp_head.addWidget(self.lbl_push_pressure_title)
        pp_head.addWidget(self.lbl_push_pressure)
        pp_layout.addLayout(pp_head)

        # 서브캡션 — 히터 열('Set Temp')과 행 구조 동일화. 단위 괄호 제거(입력칸
        #   suffix 가 "mL/min" 표시 → 중복 부연설명, 사용자 "부제·부연설명 정리").
        self.lbl_push_sub = QLabel("Flow Rate")
        pp_layout.addWidget(self.lbl_push_sub)

        self.sp_push_flow = QDoubleSpinBox()
        self.sp_push_flow.setRange(0.0, 10.0)
        self.sp_push_flow.setDecimals(3)
        self.sp_push_flow.setSingleStep(0.1)
        self.sp_push_flow.setValue(1.0)
        self.sp_push_flow.setSuffix(" mL/min")
        self.sp_push_flow.setMinimumHeight(32)
        pp_layout.addWidget(self.sp_push_flow)

        self.btn_push_start = QPushButton("START")
        self.btn_push_start.setMinimumHeight(T.H_BTN)
        self.btn_push_start.setCursor(Qt.PointingHandCursor)
        self.btn_push_start.clicked.connect(self._push_pump_start)

        self.btn_push_stop = QPushButton("STOP")
        self.btn_push_stop.setMinimumHeight(T.H_BTN)
        self.btn_push_stop.setCursor(Qt.PointingHandCursor)
        self.btn_push_stop.clicked.connect(self._push_pump_stop)

        pp_btns = QHBoxLayout()
        pp_btns.setSpacing(T.SP_SM)
        pp_btns.addWidget(self.btn_push_start, 1)
        pp_btns.addWidget(self.btn_push_stop, 1)
        pp_layout.addLayout(pp_btns)

        parent_layout.addWidget(self.push_pump_group, 1, Qt.AlignTop)

        # push_pump이 없으면 섹션 숨김
        if getattr(app, 'push_pump', None) is None:
            self.push_pump_group.setVisible(False)
            return

        # 1초 주기로 압력 갱신
        self._push_pressure_timer = QTimer(self)
        self._push_pressure_timer.timeout.connect(self._update_push_pressure)
        self._push_pressure_timer.start(1000)

    def _push_pump_start(self):
        """Push pump을 현재 UI 입력 유속으로 기동한다. (Theme A: 시리얼 호출 스레드화 — 프리즈·연타 방지)"""
        app = self.app
        if app.push_pump is None:
            app.signals.sig_log.emit("[PushPump] 오류: push_pump가 구성되지 않음")
            return
        flow = self.sp_push_flow.value()

        def _run():
            app.push_pump.set_flow(flow)
            app.push_pump.start()
            app.signals.sig_log.emit(f"[PushPump] Start @ {flow:.3f} mL/min")
        # START 만 비활성(STOP 은 안전상 상시 유지)
        self._bg_simple(_run, "[PushPump] Start 실패", disable=[self.btn_push_start])

    def _push_pump_stop(self):
        """Push pump 정지."""
        app = self.app
        if app.push_pump is None:
            return

        def _run():
            app.push_pump.stop()
            app.signals.sig_log.emit("[PushPump] Stop")
        self._bg_simple(_run, "[PushPump] Stop 실패", disable=[self.btn_push_stop])

    def _update_push_pressure(self):
        """Push pump 압력 라벨 갱신 (1 Hz)."""
        app = self.app
        if not hasattr(self, 'lbl_push_pressure'):
            return
        if app.push_pump is None:
            return
        try:
            p = app.push_pump.get_pressure()
            if p is None:
                self.lbl_push_pressure.setText("-- bar")
            else:
                self.lbl_push_pressure.setText(f"{float(p):.1f} bar")
        except Exception:
            self.lbl_push_pressure.setText("-- bar")

    def _mfc_set(self):
        """MFC 유량 설정 — 데몬 스레드 (Modbus 왕복), 실패 시 로그."""
        mfc = getattr(self.app, "mfc", None)
        if mfc is None:
            return
        val = float(self.sp_mfc.value())

        def job():
            try:
                mfc.set_flow(val)
                self.app.signals.sig_log.emit(f"[MFC] set {val:.1f} sccm")
            except Exception as e:
                self.app.signals.sig_log.emit(f"[MFC] 설정 실패: {e}")
        import threading
        threading.Thread(target=job, daemon=True).start()

    def _save_hte_params(self, *a):
        """Manual 의 HTE 파라미터 → system_params 저장 (엔진 _run_hte_droplet 사용).
        gas_sccm 은 액체치환 등가유량(gas_equiv)에도 반영 — 타이밍 프로파일 정합."""
        app = self.app
        if not hasattr(app, "cfg"):
            return
        cd = app.cfg.config_data
        spp = cd.setdefault("system_params", {})
        spp["hte_mode"] = bool(self.chk_hte.isChecked())
        spp["nitrogen_line"] = True
        for key, w in getattr(self, "_hte_params", {}).items():
            spp[key] = round(float(w.value()), 3)
        # gas_sccm → 스페이서 액체치환 등가유량(gas_equiv)로도 사용(실측 전 근사)
        spp["hte_gas_equiv_flow_ml_min"] = spp.get("hte_gas_sccm", 20.0)
        try:
            app.cfg.save_config(cd.get("inventory", []), cd.get("roles", {}), spp)
            app.signals.sig_log.emit(
                f"[HTE] 파라미터 저장 — mode={'ON' if spp['hte_mode'] else 'off'}, "
                f"spacer {spp.get('hte_spacer_vol_ml')}mL, gas {spp.get('hte_gas_sccm')}sccm")
        except Exception as e:
            app.signals.sig_log.emit(f"[HTE] 파라미터 저장 실패: {e}")

    def _toggle_hte_params(self, checked):
        """HTE 모드 체크 시에만 5-파라미터 그리드 표시(접이식) — 좁은 PROCESS 존
        높이 절약으로 라벨↔입력 겹침 방지. 저장은 _save_hte_params 가 별도 처리."""
        if hasattr(self, "hte_param_box"):
            self.hte_param_box.setVisible(bool(checked))
        # 그리드 표시/숨김으로 PROCESS 필요높이가 바뀜 → 스플리터 재배분
        self._reseed_right_split()

    def _mfc_off(self):
        mfc = getattr(self.app, "mfc", None)
        if mfc is None:
            return

        def job():
            try:
                mfc.set_flow(0.0)
                self.app.signals.sig_log.emit("[MFC] OFF")
            except Exception as e:
                self.app.signals.sig_log.emit(f"[MFC] OFF 실패: {e}")
        import threading
        threading.Thread(target=job, daemon=True).start()

    def sync_mfc_section(self):
        """roles.gas 배정 여부에 따라 MFC 블록 표시 (핫리로드 대응)."""
        if not hasattr(self, "mfc_group"):
            return
        mfc = getattr(self.app, "mfc", None)
        self.mfc_group.setVisible(mfc is not None)
        if mfc is not None:
            try:
                self.sp_mfc.setMaximum(float(getattr(mfc, "max_sccm", 100.0) or 100.0))
            except Exception:
                pass

    def sync_push_pump_section(self):
        """Hot Reload 시 push_pump 존재 여부에 따라 섹션 가시성 토글.
        타이머도 push_pump 유무에 맞춰 start/stop."""
        app = self.app
        if not hasattr(self, 'push_pump_group'):
            return
        has_pp = getattr(app, 'push_pump', None) is not None
        self.push_pump_group.setVisible(has_pp)
        if getattr(self, "sep_proc", None) is not None:
            self.sep_proc.setVisible(has_pp)   # 열 구분선도 함께
        timer = getattr(self, '_push_pressure_timer', None)
        if has_pp:
            if timer is None:
                self._push_pressure_timer = QTimer(self)
                self._push_pressure_timer.timeout.connect(self._update_push_pressure)
            if not self._push_pressure_timer.isActive():
                self._push_pressure_timer.start(1000)
        else:
            if timer is not None and timer.isActive():
                timer.stop()
            if hasattr(self, 'lbl_push_pressure'):
                self.lbl_push_pressure.setText("-- bar")

    # ── 백그라운드 실행 헬퍼 (Theme A: 하드웨어 호출 스레드화 — UI 프리즈·연타 방지) ──
    # @codesyncer(A): 수집기 스텝 이동은 수 초 블로킹(모터+시리얼). GUI 스레드에서 돌면
    #   창이 얼고, 버튼이 활성이라 연타 시 이동이 중첩되어 목표가 어긋난다. 데몬 스레드로
    #   실행 + 이동 중 전 nav 버튼 비활성 + 완료 시 GUI 스레드(QueuedConnection)에서 복원.
    #   ChannelColumnCard._threaded 와 동일 계약. 새 시그널 없이 QMetaObject.invokeMethod 로 마샬.
    _COLLECTOR_NAV_BTNS = ('btn_collector_move', 'btn_collector_back', 'btn_collector_forward',
                           'btn_collector_home', 'btn_go_well', 'btn_home_p96', 'btn_wash_p96')

    def _collector_nav_buttons(self):
        return [getattr(self, n) for n in self._COLLECTOR_NAV_BTNS if getattr(self, n, None) is not None]

    def _collector_bg(self, fn):
        """수집기 블로킹 이동을 데몬 스레드로. 이동 중 nav 버튼 전체 비활성(연타·desync 차단)."""
        if getattr(self, '_collector_busy', False):
            return
        self._collector_busy = True
        self._collector_busy_btns = self._collector_nav_buttons()
        for b in self._collector_busy_btns:
            b.setEnabled(False)
        import threading

        def job():
            try:
                fn()
            except Exception as e:
                self.app.signals.sig_log.emit(f"[Collector] 이동 실패: {e}")
            finally:
                QMetaObject.invokeMethod(self, "_collector_op_done", Qt.QueuedConnection)
        threading.Thread(target=job, daemon=True).start()

    @pyqtSlot()
    def _collector_op_done(self):
        """수집기 이동 완료 (GUI 스레드) — 버튼 복원 + 위치/스핀박스 실측 동기화."""
        self._collector_busy = False
        for b in getattr(self, '_collector_busy_btns', []):
            b.setEnabled(True)
        self._collector_busy_btns = []
        self.update_collector_position()
        app = self.app
        if app.collector and hasattr(app.collector, 'get_position') \
                and hasattr(self, 'sp_collector_tube'):
            try:
                pos = app.collector.get_position()
                if pos and pos >= 1:
                    self.sp_collector_tube.blockSignals(True)
                    self.sp_collector_tube.setValue(pos)
                    self.sp_collector_tube.blockSignals(False)
            except Exception:
                pass
        self._refresh_collector_enabled()

    def _refresh_collector_enabled(self):
        """연결 상태에 따라 수집기 nav 버튼 활성/비활성 (Theme B: 미연결 시 무반응 대신 명확한 비활성).
        이동 중(_collector_busy)에는 건드리지 않음. btn_go_well 은 well 유효성 게이팅이라 위임."""
        if getattr(self, '_collector_busy', False):
            return
        conn = bool(self.app.collector and getattr(self.app.collector, 'is_connected', False))
        go_well = getattr(self, 'btn_go_well', None)
        for b in self._collector_nav_buttons():
            if b is go_well:
                continue  # 연결 + well 유효성 = _update_selected_label 이 소유
            b.setEnabled(conn)
        if go_well is not None:
            self._update_selected_label()

    def _bg_simple(self, fn, err_prefix, disable=()):
        """짧은 블로킹 시리얼 호출(밸브/펌프 명령)을 데몬 스레드로 — GUI 프리즈 방지.
        disable 버튼은 호출 동안 비활성(연타 방지). STOP 류는 disable 에서 제외해 상시 유지."""
        for b in disable:
            b.setEnabled(False)
        self._bg_simple_btns = list(disable)
        import threading

        def job():
            try:
                fn()
            except Exception as e:
                self.app.signals.sig_log.emit(f"{err_prefix}: {e}")
            finally:
                QMetaObject.invokeMethod(self, "_bg_simple_done", Qt.QueuedConnection)
        threading.Thread(target=job, daemon=True).start()

    @pyqtSlot()
    def _bg_simple_done(self):
        for b in getattr(self, '_bg_simple_btns', []):
            b.setEnabled(True)
        self._bg_simple_btns = []

    @pyqtSlot()
    def _outlet_resync(self):
        """Outlet 밸브 ACK 실패 시 pill 을 실제 위치(진실값)로 복원 (GUI 스레드)."""
        outlet = self.app.valves.get("Outlet")
        actual = getattr(outlet, "position", 1)
        (self.btn_waste if actual == 1 else self.btn_coll).setChecked(True)

    def manual_collector_move(self):
        """Manual Tab의 Fraction Collector 이동 버튼 (목표 튜브로 이동)"""
        app = self.app
        if not (app.collector and getattr(app.collector, 'is_connected', False)):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return
        tube = self.sp_collector_tube.value()

        def _run():
            _, msg = app.collector.move_to_tube(tube)
            app.signals.sig_log.emit(f"[Collector] 이동: {msg}")
        self._collector_bg(_run)

    def sync_plate96_ui(self):
        """Collector 타입에 따라 Plate96 전용 UI 토글.
        Plate96: Plate View 상단 버튼 + Plate/Well 입력 + [Go + 원점복귀] 한 줄
        Colosseum: 튜브 번호 입력 + LCD + 단독 원점 복귀 버튼"""
        app = self.app
        is_plate96 = bool(app.collector and hasattr(app.collector, 'get_well_id'))

        # Plate View 팝업 버튼 (상단)
        if hasattr(self, 'btn_plate96_view'):
            self.btn_plate96_view.setVisible(is_plate96)

        # Plate96 전용 좌표 입력 박스 (내부에 Go + 원점복귀)
        if hasattr(self, 'plate96_ctrl_box'):
            self.plate96_ctrl_box.setVisible(is_plate96)

        # Colosseum 전용 원점복귀 버튼 (단독)
        if hasattr(self, 'btn_collector_home'):
            self.btn_collector_home.setVisible(not is_plate96)

        # 누적 스텝 LCD (Colosseum 전용)
        if hasattr(self, 'lcd_collector_step') and hasattr(self, 'lbl_step'):
            self.lcd_collector_step.setVisible(not is_plate96)
            if is_plate96:
                self.lbl_step.setText("")
                self.lbl_step.setVisible(False)
            else:
                self.lbl_step.setText("Steps:")
                self.lbl_step.setVisible(True)

        # 기존 "목표 튜브 N + 이동 + 이전/다음" — Colosseum 전용
        for w_name in ('lbl_tube', 'sp_collector_tube', 'btn_collector_move',
                       'btn_collector_back', 'btn_collector_forward'):
            w = getattr(self, w_name, None)
            if w is not None:
                w.setVisible(not is_plate96)

        self._refresh_collector_enabled()

    # ─── Plate96 좌표 입력 UI ─────────────────────────────
    def _parse_well(self, text):
        """
        Well 텍스트를 (row_char, col_int)로 파싱.
        허용 형식: 'A1', 'A12', 'H7', 'a-5', ' A 12 ' (대소문자/공백/하이픈 관대)
        반환: (row, col) 또는 None (무효)
        """
        t = (text or "").strip().upper()
        m = re.fullmatch(r"([A-H])\s*-?\s*(\d{1,2})", t)
        if not m:
            return None
        row = m.group(1)
        col = int(m.group(2))
        if not (1 <= col <= 12):
            return None
        return row, col

    def _update_selected_label(self, *args):
        """Well 입력 유효성 + 수집기 연결에 따라 이동 버튼 enable/disable"""
        if not hasattr(self, 'btn_go_well'):
            return
        if getattr(self, '_collector_busy', False):
            return
        parsed = self._parse_well(self.le_well.text())
        conn = bool(self.app.collector and getattr(self.app.collector, 'is_connected', False))
        self.btn_go_well.setEnabled(parsed is not None and conn)

    def manual_go_well(self):
        """Plate 토글 + Well 텍스트 조합으로 collector 이동"""
        app = self.app
        if not app.collector or not getattr(app.collector, 'is_connected', False):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return
        if not hasattr(app.collector, 'move_to_well'):
            app.signals.sig_log.emit("[Collector] move_to_well 미지원 드라이버")
            return
        parsed = self._parse_well(self.le_well.text())
        if parsed is None:
            app.signals.sig_log.emit(
                f"[Collector] 잘못된 well '{self.le_well.text()}' — 예: A1, H12"
            )
            return
        plate = "A" if self.btn_plate_a.isChecked() else "B"
        row, col = parsed
        well_name = f"{row}{col}"

        def _run():
            ok, msg = app.collector.move_to_well(plate, well_name)
            app.signals.sig_log.emit(f"[Collector] {plate}_{well_name}: {msg}")
        self._collector_bg(_run)

    def manual_go_wash(self):
        """WASH 포트로 이동"""
        app = self.app
        if not app.collector or not getattr(app.collector, 'is_connected', False):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return
        if not hasattr(app.collector, 'move_to_wash'):
            app.signals.sig_log.emit("[Collector] move_to_wash 미지원")
            return

        def _run():
            ok, msg = app.collector.move_to_wash()
            app.signals.sig_log.emit(f"[Collector] Wash: {msg}")
        self._collector_bg(_run)

    def open_plate96_dialog(self):
        """96-Well Manual Dialog 열기"""
        app = self.app
        if not app.collector or not hasattr(app.collector, 'get_well_id'):
            app.signals.sig_log.emit(
                "[Collector] 현재 분취기는 96-well Plate96이 아닙니다.")
            return
        try:
            from ui.dialog_plate96_manual import Plate96ManualDialog
        except ImportError as e:
            app.signals.sig_log.emit(f"[Collector] Plate96 Dialog 로드 실패: {e}")
            return
        dlg = Plate96ManualDialog(
            collector=app.collector,
            engine=getattr(app, 'engine', None),
            parent=self.app,
        )
        dlg.exec_()
        # 닫힌 후 현재 위치 갱신
        self.update_collector_position()

    def update_collector_step_preview(self, val):
        """
        목표 튜브 변경 시 LCD 미리보기.
        - Colosseum: 실제 누적 모터 스텝 (의미 있는 숫자)
        - Plate96:   cumulative_steps가 stub(튜브 번호)이므로 라벨을 'Tube #'으로 표시
        """
        app = self.app
        if app.collector and hasattr(app.collector, 'get_well_id'):
            # Plate96 — 튜브 번호만 표시 (LCD는 숫자만 가능)
            self.lbl_step.setText("Tube #:")
            self.lcd_collector_step.display(val)
        elif app.collector and hasattr(app.collector, 'cumulative_steps'):
            # Colosseum — 실제 누적 스텝
            self.lbl_step.setText("Steps:")
            steps = app.collector.cumulative_steps
            if steps and 1 <= val < len(steps):
                self.lcd_collector_step.display(steps[val])
            else:
                self.lcd_collector_step.display(0)
        else:
            self.lcd_collector_step.display(0)

    def update_collector_position(self):
        """현재 Collector 위치 라벨 업데이트"""
        app = self.app
        # @codesyncer-decision: 테마에 따라 팔레트 선택
        from ui.colors import LightPalette as Light
        P = Dark if app.is_dark_mode else Light

        if app.collector and hasattr(app.collector, 'get_position'):
            pos = app.collector.get_position()
            if pos == 0:
                self.lbl_collector_pos.setText("Position: HOME (0)")
                self.lbl_collector_pos.setStyleSheet(f"font-size: {T.FS_SM}; color: {P.ACCENT_GREEN};")
            else:
                # Plate96은 Well ID도 병기 (예: "Tube 25 (A_C1)")
                wid = ""
                if hasattr(app.collector, 'get_well_id'):
                    try:
                        w = app.collector.get_well_id(pos)
                        if w and w not in ("?", "HOME"):
                            wid = f" ({w})"
                    except Exception:
                        pass
                self.lbl_collector_pos.setText(f"Position: Tube {pos}{wid}")
                self.lbl_collector_pos.setStyleSheet(f"font-size: {T.FS_SM}; color: {P.ACCENT_BLUE};")
        else:
            self.lbl_collector_pos.setText("Position: --")
            self.lbl_collector_pos.setStyleSheet(f"font-size: {T.FS_SM}; color: {P.TEXT_DISABLED};")

    def collector_move_forward(self):
        """다음 튜브로 정방향 이동 (+1)"""
        app = self.app
        if not (app.collector and getattr(app.collector, 'is_connected', False)):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return

        def _run():
            current = app.collector.get_position()
            next_tube = current + 1
            if next_tube <= app.collector.total_tubes:
                app.collector.move_to_tube(next_tube)
                app.signals.sig_log.emit(f"[Collector] 정방향 이동: Tube {next_tube}")
            else:
                app.signals.sig_log.emit(f"[Collector] 경고: 마지막 튜브입니다 (Tube {current})")
        self._collector_bg(_run)

    def collector_move_back(self):
        """이전 튜브로 역방향 이동 (-1)"""
        app = self.app
        if not (app.collector and getattr(app.collector, 'is_connected', False)):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return

        def _run():
            current = app.collector.get_position()
            prev_tube = current - 1
            if prev_tube >= 1:
                app.collector.move_to_tube(prev_tube)
                app.signals.sig_log.emit(f"[Collector] 역방향 이동: Tube {prev_tube}")
            elif prev_tube == 0:
                _, msg = app.collector.home()
                app.signals.sig_log.emit(f"[Collector] 원점 복귀: {msg}")
            else:
                app.signals.sig_log.emit("[Collector] 경고: 이미 HOME 위치입니다")
        self._collector_bg(_run)

    def collector_go_home(self):
        """원점(HOME) 복귀"""
        app = self.app
        if not (app.collector and getattr(app.collector, 'is_connected', False)):
            app.signals.sig_log.emit("[Collector] 오류: 수집기 미연결")
            return

        def _run():
            _, msg = app.collector.home()
            app.signals.sig_log.emit(f"[Collector] 원점 복귀: {msg}")
        self._collector_bg(_run)

    def _place_feed_cards(self, layout):
        """setup·rebuild 공용 채널 카드 배치.

        @codesyncer-decision: 밸브 그룹(A~D)은 N열(4) 그리드 1칸씩 → 한 줄에 나란히.
          RoboChem(NRG 시린지·샘플러)은 밸브와 동시 사용이 적으므로 그 아래 줄에
          '2칸 span'으로 넓게 배치(빈 공간 활용 → 카드 여유↑). self.pump_card_widgets 채움.
        """
        app = self.app
        nc = getattr(self, '_feed_ncols', 4)
        gi = 0
        for p_name in app.cfg.ACTIVE_PUMPS:
            if p_name in app.pumps:
                try:
                    for w in self._create_feed_cards_for_group(p_name):
                        layout.addWidget(w, gi // nc, gi % nc, Qt.AlignTop)
                        self.pump_card_widgets.append(w)
                        gi += 1
                except Exception as e:
                    print(f"[_place_feed_cards] 그룹 카드 오류 ({p_name}): {e}")
        base_row = (gi + nc - 1) // nc if gi else 0   # 밸브 그룹 다음 줄
        ri = 0
        try:
            for w in self._create_robochem_cards():
                layout.addWidget(w, base_row + ri // 2, (ri % 2) * 2, 1, 2, Qt.AlignTop)
                self.pump_card_widgets.append(w)
                ri += 1
        except Exception as e:
            print(f"[_place_feed_cards] RoboChem 카드 오류: {e}")

    def rebuild_pump_widgets(self):
        """
        Manual Tab의 펌프 위젯을 재생성합니다.
        @codesyncer-decision: Hot Reload 시 Feed Zone 위젯 갱신
        @codesyncer-decision: self.feed_scroll 직접 참조로 안정성 확보
        """
        app = self.app
        print(f"[rebuild_pump_widgets] ACTIVE_PUMPS: {app.cfg.ACTIVE_PUMPS}")
        print(f"[rebuild_pump_widgets] self.pumps keys: {list(app.pumps.keys())}")

        if not hasattr(self, 'pump_card_widgets'):
            print("[rebuild_pump_widgets] pump_card_widgets 없음 - return")
            return

        # 기존 위젯 제거
        for widget in self.pump_card_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self.pump_card_widgets.clear()

        # Feed Zone 스크롤 영역 직접 참조
        if not hasattr(self, 'feed_scroll') or not self.feed_scroll:
            print("[rebuild_pump_widgets] feed_scroll 없음 - return")
            return

        print(f"[rebuild_pump_widgets] feed_scroll: {self.feed_scroll}")
        print(f"[rebuild_pump_widgets] feed_scroll.widget(): {self.feed_scroll.widget()}")

        if self.feed_scroll.widget():
            layout = self.feed_scroll.widget().layout()
            print(f"[rebuild_pump_widgets] layout: {layout}, bool(layout): {bool(layout) if layout else 'N/A'}")
            # @codesyncer-decision: PyQt QLayout은 __bool__에서 False 반환 가능 → is not None 사용
            if layout is not None:
                # setup_ui 와 동일한 공용 배치 — 밸브 그룹 N열 + RoboChem 2칸 span
                self._place_feed_cards(layout)
                print(f"[rebuild_pump_widgets] 생성된 위젯: {len(self.pump_card_widgets)}")

                # 테마 적용
                for pw in self.pump_card_widgets:
                    if hasattr(pw, 'apply_theme'):
                        pw.apply_theme(is_dark=app.is_dark_mode)

        # 핫 리로드 후 collector 종류가 바뀌었을 수 있음 → 목표 튜브 상한 재설정
        collector = getattr(app, "collector", None)
        total = int(getattr(collector, "total_tubes", 0) or 0)
        if total > 0 and hasattr(self, "sp_collector_tube"):
            current_val = self.sp_collector_tube.value()
            self.sp_collector_tube.setRange(1, total)
            if current_val > total:
                self.sp_collector_tube.setValue(1)

        # Plate96 전용 UI 재토글 (Colosseum ↔ Plate96 전환 대응)
        self.sync_plate96_ui()

        # Push Pump 섹션 가시성/타이머 재조정
        self.sync_push_pump_section()
        # N2 MFC 블록 가시성 재조정
        self.sync_mfc_section()
