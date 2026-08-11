# -*- coding: utf-8 -*-
"""96well 분취기 좌표 캘리브레이션 GUI — 버튼/키보드로 직접 조그하며 티칭.

앱 본체와 독립 실행 (하드웨어를 이 창만 점유). 앱이 켜져 있으면 먼저 닫을 것.

    py -3.14 calibrate_plate96_gui.py

조작: 방향키 = XY, PgUp/PgDn = Z, 1~4 = 스텝(0.1/0.5/1/5mm), Space = 캡처
측정 7점: Plate A(A1/A12/H12) → Plate B(A1/A12/H12) → WASH
"""
import sys, os, json, time, shutil, queue, threading

import serial
import serial.tools.list_ports as lp
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QLabel, QPushButton, QComboBox,
                             QGroupBox, QTextEdit, QMessageBox, QRadioButton,
                             QButtonGroup, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS = os.path.join(HERE, "hardware", "collectors", "data", "well_coordinates.json")
BAUD = 250000
ROWS = "ABCDEFGH"
STEPS = [0.1, 0.5, 1.0, 5.0]
DZ_APPROACH, DZ_TRAVEL, DZ_WASH_DIP = +2.0, +6.0, -3.0

TARGETS = [
    ("A_A1",  "Plate A — A1",  "A1 중앙 + 분주 높이까지 Z 하강 (이 Z = z_dispense)"),
    ("A_A12", "Plate A — A12", "같은 행 12번 열"),
    ("A_H12", "Plate A — H12", "대각 반대편 코너"),
    ("B_A1",  "Plate B — A1",  "플레이트 B의 A1"),
    ("B_A12", "Plate B — A12", "플레이트 B의 A12"),
    ("B_H12", "Plate B — H12", "플레이트 B의 H12"),
    ("WASH",  "WASH 위치",     "세척 포트 중앙 (이 Z = Z_dip)"),
]


