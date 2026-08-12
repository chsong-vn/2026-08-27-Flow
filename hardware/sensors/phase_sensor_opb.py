# -*- coding: utf-8 -*-
"""OPB 포토인터럽트 2채널 위상센서 — 벤더 실기 리그(Photo_Interrupt.zip) 이식.

@codesyncer-context: 하드웨어 = OPB 계열 포토인터럽트 2개 + Arduino UNO
  (A0/A1 아날로그, 50ms 주기로 "adc1,adc2\\r\\n" CSV 스트림 @115200).
  펌웨어 = Photo_Interrupt/Arduino/PhotoSensor.ino (요청-응답 아님, 상시 push).
  판정 규칙(벤더 read_sensor.py): ADC > threshold → 액체(WATER), 이하 → 기체(AIR).
  벤더 실측 캘리브레이션(2026-08-05 기준 초기값):
    S1(A0): 없음 574 / 공기 80 / 물 800 → threshold 440
    S2(A1): 없음 960 / 공기 457 / 물 977 → threshold 717
  ※ 센서 보드·튜브·정렬 변경 시 재캘리브레이션 필요 (README 지침).

@codesyncer-decision: 엔진/UI 계약은 PhaseSensorArrayHW(OCB350)와 동일 —
  read_phase/is_liquid/analog/monitor/read_event/wait_edge/calibrate/stop.
  OPB 는 투명/불투명 구분이 없어 액체 = "CLEAR_LIQUID" 로 보고 (GAS 대비만
  의미 있음 — HTE 경계 검출 용도 동일). calibrate() 는 펌웨어 지원이 없어
  no-op + 안내 로그 (임계값은 settings.thresholds 로 관리).

@codesyncer-decision: 스트림형이라 백그라운드 리더 스레드가 진실원 —
  read_phase 는 캐시 응답, stale_sec 내 새 라인이 없으면 PhaseSensorError
  (단선 감지, 조용한 실패 금지). 디바운스 = 동일 판정 debounce_n 회 연속
  (50ms 샘플 × 2 = 100ms — 슬러그 경계 채터 방지).
"""

import time
import threading
from collections import deque

from hardware.sensors.phase_sensor_array import PhaseSensorError

# 벤더 실측 초기 임계값 (채널 인덱스 기준)
DEFAULT_THRESHOLDS = {0: 440, 1: 717}
BAUDRATE = 115200


