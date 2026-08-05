# -*- coding: utf-8 -*-
"""연구노트 업로드 테스트용 F-SCH JSON 생성 — 앱의 실제 export 기능 사용.

'내 기능으로': UI [연구노트 Export] 버튼과 동일 경로 —
  NotebookExporter.plan_from_app(app) → NotebookExporter(app).save(...)
(cfg = 실제 hardware_config.json, 시약맵/시퀀스만 데모 데이터로 주입)

구성: Group A~D 각 포트 2~11 = 실제 시약 40종(MW·밀도·SMILES·CAS,
  포트 1=세척 용매 / 12=폐기 — 시스템 예약), 10스텝 시퀀스가
  A/B 포트 2→11 스위프(HTE 스크리닝 형태), C=촉매 고정, D=용매 고정.
주의: 밀도는 노트 스키마(F-LMJ component 6키)에 필드가 없어 파일엔 안 실리고
  MW/eq/concM 이 실림. mmol·massG·이론수율은 스톡 '부피' 미지정(UI 기능 현행)
  이라 0 — 업로드 스키마 검증 목적엔 무관.
"""
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.notebook_export import NotebookExporter

# ── 시약 라이브러리 (데모용 실물 값: MW g/mol · 밀도 g/mL · CAS · SMILES) ──
LIB = {
    # Group A — SM1 아릴 브로마이드 (포트 2~11)
    "Bromobenzene":            dict(mw=157.01, density=1.491, cas="108-86-1", smiles="Brc1ccccc1"),
    "4-Bromotoluene":          dict(mw=171.03, density=1.390, cas="106-38-7", smiles="Cc1ccc(Br)cc1"),
    "4-Bromoanisole":          dict(mw=187.03, density=1.456, cas="104-92-7", smiles="COc1ccc(Br)cc1"),
    "1-Bromo-4-fluorobenzene": dict(mw=175.00, density=1.593, cas="460-00-4", smiles="Fc1ccc(Br)cc1"),
    "4-Bromobenzonitrile":     dict(mw=182.02, density=1.480, cas="623-00-7", smiles="N#Cc1ccc(Br)cc1"),
    "3-Bromopyridine":         dict(mw=158.00, density=1.640, cas="626-55-1", smiles="Brc1cccnc1"),
    "2-Bromothiophene":        dict(mw=163.04, density=1.684, cas="1003-09-4", smiles="Brc1cccs1"),
    "4-Bromoacetophenone":     dict(mw=199.04, density=1.647, cas="99-90-1", smiles="CC(=O)c1ccc(Br)cc1"),
    "1-Bromonaphthalene":      dict(mw=207.07, density=1.489, cas="90-11-9", smiles="Brc1cccc2ccccc12"),
    "6-Bromoquinoline":        dict(mw=208.06, density=1.583, cas="5332-25-2", smiles="Brc1ccc2ncccc2c1"),
    # Group B — SM2 알킬 브로마이드
    "Bromocyclohexane":        dict(mw=163.06, density=1.336, cas="108-85-0", smiles="BrC1CCCCC1"),
    "1-Bromobutane":           dict(mw=137.02, density=1.276, cas="109-65-9", smiles="CCCCBr"),
    "1-Bromohexane":           dict(mw=165.07, density=1.176, cas="111-25-1", smiles="CCCCCCBr"),
    "Benzyl bromide":          dict(mw=171.03, density=1.438, cas="100-39-0", smiles="BrCc1ccccc1"),
    "1-Bromo-2-methylpropane": dict(mw=137.02, density=1.253, cas="78-77-3", smiles="CC(C)CBr"),
    "2-Bromopropane":          dict(mw=122.99, density=1.310, cas="75-26-3", smiles="CC(C)Br"),
    "(3-Bromopropyl)benzene":  dict(mw=199.09, density=1.310, cas="637-59-2", smiles="BrCCCc1ccccc1"),
    "1-Bromopentane":          dict(mw=151.05, density=1.218, cas="110-53-2", smiles="CCCCCBr"),
    "4-Bromo-1-butene":        dict(mw=135.00, density=1.323, cas="5162-44-7", smiles="C=CCCBr"),
    "1-Bromooctane":           dict(mw=193.13, density=1.108, cas="111-83-1", smiles="CCCCCCCCBr"),
    # Group C — 촉매/리간드/광촉매/환원제/염기
    "NiCl2·dme":               dict(mw=219.76, density=1.500, cas="29046-78-4", smiles=""),
    "dtbbpy":                  dict(mw=268.40, density=1.050, cas="81998-05-2", smiles="CC(C)(C)c1ccnc(-c2cc(C(C)(C)C)ccn2)c1"),
    "4CzIPN":                  dict(mw=788.88, density=1.300, cas="1416881-52-1", smiles=""),
    "TTMSS":                   dict(mw=248.66, density=0.806, cas="1873-77-4", smiles="C[Si](C)(C)[SiH]([Si](C)(C)C)[Si](C)(C)C"),
    "2,6-Lutidine":            dict(mw=107.15, density=0.920, cas="108-48-5", smiles="Cc1cccc(C)n1"),
    "DIPEA":                   dict(mw=129.24, density=0.742, cas="7087-68-5", smiles="CCN(C(C)C)C(C)C"),
    "DBU":                     dict(mw=152.24, density=1.018, cas="6674-22-2", smiles="C1CCC2=NCCCN2CC1"),
    "Zinc dust":               dict(mw=65.38,  density=7.140, cas="7440-66-6", smiles="[Zn]"),
    "LiCl":                    dict(mw=42.39,  density=2.070, cas="7447-41-8", smiles="[Li+].[Cl-]"),
    "NaI":                     dict(mw=149.89, density=3.670, cas="7681-82-5", smiles="[Na+].[I-]"),
    # Group D — 용매/첨가제
    "DMA":                     dict(mw=87.12,  density=0.937, cas="127-19-5", smiles="CN(C)C(C)=O"),
    "DMSO":                    dict(mw=78.13,  density=1.100, cas="67-68-5", smiles="CS(C)=O"),
    "MeCN":                    dict(mw=41.05,  density=0.786, cas="75-05-8", smiles="CC#N"),
    "THF":                     dict(mw=72.11,  density=0.889, cas="109-99-9", smiles="C1CCOC1"),
    "2-MeTHF":                 dict(mw=86.13,  density=0.854, cas="96-47-9", smiles="CC1CCCO1"),
    "DMF":                     dict(mw=73.09,  density=0.944, cas="68-12-2", smiles="CN(C)C=O"),
    "Toluene":                 dict(mw=92.14,  density=0.867, cas="108-88-3", smiles="Cc1ccccc1"),
    "1,4-Dioxane":             dict(mw=88.11,  density=1.033, cas="123-91-1", smiles="C1COCCO1"),
    "NMP":                     dict(mw=99.13,  density=1.028, cas="872-50-4", smiles="CN1CCCC1=O"),
    "Water":                   dict(mw=18.02,  density=0.997, cas="7732-18-5", smiles="O"),
}

