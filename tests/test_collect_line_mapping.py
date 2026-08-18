# -*- coding: utf-8 -*-
"""타임 기반 분획 스케줄의 '수집라인(Outlet→니들) 통과 지연' 매핑 검증.

배경 (2026-07-28 감사): strict_engine 의 타이머 이벤트는
  - 밸브(Outlet)  : 헤드가 '밸브' 도달 시각(t_head) 기준  ← 밸브 기준으론 옳음
  - 니들(웰 이동) : 같은 t_head 기준                       ← 니들 유출은 수집라인
                    부피(vol_collection)만큼 늦게 시작하므로 어긋남
이 파일은 부피 도메인 FIFO 모델(라인=선입선출 기둥)로 웰별 실제 내용물을
계산해 기존(legacy) 설계의 결함을 '정량'으로 고정(pin)하고, 보정(compensated)
설계의 목표 매핑을 수락 기준으로 명세한다.

모델 (strict_engine 타이머 구성과 동일한 창):
  syringe-push 분기: 밸브 COLLECT [0, target] → flush [target, target+v_line]
                     니들 well_i = [i×vpt, (i+1)×vpt), wash모브 @ target
  HPLC push 분기   : 밸브 COLLECT [0, target+0.1R] (flush 없음)
                     니들 well_i 동일, 마지막 웰이 0.1R 여유 흡수
  니들 유출(부피 v 시점) = FIFO: 처음 v_line 은 '라인 초기 내용물',
                     이후는 밸브 유입 스트림이 v_line 지연되어 나옴.

실행:  py -3.14 test_collect_line_mapping.py
"""
import sys


def line_outflow_segments(initial_label, inflow_segments, v_line):
    """밸브 유입 → 니들 유출 세그먼트 (부피 도메인 FIFO).

    @return [(vol, label), ...] — 유출 총량 = 유입 총량 (라인은 항상 가득).
    유출 순서 = [초기 내용물 v_line] + 유입 스트림, 단 마지막 v_line 은
    라인에 잔류(다음 스텝의 '초기 내용물').
    """
    column = [(v_line, initial_label)] + [(float(v), l) for v, l in inflow_segments]
    total_in = sum(v for v, _ in inflow_segments)
    out, need = [], total_in
    for vol, label in column:
        if need <= 1e-12:
            break
        take = min(vol, need)
        out.append((take, label))
        need -= take
    # 잔류(라인에 남는 마지막 v_line) 계산
    leftover, remain = [], v_line
    for vol, label in reversed(column):
        if remain <= 1e-12:
            break
        take = min(vol, remain)
        leftover.append((take, label))
        remain -= take
    leftover.reverse()
    return out, leftover


def map_to_targets(outflow, windows):
    """유출 세그먼트를 니들 위치 창(부피 도메인)에 배분.

    windows = [(name, v_start, v_end), ...] — strict_engine 의 웰 이동 시각을
    부피로 환산한 것. 창 밖 유출은 '(no-needle)'.
    @return {name: {label: vol}}
    """
    res = {}
    pos = 0.0
    for vol, label in outflow:
        seg_start, seg_end = pos, pos + vol
        for name, w0, w1 in windows:
            lo, hi = max(seg_start, w0), min(seg_end, w1)
            if hi > lo:
                res.setdefault(name, {}).setdefault(label, 0.0)
                res[name][label] += hi - lo
        pos = seg_end
    return res


# ── 공통 파라미터 (대표값: F=1mL/min, 수집라인 1.0mL = config 기본) ──
V_LINE = 1.0      # collection_line_vol_ml
TARGET = 2.0      # 수집 목표
VPT = 0.5         # vol_per_tube → 웰 4개
R01 = 0.3         # HPLC 여유 0.1×reactor(3.0mL)


def _legacy_syringe_windows():
    """legacy: 웰 창=[i×vpt,...] (t_head 기준), wash모브 @ target."""
    wins = [(f"well{i+1}", i * VPT, (i + 1) * VPT) for i in range(4)]
    wins.append(("wash_tube", TARGET, TARGET + V_LINE))
    return wins


