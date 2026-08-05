"""
UI 스타일 시스템 - 통합 색상 관리

@codesyncer-decision: 색상 통일화 시스템 적용
- ui/colors.py: 모든 색상 정의 (단일 진실 소스)
- ui/theme.py: 위젯별 스타일 생성 함수 (중복 제거)
- 이 파일: 전체 테마 스타일시트 생성 및 export

@codesyncer-context: 사용법
    from ui.styles import INDUSTRIAL_DARK_THEME, INDUSTRIAL_LIGHT_THEME
    app.setStyleSheet(INDUSTRIAL_DARK_THEME)

@codesyncer-decision: 색상 변경 방법
    1. ui/colors.py에서 원하는 색상 변경
    2. 모든 UI 자동 반영 (이 파일 수정 불필요)
"""

from ui.colors import DarkPalette, LightPalette
from ui.theme import build_theme


# @codesyncer-decision: Industrial Dark Theme 스타일 시스템
# @codesyncer-context: 산업용 제어 시스템을 위한 어두운 테마, 높은 대비, Pretendard 폰트 사용
INDUSTRIAL_DARK_THEME = build_theme(DarkPalette)


# @codesyncer-decision: Modern Minimalist Light Theme 스타일 시스템
# @codesyncer-context: Theme Factory의 Modern Minimalist 테마 적용
# 색상 팔레트: Charcoal(#36454f), Slate Gray(#708090), Light Gray(#d3d3d3), White(#ffffff)
# 폰트: DejaVu Sans Bold(헤더), DejaVu Sans(본문)
INDUSTRIAL_LIGHT_THEME = build_theme(LightPalette)


# Legacy theme (kept for compatibility)
DARK_THEME_QSS = """
/* === Global Settings === */
QWidget {
    color: #dcdde1;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 14px;
    background-color: #1e272e; /* App Background */
}

/* === Main Window & Splitter === */
QMainWindow { background-color: #1e272e; }
QSplitter::handle { background-color: #1e272e; border: 1px solid #3b4354; width: 6px; }
QSplitter::handle:hover { background-color: #2b6cee; }

/* === Card Style (Glassmorphism) === */
QGroupBox {
    background-color: #2f3640; /* Card Background */
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    margin-top: 0px;
    padding-top: 10px;
}

/* === Buttons === */
QPushButton {
    background-color: #3b4354;
    color: #ffffff;
    border: 1px solid #57606f;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover { background-color: #4b5563; border-color: #7f8fa6; }
QPushButton:pressed { background-color: #2f3640; }

/* Green Button (Start/Connect) */
QPushButton[role="primary"] {
    background-color: rgba(16, 185, 129, 0.15);
    color: #10B981;
    border: 1px solid rgba(16, 185, 129, 0.4);
}
QPushButton[role="primary"]:hover { background-color: rgba(16, 185, 129, 0.25); }

/* Red Button (Stop/Emergency) */
QPushButton[role="danger"] {
    background-color: rgba(239, 68, 68, 0.15);
    color: #EF4444;
    border: 1px solid rgba(239, 68, 68, 0.4);
}
QPushButton[role="danger"]:hover { background-color: rgba(239, 68, 68, 0.25); }

/* === Inputs (SpinBox, Edit) === */
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background-color: #151a21;
    border: 1px solid #3b4354;
    border-radius: 6px;
    padding: 6px;
    color: #ffffff;
    font-family: 'Consolas', 'JetBrains Mono', monospace; /* 숫자용 폰트 */
    font-weight: bold;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; } /* 화살표 숨김 */

/* === Labels === */
QLabel[role="subtitle"] { color: #9da6b9; font-size: 12px; font-weight: normal; }
QLabel[role="value"] { color: #ffffff; font-family: 'Consolas', monospace; font-size: 18px; font-weight: bold; }
QLabel[role="unit"] { color: #9da6b9; font-size: 12px; }

/* === ScrollArea === */
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
    background-color: transparent; border: none;
}
"""
