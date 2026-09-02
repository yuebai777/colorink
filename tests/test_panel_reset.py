"""复位面板布局：拖坏了怎么回来。

拖拽重排没有撤销，所以必须有一条回头路。复位只丢"拖出来的排布"，
不动用户勾的那几个布局开关——那是设置，不是刚才拖出来的一团。
"""

from ui.panels import registry, store
from ui.panels import tree as dock

from .test_settings_tooltips import qapp, sidebar  # noqa: F401


def test_the_settings_button_exists_and_says_what_it_does(sidebar):
    button = sidebar.btn_reset_panel_layout
    assert button.text()
    assert button.toolTip()


def test_the_button_forgets_a_dragged_arrangement(sidebar):
    store.save_into(sidebar.cfg, dock.Leaf(registry.HISTORY), "stack")
    seen = []
    sidebar.settingChanged.connect(lambda: seen.append(1))

    sidebar.btn_reset_panel_layout.click()

    assert store.CONFIG_KEY not in sidebar.cfg
    assert seen == [1], "窗口是靠这个信号重新装配的，不发就等于没复位"


def test_the_button_leaves_the_layout_switches_alone(sidebar):
    sidebar.cfg["slidersSplit"] = True
    sidebar.cfg["panelDrag"] = True

    sidebar.btn_reset_panel_layout.click()

    assert sidebar.cfg["slidersSplit"] is True
    assert sidebar.cfg["panelDrag"] is True


def test_the_button_survives_being_clicked_twice(sidebar):
    sidebar.btn_reset_panel_layout.click()
    sidebar.btn_reset_panel_layout.click()
    assert store.CONFIG_KEY not in sidebar.cfg

# ── 面板布局卡：浮出清单 + 全部收回 ─────────────────────────────────────

def test_panel_settings_live_on_their_own_page(sidebar):
    """面板的事有自己的一级分组，不再挂在"取色器"底下。"""
    page = sidebar.stack.widget(3)
    for name in ("cb_panel_drag", "cb_sliders_split", "cb_sliders_tabs",
                 "btn_reset_panel_layout", "btn_dock_all_panels",
                 "lbl_floating_panels", "lbl_same_space", "lbl_diff_space"):
        assert hasattr(sidebar, name), name
        assert page.isAncestorOf(getattr(sidebar, name)), name
    assert sidebar.nav.item(3).text() in ("面板", "Panels")


def test_the_list_says_nothing_is_out_when_nothing_is(sidebar):
    sidebar.refresh_floating_panel_list()
    assert "无" in sidebar.lbl_floating_panels.text()
    assert sidebar.btn_dock_all_panels.isEnabled() is False


def test_the_list_names_what_is_out(sidebar):
    store.save_floating_into(sidebar.cfg,
                             {registry.slider_panel_id("RGB"):
                              store.FloatingState((0, 0, 200, 80))})
    sidebar.refresh_floating_panel_list()
    assert "RGB" in sidebar.lbl_floating_panels.text()
    assert sidebar.btn_dock_all_panels.isEnabled() is True


def test_dock_all_asks_the_window_to_take_everything_back(sidebar):
    """拖到屏幕外找不着的兜底：不用找到那个窗口也能收回来。"""
    docked = []

    class _Win:
        cfg = {}

        def dock_panel(self, panel_id):
            docked.append(panel_id)
            return True

    # 保住原来的 Qt 父窗口：_parent 是它唯一的 Python 引用，覆盖掉就会被
    # 回收，C++ 侧连带删掉整个侧栏（交接坑 9 的同一类）。
    keep_alive = sidebar._parent
    assert keep_alive is not None
    window = _Win()
    window.cfg = dict(sidebar.cfg)
    store.save_floating_into(window.cfg,
                             {registry.slider_panel_id("RGB"):
                              store.FloatingState((0, 0, 200, 80)),
                              registry.HISTORY:
                              store.FloatingState((0, 0, 200, 80))})
    sidebar._parent = window

    sidebar.dock_all_floating_panels()

    assert sorted(docked) == sorted([registry.slider_panel_id("RGB"),
                                     registry.HISTORY])
    sidebar._parent = keep_alive
