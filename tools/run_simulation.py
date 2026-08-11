"""
간단한 시뮬레이션 실행 스크립트

사용법:
    1. 아래 파라미터를 원하는 값으로 수정
    2. python run_simulation.py 실행
    3. temp 폴더에 Excel 파일 생성됨
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
from engine.test_detailed_timing import (
    GroupConfig, ReagentConfig, ExperimentParams, SystemParams, TubingSpec,
    run_detailed_simulation, export_to_excel, print_summary_table, HAS_OPENPYXL
)

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                         여기서 파라미터 수정!                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ──────────────────────────────────────────────────────────────────────────────
# 1. Common Path 튜빙 규격 (내경 + 길이 → 부피 자동 계산)
# ──────────────────────────────────────────────────────────────────────────────
# Reactor (반응기)
REACTOR_TUBING = TubingSpec(id_mm=1.0, length_m=10.0)      # ID=1.0mm, L=10m ← 수정

# Post-Reactor (반응기 → Outlet 밸브 연결 튜빙)
POST_REACTOR_TUBING = TubingSpec(id_mm=1.0, length_m=2.546)  # ID=1.0mm, L=2.546m ← 수정

# Collection Line (Outlet 밸브 → 분취기 연결 튜빙)
COLLECTION_LINE_TUBING = TubingSpec(id_mm=1.0, length_m=1.273)  # ID=1.0mm, L=1.273m ← 수정

print(f"[Reactor]       ID={REACTOR_TUBING.id_mm}mm, L={REACTOR_TUBING.length_m:.2f}m → {REACTOR_TUBING.volume_ml:.3f} mL")
print(f"[Post-Reactor]  ID={POST_REACTOR_TUBING.id_mm}mm, L={POST_REACTOR_TUBING.length_m:.3f}m → {POST_REACTOR_TUBING.volume_ml:.3f} mL")
print(f"[Collection]    ID={COLLECTION_LINE_TUBING.id_mm}mm, L={COLLECTION_LINE_TUBING.length_m:.3f}m → {COLLECTION_LINE_TUBING.volume_ml:.3f} mL")

# ──────────────────────────────────────────────────────────────────────────────
# 2. 펌프 그룹 설정 (그룹 추가/삭제 가능)
# ──────────────────────────────────────────────────────────────────────────────

# 펌프별 튜빙 설정 (데드볼륨 계산용)
PUMP_TUBING = {
    "Group A": {"tube_len_cm": 100.0, "tube_id_mm": 1.0},   # 튜빙 길이(cm), 내경(mm)
    "Group B": {"tube_len_cm": 100.0, "tube_id_mm": 1.0},
    "Group C": {"tube_len_cm": 50.0, "tube_id_mm": 0.8},    # 촉매는 짧은 튜빙
}

def calc_dead_vol(tube_len_cm, tube_id_mm):
    """데드볼륨 계산: π × r² × L (mL)"""
    r_cm = (tube_id_mm / 10.0) / 2.0
    return math.pi * (r_cm ** 2) * tube_len_cm

groups = [
    # ═══ Group A ═══
    GroupConfig(
        name="Group A",
        reagent=ReagentConfig(
            name="Amine",           # 시약 이름 (자유롭게 입력)
            port=3,                 # 12-way 밸브 포트 번호 (1~12)
            concentration=1.0,      # 농도 (M) ← 수정
            equivalents=1.0         # 몰비 (eq) ← 수정
        ),
        syringe_volume=10.0,        # 시린지 용량 (mL)
        refill_rate=40.0            # 리필 속도 (mL/min)
    ),

    # ═══ Group B ═══
    GroupConfig(
        name="Group B",
        reagent=ReagentConfig(
            name="Aldehyde",
            port=5,
            concentration=0.5,      # 농도 (M) ← 수정
            equivalents=1.2         # 몰비 (eq) ← 수정
        ),
        syringe_volume=10.0,
        refill_rate=40.0
    ),

    # ═══ Group C ═══
    GroupConfig(
        name="Group C",
        reagent=ReagentConfig(
            name="Catalyst",
            port=7,
            concentration=0.1,      # 농도 (M) ← 수정
            equivalents=0.05        # 몰비 (eq) ← 수정 (촉매는 보통 0.01~0.1)
        ),
        syringe_volume=5.0,         # 촉매는 작은 시린지
        refill_rate=30.0
    ),
]

# ──────────────────────────────────────────────────────────────────────────────
# 3. 실험 파라미터
# ──────────────────────────────────────────────────────────────────────────────
experiment = ExperimentParams(
    temperature=80.0,           # 반응 온도 (°C) ← 수정
    residence_time=10.0,        # 체류 시간 (min) ← 수정 (반응 시간)
    reaction_volume=5.0,        # 반응량 (mL) ← 수정 (총 생산량)
    volume_per_tube=1.5,        # 튜브당 수거량 (mL)
    start_tube=1                # 시작 튜브 번호
)

# ──────────────────────────────────────────────────────────────────────────────
# 4. 시스템 파라미터
# ──────────────────────────────────────────────────────────────────────────────
PRIMING_RATE_ML_MIN = 5.0           # 프라이밍 유속 (mL/min)
TEMP_TOLERANCE = 1.0                # 온도 허용 오차 (°C)

# 시린지 세척 설정 (시뮬레이션에는 미포함, 참고용)
SYRINGE_WASH_RATE = 15.0            # 세척 유속 (mL/min)
SYRINGE_WASH_COUNT = 2              # 세척 반복 횟수

# 평균 데드볼륨 계산
avg_dead_vol = sum(
    calc_dead_vol(PUMP_TUBING[g.name]["tube_len_cm"], PUMP_TUBING[g.name]["tube_id_mm"])
    for g in groups
) / len(groups)

# TubingSpec → 부피 자동 계산 + 물리적 위치 추적 활성화
params = SystemParams(
    reactor_tubing=REACTOR_TUBING,
    post_reactor_tubing=POST_REACTOR_TUBING,
    collection_line_tubing=COLLECTION_LINE_TUBING,
    dead_volume_per_line=avg_dead_vol,
    priming_rate=PRIMING_RATE_ML_MIN,
    temp_tolerance=TEMP_TOLERANCE
)

# ──────────────────────────────────────────────────────────────────────────────
# 5. 출력 파일명 (None이면 자동 생성)
# ──────────────────────────────────────────────────────────────────────────────
output_filename = None  # 예: "my_experiment.xlsx"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                         아래는 수정하지 마세요                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("FLOW CHEMISTRY SIMULATION")
    print("=" * 70)

    # 유속 미리보기
    from engine.test_detailed_timing import calculate_flows
    flows = calculate_flows(groups, experiment.residence_time, params.reactor_volume)

    print("\n[입력 파라미터]")
    print(f"  온도: {experiment.temperature}°C")
    print(f"  체류 시간: {experiment.residence_time} min")
    print(f"  반응량: {experiment.reaction_volume} mL")

    print("\n[계산된 유속]")
    for g in groups:
        flow = flows[g.name]
        print(f"  {g.name} ({g.reagent.name}): {g.reagent.concentration}M × {g.reagent.equivalents}eq → {flow:.4f} mL/min")
    print(f"  총 유속: {sum(flows.values()):.4f} mL/min")

    # 시뮬레이션 실행
    print("\n시뮬레이션 실행 중...")
    events, total_time, flows, tracker = run_detailed_simulation(groups, experiment, params)

    # 결과 출력
    print_summary_table(events, flows, groups, total_time)

    # Excel 저장
    if HAS_OPENPYXL:
        filepath = export_to_excel(
            events, flows, groups, experiment, params, total_time,
            output_filename, tracker=tracker
        )

        # 자동으로 Excel 열기
        import os
        os.startfile(filepath)
    else:
        print("\n[!] openpyxl이 설치되지 않아 Excel 저장 불가")
        print("    pip install openpyxl")
