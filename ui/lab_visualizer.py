import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QImage, QPen, QBrush, QLinearGradient, QPixmap, QPainterPath
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from ui.oklab_colors import oklab_to_rgb, rgb_to_oklab

def rgb_to_lab(r, g, b):
    r_val = max(0.0, min(255.0, r)) / 255.0
    g_val = max(0.0, min(255.0, g)) / 255.0
    b_val = max(0.0, min(255.0, b)) / 255.0
    
    r_val = ((r_val + 0.055) / 1.055) ** 2.4 if r_val > 0.04045 else r_val / 12.92
    g_val = ((g_val + 0.055) / 1.055) ** 2.4 if g_val > 0.04045 else g_val / 12.92
    b_val = ((b_val + 0.055) / 1.055) ** 2.4 if b_val > 0.04045 else b_val / 12.92
    
    x = r_val * 0.4124564 + g_val * 0.3575761 + b_val * 0.1804375
    y = r_val * 0.2126729 + g_val * 0.7151522 + b_val * 0.0721750
    z = r_val * 0.0193339 + g_val * 0.1191920 + b_val * 0.9503041
    
    x50 = 1.0478112 * x + 0.0228866 * y - 0.0501270 * z
    y50 = 0.0295424 * x + 0.9904844 * y - 0.0170491 * z
    z50 = -0.0092345 * x + 0.0150436 * y + 0.7521316 * z
    
    x_scaled = x50 / 0.96422
    y_scaled = y50 / 1.0
    z_scaled = z50 / 0.82521
    
    def f(t):
        return t ** (1.0/3.0) if t > 0.008856 else (7.787 * t) + 16.0/116.0
        
    fy = f(y_scaled)
    return (
        (116.0 * fy) - 16.0,
        500.0 * (f(x_scaled) - fy),
        200.0 * (fy - f(z_scaled))
    )

