# -*- coding: utf-8 -*-
"""B 플레이트 180도 회전 가설 검증 — 네 코너 라벨 대조
=====================================================
가설: B 플레이트가 A와 반대 방향(180도 회전)으로 앉아 있어
      A1/H12, A12/H1 라벨이 뒤집혀 보인다.

각 코너로 니들을 보내고 "그 자리 웰의 인쇄 라벨"을 입력받아 자동 판정한다.
좌표 파일은 읽기만 한다 (수정/저장 없음).

  py -3.14 verify_plate_orientation.py --port COM11
"""
import sys
import json
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from calibrate_deck_v13 import Marlin, COORDS, find_port

# (표시명, plate, well) — A 는 대조군, B 가 검증 대상
TARGETS = [
    ("대조군 A_A1  (A 플레이트 앞-왼쪽)", "A", "A1"),
    ("대조군 A_H12 (A 플레이트 뒤-오른쪽)", "A", "H12"),
    ("B_A1  (앞-왼쪽)", "B", "A1"),
    ("B_A12 (뒤-왼쪽)", "B", "A12"),
    ("B_H1  (앞-오른쪽)", "B", "H1"),
    ("B_H12 (뒤-오른쪽)", "B", "H12"),
]


def rot180(well):
    """96-well 라벨의 180도 회전 대응 라벨. A1<->H12, A12<->H1"""
    r = ord(well[0]) - ord("A")      # 0..7
    c = int(well[1:]) - 1            # 0..11
    return f"{chr(ord('A') + (7 - r))}{(11 - c) + 1}"


def norm(s):
    return (s or "").strip().upper().replace(" ", "").replace("-", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    args = ap.parse_args()

    with open(COORDS, encoding="utf-8") as f:
        data = json.load(f)
    z = data["frame"]["z_levels"]
    idx = {(w["plate"], w["well"]): w for w in data["wells"]}

    port = args.port or find_port()
    if not port:
        print("[X] 분취기를 찾지 못했습니다. --port COM11 처럼 지정하세요.")
        sys.exit(2)

    print(f"연결 중: {port} ...")
    m = Marlin(port)
    print(f"  {m.fw}")
    print(f"  Z: 이동 {z['z_travel']} / 분주 {z['z_dispense']}")

    print("\n호밍합니다 (G28). 니들 경로에 장애물이 없는지 확인하세요.")
    input("  Enter 로 진행 (Ctrl+C 중단): ")
    m.cmd(f"G1 Z{z['z_travel']} F1200", wait=8.0)
    r = m.cmd("G28", wait=150.0)
    if "ok" not in r.lower():
        print(f"  !! G28 응답 이상: {r[:100]!r} — 중단합니다")
        return
    import msvcrt
    while msvcrt.kbhit():
        msvcrt.getch()
    print("  호밍 완료.\n")

    obs = {}
    for label, plate, well in TARGETS:
        w = idx[(plate, well)]
        print("=" * 68)
        print(f"  이동 -> {label}   X{w['machine_X']:.2f} Y{w['machine_Y']:.2f}")
        m.cmd(f"M117 {plate}{well}?", wait=0.5)
        m.cmd(f"G1 Z{z['z_travel']} F1200", wait=8.0)
        m.cmd(f"G1 X{w['machine_X']} Y{w['machine_Y']} F6000", wait=15.0)
        m.cmd(f"G1 Z{z['z_dispense']} F1200", wait=8.0)
        m.cmd("M400", wait=30.0)
        print(f"    실제 M114: {m.pos()}")
        ans = input(f"    ▶ 니들 아래 웰의 '인쇄 라벨'은? (예: A1 / H12, 웰 아니면 x): ")
        obs[(plate, well)] = norm(ans)

    # ── 판정 ──
    m.cmd(f"G1 Z{z['z_travel']} F1200", wait=8.0)
    m.cmd("M117 VERIFY DONE", wait=0.5)
    print("\n" + "=" * 68)
    print("  판정")
    print("=" * 68)

    for plate in ("A", "B"):
        rows = [(w, obs.get((plate, w), "")) for _, p, w in TARGETS if p == plate]
        if not rows:
            continue
        n_match = sum(1 for exp, got in rows if got == norm(exp))
        n_rot = sum(1 for exp, got in rows if got == norm(rot180(exp)))
        print(f"\n  [{plate} 플레이트]  ({len(rows)}개 확인)")
        for exp, got in rows:
            tag = "일치" if got == norm(exp) else \
                  ("180도 회전" if got == norm(rot180(exp)) else "불일치")
            print(f"    코드 {exp:4s} -> 실제 {got or '(무응답)':6s}   [{tag}]")
        if n_match == len(rows):
            print(f"    => 정방향. 좌표·라벨 모두 정상.")
        elif n_rot == len(rows):
            print(f"    => ★ 180도 회전 확정. 플레이트를 반대로 꽂았거나, "
                  f"코드에서 이 플레이트만 인덱싱을 뒤집어야 합니다.")
        else:
            print(f"    => 판정 불가 (일치 {n_match} / 회전 {n_rot} / 전체 {len(rows)}). "
                  f"단순 회전이 아닌 오프셋일 수 있습니다.")

    print("\n  (좌표 파일은 수정하지 않았습니다.)")


if __name__ == "__main__":
    main()