def _compensated_syringe_windows():
    """compensated: 니들 창 전체 +v_line 시프트, 시프트 전 구간은 WASH 파킹.
    flush 배출(라인 재충전 후 잉여)은 WASH 포트로."""
    wins = [("WASH_park", 0.0, V_LINE)]
    wins += [(f"well{i+1}", V_LINE + i * VPT, V_LINE + (i + 1) * VPT) for i in range(4)]
    wins.append(("WASH_flush", V_LINE + TARGET, V_LINE + TARGET + V_LINE))
    return wins


# ═════════════════════════════════════════════════════════════
# 1) legacy · syringe-push 분기 (flush 있음, wash tube=플레이트 웰)
# ═════════════════════════════════════════════════════════════
def test_legacy_syringe_first_wells_get_solvent():
    """[결함 고정] 첫 v_line 분량(웰 1~2)은 제품이 아니라 이전 flush 용매."""
    out, _ = line_outflow_segments(
        "prev_solvent", [(TARGET, "product"), (V_LINE, "flush_solvent")], V_LINE)
    res = map_to_targets(out, _legacy_syringe_windows())
    assert res["well1"] == {"prev_solvent": 0.5}, res.get("well1")
    assert res["well2"] == {"prev_solvent": 0.5}, res.get("well2")


def test_legacy_syringe_product_tail_lost_to_wash_tube():
    """[결함 고정] 제품 꼬리 v_line(=1.0mL, 50%)이 wash tube 로 유출 —
    라벨된 수집 웰의 제품 회수율 50%."""
    out, leftover = line_outflow_segments(
        "prev_solvent", [(TARGET, "product"), (V_LINE, "flush_solvent")], V_LINE)
    res = map_to_targets(out, _legacy_syringe_windows())
    in_wells = sum(res.get(f"well{i+1}", {}).get("product", 0.0) for i in range(4))
    in_wash = res.get("wash_tube", {}).get("product", 0.0)
    assert abs(in_wells - 1.0) < 1e-9, f"웰 내 제품 {in_wells}mL (기대 1.0 = 50%)"
    assert abs(in_wash - 1.0) < 1e-9, f"wash tube 제품 {in_wash}mL (기대 1.0)"
    # flush 용매는 라인에 잔류(다음 스텝 초기 내용물) — 세척 자체는 유효
    assert leftover == [(1.0, "flush_solvent")], leftover


def test_legacy_syringe_consumes_plate_well():
    """[결함 고정] wash 배출이 전용 좌표가 아닌 '다음 순번 웰' — 스텝당 웰 1개
    소모 (strict_engine: current_tube += num_collect_tubes + 1)."""
    wins = _legacy_syringe_windows()
    assert wins[-1][0] == "wash_tube" and wins[-1][2] - wins[-1][1] == V_LINE
    # 1.0mL 가 0.3mL급 웰 하나에 — 오버플로 리스크도 내재
    assert V_LINE > VPT


# ═════════════════════════════════════════════════════════════
# 2) legacy · HPLC push 분기 (현 활성 구성 — flush 자체가 없음)
# ═════════════════════════════════════════════════════════════
def test_legacy_hplc_no_flush_stale_product_stranded():
    """[결함 고정·현 활성 경로] flush_sec=0 → 라인 세척이 아예 없음:
    ① 첫 웰들이 이전 스텝 잔류물(stale)을 받음
    ② 런 종료 시 제품 꼬리가 라인에 좌초 → 다음 스텝의 stale 이 됨 (순환 오염)"""
    inflow = [(TARGET + R01, "product")]           # 0.1R 여유 포함, flush 없음
    out, leftover = line_outflow_segments("stale_prev_product", inflow, V_LINE)
    wins = [(f"well{i+1}", i * VPT, (i + 1) * VPT) for i in range(4)]
    wins.append(("well4_extra", 4 * VPT, TARGET + R01))   # 마지막 웰이 여유 흡수
    res = map_to_targets(out, wins)
    # ① 첫 두 웰 = 이전 런 잔류물
    assert res["well1"] == {"stale_prev_product": 0.5}, res.get("well1")
    assert res["well2"] == {"stale_prev_product": 0.5}, res.get("well2")
    # ② 라인 잔류 = 제품 (세척 용매가 아님!) → 다음 런의 stale
    assert leftover == [(1.0, "product")], leftover
    # 라벨 웰 회수율: (target+0.1R−v_line)/target = 65%
    got = sum(v.get("product", 0.0) for k, v in res.items())
    assert abs(got - (TARGET + R01 - V_LINE)) < 1e-9, got


