import colorsys
import math
import os
import re
import sys
from typing import cast

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import config, global_hotkeys, memory_sync
from ui.color_conversions import (
    clamp_rgb,
    lab_to_rgb,
    map_lab_to_gamut,
    map_oklab_to_gamut,
    oklab_to_rgb,
    oklch_to_rgb,
    rgb_to_lab,
    rgb_to_oklab,
    rgb_to_oklch,
)
from ui.color_history import ColorHistoryWidget
from ui.color_picker_overlay import ColorPickerOverlay
from ui.color_preview_box import ColorPreviewBox
from ui.color_wheel import ColorWheel, hls_to_hsv_floats, hsv_to_rgb, rgb_to_hsv
from ui.hotkey_button import (
    MOUSE_BUTTON_NAME_BY_QT,
    capture_active,
    is_mouse_hotkey,
    parse_key_event,
)
from ui.lab_visualizer import LabSlider, LabSquare
from ui.picker_panes import LabPane, PaneWithModeButton, WheelPane
from ui.ringless_mode import (
    RINGLESS_ACTIVE_BORDER,
    RinglessConfig,
    resolve_ringless_layout,
)
from ui.settings_sidebar import SettingsSidebar
from ui.slider_themes import get_slider_theme

# Drawing applications recognized by the "only show while the drawing app is
# in the foreground" tracker (onlyShowInCsp). Process basenames are matched
# with the ".exe" extension stripped; window titles are lowercased.
_DRAWING_APP_EXE_MARKERS = (
    "clipstudiopaint",   # CLIP Studio Paint main + CLIPStudioPaintApp painting process
    "clipstudio",        # CSP launcher / companion processes
    "sai",               # PaintTool SAI 1.x / 2.x (sai.exe / sai2.exe)
    "udmpaint",          # UDM Paint (UDMPaintPro.exe / UDMPaintEx.exe)
    "photoshop",         # Adobe Photoshop
)


def _exe_matches_drawing_app(exe_name: str) -> bool:
    """True if a lowercased process basename belongs to a drawing app.

    The extension is stripped first so "sai2.exe" and "sai.exe" both match
    the same "sai" marker.
    """
    stem = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name
    return any(marker in stem for marker in _DRAWING_APP_EXE_MARKERS)


def _title_matches_drawing_app(title: str) -> bool:
    """True if a lowercased window title belongs to a drawing app.

    Latin app names are matched at a word boundary so titles like
    "Photosai" can't false-positive on the "sai" marker, while real-world
    titles such as "SAI Ver.2" or "paint tool sai" still match.
    """
    if "clip studio paint" in title or "优动漫" in title or "photoshop" in title:
        return True
    if re.search(r"(?<![a-z0-9])sai", title):  # SAI / SAI Ver.2 / paint tool sai
        return True
    if re.search(r"(?<![a-z0-9])udm", title):  # UDM Paint
        return True
    return False


def _resolve_process_exe(pid: int) -> str:
    """Resolve a PID to its executable basename (lowercased).

    psutil first; if it fails (elevated / protected process, antivirus
    interference) fall back to QueryFullProcessImageNameW via ctypes so the
    foreground check keeps working for admin-run drawing apps.
    """
    try:
        import psutil
        exe = psutil.Process(pid).exe()
        if exe:
            return os.path.basename(exe).lower()
    except Exception:
        pass
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


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


