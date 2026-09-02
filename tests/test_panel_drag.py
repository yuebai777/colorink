"""拖拽重排：落点几何 + 停靠树手术（纯数据，不需要控件）。

B-4 的最后一块。落点判定（四边 + 中心）和"拖完之后树长什么样"都是可以
脱离 Qt 判定的——所以它们先在这里被测出来，控件层只负责把鼠标位置喂进来。
"""

import pytest

from ui.panels import rearrange
from ui.panels import registry, store
from ui.panels import tree as dock

RGB = registry.slider_panel_id("RGB")
HSV = registry.slider_panel_id("HSV")
HSL = registry.slider_panel_id("HSL")
LAB = registry.slider_panel_id("LAB")


def column(*panel_ids, resizable=False):
    return dock.Split(dock.VERTICAL, tuple(dock.Leaf(p) for p in panel_ids),
                      (), resizable)


# ── 落点几何 ─────────────────────────────────────────────────────────────

def test_there_is_no_dead_middle_to_aim_around():
    """整块面板都是落点：离哪条边近就落在哪边，没有"掉进它里面"这回事。"""
    assert rearrange.zone_at(200, 100, 100, 20) == rearrange.TOP
    assert rearrange.zone_at(200, 100, 100, 80) == rearrange.BOTTOM
    assert rearrange.zone_at(200, 100, 20, 50) == rearrange.LEFT
    assert rearrange.zone_at(200, 100, 180, 50) == rearrange.RIGHT
    for x in range(0, 200, 7):
        for y in range(0, 100, 7):
            assert rearrange.zone_at(200, 100, x, y) in (
                rearrange.LEFT, rearrange.RIGHT, rearrange.TOP,
                rearrange.BOTTOM), (x, y)


def test_the_tab_zone_is_still_available_when_asked_for():
    """页签落点没删掉（树和手术都支持），只是默认不开。"""
    assert rearrange.zone_at(200, 100, 100, 50, allow_center=True) == rearrange.CENTER
    assert rearrange.zone_at(200, 100, 2, 50, allow_center=True) == rearrange.LEFT


@pytest.mark.parametrize("point,expected", [
    ((5, 50), rearrange.LEFT),
    ((195, 50), rearrange.RIGHT),
    ((100, 3), rearrange.TOP),
    ((100, 97), rearrange.BOTTOM),
])
def test_each_border_band_is_its_own_zone(point, expected):
    assert rearrange.zone_at(200, 100, *point) == expected


def test_a_point_outside_the_panel_is_not_a_drop():
    assert rearrange.zone_at(200, 100, -1, 50) is None
    assert rearrange.zone_at(200, 100, 200, 50) is None
    assert rearrange.zone_at(200, 100, 100, 100) is None


def test_a_degenerate_rect_never_crashes():
    assert rearrange.zone_at(0, 0, 0, 0) is None


def test_edge_hint_covers_half_the_panel_and_center_covers_all():
    assert rearrange.drop_rect(200, 100, rearrange.LEFT) == (0, 0, 100, 100)
    assert rearrange.drop_rect(200, 100, rearrange.RIGHT) == (100, 0, 100, 100)
    assert rearrange.drop_rect(200, 100, rearrange.TOP) == (0, 0, 200, 50)
    assert rearrange.drop_rect(200, 100, rearrange.BOTTOM) == (0, 50, 200, 50)
    assert rearrange.drop_rect(200, 100, rearrange.CENTER) == (0, 0, 200, 100)


# ── 摘掉一个面板 ─────────────────────────────────────────────────────────

def test_removing_a_panel_leaves_the_others_in_order():
    pruned = rearrange.remove_panel(column(RGB, HSV, HSL), HSV)
    assert pruned.panels() == (RGB, HSL)


def test_removing_down_to_one_child_collapses_the_split():
    pruned = rearrange.remove_panel(column(RGB, HSV), HSV)
    assert pruned == dock.Leaf(RGB)


