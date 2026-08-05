# -*- coding: utf-8 -*-
"""WebGrid — PyQt5 안에 Tabulator(엑셀급 편집 그리드)를 임베드하는 위젯.

@codesyncer-decision: 촘촘한 QTableWidget 자리에 드롭인. Tabulator(MIT·무료)는
  클립보드 복붙 + 범위선택(드래그)이 내장이라 기존 계산기의 엑셀 UX와 호환된다.
  QWebEngineView + QWebChannel 로 Python↔JS 양방향. JS/CSS 는 vendor/ 번들(오프라인).

사용:
    grid = WebGrid(rows)                 # rows = list[dict] (_id 인덱스 권장)
    grid.cellChanged.connect(cb)         # 편집된 행(dict) 수신 (자동계산 값 포함)
    grid.set_rows(updated_rows)          # Python → JS 부분 갱신 (PubChem 결과 등)
    grid.rows()                          # 현재 데이터
"""
import os
import json

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QUrl, QTimer
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

_HERE = os.path.dirname(os.path.abspath(__file__))


def grid_theme(palette, is_dark=True):
    """앱 팔레트(ui/colors.py Dark/LightPalette) → WebGrid CSS 변수 dict.

    계산기·시퀀스 시약 그리드가 공유하는 단일 팔레트 매핑.
    다크 주악센트=시안블루(오렌지 아님, 오렌지는 열/온도 전용).
    """
    from ui.colors import rgba, T
    P = palette
    return {
        # 앱 디자인 토큰과 동일 폰트 스택 상속 → 글꼴 이질감 제거
        "font": T.FONT,
        "bg": P.BG_PRIMARY, "card": P.BG_SECONDARY,
        "tx": P.TEXT_TABLE, "tx2": P.TEXT_SECONDARY, "tx3": P.TEXT_DISABLED,
        "line": P.BORDER_SECONDARY, "line2": P.BORDER_TABLE, "band": P.HEADER_BG,
        "row": P.BG_SECONDARY, "row-alt": P.BG_ALTERNATE,
        "row-hover": P.STATE_HOVER_INPUT,
        # 헤더는 전용 HEADER_TEXT 토큰 — 라이트 모드에서 TEXT_TERTIARY 보다 진해
        # "글씨가 흐려서 안 보인다" 피드백 해소 (다크는 기존과 동급 밝기)
        "header-tx": P.HEADER_TEXT,
        "hdr": P.HEADER_TEXT,
        "run": P.STATE_RUN,       # 계산 완료 상태 배지(초록)
        "brand": P.ACCENT_BLUE, "brand-weak": rgba(P.ACCENT_BLUE, 0.16),
        "inp": P.BG_INPUT, "smiles": P.ACCENT_GRAY, "edit": P.ACCENT_BLUE,
        "chip-bg": P.BG_TERTIARY, "chip-tx": P.TEXT_SECONDARY,
        # 그룹 좌측바/라벨 — 팔레트 악센트 4색 순환
        "ga": P.ACCENT_BLUE, "gb": P.ACCENT_GREEN,
        "gc": P.ACCENT_PURPLE, "gd": P.ACCENT_CYAN,
    }


class _Bridge(QObject):
    configReady = pyqtSignal(str)        # rowData json → JS (초기)
    columnsReady = pyqtSignal(str)       # 동적 컬럼 정의 json → JS (스텝 그리드 등)
    updateRows = pyqtSignal(str)         # rowData json → JS (부분 갱신)
    replaceRows = pyqtSignal(str)        # rowData json → JS (전체 교체: 행 추가/삭제)
    applyTheme = pyqtSignal(str)         # 팔레트 dict json → JS (테마 주입/전환)
    _cellChanged = pyqtSignal(dict)

    def __init__(self, rows, theme=None, columns=None):
        super().__init__()
        self._rows = rows
        self._theme = theme or {}
        self._columns = columns

    @pyqtSlot()
    def onReady(self):
        # 팔레트 → 컬럼 → 데이터 순서 (흰 배경 깜빡임 없이 테마·컬럼 적용 후 렌더)
        if self._theme:
            self.applyTheme.emit(json.dumps(self._theme, ensure_ascii=False))
        if self._columns is not None:
            self.columnsReady.emit(json.dumps(self._columns, ensure_ascii=False))
        self.configReady.emit(json.dumps(self._rows, ensure_ascii=False))

    @pyqtSlot(str)
    def onCell(self, s):
        try:
            self._cellChanged.emit(json.loads(s))
        except Exception:
            pass


