# -*- coding: utf-8 -*-
"""타이밍·데드볼륨 정합 감사 — 원장 ↔ config ↔ 엔진 3층 교차검증.

PART A: tubing_measurements.json(원장 기하/실측) → 기대 mL 재계산 ↔
        hardware_config.json settings ↔ SystemConfig 파생값 3자 대조.
        허용오차: 직접 키 0.0005 mL(반올림), 등가길이 환산 키(mixing/reactor)
        0.005 mL. 미실측(measured null + 길이 없음)은 WARN.
PART B: StrictSequenceEngine._compute_plug_timing(실측값 + 실 entry_map) ↔
        본 파일의 독립 재구현(정의식 그대로, 엔진 코드 미사용) — 유속 시나리오
        7종 × (purge/pre/deficit/stagger) 전 출력 교차 (tol 1e-9).
        + 구 페어와이즈 식이었다면 생겼을 타이밍 오차 정량화(정보).
PART C: entry_map 배선 — 표준 경로/HTE 경로 소스 소비 + hte_build_profile
        v_head 기능 차등(=tj[2] 부피와 정확히 일치해야 함).
PART D: t_head 수송 구성요소 실측 분해 + 미실측 갭 플래그 (정보).

실행: 루트에서  py -3.14 tests\verify_timing_deadvol_consistency.py
"""
import json
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

fails, warns = [], []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


def warn(name, detail=""):
    print(f"  WARN {name} {detail}")
    warns.append(name)


def vol_len(id_mm, length_cm):
    return math.pi * (id_mm / 20.0) ** 2 * length_cm


# ══ PART A: 원장 → 기대값 ↔ config settings ↔ SystemConfig 파생 ══════
print("=== PART A: 원장 ↔ config ↔ 파생값 수치 대조 ===")
with open("tubing_measurements.json", encoding="utf-8") as f:
    ledger = json.load(f)
with open("hardware_config.json", encoding="utf-8") as f:
    hw = json.load(f)

prof = ledger["profiles"][ledger["active_profile"]]
settings = {pr["name"]: pr.get("settings", {}) for pr in hw["roles"]["pumps"]}
sp_json = hw["system_params"]

from engine.config import SystemConfig
cfg = SystemConfig()

DERIVED = {
    "tube_vol_inlet": cfg.line_vol_inlet,
    "tube_vol_valve_pump": cfg.line_vol_valve_pump,
    "tube_vol_selector": cfg.selector_internal_vol,
    "tube_vol_switcher": cfg.valve_internal_vol,
    "tube_vol_pump_merge": cfg.line_vol_pump_merge,
    "tube_vol_solvent": cfg.dead_vol_solvent,
    "tube_vol_reagent": cfg.dead_vol_reagent,
}


def ledger_expected(entry):
    """원장 규칙 그대로: measured_ml 최우선, 없으면 길이×내경, components 합산."""
    base = entry.get("measured_ml")
    if base is None:
        L, d = entry.get("length_cm"), entry.get("id_mm")
        if L is None or d is None:
            return None
        base = vol_len(d, L)
    return base + sum((c.get("ml") or 0.0) for c in entry.get("components", []))


for pname, keys in prof["pumps"].items():
    if pname not in settings:
        print(f"  SKIP {pname} — 원장에 있으나 roles 미등록(그룹 제외됨)")
        continue
    for key, entry in keys.items():
        exp = ledger_expected(entry)
        if exp is None:
            warn(f"{pname}.{key} 미실측(원장 값 없음)", "config 기본값 그대로")
            continue
        got_set = settings.get(pname, {}).get(key)
        got_drv = DERIVED[key].get(pname)
        ok_set = got_set is not None and abs(got_set - exp) <= 5e-4
        ok_drv = got_drv is not None and abs(got_drv - exp) <= 5e-4
        check(f"{pname}.{key}", ok_set and ok_drv,
              f"원장기대 {exp:.4f} | settings {got_set} | 파생 {got_drv} "
              f"(Δset {abs((got_set or 0) - exp) * 1000:.2f}µL)")

sysm = prof["system"]
# tj 캐스케이드 (QUAD1→QUAD2 / QUAD2→가스T)
for k, entry in sysm["tjunction_line_vols"].items():
    exp = ledger_expected(entry)
    got = cfg.tjunction_line_vols.get(int(k))
    check(f"tj[{k}] (정션 구간)", got is not None and abs(got - exp) <= 5e-4,
          f"원장 {exp:.4f} | 파생 {got} (Δ {abs((got or 0) - exp) * 1000:.2f}µL)")
