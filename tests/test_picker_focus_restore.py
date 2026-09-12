"""取色结束后「等确认键物理抬起再交还焦点」的接线测试。

背景：取色是"按下即确认"（``_tick`` 每 16ms 轮询 ``left_clicked``），而人手
一次点击的按下→松开间隔通常 60~120ms，所以 ``stop()`` 跑完时左键多半还按着。
此刻立即 ``focus_guard.restore()`` 会把画图软件拉回前台，而它拿到焦点的瞬间
从系统层面读到"左键按下中"，随后那一下 UP 又被取色钩子配对吞掉 —— 绘图软件
的鼠标状态机停在"按下"，下一次点击的 DOWN 被当成重复按下忽略（"取色后第一击
没反应，第二击才画"）。``_schedule_focus_restore`` 把交还推迟到按键物理抬起，
并给 500ms 超时兜底；驱动不置位 ``VK_LBUTTON`` 的笔路径立即交还（零延迟）。

这些测试锁定：按键按着 → 不立即交还（起定时器）；抬起 / 超时 → 交还一次并停；
``stop()`` 走调度而不是直接交还；``start()`` 在 ``capture`` 放弃时走画图软件兜底。
"""

import pytest
import win32con
from PyQt6.QtWidgets import QApplication

import ui.color_picker_overlay as cpo
from ui.color_picker_overlay import ColorPickerOverlay


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeWin32Api:
    """按 vk 集合报告物理按下状态（0x8000 = 按下）。"""

    def __init__(self, down=()):
        self.down = set(down)

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down else 0


class _RecGuard:
    """记录 capture / fallback / restore 调用次数的 focus_guard 替身。"""

    def __init__(self, capture_result=True):
        self.capture_result = capture_result
        self.captures = 0
        self.fallbacks = 0
        self.restores = 0

    def capture(self):
        self.captures += 1
        return self.capture_result

    def capture_drawing_app_fallback(self):
        self.fallbacks += 1
        return False

    def restore(self):
        self.restores += 1
        return True


class _FakeHookDll:
    def __init__(self, owed=0):
        self.owed = owed

    def install(self):
        return 1

    def uninstall(self):
        pass

    def uninstall_force(self):
        pass

    def pending(self):
        return self.owed

    def maintenance(self):
        return 0

    def left_clicked(self):
        return 0

    def right_clicked(self):
        return 0

    def get_wheel_delta(self):
        return 0


def _make_overlay(monkeypatch, guard, api):
    monkeypatch.setattr(cpo, "focus_guard", guard)
    monkeypatch.setattr(cpo, "win32api", api)
    monkeypatch.setattr(cpo, "_hook_dll", _FakeHookDll())
    monkeypatch.setattr(cpo, "_hook_has_drain", True)
    return ColorPickerOverlay(None)


# ── _schedule_focus_restore ────────────────────────────────────────────────


def test_restore_is_deferred_while_button_held(qapp, monkeypatch):
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_LBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    try:
        overlay._schedule_focus_restore()
        assert guard.restores == 0                       # 键还按着 → 不还
        assert overlay._focus_restore_timer.isActive()   # 改由轮询等抬起
    finally:
        overlay._focus_restore_timer.stop()


def test_restore_is_immediate_when_buttons_up(qapp, monkeypatch):
    """键已抬起（含不置位 VK_LBUTTON 的笔路径）→ 立即交还，零延迟。"""
    guard = _RecGuard()
    overlay = _make_overlay(monkeypatch, guard, _FakeWin32Api())
    overlay._schedule_focus_restore()
    assert guard.restores == 1
    assert not overlay._focus_restore_timer.isActive()


def test_right_button_also_defers_restore(qapp, monkeypatch):
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_RBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    try:
        overlay._schedule_focus_restore()
        assert guard.restores == 0
        assert overlay._focus_restore_timer.isActive()
    finally:
        overlay._focus_restore_timer.stop()


def test_poll_fires_restore_once_button_released(qapp, monkeypatch):
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_LBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    try:
        overlay._schedule_focus_restore()
        assert overlay._focus_restore_timer.isActive()
        api.down.clear()  # 用户松开了
        overlay._poll_focus_restore()
        assert guard.restores == 1
        assert not overlay._focus_restore_timer.isActive()
    finally:
        overlay._focus_restore_timer.stop()


def test_poll_fires_restore_after_deadline(qapp, monkeypatch):
    """按住不放也不能让交还永远等下去 —— 超时兜底。"""
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_LBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    try:
        overlay._schedule_focus_restore()
        overlay._focus_restore_deadline = 0.0  # 假装已经超时
        overlay._poll_focus_restore()
        assert guard.restores == 1
        assert not overlay._focus_restore_timer.isActive()
    finally:
        overlay._focus_restore_timer.stop()


