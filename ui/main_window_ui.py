"""메인 윈도우 UI 조립 — 사이드바/모니터카드/페이지스택/로그 패널

@codesyncer-decision: main.py 책임 분리 (믹스인 패턴)
- 기존 main.py(941줄)에 UI 조립·모니터링·운전제어·핫리로드·원격명령이
  전부 집중되어 변경 영향 범위를 추적하기 어려웠음
- 탭/매니저들이 `app.속성`으로 강결합되어 있어 컴포지션 전환은 수백 개
  참조 수정이 필요 → 믹스인으로 self 네임스페이스를 보존하면서 파일만 분리
  (행동 100% 보존, 단계적 후속 리팩토링의 발판)
- 메서드 본문은 main.py에서 그대로 이동 (수정 없음)
"""

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QGroupBox, QLabel, QLCDNumber, QPushButton, QTextBrowser,
                             QSplitter, QFrame, QSizePolicy, QScrollArea, QTabWidget)
from PyQt5.QtCore import Qt

from ui.colors import DarkPalette as Dark, T, DarkExtras
from ui.toggle_switch import ToggleSwitch
from ui.tab_dashboard import DashboardTab
from ui.tab_sequence import SequenceTab
from ui.tab_manual import ManualTab
from ui.tab_calculator import CalculatorTab