# ── 시리얼 워커 (GUI 블로킹 방지) ────────────────────────────
class Worker(QObject):
    sig_log = pyqtSignal(str)
    sig_pos = pyqtSignal(float, float, float)
    sig_conn = pyqtSignal(bool, str)
    sig_busy = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.q = queue.Queue()
        self._run = True
        self._abort = False      # 비상정지 시 진행 중 대기 루프를 즉시 탈출
        threading.Thread(target=self._loop, daemon=True).start()

    def post(self, fn, *a):
        self._abort = False        # 새 사용자 명령 = 비상정지 해제
        self.q.put((fn, a))

    # @codesyncer-decision: 비상정지는 **큐를 우회해 GUI 스레드에서 직접 write** 한다.
    #   워커가 M400 대기(최대 20~30초)로 블로킹 중일 때 큐에 넣으면 그 이동이 끝난
    #   뒤에야 실행된다 — 정지가 필요한 바로 그 순간에 안 듣는다. (샘플러 E-Stop 의
    #   '락 우회 raw 0x18' 과 같은 이유.)
    def estop(self):
        self._abort = True
        n = 0
        try:
            while True:
                self.q.get_nowait(); n += 1
        except queue.Empty:
            pass
        ser = self.ser
        if ser is None:
            self.sig_log.emit("[비상정지] 미연결 — 장비 전원을 차단하세요")
            return
        try:
            ser.write(b"\nM410\n")   # quickstop: 계획된 모션 즉시 중단
            ser.flush()
            ser.write(b"M84\n")      # 스테퍼 해제: 스톨 상태에서 미는 힘을 뺀다
            ser.flush()
            self.sig_log.emit(f"[비상정지] M410 + M84 전송 (대기 명령 {n}건 폐기). "
                              f"소리가 계속되면 전원을 차단하세요.")
        except Exception as e:
            self.sig_log.emit(f"[비상정지 실패] {e} — 즉시 장비 전원을 차단하세요")

    def motors_off(self):
        self._cmd("M84", wait=2.0)
        self.sig_log.emit("모터 해제 (M84) — 축을 손으로 움직일 수 있습니다")

    def _loop(self):
        while self._run:
            try:
                fn, a = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.sig_busy.emit(True)
            try:
                fn(*a)
            except Exception as e:
                self.sig_log.emit(f"[오류] {e}")
            finally:
                self.sig_busy.emit(False)

    # -- 저수준 --
    def _cmd(self, c, wait=2.0):
        if not self.ser:
            raise RuntimeError("미연결")
        self.ser.reset_input_buffer()
        self.ser.write((c + "\n").encode())
        t0, buf = time.time(), ""
        while time.time() - t0 < wait:
            if self._abort:          # 비상정지 — 대기 즉시 포기
                return buf
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n).decode("ascii", "replace")
                if buf.strip().endswith("ok") or "FIRMWARE_NAME" in buf:
                    break
            time.sleep(0.03)
        return buf

    def _read_pos(self):
        r = self._cmd("M114", wait=3.0)
        try:
            seg = r.split("Count")[0]
            v = {}
            for ax in "XYZ":
                i = seg.index(ax + ":")
                v[ax] = float(seg[i + 2:].split()[0])
            self.sig_pos.emit(v["X"], v["Y"], v["Z"])
            return v["X"], v["Y"], v["Z"]
        except Exception:
            self.sig_log.emit(f"[경고] M114 파싱 실패: {r[:60]!r}")
            return None

    # -- 작업 --
    def connect(self, port):
        try:
            self.ser = serial.Serial(port, BAUD, timeout=3.0)
            self.ser.dtr = False; time.sleep(0.2); self.ser.dtr = True
            time.sleep(4.0)
            self.ser.reset_input_buffer()
            r = self._cmd("M115", wait=3.0)
            if "FIRMWARE_NAME" not in r:
                self.ser.close(); self.ser = None
                self.sig_conn.emit(False, f"Marlin 응답 없음: {r[:60]!r}")
                return
            self._cmd("G90", wait=1.0)
            fw = r.split("\n")[0][:80]
            self.sig_conn.emit(True, fw)
            self.sig_log.emit(f"연결됨 — {fw}")
            self._read_pos()
        except Exception as e:
            self.ser = None
            self.sig_conn.emit(False, str(e))

    def disconnect(self):
        try:
            if self.ser:
                self._cmd("M84", wait=1.0)
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.sig_conn.emit(False, "연결 해제")

    def home(self):
        self.sig_log.emit("G28 호밍 중... (최대 90초)")
        self._cmd("G1 Z30 F1200", wait=4.0)
        r = self._cmd("G28", wait=90.0)
        if "ok" not in r.lower():
            self.sig_log.emit(f"[경고] G28 응답 이상: {r[:80]!r} — 엔드스톱 확인")
        else:
            self.sig_log.emit("호밍 완료")
        self._read_pos()

    def jog(self, dx, dy, dz):
        # @codesyncer-decision: Z 는 XY 보다 훨씬 느리게(F600). 리드스크류 Z 를 XY 속도로
        #   밀면 스텝 탈조로 끽끽거리며 위치가 조용히 어긋난다. 드라이버도 z=1200 을 쓴다.
        f = 600 if dz else 3000
        self._cmd("G91", wait=1.0)
        self._cmd(f"G1 X{dx} Y{dy} Z{dz} F{f}", wait=3.0)
        self._cmd("M400", wait=20.0)
        self._cmd("G90", wait=1.0)
        self._read_pos()

    def goto(self, x, y, z, z_travel=None):
        if z_travel is not None:
            self._cmd(f"G1 Z{z_travel} F1200", wait=5.0)
        self._cmd(f"G1 X{x} Y{y} F6000", wait=12.0)
        self._cmd(f"G1 Z{z} F1200", wait=5.0)
        self._cmd("M400", wait=30.0)
        self._read_pos()

    def quickstop(self):
        try:
            self.ser.write(b"M410\n")     # Marlin quickstop (리셋 불필요)
            self.sig_log.emit("[정지] M410 quickstop 전송")
        except Exception as e:
            self.sig_log.emit(f"[정지 실패] {e}")

    def refresh(self):
        self._read_pos()


