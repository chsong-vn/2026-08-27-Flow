# 위상센서 (OPB 포토인터럽트) 배선·전환 메모 — 2026-08-05

같은 센서 하드웨어(OPB 포토인터럽트 + TT 캘리브레이션 보드)로 **두 가지 운용 모드**를
오갈 수 있다. 앱 계약(read_phase/monitor/wait_edge)이 동일해서 하드웨어 다이얼로그의
**드라이버 라벨만 바꾸면 전환**된다. 코드 수정 불필요.

---

## 모드 A — ADC 스트림 (현행, 동료 리그 = Photo_Interrupt.zip)

- **드라이버 라벨**: `위상센서 (OPB ADC 2ch)` → `hardware/sensors/phase_sensor_opb.py`
- **펌웨어**: `Photo_Interrupt/Arduino/PhotoSensor.ino` (UNO, 115200bps)
- **배선**: 센서1 아날로그 출력→A0, 센서2→A1, 캘리브 보드 VCC→5V, GND→GND
  (⚠ 아날로그 출력 와이어는 색으로 추정하지 말고 라벨 확인 — 벤더 README 경고)
- **프로토콜**: 50ms마다 `adc1,adc2\r\n` CSV 상시 스트림 (요청-응답 아님)
- **판정**: PC측 임계값. ADC > threshold = 액체(1) / 이하 = 기체(0). 2상만.
- **캘리브레이션**: 절차 없음 — 고정 임계값. 장치 settings 예:
  ```json
  "settings": {"sensors": {"collect": 0, "reactor_in": 1},
               "thresholds": {"collect": 440, "reactor_in": 717}}
  ```
- **벤더 실측표 (2026-08-05 초기값 — 튜브/정렬 변경 시 재측정)**:

  | 채널 | 튜브없음 | 공기 | 물 | threshold | 액체신호 감쇄 허용 |
  |---|---:|---:|---:|---:|---|
  | S1(A0) | 574 | 80 | 800 | 440 | 물 대비 45%까지 |
  | S2(A1) | 960 | 457 | 977 | 717 | 물 대비 27%만 — **취약** |

- **유색/진한 시약 주의**: 액체 신호가 흡수로 깎여 임계값 아래로 내려오면 기체 오판.
  → 가장 진한 반응액으로 ADC 실측 후, 부족하면 threshold 를 공기값과 그 액체값의
  중간으로 하향. 그래도 안 되면 모드 B 전환.

## 모드 B — RoboChem 3상 스택 (색/불투명 대응 내장 — 예비)

- **드라이버 라벨**: `위상센서 어레이 (OCB350)` → `hardware/sensors/phase_sensor_array.py`
- **펌웨어**: `robochem_devices/_reference/firmware/Phase_Sensor_Array/` (UNO, 9600bps)
  ※ 플래시 시 앱 종료(포트 점유). UNO 1대 = 최대 4센서.
- **배선(센서당)**: 캘리브 보드의 **디지털 A/B 출력**→UNO 디지털 핀, Cal 핀→UNO 출력
  핀, 아날로그 출력→아날로그 핀(진단용). 핀 배정은 펌웨어 Phase_Sensor_Array.ino 참조.
- **판정**: **캘리브 보드 하드웨어**가 3상 판정 — 0=ERROR/1=투명액체/2=불투명·유색
  액체/3=기체 를 A+2B 2비트로 출력 (OCB350.cpp read()). 진한 시약 = OPAQUE_LIQUID 로
  잡히므로 색 변화에 구조적으로 강건. 엔진 is_liquid 는 GAS 아니면 전부 액체.
- **캘리브레이션**: 캠페인 시작 시 **빈 튜브(기체) 상태에서 1회** — 드라이버
  `calibrate()` = 보드 Cal 핀 100ms 펄스(보드 내부 기준 재설정). 액체 상태에서
  실행 금지(판정 뒤집힘).
- settings 의 `thresholds` 키는 이 드라이버에서 무시됨(무해) — 전환 시 지울 필요 없음.

## 전환 절차 (A→B)

1. 앱 종료 → RoboChem 펌웨어 플래시 (9600bps)
2. 캘리브 보드 디지털 A/B/Cal 핀을 UNO에 배선 추가
3. 하드웨어 다이얼로그: 해당 장치 드라이버를 `위상센서 어레이 (OCB350)` 로 변경
   (settings.sensors 는 그대로 유지됨)
4. 앱 시작 → 빈 튜브 상태에서 calibrate 1회

역방향(B→A)은 PhotoSensor.ino 재플래시 + 라벨 변경만.
