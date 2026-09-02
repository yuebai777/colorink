"""面板排布：树驱动的顺序，以及它与配置的往返。

接管的第一步是把"怎么排"变成数据。今天这棵树仍由既有的每组顺序键推导，
所以排布逐像素不变；但它会被写进 cfg["panelLayout"]，将来拖拽重排才有
地方落地，而不需要在用户背后冒出第二个真相来源。
"""

import pytest
from PyQt6.QtWidgets import QWidget

from core import config
from ui.panels import rearrange, registry, store
from ui.panels import tree as dock
from ui.window.panels_mixin import PanelProviderMixin
from ui.window.picker_actions import PickerActionsMixin

from .test_ringless_preview_support import qapp  # noqa: F401


class _Host(PanelProviderMixin, PickerActionsMixin):
    def __init__(self, cfg=None):
        self.cfg = cfg if cfg is not None else {}
        self.stack = QWidget()
        self.lab_slider_column = QWidget()
        self.preview_box = QWidget()
        self.color_history = QWidget()
        self.slider_containers = {group: QWidget() for group in config.SLIDER_GROUPS}


@pytest.fixture
def host(qapp):
    return _Host()


def test_tree_lists_every_slider_group_in_config_order(host):
    tree = host.slider_column_tree()
    ids = list(tree.panels())
    expected = [registry.HISTORY if group == "History"
                else registry.slider_panel_id(group)
                for group in config.sorted_slider_groups(host.cfg)]
    assert ids == expected


def test_slider_column_is_a_non_resizable_stack(host):
    """滑块区是内容定高的堆叠，不是可拖动分割 —— 宿主要按这个渲染。"""
    tree = host.slider_column_tree(spacing=8, margins=(4, 6, 4, 10))
    assert isinstance(tree, dock.Split)
    assert tree.resizable is False
    assert tree.spacing == 8
    assert tree.margins == (4, 6, 4, 10)


def test_layout_order_matches_the_legacy_helper(host):
    """树驱动的顺序必须和旧的配置助手完全一致（像素等价的前提）。"""
    assert host._slider_groups_in_layout_order() == config.sorted_slider_groups(host.cfg)


@pytest.mark.parametrize("order", [
    {"orderSlidersRGB": 7, "orderSlidersHistory": 1},
    {"orderSlidersLAB": 1, "orderSlidersHSV": 6},
])
def test_reordering_config_reorders_the_tree(qapp, order):
    host = _Host(dict(order))
    assert host._slider_groups_in_layout_order() == config.sorted_slider_groups(host.cfg)


def test_saved_arrangement_is_used_when_it_places_the_same_panels(host):
    derived = host.slider_column_tree()
    flipped = dock.Split(dock.VERTICAL, tuple(reversed(derived.children)),
                         (), False, derived.spacing, derived.margins)
    store.save_into(host.cfg, flipped, host.arrangement_seed())
    assert host.panel_layout_tree().panels() == flipped.panels()


def test_a_record_without_a_seed_is_not_claimed(host):
    """老存档（B-4 之前写的）没有出身信息，按推导来，不猜。"""
    derived = host.slider_column_tree()
    store.save_into(host.cfg, dock.Split(
        dock.VERTICAL, tuple(reversed(derived.children)), (), False))
    assert host.panel_layout_tree().panels() == derived.panels()


def test_saved_arrangement_is_ignored_when_the_panel_set_differs(host):
    """存档里少一个/多一个面板时回落到配置推导，不能把面板弄丢。"""
    store.save_into(host.cfg, dock.Leaf(registry.HISTORY))
    assert host.panel_layout_tree().panels() == host.slider_column_tree().panels()


def test_save_panel_layout_writes_a_readable_record(host):
    host.save_panel_layout()
    assert store.CONFIG_KEY in host.cfg
    assert store.load_from(host.cfg).panels() == host.slider_column_tree().panels()


# ── 拖拽重排落地后的归属（B-4） ───────────────────────────────────────────

def test_the_seed_says_which_switch_built_the_arrangement(host):
    assert host.arrangement_seed() == "stack"
    host.cfg["slidersSplit"] = True
    assert host.arrangement_seed() == "split"
    host.cfg["slidersTabs"] = True
    assert host.arrangement_seed() == "tabs", "页签优先于并排"


