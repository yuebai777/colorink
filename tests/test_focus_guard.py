"""笔尖焦点交还（WinTab 首笔压感修复）的测试。

背景：Photoshop 在 WinTab 模式（``UseSystemStylus 0``）下只把压感包送给"拥有
激活/键盘焦点"的那个窗口的 Wintab 上下文。别的窗口一旦把激活或焦点拿走，PS 会
挂起上下文，等用户落笔时才重新握手 —— 第一包（带压力的那包）就丢了，表现为首笔
满压感粗斑 / 折线，第二笔正常。

``core.foreground.StylusFocusGuard`` 在笔尖按下前记住当时的前台窗口，笔抬起时把
激活 + 键盘焦点还回去，让上下文在笔往画布移动的路上就恢复。

这些测试锁定三件事：
1. 采集只在"前台是别的进程"时生效（我们自己抢到的前台没什么可还）；
2. 交还有严格的安全边界（用户跑去用别的软件、目标被最小化 → 一律不动手）；
3. 主窗口的笔事件确实挂着采集/交还，鼠标事件不挂（免得用笔点数值框后没法打字）。
"""

import os
import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from core.foreground import StylusFocusGuard

_MY_PID = os.getpid()
_OTHER_PID = _MY_PID + 4242


# ── win32 替身 ──────────────────────────────────────────────────────────────


class _FakeGuiThreadInfo:
    """win32gui.GetGUIThreadInfo 的替身 —— 只需要 hwndFocus 字段。"""

    def __init__(self, hwnd_focus=0):
        self.hwndFocus = hwnd_focus


class _FakeUser32:
    def __init__(self):
        self.calls = []
        self.foreground = 0
        self.focus = 0
        self.visible = True
        self.iconic = False
        self.exists = True
        self.after_foreground = None  # SetForegroundWindow 之后的"新前台"
        self.top_windows = []         # EnumWindows 按 Z 序返回的顶层窗口
        self.visible_map = {}         # hwnd -> bool（缺省回落到 self.visible）
        self.iconic_map = {}          # hwnd -> bool（缺省回落到 self.iconic）
        self.gui_info = _FakeGuiThreadInfo()  # 前台线程的焦点窗口（0=无）

    # -- 查询 --
    def GetForegroundWindow(self):
        return self.foreground

    def GetFocus(self):
        return self.focus

    def GetGUIThreadInfo(self, tid):
        return self.gui_info

    def IsWindow(self, hwnd):
        return self.exists

    def IsWindowVisible(self, hwnd):
        return self.visible_map.get(hwnd, self.visible)

    def IsIconic(self, hwnd):
        return self.iconic_map.get(hwnd, self.iconic)

    def EnumWindows(self, callback, extra):
        for hwnd in self.top_windows:
            if callback(hwnd, extra) is False:
                break

    # -- 动作 --
    def BringWindowToTop(self, hwnd):
        self.calls.append(("BringWindowToTop", hwnd))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        if self.after_foreground is not None:
            self.foreground = self.after_foreground

    def SetFocus(self, hwnd):
        self.calls.append(("SetFocus", hwnd))


class _FakeWin32Process:
    def __init__(self, user32, pids):
        self._u = user32
        self._pids = pids  # hwnd -> pid
        self.calls = []
        self.tid_calls = []

    def GetWindowThreadProcessId(self, hwnd):
        return (hwnd, self._pids.get(hwnd, 0))

    def AttachThreadInput(self, a, b, attach):
        self.calls.append(("AttachThreadInput", a, b, attach))


class _FakeWin32Api:
    def __init__(self, tid=77):
        self._tid = tid

    def GetCurrentThreadId(self):
        return self._tid


class _Win32Env:
    """把三个 win32 模块塞进 sys.modules 的上下文管理器。"""

    def __init__(self, monkeypatch, pids=None, our_fg_hwnd=None, focus=0):
        self.user32 = _FakeUser32()
        self.proc = _FakeWin32Process(self.user32, dict(pids or {}))
        self.api = _FakeWin32Api()
        monkeypatch.setitem(sys.modules, "win32gui", self.user32)
        monkeypatch.setitem(sys.modules, "win32process", self.proc)
        monkeypatch.setitem(sys.modules, "win32api", self.api)
        if our_fg_hwnd is not None:
            self.user32.foreground = our_fg_hwnd
        self.user32.focus = focus


