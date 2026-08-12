# -*- coding: utf-8 -*-
"""CH340 포트 실체 분류 — 각 포트에 실제로 뭐가 응답하는지 프로브로 확인.

앱을 '닫고' 실행하세요 (앱이 켜져 있으면 포트를 점유해 PermissionError).
읽기 전용 조회만 보냄 → 어떤 장비도 움직이지 않습니다.

용도: Chemyx/Runze 가 같은 CH340(1A86:7523, 시리얼 없음)을 공유해 COM 번호가
     바뀌면 무엇이 어느 포트인지 헷갈릴 때, 각 포트에 chemyx/runze/reaxus 조회를
     실제로 보내 '누가 답하는지'로 확정한다.

    py -3.14 tools/classify_ch340.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import serial.tools.list_ports as lp
from core.utils import probe_chemyx, probe_runze, probe_reaxus

PROBES = [("chemyx", probe_chemyx), ("runze", probe_runze), ("reaxus", probe_reaxus)]


def main():
    ports = list(lp.comports())
    if not ports:
        print("연결된 COM 포트가 0개입니다 — 장비 전원/USB 를 연결한 뒤 다시 실행하세요.")
        return 0

    print(f"=== 연결 포트 {len(ports)}개 ===")
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        pid = f"{p.pid:04X}" if p.pid else "----"
        sn = p.serial_number or "-"
        print(f"  {p.device:8s} {vid}:{pid} SN={sn:16s} {p.description}")

    # CH340(1A86:7523) 포트만 실제 프로브 (다른 포트는 VID/PID 로 이미 식별됨)
    ch340 = [p for p in ports if p.vid == 0x1A86 and p.pid == 0x7523]
    print(f"\n=== CH340(1A86:7523) 포트 실체 확인 — {len(ch340)}개 ===")
    if not ch340:
        print("  CH340 포트 없음 (Chemyx/Runze 어댑터 미연결)")
    result = {}
    for p in ch340:
        hits = []
        for name, fn in PROBES:
            try:
                ok = fn(p.device)
            except Exception as e:
                ok = False
                print(f"  {p.device}: {name} 프로브 예외 {e}")
            if ok:
                hits.append(name)
        if not hits:
            verdict = "무응답 (전원 OFF? 케이블? 버스 한 대 OFF로 침묵?)"
        elif len(hits) == 1:
            verdict = f"→ {hits[0].upper()}"
        else:
            verdict = f"⚠ 다중응답 {hits} (프로브 충돌 — 재실행 권장)"
        result[p.device] = hits
        print(f"  {p.device:8s} {verdict}")

    # 요약 + config 대조 힌트
    print("\n=== 판정 요약 ===")
    chemyx_ports = [d for d, h in result.items() if h == ["chemyx"]]
    runze_ports = [d for d, h in result.items() if h == ["runze"]]
    print(f"  Chemyx 버스 : {chemyx_ports or '없음 (미연결/무응답)'}")
    print(f"  Runze  버스 : {runze_ports or '없음 (미연결/무응답)'}")
    print("\n  → 위 포트를 hardware_config.json 의 해당 장치 'port' 에 반영하거나,")
    print("    Claude 에게 이 출력을 주면 config 를 맞춰 고칩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