def lab_to_rgb(l, a, b):
    y = (l + 16.0) / 116.0
    x = a / 500.0 + y
    z = y - b / 200.0
    
    def f_inv(t):
        return t * t * t if t > 0.206893 else (t - 16.0/116.0) / 7.787
        
    x_val = 0.96422 * f_inv(x)
    y_val = 1.0 * f_inv(y)
    z_val = 0.82521 * f_inv(z)
    
    x65 = 0.9554734 * x_val - 0.0230984 * y_val + 0.0632595 * z_val
    y65 = -0.0283697 * x_val + 1.0099956 * y_val + 0.0210414 * z_val
    z65 = 0.0123140 * x_val - 0.0205077 * y_val + 1.3303659 * z_val
    
    r = x65 * 3.2404542 + y65 * -1.5371385 + z65 * -0.4985314
    g = x65 * -0.9692660 + y65 * 1.8760108 + z65 * 0.0415560
    bl = x65 * 0.0556434 + y65 * -0.2040259 + z65 * 1.0572252
    
    def gamma(v):
        return 12.92 * v if v <= 0.0031308 else 1.055 * (max(0.0, v) ** (1.0/2.4)) - 0.055
        
    return (
        gamma(r) * 255.0,
        gamma(g) * 255.0,
        gamma(bl) * 255.0
    )

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

        # Precomputed gamut bbox table: key = (render_mode, int(L)) →
        # (min_a, max_a, min_b, max_b). Filled once per mode at startup /
        # mode-switch so that dragging the L slider costs zero extra samples.
        self._bbox_table = {}
        self._precompute_bboxes()

        # Height of the top strip to keep clear of the floating preview box,
        # so the ab plane isn't hidden behind it. Set by MainWindow based on
        # the preview box geometry. 0 = no avoidance (plane centered).
        self.avoid_top = 0

    def set_render_mode(self, mode):
        """Set render mode: 'lab' or 'oklab'. Invalidates cache."""
        if mode != self.render_mode:
            self.render_mode = mode
            self.max_val = 110.0 if mode == "lab" else 0.3
            self._cached_img = None
            self._cached_key = None
            self._precompute_bboxes()
            self.update()

    def set_color(self, r, g, b, block_signals=False):
        if self.render_mode == "oklab":
            L, a, b_val = rgb_to_oklab(r, g, b)
            # Scale OKLab L from [0,1] to [0,100] for internal storage consistency
            self.L = L * 100.0
            self.a = a
            self.b = b_val
        else:
            l, a, b_val = rgb_to_lab(r, g, b)
            self.L = l
            self.a = a
            self.b = b_val
        self.update()
        if not block_signals:
            self.colorChanged.emit(r, g, b)

    def set_lightness(self, lightness):
        self.L = lightness
        # Keep (a, b) inside the gamut at the new L so the cursor doesn't
        # drift into the black out-of-gamut corners after L has changed
        # significantly from the L at which the color was selected.
        self.a, self.b = self._clamp_to_gamut(self.a, self.b)
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
        """Pre-render a low-res preview for instant display on mode switch."""
        self._cached_img = None
        self._cached_key = None
        self._render_ab_plane(low_quality=True)
        self._prerender_img = self._cached_img
        self._cached_img = None
        self._cached_key = None

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
            for chan, (slider, _) in win.slider_widgets.items():
                if slider.isSliderDown():
                    is_active = True
                    break
        # Also check the standalone LabSlider next to the ab plane — it is
        # not part of slider_widgets, so it would otherwise never trigger the
        # low-quality rendering path.
        if not is_active and win is not None and hasattr(win, "lab_slider"):
            if win.lab_slider.dragging:
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

        if self.render_mode == "oklab":
            # OKLab rendering: per-pixel oklab_to_rgb
            for row in range(gen_size):
                b_val = max_b - (row / gen_size) * span_b
                for col in range(gen_size):
                    a_val = min_a + (col / gen_size) * span_a
                    r_val, g_val, bv = oklab_to_rgb(self.L / 100.0, a_val, b_val)
                    if 0 <= r_val <= 255 and 0 <= g_val <= 255 and 0 <= bv <= 255:
                        argb = (255 << 24) | (int(r_val) << 16) | (int(g_val) << 8) | int(bv)
                        img.setPixel(col, row, argb)
                    else:
                        img.setPixel(col, row, 0)
        else:
            y_const = (self.L + 16.0) / 116.0
            y_val = y_const * y_const * y_const if y_const > 0.206893 else (y_const - 16.0/116.0) / 7.787

            # Precalculate x_val for columns
            x_vals = []
            for col in range(gen_size):
                a_val = min_a + (col / gen_size) * span_a
                x = a_val / 500.0 + y_const
                x_val = 0.96422 * (x * x * x if x > 0.206893 else (x - 16.0/116.0) / 7.787)
                x_vals.append(x_val)

            # Precalculate z_val for rows
            z_vals = []
            for row in range(gen_size):
                b_val = max_b - (row / gen_size) * span_b
                z = y_const - b_val / 200.0
                z_val = 0.82521 * (z * z * z if z > 0.206893 else (z - 16.0/116.0) / 7.787)
                z_vals.append(z_val)
                
            # Highly optimized rendering loop
            for row in range(gen_size):
                z_val = z_vals[row]
                for col in range(gen_size):
                    x_val = x_vals[col]
                    
                    x65 = 0.9554734 * x_val - 0.0230984 * y_val + 0.0632595 * z_val
                    y65 = -0.0283697 * x_val + 1.0099956 * y_val + 0.0210414 * z_val
                    z65 = 0.0123140 * x_val - 0.0205077 * y_val + 1.3303659 * z_val
                    
                    r = x65 * 3.2404542 + y65 * -1.5371385 + z65 * -0.4985314
                    g = x65 * -0.9692660 + y65 * 1.8760108 + z65 * 0.0415560
                    bl = x65 * 0.0556434 + y65 * -0.2040259 + z65 * 1.0572252
                    
                    r_gamma = 12.92 * r if r <= 0.0031308 else 1.055 * (max(0.0, r) ** 0.4166666666666667) - 0.055
                    g_gamma = 12.92 * g if g <= 0.0031308 else 1.055 * (max(0.0, g) ** 0.4166666666666667) - 0.055
                    b_gamma = 12.92 * bl if bl <= 0.0031308 else 1.055 * (max(0.0, bl) ** 0.4166666666666667) - 0.055
                    
                    r_rgb = r_gamma * 255.0
                    g_rgb = g_gamma * 255.0
                    b_rgb = b_gamma * 255.0
                    
                    if 0 <= r_rgb <= 255 and 0 <= g_rgb <= 255 and 0 <= b_rgb <= 255:
                        argb = (255 << 24) | (int(r_rgb) << 16) | (int(g_rgb) << 8) | int(b_rgb)
                        img.setPixel(col, row, argb)
                    else:
                        img.setPixel(col, row, 0)
                        
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
        
        # Show low-res prerender first if available
        used_prerender = False
        prerender_img = getattr(self, '_prerender_img', None)
        if prerender_img is not None:
            target = QRectF(offset_x, offset_y, size, size)
            painter.drawImage(target, prerender_img)
            self._prerender_img = None
            used_prerender = True
        else:
            self._render_ab_plane()
            if self._cached_img is not None:
                painter.drawImage(int(offset_x), int(offset_y), self._cached_img)
        
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
            self.update()  # schedule full-quality render next frame

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

    def set_lightness(self, lightness):
        self.L = lightness
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
