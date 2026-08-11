# -*- coding: utf-8 -*-
"""레벨센서 raw vs median 비교 캘리브레이션 — CH0 스윕 + 원시 스트림 기록.

목적: "median 없이 그냥 측정" vs "현재 구현(호스트 median-of-N)" 을 동일 조건에서
비교하고 curve 를 그린다.

@codesyncer-decision: 두 번 따로 재지 않고 **원시 스트림 1회 기록 → 두 추정자를
  같은 데이터에서 도출**한다. 드라이버의 median 은 호스트 계산이므로
  (ultrasonic_level._read_raw) 오프라인 median-of-N = 현재 구현과 수학적으로 동일.
  런 간 교란(온도 드리프트/메니스커스/마운트)이 제거된 진짜 '동일 조건'이고,
  median-of-5(고속)/10(구 캘리)/20(현재 기본)이 전부 공짜로 비교된다.

프로토콜 (calibrate_ch0_3x.py 검증된 메커니즘 재사용):
  시작 시 주사기 수동 0 정렬 → [0 → top → 0] × cycles, step 간격 정지-측정.
  각 스텝: settle 후 원시표본 samples_per_step 개 기록 (NA 는 기록하되 무효 표시).

사용법:
  py -3.14 calibrate_level_median_compare.py --pump COMx          # 실측 (센서 COM3)
  py -3.14 calibrate_level_median_compare.py --sim                # 하드웨어 없이 전체 검증
  py -3.14 calibrate_level_median_compare.py --replay level_cmp_raw.csv   # 기록 재분석만

출력: level_cmp_raw.csv (원시 전체) / level_cmp_report.png (4패널) /
      level_cmp_summary.txt (추정자별 오차표 + 권장 fit)
"""
import argparse, csv, math, os, re, statistics, sys, time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 팔레트 (dataviz 기본 — all-pairs 3슬롯) ──
SURFACE, INK, INK2, GRIDC = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
C_RAW, C_M20, C_M5, MUTE = "#eb6834", "#2a78d6", "#1baf7a", "#b8b6b0"


# ── Chemyx 헬퍼 (calibrate_ch0_3x.py 검증본) ─────────────────
def readfull(ser, t=0.8):
    t0 = time.time(); buf = b""
    while time.time() - t0 < t:
        n = ser.in_waiting
        if n: buf += ser.read(n)
        else: time.sleep(0.02)
    return buf


def cmd(pump, pid, c, retries=3):
    for _ in range(retries):
        pump.reset_input_buffer()
        pump.write((c + "\r").encode())
        r = readfull(pump)
        if r: return r.decode("ascii", "replace").strip()
        time.sleep(0.4)
    return ""


def read_dispensed(pump, pid):
    r = cmd(pump, pid, f"{pid} dispensed volume")
    m = re.search(r"dispensedvolume\s*=\s*(-?[0-9.]+)", r, re.IGNORECASE)
    return float(m.group(1)) if m else None


def move_to(pump, pid, rate, cur, target):
    """cur → target (mL). +infuse / -withdraw. 자연 완료 대기 (stop 금지 — 카운터 리셋)."""
    delta = target - cur
    if abs(delta) < 1e-9: return True
    setvol = -delta
    dur = abs(delta) / rate * 60.0
    cmd(pump, pid, f"{pid} set rate {rate:g}")
    cmd(pump, pid, f"{pid} set volume {setvol:g}")
    before = read_dispensed(pump, pid)
    cmd(pump, pid, f"{pid} start")
    time.sleep(dur + 2.0)
    after = read_dispensed(pump, pid)
    if before is not None and after is not None:
        moved = abs(after - before)
        if abs(moved - abs(delta)) > max(0.05, abs(delta) * 0.1):
            print(f"  !! 이동검증 편차: 요청 {abs(delta):.3f} vs 실측 {moved:.3f} mL")
            return False
    return True


# ── 표본 수집 ────────────────────────────────────────────────
def collect_step(sens, index, n, tmax=None):
    """정지 상태에서 원시표본 n개 (유효 float). 반환 [(t_rel, raw_cm)], NA 수."""
    time.sleep(0.5)                       # settle (기존 캘리와 동일)
    sens.reset_input_buffer()
    vals, na = [], 0
    t0 = time.time()
    tmax = tmax or (n * 0.35 + 5.0)
    while len(vals) < n and time.time() - t0 < tmax:
        line = sens.readline().decode(errors="ignore").strip()
        if not line: continue
        parts = line.split(",")
        if len(parts) <= index: continue
        tok = parts[index].strip()
        if not tok or tok.upper() == "NA":
            na += 1; continue
        try:
            vals.append((round(time.time() - t0, 3), float(tok)))
        except ValueError:
            pass
    return vals, na


