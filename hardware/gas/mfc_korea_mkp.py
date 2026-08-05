# -*- coding: utf-8 -*-
"""MFC KOREA (MKPrecision) 질량유량계 드라이버 — MKP RS485 ASCII 프로토콜.

프로토콜 출처: 엠에프씨코리아 Communication Protocol RS485 V1.R2.C0 (2022-01-14).
  ※ 기존 자리표시자 드라이버(Modbus RTU 가정)를 실제 사양으로 전면 교체.

통신 설정 (고정): 9600 bps / 8 data / **ODD parity** / 1 stop  — Modbus 아님.

프레임: `':' + Addr(2) + Cmd(2) + DataType(2) + Data(0~64) + Checksum(2) + '\\r'`
  · 모든 필드는 Hex-ASCII (바이트값을 2자리 대문자 hex 문자로 표현).
  · Addr : 슬레이브 주소 0x00~0xFF (2 ASCII).
  · Cmd  : 명령 코드. **MSB = 요청(0)/응답(1)** — Write SP 요청 0x01 → 응답 0x81.
  · Checksum = Start(':')부터 Data 끝까지 raw ASCII 바이트 XOR → 2자리 hex.
  · End = 0x0D('\\r').

유량 표현: **풀스케일 대비 백분율(%)**, IEEE754 single-precision **big-endian** 8 hex자.
  · 실제 유량(sccm) = % / 100 × full_scale.
  · full_scale 는 Read Full Scale(0x33) 로 장비에서 읽거나 설정값 max_sccm 사용.

@codesyncer-decision: 엔진/UI 계약(set_flow/get_flow 단위=sccm)은 그대로 두고,
  %↔sccm 환산을 드라이버 내부로 캡슐화. 100% 기준 = max_sccm(설정). connect 시
  장비 Full Scale/Unit 을 읽어 로그 + 설정 불일치 경고(환산 정확도 보호).
@codesyncer-decision: 프레임/체크섬/타임아웃 오류는 MFCProtocolError 로 승격
  (조용한 실패 금지 규약) — 엔진이 SafetyError 로 전파해 Emergency Stop.
@codesyncer-context: 찬호 셋업은 RS-485 백플레인. MKP 는 자체 ASCII 프로토콜이라
  KinCony E8v3(Modbus) 와 같은 물리버스라도 프레이밍이 달라 동일 포트 공유 불가 —
  MFC 는 별도 포트(또는 프로토콜 인지 게이트웨이)로 연결해야 함.
"""
import struct
import threading
import time

# ── 프레임 상수 ──────────────────────────────────────────────
_START = 0x3A   # ':'
_END = 0x0D     # '\r'
_RESP_FLAG = 0x80   # 응답 프레임 Command MSB

# ── 명령어 (요청 코드; 응답 = 요청 | 0x80) ──────────────────────
CMD_WRITE_SP       = 0x01   # Write Set Point (Float %, MFC 전용)
CMD_READ_SP        = 0x02   # Read Set Point  (Float %)
CMD_READ_FLOW      = 0x03   # Read Flow       (Float %)
CMD_READ_STATUS    = 0x40   # Read MFC Status (Unsigned char, 비트 플래그)
CMD_READ_MODEL     = 0x21   # Read Model Name (String)
CMD_READ_GAS       = 0x31   # Read Gas Name   (String)
CMD_READ_FULLSCALE = 0x33   # Read Full Scale (Float, native unit)
CMD_READ_UNIT      = 0x35   # Read Flow Unit  (Unsigned char, 0~6)
CMD_READ_DEVID     = 0x71   # Read Device ID  (Unsigned char)
# @codesyncer-decision(2026-08-05, 벤더 실기 드라이버 이식): 초기화 시 0x78=0x01
#   쓰기 — 실기 테스트된 MFC_Driver.zip(MFCController.initialize)이 항상 먼저
#   전송하는 명령. 디지털(시리얼) setpoint 제어 활성으로 추정 — 이거 없이는
#   실장비가 Write Set Point 를 무시할 수 있음.
CMD_WRITE_MODE     = 0x78   # Write Control Mode (Unsigned char, 0x01=digital)

