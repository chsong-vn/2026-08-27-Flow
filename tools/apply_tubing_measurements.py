# -*- coding: utf-8 -*-
"""
튜빙 실측값 반영 도구 — tubing_measurements.json → hardware_config.json

@codesyncer-decision: 엔진 무수정 반영 경로. 데드볼륨 로직(config.process_config →
  strict_engine._compute_plug_timing)은 hardware_config.json 의
  roles.pumps[].settings.tube_vol_* / system_params.* 만 읽으므로, 실측 원장을
  그 키들로 매핑해 써 넣는 것으로 충분하다. 원장이 기록(이력·방법·프로파일)을,
  hardware_config 가 런타임 진실을 담당.
@codesyncer-decision: reactor/mixing line 은 config 가 길이×내경으로 부피를
  계산하므로 직접 부피 키가 없다 → 실측 부피를 '등가 길이'(부피/단면적)로
  환산해 reactor_len_m / mixing_line_len_cm 에 기록한다. 물리 길이의 원본
  기록은 원장에 남는다.

사용법:
    py -3.14 apply_tubing_measurements.py            # dry-run (미리보기만)
    py -3.14 apply_tubing_measurements.py --apply    # 실제 반영 (_backup/ 자동 백업)
    py -3.14 apply_tubing_measurements.py --profile <이름>   # 특정 프로파일 지정

값 결정 규칙 (구간별):
    1) measured_ml 이 있으면 그 값 (직접 실측 최우선)
    2) 없고 length_cm + id_mm 이 있으면 π×(id/20)²×len 으로 계산
    3) 둘 다 없으면 해당 구간은 건드리지 않음 (기존 config 값 유지)
"""
import sys
import os
import json
import math
import argparse
import shutil
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PUMP_KEYS = (
    "tube_vol_inlet", "tube_vol_selector", "tube_vol_valve_pump",
    "tube_vol_switcher", "tube_vol_pump_merge",
    "tube_vol_solvent", "tube_vol_reagent",
)
SYSTEM_DIRECT_KEYS = ("post_reactor_vol_ml", "collection_line_vol_ml")


def vol_from_length(length_cm, id_mm):
    """π×(id_mm/20)²×length_cm — config.py 의 reactor_vol 공식과 동일 (mL)."""
    return math.pi * ((float(id_mm) / 20.0) ** 2) * float(length_cm)