class MainWindowUIMixin:
    """메인 윈도우 레이아웃 조립 + 탭 참조 동기화 + 테마 레지스트리"""

    def init_ui(self):
        w = QWidget()
        self.setCentralWidget(w)

        shell = QHBoxLayout(w)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.viewport = QWidget()
        self.viewport.setObjectName("MainViewport")
        self.viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Fill full window width responsively (no fixed 1920 viewport).
        shell.addWidget(self.viewport, 1)

        root = QHBoxLayout(self.viewport)
        root.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        root.setSpacing(T.SP_SM)

        # ===============================
        # Left Sidebar
        # ===============================
        sidebar = QFrame()
        sidebar.setObjectName("MainSidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(260)
        self.sidebar_frame = sidebar
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        sb.setSpacing(10)

        brand_wrap = QVBoxLayout()
        
        brand_wrap.setSpacing(2)
        self.lbl_brand = QLabel("VORONOI")
        self.lbl_brand.setStyleSheet(f"font-size: {T.FS_XXL}; font-weight: {T.FW_BOLD};")
        self.lbl_brand_sub = QLabel("Flowchemistry Platform")
        self.lbl_brand_sub.setStyleSheet(f"font-size: {T.FS_XS};")
        brand_wrap.addWidget(self.lbl_brand)
        brand_wrap.addWidget(self.lbl_brand_sub)
        sb.addLayout(brand_wrap)

        nav_group = QGroupBox("Navigation")
        nav_l = QVBoxLayout(nav_group)
        nav_l.setContentsMargins(T.SP_SM, T.SP_MD, T.SP_SM, T.SP_SM)
        nav_l.setSpacing(T.SP_SM)

        self.main_nav_buttons = []
        # 툴팁 1줄 원칙 (F4) — 페이지가 이미 보여주는 내용을 복창하지 않는다
        self.btn_nav_dashboard = self._create_nav_button("Dashboard", 0)
        self.btn_nav_dashboard.setToolTip("실시간 공정 현황 — 온도·압력·진행 단계")
        self.btn_nav_calculator = self._create_nav_button("Calculator", 3)
        self.btn_nav_calculator.setToolTip("농도 · 희석 · RTD 계산 도구")
        self.btn_nav_sequence = self._create_nav_button("Sequence", 1)
        self.btn_nav_sequence.setToolTip("실험 조건 편집 및 자동 실행")
        self.btn_nav_manual = self._create_nav_button("Manual", 2)
        self.btn_nav_manual.setToolTip("장비 개별 수동 제어 — 토출(Infuse)/흡입(Withdraw) · 경로 · 히터 · 분취기")
        nav_l.addWidget(self.btn_nav_dashboard)
        nav_l.addWidget(self.btn_nav_calculator)
        nav_l.addWidget(self.btn_nav_sequence)
        nav_l.addWidget(self.btn_nav_manual)
        sb.addWidget(nav_group)

        self.g_theme = QGroupBox("Theme")
        theme_layout = QHBoxLayout()
        theme_layout.setContentsMargins(T.SP_SM, 10, T.SP_SM, T.SP_SM)
        theme_layout.setSpacing(T.SP_SM)

        self.lbl_theme_status = QLabel("다크 모드")
        self.lbl_theme_status.setStyleSheet(
            f"font-weight: {T.FW_SEMI}; font-size: {T.FS_SM}; color: {Dark.TEXT_PRIMARY};"
        )

        # @codesyncer-decision: "☾" 버튼 → 좌우 슬라이딩 스위치(사용자 요청).
        #   checked=True=다크(노브 우측). toggled(새 상태) → toggle_theme(플립).
        #   테마 변경 시 theme_manager 가 setChecked(is_dark, emit=False) 로 재동기화
        #   (시그널 루프 방지). 시작은 다크.
        self.btn_theme = ToggleSwitch(checked=True)
        self.btn_theme.toggled.connect(lambda _checked: self.toggle_theme())

        theme_layout.addWidget(self.lbl_theme_status)
        theme_layout.addStretch()
        theme_layout.addWidget(self.btn_theme)
        self.g_theme.setLayout(theme_layout)
        sb.addWidget(self.g_theme)

        g_method = QGroupBox("Method")
        ml = QVBoxLayout()
        ml.setContentsMargins(T.SP_SM, 10, T.SP_SM, T.SP_SM)
        ml.setSpacing(6)

        self.btn_load_m = QPushButton("Load")
        self.btn_load_m.setMinimumHeight(32)
        self.btn_load_m.clicked.connect(self.method_io.on_load_method)
        self.btn_load_m.setToolTip(
            "<b>Method Load</b><br>"
            "이전에 저장한 실험 조건을 불러옵니다.<br>"
            "시약 설정, Step 조건, 장비 파라미터가 모두 복원됩니다."
        )

        self.btn_save_m = QPushButton("Save")
        self.btn_save_m.setMinimumHeight(32)
        self.btn_save_m.clicked.connect(self.method_io.on_save_method)
        self.btn_save_m.setToolTip(
            "<b>Method Save</b><br>"
            "현재 실험 조건을 파일로 저장합니다.<br>"
            "이전에 Load/Save한 파일이 있으면 그 파일에 즉시 덮어쓰기 합니다.<br>"
            "(직전 버전은 자동으로 .bak 파일로 백업됩니다.)"
        )

        # @codesyncer-decision: Save / Save As 분리
        #   Save = 현재 파일에 즉시 덮어쓰기 (반복 수정 시 다이얼로그 없음)
        #   Save As = 항상 파일 다이얼로그를 띄워 새 위치/이름으로 저장
        self.btn_save_as_m = QPushButton("Save As")
        self.btn_save_as_m.setMinimumHeight(32)
        self.btn_save_as_m.clicked.connect(self.method_io.on_save_method_as)
        self.btn_save_as_m.setToolTip(
            "<b>Method Save As</b><br>"
            "다른 이름 또는 새 위치에 저장합니다.<br>"
            "저장 후에는 Save 버튼이 이 파일에 덮어쓰도록 갱신됩니다."
        )

        self.btn_setting = QPushButton("Setting")
        self.btn_setting.setMinimumHeight(32)
        self.btn_setting.clicked.connect(self._open_hardware_config)
        self.btn_setting.setToolTip(
            "<b>Hardware Setting</b><br>"
            "장비 구성을 변경합니다.<br>"
            "• 반응기 튜빙 규격 (길이, 내경)<br>"
            "• 펌프 충전/프라이밍 속도<br>"
            "• 온도 허용 범위, 안전 제한값<br>"
            "<i>장비를 교체하거나 튜빙을 변경했을 때 업데이트하세요.</i>"
        )

        ml.addWidget(self.btn_setting)
        ml.addWidget(self.btn_load_m)
        ml.addWidget(self.btn_save_m)
        ml.addWidget(self.btn_save_as_m)
        g_method.setLayout(ml)
        sb.addWidget(g_method)

        sb.addStretch()

        # ===============================
        # Right Content
        # ===============================
        content_wrap = QVBoxLayout()
        content_wrap.setContentsMargins(0, 0, 0, 0)
        content_wrap.setSpacing(T.SP_SM)

        monitor_card = QGroupBox("Live Sensor")
        self.monitor_card = monitor_card
        monitor_l = QHBoxLayout(monitor_card)
        monitor_l.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        monitor_l.setSpacing(T.SP_SM)

        monitor_center = QVBoxLayout()
        monitor_center.setSpacing(T.SP_XS)

        sensors_grid = QGridLayout()
        sensors_grid.setHorizontalSpacing(6)
        sensors_grid.setVerticalSpacing(6)
        sensor_cards = []

        self.lcd_t = QLCDNumber(5)
        self.lcd_t.setMinimumHeight(36)
        self.lcd_t.setSegmentStyle(QLCDNumber.Flat)
        sensor_cards.append(self.wrap_lcd("Reactor Temp (C)", self.lcd_t, DarkExtras.SENSOR_TEMP))

        self.lcds = {}
        for p in self.cfg.ACTIVE_PUMPS:
            lcd = QLCDNumber(5)
            lcd.setMinimumHeight(36)
            lcd.setSegmentStyle(QLCDNumber.Flat)
            self.lcds[p] = lcd
            sensor_cards.append(self.wrap_lcd(f"{p} Pressure (bar)", lcd, DarkExtras.SENSOR_PRESSURE))

        # @codesyncer-decision: 센서 LCD 는 항상 한 줄 — 펌프가 늘어나도
        #   행 랩 없이 균등 분할(카드 수 = 열 수). 이전 max_cols=4 는 5개째
        #   센서(Group_D)가 2행으로 떨어져 정렬이 깨졌음.
        for idx, card in enumerate(sensor_cards):
            sensors_grid.addWidget(card, 0, idx)
            sensors_grid.setColumnStretch(idx, 1)
        monitor_center.addLayout(sensors_grid)

        self.status_frame = QFrame()
        self.status_frame.setStyleSheet(
            f"background: {Dark.BG_DARK}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: {T.R_MD}; padding: 4px;"
        )
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(T.SP_MD, 6, T.SP_MD, 6)
        status_layout.setSpacing(T.SP_MD)

        self.lbl_status_title = QLabel("STATUS")
        self.lbl_status_title.setStyleSheet(
            f"color: {Dark.TEXT_SECONDARY}; font-size: {T.FS_XS}; font-weight: {T.FW_BOLD}; font-family: {T.FONT}; letter-spacing: 1px;"
        )
        self.lbl_status_title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.lbl_status_title.setFixedWidth(74)

        self.lbl_status = QLabel("Idle")
        self.lbl_status.setStyleSheet(
            f"font-size: {T.FS_XL}; font-weight: {T.FW_BOLD}; color: {Dark.ACCENT_GREEN}; font-family: {T.FONT};"
        )
        self.lbl_status.setAlignment(Qt.AlignVCenter | Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)

        self.lbl_phase = QLabel("")
        self.lbl_phase.setStyleSheet(
            f"font-size: {T.FS_SM}; font-weight: {T.FW_SEMI}; color: {Dark.ACCENT_CYAN}; font-family: {T.FONT};"
        )
        self.lbl_phase.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.lbl_phase.setMinimumWidth(160)

        status_layout.addWidget(self.lbl_status_title, 0)
        status_layout.addWidget(self.lbl_status, 1)
        status_layout.addWidget(self.lbl_phase, 0)
        monitor_center.addWidget(self.status_frame)

        monitor_l.addLayout(monitor_center, 1)

        control_card = QGroupBox("Control")
        self.control_card = control_card
        control_card.setMinimumWidth(152)
        control_l = QVBoxLayout(control_card)
        control_l.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        control_l.setSpacing(6)

        self.btn_p = QPushButton("Pause")
        self.btn_p.setCheckable(True)
        # @codesyncer-decision: 타겟 크기 위계 (WCAG 2.2 / ISO 13850)
        # - 일반 40 < 주요동작 Pause 44 < 임계동작 E-Stop 52
        # - 비상 시 정밀 조준 없이 누를 수 있도록 E-Stop이 최대 타겟
        self.btn_p.setMinimumHeight(T.H_BTN_LG)
        self.btn_p.clicked.connect(self.tog_pause)

        self.btn_emergency = QPushButton("EMERGENCY STOP")
        self.btn_emergency.setMinimumHeight(T.H_BTN_XL)
        self.btn_emergency.clicked.connect(self.estop)

        control_l.addWidget(self.btn_p)
        control_l.addWidget(self.btn_emergency)
        control_l.addStretch()
        monitor_l.addWidget(control_card, 0)

        monitor_card.setMaximumHeight(200)
        content_wrap.addWidget(monitor_card, 0)

        # Page stack
        self.page_stack = QTabWidget()
        self.page_stack.tabBar().hide()
        self.dash_tab = DashboardTab(self)
        self.seq_tab = SequenceTab(self)
        self.man_tab = ManualTab(self)
        self.calc_tab = CalculatorTab(self)
        self.page_stack.addTab(self.dash_tab, "DASHBOARD")
        self.page_stack.addTab(self.seq_tab, "SEQUENCE")
        self.page_stack.addTab(self.man_tab, "MANUAL")
        self.page_stack.addTab(self.calc_tab, "CALCULATOR")
        self.page_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.page_stack.setMinimumSize(0, 0)
        self.tabs = self.page_stack

        self._sync_seq_refs()
        self._sync_man_refs()

        self.theme_mgr.register_widgets('dashboard', {
            'dash_tab': self.dash_tab,
            'flow_viz': self.dash_tab.flow_viz,
            'plot_widgets': self.dash_tab.plot_widgets,
        })
        self.theme_mgr.register_widgets('main', {
            'sidebar_frame': self.sidebar_frame,
            'main_nav_buttons': self.main_nav_buttons,
            'monitor_card': self.monitor_card,
            'control_card': self.control_card,
            'lbl_brand': self.lbl_brand,
            'lbl_brand_sub': self.lbl_brand_sub,
            'btn_pause': self.btn_p,
            'btn_emergency': self.btn_emergency,
        })
        self.theme_mgr.register_widgets('sequence', {
            'lbl_start_tube_seq': self.seq_tab.lbl_start_tube_seq,
            'seq_tab': self.seq_tab,
        })
        self.theme_mgr.register_widgets('calculator', {
            'calc_tab': self.calc_tab,
        })
        self._register_manual_theme_widgets()

        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.NoFrame)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        page_holder = QWidget()
        page_holder_l = QVBoxLayout(page_holder)
        page_holder_l.setContentsMargins(0, 0, 0, 0)
        page_holder_l.addWidget(self.page_stack)
        page_scroll.setWidget(page_holder)

        lg = QGroupBox("SYSTEM LOG")
        lg.setMinimumHeight(300)
        ll = QVBoxLayout()
        ll.setContentsMargins(T.SP_SM, T.SP_MD, T.SP_SM, T.SP_SM)
        ll.setSpacing(T.SP_XS)

        self.log_browser = QTextBrowser()
        self.log_browser.setStyleSheet(
            f"background:{Dark.BG_DARK}; color:{DarkExtras.LOG_TEXT}; border:1px solid {Dark.BORDER_PRIMARY}; border-radius:{T.R_LG}; font-size:{T.FS_MD};"
        )
        ll.addWidget(self.log_browser)
        lg.setLayout(ll)
        self.theme_mgr.register_widgets('main', {'log_browser': self.log_browser})

        body_splitter = QSplitter(Qt.Vertical)
        body_splitter.setHandleWidth(8)
        body_splitter.setChildrenCollapsible(False)
        body_splitter.addWidget(page_scroll)
        body_splitter.addWidget(lg)
        body_splitter.setStretchFactor(0, 4)
        body_splitter.setStretchFactor(1, 1)

        content_wrap.addWidget(body_splitter, 1)

        root.addWidget(sidebar, 0)
        root.addLayout(content_wrap, 1)

        self.theme_mgr.apply_current_theme()
        self._switch_main_page(0)

    def _create_nav_button(self, title, page_index):
        btn = QPushButton(title)
        btn.setCheckable(True)
        btn.setMinimumHeight(40)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                text-align: left;
                padding-left: 12px;
                font-size: {T.FS_MD};
                font-weight: {T.FW_BOLD};
                border-radius: {T.R_LG};
                border: 1px solid {Dark.BORDER_PRIMARY};
                background: {Dark.BG_TERTIARY};
                color: {Dark.TEXT_TERTIARY};
            }}
            QPushButton:hover {{
                background: {Dark.BG_HOVER};
                color: {Dark.TEXT_PRIMARY};
            }}
            QPushButton:checked {{
                background: {Dark.ACCENT_BLUE};
                border-color: {Dark.ACCENT_BLUE_DARK};
                color: #ffffff;
            }}
            """
        )
        btn.setProperty("page_index", page_index)
        btn.clicked.connect(lambda checked=False, i=page_index: self._switch_main_page(i))
        self.main_nav_buttons.append(btn)
        return btn

    def _switch_main_page(self, index):
        if hasattr(self, 'page_stack'):
            self.page_stack.setCurrentIndex(index)
        for btn in getattr(self, 'main_nav_buttons', []):
            btn.setChecked(btn.property("page_index") == index)

    def wrap_lcd(self, t, lcd, c):
        """센서 값을 표시하는 LCD 카드 위젯 생성."""
        if not hasattr(self, 'lcd_widgets'):
            self.lcd_widgets = []
        if not hasattr(self, 'lcd_labels'):
            self.lcd_labels = []
        self.lcd_widgets.append((lcd, c))

        w = QWidget()
        w.setMinimumSize(108, 58)
        v = QVBoxLayout(w)
        v.setContentsMargins(3, 3, 3, 3)
        v.setSpacing(2)
        lbl = QLabel(t)
        lbl.setStyleSheet(f"color: {Dark.TEXT_PRIMARY}; font-weight: {T.FW_SEMI}; font-size: {T.FS_XS};")
        lbl.setAlignment(Qt.AlignCenter)
        self.lcd_labels.append(lbl)
        lcd.setStyleSheet(
            f"color:{c}; background:{Dark.BG_DARK}; border:2px solid {Dark.BORDER_PRIMARY}; border-radius: {T.R_SM};"
        )
        v.addWidget(lbl)
        v.addWidget(lcd)
        return w

    def _sync_seq_refs(self):
        """Sequence 탭의 주요 위젯 참조를 메인 객체에 동기화한다."""
        self.prog = self.seq_tab.prog
        self.reagent_tables = self.seq_tab.reagent_tables
        self.sp_collector_start = self.seq_tab.sp_collector_start
        self.lbl_start_tube_seq = self.seq_tab.lbl_start_tube_seq

    def _sync_man_refs(self):
        """Manual 탭의 주요 위젯 참조를 메인 객체에 동기화한다."""
        self.lbl_reactor_temp = self.man_tab.lbl_reactor_temp
        self.lbl_manual_status = self.man_tab.lbl_manual_status

    def _register_manual_theme_widgets(self):
        """Manual 탭 위젯을 ThemeManager 레지스트리에 등록한다."""
        m = self.man_tab
        self.theme_mgr.register_widgets('manual', {
            'man_tab': m,
            'btn_waste': m.btn_waste,
            'btn_coll': m.btn_coll,
            'lbl_heater': m.lbl_heater,
            'btn_h_set': m.btn_h_set,
            'btn_h_off': m.btn_h_off,
            'lbl_valve': m.lbl_valve,
            'monitor_frame': m.monitor_frame,
            'lbl_current_temp_title': m.lbl_current_temp_title,
            'lbl_reactor_temp': m.lbl_reactor_temp,
            'separator_outlet': m.separator_outlet,
            'lbl_collector': m.lbl_collector,
            'lbl_collector_pos': m.lbl_collector_pos,
            'btn_collector_move': m.btn_collector_move,
            'btn_collector_back': m.btn_collector_back,
            'btn_collector_forward': m.btn_collector_forward,
            'btn_collector_home': m.btn_collector_home,
            'lbl_manual_status': m.lbl_manual_status,
            'lbl_target': m.lbl_target,
            'lbl_step': m.lbl_step,
            'lbl_tube': m.lbl_tube,
            'pump_card_widgets': m.pump_card_widgets,
            'lcd_collector_step': getattr(m, 'lcd_collector_step', None),
            'push_pump_group': getattr(m, 'push_pump_group', None),
            'lbl_push_flow': getattr(m, 'lbl_push_flow', None),
            'sp_push_flow': getattr(m, 'sp_push_flow', None),
            'btn_push_start': getattr(m, 'btn_push_start', None),
            'btn_push_stop': getattr(m, 'btn_push_stop', None),
            'lbl_push_pressure_title': getattr(m, 'lbl_push_pressure_title', None),
            'lbl_push_pressure': getattr(m, 'lbl_push_pressure', None),
        })

    def toggle_theme(self):
        """다크/라이트 모드 전환."""
        self.theme_mgr.toggle_theme()
