#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Color-space math and on-disk layout for the host brush-color struct.

CSP and UDM both expose the active brush color as one contiguous struct
that simultaneously holds the same color expressed in four spaces
(RGB / CMYK / HSV / HLS).  Each channel is persisted as a 32-bit unsigned
integer proportional to that channel's natural maximum, so any given
color resolves to a deterministic byte pattern regardless of which space
the host last wrote through.

This module is the single source of truth for:

* the fixed per-channel offsets inside that struct,
* the scaling helpers that translate between human-range values
  (e.g. ``R=200``, ``H=270``) and the packed u32 form,
* the bidirectional conversions between spaces, and
* the "which space currently holds a real color?" lookup that the
  sync backends use to recover an RGB triple from a raw memory snapshot.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Sequence, Tuple

# ---------------------------------------------------------------------------
# Fixed struct layout
# ---------------------------------------------------------------------------
# Offsets are relative to the start of the color slot and are dictated by
# the host applications; we mirror them, we do not choose them.
_RGB_OFFS: Tuple[int, ...] = (0x00, 0x04, 0x08)
_CMYK_OFFS: Tuple[int, ...] = (0x0C, 0x10, 0x14, 0x18)
_HSV_OFFS: Tuple[int, ...] = (0x1C, 0x20, 0x24)
_HLS_OFFS: Tuple[int, ...] = (0x28, 0x2C, 0x30)

_RGB_MAX: Tuple[float, ...] = (255.0, 255.0, 255.0)
_CMYK_MAX: Tuple[float, ...] = (100.0, 100.0, 100.0, 100.0)
_HSV_MAX: Tuple[float, ...] = (360.0, 100.0, 100.0)
_HLS_MAX: Tuple[float, ...] = (360.0, 100.0, 100.0)

# Iteration order used when scanning snapshots for a "live" space.
SPACE_ORDER: Tuple[str, ...] = ("rgb", "cmyk", "hsv", "hls")

# A 32-bit slot can represent values in [0, 2**32 - 1].
_U32_LIMIT = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Gray-component-replacement curve for CMYK synthesis
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _GcrCurve:
    """Tunables that shape how aggressively black ink is substituted in CMYK.

    The host applications run a similar GCR pass when writing CMYK; we
    mirror it so the K channel we compute matches what the host would
    have stored for the same RGB.
    """

    lightness_kick_in: float = 65.0    # L* above which no K is generated
    lightness_saturated: float = 35.0  # L* at which K generation saturates
    lightness_exponent: float = 1.2
    chroma_suppress_ref: float = 80.0  # chroma above which K is fully suppressed
    saturation_exponent: float = 2.0
    total_ink_cap: float = 3.0         # max c+m+y+k after normalization


_GCR = _GcrCurve()


# ---------------------------------------------------------------------------
# Internal clipping helpers
# ---------------------------------------------------------------------------
def _clip_int(value: float, low: int, high: int) -> int:
    """Round to int and clip into the inclusive range [low, high]."""
    if value <= low:
        return low
    if value >= high:
        return high
    return int(round(value))


def _clip_float(value: float, ceiling: float) -> float:
    """Clip a float into the closed range [0.0, ceiling]."""
    ceiling = float(ceiling)
    if value <= 0.0:
        return 0.0
    if value >= ceiling:
        return ceiling
    return float(value)


def _byte(value: float) -> int:
    return _clip_int(value, 0, 255)


def _percent(value: float) -> int:
    return _clip_int(value, 0, 100)


def _hue(value: float) -> int:
    return _clip_int(value, 0, 360)


def normalize_hue_for_colorsys(h: int) -> float:
    """Map an integer hue in [0, 360] onto the [0.0, 1.0) range colorsys wants.

    colorsys treats 1.0 as identical to 0.0 (full hue wrap), so 360 must
    fold to 0.0 to avoid a wasted iteration.
    """
    h_int = _hue(h)
    return 0.0 if h_int >= 360 else h_int / 360.0