def test_tab_mode_excludes_hidden_and_out_of_module_groups(host):
    """回归：分页叠放不能把用户没开启/当前模块不提供的滑块组也开成页签——
    那会在设置里明明关着、界面上却跑出一个空页签。"""
    host.cfg["slidersTabs"] = True
    host.cfg["showSlidersHistory"] = False
    host.cfg["showSlidersHSL"] = True  # HSL 在 hsv 模块的允许集之外
    groups = host._slider_groups_in_layout_order()
    tree = host._slider_column_tree_for(groups)
    ids = set(tree.panels())
    assert isinstance(tree, dock.Tabs)
    assert registry.HISTORY not in ids
    assert registry.slider_panel_id("HSL") not in ids
    assert registry.slider_panel_id("RGB") in ids


def test_saving_records_the_tree_it_was_given(host):
    """存的必须是真正挂上去的那棵树，否则页签模式会被存成单列。"""
    ids = list(host.slider_column_tree().panels())
    tabs = dock.tabbed_tree(ids, tab_size=2)
    host.cfg["slidersTabs"] = True
    host.save_panel_layout(tabs)
    assert store.load_from(host.cfg, "tabs") == tabs
    assert store.load_from(host.cfg, "stack") == dock.default_tree(), \
        "别的开关不该认领这份存档"


def test_a_dragged_arrangement_survives_the_next_refresh(host):
    groups = host._slider_groups_in_layout_order()
    derived = host._slider_column_tree_for(groups)
    dragged = rearrange.move_panel(derived, registry.HISTORY,
                                   registry.slider_panel_id("RGB"),
                                   rearrange.TOP)
    assert dragged != derived, "前提：这一拖确实改变了排布"
    host.save_panel_layout(dragged)
    assert host._slider_column_tree_for(groups) == dragged
    assert host._slider_groups_in_layout_order()[0] == "History"


def test_flipping_a_layout_switch_reseeds_the_arrangement(host):
    """勾选"并排"必须真的换成两列——不能被上一份存档顶回去。"""
    groups = host._slider_groups_in_layout_order()
    host.save_panel_layout(host._slider_column_tree_for(groups))
    host.cfg["slidersSplit"] = True
    rebuilt = host._slider_column_tree_for(groups)
    assert rebuilt.orientation == dock.HORIZONTAL
    assert rebuilt.resizable is True


def test_a_saved_arrangement_with_a_foreign_panel_set_is_ignored(host):
    store.save_into(host.cfg, dock.Leaf(registry.HISTORY),
                    host.arrangement_seed())
    groups = host._slider_groups_in_layout_order()
    assert set(host._slider_column_tree_for(groups).panels()) == \
        set(host.slider_column_tree().panels())


# ── 复位（拖坏了怎么回来） ───────────────────────────────────────────────

def test_reset_forgets_the_saved_arrangement(host):
    """拖成一团之后必须有路回来——存档清掉，顺序回到配置推导。"""
    groups = host._slider_groups_in_layout_order()
    dragged = rearrange.move_panel(host._slider_column_tree_for(groups),
                                   registry.HISTORY,
                                   registry.slider_panel_id("RGB"),
                                   rearrange.TOP)
    host.save_panel_layout(dragged)
    assert host._slider_groups_in_layout_order()[0] == "History"

    host.reset_panel_layout()
    assert store.CONFIG_KEY not in host.cfg, "存档要真的清掉，不是覆盖成另一份"
    assert host._slider_groups_in_layout_order() == config.sorted_slider_groups(host.cfg)
    assert host._slider_column_tree_for(groups) == host._slider_column_tree_for(groups)


def test_reset_is_harmless_when_nothing_was_saved(host):
    host.reset_panel_layout()
    assert store.CONFIG_KEY not in host.cfg


def test_reset_keeps_the_layout_switches(host):
    """复位的是"拖出来的排布"，不是用户勾的开关。"""
    host.cfg["slidersSplit"] = True
    host.save_panel_layout(host._slider_column_tree_for(
        host._slider_groups_in_layout_order()))
    host.reset_panel_layout()
    assert host.cfg["slidersSplit"] is True
    assert host._slider_column_tree_for(
        host._slider_groups_in_layout_order()).orientation == dock.HORIZONTAL


def test_layout_order_falls_back_without_the_panel_model(qapp):
    """窄测试替身只绑了几个方法时，顺序仍要能算出来。"""
    class _Narrow(PickerActionsMixin):
        def __init__(self):
            self.cfg = {}

    assert _Narrow()._slider_groups_in_layout_order() == config.sorted_slider_groups({})
