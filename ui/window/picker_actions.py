"""Picker/module/foreground/settings actions for the main window.

Extracted from ``ui.main_window``: module switching, picker pane toggling,
settings window management, foreground visibility tracking and dynamic
window-flag updates.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from core import config
from core.foreground import (
    _exe_matches_drawing_app,
    _resolve_process_exe,
    _title_matches_drawing_app,
)
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

    def _update_module_button_label(self):
        if hasattr(self, "btn_module"):
            name = {"hsv": "◉", "hls": "△", "lch": "◈"}.get(self._current_module, "◉")
            self.btn_module.setText(name)
            self.btn_module.setToolTip(f"模块: {_MODULE_NAMES.get(self._current_module, 'HSV')} (点击切换)")

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        show_module = self.cfg.get("showModuleSwitchButton", True)
        show_lab_toggle = self.cfg.get("showLabToggleButton", True)
        if hasattr(self, "pane_wheel"):
            self.pane_wheel.set_module_slot_reserved(show_module)
        if hasattr(self, "pane_lab"):
            self.pane_lab.set_module_slot_reserved(show_module)
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
        else:
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_lab.raise_()
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.hide()
            if hasattr(self, 'btn_module'):
                self.btn_module.hide()

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
        # Remove all from layout
        for group in config.SLIDER_GROUPS:
            self.sliders_layout.removeWidget(self.slider_containers[group])

        # Display order comes from the same shared helper the settings UI
        # uses, so reordering there always matches this layout.
        groups = config.sorted_slider_groups(self.cfg)

        # Module-aware filtering: only the current module's slider set is
        # eligible; force-hide everything outside it.
        module_key = getattr(self, "_current_module", "hsv")
        allowed = set(_MODULE_DEFS.get(module_key, _MODULE_DEFS["hsv"])["sliders"])

        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            elif g not in allowed:
                visible = False  # force-hide: not in this module's set
            else:
                visible = self.cfg.get(f"showSliders{g}", True)
            self.slider_containers[g].setVisible(visible)
            self.sliders_layout.addWidget(self.slider_containers[g])

        # Recalculate layout geometries since height changed
        self.update_geometries()
        self._adjust_content_height()

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

        # Update lab visualizer mode
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            self.lab_square.set_render_mode(viz_mode)
            self.cfg["labVisualizerMaxVal"] = 110 if viz_mode == "lab" else 0.4

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
        self._adjust_content_height()

    def _apply_ws_ex_noactivate(self, enabled: bool) -> None:
        """Add or remove WS_EX_NOACTIVATE on the native window.

        Qt's WindowDoesNotAcceptFocus is not always enough on Windows, so the
        extended style is forced directly and refreshed with SetWindowPos so
        the change takes effect immediately.
        """
        try:
            # Avoid creating the native window just to toggle the style: at
            # startup update_window_flags() runs before WA_TranslucentBackground
            # is set, and creating winId() too early would break transparency.
            # showEvent() re-applies this once the native handle exists.
            if self.windowHandle() is None:
                return
            import win32con
            import win32gui
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            new_style = ex_style
            if enabled:
                new_style |= win32con.WS_EX_NOACTIVATE
            else:
                new_style &= ~win32con.WS_EX_NOACTIVATE
            if new_style != ex_style:
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new_style)
                win32gui.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                    | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED,
                )
        except Exception:
            pass

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
