"""
Hardware Manager - 하드웨어 초기화 및 정리 담당
@codesyncer-decision: main.py에서 분리 - 하드웨어 로직만 단일 책임

사용법:
    hw_mgr = HardwareManager(cfg, signals)
    hw_mgr.init_hw()
    # hw_mgr.pumps, hw_mgr.valves, hw_mgr.heater, ... 접근
    hw_mgr.cleanup_hardware()
"""

from engine.config import SystemConfig
from engine.calculators import FlowCalculator
from engine.safety_manager import SafetyManager
from engine.strict_engine import StrictSequenceEngine

from hardware.factory import HardwareFactory
from hardware.collectors.collector_colosseum import ColosseumCollector
from hardware.collectors.collector_mock import MockCollector
from hardware.collectors.collector_plate96 import Plate96Collector
from hardware.pumps.pump_chemyx_smart import ChemyxSmartPump
from hardware.pumps.pump_chemyx import ChemyxPump
from hardware.pumps.pump_mock import MockPump
from hardware.valves.valve_mock import MockValve
from hardware.heaters.heater_mock import MockHeater

# 확장 드라이버 (없으면 무시)
try:
    from hardware.pumps.pump_vapourtec import VapourtecPump
    from hardware.pumps.pump_reaxus import ReaxusPump
    from hardware.valves.valve_arduino import ArduinoValve
    from hardware.valves.valve_runze_sv07 import RunzeSV07Valve
    from hardware.heaters.heater_bath_modbus import BathModbusHeater
except ImportError:
    pass

# ESP32-S3-ETH-8DI-8RO 3-way 밸브 (없으면 무시)
# @codesyncer-decision: 별도 try 블록 — 위 확장 드라이버 import 실패와 상호 격리
try:
    from hardware.valves.valve_esp32_eth import ESP32EthValve
except ImportError:
    pass

# NRG 시린지 펌프 + Cartesian 샘플러 (없으면 무시)
# @codesyncer-decision: 기존 try/except 블록과 분리 — 새 모듈이라 사용자가 폴더만 가져가도
#   기존 드라이버 import 실패에 영향 주지 않음.
try:
    from hardware.pumps.pump_nrg_syringe import NRGSyringePump
    from hardware.pumps.pump_nrg_syringe_mock import MockNRGSyringePump
    from hardware.pumps.pump_nrg_smart import NRGSmartPump
except ImportError:
    pass

try:
    from hardware.samplers.sampler_grbl_cartesian import GrblCartesianSampler
    from hardware.samplers.sampler_mock import MockCartesianSampler
except ImportError:
    pass


