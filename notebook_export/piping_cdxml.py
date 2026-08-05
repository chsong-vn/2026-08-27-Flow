# -*- coding: utf-8 -*-
"""VORONOI FlowChem 배관도(P&ID) -> ChemDraw CDXML 생성기.

ui/visual_diagram_parts.py(v3, 대시보드 활성 배관도)의 파츠 지오메트리와
배선을 그대로 CDXML 프리미티브(Oval/Line/Rectangle/arrow/t)로 재현한다.

핵심 배선 (v3 configure() 미러):
  채널: 병 → 12way(port10 in, port4 out) → 3way L
        3way B(하단 공통) ↓ → 시린지 펌프 노즐(분기, 주입/토출 왕복)
        3way R → 매니폴드(X_MAN)                       ← 메인 경로
  매니폴드: 캐스케이드 스파인(위→아래), 메인 라인 = 마지막 행 y_m
  메인: 매니폴드 → (N2 티: 실린더+MFC 위에서) → (push 티: 체크밸브+HPLC+용매병
        아래에서) → 반응기(세로 카드, in 좌상→out 우하) → (BPR) → 아웃렛 3way
        → Collect(우, 분취기) / Waste(아래, 폐기병)

화합물 구조식은 P&ID에 그리지 않는다(시약명만). structure()는 scheme용 보존.
"""
import math
import xml.sax.saxutils as SX

# ── v3 좌표 규약 (visual_diagram_parts.configure) ──
# P1 비율 재배분 — Qt(visual_diagram_parts) 상수 미러 (피드 압축/메인 확장)
X_SRC, X_SEL, X_SW, X_PUMP, X_MAN = 60, 165, 260, 375, 470
X_N2, X_PUSH, X_RCT, X_BPR, X_OUT, X_SINK = 560, 655, 795, 915, 1015, 1150
ROW_PITCH = 150
BL = 14.4

_id = [1000]
def nid():
    _id[0] += 1
    return _id[0]

_buf = []
def emit(s):
    _buf.append(s)

# ── 프리미티브 ──
def line(x1, y1, x2, y2, head=None):
    """head=None -> 평범한 선(graphic Line). head="Full" -> 방향 화살표."""
    i = nid()
    if head is None:
        emit(f'<graphic id="{i}" GraphicType="Line" '
             f'BoundingBox="{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}"/>')
    else:
        emit(f'<arrow id="{i}" ArrowheadHead="Full" ArrowheadType="Solid" '
             f'Head3D="{x2:.2f} {y2:.2f} 0" Tail3D="{x1:.2f} {y1:.2f} 0" '
             f'BoundingBox="{min(x1,x2):.2f} {min(y1,y2):.2f} {max(x1,x2):.2f} {max(y1,y2):.2f}"/>')

def polyline(pts, head_last=False):
    """직교 배관 폴리라인. head_last=True 면 마지막 세그먼트만 화살표."""
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        last = (k == len(pts) - 2)
        line(a[0], a[1], b[0], b[1], head="Full" if (head_last and last) else None)

def oval(cx, cy, rx, ry=None, filled=False):
    ry = rx if ry is None else ry
    i = nid()
    ot = "Circle Filled" if filled else "Circle"
    emit(f'<graphic id="{i}" GraphicType="Oval" OvalType="{ot}" '
         f'BoundingBox="{cx-rx:.2f} {cy-ry:.2f} {cx+rx:.2f} {cy+ry:.2f}" '
         f'Center3D="{cx:.2f} {cy:.2f} 0" MajorAxisEnd3D="{cx+rx:.2f} {cy:.2f} 0" '
         f'MinorAxisEnd3D="{cx:.2f} {cy+ry:.2f} 0"/>')

def rect(x1, y1, x2, y2):
    i = nid()
    emit(f'<graphic id="{i}" GraphicType="Rectangle" '
         f'BoundingBox="{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}"/>')

def text(x, y, s, size=9, face=0, center=True, font=3):
    """font 3=Arial, 4=Courier New(태그용 모노)."""
    i = nid()
    j = "Center" if center else "Left"
    s = SX.escape(s)
    emit(f'<t id="{i}" p="{x:.2f} {y:.2f}" Justification="{j}" '
         f'LineHeight="auto"><s font="{font}" size="{size}" face="{face}">{s}</s></t>')

def tag(x, y, s):
    """v3 모노 태그 (파츠 하단)."""
    text(x, y, s, size=8, font=4)

def chip_at(cx, cy, ml):
    """데드볼륨 mL 태그 — 라인 위 작은 박스 (0/None 은 미표시)."""
    try:
        v = float(ml)
    except (TypeError, ValueError):
        return
    if v <= 0:
        return
    s = f"{v:.2f} mL"
    w = 5.0 * len(s) + 8
    rect(cx - w / 2, cy - 7, cx + w / 2, cy + 7)
    text(cx, cy, s, size=6, font=4)

# ── v3 파츠 (지오메트리 전사) ──

def part_bottle(cx, cy, label="", name="", waste=False):
    """PartBottle: 몸통 40x50 라운드 + 목 16x8 + 액면. out=우측(+22.5,0)."""
    rect(cx - 20, cy - 24, cx + 20, cy + 26)          # 몸통
    rect(cx - 8, cy - 32, cx + 8, cy - 24)            # 목
    rect(cx - 17, cy + 2, cx + 17, cy + 23)           # 액면
    if label:
        text(cx, cy - 10, label, size=9, face=1)
    if name:
        text(cx, cy + 38, name, size=8)
    return (cx + 22.5, cy)                            # out 포트

def part_12way(cx, cy, tag_s="", port_num=None):
    """Part12Way: 원 R=19.4 + 12 스텁도트(반경 26.3) + 로터(중심→포트) + 배지.
    port 1=12시, port4=우(3시, out), port10=좌(9시, in)."""
    S = 3.6
    R = 5.4 * S
    oval(cx, cy, R)
    for k in range(12):
        a = math.radians(30 * k)
        px, py = 7.3 * S * math.sin(a), -7.3 * S * math.cos(a)
        # 스텁 (원 가장자리 → 포트점)
        ex, ey = 5.4 * S * math.sin(a), -5.4 * S * math.cos(a)
        line(cx + ex, cy + ey, cx + px, cy + py)
        oval(cx + px, cy + py, 1.6, filled=True)
    # 로터: 중심 → 선택 포트(기본 4=우측 out)
    pn = port_num if port_num else 4
    a = math.radians(30 * (pn - 1))
    line(cx, cy, cx + 5.0 * S * math.sin(a), cy - 5.0 * S * math.cos(a))
    oval(cx, cy, 3.4, filled=True)
    if port_num:
        text(cx, cy - R - 8, f"P{port_num}", size=8, face=1, font=4)
    if tag_s:
        tag(cx, cy + 7.3 * S + 10, tag_s)
    return {"in": (cx - 7.3 * S, cy), "out": (cx + 7.3 * S, cy)}

