# -*- coding: utf-8 -*-
"""CH0(=Chemyx ID1, Group A) 초음파 레벨센서 왕복 캘리브레이션.

전제: 5 mL 주사기, 시작 시점 주사기 내 부피 = START_VOL (mL).
프로토콜: withdraw 로 5 mL 까지 올라가며 각 지점 정지-측정,
          infuse 로 0 mL 까지 내려오며 재측정(히스테리시스).
모델: vol_mL = slope * raw_cm + intercept  (드라이버 get_volume 과 동일)

안전장치: 모든 펌프 명령은 에코 검증 + 3회 재시도. 실패 시 즉시 stop 3연발
후 부분 데이터 저장하고 종료. 각 이동 후 dispensed volume 으로 실이동 검증.

용법: py -3.14 calibrate_ch0.py
출력: ch0_cal_points.csv (vol_mL, raw_cm, leg) + fit 결과 콘솔
"""
import statistics
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

SENS_PORT = "COM3"
PUMP_PORT = "COM5"
PID = 1                 # Chemyx pump_id (Group A)
DIAMETER = 12.45        # mm (config Group A)
RATE = 10.0             # mL/min 이동 속도
START_VOL = 1.0         # 현재 주사기 내 부피 (조그 0.5 + 중단된 1차 시도 0.5)
UP_POINTS = [2.0, 3.0, 4.0, 5.0]           # withdraw 방향 목표 부피
DOWN_POINTS = [4.0, 3.0, 2.0, 1.0, 0.0]    # infuse 방향 목표 부피
CSV = "ch0_cal_points.csv"


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
    """명령 전송 + 에코 검증. 실패 시 '' 반환."""
    for _ in range(retries):
        pump.reset_input_buffer()
        pump.write((c + "\r").encode())
        r = readfull(pump)
        if r:
            return r.decode("ascii", "replace").strip()
        time.sleep(0.4)
    return ""


def emergency_stop(pump):
    for _ in range(3):
        try:
            pump.reset_input_buffer()
            pump.write(f"{PID} stop\r".encode())
            time.sleep(0.3)
        except Exception:
            pass


def ch0_median(sens, n=10, tmax=6.0):
    """CH0 정지 상태 median (settle 포함)."""
    time.sleep(0.6)               # 기계 진동 안정
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


def read_dispensed(pump):
    """dispensed volume 카운터(부호 유지). ⚠ 세션 누적식 — 이동량은 전후 차이로 판정."""
    r = cmd(pump, f"{PID} dispensed volume")
    for tok in r.replace("=", " ").split():
        try:
            return float(tok)
        except ValueError:
            continue
    return None


def move_to(pump, cur, target):
    """cur → target 이동 (부호: +infuse 는 주사기 부피 감소). 성공 시 True."""
    delta = target - cur                     # +면 withdraw(부피 증가) → set volume 음수
    setvol = -delta                          # Chemyx: +infuse / -withdraw
    dur = abs(delta) / RATE * 60.0
    before = read_dispensed(pump)
    r1 = cmd(pump, f"{PID} set rate {RATE:g}")
    r2 = cmd(pump, f"{PID} set volume {setvol:g}")
    if not r1 or not r2:
        print(f"  !! set 명령 무응답 (rate={bool(r1)}, vol={bool(r2)}) — 중단")
        return False
    r3 = cmd(pump, f"{PID} start", retries=1)
    if not r3:
        print("  !! start 무응답 — 중단")
        return False
    time.sleep(dur + 1.5)                    # 이동 + 여유
    after = read_dispensed(pump)
    if before is None or after is None:
        print("  !! dispensed 판독 불가 — 중단")
        return False
    moved = abs(after - before)
    # 카운터가 리셋되는 펌웨어 대비: 절대값 자체가 이동량과 일치해도 인정
    if abs(moved - abs(delta)) > 0.05 and abs(abs(after) - abs(delta)) > 0.05:
        print(f"  !! 이동량 불일치: 목표 {abs(delta):.2f} vs 카운터 {before!r}→{after!r} — 중단")
        return False
    return True