# ---------------------------------------------------------------------------
# u32 scaling
# ---------------------------------------------------------------------------
def encode_scaled_u32(value: float, max_value: float) -> int:
    """Pack a human-range value into a u32 proportional to ``max_value``."""
    if max_value <= 0:
        return 0
    ratio = _clip_float(value, max_value) / float(max_value)
    packed = int(round(ratio * _U32_LIMIT))
    if packed < 0:
        return 0
    if packed > _U32_LIMIT:
        return _U32_LIMIT
    return packed


def decode_scaled_u32(raw: int, max_value: float) -> int:
    """Reverse of :func:`encode_scaled_u32`: u32 -> human-range value."""
    if max_value <= 0:
        return 0
    normalized = (int(raw) & _U32_LIMIT) / _U32_LIMIT
    return int(round(normalized * float(max_value)))


# ---------------------------------------------------------------------------
# Color-space descriptor
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ColorSpaceSpec:
    """Static description of one slot inside the color struct.

    Each concrete space (RGB / CMYK / HSV / HLS) is represented by one
    instance of this class.  Methods on the instance replace what would
    otherwise be a ``SPACE_SPECS[name]`` dict lookup followed by a
    space-dispatch free function.
    """

    name: str
    channels: Tuple[str, ...]
    maxima: Tuple[float, ...]
    relative_offsets: Tuple[int, ...]

    def channel_offsets(self, anchor: int) -> Tuple[int, ...]:
        """Per-channel offsets anchored at ``anchor`` (an offset from struct base)."""
        base = int(anchor)
        return tuple(base + off for off in self.relative_offsets)

    def channel_addresses(self, anchor: int) -> Tuple[int, ...]:
        """Per-channel absolute addresses anchored at ``anchor`` (a base address)."""
        base = int(anchor)
        return tuple(base + off for off in self.relative_offsets)

    def decode(self, raws: Sequence[int]) -> Dict[str, int]:
        return {
            ch: decode_scaled_u32(raw, mx)
            for ch, raw, mx in zip(self.channels, raws, self.maxima)
        }

    def encode(self, values: Mapping[str, int]) -> Tuple[int, ...]:
        return tuple(
            encode_scaled_u32(values[ch], mx)
            for ch, mx in zip(self.channels, self.maxima)
        )

    def render(self, values: Mapping[str, int]) -> str:
        return ", ".join(
            f"{ch.upper()}={int(values[ch])}" for ch in self.channels
        )


_RGB = ColorSpaceSpec("rgb",  ("r", "g", "b"),       _RGB_MAX,  _RGB_OFFS)
_CMYK = ColorSpaceSpec("cmyk", ("c", "m", "y", "k"),  _CMYK_MAX, _CMYK_OFFS)
_HSV = ColorSpaceSpec("hsv",  ("h", "s", "v"),       _HSV_MAX,  _HSV_OFFS)
_HLS = ColorSpaceSpec("hls",  ("h", "l", "s"),       _HLS_MAX,  _HLS_OFFS)

_REGISTRY: Dict[str, ColorSpaceSpec] = {
    spec.name: spec for spec in (_RGB, _CMYK, _HSV, _HLS)
}


def _lookup(name: str) -> ColorSpaceSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown color space: {name!r}") from None


# ---------------------------------------------------------------------------
# Public offset / address / codec entry points (kept as module functions
# so external importers do not have to know about the descriptor class).
# ---------------------------------------------------------------------------
def build_space_offsets(rgb_base_offset: int) -> Dict[str, Tuple[int, ...]]:
    """Per-space per-channel offsets anchored at ``rgb_base_offset``."""
    return {name: spec.channel_offsets(rgb_base_offset) for name, spec in _REGISTRY.items()}


def build_space_addresses(rgb_base_address: int) -> Dict[str, Tuple[int, ...]]:
    """Per-space per-channel absolute addresses anchored at ``rgb_base_address``."""
    return {name: spec.channel_addresses(rgb_base_address) for name, spec in _REGISTRY.items()}


def decode_space_raws(space_name: str, raws: Sequence[int]) -> Dict[str, int]:
    return _lookup(space_name).decode(raws)


