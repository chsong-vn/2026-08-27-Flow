# --- START OF FILE hardware/collectors/collector_mock.py ---

class MockCollector:
    """
    @codesyncer-decision: 테스트용 가상 Fraction Collector
    @codesyncer-context: ColosseumCollector와 동일한 인터페이스 제공
    """
    def __init__(self):
        self.is_connected = False
        self.total_tubes = 88  # 기본 88개 튜브
        self.current_position = 0
        self.cumulative_steps = list(range(0, self.total_tubes * 100 + 1, 100))  # 가상 스텝

    def connect(self, port):
        """가상 연결"""
        self.is_connected = True
        self.current_position = 0
        print(f"[MockCollector] Connected to {port} ({self.total_tubes} tubes)")
        return True, f"Mock Connected ({self.total_tubes} tubes)"

    def disconnect(self):
        """연결 해제"""
        self.is_connected = False
        print("[MockCollector] Disconnected")

    def get_position(self):
        """현재 튜브 위치 반환 (0=홈, 1~N=튜브 번호)"""
        return self.current_position

    def move_to_tube(self, tube_idx):
        """목표 튜브로 이동"""
        if not self.is_connected:
            return False, "Not Connected"

        if 1 <= tube_idx <= self.total_tubes:
            print(f"[MockCollector] Moving: Tube {self.current_position} -> {tube_idx}")
            self.current_position = tube_idx
            return True, f"Moved to Tube {tube_idx}"
        else:
            return False, f"Invalid tube index: {tube_idx}"

    def move_to_next(self):
        """다음 튜브로 이동"""
        if self.current_position < self.total_tubes:
            return self.move_to_tube(self.current_position + 1)
        return False, "Already at last tube"

    def home(self):
        """홈 위치로 복귀"""
        if not self.is_connected:
            return False, "Not Connected"

        print(f"[MockCollector] Returning to HOME from Tube {self.current_position}")
        self.current_position = 0
        return True, "Moved to HOME"

    def stop_motion(self):
        """모션 정지 (Mock에서는 아무 동작 없음)"""
        print("[MockCollector] Stop motion (mock)")

    def reload_data(self):
        """데이터 리로드 (Mock에서는 아무 동작 없음)"""
        print("[MockCollector] Reload data (mock)")
