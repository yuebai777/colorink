"""Global-hotkey binding and dispatch for the main window."""

from typing import cast

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QApplication

from core import config, global_hotkeys
from ui.hotkey_button import is_mouse_hotkey

# Zone gate for pickKey: the picker hook DLL really swallows clicks, so
# arming it over shell UI must be blocked even for keyboard-bound hotkeys.
try:
    from core import input_zones
except Exception:
    input_zones = None


FLOAT_TAB_HOTKEY_PREFIX = "__float_tab__:"
FLOAT_LAB_HOTKEY_PREFIX = "__float_lab__:"


class HotkeyMixin:
    def init_hotkeys(self):
        # Register global hotkeys from config
        global_hotkeys.get_hotkey_signals().triggered.connect(self.on_hotkey_triggered)
        self.update_hotkey_bindings()
        self._hotkeys_ready = True

    @staticmethod
    def _bind_global_slot(hotkey_type: str, value: str) -> None:
        if is_mouse_hotkey(value):
            global_hotkeys.bind_mouse_hotkey(hotkey_type, value)
        else:
            global_hotkeys.bind_hotkey(hotkey_type, value)

    def update_hotkey_bindings(self):
        global_hotkeys.unbind_all()
        # Push the zone-filter switch into global_hotkeys before rebinding so
        # the mouse-button gate reflects the latest config immediately.
        global_hotkeys.set_zone_filter_enabled(
            self.cfg.get("mouseHotkeyZoneFilter", True))
        # Global hotkeys may be bound to a keyboard key or a mouse button —
        # route each value to the matching system hook (mouse hotkeys are
        # not suppressed, so the app under the cursor still gets the click).
        for hotkey_type in ("pickKey", "hideWindowKey", "toggleTitleBarKey",
                            "followMouseKey", "grayscaleFilterKey",
                            "regionGrayscaleKey", "toggleLabGlobalKey",
                            "toggleWheelTabGlobalKey", "toggleSliderTabGlobalKey"):
            value = cast(str, self.cfg.get(hotkey_type) or "")
            HotkeyMixin._bind_global_slot(hotkey_type, value)
        # Local hover shortcuts are registered as system-wide hooks too so they
        # keep working while focus is in the drawing app (无焦点选色模式).
        # Mouse buttons need no OS hook — the event filter sees them by cursor
        # position — so only keyboard values are bound here.
        for local_key in ("toggleLabKey", "toggleLabLocalKey"):
            key_val = cast(str, self.cfg.get(local_key) or "")
            if not is_mouse_hotkey(key_val):
                global_hotkeys.bind_hotkey(local_key, key_val)
        # Per-floating-window dedicated global hotkeys (only for open floats).
        unique_fn = getattr(self, "_unique_floating_windows", None)
        classify_fn = getattr(self, "_classify_floating_window", None)
        if callable(unique_fn):
            for win_obj in unique_fn():
                win_id = getattr(win_obj, "panel_id", "")
                if not win_id:
                    continue
                if callable(classify_fn):
                    pages, _labs, slider_pids = classify_fn(win_obj)
                    allow_tab = bool(pages or slider_pids)
                    allow_lab = any(getattr(p, "lab_enabled", False) for p in pages)
                else:
                    allow_tab = True
                    allow_lab = True
                prefs = config.get_floating_window_hotkeys(self.cfg, win_id)
                if allow_tab and prefs["tab_key"]:
                    HotkeyMixin._bind_global_slot(
                        f"{FLOAT_TAB_HOTKEY_PREFIX}{win_id}", prefs["tab_key"])
                if allow_lab and prefs["lab_key"]:
                    HotkeyMixin._bind_global_slot(
                        f"{FLOAT_LAB_HOTKEY_PREFIX}{win_id}", prefs["lab_key"])
        # Hard-wired, unbindable escape hatch: with showTitleBar=false, the
        # right button occupied and the tray unreachable, every visible entry
        # into settings can be unbound — keep one combo that always opens
        # settings. force=True skips the duplicate check (it owns no
        # user-visible slot; on collision both callbacks fire).
        global_hotkeys.bind_hotkey("__openSettings", "ctrl+alt+shift+,", force=True)

    @pyqtSlot(str)
    def on_hotkey_triggered(self, hotkey_type):
        if hotkey_type == "__openSettings":
            # Unbindable fallback combo (Ctrl+Alt+Shift+,) — always opens
            # settings, even when the main window is hidden to tray. Uses the
            # idempotent show (not the toggle) so mashing the combo in a
            # panic can never close the settings window again.
            self._show_settings_window()
            return
        if hotkey_type == "hideWindowKey":
            # 统一走 toggle_visibility，确保手动隐藏时设置 _user_hidden，
            # 前台追踪器不会立刻又把窗口拉出来。
            self.toggle_visibility()
        elif hotkey_type == "toggleTitleBarKey":
            self.toggle_title_bar()
        elif hotkey_type == "followMouseKey":
            self.follow_mouse_active = not self.follow_mouse_active
            self.cfg["followMouseEnabled"] = self.follow_mouse_active
            config.save_hotkey_config(self.cfg)
            print(f"[Hotkeys] Follow Mouse toggled to: {self.follow_mouse_active}")

            # Immediately move to cursor if activated and window is visible
            if self.follow_mouse_active and self.isVisible():
                self.show_window_at_cursor()

            # Sync settings sidebar if visible
            if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
                self.settings_sidebar.cb_follow_mouse.blockSignals(True)
                self.settings_sidebar.cb_follow_mouse.setChecked(self.follow_mouse_active)
                self.settings_sidebar.cb_follow_mouse.blockSignals(False)
        elif hotkey_type in ("toggleLabKey", "toggleLabLocalKey"):
            # System-wide hook path: the Qt event filter already consumed the
            # key when a Colorink window has focus. Without focus — e.g. while
            # drawing in CSP with 无焦点选色模式 — this hook is the only path,
            # and the matching region gate still applies.
            if QApplication.activeWindow() is not None:
                return  # handled by the Qt key path
            if hotkey_type == "toggleLabKey":
                if self._is_tab_shortcut_zone():
                    print("[Hotkeys] Tab switch shortcut (no-focus)")
                    self._run_tab_shortcut()
            elif self._is_lab_view_shortcut_zone():
                print("[Hotkeys] Local LAB toggle shortcut (no-focus)")
                self._run_lab_view_shortcut()
        elif hotkey_type == "toggleLabGlobalKey":
            print("[Hotkeys] Toggle LAB view (global)")
            toggle = getattr(self, "toggle_lab_global", None)
            if callable(toggle):
                toggle()
            else:
                self.toggle_picker_mode()
        elif hotkey_type == "toggleWheelTabGlobalKey":
            print("[Hotkeys] Wheel tab switch (global)")
            toggle = getattr(self, "toggle_wheel_tab_global", None)
            if callable(toggle):
                toggle()
        elif hotkey_type == "toggleSliderTabGlobalKey":
            print("[Hotkeys] Slider tab switch (global)")
            toggle = getattr(self, "toggle_slider_tab_global", None)
            if callable(toggle):
                toggle()
        elif hotkey_type.startswith(FLOAT_TAB_HOTKEY_PREFIX):
            win_id = hotkey_type[len(FLOAT_TAB_HOTKEY_PREFIX):]
            fn = getattr(self, "toggle_floating_window_tabs", None)
            if callable(fn):
                fn(win_id)
        elif hotkey_type.startswith(FLOAT_LAB_HOTKEY_PREFIX):
            win_id = hotkey_type[len(FLOAT_LAB_HOTKEY_PREFIX):]
            fn = getattr(self, "toggle_floating_window_lab", None)
            if callable(fn):
                fn(win_id)
        elif hotkey_type == "pickKey":
            if self.picker_overlay.is_active:
                self.picker_overlay.stop()
            else:
                # 最后一次能在 DLL 装钩前拦截的机会：取色钩子会真吞点击
                # （picker_hook.c return 1），此处保护的是其它软件不被吞，
                # 因此对键盘路径同样生效（"键盘热键不过滤"的有意例外）。
                if (self.cfg.get("mouseHotkeyZoneFilter", True)
                        and input_zones is not None
                        and input_zones.is_available()
                        and input_zones.ignore_at_cursor()):
                    print("[Hotkeys] pickKey ignored: cursor over taskbar/tray/shell UI")
                    return
                self.picker_overlay.start()
                print("[Hotkeys] Global Color Picker activated")
        elif hotkey_type == "grayscaleFilterKey":
            print("[Hotkeys] Grayscale Filter toggled")
            toggle_fs = getattr(self, "toggle_fullscreen_grayscale", None)
            if callable(toggle_fs):
                toggle_fs()
                return
            try:
                region_ctrl = getattr(self, "region_grayscale_overlay", None)
                if region_ctrl is not None and region_ctrl.is_active:
                    region_ctrl.set_active(False)
                result = self.grayscale_overlay.toggle()
                if result is False:
                    # Current backend failed: auto-fallback to Mag backend
                    # so the grayscale filter opens seamlessly without disturbing the user!
                    from core.mag_grayscale import MagFilterController
                    if not isinstance(self.grayscale_overlay, MagFilterController):
                        backend_name = type(self.grayscale_overlay).__name__
                        err = getattr(self.grayscale_overlay, "last_error", "")
                        print(f"[Hotkeys] Backend {backend_name} failed ({err}), falling back to Mag backend...")
                        mag_ctrl = MagFilterController(mode="luma")
                        if mag_ctrl.is_available:
                            try:
                                self.grayscale_overlay.set_active(False)
                                close_fn = getattr(self.grayscale_overlay, "close", None)
                                if callable(close_fn):
                                    close_fn()
                            except Exception:
                                pass
                            self.grayscale_overlay = mag_ctrl
                            self.cfg["grayscaleFilterBackend"] = "mag"
                            config.save_hotkey_config(self.cfg)
                            result = self.grayscale_overlay.toggle()
                            if result:
                                print("[Hotkeys] Successfully fell back to Mag filter backend")
                                return
                    err = getattr(self.grayscale_overlay, "last_error", "")
                    if err:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, "灰度滤镜", err)
            except Exception as e:
                print(f"[Hotkeys] Grayscale toggle error: {e}")
                from core.mag_grayscale import MagFilterController
                if not isinstance(self.grayscale_overlay, MagFilterController):
                    try:
                        mag_ctrl = MagFilterController(mode="luma")
                        if mag_ctrl.is_available:
                            self.grayscale_overlay = mag_ctrl
                            self.cfg["grayscaleFilterBackend"] = "mag"
                            config.save_hotkey_config(self.cfg)
                            if self.grayscale_overlay.toggle():
                                print("[Hotkeys] Recovered from exception with Mag backend")
                                return
                    except Exception:
                        pass
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "灰度滤镜", f"切换失败: {e}")
        elif hotkey_type == "regionGrayscaleKey":
            print("[Hotkeys] Region Grayscale Filter toggled")
            toggle_fn = getattr(self, "toggle_region_grayscale", None)
            if callable(toggle_fn):
                toggle_fn()

    def _on_picker_activated(self):
        """Picker overlay is up — pause the sync thread so its GIL
        traffic and repaint signals cannot drop the picker's frames."""
        st = getattr(self, "sync_thread", None)
        if st is not None:
            st.paused = True

    def _on_picker_deactivated(self):
        """Picker overlay is gone — resume the sync thread. The picked
        colour is written after stop() emits this, so nothing is lost."""
        st = getattr(self, "sync_thread", None)
        if st is not None:
            st.paused = False
        timer = getattr(self, "_save_zoom_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            config.save_hotkey_config(self.cfg)

    def _on_picker_color_picked(self, r, g, b):
        """Handle color picked from the global magnifier overlay."""
        self.current_rgb = (r, g, b)
        self._source_space = "rgb"
        self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        self._record_color_history()
        color = self.color_state.set_from("rgb", (r, g, b))
        self._project_color(color, source="picker")
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            self.sync_thread.flush_pending_writes()
            print(f"[Picker] Picked color RGB({r}, {g}, {b}) and flushed to sync")

    def _on_picker_zoom_changed(self, new_zoom: int):
        """Handle zoom dynamically adjusted via mouse wheel during global color picking."""
        self.cfg["pickerZoom"] = new_zoom
        sidebar = getattr(self, "settings_sidebar", None)
        if sidebar is not None and hasattr(sidebar, "lbl_picker_zoom"):
            sidebar.lbl_picker_zoom.setText(f"{new_zoom}×")
        sw = getattr(self, "settings_window", None)
        if sw is not None and hasattr(sw, "lbl_picker_zoom"):
            sw.lbl_picker_zoom.setText(f"{new_zoom}×")
        # Debounce disk write so rapid wheel scrolling does not stall the UI thread with disk I/O
        timer = getattr(self, "_save_zoom_timer", None)
        if timer is None:
            from PyQt6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: config.save_hotkey_config(self.cfg))
            self._save_zoom_timer = timer
        timer.start(500)
