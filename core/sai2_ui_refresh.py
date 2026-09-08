#!/usr/bin/env python3

"""Make PaintTool SAI repaint its own colour widgets after a memory write.

``core.sai2_brush_link`` writes the active brush colour straight into SAI's
process memory. Painting picks the new colour up immediately, but SAI has no
idea its widgets went stale, so its UI keeps showing the previous colour.
Two different SAI controls need two different nudges — measured against a
live SAI Ver.2 (Preview.2024.08.14) build:

* **Brush colour swatch** (square, ~49x49 logical, in the tool panel):
  repaints straight from the colour slot, so a plain ``InvalidateRect`` is
  enough. No synthetic input, no side effects.
* **Brush stroke preview** (wide strip, ~191x50 logical, top of the brush
  settings panel): blits a *cached* stroke bitmap, so invalidating it
  redraws the stale cache. Only a real mouse click makes SAI re-render the
  sample stroke, which is why the click is posted to that control. The same
  click also advances the preview's own background through a three-state
  cycle, so a full cycle (:data:`CLICK_CYCLE`) is posted at once and the
  background lands back on the style it started from.

The colour panel's wheel, slider knobs and numeric labels are driven by
SAI's own picker state (a separate structure), so no repaint can move them;
they are intentionally left alone.

**There is exactly one refresh mode — :data:`MODE_FULL`.** The app never
offers another: a repaint-only session silently lets the preview's cached
bitmap drift away from the colour slot, and the preview then cannot be
re-identified until SAI itself happens to re-render it. (``MODE_REPAINT`` /
``MODE_OFF`` remain in the code as internal seams for tooling and tests, but
no app configuration selects them.)

Safety rules, because this injects input into another process:

* Clicks go to one specific child window via ``PostMessage``, never to the
  canvas, and never through the system cursor — so they cannot draw.
* A control is only ever clicked once it has been *observed rendering a brush
  colour we know about*. Shape alone is not enough: on the measured build the
  brush-tool row is 195x52 against the preview's 191x50 (aspect 3.71 vs
  3.78), and clicking it switches the user's tool. Colour evidence separates
  them, and with no evidence nothing is clicked at all.
* The chosen target is then *verified* on the next colour change by rendering
  it offscreen: if the colour never shows up it is dropped after two tries
  and the refresher degrades to repaint-only.
* No clicks while SAI holds a mouse capture, has a menu open or is being
  moved/resized (the user is mid-interaction), and none while the process
  looks hung.
* Discovery renders candidate controls offscreen, so both the number of
  candidates and their size are bounded — it runs on the sync thread.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

# ── Refresh modes ────────────────────────────────────────────────────────
MODE_OFF = "off"          # never touch SAI's UI (pre-1.6.12 behaviour)
MODE_REPAINT = "repaint"  # invalidate only: swatch + slider gradients
MODE_FULL = "full"        # repaint + click the stroke preview
# Full is the app's only mode: repaint-only sessions let the stroke preview's
# cached bitmap drift away from the colour slot forever (that drift is what
# this module's discovery re-runs exist to heal). The click is bounded: it
# only ever lands on a control observed rendering a colour we know, is
# verified by its next render, and is abandoned after two failed verifies.
DEFAULT_MODE = MODE_FULL

_MODE_ALIASES = {
    "": MODE_FULL,
    "auto": MODE_FULL,
    "on": MODE_FULL,
    "true": MODE_FULL,
    "1": MODE_FULL,
    "full": MODE_FULL,
    "click": MODE_FULL,
    "repaint": MODE_REPAINT,
    "invalidate": MODE_REPAINT,
    "off": MODE_OFF,
    "false": MODE_OFF,
    "0": MODE_OFF,
    "none": MODE_OFF,
}

DEBUG = False


def _log(msg: str) -> None:
    if DEBUG:
        print(f"[SAI2UiRefresh] {msg}", file=sys.stderr, flush=True)


def normalize_mode(value: object) -> str:
    """Map config/env spellings onto one of the three modes."""
    if value is True:
        return MODE_FULL
    if value is False:
        return MODE_OFF
    return _MODE_ALIASES.get(str(value or "").strip().lower(), DEFAULT_MODE)


# ── Control classification (pure geometry, DPI independent) ──────────────
#
# Absolute pixel sizes are useless on their own: SAI is DPI-unaware, so a
# 49x49 swatch is reported as 74x74 at 150% scaling. The swatch is therefore
# located by *rendered content* (it is the square control that paints the
# colour we just wrote) and its side length then becomes the yardstick for
# every other control — the stroke preview is the strip roughly as tall as
# the swatch, while sliders and buttons are far shorter.

SWATCH_MIN_SIDE = 16          # smaller than this is an icon, not a swatch
SWATCH_MAX_SIDE = 220         # bigger is a canvas/panel, not a colour swatch
SWATCH_SQUARE_TOLERANCE = 0.14
SWATCH_MIN_FILL = 0.15        # fraction of the control painted in the colour

# Identification renders candidates offscreen, and each render is a synchronous
# send into SAI (~50 ms on the measured build), so discovery walks candidates
# best-first and stops early under a hard probe budget. The budget covers a
# full colour sweep across every strip-shaped candidate (see pick_preview), so
# a matching colour deep in the evidence list is still reached.
MAX_PROBES = 12

PREVIEW_MIN_HEIGHT_RATIO = 0.70   # relative to the swatch side
PREVIEW_MAX_HEIGHT_RATIO = 1.45   # measured 50/49; a mismatched yardstick fails
PREVIEW_MIN_ASPECT = 3.0          # width / height; keeps the 125x49 tool row out
PREVIEW_MAX_ASPECT = 5.5          # keeps 159x24 / 180x24 sliders out
# The measured sample stroke covers ~5% of the preview, so 1% keeps a wide
# margin while making it very unlikely that a control qualifies by accident.
PREVIEW_MIN_FILL = 0.01


@dataclass(frozen=True)
class Candidate:
    """One enumerated SAI child window."""

    hwnd: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height > 0 else 0.0


def is_square_control(
    cand: Candidate,
    min_side: int = SWATCH_MIN_SIDE,
    max_side: int = SWATCH_MAX_SIDE,
) -> bool:
    """True for a plausible colour-swatch shape.

    The upper bound matters for cost as much as for correctness: candidates
    that pass here get rendered offscreen and scanned pixel by pixel on the
    sync thread, and a canvas-sized square would make that expensive.
    """
    w, h = cand.width, cand.height
    if w < min_side or h < min_side or w > max_side or h > max_side:
        return False
    return abs(w - h) <= max(2, round(SWATCH_SQUARE_TOLERANCE * max(w, h)))


def is_preview_strip(cand: Candidate, swatch_side: int) -> bool:
    """True for a strip shaped like SAI's brush stroke preview.

    Anchored on the swatch side so it holds at any DPI: the preview is as
    tall as the swatch (49 vs 50 logical px) whereas SAI's sliders are less
    than half that height, and its aspect ratio stays well below a slider's.
    """
    if swatch_side <= 0 or cand.width <= 0 or cand.height <= 0:
        return False
    if not (PREVIEW_MIN_HEIGHT_RATIO * swatch_side
            <= cand.height
            <= PREVIEW_MAX_HEIGHT_RATIO * swatch_side):
        return False
    return PREVIEW_MIN_ASPECT <= cand.aspect <= PREVIEW_MAX_ASPECT


def pick_swatch(
    candidates: Sequence[Candidate],
    fill_ratio: Callable[[int], float],
    min_fill: float = SWATCH_MIN_FILL,
    max_probes: int = MAX_PROBES,
) -> tuple[int | None, int]:
    """Pick the largest square control filled with the reference colour.

    Returns ``(hwnd, side)``; ``(None, 0)`` when nothing qualifies.
    """
    # Each probe is a synchronous PrintWindow round-trip into SAI (~50 ms on
    # the measured build), so the order matters more than the scoring: walk
    # the squares from largest to smallest and stop at the first hit. The
    # brush swatch is the biggest colour-filled square in SAI's UI at any
    # DPI, so this yields the same answer as ranking them all for a fraction
    # of the cost.
    squares = sorted(
        (c for c in candidates if is_square_control(c)),
        key=lambda c: (-c.area, c.hwnd),
    )
    for probes, cand in enumerate(squares, start=1):
        if probes > max_probes:
            _log(f"swatch probe budget exhausted after {max_probes} renders")
            break
        if fill_ratio(cand.hwnd) >= min_fill:
            return cand.hwnd, min(cand.width, cand.height)
    return None, 0


def pick_preview(
    candidates: Sequence[Candidate],
    swatch_side: int,
    fill_ratio: Callable[[int, tuple[int, int, int]], float],
    references: Sequence[tuple[int, int, int]],
    min_fill: float = PREVIEW_MIN_FILL,
    max_probes: int = MAX_PROBES,
) -> int | None:
    """Pick the strip that is actually rendering a known brush colour.

    Shape alone cannot identify the stroke preview: on the measured build the
    tool row next to it is 195x52 against the preview's 191x50 — same height
    class, aspect 3.71 vs 3.78 — and on a 150%-DPI screen the tool row is
    even *larger* than the preview. Clicking the tool row switches the user's
    brush tool, so geometry only narrows the field and the decision is made
    on rendered content: the preview holds a sample stroke in the colour SAI
    last drew it with, and *references* are the colours we know about (the
    slot colour before the write, plus colours written or observed this
    session).

    Probing is colour-major: each reference colour sweeps across ALL strips
    before the next colour is tried, and a sweep is never truncated
    mid-way. A strip-major order would spend the whole render budget on the
    (larger, empty) tool-row decoys and the true preview would only ever be
    probed with the first reference or two — a matching colour later in the
    evidence list would never be reached.
    """
    strips = sorted(
        (c for c in candidates if is_preview_strip(c, swatch_side)),
        key=lambda c: (-c.area, c.hwnd),
    )
    if not strips or not references:
        return None

    probes = 0
    sweep_cost = len(strips)
    for ref in references:
        if probes + sweep_cost > max_probes:
            _log(f"preview probe budget exhausted after {probes} renders")
            return None
        for cand in strips:
            probes += 1
            if fill_ratio(cand.hwnd, ref) >= min_fill:
                return cand.hwnd
    return None


# ── Win32 backend ────────────────────────────────────────────────────────


class RefreshBackend(Protocol):
    """Everything the refresher needs from the window system."""

    def is_window(self, hwnd: int) -> bool: ...
    def is_hung(self, hwnd: int) -> bool: ...
    def main_window(self, pid: int) -> int | None: ...
    def candidates(self, pid: int) -> list[Candidate]: ...
    def fill_ratio(self, hwnd: int, rgb: tuple[int, int, int]) -> float: ...
    def invalidate(self, hwnd: int) -> bool: ...
    def click(self, hwnd: int, times: int = 1) -> bool: ...
    def input_busy(self, hwnd: int) -> bool: ...


class Win32Backend:
    """ctypes implementation of :class:`RefreshBackend`."""

    # Message / flag constants
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001
    PW_RENDERFULLCONTENT = 0x0002
    # GUI thread states that mean "the user is mid-interaction"
    GUI_INMOVESIZE = 0x00000002
    GUI_INMENUMODE = 0x00000004
    GUI_SYSTEMMENUMODE = 0x00000008
    GUI_POPUPMENUMODE = 0x00000010
    _BUSY_FLAGS = GUI_INMOVESIZE | GUI_INMENUMODE | GUI_SYSTEMMENUMODE | GUI_POPUPMENUMODE

    SAI_CHILD_CLASSES = ("sflchildwindow",)
    MAX_CANDIDATE_AREA = 400_000   # skip the canvas and big containers
    MAX_CANDIDATES = 400

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._enumproc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        u = self._user32
        u.EnumWindows.argtypes = (self._enumproc, wintypes.LPARAM)
        u.EnumChildWindows.argtypes = (wintypes.HWND, self._enumproc, wintypes.LPARAM)
        u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        u.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        u.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        u.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        )
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.InvalidateRect.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
        u.PostMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )
        u.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
        u.IsWindow.argtypes = (wintypes.HWND,)
        u.IsWindowVisible.argtypes = (wintypes.HWND,)
        u.IsHungAppWindow.argtypes = (wintypes.HWND,)
        u.GetGUIThreadInfo.argtypes = (
            wintypes.DWORD, ctypes.POINTER(self._GUITHREADINFO),
        )

    # -- queries ---------------------------------------------------------
    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self._user32.IsWindow(hwnd))

    def is_hung(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self._user32.IsHungAppWindow(hwnd))

    def _pid_of(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _class_of(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(128)
        self._user32.GetClassNameW(hwnd, buf, 128)
        return buf.value

    def _client_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return 0, 0
        return rect.right - rect.left, rect.bottom - rect.top

    def _top_levels(self, pid: int) -> list[tuple[int, int]]:
        """(area, hwnd) for every visible top-level window of the process."""
        found: list[tuple[int, int]] = []

        @self._enumproc
        def cb(hwnd, _lparam):
            if self._pid_of(hwnd) == pid and self._user32.IsWindowVisible(hwnd):
                rect = wintypes.RECT()
                self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                found.append((area, hwnd))
            return True

        self._user32.EnumWindows(cb, 0)
        return found

    def main_window(self, pid: int) -> int | None:
        """The largest visible top-level window of the process."""
        found = self._top_levels(pid)
        return max(found)[1] if found else None

    def candidates(self, pid: int) -> list[Candidate]:
        """Visible SAI child controls small enough to be widgets.

        Every top-level window is walked, not just the main one: SAI panels
        can be undocked into their own frames, and the brush swatch travels
        with its panel.
        """
        kids: list[int] = []
        for _area, top in self._top_levels(pid):

            @self._enumproc
            def cb(child, _lparam):
                kids.append(child)
                return len(kids) < self.MAX_CANDIDATES

            self._user32.EnumChildWindows(top, cb, 0)
            if len(kids) >= self.MAX_CANDIDATES:
                break

        out: list[Candidate] = []
        for child in kids:
            if not self._user32.IsWindowVisible(child):
                continue
            if self._class_of(child).lower() not in self.SAI_CHILD_CLASSES:
                continue
            w, h = self._client_size(child)
            cand = Candidate(child, w, h)
            if cand.area <= 0 or cand.area > self.MAX_CANDIDATE_AREA:
                continue
            out.append(cand)
        return out

    def _render_bgra(self, hwnd: int) -> tuple[int, int, bytes] | None:
        """Render a control offscreen; returns (width, height, BGRA bytes)."""
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0 or width * height > self.MAX_CANDIDATE_AREA:
            return None

        hdc_screen = self._user32.GetDC(0)
        if not hdc_screen:
            return None
        hdc_mem = hbmp = None
        old_bmp = None
        try:
            hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = self._gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hdc_mem or not hbmp:
                return None
            old_bmp = self._gdi32.SelectObject(hdc_mem, hbmp)
            if not self._user32.PrintWindow(hwnd, hdc_mem, self.PW_RENDERFULLCONTENT):
                return None

            header = self._BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(self._BITMAPINFOHEADER)
            header.biWidth = width
            header.biHeight = -height  # top-down rows
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = 0  # BI_RGB
            buf = ctypes.create_string_buffer(width * height * 4)
            copied = self._gdi32.GetDIBits(
                hdc_mem, hbmp, 0, height, buf, ctypes.byref(header), 0,
            )
            if not copied:
                return None
            return width, height, buf.raw
        finally:
            # DeleteObject refuses to free a bitmap that is still selected into
            # a DC, and DeleteDC does not free it either — so the bitmap has to
            # be swapped back out first. Without this the 32bpp bitmap (up to
            # MAX_CANDIDATE_AREA * 4 = 1.6 MB) leaked on every probe, and swatch
            # discovery runs several probes per pass, re-running on panel
            # re-dock / SAI restart until the process hit the GDI handle cap.
            if hdc_mem and old_bmp:
                self._gdi32.SelectObject(hdc_mem, old_bmp)
            if hbmp:
                self._gdi32.DeleteObject(hbmp)
            if hdc_mem:
                self._gdi32.DeleteDC(hdc_mem)
            self._user32.ReleaseDC(0, hdc_screen)

    def fill_ratio(self, hwnd: int, rgb: tuple[int, int, int], tolerance: int = 6) -> float:
        """Fraction of the control's own rendering painted in *rgb*.

        Renders offscreen with ``PrintWindow``, so it reports what SAI would
        paint right now — independent of what is currently on screen and of
        whether the window is covered by another one.
        """
        rendered = self._render_bgra(hwnd)
        if rendered is None:
            return 0.0
        width, height, raw = rendered
        return _count_matches(raw, rgb, tolerance) / float(width * height)

    def content_probe(self, hwnd: int) -> bool:
        """True when the control's own rendering looks like the preview.

        Content signature only, no colour evidence needed — the last-resort
        identification for a preview cache that drifted before any evidence
        existed (see :func:`is_preview_band`).
        """
        rendered = self._render_bgra(hwnd)
        if rendered is None:
            return False
        width, height, raw = rendered
        return is_preview_band(width, height, raw)

    def input_busy(self, hwnd: int) -> bool:
        """True while the user is mid-interaction inside SAI.

        A mouse capture means a drag is in progress (drawing a stroke,
        dragging a slider); menu/move-size flags mean a modal UI state. A
        posted click during any of those could be misread by SAI, so the
        caller defers it.
        """
        tid = self._user32.GetWindowThreadProcessId(hwnd, None)
        if not tid:
            return False
        info = self._GUITHREADINFO()
        info.cbSize = ctypes.sizeof(self._GUITHREADINFO)
        if not self._user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return False
        if info.hwndCapture:
            return True
        return bool(info.flags & self._BUSY_FLAGS)

    # -- actions ---------------------------------------------------------
    def invalidate(self, hwnd: int) -> bool:
        return bool(self._user32.InvalidateRect(hwnd, None, True))

    def click(self, hwnd: int, times: int = 1) -> bool:
        """Post *times* left clicks to the middle-ish of a control.

        The point is taken at a quarter of the client size instead of the
        exact centre: SAI is DPI-unaware, so its client metrics may be
        reported in a different scale than SAI itself uses, and a quarter
        point stays inside the control either way.

        The clicks are posted as one batch. Verified against the live build:
        three back-to-back clicks re-render the sample stroke, restore the
        preview background, and raise no popup — SAI does not fold them into a
        double-click.
        """
        width, height = self._client_size(hwnd)
        if width <= 0 or height <= 0:
            return False
        x = max(1, width // 4)
        y = max(1, height // 4)
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        delivered = 0
        for _ in range(max(1, int(times))):
            down = self._user32.PostMessageW(
                hwnd, self.WM_LBUTTONDOWN, self.MK_LBUTTON, lparam,
            )
            up = self._user32.PostMessageW(hwnd, self.WM_LBUTTONUP, 0, lparam)
            if not (down and up):
                break
            delivered += 1
        return delivered == max(1, int(times))


def _count_matches(raw: bytes, rgb: tuple[int, int, int], tolerance: int) -> int:
    """Count BGRA pixels within *tolerance* of *rgb*."""
    red, green, blue = rgb
    hits = 0
    for offset in range(0, len(raw) - 3, 4):
        if (abs(raw[offset] - blue) <= tolerance
                and abs(raw[offset + 1] - green) <= tolerance
                and abs(raw[offset + 2] - red) <= tolerance):
            hits += 1
    return hits


# ── Stroke-preview content classifier ─────────────────────────────────────
# Colour evidence is the primary way the stroke preview is identified (see
# pick_preview). When the preview's cached colour predates every piece of
# evidence — a drift left behind by older builds — a *content* signature is
# used as a last resort: the preview is a background panel holding one wide
# horizontal sample-stroke band, while every measured look-alike (brush-tool
# row, material/text rows) renders many small separate glyphs. Thresholds
# calibrated on a live SAI Ver.2 (Preview.2024.08.14) at 150% DPI.
#
# A control picked this way still has to survive the normal click
# verification (its render after the click must show the colour just
# written); a mis-detection is dropped after two failures and never probed
# again in the same SAI epoch.

INK_DISTANCE = 60                # channel-sum distance from the background
CLUSTER_MIN_SHARE = 0.004        # band pixels as a fraction of the control
CLUSTER_MAX_SHARE = 0.55
CLUSTER_MIN_DOMINANCE = 0.60     # share of all ink pixels
CLUSTER_MAX_COUNT = 10           # look-alikes render many separate glyphs
BAND_MAX_HEIGHT_FRACTION = 0.70  # a sample band never fills the strip height
BAND_MIN_WIDTH_FRACTION = 0.22
BAND_MIN_ASPECT = 2.0
BAND_MIN_CHROMA = 60             # saturated ink, or dark ink (black samples)
BAND_MAX_LUMINANCE = 120


def _band_stats(bgra: bytes, width: int, height: int):
    """Return (bg_rgb, [(px, bbox_w, bbox_h, avg_rgb)], ink_total).

    Ink = pixels far enough from the most common (background) colour;
    bands are 4-connected ink components, largest first.
    """
    if not bgra or width <= 0 or height <= 0:
        return (0, 0, 0), [], 0
    bg_counts: dict[tuple[int, int, int], int] = {}
    for offset in range(0, len(bgra) - 3, 4):
        key = (bgra[offset + 2], bgra[offset + 1], bgra[offset])
        bg_counts[key] = bg_counts.get(key, 0) + 1
    if not bg_counts:
        return (0, 0, 0), [], 0
    bg = max(bg_counts, key=bg_counts.get)
    bg_r, bg_g, bg_b = bg

    ink = bytearray(width * height)
    index = 0
    for offset in range(0, len(bgra) - 3, 4):
        b = bgra[offset]
        g = bgra[offset + 1]
        r = bgra[offset + 2]
        if abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b) > INK_DISTANCE:
            ink[index] = 1
        index += 1

    bands: list[list] = []
    seen = bytearray(width * height)
    queue: deque[int] = deque()
    for start in range(width * height):
        if not ink[start] or seen[start]:
            continue
        seen[start] = 1
        queue.append(start)
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        sum_r = sum_g = sum_b = 0
        while queue:
            pos = queue.popleft()
            x = pos % width
            y = pos // width
            count += 1
            px = pos * 4
            sum_b += bgra[px]
            sum_g += bgra[px + 1]
            sum_r += bgra[px + 2]
            if x < min_x:
                min_x = x
            elif x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            elif y > max_y:
                max_y = y
            if x > 0 and ink[pos - 1] and not seen[pos - 1]:
                seen[pos - 1] = 1
                queue.append(pos - 1)
            if x + 1 < width and ink[pos + 1] and not seen[pos + 1]:
                seen[pos + 1] = 1
                queue.append(pos + 1)
            if y > 0 and ink[pos - width] and not seen[pos - width]:
                seen[pos - width] = 1
                queue.append(pos - width)
            if y + 1 < height and ink[pos + width] and not seen[pos + width]:
                seen[pos + width] = 1
                queue.append(pos + width)
        bands.append([count, max_x - min_x + 1, max_y - min_y + 1,
                      (sum_r // count, sum_g // count, sum_b // count)])
    bands.sort(key=lambda b: -b[0])
    ink_total = sum(b[0] for b in bands)
    return bg, bands, ink_total


def is_preview_band(width: int, height: int, bgra: bytes) -> bool:
    """True when *bgra* looks like SAI's stroke preview (sample band).

    Pure pixel signature, deliberately conservative: everything uncertain
    returns False (the caller then simply does not click anything).
    """
    total = width * height
    if total <= 0:
        return False
    _bg, bands, ink_total = _band_stats(bgra, width, height)
    if not bands or ink_total <= 0:
        return False
    if len(bands) > CLUSTER_MAX_COUNT:
        return False
    count, band_w, band_h, avg = bands[0]
    if count / ink_total < CLUSTER_MIN_DOMINANCE:
        return False
    share = count / total
    if not (CLUSTER_MIN_SHARE <= share <= CLUSTER_MAX_SHARE):
        return False
    if band_h > BAND_MAX_HEIGHT_FRACTION * height:
        return False
    if band_w < BAND_MIN_WIDTH_FRACTION * width:
        return False
    if band_w / max(1, band_h) < BAND_MIN_ASPECT:
        return False
    red, green, blue = avg
    chroma = max(red, green, blue) - min(red, green, blue)
    luma = (red + green + blue) // 3
    return chroma >= BAND_MIN_CHROMA or luma <= BAND_MAX_LUMINANCE


# ── Refresher ────────────────────────────────────────────────────────────

# Clicking the stroke preview does double duty in SAI: it re-renders the sample
# *and* advances the preview's background through a three-state cycle
# (light -> pink -> black -> light). Measured on the live build: after three
# clicks the background is back to the style it started on, so a full cycle is
# posted every time and the refresh leaves no visible setting changed.
CLICK_CYCLE = 3

# Dragging the colour wheel writes ~10x/s. Clicking that often would run the
# background cycle continuously (a visible flicker in the preview) and inject
# far more input than needed, so the swatch follows every write while the
# preview waits for the colour to settle.
CLICK_SETTLE = 0.12

MAX_CLICK_FAILURES = 2         # give up clicking a target that never takes effect
# A failed discovery pass costs up to MAX_PROBES renders on the sync thread,
# which also polls SAI's colour every 100 ms — so retry slowly.
RESOLVE_RETRY_INTERVAL = 8.0
# A swatch-only resolution (click target missing) is re-checked on the poll
# tick: the colour evidence grows with every write, and SAI itself re-renders
# the preview cache whenever its colour changes inside SAI, so a later pass
# can succeed where the first one had no matching evidence. Rediscovery of a
# target that was *dropped* after repeated verification failures would just
# re-click the same mis-detected control, so it backs off — escalating with
# each consecutive drop.
PREVIEW_GIVE_UP_INITIAL = 300.0
PREVIEW_GIVE_UP_MAX = 3600.0
RECENT_COLOURS = 16            # written + poll-observed colours kept as evidence
MAX_REFERENCES = 4             # colours probed per discovery pass (each = a render)
# When no colour evidence ever matches (a preview cache that drifted before
# any evidence existed), fall back to the content classifier — but only after
# the colour passes have had a fair chance, and never more often than the
# scan interval. Picks still must survive click verification.
CONTENT_PROBE_DELAY = 4.0     # minimum degraded time before content probing
CONTENT_SCAN_INTERVAL = 8.0   # between content scans while still degraded

# _click_preview outcomes
CLICK_SENT = "sent"
CLICK_DEFERRED = "deferred"        # SAI mid-interaction, retry on the next tick
CLICK_UNAVAILABLE = "unavailable"  # no usable target


@dataclass
class _Resolved:
    pid: int
    main: int
    swatch: int | None = None
    swatch_side: int = 0
    preview: int | None = None
    click_failures: int = 0
    click_verified: bool = False
    probe_rgb: tuple[int, int, int] | None = None

    @property
    def needs_probe(self) -> bool:
        """True while a posted click still awaits its verification render."""
        return self.probe_rgb is not None and not self.click_verified


class SAIUiRefresher:
    """Nudges SAI into repainting its colour widgets after a memory write.

    Every window-system call goes through an injected backend so the policy
    (what to nudge, when to skip, when to give up) is testable without a
    running SAI.
    """

    def __init__(
        self,
        backend: RefreshBackend | None = None,
        mode: str = DEFAULT_MODE,
        min_interval: float = 0.06,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self.mode = normalize_mode(mode)
        self.min_interval = float(min_interval)
        self._clock = clock
        self._resolved: _Resolved | None = None
        self._last_refresh = 0.0
        self._last_write = 0.0
        self._last_resolve_attempt = 0.0
        self._dirty_rgb: tuple[int, int, int] | None = None
        self._dirty_previous: tuple[int, int, int] | None = None
        # Pre-write colour of the most recent write: the poll tick re-uses it
        # when re-running preview discovery on a swatch-only resolution.
        self._last_previous: tuple[int, int, int] | None = None
        # Re-discovery backoff after a click target was dropped.
        self._preview_give_up_until = 0.0
        self._preview_drops = 0
        # Degraded state: swatch known, preview missing, and every colour
        # pass has failed. Once this has lasted CONTENT_PROBE_DELAY seconds,
        # the content classifier is allowed to identify the preview.
        self._degraded_since: float | None = None
        self._last_content_probe = 0.0
        self._content_probed: set[int] = set()
        # Colours this session wrote OR the poll observed in SAI's slot,
        # newest first: the stroke preview's cached bitmap shows one of them,
        # which is how it gets identified. Writes alone are not enough — the
        # cache is re-rendered by SAI itself when SAI's colour changes inside
        # SAI, so the poll's read-backs are evidence too.
        self._recent: deque[tuple[int, int, int]] = deque(maxlen=RECENT_COLOURS)
        # Which slice of the evidence deque the next discovery pass probes:
        # every pass takes the pre-write colour plus a 3-colour window of the
        # deque, and the window rotates so a cache colour buried under newer
        # picks is still reached within a few passes.
        self._ref_rot = 0

    # -- configuration ---------------------------------------------------
    def set_mode(self, mode: object) -> bool:
        """Switch mode; returns True when it changed."""
        normalized = normalize_mode(mode)
        changed = normalized != self.mode
        self.mode = normalized
        if changed:
            # Discovery depends on the mode (repaint never looks for a click
            # target), so a cached resolution is no longer valid.
            self.reset()
            _log(f"mode -> {normalized}")
        return changed

    @property
    def enabled(self) -> bool:
        return self.mode != MODE_OFF

    def reset(self) -> None:
        """Forget resolved controls (SAI restarted, or layout changed)."""
        self._resolved = None
        self._last_resolve_attempt = 0.0
        self._dirty_rgb = None
        self._dirty_previous = None
        self._preview_give_up_until = 0.0
        self._preview_drops = 0
        self._degraded_since = None
        self._last_content_probe = 0.0
        self._content_probed.clear()

    # -- internals -------------------------------------------------------
    def _get_backend(self) -> RefreshBackend | None:
        if self._backend is None:
            try:
                self._backend = Win32Backend()
            except (OSError, AttributeError) as exc:  # pragma: no cover - non-Windows
                _log(f"backend unavailable: {exc}")
                return None
        return self._backend

    def wants_previous_color(self) -> bool:
        """True while the caller should hand over SAI's pre-write colour.

        That colour is the evidence used to find the stroke preview, so it is
        only worth reading until a click target has been confirmed.
        """
        if not self.enabled or self.mode != MODE_FULL:
            return False
        resolved = self._resolved
        return resolved is None or not resolved.click_verified

    def _note(self, rgb: tuple[int, int, int]) -> None:
        """Record a colour as discovery evidence (consecutive repeats merge)."""
        rgb = tuple(rgb)
        if not self._recent or self._recent[0] != rgb:
            self._recent.appendleft(rgb)

    def note_colour(self, rgb: tuple[int, int, int]) -> None:
        """Record a colour the sai-mode poll observed in SAI's slot.

        The stroke preview's cached bitmap is re-rendered by SAI itself
        whenever SAI's colour changes inside SAI (picker / eyedropper). That
        colour is a slot colour the poll reads, so feeding the reads back as
        evidence lets discovery find a preview whose cache colour was never
        written by Colorink.
        """
        self._note(rgb)

    def on_external_colour(self, rgb: tuple[int, int, int]) -> None:
        """SAI's slot colour changed *inside SAI* (not our write echo).

        SAI has just re-rendered its preview cache in this colour, so it is
        the strongest possible evidence — record it and arm an immediate
        rediscovery on the next poll tick instead of waiting out the 8 s
        backoff (later Colorink writes would push the colour out of the
        evidence window). Harmless when no preview target is missing.
        """
        rgb = tuple(rgb)
        self._note(rgb)
        self._last_previous = rgb
        # A fresh SAI render justifies one attempt even during a give-up
        # pause; consecutive mis-detections still escalate the pause.
        self._preview_give_up_until = 0.0
        self._last_resolve_attempt = float("-inf")

    def _references(
        self, previous: tuple[int, int, int] | None, offset: int = 0,
    ) -> list[tuple[int, int, int]]:
        """Colours the stroke preview could plausibly be showing right now.

        The pre-write colour always comes first (it matches the cache in a
        healthy session); the rest is a window over the evidence deque,
        rotated between discovery passes by *offset* so buried colours are
        reached eventually.
        """
        refs: list[tuple[int, int, int]] = []
        if previous is not None:
            refs.append(tuple(previous))  # type: ignore[arg-type]
        n = len(self._recent)
        if n:
            base = list(self._recent)
            for index in range(offset, offset + n):
                if len(refs) >= MAX_REFERENCES:
                    break
                rgb = base[index % n]
                if rgb not in refs:
                    refs.append(rgb)
        return refs

    def _advance_ref_rotation(self) -> None:
        """Slide the evidence window on for the next discovery pass."""
        n = len(self._recent)
        if n:
            self._ref_rot = (self._ref_rot + (MAX_REFERENCES - 1)) % n

    def _resolve(
        self,
        pid: int,
        rgb: tuple[int, int, int],
        previous: tuple[int, int, int] | None = None,
    ) -> _Resolved | None:
        """Locate SAI's colour swatch and stroke preview.

        Discovery costs a handful of offscreen renders, so a successful
        result is cached until one of its windows disappears, and a failed
        one is retried no more often than :data:`RESOLVE_RETRY_INTERVAL`.
        """
        backend = self._get_backend()
        if backend is None:
            return None

        cached = self._resolved
        if (cached is not None and cached.pid == pid
                and backend.is_window(cached.main)
                and (cached.swatch is None or backend.is_window(cached.swatch))
                and (cached.preview is None or backend.is_window(cached.preview))):
            return cached

        now = self._clock()
        if cached is None and (now - self._last_resolve_attempt) < RESOLVE_RETRY_INTERVAL:
            # A previous attempt found nothing (panel hidden, SAI busy).
            # Don't re-render every candidate on every colour change.
            return None
        self._last_resolve_attempt = now

        main = backend.main_window(pid)
        if not main:
            return None
        # A hung SAI would block PrintWindow (it is a synchronous send), so
        # skip discovery entirely rather than freezing the sync thread.
        if backend.is_hung(main):
            _log("SAI window is hung; skipping discovery")
            return None

        candidates = backend.candidates(pid)
        # The swatch paints live from the colour slot, so the colour we just
        # wrote identifies it outright.
        swatch, side = pick_swatch(
            candidates, lambda hwnd: backend.fill_ratio(hwnd, rgb),
        )
        preview = None
        if side and self.mode == MODE_FULL:
            preview = pick_preview(
                candidates, side, backend.fill_ratio,
                self._references(previous, self._ref_rot),
            )
            self._advance_ref_rotation()
        if swatch is None and preview is None:
            _log(f"no refreshable control found among {len(candidates)} candidates")
            self._resolved = None
            return None

        resolved = _Resolved(
            pid=pid, main=main, swatch=swatch, swatch_side=side, preview=preview,
        )
        _log(
            f"resolved main=0x{main:X} "
            f"swatch={'0x%X' % swatch if swatch else None} side={side} "
            f"preview={'0x%X' % preview if preview else None} "
            f"({len(candidates)} candidates)"
        )
        if preview is not None:
            self._degraded_since = None
        elif swatch is not None and self.mode == MODE_FULL and self._degraded_since is None:
            self._degraded_since = now
        self._resolved = resolved
        return resolved

    def _verify_previous_click(
        self, resolved: _Resolved, rgb: tuple[int, int, int],
    ) -> None:
        """Check that the *previous* click actually re-rendered the preview.

        Verification is deliberately one refresh late: the click is posted
        asynchronously, so SAI has not re-rendered yet when ``refresh``
        returns. By the next colour change the new sample stroke is in
        place, and a target that still shows nothing of either the probed or
        the current colour is abandoned after ``MAX_CLICK_FAILURES`` tries —
        bounding how often a mis-detected control can be clicked.
        """
        backend = self._get_backend()
        probe = resolved.probe_rgb
        if backend is None or probe is None or resolved.preview is None:
            return

        for reference in (probe, rgb):
            if backend.fill_ratio(resolved.preview, reference) >= PREVIEW_MIN_FILL:
                resolved.click_verified = True
                resolved.click_failures = 0
                resolved.probe_rgb = None
                # A verified target is healthy: give-up state belongs to
                # dropped targets only.
                self._preview_drops = 0
                self._preview_give_up_until = 0.0
                _log("preview click verified")
                return

        resolved.click_failures += 1
        resolved.probe_rgb = None
        _log(
            "preview click had no visible effect "
            f"({resolved.click_failures}/{MAX_CLICK_FAILURES})"
        )
        if resolved.click_failures >= MAX_CLICK_FAILURES:
            _log("dropping the preview target; repaint-only from now on")
            resolved.preview = None
            # Re-discovery would re-find the same mis-detected control and
            # re-click it. Back off, escalating with every consecutive drop.
            self._preview_drops += 1
            pause = min(
                PREVIEW_GIVE_UP_INITIAL * self._preview_drops, PREVIEW_GIVE_UP_MAX,
            )
            self._preview_give_up_until = self._clock() + pause
            _log(f"preview re-discovery paused for {pause:.0f}s")

    def _click_preview(
        self, resolved: _Resolved, rgb: tuple[int, int, int], probe: bool = False,
    ) -> str:
        """Post a click to the stroke preview so SAI re-renders the sample."""
        backend = self._get_backend()
        if backend is None or resolved.preview is None:
            return CLICK_UNAVAILABLE
        if not resolved.click_verified:
            if not probe:
                # Clicking an unconfirmed target is only allowed where the
                # result can be checked right away (the tick path). Otherwise a
                # mis-detected control would collect a click per colour change
                # before verification ever caught up.
                return CLICK_DEFERRED
            self._verify_previous_click(resolved, rgb)
            if resolved.preview is None:
                return CLICK_UNAVAILABLE
        if (self._clock() - self._last_write) < CLICK_SETTLE:
            # Still mid-drag: repaint the swatch now, click once it settles.
            return CLICK_DEFERRED
        if backend.input_busy(resolved.main):
            # The user is dragging / has a menu open: defer, and let the
            # caller keep the colour dirty for the trailing tick.
            _log("SAI is mid-interaction; deferring the preview click")
            return CLICK_DEFERRED
        # A whole cycle, so the preview background ends up where it started.
        if not backend.click(resolved.preview, times=CLICK_CYCLE):
            return CLICK_UNAVAILABLE
        if not resolved.click_verified:
            resolved.probe_rgb = rgb
        return CLICK_SENT

    # -- public API ------------------------------------------------------
    def refresh(
        self,
        pid: int,
        rgb: tuple[int, int, int],
        force: bool = False,
        previous: tuple[int, int, int] | None = None,
        probe: bool = False,
    ) -> bool:
        """Repaint SAI's colour widgets for the colour just written.

        *previous* is SAI's colour before the write; it identifies the stroke
        preview, whose cached bitmap still shows it. *probe* allows the slow
        work — discovery and click verification, both synchronous renders
        inside SAI — and is only set by :meth:`tick`, never by the write path.

        Returns True when at least one nudge was delivered. Never raises: a UI
        refresh must not be able to break a colour write.
        """
        if not self.enabled or not pid:
            return False

        rgb = tuple(rgb)  # type: ignore[assignment]
        self._last_previous = previous
        now = self._clock()
        if not force and (now - self._last_refresh) < self.min_interval:
            # Coalesce bursts (dragging the wheel writes ~10x/s) but keep the
            # colour so the trailing tick still lands the final value.
            self._dirty_rgb = rgb
            return False

        try:
            backend = self._get_backend()
            if backend is None:
                return False

            if not force:
                self._last_write = now

            resolved = self._resolved
            if resolved is None or resolved.pid != pid:
                # Discovery costs synchronous renders inside SAI, far too slow
                # for the write path (a colour drag writes ~10x/s). Hand it to
                # the poll-loop tick and let this write go through untouched.
                self._dirty_rgb = rgb
                self._dirty_previous = previous
                if probe:
                    resolved = self._resolve(pid, rgb, previous)
                if resolved is None:
                    return False

            delivered = False
            stale = False
            if resolved.swatch is not None:
                # Pure repaint: the swatch paints straight from the colour
                # slot, so no synthetic input is involved.
                if backend.invalidate(resolved.swatch):
                    delivered = True
                else:
                    # The panel was closed or re-docked: drop the cached
                    # handles so the next tick rediscovers them.
                    stale = True

            outcome = CLICK_UNAVAILABLE
            if self.mode == MODE_FULL and not stale:
                outcome = self._click_preview(resolved, rgb, probe=probe)
                delivered |= outcome == CLICK_SENT

            if stale:
                _log("cached control handles look stale; forcing rediscovery")
                self._resolved = None
                self._last_resolve_attempt = 0.0
                self._dirty_rgb = rgb
                self._dirty_previous = previous
                return False

            self._last_refresh = now
            if not force:
                # Only real colour writes count as activity; the tick's own
                # replay must not keep pushing the settle window out.
                self._last_write = now
            self._note(rgb)
            if outcome == CLICK_DEFERRED or resolved.needs_probe:
                # Pending work: a deferred click, or a click awaiting the
                # verification render that only the tick path performs.
                self._dirty_rgb = rgb
                self._dirty_previous = previous
            else:
                self._dirty_rgb = None
                self._dirty_previous = None
            return delivered
        except Exception as exc:  # noqa: BLE001 - never break the write path
            _log(f"refresh failed: {exc}")
            self.reset()
            return False

    def _rediscover_preview(self, pid: int) -> None:
        """Re-run stroke-preview discovery on a swatch-only resolution.

        The first discovery pass can resolve the swatch but miss the preview
        when the preview's cached bitmap shows a colour that is not in the
        evidence set yet. The evidence grows with every colour write, and SAI
        itself re-renders the preview cache whenever its colour changes
        inside SAI, so a later pass — driven by :meth:`tick` — succeeds where
        the first one had nothing to match. Never clicks anything itself: the
        normal write path handles clicks once the handle is known.
        """
        backend = self._get_backend()
        resolved = self._resolved
        if backend is None or resolved is None or resolved.pid != pid:
            return
        if (resolved.swatch is None or resolved.preview is not None
                or self.mode != MODE_FULL):
            return
        if not backend.is_window(resolved.main) or not backend.is_window(resolved.swatch):
            # Stale handles are the write path's job (it resets on a failed
            # invalidate); don't render against a dead window here.
            return
        if backend.is_hung(resolved.main):
            _log("SAI window is hung; skipping preview rediscovery")
            return

        preview = pick_preview(
            backend.candidates(pid), resolved.swatch_side,
            backend.fill_ratio,
            self._references(self._last_previous, self._ref_rot),
        )
        self._advance_ref_rotation()
        if preview is not None:
            resolved.preview = preview
            resolved.click_failures = 0
            resolved.click_verified = False
            resolved.probe_rgb = None
            self._degraded_since = None
            _log(f"preview rediscovered: 0x{preview:X}")
        else:
            if self._degraded_since is None:
                self._degraded_since = self._clock()
            _log("preview rediscovery found no matching target yet")

    def _content_probe_candidates(self, pid: int) -> None:
        """Last-resort preview identification by pixel content.

        Colour evidence has failed for a while; ask the backend which
        strip-shaped control actually *looks* like the stroke preview (a
        background holding one wide horizontal sample band). The pick still
        has to survive the normal click verification — a mis-detection is
        dropped after two failures, and the same control is never content-
        probed again in this SAI epoch.
        """
        self._last_content_probe = self._clock()
        backend = self._get_backend()
        resolved = self._resolved
        probe = getattr(backend, "content_probe", None) if backend else None
        if (probe is None or resolved is None or resolved.pid != pid
                or resolved.swatch is None or resolved.preview is not None):
            return
        strips = sorted(
            (c for c in backend.candidates(pid)
             if is_preview_strip(c, resolved.swatch_side)),
            key=lambda c: (-c.area, c.hwnd),
        )
        for cand in strips:
            if cand.hwnd in self._content_probed:
                continue
            if not probe(cand.hwnd):
                continue
            resolved.preview = cand.hwnd
            resolved.click_failures = 0
            resolved.click_verified = False
            resolved.probe_rgb = None
            self._content_probed.add(cand.hwnd)
            self._degraded_since = None
            _log(f"preview identified by content probe: 0x{cand.hwnd:X}")
            return
        _log("content probe found no preview-like strip")

    def tick(self, pid: int) -> bool:
        """Deliver refreshes the write path skipped, and do the slow work.

        Called from the sync poll loop, which is not latency critical: this is
        where control discovery and click verification run, plus the trailing
        refresh for the last colour of a drag and anything deferred while the
        user was mid-interaction. When only the swatch is known in full mode,
        this is also where preview discovery re-runs periodically, so a
        session that started with drifted evidence heals on its own; if the
        drift predates all evidence, the content classifier is the last
        resort (see :meth:`_content_probe_candidates`).
        """
        if not self.enabled or not pid:
            return False
        now = self._clock()
        delivered = False
        if (self._dirty_rgb is not None
                and (now - self._last_refresh) >= self.min_interval):
            delivered = self.refresh(
                pid, self._dirty_rgb, force=True,
                previous=self._dirty_previous, probe=True,
            )

        resolved = self._resolved
        if (self.mode == MODE_FULL and resolved is not None
                and resolved.pid == pid and resolved.swatch is not None
                and resolved.preview is None and now >= self._preview_give_up_until
                and (now - self._last_resolve_attempt) >= RESOLVE_RETRY_INTERVAL):
            self._last_resolve_attempt = now
            self._rediscover_preview(pid)
        if (self.mode == MODE_FULL and resolved is not None
                and resolved.pid == pid and resolved.swatch is not None
                and resolved.preview is None
                and self._degraded_since is not None
                and (now - self._degraded_since) >= CONTENT_PROBE_DELAY
                and (now - self._last_content_probe) >= CONTENT_SCAN_INTERVAL
                and now >= self._preview_give_up_until):
            self._content_probe_candidates(pid)
        return delivered

    def status(self) -> dict[str, object]:
        resolved = self._resolved
        return {
            "mode": self.mode,
            "swatch": f"0x{resolved.swatch:X}" if resolved and resolved.swatch else None,
            "preview": f"0x{resolved.preview:X}" if resolved and resolved.preview else None,
            "clickVerified": bool(resolved.click_verified) if resolved else False,
            "pending": self._dirty_rgb is not None,
            "knownColours": len(self._recent),
        }
