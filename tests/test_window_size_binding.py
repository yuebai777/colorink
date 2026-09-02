"""窗口尺寸联动：宽度变了要重算高度，色块模块要黏在色轮上。

两个回归点：
* 缩窄窗口后，最小高度还停在旧宽度算出来的值 —— 窗口拖不短，色轮下方
  留一条高空白带（取色区是方的，高度本该跟着宽度走）。
* 前景/背景模块锚在取色区底边上，窗口一拉高就跟着窗口底往下跑，和色轮
  脱开 —— 它的大小已经绑定色轮，位置也应该绑定。
"""

import pytest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.color_preview_box import ColorPreviewBox
from ui.window.layout import LayoutMixin

from .test_ringless_preview_support import qapp  # noqa: F401


# ── 高度跟着宽度走 ────────────────────────────────────────────────────

def test_required_height_shrinks_with_the_window_width():
    """取色区是方的：窗口越窄，需要的内容高度越小。"""
    def required(width):
        visualizer = LayoutMixin._required_visualizer_height(width, 3, 3, 100)
        return LayoutMixin._required_content_height(36, visualizer, 314, 0, 3, 4)

    assert required(500) > required(400) > required(300)


class _TimerHost(LayoutMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def _run_deferred_content_height(self):
        self.calls += 1


def test_width_change_arms_a_debounced_settle(qapp):
    """宽度变化后要安排一次（去抖的）落定：内容高度 + 全精度重绘。"""
    host = _TimerHost()
    host._schedule_width_driven_height()
    timer = host._settle_timer
    assert timer.isSingleShot()
    assert timer.isActive()
    assert timer.interval() > 0
    assert host._settle_needs_height is True


def test_scheduling_twice_reuses_one_timer(qapp):
    """连续拖动只保留一个待触发的落定，不堆积。"""
    host = _TimerHost()
    host._schedule_width_driven_height()
    first = host._settle_timer
    host._schedule_width_driven_height()
    assert host._settle_timer is first


def test_settle_without_width_change_skips_the_height_pass(qapp):
    """只拖高度时不许回弹 —— 高度策略只在宽度变了之后才跑。"""
    host = _TimerHost()
    host._schedule_settle()
    host._run_settle()
    assert host.calls == 0


def test_settle_runs_the_height_pass_once(qapp):
    host = _TimerHost()
    host._schedule_settle(width_changed=True)
    host._run_settle()
    assert host.calls == 1
    host._run_settle()          # 已经用掉了，不该再跑
    assert host.calls == 1


def test_settle_defers_while_the_frame_is_being_dragged(qapp):
    """拖动过程中落定要顺延，不能把窗口从光标下抽走。"""
    host = _TimerHost()
    host.resizing = True
    host._schedule_settle(width_changed=True)
    host._run_settle()
    assert host.calls == 0
    assert host._settle_timer.isActive()
    host.resizing = False
    host._run_settle()
    assert host.calls == 1


# ── 模块锚定在色轮圆上 ─────────────────────────────────────────────────

def _preview(mode):
    box = ColorPreviewBox()
    box.position_mode = mode
    box.resize_and_position(304, 30, 700, 250, "fg")
    return box


def test_bottom_anchor_rides_the_circle_bottom(qapp):
    """左下角：盒底贴着色轮圆的下沿，而不是取色区底边。"""
    box = _preview("bottom-left")
    circle = (200.0, 220.0, 150.0)          # 圆底 = 370
    LayoutMixin._anchor_preview_to_circle(box, circle, (0, 0, 400, 900))
    assert box.y() + box.height() == pytest.approx(370, abs=1)


def test_top_anchor_rides_the_circle_top(qapp):
    """左上角：盒顶贴着色轮圆的上沿。"""
    box = _preview("top-left")
    circle = (200.0, 220.0, 150.0)          # 圆顶 = 70
    LayoutMixin._anchor_preview_to_circle(box, circle, (0, 0, 400, 900))
    assert box.y() == pytest.approx(70, abs=1)


def test_anchor_ignores_a_taller_window(qapp):
    """窗口再高，只要色轮没动，模块就不动 —— 这正是"绑定色轮"的意思。"""
    box = _preview("bottom-left")
    circle = (200.0, 220.0, 150.0)
    LayoutMixin._anchor_preview_to_circle(box, circle, (0, 0, 400, 900))
    settled = box.y()
    # 取色区长高 400px，圆不变
    LayoutMixin._anchor_preview_to_circle(box, circle, (0, 0, 400, 1300))
    assert box.y() == settled


def test_anchor_clamps_into_bounds(qapp):
    """圆心贴边时也不能把模块推出取色区。"""
    box = _preview("top-left")
    circle = (200.0, 0.0, 150.0)            # 圆顶 = -150
    LayoutMixin._anchor_preview_to_circle(box, circle, (0, 40, 400, 900))
    assert box.y() >= 40
    box2 = _preview("bottom-left")
    LayoutMixin._anchor_preview_to_circle(box2, (200.0, 2000.0, 150.0), (0, 40, 400, 500))
    assert box2.y() + box2.height() <= 500

# ── 缩放时的几何单调性 ────────────────────────────────────────────────────

def test_picker_stays_square_while_dragging():
    """取色区高度 = 宽度（内容高度策略是去抖的，中途不能让它长成细高条）。"""
    assert LayoutMixin._picker_square_height(350, 3, 3, 100) == 344
    assert LayoutMixin._picker_square_height(500, 3, 3, 100) == 494
    # 但不能低于自身最小高度
    assert LayoutMixin._picker_square_height(80, 3, 3, 120) == 120


def test_picker_square_height_is_monotonic():
    """宽度单调增，取色区高度也必须单调增 —— 不许来回跳。"""
    heights = [LayoutMixin._picker_square_height(w, 3, 3, 100)
               for w in range(200, 600)]
    assert all(b >= a for a, b in zip(heights, heights[1:]))


class _TrimHost(LayoutMixin, QWidget):
    pass


def test_trim_factor_is_one_when_nothing_is_in_the_way(qapp):
    host = _TrimHost()
    box = _preview("bottom-left")
    far = [(box.x() + 10_000.0, box.y() + 10_000.0, 50.0)]
    assert host._preview_trim_factor(box, far, (0, 0, 400, 900)) == 1.0


def test_trim_factor_shrinks_monotonically_with_the_circle(qapp):
    """圆越大，需要的缩放越小，而且必须连续 —— 阶梯式取值会让色块抽搐。"""
    host = _TrimHost()
    box = _preview("bottom-left")
    bounds = (0, 0, 400, 900)
    factors = []
    for radius in range(120, 220, 2):
        circles = [(box.x() + 260.0, box.y() - 40.0, float(radius))]
        factors.append(host._preview_trim_factor(box, circles, bounds))
    assert all(b <= a + 1e-9 for a, b in zip(factors, factors[1:])), factors
    assert min(factors) >= host._PREVIEW_FIT_MIN
    assert max(factors) <= 1.0
    # 连续：相邻半径之间不能出现大台阶
    steps = [abs(b - a) for a, b in zip(factors, factors[1:])]
    assert max(steps) < 0.05, steps

# ── 高度策略：增长不收缩 ─────────────────────────────────────────────────

class _HeightMixin(LayoutMixin, QWidget):
    """把 adjust 需要的属性和策略接起来测。"""

    def __init__(self, hint=346, visible=True):
        super().__init__()
        self.cfg = {"uiScale": 100}
        self._manual_height_override = False
        self._last_auto_height = None
        self._adjusting_content_height = False
        self._content_height_adjust_pending = False
        self.title_bar = QWidget()
        self.main_layout = QVBoxLayout(self)
        self.sliders_container = QWidget()
        self.sliders_layout = QVBoxLayout(self.sliders_container)
        self.stack = MagicMock(minimumSizeHint=MagicMock(return_value=120),
                               minimumHeight=MagicMock(return_value=120))
        self.panel_host = None
        self.resize(456, 600)

    def _visible_title_bar_height(self, tb):
        return 36

    def sizeHint(self):
        s = super().sizeHint()
        return s


def test_adjust_never_shrinks(qapp):
    """内容变小不许把窗口拽矮 —— 切换滑块组时窗口尺寸保持稳定。"""
    host = _HeightMixin()
    host._manual_height_override = True
    host._adjust_content_height()
    height_after_max = host.height()
    assert host.height() >= host.minimumHeight() - 1
    # 旧逻辑在内容高度下降后会缩小窗口，新逻辑只收紧最小高度
    host._manual_height_override = False
    host._adjust_content_height()
    assert host.height() == height_after_max


def test_adjust_grows_when_the_content_needs_it(qapp):
    """窗口比内容矮时仍要长 —— 这个方向不能禁。"""
    host = _HeightMixin()
    host._manual_height_override = False
    first = host.height()
    host._adjust_content_height()
    assert host.height() >= first
    assert host.height() >= host.minimumHeight()