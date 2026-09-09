#!/usr/bin/env python3
"""单一真源版本号同步工具。

``core/updater.py`` 里的 ``APP_VERSION`` 是唯一真源；这个脚本把它同步到所有
写死版本号的地方，发布时只需要手改一处。

用法::

    python tools/release/bump_version.py --check        # 以 APP_VERSION 为准，检查漂移
    python tools/release/bump_version.py 1.8.7          # 写入所有位置
    python tools/release/bump_version.py 1.8.7 --check  # 只检查、不写入

退出码：0 = 一致 / 已同步；1 = 存在漂移或参数错误。

注意：``release_notes.md`` 不在这里改（需要人写发布说明），但 ``--check`` 会
检查 ``## vX.Y.Z`` 段落是否存在，``tests/test_release_contract.py`` 也会强制。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from version_sites import FILE_VERSION_SITES, VERSION_SITES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATER_REL = "core/updater.py"
NOTES_REL = "release_notes.md"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _read(rel: str) -> str:
    """读文本，保留原始换行（仓库策略是 LF，不能顺手改成 CRLF）。"""
    with open(PROJECT_ROOT / rel, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(rel: str, text: str) -> None:
    with open(PROJECT_ROOT / rel, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def source_of_truth() -> str:
    """从 core/updater.py 读出当前 APP_VERSION。"""
    match = re.search(r'^APP_VERSION = "(\d+\.\d+\.\d+)"$', _read(UPDATER_REL), re.M)
    if not match:
        raise SystemExit(f"无法从 {UPDATER_REL} 解析 APP_VERSION")
    return match.group(1)


def _as_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in version.split("."))
    return major, minor, patch


def check(target: str) -> int:
    """检查所有位置是否等于 target，返回漂移数量。"""
    drift = 0
    print(f"真源 APP_VERSION = {source_of_truth()}；检查目标 = {target}\n")
    for rel, pattern, label, expected in VERSION_SITES:
        values = [m.group(2) for m in re.finditer(pattern, _read(rel), re.M)]
        if not values:
            print(f"  [缺失] {label:<24} {rel} — 没匹配到版本号")
            drift += 1
            continue
        bad = sorted({v for v in values if v != target})
        count_note = ""
        if expected is not None and len(values) != expected:
            count_note = f"（期望 {expected} 处，实际 {len(values)} 处）"
            drift += 1
        if bad:
            print(f"  [漂移] {label:<24} {rel} — {', '.join(bad)} ≠ {target}{count_note}")
            drift += 1
        else:
            print(f"  [OK]   {label:<24} {rel}{count_note}")

    fv_text = _read("file_version_info.txt")
    major, minor, patch = _as_tuple(target)
    for pattern, template in FILE_VERSION_SITES:
        want = template.format(v=target, major=major, minor=minor, patch=patch)
        if not re.search(pattern, fv_text):
            print(f"  [缺失] file_version_info.txt — 没匹配到 {pattern}")
            drift += 1
        elif want not in fv_text:
            print(f"  [漂移] file_version_info.txt — 缺少 {want!r}")
            drift += 1
    if drift == 0:
        print("  [OK]   file_version_info.txt     四处版本值一致")

    notes = _read(NOTES_REL)
    if notes.startswith(f"## v{target}\n"):
        print(f"  [OK]   release_notes.md          顶部为 ## v{target}")
    else:
        print(
            f"  [人工] release_notes.md          顶部不是 '## v{target}'"
            " —— 需要手写发布说明（pytest 会强制）"
        )
    return drift


def apply(target: str, force: bool) -> int:
    """把所有位置写成 target，返回被修改的文件数。"""
    current = source_of_truth()
    if _as_tuple(target) < _as_tuple(current) and not force:
        print(
            f"拒绝降级：{target} < 当前 {current}（真要降级请加 --force）",
            file=sys.stderr,
        )
        return -1

    major, minor, patch = _as_tuple(target)
    changed_files: set[str] = set()
    for rel, pattern, label, expected in VERSION_SITES:
        text = _read(rel)
        values = [m.group(2) for m in re.finditer(pattern, text, re.M)]
        if not values:
            print(f"  [跳过] {label:<24} {rel} — 没匹配到版本号")
            continue
        if expected is not None and len(values) != expected:
            print(
                f"  [警告] {label:<24} {rel} — 期望 {expected} 处，实际 {len(values)} 处"
            )
        new_text = re.sub(
            pattern,
            lambda m: f"{m.group(1)}{target}{m.group(3)}",
            text,
            flags=re.M,
        )
        if new_text == text:
            print(f"  [不变] {label:<24} {rel}（已是 {target}）")
            continue
        _write(rel, new_text)
        changed_files.add(rel)
        print(f"  [写入] {label:<24} {rel} — {', '.join(sorted(set(values)))} -> {target}")

    fv_text = _read("file_version_info.txt")
    new_fv = fv_text
    for pattern, template in FILE_VERSION_SITES:
        want = template.format(v=target, major=major, minor=minor, patch=patch)
        new_fv = re.sub(pattern, want, new_fv)
    if new_fv != fv_text:
        _write("file_version_info.txt", new_fv)
        changed_files.add("file_version_info.txt")
        print(f"  [写入] file_version_info.txt       四处 -> {target}.0")
    else:
        print(f"  [不变] file_version_info.txt       已是 {target}.0")

    print(f"\n共修改 {len(changed_files)} 个文件。")
    notes = _read(NOTES_REL)
    if not notes.startswith(f"## v{target}\n"):
        print(
            f"\n下一步（必须人工）：在 {NOTES_REL} 顶部插入 '## v{target}' 段落，"
            "按 新增 / 修复 / 变更 三段写，内容从 `git log` 与 diff 提炼。"
        )
    print(
        "然后跑：python -m pytest -q tests/test_release_contract.py"
    )
    return len(changed_files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 APP_VERSION 同步到所有写死版本号的位置。",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="目标版本 X.Y.Z；省略则用 core/updater.py 里的 APP_VERSION",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查漂移，不写文件",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许版本号降级（默认拒绝）",
    )
    args = parser.parse_args()

    target = args.version or source_of_truth()
    if not VERSION_RE.match(target):
        print(f"版本号格式必须是 X.Y.Z，收到 {target!r}", file=sys.stderr)
        return 1

    if args.check:
        return 1 if check(target) else 0

    result = apply(target, args.force)
    return 1 if result < 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
