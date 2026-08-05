# 파일 위치: ui/visual_diagram.py

# @codesyncer-decision: 동적 공정도 시스템 (SVG 기반)
# @codesyncer-context: 하드웨어 설정에 따라 동적으로 공정도가 변경됨
# - 12-way 밸브: 포트 번호 표시 (P:3) + SVG 아이콘
# - 3-way 밸브: 방향 화살표 (Source ↔ Reactor) + SVG 아이콘
# - 펌프: 가동 상태 + 유량 애니메이션 + SVG 아이콘

import os
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsItem
from PyQt5.QtGui import (QPen, QColor, QPainter, QPainterPath, QFont, QBrush,
                         QLinearGradient, QFontMetricsF)
from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal, QByteArray
from PyQt5.QtSvg import QSvgRenderer
from ui.colors import DarkExtras, DarkPalette as Dark, LightExtras, LightPalette as Light
from hardware.factory import HardwareFactory

# =============================================================================
# [SVG 경로 및 헬퍼 함수]
# =============================================================================
# @codesyncer-decision: assets 폴더에서 SVG 로드
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

def load_svg_with_color(svg_filename, color):
    """
    SVG 파일을 로드하고 currentColor를 지정된 색상으로 교체

    Args:
        svg_filename: assets 폴더 내 SVG 파일명
        color: QColor 객체

    Returns:
        QSvgRenderer 객체 또는 None
    """
    svg_path = os.path.join(ASSETS_DIR, svg_filename)
    if not os.path.exists(svg_path):
        return None

    try:
        with open(svg_path, 'r', encoding='utf-8') as f:
            svg_content = f.read()

        # currentColor를 실제 색상으로 교체
        hex_color = color.name()  # #rrggbb 형식
        svg_content = svg_content.replace('currentColor', hex_color)

        # QSvgRenderer 생성
        renderer = QSvgRenderer(QByteArray(svg_content.encode('utf-8')))
        return renderer if renderer.isValid() else None
    except Exception:
        return None

# =============================================================================
# [설정] 색상 및 디자인 - 다크/라이트 테마 지원
# =============================================================================
# ═══════════════════════════════════════════════════════════════
# Diagram v2 — 레이아웃 계약 (ISA-101 레인 시스템)
# @codesyncer-decision: 충돌은 규칙 부재의 산물 — 텍스트 계급마다 영구
#   레인을 배정하고 모든 paint() 가 LANE_* 상수만 사용한다.
#   구 gap_y=115 는 행 텍스트 엔벨로프(±62=124px)보다 작아 충돌이 필연이었음.
# 레인(파이프 중심선 yP 기준 오프셋):
#   L1 NAME   (-56..-34)  그룹명 전용 (9pt DemiBold)
#   L2 SYMBOL (-28..+28)  심볼 + 파이프 + 인라인 값 칩
#   L3 TAG    (+32..+50)  ISA풍 태그·타입 ("SV-A1 · 12-Way", 8pt 60%)
#   L4 VALUE  (+54..+76)  장비 라이브 값 (Consolas 9pt Bold)
# ═══════════════════════════════════════════════════════════════
ROW_PITCH = 150
ROW_TOP = 86
LANE_NAME = (-56.0, -34.0)
LANE_SYMBOL = (-28.0, 28.0)
LANE_TAG = (32.0, 50.0)
LANE_VALUE = (54.0, 76.0)

# 파이프 3단 굵기 위계 — 구 8px 수준의 두툼한 튜빙감 유지 + 활성 경로 승급
# (사용자 피드백: 가는 라인은 배관으로 안 읽힘 → 전체 상향, 위계는 유지)
W_MAIN = 10.0       # 합류 후 주 공정 경로 (활성 시)
W_CHANNEL = 8.0     # 채널/푸시 라인 (활성 시)
W_INACTIVE = 6.0    # 비활성(회색) 상태 공통
CHEVRON_SPACING = 26
CHEVRON_STEP = 2

UNIFIED_WIDTH = W_CHANNEL   # 하위호환 참조용
FLOW_ANIM_WIDTH = 4

# @codesyncer-decision: 다크 테마 색상 팔레트 (Lab Midnight)
C_OFF       = QColor(Dark.ACCENT_GRAY_DARK)   # 꺼짐
C_ACTIVE    = QColor(Dark.ACCENT_CYAN)        # 활성
C_RUNNING   = QColor(Dark.ACCENT_GREEN)       # 가동 중
C_WARNING   = QColor(DarkExtras.WARNING)      # 경고
C_REAGENT   = QColor(Dark.ACCENT_RED)         # 시약
C_MIX       = QColor(DarkExtras.MIX)          # 혼합
C_WASTE     = QColor(Dark.TEXT_DISABLED)      # 폐기
C_BG        = QColor(Dark.BG_DARK)            # 배경
C_BORDER    = QColor(Dark.BORDER_PRIMARY)     # 테두리
C_TEXT      = QColor(Dark.TEXT_TABLE)         # 텍스트

# @codesyncer-decision: 라이트 테마 색상 팔레트 (Lab White)
# @codesyncer-context: Scientific Instrument 라이트 팔레트 (LightPalette 기반)
C_OFF_LIGHT       = QColor(Light.ACCENT_GRAY)      # 꺼짐
C_ACTIVE_LIGHT    = QColor(Light.ACCENT_CYAN)      # 활성
C_RUNNING_LIGHT   = QColor(Light.ACCENT_GREEN)     # 가동 중
C_WARNING_LIGHT   = QColor(LightExtras.WARNING)    # 경고
C_REAGENT_LIGHT   = QColor(Light.ACCENT_RED)       # 시약
C_MIX_LIGHT       = QColor(LightExtras.MIX)        # 혼합
C_WASTE_LIGHT     = QColor(Light.TEXT_DISABLED)    # 폐기
C_BG_LIGHT        = QColor(Light.BG_SECONDARY)     # 배경
C_BORDER_LIGHT    = QColor(Light.BORDER_PRIMARY)   # 테두리
C_TEXT_LIGHT      = QColor(Light.TEXT_PRIMARY)     # 텍스트

# @codesyncer-decision: 요소 배경색 (비활성 시)
C_ELEMENT_BG       = QColor(Dark.BG_SECONDARY)  # 다크모드
C_ELEMENT_BG_LIGHT = QColor(Light.BG_HOVER)     # 라이트모드



# ── 배관도 타이포그래피 규격 ──────────────────────────────────
# @codesyncer-decision: 폰트 위계 통일 (기존 7/8/9pt Pretendard/Consolas 혼재)
# - FONT_NODE  : 장비명 (노드 '위' 고정)
# - FONT_SUB   : 보조 표기 (12-Way, 3-Way, S/R, T1, 포트번호)
# - FONT_VALUE : 상태 수치 (노드 '아래' 고정, 모노)
# - FONT_CHIP  : 데드볼륨 칩 (모노)
def _font(family, size, weight=None, bold=False):
    f = QFont(family, size)
    if weight is not None:
        f.setWeight(weight)
    if bold:
        f.setBold(True)
    return f


def _font_px(family, px, weight=None, bold=False):
    """픽셀 고정 폰트 — QGraphicsScene 좌표계의 DPI 불변 타이포그래피.

    @codesyncer-decision: pt 폰트는 OS 배율 150%에서 씬 좌표 기준 1.5×로
    렌더되는데 기하(레인·세그먼트)는 그대로라 칩이 심볼을 침범했음.
    px 고정이면 씬 안에서 글자 크기가 상수가 되고, fitInView 가 도면 전체를
    (글자 포함) 균일 스케일 → 어떤 DPI 에서도 설계 비율 유지.
    """
    f = QFont(family)
    f.setPixelSize(px)
    if weight is not None:
        f.setWeight(weight)
    if bold:
        f.setBold(True)
    return f

# 서체: 앱 표준(T.FONT='Segoe UI')과 통일 — 구 'Pretendard'는 미설치 시
# 더 넓은 폴백 폰트로 대체되어 고정 rect 를 넘쳐 잘리는 원인이었음
FONT_NODE = _font_px('Segoe UI', 12, QFont.DemiBold)
FONT_SUB = _font_px('Segoe UI', 11)
FONT_VALUE = _font_px('Consolas', 12, bold=True)
FONT_CHIP = _font_px('Consolas', 11)


def _draw_text(painter, rect, text, align=Qt.AlignCenter):
    """drawText 의 클리핑 rect 를 폰트 실측폭 이상으로 넓혀 그린다.

    @codesyncer-decision: drawText(rect, ...) 는 rect 로 클리핑됨 —
    OS DPI 스케일(125/150%)·폰트 폴백에서 텍스트가 고정 rect 를 넘어
    좌우가 잘리던 결함("utlet 3-Wa", ".00 mL")의 근본 수정.
    가로는 중심 유지 확장, 세로는 폰트 높이 보장.
    """
    fm = QFontMetricsF(painter.font())
    r = QRectF(rect)
    need_w = fm.horizontalAdvance(text) + 8
    if need_w > r.width():
        cx = r.center().x()
        r.setWidth(need_w)
        r.moveCenter(QPointF(cx, r.center().y()))
    if fm.height() > r.height():
        cy = r.center().y()
        r.setHeight(fm.height())
        r.moveCenter(QPointF(r.center().x(), cy))
    painter.drawText(r, align, text)


