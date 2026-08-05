"""
테마 스타일 생성 함수 - 기능별 통합

@codesyncer-decision: 위젯별 스타일 함수화
- 같은 기능(버튼, 입력 등)의 다크/라이트 스타일을 한 함수에서 생성
- 색상 팔레트만 바꿔서 전달 → 중복 제거
- 스타일 변경 시 함수 하나만 수정하면 모든 테마 적용

@codesyncer-context: 사용법
    from ui.colors import DarkPalette, LightPalette
    from ui.theme import get_button_styles

    dark_btn_qss = get_button_styles(DarkPalette)
    light_btn_qss = get_button_styles(LightPalette)
"""

from ui.colors import DialogColors, T


def get_global_styles(P):
    """전역 설정 (배경, 폰트)"""
    return f"""
/* === Global === */
QMainWindow {{
    background: {P.BG_PRIMARY};
}}
QWidget {{
    color: {P.TEXT_PRIMARY};
    font-family: {T.FONT};
    font-size: {T.FS_MD};
    background-color: {P.BG_PRIMARY};
}}
"""


def get_dialog_styles():
    """다이얼로그 스타일 (OS 네이티브 - Dark/Light 공통)"""
    D = DialogColors
    return f"""
/* === Dialogs (시스템 기본 색상 사용) === */
QMessageBox, QFileDialog, QInputDialog, QColorDialog, QFontDialog {{
    background-color: {D.BG};
    color: {D.TEXT};
}}
QMessageBox *, QFileDialog *, QInputDialog *, QColorDialog *, QFontDialog * {{
    background-color: transparent;
    color: {D.TEXT};
}}
QMessageBox QFrame, QFileDialog QFrame {{
    background-color: {D.BG};
}}
QMessageBox QLabel, QFileDialog QLabel {{
    color: {D.TEXT};
    background-color: transparent;
}}
QMessageBox QPushButton, QFileDialog QPushButton {{
    background-color: {D.BUTTON_BG};
    color: {D.TEXT};
    border: 1px solid {D.BORDER};
    padding: 5px 15px;
}}
QMessageBox QPushButton:hover, QFileDialog QPushButton:hover {{
    background-color: {D.BUTTON_HOVER};
}}
QFileDialog QWidget {{
    background-color: {D.BG};
    color: {D.TEXT};
}}
QFileDialog QListView, QFileDialog QTreeView, QFileDialog QListWidget {{
    background-color: {D.INPUT_BG};
    color: {D.TEXT};
    border: 1px solid {D.BORDER};
}}
QFileDialog QListView::item, QFileDialog QTreeView::item {{
    color: {D.TEXT};
}}
QFileDialog QListView::item:selected, QFileDialog QTreeView::item:selected {{
    background-color: {D.SELECTED_BG};
    color: {D.SELECTED_TEXT};
}}
QFileDialog QHeaderView::section {{
    background-color: {D.BG};
    color: {D.TEXT};
    border: 1px solid {D.BORDER};
    padding: 4px;
}}
QFileDialog QLineEdit {{
    background-color: {D.INPUT_BG};
    color: {D.TEXT};
    border: 1px solid #7a7a7a;
}}
QFileDialog QComboBox {{
    background-color: {D.INPUT_BG};
    color: {D.TEXT};
    border: 1px solid {D.BORDER};
}}
QFileDialog QComboBox QAbstractItemView {{
    background-color: {D.INPUT_BG};
    color: {D.TEXT};
    selection-background-color: {D.SELECTED_BG};
    selection-color: {D.SELECTED_TEXT};
}}
QFileDialog QToolButton {{
    background-color: {D.BUTTON_BG};
    color: {D.TEXT};
    border: 1px solid {D.BORDER};
}}
QFileDialog QToolButton:hover {{
    background-color: {D.BUTTON_HOVER};
}}
QFileDialog QSplitter::handle {{
    background-color: #c0c0c0;
}}
QFileDialog QScrollArea, QFileDialog QScrollArea > QWidget {{
    background-color: {D.BG};
    color: {D.TEXT};
}}
QFileDialog QStackedWidget {{
    background-color: {D.BG};
}}
QFileDialog #sidebar {{
    background-color: {D.BG};
    color: {D.TEXT};
}}
QFileDialog QSplitter {{
    background-color: {D.BG};
}}
QFileDialog QTableView {{
    background-color: {D.INPUT_BG};
    color: {D.TEXT};
    gridline-color: #d0d0d0;
}}
QFileDialog QTableView::item {{
    color: {D.TEXT};
}}
QFileDialog QTableView::item:selected {{
    background-color: {D.SELECTED_BG};
    color: {D.SELECTED_TEXT};
}}
"""


