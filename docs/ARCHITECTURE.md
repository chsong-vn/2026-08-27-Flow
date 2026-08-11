# 아키텍처 지도

> 2026-08-11 전수 조사 기준. 실행 규칙·설계 결정·절대 규칙은 [../CLAUDE.md](../CLAUDE.md) 참조.
> 이 문서는 "어느 파일에 무엇이 있고, 실제로 무엇이 무엇을 부르는가"를 다룬다.

## 1. 계층과 실제 결합면

명목상 4계층(UI ↔ Core ↔ Engine ↔ Hardware)이지만, **실제 결합면은 `app` 객체다.**
`ui/` 모듈이 engine/core를 직접 import하는 경우는 거의 없고(모듈 수준 4건 + 함수 내 지연 6건),
대신 모든 탭이 공유 `self.app`(= `AutoPairingGUI`)의 속성을 통해 엔진에 닿는다:

```
app.engine      # StrictSequenceEngine        app.cfg        # SystemConfig
app.worker      # SequenceWorker(QThread)     app.signals    # WorkerSignals
app.map_mgr     # SystemMapManager            app.calculator # FlowCalculator
app.hw_mgr      # HardwareManager             app.excel_mgr  # ReagentExcelManager
app.calc_tab / app.reagent_tables / app.log_browser  # 탭 간 상호 참조
```

`core/app_*.py` 4개(모니터링·런제어·핫리로드·리모트)는 옛 941줄 main.py에서 잘라낸 **믹스인**이다.
컴포지션이 아닌 믹스인인 이유: 탭·매니저가 `app.<속성>` 수백 곳으로 결합돼 있어 `self` 네임스페이스를
보존해야 무변경 분리가 가능했기 때문 (각 파일 도크스트링에 동일 설명 있음).

### 실측 의존 방향

```
main.py ─▶ engine.config, core.*, ui.{main_window_ui, colors, theme_manager}
core/   ─▶ hardware/* (hw_manager가 드라이버 ~20개 직접 import)
        ─▶ engine/  (strict_engine, safety_manager, calculators, config, stock_stoich)
        ─▶ ui/      ⚠ 역방향 edge: core/app_*.py → ui.colors, ui.dialogs
engine/ ─▶ hardware/ (strict_engine → pump_chemyx_smart; config → factory)
        ─▶ core.utils (1건)
hardware/ ─▶ robochem_devices/ (NRG 펌프 2, GRBL 샘플러, 위상센서 array, 총 5건)
robochem_devices/ ─▶ (내부 참조만 — 앱 코드를 전혀 모름. 깨끗한 벤더 경계 ✅)
```

런타임 배선: `hw_manager.init_hw()`가 SafetyManager → StrictSequenceEngine → FlowCalculator를
생성해 매달고, `main._sync_hw_refs()`가 app에 복사. `SequenceWorker`가 QThread에서
`engine.run_sequence(plan, map_mgr)` 호출, SafetyError를 시그널로 변환.

### 앱 시작 순서 (main.py)

① `__pycache__` 전체 삭제 + `dont_write_bytecode` (3.14+debugpy 스테일 pyc 방지)
② `AA_ShareOpenGLContexts` — QApplication 생성 **전** 필수 (ui/webgrid의 QtWebEngine 때문)
③ 테마 → ④ `SystemConfig`(hardware_config.json) → ⑤ 시그널·엑셀·MethodIO
⑥ 그래프 버퍼(`dh`, `dh_phase` — 위상센서는 늦게 붙어 트림 루프 desync 방지로 분리)
⑦ `HardwareManager.init_hw()`(mock 폴백) → ⑧ SystemMapManager → ⑨ `init_ui()`
⑩ 시그널 배선 → ⑪ StatusWorker(1초 폴링) → ⑫ remote_cmd.txt 폴링(500ms) → ⑬ showMaximized

UI는 QTabWidget이 아니라 **사이드바 + 페이지 스택** 4개: Dashboard / Calculator / Sequence / Manual.

## 2. 모듈 지도

