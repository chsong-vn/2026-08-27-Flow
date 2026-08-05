# -*- coding: utf-8 -*-
"""다성분 stock 화학양론 순수엔진 — UI/하드웨어 무의존.

개념(2026-07-13 설계): '혼합 stock = 포트의 속성(레시피)'.
  recipe = {name, total_volume_ml, solvents:[{name, ratio}],
            components:[{reagent, mw, eq, limiting, density?, mmol?|mass_g?}]}
  limiting 성분의 mmol(또는 mass_g)이 앵커 — 나머지는 eq 비례로 파생.

파생 규칙:
  mmol_i     = (eq_i / eq_lim) × mmol_lim
  mass_g_i   = mmol_i × MW_i / 1000
  vol_ml_i   = mass_g_i / density_i         (density>0 일 때만, 액상 참고치)
  molarity_i = mmol_i / total_volume_ml     (mmol/mL = M)
  wt_pct_i   = mass_g_i / Σ mass_g × 100    (용질 기준)
  stock concM = limiting 성분의 molarity    (유속 양론이 소비하는 대표 농도)

F-SCH/F-LMJ export 는 to_export_components() 사용 — 참조 스키마
component(6키: eq/mmol/massG/reagent/limiting/molecularWeightGPerMol) 방향으로
notebook_export._normalize_component 가 최종 정리한다.
"""
import math


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def limiting_index(components):
    """limiting=True 인 성분 index (없으면 0)."""
    for i, c in enumerate(components or []):
        if c.get("limiting"):
            return i
    return 0


def compute_stock(recipe):
    """recipe(dict) → 파생값이 채워진 새 recipe dict (원본 불변).

    반환 recipe["components"][i] 에 mmol/mass_g/vol_ml/molarity/wt_pct 채움,
    recipe["conc_m"] = limiting molarity, recipe["valid"] = bool,
    recipe["errors"] = [str]  (limiting 앵커 부재 등).
    """
    rec = dict(recipe or {})
    comps = [dict(c) for c in (rec.get("components") or [])]
    total_ml = _f(rec.get("total_volume_ml"))
    errors = []

    if not comps:
        rec.update(components=[], conc_m=0.0, valid=False,
                   errors=["성분이 없습니다"])
        return rec

    li = limiting_index(comps)
    lim = comps[li]
    lim["limiting"] = True
    for i, c in enumerate(comps):
        if i != li:
            c["limiting"] = False

    # ── limiting 앵커: mmol 직접 > mass_g→mmol > molarity(×총부피) ──
    # @codesyncer(A안 인라인, 2026-07-13): 그리드 인라인 입력은 성분별 '농도(M)'만
    #   치는 흐름 — molarity 를 제3 앵커로 허용. 총부피 없으면 mmol/질량은 0으로
    #   두되 molarity 기반 값(conc_m·eq 비)은 성립(연구노트 칭량 상세는 ⚗ 다이얼로그).
    lim_mw = _f(lim.get("mw"))
    lim_molar_in = _f(lim.get("molarity"))
    lim_mmol = _f(lim.get("mmol"))
    if lim_mmol <= 0:
        mass_g = _f(lim.get("mass_g"))
        if mass_g > 0 and lim_mw > 0:
            lim_mmol = mass_g / lim_mw * 1000.0
        elif lim_molar_in > 0 and total_ml > 0:
            lim_mmol = lim_molar_in * total_ml
        elif lim_molar_in <= 0:
            errors.append("limiting 성분의 mmol 또는 (mass_g + MW) 또는 농도(M)가 필요합니다")
    lim_eq = _f(lim.get("eq"), 1.0) or 1.0
    # limiting 유효 몰농도 (eq 비 산출 기준)
    lim_molar = lim_molar_in if lim_molar_in > 0 else (
        (lim_mmol / total_ml) if (lim_mmol > 0 and total_ml > 0) else 0.0)

    # ── 성분별 파생 ──
    total_mass = 0.0
    for c in comps:
        mw = _f(c.get("mw"))
        molar_in = _f(c.get("molarity"))
        if molar_in > 0 and c is not lim:
            # 인라인: 성분 자체 농도 명시 → eq 는 농도비로 역산
            molarity = molar_in
            eq = (molar_in / lim_molar) if lim_molar > 0 else _f(c.get("eq"), 1.0)
            mmol = molar_in * total_ml if total_ml > 0 else 0.0
        else:
            eq = _f(c.get("eq"), 1.0)
            mmol = lim_mmol * (eq / lim_eq) if lim_mmol > 0 else 0.0
            if c is lim and lim_mmol > 0:
                mmol = lim_mmol
            molarity = (mmol / total_ml) if total_ml > 0 else (
                lim_molar * (eq / lim_eq) if lim_molar > 0 else 0.0)
            if c is lim and lim_molar > 0:
                molarity = lim_molar
        mass_g = mmol * mw / 1000.0
        dens = _f(c.get("density"))
        c["eq"] = eq
        c["mmol"] = mmol
        c["mass_g"] = mass_g
        c["vol_ml"] = (mass_g / dens) if dens > 0 else 0.0
        c["molarity"] = molarity
        total_mass += mass_g
        if mw <= 0 and lim_mmol > 0:
            errors.append(f"{c.get('reagent', '?')}: MW 미입력")

    for c in comps:
        c["wt_pct"] = (c["mass_g"] / total_mass * 100.0) if total_mass > 0 else 0.0

    if total_ml <= 0 and lim_mmol > 0:
        errors.append("총 부피(total_volume_ml)가 필요합니다")

    rec["components"] = comps
    rec["conc_m"] = comps[li]["molarity"]
    rec["valid"] = not errors
    rec["errors"] = errors
    return rec


