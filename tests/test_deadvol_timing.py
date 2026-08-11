# -*- coding: utf-8 -*-
"""데드볼륨 플러그 타이밍 검증 — StrictSequenceEngine._compute_plug_timing (실코드).

사용자 물리 요구 검증:
 1. 앞단(12-way·12way→3way = line_src)은 수송 타이밍(pre_sec 경로항)에 비적용
 2. 주입 타이밍 = 3-way 내부(valve_internal) + 3way→합류 배관(pump_merge) 합
 3. 다중 펌프(2/3/4라인): '단순 합산'이 아니라 —
    자기 브랜치는 자기 유속, 정션 간 공유 구간은 누적 유속, 채널별 도달시간의 max
 4. purge_order fifo(기본·기존 동작 회귀) / lifo(시약 선행) 분기
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import StrictSequenceEngine

compute = StrictSequenceEngine._compute_plug_timing
fails = []


def check(name, got, exp, tol=1e-6):
    ok = abs(got - exp) <= tol
    print(f"{'PASS' if ok else 'FAIL'} {name}: got {got:.4f}, expect {exp:.4f}")
    if not ok:
        fails.append(name)


def check_true(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


# ── Case 1: 2펌프 — 정션 구간 없음(n-2=0), max 로직 ──────────────
# f=(1,2) mL/min, inj=(0.30, 0.30) mL, src=0
flows = {"A": 1.0, "B": 2.0}
inj = {"A": 0.30, "B": 0.30}
p, pre, dv, st = compute(flows, ["A", "B"], {"A": 0, "B": 0}, inj, {}, "fifo")
# A: 0.30/1.0*60=18s / B: 0.30/2.0*60=9s → max=18 (naive 합 0.6/3*60=12 가 아님)
check("C1 pre_sec = max(채널 도달시간)", pre, 18.0)
check("C1 purge=0", p, 0.0)

# ── Case 2: 3펌프 캐스케이드 — 공유 구간은 '누적 유속' ────────────
# f=(1,2,1), inj 전채널 0.20, T1→T2 구간 V1=0.50
flows = {"A": 1.0, "B": 2.0, "C": 1.0}
inj = {k: 0.20 for k in flows}
tj = {1: 0.50}
p, pre, dv, st = compute(flows, ["A", "B", "C"], {k: 0 for k in flows}, inj, tj, "fifo")
# A: 0.2/1*60=12 + 0.5/(1+2)*60=10 → 22   (V1 은 f_A+f_B 로만 흐름)
# B: 0.2/2*60=6  + 10               → 16
# C: T2 진입 → 캐스케이드 통과 없음 → 12
check("C2 pre_sec (3펌프)", pre, 22.0)
# 나이브 '전부 합산' 반례: (0.6+0.5)/4*60 = 16.5 ≠ 22
check_true("C2 naive-sum 반례", abs(pre - 16.5) > 1.0, f"naive=16.5 vs correct={pre}")

# ── Case 3: 4펌프 — S1(F=f1+f2), S2(F=f1+f2+f3), P4 는 마지막 정션 ──
flows = {"A": 1.0, "B": 1.0, "C": 2.0, "D": 4.0}
inj = {k: 0.10 for k in flows}
tj = {1: 0.30, 2: 0.60}
p, pre, dv, st = compute(flows, ["A", "B", "C", "D"], {k: 0 for k in flows}, inj, tj, "fifo")
# A/B: 0.1/1*60=6 + 0.3/(1+1)*60=9 + 0.6/(1+1+2)*60=9 → 24
# C:   0.1/2*60=3 + 9(S2)                             → 12
# D:   0.1/4*60=1.5 (마지막 정션 진입, 통과구간 없음)   → 1.5
check("C3 pre_sec (4펌프)", pre, 24.0)

# ── Case 4: 앞단(line_src) 이 수송 경로에 안 들어감 + FIFO 퍼지 분리 ──
flows = {"A": 1.0, "B": 2.0}
src = {"A": 0.50, "B": 0.20}   # 12way·밸브→펌프 앞단 (타이밍 경로 비적용 대상)
inj = {"A": 0.30, "B": 0.30}
p, pre, dv, st = compute(flows, ["A", "B"], src, inj, {}, "fifo")
# purge = max(0.5/1, 0.2/2)*60 = 30s (앞단은 '퍼지 창'으로만)
check("C4 purge_sec", p, 30.0)
# pre(fifo) = purge 30 + 경로 max(18,9)=18 → 48  (src 가 경로항에 중복 합산 안 됨)
check("C4 pre_sec fifo = purge + inj경로", pre, 48.0)
# 스태거: A=0, B=30-6=24 → 시약 전선 동시화
check("C4 stagger A", st["A"], 0.0)
check("C4 stagger B", st["B"], 24.0)
# deficit = f_B×(30-6)/60 = 2×24/60 = 0.8 mL
check("C4 deficit_vol", dv, 0.8)

# ── Case 5: LIFO — 헤드는 퍼지 지연 없이 출발, 스태거/deficit 0 ──
p, pre, dv, st = compute(flows, ["A", "B"], src, inj, {}, "lifo")
check("C5 purge_sec 동일(창 연장용)", p, 30.0)
check("C5 pre_sec lifo = inj경로만", pre, 18.0)
check("C5 deficit=0", dv, 0.0)
check_true("C5 stagger 전부 0", all(v == 0.0 for v in st.values()), str(st))

# ── Case 6: 3-way 내부볼륨 합산 (call-site 계약: inj = pump_merge + valve_internal) ──
# 엔진 호출부가 line_inj = pump_merge + valve_internal 로 빌드 → 여기선 등가성 확인
inj_merge_only = {"A": 0.30}
inj_with_valve = {"A": 0.30 + 0.05}
_, pre1, _, _ = compute({"A": 1.0}, ["A"], {"A": 0}, inj_merge_only, {}, "fifo")
_, pre2, _, _ = compute({"A": 1.0}, ["A"], {"A": 0}, inj_with_valve, {}, "fifo")
check("C6 valve_internal 가산 = +3s (0.05mL@1mL/min)", pre2 - pre1, 3.0)

# ── Case 7: 유속 0 채널은 무시 (죽은 채널이 타이밍 왜곡 금지) ──
flows = {"A": 1.0, "B": 0.0, "C": 1.0}
inj = {k: 0.20 for k in flows}
p, pre, dv, st = compute(flows, ["A", "B", "C"], {k: 0 for k in flows}, inj, {1: 0.5}, "fifo")
# A: 12 + 0.5/(1+0)*60=30 → 42 / C: 12 → max 42 (B 무시, 누적유속에 0 기여)
check("C7 f=0 채널 무시", pre, 42.0)
check_true("C7 stagger 에 B 없음", "B" not in st)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
