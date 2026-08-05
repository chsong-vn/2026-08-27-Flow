# -*- coding: utf-8 -*-
"""Deep Wash 엔진 오프라인 검증 — 하드웨어/Qt 없이 순서·배칭·중단·복원 확인.

검증 항목:
  1. 단일 그룹(6mL) 동작 순서: P0(스킵)→P1 공통×2→P2 포트 2~11 배칭→P3 헹굼→P4 prime
  2. 폐액라인(12) 마감 순서 — 마지막 12번 배출 직전 흡인은 반드시 포트 1(깨끗한 용매)
  3. 소형 시린지(1mL) 배칭 — 넘치기 전 자동 배출, 전 포트 커버
  4. 4그룹 병렬 — 전원 done, 명령 직렬화 하에 완주
  5. 중단(stop) — aborted 기록 + _abort_refill 세팅
  6. wash_volume 원복 / Outlet=WASTE 선행 / 컬렉션 옵션 시 1→2→1
  7. 펌프 busy 시 시작 거부
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from core.deep_wash import DeepWashEngine, DeepWashOptions  # noqa: E402


class FakePump:
    def __init__(self, name, capacity=6.0, slow=0.0):
        self.name = name
        self.capacity = capacity
        self.current_vol = 0.0
        self.wash_volume = 3.0
        self.wash_speed = 8.0
        self.prime_rate = 8.0
        self.is_refilling = False
        self._abort_refill = False
        self.slow = slow
        self.ops = []          # ("withdraw", port, vol) / ("dump", port, vol) / ("prime", vol)
        self._pending = 0.0
        self._mode = None

    # wash-withdraw
    def wash_withdraw_prepare(self, solvent_port=1):
        self._pending = min(self.wash_volume, self.capacity)
        self._mode = ("withdraw", solvent_port)
        return True

    def wash_withdraw_trigger(self):
        pass

    def wash_withdraw_complete(self):
        time.sleep(self.slow)
        if self._abort_refill:
            return
        mode, port = self._mode
        self.current_vol = min(self.current_vol + self._pending, self.capacity)
        self.ops.append(("withdraw", port, round(self._pending, 4)))

    # wash-infuse (dump)
    def wash_infuse_prepare(self, waste_port=12):
        self._pending = min(self.wash_volume, self.capacity)
        self._mode = ("dump", waste_port)
        return True

    def wash_infuse_trigger(self):
        pass

    def wash_infuse_complete(self):
        time.sleep(self.slow)
        if self._abort_refill:
            return False
        mode, port = self._mode
        self.current_vol = max(0.0, self.current_vol - self._pending)
        self.ops.append(("dump", port, round(self._pending, 4)))
        return True

    # prime
    def prime_prepare(self):
        self._pending = max(self.current_vol, 0.02)
        return True

    def prime_trigger(self):
        pass

    def prime_complete(self):
        time.sleep(self.slow)
        if self._abort_refill:
            return
        self.ops.append(("prime", round(self._pending, 4)))
        self.current_vol = 0.0

    def stop(self):
        pass


class FakeOutlet:
    def __init__(self):
        self.positions = []

    def set_position(self, pos):
        self.positions.append(pos)


def run_engine(pumps, outlet=None, opt=None, timeout=20.0):
    eng = DeepWashEngine(pumps, outlet_valve=outlet, options=opt, log=lambda m: None)
    eng.CMD_INTERVAL = 0.0
    assert eng.start()
    t0 = time.time()
    while eng.running:
        if time.time() - t0 > timeout:
            raise TimeoutError("engine did not finish")
        time.sleep(0.005)
    return eng


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  ({detail})"))


print("== 1/2. 단일 그룹(6mL) 순서 + 12번 마감 (기본 = 포트마다 즉시 배출) ==")
p = FakePump("Group A")
out = FakeOutlet()
opt = DeepWashOptions(ports=range(2, 12), v_port=0.5, common_cycles=2,
                      v_common=3.0, downstream_ml=3.0)
eng = run_engine({"Group A": p}, out, opt)
ops = p.ops
# P1: 공통 2사이클
check("P1 공통 2사이클", ops[0:4] == [("withdraw", 1, 3.0), ("dump", 12, 3.0)] * 2, str(ops[0:4]))
# P2: 포트마다 "N 흡인 → 12 배출" 쌍 반복 (사용자 확정 방식)
p2 = ops[4:24]
expected_p2 = []
for n in range(2, 12):
    expected_p2 += [("withdraw", n, 0.5), ("dump", 12, 0.5)]
check("P2 포트별 흡인→12배출 쌍 반복", p2 == expected_p2, str(p2))
# P3: 마지막 12번 배출 직전 흡인은 포트 1
dumps = [i for i, o in enumerate(ops) if o[0] == "dump"]
last_dump = dumps[-1]
prev_withdraws = [o for o in ops[:last_dump] if o[0] == "withdraw"]
check("P3 폐액라인 마감(마지막 12 배출 전 흡인=포트1)", prev_withdraws[-1][1] == 1,
      str(prev_withdraws[-1]))
# P4: prime 이 최종 동작
check("P4 prime 최종", ops[-1] == ("prime", 3.0), str(ops[-1]))
check("Outlet=WASTE 선행", out.positions[:1] == [1], str(out.positions))
check("결과 done", eng.results.get("Group A") == "done", str(eng.results))
check("wash_volume 원복", p.wash_volume == 3.0, str(p.wash_volume))

print("== 3a. 소형 시린지(1mL) — 포트별 즉시 배출 ==")
pd = FakePump("Group_D", capacity=1.0)
opt_d = DeepWashOptions(ports=range(2, 12), v_port=0.3, common_cycles=1,
                        v_common=3.0, downstream_ml=3.0)
eng = run_engine({"Group_D": pd}, FakeOutlet(), opt_d)
wd_ports = [o[1] for o in pd.ops if o[0] == "withdraw" and o[1] != 1]
check("전 포트 커버", wd_ports == list(range(2, 12)), str(wd_ports))
check("용량 초과 없음(모든 흡인 ≤ capacity)",
      all(o[2] <= 1.0 + 1e-9 for o in pd.ops if o[0] == "withdraw"), str(pd.ops))
check("결과 done", eng.results.get("Group_D") == "done", str(eng.results))

print("== 3b. batch_dump=True 옵션 — 배칭 유지 ==")
pb2 = FakePump("Group A")
opt_b = DeepWashOptions(ports=range(2, 12), v_port=0.5, common_cycles=1,
                        v_common=3.0, downstream_ml=3.0, batch_dump=True)
eng = run_engine({"Group A": pb2}, FakeOutlet(), opt_b)
check("배칭 일괄 배출 5.0", ("dump", 12, 5.0) in pb2.ops, str(pb2.ops))
check("배칭 결과 done", eng.results.get("Group A") == "done", str(eng.results))

print("== 4. 4그룹 병렬 ==")
pumps = {n: FakePump(n, capacity=(1.0 if n == "Group_D" else 6.0), slow=0.003)
         for n in ("Group A", "Group_B", "Group_C", "Group_D")}
eng = run_engine(pumps, FakeOutlet(), DeepWashOptions())
check("4그룹 전원 done", all(v == "done" for v in eng.results.values()), str(eng.results))

print("== 5. 중단 ==")
ps = FakePump("Group A", slow=0.05)
eng = DeepWashEngine({"Group A": ps}, FakeOutlet(), DeepWashOptions(),
                     log=lambda m: None)
eng.CMD_INTERVAL = 0.0
eng.start()
time.sleep(0.12)
eng.stop()
t0 = time.time()
while eng.running and time.time() - t0 < 10:
    time.sleep(0.01)
check("중단 결과 aborted", eng.results.get("Group A") == "aborted", str(eng.results))
check("_abort_refill 세팅", ps._abort_refill is True, str(ps._abort_refill))

print("== 6. 컬렉션 옵션 ==")
pc = FakePump("Group A")
out = FakeOutlet()
eng = run_engine({"Group A": pc}, out,
                 DeepWashOptions(include_collection=True, collection_ml=0.3))
check("Outlet 1→2→1", out.positions == [1, 2, 1], str(out.positions))
check("컬렉션 플러시 prime 추가", [o for o in pc.ops if o[0] == "prime"][-1][1] == 0.3,
      str(pc.ops[-3:]))

print("== 6b. 유량 오버라이드 + 원복 ==")
pr = FakePump("Group A")
opt_r = DeepWashOptions(ports=[2], v_port=0.5, common_cycles=1,
                        v_common=2.0, downstream_ml=1.0)
opt_r.wash_rate = 4.0
opt_r.downstream_rate = 2.0


class RatePump(FakePump):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.seen_wash_rates = []
        self.seen_prime_rates = []

    def wash_withdraw_prepare(self, solvent_port=1):
        self.seen_wash_rates.append(self.wash_speed)
        return super().wash_withdraw_prepare(solvent_port)

    def prime_prepare(self):
        self.seen_prime_rates.append(self.prime_rate)
        return super().prime_prepare()


pr = RatePump("Group A")
eng = run_engine({"Group A": pr}, FakeOutlet(), opt_r)
check("wash_rate 4.0 적용", all(r == 4.0 for r in pr.seen_wash_rates),
      str(pr.seen_wash_rates))
check("downstream_rate 2.0 적용", pr.seen_prime_rates == [2.0],
      str(pr.seen_prime_rates))
check("종료 후 wash_speed 원복", pr.wash_speed == 8.0, str(pr.wash_speed))
check("종료 후 prime_rate 원복", pr.prime_rate == 8.0, str(pr.prime_rate))

pr0 = RatePump("Group A")
opt_r0 = DeepWashOptions(ports=[2], v_port=0.5, common_cycles=1,
                         v_common=2.0, downstream_ml=1.0)
eng = run_engine({"Group A": pr0}, FakeOutlet(), opt_r0)
check("오버라이드 0 = 그룹 설정 유지", all(r == 8.0 for r in pr0.seen_wash_rates)
      and pr0.seen_prime_rates == [8.0],
      f"{pr0.seen_wash_rates}/{pr0.seen_prime_rates}")

print("== 7. busy 시작 거부 ==")
pb = FakePump("Group A")
pb.is_refilling = True
eng = DeepWashEngine({"Group A": pb}, None, DeepWashOptions(), log=lambda m: None)
check("start()=False", eng.start() is False)

fails = sum(1 for _, ok in results if not ok)
print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILED'} ({len(results)} checks)")
sys.exit(1 if fails else 0)
