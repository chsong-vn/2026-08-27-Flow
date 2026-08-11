# -*- coding: utf-8 -*-
"""stock_stoich 양론엔진 검증 — 참조 F-LMJ 실측값 역산 + XEC 칵테일 + 엣지."""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.stock_stoich import (compute_stock, to_export_components,
                                 to_export_solvents, component_line,
                                 recipe_summary, limiting_index)

ok = True
def chk(c, m, detail=""):
    global ok
    print(("PASS" if c else "FAIL") + ": " + m + (f"  {detail}" if detail else ""))
    ok = ok and bool(c)


# ── 1) 참조 F-LMJ 역산: benzoyl chloride 1 g, MW 140.57 ──
#    참조 JSON: mmol 7.113893433876361, massG 1, concM 1.125
#    → total_vol = mmol/concM = 6.323460830112321 mL
rec = compute_stock({
    "name": "ref", "total_volume_ml": 7.113893433876361 / 1.125,
    "solvents": [{"name": "Toluene", "ratio": 1}],
    "components": [{"reagent": "benzoyl chloride", "mw": 140.57,
                    "eq": 1.0, "limiting": True, "mass_g": 1.0}],
})
c0 = rec["components"][0]
chk(abs(c0["mmol"] - 7.113893433876361) < 1e-9, "참조 mmol 역산 (1g/140.57)",
    f"({c0['mmol']:.12f})")
chk(abs(c0["mass_g"] - 1.0) < 1e-9, "massG 왕복 = 1.000")
chk(abs(rec["conc_m"] - 1.125) < 1e-9, "stock concM = 1.125", f"({rec['conc_m']})")
chk(rec["valid"], "참조 케이스 valid")
# 이론수율(참조 theoreticalYieldG 1.4090559863413248 = lim_mmol × 198.071/1000)
ty = c0["mmol"] * 198.071 / 1000.0
chk(abs(ty - 1.4090559863413248) < 1e-12, "참조 theoreticalYieldG 재현", f"({ty:.12f})")

# ── 2) XEC 촉매칵테일 (스크린샷 Pump #2 유형) ──
xec = compute_stock({
    "name": "XEC 촉매칵테일", "total_volume_ml": 5.0,
    "solvents": [{"name": "DMA", "ratio": 1}],
    "components": [
        {"reagent": "NiCl2·dme", "mw": 222.7, "eq": 1.0, "limiting": True, "mmol": 0.10},
        {"reagent": "dtbbpy", "mw": 268.4, "eq": 1.2},
        {"reagent": "Ir photocat", "mw": 1120.0, "eq": 0.02},
        {"reagent": "TTMSS", "mw": 248.66, "eq": 3.0, "density": 0.806},
    ],
})
cs = xec["components"]
chk(abs(cs[0]["mass_g"] - 0.02227) < 1e-9, "Ni 질량 22.27 mg", f"({cs[0]['mass_g']*1000:.3f} mg)")
chk(abs(cs[1]["mmol"] - 0.12) < 1e-12, "dtbbpy mmol = eq 비례 0.12")
chk(abs(cs[1]["mass_g"] - 0.032208) < 1e-12, "dtbbpy 질량 32.208 mg", f"({cs[1]['mass_g']*1000:.3f} mg)")
chk(abs(cs[3]["mmol"] - 0.30) < 1e-12, "TTMSS mmol 0.30")
chk(abs(cs[3]["vol_ml"] - 0.30 * 248.66 / 1000.0 / 0.806) < 1e-9,
    "TTMSS 부피(밀도)", f"({cs[3]['vol_ml']:.4f} mL)")
chk(abs(xec["conc_m"] - 0.02) < 1e-12, "stock concM = limiting 0.02 M")
chk(abs(cs[2]["molarity"] - 0.0004) < 1e-12, "Ir molarity 0.0004 M")
tot = sum(c["wt_pct"] for c in cs)
chk(abs(tot - 100.0) < 1e-9, "wt% 합 100")
chk(xec["valid"], "XEC valid")

# ── 3) limiting eq ≠ 1.0 (비례 기준 정확성) ──
r3 = compute_stock({
    "total_volume_ml": 2.0,
    "components": [
        {"reagent": "A", "mw": 100.0, "eq": 2.0, "limiting": True, "mmol": 0.4},
        {"reagent": "B", "mw": 50.0, "eq": 1.0},
    ],
})
chk(abs(r3["components"][1]["mmol"] - 0.2) < 1e-12,
    "lim eq=2: B(eq1) mmol = 0.4×(1/2)", f"({r3['components'][1]['mmol']})")

