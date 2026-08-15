#!/usr/bin/env python3
"""Compile the native grayscale runtime into native_grayscale/runtime/.

The runtime (``grayscale_overlay.pyc``) is a Python-version-specific bytecode
artifact checked into git so clean checkouts and packaged builds can ship it.
Whenever the source ``ui/grayscale_overlay.py`` changes — or you run a
different Python version (the pyc's magic number must match the runtime
environment, e.g. 3.14 for the checked-in copy) — recompile with:

    python native_grayscale/build_runtime.py

Uses UNCHECKED_HASH invalidation so byte-identical sources produce
byte-identical pyc files (no timestamp churn in git).
"""
import pathlib
import py_compile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ui" / "grayscale_overlay.py"
OUT_DIR = ROOT / "native_grayscale" / "runtime"
OUT = OUT_DIR / "grayscale_overlay.pyc"


def main() -> int:
    if not SOURCE.exists():
        print(
            f"源码不存在: {SOURCE}\n"
            "（仓库只跟踪编译产物；若你在别处维护源码，请把它放到 ui/ 下再编译）"
        )
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(SOURCE),
        cfile=str(OUT),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    print(f"已编译: {OUT}  (Python {sys.version.split()[0]})")
    print("提示：把该 pyc 提交进 git；目标运行环境的 Python 版本必须与此一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
