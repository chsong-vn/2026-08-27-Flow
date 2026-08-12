# -*- coding: utf-8 -*-
"""Diagram v3 — parts_pack 디자인 언어 기반 '하드웨어 적응' 배관도.

@codesyncer-decision: parts_pack.zip 은 주석(포트 타깃/앵커/그리드)이 구워진
  '스펙 시트'라 PNG 직접 삽입이 불가 — ports.json 의 포트 지오메트리와 시트의
  형태 언어(클린 스키매틱, 볼드 아웃라인, 태그)를 코드 벡터로 충실 재현한다.
  → 다크/라이트 테마, DPI 무관 선명도, 상태(로터/흐름/하이라이트) 표현 가능.

적응 요소 (모든 경우의 수):
  채널 1~4 × 라우팅(external_valve=병→12way→3way→펌프 / internal_valve=저장조→펌프
  / autosampler=바이알랙→니들→펌프, 밸브 없음) × push HPLC(+체크밸브) ×
  질소(실린더+MFC) × BPR × 분취기(plate96/colosseum) — configure() 가 cfg 에서 유도.
  + 합류 토폴로지: system_params.tjunction_entry_map 이 있으면 실물 QUAD 크로스
  (4-way, Solvent+A+B→QUAD-1, +C+D→QUAD-2)로, 없으면 레거시 캐스케이드 티로.

흐름 표시: 활성 경로 색 + 방향 대시 애니메이션(토출=정방향, 흡입=역방향).
소스 맵핑: 12-way = 현재 포트 번호(+시약명) 배지, AS = 랙에서 현재 바이알
  하이라이트 + 번호 배지.

공개 API 는 기존 FlowDiagramWidget 과 동일(ctor kw/configure/update_realtime/
set_progress/apply_theme). V3=True 로 확장 kwargs(sampler_vials, switcher_pos,
pump 상태의 refilling) 수신을 광고한다.
"""

import math

from PyQt5.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem,
                             QPushButton)
from PyQt5.QtGui import (QPainter, QPen, QBrush, QColor, QPainterPath,
                         QFont, QFontMetricsF)
from PyQt5.QtCore import Qt, QRectF, QPointF, QTimer

from ui.colors import get_palette, get_mode_extras

# ── ports.json 전사 (단위: 시트 유닛, y-up) ─────────────────────────
# 스크린 좌표(y-down) 전사 — json(y-up)에서 y 부호 반전. 일부 포트는 레이아웃
# 가독성을 위해 재정의(시린지 노즐축, 리액터 out=코일 하단) — 재드로잉 재량.
PORTS = {
    "sel12": {str(i + 1): (7.3 * math.sin(math.radians(30 * i)),
                           -7.3 * math.cos(math.radians(30 * i))) for i in range(12)},
    "v3":    {"L": (-4.0, 0.0), "R": (4.0, 0.0), "B": (0.0, 4.0)},   # B=하단 공통
    "hplc":  {"in": (-13.0, 0.0), "out": (13.0, 0.0)},
    "syr":   {"out": (10.0, -2.05), "in": (-10.0, -2.05)},  # 배럴 축선상
    "rct":   {"in": (-7.0, 0.0), "out": (7.0, 0.0)},        # 가로 코일: in 좌 → out 우 (같은 축)
    "mfc":   {"in": (4.0, 0.0), "out": (-4.0, 0.0)},
    "cyl":   {"out": (0.0, -12.7)},                          # top
    "chk":   {"in": (0.0, 1.4), "out": (0.0, -1.1)},         # 상향 흐름
    "bpr":   {"in": (-2.6, 0.0), "out": (2.6, 0.0)},
    "tee":   {"L": (-1.4, 0.0), "R": (1.4, 0.0), "B": (0.0, 1.4), "T": (0.0, -1.4)},
    "tank":  {"out": (4.5, 0.0), "top": (0.0, -6.0)},
    "fc":    {"in": (-8.4, -8.0)},
}

_DARK = True


def _pal():
    return get_palette(_DARK)


def _f(px, bold=False, mono=False):
    f = QFont("Consolas" if mono else "Segoe UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class Part(QGraphicsItem):
    """스펙시트 파츠 공통: 앵커=(0,0), PORTS×S 로 포트 스케일, 태그 하단."""
    KIND = ""
    S = 6.0

    def __init__(self, tag=""):
        super().__init__()
        self.tag = tag
        self.active = False
        self.edit = None      # 데드볼륨 편집 메타 (밸브 내부볼륨 등)
        self.owner = None
        self._ports = None   # 인스턴스 오버라이드 (미러 등)

    def port(self, name):
        ux, uy = (self._ports or PORTS[self.KIND])[name]
        return self.mapToScene(QPointF(ux * self.S, uy * self.S))

    def mouseDoubleClickEvent(self, e):
        if self.edit and self.owner is not None:
            self.owner._edit_volume(self)
        super().mouseDoubleClickEvent(e)

    def _body_pen(self, w=2.4):
        return QPen(QColor(_pal().TEXT_PRIMARY), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def _fill(self):
        return QBrush(QColor(_pal().BG_TERTIARY))

    def _tag_paint(self, p, y):
        if not self.tag:
            return
        p.setFont(_f(10, mono=True))
        p.setPen(QColor(_pal().TEXT_SECONDARY))
        r = QRectF(-60, y, 120, 14)
        p.drawText(r, Qt.AlignCenter, self.tag)

    def _stub(self, p, name, ln=7):
        """포트 스텁(짧은 선+도트) — 파이프 도킹 시각 정합."""
        ux, uy = PORTS[self.KIND][name]
        pt = QPointF(ux * self.S, uy * self.S)
        v = QPointF(ux, uy)
        n = math.hypot(v.x(), v.y()) or 1.0
        d = QPointF(v.x() / n * ln, v.y() / n * ln)
        p.setPen(self._body_pen(2.0))
        p.drawLine(pt - d, pt)
        p.setBrush(QBrush(QColor(_pal().TEXT_PRIMARY)))
        p.drawEllipse(pt, 2.2, 2.2)


class PartBottle(Part):
    """시약병/저장조/폐기 (reservoir_tank 시트)."""
    KIND = "tank"
    S = 5.0

    def __init__(self, tag="", label="", waste=False):
        super().__init__(tag)
        self.label = label
        self.waste = waste
        self.number = None      # 소스 번호 배지 (선택)

    def boundingRect(self):
        return QRectF(-34, -42, 68, 84)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        body = QRectF(-20, -24, 40, 50)
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(body, 6, 6)
        p.drawRect(QRectF(-8, -32, 16, 8))     # 목
        # 액면
        lvl = QRectF(-17, 2, 34, 21)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(P.STATE_FAULT if self.waste else P.ACCENT_CYAN)))
        p.setOpacity(0.55 if self.active else 0.28)
        p.drawRoundedRect(lvl, 4, 4)
        p.setOpacity(1.0)
        self._stub(p, "out")
        if self.label:
            p.setFont(_f(10, bold=True))
            p.setPen(QColor(P.TEXT_PRIMARY))
            p.drawText(QRectF(-30, -18, 60, 16), Qt.AlignCenter, self.label)
        self._tag_paint(p, 30)


class PartVialRack(Part):
    """오토샘플러 바이알 랙 — 밸브 없음, 니들이 소스 선택. 현재 바이알 하이라이트."""
    KIND = "tank"   # 포트 지오메트리 미사용 (out = 니들)
    W, H = 128, 74

    def __init__(self, tag="", vials=None):
        super().__init__(tag)
        self.vials = list(vials or [])[:10]
        self.current = None

    def port(self, name):   # out = 니들 도킹점 (우중앙)
        return self.mapToScene(QPointF(self.W / 2 + 6, 0))

    def boundingRect(self):
        return QRectF(-self.W / 2 - 4, -self.H / 2 - 6, self.W + 30, self.H + 26)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(QRectF(-self.W / 2, -self.H / 2, self.W, self.H), 8, 8)
        # 바이알 2×5
        n = max(1, len(self.vials))
        cols = 5
        for i, v in enumerate(self.vials):
            cx = -self.W / 2 + 16 + (i % cols) * 24
            cy = -self.H / 2 + 20 + (i // cols) * 30
            cur = (str(v) == str(self.current))
            p.setPen(QPen(QColor(P.STATE_RUN if cur else P.BORDER_LIGHT), 2.4 if cur else 1.4))
            p.setBrush(QBrush(QColor(P.STATE_RUN_BG if cur else P.BG_INPUT)))
            p.drawEllipse(QPointF(cx, cy), 9, 9)
            p.setFont(_f(8, mono=True))
            p.setPen(QColor(P.TEXT_PRIMARY if cur else P.TEXT_SECONDARY))
            p.drawText(QRectF(cx - 11, cy - 7, 22, 14), Qt.AlignCenter, str(v)[:3])
        # 니들 암 → 우측 도킹
        p.setPen(self._body_pen(2.0))
        p.drawLine(QPointF(self.W / 2, 0), QPointF(self.W / 2 + 6, 0))
        # 현재 바이알 배지
        if self.current is not None:
            p.setFont(_f(10, bold=True, mono=True))
            p.setPen(QColor(P.STATE_RUN))
            p.drawText(QRectF(-self.W / 2, self.H / 2 + 2, self.W, 14),
                       Qt.AlignCenter, f"VIAL {self.current}")
        self._tag_paint(p, self.H / 2 + 16 - 40 + 40)  # 아래
        # (tag 는 배지 아래)


class Part12Way(Part):
    KIND = "sel12"
    S = 3.6   # 실물 ~7cm 밸브 — 펌프보다 작게

    def __init__(self, tag=""):
        super().__init__(tag)
        self.port_num = 1
        self.reagent = ""

    def set_port(self, n):
        # @codesyncer-risk: 실기 미연결 시 Runze 위치가 "UNKNOWN" 문자열로
        #   보고됨 → int() 크래시가 모니터링 틱마다 발생(사용자 로그).
        #   비수치 위치는 마지막 표시를 유지(모니터링은 계속 굴러가야 함).
        try:
            self.port_num = max(1, min(12, int(float(n))))
        except (TypeError, ValueError):
            return
        self.update()

    def boundingRect(self):
        r = 7.3 * self.S + 15
        return QRectF(-r, -r - 16, 2 * r, 2 * r + 36)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        R = 5.4 * self.S
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, 0), R, R)
        # 12 스텁 + 도트
        for k in range(1, 13):
            self._stub(p, str(k), ln=6)
        # 로터 → 활성 포트
        ux, uy = PORTS["sel12"][str(self.port_num)]
        tgt = QPointF(ux * self.S, uy * self.S)
        p.setPen(QPen(QColor(P.STATE_RUN if self.active else P.ACCENT_CYAN), 4.5,
                      Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 0), tgt * 0.94)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(P.STATE_RUN if self.active else P.ACCENT_CYAN)))
        p.drawEllipse(QPointF(0, 0), 5, 5)
        # 포트 번호 배지 (현재 소스 맵핑)
        p.setFont(_f(11, bold=True, mono=True))
        p.setPen(QColor(P.TEXT_PRIMARY))
        p.drawText(QRectF(-R, -9, 2 * R, 18), Qt.AlignCenter, f"P{self.port_num}")
        if self.reagent:
            # 밸브 '위' 배치 — 하단은 태그/칩 전용 레인 (겹침 방지)
            p.setFont(_f(9))
            p.setPen(QColor(P.TEXT_SECONDARY))
            p.drawText(QRectF(-60, -7.3 * self.S - 15, 120, 12),
                       Qt.AlignCenter, self.reagent[:16])
        self._tag_paint(p, 7.3 * self.S + 4)


