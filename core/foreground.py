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


# ── 笔尖焦点交还（WinTab 首笔压感） ────────────────────────────────────
#
# 机制：Photoshop 在 WinTab 模式（PSUserConfig: UseSystemStylus 0）下，压感包
# 只会送给"当前拥有激活/键盘焦点"的那个窗口的 Wintab 上下文。别的窗口一旦把
# 激活或焦点拿走，PS 会挂起自己的上下文（WTEnable(FALSE)）；等用户落笔时才重新
# 握手，而握手期间到达的第一包 —— 恰好是带压力的那一包 —— 就丢了。表现就是
# 首笔满压感粗斑 / 折线，第二笔正常。
#
# 为什么别的外部取色工具不中招：它们要么停靠在 Photoshop 自己的窗口里（PS 始终
# 是活动的顶层窗口），要么根本不需要笔点进它们的窗口（全局热键 + 悬停采样，笔
# 一直待在画布上）。Colorink 必须让笔点进自己的窗口，于是要在交互结束、笔还在
# 往画布移动的路上，就把激活 + 焦点交还回去 —— 让 WinTab 上下文在落笔之前恢复，
# 而不是在落笔那一刻才恢复。
#
# 安全边界（三条都满足才动手）：
#   * capture 时前台是本进程以外的窗口（否则本来就是我们，没什么可还的），
#   * restore 时前台或键盘焦点确实落在我们手上（用户没跑去用别的软件），
#   * 目标窗口仍然存在、可见、未被最小化。
# 另外只挂在"笔"的事件上，鼠标交互不碰 —— 免得用户用笔点数值框之后没法打字。
#
# COLORINK_FOCUS_GUARD=0   关掉
# COLORINK_FOCUS_GUARD=force 无条件交还（诊断用：验证"预恢复上下文"是否有效）

_FOCUS_GUARD_OFF = ("0", "false", "no", "off")


def _focus_debug(*parts) -> None:
    """COLORINK_DEBUG_PEN=1 时打印笔尖焦点交还的决策过程。

    和 ``ui.window.layout._pen_debug`` 共用同一个开关（``run_pen_debug.bat``），
    这样"到底有没有采集到、为什么没还"在客户机上是可以直接看到的，不用猜。
    """
    if os.environ.get("COLORINK_DEBUG_PEN"):
        print("[focus]", *parts, flush=True)


import ctypes
from ctypes import wintypes


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class _MENUBARINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcBar", wintypes.RECT),
        ("hMenu", wintypes.HMENU),
        ("hwndMenu", wintypes.HWND),
        ("fBarFocused_fFocused", wintypes.DWORD),
    ]


# Win32 GUIThreadInfo flags for active menu / popup menu loops
GUI_INMENUMODE = 0x00000004
GUI_SYSTEMMENUMODE = 0x00000008
GUI_POPUPMENUMODE = 0x00000010
_GUI_MENU_FLAGS = GUI_INMENUMODE | GUI_SYSTEMMENUMODE | GUI_POPUPMENUMODE

# Window classes that represent native or toolkit dropdown / popup menus
_POPUP_MENU_CLASSES = {
    "#32768",      # Standard Win32 popup menu (CSP menu bar, right-click menus, SAI, UDM)
    "combolbox",   # Standard Win32 ComboBox dropdown list
    "owl.menu",    # Adobe Photoshop menu bar / context menu popup
}

_POPUP_MENU_CLASS_MARKERS = (
    "popupmenu",
    "dropdown",
    "menusite",
    "qwindowpopup",
    "popupsavebits",
    "contextmenu",
)

# Helper/system popup classes that are never user-opened dropdown menus
_NON_MENU_POPUP_CLASSES = {
    "tooltips_class32",
    "#32774",
    "sysshadow",
    "ime",
    "msctfime ui",
    "uac_inputindicatoroverlaywnd",
    "uac input indicator",
    "workerw",
    "progman",
    "static",
    "button",
    "d3dproxywindow",
    "cicerouiwndframe",
}

_NON_MENU_CLASS_PREFIXES = (
    "uac_",
    "uac ",
)

