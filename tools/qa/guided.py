"""guided：半自动引导。

流程：确保应用在跑 → 自动注入按键 → 自动截图 → 自动判定；
自动判定通过就直接记 pass（可加 always_confirm 要求人工复核），
判定失败 / 无法判定则显示截图请用户 y/n/s。
"""
from __future__ import annotations

import time

from . import appctl, common
from .common import RUNS_DIR

VK = {
    "Ctrl": 0x11,
    "Shift": 0x10,
    "Alt": 0x12,
    "Space": 0x20,
    "Enter": 0x0D,
    "Esc": 0x1B,
    "F11": 0x7A,
    "F12": 0x7B,
}

# guided 自动项在 checklist 里写的是默认快捷键；实际必须读用户本地配置，
# 否则会把用户没绑定的键（例如 F11）透传给前台浏览器/其他程序。
GUIDED_HOTKEY_KEYS = {
    "1.1": "pickKey",
    "1.6": "toggleTitleBarKey",
    "1.7": "hideWindowKey",
    "2.6": "toggleLabGlobalKey",
    "4.1": "grayscaleFilterKey",
}


def _load_hotkey_config() -> dict:
    import json
    import os

    path = os.path.join(os.environ.get("APPDATA", ""), "Colorink", "hotkey-config.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _hotkey_to_keys(hotkey: str) -> list[str] | None:
    """把 'Ctrl+Shift+F' 转成 inject() 可用的按键列表；无法注入返回 None。"""
    if not hotkey or not isinstance(hotkey, str):
        return None
    parts = [p.strip() for p in hotkey.split("+") if p.strip()]
    if not parts:
        return None
    norm: list[str] = []
    for p in parts:
        low = p.lower()
        if low in ("ctrl", "control"):
            norm.append("Ctrl")
        elif low == "shift":
            norm.append("Shift")
        elif low == "alt":
            norm.append("Alt")
        elif low in ("space",):
            norm.append("Space")
        elif low in ("esc", "escape"):
            norm.append("Esc")
        elif low in ("enter", "return"):
            norm.append("Enter")
        elif len(p) == 1 and p.isalpha():
            norm.append(p.upper())
        elif p.upper() in VK:
            norm.append(p.upper())
        else:
            # 鼠标键或其他无法用 keybd_event 注入的键
            return None
    return norm


def _resolve_keys(item_id: str, keys: list[str]) -> list[str] | None:
    """按用户实际快捷键替换 checklist 里的默认按键；无法注入返回 None。"""
    cfg_key = GUIDED_HOTKEY_KEYS.get(item_id)
    if cfg_key is None:
        return keys
    hotkey = _load_hotkey_config().get(cfg_key)
    parsed = _hotkey_to_keys(hotkey)
    if parsed is not None:
        return parsed
    return None


def _press_or_ask(args, item: dict, label: str, keys: list[str]) -> None:
    """默认自动注入；--manual-keys 时改为提示用户亲手按键。"""
    if getattr(args, "manual_keys", False):
        combo = "+".join(keys)
        print(f"  [手动] {item['id']} {label}: 请按快捷键 {combo}")
        input("    按完后回车继续 > ")
        return
    inject(keys)


def _vk(key: str) -> int:
    return VK.get(key) or ord(key.upper())


def inject(combo: list[str]) -> None:
    import win32api

    vks = [_vk(k) for k in combo]
    for v in vks:
        win32api.keybd_event(v, 0, 0, 0)
    for v in reversed(vks):
        win32api.keybd_event(v, 0, 2, 0)
    time.sleep(0.25)


def _shot(state: dict, name: str, fullscreen: bool = False):
    """截屏并保存，返回 (路径, mss ScreenShot)。"""
    import mss

    with mss.mss() as s:
        if fullscreen:
            img = s.grab(s.monitors[0])
        else:
            h = appctl.find_hwnd()
            if h is None:
                raise RuntimeError("找不到 Colorink 窗口")
            r = appctl.window_rect(h)
            img = s.grab({"left": r[0], "top": r[1], "width": r[2] - r[0], "height": r[3] - r[1]})
    path = common.shot_dir(state) / f"{name}.png"
    mss.tools.to_png(img.rgb, img.size, output=str(path))
    return path, img


def _img_diff(a, b) -> float:
    from PIL import Image, ImageChops

    ia = Image.frombytes("RGB", a.size, a.rgb).resize((160, 100))
    ib = Image.frombytes("RGB", b.size, b.rgb).resize((160, 100))
    d = ImageChops.difference(ia, ib)
    hist = d.histogram()
    total = sum(i * n for i, n in enumerate(hist))
    return total / (ia.size[0] * ia.size[1] * 3)


def _saturation_dxcam(frame) -> float:
    import numpy as np

    rgb = frame[:, :, :3].astype(np.int16)
    return float((rgb.max(axis=2) - rgb.min(axis=2)).mean())


def _screen_shot_dxcam():
    """DXGI 桌面复制截图（能拍到 GPU 覆盖层；失败返回 None）。"""
    try:
        import dxcam

        cam = dxcam.create()
        if cam is None:
            return None
        return cam.grab()
    except Exception:
        return None


def _ensure_app(state: dict):
    if appctl.find_hwnd():
        print("  … 复用已运行的 Colorink")
        return
    log = RUNS_DIR / f"run-{state['run_id']}" / "guided-app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print("  … Colorink 没在跑，启动中")
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        appctl.launch_source(logfile=lf)
    if appctl.wait_window(20) is None:
        print("  [×] 20 秒内没等到 Colorink 窗口，稍后重试")


def _judge(state: dict, item: dict, act: dict, before: dict, after: dict):
    """返回 (自动通过?, 判定说明)。None 表示无法自动判定。"""
    j = act.get("judge", "none")

    if j == "picker_fullscreen":
        d = _img_diff(before["img"], after["img"])
        return d > 4.0, f"全屏画面差异 {d:.1f}"

    if j == "image_diff":
        d = _img_diff(before["img"], after["img"])
        return d > 6.0, f"窗口画面差异 {d:.1f}"

    if j == "titlebar_toggle":
        # 优先看窗口截图差异：隐藏/显示标题栏一定会在窗口顶部产生视觉变化。
        if before.get("img") is not None and after.get("img") is not None:
            d = _img_diff(before["img"], after["img"])
            if d > 4.0:
                return True, f"标题栏画面差异 {d:.1f}"
        before_rect = before.get("rect")
        after_rect = after.get("rect")
        changed = (
            before_rect is not None
            and after_rect is not None
            and (
                after_rect[2] - after_rect[0],
                after_rect[3] - after_rect[1],
            )
            != (
                before_rect[2] - before_rect[0],
                before_rect[3] - before_rect[1],
            )
        )
        return changed, f"窗口尺寸 {before_rect} → {after_rect}"

    if j == "window_visible":
        vis = after.get("visible")
        return vis == act.get("expect_visible", False), f"IsWindowVisible={vis}"

    if j == "grayscale_cycle":
        # 灰度是 GPU/DWM 覆盖层，dxcam/mss 都不一定能抓到；不要自动判失败，
        # 一律交给用户肉眼确认。
        return None, "灰度覆盖层无法通过截屏可靠判定，请人工确认"

    return None, ""


def _run_item(state: dict, item: dict, args) -> None:
    act = item.get("action") or {}
    j = act.get("judge", "none")

    # 用用户真实快捷键替换 checklist 里的默认值，避免把未绑定的键透传给前台程序。
    resolved_steps = []
    for step in act.get("steps", []):
        keys = _resolve_keys(item["id"], step.get("keys", []))
        if keys is None:
            raise SystemExit(
                f"[×] {item['id']} 的快捷键无法自动注入，请先在 Colorink 设置里改为键盘快捷键，"
                "或手动测试该项"
            )
        resolved_steps.append({**step, "keys": keys})
    resolved_restore = []
    for step in act.get("restore", []):
        keys = _resolve_keys(item["id"], step.get("keys", []))
        if keys is None:
            raise SystemExit(
                f"[×] {item['id']} 的还原快捷键无法自动注入，请先在 Colorink 设置里改为键盘快捷键"
            )
        resolved_restore.append({**step, "keys": keys})
    gray_keys = _resolve_keys(item["id"], ["Ctrl", "G"]) if j == "grayscale_cycle" else None
    if j == "grayscale_cycle" and gray_keys is None:
        raise SystemExit(
            f"[×] {item['id']} 的灰度快捷键无法自动注入，请先在 Colorink 设置里改为键盘快捷键"
        )

    if getattr(args, "dry", False):
        steps = " → ".join("+".join(s.get("keys", [])) for s in resolved_steps)
        if j == "grayscale_cycle":
            steps = f"{'+'.join(gray_keys or ['Ctrl', 'G'])} ×{act.get('toggle', 5)}"
        print(f"  [dry] {item['id']} {item['text']}")
        print(f"        动作: {steps or '-'} | 判定: {j}")
        return

    before: dict = {}
    after: dict = {}
    before_shot = after_shot = None

    # --- 动作前采集 ---
    if j == "grayscale_cycle":
        before["dx"] = _screen_shot_dxcam()
        try:
            before_shot, before["img"] = _shot(state, f"{item['id']}-before", fullscreen=True)
        except Exception as e:
            print(f"  [!] 前置截图失败: {e}")
    elif j in ("picker_fullscreen", "image_diff", "titlebar_toggle"):
        try:
            before_shot, before["img"] = _shot(state, f"{item['id']}-before", fullscreen=(j == "picker_fullscreen"))
        except Exception as e:
            print(f"  [!] 前置截图失败: {e}")
    if j == "titlebar_toggle":
        h = appctl.find_hwnd()
        if h is not None:
            before["rect"] = appctl.window_rect(h)

    # --- 执行动作 ---
    if j == "grayscale_cycle":
        if getattr(args, "manual_keys", False):
            combo = "+".join(gray_keys or ["Ctrl", "G"])
            print(f"  [手动] {item['id']} 动作: 请连续按 {combo} {act.get('toggle', 5)} 次")
            input("    按完后回车继续 > ")
        else:
            for _ in range(act.get("toggle", 5)):
                inject(gray_keys or ["Ctrl", "G"])
                time.sleep(act.get("wait", 0.6))
        # Native/Mag 覆盖层是异步启停的；等它真正显示/消失后再截图判定，
        # 避免“按键已触发但画面还没跟上”被误判成失败。
        time.sleep(2.0)
    else:
        for step in resolved_steps:
            _press_or_ask(args, item, "动作", step.get("keys", []))
            time.sleep(step.get("wait", 0.8))

    # --- 动作后采集 ---
    if j == "grayscale_cycle":
        after["dx"] = _screen_shot_dxcam()
        try:
            after_shot, after["img"] = _shot(state, f"{item['id']}-after", fullscreen=True)
        except Exception as e:
            print(f"  [!] 后置截图失败: {e}")
    elif j in ("picker_fullscreen", "image_diff", "titlebar_toggle"):
        try:
            after_shot, after["img"] = _shot(state, f"{item['id']}-after", fullscreen=(j == "picker_fullscreen"))
        except Exception as e:
            print(f"  [!] 后置截图失败: {e}")
    if j == "titlebar_toggle":
        h = appctl.find_hwnd()
        if h is not None:
            after["rect"] = appctl.window_rect(h)
    if j == "window_visible":
        import win32gui

        h = appctl.find_hwnd()
        after["visible"] = bool(h is not None and win32gui.IsWindowVisible(h))

    # --- 判定 ---
    ok, msg = _judge(state, item, act, before, after)

    # --- 还原 ---
    for step in resolved_restore:
        _press_or_ask(args, item, "还原", step.get("keys", []))
        time.sleep(step.get("wait", 0.5))
    if j == "grayscale_cycle":
        if getattr(args, "manual_keys", False):
            combo = "+".join(gray_keys or ["Ctrl", "G"])
            print(f"  [手动] {item['id']} 还原: 请再按一次 {combo}")
            input("    按完后回车继续 > ")
        else:
            inject(gray_keys or ["Ctrl", "G"])  # 第 6 次翻转，回到初始状态
        time.sleep(0.6)

    # --- 记录 ---
    print(f"  [{item['id']}] {item['text']}")
    if ok:
        print(f"    自动判定: 通过（{msg}）")
        if act.get("always_confirm"):
            a, note = common.answer(f"    人工复核确认 y/n/s > ")
            if a == "q":
                a, note = "s", "用户退出"
            result = {"y": "pass", "n": "fail", "s": "skip"}[a]
            note = note or f"自动: {msg}"
        else:
            result, note = "pass", f"自动: {msg}"
    else:
        print(f"    自动判定: {'未通过' if ok is False else '无法自动判定'}（{msg}），请人工确认")
        if after_shot:
            print(f"    截图: {after_shot}")
        a, note = common.answer("    y/n/s > ")
        if a == "q":
            a, note = "s", "用户退出"
        result = {"y": "pass", "n": "fail", "s": "skip"}[a]
        note = note or f"自动: {msg}"

    state.setdefault("guided", {})[item["id"]] = {
        "result": result,
        "note": note,
        "ts": common.now_ts(),
        "shots": [str(before_shot) if before_shot else None, str(after_shot) if after_shot else None],
    }
    common.save_run(state)


def iter_guided_items(checklist: dict, state: dict):
    for ph in checklist["phases"]:
        if ph.get("release_only") and not state.get("release"):
            continue
        for it in ph["items"]:
            if it.get("kind") == "guided":
                yield ph, it


def run_guided(state: dict, args) -> None:
    common.require_windows()
    checklist = common.load_checklist()
    done = state.setdefault("guided", {})

    print("\n=== guided 半自动引导 ===")
    print("  向导会自动按键 + 截图 + 判定；判定不过会请你人工确认。")
    if getattr(args, "dry", False):
        print("  --dry：只打印计划，不实际执行。")
    else:
        print("  [!] 接下来会向系统注入按键，请先停下手上的操作。")
        _ensure_app(state)

    pending = [(ph, it) for ph, it in iter_guided_items(checklist, state) if it["id"] not in done]
    for ph, it in pending:
        _run_item(state, it, args)

    if not getattr(args, "dry", False):
        print("\n  半自动引导结束（应用保持运行，可继续 manual 手测）。")
