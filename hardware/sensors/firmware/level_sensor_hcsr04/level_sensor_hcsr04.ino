// HC-SR04 초음파 레벨센서 펌웨어 — Arduino UNO 1대 = 펌프 1채널
//
// @codesyncer-context: RoboChem(Science 2024, SI 3.3.1) 방식 이식.
//   시린지 플런저에 붙인 3D 프린팅 '반사 블록'까지의 거리를 측정한다
//   (액면 직접 조준 X — 빔이 배럴벽에 막힘).
//
// 프로토콜 (hardware/sensors/ultrasonic_level.py 의 _read_raw 와 계약):
//   9600 bps, 거리(cm) float 을 한 줄에 하나씩 상시 출력. 예: "12.34\n"
//   에코 타임아웃(무반사/미배선) 시 "NA" 출력 — float 파싱이 안 되므로
//   드라이버는 무시하고, 유효표본 부족 시 LevelSensorError 로 시끄럽게
//   실패한다. 진단 도구는 NA 를 세어 '펌웨어 정상 + 센서 무응답'을
//   '펌웨어 없음'과 구분한다 (test_ultrasonic_live.py).
//
// 배선 (핀은 자유 — 아래 상수만 맞출 것; 2026-07-23 실기 검증 배선):
//   VCC→5V, GND→GND, TRIG→D9, ECHO→D8
//
// 캘리브레이션: 거리 단위는 아무거나 일관되면 됨. 소프트웨어 쪽
//   level_cal_slope/intercept 가 raw→mL 선형 환산을 담당
//   (test_ultrasonic_live.py --cal 로 산출).

const int PIN_TRIG = 9;
const int PIN_ECHO = 8;
const unsigned long ECHO_TIMEOUT_US = 30000UL;  // ~5 m 왕복 상한
const unsigned long CYCLE_MS = 100;             // 10 Hz (HC-SR04 권장 >= 60 ms)

void setup() {
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  digitalWrite(PIN_TRIG, LOW);
  Serial.begin(9600);
}

void loop() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  unsigned long dur = pulseIn(PIN_ECHO, HIGH, ECHO_TIMEOUT_US);
  if (dur > 0) {
    float dist_cm = dur * 0.0343f / 2.0f;
    Serial.println(dist_cm, 2);
  } else {
    Serial.println("NA");   // 에코 없음: HC-SR04 미배선/전원/반사면 문제
  }
  delay(CYCLE_MS);
}
