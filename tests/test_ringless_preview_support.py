"""Shared test support for ringless preview test modules.

Provides a QApplication fixture and helper factories so each test
module can create a laid-out ColorPreviewBox without duplication.
"""

import os

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui.color_preview_box import ColorPreviewBox
from ui.ringless_mode import RinglessLayout, ControlsSide


# ── Module-level QApplication fixture ────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── Layout factories ─────────────────────────────────────────────────────

def canonical_layout() -> RinglessLayout:
    """RinglessLayout at 100% scale (swatches 43×24, gap 5, radius 4)."""
    return RinglessLayout(
        wheel_enabled=True, controls_enabled=True, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )


def disabled_layout() -> RinglessLayout:
    """RinglessLayout with controls_enabled=False (legacy circle mode)."""
    return RinglessLayout(
        wheel_enabled=False, controls_enabled=False, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )


def lab_state_layout() -> RinglessLayout:
    """LAB page with ringless chrome active and wheel rendering inactive."""
    return RinglessLayout(
        wheel_enabled=False,
        controls_enabled=True,
        controls_side="right",
        control_bar_height=39,
        margin=7,
        swatch_width=43,
        swatch_height=24,
        swatch_gap=5,
        corner_radius=4,
        button_gap=4,
    )


def left_side_layout() -> RinglessLayout:
    """RinglessLayout with controls_side='left'."""
    return RinglessLayout(
        wheel_enabled=True, controls_enabled=True, controls_side="left",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )


def right_side_layout() -> RinglessLayout:
    """RinglessLayout with controls_side='right'."""
    return RinglessLayout(
        wheel_enabled=True, controls_enabled=True, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )


# ── Widget factory ───────────────────────────────────────────────────────

def make_preview_box(
    layout: RinglessLayout | None = None,
    window_width: int = 300,
    title_bar_h: int = 30,
) -> ColorPreviewBox:
    """Create a ColorPreviewBox, give it initial dimensions, and optionally
    apply *layout*."""
    box = ColorPreviewBox()
    box.resize(96, 40)  # enough to fit two 43×24 swatches + gap + margins
    if layout is not None:
        box.set_ringless_layout(layout, window_width, title_bar_h)
    return box


# ── Mouse event helpers ──────────────────────────────────────────────────

def mouse_press_event(
    x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton
) -> QMouseEvent:
    """One-line MouseButtonPress at (x, y)."""
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(x, y),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def double_click_event(x: float, y: float) -> QMouseEvent:
    """One-line MouseButtonDblClick at (x, y)."""
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
