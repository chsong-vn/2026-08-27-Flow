"""오토샘플러 조율기 — autosampler 라우팅 그룹의 니들↔펌프 협응 (Phase C v0).

@codesyncer-decision: 원본 RoboChem Gen2 프로토콜의 이식.
  원본 소재: Robochem_Flex/OmniPlatypus procedures/unit_tasks/sampling/
    - liquid_handler_sampling.py :: PumpSample._execute (L1867-1990)
    - liquid_handler_moving.py   :: SamplerTask._move_xy/_move_z/_set_travel_position
    - liquid_handler_injecting.py:: Inject._execute (인젝션 포트 리그용 — v0 미사용)
  무수정 원본 사본: robochem_devices/_reference/originals/unit_tasks_sampling/

  원본 타이밍 모델(그대로 계승):
    - 모든 동작은 블로킹 호출 — 이동 완료는 GRBL G4 dwell ack, 펌프 완료는
      펌웨어 'k' ack 가 보장 (sleep 으로 순서 제어하지 않음)
    - 고정 sleep 은 ack 가 없는 물리 평형에만: 딥 전/후 기포 평형
      (원본 delay_before_dipping=0.1 / delay_after_pumping=0.1)
    - 니들 직렬화 = 별도 프리미티브 없이 "한 스레드에서 순차 블로킹 호출"
      그 자체 (원본 PrepareReactionSlug 도 동일)

@codesyncer-context: v0 토폴로지 = 직접 흡입형.
  니들 라인 = NRG 펌프의 저장조(reservoir)측 배관. 내장 메인밸브 OFF=니들,
  ON=시스템 → 니들 위치는 withdraw(리필) 동안에만 유효해야 하며, 토출(주입)
  중에는 무관하다. 따라서 조율기는 '리필 전 위치 확보 / 리필 후 리트랙트'만
  담당하고 CollectionTimer(분취 타이밍)와는 절대 겹치지 않는다.

@codesyncer-risk: 이동은 시리얼 ack 까지 블로킹(수 초~홈 120s) — 반드시
  엔진 워커 스레드에서 호출할 것 (GUI 스레드 금지). E-Stop 은 기존 전역
  경로(raw 0x18 락 우회)가 담당하므로 여기서는 다루지 않는다.
"""

import time


class SamplerCoordinationError(Exception):
    """조율 실패 — 엔진에서 SafetyError 로 승격된다."""


class SamplerCoordinator:
    """한 펌프 그룹에 배정된 카트리지언 샘플러의 니들 위치 조율.

    래퍼(GrblCartesianSampler/Mock)의 공개 API 만 사용:
      move_to_vial(vial_id) — Z-up→XY→Z-down 시퀀스 내장, 블로킹
      lift_needle() / return_home() / is_connected / vial_positions
    """

    # 원본 PumpSample 기본값 계승 (기포 평형 — ack 없는 물리 대기)
    DELAY_BEFORE_DIP_S = 0.1
    DELAY_AFTER_PUMP_S = 0.1

    def __init__(self, sampler, signals=None, group_name=""):
        self.sampler = sampler
        self.signals = signals
        self.group = group_name
        self._at_vial = None      # 현재 니들이 담긴 vial_id (None = 리트랙트 상태)

    # ── 로깅 ─────────────────────────────────────────────────
    def _log(self, msg):
        text = f"[AS·{self.group}] {msg}"
        if self.signals is not None:
            try:
                self.signals.sig_log.emit(text)
                return
            except Exception:
                pass
        print(text)

    # ── 사전 검증 (엔진 인터락에서 호출) ─────────────────────
    def ensure_ready(self, required_vials=()):
        """시퀀스 시작 전 검증: 연결 + 요청 vial 좌표 존재.

        원본에는 이 단계가 없지만(플랫폼 설정이 보장), 우리 엔진의
        인터락 원칙(시퀀스 중단보다 사전 차단)에 맞춰 추가한다.
        """
        if self.sampler is None:
            return False, "sampler 미배정"
        if not bool(getattr(self.sampler, "is_connected", False)):
            return False, "sampler 미연결"
        positions = getattr(self.sampler, "vial_positions", {}) or {}
        missing = [v for v in required_vials if v and v not in positions]
        if missing:
            return False, f"vial 좌표 없음: {missing} (positions 파일 확인)"
        return True, "ok"

    def service_vial(self, *names):
        """positions 파일에서 서비스 위치(rinse/waste 등) 이름 해석.

        원본 vial_positions.json 템플릿은 "waste"/"rinse"/"gas" 를 일반
        엔트리로 갖는다 — 대소문자 무시 첫 매칭 반환, 없으면 None.
        """
        positions = getattr(self.sampler, "vial_positions", {}) or {}
        low = {str(k).lower(): k for k in positions.keys()}
        for n in names:
            if str(n).lower() in low:
                return low[str(n).lower()]
        return None

    # ── 핵심 조율 ─────────────────────────────────────────────
    def position_for_withdraw(self, vial_id):
        """withdraw(흡입) 전에 니들을 소스 vial 에 담근다.

        원본 대응: PumpSample._execute 의 _move_xy→(aux)→plunge→dip 구간.
        우리 래퍼 move_to_vial 이 Z-up→XY→Z-down 을 내장하므로 1콜.
        블로킹 — 반환 시 니들이 액중에 있음이 ack 로 보장된 상태.
        """
        if self._at_vial == vial_id:
            return True, f"니들 이미 {vial_id}"
        self._log(f"니들 이동 → {vial_id}")
        t0 = time.time()
        try:
            ok, msg = self.sampler.move_to_vial(vial_id)
        except Exception as e:
            return False, f"이동 예외: {e}"
        if not ok:
            return False, f"이동 실패: {msg}"
        # 딥 직후 기포 평형 (원본 delay_before_dipping 대응 — dip 완료 후
        # 흡입 개시 전의 안정화로 위치를 옮겨 동일 효과)
        time.sleep(self.DELAY_BEFORE_DIP_S)
        self._at_vial = vial_id
        self._log(f"니들 {vial_id} 도달 ({time.time() - t0:.1f}s)")
        return True, msg

    def after_withdraw(self):
        """흡입 완료 후: 기포 평형 → 니들 리트랙트 (safe-Z).

        원본 대응: delay_after_pumping → retract(_set_travel_position).
        """
        time.sleep(self.DELAY_AFTER_PUMP_S)
        try:
            self.sampler.lift_needle()
        except Exception as e:
            self._log(f"리트랙트 경고: {e}")
        self._at_vial = None

    def park(self):
        """시퀀스 종료/정리: 니들 업 + 홈 복귀 (원본 HomeSampler 대응 축약)."""
        try:
            self.sampler.lift_needle()
        except Exception:
            pass
        try:
            ok, msg = self.sampler.return_home()
            self._log(f"파킹: {msg}")
        except Exception as e:
            self._log(f"파킹 경고: {e}")
        self._at_vial = None
