from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from ui.color_conversions import find_max_lab_c, find_max_oklch_c

# The disc reproduces the square a*b* plane's colours: the rim uses each
# hue's own sRGB gamut boundary (no uniform chroma cap), so no colour of
# the original square LAB visualiser is lost.  The visual edge is kept
# circular by LabSquare's antialiased outline around the disc.

# Colour-window (degrees) for smoothing the disc's radial chroma boundary.
# The sRGB gamut's constant-lightness cross-section is slightly non-convex,
# so the raw first-boundary (binary search) jumps where a concave "bay"
# begins — a knife-cut seam near the blue direction at low/mid L.  The disc
# radial scale is the circular moving AVERAGE of the boundary over this
# window, then clamped to the raw boundary per direction; the average keeps
# the scale continuous (no radial seam anywhere) and the clamp keeps every
# pixel inside the displayable gamut (the thin bay sliver is pulled to its
# inner wall instead of producing a transparent notch).
DISC_BOUNDARY_SMOOTH_DEG = 10.0
_DISC_DIRECTION_BINS = 2048


@dataclass(frozen=True, slots=True)
class LabPrewarmRequest:
    generation: int
    render_mode: str
    lightness: float
    size: int
    min_a: float
    max_a: float
    min_b: float
    max_b: float
    pixel_ratio: float
    shape: str = "square"


@dataclass(frozen=True, slots=True)
class LabPrewarmResult:
    request: LabPrewarmRequest
    image_width: int
    image_height: int
    image_bytes: bytes


class LabPrewarmSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)


