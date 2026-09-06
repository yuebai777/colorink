"""Tests for multi-panel and stacked floating windows.

Verifies:
- Tabs and Split layouts inside floating windows
- Dragging floating windows into each other to merge
- Dragging panels between main window and floating windows
- Tearing panels out of multi-panel floating windows
- Docking individual panels vs closing entire multi-panel windows
- Configuration serialization and restoration across restarts
"""

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from core import config as core_config
from ui.panels import registry, store
from ui.panels import tree as dock
from ui.panels.floating import FloatingPanelWindow
from ui.panels.host import PanelHost
from ui.panels.rearrange import BOTTOM, CENTER, MERGE_PAGE, RIGHT
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.panels_mixin import PanelProviderMixin

from .test_ringless_preview_support import qapp  # noqa: F401

RGB = registry.slider_panel_id("RGB")
HSV = registry.slider_panel_id("HSV")
HSL = registry.slider_panel_id("HSL")


def column(*panel_ids):
    return dock.Split(dock.VERTICAL, tuple(dock.Leaf(p) for p in panel_ids),
                      (), False)


def _lay_out(widget):
    for _ in range(3):
        layout = widget.layout()
        if layout is not None:
            layout.activate()
        for child in widget.findChildren(QWidget):
            inner = child.layout()
            if inner is not None:
                inner.activate()


class _TestWindow(PanelProviderMixin, FloatingPanelsMixin, QWidget):

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


@pytest.fixture
def test_window(qapp, monkeypatch):
    saved = []
    monkeypatch.setattr(core_config, "save_hotkey_config", saved.append)
    win = _TestWindow()
    win._saves = saved
    yield win
    for panel_id in list(win.floating_windows()):
        win.dock_panel(panel_id)


def test_floating_window_can_hold_tabs(test_window):
    """Floating window can hold multiple panels in Tabs."""
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    hsv_widget = test_window.panel_widget(HSV)

    win.add_panel(HSV, hsv_widget, target_panel_id=RGB, zone=CENTER)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})

    assert set(win.panel_ids) == {RGB, HSV}
    assert isinstance(win.tree(), dock.Tabs)
    assert win.panel(RGB) is test_window.panel_widget(RGB)
    assert win.panel(HSV) is hsv_widget
    assert "RGB" in win.windowTitle() and "HSV" in win.windowTitle()


def test_floating_window_can_hold_split(test_window):
    """Floating window can hold multiple panels in a vertical Split."""
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    hsv_widget = test_window.panel_widget(HSV)

    win.add_panel(HSV, hsv_widget, target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})

    assert set(win.panel_ids) == {RGB, HSV}
    assert isinstance(win.tree(), dock.Split)
    assert win.tree().orientation == dock.VERTICAL


def test_merge_floating_windows_into_tabs(test_window):
    """Dragging one floating window onto another center drops into Tabs."""
    test_window.float_panel(RGB)
    test_window.float_panel(HSV)
    win_rgb = test_window.floating_windows()[RGB]
    win_hsv = test_window.floating_windows()[HSV]
    assert win_rgb is not win_hsv

    # Move win_hsv over win_rgb's center
    win_rgb.setGeometry(100, 100, 200, 200)
    center = win_rgb.mapToGlobal(QPoint(100, 100))
    win_rgb.set_allow_tab_drops(True)

    win_hsv.dropped_at.emit(HSV, center)

    # Now both point to win_rgb, and win_rgb holds Tabs
    windows = test_window.floating_windows()
    assert windows[RGB] is windows[HSV]
    assert isinstance(windows[RGB].tree(), dock.Tabs)
    assert set(windows[RGB].panel_ids) == {RGB, HSV}


def test_merge_floating_windows_into_split(test_window):
    """Dragging one floating window onto another's bottom edge splits vertically."""
    test_window.float_panel(RGB)
    test_window.float_panel(HSV)
    win_rgb = test_window.floating_windows()[RGB]
    win_hsv = test_window.floating_windows()[HSV]

    win_rgb.setGeometry(100, 100, 200, 200)
    # Bottom area of win_rgb
    bottom_pos = win_rgb.mapToGlobal(QPoint(100, 190))

    win_hsv.dropped_at.emit(HSV, bottom_pos)

    windows = test_window.floating_windows()
    assert windows[RGB] is windows[HSV]
    assert isinstance(windows[RGB].tree(), dock.Split)
    assert windows[RGB].tree().orientation == dock.VERTICAL
    assert set(windows[RGB].panel_ids) == {RGB, HSV}


