# VORONOI Platform

흐름 자동화 시스템. PyQt5 GUI로 시린지펌프, 밸브, 히터, 분획수집기를 제어하고 실험 시퀀스를 실행한다.

**문서 지도**: `README.md`(실행법·폴더 지도·데이터 파일) · `docs/ARCHITECTURE.md`(모듈 상세·실측 의존
방향·app 결합면·이름충돌·미해결 진실) · `tests/README.md`·`tools/README.md`(스크립트 인벤토리) ·
`docs/`(배선메모 3종·캘리브레이션 백로그·나중계획).

**폴더 규약 (2026-08-11 재배치)**: 검증 스크립트는 `tests/`, 캘리브레이션·진단·유틸은 `tools/`,
실측/절차 문서는 `docs/`. 모든 스크립트는 **루트에서 실행** (`py -3.14 tests\xxx.py`) —
`engine/config.py`가 `hardware_config.json`을 CWD 상대로 읽는다. tests/tools 스크립트 상단에는
프로젝트 루트를 sys.path에 넣는 부트스트랩이 있으며 새 스크립트도 같은 규약을 따른다.

---

## 아키텍처 (4계층)

```
UI (PyQt5)          ← 사용자 조작, 실시간 모니터링
  ↕ Signal/Slot
Core                ← 하드웨어 초기화, 스레딩, 메서드 I/O
  ↕
Engine              ← 시퀀스 실행, 유량 계산, 안전 관리
  ↕
Hardware            ← 장비 드라이버 (시리얼/RS-485/MODBUS)
```

**데이터 흐름**: `hardware_config.json → SystemConfig → HardwareManager → Engine → UI Signal`

---

## 파일 맵

### 진입점
| 파일 | 역할 |
|------|------|
| `main.py` | AutoPairingGUI (QMainWindow). Config→HW→UI→모니터링 순서로 초기화 |
| `run.bat` | `py -3.14 main.py` (Python 3.14 고정 — PATH의 `python`은 3.10) |
| `hardware_config.json` | 장비 인벤토리, 역할 매핑, 시스템 파라미터 (single source of truth) |
| `tubing_measurements.json` | 튜빙 실측 원장 (손 편집) → `tools/apply_tubing_measurements.py --apply`로 config 반영 |
| `stock_recipes.json` | 다성분 stock 레시피 (시퀀스 탭이 읽고 씀, 스키마 소비자는 engine/stock_stoich) |

### engine/ — 실행 엔진
| 파일 | 클래스 | 역할 |
|------|--------|------|
| `config.py` | `SystemConfig` | JSON 로드, 런타임 파생값 계산 (reactor_vol, dead_vol 등) |
| `strict_engine.py` | `StrictSequenceEngine` | **핵심 실행 루프**: 가열→세척→프리필→주입→수송→수집 |
| `flow_engine.py` | `FlowEngine` | 부모 클래스. CSV 로깅, 백그라운드 안전 모니터, Perfetto 트레이스(`self.trace`) 개시 |
| `trace_log.py` | `TraceLogger` | Chrome Trace Event 로거 — `logs/TRACE_*.json` → ui.perfetto.dev 타임라인 (docs/TRACING.md). 예외 완전 삼킴=엔진 무영향, `FLOWCHEM_TRACE=0` 비활성 |
| `calculators.py` | `FlowCalculator` | 양론비 → 펌프 유량 변환 |
| `safety_manager.py` | `SafetyManager` | 온도/압력 감시, SafetyError 발생 |
| `valve_timeline.py` | | 밸브 타이밍 |
| ~~`sequence_timeline.py`·`simpy_engine.py`~~ | | **아카이브됨(2026-08-13)** — 소비자 0건의 엔진 복제 이중 로직(구 워크플로) → `_archive_20260805/legacy_sim_20260813/`. 시뮬은 test_detailed_timing 체인만 유지(⚠구 워크플로 배너 있음) |
| `stock_stoich.py` | `compute_stock` | 다성분 stock 양론 순수 엔진 (UI/HW 무의존, limiting 앵커) |
| `sampler_coordinator.py` | `SamplerCoordinator` | 오토샘플러 니들↔펌프 조율 (RoboChem Gen2 이식) |
| `simulation_tool.py` | | `python -m engine.simulation_tool [--gui\|--sweep]` 시뮬 드라이버 |
| `test_detailed_timing.py` | | ⚠ **테스트 아님** — 상세 타이밍 시뮬 라이브러리(SimPy+Excel). `tools/run_simulation.py`가 import |

