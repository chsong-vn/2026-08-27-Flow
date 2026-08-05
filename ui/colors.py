"""
Centralized UI color palettes for dark/light themes.
"""


from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Callable, Tuple


class DarkPalette:
    """Deep dark palette tuned for dashboard readability."""

    BG_PRIMARY = "#121418"
    BG_SECONDARY = "#181b22"
    BG_TERTIARY = "#222733"
    BG_INPUT = "#131722"
    BG_DARK = "#0c1018"
    BG_MONITOR = "#0e1320"
    BG_HOVER = "#2b3140"
    BG_PRESSED = "#1e232d"
    BG_DISABLED = "#1b2029"
    BG_ALTERNATE = "#171d28"

    TEXT_PRIMARY = "#f4f6fb"
    TEXT_SECONDARY = "#9ea7bb"
    TEXT_TERTIARY = "#cfd5e4"
    TEXT_INPUT = "#eef2fb"
    TEXT_DISABLED = "#687188"
    TEXT_TABLE = "#edf1fa"

    # 다크 모드 주 악센트 = EXPEC풍 시안 블루(#3b9eff) — 2026-07-04 사용자 결정.
    # (기존 오렌지 #ff7a2f 에서 전환. 오렌지는 열/온도 계열 SENSOR_TEMP·CHART_TEMP
    #  와 브랜드 마크에만 잔류 — 열=난색이라는 의미 매핑은 유지.)
    # 라이트 모드는 블루(#3B82F6). "ACCENT_BLUE"는 "주 악센트"의 의미.
    ACCENT_BLUE = "#3b9eff"
    ACCENT_BLUE_DARK = "#1f7fe0"
    ACCENT_GREEN = "#3fb950"
    ACCENT_GREEN_DARK = "#2ea043"
    ACCENT_RED = "#f85149"
    ACCENT_RED_DARK = "#da3633"

    # 펌프 동작 버튼 — 주 악센트(#3b9eff)와 같은 hue 로 통일
    # (구 #4A90D9 계열은 액센트 전환 후 '다른 파랑'으로 충돌했음)
    BTN_START = "#3b9eff"        # 주 동작 = 악센트와 동일
    BTN_START_HOVER = "#1f7fe0"
    BTN_WITHDRAW = "#7cc0ff"     # 같은 hue 의 연한 톤 (Withdraw/Refill)
    BTN_WITHDRAW_HOVER = "#5aabf5"
    BTN_STOP = "#6B7FA0"         # 슬레이트 블루 (정지/비활성 톤)
    BTN_STOP_HOVER = "#5A6D8F"
    # @codesyncer-decision: 의미 기반 액션 색 (위젯 간 색-개념 매핑 통일)
    # - INFUSE(시약을 반응기로 미는 비가역 위험동작) = 앰버 경고색
    # - WITHDRAW/SOURCE(용매 채움, 안전 가역) = 청록
    # - 동일 개념은 밸브/펌프 어느 위젯에서나 같은 색을 쓴다
    ACT_INFUSE = "#E8923A"       # 앰버 (주입 = 위험·비가역)
    ACT_INFUSE_HOVER = "#D47F28"
    ACT_FILL = "#2FA6A0"         # 청록 (충전/Withdraw = 안전)
    ACT_FILL_HOVER = "#248C87"
    ACT_STOP = "#6B7280"         # 중립 회색 (정지)
    ACT_STOP_HOVER = "#565C66"
    ACCENT_PURPLE = "#9d95ff"
    # 구 #0c71f7 은 새 악센트 옆에서 '세 번째 파랑'으로 충돌 + 다크 배경에서
    # 너무 어두움 → 악센트 계열의 밝은 톤으로 정렬
    ACCENT_CYAN = "#59b0ff"
    ACCENT_CYAN_ALT = "#59b0ff"
    ACCENT_GRAY = "#79839a"
    ACCENT_GRAY_DARK = "#626c84"

    BORDER_PRIMARY = "#2c3341"
    BORDER_SECONDARY = "#212736"
    BORDER_LIGHT = "#3a4558"
    BORDER_INPUT = "#3b475d"
    BORDER_TABLE = "#344055"
    SEPARATOR = "#2a3242"

    HEADER_BG = "#1e2431"
    HEADER_TEXT = "#d6deef"
    HEADER_BORDER = "#2f394b"

    STATE_SELECTED = "#3b9eff"
    STATE_HOVER_INPUT = "#2a3243"

    # ── ISA-101 / IEC 60073 의미색 (장비 상태 표시 전용) ──
    # @codesyncer-decision: 연구장비 HMI 표준 색 체계
    # - 적색: 오류/비상정지 전용 (IEC 60073) — 일반 버튼에 사용 금지
    # - 황색(amber): 주의/일시정지/미계산(stale) 상태
    # - 녹색: 정상 가동(RUNNING) 상태
    # - 칩(chip) 패턴: 어두운 틴트 BG + 컬러 텍스트 → WCAG 4.5:1 대비 확보
    STATE_RUN = "#3fb950"
    STATE_RUN_BG = "#173527"
    STATE_WARN = "#e3b341"
    STATE_WARN_BG = "#3a2d10"
    STATE_FAULT = "#ff6b63"
    STATE_FAULT_BG = "#3d1513"
    STATE_INFO = "#6cb2ff"
    STATE_INFO_BG = "#132f52"
    STATE_IDLE = "#9ea7bb"
    STATE_IDLE_BG = "#2a3348"

    # ── 표준 제어 버튼 색 (IEC 60073 / ISO 13850) ──
    # @codesyncer-risk: 전역 QSS 패치가 팔레트 외 hex를 최근접색으로 치환하므로
    #   버튼 색은 반드시 여기 토큰으로 등록해 의미색이 깨지지 않게 함
    BTN_PAUSE = "#e3b341"            # 황색 = 주의/일시정지
    BTN_PAUSE_HOVER = "#d4a437"
    BTN_PAUSE_BORDER = "#b8860b"
    BTN_PAUSE_CHECKED = "#9a7209"    # Paused(Resume 대기) 상태
    BTN_PAUSE_CHECKED_HOVER = "#876409"
    SAFETY_RING = "#f3c623"          # ISO 13850 비상정지 황색 링


