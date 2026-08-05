
import time
import csv
import json
import sys
import os
import threading
from datetime import datetime
from .safety_manager import SafetyError

class FlowEngine:
    def __init__(self, config, pumps, valves, heater, safety_mgr):
        self.cfg = config
        self.pumps = pumps
        self.valves = valves
        self.heater = heater
        self.safety = safety_mgr
        
        self.csv_file = None
        self.writer = None
        self.start_time = 0
        self.log_dir = "logs" # 기본 로그 디렉토리
        
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._emergency_triggered = False

    def _init_log(self, name_prefix="Exp"):
        try:
            if not os.path.exists(self.log_dir): 
                os.makedirs(self.log_dir)
            filename = f"LOG_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name_prefix}.csv"
            filepath = os.path.join(self.log_dir, filename)
            
            self.csv_file = open(filepath, 'w', newline='')
            self.writer = csv.writer(self.csv_file)
            
            # 헤더 작성
            headers = ["Time_s", "Status", "Temp_C"] + [f"P_{k}_Bar" for k in self.pumps]
            self.writer.writerow(headers)
            
            self.start_time = time.time()
            print(f"[Info] Log saved to: {filepath}")
        except Exception as e:
            print(f"[Error] Log Init Failed: {e}")
            self.writer = None

    def _log(self, status):
        # Injection 시작 기준 경과 타이머 prefix — 사용자가 step 내부 타이밍을 추적하기 쉽도록
        inj = getattr(self, "injection_start_ts", None)
        if inj is not None and time.time() >= inj:
            el = int(time.time() - inj)
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

        if not self.writer: return

        elap = round(time.time() - self.start_time, 1)
        
        # 온도 읽기
        t_val = -999
        if self.heater:
            try: 
                t = self.heater.get_temperature()
                t_val = t if t is not None else 0
            except: pass
            
        # 압력 읽기
        p_vals = []
        for p in self.pumps.values():
            try: 
                v = p.get_pressure()
                p_vals.append(v if v is not None else 0)
            except: 
                p_vals.append(-999)
                
        try:
            self.writer.writerow([elap, msg, t_val] + p_vals)
            self.csv_file.flush()
        except: pass

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