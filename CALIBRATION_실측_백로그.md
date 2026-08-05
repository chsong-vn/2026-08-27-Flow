# 실측 캘리브레이션 백로그 (2026-07-29)

> 소프트웨어 수정으로 못 잡는 오차는 전부 "미실측 값" 때문이다.
> 이 문서는 **무엇을, 어떻게 재서, 어디에 넣는지**를 한 곳에 고정한다.
> (적대 검증 잔존항목 #3·#6 대응 — engine 경고 `[Config] ⚠ 세분화 키 전부 0` 해소용)

## 우선순위 1 — 분획 타이밍의 몸통

### 1-A. 리액터 실물 확정 (설정 불일치!)
- **현상**: config = 3.82m × 1.0mm = 3.00mL / CLAUDE.md 문서 = 3.99m × 0.8mm = 1.98mL — **1mL 차이 = 유속 1mL/min에서 60초 오차**
- **방법**: 설치된 코일 라벨/사양 확인. 불명이면 물충전법 — 코일 단독 분리 → 주사기로 완전 충전 → 배출량 칭량(밀도 보정)
- **입력**: `system_params.reactor_len_m`, `reactor_id_mm`

### 1-B. outlet_switch_delay_sec 염료 실측 (해석적 HEAD 전체를 한 방에 우회)
- **방법**: 염료 플러그 1회 주입 런 → 주입 START부터 **아웃렛 밸브에 색 도달**까지 초시계/로그. 유속별 1점씩(주 사용 유속 2개면 충분 — 값은 부피/F라 환산 가능, 실측 1점 + 검산 1점 권장)
- **입력**: `system_params.outlet_switch_delay_sec` (실측값이 있으면 엔진이 해석식 대신 이 값 사용)
- **주의**: compensated 모드 검증과 같은 런에서 겸행 가능 — 니들 도착 시각(+Δ)도 함께 기록

### 1-C. 수집라인(밸브→니들) 부피 검산
- **현재 값**: `collection_line_vol_ml: 1.0` (추정) — compensated Δ의 분모라 정확해야 함
- **방법**: Outlet→니들 배관만 용매 충전 → 배출 칭량. 또는 염료런에서 (밸브 도달)→(니들 토출) 시간차 × F
- **입력**: `system_params.collection_line_vol_ml`

## 우선순위 2 — 펌프별 라인 세분화 부피 (현재 전부 0)

**방법(구간충전법)**: 각 구간을 공기 상태에서 용매로 채우는 데 든 주사기 눈금 변화 = 구간 부피. 12-way 포트별 inlet 은 대표 1~2포트만 재고 배관 길이비로 산출 가능.

| 구간 | 키 (펌프 role settings) | 용도 |
|------|------------------------|------|
| 바이알→12-way | `tube_vol_inlet` | 첫 사용 포트 퍼지 보정 |
| 12-way→3-way | `tube_vol_valve_pump` | 리필 과충전/퍼지 |
| 12-way 내부 | `tube_vol_selector` | 〃 |
| 3-way 내부 | `tube_vol_switcher` | 주입 타이밍(line_inj) |
| 3-way→합류 | `tube_vol_pump_merge` | 〃 |

- **입력 위치**: 대시보드 배관도 **DETAIL 칩(더블클릭)** 또는 hardware_config.json 펌프 settings
- **효과**: pre-plug 지연·스태거·퍼지 보정 활성화 (현재 0이라 전부 무효)
- 믹싱라인도 함께: `mixing_line_id_mm`/`mixing_line_len_cm` 실물 대조 (현재 1.5mm×150cm=2.65mL — 실물 맞는지)
- post-reactor: `post_reactor_vol_ml: 2.0` (추정) — 1-B 실측이 있으면 중요도 하락

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

## 완료 후 이 문서에서 지울 것
- [ ] 1-A 리액터 / [ ] 1-B 염료 delay / [ ] 1-C 수집라인
- [ ] 2 라인 세분화 (A/B/C 그룹별) / [ ] 3-A 시린지 / [ ] 3-B 행전환 / [ ] 3-C gas_equiv
- [ ] compensated 실기 확정 (→ 코드 기본값 승격 검토)
