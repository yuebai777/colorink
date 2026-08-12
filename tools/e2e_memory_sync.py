#!/usr/bin/env python3
"""End-to-end self-test: memory backend writes -> companion read-back
verifies CSP actually accepted every operation (main/sub color, active
slot, transparency). Runs automatically.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.csp_brush_link import CSPSync  # noqa: E402
from core.csp_companion_sync import CSPCompanionSync  # noqa: E402


def main() -> int:
    sync = CSPSync()
    if not sync.connect():
        print("无法连接 CSP。")
        return 1
    comp = CSPCompanionSync()
    if not comp._connected and not comp.connect():
        print("Companion 不可用。")
        return 1

    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    # 1) bg 选色（内存写副色 + 激活副色）
    print("── 1. 内存写 bg=蓝 (color_index=1) ──")
    ok = sync.set_color(0, 0, 255, color_index=1)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    sub = sync.get_sub_color()
    check(f"写入返回 {ok}", ok)
    check(f"companion 读回 index=1 (副色激活), 实际={rb.get('index') if rb else None}",
          rb is not None and rb.get("index") == 1)
    check(f"内存直读副色≈蓝, 实际={sub}",
          sub is not None and sub["b"] > 200 and sub["r"] < 60 and sub["g"] < 60)

    # 2) fg 选色（内存写主色 + 激活主色）
    print("── 2. 内存写 fg=绿 (color_index=0) ──")
    ok = sync.set_color(0, 255, 0, color_index=0)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    main = sync.get_color()
    check(f"写入返回 {ok}", ok)
    check(f"companion 读回 index=0 (主色激活), 实际={rb.get('index') if rb else None}",
          rb is not None and rb.get("index") == 0)
    check(f"内存直读主色≈绿, 实际={main}",
          main is not None and main["g"] > 200 and main["r"] < 60 and main["b"] < 60)

    # 3) bg 透明（内存激活副色 + 透明标志）
    print("── 3. 内存写 bg=透明 (color_index=1, transparent=True) ──")
    ok = sync.set_color(0, 0, 255, color_index=1, transparent=True)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    check(f"写入返回 {ok}", ok)
    check(f"companion 读回 transparent=True, 实际={rb.get('transparent') if rb else None}",
          rb is not None and rb.get("transparent") is True)

    # 4) bg 清除透明（恢复副色）
    print("── 4. 内存清 bg 透明 (color_index=1) ──")
    ok = sync.set_color(0, 0, 255, color_index=1)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    check(f"写入返回 {ok}", ok)
    check(f"companion 读回 transparent=False, 实际={rb.get('transparent') if rb else None}",
          rb is not None and rb.get("transparent") is False)

    # 5) fg 透明
    print("── 5. 内存写 fg=透明 (color_index=0, transparent=True) ──")
    ok = sync.set_color(0, 255, 0, color_index=0, transparent=True)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    check(f"写入返回 {ok}", ok)
    check(f"companion 读回 transparent=True, 实际={rb.get('transparent') if rb else None}",
          rb is not None and rb.get("transparent") is True)

    # 6) 恢复：fg=红 激活主色
    print("── 6. 恢复 fg=红 (color_index=0) ──")
    sync.set_color(255, 0, 0, color_index=0)
    time.sleep(0.5)
    rb = comp.get_color_hsv()
    check(f"读回 index=0 transparent=False, 实际=index:{rb.get('index') if rb else None} t:{rb.get('transparent') if rb else None}",
          rb is not None and rb.get("index") == 0 and rb.get("transparent") is False)

    print()
    if failures:
        print(f"失败 {len(failures)} 项:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部通过 —— 内存模式 fg/bg 颜色+激活+透明 与 CSP 完全同步！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
