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

import re
import time
import threading
from collections import deque

from hardware.sensors.phase_sensor_array import PhaseSensorError

# 벤더 실측 초기 임계값 (채널 인덱스 기준)
DEFAULT_THRESHOLDS = {0: 440, 1: 717}
BAUDRATE = 115200

# @codesyncer-decision(2026-08-17): 실기 펌웨어가 순수 CSV("adc1,adc2")가 아니라
#   라벨 포맷("S1:877,1 | S2:713,0" — adc,자체판정)을 스트리밍하는 것으로 실측 확인
#   (COM18 캡처). 파서가 CSV 만 받아 전 라인이 폐기 → 연결해도 stale 에러가 나는
#   구조였다. 두 포맷 모두 수용하되 펌웨어 자체판정 숫자는 무시한다 — 위상 판정의
#   진실원은 PC 측 thresholds (모드 A 설계 불변).
_PAT_LABELED = re.compile(
    r"S1\s*:\s*(\d+)\s*,\s*\d+\s*\|\s*S2\s*:\s*(\d+)\s*,\s*\d+")


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
        # @codesyncer(2026-08-18, 사용자 요청): 상전이(0↔1) 로그 훅 —
        #   debounce 확정 시점에 (논리키, old, new, adc) 로 호출. monitor/이벤트
        #   트리거 기구와 완전 독립(로그 전용). 엔진이 시스템 로그/트레이스로 배선.
        self.on_transition = None

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
        # @codesyncer-decision(2026-08-18, 실기 관측): 구 구현은 시리얼 예외 1회에
        #   break — 리더가 조용히 죽고 이후 read_phase 는 영구 stale, 대시보드
        #   트랙이 그 시점에서 동결됐다(부팅 +305s 프리즈 실사례). USB 순간 끊김/
        #   버스 잡음은 일시 장애이므로: 로그 1회 → 0.5s 대기 → 포트 재오픈
        #   재시도(무한, _run 이 내릴 때까지). 재연결 성공 시 스트림 자동 복구.
        _fail_logged = False
        while self._run and self._ser is not None:
            try:
                raw = self._ser.readline()
                _fail_logged = False
            except Exception as e:
                if not self._run:
                    break
                if not _fail_logged:
                    _fail_logged = True
                    print(f"[{self.name}] ⚠ 시리얼 판독 오류 — 재연결 시도 루프 진입: {e}")
                time.sleep(0.5)
                try:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    import serial
                    self._ser = serial.Serial(self.port, self.baud, timeout=1)
                    self._ser.reset_input_buffer()
                    print(f"[{self.name}] 시리얼 재연결 성공 @ {self.port}")
                except Exception:
                    continue                   # 다음 루프에서 재시도
                continue
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            vals = None
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2:               # 순수 CSV "adc1,adc2" (벤더 원형)
                try:
                    vals = [int(parts[0]), int(parts[1])]
                except ValueError:
                    vals = None
            if vals is None:                  # 라벨 포맷 "S1:adc,j | S2:adc,j" (실기 펌웨어)
                m = _PAT_LABELED.search(line)
                if m:
                    vals = [int(m.group(1)), int(m.group(2))]
            if vals is None:
                continue                      # 불량 라인 스킵
            if not all(0 <= v <= 1023 for v in vals):
                continue
            _fired = []
            with self._lock:
                self._last_rx = time.monotonic()
                for ch, adc in enumerate(vals):
                    self._adc[ch] = adc
                    new = 1 if adc > self.thresholds.get(ch, 512) else 0
                    for lk, old, nv in self._debounce_commit(ch, new):
                        _fired.append((lk, old, nv, adc))
            # 전이 로그 훅 — 반드시 락 '해제 후' 호출: 훅(엔진 _log)이 다른 락/
            # 시그널을 잡아도 리더 스레드와 교착하지 않도록. 훅 예외는 무시.
            if _fired and self.on_transition is not None:
                for lk, old, nv, adc in _fired:
                    try:
                        self.on_transition(lk, old, nv, adc)
                    except Exception:
                        pass

    def _debounce_commit(self, ch, new):
        """동일 판정 debounce_n 연속 시 확정 + 변화면 이벤트 push. (락 보유 전제)

        반환: 이번 커밋으로 확정된 전이 [(논리키, old, new), ...] —
        on_transition 로그 훅용. monitor 트리거 기구와는 독립(로그는 항상,
        이벤트 push 는 기존처럼 monitor 무장 시에만)."""
        cand, n = self._cand.get(ch, (None, 0))
        n = n + 1 if cand == new else 1
        self._cand[ch] = (new, n)
        if n < self.debounce_n:
            return []
        old = self._state.get(ch)
        if old == new:
            return []
        self._state[ch] = new
        if old is None:                       # 최초 확정은 이벤트/로그 아님
            return []
        phase = "CLEAR_LIQUID" if new == 1 else "GAS"
        trans = []
        for lk, idx in self.sensors.items():
            if idx != ch:
                continue
            trans.append((lk, old, new))
            if self._monitor.get(lk) in ("once", "always"):
                self._events[lk].append(phase)
                if self._monitor[lk] == "once":
                    self._monitor[lk] = "never"
        return trans

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
        """OCB350 캘리브 보드 하드웨어 캘리브 — 확장 펌웨어의 CALn 명령 전송.

        @codesyncer-decision(2026-08-18): opb_phase_cal.ino 업로드로 실기능화 —
          "CAL{n}\\n" 수신 시 해당 Cal 핀(S1=D6, S2=D7) 100ms LOW 펄스, 보드가
          자체 재영점. 구 펌웨어(PhotoSensor.ino 계열)는 시리얼 입력을 아예 안
          읽으므로 그쪽에 보내도 무해 no-op — 펌웨어 버전 분기 불필요.
        ⚠ RoboChem 계약: '튜브에 액체 없음' 상태에서만 호출 (N2Precal 이 배기
          완료 후 자동 호출하는 시점이 정확히 그 때). 액체 상태 캘리브는 보드
          디지털 판정의 의미를 뒤집는다. PC 임계 판정은 영향 없음(ADC 원시값
          기준이나, 캘리브가 LED 구동을 바꿔 ADC 레벨 자체는 이동할 수 있음 —
          캘리브 후엔 N2Precal 원점 재표집이 뒤따르므로 자동 정합).
        """
        k = self._key(which)
        ch = int(self.sensors.get(k, 0))
        if self._mock or self._ser is None:
            print(f"[{self.name}] (Mock/미연결) calibrate('{k}') — CAL{ch + 1} 전송 생략")
            return
        try:
            self._ser.write(f"CAL{ch + 1}\n".encode("ascii"))
            print(f"[{self.name}] calibrate('{k}') → CAL{ch + 1} 전송 "
                  f"(Cal 핀 100ms 펄스 — 확장 펌웨어)")
        except Exception as e:
            print(f"[{self.name}] ⚠ calibrate('{k}') 전송 실패(무시): {e}")

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
