"""auto：无人值守自动化守卫。

检查项（id 与 checklist.json 的 auto_checks 对应）：
  version_consistency  版本四件套一致
  pytest_all           pytest 全量 / --area 定向
  src_smoke            python main.py 冷启动冒烟
  exe_smoke            dist/Onefile/Colorink.exe 冒烟 + 版本资源
  single_instance      EXE 单实例锁
  stderr_delta         stderr.log 相对 env 基线无新增
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time

from . import appctl, common
from .common import PROJECT_ROOT, RUNS_DIR


def _app_version() -> str:
    src = (PROJECT_ROOT / "core" / "updater.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src)
    if not m:
        raise SystemExit("[×] 找不到 core/updater.py 里的 APP_VERSION")
    return m.group(1)


def _exe_file_version(exe) -> str | None:
    """Read the fixed file version from a PE's version resource.

    win32api.GetFileVersionInfo(path, "\\\\") returns the fixed info dict
    (FileVersionMS/LS), not a nested StringFileInfo dict.  Use the fixed
    fields so the check is independent of translation/codepage quirks.
    """
    try:
        import win32api

        fvi = win32api.GetFileVersionInfo(str(exe), "\\")
        ms = int(fvi.get("FileVersionMS", 0))
        ls = int(fvi.get("FileVersionLS", 0))
        return (
            f"{(ms >> 16) & 0xffff}.{ms & 0xffff}."
            f"{(ls >> 16) & 0xffff}.{ls & 0xffff}"
        )
    except Exception:
        return None


def _run_log(state: dict, name: str):
    d = RUNS_DIR / f"run-{state['run_id']}"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _tail(path, n: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def check_version(state: dict, args) -> tuple[str, str, str]:
    v = _app_version()
    ok, detail = True, []

    info = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")
    maj, min_, pat = (int(x) for x in v.split("."))
    for needle, label in [
        (f"StringStruct('FileVersion', '{v}.0')", "FileVersion"),
        (f"StringStruct('ProductVersion', '{v}.0')", "ProductVersion"),
        (f"filevers=({maj}, {min_}, {pat}, 0)", "filevers"),
        (f"prodvers=({maj}, {min_}, {pat}, 0)", "prodvers"),
    ]:
        if needle not in info:
            ok = False
            detail.append(f"file_version_info.txt 缺 {label}")

    notes = (PROJECT_ROOT / "release_notes.md").read_text(encoding="utf-8")
    if not notes.startswith(f"## v{v}\n"):
        ok = False
        detail.append(f"release_notes.md 首行不是 ## v{v}")

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"当前版本：\*\*v?([0-9.]+)\*\*", readme)
    if not m or m.group(1) != v:
        ok = False
        detail.append(f"README「当前版本」不是 v{v}")

    if ok:
        return "pass", f"版本四件套一致 v{v}", ""
    return "fail", "版本不一致，改齐再发版", "\n".join(detail)


def check_pytest(state: dict, args) -> tuple[str, str, str]:
    checklist = common.load_checklist()
    areas = getattr(args, "areas", None) or None
    if areas:
        files: list[str] = []
        for a in areas:
            if a not in checklist["areas"]:
                return "fail", f"未知区域 {a}", f"可选: {', '.join(checklist['areas'])}"
            files += checklist["areas"][a]["pytest"]
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + files
        label = f"pytest 定向（{', '.join(areas)}）"
    else:
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        label = "pytest 全量"

    print(f"  … {label} 运行中（约 40 秒）")
    try:
        out = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return "fail", f"{label} 超时（>15 分钟）", ""

    tail = (out.stdout or "")[-600:]
    m = re.search(r"(\d+)\s+passed", tail)
    passed = int(m.group(1)) if m else 0
    nfail = int((re.search(r"(\d+)\s+failed", tail) or [None, 0])[1])
    nerr = int((re.search(r"(\d+)\s+errors?", tail) or [None, 0])[1])

    if "no tests ran" in tail:
        return "fail", f"{label}: 没采到任何测试", tail
    if nfail or nerr or out.returncode not in (0, 1):
        return "fail", f"{label}: {nfail} failed / {nerr} errors", tail
    if not areas:
        base = checklist["baseline"].get("pytest_min_passed", 0)
        if passed < base:
            return "fail", f"{label}: {passed} passed < 基线 {base}（数字只许涨不许跌）", tail
    return "pass", f"{label}: {passed} passed", tail


def check_src_smoke(state: dict, args) -> tuple[str, str, str]:
    # pytest 子进程退出后，Qt 测试窗口可能还没被窗口站完全清理；
    # 等一小段时间，避免把测试残留的隐藏窗口误判成“Colorink 已在运行”。
    time.sleep(2)
    if appctl.find_hwnds():
        return "skip", "Colorink 已在运行，跳过冒烟", "先完全退出 Colorink 再跑 auto"

    log = _run_log(state, "src-smoke.log")
    before = set(appctl.find_hwnds())
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = appctl.launch_source(logfile=lf)
    h = appctl.wait_window(20)
    alive = appctl.process_alive(proc)
    if h is None and not alive:
        return "fail", "python main.py 启动即崩", _tail(log)
    if h is None:
        appctl.terminate(proc)
        return "fail", "20 秒内没找到 Colorink 窗口", _tail(log)

    new = [x for x in appctl.find_hwnds() if x not in before]
    appctl.close_windows(new)
    time.sleep(2)
    appctl.terminate(proc)

    text = _tail(log)
    if "Traceback" in text:
        return "fail", "启动过程有 Traceback", text
    return "pass", "冷启动正常：出窗口、进程存活、无 traceback", f"窗口 handle={h}"


def check_exe_smoke(state: dict, args) -> tuple[str, str, str]:
    exe = PROJECT_ROOT / "dist" / "Onefile" / "Colorink.exe"
    if not exe.exists():
        if state.get("release"):
            return "fail", "发版模式但没有打包产物", "先跑 python build_pyqt.py"
        return "skip", "没有 dist/Onefile/Colorink.exe", "动过打包相关内容时需先打包再测"
    time.sleep(2)  # 同样等上一个冒烟/测试窗口清理完
    if appctl.find_hwnds():
        return "skip", "Colorink 已在运行，跳过 EXE 冒烟", "先完全退出 Colorink 再跑 auto"

    v = _app_version()
    fv = _exe_file_version(exe)
    if fv is None:
        return "fail", "读不到 EXE 版本资源", "用 build_pyqt.py 重新打包"
    if fv != f"{v}.0":
        return "fail", f"EXE 文件版本 {fv} ≠ APP_VERSION {v}.0", "用 build_pyqt.py 重新打包"

    log = _run_log(state, "exe-smoke.log")
    before = set(appctl.find_hwnds())
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = appctl.launch_exe(exe, logfile=lf)
    h = appctl.wait_window(30)
    alive = appctl.process_alive(proc)
    if h is None and not alive:
        return "fail", "EXE 启动即退", _tail(log)
    if h is None:
        appctl.terminate(proc)
        return "fail", "30 秒内没找到 EXE 窗口", _tail(log)

    new = [x for x in appctl.find_hwnds() if x not in before]
    appctl.close_windows(new)
    time.sleep(3)
    appctl.terminate(proc)
    # onefile 的 bootloader 被 terminate 后，子进程可能仍存活；强制清掉。
    appctl.kill_all()
    appctl.close_windows([x for x in appctl.find_hwnds() if x not in before])

    text = _tail(log)
    if "Traceback" in text:
        return "fail", "EXE 运行有 Traceback", text
    return "pass", f"EXE 冒烟通过（文件版本 {fv}）", f"窗口 handle={h}"


def check_single_instance(state: dict, args) -> tuple[str, str, str]:
    exe = PROJECT_ROOT / "dist" / "Onefile" / "Colorink.exe"
    if not exe.exists():
        return "skip", "没有 dist/Onefile/Colorink.exe", ""
    time.sleep(2)  # 等上一个冒烟/测试窗口清理完
    if appctl.find_hwnds():
        return "skip", "Colorink 已在运行，跳过", "先完全退出 Colorink 再跑 auto"

    # 等上一个 EXE 冒烟遗留的 onefile 进程退出，避免共享内存仍被占用。
    for _ in range(20):
        if appctl.count_processes() <= 0:
            break
        time.sleep(0.5)
    # 如果还有残留（onefile 子进程没被父进程带走），直接强制清掉。
    appctl.kill_all()

    before = set(appctl.find_hwnds())
    log = _run_log(state, "single-instance.log")
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        p1 = appctl.launch_exe(exe, logfile=lf)
    h1 = appctl.wait_window(30)
    if h1 is None:
        appctl.terminate(p1)
        return "fail", "第一次启动没出窗口", _tail(log)
    # PyInstaller onefile 会同时存在 bootloader 父进程 + 子进程，数进程名不可靠。
    # 等窗口稳定后再记录第一实例的窗口集合，用“第二次启动是否退出/是否新增窗口”判断。
    time.sleep(2)
    first_hwnds = set(appctl.find_hwnds())

    p2 = appctl.launch_exe(exe, logfile=None)
    try:
        p2.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(1.5)
    second_hwnds = set(appctl.find_hwnds())

    new_hwnds = second_hwnds - first_hwnds
    all_new = [x for x in second_hwnds if x not in before]
    appctl.close_windows(all_new)
    time.sleep(2)
    appctl.terminate(p1)
    appctl.terminate(p2)
    appctl.kill_all()
    appctl.close_windows([x for x in appctl.find_hwnds() if x not in before])

    if p2.poll() is None:
        return "fail", "单实例锁可疑：第二个实例 10 秒后仍存活", ""
    if new_hwnds:
        return "fail", f"单实例锁可疑：第二个实例创建了新窗口 {sorted(new_hwnds)}", ""
    return "pass", f"单实例锁生效（第二个实例退出，窗口 {len(first_hwnds)} → {len(second_hwnds)}）", ""


def check_stderr_delta(state: dict, args) -> tuple[str, str, str]:
    snap = (state.get("env") or {}).get("stderr_baseline") or {}
    log = PROJECT_ROOT / "stderr.log"
    if not log.exists() and not snap.get("exists"):
        return "pass", "stderr.log 不存在（干净）", ""
    cur = hashlib.sha256(log.read_bytes()).hexdigest() if log.exists() else ""
    if cur != snap.get("sha256", ""):
        return "fail", "stderr.log 有新增内容", _tail(log, 600)
    return "pass", "stderr.log 无新增", f"{snap.get('size', 0)} 字节不变"


CHECKS = [
    ("version_consistency", check_version),
    ("pytest_all", check_pytest),
    ("src_smoke", check_src_smoke),
    ("exe_smoke", check_exe_smoke),
    ("single_instance", check_single_instance),
    ("stderr_delta", check_stderr_delta),
]


def run_auto(state: dict, args) -> bool:
    common.require_windows()
    skip_ids = set(getattr(args, "skip_check", None) or [])
    print("\n=== auto 自动化守卫 ===")
    if skip_ids:
        print(f"  手动跳过: {', '.join(sorted(skip_ids))}")

    results = state.setdefault("auto", {})
    for cid, fn in CHECKS:
        if cid in skip_ids:
            results[cid] = {"result": "skip", "note": "手动跳过", "detail": "", "ts": common.now_ts()}
            common.save_run(state)
            print(f"  [·] {cid}: 手动跳过")
            continue
        print(f"  … {cid} 检查中")
        try:
            result, note, detail = fn(state, args)
        except Exception as e:
            result, note, detail = "fail", f"{cid} 检查器异常: {e}", ""
        results[cid] = {"result": result, "note": note, "detail": detail, "ts": common.now_ts()}
        common.save_run(state)
        print(f"  {common.RESULT_ICON[result]} {cid}: {note}")
        if detail and result != "pass":
            for line in detail.splitlines()[:8]:
                print(f"       {line}")

    fails = [cid for cid, r in results.items() if r["result"] == "fail"]
    skips = [cid for cid, r in results.items() if r["result"] == "skip"]
    print(f"\n  结论: {'全绿' if not fails else f'{len(fails)} 项失败'}"
          + (f"，{len(skips)} 项跳过" if skips else ""))
    return not fails
