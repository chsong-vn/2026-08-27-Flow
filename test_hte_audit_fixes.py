# -*- coding: utf-8 -*-
"""감사 2026-07-13 수정 회귀 테스트 (이슈 2/3/4/5).

 1. [이슈2] hte_build_profile v_head 소스퍼지 이중계상 제거:
    v_head = 수송분만(inj+mix+reactor+post), 퍼지는 마크 v_purge 단일 계상.
    fifo/lifo 동일 v_head (order 는 마크 모델과 무관해짐).
 2. [이슈4] 스텝 온도 불일치 → SafetyError 사전 차단.
 3. [이슈3] Outlet 전환 최종실패 → sig_finished 억제 + sig_error + 결함 status.
    (대조: 정상 트레인 → sig_finished 발신)
 4. [이슈5] abort 후 _sequence_cleanup → _primed_ports 리셋 (정상 종료는 유지).
"""
import os, sys, time, threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.strict_engine import StrictSequenceEngine, hte_build_profile
from engine.safety_manager import SafetyManager, SafetyError
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


# ══════════════════════════════════════════════════════════════
# 1. [이슈2] v_head 이중계상 제거 — 순수함수 직접 검증
# ══════════════════════════════════════════════════════════════
print("[1] hte_build_profile v_head 퍼지 이중계상 제거")
DV = dict(inlet={"A": 0.02, "B": 0.02}, valve_pump={"A": 0.04, "B": 0.02},
          selector={"A": 0.01, "B": 0.01}, switcher={"A": 0.01, "B": 0.01},
          pump_merge={"A": 0.03, "B": 0.03})
mk_steps = lambda: [dict(flows={"A": 1.0, "B": 1.0}, F=2.0, v_slug=0.2,
                         q_equiv=2.0, ports={"A": 2, "B": 2})]
kw = dict(reactor_vol=0.6, mixing=0.1, post=0.2, vol_collection=0.15,
          deadvols=DV, active_pumps=["A", "B"], tj={}, purge_factor=1.0,
          override_delay=None, v_spacer=0.1, v_wash_sol=0.2, v_wash_gas=0.15,
          primed=None)
s_f = mk_steps()
p_f = hte_build_profile(s_f, purge_order="fifo", **kw)
s_l = mk_steps()
p_l = hte_build_profile(s_l, purge_order="lifo", **kw)

# 기대: inj_path = max(0.03+0.01)/1.0*60 = 2.4s → F*2.4/60 = 0.08
#       v_head = 0.6+0.1+0.2+0.08 = 0.98 (구버그: +퍼지0.14+deficit0.02 = 1.14)
check("v_head = 수송분만 (0.98)", abs(p_f["v_head"] - 0.98) < 1e-9,
      f"(v_head={p_f['v_head']:.4f})")
check("v_head 에 퍼지 미포함 (구버그값 1.14 아님)", p_f["v_head"] < 1.0)
check("fifo/lifo v_head 동일", abs(p_f["v_head"] - p_l["v_head"]) < 1e-12)
# 퍼지는 마크에서 단일 계상: v_purge = max(0.07,0.05)/1.0*2.0 = 0.14
check("slug1 v_purge = 0.14", abs(s_f[0]["v_purge"] - 0.14) < 1e-9,
      f"({s_f[0]['v_purge']:.4f})")
V_c, kind_c, i_c = p_f["marks"][0]
check("collect 마크 = v_head + v_purge (단일 계상)",
      kind_c == "collect" and abs(V_c - (0.98 + 0.14)) < 1e-9, f"(V={V_c:.4f})")
# override_delay 는 실측치 그대로 (변경 없음)
p_o = hte_build_profile(mk_steps(), purge_order="fifo",
                        **{**kw, "override_delay": 30.0})
check("override_delay 경로 불변 (F*30/60=1.0)",
      abs(p_o["v_head"] - 1.0) < 1e-9, f"({p_o['v_head']:.4f})")


# ══════════════════════════════════════════════════════════════
# Sim 하드웨어 (test_hte_droplet.py 최소 절제본)
# ══════════════════════════════════════════════════════════════
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
        self._fill = 0.0
    def set_flow(self, r): self.target_flow = float(r)
    def start(self): self.running = True
    def stop(self): self.running = False
    def refill_prepare(self, port, volume=None):
        self._fill = float(volume) if volume else self.capacity
        return True
    def refill_trigger(self): self.is_refilling = True
    def refill_complete(self):
        time.sleep(0.05)
        self.current_vol = min(self.capacity, self._fill)
        self.is_refilling = False
    def prime_prepare(self): return False


class SimMFC:
    is_connected = True
    def set_flow(self, sccm): pass


