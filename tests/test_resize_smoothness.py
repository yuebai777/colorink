"""缩放流畅性回归：拖窗口时不许重建样式、不许乱跳。

实测基线：一次 update_geometries 曾经 178ms —— 每个 resize 事件重写 53 张
样式表，级联出上万次 polish 事件（应用层 eventFilter 被调用 47 万次）。
样式与几何无关的东西全部改成"没变就不写"之后降到 0.7ms。
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel

from ui.color_history import ColorHistoryWidget
from ui.color_preview_box import ColorPreviewBox
from ui.slider_themes import SLIDER_THEMES
from ui.widgets.gradient_slider import GradientSlider
from ui.window.theme import _set_css

from .test_ringless_preview_support import qapp  # noqa: F401


class _CountingLabel(QLabel):
    """记录 setStyleSheet 实际被调用了几次。"""

    def __init__(self):
        super().__init__()
        self.writes = 0

    def setStyleSheet(self, css):
        self.writes += 1
        super().setStyleSheet(css)


def test_identical_stylesheet_is_not_reassigned(qapp):
    """同一张样式表写第二次要被挡掉 —— Qt 每次赋值都会重新 polish 整棵子树。"""
    label = _CountingLabel()
    for _ in range(10):
        _set_css(label, "color: #fff;")
    assert label.writes == 1


def test_changed_stylesheet_still_gets_through(qapp):
    label = _CountingLabel()
    _set_css(label, "color: #fff;")
    _set_css(label, "color: #000;")
    assert label.writes == 2
    assert label.styleSheet() == "color: #000;"


class _CountingSlider(GradientSlider):
    def __init__(self):
        self.writes = 0
        super().__init__(Qt.Orientation.Horizontal)

    def setStyleSheet(self, css):
        self.writes = getattr(self, "writes", 0) + 1
        super().setStyleSheet(css)


def test_slider_rescale_is_a_no_op_when_nothing_changed(qapp):
    """滑块的几何只跟缩放/主题有关，和窗口尺寸无关：拖窗口时不该重算。"""
    slider = _CountingSlider()
    slider.update_scale(1.0, SLIDER_THEMES["default"])
    baseline = slider.writes
    for _ in range(20):
        slider.update_scale(1.0, SLIDER_THEMES["default"])
    assert slider.writes == baseline


def test_slider_rescale_applies_a_real_change(qapp):
    slider = _CountingSlider()
    slider.update_scale(1.0, SLIDER_THEMES["default"])
    before = slider.writes
    slider.update_scale(1.5, SLIDER_THEMES["default"])
    assert slider.writes > before
    assert slider.scale == 1.5


def test_history_configure_keeps_the_selection_when_shape_is_unchanged(qapp):
    """回归：主题 pass 每次缩放都调 configure，把用户选中的历史色清掉了。"""
    grid = ColorHistoryWidget()
    grid.configure(8, 2)
    grid.set_colors([QColor("#ff0000"), QColor("#00ff00")])
    grid._selected_index = 1
    grid.configure(8, 2)
    assert grid._selected_index == 1


def test_history_configure_still_rebuilds_on_a_real_shape_change(qapp):
    grid = ColorHistoryWidget()
    grid.configure(8, 2)
    grid._selected_index = 1
    grid.configure(10, 3)
    assert (grid._cols, grid._rows) == (10, 3)
    assert grid._selected_index == -1


def test_fit_scope_defers_the_input_mask(qapp):
    """试摆位置时不重建鼠标遮罩，退出时统一应用一次。"""
    from ui import preview_clearance as pc

    box = ColorPreviewBox()
    box.resize_and_position(304, 30, 700, 250, "fg")
    with pc.fit_scope(box) as scoped:
        assert scoped._mask_suspended is True
        for _ in range(5):
            box.resize_and_position(304, 30, 700, 250, "fg")
    assert box._mask_suspended is False


def test_fit_scope_restores_even_on_error(qapp):
    from ui import preview_clearance as pc

    box = ColorPreviewBox()
    box.resize_and_position(304, 30, 700, 250, "fg")
    with pytest.raises(RuntimeError):
        with pc.fit_scope(box):
            raise RuntimeError("boom")
    assert box._mask_suspended is False
