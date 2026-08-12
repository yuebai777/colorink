#!/usr/bin/env python3
"""Crack the sub-color write format (CSP 5.1).

Probe 1 wrote u32-proportional 0xFFFFFFFF to target+0x9C and the sub
swatch turned BLACK — a strong hint the sub channel is float32 (0xFFFFFFFF
reads as NaN float → black). This script tries the most plausible formats,
asking once per candidate.

Run with CSP open; keep an eye on CSP's sub-color swatch:

    python tools/probe_sub_format.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402

_SUB_OFFS = 0x9C  # sub-color channel start (single u32 / float triplet)


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
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    assert sync.pm is not None and sync.target is not None
    backup = sync.pm.read_bytes(sync.target + _SUB_OFFS, 0x10)

    def write_raw(payload: bytes, offset: int = 0) -> None:
        assert sync.pm is not None and sync.target is not None
        sync.pm.write_bytes(sync.target + _SUB_OFFS + offset, payload, len(payload))

    candidates = [
        ("float32 ×3  (R=1.0, G=0.0, B=0.0)",
         struct.pack("<f", 1.0) + struct.pack("<f", 0.0) + struct.pack("<f", 0.0)),
        ("u32 RGBA  (0xFF0000FF)",
         bytes([0xFF, 0x00, 0x00, 0xFF])),
        ("u32 ARGB  (0xFFFF0000)",
         bytes([0x00, 0x00, 0xFF, 0xFF])),
    ]

    for name, payload in candidates:
        if name.startswith("float32"):
            write_raw(payload)  # 12 bytes at +0x9C
        else:
            write_raw(payload, 0)  # 4 bytes at +0x9C
        print(f"\n已写入候选: {name}")
        if _ask("CSP 副色色块现在显示 红色 了吗？"):
            print("\n========== 破解成功 ==========")
            print(f"副色通道: target+0x{_SUB_OFFS:X}  格式: {name}")
            return 0

    # 全部失败 → 恢复原值
    sync.pm.write_bytes(sync.target + _SUB_OFFS, backup, len(backup))
    print("\n三个候选都未命中，已恢复副色原值。")
    print("副色内存直写暂不可行；请使用 Companion 模式（协议原生支持副色 ColorIndex=1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
