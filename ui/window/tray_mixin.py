"""System-tray, window-visibility and app-lifecycle concerns for MainWindow."""

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from core import config, global_hotkeys, i18n


class TrayMixin:
    def init_tray(self):
        """Setup system tray icon with context menu for minimized window access."""
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = os.path.join("icons", "icon.ico")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            _style = QApplication.style()
            if _style is not None:
                self.tray_icon.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

        self.tray_icon.setToolTip("Colorink")

        self._build_tray_menu()
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.messageClicked.connect(self._open_pending_update)
        self.tray_icon.show()

    def _build_tray_menu(self):
        """(Re)build the tray context menu in the active language."""
        tray_menu = QMenu()

        show_action = QAction(i18n.tr("显示/隐藏"), self)
        show_action.triggered.connect(self.toggle_visibility)
        tray_menu.addAction(show_action)

        title_action = QAction(i18n.tr("显示标题栏"), self)
        title_action.setCheckable(True)
        title_action.setChecked(self.cfg.get("showTitleBar", True))
        title_action.triggered.connect(self.set_title_bar_visible)
        tray_menu.addAction(title_action)
        self.tray_title_action = title_action

        tray_menu.addSeparator()

        settings_action = QAction(i18n.tr("打开设置"), self)
        settings_action.triggered.connect(self.toggle_settings_sidebar)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        quit_action = QAction(i18n.tr("退出"), self)
        quit_action.triggered.connect(self.close_application)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)

    def retranslate(self):
        """Re-apply the active language to tray + settings window chrome."""
        if hasattr(self, "tray_icon") and self.tray_icon is not None:
            self._build_tray_menu()
        sw = getattr(self, "settings_window", None)
        if sw is not None and hasattr(sw, "_title_bar"):
            sw._title_bar._label.setText(i18n.tr("设置"))

    def on_tray_activated(self, reason):
        """Handle tray icon click: single left-click toggles visibility."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_visibility()

    def _check_updates_silently(self):
        """Background update check on startup; notify via tray, never a modal."""
        if self._startup_update_checked:
            return
        self._startup_update_checked = True
        if not self.cfg.get("checkUpdatesOnStartup", True):
            return
        import threading as _threading
        from core import updater as _updater

        def _run():
            result = _updater.check_for_update()
            # Marshal the result back onto the GUI thread.
            QTimer.singleShot(0, lambda: self._on_silent_update_result(result))

        _threading.Thread(
            target=_run, daemon=True, name="colorink-update-check"
        ).start()

    def _on_silent_update_result(self, result):
        """Show a tray notification when a newer release exists."""
        if "error" in result or not result.get("has_update"):
            return
        latest = result.get("latest_version", "")
        if latest and latest == self.cfg.get("skippedUpdateVersion", ""):
            return  # User already declined this exact version — don't nag.
        self._pending_update = result
        if (hasattr(self, "tray_icon")
                and QSystemTrayIcon.isSystemTrayAvailable()):
            self.tray_icon.showMessage(
                i18n.tr("Colorink 更新"),
                i18n.tr("发现新版本 {latest}，点击查看下载", latest=latest),
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )

    def _open_pending_update(self):
        """Handle a tray-message click: reuse the in-app download flow."""
        result = getattr(self, "_pending_update", None)
        if not result:
            return
        self._pending_update = None
        sidebar = getattr(self, "settings_sidebar", None)
        if sidebar is not None and hasattr(sidebar, "prompt_update"):
            self._show_settings_window()
            sidebar.prompt_update(result)
            return
        import webbrowser as _wb
        _wb.open(result.get("release_url") or "https://github.com/yuebai777/colorink")

    def toggle_visibility(self):
        """Toggle window visibility — same logic as hotkey hide/show."""
        if self.isVisible():
            self.hide()
        else:
            if self.follow_mouse_active:
                self.show_window_at_cursor()
            else:
                self.show()
                self.raise_()

    def set_title_bar_visible(self, visible: bool):
        """Show or hide the title bar and keep the top border aligned."""
        visible = bool(visible)
        if self.title_bar.isVisible() != visible:
            self.cfg["showTitleBar"] = visible
            config.save_hotkey_config(self.cfg)
            self.title_bar.setVisible(visible)
            self.update_geometries()
            self._adjust_content_height()
        if hasattr(self, "tray_title_action"):
            self.tray_title_action.setChecked(visible)

    def toggle_title_bar(self):
        self.set_title_bar_visible(not self.title_bar.isVisible())

    def closeEvent(self, event):
        """Override: hide to tray instead of closing the application."""
        self.hide()
        event.ignore()

    def save_window_geometry(self):
        """Persist current window geometry, normalized to 1x DPI."""
        dpr = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if dpr < 0.1:
            dpr = 1.0
        cfg = {
            "x": self.x(),
            "y": self.y(),
            "width": self.width(),
            "height": self.height(),
            "dpr": dpr,  # Store DPR so we can restore correctly
            "zoom": 0  # Default placeholder
        }
        config.save_window_config(cfg)

    def close_application(self):
        # Flush coalesced module state before writing the final window config.
        self._flush_module_config_save()
        self.save_window_geometry()

        # Clean up hotkeys and thread
        global_hotkeys.unbind_all()
        if hasattr(self, 'picker_overlay'):
            self.picker_overlay.stop()
            self.picker_overlay.close()
        if hasattr(self, 'grayscale_overlay'):
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
        if hasattr(self, 'sync_thread'):
            # Disable sync & reset PS COM ref so the polling loop
            # won't try to make new COM calls to a dead Photoshop.
            self.sync_thread.sync_enabled = False
            if hasattr(self.sync_thread, 'ps_sync'):
                try:
                    self.sync_thread.ps_sync._reset()
                except Exception:
                    pass
            if hasattr(self.sync_thread, 'companion_sync'):
                self.sync_thread.companion_sync._disconnect()
            # Signal the thread to stop, but DO NOT join it.
            # If it's blocked in a hung COM RPC call (Photoshop died
            # mid-call), joining would freeze the main thread forever.
            # The OS reclaims all resources on process exit anyway.
            self.sync_thread.running = False

        # Hide settings window before exit
        if hasattr(self, 'settings_window') and self.settings_window is not None:
            self.settings_window.hide()

        # Hide tray icon before exit
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()

        # Use os._exit to bypass Python's thread-join on exit.
        # sys.exit(0) would try to join non-daemon threads, which
        # can hang if the sync thread is stuck in a COM RPC call.
        import os as _os
        _os._exit(0)
