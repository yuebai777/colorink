#!/usr/bin/env python3
"""Verify sub-color HSV layout (+0x9C H / +0xA0 S / +0xA4 V?) and that
writing +0x08 switches the active slot — validated via companion read-back
(CSP's official state).

Fully automatic:

    python tools/probe_sub_hsv.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402

_U32 = 0xFFFFFFFF


def _read_u32(sync: CSPSync, off: int) -> str:
    assert sync.pm is not None and sync.target is not None
    return f"0x{sync._read_u32(sync.target + off):08X}"


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    comp = CSPCompanionSync()
    if not comp._connected and not comp.connect():
        print("Companion 不可用。")
        return 1
    print(f"target = 0x{sync.target:X}")
    print(f"基线: +0x08={_read_u32(sync, 0x08)} +0x9C={_read_u32(sync, 0x9C)} "
          f"+0xA0={_read_u32(sync, 0xA0)} +0xA4={_read_u32(sync, 0xA4)}")

    # 1) companion 写副色为特殊 HSV（H=180°, S=50%, V=50%）→ S/V 偏移暴露
    hsv = (0x7FFFFFFF, 0x7FFFFFFF, 0x7FFFFFFF)
    print("\n── 1. companion 写副色 HSV(180°, 50%, 50%) ──")
    ok = comp.set_color(128, 192, 128, hsv_u32=hsv, color_index=1)
    time.sleep(0.6)
    print(f"  返回 {ok}; +0x9C={_read_u32(sync, 0x9C)} +0xA0={_read_u32(sync, 0xA0)} "
          f"+0xA4={_read_u32(sync, 0xA4)} +0xA8={_read_u32(sync, 0xA8)}")

    # 2) 内存写 +0x08=1（激活副色）→ companion 读回确认
    print("\n── 2. 内存写 +0x08 = 1 (激活副色) ──")
    sync._write_u32(sync.target + 0x08, 1)
    time.sleep(0.4)
    rb = comp.get_color_hsv()
    print(f"  companion 读回: index={rb.get('index') if rb else None} "
          f"transparent={rb.get('transparent') if rb else None} "
          f"hsv=({(rb['h'] if rb else 0):.0f}°, {(rb['s'] if rb else 0):.0f}%, {(rb['v'] if rb else 0):.0f}%)")

    # 3) 内存写 +0x08=0（激活主色）
    print("\n── 3. 内存写 +0x08 = 0 (激活主色) ──")
    sync._write_u32(sync.target + 0x08, 0)
    time.sleep(0.4)
    rb = comp.get_color_hsv()
    print(f"  companion 读回: index={rb.get('index') if rb else None}")

    # 4) 内存写 +0x08 = FF（透明）→ 读回确认 transparent
    print("\n── 4. 内存写 +0x08 = 0xFFFFFFFF (透明) ──")
    sync._write_u32(sync.target + 0x08, 0xFFFFFFFF)
    time.sleep(0.4)
    rb = comp.get_color_hsv()
    print(f"  companion 读回: index={rb.get('index') if rb else None} "
          f"transparent={rb.get('transparent') if rb else None}")

    # 5) 内存写 +0x08 = 0（清透明+激活主色）
    print("\n── 5. 内存写 +0x08 = 0 (清除透明) ──")
    sync._write_u32(sync.target + 0x08, 0)
    time.sleep(0.4)
    rb = comp.get_color_hsv()
    print(f"  companion 读回: index={rb.get('index') if rb else None} "
          f"transparent={rb.get('transparent') if rb else None}")

    # 恢复：companion 写主色红（激活主色）
    comp.set_color(255, 0, 0, color_index=0)
    print("\n恢复完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
