#!/usr/bin/env python3

"""Measurement harness for the SAI UI-refresh problem.

Colorink writes the active brush colour straight into SAI's process memory
(``core.sai2_brush_link``). The write takes effect for painting, but SAI does
not know its own widgets are stale, so the colour panel keeps showing the old
value. This tool measures exactly which SAI controls display the colour and
which nudge makes them repaint.

Subcommands (run with the Windows interpreter, SAI must be running):

    read                      read the colour slot via core.sai2_brush_link
    scan R G B                report SAI controls whose on-screen pixels match
    write R G B               write the colour slot (reversible, no nudge)
    nudge HWND METHOD         send one refresh attempt to a control
    probe R G B HWND METHOD   write, scan, nudge, scan again — the full loop

METHOD is one of: invalidate, redraw, paint, click, move-click.

``scan`` needs Pillow (screen grab). Everything else is ctypes only.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sai2_brush_link  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Screen grabs come back in physical pixels, so window rects must be physical
# too. Without per-monitor-v2 awareness this process gets DPI-virtualised
# coordinates (e.g. 4224x2048 for a 5280x2560 desktop at 125%) and every
# rect-to-pixel comparison silently samples the wrong region.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except (AttributeError, OSError):
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.InvalidateRect.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
user32.RedrawWindow.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, wintypes.UINT)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_PAINT = 0x000F
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202

RDW_INVALIDATE = 0x0001
RDW_ERASE = 0x0004
RDW_UPDATENOW = 0x0100
RDW_ALLCHILDREN = 0x0080


def _virtual_origin() -> tuple[int, int]:
    """Top-left of the virtual desktop, i.e. the origin of a full-screen grab.

    A monitor placed above/left of the primary makes this negative, so pixel
    (0, 0) of the grab is not screen coordinate (0, 0).
    """
    return user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _sai_pid() -> int | None:
    sync = sai2_brush_link.SAI2Sync()
    st = sync.status()
    return st.get("pid")  # type: ignore[return-value]


def _sai_windows(pid: int) -> list[int]:
    """Every visible window of the process: top-levels plus all descendants."""
    tops: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        if _pid_of(hwnd) == pid:
            tops.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)

    out: list[int] = []
    for top in tops:
        out.append(top)
        kids: list[int] = []

        @WNDENUMPROC
        def kid_cb(hwnd, _lparam, _kids=kids):
            _kids.append(hwnd)
            return True

        user32.EnumChildWindows(top, kid_cb, 0)
        out.extend(kids)
    return [h for h in out if user32.IsWindowVisible(h)]


def cmd_read() -> int:
    sync = sai2_brush_link.SAI2Sync()
    print("status:", sync.status())
    print("color:", sync.get_color())
    return 0


def cmd_write(r: int, g: int, b: int) -> int:
    sync = sai2_brush_link.SAI2Sync()
    ok = sync.set_color(r, g, b)
    print(f"write ({r},{g},{b}) -> {ok}; read back {sync.get_color()}")
    return 0 if ok else 1


def cmd_scan(r: int, g: int, b: int, tol: int = 6, min_px: int = 8) -> int:
    from PIL import ImageGrab  # noqa: PLC0415 — optional dependency

    pid = _sai_pid()
    if not pid:
        print("SAI is not running / slot not resolvable")
        return 1

    shot = ImageGrab.grab(all_screens=True).convert("RGB")
    sw, sh = shot.size
    px = shot.load()
    vx, vy = _virtual_origin()

    # Container/canvas windows are skipped: they dwarf the actual colour
    # widgets and make a pure-Python pixel walk take minutes.
    max_area = int(os.environ.get("SAI_SCAN_MAX_AREA", "260000"))

    rows: list[tuple[int, int, int, int, int, str]] = []
    for hwnd in _sai_windows(pid):
        left, top, right, bottom = _rect_of(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0 or w * h > max_area:
            continue
        hits = 0
        for y in range(max(vy, top), min(vy + sh, bottom)):
            for x in range(max(vx, left), min(vx + sw, right)):
                pr, pg, pb = px[x - vx, y - vy]
                if abs(pr - r) <= tol and abs(pg - g) <= tol and abs(pb - b) <= tol:
                    hits += 1
        if hits >= min_px:
            rows.append((hits, hwnd, w, h, w * h, _class_of(hwnd)))

    rows.sort(key=lambda t: (-t[0] / max(1, t[4]), -t[0]))
    print(f"controls showing ({r},{g},{b}) +-{tol}:")
    for hits, hwnd, w, h, area, cls in rows:
        left, top, right, bottom = _rect_of(hwnd)
        pct = 100.0 * hits / max(1, area)
        print(
            f"  hwnd=0x{hwnd:X} [{cls}] {w}x{h} at ({left},{top}) "
            f"hits={hits} ({pct:.1f}% of rect)"
        )
    if not rows:
        print("  (none)")
    return 0


def _center(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return (r.right - r.left) // 2, (r.bottom - r.top) // 2


def cmd_nudge(hwnd: int, method: str) -> int:
    method = method.lower()
    if method == "invalidate":
        ok = user32.InvalidateRect(hwnd, None, True)
        print(f"InvalidateRect(0x{hwnd:X}) -> {ok}")
    elif method == "redraw":
        ok = user32.RedrawWindow(
            hwnd, None, None,
            RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_ALLCHILDREN,
        )
        print(f"RedrawWindow(0x{hwnd:X}) -> {ok}")
    elif method == "paint":
        ok = user32.PostMessageW(hwnd, WM_PAINT, 0, 0)
        print(f"PostMessage WM_PAINT -> {ok}")
    elif method in ("click", "move-click"):
        cx, cy = _center(hwnd)
        lp = (cy << 16) | (cx & 0xFFFF)
        if method == "move-click":
            user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
        a = user32.PostMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
        b = user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
        print(f"PostMessage click at client ({cx},{cy}) -> down={a} up={b}")
    else:
        print(f"unknown method: {method}")
        return 2
    return 0


def _print_window(hwnd: int):
    """Capture a window's own rendering via PrintWindow (occlusion-proof).

    Returns a Pillow image, or None when the window refuses to render.
    """
    from PIL import Image  # noqa: PLC0415 — optional dependency

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    left, top, right, bottom = _rect_of(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)

    PW_RENDERFULLCONTENT = 0x00000002
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    if not ok:
        return None
    return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")


def cmd_look(hwnd: int, out: str | None = None) -> int:
    """Report what a control actually renders: size, dominant colours, slot hit."""
    from collections import Counter  # noqa: PLC0415

    img = _print_window(hwnd)
    if img is None:
        print(f"PrintWindow(0x{hwnd:X}) failed")
        return 1
    sync = sai2_brush_link.SAI2Sync()
    slot = sync.get_color()
    cnt = Counter(img.getdata())
    total = img.size[0] * img.size[1]
    print(f"hwnd=0x{hwnd:X} [{_class_of(hwnd)}] {img.size[0]}x{img.size[1]} slot={slot}")
    for color, n in cnt.most_common(8):
        print(f"   {color} x{n} ({100.0 * n / total:.1f}%)")
    if slot:
        hits = sum(
            n for c, n in cnt.items()
            if abs(c[0] - slot["r"]) <= 6 and abs(c[1] - slot["g"]) <= 6
            and abs(c[2] - slot["b"]) <= 6
        )
        print(f"   slot-colour pixels: {hits}")
    if out:
        img.save(out)
        print(f"   saved {out}")
    return 0


def _top_levels(pid: int) -> set[int]:
    tops: set[int] = set()

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        if _pid_of(hwnd) == pid:
            tops.add(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return tops


def cmd_diff(hwnd: int, method: str, watch: list[int]) -> int:
    """Capture the watched controls around one nudge and report what changed.

    Answers three questions at once: did the control repaint, did it repaint
    *with the colour that is currently in the memory slot*, and did the nudge
    have visible side effects (a popup window appearing).
    """
    from PIL import Image, ImageGrab  # noqa: PLC0415 — optional dependency

    pid = _sai_pid()
    if not pid:
        print("SAI is not running / slot not resolvable")
        return 1

    sync = sai2_brush_link.SAI2Sync()
    slot = sync.get_color()
    print(f"slot colour: {slot}")

    targets = [hwnd] + [h for h in watch if h != hwnd]
    rects = {h: _rect_of(h) for h in targets}
    tops_before = _top_levels(pid)

    def grab() -> "Image.Image":
        return ImageGrab.grab(all_screens=True).convert("RGB")

    before = grab()
    cmd_nudge(hwnd, method)
    time.sleep(0.5)
    after = grab()

    tops_after = _top_levels(pid)
    new_tops = tops_after - tops_before
    if new_tops:
        print("!! new top-level windows appeared (side effect):")
        for h in new_tops:
            print(f"   hwnd=0x{h:X} [{_class_of(h)}] rect={_rect_of(h)}")
    else:
        print("no new top-level windows (no popup side effect)")

    vx, vy = _virtual_origin()
    for h in targets:
        left, top, right, bottom = rects[h]
        box = (left - vx, top - vy, right - vx, bottom - vy)
        crop_a = before.crop(box)
        crop_b = after.crop(box)
        pa, pb = crop_a.load(), crop_b.load()
        w, h_px = crop_a.size
        changed = 0
        slot_hits = 0
        for y in range(h_px):
            for x in range(w):
                if pa[x, y] != pb[x, y]:
                    changed += 1
                if slot:
                    r, g, b = pb[x, y]
                    if (abs(r - slot["r"]) <= 6 and abs(g - slot["g"]) <= 6
                            and abs(b - slot["b"]) <= 6):
                        slot_hits += 1
        total = max(1, w * h_px)
        print(
            f"hwnd=0x{h:X} [{_class_of(h)}] {w}x{h_px} at ({left},{top}): "
            f"changed={changed} ({100.0 * changed / total:.1f}%), "
            f"slot-colour px after={slot_hits}"
        )
    return 0


def cmd_probe(r: int, g: int, b: int, hwnd: int, method: str) -> int:
    sync = sai2_brush_link.SAI2Sync()
    before = sync.get_color()
    print(f"slot before: {before}")
    cmd_write(r, g, b)
    time.sleep(0.3)
    print("\n-- scan after write, before nudge --")
    cmd_scan(r, g, b)
    print(f"\n-- nudge 0x{hwnd:X} via {method} --")
    cmd_nudge(hwnd, method)
    time.sleep(0.4)
    print("\n-- scan after nudge --")
    cmd_scan(r, g, b)
    if before:
        print(f"\nrestoring {before}")
        sync.set_color(before["r"], before["g"], before["b"])
        cmd_nudge(hwnd, method)
    return 0


def cmd_verify(r: int, g: int, b: int) -> int:
    """End-to-end check of the production refresher against a live SAI.

    Writes a colour through ``core.sai2_brush_link`` (which now nudges SAI's
    widgets itself) and then confirms, by offscreen render, that both the
    brush swatch and the stroke preview really show it.
    """
    from core import sai2_ui_refresh  # noqa: PLC0415

    sai2_ui_refresh.DEBUG = True
    sync = sai2_brush_link.SAI2Sync()
    before = sync.get_color()
    print(f"slot before: {before}")

    ok = sync.set_color(r, g, b)
    print(f"set_color -> {ok}; refresher status: {sync.ui_refresher.status()}")
    time.sleep(0.6)
    # A second write exercises the lazy click verification path as well.
    sync.set_color(r, g, b)
    time.sleep(0.6)
    status = sync.ui_refresher.status()
    print(f"refresher status after verify: {status}")

    backend = sync.ui_refresher._get_backend()
    rc = 0
    for name in ("swatch", "preview"):
        handle = status.get(name)
        if not handle:
            print(f"  {name}: NOT RESOLVED")
            rc = 1
            continue
        hwnd = int(handle, 16)
        ratio = backend.fill_ratio(hwnd, (r, g, b))
        verdict = "OK" if ratio >= 0.004 else "STALE"
        print(f"  {name}: {handle} [{_class_of(hwnd)}] fill={ratio:.4f} {verdict}")
        if verdict != "OK":
            rc = 1
    if before:
        sync.set_color(before["r"], before["g"], before["b"])
        print(f"restored {before}")
    return rc


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "read":
        return cmd_read()
    if cmd == "write":
        return cmd_write(int(rest[0]), int(rest[1]), int(rest[2]))
    if cmd == "scan":
        return cmd_scan(int(rest[0]), int(rest[1]), int(rest[2]))
    if cmd == "nudge":
        return cmd_nudge(int(rest[0], 0), rest[1])
    if cmd == "diff":
        return cmd_diff(int(rest[0], 0), rest[1], [int(x, 0) for x in rest[2:]])
    if cmd == "verify":
        return cmd_verify(int(rest[0]), int(rest[1]), int(rest[2]))
    if cmd == "look":
        return cmd_look(int(rest[0], 0), rest[1] if len(rest) > 1 else None)
    if cmd == "probe":
        return cmd_probe(int(rest[0]), int(rest[1]), int(rest[2]), int(rest[3], 0), rest[4])
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
