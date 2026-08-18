"""N2 사전 캘리브레이션(_n2_precal_purge) 검증 — RoboChem OCB350 캘리브 계약 이식.

루트에서 실행:  py -3.14 tests\test_n2_precal.py

계약:
  · 기본 off = 완전 no-op (MFC/센서/밸브 어느 것도 안 건드림)
  · MFC/센서 미배정 = 경고 후 스킵 (크래시 금지)
  · Outlet=WASTE 확보 실패 = 즉시 중단 (invert 리그 — 방향 미상 가스 주입 금지)
  · 배기: 전 센서 GAS 가 settle_sec 연속 → 원점 표집 → MFC OFF
  · 플리커(가스→액체 재출현) = 안정창 리셋
  · 타임아웃/abort/센서예외 = errors 보고 + MFC OFF 보장 (finally)
  · 원점 검증: 튜브 미장착(없음값 근접)/드리프트/임계 역전 플래그
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        FAILED.append(name)


# ── 픽스처 (test_fault_masking_fixes 관례) ─────────────────────────────
class _Pump:
    current_vol = 0.0

    def get_pressure(self):
        return 0.0


class _Heater:
    def get_temp(self):
        return 25.0

    def set_temp(self, t):
        pass


class _Sig:
    def __getattr__(self, _):
        class _E:
            @staticmethod
            def emit(*a, **k):
                pass
        return _E()


class _Valve:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def set_position(self, pos):
        self.calls.append(pos)
        if self.fail:
            raise RuntimeError("comm down")


class FakeMFC:
    def __init__(self):
        self.calls = []

    def set_flow(self, sccm):
        self.calls.append(float(sccm))


class FakeSensor:
    """gas_after 초 경과 후 GAS 보고. flicker_at=(시각,지속) 이면 그 구간 액체 재출현."""

    def __init__(self, sensors=None, thresholds=None, gas_after=0.0,
                 analog_map=None, never_gas=False, raise_read=False,
                 flicker=None):
        self.sensors = sensors or {"reactor_in": 0, "collect": 1}
        # 2026-08-18 재배선 후 실측 기준 (임계 690/750, 공기 522/519)
        self.thresholds = thresholds or {0: 690, 1: 750}
        self.t0 = time.monotonic()
        self.gas_after = gas_after
        self.analog_map = analog_map or {"reactor_in": 522, "collect": 519}
        self.never_gas = never_gas
        self.raise_read = raise_read
        self.flicker = flicker            # (start_sec, dur_sec)
        self.cal_calls = []               # 하드웨어 캘리브 훅 기록

    def calibrate(self, k):
        self.cal_calls.append(k)

    def read_phase(self, k):
        if self.raise_read:
            raise RuntimeError("stale")
        el = time.monotonic() - self.t0
        if self.never_gas:
            return "CLEAR_LIQUID"
        if self.flicker:
            fs, fd = self.flicker
            if fs <= el < fs + fd:
                return "CLEAR_LIQUID"
        return "GAS" if el >= self.gas_after else "CLEAR_LIQUID"

    def analog(self, k):
        return self.analog_map.get(k, -1)


class _Cfg:
    PUMP_VALVE_MAP = {}
    PUMP_ROUTING = {}
    ACTIVE_PUMPS = []
    reactor_vol = 2.7
    reactor_vol_illuminated = 2.4
    reactor_dark_vol = 0.3
    tjunction_line_vols = {}
    tjunction_entry_map = {}
    line_vol_inlet = {}
    line_vol_valve_pump = {}
    line_vol_pump_merge = {}
    valve_internal_vol = {}
    mixing_line_dead_vol = 0.0

    def __init__(self, **sp):
        base = {"post_reactor_vol_ml": 0.2, "collection_line_vol_ml": 0.15,
                "temp_tolerance_c": 0.5, "heater_reach_timeout_sec": 30.0,
                "max_total_flow_ml_min": 100.0, "max_step_volume_ml": 500.0,
                "wash_mode": "off", "prefill_mode": "off",
                "syringe_refill_rate": 20.0,
                "n2_precal_enabled": True,
                "n2_precal_sccm": 20.0,
                "n2_precal_timeout_sec": 2.0,
                "n2_precal_settle_sec": 0.3,
                "n2_precal_sample_sec": 0.3}
        base.update(sp)
        self.config_data = {"system_params": base}


def make_engine(mfc, sensor, outlet=None, **sp):
    cfg = _Cfg(**sp)
    valves = {"Outlet": outlet} if outlet is not None else {}
    eng = StrictSequenceEngine(cfg, {}, valves, _Heater(),
                               SafetyManager(cfg, {}, _Heater()), _Sig(),
                               mfc=mfc, phase_sensor=sensor)
    return eng


print("=" * 72)
print("[1] 기본 off = 완전 no-op")
print("=" * 72)
mfc, s, o = FakeMFC(), FakeSensor(), _Valve()
eng = make_engine(mfc, s, o, n2_precal_enabled=False)
errs = []
eng._n2_precal_purge(errs)
check("MFC 미접촉", not mfc.calls, mfc.calls)
check("Outlet 미접촉", not o.calls)
check("에러 없음", not errs)

print("=" * 72)
print("[2] MFC/센서 미배정 = 스킵 (크래시 금지)")
print("=" * 72)
eng = make_engine(None, FakeSensor(), _Valve())
errs = []
eng._n2_precal_purge(errs)
check("MFC None 스킵", not errs)
eng = make_engine(FakeMFC(), None, _Valve())
errs = []
eng._n2_precal_purge(errs)
check("센서 None 스킵", not errs)

print("=" * 72)
print("[3] Outlet 확보 실패 = 가스 주입 없이 중단")
print("=" * 72)
mfc = FakeMFC()
eng = make_engine(mfc, FakeSensor(), _Valve(fail=True))
errs = []
eng._n2_precal_purge(errs)
check("가스 미주입", not mfc.calls, mfc.calls)
check("에러 보고", any("Outlet" in e for e in errs), errs)

print("=" * 72)
print("[4] 정상 경로 — 배기→안정→원점→OFF")
print("=" * 72)
mfc, o = FakeMFC(), _Valve()
s = FakeSensor(gas_after=0.3, analog_map={"reactor_in": 522, "collect": 519})
eng = make_engine(mfc, s, o)
errs = []
eng._n2_precal_purge(errs)
check("Outlet→WASTE(1) 선행", o.calls[:1] == [1], o.calls)
check("MFC on→off", mfc.calls[0] == 20.0 and mfc.calls[-1] == 0.0, mfc.calls)
check("에러 없음", not errs, errs)
bl = getattr(eng, "_n2_air_baseline", None)
check("원점 캡처(양 채널)", bl is not None and set(bl) == {"reactor_in", "collect"},
      bl and {k: v["air_adc"] for k, v in bl.items()})
check("깨끗한 공기 = 플래그 없음",
      bl and not bl["reactor_in"]["flags"] and not bl["collect"]["flags"],
      bl and {k: v["flags"] for k, v in bl.items()})
check("하드웨어 캘리브 훅 — 배기 후 양 채널 호출 (RoboChem Cal 핀 계약)",
      sorted(s.cal_calls) == ["collect", "reactor_in"], s.cal_calls)

print("=" * 72)
print("[5] 플리커 — 액체 재출현 시 안정창 리셋 후 재안정")
print("=" * 72)
mfc = FakeMFC()
s = FakeSensor(gas_after=0.1, flicker=(0.3, 0.2))
eng = make_engine(mfc, s, _Valve())
errs = []
t0 = time.monotonic()
eng._n2_precal_purge(errs)
dur = time.monotonic() - t0
check("플리커 후에도 완료", not errs, errs)
check("안정창이 플리커 뒤로 밀림 (>=0.8s)", dur >= 0.75, f"{dur:.2f}s")

print("=" * 72)
print("[6] 타임아웃 — 영원히 액체 (튜브 빠짐 실증상)")
print("=" * 72)
mfc = FakeMFC()
eng = make_engine(mfc, FakeSensor(never_gas=True), _Valve(),
                  n2_precal_timeout_sec=0.5)
errs = []
eng._n2_precal_purge(errs)
check("타임아웃 보고", any("타임아웃" in e for e in errs), errs)
check("MFC OFF 보장(finally)", mfc.calls and mfc.calls[-1] == 0.0, mfc.calls)
check("원점 미캡처", not getattr(eng, "_n2_air_baseline", None))

print("=" * 72)
print("[7] 센서 예외 — 중단 + MFC OFF")
print("=" * 72)
mfc = FakeMFC()
eng = make_engine(mfc, FakeSensor(raise_read=True), _Valve())
errs = []
eng._n2_precal_purge(errs)
check("센서 오류 보고", any("센서 오류" in e for e in errs), errs)
check("MFC OFF", mfc.calls[-1] == 0.0, mfc.calls)

print("=" * 72)
print("[8] abort — 즉시 중단 + MFC OFF")
print("=" * 72)
mfc = FakeMFC()
eng = make_engine(mfc, FakeSensor(gas_after=99.0), _Valve(),
                  n2_precal_timeout_sec=30.0)
eng.abort_flag = True
errs = []
eng._n2_precal_purge(errs)
check("abort 보고", any("abort" in e for e in errs), errs)
check("MFC OFF", not mfc.calls or mfc.calls[-1] == 0.0, mfc.calls)

print("=" * 72)
print("[9] 원점 검증 플래그")
print("=" * 72)
# 튜브 미장착: ch1 이 '없음 960' 근접 (⚠ch0 은 공기 522≈없음 553 이라 검사 스킵 설계)
mfc = FakeMFC()
s = FakeSensor(gas_after=0.1, analog_map={"reactor_in": 522, "collect": 950})
eng = make_engine(mfc, s, _Valve())
eng._n2_precal_purge([])
bl = eng._n2_air_baseline
check("튜브 미장착 플래그(ch1)", any("미장착" in f for f in bl["collect"]["flags"]),
      bl["collect"]["flags"])
check("ch0 은 미장착 검사 스킵 (none=None)",
      not any("미장착" in f for f in bl["reactor_in"]["flags"]),
      bl["reactor_in"]["flags"])
# 임계 역전: 가스인데 ADC > thr (fail-unsafe 를 원점에서 검출)
mfc = FakeMFC()
s = FakeSensor(gas_after=0.1, analog_map={"reactor_in": 522, "collect": 800})
eng = make_engine(mfc, s, _Valve())
eng._n2_precal_purge([])
bl = eng._n2_air_baseline
check("임계 역전 플래그", any("임계" in f for f in bl["collect"]["flags"]),
      bl["collect"]["flags"])
# 드리프트: 공기 기준에서 100 이상 이탈 (없음값과는 멀리)
mfc = FakeMFC()
s = FakeSensor(gas_after=0.1, analog_map={"reactor_in": 230, "collect": 519})
eng = make_engine(mfc, s, _Valve())
eng._n2_precal_purge([])
bl = eng._n2_air_baseline
check("드리프트 플래그", any("드리프트" in f for f in bl["reactor_in"]["flags"]),
      bl["reactor_in"]["flags"])

print("=" * 72)
print("[10] 모드 B 스타일 — PC 임계 없는 드라이버 (판정=보드 하드웨어)")
print("=" * 72)
# OCB350 어레이는 thresholds 를 안 씀 — 임계 역전 검사는 건너뛰고 크래시 없어야
mfc = FakeMFC()
s = FakeSensor(gas_after=0.1, thresholds={},
               analog_map={"reactor_in": 522, "collect": 519})
eng = make_engine(mfc, s, _Valve())
errs = []
eng._n2_precal_purge(errs)
bl = getattr(eng, "_n2_air_baseline", None)
check("임계 없이도 원점 캡처", bl is not None and len(bl) == 2, bl)
check("임계 역전 플래그 없음(검사 스킵)",
      bl and not any("임계" in f for v in bl.values() for f in v["flags"]),
      bl and {k: v["flags"] for k, v in bl.items()})
check("에러 없음", not errs, errs)

print()
print("RESULT:", "ALL PASS" if not FAILED else f"{len(FAILED)} FAIL: {FAILED}")
sys.exit(1 if FAILED else 0)
