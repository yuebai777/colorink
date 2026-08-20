"""Global-hotkey binding and dispatch for the main window."""

from typing import cast

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QApplication

from core import config, global_hotkeys
from ui.hotkey_button import is_mouse_hotkey


class HotkeyMixin:
    def init_hotkeys(self):
        # Register global hotkeys from config
        global_hotkeys.hotkey_signals.triggered.connect(self.on_hotkey_triggered)
        self.update_hotkey_bindings()

    def update_hotkey_bindings(self):
        global_hotkeys.unbind_all()
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

    @pyqtSlot(str)
    def on_hotkey_triggered(self, hotkey_type):
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
            # and the mouse-over-wheel gate still applies.
            if QApplication.activeWindow() is not None:
                return  # handled by the Qt key path
            if self._is_lab_toggle_zone():
                print("[Hotkeys] Toggle LAB view (local, no-focus)")
                self.toggle_picker_mode()
        elif hotkey_type == "toggleLabGlobalKey":
            print("[Hotkeys] Toggle LAB view (global)")
            self.toggle_picker_mode()
        elif hotkey_type == "pickKey":
            if self.picker_overlay.is_active:
                self.picker_overlay.stop()
            else:
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

    def _on_picker_color_picked(self, r, g, b):
        """Handle color picked from the global magnifier overlay."""
        self.current_rgb = (r, g, b)
        self._source_space = "rgb"
        self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        self._record_color_history()
        color = self.color_state.set_from("rgb", (r, g, b))
        self._project_color(color, source="picker")
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            color_index = 0 if self.active_slot == "fg" else 1
            self.sync_thread.write_color(r, g, b, source_space="rgb",
                                         source_values={"r": float(r), "g": float(g), "b": float(b)},
                                         color_index=color_index)
            print(f"[Picker] Picked color RGB({r}, {g}, {b})")
