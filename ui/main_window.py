import sys
import os
import math
import colorsys
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QStackedWidget, QSlider, QLabel, QFrame,
                             QGraphicsDropShadowEffect, QApplication, QStyle, QStyleOptionSlider)
from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSlot, QRectF, pyqtSignal, QRect, QPointF, QEvent
from PyQt6.QtGui import QColor, QPalette, QLinearGradient, QPainter, QBrush, QPen, QPixmap, QCursor, QPolygonF

from core import config
from core import memory_sync
from core import global_hotkeys
from ui.color_wheel import ColorWheel, hsv_to_rgb, rgb_to_hsv, hls_to_hsv_floats
from ui.lab_visualizer import LabSquare, LabSlider, lab_to_rgb, rgb_to_lab
from ui.oklab_colors import oklab_to_rgb, rgb_to_oklab, oklch_to_rgb, rgb_to_oklch
from ui.slider_themes import get_slider_theme
from ui.settings_sidebar import SettingsSidebar
from ui.grayscale_overlay import GrayscaleOverlay
from ui.color_history import ColorHistoryWidget

def bring_process_to_foreground(pid: int) -> bool:
    import ctypes
    user32 = ctypes.windll.user32
    
    hwnd_to_focus = None
    
    def enum_windows_callback(hwnd, lParam):
        nonlocal hwnd_to_focus
        if user32.IsWindowVisible(hwnd):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                parent = user32.GetParent(hwnd)
                owner = user32.GetWindow(hwnd, 4)  # GW_OWNER = 4
                if parent == 0 or parent is None:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        # Prefer ownerless window (main window)
                        if owner == 0 or owner is None:
                            hwnd_to_focus = hwnd
                            return False  # Stop enumeration
                        else:
                            if hwnd_to_focus is None:
                                hwnd_to_focus = hwnd
        return True
        
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    callback = WNDENUMPROC(enum_windows_callback)
    user32.EnumWindows(callback, 0)
    
    if hwnd_to_focus:
        is_minimized = user32.IsIconic(hwnd_to_focus)
        user32.ShowWindowAsync(hwnd_to_focus, 9 if is_minimized else 5)  # 9 = SW_RESTORE, 5 = SW_SHOW
        user32.BringWindowToTop(hwnd_to_focus)
        user32.SetForegroundWindow(hwnd_to_focus)
        return True
    return False

def hsv_to_hls_floats(h, s, v):
    # h: [0, 360], s: [0, 100], v: [0, 100]
    h_f = h / 360.0
    s_f = s / 100.0
    v_f = v / 100.0
    l_f = v_f * (1.0 - s_f / 2.0)
    if 0.0 < l_f < 1.0:
        hsl_s = (v_f - l_f) / min(l_f, 1.0 - l_f)
    else:
        hsl_s = 0.0
    return h_f, l_f, hsl_s

class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.drag_position = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_ui()

    def init_ui(self):
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        # Settings Button (Hamburger)
        self.btn_settings = QPushButton("☰")
        self.btn_settings.setFixedSize(9, 9)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 7px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                border-radius: 2px;
            }
        """)

        # Title
        self.title_label = QLabel("Colorink")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 7px;")
        
        # Minimize Button
        self.btn_min = QPushButton("—")
        self.btn_min.setFixedSize(9, 9)
        self.btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_min.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 6px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                border-radius: 2px;
            }
        """)
        self.btn_min.clicked.connect(self.parent.showMinimized)
        
        # Close Button
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(9, 9)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 8px;
            }
            QPushButton:hover {
                background-color: #ff5050;
                color: white;
                border-radius: 2px;
            }
        """)

        layout.addWidget(self.btn_settings)
        layout.addStretch()
        layout.addWidget(self.title_label)
        layout.addStretch()
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.parent.cfg.get("lockWindowPosition", False):
                self.drag_position = event.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position is not None:
            if not self.parent.cfg.get("lockWindowPosition", False):
                self.parent.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None


class GradientSlider(QSlider):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.gradient_colors = []
        self.groove_h = 16
        self.groove_radius = 3.0
        self.scale = 1.0
        self._theme = get_slider_theme("default")
        self.update_scale(1.0)
        self._gamut_min = None
        self._gamut_max = None

    def set_in_gamut_range(self, mn, mx):
        """Set the valid in-gamut L range.
        Values outside [mn, mx] will be grayed on the track.
        Pass None for both to clear the marking."""
        self._gamut_min = mn
        self._gamut_max = mx
        self.update()

    def clear_in_gamut_range(self):
        self._gamut_min = None
        self._gamut_max = None
        self.update()

    def wheelEvent(self, event):
        # Read the step size from configuration or parent window
        step = 1
        win = self.window()
        if win is not None and hasattr(win, "cfg"):
            step = win.cfg.get("sliderScrollStep", 1)
        
        delta = event.angleDelta().y()
        if delta == 0:
            return
            
        steps_to_move = step
        if delta < 0:
            steps_to_move = -step
            
        new_val = self.value() + steps_to_move
        new_val = max(self.minimum(), min(self.maximum(), new_val))
        self.setValue(new_val)
        event.accept()

    def update_scale(self, scale, theme=None):
        if theme is not None:
            self._theme = theme
        t = self._theme
        handle_shape = str(t.get("handle_shape", "rect"))
        self.scale = scale
        self.groove_h = max(2, int(16 * scale * float(t["groove_h_factor"])))
        self.groove_radius = 3.0 * scale * float(t["groove_radius_factor"])
        handle_w = max(2, int(5 * scale * float(t["handle_w_factor"])))
        handle_h = max(4, int(24 * scale * float(t["handle_h_factor"])))
        margin_y = -max(1, int(4 * scale * float(t["handle_margin_y_factor"])))
        border_radius = max(0, int(1 * scale * float(t["handle_radius_factor"])))

        if handle_shape == "triangle-below":
            # Native handle is invisible (but kept at standard hit size so
            # mouse drag still works). We draw the triangle ourselves in
            # paintEvent below the groove.
            self.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    height: {self.groove_h}px;
                    background: transparent;
                }}
                QSlider::handle:horizontal {{
                    background: transparent;
                    border: none;
                    width: {handle_w}px;
                    height: {handle_h}px;
                    margin: 0px;
                }}
            """)
            # Triangle needs extra vertical space below the groove
            tri_off = int(float(t.get("handle_tri_offset_y", 2)) * scale)
            tri_h = int(float(t.get("handle_tri_size_h", 6)) * scale)
            pad = max(2, int(2 * scale))
            self.setMinimumHeight(self.groove_h + tri_off + tri_h + pad)
        else:
            # Standard visible native handle (geometry configured via stylesheet, custom drawn in paintEvent)
            self.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    height: {self.groove_h}px;
                    background: transparent;
                }}
                QSlider::handle:horizontal {{
                    background: transparent;
                    border: none;
                    width: {handle_w}px;
                    height: {handle_h}px;
                    margin-top: {margin_y}px;
                    margin-bottom: {margin_y}px;
                    border-radius: {border_radius}px;
                }}
            """)
            # Reserve space for the handle's overhangs above and below the groove
            self.setMinimumHeight(self.groove_h + 2 * abs(margin_y))

    def set_gradient(self, colors):
        if hasattr(self, "_cached_colors") and self._cached_colors == colors:
            return
        self._cached_colors = colors
        self.gradient_colors = colors
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        groove_y = (rect.height() - self.groove_h) // 2
        groove_rect = QRectF(0, groove_y, rect.width(), self.groove_h)
        
        grad = QLinearGradient(0, 0, rect.width(), 0)
        for stop, color in self.gradient_colors:
            grad.setColorAt(stop, color)
             
        # Fill groove (no border)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(groove_rect, self.groove_radius, self.groove_radius)
        
        # Draw out-of-gamut gray overlay (no border)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._gamut_min is not None and self._gamut_max is not None:
            vmin = self.minimum()
            vrange = self.maximum() - vmin
            if vrange > 0:
                left_frac = (self._gamut_min - vmin) / vrange
                right_frac = (self._gamut_max - vmin) / vrange
                
                painter.setBrush(QColor(160, 160, 160, 140))
                
                if left_frac > 0.005:
                    painter.drawRect(QRectF(0, groove_y, rect.width() * left_frac, self.groove_h))
                if right_frac < 0.995:
                    painter.drawRect(QRectF(rect.width() * right_frac, groove_y, rect.width() * (1.0 - right_frac), self.groove_h))

        t = self._theme
        handle_shape = str(t.get("handle_shape", "rect"))
        
        # Get the exact handle bounding box from Qt QStyle to ensure perfect alignment
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
        )
        is_hover = bool(opt.activeSubControls & QStyle.SubControl.SC_SliderHandle)

        if handle_shape == "triangle-below":
            # Draw a small triangle (apex up) just below the groove at the
            # current value position. The native handle is invisible per the
            # style sheet, so this is the user-visible indicator.
            handle_x = handle_rect.center().x()

            tri_color = QColor(t.get("handle_tri_color", t["handle_bg"]))
            tri_size_w = float(t.get("handle_tri_size_w", 5)) * self.scale
            tri_size_h = float(t.get("handle_tri_size_h", 6)) * self.scale
            tri_offset_y = int(float(t.get("handle_tri_offset_y", 2)) * self.scale)
            tri_base_y = groove_y + self.groove_h + tri_offset_y

            painter.setBrush(tri_color)
            triangle = QPolygonF([
                QPointF(handle_x, tri_base_y),
                QPointF(handle_x - tri_size_w, tri_base_y + tri_size_h),
                QPointF(handle_x + tri_size_w, tri_base_y + tri_size_h),
            ])
            painter.setPen(Qt.PenStyle.NoPen)
            triangle_poly = painter.drawPolygon(triangle)
            
            # Concentric double-ring: black on original triangle,
            # white (or hover color) on a slightly inset copy so they nest cleanly.
            ring_w = max(1, int(1.5 * self.scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 200), ring_w))
            painter.drawPolygon(triangle)
            
            # Compute inset triangle — move each vertex toward centroid
            pts = [
                QPointF(handle_x, tri_base_y),
                QPointF(handle_x - tri_size_w, tri_base_y + tri_size_h),
                QPointF(handle_x + tri_size_w, tri_base_y + tri_size_h),
            ]
            cx = sum(p.x() for p in pts) / 3.0
            cy = sum(p.y() for p in pts) / 3.0
            inset = ring_w
            inner = QPolygonF()
            for p in pts:
                dx, dy = p.x() - cx, p.y() - cy
                d = (dx * dx + dy * dy) ** 0.5
                if d > inset:
                    inner.append(QPointF(cx + dx * (d - inset) / d,
                                         cy + dy * (d - inset) / d))
                else:
                    inner.append(QPointF(cx, cy))
            
            border_color = QColor(t["handle_hover_border"]) if is_hover else QColor(255, 255, 255, 220)
            painter.setPen(QPen(border_color, ring_w))
            painter.drawPolygon(inner)
            painter.end()
            # Do NOT call super().paintEvent — we own this paint
        else:
            # Draw the rect handle ourselves using the exact QStyle geometry.
            # No native styling conflicts, no alignment issues, 100% pixel-perfect.
            handle_f = QRectF(handle_rect)

            # 1. Fill handle background
            bg_color_str = t["handle_hover_bg"] if is_hover else t["handle_bg"]
            hr = max(0, int(1 * self.scale * float(t["handle_radius_factor"])))
            
            if bg_color_str and bg_color_str != "transparent":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(bg_color_str))
                painter.drawRoundedRect(handle_f, hr, hr)
            
            # 2. Draw double-ring border
            ring_w = max(1, int(1.5 * self.scale))
            
            # Outer ring: black for high-contrast visibility against colored/light tracks
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 200), ring_w))
            painter.drawRoundedRect(handle_f, hr, hr)
            
            # Inner ring: theme border color (grey/blue)
            border_color_str = t["handle_hover_border"] if is_hover else t["handle_border"]
            border_color = QColor(border_color_str)
            
            inner_f = QRectF(handle_f.x() + ring_w, handle_f.y() + ring_w,
                             handle_f.width() - 2 * ring_w, handle_f.height() - 2 * ring_w)
            
            if inner_f.width() > 0 and inner_f.height() > 0:
                inner_r = max(0, hr - ring_w)
                painter.setPen(QPen(border_color, ring_w))
                painter.drawRoundedRect(inner_f, inner_r, inner_r)
            
            painter.end()
            # Do NOT call super().paintEvent — we own this paint


class ClickableFrame(QFrame):
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
        
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class PaneWithModeButton(QWidget):
    """Base for stacked panes that own the floating mode-switcher button.

    The button is positioned in the pane's OWN coordinate system on every
    resize — so it always sits at the bottom-right of whatever geometry the
    pane actually has, regardless of when the parent layout settles. No
    sizeHint guessing, no deferred QTimer re-passes.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode_btn = None
        self._btn_size = 28      # px, updated by MainWindow from uiScale
        self._btn_margin = 6

    def set_mode_button(self, btn):
        self._mode_btn = btn

    def set_mode_button_metrics(self, size, margin):
        self._btn_size = size
        self._btn_margin = margin
        self._reposition_mode_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_mode_button()

    def _reposition_mode_button(self):
        btn = self._mode_btn
        if btn is None:
            return
        bw = self._btn_size
        bh = self._btn_size
        m = self._btn_margin
        px = self.width() - m - bw
        py = self.height() - m - bh
        if px < 0 or py < 0:
            return  # pane not laid out yet
        btn.setFixedSize(bw, bh)
        btn.setGeometry(px, py, bw, bh)
        btn.raise_()