### core/ — 코어 로직
| 파일 | 클래스 | 역할 |
|------|--------|------|
| `hw_manager.py` | `HardwareManager` | 모든 장비 인스턴스화 + cleanup. Mock 폴백 |
| `worker.py` | `WorkerSignals`, `SequenceWorker`, `StatusWorker` | PyQt 시그널, 시퀀스 스레드, 상태 폴링(1초) |
| `method_io.py` | `MethodIO` | 실험 메서드 JSON 저장/로드 |
| `experiment_report.py` | `ExperimentReport` | 결과 JSON + PNG 튜브 레이아웃 내보내기 |
| `reagent_excel.py` | `ReagentExcelManager` | 시약 농도 엑셀 로드 |
| `utils.py` | `SystemMapManager` | USB 포트 탐색, 펌프 라우팅 맵 |
| `deep_wash.py` | `DeepWashEngine` | 12way 전 라인 세척 (포트 2–11 배치 흡인 → 12 폐기) |
| `notebook_export.py` | `NotebookExporter` | 시퀀스 → F-SCH 연구노트 JSON (⚠ `notebook_export/` 폴더와 다름 — 폴더는 P&ID CDXML 생성기) |
| `app_monitoring.py` 외 `app_*.py` 3종 | 믹스인 4종 | 옛 941줄 main.py에서 분리 — 탭이 `app.<속성>` 수백 곳으로 결합돼 있어 컴포지션 대신 믹스인 (self 네임스페이스 보존) |

### hardware/ — 장비 드라이버
| 파일 | 프로토콜 | 장비 |
|------|----------|------|
| `pumps/pump_chemyx.py` | Serial (Chemyx) | Chemyx Fusion 시린지펌프 (저수준) |
| `pumps/pump_chemyx_smart.py` | 위 래핑 | ChemyxSmartPump: 리필/세척/프라이밍 자동화 |
| `pumps/pump_vapourtec.py` | Serial | Vapourtec R-Series 연속펌프 |
| `pumps/pump_reaxus.py` | Serial | Teledyne Reaxus 펌프 |
| `pumps/pump_nrg_syringe.py` | Serial (S/R ASCII) | NRG 시린지펌프 저수준 — robochem 백엔드, 무이동 connect |
| `pumps/pump_nrg_smart.py` | 위 래핑 | **NRGSmartPump**: 시퀀스 스마트 어댑터 (internal_valve 라우팅) |
| `samplers/sampler_grbl_cartesian.py` | Serial (Grbl 0.9j) | Cartesian 샘플러 — robochem Sampler 모션 백엔드 |
| `valves/valve_runze_sv07.py` | RS-485 HEX | 12방향 셀렉터 밸브 (데이지체인) |
| `valves/valve_arduino.py` | Serial (릴레이) | 3방향 솔레노이드 밸브 (UNO 4ch, 레거시) |
| `valves/valve_esp32_eth.py` | TCP/USB-CDC ASCII | 3방향 솔레노이드 밸브 — ESP32-S3-ETH-8DI-8RO 8ch 릴레이. port가 "COM~"이면 USB 시리얼(현행), "ip:port"면 TCP. 펌웨어 `hardware/arduino/valve_relay_eth_8ch/` (재플래시 시 앱 종료 필수 — COM7 점유) |
| `heaters/heater_bath_modbus.py` | MODBUS RTU | 항온조 히터 |
| `collectors/collector_colosseum.py` | Serial (스텝모터) | 분획수집기 |
| `collectors/collector_plate96.py` | Serial (Marlin G-code) | 96-well 분취기 |
| `sensors/phase_sensor_opb.py` | Serial (CSV 스트림 115200) | **위상센서 모드 A(현행)**: OPB ADC 리그 — PC측 임계값 2상 판정 |
| `sensors/phase_sensor_array.py` | Serial (RoboChem ASCII 9600) | **위상센서 모드 B(예비)**: 캘리브 보드 3상 판정(유색/불투명 대응) — 전환 절차는 `docs/위상센서_OPB_배선메모.md` |
| `sensors/ultrasonic_level.py` | Serial (HC-SR04 4ch) | 시린지 레벨 — 시작 시 reconcile 전용 (실시간 피드백 아님) |
| `gas/mfc_korea_mkp.py` | RS-485 **ASCII+ODD parity** | MKP MFC (Modbus 아님 — 초기 플레이스홀더를 교체한 것) |
| `factory.py` | — | 한글 드라이버명 → 영문 클래스명 매핑 |
| `*/mock_*.py` | — | 모든 장비의 가상 테스트용 드라이버 |