# ── 포트 배치: 그룹별 포트 2~11 (1=세척 용매, 12=폐기 — 시스템 예약) ──
GROUP_PORTS = {
    "Group A": ["Bromobenzene", "4-Bromotoluene", "4-Bromoanisole",
                "1-Bromo-4-fluorobenzene", "4-Bromobenzonitrile", "3-Bromopyridine",
                "2-Bromothiophene", "4-Bromoacetophenone", "1-Bromonaphthalene",
                "6-Bromoquinoline"],
    "Group_B": ["Bromocyclohexane", "1-Bromobutane", "1-Bromohexane",
                "Benzyl bromide", "1-Bromo-2-methylpropane", "2-Bromopropane",
                "(3-Bromopropyl)benzene", "1-Bromopentane", "4-Bromo-1-butene",
                "1-Bromooctane"],
    "Group_C": ["NiCl2·dme", "dtbbpy", "4CzIPN", "TTMSS", "2,6-Lutidine",
                "DIPEA", "DBU", "Zinc dust", "LiCl", "NaI"],
    "Group_D": ["DMA", "DMSO", "MeCN", "THF", "2-MeTHF",
                "DMF", "Toluene", "1,4-Dioxane", "NMP", "Water"],
}
CONC = {"Group A": 0.40, "Group_B": 0.48, "Group_C": 0.02, "Group_D": 1.00}


class MapMgr:
    """map_mgr.get_inlet 계약 — 포트 1~12 전부 응답."""
    def get_inlet(self, pump, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"name": "", "conc": 1.0, "smiles": ""}
        if port == 1:
            return {"name": "세척 용매", "conc": 1.0, "smiles": ""}
        if port == 12:
            return {"name": "폐기", "conc": 1.0, "smiles": ""}
        names = GROUP_PORTS.get(pump, [])
        if 2 <= port <= 11 and (port - 2) < len(names):
            n = names[port - 2]
            return {"name": n, "conc": CONC.get(pump, 1.0),
                    "smiles": LIB[n]["smiles"]}
        return {"name": "", "conc": 1.0, "smiles": ""}


class SeqTab:
    def __init__(self, steps):
        self.sequence_data = steps


class App:
    pass


