"""Tests for Photoshop WinTab pressure fix & Win10 compatibility enhancements.

Validates:
1. Native WM_MOUSEACTIVATE (0x0021) interception on MainWindow returning MA_NOACTIVATE (3)
   when noFocusMode is active, preventing Photoshop from receiving background deactivation.
2. Native WM_MOUSEACTIVATE interception on FloatingPanelWindow when _no_focus is active.
3. Sync debouncing during color wheel and LAB plane dragging (avoiding IPC script flood).
4. Flush write upon interactionFinished in on_interaction_finished.
"""

import ctypes
import ctypes.wintypes
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.panels.floating import FloatingPanelWindow
from ui.window.sync_mixin import SyncMixin
from ui.window.color_updates import ColorUpdatesMixin


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── 1. MainWindow nativeEvent: WM_MOUSEACTIVATE (0x0021) ─────────────────────


def test_main_window_mouseactivate_returns_ma_noactivate_when_no_focus():
    """When noFocusMode is True, WM_MOUSEACTIVATE must return (True, 3) [MA_NOACTIVATE]."""
    win = SimpleNamespace(
        cfg={"noFocusMode": True},
        width=lambda: 300,
        height=lambda: 600,
    )
    msg = ctypes.wintypes.MSG()
    msg.message = 0x0021  # WM_MOUSEACTIVATE
    msg.wParam = 0
    msg.lParam = 0

    handled, result = MainWindow.nativeEvent(win, b"windows_generic_MSG", ctypes.addressof(msg))
    assert handled is True
    assert result == 3  # MA_NOACTIVATE


def test_main_window_mouseactivate_ignored_when_no_focus_false():
    """When noFocusMode is False, WM_MOUSEACTIVATE falls through to default processing."""
    win = SimpleNamespace(
        cfg={"noFocusMode": False},
        width=lambda: 300,
        height=lambda: 600,
    )
    msg = ctypes.wintypes.MSG()
    msg.message = 0x0021
    msg.wParam = 0
    msg.lParam = 0

    handled, result = MainWindow.nativeEvent(win, b"windows_generic_MSG", ctypes.addressof(msg))
    assert handled is False
    assert result == 0


def test_main_window_other_messages_not_intercepted_by_mouseactivate():
    """Other messages (e.g. 0x0001 WM_CREATE) return False, 0."""
    win = SimpleNamespace(
        cfg={"noFocusMode": True},
        width=lambda: 300,
        height=lambda: 600,
    )
    msg = ctypes.wintypes.MSG()
    msg.message = 0x0001
    msg.wParam = 0
    msg.lParam = 0

    handled, result = MainWindow.nativeEvent(win, b"windows_generic_MSG", ctypes.addressof(msg))
    assert handled is False
    assert result == 0


# ── 2. FloatingPanelWindow nativeEvent: WM_MOUSEACTIVATE ────────────────────


def test_floating_panel_mouseactivate_returns_ma_noactivate(qapp):
    """FloatingPanelWindow with _no_focus=True returns (True, 3) on WM_MOUSEACTIVATE."""
    panel = FloatingPanelWindow("rgb_panel", "RGB", no_focus=True)
    try:
        msg = ctypes.wintypes.MSG()
        msg.message = 0x0021  # WM_MOUSEACTIVATE
        msg.wParam = 0
        msg.lParam = 0

        handled, result = panel.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert handled is True
        assert result == 3
    finally:
        panel.close()


def test_floating_panel_mouseactivate_falls_through_when_not_no_focus(qapp):
    """FloatingPanelWindow with _no_focus=False allows normal native event fallthrough."""
    panel = FloatingPanelWindow("rgb_panel", "RGB", no_focus=False)
    try:
        msg = ctypes.wintypes.MSG()
        msg.message = 0x0021
        msg.wParam = 0
        msg.lParam = 0

        handled, result = panel.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert handled is False
    finally:
        panel.close()


# ── 3. SyncMixin._push_color_to_sync: Drag Debouncing ───────────────────────


