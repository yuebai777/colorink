"""Compact slider readout with hover-only +/-1 step controls."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QCursor, QMouseEvent, QPainter, QPalette, QPolygonF
from PyQt6.QtWidgets import QLabel


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
        half_height = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)

        # Draw only the +/-1 arrow glyphs as a translucent overlay on the
        # right edge. There is no background tint, so the value text stays
        # fully readable and nothing covers the number.
        for half, center_y in ((1, half_height * 0.5), (-1, half_height * 1.5)):
            is_active = half == self._hover_half
            arrow_color = self.palette().color(QPalette.ColorRole.Text)
            arrow_color.setAlpha(150 if is_active else 70)
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
