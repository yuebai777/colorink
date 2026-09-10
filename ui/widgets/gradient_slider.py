"""Theme-aware gradient slider with optional in-gamut masking."""

import math
from typing import cast

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from ui.slider_themes import get_slider_theme


def handle_slider_jump_press(slider: QSlider, event: QMouseEvent) -> bool:
    """Handle mouse press on a QSlider so that left-clicking anywhere on the track
    immediately jumps to that position and engages dragging, even on platforms
    like Windows 10 (windowsvista / Fusion style) where QSlider defaults to
    page-stepping on left-click.

    Returns True if jump was handled (caller should return without calling super()),
    or False if default handling should proceed.
    """
    if event.button() == Qt.MouseButton.LeftButton:
        opt = QStyleOptionSlider()
        slider.initStyleOption(opt)
        hr = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, slider
        )
        pos = event.position().toPoint()
        if not hr.contains(pos):
            if slider.orientation() == Qt.Orientation.Horizontal:
                handle_w = hr.width()
                span = max(1, slider.width() - handle_w)
                pos_x = max(0, min(slider.width() - 1, pos.x()))
                val = QStyle.sliderValueFromPosition(
                    slider.minimum(), slider.maximum(),
                    int(pos_x - handle_w / 2),
                    span,
                    slider.invertedAppearance()
                )
            else:
                handle_h = hr.height()
                span = max(1, slider.height() - handle_h)
                pos_y = max(0, min(slider.height() - 1, pos.y()))
                val = QStyle.sliderValueFromPosition(
                    slider.minimum(), slider.maximum(),
                    int(pos_y - handle_h / 2),
                    span,
                    not slider.invertedAppearance()
                )
            slider.setValue(val)
            slider.initStyleOption(opt)
            new_hr = slider.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderHandle, slider
            )
            clamped_pt = QPointF(new_hr.center().x(), new_hr.center().y())
            global_pos = event.globalPosition() if hasattr(event, "globalPosition") else clamped_pt
            synth_event = QMouseEvent(
                event.type(),
                clamped_pt,
                global_pos,
                event.button(),
                event.buttons(),
                event.modifiers()
            )
            QSlider.mousePressEvent(slider, synth_event)
            return True
    return False


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

    def mousePressEvent(self, event):
        if handle_slider_jump_press(self, event):
            return
        super().mousePressEvent(event)

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

    def _rect_handle_width(self, scale=None):
        """Width of the ring drawn for a `handle_shape == "rect"` theme."""
        s = self.scale if scale is None else scale
        factor = float(cast(float, self._theme.get("handle_w_factor", 1.6)))
        return max(2, int(5 * s * factor))

    def _cursor_half_width(self, scale=None):
        """Half of the total ink extent of this theme's cursor ("thumb").

        The cursor is anchored by its geometric centre — that centre is what
        points at the value it represents (see `value_anchor_x`) — so this is
        also the smallest distance the end anchors may keep from the widget
        edge. Any less and half the cursor's stroke lands outside the widget,
        where the parent's clip rect slices it off, which is exactly what
        happened at min / max.
        """
        if str(self._theme.get("handle_shape", "rect")) == "triangle-below":
            return self._triangle_half_width(scale)
        # Rect handle: the double ring is stroked *inside* its own rect
        # (paintEvent insets the path by half a pen), so the handle is as
        # wide as its ink.
        return self._rect_handle_width(scale) / 2.0

    def _cursor_pad(self):
        """Distance each end anchor keeps from the widget edge.

        Every horizontal coordinate in this widget derives from it: the value
        axis — the gradient's stops, the out-of-gamut mask and the cursor's
        own centre — spans exactly `[pad, width - pad]`, so `minimum()` sits
        under the cursor's centre at the far left and `maximum()` at the far
        right, while the cursor itself (ink extent `2 * _cursor_half_width()`)
        still fits inside the widget at both ends.

        Size-independent by design: it feeds the stylesheet, so `update_scale`
        can call it before the widget has a width.
        """
        half = self._cursor_half_width()
        if str(self._theme.get("handle_shape", "rect")) != "triangle-below":
            return half
        # The triangle painter snaps to a half-pixel so the 1px pen lands on
        # one pixel column. A half-integer pad makes that snap a no-op at both
        # extremes, so they come out exactly mirrored instead of one end being
        # nudged outwards into the clip rect. Round up to the next half-pixel
        # so the ink can never overrun the pad it was given.
        pad = math.floor(half) + 0.5
        return pad if pad >= half else pad + 1.0

    def track_span(self, width=None):
        """`(x0, x1)` — the x range this slider's value axis is drawn over.

        The groove itself is painted across exactly this span, and the
        gradient's stops, the out-of-gamut mask and the cursor's centre all
        live on it, so the bar's two ends are `minimum()` / `maximum()` and
        the colour under the cursor's centre is the colour that value encodes.
        """
        w = float(self.width() if width is None else width)
        pad = min(self._cursor_pad(), max(0.0, w / 2.0))
        return pad, max(pad, w - pad)

    def value_anchor_x(self, frac=None, width=None):
        """x the cursor's geometric centre takes for `frac` through the range.

        Defaults to the slider's current value. The widget's edges are *not*
        the ends of the range: `track_span` insets them by half a cursor.
        """
        x0, x1 = self.track_span(width)
        if frac is None:
            vrange = self.maximum() - self.minimum()
            frac = (self.value() - self.minimum()) / vrange if vrange > 0 else 0.0
        frac = min(1.0, max(0.0, float(frac)))
        return x0 + frac * (x1 - x0)

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
        # The native handle owns the hit area AND the travel span Qt maps
        # values onto, so its width is not a free parameter: its centre sweeps
        # [w/2, width - w/2], which has to be the very span `track_span()`
        # paints the value axis over. Deriving it from the cursor's own ink
        # extent (instead of the theme's raw handle width) is what keeps the
        # cursor's centre on the value it points at, and its ink inside the
        # widget at min / max.
        handle_w = max(2, int(math.ceil(2.0 * self._cursor_pad())))
        handle_h = max(4, int(24 * scale * float(cast(float, t["handle_h_factor"]))))
        margin_y = -max(1, int(4 * scale * float(cast(float, t["handle_margin_y_factor"]))))
        border_radius = max(0, int(1 * scale * float(cast(float, t["handle_radius_factor"]))))

        if handle_shape == "triangle-below":
            # Native handle is invisible; it only reserves the travel span the
            # triangle is then centred on.
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
        # The *value axis* is not the widget: both ends are inset by half a
        # cursor. The groove is painted across exactly that span and the
        # cursor's centre travels it, so the bar's two ends ARE `minimum()`
        # and `maximum()` — the cursor's centre lands on the bar's end at
        # either extreme — while the cursor itself, which overhangs the bar
        # by half its width there, still stays inside the widget instead of
        # hanging outside where the parent's clip rect slices it.
        axis_x0, axis_x1 = self.track_span()
        axis_w = max(0.0, axis_x1 - axis_x0)
        groove_rect = QRectF(axis_x0, groove_y, axis_w, self.groove_h)

        def axis_x(frac):
            """Widget x of `frac` through the value range."""
            return axis_x0 + min(1.0, max(0.0, float(frac))) * axis_w

        grad = QLinearGradient(axis_x0, 0, axis_x1, 0)
        for stop, color in self.gradient_colors:
            grad.setColorAt(stop, color)

        # Fill groove
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        if axis_w > 0:
            painter.drawRoundedRect(groove_rect, self.groove_radius, self.groove_radius)

        # Groove outline (border theme). Inset by half the pen width so the
        # stroke stays inside the widget instead of being clipped.
        if self._groove_border_w > 0 and axis_w > 0:
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
            if vrange > 0 and axis_w > 0:
                left_frac = (self._gamut_min - vmin) / vrange
                right_frac = (self._gamut_max - vmin) / vrange
                painter.setBrush(QColor(160, 160, 160, 140))
                if left_frac > 0.005:
                    painter.drawRect(QRectF(axis_x0, groove_y,
                                            axis_w * left_frac, self.groove_h))
                if right_frac < 0.995:
                    right_x = axis_x(right_frac)
                    painter.drawRect(QRectF(right_x, groove_y,
                                            axis_x1 - right_x, self.groove_h))

        if handle_shape == "triangle-below":
            half_w = self._triangle_half_width()
            # The cursor's *geometric centre* is the anchor: it is placed on
            # the value's own position on the track above, not offset by half
            # its width. `track_span` has already reserved `half_w` of room at
            # each end, so the whole triangle stays inside the widget.
            centre = self.value_anchor_x()
            # Snap to a half-pixel so a 1px pen lands on one pixel column
            # instead of smearing across two (the indicator is small enough
            # that the blur reads as grey mush otherwise).
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
            # The indicator's *horizontal* edges — the base bar and the inner
            # line sitting on it — are landed on whole device pixels. On a
            # fractional uiScale (105%, 110%, …) or a scaled display the raw
            # float falls between two rows, and antialiasing then paints a
            # half-lit grey row under the indicator that reads as a drop
            # shadow. The slanted sides are diagonal either way.
            dpr = max(1.0, float(self.devicePixelRatioF()))
            tri_bottom = round((tri_base_y + tri_size_h) * dpr) / dpr
            left = QPointF(handle_x - tri_size_w, tri_bottom)
            right = QPointF(handle_x + tri_size_w, tri_bottom)

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
                    # Both bar edges snapped in device space, so the bar keeps
                    # a whole number of device rows and none of them bleeds
                    # out as a half-lit grey one.
                    bar_rows = max(1, round(base_h * dpr))
                    bar_top = tri_bottom - bar_rows / dpr

                    # Stop the slanted sides on top of the bar; running them to
                    # the full bottom would stroke half a pen width below it.
                    side_y = bar_top
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
                    painter.drawRect(QRectF(bx0, bar_top, bx1 - bx0, bar_rows / dpr))

                    inner_line = str(t.get("handle_tri_inner_line_color", "none"))
                    if inner_line.strip().lower() not in ("none", "transparent", ""):
                        line_h = max(1, round(max(1, int(self.scale)) * dpr)) / dpr
                        inset = base_h
                        inner_w = (bx1 - bx0) - 2 * inset
                        if inner_w > 0:
                            painter.setBrush(QColor(inner_line))
                            painter.drawRect(QRectF(
                                bx0 + inset, bar_top, inner_w, line_h
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

            bw = max(1, int(1 * self.scale))
            # Both rings are stroked *inside* the handle rect (the pen straddles
            # its path, so the path is inset by half a pen). Straddling the rect
            # instead — as this used to — put half of each outer stroke outside
            # the widget at min and at max, where the clip rect shaved it off:
            # the cursor's edge facing the widget border came out at half
            # weight while the opposite edge stayed solid.
            hf = QRectF(hx + bw / 2, hy + bw / 2, hw - bw, hh - bw)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # Inner ring: white (normal) or theme hover colour (active)
            inner_color = QColor(t["handle_hover_border"]) if is_active else QColor(255, 255, 255, 200)
            wi = QRectF(hf.x() + bw, hf.y() + bw, hf.width() - 2 * bw, hf.height() - 2 * bw)
            wr = max(0, hr - bw)
            painter.setPen(QPen(inner_color, bw))
            painter.drawRoundedRect(wi, wr, wr)

            # Black outer ring (on top)
            painter.setPen(QPen(QColor(0, 0, 0, 200), bw))
            painter.drawRoundedRect(hf, hr, hr)

            painter.end()
            super().paintEvent(event)
