"""Tests for SliceGeometry dataclass and mode-specific ringless geometry.

Covers the dataclass contracts, per-mode radius/center computation
(HSV-square, HLS-triangle, OKLCh-slice, RGB-slice), full-mode compat,
per-mode radius independence, true-bounds centering, and stale-cache
origin after equal-radius resizes.
"""

import pytest

from ui.color_wheel import SliceGeometry
from ui.ringless_mode import RinglessLayout

from .test_ringless_support import (
    canonical_layout,
    disabled_layout,
    make_wheel,
    mouse_press,
    qapp,
)

# ── Module import / interface tests ──────────────────────────────────────

def test_slice_geometry_class_is_importable():
    assert SliceGeometry is not None


def test_colorwheel_has_set_ringless_layout(qapp):
    w = make_wheel(300, 300)
    assert hasattr(w, "set_ringless_layout")
    assert callable(getattr(w, "set_ringless_layout"))


def test_colorwheel_has_get_slice_geometry(qapp):
    w = make_wheel(300, 300)
    assert hasattr(w, "get_slice_geometry")
    assert callable(getattr(w, "get_slice_geometry"))


# ── SliceGeometry dataclass tests ────────────────────────────────────────

class TestSliceGeometryDataclass:
    """Given a SliceGeometry, fields are immutable and match constructor args."""

    def test_fields_are_correct(self):
        sg = SliceGeometry(center_x=50.0, center_y=60.0, radius=70.0)
        assert sg.center_x == 50.0
        assert sg.center_y == 60.0
        assert sg.radius == 70.0

    def test_is_frozen(self):
        from dataclasses import FrozenInstanceError
        sg = SliceGeometry(center_x=1.0, center_y=2.0, radius=3.0)
        with pytest.raises(FrozenInstanceError):
            setattr(sg, "center_x", 99.0)

    def test_is_hashable(self):
        sg = SliceGeometry(center_x=1.0, center_y=2.0, radius=3.0)
        _ = hash(sg)


# ── HSV-square geometry ──────────────────────────────────────────────────

