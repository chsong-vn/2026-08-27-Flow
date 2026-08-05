/*
 * FlowChem 8-Channel 3-Way Valve Controller — Waveshare ESP32-S3-ETH-8DI-8RO
 *
 * valve_relay_4ch.ino (Arduino UNO) 의 ASCII 프로토콜을 TCP 로 포팅한 펌웨어.
 * PC 드라이버: hardware/valves/valve_esp32_eth.py (ESP32EthValve)
 *
 * 요구 환경: Arduino IDE + arduino-esp32 코어 v3.0 이상 (W5500 ETH 지원)
 *   보드 선택: "ESP32S3 Dev Module", USB CDC On Boot: Enabled
 *
 * 빌드 검증 (2026-07-29, esp32:esp32@3.3.11 / arduino-cli 1.2.0 — 경고 0, exit 0):
 *   CLI="C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
 *   & $CLI compile --fqbn "esp32:esp32:esp32s3:CDCOnBoot=cdc" <이 폴더>
 *   업로드:  & $CLI upload  --fqbn "esp32:esp32:esp32s3:CDCOnBoot=cdc" -p COM<n> <이 폴더>
 *   (Flash 사용량 43% / RAM 8% — 여유 충분)
 *
 * 프로토콜 (TCP 포트 5000 + USB 시리얼 양쪽 동일):
 *   "<channel> <position>\n"
 *     - channel: 1~8 (릴레이 채널)
 *     - position: 1 (SOURCE/WASTE) 또는 2 (REACTOR/COLLECT)
 *     - 응답: "OK <channel> <position>" / "ERR <message>"
 *   "1\n" / "2\n"  → 전체 채널 동시 제어 (UNO 하위호환) → "OK ALL <pos>"
 *   "STATE\n"      → "STATE <p1> <p2> ... <p8>" (채널별 현재 position)
 *   "ID\n"         → "ID FLOWCHEM-3WAY-ETH 8CH" (장치 식별/프로브용)
 *   "NET\n"        → "NET <up|down> IP <ip> MASK <m> GW <g> MAC <mac> ETH <ok|err> PCA <ok|err>"
 *                    (현장 진단용 — TCP 접속 실패 시 링크/IP/초기화 실패를 구분)
 *
 * 채널 배정 (2026-07-29 확정, 순차 배선 — PC측 _CH_MAP 역순 보정 불필요):
 *   RO1=Group A 스위처, RO2=Group B, RO3=Group C, RO4=Group D, RO5=Outlet
 *   RO6~RO8 = 예비
 *
 * 릴레이 결선: COM=밸브 전원(+), NO=솔레노이드
 *   릴레이 OFF = position 1 (SOURCE/WASTE) — 기본/정전 시 fail-safe 상태
 *   릴레이 ON  = position 2 (REACTOR/COLLECT)
 *
 * @codesyncer-decision: 릴레이 8ch 는 GPIO 직결이 아니라 PCA9554 I/O 익스팬더
 *   (I2C 0x20, SDA=42/SCL=41) 경유 — Waveshare 보드 회로 구조.
 *   출력 레지스터를 먼저 OFF 로 쓴 뒤 CONFIG 를 출력으로 전환해 부팅 글리치 방지.
 *   매 전환 후 출력 레지스터를 readback 검증 — 불일치면 ERR I2C (ACK 진실성).
 * @codesyncer-decision: 통신 두절 시 릴레이 상태 유지 (임의 리셋 금지) —
 *   시퀀스 중 재연결로 밸브가 튀는 사고 방지. UNO 펌웨어와 동일 정책.
 * @codesyncer-decision: TCP 신규 접속은 기존 클라이언트를 대체 — half-open
 *   소켓이 남아도 PC 드라이버 재연결이 즉시 성공해야 하므로 (거부 방식 금지).
 * @codesyncer-risk: RELAY_ACTIVE_HIGH / W5500 RST 핀은 실기 1회 검증 필요.
 *   첫 통전 시 릴레이가 전부 OFF(LED 소등)인지 반드시 확인할 것.
 */

#include <ETH.h>
#include <Wire.h>

