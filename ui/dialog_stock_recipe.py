# -*- coding: utf-8 -*-
"""다성분 Stock 레시피 편집 다이얼로그.

@codesyncer-decision(2026-07-13): '혼합 = 포트의 속성' 설계 —
  시약 그리드는 1줄(레시피명+×N 배지+자식행 뷰)만 담당하고, 성분 편집은
  이 다이얼로그가 전담(그리드 컬럼과 성분 컬럼이 달라 인라인 편집 부적합).
  양론은 engine/stock_stoich.compute_stock 순수엔진 — limiting 앵커(mmol/mass)
  입력, 나머지 eq 비례 자동계산. 프리셋(레시피 라이브러리)으로 HTE 반복 제거.

recipe dict 형식은 stock_stoich 참조. 반환: exec 후 self.recipe (None=취소).
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QLineEdit, QDoubleSpinBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QCheckBox, QComboBox, QMessageBox, QWidget,
                             QAbstractItemView)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from ui.colors import DarkPalette as Dark, LightPalette as Light, T
from engine.stock_stoich import compute_stock

# 컬럼: 0=Lim 1=시약명 2=MW 3=eq 4=mmol 5=mass(mg) 6=density 7=vol(mL) 8=M
COLS = ["기준", "시약명", "MW (g/mol)", "eq", "mmol", "질량 (mg)",
        "밀도 (g/mL)", "부피 (mL)", "농도 (M)"]
C_LIM, C_NAME, C_MW, C_EQ, C_MMOL, C_MASS, C_DENS, C_VOL, C_M = range(9)
EDITABLE = {C_NAME, C_MW, C_EQ, C_DENS}          # 항상 편집
ANCHOR = {C_MMOL, C_MASS}                        # limiting 행에서만 편집
DERIVED = {C_VOL, C_M}                           # 항상 자동


class StockRecipeDialog(QDialog):
    def __init__(self, pump, port, recipe=None, presets=None,
                 reagent_lib=None, is_dark=True, parent=None,
                 on_presets_changed=None):
        super().__init__(parent)
        self.pump, self.port = pump, port
        self.presets = dict(presets or {})       # {이름: recipe}
        self.reagent_lib = reagent_lib or {}     # {이름: {mw, density, smiles}}
        self.recipe = None                       # accept 시 결과
        # 프리셋은 '라이브러리'(포트 레시피와 별개) — 저장/삭제 즉시 영속화 콜백.
        # 다이얼로그를 취소해도 프리셋 변경은 유지되는 게 전문 관례(라이브러리 편집).
        self._on_presets_changed = on_presets_changed
        self._P = Dark if is_dark else Light
        self._building = False
        self.setWindowTitle(f"혼합 Stock 레시피 — {pump} · Port {port}")
        self.setMinimumSize(760, 420)
        self._setup_ui()
        self._load_recipe(recipe or self._blank_recipe())
        self._apply_style()

    # ── 데이터 ↔ UI ─────────────────────────────────────────
    @staticmethod
    def _blank_recipe():
        return {"name": "", "total_volume_ml": 5.0,
                "solvents": [{"name": "", "ratio": 1.0}],
                "components": [
                    {"reagent": "", "mw": 0.0, "eq": 1.0, "limiting": True,
                     "mmol": 0.0, "density": 0.0},
                ]}

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 12, 14, 12)

        # ── 상단: 레시피명 + 프리셋 ──
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("레시피명"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("예: XEC 촉매 칵테일")
        top.addWidget(self.ed_name, 2)
        top.addSpacing(10)
        lbl_pre = QLabel("프리셋")
        lbl_pre.setStyleSheet(f"color:{self._P.TEXT_SECONDARY};")
        top.addWidget(lbl_pre)
        self.cb_preset = QComboBox()
        self.cb_preset.addItem("— 선택 —")
        for name in sorted(self.presets):
            self.cb_preset.addItem(name)
        self.cb_preset.setToolTip("저장된 배합을 불러옵니다 — 실험/포트 간 재사용")
        self.cb_preset.currentTextChanged.connect(self._on_preset_pick)
        top.addWidget(self.cb_preset, 1)
        self.btn_save_preset = QPushButton("저장")
        self.btn_save_preset.setToolTip("현재 배합을 레시피명으로 프리셋에 저장")
        self.btn_save_preset.clicked.connect(self._save_preset)
        top.addWidget(self.btn_save_preset)
        self.btn_del_preset = QPushButton("삭제")
        self.btn_del_preset.setToolTip("콤보에서 선택된 프리셋을 라이브러리에서 제거")
        self.btn_del_preset.clicked.connect(self._del_preset)
        top.addWidget(self.btn_del_preset)
        root.addLayout(top)

        # ── 용매 + 총부피 ──
        sol = QHBoxLayout()
        sol.setSpacing(8)
        sol.addWidget(QLabel("용매"))
        self.ed_solvent = QLineEdit()
        self.ed_solvent.setPlaceholderText("예: DMA  또는  DMA:1, THF:3 (이름:비율)")
        sol.addWidget(self.ed_solvent, 2)
        sol.addSpacing(10)
        sol.addWidget(QLabel("총 부피"))
        self.sp_total = QDoubleSpinBox()
        self.sp_total.setRange(0.0, 10000.0)
        self.sp_total.setDecimals(3)
        self.sp_total.setSuffix(" mL")
        self.sp_total.setValue(5.0)
        self.sp_total.valueChanged.connect(self._recalc)
        sol.addWidget(self.sp_total)
        self.lbl_conc = QLabel("기준 —")
        self.lbl_conc.setStyleSheet(
            f"font-weight:{T.FW_BOLD}; color:{self._P.ACCENT_BLUE};")
        sol.addWidget(self.lbl_conc)
        sol.addStretch()
        root.addLayout(sol)

        # ── 성분 테이블 ──
        self.tbl = QTableWidget(0, len(COLS))
        self.tbl.setHorizontalHeaderLabels(COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.verticalHeader().setDefaultSectionSize(30)   # 힛타겟/가독 행높이
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        for c in (C_LIM, C_MW, C_EQ, C_MMOL, C_MASS, C_DENS, C_VOL, C_M):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.tbl.cellChanged.connect(self._on_cell)
        root.addWidget(self.tbl, 1)

        # ── 행 버튼 + 에러 라벨 ──
        rowbar = QHBoxLayout()
        btn_add = QPushButton("+ 성분")
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("− 성분")
        btn_del.clicked.connect(self._del_row)
        rowbar.addWidget(btn_add)
        rowbar.addWidget(btn_del)
        self.lbl_err = QLabel("")
        self.lbl_err.setStyleSheet(f"color:{self._P.ACCENT_RED}; font-size:{T.FS_SM};")
        rowbar.addWidget(self.lbl_err, 1)
        root.addLayout(rowbar)

        # ── 하단 버튼 ──
        btns = QHBoxLayout()
        hint = QLabel("기준(●) 성분의 mmol 또는 질량을 입력하면 나머지는 eq 비례로 자동계산됩니다.")
        hint.setStyleSheet(f"color:{self._P.TEXT_SECONDARY}; font-size:{T.FS_SM};")
        btns.addWidget(hint, 1)
        btn_clear = QPushButton("혼합 해제")
        btn_clear.setToolTip("이 포트의 레시피를 제거하고 단일시약으로 되돌립니다")
        btn_clear.clicked.connect(self._clear_recipe)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("적용")
        btn_ok.setDefault(True)
        btn_ok.setStyleSheet(
            f"background:{self._P.ACCENT_BLUE}; color:white; "
            f"font-weight:{T.FW_BOLD}; padding:6px 20px; border-radius:4px;")
        btn_ok.clicked.connect(self._accept_recipe)
        btns.addWidget(btn_clear)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        root.addLayout(btns)

    def _apply_style(self):
        P = self._P
        self.setStyleSheet(
            f"QDialog {{ background:{P.BG_PRIMARY}; color:{P.TEXT_PRIMARY}; }}"
            f"QLabel {{ color:{P.TEXT_PRIMARY}; font-size:{T.FS_SM}; }}"
            f"QLineEdit, QDoubleSpinBox, QComboBox {{ background:{P.BG_INPUT}; "
            f"color:{P.TEXT_PRIMARY}; border:1px solid {P.BORDER_SECONDARY}; "
            f"border-radius:4px; padding:4px 6px; font-size:{T.FS_SM}; }}"
            f"QPushButton {{ background:{P.BG_TERTIARY}; color:{P.TEXT_PRIMARY}; "
            f"border:1px solid {P.BORDER_SECONDARY}; border-radius:4px; "
            f"padding:5px 12px; font-size:{T.FS_SM}; }}"
            f"QPushButton:hover {{ border-color:{P.ACCENT_BLUE}; }}"
            f"QTableWidget {{ background:{P.BG_SECONDARY}; color:{P.TEXT_PRIMARY}; "
            f"gridline-color:{P.BORDER_SECONDARY}; font-size:{T.FS_SM}; "
            f"alternate-background-color:{P.BG_ALTERNATE}; }}"
            f"QHeaderView::section {{ background:{P.HEADER_BG}; color:{P.HEADER_TEXT}; "
            f"border:none; padding:5px 8px; font-weight:{T.FW_SEMI}; }}")

    # ── 테이블 구성/파싱 ─────────────────────────────────────
    def _load_recipe(self, rec):
        self._building = True
        # 인라인(그리드) 성분의 SMILES 는 다이얼로그에 컬럼이 없음 — 이름으로 보존/재부착
        self._smiles_by_name = {c.get("reagent", ""): c.get("smiles", "")
                                for c in rec.get("components") or [] if c.get("smiles")}
        self.ed_name.setText(rec.get("name", "") or "")
        self.sp_total.setValue(float(rec.get("total_volume_ml") or 0.0))
        self.ed_solvent.setText(", ".join(
            (f"{s.get('name')}:{s.get('ratio', 1):g}" if float(s.get('ratio', 1) or 1) != 1
             else str(s.get('name') or ""))
            for s in rec.get("solvents") or [] if s.get("name")))
        self.tbl.setRowCount(0)
        for c in rec.get("components") or []:
            self._append_row(c)
        if self.tbl.rowCount() == 0:
            self._append_row(self._blank_recipe()["components"][0])
        self._building = False
        self._recalc()

    def _append_row(self, c):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        chk = QCheckBox()
        chk.setChecked(bool(c.get("limiting")))
        chk.toggled.connect(lambda on, row=r: self._on_limiting(row, on))
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setAlignment(Qt.AlignCenter)
        wl.addWidget(chk)
        self.tbl.setCellWidget(r, C_LIM, w)

        def num(v, fmt="{:g}"):
            v = float(v or 0.0)
            return fmt.format(v) if v else ""
        vals = {C_NAME: c.get("reagent", "") or "", C_MW: num(c.get("mw")),
                C_EQ: num(c.get("eq", 1.0)) or "1", C_MMOL: num(c.get("mmol")),
                C_MASS: num(float(c.get("mass_g") or 0.0) * 1000.0),
                C_DENS: num(c.get("density")), C_VOL: "",
                # 인라인(molarity-only) 성분 보존 — M 셀에 실어 파싱 시 재수거
                C_M: num(c.get("molarity"))}
        for col in (C_NAME, C_MW, C_EQ, C_MMOL, C_MASS, C_DENS, C_VOL, C_M):
            it = QTableWidgetItem(vals[col])
            if col in DERIVED:
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setForeground(QColor(self._P.TEXT_SECONDARY))
            self.tbl.setItem(r, col, it)

    def _row_limiting(self, r):
        w = self.tbl.cellWidget(r, C_LIM)
        chk = w.findChild(QCheckBox) if w else None
        return bool(chk and chk.isChecked())

    def _set_limiting(self, r, on):
        w = self.tbl.cellWidget(r, C_LIM)
        chk = w.findChild(QCheckBox) if w else None
        if chk:
            chk.blockSignals(True)
            chk.setChecked(on)
            chk.blockSignals(False)

    def _parse_components(self):
        comps = []
        for r in range(self.tbl.rowCount()):
            def f(col):
                it = self.tbl.item(r, col)
                try:
                    return float((it.text() if it else "").replace(",", "") or 0.0)
                except ValueError:
                    return 0.0
            name = (self.tbl.item(r, C_NAME).text() if self.tbl.item(r, C_NAME) else "").strip()
            comp = {"reagent": name, "mw": f(C_MW), "eq": f(C_EQ) or 1.0,
                    "limiting": self._row_limiting(r),
                    "mmol": f(C_MMOL), "mass_g": f(C_MASS) / 1000.0,
                    "density": f(C_DENS)}
            # 인라인(molarity-only) 성분: mmol/질량 앵커가 없으면 M 셀 값을 명시 농도로
            if comp["mmol"] <= 0 and comp["mass_g"] <= 0 and f(C_M) > 0:
                comp["molarity"] = f(C_M)
            smi = getattr(self, "_smiles_by_name", {}).get(name)
            if smi:
                comp["smiles"] = smi
            comps.append(comp)
        return comps

    def _parse_solvents(self):
        out = []
        for tok in (self.ed_solvent.text() or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            if ":" in tok:
                n, _, ratio = tok.partition(":")
                try:
                    out.append({"name": n.strip(), "ratio": float(ratio)})
                except ValueError:
                    out.append({"name": n.strip(), "ratio": 1.0})
            else:
                out.append({"name": tok, "ratio": 1.0})
        return out

    def current_recipe(self):
        return {"name": self.ed_name.text().strip(),
                "total_volume_ml": self.sp_total.value(),
                "solvents": self._parse_solvents(),
                "components": self._parse_components()}

    # ── 이벤트 ──────────────────────────────────────────────
    def _on_limiting(self, row, on):
        if self._building:
            return
        if on:                                   # 단일 limiting 유지
            for r in range(self.tbl.rowCount()):
                if r != row:
                    self._set_limiting(r, False)
        self._recalc()

    def _on_cell(self, r, c):
        if self._building:
            return
        # 시약명 입력 → 계산기 라이브러리에서 MW/밀도 자동 채움 (비어있을 때만)
        if c == C_NAME:
            name = (self.tbl.item(r, C_NAME).text() or "").strip()
            lib = self.reagent_lib.get(name)
            if lib:
                self._building = True
                if not (self.tbl.item(r, C_MW) and self.tbl.item(r, C_MW).text().strip()):
                    self.tbl.item(r, C_MW).setText(f"{float(lib.get('mw') or 0):g}")
                if lib.get("density") and not self.tbl.item(r, C_DENS).text().strip():
                    self.tbl.item(r, C_DENS).setText(f"{float(lib['density']):g}")
                self._building = False
        # 앵커 상호배타: mmol 입력 시 mass 비움, mass 입력 시 mmol 비움 (limiting 행)
        if c in ANCHOR and self._row_limiting(r):
            other = C_MASS if c == C_MMOL else C_MMOL
            it = self.tbl.item(r, c)
            if it and it.text().strip():
                self._building = True
                self.tbl.item(r, other).setText("")
                self._building = False
        self._recalc()

    def _add_row(self):
        self._building = True
        self._append_row({"reagent": "", "mw": 0.0, "eq": 1.0})
        self._building = False
        self._recalc()

    def _del_row(self):
        r = self.tbl.currentRow()
        if r < 0:
            r = self.tbl.rowCount() - 1
        if self.tbl.rowCount() > 1 and r >= 0:
            self.tbl.removeRow(r)
            self._recalc()

    def _on_preset_pick(self, name):
        if self._building or name not in self.presets:
            return
        self._load_recipe(self.presets[name])

    def _flash(self, msg, ok=True):
        """인라인 상태 피드백 — 프리셋 저장/삭제에 모달 금지(전문 관례)."""
        color = self._P.ACCENT_GREEN if ok else self._P.ACCENT_RED
        self.lbl_err.setStyleSheet(f"color:{color}; font-size:{T.FS_SM};")
        self.lbl_err.setText(msg)

    def _notify_presets(self):
        if callable(self._on_presets_changed):
            try:
                self._on_presets_changed(dict(self.presets))
            except Exception:
                pass

    def _save_preset(self):
        rec = self.current_recipe()
        if not rec["name"]:
            self._flash("레시피명을 먼저 입력하세요", ok=False)
            self.ed_name.setFocus()
            return
        self.presets[rec["name"]] = rec
        if self.cb_preset.findText(rec["name"]) < 0:
            self.cb_preset.addItem(rec["name"])
        self._building = True                     # 재로드 트리거 방지
        self.cb_preset.setCurrentText(rec["name"])
        self._building = False
        self._notify_presets()
        self._flash(f"프리셋 '{rec['name']}' 저장됨 — 다른 포트/실험에서 재사용 가능")

    def _del_preset(self):
        name = self.cb_preset.currentText()
        if name not in self.presets:
            self._flash("삭제할 프리셋을 콤보에서 선택하세요", ok=False)
            return
        self.presets.pop(name, None)
        idx = self.cb_preset.findText(name)
        if idx >= 0:
            self._building = True
            self.cb_preset.removeItem(idx)
            self.cb_preset.setCurrentIndex(0)
            self._building = False
        self._notify_presets()
        self._flash(f"프리셋 '{name}' 삭제됨 (현재 편집 중인 배합은 유지)")

    # ── 재계산/적용 ─────────────────────────────────────────
    def _recalc(self):
        if self._building:
            return
        rec = compute_stock(self.current_recipe())
        self._building = True
        for r, c in enumerate(rec["components"]):
            if r >= self.tbl.rowCount():
                break
            lim = c.get("limiting")
            # 파생값 표시 (limiting 앵커 셀은 사용자 입력 보존)
            if not lim:
                self.tbl.item(r, C_MMOL).setText(f"{c['mmol']:.4g}" if c['mmol'] else "")
                self.tbl.item(r, C_MASS).setText(
                    f"{c['mass_g'] * 1000:.4g}" if c['mass_g'] else "")
                # molarity-명시 성분은 eq 가 농도비로 역산됨 → 표시 동기 (파싱 안정성)
                if c.get("eq"):
                    self.tbl.item(r, C_EQ).setText(f"{c['eq']:.4g}")
                for col in (C_MMOL, C_MASS):
                    self.tbl.item(r, col).setForeground(QColor(self._P.TEXT_SECONDARY))
            else:
                # limiting: 비어있는 앵커(파생된 쪽)만 채움
                if not self.tbl.item(r, C_MMOL).text().strip():
                    self.tbl.item(r, C_MMOL).setText(f"{c['mmol']:.4g}" if c['mmol'] else "")
                if not self.tbl.item(r, C_MASS).text().strip():
                    self.tbl.item(r, C_MASS).setText(
                        f"{c['mass_g'] * 1000:.4g}" if c['mass_g'] else "")
                for col in (C_MMOL, C_MASS):
                    self.tbl.item(r, col).setForeground(QColor(self._P.TEXT_PRIMARY))
            self.tbl.item(r, C_VOL).setText(f"{c['vol_ml']:.3g}" if c['vol_ml'] else "")
            self.tbl.item(r, C_M).setText(f"{c['molarity']:.4g}" if c['molarity'] else "")
        self._building = False
        self.lbl_conc.setText(f"기준 {rec['conc_m']:.4g} M" if rec["conc_m"] else "기준 —")
        if rec["errors"]:
            self.lbl_err.setStyleSheet(
                f"color:{self._P.ACCENT_RED}; font-size:{T.FS_SM};")
            self.lbl_err.setText(" · ".join(rec["errors"]))
        elif not self.lbl_err.text().startswith("프리셋"):
            self.lbl_err.setText("")

    def _clear_recipe(self):
        self.recipe = {}          # 빈 dict = '레시피 제거' 신호
        self.accept()

    def _accept_recipe(self):
        rec = compute_stock(self.current_recipe())
        if not rec["valid"]:
            QMessageBox.warning(self, "레시피", "입력 확인:\n" + "\n".join(rec["errors"]))
            return
        if not rec["name"]:
            rec["name"] = " + ".join(
                c["reagent"] for c in rec["components"] if c["reagent"])[:40] or "혼합 stock"
        self.recipe = rec
        self.accept()
