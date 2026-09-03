"""PanelHost：把停靠树渲染成真实控件，并能把当前布局读回来。

宿主只负责"怎么摆"，面板控件由 provider 提供 —— 重建布局时控件是被
**重新挂载**而不是重新创建的，否则用户正在编辑的状态会被丢掉。
"""

import pytest
from PyQt6.QtWidgets import QLabel, QSplitter, QTabWidget, QWidget

from ui.panels import registry, store
from ui.panels import tree as dock
from ui.panels.host import PanelHost

from .test_ringless_preview_support import qapp  # noqa: F401


@pytest.fixture
def panels(qapp):
    made = {}

    def provider(panel_id):
        if panel_id == "missing":
            return None
        made.setdefault(panel_id, QLabel(panel_id))
        return made[panel_id]

    return provider, made


def test_leaf_mounts_the_provided_widget(panels):
    provider, made = panels
    host = PanelHost(provider)
    host.set_tree(dock.Leaf(registry.HISTORY))
    assert host.widget_for(registry.HISTORY) is made[registry.HISTORY]
    assert made[registry.HISTORY].parent() is not None


def test_split_builds_a_splitter(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf(registry.PICKER), dock.Leaf(registry.HISTORY))))
    splitters = host.findChildren(QSplitter)
    assert len(splitters) == 1
    assert splitters[0].count() == 2


def test_tabs_build_a_tab_widget_with_titles(panels):
    provider, _ = panels
    host = PanelHost(provider)
    node = dock.Tabs((registry.HISTORY, registry.slider_panel_id("RGB")), 1)
    host.set_tree(node)
    tabs = host.findChildren(QTabWidget)
    assert len(tabs) == 1
    assert tabs[0].count() == 2
    assert tabs[0].currentIndex() == 1
    assert tabs[0].tabText(0) == registry.panel(registry.HISTORY).title


def test_nested_split_builds_nested_splitters(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.set_tree(dock.Split(dock.HORIZONTAL, (
        dock.Leaf(registry.PICKER),
        dock.Split(dock.VERTICAL, (dock.Leaf(registry.HISTORY),
                                   dock.Leaf(registry.slider_panel_id("LAB")))),
    )))
    assert len(host.findChildren(QSplitter)) == 2
    assert set(host.mounted_panels()) == {
        registry.PICKER, registry.HISTORY, registry.slider_panel_id("LAB")}


def test_panels_survive_a_rebuild(panels):
    """重挂布局不能销毁面板控件 —— 那会连带丢掉用户正在编辑的状态。"""
    provider, made = panels
    host = PanelHost(provider)
    host.set_tree(dock.default_tree())
    picker = host.widget_for(registry.PICKER)
    host.set_tree(dock.Leaf(registry.PICKER))
    assert host.widget_for(registry.PICKER) is picker
    assert picker is made[registry.PICKER]


def test_unavailable_panel_is_skipped(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf("missing"), dock.Leaf(registry.HISTORY))))
    assert host.mounted_panels() == (registry.HISTORY,)


def test_sizes_are_applied_and_read_back(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.resize(400, 400)
    node = dock.Split(dock.VERTICAL, (
        dock.Leaf(registry.PICKER), dock.Leaf(registry.HISTORY)), (0.75, 0.25))
    host.set_tree(node)
    splitter = host.findChildren(QSplitter)[0]
    sizes = splitter.sizes()
    assert sizes[0] > sizes[1], "比例没有被应用"
    read = host.tree()
    assert isinstance(read, dock.Split)
    assert len(read.sizes) == 2
    assert sum(read.sizes) == pytest.approx(1.0, abs=1e-6)


def test_current_tab_is_read_back(panels):
    provider, _ = panels
    host = PanelHost(provider)
    node = dock.Tabs((registry.HISTORY, registry.slider_panel_id("RGB")), 0)
    host.set_tree(node)
    host.findChildren(QTabWidget)[0].setCurrentIndex(1)
    assert host.tree().current == 1


def test_read_back_survives_a_round_trip_through_the_store(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.set_tree(dock.default_tree())
    config = {}
    store.save_into(config, host.tree())
    assert store.CONFIG_KEY in config
    assert store.load_from(config).panels() == dock.default_tree().panels()


# ── 持久化 ───────────────────────────────────────────────────────────────

def test_store_round_trip():
    node = dock.default_tree()
    assert store.parse(store.dump(node)) == node


def test_store_rejects_a_future_version():
    data = store.dump(dock.Leaf(registry.HISTORY))
    data["version"] = store.LAYOUT_VERSION + 1
    assert store.parse(data) == dock.default_tree()


def test_store_handles_garbage():
    assert store.parse(None) == dock.default_tree()
    assert store.parse("nope") == dock.default_tree()
    assert store.parse({}) == dock.default_tree()
    assert store.load_from(None) == dock.default_tree()


def test_store_prunes_panels_this_build_dropped():
    data = {"version": store.LAYOUT_VERSION,
            "root": {"kind": "split", "orientation": "vertical", "children": [
                {"kind": "leaf", "panel": "removed-panel"},
                {"kind": "leaf", "panel": registry.HISTORY}]}}
    assert store.parse(data) == dock.Leaf(registry.HISTORY)


def test_tabs_hint_includes_multi_panel_spacing_and_top_gap(panels):
    provider, _ = panels
    host = PanelHost(provider)
    host.set_stack_spacing(10)
    class _DummyChrome:
        top_gap = 15
        font_size = 11
        scale = 1.0
        bar_bg = ""
        background = ""
        text = ""
        bar_text = ""
        divider_color = ""
        divider_width = 1
    host.apply_chrome(_DummyChrome())
    node = dock.Tabs(pages=(
        (registry.HISTORY, registry.slider_panel_id("RGB")),
        (registry.slider_panel_id("LAB"),)
    ), current=0)
    host.set_tree(node)
    hint = host.column_hint()
    p0_height = (host._panel_box(registry.HISTORY).sizeHint().height() +
                 host._panel_box(registry.slider_panel_id("RGB")).sizeHint().height() + 10)
    assert hint >= p0_height + 15


def test_panel_tab_widget_size_hint_includes_top_gap(panels):
    from ui.panels.host import PanelTabWidget
    provider, _ = panels
    host = PanelHost(provider)
    class _DummyChrome:
        top_gap = 20
        font_size = 11
        scale = 1.0
        bar_bg = ""
        background = ""
        text = ""
        bar_text = ""
        divider_color = ""
        divider_width = 1
    host.apply_chrome(_DummyChrome())
    node = dock.Tabs(pages=((registry.HISTORY,), (registry.slider_panel_id("RGB"),)), current=0)
    host.set_tree(node)
    tab_widgets = host.findChildren(PanelTabWidget)
    assert len(tab_widgets) == 1
    hint = tab_widgets[0].sizeHint()
    min_hint = tab_widgets[0].minimumSizeHint()
    assert hint.height() >= 20
    assert min_hint.height() >= 20
