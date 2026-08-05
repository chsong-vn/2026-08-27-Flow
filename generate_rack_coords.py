# -*- coding: utf-8 -*-
"""랙 machine 좌표 생성 — 기존 96-well 5점 티칭 보정 재사용 (재티칭 불필요).

@codesyncer-decision: 포켓(홈)이 SBS 표준 풋프린트(127.76×85.48, 사용자 실측표로
  검증)라서, 랙의 포켓 기준 좌표를 SLAS-4 96-well A1 오프셋(좌측 14.38 / 상단
  11.24 mm)으로 deck 좌표계에 앵커한 뒤, well_coordinates.json 에 저장된
  플레이트별 강체변환(fit_L/fit_R: machine = origin + R(θ)·deck)을 그대로 적용.
@codesyncer-inference: 96-well 딥웰 플레이트가 SLAS-4 웰 위치 표준을 따른다고
  가정 (SBS 규격 플레이트의 전제). 검증 방법 = 스크립트 자가검증 2중:
  ①가상 96-well A1/H12 를 같은 식으로 환산 → nominal L-A1/L-H12 와 일치(1e-6)
  ②fit 적용 결과를 기존 JSON 에 구워진 96-well machine 좌표와 대조(±0.005mm)
  + 첫 실기 사용 전 A1·E5 니들 확인 필수 (본 스크립트가 확인 좌표표 출력).

사용법:
    py -3.14 generate_rack_coords.py --rack eppendorf_5x5 \
        --z-travel <mm> --z-approach <mm> --z-dispense <mm> --max-vol <mL>
    (Z 3종 미지정 시 생성 거부 — 에펜 튜브 상단이 z_travel 보다 높으면
     이동 중 니들 충돌 사고. 반드시 실측 후 입력)

출력: hardware/collectors/data/well_coordinates_<rack>.json
      (기존 well_coordinates.json 과 동일 스키마 — Plate96Collector 가 그대로 로드)
"""
import sys
import os
import json
import math
import argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "hardware", "collectors", "data")

# SLAS-4 (ANSI/SBS) 96-well A1 중심 오프셋 — 플레이트 모서리 기준
SLAS_A1_FROM_LEFT = 14.38   # 장변(127.76) 방향
SLAS_A1_FROM_TOP = 11.24    # 단변(85.48) 방향
PITCH_96 = 9.0


def _rot(theta_deg, x, y):
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    return (c * x - s * y, s * x + c * y)


def _fit_apply(fit, deck_x, deck_y):
    """well_coordinates.json 의 강체변환: machine = origin + R(θ)·deck"""
    rx, ry = _rot(fit["rotation_deg"], deck_x, deck_y)
    return (fit["origin"][0] + rx, fit["origin"][1] + ry)


def pocket_to_deck(x_mm, y_top_mm, a1_deck):
    """포켓 좌표(좌측/상단 기준) → deck 좌표.

    96-well 매핑 관찰(기존 JSON nominal): 행 A→H = deck +X, 열 1→12 = deck +Y.
    deck_X = (상단거리 − 11.24) + A1_deck_X ;  deck_Y = (좌측거리 − 14.38) + A1_deck_Y
    """
    return (a1_deck[0] + (y_top_mm - SLAS_A1_FROM_TOP),
            a1_deck[1] + (x_mm - SLAS_A1_FROM_LEFT))