# ── Color-space module definitions ────────────────────────────────────────
# Each module bundles a default wheel mode + slider subset (user can
# still toggle individual slider groups in settings via the "module
# default + adjustable" policy).
_MODULE_DEFS = {
    "hsv":  {"wheel": "hsv-square",   "sliders": ["HSV", "RGB", "LAB", "OKLab", "OKLCh"]},
    "hls":  {"wheel": "hls-triangle",  "sliders": ["HSL", "RGB", "LAB", "OKLab", "OKLCh"]},
    "rgb":  {"wheel": "rgb-slice",     "sliders": ["RGB", "HSV", "LAB", "OKLab", "OKLCh"]},
    "lch":  {"wheel": "oklch-slice",   "sliders": ["OKLCh", "OKLab", "RGB"]},
}
_MODULE_NAMES = {"hsv": "HSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
_MODULE_ORDER = ["hsv", "hls", "rgb", "lch"]

# ── Normalized chroma scale for the C_oklch slider ────────────────────────
# The C slider keeps its handle stable while L/H changes by representing a
# fraction of the current in-gamut chroma boundary. L-only drags still use
# the absolute C/H snapshot for authentic OKLCh coordinates.
_C_SCALE = 1000          # 0.001 chroma resolution (legacy; not used for slider→C)
_C_RAW_MAX = 100         # slider range → 0..100% of max chroma


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = cast("MainWindow", parent)
        self.drag_position = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.init_ui()

    def _show_context_menu(self, pos):
        """Quick toggles for the most-used settings."""
        p = self._parent
        menu = QMenu(self)

        act_follow = menu.addAction("跟随鼠标")
        if act_follow is not None:
            act_follow.setCheckable(True)
            act_follow.setChecked(cast(bool, p.cfg.get("followMouseEnabled", False)))
            act_follow.triggered.connect(lambda checked: self._toggle_follow_mouse(checked))

        act_no_focus = menu.addAction("无焦点选色模式")
        if act_no_focus is not None:
            act_no_focus.setCheckable(True)
            act_no_focus.setChecked(cast(bool, p.cfg.get("noFocusMode", False)))
            act_no_focus.triggered.connect(lambda checked: self._toggle_no_focus(checked))

        menu.addSeparator()
        act_settings = menu.addAction("打开设置")
        if act_settings is not None:
            act_settings.triggered.connect(p.toggle_settings_sidebar)
        menu.exec(self.mapToGlobal(pos))

    def _toggle_follow_mouse(self, checked):
        p = self._parent
        p.follow_mouse_active = checked
        p.cfg["followMouseEnabled"] = checked
        config.save_hotkey_config(p.cfg)
        if checked and p.isVisible():
            p.show_window_at_cursor()
        sidebar = getattr(p, "settings_sidebar", None)
        if sidebar is not None and sidebar.isVisible():
            sidebar.cfg["followMouseEnabled"] = checked
            sidebar.cb_follow_mouse.blockSignals(True)
            sidebar.cb_follow_mouse.setChecked(checked)
            sidebar.cb_follow_mouse.blockSignals(False)
            sidebar._persist_config()

    def _toggle_no_focus(self, checked):
        p = self._parent
        p.cfg["noFocusMode"] = checked
        config.save_hotkey_config(p.cfg)
        p.update_window_flags()
        p.update_no_focus_policies()
        sidebar = getattr(p, "settings_sidebar", None)
        if sidebar is not None and sidebar.isVisible():
            sidebar.cfg["noFocusMode"] = checked
            sidebar.cb_no_focus.blockSignals(True)
            sidebar.cb_no_focus.setChecked(checked)
            sidebar.cb_no_focus.blockSignals(False)
            sidebar._persist_config()

    def init_ui(self):
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        # Settings Button (Hamburger)
        self.btn_settings = QPushButton("☰")
        self.btn_settings.setFixedSize(9, 9)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        # Never keep keyboard focus — otherwise Space (the default LAB-toggle
        # hotkey) would re-click the focused button and toggle the settings.
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self.btn_min.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self.btn_min.clicked.connect(self._parent.showMinimized)
        
        # Close Button
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(9, 9)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
            if not self._parent.cfg.get("lockWindowPosition", False):
                self.drag_position = event.globalPosition().toPoint() - self._parent.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position is not None:
            if not self._parent.cfg.get("lockWindowPosition", False):
                self._parent.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None


class SliderValueLabel(QLabel):
    """Compact slider readout with clear hover-only +/-1 controls."""

    def __init__(self, slider, parent=None):
        super().__init__("0", parent)
        self.slider = slider
        self._hovered = False
        self._hover_half = 1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Upper half: +1; lower half: -1")

    def enterEvent(self, event):
        self._hovered = True
        local_pos = self.mapFromGlobal(QCursor.pos())
        self._hover_half = 1 if local_pos.y() < self.height() / 2 else -1
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, a0):
        self._hovered = False
        self._hover_half = 0
        self.update()
        super().leaveEvent(a0)

    def mouseMoveEvent(self, ev: QMouseEvent):
        next_half = 1 if ev.position().y() < self.height() / 2 else -1
        if next_half != self._hover_half:
            self._hover_half = next_half
            self.update()
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            delta = 1 if ev.position().y() < self.height() / 2 else -1
            new_value = max(
                self.slider.minimum(),
                min(self.slider.maximum(), self.slider.value() + delta),
            )
            if new_value != self.slider.value():
                self.slider.setValue(new_value)
                self.slider.sliderReleased.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def paintEvent(self, a0):
        super().paintEvent(a0)
        if not self._hovered:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        strip_left = max(0, self.width() - 12)
        half_height = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)

        # The active half gets a stronger tint so the click target is obvious.
        for half, center_y in ((1, half_height * 0.5), (-1, half_height * 1.5)):
            is_active = half == self._hover_half
            bg = QColor(90, 148, 226, 90 if is_active else 28)
            painter.setBrush(bg)
            painter.drawRoundedRect(
                QRectF(strip_left + 1, center_y - half_height * 0.5 + 1,
                       self.width() - strip_left - 2, half_height - 2),
                2, 2,
            )

            arrow_color = self.palette().color(QPalette.ColorRole.Text)
            arrow_color.setAlpha(230 if is_active else 115)
            painter.setBrush(arrow_color)
            x = self.width() - 6
            if half == 1:
                points = [
                    QPointF(x, center_y - 5),
                    QPointF(x - 5, center_y + 3),
                    QPointF(x + 5, center_y + 3),
                ]
            else:
                points = [
                    QPointF(x, center_y + 5),
                    QPointF(x - 5, center_y - 3),
                    QPointF(x + 5, center_y - 3),
                ]
            painter.drawPolygon(QPolygonF(points))
        painter.end()


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
        if win is not None:
            win_cfg = getattr(win, "cfg", None)
            if win_cfg is not None:
                step = win_cfg.get("sliderScrollStep", 1)
        
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
        self.groove_h = max(2, int(16 * scale * float(cast(float, t["groove_h_factor"]))))
        self.groove_radius = 3.0 * scale * float(cast(float, t["groove_radius_factor"]))
        handle_w = max(2, int(5 * scale * float(cast(float, t["handle_w_factor"]))))
        handle_h = max(4, int(24 * scale * float(cast(float, t["handle_h_factor"]))))
        margin_y = -max(1, int(4 * scale * float(cast(float, t["handle_margin_y_factor"]))))
        border_radius = max(0, int(1 * scale * float(cast(float, t["handle_radius_factor"]))))

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
            tri_off = int(float(cast(float, t.get("handle_tri_offset_y", 2))) * scale)
            tri_h = int(float(cast(float, t.get("handle_tri_size_h", 6))) * scale)
            pad = max(2, int(2 * scale))
            self.setMinimumHeight(self.groove_h + tri_off + tri_h + pad)
        else:
            # Native handle is invisible (transparent fill, no border).
            # The double-ring border is drawn underneath in paintEvent and
            # shows through. Hover adds a blue ring on top.
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
                QSlider::handle:horizontal:hover {{
                    background: transparent;
                    border: none;
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
        painter.setClipRect(self.rect())  # prevent partial-update clipping of handle overhang
        
        rect = self.rect()
        groove_y = (rect.height() - self.groove_h) // 2
        groove_rect = QRectF(0, groove_y, rect.width(), self.groove_h)
        
        grad = QLinearGradient(0, 0, rect.width(), 0)
        for stop, color in self.gradient_colors:
            grad.setColorAt(stop, color)
             
        # Fill groove
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(groove_rect, self.groove_radius, self.groove_radius)
        
        # Out-of-gamut overlay
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

        if handle_shape == "triangle-below":
            vrange = self.maximum() - self.minimum()
            frac = (self.value() - self.minimum()) / vrange if vrange > 0 else 0.0
            handle_x = frac * rect.width()

            tri_color = QColor(t.get("handle_tri_color", t["handle_bg"]))
            tri_border_color = QColor(t.get("handle_tri_border", t["handle_border"]))
            tri_size_w = float(cast(float, t.get("handle_tri_size_w", 5))) * self.scale
            tri_size_h = float(cast(float, t.get("handle_tri_size_h", 6))) * self.scale
            tri_offset_y = int(float(cast(float, t.get("handle_tri_offset_y", 2))) * self.scale)
            tri_base_y = groove_y + self.groove_h + tri_offset_y

            triangle = QPolygonF([
                QPointF(handle_x, tri_base_y),
                QPointF(handle_x - tri_size_w, tri_base_y + tri_size_h),
                QPointF(handle_x + tri_size_w, tri_base_y + tri_size_h),
            ])
            painter.setBrush(tri_color)
            painter.setPen(QPen(tri_border_color, 1))
            painter.drawPolygon(triangle)
            painter.end()
            # Do NOT call super().paintEvent — we own this paint
        else:
            # Draw the double-ring border UNDER the invisible native handle.
            # QStyle's rect ensures alignment; hover state is custom-drawn
            # so it always matches pixel-for-pixel.
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            _style = self.style()
            if _style is None:
                hr_q = QRect()
            else:
                hr_q = _style.subControlRect(
                    QStyle.ComplexControl.CC_Slider, opt,
                    QStyle.SubControl.SC_SliderHandle, self
                )
            is_active = bool(opt.activeSubControls & QStyle.SubControl.SC_SliderHandle)
            hx, hy, hw, hh = float(hr_q.x()), float(hr_q.y()), float(hr_q.width()), float(hr_q.height())
            hr = max(0, int(1 * self.scale * float(cast(float, t["handle_radius_factor"]))))
            hf = QRectF(hx, hy, hw, hh)

            bw = max(1, int(1 * self.scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # Inner ring: white (normal) or theme hover colour (active)
            inner_color = QColor(t["handle_hover_border"]) if is_active else QColor(255, 255, 255, 200)
            wi = QRectF(hx + bw, hy + bw, hw - 2 * bw, hh - 2 * bw)
            wr = max(0, hr - bw)
            painter.setPen(QPen(inner_color, bw))
            painter.drawRoundedRect(wi, wr, wr)

            # Black outer ring (on top)
            painter.setPen(QPen(QColor(0, 0, 0, 200), bw))
            painter.drawRoundedRect(hf, hr, hr)

            painter.end()
            super().paintEvent(event)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load_hotkey_config()
        self.current_ui_scale = self.cfg.get("uiScale", 100)
        self.current_rgb = (180, 130, 30)
        self.active_slot = "fg"  # "fg" | "bg"

        # Deferred-render coalescer.
        # update_video_colors() does heavy pure-visual work (18 slider groove
        # gradients + L-gamut ranges binary search) on every slider/wheel drag
        # step. Running that synchronously inside the dragged widget's
        # mouseMoveEvent blocks the GUI thread and delays the slider handle /
        # color-wheel indicator paints, so they stop following the cursor
        # ("不跟手"). We defer exactly that visual-only work with a coalesced
        # single-shot QTimer so handle/indicator paints flush first and the
        # heavy cosmetics lag by at most one event-loop iteration (~16ms).
        self._deferred_color_timer = QTimer(self)
        self._deferred_color_timer.setSingleShot(True)
        self._deferred_color_timer.setInterval(16)
        self._deferred_color_timer.timeout.connect(self._apply_deferred_color_updates)
        self._deferred_color_pending: tuple[int, int, int] | None = None  # latest (r, g, b) awaiting render
        self._lab_gamut_timer: QTimer = QTimer(self)
        self._lab_gamut_timer.setSingleShot(True)
        self._lab_gamut_timer.timeout.connect(self._update_lab_slider_gamut_range)
        self._lab_prerender_timer: QTimer = QTimer(self)
        self._lab_prerender_timer.setSingleShot(True)
        self._lab_prerender_timer.timeout.connect(self._prerender_lab)
        self._module_layout_timer: QTimer = QTimer(self)
        self._module_layout_timer.setSingleShot(True)
        self._module_layout_timer.timeout.connect(self._flush_module_layout_refresh)
        self._module_layout_refresh_pending: bool = False
        self._module_save_timer: QTimer = QTimer(self)
        self._module_save_timer.setSingleShot(True)
        self._module_save_timer.timeout.connect(self._flush_module_config_save)
        self._module_save_pending: bool = False
        self._deferred_dynamic_gradients_pending: bool = False
        self._oklch_target_h = 0.0
        self._oklch_target_frac = 0.0
        self._gamut_oklch_C = None
        self._gamut_oklch_h = None

        # Content-driven window height state
        self._last_auto_height = None
        self._manual_height_override = False
        self._adjusting_content_height = False
        self._content_height_adjust_pending = False
        self._content_height_timer = QTimer(self)
        self._content_height_timer.setSingleShot(True)
        self._content_height_timer.timeout.connect(self._run_deferred_content_height)
        
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

        # Fullscreen grayscale: one native OKLCh path plus the Windows Mag
        # Luma fallback. Legacy capture/render backends are migrated to native.
        mode = self.cfg.get("grayscaleFilterMode", "oklch")
        backend = self.cfg.get("grayscaleFilterBackend", "native")
        if backend == "mag":
            from core.mag_grayscale import MagFilterController
            self.grayscale_overlay = MagFilterController(mode="luma")
        else:
            from core.native_grayscale import NativeGrayscaleController
            self.grayscale_overlay = NativeGrayscaleController(mode="oklch")
        self.grayscale_overlay.set_target("all")
        # Warm the OKLCh capture/OpenGL/PBO chain off-screen so Ctrl+G only
        # reveals a prepared frame instead of paying initialization latency.
        if backend != "mag":
            prepare = getattr(self.grayscale_overlay, "prepare", None)
            if callable(prepare):
                # grayscale_overlay.prepare runs once the window is up:
                # it warms the OKLCh GL overlay off-screen (light preheat).
                QTimer.singleShot(800, prepare)
            # Pre-import dxcam off the GUI thread so the first Ctrl+G does not
            # pay the one-time ~0.4s module/D3D11/comtypes import cost.
            if sys.modules.get("dxcam") is None:
                import importlib as _importlib
                import threading as _threading
                _threading.Thread(
                    target=_importlib.import_module,
                    args=("dxcam",),
                    daemon=True,
                    name="colorink-dxcam-preload",
                ).start()

        # Global color picker overlay (magnifier + click-to-pick)
        self.picker_overlay = ColorPickerOverlay(None)
        self.picker_overlay.colorPicked.connect(self._on_picker_color_picked)

        self.init_ui()
        self.init_hotkeys()
        self.init_memory_sync()
        self.init_foreground_tracker()
        self.apply_theme()
        self.init_tray()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

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

        # Source-space tracking — carries the "native" color space the user
        # last interacted with, so sync backends can write source→memory
        # without an RGB→source round-trip.
        self._source_space = "rgb"     # "hsv" | "hls" | "rgb" | "lab" | "oklab" | "oklch" | "cmyk"
        self._source_values = None     # {ch: float, ...}  — float values in that space
        # Per-slot source tracking (fg / bg)
        self._fg_source_space = "rgb"
        self._fg_source_values = None
        self._bg_source_space = "rgb"
        self._bg_source_values = None

        # Floating mode buttons parented to their respective views
        self.btn_mode_wheel = QPushButton("☉", self.pane_wheel)
        self.btn_mode_wheel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_wheel.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_wheel.clicked.connect(self.toggle_picker_mode)
        # Never keep keyboard focus: otherwise Space (the default LAB-toggle
        # hotkey) would "click" the focused button from anywhere in the window.
        self.btn_mode_wheel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pane_wheel.set_mode_button(self.btn_mode_wheel)
        
        self.stack.addWidget(self.pane_wheel)

        # Pane 2: LAB Space
        self.pane_lab = LabPane(self)
        self.lab_layout = QHBoxLayout(self.pane_lab)
        self.lab_layout.setContentsMargins(0, 0, 0, 0)
        self.lab_layout.setSpacing(6)
        
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
        self.lab_slider.lightnessChanged.connect(self._on_lab_lightness_changed)
        self.lab_slider.interactionFinished.connect(self.on_interaction_finished)
        slider_col_layout.addWidget(self.lab_slider)
        
        self.lab_layout.addWidget(self.lab_square, stretch=1)
        self.lab_layout.addWidget(self.lab_slider_column)
        
        # Floating mode button parented directly to self.pane_lab
        self.btn_mode_lab = QPushButton("△", self.pane_lab)
        self.btn_mode_lab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_lab.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_lab.clicked.connect(self.toggle_picker_mode)
        self.btn_mode_lab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

        # Create floating module switch button (next to ⊙/△)
        self._init_module_button()

        # Apply color-space module (wheel mode + slider set from config or default)
        self._current_module = self.cfg.get("colorSpaceModule", "hsv")
        self._apply_module(self._current_module)
        
        # Apply slider visibility and order on startup
        self.refresh_slider_visibility_and_order()
        self.update_mode_buttons_visibility()
        self.update_no_focus_policies()

        # Apply persisted ringless state after all components and button
        # visibility are known
        self._sync_ringless_mode()
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_lab_prerender(100)

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
        # Restore persisted colors (config stores a list of entries — old
        # format [r,g,b] or new format {"rgb":[r,g,b],"s":"hsv","v":[h,s,v]})
        persisted = self.cfg.get("historyColors", [])
        self._history_source = {}  # index → {"source":..., "values":...}
        self._color_source_store = {}  # hex_key → {"rgb":..., "s":..., "v":...}
        self._SOURCE_CHANNELS = {
            "rgb": ("r", "g", "b"), "cmyk": ("c", "m", "y", "k"),
            "hsv": ("h", "s", "v"), "hls": ("h", "l", "s"),
            "lab": ("l", "a", "b"), "oklab": ("L", "a", "b"), "oklch": ("L", "C", "h"),
        }
        if persisted:
            from PyQt6.QtGui import QColor as _QColor
            initial_colors = []
            for i, entry in enumerate(persisted):
                src = vals = None
                if isinstance(entry, list):
                    r, g, b = int(entry[0]), int(entry[1]), int(entry[2])
                elif isinstance(entry, dict):
                    rgb = entry.get("rgb", [0, 0, 0])
                    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
                    src = entry.get("s")
                    vals = entry.get("v")
                    if src and vals:
                        self._history_source[i] = {"source": src, "values": vals}
                else:
                    continue
                hex_key = f"#{r:02x}{g:02x}{b:02x}"
                initial_colors.append(_QColor(r, g, b))
                if src and vals:
                    self._color_source_store[hex_key] = {"rgb": [r, g, b], "s": src, "v": vals}
            self.color_history.set_colors(initial_colors)
        history_lay.addWidget(self.color_history)
        self.sliders_layout.addWidget(self.slider_containers["History"])

    def create_group_sliders(self, group, channels, layout):
        for chan in channels:
            row = QHBoxLayout()
            row.setSpacing(1)
            self.slider_row_layouts.append(row)
            
            # Label
            label_text = chan.split("_")[0].upper()
            label = QLabel(label_text)
            label.setFixedWidth(12)
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
                slider.setRange(0, _C_RAW_MAX)
            elif chan == "h_oklch":
                slider.setRange(0, 360)
            else:
                slider.setRange(0, 255)
                
            val_label = SliderValueLabel(slider)
            val_label.setFixedWidth(27)
            val_label.setObjectName("ValueLabel")
            val_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
            row.addWidget(label)
            row.addWidget(slider)
            row.addSpacing(1)
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
            # Save current source to bg slot
            self._bg_source_space = self._source_space
            self._bg_source_values = self._source_values
            self.active_slot = "fg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Restore fg source
            self._source_space = self._fg_source_space
            self._source_values = self._fg_source_values
            col = self.preview_box.fg_color
            self.update_ui_colors(col.red(), col.green(), col.blue(), source="slot_change")

    def select_bg_slot(self):
        if self.active_slot != "bg":
            # Save current source to fg slot
            self._fg_source_space = self._source_space
            self._fg_source_values = self._source_values
            self.active_slot = "bg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Restore bg source
            self._source_space = self._bg_source_space
            self._source_values = self._bg_source_values
            col = self.preview_box.bg_color
            self.update_ui_colors(col.red(), col.green(), col.blue(), source="slot_change")

    def swap_colors(self):
        # Swap foreground and background
        fg = self.preview_box.fg_color
        bg = self.preview_box.bg_color
        self.preview_box.set_colors(bg, fg)
        # Swap source tracking
        self._fg_source_space, self._bg_source_space = self._bg_source_space, self._fg_source_space
        self._fg_source_values, self._bg_source_values = self._bg_source_values, self._fg_source_values
        # Update current source to match newly active slot
        if self.active_slot == "fg":
            self._source_space = self._fg_source_space
            self._source_values = self._fg_source_values
        else:
            self._source_space = self._bg_source_space
            self._source_values = self._bg_source_values
        # Maintain active slot color
        active_color = bg if self.active_slot == "fg" else fg
        r, g, b = active_color.red(), active_color.green(), active_color.blue()
        self.update_ui_colors(r, g, b, source="swap")

    def on_history_color_picked(self, color):
        """User clicked a swatch in the history widget → load into active slot."""
        r, g, b = color.red(), color.green(), color.blue()
        hex_key = f"#{r:02x}{g:02x}{b:02x}"
        stored = self._color_source_store.get(hex_key)
        if stored:
            self._source_space = stored["s"]
            # Reconstruct float values from stored list
            vals_list = stored.get("v", [])
            if hasattr(self, "_SOURCE_CHANNELS"):
                ch_names = self._SOURCE_CHANNELS.get(self._source_space, [])
                self._source_values = {ch: float(vals_list[i]) for i, ch in enumerate(ch_names) if i < len(vals_list)}
            else:
                self._source_values = None
        else:
            self._source_space = "rgb"
            self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        self.update_ui_colors(r, g, b, source="history")
        if hasattr(self, "color_history"):
            updated = self.color_history.mark_selected(color)
            self.cfg["historyColors"] = self._build_history_entries(updated)
            from core import config as _config
            _config.save_hotkey_config(self.cfg)

    def _record_color_history(self):
        """Persist the latest RGB into the history widget and into config.
        Called when an interaction finishes (slider/wheel/lab release)."""
        if not hasattr(self, "color_history"):
            return
        r, g, b = self.current_rgb
        updated = self.color_history.record(r, g, b)
        # Cache source info for the newest entry
        if self._source_space and self._source_values:
            hex_key = f"#{r:02x}{g:02x}{b:02x}"
            ch_names = self._SOURCE_CHANNELS.get(self._source_space, [])
            vals_list = [round(float(self._source_values.get(ch, 0)), 4)
                         for ch in ch_names]
            self._color_source_store[hex_key] = {
                "rgb": [r, g, b], "s": self._source_space, "v": vals_list,
            }
        self.cfg["historyColors"] = self._build_history_entries(list(updated))
        from core import config as _config
        _config.save_hotkey_config(self.cfg)

    def _build_history_entries(self, colors):
        """Convert QColor list to serialisable entries, preserving source info."""
        entries = []
        for c in colors:
            hex_key = f"#{c.red():02x}{c.green():02x}{c.blue():02x}"
            stored = self._color_source_store.get(hex_key)
            if stored:
                entries.append(stored)
            else:
                entries.append([int(c.red()), int(c.green()), int(c.blue())])
        return entries

    def _init_module_button(self):
        """Create a floating button next to ⊙/△ to cycle HSV→HLS→LCH modules."""
        btn = QPushButton("HSV", self.pane_wheel)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("切换色彩空间模块 (HSV / HLS / LCH)")
        btn.clicked.connect(self._next_module)
        # Never keep keyboard focus — Space (the default LAB-toggle hotkey)
        # must not re-activate this button from anywhere in the window.
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setVisible(self.cfg.get("showModuleSwitchButton", True))
        btn.setObjectName("ModuleButton")
        self.pane_wheel.set_module_button(btn)
        self.btn_module = btn

    def _update_module_button_label(self):
        if hasattr(self, "btn_module"):
            name = {"hsv": "◉", "hls": "△", "lch": "◈"}.get(self._current_module, "◉")
            self.btn_module.setText(name)
            self.btn_module.setToolTip(f"模块: {_MODULE_NAMES.get(self._current_module, 'HSV')} (点击切换)")

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        show_module = self.cfg.get("showModuleSwitchButton", True)
        show_lab_toggle = self.cfg.get("showLabToggleButton", True)
        if hasattr(self, "pane_wheel"):
            self.pane_wheel.set_module_slot_reserved(show_module)
        if hasattr(self, "pane_lab"):
            self.pane_lab.set_module_slot_reserved(show_module)
        if idx == 0:
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_wheel.raise_()
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.hide()
            # Module button only visible in wheel pane
            if hasattr(self, 'btn_module'):
                self.btn_module.setVisible(show_module)
                if show_module:
                    self.btn_module.raise_()
        else:
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_lab.raise_()
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.hide()
            if hasattr(self, 'btn_module'):
                self.btn_module.hide()

    def _sync_ringless_mode(self, wheel_size: int | None = None,
                            title_bar_height: int | None = None) -> None:
        """Parse ringless config and propagate layout to all components.

        The single orchestration entry point: reads ``hideHueRing`` /
        ``ringlessControlsSide``, resolves a ``RinglessLayout`` via
        :func:`resolve_ringless_layout` (using ``stack.currentIndex() == 0``
        for the page gate), and pushes it to ``ColorWheel``,
        ``ColorPreviewBox``, ``WheelPane``, and ``LabPane``.

        On every page the LAB layout reserves a margin equal to
        ``control_bar_height`` on the configured top or bottom edge when
        controls are enabled (ringless active), and zeros it when disabled.
        This gives LAB the same rectangle controls / mode button bar as the
        wheel pane.

        In active ringless mode the stack gets a minimum height of
        ``wheel_size + control_bar_height`` so the slice area stays
        near-square.  Otherwise the explicit ringless minimum is cleared
        (``setMinimumHeight(0)``) and the child hints apply.
        """
        enabled = self.cfg.get("hideHueRing", False)
        raw_side = self.cfg.get("ringlessControlsSide", "right")
        config = RinglessConfig.from_values(
            enabled, raw_side, self.cfg.get("ringlessControlBarPosition", "top")
        )

        wheel_page_active = self.stack.currentIndex() == 0
        scale = self.cfg.get("uiScale", 100) / 100.0
        layout = resolve_ringless_layout(config, wheel_page_active, scale)

        # ── Push layout to every ringless-aware component ──
        self.color_wheel.set_ringless_layout(layout)

        _ws = wheel_size if wheel_size is not None else self.width() - int(16 * scale)
        _tbh = title_bar_height if title_bar_height is not None else self.title_bar.height()
        self.preview_box.set_ringless_layout(
            layout, self.width(), _tbh, self.stack.height()
        )

        self.pane_wheel.set_ringless_layout(layout)
        self.pane_lab.set_ringless_layout(layout)

        control_margin = layout.control_bar_height if layout.controls_enabled else 0
        if layout.control_bar_position == "bottom":
            self.lab_layout.setContentsMargins(0, 0, 0, control_margin)
        else:
            self.lab_layout.setContentsMargins(0, control_margin, 0, 0)

        # ── Stack minimum height ──
        if layout.controls_enabled:
            self.stack.setMinimumHeight(max(1, _ws + layout.control_bar_height))
        else:
            self.stack.setMinimumHeight(0)

    def toggle_picker_mode(self):
        """Switch picker panes without re-running the full theme/layout pass."""
        new_index = (self.stack.currentIndex() + 1) % 2
        self.stack.setCurrentIndex(new_index)
        self.update_mode_buttons_visibility()
        # Only the page-local ringless geometry needs to move here. Re-running
        # apply_theme() would rebuild every slider stylesheet and gradient while
        # the user is only asking for a pane switch.
        self._sync_ringless_mode()
        self._update_lab_avoid()
        self.color_wheel.schedule_slice_prewarm(350)

        r, g, b = self.current_rgb
        if new_index == 1:  # LAB pane
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(self.lab_square.L)
            lab_slider_column = getattr(self, "lab_slider_column", None)
            if lab_slider_column is not None and lab_slider_column.isVisible():
                self._schedule_lab_gamut_range(50)
        else:  # Color wheel pane
            # The wheel already owns the exact state when the last source was
            # a wheel interaction. Avoid RGB?HSV re-quantization here: it can
            # shift hue slightly and evict the resident full-resolution slice.
            last_source = getattr(self, "_last_update_source", "")
            wheel_rgb = None
            get_color = getattr(self.color_wheel, "get_color", None)
            if callable(get_color):
                wheel_rgb = get_color()
            if last_source != "wheel" and wheel_rgb != (r, g, b):
                self.color_wheel.set_color(r, g, b, block_signals=True)
            else:
                self.color_wheel.update()
            if hasattr(self, "_schedule_lab_prerender"):
                self._schedule_lab_prerender(50)
        self.update()

    def _update_lab_avoid(self):
        """Tell LabSquare how much of its top is covered by the floating
        preview box, so the ab plane renders below it instead of being hidden.

        In ringless mode the top control bar reserves space via the LAB layout
        margin, so legacy preview-box avoidance is skipped.
        """
        if not hasattr(self, 'lab_square') or not hasattr(self, 'preview_box'):
            return
        pb = self.preview_box
        ls = self.lab_square

        # ── Ringless mode: layout margin owns the top spacing ──
        if self.cfg.get("hideHueRing", False):
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return

        if pb.position_mode != "top-left" or not pb.isVisible():
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Map the preview box corners into LabSquare's local coordinate system.
        # QStackedWidget keeps every page at the same geometry as the stack
        # content rect, so this is valid even while the LAB pane is hidden.
        try:
            top_left = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().topLeft()))
            bottom_right = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().bottomRight()))
        except Exception:
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Only avoid if there is horizontal overlap with LabSquare.
        if bottom_right.x() <= 0 or top_left.x() >= ls.width():
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        scale = self.cfg.get("uiScale", 100) / 100.0
        pad = int(4 * scale)
        new_avoid_top = max(0, bottom_right.y() + pad)
        if callable(getattr(ls, "set_avoid_top", None)):
            ls.set_avoid_top(new_avoid_top)
        ls.avoid_top = new_avoid_top

    def on_wheel_color_changed(self, r, g, b):
        wm = self.color_wheel.wheel_mode
        src = {"triangle": "hsv", "hsv-square": "hsv", "hls-triangle": "hls",
               "rgb-slice": "rgb", "oklch-slice": "oklch"}.get(wm, "hsv")
        self._source_space = src
        # Pass the wheel's native HSV so companion-mode writes preserve hue
        # when saturation drops to ~0 (RGB→HSV loses hue for grayscale).
        h = self.color_wheel.h
        s = self.color_wheel.s
        v = self.color_wheel.v
        # In OKLCh mode, store the native LCH values as source so
        # memory-mode CSP sync uses the original OKLCh data directly.
        oklch_wheel = None
        if wm == "oklch-slice":
            cw = self.color_wheel
            if cw._oklch_L is not None and cw._oklch_C is not None and cw._oklch_h is not None:
                self._source_values = {"L": cw._oklch_L, "C": cw._oklch_C, "h": cw._oklch_h}
                oklch_wheel = (cw._oklch_L, cw._oklch_C, cw._oklch_h)
        self.update_ui_colors(r, g, b, source="wheel", hsv=(h, s, v),
                              oklch=oklch_wheel)

    def on_lab_square_color_changed(self, r, g, b):
        self._source_space = "lab"
        self._source_values = None  # LAB square uses RGB internally
        self.update_ui_colors(r, g, b, source="lab")

    def on_rgb_slider_changed(self):
        r = self.slider_widgets["R"][0].value()
        g = self.slider_widgets["G"][0].value()
        b = self.slider_widgets["B"][0].value()
        self._source_space = "rgb"
        self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
        self.update_ui_colors(r, g, b, source="sliders_rgb", hsv=(h_hsv, s_hsv, v_hsv))

    def on_hsv_slider_changed(self):
        h = self.slider_widgets["H_hsv"][0].value()
        s = self.slider_widgets["S_hsv"][0].value()
        v = self.slider_widgets["V_hsv"][0].value()
        self._source_space = "hsv"
        self._source_values = {"h": float(h), "s": float(s), "v": float(v)}
        r, g, b = hsv_to_rgb(h, s, v)
        self.update_ui_colors(r, g, b, source="sliders_hsv", hsv=(h, s, v))

    def on_hsl_slider_changed(self):
        h = self.slider_widgets["H_hsl"][0].value()
        l_raw = self.slider_widgets["L_hsl"][0].value()
        s_raw = self.slider_widgets["S_hsl"][0].value()
        self._source_space = "hls"
        self._source_values = {"h": float(h), "l": float(l_raw), "s": float(s_raw)}
        l_val = l_raw / 100.0
        s_val = s_raw / 100.0
        r, g, b = colorsys.hls_to_rgb(h / 360.0, l_val, s_val)
        h_hsv, s_hsv, v_hsv = hls_to_hsv_floats(h, l_val, s_val)
        self.update_ui_colors(int(r * 255), int(g * 255), int(b * 255), source="sliders_hsl", hsv=(h_hsv, s_hsv, v_hsv))

    def on_lab_slider_changed(self):
        l_val = self.slider_widgets["L_lab"][0].value()
        a_val = self.slider_widgets["a_lab"][0].value()
        b_val = self.slider_widgets["b_lab"][0].value()
        # Gamut-map (chroma reduction) instead of hard clipping: L* and hue
        # stay put while out-of-gamut a/b is pulled onto the sRGB boundary.
        # The mapped values are what the UI and the sync backends see.
        l_val, a_val, b_val = map_lab_to_gamut(l_val, a_val, b_val)
        self._source_space = "lab"
        self._source_values = {"l": float(l_val), "a": float(a_val), "b": float(b_val)}
        r, g, b = lab_to_rgb(l_val, a_val, b_val)
        r_clamped, g_clamped, b_clamped = clamp_rgb(r, g, b)
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        if s_hsv < 1.0 and hasattr(self, 'color_wheel'):
            h_hsv = self.color_wheel.h
        r_int = int(r_clamped)
        g_int = int(g_clamped)
        b_int = int(b_clamped)
        self.update_ui_colors(r_int, g_int, b_int, source="sliders_lab", hsv=(h_hsv, s_hsv, v_hsv))

    def on_oklab_slider_changed(self):
        sender = self.sender()
        l_raw = self.slider_widgets["L_oklab"][0].value()
        a_raw = self.slider_widgets["a_oklab"][0].value()
        b_raw = self.slider_widgets["b_oklab"][0].value()

        # ── L slider sync ──
        # Both L_oklab and L_oklch always show the same value.
        if sender == self.slider_widgets["L_oklab"][0]:
            # User dragged L_oklab → push to L_oklch and let the OKLCh
            # handler do the actual work (avoids double processing).
            self.slider_widgets["L_oklch"][0].setValue(l_raw)
            return
        else:
            # a or b changed → sync L_oklch silently
            self.slider_widgets["L_oklch"][0].blockSignals(True)
            self.slider_widgets["L_oklch"][0].setValue(l_raw)
            self.slider_widgets["L_oklch"][0].blockSignals(False)

        l_val = l_raw / 100.0
        a_val = a_raw / 100.0
        b_val = b_raw / 100.0
        # Gamut-map (chroma reduction) instead of hard clipping: L and the
        # a/b hue ray stay put while out-of-gamut a/b is pulled onto the
        # sRGB boundary. The mapped values are what the UI/sync use.
        l_val, a_val, b_val = map_oklab_to_gamut(l_val, a_val, b_val)
        self._source_space = "oklab"
        self._source_values = {"L": l_val, "a": a_val, "b": b_val}
        r, g, b = oklab_to_rgb(l_val, a_val, b_val)
        r_clamped, g_clamped, b_clamped = clamp_rgb(r, g, b)
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        if s_hsv < 1.0 and hasattr(self, 'color_wheel'):
            h_hsv = self.color_wheel.h
        self.update_ui_colors(int(r_clamped), int(g_clamped), int(b_clamped),
                              source="sliders_oklab", hsv=(h_hsv, s_hsv, v_hsv),
                              oklab=(l_val, a_val, b_val))
        self._deferred_dynamic_gradients_pending = True

    def on_oklch_slider_changed(self):
        # ── Init fraction-based target ──
        # _oklch_target_frac stores the user's desired chroma as a fraction
        # (0…1) of max_c at the current L/h for C-slider changes. L-slider
        # changes use the absolute C/H snapshot below instead.
        if not hasattr(self, "_oklch_target_frac") or not hasattr(self, "_oklch_target_h"):
            r, g, b = self.current_rgb
            L_tmp, C_tmp, h_tmp = rgb_to_oklch(r, g, b)
            max_c_init = self._find_oklch_max_chroma(L_tmp, h_tmp)
            self._oklch_target_frac = (C_tmp / max_c_init) if max_c_init > 0.001 else 0.0
            self._oklch_target_h = h_tmp

        sender = self.sender()
        l_slider = self.slider_widgets["L_oklch"][0]
        c_slider = self.slider_widgets["C_oklch"][0]
        h_slider = self.slider_widgets["h_oklch"][0]
        l_val = l_slider.value()
        c_raw = c_slider.value()
        h_val = h_slider.value()
        L = l_val / 100.0
        h_cur = float(h_val)

        frac = max(0.0, min(1.0, c_raw / _C_RAW_MAX))
        self._source_space = "oklch"

        if sender == c_slider:
            self._oklch_target_frac = frac
            source_str = "sliders_oklch_C"
        elif sender == h_slider:
            self._oklch_target_h = h_cur
            source_str = "sliders_oklch_h"
        else:
            source_str = "sliders_oklch_L"

        # L is a real OKLCh coordinate: while the fixed C/H value remains in
        # sRGB, moving L must not scale C with the boundary at the new L.
        # Once fixed C/H is outside sRGB, reduce C to the current boundary and
        # rebase the mask on that mapped OKLCh value.
        if source_str == "sliders_oklch_L":
            fixed_C = self._gamut_oklch_C
            fixed_h = self._gamut_oklch_h
            if fixed_C is None:
                fixed_C = getattr(self, "_oklch_target_C", None)
            if fixed_h is None:
                fixed_h = getattr(self, "_oklch_target_h", h_cur)
            if fixed_C is None or fixed_h is None:
                r, g, b = self.current_rgb
                _, current_C, current_h = rgb_to_oklch(r, g, b)
                fixed_C = current_C if fixed_C is None else fixed_C
                fixed_h = current_h if fixed_h is None else fixed_h

            fixed_C = max(0.0, float(fixed_C))
            fixed_h = float(fixed_h)
            max_c = self._find_oklch_max_chroma(L, fixed_h)
            display_C = min(fixed_C, max_c) if max_c > 0.001 else 0.0
            l_only_outside_gamut = fixed_C > max_c + 1e-6
            h_for_color = fixed_h
            source_C = display_C if l_only_outside_gamut else fixed_C
            self._source_values = {"L": L, "C": source_C, "h": fixed_h}
        else:
            h_for_color = h_cur
            max_c = self._find_oklch_max_chroma(L, h_for_color)
            C = frac * max_c
            display_C = min(C, max_c) if max_c > 0.001 else 0.0
            l_only_outside_gamut = False
            self._source_values = {"L": L, "C": display_C, "h": h_for_color}

        # 保存 OKLCh 滑块值——拖一个滑块绝不改变其他滑块的值
        saved_L = l_val
        saved_h = h_val

        r, g, b = oklch_to_rgb(L, display_C, h_for_color)
        r_clamped = max(0.0, min(255.0, r))
        g_clamped = max(0.0, min(255.0, g))
        b_clamped = max(0.0, min(255.0, b))
        if l_only_outside_gamut:
            # The boundary C is the actual OKLCh value after gamut mapping at
            # this L. Rebase all L masks on it immediately; the deferred
            # updater will repaint the grooves without blocking the drag.
            self._gamut_oklch_C = display_C
            self._gamut_oklch_h = h_for_color
            self._oklch_target_C = display_C
            h_rad = math.radians(h_for_color)
            self._gamut_oklab_a = display_C * math.cos(h_rad)
            self._gamut_oklab_b = display_C * math.sin(h_rad)
            _, self._gamut_lab_a, self._gamut_lab_b = rgb_to_lab(
                r_clamped, g_clamped, b_clamped)
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r_clamped, g_clamped, b_clamped)
        if s_hsv < 1.0:
            rr_eps, gg_eps, bb_eps = oklch_to_rgb(0.5, 0.02, h_for_color)
            h_hsv, _, _ = rgb_to_hsv(
                max(0.0, min(255.0, rr_eps)),
                max(0.0, min(255.0, gg_eps)),
                max(0.0, min(255.0, bb_eps)))
        self.update_ui_colors(int(r_clamped), int(g_clamped), int(b_clamped),
                              source=source_str, hsv=(h_hsv, s_hsv, v_hsv),
                              oklch=(L, display_C, h_for_color))

        self._deferred_dynamic_gradients_pending = True

        # 恢复非 source 滑块为原始值——okLch-picker 中拖 L 不会动 C/H
        # C/H handles remain unchanged; L-only mapping only changes the
        # internal absolute C used by the color and gamut mask.
        if sender != self.slider_widgets["L_oklch"][0]:
            self.slider_widgets["L_oklch"][0].blockSignals(True)
            self.slider_widgets["L_oklch"][0].setValue(saved_L)
            self.slider_widgets["L_oklch"][0].blockSignals(False)
        if sender != self.slider_widgets["h_oklch"][0]:
            self.slider_widgets["h_oklch"][0].blockSignals(True)
            self.slider_widgets["h_oklch"][0].setValue(saved_h)
            self.slider_widgets["h_oklch"][0].blockSignals(False)

        # Sync L_oklab to match the shared L value
        if "L_oklab" in self.slider_widgets:
            self.slider_widgets["L_oklab"][0].blockSignals(True)
            self.slider_widgets["L_oklab"][0].setValue(saved_L)
            self.slider_widgets["L_oklab"][0].blockSignals(False)

        # Update labels. L drags show the fixed/mapped absolute chroma.
        cur_max_c = getattr(self, '_cached_max_c', max_c)
        for chan in ("L_oklch", "C_oklch", "h_oklch", "L_oklab"):
            if chan not in self.slider_widgets:
                continue
            sl, lb = self.slider_widgets[chan]
            val = sl.value()
            if chan == "C_oklch":
                if source_str == "sliders_oklch_L":
                    display_c_val = display_C
                else:
                    display_c_val = (val / _C_RAW_MAX) * cur_max_c
                lb.setText(f"{display_c_val:.3f}")
            else:
                lb.setText(str(val))

    def _find_oklch_max_chroma(self, L, h):
        """Binary search for max OKLCh chroma at given L, h within sRGB gamut."""
        from ui.color_conversions import find_max_oklch_c
        return find_max_oklch_c(L, h)

    def _update_oklch_slider_gradients(self):
        """更新 OKLCh 三个滑块的背景渐变，从滑块直接读取当前值。

        对应 okLch-picker 的逻辑：
        - L 条: 固定 C 和 H，显示 L 从 0→100 的渐变
        - C 条: 固定 L 和 H，显示 C 从 0→max 的渐变
        - H 条: 固定 L 和 C，显示 H 从 0→360 的渐变
        拖动一个滑块时，另外两条的背景立即重绘。
        """
        # 从滑块读取当前值
        l_slider_val = self.slider_widgets["L_oklch"][0].value()
        c_slider_val = self.slider_widgets["C_oklch"][0].value()
        h_slider_val = self.slider_widgets["h_oklch"][0].value()
        L_cur = l_slider_val / 100.0
        h_cur = float(h_slider_val)

        from ui.color_conversions import find_max_oklch_c as _fmc
        # Compute max_c once for the normalized C slider and its label.
        max_c = self._find_oklch_max_chroma(L_cur, h_cur)
        # Proportional chroma: slider value = % of max_c
        frac = max(0.0, min(1.0, c_slider_val / _C_RAW_MAX))
        C_cur = frac * max_c

        fixed_C = getattr(self, "_gamut_oklch_C", None)
        fixed_h = getattr(self, "_gamut_oklch_h", None)
        if fixed_C is None or fixed_h is None:
            l_gradient_C = C_cur
            l_gradient_h = h_cur
            hue_gradient_C = C_cur
        else:
            # L and H gradients represent the actual current OKLCh color,
            # rather than recomputing C from the normalized slider position.
            l_gradient_C = max(0.0, float(fixed_C))
            l_gradient_h = float(fixed_h)
            hue_gradient_C = l_gradient_C

        # 16) L_oklch Slider — 固定 C 和 H，显示 L 从 0→1 的渐变
        # 每步 gamut-map C 确保轨槽始终显示色域内颜色
        c0 = min(l_gradient_C, _fmc(0.0, l_gradient_h))
        cm = min(l_gradient_C, _fmc(0.5, l_gradient_h))
        c1 = min(l_gradient_C, _fmc(1.0, l_gradient_h))
        okcl0_r, okcl0_g, okcl0_b = oklch_to_rgb(0.0, c0, l_gradient_h)
        okcl_mid_r, okcl_mid_g, okcl_mid_b = oklch_to_rgb(0.5, cm, l_gradient_h)
        okcl1_r, okcl1_g, okcl1_b = oklch_to_rgb(1.0, c1, l_gradient_h)
        self.slider_widgets["L_oklch"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okcl0_r))), int(max(0, min(255, okcl0_g))), int(max(0, min(255, okcl0_b))))),
            (0.5, QColor(int(max(0, min(255, okcl_mid_r))), int(max(0, min(255, okcl_mid_g))), int(max(0, min(255, okcl_mid_b))))),
            (1.0, QColor(int(max(0, min(255, okcl1_r))), int(max(0, min(255, okcl1_g))), int(max(0, min(255, okcl1_b)))))
        ])
        # L_oklch gamut mask: computed in the *deferred* path only
        # (_update_all_L_gamut_ranges).  Don't call _compute_oklch_L_gamut_range
        # here — its 48-iteration binary search is too expensive to run on
        # every mouse-move and kills drag responsiveness ("不跟手").
        # The ~16 ms deferred lag is acceptable; there is no flicker because
        # we simply never touch the mask in the synchronous path.

        # 17) C_oklch Slider — 固定 L 和 H
        # Dynamic upper limit via proportional mapping: the slider value
        # (0 .. _C_RAW_MAX) represents a *fraction* of the current max chroma.
        # So C = (value / _C_RAW_MAX) * max_c.  When L or h moves, max_c
        # changes, the slider position stays, but the computed chroma scales
        # proportionally — giving the "dynamic upper limit" behaviour without
        # ever changing the Qt slider range (which can cause instability
        # during a drag).
        c_slider = self.slider_widgets["C_oklch"][0]
        # Gradient fills the full bar: C=0 → C=max_c
        okcc0_r, okcc0_g, okcc0_b = oklch_to_rgb(L_cur, 0.0, h_cur)
        okcc1_r, okcc1_g, okcc1_b = oklch_to_rgb(L_cur, max_c, h_cur)
        c_slider.set_gradient([
            (0.0, QColor(int(max(0, min(255, okcc0_r))), int(max(0, min(255, okcc0_g))), int(max(0, min(255, okcc0_b))))),
            (1.0, QColor(int(max(0, min(255, okcc1_r))), int(max(0, min(255, okcc1_g))), int(max(0, min(255, okcc1_b))))),
        ])
        c_slider.clear_in_gamut_range()
        # Store for the label-update loop in on_oklch_slider_changed
        self._cached_max_c = max_c

        # 18) h_oklch Slider (色相 0-360) — 固定 L 和 C，显示 H 从 0→360 的渐变
        # 对应 okLch-picker 中 paintLcStrip 的逻辑
        okch_stops = []
        for i in range(7):
            hue = i * 60
            r_h, g_h, b_h = oklch_to_rgb(L_cur, hue_gradient_C, hue)
            okch_stops.append((i / 6.0, QColor(int(max(0, min(255, r_h))), int(max(0, min(255, g_h))), int(max(0, min(255, b_h))))))
        self.slider_widgets["h_oklch"][0].set_gradient(okch_stops)

    def _update_oklab_slider_gradients(self):
        """Update OKLab slider groove gradients synchronously.

        Mirrors _update_oklch_slider_gradients so that OKLab sliders
        get their coloured bars at the same instant as OKLCh sliders
        rather than trailing by one ~16 ms deferred frame.
        """
        if "a_oklab" not in self.slider_widgets or "b_oklab" not in self.slider_widgets:
            return
        if not self.slider_containers.get("OKLab", QWidget()).isVisible():
            return

        # Derive chromaticity from the current RGB colour (full float
        # precision) so the synchronous gradient matches the deferred
        # update_slider_gradients path pixel-for-pixel.
        r, g, b = self.current_rgb
        _, a_val, b_val = rgb_to_oklab(r, g, b)
        L_cur = self.slider_widgets["L_oklab"][0].value() / 100.0

        # L_oklab — fixed a, b, L varies 0→1
        okl0_r, okl0_g, okl0_b = oklab_to_rgb(0.0, a_val, b_val)
        okl_mid_r, okl_mid_g, okl_mid_b = oklab_to_rgb(0.5, a_val, b_val)
        okl1_r, okl1_g, okl1_b = oklab_to_rgb(1.0, a_val, b_val)
        self.slider_widgets["L_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okl0_r))), int(max(0, min(255, okl0_g))), int(max(0, min(255, okl0_b))))),
            (0.5, QColor(int(max(0, min(255, okl_mid_r))), int(max(0, min(255, okl_mid_g))), int(max(0, min(255, okl_mid_b))))),
            (1.0, QColor(int(max(0, min(255, okl1_r))), int(max(0, min(255, okl1_g))), int(max(0, min(255, okl1_b))))),
        ])

        # a_oklab — fixed L, b, a varies -0.4→0.4
        oka0_r, oka0_g, oka0_b = oklab_to_rgb(L_cur, -0.4, b_val)
        oka1_r, oka1_g, oka1_b = oklab_to_rgb(L_cur, 0.4, b_val)
        self.slider_widgets["a_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, oka0_r))), int(max(0, min(255, oka0_g))), int(max(0, min(255, oka0_b))))),
            (1.0, QColor(int(max(0, min(255, oka1_r))), int(max(0, min(255, oka1_g))), int(max(0, min(255, oka1_b))))),
        ])

        # b_oklab — fixed L, a, b varies -0.4→0.4
        okb0_r, okb0_g, okb0_b = oklab_to_rgb(L_cur, a_val, -0.4)
        okb1_r, okb1_g, okb1_b = oklab_to_rgb(L_cur, a_val, 0.4)
        self.slider_widgets["b_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okb0_r))), int(max(0, min(255, okb0_g))), int(max(0, min(255, okb0_b))))),
            (1.0, QColor(int(max(0, min(255, okb1_r))), int(max(0, min(255, okb1_g))), int(max(0, min(255, okb1_b))))),
        ])

    def _on_lab_lightness_changed(self, lightness):
        """Update LAB state and keep the existing low-quality drag path."""
        self.lab_square.set_lightness(
            lightness
        )

    def on_interaction_finished(self):
        self.color_wheel.schedule_slice_prewarm(350)
        if not self.lab_square.isVisible():
            self._schedule_lab_prerender(50)
        self.color_wheel.update()
        self.lab_square.update()
        r, g, b = self.current_rgb
        # Sync _oklch_target_frac from the actual (possibly clamped) color so
        # the L-slider mask reflects the real gamut. h is NOT synced here —
        # see comment below.
        if hasattr(self, '_oklch_target_frac'):
            L_okc, C_okc, h_okc = rgb_to_oklch(r, g, b)
            if self._source_space == "oklch" and self._source_values:
                L_okc = self._source_values.get("L", L_okc)
                C_okc = self._source_values.get("C", C_okc)
                h_okc = self._source_values.get("h", h_okc)
            max_c_sync = self._find_oklch_max_chroma(L_okc, h_okc)
            self._oklch_target_frac = (C_okc / max_c_sync) if max_c_sync > 0.001 else 0.0
            # Keep the native OKLCh values through an L-only release instead
            # of replacing them with quantized RGB round-trip values.
            self._oklch_target_C = C_okc
        # On drag release, cancel any pending deferred render and run the
        # heavy visual work synchronously so the settled color's groove
        # gradients + gamut masks are immediately consistent (rather than
        # trailing by one frame). During the drag these were deferred so the
        # slider handle / wheel indicator could paint first and stay glued
        # to the cursor.
        self._deferred_color_timer.stop()
        self._deferred_color_pending = None
        self.update_slider_gradients(r, g, b)
        if self._deferred_dynamic_gradients_pending:
            self._update_oklab_slider_gradients()
            self._update_oklch_slider_gradients()
            self._deferred_dynamic_gradients_pending = False
        # An L-only release must keep the chromaticity snapshots from before
        # the drag. Recomputing them from the quantized RGB would turn a
        # chromatic OKLCh color into a different (often gray) mask on release.
        if getattr(self, "_last_update_source", "") != "sliders_oklch_L":
            _, a_ok_snap, b_ok_snap = rgb_to_oklab(r, g, b)
            self._gamut_oklab_a = a_ok_snap
            self._gamut_oklab_b = b_ok_snap
            _, a_lb_snap, b_lb_snap = rgb_to_lab(r, g, b)
            self._gamut_lab_a = a_lb_snap
            self._gamut_lab_b = b_lb_snap
        _, c_ok_snap, h_ok_snap = rgb_to_oklch(r, g, b)
        if self._source_space == "oklch" and self._source_values:
            c_ok_snap = self._source_values.get("C", c_ok_snap)
            h_ok_snap = self._source_values.get("h", h_ok_snap)
        self._gamut_oklch_C = c_ok_snap
        self._gamut_oklch_h = h_ok_snap
        self._update_all_L_gamut_ranges()
        # Record into history before pushing to drawing software so the
        # persisted state reflects *what the user just settled on*.
        self._record_color_history()
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            hsv_override = None
            if self.sync_thread.software_mode == 'companion':
                entry_h = self.slider_widgets.get("H_hsv")
                entry_s = self.slider_widgets.get("S_hsv")
                entry_v = self.slider_widgets.get("V_hsv")
                if entry_h and entry_s and entry_v:
                    MAX = 4294967295
                    hsv_override = (int(entry_h[0].value()/360*MAX), int(entry_s[0].value()/100*MAX), int(entry_v[0].value()/100*MAX))
            # Source-space sync for CSP memory mode
            self._sync_wheel_source_values()
            src_sp, src_v = self._resolve_sync_source()
            self.sync_thread.write_color(r, g, b, hsv_u32=hsv_override,
                                         source_space=src_sp, source_values=src_v)

    def _sync_wheel_source_values(self):
        """If the source space originates from the wheel, pull live h/s/v from it."""
        src = self._source_space
        if src in ("hsv", "hls", "oklch"):
            try:
                wh = self.color_wheel.h
                ws = self.color_wheel.s
                wv = self.color_wheel.v
            except Exception:
                return
            if src == "hsv":
                self._source_values = {"h": wh, "s": ws, "v": wv}
            elif src == "hls":
                l_norm = max(0.0, min(1.0,
                    (2.0 - ws / 100.0) * wv / 200.0 if wv > 0.001 else 0.0))
                s_norm = 0.0
                if l_norm > 0.001 and l_norm < 0.999:
                    s_norm = (wv / 100.0 - l_norm) / min(l_norm, 1.0 - l_norm)
                self._source_values = {"h": wh, "l": l_norm * 100.0, "s": max(0.0, min(100.0, s_norm * 100.0))}
            elif src == "oklch":
                r_f, g_f, b_f = colorsys.hsv_to_rgb(wh / 360.0, ws / 100.0, wv / 100.0)
                L, C, h = rgb_to_oklch(r_f * 255.0, g_f * 255.0, b_f * 255.0)
                self._source_values = {"L": L, "C": C, "h": h}

    def _schedule_lab_gamut_range(self, delay_ms: int = 50):
        """Coalesce the expensive LAB gamut-range refresh during fast toggles."""
        if not hasattr(self, "lab_slider_column") or not self.lab_slider_column.isVisible():
            return
        self._lab_gamut_timer.start(delay_ms)

    def _schedule_lab_prerender(self, delay_ms: int = 50):
        """Coalesce LAB preview warmups without stacking timers."""
        if self.lab_square.isVisible():
            return
        self._lab_prerender_timer.start(delay_ms)

    def _prerender_lab(self):
        """Background pre-render of the LAB visualizer."""
        if not self.lab_square.isVisible() and hasattr(self, "stack"):
            self.lab_square.resize(self.stack.size())
            r, g, b = self.current_rgb
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_square.prerender()

    def _is_slider_drag_active(self):
        """Return True while any channel slider is held by the user."""
        for slider, _ in getattr(self, "slider_widgets", {}).values():
            if slider.isSliderDown():
                return True
        return bool(getattr(getattr(self, "lab_slider", None), "dragging", False))

    def _schedule_deferred_color_updates(self, r, g, b):
        """Schedule the heavy visual-only rendering (slider groove gradients
        + L out-of-gamut masks) to run on the next idle event-loop iteration.

        Why: these computations are not safety-critical and only affect the
        colored bars behind the other sliders. Calling them synchronously
        inside every drag step blocks the GUI thread and delays the dragged
        widget's own paint, so the handle/indicator stops tracking the cursor.
        By deferring and coalescing (only one pending run is ever armed), the
        handle/indicator paints flush first and the cosmetics trail by at
        most ~16ms. Latest (r,g,b) wins if multiple moves arrive before the
        timer fires.
        """
        self._deferred_color_pending = (r, g, b)
        if not self._deferred_color_timer.isActive():
            self._deferred_color_timer.start()

    def _apply_deferred_color_updates(self):
        """Run the deferred visual-only work, then clear the pending slot.

        Safe to call directly (used by on_interaction_finished to flush the
        final state synchronously); clears the pending rgb regardless.
        """
        pending = self._deferred_color_pending
        self._deferred_color_pending = None
        if pending is None:
            return
        r, g, b = pending
        self.update_slider_gradients(r, g, b)
        if self._deferred_dynamic_gradients_pending:
            self._update_oklab_slider_gradients()
            self._update_oklch_slider_gradients()
            self._deferred_dynamic_gradients_pending = False
        self._update_all_L_gamut_ranges()

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
            okl_mid_r, okl_mid_g, okl_mid_b = oklab_to_rgb(0.5, a_oklab, b_oklab)
            okl1_r, okl1_g, okl1_b = oklab_to_rgb(1.0, a_oklab, b_oklab)
            self.slider_widgets["L_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okl0_r))), int(max(0, min(255, okl0_g))), int(max(0, min(255, okl0_b))))),
                (0.5, QColor(int(max(0, min(255, okl_mid_r))), int(max(0, min(255, okl_mid_g))), int(max(0, min(255, okl_mid_b))))),
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
            self._update_oklch_slider_gradients()

    # ── L-gamut range helpers for out-of-gamut slider marking ──

    def _compute_lab_L_gamut_range(self):
        """Return (min_L, max_L) for L_lab at the snapshot LAB chromaticity."""
        if "a_lab" not in self.slider_widgets or "b_lab" not in self.slider_widgets:
            return 0, 100
        a_fixed = getattr(self, '_gamut_lab_a', None)
        b_fixed = getattr(self, '_gamut_lab_b', None)
        if a_fixed is None or b_fixed is None:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = lab_to_rgb(L, a_fixed, b_fixed)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

    @staticmethod
    def _resolve_content_height(current_height, required_height,
                                last_auto_height, manual_override):
        required_height = max(1, int(required_height))
        current_height = int(current_height)
        if current_height < required_height:
            return required_height, False
        if (manual_override and last_auto_height is not None
                and required_height == int(last_auto_height)):
            return current_height, True
        return required_height, False

    @staticmethod
    def _required_content_height(title_height, stack_min_height, sliders_height,
                                 margins_top, margins_bottom, spacing):
        return max(
            240,
            int(title_height) + int(stack_min_height) + int(sliders_height)
            + int(margins_top) + int(margins_bottom) + 2 * int(spacing),
        )

    @staticmethod
    def _required_visualizer_height(window_width, margins_left, margins_right,
                                    stack_min_height):
        available_width = int(window_width) - int(margins_left) - int(margins_right)
        return max(available_width, int(stack_min_height))

    def _run_deferred_content_height(self):
        self._content_height_adjust_pending = False
        self._adjust_content_height()

    def _adjust_content_height(self):
        if getattr(self, "_adjusting_content_height", False):
            return
        if not self.isVisible():
            if not self._content_height_adjust_pending:
                self._content_height_adjust_pending = True
                self._content_height_timer.start(0)
            return
        self._content_height_adjust_pending = False
        required = 0
        try:
            self.sliders_layout.activate()
            self.main_layout.activate()
            margins = self.main_layout.contentsMargins()
            visualizer_h = self._required_visualizer_height(
                self.width(),
                margins.left(),
                margins.right(),
                max(
                    self.stack.minimumSizeHint().height(),
                    self.stack.minimumHeight(),
                ),
            )
            required = self._required_content_height(
                self.title_bar.sizeHint().height(),
                visualizer_h,
                self.sliders_container.sizeHint().height(),
                margins.top(), margins.bottom(), self.main_layout.spacing(),
            )
        except AttributeError:
            return

        self.setMinimumHeight(required)
        target, manual = self._resolve_content_height(
            self.height(), required, self._last_auto_height,
            self._manual_height_override,
        )
        self._last_auto_height = required
        self._manual_height_override = manual
        if target == self.height():
            return

        self._adjusting_content_height = True
        try:
            self.resize(self.width(), target)
        finally:
            self._adjusting_content_height = False

    @staticmethod
    def _compute_L_gamut_range(in_gamut):
        """Shared binary search: find [min_L, max_L] of in-gamut L values.

        Does NOT assume L=50 is in gamut — high-chroma colours near the
        gamut boundary can push mid-L out of gamut while low/high L
        remain valid.  Scans for any in-gamut reference point first,
        then searches outward in both directions from it.
        """
        # ── Find any in-gamut reference L ──
        ref_L = None
        for test_L in (0.0, 25.0, 50.0, 75.0, 100.0):
            if in_gamut(test_L):
                ref_L = test_L
                break
        if ref_L is None:
            return 0, 100  # colour is unreachable at this chromaticity

        # ── min_L: lowest in-gamut L ──
        if in_gamut(0.0):
            min_L = 0.0
        else:
            lo, hi = 0.0, ref_L
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    hi = mid
                else:
                    lo = mid
            min_L = hi

        # ── max_L: highest in-gamut L ──
        if in_gamut(100.0):
            max_L = 100.0
        else:
            lo, hi = ref_L, 100.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    lo = mid
                else:
                    hi = mid
            max_L = lo

        return int(round(min_L)), int(round(max_L))

    def _compute_oklab_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklab at the snapshot OKLab chromaticity."""
        if "a_oklab" not in self.slider_widgets or "b_oklab" not in self.slider_widgets:
            return 0, 100
        a_fixed = getattr(self, '_gamut_oklab_a', None)
        b_fixed = getattr(self, '_gamut_oklab_b', None)
        if a_fixed is None or b_fixed is None:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = oklab_to_rgb(L / 100.0, a_fixed, b_fixed)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

    def _compute_oklch_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklch at the snapshot chromaticity."""
        if "C_oklch" not in self.slider_widgets or "h_oklch" not in self.slider_widgets:
            return 0, 100
        if "L_oklch" not in self.slider_widgets:
            return 0, 100
        c_val = self._gamut_oklch_C
        h_val = self._gamut_oklch_h
        if c_val is None or h_val is None or c_val < 0.001:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = oklch_to_rgb(L / 100.0, c_val, h_val)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

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

    def update_ui_colors(self, r, g, b, source="", hsv=None, oklch=None, oklab=None):
        self._last_update_source = source
        self.current_rgb = (r, g, b)
        color = QColor(r, g, b)

        # 1) Sync swatches based on active slot
        if self.active_slot == "fg":
            self.preview_box.fg_color = color
        else:
            self.preview_box.bg_color = color
        self.preview_box.update_slot_borders(self.active_slot)

        # Persist source to the active slot (skip for slot_change/swap — those
        # restore, not overwrite, the per-slot source).
        if source not in ("slot_change", "swap") and self._source_values:
            if self.active_slot == "fg":
                self._fg_source_space = self._source_space
                self._fg_source_values = self._source_values
            else:
                self._bg_source_space = self._source_space
                self._bg_source_values = self._source_values

        # 2) Sync Color Wheel (Only if visible or during init)
        if source == "init" or (source != "wheel" and self.color_wheel.isVisible()):
            if hsv is not None:
                self.color_wheel.set_hsv(hsv[0], hsv[1], hsv[2])
            else:
                self.color_wheel.set_color(r, g, b, block_signals=True)
            # Push direct OKLCh state so the indicator avoids HSV→RGB→OKLCh drift
            if self.color_wheel.wheel_mode == "oklch-slice":
                if oklch is not None:
                    L_ok, C_ok, h_ok = oklch
                else:
                    L_ok, C_ok, h_ok = rgb_to_oklch(r, g, b)
                self.color_wheel.set_oklch(L_ok, C_ok, h_ok)

        # 3) Sync LAB Square / Slider (Only if visible or during init)
        if source == "init" or (source != "lab" and self.lab_square.isVisible()):
            if oklab is not None and self.lab_square.render_mode == "oklab":
                L_ok, a_ok, b_ok = oklab
                self.lab_square.set_oklab(L_ok, a_ok, b_ok, block_signals=True)
            else:
                self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(
                self.lab_square.L
            )

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
            self.slider_widgets["S_hsv"][0].setValue(round(s_hsv))
            self.slider_widgets["V_hsv"][0].setValue(round(v_hsv))
            if s_hsv >= 0.5:
                self.slider_widgets["H_hsv"][0].setValue(round(h_hsv))
        
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
            if "L_oklab" in self.slider_widgets:
                self.slider_widgets["L_oklab"][0].setValue(round(L_ok * 100))
            self.slider_widgets["a_oklab"][0].setValue(round(a_ok * 100))
            self.slider_widgets["b_oklab"][0].setValue(round(b_ok * 100))
        
        # OKLCh Values
        if source not in ("sliders_oklch_L", "sliders_oklch_C", "sliders_oklch_h"):
            # Use direct OKLCh values when available (avoids RGB round-trip drift)
            if oklch is not None:
                L_okc, C_okc, h_okc = oklch
            else:
                L_okc, C_okc, h_okc = rgb_to_oklch(r, g, b)
                # Preserve hue when chroma drops to ~0 (achromatic) or when
                # lightness is very low (<5%) — both cases produce near-black
                # RGB where atan2(0,0) makes h jump to 0°.
                if C_okc < 0.002 or L_okc < 0.05:
                    h_okc = self._oklch_target_h
                    if C_okc < 0.002:
                        C_okc = 0.0
            self._oklch_target_h = h_okc
            # Store fraction: use 0.32 (sRGB absolute max chroma) as reference
            # to avoid calling _find_oklch_max_chroma (16-iteration binary
            # search) on every wheel drag.  The deferred gradient update will
            # refine the fraction with the exact max_c within ~16 ms.
            max_c_ref = 0.32
            self._oklch_target_frac = min(1.0, C_okc / max_c_ref) if max_c_ref > 0.001 else 0.0
            self.slider_widgets["L_oklch"][0].setValue(round(L_okc * 100))
            self.slider_widgets["h_oklch"][0].setValue(round(h_okc))
            if self.slider_containers.get("OKLCh", QWidget()).isVisible():
                # Slider value = fraction * full range
                c_slider_val = int(round(self._oklch_target_frac * _C_RAW_MAX))
                c_sl = self.slider_widgets["C_oklch"][0]
                c_sl.blockSignals(True)
                c_sl.setValue(c_slider_val)
                c_sl.blockSignals(False)
        else:
            L_okc, C_okc, h_okc = rgb_to_oklch(r, g, b)
            if source != "sliders_oklch_L":
                self.slider_widgets["L_oklch"][0].setValue(round(L_okc * 100))
            # h slider: do NOT update from RGB round-trip when adjusting L or C.
            # h only changes when the user drags the h slider itself — matching
            # HSV's behavior where H stays fixed while adjusting S/V.
            if source != "sliders_oklch_C":
                if self.slider_containers.get("OKLCh", QWidget()).isVisible():
                    # Keep C slider at its fraction-based position (proportional)
                    if hasattr(self, "_oklch_target_frac"):
                        c_val = int(round(self._oklch_target_frac * _C_RAW_MAX))
                        c_sl = self.slider_widgets["C_oklch"][0]
                        c_sl.blockSignals(True)
                        c_sl.setValue(c_val)
                        c_sl.blockSignals(False)
        
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(False)

        # Update labels and gradient stylesheets
        for chan in all_chans:
            if chan in self.slider_widgets:
                val = self.slider_widgets[chan][0].value()
                if chan == "C_oklch":
                    if source == "sliders_oklch_L" and self._source_values:
                        display_c = self._source_values.get("C", 0.0)
                    else:
                        # C slider remains normalized to the current boundary.
                        cur_max = getattr(self, '_cached_max_c', 0.32)
                        display_c = (val / _C_RAW_MAX) * cur_max
                    self.slider_widgets[chan][1].setText(f"{display_c:.3f}")
                else:
                    self.slider_widgets[chan][1].setText(str(val))
            
        # ── Gamut-range chromaticity snapshots ─────
        # L-only drags keep all snapshots unchanged until release.
        if source != "sliders_oklch_L":
            _, a_ok, b_ok = rgb_to_oklab(r, g, b)
            self._gamut_oklab_a = a_ok
            self._gamut_oklab_b = b_ok
            _, a_lb, b_lb = rgb_to_lab(r, g, b)
            self._gamut_lab_a = a_lb
            self._gamut_lab_b = b_lb
            if oklch is not None:
                _, c_ok, h_ok = oklch
            else:
                _, c_ok, h_ok = rgb_to_oklch(r, g, b)
            self._gamut_oklch_C = c_ok
            self._gamut_oklch_h = h_ok

        # Heavy visual-only cosmetics (slider groove gradients + L out-of-gamut
        # masks) are deferred + coalesced so they never block the dragged
        # widget's paint. This is what keeps every slider handle and the color
        # wheel indicator perfectly following the cursor on every mouse move;
        # the colored groove bars / grayed gamut regions trail by ≤~16ms.
        self._schedule_deferred_color_updates(r, g, b)

        # 5) Push to drawing software
        if source != "sync" and hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            is_dragging = False
            if source.startswith("sliders_"):
                for chan, (slider, _) in self.slider_widgets.items():
                    if slider.isSliderDown():
                        is_dragging = True
                        break
            if not is_dragging:
                hsv_ov = None
                if self.sync_thread.software_mode == 'companion':
                    _U32 = 4294967295
                    if hsv is not None and len(hsv) == 3:
                        hsv_ov = (round(hsv[0]/360*_U32),
                                  round(hsv[1]/100*_U32),
                                  round(hsv[2]/100*_U32))
                    else:
                        # Fallback: wheel HSV was already updated in step 2 above.
                        # This preserves hue when RGB→HSV would lose it (grayscale).
                        hsv_ov = (round(self.color_wheel.h/360*_U32),
                                  round(self.color_wheel.s/100*_U32),
                                  round(self.color_wheel.v/100*_U32))
                src_sp, src_v = self._resolve_sync_source()
                self.sync_thread.write_color(r, g, b, hsv_u32=hsv_ov,
                                             source_space=src_sp, source_values=src_v)

    def _resolve_sync_source(self):
        """Return (space_name, values) for CSP memory-mode sync.

        Only spaces in SPACE_ORDER (rgb/cmyk/hsv/hls) are passed directly;
        lab/oklab/oklch are converted to float RGB.
        """
        src = self._source_space
        vals = self._source_values
        if not src or not vals:
            return (None, None)
        if src in ("rgb", "cmyk", "hsv", "hls"):
            return (src, vals)
        # Fallback: convert non-SPACE_ORDER sources to float RGB
        try:
            if src == "lab":
                r, g, b = lab_to_rgb(vals["l"], vals["a"], vals["b"])
            elif src == "oklab":
                r, g, b = oklab_to_rgb(vals["L"], vals["a"], vals["b"])
            elif src == "oklch":
                r, g, b = oklch_to_rgb(vals["L"], vals["C"], vals["h"])
            else:
                return (None, None)
            rgb = {"r": max(0.0, min(255.0, r)),
                   "g": max(0.0, min(255.0, g)),
                   "b": max(0.0, min(255.0, b))}
            return ("rgb", rgb)
        except Exception:
            return (None, None)

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
        
        # Calculate visualizer wheel size from the width, but never taller
        # than the visualizer pane: a short/wide window (manual resize)
        # shrinks the wheel instead of clipping its lower arc.  Mirrors the
        # clamp in ColorWheel.get_wheel_geometry().
        spacing = int(4 * dynamic_scale)
        pane_h = h - 4 - title_h - sliders_h - 2 * spacing
        wheel_size = min(w - int(16 * dynamic_scale), max(16, pane_h - 6))
        
        # ── Step 1: legacy preview sizing ALWAYS runs first ──
        # This restores legacy circle sizing/position when ringless is disabled,
        # and provides a baseline that ringless may override below.
        self.preview_box.resize_and_position(wheel_size, title_h, h, sliders_h, self.active_slot)
        self.preview_box.raise_()

        # ── Step 2: push DPI-scaled button metrics down; panes do the rest ──
        btn_size = int(28 * dynamic_scale)
        btn_margin = int(6 * dynamic_scale)
        if hasattr(self, 'pane_wheel'):
            self.pane_wheel.set_mode_button_metrics(btn_size, btn_margin)
        if hasattr(self, 'pane_lab'):
            self.pane_lab.set_mode_button_metrics(btn_size, btn_margin)

        # ── Step 3: ringless layout sync ──
        # On wheel page: applies rectangle sizing over the legacy baseline.
        # On LAB page: same rectangle controls + top bar, with layout margin.
        # Do NOT call _adjust_content_height() from resize-driven sync.
        self._sync_ringless_mode(wheel_size=wheel_size, title_bar_height=title_h)
        self.color_wheel.schedule_slice_prewarm(500)

        # ── Step 4: LAB avoidance observes FINAL preview geometry ──
        # Must run AFTER ringless sync so it sees the page-appropriate
        # (ringless rectangles or restored legacy circles) size/position.
        # In ringless mode the guard inside _update_lab_avoid skips legacy
        # avoidance because the LAB layout margin owns the top spacing.
        self._update_lab_avoid()

        # ── Step 5: settings window positioning (independent window) ──
        if hasattr(self, 'settings_window') and self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.position_near_main_window()

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
                start_pos = self.resize_start_pos
                geom = self.resize_start_geometry
                if start_pos is None or geom is None:
                    return
                delta = event.globalPosition().toPoint() - start_pos
                new_geom = QRect(geom)
                
                min_w = 200
                min_h = 300
                
                resize_dir = self.resize_dir
                if resize_dir is None:
                    return
                
                if "right" in resize_dir:
                    new_w = max(min_w, geom.width() + delta.x())
                    new_geom.setWidth(new_w)
                elif "left" in resize_dir:
                    new_w = max(min_w, geom.width() - delta.x())
                    new_geom.setLeft(geom.right() - new_w)
                    
                if "bottom" in resize_dir:
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
            self._manual_height_override = True
            self._last_auto_height = self.height()
        
        super().mouseReleaseEvent(event)

    def _is_lab_toggle_zone(self) -> bool:
        """True when the cursor is inside the visible picker pane.

        Covers the color wheel (the ring AND every position inside it) plus
        the LAB visualizer pane, so the local shortcut toggles in both
        directions without moving the mouse.
        """
        try:
            if self.stack.currentIndex() == 0 and self.color_wheel.isVisible():
                pos = self.color_wheel.mapFromGlobal(QCursor.pos())
                return self.color_wheel.rect().contains(pos)
            if self.stack.currentIndex() == 1 and self.pane_lab.isVisible():
                pos = self.pane_lab.mapFromGlobal(QCursor.pos())
                return self.pane_lab.rect().contains(pos)
        except Exception:
            pass
        return False

    def _maybe_handle_lab_toggle_key(self, event) -> bool:
        """Handle the configured local LAB-toggle shortcut.

        Toggles wheel/LAB when the cursor is over the picker pane. When the
        cursor is elsewhere the key is still consumed (unless a text field
        has focus), so the toggle key can never re-activate a previously
        focused button/checkbox — clicking ☰ once used to make Space toggle
        the settings panel.

        Only covers key events Qt delivers to this app (i.e. a Colorink
        window has focus). Without focus — 无焦点选色模式, drawing in the
        painting app — the system-wide hook registered in
        ``update_hotkey_bindings`` takes over (see ``on_hotkey_triggered``).

        Returns True when the event was consumed, False to pass it through
        (non-matching key, or a text field has focus). The event filter
        skips this entirely while a settings hotkey capture is active.
        """
        pressed = parse_key_event(event)
        if not pressed:
            return False
        expected = str(self.cfg.get("toggleLabKey", "")).lower().replace(" ", "")
        if pressed.lower().replace(" ", "") != expected:
            return False
        # Never steal the key from a text field (e.g. the Companion URL input).
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return False
        if self._is_lab_toggle_zone():
            self.toggle_picker_mode()
        return True

    def _maybe_handle_lab_toggle_mouse(self, event) -> bool:
        """Toggle wheel/LAB view when the configured local shortcut is a
        mouse button pressed over the picker pane.

        Mouse events follow the cursor, not keyboard focus, so this path
        works even in 无焦点选色模式 while the painting app holds focus.
        """
        if not self._is_lab_toggle_zone():
            return False
        name = MOUSE_BUTTON_NAME_BY_QT.get(event.button())
        if not name:
            return False
        expected = str(self.cfg.get("toggleLabKey", "")).lower().replace(" ", "")
        if name.lower() != expected:
            return False
        self.toggle_picker_mode()
        return True

    def eventFilter(self, watched, event):
        try:
            # Local LAB-toggle shortcuts (keyboard or mouse button) fire while
            # the cursor is over the color wheel / LAB pane. Skipped while a
            # settings hotkey capture is active so the recorded press wins.
            if not capture_active():
                if event.type() == QEvent.Type.KeyPress and self._maybe_handle_lab_toggle_key(event):
                    return True
                if event.type() == QEvent.Type.MouseButtonPress and self._maybe_handle_lab_toggle_mouse(event):
                    return True
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
            row.setSpacing(max(1, int(1 * scale))) # Keep the slider close to its channel label
            
        # Adjust label fixed widths (theme-aware)
        ch_w_factor = float(cast(float, slider_theme["channel_label_width_factor"]))
        for chan, label in getattr(self, "slider_labels", {}).items():
            label.setFixedWidth(max(8, int(12 * scale * ch_w_factor)))

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
        elif theme_name == "eyedropper":
            bar_stored = self.cfg.get("uiThemeDropperColorBar", "#787878")
            bg_stored = self.cfg.get("uiThemeDropperColorBg", "#b2b2b2")
            try:
                c_bar = QColor(bar_stored)
                bg = QColor(bg_stored).name()
                barBg = c_bar.name()
                border_color = c_bar.name()
                text = "#ffffff" if QColor(bg).lightness() < 128 else "#222222"
            except Exception:
                bg = barBg = border_color = "#787878"
                text = "#222222"
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

        if hasattr(self, "preview_box"):

            self.preview_box.set_theme_colors(

                RINGLESS_ACTIVE_BORDER, borderColor

            )

        
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
        
        # Style value labels directly for robust rendering (theme-aware)
        val_w_factor = float(cast(float, slider_theme["value_label_width_factor"]))
        val_radius = max(0, int(3 * scale * float(cast(float, slider_theme["value_label_radius_factor"]))))
        val_padding = slider_theme["value_label_padding"]
        for chan, (slider, val_label) in self.slider_widgets.items():
            val_label.setFixedWidth(max(24, int(27 * val_w_factor)))
            val_label.setStyleSheet(f"""
                background-color: {inputBg};
                border: 1px solid {borderColor};
                border-radius: {val_radius}px;
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {val_font_size}px;
                padding: {val_padding};
                padding-right: 10px;
            """)
            
        # Scale GradientSliders (theme-aware)
        for chan, (slider, val_label) in self.slider_widgets.items():
            if isinstance(slider, GradientSlider):
                slider.update_scale(scale, slider_theme)
            
        # Style mode buttons dynamically
        btn_w = int(28 * scale)
        btn_h = int(28 * scale)
        for btn in [self.btn_mode_wheel, self.btn_mode_lab,
                     getattr(self, 'btn_module', None)]:
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

        if not is_resize_event:
            self._adjust_content_height()

    def init_hotkeys(self):
        # Register global hotkeys from config
        global_hotkeys.hotkey_signals.triggered.connect(self.on_hotkey_triggered)
        self.update_hotkey_bindings()

    def update_hotkey_bindings(self):
        global_hotkeys.unbind_all()
        # Global hotkeys may be bound to a keyboard key or a mouse button —
        # route each value to the matching system hook (mouse hotkeys are
        # not suppressed, so the app under the cursor still gets the click).
        for hotkey_type in ("pickKey", "hideWindowKey", "followMouseKey",
                            "grayscaleFilterKey", "toggleLabGlobalKey"):
            value = cast(str, self.cfg.get(hotkey_type))
            if is_mouse_hotkey(value):
                global_hotkeys.bind_mouse_hotkey(hotkey_type, value)
            else:
                global_hotkeys.bind_hotkey(hotkey_type, value)
        # The local LAB-toggle key is bound as a system-wide hook too, so it
        # works while focus is in the drawing app (无焦点选色模式). Mouse
        # buttons need no hook — the event filter sees them by cursor
        # position — so only keyboard values are bound here.
        lab_toggle_key = cast(str, self.cfg.get("toggleLabKey"))
        if not is_mouse_hotkey(lab_toggle_key):
            global_hotkeys.bind_hotkey("toggleLabKey", lab_toggle_key)

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
        elif hotkey_type == "toggleLabKey":
            # System-wide hook path: the Qt event filter already consumed the
            # key when a Colorink window has focus. Without focus — e.g. while
            # drawing in CSP with 无焦点选色模式 — this hook is the only path,
            # and the mouse-over-wheel gate still applies.
            if QApplication.activeWindow() is not None:
                return  # handled by the Qt key path
            if self._is_lab_toggle_zone():
                print("[Hotkeys] Toggle LAB view (local, no-focus)")
                self.toggle_picker_mode()
        elif hotkey_type == "toggleLabGlobalKey":
            print("[Hotkeys] Toggle LAB view (global)")
            self.toggle_picker_mode()
        elif hotkey_type == "pickKey":
            if self.picker_overlay.is_active:
                self.picker_overlay.stop()
            else:
                self.picker_overlay.start()
                print("[Hotkeys] Global Color Picker activated")
        elif hotkey_type == "grayscaleFilterKey":
            print("[Hotkeys] Grayscale Filter toggled")
            try:
                result = self.grayscale_overlay.toggle()
                # Backends return False + last_error on failure — show it
                # clearly instead of silently switching modes.
                if result is False and hasattr(self.grayscale_overlay, 'last_error'):
                    err = getattr(self.grayscale_overlay, "last_error", "")
                    if err:
                        from PyQt6.QtWidgets import QMessageBox
                        QMessageBox.warning(self, "黑白滤镜", err)
            except Exception as e:
                print(f"[Hotkeys] Grayscale toggle error: {e}")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "黑白滤镜", f"切换失败: {e}")

    def _on_picker_color_picked(self, r, g, b):
        """Handle color picked from the global magnifier overlay."""
        self.current_rgb = (r, g, b)
        self._source_space = "rgb"
        self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        self._record_color_history()
        self.update_ui_colors(r, g, b)
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            self.sync_thread.write_color(r, g, b, source_space="rgb",
                                         source_values={"r": float(r), "g": float(g), "b": float(b)})
            print(f"[Picker] Picked color RGB({r}, {g}, {b})")
    def init_memory_sync(self):
        # Start background memory syncing thread
        self.sync_thread = memory_sync.MemorySyncThread(self)
        self.sync_thread.signals.color_changed.connect(self.on_external_color_changed)
        self.sync_thread.signals.status_changed.connect(self.on_sync_status_changed)
        self.sync_thread.signals.error_changed.connect(self.on_sync_error_changed)
        self._sync_error = None
        self._ps_perm_prompted = False
        
        # Set active software mode
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps", "companion"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        setattr(self.sync_thread, "ps_version", self.cfg.get("psVersion", "auto"))
        self.sync_thread.update_versions()
        
        # Start syncing
        self.sync_thread.start()

    @pyqtSlot(int, int, int)
    def on_external_color_changed(self, r, g, b):
        # Drawing software (CSP/SAI/UDM/PS) color changed — e.g. the user
        # Alt-picked a new color. Mirror _on_picker_color_picked: update
        # current_rgb first (read by _record_color_history), then push the
        # new color into the history widget before refreshing the UI.
        # record() collapses consecutive duplicates, so continuous live
        # slider drags in the drawing software won't flood the history.
        self.current_rgb = (r, g, b)
        hsv_direct = None
        if hasattr(self, 'sync_thread'):
            chsv = getattr(self.sync_thread, 'companion_hsv', None)
            if chsv is not None and self.sync_thread.software_mode == 'companion':
                hsv_direct = chsv
        self._record_color_history()
        self.update_ui_colors(r, g, b, source="sync", hsv=hsv_direct)

    @pyqtSlot(str, bool)
    def on_sync_status_changed(self, mode, connected):
        print(f"[Sync] Software status changed: {mode} -> connected={connected}")
        self._sync_status = (mode, connected)
        # Optionally update title bar text or border to show connection status
        mode_display = {"csp": "CSP", "sai": "SAI", "udm": "UDM", "ps": "PS", "companion": "手机"}.get(mode, mode.upper())
        status_text = f"Colorink ({mode_display} {'✓' if connected else '×'})"
        self.title_bar.title_label.setText(status_text)
        if mode == "companion" and hasattr(self, 'settings_sidebar'):
            self.settings_sidebar._refresh_companion_status()
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar._refresh_sync_status()

    @pyqtSlot(str, str, bool)
    def on_sync_error_changed(self, mode, error, permission_issue):
        """Show *why* the sync backend failed to connect (e.g. Photoshop)."""
        self._sync_error = (mode, error, permission_issue) if error else None
        if hasattr(self, 'title_bar'):
            self.title_bar.title_label.setToolTip(error if error else "")
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar._refresh_sync_status()
        # A UAC integrity mismatch is self-fixable: offer to relaunch
        # Colorink elevated. Prompt once per session to avoid nagging.
        if mode == 'ps' and permission_issue and not self._ps_perm_prompted:
            self._ps_perm_prompted = True
            self._prompt_relaunch_as_admin()

    def _prompt_relaunch_as_admin(self):
        from PyQt6.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, "需要管理员权限",
            "检测到 Photoshop 可能以管理员身份运行，而 Colorink 权限不足，"
            "无法通过 COM 连接。\n\n"
            "是否以管理员身份重启 Colorink？\n"
            "（如果 Photoshop 是绿色版 / 未正常安装，提权也无法解决）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._relaunch_as_admin()

    def _relaunch_as_admin(self):
        """Restart the app elevated via ShellExecute(runas); exit current."""
        import ctypes
        exe = sys.executable
        args = " ".join(
            f'"{a}"' if " " in a else a for a in sys.argv[1:]
        )
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, args, os.getcwd(), 1
            )
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提权失败", f"无法以管理员身份启动: {exc}")
            return
        if ret <= 32:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提权失败", f"无法以管理员身份启动 (错误码 {ret})")
            return
        # ShellExecute returned OK — this instance hands over and exits.
        os._exit(0)

    def _setup_companion_connection(self):
        if not hasattr(self, 'sync_thread'):
            return
        from core.csp_companion_sync import CSPCompanionSync
        ok = CSPCompanionSync.show_setup_dialog(self)
        if ok:
            c = self.sync_thread.companion_sync
            c._load_session()
            self.title_bar.title_label.setText("Colorink (手机 — 连接中...)")
        if hasattr(self, 'settings_sidebar'):
            self.settings_sidebar._refresh_companion_status()

    def toggle_settings_sidebar(self):
        # Lazy-create the independent settings window on first use
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if self.settings_window.isVisible():
            self.settings_window.hide()
        else:
            self.settings_sidebar.refresh_ui()
            self.settings_window.show_near_main_window()
        self.update_no_focus_policies()

    def _schedule_module_layout_refresh(self):
        """Coalesce slider reordering and geometry work during rapid module clicks."""
        self._module_layout_refresh_pending = True
        if not self._module_layout_timer.isActive():
            self._module_layout_timer.start(0)

    def _flush_module_layout_refresh(self):
        if not self._module_layout_refresh_pending:
            return
        self._module_layout_refresh_pending = False
        self.refresh_slider_visibility_and_order()

    def _schedule_module_config_save(self):
        """Batch config writes so a click burst performs one disk write."""
        self._module_save_pending = True
        self._module_save_timer.start(120)

    def _flush_module_config_save(self):
        if not self._module_save_pending:
            return
        self._module_save_pending = False
        config.save_hotkey_config(self.cfg)

    def _apply_module(self, module_name: str):
        """Apply a color-space module without blocking the click handler."""
        if module_name not in _MODULE_DEFS:
            module_name = "hsv"
        self._current_module = module_name
        self.cfg["colorSpaceModule"] = module_name
        # Persist and reflow on coalesced timers: rapid clicks update the wheel
        # and button immediately, while one final layout pass handles the burst.
        self._schedule_module_config_save()
        # Update wheel mode
        wheel_mode = _MODULE_DEFS[module_name]["wheel"]
        self.color_wheel.set_wheel_mode(wheel_mode)
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_module_layout_refresh()
        # Update module button label
        self._update_module_button_label()
        # Notify sidebar if it's open
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar.notify_module_changed()

    def _next_module(self):
        """Cycle to the next module in _MODULE_ORDER."""
        try:
            idx = _MODULE_ORDER.index(self._current_module)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(_MODULE_ORDER)
        self._apply_module(_MODULE_ORDER[next_idx])

    def refresh_slider_visibility_and_order(self):
        # Remove all from layout
        for group in config.SLIDER_GROUPS:
            self.sliders_layout.removeWidget(self.slider_containers[group])

        # Display order comes from the same shared helper the settings UI
        # uses, so reordering there always matches this layout.
        groups = config.sorted_slider_groups(self.cfg)

        # Module-aware filtering: only the current module's slider set is
        # eligible; force-hide everything outside it.
        module_key = getattr(self, "_current_module", "hsv")
        allowed = set(_MODULE_DEFS.get(module_key, _MODULE_DEFS["hsv"])["sliders"])

        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            elif g not in allowed:
                visible = False  # force-hide: not in this module's set
            else:
                visible = self.cfg.get(f"showSliders{g}", True)
            self.slider_containers[g].setVisible(visible)
            self.sliders_layout.addWidget(self.slider_containers[g])

        # Recalculate layout geometries since height changed
        self.update_geometries()
        self._adjust_content_height()

    def zoom_ui(self, factor):
        self.resize(int(320 * factor), int(710 * factor))
        self._adjust_content_height()

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
        if screen is None:
            return
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
        # Only poll while the feature is enabled; on_settings_saved() starts
        # or stops the timer when the setting changes.
        if self.cfg.get("onlyShowInCsp", False):
            self.foreground_timer.start()
            self.check_foreground_window()

    def check_foreground_window(self):
        # If settings onlyShowInCsp is False, do nothing
        if not self.cfg.get("onlyShowInCsp", False):
            return

        try:
            import win32gui
            import win32process
        except ImportError:
            return

        hwnd = win32gui.GetForegroundWindow()
        is_drawing_active = False
        pid = 0

        if hwnd:
            try:
                title = (win32gui.GetWindowText(hwnd) or "").lower()
            except Exception:
                title = ""

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pass

            if pid:
                # Cache the resolved exe per PID so an unchanged foreground
                # window doesn't re-query the process on every tick.
                if getattr(self, "_fg_exe_cache_pid", None) == pid:
                    exe_name = getattr(self, "_fg_exe_cache", "")
                else:
                    exe_name = _resolve_process_exe(pid)
                    self._fg_exe_cache_pid = pid
                    self._fg_exe_cache = exe_name
                if _exe_matches_drawing_app(exe_name):
                    is_drawing_active = True

            # Title fallback covers localized windows and cases where the
            # process query was denied.
            if not is_drawing_active and _title_matches_drawing_app(title):
                is_drawing_active = True

        # A foreground window owned by this process (main window, settings
        # window or picker overlay) means the user is interacting with us.
        # The REAL foreground PID (win32) is the source of truth here:
        # Qt's isActiveWindow() bookkeeping is unreliable — in no-focus mode
        # the palette can never become "active", and the separate settings
        # window's activation state can get stuck (activateWindow() denied
        # by the OS foreground lock leaves Qt thinking it is active forever).
        # Trusting that stale state kept the palette visible even when a
        # non-drawing app took the foreground ("仅在画图软件前台时显示"失效).
        is_our_focused = bool(pid and pid == os.getpid())

        should_be_visible = is_drawing_active or is_our_focused

        # Keep the window up during an active color pick
        picker = getattr(self, "picker_overlay", None)
        if picker is not None and picker.is_active:
            should_be_visible = True

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

        # Update grayscale controller and migrate all removed backends to the
        # validated OKLCh implementation. Mag remains the Luma fallback.
        new_backend = self.cfg.get("grayscaleFilterBackend", "native")
        new_backend = "mag" if new_backend == "mag" else "native"
        current_backend = (
            "mag" if type(self.grayscale_overlay).__name__ == "MagFilterController"
            else "native"
        )
        if new_backend != current_backend:
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
            if new_backend == "mag":
                from core.mag_grayscale import MagFilterController
                self.grayscale_overlay = MagFilterController(mode="luma")
            else:
                from core.native_grayscale import NativeGrayscaleController
                self.grayscale_overlay = NativeGrayscaleController(mode="oklch")
        self.grayscale_overlay.set_target("all")
        self.grayscale_overlay.set_mode("luma" if new_backend == "mag" else "oklch")

        # Update window flags dynamically
        self.update_window_flags()
        self.update_no_focus_policies()

        # Keep the foreground tracker running only while the feature is on,
        # and apply the new state immediately instead of waiting a tick.
        fg_timer = getattr(self, "foreground_timer", None)
        if self.cfg.get("onlyShowInCsp", False):
            if fg_timer is not None and not fg_timer.isActive():
                fg_timer.start()
            self.check_foreground_window()
        else:
            if fg_timer is not None:
                fg_timer.stop()

        # Restore visibility if onlyShowInCsp is turned off while auto_hidden
        if not self.cfg.get("onlyShowInCsp", False):
            if getattr(self, "auto_hidden", False):
                self.show()
                self.auto_hidden = False
        
        # Update active software mode in thread
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps", "companion"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        # Companion mode: show setup dialog if no saved session
        if mode == "companion":
            c = self.sync_thread.companion_sync
            if not c._connected and not c._has_session():
                from PyQt6.QtCore import QTimer as _Qt
                _Qt.singleShot(300, lambda: self._setup_companion_connection())

        # Update settings dialog variables in thread
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        setattr(self.sync_thread, "ps_version", self.cfg.get("psVersion", "auto"))
        self.sync_thread.update_versions()
        
        # Update follow mouse state
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        
        # Apply color-space module (overrides legacy colorWheelMode/wheelMode)
        module = self.cfg.get("colorSpaceModule", self._current_module)
        if module != self._current_module:
            self._apply_module(module)
        else:
            # Even if module didn't change, re-apply slider visibility in case
            # individual toggles were changed
            self.refresh_slider_visibility_and_order()

        self.color_wheel.reload_config()

        # Update lab visualizer mode
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            self.lab_square.set_render_mode(viz_mode)
            self.cfg["labVisualizerMaxVal"] = 110 if viz_mode == "lab" else 0.4

        # Update module button visibility
        if hasattr(self, 'btn_module'):
            show_btn = self.cfg.get("showModuleSwitchButton", True)
            self.btn_module.setVisible(show_btn)
            self._update_module_button_label()

        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.apply_theme()
        
        # Apply scaling zoom factor only if the target scale configuration has changed
        target_scale = self.cfg.get("uiScale", 100)
        if getattr(self, "current_ui_scale", 100) != target_scale:
            self.zoom_ui(target_scale / 100.0)
            self.current_ui_scale = target_scale
        else:
            self.update()
        # Reapply ringless layout after config/settings reload.
        # Mode OFF restores full ring/circles/bottom-right immediately.
        self._sync_ringless_mode()
        self._adjust_content_height()

    def close_application(self):
        # Flush coalesced module state before writing the final window config.
        self._flush_module_config_save()
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
        if hasattr(self, 'picker_overlay'):
            self.picker_overlay.stop()
            self.picker_overlay.close()
        if hasattr(self, 'grayscale_overlay'):
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
        if hasattr(self, 'sync_thread'):
            # Disable sync & reset PS COM ref so the polling loop
            # won't try to make new COM calls to a dead Photoshop.
            self.sync_thread.sync_enabled = False
            if hasattr(self.sync_thread, 'ps_sync'):
                try:
                    self.sync_thread.ps_sync._reset()
                except Exception:
                    pass
            if hasattr(self.sync_thread, 'companion_sync'):
                self.sync_thread.companion_sync._disconnect()
            # Signal the thread to stop, but DO NOT join it.
            # If it's blocked in a hung COM RPC call (Photoshop died
            # mid-call), joining would freeze the main thread forever.
            # The OS reclaims all resources on process exit anyway.
            self.sync_thread.running = False
        
        # Hide settings window before exit
        if hasattr(self, 'settings_window') and self.settings_window is not None:
            self.settings_window.hide()

        # Hide tray icon before exit
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        
        # Use os._exit to bypass Python's thread-join on exit.
        # sys.exit(0) would try to join non-daemon threads, which
        # can hang if the sync thread is stuck in a COM RPC call.
        import os as _os
        _os._exit(0)

    def init_tray(self):
        """Setup system tray icon with context menu for minimized window access."""
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = os.path.join("icons", "icon.ico")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            _style = QApplication.style()
            if _style is not None:
                self.tray_icon.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        
        self.tray_icon.setToolTip("Colorink")

        # Context menu
        tray_menu = QMenu()
        
        show_action = QAction("显示/隐藏", self)
        show_action.triggered.connect(self.toggle_visibility)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        """Handle tray icon click: single left-click toggles visibility."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_visibility()

    def toggle_visibility(self):
        """Toggle window visibility — same logic as hotkey hide/show."""
        if self.isVisible():
            self.hide()
        else:
            if self.follow_mouse_active:
                self.show_window_at_cursor()
            else:
                self.show()
                self.raise_()

    def closeEvent(self, event):
        """Override: hide to tray instead of closing the application."""
        self.hide()
        event.ignore()

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
                import win32con
                import win32gui
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
