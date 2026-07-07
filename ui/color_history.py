"""Color history widget: a themed grid showing recently used colors.

The widget is hosted inside the same vertical strip as the gradient sliders
and shares their order mechanism, so users can reorder it among the slider
groups from the settings sidebar (just like RGB / HSV / LAB / OKLab / OKLCh).

Design notes
------------
The color cells are positioned *absolutely* (no QLayout) rather than in a
QGridLayout. Recreating a QGridLayout while a QApplication is alive is a
known cause of C++-side access violations in PyQt6 (the old layout's
backing object is destroyed underneath Python references). Absolute
positioning lets us rebuild the grid arbitrarily without ever touching a
layout object — we just `move()` each cell and toggle its visibility.

Theme contract
--------------
MainWindow resolves the active theme (auto/CSP-matched or gray/white/black)
and pushes the three palette colors down via `apply_theme`:

    bg           — the central widget background (the body color)
    border_color — the window frame border color, used as the panel outline
    text         — the foreground text color (for the empty-state hint)

The widget paints its own background + 1px border, so it visually belongs
to the surrounding chrome regardless of which theme is active.

Recording contract
------------------
* `record(r, g, b)` is called by MainWindow whenever a drag finishes
  (slider released, wheel released, lab square released). Consecutive
  duplicates are collapsed so the strip never flashes the same color twice.
* `color_picked` is emitted back to MainWindow when the user clicks a
  swatch; MainWindow loads that color into the active slot.

Persistence
-----------
The color list is mirrored to config so it survives restarts. The maximum
in-memory list is `cols * rows`; older entries are dropped FIFO.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import QWidget

# Maximum grid extent. The settings sidebar caps the configuration at these
# values (cols ≤ 12, rows ≤ 4), so we pre-allocate a cell pool of this size
# and never need to grow it.
MAX_COLS = 12
MAX_ROWS = 4
_HALF = 0.5  # sub-pixel offset for crisp 1px borders


def _to_int(v):
    """Coerce a QColor.value-style float to a clamped int in 0..255."""
    if v is None:
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return max(0, min(255, int(round(f))))


class _SwatchCell(QWidget):
    """A single clickable color tile. Owns its paintEvent and click dispatch.

    States
    ------
    _hovered  — mouse is inside the cell → blue outline
    _selected — this cell holds the "active" color → red/white dashed frame
                (hover takes visual priority over select so the user can see
                the blue feedback on hover even when selected)
    """

    clicked = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(0, 0, 0, 0)  # transparent → "empty"
        self._selected = False
        self._hovered = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(20, 20)
        self.setVisible(False)

    @property
    def color(self):
        return self._color

    def set_color(self, color):
        self._color = QColor(color) if color is not None else QColor(0, 0, 0, 0)
        self.update()

    def clear(self):
        self._color = QColor(0, 0, 0, 0)
        self.update()

    def is_empty(self):
        return self._color.alpha() == 0

    def set_selected(self, sel):
        if self._selected != sel:
            self._selected = sel
            self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.is_empty():
            self.clicked.emit(QColor(self._color))
        event.accept()

    def sizeHint(self):
        return QSize(self.width(), self.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(0, 0, -1, -1)
            if self.is_empty():
                painter.fillRect(rect, QColor(255, 255, 255, 18))
                parent_border = getattr(self.parent(), "_border", QColor(0, 0, 0, 80))
                faint = QColor(parent_border)
                faint.setAlpha(80)
                painter.setPen(QPen(faint, 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(rect).adjusted(_HALF, _HALF, -_HALF, -_HALF))
            else:
                # Soft drop shadow
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(0, 0, 0, 45)))
                painter.drawRoundedRect(QRectF(1, 2, rect.width(), rect.height()), 2, 2)
                # Fill
                painter.setBrush(QBrush(QColor(self._color.rgb())))

                if self._hovered:
                    # Mouse hover → blue border (takes priority over select)
                    painter.setPen(QPen(QColor("#5a94e2"), 2.0))
                    painter.drawRoundedRect(QRectF(0, 0, rect.width(), rect.height()), 2, 2)
                elif self._selected:
                    # "红白相间" — red dashed frame with white rim
                    red_pen = QPen(QColor(220, 40, 40), 2.0)
                    red_pen.setDashPattern([3, 2])
                    painter.setPen(red_pen)
                    painter.drawRoundedRect(QRectF(1, 1, rect.width()-2, rect.height()-2), 2, 2)
                    # Inner white rim creates the alternating red-white effect
                    white_pen = QPen(QColor(255, 255, 255, 180), 1.0)
                    painter.setPen(white_pen)
                    painter.drawRoundedRect(QRectF(2.5, 2.5, rect.width()-5, rect.height()-5), 1, 1)
                else:
                    # Normal filled swatch: subtle light outline
                    painter.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
                    painter.drawRoundedRect(QRectF(0, 0, rect.width(), rect.height()), 2, 2)
        finally:
            painter.end()


class ColorHistoryWidget(QWidget):
    """Container that lays color swatches out in a grid and matches the
    active background/border theme.

    Public API
    ----------
    * `apply_theme(bg, border_color, text)` — push palette colors from MainWindow
    * `configure(cols, rows, swatch_size)`    — rebuild the grid geometry
    * `set_colors(list[QColor])`             — bulk-load colors (used by init)
    * `record(r, g, b)`                       — append a color, returns the
                                               updated list so MainWindow
                                               can persist it
    * `color_picked` signal                   — emitted with a QColor when the
                                               user clicks a swatch
    """

    color_picked = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ColorHistoryWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Theme colors — pushed by MainWindow. Defaults match the gray theme
        # so the widget looks reasonable before apply_theme runs.
        self._bg = QColor(178, 178, 178)
        self._border = QColor(120, 120, 120)
        self._text = QColor(34, 34, 34)
        self._cols = 8
        self._rows = 2
        self._swatch_size = 18
        self._gap = 1
        self._pad = 6
        self._left_pad = 6  # updated by _relayout after centring
        self._colors = []  # list[QColor]
        self._selected_index = -1  # index into visible cells (-1 = none)
        # Cell pool sized to MAX_COLS * MAX_ROWS; never grown. configure()
        # just repositions and toggles visibility on this pool. Avoids the
        # Qt/PyQt6 C++-side crash you get when deleting/recreating layout items.
        self._cells = []
        self._init_pool()
        self._relayout()

    # ───────────────────────── public configuration ─────────────────────────

    def apply_theme(self, bg, border_color, text):
        self._bg = QColor(bg) if not isinstance(bg, QColor) else QColor(bg)
        self._border = (QColor(border_color) if not isinstance(border_color, QColor)
                        else QColor(border_color))
        self._text = QColor(text) if not isinstance(text, QColor) else QColor(text)
        self.update()

    def configure(self, cols, rows, swatch_size=None):
        """Apply new columns/rows. The cell size is auto-derived from the
        widget width (see _relayout) so that the grid always spans the full
        width of the parent. The `swatch_size` argument is accepted for API
        compatibility but ignored — width drives the cell size now."""
        cols = max(1, min(MAX_COLS, int(cols)))
        rows = max(1, min(MAX_ROWS, int(rows)))
        self._cols = cols
        self._rows = rows
        capacity = cols * rows
        if len(self._colors) > capacity:
            self._colors = self._colors[-capacity:]
        self._selected_index = -1  # selection invalidated by shape change
        self._relayout()

    def set_colors(self, colors):
        """Bulk-load a list of QColor objects (values clipped to capacity)."""
        capacity = self._cols * self._rows
        cleaned = []
        for c in colors:
            if c is None:
                continue
            qc = QColor(c) if not isinstance(c, QColor) else QColor(c)
            if qc.isValid() and qc.alpha() > 0:
                cleaned.append(qc)
        self._colors = cleaned[-capacity:]
        self._selected_index = -1
        self._refill_cells()

    def record(self, r, g, b):
        """Append (r, g, b) to the history, collapsing consecutive duplicates
        and clipping to the grid capacity. The new color becomes the selected
        one (visible as the *first* cell). Returns the updated color list so
        the caller can persist it."""
        r_i, g_i, b_i = _to_int(r), _to_int(g), _to_int(b)
        new_color = QColor(r_i, g_i, b_i)
        if self._colors and self._colors[-1] == new_color:
            return list(self._colors)
        self._colors.append(new_color)
        capacity = self._cols * self._rows
        if len(self._colors) > capacity:
            self._colors = self._colors[-capacity:]
        self._refill_cells()
        # Newest color is now the first visible cell → mark it selected
        self._apply_selection_by_color(new_color)
        return list(self._colors)

    def clear(self):
        self._colors = []
        self._selected_index = -1
        self._refill_cells()

    # ────────────────────────────── pool setup ──────────────────────────────

    def _init_pool(self):
        """Create the cell pool once with maximum extent. We never grow or
        shrink the pool afterwards — `configure` only repositions cells
        and toggles their visibility."""
        for r in range(MAX_ROWS):
            for c in range(MAX_COLS):
                cell = _SwatchCell(self)
                cell.setFixedSize(self._swatch_size, self._swatch_size)
                cell.clicked.connect(self.color_picked)
                self._cells.append(cell)

    def _relayout(self):
        """Reposition + resize + show/hide cells to fill the *current* width.

        We never tear down a QLayout (QGridLayout rebuild is a known PyQt6 C++
        -side crash). Cell size is derived from the parent-given width so the
        grid spans the full horizontal space of whatever container hosts us
        — typically the sliders vertical strip, which tracks the window
        width. The widget's own height is then derived from the computed
        cell size × rows, and reported back via setFixedHeight so the parent
        layout knows how tall we need to be."""
        cols = self._cols
        rows = self._rows
        gap = self._gap
        pad = self._pad

        # Inner width available for cells: total width minus the 2px border
        # pair and the 2*pad. Until the first layout pass width() can be 0; in
        # that case fall back to a min cell size of 8 so we don't divide by
        # ~0 — the real width arrives via the first resizeEvent shortly after.
        width = self.width()
        # Net space for the cells *after* accounting for the (cols-1) gaps.
        inner_cells = width - 2 * pad - 2
        if cols > 1:
            inner_cells -= (cols - 1) * gap
        swatch = max(8, inner_cells // cols) if cols > 0 else 8
        self._swatch_size = swatch

        # Centre the grid within the widget so left and right margins are
        # pixel-identical (accounting for the 1px panel border at each edge).
        total_used_w = cols * swatch + (cols - 1) * gap
        left_pad = max(pad, (self.width() - 1 - total_used_w) // 2)
        self._left_pad = left_pad

        for r in range(MAX_ROWS):
            for c in range(MAX_COLS):
                idx = r * MAX_COLS + c
                if idx >= len(self._cells):
                    continue
                cell = self._cells[idx]
                visible = (r < rows and c < cols)
                if visible:
                    cell.setFixedSize(swatch, swatch)
                    cell.move(left_pad + c * (swatch + gap), pad + r * (swatch + gap))
                    cell.show()
                else:
                    cell.hide()

        # Height is ours to dictate (rows × swatch + padding + border).
        # Width is the parent's call — we only set a minimum so the parent
        # doesn't squeeze us below what cols×8 need to render sanely.
        total_h = pad * 2 + rows * swatch + (rows - 1) * gap + 2
        min_w = pad * 2 + cols * 8 + (cols - 1) * gap + 2
        self.setFixedHeight(max(20, total_h))
        self.setMinimumWidth(max(20, min_w))
        self.updateGeometry()
        self.update()
        # Repaint the cells with the new (possibly clipped) color set
        self._refill_cells()

    def resizeEvent(self, event):
        """Widget width changed (parent layout reflowed, or window resized)
        → recompute the swatch size to fill it. Bubbling up to the parent
        keeps the grid visually anchored to the window width."""
        super().resizeEvent(event)
        self._relayout()

    def _refill_cells(self):
        """Push self._colors into the visible cells, **newest first**
        (top-left cell = most recent). Also re-applies the selection flag
        on the cell at `_selected_index`."""
        visible = self._visible_cells()
        n_colors = len(self._colors)
        for i, cell in enumerate(visible):
            if i < n_colors:
                # newest = colors[-1] → fills cell 0; colors[-2] → cell 1; ...
                idx = n_colors - 1 - i
                cell.set_color(self._colors[idx])
            else:
                cell.clear()
            cell.set_selected(i == self._selected_index)

    def _visible_cells(self):
        """Return the pool cells that are currently shown, in row-major order."""
        result = []
        for r in range(self._rows):
            for c in range(self._cols):
                idx = r * MAX_COLS + c
                if idx < len(self._cells):
                    result.append(self._cells[idx])
        return result

    def _apply_selection_by_color(self, color):
        """Find the visible cell holding `color` and mark it selected."""
        visible = self._visible_cells()
        for i, cell in enumerate(visible):
            if cell.color == color and not cell.is_empty():
                self._selected_index = i
                cell.set_selected(True)
            else:
                cell.set_selected(False)

    def mark_selected(self, color):
        """Called by MainWindow when the user clicks a history swatch."""
        self._apply_selection_by_color(QColor(color))

    # ─────────────────────────── painting + sizing ─────────────────────────

    def sizeHint(self):
        """Width: report the parent's width (we adapt to it). Height: the
        current rows × derived swatch size, so vertical layout planning works
        even before the first paint. _relayout keeps `_swatch_size` in sync
        with the actual widget width."""
        swatch = self._swatch_size if self._swatch_size > 0 else 8
        h = self._pad * 2 + self._rows * swatch + (self._rows - 1) * self._gap + 2
        return QSize(max(self.width(), 20), max(20, h))

    def minimumSizeHint(self):
        # We don't have a real minimum width beyond cols×8 + padding — the
        # parent layout is free to make us narrower if needed, _relayout
        # stays robust to a 0-width input.
        min_w = self._pad * 2 + self._cols * 8 + (self._cols - 1) * self._gap + 2
        swatch = 8
        min_h = self._pad * 2 + self._rows * swatch + (self._rows - 1) * self._gap + 2
        return QSize(max(20, min_w), max(20, min_h))

    def paintEvent(self, event):
        """Draw the themed panel background + 1px outline. Drawing the panel
        ourselves (rather than via stylesheet border) gives precise control
        over how swatches align with the border."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            rect = self.rect().adjusted(0, 0, -1, -1)

            # Body
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._bg))
            painter.drawRect(rect)

            # Border
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(self._border, 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(QRectF(rect).adjusted(_HALF, _HALF, -_HALF, -_HALF))

            # Empty-cell hints so the grid reads even when nothing recorded
            if not self._colors:
                hint = QColor(self._text)
                hint.setAlpha(120)
                painter.setPen(QPen(hint, 1.0))
                size = self._swatch_size
                gap = self._gap
                top_pad = self._pad
                # Mirror the centring from _relayout so hints align with cells
                total_used = self._cols * size + (self._cols - 1) * gap
                left_pad = max(top_pad, (self.width() - 1 - total_used) // 2)
                for r in range(self._rows):
                    for c in range(self._cols):
                        x = left_pad + c * (size + gap) + _HALF
                        y = top_pad + r * (size + gap) + _HALF
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRect(QRectF(x, y, size - 1, size - 1))
        finally:
            painter.end()