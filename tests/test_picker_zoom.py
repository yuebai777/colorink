"""Picker magnifier zoom: geometry derives from an injected zoom level.

Locks in the behavior that the overlay no longer re-reads the full settings
config on every pick — the owner (MainWindow) injects the zoom once and
updates it on settings save. The geometry math is extracted into a pure
helper so it can be tested without driving the full screen-capture overlay.
"""

import pytest

from PyQt6.QtWidgets import QApplication

from ui.color_picker_overlay import (
    ColorPickerOverlay,
    _GRID_PX,
    _PAD,
    _PREVIEW,
    _zoom_geometry,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_zoom_geometry_default_matches_legacy_panel(qapp):
    cap, radius, grid_disp, panel_w, panel_h = _zoom_geometry(6)

    # Default zoom 6: cap size 15 → radius 7 → grid 90px (the legacy constants).
    assert cap == 15
    assert radius == 7
    assert grid_disp == _GRID_PX
    assert panel_w == _GRID_PX + _PAD * 2
    assert panel_h == _PAD + _GRID_PX + _PAD + _PREVIEW + _PAD + 10 + 11 + _PAD


def test_zoom_geometry_cap_is_odd_and_min_3(qapp):
    for zoom in (1, 2, 6, 12):
        cap, radius, grid_disp, *_ = _zoom_geometry(zoom)
        assert cap % 2 == 1, f"cap {cap} must be odd (zoom={zoom})"
        assert cap >= 3, f"cap {cap} must be >= 3 (zoom={zoom})"
        assert radius == (cap - 1) // 2
        assert grid_disp == cap * zoom


def test_zoom_geometry_clamps_nonpositive_zoom(qapp):
    cap, radius, *_ = _zoom_geometry(0)
    assert cap >= 3
    assert radius >= 1


def test_set_zoom_injects_value_for_start(qapp):
    overlay = ColorPickerOverlay(None)
    try:
        overlay.set_zoom(9)
        assert overlay._zoom == 9
        # A bogus zoom is clamped so the overlay never divides by zero.
        overlay.set_zoom(-3)
        assert overlay._zoom == 1
    finally:
        overlay.close()
