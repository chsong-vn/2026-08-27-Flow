# -*- coding: utf-8 -*-
"""deck_v13 (180도 회전 장착) 좌표 적용 — 5점 티칭.

deck_map.py 의 데크 좌표(원점=데크 중심)를 머신 좌표로 옮겨
well_coordinates.json 을 재생성한다.

데크 공칭 좌표 (deck_map.py, DECK_ROT_180=True, A1_CORNER=(-X,-Y))
    L-A1 (-77.49, -65.13)   L-H12 (-14.49, 33.87)
    R-A1 ( 14.49, -65.13)   R-H12 ( 77.49, 33.87)
    RES_CENTER (0.00, 77.25)   RES_LOW (42.75, 77.25)
    피치 9.000 정확, 행 A~H = +X, 열 1~12 = +Y

왜 5점인가
    deck_map.calibrate() 는 2점으로 전역 변환(평행이동+회전+**스케일**)을 푼다.
    그런데 설계 파일 자신이 경고하듯 "플레이트 유격 +-CLEARANCE mm 는 좌표에
    그대로 실린다" — 포켓 유격은 **플레이트마다 독립적인 평행이동 오차**라
    전역 변환으로는 L 을 맞추면 R 이 유격만큼 어긋난다.
    또 scale 을 자유변수로 두면 티칭 오차 0.2mm 가 그대로 0.2% 스케일로 구워져
    반대편 코너에서 되살아난다.
    -> 플레이트별 독립 강체변환(scale=1.0 고정) + RES 직접 티칭.

@codesyncer-decision: 캡처값은 목표가 아니라 M114 실측을 쓴다.
  소프트 엔드스톱이 MIN/MAX 둘 다 켜져 있어(Configuration.h:1772,1786)
  범위 밖 조그는 에러 없이 잘린다. 목표를 믿으면 좌표가 조용히 어긋난다.

@codesyncer-decision: 코드 무수정 — 좌표 데이터만 바꾼다.
  출력 스키마는 기존 well_coordinates.json 과 키 단위로 동일 (Z_dip/z_wash_dip 유지).
  RES '위에서 토출만' 도 데이터로 해결: Z_dip 값 자체를 림 위 높이로 넣는다.
  드라이버 move_to_wash() 는 Z_dip 을 그냥 Z 목표로 쓰므로 담기지 않는다.

사용법
    py -3.14 calibrate_deck_v13.py               # 자동 탐지 후 티칭
    py -3.14 calibrate_deck_v13.py --port COM15
    py -3.14 calibrate_deck_v13.py --dry-run     # 하드웨어 없이 공칭 좌표/포락선만 점검
    py -3.14 calibrate_deck_v13.py --verify      # 티칭 없이 현재 json 으로 검증 이동
"""
import sys, os, json, time, shutil, math, argparse

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COORDS = os.path.join(HERE, "hardware", "collectors", "data", "well_coordinates.json")
BAUD = 250000
ROWS = "ABCDEFGH"
PITCH = 9.00

# 펌웨어 실측 한계 (Configuration.h:1739-1748 / M211 덤프)
LIM_MIN = (0.0, 0.0)
LIM_MAX = (200.0, 200.0)
SAFE_MARGIN = 5.0          # 이 안쪽으로 들어와야 통과

# 데크 공칭 좌표 — deck_map.py 산출값
NOMINAL = {
    "L-A1":  (-77.49, -65.13),
    "L-H12": (-14.49,  33.87),
    "R-A1":  ( 14.49, -65.13),
    "R-H12": ( 77.49,  33.87),
    "RES_CENTER": (0.00, 77.25),
    "RES_LOW":    (42.75, 77.25),
}