_WS_POPUP = 0x80000000
_WS_CHILD = 0x40000000
_WS_BORDER = 0x00800000
_WS_DLGFRAME = 0x00400000
_WS_CAPTION = _WS_BORDER | _WS_DLGFRAME
_WS_THICKFRAME = 0x00040000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
# Check _WS_DLGFRAME rather than _WS_CAPTION so popup menus styled with
# WS_POPUP | WS_BORDER (1px border, no title bar) are not rejected.
_NON_MENU_STYLE_MASK = (
    _WS_CHILD
    | _WS_DLGFRAME
    | _WS_THICKFRAME
    | _WS_SYSMENU
    | _WS_MINIMIZEBOX
    | _WS_MAXIMIZEBOX
)
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_NOACTIVATE = 0x08000000
_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_GCL_STYLE = -26
_CS_SAVEBITS = 0x00000800
_CS_DROPSHADOW = 0x00020000
_MENU_CLASS_STYLE_FLAGS = _CS_SAVEBITS | _CS_DROPSHADOW
_GW_OWNER = 4
_OBJID_MENU = -3


def _menu_debug(*parts) -> None:
    """COLORINK_DEBUG_MENU=1 时打印下拉菜单检测的诊断日志。"""
    if os.environ.get("COLORINK_DEBUG_MENU"):
        print("[menu]", *parts, flush=True)


def _is_popup_menu_class(cls_name: str, *, same_process: bool = False) -> bool:
    """True if *cls_name* identifies a popup/dropdown menu window."""
    if not cls_name:
        return False
    lowered = cls_name.strip().lower()
    if lowered in _POPUP_MENU_CLASSES:
        return True
    if same_process and any(marker in lowered for marker in _POPUP_MENU_CLASS_MARKERS):
        return True
    return False


def _get_class_style(hwnd: int, win32gui_module=None) -> int:
    """Return GCL_STYLE bitmask for *hwnd*, or 0 if unavailable."""
    get_cls_long = getattr(win32gui_module, "GetClassLong", None) if win32gui_module else None
    if callable(get_cls_long):
        try:
            return int(get_cls_long(int(hwnd), _GCL_STYLE) or 0) & 0xFFFFFFFF
        except Exception:
            pass
    try:
        user32 = ctypes.windll.user32
        fn = getattr(user32, "GetClassLongPtrW", None) or getattr(user32, "GetClassLongW", None)
        if fn is not None:
            return int(fn(wintypes.HWND(int(hwnd)), _GCL_STYLE) or 0) & 0xFFFFFFFF
    except Exception:
        pass
    return 0


def _get_window_owner(hwnd: int, win32gui_module=None) -> int:
    """Return owner HWND (GW_OWNER) for *hwnd*, or 0 if none/unavailable."""
    get_window = getattr(win32gui_module, "GetWindow", None) if win32gui_module else None
    if callable(get_window):
        try:
            return int(get_window(int(hwnd), _GW_OWNER) or 0)
        except Exception:
            pass
    try:
        return int(ctypes.windll.user32.GetWindow(wintypes.HWND(int(hwnd)), _GW_OWNER) or 0)
    except Exception:
        return 0


def _query_menu_bar_open(fg_hwnd: int, win32gui_module=None) -> bool:
    """Return True if *fg_hwnd*'s native Win32 menu bar currently owns an open submenu HWND."""
    if not fg_hwnd:
        return False
    get_mbi = getattr(win32gui_module, "GetMenuBarInfo", None) if win32gui_module else None
    if callable(get_mbi):
        try:
            info = get_mbi(int(fg_hwnd), _OBJID_MENU, 0)
            if isinstance(info, dict):
                return bool(info.get("hwndMenu"))
            return bool(getattr(info, "hwndMenu", 0))
        except Exception:
            pass
    try:
        mbi = _MENUBARINFO()
        mbi.cbSize = ctypes.sizeof(_MENUBARINFO)
        if ctypes.windll.user32.GetMenuBarInfo(
            wintypes.HWND(int(fg_hwnd)), _OBJID_MENU, 0, ctypes.byref(mbi)
        ):
            return bool(mbi.hwndMenu)
    except Exception:
        pass
    return False