class LightPalette:
    """Neutral light palette: white/gray surfaces with orange accent only."""

    BG_PRIMARY = "#eaf1fb"
    BG_SECONDARY = "#ffffff"
    BG_TERTIARY = "#edf1f6"
    BG_INPUT = "#ffffff"
    BG_DARK = "#2f3354"
    BG_MONITOR = "#f9fafc"
    BG_HOVER = "#e8edf4"
    BG_PRESSED = "#dfe6ef"
    BG_DISABLED = "#edf1f6"
    BG_ALTERNATE = "#f8fafd"

    TEXT_PRIMARY = "#1f2937"
    TEXT_SECONDARY = "#667085"
    TEXT_TERTIARY = "#46505f"
    TEXT_INPUT = "#1f2937"
    TEXT_DISABLED = "#a3adbc"
    TEXT_TABLE = "#1f2937"

    ACCENT_BLUE = "#3B82F6"
    ACCENT_BLUE_DARK = "#2563EB"
    ACCENT_GREEN = "#216e4e"
    ACCENT_GREEN_DARK = "#1a5c3e"
    ACCENT_RED = "#E8856B"
    ACCENT_RED_DARK = "#D4745A"

    BTN_START = "#3B82C4"
    BTN_START_HOVER = "#2D6EAE"
    BTN_WITHDRAW = "#6DA3D4"
    BTN_WITHDRAW_HOVER = "#5D93C4"
    BTN_STOP = "#6B7B99"
    BTN_STOP_HOVER = "#5A6A88"
    ACT_INFUSE = "#D17F2A"
    ACT_INFUSE_HOVER = "#B86C1E"
    ACT_FILL = "#2A9591"
    ACT_FILL_HOVER = "#1F7B77"
    ACT_STOP = "#7B8190"
    ACT_STOP_HOVER = "#666C7A"
    ACCENT_PURPLE = "#6b64c6"
    ACCENT_CYAN = "#2563EB"
    ACCENT_CYAN_ALT = "#2563EB"
    ACCENT_GRAY = "#737d8c"
    ACCENT_GRAY_DARK = "#626b78"

    BORDER_PRIMARY = "#d8dee8"
    BORDER_SECONDARY = "#e5eaf1"
    BORDER_LIGHT = "#b9c4d4"
    BORDER_INPUT = "#cad2df"
    BORDER_TABLE = "#d8dee8"
    SEPARATOR = "#e6ebf2"

    HEADER_BG = "#f2f5fa"
    HEADER_TEXT = "#344054"
    HEADER_BORDER = "#d8dee8"

    STATE_SELECTED = "#3B82F6"
    STATE_HOVER_INPUT = "#f1f5fb"

    # ── ISA-101 / IEC 60073 의미색 (장비 상태 표시 전용) ──
    # 라이트 모드: 연한 틴트 BG + 진한 컬러 텍스트 (WCAG 4.5:1 이상)
    STATE_RUN = "#15663e"
    STATE_RUN_BG = "#e3f4ea"
    STATE_WARN = "#8a5a06"
    STATE_WARN_BG = "#fdf3d7"
    STATE_FAULT = "#b3261e"
    STATE_FAULT_BG = "#fbe9e7"
    STATE_INFO = "#2563EB"
    STATE_INFO_BG = "#e8f0fe"
    STATE_IDLE = "#667085"
    STATE_IDLE_BG = "#eef1f5"

    # ── 표준 제어 버튼 색 (IEC 60073 / ISO 13850) ──
    BTN_PAUSE = "#f0b429"
    BTN_PAUSE_HOVER = "#de9e1f"
    BTN_PAUSE_BORDER = "#b07d0a"
    BTN_PAUSE_CHECKED = "#b07d0a"
    BTN_PAUSE_CHECKED_HOVER = "#9c6f09"
    SAFETY_RING = "#e0a800"


