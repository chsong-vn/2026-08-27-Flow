# -*- coding: utf-8 -*-
"""레벨센서 4ch 실시간 스트림 — 손으로 가려서 전원/채널 확인용.

사용법:  py -3.14 live_level_stream.py [COM3] [초]
센서 앞 10~20cm 에 손바닥을 대고 값이 뜨는지 본다.
  값이 뜨면      → 그 센서 전원 정상 (반사물이 없었을 뿐)
  계속 NA 면     → 그 채널 전원/ECHO 배선 문제
"""
import sys, time
sys.stdout.reconfigure(encoding="utf-8")
import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

ser = serial.Serial(port, 9600, timeout=1.0)
time.sleep(2.0)
ser.reset_input_buffer()

print(f"=== {port} 실시간 스트림 {dur:.0f}초 ===")
print("센서 앞 10~20cm 에 손을 대보세요. 열 순서 = CH0(D2/D3) CH1(D4/D5) CH2(D6/D7) CH3(D8/D9)\n")
print(f"{'경과':>6}  {'CH0':>8} {'CH1':>8} {'CH2':>8} {'CH3':>8}   (cm)")
print("-" * 52)

t0 = time.time()
ever = [False] * 4     # 한 번이라도 값이 잡혔는가 = 전원 살아있다는 증거
best = [None] * 4
n = 0
while time.time() - t0 < dur:
    line = ser.readline().decode(errors="ignore").strip()
    if not line:
        continue
    parts = [x.strip() for x in line.split(",")]
    if len(parts) != 4:
        continue
    n += 1
    cells = []
    for i, tok in enumerate(parts):
        if tok.upper() == "NA":
            cells.append("NA")
        else:
            try:
                v = float(tok)
            except ValueError:
                cells.append("?")
                continue
            ever[i] = True
            if best[i] is None or v < best[i]:
                best[i] = v
            cells.append(f"{v:.1f}")
    if n % 3 == 0:   # ~3Hz 로 솎아서 출력
        el = time.time() - t0
        print(f"{el:6.1f}  " + " ".join(f"{c:>8}" for c in cells))
ser.close()

print("\n=== 판정 ===")
pin = ["D2/D3", "D4/D5", "D6/D7", "D8/D9"]
pump = [1, 2, 3, 4]
alive = []
for i in range(4):
    if ever[i]:
        alive.append(i)
        print(f"  CH{i} ({pin[i]}, 펌프{pump[i]}): [O] 에코 수신 — 전원/배선 정상 (최근접 {best[i]:.1f}cm)")
    else:
        print(f"  CH{i} ({pin[i]}, 펌프{pump[i]}): [X] {dur:.0f}초 내내 NA — 전원 또는 ECHO 배선 의심")
print(f"\n살아있는 채널 {len(alive)}/4 — {[f'CH{i}' for i in alive]}")
