import serial
import serial.tools.list_ports
import time


# ──────────────────────────────────────────────────────────────
# Protocol probes — 같은 VID:PID를 쓰는 장치 구분용 핸드셰이크
# 현재 사용처: CH340(1A86:7523)이 Chemyx 펌프 버스와 Runze 12-way
# 밸브 버스에 공통으로 쓰여서 VID/PID만으로는 구분 불가
# ──────────────────────────────────────────────────────────────

# @codesyncer-decision(2026-08-12, 프로브 폭풍 수정): 프로브는 2회 시도한다.
#   CH340 을 직전에 다른 프로브가 열었다 닫으면 핸들 해제 지연 + 앞 프로브가
#   남긴 이종 프로토콜 바이트(예: Runze 밸브에 Chemyx ASCII)가 첫 조회를
#   깨뜨린다. open 직후 in/out 버퍼를 모두 비우고, 매 시도마다 입력을 다시
#   비운 뒤 조회 → 잔류 1회는 흡수. 그래도 근본 완화는 config 의 시그니처/
#   포트 분류 캐시(프로브 자체를 8→2회로 축소)가 담당한다.
def probe_chemyx(com: str, timeout: float = 1.5) -> bool:
    """ID 1 Chemyx 펌프가 응답하는지 확인. `view parameter` 조회만 보냄 → 기계 미동작."""
    try:
        with serial.Serial(com, 9600, timeout=timeout) as ser:
            time.sleep(0.15)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            for _ in range(2):
                ser.reset_input_buffer()
                ser.write(b'1 view parameter\r\n')
                time.sleep(0.4)
                resp = ser.read(500).lower()
                if b'dia' in resp and b'rate' in resp:
                    return True
            return False
    except Exception:
        return False


def probe_runze(com: str, addr: int = 1, timeout: float = 0.8) -> bool:
    """Runze 12-way valve가 응답하는지 확인. 위치 조회만 보냄 → 기계 미동작."""
    try:
        frame = bytes([0xCC, addr, 0x3E, 0x00, 0x00, 0xDD])
        cksum = sum(frame).to_bytes(2, 'little')
        with serial.Serial(com, 9600, timeout=timeout) as ser:
            time.sleep(0.15)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            for _ in range(2):
                ser.reset_input_buffer()
                ser.write(frame + cksum)
                time.sleep(0.3)
                resp = ser.read(16)
                if len(resp) >= 6 and resp[0] == 0xCC and resp[5] == 0xDD:
                    return True
            return False
    except Exception:
        return False


def probe_reaxus(com: str, timeout: float = 1.2) -> bool:
    """Reaxus HPLC pump 응답 확인. PR(pressure read)만 보냄 → 기계 미동작.

    응답 형식: "OK,<pressure>/" (ASCII)
    - OK, 접두사 + / 종료자로 안전하게 판별
    - Chemyx(ASCII, 다른 포맷) / Runze(바이너리) 와 구분 가능
    """
    try:
        with serial.Serial(com, 9600, timeout=timeout) as ser:
            time.sleep(0.15)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            for _ in range(2):
                ser.reset_input_buffer()
                ser.write(b"PR\r")
                time.sleep(0.4)
                resp = ser.read(64).decode(errors='ignore')
                if resp.startswith("OK,") and "/" in resp:
                    return True
            return False
    except Exception:
        return False


def probe_opb(com: str, timeout: float = 0.5, boot_wait: float = 3.2) -> bool:
    """OPB 위상센서 리그 응답 확인 — 수신 전용(listen-only), 바이트 전송 없음.

    @codesyncer-decision(2026-08-24, USB 재열거로 OPB/MFC 사망 재발 방지):
      보드는 포트 오픈(DTR 리셋) 후 스케치 부팅 ~2s 를 거쳐 라벨포맷
      'S1:adc,판정 | S2:adc,판정' (구 펌웨어는 CSV 숫자,숫자,...) 를 상시
      스트림한다 → 열고 기다렸다 읽기만 하면 판별 가능. Chemyx/Runze/MKP 는
      무전송 시 침묵이므로 오탐 없음. boot_wait 미만 대기는 부팅 전이라
      0 byte 로 오판(실측: 1.2s 실패 / 3.5s 성공, 19.8Hz 스트림).
    """
    try:
        with serial.Serial(com, 115200, timeout=timeout) as ser:
            time.sleep(boot_wait)          # CH340 DTR 리셋 → 스케치 부팅 대기
            ser.reset_input_buffer()
            time.sleep(0.5)
            data = ser.read(ser.in_waiting or 64).decode(errors="ignore")
            if "S1:" in data or "S2:" in data:
                return True
            # 구 CSV 펌웨어 폴백: '숫자,숫자' 로 시작하는 라인 스트림
            for line in data.splitlines():
                head = line.split(",", 1)[0].strip()
                if "," in line and head.isdigit():
                    return True
            return False
    except Exception:
        return False