# ── 采集 ────────────────────────────────────────────────────────────────────


def test_capture_records_foreground_of_another_process(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()

    assert g.capture() is True
    assert g._hwnd == 100


def test_capture_ignores_our_own_foreground(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()

    assert g.capture() is False
    assert g._hwnd == 0


def test_capture_keeps_previous_target_when_foreground_is_ours(monkeypatch):
    """前台变成我们自己时，capture 放弃但**不得清空**已记住的目标。

    用鼠标的用户常在触发热键取色前刚点过我们的窗口 —— 若这里把记忆清空，
    取色结束 restore() 就无目标可还，画图软件拿不回前台，下一次点画布的
    第一击只会被用来"激活窗口"。
    """
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    assert g.capture() is True
    assert g._hwnd == 100

    env.user32.foreground = 200  # 用户点了我们的窗口，前台变成自己
    assert g.capture() is False
    assert g._hwnd == 100          # 记忆保留，restore() 还有目标


# ── 兜底采集（前台已是自己时改记 Z 序最顶的画图软件窗口）────────────────────


def test_fallback_picks_topmost_drawing_window(monkeypatch):
    import core.foreground as fg

    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID + 1})
    env.user32.top_windows = [100, 200]  # Z 序：100 在上
    g = StylusFocusGuard()
    # 100 是记事本、200 是 Photoshop → 跳过 100，记录 200
    monkeypatch.setattr(
        fg, "_resolve_process_exe",
        lambda pid: "notepad.exe" if pid == _OTHER_PID else "photoshop.exe",
    )

    assert g.capture_drawing_app_fallback() is True
    assert g._hwnd == 200
    assert g._pid == _OTHER_PID + 1


def test_fallback_skips_invisible_and_minimized(monkeypatch):
    import core.foreground as fg

    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID + 1})
    env.user32.top_windows = [100, 200]
    env.user32.iconic_map = {100: True}  # 100（PS）被最小化 → 不可作为目标
    g = StylusFocusGuard()
    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "photoshop.exe")

    assert g.capture_drawing_app_fallback() is True
    assert g._hwnd == 200                  # 挑了下一个可见的


def test_fallback_returns_false_without_drawing_app(monkeypatch):
    import core.foreground as fg

    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID})
    env.user32.top_windows = [100]
    g = StylusFocusGuard()
    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "notepad.exe")

    assert g.capture_drawing_app_fallback() is False
    assert g._hwnd == 0                    # 找不到就维持原样，绝不抢非画图软件


def test_capture_disabled_by_env(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID})
    env.user32.foreground = 100
    monkeypatch.setenv("COLORINK_FOCUS_GUARD", "0")
    g = StylusFocusGuard()

    assert g.capture() is False
    assert g._hwnd == 0


# ── 交还 ────────────────────────────────────────────────────────────────────


