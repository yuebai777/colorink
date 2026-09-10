"""Pure-Win32 shell-zone detection for the global mouse-hotkey gate.

Answers one question: "is this physical screen coordinate on top of a
Windows shell surface?" (taskbar, tray, tray overflow flyout, quick
settings, notification center, jump lists, ...). ``core.global_hotkeys``
uses it to let mouse-button hotkeys fall through to the system instead of
popping Colorink's always-on-top panel over native context menus.

No Qt anywhere in this module: the gate runs on the mouse library's
handler thread (its WH_MOUSE_LL hook proc only does ``queue.put``), and Qt
GUI APIs are not thread-safe off the GUI thread. All coordinates are
physical pixels, matching ``GetCursorPos`` / ``WindowFromPoint`` /
``APPBARDATA.rc``. Everything is fail-open: a missing pywin32, a failed
API call, or any unexpected exception means "not a shell zone", so the
hotkey fires exactly as it did before this filter existed.

Threading model
---------------
The gate path (``ignore_at_cursor``) NEVER enumerates windows:
``EnumWindows`` and ``SHAppBarMessage`` send messages to explorer.exe and
can block the calling thread when the shell is busy or restarting. A
background daemon thread refreshes the taskbar-rect snapshot every
``_TASKBAR_CACHE_TTL`` seconds; the gate only reads the latest immutable
snapshot (a tuple, swapped atomically). While the snapshot is empty
(background thread not started yet, or the last enumeration failed) the
gate falls back to the ``WindowFromPoint`` class check alone.
"""

import ctypes
import os
import threading
from ctypes import wintypes

try:
    import win32api
    import win32con
    import win32gui
    import win32process
    _AVAILABLE = True
except Exception:  # pragma: no cover - non-Windows / pywin32 missing
    win32api = win32con = win32gui = win32process = None
    _AVAILABLE = False

# ── AppBar / shell constants ────────────────────────────────────────────────

ABM_GETSTATE = 0x00000004
ABM_GETTASKBARPOS = 0x00000005
ABS_AUTOHIDE = 0x0000001

_ABE_LEFT = 0
_ABE_TOP = 1
_ABE_RIGHT = 2
_ABE_BOTTOM = 3

# Root-window class names that are always Windows shell UI.
_SHELL_CLASSES = {
    "Shell_TrayWnd",             # primary taskbar
    "Shell_SecondaryTrayWnd",    # secondary-monitor taskbars
    "TrayNotifyWnd",             # notification area
    "NotifyIconOverflowWindow",  # Win10 tray overflow flyout
    "SysPager",
    "ToolbarWindow32",           # only ever matches via the ancestor check
}

# Win11 22H2+ renders quick settings, the notification center / calendar
# flyout and the tray overflow menu as XAML islands owned by explorer.exe.
# These class names are generic (any WinUI / UWP popup also uses
# Xaml_WindowedPopupClass), so they only count as shell UI when the owning
# process is in _SHELL_PROCESSES.
_XAML_SHELL_CLASSES = {
    "Xaml_WindowedPopupClass",
    "TopLevelWindowForOverflowXamlIsland",
}
_SHELL_PROCESSES = {"explorer.exe"}

_TASKBAR_CACHE_TTL = 1.0  # seconds between background snapshot refreshes
_AUTOHIDE_EDGE_PX = 2     # collapsed strip for an auto-hidden taskbar


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


# ── Taskbar-rect snapshot (background-refresh only; gate path never blocks) ──

_rects_snapshot: tuple = ()  # tuple of (left, top, right, bottom), physical px
_refresh_thread = None
_refresh_wake = threading.Event()


def _sh_app_bar_message(message, data):
    """SHAppBarMessage wrapper — isolated so tests can patch out the live
    shell RPC (which may block when explorer is busy)."""
    return ctypes.windll.shell32.SHAppBarMessage(message, ctypes.byref(data))


def _enum_shell_taskbar_hwnds():
    """All top-level taskbar hwnds (primary + every secondary monitor)."""
    hwnds = []

    def _collect(hwnd, _extra):
        try:
            if win32gui.GetClassName(hwnd) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                hwnds.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_collect, None)
    return hwnds


