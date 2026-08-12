#!/usr/bin/env python3
"""Sub-color copy stability test: write all copies, then watch them over
time (and across a simulated X keypress) to see if CSP overwrites them."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_GREEN = bytes.fromhex("55 55 55 55 ff ff ff ff ff ff ff ff")


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


def _send_x(hwnd) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    for flags in (0, 0x0002):
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class INPUT(ctypes.Structure):
            class _I(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _anonymous_ = ("i",)
            _fields_ = [("type", wintypes.DWORD), ("i", _I)]
        inp = INPUT()
        inp.type = 1
        inp.ki.wVk = 0x58  # X
        inp.ki.dwFlags = flags
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    assert sync.pm is not None and sync.target is not None

    print("── 写副色=绿（全副本）──")
    ok = sync.set_color(0, 255, 0, color_index=1)
    addrs = list(getattr(sync, "_sub_copy_addrs", None) or [])
    print(f"  写入 {ok}, 副本 {len(addrs)} 处")

    def check(tag: str) -> None:
        bad = []
        for addr in addrs:
            try:
                if sync.pm.read_bytes(addr, 12) != _GREEN:
                    bad.append(addr)
            except Exception:
                bad.append(addr)
        print(f"  {tag}: {len(addrs) - len(bad)}/{len(addrs)} 处仍为绿"
              + (f"  被覆盖: {[hex(b) for b in bad[:6]]}" if bad else ""))

    check("写入后")
    for i in range(1, 6):
        time.sleep(2)
        check(f"{i*2}s")

    hwnd = _find_csp_window(sync.pid or 0)
    if hwnd:
        print("── 模拟按 X（切主副）──")
        _send_x(hwnd)
        time.sleep(1)
        check("按 X 后")
        _send_x(hwnd)  # 切回
        time.sleep(1)
        check("再按 X 后")
    else:
        print("未找到 CSP 窗口，跳过切槽测试")

    sync.set_color(0, 0, 255, color_index=1)
    print("已恢复副色=蓝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
