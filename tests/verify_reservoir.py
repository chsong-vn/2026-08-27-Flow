# -*- coding: utf-8 -*-
"""폐액 리저버 좌표 검증 — RES_CENTER / RES_LOW
==============================================
플레이트 코너와 달리 리저버는 티칭점이 RES_CENTER 하나뿐이고,
RES_LOW 는 공칭 오프셋(+42.75mm X)을 회전 적용해 '계산'한 값이다.
따라서 RES_LOW 가 실제로 리저버 안에 있는지는 검증된 적이 없다.

각 지점마다 2단계로 확인한다:
  1) 안전높이(z_travel)에서 XY 만 이동  -> 리저버 위인지 눈으로 확인
  2) 그 다음 토출높이(z_wash_dip)로 하강 -> 림 위인지 / 잠기는지 확인

좌표 파일은 읽기만 한다 (수정/저장 없음).

  py -3.14 verify_reservoir.py --port COM11
"""
import sys
import json
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(1, os.path.join(_ROOT, "tools"))

from calibrate_deck_v13 import Marlin, COORDS, find_port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    args = ap.parse_args()

    with open(COORDS, encoding="utf-8") as f:
        data = json.load(f)
    z = data["frame"]["z_levels"]
    wash = data["wash_positions"]
    wells = data["wells"]

    z_travel = z["z_travel"]
    ys = [w["machine_Y"] for w in wells]
    xs = [w["machine_X"] for w in wells]

    print("=" * 70)
    print("  리저버 좌표 (검증 대상)")
    print("=" * 70)
    for wp in wash:
        zd = wp.get("Z_dip", z_travel)
        print(f"    {wp['id']:11s} X{wp['X']:7.2f}  Y{wp['Y']:7.2f}  Z_dip {zd}")
    print(f"\n    참고: 플레이트 영역  X {min(xs):.1f}~{max(xs):.1f} / Y {min(ys):.1f}~{max(ys):.1f}")
    print(f"          리저버는 플레이트보다 Y {wash[0]['Y'] - max(ys):+.1f}mm 뒤쪽")
    print(f"          Z: 이동 {z_travel} / 웰분주 {z['z_dispense']} / 리저버토출 {z['z_wash_dip']}")
    if z["z_wash_dip"] < z["z_dispense"]:
        print(f"    !! 리저버 토출높이({z['z_wash_dip']})가 웰 분주높이({z['z_dispense']})보다 "
              f"{z['z_dispense'] - z['z_wash_dip']:.1f}mm 낮습니다.")
        print(f"       티칭 안내는 '담그지 말고 림 위'였는데 실제로 잠기는지 이번에 확인하세요.")
    if len(wash) > 1:
        print(f"    !! RES_LOW 는 티칭값이 아니라 RES_CENTER + 공칭 오프셋 "
              f"({wash[1]['X'] - wash[0]['X']:+.2f}, {wash[1]['Y'] - wash[0]['Y']:+.2f}) 계산값입니다.")

    port = args.port or find_port()
    if not port:
        print("\n[X] 분취기를 찾지 못했습니다. --port COM11 처럼 지정하세요.")
        sys.exit(2)

    print(f"\n연결 중: {port} ...")
    m = Marlin(port)
    print(f"  {m.fw}")

    print("\n호밍합니다 (G28). 니들 경로에 장애물이 없는지 확인하세요.")
    input("  Enter 로 진행 (Ctrl+C 중단): ")
    m.cmd(f"G1 Z{z_travel} F1200", wait=8.0)
    r = m.cmd("G28", wait=150.0)
    if "ok" not in r.lower():
        print(f"  !! G28 응답 이상: {r[:100]!r} — 중단합니다")
        return
    import msvcrt
    while msvcrt.kbhit():
        msvcrt.getch()
    print("  호밍 완료.\n")

    result = {}
    for wp in wash:
        zd = wp.get("Z_dip", z_travel)
        print("=" * 70)
        print(f"  [{wp['id']}]  X{wp['X']:.2f} Y{wp['Y']:.2f}")
        note = wp.get("note")
        if note:
            print(f"  {note}")
        print("=" * 70)

        # 1단계: 안전높이에서 XY 만
        m.cmd(f"M117 {wp['id']} XY", wait=0.5)
        m.cmd(f"G1 Z{z_travel} F1200", wait=8.0)
        m.cmd(f"G1 X{wp['X']} Y{wp['Y']} F6000", wait=15.0)
        m.cmd("M400", wait=30.0)
        print(f"    [1단계] 안전높이 Z{z_travel} 에서 XY 이동 완료 — M114: {m.pos()}")
        a1 = input("    ▶ 니들이 리저버 '입구 안쪽'에 있습니까? (y / n / 설명): ").strip()
        if a1.lower().startswith("n"):
            result[wp["id"]] = ("XY 벗어남", a1)
            print("    -> 하강을 건너뜁니다 (충돌 방지).")
            continue

        # 2단계: 토출높이로 하강
        input(f"    ▶ Z{zd} 로 하강합니다. Enter (중단하려면 Ctrl+C): ")
        m.cmd(f"M117 {wp['id']} Z{zd}", wait=0.5)
        m.cmd(f"G1 Z{zd} F600", wait=15.0)
        m.cmd("M400", wait=30.0)
        print(f"    [2단계] 하강 완료 — M114: {m.pos()}")
        a2 = input("    ▶ 니들 끝이 림 '위'입니까, 액/바닥에 '잠김'입니까? (위 / 잠김 / 설명): ").strip()
        result[wp["id"]] = ("OK", a2)
        m.cmd(f"G1 Z{z_travel} F1200", wait=8.0)
        m.cmd("M400", wait=30.0)

    m.cmd(f"G1 Z{z_travel} F1200", wait=8.0)
    m.cmd("M117 RES VERIFY DONE", wait=0.5)

    print("\n" + "=" * 70)
    print("  결과 요약")
    print("=" * 70)
    for k, (status, memo) in result.items():
        print(f"    {k:11s} [{status}]  {memo}")
    print("\n  (좌표 파일은 수정하지 않았습니다.)")


if __name__ == "__main__":
    main()
