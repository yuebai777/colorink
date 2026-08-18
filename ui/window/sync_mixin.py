"""Drawing-software color sync (CSP/SAI/UDM/PS/companion) for MainWindow."""

import os
import sys

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtGui import QColor

from core import memory_sync
from ui.color_conversions import lab_to_rgb, oklab_to_rgb, oklch_to_rgb


class SyncMixin:
    def init_memory_sync(self):
        # Start background memory syncing thread
        self.sync_thread = memory_sync.MemorySyncThread(self)
        self.sync_thread.signals.color_changed.connect(self.on_external_color_changed)
        self.sync_thread.signals.transparent_changed.connect(self.on_external_transparent_changed)
        self.sync_thread.signals.active_slot_changed.connect(self.on_external_active_slot_changed)
        self.sync_thread.signals.status_changed.connect(self.on_sync_status_changed)
        self.sync_thread.signals.error_changed.connect(self.on_sync_error_changed)
        self._sync_error = None
        self._ps_perm_prompted = False

        # Set active software mode
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps", "companion"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)

        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
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
            # Off-slot change: update the swatch only, keep the wheel state.
            if slot == "fg":
                self.preview_box.fg_color = QColor(r, g, b)
                self._fg_transparent = False
            else:
                self.preview_box.bg_color = QColor(r, g, b)
                self._bg_transparent = False
            self.preview_box.set_transparent(slot, False)
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
        self._record_color_history()
        color = self.color_state.set_from("rgb", (r, g, b))
        self._project_color(color, source="sync", hsv=hsv)

    @pyqtSlot(int)
    def on_external_active_slot_changed(self, color_index):
        """The drawing software switched its active slot (e.g. user pressed
        X in CSP / picked the sub swatch). Follow it locally WITHOUT
        writing anything back — the swatch border, transparent-tile
        highlight and source tracking switch to the new slot.
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
        # 静默更新色轮显示对应槽颜色（block_signals → 不触发写入）
        try:
            self.color_wheel.set_color(col.red(), col.green(), col.blue(),
                                       block_signals=True)
        except Exception:
            pass

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
            if mode == 'csp' and color_index == 0:
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
        if slot != self.active_slot:
            if slot == "fg":
                self.select_fg_slot()
            else:
                self.select_bg_slot()

    @pyqtSlot(str, bool)
    def on_sync_status_changed(self, mode, connected):
        self._sync_status = (mode, connected)
        # Optionally update title bar text or border to show connection status
        mode_display = {"csp": "CSP", "sai": "SAI", "udm": "UDM", "ps": "PS", "companion": "手机"}.get(mode, mode.upper())
        status_text = f"Colorink ({mode_display} {'✓' if connected else '×'})"
        self.title_bar.title_label.setText(status_text)
        if mode == "companion" and hasattr(self, 'settings_sidebar'):
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
            "无法通过 COM 连接。\n\n"
            "是否以管理员身份重启 Colorink？\n"
            "（如果 Photoshop 是绿色版 / 未正常安装，提权也无法解决）",
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

    def _setup_companion_connection(self):
        if not hasattr(self, 'sync_thread'):
            return
        from core.csp_companion_sync import CSPCompanionSync
        ok = CSPCompanionSync.show_setup_dialog(self)
        if ok:
            c = self.sync_thread.companion_sync
            c._load_session()
            self.title_bar.title_label.setText("Colorink (手机 — 连接中...)")
        if hasattr(self, 'settings_sidebar'):
            self.settings_sidebar._refresh_companion_status()

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
            if src == "lab":
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
