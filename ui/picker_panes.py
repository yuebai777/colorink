"""Picker panes: PaneWithModeButton, WheelPane, and LabPane for the color-wheel / LAB visualizer stack.

Extracted from ui.main_window to keep the two picker UI concerns —
floating mode-button layout and pane construction — in a focused module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ui.ringless_mode import centered_control_offset

if TYPE_CHECKING:
    from ui.ringless_mode import RinglessLayout


@dataclass(frozen=True, slots=True)
class ButtonPositions:
    """Pixel positions for the mode-button cluster in ringless top-bar layout."""
    module_x: int
    mode_x: int
    y: int


def ringless_button_positions(
    pane_width: int,
    button_size: int,
    layout: RinglessLayout,
    pane_height: int | None = None,
) -> ButtonPositions:
    """Compute top-bar horizontal positions and vertical center for ringless buttons.

    Swatches-right → buttons-anchor-left; swatches-left → buttons-anchor-right.
    In both clusters module button is left of mode button in screen coordinates.
    """
    y = centered_control_offset(layout.control_bar_height, button_size)
    if layout.control_bar_position == "bottom" and pane_height is not None:
        y += max(0, pane_height - layout.control_bar_height)
    if layout.controls_side == "right":
        module_x = layout.margin
        mode_x = module_x + button_size + layout.button_gap
    else:
        mode_x = pane_width - layout.margin - button_size
        module_x = mode_x - layout.button_gap - button_size
    return ButtonPositions(module_x=module_x, mode_x=mode_x, y=y)


class PaneWithModeButton(QWidget):
    """Base for stacked panes that own a floating mode-switcher button.

    When ringless mode is active (``_ringless_layout.controls_enabled``), the
    mode/module buttons anchor to the top control-bar opposite the swatches.
    Otherwise they keep the classic bottom-right corner placement.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode_btn = None
        self._module_btn = None       # optional second button (left of mode)
        self._btn_size = 28           # px, updated by MainWindow from uiScale
        self._btn_margin = 6
        self._btn_gap = 4
        self._reserve_module_slot = False
        self._ringless_layout: RinglessLayout | None = None

    # ── Button setters (trigger reposition) ──────────────────────────────

    def set_mode_button(self, btn):
        self._mode_btn = btn
        self._reposition_mode_button()

    def set_module_button(self, btn):
        """Optional second button placed to the LEFT of the mode button."""
        self._module_btn = btn
        self._reserve_module_slot = btn is not None
        self._reposition_mode_button()

    def set_module_slot_reserved(self, reserved: bool) -> None:
        self._reserve_module_slot = reserved
        self._reposition_mode_button()

    def set_mode_button_metrics(self, size, margin):
        self._btn_size = size
        self._btn_margin = margin
        self._reposition_mode_button()

    def set_ringless_layout(self, layout: RinglessLayout) -> None:
        """Apply a ringless layout (or None to revert to legacy bottom-right)."""
        self._ringless_layout = layout
        self._reposition_mode_button()

    # ── Qt event overrides ───────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_mode_button()

    # ── Reposition dispatch ──────────────────────────────────────────────

    def _reposition_mode_button(self):
        if self._ringless_layout is not None and self._ringless_layout.controls_enabled:
            self._reposition_ringless()
        else:
            self._reposition_legacy()

    # ── Legacy bottom-right (byte-for-byte identical to original) ────────

    def _reposition_legacy(self):
        bw = self._btn_size
        bh = self._btn_size
        m = self._btn_margin
        gap = self._btn_gap
        pw = self.width()
        ph = self.height()

        # Primary button: bottom-right corner
        if self._mode_btn is not None:
            px = pw - m - bw
            py = ph - m - bh
            if px >= 0 and py >= 0:
                self._mode_btn.setFixedSize(bw, bh)
                self._mode_btn.setGeometry(px, py, bw, bh)
                self._mode_btn.raise_()

        # Module button: to the left of the mode button
        if self._module_btn is not None:
            # Position relative to mode button's left edge; fall back to
            # bottom-right if mode button isn't set.
            if self._mode_btn is not None:
                left_anchor = pw - m - bw
            else:
                left_anchor = pw - m
            mx = left_anchor - gap - bw
            my = ph - m - bh
            if mx >= 0 and my >= 0:
                self._module_btn.setFixedSize(bw, bh)
                self._module_btn.setGeometry(mx, my, bw, bh)
                self._module_btn.raise_()

    # ── Ringless top-bar anchoring ───────────────────────────────────────

    def _reposition_ringless(self):
        bw = self._btn_size
        layout = self._ringless_layout
        assert layout is not None
        positions = ringless_button_positions(self.width(), bw, layout, self.height())

        mode_btn = self._mode_btn
        module_btn = self._module_btn
        module_visible = module_btn is not None and not module_btn.isHidden()

        if mode_btn is not None:
            # Keep the mode button in the same reserved slot even when the
            # optional module button is hidden. This prevents the LAB toggle
            # from jumping when switching between the two picker panes.
            if layout.controls_side == "right" and not self._reserve_module_slot:
                mx = positions.module_x
            else:
                mx = positions.mode_x
            mode_btn.setFixedSize(bw, bw)
            mode_btn.setGeometry(mx, positions.y, bw, bw)
            mode_btn.raise_()

        if module_visible and module_btn is not None:
            module_btn.setFixedSize(bw, bw)
            module_btn.setGeometry(
                positions.module_x, positions.y, bw, bw,
            )
            module_btn.raise_()


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
