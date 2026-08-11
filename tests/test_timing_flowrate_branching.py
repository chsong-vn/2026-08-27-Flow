# -*- coding: utf-8 -*-
"""타이밍 로직 검토 — 펌프별 상이 유속 · 분기(캐스케이드 누적유량) · 밀어줌(purge/deficit)
· 다음실험 이월(primed) 이 물리적으로 올바르게 반영되는지 수치 검증(반례 포함).

@codesyncer: _compute_plug_timing(순수함수) + 엔진의 line_src/line_inj/primed 구성
  로직(strict_engine 1019-1050, 1135-1136)을 그대로 복제해 대조.
"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import StrictSequenceEngine as E
fails=[]
def chk(n,c,d=""):
    print(f"  {'PASS' if c else 'FAIL'} {n}  {d}");
    if not c: fails.append(n)
def PT(flows, li, ls=None, tj=None, order="fifo"):
    ordered=list(flows); ls=ls or {p:0.0 for p in flows}
    return E._compute_plug_timing(flows, ordered, ls, li, tj or {}, order)

print("── T1: 동일 데드볼륨, 다른 flowrate → 통과시간 V/f 로 달라지고 max 채택 ──")
# A(1mL/min) vs B(2mL/min), 둘 다 주입경로 0.1mL. tA=6s, tB=3s → pre=max=6 (lifo로 purge 배제)
_,pre,_,_ = PT({"A":1.0,"B":2.0}, {"A":0.1,"B":0.1}, order="lifo")
chk("느린채널(A) 지배 pre=6s", abs(pre-6.0)<1e-6, f"(pre={pre:.2f})")
# B를 4mL/min으로 더 빠르게 → tB=1.5s, 여전히 A=6 지배
_,pre2,_,_ = PT({"A":1.0,"B":4.0}, {"A":0.1,"B":0.1}, order="lifo")
chk("B 더 빨라도 A가 지배", abs(pre2-6.0)<1e-6, f"(pre={pre2:.2f})")
# A도 2mL/min → tA=3, tB=3 → pre=3 (flowrate 반영)
_,pre3,_,_ = PT({"A":2.0,"B":2.0}, {"A":0.1,"B":0.1}, order="lifo")
chk("둘다 빠름 pre=3s (flowrate 반영)", abs(pre3-3.0)<1e-6, f"(pre={pre3:.2f})")

print("── T2: 분기(캐스케이드)는 '누적유속' F_j, 자기유속 아님 ──")
# 3채널 동일 1mL/min, tj[1]=0.3mL (T1→T2, 조합=A+B=2). 주입경로 0.
# 캐스케이드 시간 = 0.3/2*60=9s. 자기유속(1)로 계산하면 18s(오답)
_,pre,_,_ = PT({"A":1.0,"B":1.0,"C":1.0}, {p:0.0 for p in "ABC"}, tj={1:0.3}, order="lifo")
chk("누적유속(F=2) → 9s (자기유속 18s 아님)", abs(pre-9.0)<1e-6, f"(pre={pre:.2f})")
# B를 2배로 → F1=A+B=3 → 0.3/3*60=6s (분기 유량 변화 반영)
_,pre,_,_ = PT({"A":1.0,"B":2.0,"C":1.0}, {p:0.0 for p in "ABC"}, tj={1:0.3}, order="lifo")
chk("B↑ → F=3 → 6s (분기유량 반영)", abs(pre-6.0)<1e-6, f"(pre={pre:.2f})")

print("── T3: max ≠ sum (병렬 합류) ──")
# A주입 0.2mL@1 =12s, B주입 0.1@1=6s. max=12 (sum=18 아님)
_,pre,_,_ = PT({"A":1.0,"B":1.0}, {"A":0.2,"B":0.1}, order="lifo")
chk("pre=max(12,6)=12 (sum 18 아님)", abs(pre-12.0)<1e-6, f"(pre={pre:.2f})")

print("── T4: 캐스케이드 진입점 j_enter (뒤 펌프는 늦게 합류→짧은 경로) ──")
# 4채널, tj{1:0.3(F=2), 2:0.3(F=3)}. C(idx2)는 T2부터 → S_2만. D(idx3)는 T3부터 → 0구간.
# A/B: S_1(0.3/2*60=9)+S_2(0.3/3*60=6)=15s. C: S_2만=6s. D: 0s. 주입경로 0.
_,pre,_,_ = PT({"A":1.,"B":1.,"C":1.,"D":1.}, {p:0. for p in "ABCD"}, tj={1:0.3,2:0.3}, order="lifo")
chk("전경로 A/B 지배 = 15s", abs(pre-15.0)<1e-6, f"(pre={pre:.2f})")

print("── T5: FIFO purge = 자기유속, purge_sec=max, deficit 비대칭 보정 ──")
# A purge경로 0.2@1=12s, B 0.1@2=3s. purge_sec=max=12. deficit=Σf*(12-ti)/60
# A: 1*(12-12)/60=0, B: 2*(12-3)/60=0.3 → deficit=0.3mL
ps,pre,dv,so = PT({"A":1.0,"B":2.0}, {"A":0,"B":0}, ls={"A":0.2,"B":0.1}, order="fifo")
chk("purge_sec=max=12s", abs(ps-12.0)<1e-6, f"({ps:.2f})")
chk("deficit=0.3mL (비대칭)", abs(dv-0.3)<1e-6, f"({dv:.3f})")
chk("stagger B=9s (빠른채널 지연출발)", abs(so["B"]-9.0)<1e-6, f"({so['B']:.2f})")
chk("stagger A=0", abs(so["A"])<1e-6)

print("── T6: LIFO = purge 지연 없음, stagger/deficit 0 ──")
ps,pre,dv,so = PT({"A":1.0,"B":2.0}, {"A":0,"B":0}, ls={"A":0.2,"B":0.1}, order="lifo")
chk("LIFO deficit=0", abs(dv)<1e-6)
chk("LIFO stagger 0", all(abs(v)<1e-6 for v in so.values()))



# ══════ PART 2: 펌프별 상이 유속 + 분기 + 밀어줌 + 이월 E2E ══════

# ── 3펌프, 유속 전부 다름 ──
FLOWS = {"P1": 1.0, "P2": 2.0, "P3": 0.5}    # mL/min 각각 다름
ORD = ["P1","P2","P3"]
# 구간별 데드볼륨 (펌프마다 다르게)
L_IN  = {"P1":0.10, "P2":0.20, "P3":0.05}    # 병→12way (포트전용관)
L_VP  = {"P1":0.04, "P2":0.06, "P3":0.02}    # 12way→3way (공유)
L_SEL = {"P1":0.01, "P2":0.02, "P3":0.01}    # 12way 내부 (공유)
L_SW  = {"P1":0.03, "P2":0.03, "P3":0.03}    # 3way 내부
L_PM  = {"P1":0.05, "P2":0.05, "P3":0.05}    # 3way→합류
TJ    = {1: 0.30}                            # T1→T2 (P1+P2 조합)
PF = 1.0

def build_lines(ports, primed):
    """엔진 1019-1034 라인 그대로 복제 (도징 전, primed 읽기)."""
    ls, li = {}, {}
    for p in FLOWS:
        l1 = L_IN[p]; l2 = L_VP[p] + L_SEL[p]
        pr = primed.setdefault(p, set())
        ls[p] = (l2 + (0.0 if ports[p] in pr else l1)) * PF
        li[p] = L_PM[p] + L_SW[p]
    return ls, li

def mark_dosed(ports, primed):
    """엔진 1135-1136: 프리필 후 해당 포트 inlet 라인 = 시약 충전됨."""
    for p in FLOWS:
        primed.setdefault(p, set()).add(ports[p])

print("── 시나리오 A: STEP1 (첫 사용, 유속 전부 다름) ──")
primed = {}
ports1 = {"P1":2, "P2":3, "P3":2}
ls1, li1 = build_lines(ports1, primed)
ps1, pre1, dv1, so1 = E._compute_plug_timing(FLOWS, ORD, ls1, li1, TJ, "fifo")
# (도징 수행됨) → primed 표시
# 검증1: 주입경로 시간이 펌프별 유속으로 갈림 — li 동일해도 f 다르면 t 다름
#   P1: (0.05+0.03)/1.0*60=4.8s + 캐스케이드. P2: 0.08/2.0*60=2.4s. P3: 0.08/0.5*60=9.6s
#   + 캐스케이드 S_1(F=P1+P2=3): P1,P2 통과 0.30/3*60=6s. P3(idx2)는 j_enter=2, range(2,2)=∅.
#   inj: P1=4.8+6=10.8, P2=2.4+6=8.4, P3=9.6+0=9.6 → max=10.8
chk("주입경로 유속별 상이+max=10.8s", abs(pre1-(ps1+10.8))<1e-6, f"(pre={pre1:.2f} ps={ps1:.2f} inj={pre1-ps1:.2f})")
# 검증2: purge = 자기유속별 (l1+l2)/f, max. P1:(0.05+0.10)/1=9s... l2=0.05,l1=0.10 → 0.15/1*60=9
#   P2: (0.08+0.20)/2*60=8.4s  P3:(0.03+0.05)/0.5*60=9.6s → max=9.6
chk("purge_sec=max(9,8.4,9.6)=9.6s", abs(ps1-9.6)<1e-6, f"({ps1:.2f})")
mark_dosed(ports1, primed)

print("── 시나리오 B: STEP2 동일 포트 (primed 이월 → 인렛관 퍼지 스킵) ──")
ports2 = {"P1":2, "P2":3, "P3":2}   # STEP1과 동일
ls2, li2 = build_lines(ports2, primed)   # primed 이월됨
ps2, pre2, dv2, so2 = E._compute_plug_timing(FLOWS, ORD, ls2, li2, TJ, "fifo")
mark_dosed(ports2, primed)
# 이제 l1 스킵 → purge = l2/f 만. P1:0.05/1*60=3  P2:0.08/2*60=2.4  P3:0.03/0.5*60=3.6 → max=3.6
chk("STEP2 purge 단축=3.6s (인렛관 이월)", abs(ps2-3.6)<1e-6, f"({ps2:.2f})")
chk("STEP2 purge < STEP1 (다음실험 영향)", ps2 < ps1, f"({ps2:.2f} < {ps1:.2f})")

print("── 시나리오 C: STEP3 다른 포트 (미이월 → 인렛관 재퍼지) ──")
ports3 = {"P1":5, "P2":3, "P3":7}   # P1,P3 새 포트 / P2 동일
ls3, li3 = build_lines(ports3, primed)
ps3, pre3, dv3, so3 = E._compute_plug_timing(FLOWS, ORD, ls3, li3, TJ, "fifo")
mark_dosed(ports3, primed)
# P1 포트5(새): l1+l2=0.15/1*60=9  P2 포트3(이월): l2=0.08/2*60=2.4  P3 포트7(새):0.08/0.5*60=9.6
chk("STEP3 새포트 재퍼지=9.6s", abs(ps3-9.6)<1e-6, f"({ps3:.2f})")
chk("P2만 이월(재퍼지 아님) → 여전히 P3 지배", abs(ps3-9.6)<1e-6)

print("── 시나리오 D: 밀어줌(deficit) — 유속·퍼지 비대칭 잔여부피 보정 ──")
# STEP1: purge_sec=9.6. deficit=Σ f*(9.6 - purge_i)/60
#   P1: 1*(9.6-9)/60=0.010  P2: 2*(9.6-8.4)/60=0.040  P3: 0.5*(9.6-9.6)/60=0
chk("deficit=0.050mL (비대칭 밀어줌)", abs(dv1-0.050)<1e-6, f"({dv1:.4f})")
chk("stagger P3=0(최장), P1/P2 지연출발", abs(so1["P3"])<1e-6 and so1["P1"]>0 and so1["P2"]>0,
    f"(P1={so1['P1']:.2f} P2={so1['P2']:.2f} P3={so1['P3']:.2f})")

print("── 시나리오 E: 분기 유량이 캐스케이드 시간 지배 (P2 유속↑ → S_1 빨라짐) ──")
F_fast = {"P1":1.0, "P2":4.0, "P3":0.5}   # P2 4배
ls_f, li_f = build_lines({"P1":2,"P2":3,"P3":2}, {})
_,pre_f,_,_ = E._compute_plug_timing(F_fast, ORD, ls_f, li_f, TJ, "fifo")
# S_1 조합유량 F=P1+P2=5 → 0.30/5*60=3.6s (기존 F=3일때 6s보다 짧음)
_,pre_base,_,_ = E._compute_plug_timing(FLOWS, ORD, ls_f, li_f, TJ, "fifo")
chk("P2 유속↑ → 캐스케이드 단축 (분기유량 반영)", pre_f < pre_base, f"({pre_f:.2f} < {pre_base:.2f})")



# ══════ PART 3: 여러 실험(시퀀스) 간 primed 이월 정책 ══════
from unittest.mock import MagicMock
from unittest.mock import MagicMock
from engine.strict_engine import StrictSequenceEngine


def mk_engine(persist=False):
    cfg = MagicMock()
    cfg.config_data = {"system_params": {"persist_primed_lines": persist,
                                          "syringe_refill_rate": 20.0}}
    cfg.ACTIVE_PUMPS = ["A"]
    e = StrictSequenceEngine.__new__(StrictSequenceEngine)
    e.cfg = cfg
    e.pumps = {}          # 펌프 없음 → current_vol 리셋 루프 no-op
    e.abort_flag = False
    e._cleanup_done = False
    e.signals = MagicMock()
    # 실제 시퀀스 로직을 타지 않고, 시작부 리셋 블록만 검증하기 위해 스텁
    return e, cfg

def run_start(e, sp):
    """_run_sequence_impl 시작부 리셋(903 이전) + 스텝루프 방어초기화(1011) 재현."""
    if not bool(sp.get("persist_primed_lines", False)):
        e._primed_ports = {}
    if not hasattr(e, "_primed_ports"):   # 엔진 1011/1695 방어 fallback
        e._primed_ports = {}

print("── 기본(리셋): 실험1이 남긴 primed 가 실험2 시작 시 사라짐 ──")
e, cfg = mk_engine(persist=False)
sp = cfg.config_data["system_params"]
run_start(e, sp)                       # 실험1 시작
e._primed_ports.setdefault("A", set()).add(2)   # 실험1 진행 중 port2 프라임
chk("실험1 중 primed={A:{2}}", e._primed_ports == {"A": {2}})
run_start(e, sp)                       # 실험2 시작
chk("실험2 시작 시 리셋됨(기본)", e._primed_ports == {}, str(e._primed_ports))

print("── persist=True: 실험 간 이월 유지 (연속 캠페인) ──")
e2, cfg2 = mk_engine(persist=True)
sp2 = cfg2.config_data["system_params"]
run_start(e2, sp2)
e2._primed_ports.setdefault("A", set()).add(2)
run_start(e2, sp2)                     # 실험2 시작
chk("persist=True 이월 유지", e2._primed_ports == {"A": {2}}, str(e2._primed_ports))

print("── 안전성: 실험2가 실험1과 같은 port 써도 기본은 재퍼지(오염 방지) ──")
# 기본 리셋이면 실험2 첫 스텝 port2 는 primed 없음 → 인렛관 전량 퍼지 (시약병 교체 대비)
e3, cfg3 = mk_engine(persist=False)
sp3 = cfg3.config_data["system_params"]
run_start(e3, sp3); e3._primed_ports.setdefault("A", set()).add(2)  # 실험1
run_start(e3, sp3)  # 실험2
in_primed = 2 in e3._primed_ports.get("A", set())
chk("실험2 첫 port2 재퍼지 대상(primed 아님)", not in_primed)


print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