def _lane_rect(lane, cx=0.0, half_w=60.0):
    """레인 튜플(top, bottom) → 노드 로컬 좌표 rect."""
    top, bot = lane
    return QRectF(cx - half_w, top, half_w * 2, bot - top)


def _draw_lane(painter, lane, text, font, color, cx=0.0, half_w=60.0):
    """레인 계약 텍스트 — 모든 노드는 이 헬퍼로만 텍스트를 배치한다.

    세로 위치가 상수로 고정되므로 노드별 매직 오프셋이 사라지고,
    행 피치(ROW_PITCH)가 레인 엔벨로프(-56..+76)보다 크면
    행 간 충돌이 구조적으로 불가능하다.
    """
    painter.setFont(font)
    painter.setPen(color)
    _draw_text(painter, _lane_rect(lane, cx, half_w), text)


def _tag_color():
    """L3 태그용 60% 저채도 색 (호출 시점의 테마 C_TEXT 반영)."""
    c = QColor(C_TEXT)
    c.setAlpha(150)
    return c


class VisualSource(QGraphicsItem):
    """시약 바이알 노드 — 구 Visual12WayValve 내장 바이알 박스의 독립 승격.

    독립 노드가 되어야 inlet 데드볼륨 칩이 소스↔셀렉터 '파이프 위'에
    인라인으로 놓일 수 있다 (구조상 바이알 박스와 충돌 불가).
    """

    def __init__(self, number=1):
        super().__init__()
        self.number = number
        self.active = False
        self.setZValue(10)

    def boundingRect(self):
        return QRectF(-34, -60, 68, 116)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        body = QRectF(-15, -18, 30, 36)
        border = QPen(C_RUNNING if self.active else C_BORDER,
                      2 if self.active else 1.5)
        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(border)
        painter.drawRoundedRect(body, 6, 6)
        # 액면 표현 — '병'으로 읽히게 하단 45% 채움 (가동 시 녹색 틴트)
        liq = QColor(C_RUNNING) if self.active else QColor(C_TEXT)
        liq.setAlpha(90 if self.active else 45)
        painter.setBrush(liq)
        painter.setPen(Qt.NoPen)
        lq = QRectF(body.left() + 2, body.top() + body.height() * 0.55,
                    body.width() - 4, body.height() * 0.45 - 2)
        painter.drawRoundedRect(lq, 4, 4)
        # 캡(뚜껑) 라인
        painter.setPen(QPen(QColor(C_BORDER), 2))
        painter.drawLine(QPointF(-9, -18), QPointF(9, -18))
        painter.setPen(C_TEXT)
        painter.setFont(_font_px('Consolas', 14, bold=True))
        _draw_text(painter, QRectF(-15, -16, 30, 22), str(self.number))
        _draw_lane(painter, LANE_TAG, "VIAL", FONT_SUB, _tag_color(), half_w=30)

