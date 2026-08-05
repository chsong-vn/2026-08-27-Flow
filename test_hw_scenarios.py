"""장비 구성 변경 시나리오 검증 (offscreen)

시나리오 (SCENARIO 환경변수):
  reaxus2   — 펌프 2개, 모터를 연동 펌프(Reaxus)로 교체
  novalve   — Group A에서 12-way/3-way 밸브 제거
  colosseum — 컬렉터를 Colosseum으로 (포트 실패 시 Mock 폴백 포함)
  nopush    — push_pump role 제거
  onepump   — 펌프 1개만

각 시나리오에서 Manual/Sequence 탭이 구성에 맞게 적응하는지 어서션.
hardware_config.json은 백업 후 복원.
"""
import os, sys, json, shutil, traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

CFG = "hardware_config.json"
BAK = "hardware_config.json.bak_scenario"
S = os.environ.get("SCENARIO", "reaxus2")


def dev(id_, name, driver, port, **kw):
    d = dict(id=id_, name=name, driver=driver, port=port)
    d.update(kw)
    return d


def mutate(cfg):
    inv, roles = cfg["inventory"], cfg["roles"]
    if S == "reaxus2":
        inv += [dev("dev_rx_a", "ReaxA", "연동 펌프 (Reaxus)", "COM31"),
                dev("dev_rx_b", "ReaxB", "연동 펌프 (Reaxus)", "COM32")]
        roles["pumps"] = roles["pumps"][:2]
        roles["pumps"][0]["drivers"]["motor"] = "dev_rx_a"
        roles["pumps"][1]["drivers"]["motor"] = "dev_rx_b"
    elif S == "novalve":
        roles["pumps"][0]["drivers"].pop("selector", None)
        roles["pumps"][0]["drivers"].pop("switcher", None)
    elif S == "colosseum":
        inv.append(dev("dev_colo_1", "콜로세움", "분획 수집기 (Colosseum)", "COM33"))
        roles["collector"] = {"driver_id": "dev_colo_1"}
    elif S == "nopush":
        roles.pop("push_pump", None)
    elif S == "onepump":
        roles["pumps"] = roles["pumps"][:1]
    return cfg