def test_removing_the_only_panel_leaves_nothing():
    assert rearrange.remove_panel(dock.Leaf(RGB), RGB) is None


def test_removing_an_absent_panel_changes_nothing():
    node = column(RGB, HSV)
    assert rearrange.remove_panel(node, LAB) == node


def test_the_survivors_keep_their_proportions():
    node = dock.Split(dock.HORIZONTAL,
                      (dock.Leaf(RGB), dock.Leaf(HSV), dock.Leaf(HSL)),
                      (0.2, 0.3, 0.5))
    pruned = rearrange.remove_panel(node, HSV)
    assert pruned.panels() == (RGB, HSL)
    assert pruned.sizes == pytest.approx((0.2 / 0.7, 0.5 / 0.7))


def test_removing_from_a_tab_page_drops_the_empty_page():
    node = dock.Tabs((), 1, ((RGB, HSV), (HSL,)))
    pruned = rearrange.remove_panel(node, HSL)
    assert pruned.panels() == (RGB, HSV)
    assert not isinstance(pruned, dock.Tabs), "只剩一页就不该还是页签"


# ── 插入一个面板 ─────────────────────────────────────────────────────────

def test_dropping_above_a_neighbour_stays_in_the_same_column():
    node = rearrange.insert_panel(column(RGB, HSV), LAB, HSV, rearrange.TOP)
    assert node.panels() == (RGB, LAB, HSV)
    assert node.resizable is False, "普通列不该因为一次拖拽变成可拖动分割"


def test_dropping_below_the_last_panel_appends():
    node = rearrange.insert_panel(column(RGB, HSV), LAB, HSV, rearrange.BOTTOM)
    assert node.panels() == (RGB, HSV, LAB)


def test_dropping_sideways_wraps_the_target_in_a_new_split():
    node = rearrange.insert_panel(column(RGB, HSV), LAB, HSV, rearrange.LEFT)
    assert node.panels() == (RGB, LAB, HSV)
    nested = node.children[1]
    assert isinstance(nested, dock.Split)
    assert nested.orientation == dock.HORIZONTAL
    assert nested.resizable is True, "用户明确拖出来的分割应该可拖动"
    assert nested.sizes == (0.5, 0.5)


def test_dropping_in_the_middle_makes_a_tab_pair():
    node = rearrange.insert_panel(dock.Leaf(RGB), LAB, RGB, rearrange.CENTER)
    assert isinstance(node, dock.Tabs)
    assert node.pages == ((RGB,), (LAB,))
    assert node.current == 1, "拖进来的那一页应该被选中"


def test_dropping_in_the_middle_of_a_tab_adds_a_page():
    node = dock.Tabs((), 0, ((RGB,), (HSV,)))
    grown = rearrange.insert_panel(node, LAB, RGB, rearrange.CENTER)
    assert isinstance(grown, dock.Tabs)
    assert grown.pages == ((RGB,), (LAB,), (HSV,))
    assert grown.current == 1


def test_an_edge_drop_inside_a_tab_page_lands_in_that_page():
    """页签的一页就是一列——横向落点也只能落进这一列。"""
    node = dock.Tabs((), 0, ((RGB, HSV), (HSL,)))
    grown = rearrange.insert_panel(node, LAB, HSV, rearrange.TOP)
    assert grown.pages == ((RGB, LAB, HSV), (HSL,))


def test_reordering_tab_pages_keeps_the_selected_page():
    """拖页签头重排整页：选中的页要跟着它走，索引不能算错。"""
    node = dock.Tabs((), 0, ((RGB,), (HSV,), (HSL,)))
    moved = rearrange.reorder_tab_page(node, 0, 2)
    assert moved.pages == ((HSV,), (HSL,), (RGB,))
    assert moved.current == 2

    node2 = dock.Tabs((), 2, ((RGB,), (HSV,), (HSL,)))
    moved2 = rearrange.reorder_tab_page(node2, 2, 0)
    assert moved2.pages == ((HSL,), (RGB,), (HSV,))
    assert moved2.current == 0