# mixing: measured_ml ↔ 등가길이 환산된 mixing_line_dead_vol
exp_mix = sysm["mixing_line"]["measured_ml"]
seg_sum = sum(s["ml"] for s in sysm["mixing_line"]["segments"].values())
check("mixing 원장 내부일관(분할합=측정)", abs(exp_mix - seg_sum) <= 2e-4,
      f"측정 {exp_mix} vs 분할합 {seg_sum:.4f}")
check("mixing (가스T→센서→리액터)", abs(cfg.mixing_line_dead_vol - exp_mix) <= 5e-3,
      f"원장 {exp_mix:.4f} | 파생 {cfg.mixing_line_dead_vol:.4f} "
      f"(Δ {abs(cfg.mixing_line_dead_vol - exp_mix) * 1000:.2f}µL, 등가길이 환산)")
# reactor
exp_rct = sysm["reactor"]["measured_ml"]
check("reactor", abs(cfg.reactor_vol - exp_rct) <= 5e-3,
      f"원장 {exp_rct:.4f} | 파생 {cfg.reactor_vol:.4f} "
      f"(Δ {abs(cfg.reactor_vol - exp_rct) * 1000:.2f}µL, 등가길이 환산)")
# post / collection
exp_post = ledger_expected(sysm["post_reactor_vol_ml"])
if exp_post is None:
    warn("post_reactor_vol_ml 미실측", f"config {sp_json['post_reactor_vol_ml']} mL "
         "그대로 — t_head 수송에 직접 들어가는 최대 미검증 항목")
else:
    check("post_reactor (반응기→photo센서→아웃렛)",
          abs(sp_json["post_reactor_vol_ml"] - exp_post) <= 5e-4,
          f"원장 {exp_post:.4f} | config {sp_json['post_reactor_vol_ml']}")
    seg_post = sum(s["ml"] for s in
                   sysm["post_reactor_vol_ml"].get("segments", {}).values())
    if seg_post:
        check("post_reactor 원장 내부일관(분할합=길이합)",
              abs(exp_post - seg_post) <= 2e-4,
              f"길이합 {exp_post:.4f} vs 분할합 {seg_post:.4f}")
exp_col = sysm["collection_line_vol_ml"]["measured_ml"]
check("collection_line", abs(sp_json["collection_line_vol_ml"] - exp_col) <= 5e-4,
      f"원장 {exp_col} | config {sp_json['collection_line_vol_ml']}")
# 파생 합산 정체성: reagent = inlet + selector + valve_pump (원장 구간합 규칙)
for pname in prof["pumps"]:
    if pname not in settings:
        continue
    s = settings.get(pname, {})
    ident = s.get("tube_vol_inlet", 0) + s.get("tube_vol_selector", 0) \
        + s.get("tube_vol_valve_pump", 0)
    check(f"{pname} 합산 정체성 reagent=inlet+sel+vp",
          abs(ident - s.get("tube_vol_reagent", 0)) <= 2e-4,
          f"{ident:.4f} vs {s.get('tube_vol_reagent')}")

# ══ PART B: 엔진 ↔ 독립 물리모델 (실측값 + 실 entry_map) ══════════════
print("=== PART B: _compute_plug_timing ↔ 독립 재구현 (실측 시나리오) ===")
from engine.strict_engine import StrictSequenceEngine, hte_build_profile

# @codesyncer(2026-08-13): N 펌프 일반형 — 그룹 구성(A/D 만 등)이 바뀌어도
#   감사가 돌아야 한다. entry_map 은 '활성 펌프 전원 기재+단조'만 단언,
#   시나리오는 활성 목록에서 동적 생성 (구 4펌프 하드코딩이 IndexError 낸 사고).
PUMPS = list(cfg.ACTIVE_PUMPS)                      # 예: ['Group A', 'Group_D']
N = len(PUMPS)
EM = {p: max(1, int((cfg.tjunction_entry_map or {}).get(p, 1) or 1)) for p in PUMPS}
TJ = dict(cfg.tjunction_line_vols)
INJ = {p: cfg.line_vol_pump_merge[p] + cfg.valve_internal_vol[p] for p in PUMPS}
SRC = {p: cfg.line_vol_valve_pump[p] + cfg.selector_internal_vol[p] for p in PUMPS}
SRC_FIRST = {p: SRC[p] + cfg.line_vol_inlet[p] for p in PUMPS}
check("entry_map 활성 펌프 전원 기재",
      all(p in (cfg.tjunction_entry_map or {}) for p in PUMPS),
      f"활성 {PUMPS} / 맵 {cfg.tjunction_entry_map}")
_em_vals = [EM[p] for p in PUMPS]
check("entry_map 행순 단조(배관도 표현 가능)",
      all(_em_vals[i] <= _em_vals[i + 1] for i in range(N - 1)), str(EM))
check("tj 실측 로드", TJ == {1: 0.0905, 2: 0.0452}, str(TJ))


