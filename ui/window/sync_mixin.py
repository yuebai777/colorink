"""Drawing-software color sync (CSP/SAI/UDM/PS/companion) for MainWindow."""

import os
import sys

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor

from core import i18n, memory_sync
from core.foreground import (
    find_csp_companion_qr_rect,
    find_csp_companion_qr_window,
    get_widget_screen_rect,
    identify_drawing_app,
    is_csp_running,
    rects_overlap,
)
from ui.color_conversions import lab_to_rgb, oklab_to_rgb, oklch_to_rgb, vhsv_to_rgb


def _show_without_stealing_focus(widget, *, raise_win: bool = True) -> None:
    """Show *widget* without activating it or stealing focus from CSP."""
    if widget is None:
        return
    has_attr_api = hasattr(widget, "testAttribute") and hasattr(widget, "setAttribute")
    prev_no_activate = (
        widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if has_attr_api
        else None
    )
    if has_attr_api and not prev_no_activate:
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    try:
        widget.show()
    finally:
        if has_attr_api and prev_no_activate is False:
            widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
    if raise_win and hasattr(widget, "raise_"):
        widget.raise_()


class SyncMixin:
    def init_memory_sync(self):
        # Start background memory syncing thread
        self.sync_thread = memory_sync.MemorySyncThread(self)
        self.sync_thread.signals.color_changed.connect(self.on_external_color_changed)
        self.sync_thread.signals.transparent_changed.connect(self.on_external_transparent_changed)
        self.sync_thread.signals.active_slot_changed.connect(self.on_external_active_slot_changed)
        self.sync_thread.signals.status_changed.connect(self.on_sync_status_changed)
        self.sync_thread.signals.error_changed.connect(self.on_sync_error_changed)
        self.sync_thread.signals.companion_reconnect_needed.connect(
            self.on_companion_reconnect_needed
        )
        self._sync_error = None
        self._ps_perm_prompted = False
        self._csp_companion_prompted = False
        self._companion_reconnect_prompted = False
        self._companion_reconnect_pending = False
        self._companion_prompt_scheduled = False
        self._companion_dialog = None
        self._qr_scan_hiding = False
        self._qr_obscuring_hidden = False
        self._qr_hidden_settings = False
        self._qr_hidden_dialog = False

        # Set active software mode
        mode = self.cfg.get("syncSoftware", "auto")
        if mode not in ("auto", "csp", "sai", "udm", "ps", "companion"):
            mode = "auto"
        if mode == "auto":
            from core.foreground import find_running_drawing_software, resolve_auto_sync_mode
            running = find_running_drawing_software()
            c_sync = getattr(self.sync_thread, "companion_sync", None)
            has_session = (
                getattr(c_sync, "has_session", None) or getattr(c_sync, "_has_session", None)
            )
            has_session_val = bool(has_session()) if callable(has_session) else False
            initial_mode = resolve_auto_sync_mode(
                running or "csp",
                has_companion_session=has_session_val,
            ) or "csp"
            self.sync_thread.set_software_mode(initial_mode)
            if (running == "csp" or initial_mode in ("csp", "companion")) and not has_session_val:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1000, self.maybe_prompt_companion_connection)
        else:
            self.sync_thread.set_software_mode(mode)
            if mode in ("csp", "companion"):
                c_sync = getattr(self.sync_thread, "companion_sync", None)
                has_session = (
                    getattr(c_sync, "has_session", None) or getattr(c_sync, "_has_session", None)
                )
                has_session_val = bool(has_session()) if callable(has_session) else False
                if not has_session_val:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(1000, self.maybe_prompt_companion_connection)

        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.sai2_panel_mode = self.cfg.get("sai2PanelMode", "auto")
        # SAI UI refresh is a single always-on full mode (core.sai2_ui_refresh
        # defaults to it); there is no config knob and nothing here overrides it.
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        setattr(self.sync_thread, "ps_version", self.cfg.get("psVersion", "auto"))
        self.sync_thread.update_versions()

        # Start syncing
        self.sync_thread.start()

    @pyqtSlot(int, int, int, int)
    def on_external_color_changed(self, r, g, b, color_index):
        # Drawing software (CSP/SAI/UDM/PS) color changed — e.g. the user
        # Alt-picked a new color or switched colors in CSP. color_index
        # maps 0 → fg (main), 1 → bg (sub); each slot updates its own
        # swatch regardless of the local active slot.
        slot = "fg" if color_index == 0 else "bg"
        if slot != self.active_slot:
            # Off-slot change: update the swatch and that slot's source
            # tracking.  If we only update the QColor, switching to this slot
            # later would rebuild from stale source coords and revert the
            # externally changed color.
            rgb_values = {"r": float(r), "g": float(g), "b": float(b)}
            if slot == "fg":
                self.preview_box.fg_color = QColor(r, g, b)
                self._fg_transparent = False
                self._fg_source_space = "rgb"
                self._fg_source_values = rgb_values
            else:
                self.preview_box.bg_color = QColor(r, g, b)
                self._bg_transparent = False
                self._bg_source_space = "rgb"
                self._bg_source_values = rgb_values
            self.preview_box.set_transparent(slot, False)
            iter_previews = getattr(self, "_iter_floating_previews", None)
            boxes = iter_previews() if callable(iter_previews) else getattr(self, "_floating_preview_boxes", ())
            for box in boxes:
                try:
                    box.set_colors(QColor(self.preview_box.fg_color),
                                   QColor(self.preview_box.bg_color))
                    box.set_transparent(slot, False)
                except RuntimeError:
                    continue
            return
        self.current_rgb = (r, g, b)
        # Companion mode reports its own HSV: honour it so picking a
        # grayscale/black colour in CSP keeps the reported hue/saturation
        # (RGB alone carries neither for gray/black).
        hsv = None
        if hasattr(self, 'sync_thread') and self.sync_thread.software_mode == 'companion':
            chsv = getattr(self.sync_thread, 'companion_hsv', None)
            if chsv is not None:
                hsv = chsv
        # Record the just-read RGB as the active source BEFORE projecting.
        # _record_color_history reads self._source_space/_source_values; if
        # they still hold the previous interaction's coordinates they end up
        # stored against the wrong RGB (external change gets stale source).
        self._source_space = "rgb"
        self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        self._record_color_history()
        color = self.color_state.set_from("rgb", (r, g, b))
        self._project_color(color, source="sync", hsv=hsv)

    @pyqtSlot(int)
    def on_external_active_slot_changed(self, color_index):
        """The drawing software switched its active slot (e.g. user pressed
        X in CSP / picked the sub swatch). Follow it locally WITHOUT
        writing anything back — the swatch border, transparent-tile
        highlight, source tracking and the whole UI state switch to the
        new slot.
        """
        slot = "fg" if color_index == 0 else "bg"
        if slot == self.active_slot:
            return
        if slot == "fg":
            self._bg_source_space = self._source_space
            self._bg_source_values = self._source_values
            self._source_space = self._fg_source_space
            self._source_values = self._fg_source_values
            col = self.preview_box.fg_color
        else:
            self._fg_source_space = self._source_space
            self._fg_source_values = self._source_values
            self._source_space = self._bg_source_space
            self._source_values = self._bg_source_values
            col = self.preview_box.bg_color
        self.active_slot = slot
        self.preview_box.update_slot_borders(slot)
        iter_previews = getattr(self, "_iter_floating_previews", None)
        boxes = iter_previews() if callable(iter_previews) else getattr(self, "_floating_preview_boxes", ())
        for box in boxes:
            try:
                box.update_slot_borders(slot)
            except RuntimeError:
                continue
        # Rebuild the active slot's full Color and fan it out to every widget
        # (wheel, LAB, sliders, current_rgb, color_state).  `_project_color`
        # updates color_state.current as well, so later UI actions and the
        # right-click menu see the newly active slot's color.
        color = self._color_from_source(
            self._source_space, self._source_values,
            (col.red(), col.green(), col.blue()),
        )
        self._project_color(color, source="slot_change")

    @pyqtSlot(int, bool)
    def on_external_transparent_changed(self, color_index, transparent):
        """The drawing software reports a slot's color is transparent
        (companion read-back / CSP 5.1 memory flag). Mirror onto the
        matching slot and follow the change like CSP's own "current
        drawing color" concept by activating that slot (the slot-change
        write carries the transparent flag, so CSP's state is not
        overwritten).
        """
        mode = "csp"
        if hasattr(self, 'sync_thread') and self.sync_thread is not None:
            mode = self.sync_thread.software_mode
        if color_index in (0, 1):
            # 内存模式：+0x08 透明标志属于激活槽（index 恒 0），映射活动槽
            if mode in ('csp', 'udm') and color_index == 0:
                slot = self.active_slot
            else:
                slot = "fg" if color_index == 0 else "bg"
        else:
            # 透明时 companion 报告 index=-1：透明属于激活槽
            slot = self.active_slot
        if transparent:
            if slot == "fg":
                self._fg_transparent = True
            else:
                self._bg_transparent = True
        else:
            if slot == "fg":
                self._fg_transparent = False
            else:
                self._bg_transparent = False
        self.preview_box.set_transparent(slot, transparent)
        iter_previews = getattr(self, "_iter_floating_previews", None)
        boxes = iter_previews() if callable(iter_previews) else getattr(self, "_floating_preview_boxes", ())
        for box in boxes:
            try:
                box.set_transparent(slot, transparent)
            except RuntimeError:
                continue
        if slot != self.active_slot:
            if slot == "fg":
                self.select_fg_slot()
            else:
                self.select_bg_slot()

    @pyqtSlot(str, bool)
    def on_sync_status_changed(self, mode, connected):
        self._sync_status = (mode, connected)
        # Optionally update title bar text or border to show connection status
        mode_display = {"csp": "CSP", "sai": "SAI", "udm": "UDM", "ps": "PS", "companion": "csp_c"}.get(mode, mode.upper())
        status_text = f"Colorink ({mode_display} {'✓' if connected else '×'})"
        self.title_bar.title_label.setText(status_text)
        if mode == "companion":
            if connected:
                self._companion_reconnect_prompted = False
                self._companion_reconnect_pending = False
                if hasattr(self, "title_bar"):
                    self.title_bar.title_label.setToolTip("")
                dlg = getattr(self, "_companion_dialog", None)
                if dlg is not None:
                    mark_fn = getattr(dlg, "mark_connected_externally", None)
                    if callable(mark_fn):
                        mark_fn()
            else:
                c_sync = getattr(getattr(self, "sync_thread", None), "companion_sync", None)
                has_sess_fn = (
                    getattr(c_sync, "has_session", None)
                    or getattr(c_sync, "_has_session", None)
                ) if c_sync is not None else None
                if callable(has_sess_fn) and has_sess_fn() and hasattr(self, "title_bar"):
                    self.title_bar.title_label.setToolTip(
                        i18n.tr("CSP 智能手机连接已断开：请在 CSP 中点击「文件 → 连接智能手机」重新连接")
                    )
            if hasattr(self, 'settings_sidebar'):
                self.settings_sidebar._refresh_companion_status()
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar._refresh_sync_status()

    @pyqtSlot(str, str, bool)
    def on_sync_error_changed(self, mode, error, permission_issue):
        """Show *why* the sync backend failed to connect (e.g. Photoshop)."""
        self._sync_error = (mode, error, permission_issue) if error else None
        if hasattr(self, 'title_bar'):
            self.title_bar.title_label.setToolTip(error if error else "")
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar._refresh_sync_status()
        # A UAC integrity mismatch is self-fixable: offer to relaunch
        # Colorink elevated. Prompt once per session to avoid nagging.
        if mode == 'ps' and permission_issue and not self._ps_perm_prompted:
            self._ps_perm_prompted = True
            self._prompt_relaunch_as_admin()

    def _prompt_relaunch_as_admin(self):
        from PyQt6.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, "需要管理员权限",
            "检测到 Photoshop 可能以管理员身份运行，而 Colorink 权限不足，"
            "无法读取 / 写入同步桥文件。\n\n"
            "是否以管理员身份重启 Colorink？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._relaunch_as_admin()

    def _relaunch_as_admin(self):
        """Restart the app elevated via ShellExecute(runas); exit current."""
        import ctypes
        exe = sys.executable
        args = " ".join(
            f'"{a}"' if " " in a else a for a in sys.argv[1:]
        )
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, args, os.getcwd(), 1
            )
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提权失败", f"无法以管理员身份启动: {exc}")
            return
        if ret <= 32:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提权失败", f"无法以管理员身份启动 (错误码 {ret})")
            return
        # ShellExecute returned OK — this instance hands over and exits.
        os._exit(0)

    def _unique_floating_windows_for_qr(self) -> list:
        unique_fn = getattr(self, "_unique_floating_windows", None)
        if callable(unique_fn):
            try:
                return list(unique_fn())
            except Exception:
                return []
        fw_fn = getattr(self, "floating_windows", None)
        if callable(fw_fn):
            seen: set[int] = set()
            out = []
            for win in fw_fn().values():
                if win is not None and id(win) not in seen:
                    seen.add(id(win))
                    out.append(win)
            return out
        return []

    def _hide_windows_for_qr_scan(self, dialog=None) -> dict:
        """Temporarily hide all visible Colorink windows so QR screen capture is unobstructed."""
        self._qr_scan_hiding = True
        sw = getattr(self, "settings_window", None)
        floaters = self._unique_floating_windows_for_qr()
        visible_floaters = []
        for win in floaters:
            try:
                if not win.isHidden():
                    visible_floaters.append(win)
            except RuntimeError:
                continue

        snapshot = {
            "main": bool(self.isVisible()),
            "floaters": visible_floaters,
            "settings": bool(sw is not None and sw.isVisible()),
            "dialog": bool(dialog is not None and dialog.isVisible()),
        }

        if snapshot["dialog"] and dialog is not None:
            dialog.hide()
        if snapshot["settings"] and sw is not None:
            sw.hide()
        for win in visible_floaters:
            try:
                from ui.panels.transitions import cancel_window_animations
                cancel_window_animations(win, restore_opacity=1.0)
            except Exception:
                pass
            try:
                win.hide()
                win.force_native_visible(False)
            except (RuntimeError, AttributeError):
                continue
        if snapshot["main"]:
            self.hide()
        return snapshot

    def _restore_windows_after_qr_scan(self, snapshot: dict, dialog=None) -> None:
        """Restore Colorink windows that were visible prior to QR screen capture."""
        self._qr_scan_hiding = False
        if not isinstance(snapshot, dict):
            return

        if (
            snapshot.get("main")
            and not getattr(self, "_user_hidden", False)
            and not getattr(self, "_qr_obscuring_hidden", False)
        ):
            _show_without_stealing_focus(self, raise_win=True)

        if self.isVisible():
            for win in snapshot.get("floaters", ()):
                try:
                    if getattr(win, "_qr_hidden", False) or getattr(win, "_foreground_hidden", False):
                        continue
                    win.show_without_stealing_focus()
                    win.force_native_visible(True)
                except (RuntimeError, AttributeError):
                    continue

        sw = getattr(self, "settings_window", None)
        if (
            snapshot.get("settings")
            and sw is not None
            and not getattr(self, "_qr_hidden_settings", False)
        ):
            _show_without_stealing_focus(sw, raise_win=True)

        if (
            snapshot.get("dialog")
            and dialog is not None
            and not getattr(self, "_qr_hidden_dialog", False)
        ):
            _show_without_stealing_focus(dialog, raise_win=True)

        # Immediately re-apply QR-dialog overlap hiding if CSP's QR dialog is open
        self._sync_qr_overlap_visibility(_allow_lookup=True)

    def _sync_qr_overlap_visibility(
        self,
        qr_rect: tuple[int, int, int, int] | None = None,
        *,
        _allow_lookup: bool = True,
        _in_foreground_tick: bool = False,
    ) -> None:
        """Temporarily hide Colorink windows that overlap CSP's QR dialog."""
        if getattr(self, "_qr_scan_hiding", False):
            return
        if qr_rect is None and _allow_lookup:
            qr_rect = find_csp_companion_qr_rect()

        sw = getattr(self, "settings_window", None)
        dlg = getattr(self, "_companion_dialog", None)
        floaters = self._unique_floating_windows_for_qr()
        user_hidden = bool(getattr(self, "_user_hidden", False))

        if qr_rect is not None:
            if not user_hidden and not getattr(self, "_qr_obscuring_hidden", False):
                my_rect = get_widget_screen_rect(self)
                if my_rect is not None and rects_overlap(my_rect, qr_rect, margin=16):
                    self._qr_obscuring_hidden = True
                    if self.isVisible():
                        self.hide()

            if not user_hidden and not getattr(self, "_qr_obscuring_hidden", False):
                for win in floaters:
                    try:
                        if (
                            win.isHidden()
                            or getattr(win, "_qr_hidden", False)
                            or getattr(win, "_foreground_hidden", False)
                        ):
                            continue
                        w_rect = get_widget_screen_rect(win)
                        if w_rect is not None and rects_overlap(w_rect, qr_rect, margin=16):
                            win._qr_hidden = True
                            try:
                                from ui.panels.transitions import cancel_window_animations
                                cancel_window_animations(win, restore_opacity=1.0)
                            except Exception:
                                pass
                            win.hide()
                            win.force_native_visible(False)
                    except (RuntimeError, AttributeError):
                        continue

            if (
                sw is not None
                and sw.isVisible()
                and not getattr(self, "_qr_hidden_settings", False)
            ):
                sw_rect = get_widget_screen_rect(sw)
                if sw_rect is not None and rects_overlap(sw_rect, qr_rect, margin=16):
                    self._qr_hidden_settings = True
                    sw.hide()

            if dlg is not None and dlg.isVisible():
                dlg_rect = get_widget_screen_rect(dlg)
                if dlg_rect is not None and rects_overlap(dlg_rect, qr_rect, margin=16):
                    from core.csp_companion_sync import CSPCompanionSync
                    CSPCompanionSync._position_dialog_clear_of_qr(dlg, qr_rect=qr_rect)
                    dlg_rect2 = get_widget_screen_rect(dlg)
                    if dlg_rect2 is not None and rects_overlap(dlg_rect2, qr_rect, margin=12):
                        self._qr_hidden_dialog = True
                        dlg.hide()
            return

        # CSP QR dialog is not visible: restore any windows hidden for it.
        cfg = getattr(self, "cfg", {}) or {}
        fg_controlled = bool(cfg.get("onlyShowInCsp", False) or cfg.get("hideOnPopupMenu", False))

        if getattr(self, "_qr_obscuring_hidden", False):
            self._qr_obscuring_hidden = False
            if not user_hidden and not self.isVisible():
                if not fg_controlled:
                    _show_without_stealing_focus(self, raise_win=True)
                elif not _in_foreground_tick:
                    check_fg = getattr(self, "check_foreground_window", None)
                    if callable(check_fg):
                        check_fg()

        any_floater_restored = False
        for win in floaters:
            try:
                if getattr(win, "_qr_hidden", False):
                    win._qr_hidden = False
                    any_floater_restored = True
            except (RuntimeError, AttributeError):
                continue
        if any_floater_restored and self.isVisible():
            sync_floating = getattr(self, "set_floating_foreground_visible", None)
            if callable(sync_floating):
                sync_floating(True)

        if getattr(self, "_qr_hidden_settings", False):
            self._qr_hidden_settings = False
            if sw is not None:
                _show_without_stealing_focus(sw, raise_win=True)

        if getattr(self, "_qr_hidden_dialog", False):
            self._qr_hidden_dialog = False
            if dlg is not None:
                _show_without_stealing_focus(dlg, raise_win=True)

    @staticmethod
    def _is_csp_or_self_foreground() -> bool:
        """True if foreground belongs to CLIP STUDIO PAINT or this Colorink process."""
        try:
            import win32gui
            import win32process
            from core.foreground import _resolve_process_exe
        except ImportError:
            return True

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid():
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            win_class = win32gui.GetClassName(hwnd) or ""
            exe = _resolve_process_exe(pid) if pid else ""
            return identify_drawing_app(exe_name=exe, title=title, win_class=win_class) == "csp"
        except Exception:
            return False

    def _on_companion_dialog_connected(self):
        sync_thread = getattr(self, "sync_thread", None)
        if sync_thread is None:
            return
        c = sync_thread.companion_sync
        c._load_session()
        reset_fn = getattr(sync_thread, "reset_companion_reconnect_state", None)
        if callable(reset_fn):
            reset_fn()
        switch_fn = getattr(self, "switch_sync_software_mode", None)
        if callable(switch_fn):
            switch_fn("companion")
        else:
            sync_thread.set_software_mode("companion")
        if hasattr(self, 'title_bar') and hasattr(self.title_bar, 'title_label'):
            self.title_bar.title_label.setText("Colorink (csp_c — 连接中...)")
            self.title_bar.title_label.setToolTip("")
        self._companion_reconnect_prompted = False
        self._companion_reconnect_pending = False
        if hasattr(self, 'settings_sidebar'):
            self.settings_sidebar._refresh_companion_status()
            self.settings_sidebar._refresh_sync_status()

    def _on_companion_dialog_dismissed(self, accepted: bool):
        self._companion_dialog = None
        self._qr_hidden_dialog = False
        if not accepted and hasattr(self, 'settings_sidebar'):
            self.settings_sidebar._refresh_companion_status()

    def _setup_companion_connection(self, reconnect_mode: bool = False, auto_prompt: bool = False):
        if not hasattr(self, 'sync_thread'):
            return
        existing = getattr(self, "_companion_dialog", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    if not auto_prompt:
                        existing.activateWindow()
                    return
                existing.deleteLater()
            except RuntimeError:
                pass
            self._companion_dialog = None

        from core.csp_companion_sync import CSPCompanionSync
        dlg = CSPCompanionSync.create_companion_dialog(
            None,
            reconnect_mode=reconnect_mode,
            auto_show_without_activating=auto_prompt,
            hide_for_scan_cb=self._hide_windows_for_qr_scan,
            restore_after_scan_cb=self._restore_windows_after_qr_scan,
            find_qr_window_cb=find_csp_companion_qr_window,
            sync_overlap_cb=lambda r: self._sync_qr_overlap_visibility(r, _allow_lookup=False),
            on_connected_cb=self._on_companion_dialog_connected,
            on_dismissed_cb=self._on_companion_dialog_dismissed,
        )
        self._companion_dialog = dlg
        if auto_prompt:
            _show_without_stealing_focus(dlg, raise_win=True)
        else:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

    @pyqtSlot(str)
    def on_companion_reconnect_needed(self, _reason: str = ""):
        """Handle signal from MemorySyncThread when saved companion session fails."""
        self._companion_reconnect_pending = True
        self.maybe_prompt_companion_reconnection()

    def maybe_prompt_companion_reconnection(self):
        """Prompt the user once per disconnect episode when CSP Companion needs
        reconnection and CSP is currently running/active."""
        self._companion_prompt_scheduled = False
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if getattr(self, "_companion_reconnect_prompted", False):
            return
        sync_thread = getattr(self, "sync_thread", None)
        if sync_thread is None or getattr(sync_thread, "software_mode", None) != "companion":
            return
        c = getattr(sync_thread, "companion_sync", None)
        if c is None or getattr(c, "_connected", False):
            return
        has_session = getattr(c, "has_session", None) or getattr(c, "_has_session", None)
        if not (bool(has_session()) if callable(has_session) else False):
            return
        existing = getattr(self, "_companion_dialog", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    return
            except RuntimeError:
                self._companion_dialog = None

        if not is_csp_running():
            self._companion_reconnect_pending = True
            return

        if bool(getattr(self, "cfg", {}).get("onlyShowInCsp", False)):
            if not self._is_csp_or_self_foreground():
                self._companion_reconnect_pending = True
                return

        self._companion_reconnect_prompted = True
        self._companion_reconnect_pending = False

        if getattr(self, "_user_hidden", False):
            tray = getattr(self, "tray_icon", None)
            if tray is not None:
                from PyQt6.QtWidgets import QSystemTrayIcon
                if QSystemTrayIcon.isSystemTrayAvailable():
                    self._pending_tray_action = "companion_reconnect"
                    tray.showMessage(
                        i18n.tr("CSP 智能手机需要重新连接"),
                        i18n.tr("请在 CSP 中点击「文件 → 连接智能手机」重新打开二维码，点击此通知打开连接向导"),
                        QSystemTrayIcon.MessageIcon.Information,
                        10000,
                    )
            return

        QTimer.singleShot(
            250,
            lambda: self._setup_companion_connection(reconnect_mode=True, auto_prompt=True),
        )

    def maybe_prompt_companion_connection(self):
        """Automatically prompt the user to connect CSP companion once per session
        when CSP is active/detected and no saved companion session exists."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if getattr(self, "_csp_companion_prompted", False):
            return
        sync_thread = getattr(self, "sync_thread", None)
        if sync_thread is None:
            return
        c = getattr(sync_thread, "companion_sync", None)
        if c is None:
            return
        has_session = getattr(c, "has_session", None) or getattr(c, "_has_session", None)
        has_session_val = bool(has_session()) if callable(has_session) else False
        connected = bool(getattr(c, "_connected", False))
        if connected or has_session_val:
            return
        self._csp_companion_prompted = True
        QTimer.singleShot(400, self._setup_companion_connection)

    def _resolve_sync_source(self):
        """Return (space_name, values) for CSP memory-mode sync.

        Only spaces in SPACE_ORDER (rgb/cmyk/hsv/hls) are passed directly;
        lab/oklab/oklch are converted to float RGB.
        """
        src = self._source_space
        vals = self._source_values
        if not src or not vals:
            return (None, None)
        if src in ("rgb", "cmyk", "hsv", "hls"):
            return (src, vals)
        # Fallback: convert non-SPACE_ORDER sources to float RGB
        try:
            if src == "vhsv":
                r, g, b = vhsv_to_rgb(vals["h"], vals["s"], vals["v"])
            elif src == "lab":
                r, g, b = lab_to_rgb(vals["l"], vals["a"], vals["b"])
            elif src == "oklab":
                r, g, b = oklab_to_rgb(vals["L"], vals["a"], vals["b"])
            elif src == "oklch":
                r, g, b = oklch_to_rgb(vals["L"], vals["C"], vals["h"])
            else:
                return (None, None)
            rgb = {"r": max(0.0, min(255.0, r)),
                   "g": max(0.0, min(255.0, g)),
                   "b": max(0.0, min(255.0, b))}
            return ("rgb", rgb)
        except Exception:
            return (None, None)

    def _push_color_to_sync(self, r, g, b, source, hsv):
        """Push a color to the drawing software after a non-sync change.

        Skipped when the change originated from sync itself (to avoid an
        echo loop) or when a slider is still being dragged (the final push
        happens on release in on_interaction_finished).
        """
        if source == "sync" or not hasattr(self, 'sync_thread') or not self.sync_thread.isRunning():
            return
        is_dragging = False
        if source.startswith("sliders_"):
            for chan, (slider, _) in self.slider_widgets.items():
                if slider.isSliderDown():
                    is_dragging = True
                    break
        elif source == "wheel":
            wheel = getattr(self, "color_wheel", None)
            if wheel is not None and getattr(wheel, "dragging", None):
                is_dragging = True
            if not is_dragging:
                for p in (getattr(self, "wheel_panels", None) or {}).values():
                    w = getattr(p, "wheel", None)
                    if w is not None and getattr(w, "dragging", None):
                        is_dragging = True
                        break
        elif source == "lab":
            lab = getattr(self, "lab_square", None)
            if lab is not None and getattr(lab, "dragging", False):
                is_dragging = True
            lab_s = getattr(self, "lab_slider", None)
            if lab_s is not None and getattr(lab_s, "dragging", False):
                is_dragging = True
            if not is_dragging:
                flab_fn = getattr(self, "_floating_lab_squares", None)
                if callable(flab_fn):
                    for sq in flab_fn():
                        if getattr(sq, "dragging", False):
                            is_dragging = True
                            break
                lab_page = getattr(self, "_floating_lab_page", None)
                if lab_page is not None and (
                    getattr(getattr(lab_page, "lab_square", None), "dragging", False)
                    or getattr(getattr(lab_page, "lab_slider", None), "dragging", False)
                ):
                    is_dragging = True
                for wpage in (getattr(self, "_floating_wheel_pages", None) or {}).values():
                    if getattr(getattr(wpage, "lab_slider", None), "dragging", False):
                        is_dragging = True
                        break
        if is_dragging:
            return
        hsv_ov = None
        if self.sync_thread.software_mode == 'companion':
            _U32 = 4294967295
            if hsv is not None and len(hsv) == 3:
                hsv_ov = (round(hsv[0]/360*_U32),
                          round(hsv[1]/100*_U32),
                          round(hsv[2]/100*_U32))
            else:
                # Fallback: wheel HSV was already updated in the color-sync
                # step above. This preserves hue when RGB→HSV would lose it
                # (grayscale).
                if hasattr(self, "color_wheel") and getattr(self.color_wheel, "wheel_mode", None) == "vhsv-square":
                    h_act, s_act, v_act = rgb_to_hsv(r, g, b)
                    hsv_ov = (round(h_act/360*_U32),
                              round(s_act/100*_U32),
                              round(v_act/100*_U32))
                else:
                    hsv_ov = (round(self.color_wheel.h/360*_U32),
                              round(self.color_wheel.s/100*_U32),
                              round(self.color_wheel.v/100*_U32))
        src_sp, src_v = self._resolve_sync_source()
        color_index = 0 if self.active_slot == "fg" else 1
        # A transparent active slot must keep the transparent semantics when
        # a slot-change re-pushes its color (e.g. switching to a transparent
        # fg slot must not overwrite CSP's transparent state with the RGB).
        is_transparent = (
            self._fg_transparent if color_index == 0 else self._bg_transparent
        )
        self.sync_thread.write_color(r, g, b, hsv_u32=hsv_ov,
                                     source_space=src_sp, source_values=src_v,
                                     transparent=is_transparent,
                                     color_index=color_index)

    def _get_hsv_u32_for_sync(self, slot_index: int = 0):
        """Compute u32 scaled HSV for companion/CSP synchronization."""
        _U32 = 4294967295
        try:
            active_idx = 0 if getattr(self, "active_slot", "fg") == "fg" else 1
            if slot_index == active_idx and hasattr(self, "color_wheel"):
                if getattr(self.color_wheel, "wheel_mode", None) != "vhsv-square":
                    return (round(self.color_wheel.h / 360.0 * _U32),
                            round(self.color_wheel.s / 100.0 * _U32),
                            round(self.color_wheel.v / 100.0 * _U32))
            pbox = getattr(self, "preview_box", None)
            col = getattr(pbox, "fg_color" if slot_index == 0 else "bg_color", None)
            if col is not None and col.isValid():
                h, s, v, _ = col.getHsv()
                if h < 0:
                    h = 0
                return (round(h / 360.0 * _U32),
                        round((s / 255.0) * _U32),
                        round((v / 255.0) * _U32))
        except Exception:
            pass
        return None

    def switch_sync_software_mode(self, new_mode: str):
        """Switch active software sync channel and immediately synchronize
        Colorink's current palette state (both slots) to the new software.

        This prevents the newly focused software's stale colors from overwriting
        Colorink's active colors ("切到ps把颜色切回去") and ensures the new
        software receives the palette colors with zero delay.
        """
        sync_thread = getattr(self, "sync_thread", None)
        if sync_thread is None:
            return
        if getattr(sync_thread, "software_mode", None) == new_mode:
            return

        active_slot = getattr(self, "active_slot", "fg")
        active_idx = 0 if active_slot == "fg" else 1

        pbox = getattr(self, "preview_box", None)
        fg_col = getattr(pbox, "fg_color", None)
        bg_col = getattr(pbox, "bg_color", None)

        fg_rgb = (fg_col.red(), fg_col.green(), fg_col.blue()) if (fg_col and fg_col.isValid()) else getattr(self, "current_rgb", (255, 255, 255))
        bg_rgb = (bg_col.red(), bg_col.green(), bg_col.blue()) if (bg_col and bg_col.isValid()) else (255, 255, 255)

        src_sp, src_v = self._resolve_sync_source()
        get_hsv = getattr(self, "_get_hsv_u32_for_sync", None)
        hsv_0 = get_hsv(0) if callable(get_hsv) else None
        hsv_1 = get_hsv(1) if callable(get_hsv) else None

        palette = {
            0: {
                "rgb": fg_rgb,
                "transparent": bool(getattr(self, "_fg_transparent", False)),
                "source_space": src_sp if active_idx == 0 else getattr(self, "_fg_source_space", "rgb"),
                "source_values": src_v if active_idx == 0 else getattr(self, "_fg_source_values", None),
                "hsv_u32": hsv_0,
            },
            1: {
                "rgb": bg_rgb,
                "transparent": bool(getattr(self, "_bg_transparent", False)),
                "source_space": src_sp if active_idx == 1 else getattr(self, "_bg_source_space", "rgb"),
                "source_values": src_v if active_idx == 1 else getattr(self, "_bg_source_values", None),
                "hsv_u32": hsv_1,
            },
            "active_slot": active_idx,
        }

        try:
            sync_thread.set_software_mode(new_mode, initial_palette=palette)
        except TypeError:
            sync_thread.set_software_mode(new_mode)
