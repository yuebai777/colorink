import sys
import os
import ctypes
import struct
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QSharedMemory

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

def _is_process_running(pid: int) -> bool:
    """Check if a Windows process with the given PID is still running."""
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False

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
    if shared_mem.create(4):
        pid_bytes = struct.pack('I', os.getpid())
        shared_mem.lock()
        ctypes.memmove(int(shared_mem.data()), pid_bytes, 4)
        shared_mem.unlock()
    return shared_mem

def main():
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

    # Load and apply window icon
    icon_path = os.path.join("icons", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Launch main window
    window = MainWindow()
    window.show()

    # Execute application main loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
