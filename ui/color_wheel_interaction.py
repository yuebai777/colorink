"""Interaction/hit-testing and colour-state methods for ColorWheel.

Extracted from ``ui.color_wheel``: mouse/pen drag handling, colour setters
and native-space reporting.
"""

import colorsys
import math

from PyQt6.QtCore import Qt

from ui.color_conversions import (
    find_max_lab_c,
    find_max_oklch_c,
    hsv_to_hsl,
    hsv_to_vhsv,
    lab_to_rgb,
    oklch_to_rgb,
    rgb_to_hsv,
    rgb_to_lab,
    rgb_to_oklch,
    rgb_to_vhsv,
    vhsv_to_rgb,
)
from ui.color_wheel_geometry import hls_to_hsv_floats, hsv_to_rgb, project_point_to_triangle


from ui.color_session import session_of


class ColorWheelInteractionMixin:

    def is_active_interaction(self):
        """True while this wheel — or anything else — is being dragged.

        The shared session answers it; scanning the window's sliders only
        works while every picker widget lives in that same window.
        """
        if self.dragging:
            return True
        session = session_of(self)
        if session is not None:
            return session.interacting
        win = self.window()
        if win is not None:
            slider_widgets = getattr(win, "slider_widgets", None)
            if isinstance(slider_widgets, dict):
                for chan, (slider, _) in slider_widgets.items():
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
        _, s_v, v_v = rgb_to_vhsv(r, g, b)
        self._vhsv_s = s_v
        self._vhsv_v = v_v
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
        _, s_v, v_v = hsv_to_vhsv(h, s, v)
        self._vhsv_s = s_v
        self._vhsv_v = v_v
        if abs(h - old_hue) > 0.01:
            self._invalidate_slice_caches()
        if update_widget:
            self.update()

    def set_vhsv(self, h, s, v, update_widget=True):
        old_hue = self.h
        self._clear_drag_anchor()
        if s > 0.5: self._last_hue = h
        else: h = self._last_hue
        self.h = h
        self._vhsv_s = s
        self._vhsv_v = v
        r, g, b = vhsv_to_rgb(h, s, v)
        _, s_std, v_std = rgb_to_hsv(r, g, b)
        self.s = s_std
        self.v = v_std
        if abs(h - old_hue) > 0.01:
            self._invalidate_slice_caches()
        if update_widget:
            self.update()

    def get_color(self):
        if self.wheel_mode == "vhsv-square":
            r, g, b = vhsv_to_rgb(self.h, getattr(self, "_vhsv_s", self.s), getattr(self, "_vhsv_v", self.v))
            return round(r), round(g), round(b)
        return hsv_to_rgb(self.h, self.s, self.v)

    def set_wheel_mode(self, mode):
        # "triangle" | "hsl-square" | "hsv-square" | "vhsv-square" | "hls-triangle" | "rgb-slice"
        self.wheel_mode = mode
        self.update()

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

    def native_color_values(self):
        """Return (space, values) for the colour currently shown by the wheel.

        Lets MainWindow build a unified Color from the wheel's own native
        space without an RGB round-trip (which would drift hue ~0.2 deg).
        """
        wm = self.wheel_mode
        if wm == "oklch-slice" and self._oklch_h is not None:
            L = self._oklch_L if self._oklch_L is not None else 0.5
            C = self._oklch_C if self._oklch_C is not None else 0.0
            return "oklch", (L, C, self._oklch_h)
        if wm == "vhsv-square":
            s_v = getattr(self, "_vhsv_s", self.s)
            v_v = getattr(self, "_vhsv_v", self.v)
            return "vhsv", (self.h, s_v, v_v)
        if wm == "hls-triangle":
            h, l, s = hsv_to_hsl(self.h, self.s, self.v)
            return "hls", (h, l, s)
        if wm == "rgb-slice":
            # Legacy source semantics: the RGB module is labelled "rgb" for sync.
            return "rgb", tuple(float(c) for c in self.get_color())
        return "hsv", (self.h, self.s, self.v)

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
                    if self.wheel_mode == "vhsv-square":
                        self.dragging = "vhsv-square"
                        self.handle_vhsv_square_drag(px, py, sg.center_x, sg.center_y, half)
                    elif self.wheel_mode == "hsv-square":
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
                    if self.wheel_mode == "vhsv-square":
                        self.dragging = "vhsv-square"
                        self.handle_vhsv_square_drag(pos.x(), pos.y(), cx, cy, half)
                    elif self.wheel_mode == "hsv-square":
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
            elif self.dragging == "vhsv-square":
                half = int(r / 1.414) - 2
                self.handle_vhsv_square_drag(px, py, cx, cy, half)

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
            if (hasattr(self, '_drag_L') and self._drag_L is not None
                    and hasattr(self, '_drag_C') and self._drag_C is not None):
                L_ring, C_ring = self._drag_L, self._drag_C
            elif self._oklch_L is not None and self._oklch_C is not None:
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

        # Raw (unclamped) L/C from the mouse. Whether the raw point sits
        # inside the gamut decides if we need to snap — clamping L first
        # would pin the cursor to the box corners when the mouse leaves
        # the slice above or below.
        L_raw = (cy + hy - py) / (2.0 * hy)
        C_raw = (px - min_x) / scale
        C_max_raw = find_max_lab_c(max(0.0, min(100.0, L_raw * 100.0)), a_dir, b_dir)
        if 0.0 <= L_raw <= 1.0 and 0.0 <= C_raw <= C_max_raw:
            # Inside the gamut — exact position under the mouse.
            L, L_val = L_raw, L_raw * 100.0
            C = max(0.0, C_raw)
        else:
            # Mouse outside gamut — snap to the nearest boundary point.
            L, C = self._snap_to_boundary_rgb(
                px, py, cx, cy, hy, min_x, scale, a_dir, b_dir)
            L = max(0.0, min(1.0, L))
            L_val = L * 100.0
            C = max(0.0, min(C, find_max_lab_c(L_val, a_dir, b_dir)))
        
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

    def handle_vhsv_square_drag(self, px, py, cx, cy, half):
        rel_x = px - (cx - half)
        rel_y = py - (cy - half)
        
        s_val = max(0.0, min(1.0, rel_x / float(half * 2)))
        v_val = max(0.0, min(1.0, 1.0 - rel_y / float(half * 2)))
        
        self._vhsv_s = max(0.0, min(100.0, s_val * 100.0))
        self._vhsv_v = max(0.0, min(100.0, v_val * 100.0))

        r, g, b = vhsv_to_rgb(self.h, self._vhsv_s, self._vhsv_v)
        _, s_std, v_std = rgb_to_hsv(r, g, b)
        self.s = max(0.0, min(100.0, s_std))
        self.v = max(0.0, min(100.0, v_std))
        self.update()
        ri, gi, bi = round(r), round(g), round(b)
        self.colorChanged.emit(ri, gi, bi)

    def handle_oklch_slice_drag(self, px, py, cx, cy, r):
        hy = r * 0.866
        box_w = self._oklch_slice_box_width(r)
        min_x = int(math.floor(cx - box_w * 0.5))
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
        if oklch_h is None:
            oklch_h = self._oklch_h
            if oklch_h is None:
                cr, cg, cb = self.get_color()
                _, _, oklch_h = rgb_to_oklch(cr, cg, cb)

        L_raw = (cy + hy - py) / (2.0 * hy)
        C_raw = (px - min_x) / scale
        C_max_raw = find_max_oklch_c(max(0.0, min(1.0, L_raw)), oklch_h)
        if 0.0 <= L_raw <= 1.0 and 0.0 <= C_raw <= C_max_raw:
            # Inside the gamut — exact position under the mouse.
            L, C = L_raw, max(0.0, C_raw)
        else:
            # Mouse outside gamut — snap to the nearest boundary point.
            L, C = self._snap_to_boundary_oklch(
                px, py, cx, cy, hy, min_x, scale, oklch_h)
            L = max(0.0, min(1.0, L))
            C = max(0.0, min(C, find_max_oklch_c(L, oklch_h)))

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

    def _snap_to_boundary_rgb(self, px, py, cx, cy, hy, min_x, scale, a_dir, b_dir):
        """Find the closest in-gamut (L_frac, C) when the mouse is outside
        the RGB slice's gamut region.

        The region is bounded on the right by the max-chroma curve and on
        the left by the C=0 neutral axis.  The nearest point is the best of
        a coarse+fine scan over the curve and the closed-form nearest point
        on the left edge, all measured in widget pixels so the answer is
        truly "closest to the mouse" regardless of direction.
        """
        def curve_point(t):
            Cb = find_max_lab_c(t * 100.0, a_dir, b_dir)
            return Cb, min_x + Cb * scale, cy + hy * (1.0 - 2.0 * t)

        # Coarse pass over the right boundary curve
        best_L, best_dist = 0.0, float('inf')
        for t in [i / 25.0 for i in range(26)]:
            Cb, bx, by = curve_point(t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        # Fine pass around best
        lo = max(0.0, best_L - 0.04)
        hi = min(1.0, best_L + 0.04)
        for i in range(21):
            t = lo + (hi - lo) * i / 20.0
            Cb, bx, by = curve_point(t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t

        # Left edge candidate: C = 0, L clamped to the slice's L range.
        L_edge = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        ex = min_x
        ey = cy + hy * (1.0 - 2.0 * L_edge)
        d_edge = (ex - px) ** 2 + (ey - py) ** 2
        if d_edge < best_dist:
            return L_edge, 0.0
        return best_L, find_max_lab_c(best_L * 100.0, a_dir, b_dir)

    def _snap_to_boundary_oklch(self, px, py, cx, cy, hy, min_x, scale, oklch_h):
        """Find the closest in-gamut (L, C) when the mouse is outside the
        OKLCh slice's gamut region.

        Mirrors ``_snap_to_boundary_rgb``: coarse+fine scan of the max-C
        curve plus the closed-form nearest point on the C=0 neutral axis,
        measured in widget pixels.
        """
        def curve_point(t):
            Cb = find_max_oklch_c(t, oklch_h)
            return Cb, min_x + Cb * scale, cy + hy * (1.0 - 2.0 * t)

        best_L, best_dist = 0.0, float('inf')
        for t in [i / 25.0 for i in range(26)]:
            Cb, bx, by = curve_point(t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t
        lo = max(0.0, best_L - 0.04)
        hi = min(1.0, best_L + 0.04)
        for i in range(21):
            t = lo + (hi - lo) * i / 20.0
            Cb, bx, by = curve_point(t)
            d = (bx - px) ** 2 + (by - py) ** 2
            if d < best_dist:
                best_dist = d
                best_L = t

        # Left edge candidate: C = 0, L clamped to the slice's L range.
        L_edge = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        ex = min_x
        ey = cy + hy * (1.0 - 2.0 * L_edge)
        d_edge = (ex - px) ** 2 + (ey - py) ** 2
        if d_edge < best_dist:
            return L_edge, 0.0
        return best_L, find_max_oklch_c(best_L, oklch_h)
