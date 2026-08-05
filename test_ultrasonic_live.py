# -*- coding: utf-8 -*-
"""초음파 레벨센서(HC-SR04 + Arduino UNO) 실기 테스트.

이식한 hardware/sensors/ultrasonic_level.py 드라이버의 '실제 코드 경로'
(_read_raw 20샘플 median -> 선형 캘리브 -> get_volume) 를 그대로 통과시킨다.
펌웨어: hardware/sensors/firmware/level_sensor_hcsr04/level_sensor_hcsr04.ino

용법:
  py -3.14 test_ultrasonic_live.py                  # 연결된 COM 포트 나열
  py -3.14 test_ultrasonic_live.py COM5             # 1회 진단 (스트림 품질 + 드라이버 판독)
  py -3.14 test_ultrasonic_live.py COM5 --watch     # 연속 판독 (Ctrl+C 중단)
  py -3.14 test_ultrasonic_live.py COM5 --cal       # 다점 캘리브레이션 -> slope/intercept 산출
  py -3.14 test_ultrasonic_live.py COM5 --slope -0.55 --intercept 12.3
                                                    # 캘리브 적용 부피(uL) 확인

진단 합격 기준:
  1) 스트림: 유효 float 비율 >= 80%, 표본율 >= 5줄/초 (펌웨어 10 Hz 기준)
  2) 노이즈: 20샘플 표준편차가 HC-SR04 사양(+-0.3 cm) 수준
  3) 드라이버: get_volume() 예외 없이 값 반환 (slope 미지정 시 raw*1000)
"""
import argparse
import statistics
import sys
import time

from hardware.sensors.ultrasonic_level import UltrasonicLevelSensor, LevelSensorError

CH = "TestPump"   # 채널명은 임의 — 드라이버 계약상 펌프명 키일 뿐


def list_ports():
    from serial.tools import list_ports as lp
    ports = sorted(lp.comports(), key=lambda p: p.device)
    if not ports:
        print("연결된 COM 포트 없음 — UNO USB 연결/드라이버(CH340 등) 확인")
        return
    print("연결된 COM 포트:")
    for p in ports:
        print(f"  {p.device:8s} {p.description}")


