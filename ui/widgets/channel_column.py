"""Manual v4 — 채널 칼럼 카드 (Grafana풍 계기 대시보드 레퍼런스).

@codesyncer-decision: 채널을 '가로 행'이 아니라 '세로 칼럼'으로 —
  GROUP A/B/C 가 나란한 기둥이 되어 채널 간 상태 비교가 한눈에 됨.
  세척/수동 작업(WashOps)은 ⚙ 팝업으로 이동해 칼럼을 홀쭉하게 유지.

버튼 언어(v4, 레퍼런스대로): 세그먼트는 트랙(QFrame#SegTrack)+선택 pill,
  대형 액션(INFUSE/WITHDRAW/STOP)은 상태반응형 솔리드(활성 동작만 점등),
  상태는 배지 칩 — 전부 ui.theme 공통 헬퍼 경유(인라인 hex 금지, 팔레트 토큰만).

밸브 방향 매핑(실코드 확인 — simpy_engine.ValvePosition / strict_engine _set_switcher):
  position 1 = SOURCE / Refill (소스 흡입 방향; 폐기는 이 방향에서 12-way 포트로)
  position 2 = REACTOR / Infuse (반응기 주입 방향)
  → 3-way 스위처는 딱 2분기(SOURCE/REACTOR). WASTE 는 3-way 위치가 아니라
    12-way 포트라, Valve Path 에 WASTE 세그먼트를 두지 않는다.

라우팅 3종 적응 (분기 키 = routing 하나):
  external_valve : Valve Path 세그먼트(SOURCE|REACTOR) + 12-way 포트 스핀 + 이동
  autosampler    : Valve Path 세그먼트(내장 2-way) + Vial 콤보 + 니들 이동
  internal_valve : Valve Path 세그먼트 + 고정 소스 라벨

안전 계약: STOP 은 액션 행에 상시 노출, 블로킹 동작(밸브/니들 이동·리필)은
  데몬 스레드 + 버튼 비활성 패턴. 구조/기능은 기존 UI 그대로 두고 버튼 '디자인'만 교체.
"""

import threading

from PyQt5.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QToolButton, QComboBox, QSpinBox,
                             QDoubleSpinBox, QButtonGroup, QDialog,
                             QSizePolicy, QProgressBar)
from PyQt5.QtCore import Qt, QMetaObject, pyqtSlot

from ui.colors import DarkPalette as Dark, LightPalette as Light, T
from ui.theme import (get_segment_track_style, get_segment_button_style,
                      get_action_state_style, get_badge_style,
                      get_primary_button_style, get_stop_button_style,
                      get_secondary_button_style, get_caption_style)

# 모듈러 그리드 규격 — Feed 존 모든 카드(채널/NRG수동/샘플러)가 공유하는 균일폭.
# 상단선(Y)·너비를 맞춰 시선이 계단식으로 덜컥이지 않게 (사용자 지적).
CARD_W = 260


def _cap(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"font-size: {T.FS_XS}; font-weight: {T.FW_SEMI};")
    return lbl


