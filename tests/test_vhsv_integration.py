"""Comprehensive integration and unit tests for VHSV (Value-compensated HSV) mode."""

import os
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui import color_conversions as cc
from ui.color_model import Color, ColorState
from ui.color_wheel import ColorWheel
from ui.window.module_defs import _MODULE_ORDER, _MODULE_DEFS, _MODULE_NAMES


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_module_definitions_include_vhsv():
    assert "vhsv" in _MODULE_ORDER
    assert _MODULE_ORDER == ["hsv", "vhsv", "hls", "rgb", "lch"]
    spec = _MODULE_DEFS["vhsv"]
    assert spec["wheel"] == "vhsv-square"
    assert "VHSV" in spec["sliders"]
    assert _MODULE_NAMES["vhsv"] == "VHSV"


def test_color_wheel_vhsv_mode(qapp):
    wheel = ColorWheel()
    wheel.resize(300, 300)
    wheel.set_wheel_mode("vhsv-square")
    assert wheel.wheel_mode == "vhsv-square"

    wheel.set_vhsv(180.0, 60.0, 70.0)
    assert wheel.h == pytest.approx(180.0)
    assert wheel._vhsv_s == pytest.approx(60.0)
    assert wheel._vhsv_v == pytest.approx(70.0)

    space, vals = wheel.native_color_values()
    assert space == "vhsv"
    assert vals[0] == pytest.approx(180.0)
    assert vals[1] == pytest.approx(60.0)
    assert vals[2] == pytest.approx(70.0)

    # get_color matches vhsv_to_rgb
    expected_rgb = cc.vhsv_to_rgb(180.0, 60.0, 70.0)
    assert wheel.get_color() == (round(expected_rgb[0]), round(expected_rgb[1]), round(expected_rgb[2]))


def test_color_wheel_vhsv_drag(qapp):
    wheel = ColorWheel()
    wheel.resize(300, 300)
    wheel.set_wheel_mode("vhsv-square")

    geom = wheel.get_slice_geometry("vhsv-square")
    cx, cy = geom.center_x, geom.center_y
    half = int(geom.radius / 1.414) - 2

    # Drag to top-right corner of the square (S_vhsv=100, V_vhsv=100)
    pt = QPointF(cx + half, cy - half)
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        pt,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    wheel.mousePressEvent(event)
    assert wheel.dragging == "vhsv-square"
    assert wheel._vhsv_s == pytest.approx(100.0, abs=1.0)
    assert wheel._vhsv_v == pytest.approx(100.0, abs=1.0)


def test_vhsv_sync_source():
    from ui.window.sync_mixin import SyncMixin

    class DummyWindow(SyncMixin):
        def __init__(self):
            self._source_space = "vhsv"
            self._source_values = {"h": 200.0, "s": 50.0, "v": 80.0}

    win = DummyWindow()
    space, vals = win._resolve_sync_source()
    assert space == "rgb"
    expected_r, expected_g, expected_b = cc.vhsv_to_rgb(200.0, 50.0, 80.0)
    assert vals["r"] == pytest.approx(expected_r, abs=1e-3)
    assert vals["g"] == pytest.approx(expected_g, abs=1e-3)
    assert vals["b"] == pytest.approx(expected_b, abs=1e-3)


def test_vhsv_slice_dynamic_hue_variation(qapp):
    """Verify that within a single vhsv-square slice, traditional HSV hue changes dynamically across the slice."""
    wheel = ColorWheel()
    wheel.resize(300, 300)
    wheel.set_wheel_mode("vhsv-square")
    wheel.h = 103.0

    # Point 1: top-right S_vhsv=100, V_vhsv=100 -> traditional HSV hue is 103.0
    wheel.set_vhsv(103.0, 100.0, 100.0)
    r1, g1, b1 = wheel.get_color()
    h_std1, s_std1, v_std1 = cc.rgb_to_hsv(r1, g1, b1)
    assert h_std1 == pytest.approx(103.0, abs=0.5)

    # Point 2: S_vhsv=100, V_vhsv=80 -> traditional HSV hue shifts to ~108.4
    wheel.set_vhsv(103.0, 100.0, 80.0)
    r2, g2, b2 = wheel.get_color()
    h_std2, s_std2, v_std2 = cc.rgb_to_hsv(r2, g2, b2)
    assert h_std2 == pytest.approx(108.4, abs=0.5)

    # Point 3: S_vhsv=100, V_vhsv=50 -> traditional HSV hue shifts to 120.0
    wheel.set_vhsv(103.0, 100.0, 50.0)
    r3, g3, b3 = wheel.get_color()
    h_std3, s_std3, v_std3 = cc.rgb_to_hsv(r3, g3, b3)
    assert h_std3 == pytest.approx(120.0, abs=0.5)

    # Verify that wheel still retains the slice base VHSV hue (103.0)
    space, vals = wheel.native_color_values()
    assert space == "vhsv"
    assert vals[0] == pytest.approx(103.0)

