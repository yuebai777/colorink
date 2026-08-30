"""Ringless pane button-positioning tests: pure function + integration."""

import os
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from ui.picker_panes import (
    ButtonPositions,
    LabPane,
    PaneWithModeButton,
    ringless_button_positions,
)
from ui.ringless_mode import RinglessLayout

# ── QApplication fixture ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp_module():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── Layout helpers ────────────────────────────────────────────────────────

def _layout(**overrides) -> RinglessLayout:
    defaults = dict(
        wheel_enabled=True, controls_enabled=True, controls_side="right",
        control_bar_height=39, margin=7,
        swatch_width=43, swatch_height=24, swatch_gap=5,
        corner_radius=4, button_gap=4,
    )
    return RinglessLayout(**cast(Any, defaults | overrides))


def _disabled_layout() -> RinglessLayout:
    return _layout(wheel_enabled=False, controls_enabled=False)


# ── ButtonPositions dataclass ─────────────────────────────────────────────

class TestButtonPositions:

    def test_fields_are_correct(self):
        bp = ButtonPositions(module_x=10, mode_x=46, y=5)
        assert bp.module_x == 10
        assert bp.mode_x == 46
        assert bp.y == 5

    def test_is_frozen(self):
        bp = ButtonPositions(module_x=1, mode_x=2, y=3)
        with pytest.raises(FrozenInstanceError):
            setattr(bp, "module_x", 99)

    def test_is_hashable(self):
        bp = ButtonPositions(module_x=1, mode_x=2, y=3)
        _ = hash(bp)


# ── ringless_button_positions() pure function ─────────────────────────────

class TestRinglessButtonPositionsRight:
    """controls_side=right: swatches right → buttons left, module left of mode."""

    def test_module_at_margin(self):
        pos = ringless_button_positions(400, 28, _layout())
        assert pos.module_x == 7

    def test_mode_right_of_module(self):
        pos = ringless_button_positions(400, 28, _layout())
        assert pos.mode_x == 7 + 28 + 4

    def test_module_left_of_mode(self):
        pos = ringless_button_positions(300, 28, _layout())
        assert pos.module_x < pos.mode_x

    def test_y_centered_in_control_bar(self):
        pos = ringless_button_positions(400, 28, _layout(control_bar_height=39))
        assert pos.y == (39 - 28) // 2

    def test_y_centered_with_odd_difference(self):
        pos = ringless_button_positions(400, 28, _layout(control_bar_height=40))
        assert pos.y == (40 - 28) // 2

    def test_pane_width_irrelevant_for_right(self):
        p1 = ringless_button_positions(200, 28, _layout())
        p2 = ringless_button_positions(800, 28, _layout())
        assert p1.module_x == p2.module_x
        assert p1.mode_x == p2.mode_x


class TestRinglessButtonPositionsLeft:
    """controls_side=left: swatches left → buttons right, module left of mode."""

    def test_mode_at_right_edge(self):
        pos = ringless_button_positions(400, 28, _layout(controls_side="left"))
        assert pos.mode_x == 400 - 7 - 28

    def test_module_left_of_mode(self):
        pos = ringless_button_positions(400, 28, _layout(controls_side="left"))
        assert pos.module_x == 400 - 7 - 28 - 4 - 28
        assert pos.module_x < pos.mode_x

    def test_module_left_of_mode_on_small_pane(self):
        pos = ringless_button_positions(100, 28, _layout(controls_side="left"))
        assert pos.module_x < pos.mode_x

    def test_y_same_as_right_side(self):
        pos = ringless_button_positions(
            400, 28, _layout(controls_side="left", control_bar_height=39))
        assert pos.y == (39 - 28) // 2


class TestRinglessButtonPositionsScaled:

    def test_larger_button_pushes_mode_right(self):
        p_small = ringless_button_positions(400, 28, _layout())
        p_large = ringless_button_positions(400, 40, _layout())
        assert p_large.mode_x > p_small.mode_x

    def test_larger_gap_pushes_mode_right(self):
        p_tight = ringless_button_positions(400, 28, _layout(button_gap=4))
        p_loose = ringless_button_positions(400, 28, _layout(button_gap=10))
        assert p_loose.mode_x > p_tight.mode_x

    def test_taller_bar_changes_y_only(self):
        pos_39 = ringless_button_positions(400, 28, _layout(control_bar_height=39))
        pos_50 = ringless_button_positions(400, 28, _layout(control_bar_height=50))
        assert pos_50.y > pos_39.y
        assert pos_39.module_x == pos_50.module_x
        assert pos_39.mode_x == pos_50.mode_x


# ── PaneWithModeButton integration (real QPushButtons) ────────────────────

