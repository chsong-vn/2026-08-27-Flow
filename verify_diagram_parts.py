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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("OUTDIR", os.path.dirname(os.path.abspath(__file__)))

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

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
