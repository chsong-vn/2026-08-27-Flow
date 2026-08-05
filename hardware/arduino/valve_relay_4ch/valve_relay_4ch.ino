/*
 * FlowChem 4-Channel Relay Valve Controller
 *
 * 프로토콜: "<channel> <position>\n"
 *   - channel: 1~4 (릴레이 채널)
 *   - position: 1 (SOURCE/WASTE) 또는 2 (REACTOR/COLLECT)
 *   - 예: "1 2\n" → ch1을 position 2로 전환
 *
 * 하위호환: "1\n" / "2\n" → 전체 채널 동시 제어 (기존 방식)
 *
 * 릴레이 핀 매핑:
 *   ch1 → D2 (릴레이1)
 *   ch2 → D3 (릴레이2)
 *   ch3 → D4 (릴레이3)
 *   ch4 → D5 (릴레이4)
 *
 * 릴레이 상태:
 *   LOW  = position 1 (SOURCE / WASTE) - 기본 상태
 *   HIGH = position 2 (REACTOR / COLLECT)
 *
 * 응답: "OK <channel> <position>\n" (정상) / "ERR <message>\n" (에러)
 *
 * @codesyncer-decision: String 클래스 제거 → char 배열로 교체 (AVR 힙 단편화 방지)
 */

const int NUM_CHANNELS = 4;
// @codesyncer-decision: ch=pin 순차 매핑 (D2~D5)
const int relayPins[NUM_CHANNELS] = {2, 3, 4, 5};  // ch1→D2, ch2→D3, ch3→D4, ch4→D5
int relayState[NUM_CHANNELS] = {0, 0, 0, 0};        // 0=pos1(LOW), 1=pos2(HIGH)

// @codesyncer-decision: char 배열 고정 버퍼 사용 (String 힙 단편화 방지)
#define BUF_SIZE 16
char buf[BUF_SIZE];
uint8_t bufIdx = 0;

void setup() {
  Serial.begin(9600);

  for (int i = 0; i < NUM_CHANNELS; i++) {
    pinMode(relayPins[i], OUTPUT);
    digitalWrite(relayPins[i], LOW);  // 기본: position 1
  }

  Serial.println("READY 4CH");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      buf[bufIdx] = '\0';
      if (bufIdx > 0) {
        processCommand();
      }
      bufIdx = 0;
    } else {
      if (bufIdx < BUF_SIZE - 1) {
        buf[bufIdx++] = c;
      } else {
        // 오버플로우 → 리셋
        bufIdx = 0;
      }
    }
  }
}

void processCommand() {
  // 공백 위치 찾기
  int spaceIdx = -1;
  for (int i = 0; i < bufIdx; i++) {
    if (buf[i] == ' ') {
      spaceIdx = i;
      break;
    }
  }

  if (spaceIdx == -1) {
    // 하위호환: "1" 또는 "2" → 전체 채널 동시 제어
    int pos = atoi(buf);
    if (pos == 1 || pos == 2) {
      for (int i = 0; i < NUM_CHANNELS; i++) {
        setRelay(i, pos);
      }
      Serial.print("OK ALL ");
      Serial.println(pos);
    } else {
      Serial.println("ERR INVALID_CMD");
    }
    return;
  }

  // 새 프로토콜: "<channel> <position>"
  buf[spaceIdx] = '\0';  // 공백을 널로 분리
  int channel = atoi(buf);
  int position = atoi(&buf[spaceIdx + 1]);

  if (channel < 1 || channel > NUM_CHANNELS) {
    Serial.println("ERR INVALID_CH");
    return;
  }

  if (position != 1 && position != 2) {
    Serial.println("ERR INVALID_POS");
    return;
  }

  setRelay(channel - 1, position);  // 0-indexed
  Serial.print("OK ");
  Serial.print(channel);
  Serial.print(" ");
  Serial.println(position);
}

void setRelay(int idx, int position) {
  if (position == 1) {
    digitalWrite(relayPins[idx], LOW);
    relayState[idx] = 0;
  } else {
    digitalWrite(relayPins[idx], HIGH);
    relayState[idx] = 1;
  }
}