def probe_mkp(com: str, timeout: float = 0.6) -> bool:
    """MKP MFC 응답 확인. Read Set Point(0x02) 조회만 보냄 → 기계 미동작.

    프레임 ':'+Addr(2)+Cmd(2)+DataType(2)+Checksum(2)+'\\r' (Hex-ASCII),
    9600/8/ODD/1 (mfc_korea_mkp.py 와 동일 규격). 응답 검증 3중
    (':' 정렬 + XOR 체크섬 + 응답 Cmd&0x7F==0x02) 이라 이종 장치 오탐 없음.
    현장 slave_addr 기본 0, 벤더 기본 1 — 둘 다 시도.
    """
    def _frame(addr, cmd):
        body = b":" + f"{addr & 0xFF:02X}{cmd & 0xFF:02X}00".encode("ascii")
        chk = 0
        for b in body:
            chk ^= b
        return body + f"{chk & 0xFF:02X}".encode("ascii") + b"\r"

    try:
        with serial.Serial(com, 9600, bytesize=serial.EIGHTBITS,
                           parity=serial.PARITY_ODD,
                           stopbits=serial.STOPBITS_ONE, timeout=timeout) as ser:
            time.sleep(0.15)
            for addr in (0, 1):
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.write(_frame(addr, 0x02))
                resp = ser.read_until(b"\r", 64)
                si = resp.find(b":")
                if si < 0 or not resp.endswith(b"\r"):
                    continue
                buf = resp[si:-1]
                if len(buf) < 9:
                    continue
                payload, rx_chk = buf[:-2], buf[-2:]
                chk = 0
                for b in payload:
                    chk ^= b
                try:
                    resp_cmd = int(payload[3:5], 16)
                except ValueError:
                    continue
                if (f"{chk:02X}".encode("ascii") == rx_chk.upper()
                        and (resp_cmd & 0x7F) == 0x02):
                    return True
            return False
    except Exception:
        return False


_PROBE_REGISTRY = {
    "chemyx": probe_chemyx,
    "runze": probe_runze,
    "reaxus": probe_reaxus,
    "opb": probe_opb,
    "mkp": probe_mkp,
}