def get_groupbox_styles(P):
    """그룹박스 스타일"""
    return f"""
/* === GroupBox === */
QGroupBox {{
    border: 1px solid {P.BORDER_PRIMARY};
    border-radius: {T.R_LG};
    margin-top: 14px;
    background-color: {P.BG_SECONDARY};
    padding-top: 18px;
    font-family: {T.FONT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 4px 12px;
    color: {P.ACCENT_BLUE};
    font-weight: {T.FW_SEMI};
    font-size: {T.FS_MD};
    background-color: transparent;
    border-radius: {T.R_MD};
}}

/* === Zone card (Manual 탭 ①②③존) — 글래스 카드: 그라디언트 + R_XL === */
QGroupBox#ZoneCard {{
    border: 1px solid {P.BORDER_SECONDARY};
    border-radius: {T.R_XL};
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {P.BG_TERTIARY}, stop:1 {P.BG_SECONDARY});
}}
QGroupBox#ZoneCard::title {{
    color: {P.TEXT_PRIMARY};
    font-weight: {T.FW_BOLD};
}}
"""


def get_button_styles(P):
    """버튼 기본 스타일 (Secondary = default)"""
    return f"""
/* === Buttons === */
QPushButton {{
    background-color: {P.BG_TERTIARY};
    border: 1px solid {P.BORDER_PRIMARY};
    border-radius: {T.R_MD};
    padding: 8px 14px;
    font-family: {T.FONT};
    font-weight: {T.FW_SEMI};
    font-size: {T.FS_MD};
    color: {P.TEXT_PRIMARY};
}}
QPushButton:hover {{
    background-color: {P.BG_HOVER};
    border-color: {P.BORDER_LIGHT};
}}
QPushButton:pressed {{
    background-color: {P.BG_PRESSED};
}}
QPushButton:disabled {{
    background-color: {P.BG_DISABLED};
    color: {P.TEXT_DISABLED};
    border-color: {P.BORDER_SECONDARY};
}}
QPushButton:checked {{
    background-color: {P.ACCENT_BLUE};
    color: white;
    border-color: {P.ACCENT_BLUE_DARK};
}}
QPushButton:checked:hover {{
    background-color: {P.ACCENT_BLUE_DARK};
}}
"""


def get_tab_styles(P):
    """탭 스타일"""
    is_dark = hasattr(P, '__name__') and 'Dark' in P.__name__
    return f"""
/* === Tabs === */
QTabWidget::pane {{
    border: 1px solid {P.BORDER_PRIMARY};
    background: {P.BG_SECONDARY};
    border-radius: {T.R_LG};
}}
QTabBar::tab {{
    background: {P.BG_PRESSED if is_dark else P.BG_TERTIARY};
    color: {P.TEXT_SECONDARY};
    min-width: 120px;
    font-family: {T.FONT};
    font-weight: {T.FW_SEMI};
    font-size: {T.FS_MD};
    padding: 8px 16px;
    margin-right: 2px;
    border: 1px solid {P.BORDER_PRIMARY};
    border-bottom: none;
    border-top-left-radius: {T.R_LG};
    border-top-right-radius: {T.R_LG};
}}
QTabBar::tab:selected {{
    background: {P.BG_SECONDARY};
    color: {P.ACCENT_BLUE};
    border-bottom: 2px solid {P.ACCENT_BLUE};
}}
QTabBar::tab:hover:!selected {{
    background: {P.BG_SECONDARY if is_dark else P.BG_PRIMARY};
    color: {P.TEXT_TERTIARY if is_dark else P.TEXT_PRIMARY};
}}
"""


