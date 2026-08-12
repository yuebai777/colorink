#!/usr/bin/env python3
"""Decisive experiment: compare the memory locations updated by the
COMPANION write path vs the MEMORY write path for the same sub color.

If the brush reads a cache that companion updates but Colorink's memory
write does not, that cache appears in the companion search but not in the
memory search.

    python tools/compare_write_paths.py
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


def _search_all(sync: CSPSync, pattern: bytes) -> set[int]:
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

    hits: set[int] = set()
    addr = ctypes.c_void_p(0)
    mbi = _MEMORY_BASIC_INFORMATION()
    buf_size = 1 << 20
    buf = ctypes.create_string_buffer(buf_size)
    while True:
        if vqe(handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size and mbi.State == 0x1000 and (mbi.Protect & 0x3E) and not (mbi.Protect & 0x100):
            off = 0
            while off < size:
                chunk = min(buf_size, size - off)
                nread = ctypes.c_size_t(0)
                if read_proc(handle, ctypes.c_void_p(base + off), buf, chunk,
                             ctypes.byref(nread)):
                    data = buf.raw[:nread.value]
                    pos = data.find(pattern)
                    while pos != -1:
                        hits.add(base + off + pos)
                        pos = data.find(pattern, pos + 1)
                off += chunk
        addr = ctypes.c_void_p(base + size)
    return hits


def _report(hits: set[int], target: int) -> None:
    for h in sorted(hits):
        rel = h - target
        mark = "  <- 已知 +0x9C" if abs(rel - 0x9C) < 8 else ""
        print(f"    {h:016X}  (rel {rel:+d}){mark}")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    comp = CSPCompanionSync()
    if not comp._connected and not comp.connect():
        print("Companion 不可用。")
        return 1
    assert sync.pm is not None and sync.target is not None

    # 1) companion 写副色 = 蓝 → 搜
    print("── companion 写副色=蓝 → 全进程搜索蓝色模式 ──")
    comp.set_color(0, 0, 255, color_index=1)
    time.sleep(0.6)
    blue_comp = _search_all(sync, _BLUE)
    print(f"  命中 {len(blue_comp)} 处")
    _report(blue_comp, sync.target)

    # 2) 内存写副色 = 蓝 → 搜
    print("\n── 内存写副色=蓝 → 全进程搜索蓝色模式 ──")
    sync.set_color(0, 0, 255, color_index=1)
    time.sleep(0.6)
    blue_mem = _search_all(sync, _BLUE)
    print(f"  命中 {len(blue_mem)} 处")
    _report(blue_mem, sync.target)

    # 3) 对比
    only_comp = blue_comp - blue_mem
    only_mem = blue_mem - blue_comp
    print(f"\n── 对比 ──")
    print(f"companion 专属（内存写未更新）: {len(only_comp)} 处")
    for h in sorted(only_comp):
        rel = h - sync.target
        print(f"    {h:016X}  (rel {rel:+d})")
    print(f"内存写专属（companion 未更新）: {len(only_mem)} 处")
    for h in sorted(only_mem):
        rel = h - sync.target
        print(f"    {h:016X}  (rel {rel:+d})")
    print(f"共同: {len(blue_comp & blue_mem)} 处")

    # 恢复：副色蓝（保持）
    print("\n完成（副色=蓝 保持）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
