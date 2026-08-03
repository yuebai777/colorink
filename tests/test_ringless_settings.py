"""Tests for ringless configuration values, settings widget, and defaults.

RED phase: all tests should FAIL because ui/ringless_mode and
ui/ringless_settings modules do not exist yet.
"""
import os
from unittest.mock import patch

import pytest

from core.config import HOTKEY_CFG_NAME, load_hotkey_config

# ── Module import tests (should fail in RED because modules don't exist) ──


def test_ringless_mode_module_is_importable():
    from ui.ringless_mode import (
        ControlsSide,
        RinglessConfig,
        RinglessLayout,
        resolve_ringless_layout,
    )

    assert ControlsSide is not None
    assert RinglessConfig is not None
    assert RinglessLayout is not None
    assert resolve_ringless_layout is not None


def test_ringless_settings_module_is_importable():
    from ui.ringless_settings import RinglessSettingsWidget

    assert RinglessSettingsWidget is not None


# ── Value object tests (RED: RinglessConfig / resolve_ringless_layout) ──


class TestRinglessConfigFromValues:
    """Given raw boolean + side string, RinglessConfig.from_values produces correct config."""

    def test_enabled_left_returns_left_side(self):
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(True, "left")
        assert config.enabled is True
        assert config.controls_side == "left"

    def test_enabled_right_returns_right_side(self):
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(True, "right")
        assert config.enabled is True
        assert config.controls_side == "right"

    def test_disabled_left_stores_disabled(self):
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(False, "left")
        assert config.enabled is False
        assert config.controls_side == "left"

    def test_invalid_side_defaults_to_right(self):
        """Given invalid side text, from_values defaults to 'right'."""
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(True, "top")
        assert config.enabled is True
        assert config.controls_side == "right"

    def test_empty_side_defaults_to_right(self):
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(False, "")
        assert config.controls_side == "right"

    def test_control_bar_position_setting_is_preserved(self):
        from ui.ringless_mode import RinglessConfig

        config = RinglessConfig.from_values(True, "right", "bottom")
        assert config.control_bar_position == "bottom"


