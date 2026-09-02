"""面板模型：注册表 + 停靠树（纯数据，先于任何控件改动落地）。

这是面板化的地基：注册表说"有哪些面板、最小多大、谁必须跟着谁"，停靠树说
"它们怎么摆"，并且要能安全地存进配置再读回来 —— 包括读到旧版本里已经不存
在的面板 id 时不能把窗口搞空。
"""

import pytest

from ui.panels import registry
from ui.panels import tree as dock
from ui.panels.spec import PanelSpec


# ── 契约 ─────────────────────────────────────────────────────────────────

def test_spec_rejects_nonsense():
    with pytest.raises(ValueError):
        PanelSpec(id="", title="x", min_size=(10, 10))
    with pytest.raises(ValueError):
        PanelSpec(id="a", title="x", min_size=(0, 10))
    with pytest.raises(ValueError):
        PanelSpec(id="a", title="x", min_size=(10, 10), aspect=0.0)


def test_picker_declares_its_square_aspect():
    """色环/圆盘内接于正方形 —— 宿主忽略这条就会长出细高条。"""
    picker = registry.panel(registry.PICKER)
    assert picker is not None
    assert picker.aspect == 1.0
    assert picker.height_for_width(300) == 300


def test_non_aspect_panel_reports_its_minimum_height():
    history = registry.panel(registry.HISTORY)
    assert history.aspect is None
    assert history.height_for_width(999) == history.min_size[1]


def test_what_rides_inside_the_picker():
    """明度条、前景背景长在取色区里 —— 树只摆 picker 就算完整。"""
    assert registry.satellites_of(registry.PICKER) == (
        registry.LIGHTNESS, registry.SWATCHES)
    for satellite in (registry.LIGHTNESS, registry.SWATCHES):
        assert registry.is_satellite(satellite), satellite
    assert not registry.is_satellite(registry.PICKER)


def test_every_slider_group_has_a_panel():
    for group in registry.SLIDER_GROUPS:
        assert registry.panel(registry.slider_panel_id(group)) is not None


def test_unknown_panel_lookup_is_none_not_an_error():
    assert registry.panel("nope") is None
    assert registry.satellites_of("nope") == ()


def test_every_registered_panel_has_a_sane_minimum():
    for spec in registry.PANELS.values():
        assert spec.min_size[0] > 0 and spec.min_size[1] > 0


# ── 停靠树 ───────────────────────────────────────────────────────────────

def test_default_tree_places_every_non_satellite_panel_once():
    node = dock.default_tree()
    placed = node.panels()
    assert len(placed) == len(set(placed)), "同一面板被摆了两次"
    assert dock.missing_panels(node) == ()


def test_default_tree_is_a_single_column():
    node = dock.default_tree()
    assert isinstance(node, dock.Split)
    assert node.orientation == dock.VERTICAL
    assert node.panels()[0] == registry.PICKER


def test_round_trip_through_json():
    node = dock.default_tree()
    assert dock.load(node.to_json()) == node


def test_tabs_round_trip():
    node = dock.Tabs((registry.HISTORY, registry.slider_panel_id("RGB")), 1)
    assert dock.load(node.to_json()) == node


def test_nested_split_round_trip():
    node = dock.Split(dock.HORIZONTAL, (
        dock.Leaf(registry.PICKER),
        dock.Split(dock.VERTICAL, (dock.Leaf(registry.HISTORY),
                                   dock.Leaf(registry.slider_panel_id("LAB")))),
    ), (0.6, 0.4))
    assert dock.load(node.to_json()) == node


def test_unknown_panel_is_pruned():
    """旧布局里的陌生 id 直接丢掉，不能整棵树作废。"""
    data = {"kind": "split", "orientation": "vertical", "children": [
        {"kind": "leaf", "panel": "gone-in-this-build"},
        {"kind": "leaf", "panel": registry.HISTORY},
    ]}
    assert dock.load(data) == dock.Leaf(registry.HISTORY)


def test_tree_with_nothing_left_falls_back_to_default():
    data = {"kind": "leaf", "panel": "gone"}
    assert dock.load(data) == dock.default_tree()
    assert dock.load(None) == dock.default_tree()
    assert dock.load({"kind": "bogus"}) == dock.default_tree()


def test_single_child_split_collapses():
    data = {"kind": "split", "orientation": "horizontal", "children": [
        {"kind": "leaf", "panel": registry.PICKER}]}
    assert dock.load(data) == dock.Leaf(registry.PICKER)


