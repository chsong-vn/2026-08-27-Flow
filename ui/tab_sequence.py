"""
Sequence Tab - StepCard 기반 UI 재설계
@codesyncer-decision: 인간공학 개선 (3-tab → Card 통합)
- 좌측 (25%): 시약 설정 QTabWidget (Group A, B, C) - 농도 참조 전용
- 우측 (75%): StepCard 스크롤 (한 카드에 입력+결과 통합)

@codesyncer-decision: StepCard 통합 구조
- 각 Step이 하나의 카드에 온도/체류시간/반응량/분획부피 + 포트/당량 + 결과 모두 포함
- 탭 전환 없이 한 눈에 Step 전체 파악 가능
- 포트 ComboBox에 시약명+농도(M) 인라인 표시

@codesyncer-context: 중앙 집중식 데이터 모델 (변경 없음)
- self.sequence_data: 모든 step 데이터 저장
- StepCard.data가 sequence_data[i]를 직접 참조 → dict 기반 동기화
"""

import json
import math
import os
import traceback

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QComboBox, QDoubleSpinBox, QSpinBox, QScrollArea,
                             QPushButton, QProgressBar, QSplitter, QLabel,
                             QMessageBox, QTabWidget, QFrame, QGridLayout,
                             QCheckBox, QApplication, QShortcut,
                             QAbstractSpinBox, QLineEdit, QSizePolicy, QStackedWidget)
from PyQt5.QtCore import Qt, QTimer, QEvent
from PyQt5.QtGui import QKeySequence
from PyQt5.QtGui import QColor

from core.worker import SequenceWorker
from ui.colors import DarkPalette as Dark, LightPalette as Light, T


def _rnum(v):
    """숫자 파싱 (실패 시 None) — 그리드 농도 컬럼용."""
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


class _ReagentCell:
    """QTableWidgetItem 최소 호환 프록시 — 어댑터 캐시 (r,c) 를 가리킨다."""
    __slots__ = ("_ad", "_r", "_c")

    def __init__(self, ad, r, c):
        self._ad = ad
        self._r = r
        self._c = c

    def text(self):
        return self._ad._cells[self._r][self._c]

    def setText(self, s):
        self._ad._set(self._r, self._c, "" if s is None else str(s))

    def setFlags(self, *a, **k):
        pass

    def flags(self):
        return 0


class _ReagentGridAdapter:
    """WebGrid(공유 시약 그리드)에 대한 QTableWidget 최소 호환 어댑터.

    @codesyncer-decision: 시약표를 WebGrid 로 교체하되, 외부(method_io ·
      reagent_excel · 계산기 Import)가 pump 별 12×4 표(.item/.setItem/.rowCount/
      .blockSignals)로 계속 접근하도록 이 어댑터를 reagent_tables[pump] 에 둔다.
      → 블래스트 반경을 시퀀스 탭 내부로 봉쇄(외부 3파일 무수정).
    col: 0=포트라벨(RO) · 1=시약명 · 2=농도 · 3=SMILES,  row=포트-1(0..11).
    map_mgr 가 단일 진실원: 포트 2~11 편집 시 update_inlet 동기화."""

    def __init__(self, seq, pump, gi):
        self._seq = seq
        self._pump = pump
        self._gi = gi
        self._blocked = False
        self._cells = [list(seq._reagent_cell_defaults(pump, r + 1)) for r in range(12)]

    def rowCount(self):
        return 12

    def columnCount(self):
        return 4

    def item(self, r, c):
        if 0 <= r < 12 and 0 <= c < 4:
            return _ReagentCell(self, r, c)
        return None

    def setItem(self, r, c, item):
        if item is None:
            return
        self._set(r, c, item.text())

    def blockSignals(self, b):
        was = self._blocked
        self._blocked = bool(b)
        if was and not b:
            self._flush()
        return was

    def installEventFilter(self, *a, **k):
        pass

    def _set(self, r, c, val):
        if not (0 <= r < 12 and 0 <= c < 4):
            return
        self._cells[r][c] = val
        # 포트 2~11 편집 → map 동기화 (포트 1/12 예약행은 표시만)
        if 0 < r < 11 and c in (1, 2, 3) and self._seq.app.map_mgr:
            name = self._cells[r][1] or "Empty"
            conc = _rnum(self._cells[r][2])
            conc = 1.0 if conc is None else conc
            smiles = (self._cells[r][3] or "").strip()
            try:
                self._seq.app.map_mgr.update_inlet(self._pump, r + 1, name, conc, smiles)
            except Exception:
                pass
        if not self._blocked:
            self._flush()

    def _flush(self):
        self._seq._push_reagent_rows(self._pump, self._gi, self._cells)


