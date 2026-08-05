"""
Plate96 Fluid Tracker — 96-well 시퀀스 분취 미리보기
====================================================
Manual Control(dialog_plate96_manual.py)과 동일한 PyQt well grid UI 기반.
각 step의 분주 well 그룹을 색상으로 구분. matplotlib 의존 없음.
"""
import math
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QFrame, QGridLayout, QWidget, QScrollArea)
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF

from ui.colors import DarkPalette, LightPalette, T


def get_active_dark_mode():
    try:
        from ui.theme_manager import get_active_dark_mode as _f
        return _f()
    except Exception:
        return True


class _PreviewPlateGrid(QFrame):
    """읽기 전용 8x12 Plate grid — step별 색상 시각화"""

    CELL_SIZE = 24

    def __init__(self, plate_name="A", is_dark=True, parent=None):
        super().__init__(parent)
        self.plate_name = plate_name
        self.is_dark = is_dark
        self._p = DarkPalette if is_dark else LightPalette
        self.cells = {}
        self.path_segments = []
        self._init_ui()

    def _init_ui(self):
        p = self._p
        self.setFrameStyle(QFrame.Box | QFrame.Sunken)
        self.setStyleSheet(
            f"background: {p.BG_PRIMARY}; "
            f"border: 1px solid {p.BORDER_PRIMARY}; border-radius: 8px;"
        )

        root = QVBoxLayout(self)
        root.setSpacing(2)
        root.setContentsMargins(6, 6, 6, 6)

        title = QLabel(f"Plate {self.plate_name}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {p.TEXT_PRIMARY}; font-weight: {T.FW_BOLD}; font-size: {T.FS_SM};")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(2)

        for c in range(12):
            lbl = QLabel(str(c + 1))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color: {p.TEXT_PRIMARY}; font-size: {T.FS_XS}; font-weight: {T.FW_BOLD};")
            grid.addWidget(lbl, 0, c + 1)

        for r in range(8):
            rl = QLabel(chr(ord('A') + r))
            rl.setAlignment(Qt.AlignCenter)
            rl.setStyleSheet(
                f"color: {p.TEXT_PRIMARY}; font-size: {T.FS_SM}; font-weight: {T.FW_BOLD};")
            grid.addWidget(rl, r + 1, 0)

            for c in range(12):
                cell = QLabel()
                cell.setFixedSize(self.CELL_SIZE, self.CELL_SIZE)
                cell.setAlignment(Qt.AlignCenter)
                cell.setStyleSheet(self._cell_style(p.BG_TERTIARY))
                cell.setToolTip(f"{chr(ord('A') + r)}{c + 1}")
                grid.addWidget(cell, r + 1, c + 1)
                self.cells[(r, c)] = cell

        root.addLayout(grid)

    def _cell_style(self, color, border=None):
        bd = border or self._p.BORDER_PRIMARY
        return (
            f"background: {color}; border-radius: {self.CELL_SIZE // 2}px; "
            f"border: 1px solid {bd};"
        )

    def set_well_color(self, row, col, color, text="", border=None):
        cell = self.cells.get((row, col))
        if not cell:
            return
        style = self._cell_style(color, border)
        if text:
            style += " color: white; font-size: 7px; font-weight: bold;"
        cell.setStyleSheet(style)
        if text:
            cell.setText(text)

    def add_path(self, color_str, well_coords):
        """화살표 경로 추가. well_coords: [(row, col), ...]"""
        self.path_segments.append((color_str, list(well_coords)))
        self.update()

    def _cell_center(self, row, col):
        """cell 위젯의 중심점을 self(QFrame) 좌표계로 변환"""
        cell = self.cells.get((row, col))
        if not cell:
            return None
        return cell.mapTo(self, cell.rect().center())

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.path_segments:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        for color_str, coords in self.path_segments:
            if len(coords) < 2:
                continue
            points = []
            for r, c in coords:
                pt = self._cell_center(r, c)
                if pt:
                    points.append(QPointF(pt.x(), pt.y()))
            if len(points) < 2:
                continue

            pen = QPen(QColor(color_str))
            pen.setWidthF(1.8)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])

            # 화살촉: 6 well마다 + 마지막 well에
            arrow_size = 4.0
            for i in range(len(points) - 1):
                if (i + 1) % 6 != 0 and i != len(points) - 2:
                    continue
                p1 = points[i]
                p2 = points[i + 1]
                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                length = math.hypot(dx, dy)
                if length < 1.0:
                    continue
                ux, uy = dx / length, dy / length
                # 화살촉 위치: 선분의 중간
                mx = (p1.x() + p2.x()) / 2
                my = (p1.y() + p2.y()) / 2
                # 삼각형 3점
                tip = QPointF(mx + ux * arrow_size, my + uy * arrow_size)
                left = QPointF(mx - uy * arrow_size * 0.5 - ux * arrow_size * 0.3,
                               my + ux * arrow_size * 0.5 - uy * arrow_size * 0.3)
                right = QPointF(mx + uy * arrow_size * 0.5 - ux * arrow_size * 0.3,
                                my - ux * arrow_size * 0.5 - uy * arrow_size * 0.3)
                painter.setBrush(QColor(color_str))
                painter.drawPolygon(QPolygonF([tip, left, right]))
                painter.setBrush(Qt.NoBrush)

        painter.end()

    def set_start_marker(self, row, col):
        cell = self.cells.get((row, col))
        if not cell:
            return
        p = self._p
        cell.setStyleSheet(
            self._cell_style(p.ACCENT_GREEN, border=p.ACCENT_GREEN_DARK)
            + f" color: white; font-size: 9px; font-weight: {T.FW_BOLD};"
        )
        cell.setText("▶")


