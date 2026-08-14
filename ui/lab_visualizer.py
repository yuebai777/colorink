from typing import Any, cast

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
from ui.lab_prewarm import LabPrewarmRequest, LabPrewarmResult, LabPrewarmTask


class LabSquare(QWidget):
    # Emits (r, g, b)
    colorChanged = pyqtSignal(int, int, int)
    interactionFinished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(100, 100)
        
        self.L = 50.0
        self.a = 0.0
        self.b = 0.0
        self.max_val = 110.0
        self.render_mode = "lab"  # "lab" or "oklab"
        
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

    def resizeEvent(self, event):
        cached_size = None
        if isinstance(self._cached_key, tuple) and len(self._cached_key) >= 2:
            cached_size = self._cached_key[1]
        super().resizeEvent(event)
        new_size = min(self.width(), max(0, self.height() - self.avoid_top))
        # QStackedWidget may deliver a same-size resize when a page becomes
        # visible. Do not throw away a valid full image in that case.
        if cached_size is None or cached_size != new_size:
            self._invalidate_full_cache()

    def _cache_key(self, active: bool = False) -> tuple[object, ...]:
        size = min(self.width(), max(0, self.height() - self.avoid_top))
        return (int(self.L * 2), size, active, self.render_mode)

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
        self.update()

    def schedule_full_prewarm(self, delay_ms: int = 100) -> None:
        if self._prewarm_inflight or self._prewarm_timer.isActive():
            return
        self._prewarm_timer.start(max(0, delay_ms))

    def prewarm_full(self) -> None:
        if self._prewarm_inflight:
            return
        size = min(self.width(), max(0, self.height() - self.avoid_top))
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

    def _render_ab_plane(self, low_quality=False):
        """Render ab-plane into cache. Called by paintEvent and prerender."""
        w = self.width()
        h = self.height()
        avail_h = max(0, h - self.avoid_top)
        size = min(w, avail_h)
        if size <= 10:
            return

        is_active = False
        win = self.window()
        if win is not None and hasattr(win, "slider_widgets"):
            for chan, (slider, _) in cast(Any, win).slider_widgets.items():
                if slider.isSliderDown():
                    is_active = True
                    break
        # Also check the standalone LabSlider next to the ab plane — it is
        # not part of slider_widgets, so it would otherwise never trigger the
        # low-quality rendering path.
        if not is_active and win is not None and hasattr(win, "lab_slider"):
            if cast(Any, win).lab_slider.dragging:
                is_active = True

        cache_key = (int(self.L * 2), size, is_active, self.render_mode)
        if not low_quality and self._cached_key == cache_key and self._cached_img is not None:
            return

        ratio = self.devicePixelRatio()
        if is_active or low_quality:
            gen_size = min(size, 120)
        else:
            gen_size = int(size * ratio)
        img = QImage(gen_size, gen_size, QImage.Format.Format_ARGB32)

        # Dynamic ab display range: zoom into the in-gamut region for current L.
        min_a, max_a, min_b, max_b = self._get_display_range()
        span_a = max_a - min_a
        span_b = max_b - min_b

        # Vectorized conversion over the whole grid — same matrices as the
        # scalar functions (single source of truth: ui.color_conversions).
        cols = np.arange(gen_size, dtype=np.float64) / gen_size
        rows = np.arange(gen_size, dtype=np.float64) / gen_size
        a_grid = min_a + cols * span_a   # (gen_size,) one value per column
        b_grid = max_b - rows * span_b   # (gen_size,) one value per row
        if self.render_mode == "oklab":
            r_vals, g_vals, b_vals = oklab_to_rgb_array(
                self.L / 100.0, a_grid[None, :], b_grid[:, None])
        else:
            r_vals, g_vals, b_vals = lab_to_rgb_array(
                self.L, a_grid[None, :], b_grid[:, None])
        in_gamut = ((r_vals >= 0.0) & (r_vals <= 255.0)
                    & (g_vals >= 0.0) & (g_vals <= 255.0)
                    & (b_vals >= 0.0) & (b_vals <= 255.0))
        argb = np.where(
            in_gamut,
            (255 << 24)
            | (r_vals.astype(np.int64) << 16)
            | (g_vals.astype(np.int64) << 8)
            | b_vals.astype(np.int64),
            0,
        ).astype(np.uint32)
        img = QImage(argb.tobytes(), gen_size, gen_size, gen_size * 4,
                     QImage.Format.Format_ARGB32)

        # Save to cache
        if is_active:
            final_img = img.scaled(int(size * ratio), int(size * ratio), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            final_img.setDevicePixelRatio(ratio)
        else:
            final_img = img
            final_img.setDevicePixelRatio(ratio)
        self._cached_img = final_img
        self._cached_key = cache_key

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        avail_h = max(0, h - self.avoid_top)
        size = min(w, avail_h)
        if size <= 10:
            return

        offset_x = (w - size) / 2
        offset_y = self.avoid_top + (avail_h - size) / 2
        
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
            self.dragging = True
            self.handle_mouse(event.position())

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.handle_mouse(event.position())

    def mouseReleaseEvent(self, event):
        self.end_drag()

    def end_drag(self):
        self.dragging = False
        self.update()
        self.interactionFinished.emit()

    def handle_mouse(self, pos):
        w = self.width()
        h = self.height()
        avail_h = max(0, h - self.avoid_top)
        size = min(w, avail_h)
        offset_x = (w - size) / 2
        offset_y = self.avoid_top + (avail_h - size) / 2
        
        # Clamp coordinates to square bounds
        local_x = max(0.0, min(float(size), pos.x() - offset_x))
        local_y = max(0.0, min(float(size), pos.y() - offset_y))
        
        # Convert to a and b within the dynamic display range
        min_a, max_a, min_b, max_b = self._get_display_range()
        self.a = min_a + (local_x / size) * (max_a - min_a)
        self.b = max_b - (local_y / size) * (max_b - min_b)
        # Keep the cursor inside the in-gamut region: the bbox is rectangular
        # but the gamut is roughly elliptical, so the corners are out-of-gamut
        # (rendered transparent/black). Pull a/b back to the gamut boundary.
        self.a, self.b = self._clamp_to_gamut(self.a, self.b)
        
        self.update()
        r, g, b = self.get_current_rgb()
        self.colorChanged.emit(r, g, b)


class LabSlider(QWidget):
    # Emits lightness (0 to 100)
    lightnessChanged = pyqtSignal(float)
    # Emitted when the user releases the mouse, so the LabSquare can
    # re-render at full quality (during drag it renders low-res).
    interactionFinished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(18, 100)
        
        self.L = 50.0
        self.dragging = False
        self._gamut_min = 0.0
        self._gamut_max = 100.0

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
        h = self.height()
        
        # Draw L slider background gradient (white to black)
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(255, 255, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0))
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(0, 0, w, h)
        
        # Draw out-of-gamut gray overlay
        top_frac = 1.0 - self._gamut_max / 100.0
        bottom_frac = 1.0 - self._gamut_min / 100.0
        
        painter.setBrush(QColor(160, 160, 160, 140))
        if top_frac > 0.005:
            painter.drawRect(0, 0, w, int(h * top_frac))
        if bottom_frac < 0.995:
            painter.drawRect(0, int(h * bottom_frac), w, int(h * (1.0 - bottom_frac)))
        
        # Draw indicator cursor (horizontal bar)
        cy = (1.0 - self.L / 100.0) * h
        
        painter.setPen(QPen(QColor(255, 255, 255) if self.L < 50.0 else QColor(0, 0, 0), 2))
        painter.drawLine(0, int(cy), w, int(cy))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.handle_mouse(event.position())

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.handle_mouse(event.position())

    def mouseReleaseEvent(self, event):
        self.dragging = False
        self.interactionFinished.emit()

    def handle_mouse(self, pos):
        h = self.height()
        local_y = max(0.0, min(float(h), pos.y()))
        
        # Convert to L (0 to 100)
        self.L = (1.0 - local_y / h) * 100.0
        self.update()
        self.lightnessChanged.emit(self.L)
