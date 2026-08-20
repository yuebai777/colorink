"""report：汇总报告 + 发版结论。"""
from __future__ import annotations

from . import common


def compute_verdict(state: dict) -> tuple[str, list[str]]:
    problems: list[str] = []

    for cid, r in state.get("auto", {}).items():
        if r.get("result") == "fail":
            problems.append(f"auto/{cid}: {r.get('note')}")
    for iid, r in state.get("guided", {}).items():
        if r.get("result") == "fail":
            problems.append(f"guided/{iid}: {r.get('note')}")
        if r.get("result") == "skip":
            problems.append(f"guided/{iid}: 被跳过")

    checklist = common.load_checklist()
    manual = state.get("manual", {})
    for ph in checklist["phases"]:
        for it in ph["items"]:
            if it.get("kind") != "manual":
                continue
            r = manual.get(it["id"])
            if state.get("release"):
                must = ph.get("release_only") or it.get("release_only") or it.get("critical")
                if must and (not r or r.get("result") != "pass"):
                    problems.append(f"manual/{it['id']} 未通过: {it['text']}")
            elif r and r.get("result") == "fail":
                problems.append(f"manual/{it['id']}: {it['text']}")

    if state.get("release"):
        verdict = "✅ 可以发版" if not problems else "❌ 不能发版（见未过项）"
    else:
        verdict = "✅ 通过" if not problems else "⚠️ 有失败项，修完重跑"
    return verdict, problems


def generate(state: dict) -> str:
    checklist = common.load_checklist()
    verdict, problems = compute_verdict(state)
    mode = "发版模式（严格门槛）" if state.get("release") else "日常模式"
    lines: list[str] = []
    lines += [
        f"# Colorink 测试报告 — run {state['run_id']}",
        "",
        f"- 开始时间: {state.get('started')}",
        f"- 生成时间: {common.now_ts()}",
        f"- 模式: {mode}",
        "",
        f"## 结论: {verdict}",
        "",
    ]
    if problems:
        lines.append("### 未过项")
        for p in problems:
            lines.append(f"- [×] {p}")
        lines.append("")

    # auto
    lines.append("## 自动化守卫（auto）")
    if state.get("auto"):
        lines.append("| 检查 | 结果 | 说明 |")
        lines.append("|---|---|---|")
        for cid, r in state["auto"].items():
            lines.append(
                f"| {cid} | {common.RESULT_ICON.get(r.get('result'), '?')} "
                f"{common.result_name(r.get('result', ''))} | {r.get('note', '')} |"
            )
    else:
        lines.append("_未运行_")
    lines.append("")

    # guided
    lines.append("## 半自动引导（guided）")
    if state.get("guided"):
        for iid, r in state["guided"].items():
            shots = [s for s in (r.get("shots") or []) if s]
            shot_links = " ".join(f"[图]({s.replace(chr(92), '/')})" for s in shots)
            lines.append(
                f"- {common.RESULT_ICON.get(r.get('result'), '?')} **{iid}** "
                f"{r.get('note', '')} {shot_links}"
            )
    else:
        lines.append("_未运行_")
    lines.append("")

    # manual by phase
    lines.append("## 手测（manual）")
    manual = state.get("manual", {})
    for ph in checklist["phases"]:
        if ph.get("release_only") and not state.get("release"):
            continue
        shown = [
            it
            for it in ph["items"]
            if it.get("kind") == "manual"
            and (not it.get("release_only") or state.get("release"))
        ]
        if not shown:
            continue
        lines.append(f"### 阶段 {ph['id']} {ph['title']}")
        for it in shown:
            r = manual.get(it["id"])
            if r is None:
                lines.append(f"- [ ] {it['id']} {it['text']}")
            else:
                box = {"pass": "x", "fail": "x", "skip": " "}.get(r.get("result"), " ")
                icon = common.RESULT_ICON.get(r.get("result"), "?")
                note = f" — {r['note']}" if r.get("note") else ""
                lines.append(f"- [{box}] {icon} {it['id']} {it['text']}{note}")
        lines.append("")

    lines.append("---")
    lines.append(f"_由 tools/test_wizard.py 生成；数据源 tools/qa/checklist.json_")
    return "\n".join(lines)


def run_report(state: dict, args):
    md = generate(state)
    out = common.REPORTS_DIR / f"QA-{state['run_id']}.md"
    common.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    verdict, problems = compute_verdict(state)
    print(f"\n=== report 汇总报告 ===")
    print(f"  {verdict}")
    if problems:
        print("  未过项:")
        for p in problems:
            print(f"    [×] {p}")
    print(f"  报告文件: {out}")
    return out