🚩 = 1500줄 이상 대형 파일 (수정 시 특히 신중)

### core/ — 오케스트레이션·스레딩·I/O
| 파일 | 줄수 | 역할 |
|------|-----:|------|
| `hw_manager.py` | 645 | 모든 드라이버 인스턴스화(mock 폴백), 엔진/안전/계산기 조립, cleanup |
| `worker.py` | 110 | WorkerSignals(전 시그널), SequenceWorker(시퀀스 QThread), StatusWorker(1초 폴링) |
| `method_io.py` | 476 | 실험 메서드 JSON 저장/로드 (hardware_config 스냅샷 내장) |
| `experiment_report.py` | 458 | `results/날짜/EXP_nnn/` 리포트 JSON+튜브 PNG + `experiment_log.csv` 누적 |
| `reagent_excel.py` | 457 | 시약 엑셀 왕복 (`temp/` 파일 감시 자동 반영) |
| `notebook_export.py` | 519 | 시퀀스 → F-SCH 연구노트 JSON 변환 (스텝당 1건) |
| `deep_wash.py` | 315 | 12way 전 라인 세척 루틴 (포트 2–11 흡인 → 12 폐기) |
| `utils.py` | 186 | USB 포트 프로브(probe_chemyx/runze/reaxus — CH340 VID:PID 충돌 구분), SystemMapManager |
| `app_monitoring.py` `app_control.py` `app_hot_reload.py` `app_remote.py` | ~150씩 | main.py에서 분리한 믹스인 4종 (§1 참조) |

### engine/ — 실행 + 시뮬레이션
| 파일 | 줄수 | 역할 |
|------|-----:|------|
| `strict_engine.py` | 🚩3610 | **핵심 실행 루프** StrictSequenceEngine + CollectionTimer + _HteSensorSync + HTE 프로파일. 펌프 판별은 덕타이핑(`_is_smart_pump`) |
| `test_detailed_timing.py` | 🚩1831 | ⚠ **테스트 아님** — 상세 타이밍 시뮬레이션 라이브러리(SimPy+Excel). `tools/run_simulation.py`가 import |
| `simpy_engine.py` | 1186 | 이산사건 대체 엔진 (realtime on/off) |
| `sequence_timeline.py` | 962 | 전체 시퀀스 타임라인 빌더 |
| `simulation_tool.py` | 693 | `python -m engine.simulation_tool [--gui|--sweep]` CLI/GUI 시뮬 드라이버 |
| `valve_timeline.py` | 405 | 밸브 전환 타임라인 (타입별 전환시간 모델) |
| `config.py` | 364 | SystemConfig — hardware_config.json 로드 + ACTIVE_PUMPS/PUMP_ROUTING 등 파생. ⚠ 경로가 CWD 상대 → 루트 실행 전제 |
| `stock_stoich.py` | 191 | 다성분 stock 순수 계산 엔진 (UI/HW 무의존, limiting 앵커) |
| `flow_engine.py` | 157 | 부모 클래스 — CSV 로깅(`logs/`), 백그라운드 안전 모니터 |
| `sampler_coordinator.py` | 143 | 오토샘플러 니들↔펌프 조율 (RoboChem Gen2 이식) |
| `calculators.py` | ~110 | 양론 → 펌프별 유량 (conc=0 용매 허용) |
| `safety_manager.py` | ~60 | 온도/압력 감시, SafetyError (MockHeater는 온도 체크 스킵) |

