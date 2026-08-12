#!/usr/bin/env python3
"""Simulate the user pressing X (main/sub swap) in CSP via SendInput and
watch whether the sub-color storage (+0x9C) survives the slot switch.

    python tools/simulate_x_switch.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

# --- Win32 ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_X = 0x58


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


def send_key(vk: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    for flags in (0, KEYEVENTF_KEYUP):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.dwFlags = flags
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def focus_csp() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = user32.FindWindowW(None, None)
    # 通过枚举找 CLIPStudioPaint 主窗口
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and "CLIPStudioPaint" in _proc_name(pid.value):
            result.append(hwnd)
            return False
        return True

    user32.EnumWindows(cb, 0)
    if not result:
        return False
    user32.SetForegroundWindow(result[0])
    return True


_proc_cache = {}


def _proc_name(pid: int) -> str:
    if pid in _proc_cache:
        return _proc_cache[pid]
    import subprocess
    try:
        name = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            creationflags=0x08000000,
        ).decode("gbk", errors="ignore")
        name = name.strip().strip('"').split('","')[0]
    except Exception:
        name = ""
    _proc_cache[pid] = name
    return name


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    assert sync.pm is not None and sync.target is not None
    t = sync.target

    def snap(label: str) -> None:
        print(f"  {label}: +0x08=0x{sync._read_u32(t + 0x08):08X} "
              f"+0x9C=0x{sync._read_u32(t + 0x9C):08X} "
              f"+0xA0=0x{sync._read_u32(t + 0xA0):08X} "
              f"+0xA4=0x{sync._read_u32(t + 0xA4):08X}")

    print("── 0. 当前状态 ──")
    snap("初始")

    print("\n── 1. 内存写副色 = 红 (H=0°, S=100%, V=100%) ──")
    sync.set_color(255, 0, 0, color_index=1)
    time.sleep(0.4)
    snap("写后")

    print("\n── 2. 发送 X 键（切主副）──")
    if not focus_csp():
        print("  未找到 CSP 窗口（尝试继续）")
    time.sleep(0.3)
    send_key(VK_X)
    time.sleep(0.8)
    snap("按 X 后")

    print("\n── 3. 再按 X（切回）──")
    send_key(VK_X)
    time.sleep(0.8)
    snap("再按 X 后")

    # 恢复副色蓝
    sync.set_color(0, 0, 255, color_index=1)
    print("\n已恢复副色=蓝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
