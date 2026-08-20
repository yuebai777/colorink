"""manual：手测打勾，断点续测。

逐条显示 checklist.json 里的 manual 条目，用户回答 y/n/s；
每条回答即写盘，q 随时保存退出，下次运行自动续测。
"""
from __future__ import annotations

from . import common


def iter_manual_items(checklist: dict, state: dict):
    for ph in checklist["phases"]:
        if ph.get("release_only") and not state.get("release"):
            continue
        for it in ph["items"]:
            if it.get("release_only") and not state.get("release"):
                continue
            if it.get("kind") == "manual":
                yield ph, it


def _summary(checklist: dict, state: dict) -> None:
    done = state.get("manual", {})
    total = 0
    fails: list[str] = []
    for ph, it in iter_manual_items(checklist, state):
        total += 1
        r = done.get(it["id"], {}).get("result")
        if r == "fail":
            fails.append(f"{it['id']} {it['text']}")
    passed = sum(1 for r in done.values() if r.get("result") == "pass")
    print(f"\n  进度: {passed} 通过 / {len(fails)} 失败 / "
          f"{len(done) - passed - len(fails)} 跳过，共 {total} 项"
          f"（已答 {len(done)}）")
    if fails:
        print("  失败项:")
        for f in fails:
            print(f"    [×] {f}")


def run_manual(state: dict, args) -> None:
    common.require_windows()
    checklist = common.load_checklist()
    done = state.setdefault("manual", {})
    pending = [(ph, it) for ph, it in iter_manual_items(checklist, state) if it["id"] not in done]

    print("\n=== manual 手测打勾 ===")
    print(f"  待测 {len(pending)} 项；应用请保持运行。")
    print("  y=通过 n=失败 s=跳过 q=保存退出（可带备注，如: y 色轮很顺）")

    cur_phase = None
    for ph, it in pending:
        if ph["id"] != cur_phase:
            cur_phase = ph["id"]
            tag = "（发版）" if ph.get("release_only") else ""
            print(f"\n■ 阶段 {ph['id']} {ph['title']}{tag}")
        tags = []
        if it.get("critical"):
            tags.append("关键")
        if it.get("release_only"):
            tags.append("发版")
        suffix = f" 【{'/'.join(tags)}】" if tags else ""
        print(f"  [{it['id']}] {it['text']}{suffix}")
        a, note = common.answer("    > ")
        if a == "q":
            print("  已保存，下次运行自动从这里继续。")
            break
        done[it["id"]] = {
            "result": {"y": "pass", "n": "fail", "s": "skip"}[a],
            "note": note,
            "ts": common.now_ts(),
        }
        common.save_run(state)

    _summary(checklist, state)