def test_tear_panel_out_of_multipanel_floating_window(test_window):
    """A panel can be torn out of a multi-panel window into its own window."""
    test_window.float_panel(RGB)
    win_rgb = test_window.floating_windows()[RGB]
    win_rgb.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSV] = win_rgb
    test_window.panel_host.set_floating_panels({RGB, HSV})

    assert set(win_rgb.panel_ids) == {RGB, HSV}

    # Tear HSV out
    test_window._on_floating_panel_float_requested(HSV, win_rgb)

    windows = test_window.floating_windows()
    assert HSV in windows
    assert RGB in windows
    assert windows[HSV] is not windows[RGB]
    assert windows[RGB].panel_ids == (RGB,)
    assert windows[HSV].panel_ids == (HSV,)


def test_dock_single_panel_from_multipanel_floating_window(test_window):
    """Docking one panel from a multi-panel floating window leaves the other in the window."""
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    win.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=CENTER)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})

    # Dock only RGB
    assert test_window.dock_panel(RGB) is True

    # HSV is still floating in win
    assert RGB not in test_window.floating_windows()
    assert HSV in test_window.floating_windows()
    assert test_window.floating_windows()[HSV] is win
    assert win.panel_ids == (HSV,)
    assert win.isHidden() is False
    assert RGB in test_window.panel_host.mounted_panels()

    # Dock HSV
    assert test_window.dock_panel(HSV) is True
    assert HSV not in test_window.floating_windows()
    assert HSV in test_window.panel_host.mounted_panels()


def test_close_button_docks_all_panels_in_window(test_window):
    """Closing a multi-panel floating window docks all its panels."""
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    win.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})

    # Trigger close request
    win.title_bar.close_requested.emit(RGB)

    assert test_window.floating_windows() == {}
    assert set(test_window.panel_host.mounted_panels()) == {RGB, HSV, HSL}


def test_multipanel_persistence_and_restore(qapp, monkeypatch):
    """Multi-panel window survives restart: serialized and restored with same tree."""
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    cfg = {}
    win1 = _TestWindow(cfg)
    win1.float_panel(RGB)
    fwin = win1.floating_windows()[RGB]
    fwin.add_panel(HSV, win1.panel_widget(HSV), target_panel_id=RGB, zone=CENTER)
    win1.floating_windows()[HSV] = fwin
    fwin.setGeometry(120, 130, 240, 260)
    win1._save_floating_state()

    # Check store
    saved = store.load_floating_from(cfg)
    assert RGB in saved and HSV in saved
    assert saved[RGB].rect == (120, 130, 240, 260)
    assert saved[HSV].rect == (120, 130, 240, 260)
    assert isinstance(saved[RGB].tree, dock.Tabs)
    assert isinstance(saved[HSV].tree, dock.Tabs)

    # Restore in new window
    win2 = _TestWindow(cfg)
    win2.restore_floating_panels()

    windows = win2.floating_windows()
    assert RGB in windows and HSV in windows
    assert windows[RGB] is windows[HSV], "Both panels must live in the SAME floating window"
    assert set(windows[RGB].panel_ids) == {RGB, HSV}
    assert isinstance(windows[RGB].tree(), dock.Tabs)
    assert windows[RGB].geometry().x() == 120
    assert windows[RGB].geometry().y() == 130
    assert win2.panel_host.mounted_panels() == (HSL,)

    win2.dock_panel(RGB)
    win2.dock_panel(HSV)


def test_drop_hint_when_moving_over_another_floating_window(test_window):
    """Moving a floating window over another floating window shows drop hint on target."""
    test_window.float_panel(RGB)
    test_window.float_panel(HSV)
    win_rgb = test_window.floating_windows()[RGB]
    win_hsv = test_window.floating_windows()[HSV]

    win_rgb.setGeometry(100, 100, 200, 200)
    center = win_rgb.mapToGlobal(QPoint(100, 100))

    # Move over win_rgb
    win_hsv.moving_at.emit(HSV, center)
    assert win_rgb.drop_hint_rect() is not None
    assert test_window.panel_host.drop_hint_rect() is None

    # Move away
    win_hsv.moving_at.emit(HSV, QPoint(5000, 5000))
    assert win_rgb.drop_hint_rect() is None
    assert test_window.panel_host.drop_hint_rect() is None


