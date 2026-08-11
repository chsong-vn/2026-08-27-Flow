# -*- coding: utf-8 -*-
"""96well 분취기 좌표 캘리브레이션 — 대화형 조그 티칭 (Marlin).

리뉴얼로 플레이트 위치/간격이 바뀌었을 때 코너를 다시 티칭해 192웰을 재생성한다.

측정 점 (플레이트당 3점 = 위치 + 두 축 피치 + 스큐 검출):
    Plate A: A1 → A12 → H12       Plate B: A1 → A12 → H12
    WASH 위치, 그리고 Z(분주 높이)는 Plate A의 A1 에서 함께 잡는다.

기하 모델 (기존 well_coordinates.json 과 동일):
    행 A→H = +X,  열 1→12 = +Y
    Y_pitch = (A12.Y − A1.Y) / 11      X_pitch = (H12.X − A12.X) / 7

사용법:
    py -3.14 calibrate_plate96.py              # 자동 탐지 후 티칭
    py -3.14 calibrate_plate96.py --port COM11
    py -3.14 calibrate_plate96.py --verify     # 티칭 없이 현재 좌표로 검증 이동만

@codesyncer-decision: 캡처값은 소프트웨어가 보낸 목표가 아니라 M114 로 읽은 **실제 위치**를
  쓴다. 조그 중 소프트리밋에 걸려 덜 간 경우 목표를 믿으면 좌표가 조용히 어긋난다.
"""
import sys, os, json, time, shutil, argparse

sys.stdout.reconfigure(encoding="utf-8")
import serial
import serial.tools.list_ports as lp

try:
    import msvcrt
except ImportError:
    print("이 도구는 Windows 콘솔 전용입니다 (msvcrt 필요).")
    sys.exit(1)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS = os.path.join(HERE, "hardware", "collectors", "data", "well_coordinates.json")
BAUD = 250000
ROWS = "ABCDEFGH"
STEPS = [0.1, 0.5, 1.0, 5.0]

# 기존 파일의 Z 상대 오프셋 (dispense 기준) — 티칭한 분주높이에서 파생
DZ_APPROACH = +2.0
DZ_TRAVEL = +6.0
DZ_WASH_DIP = -3.0


# ── Marlin 통신 ──────────────────────────────────────────────
class Marlin:
    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=3.0)
        self.ser.dtr = False
        time.sleep(0.2)
        self.ser.dtr = True
        time.sleep(4.0)
        self.ser.reset_input_buffer()
        resp = self.cmd("M115", wait=3.0)
        if "FIRMWARE_NAME" not in resp:
            raise RuntimeError(f"Marlin 응답 없음: {resp[:80]!r}")
        self.fw = resp.split("\n")[0][:90]
        self.cmd("G90", wait=1.0)          # 절대좌표

    def cmd(self, c, wait=2.0):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\n").encode())
        t0 = time.time()
        buf = ""
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n).decode("ascii", "replace")
                if buf.strip().endswith("ok") or "FIRMWARE_NAME" in buf:
                    break
            time.sleep(0.05)
        return buf

    def pos(self):
        """M114 로 실제 좌표 읽기 → (x, y, z) 또는 None"""
        r = self.cmd("M114", wait=3.0)
        try:
            seg = r.split("Count")[0]
            out = {}
            for ax in "XYZ":
                i = seg.index(ax + ":")
                out[ax] = float(seg[i + 2:].split()[0])
            return out["X"], out["Y"], out["Z"]
        except Exception:
            return None

    def jog(self, dx=0.0, dy=0.0, dz=0.0, f=3000):
        self.cmd("G91", wait=1.0)
        self.cmd(f"G1 X{dx} Y{dy} Z{dz} F{f}", wait=2.0)
        self.cmd("M400", wait=15.0)
        self.cmd("G90", wait=1.0)

    def goto(self, x=None, y=None, z=None, f=3000):
        parts = []
        if z is not None:
            parts.append(f"Z{z}")
        g = " ".join(([f"X{x}"] if x is not None else []) + ([f"Y{y}"] if y is not None else []))
        if z is not None:
            self.cmd(f"G1 Z{z} F1200", wait=4.0)
        if g:
            self.cmd(f"G1 {g} F{f}", wait=8.0)
        self.cmd("M400", wait=30.0)

    def close(self):
        try:
            self.cmd("M84", wait=1.0)
            self.ser.close()
        except Exception:
            pass


