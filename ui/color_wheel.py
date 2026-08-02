from dataclasses import dataclass
import math
import colorsys
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QImage, QPen, QBrush, QConicalGradient, QPainterPath, QLinearGradient
from PyQt6.QtCore import Qt, QPointF, QRectF, QThreadPool, QTimer, pyqtSignal
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ui.ringless_mode import RinglessLayout
from ui.color_conversions import (
    lab_to_rgb, rgb_to_lab,
    oklch_to_rgb, rgb_to_oklch,
    find_max_lab_c, find_max_oklch_c,
)
from core import config
from ui.slice_prewarm import SlicePrewarmRequest, SlicePrewarmResult, SlicePrewarmTask


@dataclass(frozen=True, slots=True)
class SliceGeometry:
    """Immutable geometry for the active colour slice in any mode."""
    center_x: float
    center_y: float
    radius: float

def hsv_to_rgb(h, s, v):
    # h: [0, 360], s: [0, 100], v: [0, 100]
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
    return int(r * 255), int(g * 255), int(b * 255)

def rgb_to_hsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, s * 100.0, v * 100.0

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

def hls_to_hsv_floats(h, l, s):
    # h: 0-360, l: 0-1, s: 0-1
    v = l + s * min(l, 1.0 - l)
    hsv_s = 2.0 * (1.0 - l / v) if v > 0.0001 else 0.0
    return h, hsv_s * 100.0, v * 100.0

