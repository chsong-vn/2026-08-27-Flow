# --- START OF FILE hardware/collectors/collector_colosseum.py ---

import serial
import time
import os
import sys

class ColosseumCollector:
    def __init__(self):
        self.ser = None
        self.is_connected = False
        self.total_tubes = 0
        self.angles = []              # 각 튜브 간 상대 이동 스텝 (원본 데이터)
        self.cumulative_steps = []    # 튜브별 누적 스텝 (절대 위치)
        self.current_position = 0     # 현재 튜브 위치 (0 = 홈, 1 = 첫 번째 튜브)

        # @codesyncer-decision: 마이크로스테핑 배율 보정
        # 캘리브레이션 결과 (2026-02-27): full step 기준 0.27 최적값
        # 2026-03-03: CNC Shield M1 점퍼 연결 → 1/4 step 모드로 변경
        # - full step 0.27 × 4 = 1.08 → angles.txt 설계 기준값 1.0으로 시작
        # 2026-03-03: CNC Shield M1+M3 점퍼 연결 → 1/32 step 모드로 변경
        # - 1/4 step 기준 1.0 × 8 = 8.0
        # - 재캘리브레이션 필요
        self.step_scale = 8.0
        # @codesyncer-decision: 구간별 비율 보정 (비활성)
        self.step_compensation_table = [
            (999, 1.0),
        ]
        # @codesyncer-decision: 고정 백래쉬 보정 (step 단위)
        # 1/32 step 모드 기준, 보정 없이 시작 (Run 10부터 재캘리브레이션)
        self.backlash_steps = 0

        # [수정] 파일 경로를 현재 스크립트 위치 기준으로 절대 경로 설정
        # 구조: hardware/collectors/collector_colosseum.py -> hardware/collectors/data/angles.txt
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.angles_path = os.path.join(current_dir, 'data', 'angles.txt')

        # 데이터 로드 시도
        self.reload_data()

        # 초기 설정 명령어 (가속도, 속도 설정)
        # 2026-03-03: M1+M3 점퍼 → 1/32 step 모드. Full step 기준값 × 8 적용
        self.SETUP_CMDS = [
            "<SET_ACCEL,111,640.0,640.0,640.0>",
            "<SET_SPEED,111,1600.0,1600.0,1600.0>",
        ]
        # 정지 명령어
        self.STOP_CMD = "<STOP,111,0.0,0.0,0.0>"

    def reload_data(self):
        """데이터 파일 다시 읽기 및 누적 스텝 계산"""
        self.angles = self.read_angles_from_file(self.angles_path)
        self.total_tubes = len(self.angles)

        # 누적 스텝 계산 (float 정밀도 유지, 반올림은 누적값 기준)
        # @codesyncer-decision: step_scale + 고정 백래쉬 보정
        # float 누적 후 round → 개별 반올림보다 누적 오차 최소화
        # 매 튜브 이동마다 backlash_steps를 추가하여 기계적 유격 보상
        self.cumulative_steps = [0]  # 홈 위치 = 0
        total_float = 0.0
        backlash = getattr(self, 'backlash_steps', 0)
        for idx, angle in enumerate(self.angles):
            tube_num = idx + 1  # 1-based
            comp = self._get_compensation(tube_num)
            total_float += angle * self.step_scale * comp + backlash
            self.cumulative_steps.append(round(total_float))

        self.current_position = 0  # 데이터 리로드 시 위치 초기화
        print(f"[Colosseum] Initialized. Loaded {self.total_tubes} tubes from {self.angles_path}")

    def _get_compensation(self, tube_num):
        """튜브 번호에 해당하는 보정 배율 반환"""
        for threshold, comp in self.step_compensation_table:
            if tube_num <= threshold:
                return comp
        return 1.0

    def read_angles_from_file(self, path):
        """angles.txt 파일 읽기 (주석 및 공백 처리 포함)"""
        angles = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    for angle in lines:
                        # 주석(#)이 아니고 공백이 아닌 유효한 숫자만 파싱
                        if not angle.strip().startswith('#') and not angle.isspace():
                            try:
                                angles.append(int(angle.strip()))
                            except ValueError:
                                pass
                print(f"[Colosseum] Loaded angles from: {path}")
            except Exception as e:
                print(f"[Colosseum] Error reading file: {e}")
        else:
            print(f"[Colosseum] WARNING: '{path}' not found.")
            print("   -> Please create 'data/angles.txt' in the project root.")
        return angles

    def get_position(self):
        """현재 튜브 위치 반환 (0=홈, 1~N=튜브 번호)"""
        return self.current_position

    def connect(self, port):
        """시리얼 포트 연결 및 초기화"""
        try:
            # Colosseum 기본 통신 속도: 2,000,000 bps
            self.ser = serial.Serial(
                port=port,
                baudrate=2000000,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1
            )
            # 아두이노 기반 보드의 리셋 대기 시간 (3초)
            time.sleep(3)

            # 초기화 명령 전송 (속도, 가속도 설정)
            for cmd in self.SETUP_CMDS:
                self._send(cmd)
                time.sleep(0.1)

            self.is_connected = True

            # 연결 시점에 튜브 데이터가 없으면 다시 로드 시도
            if self.total_tubes == 0:
                self.reload_data()

            # 연결 시 현재 위치를 홈(0)으로 가정
            # 주의: 실제 하드웨어 위치와 동기화 필요 시 home() 호출 권장
            self.current_position = 0
            print(f"[Colosseum] Connected. Current position: HOME (0)")

            return True, f"Connected ({self.total_tubes} tubes)"
        except Exception as e:
            self.is_connected = False
            return False, str(e)

    def disconnect(self):
        """연결 해제"""
        if self.ser and self.ser.is_open:
            self.stop_motion()
            self.ser.close()
        self.is_connected = False

    def move_to_tube(self, tube_idx):
        """
        목표 튜브로 이동 (1-based index)
        현재 위치에서 목표 위치까지의 델타 스텝을 계산하여 이동
        """
        if not self.is_connected:
            return False, "Not Connected"

        # 인덱스 검사 (1 ~ N)
        if 1 <= tube_idx <= self.total_tubes:
            # 현재 위치와 목표 위치의 누적 스텝 차이 계산
            current_steps = self.cumulative_steps[self.current_position]
            target_steps = self.cumulative_steps[tube_idx]
            delta = target_steps - current_steps

            if delta == 0:
                # 이미 해당 위치에 있음
                print(f"[Colosseum] Already at Tube {tube_idx}")
                return True, f"Already at {tube_idx}"

            # 델타 스텝으로 이동 명령 생성
            # @codesyncer-decision: 모터 회전 방향이 반대여서 delta 부호 반전 적용
            delta = -delta
            cmd = f"<RUN,111,{delta},{delta},{delta}>"
            print(f"[Colosseum] Tube {self.current_position} -> {tube_idx} (delta: {delta} steps)")
            self._send(cmd)

            # 위치 업데이트
            self.current_position = tube_idx
            return True, f"Moving to {tube_idx}"
        else:
            msg = f"Invalid Tube Index: {tube_idx}. Max is {self.total_tubes}."
            print(f"[Colosseum] {msg}")
            return False, msg

    def move_to_next(self):
        """
        다음 튜브로 이동 (원본 colosseum의 순차 이동 방식)
        """
        if self.current_position < self.total_tubes:
            next_tube = self.current_position + 1
            return self.move_to_tube(next_tube)
        else:
            return False, "Already at last tube"

    def home(self):
        """
        홈 위치(0)로 복귀
        """
        if not self.is_connected:
            return False, "Not Connected"

        if self.current_position == 0:
            print("[Colosseum] Already at HOME")
            return True, "Already at HOME"

        # 홈 복귀 전 속도 재설정
        for cmd in self.SETUP_CMDS:
            self._send(cmd)
            import time; time.sleep(0.05)

        # 현재 위치에서 홈까지 역방향 이동
        current_steps = self.cumulative_steps[self.current_position]
        delta = -current_steps  # 홈은 0이므로 음수
        delta = -delta  # @codesyncer-decision: 모터 방향 반전

        cmd = f"<RUN,111,{delta},{delta},{delta}>"
        print(f"[Colosseum] Returning to HOME (delta: {delta} steps)")
        self._send(cmd)

        self.current_position = 0
        return True, "Moving to HOME"

    def stop_motion(self):
        """즉시 정지"""
        if self.is_connected:
            self._send(self.STOP_CMD)

    def _send(self, cmd_str):
        """시리얼 데이터 전송 헬퍼"""
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(cmd_str.encode())
                # 입력 버퍼 비우기 (응답 처리용, 현재 로직에서는 응답을 읽지는 않음)
                self.ser.reset_input_buffer()
            except Exception as e:
                print(f"[Colosseum] Serial Write Error: {e}")