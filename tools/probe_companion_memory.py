#!/usr/bin/env python3
"""Crack the CSP sub-color storage using the Companion protocol as the
official write path.

The companion protocol is accepted by CSP natively, so every internal
storage (authoritative brush source + UI mirrors) is updated consistently.
By writing colors through companion and diffing the memory around the
main slot, we locate the sub-color's real storage and encoding, and can
test whether CSP accepts sub-slot transparency at all.

Fully automatic (no CSP UI interaction):

    python tools/probe_companion_memory.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402

_RANGE = 0x8000  # scan target ± 32KB each side


def _read_region(sync: CSPSync, start: int, size: int) -> bytes:
    assert sync.pm is not None
    return sync.pm.read_bytes(start, size)


def _diff_report(a: bytes, b: bytes, base_addr: int, label: str,
                 known: set[int]) -> None:
    changed = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not changed:
        print(f"  {label}: (无变化)")
        return
    print(f"  {label} ({len(changed)} 字节):")
    runs: list[tuple[int, int]] = []
    rs = changed[0]
    prev = changed[0]
    for off in changed[1:]:
        if off - prev > 8:
            runs.append((rs, prev))
            rs = off
        prev = off
    runs.append((rs, prev))
    for lo, hi in runs:
        rel = lo - _RANGE
        rows = []
        for base in range((lo // 4) * 4, hi + 1, 4):
            va = a[base:base + 4]
            vb = b[base:base + 4]
            if va != vb:
                mark = "  <- 新发现" if base - _RANGE not in known else ""
                rows.append(
                    f"    rel +0x{base - _RANGE:05X}  {va.hex(' ')} -> {vb.hex(' ')}{mark}"
                )
        print(f"  段 rel +0x{rel:05X}..+0x{hi - _RANGE:05X}:")
        print("\n".join(rows))


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP 内存 —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    comp = CSPCompanionSync()
    if not comp._has_session() and not comp.connect():
        print("Companion 会话不可用 —— 请先在 Colorink 里连接手机模式。")
        return 1
    if not comp._connected:
        comp.connect()
    assert sync.pm is not None and sync.target is not None
    start = sync.target - _RANGE
    size = _RANGE * 2
    print(f"内存 target = 0x{sync.target:X}")
    print(f"扫描区域: 0x{start:X} ~ 0x{start + size:X} ({size // 1024} KB)")
    print(f"Companion: {'已连接' if comp._connected else '未连接'}")

    known: set[int] = {0x08, 0x9C}  # 已识别的偏移（相对 target）

    # 基线
    base = _read_region(sync, start, size)

    def step(label: str, fn) -> None:
        print(f"\n── {label} ──")
        before = _read_region(sync, start, size)
        ok = fn()
        time.sleep(0.6)  # 等 CSP 处理
        after = _read_region(sync, start, size)
        print(f"  执行返回: {ok}")
        _diff_report(before, after, start, "内存变化", known)

    # 1) 副色蓝
    step("1. companion 写副色 = 蓝 (ColorIndex=1)",
         lambda: comp.set_color(0, 0, 255, color_index=1))
    # 2) 副色绿
    step("2. companion 写副色 = 绿 (ColorIndex=1)",
         lambda: comp.set_color(0, 255, 0, color_index=1))
    # 3) 副色透明（试探 CSP 是否接受）
    step("3. companion 写副色 = 透明 (ColorIndex=1, transparent=True)",
         lambda: comp.set_color(0, 255, 0, color_index=1, transparent=True))
    # 4) 主色透明（对照验证 +0x08）
    step("4. companion 写主色 = 透明 (ColorIndex=0, transparent=True)",
         lambda: comp.set_color(255, 0, 0, color_index=0, transparent=True))
    # 5) 恢复：主色红、副色蓝、非透明
    step("5. 恢复 主色=红 副色=蓝 非透明",
         lambda: comp.set_color(255, 0, 0, color_index=0, transparent=False)
         and comp.set_color(0, 0, 255, color_index=1, transparent=False))

    print("\n================= 判定 =================")
    print("步骤1/2 副色变化中，除 +0x9C 外的新位置 = 副色权威副本")
    print("步骤3 的变化若含某个新偏移 = 副色透明标志（CSP 接受副色透明）")
    print("步骤4 的变化 = 主色透明标志（对照 +0x08）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