class Part3Way(Part):
    KIND = "v3"
    S = 3.6   # 소형 솔레노이드 피팅(~5cm) — 12-way 헤드보다 작게

    def __init__(self, tag=""):
        super().__init__(tag)
        self.direction = 2      # 1=SOURCE(L-B), 2=REACTOR(B-R)

    def set_direction(self, d):
        self.direction = 1 if str(d) in ("1", "SOURCE", "REFILL") else 2
        self.update()

    def boundingRect(self):
        r = 4.0 * self.S + 12
        return QRectF(-r, -r - 10, 2 * r, 2 * r + 12)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        R = 2.5 * self.S
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, 0), R, R)
        for k in ("L", "R", "B"):
            self._stub(p, k, ln=6)
        # 열린 통로 볼드 바: 1=SOURCE(L↔B) 청록, 2=REACTOR(B↔R) 가동녹/유휴시안
        a = QPointF(*[c * self.S for c in PORTS["v3"]["B"]])
        b = QPointF(*[c * self.S for c in PORTS["v3"]["L" if self.direction == 1 else "R"]])
        if self.direction == 1:
            col = QColor(P.ACT_FILL)
        else:
            col = QColor(P.STATE_RUN if self.active else P.ACCENT_CYAN)
        p.setPen(QPen(col, 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a * 0.6, QPointF(0, 0))
        p.drawLine(QPointF(0, 0), b * 0.6)
        self._tag_paint(p, -4.0 * self.S - 16)   # 상단 — 하단은 칩 레인


class PartSyringe(Part):
    """시린지 펌프 (SP 시트).
    mirror=True: 단일 노즐이 왼쪽(외부밸브 3-way 공통으로) /
    dual=True: 내장밸브 2포트(in 좌 / out 우) / 기본: out 우측."""
    KIND = "syr"
    S = 5.2   # 실물 ~25cm — 밸브류 대비 우위 강화

    def __init__(self, tag="", dual=False, mirror=False):
        super().__init__(tag)
        self.dual = dual
        self.mirror = mirror
        self.running = False
        self.refilling = False
        if mirror:
            self._ports = {"out": (-10.0, -2.05), "in": (10.0, -2.05)}

    def boundingRect(self):
        return QRectF(-11 * self.S, -3 * self.S - 14, 22 * self.S, 9 * self.S + 30)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        # 본체
        body = QRectF(-10 * self.S, -1.2 * self.S, 20 * self.S, 5.4 * self.S)
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(body, 8, 8)
        # 배럴 (본체 위, 축선 y=-2.05S)
        yb = -2.05 * self.S
        bar = QRectF(-6 * self.S, yb - 0.95 * self.S, 12 * self.S, 1.9 * self.S)
        p.setBrush(QBrush(QColor(P.BG_INPUT)))
        p.drawRect(bar)
        pm = self._ports or PORTS["syr"]
        ox = pm["out"][0]
        # 노즐 라인 (배럴 → out)
        p.drawLine(QPointF(6 * self.S if ox > 0 else -6 * self.S, yb),
                   QPointF(ox * self.S, yb))
        self._stub(p, "out", ln=5)
        if self.dual:
            ix = pm["in"][0]
            p.drawLine(QPointF(-6 * self.S if ix < 0 else 6 * self.S, yb),
                       QPointF(ix * self.S, yb))
            self._stub(p, "in", ln=5)
        # 상태 LED
        led = QColor(P.ACT_FILL if self.refilling else (P.STATE_RUN if self.running else P.TEXT_DISABLED))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(led))
        p.drawEllipse(QPointF(-8 * self.S, 1.5 * self.S), 4, 4)
        self._tag_paint(p, 4.6 * self.S)


class PartHplc(Part):
    KIND = "hplc"
    S = 4.4   # HPLC 펌프도 실물 대형(벤치탑)

    def __init__(self, tag=""):
        super().__init__(tag)
        self.running = False

    def boundingRect(self):
        return QRectF(-13 * self.S - 8, -6 * self.S, 26 * self.S + 16, 12 * self.S + 16)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(QRectF(-11 * self.S, -4.5 * self.S, 22 * self.S, 9 * self.S), 8, 8)
        # 헤드 원 2개 (HPLC 이중 플런저 관례)
        for dx in (-4 * self.S, 2.5 * self.S):
            p.drawEllipse(QPointF(dx, 0), 2.2 * self.S, 2.2 * self.S)
        self._stub(p, "in"); self._stub(p, "out")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(P.STATE_RUN if self.running else P.TEXT_DISABLED)))
        p.drawEllipse(QPointF(8.5 * self.S, -3 * self.S), 4, 4)
        p.setFont(_f(10, bold=True))
        p.setPen(QColor(P.TEXT_PRIMARY))
        p.drawText(QRectF(-40, -8, 80, 16), Qt.AlignCenter, "HPLC")
        self._tag_paint(p, 4.5 * self.S + 4)


