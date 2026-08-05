# -*- coding: utf-8 -*-
"""Deep Wash 옵션 다이얼로그 — Manual 탭 [DEEP WASH] 버튼에서 호출.

@codesyncer-context: 매뉴얼 탭 '펌프 버튼 = 밸브 무개입' 원칙(2026-07-31)과
  구분되는 별도 자동화 루틴 — 명시적으로 라벨링된 오케스트레이션 버튼이며
  개별 장비 버튼에 밸브 자동 정렬을 숨기는 것이 아님 (사용자 요청 2026-08-05).
  UI 문구는 매뉴얼 탭 영문 통일 규칙을 따름.
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QCheckBox, QSpinBox, QDoubleSpinBox, QLabel,
                             QPushButton, QGroupBox)
from PyQt5.QtCore import Qt

from core.deep_wash import DeepWashOptions


def _abbr(name):
    """'Group A'/'Group_D' → 'A'/'D' (유량 요약 표기용)."""
    s = str(name).replace("Group", "").strip(" _")
    return s or str(name)


def _rate_summary(rates):
    """{그룹명: 유량} → 'A·B·C 8 / D 1.7' (전 그룹 동일하면 '8')."""
    if not rates:
        return ""
    by_val = {}
    for n, v in rates.items():
        by_val.setdefault(round(float(v), 2), []).append(_abbr(n))
    if len(by_val) == 1:
        return f"{next(iter(by_val)):g}"
    return " / ".join(f"{'·'.join(ns)} {v:g}" for v, ns in sorted(
        by_val.items(), key=lambda kv: -kv[0]))


class DeepWashDialog(QDialog):
    """세척 대상 그룹/포트 범위/볼륨 설정 후 START 로 확정."""

    def __init__(self, groups, parent=None):
        """@param groups: {그룹명: SmartPump} — 그룹별 기본 유량 표시용.
        (하위호환: 그룹명 리스트도 허용 — 유량 숫자 미표시)"""
        if isinstance(groups, dict):
            group_names = list(groups)
            self._wash_rates = {n: float(getattr(p, "wash_speed", 0) or 0)
                                for n, p in groups.items()}
            self._flush_rates = {n: float(getattr(p, "prime_rate", 0) or 0)
                                 for n, p in groups.items()}
        else:
            group_names = list(groups)
            self._wash_rates = {}
            self._flush_rates = {}
        super().__init__(parent)
        self.setWindowTitle("Deep Wash — All Lines (Ports 2–11)")
        self.setModal(True)
        self._accepted = False

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ── 준비 안내 (전제조건) ──────────────────────────────────
        prep = QLabel(
            "Preparation checklist:\n"
            "  1. Fill wash-solvent vials at every target port (2–11).\n"
            "  2. Port 1 = solvent reservoir, Port 12 = waste line.\n"
            "  3. Outlet valve will be forced to WASTE.")
        prep.setWordWrap(True)
        lay.addWidget(prep)

        # ── 그룹 선택 ─────────────────────────────────────────────
        g_groups = QGroupBox("Groups")
        gh = QHBoxLayout(g_groups)
        self._group_checks = {}
        for name in group_names:
            cb = QCheckBox(name)
            cb.setChecked(True)
            self._group_checks[name] = cb
            gh.addWidget(cb)
        gh.addStretch()
        lay.addWidget(g_groups)

        # ── 파라미터 ──────────────────────────────────────────────
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.sp_port_from = QSpinBox()
        self.sp_port_from.setRange(2, 11)
        self.sp_port_from.setValue(2)
        self.sp_port_to = QSpinBox()
        self.sp_port_to.setRange(2, 11)
        self.sp_port_to.setValue(11)
        h_ports = QHBoxLayout()
        h_ports.addWidget(self.sp_port_from)
        h_ports.addWidget(QLabel("to"))
        h_ports.addWidget(self.sp_port_to)
        h_ports.addStretch()
        form.addRow("Ports:", h_ports)

        self.sp_v_port = QDoubleSpinBox()
        self.sp_v_port.setRange(0.05, 5.0)
        self.sp_v_port.setSingleStep(0.05)
        self.sp_v_port.setDecimals(2)
        self.sp_v_port.setSuffix(" mL")
        self.sp_v_port.setValue(0.5)
        self.sp_v_port.setToolTip("Withdraw volume per port (≥ 1.5× inlet stub volume)")
        form.addRow("Volume / port:", self.sp_v_port)

        self.sp_cycles = QSpinBox()
        self.sp_cycles.setRange(1, 5)
        self.sp_cycles.setValue(2)
        self.sp_cycles.setToolTip("Common-path wash cycles before port sweep (P1)")
        form.addRow("Common cycles:", self.sp_cycles)

        self.sp_v_common = QDoubleSpinBox()
        self.sp_v_common.setRange(0.2, 6.0)
        self.sp_v_common.setSingleStep(0.5)
        self.sp_v_common.setDecimals(1)
        self.sp_v_common.setSuffix(" mL")
        self.sp_v_common.setValue(3.0)
        self.sp_v_common.setToolTip("Volume per common-path cycle (pump line ≈ 1.9 mL)")
        form.addRow("Common volume:", self.sp_v_common)

        self.sp_downstream = QDoubleSpinBox()
        self.sp_downstream.setRange(0.0, 6.0)
        self.sp_downstream.setSingleStep(0.5)
        self.sp_downstream.setDecimals(1)
        self.sp_downstream.setSuffix(" mL")
        self.sp_downstream.setValue(3.0)
        self.sp_downstream.setToolTip(
            "Final flush toward reactor (Outlet=WASTE). 0 = skip")
        form.addRow("Downstream flush:", self.sp_downstream)

        self.sp_wash_rate = QDoubleSpinBox()
        self.sp_wash_rate.setRange(0.0, 30.0)
        self.sp_wash_rate.setSingleStep(0.5)
        self.sp_wash_rate.setDecimals(1)
        self.sp_wash_rate.setSuffix(" mL/min")
        self.sp_wash_rate.setValue(0.0)
        # 0 = 그룹별 기본(wash_speed) 사용 — 실제 숫자를 표시 (사용자 피드백 2026-08-05)
        _ws = _rate_summary(self._wash_rates)
        self.sp_wash_rate.setSpecialValueText(
            f"group default ({_ws} mL/min)" if _ws else "group default")
        self.sp_wash_rate.setToolTip(
            "Withdraw/dump flow rate for P0–P3. Spin up to override all "
            "selected groups; at 0 each group keeps its own wash_speed. "
            "Caution: small syringes may stall at high rates")
        form.addRow("Wash rate:", self.sp_wash_rate)

        self.sp_flush_rate = QDoubleSpinBox()
        self.sp_flush_rate.setRange(0.0, 30.0)
        self.sp_flush_rate.setSingleStep(0.5)
        self.sp_flush_rate.setDecimals(1)
        self.sp_flush_rate.setSuffix(" mL/min")
        self.sp_flush_rate.setValue(0.0)
        _fs = _rate_summary(self._flush_rates)
        self.sp_flush_rate.setSpecialValueText(
            f"group default ({_fs} mL/min)" if _fs else "group default")
        self.sp_flush_rate.setToolTip(
            "Downstream flush rate toward reactor (P4). "
            "At 0 each group keeps its own prime_rate")
        form.addRow("Flush rate:", self.sp_flush_rate)

        self.cb_batch = QCheckBox("Batch dumps (collect several ports, then dump)")
        self.cb_batch.setChecked(False)
        self.cb_batch.setToolTip(
            "Off (default): withdraw port N → dump to 12, one port at a time.\n"
            "On: accumulate ports in the syringe and dump when nearly full — "
            "fewer valve moves, faster")
        form.addRow("", self.cb_batch)

        self.cb_collection = QCheckBox("Include collection line (0.24 mL)")
        self.cb_collection.setChecked(False)
        self.cb_collection.setToolTip(
            "Briefly switches Outlet to COLLECT after flush — park the "
            "collector needle over a waste position first")
        form.addRow("", self.cb_collection)

        lay.addLayout(form)

        # ── 버튼 ──────────────────────────────────────────────────
        h_btn = QHBoxLayout()
        h_btn.addStretch()
        btn_cancel = QPushButton("CANCEL")
        btn_cancel.clicked.connect(self.reject)
        btn_start = QPushButton("START WASH")
        btn_start.setDefault(True)
        btn_start.clicked.connect(self.accept)
        h_btn.addWidget(btn_cancel)
        h_btn.addWidget(btn_start)
        lay.addLayout(h_btn)

    # ── 결과 ──────────────────────────────────────────────────────
    def selected_groups(self):
        return [n for n, cb in self._group_checks.items() if cb.isChecked()]

    def options(self):
        p0, p1 = self.sp_port_from.value(), self.sp_port_to.value()
        if p1 < p0:
            p0, p1 = p1, p0
        opt = DeepWashOptions(
            ports=list(range(p0, p1 + 1)),
            v_port=self.sp_v_port.value(),
            common_cycles=self.sp_cycles.value(),
            v_common=self.sp_v_common.value(),
            downstream_ml=self.sp_downstream.value(),
            include_collection=self.cb_collection.isChecked(),
            batch_dump=self.cb_batch.isChecked(),
        )
        opt.wash_rate = self.sp_wash_rate.value()
        opt.downstream_rate = self.sp_flush_rate.value()
        return opt
