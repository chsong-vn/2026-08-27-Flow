# -*- coding: utf-8 -*-
"""F-SCH 연구노트 Export — 시퀀스 최종결과(plan)를 F-SCH JSON 으로 변환.

@codesyncer-decision: ExperimentReport 와 나란히 배치 — 동일 입력(sequence plan +
  start_time)을 받아 스키마만 F-SCH(연구노트) 로 낸다. 노트의 진실원 = 시퀀스가
  반환한 최종결과(plan): 유속·포트(시약)·온도·체류시간·부피. 스톡 조성/화학메타
  (MW·density·SMILES)는 reagent_lib(계산기 PubChem 조회 결과)에서 끌어온다.

@codesyncer-decision: 스텝별 노트 1건 — plan 의 각 스텝 = 1개의 F-SCH JSON.

매핑 (F-SCH ← 소스):
  hardware.residenceTimeM        ← step.residence_time
  hardware.totalFlowRateMLPerMin ← Σ step.flows              (실현 유속)
  hardware.tube*                 ← system_params(reactor_id/len) + 계산
  setup.reactionTemperatureC     ← step.temp
  setup.backPressureBarPSI       ← system_params(bpr)
  stockParameters[pump]          ← 펌프별 스톡 (component = 그 펌프 시약)
    .reagent/.molarityM          ← step.meta[pump].name/conc
    .eq                          ← eqs[pump] (시퀀스 당량)
    .molecularWeightGPerMol/.densityGPerML ← reagent_lib[name]
    .limiting                    ← eq==1.0 인 펌프
    .pumpName/.model             ← config 인벤토리
  schemes.flow                   ← 배관도 생성기 CDXML
"""
import os
import json
import math
from datetime import datetime


def _p(text):
    """F-SCH 텍스트 필드는 <p>...</p> HTML 래핑."""
    return f"<p>{'' if text is None else str(text)}</p>"