def find_port():
    for p in lp.comports():
        if p.device == "COM3":       # 레벨센서 UNO — 건드리지 않는다
            continue
        try:
            s = serial.Serial(p.device, BAUD, timeout=2.0)
        except Exception:
            continue
        try:
            s.dtr = False; time.sleep(0.2); s.dtr = True; time.sleep(4.0)
            s.reset_input_buffer()
            s.write(b"M115\n")
            time.sleep(2.0)
            txt = s.read(s.in_waiting or 1).decode("ascii", "replace")
        finally:
            s.close()
        if "FIRMWARE_NAME" in txt:
            return p.device
    return None


# ── 조그 UI ──────────────────────────────────────────────────
HELP = """
  ← →      X− / X+          ↑ ↓      Y+ / Y−
  PgUp/PgDn Z+ / Z−          1 2 3 4  스텝 0.1 / 0.5 / 1 / 5 mm
  Enter    현재 위치 캡처     p        현재 좌표 출력
  h        재호밍 (G28)       q        중단
"""


def jog_capture(m, title, hint):
    step = 1.0
    print("\n" + "=" * 70)
    print(f"  [{title}]")
    print(f"  {hint}")
    print("=" * 70)
    print(HELP)
    while True:
        p = m.pos()
        ps = f"X{p[0]:7.2f} Y{p[1]:7.2f} Z{p[2]:6.2f}" if p else "(위치 불명)"
        print(f"\r  {ps}   스텝 {step:>4}mm   [Enter=캡처] ", end="", flush=True)
        k = msvcrt.getch()
        if k in (b"\xe0", b"\x00"):
            k2 = msvcrt.getch()
            if k2 == b"K":   m.jog(dx=-step)
            elif k2 == b"M": m.jog(dx=+step)
            elif k2 == b"H": m.jog(dy=+step)
            elif k2 == b"P": m.jog(dy=-step)
            elif k2 == b"I": m.jog(dz=+step)
            elif k2 == b"Q": m.jog(dz=-step)
            continue
        if k in b"1234":
            step = STEPS[int(k) - 1]; continue
        if k in (b"\r", b"\n"):
            p = m.pos()
            if not p:
                print("\n  [X] M114 실패 — 다시 시도")
                continue
            print(f"\n  ✔ 캡처: X{p[0]:.2f} Y{p[1]:.2f} Z{p[2]:.2f}")
            return p
        if k in (b"p", b"P"):
            print(f"\n  현재: {m.pos()}")
        elif k in (b"h", b"H"):
            print("\n  G28 호밍...")
            m.cmd("G1 Z30 F1200", wait=4.0)
            m.cmd("G28", wait=90.0)
        elif k in (b"q", b"Q"):
            raise KeyboardInterrupt


