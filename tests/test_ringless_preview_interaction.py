"""Tests for ColorPreviewBox ringless interaction semantics and paint safety.

Covers left-click select, right-click copy-menu routing, double-click swap,
legacy circle rendering, and module size check.
"""

import os
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from .test_ringless_preview_support import (
    canonical_layout,
    double_click_event,
    make_preview_box,
    mouse_press_event,
    qapp,
)


# ── Interaction semantics ────────────────────────────────────────────────

class TestRinglessInteractionSemantics:
    """Left-click select, right-click copy-menu routing, double-click swap
    all work correctly in ringless mode."""

    def test_left_click_selects_fg_when_hit(self, qapp):
        box = make_preview_box(canonical_layout())
        fg_rect, _ = box._ringless_swatch_rects()
        assert fg_rect is not None

        calls = []

        class FakeParent:
            def select_fg_slot(self): calls.append("fg_selected")
            def select_bg_slot(self): calls.append("bg_selected")
            def swap_colors(self): calls.append("swapped")

        box.parent = FakeParent()
        cx, cy = fg_rect.center().x(), fg_rect.center().y()
        box.mousePressEvent(mouse_press_event(cx, cy, Qt.MouseButton.LeftButton))
        assert calls == ["fg_selected"]

    def test_left_click_selects_bg_when_hit(self, qapp):
        box = make_preview_box(canonical_layout())
        _, bg_rect = box._ringless_swatch_rects()
        assert bg_rect is not None

        calls = []

        class FakeParent:
            def select_fg_slot(self): calls.append("fg_selected")
            def select_bg_slot(self): calls.append("bg_selected")
            def swap_colors(self): calls.append("swapped")

        box.parent = FakeParent()
        cx, cy = bg_rect.center().x(), bg_rect.center().y()
        box.mousePressEvent(mouse_press_event(cx, cy, Qt.MouseButton.LeftButton))
        assert calls == ["bg_selected"]

    def test_right_click_routes_to_menu_with_correct_color(self, qapp):
        """Right-click calls _show_color_context_menu with the correct color."""
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(10, 20, 30)
        fg_rect, _ = box._ringless_swatch_rects()
        assert fg_rect is not None

        with patch.object(box, "_show_color_context_menu") as spy_menu:
            cx, cy = fg_rect.center().x(), fg_rect.center().y()
            box.mousePressEvent(mouse_press_event(cx, cy, Qt.MouseButton.RightButton))
            spy_menu.assert_called_once()
            called_color = spy_menu.call_args[0][0]
            assert called_color.red() == 10
            assert called_color.green() == 20
            assert called_color.blue() == 30

    def test_double_click_calls_swap(self, qapp):
        box = make_preview_box(canonical_layout())
        fg_rect, _ = box._ringless_swatch_rects()
        assert fg_rect is not None

        calls = []

        class FakeParent:
            def select_fg_slot(self): pass
            def select_bg_slot(self): pass
            def swap_colors(self): calls.append("swapped")

        box.parent = FakeParent()
        cx, cy = fg_rect.center().x(), fg_rect.center().y()
        box.mouseDoubleClickEvent(double_click_event(cx, cy))
        assert calls == ["swapped"]


# ── paintEvent safety ────────────────────────────────────────────────────

class TestPaintEventSafety:
    """paintEvent renders without crash in both ringless and legacy modes."""

    def test_ringless_paint_renders_nonblank(self, qapp):
        box = make_preview_box(canonical_layout())
        box.fg_color = QColor(255, 0, 0)
        box.bg_color = QColor(0, 255, 0)
        box.active_slot = "fg"

        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

        has_content = any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )
        assert has_content, "Ringless paint should produce visible pixels"

    def test_legacy_circle_paint_renders_nonblank(self, qapp):
        """Without ringless layout, circle rendering produces visible pixels."""
        box = make_preview_box()
        box.resize(60, 60)
        box.fg_color = QColor(255, 0, 0)
        box.bg_color = QColor(0, 255, 0)
        box.active_slot = "fg"

        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

        has_content = any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )
        assert has_content, "Legacy circle paint should produce visible pixels"


# ── Module LOC check ─────────────────────────────────────────────────────

def test_color_preview_box_module_is_under_250_pure_loc():
    """Ensure ui/color_preview_box.py stays under the 250 pure LOC ceiling."""
    module_path = os.path.join(
        os.path.dirname(__file__), "..", "ui", "color_preview_box.py"
    )
    with open(module_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    pure_loc = 0
    for line in lines:
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        pure_loc += 1

    assert pure_loc <= 250, (
        f"color_preview_box.py has {pure_loc} pure LOC, exceeds 250 ceiling"
    )
