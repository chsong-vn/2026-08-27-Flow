# 실행 트레이스 (Perfetto 타임라인)

시퀀스를 실행하면 CSV 로그와 함께 `logs/TRACE_<시각>_Sequence.json`이 자동 생성된다.
이 파일을 **[ui.perfetto.dev](https://ui.perfetto.dev)** 에 드래그하면 (설치 불필요, 파일은
브라우저 로컬에서 처리됨) 장비별 트랙이 쌓인 실행 타임라인을 줌/팬으로 열람할 수 있다.

## 트랙 구성

| 트랙 | 내용 | 이벤트 형태 |
|------|------|------------|
| `PHASE` | 국면 전이 (가열 대기→프리필→도징→…) — `_emit_phase` 연동 | 스팬 |
| `DOSING` | 스텝별 도징 구간 (유량 맵·duration·refill 허용 여부 args) | 스팬 |
| `PUMP OPS` | 펌프 완료 대기 구간 (refill/prime 등, 대상 펌프 args) | 스팬 |
| `TIMER PLAN` | CollectionTimer **계획** — 예정 시각에 미리 찍힌 점 | 순간 |
| `TIMER <lane>` | 타이머 **실측** 발화 — 액션 소요가 폭, 지각(late_sec)이 args | X(구간) |
| `VALVE <이름>` | 밸브 전환 (소요 시간이 폭, 실패는 FAILED 점) | X(구간) |
| `MFC` | 질소 세그먼트 (sccm·duration args) | 스팬 |
| `LOG` | `_log()`를 지나는 모든 엔진 메시지 | 순간 |
| `Temp(C)` / `Pressure(bar)` | 모니터 주기(0.5s) 샘플 | 카운터 그래프 |

**타이밍 버그를 보는 법**: `TIMER PLAN`(계획)과 `TIMER <lane>`(실측)을 위아래로 놓고
어긋남을 보면 된다. pause/refill로 밀린 만큼 실측이 계획보다 늦는 것은 정상이고,
그 외의 편차·지각(late_sec > 0.5)·이상하게 긴 밸브 X 폭이 버그 후보다.

## 조작 팁

- `W`/`S` 줌, `A`/`D` 이동, 마우스 드래그로 구간 선택 → 하단에 스팬 목록/합계
- 쿼리 탭(Query (SQL))에서 예: 지각한 타이머 이벤트만 —
  ```sql
  select s.ts, s.dur, s.name from slice s join thread_track tt on s.track_id = tt.id
  join thread t using(utid) where t.name like 'TIMER %' order by s.ts
  ```

## 동작 규칙

- **켜기/끄기**: 기본 활성. 환경변수 `FLOWCHEM_TRACE=0` 으로 비활성 (config 무관).
- **안전성**: 트레이스는 보조 관측 — 기록 실패는 조용히 무시되며 엔진 실행에 영향 없음
  (`engine/trace_log.py`의 설계 결정 주석 참조).
- **크래시 내성**: 이벤트마다 flush — 앱이 죽어도 그 시점까지의 파일이 Perfetto에서 열림.
- **정리**: `logs/`의 다른 산출물과 동일하게 gitignore. 오래된 것은 지워도 됨.

## 계측을 추가하려면

엔진 안에서 `self.trace.instant/begin/end/complete/counter(...)` 를 호출하면 된다
(API는 `engine/trace_log.py` 도크스트링). 스팬 `end` 누락은 close 시 자동 마감된다.
`__init__`을 우회하는 테스트 스텁에서도 `trace`는 클래스 속성 기본값(NULL)이라 안전하다.

## 다음 단계 (미구현 — 계획)

- 계획-실측 자동 대사기: `sequence_timeline` 계획 트랙 추가 + 런 종료 시 편차 요약을 report.json에 기록
- 타이밍 불변식 모니터: SafetyManager에 "COLLECT 중 Outlet=COLLECT" 류 규칙 상시 감시
