"""取色区几何的唯一真相：ui/window_layout.py（纯函数，不需要窗口）。

这条公式过去同时存在于四个地方：ColorWheel.get_wheel_geometry、
LabSquare._disc_metrics、缩放 pass、主题 pass —— 后两个已经跑偏（一个减
4px，一个减 6px），导致悬浮色块的大小取决于"最后跑的是缩放还是设置"。
这里既测纯函数本身，也测真控件与它逐位一致（防止将来再各写一份）。
"""

import pytest
from PyQt6.QtCore import Qt

from ui import window_layout as wl
from ui.color_wheel import ColorWheel
from ui.lab_visualizer import LabSquare

from .test_ringless_support import qapp  # noqa: F401

SIZES = [(200, 200), (304, 304), (420, 380), (380, 420), (256, 700),
         (700, 256), (120, 120), (1000, 240)]


# ── 纯函数 ───────────────────────────────────────────────────────────────

def test_known_geometry():
    picker = wl.resolve_picker_geometry(420, 420)
    assert picker.size == 404
    assert picker.circle.x == 210
    assert picker.circle.y == 208
    assert picker.circle.radius == 200


def test_short_pane_shrinks_instead_of_clipping():
    """矮而宽的窗口要缩小圆，而不是把下半圆切掉。"""
    picker = wl.resolve_picker_geometry(600, 200)
    assert picker.size == 194
    assert picker.circle.bottom <= 200


def test_circle_never_overflows_the_widget():
    for width, height in SIZES:
        picker = wl.resolve_picker_geometry(width, height)
        assert picker.circle.top >= 0
        assert picker.circle.bottom <= height + 1
        assert picker.circle.x - picker.circle.radius >= 0
        assert picker.circle.x + picker.circle.radius <= width


def test_size_is_monotonic_in_both_axes():
    """尺寸只随宽/高单调变化 —— 任何回退都会在拖动时被看成抽搐。"""
    widths = [wl.picker_size(w, 400) for w in range(120, 600)]
    assert all(b >= a for a, b in zip(widths, widths[1:]))
    heights = [wl.picker_size(400, h) for h in range(120, 600)]
    assert all(b >= a for a, b in zip(heights, heights[1:]))


def test_wheel_size_matches_picker_size_at_scale_one():
    """窗口口径的算法和控件口径的算法必须是同一条。"""
    for width in (260, 320, 400, 520):
        pane = 600
        assert wl.wheel_size_for(width, pane, 1.0) == wl.picker_size(width, pane)


def test_pane_height_subtracts_every_band():
    assert wl.picker_pane_height(800, 3, 3, 36, 314, 4) == 800 - 3 - 3 - 36 - 314 - 8


def test_square_height_respects_the_minimum():
    assert wl.picker_square_height(350, 3, 3, 100) == 344
    assert wl.picker_square_height(80, 3, 3, 120) == 120


# ── 真控件必须与纯函数逐位一致 ─────────────────────────────────────────

@pytest.mark.parametrize("size", SIZES)
def test_color_wheel_matches_the_pure_geometry(qapp, size):
    wheel = ColorWheel()
    wheel.resize(*size)
    picker = wl.resolve_picker_geometry(wheel.width(), wheel.height())
    assert wheel.get_wheel_geometry() == (
        picker.circle.x, picker.circle.y, picker.size,
        picker.circle.radius, picker.inner_radius, picker.triangle_radius,
    )


@pytest.mark.parametrize("size", SIZES)
def test_lab_disc_matches_the_hue_ring(qapp, size):
    """没有明度条时，LAB 圆盘必须和色环严格重合。"""
    square = LabSquare()
    square.resize(*size)
    square.set_shape("disc")
    cx, cy, radius = square._disc_metrics()
    picker = wl.resolve_picker_geometry(square.width(), square.height())
    assert cx == pytest.approx(picker.circle.x)
    assert cy == pytest.approx(picker.circle.y)
    assert radius == pytest.approx(max(1.0, picker.circle.radius))


def test_circle_helpers(qapp):
    circle = wl.Circle(10.0, 20.0, 5.0)
    assert (circle.top, circle.bottom, circle.diameter) == (15.0, 25.0, 10.0)