class SimSensor:
    """--sim: 합성 에코 스트림. 참값 선형 + 가우시안 노이즈 + 저빈도 아웃라이어/NA.
    HC-SR04 ±3mm 사양 근사: SD 0.15cm, 5% 아웃라이어(+0.8~2cm), 3% NA."""
    def __init__(self, true_slope=1.06776, true_icpt=-9.2382):
        # vol = slope*raw + icpt  →  raw = (vol - icpt)/slope
        self.s, self.i = true_slope, true_icpt
        self.vol = 0.0
        import random
        self.rng = random.Random(20260801)

    def set_vol(self, v): self.vol = v

    def sample(self):
        if self.rng.random() < 0.03: return None          # NA
        raw = (self.vol - self.i) / self.s
        raw += self.rng.gauss(0, 0.15)
        if self.rng.random() < 0.05:                       # 다중반사 아웃라이어
            raw += self.rng.uniform(0.8, 2.0) * (1 if self.rng.random() < 0.7 else -1)
        return round(raw, 2)


def collect_step_sim(sim, n):
    vals, na = [], 0
    for k in range(int(n * 1.1)):
        v = sim.sample()
        if v is None: na += 1
        else: vals.append((round(k * 0.1, 3), v))
        if len(vals) >= n: break
    return vals, na


# ── 분석 ─────────────────────────────────────────────────────
def linfit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return b, my - b * mx                                  # slope, intercept


def med_of(vals, k, rng):
    """기록 표본에서 크기 k 부분표본의 median (부트스트랩 1회)."""
    if len(vals) <= k: return statistics.median(vals)
    i0 = rng.randrange(0, len(vals) - k + 1)               # 연속 구간 = 실제 판독 재현
    return statistics.median(vals[i0:i0 + k])