class LabPrewarmTask(QRunnable):
    def __init__(self, request: LabPrewarmRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = LabPrewarmSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(render_lab_plane(self.request))
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            self.signals.failed.emit((self.request, repr(exc)))


# sRGB encode is a pow() per channel — three of them on every pixel of the
# plane. The curve only depends on one scalar, so it is tabulated once and
# indexed instead: same bytes out (the table is finer than the 8-bit result),
# a fraction of the time, and it is what makes full-quality rendering cheap
# enough to keep during a drag.
_ENCODE_STEPS = 8192
_ENCODE_LUT: np.ndarray | None = None


def _encode_lut() -> np.ndarray:
    global _ENCODE_LUT
    if _ENCODE_LUT is None:
        linear = np.linspace(0.0, 1.0, _ENCODE_STEPS, dtype=np.float64)
        srgb = np.where(linear <= 0.0031308,
                        12.92 * linear,
                        1.055 * np.power(linear, 1.0 / 2.4) - 0.055) * 255.0
        _ENCODE_LUT = np.clip(np.rint(srgb), 0.0, 255.0).astype(np.uint8)
    return _ENCODE_LUT


def _encode_channel(linear: np.ndarray) -> np.ndarray:
    """Linear light → 8-bit sRGB, through the lookup table."""
    index = np.clip(linear, 0.0, 1.0) * (_ENCODE_STEPS - 1)
    return _encode_lut()[index.astype(np.int32)]


def _rgba_bytes(red: np.ndarray, green: np.ndarray, blue: np.ndarray, mask: np.ndarray) -> bytes:
    """Pack LINEAR-light channels (0..1) + coverage into RGBA8888."""
    rgba = np.empty((*red.shape, 4), dtype=np.uint8)
    rgba[..., 0] = _encode_channel(red)
    rgba[..., 1] = _encode_channel(green)
    rgba[..., 2] = _encode_channel(blue)
    rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    return rgba.tobytes()


def _lab_to_rgb(lightness: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y_const = (lightness + 16.0) / 116.0
    x = a / 500.0 + y_const
    z = y_const - b / 200.0
    x_val = 0.96422 * np.where(x > 0.206893, x ** 3, (x - 16.0 / 116.0) / 7.787)
    y_val = np.where(y_const > 0.206893, y_const ** 3, (y_const - 16.0 / 116.0) / 7.787)
    z_val = 0.82521 * np.where(z > 0.206893, z ** 3, (z - 16.0 / 116.0) / 7.787)
    x65 = 0.9555766558 * x_val - 0.0230393428 * y_val + 0.0631636684 * z_val
    y65 = -0.0282895469 * x_val + 1.0099416212 * y_val + 0.0210076609 * z_val
    z65 = 0.0122981793 * x_val - 0.0204830040 * y_val + 1.3299098908 * z_val
    r = x65 * 3.2404542 - y65 * 1.5371385 - z65 * 0.4985314
    g = -x65 * 0.9692660 + y65 * 1.8760108 + z65 * 0.0415560
    blue = x65 * 0.0556434 - y65 * 0.2040259 + z65 * 1.0572252
    # LINEAR light: the sRGB curve is applied once, by table, in _rgba_bytes.
    # In-gamut is 0..1 here, exactly what 0..255 was after the curve.
    return r, g, blue


def _oklab_to_rgb(lightness: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g = -1.2684380042 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    blue = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3
    # LINEAR light (see _lab_to_rgb).
    return r, g, blue


def _max_chroma_array(
    lightness: float,
    a_dir: np.ndarray,
    b_dir: np.ndarray,
    mode: str,
) -> np.ndarray:
    """Vectorized binary search for max in-gamut chroma along (a_dir, b_dir).

    Mirrors ``ui.color_conversions.find_max_lab_c`` / ``find_max_oklch_c``
    for a whole polar grid at once.  Used only by the circulant LAB disc
    renderer; the square plane keeps its rectangular dynamic-range lookup.
    """
    lo = np.zeros_like(a_dir)
    hi = np.full_like(a_dir, 150.0 if mode == "lab" else 0.6)
    for _ in range(16):
        mid = (lo + hi) * 0.5
        if mode == "lab":
            red, green, blue = _lab_to_rgb(
                np.full_like(a_dir, lightness), mid * a_dir, mid * b_dir)
        else:
            red, green, blue = _oklab_to_rgb(
                np.full_like(a_dir, lightness), mid * a_dir, mid * b_dir)
        ok = ((red >= 0.0) & (red <= 1.0)
              & (green >= 0.0) & (green <= 1.0)
              & (blue >= 0.0) & (blue <= 1.0))
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


def _raw_boundary_chroma(mode: str, lightness: float, hue_deg: float) -> float:
    """Max in-gamut chroma along the *hue_deg* direction (no smoothing)."""
    hue_deg = hue_deg % 360.0
    if mode == "oklab":
        return find_max_oklch_c(lightness, hue_deg)
    return find_max_lab_c(
        lightness, math.cos(math.radians(hue_deg)), math.sin(math.radians(hue_deg)))


def smoothed_boundary_chroma(mode: str, lightness: float, hue_deg: float) -> float:
    """Max in-gamut chroma, relaxed to the disc's smoothed boundary.

    Samples the raw boundary at ``+-DISC_BOUNDARY_SMOOTH_DEG`` (plus the
    midpoints), takes the circular moving average and clamps it to the raw
    boundary for the requested direction — the renderer's exact recipe, so
    the indicator / harmony dots land on the chroma the disc actually paints.
    """
    w = DISC_BOUNDARY_SMOOTH_DEG
    half = w / 2.0
    raw = _raw_boundary_chroma(mode, lightness, hue_deg)
    values = [
        _raw_boundary_chroma(mode, lightness, hue_deg + offset)
        for offset in (-w, -half, half, w)
    ]
    avg = (raw + sum(values)) / 5.0
    return min(avg, raw)


# The boundary profile costs the same whether the disc is 200px or 700px —
# 2048 directions x a 16-step binary search — and it only depends on (mode,
# lightness). Dragging the indicator around the disc does not change either,
# and a lightness drag repeats each bucket, so a handful of cached profiles
# removes most of the disc's fixed cost.
_PROFILE_CACHE: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
_PROFILE_CACHE_LIMIT = 24


def _cached_chroma_profile(lightness: float, mode: str) -> tuple[np.ndarray, np.ndarray]:
    """Boundary profile for this lightness, reused across renders.

    Keyed on the same 0.5-step lightness bucket the plane cache uses, so a
    cache hit here means the whole disc can be redrawn without re-deriving
    the gamut boundary.
    """
    scale = 200.0 if mode == "oklab" else 2.0
    key = (mode, int(round(lightness * scale)))
    hit = _PROFILE_CACHE.get(key)
    if hit is not None:
        return hit
    profile = _disc_chroma_profile(lightness, mode)
    if len(_PROFILE_CACHE) >= _PROFILE_CACHE_LIMIT:
        _PROFILE_CACHE.pop(next(iter(_PROFILE_CACHE)))
    _PROFILE_CACHE[key] = profile
    return profile


def _disc_chroma_profile(lightness: float, mode: str) -> tuple[np.ndarray, np.ndarray]:
    """Raw + circular moving-average chroma boundaries on an angular grid.

    Returns ``(raw, smoothed)``, one value per ``_DISC_DIRECTION_BINS`` angle
    bin.  The renderer maps every pixel to its bin and uses
    ``min(smoothed, raw)``: the average keeps the painted radius continuous
    across the non-convex gamut "bay" that otherwise cuts a radial seam, and
    the clamp keeps each direction inside its displayable boundary.
    """
    n = _DISC_DIRECTION_BINS
    theta = (np.arange(n, dtype=np.float32) + 0.5) * (2.0 * np.pi / n)
    raw = _max_chroma_array(np.full(n, lightness), np.cos(theta), np.sin(theta), mode)
    w = max(1, int(round(DISC_BOUNDARY_SMOOTH_DEG / 360.0 * n)))
    ext = np.concatenate([raw[-w:], raw, raw[:w]])
    window = np.lib.stride_tricks.sliding_window_view(ext, 2 * w + 1)
    return raw, window.mean(axis=1)


# The polar grid (radius, direction, angular bin) depends only on the image
# size — not on the lightness being dragged — yet it costs an arctan2 and a
# sqrt on every pixel. Caching it per size turns a lightness drag into just
# "chroma x direction -> colour", which is what lets the disc stay at full
# resolution while the bar is moving.
_GRID_CACHE: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
_GRID_CACHE_LIMIT = 6


def _disc_grid(image_size: int):
    """(radius, a_dir, b_dir, angle_bin) for a square image of this size."""
    hit = _GRID_CACHE.get(image_size)
    if hit is not None:
        return hit
    coords = (np.arange(image_size, dtype=np.float32) + 0.5) / image_size * 2.0 - 1.0
    xx, yy = np.meshgrid(coords, -coords)  # x right, y up
    rr = np.sqrt(xx * xx + yy * yy)
    # cos(atan2(y, x)) == x / r and sin(...) == y / r, so the direction comes
    # straight out of the coordinates.
    safe_r = np.where(rr > 1e-6, rr, np.float32(1.0))
    a_dir = xx / safe_r
    b_dir = yy / safe_r
    theta = np.arctan2(yy, xx) % (2.0 * np.pi)
    bins = np.floor(theta * _DISC_DIRECTION_BINS / (2.0 * np.pi)).astype(np.intp)
    bins %= _DISC_DIRECTION_BINS
    grid = (rr, a_dir, b_dir, bins)
    if len(_GRID_CACHE) >= _GRID_CACHE_LIMIT:
        _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
    _GRID_CACHE[image_size] = grid
    return grid


def render_lab_disc(request: LabPrewarmRequest) -> LabPrewarmResult:
    """Render the circulant LAB / OKLab a*b* disc at a fixed lightness.

    Polar mapping: angle = hue, radius = relative chroma.  Every hue reaches
    its own sRGB gamut boundary at radius 1, so the disc is fully coloured
    while remaining inside the displayable gamut.
    """
    ratio = max(1.0, request.pixel_ratio)
    image_size = max(1, int(round(request.size * ratio)))
    rr, a_dir, b_dir, bins = _disc_grid(image_size)

    if request.render_mode == "oklab":
        lightness = request.lightness / 100.0
    else:
        lightness = request.lightness

    # Smoothed boundary profile: the moving average kills the knife-cut seam
    # at the concave gamut bay (blue direction); clamping to the raw boundary
    # per bin keeps every pixel displayable.
    raw_profile, smoothed_profile = _cached_chroma_profile(
        lightness, request.render_mode)
    max_c = np.minimum(smoothed_profile, raw_profile)[bins]

    # Per-hue gamut boundary as the rim: each hue reaches its own maximum
    # chroma, i.e. exactly the colours the square a*b* plane shows, so no
    # colour of the original square LAB visualiser is lost.  The visual edge
    # is kept crisp and circular by LabSquare's antialiased outline.
    chroma = np.clip(rr, 0.0, 1.0) * max_c
    a = chroma * a_dir
    b = chroma * b_dir

    light = np.float32(lightness)
    if request.render_mode == "oklab":
        red, green, blue = _oklab_to_rgb(light, a, b)
    else:
        red, green, blue = _lab_to_rgb(light, a, b)

    # The disc's alpha edge is exclusively the geometric circle.  ``max_c``
    # comes from 2048 bin-centre directions, so at sharp gamut corners a
    # pixel's exact direction can be a hair more restrictive than its bin —
    # a few rim pixels would be out of gamut.  Instead of punching tiny
    # transparent notches into the circle (_rgba_bytes already clamps the
    # out-of-gamut channels to 0..1), the mask stays a perfect circle and no
    # visible "bite" appears at the edge.
    mask = rr <= 1.0
    return LabPrewarmResult(
        request=request,
        image_width=image_size,
        image_height=image_size,
        image_bytes=_rgba_bytes(red, green, blue, mask),
    )


def render_lab_plane(request: LabPrewarmRequest) -> LabPrewarmResult:
    if request.shape == "disc":
        return render_lab_disc(request)
    ratio = max(1.0, request.pixel_ratio)
    image_size = max(1, int(round(request.size * ratio)))
    x = np.linspace(request.min_a, request.max_a, image_size, dtype=np.float32)
    y = np.linspace(request.max_b, request.min_b, image_size, dtype=np.float32)
    aa, bb = np.meshgrid(x, y)
    if request.render_mode == "oklab":
        red, green, blue = _oklab_to_rgb(np.float32(request.lightness / 100.0), aa, bb)
    else:
        red, green, blue = _lab_to_rgb(np.float32(request.lightness), aa, bb)
    mask = ((red >= 0.0) & (red <= 1.0) & (green >= 0.0) & (green <= 1.0)
            & (blue >= 0.0) & (blue <= 1.0))
    return LabPrewarmResult(
        request=request,
        image_width=image_size,
        image_height=image_size,
        image_bytes=_rgba_bytes(red, green, blue, mask),
    )