def get_table_styles(P):
    """테이블 스타일"""
    return f"""
/* === Tables === */
QTableWidget, QTableView {{
    background-color: {P.BG_INPUT};
    color: {P.TEXT_TABLE};
    gridline-color: {P.HEADER_BORDER};
    font-size: {T.FS_MD};
    border: 1px solid {P.BORDER_TABLE};
    border-radius: {T.R_LG};
    alternate-background-color: {P.BG_ALTERNATE};
}}
QTableWidget QWidget {{
    background-color: {P.BG_INPUT};
}}
QTableWidget::item {{
    background-color: {P.BG_INPUT};
    color: {P.TEXT_TABLE};
    padding: 4px;
}}
QTableWidget::item:alternate {{
    background-color: {P.BG_ALTERNATE};
}}
QTableWidget::item:selected {{
    background-color: {P.STATE_SELECTED};
    color: #ffffff;
}}
QTableCornerButton::section {{
    background-color: {P.HEADER_BG};
    border: 1px solid {P.HEADER_BORDER};
}}
QHeaderView {{
    background-color: {P.HEADER_BG};
}}
QHeaderView::section {{
    background-color: {P.HEADER_BG};
    color: {P.HEADER_TEXT};
    padding: 8px;
    font-weight: {T.FW_SEMI if hasattr(P, '__name__') and 'Dark' in P.__name__ else T.FW_BOLD};
    border: 1px solid {P.HEADER_BORDER};
}}
QHeaderView::section:vertical {{
    background-color: {P.HEADER_BG};
    color: {P.HEADER_TEXT};
    padding: 4px 8px;
    font-weight: {T.FW_SEMI if hasattr(P, '__name__') and 'Dark' in P.__name__ else T.FW_BOLD};
    border: 1px solid {P.HEADER_BORDER};
}}
"""


def get_input_styles(P):
    """입력 필드 스타일"""
    return f"""
/* === Inputs === */
QLineEdit, QComboBox {{
    background-color: {P.BG_INPUT};
    color: {P.TEXT_INPUT};
    border: 1px solid {P.BORDER_INPUT};
    padding: 6px 8px;
    font-weight: {T.FW_SEMI};
    min-height: {T.H_INPUT}px;
    font-family: {T.FONT};
    border-radius: {T.R_MD};
}}
QDoubleSpinBox, QSpinBox {{
    background-color: {P.BG_INPUT};
    color: {P.TEXT_INPUT};
    border: 1px solid {P.BORDER_INPUT};
    padding: 6px 8px;
    font-weight: {T.FW_SEMI};
    min-height: {T.H_INPUT}px;
    font-family: {T.FONT_MONO};
    border-radius: {T.R_MD};
}}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {P.ACCENT_BLUE if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_LIGHT};
    background-color: {P.BG_INPUT};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 18px;
    background-color: {P.BG_TERTIARY};
    border-left: 1px solid {P.BORDER_INPUT};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {P.BG_HOVER};
}}

/* === ComboBox Dropdown === */
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {'#606060' if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_LIGHT};
    margin-right: 5px;
}}
QComboBox QAbstractItemView {{
    background-color: {P.BG_INPUT};
    color: {P.TEXT_INPUT};
    border: 1px solid {P.BORDER_INPUT};
    selection-background-color: {P.STATE_SELECTED};
    selection-color: #ffffff;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 8px;
    min-height: 24px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {P.STATE_HOVER_INPUT};
    color: {P.TEXT_INPUT};
}}
"""


