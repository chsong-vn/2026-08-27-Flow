# -*- coding: utf-8 -*-
"""런 간트 시각화 v2 — OCTOPUS Fig.5c 스타일 (Nat.Commun. 2024, 15:9669).

TRACE_*.json (Perfetto 트레이스)를 파싱해 장치 레인별 간트를 그린다.
  가로축 = 시퀀스 경과 (m:ss)
  세로축 = 장치 레인 (Heater / 시린지 그룹들 / HPLC push / N2 / Outlet / Collector)
  색 박스 = 작업 구간, 빗금 = 손실 시간(강제정지·무유량 갭) ← Fig.5c 의 지연 표기
  Outlet 레인 = WASTE/COLLECT 상태 밴드, 수직 점선 = 각 스텝 주입 T+0

v2 (2026-08-18 적대검증 반영):
  · 페어링 전-발생 스캔 — 다중 스텝/다중 세척 사이클 지원 (v1 은 첫 매칭만)
  · 주입 강제정지(자동정지 유예 초과)·주입종료~push 무유량 갭을 빗금으로 표기
  · Heater 램프(진한색)+유지 밴드(연한색) 구분, 스텝 경계선+라벨, 범례, m:ss 축

사용 (루트에서):
    py -3.14 tools\\plot_run_gantt.py                       # 최신 TRACE 자동
    py -3.14 tools\\plot_run_gantt.py logs\\TRACE_xxx.json  # 지정
출력: 같은 이름의 .gantt.png
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C = {
    "heat":    "#b48ead", "heat_hold": "#e5d5e8",
    "purge":   "#8fbcbb",
    "wash":    "#5e81ac",
    "prime":   "#a3be8c",
    "reagent": "#d08770",
    "inject":  "#bf616a",
    "push":    "#ebcb8b",
    "n2":      "#88c0d0",
    "collect": "#a3be8c", "waste": "#e4e9f0",
    "move":    "#4c566a",
    "loss":    "#bf616a",
}
LIGHT_FACE = {C["waste"], C["push"], C["heat_hold"]}


# ── 트레이스 로드 ────────────────────────────────────────────────
def load_events(path):
    d = json.load(open(path, encoding="utf-8"))
    evs = d if isinstance(d, list) else d.get("traceEvents", [])
    logs, spans = [], []
    for e in evs:
        ts = e.get("ts", 0) / 1e6
        if e.get("ph") == "i":
            a = e.get("args", {}) or {}
            msg = a.get("msg") if isinstance(a, dict) else None
            logs.append((ts, msg or e.get("name", "")))
        elif e.get("ph") == "X":
            spans.append((ts, e.get("dur", 0) / 1e6, e.get("name", "")))
    logs.sort()
    spans.sort()
    return logs, spans


def all_ts(logs, pattern):
    rx = re.compile(pattern)
    return [ts for ts, m in logs if rx.search(m)]


def pairs_all(logs, s_pat, e_pat):
    """모든 (시작, 끝) 쌍 — 전-발생 스캔 (다중 스텝/사이클 지원)."""
    s_rx, e_rx = re.compile(s_pat), re.compile(e_pat)
    out, open_ts = [], None
    for ts, m in logs:
        if open_ts is None and s_rx.search(m):
            open_ts = ts
            continue
        if open_ts is not None and e_rx.search(m):
            if ts > open_ts:
                out.append((open_ts, ts))
            open_ts = None
    return out


# ── 레인 구성 ────────────────────────────────────────────────────
def pump_lane(logs, grp):
    g = re.escape(f"[{grp}]")
    items = []

    def add(s_pat, e_pat, label, color):
        for s, e in pairs_all(logs, s_pat, e_pat):
            items.append((s, e, label, color, None))

    add(rf"{g} BubblePurge: Withdrawing", rf"{g} BubblePurge: Refill Done",
        "퍼지", C["purge"])
    add(rf"{g} BubblePurge: Wash: Infusing",
        rf"(\[BubblePurge\] 완료|{g} BubblePurge: .*(Infuse|Wash) Done)",
        "퍼지 배출", C["purge"])
    add(rf"{g} Wash \d+/\d+: Wash: Withdrawing", rf"{g} Wash \d+/\d+: Wash Done",
        "세척 흡인", C["wash"])
    add(rf"{g} Wash \d+/\d+: Wash: Infusing",
        rf"(System wash complete|{g} Wash \d+/\d+: Wash: Withdrawing)",
        "세척 배출", C["wash"])
    add(rf"{g} Phase-0 정량 리필", rf"{g} Prime Done", "Phase-0", C["prime"])
    add(rf"{g} Prime-P1: Withdrawing", rf"{g} Prime-P1: Refill Done",
        "P1 흡인", C["prime"])
    add(rf"{g} Prime-P1: Prime: Infusing", rf"{g} Prime-P1: Prime Done",
        "P1 충전", C["prime"])
    add(rf"({re.escape(grp)}: fill |{re.escape(grp)}: full capacity)",
        rf"{g} Refill Done", "시약 장전", C["reagent"])
    add(rf"{g} PushWash \d+/\d+: Wash: Withdrawing",
        rf"{g} PushWash \d+/\d+: Wash Done", "병행세척", C["wash"])
    add(rf"{g} PushWash \d+/\d+: Wash: Infusing",
        rf"(\[PushWash\] 병행 세척 완료|{g} PushWash \d+/\d+: Wash: Withdrawing)",
        "병행세척 배출", C["wash"])
    # 주입 (전 스텝) + 강제정지 빗금
    # 강제정지 로그는 '유예 초과 판정' 시각 — 실제 미토출은 유예(기본 6s) 전부터
    stalls = all_ts(logs, r"자동정지 유예 .*강제 정지")
    for s, e in pairs_all(logs, r"\[S\d+-Injection\] start",
                          r"\[S\d+-Injection\] done"):
        hatch = next(((max(s, t - 6.0), e) for t in stalls if s < t < e + 3), None)
        items.append((s, e, "주입", C["inject"], hatch))
    # legacy 경로 프라임/푸시 대응 (있으면)
    add(rf"{g} Prime: Infusing", rf"{g} Prime Done", "프라임", C["prime"])
    return items


def build_lanes(logs, spans, t0, t_end):
    lanes = []
    # Heater: 스텝별 램프(진한색) + 유지 밴드(연한색). 램프 끝 = 마지막 '연속' Heating 로그
    heats = []
    heat_ts = all_ts(logs, r"^(\[T\+[\d:]+\] )?Heating ")
    for s in all_ts(logs, r"Heat to "):
        seq = [t for t in heat_ts if t >= s]
        e = s + 2
        for t in seq:
            if t - e > 6:
                break
            e = max(e, t + 1)
        heats.append((s, e, "가열", C["heat"], None))
    for i, (s, e, *_r) in enumerate(list(heats)):
        nxt = heats[i + 1][0] if i + 1 < len(heats) else t_end
        if nxt - e > 10:
            heats.append((e, nxt, "유지", C["heat_hold"], None))
    if heats:
        lanes.append(("Heater", sorted(heats)))

    # 펌프 레인 자동 탐색 — 이름을 가정하지 않고 '펌프 동작 로그' 패턴으로 수집
    # (실기 "Group A"뿐 아니라 모의 하네스 "A"/"B" 등 임의 이름 지원)
    grps = sorted({m.group(1) for _, s in logs
                   for m in [re.search(
                       r"\[([A-Za-z0-9 _-]+)\] (?:BubblePurge:|Wash \d+/\d+:|"
                       r"Prime-P1:|Prime:|Refill Done|PushWash|Phase-0)", s)] if m})
    for grp in grps:
        lanes.append((grp, pump_lane(logs, grp)))

    hp = []
    for s, e in pairs_all(logs, r"\[PushLinePrime\] 라인 충전", r"\[PushLinePrime\] 완료"):
        hp.append((s, e, "라인 프라임", C["push"], None))
    for s, e in pairs_all(logs, r"Step \d+: HPLC push \|", r"Step \d+: push complete"):
        hp.append((s, e, "PUSH (수송+수집)", C["push"], None))
    if hp:
        lanes.append(("HPLC push", hp))

    n2 = []
    for s, e in pairs_all(logs, r"\[N2Precal\] N2 배기 시작",
                          r"\[N2Precal\] (완료|⚠)"):
        n2.append((s, e, "N2 프리캘", C["n2"], None))
    for pat, lbl in ((r"\[InjMarker\] N2 마커 front", "◀마커"),
                     (r"\[InjMarker\] N2 마커 rear", "마커▶")):
        for ts in all_ts(logs, pat):
            if "완료" not in lbl:
                n2.append((ts, ts + 3, lbl, C["n2"], None))
    if n2:
        lanes.append(("N2 (MFC)", n2))

    sw = sorted([(ts, "COLLECT" if "COLLECT" in n else "WASTE")
                 for ts, dur, n in spans if n.startswith("Outlet→")])
    ob, cur, cs = [], "WASTE", t0
    for ts, st in sw:
        if st != cur:
            ob.append((cs, ts, cur, C["collect" if cur == "COLLECT" else "waste"], None))
            cur, cs = st, ts
    ob.append((cs, t_end, cur, C["collect" if cur == "COLLECT" else "waste"], None))
    lanes.append(("Outlet", ob))

    col = []
    for s in all_ts(logs, r"Collector homing started"):
        e_c = all_ts(logs, r"pre-move")
        e = next((t for t in e_c if t > s), s + 50)
        col.append((s, e, "호밍", C["move"], None))
    for ts, dur, n in spans:
        if n.startswith("Move → "):
            col.append((ts, ts + max(dur, 4), n.replace("Move → ", ""), C["move"], None))
    if col:
        lanes.append(("Collector", col))
    return lanes


def no_flow_gaps(logs):
    """주입 종료 → push 시작 사이 무유량 갭 (타이머 정지 구간)."""
    gaps = []
    dones = all_ts(logs, r"\[S\d+-Injection\] done")
    starts = all_ts(logs, r"(Step \d+: HPLC push \||\[S\d+-Push\])")
    for d in dones:
        nxt = next((s for s in starts if s > d), None)
        if nxt and nxt - d > 2.0:
            gaps.append((d, nxt))
    return gaps


# ── 렌더 ────────────────────────────────────────────────────────
def render(path):
    logs, spans = load_events(path)
    if not logs:
        print("트레이스에 LOG 이벤트가 없습니다")
        return None
    t0 = logs[0][0]
    t_end = max(ts for ts, _ in logs)
    dur = t_end - t0
    lanes = build_lanes(logs, spans, t0, t_end)
    steps = [(ts, re.search(r"Step (\d+) Start", m).group(1))
             for ts, m in logs if re.search(r"Step \d+ Start", m)]
    injs = all_ts(logs, r"\[S\d+-Injection\] start")
    gaps = no_flow_gaps(logs)

    plt.rcParams["font.family"] = ["Malgun Gothic", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    W = max(15.0, dur / 55.0)
    fig, ax = plt.subplots(figsize=(W, 0.66 * len(lanes) + 2.9))
    H = 0.6
    n = len(lanes)

    # 행 밴딩(짝수 행 연회색) — 레인 구분 가독성
    for i in range(n):
        y = n - 1 - i
        if i % 2 == 1:
            ax.axhspan(y - 0.5, y + 0.5, color="#f4f5f7", zorder=0)

    # 스텝 경계 + 라벨 밴드
    for ts, sn in steps:
        x = ts - t0
        ax.axvline(x, color="#9aa2b1", lw=1.0, zorder=1)
        ax.text(x + dur * 0.004, n - 0.18, f"Step {sn}", fontsize=10,
                fontweight="bold", color="#4c566a", va="top", zorder=6)
    # 주입 T+0
    for ts in injs:
        x = ts - t0
        ax.axvline(x, color=C["inject"], ls="--", lw=1.2, zorder=1)
        ax.text(x + dur * 0.004, -0.42, "주입 T+0", color=C["inject"], fontsize=8.5)

    # 무유량 갭 — 전 레인 빗금 밴드
    for s, e in gaps:
        ax.add_patch(Rectangle((s - t0, -0.5), e - s, n, facecolor="none",
                               edgecolor=C["loss"], hatch="//", lw=0.0,
                               alpha=0.55, zorder=2))

    lbl_min = dur * 0.022          # 이 폭 이상이면 바 안에 라벨
    for i, (name, items) in enumerate(lanes):
        y = n - 1 - i
        for (s, e, lbl, color, hatch) in sorted(items):
            x, w = s - t0, max(e - s, dur * 0.003)
            ax.add_patch(Rectangle((x, y - H / 2), w, H, facecolor=color,
                                   edgecolor="white", lw=0.7, zorder=3))
            if hatch:                      # 강제정지 등 손실 꼬리
                hs, he = hatch
                ax.add_patch(Rectangle((hs - t0, y - H / 2), he - hs, H,
                                       facecolor="none", edgecolor="#7a1f28",
                                       hatch="////", lw=0.0, zorder=4))
            txtc = "#333" if color in LIGHT_FACE else "white"
            if w >= lbl_min:
                ax.text(x + w / 2, y, lbl, ha="center", va="center",
                        fontsize=9, color=txtc, zorder=5)
            elif w >= dur * 0.006:
                ax.text(x + w / 2, y + H / 2 + 0.06, lbl, ha="center",
                        va="bottom", fontsize=7, color="#555", zorder=5,
                        rotation=0)

    ax.set_yticks(range(n))
    ax.set_yticklabels([nm for nm, _ in reversed(lanes)], fontsize=11)
    ax.set_ylim(-0.55, n - 0.5 + 0.45)
    ax.set_xlim(-dur * 0.01, dur * 1.01)
    step_s = 60 if dur > 240 else 30
    ticks = list(range(0, int(dur) + step_s, step_s))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t // 60}:{t % 60:02d}" for t in ticks], fontsize=9)
    ax.set_xlabel("시퀀스 경과 (분:초)", fontsize=10)
    ax.grid(axis="x", alpha=0.3, lw=0.5, zorder=0)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)

    legend = [
        Patch(fc=C["heat"], label="가열"), Patch(fc=C["purge"], label="기포 퍼지"),
        Patch(fc=C["wash"], label="세척"), Patch(fc=C["prime"], label="용매 프라임"),
        Patch(fc=C["reagent"], label="시약 장전"), Patch(fc=C["inject"], label="주입"),
        Patch(fc=C["push"], label="HPLC push"), Patch(fc=C["n2"], label="N2"),
        Patch(fc=C["collect"], label="COLLECT"), Patch(fc=C["waste"], ec="#bbb", label="WASTE"),
        Patch(fc=C["move"], label="분취기 이동"),
        Patch(fc="none", ec=C["loss"], hatch="///", label="손실(정지·무유량)"),
    ]
    ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 1.005),
              ncol=len(legend), fontsize=9, frameon=False,
              handlelength=1.4, columnspacing=1.0)
    base = os.path.basename(path).replace("TRACE_", "").replace("_Sequence.json", "")
    fig.suptitle(f"런 타임라인  {base}   (총 {int(dur) // 60}분 {int(dur) % 60}초)",
                 fontsize=13, y=0.995)
    out = os.path.splitext(path)[0] + ".gantt.png"
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("saved:", out)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        p = sys.argv[1]
    else:
        cands = sorted(glob.glob(os.path.join(ROOT, "logs", "TRACE_*.json")))
        p = cands[-1] if cands else None
    if not p or not os.path.exists(p):
        print("TRACE 파일 없음")
        sys.exit(1)
    render(p)