def find_port_by_usb_info(vid=None, pid=None, serial_number=None, probe=None,
                          class_cache=None):
    """
    VID/PID/Serial Number로 COM 포트를 자동 검색합니다.

    @codesyncer-decision: USB 장치 자동 매칭 우선순위
      1. vid + pid + serial_number → 정확한 장치 특정
      2. vid + pid (serial_number 없음) → 해당 장치 타입 중 첫 번째
      3. 매칭 실패 → None 반환 (fallback으로 하드코딩 포트 사용)

    @codesyncer-decision(2026-08-12, 프로브 폭풍 수정): class_cache(dict)를 주면
      포트 분류 결과({포트: probe_이름})를 읽고 쓴다. 같은 CH340 VID/PID 를 공유하는
      Chemyx/Runze 버스가 부팅 때 서로의 포트를 반복해서 열어 프로브가 상호 오염되던
      결함(로그: 살아있는 Runze COM9 조차 'Auto match failed')을 막는다:
      - 이미 이 probe 로 분류된 포트가 있으면 재프로브 없이 반환.
      - 다른 probe 로 이미 확정된 포트는 건너뛴다(이종 프로토콜 바이트 재주입 금지).
      호출측(config.get_device_info)이 부팅 1회 스캔 동안 캐시를 공유한다.

    @codesyncer-inference: pyserial list_ports 의존성 가정
      - 가정: pyserial >= 3.0 설치됨 (serial.tools.list_ports 사용)
      - 가정: OS에서 USB 장치 정보 조회 권한 있음
      - 검증: 시스템 부팅 시 list_all_usb_ports() 호출하여 장치 목록 확인

    @codesyncer-risk: USB 포트 검색 관련 위험
      - pyserial < 3.0: serial.tools.list_ports 미지원 → ImportError 발생 가능
      - Windows: 일부 USB 장치는 VID/PID 정보 누락 (port.vid = None)
      - 권한: Linux에서 /dev/ttyUSB* 접근 시 dialout 그룹 필요
      - 완화책: None 반환 시 fallback port 사용 (config.py에서 처리)

    Args:
        vid: USB Vendor ID (예: "0403", "1A86")
        pid: USB Product ID (예: "6001", "7523")
        serial_number: USB Serial Number (고유 식별자)

    Returns:
        str: COM 포트 이름 (예: "COM7") 또는 None
    """
    if not vid or not pid:
        return None

    try:
        # VID/PID를 정수로 변환 (16진수 문자열 지원)
        vid_int = int(vid, 16) if isinstance(vid, str) else vid
        pid_int = int(pid, 16) if isinstance(pid, str) else pid
    except ValueError:
        return None

    ports = serial.tools.list_ports.comports()
    candidates = [p for p in ports if p.vid == vid_int and p.pid == pid_int]
    if not candidates:
        return None

    if serial_number:
        for p in candidates:
            if p.serial_number == serial_number:
                return p.device
        return None

    # @codesyncer-decision(2026-08-12): probe 가 지정되면 후보가 하나여도 반드시
    #   프로브로 종류를 확인한다. 기존엔 'len==1 이면 무조건 반환'이 먼저였는데,
    #   Chemyx 버스가 빠지고 CH340 이 Runze 하나만 남으면 Chemyx 조회가 그 Runze
    #   포트를 잘못 잡던 오매칭(같은 VID/PID) 결함. 프로브 실패 시 None → static 폴백.
    probe_fn = _PROBE_REGISTRY.get(probe) if probe else None
    if probe and not probe_fn:
        # 알 수 없는 probe 종류 → 확인 불가, 첫 번째로 폴백 (기존 동작 유지)
        return candidates[0].device
    if probe_fn:
        # 1) 이미 이 probe 로 분류된 포트가 있으면 재프로브 없이 반환
        if class_cache is not None:
            for p in candidates:
                if class_cache.get(p.device) == probe:
                    return p.device
        # 2) 미분류 포트만 프로브 (다른 프로토콜로 확정된 포트는 건너뜀)
        for p in candidates:
            dev = p.device
            if class_cache is not None and class_cache.get(dev) is not None:
                continue   # 이미 다른 타입 확정 — 이종 바이트 재주입 금지
            if probe_fn(dev):
                if class_cache is not None:
                    class_cache[dev] = probe
                return dev
        return None

    # probe 미지정 — VID/PID 매칭 장치가 하나뿐이면 그대로 사용
    if len(candidates) == 1:
        return candidates[0].device

    # 여러 개 매칭 + probe 없음 → Serial 없는 것 우선 (기존 동작 유지와 유사)
    no_serial = [p for p in candidates if not p.serial_number]
    if no_serial:
        return no_serial[0].device
    return candidates[0].device


def list_all_usb_ports():
    """
    현재 연결된 모든 USB 시리얼 장치 정보를 반환합니다.
    하드웨어 설정 다이얼로그에서 VID/PID/Serial 확인용.

    Returns:
        list: [{"port": "COM7", "vid": "0403", "pid": "6001", "serial": "A12345", "desc": "..."}]
    """
    result = []
    ports = serial.tools.list_ports.comports()

    for port in ports:
        info = {
            "port": port.device,
            "vid": f"{port.vid:04X}" if port.vid else None,
            "pid": f"{port.pid:04X}" if port.pid else None,
            "serial": port.serial_number,
            "desc": port.description,
            "manufacturer": port.manufacturer
        }
        result.append(info)

    return result


class TextRedirector(object):
    def __init__(self, signal): self.signal = signal
    def write(self, text): 
        if text.strip(): self.signal.emit(str(text))
    def flush(self): pass

class SystemMapManager:
    def __init__(self, pumps):
        self.inlet_map = {}
        for p in pumps:
            self.inlet_map[p] = {}
            self.inlet_map[p][1] = {"name": "세척 용매 (Solvent)", "conc": 0.0, "smiles": ""}
            for i in range(2, 12): self.inlet_map[p][i] = {"name": "비어있음", "conc": 1.0, "smiles": ""}
            self.inlet_map[p][12] = {"name": "폐기 (Waste)", "conc": 0.0, "smiles": ""}
    def update_inlet(self, pump, port, name, conc, smiles=None):
        # @codesyncer-decision: smiles=None 이면 기존 구조식 보존 (이름/농도만 바꾸는
        #   기존 호출부가 SMILES 를 지우지 않도록). 배관도 inlet 구조식 소스.
        cur = self.inlet_map.get(pump, {}).get(port, {})
        self.inlet_map[pump][port] = {
            "name": name, "conc": conc,
            "smiles": cur.get("smiles", "") if smiles is None else smiles,
        }
    def get_inlet(self, pump, port):
        return self.inlet_map[pump].get(port, {"name":"알 수 없음", "conc":1.0, "smiles":""})