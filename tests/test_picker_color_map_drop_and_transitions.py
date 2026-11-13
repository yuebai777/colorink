"""Tests for Color Map drop targets (sliders/wheels), anti-flicker reveals, and transitions."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from PyQt6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QWindow
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from ui.panels import rearrange, registry
from ui.panels.drag import PANEL_MIME, DropIndicator, PanelFrame, PanelTitleBar
from ui.panels.floating import FloatingPanelWindow
from ui.panels.host import PanelHost
from ui.panels.transitions import (
    _ACTIVE_GHOSTS,
    animate_panel_landing,
    animations_enabled,
    capture_widget_snapshot,
    capture_window_snapshot,
    consume_tear_handoff,
    finish_tear_reveal,
    set_animations_enabled_for_test,
    spawn_dock_ghost,
    spawn_tear_ghost,
    start_drag_tear_preview,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_drop_indicator_zone_labels_and_smooth_interpolation(qapp):
    parent = QWidget()
    parent.resize(320, 320)
    parent.show()
    ind = DropIndicator(parent)

    set_animations_enabled_for_test(True)
    try:
        assert animations_enabled() is True
        r1 = QRect(10, 10, 120, 80)
        r2 = QRect(40, 180, 200, 100)
        ind.show_drop(r1, zone=rearrange.TOP)
        assert ind.isVisible()
        assert ind.target_rect() == r1
        assert ind._label == "放在上方"

        ind.show_drop(r2, zone=rearrange.CENTER)
        assert ind.target_rect() == r2
        assert ind._label == "堆叠"

        ind.show_drop(r2, zone=rearrange.MERGE_PAGE)
        assert ind._label == "合并标签页"

        ind.show_drop(r1, zone=rearrange.LEFT)
        assert ind._label == "放在左侧"

        ind.show_drop(r1, zone=rearrange.RIGHT)
        assert ind._label == "放在右侧"

        ind.show_drop(r1, zone=rearrange.BOTTOM)
        assert ind._label == "放在下方"

        ind.hide()
        assert ind.isHidden()
    finally:
        set_animations_enabled_for_test(None)


def test_transitions_reveal_and_dock_ghost_cleanup(qapp):
    set_animations_enabled_for_test(True)
    try:
        win = FloatingPanelWindow("sliders.oklab", title="OKLab")
        panel = QLabel("OKLab content")
        panel.setFixedSize(220, 90)
        panel.setProperty("panelId", "sliders.oklab")

        show_calls: list[bool] = []
        orig_set_visible = win.setVisible

        def _spy_set_visible(vis: bool):
            show_calls.append(bool(vis))
            orig_set_visible(vis)

        win.setVisible = _spy_set_visible
        with win.batch_setup():
            win.setGeometry(100, 100, 236, 130)
            win.set_panel(panel)
            win.adjust_size_for_content()

        assert True not in show_calls, f"batch_setup leaked premature show: {show_calls}"

        win.reveal_animated()
        assert show_calls.count(True) == 1, show_calls
        assert win.isVisible()

        win.set_drag_dimmed(True)
        assert win._drag_dimmed is True
        win.set_drag_dimmed(False)
        assert win._drag_dimmed is False

        snap = capture_window_snapshot(win)
        assert snap is not None
        pixmap, snap_rect = snap
        assert not pixmap.isNull() and snap_rect.isValid()

        spawn_dock_ghost(snap, QRect(220, 260, 180, 90), duration_ms=40)
        from ui.panels import transitions as _tr
        assert len(_tr._ACTIVE_GHOSTS) >= 1
        for _ in range(15):
            qapp.processEvents()
        win.close()
        win.deleteLater()
    finally:
        set_animations_enabled_for_test(None)


_SUBPROCESS_SCRIPT = r"""
import copy, os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT')
from unittest.mock import patch
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QApplication
from core import config
import ui.main_window as main_window

app = QApplication(sys.argv)
base_cfg = config.load_hotkey_config()
base_cfg.update({
    'onlyShowInCsp': False,
    'panelDrag': True,
    'slidersTabs': True,
    'colorSpaceModule': 'hsv',
    'floatingPanels': {},
    'panelLayout': None,
    'pickerDocks': {},
    'floatingWheelPageOptions': {},
    'wheelModuleTabs': ['hsv', 'vhsv', 'hls', 'rgb', 'lch'],
    'wheelModuleOrder': ['hsv', 'vhsv', 'hls', 'rgb', 'lch'],
    'showSlidersRGB': True,
    'showSlidersHSL': True,
    'showSlidersLAB': True,
})
saved_snapshots = []