class PhaseSensorOPBADC:
    def __init__(self, port, sensors=None, thresholds=None, name="OPBSensors",
                 baudrate=BAUDRATE, debounce_n=2, stale_sec=2.0):
        self.name = name
        self.port = port
        self.baud = int(baudrate)
        # 논리명 → 채널 인덱스 (0=S1/A0, 1=S2/A1). 기본: 아웃렛 직전 'collect'
        self.sensors = {str(k): int(v) for k, v in (sensors or {"collect": 0}).items()}
        # 임계값: {채널인덱스: adc} — settings 는 논리명/문자열 키도 허용
        thr = dict(DEFAULT_THRESHOLDS)
        for k, v in (thresholds or {}).items():
            idx = self.sensors.get(str(k)) if str(k) in self.sensors else int(k)
            thr[int(idx)] = int(v)
        self.thresholds = thr
        self.debounce_n = max(1, int(debounce_n))
        self.stale_sec = float(stale_sec)
        self.is_connected = False
        self._mock = (not port) or ("Mock" in str(port))
        self._ser = None
        self._reader = None
        self._run = False
        self._lock = threading.Lock()
        # 채널 상태 (인덱스 기준)
        self._adc = {}                # ch → 최근 ADC
        self._state = {}              # ch → 1(액체)/0(기체), 디바운스 확정값
        self._cand = {}               # ch → (후보상태, 연속횟수)
        self._last_rx = 0.0           # 마지막 유효 라인 시각
        # monitor/이벤트 (논리명 기준 — 기존 계약과 동일)
        self._events = {k: deque() for k in self.sensors}
        self._monitor = {k: "never" for k in self.sensors}
        # Mock 시뮬 (기존 드라이버와 동일 의미론)
        self._sim_phase = {k: 1 for k in self.sensors}   # 1=CLEAR_LIQUID, 3=GAS

    # ── 연결/리더 ─────────────────────────────────────────────
    def connect(self, serial_override=None):
        """@param serial_override: 테스트용 페이크 시리얼 주입 (readline 계약)."""
        if self._mock:
            print(f"[{self.name}] Virtual OPB (Mock) — ADC 스트림 시뮬")
            self.is_connected = True
            return True
        if serial_override is not None:
            self._ser = serial_override
        else:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)                      # UNO 리셋 대기 (벤더 스크립트 관례)
            self._ser.reset_input_buffer()
        self._run = True
        self._reader = threading.Thread(target=self._reader_loop, daemon=True,
                                        name=f"opb-{self.name}")
        self._reader.start()
        self.is_connected = True
        print(f"[{self.name}] OPB ADC 스트림 연결 @ {self.port} "
              f"({self.baud}bps, thresholds={self.thresholds}, "
              f"센서 {len(self.sensors)}: {', '.join(self.sensors)})")
        return True

    def disconnect(self):
        self._run = False
        if self._reader is not None:
            try:
                self._reader.join(1.5)
            except Exception:
                pass
            self._reader = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self.is_connected = False

    def stop(self):
        """계약 균일성용 no-op (센서는 능동 동작 없음)."""

    def _reader_loop(self):
        while self._run and self._ser is not None:
            try:
                raw = self._ser.readline()
            except Exception:
                break
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 2:
                continue                      # 벤더 스크립트 관례: 불량 라인 스킵
            try:
                vals = [int(parts[0]), int(parts[1])]
            except ValueError:
                continue
            if not all(0 <= v <= 1023 for v in vals):
                continue
            with self._lock:
                self._last_rx = time.monotonic()
                for ch, adc in enumerate(vals):
                    self._adc[ch] = adc
                    new = 1 if adc > self.thresholds.get(ch, 512) else 0
                    self._debounce_commit(ch, new)

    def _debounce_commit(self, ch, new):
        """동일 판정 debounce_n 연속 시 확정 + 변화면 이벤트 push. (락 보유 전제)"""
        cand, n = self._cand.get(ch, (None, 0))
        n = n + 1 if cand == new else 1
        self._cand[ch] = (new, n)
        if n < self.debounce_n:
            return
        old = self._state.get(ch)
        if old == new:
            return
        self._state[ch] = new
        if old is None:                       # 최초 확정은 이벤트 아님
            return
        phase = "CLEAR_LIQUID" if new == 1 else "GAS"
        for lk, idx in self.sensors.items():
            if idx == ch and self._monitor.get(lk) in ("once", "always"):
                self._events[lk].append(phase)
                if self._monitor[lk] == "once":
                    self._monitor[lk] = "never"

    # ── 내부 ─────────────────────────────────────────────────
    def _key(self, which):
        k = str(which)
        if k not in self.sensors:
            raise PhaseSensorError(
                f"[{self.name}] 미등록 센서 '{which}' (등록: {list(self.sensors)})")
        return k

    def _check_fresh(self):
        if time.monotonic() - self._last_rx > self.stale_sec:
            raise PhaseSensorError(
                f"[{self.name}] ADC 스트림 두절 {self.stale_sec:.0f}s+ — "
                f"배선/포트({self.port}) 확인 (단선 의심)")

    # ── 판독 ─────────────────────────────────────────────────
    def read_phase(self, which="collect"):
        k = self._key(which)
        with self._lock:
            if self._mock:
                v = self._sim_phase[k]
                if v == 0:
                    raise PhaseSensorError(f"[{self.name}] 센서 '{k}' 단선(ERROR)")
                return "CLEAR_LIQUID" if v != 3 else "GAS"
            self._check_fresh()
            st = self._state.get(self.sensors[k])
            if st is None:
                raise PhaseSensorError(f"[{self.name}] 센서 '{k}' 판독 전 (스트림 대기)")
            return "CLEAR_LIQUID" if st == 1 else "GAS"

    def is_liquid(self, which="collect"):
        return self.read_phase(which) != "GAS"

    def read_all(self):
        return {k: self.read_phase(k) for k in self.sensors}

    def analog(self, which="collect"):
        k = self._key(which)
        with self._lock:
            if self._mock:
                return {1: 800, 2: 800, 3: 80}.get(self._sim_phase[k], 0)
            self._check_fresh()
            return int(self._adc.get(self.sensors[k], -1))

    # ── 캘리브레이션/모니터 ──────────────────────────────────
    def calibrate(self, which="collect"):
        """OPB ADC 리그는 펌웨어 자동 캘리브 미지원 — settings.thresholds 로 관리.
        벤더 README 실측표(공기/물 ADC) 기준으로 중간값을 입력할 것."""
        k = self._key(which)
        cur = None
        try:
            cur = self.analog(k)
        except Exception:
            pass
        print(f"[{self.name}] calibrate('{k}') 미지원 — 현재 ADC={cur}, "
              f"threshold={self.thresholds.get(self.sensors[k])} "
              f"(변경은 장치 settings.thresholds)")

    def monitor(self, which="collect", mode="once"):
        k = self._key(which)
        mode = str(mode).lower()
        if mode not in ("never", "once", "always"):
            raise PhaseSensorError(f"monitor mode '{mode}' (never|once|always)")
        with self._lock:
            self._monitor[k] = mode
            if mode == "never":
                self._events[k].clear()

    def read_event(self, which="collect"):
        """monitor 중 위상변화 push 를 논블로킹 폴. 변화 없으면 None."""
        k = self._key(which)
        with self._lock:
            return self._events[k].popleft() if self._events[k] else None

    def wait_edge(self, which="collect", want_gas=True, timeout=30.0, poll=0.05):
        """목표 위상 도달 대기 — PhaseSensorArrayHW 와 동일한 RETURN 정책."""
        k = self._key(which)
        want_liquid = not want_gas
        cur = self.read_phase(k)
        if (cur != "GAS") == want_liquid:
            return True
        self.monitor(k, "once")
        t_left = float(timeout)
        try:
            while t_left > 0:
                ev = self.read_event(k)
                if ev is not None and ((ev != "GAS") == want_liquid):
                    return True
                time.sleep(poll)
                t_left -= poll
        finally:
            try:
                self.monitor(k, "never")
            except Exception:
                pass
        return False

    # ── Mock 전용 (테스트/시뮬) ──────────────────────────────
    def sim_set_phase(self, which, phase):
        if not self._mock:
            raise PhaseSensorError("sim_set_phase 는 Mock 전용")
        k = self._key(which)
        names = {"ERROR": 0, "CLEAR_LIQUID": 1, "OPAQUE_LIQUID": 2, "GAS": 3}
        v = int(phase) if not isinstance(phase, str) else names[phase]
        with self._lock:
            if v != self._sim_phase[k]:
                self._sim_phase[k] = v
                if self._monitor[k] in ("once", "always"):
                    self._events[k].append(
                        "GAS" if v == 3 else "CLEAR_LIQUID")
                    if self._monitor[k] == "once":
                        self._monitor[k] = "never"
