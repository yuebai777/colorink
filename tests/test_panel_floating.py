"""浮出面板（B-5）：把一块拆成独立窗口，再收回来。

浮出**不进停靠树** —— 树仍然记着这块面板的家在哪，宿主只是暂时不挂它。
这样收回来的时候不用猜位置，也不用在树里表达"不在任何容器里"。
"""

import pytest
from PyQt6.QtWidgets import QLabel

from ui.panels import registry, store
from ui.panels import tree as dock
from ui.panels.host import PanelHost

from .test_ringless_preview_support import qapp  # noqa: F401

RGB = registry.slider_panel_id("RGB")
HSV = registry.slider_panel_id("HSV")
HSL = registry.slider_panel_id("HSL")


def column(*panel_ids):
    return dock.Split(dock.VERTICAL, tuple(dock.Leaf(p) for p in panel_ids),
                      (), False)


# ── 存档 ─────────────────────────────────────────────────────────────────

def test_floating_geometry_round_trips():
    cfg = {}
    store.save_floating_into(cfg, {RGB: store.FloatingState((10, 20, 200, 120))})
    assert store.load_floating_from(cfg)[RGB].rect == (10, 20, 200, 120)
    assert store.load_floating_from(cfg)[RGB].on_top is True, "默认置顶"


def test_no_record_means_nothing_is_floating():
    assert store.load_floating_from({}) == {}
    assert store.load_floating_from(None) == {}
    assert store.load_floating_from({store.FLOATING_KEY: "垃圾"}) == {}


def test_a_panel_this_build_dropped_is_forgotten():
    cfg = {store.FLOATING_KEY: {"no-such-panel": [0, 0, 100, 100]}}
    assert store.load_floating_from(cfg) == {}


@pytest.mark.parametrize("broken", [
    [0, 0, 100], [0, 0, 100, 0], [0, 0, -5, 100], "nope", {"x": 1},
    [0, 0, "100", 100],
])
def test_broken_geometry_is_dropped(broken):
    cfg = {store.FLOATING_KEY: {RGB: broken}}
    assert store.load_floating_from(cfg) == {}


def test_clearing_floating_removes_the_key():
    cfg = {}
    store.save_floating_into(cfg, {RGB: store.FloatingState((1, 2, 3, 4))})
    store.save_floating_into(cfg, {})
    assert store.FLOATING_KEY not in cfg


# ── 宿主 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def host(qapp):
    made = {}
    asked = []

    def provider(panel_id):
        asked.append(panel_id)
        made.setdefault(panel_id, QLabel(panel_id))
        return made[panel_id]

    host = PanelHost(provider)
    host.set_tree(column(RGB, HSV, HSL))
    host._asked = asked
    return host


def test_a_floating_panel_is_not_mounted(host):
    host.set_floating_panels({HSV})
    assert host.mounted_panels() == (RGB, HSL)
    assert host.widget_for(HSV) is None


def test_the_provider_is_not_even_asked_for_a_floating_panel(host):
    host.set_floating_panels({HSV})
    host._asked.clear()
    host.set_tree(column(RGB, HSV, HSL))
    assert HSV not in host._asked, "浮出的面板归浮窗管，宿主不该去要它"


def test_docking_back_puts_it_where_the_tree_says(host):
    host.set_floating_panels({HSV})
    host.set_floating_panels(set())
    assert host.mounted_panels() == (RGB, HSV, HSL)


def test_the_tree_still_remembers_a_floating_panel(host):
    """树是"家"的记录，浮出只是暂时不在家。"""
    host.set_floating_panels({HSV})
    assert HSV in host.tree().panels()


def test_setting_the_same_floating_set_twice_does_not_remount(host):
    host.set_floating_panels({HSV})
    widget = host.widget_for(RGB)
    host._asked.clear()
    host.set_floating_panels({HSV})
    assert host._asked == []
    assert host.widget_for(RGB) is widget


def test_floating_every_panel_leaves_an_empty_host(host):
    host.set_floating_panels({RGB, HSV, HSL})
    assert host.mounted_panels() == ()
    host.set_floating_panels(set())
    assert host.mounted_panels() == (RGB, HSV, HSL)

# ── 抓手：拖到窗口外 = 浮出 ───────────────────────────────────────────────

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.panels.drag import PanelTitleBar  # noqa: E402
from ui.panels.floating import FloatingPanelWindow  # noqa: E402