# ── 데이터 타입 ──────────────────────────────────────────────
DT_NODATA = 0x00
DT_UCHAR  = 0x02   # Unsigned char (2 ASCII = 1 binary byte)
DT_FLOAT  = 0x07   # IEEE754 single (8 ASCII = 4 binary byte)

# ── 유량 단위 코드 (Read Flow Unit 0x35) ─────────────────────
FLOW_UNITS = {0: "CCM", 1: "LPM", 2: "SCCM", 3: "SLPM",
              4: "m3/hr", 5: "m3/min", 6: "%"}

# ── 상태 비트 (Read MFC Status 0x40) ─────────────────────────
STATUS_BITS = {0: "Power Over(>32V)", 1: "Sensor Fail", 2: "Heater Power Low"}


class MFCProtocolError(RuntimeError):
    """프레임/체크섬/타임아웃/명령불일치 오류 (fault-masking 금지 규약)."""


def _float_to_ascii(value):
    """float → IEEE754 single big-endian → 8자리 대문자 hex 문자."""
    return struct.pack(">f", float(value)).hex().upper()


def _ascii_to_float(hex8):
    """8자리 hex 문자 → IEEE754 single big-endian float."""
    return struct.unpack(">f", bytes.fromhex(hex8))[0]


def _xor_checksum(payload_bytes):
    """Start('~:')부터 Data 끝까지 raw ASCII 바이트 XOR → 정수(0~255)."""
    chk = 0
    for b in payload_bytes:
        chk ^= b
    return chk & 0xFF


