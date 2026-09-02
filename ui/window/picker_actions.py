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
    _resolve_process_exe,
    _title_matches_drawing_app,
)
from ui.lab_harmony import HARMONY_MODE_NAMES
from ui.window.module_defs import _MODULE_DEFS, _MODULE_NAMES, _MODULE_ORDER


class PickerActionsMixin:

    def _init_module_button(self):
        """Create a floating button next to ⊙/△ to cycle HSV→HLS→LCH modules."""
        btn = QPushButton("HSV", self.pane_wheel)
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
        self.btn_lab_shape = QPushButton("□", self.pane_lab)
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
        self._refit_preview_box()
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
        self._update_lab_harmony_button()
        self._sync_settings_sidebar_lab_controls()
        self.update()

    def _update_lab_shape_button(self):
        if not hasattr(self, "btn_lab_shape") or not hasattr(self, "lab_square"):
            return
        is_disc = getattr(self.lab_square, "shape", "square") == "disc"
        self.btn_lab_shape.setText("◯" if is_disc else "□")
        self.btn_lab_shape.setToolTip(
            "切换 LAB 视图形状 (方形 / 圆形)" if not is_disc else "切换 LAB 视图形状 (圆形 / 方形)")

    def _update_lab_harmony_button(self):
        if not hasattr(self, "btn_lab_harmony") or not hasattr(self, "cfg"):
            return
        mode = self.cfg.get("labHarmonyMode", "analogous")
        label = HARMONY_MODE_NAMES.get(mode, "近似")
        if hasattr(self, "btn_lab_harmony"):
            self.btn_lab_harmony.setToolTip(f"LAB 调和模式: {label} (点击切换)")

    def _update_module_button_label(self):
        if hasattr(self, "btn_module"):
            name = {"hsv": "◉", "hls": "△", "lch": "◈"}.get(self._current_module, "◉")
            self.btn_module.setText(name)
            self.btn_module.setToolTip(f"模块: {_MODULE_NAMES.get(self._current_module, 'HSV')} (点击切换)")

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        show_module = self.cfg.get("showModuleSwitchButton", True)
        show_lab_toggle = self.cfg.get("showLabToggleButton", True)
        show_lab_shape_btn = self.cfg.get("showLabShapeButton", True)
        show_lab_harmony_btn = self.cfg.get("showLabHarmonyButton", True)
        if hasattr(self, "pane_wheel"):
            self.pane_wheel.set_module_slot_reserved(show_module)
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

        # Re-pack the visible button cluster after every visibility change so
        # a lone toggle always lands on the outermost edge.
        if hasattr(self, "pane_wheel"):
            self.pane_wheel._reposition_mode_button()
        if hasattr(self, "pane_lab"):
            self.pane_lab._reposition_mode_button()

    def toggle_picker_mode(self):
        """Switch picker panes without re-running the full theme/layout pass."""
        new_index = (self.stack.currentIndex() + 1) % 2
        self.stack.setCurrentIndex(new_index)
        self.update_mode_buttons_visibility()
        # Only the page-local ringless geometry needs to move here. Re-running
        # apply_theme() would rebuild every slider stylesheet and gradient while
        # the user is only asking for a pane switch.
        self._sync_ringless_mode()
        self._update_lab_avoid()
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
            if hasattr(self, "_schedule_lab_prerender"):
                self._schedule_lab_prerender(50)
        self.update()

    def _show_settings_window(self):
        """Ensure the settings window exists and is shown (no-op if already up)."""
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if not self.settings_window.isVisible():
            self.settings_sidebar.refresh_ui()
            self.settings_window.show_near_main_window()

    def toggle_settings_sidebar(self):
        # Lazy-create the independent settings window on first use
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if self.settings_window.isVisible():
            self.settings_window.hide()
        else:
            self._show_settings_window()
        # Keep the no-focus window flags in sync with settings visibility:
        # opening settings disables no-focus so the settings window can be
        # used normally; closing it restores no-focus immediately.
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
        # Update wheel mode
        wheel_mode = _MODULE_DEFS[module_name]["wheel"]
        self.color_wheel.set_wheel_mode(wheel_mode)
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_module_layout_refresh()
        # Update module button label
        self._update_module_button_label()
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

    def refresh_slider_visibility_and_order(self):
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
        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            elif g not in allowed:
                visible = False  # force-hide: not in this module's set
            else:
                visible = self.cfg.get(f"showSliders{g}", True)
            self.slider_containers[g].setVisible(visible)
            if host is None:
                self.sliders_layout.addWidget(self.slider_containers[g])

        # An empty column must take no room at all: with every panel torn
        # off, its margins alone kept ~20px of nothing under the picker, so
        # the window's minimum height no longer hugged the LAB checkerboard.
        container = getattr(self, "sliders_container", None)
        if container is not None and host is not None:
            container.setVisible(bool(host.mounted_panels()))

        # Record what was actually mounted, so a drag-reorder survives a
        # restart. Without a host there is nothing assembled to record, and
        # the mixin falls back to the derived column.
        record = getattr(self, "save_panel_layout", None)
        if callable(record):
            record(mounted)

        # Recalculate layout geometries since height changed
        self.update_geometries()
        self._adjust_content_height()

    def _slider_column_tree_for(self, groups):
        """The slider column as a dock tree, in this order.

        B-4: with slidersSplit enabled it is two draggable columns, with
        slidersTabs it is pages behind tabs, otherwise one content-sized
        stack (the classic layout). A saved arrangement — what the user
        dragged into place — outranks all three, but only while it belongs
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
        elif self.cfg.get("slidersSplit", False):
            derived = dock.two_column_tree(ids, spacing, (0, 0, 0, 0))
        else:
            derived = dock.Split(dock.VERTICAL,
                                 tuple(dock.Leaf(pid) for pid in ids),
                                 (), False, spacing, (0, 0, 0, 0))
        seed = getattr(self, "arrangement_seed", None)
        if not callable(seed):
            return derived
        saved = store.load_from(self.cfg, seed())
        if set(saved.panels()) == set(derived.panels()):
            return saved
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
        self.resize(int(320 * factor), int(710 * factor))
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

    def init_foreground_tracker(self):
        from PyQt6.QtCore import QTimer
        self.foreground_timer = QTimer(self)
        self.foreground_timer.setInterval(400)
        self.foreground_timer.timeout.connect(self.check_foreground_window)
        # Only poll while the feature is enabled; on_settings_saved() starts
        # or stops the timer when the setting changes.
        if self.cfg.get("onlyShowInCsp", False):
            self.foreground_timer.start()
            self.check_foreground_window()

    def check_foreground_window(self):
        # If settings onlyShowInCsp is False, do nothing
        if not self.cfg.get("onlyShowInCsp", False):
            return

        try:
            import win32gui
            import win32process
        except ImportError:
            return

        hwnd = win32gui.GetForegroundWindow()
        is_drawing_active = False
        pid = 0

        if hwnd:
            try:
                title = (win32gui.GetWindowText(hwnd) or "").lower()
            except Exception:
                title = ""

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pass

            if pid:
                # Cache the resolved exe per PID so an unchanged foreground
                # window doesn't re-query the process on every tick.  Only
                # cache successful resolutions: a transient failure (process
                # still starting, antivirus, protected process) must not pin
                # an empty result for the whole foreground session.
                if getattr(self, "_fg_exe_cache_pid", None) == pid:
                    exe_name = getattr(self, "_fg_exe_cache", "")
                else:
                    exe_name = _resolve_process_exe(pid)
                    if exe_name:
                        self._fg_exe_cache_pid = pid
                        self._fg_exe_cache = exe_name
                if _exe_matches_drawing_app(exe_name):
                    is_drawing_active = True

            # Title fallback covers localized windows and cases where the
            # process query was denied.
            if not is_drawing_active and _title_matches_drawing_app(title):
                is_drawing_active = True

        # A foreground window owned by this process (main window, settings
        # window or picker overlay) means the user is interacting with us.
        # The REAL foreground PID (win32) is the source of truth here:
        # Qt's isActiveWindow() bookkeeping is unreliable — in no-focus mode
        # the palette can never become "active", and the separate settings
        # window's activation state can get stuck (activateWindow() denied
        # by the OS foreground lock leaves Qt thinking it is active forever).
        # Trusting that stale state kept the palette visible even when a
        # non-drawing app took the foreground ("仅在画图软件前台时显示"失效).
        is_our_focused = bool(pid and pid == os.getpid())

        should_be_visible = is_drawing_active or is_our_focused

        # Keep the window up during an active color pick
        picker = getattr(self, "picker_overlay", None)
        if picker is not None and picker.is_active:
            should_be_visible = True

        # If follow_mouse_active is enabled and the window is visible, avoid auto-hiding it.
        # But when the user explicitly restricted visibility to the drawing app's
        # foreground (onlyShowInCsp), that restriction wins — otherwise the palette
        # would never hide while following the mouse ("切走不隐藏").
        if (getattr(self, "follow_mouse_active", False) and self.isVisible()
                and not self.cfg.get("onlyShowInCsp", False)):
            should_be_visible = True

        if should_be_visible:
            # 用户手动隐藏（热键/托盘/关闭到托盘）后，前台追踪器不能立刻又把它
            # 拉出来；只有用户再次手动显示时才清除 _user_hidden。
            if not self.isVisible() and not getattr(self, "_user_hidden", False):
                self.show()
                self.raise_()
            self.auto_hidden = False
        else:
            if self.isVisible():
                self.hide()
            # Record that the foreground restriction wants the window hidden
            # even when it is already hidden.  This lets main.py avoid an
            # unconditional show() at startup when onlyShowInCsp is enabled
            # and no drawing app is in the foreground.
            self.auto_hidden = True
        # Torn-off panels are the same palette: hide them with the main
        # window and bring them back when the drawing app returns. The
        # palette only counts as visible when the user did not explicitly
        # hide it — otherwise the tracker would show the floats over an
        # intentionally hidden main window.
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

        # Update grayscale controller and migrate all removed backends to
        # native. Mag remains the system-wide Luma fallback.
        new_backend = self.cfg.get("grayscaleFilterBackend", "native")
        new_backend = "mag" if new_backend == "mag" else "native"
        new_mode = self.cfg.get("grayscaleFilterMode", "oklch")
        if new_mode not in ("oklch", "luma"):
            new_mode = "oklch"
        current_backend = (
            "mag" if type(self.grayscale_overlay).__name__ == "MagFilterController"
            else "native"
        )
        if new_backend != current_backend:
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
            if new_backend == "mag":
                from core.mag_grayscale import MagFilterController
                self.grayscale_overlay = MagFilterController(mode="luma")
            else:
                from core.native_grayscale import NativeGrayscaleController
                self.grayscale_overlay = NativeGrayscaleController(mode=new_mode)
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)
        self.grayscale_overlay.set_mode(
            "luma" if new_backend == "mag" else new_mode
        )

        # Update window flags dynamically
        self.update_window_flags()
        self.update_no_focus_policies()

        # Keep the foreground tracker running only while the feature is on,
        # and apply the new state immediately instead of waiting a tick.
        fg_timer = getattr(self, "foreground_timer", None)
        if self.cfg.get("onlyShowInCsp", False):
            if fg_timer is not None and not fg_timer.isActive():
                fg_timer.start()
            self.check_foreground_window()
        else:
            if fg_timer is not None:
                fg_timer.stop()

        # Restore visibility if onlyShowInCsp is turned off while auto_hidden
        if not self.cfg.get("onlyShowInCsp", False):
            if getattr(self, "auto_hidden", False) and not getattr(self, "_user_hidden", False):
                self.show()
                self.auto_hidden = False
        
        # Update active software mode in thread
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps", "companion"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        # Companion mode: show setup dialog if no saved session
        if mode == "companion":
            c = self.sync_thread.companion_sync
            if not c._connected and not c._has_session():
                from PyQt6.QtCore import QTimer as _Qt
                _Qt.singleShot(300, lambda: self._setup_companion_connection())

        # Update settings dialog variables in thread
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.sai_ui_refresh = self.cfg.get("saiUiRefresh", "full")
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

        self.color_wheel.reload_config()

        # Update lab visualizer mode. The plane's own axis limit is owned by
        # LabSquare.set_render_mode (110.0 / 0.3); this used to also write a
        # "labVisualizerMaxVal" config key that nothing ever read and whose
        # oklab value (0.4) disagreed with the real limit.
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            self.lab_square.set_render_mode(viz_mode)
            self.lab_square.set_shape(self.cfg.get("labViewShape", "square"))
            self.lab_square.set_harmony_mode(self.cfg.get("labHarmonyMode", "analogous"))
            self._update_lab_shape_button()
            self._update_lab_harmony_button()

        # Update module button visibility
        if hasattr(self, 'btn_module'):
            show_btn = self.cfg.get("showModuleSwitchButton", True)
            self.btn_module.setVisible(show_btn)
            self._update_module_button_label()

        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.apply_theme()
        
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
        # Windows already torn off must follow the no-focus setting too.
        refresh_focus = getattr(self, "refresh_floating_focus", None)
        if callable(refresh_focus):
            refresh_focus()
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
        # too early would break transparency. showEvent() re-applies it.
        from ui.panels.floating import apply_no_activate

        apply_no_activate(self, enabled)

    def update_window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if not self.cfg.get("showTaskbarIcon", False):
            flags |= Qt.WindowType.Tool

        # Only apply no-focus mode if settings sidebar is CLOSED
        no_focus = self.cfg.get("noFocusMode", False) and not (hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible())
        if no_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, no_focus)

        if self.windowFlags() != flags:
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            if was_visible:
                # Re-show without activating: this is a programmatic flag
                # refresh (e.g. opening settings while no-focus is enabled),
                # not a user-initiated show, so it must not steal focus.
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                self.show()
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, no_focus)

        # Double safety: force WS_EX_NOACTIVATE via Win32 API, and also clear
        # it when no-focus is disabled so disabling the mode really restores
        # normal activation behavior.
        self._apply_ws_ex_noactivate(no_focus)

    def update_no_focus_policies(self):
        is_settings_open = hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible()
        enabled = self.cfg.get("noFocusMode", False) and not is_settings_open
        
        policy = Qt.FocusPolicy.NoFocus if enabled else Qt.FocusPolicy.StrongFocus

        # Prevent Qt from activating the top-level window when it is shown.
        # Combined with WS_EX_NOACTIVATE this keeps the drawing app focused
        # while the picker is used (无焦点取色模式).
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, enabled)

        self.setFocusPolicy(policy)
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
