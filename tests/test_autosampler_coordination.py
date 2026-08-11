# -*- coding: utf-8 -*-
"""오토샘플러 조율(Phase C v0) Mock 검증.

검증 항목:
  1. SamplerCoordinator 단위: ensure_ready / position→withdraw→lift 순서 / park
  2. 엔진 통합: _check_interlock 샘플러 검증, _smart_prefill_logic 니들 직렬화
     (autosampler 그룹: 이동→흡입→리트랙트 순서, 외부밸브 그룹: 기존 병렬 경로)
  3. _sequence_cleanup 파킹

실행: py -3.14 test_autosampler_coordination.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys

sys.dont_write_bytecode = True
EVENTS = []  # (who, action, detail) 전역 이벤트 기록


class FakeSignals:
    class _Sig:
        def emit(self, *a):
            pass
    sig_log = _Sig()
    sig_status = _Sig()
    sig_progress = _Sig()
    sig_phase_progress = _Sig()
    sig_error = _Sig()
    sig_finished = _Sig()


class RecordingSampler:
    """MockCartesianSampler 계약 축약 + 호출 기록."""
    def __init__(self, name="AS"):
        self.name = name
        self.is_connected = True
        self.vial_positions = {"A1": (10, 10), "V7": (30, 10),
                               "rinse": (50, 10), "waste": (70, 10)}

    def move_to_vial(self, vial_id, depth_override_mm=None):
        EVENTS.append(("sampler", "move_to_vial", vial_id))
        return True, f"moved {vial_id}"

    def lift_needle(self):
        EVENTS.append(("sampler", "lift_needle", ""))
        return True

    def return_home(self):
        EVENTS.append(("sampler", "return_home", ""))
        return True, "home"


class FakeSmartPump:
    """스마트펌프 계약 축약 — strict_engine._SMART_PUMP_API 전체 충족."""
    def __init__(self, name):
        self.name = name
        self.capacity = 2.5
        self.current_vol = 0.0
        self.status = ""
        self.running = False
        self.is_refilling = False
        self.driver = object()

    def refill_prepare(self, port, volume=None):
        EVENTS.append((self.name, "refill_prepare", volume))
        return True

    def refill_trigger(self):
        EVENTS.append((self.name, "refill_trigger", ""))

    def refill_complete(self):
        EVENTS.append((self.name, "refill_complete", ""))

    def prime_prepare(self):
        return False

    def prime_trigger(self):
        pass

    def prime_complete(self):
        pass

    def wash_withdraw_prepare(self, *a, **k):
        EVENTS.append((self.name, "wash_withdraw_prepare", ""))
        return True

    def wash_withdraw_trigger(self):
        EVENTS.append((self.name, "wash_withdraw_trigger", ""))

    def wash_withdraw_complete(self):
        EVENTS.append((self.name, "wash_withdraw_complete", ""))

    def wash_infuse_prepare(self, *a, **k):
        EVENTS.append((self.name, "wash_infuse_prepare", ""))
        return True

    def wash_infuse_trigger(self):
        EVENTS.append((self.name, "wash_infuse_trigger", ""))

    def wash_infuse_complete(self):
        EVENTS.append((self.name, "wash_infuse_complete", ""))

    def set_flow(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        EVENTS.append((self.name, "stop", ""))

    def get_pressure(self):
        return 0.0


class FakeConfig:
    def __init__(self):
        import copy
        self.reactor_vol = 1.98
        self.PUMP_VALVE_MAP = {}
        self.PUMP_ROUTING = {"Group AS": "autosampler", "Group EX": "external_valve"}
        # 인스턴스별 딥카피 — 클래스 속성 공유 시 테스트 간 오염
        self.config_data = copy.deepcopy({
            "system_params": {},
            "roles": {"pumps": [
                {"name": "Group AS", "settings": {"source_vial": "V7"}},
                {"name": "Group EX", "settings": {}},
            ]},
        })


def build_engine():
    from engine.strict_engine import StrictSequenceEngine
    sampler = RecordingSampler()
    pumps = {"Group AS": FakeSmartPump("Group AS"),
             "Group EX": FakeSmartPump("Group EX")}
    eng = StrictSequenceEngine(
        FakeConfig(), pumps, valves={}, heater=None, safety_mgr=None,
        signals=FakeSignals(), collector=None, push_pump=None,
        samplers={"Group AS": sampler},
    )
    return eng, sampler, pumps


def idx(who, action):
    for i, (w, a, _) in enumerate(EVENTS):
        if w == who and a == action:
            return i
    raise AssertionError(f"event not found: {who}.{action}\n{EVENTS}")


def main():
    # ── 1. 코디네이터 단위 ──────────────────────────────
    from engine.sampler_coordinator import SamplerCoordinator
    sc = SamplerCoordinator(RecordingSampler(), group_name="unit")
    ok, msg = sc.ensure_ready(["A1", "V7"])
    assert ok, msg
    ok, msg = sc.ensure_ready(["NOPE"])
    assert not ok and "NOPE" in msg
    bad = RecordingSampler(); bad.is_connected = False
    assert not SamplerCoordinator(bad).ensure_ready([])[0]
    print("[1 OK] SamplerCoordinator.ensure_ready (연결/vial 좌표 검증)")

    # ── 2. 엔진 인터락 ──────────────────────────────────
    eng, sampler, pumps = build_engine()
    plan = [{"flows": {"Group AS": 0.5, "Group EX": 0.7}}]
    eng._check_interlock(plan)  # 정상 통과
    from engine.safety_manager import SafetyError
    eng2, _, _ = build_engine()
    eng2.cfg.config_data["roles"]["pumps"][0]["settings"]["source_vial"] = "NOPE"
    try:
        eng2._check_interlock(plan)
        raise AssertionError("interlock should reject missing vial")
    except SafetyError as e:
        assert "NOPE" in str(e)
    print("[2 OK] 인터락: 샘플러 연결·vial 좌표 사전 차단")

    # ── 3. 프리필 니들 직렬화 ───────────────────────────
    EVENTS.clear()
    eng._smart_prefill_logic(
        inlet_ports={"Group AS": 1, "Group EX": 3},
        flows={"Group AS": 0.5, "Group EX": 0.7},
        fast_rate=5.0, target_vol=2.4, total_flow=1.2,
    )
    # 외부밸브 그룹: 기존 병렬 경로 (prepare→trigger→complete)
    assert idx("Group EX", "refill_prepare") < idx("Group EX", "refill_trigger") < idx("Group EX", "refill_complete")
    # autosampler 그룹: 이동 → prepare → trigger → complete → 리트랙트
    m = idx("sampler", "move_to_vial")
    assert EVENTS[m][2] == "V7", f"wrong vial: {EVENTS[m]}"
    assert m < idx("Group AS", "refill_prepare") < idx("Group AS", "refill_trigger") \
             < idx("Group AS", "refill_complete") < idx("sampler", "lift_needle")
    # 직렬화: 니들 이동은 외부밸브 그룹 complete 이후 (AS 블록이 뒤)
    assert idx("Group EX", "refill_complete") < m
    print("[3 OK] 니들 직렬화: EX 병렬 경로 → AS(이동→흡입→complete→리트랙트) 순차")

    # ── 4. cleanup 파킹 ────────────────────────────────
    EVENTS.clear()
    eng._sequence_cleanup()
    assert idx("sampler", "return_home") >= 0
    print("[4 OK] cleanup 파킹 (return_home)")

    # ── 5. per-step vial 우선 (Phase A) ────────────────
    eng3, _, _ = build_engine()
    EVENTS.clear()
    eng3._smart_prefill_logic(
        inlet_ports={"Group AS": 1}, flows={"Group AS": 0.5},
        fast_rate=5.0, inlet_vials={"Group AS": "A1"})
    m = idx("sampler", "move_to_vial")
    assert EVENTS[m][2] == "A1", f"per-step vial 무시됨: {EVENTS[m]}"
    # 플랜에 vial 없으면 그룹 설정(V7) 폴백
    EVENTS.clear()
    eng3._smart_prefill_logic(
        inlet_ports={"Group AS": 1}, flows={"Group AS": 0.5}, fast_rate=5.0)
    assert EVENTS[idx("sampler", "move_to_vial")][2] == "V7"
    print("[5 OK] per-step vial 우선, 미지정 시 그룹 설정 폴백")

    # ── 6. 인터락: per-step vial 좌표 검증 ─────────────
    eng4, _, _ = build_engine()
    bad_plan = [{"flows": {"Group AS": 0.5}, "inlet_vials": {"Group AS": "ZZ9"}}]
    try:
        eng4._check_interlock(bad_plan)
        raise AssertionError("interlock should reject unknown step vial")
    except SafetyError as e:
        assert "ZZ9" in str(e)
    print("[6 OK] 인터락: per-step vial 좌표 사전 차단")

    # ── 7. 니들 세척 (CleanNeedle 등가) ─────────────────
    eng5, _, pumps5 = build_engine()
    pumps5["Group AS"].wash_count = 1
    pumps5["Group EX"].wash_count = 1
    EVENTS.clear()
    eng5._execute_system_wash({"Group AS": 0.5, "Group EX": 0.7})
    # EX: 기존 병렬 경로 (니들 무관)
    assert idx("Group EX", "wash_withdraw_prepare") < idx("Group EX", "wash_infuse_prepare")
    # AS: rinse 에서 흡입 → waste 에서 배출 → 리트랙트
    moves = [(i, e[2]) for i, e in enumerate(EVENTS)
             if e[0] == "sampler" and e[1] == "move_to_vial"]
    assert [v for _, v in moves] == ["rinse", "waste"], f"needle path wrong: {moves}"
    assert moves[0][0] < idx("Group AS", "wash_withdraw_prepare") \
        < moves[1][0] < idx("Group AS", "wash_infuse_prepare") < idx("sampler", "lift_needle")
    print("[7 OK] 니들 세척: rinse 흡입 → waste 배출 → 리트랙트 (EX 병렬 경로 무영향)")

    print("\nALL AUTOSAMPLER COORDINATION TESTS PASSED")


if __name__ == "__main__":
    main()
