"""Concern mixins for the main window, split out of the former god class.

MainWindow inherits these so the tray, hotkey and software-sync plumbing live
in their own modules while still sharing one ``self``.
"""

from ui.window.color_slots_mixin import ColorSlotsMixin
from ui.window.color_updates import ColorUpdatesMixin
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.hotkey_mixin import HotkeyMixin
from ui.window.layout import LayoutMixin
from ui.window.panels_mixin import PanelProviderMixin
from ui.window.picker_actions import PickerActionsMixin
from ui.window.sync_mixin import SyncMixin
from ui.window.theme import ThemeMixin
from ui.window.tray_mixin import TrayMixin

__all__ = [
    "ColorSlotsMixin",
    "ColorUpdatesMixin",
    "FloatingPanelsMixin",
    "HotkeyMixin",
    "LayoutMixin",
    "PanelProviderMixin",
    "PickerActionsMixin",
    "SyncMixin",
    "ThemeMixin",
    "TrayMixin",
]
