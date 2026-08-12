#!/usr/bin/env python3
"""Runtime diagnostics for the CSP 5.1 memory backend (main/sub/transparent).

Runs every write+read path Colorink uses and prints the internal state at
each step, so a broken link (write not landing, target drifting, read-back
mismatch) is visible immediately. No UI interaction needed — the script
writes and reads through the same API Colorink uses.

    python tools/diag_memory_sync.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402


def _step(label: str) -> None:
    print(f"\n── {label} ──")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version}")
    print(f"初始 target = 0x{sync.target:X}")

    # 1) 主色写+读
    _step("1. 主色写入 (set_color 255,0,0, color_index=0)")
    print(f"  target = 0x{sync.target:X}")
    ok = sync.set_color(255, 0, 0, color_index=0)
    print(f"  写入返回: {ok}")
    print(f"  写后 target = 0x{sync.target:X}")
    main = sync.get_color()
    print(f"  读回主色: {main}")

    # 2) 副色写+读
    _step("2. 副色写入 (set_color 0,0,255, color_index=1)")
    print(f"  target = 0x{sync.target:X}")
    ok = sync.set_color(0, 0, 255, color_index=1)
    print(f"  写入返回: {ok}")
    print(f"  写后 target = 0x{sync.target:X}")
    sub = sync.get_sub_color()
    print(f"  读回副色: {sub}")

    # 3) 透明写+读
    _step("3. 主色透明写入 (transparent=True)")
    ok = sync.set_color(255, 0, 0, color_index=0, transparent=True)
    print(f"  写入返回: {ok}")
    main_t = sync.get_color()
    print(f"  读回主色(应 transparent=1): {main_t}")
    flag = sync._read_transparent_flag()
    print(f"  透明标志直读: {flag}")

    # 4) 恢复非透明
    _step("4. 清除透明 + 恢复主色")
    ok = sync.set_color(255, 0, 0, color_index=0, transparent=False)
    print(f"  写入返回: {ok}")
    main_r = sync.get_color()
    print(f"  读回主色(应 transparent=0): {main_r}")
    sub_r = sync.get_sub_color()
    print(f"  读回副色: {sub_r}")

    print("\n================= 判断 =================")
    print("对照 CSP 界面检查:")
    print("  1. CSP 主色色块/画笔应变红（步骤1后）")
    print("  2. CSP 副色色块应变蓝（步骤2后）")
    print("  3. CSP 主色应变透明（步骤3后）")
    print("  4. 主色恢复红色、非透明（步骤4后）")
    print("如果某一步 CSP 界面没变化但脚本读回正常 → 写入偏移问题")
    print("如果脚本读回不对 → target 漂移或读回逻辑问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
