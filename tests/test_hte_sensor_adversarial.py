# -*- coding: utf-8 -*-
"""HTE 하이브리드 트리거 — 센서 경우의수 '적대적' E2E 검증.

물리 브리지: 펌핑 부피를 실시간 적분해 Mock 위상센서를 '실제 경계 위치'로 구동.
드리프트 모델 = 가스 세그먼트 1개 통과마다 하류 경계가 +drift mL 지연(분산·용해·
컴플라이언스 과도의 등가) — 부피 마크(데드레코닝)는 이를 모름.

시나리오 (argv 로 선택, 기본 all):
 R1  정상          : 전 엣지 매칭·|누적보정|≈0·마크=참경계
 R2  지연 드리프트  : drift=+0.06 — 재앵커로 참경계 정합(missed=0)
 R2b 드리프트+센서OFF: 동일 드리프트에서 순수 타이밍의 collect3 오차 시연(>0.10)
 R3  잡기포+미검출  : slug2 중간 기포(창밖 spurious) + slug3-head 엣지 은닉(missed=1)
                     → 카운트 desync 없이 나머지 마크 정확
 R4  단선 mid-train : slug2 초입 센서 ERROR — sync 자체 종료, 트레인은 타이밍 완주
 R5  조기 드리프트  : drift=-0.04 — 음수 Δ 재앵커 정합
"""
import os, sys, time, threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump
from hardware.sensors.phase_sensor_array import MockPhaseSensor

T0 = time.time()


# ── Sim 하드웨어 (test_hte_droplet 하네스 축약 복제) ─────────────
class SimDriver:
    def __init__(self, pump): self.pump = pump
    def is_stopped(self): return not self.pump.running


class SimPump(ChemyxSmartPump):
    def __init__(self, name, capacity=8.0):
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
        self._sp = float(sccm)
    def displaced(self, a, b):
        v = 0.0
        for t0, t1, rate in self.segments:
            end = t1 if t1 is not None else (time.time() - T0)
            lo, hi = max(a, t0), min(b, end)
            if hi > lo: v += rate * (hi - lo) / 60.0
        return v


class SimValve:
    def __init__(self, name): self.name = name; self.position = 1; self.events = []
    def set_position(self, pos):
        self.position = pos; self.events.append((time.time() - T0, pos))


class SimCollector:
    is_connected = True; total_tubes = 96
    def __init__(self): self._p = 0
    def home(self): self._p = 0; return True, "ok"
    def move_to_tube(self, n): self._p = n; return True, "ok"
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


# ── 파라미터 (미러 모델) ─────────────────────────────────────
R, POST, COLL, MIX = 0.6, 0.2, 0.15, 0.10
FLOWS = {"A": 1.0, "B": 1.0}; F = 2.0
LINE_IN = {"A": 0.02, "B": 0.02}; LINE_VP = {"A": 0.04, "B": 0.02}
SEL_IV = {"A": 0.01, "B": 0.01}; SW_IV = {"A": 0.01, "B": 0.01}
LINE_PM = {"A": 0.03, "B": 0.03}
V_SLUG, V_SPACER = 0.4, 0.3
V_WSOL, V_WGAS = 0.2, 0.15
V_PUSH = min(COLL, V_SPACER)


def make_cfg(sensor_on, interwash=0.0):
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
            "hte_sensor_trigger": bool(sensor_on),
            "hte_interwash_vol_ml": float(interwash),
        }}
    return Cfg()


def mirror_model(plan):
    """엔진 계획 로직 미러: 퍼지·V_HEAD·명목 마크."""
    primed = {}; purges = []
    for st in plan:
        pv = 0.0
        for p, f in FLOWS.items():
            l1, l2 = LINE_IN[p], LINE_VP[p] + SEL_IV[p]
            pr = primed.setdefault(p, set()); port = st["inlet_ports"][p]
            src = (l2 + (0.0 if port in pr else l1)) * 1.0
            pv = max(pv, src / f * F); pr.add(port)
        purges.append(pv)
    line_src0 = {p: (LINE_VP[p] + SEL_IV[p] + LINE_IN[p]) for p in FLOWS}
    line_inj0 = {p: LINE_PM[p] + SW_IV[p] for p in FLOWS}
    # 감사 2026-07-13 이슈2: v_head = 순수 수송분만(퍼지는 purges[i]가 전담).
    _, inj_sec, _, _ = StrictSequenceEngine._compute_plug_timing(
        FLOWS, ["A", "B"], line_src0, line_inj0, {}, "lifo")
    v_head = R + MIX + POST + F * inj_sec / 60.0
    return v_head, purges