# ── 기하 계산 ────────────────────────────────────────────────
def build_plate(tag, a1, a12, h12):
    ypitch = (a12[1] - a1[1]) / 11.0
    xpitch = (h12[0] - a12[0]) / 7.0
    warn = []
    for nm, v in (("Y피치", ypitch), ("X피치", xpitch)):
        if not (8.5 <= v <= 9.5):
            warn.append(f"{tag}: {nm} {v:.3f}mm — SBS 9.0mm 벗어남 (웰 오지정 의심)")
    if abs(a12[0] - a1[0]) > 1.0:
        warn.append(f"{tag}: A1→A12 X편차 {a12[0]-a1[0]:+.2f}mm — 플레이트 기울어짐")
    if abs(h12[1] - a12[1]) > 1.0:
        warn.append(f"{tag}: A12→H12 Y편차 {h12[1]-a12[1]:+.2f}mm — 플레이트 기울어짐")
    wells = []
    for r in range(8):
        for c in range(12):
            wells.append({"plate": tag, "well": f"{ROWS[r]}{c+1}",
                          "row_idx": r, "col_idx": c,
                          "machine_X": round(a1[0] + r * xpitch, 3),
                          "machine_Y": round(a1[1] + c * ypitch, 3),
                          "Z_approach": None, "Z_dispense": None})
    cal = {"A1": [round(a1[0], 2), round(a1[1], 2)],
           "A12": [round(a12[0], 2), round(a12[1], 2)],
           "H12": [round(h12[0], 2), round(h12[1], 2)],
           "X_pitch_mm": round(xpitch, 4), "Y_pitch_mm": round(ypitch, 4)}
    return wells, cal, warn


