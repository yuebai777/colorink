"""Geometry, hit-testing and ringless layout for ColorWheel.

Extracted from ``ui.color_wheel``: slice/wheel geometry, triangle/barycentric
helpers and ringless layout propagation.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF

from ui.window_layout import resolve_picker_geometry
from ui.color_conversions import (
    find_max_oklch_c,
    hsl_to_hsv,
    hsv_to_rgb as _hsv_to_rgb_float,
)

if TYPE_CHECKING:
    from ui.ringless_mode import RinglessLayout


@dataclass(frozen=True, slots=True)
class SliceGeometry:
    """Immutable geometry for the active colour slice in any mode."""
    center_x: float
    center_y: float
    radius: float


def hsv_to_rgb(h, s, v):
    """Integer RGB (0–255) — QColor-friendly wrapper over cc.hsv_to_rgb."""
    r, g, b = _hsv_to_rgb_float(h, s, v)
    # round() 而非 int()：int 向零截断会让信号里的 RGB 系统性偏暗 1/255。
    return round(r), round(g), round(b)


def hls_to_hsv_floats(h, l, s):
    """Compatibility wrapper: l/s in 0–1 → HSV (h 0-360, s/v 0-100)."""
    return hsl_to_hsv(h, l * 100.0, s * 100.0)


def project_point_to_triangle(px, py, v0, v1, v2):
    denom = (v1.y() - v2.y()) * (v0.x() - v2.x()) + (v2.x() - v1.x()) * (v0.y() - v2.y())
    if abs(denom) < 1e-6:
        return v0.x(), v0.y()
    w0 = ((v1.y() - v2.y()) * (px - v2.x()) + (v2.x() - v1.x()) * (py - v2.y())) / denom
    w1 = ((v2.y() - v0.y()) * (px - v2.x()) + (v0.x() - v2.x()) * (py - v2.y())) / denom
    w2 = 1.0 - w0 - w1

    if w0 >= 0.0 and w1 >= 0.0 and w2 >= 0.0:
        return px, py

    # Project to the closest edge
    def dist_sq(x1, y1, x2, y2):
        return (x1 - x2) ** 2 + (y1 - y2) ** 2

    def project_to_segment(px, py, a, b):
        abx = b.x() - a.x()
        aby = b.y() - a.y()
        apx = px - a.x()
        apy = py - a.y()
        t = (apx * abx + apy * aby) / (abx * abx + aby * aby)
        t = max(0.0, min(1.0, t))
        return QPointF(a.x() + t * abx, a.y() + t * aby)

    p0 = project_to_segment(px, py, v0, v1)
    p1 = project_to_segment(px, py, v1, v2)
    p2 = project_to_segment(px, py, v2, v0)

    d0 = dist_sq(px, py, p0.x(), p0.y())
    d1 = dist_sq(px, py, p1.x(), p1.y())
    d2 = dist_sq(px, py, p2.x(), p2.y())

    min_d = d0
    best_p = p0
    if d1 < min_d:
        min_d = d1
        best_p = p1
    if d2 < min_d:
        min_d = d2
        best_p = p2

    return best_p.x(), best_p.y()


class ColorWheelGeometryMixin:

    def set_ringless_layout(self, layout: "RinglessLayout") -> None:
        """Apply a ringless layout, invalidating caches only when it changes.

        Idempotent: setting the same layout twice is a no-op.
        """
        current = getattr(self, "_ringless_layout", None)
        if current == layout:
            return

        # ── invalidate every geometry-dependent cache ──
        # Ring caches
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        if hasattr(self, "_cached_oklch_ring_key"):
            delattr(self, "_cached_oklch_ring_key")

        # Slice-image caches (square / HLS / RGB / OKLCh)
        self._cached_img_key = None
        if hasattr(self, "_cached_hls_key"):
            delattr(self, "_cached_hls_key")
        if hasattr(self, "_cached_rgb_key"):
            delattr(self, "_cached_rgb_key")
        if hasattr(self, "_cached_oklch_key"):
            delattr(self, "_cached_oklch_key")

        # OKLCh gamut-boundary cache
        if hasattr(self, "_bdry_h"):
            delattr(self, "_bdry_h")

        self._ringless_layout = layout
        self.update()

    def get_slice_geometry(self, mode: str | None = None) -> SliceGeometry:
        """Return the centre and radius for the active slice.

        In full mode (no ringless layout) this defers to the legacy
        wheel-geometry computation.  In ringless mode it computes the
        largest fitting geometry for the active module independently.
        """
        layout = getattr(self, "_ringless_layout", None)
        if layout is None or not layout.wheel_enabled:
            cx, cy, _, _, _, tr = self.get_wheel_geometry()
            return SliceGeometry(center_x=cx, center_y=cy, radius=tr)

        # ── ringless: one available rectangle ──
        # Margins: left, right, AND bottom.  Top margin sits below
        # the control bar so the slice does not overlap it.
        margin = layout.margin
        available_w = float(self.width() - 2 * margin)
        reserved_bar_h = layout.control_bar_height if layout.controls_enabled else 0
        available_h = float(self.height() - reserved_bar_h - 2 * margin)

        if available_w <= 0.0 or available_h <= 0.0:
            return SliceGeometry(
                center_x=float(self.width()) / 2.0,
                center_y=float(self.height()) / 2.0,
                radius=1.0,
            )

        available_center_x = float(self.width()) / 2.0
        top_reserved = reserved_bar_h if layout.control_bar_position == "top" else 0
        cy = float(top_reserved + margin) + available_h / 2.0

        active_mode = mode or self.wheel_mode
        match active_mode:
            case "hsv-square" | "hsl-square" | "vhsv-square":
                # Largest square: side = min(available_w, available_h)
                # half = int(r / 1.414) - 2  →  square_side = 2 * half
                side = min(available_w, available_h)
                radius = (side / 2.0 + 2.0) * 1.414
                center_x = available_center_x
            case "triangle" | "hls-triangle":
                # Triangle: width = 1.5r, height = 1.732r
                radius = min(available_w / 1.5, available_h / 1.732)
                center_x = available_center_x - radius / 4.0
            case "oklch-slice":
                # Visible gamut: width = r, height = 1.732r
                radius = min(available_w, available_h / 1.732)
                center_x = available_center_x
            case "rgb-slice":
                # RGB allocation: width = 2r, height = 1.732r
                radius = min(available_w / 2.0, available_h / 1.732)
                center_x = available_center_x - radius / 2.0
            case _:
                # Unknown mode — safe fallback to square
                side = min(available_w, available_h)
                radius = (side / 2.0 + 2.0) * 1.414
                center_x = available_center_x

        return SliceGeometry(center_x=center_x, center_y=cy, radius=max(1.0, radius))

    def _oklch_slice_box_width(self, r: float) -> float:
        """Horizontal extent of the OKLCh slice box (the C axis).

        Full mode: the box height (1.732r) is fixed by the L axis and the
        ring's inner circle (radius r + 3) caps the corners, so the box is
        widened horizontally to that circle limit — the slice graphic gets
        as wide as the ring allows without touching the ring itself.  The
        C axis stays fixed, so colour VALUES are unaffected — only the
        pixel scale grows.
        Ringless: the box uses the full available width, making the gamut
        region as wide as possible for easier colour picking.
        """
        layout = getattr(self, "_ringless_layout", None)
        if layout is not None and layout.wheel_enabled:
            return max(1.0, float(self.width() - 2 * layout.margin))
        # Circle limit: (0.5*box_w)^2 + (0.866*r)^2 <= (r + 3)^2
        half_w = math.sqrt(max(0.0, (r + 3.0) ** 2 - (0.866 * r) ** 2))
        return max(float(r), 2.0 * half_w)

    def _oklch_boundary_data(self, h):
        """Pre-compute gamut boundary curve for a given hue.

        Returns (scale, boundary_points) where *boundary_points* is a list
        of 201 (C_max, L) pairs (L from 0 to 1, step 0.005) and *scale*
        maps chroma to pixels so the widest part of THIS hue's gamut fills
        the slice box width — each hue is adapted separately, so the
        coloured region is always as wide as the box.  The result is
        cached so repeated calls at the same hue are free.
        """
        cache_h = getattr(self, '_bdry_h', None)
        if cache_h is not None and abs(cache_h - h) < 0.05:
            return self._bdry_scale, self._bdry_points

        points = []  # (C_max, L)  — L from 0 → 1
        max_c = 0.0
        for i in range(201):
            _l = i / 200.0
            _c = find_max_oklch_c(_l, h)
            points.append((_c, _l))
            if _c > max_c:
                max_c = _c

        self._bdry_h = h
        self._bdry_points = points
        # Per-hue scale: this hue's widest chroma fills the slice box.
        r = getattr(self, '_bdry_slice_r', None)
        if r is None:
            r = 130  # sensible default before first layout
        self._bdry_scale = (self._oklch_slice_box_width(r) * 1.0) / max(max_c, 0.001)
        return self._bdry_scale, points

    def _oklch_scale_for_hue(self, h, r):
        """Compute the C→pixel scale used by slice, indicator, and drag.

        Per-hue: every hue's gamut is stretched to fill the slice box
        width, so the coloured region is always as wide as possible (the
        indicator therefore slides horizontally when the hue changes —
        the tradeoff for the wide slice).
        """
        _, points = self._oklch_boundary_data(h)
        self._bdry_slice_r = r
        box_w = self._oklch_slice_box_width(r)
        self._bdry_scale = (box_w * 1.0) / max(
            max((c for c, _ in points), default=0.001), 0.001)
        return self._bdry_scale

    def get_wheel_geometry(self):
        """Ring geometry for the current widget size.

        The formula itself lives in :mod:`ui.window_layout` — it is shared
        with the LAB disc, the resize pass and the theme pass, which used to
        each carry their own copy (two of them had already drifted apart).
        """
        picker = resolve_picker_geometry(self.width(), self.height())
        return (picker.circle.x, picker.circle.y, picker.size,
                picker.circle.radius, picker.inner_radius,
                picker.triangle_radius)

    def get_triangle_vertices(self, cx, cy, r):
        hy = r * 0.866
        return (
            QPointF(cx + r, cy),                 # v0: pure color
            QPointF(cx - r * 0.5, cy - hy),      # v1: white
            QPointF(cx - r * 0.5, cy + hy)       # v2: black
        )

    def get_barycentric_coords(self, px, py, v0, v1, v2):
        denom = (v1.y() - v2.y()) * (v0.x() - v2.x()) + (v2.x() - v1.x()) * (v0.y() - v2.y())
        if abs(denom) < 0.0001:
            return 0.0, 0.0, 1.0
            
        w0 = ((v1.y() - v2.y()) * (px - v2.x()) + (v2.x() - v1.x()) * (py - v2.y())) / denom
        w1 = ((v2.y() - v0.y()) * (px - v2.x()) + (v0.x() - v2.x()) * (py - v2.y())) / denom
        w2 = 1.0 - w0 - w1
        
        w0 = max(0.0, min(1.0, w0))
        w1 = max(0.0, min(1.0, w1))
        w2 = max(0.0, min(1.0, w2))
        
        sum_w = w0 + w1 + w2
        if sum_w > 0.001:
            return w0 / sum_w, w1 / sum_w, w2 / sum_w
        return 0.0, 0.0, 1.0

    def _is_ringless(self) -> bool:
        layout = getattr(self, "_ringless_layout", None)
        return layout is not None and layout.wheel_enabled

    def is_point_in_triangle(self, px, py, v0, v1, v2):
        denom = (v1.y() - v2.y()) * (v0.x() - v2.x()) + (v2.x() - v1.x()) * (v0.y() - v2.y())
        if abs(denom) < 1e-6:
            return False
        w0 = ((v1.y() - v2.y()) * (px - v2.x()) + (v2.x() - v1.x()) * (py - v2.y())) / denom
        w1 = ((v2.y() - v0.y()) * (px - v2.x()) + (v0.x() - v2.x()) * (py - v2.y())) / denom
        w2 = 1.0 - w0 - w1
        return (w0 >= -0.01) and (w1 >= -0.01) and (w2 >= -0.01)

    def _is_point_in_active_slice(self, px: float, py: float, cx: float, cy: float, r: float) -> bool:
        """Return True when (px, py) is inside the current mode's slice bounds.

        Unknown wheel modes are rejected — a click in an unrecognised mode
        must never start dragging.
        """
        match self.wheel_mode:
            case "hsv-square" | "hsl-square" | "vhsv-square":
                half = int(r / 1.414) - 2
                return abs(px - cx) <= half and abs(py - cy) <= half
            case "triangle" | "hls-triangle":
                v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
                return self.is_point_in_triangle(px, py, v0, v1, v2)
            case "oklch-slice":
                hy = r * 0.866
                box_w = self._oklch_slice_box_width(r)
                min_x = cx - box_w * 0.5
                max_x = cx + box_w * (0.5 if self._is_ringless() else 1.5)
                min_y = cy - hy
                max_y = cy + hy
                if not (min_x <= px <= max_x and min_y <= py <= max_y):
                    return False
                L_coord = (cy + hy - py) / (2.0 * hy)
                if L_coord < 0.0 or L_coord > 1.0:
                    return False
                return px >= min_x
            case "rgb-slice":
                hy = r * 0.866
                min_x = cx - r * 0.5
                max_x = cx + r * 1.5
                min_y = cy - hy
                max_y = cy + hy
                if not (min_x <= px <= max_x and min_y <= py <= max_y):
                    return False
                L_coord = (cy + hy - py) / (2.0 * hy)
                if L_coord < 0.0 or L_coord > 1.0:
                    return False
                return px >= min_x
            case _:
                # Unknown mode — reject clicks
                return False
