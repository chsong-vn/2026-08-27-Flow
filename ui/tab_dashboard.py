"""
Dashboard tab layout for flow chemistry UI.
"""

from typing import Dict

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Diagram v3(parts_pack 기반) 우선, 실패 시 v2 폴백
try:
    from ui.visual_diagram_parts import FlowDiagramWidget
except Exception as _e:
    print(f"[Dashboard] diagram v3 로드 실패 → v2 폴백: {_e}")
    from ui.visual_diagram import FlowDiagramWidget
from ui.colors import (
    DarkExtras,
    DarkPalette as Dark,
    LightExtras,
    LightPalette as Light,
    T,
    get_chart_pressure_series,
    rgba,
)


class DashboardTab(QWidget):
    """Main dashboard tab with scrollable cards and tabbed map/chart workspace."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.metric_labels = {}
        self.metric_cards = []
        self.pump_status_labels = {}
        self._pump_running = {}
        self.setup_ui()
        self.apply_theme(getattr(self.app, "is_dark_mode", True))

    def setup_ui(self):
        cfg = self.app.cfg

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.canvas = QWidget()
        self.canvas.setObjectName("DashboardCanvas")
        body = QVBoxLayout(self.canvas)
        body.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        body.setSpacing(10)

        self.hero = QFrame()
        self.hero.setObjectName("DashHero")
        hero_l = QHBoxLayout(self.hero)
        hero_l.setContentsMargins(14, 10, 14, 10)
        hero_l.setSpacing(10)

        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(T.SP_XS)

        # @codesyncer-decision: 설명 문장을 헤더에 상주시키지 않고 ⓘ 도움말(툴팁)로.
        #   전문툴(Notion·Linear·Carbon) 헤더 패턴 — 짧은 제목 + hover 도움말.
        self.lbl_title = QLabel("Flow Chemistry Dashboard")
        self.lbl_sub = QLabel("ⓘ")
        self.lbl_sub.setToolTip("Live operations overview, process state, and safety metrics")
        self.lbl_sub.setCursor(Qt.WhatsThisCursor)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self.lbl_title, 0, Qt.AlignVCenter)
        title_row.addWidget(self.lbl_sub, 0, Qt.AlignVCenter)
        title_row.addStretch()
        title_wrap.addLayout(title_row)

        self.lbl_clock = QLabel("LIVE")
        self.lbl_clock.setAlignment(Qt.AlignCenter)

        hero_l.addLayout(title_wrap, 1)
        hero_l.addWidget(self.lbl_clock, 0, Qt.AlignTop | Qt.AlignRight)
        body.addWidget(self.hero)

        cards_wrap = QWidget()
        cards_grid = QGridLayout(cards_wrap)
        cards_grid.setContentsMargins(0, 0, 0, 0)
        cards_grid.setHorizontalSpacing(12)
        cards_grid.setVerticalSpacing(12)

        card_specs = [
            ("active_pumps", "Active Pumps", f"{len(cfg.ACTIVE_PUMPS)}", "Connected channels"),
            ("reactor_volume", "Reactor Volume", f"{cfg.reactor_vol:.2f} mL", "Configured volume"),
            ("max_pressure", "Peak Pressure", "0.00 bar", f"Limit: {float(getattr(cfg, 'max_pressure', 20.0)):.1f} bar"),
            (
                "total_flow",
                "Estimated Total Flow",
                "0.00 mL/min",
                f"Limit: {float(cfg.config_data.get('system_params', {}).get('max_total_flow_ml_min', 100.0)):.1f}",
            ),
        ]

        for idx, (key, title, value, subtitle) in enumerate(card_specs):
            card, value_label = self._build_metric_card(title, value, subtitle)
            self.metric_labels[key] = value_label
            self.metric_cards.append(card)
            cards_grid.addWidget(card, 0, idx)

        for col in range(4):
            cards_grid.setColumnStretch(col, 1)
        body.addWidget(cards_wrap)

        self.process_splitter = QSplitter(Qt.Horizontal)
        self.process_splitter.setHandleWidth(8)
        self.process_splitter.setChildrenCollapsible(False)

        self.workspace_card = QGroupBox("Workspace")
        ws_l = QVBoxLayout(self.workspace_card)
        ws_l.setContentsMargins(10, T.SP_MD, 10, 10)
        ws_l.setSpacing(T.SP_SM)

        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("DashTabs")
        self.main_tabs.setDocumentMode(True)
        self.main_tabs.setTabPosition(QTabWidget.North)
        self.main_tabs.setUsesScrollButtons(False)
        self.main_tabs.setElideMode(Qt.ElideNone)

        map_page = QWidget()
        map_l = QVBoxLayout(map_page)
        map_l.setContentsMargins(0, 0, 0, 0)
        map_l.setSpacing(0)

        pump_configs = cfg.config_data.get("roles", {}).get("pumps", [])
        inventory = cfg.config_data.get("inventory", [])
        self.flow_viz = FlowDiagramWidget(
            pump_configs=pump_configs,
            active_pumps=cfg.ACTIVE_PUMPS,
            inventory=inventory,
        )
        # 시스템 전체 구성(push/collector/데드볼륨)을 배관도에 반영
        self.flow_viz.configure(cfg, self.app)
        self.flow_viz.setMinimumHeight(300)
        map_l.addWidget(self.flow_viz)

        chart_page = QWidget()
        chart_l = QVBoxLayout(chart_page)
        chart_l.setContentsMargins(0, 0, 0, 0)
        chart_l.setSpacing(T.SP_SM)

        # @codesyncer-decision: 차트 카드화 — 대시보드 카드 체계와 디자인 통일
        # - 기존: 생짜 PlotWidget 2개 (프레임/타이틀/현재값 없음, 범례가 라인과 겹침)
        # - 각 차트를 [헤더(타이틀 + 실시간 현재값) + 플롯] 카드로 감싸고,
        #   범례는 헤더의 시리즈색 현재값 칩이 대체 (update_metrics가 매초 갱신)
        self.trend_splitter = QSplitter(Qt.Vertical)
        self.trend_splitter.setHandleWidth(8)
        self.trend_splitter.setChildrenCollapsible(False)

        self.plot_temp = pg.PlotWidget()
        self.plot_pressure = pg.PlotWidget()
        self.plot_temp.setMinimumHeight(170)
        self.plot_pressure.setMinimumHeight(170)

        self._setup_plot_axis(self.plot_temp, "Temperature (C)")
        self._setup_plot_axis(self.plot_pressure, "Pressure (bar)")

        self.plot_temp.setLabel("bottom", "")
        self.plot_temp.getAxis("bottom").setStyle(showValues=False)
        self.plot_pressure.setLabel("bottom", "Time (sec)")
        self.plot_temp.setXLink(self.plot_pressure)

        self.crv_t = self.plot_temp.plot(pen=pg.mkPen(DarkExtras.CHART_TEMP, width=2.6))
        # @codesyncer-decision: 차트 가시성 보강
        # - 목표 온도선(대시): 현재값이 목표 대비 어디인지 즉시 판단
        # - 압력 한계선(대시): 막힘 전조를 한계 대비 거리로 감시
        # - 최신값 도트: 실시간 '지금' 위치 강조 (라인 끝 식별)
        self.line_temp_target = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen(DarkExtras.CHART_TEMP, width=1.2, style=Qt.DashLine))
        self.line_temp_target.setVisible(False)
        self.plot_temp.addItem(self.line_temp_target, ignoreBounds=True)
        self.dot_t = self.plot_temp.plot([], [], pen=None, symbol="o", symbolSize=7,
                                         symbolBrush=DarkExtras.CHART_TEMP,
                                         symbolPen=None)

        self.line_p_limit = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen("#e05252", width=1.2, style=Qt.DashLine))
        self.line_p_limit.setPos(float(getattr(cfg, "max_pressure", 20.0)))
        self.plot_pressure.addItem(self.line_p_limit, ignoreBounds=True)

        self.crv_p = {}
        self.dot_p = {}
        # @codesyncer-decision: NRG(무압력센서) 라인은 압력 차트에서 제외 —
        #   get_pressure()=0.0 상수를 정상 압력처럼 그리면 해당 라인의 과압감시
        #   공백(SafetyManager 무력)이 은폐된다. 소비처는 전부 pressure_order 를
        #   순회하므로 여기 한 곳 필터로 일관 제외됨.
        _pumps_ref = getattr(self.app, "pumps", {}) or {}
        self.pressure_order = [p for p in cfg.ACTIVE_PUMPS
                               if not hasattr(_pumps_ref.get(p), "ROUTING_MODE")]
        colors = list(get_chart_pressure_series(True))
        for idx, p_name in enumerate(self.pressure_order):
            c = colors[idx % len(colors)]
            self.crv_p[p_name] = self.plot_pressure.plot(pen=pg.mkPen(c, width=2.2))
            self.dot_p[p_name] = self.plot_pressure.plot(
                [], [], pen=None, symbol="o", symbolSize=6, symbolBrush=c, symbolPen=None)

        # @codesyncer: 위상센서 0/1 디지털 트랙 (GAS=0/LIQ=1 스텝, 로직애널라이저 스타일)
        #   — HTE droplet 슬러그 트레인 가시화. x축은 압력 차트와 링크, 좌축 폭 78 로
        #   위 차트들과 좌변 정렬. roles.phase 센서 있을 때만 표시(set_phase_track_visible).
        # @codesyncer(2026-08-18, 사용자 요청 — 합산 레인 폐지): 위상센서를 카드
        #   분리 — "Phase Sensor 1 · INLET" / "Phase Sensor 2 · OUTLET" 독립 슬림
        #   트랙. 캐노니컬 2키 고정 생성(실물 리그 = 정확히 2센서), 그 외 키는
        #   미차트. 축은 사용자 요청대로 0(GAS)/1(LIQ) 눈금.
        self.PHASE_KEYS = ("reactor_in", "collect")     # 센서1, 센서2 순
        self.PHASE_CARD_TITLE = {"reactor_in": "전단센서 · INLET",
                                 "collect": "후단센서 · OUTLET"}
        self.PHASE_LANE_COLORS = {"collect": "ACCENT_CYAN", "reactor_in": "ACCENT_PURPLE"}
        self.plot_phases = {}     # key -> PlotWidget
        self.crv_phases = {}      # key -> PlotDataItem (테마 재도색 루프가 이 이름 소비)
        _P0 = Dark if getattr(self.app, "is_dark_mode", True) else Light
        for _pk in self.PHASE_KEYS:
            _pl = pg.PlotWidget()
            _pl.setMinimumHeight(96)
            _pl.setMaximumHeight(150)
            _axl = _pl.getAxis("left")
            _axl.enableAutoSIPrefix(False)
            _pl.getAxis("bottom").enableAutoSIPrefix(False)
            _axl.setTickFont(QFont("Segoe UI", 9))
            _axl.setWidth(78)
            # 슬림 트랙에서 pyqtgraph 가 라벨을 자동 숨기지 않도록 점유율 제한 해제
            _axl.setStyle(tickTextOffset=6, textFillLimits=[(0, 1.0)])
            _axl.setTicks([[(0, "0 GAS"), (1, "1 LIQ")]])
            _pl.getAxis("bottom").setTickFont(QFont("Segoe UI", 9))
            # 슬림 트랙에서 눈금 텍스트가 상하 클리핑되지 않도록 여유 범위
            _pl.setYRange(-0.35, 1.35, padding=0)
            _pl.setMouseEnabled(x=True, y=False)
            _pl.setXLink(self.plot_pressure)
            _pl.setLabel("bottom", "")
            _pl.getAxis("bottom").setStyle(showValues=False)
            _col0 = self._phase_lane_color(_pk, _P0)
            _cf = QColor(_col0)
            _cf.setAlphaF(0.22)
            self.crv_phases[_pk] = _pl.plot(
                stepMode=True, pen=pg.mkPen(_col0, width=2.0),
                fillBrush=pg.mkBrush(_cf), fillLevel=0)
            self.plot_phases[_pk] = _pl

        # 헤더 현재값 라벨 (시리즈색) — phase 는 카드당 칩 1개
        self.lbl_chart_temp = QLabel("-- °C")
        self.phase_chips = {k: QLabel("--") for k in self.PHASE_KEYS}
        self.chart_p_labels = {}
        self.chart_cards = []

        def _chart_card(title, plot, value_widgets):
            card = QFrame()
            card.setObjectName("chartCard")
            v = QVBoxLayout(card)
            v.setContentsMargins(T.SP_MD, T.SP_SM, T.SP_MD, T.SP_SM)
            v.setSpacing(T.SP_XS)
            head = QHBoxLayout()
            head.setSpacing(T.SP_MD)
            lbl_title = QLabel(title)
            lbl_title.setObjectName("chartTitle")
            head.addWidget(lbl_title)
            head.addStretch(1)
            for w_ in value_widgets:
                head.addWidget(w_)
            v.addLayout(head)
            v.addWidget(plot, 1)
            self.chart_cards.append((card, lbl_title))
            return card

        self.lbl_p_limit = QLabel(f"Limit {float(getattr(cfg, 'max_pressure', 20.0)):.0f} bar")
        p_widgets = [self.lbl_p_limit]
        for idx, p_name in enumerate(self.pressure_order):
            lbl = QLabel(f"● {p_name}  --")
            self.chart_p_labels[p_name] = (lbl, colors[idx % len(colors)])
            p_widgets.append(lbl)

        self.trend_splitter.addWidget(
            _chart_card("Reactor Temperature", self.plot_temp, [self.lbl_chart_temp]))
        self.trend_splitter.addWidget(
            _chart_card("Pump Pressure", self.plot_pressure, p_widgets))
        self.phase_cards = {}
        for _pk in self.PHASE_KEYS:
            _card = _chart_card(self.PHASE_CARD_TITLE[_pk],
                                self.plot_phases[_pk], [self.phase_chips[_pk]])
            self.phase_cards[_pk] = _card
            self.trend_splitter.addWidget(_card)
            _card.setVisible(False)                  # 센서(roles.phase) 있을 때만
        self.trend_splitter.setStretchFactor(0, 1)
        self.trend_splitter.setStretchFactor(1, 1)
        for _i in range(2, 2 + len(self.PHASE_KEYS)):
            self.trend_splitter.setStretchFactor(_i, 0)   # 위상 트랙 = 고정 슬림
        self.trend_splitter.setSizes([260, 260])
        chart_page.setMinimumHeight(480)

        chart_l.addWidget(self.trend_splitter)

        self.main_tabs.addTab(map_page, "Map")
        self.main_tabs.addTab(chart_page, "Chart")
        # @codesyncer-decision: Workspace 최소높이 확보 — 차트 2개(온도/압력)가
        # 스크롤 없이 한 화면에 들어오도록 (기존: 압력 차트가 잘려 안 보임)
        self.main_tabs.setMinimumHeight(560)
        ws_l.addWidget(self.main_tabs)

        self.health_card = QGroupBox("System Status")
        self.health_card.setMinimumWidth(280)
        health_l = QVBoxLayout(self.health_card)
        health_l.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, 10)
        health_l.setSpacing(6)

        # @codesyncer-decision: Reactor Temp / Outlet 라벨 + Running Pumps % 제거
        #   (상단 Metric "Active Pumps" / LCD / Phase와 중복)

        self.status_frame = QFrame()
        status_grid = QGridLayout(self.status_frame)
        status_grid.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        status_grid.setHorizontalSpacing(8)
        status_grid.setVerticalSpacing(7)

        status_groups = []
        seen = set()
        for name in list(cfg.ACTIVE_PUMPS) + ["Group_A", "Group_B", "Group_C"]:
            key = self._norm_group(name)
            if key in seen:
                continue
            seen.add(key)
            status_groups.append(name)

        # @codesyncer-decision(2026-08-25 사용자 요청): 그룹 행 아래에 구성 장비
        #   (모터·12way·3way)의 연결/유휴 상태 도트를, 그 아래에 보조 장비 전체
        #   (Outlet·MFC·Push·분취기·히터·위상·레벨)의 상태 행을 추가.
        #   상태 3값: RUN / IDLE / OFFLINE (미연결·Mock 폴백 = OFFLINE).
        self.group_dev_labels = {}
        self._pump_state = {}
        grow = 0
        for p_name in status_groups:
            lbl_n = QLabel(p_name)
            lbl_s = QLabel("IDLE")
            lbl_s.setAlignment(Qt.AlignCenter)
            lbl_n.setMinimumHeight(26)
            lbl_s.setMinimumHeight(26)
            self.pump_status_labels[p_name] = lbl_s
            self._pump_running[p_name] = False
            status_grid.addWidget(lbl_n, grow, 0)
            status_grid.addWidget(lbl_s, grow, 1)
            grow += 1
            # 2026-08-25 사용자 요청: 하위 장비도 다른 장비 행과 동일한 배지 UI 통일
            subdevs = {}
            for dv in ("모터", "12way", "3way"):
                ln = QLabel(dv)
                ln.setMinimumHeight(22)
                lb = QLabel("--")
                lb.setAlignment(Qt.AlignCenter)
                lb.setMinimumHeight(22)
                subdevs[dv] = (ln, lb)
                status_grid.addWidget(ln, grow, 0)
                status_grid.addWidget(lb, grow, 1)
                grow += 1
            self.group_dev_labels[p_name] = subdevs

        self._status_sep = QFrame()
        self._status_sep.setFrameShape(QFrame.HLine)
        self._status_sep.setFixedHeight(1)
        status_grid.addWidget(self._status_sep, grow, 0, 1, 2)
        grow += 1

        self.device_status_labels = {}
        for disp, key in (("Outlet 밸브", "valve:Outlet"), ("N2 MFC", "mfc"),
                          ("Push Pump", "push_pump"), ("분취기", "collector"),
                          ("히터", "heater"), ("위상센서", "phase_sensor"),
                          ("레벨센서", "level_sensor")):
            lbl_n = QLabel(disp)
            lbl_s = QLabel("--")
            lbl_s.setAlignment(Qt.AlignCenter)
            lbl_n.setMinimumHeight(24)
            lbl_s.setMinimumHeight(24)
            self.device_status_labels[key] = lbl_s
            status_grid.addWidget(lbl_n, grow, 0)
            status_grid.addWidget(lbl_s, grow, 1)
            grow += 1

        status_grid.setColumnStretch(0, 2)
        status_grid.setColumnStretch(1, 1)
        n_grp = max(1, len(self.pump_status_labels))
        status_frame_h = min(780, 24 + n_grp * 122 + 9 + len(self.device_status_labels) * 31)
        self.status_frame.setMinimumHeight(status_frame_h)
        self.status_frame.setMaximumHeight(status_frame_h)
        self.status_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        health_l.addWidget(self.status_frame, 0, Qt.AlignTop)
        health_l.addStretch(1)

        self.process_splitter.addWidget(self.workspace_card)
        self.process_splitter.addWidget(self.health_card)
        self.process_splitter.setStretchFactor(0, 3)
        self.process_splitter.setStretchFactor(1, 1)
        self.process_splitter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        body.addWidget(self.process_splitter, 0)
        body.addStretch(1)

        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll)

        self.plot_widgets = [self.plot_temp, self.plot_pressure,
                             *self.plot_phases.values()]
        self._update_responsive_layout()

    def _update_responsive_layout(self):
        """Adjust dashboard split sizes based on available width."""
        total_w = max(0, self.width())
        if total_w <= 0:
            return

        health_w = max(300, min(480, int(total_w * 0.24)))
        left_w = max(640, total_w - health_w - 56)
        self.process_splitter.setSizes([left_w, health_w])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    @staticmethod
    def _norm_group(name: str) -> str:
        return str(name).strip().lower().replace(" ", "").replace("_", "")

    def _build_metric_card(self, title: str, value: str, subtitle: str):
        card = QFrame()
        card.setObjectName("MetricCard")

        l = QVBoxLayout(card)
        l.setContentsMargins(14, T.SP_MD, 14, T.SP_MD)
        l.setSpacing(T.SP_XS)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("MetricTitle")

        lbl_value = QLabel(value)
        lbl_value.setObjectName("MetricValue")

        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("MetricSub")

        l.addWidget(lbl_title)
        l.addWidget(lbl_value)
        l.addWidget(lbl_sub)

        return card, lbl_value

    def _setup_plot_axis(self, plot_widget, y_label):
        # @codesyncer-decision: 겹침/x0.001 수정 — autoSIPrefix 가 빈 데이터의
        #   기본 뷰범위(±0.4)를 ×0.001 스케일 '±400'으로 표시하고, 축 라벨이
        #   눈금 텍스트 위에 겹쳐 그려지던 문제. SI 접두 비활성 + 축 폭 확보.
        ax = plot_widget.getAxis("left")
        ax.enableAutoSIPrefix(False)
        plot_widget.getAxis("bottom").enableAutoSIPrefix(False)
        plot_widget.setLabel("left", y_label, **{"font-size": "9pt", "font-family": "Segoe UI"})
        ax.setTickFont(QFont("Segoe UI", 9))
        ax.setWidth(78)                      # 라벨(회전) + 눈금 텍스트 분리 폭
        ax.setStyle(tickTextOffset=6)
        plot_widget.getAxis("bottom").setTickFont(QFont("Segoe UI", 9))
        plot_widget.showGrid(x=True, y=True, alpha=0.18)
        # 데이터 없을 때 기본 ±0.4 오토레인지가 이상하게 보이지 않도록 초기범위
        plot_widget.setYRange(0.0, 1.0, padding=0.05)
        plot_widget.enableAutoRange(y=True)

    def _set_plot_theme(self, is_dark: bool, palette=None):
        if palette:
            if is_dark:
                plot_bg = palette.BG_DARK
                axis_text = palette.TEXT_TERTIARY
                axis_pen = palette.BORDER_LIGHT
                border = palette.BORDER_PRIMARY
                grid_alpha = 0.16
            else:
                plot_bg = palette.BG_SECONDARY
                axis_text = palette.TEXT_TERTIARY
                axis_pen = palette.BORDER_PRIMARY
                border = palette.BORDER_PRIMARY
                grid_alpha = 0.14
        elif is_dark:
            plot_bg = Dark.BG_DARK
            axis_text = Dark.TEXT_TERTIARY
            axis_pen = Dark.BORDER_LIGHT
            border = Dark.BORDER_PRIMARY
            grid_alpha = 0.18
        else:
            plot_bg = Light.BG_SECONDARY
            axis_text = Light.TEXT_TERTIARY
            axis_pen = Light.BORDER_PRIMARY
            border = Light.BORDER_PRIMARY
            grid_alpha = 0.12

        for pw in self.plot_widgets:
            pw.setBackground(plot_bg)
            pw.getPlotItem().getViewBox().setBackgroundColor(plot_bg)
            pw.getAxis("left").setTextPen(axis_text)
            pw.getAxis("left").setPen(pg.mkPen(axis_pen, width=2))
            pw.getAxis("bottom").setTextPen(axis_text)
            pw.getAxis("bottom").setPen(pg.mkPen(axis_pen, width=2))
            pw.showGrid(x=True, y=True, alpha=grid_alpha)
            pw.setStyleSheet("PlotWidget { border: none; }")

        # 차트 카드 + 헤더 스타일 (대시보드 카드 체계와 동일 토큰)
        chart_temp = DarkExtras.CHART_TEMP if is_dark else LightExtras.CHART_TEMP
        series_colors = list(get_chart_pressure_series(is_dark))
        card_bg = palette.BG_SECONDARY if palette else (Dark.BG_SECONDARY if is_dark else Light.BG_SECONDARY)
        for card, lbl_title in getattr(self, "chart_cards", []):
            card.setStyleSheet(
                f"QFrame#chartCard {{ background: {card_bg}; border: 1px solid {border}; "
                f"border-radius: {T.R_LG}; }}"
            )
            lbl_title.setStyleSheet(
                f"color: {axis_text}; font-family: {T.FONT}; font-size: {T.FS_SM}; "
                f"font-weight: {T.FW_BOLD}; letter-spacing: 0.5px; border: none;"
            )
        if hasattr(self, "lbl_chart_temp"):
            self.lbl_chart_temp.setStyleSheet(
                f"color: {chart_temp}; font-family: Consolas; font-size: {T.FS_MD}; "
                f"font-weight: {T.FW_BOLD}; border: none;"
            )
        if hasattr(self, "lbl_p_limit"):
            self.lbl_p_limit.setStyleSheet(
                f"color: #e05252; font-family: Consolas; font-size: {T.FS_XS}; "
                f"font-weight: {T.FW_SEMI}; border: 1px solid #e05252; "
                f"border-radius: 8px; padding: 1px 8px;"
            )
        for idx, p_name in enumerate(getattr(self, "pressure_order", [])):
            if p_name in getattr(self, "chart_p_labels", {}):
                lbl, _c = self.chart_p_labels[p_name]
                c = series_colors[idx % len(series_colors)]
                self.chart_p_labels[p_name] = (lbl, c)
                lbl.setStyleSheet(
                    f"color: {c}; font-family: Consolas; font-size: {T.FS_SM}; "
                    f"font-weight: {T.FW_SEMI}; border: none;"
                )

        # 위상 트랙 시리즈 색 (레인별 액센트, 팔레트 추종)
        _pp = palette or (Dark if is_dark else Light)
        for _k, _crv in getattr(self, "crv_phases", {}).items():
            _col = self._phase_lane_color(_k, _pp)
            _c = QColor(_col)
            _c.setAlphaF(0.22)
            _crv.setPen(pg.mkPen(_col, width=2.0))
            try:
                _crv.setFillBrush(pg.mkBrush(_c))
            except Exception:
                pass

    def _style_pump_status_label(self, label: QLabel, running: bool, is_dark: bool):
        if is_dark:
            idle_bg = DarkExtras.STATUS_IDLE_BG
            idle_fg = DarkExtras.STATUS_IDLE
            idle_bd = Dark.BORDER_LIGHT
            run_bg = DarkExtras.STATUS_RUN_BG
            run_fg = DarkExtras.STATUS_RUN
            run_bd = Dark.ACCENT_GREEN_DARK
        else:
            idle_bg = LightExtras.STATUS_IDLE_BG
            idle_fg = LightExtras.STATUS_IDLE
            idle_bd = Light.BORDER_PRIMARY
            run_bg = LightExtras.STATUS_RUN_BG
            run_fg = LightExtras.STATUS_RUN
            run_bd = Light.ACCENT_GREEN_DARK

        if running:
            label.setText("RUN")
            label.setStyleSheet(
                f"background:{run_bg}; color:{run_fg}; border:1px solid {run_bd}; border-radius: {T.R_MD}; padding: 3px 10px; font-size: {T.FS_XS}; font-weight: {T.FW_BOLD};"
            )
        else:
            label.setText("IDLE")
            label.setStyleSheet(
                f"background:{idle_bg}; color:{idle_fg}; border:1px solid {idle_bd}; border-radius: {T.R_MD}; padding: 3px 10px; font-size: {T.FS_XS}; font-weight: {T.FW_BOLD};"
            )

    # ── 장비 상태 3값 (2026-08-25) ──────────────────────────────
    @staticmethod
    def _dev_status(obj, run_attr="running"):
        """RUN / IDLE / OFFLINE — 미연결(is_connected=False)·None·Mock 폴백 = OFFLINE.

        run_attr 는 truthy 판정(숫자 setpoint > 0 도 RUN 취급 — MFC _sp, 히터 target_temp).
        """
        if obj is None:
            return "OFFLINE"
        if type(obj).__name__.startswith("Mock"):
            return "OFFLINE"
        if getattr(obj, "is_connected", True) is False:
            return "OFFLINE"
        try:
            if run_attr and getattr(obj, run_attr, False):
                return "RUN"
        except Exception:
            pass
        return "IDLE"

    def _style_status_badge(self, label, status, is_dark):
        """상태 배지 (2026-08-26 개편, 사용자 요청) — 텍스트는 RUN/IDLE 로 통일,
        연결 여부는 색으로 구분: 연결=초록(RUN·IDLE 모두), 미연결=회색 IDLE.
        내부 상태값 'OFFLINE' 은 유지하되 표시만 회색 IDLE 로 렌더."""
        if status == "RUN":
            self._style_pump_status_label(label, True, is_dark)
            return
        if is_dark:
            grn = (DarkExtras.STATUS_RUN_BG, DarkExtras.STATUS_RUN, Dark.ACCENT_GREEN_DARK)
            off = (DarkExtras.STATUS_IDLE_BG, DarkExtras.STATUS_IDLE, Dark.BORDER_LIGHT)
        else:
            grn = (LightExtras.STATUS_RUN_BG, LightExtras.STATUS_RUN, Light.ACCENT_GREEN_DARK)
            off = (LightExtras.STATUS_IDLE_BG, LightExtras.STATUS_IDLE, Light.BORDER_PRIMARY)
        bg, fg, bd = grn if status == "IDLE" else off
        label.setText("IDLE")
        label.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {bd}; "
            f"border-radius: {T.R_MD}; padding: 3px 10px; "
            f"font-size: {T.FS_XS}; font-weight: {T.FW_BOLD};")

    def _find_group_obj(self, mapping, p_name, suffix=""):
        tgt = self._norm_group(p_name)
        for k, v in (mapping or {}).items():
            base = k[:-len(suffix)] if (suffix and k.endswith(suffix)) else (k if not suffix else None)
            if base is not None and self._norm_group(base) == tgt:
                return v
        return None

    def _update_group_devices(self, is_dark):
        """그룹 하단 구성 장비(모터·12way·3way) — 장비 행과 동일한 배지 UI (2026-08-25)."""
        app = self.app
        P = Dark if is_dark else Light
        pumps = getattr(app, "pumps", {}) or {}
        valves = getattr(app, "valves", {}) or {}
        _name_ss = (f"font-size: {T.FS_XS}; padding-left: 16px; "
                    f"color: {getattr(P, 'TEXT_SECONDARY', '#8b8d98')};")
        for p_name, subdevs in getattr(self, "group_dev_labels", {}).items():
            objs = {"모터": (self._find_group_obj(pumps, p_name), "running"),
                    "12way": (self._find_group_obj(valves, p_name, "_Selector"), None),
                    "3way": (self._find_group_obj(valves, p_name, "_Switcher"), None)}
            for dv, (ln, lb) in subdevs.items():
                obj, ra = objs[dv]
                ln.setStyleSheet(_name_ss)
                self._style_status_badge(lb, self._dev_status(obj, ra), is_dark)

    def _update_device_statuses(self, is_dark):
        """보조 장비 상태 행 갱신 (1Hz, 캐시된 속성만 읽음 — 시리얼 I/O 없음)."""
        app = self.app
        valves = getattr(app, "valves", {}) or {}
        objs = {"valve:Outlet": valves.get("Outlet"),
                "mfc": getattr(app, "mfc", None),
                "push_pump": getattr(app, "push_pump", None),
                "collector": getattr(app, "collector", None),
                "heater": getattr(app, "heater", None),
                "phase_sensor": getattr(app, "phase_sensor", None),
                "level_sensor": getattr(app, "level_sensor", None)}
        run_attrs = {"mfc": "_sp", "heater": "target_temp",
                     "valve:Outlet": None, "phase_sensor": None,
                     "level_sensor": None, "collector": None}
        for key, lbl in getattr(self, "device_status_labels", {}).items():
            st = self._dev_status(objs.get(key), run_attrs.get(key, "running"))
            self._style_status_badge(lbl, st, is_dark)

    def apply_theme(self, is_dark: bool, palette=None):
        p = palette
        if p:
            accent = p.ACCENT_BLUE
            accent_dark = p.ACCENT_BLUE_DARK
            if is_dark:
                canvas_bg = p.BG_PRIMARY
                hero_bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {DarkExtras.HERO_START}, stop:0.58 {DarkExtras.HERO_MID}, stop:1 {DarkExtras.HERO_END})"
                hero_border = p.BORDER_PRIMARY
                hero_title = "#f8fafc"
                hero_sub = p.TEXT_SECONDARY
                # LIVE 배지 — 액센트 토큰에서 유도 (구 오렌지 rgba 하드코딩 제거)
                badge_bg = rgba(accent, 0.18)
                badge_bd = rgba(accent, 0.45)
                badge_fg = "#ffffff"
                card_bg = p.BG_SECONDARY
                card_bd = p.BORDER_PRIMARY
                text_primary = p.TEXT_PRIMARY
                text_secondary = p.TEXT_SECONDARY
                group_bg = p.BG_SECONDARY
                group_bd = p.BORDER_PRIMARY
                map_bg = p.BG_DARK
                health_bg = p.BG_ALTERNATE
                temp_color = p.ACCENT_RED
                outlet_color = p.ACCENT_CYAN
                pb_bg = p.BG_TERTIARY
                pb_chunk = accent
                tab_idle_bg = p.BG_TERTIARY
                chart_temp = DarkExtras.CHART_TEMP
                chart_pressure = list(get_chart_pressure_series(True))
            else:
                canvas_bg = p.BG_PRIMARY
                hero_bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {LightExtras.HERO_START}, stop:0.55 {LightExtras.HERO_MID}, stop:1 {LightExtras.HERO_END})"
                hero_border = accent_dark
                hero_title = "#ffffff"
                hero_sub = "#dbeaff"  # 블루 히어로 그라디언트 위 한색 틴트 (구 웜톤 #ffeadd)
                badge_bg = "rgba(255,255,255,0.24)"
                badge_bd = "rgba(255,255,255,0.5)"
                badge_fg = "#ffffff"
                card_bg = p.BG_SECONDARY
                card_bd = p.BORDER_PRIMARY
                text_primary = p.TEXT_PRIMARY
                text_secondary = p.TEXT_SECONDARY
                group_bg = p.BG_SECONDARY
                group_bd = p.BORDER_PRIMARY
                map_bg = p.BG_SECONDARY
                health_bg = p.BG_ALTERNATE
                temp_color = p.ACCENT_RED
                outlet_color = p.ACCENT_PURPLE
                pb_bg = p.BG_TERTIARY
                pb_chunk = accent
                tab_idle_bg = p.BG_TERTIARY
                chart_temp = LightExtras.CHART_TEMP
                chart_pressure = list(get_chart_pressure_series(False))
        elif is_dark:
            canvas_bg = Dark.BG_PRIMARY
            hero_bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {DarkExtras.HERO_START}, stop:0.58 {DarkExtras.HERO_MID}, stop:1 {DarkExtras.HERO_END})"
            hero_border = Dark.BORDER_PRIMARY
            hero_title = "#f8fafc"
            hero_sub = Dark.TEXT_SECONDARY
            badge_bg = rgba(Dark.ACCENT_BLUE, 0.18)
            badge_bd = rgba(Dark.ACCENT_BLUE, 0.45)
            badge_fg = "#ffffff"
            card_bg = Dark.BG_SECONDARY
            card_bd = Dark.BORDER_PRIMARY
            text_primary = Dark.TEXT_PRIMARY
            text_secondary = Dark.TEXT_SECONDARY
            group_bg = Dark.BG_SECONDARY
            group_bd = Dark.BORDER_PRIMARY
            map_bg = Dark.BG_DARK
            health_bg = Dark.BG_ALTERNATE
            temp_color = Dark.ACCENT_RED
            outlet_color = Dark.ACCENT_CYAN
            pb_bg = Dark.BG_TERTIARY
            pb_chunk = Dark.ACCENT_BLUE
            tab_idle_bg = Dark.BG_TERTIARY
            chart_temp = DarkExtras.CHART_TEMP
            chart_pressure = list(get_chart_pressure_series(True))
        else:
            canvas_bg = Light.BG_PRIMARY
            hero_bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {LightExtras.HERO_START}, stop:0.55 {LightExtras.HERO_MID}, stop:1 {LightExtras.HERO_END})"
            hero_border = Light.ACCENT_BLUE_DARK
            hero_title = "#ffffff"
            hero_sub = "#dbeaff"  # 블루 히어로 그라디언트 위 한색 틴트 (구 웜톤 #ffeadd)
            badge_bg = "rgba(255,255,255,0.24)"
            badge_bd = "rgba(255,255,255,0.5)"
            badge_fg = "#ffffff"
            card_bg = Light.BG_SECONDARY
            card_bd = Light.BORDER_PRIMARY
            text_primary = Light.TEXT_PRIMARY
            text_secondary = Light.TEXT_SECONDARY
            group_bg = Light.BG_SECONDARY
            group_bd = Light.BORDER_PRIMARY
            map_bg = Light.BG_SECONDARY
            health_bg = Light.BG_ALTERNATE
            temp_color = Light.ACCENT_RED
            outlet_color = Light.ACCENT_PURPLE
            pb_bg = Light.BG_TERTIARY
            pb_chunk = Light.ACCENT_BLUE
            tab_idle_bg = Light.BG_TERTIARY
            chart_temp = LightExtras.CHART_TEMP
            chart_pressure = list(get_chart_pressure_series(False))

        self.canvas.setStyleSheet(f"QWidget#DashboardCanvas {{ background: {canvas_bg}; }}")
        self.hero.setStyleSheet(f"QFrame#DashHero {{ border-radius: {T.R_XL}; border: 1px solid {hero_border}; background: {hero_bg}; }}")

        self.lbl_title.setStyleSheet(f"color: {hero_title}; font-size: {T.FS_XL}; font-weight: {T.FW_BOLD}; background: transparent;")
        self.lbl_sub.setStyleSheet(f"color: {hero_sub}; font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; background: transparent;")
        self.lbl_clock.setStyleSheet(
            f"background:{badge_bg}; border:1px solid {badge_bd}; border-radius: 14px; color:{badge_fg}; font-size:{T.FS_XS}; font-weight:{T.FW_BOLD}; padding:4px 12px;"
        )

        for card in self.metric_cards:
            card.setStyleSheet(f"QFrame#MetricCard {{ border-radius: {T.R_XL}; border: 1px solid {card_bd}; background: {card_bg}; }}")
            for label in card.findChildren(QLabel):
                if label.objectName() == "MetricTitle":
                    label.setStyleSheet(f"font-size:{T.FS_XS}; color:{text_secondary}; font-weight:{T.FW_SEMI}; background: transparent;")
                elif label.objectName() == "MetricValue":
                    label.setStyleSheet(f"font-size:{T.FS_XL}; color:{text_primary}; font-weight:{T.FW_BOLD}; background: transparent;")
                else:
                    label.setStyleSheet(f"font-size:{T.FS_XS}; color:{text_secondary}; background: transparent;")

        self.workspace_card.setStyleSheet(
            f"QGroupBox {{ background:{group_bg}; border:1px solid {group_bd}; border-radius: {T.R_LG}; margin-top: 14px; }}"
            f"QGroupBox::title {{ color: {text_secondary}; font-size: {T.FS_SM}; font-weight: {T.FW_BOLD}; left: 10px; top: 1px; padding: 2px 8px; background: transparent; }}"
        )
        self.health_card.setStyleSheet(
            f"QGroupBox {{ background:{group_bg}; border:1px solid {group_bd}; border-radius: {T.R_LG}; margin-top: 14px; }}"
            f"QGroupBox::title {{ color: {text_secondary}; font-size: {T.FS_SM}; font-weight: {T.FW_BOLD}; left: 10px; top: 1px; padding: 2px 8px; background: transparent; }}"
        )
        self.main_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid " + group_bd + "; border-radius: " + T.R_LG + "; background: " + group_bg + "; }"
            "QTabBar::tab { min-width: 90px; min-height: 30px; padding: 4px 10px; "
            "border: 1px solid " + group_bd + "; border-bottom: none; border-top-left-radius: " + T.R_MD + "; border-top-right-radius: " + T.R_MD + "; "
            "background: " + tab_idle_bg + "; color: " + text_secondary + "; font-weight: " + T.FW_BOLD + "; }"
            "QTabBar::tab:selected { background: " + group_bg + "; color: " + text_primary + "; border-bottom: 2px solid " + pb_chunk + "; }"
        )

        self.flow_viz.setStyleSheet(f"border: 1px solid {group_bd}; border-radius: {T.R_LG}; background: {map_bg};")
        if hasattr(self.flow_viz, "apply_theme"):
            self.flow_viz.apply_theme(is_dark=is_dark)

        self.status_frame.setStyleSheet(f"QFrame {{ border:1px solid {group_bd}; border-radius: {T.R_LG}; background: {health_bg}; }}")

        self.crv_t.setPen(pg.mkPen(chart_temp, width=2.6))
        for idx, p_name in enumerate(self.pressure_order):
            if p_name in self.crv_p:
                self.crv_p[p_name].setPen(pg.mkPen(chart_pressure[idx % len(chart_pressure)], width=2.2))

        for p_name, lbl in self.pump_status_labels.items():
            _st = getattr(self, "_pump_state", {}).get(
                p_name, "RUN" if self._pump_running.get(p_name, False) else "IDLE")
            self._style_status_badge(lbl, _st, is_dark)
        self._update_group_devices(is_dark)
        self._update_device_statuses(is_dark)
        if getattr(self, "_status_sep", None) is not None:
            _P = Dark if is_dark else Light
            self._status_sep.setStyleSheet(
                f"background:{getattr(_P, 'BORDER_LIGHT', '#3a3a3a')}; border:none;")

        self._set_plot_theme(is_dark, p)

    def set_phase_track_visible(self, visible: bool):
        """위상 카드(센서1/2) 표시 토글 + 'Time (sec)' x축 라벨 소유권 이관
        (최하단 표시 플롯이 담당). 부팅/핫리로드 시 app_monitoring 이 호출."""
        if not hasattr(self, "phase_cards"):
            return
        visible = bool(visible)
        for _c in self.phase_cards.values():
            _c.setVisible(visible)
        if visible:
            self.plot_pressure.setLabel("bottom", "")
            self.plot_pressure.getAxis("bottom").setStyle(showValues=False)
            _last = self.PHASE_KEYS[-1]
            for _pk, _pl in self.plot_phases.items():
                _own = (_pk == _last)
                _pl.setLabel("bottom", "Time (sec)" if _own else "")
                _pl.getAxis("bottom").setStyle(showValues=_own)
        else:
            self.plot_pressure.setLabel("bottom", "Time (sec)")
            self.plot_pressure.getAxis("bottom").setStyle(showValues=True)

    def _phase_lane_color(self, key, P):
        return getattr(P, self.PHASE_LANE_COLORS.get(key, "ACCENT_GREEN"))

    def update_phase(self, series):
        """위상센서 독립 카드(센서1/2) 갱신 (app_monitoring.update_phase_data, 1Hz).

        series = {채널: {"t": [...], "v": [...]}}. stepMode=True 계약: x=len(y)+1 —
        마지막 샘플 +1s(폴 주기) 홀드 패딩. y = 0(GAS)/1(LIQUID) 원값.
        ⚠ 이 센서는 유량계가 아니라 '관 내용물' 감지기 — 흐름이 있어도 상(相)이
        같으면 평평한 게 정상이며, 기/액 경계가 통과할 때만 계단이 생긴다."""
        if not hasattr(self, "crv_phases"):
            return
        keys = [k for k in self.PHASE_KEYS
                if (series or {}).get(k, {}).get("t")]
        if not keys:
            return
        if not any(c.isVisible() for c in self.phase_cards.values()):
            self.set_phase_track_visible(True)   # 데이터 도착=센서 존재 (핫리로드 안전망)
        P = Dark if getattr(self.app, "is_dark_mode", True) else Light
        for k in keys:
            s = series[k]
            x = list(s["t"]) + [s["t"][-1] + 1.0]
            self.crv_phases[k].setData(x, list(s["v"]))
            lbl = self.phase_chips.get(k)
            if lbl is None or not s["v"]:
                continue
            if s["v"][-1]:
                txt, col = "● 1 LIQUID", self._phase_lane_color(k, P)
            else:
                txt, col = "○ 0 GAS", P.TEXT_DISABLED
            lbl.setText(txt)
            lbl.setStyleSheet(
                f"color: {col}; font-family: Consolas; font-size: {T.FS_SM}; "
                f"font-weight: {T.FW_SEMI}; border: none;")

    def update_metrics(self, temp: float, pressures: Dict[str, float], pump_status: Dict[str, bool], outlet_pos):
        # 차트 헤더 현재값 (실시간)
        if hasattr(self, "lbl_chart_temp"):
            self.lbl_chart_temp.setText(f"{temp:.1f} °C")
        # 목표 온도선 (히터 목표가 있을 때만)
        if hasattr(self, "line_temp_target"):
            tgt = float(getattr(getattr(self.app, "heater", None), "target_temp", 0.0) or 0.0)
            if tgt > 0:
                self.line_temp_target.setPos(tgt)
                self.line_temp_target.setVisible(True)
            else:
                self.line_temp_target.setVisible(False)
        # 최신값 도트
        dh = getattr(self.app, "dh", None)
        if dh and dh.get("t") and hasattr(self, "dot_t"):
            self.dot_t.setData([dh["t"][-1]], [dh["temp"][-1]])
            for p_name, dot in getattr(self, "dot_p", {}).items():
                if dh.get(p_name):
                    dot.setData([dh["t"][-1]], [dh[p_name][-1]])
        if hasattr(self, "chart_p_labels"):
            for p_name, (lbl, _c) in self.chart_p_labels.items():
                val = pressures.get(p_name)
                lbl.setText(f"● {p_name}  {val:.2f}" if val is not None else f"● {p_name}  --")
        self.metric_labels["active_pumps"].setText(f"{sum(1 for v in pump_status.values() if v)}/{len(pump_status)}")
        self.metric_labels["max_pressure"].setText(f"{max(pressures.values()) if pressures else 0.0:.2f} bar")

        total_flow = 0.0
        for _, p_obj in self.app.pumps.items():
            if getattr(p_obj, "running", False):
                total_flow += float(getattr(p_obj, "target_flow", 0.0))
        self.metric_labels["total_flow"].setText(f"{total_flow:.2f} mL/min")

        is_dark = getattr(self.app, "is_dark_mode", True)
        status_lookup = {self._norm_group(k): bool(v) for k, v in pump_status.items()}
        for p_name, lbl in self.pump_status_labels.items():
            running = status_lookup.get(self._norm_group(p_name), False)
            self._pump_running[p_name] = running
            # 미연결 모터는 RUN/IDLE 이전에 OFFLINE 으로 명시 (2026-08-25)
            if running:
                st = "RUN"
            else:
                motor = self._find_group_obj(getattr(self.app, "pumps", {}), p_name)
                st = "OFFLINE" if self._dev_status(motor, run_attr=None) == "OFFLINE" else "IDLE"
            self._pump_state[p_name] = st
            self._style_status_badge(lbl, st, is_dark)
        self._update_group_devices(is_dark)
        self._update_device_statuses(is_dark)

        # @codesyncer(B): 데이터 신선도 하트비트 — lbl_clock 이 상수 "LIVE"라 폴링 스레드가
        #   죽어도 라이브처럼 보이는 위험(ISA-101: stale 값은 명시돼야 함). 갱신마다 시각을
        #   찍고, 별도 1s 타이머가 마지막 갱신 후 임계 초과 시 'STALE'(앰버)로 전환.
        import time
        self._last_update_mono = time.monotonic()
        if not hasattr(self, "_fresh_timer"):
            self._fresh_timer = QTimer(self)
            self._fresh_timer.timeout.connect(self._check_freshness)
            self._fresh_timer.start(1000)
        self._render_clock(True)

    def _render_clock(self, fresh):
        import time
        P = Dark if getattr(self.app, "is_dark_mode", True) else Light
        ts = time.strftime("%H:%M:%S")
        dot, txt, fg = ("●", "LIVE", P.STATE_RUN) if fresh else ("○", "STALE", P.STATE_WARN)
        self.lbl_clock.setText(f"{dot} {txt}  {ts}")
        self.lbl_clock.setStyleSheet(
            f"background: transparent; border:1px solid {fg}; border-radius:14px; "
            f"color:{fg}; font-size:{T.FS_XS}; font-weight:{T.FW_BOLD}; padding:4px 12px;")

    def _check_freshness(self):
        """마지막 update_metrics 후 4초 초과면 lbl_clock 을 STALE 로 표시 (폴링 정지 감지)."""
        import time
        last = getattr(self, "_last_update_mono", None)
        if last is not None and (time.monotonic() - last) > 4.0:
            self._render_clock(False)
