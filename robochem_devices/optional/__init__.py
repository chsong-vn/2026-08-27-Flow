"""
robochem_devices.optional — 이식은 완료됐지만 기본 비활성인 모듈들.

이 서브패키지는 robochem_devices의 기본 import 경로에 포함되지 않는다.
(`import robochem_devices` 시 여기 모듈은 로드되지 않음 → 추가 의존성 강제 없음)

모듈 / 활성화 조건:

  phase_sensor          OCB350 위상센서 어레이 (PhaseSensorArray, PhaseSensor 프록시).
                        추가 의존성 없음. 하드웨어(Phase Sensor Controller 보드) 연결 시:
                            from robochem_devices.optional.phase_sensor import PhaseSensorArray

  task_monitoring       센서 폴링/모니터링 유닛태스크. 추가 의존성 없음.

  task_phase_sensors    슬러그 추적 태스크 (WaitForPhaseChange, OpenValveUntilPhaseChange,
                        PumpUntilPhaseChange, MonitorPhase, SlugQuality).
                        ※ 활성화 시 numpy + pandas 필요:  pip install numpy pandas

  mass_flow_controller  Bronkhorst MFC (가스 정량 공급 — 가스화학 확장용).
                        ※ 활성화 시 propar 필요:  pip install bronkhorst-propar

의도적으로 여기서 아무것도 import하지 않는다. 활성화 = 위 의존성 설치 후
필요한 모듈을 직접 import하는 것. 코드 수정 불필요.
"""