### robochem_devices/ — 벤더 패키지 (Apache-2.0, Robochem_Flex 절제본)
NRG 펌프·GRBL 샘플러의 백엔드. 원본 무수정(LICENSE/NOTICE 동봉), 런타임 미로드
참고자료·에뮬레이터·펌웨어는 `robochem_devices/_reference/`.
의존성: pyserial + **bidict + pause**. 에뮬레이터(`_reference/examples/emulators.py`)로
하드웨어 없이 어댑터 계약 테스트 가능 (스크래치 테스트 28항목 검증 이력).

### ui/ — 사용자 인터페이스
| 파일 | 역할 |
|------|------|
| `tab_dashboard.py` | 대시보드: 메트릭, 차트, 배관도 실시간 표시 |
| `tab_sequence.py` | **StepCard 기반 시퀀스 편집기** (온도/시간/포트/당량 입력) |
| `tab_manual.py` | 개별 장비 수동 제어 |
| `tab_calculator.py` | 유량 계산기 (양론 UI, WebGrid 기반, PubChem CAS 워커) |
| `visual_diagram.py` | 배관도 v2 (SVG — `assets/` 유일 소비자) |
| `visual_diagram_parts.py` | 배관도 v3 — config 조합(채널수×라우팅×N2×BPR×분취기) 적응형 벡터 |
| `main_window_ui.py` | 사이드바+페이지 스택 조립 믹스인 (QTabWidget 아님 — 4페이지) |
| `webgrid/webgrid.py` | Tabulator를 QWebEngineView+QWebChannel로 내장 (`webgrid/vendor/` JS 번들 삭제 금지) |
| `widgets/pump_controls.py` | ComponentCard·셀렉터·니들·3way 위젯 팩토리 |
| `widgets/channel_column.py` | Manual v4 채널 게이지 카드 |
| `dialogs.py` | 하드웨어 설정 다이얼로그 (HardwareConfigDialog) |
| `dialog_plate96_manual.py` 외 `dialog_*.py` | 96웰 수동 이동 / 분획 프리뷰 / stock 레시피 / Deep Wash |
| `colors.py`, `theme.py`, `theme_manager.py`, `styles.py` | 다크/라이트 테마 (colors.py가 색 단일 진실원) |

> 정리 이력 (2026-08-05): 미사용 데드 모듈 `tab_collection.py`·`tab_setting.py`·`widget_plate96.py` 는
> `_archive_20260805/dead_ui_modules/` 로 이동 (main_window_ui 는 4탭만 로드). 캘리브레이션 실측 데이터는 `calibration_data/`.
>
> 정리 이력 (2026-08-11): 루트 스크립트 65개 → `tests/`(41)·`tools/`(24) 재배치 + 부트스트랩/경로 수리.
> 루트 실측 .md 5종 → `docs/`. 픽스처 `2026-04-30.json` → `tests/fixtures/`. 탈락 PoC(`poc_nicegui`·
> `poc_pyqt_grid`)·`_backup/`·구로그 아카이브·생성물 CDXML 삭제 (git 이력으로 복구 가능).

### ⚠ 이름 충돌 (헷갈리기 쉬움 — 상세는 docs/ARCHITECTURE.md §3)
- `notebook_export/`(폴더, P&ID→CDXML 생성기 — `piping_cdxml.py`는 core가 import하는 **프로덕션 코드**) ≠ `core/notebook_export.py`(F-SCH JSON)
- `engine/test_detailed_timing.py`는 테스트가 아니라 시뮬 라이브러리 (⚠구 워크플로 재현 — 타이밍 신뢰 금지 배너 참조)
- ~~SystemConfig 2곳~~ 해소(2026-08-13): sequence_timeline.py 아카이브로 `engine/config.py`가 유일
- `temp/`는 임시폴더가 아니라 **라이브 앱 상태**(시약 엑셀 2종) — 삭제 금지