def _press(widget, local, button=Qt.MouseButton.LeftButton):
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local),
                        QPointF(widget.mapToGlobal(local)), button, button,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)
    return event


def test_a_drop_nobody_accepted_asks_to_float(qapp):
    bar = PanelTitleBar(RGB, "RGB")
    seen = []
    bar.float_requested.connect(seen.append)
    bar._finish_reorder_drag(Qt.DropAction.IgnoreAction)
    assert seen == [RGB]


def test_a_drop_the_host_took_does_not_float(qapp):
    bar = PanelTitleBar(RGB, "RGB")
    seen = []
    bar.float_requested.connect(seen.append)
    bar._finish_reorder_drag(Qt.DropAction.MoveAction)
    assert seen == []


def test_the_close_button_only_exists_where_it_makes_sense(qapp):
    assert PanelTitleBar(RGB, "RGB").close_rect().isEmpty()
    assert not PanelTitleBar(RGB, "RGB", closable=True).close_rect().isEmpty()


def test_clicking_the_close_button_asks_to_dock_back(qapp):
    bar = PanelTitleBar(RGB, "RGB", closable=True)
    bar.resize(120, PanelTitleBar.HEIGHT)
    seen = []
    bar.close_requested.connect(seen.append)
    _press(bar, bar.close_rect().center())
    assert seen == [RGB]


def test_pressing_beside_the_close_button_does_not_close(qapp):
    bar = PanelTitleBar(RGB, "RGB", closable=True)
    bar.resize(120, PanelTitleBar.HEIGHT)
    seen = []
    bar.close_requested.connect(seen.append)
    _press(bar, QPoint(4, 4))
    assert seen == []


# ── 浮窗 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def floater(qapp):
    panel = QLabel("RGB 滑块")
    window = FloatingPanelWindow(RGB, "RGB")
    window.set_panel(panel)
    window.setGeometry(120, 60, 240, 90)
    return window, panel


def test_the_floating_window_carries_the_panel(floater):
    window, panel = floater
    assert window.panel() is panel
    assert panel.window() is window


def test_it_floats_above_and_frames_itself(floater):
    window, _ = floater
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.Tool


def test_no_focus_mode_is_inherited_when_asked(qapp):
    window = FloatingPanelWindow(RGB, "RGB", no_focus=True)
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_closing_asks_to_dock_back(floater):
    window, _ = floater
    seen = []
    window.dock_requested.connect(seen.append)
    window.title_bar.close_requested.emit(RGB)
    assert seen == [RGB]


def test_handing_the_panel_back_leaves_the_window_empty(floater):
    window, panel = floater
    assert window.take_panel() is panel
    assert window.panel() is None
    assert window.take_panel() is None


def test_the_geometry_is_recordable(floater):
    window, _ = floater
    assert window.geometry_record() == (120, 60, 240, 90)


def test_a_hidden_panel_hides_its_window(floater):
    """模块切换把这组滑块藏起来时，不能留一个空浮窗挂在屏幕上。"""
    window, panel = floater
    panel.setVisible(False)
    assert window.isHidden() is True
    panel.setVisible(True)
    assert window.isHidden() is False

# ── 主窗口这边：浮出 / 收回 / 重启还原 ───────────────────────────────────

from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QWidget  # noqa: E402

from core import config as core_config  # noqa: E402
from ui.window.floating_mixin import MIN_FLOATING_SIZE, FloatingPanelsMixin  # noqa: E402
from ui.window.panels_mixin import PanelProviderMixin  # noqa: E402


class _Window(PanelProviderMixin, FloatingPanelsMixin, QWidget):
    """够用的主窗口替身：有 cfg、有面板控件、有一个宿主。"""

    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg if cfg is not None else {}
        self.stack = QWidget(self)
        self.lab_slider_column = QWidget(self)
        self.preview_box = QWidget(self)
        self.color_history = QWidget(self)
        from core import config as _config
        self.slider_containers = {group: QLabel(group, self)
                                  for group in _config.SLIDER_GROUPS}
        self.panel_host = PanelHost(self.panel_provider(), self)
        self.panel_host.set_tree(column(RGB, HSV, HSL))
        self.panel_host.float_requested.connect(self.float_panel)


def _lay_out(widget):
    """逐层激活布局：离屏时 setGeometry 只是 post 一个 resize 事件。"""
    for _ in range(3):
        layout = widget.layout()
        if layout is not None:
            layout.activate()
        for child in widget.findChildren(QWidget):
            inner = child.layout()
            if inner is not None:
                inner.activate()


