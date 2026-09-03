"""环境收集探针：抓手开关下预览簇与页签条的几何。

只读配置（绝不写盘），开真实窗口，抓=关/抓=开各测一次，
并分别走 refresh 直切 与 设置保存(on_settings_saved) 两条路径。
输出：控制台 + 仓库根目录 preview_handoff_report.txt
用法：python tools/probe_preview_handoff.py
"""

import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PyQt6.QtCore import QPoint, QRect, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

from core import config  # noqa: E402
import ui.main_window as main_window  # noqa: E402

REPORT_PATH = os.path.join(_ROOT, "preview_handoff_report.txt")


def quiesce(win):
    from core import global_hotkeys
    try:
        global_hotkeys.unbind_all()
    except Exception:
        pass
    overlay = getattr(win, "grayscale_overlay", None)
    if overlay is not None:
        overlay.set_active(False)
    sync = getattr(win, "sync_thread", None)
    if sync is not None:
        sync.sync_enabled = False
        sync.running = False
    timer = getattr(win, "foreground_timer", None)
    if timer is not None:
        timer.stop()


def settle(win, ms=350):
    """等 singleShot(0) 的 mount-apply 与防抖 settle 跑完。"""
    QApplication.processEvents()
    loop = QTimer()
    loop.setSingleShot(True)
    loop.timeout.connect(lambda: None)
    loop.start(ms)
    while loop.isActive():
        QApplication.processEvents()


def activate(host):
    for _ in range(3):
        host.layout().activate()
        for child in host.findChildren(QWidget):
            layout = child.layout()
            if layout is not None:
                layout.activate()
    QApplication.processEvents()


def geometry_snapshot(win, label, out):
    host = win.panel_host
    preview = win.preview_box
    tabs = host.findChildren(QTabWidget)
    p = preview.mapTo(win, QPoint(0, 0))
    prev_rect = QRect(p, preview.size())
    sliders_top = win.sliders_container.mapTo(win, QPoint(0, 0)).y()
    tabs_rect = None
    if tabs:
        tp = tabs[0].mapTo(win, QPoint(0, 0))
        tabs_rect = QRect(tp, tabs[0].size())
    overlap = tabs_rect.intersects(prev_rect) if tabs_rect else None
    line = (f"[{label}] position_mode={preview.position_mode}\n"
            f"    preview={prev_rect} bottom={p.y() + preview.height()}\n"
            f"    sliders_top={sliders_top} sliders_hint="
            f"{win.sliders_container.sizeHint().height()} "
            f"sliders_h={win.sliders_container.height()}\n"
            f"    tabs={tabs_rect} overlap={overlap}\n"
            f"    win_h={win.height()} min_h={win.minimumHeight()} "
            f"min_hint={win.minimumSizeHint().height()} "
            f"size_hint={win.sizeHint().height()} "
            f"required={getattr(win, '_last_required_height', None)} "
            f"column_hint={host.column_hint() if host else None}\n"
            f"    manual_override={getattr(win, '_manual_height_override', None)} "
            f"last_auto={getattr(win, '_last_auto_height', None)} "
            f"pending_height_adj={getattr(win, '_content_height_adjust_pending', None)} "
            f"panel_mount_pending={getattr(win, '_panel_mount_pending', None)} "
            f"visible={win.isVisible()} host_vis={host.isVisible() if host else None} "
            f"container_vis={win.sliders_container.isVisible()}\n"
            f"    lockWindowSize={win.cfg.get('lockWindowSize', False)} "
            f"uiScale={win.cfg.get('uiScale', 100)} "
            f"slidersTabs={win.cfg.get('slidersTabs', False)} "
            f"panelDrag={win.cfg.get('panelDrag', False)} "
            f"previewBoxPosition={win.cfg.get('previewBoxPosition', 'top-left')}")
    print(line)
    out.append(line + "\n")