class Bridge(threading.Thread):
    """물리 브리지 — 펌핑 부피 적분으로 센서 위상 구동 (+드리프트/기포/은닉/단선)."""

    def __init__(self, pumps, mfc, ps, v_head, purges, n_slug,
                 drift_per_gas=0.0, skip_bounds=(), bubble=None, kill_at=None,
                 interwash=0.0):
        super().__init__(daemon=True)
        self.pumps, self.mfc, self.ps = pumps, mfc, ps
        segs = []   # (dv, phase, label) 도착 순서 (V_HEAD 이후)
        for i in range(n_slug):
            segs.append((purges[i] + V_SLUG, 1, f"slug{i+1}"))
            segs.append((V_SPACER, 3, f"sp{i+1}"))
            if interwash > 0 and (i + 1) < n_slug:
                segs.append((interwash, 1, f"iw{i+1}"))
                segs.append((V_SPACER, 3, f"sp{i+1}b"))
        segs.append((V_WGAS, 3, "washA"))
        segs.append((V_WSOL, 1, "washsol"))
        segs.append((99.0, 3, "end"))
        # 경계 목록: (V_nominal + delay, phase_after, label). delay = drift×앞 가스세그 수
        self.bounds = []
        cum, n_gas = v_head, 0
        for k in range(len(segs) - 1):
            dv, ph, lab = segs[k]
            cum += dv
            if ph == 3:
                n_gas += 1
            nxt = segs[k + 1]
            self.bounds.append((cum + drift_per_gas * n_gas, nxt[1],
                                f"{lab}->{nxt[2]}"))
        self.skip = set(skip_bounds)      # 경계 라벨: 센서가 못 보는 전이(위상만 변경)
        self.bubble = bubble              # (V_at, dur_s) 액체 중 GAS 펄스
        self.kill_at = kill_at            # V 도달 시 센서 ERROR(단선)
        self._stop_evt = threading.Event()
        self.true_bounds = {b[2]: b[0] for b in self.bounds}

    def V(self):
        now = time.time() - T0
        return (sum(p.dispensed(0, now) for p in self.pumps.values())
                + self.mfc.displaced(0, now))

    def stop(self):
        self._stop_evt.set()
        self.join(timeout=2)

    def run(self):
        k = 0
        bubbled = False
        killed = False
        while not self._stop_evt.is_set() and k < len(self.bounds):
            v = self.V()
            if self.kill_at is not None and not killed and v >= self.kill_at:
                killed = True
                try:
                    self.ps.sim_set_phase("collect", 0)   # 단선: ERROR push (실기 계약)
                except Exception:
                    pass
            if self.bubble and not bubbled and v >= self.bubble[0]:
                bubbled = True
                self.ps.sim_set_phase("collect", 3)
                time.sleep(self.bubble[1])
                self.ps.sim_set_phase("collect", 1)
            while k < len(self.bounds) and v >= self.bounds[k][0]:
                Vb, ph, lab = self.bounds[k]
                if killed:
                    pass                                  # 단선: 아무 것도 안 보임
                elif lab in self.skip:
                    with self.ps._lock:                   # 미검출: 위상만 조용히 변경
                        if self.ps._sim_phase["collect"] != 0:
                            self.ps._sim_phase["collect"] = ph
                else:
                    try:
                        self.ps.sim_set_phase("collect", ph)
                    except Exception:
                        pass
                k += 1
            time.sleep(0.02)


