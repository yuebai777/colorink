"""取色浮层不能往系统里注入输入（WinTab 首笔压感）。

判据来自实测出来的机制：取色确认后，`_show_cursor()` 曾调用
``win32api.SetCursorPos(pos+1)`` / ``SetCursorPos(pos)`` 去"催"前台画图软件重画
笔刷光标。这两次调用是**注入的合成鼠标移动**，而且正好发生在"笔尖开始往画布
移动"的同一瞬间 —— WinTab 模式下的 Photoshop 靠"最近的输入是笔还是鼠标"做仲裁，
一次合成移动就足以让它把接下来的一笔当成鼠标输入：满压感粗斑，第二笔才恢复。

注：`SetCursorPos` 是 2026-08-20 的 b84d26b 才加进去的；同一时期的
`tests/test_picker_input_guard.py`（源码已不在，只剩 .pyc）里就写着
"_show_cursor must never call win32api.SetCursorPos (prevents first stroke
pressure loss)" —— 这条测试这次补回来，别再丢。
"""

import os
import sys

import pytest
from PyQt6.QtWidgets import QApplication

import ui.color_picker_overlay as cpo
from ui.color_picker_overlay import ColorPickerOverlay


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class _RecordingWin32Api:
    """win32api 替身：任何一次 SetCursorPos 都会被记下来。"""

    def __init__(self, pos=(100, 200)):
        self.pos = pos
        self.set_calls = []
        self.get_calls = 0

    def GetCursorPos(self):
        self.get_calls += 1
        return self.pos

    def SetCursorPos(self, p):
        self.set_calls.append(p)


@pytest.fixture()
def recorder(monkeypatch):
    rec = _RecordingWin32Api()
    monkeypatch.setattr(cpo, "win32api", rec)
    # SPI_SETCURSORS 走 ctypes：换成哑元，别真的动系统光标表。
    monkeypatch.setattr(cpo.ctypes.windll, "user32", _FakeUser32())
    return rec


class _FakeUser32:
    def __init__(self):
        self.spi = []

    def SystemParametersInfoW(self, action, param, pv, winini):
        self.spi.append((action, param, pv, winini))
        return 1


def test_show_cursor_never_injects_mouse_movement(qapp, recorder):
    """取色收尾必须只还原光标表，绝不注入合成鼠标移动。"""
    overlay = ColorPickerOverlay(None)
    try:
        overlay._cursor_hidden = True
        overlay._show_cursor()
    finally:
        overlay.close()

    assert recorder.set_calls == [], (
        "SetCursorPos 被调用了！它会往正在回到画布的 PS 注入合成鼠标移动，"
        "让第一笔被当成鼠标输入（丢压感）"
    )
    assert recorder.get_calls == 0


def test_show_cursor_restores_the_system_cursor_table(qapp, recorder):
    overlay = ColorPickerOverlay(None)
    try:
        overlay._cursor_hidden = True
        overlay._show_cursor()
        assert overlay._cursor_hidden is False
        assert cpo.ctypes.windll.user32.spi == [(0x0057, 0, None, 0)]
    finally:
        overlay.close()


def test_show_cursor_is_a_noop_when_nothing_was_hidden(qapp, recorder):
    overlay = ColorPickerOverlay(None)
    try:
        overlay._cursor_hidden = False
        overlay._show_cursor()
        assert cpo.ctypes.windll.user32.spi == []
        assert recorder.set_calls == []
    finally:
        overlay.close()


def test_cursor_nudge_requires_the_opt_in_flag(qapp, recorder, monkeypatch):
    """回退通道：只有显式设置 COLORINK_PICKER_CURSOR_NUDGE=1 才注入（排查用）。"""
    monkeypatch.setenv("COLORINK_PICKER_CURSOR_NUDGE", "1")
    overlay = ColorPickerOverlay(None)
    try:
        overlay._cursor_hidden = True
        overlay._show_cursor()
    finally:
        overlay.close()

    assert recorder.set_calls == [(101, 200), (100, 200)]
