#!/usr/bin/env python3
"""Locate the SUB color's AUTHORITATIVE storage (brush source).

Writing target+0x9C (float32) only changes CSP's UI mirror — the brush
reads the sub color from a different memory location when the sub slot is
activated. This script scans a wide region around the main slot while you
manually change the SUB color, and reports every byte that changed —
the authoritative sub storage is among them.

    python tools/find_sub_authority.py

Steps:
  1. Set MAIN=red, SUB=blue in CSP.
  2. Run, snapshot A is taken automatically.
  3. In CSP change ONLY the SUB color to green.
  4. Press Enter for snapshot B.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_RANGE = 0x2000  # scan target ± 8KB each side


def _read_region(sync: CSPSync, start: int, size: int) -> bytes:
    assert sync.pm is not None
    return sync.pm.read_bytes(start, size)


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    assert sync.pm is not None and sync.target is not None
    start = sync.target - _RANGE
    size = _RANGE * 2
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    print(f"扫描区域: 0x{start:X} ~ 0x{start + size:X} ({size // 1024} KB)")
    print("当前读到的颜色（对照确认 CSP 状态）:")
    print(f"  主色: {sync.get_color()}")
    print(f"  副色(镜像): {sync.get_sub_color()}")

    input("\n>>> 第一步：请现在去 CSP 里设置【主色=红、副色=蓝】，")
    input("    设置完成后回到这里，按 Enter 抓取快照 A…")
    a = _read_region(sync, start, size)
    print(f"    快照 A 完成（{size} 字节）")

    input("\n>>> 第二步：现在去 CSP 里把【副色改成绿】（主色不要动），")
    input("    改完后回到这里，按 Enter 抓取快照 B…")
    b = _read_region(sync, start, size)
    print(f"    快照 B 完成")

    changed = [i for i in range(size) if a[i] != b[i]]
    print(f"\n变化字节总数: {len(changed)}")

    if not changed:
        print("无变化 —— 扫描范围可能不够，或副色改动没落到此区域。")
        return 0

    # Group into contiguous runs
    runs: list[tuple[int, int]] = []
    run_start = changed[0]
    prev = changed[0]
    for off in changed[1:]:
        if off - prev > 8:
            runs.append((run_start, prev))
            run_start = off
        prev = off
    runs.append((run_start, prev))

    print(f"\n变化区域 {len(runs)} 段:")
    for lo, hi in runs:
        rel_lo = lo - _RANGE
        rel_hi = hi - _RANGE
        # Show the 4-byte-aligned window around the run
        win = ((lo // 4) * 4, ((hi // 4) * 4) + 4)
        rows = []
        for base in range(win[0], win[1], 4):
            va = a[base:base + 4]
            vb = b[base:base + 4]
            if va != vb:
                rows.append(
                    f"    {base + start:016X}  (rel +0x{base - _RANGE:04X})  "
                    f"{va.hex(' ')}  ->  {vb.hex(' ')}"
                )
        print(f"  段 @ rel +0x{rel_lo:04X}..+0x{rel_hi:04X} ({hi - lo + 1} 字节):")
        print("\n".join(rows))

    # Candidates: 4-byte values that look like 0..1 floats or 0..255 u8
    print("\n================= 候选（4 字节组，值像颜色） =================")
    for lo, hi in runs:
        for base in range((lo // 4) * 4, hi, 4):
            va = a[base:base + 4]
            vb = b[base:base + 4]
            if va == vb:
                continue
            try:
                import struct
                fa = struct.unpack("<f", va)[0]
                fb = struct.unpack("<f", vb)[0]
                if 0.0 <= fa <= 1.0 and 0.0 <= fb <= 1.0:
                    print(f"  {base + start:016X}  rel +0x{base - _RANGE:04X}  "
                          f"float {fa:.3f} -> {fb:.3f}  (候选)")
            except Exception:
                pass
    print("\n把上面的候选地址发给我；若候选为空，把'变化区域'段也发我。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