def test_reordering_a_tab_page_changes_nothing_when_it_cannot():
    node = dock.Tabs((), 0, ((RGB,), (HSV,)))
    assert rearrange.reorder_tab_page(node, 0, 0) is node
    assert rearrange.reorder_tab_page(node, 9, 0) is node
    leaf = dock.Leaf(RGB)
    assert rearrange.reorder_tab_page(leaf, 0, 0) is leaf


def test_merging_a_panel_into_a_page_drops_the_empty_page():
    """拖到页签头 = 并入那一页；空掉的来源页直接消失。"""
    node = dock.Tabs((), 0, ((RGB,), (HSV,), (HSL,)))
    merged = rearrange.merge_panel_into_page(node, HSV, RGB)
    assert merged.pages == ((RGB, HSV), (HSL,))
    assert merged.current == 0


def test_merging_within_the_same_page_reorders_it():
    node = dock.Tabs((), 0, ((RGB, HSV), (HSL,)))
    merged = rearrange.merge_panel_into_page(node, RGB, HSV)
    assert merged.pages == ((HSV, RGB), (HSL,))


def test_merging_keeps_the_rest_and_selects_the_target_page():
    """源页还有面板时不许删页，当前页要跟着面板走到目标页。"""
    node = dock.Tabs((), 0, ((RGB, HSV), (LAB,)))
    merged = rearrange.merge_panel_into_page(node, RGB, LAB)
    assert merged.pages == ((HSV,), (LAB, RGB))
    assert merged.current == 1


def test_merging_an_unknown_panel_changes_nothing():
    node = dock.Tabs((), 0, ((RGB,), (HSV,)))
    assert rearrange.merge_panel_into_page(node, LAB, RGB) is node


def test_inserting_next_to_an_unknown_target_changes_nothing():
    node = column(RGB, HSV)
    assert rearrange.insert_panel(node, LAB, "nope", rearrange.TOP) == node


# ── 整个搬家 ─────────────────────────────────────────────────────────────

def test_moving_a_panel_reorders_the_column():
    moved = rearrange.move_panel(column(RGB, HSV, HSL), HSL, RGB, rearrange.TOP)
    assert moved.panels() == (HSL, RGB, HSV)


def test_moving_a_panel_onto_itself_changes_nothing():
    node = column(RGB, HSV)
    assert rearrange.move_panel(node, RGB, RGB, rearrange.TOP) == node


def test_moving_never_loses_or_duplicates_a_panel():
    node = column(RGB, HSV, HSL, LAB)
    for zone in rearrange.ZONES:
        moved = rearrange.move_panel(node, LAB, HSV, zone)
        assert sorted(moved.panels()) == sorted(node.panels()), zone


def test_moving_the_only_panel_changes_nothing():
    node = dock.Leaf(RGB)
    assert rearrange.move_panel(node, RGB, RGB, rearrange.BOTTOM) == node


def test_a_rearranged_tree_survives_the_config_round_trip():
    moved = rearrange.move_panel(column(RGB, HSV, HSL), HSL, RGB, rearrange.LEFT)
    assert store.parse(store.dump(moved)) == moved

# ── 控件层：抓手、宿主的落点与投放 ───────────────────────────────────────

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QTabWidget, QWidget  # noqa: E402

from ui.panels.drag import PANEL_MIME, PanelFrame, PanelTitleBar  # noqa: E402
from ui.panels.host import PanelHost  # noqa: E402

from .test_ringless_preview_support import qapp  # noqa: F401,E402


def _lay_out(host):
    """逐层激活布局，把真实几何摆出来。

    离屏时 setGeometry 对未显示的控件是 **post** 一个 resize 事件，不跑事件
    循环就永远读不到（交接文档第 3 坑）。逐层 activate 是同步的。
    """
    for _ in range(3):
        host.layout().activate()
        for child in host.findChildren(QWidget):
            layout = child.layout()
            if layout is not None:
                layout.activate()