def run_train(name, sensor_on=True, drift=0.0, skip=(), bubble=None, kill_at=None,
              interwash=0.0):
    global T0
    T0 = time.time()
    cfg = make_cfg(sensor_on, interwash)
    pumps = {n: SimPump(n) for n in FLOWS}
    valves = {"Outlet": SimValve("Outlet")}
    mfc = SimMFC(); col = SimCollector()
    ps = MockPhaseSensor(sensors={"collect": 0}); ps.connect()
    eng = StrictSequenceEngine(cfg, pumps, valves, SimHeater(),
                               SafetyManager(cfg, pumps, SimHeater()), Sig(),
                               collector=col, mfc=mfc, phase_sensor=ps)
    eng.collector_start_tube = 1
    mk = lambda port: {"temp": 25.0, "vol_ml": V_SLUG, "residence_time": 18,
                       "inlet_ports": {"A": port, "B": port}, "flows": dict(FLOWS),
                       "collect_volume_per_tube": V_SLUG, "meta": {}}
    plan = [mk(2), mk(3), mk(2)]
    v_head, purges = mirror_model(plan)
    br = Bridge(pumps, mfc, ps, v_head, purges, 3,
                drift_per_gas=drift, skip_bounds=skip, bubble=bubble,
                kill_at=kill_at, interwash=interwash)
    br.start()
    t0 = time.time()
    eng.run_sequence(plan, None)
    br.stop()
    print(f"[{name}] run {time.time()-t0:.0f}s")
    # 밸브 이벤트 → 명목 pumped 부피 (기존 E2E 와 동일한 측정계)
    def Vat(t):
        return (sum(p.dispensed(0, t) for p in pumps.values())
                + mfc.displaced(0, t))
    collects = [Vat(t) for t, p in valves["Outlet"].events if p == 2]
    wastes = [Vat(t) for t, p in valves["Outlet"].events if p == 1]
    # 참 경계 (드리프트 반영)
    gas_per_cycle = 2 if interwash > 0 else 1   # 사이클당 가스세그 수(스페이서[+인터워시 뒤 스페이서])
    cum, true_c, true_w = v_head, [], []
    for i in range(3):
        d = drift * (gas_per_cycle * i)
        true_c.append(cum + purges[i] + d)
        cum += purges[i] + V_SLUG
        true_w.append(cum + d + V_PUSH)   # tail(지연 d) + v_push (스페이서가 밈)
        cum += V_SPACER
        if interwash > 0 and i < 2:
            cum += interwash + V_SPACER
    sync = eng._hte_sensor_sync
    return dict(collects=collects, wastes=wastes, true_c=true_c, true_w=true_w,
                sync=sync, mfc=mfc, valves=valves, pumps=pumps, ps=ps)


fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


def check_marks(tag, r, tol=0.09, waste_tol=None):
    ok_c = all(abs(v - t) < tol for v, t in zip(r["collects"], r["true_c"]))
    det_c = " ".join(f"{v - t:+.3f}" for v, t in zip(r["collects"], r["true_c"]))
    check(f"{tag} COLLECT=참경계", len(r["collects"]) == 3 and ok_c, f"[{det_c}]")
    wt = waste_tol or tol
    w_after = [next(w for w in r["wastes"] if w > c) for c in r["collects"]]
    ok_w = all(abs(v - t) < wt for v, t in zip(w_after, r["true_w"]))
    det_w = " ".join(f"{v - t:+.3f}" for v, t in zip(w_after, r["true_w"]))
    check(f"{tag} WASTE=참경계", ok_w, f"[{det_w}]")


def check_end(tag, r):
    check(f"{tag} 종료(MFC off·WASTE·펌프정지)",
          r["mfc"]._sp == 0.0 and r["valves"]["Outlet"].position == 1
          and not any(p.running for p in r["pumps"].values()))


SC = sys.argv[1] if len(sys.argv) > 1 else "all"


def want(s):
    return SC in ("all", s)


if want("R1"):
    r = run_train("R1", sensor_on=True, drift=0.0)
    s = r["sync"]
    check("R1 sync 생성", s is not None)
    if s:
        check("R1 전 엣지 매칭", s.matched == len(s.expected) and s.missed == 0
              and s.spurious == 0, f"(m={s.matched}/{len(s.expected)})")
        check("R1 |누적보정|<1.5s", abs(s.total_shift) < 1.5,
              f"({s.total_shift:+.2f}s)")
    check_marks("R1", r)
    check_end("R1", r)