# ── 窗口级布局 ────────────────────────────────────────────────────────────

def _layout(width=350, height=705, title=36, sliders=314, spacing=4,
            margins=(3, 0, 3, 3), minimum=120):
    return wl.resolve_window_layout(
        window_width=width, window_height=height, margins=margins,
        title_height=title, sliders_height=sliders, spacing=spacing,
        picker_minimum=minimum)


def test_window_layout_known_bands():
    """与真窗口实测值对齐的一组基准数字。"""
    layout = _layout()
    assert layout.content.as_tuple() == (3, 0, 344, 702)
    assert layout.picker.as_tuple() == (3, 40, 344, 344)
    assert layout.sliders.as_tuple() == (3, 388, 344, 314)
    assert (layout.picker_circle.x, layout.picker_circle.y,
            layout.picker_circle.radius) == (175, 210, 162)


def test_picker_is_square_when_there_is_room():
    layout = _layout(width=420, height=1200)
    assert layout.picker.height == layout.picker.width


def test_picker_gives_up_height_before_overflowing():
    """窗口不够高时取色区先缩，不许把滑块区挤出去。"""
    layout = _layout(width=420, height=520)
    assert layout.picker.height < layout.picker.width
    assert layout.sliders.bottom <= layout.content.bottom + 1


def test_bands_are_stacked_without_gaps_or_overlap():
    layout = _layout()
    assert layout.title_band.bottom + layout.spacing == layout.picker.y
    assert layout.picker.bottom + layout.spacing == layout.sliders.y


def test_circle_is_the_picker_geometry_in_window_coordinates():
    """窗口坐标的圆 = 面板坐标的圆 + 面板原点，不许另算一份。"""
    layout = _layout()
    local = wl.resolve_picker_geometry(layout.picker.width, layout.picker.height)
    assert layout.picker_circle.x == layout.picker.x + local.circle.x
    assert layout.picker_circle.y == layout.picker.y + local.circle.y
    assert layout.picker_circle.radius == local.circle.radius


def test_circle_stays_inside_the_picker():
    for width in (240, 320, 460, 700):
        layout = _layout(width=width)
        circle = layout.picker_circle
        assert circle.x - circle.radius >= layout.picker.x - 1
        assert circle.x + circle.radius <= layout.picker.right + 1
        assert circle.top >= layout.picker.y - 1
        assert circle.bottom <= layout.picker.bottom + 1


def test_picker_tracks_the_window_width_monotonically():
    """拖宽窗口时取色区和圆只能单调变大 —— 任何回退都是肉眼可见的抽搐。"""
    sizes = [_layout(width=w).picker.width for w in range(240, 600)]
    radii = [_layout(width=w).picker_circle.radius for w in range(240, 600)]
    assert all(b >= a for a, b in zip(sizes, sizes[1:]))
    assert all(b >= a for a, b in zip(radii, radii[1:]))


def test_hidden_title_bar_moves_everything_up():
    visible = _layout(title=36)
    hidden = _layout(title=0, margins=(3, 3, 3, 3))
    assert hidden.picker.y < visible.picker.y


def test_rect_helpers():
    rect = wl.Rect(10.0, 20.0, 30.0, 40.0)
    assert (rect.right, rect.bottom) == (40.0, 60.0)
    assert rect.translated(5.0, -5.0).as_tuple() == (15.0, 15.0, 30.0, 40.0)

def test_picker_size_matches_the_local_geometry():
    """悬浮色块簇按它缩放，必须等于色环真正被画出来的尺寸。"""
    layout = _layout()
    local = wl.resolve_picker_geometry(layout.picker.width, layout.picker.height)
    assert layout.picker_size == local.size


def test_picker_bounds_is_the_picker_rect():
    layout = _layout()
    assert layout.picker_bounds == (
        layout.picker.x, layout.picker.y, layout.picker.right, layout.picker.bottom)


def test_picker_size_tracks_the_window_monotonically():
    sizes = [_layout(width=w).picker_size for w in range(240, 600)]
    assert all(b >= a for a, b in zip(sizes, sizes[1:]))
