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


class _FakeUser32:
    def __init__(self):
        self.calls = []
        self.foreground = 0
        self.focus = 0
        self.visible = True
        self.iconic = False
        self.exists = True
        self.after_foreground = None  # SetForegroundWindow 之后的"新前台"

    # -- 查询 --
    def GetForegroundWindow(self):
        return self.foreground

    def GetFocus(self):
        return self.focus

    def IsWindow(self, hwnd):
        return self.exists

    def IsWindowVisible(self, hwnd):
        return self.visible

    def IsIconic(self, hwnd):
        return self.iconic

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
    """目标是画图软件时不做"是否真的被抢走"的检查 —— WinTab 的上下文焦点可能
    在驱动层就被换走，我们自己看不到；重申一次是幂等的。"""
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
