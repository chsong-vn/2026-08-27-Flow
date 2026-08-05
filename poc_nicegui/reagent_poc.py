# -*- coding: utf-8 -*-
"""NiceGUI + AG Grid PoC — 계산기/시약 스톡 입력의 '엑셀급' 대체 UX.
현 PyQt QTableWidget(촘촘/답답)과 비교용. 실행: python reagent_poc.py → localhost:8080
하드웨어 없이 독립 실행 (PubChem 조회만 네트워크)."""
import json
import math
import urllib.request
import urllib.parse

from nicegui import ui, run

# ── 상태 ──
STOCK = {"reactionType": "Photoredox", "noteCode": "F-SCH-001-001",
         "revision": 1, "stockVolumeML": 2.5, "solvent": "DMA"}
rows = [
    {"port": 2, "name": "6-Bromoisoquinoline", "cas": "34784-05-9",
     "conc": 0.5, "eq": 1.0, "limiting": True, "mw": 208.06, "density": None,
     "mmol": None, "smiles": "Brc1ccc2ccncc2c1"},
    {"port": 3, "name": "Boc-3-bromoazetidine", "cas": "1064194-10-0",
     "conc": 0.6, "eq": 1.2, "limiting": False, "mw": 236.11, "density": None,
     "mmol": None, "smiles": "O=C(OC(C)(C)C)N1CC(Br)C1"},
    {"port": 4, "name": "2,6-Lutidine", "cas": "108-48-5",
     "conc": 1.0, "eq": 2.0, "limiting": False, "mw": 107.15, "density": 0.92,
     "mmol": None, "smiles": "Cc1cccc(C)n1"},
]


def recompute():
    v = STOCK["stockVolumeML"] or 0
    for r in rows:
        c = r.get("conc")
        r["mmol"] = round(c * v, 4) if (c and v) else None
    grid.update()


