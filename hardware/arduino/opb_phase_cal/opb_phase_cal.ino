/*
  opb_phase_cal.ino — OPB(OCB350) 2채널: ADC 스트림 + 하드웨어 캘리브 명령
  Arduino UNO, 115200 bps

  기존 리그 펌웨어(PhotoSensor.ino 개조판)의 상위호환:
    · 출력 포맷 동일 유지  →  "S1:<adc>,<phase> | S2:<adc>,<phase>"  (50 ms 주기)
      단, <phase> 가 임계 판정(0/1)이 아니라 OCB350 캘리브 보드의 디지털 판정
      A+2B 로 바뀜: 0=에러(센서없음) / 1=투명액체 / 2=불투명·유색액체 / 3=기체
      (PC 드라이버는 이 숫자를 무시하므로 호환 무해 — 진단 모니터링용 보너스)
    · 시리얼 입력 신설:
        "CAL\n"  = 양쪽 캘리브   /  "CAL1\n" = 센서1  /  "CAL2\n" = 센서2
      → 해당 Cal 핀 100 ms LOW 펄스 (OCB350.cpp 원본과 동일), 응답 "CAL:OK,x"
      ⚠ RoboChem 계약: 캘리브는 반드시 '튜브에 액체 없음' 상태에서만
        (N2Precal 이 배기 완료 후 자동 호출하는 시점이 정확히 그 때)

  배선 (실물 확정 2026-08-18 — ⚠ RoboChem 표준 핀맵과 다름, 이 스케치가 실배선 기준):
    Sensor 1 (INLET, 논리 ch0):  White(Analog)→A0  Orange(OUT A)→D2
                                 Blue(OUT B)→D3    Green(Cal)→D6
    Sensor 2 (OUTLET, 논리 ch1): White(Analog)→A1  Orange(OUT A)→D4
                                 Blue(OUT B)→D5    Green(Cal)→D7
    공통: Red→5V, Black→GND
    (RoboChem Phase_Sensor_Array.ino 표준은 유닛0=Cal D2/B D3/A D4,
     유닛1=Cal D5/B D6/A D7 — 그 펌웨어를 올리려면 핀 define 수정 필요)
*/

const uint8_t S1_AN = A0, S1_A = 2, S1_B = 3, S1_CAL = 6;
const uint8_t S2_AN = A1, S2_A = 4, S2_B = 5, S2_CAL = 7;
const unsigned long SAMPLE_INTERVAL_MS = 50;

unsigned long previousSampleTime = 0;
char cmdBuf[8];
uint8_t cmdLen = 0;

void setup() {
  Serial.begin(115200);
  pinMode(S1_A, INPUT);
  pinMode(S1_B, INPUT);
  pinMode(S2_A, INPUT);
  pinMode(S2_B, INPUT);
  // Cal 핀은 idle HIGH (OCB350.cpp init 관례 — LOW 펄스가 캘리브 트리거)
  digitalWrite(S1_CAL, HIGH);
  pinMode(S1_CAL, OUTPUT);
  digitalWrite(S1_CAL, HIGH);
  digitalWrite(S2_CAL, HIGH);
  pinMode(S2_CAL, OUTPUT);
  digitalWrite(S2_CAL, HIGH);
}

void calPulse(uint8_t pin) {
  digitalWrite(pin, LOW);
  delay(100);                       // OCB350 캘리브 펄스 폭 (원본 동일)
  digitalWrite(pin, HIGH);
}

void handleCommand() {
  cmdBuf[cmdLen] = 0;
  if (strcmp(cmdBuf, "CAL") == 0) {
    calPulse(S1_CAL);
    calPulse(S2_CAL);
    Serial.println(F("CAL:OK,BOTH"));
  } else if (strcmp(cmdBuf, "CAL1") == 0) {
    calPulse(S1_CAL);
    Serial.println(F("CAL:OK,1"));
  } else if (strcmp(cmdBuf, "CAL2") == 0) {
    calPulse(S2_CAL);
    Serial.println(F("CAL:OK,2"));
  }
  cmdLen = 0;
}

void loop() {
  // ── 명령 수신 (라인 단위, 대소문자 무관) ──
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen) handleCommand();
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = toupper(c);
    } else {
      cmdLen = 0;                   // 오버플로 = 잡음 — 버림
    }
  }

  // ── 50 ms 주기 스트림 ──
  const unsigned long now = millis();
  if (now - previousSampleTime < SAMPLE_INTERVAL_MS) return;
  previousSampleTime = now;

  const int a1 = analogRead(S1_AN);
  const int a2 = analogRead(S2_AN);
  const uint8_t p1 = digitalRead(S1_A) + 2 * digitalRead(S1_B);
  const uint8_t p2 = digitalRead(S2_A) + 2 * digitalRead(S2_B);

  Serial.print(F("S1:"));
  Serial.print(a1);
  Serial.print(',');
  Serial.print(p1);
  Serial.print(F(" | S2:"));
  Serial.print(a2);
  Serial.print(',');
  Serial.println(p2);
}