// ── 네트워크 설정 (현장 배포 시 이 블록만 수정) ─────────────────────
// 2026-07-29 실측: PC 가 192.168.10.78/24, 게이트웨이 192.168.10.1 (Wi-Fi) →
// 보드도 같은 서브넷이어야 통신 가능. .60 은 ping 무응답 확인(미사용).
// @codesyncer-risk: .60 이 공유기 DHCP 풀 안이면 나중에 다른 장치와 충돌 가능 —
//   공유기에서 이 MAC 을 .60 으로 예약하거나, 풀 밖 주소로 바꿀 것.
IPAddress STATIC_IP(192, 168, 10, 60);
IPAddress GATEWAY(192, 168, 10, 1);
IPAddress SUBNET(255, 255, 255, 0);
const uint16_t TCP_PORT = 5000;

// ── W5500 이더넷 (SPI) — Waveshare ESP32-S3-ETH-8DI-8RO 핀맵 ─────────
#define ETH_PHY_ADDR_W5500  1
#define ETH_CS_PIN          16
#define ETH_IRQ_PIN         12
#define ETH_RST_PIN         -1   // 보드상 GPIO 리셋 미배선 (파워온 리셋 사용)
#define ETH_SCK_PIN         15
#define ETH_MISO_PIN        14
#define ETH_MOSI_PIN        13

// ── PCA9554 릴레이 익스팬더 ──────────────────────────────────────────
#define I2C_SDA_PIN         42
#define I2C_SCL_PIN         41
#define PCA9554_ADDR        0x20
#define REG_INPUT           0x00
#define REG_OUTPUT          0x01
#define REG_CONFIG          0x03
#define RELAY_ACTIVE_HIGH   1    // PCA9554 핀 HIGH=릴레이 ON. 실기 확인 후 필요 시 0

const int NUM_CHANNELS = 8;
uint8_t relayShadow = 0x00;      // bit=1 → position 2 (릴레이 ON)

// 초기화 성공 여부 — NET 명령으로 조회 (부팅 로그를 놓쳐도 원인 파악 가능)
bool pcaOk = false;
bool ethOk = false;

NetworkServer server(TCP_PORT);
NetworkClient client;

// UNO 펌웨어와 동일: char 고정 버퍼 (스트림별 분리)
#define BUF_SIZE 16
char serBuf[BUF_SIZE];  uint8_t serIdx = 0;
char netBuf[BUF_SIZE];  uint8_t netIdx = 0;

