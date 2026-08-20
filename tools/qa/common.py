"""公共工具：路径、状态存取、控制台交互。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QA_DIR = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "test-reports"
RUNS_DIR = REPORTS_DIR / "runs"
SHOTS_DIR = REPORTS_DIR / "shots"

RESULT_ICON = {"pass": "[√]", "fail": "[×]", "skip": "[·]", "warn": "[!]"}


def require_windows() -> None:
    if sys.platform != "win32":
        raise SystemExit(
            "[×] 测试向导必须在 Windows 的 Python 下运行。\n"
            f"    当前解释器: {sys.executable}\n"
            "    请改用 Windows 的 python.exe（例如 "
            r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe）"
        )


def load_checklist() -> dict:
    return json.loads((QA_DIR / "checklist.json").read_text(encoding="utf-8"))


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_run() -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    state = {
        "run_id": run_id,
        "started": now_ts(),
        "release": False,
        "areas": [],
        "env": {},
        "auto": {},
        "guided": {},
        "manual": {},
    }
    save_run(state)
    return state


def state_path(run_id: str) -> Path:
    return RUNS_DIR / f"run-{run_id}.json"


def save_run(state: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    state_path(state["run_id"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_run(run_id: str | None = None) -> dict:
    if run_id:
        p = state_path(run_id)
    else:
        runs = sorted(RUNS_DIR.glob("run-*.json")) if RUNS_DIR.exists() else []
        if not runs:
            raise SystemExit("[×] 没有测试记录，先跑一次 env 或 manual 建立记录。")
        p = runs[-1]
    if not p.exists():
        raise SystemExit(f"[×] 找不到测试记录 {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def answer(prompt: str) -> tuple[str, str]:
    """交互问答。返回 (结果, 备注)；结果 ∈ y/n/s/q。"""
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            return "q", ""
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        a = parts[0].lower()
        note = parts[1].strip() if len(parts) > 1 else ""
        if a in ("y", "n", "s", "q"):
            return a, note
        print("    输入 y=通过 / n=失败 / s=跳过 / q=保存退出（可带备注，如: y 色轮很顺）")


def shot_dir(state: dict) -> Path:
    d = SHOTS_DIR / state["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def result_name(r: str) -> str:
    return {"pass": "通过", "fail": "失败", "skip": "跳过", "warn": "提醒"}.get(r, r)
