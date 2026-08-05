"""
Chemyx 시린지펌프 RS-485 버스 스캔 (읽기 전용 — 펌프 미동작)

Chemyx_RS485_핀아웃_배선메모.md 의 "통신 확인 방법" 을 도구화한 것.
기계를 움직이는 명령은 절대 보내지 않는다 (pump status / view parameter 만).

사용법:
  py -3.14 pump_scan.py                  # CH340 자동탐색 + ID 1~4 스캔 + config 대조
  py -3.14 pump_scan.py --port COM8      # 포트 지정
  py -3.14 pump_scan.py --ids 1,2        # 특정 ID만
  py -3.14 pump_scan.py --repeat 3       # 3회 반복 (깨짐이 랜덤인지 고정인지 판별)

판정 규칙 (메모 기준):
  OK       — 깨끗한 ASCII 응답
  GARBLED  — 응답은 오는데 깨짐. **반복 시 매번 다르면 주소 중복(충돌)**,
             고정 패턴이면 신호 무결성(접촉/종단)
  SILENT   — 무응답. 단선 / 펌프 전원 OFF / 주소 미할당 / A-B 스왑

전원·배선 확인 순서는 메모 "★ 반드시 지킬 것" 참조.
"""

import argparse
import json
import os
import re
import sys
import time

import serial
import serial.tools.list_ports as list_ports

# Windows 콘솔 기본 cp949 → 문서용 기호(—, ★) 출력 시 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.utils import probe_chemyx, probe_runze  # noqa: E402

CH340_VID, CH340_PID = 0x1A86, 0x7523
BAUD = 9600
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hardware_config.json")

# 깨끗한 응답에 나타나도 되는 문자 (ASCII 출력 가능 + 개행 + 프롬프트)
_CLEAN_RE = re.compile(rb"^[\x20-\x7e\r\n]*$")


def list_ch340_ports():
    """CH340 후보 나열. Chemyx 버스와 Runze 버스가 같은 칩이라 VID/PID로는 구분 불가."""
    out = []
    for p in list_ports.comports():
        if p.vid == CH340_VID and p.pid == CH340_PID:
            out.append(p.device)
    return out


def autodetect_port():
    """CH340 후보를 프로브해서 Chemyx 버스를 찾는다 (Runze 버스는 제외)."""
    cands = list_ch340_ports()
    if not cands:
        return None, "CH340(1A86:7523) 포트 없음 — RS-485 어댑터 미연결"
    for com in cands:
        if probe_chemyx(com):
            return com, f"프로브 성공 (후보 {cands})"
    hints = []
    for com in cands:
        hints.append(f"{com}={'Runze' if probe_runze(com) else '무응답'}")
    return None, f"CH340는 있으나 Chemyx 무응답 — {', '.join(hints)}"


def query(ser, pump_id, cmd, wait=0.7):
    ser.reset_input_buffer()
    ser.write(f"{pump_id} {cmd}\r".encode())
    ser.flush()
    time.sleep(wait)
    return ser.read(ser.in_waiting or 1)


def classify(raw):
    if not raw:
        return "SILENT"
    return "OK" if _CLEAN_RE.match(raw) else "GARBLED"


def load_config_pumps():
    """roles.pumps → {pump_id: (name, diameter, capacity)}"""
    try:
        cfg = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for grp in cfg.get("roles", {}).get("pumps", []):
        st = grp.get("settings", {})
        pid = st.get("pump_id")
        if pid is not None:
            out[int(pid)] = (grp.get("name"), st.get("diameter"), st.get("capacity"))
    return out


def parse_diameter(text):
    m = re.search(r"dia[a-z]*\s*[:=]?\s*([\d.]+)", text, re.I)
    return float(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="지정 없으면 CH340 자동탐색")
    ap.add_argument("--ids", default="1,2,3,4")
    ap.add_argument("--repeat", type=int, default=1,
                    help="반복 스캔 — 깨짐이 매번 다르면 주소 중복")
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    cfg_pumps = load_config_pumps()

    port = args.port
    if not port:
        port, why = autodetect_port()
        print(f"[자동탐색] {why}")
        if not port:
            print("\n→ RS-485 어댑터(CH340) USB 연결 후 다시 실행하세요.")
            print("  연결돼 있는데 안 잡히면: 어댑터 485 모드 / A↔B 방향 / 펌프 전원 확인")
            print("  (배선은 Chemyx_RS485_핀아웃_배선메모.md 참조)")
            sys.exit(1)

    print(f"=== Chemyx 버스 스캔 — {port} @ {BAUD} 8N1 (읽기 전용) ===\n")

    try:
        ser = serial.Serial(port, BAUD, timeout=0.5)
    except Exception as e:
        print(f"포트 열기 실패: {type(e).__name__}: {e}")
        sys.exit(1)
    time.sleep(2.0)
    ser.read_all()

    history = {pid: [] for pid in ids}
    for rnd in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"--- 라운드 {rnd}/{args.repeat} ---")
        for pid in ids:
            raw = query(ser, pid, "pump status")
            verdict = classify(raw)
            history[pid].append(raw)
            shown = raw.decode("ascii", "replace").replace("\r", "\\r").replace("\n", "\\n")
            label = ""
            if pid in cfg_pumps:
                label = f"  [{cfg_pumps[pid][0]}]"
            print(f"  ID{pid}{label:<12} {verdict:<8} {shown!r}")
        if args.repeat > 1:
            print()

    # ── 주소 중복 판별: GARBLED 가 라운드마다 다른 바이트면 충돌 ──
    if args.repeat > 1:
        print("[반복 판정]")
        for pid in ids:
            hs = history[pid]
            if all(classify(h) == "SILENT" for h in hs):
                continue
            if any(classify(h) == "GARBLED" for h in hs):
                uniq = len(set(hs))
                if uniq > 1:
                    print(f"  ID{pid}: 깨짐이 매번 다름({uniq}종) → **주소 중복(RS-485 충돌) 유력**")
                    print(f"          앞판넬에서 중복 ID 확인 후 저장, 전원 재투입해 재스캔")
                else:
                    print(f"  ID{pid}: 깨짐이 고정 → 신호 무결성(접촉/종단/케이블)")
        print()

    # ── config 대조 (diameter 실제값 vs 설정값) ──
    print("[config 대조 — view parameter]")
    for pid in ids:
        if classify(history[pid][-1]) != "OK":
            continue
        raw = query(ser, pid, "view parameter", wait=0.8)
        text = raw.decode("ascii", "replace")
        dia = parse_diameter(text)
        if pid in cfg_pumps:
            name, cfg_dia, cap = cfg_pumps[pid]
            if dia is None:
                print(f"  ID{pid} [{name}]: diameter 파싱 실패 — 원문 {text!r}")
            elif cfg_dia is not None and abs(dia - float(cfg_dia)) > 0.01:
                print(f"  ID{pid} [{name}]: ⚠ 펌프={dia}mm vs config={cfg_dia}mm 불일치 "
                      f"(용량 {cap}mL) — 유량 오차 직결")
            else:
                print(f"  ID{pid} [{name}]: diameter {dia}mm 일치 (용량 {cap}mL)")
        else:
            print(f"  ID{pid}: config에 없는 펌프 — diameter {dia}mm")

    ser.close()
    print("\n완료 (펌프 미동작)")


if __name__ == "__main__":
    main()