class MFCKoreaMKP:
    """단일 MFC 채널 (MKP RS485 ASCII). 실패 시 예외 — 조용한 실패 금지.

    엔진/UI 계약(보존): connect / set_flow(sccm) / get_flow()->sccm /
      pulse(sccm, sec) / stop / disconnect + 속성 is_connected/max_sccm/_sp/name.
    """

    def __init__(self, port, slave_addr=1, baudrate=9600, max_sccm=100.0,
                 name="MFC", timeout=0.6):
        self.port = port
        self.addr = int(slave_addr) & 0xFF
        self.baud = int(baudrate)
        self.max_sccm = float(max_sccm)      # 100% 기준 (native full scale)
        self.name = name
        self.timeout = float(timeout)
        self.is_connected = False
        self._ser = None
        self._lock = threading.Lock()        # set_flow(Manual/엔진) 동시 접근 직렬화
        self._sp = 0.0                       # 마지막 명령 setpoint(sccm) — UI/모니터 미러
        self.device_full_scale = None        # 장비 보고 FS (native unit)
        self.flow_unit = None                # 0~6 (FLOW_UNITS)
        self.model = None                    # 장비 모델명 (0x21)
        self.gas = None                      # 가스명 (0x31)
        self._last_data_type = None          # 직전 응답 DataType (문자열 디코딩용)

    # ── 연결 ──────────────────────────────────────────────────
    def connect(self):
        if not self.port or "Mock" in str(self.port):
            print(f"[{self.name}] Virtual MFC (Mock) — MKP 프로토콜 시뮬")
            self.is_connected = True
            return True
        try:
            import serial
            self._ser = serial.Serial(
                port=self.port, baudrate=self.baud,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_ODD,
                stopbits=serial.STOPBITS_ONE, timeout=self.timeout)
            self.is_connected = True
            print(f"[{self.name}] MKP RS485 연결 {self.port} "
                  f"addr={self.addr} (9600/8/ODD/1)")
            # 디지털 제어 모드 활성 (벤더 initialize 1단계 — 0x78=0x01).
            # 실패 시에도 연결은 유지하되 크게 경고: 이 명령이 안 먹으면
            # 이후 Write Set Point 가 무시될 수 있음 (조용한 실패 금지).
            try:
                self.enable_digital_control()
                print(f"[{self.name}] 디지털 제어 모드 활성 (0x78=01)")
            except Exception as e:
                print(f"[{self.name}] ⚠⚠ 디지털 제어 모드 설정 실패: {e} — "
                      f"setpoint 명령이 무시될 수 있음 (배선/주소 확인)")
            # 풀스케일/단위 확인 — 실패해도 치명적 아님(설정 max_sccm 사용).
            # @codesyncer-decision: 장비 실측 Full Scale 을 환산 기준(max_sccm)으로
            #   자동 채택 — 규격 미입력(설정 None)이어도 %↔sccm 이 정확해짐.
            #   설정값과 다르면 로그로 알림(진단용).
            try:
                _cfg_max = self.max_sccm
                self.model = self.read_model()
                self.gas = self.read_gas()
                self.device_full_scale = self.read_full_scale()
                self.flow_unit = self.read_flow_unit()
                u = FLOW_UNITS.get(self.flow_unit, "?")
                print(f"[{self.name}] Model={self.model}, Gas={self.gas}")
                if self.device_full_scale and self.device_full_scale > 0:
                    self.max_sccm = float(self.device_full_scale)   # 환산 기준 = 장비 실측 FS
                    if _cfg_max > 0 and abs(self.device_full_scale - _cfg_max) / _cfg_max > 0.02:
                        print(f"[{self.name}] 설정 max_sccm({_cfg_max}) → 장비 "
                              f"Full Scale({self.device_full_scale:.3f} {u}) 로 대체(환산 기준)")
                    else:
                        print(f"[{self.name}] 장비 Full Scale={self.device_full_scale:.3f} {u}")
            except Exception as e:
                print(f"[{self.name}] Full Scale/Unit 조회 생략(설정 max={self.max_sccm} 사용): {e}")
            return True
        except Exception as e:
            print(f"[{self.name}] 연결 실패: {e}")
            self.is_connected = False
            self._ser = None
            return False

    # ── 프레임 I/O ─────────────────────────────────────────────
    def build_frame(self, cmd, data_type=DT_NODATA, data_ascii=""):
        """요청 프레임 bytes 조립 (체크섬 포함). 테스트에서 직접 검증 가능."""
        body = (bytes([_START])
                + f"{self.addr:02X}".encode("ascii")
                + f"{cmd & 0xFF:02X}".encode("ascii")
                + f"{data_type & 0xFF:02X}".encode("ascii")
                + str(data_ascii).encode("ascii"))
        chk = _xor_checksum(body)
        return body + f"{chk:02X}".encode("ascii") + bytes([_END])

    def _transact(self, cmd, data_type=DT_NODATA, data_ascii=""):
        """요청 전송 → 응답 프레임 파싱 → Data 필드(str) 반환.
        Mock(포트 없음)이면 None (호출부가 _sp 미러 등으로 처리)."""
        if self._ser is None:
            return None
        frame = self.build_frame(cmd, data_type, data_ascii)
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            raw = self._ser.read_until(bytes([_END]))
        if not raw or raw[-1] != _END:
            raise MFCProtocolError(f"{self.name}: 응답 타임아웃 (cmd=0x{cmd:02X})")
        # 시작 구분자 정렬 (라인 노이즈 방어)
        si = raw.find(bytes([_START]))
        if si < 0:
            raise MFCProtocolError(f"{self.name}: 시작 구분자 없음 {raw!r}")
        buf = raw[si:-1]                     # ':'..checksum ('\r' 제외)
        if len(buf) < 9:
            raise MFCProtocolError(f"{self.name}: 프레임 너무 짧음 {raw!r}")
        payload, rx_chk = buf[:-2], buf[-2:]  # payload = ':'..data, rx_chk = 체크섬 2자
        calc = _xor_checksum(payload)
        if f"{calc:02X}".encode("ascii") != rx_chk.upper():
            raise MFCProtocolError(
                f"{self.name}: 체크섬 불일치 rx={rx_chk!r} calc={calc:02X}")
        resp_cmd = int(payload[3:5], 16)
        if (resp_cmd & 0x7F) != (cmd & 0x7F):
            raise MFCProtocolError(
                f"{self.name}: 명령 불일치 req=0x{cmd:02X} resp=0x{resp_cmd:02X}")
        self._last_data_type = int(payload[5:7], 16)   # 문자열 응답 디코딩용
        return payload[7:].decode("ascii")   # Data 필드

    def _decode_string(self, data):
        """문자열 응답 디코딩 — DataType 상위 2비트 01 이면 하위 6비트 = 길이.
        (벤더 실기 드라이버 MFCController._decode 와 동일 규칙)"""
        dt = self._last_data_type or 0
        if (dt & 0xC0) == 0x40:
            return data[: dt & 0x3F]
        return data

    # ── 고수준 API (단위=sccm) ─────────────────────────────────
    def set_flow(self, sccm):
        """질소 유량 설정(sccm). 내부에서 %로 환산 후 IEEE754 Write Set Point.
        스페이서 펄스 = set_flow(x) → sleep(t) → set_flow(0)."""
        sccm = max(0.0, min(self.max_sccm, float(sccm)))
        self._sp = sccm
        pct = 0.0 if self.max_sccm <= 0 else (sccm / self.max_sccm) * 100.0
        pct = max(0.0, min(100.0, pct))
        if self._ser is None:
            print(f"[{self.name}] (Mock) set_flow {sccm:.2f} sccm ({pct:.1f}%)")
            return
        self._transact(CMD_WRITE_SP, DT_FLOAT, _float_to_ascii(pct))

    def read_setpoint(self):
        """장비 setpoint(%) → sccm."""
        if self._ser is None:
            return self._sp
        return _ascii_to_float(self._transact(CMD_READ_SP)) / 100.0 * self.max_sccm

    def get_flow(self):
        """현재 유량(%) → sccm."""
        if self._ser is None:
            return self._sp
        return _ascii_to_float(self._transact(CMD_READ_FLOW)) / 100.0 * self.max_sccm

    def read_full_scale(self):
        """장비 Full Scale (native unit, float)."""
        if self._ser is None:
            return self.max_sccm
        return _ascii_to_float(self._transact(CMD_READ_FULLSCALE))

    def read_flow_unit(self):
        """유량 단위 코드 0~6 (FLOW_UNITS)."""
        if self._ser is None:
            return 2   # SCCM 가정
        return int(self._transact(CMD_READ_UNIT), 16) & 0xFF

    def enable_digital_control(self):
        """디지털(시리얼) setpoint 제어 활성 — 벤더 initialize 의 0x78=0x01.
        연결 직후 1회 호출. 실패 시 예외 (연결부에서 시끄럽게 경고 후 진행)."""
        if self._ser is None:
            return
        self._transact(CMD_WRITE_MODE, DT_UCHAR, "01")

    def read_model(self):
        """장비 모델명 (0x21, String)."""
        if self._ser is None:
            return "-"
        return self._decode_string(self._transact(CMD_READ_MODEL))

    def read_gas(self):
        """가스명 (0x31, String)."""
        if self._ser is None:
            return "-"
        return self._decode_string(self._transact(CMD_READ_GAS))

    def read_status(self):
        """MFC 상태 비트 → 활성 오류 문자열 리스트 (빈 리스트 = 정상)."""
        if self._ser is None:
            return []
        val = int(self._transact(CMD_READ_STATUS), 16) & 0xFF
        return [msg for bit, msg in STATUS_BITS.items() if val & (1 << bit)]

    def pulse(self, sccm, sec):
        """블로킹 스페이서 펄스 — 엔진 워커 스레드 전용 (GUI 금지)."""
        self.set_flow(sccm)
        time.sleep(max(0.0, float(sec)))
        self.set_flow(0.0)

    def stop(self):
        try:
            self.set_flow(0.0)
        except Exception as e:
            print(f"[{self.name}] stop error: {e}")

    def disconnect(self):
        try:
            self.stop()
        finally:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None
            self.is_connected = False


# @codesyncer-decision: 하위호환 별칭 — 기존 import(MFCKoreaModbus)/config 라벨
#   ("질소 MFC (Modbus)")이 남아 있어도 새 드라이버로 해석되게 유지.
MFCKoreaModbus = MFCKoreaMKP
