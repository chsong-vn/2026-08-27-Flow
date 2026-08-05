# -*- coding: utf-8 -*-
"""
Deep Wash — 12-way 전 라인(포트 2~11 스터브 포함) 세척 루틴.

@codesyncer-context: 2026-08-05 사용자 설계 확정(B안):
  포트 2~11 바이알에 세척액을 채워 두고, 각 포트에서 '흡인'하면 스터브가
  바이알의 깨끗한 용매로 씻기며 잔여물이 공통 경로로 딸려 들어옴 → 12번
  폐액 포트로 배출. 마지막에 포트1 용매로 공통 경로 헹굼(=12번 폐액라인도
  깨끗한 용매로 마감), 그리고 포트1 용매를 반응기 방향으로 밀어 다운스트림
  플러시(Outlet=WASTE).

@codesyncer-decision: 배칭 — 시린지 용량(6mL/1mL)까지 여러 포트를 연속
  흡인해 모았다가 한 번에 12번으로 배출. 포트당 흡인+배출 반복 대비 밸브
  전환/배출 동작 절반 이하.

@codesyncer-decision: 명령 직렬화 — RS-485 버스(펌프 COM8, 12-way COM9)
  충돌 방지를 위해 모든 prepare/trigger(=버스 명령 발생 구간)는 전역
  _cmd_lock + 0.35s 간격으로 직렬 실행. complete(폴링 대기)는 그룹 스레드
  병렬 — strict_engine 의 '순차 begin + 병렬 complete' 관례와 동일.

@codesyncer-decision: 그룹별 독립 상태머신(스레드) — Group_D(1mL)는
  배치가 작아 사이클 수가 달라도 다른 그룹을 블록하지 않음 (사용자 요구:
  각 그룹 따로따로 동시 진행).

@codesyncer-risk: pump.wash_volume 을 일시 변경해 임의 볼륨 흡인/배출을
  구현 (하드웨어 계층 무수정 원칙). 그룹 스레드는 자기 펌프만 만지므로
  경쟁 없음 — finally 에서 원복. 시퀀스 실행 중에는 시작 자체를 차단할 것
  (UI 인터락 + start() 이중 방어).
"""
import threading
import time


class DeepWashOptions:
    """세척 파라미터 (전 그룹 공통 — 볼륨 상한은 그룹별 시린지 용량으로 클램프)."""

    def __init__(self, ports=None, v_port=0.5, common_cycles=2, v_common=3.0,
                 downstream_ml=3.0, include_collection=False, collection_ml=0.5,
                 batch_dump=False):
        self.ports = list(ports) if ports else list(range(2, 12))  # 기본 2~11
        self.v_port = float(v_port)             # 포트당 흡인량 (스터브×1.5 권장)
        self.common_cycles = int(common_cycles)  # P1 공통 경로 세척 사이클
        self.v_common = float(v_common)          # 공통 세척 1사이클 흡인량
        self.downstream_ml = float(downstream_ml)   # P4 반응기 방향 플러시량
        self.include_collection = bool(include_collection)
        self.collection_ml = float(collection_ml)
        # @codesyncer-decision(2026-08-05 사용자 확정): 기본 = 포트마다 즉시
        #   "N 흡인 → 12 배출" 반복 (12way·3way·펌프가 포트 단위로 유기 동작).
        #   True 면 시린지가 찰 때까지 여러 포트를 모아 일괄 배출 (밸브 전환
        #   횟수 절약 옵션 — 잔여물이 시린지에 누적되는 대신 빠름).
        self.batch_dump = bool(batch_dump)
        # 유량 오버라이드 (mL/min). 0 = 그룹 설정값 사용
        #   wash_rate       → P0~P3 흡인/배출 (pump.wash_speed)
        #   downstream_rate → P4 반응기 방향 플러시 (pump.prime_rate)
        self.wash_rate = 0.0
        self.downstream_rate = 0.0


