# robochem_devices — Robochem_Flex 디바이스 계층 절제 이식

원본: https://github.com/Noel-Research-Group/Robochem_Flex (Apache-2.0)
추출일: 2026-07-03 / 외부 의존성: `pip install pyserial bidict pause`

## 구성

```
robochem_devices/
  device.py           BaseDevice, DeviceParameter (dict 스타일 파라미터, stop Event)
  device_arduino.py   S/R 라인 프로토콜, 블로킹 ack, 동적 타임아웃, stop 주입
  array.py / array_arduino.py   아두이노 1대 = 논리 디바이스 N개 프록시 패턴
  syringe_pump.py     NRG 시린지펌프 (변수맵 1~23, ack k/v/a)
  sampler.py          GRBL 0.9j 샘플러 (safe_z 인터록, G4 동기화, aux needle M8/M9)
  gpio.py             GpioArray + SolenoidValve(N2) + Fan
  serial_id.py        known_devices.json 포트 자동탐색 (USB SN/VID/PID + custom ID)
  snapshot.py         디바이스 상태 스냅샷
  base_unit_task.py   태스크 규약 (validate→execute→cleanup)
  tasks_pumps.py      PrimePump / FillPump / PumpVolume(리필루프) / MixSlug
  task_errors.py      태스크 예외
  logger.py           ★재작성: slack/colorama 제거, Qt 콜백 훅 (Logger.add_callback)
  general.py          ★발췌: parse_with_units, run_all, format_float 등 (numpy 제거)
  optional/           ★비활성 이식 — 기본 import에 미포함, 의존성 설치+직접 import로 활성화
    phase_sensor.py       OCB350 위상센서 어레이 (의존성 추가 없음, 하드웨어만 연결하면 됨)
    task_monitoring.py    센서 폴링 태스크 (의존성 추가 없음)
    task_phase_sensors.py 슬러그 추적 태스크 (활성화 시 numpy+pandas 필요)
    mass_flow_controller.py  Bronkhorst MFC — 가스화학 확장용 (활성화 시 bronkhorst-propar 필요)
examples/
  smoke_pump.py             펌프 브링업 (제로잉→펌핑→비동기정지→리필루프)
  smoke_sampler_gpio.py     샘플러+N2 브링업 (호밍→이동→safe_z 인터록 검증→밸브)
  qt_bridge.py              PyQt5 패턴 (로그 브릿지, 커맨드 큐 워커, 정지 배선)
firmware/
  syringe_pump_nrg/   UNO 펌프 펌웨어 (Timer1 ISR 스텝, 엔코더 블로킹 감지)
  auxiliary_axis/     보조니들 서보 (FT3325P 교체 시 POSITION_UP/DOWN/DELAY 상수 조정)
  GPIO_Array/         범용 GPIO 컨트롤러 (N2 솔레노이드)
```

## MODIFICATIONS (Apache-2.0 §4 변경 고지)

1. 패키지 평탄화 + 상대 import 치환 (`omniplatypus.*` → `robochem_devices.*`)
2. `logger.py` 전면 재작성 — slack_sdk/colorama 제거, 인터페이스(`Logger.log_message`) 유지, 콜백 등록 추가
3. `general.py` 발췌 재작성 — numpy/pandas/ast 의존 제거, config 경로를 `ROBOCHEM_CONFIG_DIR` 환경변수화
4. `sampler.py` 버그 수정 — `position_max` 프로퍼티가 `_position_min`을 리턴하던 copy-paste 오류
5. `unit_tasks/errors.py` → `task_errors.py` 파일명 변경 (devices/errors.py와 충돌 회피)
6. optional/ 서브패키지 신설 — phase_sensor, sensing 태스크 2종, MFC를 비활성 이식 (기본 경로 미로드)

## 무하드웨어 통합검증 (완료)

`examples/emulators.py`(가상 펌프펌웨어/GRBL/GPIO) + `examples/integration_test.py`로
전 계층 검증 완료 (2026-07-03) — 통합 **28/28**, 에러경로 **17/17**, 펌프계약 **14/14**, optional 활성화 **6/6** (optional_activation_test.py):
라이프사이클, 정량 부기 일치, 리필루프 밸브 시맨틱, 비동기 정지 후 통신 정합성,
G4 동기화 블로킹, safe_z/aux 인터록(송신 전 차단), N2 페일세이프,
샘플링→주입→[건조∥리필] 병렬 시나리오, 8스레드 경합 무오염.
재실행: `cd examples && python integration_test.py && python error_test.py && python compat_contract_test.py && python optional_activation_test.py`
기존 프로그램 호환 검증 절차는 `compat_plan.md` 참조 (어댑터: examples/compat_adapter.py).

검증에서 확인된 문서화 안 된 동작 2개:
- FillPump는 제로잉 후 시린지 용량 2%를 reservoir로 되밀어냄 (백래시 보정) → 리필 직후 가용량 = 98%
- FillPump는 종료 시 밸브를 시스템(ON)으로 복귀시킴

## 브링업 순서

1. `smoke_pump.py` — 프로토콜/ack/stop 패턴 최소 검증체. steps_per_ml은 실측 캘리브레이션 필수
2. `smoke_sampler_gpio.py` — GRBL은 반드시 **0.9j** ($22 호밍 활성). 1.1 플래시 시 GrblStatus regex 수정 필요
3. `qt_bridge.py` 패턴으로 기존 PyQt5 앱에 결합 — 모든 명령은 DeviceWorker.submit() 경유

## 주의 (기존 분석 리포트 §6.4 요약)

- 밸브 배관 규약: C=시린지, ON=시스템, OFF=reservoir (리필 로직 하드코딩)
- initialize()는 실제 구동 발생 (펌프 제로잉, 샘플러 호밍) — GUI에서 연결/초기화 버튼 분리
- stop 이벤트는 set 후 반드시 명시적 clear (`stop_parameter(name, stop=False)`)
- 아두이노 open 시 auto-reset 2초 대기 내장, 9600 baud 고정
- CH340 클론보드는 USB 시리얼넘버 없음 → 펌웨어 custom ID(S1=이름, S14=저장) 필수
