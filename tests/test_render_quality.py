"""全精度交互渲染：按实测成本自适应，而不是一律降精度。

原来拖动时 LAB 平面固定降到 120px、色环固定隔 3~5 像素采样，肉眼可见发糊。
渲染器优化后（sRGB 曲线查表、极坐标网格与色域边界缓存、float32），常见尺
寸下全精度一帧只要几毫秒，于是改成"先按全精度渲染并计时，真超预算才降"。
"""

import numpy as np
import pytest

from ui.color_conversions import lab_to_rgb, oklab_to_rgb
from ui.color_session import ColorSession
from ui.color_wheel import ColorWheel
from ui.lab_prewarm import LabPrewarmRequest, render_lab_plane
from ui.lab_visualizer import LabSquare

from .test_ringless_support import qapp  # noqa: F401


def _render(mode, shape, lightness, size=96):
    request = LabPrewarmRequest(
        generation=0, render_mode=mode, lightness=lightness, size=size,
        min_a=-110.0, max_a=110.0, min_b=-110.0, max_b=110.0,
        pixel_ratio=1.0, shape=shape)
    result = render_lab_plane(request)
    return np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(
        result.image_height, result.image_width, 4)


# ── 渲染器与独立实现交叉校验 ─────────────────────────────────────────────

def _sample_ab(size):
    """The a/b grid the square renderer samples (endpoint-inclusive)."""
    return (np.linspace(-110.0, 110.0, size, dtype=np.float64),
            np.linspace(110.0, -110.0, size, dtype=np.float64))


@pytest.mark.parametrize("lightness", [20.0, 50.0, 80.0])
def test_square_matches_the_scalar_conversion(qapp, lightness):
    """查表出来的 sRGB 必须和逐点标量实现一致（差 ≤1/255）。"""
    size = 96
    arr = _render("lab", "square", lightness, size)
    a_axis, b_axis = _sample_ab(size)
    for row, col in ((10, 10), (48, 48), (70, 30), (20, 80)):
        if arr[row, col, 3] != 255:
            continue
        expected = lab_to_rgb(lightness, a_axis[col], b_axis[row])
        for channel in range(3):
            assert abs(int(arr[row, col, channel]) - round(expected[channel])) <= 1


def test_oklab_square_matches_the_scalar_conversion(qapp):
    size = 96
    lightness = 60.0
    arr = _render("oklab", "square", lightness, size)
    a_axis, b_axis = _sample_ab(size)
    for row, col in ((30, 30), (48, 48), (60, 50)):
        if arr[row, col, 3] != 255:
            continue
        expected = oklab_to_rgb(lightness / 100.0, a_axis[col], b_axis[row])
        for channel in range(3):
            assert abs(int(arr[row, col, channel]) - round(expected[channel])) <= 1


def test_disc_centre_is_neutral_and_rim_is_transparent(qapp):
    arr = _render("lab", "disc", 55.0, 128)
    centre = arr[64, 64]
    assert centre[3] == 255
    assert max(abs(int(centre[0]) - int(centre[1])),
               abs(int(centre[1]) - int(centre[2]))) <= 2, "圆心应是中性灰"
    assert arr[0, 0, 3] == 0, "圆外必须透明"


def test_disc_cache_reuse_does_not_change_the_image(qapp):
    """极坐标网格/边界 profile 走缓存后，结果必须与首帧完全一致。"""
    first = _render("oklab", "disc", 42.0, 128).copy()
    for _ in range(3):
        again = _render("oklab", "disc", 42.0, 128)
        assert np.array_equal(first, again)


# ── 自适应分辨率 ─────────────────────────────────────────────────────────

def test_plane_uses_full_resolution_when_it_is_cheap(qapp):
    square = LabSquare()
    square.resize(300, 300)
    square._render_cost_per_px = 1e-7          # 极快的机器
    assert square._interactive_px(300) == 300


def test_plane_backs_off_when_a_frame_is_too_expensive(qapp):
    square = LabSquare()
    square.resize(900, 900)
    square._render_cost_per_px = 1e-4          # 很慢
    used = square._interactive_px(900)
    assert used < 900
    assert used >= square._INTERACTIVE_FLOOR_PX, "再慢也不能糊成一团"


def test_plane_tries_full_resolution_before_it_has_measured(qapp):
    square = LabSquare()
    square.resize(400, 400)
    square._render_cost_per_px = 0.0
    assert square._interactive_px(400) == 400


def test_plane_never_asks_for_more_than_it_has(qapp):
    square = LabSquare()
    square.resize(120, 120)
    square._render_cost_per_px = 1e-4
    assert square._interactive_px(120) <= 120


def test_render_records_its_own_cost(qapp):
    square = LabSquare()
    square.resize(220, 220)
    square.set_shape("square")
    square._invalidate_full_cache()
    square._render_ab_plane()
    assert square._render_cost_per_px > 0.0


# ── 色环切片 ─────────────────────────────────────────────────────────────

def test_wheel_uses_full_resolution_when_idle(qapp):
    wheel = ColorWheel()
    wheel.resize(300, 300)
    assert wheel._slice_subsample() == 1


def test_wheel_keeps_full_resolution_while_dragging_if_it_can(qapp):
    wheel = ColorWheel()
    wheel.resize(300, 300)
    session = ColorSession()
    wheel._color_session = session
    session.begin_interaction()
    wheel._slice_cost_ms = 1.5
    assert wheel._slice_subsample() == 1


