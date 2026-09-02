"""LAB pane composition with the vertical lightness bar.

Regression cover for the bar's layout bug: showing it used to steal a fixed
column from the a*b* plane, which left the plane off-centre with a wide hole
between it and the bar, shrank the circular disc well below the size the pane
could still afford, and let the bar span the whole pane height instead of the
plane's own band.
"""

import pytest
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ui.lab_visualizer import LabSlider, LabSquare
from ui.picker_panes import LabPane
from ui.window.theme import ThemeMixin

from .test_ringless_support import qapp  # noqa: F401

BAR_W = 18
GAP = 8
BUTTON_SIZE = 28
BUTTON_MARGIN = 6


# ── LabSquare geometry (no window needed) ─────────────────────────────────

def _square(width, height, shape="square", avoid_top=0, total=0):
    sq = LabSquare()
    sq.resize(width, height)
    sq.set_shape(shape)
    sq.set_avoid_top(avoid_top)
    sq.set_side_cluster(total, BAR_W if total else 0, GAP)
    return sq


def test_plane_stays_centred_without_bar(qapp):
    """Bar hidden → unchanged legacy behaviour: plane centred on the widget."""
    sq = _square(300, 400)
    _gap, x, y, size = sq.plane_geometry()
    assert size == 300
    assert x == pytest.approx((300 - 300) / 2)
    assert y == pytest.approx((400 - 300) / 2)


def test_square_margins_are_even_with_bar(qapp):
    """[gap][plane][gap][bar][gap]: all three margins match."""
    # Pane 312 wide; the column takes 2*gap + bar, so the widget is narrower.
    total = 312
    gap = max(GAP, (total - 231 - BAR_W) / 3.0)
    sq = _square(int(total - (2 * gap + BAR_W)), 312, avoid_top=81, total=total)
    plane_gap, x, _y, size = sq.plane_geometry()
    assert size == 231                       # height-limited, unchanged
    assert plane_gap == pytest.approx(21.0)
    assert x == pytest.approx(21.0)
    # plane right edge + gap + bar + gap == pane width
    assert x + size + plane_gap + BAR_W + plane_gap == pytest.approx(total)


def test_square_never_overflows_its_own_widget(qapp):
    """A stale/oversized cluster hint must not push the plane out of view."""
    sq = _square(120, 400, total=312)
    _gap, x, _y, size = sq.plane_geometry()
    assert x >= 0
    assert x + size <= 120


def test_disc_takes_the_whole_width_budget(qapp):
    """The disc shrinks by the bar cluster only — not by a whole column."""
    total = 312
    without = _square(total, 312, shape="disc")
    _g, _x, _y, free_diameter = without.plane_geometry()

    budget = total - 3 * GAP - BAR_W
    sq = _square(total - (2 * GAP + BAR_W), 312, shape="disc", total=total)
    plane_gap, x, _y2, diameter = sq.plane_geometry()
    assert diameter == pytest.approx(budget)          # 270, not 250
    assert diameter < free_diameter                   # still gives the bar room
    assert plane_gap == pytest.approx(GAP)
    assert x == pytest.approx(GAP)
    assert x + diameter + plane_gap + BAR_W + plane_gap == pytest.approx(total)


def test_disc_ignores_avoid_top_with_bar(qapp):
    """The disc keeps mirroring the hue ring's top anchoring."""
    total = 312
    sq = _square(total - (2 * GAP + BAR_W), 312, shape="disc",
                 avoid_top=120, total=total)
    _gap, _x, y, diameter = sq.plane_geometry()
    size = diameter + 4.0
    assert y == pytest.approx(size / 2.0 + 6.0 - diameter / 2.0)


def test_disc_metrics_untouched_without_bar(qapp):
    """Hue-ring parity when the bar is hidden (the documented invariant)."""
    sq = _square(420, 420, shape="disc")
    cx, cy, radius = sq._disc_metrics()
    size = min(420 - 16, 420 - 6)
    assert cx == pytest.approx(210.0)
    assert cy == pytest.approx(size / 2.0 + 6.0)
    assert radius == pytest.approx(size / 2.0 - 2.0)