def get_lcd_styles(P):
    """LCD 디스플레이 스타일"""
    return f"""
/* === LCD Display === */
QLCDNumber {{
    background-color: {P.BG_DARK};
    color: {P.ACCENT_CYAN};
    border: 2px solid {P.BORDER_SECONDARY if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_LIGHT};
    border-radius: {T.R_SM};
}}
"""


def get_progressbar_styles(P):
    """진행바 스타일"""
    return f"""
/* === Progress Bar === */
QProgressBar {{
    border: 1px solid {P.BORDER_PRIMARY};
    text-align: center;
    font-weight: {T.FW_SEMI};
    color: {P.TEXT_PRIMARY};
    background-color: {P.BG_PRESSED if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_PRIMARY};
    border-radius: {T.R_LG};
    height: 22px;
}}
QProgressBar::chunk {{
    background-color: {P.ACCENT_BLUE};
    border-radius: {T.R_LG};
}}
"""


def get_log_styles(P):
    """로그 브라우저 스타일"""
    return f"""
/* === Log Browser === */
QTextBrowser {{
    font-family: {T.FONT_MONO};
    font-size: {T.FS_MD};
    background-color: {P.BG_DARK};
    color: {P.ACCENT_CYAN};
    border: 1px solid {P.BORDER_PRIMARY};
    border-radius: {T.R_LG};
}}
"""


def get_label_styles(P):
    """라벨 스타일"""
    return f"""
/* === Labels === */
QLabel {{
    font-family: {T.FONT};
    color: {P.TEXT_PRIMARY};
    background-color: transparent;
}}
"""


def get_scrollbar_styles(P):
    """스크롤바 스타일"""
    handle_color = P.BORDER_PRIMARY if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_PRIMARY
    handle_hover = P.BORDER_LIGHT if hasattr(P, '__name__') and 'Dark' in P.__name__ else P.BORDER_LIGHT

    return f"""
/* === ScrollBar === */
QScrollBar:vertical {{
    background: {P.BG_PRIMARY};
    width: 10px;
    border: none;
    border-radius: {T.R_SM};
}}
QScrollBar::handle:vertical {{
    background: {handle_color};
    border-radius: {T.R_SM};
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {handle_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {P.BG_PRIMARY};
    height: 10px;
    border: none;
    border-radius: {T.R_SM};
}}
QScrollBar::handle:horizontal {{
    background: {handle_color};
    border-radius: {T.R_SM};
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {handle_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
"""


def get_checkbox_radio_styles(P):
    """체크박스/라디오 버튼 포커스 링 & 호버 효과"""
    return f"""
/* === CheckBox & RadioButton === */
QCheckBox {{
    spacing: 6px;
    font-family: {T.FONT};
    font-size: {T.FS_MD};
    color: {P.TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {P.BORDER_LIGHT};
    border-radius: {T.R_SM};
    background-color: {P.BG_INPUT};
}}
QCheckBox::indicator:hover {{
    border-color: {P.ACCENT_BLUE};
}}
QCheckBox::indicator:focus {{
    border: 2px solid {P.ACCENT_BLUE};
}}
QCheckBox::indicator:checked {{
    background-color: {P.ACCENT_BLUE};
    border-color: {P.ACCENT_BLUE_DARK};
    image: none;
}}
QRadioButton {{
    spacing: 6px;
    font-family: {T.FONT};
    font-size: {T.FS_MD};
    color: {P.TEXT_PRIMARY};
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {P.BORDER_LIGHT};
    border-radius: 8px;
    background-color: {P.BG_INPUT};
}}
QRadioButton::indicator:hover {{
    border-color: {P.ACCENT_BLUE};
}}
QRadioButton::indicator:focus {{
    border: 2px solid {P.ACCENT_BLUE};
}}
QRadioButton::indicator:checked {{
    background-color: {P.ACCENT_BLUE};
    border-color: {P.ACCENT_BLUE_DARK};
}}
"""


