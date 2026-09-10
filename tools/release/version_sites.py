"""版本号落点清单 —— 单一真源是 ``core/updater.py`` 的 ``APP_VERSION``。

被两处共用，避免"脚本改的位置"和"测试检查的位置"各自漂移：

- ``tools/release/bump_version.py``（写入）
- ``tests/test_release_contract.py``（断言）

每个条目的正则必须有三个组，**group(2) 恰好是版本号本身**；
``expected`` 是期望的匹配次数（防止正则意外扩大/缩小命中范围）。
"""

from __future__ import annotations

#: (相对路径, 三组正则, 说明, 期望匹配次数)
VERSION_SITES: list[tuple[str, str, str, int]] = [
    (
        "core/updater.py",
        r'^(APP_VERSION = ")(\d+\.\d+\.\d+)(")$',
        "APP_VERSION（唯一真源）",
        1,
    ),
    (
        "README.md",
        r"(当前版本：\*\*v)(\d+\.\d+\.\d+)(\*\*)",
        "README 当前版本",
        1,
    ),
    (
        "TESTING.md",
        r"(基线（\d{4}-\d{2}，v)(\d+\.\d+\.\d+)(）)",
        "TESTING 基线版本",
        1,
    ),
    (
        "docs/index.md",
        r"(下载最新版 \(v)(\d+\.\d+\.\d+)(\))",
        "docs 首页下载按钮",
        1,
    ),
    (
        "docs/.vitepress/config.mjs",
        r"('v)(\d+\.\d+\.\d+)( 下载')",
        "docs 导航栏下载项",
        1,
    ),
    (
        "docs/.vitepress/config.mjs",
        r"('Download v)(\d+\.\d+\.\d+)(')",
        "docs 导航栏下载项（英文）",
        1,
    ),
    (
        "package.json",
        r'("version": ")(\d+\.\d+\.\d+)(")',
        "package.json",
        1,
    ),
    (
        "package-lock.json",
        r'^(  "version": ")(\d+\.\d+\.\d+)(",)$',
        "package-lock.json（根条目）",
        1,
    ),
    (
        "package-lock.json",
        r'("name": "colorink-docs",\n      "version": ")(\d+\.\d+\.\d+)(",)$',
        "package-lock.json（packages 空键）",
        1,
    ),
]

#: ``file_version_info.txt`` 的四处（PyInstaller 只认这四个值）。
#: (正则, 替换模板)，模板可用的占位符：``{v}`` / ``{major}`` / ``{minor}`` / ``{patch}``。
FILE_VERSION_SITES: list[tuple[str, str]] = [
    (r"filevers=\(\d+, \d+, \d+, 0\)", "filevers=({major}, {minor}, {patch}, 0)"),
    (r"prodvers=\(\d+, \d+, \d+, 0\)", "prodvers=({major}, {minor}, {patch}, 0)"),
    (r"StringStruct\('FileVersion', '[\d.]+'\)", "StringStruct('FileVersion', '{v}.0')"),
    (
        r"StringStruct\('ProductVersion', '[\d.]+'\)",
        "StringStruct('ProductVersion', '{v}.0')",
    ),
]
