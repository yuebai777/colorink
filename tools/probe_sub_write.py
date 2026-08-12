#!/usr/bin/env python3
"""Find the SUB-color memory write channel (one interactive question).

Known layout (CSP 5.1, rgb_u32):
  main color  write channel : target + 0x20/0x24/0x28  (verified)
  main transparent flag     : target + 0x08            (verified)
  sub color  UI copy        : target + 0x9C            (seen changing
                             when the user picks the sub color in CSP)

We do not know which offset CSP accepts as the *sub color write* channel.
Candidate: the UI copy itself (+0x9C). This script writes a red sub color
there and asks you whether CSP's sub-color swatch turned red.

Usage (CSP open, no other Colorink instance running):
    python tools/probe_sub_write.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync, _clamp_byte  # noqa: E402
from core.brush_color_spaces import encode_space_values  # noqa: E402

_SUB_UI_COPY = 0x9C  # sub color UI copy (4-byte u32 encoding, like +0x3C)
_SUB_CHANNEL_1 = 0x80  # mirrored main layout: +0x60 + 0x20 (UNVERIFIED)


def _write_color_at(sync: CSPSync, offset: int, r: int, g: int, b: int) -> None:
    assert sync.pm is not None and sync.target is not None
    encoded = encode_space_values("rgb", {"r": _clamp_byte(r), "g": _clamp_byte(g), "b": _clamp_byte(b)})
    for i, raw in enumerate(encoded):
        sync._write_u32(sync.target + offset + i * 4, raw)


def _read_u32(sync: CSPSync, offset: int) -> int:
    assert sync.pm is not None and sync.target is not None
    return sync._read_u32(sync.target + offset)


def _ask(question: str) -> str:
    while True:
        ans = input(f">>> {question} (y/n): ").strip().lower()
        if ans in ("y", "n"):
            return ans
        print("    请输入 y 或 n")


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP —— 请先启动 CLIP STUDIO PAINT。")
        return 1
    print(f"已连接: {sync.current_version} @ target=0x{sync.target:X}")
    print("请把 CSP 的副色设为任意已知颜色（比如蓝），然后开始。")

    # 1) 先写副色 UI 副本 (+0x9C) 为红色
    before = _read_u32(sync, _SUB_UI_COPY)
    print(f"\n副色 UI 副本 (+0x9C) 当前值: 0x{before:08X}")
    _write_color_at(sync, _SUB_UI_COPY, 255, 0, 0)
    after = _read_u32(sync, _SUB_UI_COPY)
    print(f"写入红色后值:              0x{after:08X}")
    ans = _ask("CSP 界面上的副色色块变红了吗？")

    if ans == "y":
        print("\n结论: 副色写入通道 = target + 0x9C（与主色 +0x20 不同，是 UI 副本通道）")
        print("实现: 内存模式 bg 槽写 target+0x9C..+0x9F，透明暂不支持（CSP 模型限制）")
        return 0

    # 2) 试镜像通道 +0x80（先备份，可恢复）
    print("\n试候选通道 target+0x80（镜像主槽布局，未验证，写入前备份原值）…")
    assert sync.pm is not None and sync.target is not None
    backup = sync.pm.read_bytes(sync.target + 0x80, 0x20)
    _write_color_at(sync, 0x80, 255, 0, 0)
    after1 = _read_u32(sync, 0x80)
    ui_after1 = _read_u32(sync, _SUB_UI_COPY)
    print(f"  +0x80 写入后: 0x{after1:08X}；副色 UI 副本(+0x9C): 0x{ui_after1:08X}")
    ans2 = _ask("CSP 副色色块这次变红了吗？")
    sync.pm.write_bytes(sync.target + 0x80, backup, 0x20)  # 恢复

    if ans2 == "y":
        print("\n结论: 副色写入通道 = target + 0x80（镜像主槽布局）")
        return 0

    print("\n结论: 未找到可靠的副色写入通道。")
    print("建议: 内存模式下 bg 槽颜色同步暂不可用；请用 Companion 模式（协议原生支持 ColorIndex=1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
