# -*- coding: utf-8 -*-
"""MFC KOREA MKP RS485 드라이버 검증 — PDF 프로토콜 예제로 직접 대조.

프레임 조립/체크섬/IEEE754/응답 파싱/%↔sccm 환산/상태비트/API 계약을
페이크 시리얼(장비 응답 시뮬)로 왕복 검증. 실제 pyserial/장비 불필요.
"""
import os
import struct
import sys

sys.path.insert(0, ".")

from hardware.gas.mfc_korea_mkp import (
    MFCKoreaMKP, MFCKoreaModbus, MFCProtocolError,
    _float_to_ascii, _ascii_to_float, _xor_checksum,
    CMD_WRITE_SP, CMD_READ_SP, CMD_READ_FLOW, CMD_READ_STATUS,
    CMD_READ_FULLSCALE, CMD_READ_UNIT, DT_FLOAT, DT_NODATA, DT_UCHAR,
)

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


# ══ 1. IEEE754 인코딩 (PDF 5.2 예제) ═══════════════════════════
# 100.00% → 0x42C80000
check("IEEE754 100.0→42C80000", _float_to_ascii(100.0) == "42C80000",
      _float_to_ascii(100.0))
# 170.383179 → 0x432A6218 (PDF 5.2.1)
check("IEEE754 170.383179→432A6218", _float_to_ascii(170.383179) == "432A6218",
      _float_to_ascii(170.383179))
# 13.0 → 0x41500000 (PDF 4.1.3 Read Flow)
check("IEEE754 13.0→41500000", _float_to_ascii(13.0) == "41500000",
      _float_to_ascii(13.0))
# 역변환 라운드트립
check("IEEE754 역변환", abs(_ascii_to_float("432A6218") - 170.383179) < 1e-3)


# ══ 2. 체크섬 (PDF 3.2.6 / 4.x 예제) ═══════════════════════════
m = MFCKoreaMKP("Mock_Port", slave_addr=1, max_sccm=100.0, name="T")

# PDF 3.2.6: ':017002 02' → checksum 0x3C  (Write Device ID addr=01 cmd=70 dt=02 data=02)
# 프레임 body = ':' '01' '70' '02' '02'
body = b":" + b"01" + b"70" + b"02" + b"02"
check("체크섬 PDF3.2.6 =0x3C", _xor_checksum(body) == 0x3C,
      f"0x{_xor_checksum(body):02X}")

# PDF 4.1.2 Read Set Point 요청 ':01 02 00' → checksum 0x39
f_rsp = m.build_frame(CMD_READ_SP, DT_NODATA, "")
check("Read SP 프레임 =':0102 0039\\r'", f_rsp == b":010200" + b"39" + b"\r",
      repr(f_rsp))

# PDF 4.1.1 Write Set Point 100% → 프레임 ':01 01 07 42C80000 40\r'
f_wsp = m.build_frame(CMD_WRITE_SP, DT_FLOAT, _float_to_ascii(100.0))
check("Write SP 100% 프레임 체크섬 40",
      f_wsp == b":01" + b"01" + b"07" + b"42C80000" + b"40" + b"\r", repr(f_wsp))

# PDF 4.1.3 Read Flow 요청 ':01 03 00 38\r'
f_rf = m.build_frame(CMD_READ_FLOW, DT_NODATA, "")
check("Read Flow 프레임 체크섬 38", f_rf == b":010300" + b"38" + b"\r", repr(f_rf))


# ══ 3. 페이크 시리얼 왕복 (응답 파싱 + %↔sccm) ═════════════════
class FakeSerial:
    """MKP 장비 응답을 시뮬 — 요청 프레임을 파싱해 규격 응답 프레임 생성."""

    def __init__(self, full_scale=100.0, flow_unit=2):
        self.full_scale = full_scale
        self.flow_unit = flow_unit
        self.sp_pct = 0.0
        self.flow_pct = 0.0
        self.status = 0x00
        self._out = b""

    def reset_input_buffer(self):
        pass

    def _resp(self, addr, cmd, dtype, data_ascii):
        rcmd = cmd | 0x80
        body = (b":" + f"{addr:02X}".encode() + f"{rcmd:02X}".encode()
                + f"{dtype:02X}".encode() + data_ascii.encode())
        return body + f"{_xor_checksum(body):02X}".encode() + b"\r"

    def write(self, frame):
        # 요청 파싱: ':' addr(2) cmd(2) dtype(2) data...
        buf = frame[:-1]                      # '\r' 제거
        addr = int(buf[1:3], 16)
        cmd = int(buf[3:5], 16)
        data = buf[7:-2].decode()             # data (체크섬 2자 제외)
        if cmd == CMD_WRITE_SP:
            self.sp_pct = _ascii_to_float(data)
            self.flow_pct = self.sp_pct        # 즉시 추종(시뮬)
            self._out = self._resp(addr, cmd, DT_FLOAT, _float_to_ascii(self.sp_pct))
        elif cmd == CMD_READ_SP:
            self._out = self._resp(addr, cmd, DT_FLOAT, _float_to_ascii(self.sp_pct))
        elif cmd == CMD_READ_FLOW:
            self._out = self._resp(addr, cmd, DT_FLOAT, _float_to_ascii(self.flow_pct))
        elif cmd == CMD_READ_FULLSCALE:
            self._out = self._resp(addr, cmd, DT_FLOAT, _float_to_ascii(self.full_scale))
        elif cmd == CMD_READ_UNIT:
            self._out = self._resp(addr, cmd, DT_UCHAR, f"{self.flow_unit:02X}")
        elif cmd == CMD_READ_STATUS:
            self._out = self._resp(addr, cmd, DT_UCHAR, f"{self.status:02X}")
        else:
            self._out = b""

    def read_until(self, expected):
        return self._out

    def close(self):
        pass