def main():
    app = QApplication(sys.argv)
    cfg = config.load_hotkey_config()
    original_cfg = dict(cfg)
    dpr = app.devicePixelRatio()
    screen = app.primaryScreen()
    out = []
    header = (
        "preview_handoff_report\n"
        f"dpr={dpr} screen_geo={screen.geometry() if screen else None} "
        f"screen_dpr={screen.devicePixelRatio() if screen else None}\n"
        f"cfg: uiScale={cfg.get('uiScale', 100)} slidersTabs="
        f"{cfg.get('slidersTabs', False)} panelDrag={cfg.get('panelDrag', False)} "
        f"lockWindowSize={cfg.get('lockWindowSize', False)} "
        f"onlyShowInCsp={cfg.get('onlyShowInCsp', False)} "
        f"noFocusMode={cfg.get('noFocusMode', False)} "
        f"position_mode(preview runtime)={None}\n"
    )
    print(header)
    out.append(header)

    win = main_window.MainWindow()
    # The foreground tracker can hide the window right after show()
    # (onlyShowInCsp / noFocusMode), which makes every measured layout
    # degenerate (0-height tab strip, pending height adjustment). Stop it
    # BEFORE measuring and force the window to stay up.
    quiesce(win)
    # on_settings_saved() re-calls check_foreground_window() when
    # onlyShowInCsp is on; from this console the foreground is not a drawing
    # app, so it would hide the window right in the middle of the "settings"
    # path and poison every later measurement. The probe measures geometry,
    # not foreground policy — neutralize it at runtime only.
    win.check_foreground_window = lambda: None
    # The timer connected timeouts to the ORIGINAL bound method at
    # construction; replacing the instance attribute is not enough.
    # on_settings_saved() restarts the timer, so disconnect it too.
    fg_timer = getattr(win, "foreground_timer", None)
    if fg_timer is not None:
        try:
            fg_timer.timeout.disconnect()
        except TypeError:
            pass
        fg_timer.timeout.connect(lambda: None)
    win._user_hidden = False
    win.auto_hidden = False
    win.resize(456, 777)
    win.show()
    win.raise_()
    QApplication.processEvents()
    host = win.panel_host
    activate(host)
    settle(win)

    # 报告中同时给出 top-left / bottom-left 两种运行时模式（只改运行时，不写配置）。
    for mode in (win.preview_box.position_mode, "bottom-left"):
        win.preview_box.position_mode = mode
        win.apply_theme()
        QApplication.processEvents()
        activate(host)
        settle(win)
        geometry_snapshot(win, f"initial mode={mode}", out)

    for drag in (True, False, True):
        # 路径 A：直接 refresh（等价内部重挂，不改盘）
        win.cfg["panelDrag"] = drag
        win.refresh_slider_visibility_and_order()
        QApplication.processEvents()
        activate(host)
        settle(win)
        geometry_snapshot(win, f"refresh drag={'ON' if drag else 'OFF'}", out)
        win.grab().save(os.path.join(
            _ROOT, f"preview_handoff_refresh_{'on' if drag else 'off'}.png"))
        # 路径 B：设置保存全流程（on_settings_saved 会从磁盘重载，所以
        # 备份 → 写入目标值 → 保存 → 跑 → 结束前恢复原配置）。
        try:
            cfg_b = config.load_hotkey_config()
            cfg_b["panelDrag"] = drag
            config.save_hotkey_config(cfg_b)
            win.on_settings_saved()
            QApplication.processEvents()
            activate(host)
            settle(win)
            geometry_snapshot(win, f"settings drag={'ON' if drag else 'OFF'}", out)
            win.grab().save(os.path.join(
                _ROOT, f"preview_handoff_settings_{'on' if drag else 'off'}.png"))
        finally:
            config.save_hotkey_config(original_cfg)
            win.cfg = config.load_hotkey_config()

    print("\nreport written to", REPORT_PATH)
    out.append("\nreport written to " + REPORT_PATH + "\n")
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
    quiesce(win)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
