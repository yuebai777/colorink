"""Regression tests for single-press follow-mouse hotkey & mouse/pen button binding."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QPoint, QRect, Qt

from core import global_hotkeys
from ui.window.hotkey_mixin import HotkeyMixin
from ui.window.picker_actions import PickerActionsMixin


def _make_checkbox(checked: bool = False):
    cb = SimpleNamespace(
        _checked=checked,
        _blocked=[],
        blockSignals=lambda flag: cb._blocked.append(flag),
        setChecked=lambda val: setattr(cb, "_checked", bool(val)),
        isChecked=lambda: cb._checked,
    )
    return cb


def _make_window(follow_active: bool = False, visible: bool = True):
    cb = _make_checkbox(follow_active)
    sidebar = SimpleNamespace(
        cfg={"followMouseEnabled": follow_active},
        cb_follow_mouse=cb,
        isVisible=lambda: True,
    )
    win = SimpleNamespace(
        follow_mouse_active=follow_active,
        cfg={"followMouseEnabled": follow_active},
        isVisible=lambda: visible,
        show_window_at_cursor=MagicMock(),
        settings_sidebar=sidebar,
    )
    return win


def test_follow_mouse_hotkey_moves_in_one_press_when_already_enabled():
    """Even when followMouseEnabled is already True, pressing followMouseKey
    must move to the cursor on the first press rather than toggling off."""
    win = _make_window(follow_active=True, visible=True)
    with patch("ui.window.hotkey_mixin.config.save_hotkey_config") as save:
        HotkeyMixin.on_hotkey_triggered(win, "followMouseKey")

    assert win.follow_mouse_active is True
    assert win.cfg["followMouseEnabled"] is True
    win.show_window_at_cursor.assert_called_once_with()
    save.assert_not_called()


def test_follow_mouse_hotkey_moves_every_consecutive_press():
    """Repeated presses at new pen positions must each summon in 1 press."""
    win = _make_window(follow_active=False, visible=True)
    with patch("ui.window.hotkey_mixin.config.save_hotkey_config") as save:
        HotkeyMixin.on_hotkey_triggered(win, "followMouseKey")
        HotkeyMixin.on_hotkey_triggered(win, "followMouseKey")
        HotkeyMixin.on_hotkey_triggered(win, "followMouseKey")

    assert win.follow_mouse_active is True
    assert win.cfg["followMouseEnabled"] is True
    assert win.settings_sidebar.cb_follow_mouse.isChecked() is True
    assert win.settings_sidebar.cfg["followMouseEnabled"] is True
    assert win.show_window_at_cursor.call_count == 3
    save.assert_called_once_with(win.cfg)


def test_follow_mouse_hotkey_summons_when_window_is_hidden():
    """Pressing followMouseKey while hidden must still show at cursor in 1 press."""
    win = _make_window(follow_active=True, visible=False)
    with patch("ui.window.hotkey_mixin.config.save_hotkey_config"):
        HotkeyMixin.on_hotkey_triggered(win, "followMouseKey")

    win.show_window_at_cursor.assert_called_once_with()


def test_show_window_at_cursor_unminimizes_moves_shows_and_raises():
    """show_window_at_cursor clears _user_hidden/minimized state and raises."""
    calls = []
    win = SimpleNamespace(
        _user_hidden=True,
        cfg={"lockWindowPosition": False},
        isMinimized=lambda: True,
        windowState=lambda: Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive,
        setWindowState=lambda s: calls.append(("state", s)),
        width=lambda: 320,
        height=lambda: 700,
        move=lambda x, y: calls.append(("move", x, y)),
        show=lambda: calls.append(("show",)),
        raise_=lambda: calls.append(("raise",)),
    )
    fake_screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080))
    with patch("PyQt6.QtGui.QCursor.pos", return_value=QPoint(1000, 600)), \
         patch("PyQt6.QtWidgets.QApplication.screenAt", return_value=fake_screen):
        PickerActionsMixin.show_window_at_cursor(win)

    assert win._user_hidden is False
    assert ("state", Qt.WindowState.WindowActive) in calls
    assert ("move", 840, 250) in calls
    assert ("show",) in calls
    assert ("raise",) in calls


def test_mouse_hotkey_triggers_on_mouse_double_event_after_pen_lift(monkeypatch):
    """mouse._winmouse converts DOWN within GetDoubleClickTime of a prior
    button event (like pen tip LEFT UP) into DOUBLE; bind_mouse_hotkey must
    subscribe to both DOWN and DOUBLE so the first press isn't dropped."""
    import mouse as _mouse

    captured = {}

    def fake_on_button(cb, buttons, types):
        captured["cb"] = cb
        captured["buttons"] = buttons
        captured["types"] = types
        return "fake-handler"

    emitted = []
    monkeypatch.setattr(global_hotkeys._mouse, "on_button", fake_on_button)
    monkeypatch.setattr(global_hotkeys._mouse, "unhook", lambda h: None)
    monkeypatch.setattr(global_hotkeys, "_bound_mouse_hotkeys", {})
    monkeypatch.setattr(global_hotkeys, "_bound_mouse_names", {})
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", False)
    monkeypatch.setattr(
        global_hotkeys,
        "get_hotkey_signals",
        lambda: SimpleNamespace(triggered=SimpleNamespace(emit=emitted.append)),
    )

    global_hotkeys.bind_mouse_hotkey("followMouseKey", "X1")

    assert _mouse.DOWN in captured["types"]
    assert _mouse.DOUBLE in captured["types"]

    # Simulate mouse library's handler behavior with a DOUBLE event
    btn_evt = _mouse._mouse_event.ButtonEvent(_mouse.DOUBLE, _mouse.X, 1.0)
    if btn_evt.event_type in captured["types"] and btn_evt.button in captured["buttons"]:
        captured["cb"]()

    assert emitted == ["followMouseKey"]