class VisualPipe(QGraphicsPathItem):
    """배관 그래픽 아이템 — v2: 굵기 위계 + 글로우 + 방향 체브론 + 포트 도트"""
    def __init__(self, path, weight=W_CHANNEL):
        super().__init__()
        self.setPath(path)
        self.weight = weight        # 활성 시 굵기 (W_MAIN/W_CHANNEL)
        self.width = weight         # 하위호환 별칭
        self._color = C_OFF
        self._flow_offset = 0
        self._flowing = False
        self.set_color(C_OFF)
        self.setZValue(0)

    def boundingRect(self):
        # 글로우 언더레이(+6px)와 포트 도트 여유 포함
        return super().boundingRect().adjusted(-8, -8, 8, 8)

    def set_color(self, color):
        self._color = color
        # 비활성(회색)=W_INACTIVE 균일, 활성=위계 굵기 — "굵기=지금 흐르는 길"
        w = W_INACTIVE if color == C_OFF else self.weight
        self.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def set_flowing(self, flowing):
        """유동 애니메이션 활성화/비활성화"""
        self._flowing = flowing
        self.update()

    def advance_flow(self):
        """유동 애니메이션 진행 (체브론 드리프트 ≈40px/s — 차분한 HMI 속도)"""
        if self._flowing:
            self._flow_offset = (self._flow_offset + CHEVRON_STEP) % CHEVRON_SPACING
            self.update()

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        path = self.path()

        # 1) 글로우 언더레이 — 활성 경로의 "지금 여기" 신호 (2-pass 페인터,
        #    QGraphicsEffect 없이 20fps 저비용)
        if self._flowing and self._color != C_OFF:
            glow = QColor(self._color)
            glow.setAlpha(45)
            painter.setPen(QPen(glow, self.pen().widthF() + 6,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        # 2) 본선
        super().paint(painter, option, widget)

        # 3) 포트 도트 — 배관이 심볼에 '도킹'되어 보이도록 양 끝점 마감
        painter.setBrush(QColor(self._color))
        painter.setPen(Qt.NoPen)
        r = self.pen().widthF() / 2.0 + 2.5
        for t in (0.0, 1.0):
            painter.drawEllipse(path.pointAtPercent(t), r, r)

        # 4) 방향 체브론 — 점선과 달리 방향을 내재적으로 표현
        #    (세로 매니폴드/푸시 라이저의 방향 모호성 해소)
        if self._flowing and self._color != C_OFF:
            length = path.length()
            if length > CHEVRON_SPACING:
                cev = QColor(C_BG)
                cev.setAlpha(210)   # 배경색 체브론 = 양 테마에서 파이프색과 확실한 대비
                painter.setPen(QPen(cev, max(2.0, self.pen().widthF() * 0.32),
                                    Qt.SolidLine, Qt.RoundCap))
                s = max(4.0, self.pen().widthF() * 0.7)   # 굵기에 비례한 체브론
                pos = self._flow_offset % CHEVRON_SPACING
                while pos < length:
                    t = pos / length
                    pt = path.pointAtPercent(t)
                    ang = path.angleAtPercent(t)
                    painter.save()
                    painter.translate(pt)
                    painter.rotate(-ang)
                    painter.drawLine(QPointF(-s, -s), QPointF(0, 0))
                    painter.drawLine(QPointF(-s, s), QPointF(0, 0))
                    painter.restore()
                    pos += CHEVRON_SPACING


class VisualSampler(QGraphicsItem):
    """오토샘플러(니들+바이알 랙) 노드 — autosampler 라우팅 전용.

    @codesyncer-decision: 구 배관도는 autosampler 를 internal_valve 와 동일하게
    (단일 바이알) 그려 구분이 불가능했음. 랙+니들 암 심볼로 "니들이 소스를
    골라 찍는" 구성임을 표현. 엔진의 autosampler 조율 태스크는 예약 상태
    (internal_valve 폴백)지만 배관도는 실제 하드웨어 구성을 보여준다.
    """

    def __init__(self):
        super().__init__()
        self.active = False
        self.needle_slot = 0    # 현재 니들 위치 (0-3, 표시용)
        self.setZValue(10)

    def boundingRect(self):
        return QRectF(-40, -60, 80, 116)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        # 랙 본체
        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(QPen(QColor(C_RUNNING) if self.active else C_BORDER,
                            2 if self.active else 1.5))
        painter.drawRoundedRect(QRectF(-26, -10, 52, 28), 5, 5)
        # 바이알 슬롯 4개 (액면 포함)
        for k in range(4):
            x0 = -21 + k * 11.5
            painter.setPen(QPen(QColor(C_BORDER), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(x0, -6, 8, 20), 2, 2)
            liq = QColor(C_RUNNING) if (self.active and k == self.needle_slot) \
                else QColor(C_TEXT)
            liq.setAlpha(100 if self.active and k == self.needle_slot else 45)
            painter.setBrush(liq)
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(x0 + 1.5, 4, 5, 8.5))
        # 니들 암 (현재 슬롯 위)
        nx = -17 + self.needle_slot * 11.5
        arm = QColor(C_RUNNING) if self.active else QColor(C_TEXT)
        arm.setAlpha(220)
        painter.setPen(QPen(arm, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(nx, -22), QPointF(nx, -8))
        painter.drawLine(QPointF(nx - 6, -22), QPointF(nx + 10, -22))
        # L3 태그
        _draw_lane(painter, LANE_TAG, getattr(self, "tag", "RACK"),
                   FONT_SUB, _tag_color(), half_w=34)


class Visual12WayValve(QGraphicsItem):
    """
    12방향 셀렉터 밸브 (Runze) - SVG 기반
    - SVG 아이콘 렌더링
    - 좌측 바이알 번호 박스 (가로 배치)
    - 중앙 포트 번호 오버레이
    """
    def __init__(self, label=""):
        super().__init__()
        self.label = label
        self.port = 1  # 현재 선택된 포트 (1-12)
        self.active = False
        self._svg_renderer = None
        self._last_color = None
        self.setZValue(10)

    def boundingRect(self):
        # v2: 바이알 분리(VisualSource) — 심볼 + L3 태그 레인만 커버
        return QRectF(-70, -60, 140, 116)

    def _update_svg(self):
        """상태에 따라 SVG 렌더러 업데이트"""
        color = C_RUNNING if self.active else C_TEXT
        if color != self._last_color:
            self._svg_renderer = load_svg_with_color("valve_12way.svg", color)
            self._last_color = color

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        import math as _m

        # === v3 셀렉터 심볼: 외곽 원 + 12 포트 도트 + 허브→선택포트 통로 ===
        # @codesyncer-decision: SVG(시계처럼 읽힘)·중앙 P:n(로터와 겹침) 제거.
        #   포트=도트, 선택 경로=허브에서 뻗는 통로 바 — "멀티포트 셀렉터"가
        #   그림만으로 읽히고, 포트 번호는 L4 값 레인으로 이동.
        sel = QColor(C_RUNNING) if self.active else QColor(C_ACTIVE)
        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(QPen(sel if self.active else C_BORDER,
                            2 if self.active else 1.5))
        painter.drawEllipse(QRectF(-26, -26, 52, 52))

        dot = QColor(C_TEXT)
        dot.setAlpha(110)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot)
        for k in range(12):
            a = _m.radians(k * 30 - 90)   # 포트 1 = 12시 방향
            painter.drawEllipse(QPointF(20 * _m.cos(a), 20 * _m.sin(a)), 2.2, 2.2)

        # 선택 포트 통로 (허브→포트) + 확대 포트 도트 — '열린 길'의 그래픽 표현
        a = _m.radians((self.port - 1) * 30 - 90)
        painter.setPen(QPen(sel, 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(0, 0), QPointF(20 * _m.cos(a), 20 * _m.sin(a)))
        painter.setBrush(sel)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(20 * _m.cos(a), 20 * _m.sin(a)), 4.2, 4.2)
        painter.drawEllipse(QPointF(0, 0), 3.6, 3.6)   # 허브(공통 포트)

        # L3 태그 / L4 선택 포트 값
        _draw_lane(painter, LANE_TAG, getattr(self, "tag", self.label),
                   FONT_SUB, _tag_color(), half_w=36)
        _draw_lane(painter, LANE_VALUE, f"P:{self.port}", FONT_VALUE,
                   sel if self.active else C_TEXT, half_w=30)

    def set_port(self, port_num):
        """포트 번호 설정 (1-12)"""
        try:
            port_int = int(port_num) if port_num else 1
            self.port = max(1, min(12, port_int))
        except (ValueError, TypeError):
            self.port = 1
        self.update()


class Visual3WayValve(QGraphicsItem):
    """
    3방향 스위처 밸브 - SVG 기반
    - SVG 아이콘 렌더링
    - Source (1) / Reactor (2) 방향 표시 오버레이
    """
    def __init__(self, label=""):
        super().__init__()
        self.label = label
        self.direction = 1  # 1: Source, 2: Reactor
        self.active = False
        self._svg_renderer = None
        self._last_color = None
        self.setZValue(10)

    def boundingRect(self):
        # v2: 심볼 + L3 태그 + L4 방향값 레인 커버
        return QRectF(-64, -60, 128, 140)

    def _update_svg(self):
        """상태에 따라 SVG 렌더러 업데이트"""
        color = C_RUNNING if self.active else C_TEXT
        if color != self._last_color:
            self._svg_renderer = load_svg_with_color("valve_3way.svg", color)
            self._last_color = color

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)

        # === v3 밸브 심볼: 원 본체 + 3 포트 스텁 + '열린 통로' 볼드 바 ===
        # @codesyncer-decision: 화살표(동작처럼 읽힘) 대신 밸브 내부의 열린
        #   통로를 그린다 — äKTA/DeltaV 계열 HMI 의 표준 밸브 묘사.
        #   흐르기 전에도 "액체가 어느 길로 정렬돼 있는지"가 그림으로 보임.
        on = QColor(C_RUNNING) if self.active else QColor(C_ACTIVE)
        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(QPen(on if self.active else C_BORDER,
                            2 if self.active else 1.5))
        painter.drawEllipse(QRectF(-18, -18, 36, 36))

        # 포트 스텁 3개 (W=Source측, E=Reactor측, S=공통/펌프측) — 배관 굵기 동조
        stub = QColor(C_TEXT)
        stub.setAlpha(120)
        painter.setPen(QPen(stub, 4.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(-18, 0, -26, 0)
        painter.drawLine(18, 0, 26, 0)
        painter.drawLine(0, 18, 0, 24)

        # 열린 통로 (허브→선택 방향) — 파이프와 같은 급의 두툼한 바
        painter.setPen(QPen(on, 7, Qt.SolidLine, Qt.RoundCap))
        if self.direction == 1:      # → Source (좌측 통로 개방)
            painter.drawLine(-16, 0, 0, 0)
        else:                        # → Reactor (우측 통로 개방)
            painter.drawLine(0, 0, 16, 0)
        painter.setBrush(on)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(0, 0), 4.0, 4.0)

        # 포트 문자 S/R — 스텁 '위' 고정 좌표
        painter.setPen(_tag_color())
        painter.setFont(FONT_SUB)
        painter.drawText(QRectF(-42, -28, 20, 14), Qt.AlignCenter, "S")
        painter.drawText(QRectF(22, -28, 20, 14), Qt.AlignCenter, "R")

        # L3 태그 (짧은 태그만), L4 경로 값 ("→ Reactor")
        _draw_lane(painter, LANE_TAG, getattr(self, "tag", self.label),
                   FONT_SUB, _tag_color(), half_w=36)
        route = "→ Source" if self.direction == 1 else "→ Reactor"
        _draw_lane(painter, LANE_VALUE, route, FONT_VALUE,
                   QColor(C_RUNNING) if self.active else C_TEXT, half_w=55)

    def set_direction(self, direction):
        """방향 설정 (1: Source, 2: Reactor)"""
        try:
            dir_val = int(direction) if direction else 2
            self.direction = 1 if dir_val == 1 else 2
        except (ValueError, TypeError):
            self.direction = 2
        self.update()


class VisualPump(QGraphicsItem):
    """
    펌프 컴포넌트 - SVG 기반
    - SVG 아이콘 렌더링 (pump.svg, pump_hplc.svg, pump_sf10.svg)
    - 가동 상태 표시 (초록/회색)
    - 유량 표시 오버레이
    - 가동 중 애니메이션
    """
    def __init__(self, label="", pump_type="syringe"):
        super().__init__()
        self.label = label
        self.pump_type = pump_type  # "syringe", "hplc", "sf10"
        self.running = False
        self.flow_rate = 0.0  # mL/min
        self.name_dx = 0      # L1 이름 가로 오프셋 (푸시 펌프: 세로 라이저 회피)
        self._anim_phase = 0
        self._svg_renderer = None
        self._last_color = None
        self.setZValue(10)

    def boundingRect(self):
        # v2: L1 이름 + 심볼 + L3 태그 + L4 유량 레인 커버
        return QRectF(-80, -60, 160, 140)

    def _get_svg_filename(self):
        """펌프 타입에 따른 SVG 파일명 반환"""
        if self.pump_type == "hplc":
            return "pump_hplc.svg"
        elif self.pump_type == "sf10":
            return "pump_sf10.svg"
        else:
            return "pump.svg"

    def _update_svg(self):
        """상태에 따라 SVG 렌더러 업데이트"""
        color = C_RUNNING if self.running else C_TEXT
        if color != self._last_color:
            self._svg_renderer = load_svg_with_color(self._get_svg_filename(), color)
            self._last_color = color

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)

        # 가동 시 글로우 프레임 (gray-first: 본체 채움 대신 외곽 강조)
        if self.running:
            glow = QColor(C_RUNNING)
            glow.setAlpha(45)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(glow, 6))
            painter.drawRoundedRect(QRectF(-42, -26, 84, 52), 10, 10)

        # === SVG 렌더링 ===
        self._update_svg()
        if self._svg_renderer and self._svg_renderer.isValid():
            svg_rect = QRectF(-38, -24, 76, 48)
            self._svg_renderer.render(painter, svg_rect)
        else:
            painter.setBrush(C_ELEMENT_BG)
            painter.setPen(QPen(QColor(C_RUNNING) if self.running else C_BORDER,
                                2 if self.running else 1.5))
            painter.drawRoundedRect(QRectF(-38, -22, 76, 44), 8, 8)

        # run-LED (본체 우상단 내부 고정) — 완만한 펄스, 알람 아님
        led = QColor(C_RUNNING) if self.running else QColor(C_TEXT)
        if self.running:
            led.setAlpha(140 + int(100 * abs((self._anim_phase % 20 - 10) / 10.0)))
        else:
            led.setAlpha(70)
        painter.setBrush(led)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(26, -18, 7, 7))

        # L1 그룹명 (언더스코어 정리) / L3 태그 / L4 유량
        _draw_lane(painter, LANE_NAME, self.label.replace("_", " "),
                   FONT_NODE, C_TEXT, cx=self.name_dx, half_w=70)
        _draw_lane(painter, LANE_TAG, getattr(self, "tag", ""),
                   FONT_SUB, _tag_color(), half_w=62)
        if self.running and self.flow_rate > 0:
            _draw_lane(painter, LANE_VALUE, f"{self.flow_rate:.2f} mL/min",
                       FONT_VALUE, C_TEXT, half_w=62)

    def set_running(self, running, flow_rate=0.0):
        """가동 상태 설정"""
        self.running = running
        self.flow_rate = flow_rate
        self._last_color = None  # SVG 색상 강제 업데이트
        self.update()

    def advance_anim(self):
        """애니메이션 진행"""
        if self.running:
            self._anim_phase = (self._anim_phase + 1) % 20
            self.update()