def ref_model(flows, ordered, src, inj, tj, order, em):
    """독립 재구현 — 문서화된 물리 정의식 그대로 (엔진 코드 미참조).
    t_i = inj_i/f_i + Σ_{j=entry_i..maxseg} V_j/F_j,  F_j = Σ_{entry_q<=j} f_q
    purge = max(src_i/f_i), pre = (fifo? purge:0) + max(t_i)
    deficit = Σ f_i(purge−purge_i)/60, stagger_i = purge−purge_i (fifo)."""
    lifo = order == "lifo"
    if em:
        ent = {p: max(1, int(em.get(p, 1) or 1)) for p in ordered}
        maxseg = max((int(k) for k, v in tj.items() if float(v or 0) > 0), default=0)
    else:
        ent = {p: (1 if i <= 1 else i) for i, p in enumerate(ordered)}
        maxseg = len(ordered) - 2
    purge, tmax = 0.0, 0.0
    for p in ordered:
        f = float(flows.get(p, 0.0))
        if f <= 0:
            continue
        purge = max(purge, src.get(p, 0.0) / f * 60.0)
        t = inj.get(p, 0.0) / f * 60.0
        for j in range(ent[p], maxseg + 1):
            Fj = sum(float(flows.get(q, 0.0)) for q in ordered if ent[q] <= j)
            Vj = float(tj.get(j, 0.0) or 0.0)
            if Fj > 0 and Vj > 0:
                t += Vj / Fj * 60.0
        tmax = max(tmax, t)
    pre = tmax if lifo else purge + tmax
    dv, st = 0.0, {}
    for p, f in flows.items():
        f = float(f)
        if f <= 0:
            continue
        pi = src.get(p, 0.0) / f * 60.0
        if not lifo:
            dv += f * (purge - pi) / 60.0
        st[p] = 0.0 if lifo else max(0.0, purge - pi)
    return purge, pre, dv, st


_ASYM = [0.2, 0.05, 0.1, 0.05, 0.08]
SCENARIOS = [
    (f"균등 0.1×{N} fifo", {p: .1 for p in PUMPS}, SRC, "fifo"),
    (f"균등 0.1×{N} lifo", {p: .1 for p in PUMPS}, SRC, "lifo"),
    ("비대칭", {p: r for p, r in zip(PUMPS, _ASYM)}, SRC, "fifo"),
    ("첫사용(인렛 포함) 균등", {p: .1 for p in PUMPS}, SRC_FIRST, "fifo"),
]
for _p in PUMPS:   # 각 채널 단독 (구식이 오계산하던 후행 진입 채널 포함)
    SCENARIOS.append((f"{_p} 단독", {q: (.1 if q == _p else 0.0) for q in PUMPS},
                      SRC, "lifo"))
_emax = max(_em_vals)
if _emax > min(_em_vals):   # 마지막 진입그룹 전용 (앞 구간 비통과 케이스)
    SCENARIOS.append((f"진입{_emax} 그룹 전용",
                      {q: (.1 if EM[q] == _emax else 0.0) for q in PUMPS},
                      SRC, "fifo"))
for name, flows, src, order in SCENARIOS:
    eng = StrictSequenceEngine._compute_plug_timing(
        flows, PUMPS, src, INJ, TJ, order, entry_map=EM)
    ref = ref_model(flows, PUMPS, src, INJ, TJ, order, EM)
    ok = (abs(eng[0] - ref[0]) < 1e-9 and abs(eng[1] - ref[1]) < 1e-9
          and abs(eng[2] - ref[2]) < 1e-9
          and all(abs(eng[3][k] - ref[3][k]) < 1e-9 for k in ref[3])
          and set(eng[3]) == set(ref[3]))
    check(f"[{name}] purge/pre/deficit/stagger 일치", ok,
          f"pre={eng[1]:.2f}s purge={eng[0]:.2f}s")

# 정보: 구 페어와이즈 식이었으면 생겼을 오차 (실측 수치)
print("  --- 구 페어와이즈 식 대비 오차(정보) ---")
P_LAST = PUMPS[-1]
for name, flows in ((f"균등 0.1×{N}", {p: .1 for p in PUMPS}),
                    (f"{P_LAST} 단독 0.1",
                     {q: (.1 if q == P_LAST else 0.0) for q in PUMPS})):
    _, pre_new, _, _ = StrictSequenceEngine._compute_plug_timing(
        flows, PUMPS, {p: 0 for p in PUMPS}, INJ, TJ, "lifo", entry_map=EM)
    _, pre_old, _, _ = StrictSequenceEngine._compute_plug_timing(
        flows, PUMPS, {p: 0 for p in PUMPS}, INJ, TJ, "lifo")
    F = sum(flows.values())
    print(f"    {name}: 신 {pre_new:.2f}s vs 구 {pre_old:.2f}s "
          f"(Δ {pre_new - pre_old:+.2f}s = {(pre_new - pre_old) * F / 60 * 1000:+.1f}µL @F={F})")

