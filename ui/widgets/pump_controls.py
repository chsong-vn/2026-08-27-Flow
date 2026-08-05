# --- START OF FILE ui/widgets/pump_controls.py ---
from PyQt5.QtWidgets import (QWidget, QGroupBox, QVBoxLayout, QHBoxLayout,
                             QLabel, QDoubleSpinBox, QPushButton, QSpinBox,
                             QFrame, QButtonGroup, QRadioButton, QTabWidget,
                             QSizePolicy, QScrollArea, QComboBox, QProgressBar,
                             QGridLayout)
from PyQt5.QtCore import Qt, QTimer
import threading

from ui.colors import DarkPalette as Dark, LightPalette as Light, T
from ui.theme import (get_primary_button_style, get_secondary_button_style,
                      get_action_state_style, get_segment_track_style,
                      get_segment_button_style, get_stop_button_style)

# =============================================================================
# [PART 1] 공통 스타일/베이스
# =============================================================================
def _component_style(P, is_dark=True):
    """공통 컴포넌트 카드 스타일 생성."""
    return f"""
    ComponentCard {{
        background-color: {P.BG_SECONDARY};
        border-radius: {T.R_LG};
        border: 1px solid {P.BORDER_PRIMARY};
        padding: {T.SP_SM}px;
        margin-bottom: {T.SP_XS}px;
    }}
    QLabel {{
        color: {P.TEXT_PRIMARY};
        font-family: {T.FONT};
        font-weight: 600;
        font-size: {T.FS_SM};
        border: none;
        padding: 0px;
        background: transparent;
    }}
    QLabel#desc {{
        color: {P.TEXT_SECONDARY};
        font-family: {T.FONT};
        font-weight: normal;
        font-size: {T.FS_SM};
        font-style: italic;
    }}
    QPushButton {{
        background-color: {P.BG_TERTIARY};
        color: {P.TEXT_PRIMARY};
        border: 1px solid {P.BORDER_PRIMARY};
        border-radius: {T.R_SM};
        padding: 6px;
        font-family: {T.FONT};
        font-weight: 600;
        font-size: {T.FS_SM};
        min-height: {T.H_BTN}px;
    }}
    QPushButton:hover {{ background-color: {P.BG_HOVER}; }}
    QPushButton:pressed {{ background-color: {P.BG_PRESSED}; }}
    QPushButton:checked {{
        background-color: {P.ACCENT_BLUE};
        color: white;
        border-color: {P.ACCENT_BLUE_DARK};
    }}
    /* 밀집 채널 카드: 값 입력만 H_INPUT_SM(28) — 액션 버튼 타겟은 유지.
       3채널이 스크롤 없이 수납되기 위한 핵심 (F1/F8) */
    QDoubleSpinBox, QSpinBox, QComboBox {{
        background: {P.BG_INPUT};
        color: {P.TEXT_INPUT};
        border: 1px solid {P.BORDER_INPUT};
        padding: 3px 6px;
        border-radius: {T.R_MD};
        font-family: {T.FONT_MONO};
        font-weight: 600;
        min-height: {T.H_INPUT_SM}px;
        max-height: {T.H_INPUT}px;
    }}
    QDoubleSpinBox:focus, QSpinBox:focus {{
        border-color: {P.ACCENT_BLUE if is_dark else P.BORDER_LIGHT};
    }}
    QTabWidget::pane {{
        border: 1px solid {P.BORDER_PRIMARY};
        background: {P.BG_PRIMARY if is_dark else P.BG_SECONDARY};
        padding: {T.SP_SM}px;
    }}
    QTabBar::tab {{
        background: {P.BG_PRESSED if is_dark else P.BORDER_PRIMARY};
        color: {P.TEXT_SECONDARY};
        font-family: {T.FONT};
        font-size: {T.FS_SM};
        height: {T.H_INPUT}px;
        min-width: 75px;
        padding: 6px 10px;
        font-weight: 600;
        border: 1px solid {P.BORDER_PRIMARY};
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {P.BG_SECONDARY};
        color: {P.ACCENT_BLUE};
        font-weight: 600;
        border-bottom: 2px solid {P.ACCENT_BLUE if is_dark else P.TEXT_PRIMARY};
    }}
    QTabBar::tab:hover:!selected {{
        background: {P.BG_HOVER};
        color: {P.TEXT_TERTIARY if is_dark else P.TEXT_PRIMARY};
    }}
"""


def get_dark_component_style():
    """다크 모드 컴포넌트 스타일 생성."""
    return _component_style(Dark, is_dark=True)

def get_light_component_style():
    """라이트 모드 컴포넌트 스타일 생성."""
    return _component_style(Light, is_dark=False)

class ComponentCard(QFrame):
    """하위 위젯 공통 베이스 카드."""
    def __init__(self):
        super().__init__()
        self.setStyleSheet(get_dark_component_style())

    def apply_theme(self, is_dark=True):
        """테마 전환 적용."""
        self.setStyleSheet(get_dark_component_style() if is_dark else get_light_component_style())


def _inlet_port_label(inlet_provider, port):
    """inlet 시약맵 기반 포트 라벨 — StepCard 와 동일 표기 ("Port 3 : 아민A (0.5M)")."""
    info = None
    if inlet_provider is not None:
        try:
            info = inlet_provider(port)
        except Exception:
            info = None
    name = (info or {}).get('name', '')
    if not name or name in ('Empty', '비어있음', '-'):
        return f"Port {port}"
    conc = (info or {}).get('conc', 0.0)
    conc_str = f" ({conc}M)" if conc and conc > 0 else ""
    return f"Port {port} : {name}{conc_str}"

# =============================================================================
# [PART 2] 밸브 제어 위젯
# =============================================================================

class RunzeSelectorWidget(ComponentCard):
    """12-way 셀렉터 밸브 위젯.

    @codesyncer-decision: inlet_provider(포트→시약맵 정보) 주입 시 포트 스핀박스를
      시약명 콤보("Port 3 : 아민A (0.5M)")로 교체 — 시퀀스 StepCard 와 동일한
      inlet 맵을 매뉴얼에서도 활용. 미주입 시 기존 스핀박스 유지 (하위호환)."""
    def __init__(self, valve_obj, inlet_provider=None):
        super().__init__()
        self.valve = valve_obj
        self._inlet = inlet_provider

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        self.lbl_title = QLabel("12-WAY VALVE")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        self.lbl_port = QLabel("Inlet Port")
        self.lbl_port.setMinimumWidth(60)
        self.lbl_port.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")

        current_pos = getattr(self.valve, 'position', 1)
        if inlet_provider is not None:
            self.sp_port = None
            self.cb_port = QComboBox()
            self.cb_port.setMinimumHeight(32)
            for p in range(1, 13):
                self.cb_port.addItem(_inlet_port_label(self._inlet, p), p)
            idx = self.cb_port.findData(current_pos if isinstance(current_pos, int) else 1)
            if idx >= 0:
                self.cb_port.setCurrentIndex(idx)
        else:
            self.cb_port = None
            self.sp_port = QSpinBox()
            self.sp_port.setRange(1, 12)
            self.sp_port.setPrefix("Port ")
            self.sp_port.setMinimumHeight(32)
            if isinstance(current_pos, int):
                self.sp_port.setValue(current_pos)

        self.btn_move = QPushButton("MOVE")
        self.btn_move.setMinimumHeight(40)
        self.btn_move.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.ACCENT_BLUE};
                color: white;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                font-weight: 600;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        self.btn_move.clicked.connect(self.on_move_click)

        layout.addWidget(self.lbl_port)
        layout.addWidget(self.cb_port if self.cb_port is not None else self.sp_port)
        layout.addWidget(self.btn_move)
        layout.addStretch()
        self.apply_theme(True)

    def _target_port(self):
        return self.cb_port.currentData() if self.cb_port is not None else self.sp_port.value()

    def on_move_click(self):
        if not self.valve: return
        target_port = self._target_port()
        self.btn_move.setText("...")
        self.btn_move.setEnabled(False)
        t = threading.Thread(target=self._thread_move, args=(target_port,))
        t.daemon = True
        t.start()

    def _thread_move(self, port):
        try:
            self.valve.set_position(port)
        except Exception as e:
            print(f"Runze Error: {e}")
        finally:
            #
            self.btn_move.setText("MOVE")
            self.btn_move.setEnabled(True)

    def apply_theme(self, is_dark=True):
        """테마 전환."""
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.lbl_port.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 13px;")
        self.btn_move.setStyleSheet(get_primary_button_style(P))

class NeedleSourceWidget(ComponentCard):
    """오토샘플러 니들 위치 카드 — autosampler 그룹의 '소스 셀렉터'.

    @codesyncer-decision: Phase B — 소스 선택 = 니들 이동인 그룹에서는
    12-way 카드 자리에 이 카드가 온다 (채널 카드 안에서 소스를 고르는
    멘탈 모델 유지). 이동은 블로킹이므로 RunzeSelectorWidget 과 동일한
    데몬 스레드 + 버튼 비활성 패턴.
    """

    def __init__(self, sampler_obj, inlet_provider=None):
        super().__init__()
        self.sampler = sampler_obj
        self._inlet = inlet_provider

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        self.lbl_title = QLabel("NEEDLE SOURCE (AS)")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        service = {"waste", "rinse", "gas", "wash", "home"}
        self.cb_vial = QComboBox()
        self.cb_vial.setMinimumHeight(32)
        positions = getattr(sampler_obj, "vial_positions", {}) or {}
        for v in sorted(positions.keys()):
            if str(v).lower() in service:
                continue
            info = None
            if inlet_provider is not None:
                try:
                    info = inlet_provider(v)
                except Exception:
                    info = None
            name = (info or {}).get('name', '')
            known = name and name not in ('Empty', '비어있음', '-', '알 수 없음')
            self.cb_vial.addItem(f"Vial {v} : {name}" if known else f"Vial {v}", v)

        self.btn_move = QPushButton("MOVE")
        self.btn_move.setMinimumHeight(40)
        self.btn_move.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.ACCENT_BLUE};
                color: white;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                font-weight: 600;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        self.btn_move.clicked.connect(self._on_move)

        layout.addWidget(self.cb_vial)
        layout.addWidget(self.btn_move)
        layout.addStretch()
        self.apply_theme(True)

    def _on_move(self):
        if not self.sampler:
            return
        vial = self.cb_vial.currentData()
        self.btn_move.setText("...")
        self.btn_move.setEnabled(False)
        t = threading.Thread(target=self._thread_move, args=(vial,))
        t.daemon = True
        t.start()

    def _thread_move(self, vial):
        try:
            self.sampler.move_to_vial(vial)
        except Exception as e:
            print(f"Needle Error: {e}")
        finally:
            self.btn_move.setText("MOVE")
            self.btn_move.setEnabled(True)

    def apply_theme(self, is_dark=True):
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.btn_move.setStyleSheet(get_primary_button_style(P))