def test_floating_window_gap_and_spacing_synchronization(test_window):
    """Floating window synchronizes panelTopGap and sliderDiffSpace with main window."""
    from ui.panels.floating import PanelChrome
    from PyQt6.QtWidgets import QSplitter

    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]
    fwin.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=BOTTOM)

    # Initial chrome with top_gap=15, diff_space=12
    chrome1 = PanelChrome(top_gap=15, diff_space=12, content_margins=(4, 6, 4, 6), grip_gap=4)
    fwin.apply_chrome(chrome1)

    # Margins on fwin.body should match top_gap
    margins = fwin.body.layout().contentsMargins()
    assert margins.top() == 15
    assert margins.bottom() == 15

    # Inter-panel stack spacing should match diff_space
    stacks = fwin.panel_host._stacks
    assert len(stacks) >= 1
    box = stacks[0][0].layout()
    assert box.spacing() == 12

    # Verify no QSplitter with default unstyled handle
    splitters = fwin.findChildren(QSplitter)
    assert len(splitters) == 0  # Vertical stacking uses QVBoxLayout stack, not QSplitter!

    # Change settings to top_gap=8, diff_space=20 and re-apply
    chrome2 = PanelChrome(top_gap=8, diff_space=20, content_margins=(4, 6, 4, 6), grip_gap=4)
    fwin.apply_chrome(chrome2)

    margins = fwin.body.layout().contentsMargins()
    assert margins.top() == 8
    assert margins.bottom() == 8
    assert box.spacing() == 20


def test_floating_window_minimum_size_limits(test_window):
    """Floating window enforces minimum width and height based on content and scale."""
    from ui.panels.floating import PanelChrome
    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]

    chrome = PanelChrome(top_gap=6, diff_space=8, scale=1.0)
    fwin.apply_chrome(chrome)

    min_w = fwin.minimumWidth()
    min_h = fwin.minimumHeight()
    assert min_w >= 200, f"Minimum width should be at least 200, got {min_w}"
    assert min_h > 50, f"Minimum height should accommodate content, got {min_h}"

    # Try resizing to tiny size (10x10) via border drag simulation
    fwin.begin_resize("bottomright", QPoint(fwin.geometry().right(), fwin.geometry().bottom()))
    fwin.resize_to(QPoint(fwin.geometry().left() + 10, fwin.geometry().top() + 10))
    fwin.end_resize()

    assert fwin.width() >= min_w, f"Width squashed below minimum: {fwin.width()} < {min_w}"
    assert fwin.height() >= min_h, f"Height squashed below minimum: {fwin.height()} < {min_h}"


def test_floating_window_tab_holds_multiple_panels_and_switches_all(test_window):
    """A single tab page can hold multiple panels; switching tabs switches all panels together."""
    from PyQt6.QtWidgets import QTabWidget

    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]

    # Add HSV as a tab (center drop creates tabs with RGB on page 0, HSV on page 1)
    fwin.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=CENTER)
    test_window.floating_windows()[HSV] = fwin

    # Add HSL to page 0 below RGB (zone=BOTTOM)
    fwin.add_panel(HSL, test_window.panel_widget(HSL), target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSL] = fwin

    tree = fwin.tree()
    assert isinstance(tree, dock.Tabs)
    assert tree.pages == ((RGB, HSL), (HSV,))

    tab_widget = fwin.findChildren(QTabWidget)[0]
    assert tab_widget.count() == 2

    # Switch to Tab 0: RGB and HSL must be visible; HSV must be hidden
    tab_widget.setCurrentIndex(0)
    assert fwin.panel(RGB).isVisibleTo(fwin) is True
    assert fwin.panel(HSL).isVisibleTo(fwin) is True
    assert fwin.panel(HSV).isVisibleTo(fwin) is False

    # Switch to Tab 1: RGB and HSL must both be hidden; HSV must be visible
    tab_widget.setCurrentIndex(1)
    assert fwin.panel(RGB).isVisibleTo(fwin) is False
    assert fwin.panel(HSL).isVisibleTo(fwin) is False
    assert fwin.panel(HSV).isVisibleTo(fwin) is True


