"""Drawing-app foreground detection and process-to-foreground helpers.

Pure matching helpers (no win32 calls) plus the Windows-specific process
resolution used by the "only show while a drawing app is in the foreground"
tracker. Extracted from the main-window god class so the matching logic stays
unit-testable without a live Windows session.
"""

import os
import re

# Drawing applications recognized by the "only show while the drawing app is
# in the foreground" tracker (onlyShowInCsp). Process basenames are matched
# with the ".exe" extension stripped; window titles are lowercased.
_DRAWING_APP_EXE_MARKERS = (
    "clipstudiopaint",   # CLIP Studio Paint main + CLIPStudioPaintApp painting process
    "clipstudio",        # CSP launcher / companion processes
    "sai",               # PaintTool SAI 1.x / 2.x (sai.exe / sai2.exe)
    "udmpaint",          # UDM Paint (UDMPaintPro.exe / UDMPaintEx.exe)
    "photoshop",         # Adobe Photoshop
)


def _exe_matches_drawing_app(exe_name: str) -> bool:
    """True if a lowercased process basename belongs to a drawing app.

    The extension is stripped first so "sai2.exe" and "sai.exe" both match
    the same "sai" marker.
    """
    stem = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name
    return any(marker in stem for marker in _DRAWING_APP_EXE_MARKERS)


def _title_matches_drawing_app(title: str) -> bool:
    """True if a lowercased window title belongs to a drawing app.

    Latin app names are matched at a word boundary so titles like
    "Photosai" can't false-positive on the "sai" marker, while real-world
    titles such as "SAI Ver.2" or "paint tool sai" still match.
    """
    if "clip studio paint" in title or "优动漫" in title or "photoshop" in title:
        return True
    if re.search(r"(?<![a-z0-9])sai", title):  # SAI / SAI Ver.2 / paint tool sai
        return True
    if re.search(r"(?<![a-z0-9])udm", title):  # UDM Paint
        return True
    return False


def _resolve_process_exe(pid: int) -> str:
    """Resolve a PID to its executable basename (lowercased).

    psutil first; if it fails (elevated / protected process, antivirus
    interference) fall back to QueryFullProcessImageNameW via ctypes so the
    foreground check keeps working for admin-run drawing apps.
    """
    try:
        import psutil
        exe = psutil.Process(pid).exe()
        if exe:
            return os.path.basename(exe).lower()
    except Exception:
        pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        # 64 位安全：HANDLE 按 c_void_p 接收，避免句柄截断。
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.QueryFullProcessImageNameW.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
        )
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def bring_process_to_foreground(pid: int) -> bool:
    import ctypes
    user32 = ctypes.windll.user32

    hwnd_to_focus = None

    def enum_windows_callback(hwnd, lParam):
        nonlocal hwnd_to_focus
        if user32.IsWindowVisible(hwnd):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                parent = user32.GetParent(hwnd)
                owner = user32.GetWindow(hwnd, 4)  # GW_OWNER = 4
                if parent == 0 or parent is None:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        # Prefer ownerless window (main window)
                        if owner == 0 or owner is None:
                            hwnd_to_focus = hwnd
                            return False  # Stop enumeration
                        else:
                            if hwnd_to_focus is None:
                                hwnd_to_focus = hwnd
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    callback = WNDENUMPROC(enum_windows_callback)
    user32.EnumWindows(callback, 0)

    if hwnd_to_focus:
        is_minimized = user32.IsIconic(hwnd_to_focus)
        user32.ShowWindowAsync(hwnd_to_focus, 9 if is_minimized else 5)  # 9 = SW_RESTORE, 5 = SW_SHOW
        user32.BringWindowToTop(hwnd_to_focus)
        user32.SetForegroundWindow(hwnd_to_focus)
        return True
    return False