class DeepWashEngine:
    SOLVENT_PORT = 1
    WASTE_PORT = 12
    CMD_INTERVAL = 0.35   # strict_engine sequential trigger 간격과 동일
    POS_WASTE = 1         # Outlet 3-way: 1=WASTE, 2=COLLECT (E-Stop 관례와 동일)
    POS_COLLECT = 2

    def __init__(self, pumps, outlet_valve=None, options=None, log=print):
        """@param pumps: {그룹명: SmartPump} — external_valve 라우팅만 넣을 것."""
        self.pumps = dict(pumps)
        self.outlet = outlet_valve
        self.opt = options or DeepWashOptions()
        self._log_cb = log
        self._cmd_lock = threading.Lock()
        self._last_cmd = 0.0
        self._stop_req = False
        self._threads = []
        self.running = False
        self.results = {}   # {그룹: "done"|"aborted"|"error: ..."}

    # ── 공용 유틸 ─────────────────────────────────────────────────
    def _log(self, msg):
        try:
            self._log_cb(f"[DeepWash] {msg}")
        except Exception:
            pass

    def _cmd(self, fn, *args, **kwargs):
        """버스 명령 구간 직렬화 + 최소 간격 보장."""
        with self._cmd_lock:
            wait = self.CMD_INTERVAL - (time.time() - self._last_cmd)
            if wait > 0:
                time.sleep(wait)
            try:
                return fn(*args, **kwargs)
            finally:
                self._last_cmd = time.time()

    def _aborted(self, pump):
        return self._stop_req or bool(getattr(pump, "_abort_refill", False))

    # ── 펌프 단위 동작 (그룹 스레드에서 호출) ─────────────────────
    def _withdraw(self, pump, port, vol):
        """포트 port 에서 vol 흡인 (시린지 여유량으로 클램프). False=중단."""
        if self._aborted(pump):
            return False
        room = max(0.0, float(pump.capacity) - float(pump.current_vol))
        eff = min(float(vol), room)
        if eff < 0.05:
            return True  # 여유 없음 — 호출측이 먼저 dump 하도록 설계돼 있음
        pump.wash_volume = eff
        if not self._cmd(pump.wash_withdraw_prepare, port):
            return False
        self._cmd(pump.wash_withdraw_trigger)
        pump.wash_withdraw_complete()
        return not self._aborted(pump)

    def _dump(self, pump):
        """시린지 잔량 전부 → 12번 폐액 포트. False=중단."""
        if self._aborted(pump):
            return False
        vol = float(pump.current_vol)
        if vol < 0.05:
            return True
        pump.wash_volume = vol
        if not self._cmd(pump.wash_infuse_prepare, self.WASTE_PORT):
            return False
        self._cmd(pump.wash_infuse_trigger)
        ok = pump.wash_infuse_complete()
        return bool(ok) and not self._aborted(pump)

    def _downstream(self, pump, vol):
        """포트1 용매 vol 흡인 → 반응기 방향(prime) 배출. False=중단."""
        if vol <= 0:
            return True
        if not self._withdraw(pump, self.SOLVENT_PORT, vol):
            return False
        if self._aborted(pump):
            return False
        if not self._cmd(pump.prime_prepare):
            return False
        self._cmd(pump.prime_trigger)
        pump.prime_complete()
        return not self._aborted(pump)

    # ── 그룹 상태머신 ─────────────────────────────────────────────
    def _run_group(self, name, pump):
        opt = self.opt
        saved_wash_vol = getattr(pump, "wash_volume", None)
        saved_wash_speed = getattr(pump, "wash_speed", None)
        saved_prime_rate = getattr(pump, "prime_rate", None)
        # 유량 오버라이드 (0 = 그룹 설정 유지). Group_D 등 소구경 시린지는
        # 과속 시 스톨 위험 — 다이얼로그 툴팁으로 안내, finally 에서 원복.
        if float(getattr(opt, "wash_rate", 0.0) or 0.0) > 0:
            pump.wash_speed = float(opt.wash_rate)
        if float(getattr(opt, "downstream_rate", 0.0) or 0.0) > 0:
            pump.prime_rate = float(opt.downstream_rate)
        used = 0.0
        try:
            cap = float(pump.capacity)
            batch_limit = cap * 0.95
            self._log(f"{name}: 시작 (시린지 {cap:.1f} mL, 포트 {opt.ports[0]}~{opt.ports[-1]}, "
                      f"{opt.v_port:.2f} mL/port)")

            # P0 — 잔량 배출 (빈 시린지 전제 확보)
            if not self._dump(pump):
                raise _Aborted()

            # P1 — 공통 경로 벌크 세척: 1번 흡인 → 12번 배출 × cycles
            v_common = min(opt.v_common, cap)
            for c in range(opt.common_cycles):
                if not self._withdraw(pump, self.SOLVENT_PORT, v_common):
                    raise _Aborted()
                used += v_common
                if not self._dump(pump):
                    raise _Aborted()
                self._log(f"{name}: 공통 세척 {c + 1}/{opt.common_cycles}")

            # P2 — 포트 순회: 각 포트 흡인(스터브 세척) → 12번 배출.
            #   기본 = 포트마다 즉시 배출 (2 흡인→12 배출→3 흡인→12 배출 …).
            #   batch_dump=True 면 시린지가 찰 때까지 모아 일괄 배출.
            done_ports = []
            for port in opt.ports:
                if opt.batch_dump and (
                        float(pump.current_vol) + opt.v_port > batch_limit):
                    if not self._dump(pump):
                        raise _Aborted()
                if not self._withdraw(pump, port, opt.v_port):
                    raise _Aborted()
                used += opt.v_port
                done_ports.append(port)
                if opt.batch_dump:
                    self._log(f"{name}: 포트 {port} 흡인 완료 "
                              f"(배치 {float(pump.current_vol):.2f}/{cap:.1f} mL)")
                else:
                    if not self._dump(pump):
                        raise _Aborted()
                    self._log(f"{name}: 포트 {port} 흡인 → 12번 배출 완료")
            if not self._dump(pump):
                raise _Aborted()

            # P3 — 공통 헹굼 + 폐액라인(12) 마감: 마지막에 깨끗한 용매가 12를 통과
            if not self._withdraw(pump, self.SOLVENT_PORT, v_common):
                raise _Aborted()
            used += v_common
            if not self._dump(pump):
                raise _Aborted()

            # P4 — 다운스트림 플러시 (Outlet=WASTE 는 start()에서 전역 설정됨)
            v_down = min(opt.downstream_ml, cap)
            if v_down > 0:
                if not self._downstream(pump, v_down):
                    raise _Aborted()
                used += v_down

            self.results[name] = "done"
            self._log(f"{name}: 완료 — 포트 {len(done_ports)}개, 용매 약 {used:.1f} mL 사용")
        except _Aborted:
            self.results[name] = "aborted"
            self._log(f"{name}: 중단됨 (용매 약 {used:.1f} mL 사용)")
        except Exception as e:
            self.results[name] = f"error: {e}"
            self._log(f"{name}: 오류 — {e}")
        finally:
            if saved_wash_vol is not None:
                pump.wash_volume = saved_wash_vol
            if saved_wash_speed is not None:
                pump.wash_speed = saved_wash_speed
            if saved_prime_rate is not None:
                pump.prime_rate = saved_prime_rate

    # ── 라이프사이클 ──────────────────────────────────────────────
    def start(self):
        """세척 시작 (논블로킹). False=시작 거부."""
        if self.running or not self.pumps:
            return False
        busy = [n for n, p in self.pumps.items() if getattr(p, "is_refilling", False)]
        if busy:
            self._log(f"시작 거부 — 동작 중인 펌프: {', '.join(busy)}")
            return False
        self.running = True
        self._stop_req = False
        self.results = {}

        # Outlet → WASTE 전역 강제 (다운스트림 플러시가 폐액병으로 가도록)
        if self.outlet is not None:
            try:
                self.outlet.set_position(self.POS_WASTE)
                self._log("Outlet → WASTE")
            except Exception as e:
                self._log(f"⚠ Outlet 전환 실패: {e} — 계속 진행")

        self._threads = []
        for name, pump in self.pumps.items():
            t = threading.Thread(target=self._run_group, args=(name, pump),
                                 daemon=True, name=f"deepwash-{name}")
            self._threads.append(t)
            t.start()
        threading.Thread(target=self._finish_watcher, daemon=True,
                         name="deepwash-watch").start()
        return True

    def _finish_watcher(self):
        for t in self._threads:
            t.join()
        # 옵션: 컬렉션 라인(아웃렛→분취기, 0.24 mL) 세척 — 분취기를 폐수 위치에
        # 두고 쓰는 옵션. Outlet=COLLECT 로 잠깐 전환 후 소량 플러시, 원복.
        if (self.opt.include_collection and not self._stop_req
                and self.outlet is not None):
            try:
                self.outlet.set_position(self.POS_COLLECT)
                self._log("Outlet → COLLECT (컬렉션 라인 세척)")
                for name, pump in self.pumps.items():
                    if self.results.get(name) == "done":
                        self._downstream(pump, self.opt.collection_ml)
            except Exception as e:
                self._log(f"⚠ 컬렉션 라인 세척 실패: {e}")
            finally:
                try:
                    self.outlet.set_position(self.POS_WASTE)
                    self._log("Outlet → WASTE (원복)")
                except Exception:
                    pass
        self.running = False
        summary = ", ".join(f"{n}={v}" for n, v in self.results.items())
        self._log(f"전체 종료 — {summary}")

    def stop(self):
        """중단 요청 — 진행 중 동작은 펌프 abort 로 정리.

        @codesyncer-decision: pump.stop() 은 driver.stop() 만 하고 _abort_refill
        은 세우지 않음 (app_control E-Stop 이 별도로 세우는 관례) — 여기서도
        직접 세워 wash/refill complete 대기 루프가 abort 로 빠지게 한다."""
        self._stop_req = True
        for pump in self.pumps.values():
            try:
                pump._abort_refill = True
                pump.stop()
            except Exception:
                pass
        self._log("중단 요청 — 펌프 정지")


class _Aborted(Exception):
    pass
