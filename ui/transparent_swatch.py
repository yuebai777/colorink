"""Transparent-swatch button + checker drawing for ColorPreviewBox.

Kept separate from ``color_preview_box.py`` so that module stays under
the project's pure-LOC ceiling.

The transparent tile is a capsule (pill) button rendered below the
fg/bg swatches. Clicking it marks the active slot as transparent; the
actual sync-side write is handled by the main window (companion protocol
supports ``IsColorTransparent``, memory modes do not yet).
"""

from __future__ import annotations

from typing import cast

from PyQt6.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QRegion
from PyQt6.QtWidgets import QWidget

# Legacy circles layout: the widget box is 60px at wheel_scale=1.0.
# The tile is a horizontal capsule; only its HEIGHT scales with the box.
_LEGACY_TILE_FACTOR = 16.0
_TILE_GAP = 2  # px between the fg/bg swatches and the capsule (both modes)

# Highlight color when the active slot is transparent (user-specified blue).
TRANSPARENT_ACTIVE_COLOR = QColor(131, 155, 201)


def _capsule_region(rect: QRect) -> QRegion:
    """Hit region matching a horizontal capsule: two half-circle ends + a
    rectangular middle. Lets clicks in the capsule's corner notches fall
    through to whatever is underneath."""
    h = rect.height()
    r = h // 2
    mid_w = max(0, rect.width() - h)
    mid = QRegion(QRect(rect.x() + r, rect.y(), mid_w, h))
    left = QRegion(QRect(rect.x(), rect.y(), h, h), QRegion.RegionType.Ellipse)
    right = QRegion(QRect(rect.x() + rect.width() - h, rect.y(), h, h),
                    QRegion.RegionType.Ellipse)
    return mid.united(left).united(right)


def apply_preview_mouse_mask(box) -> None:
    """Restrict mouse events on the floating preview box to its interactive
    areas (swatch circles/rects + transparent capsule).

    The preview box is raised above the color wheel / LAB square and its
    rect is mostly transparent. Without a mask those transparent parts
    swallow hover/drag events that should reach the wheel below — the
    crosshair cursor stops appearing over the covered arc and hue-ring
    drags stop tracking the mouse. The mask makes only the actual swatches
    (+ capsule) clickable and lets everything else pass through.
    """
    region = QRegion()
    rects = box._ringless_swatch_rects()
    if rects is not None:
        for rect in rects:
            region = region.united(QRegion(rect.toRect().adjusted(-3, -3, 3, 3)))
    else:
        scale = box.width() / 60.0
        fg_r = (40.0 * scale) / 2.0
        bg_r = (30.0 * scale) / 2.0
        box_size = float(box.width())
        border = 2.0 * scale
        if box.position_mode == "top-left":
            fg_cx = fg_r + border
            fg_cy = box_size - fg_r - border
            bg_cx = box_size - bg_r - border
            bg_cy = bg_r + border
        else:
            fg_cx = fg_r + border
            fg_cy = fg_r + border
            bg_cx = box_size - bg_r - border
            bg_cy = box_size - bg_r - border
        # Centres must match the paint code EXACTLY (unpadded radii), only
        # the mask radius gets a small pad for the border stroke. Using the
        # padded radius as the centre offset shifted the ellipse 3px away
        # from the drawn circle and swallowed clicks on its edge arc.
        for cx_, cy_, r in ((fg_cx, fg_cy, fg_r + 3.0), (bg_cx, bg_cy, bg_r + 3.0)):
            region = region.united(QRegion(
                QRectF(cx_ - r, cy_ - r, r * 2, r * 2).toRect(),
                QRegion.RegionType.Ellipse,
            ))
    tile = box._trans_tile.geometry()
    if tile.isValid() and not tile.isEmpty():
        if box._ringless_layout is not None and box._ringless_layout.controls_enabled:
            region = region.united(QRegion(tile.adjusted(-3, -3, 3, 3)))
        else:
            region = region.united(_capsule_region(tile.adjusted(-3, -3, 3, 3)))
    if region.isEmpty():
        box.clearMask()
    else:
        box.setMask(region)


