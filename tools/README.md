# tools/ — 캘리브레이션·진단·유틸리티

**실행: 프로젝트 루트에서** `py -3.14 tools\xxx.py`.
각 파일 상단 부트스트랩이 프로젝트 루트를 sys.path에 넣는다. 대부분 실제 장비가 필요하다.
실측 절차의 우선순위·체크리스트는 [../docs/CALIBRATION_실측_백로그.md](../docs/CALIBRATION_실측_백로그.md),
보류 작업 실행 문구는 [../docs/나중계획.md](../docs/나중계획.md) 참조.

## 캘리브레이션

| 파일 | 대상 | 비고 |
|------|------|------|
| `calibrate_deck_v13.py` | 분취 데크 5점 티칭 (Marlin) | 결과는 `hardware/collectors/data/well_coordinates.json` |
| `calibrate_plate96.py` / `calibrate_plate96_gui.py` | Plate96 조그 티칭 (CLI/GUI) | GUI는 루트의 `calibrate_gui.bat`로 실행 |
| `calibrate_ch0.py` / `calibrate_ch0_3x.py` | CH0 펌프 유량 캘리브레이션 | CSV를 루트에 출력 → 확정본은 `calibration_data/`로 |
| `calibrate_level_median_compare.py` | 레벨센서 raw vs median 비교 | 출력(csv/png/txt)은 루트에 생성 |
| `calibrate_collector.py` + `continuous_rotation_test.py` | 구형 분취기 회전 캘리브 | 둘이 `collector_cmd.txt`/`collector_status.txt` 파일 IPC 짝 |

## 진단·프로브 (실기)

| 파일 | 대상 |
|------|------|
| `diagnose_mapping.py` | hardware_config vs 실제 COM 포트 매칭 진단 (앱과 같은 매칭 로직) |
| `diagnose_g28.py` | Marlin 호밍(G28) 진단 |
| `pump_scan.py` / `pump_test.py` / `pump_watch.py` | 펌프 버스 스캔 / 단독 테스트 / 상태 감시 |
| `scan_valve.py` / `set_valve_address.py` | Runze 12way 스캔 / RS-485 주소 설정 |
| `relay_cycle.py` | 릴레이 사이클 테스트 |
| `check_level4ch.py` / `live_level_stream.py` | 레벨센서 4채널 점검 / 실시간 스트림 |
| `level_cal_helper.py` | 레벨 캘리브레이션 도우미 |
| `sweep_profile.py` | 유량 스윕 프로파일 (CSV) |

## 생성·반영 (config를 바꾸는 도구)

| 파일 | 역할 |
|------|------|
| `generate_rack_coords.py` | 랙 좌표 JSON 생성 (기존 티칭 재사용, `--rack` 필수) → `hardware/collectors/data/` |
| `apply_tubing_measurements.py` | `tubing_measurements.json` 원장 → `hardware_config.json` 반영. 기본 dry-run, `--apply` 시 `_backup/`에 자동 백업 |

## 시뮬레이션·데모 (장비 불필요)

| 파일 | 역할 |
|------|------|
| `run_simulation.py` | 상세 타이밍 시뮬 (파라미터는 파일 상단 편집) → `temp/`에 Excel 생성 후 자동 열림 |
| `demo_notebook_export.py` | F-SCH 연구노트 export 데모 |