def _enum_taskbar_rects():
    """Collect every taskbar's physical rect via SHAppBarMessage.

    Blocking is fine here — this only runs on the background thread.
    EnumWindows covers Shell_TrayWnd + every Shell_SecondaryTrayWnd, so
    each monitor's taskbar is included. An auto-hidden taskbar occupies no
    screen space until hovered, so its rect is collapsed to a thin strip
    on the screen edge it is docked to (otherwise the zone would vanish).
    """
    hwnds = _enum_shell_taskbar_hwnds()

    state_data = _APPBARDATA()
    state_data.cbSize = ctypes.sizeof(_APPBARDATA)
    state = _sh_app_bar_message(ABM_GETSTATE, state_data)
    autohide = bool(state & ABS_AUTOHIDE)

    rects = []
    for hwnd in hwnds:
        data = _APPBARDATA()
        data.cbSize = ctypes.sizeof(_APPBARDATA)
        data.hWnd = hwnd
        if not _sh_app_bar_message(ABM_GETTASKBARPOS, data):
            continue
        left, top = data.rc.left, data.rc.top
        right, bottom = data.rc.right, data.rc.bottom
        if autohide:
            # Collapse to a 2px strip on the docked (inner) screen edge.
            if data.uEdge == _ABE_TOP:
                bottom = top + _AUTOHIDE_EDGE_PX
            elif data.uEdge == _ABE_LEFT:
                right = left + _AUTOHIDE_EDGE_PX
            elif data.uEdge == _ABE_RIGHT:
                left = right - _AUTOHIDE_EDGE_PX
            else:  # _ABE_BOTTOM and any unknown edge
                top = bottom - _AUTOHIDE_EDGE_PX
        rects.append((left, top, right, bottom))
    return rects


def _refresh_snapshot():
    global _rects_snapshot
    try:
        _rects_snapshot = tuple(_enum_taskbar_rects())
    except Exception:
        pass  # keep the previous (possibly empty) snapshot


def _refresher_loop():
    while True:
        _refresh_snapshot()
        _refresh_wake.wait(_TASKBAR_CACHE_TTL)
        _refresh_wake.clear()


def start_background_refresh():
    """Do one synchronous refresh (called from the GUI thread at bind time)
    and start the daemon that keeps the snapshot fresh. Idempotent."""
    global _refresh_thread
    if not _AVAILABLE:
        return
    _refresh_snapshot()
    if _refresh_thread is None or not _refresh_thread.is_alive():
        _refresh_thread = threading.Thread(
            target=_refresher_loop, daemon=True, name="colorink-zone-rects")
        _refresh_thread.start()


def invalidate():
    """Ask the background thread to refresh sooner. Never blocks, never
    enumerates on the calling thread."""
    _refresh_wake.set()


def taskbar_rects():
    """Latest taskbar-rect snapshot ((l, t, r, b) physical px). May be empty."""
    return _rects_snapshot


# ── Point-in-zone checks ────────────────────────────────────────────────────


def _in_any_rect(rects, x, y):
    """Half-open containment test. Coordinates are signed physical pixels,
    so a secondary monitor left of / above the primary (negative origin,
    e.g. x=-1920) compares correctly — never abs() or unsigned-cast here."""
    for left, top, right, bottom in rects:
        if left <= x < right and top <= y < bottom:
            return True
    return False


def _root_window_at(x, y):
    """(root hwnd, root class name) of the window under the point."""
    hwnd = win32gui.WindowFromPoint((x, y))
    if not hwnd:
        return 0, ""
    root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
    return root, win32gui.GetClassName(root)


def _process_image_name(hwnd):
    """Lowercased basename of the executable owning *hwnd*, "" on failure."""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        return ""
    handle = None
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        return os.path.basename(win32process.GetModuleFileNameEx(handle, 0)).lower()
    except Exception:
        return ""
    finally:
        if handle:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def is_shell_window(x, y):
    """True when the window under (x, y) belongs to Windows shell UI that is
    NOT covered by the taskbar rects: tray menus, jump lists, overflow
    panels, quick settings, notification center, clock/calendar flyout."""
    if not _AVAILABLE:
        return False
    try:
        root, class_name = _root_window_at(x, y)
        if not class_name:
            return False
        if class_name in _SHELL_CLASSES:
            return True
        if class_name in _XAML_SHELL_CLASSES:
            # Generic XAML popup class — only shell when explorer-owned.
            return _process_image_name(root) in _SHELL_PROCESSES
        return False
    except Exception:
        return False


def should_ignore(x, y):
    """True when a mouse-hotkey at physical (x, y) should fall through to the
    system. Fail-open: any error means False (hotkey fires as before)."""
    if not _AVAILABLE:
        return False
    try:
        if _in_any_rect(taskbar_rects(), x, y):
            return True
        return is_shell_window(x, y)
    except Exception:
        return False


def ignore_at_cursor():
    """``should_ignore`` at the current physical cursor position."""
    if not _AVAILABLE:
        return False
    try:
        x, y = win32api.GetCursorPos()
        return should_ignore(x, y)
    except Exception:
        return False


def is_available():
    """False when pywin32 is missing — callers degrade to "never filter"."""
    return _AVAILABLE