def stream_check(port, baud, seconds=5.0):
    """스테이지 1: 원시 스트림 품질 — 펌웨어가 계약대로 말하는지."""
    import serial
    print(f"\n[1/2] 원시 스트림 검사 ({seconds:.0f}초) @ {port} {baud}bps")
    ser = serial.Serial(port, baud, timeout=1.0)
    try:
        time.sleep(2.0)                    # UNO 는 포트 열릴 때 오토리셋 -> 부팅 대기
        ser.reset_input_buffer()
        t0 = time.time()
        total, vals, na = 0, [], 0
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
                pass
    finally:
        ser.close()

    if total == 0:
        print("  FAIL  수신 0줄 — 펌웨어 미업로드 / 보레이트 불일치 / UNO 자체 확인")
        return False
    if na and not vals:
        print(f"  FAIL  펌웨어 정상 동작(NA {na}줄) but 에코 없음 — "
              "HC-SR04 배선 확인: VCC->5V, GND->GND, TRIG->D9, ECHO->D10, "
              "그리고 전방 2~400cm 에 반사면")
        return False
    if na:
        print(f"  NOTE  타임아웃 NA {na}줄 ({na/total*100:.0f}%) — 간헐 무반사")
    rate = total / seconds
    ratio = len(vals) / total * 100.0
    print(f"  수신 {total}줄 ({rate:.1f}줄/초), 유효 float {len(vals)}개 ({ratio:.0f}%)")
    if vals:
        med = statistics.median(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        print(f"  raw 거리: median {med:.2f} / min {min(vals):.2f} / "
              f"max {max(vals):.2f} / 표준편차 {sd:.3f}")
        if sd > 0.5:
            print("  WARN  노이즈 큼(표준편차 > 0.5) — 반사블록 정렬/고정, 주변 반사면 확인")
    ok = ratio >= 80.0 and rate >= 5.0
    print(f"  {'PASS' if ok else 'FAIL'}  (기준: 유효 80% 이상, 5줄/초 이상)")
    return ok


def make_sensor(port, baud, slope, intercept, samples):
    s = UltrasonicLevelSensor({CH: {
        "port": port, "baud": baud,
        "slope": slope, "intercept": intercept, "samples": samples,
    }}, name="LiveTest")
    s.connect()
    time.sleep(2.0)                        # UNO 오토리셋 부팅 대기
    return s


def measure_raw(sensor):
    """드라이버 내부 median 경로 그대로 raw 판독 (캘리브 전 값)."""
    return sensor._read_raw(sensor.channels[CH])


def driver_check(port, baud, slope, intercept, samples):
    """스테이지 2: 이식 드라이버 실경로 (connect -> get_volume)."""
    print(f"\n[2/2] 드라이버 경로 검사 (samples={samples}, "
          f"slope={slope}, intercept={intercept})")
    s = make_sensor(port, baud, slope, intercept, samples)
    try:
        t0 = time.time()
        raw = measure_raw(s)
        try:
            vol = s.get_volume(CH)
        except LevelSensorError as e:
            print(f"  raw median = {raw:.2f}  ({time.time()-t0:.1f}초)")
            print(f"  FAIL  get_volume: {e}")
            return False
        dt = time.time() - t0
        print(f"  raw median = {raw:.2f}  ->  volume = {vol:.0f} uL  ({dt:.1f}초)")
        if slope == 1.0 and intercept == 0.0:
            print("  NOTE  캘리브 미적용(기본 slope=1) — 부피값은 raw*1000. "
                  "--cal 로 slope/intercept 산출할 것")
        print("  PASS  드라이버 경로 정상")
        return True
    except LevelSensorError as e:
        print(f"  FAIL  {e}")
        return False
    finally:
        s.disconnect()


def watch(port, baud, slope, intercept, samples, interval=1.0):
    print(f"\n연속 판독 — Ctrl+C 로 중단 (측정당 약 {samples/10.0:.0f}초 소요)")
    s = make_sensor(port, baud, slope, intercept, samples)
    try:
        while True:
            try:
                raw = measure_raw(s)
                if slope != 1.0 or intercept != 0.0:
                    vol_ml = slope * raw + intercept
                    print(f"  raw {raw:8.2f}   ->  {max(0.0, vol_ml)*1000:8.0f} uL")
                else:
                    print(f"  raw {raw:8.2f}")
            except LevelSensorError as e:
                print(f"  판독 실패: {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n중단")
    finally:
        s.disconnect()


def calibrate(port, baud, samples):
    """다점 캘리브레이션: 알려진 부피에 플런저를 두고 raw 를 수집 -> 최소제곱 fit.

    vol_mL = slope * raw + intercept  (드라이버 get_volume 과 동일한 모델)
    권장: 최소 2점 (empty=0 mL, full=시린지 용량), 3점 이상이면 선형성도 확인.
    """
    print("\n캘리브레이션 모드 — 각 점: 시린지를 알려진 부피로 맞춘 뒤 부피(mL) 입력")
    print("빈 입력(Enter만) = 수집 종료 후 fit\n")
    s = make_sensor(port, baud, 1.0, 0.0, samples)
    pts = []
    try:
        while True:
            txt = input(f"점 {len(pts)+1} — 현재 부피 mL (Enter=종료): ").strip()
            if not txt:
                break
            try:
                vol = float(txt)
            except ValueError:
                print("  숫자가 아님 — 다시")
                continue
            print("  측정 중...")
            try:
                raw = measure_raw(s)
            except LevelSensorError as e:
                print(f"  측정 실패: {e} — 다시")
                continue
            pts.append((raw, vol))
            print(f"  기록: raw {raw:.2f}  =  {vol:g} mL")
    finally:
        s.disconnect()

    if len(pts) < 2:
        print("점 2개 미만 — fit 불가")
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        print("raw 가 전부 동일 — 플런저 위치가 안 바뀜(반사블록 부착 확인)")
        return
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    intercept = my - slope * mx
    resid = [y - (slope * x + intercept) for x, y in pts]
    rmse = (sum(r * r for r in resid) / n) ** 0.5
    print(f"\nfit 결과 ({n}점):  vol_mL = {slope:.6f} * raw + {intercept:.4f}")
    print(f"잔차 RMSE = {rmse*1000:.0f} uL  (HC-SR04 특성상 수백 uL 는 정상)")
    print("\nhardware_config.json 해당 펌프 settings 에 넣을 값:")
    print(f'  "level_cal_slope": {slope:.6f},')
    print(f'  "level_cal_intercept": {intercept:.4f},')
    print(f'  "level_samples": {samples},')
    print(f'  "level_empty_gate_ul": {max(500.0, rmse*1000*3):.0f}   '
          f"(RMSE x3 이상 권장 — gate 가 노이즈보다 커야 함)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", help="COM 포트 (생략 시 포트 나열)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--slope", type=float, default=1.0)
    ap.add_argument("--intercept", type=float, default=0.0)
    ap.add_argument("--watch", action="store_true", help="연속 판독")
    ap.add_argument("--cal", action="store_true", help="다점 캘리브레이션")
    a = ap.parse_args()

    if not a.port:
        list_ports()
        return

    if a.cal:
        calibrate(a.port, a.baud, a.samples)
        return
    if a.watch:
        watch(a.port, a.baud, a.slope, a.intercept, a.samples)
        return

    ok1 = stream_check(a.port, a.baud)
    if not ok1:
        sys.exit(1)
    ok2 = driver_check(a.port, a.baud, a.slope, a.intercept, a.samples)
    sys.exit(0 if ok2 else 1)


if __name__ == "__main__":
    main()
