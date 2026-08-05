# -*- coding: utf-8 -*-
"""펌프별 초음파 레벨센서 캘리브레이션 보조 — 채팅 주도 워크플로용.

test_ultrasonic_live.py 는 --cal 이 input() 기반이라 비대화 환경에서 못 돌고,
cp949 콘솔에서 em-dash 출력 시 크래시한다. 이 헬퍼는 두 문제를 우회한다:
  - stdout 을 UTF-8 로 강제(reconfigure)
  - 대화형 루프 없이 '한 번에 하나'의 원자적 명령만 제공(값은 사람이 넣고,
    측정은 이 스크립트가 production 드라이버 경로로 수행)

명령:
  py -3.14 level_cal_helper.py diag COM3          # 스트림 품질 진단(직결 serial, 5초)
  py -3.14 level_cal_helper.py raw COM3 [samples] # 드라이버 _read_raw median 1회(캘리브 1점)
"""
import statistics
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def diag(port, baud=9600, seconds=5.0):
    import serial
    ser = serial.Serial(port, baud, timeout=1.0)
    try:
        time.sleep(2.0)                 # UNO 오토리셋 부팅 대기
        ser.reset_input_buffer()
        t0 = time.time()
        total, vals, na, other = 0, [], 0, []
        while time.time() - t0 < seconds:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            total += 1
            if line == "NA":
                na += 1
                continue
            try:
                vals.append(float(line))
            except ValueError:
                if len(other) < 5:
                    other.append(line)
    finally:
        ser.close()

    print(f"port={port} baud={baud}  수신 {total}줄 / {seconds:.0f}초")
    print(f"  유효 float {len(vals)}개, NA {na}개, 기타 {total-len(vals)-na}개")
    if other:
        print(f"  기타 샘플(펌웨어 불일치 의심): {other}")
    if vals:
        med = statistics.median(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        print(f"  raw median {med:.2f} cm  (min {min(vals):.2f} / max {max(vals):.2f} / SD {sd:.3f})")
        rate = total / seconds
        ratio = len(vals) / total * 100.0 if total else 0.0
        ok = ratio >= 80.0 and rate >= 5.0 and sd <= 0.5
        print(f"  판정: {'PASS' if ok else 'CHECK'} (유효 {ratio:.0f}%, {rate:.1f}줄/초, SD {sd:.3f})")
    else:
        print("  판정: FAIL — 유효 거리값 0개 (펌웨어 미업로드 / 보레이트 / 배선 / 무반사)")


def raw(port, baud=9600, samples=20):
    """production 드라이버 _read_raw 경로로 median 1회 — 캘리브 1점."""
    from hardware.sensors.ultrasonic_level import UltrasonicLevelSensor, LevelSensorError
    CH = "cal"
    s = UltrasonicLevelSensor({CH: {"port": port, "baud": baud, "samples": samples}}, name="Cal")
    s.connect()
    time.sleep(2.0)
    try:
        try:
            r = s._read_raw(s.channels[CH])
        except LevelSensorError as e:
            print(f"RAW_FAIL {e}")
            return
        print(f"RAW {r:.3f}")
    finally:
        s.disconnect()


def diag4(port, baud=9600, seconds=5.0, n_ch=4):
    """4채널 CSV 스트림 진단 — 채널별 유효/NA/median 표시."""
    import serial
    ser = serial.Serial(port, baud, timeout=1.0)
    vals = [[] for _ in range(n_ch)]
    na = [0] * n_ch
    total = 0
    try:
        time.sleep(2.0)
        ser.reset_input_buffer()
        t0 = time.time()
        while time.time() - t0 < seconds:
            line = ser.readline().decode(errors="ignore").strip()
            if not line or "," not in line:
                continue
            cols = line.split(",")
            if len(cols) < n_ch:
                continue
            total += 1
            for i in range(n_ch):
                c = cols[i].strip()
                if c == "NA":
                    na[i] += 1
                    continue
                try:
                    vals[i].append(float(c))
                except ValueError:
                    pass
    finally:
        ser.close()
    print(f"port={port}  스윕 {total}줄 / {seconds:.0f}초")
    for i in range(n_ch):
        v = vals[i]
        if v:
            med = statistics.median(v)
            sd = statistics.pstdev(v) if len(v) > 1 else 0.0
            print(f"  CH{i}: 유효 {len(v):3}개  median {med:7.2f} cm  "
                  f"(min {min(v):.2f}/max {max(v):.2f}/SD {sd:.3f})  NA {na[i]}")
        else:
            print(f"  CH{i}: 유효 0개  NA {na[i]}  → 무반사/미배선/핀 확인")


def raw4(port, index, baud=9600, samples=20, n_ch=4):
    """4채널 CSV 에서 특정 채널(index)의 median 1회 — 캘리브 1점."""
    import serial
    from hardware.sensors.ultrasonic_level import _median, LevelSensorError
    ser = serial.Serial(port, baud, timeout=1.0)
    got = []
    attempts = 0
    max_attempts = samples * 4 + 10
    try:
        time.sleep(2.0)
        ser.reset_input_buffer()
        while len(got) < samples and attempts < max_attempts:
            attempts += 1
            line = ser.readline().decode(errors="ignore").strip()
            if not line or "," not in line:
                continue
            cols = line.split(",")
            if len(cols) <= index:
                continue
            c = cols[index].strip()
            if c == "NA":
                continue
            try:
                got.append(float(c))
            except ValueError:
                pass
    finally:
        ser.close()
    if len(got) < max(3, samples // 2):
        print(f"RAW_FAIL CH{index}: 유효표본 {len(got)}/{samples} — 판독 실패(무반사/핀)")
        return
    print(f"RAW {_median(got):.3f}")


def main():
    if len(sys.argv) < 3:
        print("용법: level_cal_helper.py diag|raw|diag4 <port> [samples]")
        print("      level_cal_helper.py raw4 <port> <index> [samples]")
        return
    cmd, port = sys.argv[1], sys.argv[2]
    if cmd == "diag":
        diag(port)
    elif cmd == "raw":
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        raw(port, samples=n)
    elif cmd == "diag4":
        diag4(port)
    elif cmd == "raw4":
        idx = int(sys.argv[3])
        n = int(sys.argv[4]) if len(sys.argv) > 4 else 20
        raw4(port, idx, samples=n)
    else:
        print(f"알 수 없는 명령: {cmd}")


if __name__ == "__main__":
    main()