# ═════════════════════════════════════════════════════════════
# 3) compensated 설계 목표 (수락 기준 — 구현 후 엔진이 이 매핑을 달성해야)
# ═════════════════════════════════════════════════════════════
def test_compensated_syringe_full_recovery():
    """[수락 기준] 니들 이벤트 +v_line 시프트 + WASH 파킹:
    제품 100% 가 라벨 웰에, 초기 라인 내용물은 WASH 로, 웰 소모 0."""
    out, leftover = line_outflow_segments(
        "prev_solvent", [(TARGET, "product"), (V_LINE, "flush_solvent")], V_LINE)
    res = map_to_targets(out, _compensated_syringe_windows())
    for i in range(4):
        assert res[f"well{i+1}"] == {"product": 0.5}, (i + 1, res.get(f"well{i+1}"))
    assert res["WASH_park"] == {"prev_solvent": 1.0}, res.get("WASH_park")
    assert leftover == [(1.0, "flush_solvent")], leftover   # 라인은 용매로 재충전
    assert "wash_tube" not in res                            # 플레이트 웰 소모 없음


def test_compensated_hplc_with_flush_full_recovery():
    """[수락 기준] HPLC 분기에 flush(v_line) 추가 + 시프트 + WASH 파킹:
    제품 전량 회수(여유 0.1R 포함) + 라인은 push 용매로 재충전."""
    inflow = [(TARGET + R01, "product"), (V_LINE, "push_solvent")]
    out, leftover = line_outflow_segments("stale_prev", inflow, V_LINE)
    wins = [("WASH_park", 0.0, V_LINE)]
    wins += [(f"well{i+1}", V_LINE + i * VPT, V_LINE + (i + 1) * VPT) for i in range(4)]
    wins.append(("well4_extra", V_LINE + 4 * VPT, V_LINE + TARGET + R01))
    wins.append(("WASH_flush", V_LINE + TARGET + R01, V_LINE + TARGET + R01 + V_LINE))
    res = map_to_targets(out, wins)
    got = sum(v.get("product", 0.0) for k, v in res.items()
              if k.startswith("well"))
    assert abs(got - (TARGET + R01)) < 1e-9, f"제품 회수 {got} (기대 {TARGET + R01})"
    assert res["WASH_park"] == {"stale_prev": 1.0}
    assert leftover == [(1.0, "push_solvent")], leftover


# ═════════════════════════════════════════════════════════════
# 4) 수집라인 선헹굼 (collect_preflush_vol_ml, 2026-08-15 사용자 확정)
# ═════════════════════════════════════════════════════════════
V_PF = 0.3        # collect_preflush_vol_ml (예: 0.1×reactor 3.0mL)