def build_theme(palette):
    """
    전체 테마 스타일시트 생성

    @codesyncer-decision: 기능별 스타일 함수 조합
    - 각 위젯 타입별 get_xxx_styles() 함수 호출
    - 팔레트만 전달 → 다크/라이트 자동 처리
    - 중복 제거 + 유지보수성 향상
    """
    return (
        get_global_styles(palette) +
        get_dialog_styles() +
        get_groupbox_styles(palette) +
        get_button_styles(palette) +
        get_tab_styles(palette) +
        get_table_styles(palette) +
        get_input_styles(palette) +
        get_lcd_styles(palette) +
        get_progressbar_styles(palette) +
        get_log_styles(palette) +
        get_label_styles(palette) +
        get_scrollbar_styles(palette) +
        get_checkbox_radio_styles(palette)
    )


# ─── 위젯별 특수 스타일 생성 함수 (theme_manager.py용) ────────────

def get_secondary_button_style(P):
    """Secondary 버튼 — outlined, 보조 액션용."""
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {P.TEXT_TERTIARY};
            font-family: {T.FONT};
            font-weight: {T.FW_SEMI};
            font-size: {T.FS_SM};
            border: 1.5px solid {P.BORDER_LIGHT};
            border-radius: {T.R_SM};
            padding: 8px 14px;
        }}
        QPushButton:hover {{
            color: {P.TEXT_PRIMARY};
            background-color: {P.BG_HOVER};
            border-color: {P.ACCENT_BLUE};
        }}
        QPushButton:pressed {{
            background-color: {P.BG_PRESSED};
            border-color: {P.ACCENT_BLUE_DARK};
        }}
    """


def get_ghost_button_style(P):
    """Ghost 버튼 — 텍스트만, 최하위 액션용."""
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {P.TEXT_SECONDARY};
            font-family: {T.FONT};
            font-weight: {T.FW_SEMI};
            font-size: {T.FS_SM};
            border: none;
            border-radius: {T.R_SM};
            padding: 8px 8px;
        }}
        QPushButton:hover {{
            color: {P.TEXT_PRIMARY};
            background-color: {P.BG_HOVER};
        }}
        QPushButton:pressed {{ background-color: {P.BG_PRESSED}; }}
    """


def get_outlet_valve_button_style(P, button_type):
    """
    Outlet 밸브 버튼 스타일 (Waste/Collection) — 레퍼런스 트랙+pill 세그먼트.

    @param button_type: 'waste' | 'collection'
    @codesyncer-decision: 레퍼런스 close-up = Waste 선택 시 중립 raised pill,
      Collect 활성 = 녹색(IEC 60073 가동/수집). 트랙 안에서 선택만 pill 로 뜬다.
    """
    return get_segment_button_style(P, "neutral" if button_type == "waste" else "collect")


def get_action_button_style(P, button_type):
    """
    액션 버튼 스타일 (이동/전진/후진/홈)

    @param button_type: 'move' | 'back' | 'forward' | 'home' | 'set' | 'off'
    """
    _primary = f"""
            QPushButton {{
                background-color: {P.BTN_START}; color: white;
                font-family: {T.FONT}; font-weight: {T.FW_SEMI}; font-size: {T.FS_MD};
                border: 1px solid {P.BTN_START_HOVER}; border-radius: {T.R_SM};
                padding: 8px 14px;
            }}
            QPushButton:hover {{ background-color: {P.BTN_START_HOVER}; }}
            QPushButton:pressed {{ background-color: {P.BTN_START_HOVER}; }}
    """
    _neutral = f"""
            QPushButton {{
                background-color: {P.BTN_STOP}; color: white;
                font-family: {T.FONT}; font-weight: {T.FW_SEMI}; font-size: {T.FS_MD};
                border: 1px solid {P.BTN_STOP_HOVER}; border-radius: {T.R_SM};
                padding: 8px 14px;
            }}
            QPushButton:hover {{ background-color: {P.BTN_STOP_HOVER}; }}
            QPushButton:pressed {{ background-color: {P.BTN_STOP_HOVER}; }}
    """
    _danger = f"""
            QPushButton {{
                background-color: {P.BTN_STOP}; color: white;
                font-family: {T.FONT}; font-weight: {T.FW_SEMI}; font-size: {T.FS_MD};
                border: 1px solid {P.BTN_STOP_HOVER}; border-radius: {T.R_SM};
                padding: 8px 14px;
            }}
            QPushButton:hover {{ background-color: {P.BTN_STOP_HOVER}; }}
            QPushButton:pressed {{ background-color: {P.BTN_STOP_HOVER}; }}
    """

    styles = {
        'move': _primary, 'home': _primary, 'set': _primary,
        'back': _primary, 'forward': _primary,
        'off': _danger,
    }
    return styles.get(button_type, "")


