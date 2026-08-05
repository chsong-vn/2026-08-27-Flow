"""
샘플러(GRBL 0.9j) + GPIO(N2 밸브) 브링업 테스트.
전제: GRBL $22(호밍) 활성 + 엔드스톱 배선 완료. 초회는 니들 미장착 상태로 권장.
사용: python smoke_sampler_gpio.py <GRBL포트> <GPIO포트>
"""
import sys
from robochem_devices import Sampler, GpioArray, GrblPosition
from robochem_devices.errors import ForbiddenMoveError

grbl_port = sys.argv[1] if len(sys.argv) > 1 else "COM8"
gpio_port = sys.argv[2] if len(sys.argv) > 2 else None

# ---------- 샘플러 ----------
s = Sampler()
s.open(grbl_port)                    # 웰컴 메시지에서 GRBL 버전 검사 (0.9j 아니면 경고)
s.setup(
    needle_length="51mm",
    safe_z="-30.0mm",                # 실측 후 조정
    vial_top_z="-34.5mm",
    ignore_safe_z=False,
)
s.initialize()                       # $X → 호밍($H) → G92 원점 → G21/G90 → F1000

print("상태:", s["status"])
print("영역:", s.position_min.coords(), "~", s.position_max.coords())

# 안전 높이에서 XY 이동 (완료까지 블로킹: G4 P0.1 트릭)
s["feed"] = s.default_feedrate_xy
s["move"] = GrblPosition(x=50.0, y=50.0)
print("이동 후 위치:", s["position"].coords())

# safe_z 인터록 검증: 니들 내리고 XY 시도 → 예외가 나야 정상
s["feed"] = s.default_feedrate_z
s["move"] = GrblPosition(z=-35.0)
try:
    s["move"] = GrblPosition(x=60.0)
    print("!! 인터록 미작동 - safe_z 설정 확인 필요")
except ForbiddenMoveError:
    print("safe_z 인터록 정상 작동")
s["move"] = GrblPosition(z=0.0)

# 보조 니들 (M8/M9 → 서보 아두이노)
s["aux_needle"] = "ON"
s["aux_needle"] = "OFF"

s.return_to_home()
s.close()

# ---------- GPIO / N2 ----------
if gpio_port:
    g = GpioArray()
    g.open(gpio_port)
    g.setup(array={
        "n2_valve": {"index": "D2", "class": "solenoid_valve"},
    })
    g.initialize()                   # OUTPUT 모드 + 닫힘 보장
    n2 = g["n2_valve"]
    import time
    n2["valve"] = "open"
    time.sleep(2.0)
    n2["valve"] = "close"
    print("N2 밸브 open/close OK")
    g.close()                        # prepare_for_close가 밸브 닫힘 페일세이프