class T:
    """Design tokens — typography, spacing, sizing constants."""

    # ── Typography ──
    # @codesyncer-decision: 한국어 폴백(Malgun Gothic) 명시 — 없으면 Qt 가 시스템
    #   기본 Gulim(굴림, 구형)으로 폴백해 라틴=Segoe UI / 한글=굴림으로 이질감 발생.
    #   다이얼로그가 이미 쓰는 'Malgun Gothic' 로 통일(웹 그리드도 이 스택 상속).
    FONT = "'Segoe UI', 'Malgun Gothic', sans-serif"
    FONT_MONO = "'Consolas', monospace"

    FS_XS = "13px"     # hints, captions
    FS_SM = "14px"     # secondary labels, descriptions
    FS_MD = "15px"     # body, labels, standard buttons
    FS_LG = "17px"     # section titles
    FS_XL = "22px"     # metric values, hero subtitles
    FS_XXL = "26px"    # page titles, brand

    # ── Font weights ──
    FW_SEMI = "600"    # semi-bold (labels, buttons)
    FW_BOLD = "700"    # bold (headings)

    # ── Component heights ──
    # @codesyncer-decision: 표준 타겟 크기 체계 (WCAG 2.2: 최소 24px,
    #   장갑 착용 실험실 환경 권장 32px+; ISO 13850: 비상 조작부는 최대형)
    # 4단계 위계: 밀집 입력(28) < 표준 입력(36) < 일반 버튼(40) <
    #             주요 동작(44) < 임계 동작 START/E-STOP(52)
    H_INPUT_SM = 28    # StepCard 등 밀집 영역의 스핀박스/콤보 (최소 24 이상)
    H_INPUT = 36       # spinbox, line-edit, combo
    H_BTN   = 40       # standard button
    H_BTN_LG = 44      # primary / large action button
    H_BTN_XL = 52      # critical action (E-Stop, START)
    H_CHIP = 22        # 상태 배지(chip) 높이

    # ── Component widths (8px grid) ──
    W_INPUT_SM = 112   # 밀집 스핀박스 (값+suffix+버튼이 잘리지 않는 최소폭)
    W_INPUT = 128      # 표준 입력
    W_LABEL_COND = 72  # 조건 라벨 (반응온도: 등)

    # ── Border radius ──
    # @codesyncer-decision: 현대 터치패널 지오메트리 (EXPEC 레퍼런스, 2026-07-04)
    # R_SM 은 스크롤바 핸들 등 소형 요소가 쓰므로 유지 (radius > 절반이면 렌더 깨짐)
    R_SM = "4px"       # scrollbar handles, tiny chips
    R_MD = "10px"      # buttons, inputs (소프트 필)
    R_LG = "14px"      # cards, panels, large buttons
    R_XL = "16px"      # zone cards, dialogs, hero

    # ── Spacing (4px grid) ──
    SP_XS = 4
    SP_SM = 8
    SP_MD = 12
    SP_LG = 16
    SP_XL = 24


