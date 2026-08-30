"""Boundary-snap tests: dragging outside a colour diagram must land on the
closest in-gamut point instead of a box corner / square edge.

Covers the RGB slice and OKLCh slice (ui/color_wheel_interaction) and the
LAB / OKLab plane (ui/lab_visualizer), plus exact mapping for points that
are already inside the gamut.
"""

import math

import pytest
from PyQt6.QtCore import QPointF

from ui.color_conversions import find_max_lab_c, find_max_oklch_c
from ui.lab_visualizer import LabSquare

from .test_ringless_support import canonical_layout, make_wheel, qapp


def _slice_pixel(L, C, cx, cy, hy, min_x, scale):
    """Widget-pixel position of a slice point (L fraction, C value)."""
    return (min_x + C * scale, cy + hy * (1.0 - 2.0 * L))


def _rgb_ref_distance(px, py, cx, cy, hy, min_x, scale, a_dir, b_dir, n=400):
    """Brute-force nearest pixel distance over the RGB gamut boundary:
    the max-chroma curve plus the C=0 neutral axis."""
    best = float("inf")
    for i in range(n + 1):
        t = i / n
        Cb = find_max_lab_c(t * 100.0, a_dir, b_dir)
        bx = min_x + Cb * scale
        by = cy + hy * (1.0 - 2.0 * t)
        best = min(best, (bx - px) ** 2 + (by - py) ** 2)
        by = cy + hy * (1.0 - 2.0 * t)
        best = min(best, (min_x - px) ** 2 + (by - py) ** 2)
    return math.sqrt(best)


def _oklch_ref_distance(px, py, cx, cy, hy, min_x, scale, hue, n=400):
    """Brute-force nearest pixel distance over the OKLCh gamut boundary."""
    best = float("inf")
    for i in range(n + 1):
        t = i / n
        Cb = find_max_oklch_c(t, hue)
        bx = min_x + Cb * scale
        by = cy + hy * (1.0 - 2.0 * t)
        best = min(best, (bx - px) ** 2 + (by - py) ** 2)
        by = cy + hy * (1.0 - 2.0 * t)
        best = min(best, (min_x - px) ** 2 + (by - py) ** 2)
    return math.sqrt(best)


class TestRgbSliceBoundarySnap:
    """RGB module slice: drags outside the gamut snap to the nearest point."""

    def _make(self, qapp):
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("rgb-slice")
        return w

    def _metrics(self, w):
        sg = w.get_slice_geometry()
        cx, cy, r = sg.center_x, sg.center_y, sg.radius
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))
        return cx, cy, r, hy, min_x

    def test_inside_point_is_exact(self, qapp):
        w = self._make(qapp)
        cx, cy, r, hy, min_x = self._metrics(w)
        # Prime the per-drag scale cache, then probe an interior point.
        w.handle_rgb_slice_drag(cx, cy, cx, cy, r)
        scale = w._drag_scale
        a_dir, b_dir = w._drag_a_dir, w._drag_b_dir

        L_target = 0.5
        C_target = 0.4 * find_max_lab_c(50.0, a_dir, b_dir)
        px, py = _slice_pixel(L_target, C_target, cx, cy, hy, min_x, scale)
        w.handle_rgb_slice_drag(px, py, cx, cy, r)

        assert w._drag_L == pytest.approx(L_target * 100.0, abs=1e-6)
        assert w._drag_C == pytest.approx(C_target, abs=1e-6)

    def test_neutral_axis_point_is_exact(self, qapp):
        w = self._make(qapp)
        cx, cy, r, hy, min_x = self._metrics(w)
        w.handle_rgb_slice_drag(cx, cy, cx, cy, r)
        w.handle_rgb_slice_drag(min_x, cy, cx, cy, r)
        assert w._drag_C == pytest.approx(0.0, abs=1e-6)
        assert w._drag_L == pytest.approx(50.0, abs=1e-6)

    @pytest.mark.parametrize("kind", [
        "above", "below", "above-right", "below-right", "left", "right",
    ])
    def test_outside_point_snaps_to_nearest_boundary(self, qapp, kind):
        w = self._make(qapp)
        cx, cy, r, hy, min_x = self._metrics(w)
        w.handle_rgb_slice_drag(cx, cy, cx, cy, r)
        scale = w._drag_scale
        a_dir, b_dir = w._drag_a_dir, w._drag_b_dir
        max_c = max(find_max_lab_c(20, a_dir, b_dir),
                    find_max_lab_c(50, a_dir, b_dir),
                    find_max_lab_c(80, a_dir, b_dir))
        bulge_x = min_x + max_c * scale
        box_right = min_x + 2.0 * r

        if kind == "above":
            px, py = cx, cy - hy - 40.0
        elif kind == "below":
            px, py = cx, cy + hy + 40.0
        elif kind == "above-right":
            px, py = bulge_x, cy - hy - 40.0
        elif kind == "below-right":
            px, py = bulge_x, cy + hy + 40.0
        elif kind == "left":
            px, py = min_x - 60.0, cy
        else:  # right
            px, py = box_right + 40.0, cy

        w.handle_rgb_slice_drag(px, py, cx, cy, r)

        L, C = w._drag_L / 100.0, w._drag_C
        ix, iy = _slice_pixel(L, C, cx, cy, hy, min_x, scale)
        d = math.hypot(ix - px, iy - py)
        ref = _rgb_ref_distance(px, py, cx, cy, hy, min_x, scale, a_dir, b_dir)
        assert d <= ref + 1.5
        # The snapped point must be exactly on the gamut boundary.
        assert C == pytest.approx(
            find_max_lab_c(L * 100.0, a_dir, b_dir), abs=1e-6) or C == 0.0


