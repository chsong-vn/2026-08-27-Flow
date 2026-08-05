# -*- coding: utf-8 -*-
"""HTE droplet 모드 E2E 적대 검증 — 실 StrictSequenceEngine + Sim 하드웨어.

검증축:
 A 슬러그별 수집 경계(부피 적분: 펌프+질소 치환) = v_head+Σ(퍼지+슬러그+스페이서)
 B 스페이서/서비스 구간 Outlet=WASTE, 웰 순차(1,2,3)
 C 세척 프로토콜 순서 = 질소 → 용매 플러그 → 질소 (마지막 WASTE 이후)
 D 종료 상태: MFC off·Outlet WASTE·타이머 완주
 E MFC 미구성 시 SafetyError 사전 차단
"""
import os, sys, time, threading

try:
    sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔서 ≈/⚠ 출력 방어
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager, SafetyError
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

T0 = time.time()
EV = []
LK = threading.Lock()


def rec(kind, name, data=None):
    with LK:
        EV.append((time.time() - T0, kind, name, data))


class SimDriver:
    def __init__(self, pump): self.pump = pump
    def is_stopped(self): return not self.pump.running


class SimPump(ChemyxSmartPump):
    def __init__(self, name, capacity=6.0):
        self.name = name; self.capacity = capacity
        self.current_vol = 0.0; self.running = False
        self.target_flow = 0.0; self.status = "Idle"
        self.is_refilling = False; self._abort_refill = False
        self.wash_volume = 3.0; self.prime_rate = 8.0
        self.driver = SimDriver(self)
        self.segments = []; self._fill = 0.0
    def set_flow(self, r): self.target_flow = float(r)
    def start(self):
        self.running = True
        self.segments.append([time.time() - T0, None, self.target_flow,
                              float(self.current_vol)])
        rec("pump_start", self.name, self.target_flow)
    def stop(self):
        if self.running and self.segments and self.segments[-1][1] is None:
            self.segments[-1][1] = time.time() - T0
        self.running = False
    def refill_prepare(self, port, volume=None):
        self._fill = float(volume) if volume else self.capacity
        return True
    def refill_trigger(self): self.is_refilling = True
    def refill_complete(self):
        time.sleep(0.1)
        self.current_vol = min(self.capacity, self._fill)
        self.is_refilling = False
        rec("refill", self.name, self.current_vol)
    def prime_prepare(self): return False
    def dispensed(self, a, b):
        v = 0.0
        for t0, t1, rate, avail in self.segments:
            if rate <= 0: continue
            end = t1 if t1 is not None else (time.time() - T0)
            end = min(end, t0 + (avail / rate) * 60.0)
            lo, hi = max(a, t0), min(b, end)
            if hi > lo: v += rate * (hi - lo) / 60.0
        return v


class SimMFC:
    is_connected = True
    def __init__(self): self.segments = []; self._sp = 0.0
    def set_flow(self, sccm):
        t = time.time() - T0
        if self.segments and self.segments[-1][1] is None:
            self.segments[-1][1] = t
        if sccm > 0:
            self.segments.append([t, None, float(sccm)])
            rec("gas_on", "mfc", sccm)
        else:
            if self._sp > 0:
                rec("gas_off", "mfc", 0)
        self._sp = float(sccm)
    def displaced(self, a, b):
        v = 0.0
        for t0, t1, rate in self.segments:
            end = t1 if t1 is not None else (time.time() - T0)
            lo, hi = max(a, t0), min(b, end)
            if hi > lo: v += rate * (hi - lo) / 60.0   # 테스트: sccm=치환등가 mL/min
        return v


class SimValve:
    def __init__(self, name): self.name = name; self.position = 1
    def set_position(self, pos): self.position = pos; rec("valve", self.name, pos)


class SimCollector:
    is_connected = True; total_tubes = 96
    def __init__(self): self._p = 0
    def home(self): self._p = 0; return True, "ok"
    def move_to_tube(self, n): self._p = n; rec("tube", "col", n); return True, "ok"
    def get_position(self): return self._p
    def get_well_id(self, n): return f"W{n}"


class SimHeater:
    def __init__(self): self.target_temp = 25.0
    def set_temperature(self, t): self.target_temp = float(t)
    def get_temperature(self): return self.target_temp
    def stop(self): pass


class Sig:
    def __init__(self):
        emit = lambda *a, **k: None
        for n in ("sig_log", "sig_status", "sig_phase_progress",
                  "sig_progress", "sig_finished", "sig_error"):
            setattr(self, n, type("S", (), {"emit": staticmethod(emit)})())