---

## 현재 하드웨어 구성

```
[시약1-12] → 12way밸브(Runze×4, COM14 데이지체인) → 3way밸브(ESP32-S3-ETH-8DI-8RO, ★USB-C 시리얼 COM7 — 이더넷 미사용 결정 2026-07-31, ch1~4=Group A/B/C/D) → 시린지펌프(Chemyx×4, COM9 RS-485)
                                                                              ↓
                                                        합류: Solvent+A+B→QUAD-1 → (+C+D)→QUAD-2 → 가스T(N2 MFC: MKP VIC/CAF-K, COM15, FS 10 sccm) → 센서1(INLET, OPB ch0)   ← 2026-08-12 재구성, tjunction_entry_map
                                                                              ↓
                                                        광반응기 코일 — 🔴2026-08-17 정정: 총 2.7 mL = 조사 2.4 + 암부 앞/뒤 0.15×2.
                                                          유속 산출(반응시간=조사 체류) = reactor_vol_illuminated 2.4 / 수송 t_head = 총 2.7 (역할 분리, calculators·config 참조)
                                                          예비 반응기 2.6 mL(총부피 기준인지 교체 시 재확인)
                                                                              ↓
                                                        항온조 히터 (COM5, MODBUS)
                                                                              ↓
                                                        반응기→센서2(OUTLET, OPB ch1)→아웃렛 (411 mm = 206.6 µL 실측) → 3way 아웃렛밸브 (ESP32 ch5, ⚠SW 배선반전 invert 중 — docs/아웃렛_배선반전_주의.md) → 분획수집기 (COM11, Plate96)

★OPB 위상센서 (COM18, 115200): 센서1=INLET=A0(thr 440) / 센서2=OUTLET=A1(thr 717) — 2026-08-17 실측 확정, docs/위상센서_OPB_배선메모.md 확정블록 참조.
  펌웨어=라벨포맷(`S1:adc,판정 | S2:...`) — 드라이버 양포맷 수용. ⚠튜브빠짐=액체 오판(fail-unsafe) — N2Precal 원점검사가 유일한 자동검출.
```

**펌프 그룹**: Group A / B / C / D — 각각 (시린지모터 + 12way셀렉터 + 3way스위처) 세트
**배관 실측 원장**: `tubing_measurements.json` (구간별 부피·이력) → `tools/apply_tubing_measurements.py --apply` 로 config 반영 · 정합 검증 `tests/verify_timing_deadvol_consistency.py`

---

## 실행 시퀀스 (strict_engine.py)

```
1. 글로벌 호밍 (모든 밸브 → 1번 포트) + 분취기 호밍(병렬)
1.8 프리캘 체인 (스텝1, 병행 스레드) = PushLinePrime(push 라인 용매충전)
    → N2Precal(N2 배기 → calibrate() 훅 → 센서 공기원점 캡처·검증)   ← 2026-08-17
1.9 소스라인 기포 퍼지 (포트당 1회, (inlet+selector)×factor → 12way 폐기) ← gas 브랜치 이식
2. 가열 대기 (temp_tolerance 이내, timeout 900초)
   (구 '초기 리필' 은 2026-08-13 삭제 — git 이력 참조)
4. 시스템 세척 (wash_mode에 따라) — 퍼지 잔류 시약도 여기서 헹굼
5. 스마트 프리필 (Phase-0 분기 정량 / Prime-P1 본류 충전(스텝1) / 시약 장전(매 스텝))
6. 시약 주입 (allow_refill=False ← 희석 방지) + HeadArrivalProbe(observe/anchor, 기본 off)
7. HPLC push (병행 시린지 세척) — 레거시 경로는 용매 푸시
8. 수송 딜레이 → 분획 수집 (CollectionTimer, t_head = 총 2.7 기반)
10. 후처리 대기
```

**모드 옵션** (wash_mode / prefill_mode): `"off"`, `"first_step"`, `"port_change"`, `"every_step"`

---

