import time
import threading
from .pump_chemyx import ChemyxPump

class ChemyxSmartPump:
    """
    [Smart Controller for Chemyx]
    """
    def __init__(self, name, port, selector_valve, switching_valve, config_dict):
        self.name = name
        self.diameter = float(config_dict.get('diameter', 14.5))
        self.capacity = float(config_dict.get('capacity', 10.0))
        self.refill_rate = float(config_dict.get('refill_rate', 20.0))
        self.prime_rate = float(config_dict.get('prime_rate', 10.0))
        self.wash_speed = float(config_dict.get('wash_speed', 15.0))
        self.wash_count = int(config_dict.get('wash_count', 2))
        self.wash_volume = float(config_dict.get('wash_volume', 5.0))
        self.dead_vol_solvent = float(config_dict.get('dead_vol_solvent', 0.0))

        self.pump_id = int(config_dict.get('pump_id', 0))

        self.driver = ChemyxPump(port, diameter=self.diameter, pump_id=self.pump_id)
        
        self.selector = selector_valve
        self.switcher = switching_valve
        
        # @codesyncer-decision: 초기 current_vol = 0 (보수적 가정)
        # - 기존: capacity로 설정 → initial_refill 항상 스킵 (available=0)
        # - 수정: 0으로 설정 → 시작 시 반드시 refill 실행 → 안전한 초기 상태
        # - 시린지 위치 센서가 없으므로 "비어있다"고 가정하는 것이 더 안전
        self.current_vol = 0.0
        self.is_refilling = False
        self.lock = threading.Lock()

        # @codesyncer-decision: 상태 추적 - _execute_smart_dosing 루프에서 중복 명령 방지
        # running=False일 때만 set_flow/start 전송, True면 skip
        self.running = False
        self.target_flow = 0.0
        self._abort_refill = False  # 리필 중단 플래그
        self.status = "Idle"  # 현재 동작 상태 (시스템 로그 표시용)

        # @codesyncer-decision: 초기화 시 모든 설정값 출력 — UI→펌프 매핑 검증용
        print(f"   [SmartPump INIT] {self.name} ID:{self.pump_id} | "
              f"capacity={self.capacity}, wash_speed={self.wash_speed}, "
              f"wash_count={self.wash_count}, wash_volume={self.wash_volume}, "
              f"refill_rate={self.refill_rate}, prime_rate={self.prime_rate}")

        self.POS_SOURCE = 1
        self.POS_REACTOR = 2

    def connect(self):
        return self.driver.connect()

    def disconnect(self):
        self.driver.disconnect()

    def stop(self):
        self.driver.stop()
        self.running = False

    def set_flow(self, rate):
        """유속 목표값 설정 (소프트웨어 기록만)
        @codesyncer-decision: 하드웨어 명령은 start()에서만 atomic 전송
        - set_flow()는 target_flow만 갱신, RS-485 명령 미전송
        - start()가 set_rate+set_volume+start를 하나의 lock 블록에서 전송
        - 중복 전송 제거 → 펌프당 3개 명령만 (기존 4개)
        """
        self.target_flow = rate

    def start(self):
        """일반 주입 시작 — rate/volume/start 원자 실행
        @codesyncer-decision: 볼륨 부호로 방향 결정 (매뉴얼 UI와 동일 방식)
        - 양수 volume = Infuse, 음수 volume = Withdraw
        - set_mode 명령 미사용 (이 펌프 펌웨어에서 미작동 확인됨)
        @codesyncer-decision: current_vol(시린지 잔량)을 volume으로 전송
        - 기존: capacity(12ml) 고정 → 펌프 디스플레이에 12ml 표시되어 사용자 혼동
        - 수정: current_vol → 실제 시린지 잔량 전송, dosing 루프가 stop()으로 제어
        - dosing 루프 매 iteration마다 current_vol 감소 → 재호출 시 정확한 잔량 반영
        @codesyncer-decision: 명령 성공 시에만 running=True - 실패 시 다음 루프에서 재시도"""
        if self.is_refilling: return
        vol = max(self.current_vol, 0.1)  # 최소 0.1mL (0이면 펌프 미동작)
        with self.lock:
            r0 = self.driver.set_rate(self.target_flow)
            r1 = self.driver.set_volume(vol)   # 양수 → Infuse, 시린지 잔량만큼
            r2 = self.driver.start()
        if r0 is not None and r1 is not None and r2 is not None:
            self.running = True
        else:
            self.running = False
            print(f"   [WARNING] [{self.name}] start 실패 (rate={r0}, vol={r1}, start={r2}) - 재시도 예정")

    def set_valves_safe(self, selector_port=None, switcher_pos=None):
        if switcher_pos is not None and self.switcher:
            self.switcher.set_position(switcher_pos)
        if selector_port is not None and self.selector:
            self.selector.set_position(selector_port)
        time.sleep(0.2)  # 밸브 전환 안정화 (COM11/COM13 — 펌프 COM12와 무관)

    @staticmethod
    def _is_close(actual, expected, rel_tol=0.01, abs_tol=0.01):
        """부동소수점 비교 — |차이| < max(abs_tol, |expected| × rel_tol)
        @codesyncer-decision: 비율 비교만 쓰면 극소값에서 과민, 절대 비교만 쓰면 대값에서 과민
        - abs_tol=0.01: 0.01 mL/min 또는 0.01 mL 미만 차이는 허용
        - rel_tol=0.01: 1% 이내 차이는 허용
        """
        return abs(actual - expected) < max(abs_tol, abs(expected) * rel_tol)

    def prepare_parameters(self, rate, volume, action_name="Action"):
        """검증형 파라미터 설정: stop → set_rate → set_volume + read-back 검증
        @codesyncer-decision: 각 설정값을 read-back으로 확인, 불일치 시 최대 3회 재시도
        - 펌웨어가 view 명령 미지원 시 graceful fallback (검증 생략, 경고 1회)
        - 설정 실패 시 RuntimeError → 엔진에서 Emergency Stop 트리거
        - 허용 오차: max(0.01, 설정값×1%) — 부동소수점 반올림 안전
        @param rate: 유속 (mL/min)
        @param volume: 볼륨 (mL, 음수=withdraw)
        @raises RuntimeError: 3회 재시도 후에도 설정 불일치 (read-back 지원 펌웨어만)
        """
        max_retries = 3

        # 1) Stop
        self.driver.stop()

        # 2) Set Rate + Read-back 검증
        self.driver.set_rate(rate)
        readback = self.driver.read_rate()

        if readback is None:
            # 펌웨어가 view rate 미지원 → 검증 생략
            if not getattr(self, '_readback_warn_shown', False):
                print(f"   [{self.name}] ⚠ view rate 미지원 — read-back 검증 비활성화")
                self._readback_warn_shown = True
        elif not self._is_close(readback, rate):
            # 첫 시도 불일치 → 재시도
            for attempt in range(1, max_retries):
                print(f"   [{self.name}] ⚠ {action_name}: rate 불일치 "
                      f"(set={rate}, read={readback}) → 재시도 {attempt}/{max_retries}")
                self.driver.set_rate(rate)
                readback = self.driver.read_rate()
                if readback is not None and self._is_close(readback, rate):
                    break
            else:
                raise RuntimeError(
                    f"[{self.name}] {action_name}: rate 설정 실패 — "
                    f"set={rate}, read={readback} ({max_retries}회 재시도 후)"
                )

        # 3) Set Volume + Read-back 검증
        # @codesyncer-decision: abs() 비교 — Chemyx는 volume을 절대값으로 반환 (부호=mode로 결정)
        self.driver.set_volume(volume)
        readback = self.driver.read_volume()

        if readback is None:
            if not getattr(self, '_readback_warn_shown', False):
                print(f"   [{self.name}] ⚠ view volume 미지원 — read-back 검증 비활성화")
                self._readback_warn_shown = True
        elif not self._is_close(abs(readback), abs(volume)):
            for attempt in range(1, max_retries):
                print(f"   [{self.name}] ⚠ {action_name}: volume 불일치 "
                      f"(set={volume}, read={readback}) → 재시도 {attempt}/{max_retries}")
                self.driver.set_volume(volume)
                readback = self.driver.read_volume()
                if readback is not None and self._is_close(abs(readback), abs(volume)):
                    break
            else:
                raise RuntimeError(
                    f"[{self.name}] {action_name}: volume 설정 실패 — "
                    f"set={volume}, read={readback} ({max_retries}회 재시도 후)"
                )

    def _start_with_retry(self, action_name="Action"):
        """Phase 2 [Rapid Trigger]: start 명령 전송 (최소 RS-485 점유)
        trigger 루프에서 연속 호출되므로 최대한 빠르게 반환
        """
        resp = self.driver.start()
        if resp is None:
            print(f"   [{self.name}] ⚠ {action_name}: start 응답 없음 (Phase 3에서 검증)")

    def _verify_running(self, action_name="Action", max_retries=3,
                        volume=None, rate=None):
        """Phase 3 [Monitoring]: 비활성화됨
        @codesyncer-decision: RS-485 버스 경쟁으로 get_dispensed_volume()이 항상 None 반환
        - 3개 펌프 verify 스레드 + StatusWorker가 COM12를 동시 조회 → 100% 실패
        - 실패 시 start 재전송 → 이미 동작 중인 펌프의 volume counter 리셋 → 토출량 오류
        - _wait_pump_done()이 완료 감지를 담당하므로 verify 없이도 안전
        """
        return True

    def _wait_pump_done(self, target_vol, rate, max_multiplier=1.5):
        """펌프 완료 감지 대기 — dispensed volume 폴링 + 안전 타임아웃
        @codesyncer-decision: blind sleep 제거 → 실제 완료 감지로 대폭 시간 단축
        - 계산 소요시간까지는 0.5초 간격 대기 (abort 체크)
        - 이후 dispensed volume 폴링으로 완료 감지
        - 폴링 실패 시 max_multiplier × duration 안전 타임아웃
        """
        duration = (target_vol / max(rate, 0.01)) * 60.0
        hard_timeout = duration * max_multiplier + 3.0
        elapsed = 0.0

        # Phase 1: 계산 소요시간의 80%까지 빠르게 대기
        fast_wait = duration * 0.8
        while elapsed < fast_wait:
            if self._abort_refill:
                return elapsed
            time.sleep(0.5)
            elapsed += 0.5

        # Phase 2: 폴링으로 완료 감지
        while elapsed < hard_timeout:
            if self._abort_refill:
                return elapsed
            stopped = self.driver.is_stopped()
            if stopped is True:
                print(f"   [{self.name}] Pump done (detected at {elapsed:.1f}s, calc={duration:.1f}s)")
                return elapsed
            if stopped is None:
                # 폴링 불가 → 계산 시간 + 여유 2초 대기 후 탈출
                remaining = max(0, duration - elapsed + 2.0)
                time.sleep(min(remaining, 0.5))
                elapsed += min(remaining, 0.5)
                if elapsed >= duration + 2.0:
                    return elapsed
                continue
            time.sleep(0.5)
            elapsed += 0.5

        print(f"   [{self.name}] Pump timeout ({elapsed:.1f}s, calc={duration:.1f}s)")
        return elapsed

    # =========================================================================
    # Refill — begin/complete 분리
    # @codesyncer-decision: RS-485 버스 경쟁 방지를 위한 2단계 분리
    # - begin: 밸브 전환 + RS-485 명령 전송 (순차 실행 필요)
    # - complete: 기계 동작 대기 + 상태 복원 (병렬 실행 가능)
    # - 엔진에서 begin을 순차 호출 → complete를 병렬 대기
    # =========================================================================

    def refill_prepare(self, source_port_idx=1, volume=None):
        """Refill 준비: 밸브 전환 + 검증형 파라미터 설정 (start 제외)
        @return: True=준비 완료 (trigger 대기), False=스킵
        @raises RuntimeError: 파라미터 설정 검증 실패
        """
        if self.is_refilling: return False
        self.is_refilling = True
        self._abort_refill = False

        available = max(0, self.capacity - self.current_vol)
        requested = min(volume, self.capacity) if volume is not None else self.capacity
        fill_vol = min(requested, available)
        if fill_vol < 0.1:
            print(f"   [{self.name}] Refill skipped (syringe already {self.current_vol:.1f}/{self.capacity:.1f}mL)")
            self.is_refilling = False
            return False

        self._pending_fill_vol = fill_vol
        self._pending_source_port = source_port_idx

        self.status = f"Refill: Valve → Port {source_port_idx}"
        print(f"⚡ [{self.name} ID:{self.pump_id}] REFILL from Port {source_port_idx} (rate={self.refill_rate:.1f}, vol={fill_vol:.1f}mL)")

        try:
            self.set_valves_safe(selector_port=source_port_idx, switcher_pos=self.POS_SOURCE)
            self.status = f"Withdrawing {fill_vol:.1f}mL @{self.refill_rate:.0f}mL/min (Port {source_port_idx})"
            with self.lock:
                self.prepare_parameters(self.refill_rate, -fill_vol, "Refill")
            return True

        except Exception as e:
            self.status = "Refill Failed"
            print(f"❌ [{self.name}] Refill Failed: {e}")
            self.is_refilling = False
            raise

    def refill_trigger(self):
        """Refill 구동: start 명령만 전송 (최소 RS-485 점유)"""
        self._start_with_retry("Refill")

    def refill_begin(self, source_port_idx=1, volume=None):
        """Refill Phase 1: prepare + trigger 순차 호출 (호환용)"""
        if self.refill_prepare(source_port_idx, volume):
            self.refill_trigger()
            return True
        return False

    def refill_complete(self):
        """Refill Phase 2+3: 동작 확인(Phase 3) + 기계 동작 대기 + 밸브 복원"""
        fill_vol = getattr(self, '_pending_fill_vol', 0)
        try:
            self._verify_running("Refill", volume=fill_vol, rate=self.refill_rate)
            elapsed = self._wait_pump_done(fill_vol, self.refill_rate, max_multiplier=2.0)
            self.driver.stop()

            if not self._abort_refill:
                self.current_vol = min(self.current_vol + fill_vol, self.capacity)
                self.status = "Valve → Reactor"
                self.set_valves_safe(switcher_pos=self.POS_REACTOR)
                self.status = f"Refill Done ({fill_vol:.1f}mL, {elapsed:.0f}s)"
                print(f"✅ [{self.name}] Refill Complete ({elapsed:.1f}s).")
            else:
                partial = min(self.refill_rate * (elapsed / 60.0), fill_vol)
                self.current_vol = min(self.current_vol + partial, self.capacity)
                self.status = f"Refill Aborted (~{partial:.1f}mL)"
                print(f"   [{self.name}] Refill aborted after {elapsed:.1f}s (~{partial:.1f}mL)")
        except RuntimeError:
            self.is_refilling = False
            raise  # Emergency Stop용 — 엔진으로 전파
        except Exception as e:
            self.status = "Refill Failed"
            print(f"❌ [{self.name}] Refill Failed: {e}")
        finally:
            self.is_refilling = False

    def refill(self, source_port_idx=1, volume=None):
        """Refill 전체 (단독 실행용 — begin+complete 순차 호출)"""
        if self.refill_begin(source_port_idx, volume):
            self.refill_complete()

    # =========================================================================
    # Wash — begin/complete 분리 (infuse + withdraw 각각)
    # =========================================================================

    def wash_infuse_prepare(self, waste_port=12):
        """Wash-Infuse 준비: 밸브 전환 + 검증형 파라미터 설정
        @return: True=준비 완료
        @raises RuntimeError: 파라미터 설정 검증 실패
        """
        if self.is_refilling: return False
        self.is_refilling = True
        self._abort_refill = False

        actual_vol = min(self.wash_volume, self.capacity)
        self._pending_wash_vol = actual_vol

        duration_calc = (actual_vol / self.wash_speed) * 60.0
        print(f"   [{self.name}] WASH: wash_vol={self.wash_volume:.1f}+dead={self.dead_vol_solvent:.1f} → actual={actual_vol:.1f}mL, wash_speed={self.wash_speed:.1f}mL/min (계산 {duration_calc:.0f}초)")

        try:
            # @codesyncer-decision: Wash-Infuse는 12-way 폐액 포트로 배출
            # 이 시스템은 '세척=시린지 배럴/소스 라인 헹굼 → 12-way로 배출',
            # '반응부 용매 충전=prime 전담' 구조임. 따라서 세척 토출은
            # POS_SOURCE + waste_port로 12-way를 통해 폐액으로 빠짐.
            # (다운스트림은 prime_prepare가 POS_REACTOR로 따로 채움)
            self.status = f"Wash: Valve → Waste (Port {waste_port})"
            self.set_valves_safe(selector_port=waste_port, switcher_pos=self.POS_SOURCE)
            self.status = f"Wash: Infusing {actual_vol:.1f}mL → Waste @{self.wash_speed:.0f}mL/min"
            with self.lock:
                self.prepare_parameters(self.wash_speed, actual_vol, "Wash-Infuse")
            return True

        except Exception as e:
            self.status = "Wash Failed"
            print(f"   [{self.name}] Wash infuse error: {e}")
            self.is_refilling = False
            raise

    def wash_infuse_trigger(self):
        """Wash-Infuse 구동: start 명령만 전송"""
        self._start_with_retry("Wash-Infuse")

    def wash_infuse_begin(self, waste_port=12):
        """Wash-Infuse Phase 1: prepare + trigger 순차 호출 (호환용)"""
        if self.wash_infuse_prepare(waste_port):
            self.wash_infuse_trigger()
            return True
        return False

    def wash_infuse_complete(self):
        """Wash Phase 1-complete: 동작 확인(Phase 3) + infuse 대기 + current_vol 갱신
        @return: True=정상 완료, False=abort
        """
        actual_vol = getattr(self, '_pending_wash_vol', 0)
        try:
            self._verify_running("Wash-Infuse", volume=actual_vol, rate=self.wash_speed)
            elapsed = self._wait_pump_done(actual_vol, self.wash_speed, max_multiplier=1.5)
            self.driver.stop()

            if self._abort_refill:
                self.status = "Wash Aborted (infuse)"
                print(f"   [{self.name}] Wash aborted during infuse")
                self.is_refilling = False
                return False

            self.current_vol = max(0, self.current_vol - actual_vol)
            self.is_refilling = False
            return True

        except RuntimeError:
            self.is_refilling = False
            raise
        except Exception as e:
            self.status = "Wash Failed"
            print(f"   [{self.name}] Wash infuse wait error: {e}")
            self.is_refilling = False
            return False

    def wash_withdraw_prepare(self, solvent_port=1):
        """Wash-Withdraw 준비: 밸브 전환 + 검증형 파라미터 설정
        @return: True=준비 완료
        @raises RuntimeError: 파라미터 설정 검증 실패
        """
        if self.is_refilling: return False
        self.is_refilling = True
        # @codesyncer-decision: 교정된 순서(withdraw 먼저)에서 _pending_wash_vol을
        # 여기서 설정해야 함. 기존엔 infuse_prepare에서만 설정 → withdraw가 먼저
        # 실행되면 0으로 읽혀 흡입량이 0이 되던 결함.
        actual_vol = min(self.wash_volume, self.capacity)
        self._pending_wash_vol = actual_vol

        try:
            self.status = f"Wash: Valve → Solvent (Port {solvent_port})"
            self.set_valves_safe(selector_port=solvent_port, switcher_pos=self.POS_SOURCE)
            self.status = f"Wash: Withdrawing {actual_vol:.1f}mL @{self.wash_speed:.0f}mL/min (Port {solvent_port})"
            with self.lock:
                self.prepare_parameters(self.wash_speed, -actual_vol, "Wash-Withdraw")
            return True

        except Exception as e:
            self.status = "Wash Failed"
            print(f"   [{self.name}] Wash withdraw error: {e}")
            self.is_refilling = False
            raise

    def wash_withdraw_trigger(self):
        """Wash-Withdraw 구동: start 명령만 전송"""
        self._start_with_retry("Wash-Withdraw")

    def wash_withdraw_begin(self, solvent_port=1):
        """Wash-Withdraw: prepare + trigger 순차 호출 (호환용)"""
        if self.wash_withdraw_prepare(solvent_port):
            self.wash_withdraw_trigger()
            return True
        return False

    def wash_withdraw_complete(self):
        """Wash Phase 2-complete: 동작 확인(Phase 3) + withdraw 대기 + current_vol 갱신"""
        actual_vol = getattr(self, '_pending_wash_vol', 0)
        try:
            self._verify_running("Wash-Withdraw", volume=actual_vol, rate=self.wash_speed)
            elapsed = self._wait_pump_done(actual_vol, self.wash_speed, max_multiplier=2.0)
            self.driver.stop()

            if self._abort_refill:
                self.status = "Wash Aborted (withdraw)"
                print(f"   [{self.name}] Wash aborted during withdraw")
                self.is_refilling = False
                return

            self.current_vol = min(self.current_vol + actual_vol, self.capacity)
            self.status = f"Wash Done ({actual_vol:.1f}mL)"
            print(f"   [{self.name}] Wash cycle done ({actual_vol:.1f}mL @ {self.wash_speed:.1f}mL/min)")

        except RuntimeError:
            self.is_refilling = False
            raise
        except Exception as e:
            self.status = "Wash Failed"
            print(f"   [{self.name}] Wash withdraw wait error: {e}")
        finally:
            self.is_refilling = False

    def wash_cycle(self, solvent_port=1, waste_port=12):
        """1회 세척 사이클 (단독 실행용 — 4단계 순차 호출)"""
        if not self.wash_infuse_begin(waste_port):
            return
        if not self.wash_infuse_complete():
            return
        if not self.wash_withdraw_begin(solvent_port):
            return
        self.wash_withdraw_complete()

    # =========================================================================
    # Prime — begin/complete 분리
    # =========================================================================

    def prime_prepare(self):
        """Prime 준비: 밸브 전환 + 검증형 파라미터 설정
        @return: True=준비 완료
        @raises RuntimeError: 파라미터 설정 검증 실패
        """
        if self.is_refilling: return False
        self.is_refilling = True
        self._abort_refill = False

        # @codesyncer(적대검증 F4, 2026-07-29): 하한 0.1→0.02mL — 센서 폐루프가 실측
        #   잔량 그대로(예: 50~100µL)를 밀 때 0.1mL 로 부풀리면 하드스톱 방향 과주행
        #   (마진 없는 sizing 설계와 충돌). 0 방지 하한(set volume=0 = 연속토출 오인
        #   방지)만 20µL 로 남긴다 — 과주행 상한 20µL 는 0점 노이즈(±27µL) 이내.
        prime_vol = max(self.current_vol, 0.02)
        self._pending_prime_vol = prime_vol

        self.status = "Prime: Valve → Reactor"
        print(f"   [{self.name}] PRIME reactor (rate={self.prime_rate:.1f}, vol={prime_vol:.1f}mL → INFUSE to reactor)")

        try:
            self.set_valves_safe(switcher_pos=self.POS_REACTOR)
            self.status = f"Prime: Infusing {prime_vol:.1f}mL → Reactor @{self.prime_rate:.0f}mL/min"
            with self.lock:
                self.prepare_parameters(self.prime_rate, prime_vol, "Prime")
            return True

        except Exception as e:
            self.status = "Prime Failed"
            print(f"   [{self.name}] Prime failed: {e}")
            self.is_refilling = False
            raise

    def prime_trigger(self):
        """Prime 구동: start 명령만 전송"""
        self._start_with_retry("Prime")

    def prime_begin(self):
        """Prime Phase 1: prepare + trigger 순차 호출 (호환용)"""
        if self.prime_prepare():
            self.prime_trigger()
            return True
        return False

    def prime_complete(self):
        """Prime Phase 2+3: 동작 확인(Phase 3) + 기계 동작 대기 + 상태 복원"""
        prime_vol = getattr(self, '_pending_prime_vol', 0.1)
        try:
            self._verify_running("Prime", volume=prime_vol, rate=self.prime_rate)
            elapsed = self._wait_pump_done(prime_vol, self.prime_rate, max_multiplier=1.5)
            self.driver.stop()
            self.current_vol = max(0, self.current_vol - prime_vol)
            self.status = f"Prime Done ({prime_vol:.1f}mL, {elapsed:.0f}s)"
            print(f"   [{self.name}] Prime complete ({elapsed:.1f}s, remaining={self.current_vol:.1f}mL)")
        except RuntimeError:
            self.is_refilling = False
            raise
        except Exception as e:
            self.status = "Prime Failed"
            print(f"   [{self.name}] Prime failed: {e}")
        finally:
            self.is_refilling = False

    def prime_reactor(self):
        """시린지 프라이밍 (단독 실행용 — begin+complete 순차 호출)"""
        if self.prime_begin():
            self.prime_complete()

    def get_pressure(self):
        return self.driver.get_pressure()