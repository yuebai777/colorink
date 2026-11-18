"""Compact slider readout with hover-only +/-1 step controls."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QPalette, QPolygonF
from PyQt6.QtWidgets import QLabel


class SliderValueLabel(QLabel):
    """Compact slider readout with clear hover-only +/-1 controls."""

    def __init__(self, slider, parent=None):
        super().__init__("0", parent)
        self.slider = slider
        self._hovered = False
        self._hover_half = 1
        self._arrow_color: QColor | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Upper half: +1; lower half: -1")

    def set_arrow_color(self, color) -> None:
        """Pin the hover arrow ink to match the stylesheet's resolved text colour."""
        qc = QColor(color) if color is not None else QColor()
        self._arrow_color = qc if qc.isValid() else None
        self.update()

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
        half_height = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)

        # Place the +/-1 overlay arrows on the edge opposite the numeric text
        # so right-aligned themes (CSP / SAI) keep every digit unobstructed.
        right_aligned = bool(self.alignment() & Qt.AlignmentFlag.AlignRight)
        x = 6 if right_aligned else (self.width() - 6)

        base_color = (
            QColor(self._arrow_color)
            if self._arrow_color is not None and self._arrow_color.isValid()
            else self.palette().color(QPalette.ColorRole.WindowText)
        )

        # Draw only the +/-1 arrow glyphs as a translucent overlay on the
        # outer edge. There is no background tint, so the value text stays
        # fully readable and nothing covers the number.
        for half, center_y in ((1, half_height * 0.5), (-1, half_height * 1.5)):
            is_active = half == self._hover_half
            arrow_color = QColor(base_color)
            arrow_color.setAlpha(175 if is_active else 85)
            painter.setBrush(arrow_color)
            if half == 1:
                points = [
                    QPointF(x, center_y - 4.5),
                    QPointF(x - 4.5, center_y + 2.5),
                    QPointF(x + 4.5, center_y + 2.5),
                ]
            else:
                points = [
                    QPointF(x, center_y + 4.5),
                    QPointF(x - 4.5, center_y - 2.5),
                    QPointF(x + 4.5, center_y - 2.5),
                ]
            painter.drawPolygon(QPolygonF(points))
        painter.end()