def test_dropping_center_on_multipanel_column_creates_unified_tabs(test_window):
    """Dropping CENTER on a multi-panel column converts the whole column into Tab 0."""
    from PyQt6.QtWidgets import QTabWidget

    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]

    # Create a column of RGB and HSV
    fwin.add_panel(HSV, test_window.panel_widget(HSV), target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSV] = fwin
    assert isinstance(fwin.tree(), dock.Split)

    # Drop HSL on center of RGB -> The whole column [RGB, HSV] becomes Tab 0, HSL becomes Tab 1!
    fwin.add_panel(HSL, test_window.panel_widget(HSL), target_panel_id=RGB, zone=CENTER)
    test_window.floating_windows()[HSL] = fwin

    tree = fwin.tree()
    assert isinstance(tree, dock.Tabs), f"Expected Tabs as root, got {type(tree)}"
    assert tree.pages == ((RGB, HSV), (HSL,))

    tab_widget = fwin.findChildren(QTabWidget)[0]
    assert tab_widget.count() == 2

    # Tab 0 has both RGB and HSV; Tab 1 has HSL
    tab_widget.setCurrentIndex(0)
    assert fwin.panel(RGB).isVisibleTo(fwin) is True
    assert fwin.panel(HSV).isVisibleTo(fwin) is True
    assert fwin.panel(HSL).isVisibleTo(fwin) is False

    # Switch to Tab 1: Both RGB and HSV switch away!
    tab_widget.setCurrentIndex(1)
    assert fwin.panel(RGB).isVisibleTo(fwin) is False
    assert fwin.panel(HSV).isVisibleTo(fwin) is False
    assert fwin.panel(HSL).isVisibleTo(fwin) is True


def test_refresh_floating_panels_settings_syncs_tab_and_drag_mode(test_window):
    """When panelDrag or slidersTabs changes after windows are already floated, settings sync to all floaters."""
    test_window.cfg["panelDrag"] = False
    test_window.cfg["slidersTabs"] = False

    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]
    fwin.show()
    fwin.setGeometry(100, 100, 400, 400)
    _lay_out(fwin)
    assert getattr(fwin, "_drag_enabled_pref", None) is False
    assert getattr(fwin, "_allow_tab_drops", None) is False

    # Now turn both settings on
    test_window.cfg["panelDrag"] = True
    test_window.cfg["slidersTabs"] = True
    test_window.refresh_floating_panels_settings()

    assert getattr(fwin, "_drag_enabled_pref", None) is True
    assert getattr(fwin, "_allow_tab_drops", None) is True
    assert getattr(fwin.panel_host, "_allow_tab_drops", None) is True
    # Settings can change what is on screen: a manually enlarged float window
    # must not keep a ghost band after the refresh fits it to content.
    assert fwin.height() < 400, fwin.height()


def test_drop_floating_panel_onto_empty_main_window(test_window):
    """When all panels are floating (main window host is empty), dropping a floater onto main window docks it back."""
    test_window.resize(300, 500)
    test_window.show()

    # Float RGB and HSV
    test_window.float_panel(RGB)
    test_window.float_panel(HSV)
    test_window.panel_host.set_floating_panels({RGB, HSV, HSL})

    # Host has no mounted panels
    assert len(test_window.panel_host.mounted_panels()) == 0

    # User drags RGB floating window over the main window
    main_center = test_window.mapToGlobal(test_window.rect().center())
    test_window._floating_dropped(RGB, main_center)

    # RGB should be docked back home!
    assert RGB not in test_window.floating_windows()
    assert RGB in test_window.panel_host.mounted_panels()


def test_floating_panel_drop_center_creates_tabs_after_setting_changed(test_window):
    """Enabling slidersTabs after floating windows are already out allows them to be combined into tabs."""
    test_window.cfg["slidersTabs"] = False
    test_window.float_panel(RGB)
    test_window.float_panel(HSV)

    f_rgb = test_window.floating_windows()[RGB]
    f_hsv = test_window.floating_windows()[HSV]
    f_hsv.setGeometry(100, 100, 200, 150)
    f_hsv.show()

    # Enable slidersTabs and refresh settings
    test_window.cfg["slidersTabs"] = True
    test_window.refresh_floating_panels_settings()

    # Drop RGB on center of HSV
    center_pos = f_hsv.rect().center()
    target = f_hsv.drop_target_at(center_pos)
    assert target is not None and target[1] == CENTER

    test_window._floating_dropped(RGB, f_hsv.mapToGlobal(center_pos))
    assert test_window.floating_windows()[RGB] is test_window.floating_windows()[HSV]
    tree = test_window.floating_windows()[HSV].tree()
    assert isinstance(tree, dock.Tabs)


def test_drop_into_floating_defers_widget_surgery_until_after_drag(qapp, test_window):
    """拖进浮窗的掉落在 QDrag 未结束前不得删除/重挂源面板。

    踩过：第三个组件拖进同一个浮窗时，dropEvent 同步删掉了主窗里的源抓杆
    （QDrag 仍认为那是拖拽源），进程直接原生崩溃。现在只先改浮窗映射，
    所有破坏性控件操作延到 drag 循环结束后执行。
    """
    test_window.float_panel(RGB)
    fwin = test_window.floating_windows()[RGB]
    assert HSV in test_window.panel_host.mounted_panels()

    test_window._on_panel_dropped_into_floating(HSV, (RGB, BOTTOM), fwin)

    # 映射已更新，但控件还留在主窗（源抓杆在 drag 期间不被删）
    assert test_window.floating_windows()[HSV] is fwin
    assert HSV in test_window.panel_host.mounted_panels()

    qapp.processEvents()

    assert HSV not in test_window.panel_host.mounted_panels()
    assert HSV in fwin.panel_ids
    assert RGB in fwin.panel_ids