def draw_checker(painter: QPainter, rect: QRectF, cell: int = 4) -> None:
    """Fill *rect* with a light checkerboard (transparency indicator)."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawRect(rect)
    painter.setBrush(QBrush(QColor(196, 196, 196)))
    cols = max(1, int(rect.width()) // cell)
    rows = max(1, int(rect.height()) // cell)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                x = rect.x() + c * cell
                y = rect.y() + r * cell
                w = min(cell, rect.right() - x + 1)
                h = min(cell, rect.bottom() - y + 1)
                if w > 0 and h > 0:
                    painter.drawRect(QRectF(x, y, w, h))
    painter.restore()


class TransparentTile(QWidget):
    """Capsule (pill) transparent button rendered below the fg/bg swatches.

    Owns its geometry, hover state, and click dispatch, so ColorPreviewBox
    only creates it and keeps its size in sync. Border colors are read
    live from the parent preview box (theme switches need no plumbing).
    """

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    @staticmethod
    def metrics(scale: float = 1.0) -> tuple[int, int]:
        """Legacy mode: ``(capsule_height, gap_below_swatches)``.

        Ringless mode no longer uses this — the tile is a same-size swatch
        placed directly by :meth:`place`.
        """
        s = max(0.01, float(scale))
        return (
            max(10, int(round(_LEGACY_TILE_FACTOR * s))),
            _TILE_GAP,
        )

    @staticmethod
    def capsule_width(height: int, available: float) -> int:
        """Width of the pill: between 2x and 3x the height, but never
        exceeding the available width (a narrow bar degrades gracefully)."""
        avail = max(0, int(available))
        return min(avail, max(height * 2, min(avail, height * 3)))

    def place_ringless(self, x_pos: float, y_pos: float, swatch_w: float, swatch_h: float) -> None:
        """Position the tile as a same-size swatch at *(x_pos, y_pos)*
        inside the fg/bg row (middle position between the two swatches)."""
        w = max(1, int(round(swatch_w)))
        h = max(1, int(round(swatch_h)))
        self.setGeometry(int(round(x_pos)), int(round(y_pos)), w, h)
        # Rounded-rect hit region matching the painted shape.
        parent = cast(QWidget, self.parent())
        layout = getattr(parent, "_ringless_layout", None)
        radius = float(layout.corner_radius) if layout is not None else 4.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0),
                            radius, radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def place_legacy(self, box_dim: float, scale: float = 1.0) -> None:
        """Position the capsule centered below the circles.

        The capsule is pulled up by ``2*scale`` px (half the swatch border
        stroke) so the visual gap between the bottom-most circle and the
        capsule is a constant 2 px at any scale — matching the spec.
        """
        t_h, t_gap = self.metrics(scale=scale)
        w = self.capsule_width(t_h, box_dim - 2.0 * max(4.0, 6.0 * scale))
        x = (box_dim - w) / 2.0
        y = box_dim + t_gap - 2.0 * scale
        self.setGeometry(int(round(x)), int(round(y)), int(w), int(t_h))
        # Only the pill itself is interactive — clicks in the capsule's
        # corner notches fall through to whatever is underneath.
        self.setMask(_capsule_region(self.rect()))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            parent = cast(QWidget, self.parent())
            # Highlight when the *active* slot is transparent. The swatch
            # circles keep their last color; the capsule shows the state.
            slot = getattr(parent, "active_slot", "fg")
            transparent = bool(getattr(
                parent,
                "fg_transparent" if slot == "fg" else "bg_transparent",
                False,
            ))
            inactive = QColor(getattr(parent, "inactive_border_color", "#cccccc"))
            rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            layout = getattr(parent, "_ringless_layout", None)
            if layout is not None and bool(
                getattr(layout, "controls_enabled", False)
            ):
                radius = float(layout.corner_radius)
            else:
                radius = rect.height() / 2.0
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.save()
            painter.setClipPath(path)
            draw_checker(painter, rect.adjusted(1.0, 1.0, -1.0, -1.0))
            painter.restore()
            if transparent:
                painter.setPen(QPen(TRANSPARENT_ACTIVE_COLOR, 4.0))
            else:
                painter.setPen(QPen(inactive, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
            if self._hover and not transparent:
                painter.setPen(QPen(TRANSPARENT_ACTIVE_COLOR, 2.0))
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                        radius, radius)
        finally:
            painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()
