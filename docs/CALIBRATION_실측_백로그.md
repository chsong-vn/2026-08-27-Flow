# 실측 캘리브레이션 백로그 (2026-07-29, 갱신 2026-08-13)

> 소프트웨어 수정으로 못 잡는 오차는 전부 "미실측 값" 때문이다.
> 이 문서는 **무엇을, 어떻게 재서, 어디에 넣는지**를 한 곳에 고정한다.
> (적대 검증 잔존항목 #3·#6 대응 — engine 경고 `[Config] ⚠ 세분화 키 전부 0` 해소용)
>
> **실측값의 원장은 `tubing_measurements.json`** (기록·이력·방법) →
> `py -3.14 tools\apply_tubing_measurements.py --apply` 로 config 반영.
> 정합 검증: `py -3.14 tests\verify_timing_deadvol_consistency.py` (3층 교차, ALL PASS 유지).
> 구간 실측 도구: `py -3.14 tools\calibrate_deadvol.py` (segment 대화형 / breakthrough / marks / blowout).

## 우선순위 1 — 분획 타이밍의 몸통

### 1-A. 리액터 실물 확정 ✅ 완료 (2026-08-12~13)
- **실측**: 총 **2.4 mL** (등가 reactor_len_m 3.056 반영, config 재계산 2.4002)
- **암부**: 앞단 0.15 + 뒷단 0.15 mL 무조사(총부피에 포함) → **조사 부피 2.1 mL**
  — 수송 타이밍은 총 2.4 정합, 광화학 체류시간 계산엔 2.1 사용(엔진 미소비, 원장 기록)
- **예비 반응기 1개 = 2.6 mL** (2026-08-13 기록) — 교체 시 원장 measured_ml=2.6 → --apply

### 1-B. outlet_switch_delay_sec 염료 실측 (해석적 HEAD 전체를 한 방에 우회)
- **방법**: 염료 플러그 1회 주입 런 → 주입 START부터 **아웃렛 밸브에 색 도달**까지 초시계/로그. 유속별 1점씩(주 사용 유속 2개면 충분 — 값은 부피/F라 환산 가능, 실측 1점 + 검산 1점 권장)
- **입력**: `system_params.outlet_switch_delay_sec` (실측값이 있으면 엔진이 해석식 대신 이 값 사용)
- **주의**: compensated 모드 검증과 같은 런에서 겸행 가능 — 니들 도착 시각(+Δ)도 함께 기록

### 1-C. 수집라인(밸브→니들) 부피 검산 ✅ 완료 (2026-08-13)
- **실측**: **0.25 mL** 부피 실측 (2026-08-05 의 0.24 갱신) — outlet 3-way 기점→분취기 니들, ID 0.8mm

## 우선순위 2 — 펌프별 라인 세분화 부피 ✅ 완료 (2026-08-05 ~ 08-13, 전 구간 실측)

| 구간 | 키 | 실측값 |
|------|-----|--------|
| 바이알→12-way (시약 포트) | `tube_vol_inlet` | 150mm×0.8mm = 75.4µL (포트1 세척/포트12 폐기 = 850mm = 427.3µL) |
| 12-way 내부 | `tube_vol_selector` | 22.4µL (SV-07 스펙) |
| 12-way→3-way→시린지 | `tube_vol_valve_pump` | 1.8796mL (실측 1.83 + 루어/유니온/90mm 튜브) |
| 3-way 내부 | `tube_vol_switcher` | 50.7µL (Mrv-01B M02 스펙) |
| 3-way→자기 정션 | `tube_vol_pump_merge` | 357mm×0.8mm = 179.4µL (4그룹 동일) |
| QUAD-1→QUAD-2 / QUAD-2→가스T | `tjunction_line_vols[1]/[2]` | 90.5 / 45.2µL |
| 가스T→OPB센서→반응기 | mixing (등가길이) | 116+74mm = **95.5µL** (구 2.65mL 추정 → 격감) |
| 반응기→photo센서→아웃렛 | `post_reactor_vol_ml` | 325+10+76 = 411mm = **206.6µL** (구 2.0mL 추정이 ~10배 과대, t_head 269s@0.4 단축) |

- 원장 `tubing_measurements.json` 에 구간분해·방법·이력 기록, --apply 로 config 반영 완료.
  photo센서→아웃렛 38.2µL = 센서 트리거→밸브 도달 오프셋 근거.

## 우선순위 3 — 장비 규격/동작

### 3-A. 시린지 실물 규격 (적대검증 #6)
- **현상**: config Group A = capacity 6.0mL / diameter 12.45mm, 그런데 캘리브레이션은 **5mL 주사기**로 수행
- **영향**: 직경 오차 → (d_real/d_cfg)² 만큼 모든 유속·부피 스케일 오차
- **방법**: 주사기 배럴 각인 확인 + 1mL 토출 칭량 검산
- **입력**: 펌프 settings `capacity`/`diameter` + **Chemyx 펌프 자체의 diameter 설정도 동일하게**

### 3-B. plate96 행전환 이동시간 → min_tube_sec / lead
- **방법**: Manual 탭에서 A12→B1(행전환) 이동 로그 타임스탬프 (또는 시퀀스 로그의 `액션 X.Xs 소요`)
- **입력**: `min_tube_sec` 상향 + `collector_move_lead_sec`(선행발화) 시드

### 3-C. HTE gas_equiv (HTE 모드 쓸 때)
- `hte_gas_equiv_flow_ml_min: 20.0` 실측 캘리브레이션 (기존 백로그)

## compensated 첫 실기런 체크리스트 (염료 겸행)

1. 시작: 니들이 **RES_CENTER(공칭 100.0, 171.2 — 티칭 후 실측값)** 위에 파킹되는지
   (2026-08-01 deck_v13: 구 WASH(120.7,167.0) 딥 세척통 → 폐액 리저버 '림 위 토출'로 대체.
    Z_dip 값 자체가 림 위 높이라 니들이 담기지 않는 게 정상)
2. `[CollectLine] compensated: 니들 이벤트 +Δs` 로그의 Δ가 (수집라인/유속)과 일치하는지
3. HEAD+Δ에 첫 웰 진입 / 염료 도착과 웰 경계 일치 여부
4. 종료 시 "Move → WASH (line flush)" 후 라인에 **용매만** 남는지 (다음 런 첫 웰 확인)
5. 게이트 로그: `자동정지 유예`, `[레벨센서] ... 실측 폐루프`, 잔량 경고 유무
6. 이상 시 즉시 롤백: `collect_line_mode: "legacy"`

## 진행 현황 (2026-08-13)
- [x] 1-A 리액터 (2.4 mL, 암부 0.15×2, 예비 2.6) / [ ] **1-B 염료 delay ← 남은 것 중 최우선** / [x] 1-C 수집라인 (0.25)
- [x] 2 라인 세분화 (4그룹 전 구간 + mixing + post_reactor) / [ ] 3-A 시린지 / [ ] 3-B 행전환 / [ ] 3-C gas_equiv
- [ ] compensated 실기 확정 (→ 코드 기본값 승격 검토)
- [ ] purge_order 염료 판정 (fifo↔lifo — lifo 확인 시 pre_sec 1313→172s @0.1×4)
