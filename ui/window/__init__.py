"""Concern mixins for the main window, split out of the former god class.

MainWindow inherits these so the tray, hotkey and software-sync plumbing live
in their own modules while still sharing one ``self``.
"""

from ui.window.color_slots_mixin import ColorSlotsMixin
from ui.window.hotkey_mixin import HotkeyMixin
from ui.window.sync_mixin import SyncMixin
from ui.window.tray_mixin import TrayMixin

__all__ = ["ColorSlotsMixin", "HotkeyMixin", "SyncMixin", "TrayMixin"]