class VisualReactor(QGraphicsItem):
    """반응기 — v2 장비 카드: 온도/부피를 심볼 '안'에 내장 (HMI 관례).

    - 카드 96×64, 좌측 코일 글리프 + 우측 온도 리드아웃 + 하단 진행바
    - 부피 "V=x.xx mL" 푸터 = 더블클릭 편집 (부유 칩 삭제 — 장비 값은 카드 내)
    - 유일하게 L1~L2 두 레인을 쓰는 대형 심볼 (스펙상 허용 예외)
    """
    def __init__(self, label="Reactor", on_edit=None):
        super().__init__()
        self.label = label
        self.tag = "R-01"
        self.active = False
        self.temp = 0.0
        self.vol_text = ""       # "V=1.98 mL" (draw_layout/_refresh 가 갱신)
        self.progress = -1.0     # -1 = 미표시, 0~100 = 진행률
        self.phase_name = ""
        self._on_edit = on_edit
        self._svg_renderer = None
        self._last_color = None
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("더블클릭: 반응기 부피(mL) 수정")

    def boundingRect(self):
        return QRectF(-64, -60, 128, 140)

    def mouseDoubleClickEvent(self, e):
        if callable(self._on_edit):
            self._on_edit("reactor")
        e.accept()

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        card = QRectF(-52, -32, 104, 64)

        # 가열 글로우 (앰버) — gray-first: 채움 대신 외곽
        if self.active:
            glow = QColor(C_WARNING)
            glow.setAlpha(50)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(glow, 6))
            painter.drawRoundedRect(card.adjusted(-2, -2, 2, 2), 10, 10)

        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(QPen(QColor(C_WARNING) if self.active else C_BORDER,
                            2 if self.active else 1.5))
        painter.drawRoundedRect(card, 8, 8)

        # 좌측 코일 글리프 — 겹친 루프 단면 (지그재그는 'W' 글자처럼 읽혔음)
        coil = QColor(C_WARNING) if self.active else QColor(C_TEXT)
        coil.setAlpha(210 if self.active else 150)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(coil, 2.2))
        for k in range(4):
            painter.drawEllipse(QRectF(-45 + k * 8.5, -14, 13, 28))
        # 코일/리드아웃 존 구분선
        painter.setPen(QPen(QColor(C_BORDER), 1))
        painter.drawLine(QPointF(-5, -20), QPointF(-5, 20))

        # 우측 리드아웃 블록 (세로 중앙 정렬: 온도 위 / 부피 아래)
        if self.temp > 0:
            painter.setPen(QColor(C_WARNING) if self.active else C_TEXT)
            t_txt = f"{self.temp:.1f}°C"
        else:
            painter.setPen(_tag_color())
            t_txt = "--.- °C"
        painter.setFont(_font_px('Consolas', 15, bold=True))
        _draw_text(painter, QRectF(0, -19, 46, 20), t_txt)
        if self.vol_text:
            painter.setPen(_tag_color())
            painter.setFont(FONT_CHIP)
            _draw_text(painter, QRectF(0, 3, 46, 15), self.vol_text)

        # 하단 진행바 (4px, 카드 안쪽)
        if self.progress >= 0:
            bar = QRectF(-48, 22, 96, 4)
            painter.setPen(Qt.NoPen)
            base = QColor(C_BORDER)
            painter.setBrush(base)
            painter.drawRoundedRect(bar, 2, 2)
            painter.setBrush(QColor(Dark.ACCENT_CYAN_ALT))
            fill = QRectF(bar)
            fill.setWidth(bar.width() * max(0.0, min(100.0, self.progress)) / 100.0)
            painter.drawRoundedRect(fill, 2, 2)

        # L1 이름 / L3 태그 / L4 단계 진행 텍스트
        _draw_lane(painter, LANE_NAME, self.label, FONT_NODE, C_TEXT, half_w=55)
        _draw_lane(painter, LANE_TAG, self.tag, FONT_SUB, _tag_color(), half_w=40)
        if self.progress >= 0:
            _draw_lane(painter, LANE_VALUE,
                       f"{self.phase_name} {self.progress:.0f}%",
                       FONT_VALUE, QColor(Dark.ACCENT_CYAN_ALT), half_w=60)


class VisualOutlet(QGraphicsItem):
    """출구 (Waste/Collect) - SVG 기반"""
    def __init__(self, type_name="waste"):
        super().__init__()
        self.type = type_name  # "waste" or "collect"
        self.active = False
        self._svg_renderer = None
        self._last_color = None
        self.setZValue(10)

    def boundingRect(self):
        # 텍스트 확장(_draw_text)분까지 커버
        return QRectF(-85, -60, 170, 140)

    def _update_svg(self):
        """상태에 따라 SVG 렌더러 업데이트"""
        if self.type == "waste":
            color = C_WASTE if self.active else C_TEXT
            svg_file = "waste.svg"
        else:
            color = C_MIX if self.active else C_TEXT
            svg_file = "collector.svg"

        if color != self._last_color:
            self._svg_renderer = load_svg_with_color(svg_file, color)
            self._last_color = color

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        self._update_svg()

        # 통일된 폰트 설정
        font = FONT_NODE
        painter.setFont(font)

        if self.type == "waste":
            if self._svg_renderer and self._svg_renderer.isValid():
                svg_rect = QRectF(-20, -25, 40, 50)
                self._svg_renderer.render(painter, svg_rect)
            else:
                bg = C_WASTE if self.active else C_ELEMENT_BG
                painter.setBrush(bg)
                painter.setPen(QPen(C_BORDER, 2))
                painter.drawRoundedRect(-20, -20, 40, 40, 6, 6)

            # 태그 (하단 — L3 관례)
            painter.setPen(_tag_color())
            painter.setFont(FONT_SUB)
            _draw_text(painter, QRectF(-30, 32, 60, 14), "Waste")

        else:  # collect
            if self._svg_renderer and self._svg_renderer.isValid():
                svg_rect = QRectF(-25, -30, 50, 60)
                self._svg_renderer.render(painter, svg_rect)
            else:
                bg = C_MIX if self.active else C_ELEMENT_BG
                painter.setBrush(bg)
                painter.setPen(QPen(C_BORDER, 2))
                path = QPainterPath()
                path.moveTo(-15, -20)
                path.lineTo(-20, 20)
                path.lineTo(20, 20)
                path.lineTo(15, -20)
                path.closeSubpath()
                painter.drawPath(path)

            # 라벨 (하단)
            painter.setPen(C_TEXT if not self.active else Qt.white)
            _draw_text(painter, QRectF(-50, 38, 100, 22), "COLLECT")