# ── 메인 창 ──────────────────────────────────────────────────
class Win(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("96well 분취기 좌표 캘리브레이션")
        self.resize(880, 720)
        self.w = Worker()
        self.captured = {}
        self.step = 1.0
        self.pos = (0.0, 0.0, 0.0)
        self.connected = False
        self._build()
        self.w.sig_log.connect(self.log)
        self.w.sig_pos.connect(self.on_pos)
        self.w.sig_conn.connect(self.on_conn)
        self.w.sig_busy.connect(lambda b: self.lbl_busy.setText("● 동작 중" if b else ""))
        self.setFocusPolicy(Qt.StrongFocus)
        self.scan_ports()

    # -- UI --
    def _build(self):
        L = QVBoxLayout(self)

        # 연결
        g = QGroupBox("연결")
        h = QHBoxLayout(g)
        self.cmb = QComboBox(); self.cmb.setMinimumWidth(320)
        self.btn_scan = QPushButton("포트 검색"); self.btn_scan.clicked.connect(self.scan_ports)
        self.btn_conn = QPushButton("연결"); self.btn_conn.clicked.connect(self.toggle_conn)
        self.lbl_busy = QLabel("")
        h.addWidget(self.cmb); h.addWidget(self.btn_scan); h.addWidget(self.btn_conn)
        h.addWidget(self.lbl_busy); h.addStretch()
        L.addWidget(g)

        # 위치 + 조그
        mid = QHBoxLayout()

        gp = QGroupBox("현재 위치 (M114 실측)")
        vp = QVBoxLayout(gp)
        self.lbl_pos = QLabel("X —   Y —   Z —")
        f = QFont("Consolas", 15); f.setBold(True); self.lbl_pos.setFont(f)
        vp.addWidget(self.lbl_pos)
        self.btn_ref = QPushButton("위치 새로고침"); self.btn_ref.clicked.connect(lambda: self.w.post(self.w.refresh))
        vp.addWidget(self.btn_ref)
        self.btn_home = QPushButton("원점 복귀 (G28)"); self.btn_home.clicked.connect(self.do_home)
        vp.addWidget(self.btn_home)
        self.btn_stop = QPushButton("■ 비상 정지  [Esc]")
        self.btn_stop.setStyleSheet(
            "background:#c0392b;color:white;font-weight:bold;padding:14px;font-size:14px;")
        self.btn_stop.setFocusPolicy(Qt.NoFocus)
        # 큐를 타지 않고 즉시 실행 — 이동 중에도 먹혀야 한다
        self.btn_stop.clicked.connect(self.w.estop)
        vp.addWidget(self.btn_stop)
        self.btn_moff = QPushButton("모터 해제 (M84)")
        self.btn_moff.setFocusPolicy(Qt.NoFocus)
        self.btn_moff.clicked.connect(lambda: self.w.post(self.w.motors_off))
        vp.addWidget(self.btn_moff)
        vp.addWidget(QLabel("<i>끽끽거리면 즉시 정지 — 축이 막혀 스텝을 놓치는 소리입니다.<br>"
                            "멈추지 않으면 장비 전원을 차단하세요.</i>"))
        vp.addStretch()
        mid.addWidget(gp, 1)

        gj = QGroupBox("조그  (방향키 = XY, PgUp/PgDn = Z)")
        vj = QVBoxLayout(gj)
        hs = QHBoxLayout(); hs.addWidget(QLabel("스텝:"))
        self.bg = QButtonGroup(self)
        for i, s in enumerate(STEPS):
            rb = QRadioButton(f"{s}mm  [{i+1}]")
            if s == 1.0: rb.setChecked(True)
            rb.toggled.connect(lambda ck, v=s: ck and setattr(self, "step", v))
            self.bg.addButton(rb); hs.addWidget(rb)
        hs.addStretch(); vj.addLayout(hs)

        gr = QGridLayout()
        def jb(txt, dx, dy, dz, r, c):
            b = QPushButton(txt); b.setFixedSize(74, 42)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda: self.jog(dx, dy, dz))
            gr.addWidget(b, r, c)
        jb("Y +", 0, 1, 0, 0, 1)
        jb("X −", -1, 0, 0, 1, 0)
        jb("X +", 1, 0, 0, 1, 2)
        jb("Y −", 0, -1, 0, 2, 1)
        jb("Z +", 0, 0, 1, 0, 4)
        jb("Z −", 0, 0, -1, 2, 4)
        vj.addLayout(gr)
        vj.addWidget(QLabel("<i>Z는 니들 충돌 주의 — 큰 스텝으로 하강하지 말 것</i>"))
        vj.addStretch()
        mid.addWidget(gj, 1)
        L.addLayout(mid)

        # 티칭
        gt = QGroupBox("좌표 티칭 — 위치를 맞춘 뒤 [캡처] (Space)")
        vt = QVBoxLayout(gt)
        self.tbl = QTableWidget(len(TARGETS), 5)
        self.tbl.setHorizontalHeaderLabels(["측정점", "설명", "X", "Y", "Z"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, (k, nm, hint) in enumerate(TARGETS):
            self.tbl.setItem(i, 0, QTableWidgetItem(nm))
            self.tbl.setItem(i, 1, QTableWidgetItem(hint))
            for c in (2, 3, 4):
                self.tbl.setItem(i, c, QTableWidgetItem("—"))
        self.tbl.selectRow(0)
        self.tbl.setFixedHeight(230)
        vt.addWidget(self.tbl)
        hb = QHBoxLayout()
        self.btn_cap = QPushButton("현재 위치 캡처  [Space]"); self.btn_cap.clicked.connect(self.capture)
        self.btn_cap.setStyleSheet("font-weight:bold;padding:8px;")
        self.btn_cap.setFocusPolicy(Qt.NoFocus)
        self.btn_goto = QPushButton("선택 측정점으로 이동"); self.btn_goto.clicked.connect(self.goto_sel)
        self.btn_goto.setFocusPolicy(Qt.NoFocus)
        self.btn_save = QPushButton("계산 후 저장"); self.btn_save.clicked.connect(self.save)
        self.btn_save.setFocusPolicy(Qt.NoFocus)
        hb.addWidget(self.btn_cap); hb.addWidget(self.btn_goto); hb.addStretch(); hb.addWidget(self.btn_save)
        vt.addLayout(hb)
        L.addWidget(gt)

        self.txt = QTextEdit(); self.txt.setReadOnly(True); self.txt.setFixedHeight(150)
        self.txt.setFont(QFont("Consolas", 9))
        L.addWidget(self.txt)
        self.set_enabled(False)

    def set_enabled(self, on):
        for b in (self.btn_ref, self.btn_home, self.btn_cap, self.btn_goto,
                  self.btn_stop, self.btn_moff):
            b.setEnabled(on)

    # -- 이벤트 --
    def log(self, s):
        self.txt.append(s)
        self.txt.verticalScrollBar().setValue(self.txt.verticalScrollBar().maximum())

    def on_pos(self, x, y, z):
        self.pos = (x, y, z)
        self.lbl_pos.setText(f"X {x:8.2f}   Y {y:8.2f}   Z {z:7.2f}")

    def on_conn(self, ok, msg):
        self.connected = ok
        self.btn_conn.setText("연결 해제" if ok else "연결")
        self.set_enabled(ok)
        self.log(msg if ok else f"[연결 실패] {msg}")
        if not ok:
            self.lbl_pos.setText("X —   Y —   Z —")

    def scan_ports(self):
        self.cmb.clear()
        for p in lp.comports():
            tag = "  ← 레벨센서(제외)" if p.device == "COM3" else ""
            self.cmb.addItem(f"{p.device}  |  {p.description}{tag}", p.device)
        if self.cmb.count() == 0:
            self.log("포트 없음")

    def toggle_conn(self):
        if self.connected:
            self.w.post(self.w.disconnect)
        else:
            port = self.cmb.currentData()
            if not port:
                return
            self.log(f"{port} 연결 시도 (보드 리셋 대기 4초)...")
            self.w.post(self.w.connect, port)

    def do_home(self):
        if QMessageBox.question(self, "호밍", "G28 호밍합니다. 니들 경로에 장애물이 없습니까?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.w.post(self.w.home)

    def jog(self, sx, sy, sz):
        if not self.connected:
            return
        s = self.step
        self.w.post(self.w.jog, sx * s, sy * s, sz * s)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:    self.w.estop()      # 어떤 상태에서도 최우선
        elif k == Qt.Key_Left:    self.jog(-1, 0, 0)
        elif k == Qt.Key_Right:   self.jog(1, 0, 0)
        elif k == Qt.Key_Up:      self.jog(0, 1, 0)
        elif k == Qt.Key_Down:    self.jog(0, -1, 0)
        elif k == Qt.Key_PageUp:  self.jog(0, 0, 1)
        elif k == Qt.Key_PageDown:self.jog(0, 0, -1)
        elif k == Qt.Key_Space:   self.capture()
        elif k in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            i = k - Qt.Key_1
            self.bg.buttons()[i].setChecked(True)
        else:
            super().keyPressEvent(e)

    def capture(self):
        if not self.connected:
            return
        r = self.tbl.currentRow()
        if r < 0:
            return
        key = TARGETS[r][0]
        x, y, z = self.pos
        self.captured[key] = (x, y, z)
        for c, v in ((2, x), (3, y), (4, z)):
            self.tbl.item(r, c).setText(f"{v:.2f}")
        self.log(f"캡처 {TARGETS[r][1]}: X{x:.2f} Y{y:.2f} Z{z:.2f}")
        if r + 1 < len(TARGETS):
            self.tbl.selectRow(r + 1)

    def goto_sel(self):
        r = self.tbl.currentRow()
        key = TARGETS[r][0] if r >= 0 else None
        if key not in self.captured:
            self.log("해당 측정점은 아직 캡처되지 않았습니다")
            return
        x, y, z = self.captured[key]
        self.w.post(self.w.goto, x, y, z, z + 6.0)

    # -- 저장 --
    def save(self):
        miss = [nm for k, nm, _ in TARGETS if k not in self.captured]
        if miss:
            QMessageBox.warning(self, "미완료", "아직 캡처되지 않은 측정점:\n\n" + "\n".join(miss))
            return
        c = self.captured
        wa, ca, w1 = build_plate("A", c["A_A1"], c["A_A12"], c["A_H12"])
        wb, cb, w2 = build_plate("B", c["B_A1"], c["B_A12"], c["B_H12"])
        warn = w1 + w2
        ax = [w["machine_X"] for w in wa]; bx = [w["machine_X"] for w in wb]
        if min(bx) < max(ax) and min(ax) < max(bx):
            warn.append(f"플레이트 A({min(ax):.1f}~{max(ax):.1f}) 와 B({min(bx):.1f}~{max(bx):.1f}) X범위 겹침")

        z_disp = c["A_A1"][2]
        zl = {"z_travel": round(z_disp + DZ_TRAVEL, 2),
              "z_approach": round(z_disp + DZ_APPROACH, 2),
              "z_dispense": round(z_disp, 2),
              "z_wash_dip": round(c["WASH"][2], 2),
              "note": "조그 티칭 실측 (calibrate_plate96_gui.py). z_dispense=Plate A A1 캡처, "
                      f"approach=+{DZ_APPROACH}, travel=+{DZ_TRAVEL}. G28 기준 절대 높이"}
        for w in wa + wb:
            w["Z_approach"] = zl["z_approach"]; w["Z_dispense"] = zl["z_dispense"]

        summary = (f"플레이트 A — X피치 {ca['X_pitch_mm']}mm  Y피치 {ca['Y_pitch_mm']}mm\n"
                   f"플레이트 B — X피치 {cb['X_pitch_mm']}mm  Y피치 {cb['Y_pitch_mm']}mm\n"
                   f"Z — 분주 {zl['z_dispense']} / 접근 {zl['z_approach']} / "
                   f"이동 {zl['z_travel']} / WASH {zl['z_wash_dip']}\n총 192웰")
        if warn:
            summary += "\n\n⚠ 경고\n" + "\n".join(" · " + x for x in warn)
        summary += "\n\n저장하시겠습니까? (기존 파일은 백업됩니다)"
        if QMessageBox.question(self, "저장 확인", summary,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            self.log("저장 취소")
            return

        data = {"frame": {"machine_bed": [200.0, 200.0, 250.0],
                          "bed_offset": [100.0, 100.0],
                          "z_levels": zl,
                          "z_reference": "dispense_height (jog-taught)",
                          "needs_session_setup": "G28 후 절대좌표 사용",
                          "calibration": {"method": "GUI 조그 티칭 — A/B 각 3코너 + WASH",
                                          "plate_A": ca, "plate_B": cb,
                                          "row_direction": "+X (A→H)",
                                          "col_direction": "+Y (1→12)"},
                          "global_offset_X": 0.0},
                "wash_positions": [{"id": "WASH", "X": round(c["WASH"][0], 2),
                                    "Y": round(c["WASH"][1], 2), "Z_dip": zl["z_wash_dip"]}],
                "wells": wa + wb}
        try:
            if os.path.exists(COORDS):
                bak = COORDS + time.strftime(".bak_%Y%m%d_%H%M%S")
                shutil.copy2(COORDS, bak)
                self.log(f"백업: {os.path.basename(bak)}")
            with open(COORDS, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"저장 완료 — {COORDS}")
            for x in warn:
                self.log(f"[경고] {x}")
            QMessageBox.information(self, "완료",
                                    "저장했습니다.\n\n[선택 측정점으로 이동] 으로 몇 군데 확인해 보세요.")
        except Exception as e:
            QMessageBox.critical(self, "저장 실패", str(e))

    def closeEvent(self, e):
        try:
            self.w.post(self.w.disconnect)
            time.sleep(0.3)
        except Exception:
            pass
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Win()
    win.show()
    sys.exit(app.exec_())
