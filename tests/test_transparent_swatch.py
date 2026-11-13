"""Transparent-swatch button tests (ColorPreviewBox + TransparentTile)."""

import os
from typing import Any, cast

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from ui.color_preview_box import ColorPreviewBox
from ui.transparent_swatch import TransparentTile

from .test_ringless_preview_support import (
    canonical_layout,
    left_side_layout,
    make_preview_box,
    mouse_press_event,
    qapp,
    right_side_layout,
)

# Re-export helpers used by other modules that import from here.
__all__ = ["make_preview_box", "mouse_press_event", "qapp"]


def _tile(box: ColorPreviewBox) -> TransparentTile:
    return cast(Any, box)._trans_tile


# ── State API ────────────────────────────────────────────────────────────

class TestTransparentState:
    def test_initial_state_is_opaque(self, qapp):
        box = ColorPreviewBox()
        assert box.fg_transparent is False
        assert box.bg_transparent is False

    def test_set_transparent_fg(self, qapp):
        box = ColorPreviewBox()
        box.set_transparent("fg", True)
        assert box.fg_transparent is True
        assert box.bg_transparent is False

    def test_set_transparent_bg_and_clear(self, qapp):
        box = ColorPreviewBox()
        box.set_transparent("bg", True)
        assert box.bg_transparent is True
        box.set_transparent("bg", False)
        assert box.bg_transparent is False

    def test_preview_box_has_transparent_tile_child(self, qapp):
        box = ColorPreviewBox()
        assert isinstance(_tile(box), TransparentTile)


# ── TransparentTile metrics / geometry ───────────────────────────────────

