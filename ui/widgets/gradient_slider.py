"""Theme-aware gradient slider with optional in-gamut masking."""

import math
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
        # Border theme fields (already resolved to concrete colours by
        # ThemeMixin.apply_theme); None = no groove outline.
        self._groove_border_w = 0
        self._groove_border_color = "#000000"
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

    def _triangle_extent(self, scale=None):
        """Vertical space the triangle indicator needs below the groove."""
        t = self._theme
        s = self.scale if scale is None else scale
        offset = int(float(cast(float, t.get("handle_tri_offset_y", 2))) * s)
        height = int(float(cast(float, t.get("handle_tri_size_h", 6))) * s)
        raw_bw = float(cast(float, t.get("handle_tri_border_width", 1)))
        stroke = max(1, int(raw_bw * s)) if raw_bw > 0 else 0
        return offset + height + stroke

    def _triangle_half_width(self, scale=None):
        """Horizontal half-extent of the triangle indicator.

        Covers the widest part actually painted: the triangle's own half
        width, SAI's base bar overhanging it on both sides, and half of the
        outline pen (a stroke straddles the path).
        """
        t = self._theme
        s = self.scale if scale is None else scale
        half = float(cast(float, t.get("handle_tri_size_w", 5))) * s
        half += float(cast(float, t.get("handle_tri_base_overhang", 0))) * s
        raw_bw = float(cast(float, t.get("handle_tri_border_width", 1)))
        if raw_bw > 0:
            half += max(1, int(raw_bw * s)) / 2.0
        return half

    def update_scale(self, scale, theme=None, border=None):
        """Re-apply geometry for `scale`, optionally switching themes.

        `theme`  — a slider theme dict (see `ui/slider_themes.py`).
        `border` — a *resolved* border theme dict (see `ui/border_themes.py`);
                   only its groove-outline fields are used here.
        """
        # Nothing here depends on the widget's size, only on scale/theme, so a
        # window drag re-ran the whole thing (stylesheet + re-polish) for every
        # slider on every resize event. Skip when the inputs are unchanged.
        signature = (
            float(scale),
            id(theme) if theme is not None else None,
            None if border is None else (
                int(border.get("groove_border_width", 0) or 0),
                str(border.get("groove_border_color", "#000000")),
            ),
        )
        if signature == getattr(self, "_scale_signature", None):
            return
        self._scale_signature = signature

        if theme is not None:
            self._theme = theme
        if border is not None:
            raw_w = int(border.get("groove_border_width", 0) or 0)
            self._groove_border_w = max(0, int(raw_w * scale)) if raw_w > 0 else 0
            self._groove_border_color = str(border.get("groove_border_color", "#000000"))
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
            # Native handle is invisible, but it still owns the hit area AND
            # the travel span Qt maps values onto, so it must be exactly as
            # wide as the triangle drawn on top of it. With the old narrow
            # handle the indicator both drifted away from the cursor and ran
            # off the widget at 0 / max, where half of it was clipped away.
            handle_w = max(handle_w, math.ceil(2 * self._triangle_half_width(scale)))
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
            # Reserve the groove plus everything the triangle needs below it.
            # paintEvent centres that same assembly, so the indicator can never
            # be clipped by the bottom edge.
            pad = max(2, int(2 * scale))
            self.setMinimumHeight(self.groove_h + self._triangle_extent(scale) + pad)
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

        # Qt caches the parsed stylesheet rule per widget: setStyleSheet
        # refreshes the painting but NOT the geometry subControlRect() reports,
        # so switching slider styles left the handle's hit area — and the
        # travel span mouse positions are mapped onto — stuck at the previous
        # theme's width (every triangle theme inherited the default's 8px).
        widget_style = self.style()
        if widget_style is not None:
            widget_style.unpolish(self)
            widget_style.polish(self)

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
        t = self._theme
        handle_shape = str(t.get("handle_shape", "rect"))

        # With a triangle indicator the groove is NOT centred on its own: the
        # whole assembly (groove + gap + triangle) is, otherwise any spare
        # height is split evenly above and below and the triangle is clipped
        # off the bottom edge.
        if handle_shape == "triangle-below":
            assembly_h = self.groove_h + self._triangle_extent()
            groove_y = max(0, (rect.height() - assembly_h) // 2)
        else:
            groove_y = (rect.height() - self.groove_h) // 2
        groove_rect = QRectF(0, groove_y, rect.width(), self.groove_h)

        grad = QLinearGradient(0, 0, rect.width(), 0)
        for stop, color in self.gradient_colors:
            grad.setColorAt(stop, color)

        # Fill groove
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(groove_rect, self.groove_radius, self.groove_radius)

        # Groove outline (border theme). Inset by half the pen width so the
        # stroke stays inside the widget instead of being clipped.
        if self._groove_border_w > 0:
            bw_g = self._groove_border_w
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(self._groove_border_color), bw_g))
            painter.drawRoundedRect(
                groove_rect.adjusted(bw_g / 2, bw_g / 2, -bw_g / 2, -bw_g / 2),
                self.groove_radius, self.groove_radius,
            )

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

        if handle_shape == "triangle-below":
            half_w = self._triangle_half_width()
            vrange = self.maximum() - self.minimum()
            frac = (self.value() - self.minimum()) / vrange if vrange > 0 else 0.0
            # Travel on the same inset span the invisible native handle uses
            # (update_scale sizes that handle to exactly this width): the
            # marker sits under the cursor while dragging, lands dead centre
            # at mid-range, and stays whole at both ends. Painting it at
            # ``frac * width`` — as this did — pushed half of it outside the
            # widget at 0 and at max, where the clip rect sliced it off.
            centre = half_w + frac * max(0.0, rect.width() - 2 * half_w)
            # Snap to a half-pixel so a 1px pen lands on one pixel column
            # instead of smearing across two (the indicator is small enough
            # that the blur reads as grey mush otherwise), then keep the two
            # extremes exactly flush with the groove ends.
            handle_x = math.floor(centre) + 0.5
            if rect.width() < 2 * half_w:
                # Widget narrower than the marker itself: nothing can keep it
                # whole, so at least keep it centred instead of hard left.
                handle_x = rect.width() / 2.0
            elif handle_x - half_w < 0.0:
                handle_x = half_w
            elif handle_x + half_w > rect.width():
                handle_x = rect.width() - half_w

            tri_style = str(t.get("handle_tri_style", "filled"))
            tri_fill = str(t.get("handle_tri_color", t["handle_bg"]))
            tri_border_color = QColor(str(t.get("handle_tri_border", t["handle_border"])))
            tri_size_w = float(cast(float, t.get("handle_tri_size_w", 5))) * self.scale
            tri_size_h = float(cast(float, t.get("handle_tri_size_h", 6))) * self.scale
            tri_offset_y = int(float(cast(float, t.get("handle_tri_offset_y", 2))) * self.scale)
            tri_base_y = groove_y + self.groove_h + tri_offset_y

            raw_bw = float(cast(float, t.get("handle_tri_border_width", 1)))
            tri_bw = max(1, int(raw_bw * self.scale)) if raw_bw > 0 else 0
            tri_pen = QPen(tri_border_color, tri_bw) if tri_bw > 0 else QPen(Qt.PenStyle.NoPen)

            apex = QPointF(handle_x, tri_base_y)
            left = QPointF(handle_x - tri_size_w, tri_base_y + tri_size_h)
            right = QPointF(handle_x + tri_size_w, tri_base_y + tri_size_h)

            if tri_style == "caret":
                # Thin "^" indicator (CSP): stroke only, never filled.
                # Drawn as two lines *from the apex outward* rather than one
                # polyline: a polyline's join makes the second segment
                # rasterise differently, so the two arms come out visibly
                # asymmetric (one crisp column, one 2px smear).
                caret_pen = QPen(tri_border_color, max(1, tri_bw))
                caret_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(caret_pen)
                painter.drawLine(apex, left)
                painter.drawLine(apex, right)
            else:
                # "filled" / "outline" only differ in whether the fill colour
                # is opaque; "transparent" lets the panel show through.
                base_raw = float(cast(float, t.get("handle_tri_base_width", 0)))
                base_h = max(1, int(base_raw * self.scale)) if base_raw > 0 else 0

                if tri_fill.strip().lower() in ("transparent", "none", ""):
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                else:
                    painter.setBrush(QColor(tri_fill))

                if base_h > 0:
                    # Heavy base edge (SAI): the bottom edge IS the bar, so the
                    # polygon is filled without a pen and only the two slanted
                    # sides are stroked — otherwise the bottom pen's outer half
                    # bleeds past the bar as a grey fringe.
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawPolygon(QPolygonF([apex, left, right]))

                    overhang = float(cast(float, t.get("handle_tri_base_overhang", 0))) * self.scale
                    bottom_y = tri_base_y + tri_size_h

                    # Stop the slanted sides on top of the bar; running them to
                    # the full bottom would stroke half a pen width below it.
                    side_y = bottom_y - base_h
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    side_pen = QPen(tri_pen)
                    side_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    painter.setPen(side_pen)
                    # Two lines from the apex (see the caret note above).
                    painter.drawLine(apex, QPointF(left.x(), side_y))
                    painter.drawLine(apex, QPointF(right.x(), side_y))
                    bx0 = round(handle_x - tri_size_w - overhang)
                    bx1 = round(handle_x + tri_size_w + overhang)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(str(t.get("handle_tri_base_color", "#000000"))))
                    painter.drawRect(QRectF(bx0, bottom_y - base_h, bx1 - bx0, base_h))

                    inner_line = str(t.get("handle_tri_inner_line_color", "none"))
                    if inner_line.strip().lower() not in ("none", "transparent", ""):
                        line_h = max(1, int(self.scale))
                        inset = base_h
                        inner_w = (bx1 - bx0) - 2 * inset
                        if inner_w > 0:
                            painter.setBrush(QColor(inner_line))
                            painter.drawRect(QRectF(
                                bx0 + inset, bottom_y - base_h, inner_w, line_h
                            ))
                else:
                    painter.setPen(tri_pen)
                    painter.drawPolygon(QPolygonF([apex, left, right]))
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