class DialogColors:
    """Keep native dialog contrast stable across themes."""

    BG = "#f0f0f0"
    TEXT = "#000000"
    BUTTON_BG = "#e1e1e1"
    BUTTON_HOVER = "#d0d0d0"
    BORDER = "#adadad"
    INPUT_BG = "#ffffff"
    SELECTED_BG = "#0078d7"
    SELECTED_TEXT = "#ffffff"


class DarkExtras:
    """Dark mode extra semantic colors used by charts/LCD/status chips."""

    SENSOR_TEMP = "#ff7a2f"
    SENSOR_PRESSURE = "#7bd0ff"
    LOG_TEXT = "#8bd7ff"
    WARNING = "#d29922"
    MIX = "#bc8cff"
    STATUS_RUN = "#77d0ac"
    STATUS_IDLE = "#b8bfd5"
    STATUS_RUN_BG = "#1f3b31"
    STATUS_IDLE_BG = "#2a3348"
    HERO_START = "#191b20"
    HERO_MID = "#1f232c"
    HERO_END = "#242a36"
    CHART_TEMP = "#ff7a2f"
    CHART_PRESSURE_SERIES = (
        "#ffd5bf",
        "#7fb3ff",
        "#56c79b",
        "#9e94ff",
        "#6bc6ea",
    )


class LightExtras:
    """Light mode extra semantic colors used by charts/LCD/status chips."""

    SENSOR_TEMP = "#3B82F6"
    SENSOR_PRESSURE = "#4f8ae0"
    LOG_TEXT = "#2563EB"
    WARNING = "#b8860b"
    MIX = "#7c3aed"
    STATUS_RUN = "#216e4e"
    STATUS_IDLE = "#686e8b"
    STATUS_RUN_BG = "#e5f3eb"
    STATUS_IDLE_BG = "#f1edf6"
    HERO_START = "#3B82F6"
    HERO_MID = "#2563EB"
    HERO_END = "#60A5FA"
    CHART_TEMP = "#3B82F6"
    CHART_PRESSURE_SERIES = (
        "#4f7ecf",
        "#2aa765",
        "#e69d43",
        "#7f72cc",
        "#3ca6c9",
    )


_ACTIVE_DARK_MODE = True
_STYLESHEET_PATCH_INSTALLED = False
_ORIGINAL_SET_STYLESHEET = None
_DARK_MODE_PROVIDER: Callable[[], bool] | None = None

_HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
_RGBA_RE = re.compile(r"rgba?\([^)]+\)", re.IGNORECASE)

_LEGACY_OVERRIDE_DARK = {
    "#f08b6d": DarkPalette.ACCENT_BLUE,
    "#242d42": DarkPalette.BG_TERTIARY,
    "#343e56": DarkPalette.BORDER_PRIMARY,
    "#2d3750": DarkPalette.BG_HOVER,
    "#c8cde0": DarkPalette.TEXT_TERTIARY,
    "#101726": DarkPalette.BG_DARK,
    "#0f1422": DarkPalette.BG_DARK,
    "#2a3348": DarkPalette.BORDER_PRIMARY,
    "#8ad7ff": DarkExtras.LOG_TEXT,
    "#7bd0ff": DarkExtras.SENSOR_PRESSURE,
    "#58c29a": DarkPalette.ACCENT_GREEN,
    "#58a6ff": DarkPalette.ACCENT_CYAN,
    "#e6e9f5": DarkPalette.TEXT_PRIMARY,
}

