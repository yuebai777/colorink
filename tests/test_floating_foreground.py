"""仅在软件前台显示：浮窗也要跟着主窗一起藏/现。"""

import sys

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from core import config as core_config
from ui.panels import registry
from ui.panels import tree as dock
from ui.panels.host import PanelHost
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.panels_mixin import PanelProviderMixin

from .test_ringless_preview_support import qapp  # noqa: F401

RGB = registry.slider_panel_id("RGB")


class _Window(PanelProviderMixin, FloatingPanelsMixin, QWidget):
    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg if cfg is not None else {}
        self.stack = QWidget(self)
        self.lab_slider_column = QWidget(self)
        self.preview_box = QWidget(self)
        self.color_history = QWidget(self)
        self.slider_containers = {g: QLabel(g, self) for g in core_config.SLIDER_GROUPS}
        self.panel_host = PanelHost(self.panel_provider(), self)
        self.panel_host.set_tree(dock.Split(dock.VERTICAL, (dock.Leaf(RGB),), (), False))


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    return _Window()


class _FakeWin32:
    """Very small win32gui stand-in: records ShowWindow calls."""

    calls: list = []

    @staticmethod
    def ShowWindow(hwnd, cmd):
        _FakeWin32.calls.append((hwnd, cmd))
        return True


class _FakeWin32Con:
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4


def _fake_win32(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32gui", _FakeWin32)
    monkeypatch.setitem(sys.modules, "win32con", _FakeWin32Con)


# 离屏下"带父的 Tool 窗口"可见性不可靠（父窗口未显示时永远算 hidden），
# hide 方向的断言放到真实窗口 E2E（tools/preview_panel_drag.py）里做。

def test_foreground_show_brings_them_back(window):
    window.float_panel(RGB)
    window.set_floating_foreground_visible(False)
    window.set_floating_foreground_visible(True)
    assert not window.floating_windows()[RGB].isHidden()


def test_a_panel_hidden_for_its_own_reason_stays_hidden(window):
    """模块过滤藏起来的面板，不能被前台恢复硬拉出来。"""
    window.float_panel(RGB)
    window.set_floating_foreground_visible(False)
    # 面板自己被藏起来（模拟模块切换）
    window.panel_widget(RGB).setVisible(False)
    window.set_floating_foreground_visible(True)
    assert window.floating_windows()[RGB].isHidden()


def test_no_floating_windows_is_a_no_op(window):
    window.set_floating_foreground_visible(False)
    window.set_floating_foreground_visible(True)
    assert window.floating_windows() == {}


def test_foreground_hide_drives_the_real_hwnd(window, monkeypatch):
    """Qt 的 hide() 对"已因祖先而算 hidden"的 Tool 窗口是 no-op，真隐藏
    必须落到 ShowWindow(SW_HIDE) 上——否则主窗藏了、浮窗还亮在屏幕上。"""
    _fake_win32(monkeypatch)
    window.float_panel(RGB)
    floater = window.floating_windows()[RGB]
    _FakeWin32.calls.clear()
    window.set_floating_foreground_visible(False)
    assert any(cmd == _FakeWin32Con.SW_HIDE for _, cmd in _FakeWin32.calls)
    assert floater.isHidden()

    _FakeWin32.calls.clear()
    window.set_floating_foreground_visible(True)
    assert any(cmd == _FakeWin32Con.SW_SHOWNOACTIVATE for _, cmd in _FakeWin32.calls)
    assert not floater.isHidden()


def test_foreground_hidden_panel_show_does_not_pop_the_window(window, monkeypatch):
    """主窗被前台规则藏起来后，即使面板自己（模块切换）又 show 了，浮窗也要
    继续藏——否则屏幕上会冒出孤零零的浮窗。"""
    _fake_win32(monkeypatch)
    window.float_panel(RGB)
    floater = window.floating_windows()[RGB]
    window.set_floating_foreground_visible(False)
    window.panel_widget(RGB).setVisible(False)
    window.panel_widget(RGB).setVisible(True)
    assert floater.isHidden()


def test_main_window_show_hide_events_sync_the_floats(window):
    """热键/托盘/关闭到托盘是直接 hide()/show()，不经过 check_foreground_window；
    它们也必须把浮窗一起藏/现。"""
    window.float_panel(RGB)
    floater = window.floating_windows()[RGB]
    window.show()
    window.hide()
    assert floater.isHidden()
    window.show()
    assert not floater.isHidden()