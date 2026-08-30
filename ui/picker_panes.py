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
        self._extra_btn = None        # optional third button (left of module)
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

    def set_extra_button(self, btn):
        """Optional third button placed to the LEFT of the module button."""
        self._extra_btn = btn
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

    # ── Generic visible-button packing ────────────────────────────────────

    def _pack_visible_buttons(
        self, order, edge, y, bw, bh, gap, anchor_left,
    ) -> None:
        """Pack only *visible* buttons consecutively from an edge.

        *order* is the spatial left→right order of the full button cluster;
        hidden buttons do not reserve space, so a lone visible button sits
        right at the edge (corner / control-bar edge).
        """
        visible = [b for b in order if b is not None and not b.isHidden()]
        if anchor_left:
            x = edge
            for btn in visible:
                btn.setFixedSize(bw, bh)
                btn.setGeometry(x, y, bw, bh)
                btn.raise_()
                x += bw + gap
        else:
            x = edge
            for btn in reversed(visible):
                btn.setFixedSize(bw, bh)
                btn.setGeometry(x - bw, y, bw, bh)
                btn.raise_()
                x -= bw + gap

    # ── Legacy bottom-right ───────────────────────────────────────────────

    def _reposition_legacy(self):
        bw = self._btn_size
        bh = self._btn_size
        m = self._btn_margin
        gap = self._btn_gap
        pw = self.width()
        ph = self.height()
        y = ph - m - bh
        if y < 0:
            return
        # Full cluster, left→right: [extra, module, mode].
        order = [self._extra_btn, self._module_btn, self._mode_btn]
        self._pack_visible_buttons(
            order, pw - m, y, bw, bh, gap, anchor_left=False)

    # ── Ringless top-bar anchoring ────────────────────────────────────────

    def _reposition_ringless(self):
        bw = self._btn_size
        layout = self._ringless_layout
        assert layout is not None
        positions = ringless_button_positions(
            self.width(), bw, layout, self.height(),
        )
        if layout.controls_side == "right":
            # Swatches on the right → button cluster anchored at the left edge.
            # Full cluster, left→right: [module, mode, extra].
            order = [self._module_btn, self._mode_btn, self._extra_btn]
            edge = layout.margin
        else:
            # Swatches on the left → button cluster anchored at the right edge.
            # Full cluster, left→right: [extra, module, mode].
            order = [self._extra_btn, self._module_btn, self._mode_btn]
            edge = self.width() - layout.margin
        self._pack_visible_buttons(
            order, edge, positions.y, bw, bw, layout.button_gap,
            anchor_left=(layout.controls_side == "right"),
        )


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
