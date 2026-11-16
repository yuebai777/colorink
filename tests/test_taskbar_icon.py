"""Regression tests for the 任务栏图标 (showTaskbarIcon) setting.

On Windows, top-level windows carrying ``WS_EX_NOACTIVATE`` (from
``noFocusMode``) are omitted from the taskbar unless ``WS_EX_APPWINDOW`` is
also set. Furthermore, ``update_window_flags()`` must include
``Qt.WindowType.Window`` explicitly so ``windowFlags() == flags`` avoids
re-creating the window handle on every settings save, and ``WinIdChange``
must apply the Win32 extended styles before ``ShowWindow()`` makes the
window visible.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(scope="module")
def win(qapp, tmp_path_factory):
    from core import config as _config
    from ui.main_window import MainWindow

    cfg_dir = str(tmp_path_factory.mktemp("colorink_cfg"))
    orig_dir_fn = _config.get_user_data_dir
    _config.get_user_data_dir = lambda: cfg_dir
    try:
        w = MainWindow()
        w.sync_thread.sync_enabled = False
        w.cfg["onlyShowInCsp"] = False
        yield w
        w.sync_thread.stop()
        w.sync_thread.wait(500)
        w.close()
    finally:
        _config.get_user_data_dir = orig_dir_fn


def test_main_window_has_taskbar_title(win):
    assert win.windowTitle() == "Colorink"


@pytest.mark.parametrize(
    ("show_taskbar", "no_focus", "expected_type"),
    [
        (True, True, Qt.WindowType.Window),
        (True, False, Qt.WindowType.Window),
        (False, True, Qt.WindowType.Tool),
        (False, False, Qt.WindowType.Tool),
    ],
)
def test_window_type_matches_show_taskbar_icon(
    win, show_taskbar, no_focus, expected_type
):
    win.cfg["showTaskbarIcon"] = show_taskbar
    win.cfg["noFocusMode"] = no_focus
    win.update_window_flags()

    flags = win.windowFlags()
    assert (flags & Qt.WindowType.WindowType_Mask) == expected_type
    assert bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus) is no_focus


def test_update_window_flags_does_not_recreate_when_unchanged(win, monkeypatch):
    for show_taskbar in (False, True):
        for no_focus in (False, True):
            win.cfg["showTaskbarIcon"] = show_taskbar
            win.cfg["noFocusMode"] = no_focus
            win.update_window_flags()

            calls = 0
            orig_set_flags = win.setWindowFlags

            def counted_set_flags(f, _orig=orig_set_flags):
                nonlocal calls
                calls += 1
                _orig(f)

            with monkeypatch.context() as mp:
                mp.setattr(win, "setWindowFlags", counted_set_flags)
                win.update_window_flags()

            assert calls == 0, (
                f"Redundant setWindowFlags() for showTaskbarIcon={show_taskbar}, "
                f"noFocusMode={no_focus}"
            )


def test_winidchange_triggers_apply_win32_window_styles(win, qapp, monkeypatch):
    win.cfg["onlyShowInCsp"] = False
    win.show()
    qapp.processEvents()
    try:
        assert win.windowHandle() is not None
        assert win.effectiveWinId()

        called = []
        monkeypatch.setattr(
            win, "_apply_win32_window_styles", lambda: called.append(True)
        )
        QApplication.sendEvent(win, QEvent(QEvent.Type.WinIdChange))
        assert called == [True]
    finally:
        win.hide()


def test_update_ex_style_bits_noops_without_window_handle():
    from ui.panels.floating import _update_ex_style_bits

    class _NoHandleWidget:
        def windowHandle(self):
            return None

        def winId(self):  # pragma: no cover - must never be called
            raise AssertionError("winId() must not be called without windowHandle")

    _update_ex_style_bits(_NoHandleWidget(), set_mask=0x00040000)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 extended styles only apply on Windows")
def test_apply_taskbar_appwindow_preserves_noactivate_on_native_hwnd():
    import win32con
    import win32gui

    from ui.panels.floating import apply_no_activate, apply_taskbar_appwindow

    hwnd = win32gui.CreateWindowEx(
        win32con.WS_EX_TOOLWINDOW,
        "STATIC",
        "ColorinkTaskbarTest",
        0,
        0,
        0,
        10,
        10,
        0,
        0,
        0,
        None,
    )
    try:
        class _NativeStub:
            def windowHandle(self):
                return object()

            def effectiveWinId(self):
                return hwnd

        stub = _NativeStub()

        apply_no_activate(stub, True)
        apply_taskbar_appwindow(stub, True)
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        assert ex & win32con.WS_EX_NOACTIVATE
        assert ex & win32con.WS_EX_APPWINDOW
        assert not (ex & win32con.WS_EX_TOOLWINDOW)

        apply_taskbar_appwindow(stub, False)
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        assert ex & win32con.WS_EX_NOACTIVATE
        assert not (ex & win32con.WS_EX_APPWINDOW)

        apply_taskbar_appwindow(stub, True)
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        assert ex & win32con.WS_EX_NOACTIVATE
        assert ex & win32con.WS_EX_APPWINDOW
        assert not (ex & win32con.WS_EX_TOOLWINDOW)
    finally:
        win32gui.DestroyWindow(hwnd)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 extended styles only apply on Windows")
def test_main_window_native_ex_styles_across_toggles(win, qapp):
    import win32con
    import win32gui

    win.cfg["onlyShowInCsp"] = False
    win.cfg["showTaskbarIcon"] = False
    win.cfg["noFocusMode"] = True
    win.update_window_flags()
    win.show()
    qapp.processEvents()
    try:
        assert win.isVisible()
        hwnd = int(win.effectiveWinId())
        if not win32gui.IsWindow(hwnd):
            # Offscreen QPA uses non-Win32 synthetic handles; native HWND path
            # is covered by test_apply_taskbar_appwindow_preserves_noactivate_on_native_hwnd.
            return

        cases = [
            (True, True),
            (False, True),
            (True, True),
            (True, False),
            (False, False),
        ]
        for show_taskbar, no_focus in cases:
            win.cfg["showTaskbarIcon"] = show_taskbar
            win.cfg["noFocusMode"] = no_focus
            win.update_window_flags()
            qapp.processEvents()

            assert win.isVisible()
            hwnd = int(win.effectiveWinId())
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            assert bool(ex & win32con.WS_EX_APPWINDOW) is show_taskbar
            assert bool(ex & win32con.WS_EX_TOOLWINDOW) is (not show_taskbar)
            assert bool(ex & win32con.WS_EX_NOACTIVATE) is no_focus

        # Also verify the startup / CSP-hidden path: flags changed while hidden
        # so the HWND is recreated later when show() runs.
        win.hide()
        qapp.processEvents()
        win.cfg["showTaskbarIcon"] = True
        win.cfg["noFocusMode"] = True
        win.update_window_flags()
        win.show()
        qapp.processEvents()

        hwnd = int(win.effectiveWinId())
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        assert ex & win32con.WS_EX_APPWINDOW
        assert not (ex & win32con.WS_EX_TOOLWINDOW)
        assert ex & win32con.WS_EX_NOACTIVATE
    finally:
        win.hide()
