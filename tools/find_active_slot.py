#!/usr/bin/env python3
"""Locate CSP 5.1's "active color slot" (CurrentColorIndex) in memory.

The brush always paints with the ACTIVE slot's color (main or sub). To
make Colorink's bg circle actually change the brush color, we must switch
CSP's active slot to the sub slot — which means finding the in-memory
"current color index" field.

This experiment isolates slot activation from transparency:

    python tools/find_active_slot.py

Snapshots:
  A: main=red, sub=blue, both opaque
  B: click the SUB swatch on CSP's color circle (activate sub)
  C: click the TRANSPARENT swatch (still sub active)
  D: click the MAIN swatch (activate main again)

Diffs A→B and C→D isolate the active-slot state; B→C shows what
"sub active + transparent" does (does +0x08 flip? does the sub region
change at all?).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_WINDOW = 0xC0  # main + sub slot regions


def _snap(sync: CSPSync, label: str) -> bytes:
    input(f">>> {label}，然后按 Enter 抓取快照…")
    assert sync.pm is not None and sync.target is not None
    raw = sync.pm.read_bytes(sync.target, _WINDOW)
    print(f"    captured {len(raw)} bytes")
    return raw


def _dump(a: bytes, b: bytes, label: str) -> None:
    changed = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not changed:
        print(f"  {label}: (无变化)")
        return
    print(f"  {label} ({len(changed)} 字节):")
    # 4-byte grouped view（groups 的 key 是组号 off//4，索引与标签都要 ×4）
    groups: dict[int, list[int]] = {}
    for off in changed:
        groups.setdefault(off // 4, []).append(off)
    for base in sorted(groups):
        row_a = " ".join(f"{a[base * 4 + i]:02X}" for i in range(4))
        row_b = " ".join(f"{b[base * 4 + i]:02X}" for i in range(4))
        if row_a != row_b:
            print(f"    +0x{base * 4:03X}:  {row_a}  ->  {row_b}")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    print("先把 主色设为红、副色设为蓝（都非透明），然后开始。")

    a = _snap(sync, "A: 当前状态（主红副蓝）")
    b = _snap(sync, "B: 点击 CSP 色环上的 副色色块（激活副色）")
    c = _snap(sync, "C: 点击 透明色块（副色保持激活）")
    d = _snap(sync, "D: 点击 主色色块（激活主色）")

    print("\n================= 结果 =================")
    _dump(a, b, "A→B 激活副色")
    _dump(b, c, "B→C 副色激活时点透明")
    _dump(c, d, "C→D 激活主色")
    print("\n若 A→B 与 C→D 的公共偏移存在 → 那就是激活槽状态字段")
    print("若 B→C 中 +0x08 变化 → 透明标志是'当前激活槽'全局的（副色也能透明）")
    print("若 B→C 副槽区域(+0x60..+0xBF)变化 → 副槽有独立透明表示")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