// ── PCA9554 I2C 헬퍼 ────────────────────────────────────────────────
bool pcaWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(PCA9554_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

int pcaRead(uint8_t reg) {
  Wire.beginTransmission(PCA9554_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom((int)PCA9554_ADDR, 1) != 1) return -1;
  return Wire.read();
}

// relayShadow → 출력 레지스터 기록 + readback 검증
bool applyRelays() {
  uint8_t out = RELAY_ACTIVE_HIGH ? relayShadow : (uint8_t)~relayShadow;
  if (!pcaWrite(REG_OUTPUT, out)) return false;
  int rb = pcaRead(REG_OUTPUT);
  return rb >= 0 && (uint8_t)rb == out;
}

// idx: 0-based, position: 1 or 2. 성공 시 true.
bool setRelay(int idx, int position) {
  uint8_t prev = relayShadow;
  if (position == 2) relayShadow |=  (1 << idx);
  else               relayShadow &= ~(1 << idx);
  if (applyRelays()) return true;
  // 실패 → 실제 레지스터를 다시 읽어 shadow 재동기화 (진실성 유지)
  int rb = pcaRead(REG_OUTPUT);
  if (rb >= 0) relayShadow = RELAY_ACTIVE_HIGH ? (uint8_t)rb : (uint8_t)~rb;
  else         relayShadow = prev;
  return false;
}

// ── 명령 처리 (UNO processCommand 이식 + STATE/ID 확장) ─────────────
void processLine(char *buf, uint8_t len, Print &out) {
  if (len == 0) return;

  if (strcmp(buf, "STATE") == 0 || strcmp(buf, "state") == 0) {
    out.print("STATE");
    for (int i = 0; i < NUM_CHANNELS; i++) {
      out.print(' ');
      out.print((relayShadow >> i) & 1 ? 2 : 1);
    }
    out.println();
    return;
  }

  if (strcmp(buf, "ID") == 0 || strcmp(buf, "id") == 0) {
    out.println("ID FLOWCHEM-3WAY-ETH 8CH");
    return;
  }

  if (strcmp(buf, "NET") == 0 || strcmp(buf, "net") == 0) {
    out.print("NET ");
    out.print(ETH.linkUp() ? "up" : "down");
    out.print(" IP ");   out.print(ETH.localIP());
    out.print(" MASK "); out.print(ETH.subnetMask());
    out.print(" GW ");   out.print(ETH.gatewayIP());
    out.print(" MAC ");  out.print(ETH.macAddress());
    out.print(" ETH ");  out.print(ethOk ? "ok" : "err");
    out.print(" PCA ");  out.println(pcaOk ? "ok" : "err");
    return;
  }

  int spaceIdx = -1;
  for (uint8_t i = 0; i < len; i++) {
    if (buf[i] == ' ') { spaceIdx = i; break; }
  }

  if (spaceIdx == -1) {
    // 하위호환: "1" / "2" → 전체 채널 동시 제어
    int pos = atoi(buf);
    if (pos == 1 || pos == 2) {
      bool ok = true;
      for (int i = 0; i < NUM_CHANNELS; i++) {
        if (!setRelay(i, pos)) ok = false;
      }
      if (ok) { out.print("OK ALL "); out.println(pos); }
      else    { out.println("ERR I2C"); }
    } else {
      out.println("ERR INVALID_CMD");
    }
    return;
  }

  buf[spaceIdx] = '\0';
  int channel  = atoi(buf);
  int position = atoi(&buf[spaceIdx + 1]);

  if (channel < 1 || channel > NUM_CHANNELS) { out.println("ERR INVALID_CH");  return; }
  if (position != 1 && position != 2)        { out.println("ERR INVALID_POS"); return; }

  if (setRelay(channel - 1, position)) {
    out.print("OK ");
    out.print(channel);
    out.print(' ');
    out.println(position);
  } else {
    out.println("ERR I2C");
  }
}

// 스트림 → 라인 버퍼 축적 (UNO loop 이식)
void pumpStream(Stream &in, Print &out, char *buf, uint8_t &idx) {
  while (in.available()) {
    char c = in.read();
    if (c == '\n' || c == '\r') {
      buf[idx] = '\0';
      if (idx > 0) processLine(buf, idx, out);
      idx = 0;
    } else {
      if (idx < BUF_SIZE - 1) buf[idx++] = c;
      else idx = 0;  // 오버플로우 → 리셋
    }
  }
}

void setup() {
  Serial.begin(115200);

  // 릴레이 초기화 — 출력값 OFF 를 먼저 쓰고 나서 출력 모드로 전환 (글리치 방지)
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  relayShadow = 0x00;
  uint8_t offVal = RELAY_ACTIVE_HIGH ? 0x00 : 0xFF;
  pcaOk = pcaWrite(REG_OUTPUT, offVal) && pcaWrite(REG_CONFIG, 0x00);

  // W5500 이더넷 (정적 IP — DHCP 미사용, COM 번호 지옥 탈출이 목적)
  ethOk = ETH.begin(ETH_PHY_W5500, ETH_PHY_ADDR_W5500, ETH_CS_PIN, ETH_IRQ_PIN,
                    ETH_RST_PIN, SPI2_HOST, ETH_SCK_PIN, ETH_MISO_PIN, ETH_MOSI_PIN);
  ETH.config(STATIC_IP, GATEWAY, SUBNET);

  server.begin();
  server.setNoDelay(true);

  Serial.print("READY 8CH ETH ");
  Serial.print(STATIC_IP);
  Serial.print(':');
  Serial.println(TCP_PORT);
  if (!pcaOk) Serial.println("ERR PCA9554_INIT");
}

void loop() {
  // 신규 TCP 접속 → 기존 클라이언트 대체 (드라이버 재연결 즉시 수용)
  NetworkClient newClient = server.accept();
  if (newClient) {
    if (client && client.connected()) client.stop();
    client = newClient;
    client.setNoDelay(true);
    client.println("READY 8CH ETH");
    netIdx = 0;
  }

  if (client && client.connected()) {
    pumpStream(client, client, netBuf, netIdx);
  }
  pumpStream(Serial, Serial, serBuf, serIdx);
}
