"""Slider indicator must stay whole at both ends of its travel.

Regression: the triangle indicator was painted at ``frac * width``, so at the
minimum its left half sat outside the widget and at the maximum its right
half did — the painter's clip rect sliced the marker in two.  It also ignored
the native handle's own inset, so the marker drifted away from the cursor
while dragging.
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QStyle, QStyleOptionSlider

from ui.slider_themes import SLIDER_THEMES
from ui.widgets.gradient_slider import GradientSlider

from .test_ringless_support import qapp  # noqa: F401

TRIANGLE_STYLES = [key for key, theme in SLIDER_THEMES.items()
                   if theme.get("handle_shape") == "triangle-below"]
ALL_STYLES = list(SLIDER_THEMES)
WIDTH = 121


def _slider(style, value, width=WIDTH):
    slider = GradientSlider(Qt.Orientation.Horizontal)
    # Flat red groove: the indicator is whatever is NOT red or background.
    slider.set_gradient([(0.0, QColor("#ff0000")), (1.0, QColor("#ff0000"))])
    slider.update_scale(1.0, SLIDER_THEMES[style])
    slider.setRange(0, 100)
    slider.setValue(value)
    slider.resize(width, slider.minimumHeight())
    image = QImage(slider.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("#eeeeee"))
    slider.render(image)
    return slider, image


def _indicator_columns(image):
    """Columns of indicator ink (everything below the groove)."""
    background = image.pixel(0, 0)

    def is_groove(x, y):
        color = QColor(image.pixel(x, y))
        return color.red() - color.green() > 25

    groove_rows = [y for y in range(image.height())
                   if any(is_groove(x, y) for x in range(image.width()))]
    start = (max(groove_rows) + 1) if groove_rows else 0
    columns = set()
    for y in range(start, image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) != background and not is_groove(x, y):
                columns.add(x)
    return columns


@pytest.mark.parametrize("style", TRIANGLE_STYLES)
def test_indicator_is_whole_at_both_ends(qapp, style):
    """三角把手在最左 / 最右都必须完整，不能被裁掉一半。"""
    spans = {}
    for value in (0, 50, 100):
        _, image = _slider(style, value)
        columns = _indicator_columns(image)
        assert columns, f"{style} @ {value}: 指示器没画出来"
        spans[value] = max(columns) - min(columns) + 1
        assert min(columns) >= 0
        assert max(columns) <= image.width() - 1

    assert spans[0] >= spans[50] - 1, (
        f"{style}: 值为 0 时指示器只剩 {spans[0]}px（中间是 {spans[50]}px）—— 左半边被裁了")
    assert spans[100] >= spans[50] - 1, (
        f"{style}: 值为 100 时指示器只剩 {spans[100]}px（中间是 {spans[50]}px）—— 右半边被裁了")


@pytest.mark.parametrize("style", TRIANGLE_STYLES)
def test_indicator_reaches_both_edges(qapp, style):
    """两端要顶到槽的边缘 —— 不能缩在里面留一大截空白。"""
    _, image_min = _slider(style, 0)
    _, image_max = _slider(style, 100)
    left = min(_indicator_columns(image_min))
    right = max(_indicator_columns(image_max))
    assert left <= 1, f"{style}: 最左端指示器离左边缘还有 {left}px"
    assert right >= WIDTH - 2, f"{style}: 最右端指示器离右边缘还有 {WIDTH - 1 - right}px"


@pytest.mark.parametrize("style", TRIANGLE_STYLES)
def test_native_handle_is_as_wide_as_the_indicator(qapp, style):
    """隐形的原生把手 = 指示器的实际宽度。

    它同时决定命中区域和 Qt 的取值行程；比指示器窄的话，拖动时三角会
    跟鼠标脱节，两端还会溢出控件被裁。
    """
    slider, _ = _slider(style, 50)
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    style_obj = slider.style()
    assert style_obj is not None
    handle = style_obj.subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )
    assert handle.width() >= 2 * slider._triangle_half_width() - 1


@pytest.mark.parametrize("style", TRIANGLE_STYLES)
@pytest.mark.parametrize("value", [0, 25, 50, 75, 100])
def test_indicator_follows_the_native_handle(qapp, style, value):
    """指示器中心必须落在原生把手中心上（拖动时不脱节）。"""
    slider, image = _slider(style, value)
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    style_obj = slider.style()
    assert style_obj is not None
    handle = style_obj.subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )
    expected = handle.x() + handle.width() / 2.0
    columns = _indicator_columns(image)
    painted = (min(columns) + max(columns) + 1) / 2.0
    assert painted == pytest.approx(expected, abs=2.0)


@pytest.mark.parametrize("style", ALL_STYLES)
@pytest.mark.parametrize("value", [0, 100])
def test_handle_rect_stays_inside_the_widget(qapp, style, value):
    """每套主题的把手矩形在两端都必须完整落在控件内。

    画笔按这个矩形定位（三角主题也一样），矩形一旦越界，超出的部分
    就会被 paintEvent 的裁剪框切掉。
    """
    slider, _ = _slider(style, value)
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    style_obj = slider.style()
    assert style_obj is not None
    handle = style_obj.subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )
    assert handle.x() >= 0, f"{style} @ {value}: 把手左边越界 {handle.x()}px"
    assert handle.x() + handle.width() <= slider.width(), (
        f"{style} @ {value}: 把手右边越界 "
        f"{handle.x() + handle.width() - slider.width()}px")

@pytest.mark.parametrize("style", TRIANGLE_STYLES)
@pytest.mark.parametrize("scale", [0.75, 1.0, 1.5, 2.0])
def test_indicator_stays_whole_at_every_ui_scale(qapp, style, scale):
    """缩放 75%~200% 下，两端的把手同样不能被裁。"""
    spans = {}
    for value in (0, 50, 100):
        slider = GradientSlider(Qt.Orientation.Horizontal)
        slider.set_gradient([(0.0, QColor("#ff0000")), (1.0, QColor("#ff0000"))])
        slider.update_scale(scale, SLIDER_THEMES[style])
        slider.setRange(0, 100)
        slider.setValue(value)
        slider.resize(int(160 * scale), slider.minimumHeight())
        image = QImage(slider.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor("#eeeeee"))
        slider.render(image)
        columns = _indicator_columns(image)
        assert columns, f"{style} @ {scale}x/{value}: 指示器没画出来"
        spans[value] = max(columns) - min(columns) + 1

    assert spans[0] >= spans[50] - 1, f"{style} @ {scale}x: 最左端被裁"
    assert spans[100] >= spans[50] - 1, f"{style} @ {scale}x: 最右端被裁"


@pytest.mark.parametrize("style", TRIANGLE_STYLES)
def test_narrow_widget_keeps_the_marker_centred(qapp, style):
    """控件比把手还窄时不崩、也不偏到一边（极端窗口宽度的兜底）。"""
    slider, image = _slider(style, 100, width=10)
    columns = _indicator_columns(image)
    assert columns
    painted = (min(columns) + max(columns) + 1) / 2.0
    assert painted == pytest.approx(slider.width() / 2.0, abs=2.0)
