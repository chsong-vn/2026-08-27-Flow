# -*- coding: utf-8 -*-
"""CH0 저속 연속 스윕 프로파일 — 초음파 간섭원 위치 규명.

ID1 펌프를 0→5 mL 저속(2 mL/min) withdraw 하면서 CH0 raw 를 연속 기록.
끝나면 10 mL/min 으로 0 mL 복귀. 결과:
  - ch0_sweep.csv (t_s, vol_est_mL, raw_cm)
  - 콘솔: 0.25 mL 구간별 median raw + 점프/플래토 자동 검출
플래토의 raw 값(cm) = 고정 간섭물까지의 거리 → 센서 정면에서 그 거리를 재면
무엇이 간섭하는지 물리적으로 특정 가능.
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
SWEEP_ML = 5.0
SWEEP_RATE = 2.0        # mL/min (저속 — 프로파일 해상도)
RETURN_RATE = 10.0
CSV = "ch0_sweep.csv"


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


def read_dispensed(pump):
    r = cmd(pump, f"{PID} dispensed volume")
    for tok in r.replace("=", " ").split():
        try:
            return float(tok)
        except ValueError:
            continue
    return None


def main():
    print(f"=== CH0 저속 스윕: 0→{SWEEP_ML} mL @ {SWEEP_RATE} mL/min ===")
    sens = serial.Serial(SENS_PORT, 9600, timeout=0.5)
    pump = serial.Serial(PUMP_PORT, 9600, timeout=0.2)
    time.sleep(2.2)
    rows = []
    try:
        if not cmd(pump, f"{PID} pump status"):
            print("펌프 통신 무응답 — 중단")
            return
        cmd(pump, f"{PID} set units 0")
        cmd(pump, f"{PID} set diameter {DIAMETER:g}")
        d0 = read_dispensed(pump)
        if not cmd(pump, f"{PID} set rate {SWEEP_RATE:g}") or \
           not cmd(pump, f"{PID} set volume -{SWEEP_ML:g}"):
            print("set 무응답 — 중단")
            return
        if not cmd(pump, f"{PID} start", retries=1):
            print("start 무응답 — 중단")
            return

        dur = SWEEP_ML / SWEEP_RATE * 60.0
        t0 = time.time()
        sens.reset_input_buffer()
        last_print = 0.0
        while time.time() - t0 < dur + 2.0:
            line = sens.readline().decode(errors="ignore").strip()
            if "," not in line:
                continue
            c = line.split(",")[0].strip()
            if c == "NA":
                continue
            try:
                raw = float(c)
            except ValueError:
                continue
            t = time.time() - t0
            vol = min(t * SWEEP_RATE / 60.0, SWEEP_ML)
            rows.append((t, vol, raw))
            if t - last_print >= 10.0:
                print(f"  t={t:5.0f}s  vol≈{vol:4.2f} mL  raw={raw:6.2f} cm")
                last_print = t

        stop3(pump)
        d1 = read_dispensed(pump)
        if d0 is not None and d1 is not None:
            print(f"withdraw 실이동: {abs(d1 - d0):.3f} mL (목표 {SWEEP_ML})")

        # 복귀
        print(f"0 mL 복귀 중 ({RETURN_RATE} mL/min)...")
        cmd(pump, f"{PID} set rate {RETURN_RATE:g}")
        cmd(pump, f"{PID} set volume {SWEEP_ML:g}")
        cmd(pump, f"{PID} start", retries=1)
        time.sleep(SWEEP_ML / RETURN_RATE * 60.0 + 2.0)
        stop3(pump)
        d2 = read_dispensed(pump)
        if d1 is not None and d2 is not None:
            print(f"복귀 실이동: {abs(d2 - d1):.3f} mL")
    finally:
        stop3(pump)
        sens.close()
        pump.close()
        if rows:
            with open(CSV, "w", encoding="utf-8") as f:
                f.write("t_s,vol_est_mL,raw_cm\n")
                for t, v, r in rows:
                    f.write(f"{t:.2f},{v:.4f},{r}\n")
            print(f"({len(rows)}샘플 저장: {CSV})")

    if len(rows) < 20:
        return

    # ---- 0.25 mL 구간별 median ----
    print("\n=== 부피별 raw (0.25 mL 구간 median) ===")
    bins = {}
    for _, v, r in rows:
        bins.setdefault(round(v / 0.25) * 0.25, []).append(r)
    prev = None
    for k in sorted(bins):
        med = statistics.median(bins[k])
        bar = "#" * max(0, int((med - 7.0) * 8))
        note = ""
        if prev is not None:
            d = med - prev
            if abs(d) > 0.5:
                note = f"  << 점프 {d:+.2f}"
        print(f"  {k:4.2f} mL  {med:6.2f} cm  {bar}{note}")
        prev = med

    # ---- 플래토 검출 (부피는 진행하는데 raw 정체) ----
    print("\n=== 플래토(고정물 의심) ===")
    meds = [(k, statistics.median(bins[k])) for k in sorted(bins)]
    run_start = None
    for i in range(1, len(meds)):
        flat = abs(meds[i][1] - meds[i - 1][1]) < 0.12
        if flat and run_start is None:
            run_start = i - 1
        if (not flat or i == len(meds) - 1) and run_start is not None:
            end = i if flat else i - 1
            span = meds[end][0] - meds[run_start][0]
            if span >= 0.5:
                level = statistics.median([m for _, m in meds[run_start:end + 1]])
                print(f"  vol {meds[run_start][0]:.2f}~{meds[end][0]:.2f} mL 에서 "
                      f"raw ≈ {level:.2f} cm 정체 → 센서 정면 {level:.1f} cm 지점 확인!")
            run_start = None
    print("\n(플래토가 없으면 전 구간 추적 정상 — 간섭물 제거된 것)")


if __name__ == "__main__":
    main()
