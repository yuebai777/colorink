"""Rendering/drawing methods for ColorWheel.

Extracted from ``ui.color_wheel``: all paint/draw helpers for the colour
wheel, triangle/square slices and OKLCh gamut slice.
"""

import colorsys
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from ui.color_conversions import (
    find_max_lab_c,
    find_max_oklch_c,
    oklch_to_rgb,
    rgb_to_lab,
    rgb_to_oklch,
)
from ui.color_wheel_geometry import hsv_to_rgb


class ColorWheelRenderingMixin:

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

        subsample = 3 if self.is_active_interaction() else 1
        _, img = self._render_slice_image(
            "hsl-square", self.h, cx, cy, r, subsample=subsample)

        self._cached_img = img
        self._cached_img_key = cache_key

        painter.drawImage(int(cx - half), int(cy - half), img)

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

        subsample = 3 if self.is_active_interaction() else 1
        _, img = self._render_slice_image(
            "hsv-square", self.h, cx, cy, r, subsample=subsample)

        self._cached_img = img
        self._cached_img_key = cache_key

        painter.drawImage(int(cx - half), int(cy - half), img)
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

        # Keep every active drag responsive; end_drag() invalidates the cache
        # so the next paint restores the full-quality image.
        subsample = 3 if self.is_active_interaction() else 1
        result, img = self._render_slice_image(
            "hls-triangle", self.h, cx, cy, r, subsample=subsample)

        self._cached_hls_key = cache_key
        self._cached_hls_img = img
        self._cached_hls_minx = result.min_x
        self._cached_hls_miny = result.min_y

        painter.drawImage(result.min_x, result.min_y, img)
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

        # RGB→Lab conversion is the most expensive slice renderer, so use a
        # slightly coarser active preview to keep the indicator under one frame.
        subsample = 5 if self.is_active_interaction() else 1
        result, img = self._render_slice_image(
            "rgb-slice", self.h, cx, cy, r, subsample=subsample)

        self._cached_rgb_key = cache_key
        self._cached_rgb_img = img
        self._cached_rgb_minx = result.min_x
        self._cached_rgb_miny = result.min_y

        painter.drawImage(result.min_x, result.min_y, img)

        # Save edge data and draw outline
        if result.edge_x is not None:
            self._cached_rgb_edge = (
                result.edge_x, result.min_x, result.min_y,
                result.min_y + result.height, result.height,
            )
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
        if (getattr(self, '_drag_slice', '') == "rgb"
                and hasattr(self, '_drag_C') and self._drag_C is not None
                and hasattr(self, '_drag_L') and self._drag_L is not None):
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

    def draw_oklch_slice(self, painter, cx, cy, r):
        # Derive OKLCh hue from drag cache, stored state, or current RGB color
        oklch_h = getattr(self, '_drag_oklch_h', None)
        if oklch_h is None:
            oklch_h = self._oklch_h
        if oklch_h is None:
            rgb_r, rgb_g, rgb_b = self.get_color()
            _, _, oklch_h = rgb_to_oklch(rgb_r, rgb_g, rgb_b)

        hy = r * 0.866
        box_w = self._oklch_slice_box_width(r)
        min_x = int(math.floor(cx - box_w * 0.5))
        max_x = int(math.ceil(cx + box_w * 0.5))
        min_y = int(math.floor(cy - hy))
        max_y = int(math.ceil(cy + hy))
        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return

        cache_key = (round(oklch_h, 1), r, round(cx, 3), round(cy, 3), round(box_w, 3), "oklch")
        prewarmed = self._prewarmed_slices.get("oklch-slice")
        if prewarmed is not None and prewarmed.get("key") == cache_key:
            painter.drawImage(int(prewarmed["min_x"]), int(prewarmed["min_y"]), prewarmed["image"])
            # The prewarmed image replaces the fallback render, but the gamut
            # outline must still be drawn (mirrors draw_rgb_slice).  Without
            # it the stroke vanishes after a hue change once prewarming lands.
            self._draw_oklch_outline(painter, min_x, cy, hy, r, oklch_h)
            return
        img_ready = (hasattr(self, "_cached_oklch_key")
                     and self._cached_oklch_key == cache_key
                     and hasattr(self, "_cached_oklch_img"))

        if not img_ready:
            # Keep every active drag responsive; end_drag() invalidates the cache
            # so the next paint restores the full-quality image.
            subsample = 3 if self.is_active_interaction() else 1
            scale = self._oklch_scale_for_hue(oklch_h, r)
            result, img = self._render_slice_image(
                "oklch-slice", oklch_h, cx, cy, r,
                width=box_w, scale=scale, subsample=subsample)

            self._cached_oklch_key = cache_key
            self._cached_oklch_img = img
            self._cached_oklch_minx = result.min_x
            self._cached_oklch_miny = result.min_y

        painter.drawImage(int(self._cached_oklch_minx),
                          int(self._cached_oklch_miny),
                          self._cached_oklch_img)

        # ── sRGB gamut boundary outline ──
        self._draw_oklch_outline(painter, min_x, cy, hy, r, oklch_h)

    def _draw_oklch_outline(self, painter, min_x, cy, hy, r, oklch_h):
        """Draw the sRGB gamut boundary around the OKLCh slice.

        Traces the max-C curve rightward, then closes along C=0 (the
        neutral axis).  The 201-point boundary gives a sharp peak.
        """
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
        box_w = self._oklch_slice_box_width(r)
        min_x = int(math.floor(cx - box_w * 0.5))

        # Hue from drag cache or stored OKLCh state (avoids round-trip drift)
        oklch_h = getattr(self, '_drag_oklch_h', None)
        if oklch_h is None:
            oklch_h = self._oklch_h

        if oklch_h is None:
            rgb_r, rgb_g, rgb_b = self.get_color()
            _, _, oklch_h = rgb_to_oklch(rgb_r, rgb_g, rgb_b)

        scale = self._oklch_scale_for_hue(oklch_h, r)

        if (getattr(self, '_drag_slice', '') == "oklch"
                and hasattr(self, '_drag_C') and self._drag_C is not None
                and hasattr(self, '_drag_L') and self._drag_L is not None):
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

        # Keep the indicator inside the gamut at the current hue.  After a
        # hue change the stored (L, C) can exceed the new hue's max chroma
        # (the hue ring keeps L/C fixed and clamps the colour instead), so
        # without this the dot would float outside the coloured region.
        C = min(C, find_max_oklch_c(L, oklch_h))

        px = min_x + C * scale
        py = cy + hy * (1.0 - 2.0 * L)

        self.draw_indicator_ring(painter, QPointF(px, py))
