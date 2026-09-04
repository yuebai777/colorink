"""Drawing-app foreground detection and process-to-foreground helpers.

Pure matching helpers (no win32 calls) plus the Windows-specific process
resolution used by the "only show while a drawing app is in the foreground"
tracker. Extracted from the main-window god class so the matching logic stays
unit-testable without a live Windows session.
"""

import json
import os
import re

# Drawing applications recognized by the "only show while the drawing app is
# in the foreground" tracker (onlyShowInCsp). Process basenames are matched
# with the ".exe" extension stripped; window titles are lowercased.
# Drawing applications recognized by the "only show while the drawing app is
# in the foreground" tracker (onlyShowInCsp) and automatic sync switching.
_DRAWING_APP_MODES = (
    ("photoshop", "ps"),        # Adobe Photoshop
    ("sai", "sai"),              # PaintTool SAI 1.x / 2.x (sai.exe / sai2.exe)
    ("clipstudiopaint", "csp"),  # CLIP Studio Paint main + painting process
    ("clipstudio", "csp"),       # CSP launcher / companion processes
    ("udmpaint", "udm"),         # UDM Paint (UDMPaintPro.exe / UDMPaintEx.exe)
)

_DRAWING_APP_EXE_MARKERS = tuple(marker for marker, _ in _DRAWING_APP_MODES)

# Executables known to be non-drawing applications (browsers, shells, editors, chat).
# When the foreground PID resolves to any of these, title fallback is strictly denied.
_KNOWN_NON_DRAWING_EXES = {
    # Web browsers
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera_gx.exe", "vivaldi.exe", "360chrome.exe", "360se.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "liebao.exe", "maxthon.exe", "waterfox.exe", "tor.exe",
    # System shells & desktop
    "explorer.exe", "taskmgr.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe", "lockapp.exe", "systemsettings.exe",
    # Dev tools & text editors
    "code.exe", "devenv.exe", "idea64.exe", "pycharm64.exe", "clion64.exe", "webstorm64.exe",
    "rider64.exe", "sublime_text.exe", "notepad.exe", "notepad++.exe", "windowsterminal.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "git-bash.exe",
    # Office & communication
    "wechat.exe", "qq.exe", "dingtalk.exe", "feishu.exe", "lark.exe", "slack.exe",
    "discord.exe", "telegram.exe", "winword.exe", "excel.exe", "powerpnt.exe",
    "wps.exe", "wpp.exe", "et.exe", "acrobat.exe", "foxitreader.exe",
    # Media & entertainment
    "bilibili.exe", "cloudmusic.exe", "qqmusic.exe", "spotify.exe", "steam.exe",
}

# Window classes that belong to browsers or system shells.
_KNOWN_NON_DRAWING_CLASSES = {
    "chrome_widgetwin_1",
    "chrome_widgetwin_0",
    "mozillawindowclass",
    "cabinetwclass",
    "workerw",
    "progman",
    "shell_traywnd",
    "shell_secondarytraywnd",
    "windows.ui.core.corewindow",
    "applicationframewindow",
    "cascadia_hosting_window_class",
    "consolewindowclass",
}

# Browser/platform keywords in window titles that indicate web page titles.
_BROWSER_TITLE_MARKERS = (
    "google chrome",
    "microsoft edge",
    "mozilla firefox",
    "brave",
    "opera",
    "vivaldi",
    "360安全浏览器",
    "360极速浏览器",
    "qq浏览器",
    "搜狗高速浏览器",
    "猎豹安全浏览器",
    "哔哩哔哩",
    "bilibili",
    "百度搜索",
    "知乎",
    "youtube",
)


def is_known_non_drawing_exe(exe_name: str) -> bool:
    """True if exe_name is a known non-drawing application (e.g. browser or shell)."""
    if not exe_name:
        return False
    name = os.path.basename(exe_name).lower()
    return name in _KNOWN_NON_DRAWING_EXES


