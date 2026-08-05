from PyQt5.QtCore import QObject, pyqtSignal, QThread
import time
from engine.safety_manager import SafetyError

class WorkerSignals(QObject):
    sig_log = pyqtSignal(str)
    sig_status = pyqtSignal(str)
    sig_progress = pyqtSignal(int)
    sig_finished = pyqtSignal()
    sig_error = pyqtSignal(str)
    sig_mon_data = pyqtSignal(float, dict, dict, dict)
    # @codesyncer-decision: 배관도 실시간 진행률 표시용 시그널
    sig_phase_progress = pyqtSignal(str, float)  # (단계명, 진행률 0~100)
    # @codesyncer: 위상센서(OCB350) 0/1 트랙 — 기존 sig_mon_data 시그니처 불변을 위해
    #   전용 시그널 분리. {논리명: 1(액체)|0(기체)}. 센서 미장착이면 emit 자체가 없음.
    sig_phase_data = pyqtSignal(dict)
    # @codesyncer-decision: 초음파 레벨센서(HC-SR04) 시린지 잔량(µL) — {펌프명: µL}.
    #   startup 잔량 정합 시 엔진(_startup_level_reconcile)이 1회 emit. StatusWorker
    #   1초 폴 루프에는 '의도적으로' 넣지 않음 — get_volume 은 20샘플 블로킹 시리얼
    #   판독(수백ms)이라 폴 루프 예산을 초과하고, 이 센서는 도징 실시간 피드백이
    #   아니라 startup/검증 전용(RoboChem 원본과 동일 포지셔닝)이기 때문.
    sig_level_data = pyqtSignal(dict)

class SequenceWorker(QThread):
    def __init__(self, engine, plan, map_mgr):
        super().__init__(); self.engine = engine; self.plan = plan; self.map_mgr = map_mgr
    def run(self):
        try:
            self.engine.run_sequence(self.plan, self.map_mgr)
        except SafetyError as se:
            self.engine.signals.sig_status.emit("Stopped")
            self.engine.signals.sig_log.emit(f"!!! {str(se)} !!!")
        except Exception as e:
            # @codesyncer-decision: 예외 발생 시에도 반드시 정리 수행
            self.engine.signals.sig_error.emit(str(e))
            if hasattr(self.engine, '_sequence_cleanup'):
                self.engine._sequence_cleanup()
        finally:
            self.engine.signals.sig_finished.emit()

class StatusWorker(QThread):
    def __init__(self, pumps, valves, heater, config, interval=1.0, phase_sensor=None):
        super().__init__()
        self.pumps = pumps
        self.valves = valves
        self.heater = heater
        self.cfg = config
        self.interval = interval
        self.is_running = True
        self.signals = WorkerSignals()
        # 위상센서(선택) — 폴링 스레드에서 read_phase (GUI 스레드 시리얼 금지 원칙)
        self.phase_sensor = phase_sensor

    def run(self):
        while self.is_running:
            # @codesyncer-decision: 장치별 예외 격리 (모니터링 전체 마비 버그 fix)
            # - 기존: 루프 전체가 단일 except → 히터 하나만 예외를 던져도
            #   emit이 통째로 스킵되어 펌프 압력/밸브 상태까지 전부 멈춤
            #   (BathModbus 미연결 시 get_temperature가 AttributeError → 모니터링 0회)
            # - 수정: 센서별 try/except → 부분 실패여도 나머지 데이터는 계속 흐름
            try:
                temp = 0.0
                if self.heater:
                    try:
                        t = self.heater.get_temperature()
                        if t is not None: temp = t
                    except Exception:
                        pass

                pressures = {}
                p_status = {}
                for p_name, p_obj in self.pumps.items():
                    try:
                        val = p_obj.get_pressure()
                    except Exception:
                        val = None
                    pressures[p_name] = val if val is not None else 0.0
                    p_status[p_name] = getattr(p_obj, 'running', False)

                v_status = {}
                # 12-Way 밸브 상태 읽기
                for p in self.cfg.ACTIVE_PUMPS:
                    v_name = self.cfg.PUMP_VALVE_MAP.get(p)
                    if v_name and v_name in self.valves:
                        obj = self.valves[v_name]
                        pos = getattr(obj, 'position', 1)
                        v_status[p] = pos
                    else:
                        v_status[p] = 1

                self.signals.sig_mon_data.emit(temp, pressures, p_status, v_status)

                # 위상센서 0/1 트랙 (v1 = read_phase 폴 온리 — monitor/이벤트 버퍼는
                # 엔진 하이브리드 트리거 소유물이라 여기서 건드리지 않음)
                ps = self.phase_sensor
                if ps is not None and getattr(ps, "is_connected", False):
                    phases = {}
                    for key in getattr(ps, "sensors", {"collect": 0}):
                        try:
                            phases[key] = 0 if ps.read_phase(key) == "GAS" else 1
                        except Exception:
                            pass   # 센서별 예외 격리 (단선 등 — 해당 키만 결측)
                    if phases:
                        self.signals.sig_phase_data.emit(phases)
            except Exception:
                pass
            time.sleep(self.interval)

    def stop(self):
        self.is_running = False
        self.wait()