class StepCard(QFrame):
    """단일 Step의 모든 입력/결과를 하나의 카드에 통합 표시

    @codesyncer-decision: QGridLayout 기반 정렬
    - 조건행: 4쌍(라벨+스핀박스) 고정폭 그리드 → 카드 간 정렬 보장
    - 펌프행: 6열 그리드 (이름, 포트콤보, ×, 당량, →, 유속)
    - 섹션 간 구분선(HLine)으로 시각적 분리
    """

    def __init__(self, step_num, step_data, cfg, map_mgr, parent_tab):
        super().__init__()
        self.data = step_data  # sequence_data[i] dict 직접 참조
        self.parent_tab = parent_tab
        self.cfg = cfg
        self.map_mgr = map_mgr
        self.pump_widgets = {}  # {pump_name: {'cb_port', 'sp_eq', 'lbl_flow'}}
        self._selected = False
        self._separators = []

        # @codesyncer-decision: 네이티브 Box 프레임 제거 — QSS 보더와 이중으로
        #   그려져 카드가 무거워 보였음. QSS 단일 보더 + 넉넉한 여백(8px 그리드).
        self.setFrameStyle(QFrame.NoFrame)
        self.setCursor(Qt.PointingHandCursor)
        self._quiet_labels = []
        self._apply_normal_style()

        main = QVBoxLayout()
        main.setContentsMargins(16, 12, 16, 12)
        main.setSpacing(0)

        # ── Header: Step 번호 + 요약 + 실행상태 배지 ──
        # @codesyncer-decision: 헤더에 핵심 조건 요약 + 실행상태 배지 추가 (가시성 개선)
        # - 스크롤 스캔 시 카드를 펼쳐보지 않아도 "Step 2 · 80℃ · 10min · 5mL" 즉시 파악
        # - 실행 중 RUNNING/DONE 배지로 현재 진행 위치를 카드 자체에 표시
        #   (기존: 진행률 바 숫자만으로는 어느 카드가 실행 중인지 알 수 없었음)
        header = QHBoxLayout()
        header.setSpacing(8)

        self.step_label = QLabel(f"Step {step_num}")
        self._style_step_label()
        header.addWidget(self.step_label)

        self.lbl_summary = QLabel("")
        self._style_summary_label()
        header.addWidget(self.lbl_summary)
        header.addStretch()

        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet(f"font-size: {T.FS_SM}; font-weight: {T.FW_BOLD};")
        self.lbl_state.setVisible(False)
        header.addWidget(self.lbl_state)

        main.addLayout(header)
        main.addSpacing(4)
        self._run_state = "idle"

        # ── Section 1: 반응 조건 (2×4 Grid) ──
        self.sp_temp = self._make_spinbox(0, 200, step_data['temp'], " \u2103", 1)
        self.sp_temp.setToolTip("원하는 반응 온도를 설정하세요.\n히터가 이 온도에 도달하면 실험이 시작됩니다.")
        self.sp_rt = self._make_spinbox(0.1, 999, step_data['rt'], " min", 1)
        self.sp_rt.setToolTip("시약이 반응기 안에 머무르는 시간입니다.\n이 시간 동안 반응이 진행됩니다.")
        self.sp_vol = self._make_spinbox(0.1, 999, step_data['vol'], " mL", 1)
        self.sp_vol.setToolTip("이번 Step에서 주입할 총 부피입니다.\n각 펌프의 당량 비율에 따라 자동 분배됩니다.")
        self.sp_tube = self._make_spinbox(0.1, 50, step_data['tube_vol'], " mL", 1)
        self.sp_tube.setToolTip("수집 튜브 하나에 담을 부피입니다.\n예: 반응부피 3mL ÷ 분획 1.5mL = 튜브 2개 사용")

        cond_grid = QGridLayout()
        cond_grid.setHorizontalSpacing(4)
        cond_grid.setVerticalSpacing(3)

        cond_items = [
            ("반응온도:", self.sp_temp),
            ("반응시간:", self.sp_rt),
            ("반응부피:", self.sp_vol),
            ("분획부피:", self.sp_tube),
        ]
        for idx, (lbl_text, sp) in enumerate(cond_items):
            row, col = idx // 2, (idx % 2) * 2
            lbl = QLabel(lbl_text)
            lbl.setFixedWidth(T.W_LABEL_COND)
            lbl.setStyleSheet(f"font-size: {T.FS_SM};")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cond_grid.addWidget(lbl, row, col)
            cond_grid.addWidget(sp, row, col + 1)
        cond_grid.setColumnStretch(4, 1)  # 빈 5열이 나머지 공간 흡수 → 좌측 정렬

        main.addLayout(cond_grid)

        # 콜백 연결 (data dict 직접 수정)
        self.sp_temp.valueChanged.connect(lambda v: self._update_data('temp', v))
        self.sp_rt.valueChanged.connect(lambda v: self._update_data('rt', v))
        self.sp_vol.valueChanged.connect(lambda v: self._update_data('vol', v))
        self.sp_tube.valueChanged.connect(lambda v: self._update_data('tube_vol', v))

        # ── Separator ──
        main.addSpacing(5)
        main.addWidget(self._make_separator())
        main.addSpacing(5)

        # ── Section 2: 펌프 설정 (QGridLayout N×6) ──
        pump_grid = QGridLayout()
        pump_grid.setHorizontalSpacing(8)
        pump_grid.setVerticalSpacing(6)

        inlet_pumps = list(getattr(cfg, "INLET_PUMPS", []) or [])
        for row_idx, pump_name in enumerate(inlet_pumps):
            pump_data = step_data['pumps'].get(pump_name, {'port': 2, 'eq': 1.0, 'flow': 0.0})

            # Col 0: 펌프 라벨
            pump_lbl = QLabel(f"{pump_name}:")
            pump_lbl.setFixedWidth(82)
            pump_lbl.setStyleSheet(f"font-weight: {T.FW_BOLD}; font-size: {T.FS_SM};")
            pump_grid.addWidget(pump_lbl, row_idx, 0)

            # Col 1: 소스 셀 — 라우팅 모드별 분기 (cfg.PUMP_ROUTING 이 유일한 분기 키)
            # @codesyncer-decision: external_valve = 기존 Port 1~12 콤보.
            #   internal_valve = 선택지가 없으므로(1펌프=1소스) 고정 라벨 + port=1 자동
            #   고정 — 엔진 검증(1~12 필수)을 통과시키고 NRG 어댑터는 port 를 무시.
            #   콤보를 남겨두면 "포트를 고를 수 있다"는 오조작을 유도하므로 라벨로 교체.
            #   autosampler = per-step vial 콤보 (니들 조율 v0 — Phase A/B).
            _routing = getattr(cfg, "PUMP_ROUTING", {}).get(pump_name, "external_valve")
            cb_vial = None   # autosampler 라우팅에서만 생성
            if _routing == "external_valve":
                cb_port = QComboBox()
                cb_port.setMinimumWidth(176)
                cb_port.setMinimumHeight(T.H_INPUT_SM)
                for port_idx in range(1, 13):
                    info = map_mgr.get_inlet(pump_name, port_idx) if map_mgr else {'name': '', 'conc': 0.0}
                    name = info.get('name', '')
                    is_empty = not name or name in ('Empty', '비어있음', '-')
                    conc = info.get('conc', 0.0)
                    conc_str = f" ({conc}M)" if (conc > 0 and not is_empty) else ""
                    label = f"Port {port_idx} :" if is_empty else f"Port {port_idx} : {name}{conc_str}"
                    cb_port.addItem(label, port_idx)
                port_idx_to_select = cb_port.findData(pump_data['port'])
                if port_idx_to_select >= 0:
                    cb_port.setCurrentIndex(port_idx_to_select)
                cb_port.currentIndexChanged.connect(
                    lambda _, p=pump_name, cb=cb_port: self._update_pump_port(p, cb.currentData())
                )
                pump_grid.addWidget(cb_port, row_idx, 1)
                lbl_source = None
            elif _routing == "autosampler":
                # @codesyncer-decision: Phase A/B — per-step vial 선택기.
                #   소스 선택 = 니들 이동이므로 포트 콤보 대신 vial 콤보.
                #   port 는 엔진 검증(1~12) 통과용 1 고정, 실 소스는 data['vial'].
                cb_port = None
                lbl_source = None
                if pump_name in self.data.get('pumps', {}):
                    self.data['pumps'][pump_name]['port'] = 1
                cb_vial = QComboBox()
                cb_vial.setMinimumWidth(176)
                cb_vial.setMinimumHeight(T.H_INPUT_SM)
                vials = (parent_tab.vials_for(pump_name)
                         if hasattr(parent_tab, "vials_for") else ["A1"])
                for v in vials:
                    info = map_mgr.get_inlet(pump_name, v) if map_mgr else {}
                    name = (info or {}).get('name', '')
                    known = name and name not in ('Empty', '비어있음', '-', '알 수 없음')
                    cb_vial.addItem(f"Vial {v} : {name}" if known else f"Vial {v}", v)
                cur_vial = pump_data.get('vial') or (vials[0] if vials else "A1")
                self.data['pumps'][pump_name]['vial'] = cur_vial
                v_idx = cb_vial.findData(cur_vial)
                if v_idx >= 0:
                    cb_vial.setCurrentIndex(v_idx)
                cb_vial.currentIndexChanged.connect(
                    lambda _, p=pump_name, cb=cb_vial: self._update_pump_vial(p, cb.currentData())
                )
                cb_vial.setToolTip("니들이 이 바이알로 이동해 흡입합니다 (오토샘플러)")
                pump_grid.addWidget(cb_vial, row_idx, 1)
            else:
                cb_port = None
                if pump_name in self.data.get('pumps', {}):
                    self.data['pumps'][pump_name]['port'] = 1
                lbl_source = QLabel(self._source_label_text(pump_name, _routing))
                lbl_source.setMinimumWidth(176)
                lbl_source.setMinimumHeight(T.H_INPUT_SM)
                lbl_source.setToolTip("NRG 내장 밸브 — 이 펌프의 전용 소스에서 흡인 (포트 선택 없음)")
                self._quiet_labels.append(lbl_source)
                pump_grid.addWidget(lbl_source, row_idx, 1)

            # Col 2: ×
            # \uad6c '\u00d7' \uae00\ub9ac\ud504 \u2014 \uacc4\uce21\uae30 UI \ud45c\uae30\uc5d0 \uc548 \ub9de\uc544 \uacf5\ubc31 \uc2a4\ud398\uc774\uc11c\ub85c (\uc0ac\uc6a9\uc790 \ud53c\ub4dc\ubc31)
            x_lbl = QLabel("")
            x_lbl.setFixedWidth(10)
            x_lbl.setAlignment(Qt.AlignCenter)
            self._quiet_labels.append(x_lbl)
            pump_grid.addWidget(x_lbl, row_idx, 2)

            # Col 3: 당량 스핀박스
            sp_eq = QDoubleSpinBox()
            sp_eq.setRange(0.01, 99)
            sp_eq.setValue(pump_data['eq'])
            sp_eq.setDecimals(2)
            sp_eq.setSuffix(" eq")
            sp_eq.setMinimumHeight(T.H_INPUT_SM)
            sp_eq.setFixedWidth(T.W_INPUT_SM)
            sp_eq.valueChanged.connect(
                lambda v, p=pump_name: self._update_pump_eq(p, v)
            )
            pump_grid.addWidget(sp_eq, row_idx, 3)

            # Col 4: →
            # \uad6c '\u2192' \uae00\ub9ac\ud504 \u2014 \uacc4\uce21\uae30 UI \ud45c\uae30\uc5d0 \uc548 \ub9de\uc544 \uacf5\ubc31 \uc2a4\ud398\uc774\uc11c\ub85c (\uc0ac\uc6a9\uc790 \ud53c\ub4dc\ubc31)
            arrow_lbl = QLabel("")
            arrow_lbl.setFixedWidth(10)
            arrow_lbl.setAlignment(Qt.AlignCenter)
            self._quiet_labels.append(arrow_lbl)
            pump_grid.addWidget(arrow_lbl, row_idx, 4)

            # Col 5: 유속 결과
            lbl_flow = QLabel("- mL/min")
            lbl_flow.setFixedWidth(120)
            lbl_flow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._quiet_labels.append(lbl_flow)
            pump_grid.addWidget(lbl_flow, row_idx, 5)

            self.pump_widgets[pump_name] = {
                'cb_port': cb_port,          # external_valve 외 라우팅이면 None
                'cb_vial': cb_vial,          # autosampler 전용 vial 콤보 (없으면 None)
                'lbl_source': lbl_source,    # 고정 소스 라벨 (없으면 None)
                'sp_eq': sp_eq,
                'lbl_flow': lbl_flow,
            }

        pump_grid.setColumnStretch(1, 1)  # 콤보 컬럼이 남은 공간 차지
        main.addLayout(pump_grid)

        # ── Separator ──
        main.addSpacing(5)
        main.addWidget(self._make_separator())
        main.addSpacing(4)

        # ── Section 3: 계산 결과 (Footer) ──
        footer = QHBoxLayout()
        footer.setSpacing(24)

        self.lbl_total_flow = QLabel(self._stat_html("\ucd1d \uc720\uc18d", "\u2013"))
        self.lbl_total_flow.setStyleSheet(f"font-weight: {T.FW_BOLD}; font-family: {T.FONT_MONO}; font-size: {T.FS_SM};")
        footer.addWidget(self.lbl_total_flow)

        self.lbl_est_time = QLabel(self._stat_html("\uc608\uc0c1 \uc2dc\uac04", "\u2013"))
        self.lbl_est_time.setStyleSheet(f"font-weight: {T.FW_SEMI}; font-family: {T.FONT_MONO}; font-size: {T.FS_SM};")
        footer.addWidget(self.lbl_est_time)

        self.lbl_num_tubes = QLabel(self._stat_html("분획 튜브", "–"))
        self.lbl_num_tubes.setStyleSheet(f"font-weight: {T.FW_SEMI}; font-family: {T.FONT_MONO}; font-size: {T.FS_SM};")
        footer.addWidget(self.lbl_num_tubes)

        # @codesyncer-decision: 튜브 배치 범위를 카드에 인라인 표시 (가시성 개선)
        # - 기존: Fluid Tracker 다이얼로그를 열어야만 어느 튜브에 분취되는지 확인 가능
        # - 수정: "→ Tube 5–8 (+W9)" 형태로 footer에 누적 배치 즉시 표시
        self.lbl_tube_range = QLabel("")
        self.lbl_tube_range.setStyleSheet(f"font-weight: {T.FW_SEMI}; font-family: {T.FONT_MONO}; font-size: {T.FS_SM};")
        footer.addWidget(self.lbl_tube_range)

        footer.addStretch()
        main.addLayout(footer)

        self.setLayout(main)

        # @codesyncer-decision: 카드 내 입력 위젯 편집 시 카드 자동 선택 (인간공학 개선)
        # - 기존: 카드 빈 영역 클릭만 선택으로 인정 → 사용자가 spinbox를 편집하던
        #   카드와 "선택된" 카드가 달라서 삭제/복사/이동이 엉뚱한 카드에 적용
        # - 수정: 자식 입력 위젯 FocusIn 이벤트를 감지해 해당 카드를 선택
        self._install_focus_select()
        self._apply_quiet_styles()

    def _install_focus_select(self):
        widgets = [self.sp_temp, self.sp_rt, self.sp_vol, self.sp_tube]
        for pw in self.pump_widgets.values():
            widgets.extend(w for w in (pw['cb_port'], pw['sp_eq']) if w is not None)
        for w in widgets:
            w.installEventFilter(self)
            # 스핀박스는 내부 QLineEdit이 실제 포커스를 받으므로 함께 설치
            le = getattr(w, "lineEdit", None)
            if callable(le):
                try:
                    inner = w.lineEdit()
                    if inner is not None:
                        inner.installEventFilter(self)
                except Exception:
                    pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            if not self._selected:
                self.parent_tab.select_card(self)
        return super().eventFilter(obj, event)

    def update_header_summary(self):
        """헤더 요약 갱신: 온도 · 시간 · 부피"""
        d = self.data
        # @codesyncer(\ud45c\uae30 \uaddc\uaca9): \uacc4\uce21\uae30 \uad00\ub840 \u2014 \uac12\u00b7\ub2e8\uc704 \uc0ac\uc774 \uacf5\ubc31, "\u00b0C" \ud45c\uc900 \ud45c\uae30
        #   ("25\u2103\u00b710.0min" \ubd99\uc5ec\uc4f0\uae30\uac00 \ube44\uc804\ubb38\uc801\uc774\ub77c\ub294 \uc0ac\uc6a9\uc790 \ud53c\ub4dc\ubc31)
        self.lbl_summary.setText(
            f"{d['temp']:.1f} \u00b0C \u00b7 {d['rt']:.1f} min \u00b7 {d['vol']:.1f} mL"
        )

    def _style_summary_label(self):
        P = self._get_palette()
        self.lbl_summary.setStyleSheet(
            f"font-size: {T.FS_SM}; font-family: {T.FONT_MONO}; color: {P.TEXT_SECONDARY};"
        )

    def _chip_css(self, fg, bg):
        """ISA-101 상태 칩: 틴트 BG + 의미색 텍스트 + 1px 보더 (WCAG 대비 확보)"""
        return (
            f"font-size: {T.FS_SM}; font-weight: {T.FW_BOLD}; "
            f"color: {fg}; background: {bg}; border: 1px solid {fg}; "
            f"border-radius: {T.R_SM}; padding: 2px 10px; "
            f"min-height: {T.H_CHIP}px;"
        )

    def set_run_state(self, state: str):
        """실행 상태 배지: idle / running / done / error

        @codesyncer-decision: IEC 60073 의미색 — 녹=가동, 적=오류 전용.
        칩 패턴(틴트 BG + 의미색 텍스트)으로 양 테마에서 WCAG 대비 충족.
        """
        self._run_state = state
        P = self._get_palette()
        if state == "running":
            self.lbl_state.setText("\u25b6 RUNNING")
            self.lbl_state.setStyleSheet(self._chip_css(P.STATE_RUN, P.STATE_RUN_BG))
            self.lbl_state.setVisible(True)
        elif state == "done":
            self.lbl_state.setText("\u2713 DONE")
            self.lbl_state.setStyleSheet(self._chip_css(P.STATE_RUN, "transparent"))
            self.lbl_state.setVisible(True)
        elif state == "error":
            self.lbl_state.setText("\u2715 ERROR")
            self.lbl_state.setStyleSheet(self._chip_css(P.STATE_FAULT, P.STATE_FAULT_BG))
            self.lbl_state.setVisible(True)
        else:
            self.lbl_state.setText("")
            self.lbl_state.setVisible(False)

    def mark_stale(self):
        """입력 변경 후 결과가 아직 재계산되지 않았음을 시각화 (stale 결과 함정 방지)

        @codesyncer-decision: IEC 60073 황색(주의) — 미계산 상태는 '주의' 의미색.
        """
        P = self._get_palette()
        stale_css = (
            f"font-family: {T.FONT_MONO}; font-size: {T.FS_SM}; "
            f"color: {P.STATE_WARN}; font-style: italic;"
        )
        self.lbl_total_flow.setStyleSheet(stale_css)
        self.lbl_total_flow.setText(self._stat_html("총 유속", "재계산 중\u2026"))
        self.lbl_est_time.setStyleSheet(stale_css)
        self.lbl_num_tubes.setStyleSheet(stale_css)
        self.lbl_tube_range.setStyleSheet(stale_css)

    def _make_spinbox(self, min_val, max_val, default, suffix, decimals):
        sp = QDoubleSpinBox()
        sp.setRange(min_val, max_val)
        sp.setValue(default)
        sp.setSuffix(suffix)
        sp.setDecimals(decimals)
        # @codesyncer-decision: 밀집 입력 표준 크기 — 26px는 WCAG 2.2
        #   최소 타겟(24px)에 근접한 한계치였음 → H_INPUT_SM(28) + 8px 그리드 폭
        sp.setMinimumHeight(T.H_INPUT_SM)
        sp.setFixedWidth(T.W_INPUT_SM)
        return sp

    def _make_separator(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.NoFrame)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {self._get_palette().SEPARATOR};")
        self._separators.append(sep)
        return sep

    def _get_palette(self):
        is_dark = getattr(self.parent_tab.app, 'is_dark_mode', True)
        return Dark if is_dark else Light

    def _update_data(self, key, value):
        self.data[key] = value
        self.parent_tab._mark_flow_dirty(f"{key}_changed")

    def _update_pump_port(self, pump_name, port):
        if port is not None:
            self.data['pumps'][pump_name]['port'] = port
            self.parent_tab._mark_flow_dirty("port_changed")

    def _update_pump_vial(self, pump_name, vial):
        """autosampler 소스 vial 변경 — 농도가 바뀔 수 있으므로 유속 재계산 유도."""
        if vial:
            self.data['pumps'][pump_name]['vial'] = str(vial)
            self.parent_tab._mark_flow_dirty("vial_changed")

    def _update_pump_eq(self, pump_name, value):
        self.data['pumps'][pump_name]['eq'] = value
        self.parent_tab._mark_flow_dirty("equiv_changed")

    def set_step_num(self, num):
        self.step_label.setText(f"Step {num}")

    def _style_step_label(self):
        P = self._get_palette()
        self.step_label.setStyleSheet(
            f"font-size: {T.FS_LG}; font-weight: {T.FW_BOLD}; color: {P.ACCENT_BLUE};"
        )

    def _apply_quiet_styles(self):
        """글리프(×, →)·유속 라벨을 저채도로 — 카드의 시각 노이즈 감소"""
        P = self._get_palette()
        for lbl in self._quiet_labels:
            lbl.setStyleSheet(
                f"color: {P.TEXT_SECONDARY}; font-family: {T.FONT_MONO}; "
                f"font-size: {T.FS_XS};"
            )

    def set_selected(self, selected):
        self._selected = selected
        if selected:
            P = self._get_palette()
            # @codesyncer-decision: 선택 카드 = 보더 + 좌측 4px 액센트 바
            # 보더 폭은 normal과 동일하게 고정 → 선택/호버 시 레이아웃 흔들림 없음
            self.setStyleSheet(f"""
                StepCard {{
                    border: 1px solid {P.ACCENT_BLUE};
                    border-left: 4px solid {P.ACCENT_BLUE};
                    border-radius: {T.R_LG};
                    padding: 4px;
                    background: {P.BG_SECONDARY};
                }}
            """)
        else:
            self._apply_normal_style()

    def _apply_normal_style(self):
        P = self._get_palette()
        # @codesyncer-decision: 보더 폭 상수 유지 (1px + 좌측 4px 슬롯)
        # - 기존: hover 시 1px→2px로 보더가 굵어져 카드 내용물이 1px씩 밀림(jitter)
        # - 수정: 폭은 고정, hover는 보더 '색'만 변화 → 부드러운 인터랙션
        self.setStyleSheet(f"""
            StepCard {{
                border: 1px solid {P.BORDER_SECONDARY};
                border-left: 4px solid {P.BORDER_SECONDARY};
                border-radius: {T.R_LG};
                padding: 4px;
                background: {P.BG_SECONDARY};
            }}
            StepCard:hover {{
                border-color: {P.ACCENT_GRAY};
            }}
        """)

    def mousePressEvent(self, event):
        self.parent_tab.select_card(self)
        super().mousePressEvent(event)

    @staticmethod
    def _stat_html(label, value, unit=""):
        """계측기 표기 규격: 라벨(작은 UI서체) + 값(굵게) + 단위(작게).
        @codesyncer(표기 규격): "총유속: 0.300 mL/min" 콜론 나열이 비전문적이라는
          사용자 피드백 — 라벨/값/단위 3단 위계로 통일. 색은 라벨 stylesheet 상속."""
        u = (f' <span style="font-size:12px; font-weight:600;">{unit}</span>'
             if unit else "")
        return (f'<span style="font-size:12px; font-weight:600; font-family:{T.FONT};">'
                f'{label}</span>&nbsp; '
                f'<span style="font-size:14px; font-weight:700;">{value}</span>{u}')

    def update_results(self, flows, total_flow, est_time, num_tubes, tube_range=None, over_capacity=False):
        """계산 결과를 카드에 표시 (stale 표시 해제 + 튜브 범위 포함)"""
        for pump_name, flow_val in flows.items():
            if pump_name in self.pump_widgets:
                self.pump_widgets[pump_name]['lbl_flow'].setText(f"{flow_val:.3f} mL/min")

        P = self._get_palette()
        normal_bold = (f"font-weight: {T.FW_BOLD}; font-family: {T.FONT_MONO}; "
                       f"font-size: {T.FS_SM}; color: {P.ACCENT_BLUE};")
        normal_semi = (f"font-weight: {T.FW_SEMI}; font-family: {T.FONT_MONO}; "
                       f"font-size: {T.FS_SM}; color: {P.TEXT_SECONDARY};")

        self.lbl_total_flow.setStyleSheet(normal_bold)
        self.lbl_total_flow.setText(self._stat_html("총 유속", f"{total_flow:.3f}", "mL/min"))
        self.lbl_est_time.setStyleSheet(normal_semi)
        self.lbl_est_time.setText(self._stat_html("예상 시간", f"{est_time:.1f}", "min"))
        self.lbl_num_tubes.setStyleSheet(normal_semi)
        self.lbl_num_tubes.setText(self._stat_html("분획 튜브", f"{num_tubes}"))

        if tube_range:
            if over_capacity:
                # collector 용량 초과 → IEC 60073 적색 = 오류 (실행 전 사전 인지)
                self.lbl_tube_range.setStyleSheet(
                    f"font-weight: {T.FW_BOLD}; font-family: {T.FONT_MONO}; "
                    f"font-size: {T.FS_SM}; color: {P.STATE_FAULT};"
                )
                self.lbl_tube_range.setText(f"\u26a0 {tube_range} (용량 초과!)")
            else:
                self.lbl_tube_range.setStyleSheet(normal_semi)
                self.lbl_tube_range.setText(self._stat_html("\uc218\uc9d1 \uc704\uce58", tube_range))
        else:
            self.lbl_tube_range.setText("")

        self.update_header_summary()

    def clear_results(self):
        """결과 초기화"""
        for pw in self.pump_widgets.values():
            pw['lbl_flow'].setText("- mL/min")
        P = self._get_palette()
        normal_bold = (f"font-weight: {T.FW_BOLD}; font-family: {T.FONT_MONO}; "
                       f"font-size: {T.FS_SM}; color: {P.ACCENT_BLUE};")
        normal_semi = (f"font-weight: {T.FW_SEMI}; font-family: {T.FONT_MONO}; "
                       f"font-size: {T.FS_SM}; color: {P.TEXT_SECONDARY};")
        self.lbl_total_flow.setStyleSheet(normal_bold)
        self.lbl_total_flow.setText(self._stat_html("총 유속", "–"))
        self.lbl_est_time.setStyleSheet(normal_semi)
        self.lbl_est_time.setText(self._stat_html("예상 시간", "–"))
        self.lbl_num_tubes.setStyleSheet(normal_semi)
        self.lbl_num_tubes.setText(self._stat_html("분획 튜브", "–"))
        self.lbl_tube_range.setStyleSheet(normal_semi)
        self.lbl_tube_range.setText("")

    def _source_label_text(self, pump_name, routing):
        """비-external 라우팅의 고정 소스 라벨 텍스트 (시약맵 슬롯 1 재사용)."""
        info = self.map_mgr.get_inlet(pump_name, 1) if self.map_mgr else {'name': '', 'conc': 0.0}
        name = info.get('name', '')
        is_empty = not name or name in ('Empty', '비어있음', '-')
        conc = info.get('conc', 0.0)
        conc_str = f" ({conc}M)" if (conc > 0 and not is_empty) else ""
        src = "(미지정)" if is_empty else f"{name}{conc_str}"
        # autosampler 는 이제 vial 콤보를 사용 — 이 라벨은 internal_valve 전용
        return f"전용 소스 : {src}"

    def refresh_port_combos(self, pump_name=None):
        """포트 콤보박스 항목 갱신 (시약명+농도 인라인). 고정 라벨 라우팅은 텍스트만 갱신."""
        pump_list = [pump_name] if pump_name else list(self.pump_widgets.keys())
        for p in pump_list:
            if p not in self.pump_widgets:
                continue
            cb = self.pump_widgets[p]['cb_port']
            if cb is None:
                # vial 콤보 라벨 갱신 (시약맵 변경 반영, 선택 vial 유지)
                cbv = self.pump_widgets[p].get('cb_vial')
                if cbv is not None:
                    cur = cbv.currentData()
                    cbv.blockSignals(True)
                    for i in range(cbv.count()):
                        v = cbv.itemData(i)
                        info = self.map_mgr.get_inlet(p, v) if self.map_mgr else {}
                        name = (info or {}).get('name', '')
                        known = name and name not in ('Empty', '비어있음', '-', '알 수 없음')
                        cbv.setItemText(i, f"Vial {v} : {name}" if known else f"Vial {v}")
                    cbv.blockSignals(False)
                lbl = self.pump_widgets[p].get('lbl_source')
                if lbl is not None:
                    _routing = getattr(self.cfg, "PUMP_ROUTING", {}).get(p, "external_valve")
                    lbl.setText(self._source_label_text(p, _routing))
                continue
            current_port = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            for port_idx in range(1, 13):
                info = self.map_mgr.get_inlet(p, port_idx) if self.map_mgr else {'name': '', 'conc': 0.0}
                name = info.get('name', '')
                is_empty = not name or name in ('Empty', '비어있음', '-')
                conc = info.get('conc', 0.0)
                conc_str = f" ({conc}M)" if (conc > 0 and not is_empty) else ""
                label = f"Port {port_idx} :" if is_empty else f"Port {port_idx} : {name}{conc_str}"
                cb.addItem(label, port_idx)
            idx = cb.findData(current_port)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)


