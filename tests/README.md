# tests/ — 검증 스크립트

**실행: 프로젝트 루트에서** `py -3.14 tests\test_xxx.py` (exit 0 = 전부 PASS).
pytest가 아니라 standalone 스크립트다 — PASS/FAIL을 print하고 exit code로 끝난다.
각 파일 상단 부트스트랩이 프로젝트 루트를 sys.path에 넣는다 (새 테스트도 같은 규약으로).

구분: **로직** = 하드웨어 불필요(mock/순수 계산) · **Qt** = 화면 위젯 생성(디스플레이 필요, 장비 불필요) ·
**실기** = 실제 장비 연결 필요 · ⏱ = 실시간 시뮬이라 수 분 소요

## 엔진/타이밍 (로직)

| 파일 | 검증 대상 |
|------|----------|
| `test_deadvol_timing.py` | 데드볼륨 구간분리 타이밍 순수함수 (다중펌프 누적유속) |
| `test_engine_liquid_front.py` | liquid front 추적 회귀 (10 시나리오) |
| `test_timer_lanes.py` | CollectionTimer 레인 분리·선행발화·waste 가드 |
| `test_collection_timing_fix.py` | 분취 전환 타이밍 수정 회귀 |
| `test_collector_arrival_dynamics.py` | 분취기 도착 타이밍 동역학 |
| `test_collect_line_mapping.py` | 수집 라인 매핑 순수 수학 |
| `test_fault_masking_fixes.py` | fault-masking 수정 3건 회귀 (Outlet ACK·plate96 motion confirm) |
| `test_timing_flowrate_branching.py` | 유속 분기·재퍼지 판정 |
| `test_pre_run_gates.py` ⏱ | 시퀀스 사전 게이트 (혼합온도 픽스처 = `fixtures/2026-04-30.json`) |
| `test_param_boundaries.py` | 파라미터 경계값 |
| `test_deep_wash.py` | Deep Wash 엔진 (배치 흡인→12 폐기, 에러 경로) |
| `test_hte_droplet.py` ⏱ | HTE 드롭릿 모드 (스페이서 트레인·타이머 불변식) |
| `test_hte_audit_fixes.py` ⏱ | HTE 감사 수정 회귀 |
| `test_hte_sensor_adversarial.py` ⏱ | HTE 센서 게이트 적대적 검증 |
| `test_level_gates.py` | 레벨센서 시퀀스 게이트 ①~⑤ (Fake 시리얼; `test_level_reconcile`의 페이크 재사용) |
| `test_level_reconcile.py` | 레벨 reconcile 로직 (FakeSmartPump) |
| `test_autosampler_coordination.py` | 니들↔펌프 조율 (sampler_coordinator) |
| `test_stock_stoich.py` | 다성분 stock 양론 순수 엔진 |
| `test_stock_recipe_integration.py` | stock 레시피 통합 + F-SCH export 스키마 정합 |
| `test_rack_eppendorf.py` | 에펜도르프 5×5 랙 좌표 (tools/generate_rack_coords 호출 포함) |
| `test_rack_motion_engine.py` | 랙 이동 G-code 레벨 로직 |
| `test_mfc_mkp.py` | MKP MFC 프로토콜 (ASCII+ODD parity) |
| `test_phase_sensor_opb.py` | 위상센서 OPB 판정 로직 |
| `test_esp32_valve.py` | ESP32 8ch 밸브 드라이버 (가짜 링크) |
| `test_valve_path_sync.py` | 밸브 경로 동기화 — ⚠ 2건 FAIL 상태(2026-08-11 확인, 재배치 전부터). "INFUSE 시 3-way 자동 정렬" 기대가 2026-07-31 "매뉴얼 펌프 버튼=밸브 무개입" 결정과 모순 — 기대치 갱신 필요 |
| `test_trace_log.py` | Perfetto 트레이스 로거 (JSON 유효성·크래시 내성·멀티스레드·타이머/FlowEngine 통합) |

## UI (Qt — 장비 불필요)

| 파일 | 검증 대상 |
|------|----------|
| `test_ui_sequence_improvements.py` | 시퀀스 탭 UI 개선 회귀 |
| `test_steps_grid_sync.py` | 스텝↔그리드 동기화 |
| `test_reagent_grid_adapter.py` | 시약 그리드 어댑터 |
| `verify_diagram_parts.py` | 배관도 v3 하드웨어 조합 렌더 (출력 폴더는 `OUTDIR` 환경변수, 기본 루트) |
| `verify_manual_grouping.py` | Manual 탭 모듈러 그리드 26항목 |
| `verify_mfc_integration.py` | MFC 통합 (SystemConfig 로드) |
| `test_phase_dashboard.py` | 대시보드 위상 진행 표시 (main import) |
| `test_hw_scenarios.py` ⚠ | 하드웨어 구성 시나리오 — **hardware_config.json을 복사·변조 후 복원**함. 중단시키지 말 것 |

## 실기 (장비 연결 필요)

| 파일 | 장비 |
|------|------|
| `test_3way_valve.py` | 3way 밸브 릴레이 |
| `test_relay.py` | 릴레이 보드 |
| `test_esp32_valve_live.py` | ESP32 밸브 실기 |
| `test_ultrasonic_live.py` | 초음파 레벨센서 |
| `test_phase_sensor_hw.py` | 위상센서 리그 (robochem 에뮬레이터 경로 사용) |
| `verify_plate_orientation.py` ⚠ | **데크가 실제로 움직임** (tools/calibrate_deck_v13의 Marlin 사용) |
| `verify_reservoir.py` ⚠ | **데크가 실제로 움직임** |

## fixtures/

- `2026-04-30.json` — 실측 저장 메서드(v8.1, 100/25°C 혼합온도) — `test_pre_run_gates.py` 전용 픽스처. 삭제 금지.