shutil.copy(CFG, BAK)
fails = []
try:
    cfg = json.load(open(CFG, encoding="utf-8"))
    json.dump(mutate(cfg), open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from PyQt5.QtWidgets import QApplication, QStyleFactory
    from PyQt5.QtCore import QTimer, QEventLoop
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    import main as M
    w = M.AutoPairingGUI()
    w.resize(1900, 1000)
    w.show()

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec_()

    wait(800)
    w.page_stack.setCurrentWidget(w.man_tab)
    wait(2000)  # FlowStrip 1.5s 갱신 1회 이상

    mt, st = w.man_tab, w.seq_tab

    def check(name, cond, detail=""):
        if cond:
            print(f"  PASS {name} {detail}")
        else:
            fails.append(name)
            print(f"  FAIL {name} {detail}")

    n_pumps_cfg = len(w.cfg.config_data["roles"]["pumps"])
    print(f"[{S}] pumps={list(w.pumps.keys())} collector={type(w.collector).__name__} "
          f"push={type(w.push_pump).__name__ if w.push_pump else None}")

    # ── 공통 검증 ──
    check("manual.채널카드수", len(mt.pump_card_widgets) == n_pumps_cfg,
          f"({len(mt.pump_card_widgets)}=={n_pumps_cfg})")
    fs_keys = list(mt._fs_nodes.keys())
    check("flowstrip.노드구성",
          ("push" in fs_keys) == (w.push_pump is not None), f"nodes={fs_keys}")
    feed_txt = mt._fs_nodes["feed"][1].text()
    check("flowstrip.feed갱신", str(n_pumps_cfg) in feed_txt or "가동" in feed_txt,
          f"'{feed_txt}'")
    col_txt = mt._fs_nodes["collector"][1].text()
    check("flowstrip.collector갱신", col_txt not in ("--", ""), f"'{col_txt}'")

    # Sequence 탭: 카드 펌프행 수 + 분취 상한
    card = st.step_cards[0]
    check("seq.카드펌프행", len(card.pump_widgets) == n_pumps_cfg,
          f"({len(card.pump_widgets)}=={n_pumps_cfg})")
    total = int(getattr(w.collector, "total_tubes", 0) or 0)
    expected_max = total if total > 0 else 192
    check("seq.분취상한", st.sp_collector_start.maximum() == expected_max,
          f"(max={st.sp_collector_start.maximum()}, collector={total})")
    check("manual.목표튜브상한", mt.sp_collector_tube.maximum() == expected_max,
          f"(max={mt.sp_collector_tube.maximum()})")
    st.calc_flow(silent=True)
    check("seq.유속계산", "mL/min" in card.lbl_total_flow.text(),
          f"'{card.lbl_total_flow.text()}'")

    # ── 배관도 적응 검증 (모든 시나리오 공통) ──
    fv = w.dash_tab.flow_viz
    check("diagram.펌프노드수", len(fv.items["pumps"]) == n_pumps_cfg,
          f"({len(fv.items['pumps'])})")
    check("diagram.push노드", (fv.items.get("push") is not None) == (w.push_pump is not None))
    col_node = fv.items.get("collector")
    if type(w.collector).__name__ == "Plate96Collector":
        check("diagram.collector종류", col_node is not None and col_node.kind == "plate96",
              f"({getattr(col_node, 'kind', None)})")
    # 칩 수 정밀 검증: 채널칩 3×(12way 있는 채널) + mixing/reactor/post(3) + collector면 +1
    n_sel_ch = len(fv.items["selectors"])
    n_tj_seg = max(0, len(fv.items["pumps"]) - 2)  # T-junction 간 구간 수
    expect_chips = (3 * n_sel_ch + 3 + n_tj_seg
                    + (1 if fv.items.get("collector") is not None else 0))
    check("diagram.볼륨칩수", len(fv.items.get("vol_chips", {})) == expect_chips,
          f"({len(fv.items['vol_chips'])}=={expect_chips}, sel_ch={n_sel_ch})")

    # ── 시나리오별 검증 ──
    if S == "reaxus2":
        motor_types = [type(pw.motor_widget).__name__ for pw in mt.pump_card_widgets]
        check("reaxus.모터위젯", all("Reaxus" in t for t in motor_types), f"{motor_types}")
        pix = mt.grab()
        pix.save("/mnt/user-data/outputs/manual_reaxus2.png")
    elif S == "novalve":
        first = mt.pump_card_widgets[0]
        check("novalve.selector없음", first.selector_widget is None)
        check("novalve.switcher없음", first.switcher_widget is None)
        pix = mt.grab()
        pix.save("/mnt/user-data/outputs/manual_novalve.png")
    elif S == "colosseum":
        check("colo.plate96UI숨김", not mt.plate96_ctrl_box.isVisible())
        check("colo.plateview숨김", not mt.btn_plate96_view.isVisible())
        check("colo.home버튼표시", mt.btn_collector_home.isVisible())
        pix = mt.grab()
        pix.save("/mnt/user-data/outputs/manual_colosseum.png")
    elif S == "nopush":
        check("nopush.push섹션숨김", not mt.push_pump_group.isVisible())
        check("nopush.노드없음", "push" not in fs_keys)
    elif S == "onepump":
        check("onepump.시약탭수", st.reagent_tables and len(st.reagent_tables) == 1,
              f"({list(st.reagent_tables.keys())})")
        pix = mt.grab()
        pix.save("/mnt/user-data/outputs/manual_onepump.png")

    print(f"[{S}] {'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
except Exception:
    traceback.print_exc()
    fails.append("EXCEPTION")
finally:
    shutil.move(BAK, CFG)

os._exit(1 if fails else 0)