class VisualOutletValve(QGraphicsItem):
    """출구 3-way 밸브 노드 — 실제 분기점에 밸브가 있음을 표시

    @codesyncer-decision: 기존 배관도는 분기점에 밸브 노드가 없어
    파이프가 그냥 갈라지는 것으로 보였음 → 실제 위상(Outlet 밸브) 반영
    """

    def __init__(self):
        super().__init__()
        self.is_collect = False
        self.active = False
        self.setZValue(10)

    def boundingRect(self):
        # "Outlet 3-Way" 확장 텍스트 커버
        return QRectF(-92, -56, 184, 112)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        # gray-first: 본체 채움 금지, 활성=외곽선 강조
        painter.setBrush(C_ELEMENT_BG)
        painter.setPen(QPen(QColor(C_RUNNING) if self.active else C_BORDER,
                            2 if self.active else 1.5))
        painter.drawEllipse(QRectF(-16, -16, 32, 32))
        # 내부 방향 로터 (L자 = 실제 경로)
        rotor = QColor(C_RUNNING) if self.active else QColor(C_TEXT)
        painter.setPen(QPen(rotor, 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(-9, 0, 0, 0)
        if self.is_collect:
            painter.drawLine(0, 0, 0, 11)   # 아래 = Collect
        else:
            painter.drawLine(0, 0, 9, 0)    # 우측 = Waste
        # L3 태그 (짧게 — 우측 Waste 태그와 열 간격 110px)
        _draw_lane(painter, LANE_TAG, "XV-01", FONT_SUB, _tag_color(), half_w=30)


class VisualCollector(QGraphicsItem):
    """분취기 노드 — 종류(Plate96/Colosseum/기타)에 따라 다른 형태 + 현재 위치

    @codesyncer-decision: 기존엔 고정 'collect' 원 아이콘이라 분취기 종류와
    현재 튜브 위치가 보이지 않았음 → 실제 장비 구성을 반영
    """

    def __init__(self, kind="plate96", label="Collector"):
        super().__init__()
        self.kind = kind          # "plate96" | "colosseum" | "generic" | "none"
        self.label = label
        self.active = False
        self.pos_text = ""
        self.setZValue(10)

    def boundingRect(self):
        # 라벨/위치 텍스트 확장분 커버
        return QRectF(-92, -54, 184, 116)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        border = QPen(C_RUNNING if self.active else C_BORDER, 2)
        if self.kind == "plate96":
            painter.setBrush(C_ELEMENT_BG)
            painter.setPen(border)
            painter.drawRoundedRect(QRectF(-44, -24, 88, 56), 6, 6)
            # 미니 웰 그리드 6x4
            painter.setPen(Qt.NoPen)
            dot = QColor(C_RUNNING) if self.active else QColor(C_TEXT)
            dot.setAlpha(170)
            painter.setBrush(dot)
            for r in range(4):
                for c in range(6):
                    painter.drawEllipse(QRectF(-36 + c * 13, -16 + r * 11, 5, 5))
        elif self.kind == "colosseum":
            painter.setBrush(C_ELEMENT_BG)
            painter.setPen(border)
            painter.drawEllipse(QRectF(-30, -28, 60, 60))
            dot = QColor(C_RUNNING) if self.active else QColor(C_TEXT)
            dot.setAlpha(170)
            painter.setBrush(dot)
            painter.setPen(Qt.NoPen)
            import math as _m
            for k in range(10):
                a = k / 10.0 * 2 * _m.pi
                painter.drawEllipse(QRectF(-3 + 20 * _m.cos(a), -1 + 20 * _m.sin(a), 5, 5))
        else:
            painter.setBrush(C_ELEMENT_BG)
            painter.setPen(border)
            painter.drawRoundedRect(QRectF(-30, -22, 60, 50), 8, 8)
        # 태그 = 카드 아래 (L3 관례), 현재 위치 = 카드 '안' 상단 (장비 값은 심볼 내)
        painter.setFont(FONT_SUB)
        painter.setPen(_tag_color())
        _draw_text(painter, QRectF(-58, 38, 116, 14), getattr(self, "tag", self.label))
        if self.pos_text:
            painter.setFont(FONT_VALUE)
            painter.setPen(QColor(C_RUNNING) if self.active else C_TEXT)
            _draw_text(painter, QRectF(-58, -44, 116, 16), self.pos_text)



class VisualTee(QGraphicsItem):
    """T-junction 노드 — 두 스트림의 순차 합류점"""

    def __init__(self, label=""):
        super().__init__()
        self.label = label
        self.active = False
        self.setZValue(12)

    def boundingRect(self):
        return QRectF(-26, -22, 52, 40)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        # HMI 정션 = 채운 도트 — 굵은 배관(8-10px)에 맞춘 Ø14
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C_RUNNING) if self.active else QColor(C_BORDER))
        painter.drawEllipse(QRectF(-7, -7, 14, 14))
        painter.setPen(_tag_color())
        painter.setFont(FONT_SUB)
        # 파이프가 없는 남동 사분면에 태그
        painter.drawText(QRectF(8, 8, 40, 14), Qt.AlignLeft | Qt.AlignVCenter, self.label)


class VolumeChip(QGraphicsItem):
    """배관 데드볼륨 칩 — 더블클릭으로 수치 편집

    @codesyncer-decision: 엔진 타이밍의 핵심 입력(reactor/post/collection 부피)을
    배관도 위에서 직접 보고 편집 → 분취 정합의 데드볼륨 모델이 실배관과 일치하게 유지
    """

    def __init__(self, key, text, on_edit, compact=False):
        super().__init__()
        self.key = key
        self.text = text
        self._on_edit = on_edit
        self.compact = compact
        self.setZValue(20)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("더블클릭하여 부피(mL) 수정")
        self._hover = False

    def _chip_rect(self):
        """텍스트 실측폭 기반 칩 rect — 고정폭에서 앞자리가 잘리던 결함 수정."""
        fm = QFontMetricsF(FONT_CHIP)
        min_w = 56.0 if self.compact else 88.0
        w = max(min_w, fm.horizontalAdvance(self.text) + 18)
        h = max(18.0 if self.compact else 20.0, fm.height() + 4)
        return QRectF(-w / 2, -h / 2, w, h)

    def boundingRect(self):
        return self._chip_rect().adjusted(-2, -2, 2, 2)

    def set_text(self, text):
        if text != self.text:
            self.prepareGeometryChange()  # rect 가 텍스트에 따라 변하므로 필수
            self.text = text
        self.update()

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        if callable(self._on_edit):
            self._on_edit(self.key)
        e.accept()

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        # 완전 불투명 배경(C_BG) — 라인 '위' 인라인 배치 시 파이프를 시각적으로
        # 끊어 HMI 라인 라벨처럼 읽히게 함 (반투명이면 글자가 라인에 얹혀 보임)
        bg = QColor(C_BG)
        bg.setAlpha(255)
        painter.setBrush(bg)
        painter.setPen(QPen(C_RUNNING if self._hover else C_BORDER, 1))
        rect = self._chip_rect()
        r = rect.height() / 2
        painter.drawRoundedRect(rect, r, r)
        painter.setPen(C_TEXT)
        painter.setFont(FONT_CHIP)
        _draw_text(painter, rect, self.text)


