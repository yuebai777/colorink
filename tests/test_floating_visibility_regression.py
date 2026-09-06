"""回归：浮窗拖回 / 启动恢复后，设置里关闭的滑块组不得重新出现。

PanelHost 挂载面板时一律 ``setVisible(True)``，滑块组的可见性由
``refresh_slider_visibility_and_order`` 在重挂之后统一下发。此前三条路径
（``_dock_at``、``_on_panel_dropped_into_floating``、
``restore_floating_panels``）在重挂后漏掉了这一步，导致拖回浮窗或启动
恢复浮窗时，``showSliders`` 关闭的组全部弹出（用户截图中第一张的情形）。

本测试用真实 ``MainWindow``（offscreen 子进程）复现并锁定修复。
"""

import os
import subprocess
import sys

_SUBPROCESS_SCRIPT = r'''
import os
import sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'ROOT_PLACEHOLDER')

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication
from unittest.mock import patch

from core import config
from ui.panels import rearrange
import ui.main_window as main_window

app = QApplication(sys.argv)

ENABLED = {'HSV', 'OKLab', 'History'}
DISABLED = ['RGB', 'VHSV', 'HSL', 'LAB', 'OKLCh']


def make_cfg(with_float=False):
    cfg = dict(config.load_hotkey_config())
    cfg.update({
        'showSlidersRGB': False,
        'showSlidersHSV': True,
        'showSlidersVHSV': False,
        'showSlidersHSL': False,
        'showSlidersLAB': False,
        'showSlidersOKLab': True,
        'showSlidersOKLCh': False,
        'showSlidersHistory': True,
        'panelDrag': False,
        'slidersTabs': False,
        'colorSpaceModule': 'hsv',
        'onlyShowInCsp': False,
    })
    cfg['floatingPanels'] = (
        {'sliders.hsv': {'rect': [100, 100, 200, 100], 'onTop': True}}
        if with_float else {})
    return cfg


def assert_docked_flags(win, label):
    """挂回主窗口的组必须严格遵循 showSliders（HSV 可能浮出，单独处理）。"""
    hidden = {'RGB': True, 'VHSV': True, 'HSL': True, 'LAB': True,
              'OKLCh': True, 'OKLab': False, 'History': False}
    for group, should_hide in hidden.items():
        w = win.slider_containers[group]
        assert w.isHidden() is should_hide, (
            f"{label}: {group}.isHidden()={w.isHidden()} "
            f"expected {should_hide}")


def assert_visible_set(win, label):
    got = {g for g in ['RGB', 'HSV', 'VHSV', 'HSL', 'LAB',
                       'OKLab', 'OKLCh', 'History']
           if not win.slider_containers[g].isHidden()}
    assert got == ENABLED, f"{label}: visible={got} expected={ENABLED}"


def assert_height_hugs_content(win, label):
    """启动/拖回后窗口必须贴住内容：不能残留一截空白带。"""
    assert abs(win.height() - win._last_required_height) <= 8, (
        f"{label}: height={win.height()} required={win._last_required_height}"
    )


# ── 场景 1：浮出 HSV 后拖回主窗口 ───────────────────────────────────────
cfg1 = make_cfg(with_float=False)
with patch('core.config.load_hotkey_config', return_value=cfg1), \
     patch('core.config.save_hotkey_config', side_effect=lambda c: cfg1.update(c)), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win = main_window.MainWindow()
    win.show()
    app.processEvents()
    assert_docked_flags(win, 'startup (no float)')
    assert_visible_set(win, 'startup (no float)')
    assert_height_hugs_content(win, 'startup (no float)')

    assert win.float_panel('sliders.hsv') is True
    app.processEvents()
    assert 'sliders.hsv' in win.floating_windows()

    # 把浮窗拖回主窗口，落在可见的 OKLab 组顶部 —— 走 _floating_dropped →
    # dock_panel → _dock_at 这条真实路径。
    box = win.panel_host.widget_for('sliders.oklab')
    assert box is not None
    drop_pt = box.mapToGlobal(QPoint(box.width() // 2, 1))
    win._floating_dropped('sliders.hsv', drop_pt)
    app.processEvents()

    assert 'sliders.hsv' not in win.floating_windows(), 'HSV 应已收回主窗口'
    assert_docked_flags(win, 'after drag-back')
    assert_visible_set(win, 'after drag-back')

    # 再直接走一次 _dock_at（不带 _floating_dropped 的收尾），确保它本身
    # 也会重新应用可见性。
    win._dock_at('sliders.hsv', ('sliders.oklab', rearrange.BOTTOM))
    app.processEvents()
    assert_docked_flags(win, 'after direct _dock_at')
    assert_visible_set(win, 'after direct _dock_at')
    assert_height_hugs_content(win, 'after direct _dock_at')

# ── 场景 2：启动时恢复浮窗 ──────────────────────────────────────────────
cfg2 = make_cfg(with_float=True)
with patch('core.config.load_hotkey_config', return_value=cfg2), \
     patch('core.config.save_hotkey_config', side_effect=lambda c: cfg2.update(c)), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win2 = main_window.MainWindow()
    win2.show()
    app.processEvents()

    assert 'sliders.hsv' in win2.floating_windows(), 'HSV 浮窗应被恢复'
    # HSV 在浮窗里（其上可见是正常的），主窗口其余组必须遵守 showSliders。
    assert_docked_flags(win2, 'startup with floating restore')
    assert_height_hugs_content(win2, 'startup with floating restore')

    win2.dock_panel('sliders.hsv')
    app.processEvents()
    assert_docked_flags(win2, 'after docking restored HSV')
    assert_visible_set(win2, 'after docking restored HSV')
    assert_height_hugs_content(win2, 'after docking restored HSV')

# ── 场景 3：主窗口面板拖进浮窗、再从浮窗拖出（协议回调真实路径） ──────
cfg3 = make_cfg(with_float=False)
with patch('core.config.load_hotkey_config', return_value=cfg3), \
     patch('core.config.save_hotkey_config', side_effect=lambda c: cfg3.update(c)), \
     patch('core.memory_sync.MemorySyncThread.start'), \
     patch('core.global_hotkeys.bind_hotkey'), \
     patch('core.global_hotkeys.bind_mouse_hotkey'), \
     patch('core.global_hotkeys.unbind_all'):
    win3 = main_window.MainWindow()
    win3.show()
    app.processEvents()

    assert win3.float_panel('sliders.oklab') is True
    app.processEvents()
    fwin = win3.floating_windows()['sliders.oklab']

    # 主窗口的 HSV 拖进这个浮窗（QDrag 落点回调）
    win3._on_panel_dropped_into_floating(
        'sliders.hsv', ('sliders.oklab', rearrange.BOTTOM), fwin)
    app.processEvents()

    assert 'sliders.hsv' in win3.floating_windows()
    assert win3.floating_windows()['sliders.hsv'] is fwin
    assert_docked_flags(win3, 'after drop into floating window')
    assert_height_hugs_content(win3, 'after drop into floating window')

    # 从浮窗把 HSV 拖回它自己的窗口（多面板浮窗里拖出一个）
    win3._on_floating_panel_float_requested('sliders.hsv', fwin)
    app.processEvents()
    assert win3.floating_windows()['sliders.hsv'] is not fwin
    assert fwin.panel_ids == ('sliders.oklab',)
    assert_docked_flags(win3, 'after tearing HSV out of floating window')
    assert_height_hugs_content(win3, 'after tearing HSV out of floating window')

    # 收回 HSV：主窗口仍只有开启组，且高度贴合内容
    win3.dock_panel('sliders.hsv')
    app.processEvents()
    assert_docked_flags(win3, 'after docking HSV')
    assert_visible_set(win3, 'after docking HSV')
    assert_height_hugs_content(win3, 'after docking HSV')

print('FLOATING VISIBILITY REGRESSIONS VERIFIED SUCCESSFULLY')
sys.stdout.flush()
os._exit(0)
'''


def test_floating_drag_back_does_not_resurrect_disabled_slider_groups():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    script = _SUBPROCESS_SCRIPT.replace("ROOT_PLACEHOLDER", root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"Regression test failed with exit code {result.returncode}:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "FLOATING VISIBILITY REGRESSIONS VERIFIED SUCCESSFULLY" in result.stdout
