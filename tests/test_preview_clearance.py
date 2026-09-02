"""前景/背景色模块（含透明色）必须让开色环 / LAB 圆盘。

回归点：这一组悬浮色块的位置只按窗口角落算，与色环大小无关，于是前景
圆刚好擦着色环的弧线 —— 左上角 +0.3px、左下角 -0.8px，两个位置都在
"擦边"。现在整组（两个色块 + 透明胶囊）都按色环/圆盘的圆心与半径来摆，
并在角落太浅时按比例缩一点，保证任何窗口尺寸下都留有间隙。
"""

import math

import pytest

from ui import preview_clearance as pc
from ui.color_preview_box import ColorPreviewBox

from .test_ringless_preview_support import qapp  # noqa: F401


def _box(mode="bottom-left", wheel_size=304, title_h=30, window_h=700, sliders_h=250):
    box = ColorPreviewBox()
    box.position_mode = mode
    box.resize_and_position(wheel_size, title_h, window_h, sliders_h, "fg")
    return box


# ── obstacle model ───────────────────────────────────────────────────────

def test_obstacles_cover_swatches_and_capsule(qapp):
    """两个色块 + 胶囊两端 —— 透明色也算在内，不能漏。"""
    box = _box()
    parts = pc.cluster_obstacles(box)
    assert len(parts) == 4
    fg_cx, fg_cy, fg_r, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()
    assert parts[0] == pytest.approx((box.x() + fg_cx, box.y() + fg_cy, fg_r))
    assert parts[1] == pytest.approx((box.x() + bg_cx, box.y() + bg_cy, bg_r))
    tile = box._trans_tile.geometry()
    for part in parts[2:]:
        assert part[2] == pytest.approx(tile.height() / 2.0)


def test_gap_is_negative_when_overlapping(qapp):
    box = _box()
    fg_cx, fg_cy, fg_r, *_ = box.legacy_circle_geometry()
    centre = (box.x() + fg_cx, box.y() + fg_cy, 10.0)   # 圆心就落在前景圆上
    assert pc.gap(box, [centre]) < 0
    assert pc.penetration(box, [centre]) > 0


def test_far_circle_leaves_everything_clear(qapp):
    box = _box()
    far = (box.x() + 10_000.0, box.y() + 10_000.0, 5.0)
    assert pc.penetration(box, [far]) == 0.0
    assert pc.gap(box, [far]) > 0


# ── nudging out of the circles ───────────────────────────────────────────

def _ring_touching(box, overlap=6.0):
    """一个正好压进整组 overlap 像素的圆（模拟色环弧线）。

    圆心放在与角落相反的一侧（左上角锚点 → 圆心在右下，左下角锚点 → 圆心
    在右上），和真实布局一致：整组要沿角落对角线往外挪才躲得开。
    """
    parts = pc.cluster_obstacles(box)
    px, py, pr = parts[0]
    dy = 300.0 if box.position_mode == "top-left" else -300.0
    cx, cy = px + 300.0, py + dy
    radius = math.hypot(300.0, 300.0) - pr + overlap
    return (cx, cy, radius)


@pytest.mark.parametrize("mode", ["top-left", "bottom-left"])
def test_avoid_moves_the_cluster_out(qapp, mode):
    """两个角落都要能挪出来，挪完不再重叠。"""
    box = _box(mode)
    ring = _ring_touching(box)
    assert pc.penetration(box, [ring]) > 0
    before = (box.x(), box.y())
    left = pc.avoid_circles(box, [ring])
    assert (box.x(), box.y()) != before
    assert left == 0.0
    assert pc.gap(box, [ring]) >= pc.cluster_clearance(box) - 0.5


def test_avoid_is_idempotent(qapp):
    box = _box()
    ring = _ring_touching(box)
    pc.avoid_circles(box, [ring])
    settled = (box.x(), box.y())
    pc.avoid_circles(box, [ring])
    assert (box.x(), box.y()) == settled


def test_avoid_respects_bounds(qapp):
    """挪动不能把模块推出取色区。"""
    box = _box()
    ring = _ring_touching(box, overlap=400.0)   # 深到挪不干净
    bounds = (box.x() - 4, box.y() - 4,
              box.x() + box.width() + 200, box.y() + box.height() + 200)
    left = pc.avoid_circles(box, [ring], bounds)
    assert box.x() >= bounds[0]
    assert box.y() >= bounds[1]
    assert left > 0, "挪不干净时要把剩余侵入量报回去，好让调用方缩小整组"


def test_second_circle_is_not_ignored(qapp):
    """色环和 LAB 圆盘互不包含，必须两个都躲。"""
    box = _box()
    far = (box.x() + 10_000.0, box.y() + 10_000.0, 5.0)
    ring = _ring_touching(box)
    assert pc.penetration(box, [far, ring]) == pytest.approx(
        pc.penetration(box, [ring]))


def test_ringless_mode_is_left_alone(qapp):
    """无环模式下色块在自己的控制条里，不参与这套避让。"""
    from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

    box = _box()
    layout = resolve_ringless_layout(
        RinglessConfig.from_values(True, "right", "top"), True, 1.0)
    box.set_ringless_layout(layout, 400, 30, 400)
    before = (box.x(), box.y())
    assert pc.avoid_circles(box, [_ring_touching(box)]) == 0.0
    assert (box.x(), box.y()) == before


def test_clearance_scales_with_the_cluster(qapp):
    """间距跟着整组大小走 —— 和色环绑定在一起。"""
    small = _box(wheel_size=200)
    big = _box(wheel_size=600)
    assert pc.cluster_clearance(big) > pc.cluster_clearance(small)
