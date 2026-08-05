"""
Theme manager for dark/light mode switching and widget-specific theme refinements.
"""

import pyqtgraph as pg
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QGraphicsDropShadowEffect

from ui.colors import (
    DarkPalette as Dark,
    LightPalette as Light,
    T,
    remap_widget_tree_styles,
    set_active_dark_mode,
)
from ui.styles import INDUSTRIAL_DARK_THEME, INDUSTRIAL_LIGHT_THEME
from ui.theme import (
    get_action_button_style,
    get_main_control_button_style,
    get_outlet_valve_button_style,
    get_secondary_button_style,
)


class ThemeManager:
    """Handle app-wide theme switching and per-widget visual refinements."""

    def __init__(self, main_window):
        self.app = main_window
        self.is_dark_mode = True
        self._registry = {}

    def register_widgets(self, namespace, widgets_dict):
        for name, widget in widgets_dict.items():
            self._registry[name] = (namespace, widget)

    def unregister_namespace(self, namespace):
        to_remove = [k for k, (ns, _) in self._registry.items() if ns == namespace]
        for key in to_remove:
            del self._registry[key]

    def _w(self, name):
        entry = self._registry.get(name)
        if entry is not None:
            return entry[1]
        return getattr(self.app, name, None)

    def setup_stylesheet(self):
        self.is_dark_mode = True
        self.app.is_dark_mode = True
        set_active_dark_mode(True)
        self.app.setStyleSheet(INDUSTRIAL_DARK_THEME)

    def apply_current_theme(self):
        """Apply current mode styles to all registered widgets."""
        self.app.is_dark_mode = self.is_dark_mode
        set_active_dark_mode(self.is_dark_mode)

        if self.is_dark_mode:
            self.app.setStyleSheet(INDUSTRIAL_DARK_THEME)
            self._set_theme_button("☾", "라이트 모드로 전환", True)
            status = self._w("lbl_theme_status")
            if status:
                status.setText("다크 모드")
                status.setStyleSheet(f"font-weight: {T.FW_SEMI}; font-size: {T.FS_SM}; color: {Dark.TEXT_PRIMARY};")
            palette = Dark
            self._apply_common_extras(palette, True)
            self._apply_manual_extras(palette, True)
        else:
            self.app.setStyleSheet(INDUSTRIAL_LIGHT_THEME)
            self._set_theme_button("☀", "다크 모드로 전환", False)
            status = self._w("lbl_theme_status")
            if status:
                status.setText("라이트 모드")
                status.setStyleSheet(f"font-weight: {T.FW_SEMI}; font-size: {T.FS_SM}; color: {Light.TEXT_PRIMARY};")
            palette = Light
            self._apply_common_extras(palette, False)
            self._apply_manual_extras(palette, False)

        # Fallback remap for inline styles assigned outside the centralized theme path.
        remap_widget_tree_styles(self.app, self.is_dark_mode)

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self.apply_current_theme()

    def _set_theme_button(self, text, tooltip, is_dark):
        # @codesyncer: btn_theme 은 이제 ToggleSwitch(슬라이딩 스위치). setText/
        #   setStyleSheet 대신 상태만 동기화 — emit=False 로 시그널 루프 방지.
        #   레거시 QPushButton 폴백도 유지(setText 존재 시).
        btn = self._w("btn_theme")
        if not btn:
            return

        if hasattr(btn, "setChecked") and hasattr(btn, "toggle"):
            # ToggleSwitch: checked=True=다크. 사용자 클릭 후엔 이미 그 상태라 no-op.
            try:
                btn.setChecked(bool(is_dark), animate=True, emit=False)
            except TypeError:
                btn.setChecked(bool(is_dark))
            btn.setToolTip(tooltip)
            return

        # (레거시 QPushButton 폴백)
        btn.setText(text)
        btn.setToolTip(tooltip)
        if is_dark:
            btn.setStyleSheet(
                f"QPushButton {{ font-family: 'Segoe UI Symbol', 'Arial Unicode MS', 'Segoe UI'; font-size: {T.FS_XL}; font-weight: {T.FW_BOLD}; color: {Dark.ACCENT_BLUE}; "
                f"background-color: {Dark.BG_TERTIARY}; border: 1px solid {Dark.BORDER_PRIMARY}; border-radius: {T.R_LG}; }}"
                f"QPushButton:hover {{ background-color: {Dark.BG_HOVER}; border-color: {Dark.BORDER_LIGHT}; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ font-family: 'Segoe UI Symbol', 'Arial Unicode MS', 'Segoe UI'; font-size: {T.FS_XL}; font-weight: {T.FW_BOLD}; color: {Light.ACCENT_BLUE}; "
                f"background-color: {Light.BG_SECONDARY}; border: 1px solid {Light.BORDER_PRIMARY}; border-radius: {T.R_LG}; }}"
                f"QPushButton:hover {{ background-color: {Light.BG_HOVER}; border-color: {Light.BORDER_LIGHT}; }}"
            )

    @staticmethod
    def _apply_shadow(widget, is_dark):
        """Apply or update a subtle drop shadow on a card widget."""
        if widget is None:
            return
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setBlurRadius(20 if is_dark else 14)
        effect.setOffset(0, 2)
        effect.setColor(QColor(0, 0, 0, 80 if is_dark else 18))

    def _apply_common_extras(self, palette, is_dark):
        w = self._w
        p = palette

        # Sidebar
        sidebar = w("sidebar_frame")
        if sidebar:
            if is_dark:
                sidebar.setStyleSheet(
                    f"QFrame#MainSidebar {{ background:{p.BG_PRIMARY}; border:1px solid {p.BORDER_SECONDARY}; border-radius:{T.R_XL}; }}"
                )
            else:
                sidebar.setStyleSheet(
                    f"QFrame#MainSidebar {{ background:{p.BG_PRIMARY}; border:1px solid {p.BORDER_PRIMARY}; border-radius:{T.R_XL}; }}"
                )
            self._apply_shadow(sidebar, is_dark)

        # Main navigation
        nav_buttons = w("main_nav_buttons")
        if nav_buttons:
            for btn in nav_buttons:
                if is_dark:
                    btn.setStyleSheet(
                        f"QPushButton {{ text-align:left; padding-left:12px; font-size:{T.FS_MD}; font-weight:{T.FW_BOLD}; border-radius:{T.R_LG}; "
                        f"border:1px solid {p.BORDER_SECONDARY}; background:{p.BG_TERTIARY}; color:{p.TEXT_TERTIARY}; }}"
                        f"QPushButton:hover {{ background:{p.BG_HOVER}; color:#ffffff; border-color:{p.BORDER_LIGHT}; }}"
                        # 활성 페이지 = 악센트 (라이트 분기와 동일 규칙 — 구 BG_PRESSED 는
                        # 회색이라 현재 페이지가 어디인지, 악센트가 뭔지 드러나지 않았음)
                        f"QPushButton:checked {{ background:{p.ACCENT_BLUE}; border-color:{p.ACCENT_BLUE_DARK}; color:#ffffff; }}"
                    )
                else:
                    btn.setStyleSheet(
                        f"QPushButton {{ text-align:left; padding-left:12px; font-size:{T.FS_MD}; font-weight:{T.FW_BOLD}; border-radius:{T.R_LG}; "
                        f"border:1px solid {p.BORDER_PRIMARY}; background:{p.BG_SECONDARY}; color:{p.TEXT_TERTIARY}; }}"
                        f"QPushButton:hover {{ background:{p.BG_HOVER}; color:{p.TEXT_PRIMARY}; border-color:{p.BORDER_LIGHT}; }}"
                        f"QPushButton:checked {{ background:{p.ACCENT_BLUE}; border-color:{p.ACCENT_BLUE_DARK}; color:#ffffff; }}"
                    )

        monitor_card = w("monitor_card")
        if monitor_card:
            if is_dark:
                monitor_card.setStyleSheet(
                    f"QGroupBox {{ margin-top: 12px; padding-top: 16px; border-radius: {T.R_LG}; background:{p.BG_INPUT}; border:1px solid {p.BORDER_PRIMARY}; }}"
                    f"QGroupBox::title {{ left:10px; top:0px; padding:2px 8px; font-size:{T.FS_SM}; font-weight:{T.FW_BOLD}; color:{p.ACCENT_BLUE}; background: transparent; }}"
                )
            else:
                monitor_card.setStyleSheet(
                    f"QGroupBox {{ margin-top: 12px; padding-top: 16px; border-radius: {T.R_LG}; background:{p.BG_SECONDARY}; border:1px solid {p.BORDER_PRIMARY}; }}"
                    f"QGroupBox::title {{ left:10px; top:0px; padding:2px 8px; font-size:{T.FS_SM}; font-weight:{T.FW_BOLD}; color:{p.ACCENT_BLUE}; background: transparent; }}"
                )
            self._apply_shadow(monitor_card, is_dark)

        control_card = w("control_card")
        if control_card:
            if is_dark:
                control_card.setStyleSheet(
                    f"QGroupBox {{ margin-top: 12px; padding-top: 16px; border-radius: {T.R_LG}; background:{p.BG_INPUT}; border:1px solid {p.BORDER_PRIMARY}; }}"
                    f"QGroupBox::title {{ left:10px; top:0px; padding:2px 8px; font-size:{T.FS_SM}; font-weight:{T.FW_BOLD}; color:{p.ACCENT_BLUE}; background: transparent; }}"
                )
            else:
                control_card.setStyleSheet(
                    f"QGroupBox {{ margin-top: 12px; padding-top: 16px; border-radius: {T.R_LG}; background:{p.BG_SECONDARY}; border:1px solid {p.BORDER_PRIMARY}; }}"
                    f"QGroupBox::title {{ left:10px; top:0px; padding:2px 8px; font-size:{T.FS_SM}; font-weight:{T.FW_BOLD}; color:{p.ACCENT_BLUE}; background: transparent; }}"
                )
            self._apply_shadow(control_card, is_dark)

        brand = w("lbl_brand")
        if brand:
            brand.setStyleSheet(f"font-size: {T.FS_XXL}; font-weight: {T.FW_BOLD}; color: {p.TEXT_PRIMARY}; background: transparent;")

        brand_sub = w("lbl_brand_sub")
        if brand_sub:
            brand_sub.setStyleSheet(f"font-size: {T.FS_XS}; color: {p.TEXT_SECONDARY}; background: transparent;")

        if w("status_frame"):
            w("status_frame").setStyleSheet(
                f"background: {p.BG_DARK if is_dark else p.BG_SECONDARY}; border: 1px solid {p.BORDER_SECONDARY if is_dark else p.BORDER_PRIMARY}; border-radius: {T.R_MD}; padding: 6px;"
            )
        if w("lbl_status_title"):
            w("lbl_status_title").setStyleSheet(
                f"color: {p.TEXT_SECONDARY}; font-size: {T.FS_SM}; font-weight: {T.FW_SEMI}; font-family: {T.FONT};"
            )
        if w("lbl_status"):
            w("lbl_status").setStyleSheet(
                f"font-size: {T.FS_LG}; font-weight: {T.FW_BOLD}; color: {p.ACCENT_GREEN}; font-family: {T.FONT};"
            )
        if w("lbl_manual_status"):
            w("lbl_manual_status").setStyleSheet(f"font-size: {T.FS_SM}; color: {p.TEXT_SECONDARY};")

        if w("log_browser"):
            if is_dark:
                w("log_browser").setStyleSheet(
                    f"background:{p.BG_DARK}; color:{p.ACCENT_CYAN_ALT}; border:1px solid {p.BORDER_PRIMARY}; border-radius:{T.R_LG}; font-size:{T.FS_LG};"
                )
            else:
                w("log_browser").setStyleSheet(
                    f"background:{p.BG_SECONDARY}; color:{p.TEXT_PRIMARY}; border:1px solid {p.BORDER_PRIMARY}; border-radius:{T.R_LG}; font-size:{T.FS_LG};"
                )

        # SET/ACT 인라인 칩 규격 — 구 FS_XXL 히어로는 리드아웃 타일로 대체됨
        if w("monitor_frame"):
            w("monitor_frame").setStyleSheet(
                f"background: {p.BG_DARK if is_dark else p.BG_MONITOR}; border: 1px solid {p.BORDER_SECONDARY if is_dark else p.BORDER_PRIMARY}; border-radius: {T.R_MD}; padding: 1px 6px;"
            )
        if w("lbl_current_temp_title"):
            w("lbl_current_temp_title").setStyleSheet(
                f"color: {p.TEXT_SECONDARY}; font-size: {T.FS_XS}; font-weight: {T.FW_SEMI}; "
                f"letter-spacing: 1px; background: transparent; border: none;")
        if w("lbl_reactor_temp"):
            w("lbl_reactor_temp").setStyleSheet(
                f"color: {p.TEXT_PRIMARY}; font-size: {T.FS_MD}; font-weight: {T.FW_BOLD}; "
                f"font-family: {T.FONT_MONO}; background: transparent; border: none;")

        lcd_widgets = w("lcd_widgets")
        if lcd_widgets:
            for lcd, color in lcd_widgets:
                bg = p.BG_DARK if is_dark else p.BG_INPUT
                border = p.BORDER_SECONDARY if is_dark else p.BORDER_PRIMARY
                lcd.setStyleSheet(f"color:{color}; background:{bg}; border:2px solid {border}; border-radius: {T.R_SM};")

        lcd_labels = w("lcd_labels")
        if lcd_labels:
            for lbl in lcd_labels:
                lbl.setStyleSheet(f"color: {p.TEXT_PRIMARY}; font-weight: {T.FW_SEMI}; font-size: {T.FS_SM};")

        plot_widgets = w("plot_widgets")
        if plot_widgets:
            plot_bg = p.BG_DARK if is_dark else p.BG_SECONDARY
            axis_pen = p.BORDER_LIGHT if is_dark else p.BORDER_PRIMARY
            axis_text = p.TEXT_PRIMARY
            for pw in plot_widgets:
                pw.setBackground(plot_bg)
                pw.getPlotItem().getViewBox().setBackgroundColor(plot_bg)
                pw.getAxis("left").setTextPen(axis_text)
                pw.getAxis("left").setPen(pg.mkPen(axis_pen, width=2))
                pw.getAxis("bottom").setTextPen(axis_text)
                pw.getAxis("bottom").setPen(pg.mkPen(axis_pen, width=2))

        flow_viz = w("flow_viz")
        if flow_viz:
            flow_viz.setStyleSheet(
                f"border: 1px solid {p.BORDER_PRIMARY}; border-radius: {T.R_MD}; background: {p.BG_DARK if is_dark else p.BG_SECONDARY};"
            )
            if hasattr(flow_viz, "apply_theme"):
                flow_viz.apply_theme(is_dark=is_dark)

        dash_tab = w("dash_tab")
        if dash_tab and hasattr(dash_tab, "apply_theme"):
            dash_tab.apply_theme(is_dark=is_dark, palette=p)
            for attr in ("hero", "workspace_card", "health_card"):
                self._apply_shadow(getattr(dash_tab, attr, None), is_dark)

        seq_tab = w("seq_tab")
        if seq_tab and hasattr(seq_tab, "apply_theme"):
            seq_tab.apply_theme(is_dark=is_dark)

        calc_tab = w("calc_tab")
        if calc_tab and hasattr(calc_tab, "apply_theme"):
            calc_tab.apply_theme(is_dark=is_dark)

        if w("lbl_start_tube_seq"):
            w("lbl_start_tube_seq").setStyleSheet(f"color: {p.ACCENT_BLUE}; font-weight: {T.FW_SEMI};")

        btn_pause = w("btn_pause")
        if btn_pause:
            btn_pause.setStyleSheet(get_main_control_button_style(palette, "pause"))

        btn_emergency = w("btn_emergency")
        if btn_emergency:
            btn_emergency.setStyleSheet(get_main_control_button_style(palette, "emergency"))

    def _apply_manual_extras(self, palette, is_dark):
        w = self._w
        p = palette

        # 매뉴얼탭 소유 버튼(피드 헤더/Push/Outlet 트랙)은 탭이 자체 재적용
        man_tab = w("man_tab")
        if man_tab is not None and hasattr(man_tab, "apply_theme"):
            man_tab.apply_theme(is_dark)

        if w("btn_waste"):
            w("btn_waste").setStyleSheet(get_outlet_valve_button_style(palette, "waste"))
        if w("btn_coll"):
            w("btn_coll").setStyleSheet(get_outlet_valve_button_style(palette, "collection"))

        # lbl_valve/lbl_collector/lbl_heater/lbl_target 캡션은
        # ManualTab._style_manual_buttons(공통 캡션 규격)가 담당 — 여기서 재정의 금지.
        if w("lbl_tube"):
            w("lbl_tube").setStyleSheet(f"font-size: {T.FS_SM}; color: {p.TEXT_SECONDARY}; font-weight: {T.FW_SEMI};")
        if w("lbl_step"):
            w("lbl_step").setStyleSheet(f"font-size: {T.FS_SM}; color: {p.TEXT_SECONDARY}; font-weight: {T.FW_SEMI};")
        if w("lcd_collector_step"):
            w("lcd_collector_step").setStyleSheet(
                f"background: {p.BG_INPUT if not is_dark else p.BG_DARK}; color: {p.TEXT_PRIMARY if not is_dark else p.ACCENT_CYAN_ALT}; "
                f"border: 1px solid {p.BORDER_PRIMARY if not is_dark else p.BORDER_LIGHT}; border-radius: {T.R_SM};"
            )

        if w("separator_outlet"):
            w("separator_outlet").setStyleSheet(f"background: {p.SEPARATOR};")

        # btn_h_set/off · btn_collector_move/back/forward/home 는 이제
        # ManualTab._style_manual_buttons(v4 레퍼런스 언어)가 담당 —
        # man_tab.apply_theme(위에서 호출됨)가 primary/secondary/stop 로 재적용.

        pump_cards = w("pump_card_widgets")
        if pump_cards:
            for card in pump_cards:
                if hasattr(card, "apply_theme"):
                    card.apply_theme(is_dark=is_dark)