class PartReactor(Part):
    """반응기 = 가로로 감긴 코일(튜빙). 광반응 전용 아님 → 450nm/LED 없음.
    in 좌 → out 우 (같은 축)."""
    KIND = "rct"
    S = 5.8   # 대형 코일 어셈블리

    def __init__(self, tag=""):
        super().__init__(tag)
        self.temp = None
        self.vol_ml = None   # 장비 내부 부피 표시

    def boundingRect(self):
        S = self.S
        W, H = 12.0 * S, 5.6 * S
        return QRectF(-W / 2 - 14, -H / 2 - 14, W + 28, H + 92)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        X = get_mode_extras(_DARK)
        S = self.S
        W, H = 12.0 * S, 5.6 * S
        # @codesyncer: (1) 색 통일 — 반응기 코일을 다른 파츠와 동일한 본체색(TEXT_PRIMARY)으로
        #   (기존 ACCENT_CYAN 은 홀로 튐). (2) 겹침 제거 — 촘촘히 겹친 세로 타원 대신 둥근 카트리지
        #   캡슐 + 내부 사행(serpentine) 유로 한 획 → 깔끔한 코일 리액터 아이콘. in/out 중앙축 유지.
        cap = QRectF(-W / 2, -H / 2, W, H)
        p.setPen(self._body_pen(2.4))
        p.setBrush(self._fill())
        p.drawRoundedRect(cap, H * 0.34, H * 0.34)
        # 내부 사행 유로 — 사인 궤적(겹침 없음). 가열 시에만 열색(SENSOR_TEMP) 힌트, 평소 본체색.
        inW = W - H * 0.9
        amp = H * 0.30
        periods = 5
        segs = 120
        path = QPainterPath()
        for i in range(segs + 1):
            t = i / segs
            x = -inW / 2 + inW * t
            y = amp * math.sin(2 * math.pi * periods * t)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        heated = isinstance(self.temp, (int, float)) and self.temp
        coil_col = QColor(X.SENSOR_TEMP if heated else P.TEXT_PRIMARY)
        p.setPen(QPen(coil_col, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # in/out 리드선 (캡슐 양끝 → 포트, 중앙축)
        p.setPen(self._body_pen(2.0))
        ix, _ = PORTS["rct"]["in"]; ox, _ = PORTS["rct"]["out"]
        p.drawLine(QPointF(ix * S, 0), QPointF(-W / 2, 0))
        p.drawLine(QPointF(W / 2, 0), QPointF(ox * S, 0))
        self._stub(p, "in", ln=5); self._stub(p, "out", ln=5)
        # 온도 리드아웃 (캡슐 아래)
        y_txt = H / 2 + 8
        p.setFont(_f(11, bold=True, mono=True))
        p.setPen(QColor(X.SENSOR_TEMP if heated else P.TEXT_DISABLED))
        t = f"{self.temp:.1f}°C" if isinstance(self.temp, (int, float)) else "--.- °C"
        p.drawText(QRectF(-60, y_txt, 120, 16), Qt.AlignCenter, t)
        if self.vol_ml:
            p.setFont(_f(9, mono=True))
            p.setPen(QColor(P.TEXT_SECONDARY))
            p.drawText(QRectF(-60, y_txt + 16, 120, 12), Qt.AlignCenter,
                       f"V={self.vol_ml:.2f} mL")
        self._tag_paint(p, y_txt + 32)


class PartMfc(Part):
    KIND = "mfc"
    S = 4.8   # 실물 ~10cm 박스 — 3-way 피팅보다 크고 펌프보다 작게

    def boundingRect(self):
        return QRectF(-4 * self.S - 10, -3.4 * self.S, 8 * self.S + 20, 7 * self.S + 14)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(QRectF(-2.6 * self.S, -2.6 * self.S, 5.2 * self.S, 5.2 * self.S), 6, 6)
        self._stub(p, "in"); self._stub(p, "out")
        p.setFont(_f(9, bold=True))
        p.setPen(QColor(P.TEXT_PRIMARY))
        p.drawText(QRectF(-30, -7, 60, 14), Qt.AlignCenter, "MFC")
        self._tag_paint(p, 2.6 * self.S + 3)


class PartCylinder(Part):
    KIND = "cyl"
    S = 6.2  # 실물 최장신 — 리액터/펌프보다 확실히 크게(사용자 개연성 지적)

    def boundingRect(self):
        return QRectF(-4 * self.S, -13.4 * self.S, 8 * self.S, 27 * self.S)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(QRectF(-2.6 * self.S, -11 * self.S, 5.2 * self.S, 22 * self.S),
                          8, 8)
        p.drawRect(QRectF(-1.0 * self.S, -12.4 * self.S, 2.0 * self.S, 1.6 * self.S))
        self._stub(p, "out", ln=5)
        p.save()
        p.setFont(_f(10, bold=True))
        p.setPen(QColor(P.TEXT_PRIMARY))
        p.rotate(-90)
        p.drawText(QRectF(-40, -8, 80, 16), Qt.AlignCenter, "N2")
        p.restore()
        self._tag_paint(p, 11.6 * self.S)


class PartCheck(Part):
    KIND = "chk"
    S = 7.0  # 피팅류=소형

    def __init__(self, tag="", flip=False):
        super().__init__(tag)
        self.flip = flip   # True = 하향 흐름 허용 (in=위 → out=아래, QUAD-1 solvent 라이저용)
        if flip:
            self._ports = {"in": (0.0, -1.4), "out": (0.0, 1.1)}

    def boundingRect(self):
        return QRectF(-2.4 * self.S, -2.2 * self.S, 4.8 * self.S, 4.4 * self.S)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        if self.flip:
            p.save()
            p.scale(1, -1)   # 스터브·화살표 일괄 상하 반전 — _ports 오버라이드와 정합
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, 0), 1.2 * self.S, 1.2 * self.S)
        # 체크 화살표 (상향 = 허용 방향; flip 시 스케일로 하향)
        p.setPen(QPen(QColor(P.TEXT_PRIMARY), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(0, 5), QPointF(0, -5))
        p.drawLine(QPointF(-4, -1), QPointF(0, -5))
        p.drawLine(QPointF(4, -1), QPointF(0, -5))
        self._stub(p, "in", ln=4); self._stub(p, "out", ln=4)
        if self.flip:
            p.restore()


class PartBpr(Part):
    KIND = "bpr"
    S = 6.0

    def boundingRect(self):
        return QRectF(-2.6 * self.S - 8, -2.2 * self.S - 8, 5.2 * self.S + 16, 4.6 * self.S + 20)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, -2), 1.5 * self.S, 1.5 * self.S)
        p.drawLine(QPointF(0, -2 - 1.5 * self.S), QPointF(0, -2 - 1.5 * self.S - 6))
        self._stub(p, "in"); self._stub(p, "out")
        p.setFont(_f(8, bold=True))
        p.setPen(QColor(P.TEXT_PRIMARY))
        p.drawText(QRectF(-24, -10, 48, 16), Qt.AlignCenter, "BPR")
        self._tag_paint(p, 1.6 * self.S + 4)


class PartPhaseSensor(Part):
    """위상센서(OCB350) 클램프온 글리프 — 파이프 위 어노테이션(흐름 재배선 없음).

    @codesyncer: roles.phase 배정 시 _build 가 collect(아웃렛 직전)/reactor_in(리액터
    입구) 지점에 배치. 상태 도트: 액체=시안 채움 / 기체=아웃라인 / None=뮤트(미수신).
    """
    KIND = "psen"
    S = 5.0

    def __init__(self, tag="", label=""):
        super().__init__(tag)
        self.label = label      # 짧은 채널명 (COL / IN)
        self.phase = None       # None | 0(기체) | 1(액체)

    def set_phase(self, v):
        nv = None if v is None else int(v)
        if nv != self.phase:
            self.phase = nv
            self.update()

    def boundingRect(self):
        return QRectF(-3.2 * self.S, -4.4 * self.S, 6.6 * self.S, 8.6 * self.S)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        S = self.S
        # 몸통 — 파이프(수평)를 세로로 감싸는 클램프온 하우징
        p.setPen(self._body_pen(2.0))
        p.setBrush(self._fill())
        p.drawRoundedRect(QRectF(-1.6 * S, -2.4 * S, 3.2 * S, 4.8 * S), 4, 4)
        # 광학창 슬롯 (튜브가 지나는 어두운 개구 — OCB350 시그니처)
        p.setPen(QPen(QColor(P.BORDER_SECONDARY), 1.2))
        p.setBrush(QBrush(QColor(P.BG_PRIMARY)))
        p.drawRoundedRect(QRectF(-0.55 * S, -1.45 * S, 1.1 * S, 2.9 * S), 2, 2)
        # 케이블 스텁 (우상단 → 위) — 계측기 결선 힌트
        p.setPen(QPen(QColor(P.TEXT_SECONDARY), 1.4, Qt.SolidLine, Qt.RoundCap))
        p.drawPolyline(QPointF(1.0 * S, -2.4 * S), QPointF(1.0 * S, -3.3 * S),
                       QPointF(2.1 * S, -3.3 * S))
        p.setBrush(QBrush(QColor(P.TEXT_SECONDARY)))
        p.drawEllipse(QPointF(2.1 * S, -3.3 * S), 1.4, 1.4)
        # 상태 도트 (창 안: 액체=채움 / 기체=아웃라인 / None=뮤트)
        if self.phase == 1:
            p.setPen(QPen(QColor(P.ACCENT_CYAN), 1.6))
            p.setBrush(QBrush(QColor(P.ACCENT_CYAN)))
        elif self.phase == 0:
            p.setPen(QPen(QColor(P.ACCENT_CYAN), 1.6))
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(QPen(QColor(P.TEXT_DISABLED), 1.4))
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), 0.78 * S, 0.78 * S)
        # 채널 라벨 (위) + 태그 (아래)
        if self.label:
            p.setFont(_f(8, bold=True))
            p.setPen(QColor(P.TEXT_SECONDARY))
            p.drawText(QRectF(-30, -2.4 * S - 16, 60, 12),
                       Qt.AlignCenter, self.label)
        self._tag_paint(p, 2.4 * S + 4)


