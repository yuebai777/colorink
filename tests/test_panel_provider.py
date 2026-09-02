"""面板 provider：面板 id ↔ 主窗口里真实控件的对应关系。

这是"面板模型"与"当前这套窗口"之间的接缝。它不负责摆放（经典布局仍然
照旧装配），但让停靠树、布局存档和将来的设置界面可以按 id 谈论面板。
"""

import pytest
from PyQt6.QtWidgets import QWidget

from ui.panels import registry
from ui.window.panels_mixin import PanelProviderMixin

from .test_ringless_preview_support import qapp  # noqa: F401


class _Host(PanelProviderMixin):
    def __init__(self, **widgets):
        for name, widget in widgets.items():
            setattr(self, name, widget)


@pytest.fixture
def host(qapp):
    containers = {group: QWidget() for group in registry.SLIDER_GROUPS}
    return _Host(
        stack=QWidget(),
        lab_slider_column=QWidget(),
        preview_box=QWidget(),
        color_history=QWidget(),
        slider_containers=containers,
    )


def test_every_registered_panel_resolves(host):
    """注册表里有的面板，窗口都必须给得出控件。"""
    assert host.missing_panel_widgets() == ()
    for panel_id in registry.panel_ids():
        assert host.panel_widget(panel_id) is not None, panel_id


def test_core_panels_map_to_the_expected_widgets(host):
    assert host.panel_widget(registry.PICKER) is host.stack
    assert host.panel_widget(registry.LIGHTNESS) is host.lab_slider_column
    assert host.panel_widget(registry.SWATCHES) is host.preview_box
    assert host.panel_widget(registry.HISTORY) is host.color_history


@pytest.mark.parametrize("group", registry.SLIDER_GROUPS)
def test_slider_panels_map_case_insensitively(host, group):
    """面板 id 是小写的（sliders.oklab），容器键是配置里的大小写（OKLab）。"""
    panel_id = registry.slider_panel_id(group)
    assert host.panel_widget(panel_id) is host.slider_containers[group]


def test_unknown_panel_is_none(host):
    assert host.panel_widget("nope") is None
    assert host.panel_widget("sliders.nope") is None


def test_a_window_without_widgets_reports_them_missing(qapp):
    bare = _Host()
    assert bare.panel_widget(registry.PICKER) is None
    assert set(bare.missing_panel_widgets()) == set(registry.panel_ids())


def test_provider_is_callable_for_the_host(host):
    provider = host.panel_provider()
    assert callable(provider)
    assert provider(registry.HISTORY) is host.color_history