class _FakeSyncThread:
    def __init__(self, mode="ps"):
        self.software_mode = mode
        self.writes = []

    def isRunning(self):
        return True

    def write_color(self, r, g, b, **kwargs):
        self.writes.append((r, g, b, kwargs))


class _FakeWheel:
    def __init__(self, dragging=None):
        self.dragging = dragging
        self.h = 180.0
        self.s = 50.0
        self.v = 80.0
        self.wheel_mode = "triangle"


class _SyncContext:
    def __init__(self, wheel_dragging=None, lab_dragging=False, slider_down=False):
        self.sync_thread = _FakeSyncThread(mode="ps")
        self.color_wheel = _FakeWheel(dragging=wheel_dragging)
        self.lab_square = SimpleNamespace(dragging=lab_dragging)
        self.lab_slider = SimpleNamespace(dragging=False)
        self.slider_widgets = {
            "R": (SimpleNamespace(isSliderDown=lambda: slider_down), None),
        }
        self.active_slot = "fg"
        self._fg_transparent = False
        self._bg_transparent = False

    def _resolve_sync_source(self):
        return ("rgb", {"r": 255.0, "g": 0.0, "b": 0.0})


def test_wheel_dragging_suppresses_intermediate_sync():
    """While color_wheel.dragging is truthy, wheel-sourced color changes are not pushed."""
    ctx = _SyncContext(wheel_dragging="hue")
    SyncMixin._push_color_to_sync(ctx, 255, 0, 0, source="wheel", hsv=(180, 50, 80))
    assert len(ctx.sync_thread.writes) == 0


def test_wheel_idle_allows_sync():
    """When color_wheel.dragging is None, wheel-sourced color changes push normally."""
    ctx = _SyncContext(wheel_dragging=None)
    SyncMixin._push_color_to_sync(ctx, 255, 0, 0, source="wheel", hsv=(180, 50, 80))
    assert len(ctx.sync_thread.writes) == 1
    r, g, b, kwargs = ctx.sync_thread.writes[0]
    assert (r, g, b) == (255, 0, 0)
    assert kwargs["color_index"] == 0


def test_lab_square_dragging_suppresses_sync():
    """While lab_square.dragging is True, lab-sourced color changes are suppressed."""
    ctx = _SyncContext(lab_dragging=True)
    SyncMixin._push_color_to_sync(ctx, 200, 100, 50, source="lab", hsv=None)
    assert len(ctx.sync_thread.writes) == 0


def test_lab_idle_allows_sync():
    """When lab_square is not dragging, lab-sourced color changes push normally."""
    ctx = _SyncContext(lab_dragging=False)
    SyncMixin._push_color_to_sync(ctx, 200, 100, 50, source="lab", hsv=None)
    assert len(ctx.sync_thread.writes) == 1


def test_slider_dragging_suppresses_sync():
    """While a slider is down, sliders_rgb changes are suppressed."""
    ctx = _SyncContext(slider_down=True)
    SyncMixin._push_color_to_sync(ctx, 128, 64, 32, source="sliders_rgb", hsv=None)
    assert len(ctx.sync_thread.writes) == 0


def test_programmatic_source_not_suppressed_by_wheel():
    """Non-wheel sources like eyedropper picker are never blocked by wheel dragging."""
    ctx = _SyncContext(wheel_dragging="hue")
    SyncMixin._push_color_to_sync(ctx, 10, 20, 30, source="picker", hsv=None)
    assert len(ctx.sync_thread.writes) == 1
    assert ctx.sync_thread.writes[0][:3] == (10, 20, 30)


# ── 4. on_interaction_finished: Final Color Push ────────────────────────────


