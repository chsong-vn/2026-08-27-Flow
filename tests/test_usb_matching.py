# -*- coding: utf-8 -*-
"""USB 자동매칭 프로브 폭풍 수정 검증 (2026-08-12).

배경(사용자 부팅 로그): Chemyx 4대·Runze 4대가 같은 CH340 VID/PID(1A86:7523,
시리얼 없음)를 공유. get_device_info 가 장치마다 find_port_by_usb_info 를 호출 →
CH340 포트를 8회 반복해서 열고 프로브 → 상호 오염으로 '살아있는' Runze(COM9)
조차 'Auto match failed' → static 폴백. Chemyx 는 static COM8 이 낡아 연결 실패.

수정: ①config 시그니처 캐시(같은 vid/pid/serial/probe = 1회 프로브, 결과 공유)
     ②포트 분류 캐시(이미 분류된 포트 재프로브 금지 — 이종 바이트 재주입 차단)
     ③프로브 2회 재시도(직전 프로브 잔류/CH340 재open 흡수)

검증(하드웨어 없이 — comports/probe 몽키패치로 프로브 호출 횟수 계수):
  A. 시그니처 캐시: 동일 시그니처 4장치 → 프로브 1회
  B. 분류 캐시: Chemyx 가 Runze 포트를 짓밟지 않음(정답 포트 반환)
  C. Runze 도 정상 자동매칭(살아있는 포트 회복)
  D. 미present 장치는 폴백 유지(오검출 금지)
  E. 유일 매칭은 프로브 없이 통과
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import serial.tools.list_ports as _lp
import core.utils as U

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


class FakePort:
    def __init__(self, device, vid, pid, serial=None):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.serial_number = serial


# 가짜 시스템 포트 — CH340(1A86:7523) 2개: COM9=Runze 버스, COM14=Chemyx 버스
# (+ 무관 FTDI 1개). 실제 장치 종류는 probe 몽키패치가 결정한다.
CH340 = (0x1A86, 0x7523)
LIVE = [
    FakePort("COM9", *CH340),
    FakePort("COM14", *CH340),
    FakePort("COM11", 0x0403, 0x6001, "A106ZZPGA"),
]
_ROLE = {"COM9": "runze", "COM14": "chemyx", "COM11": "ftdi"}

probe_calls = []   # (probe_name, port) 순서 기록


def _install(present=("COM9", "COM14", "COM11"), roles=None):
    probe_calls.clear()
    role = dict(_ROLE)
    if roles:
        role.update(roles)     # 시나리오별 포트 실체 재정의 (예: COM9=chemyx 재넘버링)
    _lp.comports = lambda: [p for p in LIVE if p.device in present]

    def mk(pname):
        def _fn(com, *a, **k):
            probe_calls.append((pname, com))
            return role.get(com) == pname      # 그 포트의 실제 종류일 때만 True
        return _fn
    U._PROBE_REGISTRY = {"chemyx": mk("chemyx"), "runze": mk("runze"),
                         "reaxus": mk("reaxus")}


# ── A/B/C: config.get_device_info 통합 — 시그니처+분류 캐시 동시 ──
print("[A~C] 부팅 스캔 시뮬 (Chemyx 4 + Runze 4, 공유 CH340)")
_install()
from engine.config import SystemConfig
cfg = SystemConfig()
cfg.cached_inventory = {}
# Chemyx 4대 + Runze 4대 = 동일 vid/pid, 시리얼 없음, probe 만 다름
for i in range(1, 5):
    cfg.cached_inventory[f"chemyx{i}"] = {
        "name": f"시린지펌프{i}", "port": "COM8",
        "vid": "1A86", "pid": "7523", "serial": None, "probe": "chemyx"}
    cfg.cached_inventory[f"runze{i}"] = {
        "name": f"12-way {i}", "port": "COM9",
        "vid": "1A86", "pid": "7523", "serial": None, "probe": "runze"}

# init_hw 순서 모사: 그룹마다 motor(chemyx) → selector(runze)
resolved = {}
for i in range(1, 5):
    resolved[f"chemyx{i}"] = cfg.get_device_info(f"chemyx{i}")["port"]
    resolved[f"runze{i}"] = cfg.get_device_info(f"runze{i}")["port"]

chemyx_probes = [c for c in probe_calls if c[0] == "chemyx"]
runze_probes = [c for c in probe_calls if c[0] == "runze"]
check("A1 Chemyx 프로브 1회로 축소 (8장치→시그니처 1)", len(chemyx_probes) <= 2,
      f"chemyx probe {len(chemyx_probes)}회: {chemyx_probes}")
check("A2 Runze 프로브도 소수 (재프로브 폭풍 없음)", len(runze_probes) <= 2,
      f"runze probe {len(runze_probes)}회")
check("B1 Chemyx 4대 → COM14 정확 매칭", all(resolved[f"chemyx{i}"] == "COM14"
                                        for i in range(1, 5)),
      str({k: v for k, v in resolved.items() if "chemyx" in k}))
check("C1 Runze 4대 → COM9 정확 매칭 (살아있는 포트 회복)",
      all(resolved[f"runze{i}"] == "COM9" for i in range(1, 5)),
      str({k: v for k, v in resolved.items() if "runze" in k}))
# 분류 캐시: 한 번 runze 로 확정된 COM9 를 chemyx 가 다시 열지 않는다
com9_chemyx = [c for c in probe_calls if c == ("chemyx", "COM9")]
check("B2 COM9(Runze)에 Chemyx 프로브 ≤1회 (상호오염 최소화)",
      len(com9_chemyx) <= 1, f"{len(com9_chemyx)}회")

# ── D: Chemyx 버스 미연결(COM14 없음) → 폴백 유지, 오검출 금지 ──
print("[D] Chemyx 버스 미present (COM14 제거)")
_install(present=("COM9", "COM11"))
cfg2 = SystemConfig()
cfg2.cached_inventory = {
    "chemyx1": {"name": "시린지펌프1", "port": "COM8", "vid": "1A86",
                "pid": "7523", "serial": None, "probe": "chemyx"},
    "runze1": {"name": "12-way 1", "port": "COM9", "vid": "1A86",
               "pid": "7523", "serial": None, "probe": "runze"}}
rc = cfg2.get_device_info("chemyx1")
rr = cfg2.get_device_info("runze1")
check("D1 Chemyx 미present → static 폴백 COM8 유지", rc["port"] == "COM8"
      and rc.get("_auto_matched") is False, f"{rc['port']}")
check("D2 Runze 는 여전히 COM9 자동매칭", rr["port"] == "COM9"
      and rr.get("_auto_matched") is True, f"{rr['port']}")

# ── E: 유일 CH340 여도 probe 로 종류 확인 (D1 오매칭 방지의 정면) ──
print("[E] 유일 CH340 1개 — probe 로 종류 확인")
_install(present=("COM14", "COM11"))   # CH340 하나(=chemyx)만
cfg3 = SystemConfig()
cfg3.cached_inventory = {
    "chemyx1": {"name": "시린지펌프1", "port": "COM8", "vid": "1A86",
                "pid": "7523", "serial": None, "probe": "chemyx"}}
re_ = cfg3.get_device_info("chemyx1")
check("E1 유일 CH340(=chemyx) → COM14, probe 로 확인", re_["port"] == "COM14"
      and re_.get("_auto_matched") is True
      and any(c == ("chemyx", "COM14") for c in probe_calls),
      f"port={re_['port']} probes={probe_calls}")

# 유일 CH340 이 '다른 타입'(Runze)일 때 chemyx 는 잡지 않고 폴백 (D1 축약형)
print("[E2] 유일 CH340 이 Runze 뿐 — chemyx 는 오매칭 금지")
_install(present=("COM9", "COM11"))    # CH340 하나(=runze)만
cfg4 = SystemConfig()
cfg4.cached_inventory = {
    "chemyx1": {"name": "시린지펌프1", "port": "COM8", "vid": "1A86",
                "pid": "7523", "serial": None, "probe": "chemyx"}}
re4 = cfg4.get_device_info("chemyx1")
check("E2 유일 CH340=Runze → chemyx 폴백 COM8 (오매칭 방지)",
      re4["port"] == "COM8" and re4.get("_auto_matched") is False, f"{re4['port']}")

# 히터(probe 없음, 고유 VID/PID)는 유일 매칭 단축이 그대로 동작해야 함
print("[E3] probe 없는 고유 VID/PID(히터) — 유일 매칭 단축 유지")
_install(present=("COM11",))           # FTDI 하나
cfg5 = SystemConfig()
cfg5.cached_inventory = {
    "heater": {"name": "히터", "port": "COM5", "vid": "0403",
               "pid": "6001", "serial": None, "probe": None}}
re5 = cfg5.get_device_info("heater")
check("E3 probe 없으면 유일 VID/PID 매칭 그대로 (COM11)",
      re5["port"] == "COM11" and re5.get("_auto_matched") is True, f"{re5['port']}")

# ── F: 포트 도용 방지 — Chemyx 가 probe 로 COM9 점유 후 Runze static 폴백 거부 ──
#    실기 재현: COM 재넘버링으로 Chemyx 버스가 COM9(옛 Runze 번호)로 이동.
#    Chemyx 는 probe 로 COM9 정확 매칭, Runze 는 낡은 static COM9 로 폴백 시도 →
#    도용 금지(Mock). (hw_manager 는 motor=chemyx 를 selector=runze 보다 먼저 해석)
print("[F] 포트 도용 방지 (Chemyx COM9 점유 → Runze static COM9 거부)")
# COM9 가 재넘버링으로 실제 Chemyx 버스가 된 상황 (옛 Runze 번호를 물려받음)
_install(present=("COM9", "COM11"), roles={"COM9": "chemyx"})
cfgF = SystemConfig()
cfgF.cached_inventory = {
    "chemyx1": {"name": "시린지펌프1", "port": "COM8", "vid": "1A86",
                "pid": "7523", "serial": None, "probe": "chemyx"},
    # Runze static 포트가 낡아 COM9 (지금은 Chemyx 것)
    "runze1": {"name": "12-way 1", "port": "COM9", "vid": "1A86",
               "pid": "7523", "serial": None, "probe": "runze"}}
# 해석 순서: motor(chemyx) → selector(runze)
rF_c = cfgF.get_device_info("chemyx1")
rF_r = cfgF.get_device_info("runze1")
check("F1 Chemyx probe 로 COM9 점유", rF_c["port"] == "COM9"
      and rF_c.get("_auto_matched") is True, f"{rF_c['port']}")
check("F2 Runze static COM9 도용 거부 → Mock", rF_r["port"] == "COM_Mock"
      and rF_r.get("_port_conflict") is True, f"{rF_r['port']}")

# F3: 도용 대상이 아닌 static 폴백은 그대로 (정상 장치 오작동 금지)
print("[F3] 무관 static 폴백은 보존")
_install(present=("COM9", "COM11"), roles={"COM9": "chemyx"})
cfgG = SystemConfig()
cfgG.cached_inventory = {
    "chemyx1": {"name": "시린지펌프1", "port": "COM8", "vid": "1A86",
                "pid": "7523", "serial": None, "probe": "chemyx"},
    # 히터: 고유 VID/PID, present 아님 → static COM5 폴백 (COM9 아님 = 도용 아님)
    "heater": {"name": "히터", "port": "COM5", "vid": "067B",
               "pid": "23A3", "serial": "AWATB147612", "probe": None}}
cfgG.get_device_info("chemyx1")            # COM9 claim
rH = cfgG.get_device_info("heater")
check("F3 히터 static COM5 유지 (도용 아님)", rH["port"] == "COM5"
      and not rH.get("_port_conflict"), f"{rH['port']}")

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
