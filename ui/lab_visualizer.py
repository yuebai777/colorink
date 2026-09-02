from typing import Any, cast

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

from ui.color_conversions import (
    lab_to_rgb,
    lab_to_rgb_array,
    oklab_to_rgb,
    oklab_to_rgb_array,
    rgb_to_lab,
    rgb_to_oklab,
)
from ui.color_session import session_of
from ui.lab_harmony import is_valid_harmony_mode, harmony_hue_offsets
from ui.window_layout import resolve_picker_geometry
from ui.lab_prewarm import (
    LAB_DISC_CHROMA_CEILING,
    OKLAB_DISC_CHROMA_CEILING,
    LabPrewarmRequest,
    LabPrewarmResult,
    LabPrewarmTask,
    render_lab_plane,
    smoothed_boundary_chroma,
)


class LabSquare(QWidget):
    # Emits (r, g, b)
    colorChanged = pyqtSignal(int, int, int)
    interactionFinished = pyqtSignal()
    # Emitted whenever the painted plane box may have moved or resized
    # (resize, avoid_top, shape switch) so MainWindow can re-align the
    # vertical lightness bar with it.
    planeGeometryChanged = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(100, 100)
        # Same picker crosshair as the color wheel: switching wheel ⇄ LAB
        # must not change the mouse cursor (or the pen cursor — the main
        # window's tablet sync reads the widget's cursor shape directly).
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        self.L = 50.0
        self.a = 0.0
        self.b = 0.0
        self.max_val = 110.0
        self.render_mode = "lab"  # "lab" or "oklab"
        self.shape = "square"     # "square" or "disc"
        self.harmony_mode = "analogous"

        self.dragging = False
        
        # Create tiled checkerboard texture once with transparency for theme harmony
        self.checker_pixmap = QPixmap(16, 16)
        self.checker_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.checker_pixmap)
        painter.fillRect(0, 0, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 8, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 0, 8, 8, QColor(0, 0, 0, 15))
        painter.fillRect(0, 8, 8, 8, QColor(0, 0, 0, 15))
        painter.end()
        
        # Caching variables
        self._cached_img = None
        self._cached_key = None
        self._prerender_img = None
        self._prewarm_generation = 0
        self._prewarm_inflight = False
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self.prewarm_full)
        self._prewarm_pool = QThreadPool(self)
        self._prewarm_pool.setMaxThreadCount(1)

        # Precomputed gamut bbox table: key = (render_mode, int(L)) →
        # (min_a, max_a, min_b, max_b). Filled once per mode at startup /
        # mode-switch so that dragging the L slider costs zero extra samples.
        self._bbox_table = {}
        self._precompute_bboxes()

        # Height of the top strip to keep clear of the floating preview box,
        # so the ab plane isn't hidden behind it. Set by MainWindow based on
        # the preview box geometry. 0 = no avoidance (plane centered).
        self.avoid_top = 0

        # Vertical lightness bar sharing the LAB pane with this widget.
        # ``side_total`` is the FULL pane width that the plane and the bar have
        # to share (0 = bar hidden); the pane is then composed as
        # [gap][plane][gap][bar][gap], so showing the bar no longer pushes the
        # plane off-centre with all the slack piled up on one side.
        self.side_total = 0
        self.side_bar_width = 0
        self.side_gap = 8

        # Measured cost of the plane renderer, in ms per pixel of image area.
        # Drives how many pixels an interactive frame may use.
        self._render_cost_per_px = 0.0
        # While this is in the future the widget is mid-resize (see
        # _RESIZE_QUANTUM_PX); _last_full_px is how that is noticed.
        self._resize_settles_at = 0.0
        self._last_full_px = 0

    def resizeEvent(self, event):
        cached_size = None
        if isinstance(self._cached_key, tuple) and len(self._cached_key) >= 2:
            cached_size = self._cached_key[1]
        super().resizeEvent(event)
        new_size = self._plane_size()
        # QStackedWidget may deliver a same-size resize when a page becomes
        # visible. Do not throw away a valid full image in that case.
        if cached_size is None or cached_size != new_size:
            self._invalidate_full_cache()
        self.planeGeometryChanged.emit()

    # ── plane composition ─────────────────────────────────────────────────
    #
    # Every consumer (cache key, prewarm, renderer, painter, hit-test) goes
    # through these helpers, so the box an image is rendered for can never
    # drift from the rectangle it is painted into.

    def _has_side_bar(self) -> bool:
        return self.side_total > 0 and self.side_bar_width > 0

    def _plane_width_budget(self) -> int:
        """Widest the plane may be while leaving room for the bar cluster."""
        if not self._has_side_bar():
            return self.width()
        return max(
            0, int(self.side_total - 3 * self.side_gap - self.side_bar_width))

    def _plane_gap(self, size: float) -> float:
        """Even margin M of the [M][plane][M][bar][M] pane rhythm."""
        if not self._has_side_bar():
            return 0.0
        return max(float(self.side_gap),
                   (self.side_total - size - self.side_bar_width) / 3.0)

    def _plane_left(self, size: float) -> float:
        """Left edge of the plane inside this widget."""
        if not self._has_side_bar():
            return (self.width() - size) / 2.0
        return max(0.0, min(self._plane_gap(size), self.width() - size))

    def _square_plane_size(self) -> int:
        avail_h = max(0, self.height() - self.avoid_top)
        return int(max(0, min(self._plane_width_budget(), avail_h, self.width())))

    def _plane_size(self) -> int:
        """Side of the rendered plane image (square edge / disc bounding box)."""
        if self.shape == "disc":
            return int(round(self._disc_diameter()))
        return self._square_plane_size()

    def plane_geometry(self) -> tuple[float, float, float, float]:
        """``(gap, x, y, size)`` of the painted plane, in local coordinates.

        ``gap`` is the ideal even margin of the pane rhythm: MainWindow sizes
        the lightness-bar column from it and aligns the bar's top/bottom with
        ``y`` / ``y + size`` so the bar reads as part of the plane.
        """
        if self.shape == "disc":
            cx, cy, radius = self._disc_metrics()
            diameter = radius * 2.0
            return self._plane_gap(diameter), cx - radius, cy - radius, diameter
        size = self._square_plane_size()
        avail_h = max(0, self.height() - self.avoid_top)
        return (self._plane_gap(size), self._plane_left(size),
                self.avoid_top + (avail_h - size) / 2.0, float(size))

    def set_side_cluster(self, total_width: int, bar_width: int,
                         gap: int = 8) -> None:
        """Describe the lightness-bar cluster that shares the LAB pane.

        Sized from the *pane* width instead of this widget's own width: the
        latter is exactly what the caller is about to change, so feeding it
        back in would make the layout pass chase its own tail.
        """
        total_width = max(0, int(total_width))
        bar_width = max(0, int(bar_width))
        gap = max(0, int(gap))
        if (total_width, bar_width, gap) == (
                self.side_total, self.side_bar_width, self.side_gap):
            return
        self.side_total = total_width
        self.side_bar_width = bar_width
        self.side_gap = gap
        self._invalidate_full_cache()
        self.update()

    def _cache_key(self, active: bool = False) -> tuple[object, ...]:
        return (int(self.L * 2), self._plane_size(), active,
                self.render_mode, self.shape)

    def _invalidate_full_cache(self) -> None:
        self._prewarm_generation += 1
        self._cached_img = None
        self._cached_key = None
        self._prerender_img = None

    def set_avoid_top(self, value: int) -> None:
        value = max(0, int(value))
        if value == self.avoid_top:
            return
        self.avoid_top = value
        self._invalidate_full_cache()
        self.planeGeometryChanged.emit()
        self.update()

    def schedule_full_prewarm(self, delay_ms: int = 100) -> None:
        if self._prewarm_inflight or self._prewarm_timer.isActive():
            return
        self._prewarm_timer.start(max(0, delay_ms))

    def prewarm_full(self) -> None:
        if self._prewarm_inflight:
            return
        size = self._plane_size()
        if size <= 10:
            return
        min_a, max_a, min_b, max_b = self._get_display_range()
        generation = self._prewarm_generation + 1
        self._prewarm_generation = generation
        self._prewarm_inflight = True
        request = LabPrewarmRequest(
            generation=generation, render_mode=self.render_mode,
            lightness=self.L, size=size, min_a=min_a, max_a=max_a,
            min_b=min_b, max_b=max_b, pixel_ratio=max(1.0, float(self.devicePixelRatio())),
            shape=self.shape,
        )
        task = LabPrewarmTask(request)
        task.signals.finished.connect(self._on_prewarm_finished)
        task.signals.failed.connect(self._on_prewarm_failed)
        self._prewarm_pool.start(task)

    def _on_prewarm_finished(self, result: object) -> None:
        self._prewarm_inflight = False
        if not isinstance(result, LabPrewarmResult):
            return
        request = result.request
        if request.generation != self._prewarm_generation:
            self.schedule_full_prewarm(0)
            return
        if request.shape != self.shape:
            return
        if request.render_mode != self.render_mode or int(request.lightness * 2) != int(self.L * 2):
            return
        image = QImage(
            result.image_bytes, result.image_width, result.image_height,
            result.image_width * 4, QImage.Format.Format_RGBA8888,
        ).copy()
        image.setDevicePixelRatio(request.pixel_ratio)
        self._cached_img = image
        self._cached_key = self._cache_key(False)
        self._prerender_img = None
        self.update()

    def _on_prewarm_failed(self, failure: object) -> None:
        self._prewarm_inflight = False

    def set_render_mode(self, mode):
        """Set render mode: 'lab' or 'oklab'. Invalidates cache."""
        if mode != self.render_mode:
            self.render_mode = mode
            self.max_val = 110.0 if mode == "lab" else 0.3
            self._invalidate_full_cache()
            self._precompute_bboxes()
            self.update()

    def set_shape(self, shape: str) -> None:
        """Set the a*b* plane shape: 'square' (legacy) or 'disc'."""
        if shape not in ("square", "disc"):
            return
        if shape != self.shape:
            self.shape = shape
            self._invalidate_full_cache()
            self.planeGeometryChanged.emit()
            self.update()

    def set_harmony_mode(self, mode: str) -> None:
        """Set the colour-harmony preset drawn on the circulant disc."""
        if mode != self.harmony_mode:
            self.harmony_mode = mode if is_valid_harmony_mode(mode) else "analogous"
            self.update()

    def set_color(self, r, g, b, block_signals=False, update_widget=True):
        old_l_bucket = int(self.L * 2)
        if self.render_mode == "oklab":
            l, a, b_val = rgb_to_oklab(r, g, b)
            # Scale OKLab L from [0,1] to [0,100] for internal storage consistency
            self.L = l * 100.0
            self.a = a
            self.b = b_val
        else:
            l, a, b_val = rgb_to_lab(r, g, b)
            self.L = l
            self.a = a
            self.b = b_val
        if int(self.L * 2) != old_l_bucket:
            self._invalidate_full_cache()
        if update_widget:
            self.update()
        if not block_signals:
            self.colorChanged.emit(r, g, b)

    def set_oklab(self, L, a, b, block_signals=False, update_widget=True):
        """Direct OKLab state — avoids HSV→RGB→OKLab round-trip drift."""
        # L is expected in [0, 1], convert to internal [0, 100]
        self.L = L * 100.0
        self.a = a
        self.b = b
        self.a, self.b = self._clamp_to_gamut(self.a, self.b)
        if update_widget:
            self.update()
        if not block_signals:
            r, g, b = self.get_current_rgb()
            self.colorChanged.emit(r, g, b)

    def native_color_values(self):
        """Return (space, values) for the LabSquare's current colour."""
        if self.render_mode == "oklab":
            return "oklab", (self.L / 100.0, self.a, self.b)
        return "lab", (self.L, self.a, self.b)

    def set_lightness(self, lightness, update_widget=True):
        old_l_bucket = int(self.L * 2)
        self.L = lightness
        if int(self.L * 2) != old_l_bucket:
            self._invalidate_full_cache()
        # Keep (a, b) inside the gamut at the new L so the cursor doesn't
        # drift into the black out-of-gamut corners after L has changed
        # significantly from the L at which the color was selected.
        self.a, self.b = self._clamp_to_gamut(self.a, self.b)
        if update_widget:
            self.update()
        r, g, b = self.get_current_rgb()
        self.colorChanged.emit(r, g, b)

    def get_current_rgb(self):
        if self.render_mode == "oklab":
            r, g, b = oklab_to_rgb(self.L / 100.0, self.a, self.b)
        else:
            r, g, b = lab_to_rgb(self.L, self.a, self.b)
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        return r, g, b

    def prerender(self):
        """Show low-res only when the current full cache is not ready."""
        if self._cached_img is not None and self._cached_key == self._cache_key(False):
            self._prerender_img = None
            return
        self._cached_img = None
        self._cached_key = None
        self._render_ab_plane(low_quality=True)
        self._prerender_img = self._cached_img
        self._cached_img = None
        self._cached_key = None
        self.schedule_full_prewarm(0)

    def _compute_gamut_bbox(self, L):
        """Sample the ab grid at lightness L and return (min_a, max_a,
        min_b, max_b) covering only in-gamut colors, with padding. Falls back
        to the full fixed range when no in-gamut sample is found (extreme L)."""
        step = 0.01 if self.render_mode == "oklab" else 5.0
        max_v = self.max_val
        min_a, max_a = max_v, -max_v
        min_b, max_b = max_v, -max_v
        found = False
        a = -max_v
        while a <= max_v:
            b = -max_v
            while b <= max_v:
                if self.render_mode == "oklab":
                    r, g, bv = oklab_to_rgb(L / 100.0, a, b)
                else:
                    r, g, bv = lab_to_rgb(L, a, b)
                if 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0:
                    if a < min_a: min_a = a
                    if a > max_a: max_a = a
                    if b < min_b: min_b = b
                    if b > max_b: max_b = b
                    found = True
                b += step
            a += step
        if not found:
            return -max_v, max_v, -max_v, max_v
        pad = step * 3
        min_a = max(-max_v, min_a - pad)
        max_a = min(max_v, max_a + pad)
        min_b = max(-max_v, min_b - pad)
        max_b = min(max_v, max_b + pad)
        # Guard against degenerate ranges near black/white where the gamut is
        # essentially a single point - widen around the midpoint so the cursor
        # doesn't stick to the very edge.
        min_span = step * 8
        if max_a - min_a < min_span:
            mid = (min_a + max_a) * 0.5
            min_a = max(-max_v, mid - min_span / 2)
            max_a = min(max_v, mid + min_span / 2)
        if max_b - min_b < min_span:
            mid = (min_b + max_b) * 0.5
            min_b = max(-max_v, mid - min_span / 2)
            max_b = min(max_v, mid + min_span / 2)
        return min_a, max_a, min_b, max_b

    def _get_display_range(self):
        """Look up the precomputed gamut bbox for current render_mode and L.
        Falls back to the full fixed range if the key is somehow missing."""
        return self._bbox_table.get(
            (self.render_mode, int(self.L)),
            (-self.max_val, self.max_val, -self.max_val, self.max_val),
        )

    def _precompute_bboxes(self):
        """Precompute gamut bboxes for every L (0..100) in the current
        render_mode. Called once at startup / mode-switch."""
        mode = self.render_mode
        for L_int in range(101):
            key = (mode, L_int)
            if key not in self._bbox_table:
                self._bbox_table[key] = self._compute_gamut_bbox(float(L_int))

    def _is_in_gamut(self, a, b):
        if self.render_mode == "oklab":
            r, g, bv = oklab_to_rgb(self.L / 100.0, a, b)
        else:
            r, g, bv = lab_to_rgb(self.L, a, b)
        return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

    def _clamp_to_gamut(self, a, b):
        """Pull (a, b) inside the in-gamut region at the current L.

        The bbox is rectangular but the gamut is roughly elliptical, so clicks
        in the corners land outside the gamut (rendered transparent/black) and
        the cursor would appear to leave the colored area. Binary-search along
        the segment from the click point toward a known in-gamut anchor to find
        the boundary, then sit the cursor just inside it.

        This cheap ray-to-anchor variant is used for programmatic state
        updates (L changes, external colours); interactive drags go through
        :meth:`_nearest_in_gamut` so the cursor lands on the boundary point
        closest to the mouse.
        """
        if self._is_in_gamut(a, b):
            return a, b
        min_a, max_a, min_b, max_b = self._get_display_range()
        anchors = [(0.0, 0.0),
                   ((min_a + max_a) * 0.5, (min_b + max_b) * 0.5)]
        for ax, ay in anchors:
            if not self._is_in_gamut(ax, ay):
                continue
            # Segment: t=0 at click point (out of gamut), t=1 at anchor (in).
            # Find the smallest t where the point enters the gamut.
            lo, hi = 0.0, 1.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                ca = a + (ax - a) * mid
                cb = b + (ay - b) * mid
                if self._is_in_gamut(ca, cb):
                    hi = mid
                else:
                    lo = mid
            return a + (ax - a) * hi, b + (ay - b) * hi
        # No in-gamut anchor found (extreme L): leave as-is; RGB will clamp.
        return a, b

    def _nearest_in_gamut(self, a, b, mouse_x, mouse_y, offset_x, offset_y, size):
        """Nearest in-gamut (a, b) to the raw mouse position.

        Used while dragging: the raw position may sit outside the display
        square (or in its transparent corners), and the cursor must land on
        the closest boundary point instead of being clamped to the square
        edge or pulled along the ray to the centre.  The gamut boundary is
        sampled by a ray sweep from the origin; distances are measured in
        widget pixels so asymmetric ab spans don't skew the result.
        """
        if self._is_in_gamut(a, b):
            return a, b
        min_a, max_a, min_b, max_b = self._get_display_range()

        def to_screen(aa, bb):
            sx = offset_x + (aa - min_a) / (max_a - min_a) * size
            sy = offset_y + (max_b - bb) / (max_b - min_b) * size
            return sx, sy

        def boundary_point(theta):
            dx, dy = math.cos(theta), math.sin(theta)
            lo, hi = 0.0, self.max_val * 1.5
            for _ in range(20):
                mid = (lo + hi) * 0.5
                if self._is_in_gamut(mid * dx, mid * dy):
                    lo = mid
                else:
                    hi = mid
            return lo * dx, lo * dy

        best_theta = 0.0
        best = (0.0, 0.0)
        best_dist = float('inf')
        for i in range(48):
            theta = 2.0 * math.pi * i / 48.0
            ba, bb = boundary_point(theta)
            sx, sy = to_screen(ba, bb)
            d = (sx - mouse_x) ** 2 + (sy - mouse_y) ** 2
            if d < best_dist:
                best_dist = d
                best_theta = theta
                best = (ba, bb)
        # Fine pass around the winning ray direction.
        step = 2.0 * math.pi / 48.0
        for i in range(25):
            theta = best_theta + (i - 12) * step / 12.0
            ba, bb = boundary_point(theta)
            sx, sy = to_screen(ba, bb)
            d = (sx - mouse_x) ** 2 + (sy - mouse_y) ** 2
            if d < best_dist:
                best_dist = d
                best = (ba, bb)
        return best

    # Live rendering budget. The plane is redrawn on every frame of a drag,
    # so the question is not "cheap or pretty" but "how many pixels fit in a
    # frame". The renderer measures itself and sizes the next interactive
    # frame from that, instead of the old fixed 120px cap — on any normal
    # window that now lands on the full resolution, and a huge window or a
    # slow machine degrades gradually rather than to a blur.
    _FRAME_BUDGET_MS = 7.0
    _INTERACTIVE_FLOOR_PX = 200
    # While the window is being dragged the plane gets a new size every
    # frame, and the renderer's polar grid — a third of a cold frame — can
    # never be reused. Snapping the render size to this step during a resize
    # makes consecutive frames share one grid; the result is scaled to the
    # exact box, which on a smooth gradient is invisible.
    _RESIZE_QUANTUM_PX = 16
    _RESIZE_SETTLE_S = 0.25

    def _interactive_px(self, full_px: int) -> int:
        """Pixels the next interactive frame may use."""
        cost = self._render_cost_per_px          # ms per pixel of image area
        if not cost:
            return full_px                       # not measured yet: try full
        affordable = int(math.sqrt(self._FRAME_BUDGET_MS / cost))
        return max(min(self._INTERACTIVE_FLOOR_PX, full_px),
                   min(full_px, affordable))

    def _note_render_cost(self, gen_size: int, elapsed_s: float) -> None:
        """Fold one render into the running ms-per-pixel estimate."""
        area = max(1, gen_size * gen_size)
        if area < 64 * 64:                       # too small to measure well
            return
        sample = (elapsed_s * 1000.0) / area
        previous = self._render_cost_per_px
        self._render_cost_per_px = (
            sample if not previous else previous * 0.7 + sample * 0.3)

    def _render_ab_plane(self, low_quality=False):
        """Render ab-plane into cache. Called by paintEvent and prerender."""
        size = self._plane_size()
        if size <= 10:
            return

        # Is anything else being dragged right now? Ask the shared session
        # instead of climbing to the window and scanning its sliders: once a
        # panel floats in its own window that lookup finds nothing.
        session = session_of(self)
        if session is not None:
            is_active = session.interacting
        else:
            # No session wired (bare widget, e.g. in tests): fall back to the
            # old scan so behaviour is unchanged.
            is_active = False
            win = self.window()
            if win is not None and hasattr(win, "slider_widgets"):
                for chan, (slider, _) in cast(Any, win).slider_widgets.items():
                    if slider.isSliderDown():
                        is_active = True
                        break
            if not is_active and win is not None and hasattr(win, "lab_slider"):
                if cast(Any, win).lab_slider.dragging:
                    is_active = True

        cache_key = self._cache_key(is_active)
        if not low_quality and self._cached_key == cache_key and self._cached_img is not None:
            return

        ratio = self.devicePixelRatio()
        full_px = max(1, int(size * ratio))
        if is_active or low_quality:
            gen_size = self._interactive_px(full_px)
        else:
            gen_size = full_px
        started = time.perf_counter()
        # Detect a resize from the render itself rather than from resizeEvent:
        # the event is not delivered to a widget that is not on screen, and
        # the renderer is the only place that always sees the new size.
        if full_px != self._last_full_px:
            self._last_full_px = full_px
            self._resize_settles_at = started + self._RESIZE_SETTLE_S
        if started < self._resize_settles_at:
            quantum = self._RESIZE_QUANTUM_PX
            gen_size = max(64, min(full_px, (gen_size // quantum) * quantum))

        # Both shapes go through the same renderer (ui.lab_prewarm): the
        # square used to carry a second, slower copy of the conversion here,
        # which meant every speed-up had to be made twice and the two could
        # drift apart.
        if self.shape == "disc":
            min_a = max_a = min_b = max_b = 0.0
        else:
            # Dynamic ab display range: zoom into the in-gamut region for current L.
            min_a, max_a, min_b, max_b = self._get_display_range()
        request = LabPrewarmRequest(
            generation=0, render_mode=self.render_mode,
            lightness=self.L, size=gen_size,
            min_a=min_a, max_a=max_a, min_b=min_b, max_b=max_b,
            pixel_ratio=1.0, shape=self.shape,
        )
        result = render_lab_plane(request)
        img = QImage(
            result.image_bytes, result.image_width, result.image_height,
            result.image_width * 4, QImage.Format.Format_RGBA8888,
        ).copy()

        self._note_render_cost(gen_size, time.perf_counter() - started)

        # Save to cache
        if is_active or gen_size < full_px:
            final_img = img.scaled(int(size * ratio), int(size * ratio), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            final_img.setDevicePixelRatio(ratio)
        else:
            final_img = img
            final_img.setDevicePixelRatio(ratio)
        self._cached_img = final_img
        self._cached_key = cache_key

    # ── circulant-disc helpers ────────────────────────────────────────────

    def _disc_metrics(self) -> tuple[float, float, float]:
        """Center/radius for the circulant disc — identical to the hue ring.

        Mirrors ``ColorWheel.get_wheel_geometry`` exactly: same edge margins,
        same center y (``size/2 + 6``) and same outer radius
        (``size/2 - 2``). ``avoid_top`` is deliberately ignored here so the
        disc never shrinks or shifts relative to the colour wheel.

        With the lightness bar shown the disc cannot keep the ring's full
        width, so it takes the widest diameter the pane rhythm allows and is
        centred on its own share of the pane — instead of being squeezed into
        the leftover column with all the air piled up on one side.
        """
        w = self.width()
        h = self.height()
        if self._has_side_bar():
            # The drawn diameter is ``size - 4`` (the ring's 2px inset per
            # side), so the width budget applies to that, not to ``size``.
            size = min(self._plane_width_budget() + 4, max(16, h - 6), w)
            diameter = max(1.0, size - 4.0)
            cx = self._plane_left(diameter) + diameter / 2.0
        else:
            # Shared with the hue ring (ui.window_layout) so the disc can
            # never drift away from it.
            picker = resolve_picker_geometry(w, h)
            size = picker.size
            cx = picker.circle.x
        cy = size / 2.0 + 6.0
        radius = max(1.0, size / 2.0 - 2.0)
        return cx, cy, radius

    def _disc_diameter(self) -> float:
        return max(1.0, self._disc_metrics()[2] * 2.0)

    def _disc_chroma_ceiling(self) -> float:
        """Chroma cap used by the disc renderer (kept in sync with lab_prewarm)."""
        return (OKLAB_DISC_CHROMA_CEILING if self.render_mode == "oklab"
                else LAB_DISC_CHROMA_CEILING)

    def _max_chroma_for_direction(self, a: float, b: float) -> float:
        C = math.hypot(a, b)
        if C <= 1e-9:
            return 0.0
        # Smoothed boundary (same moving-minimum as the disc renderer), so
        # indicator/harmony dots and screen→ab mapping agree with the edge.
        hue = math.degrees(math.atan2(b, a)) % 360.0
        if self.render_mode == "oklab":
            full = smoothed_boundary_chroma("oklab", self.L / 100.0, hue)
        else:
            full = smoothed_boundary_chroma("lab", self.L, hue)
        return min(full, self._disc_chroma_ceiling())

    def _disc_ab_to_screen(self, a: float, b: float) -> QPointF:
        cx, cy, r = self._disc_metrics()
        C = math.hypot(a, b)
        max_c = self._max_chroma_for_direction(a, b)
        rho = 0.0 if max_c <= 1e-9 else min(1.0, C / max_c)
        hue = math.atan2(b, a)
        return QPointF(
            cx + rho * r * math.cos(hue),
            cy - rho * r * math.sin(hue),
        )

    def _disc_screen_to_ab(self, pos: QPointF) -> tuple[float, float]:
        cx, cy, r = self._disc_metrics()
        dx = pos.x() - cx
        dy = cy - pos.y()
        rho = min(1.0, math.hypot(dx, dy) / r) if r > 1e-6 else 0.0
        hue = math.atan2(dy, dx)
        a_dir = math.cos(hue)
        b_dir = math.sin(hue)
        max_c = self._max_chroma_for_direction(a_dir, b_dir)
        C = rho * max_c
        return C * a_dir, C * b_dir

    def _harmony_points_ab(self) -> list[tuple[float, float]]:
        """Harmony points for the active preset, in a/b coordinates.

        Every point keeps the base colour's *relative* chroma (fraction of its
        own hue's gamut boundary), so the small dots sit on a smooth circle
        like Procreate's harmony wheel and always stay inside sRGB.
        """
        base_a, base_b = self.a, self.b
        base_C = math.hypot(base_a, base_b)
        max_c = self._max_chroma_for_direction(base_a, base_b)
        rho = 0.0 if max_c <= 1e-9 else min(1.0, base_C / max_c)
        base_hue = math.atan2(base_b, base_a)
        points = []
        for offset_deg in harmony_hue_offsets(self.harmony_mode):
            hue = base_hue + math.radians(offset_deg)
            a_dir = math.cos(hue)
            b_dir = math.sin(hue)
            hue_max = self._max_chroma_for_direction(a_dir, b_dir)
            C = rho * hue_max
            points.append((C * a_dir, C * b_dir))
        return points

    def _ab_to_rgb(self, a: float, b: float) -> tuple[int, int, int]:
        if self.render_mode == "oklab":
            rgb = oklab_to_rgb(self.L / 100.0, a, b)
        else:
            rgb = lab_to_rgb(self.L, a, b)
        return (
            max(0, min(255, int(rgb[0]))),
            max(0, min(255, int(rgb[1]))),
            max(0, min(255, int(rgb[2]))),
        )

    def _hit_harmony_point(self, pos: QPointF) -> tuple[float, float] | None:
        if self.shape != "disc":
            return None
        points = self._harmony_points_ab()
        for i, (a, b) in enumerate(points):
            if i == 0:
                continue  # base indicator is the large dot, not a harmony dot
            p = self._disc_ab_to_screen(a, b)
            if math.hypot(pos.x() - p.x(), pos.y() - p.y()) <= 9.0:
                return a, b
        return None

    def _draw_disc_overlay(self, painter) -> None:
        if self.shape != "disc":
            return
        points = self._harmony_points_ab()
        # Harmony dots first, base large dot on top.
        for i, (a, b) in enumerate(points):
            if i == 0:
                continue
            pos = self._disc_ab_to_screen(a, b)
            r, g, bv = self._ab_to_rgb(a, b)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(r, g, bv))
            painter.drawEllipse(pos, 5.0, 5.0)
            border = QColor(255, 255, 255) if self.L < 50.0 else QColor(0, 0, 0)
            painter.setPen(QPen(border, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(pos, 5.0, 5.0)

        pos = self._disc_ab_to_screen(self.a, self.b)
        r, g, bv = self.get_current_rgb()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, bv))
        painter.drawEllipse(pos, 10.0, 10.0)
        border = QColor(255, 255, 255) if self.L < 50.0 else QColor(0, 0, 0)
        painter.setPen(QPen(border, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(pos, 10.0, 10.0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        _gap, offset_x, offset_y, plane_size = self.plane_geometry()
        size = int(round(plane_size))
        if size <= 10:
            return
        
        # Prefer a resident full-resolution cache. If it is not ready, keep
        # showing the low-res preview and let the worker finish without ever
        # running the full pixel loop on the page-switch event.
        used_prerender = False
        full_key = self._cache_key(False)
        if self._cached_img is not None and self._cached_key == full_key:
            self._prerender_img = None
            painter.drawImage(int(offset_x), int(offset_y), self._cached_img)
        else:
            prerender_img = getattr(self, "_prerender_img", None)
            if prerender_img is None:
                self._render_ab_plane(low_quality=True)
                prerender_img = self._cached_img
                self._cached_img = None
                self._cached_key = None
                self._prerender_img = prerender_img
            if prerender_img is not None:
                target = QRectF(offset_x, offset_y, size, size)
                painter.drawImage(target, prerender_img)
                used_prerender = True
                self.schedule_full_prewarm(0)

        if self.shape == "disc":
            # Circular LAB disc: harmony small dots + large base dot.
            self._draw_disc_overlay(painter)
        else:
            # Draw cursor (clamped to the square so it never escapes when the
            # current a/b falls outside the dynamic range after an L change)
            min_a, max_a, min_b, max_b = self._get_display_range()
            cx_frac = (self.a - min_a) / (max_a - min_a) if max_a > min_a else 0.5
            cy_frac = (max_b - self.b) / (max_b - min_b) if max_b > min_b else 0.5
            ix = offset_x + max(0.0, min(1.0, cx_frac)) * size
            iy = offset_y + max(0.0, min(1.0, cy_frac)) * size

            r, g, b = self.get_current_rgb()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(r, g, b))
            painter.drawEllipse(QPointF(ix, iy), 8.0, 8.0)

            # White/Black ring outline depending on lightness
            color_border = QColor(255, 255, 255) if self.L < 50.0 else QColor(0, 0, 0)
            painter.setPen(QPen(color_border, 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(ix, iy), 8.0, 8.0)

        if used_prerender:
            # Delay the full-quality pass slightly so repeated LAB/wheel
            # toggles stay responsive while the low-res preview is visible.
            QTimer.singleShot(80, self.update)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # A small harmony dot is clickable: it becomes the new base
            # colour (large dot) without starting a drag.
            hit = self._hit_harmony_point(event.position())
            if hit is not None:
                self.a, self.b = hit
                self.update()
                r, g, b = self.get_current_rgb()
                self.colorChanged.emit(r, g, b)
                return
            self.dragging = True
            # Mirror the color wheel's inner-region drag: while picking on
            # the LAB plane the crosshair hides (the indicator dot shows the
            # position) and comes back on release.
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.handle_mouse(event.position())

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.handle_mouse(event.position())

    def mouseReleaseEvent(self, event):
        self.end_drag()

    def end_drag(self):
        self.dragging = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()
        self.interactionFinished.emit()

    def handle_mouse(self, pos):
        if self.shape == "disc":
            # Polar mapping: angle = hue, radius = relative chroma.
            self.a, self.b = self._disc_screen_to_ab(pos)
            self.update()
            r, g, b = self.get_current_rgb()
            self.colorChanged.emit(r, g, b)
            return

        _gap, offset_x, offset_y, size = self.plane_geometry()
        if size <= 0:
            return

        # Convert the RAW mouse position to a and b.  Do not clamp to the
        # square first: a drag outside the diagram should snap to the
        # nearest in-gamut boundary point, not stick to the square edge.
        min_a, max_a, min_b, max_b = self._get_display_range()
        self.a = min_a + (pos.x() - offset_x) / size * (max_a - min_a)
        self.b = max_b - (pos.y() - offset_y) / size * (max_b - min_b)
        # Keep the cursor inside the in-gamut region: the bbox is rectangular
        # but the gamut is roughly elliptical, so the corners are out-of-gamut
        # (rendered transparent/black). Snap a/b to the closest boundary point.
        self.a, self.b = self._nearest_in_gamut(
            self.a, self.b, pos.x(), pos.y(), offset_x, offset_y, size)

        self.update()
        r, g, b = self.get_current_rgb()
        self.colorChanged.emit(r, g, b)


class LabSlider(QWidget):
    # Emits lightness (0 to 100)
    lightnessChanged = pyqtSignal(float)
    # Emitted when the user grabs the bar, so the shared session can tell the
    # plane renderer to take the cheap path (it used to reach up to the window
    # and read this widget's .dragging attribute directly).
    interactionStarted = pyqtSignal()
    # Emitted when the user releases the mouse, so the LabSquare can
    # re-render at full quality (during drag it renders low-res).
    interactionFinished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # The column margins align this bar with the LAB plane's own top and
        # bottom, so the minimum height must stay small enough for a short
        # plane band; MainWindow keeps the real floor.
        self.setMinimumSize(12, 24)
        
        self.L = 50.0
        self.dragging = False
        self._gamut_min = 0.0
        self._gamut_max = 100.0
        # Vertical band this bar actually occupies, in widget coordinates
        # (0 height = the whole widget). MainWindow aligns it with the a*b*
        # plane. This is deliberately NOT done with layout margins: those
        # count towards the column's minimum height, which feeds the window's
        # content-height policy, which resizes the pane — a loop that inflated
        # the window and left a huge blank band under the plane.
        self._band_top = 0.0
        self._band_h = 0.0

    def set_track_band(self, top: float, height: float) -> None:
        """Restrict the painted track (and its hit area) to one band."""
        top = max(0.0, float(top))
        height = max(0.0, float(height))
        if (top, height) == (self._band_top, self._band_h):
            return
        self._band_top = top
        self._band_h = height
        self.update()

    def track_band(self) -> tuple[float, float]:
        """Effective (top, height) of the track inside this widget."""
        widget_h = float(self.height())
        if self._band_h <= 0.0:
            return 0.0, widget_h
        top = max(0.0, min(self._band_top, max(0.0, widget_h - 1.0)))
        return top, max(1.0, min(self._band_h, widget_h - top))

    def set_in_gamut_range(self, mn, mx):
        """Set the valid in-gamut L range.
        Values outside [mn, mx] will be grayed on the slider track."""
        self._gamut_min = mn
        self._gamut_max = mx
        self.update()

    def set_lightness(self, lightness, update_widget=True):
        self.L = lightness
        if update_widget:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        top, h = self.track_band()
        
        # Draw L slider background gradient (white to black)
        gradient = QLinearGradient(0.0, top, 0.0, top + h)
        gradient.setColorAt(0.0, QColor(255, 255, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0))
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(QRectF(0.0, top, float(w), h))
        
        # Draw out-of-gamut gray overlay
        top_frac = 1.0 - self._gamut_max / 100.0
        bottom_frac = 1.0 - self._gamut_min / 100.0
        
        painter.setBrush(QColor(160, 160, 160, 140))
        if top_frac > 0.005:
            painter.drawRect(QRectF(0.0, top, float(w), h * top_frac))
        if bottom_frac < 0.995:
            painter.drawRect(QRectF(0.0, top + h * bottom_frac,
                                    float(w), h * (1.0 - bottom_frac)))
        
        # Draw indicator cursor (horizontal bar)
        cy = top + (1.0 - self.L / 100.0) * h
        
        painter.setPen(QPen(QColor(255, 255, 255) if self.L < 50.0 else QColor(0, 0, 0), 2))
        painter.drawLine(0, int(cy), w, int(cy))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.interactionStarted.emit()
            self.handle_mouse(event.position())

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.handle_mouse(event.position())

    def mouseReleaseEvent(self, event):
        was_dragging = self.dragging
        self.dragging = False
        if was_dragging:
            self.interactionFinished.emit()

    def handle_mouse(self, pos):
        top, h = self.track_band()
        local_y = max(0.0, min(h, pos.y() - top))
        
        # Convert to L (0 to 100)
        self.L = (1.0 - local_y / h) * 100.0
        self.update()
        self.lightnessChanged.emit(self.L)