class SequenceTab(QWidget):
    """시퀀스 탭: StepCard 기반 2-column 레이아웃 (시약 참조 + Step 카드)"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.reagent_tables = {}

        # @codesyncer-decision: 중앙 데이터 모델 (구조 변경 없음)
        # 각 step은 dict: {temp, rt, vol, tube_vol, pumps: {pump_name: {port, eq, flow}}}
        self.sequence_data = []

        # StepCard 리스트 (3개 테이블 대체)
        self.step_cards = []
        self.card_layout = None
        self._selected_card = None
        self.flow_dirty = True
        self.flow_dirty_reasons = set()

        # @codesyncer-decision: 입력 변경 → 300ms 디바운스 자동 재계산 (인간공학 개선)
        # - 기존: 입력 변경 후 '유속 계산' 버튼을 눌러야만 결과 갱신
        #   → 버튼을 잊으면 stale 결과가 화면에 남은 채 실행 판단
        # - 계산은 순수 산술(하드웨어 미접촉)이라 자동 실행 부담 없음
        # - 연속 타이핑 중 매 키마다 재계산하지 않도록 300ms 디바운스
        self._recalc_timer = QTimer(self)
        self._recalc_timer.setSingleShot(True)
        self._recalc_timer.setInterval(300)
        self._recalc_timer.timeout.connect(lambda: self.calc_flow(silent=True))

        self.setup_ui()

    def setup_ui(self):
        """2-column 레이아웃 구성 (시약 참조 + Step 카드 스크롤)"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 상단 버튼 바
        top_bar = self.create_top_button_bar()
        layout.addLayout(top_bar)

        # 메인 2-column splitter
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setHandleWidth(4)
        main_splitter.setChildrenCollapsible(False)

        # 좌측: 시약 설정 QTabWidget (농도 참조 전용)
        reagent_tabs = self.create_reagent_tabs()
        reagent_tabs.setMinimumWidth(220)
        main_splitter.addWidget(reagent_tabs)

        # 우측: StepCard 스크롤 영역 / 스텝 테이블 뷰 스택
        card_area = self._create_card_area()
        card_area.setMinimumWidth(705)
        self.card_area = card_area
        # @codesyncer-decision: 하이브리드 뷰 — 카드(상세·시각화) + 테이블(HTE 대량편집/조망)
        #   토글. 둘 다 sequence_data 를 편집(단일 진실원). 그리드는 최초 토글 시 지연 생성.
        self.view_stack = QStackedWidget()
        self.view_stack.setMinimumWidth(705)
        self.view_stack.addWidget(card_area)   # index 0 = 카드 뷰
        self.steps_grid = None
        self._steps_syncing = False
        main_splitter.addWidget(self.view_stack)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 6)

        # 여분 세로 공간은 콘텐츠(스플리터)가 전부 — 툴바로 새지 않게 stretch 1
        layout.addWidget(main_splitter, 1)

        # 하단 진행률 바
        self.prog = QProgressBar()
        self.prog.setFixedHeight(28)
        self.prog.setFormat("진행률: %p%")
        layout.addWidget(self.prog)

        # 키보드 단축키 — StepCard 영역으로 스코핑.
        # @codesyncer-decision: 시약 그리드(QWebEngineView)는 Chromium이 Ctrl+C/V/
        #   Delete 를 자체 처리하므로, 창 전역(WindowShortcut) 단축키가 그리드 클립보드를
        #   가로채지 않도록 card_area(우측 StepCard) 위젯+자식으로 컨텍스트를 좁힌다.
        for seq_str, slot in (("Ctrl+V", self.paste_from_excel),
                              ("Ctrl+C", self.copy_to_clipboard),
                              ("Delete", self.delete_step)):
            sc = QShortcut(QKeySequence(seq_str), self.card_area)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        # Well ID 라벨 초기 표시
        self._update_well_id_label()

        # 첫 번째 Step 추가
        self.add_step()

    def _create_card_area(self):
        """우측: StepCard 스크롤 영역 생성"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        self.card_layout = QVBoxLayout(container)
        self.card_layout.setContentsMargins(6, 36, 6, 6)
        self.card_layout.setSpacing(10)
        self.card_layout.addStretch()  # 하단 여백

        scroll.setWidget(container)
        return scroll

    def _mark_flow_dirty(self, reason=""):
        self.flow_dirty = True
        if reason:
            self.flow_dirty_reasons.add(str(reason))
        # stale 결과 시각화 + 디바운스 자동 재계산 예약
        for card in self.step_cards:
            try:
                card.mark_stale()
                card.update_header_summary()
            except Exception:
                pass
        if hasattr(self, "_recalc_timer"):
            self._recalc_timer.start()

    def _clear_flow_dirty(self):
        self.flow_dirty = False
        self.flow_dirty_reasons.clear()

    # ─── Top Button Bar (그룹핑 적용) ─────────────────────────

    def create_top_button_bar(self):
        """상단 버튼 바 생성 (기능별 그룹 분리)

        @codesyncer-decision: 버튼 시각 위계 3단계 (심플/클린 디자인 원칙)
        - Primary(채움): 실험 시작 1개만 — 화면의 단일 주행동
        - Secondary(아웃라인): 유속 계산, Fluid Tracker
        - Ghost(텍스트): 편집/Step 조작 8개 — 기존엔 11개 버튼이 전부
          같은 무게의 회색 박스라 어디를 봐야 할지 알 수 없었음
        """
        # @codesyncer-decision: 툴바 전문화(디자인만, 버튼/시그널/순서 불변) —
        #   페이지에 흩어져 보이던 고스트 버튼들을 ①카드 스트립(QFrame#SeqToolbar)
        #   에 담고 ②기능군을 세그먼트 트랙(QFrame#SegTrack)으로 묶음. Manual 탭
        #   세그먼트 컨트롤과 동일한 시각 언어(트랙+pill hover)로 통일.
        bar = QHBoxLayout()
        bar.setSpacing(0)
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("SeqToolbar")
        # @codesyncer-risk: QFrame 은 세로 Preferred 라 부모의 여분 높이를 먹고
        #   트랙이 거대해짐(사용자 스크린샷) — 툴바는 항상 '내용 높이' 고정.
        self.toolbar_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tl = QHBoxLayout(self.toolbar_frame)
        tl.setContentsMargins(10, 6, 10, 6)
        tl.setSpacing(8)
        # @codesyncer(칸잘림 근본수정): 툴바 버튼 13개가 한 줄이라 minSizeHint≈2035px →
        #   page_stack 전체 최소폭을 그만큼 밀어, 모든 탭(계산기 포함)이 좁은 창에서
        #   가로 오버플로우 → 마지막 칸 잘림. 툴바를 가로 스크롤 래퍼에 넣어 콘텐츠 폭이
        #   페이지 최소폭을 강제하지 않게(창 좁으면 툴바만 가로스크롤, 넓으면 그대로).
        self._toolbar_scroll = QScrollArea()
        self._toolbar_scroll.setObjectName("SeqToolbarScroll")
        self._toolbar_scroll.setWidget(self.toolbar_frame)
        self._toolbar_scroll.setWidgetResizable(True)
        self._toolbar_scroll.setFrameShape(QFrame.NoFrame)
        self._toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._toolbar_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toolbar_scroll.setFixedHeight(60)   # 버튼(36) + 마진 + 스크롤바 여유
        bar.addWidget(self._toolbar_scroll)
        self._ghost_btns = []
        self._secondary_btns = []
        self._toolbar_tracks = []

        def _track():
            fr = QFrame()
            fr.setObjectName("SegTrack")
            h = QHBoxLayout(fr)
            h.setContentsMargins(3, 3, 3, 3)
            h.setSpacing(2)
            self._toolbar_tracks.append(fr)
            return fr, h

        # ── 그룹 1: 데이터 편집 (트랙) ──
        trk_edit, trk_edit_l = _track()
        btn_excel = QPushButton("Excel에서 편집")
        btn_excel.setMinimumHeight(30)
        btn_excel.setCursor(Qt.PointingHandCursor)
        btn_excel.setToolTip("Excel 파일을 열어 시약 정보와 시퀀스 Step을 편집합니다.")
        btn_excel.clicked.connect(self.app.excel_mgr.open_reagents_excel)
        self._ghost_btns.append(btn_excel)
        trk_edit_l.addWidget(btn_excel)
        tl.addWidget(trk_edit)

        # ── 그룹 2: Step 조작 (트랙) ──
        trk_step, trk_step_l = _track()

        btn_add = QPushButton("+ Step")
        btn_add.setMinimumHeight(30)
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self.add_step)
        self._ghost_btns.append(btn_add)
        trk_step_l.addWidget(btn_add)

        btn_del = QPushButton("- Step")
        btn_del.setMinimumHeight(30)
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(self.delete_step)
        self._ghost_btns.append(btn_del)
        trk_step_l.addWidget(btn_del)

        btn_copy = QPushButton("Step 복사")
        btn_copy.setMinimumHeight(30)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.setToolTip("선택한 Step의 조건을 복사하여 새 Step을 추가합니다.")
        btn_copy.clicked.connect(self.copy_step)
        self._ghost_btns.append(btn_copy)
        trk_step_l.addWidget(btn_copy)

        btn_up = QPushButton("▲")
        btn_up.setMinimumHeight(30)
        btn_up.setFixedWidth(30)
        btn_up.setCursor(Qt.PointingHandCursor)
        btn_up.setToolTip("선택한 Step을 위로 이동")
        btn_up.clicked.connect(self.move_step_up)
        self._ghost_btns.append(btn_up)
        trk_step_l.addWidget(btn_up)

        btn_down = QPushButton("▼")
        btn_down.setMinimumHeight(30)
        btn_down.setFixedWidth(30)
        btn_down.setCursor(Qt.PointingHandCursor)
        btn_down.setToolTip("선택한 Step을 아래로 이동")
        btn_down.clicked.connect(self.move_step_down)
        self._ghost_btns.append(btn_down)
        trk_step_l.addWidget(btn_down)

        btn_paste = QPushButton("Excel 붙여넣기")
        btn_paste.setMinimumHeight(30)
        btn_paste.setCursor(Qt.PointingHandCursor)
        btn_paste.setToolTip("클립보드의 엑셀 데이터(온도/시간/부피/분획부피/포트/당량)를 Step으로 변환합니다.")
        btn_paste.clicked.connect(self._paste_steps_from_excel)
        self._ghost_btns.append(btn_paste)
        trk_step_l.addWidget(btn_paste)

        btn_copy_all = QPushButton("전체 복사")
        btn_copy_all.setMinimumHeight(30)
        btn_copy_all.setCursor(Qt.PointingHandCursor)
        btn_copy_all.setToolTip("모든 Step 데이터를 클립보드에 TSV로 복사합니다. Ctrl+C")
        btn_copy_all.clicked.connect(self.copy_to_clipboard)
        self._ghost_btns.append(btn_copy_all)
        trk_step_l.addWidget(btn_copy_all)
        tl.addWidget(trk_step)

        # ── 그룹 3: 계산 ──
        btn_calc = QPushButton("유속 계산")
        btn_calc.setMinimumHeight(36)
        btn_calc.setCursor(Qt.PointingHandCursor)
        btn_calc.clicked.connect(lambda: self.calc_flow(silent=False))
        btn_calc.setToolTip(
            "<b>유속 계산</b><br>"
            "입력한 반응조건(부피, 시간, 당량)을 바탕으로<br>"
            "각 펌프가 흘려야 할 유속을 자동으로 계산합니다.<br>"
            "<i>입력 변경 시 0.3초 후 자동 재계산됩니다.</i>"
        )
        self._secondary_btns.append(btn_calc)
        tl.addWidget(btn_calc)

        # ── 뷰 토글: 카드 ↔ 테이블(HTE) ──
        self.btn_view_toggle = QPushButton("⊞ 테이블 뷰")
        self.btn_view_toggle.setMinimumHeight(36)
        self.btn_view_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_view_toggle.setToolTip(
            "<b>테이블 뷰</b><br>모든 Step을 행=스텝·열=파라미터 표로 편집합니다.<br>"
            "HTE 대량 입력·비교·엑셀 복붙에 유리. 카드 뷰와 실시간 동기화됩니다.")
        self.btn_view_toggle.clicked.connect(self._toggle_view)
        self._secondary_btns.append(self.btn_view_toggle)
        tl.addWidget(self.btn_view_toggle)

        # ── 연구노트(F-SCH) Export ──
        btn_note = QPushButton("연구노트 Export")
        btn_note.setMinimumHeight(36)
        btn_note.setCursor(Qt.PointingHandCursor)
        btn_note.setToolTip(
            "<b>연구노트 Export</b><br>"
            "현재 시퀀스의 최종 결과(유속·시약·조건)를 F-SCH 연구노트 JSON으로<br>"
            "스텝별 저장합니다. 화학메타는 계산기 PubChem 결과에서 채워집니다.")
        btn_note.clicked.connect(self._export_notebook)
        self._secondary_btns.append(btn_note)
        tl.addWidget(btn_note)

        # 전체 시퀀스 요약 — 모노 메트릭 칩 (스타일은 _apply_button_styles)
        self.lbl_total_summary = QLabel("")
        tl.addWidget(self.lbl_total_summary)

        # 좌측 버튼들 이후 남은 공간을 stretch로 밀어서 우측 정렬
        tl.addStretch()

        # ── 그룹 4: 분취 + 튜브 배치 ──
        self.lbl_start_tube_seq = QLabel("분취 시작:")
        self.lbl_start_tube_seq.setStyleSheet(f"color: {Dark.TEXT_PRIMARY}; font-weight: {T.FW_SEMI}; font-size: {T.FS_MD};")

        self.sp_collector_start = QSpinBox()
        # @codesyncer-decision: 상한을 실제 collector 용량으로 클램프 (최초 부팅 포함)
        # - 기존: 192 하드코딩, rebuild(핫리로드)에서만 보정 → Colosseum(87튜브)
        #   첫 부팅 시 #150 같은 불가능한 시작 튜브 입력 가능 → 실행 후 SafetyError
        _col_total = int(getattr(getattr(self.app, "collector", None), "total_tubes", 0) or 0)
        self.sp_collector_start.setRange(1, _col_total if _col_total > 0 else 192)
        self.sp_collector_start.setValue(1)
        # 구 '# ' 프리픽스 제거 — 계측기 표기 아님, '분취 시작' 라벨이 의미 전달 (사용자 피드백)
        self.sp_collector_start.setMinimumHeight(32)
        self.sp_collector_start.setMinimumWidth(70)

        # Well ID 라벨 — Plate96일 때 "A_A1", "B_H12" 등 표시
        self.lbl_well_id = QLabel("")
        self.lbl_well_id.setStyleSheet(
            f"font-family: {T.FONT_MONO}; font-size: {T.FS_LG}; "
            f"font-weight: {T.FW_BOLD}; padding: 0 4px;"
        )
        self.lbl_well_id.setMinimumWidth(96)
        self.sp_collector_start.valueChanged.connect(self._update_well_id_label)
        # 시작 튜브 변경 → 카드별 튜브 범위 재계산 필요
        self.sp_collector_start.valueChanged.connect(
            lambda _: self._mark_flow_dirty("start_tube_changed")
        )

        self.btn_preview = QPushButton("Fluid Tracker")
        self.btn_preview.setMinimumHeight(T.H_BTN_XL)
        self.btn_preview.setMinimumWidth(140)
        self.btn_preview.setToolTip("나선형 회전판에서 튜브 사용 예측을 시각화합니다.")
        self.btn_preview.clicked.connect(self.show_spiral_preview)

        # 랙 선택 (2026-08-05): 96-well / 에펜도르프 랙 등 — 선택 시 좌표 파일이
        # 교체되고 이후 이동은 기존 로직(서펜타인 + machine 좌표) 그대로 동작.
        self.cb_rack = QComboBox()
        for _lbl, _val in self._rack_options():
            self.cb_rack.addItem(_lbl, _val)
        self.cb_rack.setMinimumHeight(T.H_BTN_XL)
        self.cb_rack.setToolTip(
            "분취 랙 선택 — 좌표 파일이 교체되고 튜브 수/격자가 함께 바뀝니다.\n"
            "랙 교체 후에는 HOME 을 먼저 실행하세요.")
        self._sync_rack_combo()
        self.cb_rack.currentIndexChanged.connect(self._on_rack_changed)

        tl.addWidget(self.lbl_start_tube_seq)
        tl.addWidget(self.sp_collector_start)
        tl.addWidget(self.lbl_well_id)
        tl.addWidget(self.cb_rack)
        tl.addWidget(self.btn_preview)

        tl.addSpacing(12)

        # ── 그룹 5: 결과 저장 + 실험 시작 ──
        # @codesyncer-decision: 결과 저장 체크박스를 실험 시작 버튼 바로 옆에 배치
        self.chk_save_results = QCheckBox("결과 저장")
        self.chk_save_results.setChecked(True)
        self.chk_save_results.setMinimumHeight(36)
        self.chk_save_results.setToolTip("실험 완료 시 결과(JSON + 튜브 배치도 PNG)를 자동 저장합니다.")

        self.btn_run = QPushButton("실험 시작 (START)")
        self.btn_run.setMinimumHeight(T.H_BTN_XL)
        self.btn_run.clicked.connect(self.run_seq)
        self.btn_run.setToolTip(
            "<b>실험 시작</b><br>"
            "설정한 모든 Step을 순서대로 자동 실행합니다.<br>"
            "가열 → 세척 → 시약 주입 → 반응물 수집 순으로 진행되며,<br>"
            "실행 중 일시정지(Pause) 및 중지(Stop)가 가능합니다."
        )

        self._apply_button_styles(Dark)

        tl.addWidget(self.chk_save_results)
        tl.addWidget(self.btn_run)

        return bar

    # ─── Left: Reagent Tabs (농도 참조 전용) ──────────────────

    def create_reagent_tabs(self):
        """좌측: 시약 설정 WebGrid 생성 (펌프별 그룹, 계산기와 동일 디자인).

        @codesyncer-decision: 촘촘한 QTabWidget×QTableWidget → 단일 WebGrid(Tabulator).
          펌프별 그룹 밴드로 전 펌프를 한 화면에(탭 전환 불필요), 엑셀 복붙/드래그
          범위선택 내장. reagent_tables[pump] 에는 외부 호환 어댑터를 넣는다.
        """
        cfg = self.app.cfg
        inlet_pumps = list(getattr(cfg, "INLET_PUMPS", []) or [])

        self.reagent_tables = {}
        self.reagent_grid = None
        # 다성분 stock 레시피 — {(pump, port): recipe} + 프리셋. 파일에서 복원.
        self._load_stock_recipes()

        if len(inlet_pumps) == 0:
            empty_widget = QWidget()
            empty_layout = QVBoxLayout(empty_widget)
            empty_layout.setContentsMargins(12, 12, 12, 12)
            msg = QLabel("설정된 Inlet Pump가 없습니다.\nHardware 설정에서 펌프를 먼저 구성하세요.")
            msg.setAlignment(Qt.AlignCenter)
            msg.setWordWrap(True)
            empty_layout.addWidget(msg)
            return empty_widget

        from ui.webgrid.webgrid import WebGrid

        all_rows = []
        for gi, pump_name in enumerate(inlet_pumps):
            adapter = _ReagentGridAdapter(self, pump_name, gi)
            self.reagent_tables[pump_name] = adapter
            for port in range(1, 13):
                all_rows.append(self._reagent_row_dict(gi, pump_name, port, adapter._cells))

        is_dark = getattr(self.app, 'is_dark_mode', True)
        self.reagent_grid = WebGrid(all_rows, html="reagent_grid.html",
                                    theme=self._reagent_theme(is_dark))
        self.reagent_grid.cellChanged.connect(self._on_reagent_grid_cell)
        # @codesyncer: 외부(method_io·reagent_excel·계산기 import)가 참조하는
        #   app.reagent_tables 를 초기 로드에서도 현재 어댑터 dict 로 동기화(rebuild 외).
        self.app.reagent_tables = self.reagent_tables
        return self.reagent_grid

    # ── WebGrid 시약 그리드 헬퍼 ─────────────────────────────
    def _reagent_cell_defaults(self, pump, port):
        """(포트라벨, 시약명, 농도, SMILES) 초기값 — map_mgr + 예약포트."""
        # 포트 라벨은 번호만 — 역할(용매/폐기)은 시약명 컬럼이 전달(좁은 pane 잘림 방지)
        if port == 1:
            return ("1", "세척 용매", "1.0", "")
        if port == 12:
            return ("12", "폐기", "1.0", "")
        info = {}
        if self.app.map_mgr:
            info = self.app.map_mgr.get_inlet(pump, port) or {}
        name = info.get('name', '') or "Empty"
        conc = info.get('conc', 1.0)
        conc = "" if conc is None else str(conc)
        smiles = info.get('smiles', '') or ""
        return (str(port), name, conc, smiles)

    def _reagent_row_dict(self, gi, pump, port, cells):
        label, name, conc, smiles = cells[port - 1]
        # @codesyncer(placeholder 전환): 'Empty/비어있음' 센티널을 그리드에 실제 텍스트로
        #   보내면 편집 시 지우고 타이핑해야 함 — 그리드에는 빈 값으로 보내고 표시는
        #   JS 고스트 포매터가 담당. 내부 캐시/맵의 센티널 저장 방식은 불변.
        if name in ("Empty", "비어있음", "-"):
            name = ""
        row = {
            "_id": f"{gi}:{port}", "grp": pump, "gk": "abcd"[gi % 4],
            "port": label, "name": name,
            "conc": _rnum(conc), "smiles": smiles,
            "ro": port in (1, 12),
        }
        # @codesyncer(다성분 stock A안, 2026-07-13 사용자 확정): 포트행 = 첫 시약
        #   (components[0], 단일시약 UX 불변) · 추가 시약 = components[1:] 을
        #   '편집 가능한' dataTree 자식행으로 + 말미 [＋ 시약 추가] 행.
        #   안 쓸 땐 접힘(기본) — ×N 배지 클릭=펼침/접기 토글(JS).
        rec = getattr(self, "stock_recipes", {}).get((pump, port))
        if rec and len(rec.get("components") or []) >= 2:
            from engine.stock_stoich import recipe_summary
            comps = rec["components"]
            row["mixN"] = len(comps)
            row["tip"] = recipe_summary(rec)
            kids = []
            for k, c in enumerate(comps[1:], start=1):
                kids.append({
                    "_id": f"{gi}:{port}:c{k}", "_comp": True, "ro": False,
                    "grp": pump, "gk": row["gk"], "port": "",
                    "name": c.get("reagent", "") or "",
                    "conc": _rnum(c.get("molarity")) or _rnum(c.get("conc")),
                    "smiles": c.get("smiles", "") or "",
                })
            kids.append({"_id": f"{gi}:{port}:add", "_add": True, "ro": True,
                         "grp": pump, "gk": row["gk"], "port": "",
                         "name": "", "conc": None, "smiles": ""})
            row["_children"] = kids
        return row

    def _push_reagent_rows(self, pump, gi, cells):
        grid = getattr(self, 'reagent_grid', None)
        if grid is None:
            return
        # 레시피(자식행) 있는 펌프는 updateOrAddData 로 tree 구조가 안 실림 → 전체 교체
        if any((pump, p) in getattr(self, "stock_recipes", {})
               and len((self.stock_recipes[(pump, p)].get("components") or [])) >= 2
               for p in range(1, 13)):
            self._refresh_reagent_grid_full()
            return
        rows = [self._reagent_row_dict(gi, pump, p, cells) for p in range(1, 13)]
        grid.set_rows(rows)

    def _reagent_theme(self, is_dark=None):
        from ui.webgrid.webgrid import grid_theme
        if is_dark is None:
            is_dark = getattr(self.app, 'is_dark_mode', True)
        return grid_theme(Dark if is_dark else Light, is_dark)

    def _on_reagent_grid_cell(self, d):
        """그리드 셀 편집(사용자) → 어댑터 캐시 + map_mgr + Port 콤보 동기화.

        다성분 stock(A안): ⚗=다이얼로그 · ＋=자식 시약행 추가 · ✕=성분 삭제 ·
        자식행(_comp) 편집=레시피 성분 인라인 갱신 · 포트행 편집=components[0] 동기.
        """
        if d.get("_openRecipe"):
            self._open_stock_recipe(str(d["_openRecipe"]))
            return
        if d.get("_addComp"):
            self._inline_add_component(str(d["_addComp"]))
            return
        if d.get("_delComp"):
            self._inline_del_component(str(d["_delComp"]))
            return
        if d.get("_comp"):
            self._inline_edit_component(d)
            return
        if d.get("_add"):
            return
        pump = d.get("grp")
        try:
            port = int(str(d.get("port", "")).split()[0])
        except (TypeError, ValueError):
            return
        adapter = self.reagent_tables.get(pump)
        if adapter is None or not (1 <= port <= 12):
            return
        r = port - 1
        name = d.get("name", "") or ""
        conc = d.get("conc")
        conc_s = "" if conc in (None, "") else str(conc)
        smiles = (d.get("smiles", "") or "")
        # 캐시 직접 갱신 (그리드는 이미 값 보유 → 재푸시 안 함)
        adapter._cells[r][1] = name
        adapter._cells[r][2] = conc_s
        adapter._cells[r][3] = smiles
        if 0 < r < 11 and self.app.map_mgr:
            c = _rnum(conc_s)
            self.app.map_mgr.update_inlet(pump, port, name or "Empty",
                                          1.0 if c is None else c, smiles.strip())
            self.refresh_port_combos(pump)
            self._mark_flow_dirty("reagent_grid_edit")
            # 포트행 = components[0] (A안) — 레시피 있으면 첫 시약 동기
            rec = getattr(self, "stock_recipes", {}).get((pump, port))
            if rec and rec.get("components"):
                c0 = rec["components"][0]
                c0["reagent"] = name
                c0["molarity"] = _rnum(conc_s) or 0.0
                c0["smiles"] = smiles.strip()
                self._save_stock_recipes()

    # ── 다성분 stock 레시피 (혼합 = 포트의 속성, 2026-07-13) ──────
    #   저장: <프로젝트루트>/stock_recipes.json
    #   {"presets": {이름: recipe}, "assignments": {"pump|port": recipe}}
    #   그리드는 뷰(배지+자식행)만, 편집은 StockRecipeDialog, 양론은 stock_stoich.
    #   map_mgr 에는 대표값(레시피명, limiting concM)만 흘려 기존 소비자
    #   (StepCard 포트콤보·유속계산·배관도) 무수정 호환.

    def _stock_recipes_path(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "stock_recipes.json")

    def _load_stock_recipes(self):
        self.stock_recipes = {}
        self.stock_presets = {}
        try:
            with open(self._stock_recipes_path(), encoding="utf-8") as f:
                data = json.load(f)
            self.stock_presets = dict(data.get("presets") or {})
            for key, rec in (data.get("assignments") or {}).items():
                pump, _, port_s = key.partition("|")
                try:
                    self.stock_recipes[(pump, int(port_s))] = rec
                except (TypeError, ValueError):
                    continue
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[StockRecipe] 로드 실패 (무시): {e}")

    def _save_stock_recipes(self):
        try:
            data = {"presets": self.stock_presets,
                    "assignments": {f"{p}|{port}": rec
                                    for (p, port), rec in self.stock_recipes.items()}}
            with open(self._stock_recipes_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[StockRecipe] 저장 실패: {e}")

    def _rid_parse(self, row_id):
        """'gi:port[:cK|:add]' → (gi, port, pump, comp_idx|None). 실패 시 pump=None."""
        try:
            parts = str(row_id).split(":")
            gi, port = int(parts[0]), int(parts[1])
        except (TypeError, ValueError, IndexError):
            return 0, 0, None, None
        inlet = list(getattr(self.app.cfg, "INLET_PUMPS", []) or [])
        if not (0 <= gi < len(inlet)):
            return 0, 0, None, None
        k = None
        if len(parts) > 2 and parts[2].startswith("c"):
            try:
                k = int(parts[2][1:])
            except ValueError:
                k = None
        return gi, port, inlet[gi], k

    def _refresh_reagent_grid_full(self, expand=None, edit=None):
        """dataTree 구조 변경(자식 추가/삭제) → 전체 교체.
        expand='gi:port' 재펼침, edit='gi:port:cK' 새 행 이름셀 자동 편집 진입."""
        grid = getattr(self, "reagent_grid", None)
        if grid is None:
            return
        if expand or edit:
            try:
                grid.view.page().runJavaScript(
                    f"window._expandNext = {json.dumps(expand)};"
                    f"window._editNext = {json.dumps(edit)};")
            except Exception:
                pass
        all_rows = []
        inlet = list(getattr(self.app.cfg, "INLET_PUMPS", []) or [])
        for g, pn in enumerate(inlet):
            ad = self.reagent_tables.get(pn)
            if ad is None:
                continue
            for p in range(1, 13):
                all_rows.append(self._reagent_row_dict(g, pn, p, ad._cells))
        grid.replace_rows(all_rows)

    def _inline_add_component(self, row_id):
        """＋(포트행 hover) 또는 [＋ 시약 추가] 행 → 자식 시약 행 신설.
        레시피 없던 포트는 현재 행을 components[0] 으로 승격."""
        gi, port, pump, _k = self._rid_parse(row_id)
        if pump is None or port in (1, 12):
            return
        rec = self.stock_recipes.get((pump, port))
        if not rec:
            ad = self.reagent_tables.get(pump)
            cells = ad._cells[port - 1] if ad is not None else ("", "", "", "")
            name0 = cells[1] if cells[1] not in ("Empty", "비어있음", "-") else ""
            rec = {"name": "", "total_volume_ml": 0.0, "solvents": [],
                   "components": [{"reagent": name0,
                                   "molarity": _rnum(cells[2]) or 0.0,
                                   "smiles": cells[3] or "", "limiting": True}]}
        rec["components"].append({"reagent": "", "molarity": 0.0, "smiles": ""})
        self.stock_recipes[(pump, port)] = rec
        self._save_stock_recipes()
        new_k = len(rec["components"]) - 1
        self._refresh_reagent_grid_full(expand=f"{gi}:{port}",
                                        edit=f"{gi}:{port}:c{new_k}")
        self._mark_flow_dirty("stock_recipe")

    def _inline_del_component(self, row_id):
        """자식행 ✕ → 성분 삭제. 1성분만 남으면(추가 메타 없을 때) 단일시약 복귀."""
        gi, port, pump, k = self._rid_parse(row_id)
        if pump is None or k is None:
            return
        rec = self.stock_recipes.get((pump, port))
        if not rec or not (1 <= k < len(rec.get("components") or [])):
            return
        del rec["components"][k]
        if len(rec["components"]) < 2 and not (
                rec.get("solvents") or float(rec.get("total_volume_ml") or 0) > 0):
            self.stock_recipes.pop((pump, port), None)
            expand = None
        else:
            expand = f"{gi}:{port}" if len(rec["components"]) >= 2 else None
        self._save_stock_recipes()
        self._refresh_reagent_grid_full(expand=expand)
        self._mark_flow_dirty("stock_recipe")

    def _inline_edit_component(self, d):
        """자식 시약행 인라인 편집(name/conc/smiles) → 레시피 성분 갱신.
        그리드는 이미 값 보유 → 재푸시 불필요(툴팁 요약은 다음 rebuild 때 갱신)."""
        gi, port, pump, k = self._rid_parse(d.get("_id"))
        if pump is None or k is None:
            return
        rec = self.stock_recipes.get((pump, port))
        comps = (rec or {}).get("components") or []
        if not (1 <= k < len(comps)):
            return
        comps[k]["reagent"] = d.get("name", "") or ""
        comps[k]["molarity"] = _rnum(d.get("conc")) or 0.0
        comps[k]["smiles"] = (d.get("smiles", "") or "").strip()
        self._save_stock_recipes()
        self._mark_flow_dirty("stock_recipe")

    def _open_stock_recipe(self, row_id):
        """그리드 ⚗ 클릭 → 레시피 편집 다이얼로그(칭량/프리셋 상세). row_id='gi:port'."""
        gi, port, pump, _k = self._rid_parse(row_id)
        if pump is None or port in (1, 12):
            return
        # 시약 라이브러리 (계산기 PubChem/수동입력) — MW/밀도 자동채움용
        try:
            reagent_lib = self.app.calc_tab.build_reagent_lib()
        except Exception:
            reagent_lib = getattr(self.app, "reagent_lib", {}) or {}
        from ui.dialog_stock_recipe import StockRecipeDialog

        def _persist_presets(presets):
            # 프리셋 라이브러리는 다이얼로그 취소와 무관하게 즉시 영속화
            self.stock_presets = dict(presets)
            self._save_stock_recipes()

        dlg = StockRecipeDialog(pump, port,
                                recipe=self.stock_recipes.get((pump, port)),
                                presets=self.stock_presets,
                                reagent_lib=reagent_lib,
                                is_dark=getattr(self.app, "is_dark_mode", True),
                                parent=self,
                                on_presets_changed=_persist_presets)
        if dlg.exec_() != dlg.Accepted:
            return
        self.stock_presets = dict(dlg.presets)          # 프리셋 저장 반영
        self._apply_stock_recipe(pump, gi, port, dlg.recipe)

    def _apply_stock_recipe(self, pump, gi, port, recipe):
        """레시피 적용/해제(다이얼로그 경로) → 저장 + 포트행=components[0] 동기 +
        map_mgr 대표값 + 그리드 전체 갱신. (A안: 포트행 표시는 첫 시약)"""
        adapter = self.reagent_tables.get(pump)
        if recipe:                                      # 적용
            self.stock_recipes[(pump, port)] = recipe
            comps = recipe.get("components") or []
            c0 = comps[0] if comps else {}
            name = c0.get("reagent") or recipe.get("name") or "혼합 stock"
            conc = _rnum(c0.get("molarity")) or (recipe.get("conc_m") or 0.0)
            smiles = (c0.get("smiles") or "").strip()
            if adapter is not None:
                # _cells 직접 쓰기 = flush 미유발 (아래 replace_rows 가 전체 갱신)
                adapter._cells[port - 1][1] = name
                adapter._cells[port - 1][2] = f"{conc:.6g}" if conc else ""
                adapter._cells[port - 1][3] = smiles
            if self.app.map_mgr:
                try:
                    self.app.map_mgr.update_inlet(pump, port, name, conc or 1.0, smiles)
                except Exception:
                    pass
        else:                                           # 해제 (혼합 → 단일시약 복귀)
            self.stock_recipes.pop((pump, port), None)
        self._save_stock_recipes()
        # dataTree 자식행 구조 변경은 부분갱신(updateOrAddData)이 불안정 → 전체 교체
        self._refresh_reagent_grid_full(
            expand=f"{gi}:{port}" if recipe and len(
                recipe.get("components") or []) >= 2 else None)
        self.refresh_port_combos(pump)
        self._mark_flow_dirty("stock_recipe")

    def _focus_in_reagent_grid(self):
        """현재 포커스가 시약 WebGrid(웹뷰) 안에 있는가."""
        grid = getattr(self, 'reagent_grid', None)
        if grid is None:
            return False
        w = QApplication.focusWidget()
        if w is None:
            return False
        view = getattr(grid, 'view', None)
        if w is grid or w is view:
            return True
        if view is not None and view.isAncestorOf(w):
            return True
        return grid.isAncestorOf(w)

    # ── 스텝 테이블 뷰 (HTE 하이브리드) ─────────────────────────
    def _steps_view_active(self):
        return getattr(self, 'view_stack', None) is not None and self.view_stack.currentIndex() == 1

    def _toggle_view(self):
        """카드 뷰 ↔ 테이블 뷰 전환."""
        if self._steps_view_active():
            self.view_stack.setCurrentIndex(0)
            self.btn_view_toggle.setText("⊞ 테이블 뷰")
        else:
            self._ensure_steps_grid()
            self._rebuild_steps_grid()      # 카드에서의 변경 반영
            self.view_stack.setCurrentIndex(1)
            self.btn_view_toggle.setText("▤ 카드 뷰")

    def _ensure_steps_grid(self):
        if self.steps_grid is not None:
            return
        from ui.webgrid.webgrid import WebGrid
        self.steps_grid = WebGrid(self._steps_rows(), html="steps_grid.html",
                                  theme=self._reagent_theme(),
                                  columns=self._steps_columns())
        self.steps_grid.cellChanged.connect(self._on_steps_grid_cell)
        self.view_stack.addWidget(self.steps_grid)   # index 1

    def _steps_columns(self):
        """스텝 그리드 컬럼 정의(JSON 직렬화 가능) — 펌프 수에 따라 동적."""
        inlet = list(getattr(self.app.cfg, "INLET_PUMPS", []) or [])
        cols = [
            {"title": "#", "field": "step", "width": 50, "hozAlign": "center",
             "headerHozAlign": "center", "cssClass": "c-step", "ro": True, "frozen": True},
            {"title": "온도 (°C)", "field": "temp", "width": 92, "hozAlign": "right",
             "headerHozAlign": "right", "editor": "number", "dashEmpty": True},
            {"title": "체류 (min)", "field": "rt", "width": 88, "hozAlign": "right",
             "headerHozAlign": "right", "editor": "number", "dashEmpty": True},
            {"title": "반응부피 (mL)", "field": "vol", "width": 116, "hozAlign": "right",
             "headerHozAlign": "right", "editor": "number", "dashEmpty": True},
            {"title": "분획 (mL)", "field": "tube_vol", "width": 92, "hozAlign": "right",
             "headerHozAlign": "right", "editor": "number", "dashEmpty": True},
        ]
        for gi, pump in enumerate(inlet):
            # 컬럼 그룹: 상위 = 펌프명, 하위 = 포트 | 당량 (HTE 플레이트 헤더 방식)
            cols.append({"title": pump, "headerHozAlign": "center", "columns": [
                {"title": "포트", "field": f"p{gi}_port", "width": 74,
                 "hozAlign": "right", "headerHozAlign": "right", "editor": "number",
                 "cssClass": "c-inp", "dashEmpty": True},
                {"title": "당량", "field": f"p{gi}_eq", "width": 74,
                 "hozAlign": "right", "headerHozAlign": "right", "editor": "number",
                 "cssClass": "c-inp", "dashEmpty": True},
            ]})
        cols.append({"title": "총유속 (mL/min)", "field": "flow", "width": 118, "hozAlign": "right",
                     "headerHozAlign": "right", "cssClass": "c-res", "ro": True})
        return cols

    def _steps_rows(self):
        """sequence_data → 스텝 그리드 행 dict 리스트."""
        inlet = list(getattr(self.app.cfg, "INLET_PUMPS", []) or [])
        running = getattr(self, '_running_step_idx', -1)
        rows = []
        for i, sd in enumerate(self.sequence_data):
            r = {"_id": str(i), "step": i + 1,
                 "temp": sd.get('temp'), "rt": sd.get('rt'),
                 "vol": sd.get('vol'), "tube_vol": sd.get('tube_vol'),
                 "state": "running" if i == running else ""}
            total = 0.0
            for gi, pump in enumerate(inlet):
                pd = sd.get('pumps', {}).get(pump, {})
                r[f"p{gi}_port"] = pd.get('port')
                r[f"p{gi}_eq"] = pd.get('eq')
                try:
                    total += float(pd.get('flow') or 0.0)
                except (TypeError, ValueError):
                    pass
            r["flow"] = round(total, 3) if total else None
            rows.append(r)
        return rows

    def _rebuild_steps_grid(self):
        """행 추가/삭제/재정렬 반영 — 전체 교체."""
        if self.steps_grid is not None:
            self.steps_grid.replace_rows(self._steps_rows())

    def _refresh_steps_grid(self):
        """계산 결과(유속)·상태 갱신 — 부분 업데이트."""
        if self.steps_grid is not None:
            self.steps_grid.set_rows(self._steps_rows())

    def _on_steps_grid_cell(self, d):
        """스텝 그리드 셀 편집 → sequence_data + StepCard 동기화 + 유속 재계산."""
        try:
            idx = int(d.get("_id"))
        except (TypeError, ValueError):
            return
        if not (0 <= idx < len(self.sequence_data)):
            return
        sd = self.sequence_data[idx]
        card = self.step_cards[idx] if idx < len(self.step_cards) else None
        inlet = list(getattr(self.app.cfg, "INLET_PUMPS", []) or [])
        self._steps_syncing = True
        try:
            for key, attr in (("temp", "sp_temp"), ("rt", "sp_rt"),
                              ("vol", "sp_vol"), ("tube_vol", "sp_tube")):
                v = _rnum(d.get(key))
                if v is not None:
                    sd[key] = v
                    w = getattr(card, attr, None) if card else None
                    if w is not None:
                        w.blockSignals(True); w.setValue(v); w.blockSignals(False)
            for gi, pump in enumerate(inlet):
                pd = sd.setdefault('pumps', {}).setdefault(
                    pump, {'port': 2, 'eq': 1.0, 'flow': 0.0, 'vial': ''})
                pv = _rnum(d.get(f"p{gi}_port"))
                if pv is not None:
                    port = max(1, min(12, int(pv)))
                    pd['port'] = port
                    pw = card.pump_widgets.get(pump) if card else None
                    if pw and pw.get('cb_port') is not None:
                        cb = pw['cb_port']; ci = cb.findData(port)
                        if ci >= 0:
                            cb.blockSignals(True); cb.setCurrentIndex(ci); cb.blockSignals(False)
                ev = _rnum(d.get(f"p{gi}_eq"))
                if ev is not None:
                    pd['eq'] = ev
                    pw = card.pump_widgets.get(pump) if card else None
                    if pw and pw.get('sp_eq') is not None:
                        se = pw['sp_eq']; se.blockSignals(True); se.setValue(ev); se.blockSignals(False)
        finally:
            self._steps_syncing = False
        self._mark_flow_dirty("steps_grid_edit")
        self.calc_flow(silent=True)
        self._refresh_steps_grid()

    @staticmethod
    def make_readonly_conc_item(value):
        """농도 컬럼용 읽기전용 QTableWidgetItem 생성"""
        item = QTableWidgetItem(str(value))
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    def create_reagent_table(self, pump_name):
        """시약 테이블 생성 (12 포트) - 농도 컬럼 직접 편집 가능"""
        # @codesyncer-decision: col 3 = SMILES (구조식). 있으면 배관도 inlet 에
        #   실제 화학구조로, 없으면 시약명 라벨로 대체된다. Solvent/Waste 포트는
        #   구조가 없으므로 읽기전용 공란.
        table = QTableWidget(12, 4)
        table.setHorizontalHeaderLabels(["포트", "시약 이름", "농도(M)", "SMILES"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(400)

        def _ro_smiles():
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            return it

        # Port 1: Solvent (읽기 전용)
        table.setItem(0, 0, QTableWidgetItem("1 (Solvent)"))
        table.item(0, 0).setFlags(Qt.ItemIsEnabled)
        table.setItem(0, 1, QTableWidgetItem("세척 용매"))
        table.item(0, 1).setFlags(Qt.ItemIsEnabled)
        conc_item = QTableWidgetItem("1.0")
        conc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        table.setItem(0, 2, conc_item)
        table.setItem(0, 3, _ro_smiles())

        # Ports 2~11
        for r in range(1, 11):
            port_item = QTableWidgetItem(str(r + 1))
            port_item.setFlags(Qt.ItemIsEnabled)
            table.setItem(r, 0, port_item)
            table.setItem(r, 1, QTableWidgetItem("Empty"))
            # @codesyncer-decision: 농도 컬럼 UI에서 직접 편집 가능
            conc_item = QTableWidgetItem("1.0")
            conc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            table.setItem(r, 2, conc_item)
            # SMILES 컬럼 — 직접 편집/붙여넣기 가능 (기본 공란)
            smi_item = QTableWidgetItem("")
            smi_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            table.setItem(r, 3, smi_item)

        # Port 12: Waste (읽기 전용)
        table.setItem(11, 0, QTableWidgetItem("12 (Waste)"))
        table.item(11, 0).setFlags(Qt.ItemIsEnabled)
        table.setItem(11, 1, QTableWidgetItem("폐기"))
        table.item(11, 1).setFlags(Qt.ItemIsEnabled)
        conc_item = QTableWidgetItem("1.0")
        conc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        table.setItem(11, 2, conc_item)
        table.setItem(11, 3, _ro_smiles())

        # 시약 이름 또는 농도 변경 시 map 업데이트
        if self.app.map_mgr:
            table.cellChanged.connect(
                lambda r, c, p=pump_name, t=table: self.on_map_changed(p, r, c, t)
            )

        # Ctrl+C로 시약 테이블 복사 (헤더 포함)
        table.installEventFilter(self)

        return table

    def eventFilter(self, obj, event):
        """시약 테이블 Ctrl+C (복사) / Ctrl+V (붙여넣기)"""
        if isinstance(obj, QTableWidget) and event.type() == event.KeyPress:
            mods = event.modifiers()
            key = event.key()

            if mods & Qt.ControlModifier:
                # Ctrl+C: 헤더 포함 TSV 복사
                if key == Qt.Key_C:
                    ranges = obj.selectedRanges()
                    if ranges:
                        sr = ranges[0]
                        lines = []
                        header = []
                        for c in range(sr.leftColumn(), sr.rightColumn() + 1):
                            h = obj.horizontalHeaderItem(c)
                            header.append(h.text() if h else "")
                        lines.append("\t".join(header))
                        for r in range(sr.topRow(), sr.bottomRow() + 1):
                            row_data = []
                            for c in range(sr.leftColumn(), sr.rightColumn() + 1):
                                item = obj.item(r, c)
                                row_data.append(item.text() if item else "")
                            lines.append("\t".join(row_data))
                        QApplication.clipboard().setText("\n".join(lines))
                        return True

                # Ctrl+V: 엑셀 TSV 붙여넣기 (선택 셀 기준)
                if key == Qt.Key_V:
                    text = QApplication.clipboard().text()
                    if text and ("\t" in text or "\n" in text):
                        self._paste_reagent_table(obj, text)
                        return True

            # Delete: 선택 영역 셀 내용 지우기
            if key == Qt.Key_Delete:
                self._delete_reagent_selection(obj)
                return True

        return super().eventFilter(obj, event)

    def _delete_reagent_selection(self, table):
        """Delete 키: 시약 테이블 선택 영역의 편집 가능 셀 내용 지우기"""
        ranges = table.selectedRanges()
        if not ranges:
            return

        # 어떤 펌프의 테이블인지 찾기
        pump_name = None
        for pn, tbl in self.reagent_tables.items():
            if tbl is table:
                pump_name = pn
                break

        sr = ranges[0]
        table.blockSignals(True)
        try:
            for r in range(sr.topRow(), sr.bottomRow() + 1):
                if r in (0, 11):
                    continue  # Port 1(Solvent)/Port 12(Waste) 읽기전용 스킵
                for c in range(sr.leftColumn(), sr.rightColumn() + 1):
                    if c == 0:
                        continue  # 포트번호 컬럼 읽기전용
                    item = table.item(r, c)
                    if item and (item.flags() & Qt.ItemIsEditable):
                        item.setText("")
        finally:
            table.blockSignals(False)

        # map 업데이트 트리거
        if pump_name and self.app.map_mgr:
            for r in range(sr.topRow(), sr.bottomRow() + 1):
                if 0 < r < 11:
                    name = table.item(r, 1).text() if table.item(r, 1) else "Empty"
                    try:
                        conc = float(table.item(r, 2).text()) if table.item(r, 2) and table.item(r, 2).text() else 1.0
                    except ValueError:
                        conc = 1.0
                    self.app.map_mgr.update_inlet(pump_name, r + 1, name, conc)
            self.refresh_port_combos(pump_name)
            self._mark_flow_dirty("reagent_delete")

    def _paste_reagent_table(self, table, text):
        """시약 테이블에 엑셀 TSV 붙여넣기 — 선택 셀 기준으로 행×열 분배.
        - 테이블 범위 초과분은 잘라내기
        - Port 1(Solvent)/Port 12(Waste) 읽기전용 행은 스킵
        - 농도 컬럼(col 2)에 숫자 아닌 값이면 에러창 표시
        """
        # 시작 위치: 현재 선택된 셀
        sel = table.selectedRanges()
        start_row = sel[0].topRow() if sel else 0
        start_col = sel[0].leftColumn() if sel else 0

        raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while raw_lines and raw_lines[-1].strip() == "":
            raw_lines.pop()
        if not raw_lines:
            return

        # 어떤 펌프의 테이블인지 찾기
        pump_name = None
        for pn, tbl in self.reagent_tables.items():
            if tbl is table:
                pump_name = pn
                break

        # 1차 검증: 농도 셀에 숫자 아닌 값이 있으면 에러
        for i, line in enumerate(raw_lines):
            row = start_row + i
            if row >= table.rowCount() or row in (0, 11):
                continue  # 스킵 대상
            cells = line.split("\t")
            for j, val in enumerate(cells):
                col = start_col + j
                if col >= table.columnCount():
                    break
                if col == 2:  # 농도 컬럼
                    v = val.strip()
                    if v == "":
                        continue
                    try:
                        float(v.replace(",", "."))
                    except ValueError:
                        QMessageBox.warning(
                            self, "붙여넣기 오류",
                            f"농도 컬럼에는 숫자만 기입해 주세요.\n"
                            f"잘못된 값: '{v}' (행 {row + 1}, 열 '농도')"
                        )
                        return

        table.blockSignals(True)
        try:
            for i, line in enumerate(raw_lines):
                row = start_row + i
                if row >= table.rowCount():
                    break  # 테이블 범위 초과 → 잘라내기
                if row in (0, 11):
                    continue  # Port 1(Solvent)/Port 12(Waste) 스킵
                cells = line.split("\t")
                for j, val in enumerate(cells):
                    col = start_col + j
                    if col >= table.columnCount():
                        break
                    item = table.item(row, col)
                    if item is None:
                        continue
                    # col 0(포트번호) 읽기전용 유지, col 1(이름)/col 2(농도)만 편집
                    if col == 0:
                        continue
                    item.setText(val.strip())
        finally:
            table.blockSignals(False)

        # map 업데이트 트리거
        if pump_name and self.app.map_mgr:
            for i in range(len(raw_lines)):
                row = start_row + i
                if 0 < row < 11:  # Port 2~11
                    name = table.item(row, 1).text() if table.item(row, 1) else "Empty"
                    try:
                        conc = float(table.item(row, 2).text()) if table.item(row, 2) else 1.0
                    except ValueError:
                        conc = 1.0
                    self.app.map_mgr.update_inlet(pump_name, row + 1, name, conc)
            self.refresh_port_combos(pump_name)
            self._mark_flow_dirty("reagent_paste")

    # ─── Step Operations ───────────────────────────────────────

    def select_card(self, card):
        """카드 선택 (삭제/복사 대상 지정)"""
        if self._selected_card:
            self._selected_card.set_selected(False)
        self._selected_card = card
        card.set_selected(True)

    def _get_selected_index(self):
        """선택된 카드의 인덱스 반환"""
        if self._selected_card and self._selected_card in self.step_cards:
            return self.step_cards.index(self._selected_card)
        return -1

    def vials_for(self, pump_name):
        """autosampler 그룹의 선택 가능한 소스 vial 목록 (StepCard vial 콤보용).

        소스 = hw_manager 가 해석한 sampler_by_pump 의 vial_positions.
        waste/rinse/gas 등 서비스 위치는 소스 후보에서 제외.
        샘플러 미배선/미연결 환경(시뮬 등)에서는 최소 폴백 목록.
        """
        service = {"waste", "rinse", "gas", "wash", "home"}
        try:
            sampler = (getattr(getattr(self.app, "hw_mgr", None),
                               "sampler_by_pump", {}) or {}).get(pump_name)
            positions = getattr(sampler, "vial_positions", {}) or {}
            vials = sorted(v for v in positions.keys()
                           if str(v).lower() not in service)
            if vials:
                return vials
        except Exception:
            pass
        return ["A1"]

    def add_step(self):
        """새 Step 추가 (StepCard 생성)"""
        cfg = self.app.cfg
        step_num = len(self.sequence_data) + 1

        # 1. 중앙 데이터 모델에 추가
        step_data = {
            'temp': 25.0,
            'rt': 10.0,
            'vol': 5.0,
            'tube_vol': 1.5,
            'pumps': {}
        }
        for pump_name in cfg.INLET_PUMPS:
            step_data['pumps'][pump_name] = {
                'port': 2,
                'eq': 1.0,
                'flow': 0.0,
                'vial': '',   # autosampler 라우팅 전용 소스 vial (Phase A)
            }
        self.sequence_data.append(step_data)

        # 2. StepCard 생성
        card = StepCard(step_num, step_data, cfg, self.app.map_mgr, self)
        self.step_cards.append(card)

        # stretch 앞에 삽입
        insert_pos = self.card_layout.count() - 1  # stretch 위치 바로 앞
        self.card_layout.insertWidget(insert_pos, card)

        self._mark_flow_dirty("step_added")
        self._rebuild_steps_grid()

    def clear_all_steps(self):
        """모든 Step 제거 (method_io 호환용)"""
        for card in self.step_cards:
            self.card_layout.removeWidget(card)
            card.deleteLater()
        self.step_cards.clear()
        self.sequence_data.clear()
        self._selected_card = None

    def delete_step(self):
        """컨텍스트 분기 Delete:
        - 포커스가 시약 그리드 안이면 → Tabulator가 범위 셀을 비움(양보)
        - 그 외 → 선택된 StepCard 삭제
        """
        focused = QApplication.focusWidget()
        if self._focus_in_reagent_grid():
            return  # 시약 그리드는 Tabulator가 Delete 처리

        idx = self._get_selected_index()
        if idx < 0:
            QMessageBox.warning(self, "삭제 오류", "삭제할 Step을 선택하세요.")
            return

        # @codesyncer-decision: 입력 편집 중 Delete 키 가드 (인간공학 개선)
        # - 기존: 전역 QShortcut(Delete)가 spinbox/combobox 편집 중에도 발동
        #   → 숫자 지우려다 Step 카드 전체가 삭제되는 사고 위험
        # - 수정: 포커스가 입력 위젯이면 Delete를 텍스트 편집용으로 양보
        if isinstance(focused, (QAbstractSpinBox, QLineEdit, QComboBox)):
            return

        # @codesyncer-decision: 삭제 확인 — Undo가 없는 구조이므로 1회 확인 필수
        sd = self.sequence_data[idx]
        reply = QMessageBox.question(
            self, "Step 삭제",
            f"Step {idx + 1}을 삭제할까요?\n"
            f"({sd['temp']:.0f}\u2103 \u00b7 {sd['rt']:.1f}min \u00b7 {sd['vol']:.1f}mL)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 1. 중앙 데이터 모델에서 삭제
        del self.sequence_data[idx]

        # 2. 카드 제거
        card = self.step_cards.pop(idx)
        self.card_layout.removeWidget(card)
        card.deleteLater()
        self._selected_card = None

        # 3. Step 번호 재정렬
        for i, c in enumerate(self.step_cards):
            c.set_step_num(i + 1)

        self._mark_flow_dirty("step_deleted")
        self._rebuild_steps_grid()

    def copy_step(self):
        """
        선택된 Step을 복사하여 새 Step 추가
        @codesyncer-decision: 조건만 복사, 유속은 재계산 필요
        """
        idx = self._get_selected_index()
        if idx < 0:
            QMessageBox.warning(self, "복사 오류", "복사할 Step을 선택하세요.")
            return

        source_data = self.sequence_data[idx]

        # 새 스텝 추가
        self.add_step()
        new_idx = len(self.sequence_data) - 1
        new_card = self.step_cards[new_idx]

        # 값 복사
        new_card.sp_temp.setValue(source_data['temp'])
        new_card.sp_rt.setValue(source_data['rt'])
        new_card.sp_vol.setValue(source_data['vol'])
        new_card.sp_tube.setValue(source_data['tube_vol'])

        cfg = self.app.cfg
        for pump_name in cfg.INLET_PUMPS:
            pw = new_card.pump_widgets.get(pump_name)
            if pw:
                # 포트 복사 (고정 소스 라우팅은 콤보 없음 → port=1 고정)
                port_val = source_data['pumps'][pump_name]['port']
                cb = pw['cb_port']
                if cb is None:
                    self.sequence_data[new_idx]['pumps'][pump_name]['port'] = 1
                elif (cb_idx := cb.findData(port_val)) >= 0:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(cb_idx)
                    cb.blockSignals(False)
                    self.sequence_data[new_idx]['pumps'][pump_name]['port'] = port_val

                # 당량 복사
                pw['sp_eq'].setValue(source_data['pumps'][pump_name]['eq'])

        if hasattr(self.app, 'log_browser') and self.app.log_browser:
            self.app.signals.sig_log.emit(f"Step {idx+1} 복사됨 → Step {new_idx+1}")
        self._mark_flow_dirty("step_copied")
        self._rebuild_steps_grid()

    # ─── Step 순서 이동 ─────────────────────────────────────────

    def move_step_up(self):
        """선택된 Step을 위로 이동"""
        idx = self._get_selected_index()
        if idx <= 0:
            return
        self._swap_steps(idx, idx - 1)

    def move_step_down(self):
        """선택된 Step을 아래로 이동"""
        idx = self._get_selected_index()
        if idx < 0 or idx >= len(self.step_cards) - 1:
            return
        self._swap_steps(idx, idx + 1)

    def _swap_steps(self, i, j):
        """Step i와 j의 데이터·카드 순서를 교환"""
        # 데이터 교환
        self.sequence_data[i], self.sequence_data[j] = self.sequence_data[j], self.sequence_data[i]

        # 카드 리스트 교환
        self.step_cards[i], self.step_cards[j] = self.step_cards[j], self.step_cards[i]

        # 레이아웃 재배치: 모든 카드 제거 후 순서대로 재삽입 (stretch 유지)
        for card in self.step_cards:
            self.card_layout.removeWidget(card)

        for k, card in enumerate(self.step_cards):
            self.card_layout.insertWidget(k, card)
        # stretch는 마지막에 자동 유지됨 (removeWidget은 stretch를 건드리지 않음)

        # Step 번호 갱신
        for k, card in enumerate(self.step_cards):
            card.set_step_num(k + 1)

        # 이동된 카드 선택 유지
        self.select_card(self.step_cards[j])
        self._mark_flow_dirty("step_reordered")
        self._rebuild_steps_grid()

    # ─── Excel Copy & Paste ──────────────────────────────────────

    # @codesyncer-decision: 엑셀 TSV 열 순서 = 온도 | 시간 | 부피 | 분획부피 | (펌프별: 포트, 당량) ...
    #   펌프 열은 INLET_PUMPS 순서대로 (포트, 당량) 쌍으로 반복.
    #   예: 3펌프 → 온도 | 시간 | 부피 | 분획 | A포트 | A당량 | B포트 | B당량 | C포트 | C당량
    # @codesyncer-inference: 포트 값은 1~12 정수를 기대. 범위 밖이면 기본 2로 클램핑.
    #   당량은 float, 기본 1.0. 파싱 실패 시 기본값 사용.
    # @codesyncer-risk: Ctrl+C 단축키가 시퀀스 탭 전체에 바인딩됨 — 시약 테이블 내
    #   QTableWidget 셀 편집 중 Ctrl+C가 copy_to_clipboard로 가로채질 수 있음.
    #   현재는 QTableWidget이 자체 selection 모드를 쓰므로 충돌 가능성 낮으나,
    #   사용자가 시약 테이블 셀 복사 시 문제 보고하면 QShortcut context를 좁혀야 함.

    def paste_from_excel(self):
        """컨텍스트 분기 Ctrl+V:
        - 포커스가 시약 그리드 안이면 → Tabulator가 범위 붙여넣기(양보)
        - 그 외 → 기존 Step 생성 로직
        """
        if self._focus_in_reagent_grid():
            return  # 시약 그리드는 Tabulator가 Ctrl+V 처리
        self._paste_steps_from_excel()

    def _paste_steps_from_excel(self):
        """클립보드 TSV를 파싱하여 Step들을 생성 (기존 Step 유지, 뒤에 추가)"""
        text = QApplication.clipboard().text()
        if not text or ("\t" not in text and "\n" not in text):
            return

        raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while raw_lines and raw_lines[-1].strip() == "":
            raw_lines.pop()
        if not raw_lines:
            return

        cfg = self.app.cfg
        inlet_pumps = list(getattr(cfg, "INLET_PUMPS", []) or [])
        added = 0

        for line in raw_lines:
            cells = line.split("\t")
            if len(cells) < 4:
                continue

            def _f(idx, default=0.0):
                try:
                    return float(cells[idx].strip().replace(",", "."))
                except (IndexError, ValueError):
                    return default

            temp     = _f(0, 25.0)
            rt       = _f(1, 10.0)
            vol      = _f(2, 5.0)
            tube_vol = _f(3, 1.5)

            # Step 추가
            self.add_step()
            new_idx = len(self.sequence_data) - 1
            card = self.step_cards[new_idx]

            card.sp_temp.setValue(temp)
            card.sp_rt.setValue(rt)
            card.sp_vol.setValue(vol)
            card.sp_tube.setValue(tube_vol)

            # 펌프별 (포트, 당량) 쌍
            for pi, pump_name in enumerate(inlet_pumps):
                base = 4 + pi * 2
                port_raw = _f(base, 2)
                port = max(1, min(12, int(port_raw)))
                eq = _f(base + 1, 1.0)

                pw = card.pump_widgets.get(pump_name)
                if pw:
                    cb = pw['cb_port']
                    if cb is None:
                        self.sequence_data[new_idx]['pumps'][pump_name]['port'] = 1
                    elif (cb_idx := cb.findData(port)) >= 0:
                        cb.blockSignals(True)
                        cb.setCurrentIndex(cb_idx)
                        cb.blockSignals(False)
                        self.sequence_data[new_idx]['pumps'][pump_name]['port'] = port
                    pw['sp_eq'].setValue(eq)

            added += 1

        if added > 0:
            self._mark_flow_dirty("excel_paste")
            self._rebuild_steps_grid()
            if hasattr(self.app, 'log_browser') and self.app.log_browser:
                self.app.signals.sig_log.emit(f"Excel에서 {added}개 Step 붙여넣기 완료")

    def copy_to_clipboard(self):
        """컨텍스트 분기 Ctrl+C:
        - 포커스가 시약 그리드 안이면 → Tabulator가 범위 복사(양보)
        - 그 외 → Step 전체 데이터 TSV 복사
        """
        if self._focus_in_reagent_grid():
            return  # 시약 그리드는 Tabulator가 Ctrl+C 처리

        if not self.sequence_data:
            return

        cfg = self.app.cfg
        inlet_pumps = list(getattr(cfg, "INLET_PUMPS", []) or [])

        # 헤더
        headers = ["온도(℃)", "시간(min)", "부피(mL)", "분획(mL)"]
        for p in inlet_pumps:
            headers.extend([f"{p}_포트", f"{p}_당량"])
        lines = ["\t".join(headers)]

        # 데이터
        for sd in self.sequence_data:
            row = [
                f"{sd['temp']:.1f}",
                f"{sd['rt']:.1f}",
                f"{sd['vol']:.1f}",
                f"{sd['tube_vol']:.1f}",
            ]
            for p in inlet_pumps:
                pd = sd['pumps'].get(p, {'port': 2, 'eq': 1.0})
                row.append(str(pd['port']))
                row.append(f"{pd['eq']:.2f}")
            lines.append("\t".join(row))

        QApplication.clipboard().setText("\n".join(lines))
        if hasattr(self.app, 'log_browser') and self.app.log_browser:
            self.app.signals.sig_log.emit(f"{len(self.sequence_data)}개 Step을 클립보드에 복사함")

    def _update_well_id_label(self, tube_num=None):
        """스핀박스 값에 해당하는 well ID 라벨 업데이트"""
        if tube_num is None:
            tube_num = self.sp_collector_start.value()
        collector = getattr(self.app, "collector", None)
        if collector and hasattr(collector, 'get_well_id'):
            wid = collector.get_well_id(tube_num)
            self.lbl_well_id.setText(f"{wid}")
        else:
            self.lbl_well_id.setText(f"Tube {tube_num}")

    # ─── Map / Port Refresh ────────────────────────────────────

    def on_map_changed(self, pump_name, row, col, table):
        """시약 테이블 변경 시 map_mgr 업데이트 및 Port 콤보박스 갱신"""
        if self.app.map_mgr and row > 0:
            name = table.item(row, 1).text() if table.item(row, 1) else "Empty"
            try:
                conc = float(table.item(row, 2).text()) if table.item(row, 2) else 1.0
            except ValueError:
                conc = 1.0
            smiles = table.item(row, 3).text().strip() if table.item(row, 3) else ""
            self.app.map_mgr.update_inlet(pump_name, row + 1, name, conc, smiles)
            self.refresh_port_combos(pump_name)
            self._mark_flow_dirty("reagent_map_changed")

    def refresh_port_combos(self, pump_name=None):
        """모든 StepCard의 Port 콤보박스 항목 갱신"""
        for card in self.step_cards:
            card.refresh_port_combos(pump_name)

    # ─── Flow Calculation ──────────────────────────────────────

    def calc_flow(self, silent=False):
        """유속 자동 계산 및 StepCard에 결과 표시

        @param silent: True면 자동 재계산 모드 — 에러를 메시지박스 대신 로그로만 표시
        @codesyncer-decision: 누적 튜브 범위 + collector 용량 검증을 계산 단계에서 수행
        - 기존: 용량 초과는 실행 후 엔진 SafetyError로만 발견 (가열까지 끝낸 뒤 중단)
        - 수정: 입력 시점에 카드/요약에 빨간 경고 → 실험 전 사전 인지
        """
        if not self.app.calculator:
            if not silent:
                QMessageBox.warning(self, "오류", "Calculator 모듈이 초기화되지 않았습니다.")
            return

        cfg = self.app.cfg

        # 누적 튜브 배치 계산 준비 (legacy syringe push: 수집 N개 + wash 1개)
        next_tube = self.sp_collector_start.value()
        collector = getattr(self.app, "collector", None)
        max_tubes = int(getattr(collector, "total_tubes", 0) or 0)
        total_est_min = 0.0
        any_over = False

        try:
            for step_idx, step_data in enumerate(self.sequence_data):
                card = self.step_cards[step_idx]
                rt = step_data['rt']
                target_vol = step_data['vol']
                vol_per_tube = step_data['tube_vol']

                # 펌프별 농도/당량 수집
                inlet_concs = []
                inlet_eqs = []
                pump_order = []

                for pump_name in cfg.INLET_PUMPS:
                    pump_data = step_data['pumps'][pump_name]
                    port = pump_data['port']
                    eq = pump_data['eq']

                    # autosampler 그룹은 vial 키로 농도 조회 (Phase A —
                    # 구 코드는 port=1 강제라 항상 slot-1 농도를 읽었음)
                    src_key = pump_data.get('vial') or port
                    info = self.app.map_mgr.get_inlet(pump_name, src_key) if self.app.map_mgr else {'conc': 1.0}
                    inlet_concs.append(info['conc'])
                    inlet_eqs.append(eq)
                    pump_order.append(pump_name)

                # 유속 계산
                if inlet_concs:
                    inlet_flows, inlet_tf = self.app.calculator.calculate_flows(inlet_concs, inlet_eqs, rt)
                else:
                    inlet_flows, inlet_tf = [], 0.0

                # 결과 저장 및 카드 업데이트
                if inlet_tf > 0:
                    collection_time = target_vol / inlet_tf
                    est_time = rt + collection_time
                    num_tubes = math.ceil(target_vol / vol_per_tube) if vol_per_tube > 0 else 0
                    total_est_min += est_time

                    flows_dict = {}
                    for i, pump_name in enumerate(pump_order):
                        flow = inlet_flows[i]
                        step_data['pumps'][pump_name]['flow'] = flow
                        flows_dict[pump_name] = flow

                    # 튜브 범위: first..last + wash
                    first = next_tube
                    last = first + num_tubes - 1
                    wash = last + 1
                    next_tube = wash + 1
                    over = bool(max_tubes and wash > max_tubes)
                    any_over = any_over or over
                    if num_tubes == 1:
                        tube_range = f"Tube {first} · W{wash}"
                    else:
                        tube_range = f"Tube {first}\u2013{last} \u00b7 W{wash}"

                    card.update_results(flows_dict, inlet_tf, est_time, num_tubes,
                                        tube_range=tube_range, over_capacity=over)
                else:
                    card.clear_results()
                    card.update_header_summary()

            # 전체 요약 갱신 (총 시간 / 총 튜브 / 용량 경고)
            self._update_total_summary(total_est_min, next_tube - 1, max_tubes, any_over)

            if not silent and hasattr(self.app, 'log_browser') and self.app.log_browser:
                self.app.signals.sig_log.emit("유속 및 예상 시간/튜브 계산 완료")
            self._clear_flow_dirty()

        except Exception as e:
            print(f"Calc Error: {e}")
            traceback.print_exc()
            if not silent:
                QMessageBox.warning(self, "계산 오류", str(e))

    def _update_total_summary(self, total_min, last_tube, max_tubes, over_capacity):
        """상단 바 전체 요약 라벨 갱신"""
        if not hasattr(self, "lbl_total_summary"):
            return
        n_steps = len(self.sequence_data)
        cap_str = f"/{max_tubes}" if max_tubes else ""
        # @codesyncer(표기 규격): '약'/'~' 근사 기호 제거 — 계측기 표기 (사용자 피드백)
        text = (f"Steps {n_steps} \u00b7 예상 {total_min:.0f} min \u00b7 "
                f"Tube {last_tube}{cap_str}")
        from ui.colors import get_palette
        P = get_palette(getattr(self.app, 'is_dark_mode', True))
        if over_capacity:
            self.lbl_total_summary.setStyleSheet(
                f"font-family: {T.FONT_MONO}; font-size: {T.FS_MD}; "
                f"font-weight: {T.FW_BOLD}; color: {P.STATE_FAULT}; "
                f"background: {P.STATE_FAULT_BG}; border: 1px solid {P.STATE_FAULT}; "
                f"border-radius: {T.R_SM}; padding: 4px 10px;"
            )
            self.lbl_total_summary.setText(f"\u26a0 {text} \u2014 Collector 용량 초과!")
        else:
            self.lbl_total_summary.setStyleSheet(
                f"font-family: {T.FONT_MONO}; font-size: {T.FS_MD}; "
                f"font-weight: {T.FW_SEMI}; color: {P.TEXT_SECONDARY}; padding: 4px 6px;"
            )
            self.lbl_total_summary.setText(text)

    # ─── Run Sequence ──────────────────────────────────────────

    def _export_notebook(self):
        """현재 시퀀스 최종결과 → F-SCH 연구노트 JSON (스텝별). #1·2·3 배선."""
        from PyQt5.QtWidgets import QInputDialog
        from datetime import datetime
        from core.notebook_export import NotebookExporter

        # 최신 유속 반영 후 plan 구성 (실행 없이)
        try:
            self.calc_flow(silent=True)
        except Exception:
            pass
        plan, eqs = NotebookExporter.plan_from_app(self.app)
        if not plan:
            QMessageBox.warning(self, "연구노트 Export",
                                "유속이 계산된 스텝이 없습니다. '유속 계산'을 먼저 실행하세요.")
            return

        note_code, ok = QInputDialog.getText(self, "연구노트", "노트 코드 (noteCode):",
                                             text="F-SCH-001-001")
        if not ok or not note_code.strip():
            return
        revision, ok = QInputDialog.getInt(self, "연구노트", "Revision:", 1, 1, 9999)
        if not ok:
            return
        types = ["Photoredox", "Ni/Photoredox XEC", "Thermal", "Hydrogenation",
                 "C–H activation", "Other"]
        rtype, ok = QInputDialog.getItem(self, "연구노트", "Reaction type:", types, 0, True)
        if not ok:
            return

        # 시약 라이브러리 (계산기 PubChem/수동입력 병합)
        try:
            reagent_lib = self.app.calc_tab.build_reagent_lib()
        except Exception:
            reagent_lib = getattr(self.app, "reagent_lib", {}) or {}

        try:
            paths = NotebookExporter(self.app).save(
                plan, datetime.now(), note_code=note_code.strip(), revision=revision,
                reaction_type=rtype, reagent_lib=reagent_lib, eqs_by_step=eqs,
                recipes_by_port=dict(getattr(self, "stock_recipes", {}) or {}))
            if self.app.log_browser:
                self.app.signals.sig_log.emit(f"[연구노트] {len(paths)}개 F-SCH 저장")
            QMessageBox.information(
                self, "연구노트 Export",
                f"{len(paths)}개 스텝을 F-SCH JSON으로 저장했습니다.\n\n{paths[0] if paths else ''}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "연구노트 Export 실패", str(e))

    def run_seq(self):
        """실험 시퀀스 실행"""
        # @codesyncer-decision: 더블클릭 / 중복 트리거 가드
        #   이미 실행 중인 worker가 있으면 새 시퀀스 시작 금지.
        #   두 SequenceWorker가 병렬로 같은 engine.run_sequence()를 호출하면
        #   RS-485 버스 경쟁 + 펌프 is_refilling race condition 발생.
        existing = getattr(self.app, 'worker', None)
        if existing is not None and existing.isRunning():
            QMessageBox.warning(
                self, "실행 중",
                "이미 시퀀스가 실행 중입니다.\n먼저 정지하거나 완료를 기다려 주세요."
            )
            return

        if not self.app.engine:
            QMessageBox.critical(self, "실행 오류", "엔진이 초기화되지 않아 실행할 수 없습니다.")
            return

        if len(self.sequence_data) == 0:
            QMessageBox.warning(self, "실행 오류", "실행할 Step이 없습니다.")
            return
        if len(getattr(self.app.cfg, "INLET_PUMPS", []) or []) == 0:
            QMessageBox.warning(self, "실행 오류", "구성된 Inlet Pump가 없습니다. Hardware 설정을 먼저 완료하세요.")
            return

        # 입력 변경사항이 있으면 실행 직전에 자동 재계산
        if self.flow_dirty:
            self.calc_flow()
            if self.flow_dirty:
                QMessageBox.warning(self, "실행 오류", "입력 변경사항이 계산에 반영되지 않았습니다. 유속 계산을 확인하세요.")
                return

        # 시스템 파라미터 설정
        sp = self.app.cfg.config_data.get("system_params", {})
        self.app.engine.vol_reactor = self.app.cfg.reactor_vol
        self.app.engine.vol_post_common = sp.get("post_reactor_vol_ml", 2.0)
        self.app.engine.vol_collection = sp.get("collection_line_vol_ml", 1.0)
        self.app.engine.temp_tolerance = float(sp.get("temp_tolerance_c", 0.3))
        self.app.engine.heater_reach_timeout_sec = float(sp.get("heater_reach_timeout_sec", 900.0))
        self.app.engine.max_total_flow = float(sp.get("max_total_flow_ml_min", 100.0))
        self.app.engine.max_step_volume_ml = float(sp.get("max_step_volume_ml", 500.0))
        self.app.engine.wash_mode = self.app.engine._normalize_mode(sp.get("wash_mode", "port_change"), "port_change")
        self.app.engine.prefill_mode = self.app.engine._normalize_mode(sp.get("prefill_mode", "port_change"), "port_change")

        collector_start_tube = self.sp_collector_start.value()

        plan = []
        try:
            for step_data in self.sequence_data:
                d = {
                    "temp": step_data['temp'],
                    "vol_ml": step_data['vol'],
                    "residence_time": step_data['rt'],
                    "inlet_ports": {},
                    "inlet_vials": {},   # autosampler per-step 소스 vial (Phase A)
                    "flows": {},
                    "collect_volume_per_tube": step_data['tube_vol'],
                    "meta": {}
                }

                for pump_name, pump_data in step_data['pumps'].items():
                    port = pump_data['port']
                    flow = pump_data['flow']
                    vial = str(pump_data.get('vial') or '')

                    if (not math.isfinite(flow)) or flow <= 0.0:
                        # @codesyncer-decision: 원인 식별 가능한 에러 메시지
                        # - 기존 "유속을 먼저 계산하세요"는 농도 0(세척용매) 포트를
                        #   시약으로 선택한 경우에도 떠서 사용자가 원인을 못 찾았음
                        info = self.app.map_mgr.get_inlet(pump_name, port) if self.app.map_mgr else {}
                        conc = float(info.get('conc', 0) or 0)
                        if conc <= 0:
                            raise ValueError(
                                f"{pump_name}: Port {port}의 시약 농도가 0입니다. "
                                f"시약 테이블에서 농도를 입력하거나 다른 포트를 선택하세요."
                            )
                        raise ValueError(f"{pump_name}: 유속이 계산되지 않았습니다 (유속 계산 버튼)")

                    src_key = vial or port
                    inlet_info = self.app.map_mgr.get_inlet(pump_name, src_key) if self.app.map_mgr else {'name': '', 'conc': 1.0}
                    d["inlet_ports"][pump_name] = port
                    if vial:
                        d["inlet_vials"][pump_name] = vial
                    d["flows"][pump_name] = flow
                    d["meta"][pump_name] = {"name": inlet_info['name'], "conc": inlet_info['conc']}

                if len(d["flows"]) == 0:
                    raise ValueError("Step에 설정된 펌프 유량이 없습니다. 펌프 구성과 유량 계산을 확인하세요.")

                plan.append(d)

            # 분취 시작 튜브 설정
            self.app.engine.collector_start_tube = collector_start_tube

            # @codesyncer-decision: 진행률 바 범위를 실험 수에 맞게 설정
            self.prog.setMaximum(len(plan))
            self.prog.setValue(0)

            self.app.worker = SequenceWorker(self.app.engine, plan, self.app.map_mgr)
            self.app.worker.engine.signals.sig_progress.connect(self.prog.setValue)
            # 카드 실행상태 배지 갱신 (sig_progress = 완료된 step 수)
            self.app.worker.engine.signals.sig_progress.connect(self._on_step_progress)
            # 실행 시작: Step 1 RUNNING, 나머지 idle
            for i, card in enumerate(self.step_cards):
                card.set_run_state("running" if i == 0 else "idle")

            # @codesyncer-decision: 결과 저장 체크 시 sig_finished에 리포트 생성 연결
            if self.chk_save_results.isChecked():
                from datetime import datetime
                from core.experiment_report import ExperimentReport
                self._exp_start_time = datetime.now()
                self._exp_plan = plan
                self._exp_report = ExperimentReport(self.app)
                self.app.worker.engine.signals.sig_finished.connect(self._on_experiment_finished)

            # @codesyncer-decision: Run 버튼을 시퀀스 실행 동안 비활성화
            #   더블클릭 자체를 UI 레벨에서 차단. QThread.finished 시그널로
            #   완료/abort 어떤 경로로 끝나도 자동 복원.
            self.btn_run.setEnabled(False)
            self.app.worker.finished.connect(self._on_worker_finished)

            self.app.worker.start()

        except Exception as e:
            # @codesyncer-decision: 예외 발생 시 버튼 즉시 복원 — 가드 잠금 방지
            self.btn_run.setEnabled(True)
            QMessageBox.critical(self, "실행 오류", str(e))
            print(f"Run Error: {e}")

    def _on_step_progress(self, completed: int):
        """완료된 step 수(completed)에 따라 카드 배지 갱신:
        0..completed-1 → DONE, completed → RUNNING, 이후 → idle"""
        for i, card in enumerate(self.step_cards):
            if i < completed:
                card.set_run_state("done")
            elif i == completed:
                card.set_run_state("running")
            else:
                card.set_run_state("idle")

    def _on_worker_finished(self):
        """SequenceWorker 종료 시 Run 버튼 복원 + 카드 상태 정리."""
        self.btn_run.setEnabled(True)
        aborted = bool(getattr(self.app.engine, "abort_flag", False))
        for card in self.step_cards:
            if card._run_state == "running":
                # abort/에러로 중단된 step 표시, 정상 완료면 sig_progress가 이미 done 처리
                card.set_run_state("error" if aborted else "done")

    # ─── Experiment Report ─────────────────────────────────────

    def _on_experiment_finished(self):
        """실험 완료 시 결과 저장 (sig_finished에서 호출)"""
        try:
            # 시그널 1회 연결 해제 (다음 실험과 중복 방지)
            self.app.worker.engine.signals.sig_finished.disconnect(self._on_experiment_finished)
        except (TypeError, RuntimeError):
            pass

        if not hasattr(self, '_exp_report') or not self._exp_report:
            return

        # 중단 여부 판별
        status = "aborted" if getattr(self.app.engine, 'abort_flag', False) else "completed"

        try:
            exp_dir = self._exp_report.save(self._exp_plan, self._exp_start_time, status)
            if exp_dir and hasattr(self.app, 'log_browser') and self.app.log_browser:
                self.app.signals.sig_log.emit(f"실험 결과 저장됨: {exp_dir}")
        except Exception as e:
            print(f"[Report] Save error: {e}")

        # 참조 정리
        self._exp_report = None
        self._exp_plan = None
        self._exp_start_time = None

    # ─── Rebuild (Hot Reload) ──────────────────────────────────

    def rebuild(self):
        """
        시퀀스 탭 전체를 재구성합니다.
        @codesyncer-decision: Hot Reload 시 중앙 데이터 모델 보존
        """
        import copy
        saved_data = copy.deepcopy(self.sequence_data)

        # 레이아웃 제거
        old_layout = self.layout()
        if old_layout:
            def clear_layout(layout):
                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                    elif item.layout():
                        clear_layout(item.layout())
            clear_layout(old_layout)
            QWidget().setLayout(old_layout)

        # 데이터 초기화
        self.reagent_tables = {}
        self.sequence_data = []
        self.step_cards = []
        self._selected_card = None

        # UI 재생성 (setup_ui가 기본 Step 1개를 추가함)
        self.setup_ui()

        # @codesyncer-decision: rebuild 시 setup_ui가 만든 기본 step 제거 후 복원
        if saved_data:
            self.clear_all_steps()
            for step_data in saved_data:
                self.add_step()
                step_idx = len(self.sequence_data) - 1
                card = self.step_cards[step_idx]

                # 반응 조건 복원
                card.sp_temp.setValue(step_data.get('temp', 25.0))
                card.sp_rt.setValue(step_data.get('rt', 10.0))
                card.sp_vol.setValue(step_data.get('vol', 5.0))
                card.sp_tube.setValue(step_data.get('tube_vol', 1.5))

                # 펌프 설정 복원 (port/eq/flow)
                for pump_name, pump_data in step_data.get('pumps', {}).items():
                    self.sequence_data[step_idx]['pumps'][pump_name]['port'] = pump_data.get('port', 2)
                    self.sequence_data[step_idx]['pumps'][pump_name]['eq'] = pump_data.get('eq', 1.0)
                    self.sequence_data[step_idx]['pumps'][pump_name]['flow'] = pump_data.get('flow', 0.0)

                    pw = card.pump_widgets.get(pump_name)
                    if pw:
                        cb = pw['cb_port']
                        if cb is None:
                            self.sequence_data[step_idx]['pumps'][pump_name]['port'] = 1
                        elif (idx := cb.findData(pump_data.get('port', 2))) >= 0:
                            cb.blockSignals(True)
                            cb.setCurrentIndex(idx)
                            cb.blockSignals(False)
                        pw['sp_eq'].setValue(pump_data.get('eq', 1.0))

        # main.py의 reagent_tables 참조 동기화
        self.app.reagent_tables = self.reagent_tables

        # 핫 리로드 후 collector 종류가 바뀌었을 수 있음 → 시작 튜브 상한 재설정
        collector = getattr(self.app, "collector", None)
        total = int(getattr(collector, "total_tubes", 0) or 0)
        if total > 0:
            current_val = self.sp_collector_start.value()
            self.sp_collector_start.setRange(1, total)
            if current_val > total:
                self.sp_collector_start.setValue(1)

        # Well ID 라벨 초기 표시
        self._update_well_id_label()

        print(f"[rebuild] 시퀀스 탭 재구성 완료 - {len(self.sequence_data)} steps")

    # ─── Spiral Preview ──────────────────────────────────────────

    def show_spiral_preview(self):
        """나선형 분취 튜브 배치도 다이얼로그 표시.

        각 StepCard의 반응부피/분획부피로 소요 튜브 수를 계산하고,
        나선형 회전판 위에 Step별 색상으로 시각화한다.
        """
        start_tube = self.sp_collector_start.value()

        _cfg = getattr(self.app, "cfg", None)
        _wpt = (_cfg.collect_wash_tubes()
                if (_cfg is not None and hasattr(_cfg, "collect_wash_tubes")) else 1)
        step_infos = []
        for i, step_data in enumerate(self.sequence_data):
            vol = step_data["vol"]
            tube_vol = step_data["tube_vol"]
            num_tubes = math.ceil(vol / tube_vol) if tube_vol > 0 else 0
            step_infos.append({
                "name": f"Step {i + 1}",
                "num_tubes": num_tubes,
                "wash_tubes": _wpt,
                "vol_per_tube": tube_vol,
                "total_vol": vol,
            })

        if not step_infos:
            QMessageBox.information(self, "Fluid Tracker", "Step이 없습니다.")
            return

        # Collector 종류에 따라 다른 미리보기 다이얼로그 사용
        collector = getattr(self.app, "collector", None)
        if collector and type(collector).__name__ == "Plate96Collector":
            from ui.dialog_plate96_preview import Plate96PreviewDialog
            # 실제 로드된 랙 격자를 미리보기에 반영 (좌표 데이터가 진실원)
            _rows, _cols, _label = self._rack_geometry(collector)
            dlg = Plate96PreviewDialog(start_tube, step_infos, parent=self,
                                       rows=_rows, cols=_cols, rack_label=_label)
        else:
            from ui.dialogs import SpiralPreviewDialog
            dlg = SpiralPreviewDialog(start_tube, step_infos, parent=self)
        dlg.exec_()

    # ─── 랙 (분취 플레이트/튜브 랙) ────────────────────────────
    @staticmethod
    def _rack_geometry(collector):
        """로드된 좌표 데이터에서 (rows, cols, 라벨) 유도 — 하드코딩 금지.

        @codesyncer-decision(2026-08-05): 격자 크기의 진실원은 좌표 JSON.
          well_sequence 의 row_idx/col_idx 최대값으로 역산해 96-well(8×12)·
          에펜 랙(5×5) 등 어떤 랙이든 미리보기가 자동으로 맞는다."""
        try:
            wells = [w for w in getattr(collector, "well_sequence", [])
                     if w.get("plate") == "A"]
            if wells:
                rows = max(w["row_idx"] for w in wells) + 1
                cols = max(w["col_idx"] for w in wells) + 1
                label = getattr(collector, "rack_label", None) or "96-Well Plate"
                return rows, cols, label
        except Exception:
            pass
        return 8, 12, "96-Well Plate"

    def _rack_options(self):
        """선택 가능한 랙 목록 [(라벨, rack_type)] — data/ 의 좌표 파일 존재 기준."""
        import os
        opts = [("96-Well Plate (8×12)", "plate96")]
        data_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "hardware", "collectors", "data")
        try:
            for fn in sorted(os.listdir(data_dir)):
                if not (fn.startswith("well_coordinates_") and fn.endswith(".json")):
                    continue
                rack = fn[len("well_coordinates_"):-len(".json")]
                label = rack
                try:
                    import json
                    with open(os.path.join(data_dir, fn), encoding="utf-8") as f:
                        meta = (json.load(f).get("rack") or {})
                    label = (f"{meta.get('display_name', rack)} "
                             f"({meta.get('rows', '?')}×{meta.get('cols', '?')})")
                except Exception:
                    pass
                opts.append((label, rack))
        except Exception:
            pass
        return opts

    def _current_rack_type(self):
        cfg = getattr(self.app, "cfg", None)
        try:
            c_id = ((cfg.config_data.get("roles", {}) or {})
                    .get("collector", {}) or {}).get("driver_id")
            dev = cfg.get_device_info(c_id) or {}
            return str((dev.get("settings") or {}).get("rack_type", "plate96")
                       or "plate96")
        except Exception:
            return "plate96"

    def _on_rack_changed(self, idx):
        """랙 선택 → config 저장 + 분취기 좌표 재로드 (기존 이동 로직 그대로 사용)."""
        from PyQt5.QtWidgets import QMessageBox
        rack = self.cb_rack.itemData(idx)
        if rack is None or rack == self._current_rack_type():
            return
        cfg = getattr(self.app, "cfg", None)
        collector = getattr(self.app, "collector", None)
        if cfg is None:
            return
        try:
            c_id = ((cfg.config_data.get("roles", {}) or {})
                    .get("collector", {}) or {}).get("driver_id")
            dev = next((d for d in cfg.config_data.get("inventory", [])
                        if d.get("id") == c_id), None)
            if dev is None:
                QMessageBox.warning(self, "랙 변경", "분취기 장치가 설정에 없습니다.")
                self._sync_rack_combo()
                return
            dev.setdefault("settings", {})["rack_type"] = rack
            cfg.save_config(cfg.config_data.get("inventory", []),
                            cfg.config_data.get("roles", {}),
                            cfg.config_data.get("system_params", {}))
            # 실행 중인 분취기 드라이버에 즉시 반영 (재연결 없이 좌표만 교체)
            if collector is not None and hasattr(collector, "reload_data"):
                import os
                data_dir = os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "hardware", "collectors", "data")
                fname = ("well_coordinates.json" if rack == "plate96"
                         else f"well_coordinates_{rack}.json")
                collector.coords_path = os.path.join(data_dir, fname)
                collector.reload_data()
                # 위치 재확인 필요 — 좌표계가 바뀌었으므로 홈 기준으로 리셋
                collector.current_position = 0
                collector._motion_confirmed = False
                n = getattr(collector, "total_tubes", 0)
                self.app.signals.sig_log.emit(
                    f"[분취기] 랙 변경: {rack} ({n} 튜브) — 다음 이동 전 HOME 권장")
                self.sp_collector_start.setMaximum(max(1, n))
                self._update_well_id_label()
        except Exception as e:
            QMessageBox.warning(self, "랙 변경 실패", str(e))
            self._sync_rack_combo()

    def _sync_rack_combo(self):
        if not hasattr(self, "cb_rack"):
            return
        cur = self._current_rack_type()
        i = self.cb_rack.findData(cur)
        self.cb_rack.blockSignals(True)
        self.cb_rack.setCurrentIndex(i if i >= 0 else 0)
        self.cb_rack.blockSignals(False)

    # ─── Theme Support ─────────────────────────────────────────

    def _apply_button_styles(self, P):
        """실행/미리보기/툴바 버튼 스타일 적용 — 3단계 시각 위계"""
        from ui.theme import (get_secondary_button_style, get_segment_track_style,
                              get_tool_button_style, get_caption_style)

        # 툴바 카드 스트립 + 세그먼트 트랙 (Manual 탭과 동일 시각 언어)
        if getattr(self, "toolbar_frame", None) is not None:
            self.toolbar_frame.setStyleSheet(
                f"QFrame#SeqToolbar {{ background: {P.BG_MONITOR}; "
                f"border: 1px solid {P.BORDER_SECONDARY}; border-radius: {T.R_LG}; }}"
                + get_segment_track_style(P))

        # 요약/분취 라벨 — 모노 메트릭 칩 + 캡션 규격
        if getattr(self, "lbl_total_summary", None) is not None:
            self.lbl_total_summary.setStyleSheet(
                f"font-family: {T.FONT_MONO}; font-size: {T.FS_SM}; "
                f"font-weight: {T.FW_SEMI}; color: {P.TEXT_SECONDARY}; "
                f"background: {P.BG_INPUT}; border: 1px solid {P.BORDER_PRIMARY}; "
                f"border-radius: {T.R_SM}; padding: 5px 10px;")
        if getattr(self, "lbl_start_tube_seq", None) is not None:
            self.lbl_start_tube_seq.setText("분취 시작")
            self.lbl_start_tube_seq.setStyleSheet(get_caption_style(P))

        # Primary: 실험 시작 (화면 유일의 채움 버튼)
        self.btn_run.setStyleSheet(f"""
            QPushButton {{
                background: {P.ACCENT_BLUE};
                color: white;
                padding: 12px 20px;
                font-size: {T.FS_LG};
                font-weight: {T.FW_BOLD};
                border: 1px solid {P.ACCENT_BLUE_DARK};
                border-radius: {T.R_MD};
            }}
            QPushButton:hover {{ background: {P.ACCENT_BLUE_DARK}; }}
            QPushButton:pressed {{ background: {P.ACCENT_BLUE_DARK}; }}
            QPushButton:disabled {{
                background: {P.BG_DISABLED};
                color: {P.TEXT_DISABLED};
                border-color: {P.BORDER_SECONDARY};
            }}
        """)

        # Secondary: 아웃라인 (유속 계산, Fluid Tracker)
        sec_css = get_secondary_button_style(P)
        self.btn_preview.setStyleSheet(sec_css)
        for b in getattr(self, "_secondary_btns", []):
            b.setStyleSheet(sec_css)

        # 툴 버튼: 트랙 안 플랫 텍스트 + hover pill (기존 ghost 대체)
        tool_css = get_tool_button_style(P)
        for b in getattr(self, "_ghost_btns", []):
            b.setStyleSheet(tool_css)

    def apply_theme(self, is_dark=True):
        """테마 전환 지원"""
        P = Dark if is_dark else Light

        self.lbl_start_tube_seq.setStyleSheet(f"color: {P.TEXT_PRIMARY}; font-weight: {T.FW_SEMI}; font-size: {T.FS_MD};")
        self._apply_button_styles(P)

        for card in self.step_cards:
            if card._selected:
                card.set_selected(True)
            else:
                card._apply_normal_style()
            for sep in card._separators:
                sep.setStyleSheet(f"background: {P.SEPARATOR};")
            # 의미색 칩/요약/저채도 요소를 새 팔레트로 재적용
            card._style_summary_label()
            card._style_step_label()
            card._apply_quiet_styles()
            card.set_run_state(card._run_state)

        # 전체 요약 라벨 색 갱신 (재계산으로 일괄 반영)
        if self.sequence_data:
            self.calc_flow(silent=True)

        # 시약 · 스텝 WebGrid 팔레트 재주입
        if getattr(self, 'reagent_grid', None) is not None:
            self.reagent_grid.set_theme(self._reagent_theme(is_dark))
        if getattr(self, 'steps_grid', None) is not None:
            self.steps_grid.set_theme(self._reagent_theme(is_dark))