# M3 볼트 4개, 209x209 정사각 — 패턴 중심 = 데크 원점.
# 스트로크(200)보다 넓어 4점 모두 니들 도달범위 밖 -> 마킹은 센터크로스 정렬로.
# 주의: 구 베드 홀(210x210 M4, 3D collector.py)과 불일치 — 재사용 불가, 신규 고정점.
BOLTS = {
    "BOLT_FL": (-104.5, -104.5),
    "BOLT_FR": ( 104.5, -104.5),
    "BOLT_RL": (-104.5,  104.5),
    "BOLT_RR": ( 104.5,  104.5),
}

# L/R -> 기존 Plate A/B 매핑. 드라이버가 plate 태그 'A'/'B' 를 하드코딩하므로
# (collector_plate96.py:74 `for plate in ['A','B']`) 내부 태그는 반드시 A/B 를 쓴다.
# 'L'/'R' 로 쓰면 well_sequence 가 비어 total_tubes=0 이 되어 조용히 죽는다.
SIDE_TO_PLATE = {"L": "A", "R": "B"}

RES_CLEARANCE = 3.0        # 리저버 림 위 토출 클리어런스 (mm)

TEACH_POINTS = [
    ("L-A1",  "L 플레이트 A1", "좌측(-X)·전방(-Y) 모서리 웰. 여기서 Z(분주높이)도 함께 잡습니다."),
    ("L-H12", "L 플레이트 H12", "L 의 대각 반대편 코너 (행 H, 열 12)"),
    ("R-A1",  "R 플레이트 A1", "우측 플레이트의 A1"),
    ("R-H12", "R 플레이트 H12", "R 의 대각 반대편 코너"),
    ("RES_CENTER", "폐액 리저버 중앙", "니들을 리저버 중앙 **위**에 두세요. 담그지 않습니다."),
]


# ── 기하 ─────────────────────────────────────────────────────
def deck_well(side, ri, ci):
    """데크 좌표계 웰 중심. ri=0..7 (A~H, +X), ci=0..11 (1~12, +Y)"""
    ax, ay = NOMINAL[f"{side}-A1"]
    return ax + ri * PITCH, ay + ci * PITCH


def fit_rigid(nom_pts, meas_pts):
    """scale=1.0 고정 강체변환 (회전 + 평행이동) 최소자승.

    반환: (to_machine(x,y), info)
    2점이면 4식 3미지수 -> 잔차가 곧 '측정한 두 점 사이 거리'와 공칭 거리의 차이의 절반.
    잔차가 크면 티칭 오차이거나 플레이트가 기울어 앉은 것.
    """
    n = len(nom_pts)
    cnx = sum(p[0] for p in nom_pts) / n
    cny = sum(p[1] for p in nom_pts) / n
    cmx = sum(p[0] for p in meas_pts) / n
    cmy = sum(p[1] for p in meas_pts) / n

    # Kabsch 2D: theta = atan2(sum cross, sum dot)
    sxy = sxx = 0.0
    for (nx, ny), (mx, my) in zip(nom_pts, meas_pts):
        ax, ay = nx - cnx, ny - cny
        bx, by = mx - cmx, my - cmy
        sxy += ax * by - ay * bx      # cross
        sxx += ax * bx + ay * by      # dot
    theta = math.atan2(sxy, sxx)
    c, s = math.cos(theta), math.sin(theta)
    ox = cmx - (c * cnx - s * cny)
    oy = cmy - (s * cnx + c * cny)

    def to_machine(x, y):
        return round(c * x - s * y + ox, 3), round(s * x + c * y + oy, 3)

    resid = []
    for (nx, ny), (mx, my) in zip(nom_pts, meas_pts):
        px, py = to_machine(nx, ny)
        resid.append(math.hypot(px - mx, py - my))

    return to_machine, {
        "rotation_deg": round(math.degrees(theta), 4),
        "origin": [round(ox, 3), round(oy, 3)],
        "residual_max_mm": round(max(resid), 4),
        "residual_mm": [round(r, 4) for r in resid],
    }


