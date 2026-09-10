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


class HotkeyMixin:
    def init_hotkeys(self):
        # Register global hotkeys from config
        global_hotkeys.get_hotkey_signals().triggered.connect(self.on_hotkey_triggered)
        self.update_hotkey_bindings()

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
                            "toggleLabGlobalKey"):
            value = cast(str, self.cfg.get(hotkey_type))
            if is_mouse_hotkey(value):
                global_hotkeys.bind_mouse_hotkey(hotkey_type, value)
            else:
                global_hotkeys.bind_hotkey(hotkey_type, value)
        # The local LAB-toggle key is bound as a system-wide hook too, so it
        # works while focus is in the drawing app (无焦点选色模式). Mouse
        # buttons need no hook — the event filter sees them by cursor
        # position — so only keyboard values are bound here.
        lab_toggle_key = cast(str, self.cfg.get("toggleLabKey"))
        if not is_mouse_hotkey(lab_toggle_key):
            global_hotkeys.bind_hotkey("toggleLabKey", lab_toggle_key)
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
        elif hotkey_type == "toggleLabKey":
            # System-wide hook path: the Qt event filter already consumed the
            # key when a Colorink window has focus. Without focus — e.g. while
            # drawing in CSP with 无焦点选色模式 — this hook is the only path,
            # and the region gate still applies: a tab stack under the cursor
            # switches page, the picker pane flips wheel/LAB.
            if QApplication.activeWindow() is not None:
                return  # handled by the Qt key path
            if self._local_view_shortcut_zone():
                print("[Hotkeys] Local view shortcut (no-focus)")
                self._run_local_view_shortcut()
        elif hotkey_type == "toggleLabGlobalKey":
            print("[Hotkeys] Toggle LAB view (global)")
            self.toggle_picker_mode()
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
            try:
                result = self.grayscale_overlay.toggle()
                # Backends return False + last_error on failure — show it
                # clearly instead of silently switching modes.
                if result is False and hasattr(self.grayscale_overlay, 'last_error'):
                    err = getattr(self.grayscale_overlay, "last_error", "")
                    if err:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, "灰度滤镜", err)
            except Exception as e:
                print(f"[Hotkeys] Grayscale toggle error: {e}")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "灰度滤镜", f"切换失败: {e}")

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