def get_main_control_button_style(P, button_type):
    """Main top-right control button styles (pause/emergency).

    @codesyncer-decision: IEC 60073 의미색 체계 적용
    - Pause = 황색(amber, '주의/개입'): 기존 주황/파랑 악센트는 브랜드색과
      혼동되어 일시정지의 '주의' 의미가 전달되지 않았음
    - Emergency Stop = 적색 전용 (ISO 13850): 적색은 UI 전체에서
      비상정지/오류에만 사용해 의미 희석 방지. 황색 보더로 시인성 강화.
    """
    if button_type == "pause":
        # 황색 = 주의 (IEC 60073). 팔레트 토큰만 사용 (전역 hex 치환 패치 대응).
        amber = P.BTN_PAUSE
        amber_hover = P.BTN_PAUSE_HOVER
        amber_border = P.BTN_PAUSE_BORDER
        paused_bg = P.BTN_PAUSE_CHECKED
        paused_hover = P.BTN_PAUSE_CHECKED_HOVER
        text_dark = P.BG_DARK
        return f"""
            QPushButton {{
                background-color: {amber};
                color: {text_dark};
                font-size: {T.FS_LG};
                font-weight: {T.FW_BOLD};
                border: 1px solid {amber_border};
                border-radius: {T.R_LG};
            }}
            QPushButton:hover {{ background-color: {amber_hover}; }}
            QPushButton:checked {{
                background-color: {paused_bg};
                border-color: {amber_border};
                color: #ffffff;
            }}
            QPushButton:checked:hover {{ background-color: {paused_hover}; }}
        """

    # emergency — ISO 13850: 적색 본체 + 황색 링(보더)
    return f"""
        QPushButton {{
            background-color: {P.ACCENT_RED};
            color: #ffffff;
            font-size: {T.FS_LG};
            font-weight: {T.FW_BOLD};
            border: 2px solid {P.SAFETY_RING};
            border-radius: {T.R_LG};
            letter-spacing: 0.5px;
        }}
        QPushButton:hover {{ background-color: {P.ACCENT_RED_DARK}; }}
        QPushButton:pressed {{ background-color: {P.ACCENT_RED_DARK}; }}
    """


# ─── Manual v4 공통 버튼 언어 (레퍼런스 = Grafana풍 계기 대시보드, 2026-07-05) ──
# @codesyncer-decision: 세그먼트 필/상태반응 액션/배지/primary 를 한 곳에서 생성해
#   channel_column·tab_manual·pump_controls 가 공유 → 위젯마다 흩어진 인라인 hex 제거.
#   전역 QSS 패치가 비팔레트 hex 를 최근접색으로 스냅하므로 색은 팔레트 토큰만 사용.

# 세그먼트 선택 시 색: (팔레트 속성명, 텍스트색 or None=중립 raised pill+보더)
# @codesyncer-decision: 레퍼런스는 iOS풍 세그먼트 — 선택=중립 raised pill(위치/라벨로
#   구분), 색은 특수상태에만: 피드 WASTE=앰버(주의)·Collect=녹(가동). SOURCE/REACTOR
#   는 중립(구 청록/앰버 semantic 은 사용자 레퍼런스 지시로 중립화).
_SEG_ON = {
    "source":  ("BG_HOVER", None),                # 중립 raised pill
    "reactor": ("BG_HOVER", None),                # 중립 raised pill
    "waste":   ("ACT_INFUSE", "#ffffff"),         # 앰버 = 피드→폐기(주의)
    "collect": ("ACCENT_GREEN_DARK", "#ffffff"),  # 녹 = 수집 중(IEC 가동)
    "primary": ("ACCENT_BLUE", "#ffffff"),
    "neutral": ("BG_HOVER", None),                # 중립 raised pill
}