class Arduino3WayWidget(ComponentCard):
    """3-way 스위처 밸브 위젯."""
    def __init__(self, valve_obj):
        super().__init__()
        self.valve = valve_obj

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        # 용어 규약: 밸브는 '경로'(목적지 명사) — 펌프의 동작동사(토출/흡입)와
        # 의미축을 분리해 충전/주입 어휘 충돌을 구조적으로 차단
        self.lbl_title = QLabel("3-WAY PATH")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_PURPLE}; font-weight: 600; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        v_btns = QVBoxLayout()
        v_btns.setSpacing(0)
        self.bg = QButtonGroup(self)

        self.btn_src = QPushButton("SOURCE")
        self.btn_src.setCheckable(True)
        self.btn_src.setMinimumHeight(T.H_BTN)
        self.btn_src.setStyleSheet(f"""
            QPushButton {{
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                background: {Dark.BG_TERTIARY};
                color: {Dark.TEXT_PRIMARY};
                border: 1px solid {Dark.BORDER_PRIMARY};
                border-bottom: none;
                font-weight: 600;
            }}
            QPushButton:hover:!checked {{ background: #2d333b; }}
            QPushButton:checked {{ background: {Dark.ACT_FILL}; color: white; border-color: {Dark.ACT_FILL_HOVER}; }}
        """)

        self.btn_rct = QPushButton("REACTOR")
        self.btn_rct.setCheckable(True)
        self.btn_rct.setMinimumHeight(T.H_BTN)
        self.btn_rct.setStyleSheet(f"""
            QPushButton {{
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
                background: {Dark.BG_TERTIARY};
                color: {Dark.TEXT_PRIMARY};
                border: 1px solid {Dark.BORDER_PRIMARY};
                font-weight: 600;
            }}
            QPushButton:hover:!checked {{ background: #2d333b; }}
            QPushButton:checked {{ background: {Dark.ACT_INFUSE}; color: white; border-color: {Dark.ACT_INFUSE_HOVER}; }}
        """)

        self.bg.addButton(self.btn_src)
        self.bg.addButton(self.btn_rct)

        current_pos = getattr(self.valve, 'position', 2)
        if str(current_pos) in ["1", "SOURCE", "REFILL"]:
            self.btn_src.setChecked(True)
        else:
            self.btn_rct.setChecked(True)

        self.btn_src.clicked.connect(lambda: self.on_switch(1))
        self.btn_rct.clicked.connect(lambda: self.on_switch(2))

        v_btns.addWidget(self.btn_src)
        v_btns.addWidget(self.btn_rct)
        layout.addLayout(v_btns)
        layout.addStretch()
        self.apply_theme(True)

    def on_switch(self, pos):
        if not self.valve:
            return
        try:
            self.valve.set_position(pos)
        except Exception as e:
            # ACK 실패 시 드라이버 raise — 버튼을 실제 위치로 재동기화
            print(f"[3-Way] 전환 실패: {e}")
            cur = getattr(self.valve, "position", 2)
            (self.btn_src if str(cur) in ("1", "SOURCE", "REFILL")
             else self.btn_rct).setChecked(True)

    def apply_theme(self, is_dark=True):
        """테마 전환 — 채널카드 Valve Path 와 동일한 세그먼트 pill 언어."""
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_PURPLE}; font-weight: 600; font-size: 13px;")
        self.btn_src.setStyleSheet(get_segment_button_style(P, "neutral"))
        self.btn_rct.setStyleSheet(get_segment_button_style(P, "neutral"))

# =============================================================================
#
# =============================================================================

class BaseMotorWidget(ComponentCard):
    def __init__(self, pump_obj):
        super().__init__()
        self.pump = pump_obj

    def apply_theme(self, is_dark=True):
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light

        # v4 언어: 실행=primary / 보조(홈·이전·다음)=secondary /
        # 정지·끄기=중립(채널카드 STOP 과 통일, 혼자 안 튀게)
        primary_style = get_primary_button_style(P)
        neutral_style = get_secondary_button_style(P)
        danger_style = get_stop_button_style(P)

        for btn in self.findChildren(QPushButton):
            text = btn.text().lower()
            if ("stop" in text) or ("off" in text) or ("정지" in text) or ("끄기" in text):
                btn.setStyleSheet(danger_style)
            elif ("home" in text) or ("복귀" in text) or ("back" in text) or ("이전" in text) or ("다음" in text):
                btn.setStyleSheet(neutral_style)
            else:
                btn.setStyleSheet(primary_style)

class ChemyxMotorWidget(BaseMotorWidget):
    """Chemyx 시린지 펌프 제어 위젯."""
    def __init__(self, pump_obj):
        super().__init__(pump_obj)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)  # 컴팩트 마진
        layout.setSpacing(6)  # 컴팩트 간격

        #
        self.lbl_title = QLabel("CHEMYX SYRINGE PUMP")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        #
        def create_row(label_text, input_widget, btn_widget=None):
            h_layout = QHBoxLayout()
            h_layout.setSpacing(T.SP_SM)

            lbl = QLabel(label_text)
            lbl.setMinimumWidth(60)  # @codesyncer-decision: Label min-width 60px
            lbl.setStyleSheet("font-weight: bold; font-size: 13px;")

            input_widget.setMinimumHeight(32)  # 입력 높이 32px

            h_layout.addWidget(lbl)
            h_layout.addWidget(input_widget)

            if btn_widget:
                btn_widget.setMinimumHeight(40)  # 액션 버튼 높이 40px
                btn_widget.setMinimumWidth(80)
                h_layout.addWidget(btn_widget)

            return h_layout

        #
        self.sp_rate = QDoubleSpinBox()
        self.sp_rate.setRange(0.0, 100.0)
        self.sp_rate.setValue(1.0)
        self.sp_rate.setSuffix(" mL/min")
        self.sp_rate.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addLayout(create_row("Infuse:", self.sp_rate))

        #
        #
        layout.addLayout(create_row("Withdraw:", self._create_refill_rate()))

        #
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        #
        self.btn_infuse = QPushButton("INFUSE")
        self.btn_infuse.setMinimumHeight(T.H_BTN)
        self.btn_infuse.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.ACT_INFUSE};
                color: white;
                border: 2px solid {Dark.ACT_INFUSE_HOVER};
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {Dark.ACT_INFUSE_HOVER}; }}
            QPushButton:pressed {{ background-color: {Dark.ACT_INFUSE_HOVER}; }}
        """)
        self.btn_infuse.clicked.connect(self.do_infuse)

        self.btn_refill = QPushButton("WITHDRAW")
        self.btn_refill.setMinimumHeight(T.H_BTN)
        self.btn_refill.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.ACT_FILL};
                color: white;
                border: 2px solid {Dark.ACT_FILL_HOVER};
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {Dark.ACT_FILL_HOVER}; }}
            QPushButton:pressed {{ background-color: {Dark.ACT_FILL_HOVER}; }}
        """)
        self.btn_refill.clicked.connect(self.do_refill)

        # 토출 | 흡입 | 정지 — 한 줄 배치 (밀집 카드, 목업 규격)
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setMinimumHeight(T.H_BTN)  # 액션 버튼과 동일 높이 (안전 동작 가시성)
        self.btn_stop.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.ACT_STOP};
                color: white;
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {Dark.ACT_STOP_HOVER}; }}
        """)
        self.btn_stop.clicked.connect(lambda: self.pump.stop())

        btn_row.addWidget(self.btn_infuse, 1)
        btn_row.addWidget(self.btn_refill, 1)
        btn_row.addWidget(self.btn_stop, 1)
        layout.addLayout(btn_row)
        self.apply_theme(True)

    def do_infuse(self):
        rate = self.sp_rate.value()
        # @codesyncer-decision: driver에 직경+유속을 하드웨어에 직접 전송
        # - set_diameter: 펌프 펌웨어의 유속↔선속도 변환에 필수
        # - set_rate: 기존 set_flow()는 소프트웨어 변수만 설정하여 RS-485 미전송 버그 있었음
        if hasattr(self.pump, 'driver'):
            self.pump.driver.set_diameter(self.pump.diameter)
            self.pump.driver.set_rate(rate)
            self.pump.driver.set_volume(abs(self.pump.capacity))  # 양수 볼륨 = 주입
            self.pump.driver.start()

    def _create_refill_rate(self):
        """Withdraw 속도 입력 위젯을 생성한다."""
        self.sp_refill_rate = QDoubleSpinBox()
        self.sp_refill_rate.setRange(0.0, 100.0)
        self.sp_refill_rate.setValue(5.0)
        self.sp_refill_rate.setSuffix(" mL/min")
        self.sp_refill_rate.setMinimumHeight(32)
        self.sp_refill_rate.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return self.sp_refill_rate

    def do_refill(self):
        """음수 볼륨 명령으로 Withdraw를 실행한다."""
        rate = self.sp_refill_rate.value()
        if hasattr(self.pump, 'driver'):
            self.pump.driver.set_diameter(self.pump.diameter)
            self.pump.driver.set_rate(rate)
            self.pump.driver.set_volume(-abs(self.pump.capacity))  # 음수 볼륨 = Withdraw
            self.pump.driver.start()

    def apply_theme(self, is_dark=True):
        """테마 전환 — 채널카드와 동일: 토출/흡입/정지 전부 중립색 통일."""
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        neutral = get_action_state_style(P, "stop", False)
        self.btn_infuse.setStyleSheet(neutral)
        self.btn_refill.setStyleSheet(neutral)
        if hasattr(self, "btn_stop"):
            self.btn_stop.setStyleSheet(get_stop_button_style(P))

class ReaxusMotorWidget(BaseMotorWidget):
    """Reaxus 펌프 제어 위젯."""
    def __init__(self, pump_obj):
        super().__init__(pump_obj)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)  # 컴팩트 마진
        layout.setSpacing(6)  # 컴팩트 간격

        #
        self.lbl_title = QLabel("REAXUS PUMP")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        #
        h_rate = QHBoxLayout()
        h_rate.setSpacing(6)

        lbl_rate = QLabel("Flow:")
        lbl_rate.setMinimumWidth(60)  # @codesyncer-decision: Label min-width 60px
        lbl_rate.setStyleSheet("font-weight: bold; font-size: 13px;")

        self.sp_rate = QDoubleSpinBox()
        self.sp_rate.setRange(0, 100)
        self.sp_rate.setSuffix(" mL/min")
        self.sp_rate.setMinimumHeight(32)  # 입력 높이 32px

        self.btn_run = QPushButton("START")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.setMinimumWidth(80)
        self.btn_run.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE}; color: white;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                font-weight: 600; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        self.btn_run.clicked.connect(lambda: (self.pump.set_flow(self.sp_rate.value()), self.pump.start()))

        h_rate.addWidget(lbl_rate)
        h_rate.addWidget(self.sp_rate)
        h_rate.addWidget(self.btn_run)
        layout.addLayout(h_rate)

        self.btn_stop_rx = QPushButton("STOP")
        self.btn_stop_rx.setMinimumHeight(T.H_BTN)
        self.btn_stop_rx.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.BTN_STOP}; color: white;
                border: 1px solid {Dark.BTN_STOP_HOVER};
                font-weight: 600; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {Dark.BTN_STOP_HOVER}; }}
        """)
        self.btn_stop_rx.clicked.connect(lambda: self.pump.stop())
        layout.addWidget(self.btn_stop_rx)
        self.apply_theme(True)

    def apply_theme(self, is_dark=True):
        """테마 전환 — 시작=primary / 정지=stop(슬레이트)."""
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.btn_run.setStyleSheet(get_primary_button_style(P))
        self.btn_stop_rx.setStyleSheet(get_stop_button_style(P))

