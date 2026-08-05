# -*- coding: utf-8 -*-
"""HC-SR04 초음파 레벨센서 어댑터 — 시린지 잔량 실측(콜드스타트 리셋용).

@codesyncer-decision: RoboChem(Science 2024, SI 3.3.1) 원본을 이식.
  - 원본: 펌프마다 Arduino UNO 1대 + HC-SR04, 9600bps, 시린지 플런저에 붙인
    3D 프린팅 '반사 블록'까지의 거리를 측정(액면 직접 조준 X — 빔이 배럴벽에
    막힘). 20샘플 median → 펌프별 선형 캘리브레이션으로 부피 환산.
  - 용도: 도징 실시간 피드백이 '아님'. 오직 시퀀스 시작 시 1회 —
    "위치센서 없는 개루프 시린지펌프의 물리 잔량을 실측해 소프트웨어
    카운터를 진실화"(run-dry 방지) + reagent 라인은 잔량 폐액 퍼지 후
    검증된 empty로 리셋(교차오염 방지). 엔진 _startup_level_reconcile 참조.
  - 정확도: HC-SR04 실사양 ±3mm → 배럴 단면적에 곱하면 수백µL~1mL. 따라서
    '거친 게이트/검증'용이며 µL 단위 clean 인증에는 쓰지 않는다(gate ≫ 노이즈).

계약 (엔진/UI 공용 — PhaseSensorArrayHW 와 동일 스타일):
    ls = UltrasonicLevelSensor(channels, name="LevelSensors")
      channels = {펌프명: {"port","baud","slope","intercept","samples","index","mock"}}
      index ≥ 0 → 4채널 펌웨어 CSV("d0,d1,d2,d3", 무반사="NA")에서 해당 열 파싱.
        한 Arduino(포트)에 4펌프까지 공유 가능 (level_sensor_hcsr04_4ch.ino).
        단일값도 split(',')[0]으로 동작하므로 index=0 은 구 펌웨어와 하위호환.
      index < 0 → 기존 단일 float 라인 파싱 (하위호환 기본).
    ls.connect() / ls.disconnect() / ls.is_connected / ls.stop()
    ls.get_volume(펌프명) -> float  (µL, 항상 ≥ 0. 측정 실패 시 예외)
port 에 "Mock" 포함 또는 channel["mock"]=True 시 시뮬 경로
  (set_value / set_sequence 로 반환값 주입 — 실기 없이 로직 검증).
"""

import threading
from collections import deque


class LevelSensorError(RuntimeError):
    """센서 단선/프로토콜/저품질 판독 — 조용한 실패 금지."""


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        raise LevelSensorError("median: 표본 없음")
    mid = n // 2
    return s[mid] if (n % 2) else (s[mid - 1] + s[mid]) / 2.0


