# -*- coding: utf-8 -*-
"""장치 맵핑 진단 — hardware_config.json vs 실제 연결된 COM 포트.

장비 교체/재연결 후 "맵핑이 풀렸다" 할 때 실행:
    py -3.14 diagnose_mapping.py            # 진단만 (프로브 포함, 읽기전용 조회)
    py -3.14 diagnose_mapping.py --no-probe # 시리얼 프로브 없이 VID/PID 만

앱과 완전히 동일한 매칭 로직(core.utils.find_port_by_usb_info)을 사용하므로
여기서 MATCH 로 나오면 앱에서도 같은 포트로 붙는다.

판정:
  AUTO   VID/PID(+SN/프로브)로 자동 매칭됨 — 정상, port 값과 달라도 무관
  FBACK  자동 매칭 실패 → config 의 port 로 폴백 — 그 포트가 실존하면 요행, 없으면 연결 실패
  STATIC VID/PID 미등록 → port 하드코딩만 사용 (분취기 등) — COM 번호 바뀌면 즉사
  MISS   폴백/스태틱 포트가 현재 존재하지 않음 — ★이게 '맵핑 풀림'
"""
import sys, os, json, argparse

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import serial.tools.list_ports as lp
from core.utils import find_port_by_usb_info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-probe", action="store_true",
                    help="프로토콜 프로브 생략 (포트 열지 않음)")
    args = ap.parse_args()

    with open(os.path.join(HERE, "hardware_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    devices = cfg.get("inventory", [])
    roles = cfg.get("roles", {})

    live = sorted(lp.comports(), key=lambda p: (p.device[:3], int(p.device[3:] or 0)))
    print(f"=== 실제 연결 포트 {len(live)}개 ===")
    for p in live:
        vid = f"{p.vid:04X}" if p.vid else "----"
        pid = f"{p.pid:04X}" if p.pid else "----"
        print(f"  {p.device:7s} {vid}:{pid}  SN={p.serial_number or '-':14s} {p.description}")
    if not live:
        print("  (없음 — 장비 전원/USB 를 연결한 뒤 다시 실행하세요)")

    # 역할에서 실제 사용 중인 device_id 수집 (미사용 인벤토리와 구분)
    used_ids = set()
    def _walk(node):
        if isinstance(node, dict):
            for kk, vv in node.items():
                if kk in ("driver_id", "motor", "selector", "switcher", "sampler") \
                        and isinstance(vv, str):
                    used_ids.add(vv)
                else:
                    _walk(vv)
        elif isinstance(node, list):
            for vv in node:
                _walk(vv)
    _walk(roles)

    live_by_dev = {p.device: p for p in live}
    claimed = {}     # port -> [device names]
    rows = []
    for d in devices:
        name = d.get("name", d.get("id", "?"))
        vid, pid = d.get("vid"), d.get("pid")
        sn, probe = d.get("serial"), d.get("probe")
        fallback = d.get("port")
        in_use = d.get("id") in used_ids

        if vid and pid:
            auto = find_port_by_usb_info(
                vid, pid, sn, probe=None if args.no_probe else probe)
            if auto:
                status, port = "AUTO ", auto
            else:
                port = fallback
                status = "FBACK" if port in live_by_dev else "MISS "
        else:
            port = fallback
            status = "STATIC" if port in live_by_dev else "MISS "
            if port and "Mock" in str(port):
                status = "MOCK "
        if port and status.strip() not in ("MISS",):
            claimed.setdefault(port, []).append((name, d.get("driver", "?")))
        rows.append((status, name, d.get("id", "-"), port or "-",
                     f"{vid or '--'}:{pid or '--'}", sn or "-", probe or "-",
                     "역할사용" if in_use else "미사용"))

    print(f"\n=== 장치별 매핑 판정 (config {len(devices)}개) ===")
    print(f"  {'상태':5s} {'이름':<16s} {'해석포트':<8s} {'VID:PID':<10s} "
          f"{'SN':<14s} {'probe':<7s} 사용여부")
    problems = []
    for status, name, did, port, vp, sn, probe, use in rows:
        mark = "★" if status.strip() == "MISS" and use == "역할사용" else " "
        print(f" {mark}{status:5s} {name:<16s} {port:<8s} {vp:<10s} {sn:<14s} {probe:<7s} {use}")
        if status.strip() == "MISS" and use == "역할사용":
            problems.append((name, did, port, vp))

    # 중복 클레임 — 같은 드라이버끼리의 공유는 데이지체인/멀티채널 버스 = 정상.
    # 드라이버가 다른 장치가 한 포트를 가리킬 때만 오연결 경고.
    shared = {k: v for k, v in claimed.items() if len(v) > 1 and "Mock" not in k}
    for k, v in shared.items():
        drivers = {drv for _, drv in v}
        names = ", ".join(n for n, _ in v)
        if len(drivers) == 1:
            print(f"\n  [버스 공유(정상)] {k}: {names}")
        else:
            print(f"\n=== ⚠ 포트 중복 클레임 (드라이버 상이) ===")
            print(f"  {k}: {names} — 오연결 위험, 프로브/SN 확인 필요")

    # 미청구 라이브 포트 = 새 장비 후보
    unclaimed = [p for p in live if p.device not in claimed]
    if unclaimed:
        print("\n=== 어느 장치에도 배정 안 된 라이브 포트 (새 장비/바뀐 포트 후보) ===")
        for p in unclaimed:
            vid = f"{p.vid:04X}" if p.vid else "----"
            pid = f"{p.pid:04X}" if p.pid else "----"
            # 같은 VID:PID 를 쓰는 config 장치 후보 표시
            cands = [d.get("name") for d in devices
                     if d.get("vid") and f"{d['vid'].upper()}:{d['pid'].upper()}" == f"{vid}:{pid}"]
            hint = f"  ← config 후보: {', '.join(cands)}" if cands else ""
            print(f"  {p.device:7s} {vid}:{pid}  SN={p.serial_number or '-'}  "
                  f"{p.description}{hint}")

    if problems:
        print("\n=== ★ 조치 필요 ===")
        for name, did, port, vp in problems:
            print(f"  {name} ({did}): 저장 포트 {port} 이(가) 현재 없음.")
        print("  → 위 '미청구 라이브 포트'에서 새 포트를 찾아 hardware_config.json 의")
        print("    해당 장치 port 를 고치거나, VID/PID/SN 을 등록해 자동매칭으로 전환하세요.")
    elif live:
        print("\n모든 역할 사용 장치가 해석 가능 — 맵핑 정상.")


if __name__ == "__main__":
    main()
