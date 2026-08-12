#!/usr/bin/env python3
"""CSP 5.1 memory layout probe — finds the REAL write offsets.

Round 2 exposed a contradiction: Colorink writes the main color at
``target + 0x20/0x24/0x28`` (``_RGB_U32_OFFS``) but the manual diffing
showed the color changing at ``+0x3C``. This script settles it by
writing colors through the existing API and reading back which offsets
actually changed (CSP mirrors some slots, so both may move).

It also probes the SUB slot (base ``target + 0x60``) the same way, and
finishes with an interactive transparent-sub experiment.

No CSP UI interaction needed for steps A/B — just run it:

    python tools/probe_csp_layout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_MAIN_WIN = (0x00, 0x60)   # main slot region
_SUB_WIN = (0x60, 0xC0)    # sub slot region (base target+0x60)


def _snap(sync: CSPSync, lo: int, hi: int) -> bytes:
    assert sync.pm is not None and sync.target is not None
    return sync.pm.read_bytes(sync.target + lo, hi - lo)


def _diff_print(a: bytes, b: bytes, label: str, base: int) -> None:
    changed = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not changed:
        print(f"  {label}: (无变化)")
        return
    print(f"  {label} ({len(changed)} 字节):")
    for i in changed:
        print(f"    +0x{base + i:04X}  {a[i]:02X} -> {b[i]:02X}")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    print("color_format =", sync.color_format)
    if sync.color_format != "rgb_u32":
        print("警告：本脚本的偏移假设基于 5.1 (rgb_u32) 布局。")

    # ── Step A: write RED via the existing API, diff both regions ────────
    print("\n=== A: set_color(255, 0, 0) 现有 API ===")
    main_before = _snap(sync, *_MAIN_WIN)
    sub_before = _snap(sync, *_SUB_WIN)
    ok = sync.set_color(255, 0, 0)
    print(f"  set_color 返回: {ok}")
    main_after = _snap(sync, *_MAIN_WIN)
    sub_after = _snap(sync, *_SUB_WIN)
    _diff_print(main_before, main_after, "主槽区域 (+0x00..+0x5F)", 0x00)
    _diff_print(sub_before, sub_after, "副槽区域 (+0x60..+0xBF)", 0x60)

    # ── Step B: write GREEN, diff again ──────────────────────────────────
    print("\n=== B: set_color(0, 255, 0) ===")
    main_before = _snap(sync, *_MAIN_WIN)
    ok = sync.set_color(0, 255, 0)
    main_after = _snap(sync, *_MAIN_WIN)
    _diff_print(main_before, main_after, "主槽区域 (+0x00..+0x5F)", 0x00)

    # ── Step C: interactive — make the SUB color transparent ─────────────
    print("\n=== C: 副色透明定向实验 ===")
    input(">>> 先把 主色 和 副色 都设为 非透明（随便选个颜色），然后按 Enter…")
    s1 = _snap(sync, *_SUB_WIN)
    input(">>> 现在点击 副色色块（激活副色），再点击 透明色块，然后按 Enter…")
    s2 = _snap(sync, *_SUB_WIN)
    _diff_print(s1, s2, "副槽区域变化 (+0x60..+0xBF)", 0x60)
    main1 = _snap(sync, *_MAIN_WIN)
    _diff_print(s1[:0x08], main1[:0x08], "主槽 +0x00..+0x07（对照）", 0x00)
    input(">>> 把颜色恢复正常后按 Enter 退出…")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n================= 小结 =================")
    print("  - 主色写入实际生效的偏移 = A/B 步中变化的偏移（若含 +0x20..+0x28 则现有代码正确；")
    print("    若含 +0x3C..+0x3F 则真正通道是 +0x3C，需要改 _RGB_U32_OFFS）")
    print("  - 副色透明 = C 步中副槽区域变化的偏移（若 +0x68 变 FF 则副槽透明标志 = +0x68）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
