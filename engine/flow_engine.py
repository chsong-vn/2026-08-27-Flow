
import time
import csv
import json
import sys
import os
import threading
from datetime import datetime
from .safety_manager import SafetyError
from .trace_log import TraceLogger, NULL_TRACER

class FlowEngine:
    # @codesyncer-decision: trace 는 '클래스 속성' 기본값 — 테스트가 __init__ 을
    #   우회해 엔진을 만드는 패턴(__new__/부분 스텁)에서도 self.trace 접근이
    #   항상 안전하도록. _init_log 가 인스턴스 속성으로 실제 로거를 덮어쓴다.
    trace = NULL_TRACER

    def __init__(self, config, pumps, valves, heater, safety_mgr):
        self.cfg = config
        self.pumps = pumps
        self.valves = valves
        self.heater = heater
        self.safety = safety_mgr

        self.csv_file = None
        self.writer = None
        self.trace = NULL_TRACER  # _init_log 에서 실제 TraceLogger 로 교체
        self.start_time = 0
        # 로그 폴더도 프로젝트 루트 앵커 (config.py 와 동일 결정 2026-08-12) —
        # CWD 가 어디든 logs/ 는 항상 루트에 쌓인다
        self.log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._emergency_triggered = False
        # @codesyncer(2026-08-18, 실기 발견): CSV 행 기록은 호출 스레드에서 직접
        #   수행되는데 락이 없어, 병행 스레드(PrecalChain 등)와 메인 스레드가
        #   동시에 로그하는 창에서 행이 조용히 유실됐다(트레이스에는 남고 CSV만
        #   빠짐 — N2Precal 실행 여부 오진의 원인). writer 접근 전체를 직렬화.
        self._log_lock = threading.Lock()

    def _init_log(self, name_prefix="Exp", trace=True):
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"LOG_{stamp}_{name_prefix}.csv"
            filepath = os.path.join(self.log_dir, filename)

            # @codesyncer(2026-08-19, 실기 발견): encoding 미지정 = Windows cp949 —
            #   —·✓ 등 cp949 밖 문자가 든 줄은 writerow 가 UnicodeEncodeError 를
            #   던지고 except:pass 에 삼켜져 '그 줄만' 증발했다 (PushLinePrime/
            #   N2Precal 로그가 CSV 에서만 사라지던 진짜 원인 — 어제의 락 진단은
            #   동시성 유실만 막은 부분 수정). utf-8-sig = 엑셀 한글 호환 + 전 문자.
            self.csv_file = open(filepath, 'w', newline='',
                                 encoding='utf-8-sig', errors='replace')
            self.writer = csv.writer(self.csv_file)

            # 헤더 작성
            headers = ["Time_s", "Status", "Temp_C"] + [f"P_{k}_Bar" for k in self.pumps]
            self.writer.writerow(headers)

            # CSV Time_s 경과축 = monotonic (P2, 2026-08-12) — NTP 점프 무관
            self.start_time = time.monotonic()
            print(f"[Info] Log saved to: {filepath}")

            # Perfetto 트레이스 (CSV 와 동일 생명주기·타임스탬프 — ui.perfetto.dev 로 열람)
            # @codesyncer(검증 2026-08-11): print 는 ASCII 전용 — 이 try 안의 한글
            #   print 가 UnicodeEncodeError 로 writer=None(CSV 전멸)을 만들 수 있음
            #   (cp949 콘솔 전례). 판정은 enabled 가 아닌 active(실제 open 성공).
            if trace:
                self.trace = TraceLogger(os.path.join(self.log_dir, f"TRACE_{stamp}_{name_prefix}.json"))
                if getattr(self.trace, "active", False):
                    print(f"[Info] Trace: {self.trace.path} (open in ui.perfetto.dev)")
        except Exception as e:
            print(f"[Error] Log Init Failed: {e}")
            self.writer = None

    def _log(self, status):
        # Injection 시작 기준 경과 타이머 prefix — 사용자가 step 내부 타이밍을 추적하기 쉽도록
        inj = getattr(self, "injection_start_ts", None)
        if inj is not None and time.monotonic() >= inj:
            el = int(time.monotonic() - inj)
            h, rem = divmod(el, 3600)
            m, s = divmod(rem, 60)
            prefix = f"[T+{h}:{m:02d}:{s:02d}] " if h > 0 else f"[T+{m:02d}:{s:02d}] "
        else:
            prefix = ""
        msg = f"{prefix}{status}"

        # @codesyncer: 콘솔 print 인코딩 방어 — cp949 콘솔에서 ⚠/→ 등 심볼이
        #   UnicodeEncodeError 로 '엔진을 중단'시키던 결함. 콘솔은 보조 출력이므로
        #   못 찍는 문자만 치환하고 진행 (UI sig_log 는 유니코드 그대로).
        try:
            print(f"   [ENGINE] {msg}")
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(f"   [ENGINE] {msg}".encode(enc, "replace").decode(enc))

        # UI SYSTEM LOG 패널로 bridging — engine은 base class에 signals가 없을 수 있음
        sig = getattr(self, "signals", None)
        if sig is not None:
            try:
                sig.sig_log.emit(msg)
            except Exception:
                pass

        self.trace.instant("LOG", msg)

        if not self.writer: return

        # 온도/압력 읽기~writerow 전체를 락으로 직렬화 — 병행 스레드 동시 로깅 시
        # 행 유실 방지 (센서 read 는 각 드라이버 락이 있어도, writer/flush 공유가 문제)
        with self._log_lock:
            if not self.writer: return
            elap = round(time.monotonic() - self.start_time, 1)

            # 온도 읽기 (CSV 값 규약 유지: None→0, 예외/히터없음→-999)
            t_val = -999
            t_read = None
            if self.heater:
                try:
                    t_read = self.heater.get_temperature()
                    t_val = t_read if t_read is not None else 0
                except: pass

            # 압력 읽기 (CSV 값 규약 유지)
            p_vals = []
            p_reads = {}
            for k, p in self.pumps.items():
                try:
                    v = p.get_pressure()
                    p_vals.append(v if v is not None else 0)
                    if v is not None:
                        p_reads[k] = v
                except:
                    p_vals.append(-999)

            try:
                self.writer.writerow([elap, msg, t_val] + p_vals)
                self.csv_file.flush()
            except: pass

        # 트레이스 카운터 — 실측값만 기록 (None→0 을 그래프에 올리면
        # 가열 반응 중 가짜 '급랭'으로 읽힘 — 검증 발견)
        if t_read is not None:
            self.trace.counter("Temp(C)", {"temp": t_read})
        if p_reads:
            self.trace.counter("Pressure(bar)", p_reads)

    def _background_monitor(self, msg):
        """백그라운드에서 안전 상태를 감시하고 로그를 기록하는 스레드 함수"""
        while not self._stop_event.is_set():
            try:
                # 안전 점검
                self.safety.check_system()
                
                # 로그 기록
                self._log(msg)
                
                time.sleep(0.5) # 0.5초 주기
            except SafetyError:
                self._emergency_triggered = True
                self._stop_event.set()
                break
            except Exception: 
                pass

    def _start_mon(self, msg):
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._background_monitor, args=(msg,), daemon=True)
        self._monitor_thread.start()

    def _stop_mon(self):
        self._stop_event.set()
        if self._monitor_thread: 
            self._monitor_thread.join(timeout=1.0)

    def save_experiment_report(self, sequence_plan):
        """실험 완료 후 리포트 저장 (JSON)"""
        try:
            if not os.path.exists(self.log_dir): os.makedirs(self.log_dir)
            filename = f"REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(self.log_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(sequence_plan, f, indent=4, ensure_ascii=False)
            print(f"[Info] Experiment Report saved: {filepath}")
        except Exception as e:
            print(f"[Error] Report Save Failed: {e}")

    def emergency_stop(self, reason):
        self._stop_mon()
        self._emergency_triggered = True
        self.trace.instant("LOG", f"EMERGENCY STOP: {reason}")
        print(f"\n!!!!!! EMERGENCY STOP: {reason} !!!!!!")
        try: self.heater.stop()
        except: pass
        for p in self.pumps.values():
            try: p.stop()
            except: pass
        if self.csv_file: 
            self.csv_file.close()

    def run(self):
        # 기본 run 메서드 (StrictSequenceEngine에서는 오버라이드됨)
        pass