"""Lifecycle tests: actual unbound MainWindow methods against real
offscreen widgets (ColorPreviewBox, WheelPane, QPushButtons, QStackedWidget).
"""

import os

# ── Pre-populate mocks so MainWindow imports cleanly ─────────────────────
import sys as _s
import types
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QPushButton, QStackedWidget, QWidget

from ui.color_preview_box import ColorPreviewBox
from ui.picker_panes import LabPane, PaneWithModeButton, WheelPane
from ui.ringless_mode import RinglessLayout

for _m in ("brush_color_spaces","win32gui","win32api","win32con","win32process",
           "psutil","win32com","win32com.client","win32com.client.dynamic",
           "win32com.client.gencache","win32com.client.CLSIDToClass","pythoncom"):
    _s.modules[_m] = MagicMock()
setattr(_s.modules["brush_color_spaces"], "PSColorSpace", MagicMock())
for _a in ("GetForegroundWindow","GetWindowText","GetWindowLong","SetWindowLong",
            "IsWindowVisible","IsIconic","ShowWindowAsync","BringWindowToTop",
            "SetForegroundWindow","EnumWindows","GetWindowThreadProcessId",
            "GetParent","GetWindow","GetWindowTextLengthW"):
    setattr(_s.modules["win32gui"], _a, MagicMock(return_value=False))

from ui.main_window import MainWindow

# ── Canonical layouts (typed, immutable) ─────────────────────────────────

_CANONICAL = RinglessLayout(
    wheel_enabled=True, controls_enabled=True, controls_side="right",
    control_bar_height=30, margin=7, swatch_width=43, swatch_height=24,
    swatch_gap=5, corner_radius=4, button_gap=4)
_DISABLED = replace(_CANONICAL, wheel_enabled=False, controls_enabled=False)
_LEFT = replace(_CANONICAL, controls_side="left")

# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    return app

# ── Narrow harness: real widgets, bound MainWindow methods ───────────────

class _Harness:
    """Executes actual MainWindow methods against real key widgets."""
    def __init__(self):
        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget()); self.stack.addWidget(QWidget())
        self.pane_wheel = WheelPane(); self.pane_wheel.resize(400, 339)
        self.pane_lab = LabPane(); self.pane_lab.resize(400, 339)
        self.preview_box = ColorPreviewBox()
        self.preview_box.set_colors(QColor(255, 0, 0), QColor(0, 0, 255))
        self.preview_box.position_mode = "top-left"
        self.btn_mode_wheel = QPushButton("M", self.pane_wheel)
        self.btn_mode_lab = QPushButton("L", self.pane_lab)
        self.btn_module = QPushButton("H", self.pane_wheel)
        self.pane_wheel.set_mode_button(self.btn_mode_wheel)
        self.pane_wheel.set_module_button(self.btn_module)
        self.btn_lab_shape = QPushButton("□", self.pane_lab)
        self.btn_lab_harmony = QPushButton("和", self.pane_lab)
        self.pane_lab.set_mode_button(self.btn_mode_lab)
        self.pane_lab.set_module_button(self.btn_lab_shape)
        self.pane_lab.set_extra_button(self.btn_lab_harmony)
        self.color_wheel = MagicMock(set_ringless_layout=MagicMock(),
                                      set_color=MagicMock())
        self.lab_square = MagicMock(L=50, set_color=MagicMock(), avoid_top=0)
        self.lab_slider = MagicMock(set_lightness=MagicMock())
        self.lab_layout = MagicMock()
        self.sliders_container = MagicMock(
            sizeHint=MagicMock(return_value=MagicMock(
                height=MagicMock(return_value=264))))
        self.main_layout = MagicMock(
            contentsMargins=MagicMock(return_value=MagicMock(
                top=MagicMock(return_value=0), bottom=MagicMock(return_value=8))),
            spacing=MagicMock(return_value=4), activate=MagicMock())
        self.sliders_layout = MagicMock(activate=MagicMock())
        self.settings_sidebar = MagicMock(isVisible=MagicMock(return_value=False))
        self.apply_theme = MagicMock()
        self._adjust_content_height = MagicMock()
        self._update_lab_slider_gamut_range = MagicMock()
        self.update = MagicMock()
        self.active_slot = "fg"
        self.current_rgb = (128, 128, 128)
        self._fg_transparent = False
        self._bg_transparent = False
        self._source_space = "rgb"
        self._source_values = None
        self._fg_source_space = "rgb"
        self._fg_source_values = None
        self._bg_source_space = "rgb"
        self._bg_source_values = None
        self.update_ui_colors = MagicMock()
        self._project_color = MagicMock()
        self._color_from_source = MagicMock()
        self._lab_avoid_recorded: list[dict] = []
        self.cfg = {"hideHueRing": True, "ringlessControlsSide": "right",
                     "uiScale": 100, "showModuleSwitchButton": True,
                     "ui-theme": "gray", "sliderStyle": "default",
                     "fontSize": 100}
        self._win_w = 400
        self._win_h = 710

    def width(self): return self._win_w
    def height(self): return self._win_h
    def _update_lab_avoid(self):
        self._lab_avoid_recorded.append({
            "p_x": self.preview_box.x(), "p_w": self.preview_box.width(),
            "page": self.stack.currentIndex()})
    @property
    def title_bar(self):
        return MagicMock(height=MagicMock(return_value=28))
    def _bind(self):
        self.update_geometries = types.MethodType(
            MainWindow.update_geometries, self)
        self._sync_ringless_mode = types.MethodType(
            MainWindow._sync_ringless_mode, self)
        self.toggle_picker_mode = types.MethodType(
            MainWindow.toggle_picker_mode, self)
        self.update_mode_buttons_visibility = types.MethodType(
            MainWindow.update_mode_buttons_visibility, self)
        self.select_fg_slot = types.MethodType(MainWindow.select_fg_slot, self)
        self.select_bg_slot = types.MethodType(MainWindow.select_bg_slot, self)
        self._set_slot_transparent = types.MethodType(
            MainWindow._set_slot_transparent, self)