class TestOklchSliceBoundarySnap:
    """LCH module slice: drags outside the gamut snap to the nearest point."""

    def _make(self, qapp):
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("oklch-slice")
        w.set_oklch(0.5, 0.1, 30.0, update_widget=False)
        return w

    def _metrics(self, w):
        sg = w.get_slice_geometry()
        cx, cy, r = sg.center_x, sg.center_y, sg.radius
        hy = r * 0.866
        box_w = w._oklch_slice_box_width(r)
        min_x = int(math.floor(cx - box_w * 0.5))
        return cx, cy, r, hy, min_x

    def test_inside_point_is_exact(self, qapp):
        w = self._make(qapp)
        cx, cy, r, hy, min_x = self._metrics(w)
        w.handle_oklch_slice_drag(cx, cy, cx, cy, r)
        scale = w._drag_scale
        hue = w._drag_oklch_h

        L_target = 0.5
        C_target = 0.4 * find_max_oklch_c(0.5, hue)
        px, py = _slice_pixel(L_target, C_target, cx, cy, hy, min_x, scale)
        w.handle_oklch_slice_drag(px, py, cx, cy, r)

        assert w._drag_L == pytest.approx(L_target, abs=1e-6)
        assert w._drag_C == pytest.approx(C_target, abs=1e-6)

    @pytest.mark.parametrize("kind", [
        "above", "below", "above-right", "left", "right",
    ])
    def test_outside_point_snaps_to_nearest_boundary(self, qapp, kind):
        w = self._make(qapp)
        cx, cy, r, hy, min_x = self._metrics(w)
        w.handle_oklch_slice_drag(cx, cy, cx, cy, r)
        scale = w._drag_scale
        hue = w._drag_oklch_h
        max_c = max(find_max_oklch_c(i / 200.0, hue) for i in range(201))
        bulge_x = min_x + max_c * scale
        box_right = min_x + w._oklch_slice_box_width(r)

        if kind == "above":
            px, py = cx, cy - hy - 40.0
        elif kind == "below":
            px, py = cx, cy + hy + 40.0
        elif kind == "above-right":
            px, py = bulge_x, cy - hy - 40.0
        elif kind == "left":
            px, py = min_x - 60.0, cy
        else:  # right
            px, py = box_right + 40.0, cy

        w.handle_oklch_slice_drag(px, py, cx, cy, r)

        L, C = w._drag_L, w._drag_C
        ix, iy = _slice_pixel(L, C, cx, cy, hy, min_x, scale)
        d = math.hypot(ix - px, iy - py)
        ref = _oklch_ref_distance(px, py, cx, cy, hy, min_x, scale, hue)
        assert d <= ref + 1.5
        assert C == pytest.approx(find_max_oklch_c(L, hue), abs=1e-6) or C == 0.0


class TestLabSquareNearestSnap:
    """LAB / OKLab plane: drags outside the gamut snap to the nearest point."""

    def _make(self, qapp, mode="lab"):
        sq = LabSquare()
        sq.resize(200, 200)
        if mode != "lab":
            sq.set_render_mode(mode)
        return sq

    def _screen(self, sq, a, b):
        min_a, max_a, min_b, max_b = sq._get_display_range()
        sx = (a - min_a) / (max_a - min_a) * 200.0
        sy = (max_b - b) / (max_b - min_b) * 200.0
        return sx, sy

    def _ref_distance(self, sq, pos, n=360):
        min_a, max_a, min_b, max_b = sq._get_display_range()

        def to_screen(aa, bb):
            sx = (aa - min_a) / (max_a - min_a) * 200.0
            sy = (max_b - bb) / (max_b - min_b) * 200.0
            return sx, sy

        def ray(theta):
            dx, dy = math.cos(theta), math.sin(theta)
            lo, hi = 0.0, sq.max_val * 1.5
            for _ in range(24):
                mid = (lo + hi) / 2.0
                if sq._is_in_gamut(mid * dx, mid * dy):
                    lo = mid
                else:
                    hi = mid
            return lo * dx, lo * dy

        best = float("inf")
        for i in range(n):
            ba, bb = ray(2.0 * math.pi * i / n)
            sx, sy = to_screen(ba, bb)
            best = min(best, (sx - pos.x()) ** 2 + (sy - pos.y()) ** 2)
        return math.sqrt(best)

    @pytest.mark.parametrize("mode", ["lab", "oklab"])
    def test_corner_drag_snaps_to_nearest_boundary(self, qapp, mode):
        sq = self._make(qapp, mode)
        pos = QPointF(5.0, 5.0)  # inside the square, outside the gamut
        sq.handle_mouse(pos)
        assert sq._is_in_gamut(sq.a, sq.b)
        sx, sy = self._screen(sq, sq.a, sq.b)
        d = math.hypot(sx - pos.x(), sy - pos.y())
        assert d <= self._ref_distance(sq, pos) + 1.5

    @pytest.mark.parametrize("mode", ["lab", "oklab"])
    def test_outside_square_drag_snaps_to_nearest_boundary(self, qapp, mode):
        sq = self._make(qapp, mode)
        pos = QPointF(-40.0, 100.0)  # outside the diagram entirely
        sq.handle_mouse(pos)
        assert sq._is_in_gamut(sq.a, sq.b)
        sx, sy = self._screen(sq, sq.a, sq.b)
        d = math.hypot(sx - pos.x(), sy - pos.y())
        assert d <= self._ref_distance(sq, pos) + 1.5

    def test_inside_click_is_exact(self, qapp):
        sq = self._make(qapp)
        sq.handle_mouse(QPointF(100.0, 100.0))
        sx, sy = self._screen(sq, sq.a, sq.b)
        assert math.hypot(sx - 100.0, sy - 100.0) < 1e-6
        assert sq._is_in_gamut(sq.a, sq.b)
