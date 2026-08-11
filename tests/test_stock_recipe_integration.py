# -*- coding: utf-8 -*-
"""다성분 stock 통합 검증 (offscreen — QtWebEngine 불필요).

 A. StockRecipeDialog: 로드/파싱/양론 재계산/프리셋/적용(recipe 반환)
 B. export 배선: recipes_by_port → 스텝별 포트 매칭(HTE 시나리오),
    참조 F-LMJ 스키마 키 완전일치 유지
"""
import os, sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv)

ok = True
def chk(c, m, detail=""):
    global ok
    print(("PASS" if c else "FAIL") + ": " + m + (f"  {detail}" if detail else ""))
    ok = ok and bool(c)


# ══════════════════════════════════════════════════════════════
# A. 레시피 편집 다이얼로그
# ══════════════════════════════════════════════════════════════
print("[A] StockRecipeDialog")
from ui.dialog_stock_recipe import StockRecipeDialog, C_NAME, C_MW, C_EQ, C_MMOL, C_DENS

XEC = {
    "name": "XEC 촉매칵테일", "total_volume_ml": 5.0,
    "solvents": [{"name": "DMA", "ratio": 1.0}],
    "components": [
        {"reagent": "NiCl2·dme", "mw": 222.7, "eq": 1.0, "limiting": True, "mmol": 0.10},
        {"reagent": "dtbbpy", "mw": 268.4, "eq": 1.2},
        {"reagent": "TTMSS", "mw": 248.66, "eq": 3.0, "density": 0.806},
    ],
}
lib = {"Ir photocat": {"mw": 1120.0, "density": 0.0}}
dlg = StockRecipeDialog("Group_B", 2, recipe=XEC, presets={}, reagent_lib=lib)
chk(dlg.tbl.rowCount() == 3, "레시피 로드 → 3행")
chk(dlg.ed_name.text() == "XEC 촉매칵테일", "레시피명 로드")
chk(dlg.ed_solvent.text() == "DMA", "용매 로드(비율1 축약)")
chk("0.02" in dlg.lbl_conc.text(), "기준 concM 라벨", f"[{dlg.lbl_conc.text()}]")
# 파생값 표시 (dtbbpy 질량 32.208 mg → 32.21 표기)
chk(dlg.tbl.item(1, C_MMOL).text() == "0.12", "dtbbpy mmol 파생 표시",
    f"[{dlg.tbl.item(1, C_MMOL).text()}]")

# 성분 추가 + 라이브러리 MW 자동채움
dlg._add_row()
r = dlg.tbl.rowCount() - 1
dlg.tbl.item(r, C_NAME).setText("Ir photocat")
chk(dlg.tbl.item(r, C_MW).text() == "1120", "시약명 입력 → 라이브러리 MW 자동채움",
    f"[{dlg.tbl.item(r, C_MW).text()}]")
dlg.tbl.item(r, C_EQ).setText("0.02")

rec = dlg.current_recipe()
chk(len(rec["components"]) == 4, "current_recipe 4성분")
chk(rec["components"][3]["eq"] == 0.02, "신규 성분 eq 파싱")

# 프리셋 저장 (메시지박스 없이 내부 상태만 — QMessageBox 는 offscreen에서도 exec 없이 OK)
from unittest.mock import patch
with patch("ui.dialog_stock_recipe.QMessageBox"):
    dlg._save_preset()
chk("XEC 촉매칵테일" in dlg.presets, "프리셋 저장")

# 적용 → recipe (계산 포함)
with patch("ui.dialog_stock_recipe.QMessageBox"):
    dlg._accept_recipe()
chk(dlg.recipe is not None and dlg.recipe.get("valid"), "적용 → 유효 recipe")
chk(abs(dlg.recipe["conc_m"] - 0.02) < 1e-12, "적용 recipe concM")

# 혼합 해제
dlg2 = StockRecipeDialog("Group_B", 2, recipe=XEC)
dlg2._clear_recipe()
chk(dlg2.recipe == {}, "혼합 해제 → 빈 dict")

# 프리셋 저장/삭제 — 인라인 피드백 + 즉시 영속화 콜백 (모달 없음)
calls = []
dlg4 = StockRecipeDialog("Group A", 4, recipe=XEC, presets={"OldPre": XEC},
                         on_presets_changed=lambda p: calls.append(set(p)))
