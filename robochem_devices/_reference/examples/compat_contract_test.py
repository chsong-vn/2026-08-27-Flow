"""
compat_contract_test.py — 펌프 어댑터 계약 테스트.

기존 분취 제어 엔진이 펌프에 요구하는 전제 7개를 계약으로 명문화하고 검증.
같은 스위트를 (a) RoboChemPumpAdapter+에뮬레이터 [지금 실행],
(b) 기존 Chemyx 드라이버 [실험실에서 실행] 에 돌려서 둘 다 통과하면
엔진 입장에서 두 펌프는 교체 가능(호환) — 이게 검증의 정의.

 C1 논블로킹 시작 (<100ms 리턴)          — 스태거 타이밍의 전제
 C2 busy 라이프사이클 + wait()=실토출량   — 시퀀서 완료 판정
 C3 진행률 단조증가 + 완료 시 실측 스냅    — 데드볼륨 모델/차트 입력
 C4 정지 → 부분 토출 → 즉시 재사용        — 정지버튼 시맨틱
 C5 단위 변환 정합 (mL ↔ 물리 µL)        — 엔진 mL 단위계
 C6 2펌프 스태거 동시 구동               — 비대칭 라인 보정의 핵심 전제
 C7 리필 갭 가시성                       — 유체 트래커 타임라인 보정 입력
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pause
pause.seconds = lambda s: time.sleep(0.01)

from robochem_devices import SyringePump, Logger
from emulators import FakeSerial, PumpEmulator, TIME_SCALE
from compat_adapter import RoboChemPumpAdapter

Logger.console_enabled = False
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def make_adapter():
    emu = PumpEmulator()
    pump = SyringePump()
    pump._serial_iface = FakeSerial(emu)
    pump.specific_name = "cpump"
    pump.open("VIRT")
    pump.setup(**{"syringe_volume": "1.0mL", "syringe_stepperml": "78049.0",
                  "max_flowrate": 30.0, "valve": True,
                  "reservoir_valve_position": "OFF", "aux-valve": False})
    pump.initialize()
    return RoboChemPumpAdapter(pump, syringe_ml=1.0, time_scale=TIME_SCALE), emu


# ---------------- C1: 논블로킹 시작 ----------------
ad, emu = make_adapter()
t0 = time.time()
ad.start_dispense(volume_ml=0.5, flowrate_ml_min=10.0)
launch = time.time() - t0
check("C1 논블로킹 시작", launch < 0.1, f"start 리턴 {launch*1000:.0f}ms")

# ---------------- C2: busy 라이프사이클 ----------------
check("C2a 구동 중 busy=True", ad.busy)
actual = ad.wait(timeout=15)
check("C2b 완료 후 busy=False + 실토출량", not ad.busy and abs(actual - 0.5) < 0.002,
      f"actual={actual:.3f}mL")

# ---------------- C3: 진행률 ----------------
ad3, emu3 = make_adapter()
ad3.start_dispense(volume_ml=0.4, flowrate_ml_min=2.0)   # 12s(스케일 0.24s)
samples = []
while ad3.busy:
    samples.append(ad3.progress_ml)
    time.sleep(0.03)
final = ad3.wait()
monotonic = all(b >= a - 1e-9 for a, b in zip(samples, samples[1:]))
check("C3a 진행률 단조증가", monotonic, f"{len(samples)}샘플")
check("C3b 중간 진행률이 실제로 움직임", len([s for s in samples if 0 < s < 0.4]) >= 2,
      f"중간값 {[f'{s:.2f}' for s in samples[:6]]}")
check("C3c 완료 시 실측 스냅", abs(ad3.progress_ml - final) < 1e-9 and abs(final - 0.4) < 0.002)

# ---------------- C4: 정지 시맨틱 ----------------
ad4, emu4 = make_adapter()
ad4.start_dispense(volume_ml=0.9, flowrate_ml_min=1.0)   # 54s(스케일 1.08s)
time.sleep(0.35)
ad4.stop()
partial = ad4.wait(timeout=10)
check("C4a 정지 → 부분 토출", 0 < partial < 0.88, f"partial={partial:.3f}mL")
check("C4b 물리 볼륨 정합", abs(emu4.volume_ul - (1000 - partial * 1000)) < 3.0,
      f"emul={emu4.volume_ul:.0f}uL")
ad4.start_dispense(volume_ml=0.1, flowrate_ml_min=10.0)  # 정지 직후 재사용
again = ad4.wait(timeout=10)
check("C4c 정지 후 즉시 재사용", abs(again - 0.1) < 0.002, f"again={again:.3f}mL")

# ---------------- C5: 단위 정합 ----------------
ad5, emu5 = make_adapter()
before = emu5.volume_ul
ad5.start_dispense(volume_ml=0.25, flowrate_ml_min=15.0)
ad5.wait()
check("C5 mL→µL 변환 정합", abs((before - emu5.volume_ul) - 250.0) < 1.0,
      f"물리 Δ={before - emu5.volume_ul:.1f}uL")

# ---------------- C6: 스태거 동시 구동 ----------------
adA, _ = make_adapter()
adB, _ = make_adapter()
adA.start_dispense(volume_ml=0.6, flowrate_ml_min=1.0)   # 0.72s(스케일)
time.sleep(0.15)                                          # 스태거 딜레이
t0 = time.time()
adB.start_dispense(volume_ml=0.3, flowrate_ml_min=1.0)
stagger_launch = time.time() - t0
overlap = adA.busy and adB.busy
a, b = adA.wait(), adB.wait()
check("C6a 스태거 시작 (B 시작이 A에 안 막힘)", stagger_launch < 0.1 and overlap,
      f"B런치 {stagger_launch*1000:.0f}ms, 동시구동={overlap}")
check("C6b 양 펌프 정량 완료", abs(a - 0.6) < 0.002 and abs(b - 0.3) < 0.002,
      f"A={a:.3f}, B={b:.3f}")

# ---------------- C7: 리필 갭 가시성 ----------------
ad7, emu7 = make_adapter()
ad7.start_dispense(volume_ml=2.5, flowrate_ml_min=20.0, allow_refill=True)
total = ad7.wait(timeout=30)
check("C7a 다회 리필 정량 (2.5mL / 1mL 시린지)", abs(total - 2.5) < 0.005,
      f"total={total:.3f}mL")
check("C7b 리필 갭이 타임라인에 노출됨", len(ad7.refill_gaps) >= 2,
      f"갭 {len(ad7.refill_gaps)}개: {[(f'{s:.2f}-{e:.2f}s') for s, e in ad7.refill_gaps]}")

# ---------------- 정리/결과 ----------------
for a in (ad, ad3, ad4, ad5, adA, adB, ad7):
    try: a.pump.close()
    except Exception: pass
print()
passed = sum(1 for _, ok, _ in results if ok)
print(f"===== {passed}/{len(results)} PASS =====")
sys.exit(0 if passed == len(results) else 1)
