#!/usr/bin/env python3
"""CSP sub-color (background) slot locator — v2.

Round 1 showed the sub-color channels change around ``target + 0x9E``
while the main-color channels sit at ``+0x3C`` — suggesting the sub slot
base is near ``target + 0x60``. This version:

* fixes the offset display (round 1 divided byte offsets by 4),
* prints the full per-transition byte lists (sub-only, main-only, both),
* adds a FOURTH step: set the SUB color to TRANSPARENT, so the sub slot's
  transparent flag offset can be located the same way the main slot's
  (+0x08) was.

Usage
-----
1. Open CSP; set MAIN = red, SUB = blue.
2. Run::

       python tools/find_csp_subcolor.py

3. Follow the prompts (4 snapshots):
   - S1: main=red,   sub=blue
   - S2: main=red,   sub=green      (only sub changes)
   - S3: main=green, sub=blue       (only main changes)
   - S4: main=green, sub=TRANSPARENT (sub changes to transparent)

The sub-slot base is inferred from the sub-only color bytes; the sub
transparent flag is the offset that changed in S3→S4.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_WINDOW = 0x200  # bytes scanned after the main slot base


def _snapshot(sync: CSPSync, label: str) -> bytes:
    input(f">>> {label}，然后按 Enter 抓取快照…")
    assert sync.pm is not None and sync.target is not None
    raw = sync.pm.read_bytes(sync.target, _WINDOW)
    print(f"    captured {len(raw)} bytes @ 0x{sync.target:X}")
    return raw


def _diff(a: bytes, b: bytes) -> set[int]:
    return {off for off in range(min(len(a), len(b))) if a[off] != b[off]}


def _dump_offsets(a: bytes, b: bytes, label: str, offsets: set[int]) -> None:
    if not offsets:
        print(f"  {label}: (无)")
        return
    print(f"  {label} ({len(offsets)} 字节):")
    for off in sorted(offsets):
        print(f"    +0x{off:04X}  {label}={b[off]:02X}  前值={a[off]:02X}")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X} (主色槽基址)")
    cur = sync.get_color()
    print("当前颜色:", cur if cur else "(读回失败)")

    s1 = _snapshot(sync, "S1: 主色=红、副色=蓝")
    s2 = _snapshot(sync, "S2: 副色改成 绿（主色不动）")
    s3 = _snapshot(sync, "S3: 主色改成 绿、副色改回 蓝")
    s4 = _snapshot(sync, "S4: 副色改成 透明（主色不动）")

    sub_change = _diff(s1, s2)          # 副色 蓝→绿
    main_change = _diff(s1, s3)         # 主色 红→绿
    sub_trans = _diff(s3, s4)           # 副色 蓝→透明

    sub_only = sub_change - main_change
    main_only = main_change - sub_change
    both = sub_change & main_change

    print("\n================= 副色变化（蓝→绿） =================")
    _dump_offsets(s1, s2, "副色变", sub_change)
    print("\n================= 主色变化（红→绿） =================")
    _dump_offsets(s1, s3, "主色变", main_change)

    print("\n================= 副色专属偏移（副色槽候选） =================")
    _dump_offsets(s1, s2, "副专属", sub_only)
    print("\n================= 主色专属偏移 =================")
    _dump_offsets(s1, s3, "主专属", main_only)
    print("\n================= 两段都变 =================")
    _dump_offsets(s1, s2, "both", both)

    # 副色透明标志：S3→S4 变化
    print("\n================= 副色透明诊断（S3→S4 变化） =================")
    _dump_offsets(s3, s4, "副透明", sub_trans)

    print("\n================= 推断 =================")
    if sub_only:
        lo = min(sub_only)
        hi = max(sub_only)
        print(f"  副色颜色字节范围: +0x{lo:04X} .. +0x{hi:04X}")
        print(f"  相对主色通道(+0x3C)的偏移: +0x{lo - 0x3C:04X}（若副槽与主槽同构，此为副槽基址偏移）")
        print(f"  候选副槽基址 = target + 0x{lo - 0x3C:04X}")
        print(f"  若副槽同构，副槽透明标志应在 target + 0x{lo - 0x3C + 0x08:04X}")
    if sub_trans:
        print("  副色透明变化偏移:",
              [f"+0x{o:04X}" for o in sorted(sub_trans)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
