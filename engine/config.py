import json
import math
import os
from typing import Any, Dict, Optional

from core.utils import find_port_by_usb_info
from hardware.factory import HardwareFactory


class SystemConfig:
    """Runtime system configuration loaded from hardware_config.json."""

    def __init__(self):
        # @codesyncer-decision(검증 2026-08-12, 사용자 승인): 프로젝트 루트에 __file__
        #   앵커링 — 기존 CWD 상대 경로는 "모든 스크립트는 루트에서 실행" 불변식의
        #   근본 원인이었고, 다른 CWD 에서 실행 시 조용히 기본 스키마로 폴백해
        #   빈 인벤토리/기본 reactor_vol 로 오동작했다. 저장(save_config)도 같은
        #   경로를 쓰므로 CWD 무관하게 항상 루트의 단일 파일을 읽고 쓴다.
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_file = os.path.join(_root, "hardware_config.json")

        # Default schema. Existing file data will be merged over these values.
        self.config_data: Dict[str, Any] = {
            "inventory": [],
            "roles": {
                "pumps": [],
                "heater": {"driver_id": None},
                "outlet": {"driver_id": None},
                "collector": {"driver_id": None},
            },
            "system_params": {
                "reactor_len_m": 10.0,
                "reactor_id_mm": 1.0,
                "post_reactor_vol_ml": 2.0,
                "collection_line_vol_ml": 1.0,
                "mixing_line_id_mm": 1.5,
                "mixing_line_len_cm": 150.0,
                "priming_rate_ml_min": 5.0,
                "syringe_refill_rate": 20.0,
                "temp_tolerance_c": 0.3,
                "heater_reach_timeout_sec": 900.0,
                "max_temp_c": 120.0,
                "max_pressure_bar": 20.0,
                "max_sensor_fails": 5,
                "max_total_flow_ml_min": 100.0,
                "max_step_volume_ml": 500.0,
                "wash_mode": "port_change",
                "prefill_mode": "port_change",
            },
        }

        self.ACTIVE_PUMPS = []
        self.INLET_PUMPS = []
        self.OUTLET_PUMPS = []
        self.dead_vol_solvent: Dict[str, float] = {}
        self.dead_vol_reagent: Dict[str, float] = {}
        self.mixing_line_dead_vol = 0.0
        self.reactor_vol = 7.854  # mL default fallback
        self.PUMP_VALVE_MAP: Dict[str, str] = {}
        # 초음파 레벨센서(HC-SR04) startup 잔량 설정 — {펌프명: {device_id,policy,gate_ul,slope,intercept,samples}}
        self.PUMP_LEVEL_CFG: Dict[str, Dict[str, Any]] = {}
        self.cached_inventory: Dict[str, Dict[str, Any]] = {}
        # @codesyncer-decision(2026-08-12, 프로브 폭풍 수정): USB 매칭 캐시 (부팅 1회 스캔).
        #   _usb_match_cache: (vid,pid,serial,probe) → 확정 포트. 공유 버스 장치
        #     (Chemyx 4대·Runze 4대)는 시그니처가 같아 프로브가 8→2회로 축소되고,
        #     같은 버스=같은 포트로 캐시하는 게 의미상으로도 정확하다.
        #   _usb_class_cache: {포트: probe_이름}. 이미 분류된 포트를 재프로브하지 않아
        #     Chemyx 조회가 Runze 포트를 짓밟는 상호 오염을 차단.
        #   SystemConfig 는 핫리로드 시 재생성되므로 인스턴스 캐시가 자동 무효화됨.
        self._usb_match_cache: Dict[Any, Any] = {}
        self._usb_class_cache: Dict[str, str] = {}
        # 신원확인(probe)으로 점유된 포트 — static 폴백의 도용 방지 (부팅 1회 스캔)
        self._claimed_ports: set = set()

        # Safety/runtime attributes consumed by other modules
        self.max_temp = 120.0
        self.max_pressure = 20.0
        self.max_sensor_fails = 5
        self.heater_reach_timeout_sec = 900.0

        self.load_config()

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _merge_dict(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                self._merge_dict(base[key], value)
            else:
                base[key] = value
        return base

    def load_config(self):
        """Load JSON file and merge with defaults."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._merge_dict(self.config_data, data)
            except Exception as exc:
                print(f"[Config] Load error: {exc}. Using defaults.")
        else:
            # @codesyncer(검증 2026-08-12): 조용한 기본값 폴백 금지 — 파일 부재는
            #   빈 인벤토리/기본 반응기 부피로 '조용히 오동작'하는 최악 경로였다.
            print(f"[Config] WARNING: {self.config_file} not found - "
                  f"using DEFAULT schema (empty inventory). Check installation.")

        self.process_config()

    def save_config(self, inventory, roles, sys_params=None):
        """Persist config and immediately reprocess derived fields."""
        self.config_data["inventory"] = inventory if inventory is not None else []
        self.config_data["roles"] = roles if roles is not None else {
            "pumps": [],
            "heater": {"driver_id": None},
            "outlet": {"driver_id": None},
            "collector": {"driver_id": None},
        }
        if sys_params is not None:
            self.config_data["system_params"] = sys_params

        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4, ensure_ascii=False)
            print("[Config] Saved")
            self.process_config()
        except Exception as exc:
            print(f"[Config] Save error: {exc}")

    def collect_wash_tubes(self) -> int:
        """스텝당 '세척 배출용' 플레이트 웰 소모 수 — 리포트/미리보기 회계의 단일 진실원.

        @codesyncer-decision(P1.5, 2026-07-28): 엔진 current_tube 전진 규칙과 동기 —
          · push_pump(HPLC) 경로: 웰 소모 0 (별도 wash tube 없음 — 기존 UI 가 +1 로
            과계상하던 기존 불일치도 이 헬퍼로 함께 해소)
          · collect_line_mode="compensated": 배출이 WASH 좌표 → 웰 소모 0
          · legacy syringe 경로: +1 (기존 동작)
        런타임 폴백(WASH 좌표 미지원 드라이버)은 여기서 알 수 없음 — 그 경우 엔진이
        시끄럽게 로그하고 실소모가 1 커질 수 있다(미리보기와 어긋나면 로그 참조).
        """
        sp = self.config_data.get("system_params", {}) or {}
        mode = str(sp.get("collect_line_mode", "legacy") or "legacy").strip().lower()
        push = bool(((self.config_data.get("roles", {}) or {})
                     .get("push_pump", {}) or {}).get("driver_id"))
        return 0 if (push or mode == "compensated") else 1

    def get_device_info(self, dev_id: Optional[str]):
        """
        Return inventory item by id.
        If VID/PID exists, auto-detect COM port and return with resolved port.
        """
        if not dev_id or dev_id == "None":
            return None

        device = self.cached_inventory.get(dev_id)
        if not device:
            return None

        result = dict(device)

        vid = result.get("vid")
        pid = result.get("pid")
        serial = result.get("serial")
        probe = result.get("probe")

        if vid and pid:
            # 시그니처 캐시: 같은 (vid,pid,serial,probe) 는 부팅 1회만 프로브하고
            #   결과를 공유 — 공유 버스 4대가 8회 재프로브하며 서로를 짓밟던 결함 제거.
            sig = (str(vid).upper(), str(pid).upper(), serial, probe)
            if sig in self._usb_match_cache:
                auto_port = self._usb_match_cache[sig]
            else:
                auto_port = find_port_by_usb_info(
                    vid, pid, serial, probe=probe,
                    class_cache=self._usb_class_cache)
                self._usb_match_cache[sig] = auto_port
            if auto_port:
                result["port"] = auto_port
                result["_auto_matched"] = True
                self._claimed_ports.add(auto_port)   # 신원확인(probe) 점유 등록
                print(f"[Config] Auto matched {result.get('name', dev_id)} -> {auto_port}")
                return result
            else:
                result["_auto_matched"] = False
                fallback = result.get("port")
                if fallback and fallback != "Mock_Port":
                    print(f"[Config] Auto match failed, fallback port used: {fallback}")

        # @codesyncer-decision(2026-08-12, 포트 도용 방지): static 폴백(신원 미확인)이
        #   '다른 장치가 probe 로 신원확인해 점유한' 포트를 훔치지 못하게 막는다.
        #   증상: COM 재넘버링으로 Chemyx 버스가 COM9 로 이동 → Chemyx 는 probe 로
        #   COM9 정확 매칭했는데, Runze 가 낡은 static COM9 로 폴백해 셀렉터가 먼저
        #   COM9 를 열어 점유 → Chemyx 가 PermissionError. static 은 '자기 신원이
        #   확인 안 된' 번호이므로, 이미 신원확인된 포트면 그 장치는 '미연결'로 보고
        #   Mock 처리한다(present 장치의 포트를 지켜준다). 사용자 지적: "runze 는 자기
        #   아이디도 아닌데 왜 매칭해?" = 정확히 이 도용을 막자는 것.
        final_port = result.get("port")
        if (final_port and final_port != "Mock_Port"
                and not result.get("_auto_matched")
                and final_port in self._claimed_ports):
            print(f"[Config] ⚠ {result.get('name', dev_id)}: static 포트 {final_port} 는 "
                  f"이미 다른 장치가 신원확인(probe)으로 점유 — 도용 방지 위해 미연결(Mock) 처리")
            result["port"] = "COM_Mock"
            result["_port_conflict"] = True

        return result

    def process_config(self):
        """Rebuild runtime-derived values from config_data."""
        self.ACTIVE_PUMPS = []
        self.INLET_PUMPS = []
        self.OUTLET_PUMPS = []
        self.dead_vol_solvent = {}
        self.dead_vol_reagent = {}
        # @codesyncer-decision: 채널 구간별 배관 데드볼륨 (배관도 더블클릭 편집)
        # - inlet: 시약 바이알 → 12way 밸브
        # - valve_pump: 12way → 3way → 시린지 펌프 (충전 경로)
        # - pump_merge: 3way 반응기측 포트 → 합류점 배관 (주입 경로)
        # - valve_internal: 3-way 스위처 '자체' 내부 통로 볼륨 (주입 타이밍 =
        #   valve_internal + pump_merge; 앞단 inlet/valve_pump 는 타이밍 비적용)
        self.line_vol_inlet = {}
        self.line_vol_valve_pump = {}
        self.line_vol_pump_merge = {}
        self.valve_internal_vol = {}
        self.selector_internal_vol = {}   # 12-way 밸브 내부 (퍼지 경로)
        self.PUMP_VALVE_MAP = {}
        # @codesyncer-decision: 라우팅 모드 1급화 — drivers 슬롯 조합에서 유도
        #   external_valve : 외부 12-way selector 로 소스 선택 (Chemyx 기존 설계)
        #   internal_valve : NRG 내장 main valve 만 (1펌프=1소스, valve 모드)
        #   autosampler    : drivers.sampler 슬롯 지정 (니들 이동으로 소스 선택 — 예약)
        #   UI/엔진은 이 값으로 분기하며, 존재 추론(암묵)이 아닌 명시 스키마다.
        self.PUMP_ROUTING = {}
        self.PUMP_SAMPLER_MAP = {}
        self.PUMP_LEVEL_CFG = {}

        inventory = self.config_data.get("inventory", [])
        if not isinstance(inventory, list):
            inventory = []
        self.cached_inventory = {
            str(item.get("id")): item
            for item in inventory
            if isinstance(item, dict) and item.get("id") is not None
        }

        roles = self.config_data.get("roles", {})
        pumps_role = roles.get("pumps", []) if isinstance(roles, dict) else []

        for p in pumps_role:
            if not isinstance(p, dict):
                continue

            p_name = p.get("name")
            if not p_name:
                continue

            drivers = p.get("drivers", {}) if isinstance(p.get("drivers", {}), dict) else {}
            if not drivers.get("motor"):
                continue

            self.ACTIVE_PUMPS.append(p_name)
            self.INLET_PUMPS.append(p_name)

            settings = p.get("settings", {}) if isinstance(p.get("settings", {}), dict) else {}
            # @codesyncer-decision: 세척용매/시약 포트 데드볼륨 분리 저장
            self.dead_vol_solvent[p_name] = round(
                self._safe_float(settings.get("tube_vol_solvent", 0.0), 0.0), 4)
            self.dead_vol_reagent[p_name] = round(
                self._safe_float(settings.get("tube_vol_reagent", 0.0), 0.0), 4)
            self.line_vol_inlet[p_name] = round(
                self._safe_float(settings.get("tube_vol_inlet", 0.0), 0.0), 4)
            self.line_vol_valve_pump[p_name] = round(
                self._safe_float(settings.get("tube_vol_valve_pump", 0.0), 0.0), 4)
            self.line_vol_pump_merge[p_name] = round(
                self._safe_float(settings.get("tube_vol_pump_merge", 0.0), 0.0), 4)
            # 3-way 밸브 내부 볼륨 (설정 키: tube_vol_switcher, 기본 0 = 하위호환)
            self.valve_internal_vol[p_name] = round(
                self._safe_float(settings.get("tube_vol_switcher", 0.0), 0.0), 4)
            # 12-way 밸브 내부 볼륨 — 리필/퍼지 경로(line_src)에 가산
            self.selector_internal_vol[p_name] = round(
                self._safe_float(settings.get("tube_vol_selector", 0.0), 0.0), 4)

            # ── 초음파 레벨센서(HC-SR04) startup 잔량 — drivers.level 슬롯 (선택) ──
            # @codesyncer-decision: RoboChem SI 3.3.1 이식. 펌프별 '반사블록 거리→부피'
            #   선형 캘리브(mL). policy=reagent(잔량 폐액 퍼지→센서검증→리셋, 교차오염
            #   방지) | solvent(잔량 그대로 채택, RoboChem no_fill식 재사용 라인).
            #   gate_ul 은 센서노이즈(±수백µL)보다 충분히 커야 함. 슬롯 없으면 미등록
            #   → 엔진 _startup_level_reconcile 무동작(기존 '비어있다 가정' 폴백).
            level_id = drivers.get("level")
            if level_id and level_id != "None":
                pol = str(settings.get("level_policy", "reagent")).strip().lower()
                if pol not in ("reagent", "solvent"):
                    pol = "reagent"
                self.PUMP_LEVEL_CFG[p_name] = {
                    "device_id": level_id,
                    "policy": pol,
                    "gate_ul": self._safe_float(settings.get("level_empty_gate_ul", 500.0), 500.0),
                    "slope": self._safe_float(settings.get("level_cal_slope", 1.0), 1.0),
                    "intercept": self._safe_float(settings.get("level_cal_intercept", 0.0), 0.0),
                    "samples": self._safe_int(settings.get("level_samples", 20), 20),
                    # 4채널 펌웨어 CSV 열 인덱스 (0~3). 한 Arduino 를 4펌프가 공유.
                    "index": self._safe_int(settings.get("level_channel_index", 0), 0),
                }

            # ── 라우팅 모드 유도 + 조합 제약 백스톱 ──────────────────
            # @codesyncer-decision: NRG motor 는 외부 selector/switcher 와 조합 금지
            #   (내장 Mrv-01B 와 역할 충돌/무의미). 다이얼로그가 1차 차단하지만,
            #   JSON 수동 편집/구버전 파일 경로를 위해 여기서 백스톱 — 경고 후
            #   PUMP_VALVE_MAP 에 미등록해 엔진 밸브전환/인터락에서 제외한다.
            selector_id = drivers.get("selector")
            has_selector = bool(selector_id and selector_id != "None")
            sampler_id = drivers.get("sampler")
            has_sampler = bool(sampler_id and sampler_id != "None")

            motor_item = self.cached_inventory.get(str(drivers.get("motor"))) or {}
            motor_type = HardwareFactory.get_driver_type(motor_item.get("driver", ""))

            if has_sampler:
                self.PUMP_SAMPLER_MAP[p_name] = sampler_id

            if motor_type == "NRGSyringePump":
                self.PUMP_ROUTING[p_name] = "autosampler" if has_sampler else "internal_valve"
                if has_selector or (drivers.get("switcher") and drivers.get("switcher") != "None"):
                    print(f"[Config] ⚠ {p_name}: NRG motor 는 외부 selector/switcher 와 "
                          f"함께 쓸 수 없음 — 해당 밸브 배정을 무시합니다 (내장 밸브 사용)")
            else:
                self.PUMP_ROUTING[p_name] = "external_valve"
                if has_selector:
                    self.PUMP_VALVE_MAP[p_name] = f"{p_name}_Selector"

        sp = self.config_data.get("system_params", {})
        if not isinstance(sp, dict):
            sp = {}
            self.config_data["system_params"] = sp

        self.max_temp = self._safe_float(sp.get("max_temp_c", 120.0), 120.0)
        self.max_pressure = self._safe_float(sp.get("max_pressure_bar", 20.0), 20.0)
        self.heater_reach_timeout_sec = self._safe_float(sp.get("heater_reach_timeout_sec", 900.0), 900.0)
        self.max_sensor_fails = self._safe_int(sp.get("max_sensor_fails", 5), 5)

        l_m = self._safe_float(sp.get("reactor_len_m", 10.0), 10.0)
        id_mm = self._safe_float(sp.get("reactor_id_mm", 1.0), 1.0)
        self.reactor_vol = math.pi * ((id_mm / 20.0) ** 2) * (l_m * 100.0)

        # @codesyncer-decision: T-junction 캐스케이드 구간 볼륨
        # 실제 합류는 단일점이 아니라 T1(P1+P2) → T2(+P3) ... 순차 합류.
        # tjunction_line_vols[j] = T_j → T_{j+1} 구간 부피 (j는 1부터,
        # 펌프 n개면 j=1..n-2). 마지막 정션→반응기는 mixing line(기존).
        self.tjunction_line_vols = {}
        _tj = sp.get("tjunction_line_vols", {})
        if isinstance(_tj, dict):
            for k, v in _tj.items():
                try:
                    self.tjunction_line_vols[int(k)] = round(self._safe_float(v, 0.0), 4)
                except Exception:
                    continue

        # @codesyncer-decision(2026-08-12, 배관 재구성): 진입 정션 매핑 —
        # {펌프명: 진입 구간번호}. QUAD 합류 토폴로지(Solvent+A+B→QUAD-1,
        # +C+D→QUAD-2)처럼 3-in 동시 합류는 페어와이즈 캐스케이드 유도식으로
        # 표현 불가라 명시 매핑이 필요. 비어 있으면 엔진이 레거시 캐스케이드
        # (P1,P2→T1, P_m→T_{m-1})를 유도 — 구버전 config 하위호환.
        self.tjunction_entry_map = {}
        _tje = sp.get("tjunction_entry_map", {})
        if isinstance(_tje, dict):
            for k, v in _tje.items():
                try:
                    self.tjunction_entry_map[str(k)] = max(1, int(v))
                except Exception:
                    continue
        # @codesyncer(2026-08-13): 그룹은 있을 수도/없을 수도(A/D 만 등 임의 부분집합).
        #   활성 펌프가 맵에 미기재면 엔진이 진입 1(보수적)로 가정 — 실배관이 후행
        #   정션(QUAD-2)이면 타이밍 오계산이므로 부팅 시 크게 경고. 잉여 키(제외
        #   그룹)는 무해하게 남는다.
        if self.tjunction_entry_map:
            _missing = [p for p in self.ACTIVE_PUMPS
                        if p not in self.tjunction_entry_map]
            if _missing:
                print(f"[Config] ⚠ tjunction_entry_map 미기재 활성 펌프 {_missing} — "
                      f"진입 구간 1 로 가정합니다. 실배관이 QUAD-2 진입이면 "
                      f"system_params.tjunction_entry_map 에 추가하세요.")

        mixing_id = self._safe_float(sp.get("mixing_line_id_mm", 1.5), 1.5)
        mixing_len = self._safe_float(sp.get("mixing_line_len_cm", 150.0), 150.0)
        r_cm = (mixing_id / 10.0) / 2.0
        self.mixing_line_dead_vol = math.pi * (r_cm ** 2) * mixing_len

        print(f"[Config] Reactor volume: {self.reactor_vol:.4f} mL")
        print("[Config] Dead volumes (pump lines):")
        for p_name in self.ACTIVE_PUMPS:
            sv = self.dead_vol_solvent.get(p_name, 0.0)
            rv = self.dead_vol_reagent.get(p_name, 0.0)
            vi = self.valve_internal_vol.get(p_name, 0.0)
            pm = self.line_vol_pump_merge.get(p_name, 0.0)
            print(f"  {p_name}: solvent={sv:.4f} mL, reagent={rv:.4f} mL, "
                  f"3way_internal={vi:.4f} mL, pump_merge={pm:.4f} mL")
        print(f"[Config] Mixing line dead volume: {self.mixing_line_dead_vol:.4f} mL")

        # @codesyncer(감사 F1): 구형 키(tube_vol_solvent/reagent = 세척·리필 '부피' 전용)에만
        #   실측이 있고, 타이밍 모델(퍼지·동시도착·HTE 마크)이 읽는 세분화 키
        #   (tube_vol_inlet/valve_pump/selector/switcher/pump_merge, tjunction)가 전부 0이면
        #   보정이 조용히 무력화된 상태 — 시끄럽게 경고.
        _legacy_has = any(v > 0 for v in list(self.dead_vol_solvent.values())
                          + list(self.dead_vol_reagent.values()))
        # @codesyncer(감사 2026-07-13 이슈6): T-junction 은 합류 '이후' 공용 구간이라
        #   펌프별 소스 퍼지/프리필 과충전 보정을 대체하지 못함 — 기존 or 결합은
        #   tjunction 값 하나만 있어도 경고가 꺼져 펌프별 보정 누락(전부 0)이 조용히
        #   지나갔음. 펌프별 세분화 키만으로 억제 여부를 판단한다.
        _granular_pump_has = any(
            v > 0 for d in (self.line_vol_inlet, self.line_vol_valve_pump,
                            self.selector_internal_vol, self.valve_internal_vol,
                            self.line_vol_pump_merge)
            for v in d.values())
        if _legacy_has and not _granular_pump_has:
            _tj_note = (" (tjunction_line_vols 는 합류 후 공용 구간이라 펌프별 보정을 "
                        "대체하지 않습니다.)" if any(
                            v > 0 for v in self.tjunction_line_vols.values()) else "")
            print("[Config] ⚠ 펌프별 타이밍용 라인 데드볼륨(세분화 키)이 전부 0 입니다 — "
                  "tube_vol_solvent/reagent 는 세척·리필 부피 전용이라 퍼지/동시도착 "
                  "보정에 쓰이지 않습니다. 대시보드 배관도의 DETAIL 칩(더블클릭)으로 "
                  "구간별 부피를 입력하세요." + _tj_note)

    def get_max_dead_volume(self):
        """Return max dead volume across configured line segments."""
        max_solvent = max(self.dead_vol_solvent.values()) if self.dead_vol_solvent else 0.0
        max_reagent = max(self.dead_vol_reagent.values()) if self.dead_vol_reagent else 0.0
        return max(max_solvent, max_reagent, self.mixing_line_dead_vol)
