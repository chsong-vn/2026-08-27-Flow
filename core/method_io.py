"""
Method I/O Manager - 메서드 JSON 저장/로드
@codesyncer-decision: main.py에서 분리 - Method 파일 I/O 단일 책임

사용법:
    method_io = MethodIO(app)
    btn_save.clicked.connect(method_io.on_save_method)       # 현재 파일에 덮어쓰기
    btn_save_as.clicked.connect(method_io.on_save_method_as) # 새 위치에 저장
    btn_load.clicked.connect(method_io.on_load_method)
"""

import os
import json
import shutil
from datetime import datetime

from PyQt5.QtWidgets import (QFileDialog, QMessageBox, QTableWidgetItem)
from PyQt5.QtCore import Qt


# @codesyncer-decision: 윈도우 베이스 타이틀 상수
#   main.py의 setWindowTitle 값과 일치해야 한다.
#   Save/Load 시 "<base> — <filename>" 형태로 갱신되며, base만 한 곳에서 관리.
WINDOW_BASE_TITLE = "VORONOI Flowchemistry Platform"


class MethodIO:
    """메서드 JSON 파일의 저장/로드를 담당합니다."""

    def __init__(self, app):
        self.app = app
        # @codesyncer-decision: 마지막 저장/로드 경로 추적
        #   Save 버튼이 매번 파일 다이얼로그를 띄우는 불편 해결.
        #   current_path가 있으면 즉시 덮어쓰기, 없으면 Save As로 폴백.
        self.current_path = None

    def on_save_method(self):
        """현재 메서드 파일에 즉시 덮어쓰기. 경로가 없으면 Save As로 폴백."""
        if self.current_path and os.path.isfile(self.current_path):
            try:
                self._write_method_to(self.current_path)
            except Exception as e:
                QMessageBox.critical(self.app, "저장 실패", str(e))
        else:
            self.on_save_method_as()

    def on_save_method_as(self):
        """
        파일 다이얼로그를 띄워 새 위치(또는 새 이름)에 메소드 저장.

        @codesyncer-decision: Method 파일에 hardware_config 포함
          - 목적: 전체 시스템 구성 재현 가능
          - 포함 항목: inventory (장비 목록), roles (펌프 그룹 구성)
          - 로드 시: 현재 설정과 비교 후 사용자 확인 다이얼로그 표시

        @codesyncer-decision: sequence_data 직접 저장
          - 새로운 QTabWidget 구조에서는 중앙 데이터 모델(sequence_data) 사용
          - 3개 테이블(temp_time, port_equiv, fraction) → sequence_data로 통합
        """
        app = self.app
        # @codesyncer-decision: Windows 네이티브 다이얼로그 사용
        # 기존 경로가 있으면 같은 디렉토리/파일명을 초기값으로 제공
        initial = self.current_path or ""
        path, _ = QFileDialog.getSaveFileName(
            app, "메소드 다른 이름으로 저장", initial, "Flow Method Files (*.json)"
        )
        if not path:
            return

        try:
            self._write_method_to(path)
            self.current_path = path
            self._update_window_title()
        except Exception as e:
            QMessageBox.critical(app, "저장 실패", str(e))

    def _write_method_to(self, path):
        """
        실제 메소드 JSON 직렬화 및 파일 쓰기.

        @codesyncer-decision: 덮어쓰기 안전망 — 기존 파일을 .bak으로 백업
          - 확인 다이얼로그 없이 즉시 저장하는 대신, 직전 버전을 .bak으로 보존
          - 백업 실패는 경고 로그만 남기고 저장 자체는 진행
        """
        app = self.app

        # 기존 파일이 있으면 .bak으로 백업
        if os.path.isfile(path):
            try:
                shutil.copy2(path, path + ".bak")
            except Exception as e:
                print(f"[MethodIO] .bak 백업 실패 (무시): {e}")

        # hardware_config에서 임시 필드 제거 후 저장
        clean_inventory = []
        for item in app.cfg.config_data.get("inventory", []):
            clean_item = {k: v for k, v in item.items() if not k.startswith("_")}
            clean_inventory.append(clean_item)

        # 현재 연결된 하드웨어 상태 스냅샷 생성
        hw_status = self._build_hardware_status(app)

        data = {
            "version": "8.1",  # 하드웨어 상태 포함 버전
            "timestamp": str(datetime.now()),
            "hardware_config": {
                "inventory": clean_inventory,
                "roles": app.cfg.config_data.get("roles", {})
            },
            "hardware_status": hw_status,
            "system_params": app.cfg.config_data.get("system_params", {}),
            "reagents": app.map_mgr.inlet_map if app.map_mgr else {},
            "sequence": []
        }

        # sequence_data에서 직접 저장
        for step_data in app.seq_tab.sequence_data:
            row_data = {
                "temp": step_data['temp'],
                "rt": step_data['rt'],
                "target_vol": step_data['vol'],
                "vol_per_tube": step_data['tube_vol'],
                "pump_settings": {}
            }

            for pump_name, pump_data in step_data['pumps'].items():
                row_data["pump_settings"][pump_name] = {
                    "port": pump_data['port'],
                    "eq": pump_data['eq'],
                    "flow": str(pump_data['flow']) if pump_data['flow'] > 0 else "-"
                }

            data["sequence"].append(row_data)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        if app.log_browser:
            app.signals.sig_log.emit(f"메소드 저장됨: {path}")
        QMessageBox.information(app, "저장 완료", f"메소드가 저장되었습니다.\n{os.path.basename(path)}")

    def _update_window_title(self):
        """윈도우 타이틀에 현재 메서드 파일명을 표시."""
        if self.current_path:
            filename = os.path.basename(self.current_path)
            self.app.setWindowTitle(f"{WINDOW_BASE_TITLE} — {filename}")
        else:
            self.app.setWindowTitle(WINDOW_BASE_TITLE)

    def _build_hardware_status(self, app):
        """
        현재 시스템에 연결된 하드웨어 구성 스냅샷을 생성한다.

        포함 내용:
          - connected_devices: 실제 연결된 장비 목록 (Mock 제외)
          - pump_mapping: 각 펌프 그룹의 모터/셀렉터/스위처 매핑 상세
          - heater/outlet/collector: 역할별 장비 정보
        """
        from hardware.factory import HardwareFactory

        status = {
            "connected_devices": [],
            "pump_mapping": [],
            "heater": None,
            "outlet_valve": None,
            "collector": None,
        }

        # --- 장비 인벤토리에서 ID → 이름/드라이버/포트 조회 헬퍼 ---
        def _device_summary(dev_id):
            info = app.cfg.get_device_info(dev_id)
            if not info:
                return None
            return {
                "id": dev_id,
                "name": info.get("name", ""),
                "driver": info.get("driver", ""),
                "driver_type": HardwareFactory.get_driver_type(info.get("driver", "")),
                "port": info.get("port", ""),
                "address": info.get("address"),
                "channel": info.get("channel"),
            }

        def _is_mock(obj):
            if obj is None:
                return True
            cls = type(obj).__name__
            return "Mock" in cls or "Virtual" in cls

        # --- 펌프 그룹 매핑 ---
        pumps_conf = app.cfg.config_data.get("roles", {}).get("pumps", [])
        for p_role in pumps_conf:
            p_name = p_role.get("name", "")
            drivers = p_role.get("drivers", {})
            settings = p_role.get("settings", {})

            motor_info = _device_summary(drivers.get("motor"))
            selector_info = _device_summary(drivers.get("selector"))
            switcher_info = _device_summary(drivers.get("switcher"))

            # 실제 연결 여부 확인
            pump_obj = app.pumps.get(p_name)
            sel_key = f"{p_name}_Selector"
            sw_key = f"{p_name}_Switcher"
            sel_obj = app.valves.get(sel_key)
            sw_obj = app.valves.get(sw_key)

            mapping = {
                "group_name": p_name,
                "position": p_role.get("position", "inlet"),
                "motor": {
                    "device": motor_info,
                    "connected": not _is_mock(pump_obj),
                    "actual_class": type(pump_obj).__name__ if pump_obj else None,
                },
                "selector": {
                    "device": selector_info,
                    "connected": not _is_mock(sel_obj),
                    "actual_class": type(sel_obj).__name__ if sel_obj else None,
                },
                "switcher": {
                    "device": switcher_info,
                    "connected": not _is_mock(sw_obj),
                    "actual_class": type(sw_obj).__name__ if sw_obj else None,
                },
                "settings": {
                    "pump_id": settings.get("pump_id"),
                    "diameter": settings.get("diameter"),
                    "capacity": settings.get("capacity"),
                    "tube_vol_solvent": settings.get("tube_vol_solvent"),
                    "tube_vol_reagent": settings.get("tube_vol_reagent"),
                },
            }
            status["pump_mapping"].append(mapping)

            # connected_devices에 실제 연결된 것만 추가
            if motor_info and not _is_mock(pump_obj):
                status["connected_devices"].append(motor_info)
            if selector_info and not _is_mock(sel_obj):
                status["connected_devices"].append(selector_info)
            if switcher_info and not _is_mock(sw_obj):
                status["connected_devices"].append(switcher_info)

        # --- 히터 ---
        h_conf = app.cfg.config_data.get("roles", {}).get("heater", {})
        h_info = _device_summary(h_conf.get("driver_id"))
        status["heater"] = {
            "device": h_info,
            "connected": not _is_mock(app.heater),
            "actual_class": type(app.heater).__name__ if app.heater else None,
        }
        if h_info and not _is_mock(app.heater):
            status["connected_devices"].append(h_info)

        # --- 아울렛 밸브 ---
        o_conf = app.cfg.config_data.get("roles", {}).get("outlet", {})
        o_info = _device_summary(o_conf.get("driver_id"))
        outlet_obj = app.valves.get("Outlet")
        status["outlet_valve"] = {
            "device": o_info,
            "connected": not _is_mock(outlet_obj),
            "actual_class": type(outlet_obj).__name__ if outlet_obj else None,
        }
        if o_info and not _is_mock(outlet_obj):
            status["connected_devices"].append(o_info)

        # --- 분획 수집기 ---
        c_conf = app.cfg.config_data.get("roles", {}).get("collector", {})
        c_info = _device_summary(c_conf.get("driver_id"))
        status["collector"] = {
            "device": c_info,
            "connected": not _is_mock(app.collector),
            "actual_class": type(app.collector).__name__ if app.collector else None,
        }
        if c_info and not _is_mock(app.collector):
            status["connected_devices"].append(c_info)

        # 요약 카운트
        status["summary"] = {
            "total_devices_configured": len(app.cfg.config_data.get("inventory", [])),
            "total_devices_connected": len(status["connected_devices"]),
            "pump_groups": len(pumps_conf),
            "active_pumps": list(app.cfg.ACTIVE_PUMPS),
        }

        return status

    def on_load_method(self):
        """
        메소드 불러오기 (원래 레이아웃 - self.app.stbl 사용)

        @codesyncer-decision: hardware_config 로드 시 사용자 확인
          - Method 파일에 hardware_config가 있으면 현재 설정과 비교
          - 다르면 "하드웨어 구성 적용" 확인 다이얼로그 표시
          - 승인 시 hardware_config.json 업데이트 + Hot Reload
        """
        app = self.app
        if not app.map_mgr:
            return
        # @codesyncer-decision: Windows 네이티브 다이얼로그 사용
        path, _ = QFileDialog.getOpenFileName(
            app, "메소드 불러오기", "", "Flow Method Files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # [NEW] hardware_config 로드 처리
            hw_config = data.get("hardware_config")
            if hw_config:
                current_roles = app.cfg.config_data.get("roles", {})
                loaded_roles = hw_config.get("roles", {})

                # 펌프 그룹 구성 비교
                current_pump_names = [p["name"] for p in current_roles.get("pumps", [])]
                loaded_pump_names = [p["name"] for p in loaded_roles.get("pumps", [])]

                if current_pump_names != loaded_pump_names:
                    reply = QMessageBox.question(
                        app, "하드웨어 구성 변경 감지",
                        f"Method 파일의 펌프 구성이 현재와 다릅니다.\n\n"
                        f"현재: {', '.join(current_pump_names) or '없음'}\n"
                        f"Method: {', '.join(loaded_pump_names) or '없음'}\n\n"
                        f"Method 파일의 하드웨어 구성을 적용하시겠습니까?\n"
                        f"(적용 시 앱이 하드웨어를 재연결합니다)",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )

                    if reply == QMessageBox.Yes:
                        # hardware_config.json 업데이트
                        app.cfg.save_config(
                            hw_config.get("inventory", []),
                            hw_config.get("roles", {}),
                            data.get("system_params")
                        )
                        # Hot Reload
                        app.reload_hardware()
                        app.signals.sig_log.emit("[Method] 하드웨어 구성 적용 완료")

            # @codesyncer-inference: 하위 호환성 로직 - Pump_X → Group_X 자동 변환
            pump_to_group_map = {
                "Pump_1": "Group_A",
                "Pump_2": "Group_B",
                "Pump_3": "Group_C",
                "Pump_4": "Group_D",
                "Pump_5": "Group_E"
            }

            # 시약 데이터 변환 및 적용
            reagents = data.get("reagents", {})
            converted_reagents = {}
            for old_name, ports_data in reagents.items():
                new_name = pump_to_group_map.get(old_name, old_name)
                converted_reagents[new_name] = ports_data
                if old_name != new_name:
                    print(f"[Compatibility] Converted {old_name} → {new_name}")

            # @codesyncer-decision: JSON 키는 항상 문자열이므로 정수 키로 변환 필요
            for pump_name in converted_reagents:
                converted_reagents[pump_name] = {
                    int(k): v for k, v in converted_reagents[pump_name].items()
                }
            app.map_mgr.inlet_map = converted_reagents

            # 시약 테이블 업데이트
            for p_name, ports_data in app.map_mgr.inlet_map.items():
                if p_name in app.reagent_tables:
                    tbl = app.reagent_tables[p_name]
                    tbl.blockSignals(True)
                    for port_str, info in ports_data.items():
                        try:
                            r_idx = int(port_str) - 1
                            if 0 <= r_idx < tbl.rowCount():
                                tbl.setItem(r_idx, 1, QTableWidgetItem(str(info['name'])))
                                # @codesyncer-decision: 농도 셀은 읽기전용 유지
                                conc_item = QTableWidgetItem(str(info['conc']))
                                conc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                                tbl.setItem(r_idx, 2, conc_item)
                                # SMILES 셀 복원 (구버전 메서드파일엔 없을 수 있음)
                                smi_item = QTableWidgetItem(str(info.get('smiles', '') or ''))
                                if 0 < r_idx < 11:
                                    smi_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                                else:
                                    smi_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                                tbl.setItem(r_idx, 3, smi_item)
                        except:
                            pass
                    tbl.blockSignals(False)

            # @codesyncer-decision: StepCard 기반 시퀀스 로드
            # 3-tab(temp_time/port_equiv/result) → StepCard 통합 구조
            seq_tab = app.seq_tab
            seq = data.get("sequence", [])

            # 기존 데이터 초기화
            seq_tab.clear_all_steps()

            for step_idx, r_data in enumerate(seq):
                # pump_settings 키 변환
                ps_raw = r_data.get("pump_settings", {})
                ps_converted = {}
                for old_name, settings in ps_raw.items():
                    new_name = pump_to_group_map.get(old_name, old_name)
                    ps_converted[new_name] = settings

                # add_step() 호출 (기본값으로 StepCard 생성)
                seq_tab.add_step()

                # StepCard 위젯으로 직접 값 설정
                card = seq_tab.step_cards[step_idx]
                step_data = seq_tab.sequence_data[step_idx]

                # 반응 조건 (온도/체류시간/반응량/분획부피)
                step_data['temp'] = float(r_data.get("temp", 25.0))
                step_data['rt'] = float(r_data.get("rt", 10.0))
                step_data['vol'] = float(r_data.get("target_vol", 5.0))
                step_data['tube_vol'] = float(r_data.get("vol_per_tube", 1.5))

                card.sp_temp.setValue(step_data['temp'])
                card.sp_rt.setValue(step_data['rt'])
                card.sp_vol.setValue(step_data['vol'])
                card.sp_tube.setValue(step_data['tube_vol'])

                # 펌프별 설정 (포트/당량/유속)
                for p in app.cfg.INLET_PUMPS:
                    ps = ps_converted.get(p, {"port": 2, "eq": 1.0, "flow": "-"})
                    step_data['pumps'][p]['port'] = ps.get("port", 2)
                    step_data['pumps'][p]['eq'] = float(ps.get("eq", 1.0))

                    # flow 값 파싱
                    flow_str = str(ps.get("flow", "-"))
                    try:
                        step_data['pumps'][p]['flow'] = float(flow_str)
                    except:
                        step_data['pumps'][p]['flow'] = 0.0

                    # StepCard 펌프 위젯 업데이트
                    pw = card.pump_widgets.get(p)
                    if pw:
                        # Port 콤보박스
                        cb = pw['cb_port']
                        port_val = step_data['pumps'][p]['port']
                        idx = cb.findData(port_val)
                        if idx >= 0:
                            cb.blockSignals(True)
                            cb.setCurrentIndex(idx)
                            cb.blockSignals(False)

                        # 당량 스핀박스
                        pw['sp_eq'].setValue(step_data['pumps'][p]['eq'])

            # system_params 적용
            loaded_sys_params = data.get("system_params", {})
            if loaded_sys_params:
                sp = app.cfg.config_data.get("system_params", {})
                for key, value in loaded_sys_params.items():
                    sp[key] = value

                app.cfg.process_config()
                print(f"[Method Load] system_params 적용됨: reactor_vol={app.cfg.reactor_vol:.4f} mL")

            if app.log_browser:
                app.signals.sig_log.emit(f"메소드 불러옴: {path}")

            # @codesyncer-decision: Load 성공 시 current_path 갱신
            #   이후 Save 버튼이 즉시 이 파일에 덮어쓰도록 한다.
            self.current_path = path
            self._update_window_title()

            QMessageBox.information(app, "불러오기 완료", f"메소드가 적용되었습니다.\n{os.path.basename(path)}")

        except Exception as e:
            QMessageBox.critical(app, "불러오기 실패", f"오류: {str(e)}")
