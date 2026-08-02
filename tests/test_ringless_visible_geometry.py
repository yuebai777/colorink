"""Rendered bounds and cache-origin tests for asymmetric ringless slices."""

import math

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter

from .test_ringless_support import canonical_layout, make_wheel, qapp


def _horizontal_slack(
    wheel, left_bound: float, right_bound: float
) -> tuple[float, float]:
    margin = float(canonical_layout().margin)
    return left_bound - margin, float(wheel.width()) - margin - right_bound


def _render(wheel) -> None:
    image = QImage(wheel.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    wheel.render(image)


class TestHlsTrueBoundsCentering:
    def test_ringless_hls_bounds_are_centered(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("hls-triangle")
        geometry = wheel.get_slice_geometry()

        left, right = _horizontal_slack(
            wheel,
            geometry.center_x - 0.5 * geometry.radius,
            geometry.center_x + geometry.radius,
        )

        assert left >= -1.0
        assert right >= -1.0
        assert abs(left - right) <= 1.0


class TestOklchVisibleBoundsCentering:
    def test_ringless_oklch_visible_bounds_are_centered(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("oklch-slice")
        geometry = wheel.get_slice_geometry()

        left, right = _horizontal_slack(
            wheel,
            geometry.center_x - 0.5 * geometry.radius,
            geometry.center_x + 0.5 * geometry.radius,
        )

        assert left >= -1.0
        assert right >= -1.0
        assert abs(left - right) <= 1.0

    def test_rendered_oklch_gamut_is_horizontally_centered(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("oklch-slice")
        geometry = wheel.get_slice_geometry()
        image = QImage(wheel.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        wheel.draw_oklch_slice(
            painter,
            geometry.center_x,
            geometry.center_y,
            geometry.radius,
        )
        painter.end()

        colored_x = [
            x
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        assert colored_x
        visible_center = (min(colored_x) + max(colored_x)) / 2.0
        assert visible_center == pytest.approx(wheel.width() / 2.0, abs=2.0)


class TestRgbAllocationCentering:
    def test_ringless_rgb_allocation_bounds_are_centered(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("rgb-slice")
        geometry = wheel.get_slice_geometry()

        left, right = _horizontal_slack(
            wheel,
            geometry.center_x - 0.5 * geometry.radius,
            geometry.center_x + 1.5 * geometry.radius,
        )

        assert left >= -1.0
        assert right >= -1.0
        assert abs(left - right) <= 1.0


class TestLegacyCenteringCompatibility:
    @pytest.mark.parametrize("mode", ["hls-triangle", "oklch-slice"])
    def test_full_mode_keeps_legacy_center(self, qapp, mode):
        wheel = make_wheel(400, 400)
        wheel.set_wheel_mode(mode)
        assert wheel.get_slice_geometry().center_x == pytest.approx(200.0)


class TestCachedSliceOriginAfterResize:
    def test_hls_origin_tracks_width_only_resize(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("hls-triangle")
        _render(wheel)
        old_min_x = wheel._cached_hls_minx

        wheel.resize(400, 339)
        qapp.processEvents()
        geometry = wheel.get_slice_geometry()
        _render(wheel)

        expected = math.floor(geometry.center_x - 0.5 * geometry.radius)
        assert wheel._cached_hls_minx == expected
        assert wheel._cached_hls_minx != old_min_x

    def test_oklch_origin_tracks_equal_radius_height_resize(self, qapp):
        wheel = make_wheel(300, 550, canonical_layout())
        wheel.set_wheel_mode("oklch-slice")
        _render(wheel)
        old_radius = wheel.get_slice_geometry().radius
        old_min_y = wheel._cached_oklch_miny

        wheel.resize(300, 650)
        qapp.processEvents()
        geometry = wheel.get_slice_geometry()
        _render(wheel)

        expected = math.floor(geometry.center_y - 0.866 * geometry.radius)
        assert geometry.radius == pytest.approx(old_radius)
        assert wheel._cached_oklch_miny == expected
        assert wheel._cached_oklch_miny != old_min_y

    def test_rgb_origin_tracks_height_only_resize(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("rgb-slice")
        _render(wheel)
        old_min_y = wheel._cached_rgb_miny

        wheel.resize(300, 439)
        qapp.processEvents()
        geometry = wheel.get_slice_geometry()
        _render(wheel)

        expected = math.floor(geometry.center_y - 0.866 * geometry.radius)
        assert wheel._cached_rgb_miny == expected
        assert wheel._cached_rgb_miny != old_min_y
