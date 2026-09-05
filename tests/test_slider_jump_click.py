"""Tests for slider click-to-jump and drag interaction across styles and themes."""

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOptionSlider

from ui.slider_themes import SLIDER_THEMES
from ui.widgets.gradient_slider import GradientSlider
from ui.settings.settings_helpers import NonScrollSlider

from .test_ringless_support import qapp  # noqa: F401


@pytest.fixture
def vista_style(qapp):
    """Fixture ensuring tests run under windowsvista style (Win10 default)."""
    orig_name = qapp.style().name()
    vista = QStyleFactory.create("windowsvista")
    if vista:
        qapp.setStyle(vista)
    yield
    if orig_name:
        restored = QStyleFactory.create(orig_name)
        if restored:
            qapp.setStyle(restored)


def test_gradient_slider_jumps_on_left_click_under_vista_style(vista_style):
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.update_scale(1.0, SLIDER_THEMES["default"])
    slider.setRange(0, 100)
    slider.setValue(0)
    slider.resize(200, slider.minimumHeight())

    events = []
    slider.sliderPressed.connect(lambda: events.append("pressed"))
    slider.sliderReleased.connect(lambda: events.append("released"))
    slider.valueChanged.connect(lambda v: events.append(f"val:{v}"))

    # Click groove at x=150 (roughly 75%)
    p = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(150, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(p)

    # In Win10 default (without our fix), this would only step by pageStep (+10, to 10).
    # With our fix, it must jump straight to roughly 76.
    assert slider.value() >= 70, f"Expected jump to ~75, got {slider.value()}"
    assert slider.isSliderDown() is True
    assert "pressed" in events

    # Drag to x=180
    m = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(180, 10),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseMoveEvent(m)
    assert slider.value() >= 85, f"Expected drag to ~90, got {slider.value()}"

    # Release mouse
    r = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(180, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(r)
    assert slider.isSliderDown() is False
    assert "released" in events


def test_direct_handle_click_does_not_jump(vista_style):
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.update_scale(1.0, SLIDER_THEMES["default"])
    slider.setRange(0, 100)
    slider.setValue(50)
    slider.resize(200, slider.minimumHeight())

    opt = QStyleOptionSlider()
    slider.initStyleOption(opt)
    hr = slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, opt,
        QStyle.SubControl.SC_SliderHandle, slider
    )

    events = []
    slider.sliderPressed.connect(lambda: events.append("pressed"))
    slider.valueChanged.connect(lambda v: events.append(f"val:{v}"))

    # Click directly on handle center
    p = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(hr.center().x(), hr.center().y()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(p)

    # Value should not have changed
    assert slider.value() == 50
    assert slider.isSliderDown() is True
    assert events == ["pressed"]


@pytest.mark.parametrize("theme_name", list(SLIDER_THEMES.keys()))
def test_all_themes_support_jump_and_drag(vista_style, theme_name):
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.update_scale(1.0, SLIDER_THEMES[theme_name])
    slider.setRange(0, 100)
    slider.setValue(0)
    slider.resize(200, slider.minimumHeight())

    # Click at mid point x=100
    p = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(100, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(p)
    assert 45 <= slider.value() <= 55, f"Theme {theme_name}: expected ~50, got {slider.value()}"
    assert slider.isSliderDown() is True

    # Drag to x=180
    m = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(180, 10),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseMoveEvent(m)
    assert slider.value() >= 80, f"Theme {theme_name}: expected drag to >=80, got {slider.value()}"

    # Release
    r = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(180, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mouseReleaseEvent(r)
    assert slider.isSliderDown() is False


def test_non_scroll_slider_jumps_on_left_click(vista_style):
    slider = NonScrollSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(0)
    slider.resize(200, 30)

    p = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(150, 15),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(p)
    assert slider.value() >= 70
    assert slider.isSliderDown() is True
