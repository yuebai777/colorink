#!/usr/bin/env python3
"""Diff the whole CSP heap while companion writes the sub color — every
location companion updates (mirror + brush/UI caches) becomes visible.

    python tools/diff_process_memory.py
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402


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


def _regions(sync: CSPSync):
    assert sync.pm is not None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    vqe = k32.VirtualQueryEx
    vqe.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                    ctypes.POINTER(_MBI), ctypes.c_size_t]
    vqe.restype = ctypes.c_size_t
    addr = ctypes.c_void_p(0)
    mbi = _MBI()
    while True:
        if vqe(sync.pm.process_handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        # 堆/私有可读已提交，跳过代码段（0x7FF...）减少 IO
        if size and mbi.State == 0x1000 and (mbi.Protect & 0x3E) and not (mbi.Protect & 0x100) \
                and not (0x7FF000000000 <= base < 0x800000000000):
            yield base, size
        addr = ctypes.c_void_p(base + size)


def _snapshot_to_file(sync: CSPSync, path: str, index: dict) -> None:
    assert sync.pm is not None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_proc = k32.ReadProcessMemory
    read_proc.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_proc.restype = wintypes.BOOL
    with open(path, "wb") as f:
        for base, size in _regions(sync):
            off = 0
            while off < size:
                chunk = min(1 << 20, size - off)
                nread = ctypes.c_size_t(0)
                buf = ctypes.create_string_buffer(chunk)
                if read_proc(sync.pm.process_handle, ctypes.c_void_p(base + off), buf,
                             chunk, ctypes.byref(nread)) and nread.value:
                    f.write(buf.raw[:nread.value])
                else:
                    f.write(b"\x00" * chunk)
                off += chunk
            index.append((base, size))


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    comp = CSPCompanionSync()
    if not comp._connected and not comp.connect():
        print("Companion 不可用。")
        return 1

    # 先写副色=蓝（基线）
    comp.set_color(0, 0, 255, color_index=1)
    time.sleep(0.6)

    tmp_fd, tmp = tempfile.mkstemp(suffix=".bin", prefix="csp_snap_")
    os.close(tmp_fd)
    index: list[tuple[int, int]] = []
    print("快照 A（副色=蓝）…", flush=True)
    _snapshot_to_file(sync, tmp, index)
    print(f"  区域 {len(index)} 段", flush=True)

    print("companion 写副色=绿…", flush=True)
    comp.set_color(0, 255, 0, color_index=1)
    time.sleep(0.6)

    # 读 B 并 diff
    assert sync.pm is not None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_proc = k32.ReadProcessMemory
    read_proc.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_proc.restype = wintypes.BOOL

    print("diff…")
    changed: list[tuple[int, int, int, int]] = []  # (abs_addr, old, new, size_run)
    with open(tmp, "rb") as f:
        for base, size in index:
            a = f.read(size)
            off = 0
            while off < size:
                chunk = min(1 << 20, size - off)
                nread = ctypes.c_size_t(0)
                buf = ctypes.create_string_buffer(chunk)
                if read_proc(sync.pm.process_handle, ctypes.c_void_p(base + off), buf,
                             chunk, ctypes.byref(nread)) and nread.value:
                    b = buf.raw[:nread.value]
                    for i in range(0, chunk, 4):
                        if a[off + i:off + i + 4] != b[i:i + 4]:
                            old = int.from_bytes(a[off + i:off + i + 4], "little")
                            new = int.from_bytes(b[i:i + 4], "little")
                            changed.append((base + off + i, old, new, 0))
                off += chunk
    os.remove(tmp)

    print(f"\n变化 u32 数量: {len(changed)}")
    # 归组连续变化
    changed.sort()
    groups: list[tuple[int, int, int, int]] = []  # start, old, new, count
    for addr, old, new, _ in changed:
        if groups and addr == groups[-1][0] + groups[-1][3] * 4:
            groups[-1] = (groups[-1][0], groups[-1][1], new, groups[-1][3] + 1)
        else:
            groups.append((addr, old, new, 1))

    for start, old, new, count in groups:
        rel = start - (sync.target or 0)
        print(f"  {start:016X}  (rel {rel:+d})  {count}×u32: 0x{old:08X} -> 0x{new:08X}")

    # 恢复副色蓝
    comp.set_color(0, 0, 255, color_index=1)
    print("\n已恢复副色=蓝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