def resolve_ml(entry):
    """원장 엔트리 → 반영할 부피(mL) 또는 None(건드리지 않음).

    base = measured_ml(최우선) 또는 length_cm×id_mm 계산값.
    components = [{"name": "...", "ml": ...}] 부속(루어/유니온 등) 데드볼륨을
    base 에 합산. ml 이 null 인 부속은 미입력으로 표기만 하고 0 취급.
    """
    if not isinstance(entry, dict):
        return None, ""
    base, src = None, ""
    m = entry.get("measured_ml")
    if m is not None:
        base, src = float(m), "실측"
    else:
        ln, idm = entry.get("length_cm"), entry.get("id_mm")
        if ln is not None and idm is not None:
            base, src = vol_from_length(ln, idm), f"길이계산({ln}cm×ID{idm}mm)"
    if base is None:
        return None, ""
    add, missing = 0.0, []
    for c in (entry.get("components") or []):
        v = c.get("ml") if isinstance(c, dict) else None
        if v is not None:
            add += float(v)
        else:
            missing.append(str((c or {}).get("name", "?")))
    if add:
        src += f" +부속 {add * 1000:.1f}µL"
    if missing:
        src += f" (부속 미입력: {','.join(missing)})"
    return round(base + add, 4), src


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="튜빙 실측값 → hardware_config.json 반영")
    ap.add_argument("--apply", action="store_true", help="실제 반영 (기본은 dry-run 미리보기)")
    ap.add_argument("--seed-defaults", action="store_true",
                    help="hardware_config 에 없는 tube_vol_* 키를 0.0 으로 명시 생성 "
                         "(UI/JSON 직접 편집이 가능하도록 키를 노출; 동작 변화 없음)")
    ap.add_argument("--profile", default=None, help="반영할 프로파일 이름 (기본: active_profile)")
    ap.add_argument("--ledger", default=os.path.join(ROOT, "tubing_measurements.json"))
    ap.add_argument("--config", default=os.path.join(ROOT, "hardware_config.json"))
    args = ap.parse_args()

    ledger = load_json(args.ledger)
    cfg = load_json(args.config)

    prof_name = args.profile or ledger.get("active_profile")
    profiles = ledger.get("profiles", {})
    if prof_name not in profiles:
        print(f"!!! 프로파일 '{prof_name}' 이 원장에 없습니다. 보유: {list(profiles)}")
        return 1
    prof = profiles[prof_name]
    print(f"=== 프로파일: {prof_name} — {prof.get('description', '')}")
    print(f"    측정일: {prof.get('measured_date')} / 방법: {prof.get('method')}\n")

    changes = []   # (표시경로, 현재값, 새값, 출처)
    skipped = []

    # ── 펌프별 tube_vol_* ────────────────────────────────────────────
    pumps_role = {p.get("name"): p for p in cfg.get("roles", {}).get("pumps", [])
                  if isinstance(p, dict)}
    for p_name, seg_map in (prof.get("pumps") or {}).items():
        role = pumps_role.get(p_name)
        if role is None:
            print(f"  ⚠ 원장의 펌프 '{p_name}' 이 hardware_config roles.pumps 에 없음 — 건너뜀")
            continue
        settings = role.setdefault("settings", {})
        for key in PUMP_KEYS:
            entry = (seg_map or {}).get(key)
            new, src = resolve_ml(entry)
            label = f"{p_name}.{key}"
            if new is None:
                skipped.append(label)
                continue
            cur = float(settings.get(key, 0.0) or 0.0)
            changes.append((label, cur, new, src))
            settings[key] = new

    # ── 기본값 시드: 없는 키를 0.0 으로 명시 (엔진 기본값과 동일 → 무동작 변화) ──
    seeded = []
    if args.seed_defaults:
        for p_name, role in pumps_role.items():
            settings = role.setdefault("settings", {})
            for key in PUMP_KEYS:
                if key not in settings:
                    settings[key] = 0.0
                    seeded.append(f"{p_name}.{key}")

    # ── system_params ────────────────────────────────────────────────
    sp = cfg.setdefault("system_params", {})
    sysm = prof.get("system") or {}

    tj_cfg = sp.setdefault("tjunction_line_vols", {})
    for k, entry in (sysm.get("tjunction_line_vols") or {}).items():
        new, src = resolve_ml(entry)
        label = f"tjunction_line_vols[{k}]"
        if new is None:
            skipped.append(label)
            continue
        cur = float(tj_cfg.get(k, 0.0) or 0.0)
        changes.append((label, cur, new, src))
        tj_cfg[k] = new

    for key in SYSTEM_DIRECT_KEYS:
        new, src = resolve_ml(sysm.get(key))
        if new is None:
            skipped.append(key)
            continue
        cur = float(sp.get(key, 0.0) or 0.0)
        changes.append((key, cur, new, src))
        sp[key] = new

    # ── mixing line / reactor: 실측 부피 → 등가 길이 환산 ────────────
    mx = sysm.get("mixing_line") or {}
    if mx.get("measured_ml") is not None:
        id_mm = float(sp.get("mixing_line_id_mm", 1.5))
        area = math.pi * ((id_mm / 20.0) ** 2)          # mL/cm
        cur_len = float(sp.get("mixing_line_len_cm", 0.0))
        new_len = round(float(mx["measured_ml"]) / area, 2)
        changes.append((f"mixing_line_len_cm (등가, 실측 {mx['measured_ml']} mL)",
                        cur_len, new_len, "등가길이"))
        sp["mixing_line_len_cm"] = new_len
    else:
        skipped.append("mixing_line")

    rx = sysm.get("reactor") or {}
    if rx.get("measured_ml") is not None:
        id_mm = float(sp.get("reactor_id_mm", 1.0))
        area_per_m = math.pi * ((id_mm / 20.0) ** 2) * 100.0   # mL/m
        cur_len = float(sp.get("reactor_len_m", 0.0))
        new_len = round(float(rx["measured_ml"]) / area_per_m, 3)
        changes.append((f"reactor_len_m (등가, 실측 {rx['measured_ml']} mL)",
                        cur_len, new_len, "등가길이"))
        sp["reactor_len_m"] = new_len
    else:
        skipped.append("reactor")

    # ── 리포트 ───────────────────────────────────────────────────────
    if seeded:
        print(f"기본값 시드(키 명시, 값 0.0): {len(seeded)}건 — {', '.join(seeded)}\n")
    if not changes and not seeded:
        print(">>> 반영할 실측값이 없습니다 (measured_ml / length_cm+id_mm 전부 비어 있음).")
        print(f"    건너뛴 구간 {len(skipped)}개 — 원장에 실측을 채운 뒤 다시 실행하세요.")
        return 0

    print(f"{'구간':<44}{'현재':>9}{'반영값':>9}{'Δ':>9}  출처")
    print("-" * 84)
    for label, cur, new, src in changes:
        d = new - cur
        warn = "  ⚠ 변화 큼" if (cur > 0 and abs(d) / cur > 0.5) or (cur == 0 and new > 1.0) else ""
        print(f"{label:<44}{cur:>9.4f}{new:>9.4f}{d:>+9.4f}  {src}{warn}")
    print(f"\n반영 {len(changes)}건 / 건너뜀(값 없음) {len(skipped)}건")

    if not args.apply:
        print("\n>>> dry-run 입니다. 실제 반영: py -3.14 apply_tubing_measurements.py --apply")
        return 0

    # ── 백업 후 저장 (백업은 대상 config 파일 옆의 _backup/) ─────────
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(args.config)), "_backup")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(backup_dir, f"hardware_config.json.tubing_{ts}.bak")
    shutil.copy2(args.config, backup)
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
        f.write("\n")
    print(f"\n>>> 반영 완료: {args.config}")
    print(f">>> 백업: {backup}")
    print(">>> 다음 단계: 앱 실행 중이면 설정 핫 리로드(또는 재시작), "
          "회귀는 py -3.14 test_deadvol_timing.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
