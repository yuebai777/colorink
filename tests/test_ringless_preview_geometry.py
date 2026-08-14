"""Tests for ColorPreviewBox ringless geometry: swatch sizing, placement, hit-test."""

from typing import Any, cast

import pytest
from PyQt6.QtCore import Qt

from ui.color_preview_box import _STROKE_PAD, ColorPreviewBox
from ui.picker_panes import ringless_button_positions
from ui.ringless_mode import RinglessLayout

from .test_ringless_preview_support import (
    canonical_layout,
    disabled_layout,
    double_click_event,
    lab_state_layout,
    left_side_layout,
    make_preview_box,
    mouse_press_event,
    qapp,
    right_side_layout,
)

# ── Import / API surface ─────────────────────────────────────────────────

def test_color_preview_box_has_set_ringless_layout(qapp):
    box = ColorPreviewBox()
    assert hasattr(box, "set_ringless_layout")
    assert callable(getattr(box, "set_ringless_layout"))


# ── Swatch rectangle geometry ────────────────────────────────────────────

class TestRinglessSwatchGeometry:
    """Given a ringless layout at 100% scale, the FG/BG rectangles have
    the correct size, gap, corner radius, and ordering (FG left, BG right)."""

    def test_both_swatches_are_43x24_at_default_scale(self, qapp):
        box = make_preview_box(canonical_layout())
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())

        assert fg_rect.width() == pytest.approx(43.0)
        assert fg_rect.height() == pytest.approx(24.0)
        assert bg_rect.width() == pytest.approx(43.0)
        assert bg_rect.height() == pytest.approx(24.0)

    def test_gap_between_swatches_is_5px(self, qapp):
        """FG and BG stay adjacent (5 px gap); the transparent tile sits on
        the innermost side rather than between them."""
        box = make_preview_box(canonical_layout())
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())
        gap = bg_rect.left() - fg_rect.right()
        assert gap == pytest.approx(5.0)

    @pytest.mark.parametrize("layout_factory, tile_is_leftmost", [
        (right_side_layout, True),
        (left_side_layout, False),
    ])
    def test_transparent_tile_hugs_innermost_edge(
        self, qapp, layout_factory, tile_is_leftmost,
    ):
        """The transparent tile always sits nearest the window centre:
        leftmost when the group is anchored right, rightmost when left."""
        layout = layout_factory()
        box = make_preview_box(layout)
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())
        tile = box._trans_tile.geometry()

        assert tile.y() == pytest.approx(layout.swatch_padding)
        assert tile.width() == pytest.approx(fg_rect.width())
        assert tile.height() == pytest.approx(fg_rect.height())
        assert fg_rect.right() < bg_rect.left()
        if tile_is_leftmost:
            assert tile.x() < fg_rect.left()
        else:
            assert tile.x() > bg_rect.right()

    def test_fg_is_left_of_bg(self, qapp):
        """FG swatch is always left of BG regardless of controls_side."""
        for layout in (left_side_layout(), right_side_layout()):
            box = make_preview_box(layout)
            fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())
            assert fg_rect.center().x() < bg_rect.center().x()

    def test_returns_none_when_ringless_not_set(self, qapp):
        box = make_preview_box()  # no layout
        assert box._ringless_swatch_rects() is None

    def test_returns_none_when_controls_disabled(self, qapp):
        box = make_preview_box(disabled_layout())
        assert box._ringless_swatch_rects() is None

    def test_lab_state_with_controls_enabled_returns_rectangles(self, qapp):
        box = make_preview_box(lab_state_layout())
        assert box._ringless_swatch_rects() is not None


# ── Group placement (left vs right by controls_side) ─────────────────────

class TestRinglessGroupPlacement:
    """When controls_side is left/right, the widget hugs the corresponding
    window edge. The widget is vertically centred inside the control bar."""

    def test_right_side_places_widget_near_right_edge(self, qapp):
        box = make_preview_box(right_side_layout(), window_width=300, title_bar_h=30)
        assert box.x() == pytest.approx(300 - box.width() - 7, abs=1)

    def test_left_side_places_widget_near_left_edge(self, qapp):
        box = make_preview_box(left_side_layout(), window_width=300, title_bar_h=30)
        assert box.x() == pytest.approx(7, abs=1)

    def test_widget_shares_button_baseline(self, qapp):
        """Swatches and mode buttons use the same control-bar baseline."""
        layout = canonical_layout()
        box = make_preview_box(layout, window_width=400, title_bar_h=30)
        button_y = ringless_button_positions(400, 28, layout).y
        swatch_center_y = (box.y() - 30) + layout.swatch_padding + layout.swatch_height / 2
        button_center_y = button_y + 28 / 2
        assert swatch_center_y == pytest.approx(button_center_y + layout.swatch_offset_y)


# ── Hit-testing in ringless mode ─────────────────────────────────────────

class TestRinglessHitTesting:
    """When ringless is active, clicks on the FG/BG rectangles return the
    correct slot name; clicks outside return None."""

    def test_click_on_fg_rect_returns_fg(self, qapp):
        box = make_preview_box(canonical_layout())
        fg_rect, _ = cast(Any, box._ringless_swatch_rects())
        assert box._get_clicked_slot(fg_rect.center().x(), fg_rect.center().y()) == "fg"

    def test_click_on_bg_rect_returns_bg(self, qapp):
        box = make_preview_box(canonical_layout())
        _, bg_rect = cast(Any, box._ringless_swatch_rects())
        assert box._get_clicked_slot(bg_rect.center().x(), bg_rect.center().y()) == "bg"

    def test_click_outside_both_rects_returns_none(self, qapp):
        box = make_preview_box(canonical_layout())
        fg_rect, bg_rect = cast(Any, box._ringless_swatch_rects())

        # Above both
        assert box._get_clicked_slot(fg_rect.center().x(), 0) is None
        # Between (gap center)
        gap_x = (fg_rect.right() + bg_rect.left()) / 2.0
        assert box._get_clicked_slot(gap_x, fg_rect.center().y()) is None
        # Below both
        assert box._get_clicked_slot(fg_rect.center().x(), 100) is None

    def test_ringless_hit_test_uses_equal_cached_geometry(self, qapp):
        """Sequential calls return equivalent (not necessarily identical) rects."""
        box = make_preview_box(canonical_layout())
        rects_a = box._ringless_swatch_rects()
        rects_b = box._ringless_swatch_rects()
        assert rects_a is not None
        assert rects_b is not None
        assert rects_a[0] == rects_b[0]
        assert rects_a[1] == rects_b[1]

    def test_legacy_hit_test_returns_exact_expected_slot(self, qapp):
        """Without ringless, _get_clicked_slot computes circle geometry exactly.
        Box at 60×60, click at (10, 50), active_slot='fg' → FG circle hit."""
        box = make_preview_box()
        box.resize(60, 60)
        # (10, 50) is near the FG circle (large, bottom-left in top-left mode).
        # With active_slot='fg', z-order checks FG first.
        assert box._get_clicked_slot(10, 50) == "fg"