### hardware/ — 드라이버 (자세한 프로토콜 표는 CLAUDE.md)
- `pumps/` — chemyx(저수준 RS-485 데이지체인·시리얼 레지스트리 싱글톤) / chemyx_smart(리필·세척·프라임 자동화 — 스마트펌프 계약의 기준) / nrg_syringe·nrg_smart(robochem 백엔드) / vapourtec / reaxus / mock류
- `valves/` — runze_sv07(12way RS-485 HEX) / esp32_eth(8ch 릴레이, COM↔`ip:port` 겸용) / arduino(레거시 4ch) / mock
- `collectors/` — plate96(Marlin G-code 250000baud, 스네이크 1–192) / colosseum / mock + `data/well_coordinates*.json`
- `sensors/` — phase_sensor_opb(**모드 A 현행**: PC측 임계값 2상) / phase_sensor_array(**모드 B 예비**: 보드측 3상, 유색/불투명 대응) / ultrasonic_level(시린지 레벨, 시작 시 reconcile 전용 — 실시간 피드백 아님) + `firmware/`
- `samplers/` — grbl_cartesian(E-Stop은 락 우회 raw 0x18) + `data/vial_positions.json`
- `heaters/` — bath_modbus / mock
- `gas/` — mfc_korea_mkp(MKP MFC — **ASCII+ODD parity, Modbus 아님**)
- `factory.py` — 한글 드라이버 라벨 → 영문 클래스 매핑
- `arduino/` — 밸브 릴레이·모터 펌웨어 .ino + build_upload.bat

