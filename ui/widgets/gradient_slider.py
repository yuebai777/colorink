"""Theme-aware gradient slider with optional in-gamut masking."""

from typing import cast

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from ui.slider_themes import get_slider_theme


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

        old_val = self.value()
        new_val = old_val + steps_to_move
        new_val = max(self.minimum(), min(self.maximum(), new_val))
        if new_val != old_val:
            self.setValue(new_val)
            # 滚轮路径没有鼠标释放事件：手动补发 sliderReleased，让
            # 提交动作（记录历史 + 同步画图软件）与拖动/键盘路径一致。
            self.sliderReleased.emit()
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