def _rects_match_shadow(
    popup_rect: tuple[int, int, int, int],
    shadow_rect: tuple[int, int, int, int],
) -> bool:
    """True when *shadow_rect* aligns with a SysShadow window cast by *popup_rect*."""
    pl, pt, pr, pb = popup_rect
    sl, st, sr, sb = shadow_rect
    if sr <= pl or sb <= pt or pr <= pl or pb <= pt:
        return False
    return (
        abs(sl - pl) <= 12
        and abs(st - pt) <= 12
        and 0 <= (sr - pr) <= 24
        and 0 <= (sb - pb) <= 24
    )


def _query_thread_menu_info(tid: int, win32gui_module=None) -> tuple[bool, int, int, int]:
    """Query GUI thread menu state and active/focus/capture HWNDs for *tid*.

    Returns ``(in_menu_mode, hwnd_active, hwnd_focus, hwnd_capture)``.
    """
    if win32gui_module and hasattr(win32gui_module, "GetGUIThreadInfo"):
        try:
            info = win32gui_module.GetGUIThreadInfo(int(tid or 0))
            flags = int(getattr(info, "flags", 0) or 0)
            menu_owner = int(getattr(info, "hwndMenuOwner", 0) or 0)
            active = int(getattr(info, "hwndActive", 0) or 0)
            focus = int(getattr(info, "hwndFocus", 0) or 0)
            capture = int(getattr(info, "hwndCapture", 0) or 0)
            return bool((flags & _GUI_MENU_FLAGS) or menu_owner), active, focus, capture
        except Exception:
            pass
    try:
        gui = _GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(int(tid or 0), ctypes.byref(gui)):
            flags = int(gui.flags or 0)
            menu_owner = int(gui.hwndMenuOwner or 0)
            active = int(gui.hwndActive or 0)
            focus = int(gui.hwndFocus or 0)
            capture = int(gui.hwndCapture or 0)
            return bool((flags & _GUI_MENU_FLAGS) or menu_owner), active, focus, capture
    except Exception:
        pass
    return False, 0, 0, 0


def _is_custom_popup_menu(
    hwnd_int: int,
    fg_hwnd_int: int,
    style: int,
    ex_style: int,
    cls_style: int,
    owner: int,
    title: str,
    above_fg: bool,
    input_hwnds: set[int],
    *,
    is_fg: bool = False,
) -> bool:
    """Classify whether a non-child, non-captioned WS_POPUP window is a menu dropdown."""
    if ex_style & _WS_EX_APPWINDOW:
        return False
    has_menu_class_style = bool(cls_style & _MENU_CLASS_STYLE_FLAGS)
    if is_fg:
        # Toolkits like CLIP STUDIO PAINT activate/focus the dropdown popup
        # HWND itself so it becomes GetForegroundWindow(). Require positive menu
        # traits so frameless main windows or test mocks are never misclassified.
        return bool(
            (has_menu_class_style and (owner != 0 or not title))
            or (owner != 0 and not title)
        )
    is_topmost_menu = bool(
        (ex_style & _WS_EX_TOPMOST)
        and (
            not title
            or has_menu_class_style
            or (ex_style & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE))
        )
    )
    is_input_popup = hwnd_int in input_hwnds
    is_menu_class_style = bool(
        has_menu_class_style
        and (above_fg or (fg_hwnd_int and owner == fg_hwnd_int))
    )
    is_owned_noactivate = bool(
        fg_hwnd_int
        and owner == fg_hwnd_int
        and above_fg
        and (ex_style & _WS_EX_NOACTIVATE)
    )
    return is_topmost_menu or is_input_popup or is_menu_class_style or is_owned_noactivate


