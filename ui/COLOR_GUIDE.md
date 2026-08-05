# UI 색상·토큰 시스템 가이드

> 단일 진실 소스는 **`ui/colors.py`** 다. 이 문서는 요약일 뿐이며,
> 값이 다르면 언제나 `colors.py`가 맞다. (과거 버전의 이 문서는 존재하지
> 않는 팔레트 값을 실어 유지보수를 오도했음 — 2026-07-04 재작성)

---

## 파일 구조

| 파일 | 역할 | 수정 빈도 |
|------|------|----------|
| `colors.py` | 팔레트(`DarkPalette`/`LightPalette`) + 디자인 토큰(`T`) + 전역 hex 스냅 패치 | ⭐ 여기만 수정 |
| `theme.py` | 위젯별 QSS 생성 함수 (`build_theme`, `get_*_style`) | 거의 수정 안 함 |
| `styles.py` | 테마 조합·export | 수정 안 함 |
| `theme_manager.py` | 런타임 테마 전환, 위젯 레지스트리 | 거의 수정 안 함 |

## 핵심 사실

1. **다크 모드 주 악센트 = EXPEC풍 시안 블루 `#3b9eff`**
   (2026-07-04 사용자 결정 — 기존 오렌지 `#ff7a2f`에서 전환),
   라이트 모드 = 블루 `#3B82F6`. 토큰 이름은 역사적 이유로 `ACCENT_BLUE`지만
   의미는 "주 악센트"다. **오렌지는 열/온도 계열(`SENSOR_TEMP`,
   `CHART_TEMP`)과 브랜드 로고에만 잔류** — 열=난색 의미 매핑 유지.
2. **전역 스냅 패치**: `install_global_stylesheet_color_patch()`가
   `QWidget.setStyleSheet`을 몽키패치해 인라인 hex/rgba를 현재 팔레트의
   최근접 색으로 치환한다. 오프팔레트 hex가 "동작해 보이는" 이유가 이것이며,
   안전망이지 허가가 아니다 — **새 코드는 반드시 토큰만 사용**.
3. **ISA-101 / IEC 60073 의미색** (장비 상태 전용):
   - `STATE_RUN`(녹) = 정상 가동 · `STATE_WARN`(황) = 주의/과도 상태
   - `STATE_FAULT`(적) = 오류/비상 전용 — **일반 버튼에 적색 금지**
   - 칩 패턴: `STATE_*_BG` 틴트 배경 + `STATE_*` 텍스트 (WCAG 4.5:1)
4. **동작 의미색**: `ACT_INFUSE`(앰버) = 주입·비가역 방향,
   `ACT_FILL`(청록) = 충전·안전 방향, `ACT_STOP`(중립 회색).
   같은 개념은 어느 위젯에서든 같은 색.
5. **E-Stop / Pause**: `get_main_control_button_style(P, "emergency"/"pause")`
   — 적색 본체 + `SAFETY_RING` 황색 링(ISO 13850) / 황색(IEC 60073).
   비상 정지 버튼을 직접 스타일링하지 말 것.

## 디자인 토큰 `T` (colors.py)

| 분류 | 토큰 | 값 |
|------|------|-----|
| 서체 | `FONT` / `FONT_MONO` | 'Segoe UI' / 'Consolas' — **리터럴 서체명 금지** |
| 글자 | `FS_XS…FS_XXL` | 13 / 14 / 15 / 17 / 22 / 26px |
| 굵기 | `FW_SEMI` / `FW_BOLD` | 600 / 700 |
| 높이 | `H_INPUT_SM` / `H_INPUT` / `H_BTN` / `H_BTN_LG` / `H_BTN_XL` / `H_CHIP` | 28 / 36 / 40 / 44 / 52 / 22 |
| 폭 | `W_INPUT_SM` / `W_INPUT` / `W_LABEL_COND` | 112 / 128 / 72 |
| 라운드 | `R_SM…R_XL` | 4 / 6 / 8 / 12px |
| 간격 | `SP_XS…SP_XL` | 4 / 8 / 12 / 16 / 24 (4px 그리드) |

타겟 크기 위계 (WCAG 2.2 + 장갑 착용 환경): 밀집 입력 28 < 표준 입력 36 <
일반 버튼 40 < 주요 동작 44 < **임계 동작(START/E-Stop) 52**.

## 색상 변경 방법

`colors.py`의 해당 토큰 하나만 수정 → 앱 재시작. QSS 생성과 스냅 패치가
전 UI에 자동 반영한다. 특정 위젯만 안 바뀌면 그 위젯이 하드코딩된 것이니
`#[0-9a-fA-F]{6}` 로 검색해 토큰으로 치환하라.

## 사용 예

```python
from ui.colors import get_palette, T

P = get_palette()  # 현재 테마 팔레트 (다크/라이트 자동)
label.setStyleSheet(
    f"color: {P.TEXT_SECONDARY}; font-size: {T.FS_SM};")
```

테마 반응 위젯은 `apply_theme(is_dark)`를 구현하고 `theme_manager`
레지스트리에 등록한다. `__init__`에서 `Dark`를 직접 참조해 스타일을 굽는
패턴은 라이트 모드 첫 페인트가 다크로 나오는 버그를 만든다
(`IntegratedChannelGroup`, 과거 `dialog_plate96_manual` 사례).

## 알려진 미통합 표면 (2026-07-04 기준)

- `ui/widgets/pump_controls.py` — px/서체 리터럴 다수 (토큰 스윕 예정)
- `ui/dialogs.py` — Pretendard 서체 + 독자 밀도 (T 토큰화 예정)
- `ui/tab_collection.py`, `ui/widget_plate96.py` — **미장착 코드**
  (어디서도 인스턴스화되지 않음). 부활시키려면 토큰 기반으로 재작성할 것.
