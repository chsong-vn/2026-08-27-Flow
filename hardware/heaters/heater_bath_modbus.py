from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    from pymodbus.client import ModbusSerialClient
    from pymodbus.payload import BinaryPayloadBuilder, BinaryPayloadDecoder
    from pymodbus.constants import Endian
    HAS_PYMODBUS = True
except ImportError as e:
    HAS_PYMODBUS = False
    _IMPORT_ERROR = e
    # pymodbus 없을 때 클래스 정의용 더미
    class _EndianDummy:
        LITTLE = 0
        BIG = 1
    Endian = _EndianDummy

@dataclass
class BathModbusSettings:
    # 통신 설정
    port: str = "COM5"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    
    # 타임아웃 단축 (UI 반응성 향상)
    timeout: float = 1.0

    slave_id: int = 1

    # 주소 맵핑
    ADDR_SET_TEMP: int = 990    
    ADDR_RUN_STOP: int = 1002   
    ADDR_REPORT: int = 1003     
    
    READ_COUNT: int = 25        
    OFFSET_PV: int = 4          

    BYTE_ORDER = Endian.LITTLE
    WORD_ORDER = Endian.LITTLE

    # 폴링 주기
    poll_interval_s: float = 1.0


class BathModbusHeater:
    def __init__(self, port: str, settings: Optional[BathModbusSettings] = None):
        if not HAS_PYMODBUS:
            print(f"❌ [BathModbus] pymodbus library missing. Error: {_IMPORT_ERROR}")
            self._client = None
            return

        self.settings = settings or BathModbusSettings(port=port)
        self._client: Optional[ModbusSerialClient] = None

        self._lock = threading.Lock()
        
        self._target_temp: float = 0.0
        self._current_temp: float = 0.0
        self._connected: bool = False

        # 마지막으로 장비에 '전송한' 온도 기억 (중복 전송 방지용)
        self._last_written_temp: Optional[float] = None

        self._poll_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    def connect(self):
        """ 장비 연결 """
        if not HAS_PYMODBUS:
            return
        if self._connected:
            return

        self._client = ModbusSerialClient(
            port=self.settings.port,
            baudrate=self.settings.baudrate,
            bytesize=self.settings.bytesize,
            parity=self.settings.parity,
            stopbits=self.settings.stopbits,
            timeout=self.settings.timeout,
        )

        ok = self._client.connect()
        if not ok:
            self._connected = False
            raise ConnectionError(f"[BathModbus] Connection failed on {self.settings.port}")

        self._connected = True
        self.is_connected = True   # 대시보드 상태 패널용 공개 플래그 (2026-08-25)
        print(f"   [BathModbus] Connected")

        # 폴링 스레드 시작
        self._stop_evt.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def set_temperature(self, t: float):
        """ 온도 설정 (중복 명령 필터링) """
        if not self._connected or self._client is None:
            return

        t = float(t)
        
        with self._lock:
            # 목표 온도는 항상 업데이트
            self._target_temp = t

            # 이미 장비에 설정한 온도와 같으면 명령을 보내지 않음
            if self._last_written_temp is not None:
                if abs(t - self._last_written_temp) < 0.01:
                    return

            try:
                if hasattr(self._client, 'reset_input_buffer'):
                    self._client.reset_input_buffer()
                
                builder = BinaryPayloadBuilder(byteorder=self.settings.BYTE_ORDER, wordorder=self.settings.WORD_ORDER)
                builder.add_64bit_float(float(t))
                payload = builder.to_registers()

                # sleep 제거 또는 최소화
                res = self._client.write_registers(self.settings.ADDR_SET_TEMP, payload, slave=self.settings.slave_id)
                time.sleep(0.05) 
                
                if res.isError():
                    print(f"   [BathModbus] Write Temp Error: {res}")
                else:
                    self._set_run_stop_unsafe(True)
                    self._last_written_temp = t
                    print(f"   [BathModbus] Temp Set: {t:.1f}")
                    
            except Exception as e:
                print(f"   [BathModbus] set_temperature error: {e}")

    def get_temperature(self) -> float:
        return float(self._current_temp)

    def stop(self):
        """ 장비 정지 """
        if self._connected:
            with self._lock:
                self._set_run_stop_unsafe(False)
            self._last_written_temp = None
            print("   [BathModbus] Heater Stopped")

    def disconnect(self):
        """ 완전 종료 """
        self.stop()
        self._stop_evt.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        if self._client:
            self._client.close()
        self._connected = False

    # ---- Internal ----

    def _set_run_stop_unsafe(self, run: bool):
        if self._client is None: return
        val = 1 if run else 0
        try:
            self._client.write_register(self.settings.ADDR_RUN_STOP, val, slave=self.settings.slave_id)
            time.sleep(0.05)
        except Exception:
            pass

    def _poll_loop(self):
        # @codesyncer-decision(2026-08-25, 끊김 가시화): 폴링이 조용히 실패하면
        #   _current_temp 가 마지막 값에 동결된 채 '연결됨'처럼 보임 — 연속 3회
        #   실패 시 is_connected=False 로 자백(대시보드 OFFLINE), 성공 시 자가 회복.
        _fail_streak = 0
        while not self._stop_evt.is_set():
            if self._client and self._connected:
                acquired = self._lock.acquire(blocking=False)
                if acquired:
                    try:
                        rr = self._client.read_holding_registers(
                            address=self.settings.ADDR_REPORT,
                            count=self.settings.READ_COUNT,
                            slave=self.settings.slave_id
                        )
                        if not rr.isError():
                            regs = rr.registers
                            target_chunk = regs[self.settings.OFFSET_PV : self.settings.OFFSET_PV + 4]
                            dec = BinaryPayloadDecoder.fromRegisters(
                                target_chunk,
                                byteorder=self.settings.BYTE_ORDER,
                                wordorder=self.settings.WORD_ORDER
                            )
                            pv = dec.decode_64bit_float()
                            self._current_temp = float(pv)
                            _fail_streak = 0
                            self.is_connected = True
                        else:
                            _fail_streak += 1
                    except Exception:
                        _fail_streak += 1
                    finally:
                        self._lock.release()
                    if _fail_streak == 3:
                        self.is_connected = False
                        print("[BathModbus] 폴링 연속 3회 실패 — 연결 끊김 의심 (OFFLINE 표시)")

            time.sleep(self.settings.poll_interval_s)

            