# ── 좌표 생성 ────────────────────────────────────────────────
def build_plate(tag, a1, a12, h12):
    """3 코너 → 피치 + 스큐 진단 + 96웰"""
    ypitch = (a12[1] - a1[1]) / 11.0
    xpitch = (h12[0] - a12[0]) / 7.0
    skew_x = a12[0] - a1[0]      # A1→A12 는 순수 +Y 여야 함
    skew_y = h12[1] - a12[1]     # A12→H12 는 순수 +X 여야 함

    print(f"\n  [플레이트 {tag}]")
    print(f"    A1  = ({a1[0]:.2f}, {a1[1]:.2f})")
    print(f"    Y피치(열) = {ypitch:.4f} mm   X피치(행) = {xpitch:.4f} mm")
    print(f"    스큐: A1→A12 의 X편차 {skew_x:+.2f}mm / A12→H12 의 Y편차 {skew_y:+.2f}mm")
    for nm, v in (("Y피치", ypitch), ("X피치", xpitch)):
        if not (8.5 <= v <= 9.5):
            print(f"    ⚠ {nm} {v:.3f}mm — SBS 표준 9.0mm 에서 벗어남. 코너 지정 오류 의심")
    for nm, v in (("A1→A12 X편차", skew_x), ("A12→H12 Y편차", skew_y)):
        if abs(v) > 1.0:
            print(f"    ⚠ {nm} {v:+.2f}mm — 플레이트가 기울었거나 잘못된 웰을 찍음")

    wells = []
    for r in range(8):
        for c in range(12):
            wells.append({
                "plate": tag,
                "well": f"{ROWS[r]}{c+1}",
                "row_idx": r,
                "col_idx": c,
                "machine_X": round(a1[0] + r * xpitch, 3),
                "machine_Y": round(a1[1] + c * ypitch, 3),
                "Z_approach": None,     # 나중에 일괄 주입
                "Z_dispense": None,
            })
    return wells, {"A1": [round(a1[0], 2), round(a1[1], 2)],
                   "A12": [round(a12[0], 2), round(a12[1], 2)],
                   "H12": [round(h12[0], 2), round(h12[1], 2)],
                   "X_pitch_mm": round(xpitch, 4),
                   "Y_pitch_mm": round(ypitch, 4)}


