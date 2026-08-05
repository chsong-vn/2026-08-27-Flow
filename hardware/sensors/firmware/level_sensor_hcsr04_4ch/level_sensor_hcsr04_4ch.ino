// HC-SR04 x4 초음파 레벨센서 펌웨어 — 아두이노 UNO 1대 = 센서 4채널(펌프 4개)
//
// @codesyncer-context: RoboChem 원본은 '펌프당 UNO 1대(단일 센서)'였으나, 실기에서는
//   UNO 1대에 HC-SR04 4개를 물렸다. 이 펌웨어는 4채널을 순차 측정해 한 줄 CSV로 낸다.
//
// 배선 (2026-07 실기):
//   센서0: TRIG=D2, ECHO=D3     센서1: TRIG=D4, ECHO=D5
//   센서2: TRIG=D6, ECHO=D7     센서3: TRIG=D8, ECHO=D9
//   전원: 외부 5V → 각 센서 VCC, 외부 GND ↔ 아두이노 GND(공통 접지 필수)
//
// 프로토콜 (hardware/sensors/ultrasonic_level.py _read_raw 와 계약):
//   9600 bps. 매 스윕마다 한 줄: "d0,d1,d2,d3\n" (cm, 소수 2자리).
//   에코 타임아웃(무반사/미배선) 채널은 "NA". 예: "12.34,NA,8.90,45.60"
//   드라이버는 채널 인덱스(0~3)로 해당 열을 골라 median 낸다.

const int N = 4;
const int PIN_TRIG[N] = {2, 4, 6, 8};
const int PIN_ECHO[N] = {3, 5, 7, 9};
const unsigned long ECHO_TIMEOUT_US = 30000UL;  // ~5 m 왕복 상한
const unsigned long CYCLE_MS = 100;             // ~10 Hz 스윕
const unsigned int  GAP_MS = 8;                 // 채널 간 크로스토크 방지 간격

void setup() {
  for (int i = 0; i < N; i++) {
    pinMode(PIN_TRIG[i], OUTPUT);
    pinMode(PIN_ECHO[i], INPUT);
    digitalWrite(PIN_TRIG[i], LOW);
  }
  Serial.begin(9600);
}

// 한 채널 측정: 거리(cm) 또는 음수(에코 없음)
float measure(int i) {
  digitalWrite(PIN_TRIG[i], LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG[i], HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG[i], LOW);
  unsigned long dur = pulseIn(PIN_ECHO[i], HIGH, ECHO_TIMEOUT_US);
  if (dur == 0) return -1.0f;
  return dur * 0.0343f / 2.0f;
}

void loop() {
  for (int i = 0; i < N; i++) {
    float d = measure(i);
    if (d < 0) Serial.print("NA");
    else       Serial.print(d, 2);
    if (i < N - 1) Serial.print(',');
    delay(GAP_MS);   // 이전 채널 잔향이 다음 채널에 잡히는 것 방지
  }
  Serial.println();
  delay(CYCLE_MS);
}
