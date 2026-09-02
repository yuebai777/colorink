from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from ui.color_conversions import (
    find_max_lab_c,
    find_max_oklch_c,
    lab_to_linear_array,
    lab_to_rgb_array,
    oklab_to_linear_array,
    oklab_to_rgb_array,
    rgb_to_lab,
    srgb_encode_u8,
)


@dataclass(frozen=True, slots=True)
class SlicePrewarmRequest:
    generation: int
    mode: str
    hue: float
    center_x: float
    center_y: float
    radius: float
    pixel_ratio: float
    # Horizontal extent of the OKLCh slice box; None falls back to radius.
    # The box can be wider than the radius in ringless mode so the gamut
    # region has more pixels to pick from.
    width: float | None = None
    # Pre-computed C→pixel scale for the OKLCh slice. When provided, the
    # per-hue boundary scan is skipped (the caller already computed it).
    scale: float | None = None
    # Render at 1/subsample of the device resolution. The caller upscales
    # the returned image to the logical size for interactive paints.
    subsample: int = 1


@dataclass(frozen=True, slots=True)
class SlicePrewarmResult:
    request: SlicePrewarmRequest
    min_x: int
    min_y: int
    width: int
    height: int
    image_width: int
    image_height: int
    image_bytes: bytes
    edge_x: tuple[int, ...] | None = None


class SlicePrewarmSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)


