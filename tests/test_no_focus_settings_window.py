"""No-focus mode must survive the settings window being open.

The main window used to drop ``WindowDoesNotAcceptFocus`` / ``WA_ShowWithout-
Activating`` / ``WS_EX_NOACTIVATE`` while the settings sidebar was visible, so
the settings UI could take focus.  Settings now live in their own top-level
``SettingsWindow``, so dropping them is unnecessary — and harmful: the main
window could then grab the foreground from Photoshop, whose Wintab context is
reopened on activation, making the next stylus stroke start without pressure.
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(scope="module")
def win(qapp):
    from ui.main_window import MainWindow
    w = MainWindow()
    w.sync_thread.sync_enabled = False
    yield w
    w.sync_thread.stop()
    w.sync_thread.wait(500)
    w.close()


def test_no_focus_flags_kept_while_settings_are_open(win, monkeypatch):
    """Simulate the settings sidebar being visible: the old code keyed the
    no-focus flags on exactly this condition and dropped them."""
    monkeypatch.setattr(win.settings_sidebar, "isVisible", lambda: True)
    win.cfg["noFocusMode"] = True
    win.update_window_flags()
    win.update_no_focus_policies()

    assert win.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert win.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_no_focus_flags_present_when_mode_enabled(win):
    win.cfg["noFocusMode"] = True
    win.update_window_flags()
    win.update_no_focus_policies()
    assert win.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_no_focus_flags_cleared_when_mode_disabled(win):
    """Disabling the mode must really restore normal activation."""
    win.cfg["noFocusMode"] = False
    win.update_window_flags()
    win.update_no_focus_policies()
    assert not (win.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert not win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert win.focusPolicy() == Qt.FocusPolicy.StrongFocus
