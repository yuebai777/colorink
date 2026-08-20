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
  background lands back on the style it started from. Because this half is
  still synthetic input into another process, it stays opt-in
  (``MODE_FULL``).

The colour panel's wheel, slider knobs and numeric labels are driven by
SAI's own picker state (a separate structure), so no repaint can move them;
they are intentionally left alone.

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
# Repaint is the default: it is purely a redraw request. The click that the
# stroke preview needs is real input as far as SAI is concerned, and SAI
# remembers the button-down point — the next real stroke can then start with a
# wedge sweeping from it. That trade-off is the user's to opt into.
DEFAULT_MODE = MODE_REPAINT

_MODE_ALIASES = {
    "": MODE_REPAINT,
    "auto": MODE_REPAINT,
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
# best-first and stops early under a hard probe budget.
MAX_PROBES = 6

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
    class, aspect 3.71 vs 3.78. Clicking the tool row switches the user's
    brush tool, so geometry only narrows the field and the decision is made
    on rendered content: the preview holds a sample stroke in the colour SAI
    last drew it with, and *references* are the colours we know about (the
    slot colour before the write, plus colours written earlier this session).
    A candidate showing none of them is never clicked.
    """
    strips = sorted(
        (c for c in candidates if is_preview_strip(c, swatch_side)),
        key=lambda c: (-c.area, c.hwnd),
    )
    if not strips or not references:
        return None

    probes = 0
    for cand in strips:
        for ref in references:
            if probes >= max_probes:
                _log(f"preview probe budget exhausted after {probes} renders")
                return None
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

    def fill_ratio(self, hwnd: int, rgb: tuple[int, int, int], tolerance: int = 6) -> float:
        """Fraction of the control's own rendering painted in *rgb*.

        Renders offscreen with ``PrintWindow``, so it reports what SAI would
        paint right now — independent of what is currently on screen and of
        whether the window is covered by another one.
        """
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0.0
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0 or width * height > self.MAX_CANDIDATE_AREA:
            return 0.0

        hdc_screen = self._user32.GetDC(0)
        if not hdc_screen:
            return 0.0
        hdc_mem = hbmp = None
        try:
            hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = self._gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hdc_mem or not hbmp:
                return 0.0
            self._gdi32.SelectObject(hdc_mem, hbmp)
            if not self._user32.PrintWindow(hwnd, hdc_mem, self.PW_RENDERFULLCONTENT):
                return 0.0

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
                return 0.0
            return _count_matches(buf.raw, rgb, tolerance) / float(width * height)
        finally:
            if hbmp:
                self._gdi32.DeleteObject(hbmp)
            if hdc_mem:
                self._gdi32.DeleteDC(hdc_mem)
            self._user32.ReleaseDC(0, hdc_screen)

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
RECENT_COLOURS = 8             # how many written colours stay usable as evidence
MAX_REFERENCES = 4             # colours probed per discovery pass (each = a render)

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
        # Colours this session wrote, newest first: the stroke preview's
        # cached bitmap shows one of them, which is how it gets identified.
        self._recent: deque[tuple[int, int, int]] = deque(maxlen=RECENT_COLOURS)

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

    def _references(
        self, previous: tuple[int, int, int] | None,
    ) -> list[tuple[int, int, int]]:
        """Colours the stroke preview could plausibly be showing right now."""
        refs: list[tuple[int, int, int]] = []
        if previous is not None:
            refs.append(tuple(previous))  # type: ignore[arg-type]
        for rgb in self._recent:
            if len(refs) >= MAX_REFERENCES:
                break
            if rgb not in refs:
                refs.append(rgb)
        return refs

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
                candidates, side, backend.fill_ratio, self._references(previous),
            )
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
            if rgb not in self._recent:
                self._recent.appendleft(rgb)
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

    def tick(self, pid: int) -> bool:
        """Deliver refreshes the write path skipped, and do the slow work.

        Called from the sync poll loop, which is not latency critical: this is
        where control discovery and click verification run, plus the trailing
        refresh for the last colour of a drag and anything deferred while the
        user was mid-interaction.
        """
        if not self.enabled or self._dirty_rgb is None:
            return False
        now = self._clock()
        if (now - self._last_refresh) < self.min_interval:
            return False
        return self.refresh(
            pid, self._dirty_rgb, force=True,
            previous=self._dirty_previous, probe=True,
        )

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
