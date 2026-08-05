# --- START OF FILE ui/dialogs.py ---
import sys
import os
import re
import uuid
import math
import copy
import traceback
import serial.tools.list_ports

from hardware.factory import HardwareFactory

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                             QListWidget, QPushButton, QComboBox, QLabel, QSplitter,
                             QGroupBox, QFormLayout, QLineEdit, QDoubleSpinBox, QSpinBox,
                             QMessageBox, QStackedWidget, QListWidgetItem, QScrollArea, QFrame)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QColor
from ui.colors import (
    DarkPalette,
    LightPalette,
    T,
    get_active_dark_mode,
    install_global_stylesheet_color_patch,
    remap_widget_tree_styles,
    set_active_dark_mode,
)

# 하드웨어 팩토리 모듈 임포트
try:
    from hardware.factory import HardwareFactory
except ImportError:
    HardwareFactory = None

class HardwareConfigDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        install_global_stylesheet_color_patch(lambda: get_active_dark_mode())
        self.cfg = config
        self.setWindowTitle("시스템 설정")
        # @codesyncer(버튼 잘림 수정): 고정 1280×850 이 작은 화면(노트북/축소 창)에서
        #   화면보다 커져 하단 버튼(Add Device/Delete)이 잘렸음 — 가용 영역으로 클램프.
        try:
            from PyQt5.QtWidgets import QApplication
            _scr = QApplication.primaryScreen().availableGeometry()
            self.resize(min(1280, _scr.width() - 60), min(850, _scr.height() - 60))
        except Exception:
            self.resize(1280, 850)

        # 폰트 및 스타일 설정 - 메인 UI와 통일
        # @codesyncer: QFont(family, 13)는 13'pt'(고DPI서 ~21-24px)라 폼 라벨만 거대해짐.
        #   pixelSize 13 으로 필드(13px)와 통일. 패밀리도 앱 스택(Segoe UI/Malgun).
        _base_font = QFont("Segoe UI")
        _base_font.setPixelSize(13)
        self.setFont(_base_font)
        # Style is fully applied by apply_theme() after widget creation.

        try:
            self.temp_inventory = [dict(x) for x in self.cfg.config_data.get("inventory", [])]
            self.temp_roles = copy.deepcopy(self.cfg.config_data.get("roles", {"pumps":[], "heater":{}, "outlet":{}, "collector":{}, "push_pump":{}, "gas":{}}))
            # push_pump role은 legacy config에 없을 수 있으므로 기본값 보강
            if "push_pump" not in self.temp_roles:
                self.temp_roles["push_pump"] = {}
            if "gas" not in self.temp_roles:
                self.temp_roles["gas"] = {}
            self.temp_sys_params = copy.deepcopy(self.cfg.config_data.get("system_params", {}))
            self.init_ui()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Init Error", f"Configuration Load Failed: {e}")

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(T.SP_LG, T.SP_LG, T.SP_LG, T.SP_LG)
        main_layout.setSpacing(T.SP_MD)

        # 헤더
        self.lbl_header = QLabel("System Configuration")
        self.lbl_header.setObjectName("DialogHeader")
        main_layout.addWidget(self.lbl_header)

        # 탭 위젯
        self.tabs = QTabWidget()
        self.tab_inv = QWidget()
        self.tab_flow = QWidget()

        # @codesyncer-decision: System Params 탭 제거, 반응기 설정을 Process Mapping으로 통합
        self.tabs.addTab(self.tab_inv, "Hardware Inventory")
        self.tabs.addTab(self.tab_flow, "Process Configuration")

        self.tabs.currentChanged.connect(self.on_tab_switched)

        self.setup_inventory_tab()
        self.setup_workflow_tab()

        main_layout.addWidget(self.tabs)

        # 하단 버튼
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(T.SP_SM)
        self.lbl_status = QLabel("변경 사항은 저장 시 즉시 반영됩니다.")
        self.lbl_status.setObjectName("DialogStatus")

        btn_save = QPushButton("Save && Apply")
        btn_save.setObjectName("SaveBtn")
        btn_save.clicked.connect(self.save_and_restart)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("CancelBtn")
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(self.lbl_status)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        main_layout.addLayout(btn_layout)
        self.apply_theme(get_active_dark_mode())

    def apply_theme(self, is_dark: bool):
        """Apply dialog palette so hardware popup follows dark/light mode."""
        set_active_dark_mode(is_dark)
        self._is_dark_theme = bool(is_dark)
        p = DarkPalette if is_dark else LightPalette
        title_bg = p.BG_SECONDARY if is_dark else p.BG_SECONDARY
        base_text = p.TEXT_PRIMARY
        muted_text = p.TEXT_SECONDARY
        selected_bg = p.ACCENT_BLUE
        selected_border = p.ACCENT_BLUE_DARK

        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {p.BG_PRIMARY};
                color: {base_text};
                font-family: {T.FONT};
                font-size: 13px;
            }}
            QWidget {{
                color: {base_text};
                background-color: {p.BG_PRIMARY};
            }}
            QLabel {{
                color: {base_text};
                background: transparent;
                font-family: {T.FONT};
                font-size: 13px;
            }}
            QTabWidget::pane {{
                border: 1px solid {p.BORDER_PRIMARY};
                background: {p.BG_SECONDARY};
                border-radius: 4px;
            }}
            QTabWidget, QStackedWidget {{
                background: {p.BG_SECONDARY};
            }}
            QStackedWidget > QWidget {{
                background: {p.BG_SECONDARY};
            }}
            QTabBar::tab {{
                background: {p.BG_TERTIARY};
                color: {muted_text};
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
                min-width: 168px;
                min-height: 32px;
                border: 1px solid {p.BORDER_PRIMARY};
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {title_bg};
                color: {p.ACCENT_BLUE};
                border-bottom: 2px solid {p.ACCENT_BLUE};
            }}
            QListWidget {{
                background-color: {p.BG_SECONDARY};
                border: 1px solid {p.BORDER_PRIMARY};
                border-radius: 4px;
                outline: none;
                font-size: 13px;
            }}
            QListWidget::item {{
                /* @codesyncer(글자 잘림 수정): 상하 10px 패딩이 커스텀 행 위젯
                   (sizeHint 42px, 내부 마진 7px)의 가용 높이를 22px 로 줄여
                   장비명이 세로로 잘렸음 — 상하 패딩은 행 위젯 마진에 위임. */
                padding: 4px 12px;
                border-bottom: 1px solid {p.BORDER_SECONDARY};
            }}
            QListWidget::item:selected {{
                background-color: {selected_bg};
                color: #ffffff;
                border-left: 3px solid {selected_border};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {p.BG_HOVER};
                border-left: 3px solid {p.ACCENT_BLUE};
            }}
            QGroupBox {{
                font-weight: 600;
                font-size: 13px;
                border: 1px solid {p.BORDER_PRIMARY};
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 16px;
                background: {p.BG_SECONDARY};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                top: 2px;
                padding: 4px 10px;
                color: {p.ACCENT_BLUE};
                font-weight: 600;
                font-size: 13px;
                background-color: {p.BG_SECONDARY};
                border-radius: 4px;
            }}
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
                padding: 6px 10px;
                border: 1px solid {p.BORDER_INPUT};
                border-radius: 4px;
                background: {p.BG_INPUT};
                color: {p.TEXT_INPUT};
                min-height: 28px;
                font-size: 13px;
                font-weight: 600;
                font-family: {T.FONT_MONO};
            }}
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
                border: 1px solid {p.ACCENT_BLUE};
            }}
            QLineEdit:read-only {{
                background: {p.BG_TERTIARY};
                color: {p.TEXT_SECONDARY};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {p.BG_SECONDARY};
                color: {base_text};
                border: 1px solid {p.BORDER_PRIMARY};
                selection-background-color: {p.ACCENT_BLUE};
                selection-color: #ffffff;
                outline: none;
            }}
            /* @codesyncer: 버튼 통일 체계 — primary(채움) 1개(Save)만 강조,
               나머지는 일관된 아웃라인(secondary). hover 시 채워짐. */
            QPushButton {{
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: 600;
                font-size: 13px;
                min-height: 28px;
                border: 1px solid {p.BORDER_PRIMARY};
                background-color: transparent;
                color: {base_text};
            }}
            QPushButton:hover {{
                background-color: {p.BG_HOVER};
                border-color: {p.ACCENT_BLUE};
                color: {p.ACCENT_BLUE};
            }}
            QPushButton#SaveBtn {{
                background-color: {p.ACCENT_BLUE};
                color: #ffffff;
                border: 1px solid {p.ACCENT_BLUE_DARK};
            }}
            QPushButton#SaveBtn:hover {{
                background-color: {p.ACCENT_BLUE_DARK};
                color: #ffffff;
            }}
            QPushButton#AddBtn {{
                background-color: transparent;
                color: {p.ACCENT_BLUE};
                border: 1px solid {p.ACCENT_BLUE};
            }}
            QPushButton#AddBtn:hover {{
                background-color: {p.ACCENT_BLUE};
                color: #ffffff;
            }}
            QPushButton#DelBtn {{
                background-color: transparent;
                color: {p.ACCENT_RED};
                border: 1px solid {p.ACCENT_RED};
            }}
            QPushButton#DelBtn:hover {{
                background-color: {p.ACCENT_RED};
                color: #ffffff;
            }}
            QPushButton#CancelBtn {{
                background-color: transparent;
                color: {muted_text};
                border: 1px solid {p.BORDER_PRIMARY};
            }}
            QPushButton#CancelBtn:hover {{
                background-color: {p.BG_HOVER};
                color: {base_text};
                border-color: {p.BORDER_LIGHT};
            }}
            QScrollArea {{
                border: none;
                background: {p.BG_SECONDARY};
            }}
            QScrollArea > QWidget > QWidget {{
                background: {p.BG_SECONDARY};
            }}
            QAbstractScrollArea {{
                background: {p.BG_SECONDARY};
            }}
            QWidget#qt_scrollarea_viewport {{
                background: {p.BG_SECONDARY};
            }}
            QSplitter::handle {{
                background: {p.SEPARATOR};
            }}
            QFrame[frameShape="4"] {{
                background-color: {p.SEPARATOR};
                max-height: 1px;
            }}
            QLabel#DialogHeader {{
                font-size: 16px;
                font-weight: 700;
                color: {p.TEXT_PRIMARY};
                padding: 4px 0;
            }}
            QLabel#DialogStatus {{
                color: {p.TEXT_SECONDARY};
                font-size: 12px;
            }}
            QLabel#DialogSectionTitle {{
                font-weight: 700;
                font-size: 13px;
                color: {p.TEXT_PRIMARY};
                padding: 2px 0;
            }}
            QLabel#DialogHintLabel {{
                color: {p.TEXT_SECONDARY};
                font-size: 12px;
                padding: 4px 0;
            }}
            QLabel#DialogSubHeading {{
                font-weight: 700;
                color: {p.TEXT_PRIMARY};
                font-size: 12px;
            }}
            QLabel#DialogCalcValue {{
                color: {p.ACCENT_BLUE};
                font-weight: 700;
                font-size: 12px;
            }}
            QLabel#DialogHelpChip {{
                background: {p.BG_TERTIARY};
                border: 1px solid {p.BORDER_PRIMARY};
                border-radius: 4px;
                padding: 4px 6px;
                color: {p.TEXT_SECONDARY};
                font-size: 10px;
                margin-top: 4px;
            }}
            QFrame#DialogDivider {{
                background-color: {p.SEPARATOR};
                max-height: 1px;
                min-height: 1px;
                border: none;
            }}
            QGroupBox#DialogReactorGroup {{
                margin-top: 6px;
                max-height: 240px;
            }}
            QLineEdit#DialogReadOnlyField {{
                background: {p.BG_TERTIARY};
                color: {p.TEXT_SECONDARY};
            }}
            """
        )
        remap_widget_tree_styles(self, is_dark)

        if hasattr(self, "role_list"):
            self.refresh_role_list()

    def on_tab_switched(self, index):
        self.save_curr_inv_form()
        if index == 1:
            self.update_role_combos()
            
    # =========================================================================
    # TAB 1: 장치 관리
    # =========================================================================
    def setup_inventory_tab(self):
        layout = QHBoxLayout(self.tab_inv)
        layout.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        layout.setSpacing(T.SP_LG)

        # 좌측 패널 - 장치 목록
        left_panel = QVBoxLayout()
        left_panel.setSpacing(T.SP_SM)
        lbl_list = QLabel("Registered Devices")
        lbl_list.setObjectName("DialogSectionTitle")
        # 헤더 행: 제목 + Scan (시리얼 버스 자동 발견)
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.addWidget(lbl_list)
        hdr.addStretch()
        self.btn_scan = QPushButton("⟳  Scan")
        self.btn_scan.setToolTip("시리얼 버스를 스캔해 감지된 포트/USB 장비를 확인하고\n"
                                 "장비로 추가할 수 있습니다.")
        self.btn_scan.clicked.connect(self.scan_ports)
        hdr.addWidget(self.btn_scan)

        self.inv_list = QListWidget()
        self.inv_list.setFixedWidth(320)
        self.inv_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inv_list.currentRowChanged.connect(self.on_inv_selected)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)
        btn_add = QPushButton("Add Device")
        btn_add.setObjectName("AddBtn")
        btn_add.clicked.connect(self.add_device)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("DelBtn")
        btn_del.clicked.connect(self.del_device)
        btn_box.addWidget(btn_add)
        btn_box.addWidget(btn_del)

        left_panel.addLayout(hdr)
        left_panel.addWidget(self.inv_list)
        left_panel.addLayout(btn_box)

        # 우측 패널 - 장치 속성
        self.gb_inv_detail = QGroupBox("Device Properties")
        self.gb_inv_detail.setEnabled(False)
        form = QFormLayout()
        form.setContentsMargins(T.SP_LG, T.SP_LG, T.SP_LG, T.SP_LG)
        form.setVerticalSpacing(T.SP_SM)      # 밀도↑ (행 간격 축소)
        form.setHorizontalSpacing(T.SP_LG)
        form.setLabelAlignment(Qt.AlignRight)

        self.txt_dev_id = QLineEdit()
        self.txt_dev_id.setReadOnly(True)
        self.txt_dev_id.setObjectName("DialogReadOnlyField")

        self.txt_dev_name = QLineEdit()
        self.txt_dev_name.setPlaceholderText("e.g. Pump_A")
        self.txt_dev_name.textChanged.connect(self.update_inv_item_text)

        self.cb_driver = QComboBox()

        self.cb_port = QComboBox()
        self.cb_port.setEditable(True)

        if HardwareFactory:
            self.cb_driver.addItems(HardwareFactory.get_available_drivers())
        else:
            self.cb_driver.addItems(["가상펌프", "가상밸브", "가상히터"])

        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except:
            ports = []
        ports.append("Mock_Port")
        self.cb_port.addItems(ports)

        # @codesyncer-decision: 드라이버별 추가 설정 (채널/주소)
        self.lbl_addr = QLabel("Address:")
        self.sp_addr = QSpinBox()
        self.sp_addr.setRange(0, 127)
        self.sp_addr.setValue(0)
        self.sp_addr.setToolTip("3-Way valve: relay channel (Arduino 1–4 / ESP32 이더넷 1–8)\n12-Way valve: RS-485 address (0, 1, 2...)\nMFC: MKP RS485 slave address (0–255)")
        self.lbl_addr.setVisible(False)
        self.sp_addr.setVisible(False)

        # @codesyncer-decision: MFC 전용 규격 — Full Scale(sccm=100% 기준). MKP 는
        #   장비에서 Full Scale 을 읽어 자동 채택하므로 이 값은 '선언/폴백'용
        #   (연결 전 Manual 스핀박스 상한, 장비 조회 실패 시 환산 기준).
        self.lbl_maxsccm = QLabel("Full Scale (sccm):")
        self.sp_maxsccm = QDoubleSpinBox()
        self.sp_maxsccm.setRange(0.1, 100000.0)
        self.sp_maxsccm.setDecimals(1)
        self.sp_maxsccm.setValue(100.0)
        self.sp_maxsccm.setToolTip("MFC 풀스케일 유량(100% 기준, sccm).\n"
                                   "연결 시 장비 Read Full Scale 값으로 자동 대체됨.")
        self.lbl_maxsccm.setVisible(False)
        self.sp_maxsccm.setVisible(False)

        # @codesyncer-decision: USB 자동 매칭 필드 (VID/PID/Serial/Probe)
        #   - VID/PID 지정 시 부팅 시 find_port_by_usb_info로 COM 자동 재매칭
        #   - Serial: 동일 VID/PID 여러 장치 간 고유 식별
        #   - Probe: 같은 VID/PID + Serial 없음 환경에서 핸드셰이크로 구분
        self.txt_vid = QLineEdit()
        self.txt_vid.setPlaceholderText("0403 (hex, optional)")
        self.txt_vid.setToolTip("USB Vendor ID in hex (e.g. 0403 for FTDI).\n"
                                "비워두면 COM Port 문자열을 그대로 사용합니다.")
        self.txt_pid = QLineEdit()
        self.txt_pid.setPlaceholderText("6001 (hex, optional)")
        self.txt_pid.setToolTip("USB Product ID in hex (e.g. 6001 for FT232R).")
        self.txt_serial = QLineEdit()
        self.txt_serial.setPlaceholderText("Serial number (optional)")
        self.txt_serial.setToolTip("USB serial string for uniquely identifying\n"
                                   "one device among multiples with the same VID/PID.")

        self.cb_probe = QComboBox()
        self.cb_probe.addItem("(none)", None)
        self.cb_probe.addItem("Chemyx", "chemyx")
        self.cb_probe.addItem("Runze", "runze")
        self.cb_probe.addItem("Reaxus", "reaxus")
        self.cb_probe.setToolTip("같은 VID/PID 장치 여러 개가 있을 때\n"
                                 "핸드셰이크로 실제 장치를 구분합니다.\n"
                                 "• Chemyx: view parameter 응답 확인\n"
                                 "• Runze: 위치 조회 바이너리 응답\n"
                                 "• Reaxus: PR (pressure read) 응답")

        self.btn_autofill_usb = QPushButton("Autofill from selected COM")
        self.btn_autofill_usb.setToolTip("현재 COM Port 드롭다운에 선택된 포트의\nVID/PID/Serial을 자동으로 읽어 채웁니다.")
        self.btn_autofill_usb.clicked.connect(self.autofill_usb_info)

        form.addRow("Device ID:", self.txt_dev_id)
        form.addRow("Name:", self.txt_dev_name)
        form.addRow("Driver:", self.cb_driver)
        form.addRow("COM Port:", self.cb_port)
        form.addRow(self.lbl_addr, self.sp_addr)
        form.addRow(self.lbl_maxsccm, self.sp_maxsccm)
        form.addRow("USB VID:", self.txt_vid)
        form.addRow("USB PID:", self.txt_pid)
        form.addRow("USB Serial:", self.txt_serial)
        form.addRow("Probe:", self.cb_probe)
        form.addRow("", self.btn_autofill_usb)
        # 위젯 생성/폼 배치가 끝난 뒤 연결해야 초기 addItems 신호에서 AttributeError가 나지 않는다.
        self.cb_driver.currentIndexChanged.connect(self.on_driver_changed)
        for w in (self.txt_vid, self.txt_pid, self.txt_serial):
            w.textChanged.connect(self.save_curr_inv_form)
        self.cb_probe.currentIndexChanged.connect(self.save_curr_inv_form)
        self.sp_addr.valueChanged.connect(self.save_curr_inv_form)
        self.sp_maxsccm.valueChanged.connect(self.save_curr_inv_form)

        self.gb_inv_detail.setLayout(form)
        layout.addLayout(left_panel)
        layout.addWidget(self.gb_inv_detail, 1)

        # 현재 인벤토리 인덱스 초기화
        self.curr_inv_idx = -1
        self.refresh_inv_list()

    # ── 장비 리스트: 카드형(상태점 + 이름 + 타입 뱃지) · Opentrons/Tecan 스타일 ──
    def _detect_ports(self):
        """현재 시스템 시리얼 포트 목록 [{device,desc,vid,pid,serial}]."""
        out = []
        try:
            for p in serial.tools.list_ports.comports():
                out.append({
                    "device": p.device,
                    "desc": p.description or "",
                    "vid": f"{p.vid:04X}" if p.vid else "",
                    "pid": f"{p.pid:04X}" if p.pid else "",
                    "serial": p.serial_number or "",
                })
        except Exception:
            pass
        return out

    def _device_status(self, item, ports):
        """설정된 COM/USB가 현재 시스템에 있나 — present · absent · mock."""
        port = (item.get('port') or '').strip()
        vid = (item.get('vid') or '').strip().upper()
        pid = (item.get('pid') or '').strip().upper()
        if not port or port == 'Mock_Port':
            return 'mock'
        # TCP/IP 장치 (예: "192.168.0.60:5000") — 시리얼 스캔 대상 아님
        if port.lower().startswith('tcp://') or re.match(r'^\d{1,3}(\.\d{1,3}){3}(:\d+)?$', port):
            return 'net'
        if vid and pid:
            return 'present' if any(p['vid'].upper() == vid and p['pid'].upper() == pid
                                    for p in ports) else 'absent'
        return 'present' if any(p['device'] == port for p in ports) else 'absent'

    def _status_color(self, status):
        p = DarkPalette if getattr(self, "_is_dark_theme", get_active_dark_mode()) else LightPalette
        return {'present': p.ACCENT_GREEN, 'absent': p.STATE_WARN,
                'net': p.STATE_INFO}.get(status, p.TEXT_DISABLED)

    def _short_driver(self, driver):
        """드라이버 표시명 → 짧은 타입(괄호 안 벤더 우선). '시린지 펌프 (Chemyx)'→'Chemyx'."""
        d = (driver or "").strip()
        if d.endswith(")") and "(" in d:
            return d[d.rfind("(") + 1:-1].strip()
        return d

    def _build_device_row(self, item, status):
        p = DarkPalette if getattr(self, "_is_dark_theme", get_active_dark_mode()) else LightPalette
        is_dark = getattr(self, "_is_dark_theme", True)
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 7, 12, 7)
        lay.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{self._status_color(status)};font-size:10px;background:transparent;")
        dot.setToolTip({'present': '감지됨 (포트/USB 존재)',
                        'absent': '설정된 포트/USB를 찾을 수 없음',
                        'net': '네트워크 장치 (TCP/IP) — 연결은 앱 시작 시 확인',
                        'mock': '가상 · 미설정'}.get(status, ''))
        name = QLabel(item.get('name', ''))
        name.setStyleSheet(f"color:{p.TEXT_PRIMARY};font-size:13px;font-weight:600;background:transparent;")
        badge = QLabel(self._short_driver(item.get('driver', '')))
        badge.setToolTip(item.get('driver', ''))
        badge.setStyleSheet(f"color:{p.TEXT_TERTIARY if is_dark else p.TEXT_SECONDARY};"
                            f"font-size:11px;background:transparent;")
        lay.addWidget(dot)
        lay.addWidget(name)
        lay.addStretch()
        lay.addWidget(badge)
        w._name_lbl = name
        w._badge_lbl = badge
        return w

    # 인벤토리 표시 순서 — 종류(장비 계열) → 이름 자연정렬 (펌프2 < 펌프10)
    _DEV_TYPE_RANK = (("펌프", 0), ("밸브", 1), ("히터", 2), ("수집기", 3),
                      ("분취기", 3), ("샘플러", 4), ("MFC", 5), ("위상센서", 6))

    def _dev_sort_key(self, it):
        drv = it.get("driver", "") or ""
        rank = 7
        for kw, r in self._DEV_TYPE_RANK:
            if kw in drv:
                rank = r
                break
        name = it.get("name", "") or ""
        # 자연 정렬 토큰 (int/str 혼합 비교 안전: (0,숫자) < (1,문자))
        toks = tuple((0, int(t)) if t.isdigit() else (1, t.lower())
                     for t in re.split(r"(\d+)", name) if t)
        return (rank, toks, drv)

    def _inv_row_of(self, inv_idx):
        """temp_inventory 인덱스 → 현재 표시 행 (없으면 -1)."""
        for r, i in enumerate(getattr(self, "_inv_row_to_idx", [])):
            if i == inv_idx:
                return r
        return -1

    def _select_inv_by_id(self, dev_id):
        for i, it in enumerate(self.temp_inventory):
            if it.get("id") == dev_id:
                r = self._inv_row_of(i)
                if r >= 0:
                    self.inv_list.setCurrentRow(r)
                return

    def refresh_inv_list(self):
        # @codesyncer: 리스트를 '추가 순서(하단 append)'가 아니라 종류→이름 정렬로 표시.
        #   표시행 ≠ temp_inventory 인덱스가 되므로 _inv_row_to_idx 매핑을 경유하고,
        #   선택 복원은 행 번호가 아닌 dev_id 기준(정렬로 위치가 바뀌어도 유지).
        curr_id = None
        if 0 <= getattr(self, "curr_inv_idx", -1) < len(self.temp_inventory):
            curr_id = self.temp_inventory[self.curr_inv_idx].get("id")
        # @codesyncer(버그픽스): 선택 복원(setCurrentRow)이 on_inv_selected 를 재진입시켜
        #   save_curr_inv_form 이 'stale curr_inv_idx' 위치의 다른 장비를 폼 내용으로
        #   덮어쓰던 결함 — 외부에서 temp_inventory 가 insert/삭제로 밀린 경우 데이터 오염.
        #   복원 전 무장해제(-1 → save no-op), 복원 클릭은 로드만 수행.
        self.curr_inv_idx = -1
        self.inv_list.blockSignals(True)
        self.inv_list.clear()
        ports = self._detect_ports()
        order = sorted(range(len(self.temp_inventory)),
                       key=lambda i: self._dev_sort_key(self.temp_inventory[i]))
        self._inv_row_to_idx = order
        for inv_i in order:
            it = self.temp_inventory[inv_i]
            status = self._device_status(it, ports)
            row = QListWidgetItem()
            row.setData(Qt.UserRole, it.get("id"))
            widget = self._build_device_row(it, status)
            row.setSizeHint(QSize(300, 46))   # 행 위젯(글자+마진) + item 상하 패딩 8px 여유
            self.inv_list.addItem(row)
            self.inv_list.setItemWidget(row, widget)
        self.inv_list.blockSignals(False)

        if self.inv_list.count() > 0:
            r = -1
            if curr_id is not None:
                for row_i in range(self.inv_list.count()):
                    if self.inv_list.item(row_i).data(Qt.UserRole) == curr_id:
                        r = row_i
                        break
            self.inv_list.setCurrentRow(r if r >= 0 else 0)
        else:
            self.gb_inv_detail.setEnabled(False)
            self.clear_inv_form()

    def scan_ports(self):
        """시리얼 버스 스캔 → 감지된 포트를 보여주고 장비로 추가."""
        ports = self._detect_ports()
        dlg = QDialog(self)
        dlg.setWindowTitle("Scan — Detected Ports")
        dlg.setStyleSheet(self.styleSheet())
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        hdr = QLabel(f"감지된 시리얼 포트  ·  {len(ports)}개")
        hdr.setObjectName("DialogSectionTitle")
        v.addWidget(hdr)
        lst = QListWidget()
        used = {(it.get('port') or '') for it in self.temp_inventory}
        for pt in ports:
            usb = f"  [{pt['vid']}:{pt['pid']}]" if pt['vid'] else ""
            tag = "   · 등록됨" if pt['device'] in used else ""
            row = QListWidgetItem(f"{pt['device']}{usb}   {pt['desc']}{tag}")
            row.setData(Qt.UserRole, pt)
            lst.addItem(row)
        if not ports:
            _e = QListWidgetItem("감지된 포트가 없습니다.")
            _e.setFlags(Qt.NoItemFlags)
            lst.addItem(_e)
        v.addWidget(lst)
        bar = QHBoxLayout()
        bar.addStretch()
        btn_add = QPushButton("+  장비로 추가")
        btn_add.setObjectName("AddBtn")
        btn_close = QPushButton("Close")
        btn_close.setObjectName("CancelBtn")
        bar.addWidget(btn_add)
        bar.addWidget(btn_close)
        v.addLayout(bar)

        def _do_add():
            it = lst.currentItem()
            pt = it.data(Qt.UserRole) if it else None
            if not pt:
                return
            self.save_curr_inv_form()
            new = {"id": f"dev_{str(uuid.uuid4())[:8]}", "name": "New_Device",
                   "driver": "가상펌프", "port": pt['device'],
                   "vid": pt.get('vid', ''), "pid": pt.get('pid', ''),
                   "serial": pt.get('serial', '')}
            self.temp_inventory.append(new)
            self.refresh_inv_list()
            self._select_inv_by_id(new["id"])   # 정렬된 위치에서 선택
            self.on_driver_changed(0)
            dlg.accept()

        btn_add.clicked.connect(_do_add)
        btn_close.clicked.connect(dlg.reject)
        dlg.resize(540, 420)
        dlg.exec_()

    def on_inv_selected(self, row):
        if row < 0:
            return
        self.save_curr_inv_form()
        # 표시행 → 실제 인벤토리 인덱스 (정렬 매핑 경유)
        mapping = getattr(self, "_inv_row_to_idx", None)
        self.curr_inv_idx = mapping[row] if (mapping and row < len(mapping)) else row
        data = self.temp_inventory[self.curr_inv_idx]
        self.gb_inv_detail.setEnabled(True)
        self.gb_inv_detail.setTitle(f"Edit: {data['name']}")

        self.txt_dev_id.setText(data['id'])
        self.txt_dev_name.setText(data['name'])
        self.cb_driver.setCurrentText(data['driver'])
        self.cb_port.setCurrentText(data['port'])

        # 채널/주소 로드 (setValue → valueChanged → save 재진입 방지)
        drv = data.get('driver', '')
        self.sp_addr.blockSignals(True)
        self.sp_maxsccm.blockSignals(True)
        if "3방향 밸브" in drv:
            self.sp_addr.setValue(int(data.get('channel', 1)))
        elif "12방향 밸브" in drv:
            self.sp_addr.setValue(int(data.get('address', 0)))
        elif "MFC" in drv:
            _st = data.get('settings') or {}
            self.sp_addr.setValue(int(_st.get('slave_addr', _st.get('modbus_addr', 1)) or 1))
            self.sp_maxsccm.setValue(float(_st.get('max_sccm', 100.0) or 100.0))
        else:
            self.sp_addr.setValue(0)
        self.sp_addr.blockSignals(False)
        self.sp_maxsccm.blockSignals(False)

        # USB 자동 매칭 필드 로드 (textChanged → save_curr_inv_form 재진입 방지)
        for w in (self.txt_vid, self.txt_pid, self.txt_serial):
            w.blockSignals(True)
        self.cb_probe.blockSignals(True)
        self.txt_vid.setText(data.get('vid', '') or '')
        self.txt_pid.setText(data.get('pid', '') or '')
        self.txt_serial.setText(data.get('serial', '') or '')
        probe_val = data.get('probe')
        probe_idx = 0  # (none)
        if probe_val:
            for i in range(self.cb_probe.count()):
                if self.cb_probe.itemData(i) == probe_val:
                    probe_idx = i
                    break
        self.cb_probe.setCurrentIndex(probe_idx)
        for w in (self.txt_vid, self.txt_pid, self.txt_serial):
            w.blockSignals(False)
        self.cb_probe.blockSignals(False)

    def save_curr_inv_form(self):
        if not hasattr(self, 'curr_inv_idx') or self.curr_inv_idx < 0: return
        if self.curr_inv_idx >= len(self.temp_inventory): return
        item = self.temp_inventory[self.curr_inv_idx]
        item['name'] = self.txt_dev_name.text()
        item['driver'] = self.cb_driver.currentText()
        item['port'] = self.cb_port.currentText()

        # 채널/주소 저장
        drv = item['driver']
        if "3방향 밸브" in drv:
            item['channel'] = self.sp_addr.value()
            item.pop('address', None)  # 다른 키 제거
        elif "12방향 밸브" in drv:
            item['address'] = self.sp_addr.value()
            item.pop('channel', None)
        elif "MFC" in drv:
            # MFC 규격 → settings (slave_addr/max_sccm/baudrate). hw_manager 가 읽음.
            item.pop('channel', None)
            item.pop('address', None)
            st = item.get('settings') or {}
            st['slave_addr'] = self.sp_addr.value()
            st['max_sccm'] = round(float(self.sp_maxsccm.value()), 1)
            st.setdefault('baudrate', 9600)
            item['settings'] = st
        else:
            item.pop('channel', None)
            item.pop('address', None)

        # USB 자동 매칭 필드 저장 — 빈 값은 키 자체를 제거
        vid = self.txt_vid.text().strip().upper()
        pid = self.txt_pid.text().strip().upper()
        serial_str = self.txt_serial.text().strip()
        probe = self.cb_probe.currentData()
        if vid:
            item['vid'] = vid
        else:
            item.pop('vid', None)
        if pid:
            item['pid'] = pid
        else:
            item.pop('pid', None)
        if serial_str:
            item['serial'] = serial_str
        else:
            item.pop('serial', None)
        if probe:
            item['probe'] = probe
        else:
            item.pop('probe', None)

    def update_inv_item_text(self, text):
        if hasattr(self, 'curr_inv_idx') and self.curr_inv_idx >= 0:
            item = self.inv_list.item(self._inv_row_of(self.curr_inv_idx))
            w = self.inv_list.itemWidget(item) if item else None
            if w is None:
                return
            if hasattr(w, '_name_lbl'):
                w._name_lbl.setText(text)
            if hasattr(w, '_badge_lbl'):
                w._badge_lbl.setText(self._short_driver(self.cb_driver.currentText()))
                w._badge_lbl.setToolTip(self.cb_driver.currentText())

    def on_driver_changed(self, idx):
        # 초기화 도중(currentIndexChanged) 호출될 수 있으므로 방어 처리
        if not hasattr(self, "lbl_addr") or not hasattr(self, "sp_addr"):
            return

        drv = self.cb_driver.currentText()
        curr_name = self.txt_dev_name.text()
        if "New_Device" in curr_name or not curr_name:
            prefix = "장치"
            if "펌프" in drv: prefix = "펌프"
            elif "밸브" in drv: prefix = "밸브"
            elif "히터" in drv: prefix = "히터"
            if hasattr(self, 'curr_inv_idx') and self.curr_inv_idx >= 0:
                self.txt_dev_name.setText(f"{prefix}_{self.curr_inv_idx+1}")
        self.update_inv_item_text(self.txt_dev_name.text())

        # @codesyncer-decision: config 값 = 실제 하드웨어 주소 (직통, 변환 없음)
        # MFC 전용 Full Scale 필드는 MFC 드라이버에서만 노출
        is_mfc = "MFC" in drv
        self.lbl_maxsccm.setVisible(is_mfc)
        self.sp_maxsccm.setVisible(is_mfc)
        if "3방향 밸브" in drv:
            self.lbl_addr.setText("Relay Ch.:")
            # ESP32-S3-ETH-8DI-8RO 는 8채널, 구형 Arduino UNO 릴레이는 4채널
            if "ESP32" in drv:
                self.sp_addr.setRange(1, 8)
                self.sp_addr.setToolTip("Relay channel number (1–8, ESP32-S3-ETH-8DI-8RO)\n"
                                        "COM Port 칸에는 보드 IP를 입력 (예: 192.168.0.60:5000)")
            else:
                self.sp_addr.setRange(1, 4)
                self.sp_addr.setToolTip("Relay channel number (1–4)")
            self.lbl_addr.setVisible(True)
            self.sp_addr.setVisible(True)
        elif "12방향 밸브" in drv:
            self.lbl_addr.setText("RS-485 Addr.:")
            self.sp_addr.setRange(0, 127)
            self.sp_addr.setToolTip("RS-485 device address (0, 1, 2, 3...)")
            self.lbl_addr.setVisible(True)
            self.sp_addr.setVisible(True)
        elif is_mfc:
            # MKP RS485 슬레이브 주소 (0~255) — 유량 규격은 Full Scale 필드
            self.lbl_addr.setText("RS-485 Addr.:")
            self.sp_addr.setRange(0, 255)
            self.sp_addr.setToolTip("MKP RS485 slave address (0–255, 기본 1)")
            self.lbl_addr.setVisible(True)
            self.sp_addr.setVisible(True)
        else:
            self.lbl_addr.setVisible(False)
            self.sp_addr.setVisible(False)

    def add_device(self):
        self.save_curr_inv_form()
        new_item = {"id": f"dev_{str(uuid.uuid4())[:8]}", "name": "New_Device", "driver": "가상펌프", "port": "Mock_Port"}
        self.temp_inventory.append(new_item)
        self.refresh_inv_list()
        self._select_inv_by_id(new_item["id"])   # 정렬된 위치에서 선택 (하단 append 아님)
        self.on_driver_changed(0)

    def del_device(self):
        # 표시행이 아니라 매핑된 실제 인덱스 삭제 (정렬 도입 후 필수)
        idx = getattr(self, "curr_inv_idx", -1)
        if 0 <= idx < len(self.temp_inventory):
            del self.temp_inventory[idx]
            self.curr_inv_idx = -1
            self.refresh_inv_list()

    def clear_inv_form(self):
        self.txt_dev_id.clear(); self.txt_dev_name.clear(); self.curr_inv_idx = -1

    # =========================================================================
    # TAB 2: 공정 설정 (Process Mapping + System Params 통합)
    # =========================================================================
    def setup_workflow_tab(self):
        layout = QHBoxLayout(self.tab_flow)
        layout.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        layout.setSpacing(T.SP_LG)

        # 좌측 패널 - 구성요소 목록 (장치관리 탭과 동일 구조)
        left_panel = QVBoxLayout()
        left_panel.setSpacing(T.SP_SM)

        lbl_comp = QLabel("Process Modules")
        lbl_comp.setObjectName("DialogSectionTitle")
        left_panel.addWidget(lbl_comp)

        self.role_list = QListWidget()
        self.role_list.setFixedWidth(320)
        self.role_list.currentRowChanged.connect(self.on_role_selected)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)
        btn_add = QPushButton("Add Pump Group")
        btn_add.setObjectName("AddBtn")
        btn_add.clicked.connect(self.add_pump_role)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("DelBtn")
        btn_del.clicked.connect(self.del_pump_role)
        btn_box.addWidget(btn_add)
        btn_box.addWidget(btn_del)

        left_panel.addWidget(self.role_list)
        left_panel.addLayout(btn_box)

        # 우측 패널 - 설정 영역
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(T.SP_SM)

        # 스택 위젯 (구성요소별 설정)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._create_placeholder_widget())

        self.page_pump = QWidget()
        self.setup_pump_ui()
        self.stack.addWidget(self.page_pump)

        self.page_heater = QWidget()
        self.setup_heater_ui()
        self.stack.addWidget(self.page_heater)

        self.page_outlet = QWidget()
        self.setup_outlet_ui()
        self.stack.addWidget(self.page_outlet)

        self.page_collector = QWidget()
        self.setup_collector_ui()
        self.stack.addWidget(self.page_collector)

        self.page_push_pump = QWidget()
        self.setup_push_pump_ui()
        self.stack.addWidget(self.page_push_pump)

        self.page_gas = QWidget()
        self.setup_gas_ui()
        self.stack.addWidget(self.page_gas)

        # 위상센서(OCB350) 역할 페이지 — index 7 (gas 패턴 미러)
        self.page_phase = QWidget()
        self.setup_phase_ui()
        self.stack.addWidget(self.page_phase)

        # 전역 반응기 파라미터 = 스택 페이지(index 8). 모듈 리스트 'System'에서 선택.
        self.setup_system_params_section()
        self.stack.addWidget(self.page_system)

        right_layout.addWidget(self.stack, 1)

        layout.addLayout(left_panel)
        layout.addWidget(right_widget, 1)
        self.refresh_role_list()

    def _create_placeholder_widget(self):
        """선택 안내 위젯 생성"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        lbl = QLabel("← Select a module from the list")
        lbl.setObjectName("DialogHintLabel")
        lay.addWidget(lbl)
        return w

    def setup_system_params_section(self):
        """반응기 & 유체 파라미터 — 전역 설정 페이지(스택). 모든 모듈 공통.

        @codesyncer: 기존엔 우측 하단에 항상 노출돼 '모듈별 선택 ↔ 전역값'이
          한 화면에 섞여 혼란 → 모듈 리스트의 'System' 항목에서 선택하는 페이지로 이동.
        """
        self.page_system = QWidget()
        parent_layout = QVBoxLayout(self.page_system)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.setSpacing(T.SP_SM)
        _hint = QLabel("전역 설정  ·  모든 모듈에 공통 적용됩니다.")
        _hint.setObjectName("DialogHintLabel")
        parent_layout.addWidget(_hint)

        # 반응기 설정 그룹
        g_reactor = QGroupBox("Reactor && Fluidic Parameters")
        g_reactor.setObjectName("DialogReactorGroup")
        h_layout = QHBoxLayout(g_reactor)
        h_layout.setSpacing(0)
        h_layout.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, 10)

        def _vsep():
            sep = QFrame()
            sep.setFrameShape(QFrame.VLine)
            sep.setObjectName("DialogDivider")
            return sep

        # ── 섹션 1: 반응기 치수 ──────────────────────────────
        col1_lay = QVBoxLayout()
        col1_lay.setSpacing(6)
        col1_lay.setContentsMargins(0, 0, T.SP_LG, 0)

        lbl_reactor = QLabel("Reactor Geometry")
        lbl_reactor.setObjectName("DialogSubHeading")
        col1_lay.addWidget(lbl_reactor)

        col1 = QFormLayout()
        col1.setSpacing(6)
        col1.setLabelAlignment(Qt.AlignRight)

        self.sp_r_len = QDoubleSpinBox()
        self.sp_r_len.setRange(0, 1000)
        self.sp_r_len.setSuffix(" m")
        self.sp_r_len.setDecimals(2)
        self.sp_r_len.setToolTip("반응기 튜빙의 전체 길이\n튜빙 포장에 표기된 길이를 입력하세요.")

        self.sp_r_id = QDoubleSpinBox()
        self.sp_r_id.setRange(0, 10)
        self.sp_r_id.setSuffix(" mm")
        self.sp_r_id.setDecimals(2)
        self.sp_r_id.setToolTip("반응기 튜빙의 내경 (ID)\n• 1/16\" OD PEEK: 보통 0.25~0.75mm\n• 1/8\" OD PTFE: 보통 1.0~1.6mm\n튜빙 포장 라벨에서 확인하세요.")

        self.lbl_calc_vol = QLabel("-")
        self.lbl_calc_vol.setObjectName("DialogCalcValue")

        col1.addRow("Length (L):", self.sp_r_len)
        col1.addRow("Inner Dia. (ID):", self.sp_r_id)
        col1.addRow("Volume (V):", self.lbl_calc_vol)
        col1_lay.addLayout(col1)
        col1_lay.addStretch()

        # ── 섹션 2: 라인 설정 ──────────────────────────────
        col2_lay = QVBoxLayout()
        col2_lay.setSpacing(6)
        col2_lay.setContentsMargins(T.SP_LG, 0, T.SP_LG, 0)

        lbl_line = QLabel("Transfer Lines")
        lbl_line.setObjectName("DialogSubHeading")
        col2_lay.addWidget(lbl_line)

        col2 = QFormLayout()
        col2.setSpacing(6)
        col2.setLabelAlignment(Qt.AlignRight)

        self.sp_post_r = QDoubleSpinBox()
        self.sp_post_r.setRange(0, 100)
        self.sp_post_r.setSuffix(" mL")
        self.sp_post_r.setDecimals(2)
        self.sp_post_r.setToolTip("반응기 출구에서 outlet 밸브까지의 배관 부피\n이 구간을 용매로 밀어내야 반응물이 수집기에 도달합니다.\n측정: 주사기로 물을 채워서 부피 확인")

        self.sp_cl = QDoubleSpinBox()
        self.sp_cl.setRange(0, 100)
        self.sp_cl.setSuffix(" mL")
        self.sp_cl.setDecimals(2)
        self.sp_cl.setToolTip("outlet 밸브에서 수집 튜브까지의 배관 부피\n수집 완료 후 이 부피만큼 추가로 밀어서\n배관에 남은 반응물을 세척 튜브로 회수합니다.")

        self.sp_pf = QDoubleSpinBox()
        self.sp_pf.setRange(0, 100)
        self.sp_pf.setSuffix(" mL/min")
        self.sp_pf.setDecimals(2)
        self.sp_pf.setToolTip("시린지 내용물을 반응기 쪽으로 밀어내는 속도\n• 권장: 6~10 mL/min\n• 너무 빠르면 압력 상승 주의")

        col2.addRow("Post-reactor Vol.:", self.sp_post_r)
        col2.addRow("Collection Line Vol.:", self.sp_cl)
        col2.addRow("Priming Rate:", self.sp_pf)

        # 시퀀스 자동 세척 타이밍 (2026-08-05 사용자 요청: JSON 전용이던
        # system_params.wash_mode 를 콤보로 노출. 볼륨/속도는 펌프 그룹 설정)
        self.cb_wash_mode = QComboBox()
        for _lbl, _val in (("off · 세척 없음", "off"),
                           ("first_step · 첫 스텝만", "first_step"),
                           ("port_change · 포트 변경 시", "port_change"),
                           ("every_step · 매 스텝", "every_step")):
            self.cb_wash_mode.addItem(_lbl, _val)
        self.cb_wash_mode.setToolTip(
            "시퀀스 중 자동 세척(용매 흡인 → 12번 폐액 배출) 실행 시점.\n"
            "세척 볼륨/속도/횟수는 펌프 그룹 설정(Wash Volume/Flow Rate/Cycles)")
        col2.addRow("Wash Mode:", self.cb_wash_mode)
        col2_lay.addLayout(col2)
        col2_lay.addStretch()

        # ── 섹션 3: 합류 구간 ──────────────────────────────
        col3_lay = QVBoxLayout()
        col3_lay.setSpacing(6)
        col3_lay.setContentsMargins(T.SP_LG, 0, 0, 0)

        lbl_mixing = QLabel("Mixing Zone")
        lbl_mixing.setObjectName("DialogSubHeading")
        col3_lay.addWidget(lbl_mixing)

        col3 = QFormLayout()
        col3.setSpacing(6)
        col3.setLabelAlignment(Qt.AlignRight)

        self.sp_mixing_id = QDoubleSpinBox()
        self.sp_mixing_id.setRange(0, 10)
        self.sp_mixing_id.setSuffix(" mm")
        self.sp_mixing_id.setDecimals(2)
        self.sp_mixing_id.setToolTip("Tubing ID at confluence points")

        self.sp_mixing_len = QDoubleSpinBox()
        self.sp_mixing_len.setRange(0, 1000)
        self.sp_mixing_len.setSuffix(" cm")
        self.sp_mixing_len.setDecimals(1)
        self.sp_mixing_len.setToolTip("Total tubing length through all mixing sections\n(e.g. T-mixer → reactor inlet)")

        self.lbl_mixing_deadv = QLabel("-")
        self.lbl_mixing_deadv.setObjectName("DialogCalcValue")

        col3.addRow("ID:", self.sp_mixing_id)
        col3.addRow("Total Length:", self.sp_mixing_len)
        col3.addRow("Dead Vol:", self.lbl_mixing_deadv)
        col3_lay.addLayout(col3)

        lbl_help_mixing = QLabel("Sum all mixing sections (e.g. 50+100=150 cm)")
        lbl_help_mixing.setObjectName("DialogHelpChip")
        lbl_help_mixing.setWordWrap(True)
        col3_lay.addWidget(lbl_help_mixing)
        col3_lay.addStretch()

        h_layout.addLayout(col1_lay)
        h_layout.addWidget(_vsep())
        h_layout.addLayout(col2_lay)
        h_layout.addWidget(_vsep())
        h_layout.addLayout(col3_lay)
        h_layout.addStretch()

        parent_layout.addWidget(g_reactor)
        parent_layout.addStretch(1)   # 콘텐츠를 상단 정렬(하단 여백 흡수)

        # 값 로드 및 시그널 연결
        sp = self.temp_sys_params
        self.sp_r_len.setValue(sp.get("reactor_len_m", 10.0))
        self.sp_r_id.setValue(sp.get("reactor_id_mm", 1.0))
        self.sp_post_r.setValue(sp.get("post_reactor_vol_ml", 2.0))
        self.sp_cl.setValue(sp.get("collection_line_vol_ml", 1.0))
        self.sp_pf.setValue(sp.get("priming_rate_ml_min", 5.0))
        self.sp_syr_refill.setValue(sp.get("syringe_refill_rate", 20.0))
        self.sp_mixing_id.setValue(sp.get("mixing_line_id_mm", 1.5))
        self.sp_mixing_len.setValue(sp.get("mixing_line_len_cm", 150.0))
        _wm_idx = self.cb_wash_mode.findData(
            str(sp.get("wash_mode", "port_change") or "port_change"))
        if _wm_idx < 0:   # 미지 값 → 엔진 _normalize_mode 폴백과 동일하게 port_change
            _wm_idx = self.cb_wash_mode.findData("port_change")
        self.cb_wash_mode.setCurrentIndex(_wm_idx)

        self.sp_r_len.valueChanged.connect(self.calc_r_vol)
        self.sp_r_id.valueChanged.connect(self.calc_r_vol)
        self.calc_r_vol()

        self.sp_mixing_id.valueChanged.connect(self.calc_mixing_deadv)
        self.sp_mixing_len.valueChanged.connect(self.calc_mixing_deadv)
        self.calc_mixing_deadv()

    def setup_pump_ui(self):
        # 스크롤 영역 설정
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, T.SP_XS, 0)

        # @codesyncer-decision: Group 명명 체계로 변경 (Pump_X → Group_X)
        # 이유: 논리적 그룹 개념으로 사용자 이해도 향상

        # 1. 하드웨어 매핑
        g_hw = QGroupBox("Hardware Assignment")
        f_hw = QFormLayout()
        f_hw.setSpacing(T.SP_SM)
        f_hw.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f_hw.setLabelAlignment(Qt.AlignRight)

        self.p_name = QLineEdit()
        self.p_name.setPlaceholderText("e.g. Group_A")

        # @codesyncer-decision: 펌프 위치 속성 추가 (inlet/outlet 구분)
        self.p_position = QComboBox()
        self.p_position.addItems(["Upstream (pre-reactor)", "Downstream (post-reactor)"])
        self.p_position.setToolTip("Upstream: 체류시간(τ) 산출에 포함\nDownstream: Quench·희석 등 후단 처리")

        self.p_motor = QComboBox()
        self.p_motor.currentIndexChanged.connect(self.toggle_syringe_ui)

        self.p_selector = QComboBox()
        self.p_switcher = QComboBox()
        # @codesyncer-decision: 라우팅 스키마(b) — drivers.sampler 슬롯.
        #   NRG motor 전용: 지정 시 오토샘플러 모드(니들이 소스 선택, 예약),
        #   미지정 시 valve 모드(내장 밸브). 외부 selector/switcher 와 상호배타 —
        #   toggle_syringe_ui 가 활성/비활성을 강제한다.
        self.p_sampler = QComboBox()
        self.p_sampler.setToolTip(
            "NRG 펌프 전용 — Cartesian 샘플러와 짝지으면 오토샘플러 모드.\n"
            "일체형: 여기 배정만으로 샘플러가 자동 활성화됩니다 (별도 등록 불필요)")
        self.p_sampler.currentIndexChanged.connect(self.toggle_syringe_ui)

        # 라우팅 뱃지 — motor/sampler 선택에서 유도되는 모드를 실시간 표시.
        # main_valve_enabled=false 경고를 부팅 콘솔이 아닌 설정 시점에 노출.
        self.lbl_routing = QLabel("—")
        self.lbl_routing.setWordWrap(True)
        # 경고 해제 시 setStyleSheet("") 로 스타일이 소실되지 않도록
        # 기본 스타일을 정의해 두고 항상 이것으로 복원한다
        self._routing_style_base = f"font-size: {T.FS_XS};"
        self.lbl_routing.setStyleSheet(self._routing_style_base)

        f_hw.addRow("Group Name:", self.p_name)
        f_hw.addRow("Stream Position:", self.p_position)
        f_hw.addRow("Pump Drive:", self.p_motor)
        f_hw.addRow("Selector Valve (12-Way):", self.p_selector)
        f_hw.addRow("Switching Valve (3-Way):", self.p_switcher)
        f_hw.addRow("Sampler (NRG 전용):", self.p_sampler)
        f_hw.addRow("라우팅 모드:", self.lbl_routing)
        g_hw.setLayout(f_hw)

        # 2. 시린지 설정 (조건부 표시)
        self.widget_syringe_group = QGroupBox("Syringe Parameters")
        lay_syr = QFormLayout(self.widget_syringe_group)
        lay_syr.setSpacing(T.SP_SM)
        lay_syr.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        lay_syr.setLabelAlignment(Qt.AlignRight)

        self.cb_syringe = QComboBox()
        self.syringe_presets = [
            # NJ (Norm-Ject) Plastic syringes — 최대 충전량 기준
            {"label": "NJ 1 mL  (NJ.A01, ID: 4.69mm)",   "vol": 1.0,  "dia": 4.69,   "max_rate": 1.7},
            {"label": "NJ 2 mL  (NJ.A03, ID: 9.65mm)",   "vol": 3.0,  "dia": 9.65,   "max_rate": 7.4},
            {"label": "NJ 5 mL  (NJ.A05, ID: 12.45mm)",  "vol": 6.0,  "dia": 12.45,  "max_rate": 12.3},
            {"label": "NJ 10 mL (NJ.A10, ID: 15.90mm)",  "vol": 12.0, "dia": 15.90,  "max_rate": 20.1},
            {"label": "NJ 20 mL (NJ.A20, ID: 20.05mm)",  "vol": 24.0, "dia": 20.05,  "max_rate": 32.0},
            {"label": "NJ 30 mL (NJ.A30, ID: 22.90mm)",  "vol": 30.0, "dia": 22.90,  "max_rate": 41.8},
            {"label": "NJ 50 mL (NJ.A50, ID: 29.20mm)",  "vol": 60.0, "dia": 29.20,  "max_rate": 67.9},
            # Hamilton GASTIGHT 1000 series
            {"label": "Hamilton 1 mL  (#1001, ID: 4.608mm)",   "vol": 1.0,  "dia": 4.608,  "max_rate": 1.7},
            {"label": "Hamilton 2.5 mL (#1002.5, ID: 7.285mm)","vol": 2.5,  "dia": 7.285,  "max_rate": 4.2},
            {"label": "Hamilton 5 mL  (#1005, ID: 10.30mm)",   "vol": 5.0,  "dia": 10.30,  "max_rate": 8.3},
            {"label": "Hamilton 10 mL (#1010, ID: 14.567mm)",  "vol": 10.0, "dia": 14.567, "max_rate": 16.7},
            {"label": "Hamilton 25 mL (#1025, ID: 22.98mm)",   "vol": 25.0, "dia": 22.98,  "max_rate": 41.7},
            {"label": "Hamilton 50 mL (#1050, ID: 26.99mm)",   "vol": 50.0, "dia": 26.99,  "max_rate": 58.0},
            {"label": "Hamilton 100 mL (#1100, ID: 34.90mm)",  "vol": 100.0,"dia": 34.90,  "max_rate": 96.7},
        ]
        for item in self.syringe_presets:
            self.cb_syringe.addItem(item["label"])
        self.cb_syringe.currentIndexChanged.connect(self.on_syringe_preset_changed)

        self.lbl_syringe_info = QLabel("Selected: -")
        self.lbl_syringe_info.setObjectName("DialogCalcValue")

        # Hidden Data Fields
        self.p_dia = QDoubleSpinBox()
        self.p_dia.setVisible(False)
        self.p_cap = QDoubleSpinBox()
        self.p_cap.setVisible(False)

        # 세척 설정 위젯
        self.p_wash_speed = QDoubleSpinBox()
        self.p_wash_speed.setRange(0.1, 60.0)
        self.p_wash_speed.setSuffix(" mL/min")
        self.p_wash_speed.setDecimals(1)
        self.p_wash_speed.setValue(15.0)
        self.p_wash_speed.setToolTip("Wash dispensing flow rate")

        self.p_wash_count = QSpinBox()
        # @codesyncer-decision: 0 허용 — NRG(1-소스) 그룹은 세척이 기포 퍼지로만 의미가
        #   있어 옵트인(기본 0)인데, 최소 1 강제 시 다이얼로그 편집만으로 켜져버림.
        self.p_wash_count.setRange(0, 10)
        self.p_wash_count.setSuffix(" 회")
        self.p_wash_count.setValue(2)
        self.p_wash_count.setToolTip("Number of wash cycles (aspirate + dispense)\n0 = 세척 끔 (NRG 1-소스 그룹 권장 기본)")

        self.p_wash_volume = QDoubleSpinBox()
        self.p_wash_volume.setRange(0.5, 50.0)
        self.p_wash_volume.setSuffix(" mL")
        self.p_wash_volume.setDecimals(1)
        self.p_wash_volume.setValue(5.0)
        self.p_wash_volume.setToolTip("Aspirate/dispense volume per wash cycle")

        self.sp_syr_refill = QDoubleSpinBox()
        self.sp_syr_refill.setRange(1.0, 60.0)
        self.sp_syr_refill.setSuffix(" mL/min")
        self.sp_syr_refill.setDecimals(1)
        self.sp_syr_refill.setToolTip("시약/용매를 시린지로 충전하는 속도\n• 권장: 8~15 mL/min\n• 점도가 높은 시약은 낮게 설정 (기포 방지)")

        lay_syr.addRow("Syringe Model:", self.cb_syringe)
        lay_syr.addRow("Specifications:", self.lbl_syringe_info)
        lay_syr.addRow("Refill Rate:", self.sp_syr_refill)
        lay_syr.addRow("Wash Flow Rate:", self.p_wash_speed)
        lay_syr.addRow("Wash Cycles:", self.p_wash_count)
        lay_syr.addRow("Wash Volume:", self.p_wash_volume)

        # 3. 튜빙 설정
        # @codesyncer-decision: 세척용매/시약 포트 데드볼륨 분리 입력
        # - 12-way 밸브의 각 포트 튜빙 길이가 다르므로 데드볼륨이 다름
        # - 세척용매 포트 (port 1): 용매통 ~ 시린지
        # - 시약 포트 (port 2~): 시약통 ~ 시린지 (보통 더 긴 튜빙)
        g_tube = QGroupBox("Feed Line Dead Volume")
        f_tube = QFormLayout()
        f_tube.setSpacing(T.SP_SM)
        f_tube.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f_tube.setLabelAlignment(Qt.AlignRight)

        self.p_tube_vol_solvent = QDoubleSpinBox()
        self.p_tube_vol_solvent.setRange(0, 50.0)
        self.p_tube_vol_solvent.setSuffix(" mL")
        self.p_tube_vol_solvent.setDecimals(2)
        self.p_tube_vol_solvent.setSingleStep(0.1)
        self.p_tube_vol_solvent.setToolTip(
            "세척·리필 '부피' 전용 (solvent port → syringe 경로 총합).\n"
            "※ 분취/퍼지 타이밍 보정에는 쓰이지 않음 — 타이밍용 구간별 데드볼륨은\n"
            "   대시보드 배관도 DETAIL 칩(더블클릭)에서 입력")

        self.p_tube_vol_reagent = QDoubleSpinBox()
        self.p_tube_vol_reagent.setRange(0, 50.0)
        self.p_tube_vol_reagent.setSuffix(" mL")
        self.p_tube_vol_reagent.setDecimals(2)
        self.p_tube_vol_reagent.setSingleStep(0.1)
        self.p_tube_vol_reagent.setToolTip(
            "세척·리필 '부피' 전용 (reagent port → syringe 경로 총합).\n"
            "※ 분취/퍼지 타이밍 보정에는 쓰이지 않음 — 타이밍용 구간별 데드볼륨은\n"
            "   대시보드 배관도 DETAIL 칩(더블클릭)에서 입력")

        f_tube.addRow("Solvent Line (Port 1) · 세척용:", self.p_tube_vol_solvent)
        f_tube.addRow("Reagent Line (Port 2+) · 세척용:", self.p_tube_vol_reagent)
        g_tube.setLayout(f_tube)

        layout.addWidget(g_hw)
        layout.addWidget(self.widget_syringe_group)
        layout.addWidget(g_tube)
        layout.addStretch()

        scroll.setWidget(content)

        # page_pump에 스크롤 영역 추가
        page_layout = QVBoxLayout(self.page_pump)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

        # 데이터 저장 시그널 연결
        self.pump_widgets = [self.p_name, self.p_position, self.p_motor, self.p_selector, self.p_switcher,
                            self.p_sampler,
                            self.p_tube_vol_solvent, self.p_tube_vol_reagent, self.p_wash_speed, self.p_wash_count, self.p_wash_volume]
        for w in self.pump_widgets:
            if hasattr(w, 'editingFinished'):
                w.editingFinished.connect(self.save_curr_role)
            if hasattr(w, 'currentIndexChanged'):
                w.currentIndexChanged.connect(self.save_curr_role)

    def _selected_driver_type(self, combo) -> str:
        """콤보의 currentData(장치 id) → 인벤토리 driver(한글명) → 영문 드라이버 타입.

        @codesyncer-decision: 표시 텍스트 매칭 금지 — 콤보 라벨은 '장치이름 (드라이버)'
          조합이고 장치 이름은 자유 입력이라 'NRG'/'Chemyx' 가 이름에 섞이면 오분기.
          ⚠(incompatible) 접두 라벨도 텍스트 매칭을 깨뜨림. id 기반으로만 판정한다."""
        dev_id = combo.currentData()
        if not dev_id:
            return ""
        for d in self.temp_inventory:
            if d.get('id') == dev_id:
                return HardwareFactory.get_driver_type(d.get('driver', ''))
        return ""

    def toggle_syringe_ui(self):
        """모터 드라이버에 따라 시린지 설정 표시 + 라우팅 조합 제약 강제.

        @codesyncer-decision: 조합 제약 1차 차단 (다이얼로그 계층) —
          NRG motor 선택 시 외부 selector/switcher 콤보를 비활성화하고 None 으로
          클리어(내장 밸브와 충돌), sampler 콤보만 활성.
          비-NRG motor 는 반대로 sampler 를 비활성/클리어.
          save_curr_role 의 강제 None 저장과 함께 2중 방어."""
        # UI 빌드 도중 시그널 조기 발화 대비 — 필요한 위젯이 모두 생기기 전이면 no-op
        if not hasattr(self, 'widget_syringe_group') or not hasattr(self, 'p_sampler'):
            return
        motor_type = self._selected_driver_type(self.p_motor)
        is_chemyx = (motor_type == "Chemyx")
        is_nrg = (motor_type == "NRGSyringePump")
        # 시린지 파라미터는 시린지 계열(Chemyx/NRG) 공통
        self.widget_syringe_group.setVisible(is_chemyx or is_nrg)
        self.p_selector.setEnabled(not is_nrg)
        self.p_switcher.setEnabled(not is_nrg)
        self.p_sampler.setEnabled(is_nrg)
        if is_nrg:
            self.p_selector.setToolTip("NRG 내장 밸브 사용 — 외부 12-way 조합 불가")
            self.p_switcher.setToolTip("NRG 내장 밸브 사용 — 외부 3-way 조합 불가")
            if self.p_selector.currentIndex() != 0:
                self.p_selector.setCurrentIndex(0)
            if self.p_switcher.currentIndex() != 0:
                self.p_switcher.setCurrentIndex(0)
        else:
            self.p_selector.setToolTip("")
            self.p_switcher.setToolTip("")
            if self.p_sampler.currentIndex() != 0:
                self.p_sampler.setCurrentIndex(0)

        # 라우팅 뱃지 갱신 (config.PUMP_ROUTING 과 동일 규칙으로 실시간 유도)
        if hasattr(self, 'lbl_routing'):
            if is_nrg:
                has_sampler = self.p_sampler.currentData() is not None
                txt = ("오토샘플러 — 일체형 자동 활성 (니들이 스텝별 vial 로 이동해 흡입)"
                       if has_sampler else "내장 밸브 (1펌프 = 1소스)")
                dev_id = self.p_motor.currentData()
                item = next((d for d in self.temp_inventory if d.get('id') == dev_id), {})
                if not (item.get('settings') or {}).get('main_valve_enabled', False):
                    txt += "\n⚠ main_valve_enabled=false — 시퀀스 사용 불가 (MockPump 대체됨). 장치 settings 수정 필요"
                    _P = DarkPalette if get_active_dark_mode() else LightPalette
                    self.lbl_routing.setStyleSheet(
                        f"color: {_P.STATE_FAULT}; font-size: {T.FS_XS}; "
                        f"font-weight: {T.FW_SEMI};")
                else:
                    self.lbl_routing.setStyleSheet(self._routing_style_base)
            elif motor_type:
                txt = "외부 12-way 밸브 (포트 1~12 소스 선택)"
                self.lbl_routing.setStyleSheet(self._routing_style_base)
            else:
                txt = "—"
                self.lbl_routing.setStyleSheet(self._routing_style_base)
            self.lbl_routing.setText(txt)

    def on_syringe_preset_changed(self, index):
        if index < 0 or index >= len(self.syringe_presets):
            return
        data = self.syringe_presets[index]
        self.p_dia.setValue(float(data["dia"]))
        self.p_cap.setValue(float(data["vol"]))
        max_rate = float(data["max_rate"])
        self.lbl_syringe_info.setText(
            f"ID: {data['dia']} mm / Vol: {data['vol']} mL / Max: {max_rate} mL/min")
        # 시린지 최대 유속으로 rate 스핀박스 상한 제한
        for sp in (self.sp_syr_refill, self.p_wash_speed, self.sp_pf):
            sp.setMaximum(max_rate)
            if sp.value() > max_rate:
                sp.setValue(max_rate)
        self.save_curr_role()

    def setup_heater_ui(self):
        layout = QVBoxLayout(self.page_heater)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("Reactor Temperature Control")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.h_driver = QComboBox()
        self.h_driver.currentIndexChanged.connect(self.save_heater)
        f.addRow("Heater Unit:", self.h_driver)
        g.setLayout(f)

        layout.addWidget(g)
        layout.addStretch()

    def setup_outlet_ui(self):
        layout = QVBoxLayout(self.page_outlet)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("Outlet Stream Control")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.o_driver = QComboBox()
        self.o_driver.currentIndexChanged.connect(self.save_outlet)
        f.addRow("Diverter Valve:", self.o_driver)
        g.setLayout(f)

        layout.addWidget(g)
        layout.addStretch()

    def setup_collector_ui(self):
        """분획 수집기 설정 UI"""
        layout = QVBoxLayout(self.page_collector)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("Fraction Collector")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.c_driver = QComboBox()
        self.c_driver.currentIndexChanged.connect(self.save_collector)
        f.addRow("Collector Unit:", self.c_driver)

        # 설명 라벨
        lbl_desc = QLabel("Fraction collector for automated sample collection.\n"
                         "• Colosseum — 88-tube rotary collector (RS-232, custom protocol)\n"
                         "• Plate96 — dual 96-well plate (Marlin G-code, snake order)")
        lbl_desc.setObjectName("DialogHintLabel")
        lbl_desc.setWordWrap(True)
        f.addRow(lbl_desc)

        g.setLayout(f)
        layout.addWidget(g)
        layout.addStretch()

    def setup_push_pump_ui(self):
        """Push Pump (HPLC) 설정 UI — injection 후 반응기를 통과시키는 용매 push 전용 펌프."""
        layout = QVBoxLayout(self.page_push_pump)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("Push Pump (Solvent Push)")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.pp_driver = QComboBox()
        self.pp_driver.currentIndexChanged.connect(self.save_push_pump)
        f.addRow("Push Pump:", self.pp_driver)

        lbl_desc = QLabel(
            "Injection 후 반응기를 통과시키는 용매를 공급하는 독립 펌프.\n"
            "• 선택 시: Syringe pump는 injection만 담당, 이후 push는 이 펌프가 수행\n"
            "• None: 기존 방식 (Syringe에 용매 refill 후 push)\n"
            "• Push volume = 1.1 × reactor volume (라인 세척 10% 여유 포함)"
        )
        lbl_desc.setObjectName("DialogHintLabel")
        lbl_desc.setWordWrap(True)
        f.addRow(lbl_desc)

        g.setLayout(f)
        layout.addWidget(g)
        layout.addStretch()

    def setup_gas_ui(self):
        """N2 MFC (droplet HTE 질소 스페이서) 설정 UI."""
        layout = QVBoxLayout(self.page_gas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("N2 Gas Line (MFC)")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.g_driver = QComboBox()
        self.g_driver.currentIndexChanged.connect(self.save_gas)
        f.addRow("MFC Unit:", self.g_driver)

        lbl_desc = QLabel(
            "droplet HTE 모드의 질소 스페이서 공급 (합류점 하류 티에 연결).\n"
            "• MFC KOREA MKP RS485 프로토콜 (9600/8/ODD/1, 유량=풀스케일 %)\n"
            "• 배정 시: 배관도에 N2 실린더+MFC 표시, Manual 탭에 조작 카드 노출\n"
            "• 슬레이브 주소(slave_addr)/최대유량(max_sccm)은 인벤토리 장치 settings\n"
            "• HTE 파라미터(스페이서 부피·sccm 등)는 Manual 탭 N2 MFC 카드에서 설정"
        )
        lbl_desc.setObjectName("DialogHintLabel")
        lbl_desc.setWordWrap(True)
        f.addRow(lbl_desc)

        g.setLayout(f)
        layout.addWidget(g)
        layout.addStretch()

    def setup_phase_ui(self):
        """위상센서 어레이(OCB350) 설정 UI — HTE droplet 슬러그 경계 실측."""
        layout = QVBoxLayout(self.page_phase)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        g = QGroupBox("Phase Sensor (Slug Boundary)")
        f = QFormLayout()
        f.setSpacing(T.SP_SM)
        f.setContentsMargins(T.SP_LG, 20, T.SP_LG, T.SP_MD)
        f.setLabelAlignment(Qt.AlignRight)

        self.ps_driver = QComboBox()
        self.ps_driver.currentIndexChanged.connect(self.save_phase)
        f.addRow("Sensor Array:", self.ps_driver)

        lbl_desc = QLabel(
            "아웃렛 3-way 직전 튜브의 기체/액체 경계 검출 (OCB350, UNO 1대=4센서).\n"
            "• 배정 시: 대시보드 'Slug Phase' 0/1 트랙 표시 + HTE 하이브리드 트리거 가능\n"
            "• 하이브리드 트리거는 system_params.hte_sensor_trigger 로 활성화\n"
            "• 캘리브레이션은 빈 튜브(기체만) 상태에서 — 액체가 있으면 분류가 뒤집힘\n"
            "• 센서 채널 매핑(settings.sensors)은 기본 {collect: 0} — 인벤토리 settings"
        )
        lbl_desc.setObjectName("DialogHintLabel")
        lbl_desc.setWordWrap(True)
        f.addRow(lbl_desc)

        g.setLayout(f)
        layout.addWidget(g)
        layout.addStretch()

    def _section_item(self, text):
        """모듈 리스트 섹션 헤더 — 전문 장비 스타일(대문자·자간·뮤트, 대시 없음)."""
        is_dark = getattr(self, "_is_dark_theme", get_active_dark_mode())
        p = DarkPalette if is_dark else LightPalette
        it = QListWidgetItem(text.upper())
        it.setFlags(Qt.NoItemFlags)
        f = QFont("Segoe UI")
        f.setPixelSize(10)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.3)
        it.setFont(f)
        it.setForeground(QColor(p.TEXT_TERTIARY if is_dark else p.TEXT_SECONDARY))
        it.setBackground(QColor(p.BG_TERTIARY))
        it.setSizeHint(QSize(0, 30))
        return it

    def refresh_role_list(self):
        current_row = self.role_list.currentRow() if hasattr(self, "role_list") else -1
        self.role_list.clear()

        self.role_list.addItem(self._section_item("Feed Pumps"))
        for p in self.temp_roles['pumps']:
            self.role_list.addItem(QListWidgetItem(f"  {p['name']}"))

        self.role_list.addItem(self._section_item("Auxiliary Modules"))
        self.role_list.addItem(QListWidgetItem("  Reactor Heater"))
        self.role_list.addItem(QListWidgetItem("  Outlet Valve"))
        self.role_list.addItem(QListWidgetItem("  Fraction Collector"))
        self.role_list.addItem(QListWidgetItem("  Push Pump"))
        self.role_list.addItem(QListWidgetItem("  N2 MFC (Gas)"))
        self.role_list.addItem(QListWidgetItem("  Phase Sensor"))

        self.role_list.addItem(self._section_item("System"))
        self.role_list.addItem(QListWidgetItem("  Reactor & Fluidic"))

        if self.role_list.count() > 0:
            if 0 <= current_row < self.role_list.count():
                self.role_list.setCurrentRow(current_row)
            else:
                self.role_list.setCurrentRow(1)

    def on_role_selected(self, row):
        curr_item = self.role_list.currentItem()
        if not curr_item or "━━━" in curr_item.text():
            return

        self.update_role_combos()
        pumps = self.temp_roles['pumps']
        n_pumps = len(pumps)

        if 1 <= row <= n_pumps:
            self.curr_p_idx = row - 1
            p = pumps[self.curr_p_idx]
            self._loading_role = True
            self.block_role_sigs(True)

            self.p_name.setText(p['name'])
            # 펌프 위치 로드 (기본값: inlet)
            pos = p.get('position', 'inlet')
            self.p_position.setCurrentIndex(0 if pos == 'inlet' else 1)

            self.set_combo(self.p_motor, p['drivers'].get('motor'))
            self.set_combo(self.p_selector, p['drivers'].get('selector'))
            self.set_combo(self.p_switcher, p['drivers'].get('switcher'))
            self.set_combo(self.p_sampler, p['drivers'].get('sampler'))

            # [중요] 역할 로드 후 UI 표시 여부 갱신
            self.toggle_syringe_ui()

            # @codesyncer-decision: 세척/튜빙 설정을 시린지 프리셋 콜백 전에 로드
            # - on_syringe_preset_changed → save_curr_role()가 호출되므로
            #   모든 위젯에 현재 그룹의 값이 먼저 로드되어야 데이터 오염 방지
            # - 이전 코드: 시린지 프리셋 → 튜빙 → 세척 순서로 로드
            #   → save_curr_role 시 이전 그룹의 wash/tubing 값이 현재 그룹에 덮어씌워짐
            self.p_tube_vol_solvent.setValue(float(p['settings'].get('tube_vol_solvent', 0.0)))
            self.p_tube_vol_reagent.setValue(float(p['settings'].get('tube_vol_reagent', 0.0)))
            self.p_wash_speed.setValue(float(p['settings'].get('wash_speed', 15.0)))
            self.p_wash_count.setValue(int(p['settings'].get('wash_count', 2)))
            self.p_wash_volume.setValue(float(p['settings'].get('wash_volume', 5.0)))

            # 시린지 프리셋 (이제 save_curr_role이 올바른 값을 저장함)
            dia = float(p['settings'].get('diameter', 14.5))
            matched_idx = 0
            for i, preset in enumerate(self.syringe_presets):
                if math.isclose(preset['dia'], dia, abs_tol=0.01):
                    matched_idx = i
                    break
            self.cb_syringe.setCurrentIndex(matched_idx)
            self.on_syringe_preset_changed(matched_idx)

            self._loading_role = False
            self.block_role_sigs(False)
            self.stack.setCurrentIndex(1)

        elif row == n_pumps + 2:
            self.h_driver.blockSignals(True)
            self.set_combo(self.h_driver, self.temp_roles['heater'].get('driver_id'))
            self.h_driver.blockSignals(False)
            self.stack.setCurrentIndex(2)

        elif row == n_pumps + 3:
            self.o_driver.blockSignals(True)
            self.set_combo(self.o_driver, self.temp_roles['outlet'].get('driver_id'))
            self.o_driver.blockSignals(False)
            self.stack.setCurrentIndex(3)

        elif row == n_pumps + 4:
            self.c_driver.blockSignals(True)
            self.set_combo(self.c_driver, self.temp_roles.get('collector', {}).get('driver_id'))
            self.c_driver.blockSignals(False)
            self.stack.setCurrentIndex(4)

        elif row == n_pumps + 5:
            self.pp_driver.blockSignals(True)
            self.set_combo(self.pp_driver, self.temp_roles.get('push_pump', {}).get('driver_id'))
            self.pp_driver.blockSignals(False)
            self.stack.setCurrentIndex(5)

        elif row == n_pumps + 6:
            self.g_driver.blockSignals(True)
            self.set_combo(self.g_driver, self.temp_roles.get('gas', {}).get('driver_id'))
            self.g_driver.blockSignals(False)
            self.stack.setCurrentIndex(6)

        elif row == n_pumps + 7:
            self.ps_driver.blockSignals(True)
            self.set_combo(self.ps_driver, self.temp_roles.get('phase', {}).get('driver_id'))
            self.ps_driver.blockSignals(False)
            self.stack.setCurrentIndex(7)

        elif row == n_pumps + 9:   # System ─ Reactor & Fluidic (전역)
            self.stack.setCurrentIndex(8)

    def update_role_combos(self):
        # @codesyncer-decision: itemData에 dev_id 저장 — 이름에 괄호 포함 시 파싱 실패 방지
        # @codesyncer-decision: 역할별 호환 드라이버만 표시 — 사용자 오선택 방지
        #   예: Fraction Collector 콤보에는 수집기 계열만, Outlet 콤보에는 밸브만 등
        # @codesyncer-risk: 이미 저장된 매핑이 필터에 걸리는 장치(legacy data/드라이버 타입 변경)여도
        #   해당 항목을 "⚠ 호환 안 됨" 표시와 함께 유지해야 null 덮어쓰기로 인한 데이터 손실을 방지한다.

        # 현재 각 콤보에 매핑된 driver_id를 필터 예외 처리용으로 미리 수집
        keep_map = {
            self.h_driver: self.temp_roles.get('heater', {}).get('driver_id'),
            self.o_driver: self.temp_roles.get('outlet', {}).get('driver_id'),
            self.c_driver: self.temp_roles.get('collector', {}).get('driver_id'),
            self.pp_driver: self.temp_roles.get('push_pump', {}).get('driver_id'),
            self.g_driver: self.temp_roles.get('gas', {}).get('driver_id'),
            self.ps_driver: self.temp_roles.get('phase', {}).get('driver_id'),
        }
        # 현재 선택된 펌프 그룹의 motor/selector/switcher도 보존
        curr_p_idx = getattr(self, 'curr_p_idx', -1)
        pumps = self.temp_roles.get('pumps', [])
        if 0 <= curr_p_idx < len(pumps):
            drivers = pumps[curr_p_idx].get('drivers', {})
            keep_map[self.p_motor] = drivers.get('motor')
            keep_map[self.p_selector] = drivers.get('selector')
            keep_map[self.p_switcher] = drivers.get('switcher')
            keep_map[self.p_sampler] = drivers.get('sampler')

        for cb in [self.p_motor, self.p_selector, self.p_switcher, self.p_sampler,
                   self.h_driver, self.o_driver, self.c_driver, self.pp_driver,
                   self.g_driver, self.ps_driver]:
            curr_id = cb.currentData()
            keep_id = keep_map.get(cb)
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("None", None)
            for d in self.temp_inventory:
                compatible = self._is_driver_compatible(cb, d.get('driver', ''))
                is_kept = (keep_id is not None and d['id'] == keep_id)
                if not compatible and not is_kept:
                    continue
                label = f"{d['name']} ({d['driver']})"
                if not compatible and is_kept:
                    label = "⚠ " + label + "  [incompatible]"
                cb.addItem(label, d['id'])
            # 이전 선택 복원 — keep_id 우선, 없으면 curr_id fallback
            restored = False
            target_id = keep_id or curr_id
            if target_id:
                for i in range(cb.count()):
                    if cb.itemData(i) == target_id:
                        cb.setCurrentIndex(i)
                        restored = True
                        break
            if not restored:
                cb.setCurrentIndex(0)
            cb.blockSignals(False)

    def _is_driver_compatible(self, combo, driver_name: str) -> bool:
        """역할 콤보에 표시할 드라이버 호환성 필터.

        한글 드라이버명의 키워드로 역할별 노출 장치를 제한한다:
          - Fraction Collector 콤보: "수집기" / "분취기" 포함 (Colosseum, Plate96, Mock)
          - Heater 콤보            : "히터" 포함
          - Outlet / Selector / Switcher 콤보: "밸브" 포함
          - Pump motor 콤보        : "펌프" 포함
        """
        drv = driver_name or ""
        if combo is self.c_driver:
            return ("수집기" in drv) or ("분취기" in drv)
        if combo is self.h_driver:
            return "히터" in drv
        if combo in (self.o_driver, self.p_selector, self.p_switcher):
            return "밸브" in drv
        if combo is getattr(self, 'p_sampler', None):
            return "샘플러" in drv
        if combo is self.p_motor:
            return "펌프" in drv
        if combo is self.pp_driver:
            return "펌프" in drv
        if combo is getattr(self, 'g_driver', None):
            return "MFC" in drv
        if combo is getattr(self, 'ps_driver', None):
            return "위상센서" in drv
        return True

    def set_combo(self, cb, dev_id):
        if not dev_id:
            cb.setCurrentIndex(0)
            return
        for i in range(cb.count()):
            if cb.itemData(i) == dev_id:
                cb.setCurrentIndex(i)
                return
        cb.setCurrentIndex(0)

    def get_selected_id(self, cb):
        return cb.currentData()

    def block_role_sigs(self, b):
        for w in self.pump_widgets: w.blockSignals(b)
        self.cb_syringe.blockSignals(b)

    def save_curr_role(self):
        # @codesyncer-decision: 로딩 중 save 방지 — 위젯에 아직 이전 그룹 값이 남아있을 수 있음
        if getattr(self, '_loading_role', False):
            return
        if hasattr(self, 'curr_p_idx'):
            p = self.temp_roles['pumps'][self.curr_p_idx]
            p['name'] = self.p_name.text()
            # @codesyncer-decision: 펌프 위치 저장 (inlet/outlet)
            p['position'] = 'inlet' if self.p_position.currentIndex() == 0 else 'outlet'
            p['drivers']['motor'] = self.get_selected_id(self.p_motor)
            p['drivers']['selector'] = self.get_selected_id(self.p_selector)
            p['drivers']['switcher'] = self.get_selected_id(self.p_switcher)
            p['drivers']['sampler'] = self.get_selected_id(self.p_sampler)
            # @codesyncer-decision: 저장 시점 강제 (2차 방어) — NRG motor 는 외부
            #   selector/switcher 금지, 비-NRG 는 sampler 금지. UI 비활성화를 우회하는
            #   경로(로딩 순서, 프로그램적 설정)에서도 스키마 일관성 보장.
            #   판정은 표시 텍스트가 아닌 id→드라이버 타입 (_selected_driver_type).
            if self._selected_driver_type(self.p_motor) == "NRGSyringePump":
                p['drivers']['selector'] = None
                p['drivers']['switcher'] = None
            else:
                p['drivers']['sampler'] = None
            # @codesyncer-decision: pump_id = 그룹 인덱스+1 기반 자동 할당
            # - RS-485 daisy chain에서 pump_id로 펌프 구분
            # - 기존: save 시 pump_id 미저장 → JSON 원본값 의존
            p['settings']['pump_id'] = self.curr_p_idx + 1
            p['settings']['diameter'] = self.p_dia.value()
            p['settings']['capacity'] = self.p_cap.value()
            p['settings']['tube_vol_solvent'] = self.p_tube_vol_solvent.value()
            p['settings']['tube_vol_reagent'] = self.p_tube_vol_reagent.value()
            # 세척 설정 저장
            p['settings']['wash_speed'] = self.p_wash_speed.value()
            p['settings']['wash_count'] = self.p_wash_count.value()
            p['settings']['wash_volume'] = self.p_wash_volume.value()
            self.role_list.item(self.curr_p_idx + 1).setText(f"  {p['name']}")

    def save_heater(self): self.temp_roles['heater']['driver_id'] = self.get_selected_id(self.h_driver)
    def save_outlet(self): self.temp_roles['outlet']['driver_id'] = self.get_selected_id(self.o_driver)
    def save_collector(self):
        if 'collector' not in self.temp_roles:
            self.temp_roles['collector'] = {}
        self.temp_roles['collector']['driver_id'] = self.get_selected_id(self.c_driver)

    def save_push_pump(self):
        if 'push_pump' not in self.temp_roles:
            self.temp_roles['push_pump'] = {}
        self.temp_roles['push_pump']['driver_id'] = self.get_selected_id(self.pp_driver)

    def save_gas(self):
        if 'gas' not in self.temp_roles:
            self.temp_roles['gas'] = {}
        self.temp_roles['gas']['driver_id'] = self.get_selected_id(self.g_driver)

    def save_phase(self):
        if 'phase' not in self.temp_roles:
            self.temp_roles['phase'] = {}
        self.temp_roles['phase']['driver_id'] = self.get_selected_id(self.ps_driver)

    def autofill_usb_info(self):
        """선택된 COM Port의 VID/PID/Serial을 자동으로 읽어 채운다."""
        target = self.cb_port.currentText().strip()
        if not target or target == "Mock_Port":
            QMessageBox.information(self, "USB Autofill",
                                    "실제 COM 포트를 선택한 후 다시 시도하세요.")
            return
        try:
            ports = list(serial.tools.list_ports.comports())
        except Exception as exc:
            QMessageBox.warning(self, "USB Autofill", f"포트 조회 실패: {exc}")
            return

        match = next((p for p in ports if p.device == target), None)
        if match is None:
            QMessageBox.warning(self, "USB Autofill",
                                f"'{target}' 포트를 찾을 수 없습니다. USB 연결 상태를 확인하세요.")
            return

        vid = f"{match.vid:04X}" if match.vid is not None else ""
        pid = f"{match.pid:04X}" if match.pid is not None else ""
        serial_str = match.serial_number or ""

        self.txt_vid.setText(vid)
        self.txt_pid.setText(pid)
        self.txt_serial.setText(serial_str)
        self.save_curr_inv_form()

        desc = match.description or ""
        QMessageBox.information(self, "USB Autofill",
                                f"Port: {target}\nVID: {vid}\nPID: {pid}\n"
                                f"Serial: {serial_str or '(없음)'}\n\n{desc}")

    def add_pump_role(self):
        # @codesyncer-decision: 새 그룹 추가 시 Group_X 형식 사용
        group_letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
        group_idx = len(self.temp_roles['pumps'])
        group_letter = group_letters[group_idx] if group_idx < len(group_letters) else str(group_idx + 1)
        self.temp_roles['pumps'].append({"name": f"Group_{group_letter}", "drivers": {}, "settings": {}})
        self.refresh_role_list()
    
    def del_pump_role(self):
        row = self.role_list.currentRow()
        n = len(self.temp_roles['pumps'])
        if 1 <= row <= n: del self.temp_roles['pumps'][row-1]; self.refresh_role_list(); self.stack.setCurrentIndex(0)

    def calc_r_vol(self):
        """반응기 부피 자동 계산"""
        v = math.pi * ((self.sp_r_id.value() / 20.0) ** 2) * (self.sp_r_len.value() * 100.0)
        self.lbl_calc_vol.setText(f"{v:.4f} mL")

    def calc_mixing_deadv(self):
        """
        Mixing line dead volume 계산 및 표시

        @codesyncer-decision: 합류 구간 총 체적 계산
        - 모든 합류 구간을 하나로 합산
        - 사용자 입력: 총 길이 (각 구간 합)
        """
        id_mm = self.sp_mixing_id.value()
        len_cm = self.sp_mixing_len.value()

        r_cm = (id_mm / 10.0) / 2.0
        vol_ml = math.pi * (r_cm ** 2) * len_cm

        self.lbl_mixing_deadv.setText(f"{vol_ml:.3f} mL")

    def save_and_restart(self):
        """
        설정을 저장하고 다이얼로그를 닫습니다.
        @codesyncer-decision: Hot Reload 지원 - os.execl() 대신 accept() 호출
        부모 윈도우에서 다이얼로그 결과를 확인하고 reload_hardware() 호출
        """
        self.save_curr_inv_form()

        # 반응기 설정 저장
        self.temp_sys_params["reactor_len_m"] = self.sp_r_len.value()
        self.temp_sys_params["reactor_id_mm"] = self.sp_r_id.value()
        self.temp_sys_params["post_reactor_vol_ml"] = self.sp_post_r.value()
        self.temp_sys_params["collection_line_vol_ml"] = self.sp_cl.value()
        self.temp_sys_params["priming_rate_ml_min"] = self.sp_pf.value()
        self.temp_sys_params["syringe_refill_rate"] = self.sp_syr_refill.value()
        self.temp_sys_params["mixing_line_id_mm"] = self.sp_mixing_id.value()
        self.temp_sys_params["mixing_line_len_cm"] = self.sp_mixing_len.value()
        self.temp_sys_params["wash_mode"] = self.cb_wash_mode.currentData()

        self.cfg.save_config(self.temp_inventory, self.temp_roles, self.temp_sys_params)
        QMessageBox.information(self, "적용 완료", "설정이 저장되었습니다.\n하드웨어를 재연결합니다.")
        self.accept()