### ui/ — PyQt5 (4페이지: Dashboard / Calculator / Sequence / Manual)
| 파일 | 줄수 | 역할 |
|------|-----:|------|
| `tab_sequence.py` | 🚩2869 | StepCard 시퀀스 편집기 + 시약그리드 어댑터 + 플레이트 프리뷰 (sequence_data **원본 참조**) |
| `tab_calculator.py` | 🚩2842 | 양론 계산기 (PubChem CAS 워커, WebGrid 기반) |
| `dialogs.py` | 🚩2373 | HardwareConfigDialog — 하드웨어 인벤토리/역할 편집기 전체 |
| `widgets/pump_controls.py` | 🚩2366 | ComponentCard·셀렉터·니들·3way 위젯 팩토리 |
| `tab_manual.py` | 🚩2066 | 수동 제어 3존 (Feed/Reactor/Outlet) + WashOpsBand |
| `visual_diagram.py` | 🚩1699 | 배관도 v2 (assets/*.svg 유일 소비자) |
| `visual_diagram_parts.py` | 1492 | 배관도 v3 — config 조합(채널수×라우팅×N2×BPR×분취기) 적응형 벡터 |
| `tab_dashboard.py` | 857 | 대시보드 (메트릭·차트·라이브 배관도) |
| `main_window_ui.py` | 472 | 사이드바+페이지 스택 조립 믹스인 |
| `webgrid/webgrid.py` | 208 | Tabulator를 QWebEngineView+QWebChannel로 내장 (vendor JS 오프라인 번들 — **삭제 금지**) |
| `widgets/channel_column.py` | 548 | Manual v4 채널 게이지 카드 |
| `dialog_plate96_manual.py` / `dialog_plate96_preview.py` / `dialog_stock_recipe.py` / `dialog_deep_wash.py` | 391–462 | 96웰 수동 이동 / 분획 프리뷰 / stock 레시피 / Deep Wash 옵션 |
| `colors.py` `theme.py` `theme_manager.py` `styles.py` `toggle_switch.py` | — | 다크/라이트 테마 체계 (colors.py가 단일 색 진실원) |

### robochem_devices/ — 벤더 (Apache-2.0, 수정 금지)
Robochem_Flex/OmniPlatypus(Noël group) 장치 계층 절제본. NRG 펌프·GRBL 샘플러·위상센서 array의 백엔드.
`optional/`(MFC·위상센서 태스크 — 기본 미로드), `_reference/`(에뮬레이터·계약 테스트·펌웨어·원본).
의존성: pyserial + bidict + pause.

## 3. 이름 충돌 주의 (헷갈리기 쉬운 3건)

1. **`notebook_export/`(폴더) ≠ `core/notebook_export.py`** — 폴더는 P&ID→ChemDraw CDXML 생성기
   (`piping_cdxml.py`, core가 sys.path 삽입 후 import하는 프로덕션 코드), 파일은 F-SCH 연구노트 JSON 내보내기.
2. **`engine/test_detailed_timing.py`는 테스트가 아니라 라이브러리** — pytest 도입 시 수집 제외 필요.
3. **SystemConfig가 2곳** — `engine/config.py`(진짜, hardware_config.json 로더)와
   `engine/sequence_timeline.py`(별개 클래스). 시뮬 쪽은 `SystemParams`(test_detailed_timing).

## 4. 미해결 진실 (문서 간 충돌 — 실측으로만 해소 가능)

| 항목 | 충돌 내용 | 관련 |
|------|----------|------|
| 반응기 스펙 | CLAUDE.md 3.99m×0.8mm vs config 3.82m×1.0mm (1mL/60s 불일치) | [CALIBRATION_실측_백로그.md](CALIBRATION_실측_백로그.md) P1 |
| 레벨센서 채널 순서 | 2026-07-31 물리 배선이 역전 상태 — 배선을 고치는 게 정답(설정 뒤집기 금지) | [초음파레벨센서_배선메모.md](초음파레벨센서_배선메모.md) |
| well_coordinates.json | 현재 값은 **명목 설계값(미티칭)** — 티칭 전 분주 실행 금지 | [나중계획.md](나중계획.md) §3 |

## 5. 코드 수정 없는 드라이버 스왑 스위치 2건

- **위상센서 A↔B**: 다이얼로그에서 드라이버 라벨 변경 + 보드 재플래시만 (절차: 위상센서_OPB_배선메모.md)
- **ESP32 밸브 COM↔TCP**: port 값이 `COM~`이면 USB 시리얼, `ip:port`면 TCP (현행: COM7 USB, 이더넷 미사용 결정 2026-07-31)

## 6. 설정·데이터 파일 소유 지도

| 파일 | 쓰는 코드 | 읽는 코드 |
|------|----------|----------|
| `hardware_config.json` | `engine/config.save_config()`(다이얼로그 경유), `tools/apply_tubing_measurements.py --apply` | engine/config(정본), strict_engine, method_io, sampler_grbl, visual_diagram, tools/tests 다수 |
| `tubing_measurements.json` | 손 편집 (실측 원장 — `_readme`/`_segment_guide` 내장) | `tools/apply_tubing_measurements.py`만 (원장→config 일방향 반영기) |
| `stock_recipes.json` | ui/tab_sequence | 동일 (스키마 소비자는 engine/stock_stoich) |
| `tests/fixtures/2026-04-30.json` | (과거 앱 export) | `tests/test_pre_run_gates.py`만 — 100/25°C 혼합온도 게이트 픽스처 |
| `hardware/collectors/data/well_coordinates*.json` | `tools/calibrate_deck_v13.py`·`tools/generate_rack_coords.py` | collector_plate96, tab_sequence, 캘리브 스크립트 |
| `temp/reagent_settings.xlsx`·`calc_reagents.xlsx` | core/reagent_excel, ui/tab_calculator | 동일 — **라이브 상태, 삭제 금지** |
| `logs/` | engine/flow_engine(CSV), core/experiment_report(REPORT json) | (사람) |
| `results/` | core/experiment_report | (사람) — `experiment_log.csv`는 실험 카탈로그 |

## 7. tests/·tools/ 스크립트 규약 (2026-08-11 재배치)

- 루트에 흩어져 있던 스크립트 65개를 `tests/`(41) / `tools/`(24)로 이동.
- 각 스크립트 상단에 부트스트랩이 있다:
  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` → 프로젝트 루트.
  tools의 모듈을 import하는 테스트(verify_plate_orientation, verify_reservoir, test_rack_eppendorf)는
  `_ROOT/tools`도 추가.
- **새 스크립트를 만들 때도 같은 규약을 따를 것.** 데이터 파일은 CWD 상대(루트 실행 전제) 또는
  `_ROOT` 기준 절대 경로 중 하나로 통일.
- pytest 인프라는 없다 — 전부 standalone(`PASS/FAIL` 출력 + exit code). 일부는 import 시점에
  검증이 실행되므로(가드 없음) pytest를 도입한다면 수집 규칙에 주의.
