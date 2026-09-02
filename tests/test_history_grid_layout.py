"""历史色格在窗口缩放时不许左右抽搐。

回归点：格子边长由宽度整除得出，除不尽的像素原本堆在两侧边距里 —— 边距
每多 1px 宽就往右爬 1px，等格子边长进位时又猛地弹回去，整块网格来回抖。
现在余数摊进格子之间的缝隙：格子仍是正方形，左边距恒定，网格永远铺满。
"""

import pytest

from ui.color_history import ColorHistoryWidget

from .test_ringless_preview_support import qapp  # noqa: F401


def _grid(cols=8, rows=4):
    grid = ColorHistoryWidget()
    grid.configure(cols, rows)
    return grid


def _sweep(grid, widths):
    out = []
    for width in widths:
        grid.resize(width, grid.height())
        grid._relayout()
        cells = grid._visible_cells()
        out.append({
            "width": width,
            "pad": grid._left_pad,
            "first_x": cells[0].x(),
            "last_right": cells[-1].x() + cells[-1].width(),
            "swatch": grid._swatch_size,
        })
    return out


def test_left_margin_never_creeps(qapp):
    """左边距在整个缩放区间内恒定 —— 之前是 1→5 再弹回 1 的锯齿。"""
    rows = _sweep(_grid(), range(280, 400))
    pads = {r["pad"] for r in rows}
    assert len(pads) == 1, f"左边距出现 {sorted(pads)} 多种取值"


def test_first_cell_never_moves(qapp):
    rows = _sweep(_grid(), range(280, 400))
    assert len({r["first_x"] for r in rows}) == 1


def test_grid_edge_tracks_the_width_one_to_one(qapp):
    """网格右缘随宽度 1:1 前进，不许出现回退或跳跃。"""
    rows = _sweep(_grid(), range(280, 400))
    steps = [b["last_right"] - a["last_right"] for a, b in zip(rows, rows[1:])]
    assert set(steps) <= {1}, f"右缘步进出现 {sorted(set(steps))}"


@pytest.mark.parametrize("cols", [4, 8, 12, 16])
def test_grid_spans_the_full_width(qapp, cols):
    """两侧留白必须对称（差值不超过边框的 2px）。"""
    grid = _grid(cols=cols)
    for row in _sweep(grid, range(300, 340)):
        left = row["first_x"]
        right = row["width"] - row["last_right"]
        assert abs(left - right) <= 2, (cols, row)


def test_cells_stay_square(qapp):
    """余数摊进缝隙而不是格子，格子必须保持正方形。"""
    grid = _grid()
    for width in range(300, 340):
        grid.resize(width, grid.height())
        grid._relayout()
        for cell in grid._visible_cells():
            assert cell.width() == cell.height()


def test_gaps_differ_by_at_most_one_pixel(qapp):
    """摊余数时每条缝隙最多多 1px，肉眼看不出来。"""
    grid = _grid()
    for width in (301, 307, 313, 331):
        grid.resize(width, grid.height())
        grid._relayout()
        cells = grid._visible_cells()[:grid._cols]
        gaps = [b.x() - (a.x() + a.width()) for a, b in zip(cells, cells[1:])]
        assert max(gaps) - min(gaps) <= 1, (width, gaps)


def test_swatch_size_is_monotonic(qapp):
    """格子只会随宽度变大，不会忽大忽小。"""
    rows = _sweep(_grid(), range(280, 400))
    sizes = [r["swatch"] for r in rows]
    assert all(b >= a for a, b in zip(sizes, sizes[1:]))


def test_no_swatch_gap_wider_than_one_pixel(qapp):
    """回归：余数摊进缝隙后，若叠加 cell 自绘的 1px seam，会变成
    seam+bg=2px 的可视间隙。容器必须用右邻颜色补掉 spare 像素，
    使渲染出来的色块间缝隙恒为 1px（device pixel）。"""
    from PyQt6.QtGui import QColor

    grid = ColorHistoryWidget()
    grid.configure(8, 2)
    colors = [QColor(220, 30, 30), QColor(30, 220, 30), QColor(30, 30, 220),
              QColor(220, 160, 30), QColor(160, 30, 220), QColor(30, 160, 220),
              QColor(220, 90, 140), QColor(90, 140, 220)]
    for width in (301, 304, 313, 331):
        grid.resize(width, grid.height())
        grid._relayout()
        grid.set_colors(colors)
        image = grid.grab().toImage()
        dpr = image.devicePixelRatio() or 1.0
        cells = grid._visible_cells()[:grid._cols]
        y = int((cells[0].y() + cells[0].height() // 2) * dpr + 0.5)
        for a, b in zip(cells, cells[1:]):
            left = a.color.rgb()
            right = b.color.rgb()
            x0 = int((a.x() + a.width()) * dpr + 0.5) - 1
            x1 = int(b.x() * dpr + 0.5) - 1
            gap_px = sum(
                1 for x in range(x0, x1 + 1)
                if image.pixelColor(x, y).rgb() not in (left, right))
            assert gap_px == 1, (width, a.color.name(), b.color.name(), gap_px)
