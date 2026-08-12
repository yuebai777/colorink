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
    make_preview_box,
    mouse_press_event,
    qapp,
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

    def test_tile_sits_left_of_fg_same_row_ringless(self, qapp):
        """Ringless: tile is left of fg with the same gap as fg→bg."""
        layout = canonical_layout()
        box = make_preview_box(layout)
        tile = _tile(box)
        fg_rect, _ = cast(Any, box._ringless_swatch_rects())
        assert not tile.isHidden()
        assert tile.x() + tile.width() + layout.swatch_gap == pytest.approx(fg_rect.left())
        assert tile.y() == pytest.approx(fg_rect.top())
        assert tile.y() + tile.height() <= box.height()

    def test_tile_below_circles_legacy(self, qapp):
        box = make_preview_box()
        box.resize(60, 60)
        box.resize_and_position(304, 30, 400, 100, "fg")
        tile = _tile(box)
        assert not tile.isHidden()
        assert tile.width() > 0
        assert tile.y() >= 60

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
