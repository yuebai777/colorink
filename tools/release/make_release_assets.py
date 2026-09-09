#!/usr/bin/env python3
"""打包 GitHub Release 资产、提取发布说明、输出校验和。

用法::

    python tools/release/make_release_assets.py          # 用 APP_VERSION
    python tools/release/make_release_assets.py 1.8.7    # 指定版本

产出（全部落在已 gitignore 的 dist/ 下）::

    dist/Onedir/Colorink-Onedir.zip     内含 Colorink/ 目录（onedir 更新包）
    dist/Onefile/Colorink.exe           onefile 更新包（直接上传）
    dist/release_notes_vX.Y.Z.md        从 release_notes.md 提取的发布说明

资产名是硬契约，**不能改名**：老版本的自动更新按名字挑资产
（``core/updater.py::_is_installer_zip_name`` / ``find_installer_asset``）。
脚本最后会用这两个函数真跑一遍选择逻辑，确认新发布能被老版本正确识别。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ONEDIR_DIR = PROJECT_ROOT / "dist" / "Onedir" / "Colorink"
ONEFILE_EXE = PROJECT_ROOT / "dist" / "Onefile" / "Colorink.exe"
ONEDIR_ZIP = PROJECT_ROOT / "dist" / "Onedir" / "Colorink-Onedir.zip"
NOTES_REL = "release_notes.md"

# 让 `import core.updater` 在任何入口下都可用（脚本可能从别处被调用）。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def app_version() -> str:
    from core.updater import APP_VERSION  # noqa: PLC0415

    return APP_VERSION


def extract_release_notes(version: str) -> Path:
    """从 release_notes.md 取出 ``## vX.Y.Z`` 段（到下一个 --- 为止）。"""
    text = (PROJECT_ROOT / NOTES_REL).read_text(encoding="utf-8")
    heading = f"## v{version}\n"
    if not text.startswith(heading):
        raise SystemExit(
            f"{NOTES_REL} 顶部不是 {heading.strip()!r} —— 先写发布说明再打包。"
        )
    body = text[len(heading):]
    end = body.find("\n---\n")
    if end == -1:
        end = body.find("\n## v")
    if end != -1:
        body = body[:end]
    body = body.strip() + "\n"
    out = PROJECT_ROOT / "dist" / f"release_notes_v{version}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8", newline="")
    print(f"  [OK]   发布说明 -> {out.relative_to(PROJECT_ROOT)}（{len(body)} 字符）")
    return out


def build_onedir_zip() -> Path:
    if not ONEDIR_DIR.is_dir():
        raise SystemExit(
            f"缺少 {ONEDIR_DIR.relative_to(PROJECT_ROOT)} —— 先跑 build_pyqt.py"
        )
    files = sorted(p for p in ONEDIR_DIR.rglob("*") if p.is_file())
    if not files:
        raise SystemExit(f"{ONEDIR_DIR.relative_to(PROJECT_ROOT)} 是空的")
    with zipfile.ZipFile(
        ONEDIR_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        for path in files:
            arcname = "Colorink/" + path.relative_to(ONEDIR_DIR).as_posix()
            zf.write(path, arcname)
    print(
        f"  [OK]   onedir 压缩包 -> {ONEDIR_ZIP.relative_to(PROJECT_ROOT)}"
        f"（{len(files)} 个文件，{ONEDIR_ZIP.stat().st_size / (1024 * 1024):.1f} MB）"
    )
    return ONEDIR_ZIP


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_updater_picks(version: str) -> None:
    """用真正的选择逻辑确认老版本能挑中这两个资产。"""
    from core.updater import _is_installer_zip_name, find_installer_asset  # noqa: PLC0415

    if not ONEFILE_EXE.is_file():
        raise SystemExit(
            f"缺少 {ONEFILE_EXE.relative_to(PROJECT_ROOT)} —— 先跑 build_pyqt.py"
        )
    assets = [
        {"name": ONEFILE_EXE.name, "url": "u", "size": ONEFILE_EXE.stat().st_size},
        {"name": ONEDIR_ZIP.name, "url": "u", "size": ONEDIR_ZIP.stat().st_size},
        # GitHub 自动生成的源码包，绝不能被选中。
        {"name": f"colorink-{version}.zip", "url": "u", "size": 1024},
    ]
    if not _is_installer_zip_name(ONEDIR_ZIP.name):
        raise SystemExit(f"{ONEDIR_ZIP.name} 不满足 _is_installer_zip_name（老版本会忽略它）")

    picked_onefile = find_installer_asset(assets, flavor="onefile")
    picked_onedir = find_installer_asset(assets, flavor="onedir")
    if picked_onefile is None or picked_onefile["name"] != ONEFILE_EXE.name:
        raise SystemExit(f"onefile 资产选择错误：{picked_onefile}")
    if picked_onedir is None or picked_onedir["name"] != ONEDIR_ZIP.name:
        raise SystemExit(f"onedir 资产选择错误：{picked_onedir}")
    print("  [OK]   更新选择逻辑：onefile -> Colorink.exe，onedir -> Colorink-Onedir.zip")


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 Release 资产")
    parser.add_argument("version", nargs="?", help="目标版本 X.Y.Z")
    args = parser.parse_args()

    version = (args.version or app_version()).lstrip("vV")
    print(f"版本 v{version}\n")
    extract_release_notes(version)
    build_onedir_zip()
    verify_updater_picks(version)

    print("\n资产校验和（贴进发布说明或留档）：")
    for path in (ONEFILE_EXE, ONEDIR_ZIP):
        print(
            f"  {path.name:<24} {path.stat().st_size / (1024 * 1024):6.1f} MB"
            f"  sha256={sha256(path)}"
        )
    print(
        "\n下一步：\n"
        f"  gh release create v{version} --draft --verify-tag --title \"v{version}\" \\\n"
        f"    --notes-file dist\\release_notes_v{version}.md \\\n"
        f"    \"dist\\Onefile\\Colorink.exe\" \"dist\\Onedir\\Colorink-Onedir.zip\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