def test_cache_key_tracks_the_cluster(qapp):
    """Toggling the bar must re-render the plane, not reuse a stale image."""
    sq = _square(312, 312)
    before = sq._cache_key(False)
    sq.set_side_cluster(312, BAR_W, GAP)
    assert sq._cache_key(False) != before
    assert sq._cache_key(False)[1] == sq._plane_size()


# ── ThemeMixin._sync_lab_lightness_bar against real widgets ───────────────

class _BarHost(ThemeMixin, QWidget):
    """Minimal host exposing exactly what the bar sync touches."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.pane_lab = LabPane(self)
        self.pane_lab.set_mode_button_metrics(BUTTON_SIZE, BUTTON_MARGIN)
        self.lab_layout = QHBoxLayout(self.pane_lab)
        self.lab_layout.setContentsMargins(0, 0, 0, 0)
        self.lab_square = LabSquare()
        self.lab_slider_column = QWidget()
        column = QVBoxLayout(self.lab_slider_column)
        column.setContentsMargins(0, 0, 0, 0)
        self.lab_slider = LabSlider()
        column.addWidget(self.lab_slider)
        self.lab_layout.addWidget(self.lab_square, stretch=1)
        self.lab_layout.addWidget(self.lab_slider_column)
        self.lab_square.planeGeometryChanged.connect(self._sync_lab_lightness_bar)

    def compose(self, width=312, height=312, shape="square", avoid_top=0):
        self.pane_lab.resize(width, height)
        self.lab_square.set_shape(shape)
        self.lab_square.set_avoid_top(avoid_top)
        for _ in range(3):
            self._sync_lab_lightness_bar(1.0)
            self.lab_layout.activate()
            column = self.lab_slider_column.layout()
            if column is not None:
                column.activate()
        return self.report()

    def report(self):
        gap, x, y, size = self.lab_square.plane_geometry()
        bar_x = self.lab_slider_column.x() + self.lab_slider.x()
        band_top, band_h = self.lab_slider.track_band()
        bar_y = self.lab_slider_column.y() + self.lab_slider.y() + band_top
        return {
            "plane_x": x, "plane_y": y, "plane_size": size, "gap": gap,
            "left": x,
            "inner": bar_x - (x + size),
            "right": self.pane_lab.width() - (bar_x + self.lab_slider.width()),
            "bar_top": bar_y,
            "bar_bottom": bar_y + band_h,
            "column_w": self.lab_slider_column.width(),
            "column_min_h": self.lab_slider_column.minimumSizeHint().height(),
        }


def _host(qapp, **cfg):
    base = {"uiScale": 100, "showLabLightnessSlider": True}
    base.update(cfg)
    return _BarHost(base)


@pytest.mark.parametrize("shape", ["square", "disc"])
@pytest.mark.parametrize("width", [260, 312, 420])
def test_pane_margins_are_even(qapp, shape, width):
    host = _host(qapp)
    r = host.compose(width=width, height=312, shape=shape, avoid_top=81)
    assert r["left"] == pytest.approx(r["inner"], abs=1.5)
    assert r["left"] == pytest.approx(r["right"], abs=1.5)


@pytest.mark.parametrize("shape", ["square", "disc"])
def test_bar_starts_at_the_plane_top(qapp, shape):
    """条的顶端与色块顶端对齐，底端在悬浮按钮上方收住（留一个间距）。"""
    host = _host(qapp)
    r = host.compose(shape=shape, avoid_top=81)
    assert r["bar_top"] == pytest.approx(r["plane_y"], abs=1.0)
    assert r["bar_bottom"] <= r["plane_y"] + r["plane_size"] + 1.0
    assert r["bar_bottom"] > r["bar_top"] + 20


@pytest.mark.parametrize("shape", ["square", "disc"])
def test_bar_clears_the_floating_buttons(qapp, shape):
    """条的底端不能贴着右下角那组悬浮按钮 —— 要留出一个间距。

    回归点：条原本一路画到按钮顶边，视觉上"贴脸"。
    """
    host = _host(qapp)
    r = host.compose(shape=shape)
    buttons_top = host.lab_square.height() - BUTTON_SIZE - BUTTON_MARGIN
    clearance = buttons_top - r["bar_bottom"]
    assert clearance >= GAP - 1, f"条底距按钮只有 {clearance}px"
    assert clearance <= GAP + 2, f"条底距按钮空出 {clearance}px，太多了"


def test_hidden_bar_restores_full_width_plane(qapp):
    host = _host(qapp)
    shown = host.compose(shape="disc")
    host.cfg["showLabLightnessSlider"] = False
    hidden = host.compose(shape="disc")
    assert not host.lab_slider_column.isVisible()
    assert hidden["plane_size"] > shown["plane_size"]
    assert host.lab_square.side_total == 0
    # Plane is centred on the whole pane again.
    assert hidden["plane_x"] == pytest.approx(
        (host.lab_square.width() - hidden["plane_size"]) / 2.0, abs=1.0)


def test_repeated_sync_is_stable(qapp):
    """The pass must converge: no width oscillation between runs."""
    host = _host(qapp)
    first = host.compose(shape="disc")
    seen = set()
    for _ in range(6):
        host._sync_lab_lightness_bar(1.0)
        host.lab_layout.activate()
        host.lab_slider_column.layout().activate()
        seen.add((host.lab_square.width(), host.lab_slider_column.width()))
    assert len(seen) == 1
    assert host.report()["plane_size"] == pytest.approx(first["plane_size"])

# ── The bar must never drive the window's height ──────────────────────────

def test_bar_column_minimum_height_ignores_the_pane(qapp):
    """条列的最小高度不能跟着面板高度长。

    回归点：把条和色块对齐一度是用布局边距做的，而边距会计入条列的
    minimumSizeHint → LAB 面板最小高 → stack.minimumSizeHint() →
    _adjust_content_height 把窗口撑高 → 面板更高 → 下边距更大 …
    实测把 350 宽的窗口顶到 807 高，色盘只有 322，下面空出一百多像素。
    """
    host = _host(qapp)
    short = host.compose(width=312, height=312, shape="disc")["column_min_h"]
    tall = host.compose(width=312, height=900, shape="disc")["column_min_h"]
    assert short == tall, "条列最小高度随面板高度变化了 —— 会把窗口越撑越高"
    assert tall < 120, f"条列最小高度 {tall}px 太大，会撑高整个窗口"


@pytest.mark.parametrize("shape", ["square", "disc"])
def test_pane_minimum_does_not_grow_with_the_pane(qapp, shape):
    """LAB 面板的最小高度由色块自己决定，条不加码、也不随面板变高。"""
    host = _host(qapp)
    host.compose(width=312, height=312, shape=shape)
    short = host.pane_lab.minimumSizeHint().height()
    host.compose(width=312, height=900, shape=shape)
    tall = host.pane_lab.minimumSizeHint().height()
    assert tall == short
    assert tall <= host.lab_square.minimumHeight() + 1


def test_track_band_maps_clicks_inside_the_band(qapp):
    """条的取值必须按可见的那一段算，不是按整个控件高度。"""
    from PyQt6.QtCore import QPointF

    bar = LabSlider()
    bar.resize(18, 400)
    bar.set_track_band(100.0, 200.0)
    bar.handle_mouse(QPointF(9.0, 100.0))
    assert bar.L == pytest.approx(100.0)
    bar.handle_mouse(QPointF(9.0, 200.0))
    assert bar.L == pytest.approx(50.0)
    bar.handle_mouse(QPointF(9.0, 300.0))
    assert bar.L == pytest.approx(0.0)
    # 落在带子外面要夹住，不能给出范围外的亮度
    bar.handle_mouse(QPointF(9.0, 380.0))
    assert bar.L == pytest.approx(0.0)
    bar.handle_mouse(QPointF(9.0, 10.0))
    assert bar.L == pytest.approx(100.0)


def test_track_band_defaults_to_the_whole_widget(qapp):
    bar = LabSlider()
    bar.resize(18, 300)
    assert bar.track_band() == (0.0, 300.0)