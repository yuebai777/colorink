#!/usr/bin/env python3
"""CSP transparent-color memory flag locator.

The companion protocol exposes transparency as an explicit boolean
(``IsColorTransparent`` / ``IsCurrentColorTransparent``), but the in-memory
brush-color struct (RGB/CMYK/HSV/HLS u32 slots) has no documented alpha or
transparent flag. This script finds that flag by diffing the color slot
across manual transparent/non-transparent transitions.

Usage
-----
1. Open CSP and pick a solid color (e.g. red) as the drawing color.
2. Run this script from the repo root::

       python tools/find_csp_transparent.py

3. When prompted "snapshot A (solid color)":
   - press Enter -> snapshot A is taken
   - in CSP, click the TRANSPARENT swatch on the color circle
   - press Enter -> snapshot B is taken
   - in CSP, pick another solid color (e.g. blue)
   - press Enter -> snapshot C is taken
   - in CSP, click TRANSPARENT again
   - press Enter -> snapshot D is taken

The script then diffs A/B and C/D and prints every offset that changed in
*both* transitions. Offsets that changed in both are either the transparent
flag itself or memory the host rewrites on every color change; offsets that
changed in A/C (red vs blue) are the color channels. The intersection of
the two transparent diffs minus the color channels is the flag candidate.

Requires CSP running with a version profile resolvable by csp_brush_link
(auto-detected from the exe). Run with the same privileges as CSP.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_WINDOW = 0x100  # bytes of the color slot to snapshot


def _snapshot(sync: CSPSync, label: str) -> bytes | None:
    if sync.pm is None or sync.target is None:
        return None
    input(f">>> 现在{sync.target  and '' or ''}{label}，然后按 Enter 抓取快照…（在 CSP 里操作完再回来按 Enter）")
    raw = sync.pm.read_bytes(sync.target, _WINDOW)
    print(f"    captured {len(raw)} bytes @ 0x{sync.target:X}")
    return raw


def _diff_rows(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    return [
        (off, a[off], b[off])
        for off in range(min(len(a), len(b)))
        if a[off] != b[off]
    ]


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    print("快照窗口:", _WINDOW, "bytes")
    print("注意：color_format =", sync.color_format)

    # 读回当前颜色确认连接有效
    cur = sync.get_color()
    print("当前颜色:", cur if cur else "(读回失败)")

    a = _snapshot(sync, "选择 非透明颜色（如红色）")
    b = _snapshot(sync, "在 CSP 色环上点击 透明色块")
    c = _snapshot(sync, "选择 另一个非透明颜色（如蓝色）")
    d = _snapshot(sync, "再次点击 透明色块")
    if None in (a, b, c, d):
        print("快照失败（连接断开？）")
        return 1
    assert a is not None and b is not None and c is not None and d is not None

    ab = _diff_rows(a, b)
    cd = _diff_rows(c, d)
    ac = _diff_rows(a, c)

    # 两次"非透明→透明"都变化的偏移
    both = {off for off, _, _ in ab} & {off for off, _, _ in cd}
    # 其中也随颜色变化的偏移（红/蓝不同）→ 更可能是颜色通道，剔除
    color_changed = {off for off, _, _ in ac}

    print("\n================= 对比结果 =================")
    print(f"A→B（红→透明）变化 {len(ab)} 字节；C→D（蓝→透明）变化 {len(cd)} 字节")
    print(f"A→C（红→蓝）变化 {len(ac)} 字节（颜色通道）")
    print()
    print("两次透明切换都变化、且不随颜色变化的偏移（透明标志候选）:")
    candidates = sorted(both - color_changed)
    if not candidates:
        print("  （无 —— 透明标志可能在颜色通道里，或窗口太小）")
    for off in candidates:
        print(
            f"  +0x{off:03X}  A={a[off]:02X} B={b[off]:02X} "
            f"C={c[off]:02X} D={d[off]:02X}"
        )

    print("\n两次透明切换都变化（含颜色通道）的完整列表:")
    for off in sorted(both):
        mark = "  <- 颜色相关" if off in color_changed else "  <- 候选"
        print(
            f"  +0x{off:03X}  A={a[off]:02X} B={b[off]:02X} "
            f"C={c[off]:02X} D={d[off]:02X}{mark}"
        )

    print("\n================= 完整快照 =================")
    for off in range(0, _WINDOW, 4):
        row = " ".join(
            f"{snap[off + i]:02X}" for snap in (a, b, c, d) for i in range(4)
        )
        marker = " *" if off in both else ""
        print(f"+0x{off:03X}:  A={a[off]:04X}  B={b[off]:04X}  C={c[off]:04X}  D={d[off]:04X}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