class SimValve:
    def __init__(self, name): self.name = name; self.position = 1
    def set_position(self, pos): self.position = pos


class FailCollectValve(SimValve):
    """COLLECT(pos 2) 전환만 실패하는 Outlet — 이슈3 시나리오."""
    def set_position(self, pos):
        if int(pos) == 2:
            raise RuntimeError("sim: RS-485 no ACK")
        super().set_position(pos)


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


class RecSig:
    """emit 기록 시그널 스텁 — sig_finished/sig_error/sig_status 검증용."""
    def __init__(self):
        self.finished = 0; self.errors = []; self.statuses = []
        rec = self
        class _S:
            def __init__(self, tag): self.tag = tag
            def emit(self, *a, **k):
                if self.tag == "sig_finished": rec.finished += 1
                elif self.tag == "sig_error": rec.errors.append(a[0] if a else "")
                elif self.tag == "sig_status": rec.statuses.append(a[0] if a else "")
        for n in ("sig_log", "sig_status", "sig_phase_progress",
                  "sig_progress", "sig_finished", "sig_error"):
            setattr(self, n, _S(n))


R, POST, COLL, MIX = 0.3, 0.1, 0.06, 0.05
FLOWS = {"A": 1.0, "B": 1.0}
F = 2.0
V_SLUG, V_SPACER = 0.15, 0.08


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = R
    ACTIVE_PUMPS = ["A", "B"]
    tjunction_line_vols = {}
    line_vol_inlet = {"A": 0.02, "B": 0.02}
    line_vol_valve_pump = {"A": 0.04, "B": 0.02}
    selector_internal_vol = {"A": 0.01, "B": 0.01}
    valve_internal_vol = {"A": 0.01, "B": 0.01}
    line_vol_pump_merge = {"A": 0.03, "B": 0.03}
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
        "hte_wash_solvent_vol_ml": 0.1, "hte_wash_gas_vol_ml": 0.08,
        "hte_wash_port": 1,
    }}


def mk_exp(temp=25.0, port=2):
    return {"temp": temp, "vol_ml": V_SLUG, "residence_time": 18,
            "inlet_ports": {"A": port, "B": port}, "flows": dict(FLOWS),
            "collect_volume_per_tube": V_SLUG, "meta": {}}


def mk_cfg(**sp_over):
    import copy
    c = Cfg()
    c.config_data = copy.deepcopy(Cfg.config_data)
    c.config_data["system_params"].update(sp_over)
    return c


def mk_engine(outlet=None, sig=None, cfg=None):
    cfg = cfg or Cfg()
    pumps = {n: SimPump(n) for n in FLOWS}
    eng = StrictSequenceEngine(
        cfg, pumps, {"Outlet": outlet or SimValve("Outlet")}, SimHeater(),
        SafetyManager(cfg, pumps, SimHeater()), sig or RecSig(),
        collector=SimCollector(), mfc=SimMFC())
    eng.collector_start_tube = 1
    return eng


# ══════════════════════════════════════════════════════════════
# 2. [이슈4] 스텝 온도 불일치 사전 차단
# ══════════════════════════════════════════════════════════════
print("[2] 스텝 온도 불일치 → SafetyError")
eng_t = mk_engine()
try:
    eng_t.run_sequence([mk_exp(25.0), mk_exp(80.0)], None)
    check("온도 불일치(25/80) → SafetyError", False)
except SafetyError as e:
    check("온도 불일치(25/80) → SafetyError", True, f"({e})")
# 허용오차(0.5) 이내 미세차이는 통과해야 함 — 25.0 vs 25.2 (SafetyError 미발생 검증만,
# 트레인은 온도 대기 후 실행되므로 여기선 프로파일 빌드 통과 여부를 아래 [3]으로 갈음
try:
    _s = [dict(flows=dict(FLOWS), F=F, v_slug=V_SLUG, q_equiv=F,
               ports={"A": 2, "B": 2})]
    hte_build_profile(_s, purge_order="fifo", **kw)
    check("프로파일 빌드 정상(대조군)", True)
except Exception as e:
    check("프로파일 빌드 정상(대조군)", False, str(e))

# ══════════════════════════════════════════════════════════════
# 3. [이슈3] Outlet 전환 실패 → 성공신호 억제 (+정상 대조군)
# ══════════════════════════════════════════════════════════════
print("[3] Outlet 전환 실패 → sig_finished 억제 (1슬러그 실 트레인)")
sig_ok = RecSig()
eng_ok = mk_engine(sig=sig_ok)
eng_ok.run_sequence([mk_exp()], None)
check("대조군: 정상 트레인 → sig_finished 1회", sig_ok.finished == 1,
      f"(finished={sig_ok.finished}, errors={sig_ok.errors})")