class VapourtecMotorWidget(BaseMotorWidget):
    """Vapourtec SF-10 제어 위젯(스크롤 기반)."""
    def __init__(self, pump_obj):
        super().__init__(pump_obj)
        self.current_mode = "IDLE"
        #
        self.setMinimumWidth(450)  # 최소 너비 유지

        #
        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(0)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        #
        self.header_label = QLabel("Vapourtec SF-10")
        self.header_label.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px; padding: 8px;")
        outer_layout.addWidget(self.header_label)

        #
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {Dark.BG_PRIMARY};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {Dark.ACCENT_BLUE};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        #
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self.scroll_content)
        layout.setSpacing(6)
        layout.setContentsMargins(T.SP_MD, T.SP_SM, T.SP_MD, T.SP_MD)

        # ========================================
        #
        self.status_frame = QFrame()
        self.status_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Dark.BG_PRESSED};
                border: 2px solid {Dark.ACCENT_BLUE};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setSpacing(10)

        #
        self.lbl_mode = QLabel("IDLE")
        self.lbl_mode.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.TEXT_SECONDARY}; font-family: 'Segoe UI';")

        #
        self.lbl_flow = QLabel("0.00 mL/min")
        self.lbl_flow.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_BLUE}; font-family: 'Consolas';")

        #
        self.lbl_pressure = QLabel("0.0 bar")
        self.lbl_pressure.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Dark.ACCENT_BLUE}; font-family: 'Consolas';")

        status_layout.addWidget(self.lbl_mode)
        status_layout.addStretch()
        status_layout.addWidget(QLabel("유속:"))
        status_layout.addWidget(self.lbl_flow)
        status_layout.addWidget(QLabel("압력:"))
        status_layout.addWidget(self.lbl_pressure)

        layout.addWidget(self.status_frame)

        # ========================================
        #
        # ========================================
        h_ctrl = QHBoxLayout()
        h_ctrl.setSpacing(10)

        self.btn_start = QPushButton("START")
        self.btn_start.setMinimumHeight(T.H_BTN_LG)
        self.btn_start.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.BTN_START};
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid {Dark.BTN_START_HOVER};
                border-radius: 8px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background-color: {Dark.BTN_START_HOVER}; }}
            QPushButton:pressed {{ background-color: {Dark.BTN_START_HOVER}; }}
        """)
        self.btn_start.setToolTip("설정된 모드로 펌프 구동을 시작합니다.")
        # @codesyncer-decision: START 시 현재 탭의 모드+파라미터를 먼저 전송
        # - 기존: pump.start()만 호출 → UI에 표시된 값과 실제 동작이 다를 수 있음
        self.btn_start.clicked.connect(self._apply_current_mode_and_start)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setMinimumHeight(T.H_BTN_LG)
        self.btn_stop.setStyleSheet(f"""
            QPushButton {{
                background-color: {Dark.BTN_STOP};
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid {Dark.BTN_STOP_HOVER};
                border-radius: 8px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background-color: {Dark.BTN_STOP_HOVER}; }}
            QPushButton:pressed {{ background-color: {Dark.BTN_STOP_HOVER}; }}
        """)
        self.btn_stop.setToolTip("펌프 구동을 즉시 정지합니다.")
        self.btn_stop.clicked.connect(lambda: self.pump.stop())

        h_ctrl.addWidget(self.btn_start)
        h_ctrl.addWidget(self.btn_stop)
        layout.addLayout(h_ctrl)

        # ========================================
        #
        # ========================================
        self.g_valve = QFrame()
        self.g_valve.setStyleSheet(f"""
            QFrame {{
                background: {Dark.BG_TERTIARY};
                border: 1px solid {Dark.BORDER_PRIMARY};
                border-radius: 4px;
                padding: 5px;
            }}
        """)
        l_valve = QHBoxLayout(self.g_valve)
        l_valve.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)

        self.lbl_valve = QLabel("밸브 위치:")
        self.lbl_valve.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY};")
        self.bg_v = QButtonGroup(self)
        self.rb_a = QRadioButton("A (열림)")
        self.rb_a.setChecked(True)
        self.rb_b = QRadioButton("B (닫힘)")
        self.bg_v.addButton(self.rb_a)
        self.bg_v.addButton(self.rb_b)

        btn_set_v = QPushButton("설정")
        btn_set_v.setMinimumHeight(35)
        btn_set_v.setMinimumWidth(70)
        btn_set_v.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 13px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_set_v.clicked.connect(lambda: self.pump.set_valve("A" if self.rb_a.isChecked() else "B"))

        l_valve.addWidget(self.lbl_valve)
        l_valve.addWidget(self.rb_a)
        l_valve.addWidget(self.rb_b)
        l_valve.addStretch()
        l_valve.addWidget(btn_set_v)
        layout.addWidget(self.g_valve)

        # ========================================
        #
        # ========================================
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 2px solid {Dark.BORDER_LIGHT};
                background: {Dark.BG_SECONDARY};
                border-radius: 4px;
            }}
            QTabBar::tab {{
                background: {Dark.BG_PRESSED};
                color: {Dark.TEXT_SECONDARY};
                font-size: 13px;
                font-weight: bold;
                height: 35px;
                min-width: 70px;
                padding: 6px;
                margin-right: 2px;
                border: 1px solid {Dark.BORDER_LIGHT};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background: {Dark.BG_HOVER};
                color: {Dark.TEXT_PRIMARY};
                border-bottom: none;
            }}
        """)
        #
        self.tabs.currentChanged.connect(self.on_mode_changed)

        #
        INPUT_STYLE = f"""
            QDoubleSpinBox, QSpinBox {{
                background: {Dark.BG_INPUT};
                color: {Dark.TEXT_INPUT};
                border: 2px solid {Dark.ACCENT_BLUE};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 14px;
                font-weight: bold;
                font-family: 'Consolas', monospace;
            }}
            QDoubleSpinBox:focus, QSpinBox:focus {{
                border-color: {Dark.ACCENT_BLUE};
            }}
        """

        #
        t_flow = QWidget()
        t_flow.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_flow = QVBoxLayout(t_flow)
        l_flow.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_flow.setSpacing(10)

        lbl_flow_title = QLabel("FLOW 모드 - 정속 유량")
        lbl_flow_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_BLUE}; font-family: 'Segoe UI';")
        l_flow.addWidget(lbl_flow_title)

        desc_flow = QLabel("지정한 유속으로 액체를 일정하게 이송합니다.")
        desc_flow.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_flow.addWidget(desc_flow)

        #
        flow_input_frame = QFrame()
        flow_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        h_f = QHBoxLayout(flow_input_frame)
        h_f.setSpacing(10)
        h_f.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        lbl_f = QLabel("유속:")
        lbl_f.setMinimumWidth(60)
        lbl_f.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY}; font-size: 14px; font-family: 'Segoe UI';")

        self.sp_flow = QDoubleSpinBox()
        self.sp_flow.setRange(0.0, 20.0)
        self.sp_flow.setSuffix(" mL/min")
        self.sp_flow.setMinimumHeight(36)
        self.sp_flow.setStyleSheet(INPUT_STYLE)

        btn_flow = QPushButton("적용")
        btn_flow.setMinimumHeight(40)
        btn_flow.setMinimumWidth(90)
        btn_flow.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_flow.clicked.connect(lambda: self.pump.set_mode_flow(self.sp_flow.value()))

        h_f.addWidget(lbl_f)
        h_f.addWidget(self.sp_flow)
        h_f.addWidget(btn_flow)

        l_flow.addWidget(flow_input_frame)
        l_flow.addStretch()
        self.tabs.addTab(t_flow, "FLOW")

        #
        t_reg = QWidget()
        t_reg.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_reg = QVBoxLayout(t_reg)
        l_reg.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_reg.setSpacing(10)

        lbl_reg_title = QLabel("REG 모드 - 압력 조절 (BPR)")
        lbl_reg_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_GRAY}; font-family: 'Segoe UI';")
        l_reg.addWidget(lbl_reg_title)

        desc_reg = QLabel("시스템 압력을 일정하게 유지하는 레귤레이터로 동작합니다.")
        desc_reg.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_reg.addWidget(desc_reg)

        #
        reg_input_frame = QFrame()
        reg_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        h_r = QHBoxLayout(reg_input_frame)
        h_r.setSpacing(10)
        h_r.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        lbl_r = QLabel("목표 압력:")
        lbl_r.setMinimumWidth(70)
        lbl_r.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY}; font-size: 14px; font-family: 'Segoe UI';")

        self.sp_reg = QDoubleSpinBox()
        self.sp_reg.setRange(0.0, 10.0)
        self.sp_reg.setSuffix(" bar")
        self.sp_reg.setMinimumHeight(36)
        self.sp_reg.setStyleSheet(INPUT_STYLE)

        btn_reg = QPushButton("설정")
        btn_reg.setMinimumHeight(40)
        btn_reg.setMinimumWidth(90)
        btn_reg.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_reg.clicked.connect(lambda: self.pump.set_mode_regulator(self.sp_reg.value()))

        h_r.addWidget(lbl_r)
        h_r.addWidget(self.sp_reg)
        h_r.addWidget(btn_reg)

        l_reg.addWidget(reg_input_frame)

        btn_purge = QPushButton("압력 배출 (PURGE)")
        btn_purge.setMinimumHeight(36)
        btn_purge.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_GRAY};
                color: white;
                font-weight: bold;
                font-size: 13px;
                border: 1px solid {Dark.ACCENT_GRAY_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_GRAY_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_GRAY_DARK}; }}
        """)
        btn_purge.clicked.connect(lambda: self.pump.purge())
        l_reg.addWidget(btn_purge)
        l_reg.addStretch()
        self.tabs.addTab(t_reg, "REG")

        #
        t_dose = QWidget()
        t_dose.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_dose = QVBoxLayout(t_dose)
        l_dose.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_dose.setSpacing(10)

        lbl_dose_title = QLabel("DOSE 모드 - 정량 주입")
        lbl_dose_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_GREEN}; font-family: 'Segoe UI';")
        l_dose.addWidget(lbl_dose_title)

        desc_dose = QLabel("설정한 유속으로 지정 부피만 이송한 뒤 정지합니다.")
        desc_dose.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_dose.addWidget(desc_dose)

        #
        dose_input_frame = QFrame()
        dose_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        dose_inner = QVBoxLayout(dose_input_frame)
        dose_inner.setSpacing(T.SP_SM)
        dose_inner.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        h_d1 = QHBoxLayout()
        h_d1.setSpacing(10)
        lbl_d1 = QLabel("유속:")
        lbl_d1.setMinimumWidth(60)
        lbl_d1.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY}; font-size: 14px; font-family: 'Segoe UI';")
        self.sp_d_rate = QDoubleSpinBox()
        self.sp_d_rate.setSuffix(" mL/min")
        self.sp_d_rate.setMinimumHeight(36)
        self.sp_d_rate.setStyleSheet(INPUT_STYLE)
        h_d1.addWidget(lbl_d1)
        h_d1.addWidget(self.sp_d_rate)

        h_d2 = QHBoxLayout()
        h_d2.setSpacing(10)
        lbl_d2 = QLabel("부피:")
        lbl_d2.setMinimumWidth(60)
        lbl_d2.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY}; font-size: 14px; font-family: 'Segoe UI';")
        self.sp_d_vol = QDoubleSpinBox()
        self.sp_d_vol.setSuffix(" mL")
        self.sp_d_vol.setMinimumHeight(36)
        self.sp_d_vol.setStyleSheet(INPUT_STYLE)
        h_d2.addWidget(lbl_d2)
        h_d2.addWidget(self.sp_d_vol)

        dose_inner.addLayout(h_d1)
        dose_inner.addLayout(h_d2)

        l_dose.addWidget(dose_input_frame)

        btn_dose = QPushButton("주입 실행")
        btn_dose.setMinimumHeight(40)
        btn_dose.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_dose.clicked.connect(lambda: self.pump.set_mode_dose(self.sp_d_rate.value(), self.sp_d_vol.value()))
        l_dose.addWidget(btn_dose)

        l_dose.addStretch()
        self.tabs.addTab(t_dose, "DOSE")

        #
        t_gas = QWidget()
        t_gas.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_gas = QVBoxLayout(t_gas)
        l_gas.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_gas.setSpacing(10)

        lbl_gas_title = QLabel("GAS 모드 - 가스 이송")
        lbl_gas_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_GREEN}; font-family: 'Segoe UI';")
        l_gas.addWidget(lbl_gas_title)

        desc_gas = QLabel("기체를 이송할 때 사용합니다. (단위: SCCM)")
        desc_gas.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_gas.addWidget(desc_gas)

        gas_input_frame = QFrame()
        gas_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        h_g = QHBoxLayout(gas_input_frame)
        h_g.setSpacing(10)
        h_g.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        lbl_g = QLabel("유량:")
        lbl_g.setMinimumWidth(60)
        lbl_g.setStyleSheet(f"font-weight: bold; color: {Dark.TEXT_PRIMARY}; font-size: 14px; font-family: 'Segoe UI';")

        self.sp_gas = QDoubleSpinBox()
        self.sp_gas.setRange(0, 100)
        self.sp_gas.setSuffix(" SCCM")
        self.sp_gas.setMinimumHeight(36)
        self.sp_gas.setStyleSheet(INPUT_STYLE)

        btn_gas = QPushButton("설정")
        btn_gas.setMinimumHeight(40)
        btn_gas.setMinimumWidth(90)
        btn_gas.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_gas.clicked.connect(lambda: self.pump.set_mode_gas(self.sp_gas.value()))

        h_g.addWidget(lbl_g)
        h_g.addWidget(self.sp_gas)
        h_g.addWidget(btn_gas)
        l_gas.addWidget(gas_input_frame)
        l_gas.addStretch()
        self.tabs.addTab(t_gas, "GAS")

        #
        t_ramp = QWidget()
        t_ramp.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_ramp = QVBoxLayout(t_ramp)
        l_ramp.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_ramp.setSpacing(10)

        lbl_ramp_title = QLabel("RAMP 모드 - 유속 구배")
        lbl_ramp_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_BLUE}; font-family: 'Segoe UI';")
        l_ramp.addWidget(lbl_ramp_title)

        desc_ramp = QLabel("정해진 시간 동안 유속을 선형으로 변화시킵니다.")
        desc_ramp.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_ramp.addWidget(desc_ramp)

        ramp_input_frame = QFrame()
        ramp_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        ramp_inner = QVBoxLayout(ramp_input_frame)
        ramp_inner.setSpacing(6)
        ramp_inner.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        self.sp_r_start = QDoubleSpinBox()
        self.sp_r_start.setPrefix("시작: ")
        self.sp_r_start.setSuffix(" mL/min")
        self.sp_r_start.setMinimumHeight(36)
        self.sp_r_start.setStyleSheet(INPUT_STYLE)

        self.sp_r_end = QDoubleSpinBox()
        self.sp_r_end.setPrefix("종료: ")
        self.sp_r_end.setSuffix(" mL/min")
        self.sp_r_end.setMinimumHeight(36)
        self.sp_r_end.setStyleSheet(INPUT_STYLE)

        self.sp_r_time = QDoubleSpinBox()
        self.sp_r_time.setPrefix("시간: ")
        self.sp_r_time.setSuffix(" min")
        self.sp_r_time.setMinimumHeight(36)
        self.sp_r_time.setStyleSheet(INPUT_STYLE)

        ramp_inner.addWidget(self.sp_r_start)
        ramp_inner.addWidget(self.sp_r_end)
        ramp_inner.addWidget(self.sp_r_time)

        l_ramp.addWidget(ramp_input_frame)

        btn_ramp = QPushButton("구배 실행")
        btn_ramp.setMinimumHeight(40)
        btn_ramp.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_ramp.clicked.connect(lambda: self.pump.set_mode_ramp(self.sp_r_start.value(), self.sp_r_end.value(), self.sp_r_time.value()))
        l_ramp.addWidget(btn_ramp)

        l_ramp.addStretch()
        self.tabs.addTab(t_ramp, "RAMP")

        #
        t_osc = QWidget()
        t_osc.setStyleSheet(f"QWidget {{ background: {Dark.BG_PRESSED}; }}")
        l_osc = QVBoxLayout(t_osc)
        l_osc.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        l_osc.setSpacing(10)

        lbl_osc_title = QLabel("OSC 모드 - 진동 혼합")
        lbl_osc_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {Dark.ACCENT_RED}; font-family: 'Segoe UI';")
        l_osc.addWidget(lbl_osc_title)

        desc_osc = QLabel("유체를 미세하게 앞뒤로 움직여 혼합합니다.")
        desc_osc.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-size: 13px; font-family: 'Segoe UI';")
        l_osc.addWidget(desc_osc)

        osc_input_frame = QFrame()
        osc_input_frame.setStyleSheet(f"QFrame {{ background: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: 6px; padding: 8px; }}")
        osc_inner = QVBoxLayout(osc_input_frame)
        osc_inner.setSpacing(6)
        osc_inner.setContentsMargins(10, T.SP_SM, 10, T.SP_SM)

        self.sp_o_spd = QDoubleSpinBox()
        self.sp_o_spd.setPrefix("속도: ")
        self.sp_o_spd.setSuffix(" mL/min")
        self.sp_o_spd.setMinimumHeight(36)
        self.sp_o_spd.setStyleSheet(INPUT_STYLE)

        self.sp_o_disp = QSpinBox()
        self.sp_o_disp.setPrefix("변위: ")
        self.sp_o_disp.setSuffix(" uL")
        self.sp_o_disp.setRange(0, 500)
        self.sp_o_disp.setMinimumHeight(36)
        self.sp_o_disp.setStyleSheet(INPUT_STYLE)

        osc_inner.addWidget(self.sp_o_spd)
        osc_inner.addWidget(self.sp_o_disp)

        l_osc.addWidget(osc_input_frame)

        btn_osc = QPushButton("진동 실행")
        btn_osc.setMinimumHeight(40)
        btn_osc.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid {Dark.ACCENT_BLUE_DARK};
                border-radius: 4px;
                font-family: 'Segoe UI';
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {Dark.ACCENT_BLUE_DARK}; }}
        """)
        btn_osc.clicked.connect(lambda: self.pump.set_mode_oscillation(self.sp_o_spd.value(), self.sp_o_disp.value()))
        l_osc.addWidget(btn_osc)

        l_osc.addStretch()
        self.tabs.addTab(t_osc, "OSC")

        layout.addWidget(self.tabs)

        #
        self.scroll.setWidget(self.scroll_content)
        outer_layout.addWidget(self.scroll)

    def _apply_current_mode_and_start(self):
        """현재 활성 탭의 모드+파라미터를 하드웨어에 전송 후 START"""
        idx = self.tabs.currentIndex()
        if idx == 0:    # FLOW
            self.pump.set_mode_flow(self.sp_flow.value())
        elif idx == 1:  # REG
            self.pump.set_mode_regulator(self.sp_reg.value())
        elif idx == 2:  # DOSE
            self.pump.set_mode_dose(self.sp_d_rate.value(), self.sp_d_vol.value())
        elif idx == 3:  # GAS
            self.pump.set_mode_gas(self.sp_gas.value())
        elif idx == 4:  # RAMP
            self.pump.set_mode_ramp(self.sp_r_start.value(), self.sp_r_end.value(), self.sp_r_time.value())
        elif idx == 5:  # OSC
            self.pump.set_mode_oscillation(self.sp_o_spd.value(), self.sp_o_disp.value())
        self.pump.start()

    def on_mode_changed(self, index):
        """모드 변경 시 상단 상태 라벨의 색상을 갱신한다."""
        P = Dark if getattr(self, '_is_dark', True) else Light
        mode_colors = {
            0: (P.ACCENT_BLUE, "FLOW"),
            1: (P.ACCENT_GRAY, "REG"),
            2: (P.ACCENT_GREEN, "DOSE"),
            3: (P.ACCENT_GREEN, "GAS"),
            4: (P.ACCENT_BLUE, "RAMP"),
            5: (P.ACCENT_RED, "OSC"),
        }

        if index in mode_colors:
            color, mode_name = mode_colors[index]
            self.lbl_mode.setText(f"{mode_name}")
            self.lbl_mode.setStyleSheet(f"font-size: 17px; font-weight: bold; color: {color}; font-family: 'Segoe UI';")

    def apply_theme(self, is_dark=True):
        """테마 전환 시 내부 하위 위젯까지 모두 갱신한다."""
        self._is_dark = is_dark
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light

        if is_dark:
            bg_frame = Dark.BG_TERTIARY
            border_color = Dark.BORDER_PRIMARY
            text_color = Dark.TEXT_PRIMARY
            desc_color = Dark.TEXT_SECONDARY
            tab_bg = Dark.BG_PRESSED
            tab_pane = Dark.BG_SECONDARY
            status_bg = Dark.BG_PRESSED
            status_border = Dark.ACCENT_BLUE
            scroll_bg = Dark.BG_PRIMARY
            header_color = Dark.ACCENT_BLUE
        else:
            #
            bg_frame = Light.BG_SECONDARY
            border_color = Light.BORDER_PRIMARY
            text_color = Light.TEXT_PRIMARY
            desc_color = Light.TEXT_SECONDARY
            tab_bg = Light.BG_SECONDARY
            tab_pane = Light.BG_SECONDARY
            status_bg = Light.BG_SECONDARY
            status_border = Light.ACCENT_BLUE
            scroll_bg = Light.BG_SECONDARY
            header_color = Light.ACCENT_BLUE

        #
        self.header_label.setStyleSheet(f"color: {header_color}; font-weight: bold; font-size: 13px; padding: 8px;")

        #
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {scroll_bg};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {Dark.ACCENT_BLUE if is_dark else Light.ACCENT_GRAY};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self.scroll_content.setStyleSheet("background: transparent;")

        #
        self.status_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {status_bg};
                border: 2px solid {status_border};
                border-radius: 6px;
                padding: 8px;
            }}
        """)

        #
        self.g_valve.setStyleSheet(f"""
            QFrame {{
                background: {bg_frame};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 5px;
            }}
        """)
        self.lbl_valve.setStyleSheet(f"font-weight: bold; color: {text_color};")

        #
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 2px solid {border_color};
                background: {tab_pane};
                border-radius: 4px;
            }}
            QTabBar::tab {{
                background: {tab_bg};
                color: {Dark.TEXT_SECONDARY if is_dark else Light.TEXT_SECONDARY};
                font-size: 13px;
                font-weight: bold;
                height: 35px;
                min-width: 70px;
                padding: 6px;
                margin-right: 2px;
                border: 1px solid {border_color};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background: {Dark.BG_HOVER if is_dark else Light.BG_SECONDARY};
                color: {Dark.TEXT_PRIMARY if is_dark else Light.TEXT_PRIMARY};
                border-bottom: none;
            }}
        """)

        #
        for i in range(self.tabs.count()):
            tab_widget = self.tabs.widget(i)
            tab_widget.setStyleSheet(f"QWidget {{ background: {tab_bg}; }}")

            #
            for frame in tab_widget.findChildren(QFrame):
                frame.setStyleSheet(f"QFrame {{ background: {bg_frame}; border: 1px solid {border_color}; border-radius: 6px; padding: 8px; }}")

            #
            for label in tab_widget.findChildren(QLabel):
                current_style = label.styleSheet()
                if P.ACCENT_BLUE not in current_style and P.ACCENT_GREEN not in current_style and P.ACCENT_RED not in current_style and P.ACCENT_GRAY not in current_style:
                    if "font-size: 13px" in current_style and "font-weight: bold" not in current_style:
                        label.setStyleSheet(f"color: {desc_color}; font-size: 13px; font-family: 'Segoe UI';")
                    elif "font-weight: bold" in current_style:
                        label.setStyleSheet(f"font-weight: bold; color: {text_color}; font-size: 13px; font-family: 'Segoe UI';")

        input_bg = P.BG_INPUT
        input_fg = P.TEXT_PRIMARY
        input_bd = P.BORDER_INPUT
        input_hover = P.BG_HOVER
        btn_bg = P.BG_TERTIARY
        input_style = f"""
            QDoubleSpinBox, QSpinBox {{
                background: {input_bg};
                color: {input_fg};
                border: 1px solid {input_bd};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 14px;
                font-weight: bold;
                font-family: 'Consolas', monospace;
            }}
            QDoubleSpinBox::up-button, QSpinBox::up-button,
            QDoubleSpinBox::down-button, QSpinBox::down-button {{
                background: {btn_bg};
                border-left: 1px solid {input_bd};
                width: 16px;
            }}
            QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
            QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{
                background: {input_hover};
            }}
        """
        for spin in self.tabs.findChildren(QDoubleSpinBox):
            spin.setStyleSheet(input_style)
        for spin in self.tabs.findChildren(QSpinBox):
            spin.setStyleSheet(input_style)

