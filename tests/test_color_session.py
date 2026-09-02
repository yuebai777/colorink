"""共享取色会话：控件不再伸手抓宿主窗口。

面板一旦浮出成独立窗口，window() 就不再是 MainWindow，原来那些
window().slider_widgets / self._parent.select_fg_slot() 的路径会全断。
这些测试把新的契约钉死，同时保证没有会话时的旧行为原样保留。
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from ui.color_session import (
    ColorSession,
    request_slot,
    request_transparent,
    session_of,
)
from ui.color_wheel import ColorWheel
from ui.lab_visualizer import LabSlider, LabSquare

from .test_ringless_support import qapp  # noqa: F401


# ── 交互计数 ─────────────────────────────────────────────────────────────

def test_interaction_starts_and_ends(qapp):
    session = ColorSession()
    seen = []
    session.interactionChanged.connect(seen.append)
    assert session.interacting is False
    session.begin_interaction()
    assert session.interacting is True
    session.end_interaction()
    assert session.interacting is False
    assert seen == [True, False]


def test_nested_drags_only_signal_at_the_edges(qapp):
    """两个控件同时被拖时，只在真正开始/结束时各发一次。"""
    session = ColorSession()
    seen = []
    session.interactionChanged.connect(seen.append)
    session.begin_interaction()
    session.begin_interaction()
    session.end_interaction()
    assert session.interacting is True
    session.end_interaction()
    assert session.interacting is False
    assert seen == [True, False]


def test_end_without_begin_is_harmless(qapp):
    """滚轮那条路径会补发 sliderReleased —— 不能把计数打成负数。"""
    session = ColorSession()
    session.end_interaction()
    session.end_interaction()
    assert session.interacting is False
    session.begin_interaction()
    assert session.interacting is True


def test_reset_drops_every_outstanding_drag(qapp):
    session = ColorSession()
    session.begin_interaction()
    session.begin_interaction()
    session.reset_interaction()
    assert session.interacting is False
    session.reset_interaction()          # 幂等
    assert session.interacting is False


# ── 查找 ─────────────────────────────────────────────────────────────────

def test_session_of_prefers_the_widgets_own(qapp):
    widget = QWidget()
    own = ColorSession()
    widget._color_session = own
    assert session_of(widget) is own


def test_session_of_falls_back_to_the_window(qapp):
    host = QWidget()
    host.color_session = ColorSession()
    child = QWidget(host)
    assert session_of(child) is host.color_session


def test_session_of_returns_none_when_unwired(qapp):
    assert session_of(QWidget()) is None


# ── 命令 ─────────────────────────────────────────────────────────────────

class _LegacyHost:
    def __init__(self):
        self.calls = []

    def select_fg_slot(self):
        self.calls.append("fg")

    def select_bg_slot(self):
        self.calls.append("bg")

    def set_active_transparent(self):
        self.calls.append("transparent")


class _Box(QWidget):
    def __init__(self, parent_host):
        super().__init__()
        self._parent = parent_host


def test_slot_command_goes_through_the_session(qapp):
    host = _LegacyHost()
    box = _Box(host)
    box._color_session = ColorSession()
    seen = []
    box._color_session.slotRequested.connect(seen.append)
    request_slot(box, "bg")
    assert seen == ["bg"]
    assert host.calls == []          # 不再直接调宿主


def test_slot_command_falls_back_to_the_host(qapp):
    """没有会话时保持旧行为（裸控件、老测试）。"""
    host = _LegacyHost()
    box = _Box(host)
    request_slot(box, "fg")
    request_slot(box, "bg")
    request_transparent(box)
    assert host.calls == ["fg", "bg", "transparent"]


def test_transparent_command_goes_through_the_session(qapp):
    host = _LegacyHost()
    box = _Box(host)
    box._color_session = ColorSession()
    seen = []
    box._color_session.transparentRequested.connect(lambda: seen.append("t"))
    request_transparent(box)
    assert seen == ["t"]
    assert host.calls == []


# ── 渲染精度：观察者不再反过来查被观察者 ─────────────────────────────────

def test_lab_plane_reads_quality_from_the_session(qapp):
    """LAB 平面靠会话判断"别人是不是在拖"，不再扫窗口里的滑块。"""
    square = LabSquare()
    square.resize(200, 200)
    session = ColorSession()
    square._color_session = session

    square._render_ab_plane()
    assert square._cached_key[2] is False          # 没人拖 → 全精度

    session.begin_interaction()
    square._invalidate_full_cache()
    square._render_ab_plane()
    assert square._cached_key[2] is True           # 有人拖 → 低精度


def test_wheel_reads_interaction_from_the_session(qapp):
    wheel = ColorWheel()
    wheel.resize(200, 200)
    session = ColorSession()
    wheel._color_session = session
    assert wheel.is_active_interaction() is False
    session.begin_interaction()
    assert wheel.is_active_interaction() is True
    session.end_interaction()
    assert wheel.is_active_interaction() is False


def test_lightness_bar_announces_its_drag(qapp):
    """明度条要自己报告开始/结束，而不是让别人来读它的 dragging。"""
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent

    bar = LabSlider()
    bar.resize(18, 200)
    events = []
    bar.interactionStarted.connect(lambda: events.append("start"))
    bar.interactionFinished.connect(lambda: events.append("end"))

    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(9, 100),
                        QPointF(9, 100), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bar.mousePressEvent(press)
    bar.mouseReleaseEvent(None)
    assert events == ["start", "end"]

    events.clear()
    bar.mouseReleaseEvent(None)      # 没按过就放开：不该冒出一个 end
    assert events == []
