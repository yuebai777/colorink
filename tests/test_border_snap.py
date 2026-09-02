"""边框宽度对齐设备像素：分数缩放下那条"只有一侧才有的浅色竖线"。

3px 的框在 1.5 倍屏上是 4.5 个物理像素 —— Qt 画不了半个，于是一侧多出
一条半覆盖的浅色线。用户看到的就是"标题栏左边有条缝"。
"""

import pytest

from ui.window_layout import snap_border_width


@pytest.mark.parametrize("width,dpr", [
    (4, 1.0), (3, 1.0), (4, 1.5), (2, 1.5), (3, 2.0), (4, 2.0), (1, 1.0),
])
def test_a_width_that_already_lands_on_whole_pixels_is_kept(width, dpr):
    assert snap_border_width(width, dpr) == width


def test_the_half_pixel_case_moves_to_the_nearest_clean_width():
    """1.5 倍屏上的 3px：4.5 个物理像素画不出来，挪到 4（=6 物理像素）。"""
    assert snap_border_width(3, 1.5) == 4


def test_it_prefers_growing_over_shrinking():
    assert snap_border_width(3, 1.5) == 4
    assert snap_border_width(5, 1.5) == 6


@pytest.mark.parametrize("dpr", [1.25, 1.75])
def test_odd_ratios_still_produce_a_whole_number_of_device_pixels(dpr):
    snapped = snap_border_width(3, dpr)
    product = snapped * dpr
    assert abs(product - round(product)) < 1e-6


def test_zero_and_nonsense_are_left_alone():
    assert snap_border_width(0, 1.5) == 0
    assert snap_border_width(4, 0) == 4
    assert snap_border_width(-2, 1.5) == 0


def test_it_gives_up_rather_than_wander_far():
    """找不到附近的干净宽度时保持原值，不要为了对齐把框改得面目全非。"""
    assert snap_border_width(3, 1.0001) == 3