def _num(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def pump_name_model(cfg_data, pump_group):
    """config 인벤토리에서 (pumpName, model) 추출."""
    inv = {it["id"]: it for it in cfg_data.get("inventory", [])}
    for p in cfg_data.get("roles", {}).get("pumps", []):
        if p.get("name") == pump_group:
            mid = (p.get("drivers") or {}).get("motor")
            drv = (inv.get(mid) or {}).get("driver", "") or (inv.get(mid) or {}).get("name", "")
            for brand in ("Chemyx", "NRG", "Vapourtec", "Reaxus", "Hamilton", "Harvard"):
                if brand.lower() in drv.lower():
                    return "Syringe pump", brand
            return "Syringe pump", drv
    return "Syringe pump", ""


def reactor_volume_ml(sp):
    """반응기 부피(mL) = π·(id/2)²·len — reactor_vol 우선, 없으면 치수로 계산."""
    if sp.get("reactor_vol"):
        return _num(sp["reactor_vol"])
    id_mm, len_m = _num(sp.get("reactor_id_mm")), _num(sp.get("reactor_len_m"))
    return math.pi * (id_mm / 20.0) ** 2 * (len_m * 100.0) if (id_mm and len_m) else 0.0


class NotebookExporter:
    """시퀀스 plan → F-SCH 연구노트 JSON (스텝별)."""

    def __init__(self, app=None):
        self.app = app
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._root = os.path.join(base, "results", "notebook")

    # ── 시퀀스 → plan (run_seq 미러, 실행 없이) ─────────────────
    @staticmethod
    def plan_from_app(app):
        """현재 시퀀스(계산된 유속 포함)를 plan + eqs_by_step 으로. run_seq 동일 형식.
        유속>0 인 펌프만, 유속 없는 스텝은 제외. 반환 (plan, eqs_by_step) 정렬됨."""
        plan, eqs = [], []
        for sd in getattr(getattr(app, "seq_tab", None), "sequence_data", []) or []:
            d = {"temp": sd.get("temp", 25.0), "vol_ml": sd.get("vol", 0),
                 "residence_time": sd.get("rt", 10.0), "inlet_ports": {},
                 "inlet_vials": {}, "flows": {},
                 "collect_volume_per_tube": sd.get("tube_vol", 1.5), "meta": {}}
            e = {}
            for pump, pd in sd.get("pumps", {}).items():
                flow = _num(pd.get("flow"))
                if flow <= 0:
                    continue
                port = pd.get("port", 2)
                vial = str(pd.get("vial") or "")
                src = vial or port
                info = app.map_mgr.get_inlet(pump, src) if getattr(app, "map_mgr", None) \
                    else {"name": "", "conc": 1.0}
                d["inlet_ports"][pump] = port
                if vial:
                    d["inlet_vials"][pump] = vial
                d["flows"][pump] = flow
                d["meta"][pump] = {"name": info.get("name", ""), "conc": info.get("conc", 0)}
                e[pump] = _num(pd.get("eq", 1.0), 1.0)
            if d["flows"]:
                plan.append(d)
                eqs.append(e)
        return plan, eqs

    # ── 핵심 매퍼: 한 스텝 → F-SCH dict ──────────────────────────
    # F-SCH component 가 가질 수 있는 전체 필드 (템플릿 완전성용)
    # ── F-SCH 실물 스키마 정합 헬퍼 (2026-07-14 갱신) ───────────
    # 참조: F-SCH-001-007-rev-2.json — 플랫폼에서 export 된 '사용자 노트 폼' 실물.
    #   (초기엔 F-LMJ 노트를 참조했으나 F-SCH 폼은 구조가 다름 — import failed 원인)
    #   component = {eq, mmol, massG, reagent, limiting, pumpLimiting,
    #                molecularWeightGPerMol, densityGPerML, volumeML[, molarityM]}
    #   product   = '객체'(리스트 아님) {reaction, type, name, purityPercent,
    #                molecularWeightGPerMol, theoreticalYieldG, actualYieldG, yieldPercent}
    #   stock     = {concM, model, pumpName, solvents[{name, ratio, volumeML}],
    #                volumeML, components, flowRateMLPerMin}
    #   content  += setup.mixerTypes / procedure / lcms / nmrSpectra / tlc

    @staticmethod
    def _wrap_html(s):
        """이미 <p>…</p> 면 그대로, 아니면 래핑 (외부 component 정규화용)."""
        s = "" if s is None else str(s)
        return s if s.startswith("<p>") else _p(s)

    @staticmethod
    def _pump_letter(pump_group, idx=0):
        """펌프 그룹명 → 짧은 식별자('A'..). 참조 stockParameters.pumpName 형식."""
        s = str(pump_group or "").replace("Group", "").strip(" _-")
        if len(s) == 1 and s.isalnum():
            return s.upper()
        return chr(ord("A") + idx) if 0 <= idx < 26 else (s or "A")

    @staticmethod
    def _pump_model_type(cfg_data, pump_group):
        """참조 model = 펌프 '종류'(예: 'syringe'), 브랜드 아님."""
        pn, _brand = pump_name_model(cfg_data, pump_group)
        return (pn or "syringe").split()[0].lower()   # "Syringe pump" → "syringe"

    @staticmethod
    def _equipment_summary(cfg_data):
        """사용 장비 요약 → setup.mixerTypes 수기입력칸에 병기 (사용자 결정 2026-07-14).

        F-SCH 스키마상 setup 의 자유 문자열 배열이 mixerTypes 뿐이라 밸브/MFC 등
        장비 구성을 여기에 기록. roles(역할 배선) 기준 — 같은 장치가 여러 역할에
        걸리면 장치 id 로 중복 제거(역할 중복 자체는 실행 시 인터록이 별도 차단)."""
        roles = (cfg_data or {}).get("roles", {}) or {}
        inv = {d.get("id") for d in (cfg_data or {}).get("inventory", []) if d.get("id")}

        def live(did):
            return bool(did) and (not inv or did in inv)

        motors, sels, sws = set(), set(), set()
        for p in roles.get("pumps", []) or []:
            drv = p.get("drivers") or {}
            if live(drv.get("motor")):
                motors.add(drv["motor"])
            if live(drv.get("selector")):
                sels.add(drv["selector"])
            if live(drv.get("switcher")):
                sws.add(drv["switcher"])
        _out_v = (roles.get("outlet") or {}).get("driver_id")
        if live(_out_v):
            sws.add(_out_v)                      # 아웃렛 밸브 = 3-way
        out = []
        if motors:
            out.append(f"syringe pump ×{len(motors)}")
        if live((roles.get("push_pump") or {}).get("driver_id")):
            out.append("push pump ×1")
        if sels:
            out.append(f"12-way selector valve ×{len(sels)}")
        if sws:
            out.append(f"3-way valve ×{len(sws)}")
        if live((roles.get("gas") or {}).get("driver_id")):
            out.append("MFC ×1")
        if live((roles.get("heater") or {}).get("driver_id")):
            out.append("heater ×1")
        if live((roles.get("collector") or {}).get("driver_id")):
            out.append("fraction collector ×1")
        return out

    @classmethod
    def _component(cls, reagent, mw, eq, mmol, limiting, density=0.0,
                   pump_limiting=True, molarity=None, weight_percent=1.0):
        """F-SCH component 스키마 — limiting=전역 기준시약, pumpLimiting=이 stock 내 기준.

        weightPercent = 시약 순도(assay), 플랫폼 수식 Mass=MW·mmol/(1000·%Wt) 기준
        분수 표기(1 = 100%). 앱에 순도 입력이 없어 기본 100% 가정(사용자 결정
        2026-07-14) — 칭량 질량(massG)도 같은 가정: 순물질 질량과 동일."""
        mw = _num(mw); mmol = _num(mmol); dens = _num(density)
        wt = _num(weight_percent, 1.0) or 1.0
        mass_g = mmol * mw / 1000.0 / wt                  # 칭량 질량 = 순질량/순도
        out = {
            "eq": _num(eq),
            "mmol": round(mmol, 6),
            "massG": round(mass_g, 6),
            "reagent": cls._wrap_html(reagent),
            "limiting": bool(limiting),
            "pumpLimiting": bool(pump_limiting),
            "molecularWeightGPerMol": mw,
            "densityGPerML": dens,
            "weightPercent": round(wt, 6),
            "volumeML": round(mass_g / dens, 6) if dens > 0 else 0.0,  # 원액 부피
        }
        # @codesyncer(계산정합 2026-07-14): molarityM 은 방출하지 않는다 — 실물에서
        #   component molarityM(0.3)은 stock concM(0.2414)과 다른 독립 입력이고,
        #   플랫폼 'Vol = mmol/Molarity' 재계산 시 stock 농도를 넣으면 성분 부피에
        #   stock 전체부피가 들어가 Total Vol(Σ용매+Σ성분)이 이중계상됨.
        _ = molarity   # (호출부 호환 — conc_m 유도용으로만 사용)
        return out

    @classmethod
    def _normalize_component(cls, comp, global_limiting=None):
        """외부(레시피/계산기) component dict → F-SCH 키로 정규화."""
        mw = _num(comp.get("molecularWeightGPerMol"))
        mmol = _num(comp.get("mmol"))
        return cls._component(
            comp.get("reagent", ""), mw, comp.get("eq"), mmol,
            comp.get("limiting") if global_limiting is None else global_limiting,
            density=comp.get("densityGPerML"),
            pump_limiting=comp.get("limiting", False),
            molarity=comp.get("molarityM"),
            weight_percent=comp.get("weightPercent", 1.0))

    @classmethod
    def _product_entry(cls, name="", mw=0.0, theoretical_yield_g=0.0,
                       reaction=True, ptype="solution",
                       actual_yield_g=0.0, purity_pct=1.0):
        """F-SCH product 스키마 — 단일 '객체' (수율 필드 포함, 미측정=0)."""
        ty = _num(theoretical_yield_g)
        ay = _num(actual_yield_g)
        return {
            "reaction": bool(reaction),
            "type": ptype,
            "name": cls._wrap_html(name),
            "purityPercent": round(_num(purity_pct), 4),
            "molecularWeightGPerMol": _num(mw),
            "theoreticalYieldG": round(ty, 6),
            "actualYieldG": round(ay, 6),
            "yieldPercent": round(ay / ty * 100.0, 5) if ty > 0 else 0.0,
        }

    def build_fsch(self, step, cfg_data, *, eqs=None, reagent_lib=None,
                   note_code="F-SCH-000-000", revision=1, reaction_type="",
                   experiment_date=None, flow_cdxml="", batch_cdxml="",
                   solvents_by_pump=None, stock_components_by_pump=None,
                   products=None, project="", full_schema=True):
        """plan 스텝 1개 → F-LMJ 참조 스키마와 정합하는 F-SCH content dict.

        eqs = {pump: eq}, reagent_lib = {name: {mw, density, cas, smiles}},
        solvents_by_pump = {pump: [{name, ratio}]},
        stock_components_by_pump = {pump: [component dict]} (다성분 스톡 오버라이드),
        products = [{name, molecularWeightGPerMol|mw, smiles?, reaction?, type?}]
          (없으면 참조 형태만 방출 — 플랫폼이 실제 제품을 요구하면 사용자가 지정).
        project = experiment.project (참조는 평문 문자열).
        full_schema: 하위호환용(현재 참조 스키마는 항상 전체 필드 방출).
        """
        eqs = eqs or {}
        reagent_lib = reagent_lib or {}
        solvents_by_pump = solvents_by_pump or {}
        stock_components_by_pump = stock_components_by_pump or {}
        sp = cfg_data.get("system_params", {})
        date = experiment_date or datetime.now().strftime("%Y-%m-%d")
        project = project or sp.get("project", "") or ""

        flows = step.get("flows", {}) or {}
        meta = step.get("meta", {}) or {}
        total_flow = sum(_num(v) for v in flows.values())

        # limiting = eq==1.0 인 펌프 (없으면 최소 eq)
        lim_pumps = {p for p, e in eqs.items() if abs(_num(e, -1) - 1.0) < 1e-6}
        if not lim_pumps and eqs:
            mn = min(_num(e) for e in eqs.values())
            lim_pumps = {p for p, e in eqs.items() if abs(_num(e) - mn) < 1e-9}

        # ── stockParameters (F-SCH 스키마) ──
        stocks = []
        limiting_mmol = 0.0
        pump_order = list(flows.keys())
        for idx, pump in enumerate(pump_order):
            model = self._pump_model_type(cfg_data, pump)
            pump_letter = self._pump_letter(pump, idx)
            raw_solvents = solvents_by_pump.get(pump, []) or []
            solvents = [{"name": self._wrap_html(s.get("name", "")),
                         "ratio": _num(s.get("ratio", 1)),
                         "volumeML": round(_num(s.get("volumeML")), 6)}
                        for s in raw_solvents]
            stock_vol = _num((raw_solvents or [{}])[0].get("volumeML"))
            if pump in stock_components_by_pump:            # 다성분 스톡 오버라이드
                raw = stock_components_by_pump[pump] or []
                # 전역 limiting = 이 stock 의 기준성분(pumpLimiting)이면서
                # 이 펌프가 전역 기준 펌프일 때 (F-SCH 실물: limiting/pumpLimiting 분리)
                components = [self._normalize_component(
                    c, global_limiting=bool(c.get("limiting")) and (pump in lim_pumps))
                    for c in raw]
                conc_m = next((_num(c.get("molarityM")) for c in raw if c.get("limiting")),
                              _num((raw or [{}])[0].get("molarityM")))
            else:                                          # 기본: plan 의 그 펌프 시약 1성분
                info = meta.get(pump, {})
                name = info.get("name", "")
                lib = reagent_lib.get(name, {})
                conc = _num(info.get("conc"))
                mw = _num(lib.get("mw"))
                mmol = conc * stock_vol                     # M(mol/L) × mL = mmol
                components = [self._component(
                    name, mw, _num(eqs.get(pump, 1.0)), mmol, pump in lim_pumps,
                    density=lib.get("density"), pump_limiting=True, molarity=conc)]
                conc_m = conc
            for c in components:                            # 제품 이론수율용 limiting mmol
                if c.get("limiting"):
                    limiting_mmol = max(limiting_mmol, _num(c.get("mmol")))
            # stock 총부피 = 용매 부피 합 + 성분 원액 부피 합 (실물 관계식과 일치:
            # 6.3235 = 5.4977 + 0.8258). 부피 정보가 없으면 0.
            stock_total = round(sum(_num(s.get("volumeML")) for s in solvents)
                                + sum(_num(c.get("volumeML")) for c in components), 6)
            stocks.append({
                "concM": round(conc_m, 6),
                "model": model,
                "pumpName": pump_letter,
                "solvents": solvents,
                "volumeML": stock_total,
                "components": components,
                "flowRateMLPerMin": round(_num(flows.get(pump)), 6),
            })

        # ── product (F-SCH 스키마: 단일 객체) ──
        pr = (products or [{}])[0] or {}
        _pmw = _num(pr.get("molecularWeightGPerMol", pr.get("mw")))
        prod = self._product_entry(
            pr.get("name", ""), _pmw,
            round(limiting_mmol * _pmw / 1000.0, 6) if _pmw else 0.0,
            pr.get("reaction", True), pr.get("type", "solution"),
            actual_yield_g=pr.get("actualYieldG", 0.0),
            purity_pct=pr.get("purityPercent", 1.0))   # 순도 미입력=100%(분수 1) 가정

        return {
            "schemaVersion": 1,
            "noteCode": note_code,
            "revision": int(revision),
            "content": {
                "experiment": {
                    "project": project,                     # 참조는 평문(래핑 없음)
                    "reactionType": _p(reaction_type),
                    "experimentDate": date,
                },
                "hardware": {
                    "tubeLengthM": _num(sp.get("reactor_len_m")),
                    "tubeVolumeML": reactor_volume_ml(sp),
                    "residenceTimeM": _num(step.get("residence_time")),
                    "tubeInnerDiameterMM": _num(sp.get("reactor_id_mm")),
                    "totalFlowRateMLPerMin": total_flow,
                },
                "setup": {
                    "backPressureBarPSI": _num(
                        sp.get("bpr_bar") or sp.get("back_pressure") or sp.get("bpr_psi") or 0),
                    "reactionTemperatureC": _num(step.get("temp")),
                    # F-SCH 실물 필드 — 합류부 믹서 + 사용 장비 요약 병기
                    "mixerTypes": (list(sp.get("mixer_types") or ["t-junction"])
                                   + self._equipment_summary(cfg_data)),
                },
                "stockParameters": stocks,
                "product": prod,
                # F-SCH 폼 필수 섹션 — 실험 전 export 는 빈 HTML/목록으로 형태 유지
                "procedure": {"preparation": _p(""),
                              "workupAndPurification": _p(""),
                              "noReactionReason": _p("")},
                "lcms": {"files": [], "description": _p("")},
                "nmrSpectra": {"files": [], "description": _p("")},
                "tlc": {"solvent": _p(""), "retardationFactor": _p("")},
                "schemes": {"flow": flow_cdxml, "batch": batch_cdxml},
            },
        }

    # ── schemes.flow 생성 (배관도 CDXML) ────────────────────────
    @staticmethod
    def _flow_cdxml(cfg_data, reagents_by_pump=None, scheme=None):
        """schemes.flow CDXML = 상단 반응 스킴 + 하단 배관도(이름). 실패 시 ''."""
        try:
            import sys, tempfile
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ne = os.path.join(here, "notebook_export")
            if ne not in sys.path:
                sys.path.insert(0, ne)
            import piping_cdxml as PC
            tmp = os.path.join(tempfile.gettempdir(), "fsch_flow.cdxml")
            PC.build_notebook_flow(cfg_data, tmp, reagents=reagents_by_pump, scheme=scheme)
            return open(tmp, encoding="utf-8").read()
        except Exception as e:
            print(f"[Notebook] schemes.flow 생성 실패 (무시): {e}")
            return ""

    @staticmethod
    def _batch_cdxml(scheme):
        """schemes.batch CDXML = 반응 스킴(구조식)만. 참조 schemes.batch 대응. 실패 시 ''."""
        try:
            import sys, tempfile
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ne = os.path.join(here, "notebook_export")
            if ne not in sys.path:
                sys.path.insert(0, ne)
            import piping_cdxml as PC
            tmp = os.path.join(tempfile.gettempdir(), "fsch_batch.cdxml")
            PC.build_batch_scheme(scheme, tmp)
            return open(tmp, encoding="utf-8").read()
        except Exception as e:
            print(f"[Notebook] schemes.batch 생성 실패 (무시): {e}")
            return ""

    @staticmethod
    def _scheme_for_step(step, reagent_lib, reaction_type, products=None):
        """스텝 → 반응 스킴 정보. SMILES 있는 시약=반응물(구조), 없는 시약=조건(텍스트)."""
        reagent_lib = reagent_lib or {}
        meta = step.get("meta", {}) or {}
        reactants, conds_above = [], ([reaction_type] if reaction_type else [])
        for pump, m in meta.items():
            nm = (m or {}).get("name", "")
            smi = (reagent_lib.get(nm) or {}).get("smiles", "")
            if smi:
                reactants.append((smi, nm))
            elif nm:
                conds_above.append(nm)
        conds_below = [f"{_num(step.get('temp')):.0f} C"]
        prod = None
        if products and products[0] and products[0].get("smiles"):
            prod = (products[0]["smiles"], products[0].get("name", ""))
        return {"reactants": reactants[:3], "conditions_above": conds_above[:3],
                "conditions_below": conds_below, "product": prod}

    # ── 저장: plan → 스텝별 F-SCH JSON ──────────────────────────
    def save(self, plan, start_time, *, note_code, revision=1, reaction_type="",
             cfg_data=None, eqs_by_step=None, reagent_lib=None,
             solvents_by_pump=None, products=None, project="",
             recipes_by_port=None, out_dir=None):
        """plan(시퀀스 최종결과) → 스텝별 F-SCH JSON 저장. 저장 경로 리스트 반환."""
        app = self.app
        if cfg_data is None and app is not None:
            cfg_data = app.cfg.config_data
        cfg_data = cfg_data or {}
        date = (start_time or datetime.now()).strftime("%Y-%m-%d")
        out_dir = out_dir or os.path.join(self._root, date)
        os.makedirs(out_dir, exist_ok=True)

        # eq: 인자 우선, 없으면 app.seq_tab.sequence_data 에서
        if eqs_by_step is None and app is not None:
            eqs_by_step = []
            for sd in getattr(app.seq_tab, "sequence_data", []):
                eqs_by_step.append({p: pd.get("eq", 1.0)
                                    for p, pd in sd.get("pumps", {}).items()})
        eqs_by_step = eqs_by_step or [{} for _ in plan]

        paths = []
        for i, step in enumerate(plan):
            eqs = eqs_by_step[i] if i < len(eqs_by_step) else {}
            # ── 다성분 stock 레시피 해석 (2026-07-13): 레시피는 (pump, port) 속성 —
            #    HTE 처럼 스텝마다 포트가 바뀌므로 '그 스텝이 실제 쓰는 포트'로 조회.
            step_comps = {}
            step_solv = dict(solvents_by_pump or {})
            if recipes_by_port:
                from engine.stock_stoich import to_export_components, to_export_solvents
                for pump in step.get("flows", {}):
                    port = step.get("inlet_ports", {}).get(pump)
                    rec = recipes_by_port.get((pump, port))
                    if rec and rec.get("components"):
                        # 인라인 성분(농도만 입력)은 MW/밀도가 비어 massG=0 이 됨 —
                        # 계산기 시약 라이브러리(PubChem)로 보강 후 export.
                        comps = [dict(c) for c in rec["components"]]
                        for c in comps:
                            lib = (reagent_lib or {}).get(c.get("reagent", "")) or {}
                            if not _num(c.get("mw")) and lib.get("mw"):
                                c["mw"] = lib["mw"]
                            if not _num(c.get("density")) and lib.get("density"):
                                c["density"] = lib["density"]
                        rec_enriched = dict(rec)
                        rec_enriched["components"] = comps
                        exp_comps = to_export_components(rec_enriched)
                        step_comps[pump] = exp_comps
                        sv = to_export_solvents(rec)
                        # @codesyncer(계산정합 2026-07-14): 플랫폼 'Pump Volume =
                        #   Σ용매 + Σ성분 원액부피' — 레시피 총부피를 알면 용매
                        #   volumeML = (총부피 − Σ성분 원액부피)를 ratio 비례 배분해
                        #   Total Vol 결손(Missing values) 방지. 값 불변식:
                        #   Σ용매 + Σ성분 = total_volume_ml (테스트로 고정).
                        _total = _num(rec.get("total_volume_ml"))
                        if _total > 0:
                            _comp_v = sum(_num(c.get("volumeML")) for c in exp_comps)
                            _solv_total = max(0.0, _total - _comp_v)
                            if not sv and _solv_total > 1e-9:
                                # 용매 항목이 없는 레시피 — 이름은 미상이어도 부피는
                                # 물질수지(총부피−Σ성분 원액)로 확정되므로 빈 이름으로
                                # 기입, 이름은 플랫폼에서 선택 (사용자 결정 2026-07-14)
                                sv = [{"name": "", "ratio": 1.0,
                                       "volumeML": round(_solv_total, 6)}]
                            elif sv and not any(_num(s.get("volumeML")) > 0
                                                for s in sv):
                                _rsum = (sum(_num(s.get("ratio"), 1.0) for s in sv)
                                         or 1.0)
                                for s in sv:
                                    s["volumeML"] = round(
                                        _solv_total * _num(s.get("ratio"), 1.0) / _rsum, 6)
                        if sv:
                            step_solv[pump] = sv
            # 스텝별 schemes.flow = 반응 스킴(구조) + 배관도(이름), schemes.batch = 반응 스킴만
            scheme = self._scheme_for_step(step, reagent_lib, reaction_type, products=products)
            reagents_by_pump = {p: ("", (step.get("meta", {}).get(p, {}) or {}).get("name", ""))
                                for p in step.get("flows", {})}
            flow_cdxml = self._flow_cdxml(cfg_data, reagents_by_pump=reagents_by_pump,
                                          scheme=scheme)
            batch_cdxml = self._batch_cdxml(scheme)
            fsch = self.build_fsch(
                step, cfg_data, eqs=eqs, reagent_lib=reagent_lib,
                note_code=note_code, revision=revision, reaction_type=reaction_type,
                experiment_date=date, flow_cdxml=flow_cdxml, batch_cdxml=batch_cdxml,
                solvents_by_pump=step_solv, stock_components_by_pump=step_comps,
                products=products, project=project)
            path = os.path.join(out_dir, f"{note_code}_step{i + 1}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fsch, f, indent=2, ensure_ascii=False)
            paths.append(path)
        print(f"[Notebook] {len(paths)}개 F-SCH JSON 저장: {out_dir}")
        return paths
