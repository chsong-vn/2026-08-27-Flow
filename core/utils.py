import serial
import serial.tools.list_ports
import time


# ──────────────────────────────────────────────────────────────
# Protocol probes — 같은 VID:PID를 쓰는 장치 구분용 핸드셰이크
# 현재 사용처: CH340(1A86:7523)이 Chemyx 펌프 버스와 Runze 12-way
# 밸브 버스에 공통으로 쓰여서 VID/PID만으로는 구분 불가
# ──────────────────────────────────────────────────────────────

def probe_chemyx(com: str, timeout: float = 1.5) -> bool:
    """ID 1 Chemyx 펌프가 응답하는지 확인. `view parameter` 조회만 보냄 → 기계 미동작."""
    try:
        with serial.Serial(com, 9600, timeout=timeout) as ser:
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.write(b'1 view parameter\r\n')
            time.sleep(0.4)
            resp = ser.read(500).lower()
            return b'dia' in resp and b'rate' in resp
    except Exception:
        return False


def probe_runze(com: str, addr: int = 1, timeout: float = 0.8) -> bool:
    """Runze 12-way valve가 응답하는지 확인. 위치 조회만 보냄 → 기계 미동작."""
    try:
        frame = bytes([0xCC, addr, 0x3E, 0x00, 0x00, 0xDD])
        cksum = sum(frame).to_bytes(2, 'little')
        with serial.Serial(com, 9600, timeout=timeout) as ser:
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.write(frame + cksum)
            time.sleep(0.3)
            resp = ser.read(16)
            return len(resp) >= 6 and resp[0] == 0xCC and resp[5] == 0xDD
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
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.write(b"PR\r")
            time.sleep(0.4)
            resp = ser.read(64).decode(errors='ignore')
            return resp.startswith("OK,") and "/" in resp
    except Exception:
        return False


_PROBE_REGISTRY = {
    "chemyx": probe_chemyx,
    "runze": probe_runze,
    "reaxus": probe_reaxus,
}


def find_port_by_usb_info(vid=None, pid=None, serial_number=None, probe=None):
    """
    VID/PID/Serial Number로 COM 포트를 자동 검색합니다.

    @codesyncer-decision: USB 장치 자동 매칭 우선순위
      1. vid + pid + serial_number → 정확한 장치 특정
      2. vid + pid (serial_number 없음) → 해당 장치 타입 중 첫 번째
      3. 매칭 실패 → None 반환 (fallback으로 하드코딩 포트 사용)

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

    # VID/PID 매칭 장치가 하나뿐이면 그대로 사용
    if len(candidates) == 1:
        return candidates[0].device

    # 여러 개 매칭 + probe 지정 → probe로 확정
    if probe:
        probe_fn = _PROBE_REGISTRY.get(probe)
        if probe_fn:
            for p in candidates:
                if probe_fn(p.device):
                    return p.device
            return None
        # 알 수 없는 probe 종류 → 첫 번째로 폴백
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