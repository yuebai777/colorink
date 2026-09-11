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
                self.clear()
                _focus_debug("capture skip: foreground is already ours")
                return False
            self._hwnd, self._tid, self._pid = int(hwnd), int(tid or 0), int(pid or 0)
            _focus_debug("capture ok: hwnd", self._hwnd, "pid", self._pid)
            return True
        except Exception:
            return False

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

    # -- 交还 -----------------------------------------------------------
    @staticmethod
    def _is_drawing_app(pid: int) -> bool:
        """capture 到的那个窗口是不是画图软件（PS / SAI / CSP / UDM）。

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