def is_drawing_app_menu_open(
    fg_hwnd: int = 0,
    tid: int = 0,
    pid: int = 0,
    win32gui_module=None,
    win32process_module=None,
) -> bool:
    """True when the foreground drawing app has a dropdown or popup menu open.

    Combines complementary Win32 signals so both native Win32 menus (CSP menu
    bar ``#32768``, SAI menus, UDM menus) and custom toolkit dropdowns
    (ComboLBox, Photoshop ``OWL.Menu``, Qt ``QWindowPopupSaveBits``, CSP custom
    dropdowns with ``WS_BORDER``/``CS_DROPSHADOW``/``SysShadow``) are detected
    without false-triggering on hover tooltips or persistent floating palettes:
    1. ``GetGUIThreadInfo`` menu flags (``GUI_INMENUMODE``, ``GUI_POPUPMENUMODE``,
       ``GUI_SYSTEMMENUMODE``) or a non-zero ``hwndMenuOwner``.
    2. ``GetMenuBarInfo`` active submenu HWND on *fg_hwnd*.
    3. Foreground/focus/capture window class matching a popup menu class.
    4. Visible top-level popup menu / ``CS_DROPSHADOW`` / ``SysShadow``-paired
       captionless dropdown windows owned by the drawing app process.
    """
    my_pid = os.getpid()
    if pid and int(pid) == my_pid:
        return False

    if win32gui_module is None:
        try:
            import win32gui as win32gui_module
        except ImportError:
            win32gui_module = None
    if win32process_module is None:
        try:
            import win32process as win32process_module
        except ImportError:
            win32process_module = None

    fg_hwnd_int = int(fg_hwnd or 0)

    # 1. Check GUI thread menu mode flags & menu owner
    in_menu, hwnd_active, hwnd_focus, hwnd_capture = _query_thread_menu_info(
        tid, win32gui_module
    )
    if in_menu:
        _menu_debug("hit=GetGUIThreadInfo", f"tid={tid}", f"fg={fg_hwnd_int:#x}")
        return True
    if tid:
        in_menu_fg, act0, foc0, cap0 = _query_thread_menu_info(0, win32gui_module)
        if in_menu_fg:
            _menu_debug("hit=GetGUIThreadInfo(0)", f"fg={fg_hwnd_int:#x}")
            return True
        hwnd_active = hwnd_active or act0
        hwnd_focus = hwnd_focus or foc0
        hwnd_capture = hwnd_capture or cap0

    # 1b. Native Win32 menu bar currently owns a dropped submenu HWND
    if _query_menu_bar_open(fg_hwnd_int, win32gui_module):
        _menu_debug("hit=GetMenuBarInfo", f"fg={fg_hwnd_int:#x}")
        return True

    if win32gui_module is None:
        return False

    # 2. Check classes of the foreground / active / focus / capture windows
    get_class = getattr(win32gui_module, "GetClassName", None)
    if callable(get_class):
        for candidate_hwnd in (fg_hwnd_int, hwnd_active, hwnd_focus, hwnd_capture):
            if not candidate_hwnd:
                continue
            try:
                cls_name = get_class(int(candidate_hwnd)) or ""
            except Exception:
                cls_name = ""
            if _is_popup_menu_class(cls_name, same_process=True):
                _menu_debug("hit=input_hwnd_class", f"hwnd={int(candidate_hwnd):#x}", f"cls={cls_name!r}")
                return True

    # 3. Scan visible top-level windows for active popup menus / dropdowns
    enum_windows = getattr(win32gui_module, "EnumWindows", None)
    is_visible = getattr(win32gui_module, "IsWindowVisible", None)
    if not callable(enum_windows) or not callable(is_visible):
        return False

    is_iconic = getattr(win32gui_module, "IsIconic", None)
    get_rect = getattr(win32gui_module, "GetWindowRect", None)
    get_long = getattr(win32gui_module, "GetWindowLong", None)
    get_text = getattr(win32gui_module, "GetWindowText", None)
    get_tid_pid = (
        getattr(win32process_module, "GetWindowThreadProcessId", None)
        if win32process_module is not None
        else None
    )

    state = {"open": False, "fg_seen": False}
    seen_tids = {int(tid)} if tid else set()
    input_hwnds = {h for h in (hwnd_active, hwnd_focus, hwnd_capture) if h}
    shadows: list[tuple[int, tuple[int, int, int, int]]] = []
    shadow_candidates: list[tuple[int, tuple[int, int, int, int], str]] = []

    def _cb(hwnd, _extra):
        if state["open"]:
            return False
        try:
            hwnd_int = int(hwnd)
            if not is_visible(hwnd):
                return True
            if callable(is_iconic) and is_iconic(hwnd):
                return True

            w_tid, w_pid = 0, 0
            if callable(get_tid_pid):
                raw_tid, raw_pid = get_tid_pid(hwnd)
                w_tid = int(raw_tid or 0)
                w_pid = int(raw_pid or 0)
            if w_pid == my_pid:
                return True

            is_fg_window = bool(fg_hwnd_int and hwnd_int == fg_hwnd_int)
            if is_fg_window:
                state["fg_seen"] = True

            above_fg = (not fg_hwnd_int) or ((not is_fg_window) and (not state["fg_seen"]))
            same_proc = bool(pid and w_pid == int(pid))

            if same_proc and w_tid and w_tid not in seen_tids:
                seen_tids.add(w_tid)
                t_menu, t_act, t_foc, t_cap = _query_thread_menu_info(w_tid, win32gui_module)
                if t_menu:
                    state["open"] = True
                    _menu_debug("hit=secondary_tid_menu", f"tid={w_tid}")
                    return False
                for th in (t_act, t_foc, t_cap):
                    if th:
                        input_hwnds.add(th)

            cls_name = (get_class(hwnd) or "") if callable(get_class) else ""
            cls_lower = cls_name.strip().lower()

            if cls_lower == "sysshadow":
                if (same_proc or not pid) and callable(get_rect):
                    l, t, r, b = get_rect(hwnd)
                    shadows.append((w_pid, (int(l), int(t), int(r), int(b))))
                return True

            if _is_popup_menu_class(cls_name, same_process=same_proc):
                # Native #32768 menu or same-process dropdown class
                if same_proc or not pid or cls_lower == "#32768":
                    if callable(get_rect):
                        l, t, r, b = get_rect(hwnd)
                        if (r - l) <= 0 or (b - t) <= 0:
                            return True
                    state["open"] = True
                    _menu_debug("hit=popup_class", f"hwnd={hwnd_int:#x}", f"cls={cls_name!r}")
                    return False

            # Custom toolkit captionless non-resizable dropdown owned by the drawing app
            # (may either sit above fg_hwnd or take foreground itself, as CSP menus do)
            if same_proc and callable(get_long):
                if (
                    cls_lower in _NON_MENU_POPUP_CLASSES
                    or cls_lower.startswith(_NON_MENU_CLASS_PREFIXES)
                    or "inputindicator" in cls_lower
                ):
                    return True
                style = int(get_long(hwnd, _GWL_STYLE) or 0) & 0xFFFFFFFF
                ex_style = int(get_long(hwnd, _GWL_EXSTYLE) or 0) & 0xFFFFFFFF
                # Ignore click-through overlays (IME/TSF indicators, OSDs)
                if ex_style & _WS_EX_TRANSPARENT:
                    return True
                if (style & _WS_POPUP) and not (style & _NON_MENU_STYLE_MASK):
                    l, t, r, b = (0, 0, 100, 100)
                    if callable(get_rect):
                        l, t, r, b = get_rect(hwnd)
                    w, h = r - l, b - t
                    # Exclude 1-line hover tooltips (h < 48) and empty helpers
                    if w >= 40 and h >= 48:
                        title = (get_text(hwnd) or "") if callable(get_text) else ""
                        cls_style = _get_class_style(hwnd_int, win32gui_module)
                        owner = _get_window_owner(hwnd_int, win32gui_module)
                        if _is_custom_popup_menu(
                            hwnd_int,
                            fg_hwnd_int,
                            style,
                            ex_style,
                            cls_style,
                            owner,
                            title,
                            above_fg,
                            input_hwnds,
                            is_fg=is_fg_window,
                        ):
                            state["open"] = True
                            _menu_debug(
                                "hit=custom_popup",
                                f"hwnd={hwnd_int:#x}",
                                f"is_fg={is_fg_window}",
                                f"cls={cls_name!r}",
                                f"st={style:#x}",
                                f"ex={ex_style:#x}",
                                f"cst={cls_style:#x}",
                                f"own={owner:#x}",
                                f"rect={(l, t, r, b)}",
                            )
                            return False
                        if (not (ex_style & _WS_EX_APPWINDOW)) and (
                            not is_fg_window or owner != 0 or not title
                        ):
                            shadow_candidates.append(
                                (w_pid, (int(l), int(t), int(r), int(b)), cls_name)
                            )
        except Exception:
            pass
        return True

    try:
        enum_windows(_cb, None)
    except Exception:
        # win32gui.EnumWindows raises pywintypes.error when the callback stops
        # early via `return False` while a prior Win32 API call left
        # GetLastError() non-zero; preserve any hit already stored in `state`.
        pass

    if state["open"]:
        return True

    if shadows and shadow_candidates:
        for c_pid, c_rect, c_cls in shadow_candidates:
            for s_pid, s_rect in shadows:
                if (c_pid == s_pid or not pid) and _rects_match_shadow(c_rect, s_rect):
                    _menu_debug(
                        "hit=sysshadow_pair",
                        f"cls={c_cls!r}",
                        f"rect={c_rect}",
                        f"shadow={s_rect}",
                    )
                    return True

    return False


