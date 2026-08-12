"""ColorPreviewBox 鈥?overlapping fg/bg color circles drawn with QPainter.

Extracted from ui.main_window so the preview widget can evolve independently
(e.g. ringless mode) without touching the main window module.
"""

from __future__ import annotations

from typing import Any, cast

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QCursor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from ui.ringless_mode import (
    RINGLESS_ACTIVE_BORDER,
    RINGLESS_INACTIVE_BORDER,
    RinglessLayout,
    centered_control_offset,
)
from ui.transparent_swatch import TransparentTile, apply_preview_mouse_mask

_STROKE_PAD = 3  # Default padding around swatches for the compact control bar


class ColorPreviewBox(QWidget):
    """Overlapping fg/bg color swatches (+ transparent tile)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent: object = parent
        self.fg_color = QColor(255, 255, 255)
        self.bg_color = QColor(128, 128, 128)
        self.fg_transparent = False
        self.bg_transparent = False
        self.position_mode = "top-left"  # "top-left" | "bottom-left"
        self.active_slot = "fg"
        self.active_border_color = QColor(RINGLESS_ACTIVE_BORDER); self.inactive_border_color = QColor(RINGLESS_INACTIVE_BORDER)
        self.fg_size = 40
        self.bg_size = 26
        self._ringless_layout: RinglessLayout | None = None
        self._cached_ringless_rects: tuple[QRectF, QRectF] | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trans_tile = TransparentTile(self)
        self._trans_tile.clicked.connect(lambda: cast(Any, self._parent).set_active_transparent())

    # 鈹€鈹€ Public API 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def set_theme_colors(self, active_border, inactive_border):
        self.active_border_color = QColor(active_border)
        self.inactive_border_color = QColor(inactive_border)
        self.update()

    def set_colors(self, fg, bg):
        self.fg_color = fg
        self.bg_color = bg
        self.update()

    def set_transparent(self, slot, transparent):
        setattr(self, f"{slot}_transparent", bool(transparent))
        self.update()
        self._trans_tile.update()

    def update_slot_borders(self, active_slot):
        self.active_slot = active_slot
        self.update()
        self._trans_tile.update()

    def set_ringless_layout(
        self, layout: RinglessLayout, window_width: int, title_bar_height: int,
        visualizer_height: int | None = None,
    ) -> None:
        """Apply ringless presentation and compute shared rectangle geometry.

        When *controls_enabled* is ``False`` the widget restores legacy
        circle presentation (``position_mode``, colors, and active slot are
        never changed; ``resize_and_position`` still works afterward).
        """
        if not layout.controls_enabled:
            self._ringless_layout = None
            self._cached_ringless_rects = None
            self.update()
            return

        self._ringless_layout = layout

        pad = layout.swatch_padding
        sw = layout.swatch_width
        sh = layout.swatch_height
        gap = layout.swatch_gap

        # Row layout: [transparent][fg][bg] — all same size, same gap.
        self._cached_ringless_rects = (
            QRectF(float(pad + sw + gap), float(pad), float(sw), float(sh)),
            QRectF(float(pad + 2 * (sw + gap)), float(pad), float(sw), float(sh)),
        )

        # Size the widget to exactly fit the three-swatch row.
        widget_w = pad * 2 + sw * 3 + gap * 2
        widget_h = pad * 2 + sh
        self.setFixedSize(int(widget_w), int(widget_h))
        self._trans_tile.place_ringless(float(pad), float(sw), float(sh))

        # Position based on controls_side and window width
        margin = layout.margin
        if layout.controls_side == "right":
            x = window_width - int(widget_w) - margin
        else:
            x = margin
        # Vertically center the swatches inside the selected control bar band
        # (the widget floats and is raised above page content).
        bar_y = 0
        if layout.control_bar_position == "bottom" and visualizer_height is not None:
            bar_y = max(0, visualizer_height - layout.control_bar_height)
        swatch_only_h = pad * 2 + sh
        y = title_bar_height + bar_y + centered_control_offset(
            layout.control_bar_height, int(swatch_only_h)
        ) + layout.swatch_offset_y
        self.move(x, y)
        apply_preview_mouse_mask(self)
        self.update()

    def resize_and_position(self, wheel_size, title_bar_h, window_h, sliders_h, active_slot):
        # Calculate scale factor relative to default wheel size 304 to dynamically scale with the color wheel width
        wheel_scale = wheel_size / 304.0
        
        self.fg_size = int(40 * wheel_scale)
        self.bg_size = int(30 * wheel_scale)
        self.active_slot = active_slot

        box_dim = int(60 * wheel_scale)
        # Compute directly from wheel_scale — width() is stale before the
        # setFixedSize below takes effect.
        t_size, t_gap = self._trans_tile.metrics(scale=wheel_scale)
        self.setFixedSize(box_dim, box_dim + t_gap + t_size)
        self._trans_tile.place_legacy(box_dim, wheel_scale)
        
        # Position at the top-left corner of the window with clean margins
        margin_x = int(6 * wheel_scale)
        spacing = int(4 * wheel_scale)
        
        if self.position_mode == "top-left":
            margin_y = title_bar_h + spacing
            self.move(margin_x, margin_y)
        else:
            self.move(margin_x, window_h - sliders_h - box_dim - t_gap - t_size - int(6 * wheel_scale))
        apply_preview_mouse_mask(self)

    # 鈹€鈹€ Shared geometry 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _ringless_swatch_rects(self) -> tuple[QRectF, QRectF] | None:
        """Return defensive copies of the cached (fg, bg) rect pair, or None."""
        if self._ringless_layout is None or not self._ringless_layout.controls_enabled:
            return None
        rects = self._cached_ringless_rects
        if rects is None:
            return None
        return (QRectF(rects[0]), QRectF(rects[1]))

    # 鈹€鈹€ Drawing 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def draw_circle(self, painter, cx, cy, r, color, active):
        # Draw shadow
        painter.setBrush(QBrush(QColor(0, 0, 0, 45)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx - 0.5, cy + 1.5), r, r)
        
        # Draw fill
        painter.setBrush(QBrush(color))
        
        if active:
            # Active slot gets a nice distinct blue border
            painter.setPen(QPen(self.active_border_color, 2.5))
        else:
            # Inactive slot gets a thin light gray border
            painter.setPen(QPen(self.inactive_border_color, 1.0))
            
        painter.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_ringless_paint(self, painter: QPainter) -> None:
        """Draw the two rounded-rectangle swatches for ringless mode."""
        rects = self._ringless_swatch_rects()
        if rects is None:
            return  # defensive 鈥?caller guards on _ringless_layout

        fg_rect, bg_rect = rects
        assert self._ringless_layout is not None
        radius = float(self._ringless_layout.corner_radius)
        # A transparent active slot keeps the swatch colors but moves the
        # highlight to the transparent tile — no blue border here.
        bg_active = self.active_slot == "bg" and not self.bg_transparent
        fg_active = self.active_slot == "fg" and not self.fg_transparent
        self._ringless_swatch(painter, bg_rect, radius, self.bg_color, bg_active)
        self._ringless_swatch(painter, fg_rect, radius, self.fg_color, fg_active)

    def _ringless_swatch(self, painter, rect, radius, color, active):
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(self.active_border_color, 2.5) if active else QPen(self.inactive_border_color, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            if self._ringless_layout is not None and self._ringless_layout.controls_enabled:
                self._draw_ringless_paint(painter)
                return

            scale = self.width() / 60.0
            
            # Sizes
            fg_r = (40.0 * scale) / 2.0
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

            # Draw circles in correct z-order (active on top). A transparent
            # active slot shows no blue border — only the tile is highlighted.
            fg_active = self.active_slot == "fg" and not self.fg_transparent
            bg_active = self.active_slot == "bg" and not self.bg_transparent
            if self.active_slot == "fg":
                self.draw_circle(painter, bg_cx, bg_cy, bg_r, self.bg_color, bg_active)
                self.draw_circle(painter, fg_cx, fg_cy, fg_r, self.fg_color, fg_active)
            else:
                self.draw_circle(painter, fg_cx, fg_cy, fg_r, self.fg_color, fg_active)
                self.draw_circle(painter, bg_cx, bg_cy, bg_r, self.bg_color, bg_active)
        finally:
            painter.end()

    # 鈹€鈹€ Hit-testing 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _get_clicked_slot(self, px, py):
        # Ringless mode: rectangle hit-testing
        rects = self._ringless_swatch_rects()
        if rects is not None:
            fg_rect, bg_rect = rects
            pt = QPointF(px, py)
            if self.active_slot == "fg":
                if fg_rect.contains(pt):
                    return "fg"
                if bg_rect.contains(pt):
                    return "bg"
            else:
                if bg_rect.contains(pt):
                    return "bg"
                if fg_rect.contains(pt):
                    return "fg"
            return None

        # Legacy circle hit-testing
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
            if d_fg <= r2_fg:
                return "fg"
            elif d_bg <= r2_bg:
                return "bg"
        else:
            if d_bg <= r2_bg:
                return "bg"
            elif d_fg <= r2_fg:
                return "fg"
        return None

    # 鈹€鈹€ Context menu 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _show_color_context_menu(self, color):
        menu = QMenu()
        r, g, b = color.red(), color.green(), color.blue()

        menu.addAction(f"Copy RGB: rgb({r}, {g}, {b})",
                       lambda r=r, g=g, b=b: (cb := QApplication.clipboard()) is not None and cb.setText(f"rgb({r}, {g}, {b})"))
        menu.addAction(f"Copy HEX: #{r:02X}{g:02X}{b:02X}",
                       lambda r=r, g=g, b=b: (cb := QApplication.clipboard()) is not None and cb.setText(f"#{r:02X}{g:02X}{b:02X}"))

        menu.exec(QCursor.pos())

    # 鈹€鈹€ Mouse events 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def mousePressEvent(self, event):
        pos = event.position()
        px, py = pos.x(), pos.y()

        clicked_slot = self._get_clicked_slot(px, py)

        if clicked_slot is None:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if clicked_slot == "fg":
                cast(Any, self._parent).select_fg_slot()
            else:
                cast(Any, self._parent).select_bg_slot()
        elif event.button() == Qt.MouseButton.RightButton:
            color = self.fg_color if clicked_slot == "fg" else self.bg_color
            self._show_color_context_menu(color)

    def mouseDoubleClickEvent(self, event):
        # Deliberately a no-op (not forwarded to super(), which would
        # re-run the press handler): the double click must NOT swap the
        # fg/bg slot values. A quick double click while switching slots
        # is a normal "switch editing target" gesture, and swapping the
        # slot values (or mirroring an X swap into Photoshop) surprised
        # users. The first press of the double-click sequence already
        # selected the clicked slot.
        return

