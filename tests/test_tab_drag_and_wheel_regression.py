"""Regression tests for Bug 1 and Bug 2:
1. Dragging one module out of a 3-module tab page does not compress the color wheel.
2. Dragging a module into any other tab page (in empty stretch space or on panel edge)
   correctly adds the module to that tab page.
"""

import os
import subprocess
import sys
import pytest

_SUBPROCESS_SCRIPT = r"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT_PLACEHOLDER')
from PyQt6.QtWidgets import QApplication, QTabWidget
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from core import config
import ui.main_window as main_window
from unittest.mock import patch

app = QApplication(sys.argv)
test_cfg = config.load_hotkey_config()
test_cfg['panelDrag'] = True
test_cfg['slidersTabs'] = True
test_cfg['floatingPanels'] = {}
test_cfg['panelLayout'] = {
    'version': 1, 'seed': 'tabs',
    'root': {'kind': 'tabs', 'current': 0, 'pages': [['history', 'sliders.hsv', 'sliders.rgb']]}
}

with patch('core.config.load_hotkey_config', return_value=dict(test_cfg)), \
     patch('core.config.save_hotkey_config', side_effect=lambda c: test_cfg.update(c)), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win = main_window.MainWindow()
    win.show()
    app.processEvents()

    # 1. Verify Bug 1: pulling module out does not compress color wheel
    init_stack = win.stack.size()
    init_wheel = win.color_wheel.size()
    init_geom = win.color_wheel.get_wheel_geometry()
    host = win.panel_host
    box = host.frame_for('sliders.hsv')
    center = box.mapTo(host, QPoint(box.width() // 2, box.height() // 2))
    host.apply_drop('sliders.rgb', center)
    app.processEvents()
    assert win.stack.height() >= init_stack.height(), f"Stack shrunk from {init_stack.height()} to {win.stack.height()}"
    assert win.color_wheel.height() >= init_wheel.height(), f"Wheel shrunk from {init_wheel.height()} to {win.color_wheel.height()}"
    after_geom = win.color_wheel.get_wheel_geometry()
    assert after_geom[2] >= init_geom[2], f"Wheel diameter shrunk from {init_geom[2]} to {after_geom[2]}"

    # 2. Verify Bug 2: dragging into other tab pages in empty stretch area works
    tabs = host.findChildren(QTabWidget)[0]
    tabs.setCurrentIndex(1)
    app.processEvents()
    rgb_box = host.frame_for('sliders.rgb')
    content_bottom = rgb_box._panel.y() + rgb_box._panel.height()
    empty_pt = rgb_box.mapTo(host, QPoint(100, content_bottom + 30))
    target = host.drop_target_at(empty_pt)
    assert target == ('sliders.rgb', 'bottom'), f"target={target}"
    ok = host.apply_drop('sliders.hsv', empty_pt)
    assert ok is True
    tree = host.tree()
    rgb_page = next(p for p in tree.pages if 'sliders.rgb' in p)
    assert rgb_page == ('sliders.rgb', 'sliders.hsv'), f"rgb_page={rgb_page}"

    # 3. Verify Bug 3: panelTopGap synchronizes with bottom empty distance
    for gap in (6, 15, 20):
        win.cfg['panelTopGap'] = gap
        win.apply_theme()
        win._adjust_content_height()
        app.processEvents()
        scale = win.cfg.get('uiScale', 100) / 100.0
        expected_bottom_margin = int(gap * scale)
        assert win.sliders_layout.contentsMargins().bottom() == expected_bottom_margin
        assert win.height() < 900

    # 4. Verify Bug 4: Color wheel is strictly bound to window width and never compressed
    start_w = win.width()
    start_h = win.height()
    expected_diam = (start_w - 8) - 16
    assert win.color_wheel.get_wheel_geometry()[2] == expected_diam
    assert win.stack.width() == win.stack.height()

    # Simulate horizontal drag by +40px
    win.resizing = True
    win.resize_dir = 'right'
    win.resize_start_pos = QPoint(win.x() + start_w, win.y() + 100)
    win.resize_start_geometry = win.geometry()
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(start_w + 40, 100),
        QPointF(win.x() + start_w + 40, win.y() + 100),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    win.mouseMoveEvent(event)
    app.processEvents()
    assert win.width() == start_w + 40
    assert win.height() == start_h + 40
    assert win.color_wheel.get_wheel_geometry()[2] == (start_w + 40 - 8) - 16

    # 5. Verify Bug 5: Switching tabs and adjusting settings does not switch the active tab
    tabs_widget = host.findChildren(QTabWidget)[0]
    tabs_widget.setCurrentIndex(0)
    app.processEvents()
    assert tabs_widget.currentIndex() == 0
    # Simulate on_settings_saved
    win.on_settings_saved()
    app.processEvents()
    tabs_widget_after = host.findChildren(QTabWidget)[0]
    assert tabs_widget_after.currentIndex() == 0

    tabs_widget_after.setCurrentIndex(1)
    app.processEvents()
    assert tabs_widget_after.currentIndex() == 1
    win.on_settings_saved()
    app.processEvents()
    tabs_widget_after2 = host.findChildren(QTabWidget)[0]
    assert tabs_widget_after2.currentIndex() == 1

    print("ALL REGRESSIONS VERIFIED SUCCESSFULLY")
    sys.stdout.flush()
    os._exit(0)
"""


def test_tab_drag_and_wheel_compression_regressions():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    script = _SUBPROCESS_SCRIPT.replace("ROOT_PLACEHOLDER", root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"Regression test failed with exit code {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "ALL REGRESSIONS VERIFIED SUCCESSFULLY" in result.stdout
