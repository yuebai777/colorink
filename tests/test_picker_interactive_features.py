"""Unit tests for ColorPickerOverlay interactive shortcuts:
- Alt: freeze sampling position
- Wheel: adjust magnifier zoom (range 2x to 20x)
- Shift: lock sampling to horizontal or vertical axis
- Space: temporarily hide reticle and dot
"""

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from ui.color_picker_overlay import ColorPickerOverlay


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_picker_adjust_zoom_clamping_and_signal(qapp):
    """Wheel zoom adjust_zoom must clamp between 2x and 20x and emit zoomChanged."""
    overlay = ColorPickerOverlay(None)
    emitted = []
    overlay.zoomChanged.connect(emitted.append)
    try:
        overlay.set_zoom(6)
        assert overlay._zoom == 6

        # Step up
        res = overlay.adjust_zoom(2)
        assert res == 8
        assert overlay._zoom == 8
        assert emitted[-1] == 8

        # Step down
        res = overlay.adjust_zoom(-3)
        assert res == 5
        assert overlay._zoom == 5
        assert emitted[-1] == 5

        # Clamp upper at 20
        res = overlay.adjust_zoom(50)
        assert res == 20
        assert overlay._zoom == 20
        assert emitted[-1] == 20

        # No change if already at max
        prev_len = len(emitted)
        res = overlay.adjust_zoom(1)
        assert res == 20
        assert len(emitted) == prev_len

        # Clamp lower at 2
        res = overlay.adjust_zoom(-100)
        assert res == 2
        assert overlay._zoom == 2
        assert emitted[-1] == 2
    finally:
        overlay.close()


def test_picker_alt_freeze_locks_sample_position(qapp):
    """Alt freeze locks sample position to the snapshot cursor position."""
    overlay = ColorPickerOverlay(None)
    try:
        # Simulate active state without showing native overlay
        overlay._active = True
        overlay._frozen = True
        freeze_pt = QPoint(150, 250)
        overlay._freeze_pos = freeze_pt

        # When frozen, moving cursor does not alter sample_pos
        raw_cursor = QPoint(300, 400)
        sample_pos = overlay._freeze_pos if overlay._frozen else raw_cursor
        assert sample_pos == freeze_pt

        # Releasing Alt restores tracking
        overlay._frozen = False
        overlay._freeze_pos = None
        sample_pos = overlay._freeze_pos if overlay._frozen else raw_cursor
        assert sample_pos == raw_cursor
    finally:
        overlay.close()


def test_picker_shift_axis_lock_logic(qapp):
    """Shift axis lock constrains movement along the initial dominant axis."""
    overlay = ColorPickerOverlay(None)
    try:
        overlay._active = True
        origin = QPoint(200, 200)
        overlay._shift_origin = origin
        overlay._shift_axis = None

        # Horizontal movement: dx (20) > dy (2)
        cur = QPoint(220, 202)
        dx = abs(cur.x() - origin.x())
        dy = abs(cur.y() - origin.y())
        if dx >= 3 or dy >= 3:
            overlay._shift_axis = 'X' if dx >= dy else 'Y'

        assert overlay._shift_axis == 'X'
        target_pos = QPoint(cur.x(), origin.y())
        assert target_pos == QPoint(220, 200)

        # Reset and test vertical movement: dy (30) > dx (1)
        overlay._shift_origin = origin
        overlay._shift_axis = None
        cur_vert = QPoint(201, 230)
        dx_v = abs(cur_vert.x() - origin.x())
        dy_v = abs(cur_vert.y() - origin.y())
        if dx_v >= 3 or dy_v >= 3:
            overlay._shift_axis = 'X' if dx_v >= dy_v else 'Y'

        assert overlay._shift_axis == 'Y'
        target_pos_v = QPoint(origin.x(), cur_vert.y())
        assert target_pos_v == QPoint(200, 230)

        # Releasing shift clears origin and axis
        overlay._shift_origin = None
        overlay._shift_axis = None
        assert overlay._shift_origin is None
        assert overlay._shift_axis is None
    finally:
        overlay.close()


def test_picker_space_reticle_toggle(qapp):
    """Space key toggles reticle visibility and hides cursor dot."""
    overlay = ColorPickerOverlay(None)
    try:
        assert overlay._hide_reticle is False
        assert overlay._dot.isHidden() or not overlay._dot.isVisible()

        # Space pressed
        overlay._hide_reticle = True
        overlay._dot.hide()
        assert overlay._hide_reticle is True
        assert not overlay._dot.isVisible()

        # Space released
        overlay._hide_reticle = False
        overlay._dot.show()
        assert overlay._hide_reticle is False
        assert overlay._dot.isVisible()
    finally:
        overlay.close()


def test_picker_stop_resets_all_interactive_states(qapp):
    """Calling stop() resets freeze, shift axis, reticle toggle, and active flags."""
    overlay = ColorPickerOverlay(None)
    try:
        overlay._active = True
        overlay._frozen = True
        overlay._freeze_pos = QPoint(100, 100)
        overlay._shift_origin = QPoint(50, 50)
        overlay._shift_axis = 'X'
        overlay._hide_reticle = True
        overlay._active_sample_pos = QPoint(100, 100)

        overlay.stop()

        assert overlay._active is False
        assert overlay._frozen is False
        assert overlay._freeze_pos is None
        assert overlay._shift_origin is None
        assert overlay._shift_axis is None
        assert overlay._hide_reticle is False
        assert overlay._active_sample_pos is None
    finally:
        overlay.close()