@pytest.fixture
def host(qapp):
    """两块等高面板叠成一列的宿主，尺寸确定所以落点可以算。"""
    made = {}

    def provider(panel_id):
        if panel_id not in made:
            label = QLabel(panel_id)
            label.setFixedHeight(100)
            made[panel_id] = label
        return made[panel_id]

    host = PanelHost(provider)
    host.resize(200, 200)
    host.set_tree(column(RGB, HSV))
    _lay_out(host)
    return host


def test_drag_mode_gives_every_panel_a_grip(host):
    host.set_drag_enabled(True)
    frame = host.frame_for(RGB)
    assert isinstance(frame, PanelFrame)
    assert host.widget_for(RGB).parent() is frame


def test_the_grip_is_labelled_with_the_panel_title(host):
    host.set_drag_enabled(True)
    bar = host.frame_for(RGB).findChild(PanelTitleBar)
    assert bar.panel_id == RGB
    assert bar.title == registry.panel(RGB).title


def test_the_grip_carries_the_panel_id_when_dragged(host):
    host.set_drag_enabled(True)
    data = host.frame_for(RGB).findChild(PanelTitleBar).mime_data()
    assert bytes(data.data(PANEL_MIME)).decode("utf-8") == RGB


def test_leaving_drag_mode_puts_the_widget_back(host):
    widget = host.widget_for(RGB)
    host.set_drag_enabled(True)
    host.set_drag_enabled(False)
    assert host.frame_for(RGB) is None
    assert host.widget_for(RGB) is widget, "面板控件不能因为开关抓手被重建"
    assert widget.parent() is not None


def test_hiding_a_panel_hides_its_grip(host):
    """隐藏的滑块组不能只剩一条标题栏挂在那儿。"""
    host.set_drag_enabled(True)
    host.widget_for(RGB).setVisible(False)
    assert host.frame_for(RGB).isHidden() is True
    host.widget_for(RGB).setVisible(True)
    assert host.frame_for(RGB).isHidden() is False


def test_drop_target_reports_the_panel_and_the_zone(host):
    assert host.drop_target_at(QPoint(100, 4)) == (RGB, rearrange.TOP)
    assert host.drop_target_at(QPoint(100, 110)) == (HSV, rearrange.TOP)
    assert host.drop_target_at(QPoint(100, 195)) == (HSV, rearrange.BOTTOM)
    assert host.drop_target_at(QPoint(4, 150)) == (HSV, rearrange.LEFT)


def test_a_point_outside_every_panel_is_no_target(host):
    assert host.drop_target_at(QPoint(100, 260)) is None
    assert host.drop_target_at(QPoint(-5, 50)) is None


def test_the_drop_hint_covers_the_half_that_would_be_taken(host):
    target = host.show_drop_hint(QPoint(100, 4))
    assert target == (RGB, rearrange.TOP)
    hint = host.drop_hint_rect()
    assert (hint.x(), hint.y(), hint.width(), hint.height()) == (0, 0, 200, 50)
    host.clear_drop_hint()
    assert host.drop_hint_rect() is None


def test_dropping_announces_and_mounts_the_new_tree(host):
    seen = []
    host.rearranged.connect(seen.append)
    assert host.apply_drop(HSV, QPoint(100, 4)) is True
    assert len(seen) == 1
    assert seen[0].panels() == (HSV, RGB)
    assert host.tree().panels() == (HSV, RGB)
    assert host.drop_hint_rect() is None, "投放之后落点提示要撤掉"


def test_a_drop_that_changes_nothing_is_not_announced(host):
    seen = []
    host.rearranged.connect(seen.append)
    assert host.apply_drop(RGB, QPoint(100, 4)) is False
    assert seen == []


def test_a_drop_outside_every_panel_is_refused(host):
    assert host.apply_drop(HSV, QPoint(100, 260)) is False


def test_the_grip_strips_are_counted_in_the_column_height(host):
    plain = host.column_hint()
    host.set_drag_enabled(True)
    host.layout().activate()
    gap = host.frame_for(RGB).layout().spacing()
    assert host.column_hint() == plain + 2 * (PanelTitleBar.HEIGHT + gap)