def build(rack_name, z_travel, z_approach, z_dispense, max_vol,
          base_path=None, rack_path=None, out_path=None):
    base_path = base_path or os.path.join(DATA, "well_coordinates.json")
    rack_path = rack_path or os.path.join(DATA, f"rack_{rack_name}.json")
    out_path = out_path or os.path.join(DATA, f"well_coordinates_{rack_name}.json")

    base = json.load(open(base_path, encoding="utf-8"))
    rack = json.load(open(rack_path, encoding="utf-8"))

    deck = base["frame"]["deck"]["nominal_deck_coords"]
    calib = base["frame"]["calibration"]
    fits = {"A": calib["fit_L"], "B": calib["fit_R"]}
    a1_decks = {"A": deck["L-A1"], "B": deck["R-A1"]}

    # ── 자가검증 ① — 가상 96-well A1/H12 환산이 nominal 과 일치해야 함 ──
    for side, a1_key, h12_key in (("A", "L-A1", "L-H12"), ("B", "R-A1", "R-H12")):
        a1 = pocket_to_deck(SLAS_A1_FROM_LEFT, SLAS_A1_FROM_TOP, a1_decks[side])
        h12 = pocket_to_deck(SLAS_A1_FROM_LEFT + 11 * PITCH_96,
                             SLAS_A1_FROM_TOP + 7 * PITCH_96, a1_decks[side])
        for got, key in ((a1, a1_key), (h12, h12_key)):
            want = deck[key]
            if abs(got[0] - want[0]) > 1e-6 or abs(got[1] - want[1]) > 1e-6:
                raise SystemExit(f"!!! 자가검증① 실패: {key} 환산 {got} ≠ nominal {want}")

    # ── 자가검증 ② — fit 적용 결과가 기존 96-well machine 좌표와 일치해야 함 ──
    baked = {(w["plate"], w["well"]): (w["machine_X"], w["machine_Y"])
             for w in base["wells"]}
    for plate in ("A", "B"):
        for well, dx_col, dy_row in (("A1", 0, 0), ("H12", 11, 7)):
            d = pocket_to_deck(SLAS_A1_FROM_LEFT + dx_col * PITCH_96,
                               SLAS_A1_FROM_TOP + dy_row * PITCH_96, a1_decks[plate])
            m = _fit_apply(fits[plate], *d)
            want = baked[(plate, well)]
            if abs(m[0] - want[0]) > 0.005 or abs(m[1] - want[1]) > 0.005:
                raise SystemExit(f"!!! 자가검증② 실패: {plate}-{well} "
                                 f"machine {m} ≠ 기존 {want}")
    print(">>> 자가검증 통과 — 앵커·변환식이 기존 96-well 보정을 정확히 재현")

    # ── 랙 wells 생성 (A=좌측 포켓, B=우측 포켓 — 기존 side_to_plate 동일) ──
    row_names = rack["row_names"]
    wells_out = []
    lo = base["frame"]["soft_endstop_min"]
    hi = base["frame"]["soft_endstop_max"]
    for plate in ("A", "B"):
        for w in rack["wells"]:
            d = pocket_to_deck(w["x_mm"], w["y_top_mm"], a1_decks[plate])
            mx, my = _fit_apply(fits[plate], *d)
            if not (lo[0] <= mx <= hi[0] and lo[1] <= my <= hi[1]):
                raise SystemExit(f"!!! 엔벨로프 초과: {plate}-{w['well']} "
                                 f"machine=({mx:.2f},{my:.2f})")
            row = w["well"][0]
            wells_out.append({
                "plate": plate, "well": w["well"],
                "row_idx": row_names.index(row),
                "col_idx": int(w["well"][1:]) - 1,
                "machine_X": round(mx, 3), "machine_Y": round(my, 3),
                "Z_approach": z_approach, "Z_dispense": z_dispense,
                "deck_X": round(d[0], 3), "deck_Y": round(d[1], 3),
            })

    out = {
        "frame": dict(base["frame"]),
        "wash_positions": base.get("wash_positions"),
        "rack": {
            "name": rack["name"],
            "display_name": rack.get("display_name", rack["name"]),
            "rows": rack["rows"], "cols": rack["cols"],
            "max_volume_per_well_ml": max_vol,
            "source": f"generate_rack_coords.py ← rack_{rack_name}.json "
                      f"(96-well 5점 티칭 fit 재사용, SLAS-4 앵커)",
        },
        "wells": wells_out,
    }
    out["frame"] = json.loads(json.dumps(out["frame"]))   # deep copy
    out["frame"]["z_levels"] = {
        "z_travel": z_travel, "z_approach": z_approach, "z_dispense": z_dispense,
        "z_wash_dip": base["frame"]["z_levels"].get("z_wash_dip", 10.0),
        "note": f"{rack['name']} 실측 입력값 (generate_rack_coords.py 인자)",
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f">>> 생성: {out_path} ({len(wells_out)} wells)")

    # 실기 확인용 좌표표 (첫 사용 전 니들로 A1·E5 확인)
    print("\n=== 실기 확인 좌표 (G28 후 G1 로 순회하며 니들-튜브 정렬 확인) ===")
    for plate in ("A", "B"):
        for well in ("A1", f"{row_names[-1]}{rack['cols']}"):
            m = next(w for w in wells_out if w["plate"] == plate and w["well"] == well)
            print(f"  {plate}-{well}: X{m['machine_X']} Y{m['machine_Y']} "
                  f"(Z_travel {z_travel} → Z_dispense {z_dispense})")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="랙 machine 좌표 생성 (기존 보정 재사용)")
    ap.add_argument("--rack", default="eppendorf_5x5")
    ap.add_argument("--z-travel", type=float, default=None,
                    help="이동 안전 높이(mm, G28 절대) — 튜브 상단보다 높게, 실측 필수")
    ap.add_argument("--z-approach", type=float, default=None)
    ap.add_argument("--z-dispense", type=float, default=None,
                    help="분주 높이(mm) — 튜브 림 위, 실측 필수")
    ap.add_argument("--max-vol", type=float, default=None,
                    help="튜브당 최대 분주 부피(mL, 예: 에펜 1.5)")
    args = ap.parse_args()

    missing = [n for n, v in (("--z-travel", args.z_travel),
                              ("--z-approach", args.z_approach),
                              ("--z-dispense", args.z_dispense),
                              ("--max-vol", args.max_vol)) if v is None]
    if missing:
        raise SystemExit(
            f"!!! 필수 인자 누락: {', '.join(missing)}\n"
            "    에펜도르프 튜브는 96 딥웰과 높이가 다릅니다 — 튜브 상단이 z_travel 보다\n"
            "    높으면 이동 중 니들 충돌. 랙 장착 상태에서 Z 를 실측한 뒤 입력하세요.")

    build(args.rack, args.z_travel, args.z_approach, args.z_dispense, args.max_vol)


if __name__ == "__main__":
    main()
