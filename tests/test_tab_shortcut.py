"""Local view shortcut over a tab stack — switch tabs without leaving the mouse.

One local shortcut (``toggleLabKey``, default Space) is dispatched by the
region under the cursor:

* over a panel tab stack  → select the next tab (wrapping past the last one)
* over the picker pane    → flip wheel ⇄ LAB (the original behaviour)

The picker half of that dispatch is covered by tests/test_tablet_cursor.py
(pen cursor) and the ringless suite; this module covers the tab half plus the
region rule both halves share.
"""

import os
from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QTabWidget,
    QWidget,
)

from ui.panels import registry
from ui.panels import tree as dock
from ui.panels.host import PanelHost
from ui.window.hotkey_mixin import HotkeyMixin
from ui.window.layout import LayoutMixin

RGB = registry.slider_panel_id("RGB")
HSV = registry.slider_panel_id("HSV")
HSL = registry.slider_panel_id("HSL")

# Disjoint corners of the (virtual) screen so the picker pane, the tab stack
# and the floating window never claim the same point — the zones are resolved
# by geometry.
HOST_RECT = (0, 0, 240, 360)
FLOAT_RECT = (4000, 4000, 240, 360)
WHEEL_RECT = (2000, 2000, 320, 320)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _tabbed_host(rect=HOST_RECT):
    """A shown host holding one tab strip with one page per panel."""
    made: dict = {}

    def provider(panel_id):
        return made.setdefault(panel_id, QLabel(panel_id))

    host = PanelHost(provider)
    host.set_tree(dock.Tabs((), 0, ((RGB,), (HSV,), (HSL,))))
    host.setGeometry(*rect)
    host.show()  # tab_widget_at only sees stacks that are visible
    QApplication.processEvents()
    return host, host._tabs[0][0]


def _inside(tabs: QTabWidget) -> QPoint:
    """A global point in the middle of the tab stack (page area)."""
    return tabs.mapToGlobal(tabs.rect().center())


def _strip(tabs: QTabWidget) -> QPoint:
    """A global point on the tab header strip itself."""
    bar = tabs.tabBar()
    return bar.mapToGlobal(bar.rect().center())


def _outside(tabs: QTabWidget) -> QPoint:
    """A global point well clear of the tab stack."""
    rect = tabs.rect()
    return tabs.mapToGlobal(QPoint(rect.right() + 400, rect.bottom() + 400))