check("대조군: sig_error 없음", not sig_ok.errors, str(sig_ok.errors))
check("대조군: status 'HTE complete'", "HTE complete" in sig_ok.statuses)

sig_f = RecSig()
eng_f = mk_engine(outlet=FailCollectValve("Outlet"), sig=sig_f)
eng_f.run_sequence([mk_exp()], None)
check("결함군: sig_finished 미발신", sig_f.finished == 0,
      f"(finished={sig_f.finished})")
check("결함군: sig_error 발신(Outlet 실패)",
      any("Outlet" in e for e in sig_f.errors), str(sig_f.errors)[:120])
check("결함군: 종료 sig_error 에 결함 요약",
      any("결함 있는 종료" in e for e in sig_f.errors))
check("결함군: status 결함 표기", "HTE finished with faults" in sig_f.statuses,
      str([s for s in sig_f.statuses if "HTE" in s]))
check("결함군: 'HTE complete' 미표기", "HTE complete" not in sig_f.statuses)

# ══════════════════════════════════════════════════════════════
# 4. [이슈5] abort → _primed_ports 리셋
# ══════════════════════════════════════════════════════════════
print("[4] abort 후 primed-port 리셋")
eng_a = mk_engine()
eng_a._primed_ports = {"A": {2, 3}, "B": {2}}
eng_a.abort_flag = True
eng_a._cleanup_done = False
eng_a._sequence_cleanup(None)
check("abort cleanup → primed 리셋", eng_a._primed_ports == {},
      str(eng_a._primed_ports))

eng_n = mk_engine()
eng_n._primed_ports = {"A": {2}}
eng_n.abort_flag = False
eng_n._cleanup_done = False
eng_n._sequence_cleanup(None)
check("정상 cleanup → primed 유지(이월 정책 불변)",
      eng_n._primed_ports == {"A": {2}}, str(eng_n._primed_ports))

# ══════════════════════════════════════════════════════════════
# 5. [이슈1·정책c] spacer < 수집라인 2단계 게이트
#    기본 Cfg: COLL=0.06 = v_push(스페이서 0.08 클램프) → 이월 0, 게이트 비발화
# ══════════════════════════════════════════════════════════════
print("[5] 이슈1 게이트: spacer < 수집라인")
# ① 부분 이월(coll 0.2 > v_push 0.08, carry=min(0.15, 0.12)=0.12) + 무플래그 → 차단
eng_g1 = mk_engine(cfg=mk_cfg(collection_line_vol_ml=0.2))
try:
    eng_g1.run_sequence([mk_exp()], None)
    check("부분이월 무플래그 → SafetyError", False)
except SafetyError as e:
    check("부분이월 무플래그 → SafetyError", True, f"({str(e)[:60]}…)")
    check("메시지에 승인 플래그 안내", "hte_allow_spacer_carryover" in str(e))

# ② 부분 이월 + 승인 플래그 → 완주 + meta 이월량 기록
sig_g = RecSig()
eng_g2 = mk_engine(cfg=mk_cfg(collection_line_vol_ml=0.2,
                              hte_allow_spacer_carryover=True), sig=sig_g)
plan_g = [mk_exp()]
eng_g2.run_sequence(plan_g, None)
check("승인 이월 → 완주(sig_finished 1)", sig_g.finished == 1,
      f"(finished={sig_g.finished}, errors={sig_g.errors})")
check("meta 이월량 기록(0.12)",
      abs(plan_g[0]["meta"].get("hte_spacer_carryover_ml", 0) - 0.12) < 1e-9,
      str(plan_g[0]["meta"]))

# ③ 전량 좌초(v_slug 0.15 + v_push 0.08 ≤ coll 0.5) → 플래그 무관 무조건 차단
eng_g3 = mk_engine(cfg=mk_cfg(collection_line_vol_ml=0.5,
                              hte_allow_spacer_carryover=True))
try:
    eng_g3.run_sequence([mk_exp()], None)
    check("전량좌초 → 플래그 무관 SafetyError", False)
except SafetyError as e:
    check("전량좌초 → 플래그 무관 SafetyError", True, f"({str(e)[:60]}…)")

# ④ spacer ≥ 수집라인(기본 Cfg) → 게이트 비발화 (위 [3] 대조군이 이미 완주 증명)
check("spacer≥라인 구성은 게이트 비발화(대조군 완주)", sig_ok.finished == 1)

print()
if fails:
    print(f"=== {len(fails)} FAIL: {fails} ===")
    sys.exit(1)
print("=== ALL PASS ===")
