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
from ui.panels.drag import PANEL_MIME, DropIndicator, PanelFrame
from ui.panels.floating import FloatingPanelWindow
from ui.panels.host import PanelHost
from ui.panels.transitions import (
    animate_panel_landing,
    animations_enabled,
    capture_widget_snapshot,
    capture_window_snapshot,
    set_animations_enabled_for_test,
    spawn_dock_ghost,
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
        from ui.panels import rearrange
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
        finally:
            set_animations_enabled_for_test(None)
            app.removeEventFilter(spy)
        win2.close()

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
