# -*- coding: utf-8 -*-
"""에펜도르프 5×5 랙 좌표 파이프라인 검증 (오프라인, 실기 불필요).

 1. 생성 자가검증 — 앵커·강체변환이 기존 96-well 보정(구운 machine 좌표)을 재현
 2. 기하 교차검증 — 랙 A1 deck 좌표 = 96 A1 + (포켓 오프셋 차) 독립 계산과 일치
 3. 드라이버 로드 — 50튜브(25×2), 5×5 서펜타인 순서, 랙 메타(라벨/최대부피)
 4. 96-well 하위호환 — 기본 생성 시 192웰/기존 라벨 유지
 5. Z 미입력 시 생성 거부 (니들 충돌 방지 가드)
"""
import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
sys.path.insert(0, _ROOT)
sys.path.insert(1, _TOOLS)

from generate_rack_coords import build, pocket_to_deck, SLAS_A1_FROM_LEFT, SLAS_A1_FROM_TOP
from hardware.collectors.collector_plate96 import Plate96Collector

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


DATA = os.path.join(_ROOT, "hardware", "collectors", "data")
OUT = os.path.join(DATA, "well_coordinates_eppendorf_5x5_TEST.json")

print("== 1. 생성 + 자가검증 (임시 Z 값 — 실기 생성 시엔 실측 필수) ==")
try:
    build("eppendorf_5x5", z_travel=33.0, z_approach=28.5, z_dispense=26.5,
          max_vol=1.5, out_path=OUT)
    check("build 자가검증 2종 통과", True)
except SystemExit as e:
    check("build 자가검증 2종 통과", False, str(e))
    print("RESULT: FAIL")
    sys.exit(1)

d = json.load(open(OUT, encoding="utf-8"))
check("50 wells (25×2)", len(d["wells"]) == 50)
check("plate A/B 각 25", sum(1 for w in d["wells"] if w["plate"] == "A") == 25
      and sum(1 for w in d["wells"] if w["plate"] == "B") == 25)

print("== 2. 기하 교차검증 ==")
base = json.load(open(os.path.join(DATA, "well_coordinates.json"), encoding="utf-8"))
L_A1 = base["frame"]["deck"]["nominal_deck_coords"]["L-A1"]
# 랙 A1 포켓좌표 (8.88, 상단 11.54) — 96 A1 (14.38, 11.24) 과의 차 = deck 오프셋
exp_deck = (L_A1[0] + (11.54 - SLAS_A1_FROM_TOP), L_A1[1] + (8.88 - SLAS_A1_FROM_LEFT))
got = next(w for w in d["wells"] if w["plate"] == "A" and w["well"] == "A1")
check("랙 A1 deck = 96 A1 + 오프셋 (독립 계산 일치)",
      abs(got["deck_X"] - exp_deck[0]) < 1e-6 and abs(got["deck_Y"] - exp_deck[1]) < 1e-6,
      f"got=({got['deck_X']},{got['deck_Y']}) exp={exp_deck}")
lo, hi = base["frame"]["soft_endstop_min"], base["frame"]["soft_endstop_max"]
check("전 튜브 machine 엔벨로프 내",
      all(lo[0] <= w["machine_X"] <= hi[0] and lo[1] <= w["machine_Y"] <= hi[1]
          for w in d["wells"]))
# 좌/우 포켓 X 간격이 96-well 과 동일한 수준인지 (좌우 랙 독립 fit 적용 확인)
gA = next(w for w in d["wells"] if w["plate"] == "A" and w["well"] == "C3")
gB = next(w for w in d["wells"] if w["plate"] == "B" and w["well"] == "C3")
check("좌/우 랙 분리 배치 (B가 +X 쪽)", gB["machine_X"] - gA["machine_X"] > 60,
      f"Δ={gB['machine_X'] - gA['machine_X']:.1f}")

print("== 3. 드라이버 로드 (5×5 서펜타인) ==")
col = Plate96Collector()
col.coords_path = OUT
col.reload_data()
check("total_tubes = 50", col.total_tubes == 50, str(col.total_tubes))
check("랙 라벨", col.rack_label == "Eppendorf 5x5", col.rack_label)
check("최대부피 1.5", col.max_volume_per_well_ml == 1.5)
seq = [col.get_well_id(i) for i in (1, 5, 6, 10, 21, 25, 26, 50)]
check("서펜타인 순서 (A1→A5, B5→B1, …, E1→E5, 그 후 Plate B)",
      seq == ["A_A1", "A_A5", "A_B5", "A_B1", "A_E1", "A_E5", "B_A1", "B_E5"],
      str(seq))

print("== 4. 96-well 하위호환 ==")
col96 = Plate96Collector()
check("기본 192웰 유지", col96.total_tubes == 192, str(col96.total_tubes))
check("기본 라벨 96well", col96.rack_label == "96well", col96.rack_label)
check("기본 최대부피 1.5 유지", col96.max_volume_per_well_ml == 1.5)

print("== 5. Z 미입력 생성 거부 ==")
r = subprocess.run([sys.executable, os.path.join(_TOOLS, "generate_rack_coords.py"), "--rack", "eppendorf_5x5"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check("인자 누락 → 종료코드 ≠0 + 안내", r.returncode != 0 and "실측" in (r.stderr + r.stdout),
      f"rc={r.returncode}")

os.remove(OUT)   # 테스트 산출물 정리 (실 파일은 실측 Z 로 별도 생성)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