def _resolve_thread_focus(tid: int, win32gui_module=None) -> int:
    """跨进程获取指定 GUI 线程的键盘焦点窗口 HWND。

    优先尝试传入的 win32gui 模块（单测 mock 挂在此处）；在真实系统上 pywin32
    未导出该 API，通过 ctypes user32.GetGUIThreadInfo 直接调用系统底层。
    """
    if win32gui_module and hasattr(win32gui_module, "GetGUIThreadInfo"):
        try:
            info = win32gui_module.GetGUIThreadInfo(int(tid or 0))
            return int(getattr(info, "hwndFocus", 0) or 0)
        except Exception:
            pass
    try:
        gui = _GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(int(tid or 0), ctypes.byref(gui)):
            return int(gui.hwndFocus or 0)
    except Exception:
        pass
    return 0


class StylusFocusGuard:
    """记住笔尖触碰我们之前谁拥有前台/焦点，交互结束时还回去。"""

    def __init__(self) -> None:
        self._hwnd = 0
        self._tid = 0
        self._pid = 0

    # -- 配置 -----------------------------------------------------------
    @staticmethod
    def mode() -> str:
        return str(os.environ.get("COLORINK_FOCUS_GUARD", "1")).strip().lower()

    @classmethod
    def enabled(cls) -> bool:
        return cls.mode() not in _FOCUS_GUARD_OFF

    @classmethod
    def forced(cls) -> bool:
        return cls.mode() == "force"

    # -- 采集 -----------------------------------------------------------
    def capture(self) -> bool:
        """在笔尖按下之前调一次：记住当时的前台窗口。"""
        if not self.enabled():
            return False
        try:
            import win32gui
            import win32process
        except ImportError:
            return False
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid():
                # 前台已经是我们：没有"别人"的激活可以还。
                # 但**不要清空**已有的记忆 —— 用鼠标的用户常在触发热键取色前
                # 刚点过我们的窗口（前台/焦点已落在我们手上），若这里 clear()，
                # 取色结束后 restore() 就无目标可还，画图软件永远拿不回前台，
                # 下一次点画布的第一击只会被 Windows 用来"激活窗口"（"取色后
                # 第一击没反应，第二击才画"）。保留上一次记住的窗口（通常正是
                # 那个画图软件），restore() 还有目标可还；restore 自身的
                # IsWindow / 可见 / 未最小化检查会兜住过期目标。
                _focus_debug("capture skip: foreground is already ours, keep previous target",
                             self._hwnd)
                return False
            self._hwnd, self._tid, self._pid = int(hwnd), int(tid or 0), int(pid or 0)
            _focus_debug("capture ok: hwnd", self._hwnd, "pid", self._pid)
            return True
        except Exception:
            return False

    # -- 兜底采集 -----------------------------------------------------------
    def capture_drawing_app_fallback(self) -> bool:
        """前台是自己（``capture()`` 放弃）时的兜底：记录 Z 序最顶的画图软件窗口。

        用鼠标的用户常在触发热键取色前刚点过我们的窗口 —— 那时前台/焦点已在
        我们手上，``capture()`` 抓不到"该还回去的目标"。这里枚举顶层窗口，
        把第一个（Z 序最顶、也就是最近还在交互的）**可见且未最小化**的画图
        软件窗口（PS / CSP / SAI / UDM，由 ``identify_drawing_app`` 判定）记
        下来，``restore()`` 就有目标可还。只在确实找到时才覆盖记忆，找不到
        就维持原样 —— 绝不抢非画图软件的前台。
        """
        if not self.enabled():
            return False
        try:
            import win32gui
            import win32process
        except ImportError:
            return False
        found = {"hwnd": 0, "tid": 0, "pid": 0}

        def _cb(hwnd, _extra):
            if found["hwnd"]:
                return False
            try:
                if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                    return True
                tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if not pid or pid == os.getpid():
                    return True
                if self._is_drawing_app(pid):
                    found["hwnd"] = int(hwnd)
                    found["tid"] = int(tid or 0)
                    found["pid"] = int(pid or 0)
                    return False
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            return False
        if not found["hwnd"]:
            _focus_debug("fallback capture: no drawing app window found")
            return False
        self._hwnd, self._tid, self._pid = found["hwnd"], found["tid"], found["pid"]
        _focus_debug("fallback capture ok: hwnd", self._hwnd, "pid", self._pid)
        return True

    # -- 判断 -----------------------------------------------------------
    def we_hold_input(self) -> bool:
        """前台或键盘焦点是否落在本进程。"""
        try:
            import win32gui
            import win32process
        except ImportError:
            return False
        try:
            fg = win32gui.GetForegroundWindow()
            if fg:
                _, pid = win32process.GetWindowThreadProcessId(fg)
                if pid == os.getpid():
                    return True
            # GetFocus() 只回答"调用线程的消息队列是否拥有键盘焦点" ——
            # 正好用来判断焦点是不是被我们的某个窗口拿走了。
            return bool(win32gui.GetFocus())
        except Exception:
            return False

    @staticmethod
    def _target_holds_input(hwnd: int, pid: int) -> bool:
        """目标窗口是否仍握着前台**与**键盘焦点。

        正常的"全局热键取色"流程里，画图软件从未失去前台（取色浮层
        ``WS_EX_NOACTIVATE`` 抢不到激活，热键也不切换前台）。此时重申
        激活/焦点**不是**幂等操作：``AttachThreadInput`` + ``SetFocus``
        会给目标重放 ``WM_KILLFOCUS``/``WM_SETFOCUS``，并在 detach 时
        拆分两个线程共享的输入状态 —— 恰好撞上取色结束后用户点画布的
        第一击（"第一击没反应、第二击才画"，所有画图软件一致；鼠标
        路径专属，因为 Wintab/Ink 的笔输入不走 Windows 鼠标队列）。

        ``GetGUIThreadInfo`` 可跨进程查询指定 GUI 线程的焦点/捕获窗口，
        无需 AttachThreadInput，Win10/Win11 语义一致。焦点窗口属于目标
        进程（或前台线程暂无焦点窗口）即视为"焦点仍握在目标手上"。
        """
        try:
            import win32gui
            import win32process
        except ImportError:
            return False
        try:
            fg = win32gui.GetForegroundWindow()
            if not fg or int(fg) != int(hwnd):
                return False
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
            focus = _resolve_thread_focus(int(fg_tid or 0), win32gui)
            if not focus:
                return True  # 前台线程暂无焦点窗口（罕见）：前台已对，别搅
            _, focus_pid = win32process.GetWindowThreadProcessId(focus)
            return int(focus_pid or 0) == int(pid)
        except Exception:
            return False

    # -- 交还 -----------------------------------------------------------
    @staticmethod
    def _is_drawing_app(pid: int) -> bool:
        """capture 到的那个窗口是不是画图软件（PS / SAI / CSP / UDM / Krita / Painter 等）。

        对画图软件**不做**"我们是否确实拿到了前台/焦点"的检查：WinTab 的上下文
        焦点可能在驱动层就被换走了（我们自己的窗口没有激活、`GetFocus()` 也是 0，
        看起来什么都没丢），但 PS 那边已经把上下文挂起了。重申一次激活 + 焦点是
        **幂等**的 —— 本来就是我们的前台/焦点时这两个调用什么也不会发生 ——
        却能保证笔回到画布之前上下文已经恢复。
        """
        if not pid:
            return False
        try:
            exe = _resolve_process_exe(int(pid))
        except Exception:
            return False
        if not exe:
            return False
        try:
            return bool(_exe_matches_drawing_app(exe))
        except Exception:
            return False

    @staticmethod
    def _request_photoshop_focus(pid: int) -> None:
        """目标是 Photoshop 时，经 CEP 面板让 PS 主动收回焦点。

        ``SetForegroundWindow`` 受 Windows 前台锁限制，跨进程/跨完整性级别时
        可能被拒；CEP 的 ``com.adobe.PhotoshopLoseFocus`` 事件让 PS 自己收回
        焦点，不受前台锁约束 —— 与上面的直接交还互为兜底。仅在确认目标是
        PS（而非 SAI / CSP / UDM）时触发；任何失败都静默，绝不打断正常交还。
        """
        try:
            exe = _resolve_process_exe(int(pid))
        except Exception:
            return
        try:
            if identify_drawing_app(exe_name=exe) != "ps":
                return
        except Exception:
            return
        try:
            from core import photoshop_script_bridge as _psb
            ok = _psb.request_focus_restore(int(pid))
            _focus_debug("photoshop loseFocus requested, bridge-write", ok)
        except Exception:
            pass

    def restore(self) -> bool:
        """把激活 + 键盘焦点交还给 capture() 记下的那个窗口。"""
        if not self.enabled():
            return False
        if not self._hwnd:
            _focus_debug("restore skip: nothing captured")
            return False
        hwnd = self._hwnd
        pid = self._pid
        self.clear()
        try:
            import win32api
            import win32gui
            import win32process
        except ImportError:
            return False
        try:
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                _focus_debug("restore skip: target window gone/invisible", hwnd)
                return False
            if win32gui.IsIconic(hwnd):
                _focus_debug("restore skip: target minimized", hwnd)
                return False  # 用户把它最小化了，别自作主张恢复
            target_is_drawing_app = self._is_drawing_app(pid)
            if (not self.forced() and target_is_drawing_app
                    and self._target_holds_input(hwnd, pid)):
                # 目标仍握着前台与键盘焦点 —— 全局热键取色的正常路径。
                # 此时重申激活/焦点会搅动输入状态（AttachThreadInput +
                # SetFocus 重放焦点事件、detach 拆分共享按键状态），恰好
                # 吞掉取色结束后用户点画布的第一击。焦点本来就好好在
                # 目标手上：什么都不做才是真正的幂等。真正需要交还的
                # 场景（取色前点过 Colorink 窗口、前台/焦点确实被我们
                # 拿走）不会命中这个分支，仍走完整交还。
                _focus_debug("restore skip: target already holds foreground+focus", hwnd)
                return True
            if (not self.forced() and not target_is_drawing_app
                    and not self.we_hold_input()):
                _focus_debug("restore skip: we hold neither foreground nor focus")
                return False  # 用户已经去用别的软件了，不抢
            if target_is_drawing_app:
                # 画图软件：无论看不看得见"被抢走"，都重申一次（幂等）。
                _focus_debug("restore: drawing app target -> re-assert activation+focus",
                             "forced" if self.forced() else "")
            cur_tid = int(win32api.GetCurrentThreadId())
            fg = win32gui.GetForegroundWindow()
            fg_tid = 0
            if fg:
                try:
                    fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
                    fg_tid = int(fg_tid or 0)
                except Exception:
                    fg_tid = 0
            attached = False
            try:
                # 前台锁：跨进程调 SetForegroundWindow 会被系统拒绝，
                # 先把输入队列挂到当前前台线程上再调。
                if fg_tid and fg_tid != cur_tid:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    attached = True
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
                # 激活之外还要交还键盘焦点：Wintab 的上下文焦点跟着焦点窗口走；
                # 而且对"本来就是前台"的目标，SetForegroundWindow 不会产生新的
                # WM_ACTIVATEAPP，只有 SetFocus 能把 WTI_FOCUS 还给画布。
                win32gui.SetFocus(hwnd)
            finally:
                if attached:
                    try:
                        win32process.AttachThreadInput(cur_tid, fg_tid, False)
                    except Exception:
                        pass
            ok = win32gui.GetForegroundWindow() == hwnd
            # PS 专属兜底：无论 SetForegroundWindow 是否被前台锁拦下，都经 CEP
            # 让 PS 自己收回焦点（PhotoshopLoseFocus 不受前台锁限制）。两条路
            # 互为备份；bridge 未部署/面板过旧时此调用静默为空操作。
            if target_is_drawing_app:
                self._request_photoshop_focus(pid)
            _focus_debug("restore ->", hwnd, "ok" if ok else "foreground-call-refused")
            return ok
        except Exception as exc:
            _focus_debug("restore failed:", type(exc).__name__, exc)
            return False

    def clear(self) -> None:
        self._hwnd = 0
        self._tid = 0
        self._pid = 0


#: 进程级单例：取色浮层和主窗口共用一份采集/交还状态。
focus_guard = StylusFocusGuard()