class HardwareManager:
    """하드웨어 장치의 초기화와 정리를 담당합니다."""

    def __init__(self, cfg: SystemConfig, signals):
        self.cfg = cfg
        self.signals = signals
        self.pumps = {}
        self.valves = {}
        self.heater = None
        self.collector = None
        self.push_pump = None
        self.safety = None
        self.engine = None
        self.calculator = None
        # @codesyncer-decision: samplers — Cartesian 시약 픽업 장비용 신규 슬롯.
        #   pumps/valves 와 동급의 dict 로 두어 여러 대 운용 가능.
        #   manual_pumps — 시퀀스 그룹 (Group A/B/C) 에 속하지 않은 수동 펌프 슬롯.
        self.samplers = {}
        self.manual_pumps = {}

    def _safe_connect(self, device, name="device"):
        """연결 시도, 실패 시 로그만 남기고 넘어간다."""
        try:
            if hasattr(device, 'connect'):
                device.connect()
        except Exception as e:
            print(f"  - {name} 연결 실패 (무시): {e}")

    def init_hw(self):
        """모든 하드웨어를 초기화합니다. (펌프, 밸브, 히터, 수집기, 엔진)"""
        self.pumps = {}
        self.valves = {}

        # 1. Heater
        h_conf = self.cfg.config_data["roles"].get("heater", {})
        h_info = self.cfg.get_device_info(h_conf.get("driver_id"))

        if h_info:
            heater_driver = HardwareFactory.get_driver_type(h_info.get("driver"))
            if heater_driver == "BathModbusHeater" and 'BathModbusHeater' in globals():
                self.heater = BathModbusHeater(h_info["port"])
            else:
                self.heater = MockHeater()
        else:
            self.heater = MockHeater()

        self._safe_connect(self.heater, "Heater")

        # 2. Outlet Valve
        o_conf = self.cfg.config_data["roles"].get("outlet", {})
        o_info = self.cfg.get_device_info(o_conf.get("driver_id"))

        if o_info:
            outlet_driver = HardwareFactory.get_driver_type(o_info.get("driver"))
            if outlet_driver == "ArduinoValve" and 'ArduinoValve' in globals():
                o_channel = int(o_info.get("channel", 1))
                self.valves["Outlet"] = ArduinoValve(o_info["port"], channel=o_channel)
                self._safe_connect(self.valves["Outlet"], "Outlet")
            elif outlet_driver == "ESP32EthValve" and 'ESP32EthValve' in globals():
                o_channel = int(o_info.get("channel", 1))
                # @codesyncer(2026-08-13): invert = collector/waste 튜브 반대 체결을
                #   소프트웨어에서 흡수 (docs/아웃렛_배선반전_주의.md 필독)
                o_invert = bool(o_info.get("invert", False))
                self.valves["Outlet"] = ESP32EthValve(o_info["port"], channel=o_channel,
                                                      invert=o_invert)
                if o_invert:
                    print("  ⚠⚠ [Outlet] 배선 반전 모드(invert) — collector/waste 튜브가 "
                          "반대로 체결된 상태를 SW 로 흡수 중. 무전원 기본 물리 경로가 "
                          "COLLECT 쪽임에 주의. 재배관 시 config invert 제거 필수 "
                          "(docs/아웃렛_배선반전_주의.md)")
                self._safe_connect(self.valves["Outlet"], "Outlet")
            else:
                self.valves["Outlet"] = MockValve("Outlet_Virtual")
        else:
            self.valves["Outlet"] = MockValve("Outlet_Virtual")

        # 3. Pumps
        pumps_conf = self.cfg.config_data["roles"].get("pumps", [])
        for group_idx, p_role in enumerate(pumps_conf):
            p_name = p_role["name"]
            drivers = p_role["drivers"]
            settings = p_role.get("settings", {})

            m_info = self.cfg.get_device_info(drivers.get("motor"))
            motor_driver_korean = m_info["driver"] if m_info else "MockPump"
            motor_driver = HardwareFactory.get_driver_type(motor_driver_korean)
            motor_port = m_info["port"] if m_info else "COM_Mock"

            # @codesyncer-decision: 드라이버 조합 제약 강제 (config 백스톱과 동일 규칙)
            #   NRG motor 그룹에는 외부 selector/switcher 를 인스턴스화하지 않는다 —
            #   내장 Mrv-01B 와 역할 충돌. 다이얼로그(1차)·config(경고)·여기(실행 차단)
            #   3계층 방어. drivers.sampler 슬롯은 오토샘플러 모드 예약(스키마 b).
            is_nrg_motor = (motor_driver == "NRGSyringePump")
            if is_nrg_motor and (drivers.get("selector") or drivers.get("switcher")):
                print(f"  - [{p_name}] ⚠ NRG motor: 외부 selector/switcher 배정 무시 (내장 밸브 사용)")
            sampler_slot_id = drivers.get("sampler")
            if is_nrg_motor and sampler_slot_id and sampler_slot_id != "None":
                print(f"  - [{p_name}] 오토샘플러 모드 (sampler={sampler_slot_id}) — "
                      f"니들 조율 v0: 프리필 흡입 시 per-step vial 로 니들 이동")

            # @codesyncer-decision: config address = 실제 RS-485 주소 (직통, 변환 없음)
            default_sel_addr = group_idx
            default_sw_addr = group_idx

            # Selector
            sel_obj = None
            sel_info = None if is_nrg_motor else self.cfg.get_device_info(drivers.get("selector"))
            if sel_info:
                sel_driver = HardwareFactory.get_driver_type(sel_info["driver"])
                if sel_driver == "RunzeSV07Valve" and 'RunzeSV07Valve' in globals():
                    sel_addr = int(sel_info.get("address", default_sel_addr))
                    sel_obj = RunzeSV07Valve(sel_info["port"], name=f"Sel_{p_name}", address=sel_addr)
                    self._safe_connect(sel_obj, f"Sel_{p_name}")
                else:
                    sel_obj = MockValve(f"Sel_{p_name}")
            if sel_obj:
                self.valves[f"{p_name}_Selector"] = sel_obj

            # Switcher
            sw_obj = None
            sw_info = None if is_nrg_motor else self.cfg.get_device_info(drivers.get("switcher"))
            if sw_info:
                sw_driver = HardwareFactory.get_driver_type(sw_info["driver"])
                if sw_driver == "ArduinoValve" and 'ArduinoValve' in globals():
                    sw_channel = int(sw_info.get("channel", group_idx + 1))
                    sw_obj = ArduinoValve(sw_info["port"], channel=sw_channel)
                    self._safe_connect(sw_obj, f"Sw_{p_name}")
                elif sw_driver == "ESP32EthValve" and 'ESP32EthValve' in globals():
                    sw_channel = int(sw_info.get("channel", group_idx + 1))
                    sw_obj = ESP32EthValve(sw_info["port"], channel=sw_channel,
                                           invert=bool(sw_info.get("invert", False)))
                    self._safe_connect(sw_obj, f"Sw_{p_name}")
                elif sw_driver == "RunzeSV07Valve" and 'RunzeSV07Valve' in globals():
                    sw_addr = int(sw_info.get("address", default_sw_addr))
                    sw_obj = RunzeSV07Valve(sw_info["port"], name=f"Sw_{p_name}", address=sw_addr)
                    self._safe_connect(sw_obj, f"Sw_{p_name}")
                else:
                    sw_obj = MockValve(f"Sw_{p_name}")
            if sw_obj:
                self.valves[f"{p_name}_Switcher"] = sw_obj

            # Motor Driver
            if motor_driver == "Chemyx":
                # @codesyncer-decision: refill_rate/prime_rate를 system_params에서 읽기
                # - 기존: refill_rate=40.0 하드코딩 → UI "시린지 충전 속도"/"프라이밍 속도" 무시됨
                # - 수정: syringe_refill_rate → refill(WITHDRAW), priming_rate_ml_min → prime(INFUSE)
                sys_p = self.cfg.config_data.get("system_params", {})
                # @codesyncer-decision(2026-08-13, 오배정 사고): RS-485 pump_id 는
                #   '장치' 고유값(펌프 전면판에 설정된 체인 주소) — 그룹 순번이 아니다.
                #   다이얼로그가 순번+1 로 덮어써 온 탓에 B/C 그룹 제거 시 Group_D 가
                #   ID 2 로 저장돼 엉뚱한 물리 펌프(ID2)가 구동된 사고. 해석 우선순위:
                #   모터 장치 inventory.settings.pump_id → 역할 settings(레거시) → 0.
                _dev_pid = ((m_info or {}).get("settings", {}) or {}).get("pump_id")
                _pid = int(_dev_pid if _dev_pid is not None
                           else settings.get('pump_id', 0))
                if _dev_pid is not None and int(settings.get('pump_id', _dev_pid)) != int(_dev_pid):
                    print(f"  - [{p_name}] ⚠ 역할 pump_id {settings.get('pump_id')} ≠ "
                          f"장치({m_info.get('name')}) pump_id {_dev_pid} — 장치값 사용")
                spec = {
                    'diameter': float(settings.get('diameter', 14.5)),
                    'capacity': float(settings.get('capacity', 10.0)),
                    'refill_rate': float(sys_p.get('syringe_refill_rate', 20.0)),
                    'prime_rate': float(sys_p.get('priming_rate_ml_min', 10.0)),
                    'pump_id': _pid,
                    'wash_speed': float(settings.get('wash_speed', 15.0)),
                    'wash_count': int(settings.get('wash_count', 2)),
                    'wash_volume': float(settings.get('wash_volume', 5.0)),
                    'dead_vol_solvent': float(settings.get('tube_vol_solvent', 0.0)),
                }
                self.pumps[p_name] = ChemyxSmartPump(p_name, motor_port, sel_obj, sw_obj, spec)
            elif motor_driver == "Vapourtec" and 'VapourtecPump' in globals():
                self.pumps[p_name] = VapourtecPump(motor_port)
            elif motor_driver == "Reaxus" and 'ReaxusPump' in globals():
                self.pumps[p_name] = ReaxusPump(motor_port, name=p_name)
            elif motor_driver == "NRGSyringePump" and 'NRGSmartPump' in globals():
                # @codesyncer-decision: NRG 시퀀스 승격 (valve 모드) — 저수준 파라미터는
                #   인벤토리 item.settings, 시퀀스 파라미터는 그룹 settings + system_params.
                #   main_valve_enabled=false 면 reservoir↔system 라우팅이 불가능하므로
                #   조용한 오동작 대신 MockPump 대체 + 크게 안내 (조용한 폴백 함정 봉인).
                mset = (m_info.get("settings") or {}) if m_info else {}
                if not bool(mset.get("main_valve_enabled", False)):
                    print(f"!!! [{p_name}] NRG 시퀀스(valve 모드)는 main_valve_enabled=true 필수 — "
                          f"인벤토리 '{m_info.get('name', '?')}' settings 를 수정하세요. MockPump 로 대체합니다.")
                    self.pumps[p_name] = MockPump(p_name)
                else:
                    sys_p = self.cfg.config_data.get("system_params", {})
                    low = NRGSyringePump(
                        port=motor_port,
                        baudrate=int(mset.get("baudrate", 9600)),
                        syringe_diameter_mm=float(mset.get("syringe_diameter_mm", 4.6066)),
                        syringe_volume_ul=float(mset.get("syringe_volume_ul", 1000.0)),
                        max_flowrate_ml_min=float(mset.get("max_flowrate_ml_min", 5.0)),
                        pump_id=int(mset.get("pump_id",
                                             settings.get("pump_id", group_idx + 1))),
                        use_encoder=bool(mset.get("use_encoder", True)),
                        main_valve_enabled=True,
                        aux_valve_enabled=bool(mset.get("aux_valve_enabled", False)),
                    )
                    spec = {
                        'capacity': settings.get('capacity'),
                        'refill_rate': float(sys_p.get('syringe_refill_rate', 2.0)),
                        'prime_rate': float(sys_p.get('priming_rate_ml_min', 2.0)),
                        'wash_speed': float(settings.get('wash_speed', 2.0)),
                        'wash_count': int(settings.get('wash_count', 0)),
                        'wash_volume': settings.get('wash_volume'),
                        'dead_vol_solvent': float(settings.get('tube_vol_solvent', 0.0)),
                        # 장치 인벤토리 settings.pump_id 최우선 (Chemyx 와 동일 원칙)
                        'pump_id': int(mset.get('pump_id',
                                                settings.get('pump_id', group_idx + 1))),
                    }
                    spec = {k: v for k, v in spec.items() if v is not None}
                    self.pumps[p_name] = NRGSmartPump(p_name, low, spec)
            else:
                self.pumps[p_name] = MockPump(p_name)

            self._safe_connect(self.pumps[p_name], p_name)

        # @codesyncer(2026-08-13, 오배정 사고 가드): 공유 RS-485 버스에서 두 그룹이
        #   같은 pump_id 를 가지면 '둘 다 같은 물리 펌프'를 조용히 구동 — 치명적.
        #   중복/미설정(0)을 부팅 시 크게 경고 (fault-masking 금지).
        _chem_ids = {n: getattr(p, "pump_id", None) for n, p in self.pumps.items()
                     if type(p).__name__ == "ChemyxSmartPump"}
        _seen_ids = {}
        for _n, _i in _chem_ids.items():
            if not _i:
                print(f"  ⚠⚠ [{_n}] Chemyx pump_id 미설정(0) — RS-485 주소 없음, "
                      f"명령이 엉뚱한 펌프로 갈 수 있음. 장치 inventory settings.pump_id 등록 필요")
            elif _i in _seen_ids:
                print(f"  ⚠⚠ Chemyx pump_id 중복: [{_n}] 와 [{_seen_ids[_i]}] 가 모두 ID {_i} — "
                      f"같은 물리 펌프를 구동하게 됨! config 확인 필수")
            else:
                _seen_ids[_i] = _n

        # 4. Fraction Collector
        c_conf = self.cfg.config_data["roles"].get("collector", {})
        c_info = self.cfg.get_device_info(c_conf.get("driver_id"))

        if c_info:
            collector_driver = HardwareFactory.get_driver_type(c_info.get("driver"))
            if collector_driver == "ColosseumCollector":
                self.collector = ColosseumCollector()
                try:
                    _, msg = self.collector.connect(c_info["port"])
                    print(f"[Collector] {msg}")
                except Exception as e:
                    print(f"  - Collector 연결 실패 (무시): {e}")
                    self.collector = MockCollector()
                    self.collector.connect("Mock_Port")
            elif collector_driver == "Plate96Collector":
                # @codesyncer-decision(2026-08-05 랙 확장): settings.rack_type 으로
                #   좌표 파일 선택 — "plate96"(기본) | "eppendorf_5x5" 등.
                #   랙별 좌표는 tools/generate_rack_coords.py 가 생성 (기존 티칭 재사용).
                _c_set = c_info.get("settings", {}) or {}
                _rack = str(_c_set.get("rack_type", "plate96") or "plate96")
                _cfile = (None if _rack == "plate96"
                          else f"well_coordinates_{_rack}.json")
                try:
                    self.collector = Plate96Collector(coords_file=_cfile)
                    if self.collector.total_tubes <= 0:
                        raise RuntimeError(
                            f"랙 '{_rack}' 좌표 로드 실패 — "
                            f"tools/generate_rack_coords.py 로 생성했는지 확인")
                    _, msg = self.collector.connect(c_info["port"])
                    print(f"[Collector] {msg} (rack={_rack})")
                except Exception as e:
                    print(f"  - Plate96 연결 실패 (무시): {e}")
                    self.collector = MockCollector()
                    self.collector.connect("Mock_Port")
            else:
                self.collector = MockCollector()
                self.collector.connect("Mock_Port")
        else:
            self.collector = MockCollector()
            self.collector.connect("Mock_Port")

        # 5. Push Pump (optional) — injection 후 반응기 push 전용
        # @codesyncer-decision: role.pumps와 별도 슬롯. driver_id 없으면 None으로 두어
        #   엔진이 legacy syringe push 경로로 폴백할 수 있게 한다.
        pp_conf = self.cfg.config_data.get("roles", {}).get("push_pump", {}) or {}
        pp_info = self.cfg.get_device_info(pp_conf.get("driver_id")) if pp_conf.get("driver_id") else None
        self.push_pump = None
        if pp_info:
            pp_driver = HardwareFactory.get_driver_type(pp_info.get("driver"))
            pp_port = pp_info.get("port", "COM_Mock")
            if pp_driver == "Reaxus" and 'ReaxusPump' in globals():
                self.push_pump = ReaxusPump(pp_port, name="PushPump")
            elif pp_driver == "Vapourtec" and 'VapourtecPump' in globals():
                self.push_pump = VapourtecPump(pp_port)
            else:
                # 드라이버 매칭 실패 시 Mock 폴백 (연결되지 않은 상태로 존재)
                self.push_pump = MockPump("PushPump")
            self._safe_connect(self.push_pump, "PushPump")

        # 5-1. NRG Manual Syringe Pumps (역할에 속하지 않은 수동 시린지 펌프)
        # @codesyncer-decision: roles.manual_pumps 는 list of driver_id. 시퀀스 엔진은 건드리지 않고
        #   UI/tab_manual 에서 직접 조작용. NRG 시린지펌프 = 별도 채널.
        manual_pump_ids = self.cfg.config_data.get("roles", {}).get("manual_pumps", []) or []
        for mp_id in manual_pump_ids:
            mp_info = self.cfg.get_device_info(mp_id) if mp_id else None
            if not mp_info:
                continue
            mp_driver = HardwareFactory.get_driver_type(mp_info.get("driver"))
            mp_name = mp_info.get("name") or mp_id
            mp_port = mp_info.get("port", "COM_Mock")
            mp_settings = mp_info.get("settings", {}) or {}
            if mp_driver == "NRGSyringePump" and 'NRGSyringePump' in globals():
                self.manual_pumps[mp_name] = NRGSyringePump(
                    port=mp_port,
                    baudrate=int(mp_settings.get("baudrate", 9600)),
                    syringe_diameter_mm=float(mp_settings.get("syringe_diameter_mm", 4.6066)),
                    syringe_volume_ul=float(mp_settings.get("syringe_volume_ul", 1000.0)),
                    max_flowrate_ml_min=float(mp_settings.get("max_flowrate_ml_min", 5.0)),
                    pump_id=int(mp_settings.get("pump_id", 0)),
                    use_encoder=bool(mp_settings.get("use_encoder", True)),
                    main_valve_enabled=bool(mp_settings.get("main_valve_enabled", False)),
                    aux_valve_enabled=bool(mp_settings.get("aux_valve_enabled", False)),
                )
            else:
                # 폴백: 가상 NRG 펌프 (정의 안된 드라이버여도 안전하게 진행)
                if 'MockNRGSyringePump' in globals():
                    self.manual_pumps[mp_name] = MockNRGSyringePump(name=mp_name, port=mp_port)
                else:
                    self.manual_pumps[mp_name] = MockPump(mp_name)
            self._safe_connect(self.manual_pumps[mp_name], mp_name)

        # 5-2. Cartesian Samplers (전단 시약 픽업)
        # @codesyncer-decision: roles.samplers 는 list of driver_id (독립 운용 샘플러).
        #   그룹의 drivers.sampler 슬롯에 배정된 샘플러는 5-3 에서 자동 인스턴스화 —
        #   두 목록에 중복 등록할 필요 없음 (펌프+샘플러 = 물리적 일체형).
        sampler_ids = self.cfg.config_data.get("roles", {}).get("samplers", []) or []
        for sp_id in sampler_ids:
            sp_name, sampler = self._instantiate_sampler(sp_id)
            if sampler is not None:
                self.samplers[sp_name] = sampler

        # 5-3. 그룹별 오토샘플러 해석 — cfg.PUMP_SAMPLER_MAP 소비
        # @codesyncer-decision: NRG 펌프+샘플러는 물리적 일체형(리퀴드 핸들러) —
        #   Setting 에서 그룹에 sampler 를 배정하는 것만으로 완결되어야 한다.
        #   roles.samplers 에 이미 있으면 그 인스턴스를 공유하고, 없으면 여기서
        #   자동 인스턴스화해 self.samplers 에도 등록 (E-Stop/cleanup/Manual 카드
        #   경로가 모두 self.samplers 를 순회하므로 등록이 안전의 전제).
        self.sampler_by_pump = {}
        for p_name, sp_id in (getattr(self.cfg, "PUMP_SAMPLER_MAP", {}) or {}).items():
            sp_info = self.cfg.get_device_info(sp_id) if sp_id else None
            sp_name = (sp_info or {}).get("name") or sp_id
            sp_obj = self.samplers.get(sp_name)
            if sp_obj is None:
                sp_name2, sp_obj = self._instantiate_sampler(sp_id)
                if sp_obj is not None:
                    self.samplers[sp_name2] = sp_obj
                    print(f"  - [{p_name}] 일체형 샘플러 자동 인스턴스화: {sp_name2}")
            if sp_obj is None:
                print(f"!!! [{p_name}] autosampler 지정({sp_id})이지만 인벤토리에서 "
                      f"생성 실패 — 니들 조율 비활성 (internal_valve 동작)")
            else:
                self.sampler_by_pump[p_name] = sp_obj
                print(f"  - [{p_name}] autosampler 조율 활성: {sp_name}")

        # 5-4. HTE 질소 MFC — roles.gas.driver_id (선택, MKP RS485 ASCII)
        self.mfc = None
        gas_id = ((self.cfg.config_data.get("roles", {}) or {})
                  .get("gas", {}) or {}).get("driver_id")
        if gas_id and gas_id != "None":
            g_info = self.cfg.get_device_info(gas_id) or {}
            try:
                from hardware.gas.mfc_korea_mkp import MFCKoreaMKP
                g_set = g_info.get("settings", {}) or {}
                # slave_addr: 신규 키. 구 config 의 modbus_addr 도 back-compat 로 읽음.
                # @codesyncer-decision(2026-08-05): 기본 주소 1→0 — 실기 테스트된
                #   벤더 드라이버(MFC_Driver.zip)가 device_id=0x00 으로 검증됨.
                #   다른 주소의 장비는 settings.slave_addr 로 명시.
                _addr = g_set.get("slave_addr", g_set.get("modbus_addr", 0))
                self.mfc = MFCKoreaMKP(
                    g_info.get("port"),
                    slave_addr=int(_addr or 0),
                    baudrate=int(g_set.get("baudrate", 9600) or 9600),
                    max_sccm=float(g_set.get("max_sccm", 100.0) or 100.0),
                    name=g_info.get("name", "MFC"))
                self.mfc.connect()
                print(f"  - MFC(N2) 연결: {g_info.get('name')} @ {g_info.get('port')}")
            except Exception as e:
                print(f"!!! MFC 초기화 실패: {e}")
                self.mfc = None

        # 5-5. 위상센서 어레이 — roles.phase.driver_id (선택, RoboChem OCB350 스택)
        # @codesyncer: HTE droplet 수집 경계의 실측 트리거용. settings.sensors =
        #   {논리명: 어레이인덱스} (기본 {"collect": 0} = 아웃렛 직전 1개).
        self.phase_sensor = None
        ph_id = ((self.cfg.config_data.get("roles", {}) or {})
                 .get("phase", {}) or {}).get("driver_id")
        if ph_id and ph_id != "None":
            p_info = self.cfg.get_device_info(ph_id) or {}
            try:
                from hardware.sensors.phase_sensor_array import (
                    PhaseSensorArrayHW, MockPhaseSensor)
                p_set = p_info.get("settings", {}) or {}
                drv = HardwareFactory.get_driver_type(p_info.get("driver"))
                if drv == "PhaseSensorOPBADC":
                    # OPB 2ch ADC 스트림 (Photo_Interrupt.zip 벤더 리그 이식) —
                    # settings: sensors / thresholds({채널|논리명: adc}) / baudrate
                    from hardware.sensors.phase_sensor_opb import PhaseSensorOPBADC
                    self.phase_sensor = PhaseSensorOPBADC(
                        p_info.get("port"),
                        sensors=p_set.get("sensors"),
                        thresholds=p_set.get("thresholds"),
                        baudrate=int(p_set.get("baudrate", 115200) or 115200),
                        name=p_info.get("name", "OPBSensors"))
                else:
                    cls = (MockPhaseSensor if drv == "MockPhaseSensor"
                           else PhaseSensorArrayHW)
                    self.phase_sensor = cls(
                        p_info.get("port"),
                        sensors=p_set.get("sensors"),
                        name=p_info.get("name", "PhaseSensors"))
                self.phase_sensor.connect()
                print(f"  - 위상센서 연결: {p_info.get('name')} @ {p_info.get('port')}")
            except Exception as e:
                print(f"!!! 위상센서 초기화 실패: {e}")
                self.phase_sensor = None

        # 5-6. 초음파 레벨센서(HC-SR04) — roles.pumps[].drivers.level (선택, startup 잔량)
        # @codesyncer-decision: RoboChem SI 3.3.1 이식. 펌프마다 UNO 1대(각 COM 포트).
        #   config.PUMP_LEVEL_CFG(펌프별 device_id+캘리브+정책)로 채널맵 구성. 채널별
        #   mock 플래그(드라이버=Mock* 또는 포트에 "Mock")로 실기/시뮬 혼재 허용.
        #   초기화 실패는 조용히 None 폴백 → 엔진이 '센서 없음'으로 기존 동작 유지.
        self.level_sensor = None
        level_cfg = getattr(self.cfg, "PUMP_LEVEL_CFG", {}) or {}
        if level_cfg:
            try:
                from hardware.sensors.ultrasonic_level import UltrasonicLevelSensor
                channels = {}
                for p_name, lc in level_cfg.items():
                    info = self.cfg.get_device_info(lc.get("device_id")) or {}
                    drv = HardwareFactory.get_driver_type(info.get("driver"))
                    is_mock = (drv == "MockUltrasonicLevelSensor") or ("Mock" in str(info.get("port")))
                    channels[p_name] = {
                        "port": info.get("port"),
                        "baud": int((info.get("settings", {}) or {}).get("baudrate", 9600) or 9600),
                        "slope": lc.get("slope", 1.0),
                        "intercept": lc.get("intercept", 0.0),
                        "samples": lc.get("samples", 20),
                        "index": lc.get("index", 0),
                        "mock": is_mock,
                    }
                self.level_sensor = UltrasonicLevelSensor(channels, name="LevelSensors")
                self.level_sensor.connect()
                print(f"  - 레벨센서 연결: 채널 {list(channels)}")
            except Exception as e:
                print(f"!!! 레벨센서 초기화 실패: {e}")
                self.level_sensor = None

        # 6. Safety & Engine
        self.safety = SafetyManager(self.cfg, self.pumps, self.heater)
        self.engine = StrictSequenceEngine(
            self.cfg, self.pumps, self.valves, self.heater,
            self.safety, self.signals, self.collector,
            push_pump=self.push_pump,
            samplers=self.sampler_by_pump,
            mfc=self.mfc,
            phase_sensor=self.phase_sensor,
            level_sensor=self.level_sensor,
        )
        self.calculator = FlowCalculator(self.cfg)

    def _instantiate_sampler(self, sp_id):
        """인벤토리 id → 샘플러 인스턴스 (Mock 폴백). (name, obj|None) 반환."""
        sp_info = self.cfg.get_device_info(sp_id) if sp_id else None
        if not sp_info:
            return None, None
        sp_driver = HardwareFactory.get_driver_type(sp_info.get("driver"))
        sp_name = sp_info.get("name") or sp_id
        sp_port = sp_info.get("port", "COM_Mock")
        sp_settings = sp_info.get("settings", {}) or {}
        sampler = None
        if sp_driver == "GrblCartesianSampler" and 'GrblCartesianSampler' in globals():
            sampler = GrblCartesianSampler(
                name=sp_name,
                positions_file=sp_settings.get("positions_file"),
                safe_z=float(sp_settings.get("safe_z", -2.0)),
                vials_top_z=float(sp_settings.get("vials_top_z", -25.0)),
                needle_depth_mm=float(sp_settings.get("needle_depth_mm", 20.0)),
                feedrate_xy=float(sp_settings.get("feedrate_xy", 2000.0)),
                feedrate_z=float(sp_settings.get("feedrate_z", 600.0)),
                aux_needle_on_code=sp_settings.get("aux_needle_on_code", "M3"),
                aux_needle_off_code=sp_settings.get("aux_needle_off_code", "M5"),
            )
            try:
                ok, msg = sampler.connect(sp_port)
                print(f"[Sampler] {sp_name}: {msg}")
                if not ok and 'MockCartesianSampler' in globals():
                    sampler = MockCartesianSampler(name=sp_name)
                    sampler.connect("Mock_Port")
            except Exception as e:
                print(f"  - Sampler {sp_name} 연결 실패 (Mock 폴백): {e}")
                if 'MockCartesianSampler' in globals():
                    sampler = MockCartesianSampler(name=sp_name)
                    sampler.connect("Mock_Port")
        else:
            if 'MockCartesianSampler' in globals():
                sampler = MockCartesianSampler(name=sp_name)
                sampler.connect("Mock_Port")
        return sp_name, sampler

    def _cleanup_mfc(self):
        if getattr(self, "mfc", None) is not None:
            try:
                self.mfc.disconnect()
            except Exception:
                pass

    def _cleanup_phase_sensor(self):
        if getattr(self, "phase_sensor", None) is not None:
            try:
                self.phase_sensor.disconnect()
            except Exception:
                pass

    def cleanup_hardware(self):
        self._cleanup_mfc()
        self._cleanup_phase_sensor()
        # 초음파 레벨센서 시리얼 해제 (자체 관리 — _serial_registry 미사용)
        if getattr(self, "level_sensor", None) is not None:
            try:
                self.level_sensor.disconnect()
            except Exception:
                pass
        """
        기존 하드웨어 연결을 안전하게 정리합니다.
        @codesyncer-decision: Hot Reload 지원을 위한 연결 해제 로직
        주의: 모니터링 워커 중지는 호출자(main.py)가 먼저 처리해야 합니다.
        """
        print(">>> 하드웨어 연결 해제 중...")

        # 1. 실행 중인 시퀀스 중지
        if self.engine:
            if hasattr(self.engine, 'abort_flag'):
                self.engine.abort_flag = True
            if hasattr(self.engine, 'pause_event'):
                self.engine.pause_event.set()

        # 2. 펌프 정지 및 연결 해제
        for name, pump in self.pumps.items():
            try:
                if hasattr(pump, 'stop'): pump.stop()
                if hasattr(pump, 'disconnect'): pump.disconnect()
            except Exception as e:
                print(f"  - {name} 해제 오류: {e}")

        # 2-1. Push pump 정지 및 해제
        if self.push_pump is not None:
            try:
                if hasattr(self.push_pump, 'stop'): self.push_pump.stop()
                if hasattr(self.push_pump, 'disconnect'): self.push_pump.disconnect()
            except Exception as e:
                print(f"  - PushPump 해제 오류: {e}")

        # 2-2. Manual pumps (NRG 등) 정지 및 해제
        # @codesyncer-decision: pumps 와 동일 방식 — 시리얼 레지스트리는 아래에서 일괄 정리
        for name, mp in self.manual_pumps.items():
            try:
                if hasattr(mp, 'stop'): mp.stop()
                if hasattr(mp, 'disconnect'): mp.disconnect()
            except Exception as e:
                print(f"  - ManualPump {name} 해제 오류: {e}")

        # 3. 밸브 연결 해제
        for name, valve in self.valves.items():
            try:
                if hasattr(valve, 'disconnect'): valve.disconnect()
            except Exception as e:
                print(f"  - {name} 해제 오류: {e}")

        # 3-1. 공유 시리얼 포트 레지스트리 정리 (Hot Reload 대응)
        # @codesyncer-decision: ArduinoValve/RunzeSV07Valve는 _serial_registry 싱글톤 사용
        #   → disconnect()만으로는 레지스트리 미정리 → 재연결 시 "포트 열림" 오류 발생
        #   → Hot Reload 시 반드시 레지스트리까지 수동 정리 필요
        # @codesyncer-decision: NRGSyringePump / GrblCartesianSampler 도 동일 패턴 → 추가
        registry_classes = [ChemyxPump]
        if 'ArduinoValve' in globals():
            registry_classes.append(ArduinoValve)
        # ESP32EthValve 의 registry 값은 _TcpLink (is_open/close 시리얼 호환 계약)
        if 'ESP32EthValve' in globals():
            registry_classes.append(ESP32EthValve)
        if 'RunzeSV07Valve' in globals():
            registry_classes.append(RunzeSV07Valve)
        if 'NRGSyringePump' in globals():
            registry_classes.append(NRGSyringePump)
        if 'GrblCartesianSampler' in globals():
            registry_classes.append(GrblCartesianSampler)
        if registry_classes:
            for registry_class in registry_classes:
                for port, ser in list(registry_class._serial_registry.items()):
                    try:
                        if ser and ser.is_open:
                            ser.close()
                            print(f"  - {registry_class.__name__} 포트 {port} 닫힘")
                    except Exception as e:
                        print(f"  - 포트 {port} 닫기 오류: {e}")
                registry_class._serial_registry.clear()
                registry_class._lock_registry.clear()

        # 4. 히터 정지 및 연결 해제
        if self.heater:
            try:
                if hasattr(self.heater, 'stop'): self.heater.stop()
                if hasattr(self.heater, 'disconnect'): self.heater.disconnect()
            except Exception as e:
                print(f"  - Heater 해제 오류: {e}")

        # 5. 컬렉터 정지 및 연결 해제
        if self.collector:
            try:
                if hasattr(self.collector, 'disconnect'): self.collector.disconnect()
            except Exception as e:
                print(f"  - Collector 해제 오류: {e}")

        # 6. 샘플러 정지 및 연결 해제
        for name, sampler in self.samplers.items():
            try:
                if hasattr(sampler, 'emergency_stop'): sampler.emergency_stop()
                if hasattr(sampler, 'disconnect'): sampler.disconnect()
            except Exception as e:
                print(f"  - Sampler {name} 해제 오류: {e}")

        print(">>> 하드웨어 연결 해제 완료")