class WheelPane(PaneWithModeButton):
    """Pane hosting the HSV/OKLCh color wheel + its floating mode button."""
    pass


class LabPane(PaneWithModeButton):
    """Pane for the LAB visualizer; also paints a tiled checkerboard background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.checker_pixmap = QPixmap(16, 16)
        self.checker_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.checker_pixmap)
        painter.fillRect(0, 0, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 8, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 0, 8, 8, QColor(0, 0, 0, 15))
        painter.fillRect(0, 8, 8, 8, QColor(0, 0, 0, 15))
        painter.end()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(self.rect(), self.checker_pixmap)
        painter.end()


class ColorPreviewBox(QWidget):
    """Overlapping color circles preview widget drawn with QPainter for perfect z-order and anti-aliasing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.fg_color = QColor(255, 255, 255)
        self.bg_color = QColor(128, 128, 128)
        self.position_mode = "top-left"  # "top-left" | "bottom-left"
        self.active_slot = "fg"
        self.fg_size = 40
        self.bg_size = 26
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_colors(self, fg, bg):
        self.fg_color = fg
        self.bg_color = bg
        self.update()

    def update_slot_borders(self, active_slot):
        self.active_slot = active_slot
        self.update()

    def resize_and_position(self, wheel_size, title_bar_h, window_h, sliders_h, active_slot):
        # Calculate scale factor relative to default wheel size 304 to dynamically scale with the color wheel width
        wheel_scale = wheel_size / 304.0
        
        self.fg_size = int(46 * wheel_scale)
        self.bg_size = int(30 * wheel_scale)
        self.active_slot = active_slot
        
        box_dim = int(60 * wheel_scale)
        self.setFixedSize(box_dim, box_dim)
        
        # Position at the top-left corner of the window with clean margins
        margin_x = int(6 * wheel_scale)
        spacing = int(4 * wheel_scale)
        
        if self.position_mode == "top-left":
            margin_y = title_bar_h + spacing + int(6 * wheel_scale)
            self.move(margin_x, margin_y)
        else:
            self.move(margin_x, window_h - sliders_h - box_dim - int(6 * wheel_scale))

    def draw_circle(self, painter, cx, cy, r, color, active):
        # Draw shadow
        painter.setBrush(QBrush(QColor(0, 0, 0, 45)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx - 0.5, cy + 1.5), r, r)
        
        # Draw fill
        painter.setBrush(QBrush(color))
        
        if active:
            # Active slot gets a nice distinct blue border
            painter.setPen(QPen(QColor("#5a94e2"), 2.5))
        else:
            # Inactive slot gets a thin light gray border
            painter.setPen(QPen(QColor("#cccccc"), 1.0))
            
        painter.drawEllipse(QPointF(cx, cy), r, r)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            scale = self.width() / 60.0
            
            # Sizes
            fg_r = (46.0 * scale) / 2.0
            bg_r = (30.0 * scale) / 2.0
            
            box_size = float(self.width())
            border = 2.0 * scale
            
            # Calculate positions
            if self.position_mode == "top-left":
                # Foreground (large) at bottom-left
                fg_cx = fg_r + border
                fg_cy = box_size - fg_r - border
                # Background (small) at top-right
                bg_cx = box_size - bg_r - border
                bg_cy = bg_r + border
            else:
                # Foreground (large) at top-left
                fg_cx = fg_r + border
                fg_cy = fg_r + border
                # Background (small) at bottom-right
                bg_cx = box_size - bg_r - border
                bg_cy = box_size - bg_r - border

            # Draw circles in correct z-order (active on top)
            if self.active_slot == "fg":
                self.draw_circle(painter, bg_cx, bg_cy, bg_r, self.bg_color, active=False)
                self.draw_circle(painter, fg_cx, fg_cy, fg_r, self.fg_color, active=True)
            else:
                self.draw_circle(painter, fg_cx, fg_cy, fg_r, self.fg_color, active=False)
                self.draw_circle(painter, bg_cx, bg_cy, bg_r, self.bg_color, active=True)
        except Exception as e:
            pass
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
            
        pos = event.position()
        px, py = pos.x(), pos.y()
        
        # Calculate dynamic positions
        scale = self.width() / 53.0
        fg_r = (40.0 * scale) / 2.0
        bg_r = (26.0 * scale) / 2.0
        box_size = float(self.width())
        border = 2.0 * scale
        
        if self.position_mode == "top-left":
            fg_cx = fg_r + border
            fg_cy = box_size - fg_r - border
            bg_cx = box_size - bg_r - border
            bg_cy = bg_r + border
        else:
            fg_cx = fg_r + border
            fg_cy = fg_r + border
            bg_cx = box_size - bg_r - border
            bg_cy = box_size - bg_r - border
            
        d_fg = (px - fg_cx)**2 + (py - fg_cy)**2
        d_bg = (px - bg_cx)**2 + (py - bg_cy)**2
        
        r2_fg = fg_r ** 2
        r2_bg = bg_r ** 2
        
        if self.active_slot == "fg":
            # FG (large) is on top
            if d_fg <= r2_fg:
                self.parent.select_fg_slot()
            elif d_bg <= r2_bg:
                self.parent.select_bg_slot()
        else:
            # BG (small) is on top
            if d_bg <= r2_bg:
                self.parent.select_bg_slot()
            elif d_fg <= r2_fg:
                self.parent.select_fg_slot()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if hasattr(self.parent, 'swap_colors'):
                self.parent.swap_colors()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load_hotkey_config()
        self.current_ui_scale = self.cfg.get("uiScale", 100)
        self.current_rgb = (180, 130, 30)
        self.active_slot = "fg"  # "fg" | "bg"
        
        # Dragging state (mouse click-through toggle override)
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        self.auto_hidden = False

        self.slider_row_layouts = []
        self.slider_labels = {}
        self.resizing = False
        self.resize_dir = None
        self.resize_start_pos = None
        self.resize_start_geometry = None
        
        # DPI-aware screen tracking to prevent size drift when dragging across monitors
        self._last_dpr = None       # Previous screen devicePixelRatio
        self._dpi_locked_size = None  # (w, h) logical size frozen during DPI transition

        # Fullscreen grayscale overlay — choose backend from config
        mode = self.cfg.get("grayscaleFilterMode", "oklch")
        backend = self.cfg.get("grayscaleFilterBackend", "overlay")
        if backend == "dwm":
            from core.dcomp_grayscale import DCompOverlayController
            self.grayscale_overlay = DCompOverlayController()
            if not self.grayscale_overlay.is_available:
                self.grayscale_overlay = GrayscaleOverlay(mode=mode)
        else:
            self.grayscale_overlay = GrayscaleOverlay(mode=mode)
        # Apply saved screen target (no-op for DWM backend)
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)

        self.init_ui()
        self.init_hotkeys()
        self.init_memory_sync()
        self.init_foreground_tracker()
        self.apply_theme()
        QApplication.instance().installEventFilter(self)

    def init_ui(self):
        # Frameless, transparent, stays on top, taskbar icon based on config
        self.update_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Load window dimensions, adjusting for DPI differences since last save
        win_cfg = config.load_window_config()
        scale = self.cfg.get("uiScale", 100) / 100.0
        saved_dpr = win_cfg.get("dpr", None)
        current_dpr = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if current_dpr < 0.1:
            current_dpr = 1.0
        
        w = win_cfg.get("width", int(320 * scale))
        h = win_cfg.get("height", int(710 * scale))
        
        # If saved on a different DPI screen, adjust to current screen's logical pixels
        if saved_dpr is not None and abs(current_dpr - saved_dpr) > 0.01:
            phys_w = w * saved_dpr
            phys_h = h * saved_dpr
            w = int(phys_w / current_dpr)
            h = int(phys_h / current_dpr)
        
        self.resize(w, h)
        if "x" in win_cfg and "y" in win_cfg:
            self.move(win_cfg["x"], win_cfg["y"])

        # Central Widget
        self.central = QWidget(self)
        self.central.setObjectName("CentralWidget")
        self.central.setMouseTracking(True)
        self.setCentralWidget(self.central)
        self.setMouseTracking(True)

        # Main Layout
        self.main_layout = QVBoxLayout(self.central)
        self.main_layout.setContentsMargins(8, 0, 8, 8)  # Thin frame border
        self.main_layout.setSpacing(0)

        # Title Bar
        self.title_bar = TitleBar(self)
        self.title_bar.btn_close.clicked.connect(self.close_application)
        self.title_bar.btn_settings.clicked.connect(self.toggle_settings_sidebar)
        self.main_layout.addWidget(self.title_bar)

        # Stacked pane for visualizers
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)

        # Pane 1: HSV Color Wheel
        self.pane_wheel = WheelPane()
        wheel_layout = QVBoxLayout(self.pane_wheel)
        wheel_layout.setContentsMargins(0, 0, 0, 0)
        self.color_wheel = ColorWheel()
        self.color_wheel.colorChanged.connect(self.on_wheel_color_changed)
        self.color_wheel.interactionFinished.connect(self.on_interaction_finished)
        wheel_layout.addWidget(self.color_wheel)
        
        # Floating mode buttons parented to their respective views
        self.btn_mode_wheel = QPushButton("☉", self.pane_wheel)
        self.btn_mode_wheel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_wheel.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_wheel.clicked.connect(self.toggle_picker_mode)
        self.pane_wheel.set_mode_button(self.btn_mode_wheel)
        
        self.stack.addWidget(self.pane_wheel)

        # Pane 2: LAB Space
        self.pane_lab = LabPane(self)
        lab_layout = QHBoxLayout(self.pane_lab)
        lab_layout.setContentsMargins(0, 0, 0, 0)
        lab_layout.setSpacing(6)
        
        self.lab_square = LabSquare()
        self.lab_square.colorChanged.connect(self.on_lab_square_color_changed)
        self.lab_square.interactionFinished.connect(self.on_interaction_finished)
        
        # Set initial visualizer mode from config
        viz_mode = self.cfg.get("visualizerMode", "lab")
        self.lab_square.set_render_mode(viz_mode)
        
        # Wrap vertical lightness slider in a column widget to support height adjustment and hiding
        self.lab_slider_column = QWidget()
        slider_col_layout = QVBoxLayout(self.lab_slider_column)
        slider_col_layout.setContentsMargins(0, 0, 0, 0)
        slider_col_layout.setSpacing(4)
        
        self.lab_slider = LabSlider()
        self.lab_slider.lightnessChanged.connect(self.lab_square.set_lightness)
        self.lab_slider.interactionFinished.connect(self.on_interaction_finished)
        slider_col_layout.addWidget(self.lab_slider)
        
        lab_layout.addWidget(self.lab_square, stretch=1)
        lab_layout.addWidget(self.lab_slider_column)
        
        # Floating mode button parented directly to self.pane_lab
        self.btn_mode_lab = QPushButton("△", self.pane_lab)
        self.btn_mode_lab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_lab.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_lab.clicked.connect(self.toggle_picker_mode)
        self.pane_lab.set_mode_button(self.btn_mode_lab)
        
        self.stack.addWidget(self.pane_lab)

        # Sliders Area
        self.sliders_container = QWidget()
        self.sliders_layout = QVBoxLayout(self.sliders_container)
        self.sliders_layout.setContentsMargins(10, 6, 10, 10)
        self.sliders_layout.setSpacing(8)
        self.main_layout.addWidget(self.sliders_container)

        self.setup_sliders()

        # Overlapping swatches box (Floating on MainWindow to avoid clipping)
        self.preview_box = ColorPreviewBox(self)
        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.preview_box.set_colors(QColor(*self.current_rgb), QColor(255, 255, 255))
        


        # Settings Sidebar (Floating on MainWindow to avoid z-order issues)
        self.settings_sidebar = SettingsSidebar(self)
        self.settings_sidebar.setVisible(False)
        self.settings_sidebar.settingChanged.connect(self.on_settings_saved)

        # Sync slider state
        self.update_ui_colors(self.current_rgb[0], self.current_rgb[1], self.current_rgb[2], source="init")
        
        # Set initial color wheel mode
        cfg_color_mode = self.cfg.get("colorWheelMode", "hsv")
        cfg_wheel_mode = self.cfg.get("wheelMode", "hsv-square")
        if cfg_color_mode == "hls":
            self.color_wheel.set_wheel_mode("hls-triangle")
        elif cfg_color_mode == "rgb":
            self.color_wheel.set_wheel_mode("rgb-slice")
        elif cfg_color_mode == "oklch":
            self.color_wheel.set_wheel_mode("oklch-slice")
        else:
            self.color_wheel.set_wheel_mode(cfg_wheel_mode)
        
        # Apply slider visibility and order on startup
        self.refresh_slider_visibility_and_order()
        self.update_mode_buttons_visibility()
        self.update_no_focus_policies()

    def setup_sliders(self):
        # Create standard RGB, HSV, HSL, LAB groups
        self.slider_widgets = {}
        self.slider_containers = {}
        same_space_base = self.cfg.get("sliderSameSpace", 6)
        
        # 1. RGB
        self.slider_containers["RGB"] = QWidget()
        rgb_lay = QVBoxLayout(self.slider_containers["RGB"])
        rgb_lay.setContentsMargins(0, 0, 0, 0)
        rgb_lay.setSpacing(same_space_base)
        self.create_group_sliders("RGB", ["R", "G", "B"], rgb_lay)
        self.sliders_layout.addWidget(self.slider_containers["RGB"])
        
        # 2. HSV
        self.slider_containers["HSV"] = QWidget()
        hsv_lay = QVBoxLayout(self.slider_containers["HSV"])
        hsv_lay.setContentsMargins(0, 0, 0, 0)
        hsv_lay.setSpacing(same_space_base)
        self.create_group_sliders("HSV", ["H_hsv", "S_hsv", "V_hsv"], hsv_lay)
        self.sliders_layout.addWidget(self.slider_containers["HSV"])
        
        # 3. HSL
        self.slider_containers["HSL"] = QWidget()
        hsl_lay = QVBoxLayout(self.slider_containers["HSL"])
        hsl_lay.setContentsMargins(0, 0, 0, 0)
        hsl_lay.setSpacing(same_space_base)
        self.create_group_sliders("HSL", ["H_hsl", "L_hsl", "S_hsl"], hsl_lay)
        self.sliders_layout.addWidget(self.slider_containers["HSL"])
        
        # 4. LAB
        self.slider_containers["LAB"] = QWidget()
        lab_lay = QVBoxLayout(self.slider_containers["LAB"])
        lab_lay.setContentsMargins(0, 0, 0, 0)
        lab_lay.setSpacing(same_space_base)
        self.create_group_sliders("LAB", ["L_lab", "a_lab", "b_lab"], lab_lay)
        self.sliders_layout.addWidget(self.slider_containers["LAB"])
        
        # 5. OKLab
        self.slider_containers["OKLab"] = QWidget()
        oklab_lay = QVBoxLayout(self.slider_containers["OKLab"])
        oklab_lay.setContentsMargins(0, 0, 0, 0)
        oklab_lay.setSpacing(same_space_base)
        self.create_group_sliders("OKLab", ["L_oklab", "a_oklab", "b_oklab"], oklab_lay)
        self.sliders_layout.addWidget(self.slider_containers["OKLab"])
        
        # 6. OKLCh
        self.slider_containers["OKLCh"] = QWidget()
        oklch_lay = QVBoxLayout(self.slider_containers["OKLCh"])
        oklch_lay.setContentsMargins(0, 0, 0, 0)
        oklch_lay.setSpacing(same_space_base)
        self.create_group_sliders("OKLCh", ["L_oklch", "C_oklch", "h_oklch"], oklch_lay)
        self.sliders_layout.addWidget(self.slider_containers["OKLCh"])

        # 7. Color History — shares the sliders_layout's order mechanism so it
        # can be reordered among the slider groups via the settings sidebar.
        self.slider_containers["History"] = QWidget()
        history_lay = QVBoxLayout(self.slider_containers["History"])
        history_lay.setContentsMargins(0, 0, 0, 0)
        history_lay.setSpacing(0)
        self.color_history = ColorHistoryWidget(self.slider_containers["History"])
        self.color_history.color_picked.connect(self.on_history_color_picked)
        # Initial grid geometry from config
        self.color_history.configure(
            self.cfg.get("historyColumns", 8),
            self.cfg.get("historyRows", 2),
            self.cfg.get("historySwatchSize", 18),
        )
        # Restore persisted colors (config stores a list of [r,g,b] triplets)
        persisted = self.cfg.get("historyColors", [])
        if persisted:
            from PyQt6.QtGui import QColor as _QColor
            initial_colors = []
            for entry in persisted:
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    initial_colors.append(_QColor(int(entry[0]), int(entry[1]), int(entry[2])))
            self.color_history.set_colors(initial_colors)
        history_lay.addWidget(self.color_history)
        self.sliders_layout.addWidget(self.slider_containers["History"])

    def create_group_sliders(self, group, channels, layout):
        for chan in channels:
            row = QHBoxLayout()
            row.setSpacing(8)
            self.slider_row_layouts.append(row)
            
            # Label
            label_text = chan.split("_")[0]
            label = QLabel(f"{label_text}:")
            label.setFixedWidth(16)
            label.setObjectName("ChannelLabel")
            self.slider_labels[chan] = label
            
            slider = GradientSlider(Qt.Orientation.Horizontal)
            if "H" in chan:
                slider.setRange(0, 360)
            elif chan in ("S_hsv", "V_hsv", "L_hsl", "S_hsl", "L_lab"):
                slider.setRange(0, 100)
            elif chan in ("a_lab", "b_lab"):
                slider.setRange(-128, 127)
            elif chan in ("a_oklab", "b_oklab"):
                slider.setRange(-40, 40)
            elif chan in ("L_oklab", "L_oklch"):
                slider.setRange(0, 100)
            elif chan == "C_oklch":
                slider.setRange(0, 100)
            elif chan == "h_oklch":
                slider.setRange(0, 360)
            else:
                slider.setRange(0, 255)
                
            val_label = QLabel("0")
            val_label.setFixedWidth(24)
            val_label.setObjectName("ValueLabel")
            val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            row.addWidget(label)
            row.addWidget(slider)
            row.addWidget(val_label)
            layout.addLayout(row)
            
            self.slider_widgets[chan] = (slider, val_label)
            
            # Connect signals
            slider.sliderReleased.connect(self.on_interaction_finished)
            if group == "RGB":
                slider.valueChanged.connect(self.on_rgb_slider_changed)
            elif group == "HSV":
                slider.valueChanged.connect(self.on_hsv_slider_changed)
            elif group == "HSL":
                slider.valueChanged.connect(self.on_hsl_slider_changed)
            elif group == "LAB":
                slider.valueChanged.connect(self.on_lab_slider_changed)
            elif group == "OKLab":
                slider.valueChanged.connect(self.on_oklab_slider_changed)
            elif group == "OKLCh":
                slider.valueChanged.connect(self.on_oklch_slider_changed)

    def select_fg_slot(self):
        if self.active_slot != "fg":
            self.active_slot = "fg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Load fg color into visualizers and sliders
            col = self.preview_box.fg_color
            self.update_ui_colors(col.red(), col.green(), col.blue(), source="slot_change")

    def select_bg_slot(self):
        if self.active_slot != "bg":
            self.active_slot = "bg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Load bg color into visualizers and sliders
            col = self.preview_box.bg_color
            self.update_ui_colors(col.red(), col.green(), col.blue(), source="slot_change")

    def swap_colors(self):
        # Swap foreground and background
        fg = self.preview_box.fg_color
        bg = self.preview_box.bg_color
        self.preview_box.set_colors(bg, fg)
        
        # Maintain active slot color
        active_color = bg if self.active_slot == "fg" else fg
        r, g, b = active_color.red(), active_color.green(), active_color.blue()
        self.update_ui_colors(r, g, b, source="swap")

    def on_history_color_picked(self, color):
        """User clicked a swatch in the history widget → load into active slot."""
        r, g, b = color.red(), color.green(), color.blue()
        self.update_ui_colors(r, g, b, source="history")
        if hasattr(self, "color_history"):
            self.color_history.mark_selected(color)

    def _record_color_history(self):
        """Persist the latest RGB into the history widget and into config.
        Called when an interaction finishes (slider/wheel/lab release)."""
        if not hasattr(self, "color_history"):
            return
        r, g, b = self.current_rgb
        updated = self.color_history.record(r, g, b)
        # Persist as plain [r,g,b] triplets (JSON-safe)
        self.cfg["historyColors"] = [[int(c.red()), int(c.green()), int(c.blue())]
                                     for c in updated]
        # Save quietly — keep this off the hot path
        from core import config as _config
        _config.save_hotkey_config(self.cfg)

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        if idx == 0:
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.show()
                self.btn_mode_wheel.raise_()
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.hide()
        else:
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.show()
                self.btn_mode_lab.raise_()
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.hide()

    def toggle_picker_mode(self):
        new_index = (self.stack.currentIndex() + 1) % 2
        self.stack.setCurrentIndex(new_index)
        self.update_mode_buttons_visibility()
        # Sync the newly-shown visualizer to the current color. While a pane is
        # hidden in the QStackedWidget, update_ui_colors skips syncing it, so a
        # freshly-revealed pane would otherwise show a stale cursor position.
        r, g, b = self.current_rgb
        if new_index == 1:  # LAB pane
            self._update_lab_avoid()
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(self.lab_square.L)
            self._update_lab_slider_gamut_range()
        else:  # Color wheel pane
            self.color_wheel.set_color(r, g, b, block_signals=True)
        self.update()

    def _update_lab_avoid(self):
        """Tell LabSquare how much of its top is covered by the floating
        preview box, so the ab plane renders below it instead of being hidden."""
        if not hasattr(self, 'lab_square') or not hasattr(self, 'preview_box'):
            return
        pb = self.preview_box
        ls = self.lab_square
        if pb.position_mode != "top-left" or not pb.isVisible():
            ls.avoid_top = 0
            return
        # Map the preview box corners into LabSquare's local coordinate system.
        # QStackedWidget keeps every page at the same geometry as the stack
        # content rect, so this is valid even while the LAB pane is hidden.
        try:
            top_left = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().topLeft()))
            bottom_right = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().bottomRight()))
        except Exception:
            ls.avoid_top = 0
            return
        # Only avoid if there is horizontal overlap with LabSquare.
        if bottom_right.x() <= 0 or top_left.x() >= ls.width():
            ls.avoid_top = 0
            return
        scale = self.cfg.get("uiScale", 100) / 100.0
        pad = int(4 * scale)
        ls.avoid_top = max(0, bottom_right.y() + pad)

    def on_wheel_color_changed(self, r, g, b):
        self.update_ui_colors(r, g, b, source="wheel")

    def on_lab_square_color_changed(self, r, g, b):
        self.update_ui_colors(r, g, b, source="lab")

    def on_rgb_slider_changed(self):
        r = self.slider_widgets["R"][0].value()
        g = self.slider_widgets["G"][0].value()
        b = self.slider_widgets["B"][0].value()
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
        self.update_ui_colors(r, g, b, source="sliders_rgb", hsv=(h_hsv, s_hsv, v_hsv))

    def on_hsv_slider_changed(self):
        h = self.slider_widgets["H_hsv"][0].value()
        s = self.slider_widgets["S_hsv"][0].value()
        v = self.slider_widgets["V_hsv"][0].value()
        r, g, b = hsv_to_rgb(h, s, v)
        self.update_ui_colors(r, g, b, source="sliders_hsv", hsv=(h, s, v))

    def on_hsl_slider_changed(self):
        h = self.slider_widgets["H_hsl"][0].value()
        l_val = self.slider_widgets["L_hsl"][0].value() / 100.0
        s_val = self.slider_widgets["S_hsl"][0].value() / 100.0
        r, g, b = colorsys.hls_to_rgb(h / 360.0, l_val, s_val)
        h_hsv, s_hsv, v_hsv = hls_to_hsv_floats(h, l_val, s_val)
        self.update_ui_colors(int(r * 255), int(g * 255), int(b * 255), source="sliders_hsl", hsv=(h_hsv, s_hsv, v_hsv))

    def on_lab_slider_changed(self):
        l_val = self.slider_widgets["L_lab"][0].value()
        a_val = self.slider_widgets["a_lab"][0].value()
        b_val = self.slider_widgets["b_lab"][0].value()
        r, g, b = lab_to_rgb(l_val, a_val, b_val)
        r_clamped = max(0.0, min(255.0, r))
        g_clamped = max(0.0, min(255.0, g))
        b_clamped = max(0.0, min(255.0, b))
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        # Preserve current hue when a,b drop to achromatic
        if s_hsv < 1.0 and hasattr(self, 'color_wheel'):
            h_hsv = self.color_wheel.h
        r_int = int(r_clamped)
        g_int = int(g_clamped)
        b_int = int(b_clamped)
        self.update_ui_colors(r_int, g_int, b_int, source="sliders_lab", hsv=(h_hsv, s_hsv, v_hsv))

    def on_oklab_slider_changed(self):
        l_val = self.slider_widgets["L_oklab"][0].value()
        a_val = self.slider_widgets["a_oklab"][0].value() / 100.0
        b_val = self.slider_widgets["b_oklab"][0].value() / 100.0
        r, g, b = oklab_to_rgb(l_val / 100.0, a_val, b_val)
        r_clamped = max(0.0, min(255.0, r))
        g_clamped = max(0.0, min(255.0, g))
        b_clamped = max(0.0, min(255.0, b))
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        # Preserve current hue when a,b drop to achromatic
        if s_hsv < 1.0 and hasattr(self, 'color_wheel'):
            h_hsv = self.color_wheel.h
        self.update_ui_colors(int(r_clamped), int(g_clamped), int(b_clamped),
                              source="sliders_oklab", hsv=(h_hsv, s_hsv, v_hsv))

    def on_oklch_slider_changed(self):
        if not hasattr(self, "_oklch_target_C") or not hasattr(self, "_oklch_target_h"):
            r, g, b = self.current_rgb
            _, C_okc, h_okc = rgb_to_oklch(r, g, b)
            self._oklch_target_C = C_okc
            self._oklch_target_h = h_okc

        sender = self.sender()
        l_val = self.slider_widgets["L_oklch"][0].value()
        c_raw = self.slider_widgets["C_oklch"][0].value()
        h_val = self.slider_widgets["h_oklch"][0].value()
        L = l_val / 100.0

        if sender == self.slider_widgets["C_oklch"][0]:
            max_c = self._find_oklch_max_chroma(L, h_val)
            self._oklch_target_C = (c_raw / 100.0) * max_c if max_c > 0.0 else 0.0
            source_str = "sliders_oklch_C"
        elif sender == self.slider_widgets["h_oklch"][0]:
            self._oklch_target_h = float(h_val)
            source_str = "sliders_oklch_h"
        else:
            source_str = "sliders_oklch_L"

        r, g, b = oklch_to_rgb(L, self._oklch_target_C, self._oklch_target_h)
        r_clamped = max(0.0, min(255.0, r))
        g_clamped = max(0.0, min(255.0, g))
        b_clamped = max(0.0, min(255.0, b))
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        # Preserve current hue when chroma drops to achromatic
        if s_hsv < 1.0 and hasattr(self, 'color_wheel'):
            h_hsv = self.color_wheel.h
        self.update_ui_colors(int(r_clamped), int(g_clamped), int(b_clamped),
                              source=source_str, hsv=(h_hsv, s_hsv, v_hsv))

    def _find_oklch_max_chroma(self, L, h):
        """Binary search for max OKLCh chroma at given L, h within sRGB gamut."""
        # At extreme L, gamut is a single point — return small epsilon to avoid /0
        if L < 0.002 or L > 0.998:
            return 0.001
        cache_key = (round(L, 3), round(h, 1))
        cached = getattr(self, '_oklch_max_c_cache', {})
        if cache_key in cached:
            return cached[cache_key]
        lo, hi = 0.0, 0.6
        for _ in range(16):
            mid = (lo + hi) / 2.0
            r, g, b = oklch_to_rgb(L, mid, h)
            if -0.5 <= r <= 255.5 and -0.5 <= g <= 255.5 and -0.5 <= b <= 255.5:
                lo = mid
            else:
                hi = mid
        # Guard against degenerate case (all chroma values valid at extreme L)
        if lo < 0.0001:
            lo = 0.001
        cached[cache_key] = lo
        if len(cached) > 500:
            cached.pop(next(iter(cached)))
        self._oklch_max_c_cache = cached
        return lo

    def on_interaction_finished(self):
        self.color_wheel.update()
        self.lab_square.update()
        # Defer pre-render to avoid blocking mouse release
        if not self.lab_square.isVisible():
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, self._prerender_lab)
        r, g, b = self.current_rgb
        # Record into history before pushing to drawing software so the
        # persisted state reflects *what the user just settled on*.
        self._record_color_history()
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            self.sync_thread.write_color(r, g, b)
            if self.cfg.get("autoFocusDrawingSoftware", False):
                self.focus_drawing_software()

    def focus_drawing_software(self):
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            pid = self.sync_thread.get_active_pid()
            if pid:
                bring_process_to_foreground(pid)

    def _prerender_lab(self):
        """Background pre-render of LAB visualizer."""
        if not self.lab_square.isVisible() and hasattr(self, 'stack'):
            self.lab_square.resize(self.stack.size())
            # Sync the visualizer's L/a/b to the current color before prerendering,
            # otherwise the low-res preview reflects the stale color from when the
            # pane was last visible (update_ui_colors skips syncing a hidden pane).
            r, g, b = self.current_rgb
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_square.prerender()

    def update_slider_gradients(self, r, g, b):
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
        h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        l_lab, a_lab, b_lab = rgb_to_lab(r, g, b)
        L_oklab, a_oklab, b_oklab = rgb_to_oklab(r, g, b)
        L_oklch, C_oklch, h_oklch = rgb_to_oklch(r, g, b)
        
        # 1) R Slider
        self.slider_widgets["R"][0].set_gradient([
            (0.0, QColor(0, g, b)),
            (1.0, QColor(255, g, b))
        ])
        
        # 2) G Slider
        self.slider_widgets["G"][0].set_gradient([
            (0.0, QColor(r, 0, b)),
            (1.0, QColor(r, 255, b))
        ])
        
        # 3) B Slider
        self.slider_widgets["B"][0].set_gradient([
            (0.0, QColor(r, g, 0)),
            (1.0, QColor(r, g, 255))
        ])
        
        # 4) H_hsv Slider
        hue_stops = [
            (0.0, QColor(255, 0, 0)),
            (0.17, QColor(255, 255, 0)),
            (0.33, QColor(0, 255, 0)),
            (0.5, QColor(0, 255, 255)),
            (0.67, QColor(0, 0, 255)),
            (0.83, QColor(255, 0, 255)),
            (1.0, QColor(255, 0, 0))
        ]
        self.slider_widgets["H_hsv"][0].set_gradient(hue_stops)
        
        # 5) S_hsv Slider
        r0, g0, b0 = hsv_to_rgb(h_hsv, 0.0, v_hsv)
        r1, g1, b1 = hsv_to_rgb(h_hsv, 100.0, v_hsv)
        self.slider_widgets["S_hsv"][0].set_gradient([
            (0.0, QColor(int(r0), int(g0), int(b0))),
            (1.0, QColor(int(r1), int(g1), int(b1)))
        ])
        
        # 6) V_hsv Slider
        rv0, gv0, bv0 = hsv_to_rgb(h_hsv, s_hsv, 0.0)
        rv1, gv1, bv1 = hsv_to_rgb(h_hsv, s_hsv, 100.0)
        self.slider_widgets["V_hsv"][0].set_gradient([
            (0.0, QColor(int(rv0), int(gv0), int(bv0))),
            (1.0, QColor(int(rv1), int(gv1), int(bv1)))
        ])
        
        # 7) H_hsl Slider
        self.slider_widgets["H_hsl"][0].set_gradient(hue_stops)
        
        # 8) L_hsl Slider
        rl0, gl0, bl0 = colorsys.hls_to_rgb(h_hsl, 0.0, s_hsl)
        rl05, gl05, bl05 = colorsys.hls_to_rgb(h_hsl, 0.5, s_hsl)
        rl1, gl1, bl1 = colorsys.hls_to_rgb(h_hsl, 1.0, s_hsl)
        self.slider_widgets["L_hsl"][0].set_gradient([
            (0.0, QColor(int(rl0 * 255), int(gl0 * 255), int(bl0 * 255))),
            (0.5, QColor(int(rl05 * 255), int(gl05 * 255), int(bl05 * 255))),
            (1.0, QColor(int(rl1 * 255), int(gl1 * 255), int(bl1 * 255)))
        ])
        
        # 9) S_hsl Slider
        rs0, gs0, bs0 = colorsys.hls_to_rgb(h_hsl, l_hsl, 0.0)
        rs1, gs1, bs1 = colorsys.hls_to_rgb(h_hsl, l_hsl, 1.0)
        self.slider_widgets["S_hsl"][0].set_gradient([
            (0.0, QColor(int(rs0 * 255), int(gs0 * 255), int(bs0 * 255))),
            (1.0, QColor(int(rs1 * 255), int(gs1 * 255), int(bs1 * 255)))
        ])
        
        # 10) L_lab Slider
        rlab0_r, rlab0_g, rlab0_b = lab_to_rgb(0, a_lab, b_lab)
        rlab1_r, rlab1_g, rlab1_b = lab_to_rgb(100, a_lab, b_lab)
        self.slider_widgets["L_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(rlab0_r))), max(0, min(255, int(rlab0_g))), max(0, min(255, int(rlab0_b))))),
            (1.0, QColor(max(0, min(255, int(rlab1_r))), max(0, min(255, int(rlab1_g))), max(0, min(255, int(rlab1_b)))))
        ])
        
        # 11) a_lab Slider
        alab0_r, alab0_g, alab0_b = lab_to_rgb(l_lab, -128, b_lab)
        alab1_r, alab1_g, alab1_b = lab_to_rgb(l_lab, 127, b_lab)
        self.slider_widgets["a_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(alab0_r))), max(0, min(255, int(alab0_g))), max(0, min(255, int(alab0_b))))),
            (1.0, QColor(max(0, min(255, int(alab1_r))), max(0, min(255, int(alab1_g))), max(0, min(255, int(alab1_b)))))
        ])
        
        # 12) b_lab Slider
        blab0_r, blab0_g, blab0_b = lab_to_rgb(l_lab, a_lab, -128)
        blab1_r, blab1_g, blab1_b = lab_to_rgb(l_lab, a_lab, 127)
        self.slider_widgets["b_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(blab0_r))), max(0, min(255, int(blab0_g))), max(0, min(255, int(blab0_b))))),
            (1.0, QColor(max(0, min(255, int(blab1_r))), max(0, min(255, int(blab1_g))), max(0, min(255, int(blab1_b)))))
        ])

        # 13) L_oklab Slider (L from 0 to 1 mapped to slider 0-100)
        if self.slider_containers.get("OKLab", QWidget()).isVisible():
            okl0_r, okl0_g, okl0_b = oklab_to_rgb(0.0, a_oklab, b_oklab)
            okl1_r, okl1_g, okl1_b = oklab_to_rgb(1.0, a_oklab, b_oklab)
            self.slider_widgets["L_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okl0_r))), int(max(0, min(255, okl0_g))), int(max(0, min(255, okl0_b))))),
                (1.0, QColor(int(max(0, min(255, okl1_r))), int(max(0, min(255, okl1_g))), int(max(0, min(255, okl1_b)))))
            ])
            
            # 14) a_oklab Slider (a from -0.4 to 0.4 mapped to slider -40..40)
            oka0_r, oka0_g, oka0_b = oklab_to_rgb(L_oklab, -0.4, b_oklab)
            oka1_r, oka1_g, oka1_b = oklab_to_rgb(L_oklab, 0.4, b_oklab)
            self.slider_widgets["a_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, oka0_r))), int(max(0, min(255, oka0_g))), int(max(0, min(255, oka0_b))))),
                (1.0, QColor(int(max(0, min(255, oka1_r))), int(max(0, min(255, oka1_g))), int(max(0, min(255, oka1_b)))))
            ])
            
            # 15) b_oklab Slider
            okb0_r, okb0_g, okb0_b = oklab_to_rgb(L_oklab, a_oklab, -0.4)
            okb1_r, okb1_g, okb1_b = oklab_to_rgb(L_oklab, a_oklab, 0.4)
            self.slider_widgets["b_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okb0_r))), int(max(0, min(255, okb0_g))), int(max(0, min(255, okb0_b))))),
                (1.0, QColor(int(max(0, min(255, okb1_r))), int(max(0, min(255, okb1_g))), int(max(0, min(255, okb1_b)))))
            ])
        
        if self.slider_containers.get("OKLCh", QWidget()).isVisible():
            if not hasattr(self, "_oklch_target_C") or not hasattr(self, "_oklch_target_h"):
                self._oklch_target_C = C_oklch
                self._oklch_target_h = h_oklch
            
            # 16) L_oklch Slider (L from 0 to 1 mapped to slider 0-100)
            okcl0_r, okcl0_g, okcl0_b = oklch_to_rgb(0.0, self._oklch_target_C, self._oklch_target_h)
            okcl1_r, okcl1_g, okcl1_b = oklch_to_rgb(1.0, self._oklch_target_C, self._oklch_target_h)
            self.slider_widgets["L_oklch"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okcl0_r))), int(max(0, min(255, okcl0_g))), int(max(0, min(255, okcl0_b))))),
                (1.0, QColor(int(max(0, min(255, okcl1_r))), int(max(0, min(255, okcl1_g))), int(max(0, min(255, okcl1_b)))))
            ])
            
            # 17) C_oklch Slider (adaptive max chroma)
            max_c = self._find_oklch_max_chroma(L_oklch, self._oklch_target_h)
            okcc0_r, okcc0_g, okcc0_b = oklch_to_rgb(L_oklch, 0.0, self._oklch_target_h)
            okcc1_r, okcc1_g, okcc1_b = oklch_to_rgb(L_oklch, max_c, self._oklch_target_h)
            self.slider_widgets["C_oklch"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okcc0_r))), int(max(0, min(255, okcc0_g))), int(max(0, min(255, okcc0_b))))),
                (1.0, QColor(int(max(0, min(255, okcc1_r))), int(max(0, min(255, okcc1_g))), int(max(0, min(255, okcc1_b)))))
            ])
            
            # 18) h_oklch Slider (hue 0-360)
            okch_stops = []
            for i in range(7):
                hue = i * 60
                r_h, g_h, b_h = oklch_to_rgb(L_oklch, self._oklch_target_C, hue)
                okch_stops.append((i / 6.0, QColor(int(max(0, min(255, r_h))), int(max(0, min(255, g_h))), int(max(0, min(255, b_h))))))
            self.slider_widgets["h_oklch"][0].set_gradient(okch_stops)

    # ── L-gamut range helpers for out-of-gamut slider marking ──

    def _compute_lab_L_gamut_range(self):
        """Return (min_L, max_L) for L_lab slider given current a_lab, b_lab."""
        if "a_lab" not in self.slider_widgets or "b_lab" not in self.slider_widgets:
            return 0, 100
        a_val = self.slider_widgets["a_lab"][0].value()
        b_val = self.slider_widgets["b_lab"][0].value()

        def in_gamut(L):
            r, g, bv = lab_to_rgb(L, a_val, b_val)
            return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

        if not in_gamut(50.0):
            return 0, 100
        # Find L_min
        if in_gamut(0.0):
            min_L = 0.0
        else:
            lo, hi = 0.0, 50.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    hi = mid
                else:
                    lo = mid
            min_L = hi
        # Find L_max
        if in_gamut(100.0):
            max_L = 100.0
        else:
            lo, hi = 50.0, 100.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    lo = mid
                else:
                    hi = mid
            max_L = lo
        return int(round(min_L)), int(round(max_L))

    def _compute_oklab_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklab slider given current a_oklab, b_oklab."""
        if "a_oklab" not in self.slider_widgets or "b_oklab" not in self.slider_widgets:
            return 0, 100
        a_val = self.slider_widgets["a_oklab"][0].value() / 100.0
        b_val = self.slider_widgets["b_oklab"][0].value() / 100.0

        def in_gamut(L):
            r, g, bv = oklab_to_rgb(L / 100.0, a_val, b_val)
            return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

        if not in_gamut(50.0):
            return 0, 100
        if in_gamut(0.0):
            min_L = 0.0
        else:
            lo, hi = 0.0, 50.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    hi = mid
                else:
                    lo = mid
            min_L = hi
        if in_gamut(100.0):
            max_L = 100.0
        else:
            lo, hi = 50.0, 100.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    lo = mid
                else:
                    hi = mid
            max_L = lo
        return int(round(min_L)), int(round(max_L))

    def _compute_oklch_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklch slider given current C, h."""
        if "C_oklch" not in self.slider_widgets or "h_oklch" not in self.slider_widgets:
            return 0, 100
        
        if not hasattr(self, "_oklch_target_C") or not hasattr(self, "_oklch_target_h"):
            r, g, b = self.current_rgb
            _, C_okc, h_okc = rgb_to_oklch(r, g, b)
            self._oklch_target_C = C_okc
            self._oklch_target_h = h_okc

        c_abs = self._oklch_target_C
        h_val = self._oklch_target_h

        if c_abs < 0.001:
            return 0, 100

        def in_gamut(L):
            r, g, bv = oklch_to_rgb(L / 100.0, c_abs, h_val)
            return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

        if not in_gamut(50.0):
            return 0, 100
        if in_gamut(0.0):
            min_L = 0.0
        else:
            lo, hi = 0.0, 50.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    hi = mid
                else:
                    lo = mid
            min_L = hi
        if in_gamut(100.0):
            max_L = 100.0
        else:
            lo, hi = 50.0, 100.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    lo = mid
                else:
                    hi = mid
            max_L = lo
        return int(round(min_L)), int(round(max_L))

    def _update_lab_slider_gamut_range(self):
        """Update the vertical LabSlider's out-of-gamut L range
        based on the current LabSquare (a, b) and render mode."""
        if not hasattr(self, 'lab_square') or not hasattr(self, 'lab_slider'):
            return
        a_val = self.lab_square.a
        b_val = self.lab_square.b
        mode = self.lab_square.render_mode

        def in_gamut(L):
            if mode == "oklab":
                r, g, bv = oklab_to_rgb(L / 100.0, a_val, b_val)
            else:
                r, g, bv = lab_to_rgb(L, a_val, b_val)
            return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

        if not in_gamut(50.0):
            min_L, max_L = 0.0, 100.0
        else:
            if in_gamut(0.0):
                min_L = 0.0
            else:
                lo, hi = 0.0, 50.0
                for _ in range(24):
                    mid = (lo + hi) * 0.5
                    if in_gamut(mid):
                        hi = mid
                    else:
                        lo = mid
                min_L = hi
            if in_gamut(100.0):
                max_L = 100.0
            else:
                lo, hi = 50.0, 100.0
                for _ in range(24):
                    mid = (lo + hi) * 0.5
                    if in_gamut(mid):
                        lo = mid
                    else:
                        hi = mid
                max_L = lo
        self.lab_slider.set_in_gamut_range(min_L, max_L)

    def _update_all_L_gamut_ranges(self):
        """Update out-of-gamut visual marking on all L sliders."""
        if not hasattr(self, 'slider_widgets'):
            return
        # L_lab
        if "L_lab" in self.slider_widgets:
            mn, mx = self._compute_lab_L_gamut_range()
            self.slider_widgets["L_lab"][0].set_in_gamut_range(mn, mx)
        # L_oklab
        if "L_oklab" in self.slider_widgets:
            mn, mx = self._compute_oklab_L_gamut_range()
            self.slider_widgets["L_oklab"][0].set_in_gamut_range(mn, mx)
        # L_oklch
        if "L_oklch" in self.slider_widgets:
            mn, mx = self._compute_oklch_L_gamut_range()
            self.slider_widgets["L_oklch"][0].set_in_gamut_range(mn, mx)
        # Vertical LabSlider
        self._update_lab_slider_gamut_range()

    def update_ui_colors(self, r, g, b, source="", hsv=None):
        self.current_rgb = (r, g, b)
        color = QColor(r, g, b)

        # 1) Sync swatches based on active slot
        if self.active_slot == "fg":
            self.preview_box.fg_color = color
        else:
            self.preview_box.bg_color = color
        self.preview_box.update_slot_borders(self.active_slot)

        # 2) Sync Color Wheel (Only if visible or during init)
        if source == "init" or (source != "wheel" and self.color_wheel.isVisible()):
            if hsv is not None:
                self.color_wheel.set_hsv(hsv[0], hsv[1], hsv[2])
            else:
                self.color_wheel.set_color(r, g, b, block_signals=True)

        # 3) Sync LAB Square / Slider (Only if visible or during init)
        if source == "init" or (source != "lab" and self.lab_square.isVisible()):
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(self.lab_square.L)

        # 4) Sync Sliders
        # Block signals for all sliders during sync
        all_chans = ["R", "G", "B", "H_hsv", "S_hsv", "V_hsv", "H_hsl", "L_hsl", "S_hsl", "L_lab", "a_lab", "b_lab", "L_oklab", "a_oklab", "b_oklab", "L_oklch", "C_oklch", "h_oklch"]
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(True)
            
        # RGB Values
        if source != "sliders_rgb":
            self.slider_widgets["R"][0].setValue(r)
            self.slider_widgets["G"][0].setValue(g)
            self.slider_widgets["B"][0].setValue(b)
        
        # HSV Values
        if source != "sliders_hsv":
            if source == "wheel":
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
            elif hsv is not None:
                h_hsv, s_hsv, v_hsv = hsv
            else:
                h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
            self.slider_widgets["H_hsv"][0].setValue(round(h_hsv))
            self.slider_widgets["S_hsv"][0].setValue(round(s_hsv))
            self.slider_widgets["V_hsv"][0].setValue(round(v_hsv))
        
        # HSL Values
        if source != "sliders_hsl":
            if source == "wheel":
                h_hsl, l_hsl, s_hsl = hsv_to_hls_floats(self.color_wheel.h, self.color_wheel.s, self.color_wheel.v)
                self.slider_widgets["H_hsl"][0].setValue(round(h_hsl * 360.0))
            else:
                h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
                h_deg = hsv[0] if hsv is not None else h_hsl * 360.0  # Reuse handler's locked hue
                self.slider_widgets["H_hsl"][0].setValue(round(h_deg))
            self.slider_widgets["L_hsl"][0].setValue(round(l_hsl * 100.0))
            self.slider_widgets["S_hsl"][0].setValue(round(s_hsl * 100.0))
        
        # LAB Values
        if source != "sliders_lab":
            if source == "wheel":
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
                r_f, g_f, b_f = colorsys.hsv_to_rgb(h_hsv / 360.0, s_hsv / 100.0, v_hsv / 100.0)
                l_lab, a_lab, b_lab = rgb_to_lab(r_f * 255.0, g_f * 255.0, b_f * 255.0)
            else:
                l_lab, a_lab, b_lab = rgb_to_lab(r, g, b)
            self.slider_widgets["L_lab"][0].setValue(round(l_lab))
            self.slider_widgets["a_lab"][0].setValue(round(a_lab))
            self.slider_widgets["b_lab"][0].setValue(round(b_lab))
        
        # OKLab Values
        if source != "sliders_oklab":
            L_ok, a_ok, b_ok = rgb_to_oklab(r, g, b)
            self.slider_widgets["L_oklab"][0].setValue(round(L_ok * 100))
            self.slider_widgets["a_oklab"][0].setValue(round(a_ok * 100))
            self.slider_widgets["b_oklab"][0].setValue(round(b_ok * 100))
        
        # OKLCh Values
        if source not in ("sliders_oklch_L", "sliders_oklch_C", "sliders_oklch_h"):
            L_okc, C_okc, h_okc = rgb_to_oklch(r, g, b)
            self._oklch_target_C = C_okc
            self._oklch_target_h = h_okc
            self.slider_widgets["L_oklch"][0].setValue(round(L_okc * 100))
            self.slider_widgets["h_oklch"][0].setValue(round(h_okc))
            if self.slider_containers.get("OKLCh", QWidget()).isVisible():
                max_c = self._find_oklch_max_chroma(L_okc, h_okc)
                self.slider_widgets["C_oklch"][0].setValue(round(C_okc / max_c * 100) if max_c > 0.001 else 0)
        else:
            L_okc, C_okc, h_okc = rgb_to_oklch(r, g, b)
            if source != "sliders_oklch_L":
                self.slider_widgets["L_oklch"][0].setValue(round(L_okc * 100))
            if source != "sliders_oklch_h":
                self.slider_widgets["h_oklch"][0].setValue(round(h_okc))
            if source != "sliders_oklch_C":
                if self.slider_containers.get("OKLCh", QWidget()).isVisible():
                    max_c = self._find_oklch_max_chroma(L_okc, h_okc)
                    self.slider_widgets["C_oklch"][0].setValue(round(C_okc / max_c * 100) if max_c > 0.001 else 0)
        
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(False)

        # Update labels and gradient stylesheets
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][1].setText(str(self.slider_widgets[chan][0].value()))
            
        self.update_slider_gradients(r, g, b)

        # Update out-of-gamut visual marking on L sliders
        self._update_all_L_gamut_ranges()

        # 5) Push to drawing software
        if source != "sync" and hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            is_dragging = False
            if source.startswith("sliders_"):
                for chan, (slider, _) in self.slider_widgets.items():
                    if slider.isSliderDown():
                        is_dragging = True
                        break
            if not is_dragging:
                self.sync_thread.write_color(r, g, b)

    def resizeEvent(self, event):
        """Handle resize, preventing DPI-induced size drift when dragged between monitors.

        When a frameless window is dragged between screens with different DPI scaling,
        Qt may fire resize events as it recalculates device-independent pixels. Without
        intervention, the title-bar height change in apply_theme() + layout recalculation
        creates a feedback loop that causes progressive size drift with each cross-screen drag.
        """
        current_screen = self.screen()
        if current_screen is not None:
            current_dpr = current_screen.devicePixelRatio()
        else:
            current_dpr = 1.0
        
        # Detect DPI change (screen switch with different scaling)
        dpi_changed = (self._last_dpr is not None and 
                       current_dpr is not None and 
                       abs(current_dpr - self._last_dpr) > 0.01)
        
        if dpi_changed and self._dpi_locked_size is None:
            # First resize event after DPI change: lock the intended logical size.
            # We use oldSize (the size BEFORE Qt's DPI adjustment) to compute the
            # correct logical size for the new DPR.
            old_size = event.oldSize()
            if old_size.isValid() and old_size.width() > 100 and old_size.height() > 100:
                old_dpr = self._last_dpr
                new_dpr = current_dpr
                # Preserve physical pixel dimensions: convert old logical → physical → new logical
                phys_w = old_size.width() * old_dpr
                phys_h = old_size.height() * old_dpr
                target_w = max(200, min(1200, int(phys_w / new_dpr)))
                target_h = max(300, min(1600, int(phys_h / new_dpr)))
                
                new_size = event.size()
                if abs(target_w - new_size.width()) > 3 or abs(target_h - new_size.height()) > 3:
                    # Qt adjusted the size; override to maintain physical consistency
                    self._dpi_locked_size = (target_w, target_h)
                    self.resize(target_w, target_h)
                    self._last_dpr = current_dpr
                    return  # self.resize() will fire another resizeEvent
        
        # Clear DPI lock after the stabilizing resize
        if self._dpi_locked_size is not None:
            locked_w, locked_h = self._dpi_locked_size
            new_size = event.size()
            if abs(locked_w - new_size.width()) <= 3 and abs(locked_h - new_size.height()) <= 3:
                self._dpi_locked_size = None
        
        self._last_dpr = current_dpr
        
        super().resizeEvent(event)
        self.update_geometries()

    def moveEvent(self, event):
        """Block window movement when lockWindowPosition is enabled."""
        if self.cfg.get("lockWindowPosition", False):
            event.ignore()
            return
        super().moveEvent(event)

    def update_geometries(self):
        # Dimensions
        w = self.width()
        h = self.height()
        dynamic_scale = self.cfg.get("uiScale", 100) / 100.0
        
        # Apply scaling and updates
        self.apply_theme(scale=dynamic_scale, is_resize_event=True)
        
        title_h = self.title_bar.height()
        sliders_h = self.sliders_container.sizeHint().height()
        
        # Calculate visualizer wheel size solely based on width, leaving a small margin
        spacing = int(4 * dynamic_scale)
        pane_h = h - 4 - title_h - sliders_h - 2 * spacing
        wheel_size = w - int(16 * dynamic_scale)
        
        # Dynamic preview box scaling and placement
        self.preview_box.resize_and_position(wheel_size, title_h, h, sliders_h, self.active_slot)
        self.preview_box.raise_()

        # Keep LabSquare's top-avoidance in sync with the preview box position
        # so the ab plane never renders behind the floating preview box.
        self._update_lab_avoid()
        
        # If settings sidebar is open, ensure it remains on top!
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar.raise_()
        
        # The floating mode-switcher buttons are owned by their panes and position