@pytest.fixture
def window(qapp, monkeypatch):
    saved = []
    monkeypatch.setattr(core_config, "save_hotkey_config", saved.append)
    win = _Window()
    win._saves = saved
    yield win
    for panel_id in list(win.floating_windows()):
        win.dock_panel(panel_id)


def test_floating_takes_the_panel_out_of_the_host(window):
    assert window.float_panel(HSV) is True
    assert window.panel_host.mounted_panels() == (RGB, HSL)
    assert window.floating_windows()[HSV].panel() is window.panel_widget(HSV)


def test_floating_opens_exactly_one_window(window):
    """浮出只该多一个窗口。

    踩过：面板被浮窗端走后，原来的停靠框仍然订阅着它的显隐，于是控件一
    显示，那个**已经没有父控件**的框就把自己也显示出来 —— 屏幕上凭空多出
    一两个空窗口。
    """
    window.panel_host.set_drag_enabled(True)
    before = {id(w) for w in QApplication.topLevelWidgets() if not w.isHidden()}

    window.float_panel(HSV)

    fresh = [w for w in QApplication.topLevelWidgets()
             if not w.isHidden() and id(w) not in before]
    assert [type(w).__name__ for w in fresh] == ["FloatingPanelWindow"]


def test_the_old_holder_lets_go_when_another_one_adopts(window):
    window.panel_host.set_drag_enabled(True)
    frame = window.panel_host.frame_for(HSV)
    window.float_panel(HSV)
    assert frame.panel() is None, "被端走之后就不该再声称自己端着它"


def test_the_panel_widget_is_not_rebuilt_on_the_way_out(window):
    widget = window.panel_widget(HSV)
    window.float_panel(HSV)
    assert window.floating_windows()[HSV].panel() is widget
    window.dock_panel(HSV)
    assert window.panel_widget(HSV) is widget


def test_docking_puts_it_back_where_it_lived(window):
    """收回来要回到原来的位置，不是排到队尾。"""
    window.float_panel(HSV)
    window.dock_panel(HSV)
    assert window.panel_host.mounted_panels() == (RGB, HSV, HSL)


def test_docking_back_really_puts_the_widget_on_screen(window):
    """回来的不能只是记录——控件得真的挂回去、真的看得见。

    抓手开着时，面板是被 PanelFrame 端着的；浮出会把控件从框里抽走，但框
    还记着它。收回来时若只按"还是同一个控件"短路，就会既不重新挂载也不显
    示：面板从此消失，用户再也调不出来。
    """
    window.panel_host.set_drag_enabled(True)
    widget = window.panel_widget(HSV)

    window.float_panel(HSV)
    window.dock_panel(HSV)

    assert window.panel_host.widget_for(HSV) is widget
    assert widget.parent() is not None, "控件没被挂回去"
    assert widget.isHidden() is False, "挂回去了但还是隐藏的"
    assert window.panel_host.frame_for(HSV).isHidden() is False


def test_the_grip_round_trip_survives_twice(window):
    """连着浮出/收回两轮也不能把面板弄丢。"""
    window.panel_host.set_drag_enabled(True)
    widget = window.panel_widget(HSV)
    for _ in range(2):
        window.float_panel(HSV)
        window.dock_panel(HSV)
    assert widget.parent() is not None
    assert widget.isHidden() is False


def test_floating_is_written_down(window):
    window.float_panel(HSV)
    window.floating_windows()[HSV].setGeometry(30, 40, 220, 100)
    window._save_floating_state()
    assert store.load_floating_from(window.cfg)[HSV].rect == (30, 40, 220, 100)
    window.dock_panel(HSV)
    assert store.load_floating_from(window.cfg) == {}


def test_restore_reopens_the_windows_where_they_were(qapp, monkeypatch):
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    cfg = {}
    store.save_floating_into(cfg, {HSV: store.FloatingState((55, 66, 210, 90))})
    win = _Window(cfg)
    win.restore_floating_panels()
    assert list(win.floating_windows()) == [HSV]
    assert win.floating_windows()[HSV].geometry_record() == (55, 66, 210, 90)
    assert win.panel_host.mounted_panels() == (RGB, HSL)
    win.dock_panel(HSV)