class TestPaneRinglessIntegration:

    @pytest.fixture
    def qapp(self, qapp_module):
        return qapp_module

    @pytest.fixture
    def pane(self, qapp):
        p = PaneWithModeButton()
        p.resize(400, 300)
        return p

    @pytest.fixture
    def mode_btn(self, pane):
        btn = QPushButton("M", pane)
        btn.setToolTip("mode")
        pane.set_mode_button(btn)
        return btn

    @pytest.fixture
    def module_btn(self, pane):
        btn = QPushButton("X", pane)
        btn.setToolTip("module")
        pane.set_module_button(btn)
        return btn

    @pytest.fixture
    def extra_btn(self, pane, mode_btn, module_btn):
        btn = QPushButton("E", pane)
        btn.setToolTip("extra")
        pane.set_extra_button(btn)
        return btn

    # ── Legacy bottom-right (no layout / disabled) ─────────────────────

    def test_no_layout_uses_bottom_right(self, pane, mode_btn):
        pane.resize(400, 300)
        assert mode_btn.x() == 400 - 6 - 28
        assert mode_btn.y() == 300 - 6 - 28

    def test_no_layout_module_left_of_mode(self, pane, mode_btn, module_btn):
        pane.resize(400, 300)
        assert module_btn.x() == 400 - 6 - 28 - 4 - 28
        assert module_btn.y() == 300 - 6 - 28
        assert module_btn.x() < mode_btn.x()

    def test_no_layout_extra_left_of_module(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.resize(400, 300)
        assert extra_btn.x() == 400 - 6 - 28 - 4 - 28 - 4 - 28
        assert extra_btn.y() == 300 - 6 - 28
        assert extra_btn.x() < module_btn.x()

    def test_legacy_module_moves_to_corner_when_mode_hidden(
            self, pane, mode_btn, module_btn):
        pane.resize(400, 300)
        mode_btn.hide()
        pane._reposition_mode_button()
        # Lone visible button anchors to the bottom-right edge (no reserved gap)
        assert module_btn.x() == 400 - 6 - 28
        assert module_btn.y() == 300 - 6 - 28

    def test_legacy_extra_uses_mode_slot_when_module_hidden(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.resize(400, 300)
        module_btn.hide()
        pane._reposition_mode_button()
        assert extra_btn.x() == 400 - 6 - 28 - 4 - 28
        assert extra_btn.y() == 300 - 6 - 28

    def test_legacy_only_extra_anchors_to_corner(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.resize(400, 300)
        mode_btn.hide()
        module_btn.hide()
        pane._reposition_mode_button()
        # The single visible toggle takes the bottom-right corner.
        assert extra_btn.x() == 400 - 6 - 28
        assert extra_btn.y() == 300 - 6 - 28

    def test_disabled_layout_uses_bottom_right(self, pane, mode_btn):
        pane.set_ringless_layout(_disabled_layout())
        pane.resize(400, 300)
        assert mode_btn.x() == 400 - 6 - 28
        assert mode_btn.y() == 300 - 6 - 28

    def test_disabled_layout_module_left_of_mode(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_disabled_layout())
        pane.resize(400, 300)
        assert module_btn.x() < mode_btn.x()
        assert module_btn.y() == mode_btn.y() == 300 - 6 - 28

    # ── Ringless top-bar anchoring ────────────────────────────────────

    def test_ringless_right_buttons_anchor_left(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        assert module_btn.x() == 7
        assert mode_btn.x() == 7 + 28 + 4
        assert module_btn.y() == 5
        assert mode_btn.y() == 5
        assert module_btn.width() == 28
        assert mode_btn.width() == 28

    def test_ringless_left_buttons_anchor_right(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        mode_x = 400 - 7 - 28
        module_x = mode_x - 4 - 28
        assert mode_btn.x() == mode_x
        assert module_btn.x() == module_x
        assert module_btn.y() == 5
        assert mode_btn.y() == 5

    def test_ringless_right_extra_grows_toward_center(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        assert module_btn.x() == 7
        assert mode_btn.x() == 7 + 28 + 4
        # Extra button sits to the right of the mode button (toward center),
        # so it never runs off the left edge.
        assert extra_btn.x() == 7 + 28 + 4 + 28 + 4
        assert extra_btn.y() == 5

    def test_ringless_left_extra_left_of_module(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        mode_x = 400 - 7 - 28
        module_x = mode_x - 4 - 28
        assert mode_btn.x() == mode_x
        assert module_btn.x() == module_x
        assert extra_btn.x() == module_x - 4 - 28
        assert extra_btn.y() == 5

    def test_ringless_right_extra_keeps_gap_when_mode_hidden(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        mode_btn.hide()
        pane._reposition_mode_button()
        # Without the mode button the extra follows the module button
        # immediately instead of leaving an empty slot.
        assert module_btn.x() == 7
        assert extra_btn.x() == 7 + 28 + 4

    def test_ringless_left_extra_keeps_gap_when_module_hidden(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        module_btn.hide()
        pane._reposition_mode_button()
        mode_x = 400 - 7 - 28
        assert mode_btn.x() == mode_x
        assert extra_btn.x() == mode_x - 4 - 28

    def test_ringless_right_only_extra_anchors_to_edge(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        mode_btn.hide()
        module_btn.hide()
        pane._reposition_mode_button()
        assert extra_btn.x() == 7
        assert extra_btn.y() == 5

    def test_ringless_left_only_extra_anchors_to_edge(
            self, pane, mode_btn, module_btn, extra_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        mode_btn.hide()
        module_btn.hide()
        pane._reposition_mode_button()
        assert extra_btn.x() == 400 - 7 - 28
        assert extra_btn.y() == 5

    def test_ringless_only_mode_button(self, pane, mode_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        assert mode_btn.x() == 7
        assert mode_btn.y() == 5

    def test_ringless_only_mode_button_left_side(self, pane, mode_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        assert mode_btn.x() == 400 - 7 - 28
        assert mode_btn.y() == 5

    def test_lone_lab_toggle_anchors_to_edge(self, qapp):
        """A lone visible toggle anchors to the control-bar edge.

        Even when the wheel pane keeps a module button visible, hidden
        buttons must not reserve a slot inside the cluster — a single
        toggle sinks to the outermost edge.
        """
        wheel = PaneWithModeButton()
        lab = LabPane()
        wheel.resize(400, 339)
        lab.resize(400, 339)
        wheel_mode = QPushButton("W", wheel)
        wheel_module = QPushButton("X", wheel)
        lab_mode = QPushButton("L", lab)
        wheel.set_mode_button(wheel_mode)
        wheel.set_module_button(wheel_module)
        lab.set_mode_button(lab_mode)
        lab.set_module_slot_reserved(True)
        layout = _layout()
        wheel.set_ringless_layout(layout)
        lab.set_ringless_layout(layout)
        # Wheel: module at edge, mode beside it.
        assert wheel_mode.x() == 7 + 28 + 4
        # Lab: only the mode toggle is visible → it takes the edge slot.
        assert lab_mode.x() == 7
        assert lab_mode.y() == 5

    # ── Hidden module button ──────────────────────────────────────────

    def test_hidden_module_mode_to_outer_right(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout())
        module_btn.hide()
        pane.set_module_slot_reserved(False)
        pane.resize(400, 339)
        pane._reposition_mode_button()
        assert mode_btn.x() == 7
        assert mode_btn.y() == 5

    def test_hidden_module_mode_to_outer_left(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        module_btn.hide()
        pane.set_module_slot_reserved(False)
        pane.resize(400, 339)
        pane._reposition_mode_button()
        assert mode_btn.x() == 400 - 7 - 28
        assert mode_btn.y() == 5

    def test_hidden_module_not_repositioned(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout())
        module_btn.hide()
        pane.set_module_slot_reserved(False)
        pane.resize(500, 400)
        pane._reposition_mode_button()
        assert module_btn.isHidden()
        assert mode_btn.x() == 7

    # ── Reposition triggers ───────────────────────────────────────────

    def test_set_ringless_layout_triggers_reposition(self, pane, mode_btn, module_btn):
        pane.resize(400, 339)
        pane._reposition_mode_button()
        assert mode_btn.y() > 300  # bottom-right at height 339
        pane.set_ringless_layout(_layout())
        assert mode_btn.y() == 5
        assert module_btn.y() == 5

    def test_resize_repositions_ringless(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout(controls_side="left"))
        pane.resize(400, 339)
        pane._reposition_mode_button()
        mode_x1 = mode_btn.x()
        pane.resize(500, 339)
        pane._reposition_mode_button()
        assert mode_btn.x() == 500 - 7 - 28
        assert mode_btn.x() > mode_x1

    def test_set_mode_button_repositions(self, qapp, pane, module_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        btn = QPushButton("M2", pane)
        pane.set_mode_button(btn)
        assert btn.x() == 7 + 28 + 4
        assert btn.y() == 5

    def test_set_module_button_repositions(self, qapp, pane, mode_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        btn = QPushButton("X2", pane)
        pane.set_module_button(btn)
        assert btn.x() == 7
        assert btn.y() == 5

    def test_set_metrics_repositions_ringless(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout())
        pane.resize(400, 339)
        pane.set_mode_button_metrics(36, 10)
        assert mode_btn.width() == 36
        assert mode_btn.height() == 36
        assert mode_btn.y() == (39 - 36) // 2

    # ── Non-regression: callbacks / tooltips / visibility untouched ────

    def test_layout_does_not_alter_tooltip(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_layout())
        assert mode_btn.toolTip() == "mode"
        assert module_btn.toolTip() == "module"

    def test_layout_does_not_alter_cursor(self, pane, mode_btn, module_btn):
        mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pane.set_ringless_layout(_layout())
        assert mode_btn.cursor().shape() == Qt.CursorShape.PointingHandCursor
