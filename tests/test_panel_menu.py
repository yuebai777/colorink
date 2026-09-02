"""抓手右键菜单：把跟这块面板有关的操作放到手边。

复位埋在设置里、浮出要靠"拖到窗口外"这种没人猜得到的手势——右键是大家
都会试的动作，正好用来兜住这些能力。菜单项本身是纯数据，好测。
"""

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from core import config as core_config
from ui.panels import menu, registry, store
from ui.panels import tree as dock
from ui.panels.host import PanelHost
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.panels_mixin import PanelProviderMixin

from .test_ringless_preview_support import qapp  # noqa: F401

RGB = registry.slider_panel_id("RGB")
HSV = registry.slider_panel_id("HSV")


class _Window(PanelProviderMixin, FloatingPanelsMixin, QWidget):
    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg if cfg is not None else {}
        self.stack = QWidget(self)
        self.lab_slider_column = QWidget(self)
        self.preview_box = QWidget(self)
        self.color_history = QWidget(self)
        self.slider_containers = {group: QLabel(group, self)
                                  for group in core_config.SLIDER_GROUPS}
        self.panel_host = PanelHost(self.panel_provider(), self)
        self.panel_host.set_tree(dock.Split(dock.VERTICAL, (
            dock.Leaf(RGB), dock.Leaf(HSV)), (), False))
        self.refreshed = 0

    def refresh_slider_visibility_and_order(self):
        self.refreshed += 1


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    return _Window()


# ── 菜单内容（纯数据） ───────────────────────────────────────────────────

def test_a_docked_panel_can_be_sent_out():
    actions = dict(menu.panel_menu_actions(RGB, floating=False))
    assert menu.FLOAT in actions
    assert menu.DOCK not in actions


def test_a_floating_panel_can_be_taken_back():
    actions = dict(menu.panel_menu_actions(RGB, floating=True))
    assert menu.DOCK in actions
    assert menu.FLOAT not in actions


def test_every_panel_can_be_hidden_and_the_layout_reset():
    for floating in (False, True):
        actions = dict(menu.panel_menu_actions(RGB, floating=floating))
        assert menu.HIDE in actions
        assert menu.RESET in actions


def test_a_panel_with_no_visibility_switch_cannot_be_hidden():
    actions = dict(menu.panel_menu_actions(registry.PICKER, floating=False))
    assert menu.HIDE not in actions


def test_every_entry_has_a_label():
    for action, label in menu.panel_menu_actions(RGB, floating=False):
        assert isinstance(label, str) and label.strip(), action


# ── 执行 ─────────────────────────────────────────────────────────────────

def test_float_from_the_menu_tears_it_off(window):
    window.run_panel_action(RGB, menu.FLOAT)
    assert RGB in window.floating_windows()


def test_dock_from_the_menu_brings_it_back(window):
    window.float_panel(RGB)
    window.run_panel_action(RGB, menu.DOCK)
    assert RGB not in window.floating_windows()
    assert window.panel_widget(RGB).parent() is not None


def test_hide_from_the_menu_flips_the_same_switch_as_settings(window):
    window.run_panel_action(RGB, menu.HIDE)
    assert window.cfg["showSlidersRGB"] is False
    assert window.refreshed == 1, "藏起来之后要重排一次"


def test_hiding_a_floating_panel_puts_it_away_first(window):
    window.float_panel(RGB)
    window.run_panel_action(RGB, menu.HIDE)
    assert RGB not in window.floating_windows()
    assert window.cfg["showSlidersRGB"] is False


def test_reset_from_the_menu_forgets_the_arrangement(window):
    store.save_into(window.cfg, dock.Leaf(RGB), "stack")
    window.run_panel_action(RGB, menu.RESET)
    assert store.CONFIG_KEY not in window.cfg
    assert window.refreshed == 1


def test_an_unknown_action_does_nothing(window):
    assert window.run_panel_action(RGB, "nonsense") is False
