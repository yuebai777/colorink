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


def test_deleted_host_survives_drag_geometry_walks(qapp):
    """已删除的宿主不能把拖拽几何遍历变成原生崩溃。

    faulthandler 实测：拖拽移动中 drop_target_at 对已删除/回收的宿主
    调用 mapTo 触发 access violation。这里是同一场景的最小复现——
    宿主 C++ 对象已被删除后，drop_target_at / show_drop_hint 必须安全
    返回 None，而不是继续访问内存。
    """
    from PyQt6 import sip
    from PyQt6.QtCore import QPoint

    def provider2(panel_id):
        return QLabel(panel_id)

    host = PanelHost(provider2)
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf(registry.slider_panel_id("RGB")),
        dock.Leaf(registry.slider_panel_id("HSV")),
    ), (), False))
    sip.delete(host)

    assert host.drop_target_at(QPoint(5, 5)) is None
    assert host.show_drop_hint(QPoint(5, 5)) is None


def _dummy_chrome(top_gap=0):
    class _DummyChrome:
        pass
    chrome = _DummyChrome()
    chrome.top_gap = top_gap
    chrome.font_size = 11
    chrome.scale = 1.0
    chrome.bar_bg = ""
    chrome.background = ""
    chrome.text = ""
    chrome.bar_text = ""
    chrome.divider_color = ""
    chrome.divider_width = 1
    chrome.grip_gap = 4
    chrome.diff_space = 8
    return chrome


def test_box_is_mine_rejects_everything_not_below_the_host(panels, qapp):
    """"_box_is_mine：活着但已不是本宿主后代的控件一律算外人。

    QWidget::mapTo/mapFrom 沿 parentWidget() 找目标，链走不通时会解引用
    NULL —— faulthandler.log 里三次同点 access violation 都踩在这上面。
    sip.isdeleted() 挡不住这一类：控件活着，只是已经归别的窗口了。
    """
    from PyQt6.QtWidgets import QApplication, QWidget

    provider, _made = panels
    host = PanelHost(provider)
    host.set_drag_enabled(True)
    rgb = registry.slider_panel_id("RGB")
    host.set_tree(dock.Leaf(rgb))

    mine = host._panel_box(rgb)
    other_window = QWidget()
    adopted = QWidget()
    adopted.setParent(other_window)
    QApplication.processEvents()

    assert host._box_is_mine(mine) is True
    assert host._box_is_mine(host) is True
    assert host._box_is_mine(None) is False
    assert host._box_is_mine(other_window) is False
    assert host._box_is_mine(adopted) is False


def test_reparented_box_in_bookkeeping_is_ignored_not_crashed(panels, qapp):
    """账目里残留"已换爹"的控件时，拖拽几何遍历必须安全返回。

    模拟浮窗交接窗口期的残留：面板控件被挪进另一个顶层窗口，宿主的
    _mounted/_frames 故意不清理。drop_target_at / show_drop_hint /
    check_tab_hover 必须把残留当"无落点"，而不是在 mapTo 上原生崩溃。
    """
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication, QWidget

    provider, _made = panels
    host = PanelHost(provider)
    host.apply_chrome(_dummy_chrome())
    host.set_drag_enabled(True)
    host.resize(200, 200)
    rgb = registry.slider_panel_id("RGB")
    hsv = registry.slider_panel_id("HSV")
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf(rgb), dock.Leaf(hsv)), (), False))
    QApplication.processEvents()

    # RGB 的 box 被别的窗口领走，账目不清理
    other_window = QWidget()
    other_window.resize(200, 200)
    stray = host._panel_box(rgb)
    stray.setParent(other_window)

    for y in range(0, host.height() + 1, 5):
        for x in range(0, host.width() + 1, 5):
            point = QPoint(x, y)
            target = host.drop_target_at(point)
            hint = host.show_drop_hint(point)
            host.check_tab_hover(point)
            # 残留绝不能变成落点：能命中的只剩还归本宿主管的 HSV
            if target is not None:
                assert target[0] == hsv
            assert hint is None or hint[0] == hsv


def test_stale_tabs_in_bookkeeping_is_ignored_not_crashed(panels, qapp):
    """残留的 tab 条也要安全跳过。

    旧实现对 tabs 只查 isdeleted，不查归属：一条活着的 QTabWidget 记录
    如果已经不归本宿主管（比如宿主重建期间被挪走），第一段 tab 头循环里
    bar.mapTo(self, ...) 一样沿链走到 NULL。
    """
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication, QWidget

    provider, _made = panels
    host = PanelHost(provider)
    host.apply_chrome(_dummy_chrome())
    host.set_drag_enabled(True)
    host.resize(200, 200)
    rgb = registry.slider_panel_id("RGB")
    node = dock.Tabs(pages=((rgb,), (registry.HISTORY,)), current=0)
    host.set_tree(node)
    QApplication.processEvents()

    # 把整棵 tabs 挪进别的窗口，账目不清理
    other_window = QWidget()
    other_window.resize(300, 300)
    stale_tabs, _ = host._tabs[0]
    stale_tabs.setParent(other_window)

    for y in range(0, host.height() + 1, 5):
        for x in range(0, host.width() + 1, 5):
            point = QPoint(x, y)
            assert host.drop_target_at(point) is None
            assert host.show_drop_hint(point) is None
            host.check_tab_hover(point)


# ── 抓手开关往返 / 空列高度（回归：2026-09 用户报告） ──────────────────────
#
# 用户报告：堆叠+抓手都开着，把所有模块拖出去后窗口高度不对；关掉抓手再
# 打开，主窗留下一条空条。两个缺陷各有独立成因，各自钉一条测试。


def test_empty_tabbed_column_bills_no_height(panels):
    """堆叠模式下把所有面板拖出去后，空列不能再向窗口要高度。

    旧实现：_tabs_hint 在没有任何页面、也没有页签条时仍加上 pane 的
    top_gap，于是窗口在取色区下面留着一条 21px 的空条（实测 21 vs 9）。
    """
    provider, _made = panels
    host = PanelHost(provider)
    host.apply_chrome(_dummy_chrome(top_gap=6))
    node = dock.Tabs(pages=((registry.HISTORY,),
                            (registry.slider_panel_id("RGB"),)), current=0)
    host.set_tree(node)
    assert host.column_hint() > 0, "有面板时列当然要占高度"

    host.set_floating_panels(set(node.panels()), remount=True)

    assert host.mounted_panels() == ()
    assert host.column_hint() == 0, "空列不能把 pane 的 top_gap 算成高度"
    assert host.sizeHint().height() == 0


def test_mount_never_shows_a_panel_while_it_is_parentless(panels, qapp):
    """挂载不能先把面板显示出来 —— 那一刻它还是无父的顶层窗口。

    旧实现：_mount 先 widget.setVisible(True) 再塞进框。多面板浮窗会把
    这段瞬态 Show/Hide 镜像到自己身上（浮窗只在重挂时才会走到这条路），
    于是抓手一关一开，浮窗就整体消失且不再回来。
    """
    from PyQt6.QtCore import QEvent, QObject

    provider, _made = panels
    host = PanelHost(provider)
    widget = provider(registry.HISTORY)
    seen_parentless_show = []

    class _Watcher(QObject):
        def eventFilter(self, obj, event):
            if (event.type() in (QEvent.Type.Show, QEvent.Type.ShowToParent)
                    and obj.parent() is None):
                seen_parentless_show.append(event.type().name)
            return False

    watcher = _Watcher()
    widget.installEventFilter(watcher)
    try:
        host.set_drag_enabled(True)
        host.set_tree(dock.Leaf(registry.HISTORY))
    finally:
        widget.removeEventFilter(watcher)

    assert seen_parentless_show == [], \
        f"面板在无父状态下被显示过：{seen_parentless_show}"
    assert host.frame_for(registry.HISTORY) is not None
    assert widget.parent() is not None


def test_a_rebuild_keeps_hidden_groups_hidden_and_visible_ones_visible(panels):
    """重挂（抓手开关）不能把关掉的组亮出来，也不能把开着的组藏起来。

    旧实现靠 _build_stack 里的 setVisible(True) 强行把每个子件显示出来，
    再指望设置循环补一次 Hide —— 组已经关着时不会再有 Hide 事件，于是
    关掉的组留下一条"死抓手"空条。
    """
    provider, made = panels
    host = PanelHost(provider)
    host.set_drag_enabled(True)
    rgb = registry.slider_panel_id("RGB")
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf(rgb), dock.Leaf(registry.HISTORY)), (), False))

    made[rgb].setVisible(False)          # 用户把这个组关掉
    host.set_tree(host.tree())           # 一次重挂（抓手开关就是这条路）

    assert host.frame_for(rgb).isHidden() is True, "关掉的组留下了死抓手"
    assert host.frame_for(registry.HISTORY).isHidden() is False
    assert host.widget_for(rgb).parent() is not None
    assert host.visible_panels() == (registry.HISTORY,)