# max_sccm=50 인 MFC 에 페이크 시리얼 주입 (장비 FS=50 SCCM)
m2 = MFCKoreaMKP("COM_FAKE", slave_addr=1, max_sccm=50.0, name="N2")
m2._ser = FakeSerial(full_scale=50.0, flow_unit=2)
m2.is_connected = True

# set_flow(25 sccm) → 50% → 장비 sp_pct=50
m2.set_flow(25.0)
check("set_flow 25/50sccm → 장비 50%", abs(m2._ser.sp_pct - 50.0) < 1e-3,
      f"{m2._ser.sp_pct}%")
check("_sp 미러 = 25", abs(m2._sp - 25.0) < 1e-6)

# get_flow() → 50% → 25 sccm 환산
check("get_flow → 25 sccm", abs(m2.get_flow() - 25.0) < 1e-3, f"{m2.get_flow()}")

# read_setpoint() → 25 sccm
check("read_setpoint → 25 sccm", abs(m2.read_setpoint() - 25.0) < 1e-3)

# full scale / unit
check("read_full_scale → 50", abs(m2.read_full_scale() - 50.0) < 1e-3)
check("read_flow_unit → 2(SCCM)", m2.read_flow_unit() == 2)

# 클램프: max 초과 → max
m2.set_flow(999.0)
check("set_flow 클램프(999→50sccm=100%)", abs(m2._ser.sp_pct - 100.0) < 1e-3
      and abs(m2._sp - 50.0) < 1e-6, f"sp={m2._sp} pct={m2._ser.sp_pct}")

# 음수 → 0
m2.set_flow(-5.0)
check("set_flow 음수→0", abs(m2._sp) < 1e-9 and abs(m2._ser.sp_pct) < 1e-9)

# stop → 0
m2.set_flow(10.0)
m2.stop()
check("stop → 0 sccm", abs(m2._sp) < 1e-9)


# ══ 4. 상태 비트 파싱 (PDF 4.1.6) ══════════════════════════════
m2._ser.status = 0b00000101   # bit0 Power Over + bit2 Heater Power Low
errs = m2.read_status()
check("status 비트 파싱(PowerOver+HeaterLow)",
      len(errs) == 2 and any("Power Over" in e for e in errs)
      and any("Heater Power Low" in e for e in errs), str(errs))
m2._ser.status = 0x00
check("status 정상 → 빈 리스트", m2.read_status() == [])


# ══ 5. 체크섬/타임아웃 오류 승격 (fault-masking 금지) ═══════════
class BadChecksumSerial(FakeSerial):
    def write(self, frame):
        super().write(frame)
        # 체크섬 1바이트 훼손
        if self._out:
            b = bytearray(self._out)
            b[-2] ^= 0x01   # 체크섬 문자 훼손
            self._out = bytes(b)


m3 = MFCKoreaMKP("COM_FAKE", max_sccm=100.0, name="Bad")
m3._ser = BadChecksumSerial()
m3.is_connected = True
try:
    m3.get_flow()
    check("체크섬 오류 → 예외", False, "예외 안 남")
except MFCProtocolError:
    check("체크섬 오류 → MFCProtocolError", True)


class TimeoutSerial(FakeSerial):
    def read_until(self, expected):
        return b""   # 타임아웃 (빈 응답)


m4 = MFCKoreaMKP("COM_FAKE", max_sccm=100.0, name="TO")
m4._ser = TimeoutSerial()
m4.is_connected = True
try:
    m4.get_flow()
    check("타임아웃 → 예외", False, "예외 안 남")
except MFCProtocolError:
    check("타임아웃 → MFCProtocolError", True)


# ══ 6. Mock 모드 (포트 없음) — 시리얼 없이 _sp 미러 ═════════════
mm = MFCKoreaMKP("Mock_Port", max_sccm=100.0, name="Mock")
mm.connect()
check("Mock connect → is_connected", mm.is_connected is True)
mm.set_flow(33.0)
check("Mock set_flow → _sp/get_flow 미러", abs(mm.get_flow() - 33.0) < 1e-6)
check("Mock read_status → []", mm.read_status() == [])


# ══ 7. 하위호환 별칭 + factory 매핑 ════════════════════════════
check("MFCKoreaModbus 별칭 = MFCKoreaMKP", MFCKoreaModbus is MFCKoreaMKP)
from hardware.factory import HardwareFactory
drv = HardwareFactory.get_available_drivers()
check("factory 새 라벨 MKP RS485", "질소 MFC (MKP RS485)" in drv)
check("factory 신 라벨 매핑",
      HardwareFactory.get_driver_type("질소 MFC (MKP RS485)") == "MFCKoreaMKP")
check("factory 구 라벨 back-compat",
      HardwareFactory.get_driver_type("질소 MFC (Modbus)") == "MFCKoreaMKP")


print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
