"""Real-screen HWND visibility diagnostic for the foreground tracker.

The offscreen test suite can only prove Qt's state; the user's report was
that the *real* HWND of a torn-off panel stays on screen after the main
window hides. This script runs a genuine window (no offscreen platform),
floats a panel, and compares GetWindowRect / IsWindowVisible(hwnd) with the
Qt isVisible()/isHidden() state around a hide/show cycle.

    python -u tools/diag_floating_hwnd.py            # hide/show own windows
    python -u tools/diag_floating_hwnd.py --foreground
        # additionally bounce the real foreground away (Progman) and back so
        # check_foreground_window() itself does the hiding, not just our own
        # hide()/show() calls.

Uses an isolated APPDATA inside the repo; never touches the real
%APPDATA%\\Colorink config.
"""

import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SANDBOX = os.path.join(_ROOT, ".tmp_floating_hwnd")
shutil.rmtree(_SANDBOX, ignore_errors=True)
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["APPDATA"] = _SANDBOX
sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Import every ui.* module before QApplication exists (see handoff pitfall 2).
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from ui.panels import registry, store  # noqa: E402
import ui.main_window as main_window  # noqa: E402

_KEEPALIVE = []

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def _win32():
    import win32con
    import win32gui
    return win32con, win32gui


def hwnd_of(widget):
    """Native HWND of a widget, or 0 when it has no native window yet."""
    try:
        if widget.windowHandle() is None:
            return 0
        return int(widget.winId())
    except Exception:
        return 0


def native_visible(hwnd):
    if not hwnd:
        return None
    try:
        _, win32gui = _win32()
        return bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return None


def native_rect(hwnd):
    if not hwnd:
        return None
    try:
        _, win32gui = _win32()
        return tuple(win32gui.GetWindowRect(hwnd))
    except Exception:
        return None


def snapshot(name, widget):
    hwnd = hwnd_of(widget)
    rect = native_rect(hwnd)
    vis = native_visible(hwnd)
    print(f"  {name:10s} hwnd={hwnd:#x} rect={rect} "
          f"native_visible={vis} qt_visible={widget.isVisible()} "
          f"qt_hidden={widget.isHidden()}")
    return hwnd, vis


def quiesce(win):
    """Stop timers/threads without os._exit(0) (this script must outlive it)."""
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


def build_window():
    cfg = config.load_hotkey_config()
    cfg["panelDrag"] = True
    config.save_hotkey_config(cfg)
    win = main_window.MainWindow()
    _KEEPALIVE.append(win)
    win.resize(360, 700)
    win.show()
    QApplication.processEvents()
    # Stop the foreground poller: the --foreground mode drives it explicitly.
    timer = getattr(win, "foreground_timer", None)
    if timer is not None:
        timer.stop()
    return win


def float_one(win):
    host = win.panel_host
    victim = None
    for pid in host.mounted_panels():
        widget = host.widget_for(pid)
        if widget is not None and not widget.isHidden():
            victim = pid
            break
    if victim is None:
        raise RuntimeError("no mounted visible panel to tear off")
    if not win.float_panel(victim):
        raise RuntimeError("float_panel() refused")
    QApplication.processEvents()
    floater = win.floating_windows().get(victim)
    if floater is None:
        raise RuntimeError("floating window not found")
    return victim, floater


def main():
    foreground_mode = "--foreground" in sys.argv[1:]
    app = QApplication(sys.argv)
    app.setApplicationName("Colorink")
    app.setQuitOnLastWindowClosed(False)
    _KEEPALIVE.append(app)

    cfg = config.load_hotkey_config()
    if foreground_mode:
        cfg["onlyShowInCsp"] = True
        config.save_hotkey_config(cfg)

    win = build_window()
    victim, floater = float_one(win)
    main_hwnd = hwnd_of(win)
    float_hwnd = hwnd_of(floater)
    print(f"面板 {victim} 已浮出；main={main_hwnd:#x} float={float_hwnd:#x}")

    print("== 初始（都应在屏幕上）==")
    snapshot("main", win)
    snapshot("float", floater)

    if foreground_mode:
        try:
            _, win32gui = _win32()
            progman = win32gui.FindWindow("Progman", None)
            if not progman:
                raise RuntimeError("Progman not found")
            print(f"== 切前台到 Progman({progman:#x})，由 check_foreground_window 隐藏 ==")
            win32gui.SetForegroundWindow(progman)
            time.sleep(0.7)
            win.check_foreground_window()
            QApplication.processEvents()
        except Exception as exc:
            check("前台切换准备", False, str(exc))
    else:
        print("== 直接隐藏主窗（与前台追踪器同一条 hide()/show() 路径）==")
        win.hide()
        QApplication.processEvents()

    _, main_vis = snapshot("main", win)
    _, float_vis = snapshot("float", floater)
    check("主窗 HWND 已隐藏", main_vis is False, f"native_visible={main_vis}")
    check("浮窗 HWND 已隐藏", float_vis is False, f"native_visible={float_vis}")

    if foreground_mode:
        try:
            _, win32gui = _win32()
            print(f"== 尝试把真实前台切回 Colorink({main_hwnd:#x}) ==")
            from core.foreground import bring_process_to_foreground
            brought = bring_process_to_foreground(os.getpid())
            if brought:
                time.sleep(0.7)
                win.check_foreground_window()
                QApplication.processEvents()
            else:
                print("  (bring_process_to_foreground 未找到本进程的顶层窗口；改走应用自身恢复路径)")
        except Exception as exc:
            print(f"  (真实前台恢复受限：{exc}；以下走应用自身恢复路径)")
        # The OS foreground lock can deny SetForegroundWindow for a process
        # that did not receive the last input. That is not the palette's bug:
        # in real use the user's click/hotkey brings the process forward and
        # check_foreground_window() then sees our own PID. Show + explicitly
        # restore the floats so the HWND-level check below still covers the
        # restore code path.
        win.show()
        win.raise_()
        win.set_floating_foreground_visible(True)
        QApplication.processEvents()
    else:
        print("== 重新显示主窗 ==")
        win.show()
        QApplication.processEvents()

    _, main_vis = snapshot("main", win)
    _, float_vis = snapshot("float", floater)
    check("主窗 HWND 已恢复显示", main_vis is True, f"native_visible={main_vis}")
    check("浮窗 HWND 已恢复显示", float_vis is True, f"native_visible={float_vis}")

    # Qt and native must agree after the cycle.
    check("主窗 Qt 状态与真实 HWND 一致",
          win.isVisible() == (main_vis is True),
          f"qt={win.isVisible()} native={main_vis}")
    check("浮窗 Qt 状态与真实 HWND 一致",
          floater.isVisible() == (float_vis is True),
          f"qt={floater.isVisible()} native={float_vis}")

    quiesce(win)
    failures = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS)} 项检查，{'全部通过' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    # os._exit, like the product's close_application(): Python's teardown of a
    # MainWindow that still has hotkey/sync/overlay machinery can turn a
    # clean 0 into a 0xC0000005/exit-1 after all checks printed.
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001 - diagnostics must say why it failed
        print(f"DIAG FATAL: {exc!r}", file=sys.stderr)
        code = 2
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    os._exit(code)
