"""env：环境自检（换机器 / 第一次跑时确认一次）。"""
from __future__ import annotations

import hashlib
import subprocess
import sys

from . import common
from .common import PROJECT_ROOT


def run_env(state: dict, args) -> bool:
    common.require_windows()
    results: dict = {}

    # 1. 平台
    results["platform"] = {
        "result": "pass",
        "note": f"Windows Python {sys.version.split()[0]}",
        "detail": sys.executable,
    }

    # 2. 依赖
    deps = [
        ("PyQt6", "PyQt6"),
        ("pytest", "pytest"),
        ("mss", "mss"),
        ("pywin32", "win32api"),
        ("numpy", "numpy"),
        ("dxcam", "dxcam"),
        ("Pillow", "PIL"),
    ]
    missing: list[str] = []
    versions: list[str] = []
    for label, mod in deps:
        try:
            m = __import__(mod)
            versions.append(f"{label} {getattr(m, '__version__', '?')}")
        except Exception:
            missing.append(label)
    if missing:
        results["deps"] = {
            "result": "fail",
            "note": f"缺依赖: {', '.join(missing)}",
            "detail": "pip install -r requirements.txt -r requirements-dev.txt",
        }
    else:
        results["deps"] = {"result": "pass", "note": "依赖齐", "detail": "; ".join(versions)}

    # 3. git 工作区（提醒级，不拦）
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        dirty = [l for l in out.splitlines() if l.strip()]
    except Exception as e:
        dirty = [f"(git 不可用: {e})"]
    if dirty:
        results["git_clean"] = {
            "result": "warn",
            "note": f"{len(dirty)} 个未提交变更",
            "detail": "\n".join(dirty[:15]),
        }
    else:
        results["git_clean"] = {"result": "pass", "note": "工作区干净"}

    # 4. stderr.log 基线快照（供 auto 的 stderr_delta 比对）
    log = PROJECT_ROOT / "stderr.log"
    snap = {
        "path": str(log),
        "exists": log.exists(),
        "size": log.stat().st_size if log.exists() else 0,
        "sha256": hashlib.sha256(log.read_bytes()).hexdigest() if log.exists() else "",
    }
    results["stderr_snapshot"] = {
        "result": "pass",
        "note": f"stderr.log 基线 {snap['size']} 字节",
    }

    state["env"] = {"stderr_baseline": snap}
    state["env_results"] = results
    common.save_run(state)

    print("\n=== env 环境自检 ===")
    for cid, r in results.items():
        print(f"  {common.RESULT_ICON[r['result']]} {cid}: {r['note']}")
        if r.get("detail"):
            for line in r["detail"].splitlines()[:8]:
                print(f"       {line}")
    ok = not any(r["result"] == "fail" for r in results.values())
    print(f"\n  结论: {'环境 OK' if ok else '先修环境问题再继续'}")
    return ok