@pytest.fixture
def harness(qapp):
    h = _Harness(); h._bind(); return h

# ── Full lifecycle: toggle_picker_mode through actual MainWindow methods ──

class TestTogglePickerLifecycle:
    def test_wheel_to_lab_keeps_rectangle_preview(self, harness):
        h = harness; h.stack.setCurrentIndex(0)
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        rw = h.preview_box.width()
        assert rw > 0 and h.stack.minimumHeight() > 0
        h.toggle_picker_mode()  # wheel -> LAB
        # With controls_enabled on LAB, preview keeps rectangle sizing
        assert h.preview_box.width() == rw
        # Stack keeps minimum = ws + bar (controls enabled on LAB)
        assert h.stack.minimumHeight() > 0
        assert len(h._lab_avoid_recorded) >= 1
        assert h._lab_avoid_recorded[-1]["page"] == 1

    def test_lab_to_wheel_restores_ringless(self, harness):
        h = harness; h.stack.setCurrentIndex(0)
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        rw, rx = h.preview_box.width(), h.preview_box.x()
        h.toggle_picker_mode(); h.toggle_picker_mode()  # LAB round-trip
        assert h.preview_box.width() == rw and h.preview_box.x() == rx
        assert h.stack.minimumHeight() > 0

    def test_return_to_wheel_keeps_wheel_state_after_wheel_color_change(self, harness):
        h = harness
        h._last_update_source = "wheel"
        h.stack.setCurrentIndex(1)
        h.toggle_picker_mode()
        h.color_wheel.set_color.assert_not_called()

    def test_toggle_avoids_on_both_pages(self, harness):
        h = harness; h.stack.setCurrentIndex(0)
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        h._lab_avoid_recorded.clear()
        h.toggle_picker_mode(); h.toggle_picker_mode()
        assert len(h._lab_avoid_recorded) >= 2
        assert h._lab_avoid_recorded[0]["page"] == 1
        assert h._lab_avoid_recorded[-1]["page"] == 0

# ── Module button hidden via actual visibility + sync ────────────────────