dlg4.ed_name.setText("NewPre")
dlg4._save_preset()
chk("NewPre" in dlg4.presets and calls and "NewPre" in calls[-1],
    "프리셋 저장 → 즉시 영속화 콜백", str(calls[-1] if calls else None))
chk("저장됨" in dlg4.lbl_err.text(), "저장 인라인 피드백(모달 아님)")
chk(dlg4.cb_preset.currentText() == "NewPre", "저장 후 콤보 선택 동기")
dlg4.cb_preset.setCurrentText("OldPre")
dlg4._del_preset()
chk("OldPre" not in dlg4.presets and "OldPre" not in calls[-1],
    "프리셋 삭제 → 콜백 반영")
chk("삭제됨" in dlg4.lbl_err.text(), "삭제 인라인 피드백")

# 유효성 거부: 앵커 없음
dlg3 = StockRecipeDialog("Group A", 3)
dlg3.tbl.item(0, C_NAME).setText("X")
dlg3.tbl.item(0, C_MW).setText("100")
with patch("ui.dialog_stock_recipe.QMessageBox") as mb:
    dlg3._accept_recipe()
chk(dlg3.recipe is None and mb.warning.called, "앵커 없음 → 적용 거부+경고")


# ══════════════════════════════════════════════════════════════
# B. export 배선 — HTE 스텝별 포트 매칭
# ══════════════════════════════════════════════════════════════
print("[B] export recipes_by_port")
from core.notebook_export import NotebookExporter

cfg = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "hardware_config.json"), encoding="utf-8"))
lib2 = {"6-Bromoquinazoline": {"mw": 209.05, "density": 0.0, "smiles": "Brc1ccc2ncncc12"}}

def mk_step(portA, portB):
    return {"temp": 25.0, "residence_time": 10.0, "vol_ml": 0.5,
            "flows": {"Group A": 0.5, "Group_B": 0.5},
            "inlet_ports": {"Group A": portA, "Group_B": portB},
            "meta": {"Group A": {"name": "6-Bromoquinazoline", "conc": 0.4},
                     "Group_B": {"name": "XEC 촉매칵테일", "conc": 0.02}}}

# HTE 시나리오: Group A 는 스텝별 포트 2→3 (단일시약), Group_B 는 포트 2 고정(칵테일)
plan = [mk_step(2, 2), mk_step(3, 2)]
eqs = [{"Group A": 1.0, "Group_B": 1.0}] * 2
recipes = {("Group_B", 2): dict(XEC)}

exp = NotebookExporter()
out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "temp", "_test_notebook_stock")
os.makedirs(out_dir, exist_ok=True)
paths = exp.save(plan, None, note_code="F-SCH-TEST-STOCK", revision=1,
                 reaction_type="Ni/Photoredox XEC", cfg_data=cfg,
                 eqs_by_step=eqs, reagent_lib=lib2,
                 recipes_by_port=recipes, out_dir=out_dir,
                 products=[{"name": "product-X", "molecularWeightGPerMol": 300.0}])
chk(len(paths) == 2, "스텝 2개 저장")

d1 = json.load(open(paths[0], encoding="utf-8"))
d2 = json.load(open(paths[1], encoding="utf-8"))
sp1 = {s["pumpName"]: s for s in d1["content"]["stockParameters"]}
# pumpName 은 레터로 축약 (Group A→A, Group_B→B)
chk(set(sp1.keys()) == {"A", "B"}, "pumpName 레터", str(set(sp1.keys())))
chk(len(sp1["B"]["components"]) == 3, "B(칵테일 포트2) → 3성분",
    f"({len(sp1['B']['components'])})")
chk(len(sp1["A"]["components"]) == 1, "A(단일) → 1성분")
comp_keys = set(sp1["B"]["components"][0].keys())
_req = {"eq", "mmol", "massG", "reagent", "limiting", "pumpLimiting",
        "molecularWeightGPerMol", "densityGPerML", "weightPercent", "volumeML"}
chk(comp_keys == _req,
    "component F-SCH 키셋 (+weightPercent, molarityM 미방출)", str(sorted(comp_keys)))