class ColorWheel(QWidget):
    # Emits (r, g, b)
    colorChanged = pyqtSignal(int, int, int)
    interactionFinished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.cfg = config.load_hotkey_config()

        # Color state (HSV)
        self.h = 0.0
        self.s = 100.0
        self.v = 100.0
        self._last_hue = 0.0

        # Direct OKLCh state — set by external callers so the indicator
        # doesn't have to round-trip through HSV→RGB→OKLCh.
        self._oklch_L = None      # float 0-1 or None
        self._oklch_C = None      # float 0-0.5 or None
        self._oklch_h = None      # float 0-360 or None
        
        self.dragging = None
        
        # Mode
        self.wheel_mode = "hsv-square"
        
        # Ringless-mode layout (None = full mode / disabled)
        self._ringless_layout: "RinglessLayout | None" = None

        # Cache variables for fast rendering
        self._cached_img = None
        self._cached_img_key = None

        # Full-resolution slice warmups are generated off the GUI thread.
        # The result is kept per mode so switching modules does not throw away
        # the other modes' ready-to-paint images.
        self._prewarmed_slices: dict[str, dict[str, object]] = {}
        self._prewarm_generation = 0
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self.prewarm_all_slices)
        self._prewarm_pool = QThreadPool(self)
        self._prewarm_pool.setMaxThreadCount(1)

    def resizeEvent(self, event):
        """Invalidate cached ring image on resize and force a full repaint.

        Without this, when the window is occluded and then resized, Qt's
        backing store (WA_TranslucentBackground) may leave stale pixels in
        previously-occluded regions because the layout-triggered resize does
        not automatically schedule a paint event for those areas.
        """
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        self._invalidate_slice_caches()
        super().resizeEvent(event)
        self.update()

    def reload_config(self):
        self.cfg = config.load_hotkey_config()
        # Invalidate the ring cache so it gets redrawn with the new settings
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        self.update()

    _PREWARM_MODES = (
        "hsv-square", "hls-triangle", "rgb-slice", "oklch-slice",
    )

    def schedule_slice_prewarm(self, delay_ms: int = 250) -> None:
        """Warm full-resolution slices after the UI has settled."""
        self._prewarm_timer.start(max(0, delay_ms))

    def _slice_cache_key(self, mode: str, center_x: float, center_y: float, radius: float) -> tuple[object, ...]:
        if mode in {"hsv-square", "hsl-square"}:
            half = int(radius / 1.414) - 2
            tag = "hsv-square" if mode == "hsv-square" else "square"
            return (int(self.h), half * 2, half * 2, tag)
        if mode == "oklch-slice":
            hue = self._oklch_h if self._oklch_h is not None else self.h
            return (round(hue, 1), radius, round(center_x, 3), round(center_y, 3), "oklch")
        tag = "hls" if mode == "hls-triangle" else "rgb"
        return (self.h, radius, round(center_x, 3), round(center_y, 3), tag)

    def _invalidate_slice_caches(self) -> None:
        self._prewarm_generation += 1
        self._prewarmed_slices.clear()
        self._cached_img_key = None
        for attr in ("_cached_hls_key", "_cached_rgb_key", "_cached_oklch_key"):
            if hasattr(self, attr):
                delattr(self, attr)

    def prewarm_all_slices(self) -> None:
        """Queue one background full-resolution render for every module slice."""
        if self.width() < 40 or self.height() < 40:
            return
        self._prewarm_generation += 1
        generation = self._prewarm_generation
        self._prewarmed_slices.clear()
        pixel_ratio = max(1.0, float(self.devicePixelRatio()))
        ordered_modes = (self.wheel_mode,) + tuple(
            mode for mode in self._PREWARM_MODES if mode != self.wheel_mode
        )
        for mode in ordered_modes:
            if mode not in self._PREWARM_MODES:
                continue
            geometry = self.get_slice_geometry(mode)
            hue = self._oklch_h if mode == "oklch-slice" and self._oklch_h is not None else self.h
            request = SlicePrewarmRequest(
                generation=generation, mode=mode, hue=hue,
                center_x=geometry.center_x, center_y=geometry.center_y,
                radius=geometry.radius, pixel_ratio=pixel_ratio,
            )
            task = SlicePrewarmTask(request)
            task.signals.finished.connect(self._on_slice_prewarm_finished)
            task.signals.failed.connect(self._on_slice_prewarm_failed)
            self._prewarm_pool.start(task)

    def _on_slice_prewarm_finished(self, result: object) -> None:
        if not isinstance(result, SlicePrewarmResult):
            return
        request = result.request
        if request.generation != self._prewarm_generation:
            return
        image = QImage(
            result.image_bytes, result.image_width, result.image_height,
            result.image_width * 4, QImage.Format.Format_RGBA8888,
        ).copy()
        image.setDevicePixelRatio(request.pixel_ratio)
        self._prewarmed_slices[request.mode] = {
            "key": self._slice_cache_key(
                request.mode, request.center_x, request.center_y, request.radius
            ),
            "image": image,
            "min_x": result.min_x,
            "min_y": result.min_y,
            "width": result.width,
            "height": result.height,
            "edge_x": result.edge_x,
        }
        self.update()

    def _on_slice_prewarm_failed(self, failure: object) -> None:
        # Prewarming is an optimization only; normal paint remains the fallback.
        return

    def is_active_interaction(self):
        """Return True when wheel is being dragged or an external slider is active."""
        if self.dragging:
            return True
        win = self.window()
        if win is not None and hasattr(win, "slider_widgets"):
            for chan, (slider, _) in win.slider_widgets.items():
                if slider.isSliderDown():
                    return True
        return False

    def _clear_drag_anchor(self) -> None:
        """Forget the last exact slice coordinate after an external update."""
        self._drag_slice = ""
        self._drag_C = None
        self._drag_L = None
        self._drag_oklch_h = None

    def set_color(self, r, g, b, block_signals=False, update_widget=True):
        old_hue = self.h
        self._clear_drag_anchor()  # external color change, reset indicator mode
        h, s, v = rgb_to_hsv(r, g, b)
        if s > 0.5: self._last_hue = h
        else: h = self._last_hue
        self.h = h; self.s = s; self.v = v
        # Slice pixels depend on hue and geometry, not the current S/V point.
        # Keep the full-resolution cache alive when a page switch restores the
        # same hue; only a real hue change needs a new image.
        if abs(h - old_hue) > 0.01:
            self._invalidate_slice_caches()
        if update_widget:
            self.update()
        if not block_signals:
            self.colorChanged.emit(r, g, b)

    def set_hsv(self, h, s, v, update_widget=True):
        old_hue = self.h
        self._clear_drag_anchor()
        if s > 0.5: self._last_hue = h
        else: h = self._last_hue
        self.h = h; self.s = s; self.v = v
        if abs(h - old_hue) > 0.01:
            self._invalidate_slice_caches()
        if update_widget:
            self.update()

    def get_color(self):
        return hsv_to_rgb(self.h, self.s, self.v)

    def set_wheel_mode(self, mode):
        # "triangle" | "hsl-square" | "hsv-square" | "hls-triangle" | "rgb-slice"
        self.wheel_mode = mode
        self.update()

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
            case "hsv-square" | "hsl-square":
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

    def set_oklch(self, L, C, h, update_widget=True):
        """Direct OKLCh state — avoids HSV→RGB→OKLCh round-trip drift."""
        old_hue = self._oklch_h
        self._clear_drag_anchor()
        self._oklch_L = L
        self._oklch_C = C
        self._oklch_h = h
        if old_hue is None or abs(h - old_hue) > 0.01:
            self._invalidate_slice_caches()
            # A new hue also needs a new boundary and fallback image.
            if hasattr(self, "_cached_oklch_key"):
                delattr(self, "_cached_oklch_key")
            if hasattr(self, "_bdry_h"):
                delattr(self, "_bdry_h")
        if update_widget:
            self.update()

    def _oklch_boundary_data(self, h):
        """Pre-compute gamut boundary curve for a given hue.

        Returns (scale, boundary_points) where *boundary_points* is a list
        of 201 (C_max, L) pairs (L from 0 to 1, step 0.005) and *scale*
        maps chroma to pixels so the widest part fills the slice width.
        The result is cached so repeated calls at the same hue are free.
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
        # Scale: widest chroma fills the triangle radius (100 %, not 95 % —
        # the boundary line itself marks the gamut edge precisely).
        r = getattr(self, '_bdry_slice_r', None)
        if r is None:
            r = 130  # sensible default before first layout
        self._bdry_scale = (r * 1.0) / max(max_c, 0.001)
        return self._bdry_scale, points

    def _oklch_scale_for_hue(self, h, r):
        """Compute the C→pixel scale used by slice, indicator, and drag."""
        scale, _ = self._oklch_boundary_data(h)
        # Update the cached slice radius so boundary_data's scale stays accurate
        self._bdry_slice_r = r
        self._bdry_scale = (r * 1.0) / max(
            max((c for c, _ in self._bdry_points), default=0.001), 0.001)
        return self._bdry_scale

    def get_wheel_geometry(self):
        w = self.width()
        h = self.height()
        # Enlarge the wheel to touch the sides as much as possible
        size = w - 16
        cx = w / 2.0
        # Position near the top with a constant offset to align closely with the preview circles
        cy = size / 2.0 + 6.0
        
        outer_radius = size / 2.0 - 2.0
        ring_width = max(12.0, size * 0.08)
        inner_radius = outer_radius - ring_width
        triangle_radius = max(1.0, inner_radius - 3.0)
        
        return cx, cy, size, outer_radius, inner_radius, triangle_radius

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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        ringless = self._is_ringless()

        if ringless:
            # ── ringless: only the colour slice, no ring, no hue indicator ──
            sg = self.get_slice_geometry()
            slice_cx, slice_cy, slice_r = sg.center_x, sg.center_y, sg.radius
            if slice_r <= 1.0:
                return
        else:
            cx, cy, size, outer_radius, inner_radius, triangle_radius = self.get_wheel_geometry()
            if size <= 20:
                return
            slice_cx, slice_cy, slice_r = cx, cy, triangle_radius

            # 1) Draw Hue Ring with Caching (skip in oklch-slice mode — it has no ring)
            if self.wheel_mode != "oklch-slice":
                flip_h = self.cfg.get("flipColorWheelHorizontally", False)
                ring_key = (int(cx), int(cy), int(outer_radius), int(inner_radius), flip_h)
                if not hasattr(self, "_cached_ring_key") or self._cached_ring_key != ring_key or not hasattr(self, "_cached_ring_img") or self._cached_ring_img is None:
                    w = self.width()
                    h = self.height()
                    self._cached_ring_img = QImage(w, h, QImage.Format.Format_ARGB32)
                    self._cached_ring_img.fill(0)

                    p = QPainter(self._cached_ring_img)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)

                    if flip_h:
                        gradient = QConicalGradient(QPointF(cx, cy), 150.0)
                        for i in range(361):
                            gradient.setColorAt(i / 360.0, QColor.fromHsvF((360 - i) / 360.0, 1.0, 1.0))
                    else:
                        gradient = QConicalGradient(QPointF(cx, cy), 30.0)
                        for i in range(361):
                            gradient.setColorAt(i / 360.0, QColor.fromHsvF(i / 360.0, 1.0, 1.0))

                    # Calculate geometry
                    ring_width = outer_radius - inner_radius
                    r_mid = (outer_radius + inner_radius) / 2.0

                    # Draw ring using a thick pen with the conical gradient
                    pen = QPen(QBrush(gradient), ring_width)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(cx, cy), r_mid, r_mid)

                    # Draw thin gray outlines to eliminate aliasing/jagged edges
                    p.setPen(QPen(QColor(128, 128, 128, 90), 1.0))
                    p.drawEllipse(QPointF(cx, cy), outer_radius, outer_radius)
                    p.drawEllipse(QPointF(cx, cy), inner_radius, inner_radius)
                    p.end()

                    self._cached_ring_key = ring_key

                painter.drawImage(0, 0, self._cached_ring_img)
            else:
                # OKLCh mode: hue ring with fixed L=85%, C=0.4
                #（色环固定明度和彩度，仅响应色相变化）
                L_ring = 0.85
                C_ring = 0.4
                flip_h = self.cfg.get("flipColorWheelHorizontally", False)
                ring_key = (int(cx), int(cy), int(outer_radius), int(inner_radius), flip_h)
                if not hasattr(self, "_cached_oklch_ring_key") or self._cached_oklch_ring_key != ring_key or not hasattr(self, "_cached_oklch_ring_img"):
                    w = self.width()
                    h = self.height()
                    self._cached_oklch_ring_img = QImage(w, h, QImage.Format.Format_ARGB32)
                    self._cached_oklch_ring_img.fill(0)
                    p = QPainter(self._cached_oklch_ring_img)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    # Build OKLCh conical gradient
                    if flip_h:
                        gradient = QConicalGradient(QPointF(cx, cy), 150.0)
                        for i in range(361):
                            hue = (360 - i) % 360
                            c_safe = min(C_ring, find_max_oklch_c(L_ring, hue))
                            rr, gg, bb = oklch_to_rgb(L_ring, c_safe, hue)
                            gradient.setColorAt(i / 360.0, QColor(
                                max(0, min(255, int(rr))),
                                max(0, min(255, int(gg))),
                                max(0, min(255, int(bb)))))
                    else:
                        gradient = QConicalGradient(QPointF(cx, cy), 30.0)
                        for i in range(361):
                            hue = i
                            # Gamut-clamp C at this hue so ring colours match the
                            # in-gamut slice (oklch.com's range strips do the same).
                            c_safe = min(C_ring, find_max_oklch_c(L_ring, hue))
                            rr, gg, bb = oklch_to_rgb(L_ring, c_safe, hue)
                            gradient.setColorAt(i / 360.0, QColor(
                                max(0, min(255, int(rr))),
                                max(0, min(255, int(gg))),
                                max(0, min(255, int(bb)))))
                    ring_width = outer_radius - inner_radius
                    r_mid = (outer_radius + inner_radius) / 2.0
                    pen = QPen(QBrush(gradient), ring_width)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(cx, cy), r_mid, r_mid)
                    p.setPen(QPen(QColor(128, 128, 128, 90), 1.0))
                    p.drawEllipse(QPointF(cx, cy), outer_radius, outer_radius)
                    p.drawEllipse(QPointF(cx, cy), inner_radius, inner_radius)
                    p.end()
                    self._cached_oklch_ring_key = ring_key
                painter.drawImage(0, 0, self._cached_oklch_ring_img)

            # 3) Draw Hue Indicator on Ring
            self.draw_hue_indicator(painter, cx, cy, inner_radius, outer_radius)

        # ── 2) Draw colour slice (shared by full and ringless) ──
        if self.wheel_mode == "triangle":
            self.draw_triangle(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "hsv-square":
            self.draw_hsv_square(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "hls-triangle":
            self.draw_hls_triangle(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "rgb-slice":
            self.draw_rgb_slice(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "oklch-slice":
            self.draw_oklch_slice(painter, slice_cx, slice_cy, slice_r)
        else:
            self.draw_hsl_square(painter, slice_cx, slice_cy, slice_r)

        # ── 3) Draw internal indicator (shared) ──
        if self.wheel_mode == "triangle":
            self.draw_sv_indicator(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "hsv-square":
            self.draw_hsv_square_indicator(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "hls-triangle":
            self.draw_hls_indicator(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "rgb-slice":
            self.draw_rgb_indicator(painter, slice_cx, slice_cy, slice_r)
        elif self.wheel_mode == "oklch-slice":
            self.draw_oklch_indicator(painter, slice_cx, slice_cy, slice_r)
        else:
            self.draw_hsl_indicator(painter, slice_cx, slice_cy, slice_r)

    def draw_triangle(self, painter, cx, cy, r):
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
        
        # Create triangle path
        path = QPainterPath()
        path.moveTo(v0)
        path.lineTo(v1)
        path.lineTo(v2)
        path.closeSubpath()
        
        # Save painter state
        painter.save()
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Bounding rect
        rect = QRectF(cx - r - 2, cy - r - 2, r * 2 + 4, r * 2 + 4)
        
        # 1) Base Gradient: from White (v1) to Pure Color (v0)
        grad1 = QLinearGradient(v1, v0)
        grad1.setColorAt(0.0, QColor(255, 255, 255))
        # Pure HSV color
        pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
        grad1.setColorAt(1.0, QColor(pure_r, pure_g, pure_b))
        
        painter.setBrush(grad1)
        painter.drawRect(rect)
        
        # 2) Overlay Gradient: from Black (v2) to midpoint of v0-v1
        midpoint = QPointF((v0.x() + v1.x()) / 2.0, (v0.y() + v1.y()) / 2.0)
        grad2 = QLinearGradient(v2, midpoint)
        grad2.setColorAt(0.0, QColor(0, 0, 0, 255)) # Pure black
        grad2.setColorAt(1.0, QColor(0, 0, 0, 0))   # Transparent black
        
        painter.setBrush(grad2)
        painter.drawRect(rect)
        
        # Restore painter state
        painter.restore()

    def draw_hsl_square(self, painter, cx, cy, r):
        half = int(r / 1.414) - 2
        width = half * 2
        height = half * 2
        if width <= 0 or height <= 0:
            return
            
        # Check cache
        cache_key = (int(self.h), width, height, "square")
        prewarmed = self._prewarmed_slices.get("hsl-square")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            painter.drawImage(int(cx - half), int(cy - half), prewarmed["image"])
            return
        if self._cached_img_key == cache_key and self._cached_img is not None:
            painter.drawImage(int(cx - half), int(cy - half), self._cached_img)
            return
            
        ratio = self.devicePixelRatio()
        is_active = self.is_active_interaction()
        
        if is_active:
            subsample = 3
        else:
            subsample = 1
            
        sub_w = max(1, int(width * ratio) // subsample if is_active else int(width * ratio))
        sub_h = max(1, int(height * ratio) // subsample if is_active else int(height * ratio))
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        
        for y in range(sub_h):
            l_val = 1.0 - (y / float(sub_h - 1)) if sub_h > 1 else 0.5
            for x in range(sub_w):
                s_val = x / float(sub_w - 1) if sub_w > 1 else 0.5
                red, green, blue = colorsys.hls_to_rgb(self.h / 360.0, l_val, s_val)
                img.setPixelColor(x, y, QColor(int(red * 255), int(green * 255), int(blue * 255)))
                
        if is_active:
            final_img = img.scaled(int(width * ratio), int(height * ratio), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            final_img.setDevicePixelRatio(ratio)
        else:
            final_img = img
            final_img.setDevicePixelRatio(ratio)
            
        self._cached_img = final_img
        self._cached_img_key = cache_key
        
        painter.drawImage(int(cx - half), int(cy - half), final_img)

    def draw_hsv_square(self, painter, cx, cy, r):
        half = int(r / 1.414) - 2
        width = half * 2
        height = half * 2
        if width <= 0 or height <= 0:
            return
            
        # Check cache
        cache_key = (int(self.h), width, height, "hsv-square")
        prewarmed = self._prewarmed_slices.get("hsv-square")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            painter.drawImage(int(cx - half), int(cy - half), prewarmed["image"])
            painter.save()
            painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(int(cx - half), int(cy - half), width, height)
            painter.restore()
            return
        if self._cached_img_key == cache_key and self._cached_img is not None:
            painter.drawImage(int(cx - half), int(cy - half), self._cached_img)
            painter.save()
            painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(int(cx - half), int(cy - half), width, height)
            painter.restore()
            return
            
        ratio = self.devicePixelRatio()
        is_active = self.is_active_interaction()
        
        if is_active:
            subsample = 3
        else:
            subsample = 1
            
        sub_w = max(1, int(width * ratio) // subsample if is_active else int(width * ratio))
        sub_h = max(1, int(height * ratio) // subsample if is_active else int(height * ratio))
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        
        for y in range(sub_h):
            v_val = 1.0 - (y / float(sub_h - 1)) if sub_h > 1 else 0.5
            for x in range(sub_w):
                s_val = x / float(sub_w - 1) if sub_w > 1 else 0.5
                red, green, blue = hsv_to_rgb(self.h, s_val * 100.0, v_val * 100.0)
                img.setPixelColor(x, y, QColor(red, green, blue))
                
        if is_active:
            final_img = img.scaled(int(width * ratio), int(height * ratio), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            final_img.setDevicePixelRatio(ratio)
        else:
            final_img = img
            final_img.setDevicePixelRatio(ratio)
            
        self._cached_img = final_img
        self._cached_img_key = cache_key
        
        painter.drawImage(int(cx - half), int(cy - half), final_img)
        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(int(cx - half), int(cy - half), width, height)
        painter.restore()

    def draw_hue_indicator(self, painter, cx, cy, inner_r, outer_r):
        # In OKLCh mode the ring uses OKLCh hue angles, not HSV
        hue = self._oklch_h if (self.wheel_mode == "oklch-slice" and self._oklch_h is not None) else self.h
        if self.cfg.get("flipColorWheelHorizontally", False):
            angle_deg = (150.0 - hue) % 360.0
        else:
            angle_deg = (hue + 30.0) % 360.0
        rad = math.radians(angle_deg)
        r = (inner_r + outer_r) / 2.0
        pos_x = cx + r * math.cos(rad)
        pos_y = cy - r * math.sin(rad)
        
        pos = QPointF(pos_x, pos_y)
        
        # Calculate indicator radius to be perfectly tangent to the color wheel ring width
        ring_width = outer_r - inner_r
        indicator_r = ring_width / 2.0
        
        # Outer black ring (width 2.0)
        painter.setPen(QPen(QColor(0, 0, 0), 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(pos, indicator_r - 1.0, indicator_r - 1.0)
        
        # Inner white ring (width 1.0) for maximum contrast on all gradient colors
        painter.setPen(QPen(QColor(255, 255, 255), 1.0))
        painter.drawEllipse(pos, indicator_r - 2.0, indicator_r - 2.0)

    def draw_indicator_ring(self, painter, pos):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Outer black border
        painter.setPen(QPen(QColor(0, 0, 0, 180), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(pos, 5, 5)
        
        # Inner white indicator ring
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.drawEllipse(pos, 4, 4)
        
        painter.restore()

    def draw_sv_indicator(self, painter, cx, cy, r):
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
        
        # Map S and V back to barycentric coordinates
        v_val = self.v / 100.0
        s_val = self.s / 100.0
        
        w0 = s_val * v_val
        w2 = 1.0 - v_val
        w1 = 1.0 - w0 - w2
        
        pos_x = w0 * v0.x() + w1 * v1.x() + w2 * v2.x()
        pos_y = w0 * v0.y() + w1 * v1.y() + w2 * v2.y()
        
        pos = QPointF(pos_x, pos_y)
        self.draw_indicator_ring(painter, pos)

    def draw_hsl_indicator(self, painter, cx, cy, r):
        # Convert HSV to HSL
        v_val = self.v / 100.0
        s_val = self.s / 100.0
        
        l_val = v_val * (1.0 - s_val / 2.0)
        if 0.0 < l_val < 1.0:
            hsl_s = (v_val - l_val) / min(l_val, 1.0 - l_val)
        else:
            hsl_s = 0.0
            
        half = int(r / 1.414) - 2
        
        pos_x = cx - half + hsl_s * (half * 2)
        pos_y = cy - half + (1.0 - l_val) * (half * 2)
        
        pos = QPointF(pos_x, pos_y)
        self.draw_indicator_ring(painter, pos)

    def draw_hsv_square_indicator(self, painter, cx, cy, r):
        half = int(r / 1.414) - 2
        
        pos_x = cx - half + (self.s / 100.0) * (half * 2)
        pos_y = cy - half + (1.0 - self.v / 100.0) * (half * 2)
        
        pos = QPointF(pos_x, pos_y)
        self.draw_indicator_ring(painter, pos)

    def is_point_in_triangle(self, px, py, v0, v1, v2):
        denom = (v1.y() - v2.y()) * (v0.x() - v2.x()) + (v2.x() - v1.x()) * (v0.y() - v2.y())
        if abs(denom) < 1e-6:
            return False
        w0 = ((v1.y() - v2.y()) * (px - v2.x()) + (v2.x() - v1.x()) * (py - v2.y())) / denom
        w1 = ((v2.y() - v0.y()) * (px - v2.x()) + (v0.x() - v2.x()) * (py - v2.y())) / denom
        w2 = 1.0 - w0 - w1
        return (w0 >= -0.01) and (w1 >= -0.01) and (w2 >= -0.01)

    def draw_hls_triangle(self, painter, cx, cy, r):
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
        cache_key = (self.h, r, round(cx, 3), round(cy, 3), "hls")
        prewarmed = self._prewarmed_slices.get("hls-triangle")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            painter.drawImage(int(prewarmed["min_x"]), int(prewarmed["min_y"]), prewarmed["image"])
            self._draw_hls_triangle_outline(painter, v0, v1, v2, 1)
            return
        if hasattr(self, "_cached_hls_key") and self._cached_hls_key == cache_key and hasattr(self, "_cached_hls_img"):
            painter.drawImage(int(self._cached_hls_minx), int(self._cached_hls_miny), self._cached_hls_img)
            is_active = self.is_active_interaction()
            ss = 3 if (is_active and self.dragging != "hls-triangle") else 1
            self._draw_hls_triangle_outline(painter, v0, v1, v2, ss)
            return
            
        hy = r * 0.866
        px_left = cx - 0.5 * r
        
        min_x = int(math.floor(min(v0.x(), v1.x(), v2.x())))
        max_x = int(math.ceil(max(v0.x(), v1.x(), v2.x())))
        min_y = int(math.floor(min(v0.y(), v1.y(), v2.y())))
        max_y = int(math.ceil(max(v0.y(), v1.y(), v2.y())))
        width = max_x - min_x
        height = max_y - min_y
        
        if width <= 0 or height <= 0:
            return
            
        # Keep every active drag responsive; end_drag() invalidates the cache
        # so the next paint restores the full-quality image.
        is_active = self.is_active_interaction()
        subsample = 3 if is_active else 1
            
        sub_w = max(1, (width + subsample - 1) // subsample)
        sub_h = max(1, (height + subsample - 1) // subsample)
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        img.fill(0)
        
        for y in range(sub_h):
            py = min_y + y * subsample
            l_val = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
            px_right = px_left + 3.0 * r * (0.5 - abs(l_val - 0.5))
            row_w = px_right - px_left
            first_colored = -1
            
            for x in range(sub_w):
                px = min_x + x * subsample
                if px >= px_left and px <= px_right and self.is_point_in_triangle(px, py, v0, v1, v2):
                    s_val = (px - px_left) / row_w if row_w > 0.001 else 0.0
                    s_val = max(0.0, min(1.0, s_val))
                    red, green, blue = colorsys.hls_to_rgb(self.h / 360.0, l_val, s_val)
                    color = QColor(int(red * 255), int(green * 255), int(blue * 255))
                    img.setPixelColor(x, y, color)
                    if first_colored < 0:
                        first_colored = x
                        
            # Fill gap on the left — caused by integer pixel positions vs fractional triangle edge
            if first_colored > 0:
                fill = img.pixelColor(first_colored, y)
                for x in range(first_colored):
                    img.setPixelColor(x, y, fill)
                    
        self._cached_hls_key = cache_key
        if subsample > 1:
            self._cached_hls_img = img.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self._cached_hls_img = img
        self._cached_hls_minx = min_x
        self._cached_hls_miny = min_y
        
        painter.drawImage(min_x, min_y, self._cached_hls_img)
        self._draw_hls_triangle_outline(painter, v0, v1, v2, subsample)

    def _draw_hls_triangle_outline(self, painter, v0, v1, v2, subsample=1):
        path = QPainterPath()
        path.moveTo(v0)
        path.lineTo(v1)
        path.lineTo(v2)
        path.closeSubpath()
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def draw_hls_indicator(self, painter, cx, cy, r):
        hy = r * 0.866
        px_left = cx - 0.5 * r
        
        rgb_r, rgb_g, rgb_b = self.get_color()
        h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(rgb_r / 255.0, rgb_g / 255.0, rgb_b / 255.0)
        
        py = cy + hy * (1.0 - 2.0 * l_hsl)
        px_right = px_left + 3.0 * r * (0.5 - abs(l_hsl - 0.5))
        row_w = px_right - px_left
        px = px_left + s_hsl * row_w
        
        pos = QPointF(px, py)
        self.draw_indicator_ring(painter, pos)

    def draw_rgb_slice(self, painter, cx, cy, r):
        cache_key = (self.h, r, round(cx, 3), round(cy, 3), "rgb")
        prewarmed = self._prewarmed_slices.get("rgb-slice")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            edge_x = prewarmed.get("edge_x")
            if edge_x is not None:
                self._cached_rgb_edge = (edge_x, prewarmed["min_x"], prewarmed["min_y"], prewarmed["min_y"] + prewarmed["height"], prewarmed["height"])
            painter.drawImage(int(prewarmed["min_x"]), int(prewarmed["min_y"]), prewarmed["image"])
            self._draw_slice_outline(painter, "rgb")
            return
        if hasattr(self, "_cached_rgb_key") and self._cached_rgb_key == cache_key and hasattr(self, "_cached_rgb_img"):
            painter.drawImage(int(self._cached_rgb_minx), int(self._cached_rgb_miny), self._cached_rgb_img)
            self._draw_slice_outline(painter, "rgb")
            return
            
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))
        max_x = int(math.ceil(cx + r * 1.5))
        min_y = int(math.floor(cy - hy))
        max_y = int(math.ceil(cy + hy))
        width = max_x - min_x
        height = max_y - min_y
        
        if width <= 0 or height <= 0:
            return
            
        # RGB?Lab conversion is the most expensive slice renderer, so use a
        # slightly coarser active preview to keep the indicator under one frame.
        is_active = self.is_active_interaction()
        subsample = 5 if is_active else 1
            
        sub_w = max(1, (width + subsample - 1) // subsample)
        sub_h = max(1, (height + subsample - 1) // subsample)
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        img.fill(0)
        
        pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
        l_p, a_p, b_p = rgb_to_lab(pure_r, pure_g, pure_b)
        C_pure = math.sqrt(a_p * a_p + b_p * b_p)
        a_dir = a_p / C_pure if C_pure > 0.001 else 0.0
        b_dir = b_p / C_pure if C_pure > 0.001 else 0.0

        # Sample max chroma at multiple L and scale to fill available width
        max_c = max(
            find_max_lab_c(20, a_dir, b_dir),
            find_max_lab_c(50, a_dir, b_dir),
            find_max_lab_c(80, a_dir, b_dir),
        )
        scale = (r * 1.05) / max(max_c, 0.001)

        sub_edge_x = [min_x] * sub_h
        
        for y in range(sub_h):
            py = min_y + y * subsample
            L = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
            L_val = L * 100.0
            
            for x in range(sub_w):
                px = min_x + x * subsample
                C = (px - min_x) / scale
                a_val = C * a_dir
                b_val = C * b_dir
                
                rgb_r, rgb_g, rgb_b = lab_to_rgb(L_val, a_val, b_val)
                
                if (0.0 <= rgb_r <= 255.0 and
                    0.0 <= rgb_g <= 255.0 and
                    0.0 <= rgb_b <= 255.0):
                    
                    img.setPixelColor(x, y, QColor(
                        max(0, min(255, int(rgb_r))),
                        max(0, min(255, int(rgb_g))),
                        max(0, min(255, int(rgb_b)))
                    ))
                    if px > sub_edge_x[y]:
                        sub_edge_x[y] = px
                        
        self._cached_rgb_key = cache_key
        if subsample > 1:
            self._cached_rgb_img = img.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self._cached_rgb_img = img
        self._cached_rgb_minx = min_x
        self._cached_rgb_miny = min_y
        
        painter.drawImage(min_x, min_y, self._cached_rgb_img)
        
        # Save edge data and draw outline
        edge_x = [min_x] * height
        for y in range(height):
            sub_y = min(sub_h - 1, y // subsample) if subsample > 1 else y
            edge_x[y] = sub_edge_x[sub_y]
        self._cached_rgb_edge = (edge_x, min_x, min_y, max_y, height)
        self._draw_slice_outline(painter, "rgb")

    def _draw_slice_outline(self, painter, tag):
        """Draw gamut boundary outline from cached edge data."""
        attr_key = f"_cached_{tag}_edge"
        if not hasattr(self, attr_key):
            return
        edge_x, min_x, min_y, max_y, height = getattr(self, attr_key)
        path = QPainterPath()
        path.moveTo(min_x, min_y)
        for y in range(height):
            path.lineTo(edge_x[y], y + min_y)
        path.lineTo(min_x, max_y)
        path.closeSubpath()
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def draw_rgb_indicator(self, painter, cx, cy, r):
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))
        pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
        _, a_pure, b_pure = rgb_to_lab(pure_r, pure_g, pure_b)
        C_pure = math.sqrt(a_pure * a_pure + b_pure * b_pure)
        a_dir = a_pure / C_pure if C_pure > 0.001 else 0.0
        b_dir = b_pure / C_pure if C_pure > 0.001 else 0.0
        max_c = max(find_max_lab_c(20, a_dir, b_dir), find_max_lab_c(50, a_dir, b_dir), find_max_lab_c(80, a_dir, b_dir))
        scale = (r * 1.05) / max(max_c, 0.001)

        # Use exact drag position if mid-drag, otherwise compute from current color
        if getattr(self, '_drag_slice', '') == "rgb" and hasattr(self, '_drag_C'):
            C = self._drag_C
            L = self._drag_L
        else:
            rgb_r, rgb_g, rgb_b = self.get_color()
            l_lab, a_lab, b_lab = rgb_to_lab(rgb_r, rgb_g, rgb_b)
            C = math.sqrt(a_lab * a_lab + b_lab * b_lab)
            L = l_lab

        px = min_x + C * scale
        py = cy + hy * (1.0 - 2.0 * (L / 100.0))
        
        pos = QPointF(px, py)
        self.draw_indicator_ring(painter, pos)

    def _is_point_in_active_slice(self, px: float, py: float, cx: float, cy: float, r: float) -> bool:
        """Return True when (px, py) is inside the current mode's slice bounds.

        Unknown wheel modes are rejected — a click in an unrecognised mode
        must never start dragging.
        """
        match self.wheel_mode:
            case "hsv-square" | "hsl-square":
                half = int(r / 1.414) - 2
                return abs(px - cx) <= half and abs(py - cy) <= half
            case "triangle" | "hls-triangle":
                v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
                return self.is_point_in_triangle(px, py, v0, v1, v2)
            case "oklch-slice":
                hy = r * 0.866
                min_x = cx - r * 0.5
                max_x = cx + r * (0.5 if self._is_ringless() else 1.5)
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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._clear_drag_anchor()  # reset indicator mode on new interaction
            ringless = self._is_ringless()

            if ringless:
                # ── ringless: no hue ring; only slice-drag possible ──
                sg = self.get_slice_geometry()
                pos = event.position()
                px, py = pos.x(), pos.y()
                if not self._is_point_in_active_slice(px, py, sg.center_x, sg.center_y, sg.radius):
                    return  # click outside slice — no drag starts

                # Dispatch to mode-specific handler (same logic as full mode)
                if self.wheel_mode in ("triangle", "hls-triangle"):
                    self.dragging = self.wheel_mode
                    if self.wheel_mode == "triangle":
                        self.handle_triangle_drag(px, py, sg.center_x, sg.center_y, sg.radius)
                    else:
                        self.handle_hls_triangle_drag(px, py, sg.center_x, sg.center_y, sg.radius)
                elif self.wheel_mode == "rgb-slice":
                    self.dragging = "rgb-slice"
                    self.handle_rgb_slice_drag(px, py, sg.center_x, sg.center_y, sg.radius)
                elif self.wheel_mode == "oklch-slice":
                    self.dragging = "oklch-slice"
                    self.handle_oklch_slice_drag(px, py, sg.center_x, sg.center_y, sg.radius)
                else:
                    half = int(sg.radius / 1.414) - 2
                    if self.wheel_mode == "hsv-square":
                        self.dragging = "hsv-square"
                        self.handle_hsv_square_drag(px, py, sg.center_x, sg.center_y, half)
                    else:
                        self.dragging = "square"
                        self.handle_square_drag(px, py, sg.center_x, sg.center_y, half)

                if self.dragging:
                    self.setCursor(Qt.CursorShape.BlankCursor)
                return

            # ── full mode: legacy flow ──
            cx, cy, _, outer_radius, inner_radius, triangle_radius = self.get_wheel_geometry()
            pos = event.position()
            dx = pos.x() - cx
            dy = pos.y() - cy
            d = math.sqrt(dx*dx + dy*dy)

            if inner_radius <= d <= outer_radius + 4:
                self.dragging = "hue"
                self.handle_hue_drag(pos.x(), pos.y(), cx, cy)
            elif d < inner_radius:
                if self.wheel_mode in ("triangle", "hls-triangle"):
                    self.dragging = self.wheel_mode
                    if self.wheel_mode == "triangle":
                        self.handle_triangle_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
                    else:
                        self.handle_hls_triangle_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
                elif self.wheel_mode == "rgb-slice":
                    self.dragging = "rgb-slice"
                    self.handle_rgb_slice_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
                elif self.wheel_mode == "oklch-slice":
                    self.dragging = "oklch-slice"
                    self.handle_oklch_slice_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
                else:
                    half = int(triangle_radius / 1.414) - 2
                    if self.wheel_mode == "hsv-square":
                        self.dragging = "hsv-square"
                        self.handle_hsv_square_drag(pos.x(), pos.y(), cx, cy, half)
                    else:
                        self.dragging = "square"
                        self.handle_square_drag(pos.x(), pos.y(), cx, cy, half)

            if self.dragging and self.dragging != "hue":
                self.setCursor(Qt.CursorShape.BlankCursor)

    def mouseMoveEvent(self, event):
        if self.dragging:
            ringless = self._is_ringless()
            if ringless:
                sg = self.get_slice_geometry()
                pos = event.position()
                px, py = pos.x(), pos.y()
                cx, cy, r = sg.center_x, sg.center_y, sg.radius
            else:
                cx, cy, _, _, _, triangle_radius = self.get_wheel_geometry()
                pos = event.position()
                px, py = pos.x(), pos.y()
                r = triangle_radius

            if self.dragging == "hue":
                self.handle_hue_drag(px, py, cx, cy)
            elif self.dragging == "triangle":
                self.handle_triangle_drag(px, py, cx, cy, r)
            elif self.dragging == "hls-triangle":
                self.handle_hls_triangle_drag(px, py, cx, cy, r)
            elif self.dragging == "rgb-slice":
                self.handle_rgb_slice_drag(px, py, cx, cy, r)
            elif self.dragging == "oklch-slice":
                self.handle_oklch_slice_drag(px, py, cx, cy, r)
            elif self.dragging == "square":
                half = int(r / 1.414) - 2
                self.handle_square_drag(px, py, cx, cy, half)
            elif self.dragging == "hsv-square":
                half = int(r / 1.414) - 2
                self.handle_hsv_square_drag(px, py, cx, cy, half)

    def mouseReleaseEvent(self, event):
        self.end_drag()

    def end_drag(self):
        self.dragging = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        # Clear all caches to force a high-quality redraw on release
        self._cached_img_key = None
        if hasattr(self, "_cached_hls_key"):
            delattr(self, "_cached_hls_key")
        if hasattr(self, "_cached_rgb_key"):
            delattr(self, "_cached_rgb_key")
        if hasattr(self, "_cached_oklch_key"):
            delattr(self, "_cached_oklch_key")
        # Keep the last exact slice anchor through the release repaint.
        # Clearing it here makes the indicator round-trip through quantized
        # RGB/HSV and visibly jump away from the point the user selected.
        self._drag_scale = None
        self.update()
        self.interactionFinished.emit()

    def handle_hue_drag(self, px, py, cx, cy):
        # A hue change changes every prewarmed slice image; invalidate the
        # current generation so an older worker result cannot be installed
        # under the new hue key.
        self._invalidate_slice_caches()
        dy = -(py - cy)
        dx = px - cx
        angle = math.atan2(dy, dx)
        deg = math.degrees(angle)
        if deg < 0:
            deg += 360.0

        if self.wheel_mode == "oklch-slice":
            # Mouse angle maps directly to OKLCh hue on the OKLCh ring
            # (same mapping as the ConicalGradient which starts at 30°)
            if self.cfg.get("flipColorWheelHorizontally", False):
                oklch_h = (150.0 - deg) % 360.0
            else:
                oklch_h = (deg - 30.0) % 360.0
            if oklch_h < 0:
                oklch_h += 360.0
            self._oklch_h = oklch_h
            self._drag_oklch_h = None

            # Derive ring L, C to compute the colour at this hue
            if hasattr(self, '_drag_L') and self._drag_L is not None:
                L_ring, C_ring = self._drag_L, self._drag_C
            elif self._oklch_L is not None:
                L_ring, C_ring = self._oklch_L, self._oklch_C
            else:
                cr, cg, cb = self.get_color()
                L_ring, C_ring, _ = rgb_to_oklch(cr, cg, cb)

            # Keep _oklch_* triad in sync so on_wheel_color_changed can pass
            # the exact OKLCh values to update_ui_colors without an RGB
            # round-trip (which would shift the hue ~0.2° on the H slider).
            self._oklch_L = L_ring
            self._oklch_C = C_ring

            rr, gg, bb = oklch_to_rgb(L_ring, C_ring, oklch_h)
            r = max(0, min(255, int(rr)))
            g = max(0, min(255, int(gg)))
            b = max(0, min(255, int(bb)))
            self.h, self.s, self.v = rgb_to_hsv(r, g, b)
            if self.s > 0.5:
                self._last_hue = self.h

            if hasattr(self, "_cached_oklch_key"):
                delattr(self, "_cached_oklch_key")
            if hasattr(self, "_bdry_h"):
                delattr(self, "_bdry_h")
        else:
            if self.cfg.get("flipColorWheelHorizontally", False):
                self.h = (150.0 - deg) % 360.0
            else:
                self.h = (deg - 30.0) % 360.0
            if self.h < 0:
                self.h += 360.0
            if self.s > 0.5:
                self._last_hue = self.h

        self.update()
        r, g, b = self.get_color()
        self.colorChanged.emit(r, g, b)

    def handle_triangle_drag(self, px, py, cx, cy, r):
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
        px, py = project_point_to_triangle(px, py, v0, v1, v2)
        w0, w1, w2 = self.get_barycentric_coords(px, py, v0, v1, v2)
        
        v_val = max(0.001, 1.0 - w2)
        s_val = w0 / v_val
        
        self.s = max(0.0, min(100.0, s_val * 100.0))
        self.v = max(0.0, min(100.0, v_val * 100.0))
        self.update()
        r_val, g, b = self.get_color()
        self.colorChanged.emit(r_val, g, b)

    def handle_hls_triangle_drag(self, px, py, cx, cy, r):
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
        px, py = project_point_to_triangle(px, py, v0, v1, v2)
        
        hy = r * 0.866
        px_left = cx - 0.5 * r
        l_val = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        px_right = px_left + 3.0 * r * (0.5 - abs(l_val - 0.5))
        row_w = px_right - px_left
        s_val = (px - px_left) / row_w if row_w > 0.001 else 0.0
        s_val = max(0.0, min(1.0, s_val))
        
        red, green, blue = colorsys.hls_to_rgb(self.h / 360.0, l_val, s_val)
        
        # Calculate HSV using high-precision floats to bypass integer quantization
        h_new, s_new, v_new = hls_to_hsv_floats(self.h, l_val, s_val)
        self.s = s_new
        self.v = v_new
        self.update()
        self.colorChanged.emit(int(red * 255), int(green * 255), int(blue * 255))

    def handle_rgb_slice_drag(self, px, py, cx, cy, r):
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))

        # Cache scale during drag — self.h and r are constant while in slice
        if not hasattr(self, '_drag_scale') or self._drag_scale is None:
            pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
            l_p, a_p, b_p = rgb_to_lab(pure_r, pure_g, pure_b)
            C_pure = math.sqrt(a_p * a_p + b_p * b_p)
            self._drag_a_dir = a_p / C_pure if C_pure > 0.001 else 0.0
            self._drag_b_dir = b_p / C_pure if C_pure > 0.001 else 0.0
            max_c = max(find_max_lab_c(20, self._drag_a_dir, self._drag_b_dir),
                        find_max_lab_c(50, self._drag_a_dir, self._drag_b_dir),
                        find_max_lab_c(80, self._drag_a_dir, self._drag_b_dir))
            self._drag_scale = (r * 1.05) / max(max_c, 0.001)
        scale = self._drag_scale
        a_dir = self._drag_a_dir
        b_dir = self._drag_b_dir
        
        L = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        L_val = L * 100.0
        
        C_max = find_max_lab_c(L_val, a_dir, b_dir)
        C_raw = (px - min_x) / scale
        if C_raw > C_max and C_max > 0:
            # Mouse outside gamut — snap to nearest point on boundary
            L, L_val = self._snap_to_boundary_rgb(L, L_val, C_raw, a_dir, b_dir, scale, px, py, cx, cy, hy, min_x)
            C_max = find_max_lab_c(L_val, a_dir, b_dir)
        C = max(0.0, min(C_max, (px - min_x) / scale))
        
        a_val = C * a_dir
        b_val = C * b_dir
        
        rgb_r, rgb_g, rgb_b = lab_to_rgb(L_val, a_val, b_val)
        rgb_r_clamped = max(0.0, min(255.0, rgb_r))
        rgb_g_clamped = max(0.0, min(255.0, rgb_g))
        rgb_b_clamped = max(0.0, min(255.0, rgb_b))
        
        # Calculate HSV using high-precision floats to bypass integer quantization
        h_new, s_new, v_new = rgb_to_hsv(rgb_r_clamped, rgb_g_clamped, rgb_b_clamped)
        self.s = s_new
        self.v = v_new
        # Store exact C/L for pixel-perfect indicator positioning
        self._drag_C = C
        self._drag_L = L_val
        self._drag_slice = "rgb"
        self.update()
        self.colorChanged.emit(int(rgb_r_clamped), int(rgb_g_clamped), int(rgb_b_clamped))

    def handle_square_drag(self, px, py, cx, cy, half):
        rel_x = px - (cx - half)
        rel_y = py - (cy - half)
        
        s_val = max(0.0, min(1.0, rel_x / float(half * 2)))
        l_val = max(0.0, min(1.0, 1.0 - rel_y / float(half * 2)))
        
        # Convert HSL to HSV
        v_val = l_val + s_val * min(l_val, 1.0 - l_val)
        if v_val > 0.0:
            hsv_s = 2.0 * (1.0 - l_val / v_val)
        else:
            hsv_s = 0.0
            
        self.s = max(0.0, min(100.0, hsv_s * 100.0))
        self.v = max(0.0, min(100.0, v_val * 100.0))
        self.update()
        r, g, b = self.get_color()
        self.colorChanged.emit(r, g, b)

    def handle_hsv_square_drag(self, px, py, cx, cy, half):
        rel_x = px - (cx - half)
        rel_y = py - (cy - half)
        
        s_val = max(0.0, min(1.0, rel_x / float(half * 2)))
        v_val = max(0.0, min(1.0, 1.0 - rel_y / float(half * 2)))
        
        self.s = max(0.0, min(100.0, s_val * 100.0))
        self.v = max(0.0, min(100.0, v_val * 100.0))
        self.update()
        r, g, b = self.get_color()
        self.colorChanged.emit(r, g, b)

    def draw_oklch_slice(self, painter, cx, cy, r):
        # Derive OKLCh hue from drag cache, stored state, or current RGB color
        oklch_h = getattr(self, '_drag_oklch_h', None)
        if oklch_h is None:
            oklch_h = self._oklch_h
        if oklch_h is None:
            rgb_r, rgb_g, rgb_b = self.get_color()
            _, _, oklch_h = rgb_to_oklch(rgb_r, rgb_g, rgb_b)

        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))
        max_x = int(math.ceil(cx + r * 0.5))
        min_y = int(math.floor(cy - hy))
        max_y = int(math.ceil(cy + hy))
        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return

        cache_key = (round(oklch_h, 1), r, round(cx, 3), round(cy, 3), "oklch")
        prewarmed = self._prewarmed_slices.get("oklch-slice")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            painter.drawImage(int(prewarmed["min_x"]), int(prewarmed["min_y"]), prewarmed["image"])
            return
        img_ready = (hasattr(self, "_cached_oklch_key")
                     and self._cached_oklch_key == cache_key
                     and hasattr(self, "_cached_oklch_img"))

        if not img_ready:
            # Keep every active drag responsive; end_drag() invalidates the cache
            # so the next paint restores the full-quality image.
            is_active = self.is_active_interaction()
            subsample = 3 if is_active else 1
            sub_w = max(1, (width + subsample - 1) // subsample)
            sub_h = max(1, (height + subsample - 1) // subsample)

            img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
            img.fill(0)

            scale = self._oklch_scale_for_hue(oklch_h, r)

            for y in range(sub_h):
                py = min_y + y * subsample
                L = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))

                for x in range(sub_w):
                    px = min_x + x * subsample
                    C = max(0.0, (px - min_x) / scale)

                    rgb_r, rgb_g, rgb_b = oklch_to_rgb(L, C, oklch_h)

                    if not (0.0 <= rgb_r <= 255.0 and
                            0.0 <= rgb_g <= 255.0 and
                            0.0 <= rgb_b <= 255.0):
                        continue

                    r8 = max(0, min(255, int(rgb_r)))
                    g8 = max(0, min(255, int(rgb_g)))
                    b8 = max(0, min(255, int(rgb_b)))

                    img.setPixelColor(x, y, QColor(r8, g8, b8))

            self._cached_oklch_key = cache_key
            if subsample > 1:
                self._cached_oklch_img = img.scaled(
                    width, height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation)
            else:
                self._cached_oklch_img = img
            self._cached_oklch_minx = min_x
            self._cached_oklch_miny = min_y

        painter.drawImage(int(self._cached_oklch_minx),
                          int(self._cached_oklch_miny),
                          self._cached_oklch_img)

        # ── sRGB gamut boundary outline ──
        # Trace the max-C curve rightward, then close along C=0 (the
        # neutral axis).  201-point boundary gives a sharp peak.
        scale = getattr(self, '_bdry_scale', None)
        if scale is None:
            scale = self._oklch_scale_for_hue(oklch_h, r)
        _, bdry = self._oklch_boundary_data(oklch_h)

        path = QPainterPath()
        # 1) Gamut edge: L=0 → L=1 (left to right along the max-C curve)
        for i, (C_max, L_val) in enumerate(bdry):
            px_b = min_x + C_max * scale
            py_b = cy + hy * (1.0 - 2.0 * L_val)
            if i == 0:
                path.moveTo(px_b, py_b)
            else:
                path.lineTo(px_b, py_b)
        # 2) Return along C=0 (neutral axis) — L=1 → L=0
        px_c0_top = min_x  # C=0, L=1
        py_c0_top = cy + hy * (1.0 - 2.0 * 1.0)  # cy - hy
        px_c0_bot = min_x  # C=0, L=0
        py_c0_bot = cy + hy * (1.0 - 2.0 * 0.0)  # cy + hy
        path.lineTo(px_c0_top, py_c0_top)
        path.lineTo(px_c0_bot, py_c0_bot)
        path.closeSubpath()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 100), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.restore()

    def draw_oklch_indicator(self, painter, cx, cy, r):
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))

        # Hue from drag cache or stored OKLCh state (avoids round-trip drift)
        oklch_h = getattr(self, '_drag_oklch_h', None)
        if oklch_h is None:
            oklch_h = self._oklch_h

        if oklch_h is None:
            rgb_r, rgb_g, rgb_b = self.get_color()
            _, _, oklch_h = rgb_to_oklch(rgb_r, rgb_g, rgb_b)

        scale = self._oklch_scale_for_hue(oklch_h, r)

        if getattr(self, '_drag_slice', '') == "oklch" and hasattr(self, '_drag_C'):
            C = self._drag_C
            L = self._drag_L
        elif self._oklch_C is not None and self._oklch_L is not None:
            # Use direct OKLCh state (set by main_window) — no RGB round-trip
            C = self._oklch_C
            L = self._oklch_L
        else:
            rgb_r, rgb_g, rgb_b = self.get_color()
            L_ok, C_ok, h_ok = rgb_to_oklch(rgb_r, rgb_g, rgb_b)
            C = min(C_ok, find_max_oklch_c(L_ok, oklch_h))
            L = L_ok

        px = min_x + C * scale
        py = cy + hy * (1.0 - 2.0 * L)

        self.draw_indicator_ring(painter, QPointF(px, py))

    def handle_oklch_slice_drag(self, px, py, cx, cy, r):
        hy = r * 0.866
        min_x = int(math.floor(cx - r * 0.5))
        if not hasattr(self, '_drag_scale') or self._drag_scale is None:
            # Lock in OKLCh hue from stored state — avoids RGB→OKLCh
            # round-trip drift (~0.2° per interaction). Fall back to
            # rgb_to_oklch only when _oklch_h has never been set.
            oklch_h = self._oklch_h
            if oklch_h is None:
                cr, cg, cb = self.get_color()
                _, _, oklch_h = rgb_to_oklch(cr, cg, cb)
            self._drag_scale = self._oklch_scale_for_hue(oklch_h, r)
            self._drag_oklch_h = oklch_h
        scale = self._drag_scale
        oklch_h = self._drag_oklch_h

        L = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        C_max = find_max_oklch_c(L, oklch_h)
        C_raw = (px - min_x) / scale
        if C_raw > C_max and C_max > 0:
            L = self._snap_to_boundary_oklch(L, C_raw, scale, px, py, cx, cy, hy, min_x, oklch_h)
            C_max = find_max_oklch_c(L, oklch_h)
        C = max(0.0, min(C_max, (px - min_x) / scale))

        rgb_r, rgb_g, rgb_b = oklch_to_rgb(L, C, oklch_h)
        rgb_r_clamped = max(0.0, min(255.0, rgb_r))
        rgb_g_clamped = max(0.0, min(255.0, rgb_g))
        rgb_b_clamped = max(0.0, min(255.0, rgb_b))

        h_new, s_new, v_new = rgb_to_hsv(rgb_r_clamped, rgb_g_clamped, rgb_b_clamped)
        if s_new > 0.5:
            self._last_hue = h_new
            self.h = h_new
        else:
            # C ≈ 0: rgb_to_hsv returns h=0 for achromatic colors.
            # Probe the hue direction at a fixed mid-gray point with just
            # enough chroma to survive every noise filter and give atan2 a
            # clean signal, without being so saturated that the hue mapping
            # drifts noticeably from the C≈0 region (avoids a "colour jump"
            # when the user barely crosses the C=0 boundary).
            rr_eps, gg_eps, bb_eps = oklch_to_rgb(0.5, 0.02, oklch_h)
            h_eps, _, _ = rgb_to_hsv(
                max(0.0, min(255.0, rr_eps)),
                max(0.0, min(255.0, gg_eps)),
                max(0.0, min(255.0, bb_eps)))
            self.h = h_eps
        self.s = s_new
        self.v = v_new
        self._drag_C = C
        self._drag_L = L
        self._drag_slice = "oklch"
        # Keep _oklch_* in sync so hue ring colors match live drag position
        self._oklch_L = L
        self._oklch_C = C
        self._oklch_h = oklch_h
        self.update()
        self.colorChanged.emit(int(rgb_r_clamped), int(rgb_g_clamped), int(rgb_b_clamped))

    def _snap_to_boundary_rgb(self, L, L_val, C_raw, a_dir, b_dir, scale, px, py, cx, cy, hy, min_x):
        """Find closest in-gamut (C,L) pair when mouse is outside RGB gamut."""
        # Coarse pass
        best_L, best_dist = L, float('inf')
        for t in [i / 25.0 for i in range(26)]:
            t_val = t * 100.0
            Cb = find_max_lab_c(t_val, a_dir, b_dir)
            bx = min_x + Cb * scale
            by = cy + hy * (1.0 - 2.0 * t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        # Fine pass around best
        lo = max(0.0, best_L - 0.04)
        hi = min(1.0, best_L + 0.04)
        for i in range(21):
            t = lo + (hi - lo) * i / 20.0
            t_val = t * 100.0
            Cb = find_max_lab_c(t_val, a_dir, b_dir)
            bx = min_x + Cb * scale
            by = cy + hy * (1.0 - 2.0 * t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        return best_L, best_L * 100.0

    def _snap_to_boundary_oklch(self, L, C_raw, scale, px, py, cx, cy, hy, min_x, oklch_h):
        """Find closest in-gamut L when mouse is outside OKLCh gamut."""
        best_L, best_dist = L, float('inf')
        for t in [i / 25.0 for i in range(26)]:
            Cb = find_max_oklch_c(t, oklch_h)
            bx = min_x + Cb * scale
            by = cy + hy * (1.0 - 2.0 * t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        lo = max(0.0, best_L - 0.04)
        hi = min(1.0, best_L + 0.04)
        for i in range(21):
            t = lo + (hi - lo) * i / 20.0
            Cb = find_max_oklch_c(t, oklch_h)
            bx = min_x + Cb * scale
            by = cy + hy * (1.0 - 2.0 * t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        return best_L

