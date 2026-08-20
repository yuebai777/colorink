#!/usr/bin/env python3

"""Read-only dump of the PaintTool SAI window tree.

Enumerates every top-level window owned by ``sai2.exe`` (or a process name
given on the command line) and walks the child hierarchy, printing class
name, window text, client size and screen rect for each node. Used to
identify the brush-preview control that has to be nudged after a memory
write so SAI repaints its own colour indicator.

Nothing is posted or written here — enumeration only, safe to run against a
live SAI session with unsaved work.

Usage:
    python tools/probe_sai_windows.py [process_name]
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextA.argtypes = (wintypes.HWND, ctypes.c_char_p, ctypes.c_int)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _text_of(hwnd: int) -> str:
    """Window text, tolerant of SAI's ANSI (cp936-ish) window titles.

    SAI Ver.2 is not a Unicode app: ``GetWindowTextW`` hands back the raw
    ANSI bytes reinterpreted as UTF-16 for some of its panels, which prints
    as mojibake. Reading the ANSI text and decoding with the system code
    page recovers the real panel names.
    """
    buf_a = ctypes.create_string_buffer(1024)
    if user32.GetWindowTextA(hwnd, buf_a, 1024):
        raw = buf_a.value
        for codec in ("mbcs", "gbk", "utf-8"):
            try:
                return raw.decode(codec)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("latin-1")
    buf_w = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf_w, 512)
    return buf_w.value


def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _client_size(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def _find_pids(process_name: str) -> list[int]:
    """Return pids whose image name matches, via toolhelp snapshot."""

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32))
    kernel32.Process32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32))
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == 0xFFFFFFFF:
        return []
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    wanted = process_name.lower().encode("utf-8")
    out: list[int] = []
    if kernel32.Process32First(snap, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == wanted:
                out.append(pe.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break
    kernel32.CloseHandle(snap)
    return out


def _children(hwnd: int) -> list[int]:
    found: list[int] = []

    @WNDENUMPROC
    def cb(child, _lparam):
        found.append(child)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return found


def _describe(hwnd: int, depth: int) -> str:
    left, top, right, bottom = _rect_of(hwnd)
    cw, ch = _client_size(hwnd)
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF  # GWL_STYLE
    vis = "vis" if user32.IsWindowVisible(hwnd) else "hid"
    text = _text_of(hwnd)
    label = f' "{text}"' if text else ""
    return (
        f"{'  ' * depth}hwnd=0x{hwnd:X} [{_class_of(hwnd)}]{label} "
        f"{vis} client={cw}x{ch} screen=({left},{top})-({right},{bottom}) "
        f"style=0x{style:08X}"
    )


def main() -> int:
    try:  # SAI panel names are non-ASCII; never let the console codec kill the dump
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    process_name = sys.argv[1] if len(sys.argv) > 1 else "sai2.exe"
    pids = _find_pids(process_name)
    if not pids:
        print(f"{process_name} is not running")
        return 1
    print(f"{process_name} pids: {pids}")

    tops: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        if _pid_of(hwnd) in pids:
            tops.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)

    for top in tops:
        print()
        print(_describe(top, 0))
        # EnumChildWindows already walks the whole descendant tree; print it
        # flat but indented by real parent depth for readability.
        for child in _children(top):
            depth = 1
            parent = user32.GetParent(child)
            while parent and parent != top:
                depth += 1
                parent = user32.GetParent(parent)
            print(_describe(child, depth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