class FlowDiagramWidget(QGraphicsView):
    """
    동적 공정도 위젯

    @codesyncer-decision: 하드웨어 설정에 따른 동적 레이아웃 (3-way 밸브 제거)
    - Selector+Pump: 12-way 셀렉터 + 펌프
    - Pump only: 펌프만
    """

    def __init__(self, pump_configs=None, active_pumps=None, inventory=None):
        """
        Args:
            pump_configs: 펌프 설정 리스트 (hardware_config.json의 roles.pumps)
            active_pumps: 활성 펌프 이름 리스트 (기존 호환성)
            inventory: 장비 인벤토리 리스트 (hardware_config.json의 inventory)
        """
        super().__init__()

        self.pump_configs = pump_configs or []
        self.active_pumps = active_pumps or []
        self.inventory = {item["id"]: item for item in (inventory or [])}
        self._cfg = None
        self._app = None
        self.collector_kind = "generic"
        self.has_push = False
        self.sys_params = {}

        # 씬 설정
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(C_BG)

        # 요소 저장소
        self.items = {
            "pipes": {},
            "selectors": {},    # 12-way 밸브
            "switchers": {},    # 3-way 밸브
            "pumps": {},
            "reactor": None,
            "outlets": {}
        }

        # 애니메이션 타이머
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(50)  # 20fps

        self.draw_layout()

    def _detect_pump_type(self, motor_id):
        """
        모터 드라이버 ID로 펌프 타입 감지

        @codesyncer-decision: 드라이버 이름 기반 펌프 타입 매핑
        - "시린지 펌프", "Chemyx" → "syringe"
        - "연동 펌프", "Vapourtec" → "hplc"
        - "Reaxus", "SF-10" → "sf10"

        Args:
            motor_id: 모터 드라이버 ID (예: "dev_chemyx_2")

        Returns:
            str: 펌프 타입 ("syringe", "hplc", "sf10")
        """
        if not motor_id or motor_id not in self.inventory:
            return "syringe"  # 기본값

        device = self.inventory[motor_id]
        driver_name = device.get("driver", "").lower()

        # 드라이버 이름으로 펌프 타입 결정
        if "vapourtec" in driver_name or "연동" in driver_name or "hplc" in driver_name:
            return "hplc"
        elif "reaxus" in driver_name or "sf-10" in driver_name or "sf10" in driver_name:
            return "sf10"
        else:
            return "syringe"  # Chemyx 및 기타

    def configure(self, cfg, app=None):
        """SystemConfig 전체를 받아 배관도를 실제 시스템 구성과 동기화한다.

        @codesyncer-decision: 배관도의 단일 진실원(SSOT) = hardware_config
        - 기존: pumps만 받아 push pump/collector 종류가 반영 안 됨
        - configure는 roles 전체(collector/push_pump)와 system_params(데드볼륨)를
          읽어 노드를 구성하고, app 핸들로 편집값을 엔진에 즉시 반영
        """
        self._cfg = cfg
        self._app = app
        cd = cfg.config_data
        self.pump_configs = cd.get("roles", {}).get("pumps", [])
        self.inventory = {item["id"]: item for item in cd.get("inventory", [])}
        roles = cd.get("roles", {})
        inv = self.inventory

        # collector 종류 감지
        col_id = (roles.get("collector") or {}).get("driver_id")
        col_drv = (inv.get(col_id) or {}).get("driver", "") if col_id else ""
        if not col_id:
            self.collector_kind = "none"
        elif "plate" in col_drv.lower() or "96" in col_drv:
            self.collector_kind = "plate96"
        elif "colosseum" in col_drv.lower() or "콜로세움" in col_drv or "분획" in col_drv:
            self.collector_kind = "colosseum"
        else:
            self.collector_kind = "generic"

        # push pump 존재 여부
        pp_id = (roles.get("push_pump") or {}).get("driver_id")
        self.has_push = bool(pp_id)
        self.push_label = (inv.get(pp_id) or {}).get("name", "Push") if pp_id else ""

        self.sys_params = cd.get("system_params", {})
        self.draw_layout()

    # ── 데드볼륨 편집 ──────────────────────────────────────────
    VOL_KEYS = {
        "reactor": ("Reactor 부피", "reactor_vol"),
        "post": ("Reactor→Outlet 라인 (post)", "post_reactor_vol_ml"),
        "collection": ("Outlet→Collector 라인", "collection_line_vol_ml"),
        "mixing": ("합류→Reactor 믹싱 라인", "mixing_line"),
    }
    SEG_TITLES = {
        "inlet": "시약 → 12-way 라인",
        "valve_pump": "12-way → 3-way → 시린지 라인",
        "pump_merge": "시린지 → 합류점 라인",
    }

    def _current_vol(self, key):
        if self._cfg is None:
            return 0.0
        if key == "reactor":
            return float(getattr(self._cfg, "reactor_vol", 0.0))
        if key == "mixing":
            return float(getattr(self._cfg, "mixing_line_dead_vol", 0.0) or 0.0)
        if key.startswith("ch|"):
            _, pname, seg = key.split("|", 2)
            d = getattr(self._cfg, f"line_vol_{seg}", {}) or {}
            try:
                return float(d.get(pname, 0.0) or 0.0)
            except Exception:
                return 0.0
        if key.startswith("tj|"):
            j = int(key.split("|", 1)[1])
            d = getattr(self._cfg, "tjunction_line_vols", {}) or {}
            try:
                return float(d.get(j, 0.0) or 0.0)
            except Exception:
                return 0.0
        sp = self._cfg.config_data.get("system_params", {})
        name = self.VOL_KEYS[key][1]
        try:
            return float(sp.get(name, 0.0) or 0.0)
        except Exception:
            return 0.0

    def _on_volume_edit(self, key):
        """VolumeChip 더블클릭 → 수치 입력 → config 저장 + 엔진 즉시 반영"""
        if self._cfg is None:
            return
        from PyQt5.QtWidgets import QInputDialog
        if key.startswith("ch|"):
            _, pname, seg = key.split("|", 2)
            title = f"{pname}: {self.SEG_TITLES.get(seg, seg)}"
        elif key.startswith("tj|"):
            j = int(key.split("|", 1)[1])
            title = f"T-junction 구간 T{j} → T{j + 1}"
        elif key in self.VOL_KEYS:
            title = self.VOL_KEYS[key][0]
        else:
            return
        cur = self._current_vol(key)
        val, ok = QInputDialog.getDouble(
            self, "데드볼륨 입력", f"{title} (mL):", cur, 0.001, 500.0, 3)
        if not ok:
            return
        cfg = self._cfg
        sp = cfg.config_data.setdefault("system_params", {})
        if key.startswith("ch|"):
            # 채널 구간 → roles.pumps[].settings에 저장 (process_config가 재로딩)
            _, pname, seg = key.split("|", 2)
            for pc in cfg.config_data.get("roles", {}).get("pumps", []):
                if pc.get("name") == pname:
                    pc.setdefault("settings", {})[f"tube_vol_{seg}"] = val
                    break
        elif key.startswith("tj|"):
            j = key.split("|", 1)[1]
            sp.setdefault("tjunction_line_vols", {})[str(j)] = val
        elif key == "mixing":
            # mixing_line_dead_vol = pi*r^2*len → 부피 입력 시 길이 역산 (내경 유지)
            import math as _m
            mid = float(sp.get("mixing_line_id_mm", 1.5) or 1.5)
            r_cm = (mid / 10.0) / 2.0
            sp["mixing_line_len_cm"] = round(val / (_m.pi * r_cm * r_cm), 3)
        elif key == "reactor":
            # reactor_vol = pi*(id/20)^2*(len_m*100) → 부피 입력 시 길이 역산
            # (내경은 유지: 실배관 교체 없이 코일 길이만 달라진 것으로 모델링)
            import math as _m
            id_mm = float(sp.get("reactor_id_mm", 1.0) or 1.0)
            area = _m.pi * ((id_mm / 20.0) ** 2)
            sp["reactor_len_m"] = round(val / (area * 100.0), 4)
            cfg.reactor_vol = val
        else:
            sp[self.VOL_KEYS[key][1]] = val
        # 저장 (save_config가 파생값 재계산 포함)
        try:
            cfg.save_config(cfg.config_data.get("inventory", []),
                            cfg.config_data.get("roles", {}), sp)
        except Exception as e:
            print(f"[Diagram] config 저장 실패: {e}")
        # 살아있는 엔진에 즉시 반영 (다음 run부터 새 데드볼륨으로 타이밍 계산)
        eng = getattr(self._app, "engine", None) if self._app else None
        if eng is not None:
            try:
                if key == "reactor":
                    eng.vol_reactor = float(cfg.reactor_vol)
                elif key == "post":
                    eng.vol_post_common = val
                elif key == "collection":
                    eng.vol_collection = val
            except Exception:
                pass
        sig = getattr(self._app, "signals", None) if self._app else None
        if sig is not None:
            try:
                sig.sig_log.emit(f">>> 배관도 데드볼륨 갱신: {title} = {val:.3f} mL")
            except Exception:
                pass
        self._refresh_vol_chips()

    def _refresh_vol_chips(self):
        for key, chip in self.items.get("vol_chips", {}).items():
            # \u270e \uc774\ubaa8\uc9c0 \uc81c\uac70: Consolas \uc5d0 \uc5c6\ub294 \uae00\ub9ac\ud504\ub77c \ud3f4\ubc31 \ub80c\ub354 \ud3ed\uc774 \uce21\uc815\uce58\ub97c
            # \ucd08\uacfc\ud574 \uce69\uc774 \uc798\ub838\uc74c. \ud3b8\uc9d1 \uc5b4\ud3ec\ub358\uc2a4\ub294 hover \ubcf4\ub354+\ucee4\uc11c+\ud234\ud301\uc774 \ub2f4\ub2f9.
            chip.set_text(f"{self._current_vol(key):.2f} mL")
        # \ub9ac\uc561\ud130 \ubd80\ud53c\ub294 \uce74\ub4dc \ub0b4 \ud478\ud130 (\uc7a5\ube44 \uac12 \uaddc\uce59 \u2014 \ubd80\uc720 \uce69 \ub300\uccb4)
        reactor = self.items.get("reactor")
        if reactor is not None and self._cfg is not None:
            reactor.vol_text = f"V={self._current_vol('reactor'):.2f} mL"
            reactor.update()

    def draw_layout(self):
        """하드웨어 설정 기반 동적 레이아웃 — 실제 액체 경로 순서로 배치"""
        self.scene.clear()
        self.items = {
            "pipes": {}, "selectors": {}, "switchers": {},
            "pumps": {}, "reactor": None, "outlets": {},
            "outlet_valve": None, "collector": None, "push": None,
            "tees": {}, "vol_chips": {}, "sources": {},
        }

        active_groups = []
        if self.pump_configs:
            for cfg in self.pump_configs:
                drivers = cfg.get("drivers", {})
                pump_name = cfg.get("name", "Unknown")
                if drivers.get("motor"):
                    pump_type = self._detect_pump_type(drivers.get("motor"))
                    # @codesyncer-decision: 매핑 버그 수정 — config 그대로 사용
                    # (selector=12방향 Runze, switcher=3방향)
                    # 라우팅 모드 유도 — config.PUMP_ROUTING 과 동일 규칙 (드라이버 타입 기반)
                    _motor_item = self.inventory.get(drivers.get("motor"), {}) if self.inventory else {}
                    _mtype = HardwareFactory.get_driver_type(_motor_item.get("driver", ""))
                    if _mtype == "NRGSyringePump":
                        _routing = "autosampler" if drivers.get("sampler") else "internal_valve"
                    else:
                        _routing = "external_valve"
                    active_groups.append({
                        "name": pump_name,
                        "has_selector": drivers.get("selector") is not None,
                        "has_switcher": drivers.get("switcher") is not None,
                        "pump_type": pump_type,
                        "routing": _routing,
                    })
        elif self.active_pumps:
            for name in self.active_pumps:
                active_groups.append({"name": name, "has_selector": True,
                                      "has_switcher": False, "pump_type": "syringe",
                                      "routing": "external_valve"})
        if not active_groups:
            self.scene.setSceneRect(0, 0, 400, 200)
            return

        # ── 좌표 체계 v2 (액체 경로 순) — ROW_PITCH(150) 레인 계약 기반 ──
        # 열 간격을 넓혀 세그먼트마다 인라인 칩이 들어갈 폭을 확보
        x_source = 44
        x_selector = 180
        x_3way = 284       # 소스/반응기 경로 전환 3-way
        x_pump = 420       # 3way↔펌프 세그먼트에 인라인 칩(≈64px)이 들어가는 폭
        x_merge = 560
        x_reactor = 692
        x_valve = 830
        x_waste = 940
        scene_width = 1014

        num_groups = len(active_groups)
        y_positions = [ROW_TOP + i * ROW_PITCH for i in range(num_groups)]
        y_mid = sum(y_positions) / num_groups

        # 1) 펌프 채널들 — 태그 체계: 채널 문자 A/B/C…, 펌프 번호 01..
        _pump_type_names = {"syringe": "Syringe", "hplc": "HPLC", "sf10": "SF-10"}
        for i, group in enumerate(active_groups):
            y = y_positions[i]
            name = group["name"]
            ch = chr(ord('A') + i)

            # 소스 노드 — autosampler 라우팅이면 니들+랙, 아니면 단일 바이알
            if group.get("routing") == "autosampler":
                src = VisualSampler()
                src.tag = f"AS-{ch}1"
            else:
                src = VisualSource(number=1)
            src.setPos(x_source, y)
            self.scene.addItem(src)
            self.items.setdefault("sources", {})[name] = src

            if group["has_selector"]:
                selector = Visual12WayValve("12-Way")
                selector.tag = f"SV-{ch}1"
                selector.setPos(x_selector, y)
                self.scene.addItem(selector)
                self.items["selectors"][name] = selector
                self.add_pipe(f"{name}_sel_in",
                              QPointF(x_source + 18, y), QPointF(x_selector - 28, y))
                if group["has_switcher"]:
                    # 실제 위상 반영 — 12way와 시린지 사이 경로 전환 3-way
                    sw = Visual3WayValve("3-Way")
                    sw.tag = f"DV-{ch}1"
                    sw.setPos(x_3way, y)
                    self.scene.addItem(sw)
                    self.items["switchers"][name] = sw
                    self.add_pipe(f"{name}_sel_sw",
                                  QPointF(x_selector + 28, y), QPointF(x_3way - 26, y))
                    self.add_pipe(f"{name}_sw_pump",
                                  QPointF(x_3way + 26, y), QPointF(x_pump - 40, y))
                else:
                    self.add_pipe(f"{name}_sel_pump",
                                  QPointF(x_selector + 28, y), QPointF(x_pump - 40, y))
            else:
                # 셀렉터 없는 그룹 — 1-소스 라우팅(NRG)이면 내장 2-way 밸브 노드 표시
                # 소스 심볼 폭: 샘플러 랙(±26) > 바이알(±15) → 파이프 시작점 보정
                x_src_out = x_source + (28 if group.get("routing") == "autosampler" else 18)
                if group.get("routing") in ("internal_valve", "autosampler"):
                    v2 = Visual3WayValve("2-Way")
                    v2.tag = f"IV-{ch}1"
                    v2.setPos(x_3way, y)
                    self.scene.addItem(v2)
                    self.items["switchers"][name] = v2
                    self.add_pipe(f"{name}_in",
                                  QPointF(x_src_out, y), QPointF(x_3way - 26, y))
                    self.add_pipe(f"{name}_sw_pump",
                                  QPointF(x_3way + 26, y), QPointF(x_pump - 40, y))
                else:
                    self.add_pipe(f"{name}_in",
                                  QPointF(x_src_out, y), QPointF(x_pump - 40, y))

            pump = VisualPump(name, pump_type=group.get("pump_type", "syringe"))
            pump.tag = (f"P-{i + 1:02d} · "
                        f"{_pump_type_names.get(group.get('pump_type'), 'Pump')}")
            pump.setPos(x_pump, y)
            self.scene.addItem(pump)
            self.items["pumps"][name] = pump
            self.add_pipe(f"{name}_out", QPointF(x_pump + 40, y), QPointF(x_merge, y))

            # 채널 구간 데드볼륨 칩 — 파이프 '위' 인라인 (HMI 라인 라벨).
            # 불투명 칩이 라인을 끊으므로 어떤 텍스트 레인과도 충돌 불가.
            if self._cfg is not None and group["has_selector"]:
                if group["has_switcher"]:
                    cx_vp = (x_3way + 26 + x_pump - 40) / 2
                else:
                    cx_vp = (x_selector + 28 + x_pump - 40) / 2
                for key_seg, cx in (
                        ("inlet", (x_source + 18 + x_selector - 28) / 2),
                        ("valve_pump", cx_vp),
                        ("pump_merge", (x_pump + 40 + x_merge) / 2)):
                    chip = VolumeChip(f"ch|{name}|{key_seg}", "",
                                      self._on_volume_edit, compact=True)
                    chip.setPos(cx, y)
                    self.scene.addItem(chip)
                    self.items["vol_chips"][f"ch|{name}|{key_seg}"] = chip

        # 2) T-junction 캐스케이드 합류
        # @codesyncer-decision: 실제 위상 반영 — 단일 합류점이 아니라
        # T1(P1+P2) → [구간 S1] → T2(+P3) → … 순차 합류.
        # 정션 간 구간(S_j)마다 데드볼륨 칩(tj|j) 편집 가능.
        if num_groups > 1:
            y_j1 = (y_positions[0] + y_positions[1]) / 2.0
            # P1/P2 → T1
            self.add_pipe("merge_v_0", QPointF(x_merge, y_positions[0]), QPointF(x_merge, y_j1))
            self.add_pipe("merge_v_1", QPointF(x_merge, y_positions[1]), QPointF(x_merge, y_j1))
            tee = VisualTee("T-01")
            tee.setPos(x_merge, y_j1)
            self.scene.addItem(tee)
            self.items["tees"][1] = tee
            y_prev = y_j1
            # P3.. 순차 합류: 구간 S_{j} = T_j → T_{j+1}
            for idx in range(2, num_groups):
                y_jn = y_positions[idx]
                self.add_pipe(f"merge_tj_{idx - 1}", QPointF(x_merge, y_prev), QPointF(x_merge, y_jn))
                if self._cfg is not None:
                    chip = VolumeChip(f"tj|{idx - 1}", "", self._on_volume_edit, compact=True)
                    # 세로 매니폴드 칩 = 라인 위 인라인 (불투명 칩이 라인을 덮는
                    # HMI 인라인 태그 관례) — 옆에 띄우면 T 라벨과 경쟁
                    chip.setPos(x_merge, (y_prev + y_jn) / 2.0)
                    self.scene.addItem(chip)
                    self.items["vol_chips"][f"tj|{idx - 1}"] = chip
                tee = VisualTee(f"T-{idx:02d}")
                tee.setPos(x_merge, y_jn)
                self.scene.addItem(tee)
                self.items["tees"][idx] = tee
                y_prev = y_jn
            y_mid = y_prev  # 마지막 정션 출구가 메인 라인의 y

        # 3) Push pump (HPLC 용매 push — 합류점 아래에서 진입)
        if getattr(self, "has_push", False):
            y_push = y_positions[-1] + int(ROW_PITCH * 0.8)
            push = VisualPump("Push (용매)", pump_type="hplc")
            push.tag = "P-91 · HPLC"
            push.name_dx = 96   # 세로 라이저가 이름을 관통하지 않도록 우측 배치
            push.setPos(x_merge, y_push)
            self.scene.addItem(push)
            self.items["push"] = push
            self.add_pipe("push_up", QPointF(x_merge, y_push - 28), QPointF(x_merge, y_mid))
        else:
            y_push = y_positions[-1]

        # 4) 합류 → 반응기 → 출구밸브 (주 공정 경로 = W_MAIN 위계)
        self.add_pipe("main_to_reactor",
                      QPointF(x_merge, y_mid), QPointF(x_reactor - 48, y_mid),
                      weight=W_MAIN)
        reactor = VisualReactor("Reactor", on_edit=self._on_volume_edit)
        reactor.setPos(x_reactor, y_mid)
        self.scene.addItem(reactor)
        self.items["reactor"] = reactor
        self.add_pipe("reactor_out",
                      QPointF(x_reactor + 48, y_mid), QPointF(x_valve - 18, y_mid),
                      weight=W_MAIN)

        ov = VisualOutletValve()
        ov.setPos(x_valve, y_mid)
        self.scene.addItem(ov)
        self.items["outlet_valve"] = ov

        # 5) Waste(우) / Collector(아래)
        waste = VisualOutlet("waste")
        waste.setPos(x_waste, y_mid)
        self.scene.addItem(waste)
        self.items["outlets"]["waste"] = waste
        self.add_pipe("to_waste",
                      QPointF(x_valve + 18, y_mid), QPointF(x_waste - 24, y_mid),
                      weight=W_MAIN)

        kind = getattr(self, "collector_kind", "generic")
        if kind != "none":
            label = {"plate96": "Plate96", "colosseum": "Colosseum"}.get(kind, "Collector")
            col = VisualCollector(kind, label)
            col.tag = f"FC-01 · {label}"
            col.setPos(x_valve, y_mid + ROW_PITCH)
            self.scene.addItem(col)
            self.items["collector"] = col
            self.add_pipe("to_collect",
                          QPointF(x_valve, y_mid + 18),
                          QPointF(x_valve, y_mid + ROW_PITCH - 34),
                          weight=W_MAIN)

        # 6) 데드볼륨 칩 — 전부 파이프 위 인라인 (세그먼트 값 규칙).
        #    reactor 부피는 장비 값 → 카드 내 푸터로 이동 (부유 칩 삭제)
        if self._cfg is not None:
            chips = [
                ("mixing", QPointF((x_merge + x_reactor - 48) / 2, y_mid)),
                ("post", QPointF((x_reactor + 48 + x_valve - 18) / 2, y_mid)),
            ]
            if kind != "none":
                chips.append(
                    ("collection", QPointF(x_valve, y_mid + ROW_PITCH / 2 - 8)))
            for key, pos in chips:
                chip = VolumeChip(key, "", self._on_volume_edit, compact=True)
                chip.setPos(pos)
                self.scene.addItem(chip)
                self.items["vol_chips"][key] = chip
            self._refresh_vol_chips()

        # 씬 크기 (v2: 콜렉터 y_mid+ROW_PITCH 카드 + 레인 엔벨로프 반영)
        bottom_candidates = [y_positions[-1] + 100]
        if getattr(self, "has_push", False):
            bottom_candidates.append(y_push + 100)
        if kind != "none":
            bottom_candidates.append(y_mid + ROW_PITCH + 70)
        scene_height = max(300, *bottom_candidates)
        # 아이템 실측 경계 ∪ 명목 좌표계 — 확장된 라벨/칩이 fit 범위 밖으로
        # 밀려 잘리지 않도록 (구: 명목 (0,0,w,h) 고정)
        nominal = QRectF(0, 0, scene_width, scene_height)
        self.scene.setSceneRect(
            nominal.united(self.scene.itemsBoundingRect()).adjusted(-12, -10, 12, 10))
        self._fit_view()

    def _fit_view(self):
        """@codesyncer-decision: 배관도 비율 자동 맞춤
        - 기존: 1:1 고정 렌더 → 큰 화면에서 도면이 좌상단에 작게 표시되고
          여백이 낭비됨 (가시성 저하)
        - 위젯 크기에 맞춰 KeepAspectRatio로 스케일. 상한 1.35배(텍스트 블러 방지)"""
        rect = self.scene.sceneRect()
        if rect.width() <= 0:
            return
        self.fitInView(rect.adjusted(-8, -8, 8, 8), Qt.KeepAspectRatio)
        t = self.transform()
        if t.m11() > 1.35:
            self.resetTransform()
            self.scale(1.35, 1.35)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_view()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_view()

    def add_pipe(self, name, p1, p2, weight=W_CHANNEL):
        """파이프 추가 — weight = 활성 시 위계 굵기 (W_MAIN/W_CHANNEL)"""
        path = QPainterPath(p1)
        path.lineTo(p2)
        pipe = VisualPipe(path, weight=weight)
        self.scene.addItem(pipe)
        self.items["pipes"][name] = pipe

    def _animate(self):
        """애니메이션 업데이트"""
        # 파이프 유동 애니메이션
        for pipe in self.items["pipes"].values():
            pipe.advance_flow()

        # 펌프 애니메이션
        for pump in self.items["pumps"].values():
            pump.advance_anim()

    def update_realtime(self, pumps_status, valves_status=None,
                        selector_ports=None, outlet_valve_pos=1, reactor_temp=0,
                        collector_pos=None, collector_well="", push_running=False):
        """
        실시간 상태 업데이트

        Args:
            pumps_status: {pump_name: {"running": bool, "flow_rate": float}}
            valves_status: (미사용 - 3-way 밸브 제거됨)
            selector_ports: {pump_name: port_num} - 12-way 밸브 포트
            outlet_valve_pos: 1(Waste)/2(Collect)
            reactor_temp: 반응기 온도
        """
        pumps_status = pumps_status or {}
        valves_status = valves_status or {}
        selector_ports = selector_ports or {}

        any_running = False

        # 1. 각 펌프 그룹 상태 업데이트
        for name, pump in self.items["pumps"].items():
            status = pumps_status.get(name, {})

            # 호환성: bool 또는 dict 형태 모두 지원
            if isinstance(status, bool):
                running = status
                flow_rate = 0.0
            else:
                running = status.get("running", False)
                flow_rate = status.get("flow_rate", 0.0)

            pump.set_running(running, flow_rate)

            if running:
                any_running = True

            # 시약 바이알 상태
            src = self.items.get("sources", {}).get(name)
            if src is not None:
                src.active = running
                src.update()

            # 12-way 셀렉터 상태
            if name in self.items["selectors"]:
                selector = self.items["selectors"][name]
                port = selector_ports.get(name, 1)
                selector.set_port(port)
                selector.active = running
                selector.update()

            # 3-way 스위처 상태
            if name in self.items["switchers"]:
                switcher = self.items["switchers"][name]
                direction = valves_status.get(name, 1)
                switcher.set_direction(direction)
                switcher.active = running
                switcher.update()

            # 파이프 색상 및 유동 상태
            color = C_RUNNING if running else C_OFF

            # 입력 파이프
            for pipe_key in [f"{name}_sel_in", f"{name}_in"]:
                if pipe_key in self.items["pipes"]:
                    self.items["pipes"][pipe_key].set_color(color)
                    self.items["pipes"][pipe_key].set_flowing(running)

            # 연결 파이프들
            for pipe_key in [f"{name}_sel_sw", f"{name}_sel_pump", f"{name}_sw_pump", f"{name}_out"]:
                if pipe_key in self.items["pipes"]:
                    self.items["pipes"][pipe_key].set_color(color)
                    self.items["pipes"][pipe_key].set_flowing(running)

        # 2. 합류 및 메인 라인
        main_color = C_RUNNING if any_running else C_OFF

        for key in self.items["pipes"]:
            if key.startswith("merge_") or key in ["main_to_reactor", "reactor_out"]:
                self.items["pipes"][key].set_color(main_color)
                self.items["pipes"][key].set_flowing(any_running)

        # 3. 반응기
        if self.items["reactor"]:
            self.items["reactor"].active = any_running
            self.items["reactor"].temp = reactor_temp
            self.items["reactor"].update()

        # 4. 출구
        is_collect = str(outlet_valve_pos).upper() in ["2", "COLLECT", "COLLECTION"]

        if "to_waste" in self.items["pipes"]:
            self.items["pipes"]["to_waste"].set_color(main_color if not is_collect else C_OFF)
            self.items["pipes"]["to_waste"].set_flowing(any_running and not is_collect)

        if "to_collect" in self.items["pipes"]:
            self.items["pipes"]["to_collect"].set_color(main_color if is_collect else C_OFF)
            self.items["pipes"]["to_collect"].set_flowing(any_running and is_collect)

        if "waste" in self.items["outlets"]:
            self.items["outlets"]["waste"].active = any_running and not is_collect
            self.items["outlets"]["waste"].update()

        # 4.5 T-junction 노드 활성
        for j, tee in self.items.get("tees", {}).items():
            tee.active = any_running
            tee.update()

        # 5. 출구 밸브 노드 (방향 + 활성)
        ov = self.items.get("outlet_valve")
        if ov is not None:
            ov.is_collect = is_collect
            ov.active = any_running
            ov.update()

        # 6. Collector 노드 (위치 + 활성)
        col = self.items.get("collector")
        if col is not None:
            col.active = any_running and is_collect
            if collector_pos is not None:
                if collector_pos == 0:
                    col.pos_text = "HOME"
                else:
                    well = f" ({collector_well})" if collector_well else ""
                    col.pos_text = f"Tube {collector_pos}{well}"
            col.update()

        # 7. Push pump 노드
        push = self.items.get("push")
        if push is not None:
            if push.running != bool(push_running):
                push.set_running(bool(push_running))
            self_pipe = self.items["pipes"].get("push_up")
            if self_pipe:
                self_pipe.set_color(C_RUNNING if push_running else C_OFF)
                self_pipe.set_flowing(bool(push_running))

    def set_progress(self, phase_name, pct):
        """
        반응기 아래 진행률 표시 업데이트
        @codesyncer-decision: 배관도 실시간 진행률 표시
        Args:
            phase_name: 단계명 ("Injection", "Transit" 등)
            pct: 진행률 (0~100), -1이면 미표시
        """
        reactor = self.items.get("reactor")
        if reactor:
            reactor.phase_name = phase_name
            reactor.progress = pct
            reactor.update()

    def set_pump_configs(self, pump_configs, inventory=None):
        """펌프 설정 업데이트 및 레이아웃 재생성 (configure가 가능하면 위임)"""
        if self._cfg is not None:
            self.configure(self._cfg, self._app)
            return
        self.pump_configs = pump_configs
        if inventory is not None:
            self.inventory = {item["id"]: item for item in inventory}
        self.draw_layout()

    def apply_theme(self, is_dark=True):
        """
        테마 전환 메서드
        @codesyncer-decision: 다크/라이트 테마에 따라 배경색 및 텍스트 색상 변경
        """
        global C_OFF, C_BG, C_BORDER, C_TEXT, C_ELEMENT_BG

        if is_dark:
            C_OFF = QColor(Dark.ACCENT_GRAY_DARK)
            C_BG = QColor(Dark.BG_DARK)
            C_BORDER = QColor(Dark.BORDER_PRIMARY)
            C_TEXT = QColor(Dark.TEXT_TABLE)
            C_ELEMENT_BG = QColor(Dark.BG_SECONDARY)
        else:
            C_OFF = C_OFF_LIGHT
            C_BG = C_BG_LIGHT
            C_BORDER = C_BORDER_LIGHT
            C_TEXT = C_TEXT_LIGHT
            C_ELEMENT_BG = C_ELEMENT_BG_LIGHT

        # 배경 업데이트
        self.setBackgroundBrush(C_BG)

        # 비활성 파이프 색상 업데이트
        for pipe in self.items["pipes"].values():
            if not pipe._flowing:
                pipe.set_color(C_OFF)

        # 레이아웃 다시 그리기 (텍스트 색상 등 반영)
        self.draw_layout()
