#!/usr/bin/env python3
"""构建前后的一致性校验：把"人眼核对"换成可复现的断言。

用法::

    python tools/release/verify_bundle.py                # 全量校验（输入 + 版本 + 产物）
    python tools/release/verify_bundle.py --inputs-only  # 构建前门禁（不要求 dist 存在）

校验内容：

1. 两个 spec 的 ``_add_if_exists`` 声明集合一致，且每个声明文件在磁盘上真实存在
   （``_add_if_exists`` 找不到会静默跳过 —— ``mag_overlay/build/mag_filter.exe``
   就是这种"构建日志干净、发行包缺东西"的典型）。
2. ``APP_VERSION`` / ``file_version_info.txt`` / ``release_notes.md`` 三者一致。
3. 两个 EXE 的 Windows FileVersion / ProductVersion 都是 ``X.Y.Z.0``。
4. 每个声明资源都真的进了 onedir 的 ``_internal`` 与 onefile 的内嵌归档。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_FILES = ("Colorink.spec", "Colorink Onefile.spec")
ONEDIR_DIR = PROJECT_ROOT / "dist" / "Onedir" / "Colorink"
ONEFILE_EXE = PROJECT_ROOT / "dist" / "Onefile" / "Colorink.exe"

_DECL_RE = re.compile(r"_add_if_exists\('([^']+)'")

# 让 `import core.updater` 在任何入口下都可用。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_failures: list[str] = []


def _ok(message: str) -> None:
    print(f"  [OK]   {message}")


def _fail(message: str) -> None:
    print(f"  [FAIL] {message}")
    _failures.append(message)


def app_version() -> str:
    from core.updater import APP_VERSION  # noqa: PLC0415 - 复用仓库真源

    return APP_VERSION


def declared_resources() -> list[str]:
    """两个 spec 声明的资源集合（已校验一致），返回排序后的列表。"""
    sets: list[set[str]] = []
    for spec_name in SPEC_FILES:
        spec_path = PROJECT_ROOT / spec_name
        if not spec_path.is_file():
            _fail(f"缺少 spec 文件：{spec_name}")
            return []
        sets.append(set(_DECL_RE.findall(spec_path.read_text(encoding="utf-8"))))
    if sets[0] != sets[1]:
        _fail(
            "两个 spec 的 _add_if_exists 集合不一致："
            f" 仅 {SPEC_FILES[0]} 有 {sorted(sets[0] - sets[1])}；"
            f" 仅 {SPEC_FILES[1]} 有 {sorted(sets[1] - sets[0])}"
        )
    return sorted(sets[0])


def check_inputs(resources: list[str]) -> None:
    print("\n[1/4] 构建输入（磁盘）")
    missing = []
    for rel in resources:
        if (PROJECT_ROOT / rel).is_file():
            _ok(rel)
        else:
            missing.append(rel)
    if missing:
        for rel in missing:
            hint = ""
            if rel.startswith("mag_overlay/"):
                hint = "（先跑 mag_overlay\\build.bat 重新编译）"
            _fail(f"声明了但磁盘上不存在：{rel}{hint}")
    else:
        _ok(f"{len(resources)} 个声明资源全部存在")


def check_version_consistency() -> str:
    print("\n[2/4] 版本一致性")
    version = app_version()
    _ok(f"core/updater.py APP_VERSION = {version}")

    fv_text = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(x) for x in version.split("."))
    wanted = {
        f"filevers=({major}, {minor}, {patch}, 0)": False,
        f"prodvers=({major}, {minor}, {patch}, 0)": False,
        f"StringStruct('FileVersion', '{version}.0')": False,
        f"StringStruct('ProductVersion', '{version}.0')": False,
    }
    for needle in wanted:
        if needle in fv_text:
            wanted[needle] = True
        else:
            _fail(f"file_version_info.txt 缺少 {needle!r}")
    if all(wanted.values()):
        _ok(f"file_version_info.txt 四处版本值 = {version}.0")

    notes = (PROJECT_ROOT / "release_notes.md").read_text(encoding="utf-8")
    if notes.startswith(f"## v{version}\n"):
        _ok(f"release_notes.md 顶部为 ## v{version}")
    else:
        _fail(f"release_notes.md 顶部不是 '## v{version}'（发布说明还没写？）")
    return version


def _exe_versions(exe: Path) -> tuple[str, str]:
    """读 Windows 资源里的 FileVersion / ProductVersion。"""
    import win32api  # noqa: PLC0415 - pywin32 是运行时依赖，缺失时给出明确提示

    info = win32api.GetFileVersionInfo(str(exe), "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    file_version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    ms, ls = info["ProductVersionMS"], info["ProductVersionLS"]
    product_version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    return file_version, product_version


def check_exe_versions(version: str) -> None:
    print("\n[3/4] EXE 版本资源")
    want = f"{version}.0"
    for exe in (ONEDIR_DIR / "Colorink.exe", ONEFILE_EXE):
        if not exe.is_file():
            _fail(f"缺少 {exe.relative_to(PROJECT_ROOT)}（还没构建？）")
            continue
        try:
            file_version, product_version = _exe_versions(exe)
        except ImportError:
            _fail("读 EXE 版本需要 pywin32（pip install pywin32）")
            return
        label = str(exe.relative_to(PROJECT_ROOT))
        if file_version == want and product_version == want:
            _ok(f"{label} — FileVersion=ProductVersion={want}")
        else:
            _fail(
                f"{label} — FileVersion={file_version} ProductVersion={product_version}，"
                f"应为 {want}"
            )


def check_onedir(resources: list[str]) -> None:
    print("\n[4/4] 产物资源")
    if not ONEDIR_DIR.is_dir():
        _fail(f"缺少 {ONEDIR_DIR.relative_to(PROJECT_ROOT)}（还没构建？）")
        return
    for extra in ("Colorink.exe", "LICENSE"):
        if (ONEDIR_DIR / extra).is_file():
            _ok(f"dist/Onedir/Colorink/{extra}")
        else:
            _fail(f"dist/Onedir/Colorink/{extra} 缺失")

    internal = ONEDIR_DIR / "_internal"
    missing = [rel for rel in resources if not (internal / rel).is_file()]
    if missing:
        for rel in missing:
            _fail(f"onedir 产物缺少 _internal/{rel}")
    else:
        _ok(f"onedir _internal 含全部 {len(resources)} 个声明资源")
    total = sum(p.stat().st_size for p in ONEDIR_DIR.rglob("*") if p.is_file())
    print(f"         onedir 体积 {total / (1024 * 1024):.1f} MB")


def check_onefile(resources: list[str]) -> None:
    if not ONEFILE_EXE.is_file():
        _fail(f"缺少 {ONEFILE_EXE.relative_to(PROJECT_ROOT)}（还没构建？）")
        return
    from PyInstaller.archive.readers import CArchiveReader  # noqa: PLC0415

    reader = CArchiveReader(str(ONEFILE_EXE))
    embedded = {name.replace("\\", "/") for name in reader.toc}
    missing = [rel for rel in resources if rel not in embedded]
    if missing:
        for rel in missing:
            _fail(f"onefile 内嵌归档缺少 {rel}")
    else:
        _ok(f"onefile 内嵌归档含全部 {len(resources)} 个声明资源")
    print(f"         onefile 体积 {ONEFILE_EXE.stat().st_size / (1024 * 1024):.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description="Colorink 构建产物一致性校验")
    parser.add_argument(
        "--inputs-only",
        action="store_true",
        help="只校验构建输入与版本号（构建前门禁），不要求 dist 存在",
    )
    args = parser.parse_args()

    resources = declared_resources()
    check_inputs(resources)
    version = check_version_consistency()

    if args.inputs_only:
        print("\n[跳过] 产物校验（--inputs-only）")
    else:
        check_exe_versions(version)
        check_onedir(resources)
        check_onefile(resources)

    print("\n" + "=" * 60)
    if _failures:
        print(f"  校验失败：{len(_failures)} 项")
        for item in _failures:
            print(f"    - {item}")
        return 1
    print("  校验通过：构建输入、版本号、产物资源全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
