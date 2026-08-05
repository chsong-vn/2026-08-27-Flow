# -*- coding: utf-8 -*-
"""AgGridWidget — PyQt5 안에 AG Grid(엑셀급 편집 그리드)를 임베드하는 위젯.

@codesyncer-decision: 앱 전체를 웹으로 옮기지 않고, 촘촘한 QTableWidget 자리에만
  드롭인. QWebEngineView(내장 크로미움)로 로컬 grid.html 을 띄우고, QWebChannel 로
  Python↔JS 양방향 바인딩. AG Grid JS/CSS 는 vendor/ 에 번들(오프라인 동작).

사용:
    grid = AgGridWidget(column_defs, row_data)   # column_defs = AG Grid columnDefs
    grid.cellChanged.connect(lambda row: ...)    # 편집된 행(dict) 수신
    grid.rows()                                  # 현재 행 데이터(list[dict])
"""
import os
import json

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QUrl
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

_HERE = os.path.dirname(os.path.abspath(__file__))


class _Bridge(QObject):
    """JS 와 통신하는 브리지 (QWebChannel 로 노출)."""
    configReady = pyqtSignal(str, str)   # (columnDefs json, rowData json) → JS
    _cellChanged = pyqtSignal(dict)      # JS → 내부

    def __init__(self, cols, rows):
        super().__init__()
        self._cols = cols
        self._rows = rows

    @pyqtSlot()
    def onReady(self):
        """JS 준비 완료 → 초기 config 밀어넣기."""
        self.configReady.emit(json.dumps(self._cols, ensure_ascii=False),
                              json.dumps(self._rows, ensure_ascii=False))

    @pyqtSlot(str)
    def onCell(self, s):
        """JS 셀 편집 → dict 로 파싱."""
        try:
            self._cellChanged.emit(json.loads(s))
        except Exception:
            pass


class AgGridWidget(QWidget):
    """AG Grid 를 감싼 PyQt5 위젯."""
    cellChanged = pyqtSignal(dict)   # 편집된 행(dict)

    def __init__(self, column_defs, row_data, parent=None, html="grid.html"):
        super().__init__(parent)
        self._rows = row_data
        self._html = html
        self.view = QWebEngineView(self)
        # @codesyncer-decision: QWebEngine 은 기본적으로 JS 클립보드 접근/붙여넣기 차단 →
        #   엑셀 복붙(Ctrl+C/V)이 동작하려면 명시적으로 허용해야 한다.
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.JavascriptCanPaste, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)

        self._bridge = _Bridge(column_defs, row_data)
        self._bridge._cellChanged.connect(self._on_cell)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.view.page().setWebChannel(self._channel)
        self.view.load(QUrl.fromLocalFile(os.path.join(_HERE, self._html)))

    def _on_cell(self, d):
        key = d.get("port")
        for r in self._rows:
            if r.get("port") == key:
                r.update(d)
                break
        self.cellChanged.emit(d)

    def rows(self):
        return self._rows