chk(abs(sp1["B"]["concM"] - 0.02) < 1e-9, "B concM = limiting 0.02")
# 용매 부피 자동배분: 총 5.0 − TTMSS 원액(0.074598g/0.806=0.092553mL) = 4.907447
chk(sp1["B"]["solvents"] == [{"name": "<p>DMA</p>", "ratio": 1.0,
                              "volumeML": 4.907447}],
    "B solvents 부피 자동배분(총부피−성분원액, 액체시약 밀도 반영)",
    str(sp1["B"]["solvents"]))
chk(abs(sp1["B"]["volumeML"] - 5.0) < 1e-9,
    "불변식: stock 총부피 = Σ용매 + Σ성분 = 레시피 총부피", str(sp1["B"]["volumeML"]))
chk(abs(sp1["B"]["flowRateMLPerMin"] - 0.5) < 1e-12, "B flowRate")
ni = next(c for c in sp1["B"]["components"] if "NiCl2" in c["reagent"])
chk(ni["pumpLimiting"] is True and abs(ni["massG"] - 0.02227) < 1e-9,
    "Ni pumpLimiting+massG")
# 제품 이론수율 = limiting mmol(0.10) × 300/1000 = 0.03 g — product 는 이제 '객체'
chk(abs(d1["content"]["product"]["theoreticalYieldG"] - 0.03) < 1e-9,
    "theoreticalYieldG (limiting 기반, product=객체)",
    f"({d1['content']['product']['theoreticalYieldG']})")
chk(all(k in d1["content"] for k in ("procedure", "lcms", "nmrSpectra", "tlc")),
    "F-SCH 필수 섹션 4종 존재")

# 스텝2: Group A 포트3(레시피 없음) → 여전히 1성분, B 는 칵테일 유지
sp2 = {s["pumpName"]: s for s in d2["content"]["stockParameters"]}
chk(len(sp2["A"]["components"]) == 1 and len(sp2["B"]["components"]) == 3,
    "스텝2 포트매칭 (A포트3=단일, B포트2=칵테일)")

# 참조 스키마 키 완전일치 — 진짜 F-SCH 폼 실물(플랫폼 export, 2026-07-14 교체)
ref = json.load(open(r"C:\Users\gogoc\Downloads\F-SCH-001-007-rev-2.json", encoding="utf-8"))
def keyset(d):
    if isinstance(d, dict):
        return {k: keyset(v) for k, v in d.items()}
    if isinstance(d, list):
        return keyset(d[0]) if d else "[]"
    return "leaf"
def diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append(f"MISSING {path}.{k}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        for k in b:
            if k not in a:
                out.append(f"EXTRA {path}.{k}")
    return out
# molarityM(실물 선택필드, 우린 미방출)·weightPercent(우리 추가, 사용자 결정
# '순도 기본 100%' — 실물 rev-2 엔 비어 미기록) 은 diff 제외
dd = [x for x in diff(keyset(ref), keyset(d1))
      if ".molarityM" not in x and ".weightPercent" not in x]
chk(not dd, "참조 F-SCH(실물) 키 완전일치 (선택필드 제외)", str(dd[:4]))
s = json.dumps(d1, ensure_ascii=False, allow_nan=False)
chk(bool(json.loads(s)), "strict JSON 유효")

# ══════════════════════════════════════════════════════════════
# D. 불변성 증명 — "양론식을 바꿔도 되는가?" → 양론식은 불변, export 필드만 변경
# ══════════════════════════════════════════════════════════════
print("[D] 양론식/화학값 불변성")
from engine.calculators import FlowCalculator

class _C:
    reactor_vol = 7.5          # rt 10 → 총유속 0.75 (플랫폼 스크린샷 예제와 동일)

_fl, _tot = FlowCalculator(_C()).calculate_flows(
    [0.40, 0.48, 0.02, 1.0], [1.0, 1.2, 0.02, 1.0], 10.0)
chk(abs(_tot - 0.75) < 1e-12 and abs(_fl[0] - 0.75 * 2.5 / 7) < 1e-9,
    "양론식 불변 — 앱 FlowCalculator ≡ 플랫폼 수식 (A=0.267857)",
    f"(A={_fl[0]:.6f})")
chk(abs(_fl[2] - 0.75 * 1.0 / 7) < 1e-9, "C 채널도 동치 (0.107143)")
# 화학값 불변: 순도 기본 1(100%) → massG = mmol·MW/1000 (기존과 동일)
_dtb = next(c for c in sp1["B"]["components"] if "dtbbpy" in c["reagent"])
chk(abs(_dtb["massG"] - _dtb["mmol"] * _dtb["molecularWeightGPerMol"] / 1000.0) < 1e-12,
    "massG 불변 (순도 100% 기본 → 순질량과 동일)")
chk(_dtb["weightPercent"] == 1.0, "weightPercent 기본 1 (=100%)")
chk("molarityM" not in _dtb, "molarityM 미방출 (Total Vol 이중계상 함정 제거)")

# ══════════════════════════════════════════════════════════════
# C. 인라인(그리드 ＋)으로 추가된 시약 → 연구노트 반영
#    _inline_add/_edit 가 만드는 정확한 형태(농도만, MW/총부피 없음)로 검증
# ══════════════════════════════════════════════════════════════
print("[C] 인라인 추가 시약 → 연구노트")
INLINE = {"name": "", "total_volume_ml": 0.0, "solvents": [],
          "components": [
              {"reagent": "NiCl2·dme", "molarity": 0.02, "smiles": "", "limiting": True},
              {"reagent": "dtbbpy", "molarity": 0.024, "smiles": ""},
              {"reagent": "TTMSS", "molarity": 0.06, "smiles": ""}]}
lib3 = {"NiCl2·dme": {"mw": 222.7}, "dtbbpy": {"mw": 268.4},
        "TTMSS": {"mw": 248.66, "density": 0.806},
        "6-Bromoquinazoline": {"mw": 209.05}}
paths_c = exp.save([mk_step(2, 2)], None, note_code="F-SCH-TEST-INLINE", revision=1,
                   reaction_type="XEC", cfg_data=cfg, eqs_by_step=eqs[:1],
                   reagent_lib=lib3, recipes_by_port={("Group_B", 2): INLINE},
                   out_dir=out_dir)
dc = json.load(open(paths_c[0], encoding="utf-8"))
spc = {s["pumpName"]: s for s in dc["content"]["stockParameters"]}
names = [c["reagent"] for c in spc["B"]["components"]]
chk(len(spc["B"]["components"]) == 3, "인라인 3성분 전부 노트에 포함", str(names))
chk("<p>dtbbpy</p>" in names and "<p>TTMSS</p>" in names, "추가 시약 이름 반영")
dtb = next(c for c in spc["B"]["components"] if "dtbbpy" in c["reagent"])
chk(abs(dtb["eq"] - 1.2) < 1e-9, "추가 시약 eq = 농도비(0.024/0.02)", str(dtb["eq"]))
chk(abs(dtb["molecularWeightGPerMol"] - 268.4) < 1e-9,
    "MW 라이브러리 보강(인라인은 MW 미입력)", str(dtb["molecularWeightGPerMol"]))
chk(abs(spc["B"]["concM"] - 0.02) < 1e-12, "concM = 첫(limiting) 시약 농도")
chk(dtb["mmol"] == 0.0 and dtb["massG"] == 0.0,
    "총부피 미설정 → mmol/massG 0 (⚗ 상세에서 부피 입력 시 채워짐)")

# 총부피를 ⚗ 다이얼로그에서 설정한 경우 → mmol/massG 산출
INLINE_V = dict(INLINE, total_volume_ml=5.0)
paths_v = exp.save([mk_step(2, 2)], None, note_code="F-SCH-TEST-INLINEV", revision=1,
                   reaction_type="XEC", cfg_data=cfg, eqs_by_step=eqs[:1],
                   reagent_lib=lib3, recipes_by_port={("Group_B", 2): INLINE_V},
                   out_dir=out_dir)
dv = json.load(open(paths_v[0], encoding="utf-8"))
spv = {s["pumpName"]: s for s in dv["content"]["stockParameters"]}
dtb2 = next(c for c in spv["B"]["components"] if "dtbbpy" in c["reagent"])
chk(abs(dtb2["mmol"] - 0.12) < 1e-9, "총부피 5mL → dtbbpy mmol 0.12", str(dtb2["mmol"]))
chk(abs(dtb2["massG"] - 0.032208) < 1e-9, "→ massG 0.032208 g", str(dtb2["massG"]))

# ══════════════════════════════════════════════════════════════
# F. 용매 합성 기입 + 사용 장비 요약(setup.mixerTypes) — 2026-07-14 사용자 요구
# ══════════════════════════════════════════════════════════════
print("[F] 용매 합성 기입 + 장비 요약")
# 용매 항목이 없는 레시피(INLINE_V) — 총부피 물질수지로 용매 부피 확정, 이름은 빈칸
_svv = spv["B"]["solvents"]
_ttm = next(c for c in spv["B"]["components"] if "TTMSS" in c["reagent"])
chk(abs(_ttm["volumeML"] - 0.092553) < 1e-6,
    "액체시약(TTMSS) 원액부피 = massG/density (밀도 전파)", str(_ttm["volumeML"]))
chk(len(_svv) == 1 and abs(_svv[0]["volumeML"] - 4.907447) < 1e-6,
    "용매 합성 기입 — volumeML = 총부피 − Σ성분 원액 = 4.907447", str(_svv))
chk(abs(spv["B"]["volumeML"] - 5.0) < 1e-6,
    "부피 보존 불변식 유지 — stock volumeML = Σ용매+Σ성분 = 총부피 5.0",
    str(spv["B"]["volumeML"]))

# 장비 요약 — roles 기반 집계 (장치 id 중복 제거, 아웃렛=3-way)
_cfg_eq = {"roles": {
    "pumps": [
        {"name": "Group A",
         "drivers": {"motor": "m1", "selector": "s1", "switcher": "w1"}},
        {"name": "Group_B",
         "drivers": {"motor": "m2", "selector": "s2", "switcher": "w2"}}],
    "outlet": {"driver_id": "w3"}, "gas": {"driver_id": "g1"},
    "heater": {"driver_id": "h1"}, "collector": {"driver_id": "c1"},
    "push_pump": {"driver_id": "pp1"}}, "system_params": {}}
_mx = NotebookExporter._equipment_summary(_cfg_eq)
chk(_mx == ["syringe pump ×2", "push pump ×1", "12-way selector valve ×2",
            "3-way valve ×3", "MFC ×1", "heater ×1", "fraction collector ×1"],
    "장비 요약 — 펌프/밸브(12way·3way)/MFC/히터/수집기 집계", str(_mx))
_deq = exp.build_fsch(mk_step(2, 2), _cfg_eq, eqs={}, reagent_lib={})
_mts = _deq["content"]["setup"]["mixerTypes"]
chk(_mts[0] == "t-junction" and "3-way valve ×3" in _mts and "MFC ×1" in _mts,
    "mixerTypes = 믹서 + 장비 요약 병기", str(_mts))
# 실 config export 에도 병기됐는지 (개수는 config 에 따라 변하므로 존재만 확인)
_mts1 = d1["content"]["setup"]["mixerTypes"]
chk(any("12-way" in m for m in _mts1) and any("3-way" in m for m in _mts1),
    "실 config export 에 밸브 요약 포함", str(_mts1))

# ══════════════════════════════════════════════════════════════
# E. 공식 연구노트 JSON Schema 검증 (플랫폼 제공 스키마, 2026-07-14)
#    additionalProperties:false — 허용 외 키가 하나라도 있으면 import 거부
# ══════════════════════════════════════════════════════════════
print("[E] 공식 스키마(jsonschema) 검증")
import jsonschema
_sch = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "core", "schemas", "f_sch.schema.json"),
                      encoding="utf-8"))
jsonschema.Draft202012Validator.check_schema(_sch)
_val = jsonschema.Draft202012Validator(_sch)
for _tag, _doc in (("다성분 레시피", d1), ("인라인 추가", dc), ("인라인+총부피", dv)):
    _errs = ["/".join(map(str, e.path)) + ": " + e.message[:90]
             for e in _val.iter_errors(_doc)]
    chk(not _errs, f"공식 스키마 통과 — {_tag}", str(_errs[:3]))

# 정리
import shutil
shutil.rmtree(out_dir, ignore_errors=True)

print()
print("=== " + ("ALL PASS" if ok else "SOME FAIL") + " ===")
sys.exit(0 if ok else 1)
