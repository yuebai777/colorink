"""Picker/module/foreground/settings actions for the main window.

Extracted from ``ui.main_window``: module switching, picker pane toggling,
settings window management, foreground visibility tracking and dynamic
window-flag updates.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMenu, QPushButton

from core import config
from core.foreground import (
    _exe_matches_drawing_app,
    _is_popup_menu_class,
    _resolve_process_exe,
    _title_matches_drawing_app,
    identify_drawing_app,
    is_drawing_app_menu_open,
    resolve_auto_sync_mode,
)
from ui.lab_harmony import HARMONY_MODE_NAMES
from ui.panels import registry
from ui.widgets.picker_icon_button import PickerIconButton
from ui.window.module_defs import (
    _LAB_VIZ_NAMES,
    _MODULE_DEFS,
    _MODULE_NAMES,
    _MODULE_ORDER,
    normalize_lab_visualizer_order,
    normalize_wheel_module_order,
    normalize_wheel_module_tabs,
)


class PickerActionsMixin:

    def _init_picker_tab_bar(self):
        """Wire the top picker tab bar for wheel-module and LAB/OKLab switching."""
        tab_bar = getattr(self, "picker_tab_bar", None)
        if tab_bar is None:
            return
        tab_bar.currentChanged.connect(self._on_picker_tab_changed)
        tab_bar.tabMoved.connect(self._on_picker_tab_moved)
        tab_bar.wheelGripDropped.connect(self._on_wheel_grip_dropped)
        grip = getattr(self, "wheel_grip_bar", None)
        if grip is not None:
            grip.float_requested.connect(
                lambda _pid: self._float_active_wheel_page())
            grip.toggled.connect(
                lambda _pid: self._float_active_wheel_page())
            grip.menu_requested.connect(self._show_wheel_grip_menu)
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()

    def _wheel_visible_keys(self) -> list[str]:
        """Enabled module keys whose page is not currently floating."""
        cfg = getattr(self, "cfg", {})
        keys = normalize_wheel_module_tabs(
            cfg.get("wheelModuleTabs"),
            cfg.get("wheelModuleOrder"),
            getattr(self, "_current_module", "hsv"),
        )
        windows = {}
        windows_of = getattr(self, "floating_windows", None)
        if callable(windows_of):
            try:
                windows = windows_of()
            except Exception:
                windows = {}
        host = getattr(self, "panel_host", None)
        pdrop = getattr(self, "picker_drop", None)
        result = []
        for key in keys:
            panel_id = registry.wheel_panel_id(key)
            if panel_id in windows:
                continue
            if host is not None:
                try:
                    if host.widget_for(panel_id) is not None:
                        # Dragged into the shared slider host: it is a panel
                        # there now, so it must not stay in the picker stack.
                        continue
                except RuntimeError:
                    pass
            if pdrop is not None and pdrop.owns_panel(panel_id):
                continue
            result.append(key)
        return result

    def _sync_wheel_stack(self) -> list[str]:
        """Keep the wheel page stack in tab order; floating pages are absent."""
        stack = getattr(self, "wheel_stack", None)
        panels = getattr(self, "wheel_panels", None)
        if stack is None or not panels:
            return []
        keys = self._wheel_visible_keys()
        for key, panel in panels.items():
            if key not in keys and stack.indexOf(panel) >= 0:
                stack.removeWidget(panel)
        for index, key in enumerate(keys):
            panel = panels.get(key)
            if panel is None:
                continue
            current = stack.indexOf(panel)
            if current < 0:
                stack.insertWidget(index, panel)
            elif current != index:
                stack.removeWidget(panel)
                stack.insertWidget(index, panel)
        active = str(getattr(self, "_current_module", "hsv")).lower()
        target = active if active in keys else (keys[0] if keys else None)
        if target is not None:
            panels[target].show()
            stack.setCurrentWidget(panels[target])
        return keys

    def _sync_wheel_grip(self) -> None:
        """Show the grip strip for the docked wheel page or docked LAB page."""
        from ui.lab_float import LAB_FLOAT_ID
        grip = getattr(self, "wheel_grip_bar", None)
        if grip is None:
            return
        cfg = getattr(self, "cfg", {})
        stack = getattr(self, "stack", None)
        on_wheel = stack is not None and stack.currentIndex() == 0
        on_lab = stack is not None and stack.currentIndex() == 1
        active = str(getattr(self, "_current_module", "hsv")).lower()
        lab_visible = (on_lab and getattr(self, "pane_lab", None) is not None
                       and self.pane_lab.isVisible())
        show = (bool(cfg.get("panelDrag", False))
                and ((on_wheel and active in self._wheel_visible_keys())
                     or lab_visible))
        if show:
            if on_lab:
                mode = str(cfg.get("visualizerMode", "lab")).lower()
                grip.panel_id = LAB_FLOAT_ID
                grip.set_title("OKLab" if mode == "oklab" else "LAB")
            else:
                grip.panel_id = registry.wheel_panel_id(active)
                grip.set_title(_MODULE_NAMES.get(active, active.upper()))
        grip.setVisible(show)
        gap = getattr(self, "picker_top_gap", None)
        if gap is not None:
            tab_bar = getattr(self, "picker_tab_bar", None)
            show_tabs = tab_bar is not None and not tab_bar.isHidden()
            gap.setVisible(show and show_tabs)

    def _sync_picker_tab_bar(self) -> bool:
        """Populate and show/hide the top tabs for the current picker page."""
        tab_bar = getattr(self, "picker_tab_bar", None)
        if tab_bar is None:
            return False
        cfg = getattr(self, "cfg", {})
        show_tabs = bool(cfg.get("showModuleSwitchButton", True))
        stack = getattr(self, "stack", None)
        on_lab = stack is not None and stack.currentIndex() == 1
        items = []
        active_key = ""
        if on_lab:
            # LAB/OKLab switching keeps its existing click/scroll behaviour.
            order = normalize_lab_visualizer_order(cfg.get("labVisualizerOrder"))
            items = [(k, _LAB_VIZ_NAMES[k]) for k in order]
            active_key = str(cfg.get("visualizerMode", "lab")).lower()
            if active_key not in _LAB_VIZ_NAMES:
                active_key = "lab"
        else:
            visible = self._sync_wheel_stack()
            active_key = str(
                getattr(self, "_current_module", cfg.get("colorSpaceModule", "hsv"))
            ).lower()
            if active_key not in visible and visible:
                # The active module was floated or switched off: follow the
                # first page that is still docked. _apply_module is re-entrant
                # here only once — the second pass sees active in visible.
                if hasattr(self, "_apply_module"):
                    self._apply_module(visible[0])
                    visible = self._wheel_visible_keys()
                    active_key = str(getattr(self, "_current_module", "hsv")).lower()
            if active_key not in visible and visible:
                active_key = visible[0]
            items = [(k, _MODULE_NAMES[k]) for k in visible]
        tab_bar.set_tabs(items, active_key)
        host_owned = self._host_owned_wheel() if not on_lab else False
        show_bar = show_tabs and (bool(items) or host_owned)
        was_visible = not tab_bar.isHidden()
        tab_bar.setVisible(show_bar)
        self._sync_wheel_grip()
        self._sync_main_picker_visibility()
        return was_visible != tab_bar.isVisible()

    def _host_owned_wheel(self) -> bool:
        host = getattr(self, "panel_host", None)
        if host is None:
            return False
        cfg = getattr(self, "cfg", {}) or {}
        try:
            return any(
                host.widget_for(registry.wheel_panel_id(k)) is not None
                for k in normalize_wheel_module_tabs(
                    cfg.get("wheelModuleTabs"),
                    cfg.get("wheelModuleOrder"),
                    "hsv"))
        except RuntimeError:
            return False

    def _sync_main_picker_visibility(self) -> None:
        """When every wheel page is docked elsewhere, collapse the blank top.

        Dragging all colour pages into the slider host or into floating
        windows used to leave an empty square at the top of the main window.
        The picker is a view of the wheel pages, so when none are here it
        disappears; bringing one back restores it.
        """
        stack = getattr(self, "stack", None)
        if stack is None:
            return
        on_lab = stack.currentIndex() == 1
        show = on_lab or bool(self._wheel_visible_keys())
        container = getattr(self, "picker_tab_container", None)
        preview = getattr(self, "preview_box", None)
        tab_bar = getattr(self, "picker_tab_bar", None)
        grip = getattr(self, "wheel_grip_bar", None)
        if not show:
            if not stack.isHidden():
                stack.hide()
            stack.setMinimumHeight(0)
            stack.setMaximumHeight(0)
            if container is not None:
                # If the pages live in the slider host, keep the (empty) tab
                # strip as the drop target that brings them back. With all
                # pages floating there is nothing to drop on, so hide it.
                keep_return_strip = (self._host_owned_wheel()
                                     and tab_bar is not None
                                     and not tab_bar.isHidden())
                container.setVisible(keep_return_strip)
            if preview is not None:
                preview.hide()
        else:
            if stack.isHidden():
                stack.show()
                stack.setMaximumHeight(16777215)
            if preview is not None:
                preview.show()
            if container is not None:
                has_chrome = ((tab_bar is not None and not tab_bar.isHidden())
                              or (grip is not None and not grip.isHidden())
                              or (getattr(self, "picker_top_gap", None) is not None
                                  and not self.picker_top_gap.isHidden()))
                container.setVisible(has_chrome)
        sync_spacers = getattr(self, "_sync_band_spacers", None)
        if callable(sync_spacers):
            sync_spacers()
        self._adjust_content_height()

    def _on_picker_tab_changed(self, index: int) -> None:
        tab_bar = getattr(self, "picker_tab_bar", None)
        if tab_bar is None or index < 0:
            return
        raw_key = tab_bar.tabData(index)
        if not raw_key:
            return
        key = str(raw_key).lower()
        stack = getattr(self, "stack", None)
        on_lab = stack is not None and stack.currentIndex() == 1
        if on_lab:
            if key != self.cfg.get("visualizerMode", "lab"):
                self.set_lab_visualizer_mode(key)
        else:
            if key != getattr(self, "_current_module", None):
                self._apply_module(key)

    def _on_picker_tab_moved(self, _from_idx: int, _to_idx: int) -> None:
        tab_bar = getattr(self, "picker_tab_bar", None)
        if tab_bar is None:
            return
        visible_keys = tab_bar.tab_keys()
        stack = getattr(self, "stack", None)
        on_lab = stack is not None and stack.currentIndex() == 1
        if on_lab:
            self.cfg["labVisualizerOrder"] = normalize_lab_visualizer_order(visible_keys)
            self._schedule_module_config_save()
            return
        full_order = normalize_wheel_module_order(self.cfg.get("wheelModuleOrder"))
        vis_set = set(visible_keys)
        vis_iter = iter(visible_keys)
        merged = [next(vis_iter) if k in vis_set else k for k in full_order]
        self.cfg["wheelModuleOrder"] = normalize_wheel_module_order(merged)
        self.cfg["wheelModuleTabs"] = normalize_wheel_module_tabs(
            self.cfg.get("wheelModuleTabs"),
            self.cfg["wheelModuleOrder"],
            getattr(self, "_current_module", "hsv"),
        )
        self._schedule_module_config_save()
        self._sync_wheel_stack()
        self._sync_wheel_grip()
        if hasattr(self, "settings_sidebar") and self.settings_sidebar.isVisible():
            self.settings_sidebar.notify_module_changed()

    def _on_wheel_grip_dropped(self, panel_id: str, index: int) -> None:
        """A wheel grip was dropped on the tab strip: reorder that module."""
        stack = getattr(self, "stack", None)
        if stack is None or stack.currentIndex() != 0:
            return
        key = registry.wheel_panel_key(panel_id)
        tab_bar = getattr(self, "picker_tab_bar", None)
        if key is None or tab_bar is None:
            return
        host = getattr(self, "panel_host", None)
        if host is not None:
            try:
                owned = host.widget_for(panel_id) is not None
            except RuntimeError:
                owned = False
            if owned:
                # It was dragged into the shared slider host. Dropping its
                # grip on the strip brings the page back to the picker.
                from ui.panels import rearrange
                tree = host.tree()
                new_tree = rearrange.remove_panel(tree, panel_id)
                if new_tree != tree:
                    host.set_tree(new_tree)
                    record = getattr(self, "save_panel_layout", None)
                    if callable(record):
                        record(new_tree)
                    config.save_hotkey_config(self.cfg)
                    self.refresh_slider_visibility_and_order()
                    self._sync_picker_tab_bar()
                return
        pdrop = getattr(self, "picker_drop", None)
        if pdrop is not None and pdrop.owns_panel(panel_id):
            pdrop.remove_panel(panel_id)
            config.save_hotkey_config(self.cfg)
            self.refresh_slider_visibility_and_order()
            self._sync_picker_tab_bar()
            return
        keys = tab_bar.tab_keys()
        if key not in keys:
            return
        keys.remove(key)
        index = max(0, min(int(index), len(keys)))
        keys.insert(index, key)
        full_order = normalize_wheel_module_order(self.cfg.get("wheelModuleOrder"))
        vis_set = set(keys)
        vis_iter = iter(keys)
        merged = [next(vis_iter) if k in vis_set else k for k in full_order]
        self.cfg["wheelModuleOrder"] = normalize_wheel_module_order(merged)
        self.cfg["wheelModuleTabs"] = normalize_wheel_module_tabs(
            self.cfg.get("wheelModuleTabs"),
            self.cfg["wheelModuleOrder"],
            key,
        )
        self._schedule_module_config_save()
        self._apply_module(key)
        if hasattr(self, "settings_sidebar") and self.settings_sidebar.isVisible():
            self.settings_sidebar.notify_module_changed()

    def _float_active_wheel_page(self) -> bool:
        """Tear off the wheel page currently shown (grip drag / double-click)."""
        stack = getattr(self, "stack", None)
        if stack is None:
            return False
        if stack.currentIndex() == 1:
            float_lab = getattr(self, "float_lab_view", None)
            return bool(float_lab()) if callable(float_lab) else False
        if stack.currentIndex() != 0:
            return False
        key = str(getattr(self, "_current_module", "")).lower()
        if key not in getattr(self, "wheel_panels", {}):
            return False
        float_wheel = getattr(self, "float_wheel_module", None)
        if not callable(float_wheel):
            return False
        return bool(float_wheel(key))

    def _show_wheel_grip_menu(self, _panel_id=None, global_pos=None) -> None:
        """Right-click on the wheel grip: the same shortcuts as sliders."""
        from PyQt6.QtWidgets import QMenu
        popup = QMenu(self)
        float_action = popup.addAction("浮出为独立窗口")
        stack = getattr(self, "stack", None)
        on_lab = stack is not None and stack.currentIndex() == 1
        reset_action = None if on_lab else popup.addAction("复位色图标签顺序")
        picked = popup.exec(global_pos) if global_pos is not None else popup.exec()
        if picked == float_action:
            self._float_active_wheel_page()
        elif reset_action is not None and picked == reset_action:
            self.cfg["wheelModuleOrder"] = list(_MODULE_ORDER)
            self.cfg["wheelModuleTabs"] = list(_MODULE_ORDER)
            self._schedule_module_config_save()
            self._sync_picker_tab_bar()

    def _sync_floating_wheel_chrome(self) -> None:
        """Push preview visibility/theme changes into open colour-map windows."""
        pages = getattr(self, "_floating_wheel_pages", None)
        if not pages:
            return
        cfg = getattr(self, "cfg", {}) or {}
        chrome = getattr(self, "_floating_chrome", None)
        for page in list(pages.values()):
            try:
                page.apply_visibility_defaults()
                page.sync_lab_settings()
                page.preview.position_mode = cfg.get(
                    "previewBoxPosition", "top-left")
                page.sync_from_owner()
                if chrome is not None:
                    page.apply_chrome(chrome)
            except RuntimeError:
                continue
        lab_page = getattr(self, "_floating_lab_page", None)
        if lab_page is not None:
            try:
                lab_page.sync_from_owner()
                if chrome is not None:
                    lab_page.apply_chrome(chrome)
            except RuntimeError:
                pass

    def set_lab_visualizer_mode(self, mode: str) -> None:
        """Switch the LAB visualizer between 'lab' and 'oklab'."""
        mode = "oklab" if str(mode).lower() == "oklab" else "lab"
        self.cfg["visualizerMode"] = mode
        self._schedule_module_config_save()
        if hasattr(self, "lab_square"):
            self.lab_square.set_render_mode(mode)
            r, g, b = getattr(self, "current_rgb", (255, 255, 255))
            self.lab_square.set_color(r, g, b, block_signals=True)
            if hasattr(self, "lab_slider") and self.lab_slider is not None:
                self.lab_slider.set_lightness(self.lab_square.L)
            lab_slider_column = getattr(self, "lab_slider_column", None)
            if (lab_slider_column is not None and lab_slider_column.isVisible()
                    and hasattr(self, "_schedule_lab_gamut_range")):
                self._schedule_lab_gamut_range(50)
        sync_lab = getattr(self, "_sync_floating_lab_settings", None)
        if callable(sync_lab):
            sync_lab()
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()
        self._sync_settings_sidebar_lab_controls()
        self.update()

    def _sync_floating_lab_settings(self) -> None:
        pages = getattr(self, "_floating_wheel_pages", None) or {}
        for page in list(pages.values()):
            try:
                page.sync_lab_settings()
                page._sync_lab_from_owner()
            except RuntimeError:
                continue
        lab_page = getattr(self, "_floating_lab_page", None)
        if lab_page is not None:
            try:
                lab_page.sync_lab_settings()
                lab_page._sync_lab_from_owner()
                window = lab_page.window()
                mode = str(self.cfg.get("visualizerMode", "lab")).lower()
                title = "OKLab" if mode == "oklab" else "LAB"
                if getattr(window, "_title_override", None) != title:
                    window._title_override = title
                    window._update_window_title()
            except (RuntimeError, AttributeError):
                pass


    def _init_module_button(self):
        """Create a floating button next to ⊙/△ to cycle HSV→HLS→LCH modules."""
        btn = PickerIconButton("HSV", self.pane_wheel, icon_kind="module:hsv")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("切换色彩空间模块 (HSV / HLS / LCH)")
        btn.clicked.connect(self._next_module)
        # Never keep keyboard focus — Space (the default LAB-toggle hotkey)
        # must not re-activate this button from anywhere in the window.
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setVisible(self.cfg.get("showModuleSwitchButton", True))
        btn.setObjectName("ModuleButton")
        self.pane_wheel.set_module_button(btn)
        self.btn_module = btn

    def _init_lab_toggle_buttons(self):
        """Create the LAB pane shape toggle + harmony-mode menu buttons.

        Both are optional floating buttons on ``pane_lab`` (left of the
        existing wheel/LAB toggle) and are only visible on the LAB pane.
        """
        self.btn_lab_shape = PickerIconButton("□", self.pane_lab, icon_kind="lab:square")
        self.btn_lab_shape.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lab_shape.setToolTip("切换 LAB 视图形状 (方形 / 圆形)")
        self.btn_lab_shape.clicked.connect(self.toggle_lab_view_shape)
        self.btn_lab_shape.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_lab_shape.setObjectName("ModuleButton")
        self.pane_lab.set_module_button(self.btn_lab_shape)

        self.btn_lab_harmony = QPushButton("和", self.pane_lab)
        self.btn_lab_harmony.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lab_harmony.setToolTip("LAB 调和模式")
        self.btn_lab_harmony.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_lab_harmony.setObjectName("ModuleButton")
        self._lab_harmony_menu = QMenu(self.btn_lab_harmony)
        for mode, label in HARMONY_MODE_NAMES.items():
            action = self._lab_harmony_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, m=mode: self.set_lab_harmony_mode(m))
        self.btn_lab_harmony.setMenu(self._lab_harmony_menu)
        self.pane_lab.set_extra_button(self.btn_lab_harmony)

        self._update_lab_shape_button()
        self._update_lab_harmony_button()

    def _sync_settings_sidebar_lab_controls(self):
        """Keep the settings dialog's LAB combos in sync after an on-pane toggle."""
        sidebar = getattr(self, "settings_sidebar", None)
        if sidebar is not None and sidebar.isVisible():
            sidebar.notify_lab_settings_changed()

    def toggle_lab_view_shape(self):
        """Switch the existing LAB a*b* plane between square and circulant disc."""
        current = self.cfg.get("labViewShape", "square")
        new_shape = "disc" if current != "disc" else "square"
        self.cfg["labViewShape"] = new_shape
        config.save_hotkey_config(self.cfg)
        self.lab_square.set_shape(new_shape)
        self._sync_lab_lightness_bar()
        if self.cfg.get("hideHueRing", False):
            self._sync_ringless_mode()
        else:
            self._refit_preview_box()
        sync_lab = getattr(self, "_sync_floating_lab_settings", None)
        if callable(sync_lab):
            sync_lab()
        self._update_lab_shape_button()
        self._sync_settings_sidebar_lab_controls()
        self.update()

    def set_lab_harmony_mode(self, mode: str):
        """Apply a colour-harmony preset (complementary/split/analogous/...)."""
        if mode not in HARMONY_MODE_NAMES:
            mode = "analogous"
        self.cfg["labHarmonyMode"] = mode
        config.save_hotkey_config(self.cfg)
        self.lab_square.set_harmony_mode(mode)
        sync_lab = getattr(self, "_sync_floating_lab_settings", None)
        if callable(sync_lab):
            sync_lab()
        self._update_lab_harmony_button()
        self._sync_settings_sidebar_lab_controls()
        self.update()

    def _update_lab_shape_button(self):
        if not hasattr(self, "btn_lab_shape") or not hasattr(self, "lab_square"):
            return
        is_disc = getattr(self.lab_square, "shape", "square") == "disc"
        self.btn_lab_shape.setText("◯" if is_disc else "□")
        if hasattr(self.btn_lab_shape, "set_icon_kind"):
            self.btn_lab_shape.set_icon_kind("lab:disc" if is_disc else "lab:square")
        self.btn_lab_shape.setToolTip(
            "切换 LAB 视图形状 (方形 / 圆形)" if not is_disc else "切换 LAB 视图形状 (圆形 / 方形)")

    def _update_lab_harmony_button(self):
        if not hasattr(self, "btn_lab_harmony") or not hasattr(self, "cfg"):
            return
        mode = self.cfg.get("labHarmonyMode", "analogous")
        label = HARMONY_MODE_NAMES.get(mode, "近似")
        if hasattr(self, "btn_lab_harmony"):
            char = label[0] if label else "和"
            self.btn_lab_harmony.setText(char)
            self.btn_lab_harmony.setToolTip(f"LAB 调和模式: {label} (点击切换)")

    def _update_module_button_label(self):
        if hasattr(self, "btn_module"):
            name = {"hsv": "◉", "vhsv": "◩", "hls": "△", "rgb": "■", "lch": "◈"}.get(self._current_module, "◉")
            self.btn_module.setText(name)
            if hasattr(self.btn_module, "set_icon_kind"):
                self.btn_module.set_icon_kind(f"module:{self._current_module}")
            self.btn_module.setToolTip(f"模块: {_MODULE_NAMES.get(self._current_module, 'HSV')} (点击切换)")

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        show_module = self.cfg.get("showModuleSwitchButton", True)
        show_lab_toggle = self.cfg.get("showLabToggleButton", True)
        show_lab_shape_btn = self.cfg.get("showLabShapeButton", True)
        show_lab_harmony_btn = self.cfg.get("showLabHarmonyButton", True)
        if hasattr(self, "pane_wheel"):
            has_mod_btn = hasattr(self, "btn_module") and self.btn_module is not None
            self.pane_wheel.set_module_slot_reserved(show_module if has_mod_btn else False)
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()
        if hasattr(self, "pane_lab"):
            # The LAB pane keeps its shape toggle + harmony menu visible even
            # when the wheel module button is hidden, so always reserve the slot.
            self.pane_lab.set_module_slot_reserved(True)
        if idx == 0:
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_wheel.raise_()
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.hide()
            # Module button only visible in wheel pane
            if hasattr(self, 'btn_module'):
                self.btn_module.setVisible(show_module)
                if show_module:
                    self.btn_module.raise_()
            for _btn in ("btn_lab_shape", "btn_lab_harmony"):
                if hasattr(self, _btn):
                    getattr(self, _btn).hide()
        else:
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_lab.raise_()
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.hide()
            if hasattr(self, 'btn_module'):
                self.btn_module.hide()
            # LAB pane's shape toggle + harmony menu follow their own settings.
            if hasattr(self, 'btn_lab_shape'):
                self.btn_lab_shape.setVisible(show_lab_shape_btn)
                if show_lab_shape_btn:
                    self.btn_lab_shape.raise_()
            if hasattr(self, 'btn_lab_harmony'):
                self.btn_lab_harmony.setVisible(show_lab_harmony_btn)
                if show_lab_harmony_btn:
                    self.btn_lab_harmony.raise_()

        show_readout = bool(self.cfg.get("showColorValueReadout", True))
        pdrop = getattr(self, "picker_drop", None)
        picker_showing = (
            (pdrop is None or pdrop.center_shows_picker())
            and hasattr(self, "stack")
            and not self.stack.isHidden()
        )
        # Re-pack the visible button cluster after every visibility change so
        # a lone toggle always lands on the outermost edge.
        if hasattr(self, "pane_wheel"):
            self.pane_wheel.set_readout_enabled(show_readout and picker_showing and idx == 0)
            self.pane_wheel._reposition_mode_button()
        if hasattr(self, "pane_lab"):
            self.pane_lab.set_readout_enabled(show_readout and picker_showing and idx == 1)
            self.pane_lab._reposition_mode_button()

    def _on_readout_mode_changed(self, mode: str) -> None:
        """Persist the inline numeric readout format and sync all panes."""
        self.cfg["colorValueReadoutMode"] = mode
        config.save_hotkey_config(self.cfg)
        iter_ro = getattr(self, "_iter_readouts", None)
        readouts = iter_ro() if callable(iter_ro) else (
            getattr(self, "readout_wheel", None),
            getattr(self, "readout_lab", None),
        )
        for ro in readouts:
            if ro is not None:
                ro.set_mode(mode, emit=False)
        for pane_attr in ("pane_wheel", "pane_lab"):
            pane = getattr(self, pane_attr, None)
            if pane is not None:
                pane._reposition_mode_button()
        for wpage in (getattr(self, "_floating_wheel_pages", None) or {}).values():
            place = getattr(wpage, "_place_overlays", None)
            if callable(place):
                place()

    def toggle_lab_global(self):
        """The system-wide LAB shortcut: main pane plus opted-in floats."""
        self.toggle_picker_mode()
        stack = getattr(self, "stack", None)
        target = "lab" if (stack is not None and stack.currentIndex() == 1) else "wheel"
        pages = getattr(self, "_floating_wheel_pages", None) or {}
        for page in list(pages.values()):
            try:
                if page.follow_global_lab and page.lab_enabled:
                    page.set_view_mode(target)
            except RuntimeError:
                continue

    def toggle_wheel_tab_global(self) -> None:
        """Advance wheel/LAB tabs globally across main picker and opted-in floats."""
        bar = getattr(self, "picker_tab_bar", None)
        stack = getattr(self, "stack", None)
        if (bar is not None and stack is not None
                and not stack.isHidden() and bar.isVisible() and bar.count() >= 2):
            bar.advance_tab()
        unique_fn = getattr(self, "_unique_floating_windows", None)
        classify_fn = getattr(self, "_classify_floating_window", None)
        advance_fn = getattr(self, "_advance_floating_window_tabs", None)
        if callable(unique_fn) and callable(classify_fn) and callable(advance_fn):
            for win_obj in unique_fn():
                win_id = getattr(win_obj, "panel_id", "")
                pages, _labs, _sliders = classify_fn(win_obj)
                if not pages:
                    continue
                if config.get_floating_window_hotkeys(self.cfg, win_id)["follow_global_wheel_tab"]:
                    advance_fn(win_obj)

    def toggle_slider_tab_global(self) -> None:
        """Advance slider/panel tab stacks globally across main host and opted-in floats."""
        advance_host_fn = getattr(self, "_advance_host_tabs", None)
        main_host = getattr(self, "panel_host", None)
        if callable(advance_host_fn) and main_host is not None:
            advance_host_fn(main_host)
        unique_fn = getattr(self, "_unique_floating_windows", None)
        classify_fn = getattr(self, "_classify_floating_window", None)
        advance_fn = getattr(self, "_advance_floating_window_tabs", None)
        if callable(unique_fn) and callable(classify_fn) and callable(advance_fn):
            for win_obj in unique_fn():
                win_id = getattr(win_obj, "panel_id", "")
                _pages, _labs, slider_pids = classify_fn(win_obj)
                if not slider_pids:
                    continue
                if config.get_floating_window_hotkeys(self.cfg, win_id)["follow_global_slider_tab"]:
                    advance_fn(win_obj)

    def toggle_floating_window_tabs(self, window_id: str) -> bool:
        """Advance tab stack(s) inside the open floating window matching *window_id*."""
        find_fn = getattr(self, "_floating_hotkey_window_for", None)
        advance_fn = getattr(self, "_advance_floating_window_tabs", None)
        if callable(find_fn) and callable(advance_fn):
            win_obj = find_fn(window_id)
            if win_obj is not None:
                return advance_fn(win_obj)
        return False

    def toggle_floating_window_lab(self, window_id: str) -> bool:
        """Toggle wheel <-> LAB on floating wheel page(s) inside *window_id*."""
        find_fn = getattr(self, "_floating_hotkey_window_for", None)
        classify_fn = getattr(self, "_classify_floating_window", None)
        if not (callable(find_fn) and callable(classify_fn)):
            return False
        win_obj = find_fn(window_id)
        if win_obj is None:
            return False
        pages, _labs, _sliders = classify_fn(win_obj)
        toggled = False
        for page in pages:
            if getattr(page, "lab_enabled", True):
                page.toggle_view()
                toggled = True
        return toggled

    def toggle_picker_mode(self):
        """Switch picker panes without re-running the full theme/layout pass."""
        new_index = (self.stack.currentIndex() + 1) % 2
        self.stack.setCurrentIndex(new_index)
        self.update_mode_buttons_visibility()
        # Only the page-local ringless geometry needs to move here. Re-running
        # apply_theme() would rebuild every slider stylesheet and gradient while
        # the user is only asking for a pane switch.
        self._sync_ringless_mode()
        lock_fn = getattr(self, "_lock_picker_stack_square", None)
        if callable(lock_fn):
            lock_fn()
        self._update_lab_avoid()
        if not self.cfg.get("hideHueRing", False):
            refit = getattr(self, "_refit_preview_box", None)
            if callable(refit):
                refit()
        self.color_wheel.schedule_slice_prewarm(350)

        r, g, b = self.current_rgb
        if new_index == 1:  # LAB pane
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(self.lab_square.L)
            lab_slider_column = getattr(self, "lab_slider_column", None)
            if lab_slider_column is not None and lab_slider_column.isVisible():
                self._schedule_lab_gamut_range(50)
        else:  # Color wheel pane
            # The wheel already owns the exact state when the last source was
            # a wheel interaction. Avoid RGB?HSV re-quantization here: it can
            # shift hue slightly and evict the resident full-resolution slice.
            last_source = getattr(self, "_last_update_source", "")
            wheel_rgb = None
            get_color = getattr(self.color_wheel, "get_color", None)
            if callable(get_color):
                wheel_rgb = get_color()
            if last_source != "wheel" and wheel_rgb != (r, g, b):
                self.color_wheel.set_color(r, g, b, block_signals=True)
            else:
                self.color_wheel.update()
            sync_page = getattr(self, "_sync_wheel_page", None)
            if callable(sync_page):
                sync_page(self.color_wheel)
            if hasattr(self, "_schedule_lab_prerender"):
                self._schedule_lab_prerender(50)
        adjust = getattr(self, "_adjust_content_height", None)
        if callable(adjust):
            adjust()
        self.update()

    def _show_settings_window(self, page_index: int | None = None):
        """Ensure the settings window exists and is shown (no-op if already up)."""
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if page_index is not None and hasattr(self.settings_sidebar, "nav"):
            self.settings_sidebar.nav.setCurrentRow(page_index)
        self.settings_sidebar.refresh_ui()
        if not self.settings_window.isVisible():
            self.settings_window.show_near_main_window()
        else:
            self.settings_window.raise_()

    def toggle_settings_sidebar(self):
        # Lazy-create the independent settings window on first use
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if self.settings_window.isVisible():
            self.settings_window.hide()
        else:
            self._show_settings_window()
        # Keep the no-focus window flags in sync with settings visibility.
        # The main window keeps NOACTIVATE either way (settings use their own
        # top-level window) — toggling them here would let this window steal
        # the drawing app's foreground and cost the next stroke its pressure.
        self.update_window_flags()
        self.update_no_focus_policies()

    def _schedule_module_layout_refresh(self):
        """Coalesce slider reordering and geometry work during rapid module clicks."""
        self._module_layout_refresh_pending = True
        if not self._module_layout_timer.isActive():
            self._module_layout_timer.start(0)

    def _flush_module_layout_refresh(self):
        if not self._module_layout_refresh_pending:
            return
        self._module_layout_refresh_pending = False
        self.refresh_slider_visibility_and_order()

    def _schedule_module_config_save(self):
        """Batch config writes so a click burst performs one disk write."""
        self._module_save_pending = True
        self._module_save_timer.start(120)

    def _flush_module_config_save(self):
        if not self._module_save_pending:
            return
        self._module_save_pending = False
        config.save_hotkey_config(self.cfg)

    def _apply_module(self, module_name: str):
        """Apply a color-space module without blocking the click handler."""
        if module_name not in _MODULE_DEFS:
            module_name = "hsv"
        self._current_module = module_name
        self.cfg["colorSpaceModule"] = module_name
        # Persist and reflow on coalesced timers: rapid clicks update the wheel
        # and button immediately, while one final layout pass handles the burst.
        self._schedule_module_config_save()
        # Switch the page's own wheel. Each module keeps a separate wheel so a
        # floating page does not have to share one with the docked view.
        wheel_mode = _MODULE_DEFS[module_name]["wheel"]
        panel = getattr(self, "wheel_panels", {}).get(module_name)
        if panel is not None:
            self.color_wheel = panel.wheel
            self.color_wheel.set_wheel_mode(wheel_mode)
            sync_page = getattr(self, "_sync_wheel_page", None)
            if callable(sync_page):
                sync_page(self.color_wheel)
        else:
            self.color_wheel.set_wheel_mode(wheel_mode)
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_module_layout_refresh()
        # Update module button label & the page stack
        self._update_module_button_label()
        sync_stack = getattr(self, "_sync_wheel_stack", None)
        if callable(sync_stack):
            sync_stack()
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()
        # Notify sidebar if it's open
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar.notify_module_changed()

    def _next_module(self):
        """Cycle to the next module in _MODULE_ORDER."""
        try:
            idx = _MODULE_ORDER.index(self._current_module)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(_MODULE_ORDER)
        self._apply_module(_MODULE_ORDER[next_idx])

    def refresh_slider_visibility_and_order(self, defer: bool = False):
        """Re-assemble the slider column: order, visibility, height.

        This is the heaviest routine in the panel system — it re-mounts every
        panel through the host, re-saves the arrangement and runs the height
        policy. A gesture that moves several panels (a whole floating window
        dropped on the column, "float them all") used to call it once per
        panel, so one drop re-mounted the column eight times and wrote the
        config six times.

        With *defer* the work is coalesced into one pass on the next event
        loop turn: callers that are in the middle of a multi-panel gesture say
        so, and the pass that actually runs sees the final state. The result
        is identical, only the intermediate ones are skipped.
        """
        if defer:
            self._panel_refresh_pending = True
            timer = getattr(self, "_panel_refresh_timer", None)
            if timer is None:
                from PyQt6.QtCore import QTimer
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._run_panel_refresh)
                self._panel_refresh_timer = timer
            timer.start(0)
            return
        self._run_panel_refresh()

    def _flush_panel_refresh(self) -> None:
        """Run a coalesced pass now, if one is waiting."""
        if getattr(self, "_panel_refresh_pending", False):
            self._run_panel_refresh()

    def _run_panel_refresh(self):
        self._panel_refresh_pending = False
        # Take the blocks out of whatever holds them; the panel host mounts
        # them again below (they were added by hand before the host existed).
        for group in config.SLIDER_GROUPS:
            self.sliders_layout.removeWidget(self.slider_containers[group])

        # Display order comes from the panel arrangement (ui/panels): the
        # tree is built from the same shared config helper the settings UI
        # uses, so reordering there still matches this layout — but the
        # arrangement is now data, which is what a dock host can consume.
        groups = self._slider_groups_in_layout_order()

        # Module-aware filtering: only the current module's slider set is
        # eligible; force-hide everything outside it.
        module_key = getattr(self, "_current_module", "hsv")
        allowed = set(_MODULE_DEFS.get(module_key, _MODULE_DEFS["hsv"])["sliders"])

        title_bar = getattr(self, "title_bar", None)
        sync_grips = getattr(title_bar, "sync_panel_grips", None)
        if callable(sync_grips):
            sync_grips(bool(self.cfg.get("panelDrag", False)))
        host = getattr(self, "panel_host", None)
        mounted = None
        if host is not None:
            # Grips first: switching drag mode re-mounts, so doing it after
            # set_tree would throw the fresh arrangement away and rebuild it.
            host.set_drag_enabled(bool(self.cfg.get("panelDrag", False)))
            host.set_allow_tab_drops(bool(self.cfg.get("slidersTabs", False)))
            mounted = self._slider_column_tree_for(groups)
            host.set_tree(mounted)
        desired_visibility: dict[str, bool] = {}
        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            elif g not in allowed:
                visible = False  # force-hide: not in this module's set
            else:
                visible = self.cfg.get(f"showSliders{g}", True)
            desired_visibility[g] = bool(visible)
            cont = self.slider_containers[g]
            # Never show a parentless container when a panel_host manages layout
            # (e.g. while owned/about to be mounted by pickerDocks): showing a
            # parentless QWidget promotes it into a top-level HWND at (0, 0).
            if not visible or host is None or cont.parentWidget() is not None:
                cont.setVisible(visible)
            if host is None:
                self.sliders_layout.addWidget(cont)

        # An empty column must take no room at all: with every panel torn
        # off — or with only switched-off groups left in it — its margins and
        # grips alone kept a band of nothing under the picker. "Empty" means
        # nothing the user can see, not just nothing mounted: the leftover
        # grey strip (with a dead 历史颜色 grip) after dragging the visible
        # groups out into their own window was exactly this.
        container = getattr(self, "sliders_container", None)
        if container is not None and host is not None:
            visible = host.visible_panels()
            container.setVisible(bool(visible))

        pdrop = getattr(self, "picker_drop", None)
        if pdrop is not None:
            pdrop.sync_docks(remount=True)
            if host is not None:
                for g, visible in desired_visibility.items():
                    cont = self.slider_containers[g]
                    if cont.parentWidget() is not None or not visible:
                        cont.setVisible(visible)

        # Record what was actually mounted, so a drag-reorder survives a
        # restart. Without a host there is nothing assembled to record, and
        # the mixin falls back to the derived column.
        record = getattr(self, "save_panel_layout", None)
        if callable(record):
            record(mounted)

        refresh_floating = getattr(self, "refresh_floating_panels_settings", None)
        if callable(refresh_floating):
            refresh_floating()

        # The picker grip follows the same 面板抓手 switch as the sliders.
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()

        # Recalculate layout geometries since height changed
        self.update_geometries()
        self._adjust_content_height()

    def _slider_column_tree_for(self, groups):
        """The slider column as a dock tree, in this order.

        With slidersTabs it is pages behind tabs, otherwise one content-sized
        stack (the classic layout). A saved arrangement — what the user
        dragged into place — outranks both, but only while it belongs
        to the same switch and still places exactly the same panels.
        """
        from ui.panels import registry, store
        from ui.panels import tree as dock

        scale = self.cfg.get("uiScale", 100) / 100.0
        spacing = int(self.cfg.get("sliderDiffSpace", 8) * scale)
        module_key = getattr(self, "_current_module", "hsv")
        allowed = set(_MODULE_DEFS.get(module_key, _MODULE_DEFS["hsv"])["sliders"])
        tabs = bool(self.cfg.get("slidersTabs", False))
        ids = []
        for group in groups:
            panel_id = (registry.HISTORY if group == "History"
                        else registry.slider_panel_id(group))
            if self.panel_widget(panel_id) is None:
                continue
            # In tabs mode a hidden group must not become a tab page at all —
            # it would show an empty/ghost tab for a slider set the user never
            # turned on (or the current module does not provide).
            if tabs:
                if group == "History":
                    if not self.cfg.get("showSlidersHistory", True):
                        continue
                elif (group not in allowed
                      or not self.cfg.get(f"showSliders{group}", True)):
                    continue
            ids.append(panel_id)
        if tabs:
            # Every page holds up to two groups; a single page is a plain column.
            derived = dock.tabbed_tree(ids, tab_size=2)
        else:
            derived = dock.Split(dock.VERTICAL,
                                 tuple(dock.Leaf(pid) for pid in ids),
                                 (), False, spacing, (0, 0, 0, 0))
        seed = getattr(self, "arrangement_seed", None)
        if not callable(seed):
            return derived
        saved = store.load_from(self.cfg, seed())
        allowed = getattr(self, "_saved_panel_layout_allowed", None)
        if callable(allowed):
            if allowed(saved, derived):
                return saved
        elif set(saved.panels()) == set(derived.panels()):
            return saved
        from ui.window.picker_drop import _subtree_without, picker_dock_panel_ids
        dock_panels = picker_dock_panel_ids(self.cfg)
        if dock_panels:
            trimmed = _subtree_without(derived, dock_panels)
            return trimmed if trimmed is not None else dock.Split(dock.VERTICAL, ())
        return derived

    def _slider_groups_in_layout_order(self):
        """Slider group names in the order the panel arrangement places them.

        Falls back to the config helper when the panel model is unavailable
        (narrow test harnesses bind only a few MainWindow methods).
        """
        from ui.panels import registry

        tree = getattr(self, "panel_layout_tree", None)
        if not callable(tree):
            return config.sorted_slider_groups(self.cfg)
        placed = list(tree().panels())
        by_panel = {}
        for group in config.SLIDER_GROUPS:
            panel_id = (registry.HISTORY if group == "History"
                        else registry.slider_panel_id(group))
            by_panel[panel_id] = group
        ordered = [by_panel[pid] for pid in placed if pid in by_panel]
        # Anything the tree did not mention keeps its config position.
        for group in config.sorted_slider_groups(self.cfg):
            if group not in ordered:
                ordered.append(group)
        return ordered

    def zoom_ui(self, factor):
        w = int(320 * factor)
        h = int(710 * factor)
        self._user_logical_width = w
        self._user_manual_height = h
        self.resize(w, h)
        self._adjust_content_height()

    def show_window_at_cursor(self):
        self._user_hidden = False
        if self.cfg.get("lockWindowPosition", False):
            self.show()
            return
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        
        # Center the window around the cursor
        w, h = self.width(), self.height()
        x = cursor_pos.x() - w // 2
        y = cursor_pos.y() - h // 2
        
        # Keep window inside the available screen geometry
        x = max(geom.x(), min(x, geom.x() + geom.width() - w))
        y = max(geom.y(), min(y, geom.y() + geom.height() - h))
        
        self.move(x, y)
        self.show()

    def _needs_foreground_tracker(self) -> bool:
        sync_software = self.cfg.get("syncSoftware", "auto")
        sync_thread = getattr(self, "sync_thread", None)
        active_mode = getattr(sync_thread, "software_mode", None) if sync_thread is not None else None
        return bool(
            self.cfg.get("onlyShowInCsp", False)
            or self.cfg.get("hideOnPopupMenu", False)
            or sync_software in ("auto", "companion", "csp")
            or active_mode == "companion"
        )

    def init_foreground_tracker(self):
        from PyQt6.QtCore import QTimer
        self.foreground_timer = QTimer(self)
        self.foreground_timer.setInterval(100)
        self.foreground_timer.timeout.connect(self.check_foreground_window)
        # Poll while onlyShowInCsp, hideOnPopupMenu, auto sync, or CSP companion watch is active
        if self._needs_foreground_tracker():
            self.foreground_timer.start()
            self.check_foreground_window()

    def check_foreground_window(self):
        if getattr(self, "_qr_scan_hiding", False):
            return

        only_show_in_csp = bool(self.cfg.get("onlyShowInCsp", False))
        hide_on_popup_menu = bool(self.cfg.get("hideOnPopupMenu", False))
        sync_software = self.cfg.get("syncSoftware", "auto")
        is_auto_sync = bool(sync_software == "auto")
        sync_thread = getattr(self, "sync_thread", None)
        active_mode = getattr(sync_thread, "software_mode", None) if sync_thread is not None else None
        is_companion_watch = bool(
            sync_software in ("companion", "csp")
            or active_mode == "companion"
            or getattr(self, "_companion_dialog", None) is not None
            or getattr(self, "_qr_obscuring_hidden", False)
            or getattr(self, "_qr_hidden_settings", False)
        )
        if (not only_show_in_csp and not hide_on_popup_menu
                and not is_auto_sync and not is_companion_watch):
            return

        try:
            import win32gui
            import win32process
        except ImportError:
            return

        hwnd = win32gui.GetForegroundWindow()
        is_drawing_active = False
        tid = 0
        pid = 0
        win_class = ""
        detected_app = None

        if hwnd:
            try:
                title = (win32gui.GetWindowText(hwnd) or "")
            except Exception:
                title = ""

            try:
                win_class = (win32gui.GetClassName(hwnd) or "")
            except Exception:
                win_class = ""

            try:
                tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pass

            if pid:
                # Cache the resolved exe per PID so an unchanged foreground
                # window doesn't re-query the process on every tick.
                if getattr(self, "_fg_exe_cache_pid", None) == pid:
                    exe_name = getattr(self, "_fg_exe_cache", "")
                else:
                    exe_name = _resolve_process_exe(pid)
                    if exe_name:
                        self._fg_exe_cache_pid = pid
                        self._fg_exe_cache = exe_name
            else:
                exe_name = ""

            # Multi-layer identification of foreground drawing software
            detected_app = identify_drawing_app(
                exe_name=exe_name,
                title=title,
                win_class=win_class,
            )
            if detected_app is not None:
                is_drawing_active = True
                if pid:
                    self._last_drawing_pid = pid

        # A foreground window owned by this process (main window, settings
        # window or picker overlay) means the user is interacting with us.
        is_our_focused = bool(pid and pid == os.getpid())

        # Auto-switch sync software channel when a drawing app is active
        if is_auto_sync and detected_app is not None:
            sync_thread = getattr(self, "sync_thread", None)
            if sync_thread is not None:
                c_sync = getattr(sync_thread, "companion_sync", None)
                has_session = (
                    getattr(c_sync, "has_session", None) or getattr(c_sync, "_has_session", None)
                )
                has_session_val = bool(has_session()) if callable(has_session) else False
                current_mode = getattr(sync_thread, "software_mode", None)
                target_mode = resolve_auto_sync_mode(
                    detected_app,
                    current_mode=current_mode,
                    has_companion_session=has_session_val,
                )
                if target_mode and current_mode != target_mode:
                    switch_fn = getattr(self, "switch_sync_software_mode", None)
                    if callable(switch_fn):
                        switch_fn(target_mode)
                    else:
                        sync_thread.set_software_mode(target_mode)

                if detected_app == "csp" and not has_session_val:
                    maybe_prompt = getattr(self, "maybe_prompt_companion_connection", None)
                    if callable(maybe_prompt):
                        maybe_prompt()

        if detected_app == "csp":
            if (
                getattr(self, "_companion_reconnect_pending", False)
                and not getattr(self, "_companion_reconnect_prompted", False)
                and not getattr(self, "_companion_prompt_scheduled", False)
            ):
                maybe_reprompt = getattr(self, "maybe_prompt_companion_reconnection", None)
                if callable(maybe_reprompt):
                    from PyQt6.QtCore import QTimer
                    self._companion_prompt_scheduled = True
                    QTimer.singleShot(800, maybe_reprompt)
        elif detected_app is not None and detected_app != "csp":
            dlg = getattr(self, "_companion_dialog", None)
            if dlg is not None and getattr(dlg, "_auto_prompt", False):
                try:
                    dlg.reject()
                    self._companion_reconnect_prompted = False
                    self._companion_reconnect_pending = True
                except Exception:
                    pass

        dlg = getattr(self, "_companion_dialog", None)
        if (
            only_show_in_csp
            and dlg is not None
            and getattr(dlg, "_auto_prompt", False)
            and not getattr(dlg, "is_scanning", lambda: False)()
        ):
            if is_drawing_active or is_our_focused:
                if getattr(self, "_companion_dialog_fg_hidden", False) and not getattr(self, "_qr_hidden_dialog", False):
                    self._companion_dialog_fg_hidden = False
                    from ui.window.sync_mixin import _show_without_stealing_focus
                    _show_without_stealing_focus(dlg, raise_win=True)
            else:
                if dlg.isVisible():
                    self._companion_dialog_fg_hidden = True
                    dlg.hide()

        sync_qr = getattr(self, "_sync_qr_overlap_visibility", None)
        if callable(sync_qr):
            sync_qr(_in_foreground_tick=True)

        menu_open = False
        if hide_on_popup_menu and not is_our_focused:
            # Never treat Colorink's own active QMenu popup as a drawing-app menu
            app_has_popup = False
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None and app.activePopupWidget() is not None:
                    app_has_popup = True
            except Exception:
                pass
            if not app_has_popup:
                last_drawing_pid = getattr(self, "_last_drawing_pid", None)
                same_drawing_pid = bool(pid and last_drawing_pid and pid == last_drawing_pid)
                is_menu_fg = bool(
                    hwnd
                    and _is_popup_menu_class(win_class, same_process=same_drawing_pid)
                    and (not pid or same_drawing_pid or win_class.strip().lower() == "#32768")
                )
                probe_pid = pid or (last_drawing_pid if not hwnd else 0)
                if is_drawing_active or is_menu_fg or (not hwnd and last_drawing_pid):
                    menu_open = is_drawing_app_menu_open(
                        hwnd,
                        tid=tid,
                        pid=probe_pid,
                        win32gui_module=win32gui,
                        win32process_module=win32process,
                    )

        if only_show_in_csp or hide_on_popup_menu:
            if only_show_in_csp:
                should_be_visible = is_drawing_active or is_our_focused
            else:
                should_be_visible = True

            if hide_on_popup_menu and menu_open and not is_our_focused:
                should_be_visible = False

            # Keep the window up during an active color pick
            picker = getattr(self, "picker_overlay", None)
            if picker is not None and picker.is_active:
                should_be_visible = True

            # Temporarily hide when overlapping CSP's Companion QR dialog
            if getattr(self, "_qr_obscuring_hidden", False):
                should_be_visible = False

            # If follow_mouse_active is enabled and the window is visible, avoid auto-hiding it.
            if (getattr(self, "follow_mouse_active", False) and self.isVisible()
                    and not only_show_in_csp
                    and not (hide_on_popup_menu and menu_open)):
                should_be_visible = True

            if should_be_visible:
                # 用户手动隐藏后不强制显示；再次手动显示时清除 _user_hidden。
                if not self.isVisible() and not getattr(self, "_user_hidden", False):
                    # Preserve drawing-app focus when auto-restoring the palette
                    has_attr_api = hasattr(self, "testAttribute") and hasattr(self, "setAttribute")
                    prev_no_activate = (
                        self.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                        if has_attr_api
                        else None
                    )
                    if has_attr_api and not prev_no_activate:
                        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                    try:
                        self.show()
                    finally:
                        if has_attr_api and prev_no_activate is False:
                            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
                    self.raise_()
                self.auto_hidden = False
            else:
                if self.isVisible():
                    self.hide()
                self.auto_hidden = True

            palette_visible = bool(should_be_visible
                                   and not getattr(self, "_user_hidden", False))
            sync_floating = getattr(self, "set_floating_foreground_visible", None)
            if callable(sync_floating):
                sync_floating(palette_visible)

    def on_settings_saved(self):
        # Reload configs
        self.cfg = config.load_hotkey_config()
        if hasattr(self, "picker_overlay"):
            self.picker_overlay.set_zoom(self.cfg.get("pickerZoom", 6))
        self.update_hotkey_bindings()
        if hasattr(self, "title_bar"):
            self.title_bar.setVisible(self.cfg.get("showTitleBar", True))
        if hasattr(self, "tray_title_action"):
            self.tray_title_action.setChecked(self.cfg.get("showTitleBar", True))
        if hasattr(self, "tray_border_action"):
            self.tray_border_action.setChecked(self.cfg.get("showWindowBorder", True))

        # Update grayscale controller
        PickerActionsMixin._apply_grayscale_settings_from_cfg(self)

        # Update window flags dynamically
        self.update_window_flags()
        self.update_no_focus_policies()

        # Keep the foreground tracker running while onlyShowInCsp, hideOnPopupMenu,
        # auto sync, or CSP companion watch is on
        fg_timer = getattr(self, "foreground_timer", None)
        needs_fn = getattr(self, "_needs_foreground_tracker", None)
        need_tracker = needs_fn() if callable(needs_fn) else bool(
            self.cfg.get("onlyShowInCsp", False)
            or self.cfg.get("hideOnPopupMenu", False)
            or (self.cfg.get("syncSoftware", "auto") in ("auto", "companion", "csp"))
        )
        if need_tracker:
            if fg_timer is not None and not fg_timer.isActive():
                fg_timer.start()
            check_fg = getattr(self, "check_foreground_window", None)
            if callable(check_fg):
                check_fg()
        else:
            if fg_timer is not None:
                fg_timer.stop()

        # Restore visibility if onlyShowInCsp and hideOnPopupMenu are turned off while auto_hidden
        if not self.cfg.get("onlyShowInCsp", False) and not self.cfg.get("hideOnPopupMenu", False):
            if getattr(self, "auto_hidden", False) and not getattr(self, "_user_hidden", False):
                show_fn = getattr(self, "show", None)
                if callable(show_fn):
                    show_fn()
                self.auto_hidden = False
                sync_floating = getattr(self, "set_floating_foreground_visible", None)
                if callable(sync_floating):
                    sync_floating(True)
        
        # Update active software mode in thread
        mode = self.cfg.get("syncSoftware", "auto")
        if mode not in ("auto", "csp", "sai", "udm", "ps", "companion"):
            mode = "auto"
        if mode != "auto":
            switch_fn = getattr(self, "switch_sync_software_mode", None)
            if callable(switch_fn):
                switch_fn(mode)
            else:
                self.sync_thread.set_software_mode(mode)
        else:
            check_fg = getattr(self, "check_foreground_window", None)
            if callable(check_fg):
                check_fg()

        
        # Companion mode / CSP: show setup dialog if no saved session
        is_csp = (mode in ("companion", "csp"))
        if mode == "auto":
            active_m = getattr(self.sync_thread, "software_mode", None)
            if active_m in ("companion", "csp"):
                is_csp = True
        if is_csp:
            c = getattr(self.sync_thread, "companion_sync", None)
            has_session = (getattr(c, "has_session", None) or getattr(c, "_has_session", None)) if c else None
            has_session_val = bool(has_session()) if callable(has_session) else False
            if c is not None and not getattr(c, "_connected", False) and not has_session_val:
                maybe_prompt = getattr(self, "maybe_prompt_companion_connection", None)
                if callable(maybe_prompt):
                    maybe_prompt()
                else:
                    from PyQt6.QtCore import QTimer as _Qt
                    _Qt.singleShot(300, lambda: self._setup_companion_connection())

        # Update settings dialog variables in thread
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.sai2_panel_mode = self.cfg.get("sai2PanelMode", "auto")
        # SAI UI refresh is a single always-on full mode (core.sai2_ui_refresh
        # defaults to it); there is no config knob and nothing here overrides it.
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        setattr(self.sync_thread, "ps_version", self.cfg.get("psVersion", "auto"))
        self.sync_thread.update_versions()
        
        # Update follow mouse state
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        
        # Apply color-space module (overrides legacy colorWheelMode/wheelMode)
        module = self.cfg.get("colorSpaceModule", self._current_module)
        if module != self._current_module:
            self._apply_module(module)
        else:
            # Even if module didn't change, re-apply slider visibility in case
            # individual toggles were changed
            self.refresh_slider_visibility_and_order()

        for _wheel_panel in getattr(self, "wheel_panels", {}).values():
            _wheel_panel.wheel.reload_config()

        # Update lab visualizer mode. The plane's own axis limit is owned by
        # LabSquare.set_render_mode (110.0 / 0.3); this used to also write a
        # "labVisualizerMaxVal" config key that nothing ever read and whose
        # oklab value (0.4) disagreed with the real limit.
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            old_viz_mode = getattr(self.lab_square, "render_mode", "lab")
            self.lab_square.set_render_mode(viz_mode)
            if old_viz_mode != viz_mode and hasattr(self, "current_rgb"):
                r, g, b = self.current_rgb
                self.lab_square.set_color(r, g, b, block_signals=True)
                if hasattr(self, "lab_slider") and self.lab_slider is not None:
                    self.lab_slider.set_lightness(self.lab_square.L)
                lab_slider_column = getattr(self, "lab_slider_column", None)
                if (lab_slider_column is not None and lab_slider_column.isVisible()
                        and hasattr(self, "_schedule_lab_gamut_range")):
                    self._schedule_lab_gamut_range(50)
            self.lab_square.set_shape(self.cfg.get("labViewShape", "square"))
            self.lab_square.set_harmony_mode(self.cfg.get("labHarmonyMode", "analogous"))
            self._update_lab_shape_button()
            self._update_lab_harmony_button()
        sync_lab = getattr(self, "_sync_floating_lab_settings", None)
        if callable(sync_lab):
            sync_lab()
        if hasattr(self, 'pane_lab'):
            self.pane_lab.set_show_checkerboard(self.cfg.get("showLabCheckerboard", True))

        # Update module button / tab bar visibility
        if hasattr(self, 'btn_module'):
            show_btn = self.cfg.get("showModuleSwitchButton", True)
            self.btn_module.setVisible(show_btn)
            self._update_module_button_label()
        _sync_tabs = getattr(self, "_sync_picker_tab_bar", None)
        if callable(_sync_tabs):
            _sync_tabs()

        readout_mode = self.cfg.get("colorValueReadoutMode", "rgb")
        iter_ro = getattr(self, "_iter_readouts", None)
        readouts = iter_ro() if callable(iter_ro) else (
            getattr(self, "readout_wheel", None),
            getattr(self, "readout_lab", None),
        )
        for ro in readouts:
            if ro is not None:
                ro.retranslate()
                ro.set_mode(readout_mode, emit=False)
        update_vis = getattr(self, "update_mode_buttons_visibility", None)
        if callable(update_vis):
            update_vis()

        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.apply_theme()
        _sync_float = getattr(self, "_sync_floating_wheel_chrome", None)
        if callable(_sync_float):
            _sync_float()
        
        # Apply scaling zoom factor only if the target scale configuration has changed
        target_scale = self.cfg.get("uiScale", 100)
        if getattr(self, "current_ui_scale", 100) != target_scale:
            self.zoom_ui(target_scale / 100.0)
            self.current_ui_scale = target_scale
        else:
            self.update()
        # Reapply ringless layout after config/settings reload.
        # Mode OFF restores full ring/circles/bottom-right immediately.
        self._sync_ringless_mode()
        update_lab_avoid = getattr(self, "_update_lab_avoid", None)
        if callable(update_lab_avoid):
            update_lab_avoid()
        # Windows already torn off must follow the no-focus setting too.
        refresh_focus = getattr(self, "refresh_floating_focus", None)
        if callable(refresh_focus):
            refresh_focus()
        refresh_floating = getattr(self, "refresh_floating_panels_settings", None)
        if callable(refresh_floating):
            refresh_floating()
        # Reset manual override on settings save so spacing/gap adjustments
        # immediately resize the window to match the new configuration.
        self._manual_height_override = False
        self._adjust_content_height()

    def _apply_ws_ex_noactivate(self, enabled: bool) -> None:
        """Add or remove WS_EX_NOACTIVATE on the native window.

        Qt's WindowDoesNotAcceptFocus is not always enough on Windows, so the
        extended style is forced directly and refreshed with SetWindowPos so
        the change takes effect immediately.
        """
        # Same treatment the floating panel windows need, so it lives in one
        # place now (ui/panels/floating.apply_no_activate): it skips windows
        # with no native handle yet, because at startup update_window_flags()
        # runs before WA_TranslucentBackground is set and creating winId()
        # too early would break transparency. showEvent() / WinIdChange re-applies it.
        from ui.panels.floating import apply_no_activate

        apply_no_activate(self, enabled)

    def _apply_ws_ex_appwindow(self, enabled: bool) -> None:
        """Add or remove WS_EX_APPWINDOW on the native window.

        Windows hides WS_EX_NOACTIVATE windows from the taskbar by default
        unless WS_EX_APPWINDOW is explicitly set.
        """
        from ui.panels.floating import apply_taskbar_appwindow

        apply_taskbar_appwindow(self, enabled)

    def _apply_win32_window_styles(self) -> None:
        """Sync native Win32 extended styles (no-activate & taskbar visibility)."""
        cfg = getattr(self, "cfg", {})
        self._apply_ws_ex_noactivate(bool(cfg.get("noFocusMode", False)))
        self._apply_ws_ex_appwindow(bool(cfg.get("showTaskbarIcon", False)))

    def update_window_flags(self):
        show_taskbar = bool(self.cfg.get("showTaskbarIcon", False))
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if show_taskbar:
            flags |= Qt.WindowType.Window
        else:
            flags |= Qt.WindowType.Tool

        # No-focus mode is a global preference — keep it on even while the
        # settings UI is open.  Settings now live in their own top-level
        # SettingsWindow, so the main window never needs to accept focus on
        # its behalf.  Dropping NOACTIVATE here used to let the main window
        # grab Photoshop's foreground when it was clicked with settings open;
        # PS then closes and reopens its Wintab context, and the next stylus
        # stroke starts without pressure.
        no_focus = bool(self.cfg.get("noFocusMode", False))
        if no_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, no_focus)

        if self.windowFlags() != flags:
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            # Re-apply native extended styles before re-showing so Windows
            # sees WS_EX_APPWINDOW / WS_EX_NOACTIVATE on ShowWindow().
            self._apply_win32_window_styles()
            if was_visible:
                # Re-show without activating: this is a programmatic flag
                # refresh (e.g. opening settings while no-focus is enabled),
                # not a user-initiated show, so it must not steal focus.
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                self.show()
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, no_focus)

        # Double safety: force WS_EX_NOACTIVATE / WS_EX_APPWINDOW via Win32 API.
        self._apply_win32_window_styles()

    def update_no_focus_policies(self):
        # Mirror update_window_flags(): the no-focus preference no longer
        # depends on settings visibility (settings own a separate window), so
        # the main window keeps refusing focus for Photoshop's sake.
        enabled = bool(self.cfg.get("noFocusMode", False))

        policy = Qt.FocusPolicy.NoFocus if enabled else Qt.FocusPolicy.StrongFocus

        # Prevent Qt from activating the top-level window when it is shown.
        # Combined with WS_EX_NOACTIVATE this keeps the drawing app focused
        # while the picker is used (无焦点取色模式).
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, enabled)

        self.setFocusPolicy(policy)
        for _wheel_panel in getattr(self, "wheel_panels", {}).values():
            _wheel_panel.wheel.setFocusPolicy(policy)
        if hasattr(self, 'color_wheel'):
            self.color_wheel.setFocusPolicy(policy)
        if hasattr(self, 'lab_square'):
            self.lab_square.setFocusPolicy(policy)
        if hasattr(self, 'lab_slider'):
            self.lab_slider.setFocusPolicy(policy)
        if hasattr(self, 'preview_box'):
            self.preview_box.setFocusPolicy(policy)
        
        if hasattr(self, 'slider_widgets'):
            for chan, (slider, val_label) in self.slider_widgets.items():
                slider.setFocusPolicy(policy)
            
        if hasattr(self, 'title_bar'):
            for btn in [self.title_bar.btn_settings, self.title_bar.btn_close, self.title_bar.btn_min]:
                btn.setFocusPolicy(policy)

    # ── Fullscreen & Window / Region Grayscale Filter ────────────────────

    def _apply_grayscale_settings_from_cfg(self) -> None:
        """Synchronize fullscreen and region grayscale controllers with ``self.cfg``."""
        new_backend = self.cfg.get("grayscaleFilterBackend", "dcomp")
        if new_backend not in ("dcomp", "native", "mag"):
            new_backend = "dcomp"
        new_mode = self.cfg.get("grayscaleFilterMode", "oklch")
        if new_mode not in ("oklch", "luma"):
            new_mode = "oklch"
        cls_name = type(self.grayscale_overlay).__name__
        if cls_name == "DCompGrayscaleController":
            current_backend = "dcomp"
        elif cls_name == "MagFilterController":
            current_backend = "mag"
        else:
            current_backend = "native"

        was_fs_active = self.grayscale_overlay.is_active is True
        if new_backend != current_backend:
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
            if new_backend == "mag":
                from core.mag_grayscale import MagFilterController
                self.grayscale_overlay = MagFilterController(mode="luma")
            elif new_backend == "native":
                from core.native_grayscale import NativeGrayscaleController
                self.grayscale_overlay = NativeGrayscaleController(mode=new_mode)
            else:  # dcomp
                from core.dcomp_grayscale import DCompGrayscaleController
                dcomp_ctrl = DCompGrayscaleController(mode=new_mode)
                if dcomp_ctrl.is_available and dcomp_ctrl.is_healthy:
                    self.grayscale_overlay = dcomp_ctrl
                else:
                    from core.native_grayscale import NativeGrayscaleController
                    fallback = NativeGrayscaleController(mode=new_mode)
                    if fallback.is_available:
                        self.grayscale_overlay = fallback
                    else:
                        from core.mag_grayscale import MagFilterController
                        self.grayscale_overlay = MagFilterController(mode="luma")
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)
        self.grayscale_overlay.set_mode(
            "luma" if new_backend == "mag" else new_mode
        )
        if new_backend != current_backend and was_fs_active:
            if not self.grayscale_overlay.set_active(True):
                err = getattr(self.grayscale_overlay, "last_error", "")
                if err:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, "灰度滤镜", err)
        region_ctrl = getattr(self, "region_grayscale_overlay", None)
        if region_ctrl is not None:
            from core.region_grayscale import (
                active_region_tuples,
                read_region_entries_from_config,
            )
            region_ctrl.set_backend("mag" if new_backend == "mag" else "auto")
            region_ctrl.set_target(screen_target)
            region_ctrl.set_mode("luma" if new_backend == "mag" else new_mode)
            region_ctrl.set_regions(active_region_tuples(read_region_entries_from_config(self.cfg)))
        sync_ui = getattr(self, "_sync_sidebar_grayscale_ui", None)
        if callable(sync_ui):
            sync_ui()

    def _sync_sidebar_grayscale_ui(self) -> None:
        sidebar = getattr(self, "settings_sidebar", None)
        if sidebar is None:
            return
        sidebar.cfg["grayscaleFilterRegions"] = list(self.cfg.get("grayscaleFilterRegions") or [])
        sidebar.cfg["grayscaleFilterRegion"] = self.cfg.get("grayscaleFilterRegion")
        refresh_region = getattr(sidebar, "_refresh_grayscale_region_ui", None)
        if callable(refresh_region):
            refresh_region()
        refresh_status = getattr(sidebar, "_refresh_grayscale_status_ui", None)
        if callable(refresh_status):
            refresh_status()

    def toggle_fullscreen_grayscale(self) -> bool:
        """Toggle the fullscreen grayscale filter with automatic Mag fallback."""
        fs_ctrl = getattr(self, "grayscale_overlay", None)
        if fs_ctrl is None:
            return False
        try:
            region_ctrl = getattr(self, "region_grayscale_overlay", None)
            if region_ctrl is not None and region_ctrl.is_active:
                region_ctrl.set_active(False)
            result = fs_ctrl.toggle()
            if result is False:
                from core.mag_grayscale import MagFilterController
                if not isinstance(fs_ctrl, MagFilterController):
                    backend_name = type(fs_ctrl).__name__
                    err = getattr(fs_ctrl, "last_error", "")
                    print(f"[Grayscale] Backend {backend_name} failed ({err}), falling back to Mag backend...")
                    mag_ctrl = MagFilterController(mode="luma")
                    if mag_ctrl.is_available:
                        try:
                            fs_ctrl.set_active(False)
                            close_fn = getattr(fs_ctrl, "close", None)
                            if callable(close_fn):
                                close_fn()
                        except Exception:
                            pass
                        self.grayscale_overlay = mag_ctrl
                        self.cfg["grayscaleFilterBackend"] = "mag"
                        config.save_hotkey_config(self.cfg)
                        result = self.grayscale_overlay.toggle()
                        if result:
                            print("[Grayscale] Successfully fell back to Mag filter backend")
                            self._sync_sidebar_grayscale_ui()
                            return True
                err = getattr(self.grayscale_overlay, "last_error", "")
                if err:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, "灰度滤镜", err)
            self._sync_sidebar_grayscale_ui()
            return bool(result)
        except Exception as exc:
            print(f"[Grayscale] Fullscreen toggle error: {exc}")
            from core.mag_grayscale import MagFilterController
            if not isinstance(getattr(self, "grayscale_overlay", None), MagFilterController):
                try:
                    mag_ctrl = MagFilterController(mode="luma")
                    if mag_ctrl.is_available:
                        self.grayscale_overlay = mag_ctrl
                        self.cfg["grayscaleFilterBackend"] = "mag"
                        config.save_hotkey_config(self.cfg)
                        if self.grayscale_overlay.toggle():
                            self._sync_sidebar_grayscale_ui()
                            return True
                except Exception:
                    pass
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "灰度滤镜", f"切换失败: {exc}")
            self._sync_sidebar_grayscale_ui()
            return False

    def toggle_region_grayscale(self) -> bool:
        """Toggle the window/region grayscale filter (maximized by default or custom regions)."""
        region_ctrl = getattr(self, "region_grayscale_overlay", None)
        if region_ctrl is None:
            return False
        try:
            # Deactivate fullscreen grayscale first if we are about to activate region grayscale
            if not region_ctrl.is_active:
                fs_ctrl = getattr(self, "grayscale_overlay", None)
                if fs_ctrl is not None and fs_ctrl.is_active:
                    fs_ctrl.set_active(False)
                from core.region_grayscale import (
                    active_region_tuples,
                    read_region_entries_from_config,
                )
                entries = read_region_entries_from_config(self.cfg)
                region_ctrl.set_regions(active_region_tuples(entries))
            ok = region_ctrl.toggle()
            if not ok and region_ctrl.last_error:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "区域灰度滤镜", region_ctrl.last_error)
            self._sync_sidebar_grayscale_ui()
            return ok
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "区域灰度滤镜", f"切换区域灰度失败: {exc}")
            self._sync_sidebar_grayscale_ui()
            return False

    def _ensure_region_grayscale_selector(self):
        selector = getattr(self, "region_grayscale_selector", None)
        if selector is None:
            from ui.region_grayscale_selector import RegionGrayscaleSelector
            selector = RegionGrayscaleSelector(None)
            selector.regionsSelected.connect(self._on_grayscale_regions_selected)
            selector.regionReplaced.connect(self._on_grayscale_region_replaced)
            selector.regionReset.connect(self._on_grayscale_region_reset)
            selector.canceled.connect(self._on_grayscale_selection_canceled)
            self.region_grayscale_selector = selector
        return selector

    def _restore_after_region_selection(self, activate_region: bool | None = None) -> None:
        resume = getattr(self, "_resume_grayscale_after_select", None)
        self._resume_grayscale_after_select = None

        if activate_region is True:
            region_ctrl = getattr(self, "region_grayscale_overlay", None)
            if region_ctrl is not None and not region_ctrl.is_active:
                fs_ctrl = getattr(self, "grayscale_overlay", None)
                if fs_ctrl is not None and fs_ctrl.is_active:
                    fs_ctrl.set_active(False)
                if not region_ctrl.set_active(True):
                    err = getattr(region_ctrl, "last_error", "")
                    if err:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, "区域灰度滤镜", err)
        elif activate_region is None:
            if resume == "region":
                region_ctrl = getattr(self, "region_grayscale_overlay", None)
                if region_ctrl is not None and not region_ctrl.is_active:
                    if not region_ctrl.set_active(True):
                        err = getattr(region_ctrl, "last_error", "")
                        if err:
                            from PyQt6.QtWidgets import QMessageBox
                            QMessageBox.warning(self, "区域灰度滤镜", err)
            elif resume == "fullscreen":
                fs_ctrl = getattr(self, "grayscale_overlay", None)
                if fs_ctrl is not None and not fs_ctrl.is_active:
                    fs_ctrl.set_active(True)

        if getattr(self, "_restore_settings_after_select", False):
            self._restore_settings_after_select = False
            sw = getattr(self, "settings_window", None)
            if sw is not None:
                sw.show()
                sw.raise_()
        self._sync_sidebar_grayscale_ui()

    def apply_grayscale_regions(self, entries: list, activate: bool = False) -> list[dict]:
        """Persist region entries and sync the live RegionGrayscaleController + sidebar."""
        from core.region_grayscale import (
            active_region_tuples,
            write_regions_to_config,
        )
        normalized = write_regions_to_config(self.cfg, entries)
        config.save_hotkey_config(self.cfg)
        region_ctrl = getattr(self, "region_grayscale_overlay", None)
        if region_ctrl is not None:
            if activate:
                fs_ctrl = getattr(self, "grayscale_overlay", None)
                if fs_ctrl is not None and fs_ctrl.is_active:
                    fs_ctrl.set_active(False)
            region_ctrl.set_regions(active_region_tuples(normalized))
            if activate and not region_ctrl.is_active:
                region_ctrl.set_active(True)
        self._sync_sidebar_grayscale_ui()
        return normalized

    def start_select_grayscale_region(self, replace_index: int | None = None) -> None:
        """Launch the interactive fullscreen multi-region selector for the grayscale filter."""
        from core.region_grayscale import read_region_entries_from_config

        selector = self._ensure_region_grayscale_selector()

        region_ctrl = getattr(self, "region_grayscale_overlay", None)
        fs_ctrl = getattr(self, "grayscale_overlay", None)
        if region_ctrl is not None and region_ctrl.is_active:
            self._resume_grayscale_after_select = "region"
            region_ctrl.set_active(False)
        elif fs_ctrl is not None and fs_ctrl.is_active:
            self._resume_grayscale_after_select = "fullscreen"
            fs_ctrl.set_active(False)
        else:
            self._resume_grayscale_after_select = None

        sw = getattr(self, "settings_window", None)
        if sw is not None and sw.isVisible():
            self._restore_settings_after_select = True
            sw.hide()
        else:
            self._restore_settings_after_select = False

        entries = read_region_entries_from_config(self.cfg)
        self._selecting_replace_index = replace_index
        selector.start_selection(regions=entries, replace_index=replace_index)

    def _on_grayscale_regions_selected(self, selected_entries: list) -> None:
        """Persist multi-region selection (preserving disabled rows) and activate region grayscale."""
        from core.region_grayscale import read_region_entries_from_config
        existing = read_region_entries_from_config(self.cfg)
        disabled_entries = [dict(e) for e in existing if not e.get("enabled", True)]
        merged = list(selected_entries) + disabled_entries
        self.apply_grayscale_regions(merged, activate=False)
        self._restore_after_region_selection(activate_region=True)

    def _on_grayscale_region_replaced(
        self, index: int, x: int, y: int, width: int, height: int
    ) -> None:
        """Replace a single existing region entry while keeping others and its name intact."""
        from core.region_grayscale import read_region_entries_from_config
        entries = read_region_entries_from_config(self.cfg)
        new_entry: dict = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "enabled": True,
        }
        if 0 <= index < len(entries):
            old_name = entries[index].get("name", "")
            if old_name:
                new_entry["name"] = old_name
            entries[index] = new_entry
        else:
            entries.append(new_entry)
        self.apply_grayscale_regions(entries, activate=False)
        self._restore_after_region_selection(activate_region=True)

    def _on_grayscale_region_selected(self, x: int, y: int, width: int, height: int) -> None:
        """Backward-compatible single-rectangle selection handler."""
        self._on_grayscale_regions_selected([{
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "enabled": True,
        }])

    def _on_grayscale_region_reset(self) -> None:
        """Callback when user resets to maximized inside the region selector."""
        self.reset_grayscale_region_to_maximized()
        self._restore_after_region_selection(activate_region=False)

    def _on_grayscale_selection_canceled(self) -> None:
        self._restore_after_region_selection(activate_region=None)

    def reset_grayscale_region_to_maximized(self) -> None:
        """Reset the region grayscale filter to its default frameless maximized window."""
        self.apply_grayscale_regions([], activate=False)

