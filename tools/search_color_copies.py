#!/usr/bin/env python3
"""Find every copy of the sub-color value in CSP's memory.

The brush reads the sub color from a cache that may live outside the
±32KB scan window. This script writes the sub color to BLUE, searches the
whole process for the blue HSV pattern (AA AA AA AA FF FF FF FF FF FF FF FF),
then writes GREEN and searches again — locations that match BOTH patterns
are real sub-color storages (mirror + brush cache).

    python tools/search_color_copies.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path
from ctypes import wintypes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_MEM_COMMIT = 0x1000
_PAGE_READABLE = 0x02 | 0x04 | 0x08 | 0x10 | 0x20  # read / rw / wc / execute-read
_BLUE = bytes.fromhex("aa aa aa aa ff ff ff ff ff ff ff ff")
_GREEN = bytes.fromhex("55 55 55 55 ff ff ff ff ff ff ff ff")


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _search_all(sync: CSPSync, pattern: bytes) -> list[int]:
    assert sync.pm is not None
    handle = sync.pm.process_handle
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vqe = k32.VirtualQueryEx
    vqe.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                    ctypes.POINTER(_MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    vqe.restype = ctypes.c_size_t
    read_proc = k32.ReadProcessMemory
    read_proc.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_proc.restype = wintypes.BOOL

    hits: list[int] = []
    addr = ctypes.c_void_p(0)
    mbi = _MEMORY_BASIC_INFORMATION()
    buf_size = 1 << 20  # 1MB chunks
    buf = ctypes.create_string_buffer(buf_size)
    while True:
        if vqe(handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size and mbi.State == _MEM_COMMIT and (mbi.Protect & _PAGE_READABLE) and \
                not (mbi.Protect & 0x100):  # skip guard pages
            # also skip the module's static range? keep everything readable
            off = 0
            while off < size:
                chunk = min(buf_size, size - off)
                nread = ctypes.c_size_t(0)
                if read_proc(handle, ctypes.c_void_p(base + off), buf, chunk,
                             ctypes.byref(nread)):
                    data = buf.raw[:nread.value]
                    pos = data.find(pattern)
                    while pos != -1:
                        hits.append(base + off + pos)
                        pos = data.find(pattern, pos + 1)
                off += chunk
        addr = ctypes.c_void_p(base + size)
    return hits


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")

    # 1) 写副色 = 蓝，全进程搜索
    print("\n── 写副色=蓝，全进程搜索模式 ──")
    sync.set_color(0, 0, 255, color_index=1)
    time.sleep(0.5)
    blue_hits = _search_all(sync, _BLUE)
    print(f"  蓝色模式命中 {len(blue_hits)} 处:")
    for h in blue_hits:
        rel = h - sync.target
        mark = "  <- 已知镜像 +0x9C" if abs(rel - 0x9C) < 8 else ""
        print(f"    {h:016X}  (rel {rel:+d}){mark}")

    # 2) 写副色 = 绿，全进程搜索
    print("\n── 写副色=绿，全进程搜索模式 ──")
    sync.set_color(0, 255, 0, color_index=1)
    time.sleep(0.5)
    green_hits = _search_all(sync, _GREEN)
    print(f"  绿色模式命中 {len(green_hits)} 处:")
    for h in green_hits:
        rel = h - sync.target
        mark = "  <- 已知镜像 +0x9C" if abs(rel - 0x9C) < 8 else ""
        print(f"    {h:016X}  (rel {rel:+d}){mark}")

    # 3) 交集 = 真实副色存储副本
    print("\n── 蓝绿都命中的位置（真实副色存储副本）──")
    common = set(blue_hits) & set(green_hits)
    if not common:
        print("  (无交集 —— 模式可能被 CSP 规范化，或副本不连续)")
    for h in sorted(common):
        rel = h - sync.target
        mark = "  <- 已知镜像 +0x9C" if abs(rel - 0x9C) < 8 else ""
        print(f"    {h:016X}  (rel {rel:+d}){mark}")

    # 恢复副色蓝
    sync.set_color(0, 0, 255, color_index=1)
    print("\n已恢复副色=蓝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
