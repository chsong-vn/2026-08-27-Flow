# -*- coding: utf-8 -*-
"""MFC 통합 검증 — 장비 규격 입력(다이얼로그) → config → 드라이버 → Manual 전파.

에러 검증 + Manual 연동 + 장비 규격 정합을 실제 위젯/드라이버로 왕복 확인.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


# ══ ① 다이얼로그 장비 규격 입력 왕복 (add → 규격 입력 → save → reload) ══
from engine.config import SystemConfig
from ui.dialogs import HardwareConfigDialog

cfg = SystemConfig()
dlg = HardwareConfigDialog(cfg)


def select_inv(inv_idx):
    """인벤토리 '배열 인덱스'로 선택 — 리스트는 종류→이름 정렬 표시라
    표시행 ≠ 배열 인덱스 (@codesyncer refresh_inv_list). _inv_row_to_idx 경유.
    (2026-08-05 수리: 정렬 도입 후 배열 인덱스를 행 번호로 넘기던 검증기 결함)"""
    dlg.on_inv_selected(dlg._inv_row_to_idx.index(inv_idx))


# MFC 장비 + 스위치용 더미 장비 추가
dlg.add_device()
idxA = len(dlg.temp_inventory) - 1       # MFC 장비
dlg.add_device()
idxB = len(dlg.temp_inventory) - 1       # 스위치용 더미

# MFC 장비 선택 + 규격 입력 (on_inv_selected 경유해 curr_inv_idx 정합)
select_inv(idxA)
dlg.cb_driver.setCurrentText("질소 MFC (MKP RS485)")
dlg.on_driver_changed(dlg.cb_driver.currentIndex())
check("MFC 선택 시 slave_addr 필드 노출", dlg.sp_addr.isVisibleTo(dlg))
check("MFC 선택 시 Full Scale 필드 노출",
      dlg.sp_maxsccm.isVisibleTo(dlg) and dlg.lbl_maxsccm.isVisibleTo(dlg))
check("slave_addr 범위 0~255", dlg.sp_addr.minimum() == 0 and dlg.sp_addr.maximum() == 255)

# 규격 입력: 주소 7, Full Scale 200 sccm (valueChanged → autosave)
dlg.txt_dev_name.setText("N2-MFC-A")
dlg.sp_addr.setValue(7)
dlg.sp_maxsccm.setValue(200.0)
dlg.save_curr_inv_form()
st = dlg.temp_inventory[idxA].get("settings") or {}
check("규격 저장 slave_addr=7", st.get("slave_addr") == 7, str(st))
check("규격 저장 max_sccm=200", abs(float(st.get("max_sccm", 0)) - 200.0) < 1e-6)
check("규격 저장 baudrate=9600(기본)", st.get("baudrate") == 9600)

# 다른 장치로 전환(autosave) 후 복귀 → 진짜 재로드 검증
select_inv(idxB)
select_inv(idxA)
check("재로드 slave_addr=7", dlg.sp_addr.value() == 7, str(dlg.sp_addr.value()))
check("재로드 Full Scale=200", abs(dlg.sp_maxsccm.value() - 200.0) < 1e-6)

# 규격 스냅샷 (이후 위젯 변경에 오염되지 않도록 복사)
import copy
saved = copy.deepcopy(dlg.temp_inventory[idxA])

# 밸브로 전환 시 Full Scale 숨김 (오노출 회귀) — 더미 장치에서
select_inv(idxB)
dlg.cb_driver.setCurrentText("12방향 밸브 (Runze)")
dlg.on_driver_changed(dlg.cb_driver.currentIndex())
check("밸브 전환 시 Full Scale 숨김", not dlg.sp_maxsccm.isVisibleTo(dlg))


# ══ ② config → 드라이버 전파 (hw_manager 생성 로직과 동일) ══
from hardware.gas.mfc_korea_mkp import MFCKoreaMKP

def build_mfc_from_device(g_info):
    """hw_manager 5-4 블록과 동일한 생성 로직 (규격 전파 검증)."""
    g_set = g_info.get("settings", {}) or {}
    # 기본 주소 0 — 벤더 실기 드라이버(MFC_Driver.zip, device_id=0x00) 정합
    _addr = g_set.get("slave_addr", g_set.get("modbus_addr", 0))
    m = MFCKoreaMKP(
        g_info.get("port"),
        slave_addr=int(_addr or 0),
        baudrate=int(g_set.get("baudrate", 9600) or 9600),
        max_sccm=float(g_set.get("max_sccm", 100.0) or 100.0),
        name=g_info.get("name", "MFC"))
    m.connect()
    return m

# 방금 다이얼로그가 만든 규격 그대로
mfc = build_mfc_from_device(saved)
check("드라이버 addr = 입력 7", mfc.addr == 7, str(mfc.addr))
check("드라이버 max_sccm = 입력 200", abs(mfc.max_sccm - 200.0) < 1e-6)
check("드라이버 baud 9600", mfc.baud == 9600)

# settings=None (기존 config 실제 상태) → 기본값 폴백, 무크래시
mfc_none = build_mfc_from_device(
    {"name": "펌프_17", "port": "Mock_Port", "settings": None})
check("settings=None → addr 기본 0(벤더 정합)", mfc_none.addr == 0)
check("settings=None → max 기본 100", abs(mfc_none.max_sccm - 100.0) < 1e-6)

# 구 modbus_addr 키 back-compat
mfc_old = build_mfc_from_device(
    {"name": "old", "port": "Mock_Port", "settings": {"modbus_addr": 4, "max_sccm": 50}})
check("구 modbus_addr back-compat → addr 4", mfc_old.addr == 4)


# ══ ③ Manual 탭 연동 (규격이 스핀박스 상한/조작에 반영) ══
# @codesyncer-decision(2026-08-05): render_manual_tab.py 삭제(대청소)로 FakeApp
#   출처를 verify_manual_grouping.py 헤더로 교체 — 동일 목적의 오프스크린 목.
exec(open("verify_manual_grouping.py", encoding="utf-8").read()
     .split('app = QApplication')[0])
from ui.tab_manual import ManualTab

# 실제 드라이버(Mock, max=200)를 app.mfc 로 — Manual 이 규격 반영하는지
fa = FakeApp()
fa.mfc = build_mfc_from_device(saved)     # max_sccm=200
tab = ManualTab(fa)
check("Manual MFC 블록 표시", tab.mfc_group.isVisibleTo(tab))
check("Manual sp_mfc 상한 = 규격 200", abs(tab.sp_mfc.maximum() - 200.0) < 1e-6,
      str(tab.sp_mfc.maximum()))

# SET → 실제 드라이버 set_flow (%-환산 경유) → _sp 반영
tab.sp_mfc.setValue(50.0)
tab._mfc_set()
time.sleep(0.3)
check("Manual SET 50 → 드라이버 _sp=50", abs(fa.mfc._sp - 50.0) < 1e-6, str(fa.mfc._sp))
# 50 sccm / 200 FS = 25% (드라이버 내부 환산 정합; Mock 은 _sp 만 미러)
tab._mfc_off()
time.sleep(0.3)
check("Manual OFF → _sp 0", abs(fa.mfc._sp) < 1e-9)

# HTE 파라미터 저장 경로
tab.chk_hte.setChecked(True)
tab._hte_params["hte_gas_sccm"].setValue(30.0)
tab._save_hte_params()
spp = fa.cfg.config_data.get("system_params", {})
check("HTE 저장 gas_sccm=30", abs(float(spp.get("hte_gas_sccm", 0)) - 30.0) < 1e-6)
check("HTE 저장 hte_mode=True", spp.get("hte_mode") is True)


# ══ ④ 실제 hardware_config.json 무크래시 로드 (roles.gas 실제) ══
import json
real = json.load(open("hardware_config.json", encoding="utf-8"))
gas_id = (real.get("roles", {}).get("gas") or {}).get("driver_id")
dev = next((d for d in real.get("inventory", []) if d.get("id") == gas_id), None)
check("실 config roles.gas 장비 존재", dev is not None, str(gas_id))
if dev:
    mreal = build_mfc_from_device(dev)   # settings None 이어도 폴백
    check("실 config MFC 생성 무크래시", mreal.is_connected)


print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