def pubchem(query):
    ua = {"User-Agent": "FlowChemPoC/1.0"}

    def fetch(url):
        req = urllib.request.Request(url, headers=ua)
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())

    enc = urllib.parse.quote(query)
    cid = fetch("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                f"{enc}/cids/JSON")["IdentifierList"]["CID"][0]
    p = fetch("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
              f"{cid}/property/IUPACName,MolecularWeight,CanonicalSMILES/JSON"
              )["PropertyTable"]["Properties"][0]
    return {"name": p.get("IUPACName", ""), "mw": float(p.get("MolecularWeight", 0) or 0),
            "smiles": p.get("CanonicalSMILES") or p.get("SMILES") or ""}


async def autofill():
    """CAS/이름 있는 행을 PubChem으로 자동채움 (MW·SMILES·이름)."""
    n = ui.notification("PubChem 조회 중 …", spinner=True, timeout=None)
    filled = 0
    for r in rows:
        q = (r.get("cas") or "").strip() or (r.get("name") or "").strip()
        if not q or (r.get("mw") and r.get("smiles")):
            continue
        try:
            res = await run.io_bound(pubchem, q)
            if res["mw"]:
                r["mw"] = round(res["mw"], 2)
            if res["smiles"]:
                r["smiles"] = res["smiles"]
            if res["name"] and not r.get("name"):
                r["name"] = res["name"]
            filled += 1
        except Exception as e:
            print("pubchem fail", q, e)
    n.dismiss()
    recompute()
    ui.notify(f"{filled}개 시약 자동채움 완료", type="positive")


def fsch_preview():
    """그리드 → F-SCH stockParameters 미리보기 (연구노트 연동 형태)."""
    def _p(t):
        return f"<p>{t}</p>"
    comps = []
    for r in rows:
        if not (r.get("name") or "").strip():
            continue
        c = r.get("conc") or 0
        mw = r.get("mw") or 0
        v = STOCK["stockVolumeML"] or 0
        mmol = c * v
        dens = r.get("density") or 0
        comps.append({
            "reagent": _p(r["name"]), "limiting": bool(r.get("limiting")),
            "molecularWeightGPerMol": mw, "molarityM": c, "eq": r.get("eq") or 0,
            "mmol": round(mmol, 4), "densityGPerML": dens,
            "volumeML": round(mmol * mw / 1000 / dens, 4) if dens else 0.0,
            "weightPercent": 0.0,
        })
    fsch = {"schemaVersion": 1, "noteCode": STOCK["noteCode"], "revision": STOCK["revision"],
            "content": {"experiment": {"reactionType": _p(STOCK["reactionType"])},
                        "stockParameters": [{"components": comps, "pumpName": "Syringe pump",
                                             "model": "Chemyx",
                                             "solvents": [{"name": _p(STOCK["solvent"]), "ratio": 1,
                                                           "volumeML": STOCK["stockVolumeML"]}],
                                             "volumeML": STOCK["stockVolumeML"]}]}}
    with ui.dialog() as dlg, ui.card().classes("w-[720px]"):
        ui.label("F-SCH 연구노트 미리보기 (stockParameters)").classes("text-lg font-bold")
        ui.code(json.dumps(fsch, indent=2, ensure_ascii=False), language="json").classes("w-full")
        ui.button("닫기", on_click=dlg.close).props("flat")
    dlg.open()


# ── 레이아웃 ──
ui.colors(primary="#e8743b")
with ui.header().classes("items-center justify-between bg-white text-gray-800 shadow-sm"):
    ui.label("VORONOI · 시약 스톡 (NiceGUI + AG Grid PoC)").classes("text-xl font-bold")
    ui.label("엑셀급 편집 · PubChem 자동채움 · F-SCH 연동").classes("text-sm text-gray-500")

with ui.column().classes("w-full max-w-[1100px] mx-auto gap-4 p-4"):
    # 실험/스톡 파라미터 카드
    with ui.card().classes("w-full"):
        ui.label("실험 · 스톡 파라미터").classes("text-base font-semibold text-gray-700")
        with ui.row().classes("w-full gap-6 items-end"):
            ui.select(["Photoredox", "Ni/Photoredox XEC", "Thermal", "Hydrogenation"],
                      label="Reaction type", value=STOCK["reactionType"],
                      on_change=lambda e: STOCK.update(reactionType=e.value)).classes("w-52")
            ui.input("Note code", value=STOCK["noteCode"],
                     on_change=lambda e: STOCK.update(noteCode=e.value)).classes("w-44")
            ui.number("Stock 총부피 (mL)", value=STOCK["stockVolumeML"], step=0.5, format="%.2f",
                      on_change=lambda e: (STOCK.update(stockVolumeML=e.value), recompute())).classes("w-40")
            ui.input("Solvent", value=STOCK["solvent"],
                     on_change=lambda e: STOCK.update(solvent=e.value)).classes("w-32")

    # 시약 그리드
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("시약 (스톡 성분)").classes("text-base font-semibold text-gray-700")
            with ui.row().classes("gap-2"):
                ui.button("＋ 시약", on_click=lambda: (rows.append(
                    {"port": (rows[-1]["port"] + 1 if rows else 2), "name": "", "cas": "",
                     "conc": None, "eq": 1.0, "limiting": False, "mw": None, "density": None,
                     "mmol": None, "smiles": ""}), grid.update())).props("outline")
                ui.button("PubChem 자동채움", on_click=autofill).props("color=primary")
                ui.button("F-SCH 미리보기", on_click=fsch_preview).props("outline color=primary")
        grid = ui.aggrid({
            "columnDefs": [
                {"headerName": "포트", "field": "port", "width": 80, "editable": False,
                 "cellClass": "text-gray-500"},
                {"headerName": "시약명", "field": "name", "flex": 2, "editable": True},
                {"headerName": "CAS", "field": "cas", "width": 130, "editable": True},
                {"headerName": "농도 (M)", "field": "conc", "width": 110, "editable": True,
                 "type": "numericColumn"},
                {"headerName": "당량 (eq)", "field": "eq", "width": 110, "editable": True,
                 "type": "numericColumn"},
                {"headerName": "한계", "field": "limiting", "width": 80,
                 "cellRenderer": "agCheckboxCellRenderer", "editable": True},
                {"headerName": "MW", "field": "mw", "width": 100, "editable": True,
                 "type": "numericColumn", "cellClass": "text-gray-600"},
                {"headerName": "밀도", "field": "density", "width": 90, "editable": True,
                 "type": "numericColumn", "cellClass": "text-gray-600"},
                {"headerName": "mmol", "field": "mmol", "width": 100, "editable": False,
                 "type": "numericColumn", "cellClass": "text-orange-600 font-semibold"},
                {"headerName": "SMILES", "field": "smiles", "flex": 2, "editable": True,
                 "cellClass": "text-gray-500 font-mono text-xs"},
            ],
            "rowData": rows,
            "defaultColDef": {"sortable": True, "resizable": True},
            "rowHeight": 42, "headerHeight": 46, "animateRows": True,
            "domLayout": "autoHeight",
        }).classes("w-full")

        def _cell_changed(e):
            d = e.args["data"]
            for r in rows:
                if r["port"] == d["port"]:
                    r.update(d)
                    break
            recompute()
        grid.on("cellValueChanged", _cell_changed)

    ui.label("↑ 넉넉한 행 높이·인라인 편집·정렬/리사이즈·체크박스·자동계산 — "
             "현 PyQt 표와 비교해보세요.").classes("text-sm text-gray-400")

recompute()
ui.run(title="VORONOI Reagent PoC", port=8080, reload=False, show=False)