# 액션(솔리드) 버튼 활성 색: (bg 속성명, hover 속성명)
_ACTION_ON = {
    "infuse":   ("ACT_INFUSE", "ACT_INFUSE_HOVER"),
    "withdraw": ("ACT_FILL", "ACT_FILL_HOVER"),
    "stop":     ("ACT_STOP", "ACT_STOP_HOVER"),
}

# 상태 배지: (텍스트 속성명, 배경 틴트 속성명)
_BADGE = {
    "run":   ("STATE_RUN", "STATE_RUN_BG"),
    "warn":  ("STATE_WARN", "STATE_WARN_BG"),
    "info":  ("STATE_INFO", "STATE_INFO_BG"),
    "fault": ("STATE_FAULT", "STATE_FAULT_BG"),
    "idle":  ("STATE_IDLE", "STATE_IDLE_BG"),
}


def get_segment_track_style(P):
    """세그먼트 컨트롤의 '트랙' 컨테이너(QFrame#SegTrack) — 선택 pill 이 안에 떠 보이게."""
    return (f"QFrame#SegTrack {{ background-color: {P.BG_INPUT}; "
            f"border: 1px solid {P.BORDER_PRIMARY}; border-radius: {T.R_MD}; }}")


def get_segment_button_style(P, kind="neutral"):
    """트랙 안 세그먼트 버튼 — 미선택=투명 텍스트, 선택=raised pill.
    중립(kind fg=None)은 얇은 보더로 '떠 보이는' 필, 의미색은 보더 없이 채움."""
    on_name, on_fg = _SEG_ON.get(kind, _SEG_ON["neutral"])
    on_bg = getattr(P, on_name)
    if on_fg is None:
        on_fg = P.TEXT_PRIMARY
        checked_border = f"border: 1px solid {P.BORDER_LIGHT};"
    else:
        checked_border = "border: none;"
    return f"""
        QPushButton {{
            background-color: transparent; color: {P.TEXT_SECONDARY};
            border: none; border-radius: {T.R_SM}; padding: 6px 4px;
            font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; letter-spacing: 0.3px;
        }}
        QPushButton:hover:!checked {{ color: {P.TEXT_PRIMARY}; }}
        QPushButton:checked {{ background-color: {on_bg}; color: {on_fg}; {checked_border} font-weight: {T.FW_BOLD}; }}
        QPushButton:disabled {{ color: {P.TEXT_DISABLED}; }}
    """


def get_action_state_style(P, action, active):
    """대형 액션 버튼(INFUSE/WITHDRAW/STOP) — 활성=의미색 채움, 유휴=중립 raised."""
    if active:
        bg_name, hov_name = _ACTION_ON[action]
        bg = getattr(P, bg_name); hov = getattr(P, hov_name)
        fg = "#ffffff"; hov_fg = "#ffffff"
    else:
        bg = P.BG_TERTIARY; hov = P.BG_HOVER
        fg = P.TEXT_TERTIARY; hov_fg = P.TEXT_PRIMARY
    return f"""
        QPushButton {{
            background-color: {bg}; color: {fg}; border: none;
            border-radius: {T.R_MD}; font-weight: {T.FW_BOLD};
            font-size: {T.FS_XS}; letter-spacing: 0.5px;
        }}
        QPushButton:hover {{ background-color: {hov}; color: {hov_fg}; }}
        QPushButton:disabled {{ background-color: {P.BG_DISABLED}; color: {P.TEXT_DISABLED}; }}
    """