class ChannelColumnCard(QFrame):
    """한 펌프 그룹의 전체 수동 제어를 담는 세로 칼럼 카드."""

    SERVICE_VIALS = {"waste", "rinse", "gas", "wash", "home", "cleaning"}

    def __init__(self, group_name, pump_obj, selector_obj=None, switcher_obj=None,
                 sampler_obj=None, routing="external_valve", inlet_provider=None,
                 wash_ops_factory=None, parent=None):
        super().__init__(parent)
        self.group_name = group_name
        self.pump = pump_obj
        self.selector = selector_obj
        self.switcher = switcher_obj
        self.sampler = sampler_obj
        self.routing = routing
        self._inlet = inlet_provider
        self._wash_factory = wash_ops_factory
        self._wash_dialog = None
        self._busy = False

        # @codesyncer-decision: 4칸(시린지펌프 최대 4)에 맞춰 폭을 채우도록 확장.
        #   고정 260 → min 260, 상한 420(버튼 과확대 방지)으로 그리드 열을 채운다.
        #   높이는 '내용만큼'(Maximum), 스크롤 영역에서 상단 정렬.
        self.setMinimumWidth(CARD_W)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SP_MD, T.SP_MD, T.SP_MD, T.SP_MD)
        root.setSpacing(T.SP_SM)

        # ── 헤더: 상태 도트 + 그룹명 + 상태배지 + ⚙ ────────────
        # @codesyncer(감사 2026-07-13): 상태 배지를 푸터→헤더로 승격 — 계측 SW 관례
        #   (여러 채널 스캔 시 카드 상단만 훑어도 RUNNING/IDLE 판독). 도트=활동
        #   인디케이터(색), 배지=동작 상태 텍스트로 역할 분담.
        head = QHBoxLayout()
        head.setSpacing(6)
        self.lbl_dot = QLabel("●")
        self.lbl_name = QLabel(group_name.replace("_", " ").upper())
        self.lbl_name.setStyleSheet(
            f"font-weight: {T.FW_BOLD}; font-size: {T.FS_SM}; letter-spacing: 0.5px;")
        head.addWidget(self.lbl_dot)
        head.addWidget(self.lbl_name)
        head.addStretch()
        self.lbl_state = QLabel("IDLE")
        head.addWidget(self.lbl_state)
        self.btn_gear = QToolButton()
        self.btn_gear.setText("⚙")
        self.btn_gear.setToolTip("Wash / Service Ops (Refill · Wash · Prime)")
        self.btn_gear.setCursor(Qt.PointingHandCursor)
        self.btn_gear.setFixedSize(28, 28)   # 최소 힛타겟(터치) 규격
        self.btn_gear.clicked.connect(self._open_wash_dialog)
        if wash_ops_factory is None:
            self.btn_gear.setVisible(False)
        head.addWidget(self.btn_gear)
        root.addLayout(head)

        # ── Valve Path 세그먼트 (SOURCE | REACTOR) — 3-way 2분기 ──
        # position 1=SOURCE/Refill · 2=REACTOR/Infuse (매핑 실코드 확인, 상단 주석)
        self.btn_path_src = None
        self.btn_path_rct = None
        if switcher_obj is not None:
            root.addWidget(_cap("Valve Path"))
            self.path_track = QFrame()
            self.path_track.setObjectName("SegTrack")
            pt = QHBoxLayout(self.path_track)
            pt.setContentsMargins(3, 3, 3, 3)
            pt.setSpacing(2)
            self.btn_path_src = QPushButton("SOURCE")
            self.btn_path_rct = QPushButton("REACTOR")
            grp = QButtonGroup(self)
            grp.setExclusive(True)
            for b in (self.btn_path_src, self.btn_path_rct):
                b.setCheckable(True)
                b.setMinimumHeight(30)
                b.setCursor(Qt.PointingHandCursor)
                grp.addButton(b)
                pt.addWidget(b, 1)
            cur = getattr(switcher_obj, "position", 2)
            (self.btn_path_src if str(cur) in ("1", "SOURCE", "REFILL")
             else self.btn_path_rct).setChecked(True)
            self.btn_path_src.clicked.connect(lambda: self._switch_path(1))
            self.btn_path_rct.clicked.connect(lambda: self._switch_path(2))
            root.addWidget(self.path_track)

        # ── 소스 선택 (라우팅별) — 입력 전폭 + 아래 와이드 버튼(포인팅 정확도) ──
        self.sp_port = None
        self.cb_vial = None
        self.btn_port_go = None
        if routing == "external_valve" and selector_obj is not None:
            root.addWidget(_cap("Valve Position (12-Way)"))
            self.sp_port = QSpinBox()
            self.sp_port.setRange(1, 12)
            self.sp_port.setPrefix("Port ")
            self.sp_port.setMinimumHeight(30)
            cur = getattr(selector_obj, "position", 1)
            if isinstance(cur, int):
                self.sp_port.setValue(cur)
            # @codesyncer(감사 2026-07-13): 포트의 '내용물'을 인라인 표시 — 밸브
            #   선택기엔 현재 연결 소스 표기가 계측 관례(기존엔 툴팁에만 숨음).
            #   StepCard 포트콤보("Port 2 : 시약명")와 정보 등가.
            self.lbl_port_reagent = QLabel("")
            self.lbl_port_reagent.setStyleSheet(
                f"color: {Dark.TEXT_SECONDARY}; font-size: {T.FS_XS}; "
                f"background: transparent; border: none;")
            self.sp_port.valueChanged.connect(self._update_port_tooltip)
            self._update_port_tooltip(self.sp_port.value())
            # @codesyncer(2026-08-13, 사용자 요청): 포트 번호 입력 후 Enter = 즉시
            #   이동 — 숫자 치고 MOVE 마우스 클릭까지 가는 왕복 제거 (키보드 운전).
            self.sp_port.lineEdit().returnPressed.connect(self._enter_move_port)
            self.sp_port.setToolTip("포트 번호 입력 후 Enter = 즉시 이동")
            self.btn_port_go = QPushButton("MOVE")
            self.btn_port_go.setMinimumHeight(T.H_BTN)
            self.btn_port_go.setCursor(Qt.PointingHandCursor)
            self.btn_port_go.clicked.connect(self._move_port)
            root.addWidget(self.sp_port)
            root.addWidget(self.lbl_port_reagent)
            root.addWidget(self.btn_port_go)
        elif routing == "autosampler" and sampler_obj is not None:
            root.addWidget(_cap("Vial (Needle Source)"))
            self.cb_vial = QComboBox()
            self.cb_vial.setMinimumHeight(30)
            positions = getattr(sampler_obj, "vial_positions", {}) or {}
            for v in sorted(positions.keys()):
                if str(v).lower() in self.SERVICE_VIALS:
                    continue
                info = None
                if inlet_provider is not None:
                    try:
                        info = inlet_provider(v)
                    except Exception:
                        info = None
                name = (info or {}).get("name", "")
                known = name and name not in ("Empty", "비어있음", "-", "알 수 없음")
                self.cb_vial.addItem(f"{v} : {name}" if known else str(v), v)
            self.btn_vial_go = QPushButton("MOVE")
            self.btn_vial_go.setMinimumHeight(T.H_BTN)
            self.btn_vial_go.setCursor(Qt.PointingHandCursor)
            self.btn_vial_go.clicked.connect(self._move_needle)
            root.addWidget(self.cb_vial)
            root.addWidget(self.btn_vial_go)
        else:
            root.addWidget(_cap("Source"))
            lbl = QLabel("Fixed (Internal Valve)")
            lbl.setStyleSheet(f"font-size: {T.FS_SM};")
            root.addWidget(lbl)

        # ── 유속 필드 (Infuse / Withdraw 나란히) — 구성 바로 아래 밀착(짧은 카드)
        rates = QHBoxLayout()
        rates.setSpacing(8)
        for key, cap_text, default in (("infuse", "Infuse (mL/min)", 1.0),
                                       ("withdraw", "Withdraw (mL/min)", 5.0)):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_cap(cap_text))
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 100.0)
            sp.setDecimals(3)
            sp.setValue(default)
            sp.setMinimumHeight(34)
            sp.setAlignment(Qt.AlignCenter)
            col.addWidget(sp)
            rates.addLayout(col)
            setattr(self, f"sp_{key}", sp)
        root.addLayout(rates)

        # ── 타겟 볼륨 (0 = 연속 구동, 기존 동작) ─────────────────
        # @codesyncer(2026-08-13, 사용자 요청): 정량 수동 구동 — Chemyx 는 볼륨
        #   모드가 네이티브(set_volume 후 도달 시 자동 정지)라 타겟만 넘기면 됨.
        #   0 이면 기존처럼 용량 전체(=STOP 까지 연속). INFUSE/WITHDRAW 공용.
        root.addWidget(_cap("Target Vol (mL) — 0 = 연속"))
        self.sp_target = QDoubleSpinBox()
        self.sp_target.setRange(0.0, 999.0)
        self.sp_target.setDecimals(3)
        self.sp_target.setSingleStep(0.01)
        self.sp_target.setValue(0.0)
        self.sp_target.setMinimumHeight(34)
        self.sp_target.setAlignment(Qt.AlignCenter)
        self.sp_target.setToolTip("이 부피만 구동 후 자동 정지 (0 = 연속, STOP 으로 정지)")
        root.addWidget(self.sp_target)

        # ── 동작: [INFUSE | WITHDRAW] + STOP 전폭 분리 행 ──
        # @codesyncer-decision: STOP 을 동일 크기 회색 3연버튼에 끼우면
        #   정지하려다 INFUSE 를 누르는 오조작 위험(사용자 지적) →
        #   공간 분리(별도 행·전폭) + 적색 아웃라인 식별.
        act = QHBoxLayout()
        act.setSpacing(6)
        self.btn_infuse = QPushButton("INFUSE")
        self.btn_withdraw = QPushButton("WITHDRAW")
        for b in (self.btn_infuse, self.btn_withdraw):
            b.setMinimumHeight(T.H_BTN)
            b.setCursor(Qt.PointingHandCursor)
        self.btn_infuse.clicked.connect(self._do_infuse)
        self.btn_withdraw.clicked.connect(self._do_withdraw)
        act.addWidget(self.btn_infuse, 1)
        act.addWidget(self.btn_withdraw, 1)
        root.addLayout(act)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setMinimumHeight(T.H_BTN)
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.clicked.connect(self._do_stop)
        root.addWidget(self.btn_stop)

        # ── 푸터: 구분선 + Vol 수치 + 잔량 게이지 ─────────────
        # @codesyncer(감사 2026-07-13): 시린지 잔량 = 이 카드의 1급 연속량 —
        #   시린지펌프 전면패널 관례대로 용량 대비 게이지 바 병기(리필 타이밍 즉독).
        self.sep_foot = QFrame()
        self.sep_foot.setFrameShape(QFrame.HLine)
        self.sep_foot.setFixedHeight(1)
        root.addWidget(self.sep_foot)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.lbl_vol = QLabel("Vol: --")
        foot.addWidget(self.lbl_vol)
        self.bar_vol = QProgressBar()
        self.bar_vol.setRange(0, 1000)
        self.bar_vol.setValue(0)
        self.bar_vol.setTextVisible(False)
        self.bar_vol.setFixedHeight(6)
        foot.addWidget(self.bar_vol, 1)
        root.addLayout(foot)

        self.apply_theme(True)

    # ── 팔레트 ────────────────────────────────────────────────
    def apply_theme(self, is_dark=True):
        P = Dark if is_dark else Light
        self._P = P
        self.setStyleSheet(
            f"ChannelColumnCard {{ background-color: {P.BG_SECONDARY}; "
            f"border: 1px solid {P.BORDER_PRIMARY}; border-radius: {T.R_LG}; }}"
            f"QLabel {{ background-color: transparent; border: none; color: {P.TEXT_PRIMARY}; }}"
            + get_segment_track_style(P))
        self.btn_gear.setStyleSheet(
            f"QToolButton {{ background-color: transparent; border: none; "
            f"color: {P.TEXT_SECONDARY}; font-size: {T.FS_LG}; }}"
            f"QToolButton:hover {{ color: {P.TEXT_PRIMARY}; }}")

        # Valve Path 세그먼트 (중립 pill)
        if self.btn_path_src is not None:
            self.btn_path_src.setStyleSheet(get_segment_button_style(P, "source"))
            self.btn_path_rct.setStyleSheet(get_segment_button_style(P, "reactor"))

        if getattr(self, "sep_foot", None) is not None:
            self.sep_foot.setStyleSheet(
                f"background-color: {P.SEPARATOR}; border: none;")

        # @codesyncer(감사 2026-07-13): MOVE = primary → secondary 강등.
        #   카드당 파랑 대면적이 3~4개면 위계 붕괴(존당 primary 1개 관례) —
        #   진짜 공정 시작(PROCESS SET/START)의 상대 무게 회복. 기능 불변.
        if getattr(self, "btn_port_go", None) is not None:
            self.btn_port_go.setStyleSheet(get_secondary_button_style(P))
        if getattr(self, "btn_vial_go", None) is not None:
            self.btn_vial_go.setStyleSheet(get_secondary_button_style(P))
        if getattr(self, "lbl_port_reagent", None) is not None:
            self.lbl_port_reagent.setStyleSheet(
                f"color: {P.TEXT_SECONDARY}; font-size: {T.FS_XS}; "
                f"background: transparent; border: none;")
        # 잔량 게이지 (용량 대비) — 값/가시성은 refresh_status 가 갱신
        self.bar_vol.setStyleSheet(
            f"QProgressBar {{ background: {P.BG_TERTIARY}; border: none; "
            f"border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {P.STATE_RUN}; "
            f"border-radius: 3px; }}")

        # 액션: INFUSE/WITHDRAW = 중립 통일(상태는 배지/도트로만),
        # STOP = 전폭 분리 행 + 적색 아웃라인(오조작 방지 식별).
        neutral = get_action_state_style(P, "stop", False)
        self.btn_infuse.setStyleSheet(neutral)
        self.btn_withdraw.setStyleSheet(neutral)
        self.btn_stop.setStyleSheet(get_stop_button_style(P))

        self.lbl_vol.setStyleSheet(
            f"font-family: {T.FONT_MONO}; font-size: {T.FS_SM}; "
            f"font-weight: {T.FW_SEMI}; background: transparent; border: none;")
        # 배지 + 도트 상태 → refresh_status
        self.refresh_status()

    # ── 밸브/니들/포트 ───────────────────────────────────────
    def _switch_path(self, pos):
        """Valve Path 세그먼트 클릭 → 3-way 전환.

        @codesyncer(수정 2026-07-13): ①GUI 스레드 블로킹 시리얼 호출 → _threaded 로
        ②완료 시 _post_action_sync 가 실제 위치로 강조 재동기(성공=유지·실패=원복,
        기존 1.5s 타이머 대기 없이 즉시). 이동 중엔 _busy 가 타이머 resync 를 막아
        하이라이트 깜빡임 없음."""
        if self.switcher is None or not hasattr(self.switcher, "set_position"):
            return
        self._threaded([self.btn_path_src, self.btn_path_rct],
                       lambda: self.switcher.set_position(pos))

    def _update_port_tooltip(self, port):
        """포트 변경 → 툴팁 + 인라인 시약명 라벨 갱신."""
        if self._inlet is None or self.sp_port is None:
            return
        try:
            info = self._inlet(int(port)) or {}
            name = info.get("name", "") or ""
            if name in ("Empty", "비어있음", "-"):
                name = ""
            self.sp_port.setToolTip(f"Port {port}: {name}" if name else f"Port {port}")
            lbl = getattr(self, "lbl_port_reagent", None)
            if lbl is not None:
                fm = lbl.fontMetrics()
                shown = fm.elidedText(name if name else "(비어있음)",
                                      Qt.ElideRight, max(80, self.width() - 60))
                lbl.setText(shown)
        except Exception:
            pass

    def _move_port(self):
        if self.selector is None or self.sp_port is None:
            return
        port = self.sp_port.value()
        self._threaded(self.btn_port_go,
                       lambda: self.selector.set_position(port), busy_text="…")

    def _threaded(self, btns, fn, busy_text=None):
        """블로킹 하드웨어 호출을 데몬 스레드로 — 대상 버튼 비활성(선택 시 '…')."""
        if self._busy:
            return
        self._busy = True
        if not isinstance(btns, (list, tuple)):
            btns = [btns]
        originals = [(b, b.text()) for b in btns]
        for b in btns:
            b.setEnabled(False)
            if busy_text is not None:
                b.setText(busy_text)

        def job():
            try:
                fn()
            except Exception as e:
                print(f"[{self.group_name}] 동작 오류: {e}")
            finally:
                for b, txt in originals:
                    if busy_text is not None:
                        b.setText(txt)
                    b.setEnabled(True)
                self._busy = False
                # 동작 직후 상태/밸브강조 즉시 재동기 (1.5s 타이머 대기 제거) —
                # GUI 스레드로 마샬 (tab_manual QMetaObject 패턴)
                try:
                    QMetaObject.invokeMethod(self, "_post_action_sync",
                                             Qt.QueuedConnection)
                except Exception:
                    pass

        threading.Thread(target=job, daemon=True).start()

    @pyqtSlot()
    def _post_action_sync(self):
        """워커 완료 직후 GUI 스레드에서 상태 미러 — Valve Path 강조/포트/배지."""
        try:
            self.refresh_status()
        except Exception:
            pass

    def _move_needle(self):
        if self.sampler is None or self.cb_vial is None:
            return
        vial = self.cb_vial.currentData()
        self._threaded(self.btn_vial_go,
                       lambda: self.sampler.move_to_vial(vial), busy_text="…")

    def _enter_move_port(self):
        """포트 번호 입력 후 Enter = 즉시 이동 (MOVE 클릭 대체).
        interpretText 로 타이핑 중 텍스트를 값으로 확정한 뒤 이동."""
        try:
            self.sp_port.interpretText()
        except Exception:
            pass
        self._move_port()

    # ── 펌프 동작 (덕타이핑 — Chemyx driver / 스마트 API 공용) ──
    def start_infuse(self):
        """헤더 '모두 토출'에서도 호출되는 공개 진입점."""
        self._do_infuse()

    def _do_infuse(self):
        # @codesyncer(A): 시약을 반응기로 밀어넣는 1급 동작 — WITHDRAW/포트이동과 동일하게
        #   블로킹 시리얼 호출을 스레드화 + 버튼 비활성("…") + _busy 가드(밸브 이동 중 주입 금지).
        #   연타 시 start() 중복 호출 방지. 버튼 색은 설계상 상태무관(RUNNING 은 배지/도트).
        pump = self.pump
        rate = self.sp_infuse.value()
        tgt = float(self.sp_target.value())
        drv = getattr(pump, "driver", None)

        def _run():
            # @codesyncer(수정 2026-07-13, 사용자 "방향 따라 강조 안 바뀜"): INFUSE 는
            #   반응기 방향 1급 동작 — WITHDRAW(smart refill)가 3-way 를 SOURCE 로
            #   돌리는 것과 대칭으로, 토출 전 REACTOR(2) 정렬. 기존엔 모터만 구동 →
            #   직전 리필 후 밸브가 SOURCE 에 남아 시약이 12-way/소스로 역류하는
            #   물리 오동작 + Valve Path 강조가 방향을 안 따라가는 증상.
            # @codesyncer-decision(2026-07-31, 사용자 지시 확정): 매뉴얼 탭 = 순수
            #   장비 독립 제어 — 펌프 버튼은 밸브에 '일절' 개입하지 않는다
            #   (7/13 자동 REACTOR 정렬 폐지. "펌프를 밀면 밸브가 자동으로 바뀌는"
            #   동작 자체가 매뉴얼 원칙 위반이라는 피드백).
            #   방향 정렬은 운전자가 Valve Path 세그먼트로 직접 수행.
            #   시퀀스 엔진(strict_engine)의 엄격 밸브-펌프 결합은 불변.
            if drv is not None and hasattr(drv, "set_rate") and hasattr(pump, "diameter"):
                # Chemyx 계열: 직경+유속+양수 볼륨 전송 (기존 ChemyxMotorWidget 규약)
                # 타겟>0 이면 그 부피만 — 펌프 볼륨 모드가 도달 시 자동 정지.
                drv.set_diameter(pump.diameter)
                drv.set_rate(rate)
                drv.set_volume(tgt if tgt > 0 else abs(getattr(pump, "capacity", 10.0)))
                drv.start()
            elif hasattr(pump, "set_flow") and hasattr(pump, "start"):
                pump.set_flow(rate)
                pump.start()

        self._threaded(self.btn_infuse, _run, busy_text="…")

    def _do_withdraw(self):
        pump = self.pump
        rate = self.sp_withdraw.value()
        tgt = float(self.sp_target.value())
        drv = getattr(pump, "driver", None)

        def _run():
            # 오토샘플러: 흡입 전 니들을 선택 vial 로 (일체형 규약)
            if self.routing == "autosampler" and self.sampler is not None \
                    and self.cb_vial is not None:
                ok, msg = self.sampler.move_to_vial(self.cb_vial.currentData())
                if not ok:
                    raise RuntimeError(f"니들 이동 실패: {msg}")
                try:
                    pump.refill(1, volume=(tgt if tgt > 0 else None))
                finally:
                    self.sampler.lift_needle()
                return
            def _raw_withdraw():
                """모터 단독 흡인 (음수 볼륨) — 밸브 미개입.
                타겟>0 이면 그 부피만 흡인 후 자동 정지 (펌프 볼륨 모드)."""
                drv.set_diameter(pump.diameter)
                drv.set_rate(rate)
                drv.set_volume(-(tgt if tgt > 0 else abs(getattr(pump, "capacity", 10.0))))
                drv.start()

            _raw_ok = (drv is not None and hasattr(drv, "set_rate")
                       and hasattr(pump, "diameter"))
            # @codesyncer-decision(2026-07-31, 사용자 지시 확정): 매뉴얼 = 순수 장비
            #   독립 제어 — WITHDRAW 도 밸브(12-way/3-way)에 일절 개입하지 않는다.
            #   외장밸브 라우팅(Chemyx 계열)은 모터 단독 음수볼륨 흡인. 소스 포트
            #   선택·3-way 방향은 운전자가 카드의 MOVE/Valve Path 로 직접.
            #   내장밸브 일체형(NRG)은 밸브가 펌프 자체 요소이므로 refill 유지.
            #   부피 추적(current_vol)은 스마트 경로 밖 — 시퀀스 시작 시 0 리셋으로 복원.
            if _raw_ok:
                _raw_withdraw()
            elif hasattr(pump, "refill"):
                port = self.sp_port.value() if self.sp_port is not None else 1
                pump.refill(port, volume=(tgt if tgt > 0 else None))

        self._threaded(self.btn_withdraw, _run, busy_text="…")

    def _do_stop(self):
        try:
            self.pump.stop()
        except Exception as e:
            print(f"[{self.group_name}] STOP 오류: {e}")
        self.refresh_status()

    # ── ⚙ 세척/수동 작업 팝업 ────────────────────────────────
    def _open_wash_dialog(self):
        if self._wash_factory is None:
            return
        if self._wash_dialog is None:
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Wash / Service — {self.group_name}")
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(T.SP_SM, T.SP_SM, T.SP_SM, T.SP_SM)
            w = self._wash_factory()
            lay.addWidget(w)
            self._wash_dialog = dlg
        self._wash_dialog.show()
        self._wash_dialog.raise_()

    # ── 상태 미러 (tab_manual 1.5s 타이머에서 호출) ──────────
    def refresh_status(self):
        P = getattr(self, "_P", Dark)
        pump = self.pump
        running = bool(getattr(pump, "running", False))
        refilling = bool(getattr(pump, "is_refilling", False))
        vol = getattr(pump, "current_vol", None)
        self.lbl_vol.setText(f"Vol: {float(vol):.1f} mL" if vol is not None else "Vol: --")
        cap = float(getattr(pump, "capacity", 0) or 0)
        if cap > 0 and vol is not None:
            self.bar_vol.setVisible(True)
            self.bar_vol.setValue(max(0, min(1000, int(float(vol) / cap * 1000))))
            self.bar_vol.setToolTip(f"{float(vol):.1f} / {cap:.0f} mL")
        else:
            self.bar_vol.setVisible(False)

        # 액션 버튼 색은 상태 무관(전부 중립, apply_theme 고정) — 상태는 배지/도트로만.
        # 상태 배지 칩
        if refilling:
            state, level = "WITHDRAW", "info"
        elif running:
            state, level = "RUNNING", "run"
        else:
            state, level = "IDLE", "idle"
        self.lbl_state.setText(state)
        self.lbl_state.setStyleSheet(get_badge_style(P, level))
        self.lbl_dot.setStyleSheet(
            f"color: {P.STATE_RUN if (running or refilling) else P.TEXT_DISABLED}; "
            f"font-size: {T.FS_XS}; background: transparent; border: none;")

        # @codesyncer(B): 밸브 경로 pill·12-way 포트를 라이브 하드웨어 위치로 재동기화 →
        #   엔진/외부가 밸브를 움직여도 Manual 탭이 진실을 표시(기존엔 로컬 실패 시에만 resync).
        #   신호 차단으로 재-이동 트리거 방지. 이동 중(_busy)엔 스킵. 포트는 입력칸을 겸하므로
        #   사용자가 편집(포커스) 중이면 타이핑을 덮어쓰지 않도록 스킵.
        if not self._busy:
            if self.btn_path_src is not None and self.switcher is not None:
                cur = getattr(self.switcher, "position", None)
                if cur is not None:
                    tgt = (self.btn_path_src if str(cur) in ("1", "SOURCE", "REFILL")
                           else self.btn_path_rct)
                    if not tgt.isChecked():
                        tgt.blockSignals(True)
                        tgt.setChecked(True)
                        tgt.blockSignals(False)
            if self.sp_port is not None and self.selector is not None \
                    and not self.sp_port.hasFocus():
                cur = getattr(self.selector, "position", None)
                if isinstance(cur, int) and cur != self.sp_port.value():
                    self.sp_port.blockSignals(True)
                    self.sp_port.setValue(cur)
                    self.sp_port.blockSignals(False)
                    self._update_port_tooltip(cur)   # 인라인 시약명도 동기