def encode_space_values(space_name: str, values: Mapping[str, int]) -> Tuple[int, ...]:
    return _lookup(space_name).encode(values)


def format_space_values(space_name: str, values: Mapping[str, int]) -> str:
    return _lookup(space_name).render(values)


# ---------------------------------------------------------------------------
# Raw-value inspection
# ---------------------------------------------------------------------------
def space_has_nonzero_raws(raws: Sequence[int]) -> bool:
    """True if any of the u32 raws is non-zero after masking to 32 bits."""
    return any((int(raw) & _U32_LIMIT) != 0 for raw in raws)


def any_space_has_nonzero_raws(
    snapshots: Mapping[str, Mapping[str, object]]
) -> bool:
    """True if any per-space snapshot in ``snapshots`` carries non-zero raws.

    Used by the sync backends to decide whether the resolved color-slot
    pointer is pointing at real data or at a freshly-zeroed buffer (e.g.
    while the color wheel is being dragged in CSP).
    """
    for snapshot in snapshots.values():
        raws = snapshot.get("raws") or ()
        if space_has_nonzero_raws(raws):
            return True
    return False


# ---------------------------------------------------------------------------
# Direct space conversions
# ---------------------------------------------------------------------------
def rgb_to_hsv_values(rgb: Mapping[str, int]) -> Dict[str, int]:
    r = _byte(rgb["r"]) / 255.0
    g = _byte(rgb["g"]) / 255.0
    b = _byte(rgb["b"]) / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return {"h": _hue(h * 360.0), "s": _percent(s * 100.0), "v": _percent(v * 100.0)}


def hsv_to_rgb_values(values: Mapping[str, int]) -> Dict[str, int]:
    r, g, b = colorsys.hsv_to_rgb(
        normalize_hue_for_colorsys(values["h"]),
        _percent(values["s"]) / 100.0,
        _percent(values["v"]) / 100.0,
    )
    return {"r": _byte(r * 255.0), "g": _byte(g * 255.0), "b": _byte(b * 255.0)}


def rgb_to_hls_values(rgb: Mapping[str, int]) -> Dict[str, int]:
    r = _byte(rgb["r"]) / 255.0
    g = _byte(rgb["g"]) / 255.0
    b = _byte(rgb["b"]) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return {"h": _hue(h * 360.0), "l": _percent(l * 100.0), "s": _percent(s * 100.0)}


def hls_to_rgb_values(values: Mapping[str, int]) -> Dict[str, int]:
    r, g, b = colorsys.hls_to_rgb(
        normalize_hue_for_colorsys(values["h"]),
        _percent(values["l"]) / 100.0,
        _percent(values["s"]) / 100.0,
    )
    return {"r": _byte(r * 255.0), "g": _byte(g * 255.0), "b": _byte(b * 255.0)}


# --- CIE L*a*b* path (drives the CMYK GCR curve) -------------------------
def _srgb_to_linear(c: float) -> float:
    """Inverse sRGB gamma; the canonical piecewise curve from IEC 61966-2-1."""
    if c > 0.04045:
        return math.pow((c + 0.055) / 1.055, 2.4)
    return c / 12.92


# D65 reference white in 2-degree observer (X=95.047, Y=100, Z=108.883);
# CSP stores Lab in D50, so we Bradford-adapt D65 -> D50 below.
_XYZ_D65_TO_D50 = (
    (1.0478112,  0.0228866, -0.0501270),
    (0.0295424,  0.9904844, -0.0170491),
    (-0.0092345, 0.0150436,  0.7521316),
)

# Linear-RGB -> D65 XYZ, sRGB IEC 61966-2-1 primaries.
_LINEAR_TO_XYZ_D65 = (
    (0.4124564390896922, 0.357576077643909,  0.18043748326639894),
    (0.21267285140562253, 0.715152155287818, 0.07217499330655958),
    (0.019330818715591851, 0.11919477979462598, 0.9505321522496607),
)