# themselves from the pane's own resizeEvent — so they always sit at the
# bottom-right of whichever pane geometry the layout actually gave them.
        # We only push the DPI-scaled metrics down; the panes do the rest.
        btn_size = int(28 * dynamic_scale)
        btn_margin = int(6 * dynamic_scale)
        if hasattr(self, 'pane_wheel'):
            self.pane_wheel.set_mode_button_metrics(btn_size, btn_margin)
        if hasattr(self, 'pane_lab'):
            self.pane_lab.set_mode_button_metrics(btn_size, btn_margin)
        
        # Position settings sidebar
        if hasattr(self, 'settings_sidebar'):
            self.settings_sidebar.setGeometry(2, title_h, int(w * 0.75), h - title_h - 2)
            self.settings_sidebar.raise_()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.cfg.get("lockWindowSize", False):
                event.accept()
                return
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            new_w = int(self.width() * factor)
            new_h = int(self.height() * factor)
            new_w = max(180, min(1200, new_w))
            new_h = max(240, min(1600, new_h))
            self.resize(new_w, new_h)
            event.accept()
        else:
            super().wheelEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        try:
            import win32api
            import win32con
            is_down = win32api.GetKeyState(win32con.VK_LBUTTON) < 0
        except Exception:
            is_down = True
            
        if not is_down:
            is_slider_down = False
            if hasattr(self, 'slider_widgets'):
                for chan, (slider, _) in self.slider_widgets.items():
                    if slider.isSliderDown():
                        slider.setDown(False)
                        is_slider_down = True
            
            wheel_dragging = hasattr(self, 'color_wheel') and self.color_wheel.dragging
            lab_dragging = hasattr(self, 'lab_square') and self.lab_square.dragging
            
            if is_slider_down or wheel_dragging or lab_dragging:
                if wheel_dragging:
                    try:
                        self.color_wheel.mouseReleaseEvent(None)
                    except Exception:
                        pass
                if lab_dragging:
                    try:
                        self.lab_square.mouseReleaseEvent(None)
                    except Exception:
                        pass
                self.on_interaction_finished()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.cfg.get("lockWindowSize", False):
            pos = event.position()
            direction = self.get_resize_direction(pos)
            if direction:
                self.resizing = True
                self.resize_dir = direction
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geometry = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.cfg.get("lockWindowSize", False):
            pos = event.position()
            if getattr(self, "resizing", False):
                delta = event.globalPosition().toPoint() - self.resize_start_pos
                geom = self.resize_start_geometry
                new_geom = QRect(geom)
                
                min_w = 200
                min_h = 300
                
                if "right" in self.resize_dir:
                    new_w = max(min_w, geom.width() + delta.x())
                    new_geom.setWidth(new_w)
                elif "left" in self.resize_dir:
                    new_w = max(min_w, geom.width() - delta.x())
                    new_geom.setLeft(geom.right() - new_w)
                    
                if "bottom" in self.resize_dir:
                    new_h = max(min_h, geom.height() + delta.y())
                    new_geom.setHeight(new_h)
                
                self.setGeometry(new_geom)
                event.accept()
                return
            else:
                direction = self.get_resize_direction(pos)
                target = Qt.CursorShape.ArrowCursor
                if direction == "left" or direction == "right":
                    target = Qt.CursorShape.SizeHorCursor
                elif direction == "bottom":
                    target = Qt.CursorShape.SizeVerCursor
                elif direction == "bottom-left":
                    target = Qt.CursorShape.SizeBDiagCursor
                elif direction == "bottom-right":
                    target = Qt.CursorShape.SizeFDiagCursor
                
                if self.cursor().shape() != target:
                    if target == Qt.CursorShape.ArrowCursor:
                        self.unsetCursor()
                    else:
                        self.setCursor(target)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_resizing = self.resizing
        self.resizing = False
        self.resize_dir = None
        self.unsetCursor()
        # Only save window geometry on actual manual resize, not on every mouse-up
        # (prevents saving DPI-corrupted sizes from cross-screen drags)
        if was_resizing:
            cfg = config.load_window_config()
            cfg["width"] = self.width()
            cfg["height"] = self.height()
            config.save_window_config(cfg)
        
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        try:
            # Intercept MouseMove events globally for this window's child widgets
            # to ensure the cursor correctly resets when leaving the 8px border zone
            if event.type() == QEvent.Type.MouseMove and isinstance(watched, QWidget) and self.window() == watched.window():
                if not getattr(self, "resizing", False) and not self.cfg.get("lockWindowSize", False):
                    pos_in_main = self.mapFromGlobal(QCursor.pos())
                    direction = self.get_resize_direction(pos_in_main)
                    
                    target = Qt.CursorShape.ArrowCursor
                    if direction == "left" or direction == "right":
                        target = Qt.CursorShape.SizeHorCursor
                    elif direction == "bottom":
                        target = Qt.CursorShape.SizeVerCursor
                    elif direction == "bottom-left":
                        target = Qt.CursorShape.SizeBDiagCursor
                    elif direction == "bottom-right":
                        target = Qt.CursorShape.SizeFDiagCursor
                        
                    if self.cursor().shape() != target:
                        if target == Qt.CursorShape.ArrowCursor:
                            self.unsetCursor()
                        else:
                            self.setCursor(target)
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def get_resize_direction(self, pos):
        w = self.width()
        h = self.height()
        border = 8
        
        x = pos.x()
        y = pos.y()
        
        is_left = x <= border
        is_right = x >= w - border
        is_bottom = y >= h - border
        
        if is_left and is_bottom:
            return "bottom-left"
        elif is_right and is_bottom:
            return "bottom-right"
        elif is_left:
            return "left"
        elif is_right:
            return "right"
        elif is_bottom:
            return "bottom"
        return None

    def apply_theme(self, scale=None, is_resize_event=False):
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0

        # Resolve slider theme (visual preset for slider track/handle/labels).
        # Falls back to "default" if the key is missing or unknown.
        slider_theme = get_slider_theme(self.cfg.get("sliderStyle", "default"))

        # Dynamically toggle vertical lightness slider visibility based on configuration
        show_lab_slider = self.cfg.get("showLabLightnessSlider", True)
        if hasattr(self, 'lab_slider_column'):
            self.lab_slider_column.setVisible(show_lab_slider)
            # Adjust margins to align with switcher button and prevent overlap
            layout = self.lab_slider_column.layout()
            if layout is not None:
                layout.setContentsMargins(int(9 * scale), int(8 * scale), int(9 * scale), int(34 * scale))

        self.update_mode_buttons_visibility()

        # Update layouts margins & spacing
        # Get screen device pixel ratio to keep the physical size exactly 28px on High-DPI screens.
        # Only adjust title bar height on non-resize-event calls (init / settings change)
        # to avoid DPI-triggered layout cascades when dragging between monitors.
        ratio = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if ratio < 0.1:
            ratio = 1.0
            
        tb_height = max(12, int(28 / ratio))
        title_btn_size = max(8, int(18 / ratio))
        tb_margin = max(2, int(6 / ratio))
        tb_spacing = max(2, int(6 / ratio))
        
        self.title_bar.setFixedHeight(tb_height)
        tb_layout = self.title_bar.layout()
        if tb_layout is not None:
            tb_layout.setContentsMargins(tb_margin, 0, tb_margin, 0)
            tb_layout.setSpacing(tb_spacing)
            
        self.title_bar.btn_settings.setFixedSize(title_btn_size, title_btn_size)
        self.title_bar.btn_min.setFixedSize(title_btn_size, title_btn_size)
        self.title_bar.btn_close.setFixedSize(title_btn_size, title_btn_size)
        
        
        self.main_layout.setContentsMargins(4, 0, 4, 4)  # Fixed 4px margins
        spacing = int(4 * scale)
        self.main_layout.setSpacing(spacing)
        
        # Get Same-space and Diff-space spacing values from configuration
        same_space = self.cfg.get("sliderSameSpace", 6)
        diff_space = self.cfg.get("sliderDiffSpace", 8)
        
        self.sliders_layout.setSpacing(int(diff_space * scale))
        self.sliders_layout.setContentsMargins(
            int(4 * scale), # closer to edge
            int(6 * scale),
            int(4 * scale), # closer to edge
            int(10 * scale)
        )
        
        # Update spacing within each color space block
        for group in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            if hasattr(self, "slider_containers") and group in self.slider_containers:
                container = self.slider_containers[group]
                lay = container.layout()
                if lay is not None:
                    lay.setSpacing(int(same_space * scale))
        
        # Adjust row spacings closer to text
        for row in getattr(self, "slider_row_layouts", []):
            row.setSpacing(int(3 * scale)) # 3px at 1.0 scale
            
        # Adjust label fixed widths (theme-aware)
        ch_w_factor = float(slider_theme["channel_label_width_factor"])
        for chan, label in getattr(self, "slider_labels", {}).items():
            label.setFixedWidth(max(8, int(16 * scale * ch_w_factor)))

        theme_name = self.cfg.get("ui-theme", "auto")
        if theme_name == "auto":
            try:
                from core.csp_brush_link import get_csp_theme
                t = get_csp_theme()
                bg = t["bg"]
                text = t["text"]
                border_color = t["border"].split(" ")[-1] if "solid" in t["border"] else t["border"]
                barBg = border_color
            except Exception:
                bg, text, border_color = "#b2b2b2", "#222222", "#787878"
                barBg = border_color
        else:
            themes = {
                "black": {"bg": "#1e1e1e", "text": "#ffffff", "border": "#2d2d2d"},
                "white": {"bg": "#ffffff", "text": "#222222", "border": "#b2b2b2"},
                "gray": {"bg": "#b2b2b2", "text": "#222222", "border": "#787878"}
            }
            t = themes.get(theme_name, themes["gray"])
            bg = t["bg"]
            text = t["text"]
            border_color = t["border"]
            barBg = border_color
            
        # Determine label text color based on background/text lightness to avoid low contrast
        is_dark_text = QColor(text).lightness() < 128
        channel_text_color = "#666666" if is_dark_text else "#e9e9e9"
        inputBg = "#eaeaea" if is_dark_text else "#2e2e2e"
        borderColor = "#d0d0d0" if is_dark_text else "#555555"
        handle_border_global = "#999999" if is_dark_text else "#b0b0b0"
        
        # Determine title bar text color and button hover backgrounds
        title_text_color = "#666666" if is_dark_text else "#a0a0a0"
        hover_bg = "rgba(0,0,0,0.08)" if is_dark_text else "rgba(255,255,255,0.12)"

        font_factor = (self.cfg.get("fontSize", 100) / 100.0) * scale
        lbl_font_size = int(11 * font_factor)
        val_font_size = int(10 * font_factor)
        title_font_size = int(8 * font_factor)
        
        # Calculate scaled font sizes using device pixel ratio
        fs_settings = max(6, int(14 * font_factor / ratio))
        fs_title = max(6, int(11 * font_factor / ratio))
        fs_min = max(5, int(10 * font_factor / ratio))
        fs_close = max(6, int(14 * font_factor / ratio))

        self.title_bar.btn_settings.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_settings}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        self.title_bar.title_label.setStyleSheet(f"font-weight: bold; color: {title_text_color}; font-size: {fs_title}px;")
        self.title_bar.btn_min.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_min}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        self.title_bar.btn_close.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_close}px; }} QPushButton:hover {{ background-color: #ff5050; color: white; border-radius: 2px; }}")

        self.setStyleSheet(f"""
            QWidget#CentralWidget {{
                background-color: {bg};
                border-left: 4px solid {border_color};
                border-right: 4px solid {border_color};
                border-bottom: 4px solid {border_color};
                border-top: none;
                border-radius: 0px;
            }}
            TitleBar {{
                background-color: {barBg};
                color: {title_text_color};
                border-bottom: none;
            }}
            TitleBar QLabel {{
                color: {title_text_color};
                font-size: {fs_title}px;
                font-weight: bold;
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            }}
            TitleBar QPushButton {{
                color: {title_text_color};
                font-size: {fs_settings}px;
            }}
            TitleBar QPushButton:hover {{
                background-color: {hover_bg};
                border-radius: 2px;
            }}
            QLabel {{
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {lbl_font_size}px;
            }}
            QLabel#ChannelLabel {{
                color: {channel_text_color};
                font-weight: {slider_theme["channel_label_weight"]};
                font-size: {lbl_font_size}px;
            }}
            QLabel#ValueLabel {{
                background-color: {inputBg};
                border: 1px solid {borderColor};
                border-radius: {int(2 * scale)}px;
                padding: 1px 3px;
                color: {text};
                font-size: {val_font_size}px;
            }}
            QSlider::groove:horizontal {{
                height: {int(6 * scale)}px;
                background: transparent;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                border: 1px solid {handle_border_global};
                width: {int(6 * scale)}px;
                height: {int(14 * scale)}px;
                margin-top: {-int(4 * scale)}px;
                margin-bottom: {-int(4 * scale)}px;
                border-radius: {int(3 * scale)}px;
            }}
            QSlider::handle:horizontal:hover {{
                background: #eaeaea;
                border-color: #5a94e2;
            }}
        """)
        
        # Propagate custom CSS variables to the settings sidebar if present
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar is not None:
            sb_font_size = int(10 * font_factor)
            sb_header_font_size = int(11 * font_factor)
            self.settings_sidebar.setStyleSheet(f"""
                QScrollArea {{
                    background-color: {barBg};
                    border: none;
                }}
                QWidget {{
                    background-color: {barBg};
                    color: {text};
                    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                    font-size: {sb_font_size}px;
                }}
                QLabel {{
                    color: {text};
                }}
                QLabel#SectionHeader {{
                    font-weight: bold;
                    font-size: {sb_header_font_size}px;
                    margin-top: 5px;
                    color: {text};
                    border-bottom: 1px solid rgba(0,0,0,0.15);
                    padding-bottom: 1px;
                }}
                QCheckBox {{
                    color: {text};
                }}
                QComboBox {{
                    background-color: {bg};
                    border: 1px solid {borderColor};
                    color: {text};
                    border-radius: 2px;
                    padding: 2px 4px;
                }}
                QPushButton {{
                    background-color: {bg};
                    border: 1px solid {borderColor};
                    color: {text};
                    border-radius: 2px;
                    padding: 2px 6px;
                }}
                QSlider#ScaleSlider::groove:horizontal {{
                    height: 4px;
                    background: {bg};
                    border: 1px solid {borderColor};
                    border-radius: 2px;
                }}
                QSlider#ScaleSlider::handle:horizontal {{
                    background: {text};
                    width: 10px;
                    height: 10px;
                    margin-top: -3px;
                    margin-bottom: -3px;
                    border-radius: 5px;
                }}
            """)
            
        # Style value labels directly for robust rendering (theme-aware)
        val_w_factor = float(slider_theme["value_label_width_factor"])
        val_radius = max(0, int(3 * scale * float(slider_theme["value_label_radius_factor"])))
        val_padding = slider_theme["value_label_padding"]
        for chan, (slider, val_label) in self.slider_widgets.items():
            val_label.setFixedWidth(max(20, int(34 * val_w_factor)))
            val_label.setStyleSheet(f"""
                background-color: {inputBg};
                border: 1px solid {borderColor};
                border-radius: {val_radius}px;
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {val_font_size}px;
                padding: {val_padding};
                qproperty-alignment: 'AlignCenter';
            """)
            
        # Scale GradientSliders (theme-aware)
        for chan, (slider, val_label) in self.slider_widgets.items():
            if isinstance(slider, GradientSlider):
                slider.update_scale(scale, slider_theme)
            
        # Style mode buttons dynamically
        btn_w = int(28 * scale)
        btn_h = int(28 * scale)
        for btn in [self.btn_mode_wheel, self.btn_mode_lab]:
            if btn is not None:
                btn.setFixedSize(btn_w, btn_h)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {barBg};
                        border: 1px solid {borderColor};
                        border-radius: {int(4 * scale)}px;
                        color: {text};
                        font-size: {int(13 * scale)}px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background-color: {bg};
                        border-color: #5a94e2;
                    }}
                """)

        # Theme + geometry for the color history panel. It uses the same
        # bg / border / text as the main chrome so it visually belongs to
        # whichever theme is active (auto / gray / white / black). Cell
        # size auto-fits the widget width (set in _relayout), so we only
        # need to push cols/rows here.
        if hasattr(self, "color_history"):
            self.color_history.configure(
                self.cfg.get("historyColumns", 8),
                self.cfg.get("historyRows", 2),
            )
            self.color_history.apply_theme(bg, border_color, text)

        # Reposition the color preview box immediately when applying theme/settings
        if hasattr(self, 'preview_box') and hasattr(self, 'sliders_container') and hasattr(self, 'title_bar'):
            title_h = self.title_bar.height()
            sliders_h = self.sliders_container.sizeHint().height()
            w = self.width()
            h = self.height()
            spacing = int(4 * scale)
            wheel_size = min(w - 8, h - 4 - title_h - sliders_h - 2 * spacing) - 4
            self.preview_box.resize_and_position(wheel_size, title_h, h, sliders_h, self.active_slot)
            self.preview_box.raise_()
            
            # If settings sidebar is open, ensure it remains on top!
            if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
                self.settings_sidebar.raise_()

    def init_hotkeys(self):
        # Register global hotkeys from config
        global_hotkeys.hotkey_signals.triggered.connect(self.on_hotkey_triggered)
        self.update_hotkey_bindings()

    def update_hotkey_bindings(self):
        global_hotkeys.unbind_all()
        global_hotkeys.bind_hotkey("pickKey", self.cfg.get("pickKey"))
        global_hotkeys.bind_hotkey("hideWindowKey", self.cfg.get("hideWindowKey"))
        global_hotkeys.bind_hotkey("followMouseKey", self.cfg.get("followMouseKey"))
        global_hotkeys.bind_hotkey("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey"))

    @pyqtSlot(str)
    def on_hotkey_triggered(self, hotkey_type):
        if hotkey_type == "hideWindowKey":
            if self.isVisible():
                self.hide()
            else:
                if self.follow_mouse_active:
                    self.show_window_at_cursor()
                else:
                    self.show()
        elif hotkey_type == "followMouseKey":
            self.follow_mouse_active = not self.follow_mouse_active
            self.cfg["followMouseEnabled"] = self.follow_mouse_active
            config.save_hotkey_config(self.cfg)
            print(f"[Hotkeys] Follow Mouse toggled to: {self.follow_mouse_active}")
            
            # Immediately move to cursor if activated and window is visible
            if self.follow_mouse_active and self.isVisible():
                self.show_window_at_cursor()
                
            # Sync settings sidebar if visible
            if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
                self.settings_sidebar.cb_follow_mouse.blockSignals(True)
                self.settings_sidebar.cb_follow_mouse.setChecked(self.follow_mouse_active)
                self.settings_sidebar.cb_follow_mouse.blockSignals(False)
        elif hotkey_type == "pickKey":
            print("[Hotkeys] Global Pick Color triggered")
        elif hotkey_type == "grayscaleFilterKey":
            print("[Hotkeys] Grayscale Filter toggled")
            try:
                result = self.grayscale_overlay.toggle()
                # DWM backend returns False on failure
                if result is False and hasattr(self.grayscale_overlay, 'last_error'):
                    err = self.grayscale_overlay.last_error
                    if err:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, "黑白滤镜", err)
            except Exception as e:
                print(f"[Hotkeys] Grayscale toggle error: {e}")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "黑白滤镜", f"切换失败: {e}")

    def init_memory_sync(self):
        # Start background memory syncing thread
        self.sync_thread = memory_sync.MemorySyncThread(self)
        self.sync_thread.signals.color_changed.connect(self.on_external_color_changed)
        self.sync_thread.signals.status_changed.connect(self.on_sync_status_changed)
        
        # Set active software mode
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        self.sync_thread.ps_version = self.cfg.get("psVersion", "auto")
        self.sync_thread.update_versions()
        
        # Start syncing
        self.sync_thread.start()

    @pyqtSlot(int, int, int)
    def on_external_color_changed(self, r, g, b):
        self.update_ui_colors(r, g, b, source="sync")

    @pyqtSlot(str, bool)
    def on_sync_status_changed(self, mode, connected):
        print(f"[Sync] Software status changed: {mode} -> connected={connected}")
        # Optionally update title bar text or border to show connection status
        status_text = f"Colorink ({mode.upper()} {'✓' if connected else '×'})"
        self.title_bar.title_label.setText(status_text)

    def toggle_settings_sidebar(self):
        vis = not self.settings_sidebar.isVisible()
        self.settings_sidebar.setVisible(vis)
        if vis:
            self.settings_sidebar.refresh_ui()
            self.settings_sidebar.raise_()
            
            # Temporarily remove WindowDoesNotAcceptFocus to allow hotkey recording / settings focus
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            if not self.cfg.get("showTaskbarIcon", False):
                flags |= Qt.WindowType.Tool
            if self.windowFlags() != flags:
                self.setWindowFlags(flags)
                self.show()
        else:
            self.update_window_flags()
        self.update_no_focus_policies()

    def refresh_slider_visibility_and_order(self):
        # Remove all from layout
        for group in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh", "History"]:
            self.sliders_layout.removeWidget(self.slider_containers[group])

        # Sort groups by order cfg; History defaults to 7 so it sits last
        groups = ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh", "History"]
        groups.sort(key=lambda g: self.cfg.get(f"orderSliders{g}", 7 if g == "History" else 1))

        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            else:
                visible = self.cfg.get(f"showSliders{g}", True if g in ("HSV", "LAB", "OKLab") else False)
            self.slider_containers[g].setVisible(visible)
            self.sliders_layout.addWidget(self.slider_containers[g])

        # Recalculate layout geometries since height changed
        self.update_geometries()

    def zoom_ui(self, factor):
        self.resize(int(320 * factor), int(710 * factor))

    def show_window_at_cursor(self):
        if self.cfg.get("lockWindowPosition", False):
            self.show()
            return
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        geom = screen.availableGeometry()
        
        # Center the window around the cursor
        w, h = self.width(), self.height()
        x = cursor_pos.x() - w // 2
        y = cursor_pos.y() - h // 2
        
        # Keep window inside the available screen geometry
        x = max(geom.x(), min(x, geom.x() + geom.width() - w))
        y = max(geom.y(), min(y, geom.y() + geom.height() - h))
        
        self.move(x, y)
        self.show()

    def init_foreground_tracker(self):
        from PyQt6.QtCore import QTimer
        self.foreground_timer = QTimer(self)
        self.foreground_timer.setInterval(400)
        self.foreground_timer.timeout.connect(self.check_foreground_window)
        self.foreground_timer.start()

    def check_foreground_window(self):
        # If settings onlyShowInCsp is False, do nothing
        if not self.cfg.get("onlyShowInCsp", False):
            return
            
        try:
            import win32gui
            import win32process
            import os
            import psutil
        except ImportError:
            return
            
        hwnd = win32gui.GetForegroundWindow()
        is_drawing_active = False
        title = ""
        exe_name = ""
        
        if hwnd:
            try:
                title = (win32gui.GetWindowText(hwnd) or "").lower()
            except Exception:
                pass
            
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    p = psutil.Process(pid)
                    exe_name = os.path.basename(p.exe()).lower()
            except Exception:
                pass
                
            is_drawing_active = (
                "clipstudiopaint.exe" in exe_name or "clipstudiopaint" in exe_name or 
                "clip studio paint" in title or "clipstudiopaint" in title or "优动漫" in title or
                "sai2.exe" in exe_name or "sai2" in exe_name or "sai2" in title or
                "painttool sai" in title or "paint tool sai" in title or
                "udmpaintpro.exe" in exe_name or "udmpaintpro" in exe_name or
                "udmpaintex.exe" in exe_name or "udmpaintex" in exe_name or "udm paint" in title or
                "photoshop.exe" in exe_name or "photoshop" in exe_name or "adobe photoshop" in title
            )
            
        is_our_focused = self.isActiveWindow()
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            if self.settings_sidebar.isActiveWindow():
                is_our_focused = True
                
        should_be_visible = is_drawing_active or is_our_focused
        
        # If follow_mouse_active is enabled and the window is visible, avoid auto-hiding it
        if getattr(self, "follow_mouse_active", False) and self.isVisible():
            should_be_visible = True
            
        if should_be_visible:
            if not self.isVisible():
                self.show()
                self.raise_()
            self.auto_hidden = False
        else:
            if self.isVisible():
                self.hide()
                self.auto_hidden = True

    def on_settings_saved(self):
        # Reload configs
        self.cfg = config.load_hotkey_config()
        self.update_hotkey_bindings()

        # Update grayscale overlay — check if backend changed
        new_backend = self.cfg.get("grayscaleFilterBackend", "overlay")
        current_is_dwm = not isinstance(self.grayscale_overlay, GrayscaleOverlay)
        if (new_backend == "dwm") != current_is_dwm:
            # Backend changed — tear down old, create new
            self.grayscale_overlay.set_active(False)
            if hasattr(self.grayscale_overlay, 'close'):
                self.grayscale_overlay.close()
            mode = self.cfg.get("grayscaleFilterMode", "oklch")
            if new_backend == "dwm":
                from core.dcomp_grayscale import DCompOverlayController
                self.grayscale_overlay = DCompOverlayController()
                if not self.grayscale_overlay.is_available:
                    self.grayscale_overlay = GrayscaleOverlay(mode=mode)
            else:
                self.grayscale_overlay = GrayscaleOverlay(mode=mode)

        # Update grayscale overlay target and mode
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)
        mode = self.cfg.get("grayscaleFilterMode", "oklch")
        self.grayscale_overlay.set_mode(mode)

        # Update window flags dynamically
        self.update_window_flags()
        self.update_no_focus_policies()

        # Restore visibility if onlyShowInCsp is turned off while auto_hidden
        if not self.cfg.get("onlyShowInCsp", False):
            if getattr(self, "auto_hidden", False):
                self.show()
                self.auto_hidden = False
        
        # Update active software mode in thread
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        # Update settings dialog variables in thread
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        self.sync_thread.ps_version = self.cfg.get("psVersion", "auto")
        self.sync_thread.update_versions()
        
        # Update follow mouse state
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        
        # Update color wheel mode
        cfg_color_mode = self.cfg.get("colorWheelMode", "hsv")
        cfg_wheel_mode = self.cfg.get("wheelMode", "hsv-square")
        if cfg_color_mode == "hls":
            self.color_wheel.set_wheel_mode("hls-triangle")
        elif cfg_color_mode == "rgb":
            self.color_wheel.set_wheel_mode("rgb-slice")
        elif cfg_color_mode == "oklch":
            self.color_wheel.set_wheel_mode("oklch-slice")
        else:
            self.color_wheel.set_wheel_mode(cfg_wheel_mode)
        
        self.color_wheel.reload_config()
        
        # Update lab visualizer mode
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            self.lab_square.set_render_mode(viz_mode)
            # Update max_val in config for persistence
            self.cfg["labVisualizerMaxVal"] = 110 if viz_mode == "lab" else 0.4
        
        # Apply slider visibility and order
        self.refresh_slider_visibility_and_order()
        
        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.apply_theme()
        
        # Apply scaling zoom factor only if the target scale configuration has changed
        target_scale = self.cfg.get("uiScale", 100)
        if getattr(self, "current_ui_scale", 100) != target_scale:
            self.zoom_ui(target_scale / 100.0)
            self.current_ui_scale = target_scale
        else:
            self.update()

    def close_application(self):
        # Save window settings on exit, normalized to 1x DPI for consistency
        dpr = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if dpr < 0.1:
            dpr = 1.0
        cfg = {
            "x": self.x(),
            "y": self.y(),
            "width": self.width(),
            "height": self.height(),
            "dpr": dpr,  # Store DPR so we can restore correctly
            "zoom": 0  # Default placeholder
        }
        config.save_window_config(cfg)
        
        # Clean up hotkeys and thread
        global_hotkeys.unbind_all()
        if hasattr(self, 'grayscale_overlay'):
            self.grayscale_overlay.set_active(False)
            if hasattr(self.grayscale_overlay, 'close'):
                self.grayscale_overlay.close()
        if hasattr(self, 'sync_thread'):
            self.sync_thread.stop()
        
        sys.exit(0)

    def update_window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if not self.cfg.get("showTaskbarIcon", False):
            flags |= Qt.WindowType.Tool
            
        # Only apply no-focus mode if settings sidebar is CLOSED
        no_focus = self.cfg.get("noFocusMode", False) and not (hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible())
        if no_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
            
        if self.windowFlags() != flags:
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
                
        # Double safety: Force WS_EX_NOACTIVATE via Win32 API
        if no_focus:
            try:
                import win32gui
                import win32con
                hwnd = int(self.winId())
                ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if not (ex_style & win32con.WS_EX_NOACTIVATE):
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_NOACTIVATE)
            except Exception:
                pass

    def update_no_focus_policies(self):
        is_settings_open = hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible()
        enabled = self.cfg.get("noFocusMode", False) and not is_settings_open
        
        policy = Qt.FocusPolicy.NoFocus if enabled else Qt.FocusPolicy.StrongFocus
        
        self.setFocusPolicy(policy)
        if hasattr(self, 'color_wheel'):
            self.color_wheel.setFocusPolicy(policy)
        if hasattr(self, 'lab_square'):
            self.lab_square.setFocusPolicy(policy)
        if hasattr(self, 'lab_slider'):
            self.lab_slider.setFocusPolicy(policy)
        if hasattr(self, 'preview_box'):
            self.preview_box.setFocusPolicy(policy)
        
        if hasattr(self, 'slider_widgets'):
            for chan, (slider, val_label) in self.slider_widgets.items():
                slider.setFocusPolicy(policy)
            
        if hasattr(self, 'title_bar'):
            for btn in [self.title_bar.btn_settings, self.title_bar.btn_close, self.title_bar.btn_min]:
                btn.setFocusPolicy(policy)
