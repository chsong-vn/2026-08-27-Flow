"""파라미터 경계 결함 탐색 — validate/calculator 그리드 테스트

당량·부피·유속·온도 축의 경계값(0, 음수, NaN, inf, 용량 초과, 극단 비대칭)을
_validate_step_inputs와 FlowCalculator에 직접 주입해 방어 여부를 매트릭스로 검사.
기대: BLOCK(SafetyError/ValueError로 차단) 또는 OK(통과).
"""
import sys, math
sys.path.insert(0, ".")

from engine.strict_engine import StrictSequenceEngine
from engine.safety_manager import SafetyManager, SafetyError
from engine.calculators import FlowCalculator
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump


class P(ChemyxSmartPump):
    def __init__(self, cap=6.0):
        self.capacity = cap
        self.current_vol = 0.0
        self.running = False
        self.target_flow = 0.0
        self.status = "Idle"
        self.is_refilling = False
        self._abort_refill = False


class Col:
    is_connected = True
    total_tubes = 88


class Cfg:
    PUMP_VALVE_MAP = {}
    reactor_vol = 2.6
    max_temp = 120.0
    config_data = {"system_params": {
        "post_reactor_vol_ml": 2.0, "collection_line_vol_ml": 1.0,
        "max_total_flow_ml_min": 100.0, "max_step_volume_ml": 500.0,
        "wash_mode": "off", "prefill_mode": "off",
    }}


class Sig:
    def __getattr__(self, n):
        return type("S", (), {"emit": staticmethod(lambda *a: None)})()


pumps = {"A": P(), "B": P()}
eng = StrictSequenceEngine(Cfg(), pumps, {}, None, SafetyManager(Cfg(), pumps, None),
                           Sig(), collector=Col(), push_pump=None)
eng.current_tube = 1
FR = {"enabled": True}


def exp(vol=1.0, tube=0.5, fa=1.0, fb=1.0, temp=25.0, ports=None):
    e = {"temp": temp, "vol_ml": vol, "collect_volume_per_tube": tube,
         "flows": {"A": fa, "B": fb}, "inlet_ports": ports if ports is not None else {"A": 2, "B": 3}}
    return e


CASES = [
    # (이름, exp, 기대: "OK" | "BLOCK")
    ("정상 기준", exp(), "OK"),
    ("vol=0", exp(vol=0), "BLOCK"),
    ("vol 음수", exp(vol=-1), "BLOCK"),
    ("vol NaN", exp(vol=float("nan")), "BLOCK"),
    ("vol inf", exp(vol=float("inf")), "BLOCK"),
    ("vol > max(500)", exp(vol=501), "BLOCK"),
    ("tube=0", exp(tube=0), "BLOCK"),
    ("tube 음수", exp(tube=-0.5), "BLOCK"),
    ("tube NaN", exp(tube=float("nan")), "BLOCK"),
    ("tube ≪ vol (튜브폭발→용량초과)", exp(vol=50, tube=0.5), "BLOCK"),
    ("flow A=0,B=0", exp(fa=0, fb=0), "BLOCK"),
    ("flow 음수", exp(fa=-1, fb=2), "BLOCK"),
    ("flow NaN", exp(fa=float("nan"), fb=1), "BLOCK"),
    ("flow inf", exp(fa=float("inf"), fb=1), "BLOCK"),
    ("총유속 > max(100)", exp(fa=60, fb=60), "BLOCK"),
    ("temp > max(120)", exp(temp=150), "BLOCK"),
    ("temp NaN", exp(temp=float("nan")), "BLOCK"),
    ("port=0", exp(ports={"A": 0, "B": 2}), "BLOCK"),
    ("port=13", exp(ports={"A": 13, "B": 2}), "BLOCK"),
    ("flows에 있는데 ports에 없음(용매 오주입)", exp(ports={"A": 2}), "BLOCK"),
    ("시린지 용량 초과 inject(7mL/pump)", exp(vol=14, tube=2.0), "BLOCK"),
    ("극초단 injection(<2s, 비율 붕괴)", exp(vol=0.1, fa=20, fb=20, tube=0.5), "BLOCK"),
    ("극초단 분획(tube_sec<2s)", exp(vol=4, tube=0.1, fa=20, fb=20), "BLOCK"),
    ("legacy push 용매 필요량 > 용량", None, "BLOCK"),  # 아래 별도 구성
    ("당량 극단비대칭 1.9:0.1 (정상)", exp(fa=1.9, fb=0.1), "OK"),
    ("단일튜브 tube>vol (정상)", exp(vol=0.4, tube=1.5), "OK"),
]

fails = []
print("=== _validate_step_inputs 그리드 ===")
for name, e, expect in CASES:
    if e is None:
        # legacy push 필요량: 작은 시린지(1.5mL)로 push_total(5.6mL)의 절반(2.8)>1.5
        small = {"A": P(cap=1.5), "B": P(cap=1.5)}
        eng2 = StrictSequenceEngine(Cfg(), small, {}, None,
                                    SafetyManager(Cfg(), small, None), Sig(),
                                    collector=Col(), push_pump=None)
        eng2.current_tube = 1
        target = (eng2, exp(vol=1.0, tube=0.5))
    else:
        target = (eng, e)
    en, ee = target
    try:
        en._validate_step_inputs(1, ee, FR)
        result = "OK"
    except (SafetyError, ValueError) as ex:
        result = "BLOCK"
        detail = str(ex)[:60]
    except Exception as ex:
        result = f"CRASH({type(ex).__name__})"
        detail = str(ex)[:60]
    mark = "PASS" if result == expect else "FAIL"
    if mark == "FAIL":
        fails.append(name)
    extra = f" [{detail}]" if result != "OK" and mark == "PASS" else ""
    print(f"  {mark} {name}: 기대={expect} 결과={result}{extra}")

print("\n=== FlowCalculator 그리드 ===")
calc = FlowCalculator(Cfg())
CALC_CASES = [
    ("정상", ([0.2, 0.4], [1.0, 2.0], 10.0), "OK"),
    ("rt=0", ([0.2], [1.0], 0.0), "BLOCK"),
    ("rt 음수", ([0.2], [1.0], -5.0), "BLOCK"),
    ("conc 음수", ([-0.2, 0.4], [1.0, 1.0], 10.0), "BLOCK"),
    ("eq 음수 → 음수 유속 산출 금지", ([0.2, 0.4], [-1.0, 2.0], 10.0), "BLOCK"),
    ("eq NaN", ([0.2], [float("nan")], 10.0), "BLOCK"),
    ("conc NaN", ([float("nan")], [1.0], 10.0), "BLOCK"),
    ("conc=0 (세척용매, flow 0 허용)", ([0.0, 0.4], [1.0, 1.0], 10.0), "OK"),
    ("eq 전부 0 → 총유속 0 반환", ([0.2, 0.4], [0.0, 0.0], 10.0), "OK0"),
]
for name, args, expect in CALC_CASES:
    try:
        flows, tf = calc.calculate_flows(*args)
        if any((not math.isfinite(f)) or f < 0 for f in flows):
            result = "BAD_OUTPUT"
        elif expect == "OK0":
            result = "OK0" if tf == 0.0 else "OK"
        else:
            result = "OK"
    except (ValueError,) as ex:
        result = "BLOCK"
    except Exception as ex:
        result = f"CRASH({type(ex).__name__})"
    mark = "PASS" if result == expect else "FAIL"
    if mark == "FAIL":
        fails.append("calc:" + name)
    print(f"  {mark} {name}: 기대={expect} 결과={result}")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