class PartTee(Part):
    """합류 피팅: used=배관 연결 포트(스터브+도트), capped=배관 없는 물리 포트(캡 마개).
    4포트 전부면 4-way 크로스(QUAD), 3포트면 T. 태그는 우하단(스파인·칩 레인 회피)."""
    KIND = "tee"
    S = 7.0

    def __init__(self, tag="", used=("L", "R", "B"), capped=()):
        super().__init__(tag)
        self.used = used
        self.capped = tuple(capped)

    def boundingRect(self):
        r = QRectF(-1.6 * self.S, -1.6 * self.S, 3.2 * self.S, 3.2 * self.S)
        if self.tag:
            r = r.united(QRectF(10, 8, 56, 18))
        return r

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(self._body_pen(2.2))
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, 0), 0.9 * self.S, 0.9 * self.S)
        for k in self.used:
            self._stub(p, k, ln=4)
        for k in self.capped:
            # 캡 마개: 짧은 스터브 + 직교 캡 바 — QUAD 크로스의 미배관 여분 포트
            ux, uy = PORTS[self.KIND][k]
            pt = QPointF(ux * self.S, uy * self.S)
            nlen = math.hypot(ux, uy) or 1.0
            nv = QPointF(-uy / nlen * 4.5, ux / nlen * 4.5)
            p.setPen(self._body_pen(2.0))
            p.drawLine(pt * 0.62, pt)
            p.drawLine(pt - nv, pt + nv)
        if self.tag:
            p.setFont(_f(9, mono=True))
            p.setPen(QColor(P.TEXT_SECONDARY))
            p.drawText(QRectF(12, 9, 52, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       self.tag)


class PartOutlet3Way(Part):
    """아웃렛 3-way: L=in(반응기측), R=Collect, B=Waste."""
    KIND = "v3"
    S = 4.0   # 3-way 급 피팅

    def __init__(self, tag=""):
        super().__init__(tag)
        self.collect = False

    def boundingRect(self):
        return QRectF(-4 * self.S - 12, -4 * self.S - 12, 8 * self.S + 24, 8 * self.S + 28)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        R = 2.5 * self.S
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawEllipse(QPointF(0, 0), R, R)
        for k in ("L", "R", "B"):
            self._stub(p, k, ln=6)
        a = QPointF(*[c * self.S for c in PORTS["v3"]["L"]])
        b = QPointF(*[c * self.S for c in PORTS["v3"]["R" if self.collect else "B"]])
        p.setPen(QPen(QColor(P.STATE_RUN if self.collect else P.ACT_STOP), 4.5,
                      Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a * 0.6, QPointF(0, 0))
        p.drawLine(QPointF(0, 0), b * 0.6)
        p.setFont(_f(9, bold=True))
        p.setPen(QColor(P.STATE_RUN if self.collect else P.TEXT_SECONDARY))
        p.drawText(QRectF(-40, -R - 16, 80, 12), Qt.AlignCenter,
                   "COLLECT" if self.collect else "WASTE")
        self._tag_paint(p, 4.0 * self.S + 4)


class PartCollector(Part):
    KIND = "fc"
    S = 6.6  # 실물 대형(플레이트 스테이지)

    def __init__(self, tag="", plate=True):
        super().__init__(tag)
        self.plate = plate
        self.pos = None
        self.well = ""

    def boundingRect(self):
        return QRectF(-8.4 * self.S - 12, -8 * self.S - 10, 16.8 * self.S + 24, 16 * self.S + 32)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        card = QRectF(-7 * self.S, -6.4 * self.S, 14 * self.S, 12.8 * self.S)
        p.setPen(self._body_pen())
        p.setBrush(self._fill())
        p.drawRoundedRect(card, 8, 8)
        if self.plate:
            # 8×12 미니 그리드
            gx0, gy0 = card.left() + 8, card.top() + 10
            gw = (card.width() - 16) / 12
            gh = (card.height() - 34) / 8
            p.setPen(QPen(QColor(P.BORDER_LIGHT), 0.8))
            for r in range(8):
                for c in range(12):
                    p.drawEllipse(QPointF(gx0 + c * gw + gw / 2, gy0 + r * gh + gh / 2),
                                  min(gw, gh) * 0.32, min(gw, gh) * 0.32)
            if self.pos:
                i = int(self.pos) - 1
                r, c = (i // 12) % 8, i % 12
                p.setPen(QPen(QColor(P.STATE_RUN), 2))
                p.setBrush(QBrush(QColor(P.STATE_RUN)))
                p.drawEllipse(QPointF(gx0 + c * gw + gw / 2, gy0 + r * gh + gh / 2),
                              min(gw, gh) * 0.42, min(gw, gh) * 0.42)
        else:
            # 콜로세움: 원형 캐러셀
            p.setPen(QPen(QColor(P.BORDER_LIGHT), 1.2))
            cc = card.center()
            for k in range(12):
                a = math.radians(k * 30)
                p.drawEllipse(QPointF(cc.x() + 34 * math.sin(a), cc.y() - 34 * math.cos(a)), 4, 4)
            if self.pos:
                a = math.radians(((int(self.pos) - 1) % 12) * 30)
                p.setBrush(QBrush(QColor(P.STATE_RUN)))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(cc.x() + 34 * math.sin(a), cc.y() - 34 * math.cos(a)), 6, 6)
        self._stub(p, "in", ln=6)
        p.setFont(_f(10, bold=True, mono=True))
        p.setPen(QColor(P.STATE_RUN if self.pos else P.TEXT_SECONDARY))
        txt = self.well or (f"T{self.pos}" if self.pos else "--")
        p.drawText(QRectF(card.left(), card.bottom() - 20, card.width(), 16),
                   Qt.AlignCenter, txt)
        self._tag_paint(p, card.bottom() + 4)


class ZoneBand(QGraphicsItem):
    """공정 존 배경 밴드 (FEED SUPPLY / REACTION / COLLECTION) — ISA 구획 관례.
    파이프/파츠 뒤(z=-10) 저채도 배경 + 좌상단 캡션. 색은 _pal() 추종."""

    def __init__(self, rect, caption):
        super().__init__()
        self._r = QRectF(rect)
        self.caption = caption
        self.setZValue(-10)

    def boundingRect(self):
        return self._r

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        fill = QColor(P.BG_TERTIARY)
        fill.setAlphaF(0.30)
        bd = QColor(P.BORDER_SECONDARY)
        bd.setAlphaF(0.55)
        p.setPen(QPen(bd, 1))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(self._r.adjusted(2, 2, -2, -2), 10, 10)
        p.setFont(_f(10, bold=True))
        cap = QColor(P.TEXT_DISABLED)
        p.setPen(cap)
        p.drawText(QRectF(self._r.left() + 14, self._r.top() + 8,
                          self._r.width() - 20, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, self.caption)


class FlowChevron(QGraphicsItem):
    """정적 흐름 방향 마커 '>' — 정지 화면/스크린샷에서도 방향이 읽히게(P&ID 관례).
    런타임 대시 애니메이션과 별개의 저채도 힌트."""

    def __init__(self):
        super().__init__()
        self.setZValue(1.5)

    def boundingRect(self):
        return QRectF(-7, -7, 14, 14)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        p.setPen(QPen(QColor(P.TEXT_DISABLED), 1.7, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPointF(-3, -4.5), QPointF(3.5, 0), QPointF(-3, 4.5))


class VolChip(QGraphicsItem):
    """세그먼트 데드볼륨 배지 — 현재 mL 표시 + 더블클릭 편집."""

    def __init__(self, owner, edit):
        super().__init__()
        self.owner = owner
        self.edit = edit
        self.setZValue(3)
        self.setToolTip(owner._vol_tip(edit))
        self.setCursor(Qt.PointingHandCursor)

    def _txt(self):
        return f"{self.owner._vol_cur(self.edit):.2f}"

    def _value(self):
        try:
            return float(self.owner._vol_cur(self.edit))
        except Exception:
            return 0.0

    def boundingRect(self):
        return QRectF(-24, -9, 48, 18)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        full = self._value() > 1e-9 or getattr(self.owner, "show_detail", False)
        if not full:
            # @codesyncer(사용자 지적): 미입력 구간이 숨겨지면 '채울 자리'가 안 보임 —
            #   항상 표시하되 저채도 소형 '+' 플레이스홀더 (DETAIL 켜면 0.00 풀칩).
            r = QRectF(-10, -7, 20, 14)
            pen = QPen(QColor(P.TEXT_DISABLED), 1)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QBrush(QColor(P.BG_DARK)))
            p.drawRoundedRect(r, 6, 6)
            p.setFont(_f(9, bold=True))
            p.setPen(QColor(P.TEXT_DISABLED))
            p.drawText(r, Qt.AlignCenter, "+")
            return
        r = QRectF(-22, -8, 44, 16)
        p.setPen(QPen(QColor(P.BORDER_LIGHT), 1))
        p.setBrush(QBrush(QColor(P.BG_DARK)))
        p.drawRoundedRect(r, 7, 7)
        p.setFont(_f(9, mono=True))
        p.setPen(QColor(P.TEXT_TERTIARY))
        p.drawText(r, Qt.AlignCenter, self._txt())

    def mouseDoubleClickEvent(self, e):
        if self.owner is not None:
            self.owner._edit_volume(self)
        super().mouseDoubleClickEvent(e)


class Pipe(QGraphicsItem):
    """직교 배관 — 상태(활성/방향/흐름) + 대시 애니메이션."""

    def __init__(self, points, weight=5.0):
        super().__init__()
        self.edit = None      # (title, key_kind, ident, key) — 데드볼륨 편집 메타
        self.owner = None
        self.pts = [QPointF(*pt) if isinstance(pt, tuple) else pt for pt in points]
        self.weight = weight
        self.active = False
        self.flowing = False
        self.direction = 1      # +1 정방향(pts 순서), -1 역방향
        self.color_key = "run"  # run|fill|gas
        self.phase = 0.0
        self.setZValue(-1)

    def endpoints(self):
        return self.pts[0], self.pts[-1]

    def _path(self):
        path = QPainterPath(self.pts[0])
        for pt in self.pts[1:]:
            path.lineTo(pt)
        return path

    def boundingRect(self):
        r = self._path().boundingRect()
        return r.adjusted(-8, -8, 8, 8)

    def paint(self, p, o, w):
        p.setRenderHint(QPainter.Antialiasing)
        P = _pal()
        base = QColor(P.BORDER_LIGHT)
        col = {"run": QColor(P.STATE_RUN), "fill": QColor(P.ACT_FILL),
               "gas": QColor(P.ACCENT_PURPLE)}[self.color_key]
        path = self._path()
        # 바탕관
        p.setPen(QPen(base if not self.active else col, self.weight,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.active:
            glow = QColor(col); glow.setAlpha(46)
            p.setPen(QPen(glow, self.weight + 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(col, self.weight, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        # 흐름 대시 (방향 애니메이션)
        if self.flowing:
            dash = QPen(QColor("#ffffff"), max(2.0, self.weight - 2.6),
                        Qt.CustomDashLine, Qt.RoundCap)
            dash.setDashPattern([1.2, 2.6])
            dash.setDashOffset(-self.phase if self.direction >= 0 else self.phase)
            p.setPen(dash)
            p.setOpacity(0.85)
            p.drawPath(path)
            p.setOpacity(1.0)

    def mouseDoubleClickEvent(self, e):
        if self.edit and self.owner is not None:
            self.owner._edit_volume(self)
        super().mouseDoubleClickEvent(e)

    def set_state(self, active=None, flowing=None, direction=None, color=None):
        if active is not None: self.active = bool(active)
        if flowing is not None: self.flowing = bool(flowing)
        if direction is not None: self.direction = 1 if direction >= 0 else -1
        if color is not None: self.color_key = color
        self.update()


# ═══════════════════════════════════════════════════════════════════
class FlowDiagramWidget(QGraphicsView):
    """parts_pack 기반 하드웨어 적응 배관도 (기존 API 호환, V3)."""
    V3 = True

    ROW = 136

    def __init__(self, pump_configs=None, active_pumps=None, inventory=None,
                 parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setFrameShape(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._cfg = None
        self._app = None
        self._inlet = None
        self.items_ = {"pumps": {}, "selectors": {}, "switchers": {}, "racks": {},
                       "sources": {}, "ch_pipes": {}, "src_pipes": {},
                       "pump_link": {}}
        self.phase_text = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(120)
        self._all_pipes = []
        # @codesyncer(P2/ISA-101): 데드볼륨 칩은 상세 레이어 — 기본은 '값 있는 칩만'
        #   표시(0.00 숨김=오버뷰 클러터 제거), DETAIL 토글로 전체(편집 진입점) 노출.
        self.show_detail = False
        self._vol_chips = []
        self._btn_detail = QPushButton("DETAIL", self)
        self._btn_detail.setCheckable(True)
        self._btn_detail.setCursor(Qt.PointingHandCursor)
        self._btn_detail.setToolTip(
            "데드볼륨(mL) 배지 전체 표시 — 값이 0인 구간 포함 (더블클릭 편집)")
        self._btn_detail.toggled.connect(self.set_detail)
        self._style_detail_btn()
        if active_pumps:
            class _MiniCfg:  # configure 전 초기 표시용
                ACTIVE_PUMPS = list(active_pumps)
                PUMP_ROUTING = {p: "external_valve" for p in active_pumps}
                PUMP_SAMPLER_MAP = {}
                config_data = {"system_params": {}}
            self._build(_MiniCfg(), None)

    # ── 토폴로지 빌드 ────────────────────────────────────────────
    def configure(self, cfg, app=None):
        self._cfg = cfg
        self._app = app
        mm = getattr(app, "map_mgr", None)
        self._inlet = (lambda p, port: mm.get_inlet(p, port)) if mm else None
        self._build(cfg, app)
        # 핫리로드 재구성: 씬 크기가 변했으므로 즉시 refit + 관측 로그
        print(f"[Diagram] rebuild: {len(self.items_['pumps'])} ch | "
              f"N2={'Y' if self.n2_parts else 'N'} push={'Y' if self.push_parts else 'N'} "
              f"BPR={'Y' if self.bpr else 'N'} col={'Y' if self.collector_part else 'N'}")
        if self.isVisible():
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def _clear(self):
        self._scene.clear()
        self._all_pipes = []
        for k in self.items_:
            self.items_[k] = {}
        self.phase_text = None
        self.sensor_items = {}
        self._vol_chips = []

    def _build(self, cfg, app):
        self._clear()
        pumps = list(getattr(cfg, "ACTIVE_PUMPS", []) or [])
        routing = getattr(cfg, "PUMP_ROUTING", {}) or {}
        sp = (getattr(cfg, "config_data", {}) or {}).get("system_params", {})
        n = max(1, len(pumps))
        has_push = bool(getattr(app, "push_pump", None)) if app else bool(sp.get("push_pump_diagram"))
        # 질소: 플래그 OR roles.gas 배정 OR 런타임 MFC 인스턴스 — 어느 경로로
        # 추가돼도 배관도가 MFC 어셈블리를 그린다
        gas_id = (((getattr(cfg, "config_data", {}) or {}).get("roles", {}) or {})
                  .get("gas", {}) or {}).get("driver_id")
        has_n2 = (bool(sp.get("nitrogen_line", False))
                  or bool(gas_id and gas_id != "None")
                  or bool(getattr(getattr(app, "hw_mgr", None), "mfc", None)))
        has_bpr = bool(sp.get("bpr_enabled", False))
        col = getattr(app, "collector", None) if app else None
        has_col = col is not None
        plate = bool(col is not None and hasattr(col, "get_well_id"))

        # X 레인
        # @codesyncer(P1): 비율 재배분 — 피드 존 ~20% 압축, 메인 공정부 ~28% 확장
        #   (피드 반복 4행이 시각 무게 과점, 리액터~수집이 왜소하던 문제 — 사용자 지적).
        #   P&ID 관례: 공정의 '이야기'(반응~수집)가 가로 폭의 주역.
        X_SRC, X_SEL, X_SW, X_PUMP, X_MAN = 60, 165, 260, 375, 470
        X_N2, X_PUSH = 560, 655
        X_RCT, X_BPR, X_OUT, X_SINK = 795, 915, 1015, 1150
        top = 70
        ys = [top + 60 + i * self.ROW for i in range(n)]
        y_m = ys[-1]                     # 스파인은 위→아래로 합류, 머지=마지막 채널行
        add = self._scene.addItem

        def pipe(points, edit=None, chip=True, chip_side="auto", **kw):
            pl = Pipe(points, **kw)
            pl.owner = self
            if edit is not None:
                pl.edit = edit
                pl.setToolTip(self._vol_tip(edit))
                pl.setCursor(Qt.PointingHandCursor)
                if chip:
                    # 가장 긴 세그먼트의 중점 위에 mL 칩 — chip_side 로 겹침 회피 위치 명시
                    best, bl, horiz = None, -1.0, True
                    for a, b in zip(pl.pts, pl.pts[1:]):
                        L = abs(b.x() - a.x()) + abs(b.y() - a.y())
                        if L > bl:
                            bl, best = L, (a + b) / 2
                            horiz = abs(b.x() - a.x()) >= abs(b.y() - a.y())
                    off = {"above": QPointF(0, -14), "below": QPointF(0, 15),
                           "left": QPointF(-28, 0), "right": QPointF(28, 0)}.get(
                        chip_side, QPointF(0, -14) if horiz else QPointF(28, 0))
                    ch = VolChip(self, edit)
                    self._vol_chips.append(ch)
                    ch.setPos(best + off)
                    add(ch)
            add(pl)
            self._all_pipes.append(pl)
            return pl

        def editable_part(part, edit, chip_dy=None):
            part.owner = self
            part.edit = edit
            part.setToolTip(self._vol_tip(edit))
            part.setCursor(Qt.PointingHandCursor)
            if chip_dy is not None:
                ch = VolChip(self, edit)
                self._vol_chips.append(ch)
                ch.setPos(part.pos() + QPointF(0, chip_dy))
                add(ch)

        # ── QUAD 판정 (2026-08-12 배관 재구성) ─────────────────────
        # @codesyncer(2026-08-12b, 배관 재구성 반영 2단계): system_params.
        #   tjunction_entry_map 이 표현 가능한 형태(진입값 행순 단조 + 값 1..K 연속
        #   + K≤2)면 실물 그대로 'QUAD 크로스' 토폴로지로 그린다 — 캐스케이드 티
        #   폐지. 표현 불가 맵(비단조/정션 3개+)은 콘솔 경고 후 레거시 캐스케이드
        #   폴백(엔진 계산은 entry_map 그대로 정확 — 그림만 근사).
        entry_cfg = getattr(cfg, "tjunction_entry_map", {}) or {}
        entries = ([max(1, int(entry_cfg.get(p, 1) or 1)) for p in pumps]
                   if entry_cfg else [])
        quad = False
        vals, q_groups, q_rows = [], {}, {}
        if entries and n >= 2:
            vals = sorted(set(entries))
            mono = all(entries[i] <= entries[i + 1] for i in range(n - 1))
            if mono and vals == list(range(1, len(vals) + 1)) and len(vals) <= 2:
                quad = True
                for i, e in enumerate(entries):
                    q_groups.setdefault(e, []).append(i)
                for v, rows in q_groups.items():
                    # 정션 1 = 그룹 마지막 행(위에서 드롭), 정션 2+ = 첫 행(아래서 라이즈)
                    q_rows[v] = rows[-1] if v == vals[0] else rows[0]
                if any(len(rows) > 2 for rows in q_groups.values()):
                    print("[Diagram] entry 그룹 행수>2 — 중간 행은 수직 수집관에 탭(근사)")
            else:
                print(f"[Diagram] entry_map 표현 불가({entries}) — 캐스케이드 폴백")
                entries = []
        self._quad = quad
        self._entry_by_pump = ({p: max(1, int(entry_cfg.get(p, 1) or 1))
                                for p in pumps} if quad else {})

        def _tail(i):
            """quad 모드: 행 i 의 합류라인이 (X_MAN, y) 이후 정션 포트로 가는 추가 점.
            정션 행=L 직결(추가 없음), 그 외=이웃 행 방향으로 드롭/라이즈(2행 그룹=포트
            정확 도킹, 3행+ 그룹=다음 행 수집관에 탭)."""
            if not quad:
                return []
            v = entries[i]
            rows, jr = q_groups[v], q_rows[v]
            if i == jr:
                return []
            k = rows.index(i)
            nxt = rows[k + 1] if v == vals[0] else rows[k - 1]
            off = (-14 if v == vals[0] else 14) if nxt == jr else 0
            return [(X_MAN, ys[nxt] + off)]

        # ── 채널들 ──
        for i, pname in enumerate(pumps):
            y = ys[i]
            rt = routing.get(pname, "external_valve")
            tagn = pname.split("_")[-1]
            pump = PartSyringe(tag=f"P-{i+1:02d}",
                               dual=(rt != "external_valve"),
                               mirror=(rt == "external_valve"))
            pump.setPos(X_PUMP, y + 42)
            add(pump)
            self.items_["pumps"][pname] = pump
            seg = []
            if rt == "external_valve":
                src = PartBottle(tag=f"RSV-{tagn}", label=f"S{i+1}")
                src.setPos(X_SRC, y); add(src)
                _sfx = tagn.replace("Group", "").strip(" _-") or tagn
                sel = Part12Way(tag=f"12-Way Valve {_sfx}")
                sel.setPos(X_SEL, y); add(sel)
                sw = Part3Way(tag=f"3-Way Valve {_sfx}")
                sw.setPos(X_SW, y); add(sw)
                self.items_["selectors"][pname] = sel
                self.items_["switchers"][pname] = sw
                self.items_["sources"][pname] = src
                # 12-way 내부 = 밸브 태그(아래) 더 아래로, 3-way 내부 = 툴팁만(칩은 p3)
                editable_part(sel, ("12-way 밸브 내부", "pump", pname,
                                    "tube_vol_selector"), chip_dy=7.3 * sel.S + 46)
                editable_part(sw, ("3-way 내부+커먼", "pump", pname,
                                   "tube_vol_switcher"))
                # 병→12way: 위(공간 여유), 12way→3way: 아래(위엔 '3-Way Valve' 라벨),
                # 3way→펌프(세로): 우측, 3way→합류(긴 수평): 위(행 상단 공백)
                p1 = pipe([src.port("out"), sel.port("10")], weight=4, chip_side="above",
                          edit=("Inlet (병→12-way)", "pump", pname, "tube_vol_inlet"))
                p2 = pipe([sel.port("4"), sw.port("L")], weight=4, chip_side="below",
                          edit=("12-way→3-way", "pump", pname, "tube_vol_valve_pump"))
                pb = sw.port("B")
                po = pump.port("out")
                p3 = pipe([pb, (pb.x(), po.y()), po], weight=4, chip_side="right",
                          edit=("3-way 내부+커먼", "pump", pname, "tube_vol_switcher"))
                p4 = pipe([sw.port("R"), (X_MAN, y)] + _tail(i), weight=5,
                          chip_side="above",
                          edit=("3-way→합류", "pump", pname, "tube_vol_pump_merge"))
                self.items_["src_pipes"][pname] = [p1, p2]
                self.items_["pump_link"][pname] = [p3]
                seg = [p4]
            elif rt == "autosampler":
                vials = []
                smp = None
                if app is not None:
                    smp = (getattr(getattr(app, "hw_mgr", None), "sampler_by_pump", {})
                           or {}).get(pname)
                if smp is not None:
                    service = {"waste", "rinse", "gas", "wash", "home", "cleaning"}
                    vials = [v for v in sorted(getattr(smp, "vial_positions", {}) or {})
                             if str(v).lower() not in service]
                rack = PartVialRack(tag=f"AS-{tagn}", vials=vials or ["A1", "A2", "A3"])
                rack.setPos(X_SRC + 60, y); add(rack)
                self.items_["racks"][pname] = rack
                pin = pump.port("in")
                p1 = pipe([rack.port("out"), ((rack.port("out").x() + pin.x()) / 2, y),
                           ((rack.port("out").x() + pin.x()) / 2, pin.y()), pin], weight=4,
                          edit=("니들 라인", "pump", pname, "tube_vol_valve_pump"))
                p4 = pipe([pump.port("out"), (X_MAN, pump.port("out").y()),
                           (X_MAN, y)] + _tail(i), weight=5,
                          edit=("펌프→합류", "pump", pname, "tube_vol_pump_merge"))
                self.items_["src_pipes"][pname] = [p1]
                seg = [p4]
            else:  # internal_valve
                src = PartBottle(tag=f"RSV-{tagn}", label=f"S{i+1}")
                src.setPos(X_SEL, y); add(src)
                self.items_["sources"][pname] = src
                pin = pump.port("in")
                p1 = pipe([src.port("out"), ((src.port("out").x() + pin.x()) / 2, y),
                           ((src.port("out").x() + pin.x()) / 2, pin.y()), pin], weight=4,
                          edit=("저장조 라인", "pump", pname, "tube_vol_valve_pump"))
                p4 = pipe([pump.port("out"), (X_MAN, pump.port("out").y()),
                           (X_MAN, y)] + _tail(i), weight=5,
                          edit=("펌프→합류", "pump", pname, "tube_vol_pump_merge"))
                self.items_["src_pipes"][pname] = [p1]
                seg = [p4]
            self.items_["ch_pipes"][pname] = seg

        # ── 합류부: QUAD 크로스(실물 4-way) 또는 레거시 캐스케이드 티 ──
        # @codesyncer(교차비교 수정): 스파인 tj 칩을 타이밍 모델(_compute_plug_timing)과 정합.
        #   흐름은 아래로(exit=최하단 y_m). tee@ys[j]=T_j (j=1..n-1). 조합흐름 구간
        #   S_k=T_k→T_{k+1} = 파이프 ys[k]→ys[k+1] = 엔진 tjunction_line_vols[k] (k=1..n-2).
        #   따라서 파이프 ys[j-1]→ys[j] 는 T_{j-1}→T_j = tj_vols[j-1]. j=1(최상단 펌프의
        #   T1 이전 단독 강하)은 엔진 tj 변수가 없어 칩 생략(펌프 라인에 귀속).
        # @codesyncer(2026-08-12b, 배관 재구성 반영 2단계): quad 모드는 채널당 티
        #   캐스케이드 대신 '실물 그대로' 4-way 크로스를 그린다.
        #   QUAD-1(그룹1 마지막 행): T=첫 행 드롭, L=마지막 행 직결, R=solvent 유입
        #   (없으면 캡), B=출구 → tj[1] 수직 스파인. QUAD-2(그룹2 첫 행): T=tj[1]
        #   유입, L=첫 행 직결, B=마지막 행 라이즈, R=출구 → 수평런(y_run=그 행).
        #   tj[e_max](마지막 정션→가스T)는 부유 칩이 아니라 '실제 파이프'로 편집.
        #   미배관 물리 포트는 캡 마개. 엔진 entry_map 매핑과 1:1 정합.
        self.spine = []
        y_run = y_m
        if quad:
            v1 = vals[0]
            y_q1 = ys[q_rows[v1]]
            k2 = len(vals) == 2
            used1 = ["L"] + (["T"] if len(q_groups[v1]) >= 2 else []) \
                + (["B"] if k2 else ["R"])
            if has_push:
                used1.append("R" if k2 else "B")   # solvent 유입: K2=우측, K1=하단
            q1p = PartTee(tag=f"QUAD-{v1}", used=tuple(used1),
                          capped=tuple(k for k in ("L", "T", "B", "R")
                                       if k not in used1))
            q1p.setPos(X_MAN, y_q1); add(q1p)
            if k2:
                v2 = vals[1]
                y_q2 = ys[q_rows[v2]]
                used2 = ["T", "L", "R"] + (["B"] if len(q_groups[v2]) >= 2 else [])
                q2p = PartTee(tag=f"QUAD-{v2}", used=tuple(used2),
                              capped=tuple(k for k in ("L", "T", "B", "R")
                                           if k not in used2))
                q2p.setPos(X_MAN, y_q2); add(q2p)
                tj1 = pipe([(X_MAN, y_q1 + 14), (X_MAN, y_q2 - 14)], weight=5,
                           chip_side="left",
                           edit=(f"정션 QUAD{v1}→QUAD{v2} 구간 (조합 흐름)",
                                 "tj", v1, None))
                tj1._tj_seg = v1
                self.spine.append(tj1)
                y_run = y_q2
            else:
                y_run = y_q1
        elif n >= 2:
            for j in range(1, n):
                tee = PartTee(used=("L", "T", "B"))
                tee.setPos(X_MAN, ys[j]); add(tee)
                _seg = [(X_MAN, ys[j - 1]), (X_MAN, ys[j] - 14)]
                if j >= 2:
                    sp_pipe = pipe(_seg, weight=5, chip_side="left",
                                   edit=(f"정션 T{j-1}→T{j} 구간 (조합 흐름)",
                                         "tj", j - 1, None))
                else:
                    sp_pipe = pipe(_seg, weight=5)   # 최상단 단독강하 — tj 변수 없음
                self.spine.append(sp_pipe)

        # ── (quad: 마지막 크로스 R | legacy: 스파인 하단) → (N2) → (push) → reactor ──
        self.tj_out_pipe = None
        self.n2_parts = None
        self.push_parts = None
        x_cursor = X_MAN
        if quad:
            # tj[e_max]: 마지막 정션 → 가스T(N2 없으면 합류 하류) — 실제 파이프로 편집/칩
            _lbl_node = "가스T" if has_n2 else "합류 하류"
            self.tj_out_pipe = pipe(
                [(X_MAN + 14, y_run), (X_N2 - (14 if has_n2 else 0), y_run)],
                weight=5, chip_side="above",
                edit=(f"정션 QUAD{vals[-1]}→{_lbl_node} 구간 (조합 흐름)",
                      "tj", vals[-1], None))
            merge_pts = [(X_N2, y_run)]
        else:
            merge_pts = [(X_MAN, y_run)]
        if has_n2:
            if not quad:
                merge_pts.append((X_N2, y_run))
            tee_n2 = PartTee(used=("L", "R", "T"))
            tee_n2.setPos(X_N2, y_run); add(tee_n2)
            # @codesyncer-decision: N2 어셈블리를 합류부에서 바깥쪽(우상단)으로 —
            #   실린더가 커진(S 6.2) 뒤 티/리액터 사이에 끼어 답답(사용자 지적).
            #   MFC 는 티 위 96px, 실린더는 +96/위 190px 로 여유 확보(위 공간은
            #   채널 관이 X_MAN 왼쪽에만 있어 비어 있음).
            mfc = PartMfc(tag="MFC-01"); mfc.setPos(X_N2, y_run - 96); add(mfc)
            cyl = PartCylinder(tag="GC-N2"); cyl.setPos(X_N2 + 96, y_run - 190); add(cyl)
            g1 = pipe([cyl.port("out"), (cyl.port("out").x(), y_run - 96), mfc.port("in")],
                      weight=3.5)
            g2 = pipe([mfc.port("out"), (X_N2 - 30, y_run - 96), (X_N2 - 30, y_run - 30),
                       (X_N2, y_run - 30), (X_N2, y_run - 13)], weight=3.5)
            for g in (g1, g2):
                g.set_state(color="gas")
            self.n2_parts = (cyl, mfc, [g1, g2])
            x_cursor = X_N2
        if has_push and not quad:
            merge_pts.append((X_PUSH, y_run))
            tee_p = PartTee(used=("L", "R", "B"))
            tee_p.setPos(X_PUSH, y_run); add(tee_p)
            chk = PartCheck(tag=""); chk.setPos(X_PUSH, y_run + 56); add(chk)
            hp = PartHplc(tag="P-91"); hp.setPos(X_PUSH - 4, y_run + 132); add(hp)
            tank = PartBottle(tag="RSV-91", label="SOL")
            tank.setPos(X_PUSH - 128, y_run + 132); add(tank)
            q0 = pipe([tank.port("out"), hp.port("in")], weight=3.5)
            q1 = pipe([hp.port("out"), (X_PUSH + 56, y_run + 132), (X_PUSH + 56, y_run + 92),
                       (X_PUSH, y_run + 92), chk.port("in")], weight=3.5)
            q2 = pipe([chk.port("out"), (X_PUSH, y_run + 14)], weight=3.5)
            self.push_parts = (hp, [q0, q1, q2])
            x_cursor = X_PUSH
        elif has_push and quad:
            # @codesyncer(2026-08-12b): solvent 펌프는 QUAD-1 유입이 실배관 —
            #   런의 push 티 폐지. K2=우상단 코리도어(체크밸브 하향 flip)로 R 포트
            #   도킹, K1=기존 하단 어셈블리 배치에서 B 포트로 상승.
            if len(vals) == 2:
                y_s = ys[0] - 62                    # 그룹1 위 코리도어 레인
                x_r = X_MAN + 36                    # 라이저 (채널·N2 와 무교차 코리도어)
                x_hp = X_MAN + 230
                chk = PartCheck(tag="", flip=True)
                chk.setPos(x_r, (y_s + y_q1) / 2); add(chk)
                hp = PartHplc(tag="P-91"); hp.setPos(x_hp, y_s); add(hp)
                tank = PartBottle(tag="RSV-91", label="SOL")
                tank.setPos(x_hp - 128, y_s); add(tank)
                q0 = pipe([tank.port("out"), hp.port("in")], weight=3.5)
                q1 = pipe([hp.port("out"), (x_hp + 70, y_s), (x_hp + 70, y_s - 34),
                           (x_r, y_s - 34), chk.port("in")], weight=3.5)
                q2 = pipe([chk.port("out"), (x_r, y_q1), (X_MAN + 14, y_q1)],
                          weight=3.5)
            else:
                chk = PartCheck(tag=""); chk.setPos(X_MAN, y_run + 56); add(chk)
                hp = PartHplc(tag="P-91"); hp.setPos(X_MAN - 4, y_run + 132); add(hp)
                tank = PartBottle(tag="RSV-91", label="SOL")
                tank.setPos(X_MAN - 128, y_run + 132); add(tank)
                q0 = pipe([tank.port("out"), hp.port("in")], weight=3.5)
                q1 = pipe([hp.port("out"), (X_MAN + 56, y_run + 132),
                           (X_MAN + 56, y_run + 92), (X_MAN, y_run + 92),
                           chk.port("in")], weight=3.5)
                q2 = pipe([chk.port("out"), (X_MAN, y_run + 14)], weight=3.5)
            self.push_parts = (hp, [q0, q1, q2])

        rct = PartReactor(tag="R-01")
        rct.vol_ml = float(getattr(cfg, "reactor_vol", 0.0) or 0.0) or None
        rct.setPos(X_RCT, y_run); add(rct)
        self.reactor = rct
        rin = rct.port("in")
        merge_pts += [(rin.x() - 22, y_run), (rin.x() - 22, rin.y()), rin]
        self.merge_pipe = pipe(merge_pts, weight=5)

        rout = rct.port("out")
        y_out = rout.y()
        after = [rout]
        self.bpr = None
        if has_bpr:
            bpr = PartBpr(tag="BPR-01"); bpr.setPos(X_BPR, y_out); add(bpr)
            self.bpr = bpr
            after += [bpr.port("in")]
            self.post_pipe = pipe(after, weight=5)
            after2 = [bpr.port("out")]
        else:
            self.post_pipe = None
            after2 = [rout]

        out3 = PartOutlet3Way(tag="")   # 본체에 'Outlet' 캡션 有 — 코드태그 제거
        out3.setPos(X_OUT, y_out); add(out3)
        self.outlet = out3
        after2 += [out3.port("L")]
        self.out_in_pipe = pipe(after2, weight=5)

        if has_col:
            fc = PartCollector(tag="FC-01", plate=plate)
            fc.setPos(X_SINK + 10, y_out + 26); add(fc)
            self.collector_part = fc
            fin = fc.port("in")
            self.collect_pipe = pipe([out3.port("R"), (fin.x() - 16, y_out),
                                      (fin.x() - 16, fin.y()), fin], weight=4.5)
        else:
            # 분취기 미구성: COLLECT 출구는 개방 스텁
            self.collector_part = None
            self.collect_pipe = pipe([out3.port("R"), (X_OUT + 64, y_out)], weight=4.5)
        waste = PartBottle(tag="RSV-99", label="W", waste=True)
        waste.setPos(X_OUT, y_out + 96); add(waste)
        wb = out3.port("B")
        self.waste_pipe = pipe([wb, (wb.x(), waste.port("top").y() - 4)], weight=4.5)

        # ── 위상센서 글리프 (roles.phase 배정 시에만) ─────────────
        # collect    = 아웃렛 3-way 직전 (수집 경계 트리거 — 필수 1번 센서)
        # reactor_in = 리액터 입구, N2 티 하류 (슬러그 생성 QC + transit 실측 — 선택 2번)
        self.sensor_items = {}
        _ps = getattr(app, "phase_sensor", None)
        _keys = list(getattr(_ps, "sensors", {}) or {}) if _ps is not None else []
        if "collect" in _keys:
            s = PartPhaseSensor(tag="Collect Sensor")
            s.setPos(out3.port("L").x() - 34, y_out)
            add(s)
            self.sensor_items["collect"] = s
        if "reactor_in" in _keys:
            s = PartPhaseSensor(tag="Inlet Sensor")
            # 마지막 티(x_cursor)~리액터 도킹점 세그먼트 '중앙' — 티/체크밸브와 겹침 방지
            s.setPos((x_cursor + rin.x() - 22) / 2, y_run)
            add(s)
            self.sensor_items["reactor_in"] = s

        # phase 배너
        from PyQt5.QtWidgets import QGraphicsSimpleTextItem
        self.phase_text = QGraphicsSimpleTextItem("")
        self.phase_text.setFont(_f(12, bold=True))
        self.phase_text.setPos(X_SRC - 30, top - 44)
        add(self.phase_text)

        # ── 메인라인 데드볼륨 칩 (감사 F3): 합류→리액터 / 리액터→아웃렛 / 아웃렛→분취기
        #    채널·티정션 칩과 함께 '전 구간'이 배관도에서 보이고 편집 가능해짐.
        # @codesyncer(2026-08-12b): quad 모드의 tj[e_max]는 위에서 실제 파이프
        #   (self.tj_out_pipe)로 그려져 부유 칩이 필요 없다 — mixing 칩만
        #   가스T 하류(센서 글리프 회피 우측)에 남긴다.
        _x_end = rin.x() - 22
        if quad:
            _mix_cx = X_N2 + (_x_end - X_N2) * 0.72
            _mix_lbl = "가스T→센서→리액터 (mixing line)" if has_n2 \
                else "합류→리액터 (mixing line)"
        else:
            _mix_cx = (merge_pts[0][0] + merge_pts[1][0]) / 2
            _mix_lbl = "합류→리액터 (mixing line)"
        for _edit, _cx, _cy in (
                ((_mix_lbl, "sp", "mixing", None),
                 _mix_cx, y_run),
                (("리액터→아웃렛 (post)", "sp", "post", "post_reactor_vol_ml"),
                 rout.x() + 42, y_out),
                (("아웃렛→분취기 (collection line)", "sp", "collection",
                  "collection_line_vol_ml"),
                 out3.port("R").x() + 26, y_out)):
            _ch = VolChip(self, _edit)
            self._vol_chips.append(_ch)
            _ch.setPos(_cx, _cy - 14)
            add(_ch)

        # ── P2: 존 밴드(FEED/REACTION/COLLECTION) + 흐름 셰브론 + 칩 가시성 ──
        bbox = self._scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
        zx1, zx2 = X_MAN + 52, X_OUT - 52   # 스파인·tj칩은 FEED 존 소속
        for a, b, cap in ((bbox.left(), zx1, "FEED SUPPLY"),
                          (zx1, zx2, "REACTION"),
                          (zx2, bbox.right(), "COLLECTION")):
            add(ZoneBand(QRectF(a, bbox.top(), b - a, bbox.height()), cap))
        # 정적 흐름 방향 힌트 — 티/센서/칩 회피 지점 3곳 (quad 는 tj[e_max] 칩 회피
        # 위해 가스T 하류 merge 위에)
        _chev_pts = [((X_N2 + 34) if quad else (X_MAN + 68), y_run),
                     (rout.x() + 26, y_out)]
        if self.collector_part is not None:
            _fin = self.collector_part.port("in")
            _chev_pts.append(((out3.port("R").x() + _fin.x() - 16) / 2, y_out))
        else:
            _chev_pts.append((out3.port("R").x() + 30, y_out))
        for _cx, _cy in _chev_pts:
            _cv = FlowChevron()
            _cv.setPos(_cx, _cy)
            add(_cv)
        self._apply_chip_visibility()

        rect = self._scene.itemsBoundingRect().adjusted(-24, -24, 24, 24)
        self._scene.setSceneRect(rect)
        self.apply_theme(_DARK)

    # ── 실시간 ───────────────────────────────────────────────────
    def update_realtime(self, pumps_status, valves_status=None, selector_ports=None,
                        outlet_valve_pos=1, reactor_temp=0, collector_pos=None,
                        collector_well="", push_running=False,
                        sampler_vials=None, switcher_pos=None, gas_flowing=False,
                        sensor_phases=None):
        pumps_status = pumps_status or {}
        selector_ports = selector_ports or {}
        sampler_vials = sampler_vials or {}
        switcher_pos = switcher_pos or {}
        # 위상센서 도트 (액체=채움/기체=아웃라인/None=뮤트)
        for _k, _it in (getattr(self, "sensor_items", {}) or {}).items():
            _it.set_phase(None if sensor_phases is None else sensor_phases.get(_k))
        any_run = False
        _run_names = set()
        for name, part in self.items_["pumps"].items():
            st = pumps_status.get(name, {})
            if isinstance(st, bool):
                st = {"running": st}
            running = bool(st.get("running"))
            refilling = bool(st.get("refilling"))
            part.running, part.refilling = running, refilling
            part.update()
            any_run = any_run or running
            if running:
                _run_names.add(name)
            # 채널 본선(→매니폴드): 토출 시에만 흐름 (정방향)
            for pl in self.items_["ch_pipes"].get(name, []):
                pl.set_state(active=running, flowing=running,
                             direction=1, color="run")
            # 소스 라인(병/랙→밸브/펌프): 흡입 시에만, 소스→펌프 방향(+1)
            for pl in self.items_["src_pipes"].get(name, []):
                pl.set_state(active=refilling, flowing=refilling,
                             direction=1, color="fill")
            # 밸브↔펌프 공용관: 흡입 +1(밸브→펌프) / 토출 -1(펌프→밸브)
            for pl in self.items_["pump_link"].get(name, []):
                pl.set_state(active=running or refilling,
                             flowing=running or refilling,
                             direction=1 if refilling else -1,
                             color="fill" if refilling else "run")
            sel = self.items_["selectors"].get(name)
            if sel is not None:
                port = selector_ports.get(name, sel.port_num)
                sel.set_port(port)
                sel.active = running or refilling
                if self._inlet:
                    try:
                        info = self._inlet(name, int(port)) or {}
                        sel.reagent = info.get("name", "") or ""
                    except Exception:
                        pass
                sel.update()
            sw = self.items_["switchers"].get(name)
            if sw is not None:
                d = switcher_pos.get(name)
                if d is not None:
                    sw.set_direction(d)
                else:
                    sw.set_direction(1 if refilling else 2)
                sw.active = running or refilling
                sw.update()
            rack = self.items_["racks"].get(name)
            if rack is not None:
                v = sampler_vials.get(name)
                if v is not None:
                    rack.current = v
                rack.active = running or refilling
                rack.update()
            src = self.items_["sources"].get(name)
            if src is not None:
                src.active = running or refilling
                src.update()

        # quad: tj[k] 구간은 '구간 k 이전 진입' 채널(또는 QUAD-1 유입 solvent)이
        # 흐를 때만 — 엔진 F_j 누적유속 모델과 동일한 분절
        _quad = getattr(self, "_quad", False)
        for pl in getattr(self, "spine", []):
            if _quad:
                _seg = getattr(pl, "_tj_seg", 1)
                _fl = push_running or any(
                    self._entry_by_pump.get(nm, 1) <= _seg for nm in _run_names)
            else:
                _fl = any_run
            pl.set_state(active=_fl, flowing=_fl, direction=1, color="run")
        if getattr(self, "tj_out_pipe", None) is not None:
            _fl = any_run or push_running
            self.tj_out_pipe.set_state(active=_fl, flowing=_fl, direction=1,
                                       color="run")
        self.merge_pipe.set_state(active=any_run or push_running,
                                  flowing=any_run or push_running, direction=1, color="run")
        if self.post_pipe is not None:
            self.post_pipe.set_state(active=any_run or push_running,
                                     flowing=any_run or push_running, direction=1)
        self.out_in_pipe.set_state(active=any_run or push_running,
                                   flowing=any_run or push_running, direction=1)

        collect = (outlet_valve_pos == 2 or str(outlet_valve_pos).upper() == "COLLECT")
        self.outlet.collect = collect
        self.outlet.update()
        flowing_out = any_run or push_running
        self.collect_pipe.set_state(active=collect and flowing_out,
                                    flowing=collect and flowing_out, direction=1)
        self.waste_pipe.set_state(active=(not collect) and flowing_out,
                                  flowing=(not collect) and flowing_out, direction=1)

        self.reactor.temp = reactor_temp if reactor_temp else None
        self.reactor.update()
        # 질소 스페이서(HTE) 흐름 — N2 분지 + 합류하류 파이프 가스색
        if self.n2_parts is not None:
            for g in self.n2_parts[2]:
                g.set_state(active=gas_flowing, flowing=gas_flowing, direction=1, color="gas")
        if gas_flowing:
            self.merge_pipe.set_state(active=True, flowing=True, direction=1, color="gas")
            if self.post_pipe is not None:
                self.post_pipe.set_state(active=True, flowing=True, direction=1, color="gas")
            self.out_in_pipe.set_state(active=True, flowing=True, direction=1, color="gas")
        if self.push_parts is not None:
            hp, qs = self.push_parts
            hp.running = push_running
            hp.update()
            for q in qs:
                q.set_state(active=push_running, flowing=push_running, direction=1)
        if self.collector_part is not None:
            self.collector_part.pos = collector_pos
            self.collector_part.well = collector_well or ""
            self.collector_part.update()

    # ── 데드볼륨 편집 (더블클릭) ──────────────────────────────
    def _vol_cur(self, edit):
        title, kind, ident, key = edit
        cfg = self._cfg
        if cfg is None:
            return 0.0
        try:
            if kind == "pump":
                attr = {"tube_vol_inlet": "line_vol_inlet",
                        "tube_vol_valve_pump": "line_vol_valve_pump",
                        "tube_vol_switcher": "valve_internal_vol",
                        "tube_vol_selector": "selector_internal_vol",
                        "tube_vol_pump_merge": "line_vol_pump_merge"}[key]
                return float((getattr(cfg, attr, {}) or {}).get(ident, 0.0) or 0.0)
            if kind == "tj":
                return float((getattr(cfg, "tjunction_line_vols", {}) or {})
                             .get(int(ident), 0.0) or 0.0)
            if kind == "sp":
                if ident == "mixing":
                    return float(getattr(cfg, "mixing_line_dead_vol", 0.0) or 0.0)
                sp = (cfg.config_data.get("system_params", {}) or {})
                return float(sp.get(key, 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    def _vol_tip(self, edit):
        return (f"{edit[0]} 데드볼륨: {self._vol_cur(edit):.3f} mL\n"
                f"더블클릭으로 수정")

    def _edit_volume(self, pl):
        from PyQt5.QtWidgets import QInputDialog
        title, kind, ident, key = pl.edit
        cur = self._vol_cur(pl.edit)
        val, okd = QInputDialog.getDouble(
            self, "데드볼륨 편집", f"{title} (mL)", cur, 0.0, 500.0, 3)
        if not okd or self._cfg is None:
            return
        cd = self._cfg.config_data
        if kind == "pump":
            for pr in (cd.get("roles", {}) or {}).get("pumps", []):
                if pr.get("name") == ident:
                    pr.setdefault("settings", {})[key] = round(val, 4)
        elif kind == "tj":
            cd.setdefault("system_params", {}).setdefault(
                "tjunction_line_vols", {})[str(ident)] = round(val, 4)
        elif kind == "sp":
            spd = cd.setdefault("system_params", {})
            if ident == "mixing":
                # 파생값(id×len) 원본 보존 — mL 입력을 길이(cm)로 역산 (ID 유지)
                id_mm = float(spd.get("mixing_line_id_mm", 1.5) or 1.5)
                r_cm = (id_mm / 10.0) / 2.0
                spd["mixing_line_len_cm"] = round(val / (math.pi * r_cm ** 2), 2)
            else:
                spd[key] = round(val, 4)
        try:
            self._apply_chip_visibility()
            self._cfg.save_config(cd.get("inventory", []), cd.get("roles", {}),
                                  cd.get("system_params", {}))
        except Exception as e:
            print(f"[Diagram] 데드볼륨 저장 실패: {e}")
        # 장비/라인 변경과 동일 경로: 재빌드로 즉시 반영
        self.configure(self._cfg, self._app)

    def set_progress(self, phase_name, pct):
        if self.phase_text is None:
            return
        if not phase_name or pct is None or pct < 0:
            self.phase_text.setText("")
        else:
            self.phase_text.setText(f"▶ {phase_name}  {pct:.0f}%")
        self.phase_text.setBrush(QBrush(QColor(_pal().ACCENT_BLUE)))

    def set_detail(self, on):
        self.show_detail = bool(on)
        self._apply_chip_visibility()

    def _apply_chip_visibility(self):
        # 사용자 지적 반영: 칩은 항상 보임(미입력=+ 플레이스홀더 / 입력=수치 칩).
        # DETAIL 은 0 칩을 '0.00 풀칩'으로 바꿔 일괄 편집 모드가 됨.
        for ch in getattr(self, "_vol_chips", []):
            ch.setVisible(True)
            ch.update()

    def _style_detail_btn(self):
        P = _pal()
        self._btn_detail.setStyleSheet(
            f"QPushButton {{ background: {P.BG_SECONDARY}; color: {P.TEXT_SECONDARY}; "
            f"border: 1px solid {P.BORDER_SECONDARY}; border-radius: 9px; "
            f"padding: 2px 10px; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}"
            f"QPushButton:checked {{ color: {P.ACCENT_BLUE}; "
            f"border-color: {P.ACCENT_BLUE}; }}")

    def apply_theme(self, is_dark=True):
        global _DARK
        _DARK = bool(is_dark)
        P = _pal()
        self._scene.setBackgroundBrush(QBrush(QColor(P.BG_MONITOR)))
        if hasattr(self, "_btn_detail"):
            self._style_detail_btn()
        if self.phase_text is not None:
            self.phase_text.setBrush(QBrush(QColor(P.ACCENT_BLUE)))
        for it in self._scene.items():
            it.update()

    def _advance(self):
        vis = self.isVisible()
        for pl in self._all_pipes:
            if pl.flowing:
                pl.phase = (pl.phase + 2.2) % 1000.0
                if vis:
                    pl.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        if hasattr(self, "_btn_detail"):
            self._btn_detail.move(self.width() - self._btn_detail.width() - 10, 8)

    def showEvent(self, e):
        super().showEvent(e)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