def test_dragging_a_floating_window_over_the_host_shows_where_it_would_land(window):
    """拖回来的时候要看得见落点，否则只能盲猜它会去哪。"""
    window.panel_host.setGeometry(0, 0, 200, 300)
    window.panel_host.set_drag_enabled(True)
    window.float_panel(HSV)
    _lay_out(window.panel_host)
    # 从真实几何取点：宿主现在是内容定高的，写死坐标会落到它外面
    box = window.panel_host.frame_for(RGB) or window.panel_host.widget_for(RGB)
    inside = box.mapToGlobal(QPoint(box.width() // 2, 2))
    window.floating_windows()[HSV].moving_at.emit(HSV, inside)
    assert window.panel_host.drop_hint_rect() is not None
    window.floating_windows()[HSV].moving_at.emit(HSV, QPoint(5000, 5000))
    assert window.panel_host.drop_hint_rect() is None


def test_dropping_it_on_a_neighbour_docks_it_there(window):
    """落在谁头上就插到谁前面 —— 和窗口内拖拽是同一套规则。"""
    window.panel_host.setGeometry(0, 0, 200, 300)
    window.float_panel(HSL)
    _lay_out(window.panel_host)
    rgb_box = window.panel_host.widget_for(RGB)
    on_rgb = rgb_box.mapToGlobal(QPoint(rgb_box.width() // 2, 1))
    window.floating_windows()[HSL].dropped_at.emit(HSL, on_rgb)
    assert HSL not in window.floating_windows()
    assert window.panel_host.tree().panels()[0] == HSL
    assert window.panel_widget(HSL).parent() is not None


def test_dropping_the_window_back_over_the_host_docks_it(window):
    window.panel_host.setGeometry(0, 0, 200, 300)
    window.float_panel(HSV)
    inside = window.panel_host.mapToGlobal(QRect(0, 0, 200, 300).center())
    window.floating_windows()[HSV].dropped_at.emit(HSV, inside)
    assert HSV not in window.floating_windows()
    assert window.panel_host.mounted_panels() == (RGB, HSV, HSL)


def test_dropping_it_somewhere_else_just_records_the_place(window):
    window.panel_host.setGeometry(0, 0, 200, 300)
    window.float_panel(HSV)
    window.floating_windows()[HSV].setGeometry(900, 900, 200, 80)
    window.floating_windows()[HSV].dropped_at.emit(HSV, QPoint(4000, 4000))
    assert HSV in window.floating_windows()
    assert store.load_floating_from(window.cfg)[HSV].rect == (900, 900, 200, 80)


def test_floating_the_same_panel_twice_changes_nothing(window):
    window.float_panel(HSV)
    first = window.floating_windows()[HSV]
    assert window.float_panel(HSV) is False
    assert window.floating_windows()[HSV] is first


def test_refusing_what_cannot_float(window):
    assert window.float_panel("no-such-panel") is False
    assert window.dock_panel(HSV) is False, "本来就没浮出，收什么"


def test_the_no_focus_setting_reaches_the_floating_window(window):
    window.cfg["noFocusMode"] = True
    window.float_panel(HSV)
    flags = window.floating_windows()[HSV].windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus


def test_toggling_no_focus_reaches_windows_that_are_already_out(window):
    window.float_panel(HSV)
    window.cfg["noFocusMode"] = True
    window.refresh_floating_focus()
    assert (window.floating_windows()[HSV].windowFlags()
            & Qt.WindowType.WindowDoesNotAcceptFocus)


def test_double_clicking_a_grip_floats_it(window):
    """双击抓手 = 拖出窗口，但没人猜得到要"拖到窗口外面"。"""
    window.panel_host.set_drag_enabled(True)
    window.panel_host.frame_for(HSV).title_bar.toggled.emit(HSV)
    assert HSV in window.floating_windows()


def test_double_clicking_a_floating_title_docks_it(window):
    window.float_panel(HSV)
    window.floating_windows()[HSV].title_bar.toggled.emit(HSV)
    assert HSV not in window.floating_windows()
    assert window.panel_widget(HSV).parent() is not None


def test_the_grip_asks_the_window_to_float_it(window):
    """抓手拖到窗口外 → 宿主转发 → 真的浮出来。"""
    window.panel_host.set_drag_enabled(True)
    bar = window.panel_host.frame_for(HSV).title_bar
    bar._finish_reorder_drag(Qt.DropAction.IgnoreAction)
    assert HSV in window.floating_windows()

# ── 浮窗要能调大小 ───────────────────────────────────────────────────────

from ui.panels.floating import (  # noqa: E402
    BORDER,
    resize_edge_at,
)


@pytest.mark.parametrize("point,expected", [
    ((2, 40), "left"),
    ((198, 40), "right"),
    ((100, 1), "top"),
    ((100, 99), "bottom"),
    ((1, 1), "topleft"),
    ((199, 1), "topright"),
    ((1, 99), "bottomleft"),
    ((199, 99), "bottomright"),
    ((100, 50), ""),
])
def test_the_border_says_which_way_it_resizes(point, expected):
    assert resize_edge_at(200, 100, *point) == expected


def test_a_point_outside_the_window_is_not_a_resize():
    assert resize_edge_at(200, 100, -3, 50) == ""
    assert resize_edge_at(200, 100, 250, 50) == ""


def test_dragging_the_right_border_widens_the_window(floater):
    window, _ = floater
    window.begin_resize("right", QPoint(360, 105))
    window.resize_to(QPoint(420, 105))
    assert window.geometry_record() == (120, 60, 300, 90)


def test_dragging_the_top_border_moves_the_top_edge_only(floater):
    window, _ = floater
    window.begin_resize("top", QPoint(240, 60))
    window.resize_to(QPoint(240, 40))
    assert window.geometry_record() == (120, 40, 240, 110)


def test_a_window_cannot_be_dragged_smaller_than_it_is_usable(floater):
    window, _ = floater
    window.begin_resize("bottomright", QPoint(360, 150))
    window.resize_to(QPoint(0, 0))
    _, _, width, height = window.geometry_record()
    assert (width, height) >= MIN_FLOATING_SIZE


def test_finishing_a_resize_is_worth_writing_down(floater):
    window, _ = floater
    seen = []
    window.geometry_changed.connect(seen.append)
    window.begin_resize("right", QPoint(360, 105))
    window.resize_to(QPoint(420, 105))
    window.end_resize()
    assert seen == [RGB]


# ── 置顶开关 ─────────────────────────────────────────────────────────────

def test_a_floating_window_starts_on_top(floater):
    window, _ = floater
    assert window.always_on_top() is True


def test_the_pin_button_toggles_the_layer(floater):
    window, _ = floater
    window.title_bar.pin_toggled.emit(RGB, False)
    assert window.always_on_top() is False
    assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    window.title_bar.pin_toggled.emit(RGB, True)
    assert window.always_on_top() is True
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_the_pin_button_sits_beside_the_close_button(qapp):
    bar = PanelTitleBar(RGB, "RGB", closable=True, pinnable=True)
    bar.resize(140, bar.height())
    assert not bar.pin_rect().isEmpty()
    assert not bar.pin_rect().intersects(bar.close_rect())


def test_clicking_the_pin_flips_it(qapp):
    bar = PanelTitleBar(RGB, "RGB", closable=True, pinnable=True)
    bar.resize(140, bar.height())
    seen = []
    bar.pin_toggled.connect(lambda pid, on: seen.append(on))
    _press(bar, bar.pin_rect().center())
    _press(bar, bar.pin_rect().center())
    assert seen == [False, True]


# ── 层级/几何都要活过重启 ────────────────────────────────────────────────

def test_the_pin_state_is_remembered(window):
    window.float_panel(HSV)
    window.floating_windows()[HSV].set_always_on_top(False)
    window._save_floating_state()
    assert store.load_floating_from(window.cfg)[HSV].on_top is False


def test_restoring_brings_back_the_pin_state(qapp, monkeypatch):
    """还原时"取消置顶"这一下不能反过来把存档覆盖掉。

    踩过：set_always_on_top 会发 geometry_changed，而它在窗口摆好之前就被
    接上了保存 —— 于是半成品的几何写回存档，重启后窗口缩成最小尺寸。
    """
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    cfg = {}
    store.save_floating_into(cfg, {HSV: store.FloatingState((10, 10, 260, 140), False)})
    win = _Window(cfg)
    win.restore_floating_panels()
    assert win.floating_windows()[HSV].always_on_top() is False
    assert win.floating_windows()[HSV].geometry_record() == (10, 10, 260, 140)
    assert store.load_floating_from(win.cfg)[HSV].rect == (10, 10, 260, 140)
    win.dock_panel(HSV)