def to_export_components(recipe):
    """계산된 recipe → F-SCH export component 리스트.

    notebook_export._normalize_component 가 소비하는 형태(초과 키는 거기서 제거).
    molarityM 은 build_fsch 의 stock concM(=limiting) 유도에 쓰인다.
    """
    rec = compute_stock(recipe)
    out = []
    for c in rec["components"]:
        out.append({
            "reagent": c.get("reagent", ""),
            "limiting": bool(c.get("limiting")),
            "molecularWeightGPerMol": _f(c.get("mw")),
            "eq": _f(c.get("eq"), 1.0),
            "mmol": _f(c.get("mmol")),
            "massG": _f(c.get("mass_g")),
            "molarityM": _f(c.get("molarity")),
            # 밀도·원액부피 누락 시 notebook_export 의 원액 volumeML(=massG/density)이
            # 0 이 되고, save() 의 용매 자동배분 Σ성분도 0 으로 잡혀 용매가 과대
            # (총부피 전량) 기입되며 Σ용매+Σ성분=총부피 불변식이 깨짐 (2026-07-14)
            "densityGPerML": _f(c.get("density")),
            "volumeML": _f(c.get("vol_ml")),
        })
    return out


def to_export_solvents(recipe):
    """recipe.solvents → F-SCH solvents [{name, ratio}] (+volumeML 보존 시 전달)."""
    out = []
    for s in (recipe or {}).get("solvents") or []:
        d = {"name": s.get("name", ""), "ratio": _f(s.get("ratio"), 1.0)}
        if _f(s.get("volumeML")) > 0:
            d["volumeML"] = _f(s.get("volumeML"))
        out.append(d)
    return out


def component_line(c):
    """자식행/툴팁용 한 줄 요약 — '● NiCl2·dme · MW 222.7 · eq 1.00 · 0.100 mmol · 22.3 mg'."""
    mark = "●" if c.get("limiting") else "○"
    parts = [f"{mark} {c.get('reagent', '') or '(이름없음)'}"]
    if _f(c.get("mw")) > 0:
        parts.append(f"MW {_f(c.get('mw')):g}")
    parts.append(f"eq {_f(c.get('eq'), 1.0):g}")
    if _f(c.get("mmol")) > 0:
        parts.append(f"{_f(c.get('mmol')):.4g} mmol")
    mg = _f(c.get("mass_g")) * 1000.0
    if mg > 0:
        parts.append(f"{mg:.4g} mg")
    if _f(c.get("vol_ml")) > 0:
        parts.append(f"{_f(c.get('vol_ml')):.3g} mL")
    return " · ".join(parts)


def recipe_summary(recipe):
    """포트 셀 툴팁용 여러 줄 요약."""
    rec = compute_stock(recipe)
    lines = [f"{rec.get('name') or '혼합 stock'} — {len(rec['components'])}성분, "
             f"{_f(rec.get('total_volume_ml')):g} mL, 기준 {rec.get('conc_m', 0):.4g} M"]
    sol = ", ".join(f"{s.get('name')}({_f(s.get('ratio'), 1):g})"
                    for s in rec.get("solvents") or [] if s.get("name"))
    if sol:
        lines.append(f"용매: {sol}")
    lines += [component_line(c) for c in rec["components"]]
    return "\n".join(lines)