class TestTransparentTileGeometry:
    def test_metrics_legacy_scales_with_scale(self, qapp):
        size_small, gap_small = TransparentTile.metrics(scale=0.5)
        size_big, gap_big = TransparentTile.metrics(scale=1.0)
        assert size_small < size_big
        assert size_big == 16
        # Gap between swatches and capsule is a fixed 2 px per spec.
        assert gap_small == 2
        assert gap_big == 2

    def test_capsule_width_at_least_double_height(self, qapp):
        assert TransparentTile.capsule_width(10, 100) == 30
        assert TransparentTile.capsule_width(16, 47) == 47
        assert TransparentTile.capsule_width(16, 30) == 30

    def test_ringless_tile_is_same_size_as_swatches(self, qapp):
        """Ringless: the transparent tile shares the swatch row size."""
        box = make_preview_box(canonical_layout())
        tile = _tile(box)
        fg_rect, _ = cast(Any, box._ringless_swatch_rects())
        assert tile.width() == fg_rect.width()
        assert tile.width() == 43
        assert tile.height() == fg_rect.height()
        assert tile.height() == 24

    @pytest.mark.parametrize("layout_factory, tile_is_leftmost", [
        (right_side_layout, True),
        (left_side_layout, False),
    ])
    def test_tile_hugs_innermost_edge_ringless(
        self, qapp, layout_factory, tile_is_leftmost,
    ):
        """Ringless: the transparent tile always sits nearest the window
        centre — left of the fg/bg pair on the right side, right of the pair
        on the left side. FG stays left of BG in both orientations."""
        layout = layout_factory()
        box = make_preview_box(layout)
        tile = _tile(box)
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())
        assert not tile.isHidden()
        assert fg_rect.right() < bg_rect.left()
        if tile_is_leftmost:
            assert tile.x() + tile.width() + layout.swatch_gap == pytest.approx(fg_rect.left())
        else:
            assert bg_rect.right() + layout.swatch_gap == pytest.approx(tile.x())
        assert tile.y() == pytest.approx(fg_rect.top())
        assert tile.y() + tile.height() <= box.height()

    def test_tile_above_circles_legacy_top_left(self, qapp):
        """Top-left anchor mirrors bottom-left: the transparent capsule sits
        ABOVE the fg/bg circles, not below them."""
        box = make_preview_box()
        box.resize(60, 60)
        box.resize_and_position(304, 30, 400, 100, "fg")  # default top-left
        tile = _tile(box)
        assert not tile.isHidden()
        assert tile.width() > 0
        assert tile.y() == 0  # capsule hugs the widget top
        assert tile.y() + tile.height() <= 60  # above the circle box
        fg_cx, fg_cy, fg_r, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()
        assert bg_cy - bg_r >= tile.height()  # circles start below the tile

    def test_tile_below_circles_legacy_bottom_left(self, qapp):
        """Bottom-left keeps the capsule below — the mirror of top-left."""
        box = make_preview_box()
        box.resize(60, 60)
        box.position_mode = "bottom-left"
        box.resize_and_position(304, 30, 400, 100, "fg")
        tile = _tile(box)
        assert not tile.isHidden()
        assert tile.width() > 0
        assert tile.y() >= 60  # below the circle box

    def test_legacy_geometry_is_vertically_mirrored_between_positions(self, qapp):
        """The top-left module is the vertical mirror of the bottom-left one:
        same circle arrangement (fg large at the outer corner pair), but the
        transparent tile flips from below to above."""
        box = make_preview_box()
        box.resize(60, 60)
        box.position_mode = "bottom-left"
        box.resize_and_position(304, 30, 400, 100, "fg")
        bottom = box.legacy_circle_geometry()
        tile_bottom_y = _tile(box).y()  # snapshot; same widget is re-placed below

        box.position_mode = "top-left"
        box.resize_and_position(304, 30, 400, 100, "fg")
        top = box.legacy_circle_geometry()
        tile_top_y = _tile(box).y()

        # Circle radii and x positions unchanged; only the y block flips.
        assert top[2] == bottom[2] and top[5] == bottom[5]
        assert top[0] == bottom[0] and top[3] == bottom[3]
        # Vertical mirror about the widget height (78 at 100% scale); the
        # 2px slack of the border-stroke compensation may shift the mirror.
        h = box.height()
        assert top[1] == pytest.approx(h - bottom[1], abs=2)  # fg cy
        assert top[4] == pytest.approx(h - bottom[4], abs=2)  # bg cy
        # Tile flips side: top-left above the circles, bottom-left below.
        assert tile_top_y < 20 < tile_bottom_y
        # Top-left: fg below bg (bottom of the module); bottom-left: fg first.
        assert top[1] > top[4]
        assert bottom[1] < bottom[4]

    def test_clicking_tile_emits_clicked(self, qapp):
        box = make_preview_box()
        calls = []

        class FakeParent:
            def set_active_transparent(self):
                calls.append("transparent")

        box._parent = FakeParent()
        tile = _tile(box)
        cx = tile.x() + tile.width() / 2
        cy = tile.y() + tile.height() / 2
        tile.mousePressEvent(mouse_press_event(cx, cy, Qt.MouseButton.LeftButton))
        assert calls == ["transparent"]

    def test_clicking_swatch_does_not_trigger_tile(self, qapp):
        from ui.ringless_mode import RinglessLayout
        layout = RinglessLayout(
            wheel_enabled=True, controls_enabled=True, controls_side="right",
            control_bar_height=39, margin=7,
            swatch_width=43, swatch_height=24, swatch_gap=5,
            corner_radius=4, button_gap=4,
        )
        box = make_preview_box(layout)
        calls = []

        class FakeParent:
            def select_fg_slot(self):
                calls.append("fg")
            def set_active_transparent(self):
                calls.append("transparent")

        box._parent = FakeParent()
        fg_rect, _ = cast(Any, box._ringless_swatch_rects())
        box.mousePressEvent(mouse_press_event(
            fg_rect.center().x(), fg_rect.center().y(), Qt.MouseButton.LeftButton
        ))
        assert calls == ["fg"]


# ── Paint safety ─────────────────────────────────────────────────────────