def is_known_non_drawing_class(cls_name: str) -> bool:
    """True if cls_name is a known non-drawing window class (e.g. Chrome_WidgetWin_1)."""
    if not cls_name:
        return False
    return cls_name.strip().lower() in _KNOWN_NON_DRAWING_CLASSES


def identify_drawing_app(
    exe_name: str = "",
    title: str = "",
    win_class: str = "",
) -> str | None:
    """Identify which drawing software is active ('ps', 'sai', 'csp', 'udm', or None).

    Multi-layer defense:
    1. Process name is authoritative: if exe matches a drawing app, returns mode.
    2. If exe is a known non-drawing app (browser, shell, etc.), returns None immediately.
    3. If window class belongs to a browser/shell, returns None immediately.
    4. Title check only runs if exe is not a known non-drawing process, and strictly rejects
       browser titles (e.g. tutorials with 'photoshop' or 'sai' in tab title).
    """
    if exe_name:
        stem = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name
        stem = stem.lower()
        for marker, mode in _DRAWING_APP_MODES:
            if marker in stem:
                return mode
        if is_known_non_drawing_exe(exe_name):
            return None

    if win_class and is_known_non_drawing_class(win_class):
        return None

    if title:
        title_lower = title.lower()
        # Reject web browser windows (e.g. "- Google Chrome", "- Microsoft Edge", "Bilibili")
        if any(marker in title_lower for marker in _BROWSER_TITLE_MARKERS):
            return None

        # Check drawing app title patterns
        if "photoshop" in title_lower:
            if ("adobe photoshop" in title_lower or title_lower.endswith("photoshop")
                    or "photoshop 20" in title_lower or "photoshop cc" in title_lower):
                return "ps"
        if "clip studio paint" in title_lower or "优动漫" in title_lower or "clip studio" in title_lower:
            return "csp"
        if re.search(r"(?<![a-z0-9])sai", title_lower):
            if (title_lower.startswith("sai") or "sai ver" in title_lower
                    or "paint tool sai" in title_lower or "- sai" in title_lower):
                return "sai"
        if "udm paint" in title_lower or re.search(r"(?<![a-z0-9])udm", title_lower):
            return "udm"

    return None


def _exe_matches_drawing_app(exe_name: str) -> bool:
    """True if a lowercased process basename belongs to a drawing app."""
    return identify_drawing_app(exe_name=exe_name) is not None


def _title_matches_drawing_app(title: str) -> bool:
    """True if a window title belongs to a genuine drawing app window.

    Browser pages containing 'photoshop' or 'sai' in their titles are rejected.
    """
    return identify_drawing_app(title=title) is not None


def find_running_drawing_software() -> str | None:
    """Find any currently running drawing application (ps, sai, csp, or udm)."""
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info.get("name")
                if name:
                    app = identify_drawing_app(exe_name=name)
                    if app:
                        return app
            except Exception:
                continue
    except Exception:
        pass
    return None


def has_saved_companion_session() -> bool:
    """True if a valid CSP companion session file exists in AppData."""
    try:
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            appdata = os.path.expanduser("~")
        path = os.path.join(appdata, "Colorink", "csp_companion_session.json")
        if not os.path.isfile(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("host") and data.get("port") and data.get("password"))
    except Exception:
        return False


def resolve_auto_sync_mode(
    detected_app: str | None,
    current_mode: str | None = None,
    has_companion_session: bool | None = None,
) -> str | None:
    """Resolve the target sync channel from a detected drawing application.

    When CSP is detected ('csp'), companion mode ('companion') is prioritized
    if a saved companion session exists or if Colorink is already in companion mode.
    If no companion session exists and not in companion mode, falls back to
    memory sync ('csp').
    """
    if detected_app is None:
        return None
    if detected_app == "csp":
        if has_companion_session is None:
            has_companion_session = has_saved_companion_session()
        if has_companion_session or current_mode == "companion":
            return "companion"
        return "csp"
    return detected_app


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