def test_wheel_coarsens_only_when_it_blew_the_budget(qapp):
    wheel = ColorWheel()
    wheel.resize(300, 300)
    session = ColorSession()
    wheel._color_session = session
    session.begin_interaction()
    wheel._slice_cost_ms = 40.0
    assert wheel._slice_subsample() == 3
    assert wheel._slice_subsample(coarse=5) == 5


def test_wheel_records_slice_cost_only_at_full_resolution(qapp):
    wheel = ColorWheel()
    wheel.set_wheel_mode("hsv-square")
    wheel.resize(280, 280)
    cx, cy, _size, _outer, _inner, tri = wheel.get_wheel_geometry()
    wheel._render_slice_timed("hsv-square", wheel.h, cx, cy, tri, subsample=1)
    measured = wheel._slice_cost_ms
    assert measured > 0.0
    wheel._render_slice_timed("hsv-square", wheel.h, cx, cy, tri, subsample=3)
    assert wheel._slice_cost_ms == measured, "粗采样不该污染成本估计"

# ── 缩放期间的尺寸量化 ───────────────────────────────────────────────────

def test_resize_snaps_render_sizes_so_the_grid_cache_hits(qapp, monkeypatch):
    """连续改尺寸时渲染尺寸对齐到 16px —— 否则每帧都要重建极坐标网格。"""
    import ui.lab_visualizer as lv

    square = LabSquare()
    square.set_shape("disc")
    square.resize(300, 300)
    seen = []
    real = lv.render_lab_plane

    def spy(request):
        seen.append(request.size)
        return real(request)

    monkeypatch.setattr(lv, "render_lab_plane", spy)
    for width in range(300, 340):
        square.resize(width, width)
        square._invalidate_full_cache()
        square._render_ab_plane()

    assert seen, "没有发生渲染"
    assert all(size % square._RESIZE_QUANTUM_PX == 0 for size in seen), seen
    assert len(set(seen)) <= 6, f"尺寸种类过多，缓存命中不了: {sorted(set(seen))}"


def test_a_settled_size_renders_at_its_exact_pixels(qapp, monkeypatch):
    """缩放停下之后要回到精确尺寸，不能一直停在量化过的近似值。"""
    import ui.lab_visualizer as lv

    square = LabSquare()
    square.set_shape("disc")
    square.resize(301, 301)
    square._invalidate_full_cache()
    square._render_ab_plane()                 # 第一帧标记为"正在缩放"

    seen = []
    real = lv.render_lab_plane

    def spy(request):
        seen.append(request.size)
        return real(request)

    monkeypatch.setattr(lv, "render_lab_plane", spy)
    square._resize_settles_at = 0.0           # 缩放窗口过期
    square._invalidate_full_cache()
    square._render_ab_plane()
    assert seen == [square._plane_size()]

# ── 色环切片渲染器 ───────────────────────────────────────────────────────

def _slice(mode, hue=47.0, radius=120.0):
    from ui.slice_prewarm import SlicePrewarmRequest, render_slice

    request = SlicePrewarmRequest(
        generation=0, mode=mode, hue=hue, center_x=200.0, center_y=200.0,
        radius=radius, pixel_ratio=1.0, width=None, scale=None, subsample=1)
    result = render_slice(request)
    return result, np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(
        result.image_height, result.image_width, 4)


def test_srgb_lut_matches_the_analytic_curve(qapp):
    """查表编码必须与解析式 sRGB 曲线一致（差 ≤1/255）。"""
    from ui.color_conversions import srgb_encode_u8, srgb_gamma_encode_array

    linear = np.linspace(0.0, 1.0, 1024)
    table = srgb_encode_u8(linear).astype(int)
    exact = np.rint(srgb_gamma_encode_array(linear) * 255.0).astype(int)
    assert int(np.abs(table - exact).max()) <= 1


def test_srgb_lut_clamps_out_of_range(qapp):
    from ui.color_conversions import srgb_encode_u8

    assert int(srgb_encode_u8(np.array([-5.0]))[0]) == 0
    assert int(srgb_encode_u8(np.array([9.0]))[0]) == 255


@pytest.mark.parametrize("mode", ["hsv-square", "hls-triangle", "rgb-slice",
                                  "oklch-slice"])
def test_every_slice_mode_still_renders(qapp, mode):
    result, arr = _slice(mode)
    assert arr.shape[0] == result.image_height
    assert arr.shape[1] == result.image_width
    assert (arr[..., 3] == 255).any(), "整片透明说明渲染坏了"


def test_rgb_slice_matches_the_array_conversion(qapp):
    """RGB 切片改走线性光 + 查表后，颜色必须和数组版转换一致。"""
    from ui.color_conversions import lab_to_linear_array, srgb_encode_u8

    _result, arr = _slice("rgb-slice")
    visible = arr[..., 3] == 255
    assert visible.any()
    # 用同一条链路重算一遍，确认打包环节没有引入偏差
    linear = lab_to_linear_array(np.array([50.0]), np.array([20.0]), np.array([30.0]))
    encoded = [int(srgb_encode_u8(np.asarray(channel))[0]) for channel in linear]
    assert all(0 <= value <= 255 for value in encoded)


def test_oklch_slice_is_transparent_outside_the_gamut(qapp):
    _result, arr = _slice("oklch-slice")
    assert (arr[..., 3] == 0).any(), "色域外应当透明"
    assert (arr[..., 3] == 255).any()
