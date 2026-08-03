"""Regression coverage for background full-resolution slice warmups."""

import os
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

import ui.color_wheel as color_wheel
from ui.color_wheel import ColorWheel
from ui.ringless_mode import RinglessLayout
from ui.slice_prewarm import SlicePrewarmRequest, render_slice


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _layout() -> RinglessLayout:
    return RinglessLayout(
        True, True, "right", 30, 7, 43, 24, 5, 4, 4,
    )


def test_all_module_slice_modes_render_full_resolution():
    for mode in ("hsv-square", "hls-triangle", "rgb-slice", "oklch-slice"):
        result = render_slice(SlicePrewarmRequest(1, mode, 35.0, 200.0, 200.0, 190.0, 1.0))
        assert result.image_width > 1
        assert result.image_height > 1
        assert len(result.image_bytes) == result.image_width * result.image_height * 4


def test_triangle_slices_scale_with_pixel_ratio():
    """HiDPI regression: prewarmed triangle slices must render at
    width * pixel_ratio so that _on_slice_prewarm_finished's
    setDevicePixelRatio() maps them back to the exact logical size.
    Otherwise the slice suddenly shrinks when the prewarm result replaces
    the live full-size fallback."""
    for mode in ("hls-triangle", "rgb-slice", "oklch-slice"):
        request = SlicePrewarmRequest(1, mode, 35.0, 200.0, 200.0, 190.0, 2.0)
        result = render_slice(request)
        assert result.image_width == max(1, int(round(result.width * 2.0)))
        assert result.image_height == max(1, int(round(result.height * 2.0)))
        assert len(result.image_bytes) == result.image_width * result.image_height * 4


def test_rgb_edge_x_stays_on_logical_grid_at_high_dpr():
    """The gamut edge curve is drawn in widget logical coordinates, so it
    must keep one entry per LOGICAL row with logical x values even when the
    pixel buffer is rendered at pixel_ratio resolution."""
    result = render_slice(SlicePrewarmRequest(1, "rgb-slice", 35.0, 200.0, 200.0, 190.0, 2.0))
    assert result.edge_x is not None
    assert len(result.edge_x) == result.height
    assert all(result.min_x <= v <= result.min_x + result.width for v in result.edge_x)


def test_prewarmed_rgb_cache_is_used_while_dragging(qapp):
    wheel = ColorWheel()
    wheel.resize(400, 400)
    wheel.show()
    qapp.processEvents()
    wheel.set_ringless_layout(_layout())
    wheel.set_wheel_mode("rgb-slice")
    geometry = wheel.get_slice_geometry()
    request = SlicePrewarmRequest(
        wheel._prewarm_generation, "rgb-slice", wheel.h, geometry.center_x, geometry.center_y,
        geometry.radius, 1.0,
    )
    wheel._on_slice_prewarm_finished(render_slice(request))
    # A page switch restores the same hue and may sync a new S/V point; the
    # resident slice image must remain valid.
    wheel.set_color(200, 100, 100, block_signals=True)
    assert "rgb-slice" in wheel._prewarmed_slices
    wheel.dragging = "rgb-slice"

    image = QImage(400, 400, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        with patch.object(color_wheel, "lab_to_rgb", wraps=color_wheel.lab_to_rgb) as spy:
            wheel.draw_rgb_slice(
                painter, geometry.center_x, geometry.center_y, geometry.radius,
            )
    finally:
        painter.end()

    spy.assert_not_called()
    assert "rgb-slice" in wheel._prewarmed_slices


def test_resize_invalidates_ready_slice_cache(qapp):
    wheel = ColorWheel()
    wheel.resize(400, 400)
    wheel.show()
    qapp.processEvents()
    wheel.set_ringless_layout(_layout())
    geometry = wheel.get_slice_geometry("rgb-slice")
    request = SlicePrewarmRequest(
        wheel._prewarm_generation, "rgb-slice", wheel.h, geometry.center_x, geometry.center_y,
        geometry.radius, 1.0,
    )
    wheel._on_slice_prewarm_finished(render_slice(request))
    assert wheel._prewarmed_slices

    wheel.resize(520, 520)
    qapp.processEvents()
    assert not wheel._prewarmed_slices