class TestResolveRinglessLayout:
    """Given config, page state, and scale, resolve_ringless_layout produces a RinglessLayout."""

    def test_enabled_wheel_active_scales_dimensions(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(enabled=True, controls_side="left")
        layout = resolve_ringless_layout(config, wheel_page_active=True, ui_scale=1.0)
        assert layout.wheel_enabled is True
        assert layout.controls_enabled is True
        assert layout.controls_side == "left"
        assert layout.control_bar_height == 30
        assert layout.margin == 7
        assert layout.swatch_width == 43
        assert layout.swatch_height == 24
        assert layout.swatch_gap == 5
        assert layout.corner_radius == 4
        assert layout.button_gap == 4

    def test_disabled_config_disables_both(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(enabled=False, controls_side="right")
        layout = resolve_ringless_layout(config, wheel_page_active=True, ui_scale=1.0)
        assert layout.wheel_enabled is False
        assert layout.controls_enabled is False  # enabled=False => controls off regardless

    def test_control_bar_can_move_to_bottom_without_disabling_ringless_wheel(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(
            enabled=True, controls_side="right", control_bar_position="bottom"
        )
        layout = resolve_ringless_layout(config, wheel_page_active=True, ui_scale=1.0)
        assert layout.wheel_enabled is True
        assert layout.controls_enabled is True
        assert layout.control_bar_position == "bottom"

    def test_enabled_but_wheel_inactive_disables_wheel_only(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(enabled=True, controls_side="right")
        layout = resolve_ringless_layout(
            config, wheel_page_active=False, ui_scale=1.0
        )
        assert layout.wheel_enabled is True
        assert layout.controls_enabled is True

    def test_scale_2x_doubles_dimensions(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(enabled=True, controls_side="right")
        layout = resolve_ringless_layout(config, wheel_page_active=True, ui_scale=2.0)
        assert layout.control_bar_height == 60
        assert layout.margin == 14
        assert layout.swatch_width == 86

    def test_scale_below_minimum_clamped(self):
        from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

        config = RinglessConfig(enabled=True, controls_side="left")
        layout = resolve_ringless_layout(config, wheel_page_active=True, ui_scale=0.0)
        # scale clamped to 0.01 → tiny values clamped to 1
        assert layout.control_bar_height == 1
        assert layout.margin == 1
        assert layout.swatch_width == 1


# ── Widget tests (RED: RinglessSettingsWidget) ──


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication for the test module (offscreen, no display needed)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestRinglessSettingsWidget:
    """Given a RinglessSettingsWidget, it exposes config get/set and UI state."""

    def test_initial_config_is_disabled_right(self, qapp):
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        config = widget.config()
        assert config.enabled is False
        assert config.controls_side == "right"

    def test_control_bar_setting_is_placed_under_slice_toggle(self, qapp):
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        layout = widget.layout()
        assert layout is not None
        assert layout.indexOf(widget.control_bar_position_row) == (
            layout.indexOf(widget.enabled_checkbox) + 1
        )

    def test_rgb_slice_module_option_is_available(self, qapp):
        from ui.settings_sidebar import SettingsSidebar

        # Use the real sidebar to verify the persisted module selector exposes
        # the optional RGB slice alongside HSV/HLS/LCH.
        sidebar = SettingsSidebar()
        assert sidebar.combo_module.findText("RGB") >= 0

    def test_control_bar_position_setting_can_select_bottom(self, qapp):
        from ui.ringless_mode import RinglessConfig
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        widget.set_config(RinglessConfig(True, "right", "bottom"))
        assert widget.config().control_bar_position == "bottom"
        assert not widget.side_row_wrapper.isHidden()

    def test_set_config_updates_checkbox_and_combo(self, qapp):
        from ui.ringless_mode import RinglessConfig
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        new_config = RinglessConfig(enabled=True, controls_side="left")
        widget.set_config(new_config)

        result = widget.config()
        assert result.enabled is True
        assert result.controls_side == "left"

    def test_disabled_config_hides_side_row(self, qapp):
        from ui.ringless_mode import RinglessConfig
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        disabled = RinglessConfig(enabled=False, controls_side="right")
        widget.set_config(disabled)
        assert widget.side_row_wrapper.isHidden()

    def test_enabled_config_shows_side_row_and_combo_reads_chinese(self, qapp):
        from ui.ringless_mode import RinglessConfig
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        enabled_left = RinglessConfig(enabled=True, controls_side="left")
        widget.set_config(enabled_left)
        assert not widget.side_row_wrapper.isHidden()
        assert widget.side_combo.currentText() == "左侧"

    def test_side_label_displays_approved_text(self, qapp):
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        assert widget.side_label.text() == "无色环双色位置"

    def test_changed_signal_emitted_on_checkbox_toggle(self, qapp):
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        emitted = []

        def on_changed():
            emitted.append(True)

        widget.changed.connect(on_changed)
        widget.enabled_checkbox.setChecked(True)
        assert len(emitted) == 1

    def test_set_config_does_not_emit_changed(self, qapp):
        """set_config() blocks signals — it should NOT emit changed."""
        from ui.ringless_mode import RinglessConfig
        from ui.ringless_settings import RinglessSettingsWidget

        widget = RinglessSettingsWidget()
        emitted = []

        def on_changed():
            emitted.append(True)

        widget.changed.connect(on_changed)
        widget.set_config(RinglessConfig(enabled=True, controls_side="left"))
        assert len(emitted) == 0


# ── Config defaults tests (RED: ringless keys don't exist yet) ──


def test_ringless_defaults_are_disabled_off_right():
    """First-run defaults: ringless disabled, side 'right'."""
    # Patch to force first-run path (no config file on disk)
    with patch("core.config.get_user_data_dir") as mock_dir:
        mock_dir.return_value = "/nonexistent"
        with patch("os.path.exists", return_value=False):
            config = load_hotkey_config()

    assert config["hideHueRing"] is False
    assert config["ringlessControlsSide"] == "right"
    assert config["ringlessControlBarPosition"] == "top"


def test_ringless_keys_merge_on_missing(tmp_path):
    """Existing config without ringless keys gets defaults merged in."""
    import json

    path = tmp_path / HOTKEY_CFG_NAME
    path.write_text(
        json.dumps({"pickKey": "F2", "colorSpaceModule": "lch"}),
        encoding="utf-8",
    )

    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        config = load_hotkey_config()

    assert config["pickKey"] == "F2"  # preserved
    assert config["colorSpaceModule"] == "lch"  # preserved
    assert config["hideHueRing"] is False  # merged
    assert config["ringlessControlsSide"] == "right"  # merged
    assert config["ringlessControlBarPosition"] == "top"  # merged