## 라우팅 모드 (2026-07 — 소스 선택 방식의 1급 개념)

`roles.pumps[].drivers` 슬롯 조합에서 config 가 `PUMP_ROUTING[그룹]` 을 유도한다:

| 모드 | drivers 조합 | 소스 선택 | 엔진 동작 차이 |
|------|-------------|----------|--------------|
| `external_valve` | motor(Chemyx 등) + selector + switcher | 외부 12-way 밸브 전환 | 기존 전체 흐름 |
| `internal_valve` | motor(NRG)만 | NRG 내장 2-way (1펌프=1소스) | initial refill·prefill Phase-0 prime **스킵** (소스액 폐기 방지) |
| `autosampler` | motor(NRG) + **sampler** 슬롯 | 니들 이동 (예약) | 조율 태스크 미구현 — internal_valve 폴백 |

**조합 제약 (3계층 강제)**: NRG motor + 외부 selector/switcher 금지, 인벤토리 `main_valve_enabled=true` 필수.
다이얼로그(콤보 비활성+저장시 None) → config(경고+PUMP_VALVE_MAP 미등록) → hw_manager(미인스턴스화, 위반 시 시끄러운 MockPump 대체).

**NRG 어댑터 규약** (pump_nrg_smart.py): 부피 진실원=펌웨어 `volume`(complete 마다 current_vol 스냅) · 최초 리필 전 zero EMPTY 0점 · 리필=`PumpVolume(reverse=True)` · 밸브 위치 진실원=`parameter.last_known_value`(로컬 캐시 금지) · 유량 초과=클램프 대신 **거부**(엔진 사전검증이 SafetyError 차단) · 에러=RuntimeError 승격→Emergency Stop · connect 시 `ack_pump=ON` 강제(정지/완료감지 전제).
**NRG 라인은 압력센서 없음(get_pressure=0.0) — 과압 감시는 엔코더 stall 전파가 유일.**

---

## 핵심 설계 결정 — 변경 시 반드시 확인

| 결정 | 이유 | 위치 |
|------|------|------|
| **시간 구동 실행** (위치 센서 없음) | 시린지펌프에 위치 피드백 없음 | strict_engine.py |
| **주입 중 리필 금지** (allow_refill=False) | 시약 희석 방지 | strict_engine.py:486 |
| **RS-485 순차 트리거** (0.35초 간격) | 9600bps 버스 충돌 방지 | strict_engine.py |
| **데드볼륨 분리** (용매/시약 별도) | 세척 경로 ≠ 시약 경로 | config.py:187 |
| **시퀀스 시작 시 current_vol=0 리셋** | 위치 센서 없어 누적 오차 방지 | strict_engine.py:356 |
| **시린지 용량 사전 검증** | 시퀀스 중단보다 사전 차단이 안전 | strict_engine.py:247 |
| **MockHeater 온도 체크 스킵** | AttributeError 방지 | safety_manager.py:10 |
| **시리얼 레지스트리 싱글톤** | 핫 리로드 시 수동 cleanup 필요 | hw_manager.py:229 |
| **한글 드라이버명 → 영문 매핑** | UI 사용성 (한글) + 코드 안정성 (영문) | factory.py |
| **StepCard 통합 구조** | 3탭 → 카드 1장 = 인간공학 개선 | tab_sequence.py:7 |
| **sequence_data 직접 참조** | 복사본 아닌 원본 dict 참조 (동기화) | tab_sequence.py:12 |
| **시퀀스펌프 판별 = 덕타이핑** (`_is_smart_pump`, isinstance 금지) | NRG 등 다른 계열 스마트 어댑터 허용 | strict_engine.py 상단 |
| **브랜드 텍스트 분기 금지** | 장치 '이름'은 자유입력 — id→드라이버타입 / 정확한 클래스명 집합 / 능력으로만 분기 | dialogs.`_selected_driver_type`, pump_controls 팩토리 |
| **펌프별 max_flowrate 사전검증** | NRG 는 초과 유량을 거부(무토출)하므로 도징에서 조용한 죽은 채널이 됨 | strict_engine.`_validate_step_inputs` |
| **1-소스 라우팅은 initial refill/Phase-0 prime 스킵** | "port 1=세척액" 전제가 없어 소스액을 waste 로 버리게 됨 | strict_engine.`_pump_routing` 분기 |
| **샘플러 E-Stop = 락 우회 raw 0x18** | 이동 중 워커가 시리얼 락 점유 → 락 경유 E-Stop 은 이동 종료까지 블록 | sampler_grbl_cartesian.emergency_stop |
| **매뉴얼 탭 펌프 버튼 = 밸브 무개입** (2026-07-31 사용자 확정) | 매뉴얼 = 순수 장비 독립 제어. 방향 정렬은 운전자가 Valve Path/MOVE 로 직접. 자동 정렬 재도입 금지 — 시퀀스 엔진의 엄격 결합은 별개로 유지 | channel_column._do_infuse/_do_withdraw |

