#!/usr/bin/env python3
"""Test whether +0x08 low byte is the active-slot index (0=main, 1=sub).

The sub-color edit experiment showed +0x08's first byte flipping 00→01
when the SUB color changed — hinting it tracks the active slot. If so,
Colorink can switch CSP's brush to the sub slot by writing it, and then
the +0x9C sub mirror IS the brush source.

Experiment (CSP open, main=red, sub=blue):
    python tools/probe_active_flag.py
1. Writes sub mirror (+0x9C..+0xA7) to RED (float 1.0, 0.0, 0.0)
2. Writes +0x08 = 0x00000001 (try activating the sub slot)
3. Asks you to paint one stroke
4. Restores everything
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402


def _ask(question: str) -> bool:
    while True:
        ans = input(f">>> {question} (y/n): ").strip().lower()
        if ans in ("y", "n"):
            return ans == "y"
        print("    请输入 y 或 n")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    assert sync.pm is not None and sync.target is not None
    t = sync.target
    print(f"已连接: {sync.current_version} @ target=0x{t:X}")
    print(f"当前 +0x08 区域: {sync.pm.read_bytes(t + 0x08, 4).hex(' ')}")

    # 备份
    backup_flag = sync.pm.read_bytes(t + 0x08, 4)
    backup_sub = sync.pm.read_bytes(t + 0x9C, 12)

    # 1) 写副色镜像为红色 float
    red = struct.pack("<f", 1.0) + struct.pack("<f", 0.0) + struct.pack("<f", 0.0)
    sync.pm.write_bytes(t + 0x9C, red, 12)
    print("\n已写副色镜像(+0x9C) = 红色 float (1.0, 0, 0)")

    # 2) 写 +0x08 = 1（尝试激活副色）
    sync.pm.write_bytes(t + 0x08, struct.pack("<I", 1), 4)
    print(f"已写 +0x08 = 0x00000001（尝试激活副色槽）")
    print(f"现在 +0x08 区域: {sync.pm.read_bytes(t + 0x08, 4).hex(' ')}")

    ok = _ask("现在在 CSP 里画一笔（不用切任何槽），画出来是 红色 吗？")
    if ok:
        print("\n========== 成功 ==========")
        print("激活槽 = +0x08 低字节 (0=主色, 1=副色)；副色画笔源 = +0x9C float")
        print("实现：点 bg 圆 → 写副色 float + 置 +0x08=1；点 fg 圆 → 写主色 + 置 +0x08=0")
    else:
        # 备用：可能 +0x08 全 FF 才是透明、低字节确实是槽索引但写 1 不够？
        print("\n未命中。尝试 +0x08 = 0x00000001 且同时不写镜像（只用 CSP 原副色验证激活）：")
        sync.pm.write_bytes(t + 0x9C, backup_sub, 12)  # 恢复镜像
        ok2 = _ask("现在画一笔，画出来是 CSP 原本的 副色（蓝） 吗？")
        if ok2:
            print("\n结论：+0x08 低字节确实切换激活槽（画笔已切到副色并读出原副色）")
            print("但副色画笔源不在 +0x9C —— 需要继续找权威副本")
        else:
            print("\n结论：+0x08 低字节不是激活槽开关，副色激活状态在别处")

    # 恢复
    sync.pm.write_bytes(t + 0x08, backup_flag, 4)
    sync.pm.write_bytes(t + 0x9C, backup_sub, 12)
    print("\n已恢复所有写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