def verify_moves(m, data):
    """생성된 좌표로 대표 웰 순회 — 눈으로 확인"""
    z = data["frame"]["z_levels"]
    targets = [("A", "A1"), ("A", "A12"), ("A", "H12"), ("A", "D6"),
               ("B", "A1"), ("B", "H12")]
    idx = {(w["plate"], w["well"]): w for w in data["wells"]}
    print("\n=== 검증 이동 — 니들이 각 웰 중앙에 오는지 눈으로 확인 ===")
    for pl, wid in targets:
        w = idx.get((pl, wid))
        if not w:
            continue
        input(f"  Enter → {pl}_{wid} (X{w['machine_X']:.2f} Y{w['machine_Y']:.2f}) 로 이동")
        m.cmd(f"G1 Z{z['z_travel']} F1200", wait=4.0)
        m.cmd(f"G1 X{w['machine_X']} Y{w['machine_Y']} F6000", wait=10.0)
        m.cmd(f"G1 Z{z['z_dispense']} F1200", wait=4.0)
        m.cmd("M400", wait=30.0)
        print(f"    실제 M114: {m.pos()}")
    wp = data["wash_positions"][0]
    input(f"  Enter → WASH (X{wp['X']} Y{wp['Y']}) 로 이동")
    m.cmd(f"G1 Z{z['z_travel']} F1200", wait=4.0)
    m.cmd(f"G1 X{wp['X']} Y{wp['Y']} F6000", wait=10.0)
    m.cmd(f"G1 Z{wp['Z_dip']} F1200", wait=4.0)
    m.cmd("M400", wait=30.0)
    print(f"    실제 M114: {m.pos()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--verify", action="store_true", help="티칭 없이 현재 좌표로 검증 이동만")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("[X] Marlin 분취기를 찾지 못했습니다. --port COM11 처럼 지정하세요.")
        sys.exit(2)

    print(f"연결 중: {port} @{BAUD} ...")
    m = Marlin(port)
    print(f"  {m.fw}")

    try:
        if args.verify:
            with open(COORDS, encoding="utf-8") as f:
                verify_moves(m, json.load(f))
            return

        print("\n호밍합니다 (G28). 니들 경로에 장애물이 없는지 확인하세요.")
        input("  Enter 로 진행 (Ctrl+C 중단): ")
        m.cmd("G1 Z30 F1200", wait=4.0)
        r = m.cmd("G28", wait=120.0)
        if "ok" not in r.lower():
            print(f"  ⚠ G28 응답 이상: {r[:100]!r} — 엔드스톱 확인 필요")

        pts = {}
        pts["A_A1"] = jog_capture(m, "Plate A — A1", "니들을 A1 중앙, 분주 높이(Z)까지 내려서 맞추세요. 이 Z가 z_dispense 가 됩니다.")
        z_disp = pts["A_A1"][2]
        pts["A_A12"] = jog_capture(m, "Plate A — A12", "같은 행의 12번 열 (A1에서 열 방향 끝)")
        pts["A_H12"] = jog_capture(m, "Plate A — H12", "마지막 행 H의 12번 열 (대각 반대편 코너)")
        pts["B_A1"] = jog_capture(m, "Plate B — A1", "플레이트 B의 A1")
        pts["B_A12"] = jog_capture(m, "Plate B — A12", "플레이트 B의 A12")
        pts["B_H12"] = jog_capture(m, "Plate B — H12", "플레이트 B의 H12")
        pts["WASH"] = jog_capture(m, "WASH 위치", "세척 포트 중앙. 여기 Z가 Z_dip 이 됩니다.")

        wells_a, cal_a = build_plate("A", pts["A_A1"], pts["A_A12"], pts["A_H12"])
        wells_b, cal_b = build_plate("B", pts["B_A1"], pts["B_A12"], pts["B_H12"])

        ax = [w["machine_X"] for w in wells_a]
        bx = [w["machine_X"] for w in wells_b]
        if min(bx) < max(ax) and min(ax) < max(bx):
            print(f"\n  ⚠ 플레이트 A({min(ax):.1f}~{max(ax):.1f}) 와 B({min(bx):.1f}~{max(bx):.1f}) 의 X 범위가 겹칩니다 — 좌표 확인 필요")

        z_levels = {
            "z_travel": round(z_disp + DZ_TRAVEL, 2),
            "z_approach": round(z_disp + DZ_APPROACH, 2),
            "z_dispense": round(z_disp, 2),
            "z_wash_dip": round(pts["WASH"][2], 2),
            "note": f"조그 티칭 실측 (calibrate_plate96.py). z_dispense=Plate A A1 에서 캡처, "
                    f"approach=+{DZ_APPROACH}, travel=+{DZ_TRAVEL}. G28 기준 절대 높이",
        }
        for w in wells_a + wells_b:
            w["Z_approach"] = z_levels["z_approach"]
            w["Z_dispense"] = z_levels["z_dispense"]

        data = {
            "frame": {
                "machine_bed": [200.0, 200.0, 250.0],
                "bed_offset": [100.0, 100.0],
                "z_levels": z_levels,
                "z_reference": "dispense_height (jog-taught)",
                "needs_session_setup": "G28 후 절대좌표 사용",
                "calibration": {
                    "method": "조그 티칭 — Plate A/B 각 3코너(A1/A12/H12) + WASH",
                    "plate_A": cal_a,
                    "plate_B": cal_b,
                    "row_direction": "+X (A→H)",
                    "col_direction": "+Y (1→12)",
                },
                "global_offset_X": 0.0,
            },
            "wash_positions": [{"id": "WASH",
                                "X": round(pts["WASH"][0], 2),
                                "Y": round(pts["WASH"][1], 2),
                                "Z_dip": z_levels["z_wash_dip"]}],
            "wells": wells_a + wells_b,
        }

        print(f"\n총 {len(data['wells'])}웰 생성. Z: {z_levels['z_dispense']} (분주) / "
              f"{z_levels['z_approach']} (접근) / {z_levels['z_travel']} (이동) / {z_levels['z_wash_dip']} (WASH)")

        if input("\n저장할까요? (y/N): ").strip().lower() != "y":
            print("취소 — 파일 변경 없음")
            return

        if os.path.exists(COORDS):
            bak = COORDS + time.strftime(".bak_%Y%m%d_%H%M%S")
            shutil.copy2(COORDS, bak)
            print(f"  백업: {os.path.basename(bak)}")
        with open(COORDS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  저장 완료: {COORDS}")

        if input("\n검증 이동을 할까요? (y/N): ").strip().lower() == "y":
            verify_moves(m, data)

    except KeyboardInterrupt:
        print("\n\n중단 — 파일 변경 없음")
    finally:
        m.close()
        print("연결 종료")


if __name__ == "__main__":
    main()