def envelope_report(points, label=""):
    """생성 좌표가 소프트 엔드스톱 안에 있는지. 반환 True=통과"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lo = (min(xs), min(ys))
    hi = (max(xs), max(ys))
    margin = (lo[0] - LIM_MIN[0], LIM_MAX[0] - hi[0],
              lo[1] - LIM_MIN[1], LIM_MAX[1] - hi[1])
    ok = min(margin) >= SAFE_MARGIN
    mark = "OK" if ok else "!! 범위 벗어남"
    print(f"\n  [포락선 {label}] {mark}")
    print(f"    X {lo[0]:7.2f} ~ {hi[0]:7.2f}   (한계 {LIM_MIN[0]:.0f}~{LIM_MAX[0]:.0f}, "
          f"여유 {margin[0]:+.2f} / {margin[1]:+.2f})")
    print(f"    Y {lo[1]:7.2f} ~ {hi[1]:7.2f}   (한계 {LIM_MIN[1]:.0f}~{LIM_MAX[1]:.0f}, "
          f"여유 {margin[2]:+.2f} / {margin[3]:+.2f})")
    if not ok:
        print(f"    -> 소프트 엔드스톱이 MIN/MAX 둘 다 켜져 있어 이 좌표는 "
              f"에러 없이 잘립니다. 데크를 물리적으로 옮겨야 합니다.")
    return ok


def build_wells(side, to_machine, z_approach, z_dispense):
    plate = SIDE_TO_PLATE[side]
    out = []
    for ri in range(8):
        for ci in range(12):
            dx, dy = deck_well(side, ri, ci)
            mx, my = to_machine(dx, dy)
            out.append({
                "plate": plate,
                "well": f"{ROWS[ri]}{ci + 1}",
                "row_idx": ri,
                "col_idx": ci,
                "machine_X": mx,
                "machine_Y": my,
                "Z_approach": z_approach,
                "Z_dispense": z_dispense,
                "deck_X": round(dx, 3),
                "deck_Y": round(dy, 3),
            })
    return out


# ── Marlin 통신 ──────────────────────────────────────────────
class Marlin:
    def __init__(self, port):
        import serial
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
        self.cmd("G90", wait=1.0)
        lim = self.cmd("M211", wait=2.0)
        print(f"  M211: {lim.strip().splitlines()[0] if lim.strip() else '(무응답)'}")

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
        r = self.cmd("M114", wait=3.0)
        try:
            seg = r.split("Count")[0]
            return tuple(float(seg[seg.index(a + ":") + 2:].split()[0]) for a in "XYZ")
        except Exception:
            return None

    def jog(self, dx=0.0, dy=0.0, dz=0.0, f=3000):
        self.cmd("G91", wait=1.0)
        self.cmd(f"G1 X{dx} Y{dy} Z{dz} F{f}", wait=2.0)
        self.cmd("M400", wait=15.0)
        self.cmd("G90", wait=1.0)

    def close(self):
        try:
            self.cmd("M84", wait=1.0)
            self.ser.close()
        except Exception:
            pass


def configured_port():
    """hardware_config.json 에 등록된 분취기 포트 (dev_plate96)"""
    try:
        with open(os.path.join(HERE, "hardware_config.json"), encoding="utf-8") as f:
            hw = json.load(f)
        # @codesyncer-decision: config 최상위 키는 'inventory' (결함 2 수정, 2026-08-04)
        #   'devices' 로 읽으면 항상 None -> 전체 COM 스캔 폴백 -> DTR 토글로
        #   Chemyx/ESP32/Runze 가 리셋된다. 등록 포트 조회가 반드시 먼저 성공해야 함.
        for d in hw.get("inventory", []):
            if "plate96" in d.get("id", "").lower() or "Plate96" in d.get("driver", ""):
                return d.get("port")
    except Exception:
        pass
    return None


def probe(port):
    """해당 포트가 Marlin 인지 1회 확인"""
    import serial
    try:
        s = serial.Serial(port, BAUD, timeout=2.0)
    except Exception:
        return False
    try:
        s.dtr = False; time.sleep(0.2); s.dtr = True; time.sleep(4.0)
        s.reset_input_buffer()
        s.write(b"M115\n")
        time.sleep(2.0)
        txt = s.read(s.in_waiting or 1).decode("ascii", "replace")
    finally:
        s.close()
    return "FIRMWARE_NAME" in txt


def find_port():
    # 1) 등록 포트 우선 — 전체 스캔은 DTR 토글로 다른 계측기(Chemyx/ESP32/Runze)를
    #    리셋시킬 수 있어 마지막 수단으로만 쓴다.
    cp = configured_port()
    if cp:
        print(f"  hardware_config.json 등록 포트 {cp} 확인 중...")
        if probe(cp):
            return cp
        print(f"  {cp} 무응답 — 전체 스캔으로 전환 (다른 계측기 연결 중이면 Ctrl+C 권장)")
    import serial
    import serial.tools.list_ports as lp
    for p in lp.comports():
        if p.device == "COM3":       # 레벨센서 UNO — 건드리지 않는다
            continue
        if cp and p.device == cp:
            continue                 # 이미 확인함
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


HELP = """
  <- ->     X- / X+           ^ v      Y+ / Y-
  PgUp/PgDn Z+ / Z-           1 2 3 4  스텝 0.05 / 0.25 / 0.5 / 2.5 mm
  Enter     현재 위치 캡처     p        현재 좌표 출력
  h         재호밍 (G28)       q        중단