def _save_cfg(cfg):
    saved_snapshots.append(copy.deepcopy(cfg))

def _send_panel_drag_and_drop(target_widget, local_pos: QPoint, panel_id: str, indicator_widget_getter):
    mime = QMimeData()
    mime.setData('application/x-colorink-panel', panel_id.encode('utf-8'))
    pos_f = QPointF(float(local_pos.x()), float(local_pos.y()))
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    ev_enter = QDragEnterEvent(local_pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target_widget, ev_enter)
    app.processEvents()

    ev_move = QDragMoveEvent(local_pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target_widget, ev_move)
    app.processEvents()

    ind = indicator_widget_getter()
    assert ind is not None and ind.isVisible(), f"Expected DropIndicator visible while hovering {panel_id} at {local_pos}"

    ev_drop = QDropEvent(pos_f, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target_widget, ev_drop)
    assert ev_drop.isAccepted(), f"Expected drop accepted for {panel_id} at {local_pos}"
    for _ in range(5):
        app.processEvents()

with patch('core.config.load_hotkey_config', side_effect=lambda: copy.deepcopy(base_cfg)), \
     patch('core.config.save_hotkey_config', side_effect=_save_cfg), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):

    win = main_window.MainWindow()
    win.show()
    app.processEvents()
    win.apply_theme()
    app.processEvents()
    pdrop = win.picker_drop

    # 1. Native layout preserved when no Color Map edge/center docks exist
    assert not pdrop.has_any_docks()
    assert win.stack.parentWidget() is win.central

    # 2. Hidden color wheel page tearoff uses win.stack size (never 640x480 / 656x520)
    #    and reveals cleanly in a single show() after content setup completes.
    from PyQt6.QtWidgets import QWidget as _QW
    from ui.panels.floating import FloatingPanelWindow
    show_sizes = []
    def _spy_set_visible(self, visible):
        if visible:
            show_sizes.append((self.panel_id, self.width(), self.height()))
        _QW.setVisible(self, visible)
    FloatingPanelWindow.setVisible = _spy_set_visible
    try:
        assert win.float_wheel_module('hls')
        for _ in range(4):
            app.processEvents()
    finally:
        del FloatingPanelWindow.setVisible

    hls_win = win.floating_windows()['wheel.hls']
    hls_shows = [sz for pid, w, h in show_sizes if pid == 'wheel.hls' for sz in [(w, h)]]
    assert len(hls_shows) == 1, f"Expected single reveal for wheel.hls, got {hls_shows}"
    expected_side = win.stack.width()
    assert abs(hls_shows[0][0] - (expected_side + 16)) <= 40, (hls_shows[0], expected_side)
    assert hls_win.width() < 560 and hls_win.height() < 560, hls_win.size()

    # Drag floating wheel.hls back onto Color Map (preview + dim + dock)
    stack_center_g = win.stack.mapToGlobal(QPoint(win.stack.width() // 2, win.stack.height() // 2))
    win._floating_moving('wheel.hls', stack_center_g)
    assert pdrop._indicator is not None and pdrop._indicator.isVisible()
    assert hls_win._drag_dimmed is True

    win._floating_moving('wheel.hls', QPoint(-600, -600))
    assert pdrop._indicator.isHidden()
    assert hls_win._drag_dimmed is False

    win._floating_moving('wheel.hls', stack_center_g)
    win._floating_dropped('wheel.hls', stack_center_g)
    app.processEvents()
    assert 'wheel.hls' not in win.floating_windows()
    assert 'hls' in win.picker_tab_bar.tab_keys()

    # Even with panelDrag=False, floating wheel returning onto Color Map still previews CENTER and docks cleanly!
    win.cfg['panelDrag'] = False
    win.refresh_slider_visibility_and_order()
    assert win.float_wheel_module('hls')
    app.processEvents()
    hls_win2 = win.floating_windows()['wheel.hls']
    win._floating_moving('wheel.hls', stack_center_g)
    assert pdrop._indicator is not None and pdrop._indicator.isVisible()
    win._floating_dropped('wheel.hls', stack_center_g)
    app.processEvents()
    assert 'wheel.hls' not in win.floating_windows()
    assert 'hls' in win.picker_tab_bar.tab_keys()

    # Re-enable panelDrag for Color Map edge/center drops
    win.cfg['panelDrag'] = True
    win.refresh_slider_visibility_and_order()
    win.apply_theme()
    app.processEvents()

    # 3. Real QDragEnter/QDragMove/QDrop onto Color Map zones: TOP, LEFT, RIGHT, CENTER, BOTTOM
    w, h = win.stack.width(), win.stack.height()

    # TOP edge
    _send_panel_drag_and_drop(win.stack, QPoint(w // 2, 12), 'sliders.oklab', lambda: pdrop._indicator)
    assert 'sliders.oklab' in pdrop.top_host.tree().panels()
    assert 'sliders.oklab' not in win.panel_host.tree().panels()
    assert pdrop.top_container.isVisible()

    # LEFT edge
    _send_panel_drag_and_drop(win.stack, QPoint(12, win.stack.height() // 2), 'sliders.hsv', lambda: pdrop._indicator)
    assert 'sliders.hsv' in pdrop.left_host.tree().panels()
    assert 'sliders.hsv' not in win.panel_host.tree().panels()
    assert pdrop.left_container.isVisible()
    if win.preview_box.isVisible():
        assert win.preview_box.x() >= pdrop.left_side_offset(), (win.preview_box.geometry(), pdrop.left_side_offset())

    # RIGHT edge
    _send_panel_drag_and_drop(win.stack, QPoint(win.stack.width() - 12, win.stack.height() // 2), 'sliders.hsl', lambda: pdrop._indicator)
    assert 'sliders.hsl' in pdrop.right_host.tree().panels()
    assert pdrop.right_container.isVisible()

    # CENTER tab-stack alongside Color Map ("色图")
    _send_panel_drag_and_drop(win.stack, QPoint(win.stack.width() // 2, win.stack.height() // 2), 'sliders.rgb', lambda: pdrop._indicator)
    assert 'sliders.rgb' in pdrop.center_host.tree().panels()
    assert pdrop.center_tab_bar.isVisible()
    assert pdrop.center_tab_bar.tabText(0) == '色图'
    assert pdrop.center_tab_bar.count() >= 2
    assert pdrop.center_tab_bar.currentIndex() == 1
    assert not pdrop.center_shows_picker()
    assert win.preview_box.isHidden(), "preview_box must hide when stacked slider page covers wheel"

    # Switch back to tab 0 ("色图") -> preview_box returns cleanly
    pdrop.center_tab_bar.setCurrentIndex(0)
    app.processEvents()
    assert pdrop.center_shows_picker()

    # BOTTOM -> top of primary slider column (win.panel_host)
    _send_panel_drag_and_drop(win.stack, QPoint(win.stack.width() // 2, win.stack.height() - 10), 'sliders.lab', lambda: pdrop._indicator)
    assert win.panel_host.tree().panels()[0] == 'sliders.lab', win.panel_host.tree().panels()

    # 4. Floating window dragged onto Color Map TOP merges into top_host
    assert win.float_panel('sliders.oklch')
    app.processEvents()
    oklch_win = win.floating_windows()['sliders.oklch']
    top_drop_g = win.stack.mapToGlobal(QPoint(win.stack.width() // 2, 12))
    win._floating_moving('sliders.oklch', top_drop_g)
    assert pdrop._indicator.isVisible()
    assert oklch_win._drag_dimmed is True
    win._floating_dropped('sliders.oklch', top_drop_g)
    app.processEvents()
    assert 'sliders.oklch' not in win.floating_windows()
    assert 'sliders.oklch' in pdrop.top_host.tree().panels()

    # 5. Persistence across restart: pickerDocks restores identically without duplicating into panel_host
    persisted_cfg = copy.deepcopy(win.cfg)
    assert 'pickerDocks' in persisted_cfg
    assert set(persisted_cfg['pickerDocks'].keys()) == {'top', 'left', 'right', 'center'}

    win.close()
    win2_cfg = persisted_cfg
    with patch('core.config.load_hotkey_config', return_value=copy.deepcopy(win2_cfg)):
        win2 = main_window.MainWindow()
        win2.show()
        app.processEvents()
        pdrop2 = win2.picker_drop
        assert 'sliders.oklab' in pdrop2.top_host.tree().panels()
        assert 'sliders.oklch' in pdrop2.top_host.tree().panels()
        assert 'sliders.hsv' in pdrop2.left_host.tree().panels()
        assert 'sliders.hsl' in pdrop2.right_host.tree().panels()
        assert 'sliders.rgb' in pdrop2.center_host.tree().panels()
        docked_set = set(pdrop2.top_host.tree().panels()) | set(pdrop2.left_host.tree().panels()) | set(pdrop2.right_host.tree().panels()) | set(pdrop2.center_host.tree().panels())
        assert not (docked_set & set(win2.panel_host.tree().panels())), "No duplicate panels across hosts after restart"

        # 6. Clearing all Color Map docks detaches workspace back cleanly to win2.central
        for pid in list(docked_set):
            pdrop2.remove_panel(pid)
        win2.refresh_slider_visibility_and_order()
        app.processEvents()
        assert not pdrop2.has_any_docks()
        assert win2.stack.parentWidget() is win2.central
        assert 'pickerDocks' not in win2.cfg

        # 7. Anti-flash invariant & landing animations across rearrange/stack/Color Map drop:
        # install a spy verifying zero stray top-level QWindow / PanelFrame shows.
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtGui import QWindow
        from ui.panels import rearrange, registry
        from ui.panels.drag import PanelFrame
        from ui.panels.transitions import set_animations_enabled_for_test

        class _FlashSpy(QObject):
            def __init__(self):
                super().__init__()
                self.bad = []
            def eventFilter(self, obj, ev):
                if ev.type() in (QEvent.Type.Show, QEvent.Type.ShowToParent):
                    if isinstance(obj, QWindow):
                        title = obj.title() or type(obj).__name__
                        if 'PanelFrame' in title:
                            self.bad.append(('QWin', title, obj.geometry().getRect()))
                    elif isinstance(obj, PanelFrame) and (obj.isWindow() or obj.parent() is None):
                        self.bad.append(('PanelFrame', getattr(obj, 'panel_id', ''), obj.geometry().getRect()))
                return False

        spy = _FlashSpy()
        app.installEventFilter(spy)
        set_animations_enabled_for_test(True)
        try:
            mounted = list(win2.panel_host.mounted_panels())
            assert len(mounted) >= 2
            box0 = win2.panel_host.frame_for(mounted[0])
            pt = win2.panel_host.mapFromGlobal(box0.mapToGlobal(QPoint(box0.width() // 2, box0.height() // 2)))
            win2.panel_host.apply_drop(mounted[1], pt)
            for _ in range(4):
                app.processEvents()
            landed_target = win2.panel_host._landing_target_widget(mounted[1])
            assert landed_target is not None
            assert getattr(landed_target, '_landing_overlay', None) is not None
            pdrop2.apply_panel_drop((mounted[0],), rearrange.TOP)
            for _ in range(4):
                app.processEvents()
            top_target = pdrop2.top_host._landing_target_widget(mounted[0])
            assert top_target is not None
            assert getattr(top_target, '_landing_overlay', None) is not None
            assert spy.bad == [], f"Unexpected top-level PanelFrame flash events: {spy.bad}"

            # Phase 3: Drag tear-out preview (real disposable FloatingPanelWindow) -> exact swap + wheel outward ghost
            from ui.panels.transitions import start_drag_tear_preview, _ACTIVE_GHOSTS
            scr_pg = app.primaryScreen().availableGeometry()
            win2.setMinimumWidth(0)
            win2.resize(300, 520)
            win2.move(scr_pg.x() + 16, scr_pg.y() + 16)
            app.processEvents()
            src_panel_id = mounted[0]
            src_widget = win2.panel_widget(src_panel_id)
            assert src_widget is not None
            sess = start_drag_tear_preview(
                src_panel_id,
                src_panel_id,
                src_widget,
                QPoint(32, 8),
                chrome=getattr(win2, '_floating_chrome', None),
                no_focus=True,
            )
            outside_pt = QPoint(win2.frameGeometry().right() + 48, scr_pg.y() + 60)
            sess.update_state(None, outside_pt, now=10.0)
            app.processEvents()
            assert sess.preview is not None and sess.preview.isVisible()
            assert isinstance(sess.preview.window, FloatingPanelWindow)
            assert sess.preview.window._preview_only is True
            assert sess.preview.window._chrome is getattr(win2, '_floating_chrome', None)
            preview_size = sess.preview.window.size()
            expected_origin = sess.preview.card_rect_for_cursor(outside_pt).topLeft()
            handoff = sess.finish(accepted=False, cursor_pos=outside_pt)
            assert handoff is not None and handoff.from_drag is True
            assert win2.float_panel(src_panel_id) is True
            app.processEvents()
            fwin = win2.floating_windows().get(src_panel_id)
            assert fwin is not None
            assert fwin is not handoff.preview.window
            assert fwin.pos() == expected_origin
            assert fwin.size() == preview_size
            assert handoff.preview._handed_off is True

            ghost_before = len(_ACTIVE_GHOSTS)
            assert win2.float_wheel_module('hls') is True
            app.processEvents()
            wheel_pid = registry.wheel_panel_id('hls')
            wheel_fwin = win2.floating_windows().get(wheel_pid)
            assert wheel_fwin is not None
            assert len(_ACTIVE_GHOSTS) > ghost_before or getattr(wheel_fwin.body, '_landing_overlay', None) is not None

            # Audit regressions:
            # (a) preview_box over Color Map accepts drops and clears hover without Blocking.
            assert win2.preview_box.acceptDrops() is True
            assert win2.preview_box.isVisible()
            before_mounted = list(win2.panel_host.mounted_panels())
            assert before_mounted, before_mounted
            preview_drop_pid = before_mounted[0]
            _send_panel_drag_and_drop(
                win2.preview_box,
                win2.preview_box.rect().center(),
                preview_drop_pid,
                lambda: pdrop2._indicator,
            )

            # (b) Split tree in center_host emits a single composite page tab (no dead extra tabs).
            from ui.panels import tree as _dock
            split_pids = [
                p for p in win2.panel_host.tree().panels()
                if p not in win2.floating_windows()
            ][:2]
            assert len(split_pids) == 2, split_pids
            pdrop2.center_host.set_tree(
                _dock.Split(_dock.VERTICAL, (_dock.Leaf(split_pids[0]), _dock.Leaf(split_pids[1])))
            )
            pdrop2._sync_center_tabs()
            app.processEvents()
            assert pdrop2.center_tab_bar.count() == 2, (
                f"Expected ['色图', composite] (2 tabs) for Split center_host, got {[pdrop2.center_tab_bar.tabText(i) for i in range(pdrop2.center_tab_bar.count())]}"
            )
            assert pdrop2.center_tab_bar.tabText(0) == '色图'
            assert '/' in pdrop2.center_tab_bar.tabText(1), pdrop2.center_tab_bar.tabText(1)
            pdrop2.center_tab_bar.setCurrentIndex(0)
            app.processEvents()

            # (c) float_lab_view consumes & dismisses TearHandoff when LAB window is already open.
            from ui.lab_float import LAB_FLOAT_ID
            from ui.panels.transitions import consume_tear_handoff
            assert win2.float_lab_view() is True
            app.processEvents()
            assert LAB_FLOAT_ID in win2.floating_windows()
            assert win2.stack.isVisible()
            outside_lab = QPoint(win2.frameGeometry().right() + 80, scr_pg.y() + 60)
            sess_lab = start_drag_tear_preview(
                LAB_FLOAT_ID,
                'LAB',
                win2.stack,
                QPoint(24, 8),
                chrome=getattr(win2, '_floating_chrome', None),
                no_focus=True,
            )
            sess_lab.update_state(None, outside_lab, now=20.0)
            app.processEvents()
            assert sess_lab.preview is not None and sess_lab.preview.isVisible()
            handoff_lab = sess_lab.finish(accepted=False, cursor_pos=outside_lab)
            assert handoff_lab is not None
            assert win2.float_lab_view() is False
            app.processEvents()
            assert consume_tear_handoff(LAB_FLOAT_ID) is None
            assert handoff_lab.preview._handed_off is True
        finally:
            set_animations_enabled_for_test(None)
            app.removeEventFilter(spy)
        win2.close()

        # (d) Switching slider tabs in panelDrag + slidersTabs mode must not change picker/wheel height.
        cfg_tabs = copy.deepcopy(base_cfg)
        cfg_tabs.update({
            'panelDrag': True,
            'slidersTabs': True,
            'hideHueRing': False,
            'showSlidersRGB': False,
            'showSlidersHSV': True,
            'showSlidersHSL': False,
            'showSlidersLAB': False,
            'showSlidersOKLAB': True,
            'showSlidersOKLCH': True,
            'showHistory': True,
            'floatingPanels': {},
            'pickerDocks': {},
        })
        from ui.panels import store as _store
        _store.save_into(
            cfg_tabs,
            _dock.Tabs(
                pages=(('sliders.oklch', 'history'), ('sliders.oklab', 'sliders.hsv')),
                current=0,
            ),
            'tabs',
        )
        with patch('core.config.load_hotkey_config', side_effect=lambda: copy.deepcopy(cfg_tabs)):
            win3 = main_window.MainWindow()
            win3.show()
            for _ in range(8):
                app.processEvents()
            tabs3 = win3.panel_host._tabs[0][0]
            base_win_h = win3.height()
            base_stack_h = win3.stack.height()
            base_col_hint = win3.panel_host.column_hint()
            base_wheel_geom = win3.color_wheel.get_wheel_geometry()
            for target_idx in (1, 0, 1):
                tabs3.setCurrentIndex(target_idx)
                for _ in range(8):
                    app.processEvents()
                assert win3.panel_host.column_hint() == base_col_hint
                assert win3.height() == base_win_h
                assert win3.stack.height() == base_stack_h
                assert win3.stack.minimumHeight() == base_stack_h
                assert win3.color_wheel.get_wheel_geometry() == base_wheel_geom
            win3.toggle_picker_mode()
            for _ in range(4):
                app.processEvents()
            assert win3.stack.minimumHeight() == base_stack_h
            win3.toggle_picker_mode()
            for _ in range(4):
                app.processEvents()
            assert win3.stack.height() == base_stack_h
            win3.close()

print("COLOR MAP DROP AND TRANSITIONS OK")
sys.stdout.flush()
os._exit(0)
"""


def test_panel_landing_overlay_and_glide_no_top_level_window(qapp):
    win = QWidget()
    win.resize(360, 320)
    src = QLabel("source", win)
    src.setGeometry(12, 12, 160, 72)
    dst = QLabel("target", win)
    dst.setGeometry(12, 180, 200, 90)
    win.show()
    qapp.processEvents()

    class _WindowSpy(QObject):
        def __init__(self):
            super().__init__()
            self.stray = []

        def eventFilter(self, obj, ev):
            if ev.type() in (QEvent.Type.Show, QEvent.Type.ShowToParent):
                if isinstance(obj, QWidget) and obj is not win and obj.isWindow():
                    self.stray.append((type(obj).__name__, obj.geometry().getRect()))
                elif isinstance(obj, QWindow) and obj is not win.windowHandle():
                    self.stray.append(("QWindow", obj.title(), obj.geometry().getRect()))
            return False

    spy = _WindowSpy()
    qapp.installEventFilter(spy)
    set_animations_enabled_for_test(True)
    try:
        snap = capture_widget_snapshot(src)
        assert snap is not None
        assert animate_panel_landing(dst, start_snapshot=snap, duration_ms=140) is True
        overlay = getattr(dst, "_landing_overlay", None)
        glide = getattr(win, "_glide_overlay", None)
        assert overlay is not None and not overlay.isWindow()
        assert glide is not None and not glide.isWindow()
        qapp.processEvents()
        assert spy.stray == []
        overlay.stop()
        glide.stop()
        qapp.processEvents()
    finally:
        set_animations_enabled_for_test(None)
        qapp.removeEventFilter(spy)
        win.close()


def test_panel_host_zero_top_level_frame_shows_and_landing_animation(qapp):
    from ui.panels.tree import Leaf, Split, VERTICAL

    host_win = QWidget()
    host_win.resize(260, 420)
    made = {}
    for pid in ("sliders.rgb", "sliders.hsv", "sliders.oklab"):
        lbl = QLabel(pid)
        lbl.setFixedHeight(80)
        made[pid] = lbl

    host = PanelHost(lambda pid: made.get(pid), parent=host_win)
    from PyQt6.QtWidgets import QVBoxLayout

    box = QVBoxLayout(host_win)
    box.setContentsMargins(0, 0, 0, 0)
    box.addWidget(host)

    host.set_allow_tab_drops(True)
    host.set_drag_enabled(True)
    host.set_tree(Split(VERTICAL, tuple(Leaf(p) for p in made), (), False, 4, (0, 0, 0, 0)))
    host_win.show()
    qapp.processEvents()

    class _FrameSpy(QObject):
        def __init__(self):
            super().__init__()
            self.flashes = []

        def eventFilter(self, obj, ev):
            if ev.type() in (QEvent.Type.Show, QEvent.Type.ShowToParent):
                if isinstance(obj, PanelFrame) and (obj.isWindow() or obj.parent() is None):
                    self.flashes.append((getattr(obj, "panel_id", ""), obj.geometry().getRect()))
                elif isinstance(obj, QWindow) and "PanelFrame" in (obj.title() or type(obj).__name__):
                    self.flashes.append(("QWindow", obj.title(), obj.geometry().getRect()))
            return False

    spy = _FrameSpy()
    qapp.installEventFilter(spy)
    set_animations_enabled_for_test(True)
    try:
        # Stack sliders.oklab onto sliders.rgb (CENTER -> Tabs)
        rgb_frame = host.frame_for("sliders.rgb")
        assert rgb_frame is not None and not rgb_frame.isWindow()
        center_pos = host.mapFromGlobal(rgb_frame.mapToGlobal(rgb_frame.rect().center()))
        assert host.apply_drop("sliders.oklab", center_pos) is True
        for _ in range(4):
            qapp.processEvents()

        # Reorder sliders.hsv to top
        oklab_or_rgb = host._landing_target_widget("sliders.oklab")
        assert oklab_or_rgb is not None
        assert getattr(oklab_or_rgb, "_landing_overlay", None) is not None
        target_pos = host.mapFromGlobal(oklab_or_rgb.mapToGlobal(QPoint(oklab_or_rgb.width() // 2, 4)))
        assert host.apply_drop("sliders.hsv", target_pos) is True
        for _ in range(4):
            qapp.processEvents()

        hsv_target = host._landing_target_widget("sliders.hsv")
        assert hsv_target is not None
        assert getattr(hsv_target, "_landing_overlay", None) is not None
        assert spy.flashes == []
        for frame in host.findChildren(PanelFrame):
            assert frame.isWindow() is False
    finally:
        set_animations_enabled_for_test(None)
        qapp.removeEventFilter(spy)
        host_win.close()


def test_color_map_drops_anti_flicker_and_persistence():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT.replace("ROOT", root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "COLOR MAP DROP AND TRANSITIONS OK" in result.stdout


def test_drag_tear_preview_session_and_handoff(qapp):
    from PyQt6.QtGui import QDrag
    from ui.panels.floating import PanelChrome

    src_win = QWidget()
    src_win.setGeometry(100, 100, 300, 360)
    bar = PanelTitleBar("sliders.hsv", "HSV", src_win)
    bar.setGeometry(12, 12, 240, 16)
    body = QLabel("HSV Content", src_win)
    body.setGeometry(12, 28, 240, 150)
    src_win.show()
    qapp.processEvents()

    chrome = PanelChrome(
        background="rgba(240,240,240,0.700)",
        border_color="rgba(224,224,224,0.700)",
        border_width=4,
        radius=0,
        text="#222222",
        bar_bg="#e0e0e0",
        bar_text="#666666",
        divider_color="#e0e0e0",
        divider_width=0,
        opacity=0.7,
    )
    bar._chrome = chrome

    shown_geoms = []

    class _ShowSpy(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                shown_geoms.append((type(obj).__name__, obj.geometry().getRect()))
            return False

    spy = _ShowSpy()
    qapp.installEventFilter(spy)
    set_animations_enabled_for_test(True)
    try:
        # 1. Verify transparent 1x1 drag pixmap suppresses Qt default box and redundant title pill.
        drag = QDrag(bar)
        drag.setMimeData(bar.mime_data())
        bar._dress_drag(drag, QPoint(40, 8))
        pm = drag.pixmap()
        assert not pm.isNull()
        assert pm.rect().contains(drag.hotSpot())
        assert pm.size().width() == 1 and pm.size().height() == 1
        assert pm.toImage().pixelColor(0, 0).alpha() == 0

        # 2. Tear preview session creates a real disposable FloatingPanelWindow wearing chrome.
        sess = start_drag_tear_preview(
            "sliders.hsv",
            "HSV",
            body,
            QPoint(36, 8),
            chrome=chrome,
            no_focus=True,
            drag=drag,
        )
        preview = sess.preview
        assert preview is not None
        assert isinstance(preview.window, FloatingPanelWindow)
        assert preview.window._preview_only is True
        assert preview.window._chrome is chrome
        assert not preview.isVisible()
        flags = preview.windowFlags()
        assert bool(flags & Qt.WindowType.WindowTransparentForInput)
        assert bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus)

        # Hover inside gap (< 110ms) must NOT flicker preview open.
        inside_pt = QPoint(200, 220)
        sess.update_state(None, inside_pt, now=1.00)
        sess.update_state(None, inside_pt, now=1.05)
        assert not preview.isVisible()

        # Dwell inside (> 110ms) or exit outside source window -> blooms at clamped rect (never 0,0).
        outside_pt = QPoint(520, 260)
        sess.update_state(None, outside_pt, now=1.06)
        qapp.processEvents()
        assert preview.isVisible()
        assert preview.is_tear_active()
        assert all(g[0] != 0 or g[1] != 0 for _name, g in shown_geoms)

        # Re-entering valid dock target yields immediately.
        sess.update_state(src_win, QPoint(200, 150), now=1.10)
        assert not preview.is_tear_active()

        # Exit again and finish with IgnoreAction -> registers TearHandoff consumed cleanly.
        sess.update_state(None, outside_pt, now=1.15)
        qapp.processEvents()
        handoff = sess.finish(accepted=False, cursor_pos=outside_pt)
        assert handoff is not None and handoff.from_drag is True
        popped = consume_tear_handoff("sliders.hsv")
        assert popped is handoff

        # Seamless swap onto real floating window and dispose temporary preview ("拖完就作废").
        target_win = FloatingPanelWindow("sliders.hsv", "HSV", no_focus=True)
        target_win.setGeometry(popped.target_rect)
        target_win.show()
        qapp.processEvents()
        assert finish_tear_reveal(target_win, handoff=popped, no_focus=True) is True
        assert preview._handed_off is True
        target_win.close()

        # Accepted drop discards preview immediately without registering handoff.
        sess2 = start_drag_tear_preview("sliders.hsv", "HSV", body, QPoint(24, 8), chrome=chrome, no_focus=True)
        sess2.update_state(None, outside_pt, now=2.0)
        assert sess2.finish(accepted=True) is None
        assert consume_tear_handoff("sliders.hsv") is None

        # Outward ghost helper (used for double-click/menu tear-off) spawns safely.
        snap = capture_widget_snapshot(body)
        before_ghosts = len(_ACTIVE_GHOSTS)
        spawn_tear_ghost(snap, QRect(480, 240, 260, 180), no_focus=True, duration_ms=110)
        assert len(_ACTIVE_GHOSTS) >= before_ghosts + 1
        for g in list(_ACTIVE_GHOSTS):
            try:
                g.stop()
            except RuntimeError:
                pass
        _ACTIVE_GHOSTS.clear()
        qapp.processEvents()
    finally:
        set_animations_enabled_for_test(None)
        qapp.removeEventFilter(spy)
        src_win.close()


def test_drop_indicator_show_drop_preserves_running_animation(qapp):
    parent = QWidget()
    parent.resize(320, 320)
    parent.show()
    ind = DropIndicator(parent)

    set_animations_enabled_for_test(True)
    try:
        r1 = QRect(10, 10, 120, 80)
        r2 = QRect(40, 180, 200, 100)
        ind.show_drop(r1, zone=rearrange.TOP)
        ind.show_drop(r2, zone=rearrange.BOTTOM)
        running_anim = ind._rect_anim
        assert running_anim is not None
        # Repeated dragMove updates inside the same zone/rect must NOT kill & restart the glide.
        ind.show_drop(QRect(r2), zone=rearrange.BOTTOM)
        assert ind._rect_anim is running_anim
        # Switching zone stops and updates cleanly.
        ind.show_drop(QRect(r2), zone=rearrange.CENTER)
        assert ind._label == "堆叠"
    finally:
        ind.hide()
        set_animations_enabled_for_test(None)
        parent.close()
