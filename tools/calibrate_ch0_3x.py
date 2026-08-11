# -*- coding: utf-8 -*-
"""CH0(=ID1) 3왕복 정밀 캘리브레이션 — 0.3 mL 스텝 정지-측정.

전제: 시작 시 주사기 0 mL (사용자 수동 정렬).
프로토콜: [0 → 5 mL → 0] × 3 사이클, 0.3 mL 스텝(끝단 5.0/0.0 포함),
각 스텝 정지 후 CH0 median 측정.
출력: ch0_cal3_points.csv (cycle, leg, vol_mL, raw_cm)
분석: 통합 선형 fit + 0.3 mL 지점별 잔차/왕복차/사이클 재현성.

주의: Chemyx dispensed 카운터는 stop 시 리셋됨 → 이동검증은 stop 없이
      전후 델타로 수행(자연 완료 대기).
"""
import statistics
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

SENS_PORT, PUMP_PORT = "COM3", "COM5"
PID = 1
DIAMETER = 12.45
RATE = 5.0              # mL/min ("천천히")
STEP = 0.3
TOP = 5.0
CSV = "ch0_cal3_points.csv"
CYCLES = 3


def readfull(ser, t=0.8):
    t0 = time.time()
    buf = b""
    while time.time() - t0 < t:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.02)
    return buf


def cmd(pump, c, retries=3):
    for _ in range(retries):
        pump.reset_input_buffer()
        pump.write((c + "\r").encode())
        r = readfull(pump)
        if r:
            return r.decode("ascii", "replace").strip()
        time.sleep(0.4)
    return ""


def stop3(pump):
    for _ in range(3):
        try:
            pump.reset_input_buffer()
            pump.write(f"{PID} stop\r".encode())
            time.sleep(0.3)
        except Exception:
            pass


import re