"""


def jog_capture(m, title, hint, z_hint=None):
    import msvcrt
    # 호밍 등 긴 대기 중 눌린 키(특히 Enter)가 버퍼에 남아 진입 즉시
    # 오캡처되는 사고 방지 — 진입 시 콘솔 입력 버퍼를 비운다.
    while msvcrt.kbhit():
        msvcrt.getch()
    steps = [0.05, 0.25, 0.5, 2.5]
    step = 0.5
    print("\n" + "=" * 72)
    print(f"  [{title}]")
    print(f"  {hint}")
    if z_hint:
        print(f"  * {z_hint}")
    print("=" * 72)
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
            step = steps[int(k) - 1]; continue
        if k in (b"\r", b"\n"):
            p = m.pos()
            if not p:
                print("\n  [X] M114 실패 — 다시 시도")
                continue
            print(f"\n  o 캡처: X{p[0]:.2f} Y{p[1]:.2f} Z{p[2]:.2f}")
            return p
        if k in (b"p", b"P"):
            print(f"\n  현재: {m.pos()}")
        elif k in (b"h", b"H"):
            print("\n  G28 호밍...")
            m.cmd("G1 Z40 F1200", wait=6.0)
            r = m.cmd("G28", wait=120.0)
            if "ok" not in r.lower():
                print(f"\n  !! G28 응답 이상: {r[:100]!r}")
                print("  !! 원점 미확정 — 이 상태에서 캡처한 좌표는 무효입니다. "
                      "q 로 중단하고 원인 해결을 권장")
            while msvcrt.kbhit():   # 호밍 중 눌린 키 제거 (오캡처 방지)
                msvcrt.getch()
        elif k in (b"q", b"Q"):
            raise KeyboardInterrupt


# ── 조립 ─────────────────────────────────────────────────────
def assemble(meas, z_dispense, z_travel, z_res_discharge):
    """meas: {name: (x, y, z)} 5점 -> well_coordinates.json 데이터"""
    z_approach = round(min(z_dispense + 2.0, z_travel), 2)

    fits = {}
    all_pts = []
    wells = []
    for side in ("L", "R"):
        nom = [NOMINAL[f"{side}-A1"], NOMINAL[f"{side}-H12"]]
        mea = [meas[f"{side}-A1"][:2], meas[f"{side}-H12"][:2]]
        to_m, info = fit_rigid(nom, mea)
        fits[side] = info
        print(f"\n  [플레이트 {side} -> {SIDE_TO_PLATE[side]}]  "
              f"회전 {info['rotation_deg']:+.4f}deg   잔차 {info['residual_max_mm']:.3f}mm")
        if info["residual_max_mm"] > 0.3:
            print(f"    !! 잔차 0.3mm 초과 — 티칭 오차이거나 플레이트가 기울어 앉았습니다. "
                  f"코너를 다시 확인하세요.")
        if abs(info["rotation_deg"]) > 0.5:
            print(f"    !! 회전 0.5deg 초과 — 데크/플레이트가 돌아가 있습니다.")
        w = build_wells(side, to_m, z_approach, z_dispense)
        wells.extend(w)
        all_pts.extend([(x["machine_X"], x["machine_Y"]) for x in w])

    # RES: 중앙은 직접 티칭값을 그대로 쓰고, LOW 는 전역 회전만 적용해 파생
    nom_all = [NOMINAL[k] for k in ("L-A1", "L-H12", "R-A1", "R-H12")]
    mea_all = [meas[k][:2] for k in ("L-A1", "L-H12", "R-A1", "R-H12")]
    to_g, ginfo = fit_rigid(nom_all, mea_all)
    print(f"\n  [전역 변환] 회전 {ginfo['rotation_deg']:+.4f}deg  "
          f"원점 {ginfo['origin']}  잔차 {ginfo['residual_max_mm']:.3f}mm")
    print(f"    -> 전역 잔차는 두 플레이트의 포켓 유격 차이를 포함합니다 "
          f"(설계 파일이 경고한 +-CLEARANCE).")

    res_c = (round(meas["RES_CENTER"][0], 3), round(meas["RES_CENTER"][1], 3))
    pred_c = to_g(*NOMINAL["RES_CENTER"])
    err = math.hypot(res_c[0] - pred_c[0], res_c[1] - pred_c[1])
    print(f"\n  [RES_CENTER] 티칭 {res_c}  vs  공칭예측 {pred_c}   차이 {err:.2f}mm")
    if err > 2.0:
        print(f"    !! 2mm 초과 — 리저버 안착 위치 또는 티칭을 확인하세요.")

    # RES_LOW = RES_CENTER + 전역 회전 적용한 (42.75, 0)
    dx = NOMINAL["RES_LOW"][0] - NOMINAL["RES_CENTER"][0]
    dy = NOMINAL["RES_LOW"][1] - NOMINAL["RES_CENTER"][1]
    th = math.radians(ginfo["rotation_deg"])
    res_l = (round(res_c[0] + dx * math.cos(th) - dy * math.sin(th), 3),
             round(res_c[1] + dx * math.sin(th) + dy * math.cos(th), 3))

    all_pts.extend([res_c, res_l])
    ok = envelope_report(all_pts, "전체 194점")

    # 키 이름은 기존 스키마 그대로 유지 — 코드 무수정이 목표.
    # z_wash_dip 의 '값'만 의미가 바뀐다: 담금 깊이가 아니라 리저버 림 위 토출 높이.
    # 드라이버는 wash_positions[0].Z_dip 을 그냥 Z 목표로 쓸 뿐이라 데이터만으로 충분.
    z_levels = {
        "z_travel": round(z_travel, 2),
        "z_approach": z_approach,
        "z_dispense": round(z_dispense, 2),
        "z_wash_dip": round(z_res_discharge, 2),
        "note": "deck_v13 5점 티칭 (calibrate_deck_v13.py). z_dispense=L-A1 실측, "
                "z_travel=별도 티칭(파생 금지 — 웰 깊이만큼 내려가면 dispense+6 은 림 아래), "
                "z_wash_dip=폐액 리저버 '림 위' 토출 높이 (담금 아님 — 위에서 토출만). "
                "G28 기준 절대 높이",
    }

    data = {
        "frame": {
            "soft_endstop_min": [LIM_MIN[0], LIM_MIN[1], 0.0],
            "soft_endstop_max": [LIM_MAX[0], LIM_MAX[1], 250.0],
            "z_levels": z_levels,
            "z_reference": "G28 절대좌표 (deck_v13, 프로파일 상면 마운트)",
            "needs_session_setup": "G28 후 절대좌표 사용",
            "deck": {
                "source": "deck_map.py / deck_v13.py",
                "deck_rot_180": True,
                "a1_corner": ["-X", "-Y"],
                "pitch_mm": PITCH,
                "nominal_deck_coords": {k: list(v) for k, v in NOMINAL.items()},
                "side_to_plate": SIDE_TO_PLATE,
                "bolt_pattern": {
                    "thread": "M3", "square_mm": 209.0,
                    "deck_coords": {k: list(v) for k, v in BOLTS.items()},
                    "machine_coords_global_fit": {
                        k: list(to_g(*v)) for k, v in BOLTS.items()},
                    "note": "패턴 중심=데크 원점. 4점 모두 니들 스트로크 밖(정상). "
                            "구 베드 홀 210x210 M4 와 비호환",
                },
            },
            "calibration": {
                "method": "5점 티칭 — L(A1,H12) / R(A1,H12) / RES_CENTER. "
                          "플레이트별 강체변환 scale=1.0 고정",
                "taught": {k: [round(v[0], 2), round(v[1], 2), round(v[2], 2)]
                           for k, v in meas.items()},
                "fit_L": fits["L"],
                "fit_R": fits["R"],
                "fit_global": ginfo,
                "res_center_predict_error_mm": round(err, 3),
                "row_direction": "+X (A->H)",
                "col_direction": "+Y (1->12)",
            },
            "envelope_ok": ok,
        },
        # 기존 스키마(Z_dip) 그대로 — 드라이버 move_to_wash() 무수정 호환.
        # Z_dip 값이 림 위 높이이므로 니들은 담기지 않고 위에서 토출만 한다.
        # [0]=RES_CENTER 가 move_to_wash() 목표. RES_LOW 는 참조용(드라이버 미사용).
        "wash_positions": [
            {"id": "RES_CENTER", "X": res_c[0], "Y": res_c[1],
             "Z_dip": z_levels["z_wash_dip"],
             "note": "폐액 토출 위치 (리저버 중앙, 벽에서 가장 멀다). Z_dip=림 위 — 담금 아님"},
            {"id": "RES_LOW", "X": res_l[0], "Y": res_l[1],
             "Z_dip": z_levels["z_wash_dip"],
             "note": "경사 낮은 쪽/배출 노치. 참조용 — 드라이버는 [0]만 사용"},
        ],
        "wells": wells,
    }
    return data, ok


def dry_run():
    """하드웨어 없이 공칭 좌표만으로 포락선 점검 — 데크 원점 후보를 훑는다."""
    print("=== dry-run: 데크 원점별 포락선 ===")
    print(f"  소프트 엔드스톱 X/Y {LIM_MIN[0]:.0f}~{LIM_MAX[0]:.0f} (안전여유 {SAFE_MARGIN}mm)")
    pts = []
    for side in ("L", "R"):
        for ri in range(8):
            for ci in range(12):
                pts.append(deck_well(side, ri, ci))
    pts.append(NOMINAL["RES_CENTER"])
    pts.append(NOMINAL["RES_LOW"])
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    print(f"\n  데크 좌표 소요범위  X {min(xs):+.2f}~{max(xs):+.2f} ({max(xs)-min(xs):.2f}mm)"
          f"   Y {min(ys):+.2f}~{max(ys):+.2f} ({max(ys)-min(ys):.2f}mm)")
    ox_lo = LIM_MIN[0] + SAFE_MARGIN - min(xs); ox_hi = LIM_MAX[0] - SAFE_MARGIN - max(xs)
    oy_lo = LIM_MIN[1] + SAFE_MARGIN - min(ys); oy_hi = LIM_MAX[1] - SAFE_MARGIN - max(ys)
    print(f"  데크 원점 허용창    X {ox_lo:.2f} ~ {ox_hi:.2f}   Y {oy_lo:.2f} ~ {oy_hi:.2f}")
    rec = ((ox_lo + ox_hi) / 2, (oy_lo + oy_hi) / 2)
    print(f"  권장(창 중앙)       X {rec[0]:.2f}   Y {rec[1]:.2f}")
    print(f"  * 볼트패턴 중심 = 데크 원점 -> 위 창이 곧 볼트패턴 중심 허용창")

    print(f"\n  [권장 배치 볼트 머신좌표 (M3, 209 정사각 — 전부 스트로크 밖=정상)]")
    for k, (bx, by) in BOLTS.items():
        print(f"      {k}  ({rec[0] + bx:7.2f}, {rec[1] + by:7.2f})")
    print(f"\n  [마운팅 정렬 조그 타깃 (권장 배치)]")
    print(f"      데크 센터크로스   -> G1 X{rec[0]:.2f} Y{rec[1]:.2f}")
    print(f"      회전 정렬 (y=0선) -> G1 X{rec[0]-60:.2f} Y{rec[1]:.2f}  <->  "
          f"G1 X{rec[0]+60:.2f} Y{rec[1]:.2f}")

    for name, org in (("베드중심 (100,100)", (100.0, 100.0)),
                      ("구 지그 실측 (106.6, 98.6)", (106.6, 98.6))):
        moved = [(x + org[0], y + org[1]) for x, y in pts]
        envelope_report(moved, name)
        for k in ("L-A1", "L-H12", "R-A1", "R-H12", "RES_CENTER", "RES_LOW"):
            nx, ny = NOMINAL[k]
            print(f"      {k:11s} ({nx + org[0]:7.2f}, {ny + org[1]:7.2f})")


def verify_moves(m, data):
    z = data["frame"]["z_levels"]
    idx = {(w["plate"], w["well"]): w for w in data["wells"]}
    targets = [("A", "A1"), ("A", "A12"), ("A", "H12"), ("A", "D6"),
               ("B", "A1"), ("B", "H12")]
    print("\n=== 검증 이동 — 니들이 각 웰 중앙에 오는지 눈으로 확인 ===")
    for pl, wid in targets:
        w = idx.get((pl, wid))
        if not w:
            continue
        input(f"  Enter -> {pl}_{wid} (X{w['machine_X']:.2f} Y{w['machine_Y']:.2f})")
        m.cmd(f"G1 Z{z['z_travel']} F1200", wait=6.0)
        m.cmd(f"G1 X{w['machine_X']} Y{w['machine_Y']} F6000", wait=12.0)
        m.cmd(f"G1 Z{z['z_dispense']} F1200", wait=6.0)
        m.cmd("M400", wait=30.0)
        print(f"    실제 M114: {m.pos()}")
    for wp in data["wash_positions"]:
        zt = wp.get("Z_dip", z["z_travel"])
        input(f"  Enter -> {wp['id']} (X{wp['X']} Y{wp['Y']} Z{zt}) — 담그지 않고 위에서만")
        m.cmd(f"G1 Z{z['z_travel']} F1200", wait=6.0)
        m.cmd(f"G1 X{wp['X']} Y{wp['Y']} F6000", wait=12.0)
        m.cmd(f"G1 Z{zt} F1200", wait=6.0)
        m.cmd("M400", wait=30.0)
        print(f"    실제 M114: {m.pos()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        dry_run()
        return

    port = args.port or find_port()
    if not port:
        print("[X] Marlin 분취기를 찾지 못했습니다. --port COM15 처럼 지정하세요.")
        sys.exit(2)

    print(f"연결 중: {port} @{BAUD} ...")
    m = Marlin(port)
    print(f"  {m.fw}")

    try:
        if args.verify:
            # 접속 시 DTR 리셋으로 논리좌표가 0,0,0 이 된 상태 — 호밍 없이
            # 절대좌표 이동하면 전부 어긋난다 (2026-08-04 실기에서 확인).
            print("\n호밍합니다 (G28). 니들 경로에 장애물이 없는지 확인하세요.")
            input("  Enter 로 진행 (Ctrl+C 중단): ")
            m.cmd("G1 Z40 F1200", wait=6.0)
            r = m.cmd("G28", wait=150.0)
            if "ok" not in r.lower():
                print(f"  !! G28 응답 이상: {r[:100]!r} — 검증을 중단합니다")
                return
            import msvcrt
            while msvcrt.kbhit():   # 호밍 중 눌린 키 제거 (첫 타깃 자동진행 방지)
                msvcrt.getch()
            with open(COORDS, encoding="utf-8") as f:
                verify_moves(m, json.load(f))
            return

        print("\n호밍합니다 (G28). 니들 경로에 장애물이 없는지 확인하세요.")
        input("  Enter 로 진행 (Ctrl+C 중단): ")
        m.cmd("G1 Z40 F1200", wait=6.0)
        r = m.cmd("G28", wait=150.0)
        # @codesyncer-decision: G28 실패 시 티칭 진행 금지 (2026-08-04 실기 결함 1)
        #   원점 미확정 상태로 캡처하면 잘못된 좌표 192웰이 경고 한 줄만 남기고
        #   조용히 저장된다. --verify 경로와 동일하게 즉시 중단.
        if "ok" not in r.lower():
            print(f"  !! G28 응답 이상: {r[:100]!r} — 원점 미확정. 티칭을 중단합니다")
            print("     (엔드스톱/모터 커넥터 확인 후 다시 실행하세요)")
            return

        # 0단계: 극단 2점 도달성 사전 확인 (데크가 창 밖이면 여기서 걸린다)
        print("\n--- 0단계: 도달성 확인 ---")
        print("  L-A1(최소 X/Y) 과 RES_CENTER(최대 Y) 두 극단에 니들이 닿는지 먼저 봅니다.")
        print("  여기서 못 닿으면 좌표가 아니라 데크 위치를 옮겨야 합니다.")

        meas = {}
        for i, (key, title, hint) in enumerate(TEACH_POINTS, 1):
            zh = None
            if key == "L-A1":
                zh = "이 Z 가 z_dispense 가 됩니다 — 웰 안쪽 분주 높이로 내리세요."
            elif key == "RES_CENTER":
                zh = "담그지 마세요. 리저버 림 위 토출 높이에서 캡처합니다."
            meas[key] = jog_capture(m, f"{i}/5  {title}", hint, zh)

        z_dispense = meas["L-A1"][2]
        z_res_discharge = meas["RES_CENTER"][2]

        # z_travel 은 파생하지 않고 별도 티칭 — 웰 깊이만큼 내려가 티칭했으면
        # dispense+6 은 플레이트 림 아래가 되어 XY 이동 시 긁는다.
        zt = jog_capture(
            m, "6/5  Z 안전 이동 높이",
            "니들이 플레이트/리저버 **모든 것보다 위**에 오도록 Z 만 올리세요.",
            "여기가 z_travel 입니다. XY 이동은 전부 이 높이에서 일어납니다.")
        z_travel = zt[2]

        if z_travel <= z_dispense:
            print(f"\n  !! z_travel({z_travel}) <= z_dispense({z_dispense}) — 취소합니다.")
            return
        if z_res_discharge > z_travel:
            print(f"\n  !! z_res_discharge({z_res_discharge}) > z_travel({z_travel}) — "
                  f"토출 높이가 이동 높이보다 높습니다. 취소합니다.")
            return

        data, ok = assemble(meas, z_dispense, z_travel, z_res_discharge)

        print(f"\n총 {len(data['wells'])}웰 + RES {len(data['wash_positions'])}점 생성")
        zl = data["frame"]["z_levels"]
        print(f"  Z: 분주 {zl['z_dispense']} / 접근 {zl['z_approach']} / "
              f"이동 {zl['z_travel']} / RES토출 {zl['z_wash_dip']}")

        if not ok:
            print("\n  !! 포락선 미통과 — 저장을 권하지 않습니다.")
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
