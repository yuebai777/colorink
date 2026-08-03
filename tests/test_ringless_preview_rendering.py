"""Tests for ColorPreviewBox ringless rendering and mode restoration.

Covers offscreen-rendered border pixel evidence, LAB-state paint path,
circle-restoration proof via spies, and clipping constraints.
"""

from typing import Any, cast
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from ui.color_preview_box import _STROKE_PAD

from .test_ringless_preview_support import (
    canonical_layout,
    disabled_layout,
    lab_state_layout,
    make_preview_box,
    qapp,
)

# ── Offscreen rendering: border pixel evidence ────────────────────────────

class TestRinglessRendering:
    """Rectangles render with correct colors and distinct active/inactive borders."""

    def test_active_and_inactive_border_pixels_differ(self, qapp):
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(255, 0, 0)      # red
        box.bg_color = QColor(0, 0, 255)      # blue
        box.active_slot = "fg"

        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())

        # Sample a 3×3 region centred on the left border of the active FG swatch.
        # The 2.5 px active border (#5a94e2) should tint at least one pixel blue-ish.
        active_has_blue = False
        bx = int(fg_rect.left())
        by = int(fg_rect.center().y())
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                px = bx + dx
                py = by + dy
                if 0 <= px < image.width() and 0 <= py < image.height():
                    c = image.pixelColor(px, py)
                    if c.blue() > 50:  # #5a94e2 has blue=226
                        active_has_blue = True
        assert active_has_blue, "Active FG border region should have blue-tinted pixels from stroke"

        # Sample a 3×3 region centred on the left border of the inactive BG swatch.
        # The inactive 1.0 px border (#cccccc=204,204,204) should make at least
        # one pixel gray-ish (not pure blue fill).
        inactive_has_gray = False
        ibx = int(bg_rect.left())
        iby = int(bg_rect.center().y())
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                px = ibx + dx
                py = iby + dy
                if 0 <= px < image.width() and 0 <= py < image.height():
                    c = image.pixelColor(px, py)
                    # Gray-ish means channels are close
                    if abs(c.red() - c.green()) < 50 and abs(c.green() - c.blue()) < 50 and c.red() > 100:
                        inactive_has_gray = True
        assert inactive_has_gray, "Inactive BG border region should have gray-ish pixels from stroke"

    def test_fg_swatch_shows_fg_color_center(self, qapp):
        """Center of FG swatch pixel matches fg_color."""
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(255, 0, 0)  # red
        box.bg_color = QColor(0, 255, 0)  # green
        box.active_slot = "fg"

        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

        fg_rect, _ = cast(Any, box._ringless_swatch_rects())
        cx, cy = int(fg_rect.center().x()), int(fg_rect.center().y())
        pixel = image.pixelColor(cx, cy)
        assert pixel.red() > 200
        assert pixel.green() < 50

    def test_bg_swatch_shows_bg_color_center(self, qapp):
        """Center of BG swatch pixel matches bg_color."""
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(255, 0, 0)  # red
        box.bg_color = QColor(0, 255, 0)  # green
        box.active_slot = "bg"

        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

        _, bg_rect = cast(Any, box._ringless_swatch_rects())
        cx, cy = int(bg_rect.center().x()), int(bg_rect.center().y())
        pixel = image.pixelColor(cx, cy)
        assert pixel.green() > 200

    def test_fixed_dimensions_do_not_clip_active_stroke(self, qapp):
        """The widget size accommodates the 2.5px active border without clipping."""
        box = make_preview_box(canonical_layout())
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())

        # Widget width must be at least bg_rect.right() + _STROKE_PAD
        assert box.width() >= bg_rect.right() + _STROKE_PAD
        # Widget height must be at least sh + 2 * _STROKE_PAD
        assert box.height() >= fg_rect.height() + 2 * _STROKE_PAD


# ── LAB-state paint path ─────────────────────────────────────────────────

class TestLabStatePaintPath:
    """When controls_enabled=True but wheel_enabled=False (LAB page),
    ringless rectangles are drawn and legacy circles are NOT used."""

    def test_lab_state_uses_rectangles_not_circles(self, qapp):
        box = make_preview_box(lab_state_layout())
        box.fg_color = QColor(255, 0, 0)
        box.bg_color = QColor(0, 255, 0)

        with (
            patch.object(box, "draw_circle", wraps=box.draw_circle) as circle_spy,
            patch.object(
                box, "_draw_ringless_paint", wraps=box._draw_ringless_paint
            ) as rect_spy,
        ):
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)

            rect_spy.assert_called()
            circle_spy.assert_not_called()


# ── Mode restoration ─────────────────────────────────────────────────────

class TestRinglessModeRestoration:
    """When ringless is disabled, circles are drawn, rectangles are not,
    and position_mode/colors/active_slot are preserved."""

    def test_disable_ringless_draws_circles_not_rectangles(self, qapp):
        """After enabling then disabling ringless, paint uses circles."""
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(255, 0, 0)
        box.bg_color = QColor(0, 255, 0)
        box.active_slot = "fg"

        # Disable ringless
        box.set_ringless_layout(disabled_layout(), 300, 30)
        box.resize(60, 60)

        with (
            patch.object(box, "draw_circle", wraps=box.draw_circle) as circle_spy,
            patch.object(box, "_draw_ringless_paint", wraps=box._draw_ringless_paint) as rect_spy,
        ):
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            box.render(image)

            circle_spy.assert_called()
            rect_spy.assert_not_called()

    def test_position_mode_preserved_after_enable_disable(self, qapp):
        """position_mode is not changed by enabling/disabling ringless."""
        box = make_preview_box()
        original_mode = box.position_mode
        box.set_ringless_layout(canonical_layout(), 300, 30)
        assert box.position_mode == original_mode
        box.set_ringless_layout(disabled_layout(), 300, 30)
        assert box.position_mode == original_mode