def _key_event(key=Qt.Key.Key_Space, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


class _FakePointerEvent:
    """Mouse/tablet press stand-in: global position + pressed button."""

    def __init__(self, global_pos: QPoint, button: Qt.MouseButton):
        self._pos = QPointF(global_pos)
        self._button = button

    def globalPosition(self):
        return self._pos

    def button(self):
        return self._button


class _ShortcutWindow(LayoutMixin):
    """Minimal main-window stand-in for the local shortcut paths.

    Carries a real panel host (so the tab geometry is real) plus a real
    picker stack whose only page is the wheel — the same structure
    ``_is_lab_toggle_zone`` reads on the live window.
    """

    def __init__(self, host, wheel, hotkey="Space"):
        self.cfg = {"toggleLabKey": hotkey}
        self.panel_host = host
        self.stack = QStackedWidget()
        self.color_wheel = wheel
        self.stack.addWidget(wheel)
        self.stack.setGeometry(*WHEEL_RECT)
        self.stack.show()
        self.pane_lab = QWidget()
        self.floating_windows = lambda: {}
        self.picker_toggles = 0
        self._last_local_view_ts = 0.0
        QApplication.processEvents()

    def toggle_picker_mode(self):
        self.picker_toggles += 1


@pytest.fixture
def tabbed(qapp):
    host, tabs = _tabbed_host()
    yield host, tabs
    host.hide()
    host.deleteLater()


@pytest.fixture
def window(qapp):
    host, tabs = _tabbed_host()
    wheel = QWidget()
    win = _ShortcutWindow(host, wheel)
    yield win, host, tabs, wheel
    win.stack.hide()
    host.hide()
    host.deleteLater()
    win.stack.deleteLater()


# ── PanelHost.tab_widget_at: what counts as "the tab area" ──────────────────


def test_the_page_area_belongs_to_the_tab_zone(tabbed):
    """Cursor left the strip for the page below it — still the tab area."""
    host, tabs = tabbed
    assert host.tab_widget_at(_inside(tabs)) is tabs


def test_the_header_strip_belongs_to_the_tab_zone(tabbed):
    host, tabs = tabbed
    assert host.tab_widget_at(_strip(tabs)) is tabs


def test_a_point_outside_the_stack_is_not_a_tab_zone(tabbed):
    host, tabs = tabbed
    assert host.tab_widget_at(_outside(tabs)) is None


def test_a_lone_page_collapses_to_a_plain_column(qapp):
    """One page is not a tab strip at all — nothing to switch to."""
    made: dict = {}

    def provider(panel_id):
        return made.setdefault(panel_id, QLabel(panel_id))

    host = PanelHost(provider)
    host.set_tree(dock.Tabs((), 0, ((RGB,),)))
    try:
        assert host.findChildren(QTabWidget) == []
    finally:
        host.deleteLater()


def test_a_single_page_stack_claims_neither_key_nor_cursor(tabbed):
    host, _tabs = tabbed
    lone = QTabWidget()
    lone.addTab(QLabel("only"), "only")
    lone.setGeometry(600, 600, 160, 160)
    lone.show()
    host._tabs.append((lone, dock.Tabs((RGB,), 0)))
    try:
        assert lone.count() == 1
        assert host.tab_widget_at(lone.mapToGlobal(lone.rect().center())) is None
        assert host.advance_tab(lone) is False
    finally:
        lone.hide()
        lone.deleteLater()


# ── PanelHost.advance_tab: next page, wrapping ─────────────────────────────


def test_advance_tab_selects_the_next_page(tabbed):
    host, tabs = tabbed
    assert tabs.currentIndex() == 0
    assert host.advance_tab(tabs) is True
    assert tabs.currentIndex() == 1


def test_advance_tab_wraps_past_the_last_page(tabbed):
    host, tabs = tabbed
    for _ in range(tabs.count() - 1):
        host.advance_tab(tabs)
    assert tabs.currentIndex() == tabs.count() - 1
    assert host.advance_tab(tabs) is True
    assert tabs.currentIndex() == 0


def test_advance_tab_leaves_only_the_new_page_visible(tabbed):
    """The switch must land in the same frame, not after a layout pass."""
    host, tabs = tabbed
    host.advance_tab(tabs)
    for index in range(tabs.count()):
        assert tabs.widget(index).isVisible() is (index == tabs.currentIndex())


def test_advance_tab_reports_the_new_page_to_the_host(tabbed):
    """The host records the page, so a restart reopens the same one."""
    host, tabs = tabbed
    seen: list[int] = []
    host.tab_changed.connect(seen.append)
    host.advance_tab(tabs)
    assert seen == [1]


# ── Region dispatch: one key, the region under the cursor decides ───────────


def test_key_over_a_tab_stack_switches_tab(window):
    """The region rule reads the live cursor, so park it on the tab stack."""
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.layout.QCursor.pos", return_value=_inside(tabs)):
        assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is True
    assert tabs.currentIndex() == 1
    assert win.picker_toggles == 0, "标签页区域不该顺手切色轮/LAB"


def test_key_over_the_picker_pane_still_flips_wheel_and_lab(window):
    win, _host, tabs, wheel = window
    before = tabs.currentIndex()
    wheel_center = wheel.mapToGlobal(wheel.rect().center())
    with mock.patch("ui.window.layout.QCursor.pos", return_value=wheel_center):
        assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is True
    assert win.picker_toggles == 1
    assert tabs.currentIndex() == before


def test_key_outside_every_zone_is_consumed_without_acting(window):
    """Consumed on purpose: the key must not "click" a focused button."""
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.layout.QCursor.pos", return_value=QPoint(-5000, -5000)), \
         mock.patch("ui.window.layout.QApplication.widgetAt", return_value=None):
        assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is True
    assert tabs.currentIndex() == 0
    assert win.picker_toggles == 0


def test_a_text_field_keeps_the_key(window):
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.layout.QApplication.focusWidget",
                    return_value=QLineEdit()):
        assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is False
    assert tabs.currentIndex() == 0


def test_a_different_key_passes_through(window):
    win, _host, tabs, _wheel = window
    event = _key_event(Qt.Key.Key_J)
    assert LayoutMixin._maybe_handle_local_view_key(win, event) is False
    assert tabs.currentIndex() == 0


