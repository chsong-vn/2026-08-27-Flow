"""
Experiment Report Generator - 실험 결과 JSON + 튜브 배치도 PNG 저장
@codesyncer-decision: main.py에서 분리 - 실험 결과 저장 단일 책임

@codesyncer-decision: 날짜별 폴더 + 일련번호 구조
  results/
    experiment_log.csv       ← 전체 실험 카탈로그 (한 줄씩 누적)
    2026-03-06/
      EXP_001/
        ├── report.json
        ├── tube_layout.png
        └── LOG_*.csv
      EXP_002/
        └── ...

사용법:
    report = ExperimentReport(app)
    report.save(sequence_plan, start_time)
"""

import os
import csv
import math
import json
import shutil
from datetime import datetime


class ExperimentReport:
    """실험 결과를 JSON + 튜브 배치도 PNG로 저장합니다."""

    def __init__(self, app):
        self.app = app
        self._results_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results"
        )

    def save(self, plan, start_time, status="completed"):
        """
        실험 결과 폴더 생성 및 저장.

        Args:
            plan: sequence_plan (run_seq에서 전달)
            start_time: 실험 시작 datetime
            status: "completed" | "aborted" | "error"
        Returns:
            결과 폴더 경로 (str) or None
        """
        app = self.app
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() / 60.0

        # ── 날짜 폴더 + 일련번호 ──
        date_str = start_time.strftime("%Y-%m-%d")
        date_dir = os.path.join(self._results_root, date_str)
        os.makedirs(date_dir, exist_ok=True)

        exp_num = self._next_exp_number(date_dir)
        exp_folder = f"EXP_{exp_num:03d}"
        exp_dir = os.path.join(date_dir, exp_folder)
        os.makedirs(exp_dir, exist_ok=True)

        # ── 튜브 배치도 PNG ──
        tube_png_name = "tube_layout.png"
        step_infos = self._build_step_infos()
        start_tube = app.seq_tab.sp_collector_start.value()
        try:
            self._save_tube_layout_png(
                os.path.join(exp_dir, tube_png_name),
                start_tube, step_infos
            )
        except Exception as e:
            print(f"[Report] Tube layout PNG failed: {e}")
            tube_png_name = None

        # ── report.json 구성 ──
        cfg = app.cfg
        sp = cfg.config_data.get("system_params", {})

        # 하드웨어 스냅샷
        hardware = {
            "reactor_length_m": sp.get("reactor_len_m", 10.0),
            "reactor_id_mm": sp.get("reactor_id_mm", 1.0),
            "reactor_vol_ml": round(cfg.reactor_vol, 4),
            "post_reactor_vol_ml": sp.get("post_reactor_vol_ml", 2.0),
            "collection_line_vol_ml": sp.get("collection_line_vol_ml", 1.0),
            "mixing_line_id_mm": sp.get("mixing_line_id_mm", 1.5),
            "mixing_line_len_cm": sp.get("mixing_line_len_cm", 150.0),
            "mixing_line_dead_vol_ml": round(cfg.mixing_line_dead_vol, 4),
            "pumps": list(cfg.INLET_PUMPS),
        }

        # 시약 정보 (사용된 포트만)
        reagents = {}
        used_ports = {}
        for step in plan:
            for pump_name, port in step.get("inlet_ports", {}).items():
                used_ports.setdefault(pump_name, set()).add(port)

        if app.map_mgr:
            for pump_name, ports in used_ports.items():
                reagents[pump_name] = {}
                for port in sorted(ports):
                    info = app.map_mgr.get_inlet(pump_name, port)
                    reagents[pump_name][str(port)] = {
                        "name": info.get("name", ""),
                        "conc": info.get("conc", 1.0)
                    }

        # Step별 데이터
        # @codesyncer(P1.5): wash 웰 소모는 cfg.collect_wash_tubes() 단일 진실원
        #   (HPLC push/compensated 라인모드 = 0 — 기존 무조건 +1 과계상 해소)
        _wpt = 1
        _cfg = getattr(app, "cfg", None)
        if _cfg is not None and hasattr(_cfg, "collect_wash_tubes"):
            try:
                _wpt = int(_cfg.collect_wash_tubes())
            except Exception:
                _wpt = 1
        sequence = []
        tube_cursor = start_tube
        for i, step in enumerate(plan):
            total_flow = sum(step.get("flows", {}).values())
            target_vol = step.get("vol_ml", 0)
            tube_vol = step.get("collect_volume_per_tube", 1.5)
            num_tubes = math.ceil(target_vol / tube_vol) if tube_vol > 0 else 0
            tube_range = f"{tube_cursor}-{tube_cursor + num_tubes - 1}" if num_tubes > 0 else "-"
            wash_tube = (tube_cursor + num_tubes) if (num_tubes > 0 and _wpt > 0) else None

            step_entry = {
                "step": i + 1,
                "temp_c": step.get("temp", 25.0),
                "rt_min": step.get("residence_time", 10.0),
                "target_vol_ml": target_vol,
                "tube_vol_ml": tube_vol,
                "pumps": {},
                "total_flow_ml_min": round(total_flow, 4),
                "tubes_used": tube_range,
                "wash_tube": wash_tube,
            }

            for pump_name, flow in step.get("flows", {}).items():
                port = step.get("inlet_ports", {}).get(pump_name, 2)
                meta = step.get("meta", {}).get(pump_name, {})
                step_entry["pumps"][pump_name] = {
                    "port": port,
                    "reagent": meta.get("name", ""),
                    "conc": meta.get("conc", 1.0),
                    "flow_ml_min": round(flow, 4),
                }

            sequence.append(step_entry)
            tube_cursor += num_tubes + _wpt

        # 분취 요약
        total_tubes_used = tube_cursor - start_tube
        collection = {
            "start_tube": start_tube,
            "total_tubes_used": total_tubes_used,
        }
        if tube_png_name:
            collection["layout_image"] = tube_png_name

        # CSV 로그 파일 찾기 → 복사
        log_file = self._find_latest_csv_log()
        log_basename = None
        if log_file:
            log_basename = os.path.basename(log_file)
            try:
                shutil.copy2(log_file, exp_dir)
            except Exception as e:
                print(f"[Report] CSV copy failed: {e}")
                log_basename = None

        report = {
            "experiment": {
                "id": f"{date_str}/{exp_folder}",
                "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_min": round(duration, 1),
                "status": status,
                "steps": len(plan),
            },
            "hardware": hardware,
            "reagents": reagents,
            "sequence": sequence,
            "collection": collection,
        }
        if log_basename:
            report["log_file"] = log_basename

        # JSON 저장
        report_path = os.path.join(exp_dir, "report.json")
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[Report] JSON save failed: {e}")
            return None

        # ── 실험 카탈로그 (experiment_log.csv) 추가 ──
        self._append_to_catalog(
            date_str, exp_folder, start_time, end_time,
            status, len(plan), total_tubes_used
        )

        print(f"[Report] Saved: {exp_dir}")
        return exp_dir

    # ─── 폴더/번호 관리 ─────────────────────────────────────────

    @staticmethod
    def _next_exp_number(date_dir):
        """날짜 폴더 내 다음 실험 번호 반환 (EXP_001, EXP_002, ...)"""
        existing = []
        if os.path.isdir(date_dir):
            for name in os.listdir(date_dir):
                if name.startswith("EXP_") and os.path.isdir(os.path.join(date_dir, name)):
                    try:
                        num = int(name.split("_")[1])
                        existing.append(num)
                    except (ValueError, IndexError):
                        pass
        return max(existing, default=0) + 1

    def _append_to_catalog(self, date_str, exp_folder, start_time, end_time,
                           status, num_steps, total_tubes):
        """
        results/experiment_log.csv에 실험 1행 추가.
        @codesyncer-decision: 전체 실험 이력을 단일 CSV로 관리
          - Excel/스프레드시트에서 바로 열어 필터링 가능
          - 헤더가 없으면 자동 생성
        """
        catalog_path = os.path.join(self._results_root, "experiment_log.csv")
        write_header = not os.path.exists(catalog_path)

        try:
            with open(catalog_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow([
                        "Date", "Experiment", "Start", "End",
                        "Duration(min)", "Status", "Steps", "Tubes Used", "Folder"
                    ])
                writer.writerow([
                    date_str,
                    exp_folder,
                    start_time.strftime("%H:%M:%S"),
                    end_time.strftime("%H:%M:%S"),
                    round((end_time - start_time).total_seconds() / 60.0, 1),
                    status,
                    num_steps,
                    total_tubes,
                    f"{date_str}/{exp_folder}",
                ])
        except Exception as e:
            print(f"[Report] Catalog append failed: {e}")

    # ─── 내부 헬퍼 ──────────────────────────────────────────────

    def _build_step_infos(self):
        """SequenceTab의 sequence_data에서 step_infos 리스트 생성 (SpiralPreview 형식)"""
        _cfg = getattr(self.app, "cfg", None)
        _wpt = (_cfg.collect_wash_tubes()
                if (_cfg is not None and hasattr(_cfg, "collect_wash_tubes")) else 1)
        step_infos = []
        for i, step_data in enumerate(self.app.seq_tab.sequence_data):
            vol = step_data["vol"]
            tube_vol = step_data["tube_vol"]
            num_tubes = math.ceil(vol / tube_vol) if tube_vol > 0 else 0
            step_infos.append({
                "name": f"Step {i + 1}",
                "num_tubes": num_tubes,
                "wash_tubes": _wpt,
                "vol_per_tube": tube_vol,
                "total_vol": vol,
            })
        return step_infos

    def _find_latest_csv_log(self):
        """가장 최근 CSV 로그 파일 경로 반환"""
        log_dir = "logs"
        if hasattr(self.app, 'engine') and self.app.engine:
            log_dir = getattr(self.app.engine, 'log_dir', 'logs')
        if not os.path.isdir(log_dir):
            return None
        csv_files = [
            os.path.join(log_dir, f)
            for f in os.listdir(log_dir)
            if f.startswith("LOG_") and f.endswith(".csv")
        ]
        if not csv_files:
            return None
        return max(csv_files, key=os.path.getmtime)

    # ─── 튜브 배치도 PNG 생성 (Headless) ────────────────────────

    # 나선 파라미터 (SpiralPreviewDialog과 동일)
    N_TUBES    = 88
    ARC        = 13.0
    SEPARATION = 17.39
    INIT_D     = 18.595
    R_BED      = 100
    R_EFF      = 90
    R_EMPTY    = 11.5
    R_TUBE     = 5.5

    STEP_COLORS = [
        "#4a90d9", "#e8943a", "#50c878", "#e05555",
        "#9d7fd9", "#d9a03a", "#3ac4d9", "#d94a8a",
        "#7fd97f", "#d9d93a",
    ]

    @staticmethod
    def _spiral_points(init_d, arc, separation):
        """아르키메데스 나선 좌표 생성기."""
        yield (0, 0, 0)
        b = separation / (2 * math.pi)
        r = init_d
        phi = r / b
        while True:
            yield (r * math.cos(phi), r * math.sin(phi), phi)
            phi += arc / r
            r = b * phi

    def _generate_spiral(self):
        """88개 튜브 좌표 배열 생성."""
        data = []
        for idx, (x, y, _) in enumerate(
            self._spiral_points(self.INIT_D, self.ARC, self.SEPARATION), -1
        ):
            if idx == self.N_TUBES:
                break
            if idx >= 0:
                data.append((x, y))
        return data

    def _save_tube_layout_png(self, filepath, start_tube, step_infos):
        """
        나선형 튜브 배치도를 PNG 파일로 저장 (화면 표시 없이).

        @codesyncer-decision: SpiralPreviewDialog._draw_spiral()과 동일 로직
          - GUI 없이 matplotlib Agg 백엔드로 렌더링
          - 흰 배경 고정 (인쇄/문서 첨부 용도)
        """
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        spiral_data = self._generate_spiral()

        # 색상 매핑
        tube_colors = {}
        tube_is_wash = set()
        current = start_tube - 1  # 0-based

        total_used = 0
        for si, info in enumerate(step_infos):
            n = info["num_tubes"]
            w = info.get("wash_tubes", 0)
            color = self.STEP_COLORS[si % len(self.STEP_COLORS)]
            for j in range(n):
                t_idx = current + j
                if 0 <= t_idx < self.N_TUBES:
                    tube_colors[t_idx] = color
            current += n
            for j in range(w):
                t_idx = current + j
                if 0 <= t_idx < self.N_TUBES:
                    tube_colors[t_idx] = color
                    tube_is_wash.add(t_idx)
            current += w
            total_used += n + w

        # 그리기 (흰 배경)
        fig, ax = plt.subplots(figsize=(8, 8), dpi=150)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.set_aspect("equal")

        border_color = "#cccccc"
        empty_color = "#999999"

        # 경계 원
        for r_val, ls in [(self.R_BED, "-"), (self.R_EFF, "--"), (self.R_EMPTY, "--")]:
            ax.add_patch(mpatches.Circle(
                (0, 0), r_val, facecolor="none",
                edgecolor=border_color, linewidth=0.8, linestyle=ls
            ))

        # 나선 경로
        xs = [p[0] for p in spiral_data]
        ys = [p[1] for p in spiral_data]
        ax.plot(xs, ys, "-", color=border_color, linewidth=0.4, alpha=0.4, zorder=1)

        # 튜브
        for i, (x, y) in enumerate(spiral_data):
            if i in tube_colors:
                if i in tube_is_wash:
                    ax.add_patch(mpatches.Circle(
                        (x, y), self.R_TUBE,
                        facecolor="none", edgecolor=tube_colors[i],
                        linewidth=1.2, alpha=0.9, zorder=2, linestyle="--",
                    ))
                    r = self.R_TUBE * 0.55
                    ax.plot([x-r, x+r], [y-r, y+r], color=tube_colors[i], lw=0.6, alpha=0.7, zorder=2.5)
                    ax.plot([x-r, x+r], [y+r, y-r], color=tube_colors[i], lw=0.6, alpha=0.7, zorder=2.5)
                    txt_color = tube_colors[i]
                else:
                    ax.add_patch(mpatches.Circle(
                        (x, y), self.R_TUBE,
                        facecolor=tube_colors[i], edgecolor=tube_colors[i],
                        linewidth=0.8, alpha=0.85, zorder=2
                    ))
                    txt_color = "#ffffff"
            else:
                ax.add_patch(mpatches.Circle(
                    (x, y), self.R_TUBE,
                    facecolor="none", edgecolor=empty_color,
                    linewidth=0.8, alpha=0.3, zorder=2
                ))
                txt_color = empty_color

            ax.text(x, y, str(i + 1), ha="center", va="center",
                    fontsize=5, color=txt_color, fontweight="bold", zorder=3,
                    alpha=1.0 if i in tube_colors else 0.4)

        # 축 설정
        margin = 15
        ax.set_xlim(-self.R_BED - margin, self.R_BED + margin)
        ax.set_ylim(-self.R_BED - margin, self.R_BED + margin)
        ax.set_xlabel("X [mm]", fontsize=9)
        ax.set_ylabel("Y [mm]", fontsize=9)

        title = f"Tube {start_tube} ~ {start_tube + total_used - 1} (Total {total_used})"
        if start_tube + total_used - 1 > self.N_TUBES:
            title += f"  [WARNING: exceeds {self.N_TUBES}]"
        ax.set_title(title, fontsize=11, fontweight="600", pad=12)

        # 범례
        legend_handles = []
        for si, info in enumerate(step_infos):
            color = self.STEP_COLORS[si % len(self.STEP_COLORS)]
            patch = mpatches.Patch(
                color=color,
                label=f'{info["name"]}: {info["num_tubes"]}tubes, {info["total_vol"]:.1f}mL'
            )
            legend_handles.append(patch)

        if legend_handles:
            ax.legend(handles=legend_handles, loc="upper right", fontsize=8,
                      framealpha=0.9, edgecolor="#cccccc")

        fig.tight_layout()
        fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"[Report] Tube layout saved: {filepath}")
