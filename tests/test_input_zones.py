"""Shell-zone gate for mouse-button hotkeys, and the settings escape hatch.

Two safety features ship together here, and both are the kind that only bite
the user when they are broken:

* ``core/input_zones`` decides whether the physical cursor is over Windows
  shell UI (taskbar, tray, overflow flyout, quick settings, jump lists). The
  gate lets a mouse-button hotkey fall through to the system there, so
  Colorink's always-on-top panel never covers a native context menu. Its whole
  contract is **fail-open**: any missing dependency, failed API call or
  unexpected exception must mean "not a shell zone", because the alternative is
  silently swallowing the user's clicks.
* ``__openSettings`` (Ctrl+Alt+Shift+,) is the unbindable combo that always
  opens settings, for when the right button is occupied and the tray is
  unreachable. It must *show*, never toggle — mashing it in a panic must not
  close the window it just opened.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from core import global_hotkeys, input_zones


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── point-in-rect: half-open, signed physical pixels ───────────────────────


def test_in_any_rect_is_half_open_on_both_edges():
    rects = ((0, 1040, 1920, 1080),)
    assert input_zones._in_any_rect(rects, 10, 1050) is True
    assert input_zones._in_any_rect(rects, 0, 1040) is True      # left/top inclusive
    assert input_zones._in_any_rect(rects, 1919, 1079) is True   # last pixel
    assert input_zones._in_any_rect(rects, 1920, 1050) is False  # right exclusive
    assert input_zones._in_any_rect(rects, 10, 1039) is False    # above the taskbar


def test_in_any_rect_handles_negative_origin_monitors():
    """A monitor left of / above the primary has negative physical coords."""
    rects = ((-1920, 0, 0, 1080),)
    assert input_zones._in_any_rect(rects, -100, 500) is True
    assert input_zones._in_any_rect(rects, -1920, 500) is True
    assert input_zones._in_any_rect(rects, 0, 500) is False
    # An abs()/unsigned-cast mistake would make this look like the primary
    # monitor's origin and wrongly report a hit.
    assert input_zones._in_any_rect(rects, 1920, 500) is False


# ── should_ignore / ignore_at_cursor: fail-open ────────────────────────────


def test_should_ignore_true_inside_taskbar_rect(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "taskbar_rects", lambda: ((0, 1040, 1920, 1080),))
    monkeypatch.setattr(input_zones, "is_shell_window", lambda x, y: False)

    assert input_zones.should_ignore(100, 1050) is True
    assert input_zones.should_ignore(100, 900) is False


def test_should_ignore_falls_back_to_the_class_check(monkeypatch):
    """With no taskbar snapshot yet, the shell-class check still decides."""
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "taskbar_rects", lambda: ())
    monkeypatch.setattr(input_zones, "is_shell_window", lambda x, y: True)

    assert input_zones.should_ignore(100, 900) is True


def test_should_ignore_fails_open_without_pywin32(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", False)
    assert input_zones.should_ignore(100, 1050) is False


def test_should_ignore_fails_open_when_a_check_raises(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)

    def _boom():
        raise OSError("explorer is busy")

    monkeypatch.setattr(input_zones, "taskbar_rects", _boom)

    assert input_zones.should_ignore(1, 2) is False


def test_ignore_at_cursor_fails_open_when_the_cursor_query_raises(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "win32api", SimpleNamespace(
        GetCursorPos=mock.Mock(side_effect=OSError("no desktop"))))

    assert input_zones.ignore_at_cursor() is False


def test_ignore_at_cursor_reads_the_physical_cursor(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "win32api",
                        SimpleNamespace(GetCursorPos=lambda: (100, 1050)))
    monkeypatch.setattr(input_zones, "taskbar_rects", lambda: ((0, 1040, 1920, 1080),))
    monkeypatch.setattr(input_zones, "is_shell_window", lambda x, y: False)

    assert input_zones.ignore_at_cursor() is True


# ── is_shell_window: class table ───────────────────────────────────────────


@pytest.mark.parametrize("class_name", ["Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                                        "TrayNotifyWnd", "NotifyIconOverflowWindow"])
def test_is_shell_window_recognises_taskbar_and_tray(monkeypatch, class_name):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "_root_window_at", lambda x, y: (42, class_name))

    assert input_zones.is_shell_window(0, 0) is True


def test_is_shell_window_xaml_popup_only_counts_when_owned_by_explorer(monkeypatch):
    """``Xaml_WindowedPopupClass`` is generic — any WinUI popup uses it."""
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "_root_window_at",
                        lambda x, y: (42, "Xaml_WindowedPopupClass"))
    monkeypatch.setattr(input_zones, "_process_image_name", lambda hwnd: "explorer.exe")
    assert input_zones.is_shell_window(0, 0) is True

    monkeypatch.setattr(input_zones, "_process_image_name", lambda hwnd: "photoshop.exe")
    assert input_zones.is_shell_window(0, 0) is False


def test_is_shell_window_ignores_ordinary_application_windows(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "_root_window_at", lambda x, y: (42, "Photoshop"))
    monkeypatch.setattr(input_zones, "_process_image_name", lambda hwnd: "photoshop.exe")

    assert input_zones.is_shell_window(0, 0) is False


def test_is_shell_window_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(input_zones, "_AVAILABLE", True)
    monkeypatch.setattr(input_zones, "_root_window_at", mock.Mock(side_effect=OSError()))

    assert input_zones.is_shell_window(0, 0) is False


# ── global_hotkeys: the gate wired into the mouse callback ─────────────────


class _FakeZones:
    def __init__(self, *, available=True, ignore=False):
        self._available = available
        self._ignore = ignore
        self.refreshes = 0

    def is_available(self):
        return self._available

    def start_background_refresh(self):
        self.refreshes += 1

    def ignore_at_cursor(self):
        return self._ignore


class _FakeSignals:
    def __init__(self):
        self.emitted = []
        self.triggered = SimpleNamespace(emit=self.emitted.append)


def _capture_mouse_callback(monkeypatch):
    """Stub the OS mouse hook and hand back the callback it was given."""
    captured = {}
    monkeypatch.setattr(
        global_hotkeys._mouse, "on_button",
        lambda cb, buttons, types: captured.setdefault("cb", cb))
    monkeypatch.setattr(global_hotkeys._mouse, "unhook", lambda handler: None)
    monkeypatch.setattr(global_hotkeys, "_bound_mouse_hotkeys", {})
    monkeypatch.setattr(global_hotkeys, "_bound_mouse_names", {})
    return captured


def test_mouse_hotkey_falls_through_over_a_shell_zone(monkeypatch):
    """Over the taskbar/tray the click must reach the system untouched."""
    captured = _capture_mouse_callback(monkeypatch)
    signals = _FakeSignals()
    monkeypatch.setattr(global_hotkeys, "get_hotkey_signals", lambda: signals)
    monkeypatch.setattr(global_hotkeys, "input_zones",
                        _FakeZones(available=True, ignore=True))
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", True)

    global_hotkeys.bind_mouse_hotkey("hideWindowKey", "X2")
    captured["cb"]()

    assert signals.emitted == []


def test_mouse_hotkey_still_fires_off_the_shell_zone(monkeypatch):
    captured = _capture_mouse_callback(monkeypatch)
    signals = _FakeSignals()
    monkeypatch.setattr(global_hotkeys, "get_hotkey_signals", lambda: signals)
    monkeypatch.setattr(global_hotkeys, "input_zones",
                        _FakeZones(available=True, ignore=False))
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", True)

    global_hotkeys.bind_mouse_hotkey("hideWindowKey", "X2")
    captured["cb"]()

    assert signals.emitted == ["hideWindowKey"]


def test_mouse_hotkey_skips_the_gate_when_the_filter_is_off(monkeypatch):
    captured = _capture_mouse_callback(monkeypatch)
    signals = _FakeSignals()
    monkeypatch.setattr(global_hotkeys, "get_hotkey_signals", lambda: signals)
    monkeypatch.setattr(global_hotkeys, "input_zones",
                        _FakeZones(available=True, ignore=True))
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", False)

    global_hotkeys.bind_mouse_hotkey("hideWindowKey", "X2")
    captured["cb"]()

    assert signals.emitted == ["hideWindowKey"]


def test_mouse_hotkey_survives_a_gate_error(monkeypatch):
    """A raising gate must not eat the click — it fails open."""
    captured = _capture_mouse_callback(monkeypatch)
    signals = _FakeSignals()
    monkeypatch.setattr(global_hotkeys, "get_hotkey_signals", lambda: signals)
    zones = _FakeZones(available=True)
    zones.ignore_at_cursor = mock.Mock(side_effect=OSError("shell died"))
    monkeypatch.setattr(global_hotkeys, "input_zones", zones)
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", True)

    global_hotkeys.bind_mouse_hotkey("hideWindowKey", "X2")
    captured["cb"]()

    assert signals.emitted == ["hideWindowKey"]


@pytest.mark.parametrize("zones", [_FakeZones(available=False), None],
                         ids=["pywin32-missing", "module-missing"])
def test_zone_gate_is_inactive_when_unavailable(monkeypatch, zones):
    monkeypatch.setattr(global_hotkeys, "input_zones", zones)
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", True)

    assert global_hotkeys._zone_gate_active() is False


def test_enabling_the_filter_kicks_the_taskbar_snapshot(monkeypatch):
    """The first gated click must already have a snapshot to test against."""
    zones = _FakeZones(available=True)
    monkeypatch.setattr(global_hotkeys, "input_zones", zones)
    monkeypatch.setattr(global_hotkeys, "_zone_filter_enabled", False)

    global_hotkeys.set_zone_filter_enabled(True)

    assert global_hotkeys._zone_gate_active() is True
    assert zones.refreshes == 1


# ── the unbindable settings escape hatch ───────────────────────────────────


def test_open_settings_fallback_shows_instead_of_toggling(qapp):
    """Mashing Ctrl+Alt+Shift+, in a panic must not close what it opened."""
    from PyQt6.QtCore import QObject

    from ui.window.hotkey_mixin import HotkeyMixin

    calls = []

    class FakeWindow(QObject, HotkeyMixin):
        def _show_settings_window(self):
            calls.append("show")

        def toggle_settings_sidebar(self):
            calls.append("toggle")

    window = FakeWindow()
    window.on_hotkey_triggered("__openSettings")

    assert calls == ["show"]


def test_update_hotkey_bindings_registers_the_fallback(monkeypatch, qapp):
    """It is bound on every rebind, whatever the user's config says."""
    from ui.window.hotkey_mixin import HotkeyMixin

    bound = []
    monkeypatch.setattr(global_hotkeys, "unbind_all", lambda: None)
    monkeypatch.setattr(global_hotkeys, "set_zone_filter_enabled", lambda flag: None)
    monkeypatch.setattr(global_hotkeys, "bind_hotkey",
                        lambda htype, value, force=False: bound.append((htype, value, force)))
    monkeypatch.setattr(global_hotkeys, "bind_mouse_hotkey", lambda htype, value: None)

    class FakeWindow(HotkeyMixin):
        def __init__(self):
            self.cfg = {"pickKey": "", "hideWindowKey": "", "followMouseKey": "",
                        "grayscaleFilterKey": "", "toggleLabKey": "",
                        "toggleLabGlobalKey": "", "toggleTitleBarKey": ""}

    FakeWindow().update_hotkey_bindings()

    assert ("__openSettings", "ctrl+alt+shift+,", True) in bound


def test_update_hotkey_bindings_pushes_the_zone_filter_flag(monkeypatch, qapp):
    """Toggling the checkbox must reach the live gate without a restart."""
    from ui.window.hotkey_mixin import HotkeyMixin

    seen = []
    monkeypatch.setattr(global_hotkeys, "unbind_all", lambda: None)
    monkeypatch.setattr(global_hotkeys, "set_zone_filter_enabled", seen.append)
    monkeypatch.setattr(global_hotkeys, "bind_hotkey",
                        lambda htype, value, force=False: None)
    monkeypatch.setattr(global_hotkeys, "bind_mouse_hotkey", lambda htype, value: None)

    class FakeWindow(HotkeyMixin):
        def __init__(self, cfg):
            self.cfg = cfg

    FakeWindow({"mouseHotkeyZoneFilter": False}).update_hotkey_bindings()
    FakeWindow({}).update_hotkey_bindings()  # default must be on

    assert seen == [False, True]