def test_interaction_finished_pushes_final_color():
    """on_interaction_finished writes the final color to sync_thread on release."""
    class _MockInteractionWin:
        def __init__(self):
            self.sync_thread = _FakeSyncThread(mode="ps")
            self.color_wheel = SimpleNamespace(
                schedule_slice_prewarm=mock.MagicMock(),
                update=mock.MagicMock(),
            )
            self.lab_square = SimpleNamespace(
                isVisible=lambda: False,
                update=mock.MagicMock(),
            )
            self._deferred_color_timer = SimpleNamespace(stop=mock.MagicMock())
            self._deferred_dynamic_gradients_pending = False
            self.current_rgb = (100, 150, 200)
            self.active_slot = "fg"
            self._fg_transparent = False
            self._bg_transparent = False
            self._source_space = "rgb"
            self._source_values = {"r": 100.0, "g": 150.0, "b": 200.0}
            self.slider_widgets = {}

        def _schedule_lab_prerender(self, delay):
            pass

        def update_slider_gradients(self, rf, gf, bf):
            pass

        def _update_all_L_gamut_ranges(self):
            pass

        def _record_color_history(self):
            pass

        def _resolve_sync_source(self):
            return ("rgb", {"r": 100.0, "g": 150.0, "b": 200.0})

        def _get_hsv_u32_for_sync(self, slot_idx):
            return (1000, 2000, 3000)

    win = _MockInteractionWin()
    ColorUpdatesMixin.on_interaction_finished(win)

    assert len(win.sync_thread.writes) == 1
    r, g, b, kwargs = win.sync_thread.writes[0]
    assert (r, g, b) == (100, 150, 200)
    assert kwargs["color_index"] == 0
    assert kwargs["transparent"] is False


# ── 5. ColorWheel Drag Cursor Blanking & 1500ms Protection Window ────────────


def test_color_wheel_drag_blanks_cursor_during_drag(qapp):
    """Dragging the color wheel must blank the cursor so it never obstructs the indicator."""
    from ui.color_wheel import ColorWheel
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF, Qt

    wheel = ColorWheel()
    wheel.setCursor(Qt.CursorShape.CrossCursor)

    cx, cy, _, _, _, _ = wheel.get_wheel_geometry()
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(cx, cy),
        QPointF(cx, cy),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    wheel.mousePressEvent(press)
    if wheel.dragging:
        assert wheel.cursor().shape() == Qt.CursorShape.BlankCursor
        wheel.end_drag()
        assert wheel.cursor().shape() == Qt.CursorShape.CrossCursor


def test_silence_shield_window_is_1500ms():
    """Default drawing shield window in PhotoshopSync and PhotoshopScriptBridge must be 1500ms."""
    from core.photoshop_color_sync import PhotoshopSync
    from core.photoshop_script_bridge import PhotoshopScriptBridge
    import inspect

    ps_sig = inspect.signature(PhotoshopSync.note_color_applied)
    assert ps_sig.parameters["window_ms"].default == 1500

    bridge_sig = inspect.signature(PhotoshopScriptBridge.note_color_applied)
    assert bridge_sig.parameters["window_ms"].default == 1500


def test_gradient_slider_blanks_cursor_on_drag(qapp):
    """GradientSlider must hide cursor (BlankCursor) while dragged and restore on release."""
    from ui.widgets.gradient_slider import GradientSlider
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF, Qt

    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.resize(100, 20)

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(50, 10),
        QPointF(50, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(press)
    assert slider.cursor().shape() == Qt.CursorShape.BlankCursor

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(50, 10),
        QPointF(50, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(release)
    assert slider.cursor().shape() != Qt.CursorShape.BlankCursor


def test_lab_slider_blanks_cursor_on_drag(qapp):
    """LabSlider must hide cursor (BlankCursor) while dragged and restore on release."""
    from ui.lab_visualizer import LabSlider
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF, Qt

    slider = LabSlider()
    slider.resize(20, 100)

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 50),
        QPointF(10, 50),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(press)
    assert slider.dragging is True
    assert slider.cursor().shape() == Qt.CursorShape.BlankCursor

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(10, 50),
        QPointF(10, 50),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(release)
    assert slider.dragging is False
    assert slider.cursor().shape() != Qt.CursorShape.BlankCursor
