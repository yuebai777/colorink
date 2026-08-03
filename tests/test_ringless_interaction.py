"""Tests for ringless interaction guards, user input, and paint-path coverage.

Covers hit-test rejection (blank area, outside slice, control bar),
hue-drag impossibility, slice-drag start, full-mode legacy preservation,
and paint-path verification via spies.
"""

from unittest.mock import patch

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage, QPainter, QPaintEvent

import ui.color_wheel as color_wheel

from .test_ringless_support import (
    canonical_layout,
    disabled_layout,
    make_wheel,
    mouse_press,
    qapp,
)

# ── Interaction guards ───────────────────────────────────────────────────

class TestRinglessInteractionGuards:
    """When ringless is enabled, only valid slice clicks start a drag."""

    def test_blank_area_click_does_not_start_drag(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hsv-square")
        w.mousePressEvent(mouse_press(QPointF(3, 3)))
        assert w.dragging is None

    def test_outside_slice_click_does_not_start_drag(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hsv-square")
        w.mousePressEvent(mouse_press(QPointF(5, 150)))
        assert w.dragging is None

    def test_hue_drag_is_impossible(self, qapp):
        """Click far outside the slice radius must NOT start any drag."""
        w = make_wheel(400, 400, canonical_layout())
        sg = w.get_slice_geometry()
        click_x = sg.center_x + sg.radius + 30
        w.mousePressEvent(mouse_press(QPointF(click_x, sg.center_y)))
        assert w.dragging is None

    def test_inside_slice_click_starts_drag(self, qapp):
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("hsv-square")
        sg = w.get_slice_geometry()
        w.mousePressEvent(mouse_press(QPointF(sg.center_x, sg.center_y)))
        assert w.dragging is not None
        assert w.dragging != "hue"

    def test_control_bar_click_does_not_start_drag(self, qapp):
        """Click inside the top control-bar area starts no slice drag."""
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hsv-square")
        sg = w.get_slice_geometry()
        bar_mid_y = canonical_layout().control_bar_height / 2.0  # 19.5
        w.mousePressEvent(mouse_press(QPointF(sg.center_x, bar_mid_y)))
        assert w.dragging is None

    def test_unknown_mode_rejects_clicks(self, qapp):
        """An unrecognised wheel mode must never accept clicks."""
        w = make_wheel(400, 400, canonical_layout())
        w.wheel_mode = "nonexistent-mode"
        sg = w.get_slice_geometry()
        w.mousePressEvent(mouse_press(QPointF(sg.center_x, sg.center_y)))
        assert w.dragging is None

    def test_oklch_visible_right_edge_is_interactive(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("oklch-slice")
        sg = w.get_slice_geometry()
        box_w = w._oklch_slice_box_width(sg.radius)

        # The ringless box uses the full available width (wider than the
        # radius), so the far right of the box is still draggable.
        assert w._is_point_in_active_slice(
            sg.center_x + 0.5 * box_w - 1.0,
            sg.center_y,
            sg.center_x,
            sg.center_y,
            sg.radius,
        )

    def test_oklch_transparent_right_area_rejects_clicks(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("oklch-slice")
        sg = w.get_slice_geometry()
        box_w = w._oklch_slice_box_width(sg.radius)

        assert not w._is_point_in_active_slice(
            sg.center_x + 0.5 * box_w + 1.0,
            sg.center_y,
            sg.center_x,
            sg.center_y,
            sg.radius,
        )


# ── Full-mode regression ─────────────────────────────────────────────────

class TestFullModeInteractionPreserved:
    """When ringless is disabled, existing hue drag still works."""

    def test_hue_ring_drag_works_in_full_mode(self, qapp):
        w = make_wheel(400, 400, disabled_layout())
        cx, cy, _, outer_r, inner_r, _ = w.get_wheel_geometry()
        r_mid = (outer_r + inner_r) / 2.0
        w.mousePressEvent(mouse_press(QPointF(cx + r_mid, cy)))
        assert w.dragging == "hue"

    def test_oklch_full_mode_keeps_legacy_rectangular_hit_area(self, qapp):
        w = make_wheel(400, 400, disabled_layout())
        w.set_wheel_mode("oklch-slice")
        sg = w.get_slice_geometry()

        assert w._is_point_in_active_slice(
            sg.center_x + sg.radius,
            sg.center_y,
            sg.center_x,
            sg.center_y,
            sg.radius,
        )


# ── Paint-path coverage ──────────────────────────────────────────────────

class TestRinglessPaintPath:
    """Ringless paint skips ring+hue-indicator and uses shared slice geometry.
    Full-mode paint still calls the hue indicator."""

    def test_ringless_paint_calls_slice_and_indicator_with_same_geometry(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hsv-square")
        sg = w.get_slice_geometry()

        with (
            patch.object(w, "draw_hue_indicator", wraps=w.draw_hue_indicator) as spy_hue,
            patch.object(w, "draw_hsv_square", wraps=w.draw_hsv_square) as spy_slice,
            patch.object(w, "draw_hsv_square_indicator", wraps=w.draw_hsv_square_indicator) as spy_ind,
        ):
            w.paintEvent(QPaintEvent(w.rect()))

            spy_hue.assert_not_called()
            spy_slice.assert_called_once()
            spy_ind.assert_called_once()

            # Slice draw args: (painter, cx, cy, r)
            _, scx, scy, sr = spy_slice.call_args[0]
            assert scx == pytest.approx(sg.center_x)
            assert scy == pytest.approx(sg.center_y)
            assert sr == pytest.approx(sg.radius)

            # Indicator must receive the exact same geometry
            _, icx, icy, ir = spy_ind.call_args[0]
            assert icx == pytest.approx(sg.center_x)
            assert icy == pytest.approx(sg.center_y)
            assert ir == pytest.approx(sg.radius)

    def test_full_mode_paint_calls_hue_indicator(self, qapp):
        w = make_wheel(400, 400)  # no layout → full mode
        with patch.object(w, "draw_hue_indicator", wraps=w.draw_hue_indicator) as spy_hue:
            w.paintEvent(QPaintEvent(w.rect()))
            spy_hue.assert_called_once()

    def test_rgb_slice_drag_uses_preview_resolution(self, qapp):
        """Internal slice drags must not block the indicator on full-res rebuilds."""
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("rgb-slice")
        w.dragging = "rgb-slice"
        image = QImage(400, 400, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        try:
            sg = w.get_slice_geometry()
            with patch.object(color_wheel, "lab_to_rgb", wraps=color_wheel.lab_to_rgb) as spy_lab_to_rgb:
                w.draw_rgb_slice(painter, sg.center_x, sg.center_y, sg.radius)
        finally:
            painter.end()

        # The full-quality image is rebuilt after release; while dragging,
        # the preview must sample substantially fewer pixels so the indicator
        # can paint immediately.
        full_width = int(sg.radius * 2.0)
        full_height = int(sg.radius * 2.0 * 0.866)
        assert spy_lab_to_rgb.call_count < (full_width * full_height) // 2

    def test_rgb_indicator_stays_put_after_release(self, qapp):
        """Releasing an RGB slice drag must not quantize the indicator position."""
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("rgb-slice")
        sg = w.get_slice_geometry()
        w.dragging = "rgb-slice"
        w.handle_rgb_slice_drag(
            sg.center_x + sg.radius * 0.2,
            sg.center_y - sg.radius * 0.2,
            sg.center_x, sg.center_y, sg.radius,
        )

        image = QImage(400, 400, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        try:
            with patch.object(w, "draw_indicator_ring", wraps=w.draw_indicator_ring) as spy:
                w.draw_rgb_indicator(painter, sg.center_x, sg.center_y, sg.radius)
                before = spy.call_args.args[1]
            w.end_drag()
            with patch.object(w, "draw_indicator_ring", wraps=w.draw_indicator_ring) as spy:
                w.draw_rgb_indicator(painter, sg.center_x, sg.center_y, sg.radius)
                after = spy.call_args.args[1]
        finally:
            painter.end()

        assert after.x() == pytest.approx(before.x(), abs=0.5)
        assert after.y() == pytest.approx(before.y(), abs=0.5)
