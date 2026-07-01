import math
import colorsys
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QImage, QPen, QBrush, QConicalGradient, QPainterPath, QLinearGradient
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from ui.lab_visualizer import lab_to_rgb, rgb_to_lab
from core import config

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

def find_max_c(L_val, a_dir, b_dir):
    low = 0.0
    high = 150.0
    for _ in range(12):
        mid = (low + high) / 2.0
        r, g, b = lab_to_rgb(L_val, mid * a_dir, mid * b_dir)
        if 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= b <= 255.0:
            low = mid
        else:
            high = mid
    return low

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
        
        self.dragging = None
        
        # Mode
        self.wheel_mode = "hsv-square"
        
        # Cache variables for fast rendering
        self._cached_img = None
        self._cached_img_key = None
        
    def reload_config(self):
        self.cfg = config.load_hotkey_config()
        # Invalidate the ring cache so it gets redrawn with the new settings
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        self.update()

    def is_active_interaction(self):
        if self.dragging:
            return True
        win = self.window()
        if win is not None and hasattr(win, "slider_widgets"):
            for chan, (slider, _) in win.slider_widgets.items():
                if slider.isSliderDown():
                    return True
        return False

    def set_color(self, r, g, b, block_signals=False):
        h, s, v = rgb_to_hsv(r, g, b)
        self.h = h
        self.s = s
        self.v = v
        self.update()
        if not block_signals:
            self.colorChanged.emit(r, g, b)

    def set_hsv(self, h, s, v):
        self.h = h
        self.s = s
        self.v = v
        self.update()

    def get_color(self):
        return hsv_to_rgb(self.h, self.s, self.v)

    def set_wheel_mode(self, mode):
        # "triangle" | "hsl-square" | "hsv-square" | "hls-triangle" | "rgb-slice"
        self.wheel_mode = mode
        self.update()

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
        triangle_radius = max(1.0, inner_radius - 7.0)
        
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        cx, cy, size, outer_radius, inner_radius, triangle_radius = self.get_wheel_geometry()
        if size <= 20:
            return
            
        # 1) Draw Hue Ring with Caching
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
        
        # 2) Draw SV triangle, HSL square, HSV square, HLS triangle, or RGB slice
        if self.wheel_mode == "triangle":
            self.draw_triangle(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "hsv-square":
            self.draw_hsv_square(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "hls-triangle":
            self.draw_hls_triangle(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "rgb-slice":
            self.draw_rgb_slice(painter, cx, cy, triangle_radius)
        else:
            self.draw_hsl_square(painter, cx, cy, triangle_radius)
            
        # 3) Draw Hue Indicator on Ring
        self.draw_hue_indicator(painter, cx, cy, inner_radius, outer_radius)
        
        # 4) Draw SV/HSL/HSV Indicator inside
        if self.wheel_mode == "triangle":
            self.draw_sv_indicator(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "hsv-square":
            self.draw_hsv_square_indicator(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "hls-triangle":
            self.draw_hls_indicator(painter, cx, cy, triangle_radius)
        elif self.wheel_mode == "rgb-slice":
            self.draw_rgb_indicator(painter, cx, cy, triangle_radius)
        else:
            self.draw_hsl_indicator(painter, cx, cy, triangle_radius)

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
        cache_key = (int(self.h), width, height, "square", self.is_active_interaction())
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
        cache_key = (int(self.h), width, height, "hsv-square", self.is_active_interaction())
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

    def draw_hue_indicator(self, painter, cx, cy, inner_r, outer_r):
        if self.cfg.get("flipColorWheelHorizontally", False):
            angle_deg = (150.0 - self.h) % 360.0
        else:
            angle_deg = (self.h + 30.0) % 360.0
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
        cache_key = (self.h, r, "hls", self.is_active_interaction())
        if hasattr(self, "_cached_hls_key") and self._cached_hls_key == cache_key and hasattr(self, "_cached_hls_img"):
            painter.drawImage(int(self._cached_hls_minx), int(self._cached_hls_miny), self._cached_hls_img)
            return
            
        v0, v1, v2 = self.get_triangle_vertices(cx, cy, r)
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
            
        # Use subsampling only during active dragging for maximum responsiveness
        if self.is_active_interaction():
            subsample = 3
        else:
            subsample = 1
            
        sub_w = max(1, width // subsample)
        sub_h = max(1, height // subsample)
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        img.fill(0)
        
        for y in range(sub_h):
            py = min_y + y * subsample
            l_val = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
            px_right = px_left + 3.0 * r * (0.5 - abs(l_val - 0.5))
            row_w = px_right - px_left
            
            for x in range(sub_w):
                px = min_x + x * subsample
                if px >= px_left and px <= px_right and self.is_point_in_triangle(px, py, v0, v1, v2):
                    s_val = (px - px_left) / row_w if row_w > 0.001 else 0.0
                    s_val = max(0.0, min(1.0, s_val))
                    red, green, blue = colorsys.hls_to_rgb(self.h / 360.0, l_val, s_val)
                    img.setPixelColor(x, y, QColor(int(red * 255), int(green * 255), int(blue * 255)))
                    
        self._cached_hls_key = cache_key
        if subsample > 1:
            self._cached_hls_img = img.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self._cached_hls_img = img
        self._cached_hls_minx = min_x
        self._cached_hls_miny = min_y
        
        painter.drawImage(min_x, min_y, self._cached_hls_img)
        
        # Stroke boundary
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
        cache_key = (self.h, r, "rgb", self.is_active_interaction())
        if hasattr(self, "_cached_rgb_key") and self._cached_rgb_key == cache_key and hasattr(self, "_cached_rgb_img"):
            painter.drawImage(int(self._cached_rgb_minx), int(self._cached_rgb_miny), self._cached_rgb_img)
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
            
        # Use subsampling only during active dragging for maximum responsiveness
        if self.is_active_interaction():
            subsample = 3
        else:
            subsample = 1
            
        sub_w = max(1, width // subsample)
        sub_h = max(1, height // subsample)
        
        img = QImage(sub_w, sub_h, QImage.Format.Format_ARGB32)
        img.fill(0)
        
        scale = (r * 1.3) / 130.0
        
        pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
        l_p, a_p, b_p = rgb_to_lab(pure_r, pure_g, pure_b)
        C_pure = math.sqrt(a_p * a_p + b_p * b_p)
        a_dir = a_p / C_pure if C_pure > 0.001 else 0.0
        b_dir = b_p / C_pure if C_pure > 0.001 else 0.0
        
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
                
                if (-0.5 <= rgb_r <= 255.5 and 
                    -0.5 <= rgb_g <= 255.5 and 
                    -0.5 <= rgb_b <= 255.5):
                    
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
        
        # Calculate full-resolution edge_x for smooth outline path
        edge_x = [min_x] * height
        for y in range(height):
            if subsample > 1:
                sub_y = min(sub_h - 1, y // subsample)
                edge_x[y] = sub_edge_x[sub_y]
            else:
                edge_x[y] = sub_edge_x[y]
        
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
        min_x = cx - r * 0.5
        scale = (r * 1.3) / 130.0
        
        rgb_r, rgb_g, rgb_b = self.get_color()
        l_lab, a_lab, b_lab = rgb_to_lab(rgb_r, rgb_g, rgb_b)
        
        C = math.sqrt(a_lab * a_lab + b_lab * b_lab)
        px = min_x + C * scale
        py = cy + hy * (1.0 - 2.0 * (l_lab / 100.0))
        
        pos = QPointF(px, py)
        self.draw_indicator_ring(painter, pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
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
            cx, cy, _, _, _, triangle_radius = self.get_wheel_geometry()
            pos = event.position()
            if self.dragging == "hue":
                self.handle_hue_drag(pos.x(), pos.y(), cx, cy)
            elif self.dragging == "triangle":
                self.handle_triangle_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
            elif self.dragging == "hls-triangle":
                self.handle_hls_triangle_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
            elif self.dragging == "rgb-slice":
                self.handle_rgb_slice_drag(pos.x(), pos.y(), cx, cy, triangle_radius)
            elif self.dragging == "square":
                half = int(triangle_radius / 1.414) - 2
                self.handle_square_drag(pos.x(), pos.y(), cx, cy, half)
            elif self.dragging == "hsv-square":
                half = int(triangle_radius / 1.414) - 2
                self.handle_hsv_square_drag(pos.x(), pos.y(), cx, cy, half)

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
        self.update()
        self.interactionFinished.emit()

    def handle_hue_drag(self, px, py, cx, cy):
        dy = -(py - cy)
        dx = px - cx
        angle = math.atan2(dy, dx)
        deg = math.degrees(angle)
        if deg < 0:
            deg += 360.0
            
        if self.cfg.get("flipColorWheelHorizontally", False):
            self.h = (150.0 - deg) % 360.0
        else:
            self.h = (deg - 30.0) % 360.0
            
        if self.h < 0:
            self.h += 360.0
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
        min_x = cx - r * 0.5
        scale = (r * 1.3) / 130.0
        
        L = max(0.0, min(1.0, (cy + hy - py) / (2.0 * hy)))
        L_val = L * 100.0
        
        pure_r, pure_g, pure_b = hsv_to_rgb(self.h, 100.0, 100.0)
        l_p, a_p, b_p = rgb_to_lab(pure_r, pure_g, pure_b)
        C_pure = math.sqrt(a_p * a_p + b_p * b_p)
        a_dir = a_p / C_pure if C_pure > 0.001 else 0.0
        b_dir = b_p / C_pure if C_pure > 0.001 else 0.0
        
        C_max = find_max_c(L_val, a_dir, b_dir)
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