_LEGACY_OVERRIDE_LIGHT = {
    "#f08b6d": LightPalette.ACCENT_BLUE,
    "#242d42": LightPalette.BG_TERTIARY,
    "#343e56": LightPalette.BORDER_PRIMARY,
    "#2d3750": LightPalette.BG_HOVER,
    "#c8cde0": LightPalette.TEXT_TERTIARY,
    "#101726": LightPalette.BG_MONITOR,
    "#0f1422": LightPalette.BG_MONITOR,
    "#2a3348": LightPalette.BORDER_PRIMARY,
    "#8ad7ff": LightExtras.LOG_TEXT,
    "#7bd0ff": LightExtras.SENSOR_PRESSURE,
    "#58c29a": LightPalette.ACCENT_GREEN,
    "#58a6ff": LightPalette.ACCENT_CYAN,
    "#e6e9f5": LightPalette.TEXT_PRIMARY,
}


def set_active_dark_mode(is_dark: bool) -> None:
    """Set current app-wide theme mode used by runtime QSS color remapping."""
    global _ACTIVE_DARK_MODE
    _ACTIVE_DARK_MODE = bool(is_dark)


def get_active_dark_mode() -> bool:
    """Return current app-wide theme mode."""
    return _ACTIVE_DARK_MODE


def get_palette(is_dark: bool | None = None):
    """Return DarkPalette or LightPalette for given mode."""
    dark = _ACTIVE_DARK_MODE if is_dark is None else bool(is_dark)
    return DarkPalette if dark else LightPalette


def get_mode_extras(is_dark: bool | None = None):
    """Return DarkExtras or LightExtras for given mode."""
    dark = _ACTIVE_DARK_MODE if is_dark is None else bool(is_dark)
    return DarkExtras if dark else LightExtras


def get_chart_pressure_series(is_dark: bool | None = None) -> Tuple[str, ...]:
    """Get pressure chart line colors for current mode."""
    return tuple(get_mode_extras(is_dark).CHART_PRESSURE_SERIES)


def _normalize_hex(hex_value: str) -> str:
    v = hex_value.lower()
    if len(v) == 4:  # #rgb
        return "#" + "".join(ch * 2 for ch in v[1:])
    if len(v) in (7, 9):
        return v
    return v


def rgba(hex_color: str, alpha: float) -> str:
    """팔레트 hex 토큰 → 'rgba(r, g, b, a)' QSS 문자열.

    악센트 틴트 배경 등 반투명이 필요한 곳에서 rgba 리터럴을 직접 쓰지 말고
    이 헬퍼로 토큰에서 유도할 것 (토큰 변경 시 자동 추종).
    """
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _hex_to_rgb(hex_value: str) -> Tuple[int, int, int]:
    hv = _normalize_hex(hex_value)
    if len(hv) == 9:  # #rrggbbaa
        hv = hv[:7]
    if len(hv) != 7:
        return 0, 0, 0
    return int(hv[1:3], 16), int(hv[3:5], 16), int(hv[5:7], 16)


def _palette_color_pool(is_dark: bool) -> Tuple[str, ...]:
    palette = get_palette(is_dark)
    extras = get_mode_extras(is_dark)

    values = []
    # DialogColors are intentionally excluded in dark mode so legacy light QSS
    # tokens are remapped to dark palette colors instead of staying light.
    sources = (palette, extras) if is_dark else (palette, extras, DialogColors)
    for source in sources:
        for name, value in source.__dict__.items():
            if name.startswith("_"):
                continue
            if isinstance(value, str) and value.startswith("#"):
                values.append(_normalize_hex(value))
            elif isinstance(value, (tuple, list)):
                for entry in value:
                    if isinstance(entry, str) and entry.startswith("#"):
                        values.append(_normalize_hex(entry))

    # Keep insertion order and remove duplicates.
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=4096)
def _closest_palette_color(hex_value: str, is_dark: bool) -> str:
    src = _normalize_hex(hex_value)
    if src.startswith("#") and len(src) == 9:
        src = src[:7]

    overrides = _LEGACY_OVERRIDE_DARK if is_dark else _LEGACY_OVERRIDE_LIGHT
    if src in overrides:
        return _normalize_hex(overrides[src])

    candidates = _palette_color_pool(is_dark)
    if not candidates:
        return src
    if src in candidates:
        return src

    sr, sg, sb = _hex_to_rgb(src)
    best = candidates[0]
    best_dist = float("inf")
    for candidate in candidates:
        cr, cg, cb = _hex_to_rgb(candidate)
        dist = math.sqrt((sr - cr) ** 2 + (sg - cg) ** 2 + (sb - cb) ** 2)
        if dist < best_dist:
            best = candidate
            best_dist = dist
    return best