def main():
    print(f"=== CH0(ID{PID}) 왕복 캘리브레이션 ===")
    print(f"시작 부피 {START_VOL} mL, 속도 {RATE} mL/min")
    sens = serial.Serial(SENS_PORT, 9600, timeout=0.5)
    pump = serial.Serial(PUMP_PORT, 9600, timeout=0.2)
    time.sleep(2.2)
    pts = []                                  # (vol, raw, leg)

    try:
        # 통신 사전 확인 + 초기 설정
        if not cmd(pump, f"{PID} pump status"):
            print("펌프 통신 무응답 — 시작 불가")
            return
        cmd(pump, f"{PID} set units 0")
        cmd(pump, f"{PID} set diameter {DIAMETER:g}")

        # 시작점 측정
        raw = ch0_median(sens)
        if raw is None:
            print("CH0 판독 실패 — 시작 불가")
            return
        pts.append((START_VOL, raw, "start"))
        print(f"  [start   ] vol {START_VOL:4.1f} mL  raw {raw:7.2f} cm")

        cur = START_VOL
        for leg, targets in (("up", UP_POINTS), ("down", DOWN_POINTS)):
            for tgt in targets:
                print(f"  이동 {cur:.1f} → {tgt:.1f} mL ...")
                if not move_to(pump, cur, tgt):
                    emergency_stop(pump)
                    print("  중단 — 부분 데이터 저장")
                    return
                cur = tgt
                raw = ch0_median(sens)
                if raw is None:
                    print("  !! CH0 판독 실패 — 중단")
                    emergency_stop(pump)
                    return
                pts.append((tgt, raw, leg))
                print(f"  [{leg:8s}] vol {tgt:4.1f} mL  raw {raw:7.2f} cm")

        print("\n=== 수집 완료 ===")
    finally:
        emergency_stop(pump)
        sens.close()
        pump.close()
        if pts:
            with open(CSV, "w", encoding="utf-8") as f:
                f.write("vol_mL,raw_cm,leg\n")
                for v, r, leg in pts:
                    f.write(f"{v},{r},{leg}\n")
            print(f"({len(pts)}점 저장: {CSV})")

    # ---- fit ----
    if len(pts) < 3:
        return
    xs = [r for _, r, _ in pts]
    ys = [v for v, _, _ in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    inter = my - slope * mx
    resid = [y - (slope * x + inter) for x, y in zip(xs, ys)]
    rmse = (sum(r * r for r in resid) / n) ** 0.5

    # 히스테리시스: 같은 부피의 up/down raw 차이
    up = {v: r for v, r, leg in pts if leg == "up"}
    dn = {v: r for v, r, leg in pts if leg == "down"}
    hyst = {v: abs(up[v] - dn[v]) for v in up.keys() & dn.keys()}

    print("\n=== FIT 결과 ===")
    print(f"vol_mL = {slope:.6f} * raw_cm + {inter:.4f}")
    print(f"RMSE = {rmse * 1000:.0f} uL   (점 {n}개)")
    for v, r, leg in pts:
        pred = slope * r + inter
        print(f"  vol {v:4.1f}  raw {r:7.2f}  예측 {pred:6.3f}  잔차 {(v - pred) * 1000:+5.0f} uL  [{leg}]")
    if hyst:
        wm = max(hyst, key=hyst.get)
        print(f"히스테리시스(같은 부피 up/down raw 차): 최대 {hyst[wm]:.3f} cm @ {wm} mL "
              f"(≈ {abs(slope) * hyst[wm] * 1000:.0f} uL)")
    print("\nhardware_config.json → Group A settings 권장값:")
    print(f'  "level_cal_slope": {slope:.6f},')
    print(f'  "level_cal_intercept": {inter:.4f},')
    print(f'  "level_samples": 20,')
    print(f'  "level_empty_gate_ul": {max(500.0, rmse * 1000 * 3):.0f}')


if __name__ == "__main__":
    main()
