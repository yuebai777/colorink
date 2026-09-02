"""Diagnose the "some history swatches have a 2px gap" report.

The layout spreads the leftover pixels one-per-gap so the grid always spans
the full width. Each cell also paints a 1px gray seam on its right edge, so a
gap that got one extra pixel used to become seam(1) + background(1) = a
visible 2px gap where the neighbouring gaps are only 1px.

    python -u tools/diag_history_gap.py            # offscreen, rendered pixels
    QT_SCALE_FACTOR=1.5 python -u tools/diag_history_gap.py   # dpr 1.5 check

Prints the *rendered* gap widths (device pixels between two colour blocks)
and fails if any gap is wider than 1px.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.color_history import ColorHistoryWidget  # noqa: E402

_COLORS = [QColor(220, 30, 30), QColor(30, 220, 30), QColor(30, 30, 220),
           QColor(220, 160, 30), QColor(160, 30, 220), QColor(30, 160, 220),
           QColor(220, 90, 140), QColor(90, 140, 220)]


def _dev(v, dpr):
    return int(v * dpr + 0.5)  # qRound, same as Qt's widget rounding


def rendered_gap_widths(grid):
    """Per adjacent pair: number of device pixels between the two colour
    blocks that are neither cell's colour (i.e. the visible "gap")."""
    grid.set_colors(_COLORS)
    image = grid.grab().toImage()
    dpr = image.devicePixelRatio() or 1.0
    cells = grid._visible_cells()[:grid._cols]
    y = _dev(cells[0].y() + cells[0].height() // 2, dpr)
    gaps = []
    for a, b in zip(cells, cells[1:]):
        left = a.color.rgb()
        right = b.color.rgb()
        x0 = _dev(a.x() + a.width(), dpr) - 1
        x1 = _dev(b.x(), dpr) - 1
        count = 0
        for x in range(x0, x1 + 1):
            px = image.pixelColor(x, y).rgb()
            if px != left and px != right:
                count += 1
        gaps.append(count)
    return gaps


def main():
    app = QApplication(sys.argv)
    dpr = app.devicePixelRatio() or 1.0
    print(f"dpr={dpr}")
    grid = ColorHistoryWidget()
    grid.configure(8, 2)
    bad = 0
    for width in range(300, 361):
        grid.resize(width, grid.height())
        grid._relayout()
        gaps = rendered_gap_widths(grid)
        if max(gaps) > 1:
            bad += 1
            if bad <= 5:
                print(f"width={width} swatch={grid._swatch_size} gaps={gaps}")
    print(f"bad widths: {bad} / 61")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
