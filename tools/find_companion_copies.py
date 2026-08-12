#!/usr/bin/env python3
"""Find companion's sub-color copy set (data section only)."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402

_GREEN = bytes.fromhex("55 55 55 55 ff ff ff ff ff ff ff ff")


class _MBI(ctypes.Structure):
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


def _search_data(sync: CSPSync, pattern: bytes) -> list[int]:
    assert sync.pm is not None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vqe = k32.VirtualQueryEx
    vqe.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                    ctypes.POINTER(_MBI), ctypes.c_size_t]
    vqe.restype = ctypes.c_size_t
    read_proc = k32.ReadProcessMemory
    read_proc.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_proc.restype = wintypes.BOOL
    hits: list[int] = []
    addr = ctypes.c_void_p(0)
    mbi = _MBI()
    buf = ctypes.create_string_buffer(1 << 20)
    while True:
        if vqe(sync.pm.process_handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size and mbi.State == 0x1000 and (mbi.Protect & 0x3E) and not (mbi.Protect & 0x100) \
                and not (0x7FF000000000 <= base < 0x800000000000):
            off = 0
            while off < size:
                chunk = min(1 << 20, size - off)
                nread = ctypes.c_size_t(0)
                if read_proc(sync.pm.process_handle, ctypes.c_void_p(base + off), buf,
                             chunk, ctypes.byref(nread)):
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
    comp = CSPCompanionSync()
    if not comp._connected and not comp.connect():
        print("Companion 不可用。")
        return 1

    print("── companion 写副色=绿 ──")
    comp.set_color(0, 255, 0, color_index=1)
    time.sleep(0.6)
    hits = _search_data(sync, _GREEN)
    print(f"数据区绿色模式命中 {len(hits)} 处:")
    for h in sorted(hits):
        rel = h - (sync.target or 0)
        mark = "  <- 已知 +0x9C" if abs(rel - 0x9C) < 8 else ""
        print(f"    {h:016X}  (rel {rel:+d}){mark}")

    # 恢复副色蓝
    comp.set_color(0, 0, 255, color_index=1)
    print("\n已恢复副色=蓝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