# ── 4) 엣지: 앵커 없음 / MW 없음 / 총부피 0 ──
r4 = compute_stock({"total_volume_ml": 5.0,
                    "components": [{"reagent": "X", "mw": 100.0, "eq": 1.0}]})
chk(not r4["valid"] and any("mmol" in e for e in r4["errors"]), "앵커 부재 → invalid")
r5 = compute_stock({"total_volume_ml": 0,
                    "components": [{"reagent": "X", "mw": 100.0, "eq": 1.0,
                                    "limiting": True, "mmol": 1.0}]})
chk(not r5["valid"] and any("부피" in e for e in r5["errors"]), "총부피 0 → invalid")
r6 = compute_stock({"total_volume_ml": 1.0,
                    "components": [{"reagent": "X", "mw": 0, "eq": 1.0,
                                    "limiting": True, "mmol": 1.0}]})
chk(not r6["valid"], "MW 0 → invalid(경고)")
chk(limiting_index([{"a": 1}, {"limiting": True}]) == 1, "limiting_index")

# ── 4b) molarity 앵커 (A안 인라인: 농도만 입력, 총부피 없음) ──
r7 = compute_stock({"total_volume_ml": 0,
                    "components": [
                        {"reagent": "NiCl2·dme", "molarity": 0.02, "limiting": True},
                        {"reagent": "dtbbpy", "molarity": 0.024},
                        {"reagent": "TTMSS", "molarity": 0.06}]})
chk(r7["valid"], "인라인(molarity만·부피0) → valid", str(r7["errors"]))
chk(abs(r7["conc_m"] - 0.02) < 1e-12, "인라인 conc_m = limiting 농도")
chk(abs(r7["components"][1]["eq"] - 1.2) < 1e-12, "인라인 eq 역산 (0.024/0.02)",
    f"({r7['components'][1]['eq']})")
chk(abs(r7["components"][2]["molarity"] - 0.06) < 1e-12, "인라인 성분 농도 보존")
chk(r7["components"][1]["mmol"] == 0.0, "부피 없음 → mmol 0 (칭량은 다이얼로그)")
# molarity 앵커 + 총부피 → mmol 도 산출
r8 = compute_stock({"total_volume_ml": 5.0,
                    "components": [
                        {"reagent": "Ni", "molarity": 0.02, "limiting": True},
                        {"reagent": "L", "molarity": 0.024}]})
chk(abs(r8["components"][0]["mmol"] - 0.10) < 1e-12, "molarity×부피 → lim mmol 0.10")
chk(abs(r8["components"][1]["mmol"] - 0.12) < 1e-12, "molarity×부피 → 성분 mmol 0.12")

# ── 5) export 변환 ──
exp = to_export_components({
    "total_volume_ml": 5.0,
    "components": [
        {"reagent": "NiCl2·dme", "mw": 222.7, "eq": 1.0, "limiting": True, "mmol": 0.10},
        {"reagent": "dtbbpy", "mw": 268.4, "eq": 1.2},
    ],
})
chk(set(exp[0].keys()) == {"reagent", "limiting", "molecularWeightGPerMol",
                           "eq", "mmol", "massG", "molarityM",
                           "densityGPerML", "volumeML"},
    "export component 키셋 (+densityGPerML/volumeML — 용매배분 물질수지용)")
chk(exp[0]["limiting"] is True and abs(exp[0]["molarityM"] - 0.02) < 1e-12,
    "export limiting molarityM")
chk(abs(exp[1]["massG"] - 0.032208) < 1e-12, "export massG")
sol = to_export_solvents({"solvents": [{"name": "DMA", "ratio": 1, "volumeML": 2.5}]})
chk(sol == [{"name": "DMA", "ratio": 1.0, "volumeML": 2.5}], "export solvents")

# ── 6) 표시 헬퍼 ──
line = component_line(xec["components"][0])
chk(line.startswith("● NiCl2·dme") and "22.27 mg" in line, "component_line", f"[{line}]")
summ = recipe_summary(xec)
chk("4성분" in summ and "DMA" in summ and "0.02 M" in summ, "recipe_summary")

print()
print("=== " + ("ALL PASS" if ok else "SOME FAIL") + " ===")
sys.exit(0 if ok else 1)
