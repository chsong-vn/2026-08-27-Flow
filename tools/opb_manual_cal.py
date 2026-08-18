# -*- coding: utf-8 -*-
"""OPB 위상센서 수동 하드웨어 캘리브레이션 — RoboChem platform_calibration.py 등가.

@codesyncer-decision(2026-08-18, 사용자 확정): 매런 자동 CAL(N2Precal 훅)은
  '비었음'을 캘리브 대상 센서 자신의 판정으로 보증하는 순환 논리라 유색액 위
  오발사로 보드 기준을 오염시켰다(센서2 상시-0 사고). RoboChem 원본처럼
  CAL 은 사람이 빈 라인을 육안 확인한 뒤에만 쏘는 수동 절차로 격하 — 이 도구가
  그 절차다: 육안 확인 → CAL 전 ADC 표집 → CAL 펄스 → CAL 후 ADC 표집 → 요약.

사용 (루트에서, ⚠앱 종료 필수 — COM 점유):
    py -3.14 tools\\opb_manual_cal.py            # 포트는 config dev_opb_1 에서
    py -3.14 tools\\opb_manual_cal.py COM19      # 포트 직접 지정
CAL 후에는 공기/물/유색 반응액 ADC 를 재실측해 thresholds 를 갱신할 것
(레벨맵이 이동했을 수 있음 — docs/위상센서_OPB_배선메모.md).
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial  # noqa: E402  (pyserial)

PAT = re.compile(r"S1:\s*(\d+)\s*,\s*(\d+)\s*\|\s*S2:\s*(\d+)\s*,\s*(\d+)")


def resolve_port():
    if len(sys.argv) > 1:
        return sys.argv[1]
    import json
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "hardware_config.json")
    with open(cfg, encoding="utf-8") as f:
        d = json.load(f)
    for dev in d.get("inventory", []):
        if dev.get("id") == "dev_opb_1":
            return dev.get("port", "COM19")
    return "COM19"


def sample(ser, sec=3.0):
    """sec 동안 스트림 표집 → 채널별 (mean, sd, min, max, n)."""
    acc = {1: [], 2: []}
    t0 = time.monotonic()
    while time.monotonic() - t0 < sec:
        line = ser.readline().decode("utf-8", errors="replace")
        m = PAT.search(line)
        if not m:
            continue
        acc[1].append(int(m.group(1)))
        acc[2].append(int(m.group(3)))
    out = {}
    for ch, vals in acc.items():
        if vals:
            mean = sum(vals) / len(vals)
            sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            out[ch] = (mean, sd, min(vals), max(vals), len(vals))
        else:
            out[ch] = None
    return out


def show(tag, st):
    for ch in (1, 2):
        s = st[ch]
        name = "센서1 INLET " if ch == 1 else "센서2 OUTLET"
        if s is None:
            print(f"  {tag} {name}: 표본 없음 (스트림/배선 확인)")
        else:
            print(f"  {tag} {name}: ADC {s[0]:6.1f} ±{s[1]:5.1f}  "
                  f"(범위 {s[2]}~{s[3]}, n={s[4]})")


def main():
    port = resolve_port()
    print(f"OPB 수동 캘리브레이션 — 포트 {port} @115200")
    print("⚠ 전제: 두 센서 관 모두 액체 없이 '공기'로 차 있어야 함 (RoboChem 계약)")
    try:
        ans = input("  두 센서 지점 튜브가 비어 있는지 육안으로 확인했습니까? [y/N] ")
    except EOFError:
        ans = ""
    if ans.strip().lower() not in ("y", "yes"):
        print("중단 — 라인을 비운 뒤(N2 배기 등) 다시 실행하세요.")
        return 1

    ser = serial.Serial(port, 115200, timeout=0.5)
    try:
        time.sleep(2.0)                       # 아두이노 리셋 대기
        ser.reset_input_buffer()
        print("\n[1/3] CAL 전 표집 (3s)…")
        before = sample(ser)
        show("전 ", before)

        print("\n[2/3] CAL 펄스 발사 (CAL1 + CAL2)…")
        for cmd in (b"CAL1\n", b"CAL2\n"):
            ser.write(cmd)
            ser.flush()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 2.0:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line.startswith("CAL:"):
                    print(f"  {cmd.decode().strip()} → {line}")
                    break
        time.sleep(1.0)                       # 보드 재영점 안정
        ser.reset_input_buffer()

        print("\n[3/3] CAL 후 표집 (3s)…")
        after = sample(ser)
        show("후 ", after)

        print("\n요약:")
        for ch in (1, 2):
            b, a = before[ch], after[ch]
            if b and a:
                print(f"  ch{ch - 1}: {b[0]:.0f} → {a[0]:.0f}  (Δ{a[0] - b[0]:+.0f})")
        print("\n다음 단계: 이 '공기' 값 기록 → 물/유색 반응액 흘리며 ADC 재실측 →")
        print("  hardware_config.json settings.thresholds 갱신 (배선메모 표 갱신 포함)")
        return 0
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main())
