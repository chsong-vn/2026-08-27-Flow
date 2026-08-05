# --- START OF FILE hardware/collectors/collector_plate96.py ---
"""
96-Well Plate Fraction Collector (3D Printer / Marlin G-code based)
====================================================================
NEOSIS N3D N200 + 분취기 지그 + 96-well 딥웰 플레이트 2장.

- 통신: USB Serial (250000 baud), Marlin 2.1.2.5 (FlowChem Fraction Collector)
- 좌표: well_coordinates.json (실측 보정값 포함)
- 인터페이스: ColosseumCollector와 호환 (move_to_tube 1-based, 1~192)

# @codesyncer-decision: 기존 ColosseumCollector 인터페이스 100% 호환
#   - move_to_tube(n): 1~96 = Plate A, 97~192 = Plate B (snake order)
#   - cumulative_steps: 사용 안 하지만 호환용 빈 리스트 유지
"""
import serial
import time
import os
import json
import threading


class Plate96Collector:
    """96-well plate (Plate A 96 + Plate B 96 = 192) 분취기"""

    def __init__(self):
        self.ser = None
        self.is_connected = False
        self._serial_lock = threading.Lock()  # 시리얼 경합 방지
        self.current_position = 0       # 0=홈, 1~192=well 인덱스
        self.total_tubes = 192          # 192개 well 호환
        self.cumulative_steps = []      # 호환용 (Marlin은 절대좌표라 미사용)
        # @codesyncer-decision: 모션 확인 플래그 (2026-07-06 감사 F2)
        # M400 무응답이면 실제 위치 불확실 → 다음 이동의 same_row Z-리프트
        # 생략을 금지 (오판 시 니들이 분주 높이로 웰 사이를 긁는 사고 방지)
        self._motion_confirmed = True

        # well_coordinates.json 경로
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.coords_path = os.path.join(current_dir, 'data', 'well_coordinates.json')
        self.coords = None
        self.well_sequence = []   # snake order로 정렬된 well 리스트

        # Z 레벨 (well_coordinates.json의 frame.z_levels에서 로드)
        self.z_travel = 29.0
        self.z_dispense = 27.0
        self.feedrate_xy = 6000   # mm/min (XY는 빠르게)
        self.feedrate_z = 1200    # mm/min (Z도 가속)
        # 한 well당 최대 분주 가능 부피 (mL) — engine 검증용
        self.max_volume_per_well_ml = 1.5

        self.reload_data()

    # ─────────────────────────────────────
    # 데이터 로드
    # ─────────────────────────────────────
    def reload_data(self):
        """well_coordinates.json 다시 읽기"""
        try:
            with open(self.coords_path, 'r', encoding='utf-8') as f:
                self.coords = json.load(f)
        except Exception as e:
            print(f"[Plate96] coords load failed: {e}")
            self.total_tubes = 0
            self.coords = None
            return

        # Z 레벨 적용
        zlv = self.coords.get('frame', {}).get('z_levels', {})
        self.z_travel = zlv.get('z_travel', 29.0)
        self.z_dispense = zlv.get('z_dispense', 27.0)

        # snake order: A1→A12, B12→B1, ..., 그 다음 Plate B
        self.well_sequence = []
        for plate in ['A', 'B']:
            plate_wells = [w for w in self.coords['wells'] if w['plate'] == plate]
            for r in range(8):
                row = sorted([w for w in plate_wells if w['row_idx'] == r],
                             key=lambda w: w['col_idx'])
                if r % 2 == 1:
                    row.reverse()
                self.well_sequence.extend(row)

        self.total_tubes = len(self.well_sequence)
        # 호환용 cumulative_steps (실제 사용 안 됨)
        self.cumulative_steps = list(range(self.total_tubes + 1))
        self.current_position = 0
        print(f"[Plate96] Loaded {self.total_tubes} wells (Plate A + B, snake order)")

    # ─────────────────────────────────────
    # 인터페이스 (ColosseumCollector 호환)
    # ─────────────────────────────────────
    def get_position(self):
        """현재 well 인덱스 (0=홈, 1~192)"""
        return self.current_position

    def get_well_id(self, idx=None):
        """인덱스 → 'A_A1' 같은 well ID"""
        if idx is None:
            idx = self.current_position
        if idx == 0:
            return "HOME"
        if 1 <= idx <= self.total_tubes:
            w = self.well_sequence[idx - 1]
            return f"{w['plate']}_{w['well']}"
        return "?"

    def connect(self, port):
        """Marlin 보드 연결 (250000 baud)"""
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=250000,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=3.0
            )
            # Arduino reset
            self.ser.dtr = False
            time.sleep(0.2)
            self.ser.dtr = True
            time.sleep(4.0)
            self.ser.reset_input_buffer()

            # 핸드셰이크
            resp = self._send_wait('M115', wait=3.0)
            if 'FIRMWARE_NAME' not in resp:
                self.disconnect()
                return False, f"No firmware response: {resp[:60]}"

            # 절대 모드 설정
            self._send_wait('G90', wait=1.0)

            self.is_connected = True
            self.current_position = 0
            print(f"[Plate96] Connected on {port} ({self.total_tubes} wells)")
            return True, f"Connected ({self.total_tubes} wells)"
        except Exception as e:
            self.is_connected = False
            return False, str(e)

    def disconnect(self):
        if self.ser and self.ser.is_open:
            try:
                self.stop_motion()
                self._send_wait('M84', wait=1.0)  # 모터 OFF
            except Exception:
                pass
            self.ser.close()
        self.is_connected = False

    def move_to_tube(self, tube_idx):
        """
        1-based well 인덱스로 이동 (snake order)
        1~96   = Plate A (A1→A12, B12→B1, ...)
        97~192 = Plate B

        # @codesyncer-decision: M400으로 실제 모션 완료까지 동기 대기
        #   → 엔진의 tube_sec 타이밍과 정확히 일치 (overflow 방지)
        # @codesyncer-decision: 같은 행 안에서는 Z 리프트 생략 (snake 최적화)
        #   → XY만 이동하면 시간 ~50% 단축
        """
        if not self.is_connected:
            return False, "Not Connected"
        if not (1 <= tube_idx <= self.total_tubes):
            return False, f"Invalid index: {tube_idx} (max {self.total_tubes})"

        w = self.well_sequence[tube_idx - 1]
        wid = f"{w['plate']}_{w['well']}"
        x, y = w['machine_X'], w['machine_Y']

        # 같은 행/같은 plate 안에서 이동인지 판단 (Z 리프트 생략)
        # — 직전 모션이 M400 으로 확인된 경우에만 (위치 불확실 시 강제 풀 리프트)
        same_row = False
        if self._motion_confirmed and 1 <= self.current_position <= self.total_tubes:
            prev = self.well_sequence[self.current_position - 1]
            same_row = (prev['plate'] == w['plate'] and prev['row_idx'] == w['row_idx'])

        # LCD 표시
        self._send_wait(f"M117 96well {wid} ({tube_idx}/{self.total_tubes})", wait=0.3)

        if same_row:
            # 같은 행: Z 유지하며 XY만 이동
            self._send_wait(f"G1 X{x} Y{y} F{self.feedrate_xy}", wait=2.0)
        else:
            # 다른 행 또는 plate: Z 안전 높이로 상승 → XY → Z 분주 높이
            self._send_wait(f"G1 Z{self.z_travel} F{self.feedrate_z}", wait=2.0)
            self._send_wait(f"G1 X{x} Y{y} F{self.feedrate_xy}", wait=4.0)
            self._send_wait(f"G1 Z{self.z_dispense} F{self.feedrate_z}", wait=2.0)

        # ★ M400: planner buffer 비우고 실제 모션 완료까지 블로킹
        resp = self._send_wait("M400", wait=10.0)
        # 절대좌표 명령이므로 목표 인덱스가 최선 추정 (웰 라벨링/다음 목표 계산용)
        self.current_position = tube_idx
        if not self._resp_ok(resp):
            # F2(2026-07-06): 모션 완료 미확인 — 성공으로 가장하지 않는다.
            # (2026-07-29 확장: 무응답뿐 아니라 Error/kill 텍스트도 미확인 처리)
            self._motion_confirmed = False
            reason = "M400 timeout" if not resp else f"M400 error: {resp[:60]}"
            print(f"[Plate96] WARNING: {reason} for tube {tube_idx} — "
                  f"위치 미확인, 다음 이동은 풀 Z리프트")
            return False, f"{reason} at {wid} (position unconfirmed)"

        self._motion_confirmed = True
        print(f"[Plate96] tube {tube_idx} = {wid} at ({x}, {y})")
        return True, f"At {wid}"

    def move_to_next(self):
        if self.current_position < self.total_tubes:
            return self.move_to_tube(self.current_position + 1)
        return False, "Already at last well"

    def home(self):
        """전체 호밍 (G28) — XYZ 모두 절대 원점으로"""
        if not self.is_connected:
            return False, "Not Connected"
        # Z 안전 높이로 먼저 상승
        try:
            self._send_wait(f"G1 Z{self.z_travel} F{self.feedrate_z}", wait=4.0)
        except Exception:
            pass
        print("[Plate96] G28 homing start...")
        resp = self._send_wait("G28", wait=60.0)
        print(f"[Plate96] G28 response: '{resp[:120] if resp else '(empty)'}'")
        # @codesyncer(적대검증 F-1, 2026-07-29): G28 도 응답 검증 — 무응답/Error/kill 은
        #   좌표계 미확정이므로 성공으로 가장하지 않는다(엔드스톱 미접촉·케이블 탈락 시
        #   전 시퀀스가 어긋난 절대좌표로 이동하던 fault-masking 제거).
        if not self._resp_ok(resp):
            self._motion_confirmed = False
            print("[Plate96] WARNING: G28 미확인/오류 — 위치 불확정")
            return False, f"G28 failed/unconfirmed: '{(resp or '(no response)')[:80]}'"
        # 호밍 후 다시 안전 높이
        self._send_wait(f"G1 Z{self.z_travel} F{self.feedrate_z}", wait=4.0)
        self._send_wait("M117 HOMED", wait=0.5)
        self.current_position = 0
        self._motion_confirmed = True   # 호밍 = 위치 확정
        print("[Plate96] Homed (G28)")
        return True, "Home complete"

    def stop_motion(self):
        """즉시 정지 — M410(quickstop) 사용, Marlin halt 방지
        @codesyncer-decision: M112 대신 M410 사용
        - M112: Marlin halt → 이후 모든 G-code 무시, 보드 리셋 필요
        - M410: quickstop → 모터 즉시 정지, Marlin은 정상 상태 유지
        - halt 복구(M999) 후에도 위치 정보 손실 위험 → M410이 안전
        """
        if self.is_connected and self.ser and self.ser.is_open:
            # @codesyncer(적대검증 F-8, 2026-07-29): quickstop = 진행 중 이동이 목표
            #   도달 전에 끊김 — 물리 위치·Z 높이 모두 미지. 미확인 플래그를 세워
            #   다음 이동의 same_row Z-생략을 금지한다 (Manual STOP 후 긁힘 방지).
            self._motion_confirmed = False
            try:
                self.ser.write(b"M410\n")
                self.ser.flush()
                time.sleep(0.1)
                # 모터 정지 확인 후 현재 위치 동기화
                self.ser.write(b"M114\n")
                self.ser.flush()
                print("[Plate96] Quick stop (M410)")
            except Exception:
                pass

    # ─────────────────────────────────────
    # 신규 메서드 (96웰 전용 기능)
    # ─────────────────────────────────────
    def move_to_well(self, plate, well):
        """('A', 'A1') 같이 plate + well_id로 직접 이동"""
        for i, w in enumerate(self.well_sequence, 1):
            if w['plate'].upper() == plate.upper() and w['well'].upper() == well.upper():
                return self.move_to_tube(i)
        return False, f"Well {plate}_{well} not found"

    def move_to_wash(self):
        """WASH 포트로 이동 (well_coordinates.json의 wash_positions 사용)"""
        if not self.is_connected:
            return False, "Not Connected"
        if not self.coords:
            return False, "No coordinates loaded"
        wash_list = self.coords.get('wash_positions', [])
        if not wash_list:
            return False, "No wash position defined"
        wp = wash_list[0]
        x, y = wp['X'], wp['Y']
        z_dip = wp.get('Z_dip', self.z_dispense)
        self._send_wait(f"M117 WASH ({x},{y})", wait=0.3)
        self._send_wait(f"G1 Z{self.z_travel} F{self.feedrate_z}", wait=2.0)
        self._send_wait(f"G1 X{x} Y{y} F{self.feedrate_xy}", wait=4.0)
        self._send_wait(f"G1 Z{z_dip} F{self.feedrate_z}", wait=2.0)
        resp = self._send_wait("M400", wait=10.0)
        # WASH 는 well_sequence 밖 — current_position(마지막 웰) 부기와 물리 위치가
        # 어긋나므로 다음 웰 이동의 same_row Z-생략을 금지 (긁힘 방지)
        self._motion_confirmed = False
        # @codesyncer(적대검증 F-4, 2026-07-29): M400 미확인이면 니들이 WASH 에 도착
        #   했다고 보고하지 않는다 — compensated 파킹 게이트가 실패를 볼 수 있게.
        if not self._resp_ok(resp):
            print(f"[Plate96] WARNING: WASH 이동 M400 미확인 — 위치 불확정")
            return False, f"WASH move unconfirmed (M400: '{(resp or 'timeout')[:60]}')"
        print(f"[Plate96] WASH at ({x}, {y}, Z{z_dip})")
        return True, f"At WASH ({x}, {y})"

    def goto_safe_height(self):
        """Z를 travel 높이로 상승"""
        if not self.is_connected:
            return False, "Not Connected"
        self._send_wait(f"G1 Z{self.z_travel} F{self.feedrate_z}", wait=4.0)
        # @codesyncer(적대검증 F-13, 2026-07-29): Z 가 dispense(24)→travel(30)로 이탈
        #   — same_row 분기는 Z 명령이 없어, 플래그를 유지하면 다음 같은 행 이동이
        #   6mm 상공에서 분주(스플래시/교차오염). move_to_wash 와 동일하게 해제해
        #   다음 이동을 풀 Z리프트(→ z_dispense 복귀) 경로로 강제한다.
        self._motion_confirmed = False
        return True, f"Z={self.z_travel}"

    # ─────────────────────────────────────
    # 시리얼 헬퍼
    # ─────────────────────────────────────
    @staticmethod
    def _resp_ok(resp):
        """Marlin 응답 성공 판정 — 'ok' 라인 수신 + Error/kill 부재.

        @codesyncer(적대검증 F-1, 2026-07-29): _send_wait 는 타임아웃이든 에러 텍스트든
        누적 문자열을 그대로 반환해 'Error:Printer halted. kill() called!' 도 비어있지
        않다는 이유로 성공 취급되던 fault-masking 의 뿌리. 성공 = ok 수신이 유일 기준."""
        if not resp:
            return False
        low = resp.lower()
        if "error" in low or "kill" in low:
            return False
        return any(l.strip().lower().startswith("ok") for l in resp.splitlines())

    def _send_wait(self, cmd, wait=4.0):
        """G-code 전송 후 ok 응답 기다림 (thread-safe)"""
        if not self.ser or not self.ser.is_open:
            return ""
        with self._serial_lock:
            self.ser.reset_input_buffer()
            self.ser.write((cmd + '\n').encode())
            self.ser.flush()
            deadline = time.time() + wait
            data = b''
            while time.time() < deadline:
                if self.ser.in_waiting:
                    data += self.ser.read(self.ser.in_waiting)
                    text = data.decode('utf-8', errors='replace')
                    lines = [l for l in text.strip().splitlines() if l.strip()]
                    if lines and lines[-1].strip().lower().startswith('ok'):
                        deadline = min(deadline, time.time() + 0.15)
                else:
                    time.sleep(0.03)
            return data.decode('utf-8', errors='replace').strip()


# 단독 테스트용
if __name__ == "__main__":
    c = Plate96Collector()
    print(f"Total wells: {c.total_tubes}")
    print(f"Z travel: {c.z_travel}, dispense: {c.z_dispense}")
    print(f"First well: {c.get_well_id(1)}")
    print(f"Last well: {c.get_well_id(192)}")
    print(f"Mid well: {c.get_well_id(96)}")
