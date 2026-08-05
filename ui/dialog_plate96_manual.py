"""
Plate96 Manual Control Dialog — 수동 제어 전용
==============================================
Plate A + Plate B (각 8x12) 시각화. 각 well을 클릭해서 선택 -> "Go"로 이동.
다크/라이트 모드 팔레트 자동 반영.
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
                             QPushButton, QFrame, QMessageBox, QWidget)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

from ui.colors import DarkPalette as Dark, LightPalette as Light, T
from ui.theme import (get_primary_button_style, get_secondary_button_style,
                      get_main_control_button_style)


def _get_dark():
    # @codesyncer-decision: get_active_dark_mode 는 ui.colors 소유 —
    #   과거 ui.theme_manager 에서 import 해 항상 ImportError → 폴백 True
    #   (라이트 모드에서도 다크 팝업) 이었던 결함 수정
    try:
        from ui.colors import get_active_dark_mode as _f
        return _f()
    except Exception:
        return True


class _ClickableCell(QLabel):
    clicked = pyqtSignal(int, int)
    double_clicked = pyqtSignal(int, int)

    def __init__(self, row, col, parent=None):
        super().__init__(parent)
        self.row = row
        self.col = col
        self.setFixedSize(26, 26)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.row, self.col)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit(self.row, self.col)


class _ClickablePlateGrid(QFrame):
    well_clicked = pyqtSignal(str, int, int)
    well_double_clicked = pyqtSignal(str, int, int)

    def __init__(self, plate_name="A", is_dark=True, parent=None):
        super().__init__(parent)
        self.plate_name = plate_name
        self.is_dark = is_dark
        self.cells = {}
        self.current_well = None
        self.selected_well = None
        self._p = Dark if is_dark else Light
        self._init_ui()

    @property
    def _empty_color(self):
        return self._p.BG_TERTIARY

    @property
    def _border_color(self):
        return self._p.BORDER_PRIMARY

    @property
    def _text_color(self):
        return self._p.TEXT_PRIMARY

    def _init_ui(self):
        p = self._p
        self.setStyleSheet(
            f"_ClickablePlateGrid {{ background: {p.BG_SECONDARY}; "
            f"border: 1px solid {p.BORDER_PRIMARY}; border-radius: {T.R_LG}; }}"
        )
        root = QVBoxLayout(self)
        root.setSpacing(3)
        root.setContentsMargins(10, 8, 10, 10)

        title = QLabel(f"PLATE {self.plate_name}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {self._text_color}; font-weight: {T.FW_BOLD}; font-size: {T.FS_SM}; "
            f"letter-spacing: 1px; padding-bottom: 2px;"
        )
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(3)

        for c in range(12):
            lbl = QLabel(str(c + 1))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color: {p.TEXT_SECONDARY}; font-size: {T.FS_XS}; font-weight: {T.FW_SEMI};"
            )
            grid.addWidget(lbl, 0, c + 1)

        for r in range(8):
            rl = QLabel(chr(ord('A') + r))
            rl.setAlignment(Qt.AlignCenter)
            rl.setStyleSheet(
                f"color: {p.TEXT_SECONDARY}; font-size: {T.FS_XS}; font-weight: {T.FW_SEMI};"
            )
            grid.addWidget(rl, r + 1, 0)

            for c in range(12):
                cell = _ClickableCell(r, c)
                cell.setStyleSheet(self._cell_style(self._empty_color, hover=True))
                cell.setToolTip(f"{chr(ord('A') + r)}{c + 1}")
                cell.clicked.connect(self._on_cell_clicked)
                cell.double_clicked.connect(self._on_cell_dbl_clicked)
                grid.addWidget(cell, r + 1, c + 1)
                self.cells[(r, c)] = cell

        root.addLayout(grid)

    def _cell_style(self, color, border=None, border_width=1, hover=False):
        bd = border or self._border_color
        base = (f"background: {color}; border-radius: 13px; "
                f"border: {border_width}px solid {bd};")
        # 빈 웰만 호버 피드백(터치/마우스 조준) — 채워진 상태(현재/선택)는 그대로 유지
        if hover:
            return (f"QLabel {{ {base} }}"
                    f"QLabel:hover {{ background: {self._p.STATE_HOVER_INPUT}; "
                    f"border: 1px solid {self._p.ACCENT_BLUE}; }}")
        return base

    def _on_cell_clicked(self, r, c):
        self.well_clicked.emit(self.plate_name, r, c)

    def _on_cell_dbl_clicked(self, r, c):
        self.well_double_clicked.emit(self.plate_name, r, c)

    def set_current(self, row, col):
        prev = self.current_well
        self.current_well = (row, col) if row is not None else None
        if prev is not None:
            self._refresh_cell(*prev)
        if row is not None:
            self._refresh_cell(row, col)

    def set_selected(self, row, col):
        prev = self.selected_well
        self.selected_well = (row, col) if row is not None else None
        if prev is not None:
            self._refresh_cell(*prev)
        if row is not None:
            self._refresh_cell(row, col)

    def clear_selected(self):
        prev = self.selected_well
        self.selected_well = None
        if prev is not None:
            self._refresh_cell(*prev)

    def _refresh_cell(self, r, c):
        cell = self.cells.get((r, c))
        if not cell:
            return
        is_current = (r, c) == self.current_well
        is_selected = (r, c) == self.selected_well
        current_color = self._p.ACCENT_RED
        selected_color = self._p.ACCENT_BLUE
        if is_current and is_selected:
            cell.setStyleSheet(self._cell_style(
                current_color, border=selected_color, border_width=2))
        elif is_current:
            cell.setStyleSheet(self._cell_style(current_color))
        elif is_selected:
            cell.setStyleSheet(self._cell_style(
                selected_color, border=self._p.ACCENT_BLUE_DARK, border_width=2))
        else:
            cell.setStyleSheet(self._cell_style(self._empty_color, hover=True))


class Plate96ManualDialog(QDialog):
    """96-Well Manual Control Dialog — 다크/라이트 팔레트 자동 반영"""

    def __init__(self, collector, engine=None, parent=None):
        super().__init__(parent)
        self.collector = collector
        self.engine = engine
        self.selected = None
        self.is_dark = _get_dark()
        self._p = Dark if self.is_dark else Light

        self.setWindowTitle("96-Well Manual Control")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(840, 620)

        self._init_ui()
        self._refresh_current()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_current)
        self._poll_timer.start(800)

    def _init_ui(self):
        p = self._p
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SP_LG, T.SP_LG, T.SP_LG, T.SP_LG)
        root.setSpacing(T.SP_SM)
        self.setStyleSheet(
            f"QDialog {{ background: {p.BG_PRIMARY}; color: {p.TEXT_PRIMARY}; "
            f"font-family: {T.FONT}; }}"
            f"QLabel {{ color: {p.TEXT_PRIMARY}; font-family: {T.FONT}; }}"
        )

        # 상단: 현재 위치(강조) + 범례(현재/선택) + 연결상태
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(T.SP_MD)
        self.lbl_current = QLabel("현재 위치  —")
        self.lbl_current.setStyleSheet(
            f"font-size: {T.FS_MD}; font-weight: {T.FW_BOLD}; color: {p.TEXT_PRIMARY};")
        head.addWidget(self.lbl_current)
        head.addStretch()
        # 범례 — 현재(적)/선택(청) 색 의미 명시
        def _legend(color, text):
            w = QLabel(f"<span style='color:{color}'>●</span> "
                       f"<span style='color:{p.TEXT_SECONDARY}'>{text}</span>")
            w.setStyleSheet(f"font-size: {T.FS_XS};")
            return w
        head.addWidget(_legend(p.ACCENT_RED, "현재"))
        head.addWidget(_legend(p.ACCENT_BLUE, "선택"))
        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet(
            f"font-size: {T.FS_XS}; color: {p.TEXT_SECONDARY}; padding-left: 6px;")
        head.addWidget(self.lbl_status)
        root.addLayout(head)

        # Wash 구역 — 상단 (실제 지그: wash reservoir가 plate 위)
        wash_frame = QFrame()
        wash_frame.setFrameStyle(QFrame.Box | QFrame.Sunken)
        wash_frame.setStyleSheet(
            f"background: {p.BG_SECONDARY}; "
            f"border: 1px solid {p.BORDER_PRIMARY}; border-radius: {T.R_LG};"
        )
        wash_frame.setFixedHeight(44)
        wash_hl = QHBoxLayout(wash_frame)
        wash_hl.setContentsMargins(T.SP_MD, 4, T.SP_MD, 4)
        wash_hl.setSpacing(T.SP_SM)
        lbl_wash_title = QLabel("Wash Reservoir")
        lbl_wash_title.setStyleSheet(
            f"color: {p.TEXT_SECONDARY}; font-size: {T.FS_XS};")
        wash_hl.addWidget(lbl_wash_title)
        wash_hl.addStretch()
        self.btn_wash_dlg = QPushButton("Wash 이동")
        self.btn_wash_dlg.setMinimumHeight(30)
        self.btn_wash_dlg.setCursor(Qt.PointingHandCursor)
        self.btn_wash_dlg.setStyleSheet(get_secondary_button_style(p))
        self.btn_wash_dlg.clicked.connect(self._go_wash)
        wash_hl.addWidget(self.btn_wash_dlg)
        root.addWidget(wash_frame)

        # Plate 그리드
        plates = QHBoxLayout()
        plates.setSpacing(T.SP_SM)
        self.grid_a = _ClickablePlateGrid("A", is_dark=self.is_dark)
        self.grid_b = _ClickablePlateGrid("B", is_dark=self.is_dark)
        for g in (self.grid_a, self.grid_b):
            g.well_clicked.connect(self._on_well_clicked)
            g.well_double_clicked.connect(self._on_well_double_clicked)
        plates.addWidget(self.grid_a, 1)
        plates.addWidget(self.grid_b, 1)
        root.addLayout(plates)

        # 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(T.SP_SM)

        self.btn_go = QPushButton("이동")
        self.btn_go.setMinimumHeight(T.H_BTN_LG)
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setStyleSheet(get_primary_button_style(p))
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._go_selected)

        self.btn_home = QPushButton("원점 (G28)")
        self.btn_home.setMinimumHeight(T.H_BTN_LG)
        self.btn_home.setCursor(Qt.PointingHandCursor)
        self.btn_home.setStyleSheet(get_secondary_button_style(p))
        self.btn_home.clicked.connect(self._home)

        self.btn_stop = QPushButton("비상 정지")
        # ISO 13850: E-Stop 은 최대 타겟 + 적색 본체/황색 링 — 일반 버튼과
        # 시각적으로 구분돼야 사고 시 조준 없이 찾을 수 있음
        self.btn_stop.setMinimumHeight(T.H_BTN_XL)
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setStyleSheet(
            get_main_control_button_style(self._p, "emergency"))
        self.btn_stop.clicked.connect(self._stop)

        btn_close = QPushButton("닫기")
        btn_close.setMinimumHeight(T.H_BTN_LG)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(get_secondary_button_style(p))
        btn_close.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_go, 2)
        btn_row.addWidget(self.btn_home, 1)
        btn_row.addWidget(self.btn_stop, 1)
        btn_row.addStretch()
        btn_row.addWidget(btn_close, 1)
        root.addLayout(btn_row)
        root.addStretch(1)

    # ─── safety ───
    def _is_safe_to_move(self) -> bool:
        if not self.collector or not getattr(self.collector, "is_connected", False):
            QMessageBox.warning(self, "미연결", "분취기가 연결되지 않았습니다.")
            return False
        if self.engine and getattr(self.engine, "running", False):
            QMessageBox.warning(self, "시퀀스 실행 중",
                                "자동 시퀀스가 진행 중입니다.\n시퀀스를 먼저 중지하세요.")
            return False
        return True

    def _update_enabled_state(self):
        connected = bool(self.collector and getattr(self.collector, "is_connected", False))
        seq_running = bool(self.engine and getattr(self.engine, "running", False))
        can_move = connected and not seq_running
        self.btn_home.setEnabled(can_move)
        self.btn_stop.setEnabled(connected)
        self.btn_go.setEnabled(can_move and self.selected is not None)
        if not connected:
            self.lbl_status.setText("미연결")
        elif seq_running:
            self.lbl_status.setText("시퀀스 실행 중")
        else:
            self.lbl_status.setText("연결됨")

    # ─── interactions ───
    def _on_well_clicked(self, plate, row, col):
        if plate == "A":
            self.grid_b.clear_selected()
            self.grid_a.set_selected(row, col)
        else:
            self.grid_a.clear_selected()
            self.grid_b.set_selected(row, col)
        self.selected = (plate, row, col)
        self._update_enabled_state()

    def _on_well_double_clicked(self, plate, row, col):
        self._on_well_clicked(plate, row, col)
        self._go_selected()

    def _go_selected(self):
        if not self.selected or not self._is_safe_to_move():
            return
        plate, row, col = self.selected
        well_name = f"{chr(ord('A') + row)}{col + 1}"
        try:
            ok, msg = self.collector.move_to_well(plate, well_name)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"이동 실패: {e}")
            return
        if not ok:
            QMessageBox.warning(self, "이동 실패", str(msg))
        self._refresh_current()

    def _go_wash(self):
        if not self._is_safe_to_move():
            return
        if not hasattr(self.collector, 'move_to_wash'):
            QMessageBox.warning(self, "미지원", "move_to_wash 미지원 드라이버")
            return
        try:
            ok, msg = self.collector.move_to_wash()
        except Exception as e:
            QMessageBox.critical(self, "오류", f"Wash 이동 실패: {e}")
            return
        if not ok:
            QMessageBox.warning(self, "이동 실패", str(msg))
        self._refresh_current()

    def _home(self):
        if not self._is_safe_to_move():
            return
        ret = QMessageBox.question(
            self, "호밍 확인",
            "G28 호밍은 최대 60초가 소요됩니다. 계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            self.collector.home()
        except Exception as e:
            QMessageBox.critical(self, "오류", str(e))
        self._refresh_current()

    def _stop(self):
        if not self.collector or not getattr(self.collector, "is_connected", False):
            QMessageBox.warning(self, "미연결", "분취기가 연결되지 않았습니다.")
            return
        ret = QMessageBox.question(
            self, "비상 정지",
            "M112 비상 정지는 Marlin을 halt 상태로 만듭니다.\n"
            "이후 재연결 전까지 모든 이동 명령이 무시됩니다.\n계속하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            self.collector.stop_motion()
        except Exception as e:
            QMessageBox.critical(self, "오류", str(e))

    def _refresh_current(self):
        self._update_enabled_state()
        p = self._p
        if not self.collector:
            return
        try:
            pos = self.collector.get_position()
        except Exception:
            pos = 0

        if pos == 0 or not hasattr(self.collector, "get_well_id"):
            self.lbl_current.setText("현재 위치: HOME (0)")
            self.lbl_current.setStyleSheet(
                f"font-size: {T.FS_SM}; color: {p.ACCENT_GREEN};")
            self.grid_a.set_current(None, None)
            self.grid_b.set_current(None, None)
            return

        try:
            wid = self.collector.get_well_id(pos)
        except Exception:
            wid = "?"
        if wid in ("?", "HOME"):
            self.lbl_current.setText(f"현재 위치: Tube {pos}")
            self.grid_a.set_current(None, None)
            self.grid_b.set_current(None, None)
            return

        try:
            plate, well = wid.split("_")
            row = ord(well[0]) - ord('A')
            col = int(well[1:]) - 1
        except Exception:
            self.lbl_current.setText(f"현재 위치: {wid} (Tube {pos})")
            return

        self.lbl_current.setText(f"현재 위치: {wid}  (Tube {pos})")
        self.lbl_current.setStyleSheet(
            f"font-size: {T.FS_SM}; color: {p.ACCENT_BLUE};")
        if plate == "A":
            self.grid_a.set_current(row, col)
            self.grid_b.set_current(None, None)
        else:
            self.grid_b.set_current(row, col)
            self.grid_a.set_current(None, None)

    def closeEvent(self, e):
        if self._poll_timer:
            self._poll_timer.stop()
        super().closeEvent(e)
