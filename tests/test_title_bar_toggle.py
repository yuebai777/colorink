"""Tests for the title-bar visibility toggle (hotkey, tray, layout offset)."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_MODS = [
    "brush_color_spaces", "win32gui", "win32api", "win32con",
    "win32process", "psutil", "win32com", "win32com.client",
    "win32com.client.dynamic", "win32com.client.gencache",
    "win32com.client.CLSIDToClass", "pythoncom",
]
for _m in _MODS:
    sys.modules[_m] = MagicMock()

setattr(sys.modules["brush_color_spaces"], "PSColorSpace", MagicMock())
for _a in ("GetForegroundWindow", "GetWindowText", "GetWindowLong",
           "SetWindowLong", "IsWindowVisible", "IsIconic",
           "ShowWindowAsync", "BringWindowToTop", "SetForegroundWindow",
           "EnumWindows", "GetWindowThreadProcessId", "GetParent",
           "GetWindow", "GetWindowTextLengthW"):
    setattr(sys.modules["win32gui"], _a, MagicMock(return_value=False))

import ui.main_window as mw
from ui.main_window import MainWindow, _title_bar_content_offset


class FakeTitleBar:
    def __init__(self, visible: bool = True):
        self._visible = visible
        self._height = 28

    def isVisible(self) -> bool:
        return self._visible

    def setVisible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def height(self) -> int:
        return self._height

    def sizeHint(self):
        return SimpleNamespace(height=lambda: self._height)


def test_hotkey_binding_registers_title_bar_toggle():
    fake = SimpleNamespace(cfg={
        "pickKey": "F11",
        "hideWindowKey": "Ctrl+H",
        "toggleTitleBarKey": "Ctrl+Shift+T",
        "followMouseKey": "Ctrl+R",
        "grayscaleFilterKey": "Ctrl+G",
        "toggleLabGlobalKey": "Ctrl+L",
        "toggleLabKey": "Space",
    })
    with patch.object(mw.global_hotkeys, "unbind_all") as unbind, \
         patch.object(mw.global_hotkeys, "bind_hotkey") as bind, \
         patch.object(mw.global_hotkeys, "bind_mouse_hotkey"), \
         patch.object(mw, "is_mouse_hotkey", return_value=False):
        MainWindow.update_hotkey_bindings(fake)

    unbind.assert_called_once_with()
    assert any(call.args[0] == "toggleTitleBarKey" for call in bind.call_args_list)


def test_hotkey_trigger_toggles_title_bar():
    fake = SimpleNamespace(toggle_title_bar=MagicMock())
    MainWindow.on_hotkey_triggered(fake, "toggleTitleBarKey")
    fake.toggle_title_bar.assert_called_once_with()


def test_set_title_bar_visible_hides_and_persists():
    fake = SimpleNamespace(
        cfg={"showTitleBar": True},
        title_bar=FakeTitleBar(True),
        update_geometries=MagicMock(),
        _adjust_content_height=MagicMock(),
        tray_title_action=MagicMock(),
    )
    with patch("ui.main_window.config.save_hotkey_config") as save:
        MainWindow.set_title_bar_visible(fake, False)

    assert fake.title_bar.isVisible() is False
    assert fake.cfg["showTitleBar"] is False
    save.assert_called_once_with(fake.cfg)
    fake.update_geometries.assert_called_once_with()
    fake._adjust_content_height.assert_called_once_with()
    fake.tray_title_action.setChecked.assert_called_once_with(False)


def test_set_title_bar_visible_shows_again():
    fake = SimpleNamespace(
        cfg={"showTitleBar": False},
        title_bar=FakeTitleBar(False),
        update_geometries=MagicMock(),
        _adjust_content_height=MagicMock(),
        tray_title_action=MagicMock(),
    )
    with patch("ui.main_window.config.save_hotkey_config"):
        MainWindow.set_title_bar_visible(fake, True)

    assert fake.title_bar.isVisible() is True
    assert fake.cfg["showTitleBar"] is True
    fake.update_geometries.assert_called_once_with()


def test_hidden_title_bar_keeps_top_border_offset():
    title_bar = FakeTitleBar(False)
    layout = SimpleNamespace(contentsMargins=lambda: SimpleNamespace(top=lambda: 4))
    assert _title_bar_content_offset(title_bar, layout) == 4
