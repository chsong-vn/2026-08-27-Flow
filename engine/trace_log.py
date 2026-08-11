# -*- coding: utf-8 -*-
"""Chrome Trace Event Format 로거 — Perfetto UI(ui.perfetto.dev)로 여는 실행 타임라인.

시퀀스 실행 중 장비별 이벤트(도징 스팬, 밸브 전환, 타이머 계획/발화, 국면 전이,
온도/압력 카운터)를 logs/TRACE_*.json 에 기록한다. 파일을 ui.perfetto.dev 에
드래그하면 장비별 트랙이 쌓인 타임라인을 줌/팬/SQL 질의로 열람할 수 있다.
형식 참조: https://perfetto.dev/docs/getting-started/other-formats (Chrome JSON)

@codesyncer-decision(2026-08-11): 트레이스는 '보조 관측'이다 — 어떤 경우에도
  엔진 실행을 깨뜨리면 안 되므로 모든 공개 메서드는 내부에서 예외를 삼킨다.
  기록 실패는 조용히 무시된다 (콘솔/CSV 로그가 1차 기록으로 이미 존재).
@codesyncer-decision: 크래시 내성 — 각 이벤트를 append+flush 하는 JSON 배열
  스트림으로 쓴다. Perfetto 는 닫히지 않은 배열(끝의 ',' 포함)을 허용하므로
  프로세스가 죽어도 그 시점까지의 트레이스가 열린다. close() 시에는 ']' 를
  붙여 엄격한 JSON 으로 마감한다.
@codesyncer-decision: 기본 활성. 환경변수 FLOWCHEM_TRACE=0 으로 비활성
  (hardware_config 스키마를 건드리지 않기 위해 env 게이트 선택).

사용 (엔진 내부):
    self.trace = TraceLogger(os.path.join("logs", "TRACE_20260811_120000_Sequence.json"))
    self.trace.instant("LOG", "메시지")
    self.trace.begin("PHASE", "가열 대기"); ...; self.trace.end("PHASE")
    self.trace.complete("VALVE Outlet", "→2", start_ts, dur_sec)
    self.trace.counter("Temp(C)", {"temp": 25.1})
    self.trace.close()
"""

import json
import os
import threading
import time


class NullTracer:
    """트레이스 비활성/미초기화 시의 무해한 대체물 — 모든 호출이 no-op."""

    def __getattr__(self, _name):
        return lambda *a, **k: None

    def __bool__(self):
        return False


class TraceLogger:
    def __init__(self, path):
        self.enabled = os.environ.get("FLOWCHEM_TRACE", "1") != "0"
        self.path = path
        self._lock = threading.Lock()
        self._f = None
        self._tids = {}          # track name -> tid
        self._open_spans = {}    # track name -> [span name, ...] (auto-close 용)
        self._pid = os.getpid()
        if not self.enabled:
            return
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            self._f = open(path, "w", encoding="utf-8")
            self._f.write("[\n")
            self._write({"ph": "M", "name": "process_name", "pid": self._pid, "tid": 0,
                         "args": {"name": "FlowChem run"}})
        except Exception:
            self._f = None

    # ── 내부 (호출측은 _lock 보유) ─────────────────────────────────
    def _write(self, ev):
        self._f.write(json.dumps(ev, ensure_ascii=False) + ",\n")
        self._f.flush()

    def _tid(self, track):
        tid = self._tids.get(track)
        if tid is None:
            tid = len(self._tids) + 1
            self._tids[track] = tid
            self._write({"ph": "M", "name": "thread_name", "pid": self._pid,
                         "tid": tid, "args": {"name": track}})
        return tid

    @staticmethod
    def _ts_us(ts=None):
        return round((time.time() if ts is None else float(ts)) * 1e6, 1)

    def _emit(self, ph, track, name, ts=None, dur_sec=None, args=None, extra=None):
        if self._f is None:
            return
        try:
            with self._lock:
                if self._f is None:
                    return
                ev = {"ph": ph, "name": str(name)[:200], "cat": "flow",
                      "pid": self._pid, "tid": self._tid(str(track)),
                      "ts": self._ts_us(ts)}
                if dur_sec is not None:
                    ev["dur"] = round(float(dur_sec) * 1e6, 1)
                if args:
                    ev["args"] = args
                if extra:
                    ev.update(extra)
                self._write(ev)
        except Exception:
            pass

    # ── 공개 API (전부 예외 안전) ──────────────────────────────────
    def instant(self, track, name, args=None, ts=None):
        """순간 이벤트 (타이머 발화, 밸브 ACK, 로그 메시지 등)."""
        self._emit("i", track, name, ts=ts, args=args, extra={"s": "t"})

    def begin(self, track, name, args=None, ts=None):
        """스팬 시작. end() 누락 시 close() 가 자동으로 닫는다."""
        self._emit("B", track, name, ts=ts, args=args)
        try:
            with self._lock:
                self._open_spans.setdefault(str(track), []).append(str(name)[:200])
        except Exception:
            pass

    def end(self, track, ts=None):
        """해당 트랙의 가장 최근 스팬 종료."""
        try:
            with self._lock:
                stack = self._open_spans.get(str(track))
                name = stack.pop() if stack else ""
        except Exception:
            name = ""
        self._emit("E", track, name, ts=ts)

    def complete(self, track, name, start_ts, dur_sec, args=None):
        """이미 끝난 구간을 한 번에 기록 (X 이벤트) — 밸브 전환·이동 소요 등."""
        self._emit("X", track, name, ts=start_ts, dur_sec=max(0.0, dur_sec), args=args)

    def counter(self, track, values, ts=None):
        """수치 트랙 (온도, 압력, 유량). values = {계열명: 값} dict."""
        if not isinstance(values, dict):
            values = {"value": values}
        clean = {}
        for k, v in values.items():
            try:
                clean[str(k)] = round(float(v), 3)
            except Exception:
                pass
        if clean:
            self._emit("C", track, str(track), ts=ts, args=clean)

    def close(self):
        """열린 스팬을 전부 닫고 엄격한 JSON 으로 마감. 이후 호출은 전부 no-op."""
        if self._f is None:
            return
        try:
            with self._lock:
                if self._f is None:
                    return
                now = self._ts_us()
                for track, stack in self._open_spans.items():
                    tid = self._tid(track)
                    for name in reversed(stack):
                        self._write({"ph": "E", "name": name, "cat": "flow",
                                     "pid": self._pid, "tid": tid, "ts": now})
                self._open_spans.clear()
                # 마지막 원소는 콤마 없이 → 유효한 JSON 배열로 마감
                self._f.write(json.dumps(
                    {"ph": "M", "name": "trace_closed", "pid": self._pid, "tid": 0,
                     "args": {}}, ensure_ascii=False) + "\n]\n")
                self._f.close()
                self._f = None
        except Exception:
            try:
                self._f = None
            except Exception:
                pass


NULL_TRACER = NullTracer()
