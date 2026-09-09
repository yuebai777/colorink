#!/usr/bin/env python3

"""SAI2 colour-panel UI synchronisation (wheel, marker, sliders) via memory.

Background — all measured against a live SAI Ver.2 (Preview.2024.08.14,
150% DPI); full evidence in ``docs/sync/sai2-color-wheel-sync-analysis.md``:

* The brush colour slot (image offset ``0x321700``, BGR) drives what SAI
  paints with.  Writing it does **not** update SAI's own colour panel.
* The colour panel is driven by a separate *picker state*: three float fields
  at image offsets ``0x3216E0`` / ``0x3216E4`` / ``0x3216E8``.  Writing them
  moves the wheel marker, the slider thumbs and (on the next repaint) the
  numeric labels — verified pixel-for-pixel identical to clicking the colour
  in SAI (0 px difference on the wheel region).
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

MATCH_TOL = 1e-4           # mode detection tolerance on the three fields

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


def detect_mode_from(slot: Sequence[int] | None, fields: Sequence[float] | None,
                     tol: float = MATCH_TOL) -> str | None:
    """Infer the panel mode from a slot colour and the three fields.

    Returns ``None`` for grey colours (hue undefined, all modes look alike)
    or when nothing matches within *tol*.
    """
    if slot is None or fields is None or len(fields) < 3:
        return None
    if is_grey(slot):
        return None
    best_err = None
    best_mode = None
    for mode in MODES:
        calc = fields_for(mode, slot)
        if calc is None:
            continue
        err = max(abs(a - b) for a, b in zip(fields, calc))
        if best_err is None or err < best_err:
            best_err, best_mode = err, mode
    if best_mode is None or best_err is None or best_err > tol:
        return None
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
    for hwnd in _all_child_windows(pid):
        if _class_of(hwnd).lower() != "sflchildwindow":
            continue
        if not _user32.IsWindowVisible(hwnd):
            continue
        text = _text_of(hwnd).strip().upper()
        if text == "L":
            saw_l = True
        elif text == "V":
            saw_v = True
    if saw_l and not saw_v:
        return "hsl"
    if saw_v and not saw_l:
        return "hsv_or_vhsv"
    return None


def discover_controls(pid: int) -> dict:
    """Find the wheel and the six slider tracks of the colour panel.

    Located purely by class + size + vertical order so that window moves,
    DPI changes and SAI restarts need no hard-coded handles.
    """
    tops: list[int] = []

    @_WNDENUMPROC
    def cb(hwnd, _lparam):
        if _pid_of(hwnd) == pid:
            tops.append(hwnd)
        return True

    _user32.EnumWindows(cb, 0)

    tracks: list[tuple[int, int, int]] = []
    wheel = None
    for top in tops:
        kids: list[int] = []

        @_WNDENUMPROC
        def kid_cb(hwnd, _lparam, _kids=kids):
            _kids.append(hwnd)
            return True

        _user32.EnumChildWindows(top, kid_cb, 0)
        for h in kids:
            if _class_of(h).lower() != "sflchildwindow":
                continue
            if not _user32.IsWindowVisible(h):
                continue
            left, top_y, right, bottom = _rect_of(h)
            size = (right - left, bottom - top_y)
            if size == TRACK_SIZE:
                tracks.append((top_y, left, h))
            elif size == WHEEL_SIZE and wheel is None:
                wheel = h
    tracks.sort()
    out = {"wheel": wheel, "tracks": {}, "origins": {}}
    for (top_y, left, h), name in zip(tracks, TRACK_ORDER):
        out["tracks"][name] = h
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

        Called by the writer **before** it touches the colour slot.  Two
        independent sources are used, most trustworthy first:

        1. SAI's slider labels — ``L`` means HSL, ``V`` means HSV or VHSV.
           These are drawn by SAI and cannot be fooled by our field writes.
        2. The picker fields vs the current slot colour, which separates HSV
           from VHSV — but only while those fields are still SAI's own, hence
           the label check above comes first.
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
            detected = detect_mode_from(slot, fields) if slot and fields else None
        except Exception:  # noqa: BLE001
            detected = None
        # Only HSV/VHSV can be confirmed from the fields when the labels say V;
        # an "hsl" verdict here would just be our own previous write echoing.
        if detected in ("hsv", "vhsv") and label != "hsl":
            if self._resolved != detected:
                _log("panel mode observed from fields: %s" % detected)
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