def _mat3_vec3(m, v):
    """3x3 matrix times 3-vector; inlined to avoid a numpy dependency."""
    a, b, c = v
    return (
        m[0][0] * a + m[0][1] * b + m[0][2] * c,
        m[1][0] * a + m[1][1] * b + m[1][2] * c,
        m[2][0] * a + m[2][1] * b + m[2][2] * c,
    )


def _linear_rgb_to_xyz_d65(r: float, g: float, b: float) -> Tuple[float, float, float]:
    x, y, z = _mat3_vec3(_LINEAR_TO_XYZ_D65, (r, g, b))
    return x * 100.0, y * 100.0, z * 100.0


def _xyz_d65_to_d50(x: float, y: float, z: float) -> Tuple[float, float, float]:
    return _mat3_vec3(_XYZ_D65_TO_D50, (x, y, z))


def _lab_nonlinear(t: float) -> float:
    """The CIE L*a*b* forward nonlinearity."""
    delta = 6.0 / 29.0
    threshold = delta ** 3
    if t > threshold:
        return math.pow(t, 1.0 / 3.0)
    return t / (3.0 * delta * delta) + 4.0 / 29.0


# D50 reference white used by the L*a*b* forward transform.
_D50_X, _D50_Y, _D50_Z = 96.422, 100.0, 82.521


def _xyz_d50_to_lab(x: float, y: float, z: float) -> Tuple[float, float, float]:
    fx = _lab_nonlinear(x / _D50_X)
    fy = _lab_nonlinear(y / _D50_Y)
    fz = _lab_nonlinear(z / _D50_Z)
    L_star = 116.0 * fy - 16.0
    a_star = 500.0 * (fx - fy)
    b_star = 200.0 * (fy - fz)
    # Two-decimal rounding matches what CSP persists on disk.
    return (
        max(0.0, min(100.0, round(L_star * 100.0) / 100.0)),
        max(-128.0, min(127.0, round(a_star * 100.0) / 100.0)),
        max(-128.0, min(127.0, round(b_star * 100.0) / 100.0)),
    )


def rgb_to_lab_values(rgb: Mapping[str, int]) -> Dict[str, float]:
    r = _srgb_to_linear(_byte(rgb["r"]) / 255.0)
    g = _srgb_to_linear(_byte(rgb["g"]) / 255.0)
    b = _srgb_to_linear(_byte(rgb["b"]) / 255.0)
    x65, y65, z65 = _linear_rgb_to_xyz_d65(r, g, b)
    x50, y50, z50 = _xyz_d65_to_d50(x65, y65, z65)
    L, a, b_lab = _xyz_d50_to_lab(x50, y50, z50)
    return {"l": L, "a": a, "b": b_lab}


# --- CMYK synthesis with gray-component replacement ---------------------
def _gcr_k_fraction(rgb: Mapping[str, int]) -> float:
    """Compute the K fraction (0..1) for an RGB color under the GCR curve."""
    lab = rgb_to_lab_values(rgb)
    chroma = math.sqrt(lab["a"] * lab["a"] + lab["b"] * lab["b"])
    saturation = min(1.0, max(0.0, rgb_to_hsv_values(rgb)["s"] / 100.0))

    # Lightness leg: only push toward K as L* drops below the kick-in point.
    lightness_factor = 0.0
    if lab["l"] < _GCR.lightness_kick_in:
        span = max(1.0, _GCR.lightness_kick_in - _GCR.lightness_saturated)
        t = min(1.0, max(0.0, (_GCR.lightness_kick_in - lab["l"]) / span))
        lightness_factor = math.pow(t, _GCR.lightness_exponent)

    chroma_suppress = min(1.0, max(0.0, 1.0 - chroma / _GCR.chroma_suppress_ref))
    saturation_suppress = math.pow(1.0 - saturation, _GCR.saturation_exponent)
    return min(1.0, max(0.0, lightness_factor * max(chroma_suppress, saturation_suppress)))


def rgb_to_cmyk_values(rgb: Mapping[str, int]) -> Dict[str, int]:
    r = _byte(rgb["r"])
    g = _byte(rgb["g"])
    b = _byte(rgb["b"])
    # Pure CMY (no K) would be these complements.
    c_pure = 1.0 - r / 255.0
    m_pure = 1.0 - g / 255.0
    y_pure = 1.0 - b / 255.0
    neutral = min(c_pure, m_pure, y_pure)
    k = neutral * _gcr_k_fraction({"r": r, "g": g, "b": b})
    c = max(0.0, c_pure - k)
    m = max(0.0, m_pure - k)
    y = max(0.0, y_pure - k)

    # Total ink coverage cap: if c+m+y+k exceeds the cap, scale the
    # chromatic channels down to fit while K is preserved.
    total = c + m + y + k
    if total > _GCR.total_ink_cap:
        chroma_sum = max(1e-6, c + m + y)
        scale = (_GCR.total_ink_cap - k) / chroma_sum
        c *= scale
        m *= scale
        y *= scale

    return {
        "c": _percent(c * 100.0),
        "m": _percent(m * 100.0),
        "y": _percent(y * 100.0),
        "k": _percent(k * 100.0),
    }


def cmyk_to_rgb_values(values: Mapping[str, int]) -> Dict[str, int]:
    c = _percent(values["c"]) / 100.0
    m = _percent(values["m"]) / 100.0
    y = _percent(values["y"]) / 100.0
    k = _percent(values["k"]) / 100.0
    return {
        "r": _byte((1.0 - c) * (1.0 - k) * 255.0),
        "g": _byte((1.0 - m) * (1.0 - k) * 255.0),
        "b": _byte((1.0 - y) * (1.0 - k) * 255.0),
    }


# ---------------------------------------------------------------------------
# RGB <-> any-space dispatch tables
# ---------------------------------------------------------------------------
_RGB_TO_SPACE: Dict[str, Callable[[Mapping[str, int]], Dict[str, int]]] = {
    "rgb": lambda rgb: {"r": _byte(rgb["r"]), "g": _byte(rgb["g"]), "b": _byte(rgb["b"])},
    "cmyk": rgb_to_cmyk_values,
    "hsv": rgb_to_hsv_values,
    "hls": rgb_to_hls_values,
}

_SPACE_TO_RGB: Dict[str, Callable[[Mapping[str, int]], Dict[str, int]]] = {
    "rgb": lambda v: {"r": _byte(v["r"]), "g": _byte(v["g"]), "b": _byte(v["b"])},
    "cmyk": cmyk_to_rgb_values,
    "hsv": hsv_to_rgb_values,
    "hls": hls_to_rgb_values,
}


def rgb_to_space_values(space_name: str, rgb: Mapping[str, int]) -> Dict[str, int]:
    try:
        return _RGB_TO_SPACE[space_name](rgb)
    except KeyError:
        raise KeyError(f"unknown color space: {space_name!r}") from None


def space_to_rgb_values(space_name: str, values: Mapping[str, int]) -> Dict[str, int]:
    try:
        return _SPACE_TO_RGB[space_name](values)
    except KeyError:
        raise KeyError(f"unknown color space: {space_name!r}") from None


# ---------------------------------------------------------------------------
# Snapshot resolution
# ---------------------------------------------------------------------------
def resolve_active_rgb(
    snapshots: Mapping[str, Mapping[str, object]]
) -> Tuple[str, Dict[str, int], Dict[str, int]]:
    """Find the first space in :data:`SPACE_ORDER` whose snapshot carries data.

    Returns ``(space_name, rgb_dict, source_values_dict)``.  Falls back to
    zero-black in RGB if no space has non-zero raws (e.g. the host has not
    initialized the slot yet, or the wheel widget is mid-drag with all
    channels at zero).
    """
    for name in SPACE_ORDER:
        snapshot = snapshots.get(name)
        if not snapshot:
            continue
        raws = snapshot.get("raws") or ()
        if space_has_nonzero_raws(raws):
            values = snapshot["values"]
            return name, space_to_rgb_values(name, values), values
    return "rgb", {"r": 0, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 0}
