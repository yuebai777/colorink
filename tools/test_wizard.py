#!/usr/bin/env python3
"""Colorink 测试向导 —— 一条龙测试编排器。

把「机器能测的」和「人必须测的」合并成一条流水线：
  python tools/test_wizard.py env      环境自检（换机器时跑一次）
  python tools/test_wizard.py auto     自动化守卫（无人值守；--area 定向，--skip-check 跳过）
  python tools/test_wizard.py guided   半自动引导（自动按键+截图+判定，按 y/n 复核）
  python tools/test_wizard.py manual   手测打勾（断点续测：q 保存退出，下次自动接着来）
  python tools/test_wizard.py report   汇总报告 + 发版结论
  python tools/test_wizard.py panel    本地网页面板 http://127.0.0.1:8799/
  python tools/test_wizard.py all      一条龙：env → auto → guided → manual → report

通用参数：--release 发版模式（含发版阶段 + 严格门槛）；--run ID 指定记录；
--fresh 新开记录；--list-areas 列出区域名。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa import common  # noqa: E402


def _common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--release", action="store_true", help="发版模式：含发版阶段 + 严格门槛")
    p.add_argument("--run", help="指定 run id（缺省用最近一次记录）")
    p.add_argument("--fresh", action="store_true", help="新开一条测试记录，不用旧的")
    p.add_argument("--area", action="append", help="auto: 只跑指定区域（可多次；--list-areas 看名单）")
    p.add_argument("--skip-check", action="append", help="auto: 跳过指定检查（如 --skip-check exe_smoke）")
    p.add_argument("--list-areas", action="store_true", help="列出可选区域")


def _state(args) -> dict:
    if getattr(args, "fresh", False):
        return common.new_run()
    if getattr(args, "run", None):
        return common.load_run(args.run)
    try:
        return common.load_run(None)
    except SystemExit:
        return common.new_run()


def main(argv=None) -> int:
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(
        prog="python tools/test_wizard.py",
        description="Colorink 测试向导：自动测试 + 手测打勾 + 报告结论，一条流水线。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("把「")[0],
    )
    p.add_argument("--list-areas", action="store_true", help="列出可选区域")
    sub = p.add_subparsers(dest="cmd")
    for name, help_ in [
        ("env", "环境自检"),
        ("auto", "自动化守卫（无人值守）"),
        ("guided", "半自动引导（按键注入+截图+判定）"),
        ("manual", "手测打勾（断点续测）"),
        ("report", "汇总报告 + 发版结论"),
        ("panel", "本地网页面板"),
        ("all", "一条龙：env→auto→guided→manual→report"),
    ]:
        sp = sub.add_parser(name, help=help_)
        _common_flags(sp)
        if name == "guided":
            sp.add_argument("--dry", action="store_true", help="只打印计划，不实际按键/截图")
            sp.add_argument("--manual-keys", action="store_true",
                            help="不自动注入按键，改为提示用户亲手按真实快捷键")
        if name == "panel":
            sp.add_argument("--port", type=int, default=8799, help="端口（缺省 8799）")

    args = p.parse_args(argv)

    if getattr(args, "list_areas", False):
        cl = common.load_checklist()
        print("可用区域（auto --area 用）:")
        for k, v in cl["areas"].items():
            print(f"  {k:12s} {v['label']}")
        return 0

    if not args.cmd:
        p.error("必须指定子命令：env / auto / guided / manual / report / panel / all")

    common.require_windows()

    if args.cmd == "env":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import envcheck

        return 0 if envcheck.run_env(state, args) else 1

    if args.cmd == "auto":
        state = _state(args)
        if args.release:
            state["release"] = True
        if getattr(args, "areas", None):
            state["areas"] = args.areas
        common.save_run(state)
        from qa import autogate

        return 0 if autogate.run_auto(state, args) else 1

    if args.cmd == "guided":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import guided

        guided.run_guided(state, args)
        return 0

    if args.cmd == "manual":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import manual

        manual.run_manual(state, args)
        return 0

    if args.cmd == "report":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import report

        report.run_report(state, args)
        return 0

    if args.cmd == "panel":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import webpanel

        webpanel.run_panel(state, args)
        return 0

    if args.cmd == "all":
        state = _state(args)
        if args.release:
            state["release"] = True
        from qa import autogate, envcheck, guided, manual, report

        envcheck.run_env(state, args)
        autogate.run_auto(state, args)
        if not sys.stdin.isatty():
            print("\n  [!] 非交互环境（管道输入），跳过 guided / manual，直接出报告。")
        else:
            if _yes("继续半自动引导（会注入按键）？[y/n] ", True):
                guided.run_guided(state, args)
            if _yes("继续手测打勾？[y/n] ", True):
                manual.run_manual(state, args)
        report.run_report(state, args)
        return 0

    return 0


def _yes(prompt: str, default: bool) -> bool:
    try:
        raw = input(prompt).strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes", "是")


if __name__ == "__main__":
    raise SystemExit(main())
