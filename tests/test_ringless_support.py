"""Shared test support for ringless-geometry test modules.

Provides a QApplication fixture and helper factories so every test
module can create a laid-out ColorWheel without duplication.
"""

import os
from dataclasses import FrozenInstanceError

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui.color_wheel import ColorWheel, SliceGeometry
from ui.ringless_mode import RinglessLayout

# ── Module-level QApplication fixture ────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── Shared helpers ───────────────────────────────────────────────────────

def mouse_press(pos: QPointF) -> QMouseEvent:
    """One-line MouseButtonPress event at *pos*."""
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def make_wheel(
    width: int,
    height: int,
    layout: RinglessLayout | None = None,
) -> ColorWheel:
    """Create a ColorWheel, resize, set hsv-square mode, and optionally apply *layout*."""
    w = ColorWheel()
    w.resize(width, height)
    w.set_wheel_mode("hsv-square")
    w.set_color(255, 0, 0, block_signals=True)  # h=0, s=100, v=100
    if layout is not None:
        w.set_ringless_layout(layout)
    return w


def canonical_layout() -> RinglessLayout:
    """RinglessLayout with the canonical 39/7 metrics."""
    return RinglessLayout(
        wheel_enabled=True, controls_enabled=True, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )


def disabled_layout() -> RinglessLayout:
    """RinglessLayout with wheel_enabled=False."""
    return RinglessLayout(
        wheel_enabled=False, controls_enabled=False, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )
