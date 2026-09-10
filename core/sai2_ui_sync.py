#!/usr/bin/env python3

"""SAI2 colour-panel UI synchronisation (wheel + track gradients) via memory.

Background — all measured against a live SAI Ver.2 (Preview.2024.08.14,
150% DPI); full evidence in ``docs/sync/sai2-color-wheel-sync-analysis.md``:

* The brush colour slot (image offset ``0x321700``, BGR) drives what SAI
  paints with.  Writing it does **not** update SAI's own colour panel.
* The colour panel is driven by a separate *picker state*: three float fields
  at image offsets ``0x3216E0`` / ``0x3216E4`` / ``0x3216E8``.  Writing them
  moves the wheel (hue ring content, inner-square content, both markers) and
  the slider *track gradients*.
* They do **not** move the slider thumbs or the numeric read-outs beside them
  (measured on a live build, 2026-09: the six tracks and the six read-outs are
  pixel-identical before and after a field write, in any order, even after a
  forced ``RedrawWindow(..., RDW_ALLCHILDREN)``).  Those two are driven by
  SAI's own widget state, which only SAI's input path updates — the read-outs
  are also never refreshed by a repaint.  Mirror-writing therefore syncs the
  wheel and the track gradients only; the thumbs/read-outs keep SAI's last
  self-set colour, which is exactly what makes them usable as a mode signal
  (see :func:`detect_mode_from_labels`) and why clicking a track remains the
  only way to move them.
* Field meaning depends on SAI's panel mode:

  =====  ================  ==========================  ==========
  mode   field 1 (+0x40)   field 2 (+0x44)             field 3
  =====  ================  ==========================  ==========
  vhsv   v/100             s/100                       h/60
  hsv    max/255           (max-min)/max               h/60
  hsl    (max+min)/510     HSL saturation              h/60
  =====  ================  ==========================  ==========

  ``vhsv`` is SAI's own conversion; colorink already implements it exactly
  (``ui.color_conversions.rgb_to_vhsv``), so no new colour maths is needed.
* Grey colours (max == min) have no hue: SAI keeps the previous hue field, so
  this module deliberately leaves field 3 untouched for grey targets.
* SAI only repaints the panel when it observes a *changed* mouse position.
  Two ``WM_MOUSEMOVE`` messages (wheel -> R track) are therefore posted.
  **No click is ever sent**, so nothing in SAI can be activated by accident.

Design rules (why this is safe to bolt onto a working app):

* ``sync()`` never raises: any failure returns ``False`` and leaves the
  already-written colour slot untouched, so the brush colour is never wrong.
* Opt-in: the caller decides whether to call it (``sai2UiSync`` config key).
* No hard-coded window handles: controls are discovered at runtime by class,
  size and vertical order, and re-discovered when they go stale.
* Whitelist writes only: the colour slot plus the three picker fields.
"""

from __future__ import annotations

import colorsys
import ctypes
import struct
import sys
import time
from ctypes import wintypes
from typing import Iterable, Sequence

from core import sai2_brush_link

# ── memory layout (offsets relative to the sai2.exe image base) ───────────
ZONE_BASE = 0x3216A0
F_V = 0x40
F_S = 0x44
F_H = 0x48
ZONE_LEN = 0xA0

# ── panel modes ───────────────────────────────────────────────────────────
MODES = ("vhsv", "hsv", "hsl")
DEFAULT_MODE = "auto"
_MODE_ALIASES = {
    "": DEFAULT_MODE,
    "auto": DEFAULT_MODE,
    "vhsv": "vhsv",
    "hsv": "hsv",
    "hsl": "hsl",
    "hls": "hsl",          # colorink spells its module "HLS"
}

MATCH_TOL = 1e-4           # tolerance when comparing two raw field values

# Mode-detection tolerances, in **8-bit colour levels** rather than raw field
# units.  The colour slot is an 8-bit rounding of SAI's own float colour, so no
# mode formula can reproduce the stored fields exactly; worse, the saturation
# field is violently sensitive near black (residuals up to 0.15 there), which
# makes any fixed field-unit threshold either blind or deaf.  Reconstructing the
# colour instead gives an error whose unit has physical meaning: the correct
# mode reproduces the slot to ≈0.5 level, a wrong mode misses by 16 levels or
# more (measured over 6000 colours, see tests/test_sai2_ui_sync.py).
MODE_TOL_LEVELS = 2.5      # memory signal (slot + picker fields)
LABEL_TOL_LEVELS = 5.0     # SAI's numeric read-outs are integers: coarser
LABEL_MARGIN_LEVELS = 3.0  # ...so only trust them when the runner-up is far off

DEBUG = False


def _log(msg: str) -> None:
    if DEBUG:
        print(f"[SAI2UiSync] {msg}", file=sys.stderr, flush=True)


# ── pure helpers (unit-tested without SAI) ────────────────────────────────
def normalize_mode(value: object) -> str:
    """Map config/env spellings onto ``auto``/``vhsv``/``hsv``/``hsl``."""
    if value is True or value is None:
        return DEFAULT_MODE
    if value is False:
        return DEFAULT_MODE
    return _MODE_ALIASES.get(str(value).strip().lower(), DEFAULT_MODE)


def is_grey(rgb: Sequence[int]) -> bool:
    """True when the colour has no hue (max == min)."""
    return max(rgb) == min(rgb)


def _vhsv_converter():
    """colorink's exact SAI2 VHSV conversion, imported lazily.

    Imported inside the function so this core module never hard-depends on
    the UI package (and so a missing PyQt install cannot break SAI sync).
    """
    try:
        from ui.color_conversions import rgb_to_vhsv  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    return rgb_to_vhsv


def _vhsv_rgb_converter():
    """The inverse (``vhsv_to_rgb``), imported lazily for the same reason."""
    try:
        from ui.color_conversions import vhsv_to_rgb  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    return vhsv_to_rgb


def fields_for(mode: str, rgb: Sequence[int]) -> tuple[float, float, float] | None:
    """Picker field values for *rgb* under *mode*; ``None`` when unavailable.

    Returns ``(field1, field2, field3)`` in the units SAI stores (0..1 for the
    first two, 0..6 for the hue sector).
    """
    r, g, b = [max(0, min(255, int(c))) / 255.0 for c in rgb]
    if mode == "hsv":
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        return v, s, h * 6.0
    if mode == "hsl":
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        return l, s, h * 6.0
    if mode == "vhsv":
        converter = _vhsv_converter()
        if converter is None:
            return None
        h, s, v = converter(int(rgb[0]), int(rgb[1]), int(rgb[2]))
        return v / 100.0, s / 100.0, h / 60.0
    return None


def rgb_from_fields(mode: str, fields: Sequence[float] | None
                    ) -> tuple[float, float, float] | None:
    """The sRGB colour (0–255 floats) SAI derives from *fields* in *mode*.

    Exact inverse of :func:`fields_for`; ``None`` for an unknown mode, missing
    fields, or when the VHSV implementation is unavailable.
    """
    if fields is None or len(fields) < 3:
        return None
    f1, f2, f3 = (float(v) for v in fields[:3])

    def _c(x: float) -> float:
        return max(0.0, min(1.0, x))

    if mode == "hsv":
        r, g, b = colorsys.hsv_to_rgb((f3 / 6.0) % 1.0, _c(f2), _c(f1))
        return r * 255.0, g * 255.0, b * 255.0
    if mode == "hsl":
        r, g, b = colorsys.hls_to_rgb((f3 / 6.0) % 1.0, _c(f1), _c(f2))
        return r * 255.0, g * 255.0, b * 255.0
    if mode == "vhsv":
        converter = _vhsv_rgb_converter()
        if converter is None:
            return None
        return tuple(converter((f3 * 60.0) % 360.0, _c(f2) * 100.0, _c(f1) * 100.0))
    return None


def detect_mode_from(slot: Sequence[int] | None, fields: Sequence[float] | None,
                     tol_levels: float = MODE_TOL_LEVELS,
                     prefer: str | None = None) -> str | None:
    """Infer the panel mode from a slot colour and the three picker fields.

    Each candidate mode *interprets* the fields and is scored by how far the
    colour it produces is from SAI's own 8-bit slot — so the residual is in
    colour levels, which is the only unit in which "this mode is wrong" means
    something.  Comparing raw field deltas instead is what made the old
    detector miss a panel that was plainly in HSV mode: the 8-bit slot is a
    rounding of SAI's float colour, worth up to 0.0034 in the hue field, while
    the old tolerance was 1e-4.

    Returns ``None`` for grey colours (hue undefined, every mode stores the
    same fields) or when no mode reproduces the slot within *tol_levels*.
    ``prefer`` keeps a previously resolved mode on a tie, so detection cannot
    flip-flop between two modes that describe the colour equally well.
    """
    if slot is None or fields is None:
        return None
    slot = tuple(float(c) for c in slot[:3])
    if len(slot) < 3 or is_grey(slot):
        return None
    scored = []
    for mode in MODES:
        back = rgb_from_fields(mode, fields)
        if back is None:
            continue
        scored.append((max(abs(a - b) for a, b in zip(back, slot)), mode))
    if not scored:
        return None
    scored.sort()
    best_err, best_mode = scored[0]
    if best_err > tol_levels:
        return None
    if prefer:
        for err, mode in scored:
            if mode == prefer and err <= best_err + 1.0:
                return mode
    return best_mode


def pack_field(value: float) -> bytes:
    """Serialise one picker field as a little-endian float32."""
    return struct.pack("<f", float(value))


# ── window discovery ──────────────────────────────────────────────────────
_user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass

_user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
_user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.GetWindowTextA.argtypes = (wintypes.HWND, ctypes.c_char_p, ctypes.c_int)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD))
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
_user32.EnumChildWindows.argtypes = (wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM)

WM_MOUSEMOVE = 0x0200
TRACK_SIZE = (239, 36)      # physical px: all six slider tracks
LABEL_SIZE = (33, 23)       # physical px: the numeric read-out beside a track
WHEEL_SIZE = (250, 250)
TRACK_ORDER = ("r", "g", "b", "h", "s", "v")


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _rect_of(hwnd: int):
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _text_of(hwnd: int) -> str:
    """Window text; SAI is an ANSI app, so titles arrive as cp936/GBK bytes."""
    buf = ctypes.create_string_buffer(256)
    _user32.GetWindowTextA(hwnd, buf, 256)
    raw = buf.value
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _all_child_windows(pid: int) -> list[int]:
    """Every descendant window of the process (top-levels plus children)."""
    tops: list[int] = []

    @_WNDENUMPROC
    def cb(hwnd, _lparam):
        if _pid_of(hwnd) == pid:
            tops.append(hwnd)
        return True

    _user32.EnumWindows(cb, 0)

    out: list[int] = []
    for top in tops:
        kids: list[int] = []

        @_WNDENUMPROC
        def kid_cb(hwnd, _lparam, _kids=kids):
            _kids.append(hwnd)
            return True

        _user32.EnumChildWindows(top, kid_cb, 0)
        out.extend(kids)
    return out


def detect_mode_by_labels(pid: int) -> str | None:
    """Tell the panel mode from SAI's own slider labels.

    The bottom row of the colour panel is labelled ``L`` in HSL mode and ``V``
    in HSV/VHSV mode.  SAI draws those labels itself, so the result is immune
    to our own picker-field writes (which would otherwise make the fields
    self-consistently match whatever mode we last wrote).

    Returns ``"hsl"``, ``"hsv_or_vhsv"`` or ``None`` when undecidable.
    """
    saw_l = saw_v = False
    for _hwnd, _rect, text in _visible_sfl_windows(pid):
        text = text.strip().upper()
        if text == "L":
            saw_l = True
        elif text == "V":
            saw_v = True
    if saw_l and not saw_v:
        return "hsl"
    if saw_v and not saw_l:
        return "hsv_or_vhsv"
    return None


