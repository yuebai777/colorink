"""Floating wheel pages: local LAB view, empty-picker collapse, title menu."""

import os
import subprocess
import sys


_SCRIPT = r"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT')
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from core import config
import ui.main_window as main_window

app = QApplication(sys.argv)
cfg = config.load_hotkey_config()
cfg.update({
    'onlyShowInCsp': False, 'panelDrag': True, 'slidersTabs': True,
    'colorSpaceModule': 'hsv', 'floatingPanels': {}, 'panelLayout': None,
    'floatingWheelPageOptions': {},
})
with patch('core.config.load_hotkey_config', return_value=dict(cfg)), \
     patch('core.config.save_hotkey_config'), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win = main_window.MainWindow()
    win.show(); app.processEvents(); win.apply_theme(); app.processEvents()
    for key in list(win._wheel_visible_keys()):
        win.float_wheel_module(key); app.processEvents()
    win._sync_picker_tab_bar(); win._adjust_content_height(); app.processEvents()
    assert win.stack.isHidden(), 'all wheel pages are out: picker must collapse'
    assert win.picker_tab_container.isHidden(), 'all wheel pages are out: no blank strip'
    assert win.height() < 400, win.height()

    hsv_win = win.floating_windows()['wheel.hsv']
    hls_win = win.floating_windows()['wheel.hls']
    hsv = hsv_win.panel('wheel.hsv')
    hls = hls_win.panel('wheel.hls')
    hsv.set_view_mode('lab'); app.processEvents()
    assert hsv.view_mode == 'lab' and hsv.lab_square is not None
    assert hls.view_mode == 'wheel', 'LAB switch is local to one floating page'
    hsv.lab_square.set_color(255, 0, 0, block_signals=False); app.processEvents()
    assert win.color_state.current.rgb == (255, 0, 0)
    assert hls.lab_square is None

    called = []
    win._show_floating_options = lambda w, p: called.append(w)
    QTest.mouseClick(hsv_win.title_bar, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(7, hsv_win.title_bar.height() // 2))
    app.processEvents()
    assert called == [hsv_win], 'title-bar left icon is the options menu'

    # Per-window follow / hide options.
    hsv.set_view_mode('wheel'); hsv.set_follow_global_lab(True, remember=True)
    win.toggle_lab_global(); app.processEvents()
    assert hsv.view_mode == 'lab' and hls.view_mode == 'wheel'
    hsv.set_lab_enabled(False, remember=True); app.processEvents()
    assert hsv.view_mode == 'wheel' and not hsv.mode_btn.isVisible()
    win.toggle_lab_global(); app.processEvents()
    assert hsv.view_mode == 'wheel', 'hidden LAB page ignores the global switch'
    hsv.set_lab_enabled(True, remember=True)

    win.dock_panel('wheel.hsv'); app.processEvents()
    win._sync_picker_tab_bar(); win._adjust_content_height(); app.processEvents()
    assert not win.stack.isHidden(), 'docking one page restores the picker'
    assert win.height() > 300, win.height()

    # The docked LAB page has the same grip and tears off independently.
    win.toggle_picker_mode(); win._sync_picker_tab_bar(); app.processEvents()
    assert win.wheel_grip_bar.isVisible()
    assert win.wheel_grip_bar.panel_id == 'lab.visualizer'
    assert win._float_active_wheel_page(); app.processEvents()
    assert 'lab.visualizer' in win.floating_windows()
    lab_page = win.floating_windows()['lab.visualizer'].panel('lab.visualizer')
    assert type(lab_page).__name__ == 'FloatingLabPage'
    assert lab_page.lab_square is not None
    assert lab_page.preview in win._floating_preview_boxes
    win.set_active_transparent(); app.processEvents()
    assert lab_page.preview.fg_transparent is True
    win.set_active_transparent(); app.processEvents()
    assert lab_page.preview.fg_transparent is False
    win.dock_panel('lab.visualizer'); app.processEvents()
    assert 'lab.visualizer' not in win.floating_windows()
    assert lab_page.preview not in win._floating_preview_boxes

    # Grip bar remains available when panelDrag=True even if module tab bar is hidden.
    win.toggle_picker_mode(); app.processEvents()
    win.cfg['showModuleSwitchButton'] = False
    win.cfg['panelDrag'] = True
    win._sync_picker_tab_bar(); app.processEvents()
    assert not win.picker_tab_bar.isVisible()
    assert win.wheel_grip_bar.isVisible(), 'wheel grip stays visible in panelDrag mode when tab bar is hidden'
    win.cfg['showModuleSwitchButton'] = True
    win._sync_picker_tab_bar(); app.processEvents()

    # Dropping a floating wheel window onto slider area with panelDrag=False docks to picker, not slider host.
    for k in ('vhsv', 'hls', 'rgb', 'lch'):
        win.dock_panel(f'wheel.{k}')
    app.processEvents()
    win.float_wheel_module('hsv'); app.processEvents()
    win.cfg['panelDrag'] = False
    slider_center = win.panel_host.mapToGlobal(win.panel_host.rect().center())
    win._floating_dropped('wheel.hsv', slider_center); app.processEvents()
    assert 'wheel.hsv' not in win.floating_windows()
    assert 'wheel.hsv' not in win.panel_host.tree().panels(), 'must dock to picker, not slider host when panelDrag=False'
    assert win.wheel_stack.indexOf(win.wheel_panels['hsv']) >= 0
    print('FLOAT LAB OK'); sys.stdout.flush()
    os._exit(0)
"""


def test_floating_lab_page_is_local_and_empty_picker_collapses():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.replace("ROOT", root)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "FLOAT LAB OK" in result.stdout