def read_dispensed(pump):
    """'dispensedvolume = X' 의 X 를 파싱.
    ⚠ 첫 float 토큰을 잡으면 명령 에코의 펌프 ID('1 dispensed volume')를
    값으로 오인한다 — 반드시 키워드 뒤의 숫자만."""
    r = cmd(pump, f"{PID} dispensed volume")
    m = re.search(r"dispensedvolume\s*=\s*(-?[0-9.]+)", r, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def ch0_median(sens, n=10, tmax=6.0):
    time.sleep(0.5)
    sens.reset_input_buffer()
    vals = []
    t0 = time.time()
    while len(vals) < n and time.time() - t0 < tmax:
        line = sens.readline().decode(errors="ignore").strip()
        if "," in line:
            c = line.split(",")[0].strip()
            if c != "NA":
                try:
                    vals.append(float(c))
                except ValueError:
                    pass
    if len(vals) < max(3, n // 2):
        return None
    return statistics.median(vals)


def move_to(pump, cur, target):
    delta = target - cur
    if abs(delta) < 1e-9:
        return True
    setvol = -delta                       # +infuse / -withdraw
    dur = abs(delta) / RATE * 60.0
    before = read_dispensed(pump)
    if not cmd(pump, f"{PID} set rate {RATE:g}"):
        print("  !! set rate 무응답")
        return False
    if not cmd(pump, f"{PID} set volume {setvol:g}"):
        print("  !! set volume 무응답")
        return False
    if not cmd(pump, f"{PID} start", retries=1):
        print("  !! start 무응답")
        return False
    time.sleep(dur + 1.2)
    after = read_dispensed(pump)
    if before is None or after is None:
        print("  !! dispensed 판독 불가")
        return False
    moved = abs(after - before)
    if abs(moved - abs(delta)) > 0.05 and abs(abs(after) - abs(delta)) > 0.05:
        print(f"  !! 이동량 불일치: 목표 {abs(delta):.2f} vs 카운터 {before!r}→{after!r}")
        return False
    return True


def frange_up():
    pts = []
    v = STEP
    while v < TOP - 1e-9:
        pts.append(round(v, 2))
        v += STEP
    pts.append(TOP)
    return pts


def main():
    up_pts = frange_up()                      # 0.3 .. 4.8, 5.0
    down_pts = [round(TOP - p, 2) for p in up_pts[:-1]][::-1] + [0.0]
    # down: 4.8? no — 대칭으로 4.7? 간단히: up 의 역순(5.0 제외) + 0.0
    down_pts = up_pts[:-1][::-1] + [0.0]      # 4.8, 4.5, ..., 0.3, 0.0

    print(f"=== CH0 3왕복 캘리브 (0.3 mL 스텝, {RATE} mL/min) ===")
    print(f"up {len(up_pts)}점 + down {len(down_pts)}점, {CYCLES}사이클")
    sens = serial.Serial(SENS_PORT, 9600, timeout=0.5)
    pump = serial.Serial(PUMP_PORT, 9600, timeout=0.2)
    time.sleep(2.2)
    pts = []

    def save():
        if pts:
            with open(CSV, "w", encoding="utf-8") as f:
                f.write("cycle,leg,vol_mL,raw_cm\n")
                for cy, leg, v, r in pts:
                    f.write(f"{cy},{leg},{v},{r}\n")
            print(f"({len(pts)}점 저장: {CSV})")

    try:
        if not cmd(pump, f"{PID} pump status"):
            print("펌프 통신 무응답 — 시작 불가")
            return
        cmd(pump, f"{PID} set units 0")
        cmd(pump, f"{PID} set diameter {DIAMETER:g}")

        # 직전 중단으로 플런저가 0.3 mL 에 있음 → 0 으로 복귀 후 시작
        if not move_to(pump, 0.3, 0.0):
            print("0 mL 복귀 실패 — 중단")
            return
        cur = 0.0
        raw = ch0_median(sens)
        if raw is None:
            print("CH0 판독 실패 — 시작 불가")
            return
        pts.append((1, "up", 0.0, raw))
        print(f"[c1 up  ] 0.00 mL  raw {raw:6.2f}")

        for cy in range(1, CYCLES + 1):
            for leg, targets in (("up", up_pts), ("down", down_pts)):
                for tgt in targets:
                    if not move_to(pump, cur, tgt):
                        print("중단 — 부분 저장")
                        stop3(pump)
                        return
                    cur = tgt
                    raw = ch0_median(sens)
                    if raw is None:
                        print("CH0 판독 실패 — 중단")
                        stop3(pump)
                        return
                    pts.append((cy, leg, tgt, raw))
                    print(f"[c{cy} {leg:4s}] {tgt:4.2f} mL  raw {raw:6.2f}")
            if cy < CYCLES:
                pts.append((cy + 1, "up", 0.0, pts[-1][3]))  # 0점은 다음 사이클 시작점 공유
        print("\n=== 수집 완료 ===")
    finally:
        stop3(pump)
        sens.close()
        pump.close()
        save()

    # ================= 분석 =================
    if len(pts) < 10:
        return
    xs = [r for _, _, _, r in pts]
    ys = [v for _, _, v, _ in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    inter = my - slope * mx
    resid = [y - (slope * x + inter) for x, y in zip(xs, ys)]
    rmse = (sum(r * r for r in resid) / n) ** 0.5

    print("\n=== 통합 선형 FIT ===")
    print(f"vol_mL = {slope:.6f} * raw_cm + {inter:.4f}")
    print(f"RMSE = {rmse * 1000:.0f} uL  (점 {n}개)")

    # 부피별 통계
    byvol = {}
    for cy, leg, v, r in pts:
        byvol.setdefault(v, {"up": [], "down": []})[leg].append(r)
    print("\n=== 0.3 mL 지점별 상세 (raw: 평균±스프레드 / up-down 차 / fit 잔차) ===")
    print(" vol   raw평균  사이클스프레드  왕복차(up-down)  fit잔차")
    for v in sorted(byvol):
        allr = byvol[v]["up"] + byvol[v]["down"]
        m = statistics.mean(allr)
        spread = max(allr) - min(allr)
        ud = ""
        if byvol[v]["up"] and byvol[v]["down"]:
            d = statistics.mean(byvol[v]["up"]) - statistics.mean(byvol[v]["down"])
            ud = f"{d:+.3f} cm"
        pred = slope * m + inter
        print(f" {v:4.2f}  {m:7.3f}   {spread:6.3f} cm      {ud:>10s}   {(v - pred) * 1000:+6.0f} uL")

    print("\n0 mL 근처(empty 검증용) 상세:")
    z = byvol.get(0.0, {"up": [], "down": []})
    zn = z["up"] + z["down"]
    if zn:
        print(f"  0 mL raw: {[f'{x:.2f}' for x in zn]}  (spread {max(zn)-min(zn):.3f} cm)")

    print("\nhardware_config.json → Group A 권장값 (통합 fit):")
    print(f'  "level_cal_slope": {slope:.6f},')
    print(f'  "level_cal_intercept": {inter:.4f},')
    print(f'  "level_samples": 20,')
    print(f'  "level_empty_gate_ul": {max(500.0, rmse * 1000 * 3):.0f}')


if __name__ == "__main__":
    main()
