# -*- coding: utf-8 -*-
"""PyQt5 창 안에 AG Grid 임베드 데모 — 앱 계산기 테이블(9칸)과 동일 구성.
칸: # | CAS | 시약명 | MW(g/mol) | 밀도(g/mL) | 부피(mL) | 질량(g) | 농도(M) | 계산 결과
그룹별 구분행(Group A/B · Port 2~11) + MW+2개 입력 시 자동 계산.
실행: py -3.14 demo.py"""
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QLabel, QPlainTextEdit)

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from aggrid_widget import AgGridWidget

# 컬럼 순서: 포트 · CAS · MW · SMILES · 시약명 · 밀도 · 부피 · 질량 · 농도 · 계산결과
COLUMN_DEFS = [
    {"headerName": "포트", "field": "port", "width": 64,
     "editable": False, "cellClass": "cell-muted"},
    {"headerName": "CAS", "field": "cas", "width": 150},
    {"headerName": "MW\n(g/mol)", "field": "mw", "width": 94, "type": "numericColumn"},
    {"headerName": "SMILES", "field": "smiles", "width": 210, "cellClass": "cell-smiles"},
    {"headerName": "시약명", "field": "name", "width": 190},
    {"headerName": "밀도\n(g/mL)", "field": "density", "width": 86, "type": "numericColumn"},
    {"headerName": "부피\n(mL)", "field": "vol", "width": 80,
     "type": "numericColumn", "cellClass": "cell-inp"},
    {"headerName": "질량\n(g)", "field": "mass", "width": 80,
     "type": "numericColumn", "cellClass": "cell-inp"},
    {"headerName": "농도\n(M)", "field": "conc", "width": 80,
     "type": "numericColumn", "cellClass": "cell-inp"},
    {"headerName": "계산  결과", "field": "result", "flex": 1, "minWidth": 150,
     "editable": False, "cellClass": "cell-res"},
]


def _blank(port):
    return {"port": port, "cas": "", "name": "", "mw": None, "smiles": "",
            "density": None, "vol": None, "mass": None, "conc": None, "result": ""}


def _build_rows():
    rows = []
    groups = [("A", "#e8743b"), ("B", "#2f80ed"), ("C", "#27ae60"), ("D", "#8b5cf6")]
    for name, color in groups[:2]:
        rows.append({"_grp": name, "_color": color, "_ports": "Ports 2–11", "_count": "10 ports"})
        for port in range(2, 12):
            rows.append(_blank(port))
    # Group A 예시 시약 (인덱스 1,2,3 = 포트 2,3,4)
    rows[1].update({"cas": "34784-05-9", "name": "6-Bromoisoquinoline", "mw": 208.06,
                    "smiles": "Brc1ccc2ccncc2c1", "vol": 5.0, "conc": 0.5})
    rows[2].update({"cas": "1064194-10-0", "name": "Boc-3-bromoazetidine", "mw": 236.11,
                    "smiles": "O=C(OC(C)(C)C)N1CC(Br)C1", "vol": 5.0, "conc": 0.6})
    rows[3].update({"cas": "108-48-5", "name": "2,6-Lutidine", "mw": 107.15,
                    "smiles": "Cc1cccc(C)n1", "density": 0.92, "vol": 5.0, "conc": 1.0})
    return rows


def main():
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("PyQt5 + AG Grid — 계산기 9칸 동일 구성")
    central = QWidget()
    lay = QVBoxLayout(central)
    lay.addWidget(QLabel("앱 계산기 테이블(9칸)을 AG Grid 로 동일 재현 — "
                         "MW + (부피·질량·농도 중 2개) 입력 시 나머지+결과 자동 계산. "
                         "편집은 Python 으로 전달(아래 로그)."))
    grid = AgGridWidget(COLUMN_DEFS, _build_rows())
    lay.addWidget(grid, 1)
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMaximumHeight(110)
    log.setPlaceholderText("셀 편집 로그 (Python 수신) …")
    lay.addWidget(log)
    grid.cellChanged.connect(lambda d: log.appendPlainText(
        f"편집 → 포트 {d.get('port')}: {d.get('name','')} | 결과: {d.get('result','')}"))
    win.setCentralWidget(central)
    win.resize(1300, 720)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