if want("R2"):
    r = run_train("R2", sensor_on=True, drift=0.06)
    s = r["sync"]
    if s:
        check("R2 전엣지 매칭·미검출0", s.matched == len(s.expected) and s.missed == 0,
              f"(m={s.matched}/{len(s.expected)} miss={s.missed} spur={s.spurious})")
        check("R2 누적보정>+2s(지연 흡수)", s.total_shift > 2.0,
              f"({s.total_shift:+.2f}s)")
    check_marks("R2", r)
    check_end("R2", r)

if want("R2b"):
    r = run_train("R2b", sensor_on=False, drift=0.06)
    err3 = abs(r["collects"][2] - r["true_c"][2]) if len(r["collects"]) == 3 else 9
    check("R2b 타이밍-only collect3 오차>0.10 (드리프트 시연)", err3 > 0.10,
          f"({err3:.3f} mL)")
    check_end("R2b", r)

if want("R3"):
    v_head, purges = mirror_model([{"inlet_ports": {"A": 2, "B": 2}},
                                   {"inlet_ports": {"A": 3, "B": 3}},
                                   {"inlet_ports": {"A": 2, "B": 2}}])
    bubble_v = v_head + purges[0] + V_SLUG + V_SPACER + purges[1] + 0.05
    r = run_train("R3", sensor_on=True, drift=0.0,
                  skip=("sp2->slug3",), bubble=(bubble_v, 0.4))
    s = r["sync"]
    if s:
        check("R3 잡기포 무시(spurious≥2)", s.spurious >= 2, f"({s.spurious})")
        check("R3 은닉 엣지 미검출=1(폴백)", s.missed == 1, f"({s.missed})")
        check("R3 나머지 전부 매칭", s.matched == len(s.expected) - 1,
              f"({s.matched}/{len(s.expected)})")
    check_marks("R3", r)
    check_end("R3", r)

if want("R4"):
    v_head, purges = mirror_model([{"inlet_ports": {"A": 2, "B": 2}},
                                   {"inlet_ports": {"A": 3, "B": 3}},
                                   {"inlet_ports": {"A": 2, "B": 2}}])
    kill_v = v_head + purges[0] + V_SLUG + V_SPACER + 0.05   # slug2 초입
    r = run_train("R4", sensor_on=True, drift=0.0, kill_at=kill_v)
    s = r["sync"]
    if s:
        check("R4 단선 전 매칭≥1", s.matched >= 1, f"({s.matched})")
        check("R4 sync 자체 종료(트레인 계속)", not s.is_alive())
    check_marks("R4", r)   # drift=0 → 타이밍 폴백도 정확해야
    check_end("R4", r)

if want("R5"):
    r = run_train("R5", sensor_on=True, drift=-0.04)
    s = r["sync"]
    if s:
        check("R5 조기 엣지 전부 매칭", s.matched == len(s.expected) and s.missed == 0,
              f"(m={s.matched}/{len(s.expected)})")
        check("R5 누적보정<-1s(음수 재앵커)", s.total_shift < -1.0,
              f"({s.total_shift:+.2f}s)")
    check_marks("R5", r)
    check_end("R5", r)

if want("R6"):
    r = run_train("R6", sensor_on=True, drift=0.06, interwash=0.2)
    s6 = r["sync"]
    if s6:
        check("R6 인터워시 전엣지 매칭·미검출0",
              s6.matched == len(s6.expected) and s6.missed == 0,
              f"(m={s6.matched}/{len(s6.expected)} spur={s6.spurious})")
        check("R6 엣지 목록에 인터워시 포함",
              any("interwash" in lab for _t, _k, lab in s6.expected))
    check_marks("R6", r)      # collect 3개뿐(인터워시 오수집 없음)도 내부 검증
    check_end("R6", r)

if want("R6b"):
    r = run_train("R6b", sensor_on=False, drift=0.0, interwash=0.2)
    check_marks("R6b(타이밍-only·무드리프트)", r)
    check_end("R6b", r)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
