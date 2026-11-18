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
_PREV_MODS = {_m: sys.modules.get(_m) for _m in _MODS}
for _m in _MODS:
    sys.modules[_m] = MagicMock()

setattr(sys.modules["brush_color_spaces"], "PSColorSpace", MagicMock())
for _a in ("GetForegroundWindow", "GetWindowText", "GetWindowLong",
           "SetWindowLong", "IsWindowVisible", "IsIconic",
           "ShowWindowAsync", "BringWindowToTop", "SetForegroundWindow",
           "EnumWindows", "GetWindowThreadProcessId", "GetParent",
           "GetWindow", "GetWindowTextLengthW"):
    setattr(sys.modules["win32gui"], _a, MagicMock(return_value=False))

from ui.main_window import MainWindow, _title_bar_content_offset

for _m, _orig in _PREV_MODS.items():
    if _orig is None:
        sys.modules.pop(_m, None)
    else:
        sys.modules[_m] = _orig


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
        "pickKey": "Ctrl+Alt+Q",
        "hideWindowKey": "Ctrl+Alt+Y",
        "toggleTitleBarKey": "Ctrl+Alt+K",
        "followMouseKey": "Ctrl+Alt+J",
        "grayscaleFilterKey": "Ctrl+Alt+D",
        "toggleLabGlobalKey": "Ctrl+Alt+L",
        "toggleLabKey": "Space",
    })
    with patch("ui.window.hotkey_mixin.global_hotkeys.unbind_all") as unbind, \
         patch("ui.window.hotkey_mixin.global_hotkeys.bind_hotkey") as bind, \
         patch("ui.window.hotkey_mixin.global_hotkeys.bind_mouse_hotkey"), \
         patch("ui.window.hotkey_mixin.is_mouse_hotkey", return_value=False):
        MainWindow.update_hotkey_bindings(fake)

    unbind.assert_called_once_with()
    assert any(call.args[0] == "toggleTitleBarKey" for call in bind.call_args_list)


def test_hotkey_trigger_toggles_title_bar():
    fake = SimpleNamespace(toggle_title_bar=MagicMock())
    MainWindow.on_hotkey_triggered(fake, "toggleTitleBarKey")
    fake.toggle_title_bar.assert_called_once_with()


def test_hotkey_trigger_hide_window_uses_toggle_visibility():
    fake = SimpleNamespace(toggle_visibility=MagicMock())
    MainWindow.on_hotkey_triggered(fake, "hideWindowKey")
    fake.toggle_visibility.assert_called_once_with()


def test_set_title_bar_visible_hides_and_persists():
    fake = SimpleNamespace(
        cfg={"showTitleBar": True},
        title_bar=FakeTitleBar(True),
        update_geometries=MagicMock(),
        _adjust_content_height=MagicMock(),
        tray_title_action=MagicMock(),
    )
    with patch("ui.window.tray_mixin.config.save_hotkey_config") as save:
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
    with patch("ui.window.tray_mixin.config.save_hotkey_config"):
        MainWindow.set_title_bar_visible(fake, True)

    assert fake.title_bar.isVisible() is True
    assert fake.cfg["showTitleBar"] is True
    fake.update_geometries.assert_called_once_with()


def test_hidden_title_bar_keeps_top_border_offset():
    title_bar = FakeTitleBar(False)
    layout = SimpleNamespace(contentsMargins=lambda: SimpleNamespace(top=lambda: 4))
    assert _title_bar_content_offset(title_bar, layout) == 4


def test_hidden_title_bar_without_border_has_zero_top_offset():
    title_bar = FakeTitleBar(False)
    layout = SimpleNamespace(contentsMargins=lambda: SimpleNamespace(top=lambda: 0))
    assert _title_bar_content_offset(title_bar, layout) == 0


class _ToggleStub:
    def __init__(self, checked: bool = True):
        self._checked = checked
        self._blocked = False

    def blockSignals(self, blocked: bool):
        self._blocked = bool(blocked)

    def setChecked(self, checked: bool):
        self._checked = bool(checked)

    def isChecked(self) -> bool:
        return self._checked


def _make_fake_window():
    calls = []
    title_bar = FakeTitleBar(True)
    sidebar = SimpleNamespace(
        cb_show_title_bar=_ToggleStub(True),
        cb_show_window_border=_ToggleStub(True),
    )
    fake = SimpleNamespace(
        cfg={"showTitleBar": True, "showWindowBorder": True, "uiScale": 100},
        title_bar=title_bar,
        tray_title_action=_ToggleStub(True),
        tray_border_action=_ToggleStub(True),
        settings_sidebar=sidebar,
        apply_style=lambda *a, **kw: calls.append("apply_style"),
        apply_theme=lambda *a, **kw: calls.append("apply_theme"),
        apply_layout=lambda *a, **kw: calls.append("apply_layout"),
        update_geometries=lambda *a, **kw: calls.append("update_geometries"),
        _adjust_content_height=lambda *a, **kw: calls.append("_adjust_content_height"),
        save_window_geometry=lambda *a, **kw: calls.append("save_window_geometry"),
        _calls=calls,
    )
    return fake


def test_set_title_bar_visible_updates_style_and_settings_checkbox():
    fake = _make_fake_window()
    with patch("ui.window.tray_mixin.config.save_hotkey_config") as save:
        MainWindow.set_title_bar_visible(fake, False)
    assert fake.cfg["showTitleBar"] is False
    assert fake.title_bar.isVisible() is False
    assert fake.tray_title_action.isChecked() is False
    assert fake.settings_sidebar.cb_show_title_bar.isChecked() is False
    assert "apply_style" in fake._calls
    assert fake._calls.count("update_geometries") == 1
    assert fake._calls.count("_adjust_content_height") == 1
    save.assert_called_once()


def test_set_window_border_visible_toggles_and_persists():
    fake = _make_fake_window()
    with patch("ui.window.tray_mixin.config.save_hotkey_config") as save:
        MainWindow.set_window_border_visible(fake, False)
        assert fake.cfg["showWindowBorder"] is False
        assert fake.tray_border_action.isChecked() is False
        assert fake.settings_sidebar.cb_show_window_border.isChecked() is False
        assert "apply_theme" in fake._calls
        assert "_adjust_content_height" in fake._calls

        fake._calls.clear()
        MainWindow.set_window_border_visible(fake, True)
        assert fake.cfg["showWindowBorder"] is True
        assert fake.tray_border_action.isChecked() is True
        assert fake.settings_sidebar.cb_show_window_border.isChecked() is True
        assert "apply_theme" in fake._calls
        assert "_adjust_content_height" in fake._calls
    assert save.call_count == 2