def test_restore_hands_back_when_we_hold_foreground(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()

    # 笔点进我们窗口：前台变成我们，交还后应回到 100。
    env.user32.foreground = 200
    env.user32.after_foreground = 100

    assert g.restore() is True
    assert ("BringWindowToTop", 100) in env.user32.calls
    assert ("SetForegroundWindow", 100) in env.user32.calls
    # 激活之外必须交还键盘焦点：Wintab 的上下文焦点跟着焦点窗口走。
    assert ("SetFocus", 100) in env.user32.calls


def test_restore_attaches_and_detaches_input_queue(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200
    env.user32.after_foreground = 100

    g.restore()

    # 前台锁：跨进程 SetForegroundWindow 需要先挂输入队列，且必须解挂。
    assert ("AttachThreadInput", 77, 200, True) in env.proc.calls
    assert ("AttachThreadInput", 77, 200, False) in env.proc.calls


def test_restore_uses_keyboard_focus_when_foreground_is_elsewhere(monkeypatch):
    """前台还是别人的，但键盘焦点被我们的窗口拿走了 —— 一样要还。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.focus = 999  # 我们的窗口持有焦点（GetFocus 只回答本线程队列）
    env.user32.after_foreground = 100

    assert g.restore() is True
    assert ("SetFocus", 100) in env.user32.calls


def test_restore_is_noop_when_we_hold_nothing(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200  # 用户跑去用别的软件了
    env.user32.focus = 0

    assert g.restore() is False
    assert env.user32.calls == []


def test_restore_skips_minimized_or_invisible_target(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()

    g.capture()
    env.user32.foreground = 200
    env.user32.iconic = True
    assert g.restore() is False
    assert env.user32.calls == []
    env.user32.iconic = False

    env.user32.foreground = 100
    g.capture()
    env.user32.foreground = 200
    env.user32.visible = False
    assert g.restore() is False
    assert env.user32.calls == []
    env.user32.visible = True

    env.user32.foreground = 100
    g.capture()
    env.user32.foreground = 200
    env.user32.exists = False
    assert g.restore() is False
    assert env.user32.calls == []


def test_restore_reasserts_for_drawing_app_even_when_we_hold_nothing(monkeypatch):
    """前台已被第三方进程拿走、目标是画图软件时仍要交还 —— WinTab 的上下文
    焦点可能在驱动层就被换走，我们自己看不到。（"目标仍握前台+焦点"的
    正常路径由下面的零搅动用例覆盖，不走这里。）"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID})
    env.user32.foreground = 100
    env.user32.after_foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200          # 前台不是我们
    env.user32.focus = 0                 # 焦点也不在我们手上
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app", staticmethod(lambda pid: True))

    assert g.restore() is True
    assert ("SetForegroundWindow", 100) in env.user32.calls
    assert ("SetFocus", 100) in env.user32.calls


# ── 交还的"零搅动"路径（取色后第一击失效的修复）─────────────────────────────


def test_restore_is_noop_when_target_already_holds_foreground_and_focus(monkeypatch):
    """全局热键取色的正常路径：画图软件从未失去前台（浮层 WS_EX_NOACTIVATE），
    键盘焦点也一直在它手上。

    此时重申激活/焦点**不是**幂等的：AttachThreadInput + SetFocus 会给目标
    重放 WM_KILLFOCUS/WM_SETFOCUS，detach 时拆分两线程共享的按键状态 ——
    恰好吞掉取色结束后用户点画布的第一击（"第一击没反应、第二击才画"，
    鼠标路径专属：笔走 Wintab/Ink 不进 Windows 鼠标队列，所以笔没事）。
    目标已握前台+焦点时必须一次系统调用都不做。
    """
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 300: _OTHER_PID})
    env.user32.foreground = 100
    env.user32.gui_info.hwndFocus = 300  # 焦点窗口也属于目标进程
    g = StylusFocusGuard()
    g.capture()
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app", staticmethod(lambda pid: True))
    calls = []
    monkeypatch.setattr(StylusFocusGuard, "_request_photoshop_focus",
                        staticmethod(lambda pid: calls.append(pid)))

    assert g.restore() is True
    assert env.user32.calls == []        # SetForegroundWindow/SetFocus 一次都不做
    assert env.proc.calls == []          # 也没有 AttachThreadInput
    assert calls == []                   # 焦点本来就在它手上，连 loseFocus 都不用发


def test_restore_still_hands_back_when_focus_slipped_to_us(monkeypatch):
    """前台还是画图软件，但键盘焦点被我们的窗口拿走 → 不能跳过，必须还。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 300: _OTHER_PID, 400: _MY_PID})
    env.user32.foreground = 100
    env.user32.gui_info.hwndFocus = 400  # 焦点窗口属于我们
    env.user32.after_foreground = 100
    g = StylusFocusGuard()
    g.capture()
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app", staticmethod(lambda pid: True))

    assert g.restore() is True
    assert ("SetForegroundWindow", 100) in env.user32.calls
    assert ("SetFocus", 100) in env.user32.calls


def test_restore_force_mode_overrides_the_noop_shortcut(monkeypatch):
    """force（诊断模式）保持"无条件交还"语义：绕过零搅动跳过，照常重申。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 300: _OTHER_PID})
    env.user32.foreground = 100
    env.user32.gui_info.hwndFocus = 300
    env.user32.after_foreground = 100
    g = StylusFocusGuard()
    g.capture()
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app", staticmethod(lambda pid: True))
    monkeypatch.setenv("COLORINK_FOCUS_GUARD", "force")

    assert g.restore() is True
    assert ("SetForegroundWindow", 100) in env.user32.calls
    assert ("SetFocus", 100) in env.user32.calls


