"""Picker tab bar: wheel modules on the wheel page, LAB/OKLab on the LAB page.

Replaces the bottom-right module cycle button. Covers the panel-grip reorder
(dragging a tab reorders it without switching the active module) and the local
view shortcut over the strip. Runs a real MainWindow in a subprocess (same
pattern as test_tab_drag_and_wheel_regression).
"""

import os
import subprocess
import sys

from ui.window.module_defs import (
    normalize_lab_visualizer_order,
    normalize_wheel_module_order,
    normalize_wheel_module_tabs,
)


def test_wheel_order_keeps_every_module_once():
    assert normalize_wheel_module_order(["lch", "hsv", "lch", "bogus"]) == [
        "lch", "hsv", "vhsv", "hls", "rgb"]


def test_wheel_tabs_follow_order_and_never_empty():
    order = ["rgb", "hsv", "vhsv", "hls", "lch"]
    assert normalize_wheel_module_tabs(["hsv", "rgb"], order) == ["rgb", "hsv"]
    assert normalize_wheel_module_tabs([], order, "hls") == ["hls"]


def test_lab_order_normalizes():
    assert normalize_lab_visualizer_order(["oklab"]) == ["oklab", "lab"]
    assert normalize_lab_visualizer_order(None) == ["lab", "oklab"]


_SCRIPT = r"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT')
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint
from core import config
import ui.main_window as main_window

app = QApplication(sys.argv)
cfg = config.load_hotkey_config()
cfg.update({'colorSpaceModule': 'hsv', 'hideHueRing': False, 'onlyShowInCsp': False,
            'floatingPanels': {}, 'visualizerMode': 'lab', 'showModuleSwitchButton': True,
            'wheelModuleTabs': ['hsv', 'vhsv', 'hls', 'rgb', 'lch'],
            'wheelModuleOrder': ['hsv', 'vhsv', 'hls', 'rgb', 'lch'],
            'labVisualizerOrder': ['lab', 'oklab']})
