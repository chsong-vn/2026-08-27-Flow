# -*- coding: utf-8 -*-
"""Diagram v3 적대적 자기검증 — 하드웨어 조합 전수 + 상태 전이 + 렌더.

조합축: 채널수 1~4 × 라우팅(external/internal/autosampler 혼합) ×
        push HPLC × 질소 × BPR × 분취기(plate/colosseum)
단언: A 파츠 존재/레지스트리 정합  B 파츠 bbox 비중첩  C 씬 포함
      D 흐름 상태 전이(토출 정방향/흡입 역방향/아웃렛 방향/바이알·포트 맵핑)
렌더: 조합별 PNG (육안 적대 검토용)
"""
import os, sys, itertools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.environ.get("OUTDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPainter, QImage
from PyQt5.QtCore import QRectF

app = QApplication(sys.argv)

from ui.visual_diagram_parts import FlowDiagramWidget, Part
from ui.colors import set_active_dark_mode

set_active_dark_mode(True)
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


class Cfg:
    def __init__(self, routings, n2=False, bpr=False):
        self.ACTIVE_PUMPS = [f"Group_{chr(65+i)}" for i in range(len(routings))]
        self.PUMP_ROUTING = {p: r for p, r in zip(self.ACTIVE_PUMPS, routings)}
        self.PUMP_SAMPLER_MAP = {}
        self.config_data = {"system_params": {"nitrogen_line": n2, "bpr_enabled": bpr}}


class Samp:
    vial_positions = {"A1": 1, "A2": 2, "A3": 3, "B1": 4, "waste": 9, "rinse": 8}
    current_vial = None


class Coll:
    def __init__(self, plate):
        if plate:
            self.get_well_id = lambda n: f"A_{n}"


class HW:
    def __init__(self, cfg):
        self.sampler_by_pump = {p: Samp() for p, r in cfg.PUMP_ROUTING.items()
                                if r == "autosampler"}


class MapMgr:
    def get_inlet(self, p, port):
        return {"name": f"Reag{port}"}


class App:
    def __init__(self, cfg, push, plate, has_col=True):
        self.push_pump = object() if push else None
        self.collector = Coll(plate) if has_col else None
        self.hw_mgr = HW(cfg)
        self.map_mgr = MapMgr()


COMBOS = [   # (이름, 라우팅, push, n2, bpr, plate, collector유무)
    ("c1_1ext",        ["external_valve"], 0, 0, 0, 0, 1),
    ("c2_2ext_push",   ["external_valve"] * 2, 1, 0, 0, 1, 1),
    ("c3_3ext_full",   ["external_valve"] * 3, 1, 1, 1, 1, 1),
    ("c4_4ext_n2",     ["external_valve"] * 4, 0, 1, 0, 1, 1),
    ("c5_ext_as_push", ["external_valve", "autosampler"], 1, 0, 0, 1, 1),
    ("c6_1as",         ["autosampler"], 0, 0, 0, 0, 1),
    ("c7_mix_full",    ["internal_valve", "autosampler", "external_valve"], 1, 1, 1, 1, 1),
    ("c8_5ext_full",   ["external_valve"] * 5, 1, 1, 1, 1, 1),
    ("c9_5mix_2as",    ["external_valve", "autosampler", "internal_valve",
                        "autosampler", "external_valve"], 1, 1, 0, 1, 1),
    ("c10_nocol",      ["external_valve"] * 2, 0, 0, 0, 0, 0),  # AUTOCOLLECTOR off
]

for name, routings, push, n2, bpr, plate, has_col in COMBOS:
    print(f"=== {name} ===")
    cfg = Cfg(routings, n2=bool(n2), bpr=bool(bpr))
    a = App(cfg, push, plate, has_col=bool(has_col))
    w = FlowDiagramWidget(pump_configs=[], active_pumps=cfg.ACTIVE_PUMPS, inventory=[])
    w.configure(cfg, a)

    n_ext = routings.count("external_valve")
    n_as = routings.count("autosampler")
    n_int = routings.count("internal_valve")
    it = w.items_
    check(f"{name} 펌프 {len(routings)}", len(it["pumps"]) == len(routings))
    check(f"{name} 12way={n_ext}", len(it["selectors"]) == n_ext)
    check(f"{name} 3way={n_ext}", len(it["switchers"]) == n_ext)
    check(f"{name} 랙={n_as}", len(it["racks"]) == n_as)
    check(f"{name} 병={n_ext + n_int}", len(it["sources"]) == n_ext + n_int)
    check(f"{name} N2 {'유' if n2 else '무'}", (w.n2_parts is not None) == bool(n2))
    check(f"{name} push {'유' if push else '무'}", (w.push_parts is not None) == bool(push))
    check(f"{name} BPR {'유' if bpr else '무'}", (w.bpr is not None) == bool(bpr))
    if has_col:
        check(f"{name} 분취기 plate={plate}", w.collector_part.plate == bool(plate))
    else:
        check(f"{name} 분취기 없음(스텁)", w.collector_part is None)
    # 데드볼륨 편집 메타: 라우팅별 노출 키 분기 확인
    for pn, rt in cfg.PUMP_ROUTING.items():
        keys = {pl.edit[3] for pl in (it["src_pipes"].get(pn, [])
                                      + it["pump_link"].get(pn, [])
                                      + it["ch_pipes"].get(pn, [])) if pl.edit}
        for prt in (it["selectors"].get(pn), it["switchers"].get(pn)):
            if prt is not None and prt.edit:
                keys.add(prt.edit[3])
        if rt == "external_valve":
            exp = {"tube_vol_inlet", "tube_vol_valve_pump", "tube_vol_selector",
                   "tube_vol_switcher", "tube_vol_pump_merge"}
        else:
            exp = {"tube_vol_valve_pump", "tube_vol_pump_merge"}
        check(f"{name} {pn} 편집키 {rt}", keys == exp, str(keys ^ exp))
    if len(routings) >= 2:
        # @codesyncer: 조합흐름 tj 구간 = n-2개 (T_k→T_{k+1}, k=1..n-2). 엔진
        #   _compute_plug_timing 이 읽는 tjunction_line_vols[1..n-2] 와 정합.
        #   (구 기대값 n-1 은 off-by-one 버그 — 최상단 단독강하엔 tj 변수 없음.)
        tj_ids = sorted(pl.edit[2] for pl in w.spine
                        if pl.edit and pl.edit[1] == "tj")
        check(f"{name} 정션 편집 {max(0, len(routings)-2)}개",
              tj_ids == list(range(1, len(routings) - 1)))

    # B. 파츠 bbox 비중첩 (그림 카드끼리 — 파이프 제외, 14px^2 이상 겹침 금지)
    parts = [x for x in w.scene().items() if isinstance(x, Part)]
    bad = []
    for x, y2 in itertools.combinations(parts, 2):
        r = x.sceneBoundingRect().intersected(y2.sceneBoundingRect())
        if r.width() > 14 and r.height() > 14:
            bad.append(f"{type(x).__name__}×{type(y2).__name__}"
                       f"({r.width():.0f}x{r.height():.0f})")
    check(f"{name} 파츠 비중첩", not bad, "; ".join(bad[:4]))

    # C. 씬 포함
    sr = w.scene().sceneRect()
    inside = all(sr.contains(x.sceneBoundingRect()) for x in parts)
    check(f"{name} 씬 포함", inside)

    # D. 상태 전이
    p0 = cfg.ACTIVE_PUMPS[0]
    # 토출: 정방향 run
    w.update_realtime({p0: {"running": True, "refilling": False}},
                      selector_ports={p0: 7}, outlet_valve_pos=2,
                      reactor_temp=57.5, collector_pos=5, collector_well="A_A5",
                      push_running=bool(push),
                      sampler_vials={p: "A2" for p in it["racks"]},
                      switcher_pos={p0: 2})
    ch = it["ch_pipes"][p0][0]
    check(f"{name} 토출=정방향 흐름", ch.flowing and ch.direction == 1 and ch.color_key == "run")
    for lk in it["pump_link"].get(p0, []):
        check(f"{name} 토출: 펌프→밸브(-1)", lk.flowing and lk.direction == -1
              and lk.color_key == "run")
    if p0 in it["selectors"]:
        check(f"{name} 포트 배지=7", it["selectors"][p0].port_num == 7)
        check(f"{name} 시약명 맵핑", it["selectors"][p0].reagent == "Reag7")
    for rp, rack in it["racks"].items():
        check(f"{name} 바이알 맵핑 A2", rack.current == "A2")
    check(f"{name} 아웃렛 COLLECT", w.outlet.collect and w.collect_pipe.active
          and not w.waste_pipe.active)
    check(f"{name} 리액터 온도", w.reactor.temp == 57.5)
    if has_col:
        check(f"{name} 분취 위치", w.collector_part.pos == 5)
    if push:
        check(f"{name} push 흐름", all(q.flowing for q in w.push_parts[1]))
    # 흡입: 역방향 fill
    w.update_realtime({p0: {"running": False, "refilling": True}},
                      outlet_valve_pos=1, switcher_pos={p0: 1})
    src = it["src_pipes"][p0][0]
    check(f"{name} 흡입: 소스→펌프(+1) fill", src.flowing and src.direction == 1
          and src.color_key == "fill")
    for lk in it["pump_link"].get(p0, []):
        check(f"{name} 흡입: 밸브→펌프(+1)", lk.flowing and lk.direction == 1
              and lk.color_key == "fill")
    check(f"{name} 아웃렛 WASTE 복귀", not w.outlet.collect and not w.collect_pipe.active)
    if p0 in it["switchers"]:
        check(f"{name} 3way SOURCE 방향", it["switchers"][p0].direction == 1)

    # 렌더
    w.update_realtime({p0: {"running": True}}, selector_ports={p0: 7},
                      outlet_valve_pos=2, reactor_temp=57.5, collector_pos=5,
                      push_running=bool(push),
                      sampler_vials={p: "A2" for p in it["racks"]})
    sr = w.scene().sceneRect()
    img = QImage(int(sr.width()), int(sr.height()), QImage.Format_ARGB32)
    img.fill(0xFF0E1320)
    pt = QPainter(img)
    pt.setRenderHint(QPainter.Antialiasing)
    w.scene().render(pt, QRectF(img.rect()), sr)
    pt.end()
    path = os.path.join(OUT, f"diagram_{name}.png")
    img.save(path)
    print(f"  render → {path}")
    w.deleteLater()

# ══ 핫리로드 재구성: 같은 인스턴스 3ch → 4ch → 5ch+N2(roles.gas) ══
print("=== reconfig (hot-reload 시나리오) ===")
cfg3 = Cfg(["external_valve"] * 3)
a3 = App(cfg3, 0, 1)
w = FlowDiagramWidget(pump_configs=[], active_pumps=cfg3.ACTIVE_PUMPS, inventory=[])
w.configure(cfg3, a3)
check("R0 초기 3ch", len(w.items_["pumps"]) == 3)

cfg4 = Cfg(["external_valve"] * 4)          # 그룹 추가 후 핫리로드
w.configure(cfg4, App(cfg4, 0, 1))
check("R1 재구성 4ch (그룹 추가 반영)", len(w.items_["pumps"]) == 4,
      f"({len(w.items_['pumps'])})")
check("R1 4ch 12way도 4개", len(w.items_["selectors"]) == 4)

cfg5 = Cfg(["external_valve"] * 5)
cfg5.config_data["roles"] = {"gas": {"driver_id": "mfc01"}}   # 플래그 없이 roles.gas 만
w.configure(cfg5, App(cfg5, 1, 1))
check("R2 재구성 5ch", len(w.items_["pumps"]) == 5)
check("R2 roles.gas → N2/MFC 어셈블리 생성", w.n2_parts is not None)
check("R2 push 동시", w.push_parts is not None)

class _HW:  # 런타임 MFC 인스턴스 경로
    sampler_by_pump = {}
    mfc = object()
cfg2 = Cfg(["external_valve"] * 2)
a2 = App(cfg2, 0, 1); a2.hw_mgr = _HW()
w.configure(cfg2, a2)
check("R3 hw_mgr.mfc → N2 표시 + 2ch 축소", w.n2_parts is not None
      and len(w.items_["pumps"]) == 2)
# 재구성 후 실시간 갱신 무크래시
w.update_realtime({cfg2.ACTIVE_PUMPS[0]: {"running": True}},
                  outlet_valve_pos=2, gas_flowing=True)
check("R3 재구성 후 realtime+gas 무크래시", True)

# ══ QUAD 합류 토폴로지 (2026-08-12 배관 재구성): tjunction_entry_map ══
print("=== quad (entry_map 토폴로지) ===")
from ui.visual_diagram_parts import PartTee

cfg_q = Cfg(["external_valve"] * 4, n2=True)
cfg_q.tjunction_entry_map = {"Group_A": 1, "Group_B": 1, "Group_C": 2, "Group_D": 2}
a_q = App(cfg_q, 0, 1)
wq = FlowDiagramWidget(pump_configs=[], active_pumps=cfg_q.ACTIVE_PUMPS, inventory=[])
wq.configure(cfg_q, a_q)

# 스파인 tj 칩 = QUAD1→QUAD2 (key 1) 하나뿐 — 같은 정션 내부 수집엔 칩 없음
tj_spine = sorted(pl.edit[2] for pl in wq.spine if pl.edit and pl.edit[1] == "tj")
check("Q1 스파인 tj = [1] (QUAD1→QUAD2)", tj_spine == [1], str(tj_spine))
# 수평런 tj[2] 칩 (QUAD2→가스T) — VolChip 으로 존재
tj_chips = [c for c in wq._vol_chips
            if c.edit and c.edit[1] == "tj" and c.edit[2] == 2]
check("Q2 수평런 tj[2] 칩 (QUAD2→가스T)", len(tj_chips) == 1
      and "가스T" in tj_chips[0].edit[0], str([c.edit[0] for c in tj_chips]))
# QUAD 태그 티 — 그룹 마지막 행에만
quad_tags = sorted(x.tag for x in wq.scene().items()
                   if isinstance(x, PartTee) and x.tag)
check("Q3 QUAD-1/QUAD-2 태그", quad_tags == ["QUAD-1", "QUAD-2"], str(quad_tags))
# mixing 칩 라벨 = 가스T→센서→리액터
mix_chips = [c for c in wq._vol_chips if c.edit and c.edit[1] == "sp"
             and c.edit[2] == "mixing"]
check("Q4 mixing 칩 = 가스T 하류 라벨", len(mix_chips) == 1
      and "가스T" in mix_chips[0].edit[0], str([c.edit[0] for c in mix_chips]))
# 레거시 대조: entry_map 없으면 기존 캐스케이드 (tj = [1, 2] 스파인)
cfg_l = Cfg(["external_valve"] * 4, n2=True)
wl = FlowDiagramWidget(pump_configs=[], active_pumps=cfg_l.ACTIVE_PUMPS, inventory=[])
wl.configure(cfg_l, App(cfg_l, 0, 1))
tj_leg = sorted(pl.edit[2] for pl in wl.spine if pl.edit and pl.edit[1] == "tj")
check("Q5 레거시 스파인 불변 [1,2]", tj_leg == [1, 2], str(tj_leg))
leg_tags = [x.tag for x in wl.scene().items() if isinstance(x, PartTee) and x.tag]
check("Q6 레거시 QUAD 태그 없음", not leg_tags, str(leg_tags))

# ── 2026-08-12b: 실물 4-way 크로스 렌더 검증 ─────────────────────
# Q7 QUAD 티 = 물리 4포트 크로스 (사용 스터브 + 캡 마개 = L/T/B/R 전부)
qts = {x.tag: x for x in wq.scene().items() if isinstance(x, PartTee) and x.tag}
q7ok = all(set(t.used) | set(t.capped) == {"L", "T", "B", "R"}
           for t in qts.values())
check("Q7 QUAD 티 = 물리 4포트(크로스)", q7ok,
      str({k: (t.used, t.capped) for k, t in qts.items()}))
check("Q7b QUAD-2 4포트 전부 배관(T=유입·L=C·B=D·R=출구)",
      set(qts["QUAD-2"].used) == {"L", "T", "B", "R"}, str(qts["QUAD-2"].used))
check("Q7c push 없음 → QUAD-1 R 캡", "R" in qts["QUAD-1"].capped,
      str(qts["QUAD-1"].capped))

# Q8 수평런이 QUAD-2 행으로 이동 + tj[e_max]=실제 파이프
Y_Q2 = 70 + 60 + 2 * 136   # ys[2] (그룹 2 첫 행)
check("Q8 리액터 y = QUAD-2 행", abs(wq.reactor.pos().y() - Y_Q2) < 1,
      str(wq.reactor.pos().y()))
check("Q8b tj[2] 실파이프(부유 칩 핵 제거)", wq.tj_out_pipe is not None
      and wq.tj_out_pipe.edit and wq.tj_out_pipe.edit[2] == 2)

# Q9 QUAD + push: solvent 코리도어 → QUAD-1 'R' 포트 도킹, 런의 push 티 폐지
cfg_qp = Cfg(["external_valve"] * 4, n2=True)
cfg_qp.tjunction_entry_map = {"Group_A": 1, "Group_B": 1, "Group_C": 2, "Group_D": 2}
wqp = FlowDiagramWidget(pump_configs=[], active_pumps=cfg_qp.ACTIVE_PUMPS, inventory=[])
wqp.configure(cfg_qp, App(cfg_qp, 1, 1))
qts_p = {x.tag: x for x in wqp.scene().items() if isinstance(x, PartTee) and x.tag}
check("Q9 push → QUAD-1 R 배관", "R" in qts_p["QUAD-1"].used,
      str(qts_p["QUAD-1"].used))
_endp = wqp.push_parts[1][-1].pts[-1]
Y_Q1 = 70 + 60 + 1 * 136   # ys[1] (그룹 1 마지막 행 = QUAD-1)
check("Q9b solvent 라인 QUAD-1 도킹", abs(_endp.x() - (470 + 14)) < 1
      and abs(_endp.y() - Y_Q1) < 1, f"({_endp.x():.0f},{_endp.y():.0f})")
_push_tees = [x for x in wqp.scene().items() if isinstance(x, PartTee)
              and abs(x.pos().x() - 655) < 1]
check("Q9c 런의 push 티 폐지", not _push_tees, str(len(_push_tees)))

# Q10 tj 분절 흐름 = 엔진 F_j 모델 정합: 구간 k 이전 진입 채널만 tj[k] 구동
wq.update_realtime({"Group_C": {"running": True}}, outlet_valve_pos=1)
check("Q10 C만 구동 → tj[1] 무흐름", not wq.spine[0].flowing)
wq.update_realtime({"Group_A": {"running": True}}, outlet_valve_pos=1)
check("Q10b A 구동 → tj[1] 흐름", wq.spine[0].flowing)
wqp.update_realtime({}, push_running=True, outlet_valve_pos=1)
check("Q10c push(solvent)만 → tj[1]+tj[2] 흐름", wqp.spine[0].flowing
      and wqp.tj_out_pipe.flowing and all(q.flowing for q in wqp.push_parts[1]))

# Q12 그룹 부분집합 (2026-08-13): 장비는 있을 수도 없을 수도 —
# (a) A/D 만 (entries 1,2 — 현 실구성 형태): QUAD 두 개, 런=둘째 행
cfg_s = Cfg(["external_valve"] * 2, n2=True)
cfg_s.tjunction_entry_map = {"Group_A": 1, "Group_B": 2}
ws = FlowDiagramWidget(pump_configs=[], active_pumps=cfg_s.ACTIVE_PUMPS, inventory=[])
ws.configure(cfg_s, App(cfg_s, 0, 1))
tags_s = sorted(x.tag for x in ws.scene().items() if isinstance(x, PartTee) and x.tag)
check("Q12a A/D형 QUAD-1/2 태그", tags_s == ["QUAD-1", "QUAD-2"], str(tags_s))
check("Q12a 런 = 둘째 행", abs(ws.reactor.pos().y() - (70 + 60 + 1 * 136)) < 1,
      str(ws.reactor.pos().y()))
check("Q12a tj[1] 스파인 + tj[2] 런파이프",
      [pl.edit[2] for pl in ws.spine if pl.edit] == [1]
      and ws.tj_out_pipe is not None and ws.tj_out_pipe.edit[2] == 2)
# (b) C/D 만 (entries 2,2 — vals 가 1 로 시작 안 함): 단일 정션 QUAD-2, tj[2] 런
cfg_c = Cfg(["external_valve"] * 2, n2=True)
cfg_c.tjunction_entry_map = {"Group_A": 2, "Group_B": 2}
wc = FlowDiagramWidget(pump_configs=[], active_pumps=cfg_c.ACTIVE_PUMPS, inventory=[])
wc.configure(cfg_c, App(cfg_c, 0, 1))
tags_c = sorted(x.tag for x in wc.scene().items() if isinstance(x, PartTee) and x.tag)
check("Q12b C/D형 단일 정션 QUAD-2", tags_c == ["QUAD-2"], str(tags_c))
check("Q12b 스파인 tj 없음 + tj[2] 런파이프", not [p for p in wc.spine if p.edit]
      and wc.tj_out_pipe is not None and wc.tj_out_pipe.edit[2] == 2,
      str(wc.tj_out_pipe.edit if wc.tj_out_pipe else None))
# 부분집합 realtime 스모크 (엔터리 분절 흐름 포함)
ws.update_realtime({"Group_B": {"running": True}}, outlet_valve_pos=1)
check("Q12c A/D형: D만 구동 → tj[1] 무흐름", not ws.spine[0].flowing)

# Q11 quad 네 케이스 bbox 비중첩 + 씬 포함 (레거시 콤보와 동일 기준)
for _nm, _w in (("quad", wq), ("quad_push", wqp), ("quad_2p_AD", ws),
                ("quad_2p_CD", wc)):
    parts_q = [x for x in _w.scene().items() if isinstance(x, Part)]
    bad_q = []
    for x, y2 in itertools.combinations(parts_q, 2):
        r = x.sceneBoundingRect().intersected(y2.sceneBoundingRect())
        if r.width() > 14 and r.height() > 14:
            bad_q.append(f"{type(x).__name__}×{type(y2).__name__}"
                         f"({r.width():.0f}x{r.height():.0f})")
    check(f"Q11 {_nm} 파츠 비중첩", not bad_q, "; ".join(bad_q[:4]))
    sr_q = _w.scene().sceneRect()
    check(f"Q11b {_nm} 씬 포함",
          all(sr_q.contains(x.sceneBoundingRect()) for x in parts_q))

# 렌더 (육안 검토용) — 기본 quad + push(solvent→QUAD-1) 두 장
for _nm, _w, _st in (("quad_topology", wq, {}),
                     ("quad_push", wqp, {"push_running": True})):
    _w.update_realtime({"Group_A": {"running": True}},
                       outlet_valve_pos=2, gas_flowing=False, **_st)
    sr = _w.scene().sceneRect()
    img = QImage(int(sr.width()), int(sr.height()), QImage.Format_ARGB32)
    img.fill(0xFF0E1320)
    pt = QPainter(img)
    pt.setRenderHint(QPainter.Antialiasing)
    _w.scene().render(pt, QRectF(img.rect()), sr)
    pt.end()
    path = os.path.join(OUT, f"diagram_{_nm}.png")
    img.save(path)
    print(f"  render → {path}")

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