def test_restore_noop_shortcut_requires_drawing_app_target(monkeypatch):
    """零搅动跳过只对画图软件目标生效；普通窗口目标维持原有安全边界。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 300: _OTHER_PID})
    env.user32.foreground = 100
    env.user32.gui_info.hwndFocus = 300
    g = StylusFocusGuard()
    g.capture()
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app", staticmethod(lambda pid: False))
    env.user32.foreground = 200          # 用户跑去别的软件 → 不抢

    assert g.restore() is False
    assert env.user32.calls == []


def test_restore_requests_photoshop_focus_for_drawing_app(monkeypatch):
    """交还画图软件时，除 SetForegroundWindow 外还要经 CEP 让 PS 自己收回焦点
    （前台锁兜底）。非画图软件目标不触发。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200
    env.user32.after_foreground = 100
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app",
                        staticmethod(lambda pid: True))
    calls = []
    monkeypatch.setattr(StylusFocusGuard, "_request_photoshop_focus",
                        staticmethod(lambda pid: calls.append(pid)))

    assert g.restore() is True
    assert calls == [_OTHER_PID]


def test_restore_skips_photoshop_focus_for_non_drawing_app(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200
    env.user32.after_foreground = 100
    monkeypatch.setattr(StylusFocusGuard, "_is_drawing_app",
                        staticmethod(lambda pid: False))
    calls = []
    monkeypatch.setattr(StylusFocusGuard, "_request_photoshop_focus",
                        staticmethod(lambda pid: calls.append(pid)))

    assert g.restore() is True
    assert calls == []


def test_is_drawing_app_matches_known_drawing_executables(monkeypatch):
    import core.foreground as fg

    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "photoshop.exe")
    assert StylusFocusGuard._is_drawing_app(4242) is True

    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "explorer.exe")
    assert StylusFocusGuard._is_drawing_app(4242) is False

    monkeypatch.setattr(fg, "_resolve_process_exe", lambda pid: "")
    assert StylusFocusGuard._is_drawing_app(4242) is False
    assert StylusFocusGuard._is_drawing_app(0) is False


def test_restore_force_mode_ignores_ownership(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _OTHER_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200
    env.user32.after_foreground = 100
    monkeypatch.setenv("COLORINK_FOCUS_GUARD", "force")

    assert g.restore() is True
    assert ("SetForegroundWindow", 100) in env.user32.calls


def test_restore_without_capture_is_noop(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID})
    g = StylusFocusGuard()

    assert g.restore() is False
    assert env.user32.calls == []


def test_restore_clears_capture_so_second_call_is_noop(monkeypatch):
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID, 200: _MY_PID})
    env.user32.foreground = 100
    g = StylusFocusGuard()
    g.capture()
    env.user32.foreground = 200
    env.user32.after_foreground = 100

    assert g.restore() is True
    before = list(env.user32.calls)
    assert g.restore() is False
    assert env.user32.calls == before


# ── 接线：主窗口的笔事件 ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class _RecordingGuard:
    def __init__(self):
        self.captured = 0
        self.restored = 0

    def capture(self):
        self.captured += 1
        return True

    def restore(self):
        self.restored += 1
        return True


class _FakeEvent(QEvent):
    """真 QEvent 子类 + 伪造 type()：把 eventFilter 引到我们要测的分支。

    必须是真 QEvent —— 分支末尾会走 ``super().eventFilter()``，Shiboken 无法把
    普通 Python 对象转换成 ``QEvent*``。
    """

    def __init__(self, etype):
        super().__init__(QEvent.Type.User)
        self._etype = etype

    def type(self):
        return self._etype

    def button(self):
        return None

    def globalPosition(self):
        from PyQt6.QtCore import QPointF
        return QPointF(0, 0)


def _layout_host():
    from ui.window.layout import LayoutMixin

    class _Host(LayoutMixin, QWidget):
        pass

    return _Host()


def test_tablet_press_and_release_are_wired_to_the_guard(qapp, monkeypatch):
    import ui.window.layout as layout

    rec = _RecordingGuard()
    monkeypatch.setattr(layout, "focus_guard", rec)
    host = _layout_host()
    try:
        host.eventFilter(host, _FakeEvent(QEvent.Type.TabletPress))
        host.eventFilter(host, _FakeEvent(QEvent.Type.TabletRelease))
    finally:
        host.close()

    assert (rec.captured, rec.restored) == (1, 1)


