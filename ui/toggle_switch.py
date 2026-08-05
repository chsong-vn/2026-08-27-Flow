"""애니메이션 슬라이딩 토글 스위치 (iOS 식) — 다크/라이트 테마 전환용.

@codesyncer-decision: 기존 "☾" QPushButton 을 좌우로 미끄러지는 스위치로 교체
  (사용자 요청 "해/달 아이콘 스위치"). 이진 on/off 설정(다크모드)에 버튼보다
  직관적. 트랙에 ☀(좌)/☾(우) 아이콘, 흰 노브가 활성 쪽을 덮는다(승인된 프리뷰
  형태). checked=True → 다크(노브 우측·남색 트랙), False → 라이트(노브 좌측·
  밝은 파랑 트랙). 색은 pos(0~1) 보간이라 앱 테마와 무관하게 상태를 자체 표현.

재사용: toggled(bool) 시그널(새 상태) · setChecked(v, animate, emit) · isChecked().
프로그램 갱신은 emit=False 로 시그널 루프 방지(테마매니저가 상태 동기화 시 사용).
"""
from PyQt5.QtCore import (Qt, QPropertyAnimation, pyqtProperty, pyqtSignal,
                          QRectF, QEasingCurve, QSize)
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QWidget


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_color(c0, c1, t):
    return QColor(
        int(_lerp(c0.red(), c1.red(), t)),
        int(_lerp(c0.green(), c1.green(), t)),
        int(_lerp(c0.blue(), c1.blue(), t)),
    )


class ToggleSwitch(QWidget):
    """좌우 슬라이딩 on/off 스위치. checked=True 를 '우측(다크)' 로 표현."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=True, width=58, height=30, icon_mode="theme",
                 parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0   # 0=좌(off), 1=우(on)
        self._w = int(width)
        self._h = int(height)
        self._icon_mode = icon_mode   # "theme"(☀/☾) | "plain"(무아이콘, 초록=ON)
        self.setFixedSize(self._w, self._h)
        self.setCursor(Qt.PointingHandCursor)

        self._knob = QColor("#ffffff")
        self._sun = QColor("#f5b301")          # ☀ 앰버
        self._moon = QColor("#e8eef7")         # ☾ 페일
        if icon_mode == "plain":
            # 범용 기능 on/off — off=회색, on=초록(활성). STATE_RUN 관례.
            self._track_off = QColor("#3a4453")
            self._track_on = QColor("#3fb950")
        else:
            # 테마 전환 — off(라이트)=밝은 파랑, on(다크)=남색
            self._track_off = QColor("#8ec5ff")
            self._track_on = QColor("#1e3a5f")
        if icon_mode == "theme":
            self.setToolTip("라이트 모드로 전환" if self._checked
                            else "다크 모드로 전환")

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

    # --- 애니메이션 대상 프로퍼티 (노브 위치 0~1) ---
    def getPos(self):
        return self._pos

    def setPos(self, v):
        self._pos = max(0.0, min(1.0, float(v)))
        self.update()

    pos = pyqtProperty(float, fget=getPos, fset=setPos)

    # --- 상태 API ---
    def isChecked(self):
        return self._checked

    def setChecked(self, v, animate=True, emit=False):
        """상태 설정. emit=False(기본) 면 시그널 미발생 — 프로그램 동기화용."""
        v = bool(v)
        target = 1.0 if v else 0.0
        changed = (v != self._checked)
        self._checked = v
        if self._icon_mode == "theme":
            self.setToolTip("라이트 모드로 전환" if v else "다크 모드로 전환")
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.setPos(target)
        if emit and changed:
            self.toggled.emit(v)

    def toggle(self):
        self.setChecked(not self._checked, animate=True, emit=True)

    def set_colors(self, track_off=None, track_on=None, knob=None,
                   sun=None, moon=None):
        """팔레트 연동(선택) — 앱 테마 톤에 맞춰 미세조정하고 싶을 때."""
        if track_off:
            self._track_off = QColor(track_off)
        if track_on:
            self._track_on = QColor(track_on)
        if knob:
            self._knob = QColor(knob)
        if sun:
            self._sun = QColor(sun)
        if moon:
            self._moon = QColor(moon)
        self.update()

    # --- 상호작용 ---
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle()
            e.accept()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle()
            e.accept()
            return
        super().keyPressEvent(e)

    def sizeHint(self):
        return QSize(self._w, self._h)

    # --- 렌더 ---
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = r.height() / 2.0

        # 트랙 (pos 로 라이트↔다크 색 보간)
        track = _lerp_color(self._track_off, self._track_on, self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)

        # @codesyncer(사용자 요청 "해달아이콘 없애줘"): ☀/☾ 트랙 아이콘 제거.
        #   상태 표현은 트랙 색(라이트=밝은파랑/다크=남색, HTE=회색/초록) + 노브
        #   위치로 충분. icon_mode 는 이제 색 스킴만 구분("theme" vs "plain").

        # 노브 (활성 쪽으로 이동)
        d = r.height() - 6
        x = r.left() + 3 + self._pos * (r.width() - d - 6)
        knob_rect = QRectF(x, r.top() + 3, d, d)
        # 노브 그림자 살짝
        shadow = QColor(0, 0, 0, 55)
        p.setBrush(shadow)
        p.drawEllipse(knob_rect.adjusted(0, 1.2, 0, 1.2))
        p.setBrush(self._knob)
        p.drawEllipse(knob_rect)