def test_preflush_cleans_line_with_fresh_solvent_before_product():
    """[수락 기준] Outlet 을 선단 도달보다 V_PF 앞당겨 전환하면:
    ① 라인의 stale 내용물 + 선헹굼 신선용매가 모두 WASH 로 배출(라인 세정)
    ② 제품은 여전히 100% 라벨 웰에 (니들 이벤트 시각 불변이므로)
    밸브 전환 시점 기준 부피 도메인: 유입 = [선헹굼][제품][push용매]"""
    inflow = [(V_PF, "preflush_solvent"), (TARGET + R01, "product"),
              (V_LINE, "push_solvent")]
    out, leftover = line_outflow_segments("stale_prev", inflow, V_LINE)
    # 니들 이벤트는 t_head 기준 그대로 → 밸브 전환 기준으론 +V_PF 만큼 뒤
    base = V_PF + V_LINE
    wins = [("WASH_park", 0.0, base)]
    wins += [(f"well{i+1}", base + i * VPT, base + (i + 1) * VPT) for i in range(4)]
    wins.append(("well4_extra", base + 4 * VPT, base + TARGET + R01))
    wins.append(("WASH_flush", base + TARGET + R01, base + TARGET + R01 + V_LINE))
    res = map_to_targets(out, wins)
    # ① WASH 가 stale 전량 + 선헹굼 용매 전량을 받음 = 제품 도착 전 라인 세정 완료
    park = res["WASH_park"]
    assert set(park) == {"stale_prev", "preflush_solvent"}, park
    assert abs(park["stale_prev"] - V_LINE) < 1e-9, park
    assert abs(park["preflush_solvent"] - V_PF) < 1e-9, park
    # ② 제품 회수율 100% (선헹굼이 회수를 깎지 않음)
    got = sum(v.get("product", 0.0) for k, v in res.items() if k.startswith("well"))
    assert abs(got - (TARGET + R01)) < 1e-9, f"제품 회수 {got} (기대 {TARGET + R01})"
    # 웰에 용매 혼입 없음 — 라벨은 product 단독, 양은 VPT
    for i in range(4):
        w = res[f"well{i+1}"]
        assert set(w) == {"product"} and abs(w["product"] - VPT) < 1e-9, (i + 1, w)
    assert leftover == [(1.0, "push_solvent")], leftover


def test_preflush_guarded_off_in_legacy_would_contaminate_first_well():
    """[가드 근거] legacy(니들이 첫 웰에 파킹)에서 선헹굼을 적용하면 그 용매가
    첫 웰로 들어간다 → 엔진은 compensated + WASH 포트에서만 적용한다."""
    inflow = [(V_PF, "preflush_solvent"), (TARGET + R01, "product")]
    out, _ = line_outflow_segments("stale_prev", inflow, V_LINE)
    # legacy: 니들 창이 밸브 전환 시각부터 (시프트/파킹 없음)
    wins = [(f"well{i+1}", i * VPT, (i + 1) * VPT) for i in range(4)]
    res = map_to_targets(out, wins)
    # 첫 웰들이 stale 을 받고, 선헹굼 용매도 웰로 유입 (제품 아님)
    assert res["well1"] == {"stale_prev": VPT}, res.get("well1")
    solvent_in_wells = sum(v.get("preflush_solvent", 0.0) for v in res.values())
    assert solvent_in_wells > 0, "legacy 에선 선헹굼 용매가 웰로 — 가드 필요"


def test_preflush_shifts_only_valve_event():
    """[불변식] 선헹굼은 밸브 전환 시각만 앞당긴다 — 니들 이동/수집 종료/
    push 시간은 t_head 기준 그대로 (수집 창 정의 불변)."""
    t_head, F = 435.5, 0.481
    pf_sec = (V_PF / F) * 60.0
    t_collect = max(0.0, t_head - pf_sec)
    assert t_collect < t_head and abs((t_head - t_collect) - pf_sec) < 1e-9
    # 클램프: 선헹굼이 t_head 보다 크면 0 으로 (음수 시각 금지)
    assert max(0.0, t_head - (t_head + 10.0) ) == 0.0


def test_delta_scales_inversely_with_flow():
    """[정량 법칙] 시프트 Δt = v_line/F — 유속이 낮을수록 시간 오차 증폭.
    (F=1mL/min→60s, 0.5→120s: '생각보다 심각'의 산술적 근거)"""
    for f, expect in ((1.0, 60.0), (0.5, 120.0), (2.0, 30.0)):
        assert abs(V_LINE / f * 60.0 - expect) < 1e-9


# ── 러너 ─────────────────────────────────────────────────────
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