class TestTransparentPaint:
    def test_legacy_transparent_paint_renders(self, qapp):
        box = make_preview_box()
        box.resize(60, 60)
        box.resize_and_position(304, 30, 400, 100, "fg")
        box.fg_color = QColor(255, 0, 0)
        box.bg_color = QColor(0, 255, 0)
        box.set_transparent("fg", True)
        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )

    def test_ringless_transparent_paint_renders(self, qapp):
        from ui.ringless_mode import RinglessLayout
        layout = RinglessLayout(
            wheel_enabled=True, controls_enabled=True, controls_side="right",
            control_bar_height=39, margin=7,
            swatch_width=43, swatch_height=24, swatch_gap=5,
            corner_radius=4, button_gap=4,
        )
        box = make_preview_box(layout)
        box.fg_color = QColor(255, 0, 0)
        box.set_transparent("bg", True)
        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )

    def test_tile_repaints_highlight_on_transparent_state(self, qapp):
        """set_transparent / update_slot_borders must repaint the tile
        child — otherwise the blue active-highlight never appears."""
        from ui.transparent_swatch import TRANSPARENT_ACTIVE_COLOR
        box = make_preview_box()
        box.resize(80, 90)
        box.resize_and_position(304, 30, 400, 100, "fg")
        tile = _tile(box)

        # 激活 fg + fg 透明 → tile 应显示蓝色高亮（131,155,201）
        box.set_transparent("fg", True)
        qapp.processEvents()
        img = QImage(tile.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        tile.render(img)
        blue = any(
            img.pixelColor(x, y).red() == TRANSPARENT_ACTIVE_COLOR.red()
            and img.pixelColor(x, y).green() == TRANSPARENT_ACTIVE_COLOR.green()
            and img.pixelColor(x, y).blue() == TRANSPARENT_ACTIVE_COLOR.blue()
            for y in range(img.height())
            for x in range(img.width())
        )
        assert blue, "transparent tile highlight not painted after set_transparent"

        # 切槽到 bg（fg 不再激活）→ 高亮应消失
        box.update_slot_borders("bg")
        qapp.processEvents()
        img2 = QImage(tile.size(), QImage.Format.Format_ARGB32)
        img2.fill(Qt.GlobalColor.transparent)
        tile.render(img2)
        blue2 = any(
            img2.pixelColor(x, y).red() == TRANSPARENT_ACTIVE_COLOR.red()
            and img2.pixelColor(x, y).green() == TRANSPARENT_ACTIVE_COLOR.green()
            and img2.pixelColor(x, y).blue() == TRANSPARENT_ACTIVE_COLOR.blue()
            for y in range(img2.height())
            for x in range(img2.width())
        )
        assert not blue2, "highlight should clear after switching active slot"


# ── Highlight exclusivity: transparent active → only the tile is active ──

class TestHighlightExclusivity:
    """When the active slot is transparent, the fg/bg swatches must NOT
    show the active blue border — the transparent tile is the only
    highlighted element. Applies in both legacy and ringless modes."""

    def test_legacy_active_transparent_fg_circle_gets_no_active_border(self, qapp):
        from unittest.mock import patch

        box = make_preview_box()
        box.resize(60, 60)
        box.resize_and_position(304, 30, 400, 100, "fg")
        box.set_transparent("fg", True)

        with patch.object(box, "draw_circle", wraps=box.draw_circle) as spy:
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)
        # draw_circle(painter, cx, cy, r, color, active); with active_slot='fg'
        # the BG is painted first (inactive), then FG last.
        assert len(spy.call_args_list) == 2
        assert spy.call_args_list[0].args[5] is False  # bg
        assert spy.call_args_list[1].args[5] is False  # fg: NO blue border

    def test_legacy_opaque_active_fg_still_highlighted(self, qapp):
        """Control: without transparency the active circle keeps its border."""
        from unittest.mock import patch

        box = make_preview_box()
        box.resize(60, 60)
        box.resize_and_position(304, 30, 400, 100, "fg")

        with patch.object(box, "draw_circle", wraps=box.draw_circle) as spy:
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)
        assert len(spy.call_args_list) == 2
        assert spy.call_args_list[0].args[5] is False  # bg
        assert spy.call_args_list[1].args[5] is True   # fg keeps border

    def test_ringless_active_transparent_fg_swatch_gets_no_active_border(self, qapp):
        from unittest.mock import patch

        box = make_preview_box(canonical_layout())
        box.set_transparent("fg", True)
        box.active_slot = "fg"

        with patch.object(box, "_ringless_swatch",
                          wraps=box._ringless_swatch) as spy:
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)

        fg_calls = [c for c in spy.call_args_list if c.args[3] == box.fg_color]
        assert len(fg_calls) == 1
        assert fg_calls[0].args[4] is False

    def test_ringless_opaque_active_fg_still_highlighted(self, qapp):
        from unittest.mock import patch

        box = make_preview_box(canonical_layout())
        box.active_slot = "fg"

        with patch.object(box, "_ringless_swatch",
                          wraps=box._ringless_swatch) as spy:
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)

        fg_calls = [c for c in spy.call_args_list if c.args[3] == box.fg_color]
        assert len(fg_calls) == 1
        assert fg_calls[0].args[4] is True

    def test_ringless_transparent_tile_paints_highlight(self, qapp):
        """In ringless mode the tile still shows the blue highlight when the
        active slot is transparent (it is the ONLY highlighted element)."""
        from ui.transparent_swatch import TRANSPARENT_ACTIVE_COLOR

        box = make_preview_box(canonical_layout())
        box.set_transparent("bg", True)
        box.active_slot = "bg"
        tile = _tile(box)
        qapp.processEvents()

        img = QImage(tile.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        tile.render(img)
        blue = any(
            img.pixelColor(x, y).red() == TRANSPARENT_ACTIVE_COLOR.red()
            and img.pixelColor(x, y).green() == TRANSPARENT_ACTIVE_COLOR.green()
            and img.pixelColor(x, y).blue() == TRANSPARENT_ACTIVE_COLOR.blue()
            for y in range(img.height())
            for x in range(img.width())
        )
        assert blue, "ringless transparent tile highlight not painted"


class TestTransparentTileAntialiasingAndAlignment:
    """Regression tests for capsule border clipping / staircase artifacts on
    High-DPI screens (e.g. 150% DPI)."""

    def test_tile_never_uses_1bit_widget_mask(self, qapp):
        """A QRegion widget mask on TransparentTile is 1-bit integer-clipped
        and shaves the left/top antialiased border on 150% DPI."""
        box = make_preview_box()
        box.resize(80, 90)
        box.resize_and_position(304, 30, 400, 100, "fg")
        tile = _tile(box)
        assert tile.mask().isEmpty()

        box_rl = make_preview_box(canonical_layout())
        tile_rl = _tile(box_rl)
        assert tile_rl.mask().isEmpty()

    @pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
    def test_legacy_capsule_border_is_symmetric_across_dpr(self, qapp, dpr):
        box = make_preview_box()
        box.resize(80, 90)
        box.resize_and_position(304, 30, 400, 100, "fg")
        box.set_transparent("fg", True)
        tile = _tile(box)

        w_px = int(round(tile.width() * dpr))
        h_px = int(round(tile.height() * dpr))
        img = QImage(w_px, h_px, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        tile.render(img)

        def is_blue_border(c: QColor) -> bool:
            return c.alpha() > 80 and (c.blue() - c.red()) >= 25

        mid_y = h_px // 2
        left_blue = [x for x in range(w_px // 2) if is_blue_border(img.pixelColor(x, mid_y))]
        right_blue = [x for x in range(w_px // 2, w_px) if is_blue_border(img.pixelColor(x, mid_y))]
        assert left_blue and right_blue
        assert abs(len(left_blue) - len(right_blue)) <= 1, (dpr, left_blue, right_blue)

        mid_x = w_px // 2
        top_blue = [y for y in range(h_px // 2) if is_blue_border(img.pixelColor(mid_x, y))]
        bot_blue = [y for y in range(h_px // 2, h_px) if is_blue_border(img.pixelColor(mid_x, y))]
        assert top_blue and bot_blue
        assert abs(len(top_blue) - len(bot_blue)) <= 1, (dpr, top_blue, bot_blue)

    def test_legacy_circles_have_transparent_ring_gap_without_back_bleed(self, qapp):
        """Legacy FG/BG circles leave a transparent gap ring between the outer
        border ring and inner color disc, and the overlapped back circle is
        clipped out so it never bleeds into the front circle's gap ring."""
        import math
        from PyQt6.QtCore import QPoint
        from PyQt6.QtWidgets import QWidget

        box = make_preview_box()
        box.position_mode = "bottom-left"
        box.resize_and_position(304, 30, 400, 100, "fg")
        box.set_colors(QColor(134, 149, 213), QColor(255, 0, 0))

        dpr = 2.0
        img = QImage(
            int(box.width() * dpr),
            int(box.height() * dpr),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        box.render(img, QPoint(), flags=QWidget.RenderFlag.DrawChildren)

        fg_cx, fg_cy, fg_r, bg_cx, bg_cy, _ = box.legacy_circle_geometry()
        # Sample along the ray from FG center toward BG center right in the
        # annular gap (r - 2.0, between inner disc r-2.75 and outer stroke r-1.25).
        dx = bg_cx - fg_cx
        dy = bg_cy - fg_cy
        dist = math.hypot(dx, dy)
        gap_r = fg_r - 2.0
        gx = int(round((fg_cx + (dx / dist) * gap_r) * dpr))
        gy = int(round((fg_cy + (dy / dist) * gap_r) * dpr))
        gap_pixel = img.pixelColor(gx, gy)
        # Must be transparent (and must NOT contain the red BG circle underneath)
        assert gap_pixel.alpha() == 0, f"Expected transparent gap ring pixel, got {gap_pixel.getRgb()}"

