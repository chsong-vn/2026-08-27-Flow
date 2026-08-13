# -*- coding: utf-8 -*-
"""데드볼륨 실측 '대화 구동' 서버 — Claude 가 파일 명령으로 한 스텝씩 구동.

배경: 대화형 콘솔 대신, 하드웨어 연결을 유지한 채 명령 파일을 폴링해
한 동작씩 실행한다 (운전자는 리그에서 선단을 육안 확인, 명령은 Claude 가 발행).
명령 사이에는 펌프가 항상 정지 상태 — 세션이 끊겨도 유체는 멈춰 있다.

명령 파일: logs/calib_cmd.json  {"id": N, "action": "...", ...}
  id 가 직전 처리분보다 클 때만 실행 (중복/재읽기 무해).
응답 로그: logs/calib_session.log  한 줄씩 append —
  RSP <id> <ok|err> <상세> | cum=<주입누적> vol=<시린지잔량> phase=<센서>

액션 (부피 mL, 유량 mL/min):
  {"action":"charge",  "ml":1.0}            시린지 충전 — refill 로직(밸브 왕복 포함)
  {"action":"withdraw","ml":0.05,"rate":1.7} 밸브 그대로 두고 잘게 흡인 (wcum 가산 —
                                            흡인경로 구간 실측용. 밸브는 valves 로 먼저 정렬)
  {"action":"expel",   "ml":2.3, "rate":2}  포트12 폐기 배출 (3way=SOURCE+포트12)
  {"action":"push",    "ml":0.02,"rate":0.5} 정속 INFUSE (측정 밀기, cum 가산)
  {"action":"valves",  "selector":2, "switcher":2}   밸브 정렬 (생략 키는 무변경)
  {"action":"outlet",  "pos":1}             아웃렛 3way (1=WASTE, 2=COLLECT)
  {"action":"zero"}                         측정 0점 (cum=0 — 3way REACTOR 전환 직후)
  {"action":"mark",    "label":"QUAD1"}     현재 cum 을 랜드마크로 기록
  {"action":"status"}                       상태만 보고
  {"action":"blow",    "sccm":20, "sec":60} N2 블로우
  {"action":"stop"}                         펌프·MFC 즉시 정지
  {"action":"quit"}                         종료 (정지+정리)

실행: py -3.14 tools\calib_server.py --channel "Group A" --source-port 2
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMD = os.path.join(ROOT, "logs", "calib_cmd.json")
LOG = os.path.join(ROOT, "logs", "calib_session.log")

from tools.calibrate_deadvol import (_load_live, push_bounded, charge_syringe,
                                     expel_to_waste, run_blowout)


def log_line(txt):
    line = f"{time.strftime('%H:%M:%S')} {txt}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="Group A")
    ap.add_argument("--source-port", type=int, default=2)
    args = ap.parse_args()

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    # 구세션 명령 파일 제거 — 오래된 id 재실행 방지
    try:
        os.remove(CMD)
    except OSError:
        pass

    log_line(f"BOOT 채널={args.channel} 소스포트={args.source_port} — 하드웨어 연결중")
    cfg, hw, pump, sensor, mfc = _load_live(args.channel, args.source_port)
    smart = hasattr(pump, "refill") and hasattr(pump, "current_vol")
    mock = "mock" in type(pump).__name__.lower()

    def phase():
        try:
            return sensor.read_phase("collect") if sensor is not None else "-"
        except Exception:
            return "?"

    def state():
        v = float(getattr(pump, "current_vol", -1.0) or 0.0) if smart else -1.0
        return (f"cum={S['cum']:.4f} wcum={S['wcum']:.4f} "
                f"vol={v:.3f} phase={phase()}")

    S = {"cum": 0.0, "wcum": 0.0, "last_id": 0}
    log_line(f"READY pump={type(pump).__name__}{' ⚠MOCK-가짜측정위험' if mock else ''} "
             f"sensor={'유' if sensor else '무'} mfc={'유' if mfc else '무'} | {state()}")

    try:
        while True:
            time.sleep(0.3)
            try:
                with open(CMD, encoding="utf-8") as f:
                    cmd = json.load(f)
            except (OSError, ValueError):
                continue
            cid = int(cmd.get("id", 0) or 0)
            if cid <= S["last_id"]:
                continue
            S["last_id"] = cid
            act = str(cmd.get("action", "")).lower()
            try:
                if act == "charge":
                    ml = float(cmd.get("ml", 1.0))
                    got = charge_syringe(pump, args.source_port, ml)
                    log_line(f"RSP {cid} ok charge {got:.3f}mL (3way=REACTOR 복원) | {state()}")
                elif act == "expel":
                    ml = float(cmd.get("ml", 1.0))
                    rate = float(cmd.get("rate", 2.0))
                    out = expel_to_waste(pump, ml, rate)
                    log_line(f"RSP {cid} ok expel {out:.3f}mL→포트12 (3way=SOURCE 상태) | {state()}")
                elif act == "withdraw":
                    # 밸브 무개입 잘게 흡인 — 엔진 refill 과 동일한 검증형 시퀀스
                    # (prepare→start→동작확인→대기→stop), 밸브만 안 건드림.
                    ml = float(cmd.get("ml", 0.05))
                    rate = float(cmd.get("rate",
                                         getattr(pump, "refill_rate", 1.7) or 1.7))
                    if not smart:
                        log_line(f"RSP {cid} err withdraw — 스마트펌프 아님 | {state()}")
                        continue
                    cap = float(pump.capacity)
                    if float(pump.current_vol) + ml > cap + 1e-9:
                        log_line(f"RSP {cid} err withdraw 거부 — 용량 초과 "
                                 f"({float(pump.current_vol):.3f}+{ml:.3f}>{cap:.1f}) | {state()}")
                        continue
                    with pump.lock:
                        pump.prepare_parameters(rate, -ml, "CalWithdraw")
                    pump._start_with_retry("CalWithdraw")
                    pump._verify_running("CalWithdraw", volume=ml, rate=rate)
                    pump._wait_pump_done(ml, rate, max_multiplier=2.0)
                    pump.driver.stop()
                    pump.current_vol = min(cap, float(pump.current_vol) + ml)
                    S["wcum"] += ml
                    log_line(f"RSP {cid} ok withdraw +{ml:.4f}mL | {state()}")
                elif act == "push":
                    ml = float(cmd.get("ml", 0.01))
                    rate = float(cmd.get("rate", 0.5))
                    if smart and float(pump.current_vol) < ml - 1e-9:
                        log_line(f"RSP {cid} err push 거부 — 시린지 잔량 "
                                 f"{float(pump.current_vol):.3f} < {ml:.3f} (charge 필요) | {state()}")
                        continue
                    dv = push_bounded(rate, ml, pump)
                    S["cum"] += dv
                    if smart:
                        pump.current_vol = max(0.0, float(pump.current_vol) - dv)
                    log_line(f"RSP {cid} ok push +{dv:.4f}mL | {state()}")
                elif act == "valves":
                    sel = cmd.get("selector")
                    sw = cmd.get("switcher")
                    if hasattr(pump, "set_valves_safe"):
                        pump.set_valves_safe(
                            selector_port=(int(sel) if sel is not None else None),
                            switcher_pos=(int(sw) if sw is not None else None))
                        log_line(f"RSP {cid} ok valves sel={sel} sw={sw} | {state()}")
                    else:
                        log_line(f"RSP {cid} err valves — 스마트펌프 아님 | {state()}")
                elif act == "outlet":
                    pos = int(cmd.get("pos", 1))
                    v = (getattr(hw, "valves", {}) or {}).get("Outlet")
                    if v is not None:
                        v.set_position(pos)
                        log_line(f"RSP {cid} ok outlet→{pos} | {state()}")
                    else:
                        log_line(f"RSP {cid} err outlet 없음 | {state()}")
                elif act == "zero":
                    S["cum"] = 0.0
                    S["wcum"] = 0.0
                    log_line(f"RSP {cid} ok zero — 측정 0점(push·withdraw) | {state()}")
                elif act == "mark":
                    lab = str(cmd.get("label", "?"))
                    log_line(f"RSP {cid} ok MARK {lab} push={S['cum']:.4f} "
                             f"withdraw={S['wcum']:.4f} mL | {state()}")
                elif act == "status":
                    log_line(f"RSP {cid} ok status | {state()}")
                elif act == "blow":
                    ok, dur = run_blowout(mfc, phase, float(cmd.get("sccm", 20)),
                                          float(cmd.get("sec", 60)))
                    log_line(f"RSP {cid} ok blow {'완전배출' if ok else '미완'} "
                             f"{dur:.1f}s | {state()}")
                elif act == "stop":
                    try:
                        pump.stop()
                    except Exception:
                        pass
                    try:
                        mfc and mfc.stop()
                    except Exception:
                        pass
                    log_line(f"RSP {cid} ok stop | {state()}")
                elif act == "quit":
                    log_line(f"RSP {cid} ok quit — 정리 후 종료")
                    break
                else:
                    log_line(f"RSP {cid} err 알 수 없는 액션 '{act}'")
            except Exception as e:
                log_line(f"RSP {cid} err {type(e).__name__}: {e} | {state()}")
    finally:
        for fn in ("stop",):
            try:
                getattr(pump, fn)()
            except Exception:
                pass
        try:
            mfc and mfc.stop()
        except Exception:
            pass
        try:
            getattr(hw, "cleanup", lambda: None)()
        except Exception:
            pass
        log_line("SHUTDOWN 완료 (펌프 정지·정리)")


if __name__ == "__main__":
    sys.exit(main())