def part_3way(cx, cy, tag_s=""):
    """Part3Way: 원 R=9 + L/R/B 스텁(±14.4, B=+14.4) + 중심도트. 태그는 상단."""
    S = 3.6
    R = 2.5 * S
    oval(cx, cy, R)
    line(cx - R, cy, cx - 4.0 * S, cy)                # L 스텁
    line(cx + R, cy, cx + 4.0 * S, cy)                # R 스텁
    line(cx, cy + R, cx, cy + 4.0 * S)                # B 스텁 (하단 공통)
    oval(cx - 4.0 * S, cy, 1.6, filled=True)
    oval(cx + 4.0 * S, cy, 1.6, filled=True)
    oval(cx, cy + 4.0 * S, 1.6, filled=True)
    oval(cx, cy, 2.6, filled=True)
    if tag_s:
        tag(cx, cy - 4.0 * S - 12, tag_s)
    return {"L": (cx - 4.0 * S, cy), "R": (cx + 4.0 * S, cy), "B": (cx, cy + 4.0 * S)}

def part_syringe(cx, cy, label="", dual=False):
    """PartSyringe: 본체 104x28 + 배럴 62x10. mirror(기본)=좌 단일 노즐(out),
    dual=True=좌 in/우 out(오토샘플러·내장밸브)."""
    S = 5.2
    yb = cy - 2.05 * S                                 # 배럴 축선
    rect(cx - 10 * S, cy - 1.2 * S, cx + 10 * S, cy + 4.2 * S)   # 본체
    rect(cx - 6 * S, yb - 0.95 * S, cx + 6 * S, yb + 0.95 * S)   # 배럴
    line(cx - 6 * S, yb, cx - 10 * S, yb)              # 좌 노즐
    oval(cx - 10 * S, yb, 1.6, filled=True)
    oval(cx - 8 * S, cy + 1.5 * S, 2.0)                # 상태 LED
    if label:
        text(cx, cy + 4.6 * S + 8, label, size=9, face=1)
    if dual:
        line(cx + 6 * S, yb, cx + 10 * S, yb)          # 우 노즐
        oval(cx + 10 * S, yb, 1.6, filled=True)
        return {"in": (cx - 10 * S, yb), "out": (cx + 10 * S, yb)}
    return {"out": (cx - 10 * S, yb)}