# ══ PART C: entry_map 배선 (표준 + HTE) ═══════════════════════════════
print("=== PART C: entry_map 배선 경로 ===")
src_txt = open(os.path.join("engine", "strict_engine.py"), encoding="utf-8").read()
check("표준 경로가 entry_map 전달", "entry_map=_tj_entry" in src_txt)
check("HTE 경로가 entry_map 전달", "entry_map=tj_entry or None" in src_txt)
check("엔진 호출부가 cfg.tjunction_entry_map 소비",
      'getattr(self.cfg, "tjunction_entry_map"' in src_txt)

dv = {"inlet": dict(cfg.line_vol_inlet), "valve_pump": dict(cfg.line_vol_valve_pump),
      "selector": dict(cfg.selector_internal_vol),
      "switcher": dict(cfg.valve_internal_vol),
      "pump_merge": dict(cfg.line_vol_pump_merge)}
steps = [dict(flows={P_LAST: 0.1}, F=0.1, v_slug=0.1, q_equiv=1.0,
              ports={P_LAST: 2})]
common = dict(reactor_vol=cfg.reactor_vol, mixing=cfg.mixing_line_dead_vol,
              post=float(sp_json["post_reactor_vol_ml"]), vol_collection=0.24,
              deadvols=dv, active_pumps=PUMPS, tj=TJ, purge_factor=1.0,
              purge_order="fifo", override_delay=None, v_spacer=0.05,
              v_wash_sol=0.0, v_wash_gas=0.1)
vh_new = hte_build_profile([dict(s) for s in steps], tj_entry=EM, **common)["v_head"]
vh_old = hte_build_profile([dict(s) for s in steps], tj_entry=None, **common)["v_head"]
# 마지막 진입그룹 단독: 구식(페어와이즈)은 후행 채널이 공유구간 미통과 —
# 차이는 정확히 tj[e_max]=tj[2] 부피여야 함 (entry 2 채널이 있을 때)
if EM[P_LAST] == max(_em_vals) and max(_em_vals) == 2:
    check(f"HTE v_head 차등 = tj[2] 부피({P_LAST} 단독)",
          abs((vh_new - vh_old) - TJ[2]) < 1e-6,
          f"Δv_head {vh_new - vh_old:.4f} mL vs tj[2] {TJ[2]} mL")

# ══ PART D: t_head 수송 구성요소 분해 (정보) ══════════════════════════
print(f"=== PART D: t_head 구성요소 (균등 0.1×{N}, F={0.1 * N:.1f}) ===")
F = 0.1 * N
flows = {p: 0.1 for p in PUMPS}
purge, pre_f, dvol, stag = StrictSequenceEngine._compute_plug_timing(
    flows, PUMPS, SRC, INJ, TJ, "fifo", entry_map=EM)
_, pre_l, _, _ = StrictSequenceEngine._compute_plug_timing(
    flows, PUMPS, SRC, INJ, TJ, "lifo", entry_map=EM)
trans = (cfg.reactor_vol + cfg.mixing_line_dead_vol
         + float(sp_json["post_reactor_vol_ml"])) / F * 60.0
print(f"    수송(reactor {cfg.reactor_vol:.4f} + mixing {cfg.mixing_line_dead_vol:.4f}"
      f" + post {sp_json['post_reactor_vol_ml']:.4f})/F = {trans:.1f}s")
print(f"    주입경로 도달(lifo pre) = {pre_l:.1f}s | fifo pre(=purge {purge:.1f}s 선행"
      f" 포함) = {pre_f:.1f}s | deficit {dvol:.4f} mL")
post_share = float(sp_json["post_reactor_vol_ml"]) / (
    cfg.reactor_vol + cfg.mixing_line_dead_vol + float(sp_json["post_reactor_vol_ml"]))
if exp_post is None:
    print(f"    ⚠ post_reactor {sp_json['post_reactor_vol_ml']} mL 미실측 = 수송부피의 "
          f"{post_share * 100:.0f}% — 실측 시 t_head 오차 지배 항목")
else:
    print(f"    post_reactor {sp_json['post_reactor_vol_ml']} mL 실측 반영 "
          f"(수송부피의 {post_share * 100:.0f}%)")
if sp_json.get("outlet_switch_delay_sec"):
    print(f"    (참고) outlet_switch_delay_sec override 활성: "
          f"{sp_json['outlet_switch_delay_sec']}s — 위 공식 대신 이 값이 최우선")

print()
print("RESULT:", ("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
      + (f" | WARN {len(warns)}: {warns}" if warns else ""))
sys.exit(1 if fails else 0)
