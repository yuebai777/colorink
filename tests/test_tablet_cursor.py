"""Pen-hover cursor sync — regression tests for the color wheel crosshair.

On Windows, pen hover is delivered as QTabletEvent(TabletMove) and Qt does
not re-apply widget cursors on that path, so the wheel's CrossCursor never
shows with a tablet pen.  ``MainWindow._sync_tablet_cursor`` mirrors the
mouse cursor logic (resize borders → drag-blank → widget-under-pen shape)
and forces the OS cursor natively.

The native-forcing tests need a QApplication (QCursor construction requires
one in PyQt6), following the same module-scoped ``qapp`` fixture convention
as the rest of the suite.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _window(**overrides) -> SimpleNamespace:
    """Minimal MainWindow stand-in exposing just the attributes the cursor
    sync reads (same style as tests/test_window_height.py)."""
    cursor = SimpleNamespace(shape=mock.MagicMock(return_value=object()))
    attrs = {
        "cfg": {"lockWindowSize": False},
        "resizing": False,
        "color_wheel": SimpleNamespace(dragging=None),
        "slider_widgets": {},
        "mapFromGlobal": lambda p: p,
        "get_resize_direction": mock.MagicMock(return_value=None),
        "cursor": mock.MagicMock(return_value=cursor),
        "unsetCursor": mock.MagicMock(),
        "setCursor": mock.MagicMock(),
        "window": lambda: "MAIN",
        "_sync_resize_cursor": mock.MagicMock(),
        "_force_cursor_shape": mock.MagicMock(),
        "_blank_cursor_handle": mock.MagicMock(return_value=424242),
        "_forced_blank_cursor": None,
    }
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


def _sync(window, pos=QPoint(50, 60)):
    MainWindow._sync_tablet_cursor(window, pos)
    return window._force_cursor_shape


# ── _sync_tablet_cursor: widget-under-pen resolution ────────────────────────


def test_wheel_crosshair_is_forced_for_pen_hover():
    wheel = SimpleNamespace(
        window=lambda: "MAIN",
        cursor=mock.MagicMock(
            return_value=SimpleNamespace(
                shape=mock.MagicMock(return_value=Qt.CursorShape.CrossCursor)
            )
        ),
    )
    window = _window()
    with mock.patch("ui.window.layout.QApplication.widgetAt", return_value=wheel) as widget_at:
        force = _sync(window)
    widget_at.assert_called_once_with(QPoint(50, 60))
    force.assert_called_once_with(Qt.CursorShape.CrossCursor)


def test_arrow_widget_forces_arrow():
    arrow_widget = SimpleNamespace(
        window=lambda: "MAIN",
        cursor=mock.MagicMock(
            return_value=SimpleNamespace(
                shape=mock.MagicMock(return_value=Qt.CursorShape.ArrowCursor)
            )
        ),
    )
    window = _window()
    with mock.patch("ui.window.layout.QApplication.widgetAt", return_value=arrow_widget):
        force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.ArrowCursor)


def test_pen_outside_the_window_forces_arrow():
    window = _window()
    with mock.patch("ui.window.layout.QApplication.widgetAt", return_value=None):
        force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.ArrowCursor)


def test_pen_over_foreign_window_forces_arrow():
    foreign = SimpleNamespace(
        window=lambda: "OTHER",
        cursor=mock.MagicMock(
            return_value=SimpleNamespace(
                shape=mock.MagicMock(return_value=Qt.CursorShape.CrossCursor)
            )
        ),
    )
    window = _window()
    with mock.patch("ui.window.layout.QApplication.widgetAt", return_value=foreign):
        force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.ArrowCursor)


def test_active_wheel_drag_blanks_cursor():
    window = _window(color_wheel=SimpleNamespace(dragging="hue"))
    force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.BlankCursor)


def test_active_slider_drag_blanks_cursor():
    slider = SimpleNamespace(isSliderDown=mock.MagicMock(return_value=True))
    window = _window(slider_widgets={"h": (slider, object())})
    force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.BlankCursor)


def test_resize_border_forces_size_cursor():
    window = _window(get_resize_direction=mock.MagicMock(return_value="left"))
    force = _sync(window)
    force.assert_called_once_with(Qt.CursorShape.SizeHorCursor)


def test_locked_size_still_forces_wheel_crosshair():
    """lockWindowSize must NOT turn the pen into an arrow over the wheel —
    it only disables edge-resize cursors (regression from the first 1.6.15
    fix, which forced arrow whenever the window size was locked)."""
    wheel = SimpleNamespace(
        window=lambda: "MAIN",
        cursor=mock.MagicMock(
            return_value=SimpleNamespace(
                shape=mock.MagicMock(return_value=Qt.CursorShape.CrossCursor)
            )
        ),
    )
    window = _window(cfg={"lockWindowSize": True})
    with mock.patch("ui.window.layout.QApplication.widgetAt", return_value=wheel) as widget_at:
        force = _sync(window)
    widget_at.assert_called_once_with(QPoint(50, 60))
    force.assert_called_once_with(Qt.CursorShape.CrossCursor)
    window._sync_resize_cursor.assert_not_called()


def test_locked_size_skips_resize_border_parsing():
    window = _window(cfg={"lockWindowSize": True})
    with mock.patch("ui.window.layout.QApplication.widgetAt") as widget_at:
        force = _sync(window)
    widget_at.assert_called_once()
    window.get_resize_direction.assert_not_called()
    force.assert_called_once()  # arrow or widget shape from the pen position


def test_resizing_is_ignored():
    window = _window(resizing=True)
    force = _sync(window)
    force.assert_not_called()


# ── _force_cursor_shape: native cursor forcing ──────────────────────────────


def test_system_shape_calls_native_setcursor(qapp):
    with mock.patch("ctypes.windll.user32.SetCursor") as set_cursor:
        window = _window()
        MainWindow._force_cursor_shape(window, Qt.CursorShape.CrossCursor)
    set_cursor.assert_called_once()
    handle = set_cursor.call_args[0][0]
    assert isinstance(handle, int)
    assert handle > 0


def test_force_blank_uses_kept_alive_handle(qapp):
    with mock.patch("ctypes.windll.user32.SetCursor") as set_cursor:
        window = _window()
        MainWindow._force_cursor_shape(window, Qt.CursorShape.BlankCursor)
    window._blank_cursor_handle.assert_called_once_with()
    set_cursor.assert_called_once_with(424242)


def test_blank_cursor_handle_created_once_and_kept_alive():
    window = _window()
    h1 = MainWindow._blank_cursor_handle(window)
    h2 = MainWindow._blank_cursor_handle(window)
    assert h1 == h2
    assert h1 > 0
    # Cache tuple keeps handle + AND/XOR masks alive for the OS.
    assert window._forced_blank_cursor is not None
    assert window._forced_blank_cursor[0] == h1


def test_unmapped_shape_skips_setcursor(qapp):
    with mock.patch("ctypes.windll.user32.SetCursor") as set_cursor:
        window = _window()
        MainWindow._force_cursor_shape(window, Qt.CursorShape.BusyCursor)
    set_cursor.assert_not_called()


def test_failure_is_silent(qapp):
    with mock.patch("ctypes.windll.user32.SetCursor", side_effect=OSError("no user32")):
        window = _window()
        MainWindow._force_cursor_shape(window, Qt.CursorShape.CrossCursor)  # must not raise