def test_an_unbound_shortcut_switches_nothing(window):
    """「无」hands the key back to the painting app rather than consuming it."""
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = ""
    assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is False
    assert tabs.currentIndex() == 0


# ── Mouse / pen presses ────────────────────────────────────────────────────


def test_mouse_button_over_a_tab_stack_switches_tab(window):
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = "MiddleButton"
    event = _FakePointerEvent(_inside(tabs), Qt.MouseButton.MiddleButton)
    assert LayoutMixin._maybe_handle_local_view_mouse(win, event) is True
    assert tabs.currentIndex() == 1


def test_mouse_button_outside_the_zones_passes_through(window):
    """A bound mouse button must stay the painting app's everywhere else."""
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = "MiddleButton"
    event = _FakePointerEvent(_outside(tabs), Qt.MouseButton.MiddleButton)
    assert LayoutMixin._maybe_handle_local_view_mouse(win, event) is False
    assert tabs.currentIndex() == 0


def test_a_non_matching_button_over_the_zone_passes_through(window):
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = "MiddleButton"
    event = _FakePointerEvent(_inside(tabs), Qt.MouseButton.RightButton)
    assert LayoutMixin._maybe_handle_local_view_mouse(win, event) is False
    assert tabs.currentIndex() == 0


def test_pen_button_over_a_tab_stack_switches_tab(window):
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = "RightButton"
    event = _FakePointerEvent(_inside(tabs), Qt.MouseButton.RightButton)
    assert LayoutMixin._maybe_handle_local_view_tablet(win, event) is True
    assert tabs.currentIndex() == 1


def test_pen_and_its_synthetic_mouse_twin_switch_only_once(window):
    """Drivers deliver a pen press twice; the pair must not double-switch."""
    win, _host, tabs, _wheel = window
    win.cfg["toggleLabKey"] = "RightButton"
    pos = _inside(tabs)
    tablet = _FakePointerEvent(pos, Qt.MouseButton.RightButton)
    mouse = _FakePointerEvent(pos, Qt.MouseButton.RightButton)
    assert LayoutMixin._maybe_handle_local_view_tablet(win, tablet) is True
    assert LayoutMixin._maybe_handle_local_view_mouse(win, mouse) is True
    assert tabs.currentIndex() == 1


def test_a_floating_panel_window_brings_its_own_tab_stack(window):
    """Tabs live in whichever host owns them — a floating window included."""
    win, _host, tabs, _wheel = window
    float_host, float_tabs = _tabbed_host(FLOAT_RECT)
    win.floating_windows = lambda: {"sliders.rgb":
                                    SimpleNamespace(panel_host=float_host)}
    try:
        with mock.patch("ui.window.layout.QCursor.pos",
                        return_value=_inside(float_tabs)):
            assert LayoutMixin._maybe_handle_local_view_key(win, _key_event()) is True
        assert float_tabs.currentIndex() == 1
        assert tabs.currentIndex() == 0, "不该顺手翻主窗口的标签页"
    finally:
        float_host.hide()
        float_host.deleteLater()


# ── No-focus hook path (painting app holds focus) ──────────────────────────


def test_no_focus_hook_switches_the_tab_under_the_cursor(window):
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.hotkey_mixin.QApplication.activeWindow",
                    return_value=None), \
         mock.patch("ui.window.layout.QCursor.pos", return_value=_inside(tabs)):
        HotkeyMixin.on_hotkey_triggered(win, "toggleLabKey")
    assert tabs.currentIndex() == 1
    assert win.picker_toggles == 0


def test_focused_window_leaves_the_key_to_the_qt_path(window):
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.hotkey_mixin.QApplication.activeWindow",
                    return_value=SimpleNamespace()):
        HotkeyMixin.on_hotkey_triggered(win, "toggleLabKey")
    assert tabs.currentIndex() == 0


def test_no_focus_hook_ignores_a_cursor_outside_every_zone(window):
    win, _host, tabs, _wheel = window
    with mock.patch("ui.window.hotkey_mixin.QApplication.activeWindow",
                    return_value=None), \
         mock.patch("ui.window.layout.QCursor.pos",
                    return_value=QPoint(-5000, -5000)), \
         mock.patch("ui.window.layout.QApplication.widgetAt", return_value=None):
        HotkeyMixin.on_hotkey_triggered(win, "toggleLabKey")
    assert tabs.currentIndex() == 0
    assert win.picker_toggles == 0