class Plate96PreviewDialog(QDialog):
    """96-well 시퀀스 분취 미리보기 — PyQt grid 기반 (Manual UI와 동일 스타일)"""

    STEP_COLORS = [
        "#4a90d9", "#e8943a", "#50c878", "#e05555",
        "#9d7fd9", "#d9a03a", "#3ac4d9", "#d94a8a",
        "#7fd97f", "#d9d93a",
    ]

    def __init__(self, start_tube, step_infos, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle("Fluid Tracker — 96-Well Plate")
        self.resize(820, 620)

        self.start_tube = max(1, start_tube)
        self.step_infos = step_infos
        self.is_dark = get_active_dark_mode()
        self._init_ui()

    @staticmethod
    def snake_index_to_well(idx):
        if not (1 <= idx <= 192):
            return None
        plate = "A" if idx <= 96 else "B"
        local = (idx - 1) % 96
        row = local // 12
        if row % 2 == 0:
            col = local % 12
        else:
            col = 11 - (local % 12)
        return plate, row, col

    def _init_ui(self):
        P = DarkPalette if self.is_dark else LightPalette

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SP_LG, T.SP_LG, T.SP_LG, T.SP_LG)
        root.setSpacing(T.SP_MD)
        self.setStyleSheet(
            f"QDialog {{ background: {P.BG_PRIMARY}; color: {P.TEXT_PRIMARY}; }}"
            f"QLabel {{ color: {P.TEXT_PRIMARY}; }}"
        )

        # 타이틀
        title = QLabel("Fluid Tracker")
        title.setStyleSheet(
            f"font-size: {T.FS_MD}; font-weight: {T.FW_BOLD};")
        root.addWidget(title)

        # 시작 위치 — 플레이트의 ▶ 마커 의미를 여기서 정의 (범례-인-캡션 핵 제거)
        sw = self.snake_index_to_well(self.start_tube)
        if sw:
            start_id = f"{sw[0]}_{chr(ord('A') + sw[1])}{sw[2] + 1}"
        else:
            start_id = f"Tube {self.start_tube}"
        lbl_start = QLabel(
            f"<span style='color:{P.ACCENT_GREEN};'>▶</span>"
            f"<span style='color:{P.TEXT_SECONDARY};'> 시작</span>&nbsp; "
            f"<b>{start_id}</b>"
            f"<span style='color:{P.TEXT_SECONDARY};'> · Tube {self.start_tube}</span>")
        lbl_start.setStyleSheet(f"font-size: {T.FS_SM};")
        root.addWidget(lbl_start)

        # Plate 그리드
        plates_row = QHBoxLayout()
        plates_row.setSpacing(T.SP_MD)
        self.grid_a = _PreviewPlateGrid("A", is_dark=self.is_dark)
        self.grid_b = _PreviewPlateGrid("B", is_dark=self.is_dark)
        plates_row.addWidget(self.grid_a, 1)
        plates_row.addWidget(self.grid_b, 1)
        root.addLayout(plates_row)

        # Well 색상 채우기
        self._fill_wells()

        # 범례
        self._build_legend(root, P)

        # 표기규격: 화살표(→) 금지 — 경로는 플레이트 위 선으로 이미 시각화됨.
        info = QLabel("서펜타인 채움 · 플레이트 2 × 96 = 192 wells")
        info.setStyleSheet(f"font-size: {T.FS_XS}; color: {P.TEXT_SECONDARY};")
        root.addWidget(info)

    def _fill_wells(self):
        """step_infos 기반 well 색상 + 경로 화살표 채우기"""
        cur_idx = self.start_tube

        # START 마커
        sw = self.snake_index_to_well(self.start_tube)
        if sw:
            grid = self.grid_a if sw[0] == "A" else self.grid_b
            grid.set_start_marker(sw[1], sw[2])

        for step_i, info in enumerate(self.step_infos):
            color = self.STEP_COLORS[step_i % len(self.STEP_COLORS)]
            n = info.get("num_tubes", 0)

            # Plate별 경로 좌표 수집 (Plate 전환 시 segment 분리)
            path_a = []
            path_b = []

            for k in range(n):
                snake_idx = cur_idx + k
                if snake_idx > 192:
                    break
                w = self.snake_index_to_well(snake_idx)
                if not w:
                    continue
                plate, row, col = w
                grid = self.grid_a if plate == "A" else self.grid_b
                text = str(step_i + 1) if k == 0 else ""
                grid.set_well_color(row, col, color, text=text)

                if plate == "A":
                    path_a.append((row, col))
                else:
                    path_b.append((row, col))

            # 경로 화살표 추가 (2 well 이상인 segment만)
            if len(path_a) > 1:
                self.grid_a.add_path(color, path_a)
            if len(path_b) > 1:
                self.grid_b.add_path(color, path_b)

            cur_idx += n + info.get("wash_tubes", 0)

    def _build_legend(self, layout, P):
        legend_frame = QFrame()
        legend_frame.setObjectName("legendFrame")
        # @codesyncer(수정 2026-07-13): 무선택자 stylesheet 는 자식 QLabel 까지 border 를
        #   상속시켜 범례가 '상자 속 상자'(행마다 pill + ● 자리 빈 사각형)로 렌더되던
        #   버그 — objectName 스코프로 프레임에만 적용, 자식은 명시 무테.
        legend_frame.setStyleSheet(
            f"QFrame#legendFrame {{ background: {P.BG_SECONDARY}; "
            f"border: 1px solid {P.BORDER_PRIMARY}; border-radius: {T.R_LG}; }}"
            f"QFrame#legendFrame QLabel {{ border: none; background: transparent; }}"
            f"QFrame#legendFrame QWidget {{ border: none; }}")
        legend_l = QVBoxLayout(legend_frame)
        legend_l.setContentsMargins(T.SP_MD, T.SP_SM, T.SP_MD, T.SP_SM)
        legend_l.setSpacing(4)

        cur_idx = self.start_tube
        for i, info in enumerate(self.step_infos):
            color = self.STEP_COLORS[i % len(self.STEP_COLORS)]
            n = info.get("num_tubes", 0)

            wells_used = []
            for k in range(n):
                idx = cur_idx + k
                if idx > 192:
                    break
                w = self.snake_index_to_well(idx)
                if w:
                    wells_used.append(f"{w[0]}_{chr(ord('A') + w[1])}{w[2] + 1}")

            row = QHBoxLayout()
            sw = QLabel("●")
            sw.setStyleSheet(f"color: {color}; font-size: 16px;")
            sw.setFixedWidth(20)
            row.addWidget(sw)

            # 표기규격 정합: 이름(강조) · 메트릭(저채도 · 구분) · 웰 범위(모노, en-dash)
            #   기존 산문형 "Step — n wells (x mL/well, total y mL) [A → B]" 는
            #   괄호/대시/화살표 뭉침 — 계측기 위계(값 강조·단위 저채도)로 재구성.
            mut = f"color:{P.TEXT_SECONDARY};"
            text = (
                f"<b>{info.get('name', 'Step')}</b>&nbsp;&nbsp;"
                f"<span style='{mut}'>{n} wells · "
                f"{info.get('vol_per_tube', 0):.2f} mL/well · "
                f"총 {info.get('total_vol', 0):.2f} mL</span>"
            )
            if wells_used:
                rng = (wells_used[0] if len(wells_used) == 1
                       else f"{wells_used[0]}–{wells_used[-1]}")
                text += (f"&nbsp;&nbsp;<span style='font-family:{T.FONT_MONO}; "
                         f"font-weight:{T.FW_SEMI};'>{rng}</span>")
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {P.TEXT_PRIMARY}; font-size: {T.FS_XS};")
            row.addWidget(lbl, 1)

            w_container = QWidget()
            w_container.setLayout(row)
            legend_l.addWidget(w_container)

            cur_idx += n + info.get("wash_tubes", 0)

        if cur_idx - 1 > 192:
            warn = QLabel(f"⚠ {cur_idx - 1 - 192} wells overflow (192 well 한계 초과)")
            warn.setStyleSheet("color: #f85149; font-weight: bold;")
            legend_l.addWidget(warn)

        layout.addWidget(legend_frame)
