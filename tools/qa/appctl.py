"""被测应用（Colorink）的启动 / 查找 / 关闭。仅 Windows。"""
from __future__ import annotations

import subprocess
import sys
import time

from .common import PROJECT_ROOT

WINDOW_TITLE = "Colorink"


def find_hwnds() -> list[int]:
    import win32gui

    found: list[int] = []

    def cb(h, _):
        try:
            t = win32gui.GetWindowText(h)
            # 只匹配 Colorink 自己的窗口（标题精确为 "Colorink"），
            # 不要误把资源管理器里打开的 "colorink - 文件资源管理器" 当成应用。
            if t and t.strip().lower() == WINDOW_TITLE.lower() and win32gui.IsWindow(h):
                found.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return found


def find_hwnd() -> int | None:
    hs = find_hwnds()
    if not hs:
        return None

    # Colorink 一个进程会有多个标题为 "Colorink" 的 Qt 顶层窗口：
    # 主窗口、离屏 OpenGL/覆盖层、隐藏图标窗口。取窗口矩形/可见性时必须用
    # 主窗口，否则会误读覆盖层或隐藏辅助窗口。
    import win32gui

    for h in hs:
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            cls = ""
        if "ToolSaveBits" in cls and "OwnDC" not in cls:
            return h

    # 兜底：优先可见、在屏内、尺寸正常的窗口。
    def score(h):
        try:
            if not win32gui.IsWindowVisible(h):
                return 0
            l, t, r, b = win32gui.GetWindowRect(h)
            if r - l < 50 or b - t < 50:
                return 1
            if l < -5000 or t < -5000:
                return 1
            return 10
        except Exception:
            return 0

    return max(hs, key=score) if hs else None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui

    return win32gui.GetWindowRect(hwnd)


def launch_source(logfile=None) -> subprocess.Popen:
    kwargs = {}
    if logfile:
        kwargs = {"stdout": logfile, "stderr": subprocess.STDOUT}
    return subprocess.Popen([sys.executable, "main.py"], cwd=str(PROJECT_ROOT), **kwargs)


def launch_exe(exe_path, logfile=None) -> subprocess.Popen:
    kwargs = {}
    if logfile:
        kwargs = {"stdout": logfile, "stderr": subprocess.STDOUT}
    return subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent), **kwargs)


def wait_window(timeout: float = 20.0) -> int | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = find_hwnd()
        if h is not None:
            return h
        time.sleep(0.3)
    return None


def close_windows(hwnds: list[int]) -> None:
    import win32con
    import win32gui

    for h in hwnds:
        try:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass


def terminate(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def process_alive(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


def count_processes(name: str = "Colorink.exe") -> int:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:
        return -1
    return sum(1 for ln in out.splitlines() if ln.lower().startswith(f'"{name.lower()}"'))


def kill_all(name: str = "Colorink.exe") -> None:
    """Force-kill every process with the given image name.

    PyInstaller onefile has a bootloader parent + child; terminating only the
    Popen handle can leave the child running.  QA cleanup uses this to ensure
    no leftover Colorink.exe survives between checks.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass
