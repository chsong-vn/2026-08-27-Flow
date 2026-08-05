# -*- coding: utf-8 -*-
"""PyQt5 + Tabulator 임베드 — AG Grid 디자인 그대로 + 엑셀 복붙/범위선택 내장.
실행: py -3.14 demo_tabulator.py"""
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QLabel, QPlainTextEdit)

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from aggrid_widget import AgGridWidget


def _blank(grp, port):
    return {"_id": f"{grp}{port}", "grp": grp, "port": port, "cas": "", "mw": None,
            "smiles": "", "name": "", "density": None, "vol": None, "mass": None,
            "conc": None, "result": ""}


def _build_rows():
    rows = []
    for grp in ("A", "B"):
        for port in range(2, 12):
            rows.append(_blank(grp, port))
    # Group A 예시
    rows[0].update({"cas": "34784-05-9", "name": "6-Bromoisoquinoline", "mw": 208.06,
                    "smiles": "Brc1ccc2ccncc2c1", "vol": 5.0, "conc": 0.5})
    rows[1].update({"cas": "1064194-10-0", "name": "Boc-3-bromoazetidine", "mw": 236.11,
                    "smiles": "O=C(OC(C)(C)C)N1CC(Br)C1", "vol": 5.0, "conc": 0.6})
    rows[2].update({"cas": "108-48-5", "name": "2,6-Lutidine", "mw": 107.15,
                    "smiles": "Cc1cccc(C)n1", "density": 0.92, "vol": 5.0, "conc": 1.0})
    return rows


def main():
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("PyQt5 + Tabulator — 디자인 동일 + 엑셀 복붙")
    central = QWidget()
    lay = QVBoxLayout(central)
    lay.addWidget(QLabel("Tabulator(무료·MIT) — AG Grid와 동일 디자인 + 엑셀 복붙/드래그 선택 내장. "
                         "셀 드래그로 범위 선택, Ctrl+C/Ctrl+V(엑셀 붙여넣기) 동작. 더블클릭=편집."))
    grid = AgGridWidget([], _build_rows(), html="tabulator_grid.html")
    lay.addWidget(grid, 1)
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMaximumHeight(100)
    log.setPlaceholderText("편집/붙여넣기 로그 (Python 수신) …")
    lay.addWidget(log)
    grid.cellChanged.connect(lambda d: log.appendPlainText(
        "붙여넣기(bulk) 반영됨" if d.get("_bulk") else
        f"편집 → {d.get('grp')}{d.get('port')}: {d.get('name','')} | {d.get('result','')}"))
    win.setCentralWidget(central)
    win.resize(1300, 720)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