def test_dropping_works_with_the_grips_on(host):
    """抓手开着时落点算的是"面板 + 抓手"那一整块。"""
    host.set_drag_enabled(True)
    _lay_out(host)
    assert host.drop_target_at(QPoint(100, 4)) == (RGB, rearrange.TOP)
    assert host.apply_drop(HSV, QPoint(100, 4)) is True
    assert host.tree().panels() == (HSV, RGB)
    assert host.frame_for(HSV) is not None


def test_a_remount_that_changes_the_panel_set_is_announced(host):
    """挂载变了才通知——窗口高度策略据此重算，空喊会白跑一轮布局。"""
    seen = []
    host.mount_changed.connect(lambda: seen.append(1))
    host.set_tree(column(RGB, HSV, HSL))
    assert seen == [1]
    host.set_tree(column(HSV, RGB, HSL))
    assert seen == [1], "只是换顺序，面板还是那几个"


def test_turning_grips_on_before_anything_is_mounted_mounts_nothing(qapp):
    """开抓手不能顺手把默认树挂起来。

    默认树里有 picker —— 宿主装的是滑块列，一旦它去 provider 要 picker，
    主窗口的取色区就会被拽进滑块列、从主布局里消失（真踩过：窗口开起来
    没有色轮）。
    """
    asked = []

    def provider(panel_id):
        asked.append(panel_id)
        return QLabel(panel_id)

    empty = PanelHost(provider)
    empty.set_drag_enabled(True)
    assert asked == []
    assert empty.mounted_panels() == ()
    # 真正挂一次之后，开关抓手才重挂——而且重挂的是这棵树。
    empty.set_tree(column(RGB, HSV))
    empty.set_drag_enabled(False)
    empty.set_drag_enabled(True)
    assert empty.mounted_panels() == (RGB, HSV)


def test_drag_mode_survives_a_remount(host):
    host.set_drag_enabled(True)
    widget = host.widget_for(RGB)
    host.set_tree(column(HSV, RGB))
    assert host.frame_for(RGB) is not None
    assert host.widget_for(RGB) is widget


def test_a_frame_re_adopts_a_panel_that_was_taken_away(host):
    """被浮窗抢走的面板再交回来，框必须真的重新挂它。"""
    from PyQt6.QtWidgets import QWidget as _QWidget

    host.set_drag_enabled(True)
    _lay_out(host)
    frame = host.frame_for(RGB)
    panel = host.widget_for(RGB)

    elsewhere = _QWidget()
    panel.setParent(elsewhere)          # 浮窗把它端走了
    frame.set_panel(panel)              # 又还回来

    assert panel.parent() is frame
    assert frame.panel() is panel
    assert frame.isHidden() is False


def test_two_columns_are_as_tall_as_the_taller_one(host):
    """并排的两列不能把高度加起来——那会把窗口顶高一倍。

    横向落点会造出这种树（勾选 slidersSplit 也会），高度策略读的就是这个
    值，加错了窗口就凭空长一截。
    """
    host.set_tree(dock.Split(dock.HORIZONTAL, (
        dock.Split(dock.VERTICAL, (dock.Leaf(RGB), dock.Leaf(HSV)), (), False),
        dock.Split(dock.VERTICAL, (dock.Leaf(HSL),), (), False),
    ), (0.5, 0.5)))
    _lay_out(host)
    assert host.column_hint() == 200


def test_a_plain_column_still_adds_up(host):
    """单列的高度必须和以前逐像素一致（经典布局就是它）。"""
    assert host.column_hint() == 200
    host.set_tree(dock.Split(dock.VERTICAL, (
        dock.Leaf(RGB), dock.Leaf(HSV), dock.Leaf(HSL)), (), False, 6))
    _lay_out(host)
    assert host.column_hint() == 300 + 6 * 2