class TestModuleHiddenLifecycle:
    def test_hidden_mode_at_outer_edge(self, harness):
        h = harness; h.cfg["showModuleSwitchButton"] = False
        h.stack.setCurrentIndex(0)
        h.update_mode_buttons_visibility()
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        assert h.btn_module.isHidden()
        assert h.btn_mode_wheel.x() == 7 and not h.btn_mode_wheel.isHidden()

    def test_hidden_left_side(self, harness):
        h = harness; h.cfg.update(showModuleSwitchButton=False,
                                    ringlessControlsSide="left")
        h.stack.setCurrentIndex(0)
        h.update_mode_buttons_visibility()
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        assert h.btn_module.isHidden()
        assert h.btn_mode_wheel.x() == 400 - 7 - 28

    def test_module_not_force_shown(self, harness):
        h = harness; h.cfg["showModuleSwitchButton"] = False
        h.stack.setCurrentIndex(0)
        for _ in range(2):
            h.update_mode_buttons_visibility()
            h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        assert h.btn_module.isHidden()

    def test_lab_toggles_follow_settings(self, harness):
        h = harness
        h.stack.setCurrentIndex(1)
        h.cfg["showLabShapeButton"] = False
        h.cfg["showLabHarmonyButton"] = False
        h.update_mode_buttons_visibility()
        assert h.btn_lab_shape.isHidden()
        assert h.btn_lab_harmony.isHidden()

        h.cfg["showLabShapeButton"] = True
        h.cfg["showLabHarmonyButton"] = True
        h.update_mode_buttons_visibility()
        assert not h.btn_lab_shape.isHidden()
        assert not h.btn_lab_harmony.isHidden()

    def test_lone_lab_harmony_button_anchors_to_edge(self, harness):
        h = harness
        h.stack.setCurrentIndex(1)
        h.cfg["showLabToggleButton"] = False
        h.cfg["showLabShapeButton"] = False
        h.cfg["showLabHarmonyButton"] = True
        h.update_mode_buttons_visibility()
        h._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        assert not h.btn_lab_harmony.isHidden()
        assert h.btn_lab_harmony.x() == 7  # controls_side right → left edge

# ── Preview geometry round-trip (real ColorPreviewBox, manual) ───────────

class TestPreviewGeometry:
    @pytest.fixture
    def box(self, qapp):
        pb = ColorPreviewBox()
        pb.set_colors(QColor(255, 0, 0), QColor(0, 0, 255))
        pb.position_mode = "top-left"; return pb

    def test_ringless_sets_fixed_size(self, box):
        box.resize_and_position(384, 28, 710, 300, "fg")
        box.set_ringless_layout(_CANONICAL, 400, 28)
        assert box.width() > 10 and box.width() != 384

    def test_legacy_sizing_restored(self, box):
        box.set_ringless_layout(_CANONICAL, 400, 28)
        rw = box.width()
        box.resize_and_position(384, 28, 710, 300, "fg")
        box.set_ringless_layout(_DISABLED, 400, 28)
        assert box.width() != rw  # legacy restored

    def test_round_trip_restores(self, box):
        box.set_ringless_layout(_CANONICAL, 400, 28)
        rw, rx = box.width(), box.x()
        box.resize_and_position(384, 28, 710, 300, "fg")
        box.set_ringless_layout(_DISABLED, 400, 28)
        box.resize_and_position(384, 28, 710, 300, "fg")
        box.set_ringless_layout(_CANONICAL, 400, 28)
        assert box.width() == rw and box.x() == rx

    def test_baseline_always_first(self, box):
        box.set_ringless_layout(_CANONICAL, 400, 28)
        box.resize_and_position(300, 28, 600, 250, "fg")
        box.set_ringless_layout(_CANONICAL, 400, 28)
        # Three-swatch row (43 wide, gap 5): FG left of BG, transparent on
        # the innermost side. Width is padding + 3 swatches + 2 gaps.
        assert box.width() == 3 * 2 + 43 * 3 + 5 * 2  # _STROKE_PAD=3


# ── Clicking a slot swatch restores its opaque highlight ─────────────────