def read_panel_labels(pid: int) -> tuple[str, ...] | None:
    """SAI's six numeric read-outs (R, G, B, H, S, V) as currently displayed.

    Each read-out is its own window, sitting on the same row as the track it
    belongs to, so the six are matched to the tracks by class, size and
    vertical order — no hard-coded handles.

    These numbers are written by SAI itself and are **never** refreshed by our
    picker-field mirror writes (measured: writing the fields repaints the wheel
    and the track gradients, but leaves both the thumbs and these labels
    untouched).  They therefore still describe the last colour SAI set on its
    own, which makes them the one mode signal our own writes cannot fool.
    """
    tracks: list[tuple[int, int, int, int]] = []
    labels: list[tuple[int, int, str]] = []
    for _hwnd, (left, top, right, bottom), text in _visible_sfl_windows(pid):
        size = (right - left, bottom - top)
        if size == TRACK_SIZE:
            tracks.append((top, left, right, bottom))
        elif size == LABEL_SIZE:
            labels.append((top, left, text))
    tracks.sort()
    if len(tracks) < len(TRACK_ORDER):
        return None
    out: list[str] = []
    for (top, _left, right, _bottom), _name in zip(tracks, TRACK_ORDER):
        row = [text for (ltop, lleft, text) in labels
               if abs(ltop - top) <= 6 and lleft >= right]
        if not row:
            return None
        out.append(row[0])
    return tuple(out)


def detect_mode_from_labels(labels: Sequence[str] | None,
                            tol_levels: float = LABEL_TOL_LEVELS,
                            margin: float = LABEL_MARGIN_LEVELS) -> str | None:
    """Infer the panel mode from SAI's own numeric read-outs.

    ``labels`` is ``(R, G, B, H, S, third)`` as displayed.  Interpreting the
    third read-out as V (HSV/VHSV) or as L (HSL) turns it back into picker
    fields, and the mode whose colour matches the displayed RGB is the mode the
    panel is in.  The integers are coarse, so the winner must also beat the
    runner-up by *margin* — otherwise this source says nothing rather than
    something wrong.
    """
    if labels is None or len(labels) < 6:
        return None
    try:
        r, g, b, hue, sat, third = (int(str(v).strip()) for v in labels[:6])
    except (TypeError, ValueError):
        return None
    slot = (r, g, b)
    if is_grey(slot):
        return None
    fields = (third / 100.0, sat / 100.0, hue / 60.0)
    scored = []
    for mode in MODES:
        back = rgb_from_fields(mode, fields)
        if back is None:
            continue
        scored.append((max(abs(a - b) for a, b in zip(back, slot)), mode))
    if not scored:
        return None
    scored.sort()
    best_err, best_mode = scored[0]
    if best_err > tol_levels:
        return None
    if len(scored) > 1 and scored[1][0] - best_err < margin:
        return None
    return best_mode


def _visible_sfl_windows(pid: int) -> list[tuple[int, tuple[int, int, int, int], str]]:
    """Every visible SAI child window as ``(hwnd, rect, text)``.

    SAI draws its whole UI with one window class (``sflChildWindow``), so
    controls are identified by their geometry instead of their class.
    """
    out: list[tuple[int, tuple[int, int, int, int], str]] = []
    for hwnd in _all_child_windows(pid):
        if _class_of(hwnd).lower() != "sflchildwindow":
            continue
        if not _user32.IsWindowVisible(hwnd):
            continue
        left, top, right, bottom = _rect_of(hwnd)
        out.append((hwnd, (left, top, right, bottom), _text_of(hwnd)))
    return out


def discover_controls(pid: int) -> dict:
    """Find the wheel and the six slider tracks of the colour panel.

    Located purely by class + size + vertical order so that window moves,
    DPI changes and SAI restarts need no hard-coded handles.
    """
    tracks: list[tuple[int, int, int]] = []
    wheel = None
    for hwnd, (left, top, right, bottom), _text in _visible_sfl_windows(pid):
        size = (right - left, bottom - top)
        if size == TRACK_SIZE:
            tracks.append((top, left, hwnd))
        elif size == WHEEL_SIZE and wheel is None:
            wheel = hwnd
    tracks.sort()
    out = {"wheel": wheel, "tracks": {}, "origins": {}}
    for (top_y, left, hwnd), name in zip(tracks, TRACK_ORDER):
        out["tracks"][name] = hwnd
        out["origins"][name] = (left, top_y)
    return out


