# VORONOI Flowchemistry Platform

PyQt5 GUI로 시린지펌프·12way/3way 밸브·항온조 히터·96-well 분획수집기를 제어하고
실험 시퀀스를 자동 실행하는 흐름화학(Flow Chemistry) 플랫폼.

> AI 작업 규칙·설계 결정·절대 규칙은 [CLAUDE.md](CLAUDE.md),
> 모듈 단위 상세 지도는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 참조.

---

## 실행 방법

| 무엇 | 명령 | 비고 |
|------|------|------|
| **본 앱 (GUI)** | `run.bat` 또는 `py -3.14 main.py` | Python **3.14 고정** (`python`은 3.10이라 사용 금지 — robochem이 3.11+ 문법 사용) |
| 오프라인 타이밍 시뮬레이션 | `py -3.14 tools\run_simulation.py` | 하드웨어 불필요. 결과 Excel은 `temp/`에 생성 |
| 검증 테스트 | `py -3.14 tests\test_xxx.py` | 인벤토리: [tests/README.md](tests/README.md) |
| 캘리브레이션/진단 도구 | `py -3.14 tools\xxx.py` | 인벤토리: [tools/README.md](tools/README.md) |
| Plate96 티칭 GUI | `calibrate_gui.bat` | 내부적으로 `tools\calibrate_plate96_gui.py` 실행 |

**⚠ 모든 스크립트는 프로젝트 루트에서 실행** (CWD=루트).
`engine/config.py`가 `hardware_config.json`을 CWD 상대 경로로 읽기 때문.
tests/·tools/ 스크립트는 자체 부트스트랩(`sys.path`에 루트 삽입)이 있어 import는 어디서든 되지만,
설정 파일 접근 때문에 루트 실행이 표준이다.

---

## 폴더 지도

| 폴더 | 내용 | 성격 |
|------|------|------|
| `main.py` | 앱 진입점 (AutoPairingGUI) | 코드 |
| `core/` | 하드웨어 초기화·스레딩·메서드 I/O·리포트·앱 믹스인 | 코드 |
| `engine/` | 시퀀스 실행 엔진·유량 계산·안전 관리·시뮬레이션 | 코드 |
| `hardware/` | 장비 드라이버 (pumps/valves/collectors/sensors/samplers/heaters/gas) + 아두이노 펌웨어 | 코드 |
| `ui/` | PyQt5 화면 (4탭: Dashboard/Calculator/Sequence/Manual) | 코드 |
| `robochem_devices/` | **벤더 패키지** (Apache-2.0, Robochem_Flex 절제본) — NRG 펌프·GRBL 샘플러 백엔드. 수정 금지 | 벤더 |
| `tests/` | 검증 스크립트 (test_*, verify_*) + `fixtures/` | 검증 |
| `tools/` | 캘리브레이션·진단·장비 프로브·시뮬레이션 도구 | 도구 |
| `docs/` | 배선 메모·캘리브레이션 백로그·아키텍처 문서 | 문서 |
| `notebook_export/` | P&ID→ChemDraw CDXML 생성기 (`piping_cdxml.py`는 **프로덕션 코드** — core가 import) | 코드+산출물 |
| `assets/` | 배관도 v2용 SVG (visual_diagram.py만 사용) | 리소스 |
| `calibration_data/` | **실측 캘리브레이션 데이터** (CH0 펌프·레벨센서·데크 좌표) — 삭제 금지 | 데이터 |
| `logs/` | 런 로그 CSV + 리포트 JSON (자동 생성, gitignore) | 산출물 |
| `results/` | 실험 기록 `날짜/EXP_nnn/` + `experiment_log.csv`(라이브 누적) — 삭제 금지 | 데이터 |
| `temp/` | **⚠ 이름과 달리 라이브 앱 상태** — 시약 농도 엑셀(`reagent_settings.xlsx`, `calc_reagents.xlsx`). gitignore라 지우면 복구 불가 | 데이터 |
| `_archive_20260805/` | 데드 코드 격리 (복원 절차는 안의 README) | 보관 |

## 설정 파일 (루트)

| 파일 | 역할 | 쓰는 주체 |
|------|------|----------|
| `hardware_config.json` | 장비 인벤토리·역할 매핑·시스템 파라미터 — **단일 진실원** | 앱의 하드웨어 설정 다이얼로그 (또는 `tools/apply_tubing_measurements.py --apply`) |
| `tubing_measurements.json` | 튜빙 실측 원장 (측정값 기록 → apply 도구로 config에 반영) | 손으로 편집 |
| `stock_recipes.json` | 다성분 stock 레시피 프리셋/할당 | 시퀀스 탭 |

## 문서 인덱스 (docs/)

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 모듈 지도·의존 방향·결합면·주의사항 (AI/신규 참여자용)
- [CALIBRATION_실측_백로그.md](docs/CALIBRATION_실측_백로그.md) — 소프트웨어로 못 고치는 실측 항목 우선순위
- [나중계획.md](docs/나중계획.md) — 보류 작업 4건 (실행 문구로 호출, 완료 시 삭제)
- [Chemyx_RS485_핀아웃_배선메모.md](docs/Chemyx_RS485_핀아웃_배선메모.md) — DB9 핀아웃·버스 함정 (실측 검증됨)
- [위상센서_OPB_배선메모.md](docs/위상센서_OPB_배선메모.md) — 위상센서 모드 A(현행)↔B(예비) 전환 절차
- [초음파레벨센서_배선메모.md](docs/초음파레벨센서_배선메모.md) — 4채널 핀 맵 (⚠ 배선 역전 이슈 기록)
