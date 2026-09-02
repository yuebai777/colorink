"""Capture what the title bar's left edge actually looks like on screen.

Offscreen rendering says the frame is symmetric in every theme, DPI and
opacity combination I can produce — so if a gap is visible on a real
display, the difference is in the compositing that only the real platform
does (fractional DPI rounding, translucency against the desktop).

This runs the app *on the real screen* with a **copy** of your config (your
real one is never touched), captures the window two ways, and prints the
pixels around the title bar's left and right edges.

    python tools/diag_titlebar_gap.py

Paste the whole output back. A window will flash on screen for a moment.
"""

import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SANDBOX = os.path.join(_ROOT, ".tmp_realcfg")


def _isolate_config():
    """Run against a copy, so nothing here can modify the real settings."""
    real = os.path.join(os.environ.get("APPDATA", ""), "Colorink")
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    target = os.path.join(_SANDBOX, "Colorink")
    os.makedirs(target, exist_ok=True)
    if os.path.isdir(real):
        for name in os.listdir(real):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(real, name), os.path.join(target, name))
    os.environ["APPDATA"] = _SANDBOX


_isolate_config()
sys.path.insert(0, _ROOT)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
import ui.main_window as main_window  # noqa: E402

_KEEPALIVE = []


def _edges(image, y, count=10):
    left = " ".join(image.pixelColor(x, y).name()[1:] for x in range(count))
    right = " ".join(image.pixelColor(image.width() - 1 - x, y).name()[1:]
                     for x in range(count))
    return left, right


def _report(tag, image, bar_height, ratio):
    print(f"\n--- {tag} ({image.width()}x{image.height()}, ratio {ratio}) ---")
    rows = [0, 1, 2, 3, bar_height // 2, bar_height - 1, bar_height,
            bar_height + 1, bar_height + 4]
    for y in rows:
        y_dev = int(y * ratio)
        if not (0 <= y_dev < image.height()):
            continue
        left, right = _edges(image, y_dev)
        mark = "" if left == right else "   <== 左右不同"
        print(f"y={y:>3}  左: {left}   右: {right}{mark}")


def main():
    app = QApplication(sys.argv)
    _KEEPALIVE.append(app)
    cfg = config.load_hotkey_config()
    print("主题:", cfg.get("ui-theme"), "| 边框:", cfg.get("borderStyle"),
          "| 滑块:", cfg.get("sliderStyle"), "| 不透明度:",
          cfg.get("backgroundOpacity"), "| 缩放:", cfg.get("uiScale"))

    window = main_window.MainWindow()
    _KEEPALIVE.append(window)
    window.show()
    for _ in range(6):
        app.processEvents()
    bar = window.title_bar
    print("设备像素比:", window.devicePixelRatio(),
          "| 标题栏 geo:", bar.geometry().getRect(),
          "| 窗口:", window.geometry().getRect())

    grabbed = window.grab().toImage()
    _report("widget.grab()（Qt 自己画的）", grabbed, bar.height(),
            grabbed.width() / max(1, window.width()))

    screen = window.screen() or app.primaryScreen()
    if screen is not None:
        shot = screen.grabWindow(0).toImage()
        ratio = shot.width() / max(1, screen.geometry().width())
        origin = window.frameGeometry().topLeft()
        cropped = shot.copy(int(origin.x() * ratio), int(origin.y() * ratio),
                            int(window.width() * ratio),
                            int((bar.height() + 8) * ratio))
        cropped.save(os.path.join(_SANDBOX, "titlebar.png"))
        _report("屏幕截图（你眼睛看到的）", cropped, bar.height(), ratio)
        print("\n放大图已存到:", os.path.join(_SANDBOX, "titlebar.png"))

    try:
        from core import global_hotkeys
        global_hotkeys.unbind_all()
    except Exception:
        pass
    overlay = getattr(window, "grayscale_overlay", None)
    if overlay is not None:
        overlay.set_active(False)
    sync = getattr(window, "sync_thread", None)
    if sync is not None:
        sync.sync_enabled = False
        sync.running = False
    window.hide()
    app.processEvents()
    return 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    os._exit(status)