# ── stop() / start() 接线 ──────────────────────────────────────────────────


def test_stop_schedules_restore_instead_of_restoring_immediately(qapp, monkeypatch):
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_LBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    try:
        overlay._active = True
        overlay.stop()
        assert guard.restores == 0                       # 键还按着 → stop 不直接还
        assert overlay._focus_restore_timer.isActive()
    finally:
        overlay._focus_restore_timer.stop()


def test_start_resets_a_pending_focus_restore(qapp, monkeypatch):
    """新一轮取色必须停掉上一轮遗留的交还轮询，避免串场。"""
    guard = _RecGuard()
    api = _FakeWin32Api(down={win32con.VK_LBUTTON})
    overlay = _make_overlay(monkeypatch, guard, api)
    monkeypatch.setattr(ColorPickerOverlay, "_hide_cursor", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_capture_all_screens", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_tick", lambda self: None)
    try:
        overlay._schedule_focus_restore()
        assert overlay._focus_restore_timer.isActive()
        overlay.start()
        assert not overlay._focus_restore_timer.isActive()
    finally:
        overlay._active = False
        overlay._timer.stop()
        overlay._watchdog.stop()
        overlay._focus_restore_timer.stop()


def test_start_falls_back_when_capture_finds_our_own_foreground(qapp, monkeypatch):
    """capture 放弃（前台是我们自己）时，start() 走画图软件兜底采集。"""
    guard = _RecGuard(capture_result=False)
    overlay = _make_overlay(monkeypatch, guard, _FakeWin32Api())
    monkeypatch.setattr(ColorPickerOverlay, "_hide_cursor", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_capture_all_screens", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_tick", lambda self: None)
    try:
        overlay.start()
        assert guard.captures == 1
        assert guard.fallbacks == 1
    finally:
        overlay._active = False
        overlay._timer.stop()
        overlay._watchdog.stop()


def test_start_does_not_fallback_when_capture_succeeds(qapp, monkeypatch):
    guard = _RecGuard(capture_result=True)
    overlay = _make_overlay(monkeypatch, guard, _FakeWin32Api())
    monkeypatch.setattr(ColorPickerOverlay, "_hide_cursor", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_capture_all_screens", lambda self: None)
    monkeypatch.setattr(ColorPickerOverlay, "_tick", lambda self: None)
    try:
        overlay.start()
        assert guard.captures == 1
        assert guard.fallbacks == 0
    finally:
        overlay._active = False
        overlay._timer.stop()
        overlay._watchdog.stop()


def test_overlay_and_cursor_dot_native_mouse_activate_noactivate(qapp, monkeypatch):
    """ColorPickerOverlay 和 CursorDot 必须在收到 WM_MOUSEACTIVATE 时返回 (True, 3) (MA_NOACTIVATE)，
    绝不激活自身、绝不抢夺画图软件的前台状态，避免触发 WinTab 上下文挂起。"""
    import ctypes
    import ctypes.wintypes
    from ui.color_picker_overlay import CursorDot

    overlay = _make_overlay(monkeypatch, _RecGuard(), _FakeWin32Api())
    dot = CursorDot()

    # 构造 WM_MOUSEACTIVATE (0x0021) 结构体
    msg = ctypes.wintypes.MSG()
    msg.message = 0x0021
    msg_addr = ctypes.addressof(msg)

    res_overlay = overlay.nativeEvent(b"windows_generic_MSG", msg_addr)
    assert res_overlay == (True, 3), f"ColorPickerOverlay should return (True, 3), got {res_overlay}"

    res_dot = dot.nativeEvent(b"windows_generic_MSG", msg_addr)
    assert res_dot == (True, 3), f"CursorDot should return (True, 3), got {res_dot}"

    # 非 WM_MOUSEACTIVATE 消息应返回 (False, 0)
    msg.message = 0x0001
    assert overlay.nativeEvent(b"windows_generic_MSG", msg_addr) == (False, 0)
    assert dot.nativeEvent(b"windows_generic_MSG", msg_addr) == (False, 0)


def test_release_hook_drain_deadline_is_short(qapp, monkeypatch):
    """验证 _release_hook 的等待超时为 0.3 秒级别（防止持续 5 秒全局吃键导致落笔无反应）。"""
    import time

    guard = _RecGuard()
    overlay = _make_overlay(monkeypatch, guard, _FakeWin32Api())
    # 模拟 hook 仍欠 1 个按键释放
    cpo._hook_dll.owed = 1
    t0 = time.monotonic()
    overlay._release_hook()
    assert overlay._drain_deadline > t0
    # deadline 应在 0.3s 附近，严禁使用 5.0 秒的大延迟
    assert overlay._drain_deadline - t0 < 1.0
    overlay._drain_timer.stop()