def main():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "hardware_config.json")
    cfg_data = json.load(open(cfg_path, encoding="utf-8"))

    class Cfg:
        config_data = cfg_data
    app = App()
    app.cfg = Cfg()
    app.map_mgr = MapMgr()

    # ── 시퀀스: 10스텝 — A/B 포트 2→11 스위프, C(촉매)·D(용매) 고정 ──
    # 유속 = 앱 FlowCalculator 와 동일한 양론식(플랫폼 재계산과 일치):
    #   flow_i = 총유속 × (eq_i/conc_i) / Σ(eq/conc),  총유속 = reactor_vol / rt
    rt = 10.0
    reactor_vol = float(cfg_data.get("system_params", {}).get("reactor_vol") or 3.0002)
    eqs_fixed = {"Group A": 1.0, "Group_B": 1.2, "Group_C": 0.02, "Group_D": 1.0}
    fixed_port = {"Group_C": 2, "Group_D": 2}
    total_flow = reactor_vol / rt
    steps = []
    for i, port in enumerate(range(2, 12)):
        ratios = {p: eqs_fixed[p] / CONC[p] for p in eqs_fixed}
        scale = total_flow / sum(ratios.values())
        steps.append({
            "temp": 25.0, "rt": rt, "vol": 2.0, "tube_vol": 1.5,
            "pumps": {p: {"port": fixed_port.get(p, port), "eq": eqs_fixed[p],
                          "flow": round(ratios[p] * scale, 6), "vial": ""}
                      for p in eqs_fixed},
        })
    app.seq_tab = SeqTab(steps)

    # ── 실제 기능 경로: plan_from_app → save ──
    plan, eqs = NotebookExporter.plan_from_app(app)
    print(f"plan {len(plan)}스텝 (A/B 포트 2~11 스위프)")
    reagent_lib = {k: dict(v) for k, v in LIB.items()}

    paths = NotebookExporter(app).save(
        plan, datetime.now(),
        note_code="F-SCH-DEMO-001", revision=1,
        reaction_type="Ni/Photoredox XEC",
        cfg_data=cfg_data, eqs_by_step=eqs, reagent_lib=reagent_lib,
        recipes_by_port={})

    # ── 검증: 참조 스키마 키 일치 + strict JSON ──
    ref_path = r"C:\Users\gogoc\Downloads\F-SCH-001-007-rev-2.json"
    ok = True
    if os.path.exists(ref_path):
        ref = json.load(open(ref_path, encoding="utf-8"))

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
                    out += ([f"MISSING {path}.{k}"] if k not in b
                            else diff(a[k], b[k], f"{path}.{k}"))
                for k in b:
                    if k not in a:
                        out.append(f"EXTRA {path}.{k}")
            return out
        for p in paths:
            d = json.loads(open(p, encoding="utf-8").read())
            json.dumps(d, ensure_ascii=False, allow_nan=False)   # strict
            dd = [x for x in diff(keyset(ref), keyset(d))
                  if ".molarityM" not in x and ".weightPercent" not in x]
            if dd:
                ok = False
                print(f"  ⚠ {os.path.basename(p)}: {dd[:3]}")
    print(f"참조 스키마 키 일치: {'전 파일 OK' if ok else 'FAIL'}")

    # 공식 연구노트 JSON Schema 검증 (core/schemas/f_sch.schema.json)
    try:
        import jsonschema
        _sp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "core", "schemas", "f_sch.schema.json")
        _val = jsonschema.Draft202012Validator(json.load(open(_sp, encoding="utf-8")))
        n_bad = 0
        for p in paths:
            errs = ["/".join(map(str, e.path)) + ": " + e.message[:90]
                    for e in _val.iter_errors(json.load(open(p, encoding="utf-8")))]
            if errs:
                n_bad += 1
                print(f"  ⚠ 스키마 위반 {os.path.basename(p)}: {errs[:3]}")
        print(f"공식 스키마 검증: {'전 파일 OK' if n_bad == 0 else f'{n_bad}개 위반'}")
    except ImportError:
        print("공식 스키마 검증: 건너뜀 (jsonschema 미설치)")

    # 요약
    d1 = json.load(open(paths[0], encoding="utf-8"))
    sps = d1["content"]["stockParameters"]
    print(f"step1 stockParameters {len(sps)}펌프:")
    for s in sps:
        c = s["components"][0]
        print(f"  {s['pumpName']}: {c['reagent'][:36]:38s} MW {c['molecularWeightGPerMol']:>7} "
              f"eq {c['eq']:>5} concM {s['concM']} flow {s['flowRateMLPerMin']}")
    print(f"\n{len(paths)}개 저장: {os.path.dirname(paths[0])}")
    return paths


if __name__ == "__main__":
    main()
