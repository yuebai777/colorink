"""Vector-painted icon button for the floating picker mode/module controls.

Replaces raw Unicode glyphs (``◉``, ``☉``, ``◩``, ``△``, etc.) which render
at inconsistent sizes with ClearType subpixel colour fringing and baseline
drift on Windows High-DPI displays.
"""

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QPushButton, QStyle, QStyleOptionButton


class PickerIconButton(QPushButton):
    """A ``QPushButton`` that keeps its stylesheet chrome and ``text()``
    contract while painting a crisp, centred vector icon in ``paintEvent``."""

    def __init__(self, text: str = "", parent=None, *, icon_kind: str = ""):
        super().__init__(text, parent)
        self._icon_kind = icon_kind

    @property
    def icon_kind(self) -> str:
        return self._icon_kind

    def set_icon_kind(self, kind: str) -> None:
        if self._icon_kind != kind:
            self._icon_kind = kind
            self.update()

    def paintEvent(self, event) -> None:
        if not self._icon_kind:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            fg = QColor(opt.palette.buttonText().color())
            if not fg.isValid() or fg.alpha() == 0:
                fg = QColor(45, 45, 45)

            # Draw the themed button background + border without the raw text.
            opt.text = ""
            opt.icon = QIcon()
            style = self.style()
            if style is not None:
                style.drawControl(
                    QStyle.ControlElement.CE_PushButton, opt, painter, self
                )

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_vector_icon(painter, QRectF(self.rect()), fg)
        finally:
            painter.end()

    def _draw_vector_icon(self, p: QPainter, rect: QRectF, fg: QColor) -> None:
        cx = rect.center().x()
        cy = rect.center().y()
        side = min(rect.width(), rect.height())
        # Compact, understated icon geometry with hairline (~0.9px) strokes and
        # muted opacity so these utility buttons blend into the chrome.
        r = side * 0.23
        sw = max(0.85, side * 0.032)

        kind = self._icon_kind
        if kind.startswith("module:"):
            self._draw_module_icon(p, cx, cy, r, sw, fg, kind.split(":", 1)[1])
        elif kind == "mode:lab":
            self._draw_lab_mode_icon(p, cx, cy, r, sw, fg)
        elif kind == "mode:wheel":
            self._draw_wheel_mode_icon(p, cx, cy, r, sw, fg)
        elif kind == "lab:square":
            self._draw_lab_shape_icon(p, cx, cy, r, sw, fg, disc=False)
        elif kind == "lab:disc":
            self._draw_lab_shape_icon(p, cx, cy, r, sw, fg, disc=True)

    def _draw_module_icon(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        sw: float,
        fg: QColor,
        module: str,
    ) -> None:
        """Draw outer hue ring + inner slice shape for the active wheel module."""
        ring_color = QColor(fg)
        ring_color.setAlpha(85)
        p.setPen(QPen(ring_color, sw))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

        stroke_color = QColor(fg)
        stroke_color.setAlpha(105)
        inner_pen = QPen(
            stroke_color,
            max(0.8, sw * 0.90),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        fill_whisper = QColor(fg)
        fill_whisper.setAlpha(20)

        if module == "hsv":
            # Inscribed HSV square inside the hue ring
            half = r * 0.50
            sq = QRectF(cx - half, cy - half, half * 2.0, half * 2.0)
            p.setPen(inner_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(sq, 0.6, 0.6)

        elif module == "vhsv":
            # Inscribed VHSV square with subtle diagonal perceptual split
            half = r * 0.50
            sq = QRectF(cx - half, cy - half, half * 2.0, half * 2.0)
            tri = QPolygonF([
                QPointF(sq.left(), sq.bottom()),
                QPointF(sq.right(), sq.top()),
                QPointF(sq.right(), sq.bottom()),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill_whisper)
            p.drawPolygon(tri)
            p.setPen(inner_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(sq, 0.6, 0.6)
            p.drawLine(
                QPointF(sq.left() + 0.3, sq.bottom() - 0.3),
                QPointF(sq.right() - 0.3, sq.top() + 0.3),
            )

        elif module == "hls":
            # Inscribed equilateral triangle pointing right (matches HLS slice)
            tr = r * 0.65
            pts = QPolygonF([
                QPointF(cx + tr, cy),
                QPointF(cx - tr * 0.5, cy - tr * 0.866),
                QPointF(cx - tr * 0.5, cy + tr * 0.866),
            ])
            p.setPen(inner_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(pts)

        elif module == "rgb":
            # Isometric RGB colour cube inside the hue ring
            cr = r * 0.63
            pts = [
                QPointF(cx + cr * math.cos(math.radians(-90 + 60 * i)),
                        cy + cr * math.sin(math.radians(-90 + 60 * i)))
                for i in range(6)
            ]
            p.setPen(inner_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            inner_y_pen = QPen(stroke_color, max(0.8, sw * 0.82), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(inner_y_pen)
            p.drawLine(QPointF(cx, cy), pts[1])
            p.drawLine(QPointF(cx, cy), pts[3])
            p.drawLine(QPointF(cx, cy), pts[5])

        elif module == "lch":
            # Inscribed diamond / rhombus with subtle centre point
            dr = r * 0.63
            diamond = QPolygonF([
                QPointF(cx, cy - dr),
                QPointF(cx + dr, cy),
                QPointF(cx, cy + dr),
                QPointF(cx - dr, cy),
            ])
            p.setPen(inner_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(diamond)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(stroke_color)
            p.drawEllipse(QPointF(cx, cy), max(0.85, sw * 0.75), max(0.85, sw * 0.75))

    def _draw_lab_mode_icon(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        sw: float,
        fg: QColor,
    ) -> None:
        """Icon for switching to the LAB view: 2D a*b* plane + separated L* bar."""
        h = r * 1.56
        plane_w = h
        gap = r * 0.34
        bar_w = max(1.5, r * 0.30)
        total_w = plane_w + gap + bar_w
        left = cx - total_w * 0.5
        top = cy - h * 0.5
        bar_x = left + plane_w + gap

        stroke_color = QColor(fg)
        stroke_color.setAlpha(105)
        plane_rect = QRectF(left, top, plane_w, h)
        p.setPen(QPen(stroke_color, sw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(plane_rect, 1.0, 1.0)

        # Subtle a*/b* centre crosshair inside the plane
        axis_color = QColor(fg)
        axis_color.setAlpha(65)
        p.setPen(QPen(axis_color, max(0.75, sw * 0.78), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        pcx = plane_rect.center().x()
        pcy = plane_rect.center().y()
        pad = plane_w * 0.25
        p.drawLine(QPointF(plane_rect.left() + pad, pcy), QPointF(plane_rect.right() - pad, pcy))
        p.drawLine(QPointF(pcx, plane_rect.top() + pad), QPointF(pcx, plane_rect.bottom() - pad))

        # Thin vertical L* lightness bar on the right
        bar_rect = QRectF(bar_x, top, bar_w, h)
        p.setPen(QPen(stroke_color, max(0.8, sw * 0.90)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bar_rect, bar_w * 0.45, bar_w * 0.45)

    def _draw_wheel_mode_icon(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        sw: float,
        fg: QColor,
    ) -> None:
        """Icon for switching back to the Colour Wheel view: hue ring + inner
        triangle + small hue indicator dot on the ring."""
        ring_color = QColor(fg)
        ring_color.setAlpha(90)
        p.setPen(QPen(ring_color, sw))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

        stroke_color = QColor(fg)
        stroke_color.setAlpha(105)
        # Small hue indicator dot sitting on the ring at -45 deg
        ang = math.radians(-45.0)
        hx = cx + r * math.cos(ang)
        hy = cy + r * math.sin(ang)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(stroke_color)
        p.drawEllipse(QPointF(hx, hy), sw * 1.0, sw * 1.0)

        # Inner triangle representing the colour wheel slice
        tr = r * 0.56
        pts = QPolygonF([
            QPointF(cx + tr, cy),
            QPointF(cx - tr * 0.5, cy - tr * 0.866),
            QPointF(cx - tr * 0.5, cy + tr * 0.866),
        ])
        p.setPen(QPen(stroke_color, max(0.8, sw * 0.90), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(pts)

    def _draw_lab_shape_icon(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        sw: float,
        fg: QColor,
        *,
        disc: bool,
    ) -> None:
        """Icon for the LAB plane shape toggle (square vs circulant disc)."""
        stroke_color = QColor(fg)
        stroke_color.setAlpha(105)
        p.setPen(QPen(stroke_color, sw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if disc:
            p.drawEllipse(QPointF(cx, cy), r * 0.90, r * 0.90)
        else:
            half = r * 0.82
            p.drawRoundedRect(QRectF(cx - half, cy - half, half * 2.0, half * 2.0), 1.0, 1.0)

        axis_color = QColor(fg)
        axis_color.setAlpha(65)
        p.setPen(QPen(axis_color, max(0.8, sw * 0.78), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        span = r * 0.40
        p.drawLine(QPointF(cx - span, cy), QPointF(cx + span, cy))
        p.drawLine(QPointF(cx, cy - span), QPointF(cx, cy + span))
