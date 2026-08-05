# -*- coding: utf-8 -*-
"""초음파 레벨센서 4ch 동작 확인 — level_sensor_hcsr04_4ch.ino 계약 검증.

프로토콜: 9600bps, 매 스윕 "d0,d1,d2,d3\n" (cm, 2자리), 무반사 채널 = "NA".
CH0 캘리브레이션(config): slope=1.06776, intercept=-9.2382 (mL = slope*cm + intercept)
"""
import sys, time, statistics
sys.stdout.reconfigure(encoding="utf-8")
import serial
import serial.tools.list_ports as lp

SLOPE, INTERCEPT = 1.06776, -9.2382   # CH0 (Group A) 실측 캘리브레이션
N_LINES = 40                           # ~4초 (10Hz 스윕)

# ── 포트 탐색 ────────────────────────────────────────────────
ports = list(lp.comports())
print("=== 연결된 시리얼 포트 ===")
for p in ports:
    print(f"  {p.device} | {p.description} | {p.hwid}")

target = sys.argv[1] if len(sys.argv) > 1 else None
if not target:
    # config 상 UNO = VID 2341 / PID 0043. 클론(CH340)도 있을 수 있으나
    # COM6 은 펌프 RS-485 어댑터로 확정됐으므로 제외한다.
    cand = [p for p in ports if "2341" in (p.hwid or "").upper().replace("VID:PID=", "")
            or "ARDUINO" in (p.description or "").upper()]
    cand = [p for p in cand if p.device != "COM6"]
    if not cand:
        print("\n[X] 레벨센서 아두이노(UNO)를 찾지 못했습니다.")
        print("    config 등록: COM3 / VID 2341 PID 0043 / 9600bps")
        print("    → USB 를 꽂고 다시 실행하거나, 포트를 인자로 주세요:  py -3.14 check_level4ch.py COM3")
        sys.exit(2)
    target = cand[0].device

print(f"\n=== {target} 9600bps 판독 ({N_LINES}줄) ===")
ser = serial.Serial(target, 9600, timeout=1.0)
time.sleep(2.0)          # UNO 자동 리셋 대기
ser.reset_input_buffer()

raw_lines, cols = [], [[], [], [], []]
na = [0, 0, 0, 0]
bad_format = 0
t0 = time.time()
while len(raw_lines) < N_LINES and time.time() - t0 < 20:
    line = ser.readline().decode(errors="ignore").strip()
    if not line:
        continue
    raw_lines.append(line)
    parts = [x.strip() for x in line.split(",")]
    if len(parts) != 4:
        bad_format += 1
        continue
    for i, tok in enumerate(parts):
        if tok.upper() == "NA":
            na[i] += 1
        else:
            try:
                cols[i].append(float(tok))
            except ValueError:
                bad_format += 1
ser.close()

print("  샘플 원문 5줄:")
for l in raw_lines[:5]:
    print(f"    {l!r}")
print(f"  수신 {len(raw_lines)}줄 / 형식오류 {bad_format}")

if not raw_lines:
    print("\n[X] 데이터 수신 없음 — 펌웨어 미업로드 또는 보율 불일치 의심")
    sys.exit(1)

print(f"\n{'CH':<4}{'유효/전체':<12}{'NA':<6}{'중앙값 cm':<12}{'min~max cm':<18}{'표준편차':<10}{'판정'}")
print("-" * 82)
ok_ch = []
for i in range(4):
    v, n = cols[i], na[i]
    tot = len(v) + n
    if not v:
        print(f"CH{i:<3}{0}/{tot:<10}{n:<6}{'-':<12}{'-':<18}{'-':<10}[X] 전부 NA (미배선/무반사)")
        continue
    med = statistics.median(v)
    sd = statistics.pstdev(v) if len(v) > 1 else 0.0
    rate = len(v) / tot if tot else 0
    if rate < 0.5:
        verdict = "[X] 유효율 50% 미만 — 드라이버 판독 실패 조건"
    elif sd > 1.0:
        verdict = f"[!] 산포 큼 (±{sd:.2f}cm) — 반사블록/정렬 확인"
    else:
        verdict = "[O] 정상"
        ok_ch.append(i)
    print(f"CH{i:<3}{len(v)}/{tot:<10}{n:<6}{med:<12.2f}{f'{min(v):.2f}~{max(v):.2f}':<18}{sd:<10.3f}{verdict}")

print(f"\n=== CH0 부피 환산 (slope={SLOPE}, intercept={INTERCEPT}) ===")
if cols[0]:
    med0 = statistics.median(cols[0])
    ml = SLOPE * med0 + INTERCEPT
    print(f"  raw {med0:.2f}cm → {ml:.3f}mL ({max(0.0, ml)*1000:.0f}µL)")
    if ml < -0.2:
        print("  [X] 음수 — 드라이버가 LevelSensorError 발생시키는 구간(마운트/부호 오류)")
else:
    print("  CH0 무효 — 환산 불가")

print(f"\nRESULT: 정상 채널 {['CH%d' % i for i in ok_ch]} ({len(ok_ch)}/4)")
