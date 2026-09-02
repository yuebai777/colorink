import ctypes
import os
import struct
import sys

from PyQt6.QtCore import QSharedMemory
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# Ensure working directory is set to script directory
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
if script_dir not in sys.path:
    sys.path.append(script_dir)
core_dir = os.path.join(script_dir, "core")
if core_dir not in sys.path:
    sys.path.append(core_dir)

from ui.main_window import MainWindow, bring_process_to_foreground

SINGLE_INSTANCE_KEY = "ColorinkPaletteLitePyQt_SingleInstance_v1"
#: A packaged build may be auto-started at login (or left open from a test)
#: and hold the single-instance lock; a source run must not then silently
#: exit — that is how "用源码启动打不开" happens. Keep source launches on
#: their own lock so they can run side by side with a packaged instance.
if not getattr(sys, "frozen", False):
    SINGLE_INSTANCE_KEY += "_dev"

def _is_process_running(pid: int) -> bool:
    """Check if a Windows process with the given PID is still running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    # 64 位安全：HANDLE 必须按 c_void_p 接收，否则高句柄值会被截断成 0，
    # 把仍在运行的实例误判为"已死"并放行第二个实例。
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # OpenProcess can return access-denied for a live elevated process.  In
    # that case the PID is almost certainly still running; treat it as alive
    # instead of allowing a second instance to take over the lock.
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED

def _acquire_instance_lock() -> QSharedMemory | None:
    """
    Acquire single-instance lock via QSharedMemory.
    Returns the QSharedMemory instance if this is the first instance (MUST keep alive),
    or None if another instance already has the lock (caller should exit).
    On duplicate, focuses the existing window before returning None.
    """
    shared_mem = QSharedMemory(SINGLE_INSTANCE_KEY)

    if shared_mem.create(4):
        # First instance — store PID and return the lock
        pid_bytes = struct.pack('I', os.getpid())
        shared_mem.lock()
        ctypes.memmove(int(shared_mem.data()), pid_bytes, 4)
        shared_mem.unlock()
        return shared_mem

    # Shared memory already exists — another instance may be running
    if not shared_mem.attach():
        return shared_mem  # Can't attach: allow to continue anyway

    shared_mem.lock()
    pid_bytes = ctypes.string_at(int(shared_mem.data()), 4)
    existing_pid = struct.unpack('I', pid_bytes)[0]
    shared_mem.unlock()
    shared_mem.detach()

    if _is_process_running(existing_pid):
        bring_process_to_foreground(existing_pid)
        return None  # Duplicate — exit

    # Dead process from previous crash — take over
    for attempt in range(3):
        if shared_mem.create(4):
            pid_bytes = struct.pack('I', os.getpid())
            shared_mem.lock()
            ctypes.memmove(int(shared_mem.data()), pid_bytes, 4)
            shared_mem.unlock()
            return shared_mem
        # 旧进程的段可能尚未被系统完全释放（僵尸句柄等）——短暂重试，
        # 避免直接落回"无锁运行"放行第二个实例。
        import time as _time
        _time.sleep(0.5)
    return shared_mem

def _log_exception(exc_type, exc_value, exc_tb):
    """Global exception hook — write to stderr.log so crashes leave a trace,
    and drop a crash marker for the next-launch prompt."""
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) + "\n"
    # onefile 打包后 __file__/cwd 指向 _MEIxxxx 临时解包目录，进程退出即被
    # 删除——崩溃日志必须写到用户数据目录，否则"打开日志文件"永远找不到。
    if getattr(sys, "frozen", False):
        from core import config as _config
        log_dir = _config.get_user_data_dir()
    else:
        log_dir = "."
    log_path = os.path.abspath(os.path.join(log_dir, "stderr.log"))
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        traceback.print_exc()
    # Best-effort marker for the next-launch "上次运行发生异常" prompt.
    # Never let marker-writing itself raise out of the exception hook.
    try:
        from core import crash_report
        crash_report.write_crash_marker(text, log_path=log_path)
    except Exception:
        pass


def _prompt_previous_crash(crash):
    """Show a one-time dialog when the previous run ended with an uncaught
    exception, offering to copy or open the saved log."""
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QMessageBox
    from core import i18n

    tb = (crash.get("traceback") or "").strip()
    snippet = tb if len(tb) <= 1200 else tb[:1200] + "\n\u2026"
    box = QMessageBox()
    box.setWindowTitle("Colorink")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(i18n.tr("检测到 Colorink 上次运行发生异常。"))
    box.setInformativeText(i18n.tr("已保存错误日志，可复制或打开查看：") + "\n\n" + snippet)
    copy_btn = box.addButton(i18n.tr("复制错误信息"), QMessageBox.ButtonRole.ActionRole)
    open_btn = box.addButton(i18n.tr("打开日志文件"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(i18n.tr("关闭"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is copy_btn:
        try:
            QGuiApplication.clipboard().setText(tb)
        except Exception:
            pass
    elif clicked is open_btn:
        log_path = crash.get("log_path")
        if log_path and os.path.exists(log_path):
            target = log_path
        elif log_path:
            target = os.path.dirname(log_path)
        else:
            target = "."
        try:
            os.startfile(target)
        except Exception:
            pass

def main():
    # Install global exception hook early
    sys.excepthook = _log_exception

    # Set explicit AppUserModelID on Windows for proper taskbar grouping
    if sys.platform == 'win32':
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("colorink.palette-lite.pyqt.1.0")
        except Exception:
            pass

    # Initialize QApplication (needed before QSharedMemory)
    app = QApplication(sys.argv)
    app.setApplicationName("Colorink")
    app.setQuitOnLastWindowClosed(False)

    # Single-instance guard — lock MUST stay alive for app lifetime
    lock = _acquire_instance_lock()
    if lock is None:
        sys.exit(0)

    # Re-assert the HKCU Run entry so it always matches the persisted
    # "openAtLogin" setting. The portable EXE may have been re-downloaded/moved
    # since the entry was written, which leaves Windows pointing at a stale
    # path and "开机自启动" silently stops working; conversely a stale entry
    # must not survive when the user has the setting off. apply_autostart() is
    # idempotent and no-ops outside a frozen Windows build.
    from core import autostart as _autostart
    from core import config as _config
    _autostart.apply_autostart(
        bool(_config.load_hotkey_config().get("openAtLogin", False))
    )

    # Load and apply window icon
    icon_path = os.path.join("icons", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Launch main window.  With "仅在绘画软件前台时显示" enabled, the
    # foreground tracker may have already decided the window should stay
    # hidden (no drawing app is in the foreground); don't override that
    # decision with an unconditional show().
    window = MainWindow()
    if not getattr(window, "auto_hidden", False):
        window.show()

    # If the previous run crashed (uncaught exception), offer to inspect the
    # log once, then clear the marker so it is never announced twice.
    from core import crash_report
    crash = crash_report.detect_previous_crash()
    if crash:
        crash_report.clear_crash_marker()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(600, lambda c=crash: _prompt_previous_crash(c))

    # Execute application main loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