# ═══════════════════════════════════════════════════════════════
# SpiralPreviewDialog — 나선형 분취 튜브 배치도
# ═══════════════════════════════════════════════════════════════

class SpiralPreviewDialog(QDialog):
    """나선형 회전판의 튜브 사용 예측을 시각화하는 다이얼로그.

    Colosseum 프로젝트 (pachterlab/colosseum) 기반 아르키메데스 나선 배치.
    각 Step별 소요 튜브를 색상으로 구분하여 표시한다.
    """

    # 나선 파라미터 (Colosseum 기본값)
    N_TUBES    = 88
    ARC        = 13.0
    SEPARATION = 17.39
    INIT_D     = 18.595
    R_BED      = 100
    R_EFF      = 90
    R_EMPTY    = 11.5
    R_TUBE     = 5.5

    # Step별 색상 (최대 10개)
    STEP_COLORS = [
        "#4a90d9", "#e8943a", "#50c878", "#e05555",
        "#9d7fd9", "#d9a03a", "#3ac4d9", "#d94a8a",
        "#7fd97f", "#d9d93a",
    ]

    def __init__(self, start_tube, step_infos, parent=None):
        """
        Args:
            start_tube: 분취 시작 튜브 번호 (1-based)
            step_infos: [{name, num_tubes, vol_per_tube, total_vol}, ...]
            parent: 부모 위젯
        """
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.start_tube = start_tube
        self.step_infos = step_infos
        self.setWindowTitle("Fluid Tracker")
        self.resize(880, 920)

        self._spiral_data = self._generate_spiral()
        self._init_ui()

    # ── 나선 좌표 생성 ────────────────────────────────────────

    @staticmethod
    def _spiral_points(init_d, arc, separation):
        """아르키메데스 나선 좌표 생성기."""
        yield (0, 0, 0)
        b = separation / (2 * math.pi)
        r = init_d
        phi = r / b
        while True:
            yield (r * math.cos(phi), r * math.sin(phi), phi)
            phi += arc / r
            r = b * phi

    def _generate_spiral(self):
        """88개 튜브 좌표 배열 생성."""
        data = []
        for idx, (x, y, p) in enumerate(
            self._spiral_points(self.INIT_D, self.ARC, self.SEPARATION), -1
        ):
            if idx == self.N_TUBES:
                break
            if idx >= 0:
                data.append((x, y))
        return data

    # ── UI 구성 ───────────────────────────────────────────────

    def _init_ui(self):
        is_dark = get_active_dark_mode()
        P = DarkPalette if is_dark else LightPalette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        layout.setSpacing(10)

        self.setStyleSheet(f"""
            QDialog {{
                background: {P.BG_PRIMARY};
                color: {P.TEXT_PRIMARY};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {P.TEXT_PRIMARY};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
        """)

        # matplotlib 캔버스
        try:
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            import numpy as np
        except ImportError:
            layout.addWidget(QLabel("matplotlib가 설치되지 않았습니다.\npip install matplotlib numpy"))
            return

        fig = Figure(figsize=(7, 7), dpi=100)
        fig.patch.set_facecolor(P.BG_PRIMARY)
        self.canvas = FigureCanvasQTAgg(fig)
        self.ax = fig.add_subplot(111)
        layout.addWidget(self.canvas, 1)

        # 범례 영역
        self.legend_layout = QVBoxLayout()
        self.legend_layout.setSpacing(2)
        self.legend_layout.setContentsMargins(8, 10, 8, 8)
        legend_frame = QFrame()
        legend_frame.setStyleSheet(f"""
            QFrame {{
                background: {P.BG_SECONDARY};
                border: 1px solid {P.BORDER_SECONDARY};
                border-radius: {T.R_MD};
            }}
        """)
        legend_frame.setLayout(self.legend_layout)
        layout.addWidget(legend_frame, 0)

        self._draw_spiral()

    # ── 나선형 그리기 ─────────────────────────────────────────

    def _draw_spiral(self):
        is_dark = get_active_dark_mode()
        P = DarkPalette if is_dark else LightPalette

        ax = self.ax
        ax.clear()
        ax.set_facecolor(P.BG_PRIMARY)
        ax.set_aspect("equal")

        text_color = P.TEXT_PRIMARY
        border_color = P.BORDER_LIGHT

        # 튜브별 색상 매핑 계산
        tube_colors = {}   # tube_index (0-based) → color hex
        tube_step = {}     # tube_index → step number
        tube_is_wash = set()  # 세척 튜브 인덱스
        current = self.start_tube - 1  # 0-based

        total_used = 0
        for si, info in enumerate(self.step_infos):
            n = info["num_tubes"]
            w = info.get("wash_tubes", 0)
            color = self.STEP_COLORS[si % len(self.STEP_COLORS)]
            for j in range(n):
                t_idx = current + j
                if 0 <= t_idx < self.N_TUBES:
                    tube_colors[t_idx] = color
                    tube_step[t_idx] = si + 1
            current += n
            # 세척 튜브
            for j in range(w):
                t_idx = current + j
                if 0 <= t_idx < self.N_TUBES:
                    tube_colors[t_idx] = color
                    tube_step[t_idx] = si + 1
                    tube_is_wash.add(t_idx)
            current += w
            total_used += n + w

        # 경계 원
        import matplotlib.patches as mpatches
        for r_val, ls in [(self.R_BED, "-"), (self.R_EFF, "--"), (self.R_EMPTY, "--")]:
            ax.add_patch(mpatches.Circle(
                (0, 0), r_val, facecolor="none",
                edgecolor=border_color, linewidth=0.8, linestyle=ls
            ))

        # 나선 경로 연결선
        xs = [p[0] for p in self._spiral_data]
        ys = [p[1] for p in self._spiral_data]
        ax.plot(xs, ys, "-", color=border_color, linewidth=0.4, alpha=0.4, zorder=1)

        # 튜브 그리기
        for i, (x, y) in enumerate(self._spiral_data):
            if i in tube_colors:
                if i in tube_is_wash:
                    # 세척 튜브: 빗금 패턴 + 연한 색
                    fc = "none"
                    ec = tube_colors[i]
                    alpha = 0.9
                    txt_color = ec
                    circle = mpatches.Circle(
                        (x, y), self.R_TUBE,
                        facecolor=fc, edgecolor=ec,
                        linewidth=1.2, alpha=alpha, zorder=2,
                        linestyle="--",
                    )
                    ax.add_patch(circle)
                    # 빗금 표현: 대각선 2개
                    r = self.R_TUBE * 0.55
                    ax.plot([x - r, x + r], [y - r, y + r], color=ec, linewidth=0.6, alpha=0.7, zorder=2.5)
                    ax.plot([x - r, x + r], [y + r, y - r], color=ec, linewidth=0.6, alpha=0.7, zorder=2.5)
                else:
                    fc = tube_colors[i]
                    ec = fc
                    alpha = 0.85
                    txt_color = "#ffffff"
                    ax.add_patch(mpatches.Circle(
                        (x, y), self.R_TUBE,
                        facecolor=fc, edgecolor=ec,
                        linewidth=0.8, alpha=alpha, zorder=2
                    ))
            else:
                fc = "none"
                ec = P.TEXT_SECONDARY if is_dark else "#999999"
                alpha = 0.3
                txt_color = ec
                ax.add_patch(mpatches.Circle(
                    (x, y), self.R_TUBE,
                    facecolor=fc, edgecolor=ec,
                    linewidth=0.8, alpha=alpha, zorder=2
                ))
            ax.text(x, y, str(i + 1), ha="center", va="center",
                    fontsize=5, color=txt_color, fontweight="bold", zorder=3,
                    alpha=1.0 if i in tube_colors else 0.4)

        # 축 설정
        margin = 15
        ax.set_xlim(-self.R_BED - margin, self.R_BED + margin)
        ax.set_ylim(-self.R_BED - margin, self.R_BED + margin)
        ax.set_xlabel("X [mm]", color=P.TEXT_SECONDARY, fontsize=9,
                      fontfamily=["Segoe UI", "sans-serif"])
        ax.set_ylabel("Y [mm]", color=P.TEXT_SECONDARY, fontsize=9,
                      fontfamily=["Segoe UI", "sans-serif"])
        ax.tick_params(colors=P.TEXT_SECONDARY, labelsize=7.5)
        for spine in ax.spines.values():
            spine.set_color(border_color)

        title = f"Tube {self.start_tube}번부터 총 {total_used}개 사용 예정 ({self.start_tube}\u2013{self.start_tube + total_used - 1}번)"
        if self.start_tube + total_used - 1 > self.N_TUBES:
            title += f"  [경고: {self.N_TUBES}개 초과!]"
        ax.set_title(title, color=text_color, fontsize=11, fontweight="600", pad=12,
                     fontfamily=["Segoe UI", "Pretendard", "Malgun Gothic", "sans-serif"])

        self.canvas.draw()

        # 범례 라벨
        # 기존 범례 라벨 제거
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        is_dark = get_active_dark_mode()
        P = DarkPalette if is_dark else LightPalette

        # HTML 테이블로 범례 생성
        hdr_c = P.TEXT_SECONDARY
        txt_c = P.TEXT_PRIMARY
        sub_c = P.TEXT_SECONDARY
        bdr_c = P.BORDER_SECONDARY
        alt_bg = P.BG_ALTERNATE

        rows_html = ""
        current = self.start_tube
        for si, info in enumerate(self.step_infos):
            n = info["num_tubes"]
            w = info.get("wash_tubes", 0)
            color = self.STEP_COLORS[si % len(self.STEP_COLORS)]
            end_tube = current + n - 1
            total_end = current + n + w - 1
            vol = f'{info["total_vol"]:.1f}' if info.get("total_vol") else "\u2014"
            vpt = f'{info["vol_per_tube"]:.1f}' if info.get("vol_per_tube") else "\u2014"
            wash_cell = f'Tube {end_tube + 1}' if w > 0 else "\u2014"
            overflow = ' <span style="color:#e05555;">&#9888;</span>' if total_end > self.N_TUBES else ""
            row_bg = f"background:{alt_bg};" if si % 2 == 1 else ""

            rc = f"padding:5px 10px; border-bottom:1px solid {bdr_c};"
            rows_html += f"""
            <tr style="{row_bg}">
                <td style="{rc} text-align:center;">
                    <span style="color:{color}; font-size:16px;">\u25cf</span>
                </td>
                <td style="{rc} font-weight:600; color:{txt_c}; white-space:nowrap;">
                    {info["name"]}
                </td>
                <td style="{rc} color:{txt_c}; text-align:center;">
                    {n}
                </td>
                <td style="{rc} color:{sub_c}; white-space:nowrap;">
                    Tube {current}\u2013{end_tube}
                </td>
                <td style="{rc} color:{sub_c}; text-align:center; white-space:nowrap;">
                    {wash_cell}
                </td>
                <td style="{rc} color:{txt_c}; text-align:right; font-weight:600; white-space:nowrap;">
                    {vol} mL{overflow}
                </td>
                <td style="{rc} color:{sub_c}; text-align:right; white-space:nowrap;">
                    {vpt} mL/tube
                </td>
            </tr>"""
            current += n + w

        remaining = self.N_TUBES - (self.start_tube - 1 + total_used)

        # 공통 셀 스타일
        hc = f"padding:6px 10px; border-bottom:1px solid {bdr_c};"  # header cell
        bc = f"padding:5px 10px; border-bottom:1px solid {bdr_c};"  # body cell
        fc = f"padding:6px 10px;"  # footer cell

        table_html = f"""
        <table cellspacing="0" cellpadding="0" style="border-collapse:collapse; width:100%;
               font-family:'Segoe UI','Pretendard','Malgun Gothic',sans-serif; font-size:12px;">
            <thead>
                <tr>
                    <th style="{hc} width:28px;"></th>
                    <th style="{hc} text-align:left; font-weight:600; color:{hdr_c}; font-size:11px;">STEP</th>
                    <th style="{hc} text-align:center; font-weight:600; color:{hdr_c}; font-size:11px;">수거</th>
                    <th style="{hc} text-align:left; font-weight:600; color:{hdr_c}; font-size:11px;">범위</th>
                    <th style="{hc} text-align:center; font-weight:600; color:{hdr_c}; font-size:11px;">세척</th>
                    <th style="{hc} text-align:right; font-weight:600; color:{hdr_c}; font-size:11px;">총량</th>
                    <th style="{hc} text-align:right; font-weight:600; color:{hdr_c}; font-size:11px;">per tube</th>
                </tr>
            </thead>
            <tbody>{rows_html}
            </tbody>
            <tfoot>
                <tr>
                    <td style="{fc}"></td>
                    <td style="{fc} font-weight:600; color:{txt_c};">합계</td>
                    <td style="{fc} text-align:center; font-weight:600; color:{txt_c};">{total_used}</td>
                    <td style="{fc} color:{sub_c};">Tube {self.start_tube}\u2013{self.start_tube + total_used - 1}</td>
                    <td style="{fc} text-align:center; color:{sub_c};">{total_used - sum(i['num_tubes'] for i in self.step_infos)}</td>
                    <td colspan="2" style="{fc} text-align:right; color:{sub_c}; font-size:11px;">
                        잔여 {max(0, remaining)}개 / {self.N_TUBES}개</td>
                </tr>
            </tfoot>
        </table>"""

        table_label = QLabel(table_html)
        table_label.setWordWrap(False)
        table_label.setStyleSheet("background:transparent; border:none;")
        self.legend_layout.addWidget(table_label)