def test_mouse_press_does_not_touch_the_guard(qapp, monkeypatch):
    """鼠标交互不碰焦点：否则用笔点数值框之后就没法打字了。"""
    import ui.window.layout as layout

    rec = _RecordingGuard()
    monkeypatch.setattr(layout, "focus_guard", rec)
    host = _layout_host()
    try:
        host.eventFilter(host, _FakeEvent(QEvent.Type.MouseButtonPress))
        host.eventFilter(host, _FakeEvent(QEvent.Type.MouseButtonRelease))
    finally:
        host.close()

    assert (rec.captured, rec.restored) == (0, 0)


def test_resolve_thread_focus_uses_ctypes_when_win32gui_lacks_api(monkeypatch):
    """当 win32gui 模块没有 GetGUIThreadInfo 时（真实 pywin32 环境），
    _resolve_thread_focus 正确通过 ctypes 底层调用 user32.GetGUIThreadInfo。"""
    import ctypes
    from core.foreground import _resolve_thread_focus

    # 创建一个没有 GetGUIThreadInfo 的伪 win32gui 模块
    class DummyWin32Gui:
        pass

    class DummyUser32:
        def __init__(self):
            self.called_tid = None

        def GetGUIThreadInfo(self, tid, p_info):
            self.called_tid = int(getattr(tid, "value", tid))
            p_info._obj.hwndFocus = 9999
            return 1

    dummy_user32 = DummyUser32()
    monkeypatch.setattr(ctypes.windll, "user32", dummy_user32)

    focus_hwnd = _resolve_thread_focus(1234, DummyWin32Gui())
    assert dummy_user32.called_tid == 1234
    assert focus_hwnd == 9999


def test_is_any_drawing_exe_covers_expanded_software():
    """兼容性加固：支持 PS / SAI / CSP / UDM 之外的绘画软件
    （Krita / Painter / Medibang / FireAlpaca / Rebelle / Sketchbook / IbisPaint / Aseprite 等）。"""
    from core.foreground import is_any_drawing_exe

    drawing_exes = [
        "photoshop.exe", "Photoshop.exe", "PhotoshopPrefs.exe",
        "sai.exe", "sai2.exe",
        "clipstudiopaint.exe", "CLIPStudioPaint.exe",
        "udmpaintpro.exe",
        "krita.exe", "Krita.exe",
        "Painter 2023.exe", "Corel Painter.exe",
        "medibangpaintpro.exe", "MediBangPaint.exe",
        "firealpaca.exe", "FireAlpaca64.exe",
        "rebelle.exe", "Rebelle 7.exe",
        "sketchbook.exe",
        "ibispaint.exe",
        "tvpaint.exe",
        "aseprite.exe",
        "illustrator.exe",
    ]
    for exe in drawing_exes:
        assert is_any_drawing_exe(exe) is True, f"Expected {exe} to be recognized as drawing app"

    non_drawing_exes = [
        "chrome.exe", "msedge.exe", "code.exe", "notepad.exe", "explorer.exe",
        "cmd.exe", "powershell.exe", "wechat.exe", "discord.exe",
    ]
    for exe in non_drawing_exes:
        assert is_any_drawing_exe(exe) is False, f"Expected {exe} not to be recognized as drawing app"


def test_restore_zero_churn_works_for_expanded_drawing_apps(monkeypatch):
    """验证 expanded 绘图软件（例如 krita.exe）在仍握着前台+焦点时，
    restore() 同样走零搅动路径，一次系统调用都不做，防止首笔丢失。"""
    env = _Win32Env(monkeypatch, pids={100: _OTHER_PID})
    env.user32.foreground = 100
    env.user32.after_foreground = 100
    env.user32.gui_info.hwndFocus = 100   # 焦点在 Krita 自己手里
    monkeypatch.setattr("core.foreground._resolve_process_exe", lambda pid: "krita.exe")

    g = StylusFocusGuard()
    assert g.capture() is True
    assert g._is_drawing_app(_OTHER_PID) is True

    # restore 应该识别到目标已经握着前台+焦点，完全不调用 SetForegroundWindow / SetFocus
    assert g.restore() is True
    assert env.user32.calls == []

