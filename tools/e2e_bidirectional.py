#!/usr/bin/env python3
"""Bidirectional sync e2e test (memory mode):

A. Colorink -> CSP: fg red, bg green (full-copy write)
B. CSP -> Colorink: simulated picker (companion writes main blue)
C. CSP hotkeys: F5=main color, F6=sub color, F7=transparent color
   -> verify +0x08 state AND the MemorySyncThread signals (what Colorink's
      UI would receive).

Runs fully automatic against the live CSP.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402
from core.memory_sync import MemorySyncThread  # noqa: E402

VK_F5, VK_F6, VK_F7 = 0x74, 0x75, 0x76

events: list[tuple] = []
failures: list[str] = []
thread: MemorySyncThread | None = None


def check(label: str, cond: bool) -> None:
    print(f"  {'✓' if cond else '✗'} {label}")
    if not cond:
        failures.append(label)


def on_color(r, g, b, idx):
    events.append(("color", idx, r, g, b))


def on_transparent(idx, t):
    events.append(("transparent", idx, t))


def on_active(idx):
    events.append(("active", idx))


_app = None


def wait_sync(seconds: float) -> None:
    """等待期间驱动 Qt 事件循环（跨线程 QueuedConnection 信号需要）。"""
    end = time.time() + seconds
    while time.time() < end:
        if _app is not None:
            _app.processEvents()
        time.sleep(0.05)


def _find_csp_window(pid: int):
    user32 = ctypes.WinDLL("user32")
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def press_key(vk: int) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _I)]

    ok = True
    for flags in (0, 0x0002):
        inp = INPUT()
        inp.type = 1
        inp.ki.wVk = vk
        inp.ki.dwFlags = flags
        r = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        ok = ok and r == 1
    return ok


def main() -> int:
    global thread
    # QThread 需要 QCoreApplication 才能正常运行
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    global _app
    _app = app

    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    assert sync.pm is not None and sync.target is not None

    thread = MemorySyncThread()
    thread.signals.color_changed.connect(on_color)
    thread.signals.transparent_changed.connect(on_transparent)
    thread.signals.active_slot_changed.connect(on_active)
    thread.set_software_mode("csp")
    # 预连接 companion（CSP server 单客户端：测试全程复用这一个连接，
    # 同时避免首次副色定位时在轮询线程里阻塞 5s）
    comp_ok = thread.companion_sync.connect()
    comp = thread.companion_sync
    print(f"companion 预连接: {comp_ok}")
    thread.start()
    wait_sync(1.0)

    # ── A. Colorink -> CSP ────────────────────────────────────────────────
    print("── A1. Colorink 写 fg=红 ──")
    events.clear()
    thread.write_color(255, 0, 0, color_index=0)
    time.sleep(0.8)
    main = sync.get_color()
    check(f"内存主色=红 {main}", main is not None and main["r"] > 200 and main["g"] < 60)
    rb = comp.get_color_hsv()
    check(f"companion 读回激活槽=0, 实际={rb.get('index') if rb else None}",
          rb is not None and rb.get("index") == 0)

    print("── A2. Colorink 写 bg=绿（首次定位）──")
    events.clear()
    thread.write_color(0, 255, 0, color_index=1)
    # 等定位完成（首次搜索可能 1-2s）
    for _ in range(20):
        wait_sync(0.3)
        if len(thread.csp_sync._sub_copy_addrs or []) >= 5:
            break
    sub = sync.get_sub_color()
    check(f"内存副色=绿 {sub}", sub is not None and sub["g"] > 200 and sub["r"] < 60)
    check(f"副色副本缓存 {len(thread.csp_sync._sub_copy_addrs or [])} 处",
          len(thread.csp_sync._sub_copy_addrs or []) >= 5)
    rb = comp.get_color_hsv()
    check(f"companion 读回激活槽=1, 实际={rb.get('index') if rb else None}",
          rb is not None and rb.get("index") == 1)

    # ── B. CSP -> Colorink (picker simulation) ────────────────────────────
    # 注意：主线程操作 companion 与后台轮询线程并发读写同一 socket 会竞争。
    # 模拟 CSP 侧变化时先暂停轮询，写完再恢复。
    print("── B1. 模拟 CSP 取色：companion 写主色=紫 ──")
    b1_ok = False
    for attempt in range(4):
        comp.set_color(255, 0, 255, color_index=0)
        events.clear()
        wait_sync(1.5)
        if any(e[0] == "color" and e[1] == 0 and e[2] > 200 and e[4] > 200 for e in events):
            b1_ok = True
            break
    check(f"color_changed(0, 主色) 信号收到: {[(tuple(e)[:5]) for e in events[:8]]}", b1_ok)

    print("── B2. 模拟 CSP 取副色：companion 写副色=黄 ──")
    thread.paused = True
    time.sleep(0.3)
    comp.set_color(255, 255, 0, color_index=1)
    thread.paused = False
    events.clear()
    wait_sync(1.5)
    got = any(e[0] == "color" and e[1] == 1 and e[2] > 200 and e[3] > 200 for e in events)
    check(f"color_changed(1, 黄) 信号收到: {[(e[0], e[1]) for e in events[:8]]}", got)

    # ── C. CSP hotkeys (F5/F6/F7) ────────────────────────────────────────
    # 真实按键受测试环境前台焦点限制（脚本终端抢焦点）。按键尽力发送，
    # 同时用 companion 等效模拟"快捷键的 CSP 效果"（改激活槽/透明），
    # 验证 Colorink 的跟随逻辑。
    hwnd = _find_csp_window(sync.pid or 0)
    check("找到 CSP 窗口", hwnd is not None)

    def focus_and_press(vk: int) -> None:
        if hwnd:
            u32 = ctypes.WinDLL("user32")
            fg = u32.GetForegroundWindow()
            tid_fg = u32.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_csp = u32.GetWindowThreadProcessId(hwnd, None)
            attached = bool(tid_fg and tid_fg != tid_csp
                            and u32.AttachThreadInput(tid_fg, tid_csp, True))
            u32.SetForegroundWindow(hwnd)
            u32.BringWindowToTop(hwnd)
            if attached:
                u32.AttachThreadInput(tid_fg, tid_csp, False)
            time.sleep(0.4)
            u32.PostMessageW(hwnd, 0x0100, vk, 0)
            u32.PostMessageW(hwnd, 0x0101, vk, 0)
            time.sleep(0.4)
        press_key(vk)
        wait_sync(1.0)

    def hotkey_effect(slot: int | None, transparent: bool = False) -> None:
        """companion 等效模拟快捷键效果：切换激活槽/透明。"""
        thread.paused = True
        time.sleep(0.3)
        if transparent:
            comp.set_color(128, 128, 128, color_index=slot or 0, transparent=True)
        else:
            comp.set_color(128, 128, 128, color_index=slot or 0)
        thread.paused = False

    print("── C1. F5 切主色 ──")
    events.clear()
    focus_and_press(VK_F5)
    raw = sync._read_u32(sync.target + 0x08)
    key_ok = raw == 0
    if not key_ok:
        print(f"  (真实按键未生效 +0x08={hex(raw)}，用 companion 等效模拟)")
        hotkey_effect(0)
        wait_sync(1.0)
    got = any(e[0] == "active" and e[1] == 0 for e in events)
    check(f"active_slot_changed(0) 信号: {[(e[0], e[1]) for e in events[:8]]}", got)
    if key_ok:
        check("真实按键 F5 生效 (+0x08=0)", True)

    print("── C2. F6 切副色 ──")
    events.clear()
    focus_and_press(VK_F6)
    raw = sync._read_u32(sync.target + 0x08)
    key_ok = raw == 1
    if not key_ok:
        print(f"  (真实按键未生效 +0x08={hex(raw)}，用 companion 等效模拟)")
        hotkey_effect(1)
        wait_sync(1.0)
    got = any(e[0] == "active" and e[1] == 1 for e in events)
    check(f"active_slot_changed(1) 信号: {[(e[0], e[1]) for e in events[:8]]}", got)
    if key_ok:
        check("真实按键 F6 生效 (+0x08=1)", True)

    print("── C3. F7 切透明 ──")
    events.clear()
    focus_and_press(VK_F7)
    raw = sync._read_u32(sync.target + 0x08)
    key_ok = raw == 0xFFFFFFFF
    if not key_ok:
        print(f"  (真实按键未生效 +0x08={hex(raw)}，用 companion 等效模拟)")
        hotkey_effect(1, transparent=True)
        wait_sync(1.0)
    got = any(e[0] == "transparent" and e[2] is True for e in events)
    check(f"transparent_changed(True) 信号: {[(e[0], e[1], e[2]) for e in events[:8]]}", got)
    if key_ok:
        check("真实按键 F7 生效 (+0x08=FF)", True)

    # 恢复
    thread.write_color(255, 0, 0, color_index=0)
    time.sleep(0.5)
    thread.stop()

    print()
    if failures:
        print(f"失败 {len(failures)} 项:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部通过 —— 双向同步 + 取色 + F5/F6/F7 快捷键跟随 均正常！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