def part_vialrack(cx, cy, vials=None, tag_s=""):
    """PartVialRack: 카드 128x74 + 바이알 2x5 그리드 + 우측 니들 암. out=우중(+70)."""
    W, H = 128, 74
    rect(cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2)
    vials = vials or ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5"]
    for i, v in enumerate(vials[:10]):
        vx = cx - W / 2 + 16 + (i % 5) * 24
        vy = cy - H / 2 + 20 + (i // 5) * 30
        oval(vx, vy, 9)
        text(vx, vy, str(v)[:3], size=6, font=4)
    line(cx + W / 2, cy, cx + W / 2 + 6, cy)           # 니들 암
    if tag_s:
        tag(cx, cy + H / 2 + 12, tag_s)
    return {"out": (cx + W / 2 + 6, cy)}

def part_hplc(cx, cy, tag_s=""):
    """PartHplc: 본체 97x40 + 이중 플런저 헤드 원 2개 + HPLC 텍스트."""
    S = 4.4
    rect(cx - 11 * S, cy - 4.5 * S, cx + 11 * S, cy + 4.5 * S)
    oval(cx - 4 * S, cy, 2.2 * S)
    oval(cx + 2.5 * S, cy, 2.2 * S)
    text(cx, cy, "HPLC", size=8, face=1)
    if tag_s:
        tag(cx, cy + 4.5 * S + 10, tag_s)
    return {"in": (cx - 13 * S, cy), "out": (cx + 13 * S, cy)}

def part_check(cx, cy):
    """PartCheck: 원 r=8.4 + 상향 화살표(허용 방향). in=하단, out=상단."""
    S = 7.0
    oval(cx, cy, 1.2 * S)
    line(cx, cy + 5, cx, cy - 5)
    line(cx - 4, cy - 1, cx, cy - 5)
    line(cx + 4, cy - 1, cx, cy - 5)
    return {"in": (cx, cy + 1.4 * S), "out": (cx, cy - 1.1 * S)}

def part_reactor(cx, cy, tag_s="R-01", sub=""):
    """반응기 = 카트리지 몸통 + 내부 사행(serpentine) 유로 — Qt v3 신디자인 정합.

    @codesyncer: 구형 '겹침 타원 코일'은 Qt 쪽에서 폐기(색 튐·지저분) — 두 뷰의
    아이콘 언어를 일치시킴. in=좌, out=우(같은 y). 사행은 삼각파 폴리라인(도면 관례)."""
    S = 5.8
    W, H = 12.0 * S, 5.6 * S
    left, right = cx - W / 2, cx + W / 2
    rect(left, cy - H / 2, right, cy + H / 2)          # 카트리지 몸통
    # 내부 사행 유로 (5주기 삼각파 — 겹침 없음)
    inW, amp = W - H * 0.9, H * 0.30
    nseg = 10
    dx = inW / nseg
    pts = [(cx - inW / 2, cy)]
    for k in range(nseg):
        pts.append((cx - inW / 2 + dx * (k + 0.5), cy + (amp if k % 2 == 0 else -amp)))
    pts.append((cx + inW / 2, cy))
    polyline(pts)
    line(left - 14, cy, left, cy)                      # in 리드선
    line(right, cy, right + 14, cy)                    # out 리드선
    oval(left - 14, cy, 1.6, filled=True)
    oval(right + 14, cy, 1.6, filled=True)
    if sub:
        text(cx, cy + H / 2 + 11, sub, size=7, font=4)
    tag(cx, cy + H / 2 + 25, tag_s)
    return {"in": (left - 14, cy), "out": (right + 14, cy)}


def part_phase_sensor(cx, cy, tag_s="Collect Sensor", label=""):
    """위상센서(OCB350) 클램프온 — 파이프 위 어노테이션 (Qt PartPhaseSensor 정합).
    몸통 + 광학창 슬롯 + 창(빈 원, 정적 도면) + 케이블 스텁. 흐름 재배선 없음."""
    S = 5.0
    rect(cx - 1.6 * S, cy - 2.4 * S, cx + 1.6 * S, cy + 2.4 * S)   # 몸통
    rect(cx - 0.55 * S, cy - 1.45 * S, cx + 0.55 * S, cy + 1.45 * S)  # 광학창 슬롯
    oval(cx, cy, 0.78 * S)                                          # 검출 창
    # 케이블 스텁 (우상단 → 위)
    polyline([(cx + 1.0 * S, cy - 2.4 * S), (cx + 1.0 * S, cy - 3.3 * S),
              (cx + 2.1 * S, cy - 3.3 * S)])
    oval(cx + 2.1 * S, cy - 3.3 * S, 1.2, filled=True)
    if label:
        text(cx, cy - 2.4 * S - 9, label, size=6, face=1)
    tag(cx, cy + 2.4 * S + 9, tag_s)

def part_mfc(cx, cy, tag_s="MFC-01"):
    """PartMfc: 박스 25x25 + MFC. in=우(+19.2), out=좌(-19.2)."""
    S = 4.8
    rect(cx - 2.6 * S, cy - 2.6 * S, cx + 2.6 * S, cy + 2.6 * S)
    text(cx, cy, "MFC", size=7, face=1)
    if tag_s:
        tag(cx, cy + 2.6 * S + 9, tag_s)
    return {"in": (cx + 4 * S, cy), "out": (cx - 4 * S, cy)}

def part_cylinder(cx, cy, tag_s="GC-N2"):
    """PartCylinder: 몸통 32x136 라운드 + 꼭지. out=top(0,-78.7)."""
    S = 6.2
    rect(cx - 2.6 * S, cy - 11 * S, cx + 2.6 * S, cy + 11 * S)   # 몸통
    rect(cx - 1.0 * S, cy - 12.4 * S, cx + 1.0 * S, cy - 10.8 * S)  # 꼭지
    text(cx, cy, "N2", size=9, face=1)
    if tag_s:
        tag(cx, cy + 11.6 * S + 8, tag_s)
    return {"out": (cx, cy - 12.7 * S), "bottom": (cx, cy + 11 * S)}

def part_bpr(cx, cy, tag_s="BPR-01"):
    """PartBpr: 원 r=9(중심 y-2) + 상단 꼭지 + BPR. in/out=±15.6."""
    S = 6.0
    oval(cx, cy - 2, 1.5 * S)
    line(cx, cy - 2 - 1.5 * S, cx, cy - 2 - 1.5 * S - 6)
    text(cx, cy - 2, "BPR", size=7, face=1)
    if tag_s:
        tag(cx, cy + 1.6 * S + 8, tag_s)
    return {"in": (cx - 2.6 * S, cy), "out": (cx + 2.6 * S, cy)}

def part_tee(cx, cy):
    """PartTee: 소원 r=6.3 + 도트."""
    oval(cx, cy, 6.3)
    oval(cx, cy, 2.2, filled=True)
    return {"L": (cx - 9.8, cy), "R": (cx + 9.8, cy),
            "T": (cx, cy - 9.8), "B": (cx, cy + 9.8)}

def part_outlet3way(cx, cy, tag_s="XV-01"):
    """PartOutlet3Way: 원 R=10 + L/R/B 스텁. L=in R=Collect B=Waste."""
    S = 4.0
    R = 2.5 * S
    oval(cx, cy, R)
    line(cx - R, cy, cx - 4 * S, cy)
    line(cx + R, cy, cx + 4 * S, cy)
    line(cx, cy + R, cx, cy + 4 * S)
    for px, py in ((cx - 4 * S, cy), (cx + 4 * S, cy), (cx, cy + 4 * S)):
        oval(px, py, 1.6, filled=True)
    oval(cx, cy, 2.6, filled=True)
    text(cx, cy - R - 10, "Outlet", size=8)
    tag(cx, cy + 4 * S + 10, tag_s)
    return {"L": (cx - 4 * S, cy), "R": (cx + 4 * S, cy), "B": (cx, cy + 4 * S)}

def part_collector(cx, cy, kind="plate96", tag_s="FC-01"):
    """PartCollector: 카드 92x84 + 8x12 미니웰(plate) / 원형 캐러셀(colosseum).
    in=좌상(-55.4, -52.8)."""
    S = 6.6
    left, top = cx - 7 * S, cy - 6.4 * S
    right, bot = cx + 7 * S, cy + 6.4 * S
    rect(left, top, right, bot)
    if kind == "colosseum":
        for k in range(12):
            a = math.radians(k * 30)
            oval(cx + 34 * math.sin(a), cy - 34 * math.cos(a), 4)
    else:
        gx0, gy0 = left + 8, top + 10
        gw = (right - left - 16) / 12.0
        gh = (bot - top - 34) / 8.0
        r = min(gw, gh) * 0.32
        for rr in range(8):
            for cc in range(12):
                oval(gx0 + cc * gw + gw / 2, gy0 + rr * gh + gh / 2, r)
    # in 스텁 (좌상)
    inx, iny = cx - 8.4 * S, cy - 8.0 * S
    line(inx, iny, left, iny)
    oval(inx, iny, 1.6, filled=True)
    tag(cx, bot + 10, tag_s)
    return {"in": (inx, iny)}

# ── (scheme용 보존) RDKit 구조 -> CDXML fragment ──
def structure(smiles, right_x, y_center):
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        raise ValueError(smiles)
    AllChem.Compute2DCoords(m)
    Chem.Kekulize(m, clearAromaticFlags=True)
    conf = m.GetConformer()
    ds = []
    for b in m.GetBonds():
        p = conf.GetAtomPosition(b.GetBeginAtomIdx())
        q = conf.GetAtomPosition(b.GetEndAtomIdx())
        ds.append(math.hypot(p.x - q.x, p.y - q.y))
    sc = BL / (sum(ds) / len(ds)) if ds else BL
    xs = [conf.GetAtomPosition(i).x for i in range(m.GetNumAtoms())]
    ys = [conf.GetAtomPosition(i).y for i in range(m.GetNumAtoms())]
    maxx = max(xs)
    cy = (min(ys) + max(ys)) / 2
    emit(f'<fragment id="{nid()}">')
    idmap = {}
    anchor = (-1e9, None)
    for i in range(m.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        X = right_x - (maxx - p.x) * sc
        Y = y_center - (p.y - cy) * sc
        a = m.GetAtomWithIdx(i)
        aid = nid()
        idmap[i] = aid
        el = f' Element="{a.GetAtomicNum()}"' if a.GetSymbol() != "C" else ""
        emit(f'<n id="{aid}" p="{X:.2f} {Y:.2f}"{el}/>')
        if X > anchor[0]:
            anchor = (X, (X, Y))
    for b in m.GetBonds():
        o = b.GetBondTypeAsDouble()
        od = ' Order="2"' if o == 2 else (' Order="3"' if o == 3 else '')
        emit(f'<b id="{nid()}" B="{idmap[b.GetBeginAtomIdx()]}" E="{idmap[b.GetEndAtomIdx()]}"{od}/>')
    emit('</fragment>')
    return anchor[1]

# ── 스텝 -> 시약 매핑 ──
def reagents_from_step(inlet_map, step_pumps):
    """스텝에서 각 펌프가 선택한 포트(바이알)의 시약을 뽑아 배관도 입력으로.
    반환 {pump_name: (smiles, display_name, port)}."""
    out = {}
    for pump, pd in (step_pumps or {}).items():
        port = pd.get("port", 1)
        info = (inlet_map.get(pump) or {}).get(port, {}) or {}
        out[pump] = (info.get("smiles", ""), info.get("name", ""), port)
    return out

def deadvols_from_config(cfg):
    """구간별 데드볼륨(mL) 추출 — SystemConfig.line_vol_* 와 동일 소스
    (roles.pumps[].settings.tube_vol_* / system_params). 반응기 부피는 치수에서 계산."""
    roles = cfg.get("roles", {})
    sp = cfg.get("system_params", {})

    def f(v):
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0

    pump = {}
    for p in roles.get("pumps", []):
        s = p.get("settings", {}) or {}
        pump[p.get("name")] = {
            "inlet": f(s.get("tube_vol_inlet")),          # 병 → 12-way
            "valve_pump": f(s.get("tube_vol_valve_pump")),  # 12-way → 3-way
            "pump_merge": f(s.get("tube_vol_pump_merge")),  # 3-way → 합류
            "selector": f(s.get("tube_vol_selector")),      # 12-way 내부
            "switcher": f(s.get("tube_vol_switcher")),      # 3-way 내부
        }
    tj = {}
    for k, v in (sp.get("tjunction_line_vols") or {}).items():
        try:
            tj[int(k)] = f(v)
        except (TypeError, ValueError):
            pass
    rl, rid = f(sp.get("reactor_len_m")), f(sp.get("reactor_id_mm"))
    reactor = math.pi * (rid / 20.0) ** 2 * (rl * 100.0) if (rl and rid) else 0.0  # mL
    mid, mlen = f(sp.get("mixing_line_id_mm", 1.5)), f(sp.get("mixing_line_len_cm", 150.0))
    mixing = math.pi * ((mid / 10.0) / 2.0) ** 2 * mlen if (mid and mlen) else 0.0
    return {"pump": pump, "tj": tj,
            "mixing": round(mixing, 3),                        # 합류 → 반응기
            "post": f(sp.get("post_reactor_vol_ml")),          # 반응기 → 아웃렛
            "collection": f(sp.get("collection_line_vol_ml")),  # 아웃렛 → 분취기
            "reactor": round(reactor, 3)}

# ── 문서 헤더 ──
HEADER = '''<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE CDXML SYSTEM "https://static.chemistry.revvitycloud.com/cdxml/CDXML.dtd" >
<CDXML CreationProgram="VORONOI FlowChem" BondLength="14.40" LabelFont="3" LabelSize="10"
 CaptionFont="3" CaptionSize="9" LineWidth="0.60" BoldWidth="2" HashSpacing="2.50"
 MarginWidth="1.60" color="0" bgcolor="1">
<colortable><color r="1" g="1" b="1"/><color r="0" g="0" b="0"/><color r="1" g="0" b="0"/>
<color r="1" g="1" b="0"/><color r="0" g="1" b="0"/><color r="0" g="1" b="1"/>
<color r="0" g="0" b="1"/><color r="1" g="0" b="1"/></colortable>
<fonttable><font id="3" charset="iso-8859-1" name="Arial"/>
<font id="4" charset="iso-8859-1" name="Courier New"/></fonttable>
<page id="1" BoundingBox="__BB__" Width="__W__" Height="__H__" DrawingSpace="poster">'''
FOOTER = '</page></CDXML>'

def _suffix(name):
    """'Group A'/'Group_B' -> 'A'/'B' (펌프/태그 접미)."""
    s = name.replace("Group", "").replace("group", "").strip(" _-")
    return s or name

def build_from_config(cfg, out_path, reagents=None, deadvols=None, base_top=None):
    """hardware_config(dict) 기반, v3 배선 그대로 CDXML 생성.
    reagents = {group_name: (smiles, display_name[, port])} — 이름만 사용.
    deadvols = deadvols_from_config() 결과(미지정 시 cfg 에서 유도) — 구간 mL 태그."""
    reagents = reagents or {}
    roles = cfg.get("roles", {})
    inv = {it["id"]: it for it in cfg.get("inventory", [])}
    sp = cfg.get("system_params", {})
    dvs = deadvols or deadvols_from_config(cfg)
    dvp = dvs.get("pump", {})

    pumps = [p for p in roles.get("pumps", []) if p.get("position", "inlet") == "inlet"]
    n = len(pumps)
    has_n2 = bool((roles.get("gas") or {}).get("driver_id"))
    has_push = bool((roles.get("push_pump") or {}).get("driver_id"))
    # 위상센서(OCB350) — roles.phase 배정 시, 장치 settings.sensors 채널 키 기준
    #   collect(아웃렛 직전)/reactor_in(리액터 입구) 두 지점에 클램프온 글리프
    ph_id = (roles.get("phase") or {}).get("driver_id")
    ph_keys = []
    if ph_id:
        _ps_set = (inv.get(ph_id) or {}).get("settings") or {}
        ph_keys = list((_ps_set.get("sensors") or {"collect": 0}).keys())
    bpr = sp.get("bpr_bar") or sp.get("back_pressure") or sp.get("bpr_psi")

    # 행 배치: base_top 지정 시 그 값(상단 스킴 아래로 밀 때), 아니면 자동
    if base_top is not None:
        row_top = base_top
    else:
        row_top = 130
        if has_n2:
            row_top = max(row_top, 320 - (n - 1) * ROW_PITCH)
    ys = [row_top + i * ROW_PITCH for i in range(n)]
    y_m = ys[-1] if ys else row_top

    # ── 채널들 ──
    for i, p in enumerate(pumps):
        y = ys[i]
        name = p.get("name", "")
        sfx = _suffix(name)
        dv = p.get("drivers", {})
        routing = p.get("routing",
                        "external_valve" if dv.get("selector") else
                        ("autosampler" if dv.get("sampler") else "internal_valve"))
        rg = reagents.get(name, ("", "", None))
        rg_name = rg[1] if len(rg) > 1 else ""
        rg_port = rg[2] if len(rg) > 2 else None

        d = dvp.get(name, {})
        if routing == "external_valve":
            b_out = part_bottle(X_SRC, y, label=f"S{i+1}", name=rg_name)
            sel = part_12way(X_SEL, y, tag_s=f"12-Way Valve {sfx}", port_num=rg_port)
            sw = part_3way(X_SW, y, tag_s=f"3-Way Valve {sfx}")
            # 병 → 12way(좌) / 12way(우) → 3way L
            line(b_out[0], y, sel["in"][0], y, head="Full")
            line(sel["out"][0], y, sw["L"][0], y, head="Full")
            # ★ 시린지 분기: 3way B ↓ → 아래 펌프 노즐 (주입/토출 왕복)
            sp = part_syringe(X_PUMP, y + 42, label=f"Syringe Pump {sfx}")
            nz = sp["out"]
            polyline([sw["B"], (sw["B"][0], nz[1]), nz])
            # 메인: 3way R → 매니폴드
            line(sw["R"][0], y, X_MAN, y, head="Full")
            chip_at((b_out[0] + sel["in"][0]) / 2, y - 12, d.get("inlet"))
            chip_at((sel["out"][0] + sw["L"][0]) / 2, y - 12, d.get("valve_pump"))
            chip_at((sw["R"][0] + X_MAN) / 2, y - 12, d.get("pump_merge"))
            chip_at(X_SEL, y + 56, d.get("selector"))
            chip_at(X_SW + 34, y + 16, d.get("switcher"))
        elif routing == "autosampler":
            # 앱 배관도(visual_diagram_parts.py) 그대로: 바이알 랙 → 니들 라인 →
            # 시린지(dual: in 좌/out 우) → 매니폴드. (3-way 없음)
            rack = part_vialrack(X_SRC + 60, y, tag_s=f"AS-{sfx}")
            sp = part_syringe(X_PUMP, y + 42, label=f"Syringe Pump {sfx}", dual=True)
            pin, pout = sp["in"], sp["out"]
            mid = (rack["out"][0] + pin[0]) / 2
            polyline([rack["out"], (mid, y), (mid, pin[1]), pin], head_last=True)  # 니들 라인
            polyline([pout, (X_MAN, pout[1]), (X_MAN, y)], head_last=True)         # 펌프→합류
            chip_at((rack["out"][0] + pin[0]) / 2, y - 12, d.get("valve_pump"))
            chip_at((X_PUMP + 52 + X_MAN) / 2, pout[1] - 12, d.get("pump_merge"))
        else:  # internal_valve — 저장조 → 시린지(dual) → 매니폴드
            b_out = part_bottle(X_SEL, y, label=f"S{i+1}", name=rg_name)
            sp = part_syringe(X_PUMP, y + 42, label=f"Syringe Pump {sfx}", dual=True)
            pin, pout = sp["in"], sp["out"]
            mid = (b_out[0] + pin[0]) / 2
            polyline([(b_out[0], y), (mid, y), (mid, pin[1]), pin], head_last=True)
            polyline([pout, (X_MAN, pout[1]), (X_MAN, y)], head_last=True)
            text(X_SW, y - 20, "(internal 2-way)", size=6)
            chip_at((X_PUMP + 52 + X_MAN) / 2, y + 42 - 12, d.get("pump_merge"))

    # ── 매니폴드 스파인 (캐스케이드) + 정션 데드볼륨 ──
    # tj 매핑: 파이프 ys[j-1]→ys[j] = T_{j-1}→T_j = tj_vols[j-1] (j>=2). j=1 은 최상단
    #   단독 강하(엔진 tj 변수 없음) — Qt 배관도와 정합(off-by-one 수정).
    if n >= 2:
        for j in range(1, n):
            part_tee(X_MAN, ys[j])
            line(X_MAN, ys[j - 1], X_MAN, ys[j] - 9.8)
            if j >= 2:
                chip_at(X_MAN + 34, (ys[j - 1] + ys[j]) / 2,
                        dvs.get("tj", {}).get(j - 1))
        oval(X_MAN, ys[0], 2.2, filled=True)

    # ── 메인 라인: 매니폴드 → (N2) → (push) → 반응기 ──
    x_cursor = X_MAN
    top_min = ys[0] - 70
    _x_first = X_N2 if has_n2 else (X_PUSH if has_push else X_RCT - 62)
    chip_at((X_MAN + _x_first) / 2, y_m - 12, dvs.get("mixing"))   # 합류→반응기
    if has_n2:
        line(x_cursor, y_m, X_N2 - 9.8, y_m)
        tee_n2 = part_tee(X_N2, y_m)
        mfc = part_mfc(X_N2, y_m - 96)
        cyl = part_cylinder(X_N2 + 96, y_m - 190)
        # 실린더(아래) → MFC in(우) : ↓ 후 좌향
        polyline([cyl["bottom"], (cyl["bottom"][0], y_m - 96), mfc["in"]], head_last=True)
        # MFC out(좌) → 좌/하 조그 → 티 위로 진입 (v3 조그 재현)
        polyline([mfc["out"], (X_N2 - 30, y_m - 96), (X_N2 - 30, y_m - 30),
                  (X_N2, y_m - 30), tee_n2["T"]], head_last=True)
        top_min = min(top_min, y_m - 190 - 90)
        x_cursor = X_N2
    if has_push:
        line(x_cursor + (9.8 if x_cursor != X_MAN else 0), y_m, X_PUSH - 9.8, y_m)
        tee_p = part_tee(X_PUSH, y_m)
        chk = part_check(X_PUSH, y_m + 56)
        hp = part_hplc(X_PUSH - 4, y_m + 132, tag_s="P-91")
        sol = part_bottle(X_PUSH - 128, y_m + 132, label="SOL")
        line(sol[0], y_m + 132, hp["in"][0], y_m + 132, head="Full")
        polyline([hp["out"], (X_PUSH + 56, y_m + 132), (X_PUSH + 56, y_m + 92),
                  (X_PUSH, y_m + 92), chk["in"]], head_last=False)
        line(chk["out"][0], chk["out"][1], X_PUSH, y_m + 9.8)
        x_cursor = X_PUSH

    # 반응기 (가로 코일, in/out 모두 y_m)
    rct = part_reactor(X_RCT, y_m, tag_s="R-01",
                       sub=(f"{sp.get('reactor_len_m')} m x {sp.get('reactor_id_mm')} mm"
                            if sp.get("reactor_len_m") and sp.get("reactor_id_mm") else ""))
    rin = rct["in"]
    _seg_a = x_cursor + (9.8 if x_cursor != X_MAN else 0)
    if "reactor_in" in ph_keys:
        # PS-02(IN): 리액터 입구 — 센서 몸통 구간에서 라인을 끊어 관통 방지
        _sx = (_seg_a + rin[0]) / 2
        line(_seg_a, y_m, _sx - 8, y_m)
        line(_sx + 8, y_m, rin[0], y_m, head="Full")
        part_phase_sensor(_sx, y_m, tag_s="Inlet Sensor")
    else:
        line(_seg_a, y_m, rin[0], y_m, head="Full")
    rout = rct["out"]
    y_out = rout[1]
    chip_at(X_RCT, y_m + 56, dvs.get("reactor"))      # 반응기 내부 부피

    # (BPR) → 아웃렛 3way
    x_cur = rout[0]
    if bpr:
        b = part_bpr(X_BPR, y_out)
        line(x_cur, y_out, b["in"][0], y_out, head="Full")
        x_cur = b["out"][0]
    out3 = part_outlet3way(X_OUT, y_out, tag_s="")  # 본체 Outlet 캡션 有
    if "collect" in ph_keys:
        # PS-01(COL): 아웃렛 3-way 직전 — 수집 경계 트리거 센서
        _sx2 = out3["L"][0] - 26
        line(x_cur, y_out, _sx2 - 8, y_out)
        line(_sx2 + 8, y_out, out3["L"][0], y_out, head="Full")
        part_phase_sensor(_sx2, y_out, tag_s="Collect Sensor")
    else:
        line(x_cur, y_out, out3["L"][0], y_out, head="Full")
    chip_at((rout[0] + out3["L"][0]) / 2, y_out - 12, dvs.get("post"))  # 반응기→아웃렛

    # Collect(우) → 분취기 / Waste(아래) → 폐기병
    col_id = (roles.get("collector") or {}).get("driver_id")
    if col_id:
        drv = (inv.get(col_id) or {}).get("driver", "").lower()
        kind = ("plate96" if ("plate" in drv or "96" in drv) else
                "colosseum" if ("colosseum" in drv or "콜로세움" in drv or "분획" in drv) else
                "generic")
        fc = part_collector(X_SINK + 10, y_out + 26, kind=kind)
        fin = fc["in"]
        polyline([out3["R"], (fin[0] - 16, y_out), (fin[0] - 16, fin[1]), fin],
                 head_last=True)
        chip_at((out3["R"][0] + fin[0]) / 2, y_out - 12, dvs.get("collection"))  # 아웃렛→분취기
    else:
        line(out3["R"][0], y_out, X_OUT + 64, y_out, head="Full")
    waste_top = part_bottle(X_OUT, y_out + 96, label="W", name="Waste", waste=True)
    line(out3["B"][0], out3["B"][1], X_OUT, y_out + 96 - 32, head="Full")

    # ── 페이지 경계 ──
    bottom = max(y_m + 132 + 70 if has_push else y_m + 90,
                 y_out + 96 + 60,
                 ys[-1] + 42 + 40 + 30)
    page_top = min(0.0, top_min)
    page_w = X_SINK + 120
    header = (HEADER
              .replace("__BB__", f"{page_top:.0f} {page_top:.0f} {page_w:.0f} {bottom:.0f}")
              .replace("__W__", f"{page_w:.0f}")
              .replace("__H__", f"{bottom - page_top:.0f}"))
    xml = header + "\n" + "\n".join(_buf) + "\n" + FOOTER
    open(out_path, "w", encoding="utf-8").write(xml)
    return out_path

def build_reaction_scheme(reactants, conditions_above, conditions_below, product,
                          y_center=110, x_start=70):
    """상단 반응 스킴: SM1 (+ SM2 …) → product, 조건은 화살표 위/아래.
    reactants/product = (smiles, name). SMILES 없거나 파싱 실패 시 이름 박스 폴백."""
    def draw_mol(smi, name, right_x):
        drew = False
        if smi:
            try:
                structure(smi, right_x, y_center); drew = True
            except Exception:
                drew = False
        if not drew:
            rect(right_x - 72, y_center - 16, right_x - 2, y_center + 16)
        if name:
            text(right_x - 37, y_center + 34, name, size=7)

    x = x_start
    for i, rp in enumerate(reactants):
        smi, name = (rp + ("",))[:2] if isinstance(rp, tuple) else (rp, "")
        if i > 0:
            text(x + 8, y_center, "+", size=16, face=1); x += 30
        right = x + 80
        draw_mol(smi, name, right)
        x = right + 16
    # 반응 화살표
    ax0, ax1 = x + 8, x + 150
    line(ax0, y_center, ax1, y_center, head="Full")
    yy = y_center - 13
    for ln in (conditions_above or []):
        text((ax0 + ax1) / 2, yy, ln, size=7); yy -= 11
    yy = y_center + 14
    for ln in (conditions_below or []):
        text((ax0 + ax1) / 2, yy, ln, size=7); yy += 11
    # product
    px = ax1 + 92
    pr = product if (product and product[0]) else None
    if pr:
        draw_mol(pr[0], pr[1] if len(pr) > 1 else "", px)
    else:
        rect(px - 80, y_center - 16, px - 2, y_center + 16)
        text(px - 41, y_center, "product", size=8)
        if product and len(product) > 1 and product[1]:
            text(px - 41, y_center + 34, product[1], size=7)
    return px


def build_notebook_flow(cfg, out_path, reagents=None, scheme=None, deadvols=None):
    """schemes.flow = 상단 반응 스킴(구조식) + 하단 배관도(시약이름). 한 CDXML."""
    _buf.clear(); _id[0] = 1000
    if scheme:
        build_reaction_scheme(scheme.get("reactants", []),
                              scheme.get("conditions_above", []),
                              scheme.get("conditions_below", []),
                              scheme.get("product"),
                              y_center=110, x_start=70)
    # 배관도는 스킴 아래(base_top)로. _buf 는 여기서 지우지 않음(스킴 유지).
    build_from_config(cfg, out_path, reagents=reagents, deadvols=deadvols, base_top=320)
    return out_path


def build_batch_scheme(scheme, out_path):
    """schemes.batch = 반응 스킴(구조식)만 (배관도 없음).

    F-LMJ 참조는 schemes 에 flow 와 batch 를 모두 요구 — batch 는 배치 반응식
    표현(구조식 중심). scheme = NotebookExporter._scheme_for_step 결과.
    """
    _buf.clear(); _id[0] = 2000
    if scheme:
        build_reaction_scheme(scheme.get("reactants", []),
                              scheme.get("conditions_above", []),
                              scheme.get("conditions_below", []),
                              scheme.get("product"),
                              y_center=110, x_start=70)
    page_w, page_h = 720, 220
    header = (HEADER.replace("__BB__", f"0 0 {page_w} {page_h}")
              .replace("__W__", str(page_w)).replace("__H__", str(page_h)))
    xml = header + "\n" + "\n".join(_buf) + "\n" + FOOTER
    open(out_path, "w", encoding="utf-8").write(xml)
    return out_path


# ══════════════════════════════════════════════════════════════════════
# RoboChem-Flex 선형 플랫폼 (SI 모형도 기반, Analysis Module 제외)
#  저장조→[Sampling/Main 펌프]→Sampler(니들+바이알)→N2→코일→Reactor Module
#  →(Analysis 생략)→Collector(니들+바이알)→BPR→Waste, N2(상/하), BPR 2.8bar 복귀
# ══════════════════════════════════════════════════════════════════════

def part_syringe_v(cx, cy, label="", tag=""):
    """세로 시린지 펌프(프레임+배럴+플런저). 포트: bottom/left/right."""
    w, h = 30, 64
    rect(cx - w / 2 - 6, cy - h / 2 - 6, cx + w / 2 + 6, cy + h / 2 + 6)  # 프레임
    rect(cx - w / 2, cy - h / 2 + 8, cx + w / 2, cy + h / 2)              # 배럴
    line(cx, cy - h / 2 + 8, cx, cy - h / 2 - 6)                          # 플런저 로드
    line(cx - 8, cy - h / 2 - 6, cx + 8, cy - h / 2 - 6)                  # 플런저 노브
    rect(cx - w / 2 + 3, cy + 4, cx + w / 2 - 3, cy + h / 2 - 3)          # 액면
    if label:
        text(cx, cy + h / 2 + 16, label, size=8, face=1)
    if tag:
        tag(cx, cy + h / 2 + 27, tag)
    return {"bottom": (cx, cy + h / 2 + 6), "left": (cx - w / 2 - 6, cy),
            "right": (cx + w / 2 + 6, cy), "top": (cx, cy - h / 2 - 6)}

def part_module_sampler(cx, cy, label="Sampler", tag="", nv=12):
    """Sampler/Collector 모듈: 외곽 + 헤더 스트립 + 바이알 행 + 니들 인젝터.
    포트: feed(상단 니들측), out(우), in(좌)."""
    w, h = 240, 108
    left, top, right, bot = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    rect(left, top, right, bot)                     # 외곽
    rect(left, top, right, top + 20)                # 헤더 스트립
    text(left + 6, top + 10, label, size=9, face=1, center=False)
    # 바이알 행 (하단)
    vw = (w - 24) / nv
    for k in range(nv):
        vx = left + 12 + k * vw + vw / 2
        rect(vx - 4.5, bot - 34, vx + 4.5, bot - 8)   # 바이알
        oval(vx, bot - 34, 4.5, 2.4)                  # 캡
    # 니들 인젝터 (헤더에서 바이알로 사선 하강)
    nx = left + w * 0.40
    line(nx + 10, top + 20, nx, top + 40)            # 암
    line(nx, top + 40, nx, bot - 34)                 # 니들
    oval(nx, top + 40, 3, filled=True)
    return {"feed": (nx + 10, top), "out": (right, cy - 8), "in": (left, cy - 8),
            "top": (nx + 10, top)}

def part_reactor_module(cx, cy, tag="R-01", sub=""):
    """Reactor Module: 둥근 모듈 박스 + 내부 세로 코일. in=좌상, out=우상."""
    w, h = 120, 190
    left, top, right, bot = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    rect(left, top, right, bot)
    # 내부 세로 코일 (겹친 타원)
    n = 8
    ry = (h - 60) / 2.0
    ccy = cy + 6
    for i in range(n):
        oval(cx, ccy - ry + i * (2 * ry / (n - 1)), 22, 9)
    # in/out 스텁 (상단 좌/우)
    line(left, top + 22, left - 14, top + 22)
    oval(left - 14, top + 22, 1.6, filled=True)
    line(right, top + 22, right + 14, top + 22)
    oval(right + 14, top + 22, 1.6, filled=True)
    text(cx, top + 12, "Reactor Module", size=8, face=1)
    if sub:
        text(cx, bot - 10, sub, size=7, font=4)
    tag(cx, bot + 12, tag)
    return {"in": (left - 14, top + 22), "out": (right + 14, top + 22)}

def part_gas_reg(cx, cy, label="N2", tag=""):
    """N2 실린더 + 레귤레이터 게이지. out=상단."""
    w, h = 30, 90
    rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    oval(cx, cy - h / 2, w / 2)                       # 둥근 상단
    text(cx, cy, label, size=10, face=1)
    oval(cx, cy - h / 2 - 16, 8)                      # 게이지
    line(cx, cy - h / 2, cx, cy - h / 2 - 8)
    if tag:
        tag(cx, cy + h / 2 + 10, tag)
    return {"out": (cx, cy - h / 2 - 24), "side": (cx + w / 2, cy)}

def part_gauge(cx, cy, r=9):
    """압력/센서 게이지 (원 + 바늘)."""
    oval(cx, cy, r)
    line(cx, cy, cx + r * 0.6, cy - r * 0.6)
    return {"in": (cx - r, cy), "out": (cx + r, cy)}

def part_valve_s(cx, cy, letter="S"):
    """소형 2/3-way 밸브 (사각 + 글자)."""
    rect(cx - 8, cy - 8, cx + 8, cy + 8)
    text(cx, cy, letter, size=7, face=1)
    return {"L": (cx - 8, cy), "R": (cx + 8, cy), "B": (cx, cy + 8), "T": (cx, cy - 8)}

def build_robochem_platform(cfg, out_path):
    """RoboChem-Flex SI 모형도 재현 (Analysis Module 제외)."""
    sp = (cfg or {}).get("system_params", {})
    # ── 좌표 레인 ──
    X_RES, X_PUMP = 60, 155
    Y_SAMPUMP, Y_MAINPUMP = 250, 500
    X_COIL3, Y_COIL3 = 285, 165
    X_SAMPLER, Y_SAMPLER = 360, 330
    X_N2LO, Y_N2LO = 235, 585
    X_REACT, Y_REACT = 640, 330
    X_COLL, Y_COLL = 1010, 330
    X_WASTE, Y_WASTE = 1080, 560
    X_N2HI, Y_N2HI = 1155, 140

    # ── 펌프 & 저장조 ──
    res = part_bottle(X_RES, 520, name="Reservoir")
    samp_pump = part_syringe_v(X_PUMP, Y_SAMPUMP, "Sampling pump", "P-1")
    main_pump = part_syringe_v(X_PUMP, Y_MAINPUMP, "Main pump", "P-2")
    # 저장조 → 두 펌프 (좌측 라이저)
    line(res[0], 520, X_RES + 4, 520)
    polyline([(X_RES + 4, 520), (X_RES + 4, Y_SAMPUMP), samp_pump["left"]], head_last=True)
    polyline([(X_RES + 4, 520), (X_RES + 4, Y_MAINPUMP), main_pump["left"]], head_last=True)

    # ── Sampler (니들+바이알) ──
    sampler = part_module_sampler(X_SAMPLER, Y_SAMPLER, "Sampler", "SM-01")
    # Sampling pump → 코일(3) → Sampler feed(상단)
    polyline([samp_pump["top"], (X_PUMP, Y_COIL3)], head_last=False)
    # 가로 코일(3)
    _coil_h(X_COIL3, Y_COIL3, loops=4)
    polyline([(X_PUMP, Y_COIL3), (X_COIL3 - 40, Y_COIL3)])
    polyline([(X_COIL3 + 40, Y_COIL3), (sampler["top"][0], Y_COIL3),
              sampler["top"]], head_last=True)

    # ── N2 (하단) → 메인 라인 ──
    n2lo = part_gas_reg(X_N2LO, Y_N2LO, "N2")
    vS = part_valve_s(X_N2LO + 80, Y_N2LO - 70, "S")
    polyline([n2lo["out"], (X_N2LO, Y_N2LO - 70), vS["L"]], head_last=True)
    # Main pump → 우 → 위 → 메인 합류(샘플러 out 라인)
    y_main = Y_SAMPLER - 8
    polyline([main_pump["right"], (X_SAMPLER + 150, Y_MAINPUMP),
              (X_SAMPLER + 150, y_main)], head_last=False)
    # N2(S밸브) → 메인 라인
    polyline([vS["R"], (X_SAMPLER + 120, Y_N2LO - 70),
              (X_SAMPLER + 120, y_main)], head_last=True)
    # Sampler out → 합류점 → 코일(10) → Reactor in
    react = part_reactor_module(X_REACT, Y_REACT, "R-01",
                                sub=(f"{sp.get('reactor_len_m')} m x {sp.get('reactor_id_mm')} mm"
                                     if sp.get("reactor_len_m") and sp.get("reactor_id_mm") else ""))
    jx = X_SAMPLER + 150
    line(sampler["out"][0], sampler["out"][1], jx, y_main)
    oval(jx, y_main, 2.4, filled=True)                # 합류 티
    _coil_h(jx + 60, y_main, loops=3)
    polyline([(jx, y_main), (jx + 20, y_main)])
    polyline([(jx + 100, y_main), (react["in"][0], y_main),
              (react["in"][0], react["in"][1]), react["in"]], head_last=True)

    # ── Reactor → (Analysis 생략) → Collector ──
    g15 = part_gauge(react["out"][0] + 22, react["out"][1])
    line(react["out"][0], react["out"][1], g15["in"][0], g15["out"][1])
    coll = part_module_sampler(X_COLL, Y_COLL, "Collector", "FC-01")
    # 반응기 out → 위로 → 오른쪽 → Collector in (Analysis 자리 비움)
    polyline([g15["out"], (g15["out"][0], Y_REACT - 150),
              (coll["in"][0] - 40, Y_REACT - 150),
              (coll["in"][0] - 40, coll["in"][1]), coll["in"]], head_last=True)

    # ── N2 (상단) → Collector 상단 ──
    n2hi = part_gas_reg(X_N2HI, Y_N2HI, "N2")
    g25 = part_gauge(X_N2HI - 110, Y_N2HI - 30)
    polyline([n2hi["side"], (g25["out"][0], Y_N2HI), g25["out"]], head_last=False)
    polyline([g25["in"], (coll["top"][0], Y_N2HI - 30), coll["top"]], head_last=True)

    # ── Collector → BPR(21) → 체크 → Waste ──
    waste = part_bottle(X_WASTE, Y_WASTE, name="Waste", waste=True)
    bpr21 = part_bpr((X_COLL + X_WASTE) / 2 - 40, Y_COLL + 180, "BPR")
    chk22 = part_check(X_WASTE - 60, Y_WASTE - 40)
    chk20 = part_check(X_WASTE + 60, Y_WASTE - 40)
    polyline([coll["out"], (coll["out"][0] + 20, Y_COLL - 8),
              (coll["out"][0] + 20, Y_COLL + 180), bpr21["in"]], head_last=False)
    polyline([bpr21["out"], (chk22["in"][0], Y_COLL + 180),
              (chk22["in"][0], chk22["in"][1]), chk22["in"]], head_last=True)
    line(chk22["out"][0], chk22["out"][1], X_WASTE, waste[1] - 40, head="Full")

    # ── BPR 2.8 bar (저장조 복귀) ──
    bpr23 = part_bpr(340, 630, "2.8 bar")
    polyline([main_pump["bottom"], (X_PUMP, 630), bpr23["in"]], head_last=False)
    polyline([bpr23["out"], (X_RES - 10, 630), (X_RES - 10, 540), (res[0] - 22, 540)],
             head_last=True)

    page_w, page_h = 1280, 720
    header = (HEADER.replace("__BB__", f"0 0 {page_w} {page_h}")
              .replace("__W__", str(page_w)).replace("__H__", str(page_h)))
    xml = header + "\n" + "\n".join(_buf) + "\n" + FOOTER
    open(out_path, "w", encoding="utf-8").write(xml)
    return out_path

def _coil_h(cx, cy, loops=3, rx=8, ry=16, step=8):
    """가로 코일 (겹친 투명 타원)."""
    n = max(3, loops * 3)
    span = (n - 1) * step
    x0 = cx - span / 2.0
    for i in range(n):
        oval(x0 + i * step, cy, rx, ry)

if __name__ == "__main__":
    import json, os
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(os.path.dirname(here), "hardware_config.json"),
                         encoding="utf-8"))
    reagents = {
        "Group A": ("", "6-Bromoisoquinoline", 2),
        "Group_B": ("", "Boc-azetidine-Br", 5),
        "Group_C": ("", "2,6-Lutidine", 4),
        "Group_D": ("", "DMA", 1),
    }
    out = os.path.join(here, "PID_voronoi.cdxml")
    print(">>> wrote", build_from_config(cfg, out, reagents))