with patch('core.config.load_hotkey_config', return_value=dict(cfg)), \
     patch('core.config.save_hotkey_config'), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win = main_window.MainWindow()
    win.show(); app.processEvents()
    bar = win.picker_tab_bar
    # No bottom-right module button any more.
    assert not hasattr(win, 'btn_module')
    assert bar.isVisible()
    assert bar.tab_keys() == ['hsv', 'vhsv', 'hls', 'rgb', 'lch'], bar.tab_keys()
    # Tab bar sits between the title bar and the picker.
    assert bar.mapTo(win.central, bar.rect().topLeft()).y() >= win.title_bar.geometry().bottom()
    assert win.stack.geometry().top() >= bar.mapTo(win.central, bar.rect().bottomLeft()).y()
    # Picker stays square and bound to the window width.
    app.processEvents()
    win._adjust_content_height(); app.processEvents()
    assert win.stack.width() == win.stack.height(), (win.stack.width(), win.stack.height())

    bar.setCurrentIndex(bar.tab_keys().index('rgb')); app.processEvents()
    assert win._current_module == 'rgb'
    assert win.color_wheel.wheel_mode == 'rgb-slice'

    # Drag-reorder persists the order.
    bar.moveTab(bar.tab_keys().index('rgb'), 0); app.processEvents()
    assert win.cfg['wheelModuleOrder'][0] == 'rgb', win.cfg['wheelModuleOrder']
    assert win.wheel_stack.widget(0).key == 'rgb', win.wheel_stack.widget(0).key

    # Grip mode is a real panel grip: the active wheel page can be torn
    # off into a floating window, and the tab strip loses that page until it
    # is docked back.
    win.cfg['panelDrag'] = True
    win.refresh_slider_visibility_and_order()
    win._sync_picker_tab_bar(); win.apply_theme(); win._adjust_content_height()
    app.processEvents()
    # A wheel page can be dropped into the shared slider host (the same
    # PanelHost as the sliders), and its grip on the tab strip brings it back.
    host = win.panel_host
    frame = host.frame_for('sliders.hsv')
    assert frame is not None
    drop_pos = frame.mapTo(host, QPoint(frame.width() // 2,
                                       frame.height() // 2))
    assert host.apply_drop('wheel.lch', drop_pos)
    app.processEvents()
    assert 'wheel.lch' in host.tree().panels(), host.tree().panels()
    assert 'lch' not in bar.tab_keys(), bar.tab_keys()
    win._on_wheel_grip_dropped('wheel.lch', 0)
    app.processEvents()
    assert 'wheel.lch' not in host.tree().panels(), host.tree().panels()
    assert 'lch' in bar.tab_keys(), bar.tab_keys()
    assert win.wheel_grip_bar.isVisible()
    assert win.wheel_grip_bar.title == win._current_module.upper()
    active_before = win._current_module
    float_id = 'wheel.' + active_before
    assert win._float_active_wheel_page()
    app.processEvents()
    assert float_id in win.floating_windows()
    float_win = win.floating_windows()[float_id]
    float_page = float_win.panel()
    assert float_page.panel is win.wheel_panels[active_before]
    assert float_page.preview.isVisible()
    assert float_page.mode_btn.isVisible()
    assert float_win.height() > 300, float_win.height()
    assert bar.current_key() != active_before or active_before not in bar.tab_keys()
    assert win.wheel_stack.indexOf(win.wheel_panels[active_before]) < 0
    # The floating wheel reports its own native space; the docked page must
    # follow it (not stay on the colour it had when the page was torn off).
    float_page.panel.wheel.set_color(10, 20, 30)
    app.processEvents()
    assert win.current_rgb == (10, 20, 30), win.current_rgb
    assert win.color_wheel.get_color() == (10, 20, 30), win.color_wheel.get_color()
    assert win.dock_panel(float_id)
    app.processEvents()
    assert float_id not in win.floating_windows()
    assert win.wheel_stack.indexOf(win.wheel_panels[active_before]) >= 0
    assert bar.current_key() == active_before, (bar.current_key(), active_before)
    win.cfg['panelDrag'] = False
    win._sync_picker_tab_bar(); app.processEvents()
    assert not win.wheel_grip_bar.isVisible()

    # The local view shortcut over the strip walks to the next module, like
    # it already does over a slider tab stack.
    shortcut_start = bar.current_key()
    assert win._tab_toggle_target(bar.mapToGlobal(bar.rect().center())) == (win, bar)
    assert win._run_local_view_shortcut(bar.mapToGlobal(bar.rect().center()))
    app.processEvents()
    assert bar.current_key() != shortcut_start
    bar.setCurrentIndex(bar.tab_keys().index('rgb')); app.processEvents()

    # LAB page: tabs become LAB / OKLab.
    win.toggle_picker_mode(); app.processEvents()
    assert bar.tab_keys() == ['lab', 'oklab'], bar.tab_keys()
    assert bar.current_key() == 'lab'
    bar.setCurrentIndex(1); app.processEvents()
    assert win.cfg['visualizerMode'] == 'oklab'
    assert win.lab_square.render_mode == 'oklab'

    # Back to the wheel: the last wheel module is restored.
    win.toggle_picker_mode(); app.processEvents()
    assert bar.current_key() == 'rgb'

    # Hiding modules from settings removes their tabs.
    win.cfg['wheelModuleTabs'] = ['hsv', 'lch']
    win._sync_picker_tab_bar()
    assert set(bar.tab_keys()) == {'hsv', 'lch'}
    # The removed active module (rgb) hands over to the selected tab.
    assert win._current_module == bar.current_key() == 'hsv', win._current_module

    # Turning the bar off hides it.
    win.cfg['showModuleSwitchButton'] = False
    win._sync_picker_tab_bar(); app.processEvents()
    assert not bar.isVisible()
    if len(sys.argv) > 1:
        win.cfg['showModuleSwitchButton'] = True
        win.cfg['wheelModuleTabs'] = ['hsv', 'vhsv', 'hls', 'rgb', 'lch']
        win._sync_picker_tab_bar(); win.apply_theme(); app.processEvents()
        win.grab().save(sys.argv[1])
    print('PICKER TAB BAR OK'); sys.stdout.flush()
    os._exit(0)
"""


def run_script(*extra):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT.replace("ROOT", root), *extra],
        capture_output=True, text=True, timeout=120,
    )


def test_picker_tab_bar_live_window():
    result = run_script()
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "PICKER TAB BAR OK" in result.stdout