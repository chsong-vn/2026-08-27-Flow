# -*- coding: utf-8 -*-
"""전체 프로세스 설계 타임라인 — 정본 워크플로 한 장 그림 (2026-08-19).

실측 기반 대표 시간(2026-08-18 16:31 실런, 1스텝 4mL @0.8mL/min, port_change)
위에 브래킷 마커(inj_marker_mode="bracket") 설계 위치를 얹은 도식.
간트 도구(plot_run_gantt)가 '실제 런 기록'이라면 이 그림은 '설계 정본'이다.

출력: docs/workflow_schematic.png
실행: py -3.14 tools\\plot_workflow_schematic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle, FancyArrowPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C = {"heat": "#b48ead", "hold": "#e5d5e8", "purge": "#8fbcbb", "wash": "#5e81ac",
     "prime": "#a3be8c", "reagent": "#d08770", "inject": "#bf616a",
     "push": "#ebcb8b", "n2": "#88c0d0", "collect": "#a3be8c",
     "waste": "#e4e9f0", "move": "#4c566a", "loss": "#bf616a"}
LIGHT = {C["waste"], C["push"], C["hold"]}

# ── 대표 시간 (16:31 실런 실측 + 마커 설계식) ──────────────────────
T0 = 374.0            # 주입 시작 (시퀀스 기준)
PRE = 58.5            # pre_sec: 3way→QUAD→가스T 주입경로 도달
DOSE = 308.0          # 주입 시간
GAP = (682.0, 690.0)  # 주입종료→push 무유량 (타이머 정지)
HEAD = 666.0          # Outlet→COLLECT (t_head)
CEND = 997.0          # 수집 종료 → WASTE
END = 1020.0
MK1 = T0 + PRE                       # 전단마커 발사 = 432.5
MK2 = GAP[1] + PRE                   # 후단마커 발사 = 748.5 (펌핑경과 DOSE+PRE)
S2_F = HEAD - 3                      # 전단마커 꼬리(=선단) 센서2 통과
S2_R = MK2 + (S2_F - MK1)            # 후단마커 센서2 통과 ≈ 979

BAR = lambda s, e, l, c: (s, e, l, c, None)
LANES = [
    ("Heater", [BAR(51, 61, "가열", C["heat"]), BAR(61, END, "온도 유지", C["hold"])]),
    ("Group A", [
        BAR(60, 75, "퍼지 흡인", C["purge"]), BAR(82, 88, "배출", C["purge"]),
        BAR(94, 137, "세척 흡인", C["wash"]), BAR(143, 186, "세척 배출", C["wash"]),
        BAR(187, 227, "Phase-0", C["prime"]),
        BAR(232, 263, "P1 흡인", C["prime"]), BAR(269, 327, "P1 충전", C["prime"]),
        BAR(327, 374, "시약 장전", C["reagent"]),
        BAR(T0, 682, "주입 (시약 → 반응기)", C["inject"]),
        BAR(696, 739, "병행세척 흡인", C["wash"]), BAR(745, 790, "배출", C["wash"]),
    ]),
    ("Group_D", []),                  # A 와 동일 — 아래서 복제
    ("HPLC push", [
        BAR(60, 76, "라인 프라임", C["push"]),
        BAR(GAP[1], 1012, "PUSH (수송 + 수집 + 라인워시)", C["push"]),
    ]),
    ("N2 (MFC)", [
        BAR(76, 111, "N2 프리캘", C["n2"]),
        BAR(MK1, MK1 + 10, "◀전단", C["n2"]),
        BAR(MK2, MK2 + 10, "후단▶", C["n2"]),
    ]),
    ("Outlet", [
        BAR(0, HEAD, "WASTE", C["waste"]),
        BAR(HEAD, CEND, "COLLECT", C["collect"]),
        BAR(CEND, END, "WASTE", C["waste"]),
    ]),
    ("Collector", [
        BAR(0, 51, "호밍·파킹", C["move"]),
        BAR(688, 696, "A1", C["move"]), BAR(800, 808, "A2", C["move"]),
        BAR(912, 920, "A3", C["move"]), BAR(1000, 1008, "WASH", C["move"]),
    ]),
]
LANES[2] = ("Group_D", list(LANES[1][1]))

PHASES = [(0, T0, "① 준비 — 호밍·프리캘·퍼지·세척·프라임·장전"),
          (T0, GAP[0], "② 주입"),
          (GAP[0], END, "③ 수송 · 수집 (push)")]

plt.rcParams["font.family"] = ["Malgun Gothic", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
fig, ax = plt.subplots(figsize=(19, 8.2))
n = len(LANES)
H = 0.6

for i in range(n):
    if i % 2 == 1:
        ax.axhspan(n - 1 - i - 0.5, n - 1 - i + 0.5, color="#f4f5f7", zorder=0)

# 국면 밴드 (상단)
for s, e, lbl in PHASES:
    ax.axvline(s, color="#9aa2b1", lw=1.0, zorder=1)
    ax.text((s + e) / 2, n - 0.02, lbl, ha="center", va="bottom",
            fontsize=10.5, fontweight="bold", color="#4c566a")

# 주입 T+0 / 무유량 갭
ax.axvline(T0, color=C["inject"], ls="--", lw=1.3, zorder=2)
ax.text(T0 + 4, -0.44, "주입 T+0", color=C["inject"], fontsize=9)
ax.add_patch(Rectangle((GAP[0], -0.5), GAP[1] - GAP[0], n, facecolor="none",
                       edgecolor=C["loss"], hatch="//", lw=0, alpha=0.5, zorder=2))

for i, (name, items) in enumerate(LANES):
    y = n - 1 - i
    for s, e, lbl, color, _h in items:
        w = e - s
        ax.add_patch(Rectangle((s, y - H / 2), w, H, facecolor=color,
                               edgecolor="white", lw=0.7, zorder=3))
        txt = "#333" if color in LIGHT else "white"
        if w >= 28:
            ax.text(s + w / 2, y, lbl, ha="center", va="center", fontsize=9,
                    color=txt, zorder=5)
        else:
            ax.text(s + w / 2, y + H / 2 + 0.05, lbl, ha="center", va="bottom",
                    fontsize=7.5, color="#555", zorder=5)

# 마커 → 센서2 통과(게이트 트리거) 관계 화살표
y_n2 = n - 1 - 4
y_out = n - 1 - 5
for x0, x1, lbl in ((MK1 + 5, S2_F, "①선단 도착 → COLLECT"),
                    (MK2 + 5, S2_R, "②꼬리 통과 → WASTE 절단")):
    ax.add_patch(FancyArrowPatch((x0, y_n2 - H / 2), (x1, y_out + H / 2 + 0.03),
                                 arrowstyle="-|>", mutation_scale=11,
                                 color="#2e5d73", lw=1.2, ls=(0, (4, 3)), zorder=6))
    ax.plot(x1, y_out + H / 2 + 0.04, "v", color="#2e5d73", ms=7, zorder=7)
    ax.text(min(x1, 985), y_out + H / 2 + 0.16, lbl, ha="right", fontsize=8.2,
            color="#2e5d73", zorder=7)

ax.text(GAP[0] + 4, n - 1 - 3 + 0.45, "무유량 8s(빗금) — 타이머·마커 클록 정지",
        fontsize=8, color="#9a5b60")

ax.set_yticks(range(n))
ax.set_yticklabels([nm for nm, _ in reversed(LANES)], fontsize=11)
ax.set_ylim(-0.55, n + 0.45)
ax.set_xlim(-8, END + 8)
ticks = list(range(0, int(END) + 60, 60))
ax.set_xticks(ticks)
ax.set_xticklabels([f"{t // 60}:{t % 60:02d}" for t in ticks], fontsize=9)
ax.set_xlabel("시퀀스 경과 (분:초) — 대표 시간: 1스텝 4 mL @ 0.8 mL/min", fontsize=10)
ax.grid(axis="x", alpha=0.3, lw=0.5, zorder=0)
for sp in ("top", "right", "left"):
    ax.spines[sp].set_visible(False)

legend = [Patch(fc=C["heat"], label="가열"), Patch(fc=C["purge"], label="기포 퍼지"),
          Patch(fc=C["wash"], label="세척"), Patch(fc=C["prime"], label="용매 프라임"),
          Patch(fc=C["reagent"], label="시약 장전"), Patch(fc=C["inject"], label="주입"),
          Patch(fc=C["push"], label="HPLC push"), Patch(fc=C["n2"], label="N2"),
          Patch(fc=C["collect"], label="COLLECT"), Patch(fc=C["waste"], ec="#bbb", label="WASTE"),
          Patch(fc=C["move"], label="분취기"),
          Patch(fc="none", ec=C["loss"], hatch="///", label="무유량")]
ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 1.06),
          ncol=len(legend), fontsize=9, frameon=False, handlelength=1.3,
          columnspacing=0.9)
fig.suptitle("VORONOI 표준 시퀀스 — 전체 프로세스 설계 타임라인 (마커·게이트 포함)",
             fontsize=13.5, y=0.99)
out = os.path.join(ROOT, "docs", "workflow_schematic.png")
fig.tight_layout(rect=(0, 0, 1, 0.92))
fig.savefig(out, dpi=150, bbox_inches="tight")
print("saved:", out)
