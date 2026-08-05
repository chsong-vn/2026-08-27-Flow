// 핀 출력 테스트 — 멀티미터로 D2~D9 신호선 검증용 (센서 없이)
//
// 용도: HC-SR04 4채널 배선 문제 진단 중, "아두이노 D핀이 실제로 신호를 내는가?"를
//   멀티미터(DC 전압)로 확인. 정상 펌웨어의 TRIG 펄스는 10us라 멀티미터로 못 잡으므로,
//   이 스케치는 각 핀을 3초씩 HIGH로 '고정'해 육안/계측 확인이 가능하게 한다.
//
// 사용법:
//   1) 센서 선 다 뽑은 맨보드에 업로드
//   2) 멀티미터 DC 전압 모드, 검정탐침=아두이노 GND, 빨강탐침=측정할 D핀
//   3) 시리얼(9600)에 "NOW HIGH: D6" 뜬 동안 그 핀을 짚으면 ~5V, 나머지 핀은 ~0V
//   4) 8개 핀(2~9) 전부 5V까지 오르면 아두이노 출력·핀 정상
//
// TRIG=짝수핀(2,4,6,8), ECHO=홀수핀(3,5,7,9). 여기선 8개 다 OUTPUT으로 구동해 확인.

const int PINS[8] = {2, 3, 4, 5, 6, 7, 8, 9};
const int N = 8;
const unsigned long HOLD_MS = 3000;   // 핀당 HIGH 유지 시간

void setup() {
  for (int i = 0; i < N; i++) {
    pinMode(PINS[i], OUTPUT);
    digitalWrite(PINS[i], LOW);
  }
  Serial.begin(9600);
  Serial.println("PIN TEST: D2~D9 를 하나씩 3초 HIGH. 멀티미터 DC 로 확인(GND 기준).");
}

void loop() {
  for (int i = 0; i < N; i++) {
    for (int j = 0; j < N; j++) digitalWrite(PINS[j], j == i ? HIGH : LOW);
    Serial.print("NOW HIGH: D");
    Serial.println(PINS[i]);
    delay(HOLD_MS);
  }
}