def get_badge_style(P, level="idle"):
    """상태 배지 칩 — 틴트 배경 + 의미색 텍스트(WCAG 대비)."""
    fg_name, bg_name = _BADGE.get(level, _BADGE["idle"])
    return (f"QLabel {{ color: {getattr(P, fg_name)}; background-color: {getattr(P, bg_name)}; "
            f"border: none; border-radius: {T.R_SM}; padding: 2px 8px; "
            f"font-size: {T.FS_XS}; font-weight: {T.FW_BOLD}; letter-spacing: 0.5px; }}")


def get_stop_button_style(P):
    """장비 STOP 버튼 — 오조작 방지 규격.

    @codesyncer-decision: INFUSE/WITHDRAW 와 동일한 회색조로 다닥다닥 붙으면
      정지하려다 주입을 누르는 치명적 오조작 위험(사용자 지적). 해법 =
      ① 공간 분리(전폭 별도 행) + ② 적색 아웃라인 식별(stop=red 산업 관례).
      솔리드 적색+황색 링은 E-Stop 전용(ISO 13850)이므로 아웃라인으로 위계 구분.
    """
    return f"""
        QPushButton {{
            background-color: transparent; color: {P.ACCENT_RED};
            border: 1.5px solid {P.ACCENT_RED};
            border-radius: {T.R_MD}; font-weight: {T.FW_BOLD};
            font-size: {T.FS_XS}; letter-spacing: 0.5px;
        }}
        QPushButton:hover {{ background-color: {P.ACCENT_RED}; color: #ffffff; }}
        QPushButton:pressed {{ background-color: {P.ACCENT_RED_DARK}; color: #ffffff; }}
        QPushButton:disabled {{ border-color: {P.BORDER_PRIMARY}; color: {P.TEXT_DISABLED}; background: transparent; }}
    """


def get_tool_button_style(P):
    """툴바 트랙(QFrame#SegTrack) 안의 액션 버튼 — 플랫 텍스트, hover=raised pill.

    @codesyncer-decision: 시퀀스탭 툴바 전문화 — 세그먼트 트랙의 시각 언어를
      (checkable 이 아닌) 클릭 액션 버튼에 이식. 미조작 시 조용하고,
      hover 에서만 pill 이 떠올라 밀도 높은 툴바가 정돈돼 보임.
    """
    return f"""
        QPushButton {{
            background-color: transparent; color: {P.TEXT_SECONDARY};
            border: none; border-radius: {T.R_SM}; padding: 6px 10px;
            font-size: {T.FS_SM}; font-weight: {T.FW_SEMI};
        }}
        QPushButton:hover {{ background-color: {P.BG_TERTIARY}; color: {P.TEXT_PRIMARY}; }}
        QPushButton:pressed {{ background-color: {P.BG_HOVER}; }}
        QPushButton:disabled {{ color: {P.TEXT_DISABLED}; }}
    """


def get_caption_style(P):
    """섹션 캡션(소제목) 공통 규격 — 매뉴얼탭 전 카드 동일 서체/크기/자간."""
    return (f"color: {P.TEXT_SECONDARY}; font-size: {T.FS_XS}; "
            f"font-weight: {T.FW_SEMI}; letter-spacing: 0.8px; "
            f"background: transparent; border: none;")


def get_primary_button_style(P):
    """Primary(강조) 버튼 — Start All / SET / 이동. 악센트 채움."""
    return f"""
        QPushButton {{
            background-color: {P.ACCENT_BLUE}; color: #ffffff; border: none;
            border-radius: {T.R_MD}; padding: 8px 16px;
            font-weight: {T.FW_SEMI}; font-size: {T.FS_SM};
        }}
        QPushButton:hover {{ background-color: {P.ACCENT_BLUE_DARK}; }}
        QPushButton:pressed {{ background-color: {P.ACCENT_BLUE_DARK}; }}
        QPushButton:disabled {{ background-color: {P.BG_DISABLED}; color: {P.TEXT_DISABLED}; }}
    """