class TestGetSliceGeometryHsvSquare:
    """300×339 / bar39 / margin7 → available 286×286.  Square fits exactly."""

    def test_radius_fits_square_in_available_area(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hsv-square")
        sg = w.get_slice_geometry()

        half = int(sg.radius / 1.414) - 2
        square_side = half * 2
        assert square_side <= 286
        assert square_side >= 282  # within int-truncation tolerance

    def test_center_is_available_rectangle_center(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        sg = w.get_slice_geometry()

        assert sg.center_x == pytest.approx(150.0, abs=1.0)
        # center_y = bar + margin + available_h / 2 = 39 + 7 + 286 / 2 = 189
        assert sg.center_y == pytest.approx(189.0, abs=1.0)


# ── HLS-triangle geometry ────────────────────────────────────────────────

class TestGetSliceGeometryHlsTriangle:
    """300×339 / bar39 / margin7 → available 286×286.
    1.5r ≤ 286, 1.732r ≤ 286 → r ≤ min(190.67, 165.13) = 165.13"""

    def test_triangle_fits_available_area(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hls-triangle")
        sg = w.get_slice_geometry()

        assert 1.5 * sg.radius <= 286.0 + 1.0
        assert 1.732 * sg.radius <= 286.0 + 1.0

    def test_radius_is_maximal(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("hls-triangle")
        sg = w.get_slice_geometry()

        expected = min(286.0 / 1.5, 286.0 / 1.732)  # 165.13
        assert sg.radius >= expected - 1.0


# ── OKLCh-slice geometry ─────────────────────────────────────────────────

class TestGetSliceGeometryOklchSlice:
    """300×339 / bar39 / margin7 → available 286×286.
    Visible width = r, height = 1.732r, so height limits r to 165.13."""

    def test_slice_fits_available_area(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("oklch-slice")
        sg = w.get_slice_geometry()

        assert sg.radius <= 286.0 + 1.0
        assert 1.732 * sg.radius <= 286.0 + 1.0

    def test_radius_is_maximal(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("oklch-slice")
        sg = w.get_slice_geometry()

        expected = min(286.0, 286.0 / 1.732)  # 165.13
        assert sg.radius >= expected - 1.0


# ── RGB-slice geometry (N2 fix: was falling through to square) ───────────

class TestGetSliceGeometryRgbSlice:
    """300×339 / bar39 / margin7 → available 286×286.
    2r ≤ 286, 1.732r ≤ 286 → r ≤ 143"""

    def test_slice_fits_available_area(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("rgb-slice")
        sg = w.get_slice_geometry()

        assert 2.0 * sg.radius <= 286.0 + 1.0
        assert 1.732 * sg.radius <= 286.0 + 1.0

    def test_radius_is_maximal(self, qapp):
        w = make_wheel(300, 339, canonical_layout())
        w.set_wheel_mode("rgb-slice")
        sg = w.get_slice_geometry()

        expected = min(286.0 / 2.0, 286.0 / 1.732)  # 143
        assert sg.radius >= expected - 1.0


# ── Full-mode compat ─────────────────────────────────────────────────────

class TestGetSliceGeometryFullMode:
    """When ringless is disabled, get_slice_geometry returns legacy values."""

    def test_full_mode_matches_legacy_geometry(self, qapp):
        w = make_wheel(400, 400)  # no layout → full mode
        sg = w.get_slice_geometry()
        cx, cy, _, _, _, tr = w.get_wheel_geometry()
        assert sg.center_x == pytest.approx(cx, abs=0.5)
        assert sg.center_y == pytest.approx(cy, abs=0.5)
        assert sg.radius == pytest.approx(tr, abs=0.5)

    def test_disabled_layout_matches_legacy_geometry(self, qapp):
        w = make_wheel(400, 300, disabled_layout())
        sg = w.get_slice_geometry()
        cx, cy, _, _, _, tr = w.get_wheel_geometry()
        assert sg.center_x == pytest.approx(cx, abs=0.5)
        assert sg.center_y == pytest.approx(cy, abs=0.5)
        assert sg.radius == pytest.approx(tr, abs=0.5)


# ── Mode-specific radius independence ────────────────────────────────────

class TestRadiusIndependentPerMode:
    """Each mode's radius is computed independently — one mode does not
    constrain another.  With a 400×400 widget, bar=39, margin=7:
    available_w=386, available_h=354.

    hsv-square: square fits in 354 → r ≈ 253
    hls-triangle: r = min(386/1.5, 354/1.732) = min(257.3, 204.4) ≈ 204
    oklch-slice: r = min(386, 354/1.732) = 204.4
    """

    def test_hsv_radius_exceeds_oklch_limit(self, qapp):
        w = make_wheel(400, 400, canonical_layout())
        w.set_wheel_mode("hsv-square")
        rh = w.get_slice_geometry().radius
        w.set_wheel_mode("oklch-slice")
        ro = w.get_slice_geometry().radius
        assert rh > ro  # square-mode radius is larger

    def test_each_mode_matches_its_own_constraint(self, qapp):
        """Every known mode's radius matches its formula-derived maximum."""
        w = make_wheel(400, 400, canonical_layout())
        available_w = 400 - 2 * 7  # 386
        available_h = 400 - 39 - 2 * 7  # 354

        w.set_wheel_mode("hsv-square")
        rh = w.get_slice_geometry().radius
        half_hsv = int(rh / 1.414) - 2
        assert 2 * half_hsv >= min(available_w, available_h) - 4

        w.set_wheel_mode("hls-triangle")
        rt = w.get_slice_geometry().radius
        assert rt >= min(available_w / 1.5, available_h / 1.732) - 1.0

        w.set_wheel_mode("oklch-slice")
        ro = w.get_slice_geometry().radius
        assert ro >= min(available_w, available_h / 1.732) - 1.0

        w.set_wheel_mode("rgb-slice")
        rr = w.get_slice_geometry().radius
        assert rr >= min(available_w / 2.0, available_h / 1.732) - 1.0