class WebGrid(QWidget):
    cellChanged = pyqtSignal(dict)

    def __init__(self, rows, parent=None, html="calc_grid.html", theme=None, columns=None):
        super().__init__(parent)
        self._rows = rows
        self._ready = False
        self.view = QWebEngineView(self)
        s = self.view.settings()
        # QWebEngine 기본 클립보드 차단 해제 (엑셀 복붙 동작 필수)
        s.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.JavascriptCanPaste, True)
        # 흰색 노출 방지 3중 방어: (1)페이지 베이스색 (2)위젯 autofill (3)loadFinished 재적용
        # @codesyncer-decision: QWebEngineView 기본 베이스=흰색. QSplitter 안에서 문서가
        #   뷰보다 짧거나 리사이즈 시 하단에 흰 사각형이 노출됨 → 베이스를 앱 배경으로.
        self._bg = (theme or {}).get("bg", "#121418")
        try:
            from PyQt5.QtGui import QColor, QPalette
            self.view.page().setBackgroundColor(QColor(self._bg))
            self.setAutoFillBackground(True)
            pal = self.palette()
            pal.setColor(QPalette.Window, QColor(self._bg))
            self.setPalette(pal)
            self.view.loadFinished.connect(self._reapply_bg)
        except Exception:
            pass
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)

        self._bridge = _Bridge(rows, theme, columns)
        self._bridge._cellChanged.connect(self._on_cell)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self.view.page().setWebChannel(self._channel)
        self.view.load(QUrl.fromLocalFile(os.path.join(_HERE, html)))

    def resizeEvent(self, e):
        # @codesyncer: 위젯(스플리터/창) 리사이즈 시 Tabulator fitColumns 재정렬.
        #   좁은 폭에서 빌드된 그리드가 넓어져도 컬럼이 왼쪽에 뭉치던 버그 해결.
        super().resizeEvent(e)
        t = getattr(self, '_redraw_timer', None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(90)
            t.timeout.connect(self._refit)
            self._redraw_timer = t
        t.start()

    def _refit(self):
        try:
            self.view.page().runJavaScript(
                "if(typeof table!=='undefined'&&table){table.redraw(true);}")
        except Exception:
            pass

    def recalc_all(self):
        """그리드의 모든 행을 강제 재계산 (계산 그리드의 '전체 계산' 버튼용)."""
        try:
            self.view.page().runJavaScript(
                "if(typeof recalcAll==='function'){recalcAll();}")
        except Exception:
            pass

    def _reapply_bg(self, ok=True):
        try:
            from PyQt5.QtGui import QColor
            self.view.page().setBackgroundColor(QColor(self._bg))
        except Exception:
            pass

    def _on_cell(self, d):
        if d.get("_bulk"):
            # @codesyncer: 벌크(전체계산/붙여넣기)가 재계산된 행을 함께 실어오면 로컬 미러를
            #   갱신 → 소비자(_on_grid_cell)가 rows()로 파생값(자동계산 conc 등)을 정확히 읽음.
            #   _rows 미포함(구 호출·타 그리드)이면 무시 → 하위호환.
            rows = d.get("_rows")
            if rows:
                by_id = {r.get("_id"): r for r in self._rows}
                for u in rows:
                    if u.get("_id") in by_id:
                        by_id[u["_id"]].update(u)
            self.cellChanged.emit(d)
            return
        key = d.get("_id")
        for r in self._rows:
            if r.get("_id") == key:
                r.update(d)
                break
        self.cellChanged.emit(d)

    def set_theme(self, theme):
        """앱 테마 전환 시 팔레트 재주입 (다크/라이트)."""
        self._bridge._theme = dict(theme or {})
        self._bg = self._bridge._theme.get("bg", "#121418")
        try:
            from PyQt5.QtGui import QColor, QPalette
            self.view.page().setBackgroundColor(QColor(self._bg))
            pal = self.palette()
            pal.setColor(QPalette.Window, QColor(self._bg))
            self.setPalette(pal)
        except Exception:
            pass
        self._bridge.applyTheme.emit(json.dumps(self._bridge._theme, ensure_ascii=False))

    def replace_rows(self, rows):
        """Python → JS 전체 데이터 교체 (행 추가/삭제 반영). 로컬 미러도 교체."""
        self._rows = list(rows)
        self._bridge._rows = self._rows
        self._bridge.replaceRows.emit(json.dumps(rows, ensure_ascii=False))

    def set_rows(self, rows):
        """Python → JS 부분 갱신 (_id 인덱스로 매칭). PubChem 결과 등."""
        # 로컬 미러도 갱신
        by_id = {r.get("_id"): r for r in self._rows}
        for r in rows:
            if r.get("_id") in by_id:
                by_id[r["_id"]].update(r)
        self._bridge.updateRows.emit(json.dumps(rows, ensure_ascii=False))

    def rows(self):
        return self._rows