class GenericMotorWidget(BaseMotorWidget):
    """Generic/Mock 펌프 제어 위젯."""
    def __init__(self, pump_obj):
        super().__init__(pump_obj)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)  # 컴팩트 마진
        layout.setSpacing(6)  # 컴팩트 간격

        #
        self.lbl_title = QLabel("GENERIC PUMP")
        self.lbl_title.setStyleSheet(f"color: {Dark.TEXT_SECONDARY}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        #
        h_rate = QHBoxLayout()
        h_rate.setSpacing(6)

        lbl_rate = QLabel("Flow:")
        lbl_rate.setMinimumWidth(60)  # @codesyncer-decision: Label min-width 60px
        lbl_rate.setStyleSheet("font-weight: bold; font-size: 13px;")

        self.sp_rate = QDoubleSpinBox()
        self.sp_rate.setRange(0, 100)
        self.sp_rate.setSuffix(" mL/min")
        self.sp_rate.setMinimumHeight(32)  # 입력 높이 32px

        self.btn_toggle = QPushButton("START")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setMinimumHeight(40)  # 액션 버튼 높이 40px
        self.btn_toggle.setMinimumWidth(80)
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{
                background: {Dark.ACCENT_BLUE};
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background: {Dark.ACCENT_BLUE_DARK}; }}
            QPushButton:checked {{
                background: {Dark.ACCENT_RED};
            }}
            QPushButton:checked:hover {{ background: {Dark.ACCENT_RED_DARK}; }}
        """)
        self.btn_toggle.toggled.connect(self.on_toggle)

        h_rate.addWidget(lbl_rate)
        h_rate.addWidget(self.sp_rate)
        h_rate.addWidget(self.btn_toggle)
        layout.addLayout(h_rate)

    def on_toggle(self, checked):
        if checked:
            self.pump.set_flow(self.sp_rate.value())
            self.pump.start()
            self.btn_toggle.setText("STOP")
        else:
            self.pump.stop()
            self.btn_toggle.setText("START")

    def apply_theme(self, is_dark=True):
        """테마 전환."""
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-weight: bold; font-size: 13px;")
        self.btn_toggle.setStyleSheet(f"""
            QPushButton {{ background: {P.ACCENT_BLUE}; color: white;
                font-weight: bold; border-radius: 4px; }}
            QPushButton:hover {{ background: {P.ACCENT_BLUE_DARK}; }}
            QPushButton:checked {{ background: {P.ACCENT_RED}; }}
            QPushButton:checked:hover {{ background: {P.ACCENT_RED_DARK}; }}
        """)

# =============================================================================
#
# =============================================================================

class IntegratedChannelGroup(QGroupBox):
    """채널별 제어 위젯(12-way, 3-way, 펌프)을 가로로 묶는 컨테이너."""
    def __init__(self, channel_name):
        super().__init__()
        self.setTitle(f"{channel_name}")

        #
        #
        # @codesyncer-decision: 채널 카드 보더 톤다운 (디자인 위계)
        # - 기존: 모든 채널이 액센트 오렌지 보더 → 화면 전체가 '강조'라
        #   정작 봐야 할 동작 상태(Infusing 등)가 묻힘
        # - 수정: 카드는 중립 보더, 채널명 텍스트만 액센트 → 강조는 상태에게 양보
        P = Dark
        self.setStyleSheet(f"""
            QGroupBox {{
                background-color: {P.BG_SECONDARY};
                border: 1px solid {P.BORDER_PRIMARY};
                border-radius: {T.R_LG};
                margin-top: 6px;
                padding-top: 4px;
                color: {P.TEXT_PRIMARY};
                font-weight: bold;
                font-family: 'Segoe UI', sans-serif;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                top: 0px;
                background-color: {P.BG_PRIMARY};
                color: {P.ACCENT_BLUE};
                padding: 2px 8px;
                border: 1px solid {P.BORDER_PRIMARY};
                border-radius: 4px;
            }}
        """)

        #
        self.layout = QHBoxLayout(self)
        self.layout.setSpacing(T.SP_XS)  # 초소형 간격
        self.layout.setContentsMargins(T.SP_SM, T.SP_MD, T.SP_SM, 6)  # 상단 여백 최소화
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        #
        self.selector_widget = None
        self.switcher_widget = None
        self.motor_widget = None

    def set_selector(self, widget):
        """12-way 밸브 위젯(좌측)."""
        if widget:
            self.selector_widget = widget
            widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            # 12-way: inlet 시약명 콤보("Port 3 : 아민A (0.5M)")가 들어가므로
            # 기존 190 상한에선 텍스트가 잘림 → 240 으로 완화
            widget.setMinimumWidth(170)
            widget.setMaximumWidth(240)

    def set_switcher(self, widget):
        """3-way 밸브 (중앙)"""
        if widget:
            self.switcher_widget = widget
            widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            # 3-way: 버튼 2개 → 과대 폭(200–280) 축소
            widget.setMinimumWidth(140)
            widget.setMaximumWidth(180)

    def set_motor(self, widget):
        """펌프 제어 위젯(우측)."""
        if widget:
            self.motor_widget = widget
            # @codesyncer-decision: 모터 카드가 남는 폭을 채움 (좌측정렬 stretch 제거)
            # - 기존: 폭 상한 520 + 우측 stretch → Feed 존이 넓으면 카드 오른쪽이
            #   비어 세척/작업 패널(전폭)과 오른쪽 모서리가 어긋남 (사용자 지적)
            # - 수정: 밸브 카드들은 고정폭, 모터 카드만 Expanding — 그룹 카드와
            #   세척 패널의 좌우 모서리가 일치
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            widget.setMinimumWidth(360)

    def finalize(self):
        """모든 컴포넌트를 순서대로 배치한다(Selector -> Switcher -> Motor)."""
        if self.selector_widget:
            self.layout.addWidget(self.selector_widget)
            self._add_separator()

        if self.switcher_widget:
            self.layout.addWidget(self.switcher_widget)
            self._add_separator()

        if self.motor_widget:
            self.layout.addWidget(self.motor_widget, 1)
        else:
            # 모터 카드가 없더라도 행 폭을 유지
            self.layout.addStretch(1)

    def _add_separator(self):
        """컴포넌트 사이 구분선."""
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet(f"background: {Dark.BORDER_PRIMARY}; max-width: 1px;")
        line.setObjectName("separator")
        self.layout.addWidget(line)

    def apply_theme(self, is_dark=True):
        """테마 전환을 적용한다."""
        P = Dark if is_dark else Light
        font_family = "'Segoe UI', sans-serif" if is_dark else "'DejaVu Sans', 'Segoe UI', sans-serif"

        self.setStyleSheet(f"""
            QGroupBox {{
                background-color: {P.BG_SECONDARY};
                border: 1px solid {P.BORDER_PRIMARY};
                border-radius: 8px;
                margin-top: 6px;
                padding-top: 4px;
                color: {P.TEXT_PRIMARY};
                font-weight: bold;
                font-family: {font_family};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                top: 0px;
                background-color: {P.BG_PRIMARY};
                color: {P.ACCENT_BLUE};
                padding: 2px 8px;
                border: 1px solid {P.BORDER_PRIMARY};
                border-radius: 4px;
            }}
        """)

        #
        sep_color = Dark.BORDER_PRIMARY if is_dark else Light.BORDER_PRIMARY
        for i in range(self.layout.count()):
            widget = self.layout.itemAt(i).widget()
            if widget and widget.objectName() == "separator":
                widget.setStyleSheet(f"background: {sep_color}; max-width: 1px;")

        #
        if self.selector_widget and hasattr(self.selector_widget, 'apply_theme'):
            self.selector_widget.apply_theme(is_dark)
        if self.switcher_widget and hasattr(self.switcher_widget, 'apply_theme'):
            self.switcher_widget.apply_theme(is_dark)
        if self.motor_widget and hasattr(self.motor_widget, 'apply_theme'):
            self.motor_widget.apply_theme(is_dark)

# =============================================================================
# NRG 시린지펌프 / Cartesian 샘플러 수동 카드 (robochem 백엔드)
# =============================================================================

class NRGSyringeManualWidget(ComponentCard):
    """NRG 시린지펌프 수동 제어 카드.

    NRGSmartPump(시퀀스 승격형)·NRGSyringePump(manual_pumps)·Mock 모두 수용 —
    저수준 드라이버는 `pump.driver`(스마트) 또는 pump 자신으로 해석.
    @codesyncer-decision: 모든 구동 명령은 블로킹(ack 대기, zero 는 최대 2분) →
      RunzeSelectorWidget._thread_move 와 동일한 데몬 스레드 + 버튼 비활성 패턴.
      GUI 스레드 직접 호출 금지.
    """

    def __init__(self, pump_obj):
        super().__init__()
        self.pump = pump_obj
        low = self._low()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        self.lbl_title = QLabel("NRG SYRINGE PUMP")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        max_rate = float(getattr(low, 'max_flowrate', 5.0) or 5.0)
        cap_ul = float(getattr(low, 'syringe_volume_ul', 1000.0) or 1000.0)

        # 잔량바 + 밸브 상태 — 시퀀스 부기(current_vol)와 밸브 last_known_value 를
        # 읽기만 하므로 시리얼 왕복 0. 스마트 어댑터가 아닐 땐(수동 low 펌프) 숨김.
        self._is_smart = hasattr(pump_obj, 'current_vol') and hasattr(pump_obj, 'capacity')
        status_row = QHBoxLayout()
        self.bar_vol = QProgressBar()
        self.bar_vol.setRange(0, 100)
        self.bar_vol.setTextVisible(True)
        self.bar_vol.setFormat("Vol -.-- mL")
        self.bar_vol.setMaximumHeight(18)
        self.lbl_valve_state = QLabel("Valve")
        self.lbl_valve_state.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
        status_row.addWidget(self.bar_vol, 2)
        status_row.addWidget(self.lbl_valve_state, 1)
        layout.addLayout(status_row)
        if not self._is_smart:
            self.bar_vol.setVisible(False)

        row1 = QHBoxLayout()
        self.sp_rate = QDoubleSpinBox()
        self.sp_rate.setRange(0.01, max_rate)
        self.sp_rate.setDecimals(2)
        self.sp_rate.setValue(min(1.0, max_rate))
        self.sp_rate.setSuffix(" mL/min")
        self.sp_vol = QDoubleSpinBox()
        self.sp_vol.setRange(1.0, cap_ul)
        self.sp_vol.setDecimals(0)
        self.sp_vol.setValue(min(100.0, cap_ul))
        self.sp_vol.setSuffix(" µL")
        row1.addWidget(self.sp_rate, 1)
        row1.addWidget(self.sp_vol, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_withdraw = QPushButton("WITHDRAW")
        self.btn_dispense = QPushButton("INFUSE")
        row2.addWidget(self.btn_withdraw, 1)
        row2.addWidget(self.btn_dispense, 1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_fill = QPushButton("Zero FILL")
        self.btn_empty = QPushButton("Zero EMPTY")
        row3.addWidget(self.btn_fill, 1)
        row3.addWidget(self.btn_empty, 1)
        layout.addLayout(row3)

        self._has_valve = bool(getattr(low, 'main_valve_enabled', False))
        self.btn_v_res = self.btn_v_sys = None
        if self._has_valve:
            row4 = QHBoxLayout()
            self.btn_v_res = QPushButton("VALVE→RES")
            self.btn_v_sys = QPushButton("VALVE→SYS")
            row4.addWidget(self.btn_v_res, 1)
            row4.addWidget(self.btn_v_sys, 1)
            layout.addLayout(row4)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(get_stop_button_style(Dark))
        self.btn_stop.setMinimumHeight(32)
        layout.addWidget(self.btn_stop)

        self.lbl_state = QLabel("Idle")
        self.lbl_state.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
        layout.addWidget(self.lbl_state)
        layout.addStretch()

        self._action_btns = [b for b in (self.btn_withdraw, self.btn_dispense,
                                         self.btn_fill, self.btn_empty,
                                         self.btn_v_res, self.btn_v_sys) if b]
        self.btn_withdraw.clicked.connect(lambda: self._run("Withdraw", self._do_withdraw))
        self.btn_dispense.clicked.connect(lambda: self._run("Infuse", self._do_dispense))
        self.btn_fill.clicked.connect(lambda: self._run("Zero FILL", lambda: self._low().zero_fill()))
        self.btn_empty.clicked.connect(lambda: self._run("Zero EMPTY", lambda: self._low().zero_empty()))
        if self._has_valve:
            self.btn_v_res.clicked.connect(lambda: self._run("Valve→RES", lambda: self._low().set_main_valve("OFF")))
            self.btn_v_sys.clicked.connect(lambda: self._run("Valve→SYS", lambda: self._low().set_main_valve("ON")))
        self.btn_stop.clicked.connect(self._on_stop)

        # 저빈도 폴링 (2s) — 시리얼 왕복 없는 캐시값만 읽음. StatusWorker 편입 금지 원칙 유지.
        self._manual_busy = False
        self._seq_locked = False
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(2000)

    def _low(self):
        return getattr(self.pump, 'driver', self.pump)

    def _valve_state_text(self):
        """밸브 위치 표시 — last_known_value 만 읽음 (시리얼 0)."""
        try:
            if hasattr(self.pump, '_valve_pos'):
                v = self.pump._valve_pos()
            else:
                dev = getattr(self._low(), 'device', None)
                lv = dev.parameter_by_name("valve_setpoint").last_known_value if dev else None
                v = str(lv) if lv is not None else None
            return {"ON": "Valve ● SYS", "OFF": "Valve ● RES"}.get(v, "Valve")
        except Exception:
            return "Valve"

    def _poll(self):
        """잔량바/밸브상태 갱신 + 시퀀스 점유 잠금 시각화."""
        if self._has_valve or hasattr(self.pump, '_valve_pos'):
            self.lbl_valve_state.setText(self._valve_state_text())
        if self._is_smart:
            try:
                cap = max(float(self.pump.capacity), 1e-9)
                vol = max(0.0, float(self.pump.current_vol))
                self.bar_vol.setValue(int(min(vol / cap, 1.0) * 100))
                self.bar_vol.setFormat(f"Vol {vol:.2f}/{cap:.2f} mL")
            except Exception:
                pass
        # 시퀀스가 이 펌프를 잡고 있으면 카드 잠금 (수동 조작 중이면 그 상태 유지)
        seq_busy = bool(getattr(self.pump, 'is_refilling', False) or getattr(self.pump, 'running', False))
        if seq_busy and not self._seq_locked:
            self._seq_locked = True
            for b in self._action_btns:
                b.setEnabled(False)
            self.lbl_state.setText("🔒 Locked: sequence active")
        elif not seq_busy and self._seq_locked:
            self._seq_locked = False
            if not self._manual_busy:
                for b in self._action_btns:
                    b.setEnabled(True)
                self.lbl_state.setText("Idle")

    def _busy_guard(self) -> bool:
        # 시퀀스가 이 펌프를 잡고 있으면 수동 조작 차단
        if getattr(self.pump, 'is_refilling', False) or getattr(self.pump, 'running', False):
            self.lbl_state.setText("Blocked: sequence active")
            return True
        return False

    def _do_withdraw(self):
        low = self._low()
        low.set_rate(self.sp_rate.value())
        low.withdraw(self.sp_vol.value())

    def _do_dispense(self):
        low = self._low()
        low.set_rate(self.sp_rate.value())
        low.dispense(self.sp_vol.value())

    def _run(self, label, fn):
        if self._busy_guard():
            return
        self._manual_busy = True
        for b in self._action_btns:
            b.setEnabled(False)
        self.lbl_state.setText(f"{label}...")

        def job():
            try:
                fn()
                self.lbl_state.setText(f"{label} done")
            except Exception as e:
                print(f"[NRG Manual] {label} error: {e}")
                self.lbl_state.setText(f"{label} FAIL: {e}")
            finally:
                self._manual_busy = False
                if not self._seq_locked:
                    for b in self._action_btns:
                        b.setEnabled(True)

        threading.Thread(target=job, daemon=True).start()

    def _on_stop(self):
        def job():
            try:
                # 스마트 어댑터면 stop 이벤트 라이프사이클(set→join→clear)까지 마감
                self.pump.stop()
                low = self._low()
                dev = getattr(low, 'device', None)
                if dev is not None:
                    for pname in ("pump", "zero"):
                        try:
                            dev.stop_parameter(pname, stop=False)
                        except Exception:
                            pass
                self.lbl_state.setText("Stopped")
            except Exception as e:
                print(f"[NRG Manual] stop error: {e}")

        threading.Thread(target=job, daemon=True).start()

    def apply_theme(self, is_dark=True):
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.lbl_state.setStyleSheet(f"color: {P.TEXT_TERTIARY}; font-size: 13px;")
        self.btn_stop.setStyleSheet(get_stop_button_style(P))


class SamplerManualWidget(ComponentCard):
    """Cartesian 샘플러(GRBL) 수동 제어 카드 — 호밍/바이알 이동/보조니들.

    @codesyncer-decision: home 은 최대 120초 블로킹 → 스레드 + 버튼 비활성.
      E-Stop 은 lock-bypass raw 0x18 (emergency_stop) 이라 GUI 스레드에서 즉시 호출 가능.
    """

    def __init__(self, name, sampler_obj):
        super().__init__()
        self.sampler = sampler_obj

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        # '—' 장식 제거 — 전문 장비 타이틀 표기 (사용자 피드백 2026-07-13)
        self.lbl_title = QLabel(f"SAMPLER · {name}")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        self.btn_home = QPushButton("HOME ($H)")
        layout.addWidget(self.btn_home)

        row_vial = QHBoxLayout()
        self.cb_vial = QComboBox()
        vials = sorted((getattr(sampler_obj, 'vial_positions', {}) or {}).keys())
        self.cb_vial.addItems(vials if vials else ["-"])
        self.btn_go_vial = QPushButton("GO TO VIAL")
        row_vial.addWidget(self.cb_vial, 1)
        row_vial.addWidget(self.btn_go_vial, 1)
        layout.addLayout(row_vial)

        # Injection port 이동 (좌표 파일의 injection_ports)
        row_inj = QHBoxLayout()
        self.cb_inj = QComboBox()
        inj_ports = sorted((getattr(sampler_obj, 'injection_ports', {}) or {}).keys())
        self.cb_inj.addItems(inj_ports if inj_ports else ["-"])
        self.btn_go_inj = QPushButton("TO INJ PORT")
        if not inj_ports:
            self.cb_inj.setEnabled(False)
            self.btn_go_inj.setEnabled(False)
            self.btn_go_inj.setToolTip("No injection_ports in coordinate file")
        row_inj.addWidget(self.cb_inj, 1)
        row_inj.addWidget(self.btn_go_inj, 1)
        layout.addLayout(row_inj)

        row_aux = QHBoxLayout()
        self.btn_lift = QPushButton("NEEDLE UP")
        self.btn_aux_on = QPushButton("AUX IN")
        self.btn_aux_off = QPushButton("AUX OUT")
        row_aux.addWidget(self.btn_lift, 1)
        row_aux.addWidget(self.btn_aux_on, 1)
        row_aux.addWidget(self.btn_aux_off, 1)
        layout.addLayout(row_aux)

        # 위치 표시 — 자동 폴링 금지(이동 중 시리얼 락 경합) → 수동 [갱신] 버튼
        row_pos = QHBoxLayout()
        self.lbl_pos = QLabel("Pos: —")
        self.lbl_pos.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
        self.btn_pos_refresh = QPushButton("REFRESH")
        self.btn_pos_refresh.setMaximumWidth(90)
        row_pos.addWidget(self.lbl_pos, 1)
        row_pos.addWidget(self.btn_pos_refresh, 0)
        layout.addLayout(row_pos)

        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet(
            "QPushButton { background-color: #b3261e; color: white; font-weight: 600; border-radius: 4px; }")
        self.btn_estop.setMinimumHeight(32)
        layout.addWidget(self.btn_estop)

        self.lbl_state = QLabel("Idle")
        self.lbl_state.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
        layout.addWidget(self.lbl_state)
        layout.addStretch()

        self._action_btns = [self.btn_home, self.btn_go_vial, self.btn_go_inj,
                             self.btn_lift, self.btn_aux_on, self.btn_aux_off,
                             self.btn_pos_refresh]
        self.btn_home.clicked.connect(lambda: self._run("Home", lambda: self.sampler.home()))
        self.btn_go_vial.clicked.connect(
            lambda: self._run("Go to vial", lambda: self.sampler.move_to_vial(self.cb_vial.currentText())))
        self.btn_go_inj.clicked.connect(
            lambda: self._run("To inj port",
                              lambda: self.sampler.move_to_injection_port(self.cb_inj.currentText())))
        self.btn_lift.clicked.connect(lambda: self._run("Needle up", lambda: self.sampler.lift_needle()))
        self.btn_aux_on.clicked.connect(lambda: self._run("Aux in", lambda: self.sampler.insert_aux_needle()))
        self.btn_aux_off.clicked.connect(lambda: self._run("Aux out", lambda: self.sampler.retract_aux_needle()))
        self.btn_pos_refresh.clicked.connect(lambda: self._run("Refresh", self._do_pos_refresh))
        self.btn_estop.clicked.connect(self._on_estop)
        self.apply_theme(True)

    def _do_pos_refresh(self):
        pos = self.sampler.query_position()
        if pos and pos.get("x") is not None:
            self.lbl_pos.setText(f"Pos: X{pos['x']:.1f} Y{pos['y']:.1f} Z{pos['z']:.1f}")
        else:
            self.lbl_pos.setText("Pos: query failed")
        return True

    def _run(self, label, fn):
        for b in self._action_btns:
            b.setEnabled(False)
        self.lbl_state.setText(f"{label}...")

        def job():
            try:
                result = fn()
                ok = result[0] if isinstance(result, tuple) else bool(result)
                self.lbl_state.setText(f"{label} {'done' if ok else 'FAIL'}")
            except Exception as e:
                print(f"[Sampler Manual] {label} error: {e}")
                self.lbl_state.setText(f"{label} FAIL: {e}")
            finally:
                for b in self._action_btns:
                    b.setEnabled(True)

        threading.Thread(target=job, daemon=True).start()

    def _on_estop(self):
        try:
            self.sampler.emergency_stop()
            self.lbl_state.setText("E-STOP sent (home before reuse)")
        except Exception as e:
            print(f"[Sampler Manual] estop error: {e}")

    def apply_theme(self, is_dark=True):
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.lbl_state.setStyleSheet(f"color: {P.TEXT_TERTIARY}; font-size: 13px;")
        # @codesyncer(사용자 결정 2026-07-13): 채움+황색링(ISO 13850) → 채널카드 STOP 과
        #   동일한 적색 아웃라인으로 통일 — "stop 버튼 일관되게" 피드백. 기능(raw 0x18) 불변.
        self.btn_estop.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {P.ACCENT_RED}; "
            f"border: 1.5px solid {P.ACCENT_RED}; border-radius: {T.R_MD}; "
            f"font-weight: {T.FW_BOLD}; letter-spacing: 0.5px; }} "
            f"QPushButton:hover {{ background: {P.ACCENT_RED}; color: #ffffff; }}")


class WashOpsWidget(ComponentCard):
    """그룹 세척/수동 작업 패널 — 시퀀스와 동일한 composite API 를 매뉴얼에 개방.

    @codesyncer-decision: 세척을 '밸브 수동 전환 + 모터 수동 구동'의 조합으로 시키지
      않고, 스마트펌프의 단독 실행용 composite(refill/wash_cycle/prime_reactor)를
      그대로 호출 — is_refilling 가드·밸브 복귀·부피 부기가 시퀀스와 동일하게
      적용된다. 라우팅 무관 덕타이핑: external=포트 선택형, internal(NRG)=전용
      소스/기포 퍼지형으로 같은 패널이 분기.
    @codesyncer-decision: 소스/용매/폐기 포트 콤보는 inlet_provider(시약맵)로
      이름 표기 — 시퀀스 StepCard 와 같은 데이터 소스 ("inlet 활용").
      모든 동작은 블로킹 composite → 데몬 스레드 + 버튼 비활성, 시퀀스 점유 시
      🔒 잠금(2s 폴, 자기 작업 중에는 미간섭).
    """

    def __init__(self, group_name, pump_obj, routing="external_valve", inlet_provider=None,
                 sampler=None):
        super().__init__()
        self.pump = pump_obj
        self.routing = routing
        self.sampler = sampler        # autosampler 그룹: 니들 세척용 (일체형)
        self._inlet = inlet_provider
        self._abort = False
        self._manual_busy = False
        self._seq_locked = False
        cap = float(getattr(pump_obj, 'capacity', 50.0) or 50.0)
        is_external = (routing == "external_valve")
        is_as = (routing == "autosampler" and sampler is not None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
        layout.setSpacing(6)

        # '—' 장식 제거 — 전문 장비 타이틀 표기 (사용자 피드백 2026-07-13)
        self.lbl_title = QLabel(f"WASH / SERVICE · {group_name}")
        self.lbl_title.setStyleSheet(f"color: {Dark.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.lbl_title)

        # ── 작업 그리드 (4컬럼) — 행마다 컬럼 경계가 어긋나던 HBox 배열을 교체
        # @codesyncer-decision: c0/c1=포트 선택, c2=수치(부피·횟수), c3=실행 버튼으로
        #   고정 — 부피/횟수 스핀박스와 실행 버튼이 수직 정렬되어 스캔이 쉬움.
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for col, st in ((0, 2), (1, 2), (2, 1), (3, 1)):
            grid.setColumnStretch(col, st)

        self.sp_vol = QDoubleSpinBox()
        self.sp_vol.setRange(0.05, cap)
        self.sp_vol.setDecimals(2)
        self.sp_vol.setValue(min(2.0, cap))
        self.sp_vol.setSuffix(" mL")
        self.btn_fill = QPushButton("Refill")
        self.sp_cnt = QSpinBox()
        self.sp_cnt.setRange(1, 10)
        self.sp_cnt.setValue(1)
        self.sp_cnt.setSuffix(" cyc")
        self.btn_wash = QPushButton("Wash")
        self.btn_prime = QPushButton("Prime")
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(get_stop_button_style(Dark))

        if is_external:
            self.cb_src = self._make_port_combo(default=1)
            self.cb_solv = self._make_port_combo(default=1)
            self.cb_waste = self._make_port_combo(default=12)
            self.cb_solv.setToolTip("Solvent port for washing")
            self.cb_waste.setToolTip("Waste port for rinse discharge")
            grid.addWidget(self.cb_src, 0, 0, 1, 2)
            grid.addWidget(self.cb_solv, 1, 0)
            grid.addWidget(self.cb_waste, 1, 1)
        elif is_as:
            # @codesyncer-decision: AS(일체형) 변형 — 포트 대신 vial 콤보.
            #   세척 = 원본 RoboChem CleanNeedle 등가: 니들→rinse 에서 흡입,
            #   니들→waste 에서 배출 (내장밸브식 '제자리 기포퍼지' 탈피)
            service = {"waste", "rinse", "gas", "wash", "home", "cleaning"}
            positions = sorted((getattr(sampler, "vial_positions", {}) or {}).keys())

            def _vial_combo(items, default=None):
                cb = QComboBox()
                cb.setMinimumHeight(28)
                for v in items:
                    cb.addItem(f"Vial {v}", v)
                if default is not None and (i := cb.findData(default)) >= 0:
                    cb.setCurrentIndex(i)
                return cb

            sources = [v for v in positions if str(v).lower() not in service]
            low_map = {str(v).lower(): v for v in positions}
            rinse_default = next((low_map[k] for k in ("rinse", "wash", "cleaning")
                                  if k in low_map), None)
            waste_default = low_map.get("waste")
            self.cb_src = _vial_combo(sources or positions)
            self.cb_solv = _vial_combo(positions, default=rinse_default)
            self.cb_waste = _vial_combo(positions, default=waste_default)
            self.cb_src.setToolTip("Refill: needle moves to this vial and withdraws")
            self.cb_solv.setToolTip("Wash solvent vial (needle withdraws here)")
            self.cb_waste.setToolTip("Discharge vial (needle infuses here)")
            grid.addWidget(self.cb_src, 0, 0, 1, 2)
            grid.addWidget(self.cb_solv, 1, 0)
            grid.addWidget(self.cb_waste, 1, 1)
        else:
            self.cb_src = None
            self.cb_solv = None
            self.cb_waste = None
            lbl_src = QLabel("Fixed source (internal valve)")
            lbl_src.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
            lbl_wash = QLabel("Bubble purge (FILL→EMPTY @reservoir)")
            lbl_wash.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
            grid.addWidget(lbl_src, 0, 0, 1, 2)
            grid.addWidget(lbl_wash, 1, 0, 1, 2)
        grid.addWidget(self.sp_vol, 0, 2)
        grid.addWidget(self.btn_fill, 0, 3)
        grid.addWidget(self.sp_cnt, 1, 2)
        grid.addWidget(self.btn_wash, 1, 3)
        grid.addWidget(self.btn_prime, 2, 0, 1, 3)
        grid.addWidget(self.btn_stop, 2, 3)
        layout.addLayout(grid)

        self.lbl_state = QLabel("Idle")
        self.lbl_state.setStyleSheet(f"color: {Dark.TEXT_TERTIARY}; font-size: 13px;")
        layout.addWidget(self.lbl_state)

        self._action_btns = [b for b in (self.btn_fill, self.btn_wash, self.btn_prime) if b]
        self.btn_fill.clicked.connect(lambda: self._run("Refill", self._do_fill))
        self.btn_wash.clicked.connect(lambda: self._run("Wash", self._do_wash))
        self.btn_prime.clicked.connect(lambda: self._run("Prime", self._do_prime))
        self.btn_stop.clicked.connect(self._on_stop)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(2000)
        self.apply_theme(True)

    def _make_port_combo(self, default=1):
        cb = QComboBox()
        cb.setMinimumHeight(28)
        for p in range(1, 13):
            cb.addItem(_inlet_port_label(self._inlet, p), p)
        idx = cb.findData(default)
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        return cb

    def refresh_port_labels(self):
        """시약맵 변경 후 콤보 라벨 갱신 (선택 포트 유지)."""
        for cb in (self.cb_src, self.cb_solv, self.cb_waste):
            if cb is None:
                continue
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            for p in range(1, 13):
                cb.addItem(_inlet_port_label(self._inlet, p), p)
            idx = cb.findData(cur)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)

    # ── 동작 (전부 워커 스레드에서 실행) ──
    def _is_as(self):
        return self.routing == "autosampler" and self.sampler is not None

    def _needle_to(self, vial):
        ok, msg = self.sampler.move_to_vial(vial)
        if not ok:
            raise RuntimeError(f"니들 이동 실패({vial}): {msg}")

    def _do_fill(self):
        if self._is_as():
            # 일체형 리필: 니들→소스 vial → 흡입 → 리트랙트
            self._needle_to(self.cb_src.currentData())
            try:
                self.pump.refill(1, volume=self.sp_vol.value())
            finally:
                self.sampler.lift_needle()
            return
        port = self.cb_src.currentData() if self.cb_src is not None else 1
        self.pump.refill(port, volume=self.sp_vol.value())

    def _do_wash(self):
        n = self.sp_cnt.value()
        if self._is_as():
            # 원본 CleanNeedle 등가: [rinse 에서 FILL → waste 에서 EMPTY] × n
            rinse = self.cb_solv.currentData()
            waste = self.cb_waste.currentData()
            low = getattr(self.pump, "driver", None) or self.pump
            use_zero = hasattr(low, "zero_fill") and hasattr(low, "zero_empty")
            try:
                for i in range(n):
                    if self._abort:
                        self.lbl_state.setText(f"Wash aborted ({i}/{n} done)")
                        return
                    self.lbl_state.setText(f"Needle wash {i + 1}/{n}...")
                    if use_zero:
                        self._needle_to(rinse)
                        low.zero_fill()      # 깨끗한 용매 흡입 (니들 라인)
                        self._needle_to(waste)
                        low.zero_empty()     # 오염분 배출
                    else:
                        # 저수준 zero 미지원 펌프: rinse 위치에서 제자리 퍼지 폴백
                        self._needle_to(rinse)
                        self.pump.wash_cycle(solvent_port=1, waste_port=12)
            finally:
                self.sampler.lift_needle()
            return
        s = self.cb_solv.currentData() if self.cb_solv is not None else 1
        w = self.cb_waste.currentData() if self.cb_waste is not None else 12
        for i in range(n):
            if self._abort:
                self.lbl_state.setText(f"Wash aborted ({i}/{n} done)")
                return
            self.lbl_state.setText(f"Wash {i + 1}/{n}...")
            self.pump.wash_cycle(solvent_port=s, waste_port=w)

    def _do_prime(self):
        self.pump.prime_reactor()

    def _run(self, label, fn):
        if getattr(self.pump, 'is_refilling', False) or getattr(self.pump, 'running', False):
            self.lbl_state.setText("Blocked: sequence/other op active")
            return
        self._abort = False
        self._manual_busy = True
        for b in self._action_btns:
            b.setEnabled(False)
        self.lbl_state.setText(f"{label}...")

        def job():
            try:
                fn()
                if not self._abort:
                    self.lbl_state.setText(f"{label} done")
            except Exception as e:
                print(f"[WashOps] {label} error: {e}")
                self.lbl_state.setText(f"{label} FAIL: {e}")
            finally:
                self._manual_busy = False
                if not self._seq_locked:
                    for b in self._action_btns:
                        b.setEnabled(True)

        threading.Thread(target=job, daemon=True).start()

    def _on_stop(self):
        def job():
            try:
                self._abort = True
                if hasattr(self.pump, '_abort_refill'):
                    self.pump._abort_refill = True
                self.pump.stop()
                self.lbl_state.setText("Stopped")
            except Exception as e:
                print(f"[WashOps] stop error: {e}")

        threading.Thread(target=job, daemon=True).start()

    def _poll(self):
        """시퀀스 점유 잠금 — 자기 작업(_manual_busy) 중에는 간섭하지 않음."""
        busy = bool(getattr(self.pump, 'is_refilling', False) or getattr(self.pump, 'running', False))
        if busy and not self._manual_busy and not self._seq_locked:
            self._seq_locked = True
            for b in self._action_btns:
                b.setEnabled(False)
            self.lbl_state.setText("🔒 Locked: sequence active")
        elif not busy and self._seq_locked:
            self._seq_locked = False
            if not self._manual_busy:
                for b in self._action_btns:
                    b.setEnabled(True)
                self.lbl_state.setText("Idle")

    def apply_theme(self, is_dark=True):
        super().apply_theme(is_dark)
        P = Dark if is_dark else Light
        self.lbl_title.setStyleSheet(f"color: {P.ACCENT_BLUE}; font-weight: bold; font-size: 13px;")
        self.lbl_state.setStyleSheet(f"color: {P.TEXT_TERTIARY}; font-size: 13px;")
        self.btn_stop.setStyleSheet(get_stop_button_style(P))


# =============================================================================
#
# =============================================================================

class PumpWidgetFactory:
    """채널 제어 위젯 팩토리.
    1. 12-way Selector (Runze)
    2. 3-way Switcher (Arduino)
    3. Motor/Pump 제어
    """
    @staticmethod
    def create_motor(pump_obj):
        """펌프 타입 → 모터 제어 위젯 (정확한 클래스명 집합 분기 — 부분매칭 금지)."""
        pump_type = type(pump_obj).__name__
        if pump_type in ("ChemyxSmartPump", "ChemyxPump"):
            return ChemyxMotorWidget(pump_obj)
        if pump_type in ("NRGSmartPump", "NRGSyringePump", "MockNRGSyringePump"):
            return NRGSyringeManualWidget(pump_obj)
        if pump_type in ("ReaxusPump",):
            return ReaxusMotorWidget(pump_obj)
        if pump_type in ("VapourtecPump",):
            return VapourtecMotorWidget(pump_obj)
        return GenericMotorWidget(pump_obj)

    @staticmethod
    def create(name, pump_obj, selector_obj=None, switcher_obj=None, inlet_provider=None,
               sampler_obj=None):
        group = IntegratedChannelGroup(name)

        # 오토샘플러 그룹: 12-way 자리에 니들 소스 카드 (소스 선택 = 니들 이동)
        if sampler_obj is not None and selector_obj is None:
            group.set_selector(NeedleSourceWidget(sampler_obj, inlet_provider=inlet_provider))
        elif selector_obj:
            group.set_selector(RunzeSelectorWidget(selector_obj, inlet_provider=inlet_provider))

        # 2. Switcher (3-way) - 중앙
        if switcher_obj:
            group.set_switcher(Arduino3WayWidget(switcher_obj))

        # @codesyncer-decision: 부분문자열 매칭 금지 — 정확한 클래스명 집합으로 분기.
        #   (예: 'NRG' 부분매칭은 향후 하이브리드 클래스명에서 오분기 위험)
        group.set_motor(PumpWidgetFactory.create_motor(pump_obj))

        #
        group.finalize()

        return group