# ── 浮窗内容贴合：移出面板后不得留下空白占位 ─────────────────────────────

class _SizedPanel(QWidget):
    """A panel with a controllable sizeHint, so page heights can differ."""

    def __init__(self, height: int):
        super().__init__()
        self._height = height

    def sizeHint(self):
        return QSize(100, self._height)


def _multipanel_split_window(test_window, tall=None, short=None):
    """Float RGB, then drop HSV into the same window as a vertical stack."""
    if tall is not None:
        test_window.slider_containers["RGB"] = _SizedPanel(tall)
    if short is not None:
        test_window.slider_containers["HSV"] = _SizedPanel(short)
    test_window.cfg["panelDrag"] = True
    test_window.panel_host.set_drag_enabled(True)
    test_window.panel_host.set_tree(column(RGB, HSV, HSL))
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    win.add_panel(HSV, test_window.panel_widget(HSV),
                  target_panel_id=RGB, zone=BOTTOM)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})
    win.show()
    win.setGeometry(100, 100, 400, 400)
    _lay_out(win)
    return win


def test_tearing_a_panel_out_shrinks_the_source_window(test_window):
    """拖出多面板浮窗中的一个组件后，源窗口要贴着剩余内容，不留空白带。"""
    win = _multipanel_split_window(test_window)
    big = win.height()

    test_window._on_floating_panel_float_requested(HSV, win)
    _lay_out(win)

    assert win.panel_ids == (RGB,)
    assert win.height() < big
    assert win.height() < 100, win.height()
    assert all(f.panel() is not None for f in win.panel_host._frames.values())


def test_docking_one_panel_out_shrinks_the_source_window(test_window):
    """从多面板浮窗收回一个组件后，剩下的面板窗口也要收缩。"""
    win = _multipanel_split_window(test_window)
    big = win.height()

    assert test_window.dock_panel(RGB) is True
    _lay_out(win)

    assert win.panel_ids == (HSV,)
    assert win.height() < big
    assert win.height() < 100, win.height()


def test_removing_a_panel_from_tabbed_floating_window_shrinks_it(test_window):
    """堆叠（页签）浮窗移出一个组件后，窗口收缩到剩余页的最大高度。"""
    test_window.cfg["panelDrag"] = True
    test_window.panel_host.set_drag_enabled(True)
    test_window.panel_host.set_tree(column(RGB, HSV, HSL))
    test_window.slider_containers["RGB"] = _SizedPanel(120)
    test_window.slider_containers["HSV"] = _SizedPanel(40)
    test_window.float_panel(RGB)
    win = test_window.floating_windows()[RGB]
    win.add_panel(HSV, test_window.panel_widget(HSV),
                  target_panel_id=RGB, zone=CENTER)
    test_window.floating_windows()[HSV] = win
    test_window.panel_host.set_floating_panels({RGB, HSV})
    win.show()
    win.setGeometry(100, 100, 400, 400)
    _lay_out(win)
    big = win.height()

    test_window._on_floating_panel_float_requested(HSV, win)
    _lay_out(win)

    assert win.panel_ids == (RGB,)
    assert win.height() < big
    # 只剩一页（120px 内容 + 标题栏/边框），窗口不能还是 400px。
    assert win.height() < 240, win.height()


def test_restored_multipanel_window_fits_content(qapp, monkeypatch):
    """恢复的多面板浮窗若存档尺寸大于内容，也要贴住内容。"""
    monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
    cfg = {}
    win1 = _TestWindow(cfg)
    win1.float_panel(RGB)
    fwin = win1.floating_windows()[RGB]
    fwin.add_panel(HSV, win1.panel_widget(HSV),
                   target_panel_id=RGB, zone=CENTER)
    win1.floating_windows()[HSV] = fwin
    fwin.setGeometry(120, 130, 400, 400)
    win1._save_floating_state()

    win2 = _TestWindow(cfg)
    win2.restore_floating_panels()

    fw = win2.floating_windows()[RGB]
    assert fw is win2.floating_windows()[HSV]
    _lay_out(fw)
    assert fw.width() <= 400
    assert fw.height() < 400, fw.height()
    assert fw.height() < 100, fw.height()