def test_a_hidden_panel_costs_nothing(host):
    host.widget_for(HSV).setVisible(False)
    assert host.column_hint() == 100


def test_reading_back_a_tab_keeps_its_pages(host):
    """一页里叠了两块的页签，读回来不能被拆成两页。"""
    pages = ((RGB, HSV), (HSL,))
    host.set_tree(dock.Tabs((), 0, pages))
    from PyQt6.QtWidgets import QTabWidget
    host.findChildren(QTabWidget)[0].setCurrentIndex(1)
    read = host.tree()
    assert read.pages == pages
    assert read.current == 1


def test_center_drop_is_available_over_a_tabbed_panel(host):
    """回归：拖到已叠放的页签面板上，中心应该是"再加一页"，而不是被
    当成某个边带塞进当前页（塞进去之后就是用户说的"奇怪的样子"）。"""
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSV,))))
    _lay_out(host)
    box = host.widget_for(RGB)
    center = box.mapTo(host, QPoint(box.width() // 2, box.height() // 2))
    assert host.drop_target_at(center) == (RGB, rearrange.CENTER)


def test_center_drop_follows_the_tabs_setting(host):
    """slidersTabs 开着时，即使当前只落成单列，中心落点也要是"叠放"。"""
    host.set_allow_tab_drops(True)
    _lay_out(host)
    box = host.widget_for(RGB)
    center = box.mapTo(host, QPoint(box.width() // 2, box.height() // 2))
    assert host.drop_target_at(center) == (RGB, rearrange.CENTER)


def test_drops_do_not_target_hidden_tab_pages(host):
    """回归：非当前页签里的面板不能抢落点。

    isHidden() 对"祖先（页签页面容器）被隐藏但自己没被显式隐藏"的子控件
    会说谎——旧代码因此把拖到可见页的落点判到隐藏页上，面板被塞进一个
    用户根本看不见的页面（"很奇怪的叠放"）。
    """
    host.set_drag_enabled(True)
    ok_lab = registry.slider_panel_id("OKLab")
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSL, ok_lab))))
    _lay_out(host)
    box = host.frame_for(RGB)
    center = box.mapTo(host, QPoint(box.width() // 2, box.height() // 2))
    target = host.drop_target_at(center)
    assert target is not None and target[0] == RGB, target


def test_only_the_current_tab_page_stays_visible(host):
    """回归：QStackedWidget 用 Hide 事件隐藏非当前页，PanelHolder 的事件
    过滤器不能再把它弹回来——否则每一页都可见，拖到当前页的落点会被
    用户看不见的页抢走，本页的边落点也失去目标。"""
    host.set_drag_enabled(True)
    host.set_tree(dock.Tabs((), 1, ((RGB,), (HSV,), (HSL,))))
    _lay_out(host)
    tabs = host.findChildren(QTabWidget)[0]
    assert tabs.currentIndex() == 1
    assert tabs.widget(1) is host.frame_for(HSV)
    for index in range(tabs.count()):
        page = tabs.widget(index)
        assert page.isVisibleTo(host) == (index == 1), (
            index, page.isVisible(), page.isHidden(), page.isVisibleTo(host))


def test_tab_bar_is_movable_and_reorder_commits_on_release(host):
    """页签头可拖；拖完松手才重排，拖的过程中不重建。"""
    host.set_drag_enabled(True)
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSV,), (HSL,))))
    _lay_out(host)
    tabs = host.findChildren(QTabWidget)[0]
    bar = tabs.tabBar()
    assert bar.isMovable()
    # QTabBar emits tabMoved mid-drag; the host must wait for the release.
    bar.tabMoved.emit(0, 2)
    assert host.tree().pages == ((RGB,), (HSV,), (HSL,))
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(0, 0),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(bar, release)
    assert host.tree().pages == ((HSV,), (HSL,), (RGB,))