# ── the synchroniser ──────────────────────────────────────────────────────
class SAI2UiSync:
    """Mirror-write SAI2's picker fields so its colour panel follows colorink.

    The class is deliberately forgiving: :meth:`sync` swallows every error and
    reports ``False`` so a failure can never affect the brush colour itself.
    """

    def __init__(self, mode: object = None) -> None:
        self._mode = normalize_mode(mode)
        self._resolved: str | None = None      # last successfully detected mode
        self._controls: dict | None = None
        self._controls_pid: int | None = None
        self._fail_streak = 0
        self.max_fail_streak = 5               # give up quietly after this many
        self._last_observe = 0.0
        # Fields we last wrote; SAI rewrites them whenever the user switches
        # the panel mode, which is how a live mode change is noticed.
        self._last_written: tuple[float, float, float] | None = None
        # Re-checking SAI's labels walks the whole window tree; once the mode
        # is known there is no need to do that on every colour change.
        self.observe_interval = 2.0            # seconds

    # -- configuration ----------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: object) -> bool:
        """Set the panel mode; returns True when the value changed."""
        normalized = normalize_mode(mode)
        changed = normalized != self._mode
        self._mode = normalized
        if changed:
            self._resolved = None
        return changed

    @property
    def fail_streak(self) -> int:
        return self._fail_streak

    # -- public API -------------------------------------------------------
    def sync(self, sync, rgb: Iterable[int]) -> bool:
        """Make SAI's colour panel show *rgb*.

        ``sync`` is a connected :class:`core.sai2_brush_link.SAI2Sync`; the
        colour slot is assumed to be written already (that is the caller's
        job and stays the authoritative brush colour).  Returns True when the
        picker fields were written and a repaint was requested.
        """
        try:
            rgb = tuple(int(c) for c in rgb)[:3]
            if len(rgb) != 3:
                return False
            if self._fail_streak >= self.max_fail_streak:
                return False
            mode = self._resolve_mode(sync)
            if mode is None:
                return self._fail("no panel mode")
            fields = fields_for(mode, rgb)
            if fields is None:
                return self._fail("mode %s unavailable" % mode)
            controls = self._get_controls(sync)
            if not controls or not controls.get("tracks"):
                return self._fail("colour panel controls not found")
            if not self._write_fields(sync, fields, grey=is_grey(rgb)):
                return self._fail("field write failed")
            self._last_written = tuple(fields)
            self._trigger_repaint(controls)
            self._fail_streak = 0
            return True
        except Exception as exc:  # noqa: BLE001 - must never propagate
            return self._fail("exception: %r" % (exc,))

    def reset(self) -> None:
        """Forget cached mode/controls (e.g. after a SAI restart)."""
        self._resolved = None
        self._controls = None
        self._controls_pid = None
        self._fail_streak = 0

    # -- internals --------------------------------------------------------
    def _fail(self, why: str) -> bool:
        self._fail_streak += 1
        _log("skip: %s (streak %d)" % (why, self._fail_streak))
        return False

    def observe(self, sync) -> str | None:
        """Refresh the cached panel mode from SAI's own state.

        Called by the writer **before** it touches the colour slot.  Three
        independent sources are used, most trustworthy first:

        1. SAI's slider labels — ``L`` means HSL, ``V`` means HSV or VHSV.
           They are drawn by SAI and cannot be fooled by our field writes.
        2. SAI's slot colour interpreted by each candidate mode's own
           conversion; the mode that reproduces the slot is the panel's mode.
           Valid while the fields are still SAI's own, which is why
           :meth:`_fields_changed_externally` watches for SAI rewriting them.
        3. SAI's numeric read-outs (R, G, B, H, S, V), which SAI never
           refreshes from our writes — so they still describe the last colour
           SAI set itself and can settle the mode when the slot has gone grey
           (grey has no hue, so source 2 has nothing to compare).
        """
        if self._mode in MODES:
            return self._mode
        now = time.time()
        if (self._resolved is not None
                and now - self._last_observe < self.observe_interval):
            # SAI rewrites the picker fields whenever the user switches its
            # panel mode; if they no longer match what we last wrote, the mode
            # changed and must be re-detected immediately (no throttle).
            if not self._fields_changed_externally(sync):
                return self._resolved
        self._last_observe = now
        pid = getattr(sync, "_pid", None)
        try:
            label = detect_mode_by_labels(pid) if pid else None
        except Exception:  # noqa: BLE001 - observation must never break a write
            label = None
        if label == "hsl":
            if self._resolved != "hsl":
                _log("panel mode observed from labels: hsl")
            self._resolved = "hsl"
            return "hsl"
        try:
            slot = self._read_slot(sync)
            fields = self._read_fields(sync)
            detected = (detect_mode_from(slot, fields, prefer=self._resolved)
                        if slot and fields else None)
        except Exception:  # noqa: BLE001
            detected = None
        if detected is None and pid:
            # The memory picture is inconclusive: SAI's own read-outs may
            # still know, because they hold whatever colour SAI set last.
            try:
                detected = detect_mode_from_labels(read_panel_labels(pid))
            except Exception:  # noqa: BLE001
                detected = None
        # The slider labels are drawn by SAI, so they outrank anything inferred:
        # a "V" row means the mode is HSV or VHSV whatever the maths says.
        if detected is not None and (label is None
                                     or (label == "hsv_or_vhsv") == (detected != "hsl")):
            if self._resolved != detected:
                _log("panel mode observed: %s" % detected)
            self._resolved = detected
        return self._resolved

    def _fields_changed_externally(self, sync) -> bool:
        """True when SAI rewrote the picker fields since our last write.

        A live panel-mode switch makes SAI recompute all three fields, so this
        is the cheapest reliable signal that the cached mode is stale. Only
        the first two fields are compared: grey targets deliberately keep the
        hue field, which would otherwise look like an external change.
        """
        if self._last_written is None:
            # Nothing written yet: there is no baseline to compare against, so
            # this is not evidence of a mode switch.
            return False
        try:
            current = self._read_fields(sync)
        except Exception:  # noqa: BLE001
            return False
        if current is None:
            return False
        return (abs(current[0] - self._last_written[0]) > MATCH_TOL
                or abs(current[1] - self._last_written[1]) > MATCH_TOL)

    def _resolve_mode(self, sync) -> str | None:
        if self._mode in MODES:
            return self._mode
        if self._resolved is not None:
            return self._resolved
        # Nothing observed yet (SAI started on a grey colour): all three modes
        # produce identical fields for grey, so any mode is safe until a
        # coloured slot lets :meth:`observe` pin the real one.
        _log("panel mode unknown; assuming vhsv for this write")
        return "vhsv"

    def _get_controls(self, sync) -> dict | None:
        pid = getattr(sync, "_pid", None)
        if pid is None:
            return None
        if self._controls is not None and self._controls_pid == pid:
            return self._controls
        self._controls = discover_controls(pid)
        self._controls_pid = pid
        _log("controls: wheel=%s tracks=%s"
             % (self._controls.get("wheel"),
                sorted(self._controls["tracks"])))
        return self._controls

    @staticmethod
    def _read_slot(sync) -> tuple[int, int, int] | None:
        data = sai2_brush_link._read_memory(sync._handle, sync._color_addr, 3)
        if not data or len(data) != 3:
            return None
        return data[2], data[1], data[0]

    @staticmethod
    def _read_fields(sync) -> tuple[float, float, float] | None:
        data = sai2_brush_link._read_memory(sync._handle,
                                            sync._base + ZONE_BASE, ZONE_LEN)
        if not data or len(data) < F_H + 4:
            return None
        return (struct.unpack_from("<f", data, F_V)[0],
                struct.unpack_from("<f", data, F_S)[0],
                struct.unpack_from("<f", data, F_H)[0])

    @staticmethod
    def _write_fields(sync, fields: Sequence[float], grey: bool) -> bool:
        """Write the picker fields; grey targets keep SAI's previous hue."""
        ok = sai2_brush_link._write_memory(
            sync._handle, sync._base + ZONE_BASE + F_V, pack_field(fields[0]))
        ok = sai2_brush_link._write_memory(
            sync._handle, sync._base + ZONE_BASE + F_S, pack_field(fields[1])) and ok
        if not grey:
            ok = sai2_brush_link._write_memory(
                sync._handle, sync._base + ZONE_BASE + F_H,
                pack_field(fields[2])) and ok
        return ok

    @staticmethod
    def _trigger_repaint(controls: dict) -> None:
        """Two cross-control moves: SAI repaints only on a *changed* position."""
        wheel = controls.get("wheel")
        track = controls["tracks"].get("r")
        if wheel:
            _user32.PostMessageW(wheel, WM_MOUSEMOVE, 0, ((100 << 16) | 100))
            time.sleep(0.05)
        if track:
            _user32.PostMessageW(track, WM_MOUSEMOVE, 0, ((18 << 16) | 100))