R, POST, COLL, MIX = 0.6, 0.2, 0.15, 0.10
FLOWS = {"A": 1.0, "B": 1.0}
F = 2.0
LINE_IN = {"A": 0.02, "B": 0.02}
LINE_VP = {"A": 0.04, "B": 0.02}
SEL_IV = {"A": 0.01, "B": 0.01}
SW_IV = {"A": 0.01, "B": 0.01}
LINE_PM = {"A": 0.03, "B": 0.03}
V_SLUG, V_SPACER = 0.2, 0.1
V_WSOL, V_WGAS = 0.2, 0.15


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = R
    ACTIVE_PUMPS = ["A", "B"]
    tjunction_line_vols = {}
    line_vol_inlet = dict(LINE_IN)
    line_vol_valve_pump = dict(LINE_VP)
    selector_internal_vol = dict(SEL_IV)
    valve_internal_vol = dict(SW_IV)
    line_vol_pump_merge = dict(LINE_PM)
    mixing_line_dead_vol = MIX
    heater_reach_timeout_sec = 30.0
    config_data = {"system_params": {
        "post_reactor_vol_ml": POST, "collection_line_vol_ml": COLL,
        "temp_tolerance_c": 0.5, "heater_reach_timeout_sec": 30.0,
        "max_total_flow_ml_min": 100.0, "max_step_volume_ml": 500.0,
        "wash_mode": "off", "prefill_mode": "every_step",
        "syringe_refill_rate": 20.0,
        "hte_mode": True, "hte_spacer_vol_ml": V_SPACER,
        "hte_gas_equiv_flow_ml_min": F, "hte_gas_sccm": F,
        "hte_wash_solvent_vol_ml": V_WSOL, "hte_wash_gas_vol_ml": V_WGAS,
        "hte_wash_port": 1,
        # 감사 2026-07-13 이슈1 게이트: 이 구성은 COLL(0.15)>spacer(0.1) 부분이월
        # (슬러그당 0.05mL) — 명시 승인해야 실행됨. 게이트 자체는 audit_fixes 서 검증.
        "hte_allow_spacer_carryover": True,
    }}


fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# ── E: MFC 미구성 사전 차단 ──
cfg = Cfg()
pumps = {n: SimPump(n) for n in FLOWS}
eng0 = StrictSequenceEngine(cfg, pumps, {"Outlet": SimValve("Outlet")}, SimHeater(),
                            SafetyManager(cfg, pumps, SimHeater()), Sig(),
                            collector=SimCollector(), mfc=None)
try:
    eng0.run_sequence([{"temp": 25.0, "vol_ml": V_SLUG, "residence_time": 18,
                        "inlet_ports": {"A": 2, "B": 2}, "flows": dict(FLOWS),
                        "collect_volume_per_tube": V_SLUG, "meta": {}}], None)
    check("E MFC 미구성 → SafetyError", False)
except SafetyError:
    check("E MFC 미구성 → SafetyError", True)

# ── E2: 빈 시퀀스 사전 차단 (C7 — steps[0] IndexError 방지) ──
_pumps_e = {n: SimPump(n) for n in FLOWS}
eng_e = StrictSequenceEngine(Cfg(), _pumps_e, {"Outlet": SimValve("Outlet")},
                             SimHeater(), SafetyManager(Cfg(), _pumps_e, SimHeater()),
                             Sig(), collector=SimCollector(), mfc=SimMFC())
try:
    eng_e.run_sequence([], None)
    check("E2 빈 시퀀스 → SafetyError", False)
except SafetyError:
    check("E2 빈 시퀀스 → SafetyError", True)

# ── 본 트레인: 슬러그 3개 (포트 2/3/2 — 재사용 프라이밍 경로 포함) ──
EV.clear()
pumps = {n: SimPump(n) for n in FLOWS}
valves = {"Outlet": SimValve("Outlet")}
mfc = SimMFC()
col = SimCollector()
eng = StrictSequenceEngine(Cfg(), pumps, valves, SimHeater(),
                           SafetyManager(Cfg(), pumps, SimHeater()), Sig(),
                           collector=col, mfc=mfc)
eng.collector_start_tube = 1
mk = lambda port: {"temp": 25.0, "vol_ml": V_SLUG, "residence_time": 18,
                   "inlet_ports": {"A": port, "B": port}, "flows": dict(FLOWS),
                   "collect_volume_per_tube": V_SLUG, "meta": {}}
plan = [mk(2), mk(3), mk(2)]
t_run = time.time()
eng.run_sequence(plan, None)
print(f"run: {time.time()-t_run:.0f}s")

# 이슈1 게이트(승인 이월 모드): 이월량 = min(V_SLUG, COLL−v_push) = 0.05 가 meta 에 기록
check("이슈1 meta 이월량 기록(0.05)",
      all(abs(st["meta"].get("hte_spacer_carryover_ml", 0) - 0.05) < 1e-9 for st in plan))


def V(a, b):
    return sum(p.dispensed(a, b) for p in pumps.values()) + mfc.displaced(a, b)


# 기대 모델 (엔진 계획 로직 미러)
pf = 1.0
primed = {}
purges = []
for st in plan:
    pv = 0.0
    for p, f in FLOWS.items():
        l1, l2 = LINE_IN[p], LINE_VP[p] + SEL_IV[p]
        pr = primed.setdefault(p, set())
        port = st["inlet_ports"][p]
        src = (l2 + (0.0 if port in pr else l1)) * pf
        pv = max(pv, src / f * F)
        pr.add(port)
    purges.append(pv)
