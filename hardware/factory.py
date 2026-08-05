
class HardwareFactory:
    @staticmethod
    def get_available_drivers():
        """
        시스템에서 사용 가능한 모든 하드웨어 드라이버 이름 목록을 반환합니다.
        Dialog의 콤보박스에 표시될 항목들입니다.

        @codesyncer-decision: 드라이버 이름 한글화로 사용자 편의성 향상
        """
        return [
            # --- 1. 펌프 (Pumps) ---
            "가상펌프",              # MockPump - 테스트용
            "시린지 펌프 (Chemyx)",  # Chemyx Fusion
            "시린지 펌프 (NRG)",     # NRGSyringePump - RoboChem-Flex 시린지펌프 (Arduino + Mrv-01B)
            "연동 펌프 (Vapourtec)", # Vapourtec R-Series
            "연동 펌프 (Reaxus)",    # Teledyne Reaxus

            # --- 2. 밸브 (Valves) ---
            "가상밸브",              # MockValve
            "3방향 밸브",            # ArduinoValve - 3-Way (UNO 릴레이 4ch, 레거시)
            "3방향 밸브 (ESP32 이더넷)",  # ESP32EthValve - 3-Way (ESP32-S3-ETH-8DI-8RO, TCP 8ch)
            "12방향 밸브 (Runze)",   # Runze SV07 - 12-Way Selector

            # --- 3. 히터 (Heaters) ---
            "가상히터",              # MockHeater
            "항온조 히터",           # BathModbusHeater - Oil Bath/Chiller

            # --- 4. 수집기 (Collectors) ---
            "가상 수집기",           # MockCollector - 테스트용
            "분획 수집기 (Colosseum)", # ColosseumCollector - Fraction Collector
            "96-well 분취기 (Plate96)", # Plate96Collector - Marlin G-code

            # --- 5. 샘플러 (Samplers, 전단 시약 픽업) ---
            "가상 샘플러",                  # MockCartesianSampler
            "Cartesian 샘플러 (Grbl)",      # GrblCartesianSampler - RoboChem-Flex 3축

            # --- 6. 가스 (Gas — droplet HTE 질소 스페이서) ---
            "가상 MFC",                     # Mock (포트 Mock_Port)
            "질소 MFC (MKP RS485)",         # MFCKoreaMKP - MFC KOREA, MKP ASCII 프로토콜

            # --- 7. 센서 (Sensors — 슬러그 경계 검출 / 시린지 잔량) ---
            "가상 위상센서",                # MockPhaseSensor
            "위상센서 어레이 (OCB350)",     # PhaseSensorArrayHW - RoboChem 원본, UNO 1대=4센서
            "위상센서 (OPB ADC 2ch)",       # PhaseSensorOPBADC - UNO A0/A1 CSV 스트림 @115200
            "가상 레벨센서 (HC-SR04)",      # MockUltrasonicLevelSensor - 테스트용
            "초음파 레벨센서 (HC-SR04)",    # UltrasonicLevelSensor - RoboChem 원본, 펌프당 UNO 1대
        ]

    @staticmethod
    def get_driver_type(korean_name):
        """
        한글 드라이버 이름을 영어 타입으로 변환합니다.

        @codesyncer-decision: 한글 UI와 영어 클래스 이름 간 매핑
        """
        driver_map = {
            # Pumps
            "가상펌프": "MockPump",
            "시린지 펌프 (Chemyx)": "Chemyx",
            "시린지 펌프 (NRG)": "NRGSyringePump",
            "연동 펌프 (Vapourtec)": "Vapourtec",
            "연동 펌프 (Reaxus)": "Reaxus",

            # Valves
            "가상밸브": "MockValve",
            "3방향 밸브": "ArduinoValve",
            "3방향 밸브 (ESP32 이더넷)": "ESP32EthValve",
            "12방향 밸브 (Runze)": "RunzeSV07Valve",

            # Heaters
            "가상히터": "MockHeater",
            "항온조 히터": "BathModbusHeater",

            # Collectors
            "가상 수집기": "MockCollector",
            "분획 수집기 (Colosseum)": "ColosseumCollector",
            "96-well 분취기 (Plate96)": "Plate96Collector",

            # Samplers (전단 시약 픽업)
            "가상 샘플러": "MockCartesianSampler",
            "Cartesian 샘플러 (Grbl)": "GrblCartesianSampler",

            # Gas (droplet HTE 질소) — MKP RS485 ASCII 프로토콜
            "가상 MFC": "MockMFC",
            "질소 MFC (MKP RS485)": "MFCKoreaMKP",
            "질소 MFC (Modbus)": "MFCKoreaMKP",   # back-compat: 구 라벨 → 신 드라이버

            # Sensors (슬러그 경계 검출 — RoboChem OCB350 스택)
            "가상 위상센서": "MockPhaseSensor",
            "위상센서 어레이 (OCB350)": "PhaseSensorArrayHW",
            "위상센서 (OPB ADC 2ch)": "PhaseSensorOPBADC",

            # Sensors (시린지 잔량 — RoboChem HC-SR04 스택)
            "가상 레벨센서 (HC-SR04)": "MockUltrasonicLevelSensor",
            "초음파 레벨센서 (HC-SR04)": "UltrasonicLevelSensor",
        }
        return driver_map.get(korean_name, korean_name)