class TestTransparentSlotClickRestoresHighlight:
    """Clicking the fg/bg swatch must bring back its highlight even when the
    slot is already active and transparent — one click, no tile round-trip."""

    @pytest.fixture
    def h(self, qapp):
        h = _Harness(); h._bind(); return h

    def test_click_active_transparent_fg_restores_opaque(self, h):
        h.preview_box.set_transparent("fg", True)
        h._fg_transparent = True
        h.select_fg_slot()
        assert h._fg_transparent is False
        assert h.preview_box.fg_transparent is False
        # Already active → no slot-change re-push, but the sync write must
        # carry the cleared flag.
        h._project_color.assert_not_called()

    def test_click_active_transparent_bg_restores_opaque(self, h):
        h.active_slot = "bg"
        h.current_rgb = (0, 0, 255)
        h.preview_box.set_transparent("bg", True)
        h._bg_transparent = True
        h.select_bg_slot()
        assert h._bg_transparent is False
        assert h.preview_box.bg_transparent is False

    def test_click_opaque_active_slot_changes_nothing(self, h):
        h.select_fg_slot()
        assert h._fg_transparent is False
        h._project_color.assert_not_called()

    def test_click_fg_while_bg_active_switches_and_clears(self, h):
        h.active_slot = "bg"
        h.preview_box.set_transparent("fg", True)
        h._fg_transparent = True
        h.select_fg_slot()
        assert h.active_slot == "fg"
        assert h._fg_transparent is False
        h._project_color.assert_called_once()
        assert h._project_color.call_args.kwargs["source"] == "slot_change"

    def test_click_fg_keeps_other_slot_transparency(self, h):
        h.preview_box.set_transparent("fg", True)
        h.preview_box.set_transparent("bg", True)
        h._fg_transparent = True
        h._bg_transparent = True
        h.select_fg_slot()
        assert h._fg_transparent is False
        assert h._bg_transparent is True
        assert h.preview_box.bg_transparent is True

    def test_active_transparent_clear_pushes_sync_write(self, h):
        h.sync_thread = MagicMock(
            isRunning=MagicMock(return_value=True), write_color=MagicMock())
        h.preview_box.set_transparent("fg", True)
        h._fg_transparent = True
        h.select_fg_slot()
        # The color always comes from the slot's own swatch (red here).
        h.sync_thread.write_color.assert_called_once_with(
            255, 0, 0, transparent=False, color_index=0)

    def test_switching_clear_pushes_slot_color(self, h):
        """While switching, the clear still pushes the *fg* color — the
        swatch color, not the old active slot's current_rgb."""
        h.sync_thread = MagicMock(
            isRunning=MagicMock(return_value=True), write_color=MagicMock())
        h.active_slot = "bg"
        h.current_rgb = (0, 0, 255)  # bg color
        h.preview_box.set_transparent("fg", True)
        h._fg_transparent = True
        h.select_fg_slot()
        h.sync_thread.write_color.assert_called_once_with(
            255, 0, 0, transparent=False, color_index=0)

# ── Module button gap (real widgets, manual) ─────────────────────────────

class TestModuleButtonGap:
    @pytest.fixture
    def pane(self, qapp):
        p = PaneWithModeButton(); p.resize(400, 300); return p
    @pytest.fixture
    def mode_btn(self, pane):
        b = QPushButton("M", pane); pane.set_mode_button(b); return b
    @pytest.fixture
    def module_btn(self, pane):
        b = QPushButton("X", pane); pane.set_module_button(b); return b

    def test_both_visible(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_CANONICAL)
        assert not mode_btn.isHidden() and module_btn.x() < mode_btn.x()

    def test_hidden_module_moves_mode_to_edge(self, pane, mode_btn, module_btn):
        pane.set_ringless_layout(_CANONICAL)
        module_btn.hide()
        pane.set_ringless_layout(_CANONICAL)
        assert module_btn.isHidden() and mode_btn.x() == 7

    def test_hidden_left_outer(self, pane, mode_btn, module_btn):
        pane.resize(400, 339); pane.set_ringless_layout(_LEFT)
        module_btn.hide(); pane.set_ringless_layout(_LEFT)
        assert module_btn.isHidden() and mode_btn.x() == 400 - 7 - 28

    def test_not_force_shown(self, pane, mode_btn, module_btn):
        module_btn.hide()
        pane.set_ringless_layout(_CANONICAL)
        pane.set_ringless_layout(_CANONICAL)
        assert module_btn.isHidden()


# ── LAB top control bar lifecycle ────────────────────────────────────────────


class TestLabTopControlBarLifecycle:
    def test_ringless_reserves_lab_control_bar(self, harness):
        harness.cfg["hideHueRing"] = True
        harness.stack.setCurrentIndex(1)
        harness._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        harness.lab_layout.setContentsMargins.assert_called_with(0, 30, 0, 0)

    def test_disabled_mode_restores_zero_lab_margin(self, harness):
        harness.cfg["hideHueRing"] = False
        harness._sync_ringless_mode(wheel_size=384, title_bar_height=28)
        harness.lab_layout.setContentsMargins.assert_called_with(0, 0, 0, 0)

    def test_ringless_layout_owns_lab_top_spacing(self, harness):
        harness.cfg["hideHueRing"] = True
        harness.lab_square.avoid_top = 99
        harness.preview_box.setGeometry(10, 10, 100, 40)
        harness.preview_box.show()
        MainWindow._update_lab_avoid(harness)
        assert harness.lab_square.avoid_top == 0