line_src0 = {p: (LINE_VP[p] + SEL_IV[p] + LINE_IN[p]) * pf for p in FLOWS}
line_inj0 = {p: LINE_PM[p] + SW_IV[p] for p in FLOWS}
# 감사 2026-07-13 이슈2: v_head 는 주입경로+믹싱+리액터+포스트 순수 수송분만 —
# 소스 퍼지는 마크의 v_purge(아래 루프의 purges[i])가 전담(이중계상 제거).
# "lifo" 호출 = pre_sec 에서 inj_path_sec 만 추출(엔진과 동일 기법).
_, inj_sec, _, _ = StrictSequenceEngine._compute_plug_timing(
    FLOWS, ["A", "B"], line_src0, line_inj0, {}, "lifo")
V_HEAD = R + MIX + POST + F * inj_sec / 60.0

collects = [(t, d) for t, k, n, d in EV if k == "valve" and d == 2]
wastes = [(t, d) for t, k, n, d in EV if k == "valve" and d == 1]
tubes = [(t, d) for t, k, n, d in EV if k == "tube"]
gas_on = [t for t, k, n, d in EV if k == "gas_on"]
starts = sorted(t for t, k, n, d in EV if k == "pump_start" and (d or 0) > 0)
inj0 = starts[0]

check("A 슬러그 3회 COLLECT", len(collects) == 3, f"({len(collects)})")
# C1: waste 마크 = 꼬리@밸브 + 스페이서가 수집라인 꼬리를 웰까지 밀어낸 시점(v_push)
V_PUSH = min(COLL, V_SPACER)
v_cum = 0.0
TOL = 0.09
for i in range(3):
    v_cum += purges[i]
    tc = collects[i][0]
    vc = V(inj0, tc)
    check(f"A slug{i+1} COLLECT V≈{V_HEAD + v_cum:.3f}",
          abs(vc - (V_HEAD + v_cum)) < TOL, f"(V={vc:.3f})")
    v_cum += V_SLUG
    tw = next(t for t, d in wastes if t > tc)
    vw = V(inj0, tw)
    check(f"A slug{i+1} WASTE V≈{V_HEAD + v_cum + V_PUSH:.3f}",
          abs(vw - (V_HEAD + v_cum + V_PUSH)) < TOL, f"(V={vw:.3f})")
    v_cum += V_SPACER
seq_tubes = [d for t, d in tubes if t > inj0 - 1]
dedup = [x for j, x in enumerate(seq_tubes) if j == 0 or x != seq_tubes[j - 1]]
check("B 웰 순차 1,2,3", dedup[-3:] == [1, 2, 3], str(seq_tubes))
# C3: 웰 선이동 — 웰2 이동이 slug2 COLLECT 이전(수집 없는 스페이서 구간)에 발생
t_tube2 = next((t for t, d in tubes if d == 2), None)
check("C3 웰2 선이동 < collect2",
      t_tube2 is not None and t_tube2 < collects[1][0],
      f"(tube2={f'{t_tube2:.1f}' if t_tube2 else 'X'} c2={collects[1][0]:.1f})")
# B: COLLECT 창 사이(스페이서)엔 WASTE 상태 (마지막 valve 이벤트가 1)
ok_spacer = True
for i in range(2):
    tw = next(t for t, d in wastes if t > collects[i][0])
    tc_next = collects[i + 1][0]
    mid_events = [d for t, k, n, d in EV if k == "valve" and tw < t < tc_next]
    if any(d == 2 for d in mid_events):
        ok_spacer = False
check("B 스페이서 구간 WASTE 유지", ok_spacer)

# C: 세척 순서 — '구동 시퀀스' 기준 (밸브 이벤트는 이송 지연으로 나중에 발화).
# 도징 에피소드 = pump_start 클러스터(1s): [slug1, slug2, slug3, washSolvent]
episodes = []
for t in starts:
    if not episodes or t - episodes[-1] > 1.0:
        episodes.append(t)
check("C 도징 에피소드 4회(슬러그3+세척용매1)", len(episodes) == 4, str(len(episodes)))
check("C 가스 세그먼트 ≥5(스페이서3+세척2)", len(gas_on) >= 5, str(len(gas_on)))
# 순서: 마지막 스페이서(g3) < 세척N2-A(g4) < 용매 에피소드 < 세척N2-B(g5)
ok_order = (len(gas_on) >= 5 and len(episodes) == 4
            and gas_on[2] < gas_on[3] < episodes[3] < gas_on[4])
check("C 세척: 질소 → 용매 → 질소", ok_order,
      f"(g3={gas_on[2]:.0f} g4={gas_on[3]:.0f} sol={episodes[3]:.0f} g5={gas_on[4]:.0f})"
      if len(gas_on) >= 5 and len(episodes) == 4 else "")
v_sol = sum(p.dispensed(episodes[3] - 0.2, gas_on[4] if len(gas_on) > 4 else 1e9)
            for p in pumps.values())
check("C 용매 플러그 부피", abs(v_sol - V_WSOL) < 0.06, f"({v_sol:.3f})")

# D: 종료 상태
check("D MFC off", mfc._sp == 0.0)
check("D Outlet=WASTE", valves["Outlet"].position == 1)
check("D 펌프 정지", not any(p.running for p in pumps.values()))

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