---

## 절대 규칙

### 추측 금지 — 반드시 질문
- API base URL, 엔드포인트, 인증/토큰 저장 위치
- 비즈니스 규칙 (유량/압력/온도 제한값) 숫자
- 배포/프로덕션 설정
- 유량 계산 공식 (calculators.py의 화학양론 모델)
- 밸브 모드 열거값 (4가지 고정)

### 멈추고 확인해야 하는 변경
- 상태관리 구조 변경
- 라우팅 구조 변경
- 시그널 정의 추가/변경 (UI 핸들러 연결 필요)
- 유량 계산 공식 변경 (캘리브레이션 영향)
- 스레드 안전 관련 (sequential_trigger 간격, 뮤텍스)
- 시리얼 레지스트리 cleanup 로직
- 디자인 시스템/테마 변경
- 공용 컴포넌트 Props breaking change

### 작업 순서
1. 먼저 계획 (변경 파일/범위/리스크) 제시
2. 승인 후 최소 범위로 구현
3. 중요한 변경에는 @codesyncer 태그 추가
4. 실행/테스트 방법 안내

---

## @codesyncer 태그 규칙

코드 내 중요한 결정/가정/리스크를 태그로 남긴다:

```python
# @codesyncer-decision: 설계/정책 결정 (왜 그렇게 했는지)
# @codesyncer-inference: 불확실한 가정 + 검증 방법
# @codesyncer-risk: UI 깨짐/성능/보안 리스크 + 완화책
# @codesyncer-context: 프로젝트 맥락
```

---

## 시그널 체계 (worker.py)

| 시그널 | 타입 | 용도 |
|--------|------|------|
| `sig_log` | str | 로그 메시지 |
| `sig_status` | str | 상태 ("Running", "Paused", "Stopped") |
| `sig_progress` | int | 진행률 0-100 |
| `sig_finished` | — | 시퀀스 완료 |
| `sig_error` | str | 에러 발생 |
| `sig_mon_data` | float, dict, dict, dict | (온도, 압력맵, 펌프상태, 밸브상태) |
| `sig_phase_progress` | str, float | 배관도 시각화용 (단계명, %) |

---

## 의존성 트리

```
main.py
├── engine/config.py (SystemConfig)
├── core/hw_manager.py (HardwareManager)
│   ├── hardware/factory.py
│   ├── hardware/pumps/*.py  ← NRG 계열은 robochem_devices/ (벤더)
│   ├── hardware/samplers/*.py ← robochem_devices/ (벤더)
│   ├── hardware/valves/*.py
│   ├── hardware/heaters/*.py
│   ├── hardware/collectors/*.py
│   ├── engine/strict_engine.py ← engine/flow_engine.py
│   ├── engine/calculators.py
│   └── engine/safety_manager.py
├── core/worker.py (Signals, Threads)
├── core/utils.py (USB, MapManager)
├── core/method_io.py
├── core/reagent_excel.py
└── ui/tab_*.py (각 탭)
```

---

## 기술 스택

- **Python 3.14** (`py -3.14` — PATH 의 `python` 은 3.10이므로 주의; robochem 이 `.add_note()` 3.11+ 사용) + **PyQt5**
- **pyserial** (시리얼 통신)
- **bidict + pause** (robochem_devices 벤더 의존성)
- **minimalmodbus** (MODBUS RTU)
- **SimPy** (시뮬레이션)
- **openpyxl** (엑셀 I/O)
- 로그: CSV (센서) + JSON (메서드/결과)