def test_drop_on_a_tab_header_merges_into_that_page(host):
    """拖面板到某页签头上 = 并入那一页（而不是新建一页）。"""
    host.set_drag_enabled(True)
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSV,), (HSL,))))
    _lay_out(host)
    tabs = host.findChildren(QTabWidget)[0]
    bar = tabs.tabBar()
    header = bar.mapTo(host, QPoint(bar.width() // 2, bar.height() // 2))
    target = host.drop_target_at(header)
    assert target == (RGB, rearrange.MERGE_PAGE), target
    assert host.show_drop_hint(header) == target
    assert host.drop_hint_rect() is not None
    assert host.apply_drop(HSV, header) is True
    assert host.tree() == dock.Tabs((), 0, ((RGB, HSV), (HSL,)))


def test_tab_strip_wears_the_chrome(host):
    """页签条跟着主题色走，而不是 Qt 默认的 Windows 蓝。"""
    from ui.panels.floating import PanelChrome

    host.set_drag_enabled(True)
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSV,))))
    host.apply_chrome(PanelChrome(
        background="#101010", border_color="#202020", border_width=1,
        radius=0, text="#ffffff", bar_bg="#202020", bar_text="#dddddd",
        divider_color="#303030", divider_width=2, scale=1.0, font_size=11))
    tabs = host.findChildren(QTabWidget)[0]
    css = tabs.styleSheet()
    assert "#202020" in css
    assert "QTabBar::tab:selected" in css
    assert "font-size: 11px" in css


def test_tab_titles_join_the_panels_in_a_page(host):
    """页签标题列出整页的面板：历史颜色/HSV 这样，而不是只写第一个。"""
    host.set_drag_enabled(True)
    host.set_tree(dock.Tabs((), 0, ((RGB, HSV), (HSL, LAB))))
    _lay_out(host)
    tabs = host.findChildren(QTabWidget)[0]
    assert tabs.tabText(0) == "RGB/HSV"
    assert tabs.tabText(1) == "HSL/LAB"


# ── Qt 自己的拖放事件 ────────────────────────────────────────────────────

# 合成的拖放事件只是**借用** QMimeData（Qt 不持有它）。让 Python 在事件还
# 在的时候回收它会破坏堆，几秒后炸在完全无关的地方——留着，别省这点内存。
_KEEPALIVE = []


def _send(host, event):
    _KEEPALIVE.append(event)
    QApplication.sendEvent(host, event)
    return event


def _panel_drag(host, panel_id, pos, event_type):
    mime = host.frame_for(panel_id).findChild(PanelTitleBar).mime_data()
    _KEEPALIVE.append(mime)
    if event_type is QDropEvent:
        return QDropEvent(QPointF(pos), Qt.DropAction.MoveAction, mime,
                          Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
    return event_type(pos, Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def test_a_real_drag_and_drop_moves_the_panel(host):
    """走 Qt 自己的事件通道，不是只调宿主的辅助方法。"""
    host.set_drag_enabled(True)
    _lay_out(host)
    pos = QPoint(100, 2)
    for event_type in (QDragEnterEvent, QDragMoveEvent):
        assert _send(host, _panel_drag(host, HSV, pos, event_type)).isAccepted()
    assert host.drop_hint_rect() is not None, "拖过去的时候要有落点提示"
    drop = _send(host, _panel_drag(host, HSV, pos, QDropEvent))
    assert drop.isAccepted()
    assert host.tree().panels() == (HSV, RGB)


def test_a_drag_that_is_not_a_panel_is_ignored(host):
    host.set_drag_enabled(True)
    _lay_out(host)
    data = QMimeData()
    data.setText("我不是面板")
    _KEEPALIVE.append(data)
    event = _send(host, QDragEnterEvent(
        QPoint(100, 2), Qt.DropAction.MoveAction, data,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert not event.isAccepted()
    assert host.drop_hint_rect() is None


def test_drops_are_refused_while_rearranging_is_off(host):
    host.set_drag_enabled(True)
    _lay_out(host)
    drag = _panel_drag(host, HSV, QPoint(100, 2), QDragEnterEvent)
    host.set_drag_enabled(False)
    assert not _send(host, drag).isAccepted()