def analyze(rows, out_png, out_txt, min_valid=10):
    """rows: [(cycle, leg, vol, [(t,raw),...], na)]

    min_valid: 드라이버 품질게이트(유효 < 절반 → 실패)와 같은 취지 — 표본이 이보다
    적은 스텝은 fit/통계에서 제외하고 시끄럽게 알린다 (적은 표본 median = 왜곡원).
    """
    import random
    rng = random.Random(7)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False

    all_steps = [(c, leg, v, [x[1] for x in s], na) for c, leg, v, s, na in rows if s]
    steps = [t for t in all_steps if len(t[3]) >= min_valid]
    dropped = [t for t in all_steps if len(t[3]) < min_valid]
    for c, leg, v, raws, na in dropped:
        print(f"  !! 스텝 제외: c{c} {leg} {v:.2f}mL — 유효표본 {len(raws)}<{min_valid} "
              f"(NA {na}) — 센서 조준/반사블록 확인")
    if len(steps) < 4:
        raise SystemExit(f"[X] 분석 가능 스텝 {len(steps)}개 (<4) — 측정 품질 불충분")

    # 기준 fit: 현재 구현(median-of-20) 포인트로
    m20 = [(v, statistics.median(raws[:20]) if len(raws) >= 20 else statistics.median(raws))
           for _, _, v, raws, _ in steps]
    slope, icpt = linfit([r for _, r in m20], [v for v, _ in m20])   # vol = s*raw + i

    def vol_err_ul(raw, true_v):
        return (slope * raw + icpt - true_v) * 1000.0

    # 추정자별 오차 분포 (µL)
    est_errs = {"raw 1샷": [], "median-of-5": [], "median-of-10": [], "median-of-20": []}
    per_step = []                     # (vol, spread SD, m20 err)
    for _, _, v, raws, _ in steps:
        for r in raws:
            est_errs["raw 1샷"].append(vol_err_ul(r, v))
        for name, k in (("median-of-5", 5), ("median-of-10", 10), ("median-of-20", 20)):
            for _ in range(30):       # 부트스트랩 30회/스텝
                est_errs[name].append(vol_err_ul(med_of(raws, k, rng), v))
        per_step.append((v, statistics.pstdev(raws) * slope * 1000.0,
                         vol_err_ul(statistics.median(raws[:20]), v)))

    def stats_row(errs):
        a = sorted(abs(e) for e in errs)
        p95 = a[min(len(a) - 1, int(0.95 * len(a)))]
        return (statistics.mean(a), statistics.pstdev(errs), p95, a[-1])

    # ── 리포트 텍스트 ──
    lines = [f"기준 fit (median-of-20): vol[mL] = {slope:.5f} * raw[cm] + {icpt:.4f}",
             f"  (현재 config: slope 1.06776 / intercept -9.2382 — 비교용)",
             f"스텝 {len(steps)}개, 총 원시표본 {sum(len(r) for _, _, _, r, _ in steps)}개, "
             f"NA {sum(na for *_, na in steps)}개", "",
             f"{'추정자':<14s}{'평균|err|':>10s}{'SD':>9s}{'P95':>9s}{'최대':>9s}  (µL)"]
    for name in ("raw 1샷", "median-of-5", "median-of-10", "median-of-20"):
        m, sd, p95, mx = stats_row(est_errs[name])
        gate = "  <— gate 100µL " + ("통과" if p95 <= 100 else "★초과")
        lines.append(f"{name:<14s}{m:>10.1f}{sd:>9.1f}{p95:>9.1f}{mx:>9.1f}"
                     + (gate if name in ("raw 1샷", "median-of-20") else ""))
    # 왕복(히스테리시스): 같은 vol 의 up/down median 차
    byv = {}
    for _, leg, v, raws, _ in steps:
        byv.setdefault(round(v, 3), {})[leg] = statistics.median(raws)
    hyst = [(v, (d["down"] - d["up"]) * slope * 1000.0)
            for v, d in sorted(byv.items()) if "up" in d and "down" in d]
    if hyst:
        hs = [abs(h) for _, h in hyst]
        lines += ["", f"왕복차(히스테리시스): 평균 {statistics.mean(hs):.1f} µL / "
                      f"최대 {max(hs):.1f} µL"]
    txt = "\n".join(lines)
    print("\n" + txt)
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(txt + "\n")

    # ── 4패널 그림 ──
    fig, axs = plt.subplots(2, 2, figsize=(14, 9.5), facecolor=SURFACE)
    for ax in axs.flat:
        ax.set_facecolor(SURFACE); ax.grid(True, color=GRIDC, lw=0.6)
        ax.set_axisbelow(True); ax.tick_params(colors=INK2, labelsize=8)
        for sp in ax.spines.values(): sp.set_color(GRIDC)

    ax = axs[0][0]                                   # ① 커브: raw 산점 + median
    for _, _, v, raws, _ in steps:
        ax.scatter([v] * len(raws), raws, s=6, color=C_RAW, alpha=0.25, lw=0)
    ax.scatter([v for v, _ in m20], [r for _, r in m20], s=22, color=C_M20, zorder=5)
    vg = [min(v for v, _ in m20), max(v for v, _ in m20)]
    ax.plot(vg, [(v - icpt) / slope for v in vg], color=C_M20, lw=1.4)
    ax.set_title("캘리브레이션 커브 — 원시(주황 산점) vs median-of-20(파랑)",
                 color=INK, fontsize=10, fontweight="bold")
    ax.set_xlabel("주사기 부피 (mL)", color=INK2, fontsize=9)
    ax.set_ylabel("raw 거리 (cm)", color=INK2, fontsize=9)

    ax = axs[0][1]                                   # ② 부피오차 vs vol
    for _, _, v, raws, _ in steps:
        ax.scatter([v] * len(raws), [vol_err_ul(r, v) for r in raws],
                   s=6, color=C_RAW, alpha=0.25, lw=0)
    ax.scatter([v for v, _, _ in per_step], [e for _, _, e in per_step],
               s=22, color=C_M20, zorder=5)
    ax.axhline(0, color=INK2, lw=0.8)
    for g in (100, -100):
        ax.axhline(g, color=MUTE, lw=0.9, ls=(0, (4, 3)))
    ax.set_title("부피 오차 (µL) — 점선 = ±100µL 게이트", color=INK, fontsize=10,
                 fontweight="bold")
    ax.set_xlabel("주사기 부피 (mL)", color=INK2, fontsize=9)

    ax = axs[1][0]                                   # ③ |오차| 분포 (박스)
    names = ["raw 1샷", "median-of-5", "median-of-10", "median-of-20"]
    data = [[abs(e) for e in est_errs[n]] for n in names]
    bp = ax.boxplot(data, tick_labels=names, showfliers=False, patch_artist=True,
                    medianprops=dict(color=INK))
    for patch, c in zip(bp["boxes"], (C_RAW, C_M5, C_M5, C_M20)):
        patch.set_facecolor(c); patch.set_alpha(0.45)
    ax.axhline(100, color=MUTE, lw=0.9, ls=(0, (4, 3)))
    ax.set_title("|부피 오차| 분포 — 추정자별 (µL)", color=INK, fontsize=10,
                 fontweight="bold")

    ax = axs[1][1]                                   # ④ 왕복차
    if hyst:
        ax.bar([f"{v:g}" for v, _ in hyst], [h for _, h in hyst],
               color=C_M20, alpha=0.75, width=0.6)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_title("왕복차 down-up (µL) — 마운트/플런저 히스테리시스",
                     color=INK, fontsize=10, fontweight="bold")
        ax.tick_params(axis="x", rotation=60)
    else:
        ax.text(0.5, 0.5, "단방향 스윕 — 왕복 데이터 없음", ha="center",
                color=INK2, transform=ax.transAxes)
    fig.suptitle("레벨센서 raw vs median 비교 (동일 에코 스트림에서 도출)",
                 color=INK, fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(out_png, dpi=160, facecolor=SURFACE)
    print(f"\n저장: {out_png}\n저장: {out_txt}")


# ── 메인 ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sens", default="COM3")
    ap.add_argument("--pump", help="Chemyx 포트 (실측 필수 — 맵핑 확인 후)")
    ap.add_argument("--pid", type=int, default=1)
    ap.add_argument("--diameter", type=float, default=12.45,
                    help="주사기 직경 mm — ⚠ 백로그 3-A: config 12.45 vs 실물(5mL 주사기?) "
                         "미확정. 실물 각인 확인 후 넣을 것 (오차가 (d_real/d_cfg)^2 로 전파)")
    ap.add_argument("--index", type=int, default=0, help="4ch CSV 채널 (CH0=0)")
    ap.add_argument("--rate", type=float, default=5.0)
    ap.add_argument("--step", type=float, default=0.3)
    ap.add_argument("--top", type=float, default=5.0)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--samples-per-step", type=int, default=40)
    ap.add_argument("--sim", action="store_true", help="하드웨어 없이 합성 데이터로 전체 검증")
    ap.add_argument("--replay", help="기존 raw CSV 재분석만")
    args = ap.parse_args()

    # 실측마다 별도 보존 (덮어쓰기 방지) — replay 로 언제든 재분석 가능
    tag = time.strftime("%Y%m%d_%H%M%S")
    raw_csv = os.path.join(HERE, f"level_cmp_raw_{tag}.csv")
    out_png = os.path.join(HERE, f"level_cmp_report_{tag}.png")
    out_txt = os.path.join(HERE, f"level_cmp_summary_{tag}.txt")

    if args.replay:
        rows = {}
        with open(args.replay, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (int(r["cycle"]), r["leg"], float(r["vol_mL"]))
                rows.setdefault(key, ([], 0))
                rows[key][0].append((float(r["t_rel"]), float(r["raw_cm"])))
        analyze([(c, l, v, s, 0) for (c, l, v), (s, _) in sorted(rows.items())],
                out_png, out_txt)
        return

    # 스윕 볼륨 시퀀스: [0→top→0] × cycles
    def leg_vols(a, b):
        n = int(round(abs(b - a) / args.step))
        return [round(a + (b - a) * i / n, 3) for i in range(n + 1)]
    plan = []
    for c in range(1, args.cycles + 1):
        plan += [(c, "up", v) for v in leg_vols(0.0, args.top)]
        plan += [(c, "down", v) for v in leg_vols(args.top, 0.0)[1:]]

    rows = []
    if args.sim:
        sim = SimSensor()
        print(f"[SIM] {len(plan)}스텝 합성 스윕")
        for cyc, leg, v in plan:
            sim.set_vol(v)
            s, na = collect_step_sim(sim, args.samples_per_step)
            rows.append((cyc, leg, v, s, na))
    else:
        if not args.pump:
            print("[X] --pump COMx 필요 (diagnose_mapping.py 로 포트 확인).")
            sys.exit(2)
        import serial
        sens = serial.Serial(args.sens, 9600, timeout=1.0)
        pump = serial.Serial(args.pump, 9600, timeout=1.0)
        time.sleep(2.0)
        cmd(pump, args.pid, f"{args.pid} set diameter {args.diameter:g}")
        input(f"주사기를 0 mL 로 수동 정렬 후 Enter ({args.cycles}사이클, "
              f"0→{args.top}→0, {args.step} 스텝): ")
        cur = 0.0
        try:
            for i, (cyc, leg, v) in enumerate(plan, 1):
                if abs(v - cur) > 1e-9:
                    if not move_to(pump, args.pid, args.rate, cur, v):
                        print("  이동검증 실패 — 중단"); break
                    cur = v
                s, na = collect_step(sens, args.index, args.samples_per_step)
                med = statistics.median([x[1] for x in s]) if s else float("nan")
                print(f"  [{i}/{len(plan)}] c{cyc} {leg:4s} {v:5.2f} mL — "
                      f"표본 {len(s)} (NA {na})  median {med:.2f} cm")
                rows.append((cyc, leg, v, s, na))
        finally:
            for _ in range(3):
                try: pump.write(f"{args.pid} stop\r".encode()); time.sleep(0.3)
                except Exception: pass
            sens.close(); pump.close()

    with open(raw_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cycle", "leg", "vol_mL", "t_rel", "raw_cm"])
        for cyc, leg, v, s, _ in rows:
            for t, r in s:
                w.writerow([cyc, leg, v, t, r])
    print(f"원시 기록: {raw_csv} ({sum(len(s) for *_, s, _ in rows)}표본)")
    analyze(rows, out_png, out_txt)


if __name__ == "__main__":
    main()
