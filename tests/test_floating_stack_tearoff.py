"""Tearing a stacked wheel page out of a floating window keeps both pages."""

import os
import subprocess
import sys


_SCRIPT = r"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT')
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
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
    win.float_wheel_module('hsv'); app.processEvents()
    fwin = win.floating_windows()['wheel.hsv']
    from ui.panels import rearrange
    win._on_panel_dropped_into_floating(
        'wheel.hls', ('wheel.hsv', rearrange.MERGE_PAGE), fwin)
    app.processEvents(); app.processEvents()
    assert set(fwin.panel_ids) == {'wheel.hsv', 'wheel.hls'}

    win._on_floating_panel_float_requested('wheel.hsv', fwin)
    app.processEvents(); app.processEvents()
    windows = win.floating_windows()
    assert 'wheel.hsv' in windows and 'wheel.hls' in windows
    assert windows['wheel.hsv'] is not windows['wheel.hls']
    assert set(windows['wheel.hsv'].panel_ids) == {'wheel.hsv'}
    assert set(windows['wheel.hls'].panel_ids) == {'wheel.hls'}
    for pid in ('wheel.hsv', 'wheel.hls'):
        page = windows[pid].panel(pid)
        assert page is not None
        assert page.isVisible(), pid
        assert page.panel.isVisible(), f'{pid} had no wheel content'
    print('STACK TEAROFF OK'); sys.stdout.flush()
    os._exit(0)
"""


def test_stacked_wheel_page_tears_off_with_content():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.replace("ROOT", root)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "STACK TEAROFF OK" in result.stdout