def _normalize_rgba_token(token: str, is_dark: bool) -> str:
    # Supports rgb(r,g,b) and rgba(r,g,b,a)
    raw = token.strip()
    open_idx = raw.find("(")
    close_idx = raw.rfind(")")
    if open_idx < 0 or close_idx < 0:
        return token

    fn = raw[:open_idx].lower()
    body = raw[open_idx + 1:close_idx]
    parts = [x.strip() for x in body.split(",")]
    if len(parts) not in (3, 4):
        return token

    try:
        r, g, b = [max(0, min(255, int(float(parts[i])))) for i in range(3)]
    except ValueError:
        return token

    nearest = _closest_palette_color(f"#{r:02x}{g:02x}{b:02x}", is_dark)
    nr, ng, nb = _hex_to_rgb(nearest)
    if fn == "rgb" or len(parts) == 3:
        return f"rgb({nr}, {ng}, {nb})"

    alpha = parts[3]
    return f"rgba({nr}, {ng}, {nb}, {alpha})"


def normalize_stylesheet_colors(stylesheet: str, is_dark: bool | None = None) -> str:
    """Map inline hex/rgba literals to the centralized palette in this file."""
    if not isinstance(stylesheet, str) or not stylesheet:
        return stylesheet

    dark = _ACTIVE_DARK_MODE if is_dark is None else bool(is_dark)

    def _hex_sub(match):
        return _closest_palette_color(match.group(0), dark)

    out = _HEX_RE.sub(_hex_sub, stylesheet)
    out = _RGBA_RE.sub(lambda m: _normalize_rgba_token(m.group(0), dark), out)
    return out


def remap_widget_tree_styles(root_widget, is_dark: bool | None = None) -> None:
    """Re-apply centralized color mapping to root and all child widget styles."""
    if root_widget is None:
        return

    dark = _ACTIVE_DARK_MODE if is_dark is None else bool(is_dark)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QWidget

    stack = [root_widget]
    while stack:
        widget = stack.pop()
        try:
            style = widget.styleSheet()
            if style:
                widget.setStyleSheet(normalize_stylesheet_colors(style, dark))
        except Exception:
            pass
        try:
            stack.extend(widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly))
        except Exception:
            pass


def install_global_stylesheet_color_patch(
    dark_mode_provider: Callable[[], bool] | None = None,
) -> None:
    """Patch QWidget.setStyleSheet so inline colors are centralized via this module."""
    global _STYLESHEET_PATCH_INSTALLED, _ORIGINAL_SET_STYLESHEET, _DARK_MODE_PROVIDER

    if dark_mode_provider is not None:
        _DARK_MODE_PROVIDER = dark_mode_provider

    if _STYLESHEET_PATCH_INSTALLED:
        return

    from PyQt5.QtWidgets import QWidget  # local import keeps this module lightweight

    _ORIGINAL_SET_STYLESHEET = QWidget.setStyleSheet

    def _patched_set_stylesheet(widget, stylesheet):
        if isinstance(stylesheet, str) and stylesheet:
            try:
                dark = bool(_DARK_MODE_PROVIDER()) if _DARK_MODE_PROVIDER else _ACTIVE_DARK_MODE
            except Exception:
                dark = _ACTIVE_DARK_MODE
            stylesheet = normalize_stylesheet_colors(stylesheet, dark)
        _ORIGINAL_SET_STYLESHEET(widget, stylesheet)

    QWidget.setStyleSheet = _patched_set_stylesheet
    _STYLESHEET_PATCH_INSTALLED = True
