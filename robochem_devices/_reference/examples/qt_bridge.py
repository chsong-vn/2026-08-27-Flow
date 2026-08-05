"""
PyQt5 통합 최소 패턴 3가지.
핵심 규칙: 디바이스 read/write는 블로킹(수 분 가능) → 워커 스레드 전용. GUI 스레드 호출 금지.

1) Logger → Qt signal 브릿지
2) 디바이스별 커맨드 큐 워커 (수동/자동 명령이 같은 큐로 직렬화 = 유기성)
3) 정지 버튼 배선 (soft stop / estop)
"""
from queue import Queue
from PyQt5.QtCore import QObject, QThread, pyqtSignal

from robochem_devices import Logger


# ---------- 1) 로그 브릿지 ----------
class LogBridge(QObject):
    message = pyqtSignal(str, str, str)  # (msg, origin, level)

log_bridge = LogBridge()
Logger.add_callback(lambda m, o, l: log_bridge.message.emit(m, o, l))
# GUI에서: log_bridge.message.connect(self.append_log_line)


# ---------- 2) 디바이스 커맨드 큐 워커 ----------
class DeviceWorker(QThread):
    """
    디바이스 1개당 워커 1개. 수동 버튼도 시퀀서도 submit()으로만 명령.
    같은 큐를 타므로 시리얼 충돌/순서 꼬임이 구조적으로 불가능.
    job = 인자 없는 callable (예: lambda: pump.__setitem__('pump', 100.0)
          또는 lambda: PumpVolume.run(pump=pump, volume=5000, allow_refill=True))
    """
    job_done = pyqtSignal(object)      # 결과
    job_failed = pyqtSignal(object)    # 예외

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: Queue = Queue()
        self._running = True

    def submit(self, job):
        self._queue.put(job)

    def run(self):
        while self._running:
            job = self._queue.get()
            if job is None:
                break
            try:
                self.job_done.emit(job())
            except Exception as e:
                self.job_failed.emit(e)

    def shutdown(self):
        self._running = False
        self._queue.put(None)


# ---------- 3) 정지 배선 ----------
def wire_stop_buttons(window, pump, sampler):
    """
    soft stop: 진행 중인 파라미터만 중단 (ack 대기 중 S21= 주입).
               반드시 나중에 stop=False로 해제해야 다음 명령 가능.
    estop:     디바이스 전체 비상해제 (샘플러는 soft-reset + unlock까지 수행).
    """
    def soft_stop():
        pump.stop_parameter("pump")            # threading.Event.set() — 어느 스레드에서든 안전
        # 워커의 job_failed/job_done 처리 후 GUI에서:
        # pump.stop_parameter("pump", stop=False)

    def estop():
        pump.emergency_close()
        sampler.emergency_close()

    window.btn_stop.clicked.connect(soft_stop)
    window.btn_estop.clicked.connect(estop)
