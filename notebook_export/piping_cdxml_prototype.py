# -*- coding: utf-8 -*-
"""ITC Example P&ID + inlet 화학구조식 프로토타입 생성기.
바이너리 CDX에서 뽑은 Example_from_cdx.cdxml(실제 조립 배관도)에
RDKit로 그린 실제 시약 구조식을 두 펌프 inlet에 얹는다."""
import xml.etree.ElementTree as ET
from rdkit import Chem
from rdkit.Chem import AllChem

SRC = "Example_from_cdx.cdxml"
OUT = "PID_with_structures.cdxml"

# ── inlet 시약 (SMILES, 펌프 inlet y중심) ──
INLETS = [
    ("Brc1ccc2ccncc2c1", 240.0, "6-Bromoisoquinoline"),          # 상단 펌프
    ("O=C(OC(C)(C)C)N1CC(Br)C1", 289.0, "1-Boc-3-bromoazetidine"), # 하단 펌프
]
INLET_RIGHT_X = 58.0   # 구조식 오른쪽 끝(펌프 inlet x≈65.8 바로 왼쪽)

tree = ET.parse(SRC)
root = tree.getroot()
BL = float(root.get("BondLength", "14.4"))
page = next(root.iter("page"))

_id = [900000]
def nid():
    _id[0]+=1; return _id[0]

def mol_fragment(smiles, right_x, y_center):
    m = Chem.MolFromSmiles(smiles)
    if m is None: raise ValueError("bad smiles "+smiles)
    m = Chem.AddHs(m, explicitOnly=False, onlyOnAtoms=[]) if False else m
    AllChem.Compute2DCoords(m)
    Chem.Kekulize(m, clearAromaticFlags=True)
    conf = m.GetConformer()
    # rdkit 평균 결합길이 → 문서 BondLength 로 스케일
    import math
    ds=[]
    for b in m.GetBonds():
        p=conf.GetAtomPosition(b.GetBeginAtomIdx()); q=conf.GetAtomPosition(b.GetEndAtomIdx())
        ds.append(math.hypot(p.x-q.x,p.y-q.y))
    scale = BL/(sum(ds)/len(ds)) if ds else BL
    xs=[conf.GetAtomPosition(i).x for i in range(m.GetNumAtoms())]
    ys=[conf.GetAtomPosition(i).y for i in range(m.GetNumAtoms())]
    minx,maxx=min(xs),max(xs); cy=(min(ys)+max(ys))/2
    # 오른쪽 끝을 right_x 에 맞추고, y중심을 y_center 에 (CDXML은 Y아래+ → y뒤집기)
    frag = ET.Element("fragment", {"id":str(nid())})
    idmap={}
    inlet_node=None; inlet_nx=-1
    for i in range(m.GetNumAtoms()):
        p=conf.GetAtomPosition(i)
        X = right_x - (maxx - p.x)*scale
        Y = y_center - (p.y - cy)*scale
        a=m.GetAtomWithIdx(i)
        attrs={"id":str(nid()),"p":f"{X:.2f} {Y:.2f}"}
        if a.GetSymbol()!="C":
            attrs["Element"]=str(a.GetAtomicNum())
        n=ET.SubElement(frag,"n",attrs); idmap[i]=int(attrs["id"])
        if X>inlet_nx: inlet_nx=X; inlet_node=int(attrs["id"])
    for b in m.GetBonds():
        o=b.GetBondTypeAsDouble()
        attrs={"id":str(nid()),"B":str(idmap[b.GetBeginAtomIdx()]),"E":str(idmap[b.GetEndAtomIdx()])}
        if o==2.0: attrs["Order"]="2"
        elif o==3.0: attrs["Order"]="3"
        ET.SubElement(frag,"b",attrs)
    return frag, inlet_node, (right_x, y_center)

for smiles, yc, name in INLETS:
    frag, node_id, (rx,ry) = mol_fragment(smiles, INLET_RIGHT_X, yc)
    page.append(frag)
    # inlet 구조 → 펌프 배관 화살표 (튜빙)
    arrow=ET.SubElement(page,"arrow",{
        "id":str(nid()),"GraphicType":"Line","ArrowheadType":"Solid","ArrowheadHead":"Full",
        "Head3D":f"65.80 {ry:.2f} 0","Tail3D":f"{INLET_RIGHT_X+1:.2f} {ry:.2f} 0",
        "BoundingBox":f"{INLET_RIGHT_X:.2f} {ry-1:.2f} 65.80 {ry+1:.2f}"})

tree.write(OUT, encoding="utf-8", xml_declaration=True)
print(">>> wrote", OUT)