class SlicePrewarmTask(QRunnable):
    def __init__(self, request: SlicePrewarmRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = SlicePrewarmSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(render_slice(self.request))
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            self.signals.failed.emit((self.request, repr(exc)))


def render_slice(request: SlicePrewarmRequest) -> SlicePrewarmResult:
    mode = request.mode
    if mode in {"hsv-square", "hsl-square"}:
        return _render_square(request, mode == "hsv-square")
    if mode == "hls-triangle":
        return _render_hls_triangle(request)
    if mode == "rgb-slice":
        return _render_rgb_slice(request)
    if mode == "oklch-slice":
        return _render_oklch_slice(request)
    raise ValueError(f"unsupported slice prewarm mode: {mode}")


def _rgba_bytes(red: np.ndarray, green: np.ndarray, blue: np.ndarray, mask: np.ndarray | None = None) -> bytes:
    height, width = red.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(red, 0.0, 255.0).astype(np.uint8)
    rgba[..., 1] = np.clip(green, 0.0, 255.0).astype(np.uint8)
    rgba[..., 2] = np.clip(blue, 0.0, 255.0).astype(np.uint8)
    rgba[..., 3] = 255 if mask is None else np.where(mask, 255, 0).astype(np.uint8)
    return rgba.tobytes()


def _rgba_bytes_linear(red: np.ndarray, green: np.ndarray, blue: np.ndarray,
                       mask: np.ndarray) -> bytes:
    """Pack LINEAR-light channels (0-1) + coverage into RGBA8888.

    The sRGB curve is applied here, once, by table — instead of a pow() per
    channel per pixel inside the conversion (see srgb_encode_u8).
    """
    height, width = red.shape
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = srgb_encode_u8(red)
    rgba[..., 1] = srgb_encode_u8(green)
    rgba[..., 2] = srgb_encode_u8(blue)
    rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    return rgba.tobytes()


def _hsv_to_rgb(h: float, saturation: np.ndarray, value: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h6 = (h % 360.0) / 60.0
    sector = int(np.floor(h6)) % 6
    fraction = h6 - np.floor(h6)
    p = value * (1.0 - saturation)
    q = value * (1.0 - fraction * saturation)
    t = value * (1.0 - (1.0 - fraction) * saturation)
    choices = (
        (value, t, p),
        (q, value, p),
        (p, value, t),
        (p, q, value),
        (t, p, value),
        (value, p, q),
    )
    return choices[sector]


def _hls_to_rgb(h: float, lightness: np.ndarray, saturation: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    chroma = (1.0 - np.abs(2.0 * lightness - 1.0)) * saturation
    h6 = (h % 360.0) / 60.0
    x = chroma * (1.0 - np.abs((h6 % 2.0) - 1.0))
    match = lightness - chroma / 2.0
    sector = int(np.floor(h6)) % 6
    choices = (
        (chroma, x, np.zeros_like(chroma)),
        (x, chroma, np.zeros_like(chroma)),
        (np.zeros_like(chroma), chroma, x),
        (np.zeros_like(chroma), x, chroma),
        (x, np.zeros_like(chroma), chroma),
        (chroma, np.zeros_like(chroma), x),
    )
    red, green, blue = choices[sector]
    return red + match, green + match, blue + match


def _render_square(request: SlicePrewarmRequest, hsv: bool) -> SlicePrewarmResult:
    half = int(request.radius / 1.414) - 2
    width = max(1, half * 2)
    height = width
    ratio = max(1.0, request.pixel_ratio)
    subsample = max(1, int(request.subsample))
    image_width = max(1, int(round(width * ratio)) // subsample)
    image_height = max(1, int(round(height * ratio)) // subsample)
    x = np.linspace(0.0, 1.0, image_width, dtype=np.float32)
    y = np.linspace(1.0, 0.0, image_height, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    if hsv:
        red, green, blue = _hsv_to_rgb(request.hue, xx, yy)
    else:
        red, green, blue = _hls_to_rgb(request.hue, yy, xx)
    return SlicePrewarmResult(
        request=request,
        min_x=int(round(request.center_x - half)),
        min_y=int(round(request.center_y - half)),
        width=width,
        height=height,
        image_width=image_width,
        image_height=image_height,
        image_bytes=_rgba_bytes(red * 255.0, green * 255.0, blue * 255.0),
    )


def _triangle_grid(request: SlicePrewarmRequest, right_extent: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, int, int, int]:
    radius = request.radius
    hy = radius * 0.866
    min_x = int(np.floor(request.center_x - radius * 0.5))
    max_x = int(np.ceil(request.center_x + radius * right_extent))
    min_y = int(np.floor(request.center_y - hy))
    max_y = int(np.ceil(request.center_y + hy))
    width = max(1, max_x - min_x)
    height = max(1, max_y - min_y)
    px = min_x + np.arange(width, dtype=np.float32)[None, :]
    py = min_y + np.arange(height, dtype=np.float32)[:, None]
    return px, py, np.broadcast_to(px, (height, width)), np.broadcast_to(py, (height, width)), min_x, min_y, width, height


def _render_hls_triangle(request: SlicePrewarmRequest) -> SlicePrewarmResult:
    _, _, _, _, min_x, min_y, width, height = _triangle_grid(request, 1.0)
    ratio = max(1.0, request.pixel_ratio)
    subsample = max(1, int(request.subsample))
    image_width = max(1, int(round(width * ratio)) // subsample)
    image_height = max(1, int(round(height * ratio)) // subsample)
    hy = request.radius * 0.866
    grid_x = np.linspace(min_x, min_x + width, image_width, endpoint=False, dtype=np.float32)[None, :]
    grid_y = np.linspace(min_y, min_y + height, image_height, endpoint=False, dtype=np.float32)[:, None]
    grid_x = np.broadcast_to(grid_x, (image_height, image_width))
    grid_y = np.broadcast_to(grid_y, (image_height, image_width))
    lightness = np.clip((request.center_y + hy - grid_y) / (2.0 * hy), 0.0, 1.0)
    px_left = request.center_x - 0.5 * request.radius
    px_right = px_left + 3.0 * request.radius * (0.5 - np.abs(lightness - 0.5))
    saturation = np.divide(
        grid_x - px_left,
        px_right - px_left,
        out=np.zeros_like(grid_x),
        where=(px_right - px_left) > 0.001,
    )
    mask = (grid_x >= px_left) & (grid_x <= px_right)
    saturation = np.clip(saturation, 0.0, 1.0)
    red, green, blue = _hls_to_rgb(request.hue, lightness, saturation)
    return SlicePrewarmResult(
        request=request, min_x=min_x, min_y=min_y, width=width, height=height,
        image_width=image_width, image_height=image_height,
        image_bytes=_rgba_bytes(red * 255.0, green * 255.0, blue * 255.0, mask),
    )


def _render_rgb_slice(request: SlicePrewarmRequest) -> SlicePrewarmResult:
    _, _, _, _, min_x, min_y, width, height = _triangle_grid(request, 1.5)
    ratio = max(1.0, request.pixel_ratio)
    subsample = max(1, int(request.subsample))
    image_width = max(1, int(round(width * ratio)) // subsample)
    image_height = max(1, int(round(height * ratio)) // subsample)
    hy = request.radius * 0.866
    pure_h = request.hue % 360.0
    pure_r, pure_g, pure_b = _hsv_to_rgb(pure_h, np.asarray(1.0), np.asarray(1.0))
    l_p, a_p, b_p = rgb_to_lab(float(pure_r * 255.0), float(pure_g * 255.0), float(pure_b * 255.0))
    pure_c = float(np.hypot(a_p, b_p))
    a_dir = a_p / pure_c if pure_c > 0.001 else 0.0
    b_dir = b_p / pure_c if pure_c > 0.001 else 0.0
    max_c = max(find_max_lab_c(20.0, a_dir, b_dir), find_max_lab_c(50.0, a_dir, b_dir), find_max_lab_c(80.0, a_dir, b_dir))
    scale = (request.radius * 1.05) / max(max_c, 0.001)
    x = np.linspace(min_x, min_x + width, image_width, endpoint=False, dtype=np.float32)[None, :]
    y = np.linspace(min_y, min_y + height, image_height, endpoint=False, dtype=np.float32)[:, None]
    lightness = np.clip((request.center_y + hy - y) / (2.0 * hy), 0.0, 1.0) * 100.0
    chroma = np.maximum(0.0, (x - min_x) / scale)
    a = chroma * a_dir
    b = chroma * b_dir
    red, green, blue = lab_to_linear_array(lightness, a, b)
    mask = ((red >= 0.0) & (red <= 1.0) & (green >= 0.0) & (green <= 1.0)
            & (blue >= 0.0) & (blue <= 1.0))
    # Edge curve: one value per LOGICAL row (the outline is drawn in widget
    # logical coordinates), sampled from the rendered mask. The image→logical
    # scales cover subsampled renders, not just the device pixel ratio.
    last = np.where(mask, np.arange(image_width, dtype=np.int32)[None, :], -1).max(axis=1)
    row_scale = image_height / float(height)
    col_scale = image_width / float(width)
    logical_rows = np.minimum(image_height - 1, (np.arange(height, dtype=np.float64) * row_scale + 0.5).astype(np.int64))
    edge_x = tuple((min_x + np.round(np.maximum(last[logical_rows], 0) / col_scale)).astype(np.int32).tolist())
    return SlicePrewarmResult(
        request=request, min_x=min_x, min_y=min_y, width=width, height=height,
        image_width=image_width, image_height=image_height,
        image_bytes=_rgba_bytes_linear(red, green, blue, mask), edge_x=edge_x,
    )


def _render_oklch_slice(request: SlicePrewarmRequest) -> SlicePrewarmResult:
    radius = request.radius
    hy = radius * 0.866
    box_w = request.width or radius
    min_x = int(np.floor(request.center_x - box_w * 0.5))
    max_x = int(np.ceil(request.center_x + box_w * 0.5))
    min_y = int(np.floor(request.center_y - hy))
    max_y = int(np.ceil(request.center_y + hy))
    width = max(1, max_x - min_x)
    height = max(1, max_y - min_y)
    ratio = max(1.0, request.pixel_ratio)
    subsample = max(1, int(request.subsample))
    image_width = max(1, int(round(width * ratio)) // subsample)
    image_height = max(1, int(round(height * ratio)) // subsample)
    hue = request.hue % 360.0
    # Per-hue scale (same 201-point C_max scan as ColorWheel.
    # _oklch_boundary_data): each hue's gamut fills the box width, and the
    # prewarmed image matches the widget's outline and indicator exactly.
    # A caller-supplied scale skips the scan — the interactive fallback
    # already computed the boundary for the outline in the same paint.
    if request.scale is not None and request.scale > 0.0:
        scale = float(request.scale)
    else:
        max_c = max(find_max_oklch_c(v, hue) for v in np.linspace(0.0, 1.0, 201))
        scale = box_w / max(max_c, 0.001)
    x = np.linspace(min_x, min_x + width, image_width, endpoint=False, dtype=np.float32)[None, :]
    y = np.linspace(min_y, min_y + height, image_height, endpoint=False, dtype=np.float32)[:, None]
    lightness = np.clip((request.center_y + hy - y) / (2.0 * hy), 0.0, 1.0)
    chroma = np.maximum(0.0, (x - min_x) / scale)
    angle = np.deg2rad(hue)
    a = chroma * np.cos(angle)
    b = chroma * np.sin(angle)
    red, green, blue = oklab_to_linear_array(lightness, a, b)
    mask = ((red >= 0.0) & (red <= 1.0) & (green >= 0.0) & (green <= 1.0)
            & (blue >= 0.0) & (blue <= 1.0))
    return SlicePrewarmResult(
        request=request, min_x=min_x, min_y=min_y, width=width, height=height,
        image_width=image_width, image_height=image_height,
        image_bytes=_rgba_bytes_linear(red, green, blue, mask),
    )