class UltrasonicLevelSensor:
    def __init__(self, channels=None, name="LevelSensors"):
        self.name = name
        # 펌프명 → {port, baud, slope, intercept, samples, mock}
        self.channels = {}
        for pump, ch in (channels or {}).items():
            ch = dict(ch or {})
            port = ch.get("port")
            ch["port"] = port
            ch["baud"] = int(ch.get("baud", 9600) or 9600)
            ch["slope"] = float(ch.get("slope", 1.0))
            ch["intercept"] = float(ch.get("intercept", 0.0))
            ch["samples"] = int(ch.get("samples", 20) or 20)
            # 4채널 CSV 채널 인덱스 (None/-1 = 단일값 라인 모드)
            _idx = ch.get("index", -1)
            ch["index"] = int(_idx) if _idx is not None else -1
            # 명시 mock 플래그 OR 포트명에 "Mock" OR 포트 없음 → 시뮬
            ch["mock"] = bool(ch.get("mock")) or (not port) or ("Mock" in str(port))
            self.channels[str(pump)] = ch
        self.is_connected = False
        self._serials = {}          # port str → serial.Serial (실기, 포트 단위 공유)
        self._lock = threading.Lock()
        # 시뮬 상태 (Mock 채널 전용) — 테스트 훅
        self._sim_val = {p: 0.0 for p in self.channels}
        self._sim_seq = {p: deque() for p in self.channels}

    # ── 연결 ─────────────────────────────────────────────────
    def connect(self):
        for pump, ch in self.channels.items():
            if ch["mock"]:
                continue
            port = ch["port"]
            if port in self._serials:
                continue
            import serial  # 지연 import (mock 전용 환경에서 pyserial 불필요)
            self._serials[port] = serial.Serial(port, ch["baud"], timeout=1.0)
        self.is_connected = True
        n_mock = sum(1 for c in self.channels.values() if c["mock"])
        print(f"[{self.name}] 연결 — 채널 {len(self.channels)}개 "
              f"(실기 {len(self._serials)} 포트, mock {n_mock})")
        return True

    def disconnect(self):
        with self._lock:
            for port, ser in list(self._serials.items()):
                try:
                    if ser and ser.is_open:
                        ser.close()
                except Exception:
                    pass
            self._serials = {}
            self.is_connected = False

    def stop(self):
        """계약 균일성용 no-op (센서는 능동 동작 없음)."""

    # ── 내부 ─────────────────────────────────────────────────
    def _channel(self, pump):
        k = str(pump)
        if k not in self.channels:
            raise LevelSensorError(
                f"[{self.name}] 미등록 채널 '{pump}' (등록: {list(self.channels)})")
        return k, self.channels[k]

    def _read_raw(self, ch, samples=None):
        """실기: 시리얼에서 samples 개의 유효 float 을 모아 median 반환.

        samples 오버라이드: 토출 중 실시간 모니터링('밀면서 측정')용 고속 판독 —
        5표본 ≈ 0.5초. 노이즈는 커지지만 정지 트리거 용도로 충분하며, 최종 판정은
        항상 풀샘플 검증 측정이 담당한다."""
        port = ch["port"]
        ser = self._serials.get(port)
        if ser is None:
            raise LevelSensorError(f"[{self.name}] 미연결 포트: {port}")
        need = int(samples) if samples else ch["samples"]
        vals = []
        attempts = 0
        max_attempts = need * 3 + 5
        with self._lock:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            while len(vals) < need and attempts < max_attempts:
                attempts += 1
                try:
                    line = ser.readline().decode(errors="ignore").strip()
                except Exception:
                    continue
                if not line:
                    continue
                # @codesyncer-decision: 4채널 펌웨어(CSV "d0,d1,d2,d3") 지원 —
                #   index ≥ 0 이면 해당 열만 취한다. "NA"(무반사)는 무효 표본으로
                #   버림 → 절반 미만이면 아래 품질 게이트가 시끄럽게 실패시킴
                #   (조용한 오판 금지 정책 그대로 유지).
                if ch.get("index", -1) is not None and ch.get("index", -1) >= 0:
                    parts = line.split(",")
                    if len(parts) <= ch["index"]:
                        continue
                    line = parts[ch["index"]].strip()
                    if not line or line.upper() == "NA":
                        continue
                try:
                    vals.append(float(line))
                except ValueError:
                    continue
        # 절반 미만만 유효하면 신뢰 불가 (조용한 오판 금지)
        if len(vals) < max(3, need // 2):
            raise LevelSensorError(
                f"[{self.name}] 포트 {port}: 유효표본 {len(vals)}/{need} — 판독 실패")
        return _median(vals)

    # ── 판독 ─────────────────────────────────────────────────
    def get_volume(self, pump, samples=None):
        """시린지 현재 잔량(µL). 실패 시 LevelSensorError.

        Mock 채널이면 주입된 시뮬값(set_value/set_sequence)을 반환.
        실기면 raw(반사블록 거리) → 펌프별 선형 캘리브레이션(mL) → µL.
        samples: 판독 표본 수 오버라이드 — 토출 중 실시간 모니터링용 고속 판독
        (예: 5표본 ≈ 0.5초). None 이면 채널 기본값(정밀 검증 측정).
        """
        k, ch = self._channel(pump)
        if ch["mock"]:
            return float(self._sim_get(k))
        raw = self._read_raw(ch, samples=samples)
        vol_ml = ch["slope"] * raw + ch["intercept"]
        # @codesyncer-decision: 큰 음수는 노이즈가 아니라 캘리브레이션 부호/절편 오류 또는
        #   마운트 뒤집힘 신호 → 조용히 0으로 클램프하면 '거짓 empty' 인증(오염+과리필).
        #   따라서 노이즈 폭(-0.2mL) 밖 음수는 예외로 시끄럽게 실패시킨다. 엔진은 이 예외를
        #   '측정 실패 → 가정 empty 폴백'으로 처리하므로 거짓 clean 을 만들지 않는다.
        if vol_ml < -0.2:
            raise LevelSensorError(
                f"[{self.name}] {ch.get('port')}: 환산 부피 {vol_ml:.2f}mL (<0) — "
                f"캘리브레이션 부호/절편 또는 마운트 오류 의심 (raw={raw})")
        return max(0.0, vol_ml) * 1000.0

    def read_all(self):
        out = {}
        for p in self.channels:
            try:
                out[p] = self.get_volume(p)
            except Exception:
                pass
        return out

    # ── Mock 전용 (테스트/시뮬) ──────────────────────────────
    def _sim_get(self, k):
        if self._sim_seq[k]:
            return self._sim_seq[k].popleft()
        return self._sim_val[k]

    def set_value(self, pump, ul):
        """Mock: 고정 반환값(µL) 설정."""
        k, ch = self._channel(pump)
        if not ch["mock"]:
            raise LevelSensorError("set_value 는 Mock 채널 전용")
        self._sim_val[k] = float(ul)

    def set_sequence(self, pump, values):
        """Mock: 연속 get_volume 호출에 순서대로 반환할 값들(µL). 소진 후 set_value 폴백.

        @codesyncer: 퍼지→재측정 루프 검증용 — 예: [2000, 0] = 첫 측정 잔량,
          1회 퍼지 후 재측정 0. 소진되면 마지막 set_value(기본 0) 반환.
        """
        k, ch = self._channel(pump)
        if not ch["mock"]:
            raise LevelSensorError("set_sequence 는 Mock 채널 전용")
        self._sim_seq[k] = deque(float(v) for v in values)


class MockUltrasonicLevelSensor(UltrasonicLevelSensor):
    """가상 레벨센서 — 모든 채널 강제 Mock (factory '가상 레벨센서 (HC-SR04)')."""

    def __init__(self, channels=None, pumps=None, name="MockLevelSensor"):
        if channels is None:
            channels = {p: {} for p in (pumps or [])}
        forced = {}
        for pump, ch in channels.items():
            ch = dict(ch or {})
            ch["mock"] = True
            ch.setdefault("port", "Mock_Port")
            forced[pump] = ch
        super().__init__(forced, name=name)