def test_tabs_with_one_survivor_collapses_to_a_leaf():
    data = {"kind": "tabs", "items": ["gone", registry.HISTORY], "current": 1}
    assert dock.load(data) == dock.Leaf(registry.HISTORY)


def test_bad_current_index_is_clamped():
    data = {"kind": "tabs",
            "items": [registry.HISTORY, registry.slider_panel_id("RGB")],
            "current": 99}
    assert dock.load(data).current == 0


def test_mismatched_sizes_are_dropped():
    data = {"kind": "split", "orientation": "vertical", "children": [
        {"kind": "leaf", "panel": registry.PICKER},
        {"kind": "leaf", "panel": registry.HISTORY}], "sizes": [1.0]}
    assert dock.load(data).sizes == ()


def test_missing_panels_reports_what_a_partial_tree_left_out():
    node = dock.Leaf(registry.PICKER)
    missing = dock.missing_panels(node)
    assert registry.HISTORY in missing
    assert registry.LIGHTNESS not in missing, "卫星面板不该被算作缺失"

# ── B-4：可拖动分割 ──────────────────────────────────────────────────────

def test_two_column_splits_in_half():
    """面板列表一分为二，放进一个可拖动的水平分割。"""
    ids = ("history", "sliders.rgb", "sliders.hsv", "sliders.hsl",
           "sliders.lab", "sliders.oklab", "sliders.oklch")
    node = dock.two_column_tree(ids, spacing=8)
    assert isinstance(node, dock.Split)
    assert node.orientation == dock.HORIZONTAL
    assert node.resizable is True
    assert len(node.children) == 2
    left = node.children[0].panels(); right = node.children[1].panels()
    assert len(left) == 4 and len(right) == 3
    assert left + right == ids
    assert all(not child.resizable for child in node.children), "两列内仍是内容定高堆叠"


def test_two_column_respects_first_count():
    node = dock.two_column_tree(("a", "b", "c", "d", "e"), first_count=2)
    assert node.children[0].panels() == ("a", "b")
    assert node.children[1].panels() == ("c", "d", "e")


def test_two_column_with_a_single_panel_collapses():
    node = dock.two_column_tree(("only",))
    assert not node.resizable
    assert node.panels() == ("only",)


def test_two_column_round_trips_through_json():
    """单子列在反问时会塌缩成 Leaf（既有规则），所以按序列化形态比较。"""
    ids = tuple(f"sliders.{g}" for g in ("rgb", "hsv", "lab"))
    tree = dock.two_column_tree(ids, spacing=6)
    loaded = dock.load(tree.to_json())
    assert dock.load(loaded.to_json()).to_json() == loaded.to_json()
    assert loaded.panels() == ids

# ── B-4：页签 ────────────────────────────────────────────────────────────

def test_tabbed_tree_groups_panels_per_page():
    ids = tuple(f"sliders.{g}" for g in ("rgb", "hsv", "hsl", "lab"))
    node = dock.tabbed_tree(ids, tab_size=2)
    assert isinstance(node, dock.Tabs)
    assert len(node.pages) == 2
    assert node.pages[0] == ids[:2]
    assert node.pages[1] == ids[2:]


def test_tabbed_tree_collapses_to_a_column_when_single_page():
    node = dock.tabbed_tree(("a", "b"), tab_size=3)
    assert isinstance(node, dock.Split)
    assert node.panels() == ("a", "b")


def test_tabs_pages_round_trips_through_json():
    ids = tuple(f"sliders.{g}" for g in ("rgb", "hsv", "hsl"))
    node = dock.tabbed_tree(ids, tab_size=2)
    loaded = dock.load(node.to_json())
    assert isinstance(loaded, dock.Tabs)
    assert loaded.pages == node.pages
    assert dock.load(loaded.to_json()).to_json() == loaded.to_json()


def test_tabs_legacy_items_still_work():
    """旧存档（items：每个面板一页）仍能读出来。"""
    data = {"kind": "tabs", "items": ["sliders.rgb", "sliders.hsv"],
            "current": 1}
    loaded = dock.load(data)
    assert isinstance(loaded, dock.Tabs)
    assert loaded.pages == (("sliders.rgb",), ("sliders.hsv",))
    assert loaded.panels() == ("sliders.rgb", "sliders.hsv")
