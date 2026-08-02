"""Integration tests for MainWindow._sync_ringless_mode orchestration.

Uses unbound method calls on SimpleNamespace fixtures — no real MainWindow
construction, no OS/thread/tray side effects.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# ── Pre-populate sys.modules with mocks for external deps ──
_MOCK_MODS = [
    "brush_color_spaces", "win32gui", "win32api", "win32con",
    "win32process", "psutil", "win32com", "win32com.client",
    "win32com.client.dynamic", "win32com.client.gencache",
    "win32com.client.CLSIDToClass", "pythoncom",
]
for _m in _MOCK_MODS:
    sys.modules[_m] = MagicMock()
sys.modules["brush_color_spaces"].PSColorSpace = MagicMock()
for _a in ("GetForegroundWindow", "GetWindowText", "GetWindowLong", "SetWindowLong",
            "IsWindowVisible", "IsIconic", "ShowWindowAsync", "BringWindowToTop",
            "SetForegroundWindow", "EnumWindows", "GetWindowThreadProcessId",
            "GetParent", "GetWindow", "GetWindowTextLengthW"):
    setattr(sys.modules["win32gui"], _a, MagicMock(return_value=False))

from ui.main_window import MainWindow


# ── Shared fixture factories ────────────────────────────────────────────────


def _cfg(**overrides):
    defaults = {"hideHueRing": False, "ringlessControlsSide": "right",
                "uiScale": 100, "showModuleSwitchButton": True}
    return defaults | overrides


def _fixture(*, cfg_overrides=None, stack_index=0, window_width=400,
             title_bar_height=28) -> SimpleNamespace:
    return SimpleNamespace(
        cfg=_cfg(**(cfg_overrides or {})),
        stack=MagicMock(currentIndex=MagicMock(return_value=stack_index)),
        preview_box=MagicMock(), color_wheel=MagicMock(), pane_wheel=MagicMock(),
        pane_lab=MagicMock(),
        lab_layout=MagicMock(),
        width=MagicMock(return_value=window_width),
        title_bar=MagicMock(height=MagicMock(return_value=title_bar_height)),
    )


def _sync(f, *, ws=None, tbh=None):
    return MainWindow._sync_ringless_mode(f, wheel_size=ws, title_bar_height=tbh)


# ── Default-off ─────────────────────────────────────────────────────────────


class TestSyncDisabled:
    def test_wheel_disabled_layout(self):
        f = _fixture(); _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is False

    def test_preview_disabled_layout(self):
        f = _fixture(); _sync(f)
        args = f.preview_box.set_ringless_layout.call_args[0]
        assert args[0].controls_enabled is False  # disabled → disabled layout

    def test_stack_min_cleared_wheel(self):
        f = _fixture(); _sync(f)
        f.stack.setMinimumHeight.assert_called_once_with(0)

    def test_stack_min_cleared_lab(self):
        f = _fixture(stack_index=1); _sync(f)
        f.stack.setMinimumHeight.assert_called_once_with(0)


# ── Enabled wheel page ──────────────────────────────────────────────────────


class TestSyncEnabledWheel:
    def test_wheel_controls_enabled(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        layout = f.color_wheel.set_ringless_layout.call_args[0][0]
        assert layout.controls_enabled is True and layout.wheel_enabled is True

    def test_preview_controls_enabled(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        layout, ww, tbh, _vh = f.preview_box.set_ringless_layout.call_args[0]
        assert layout.controls_enabled is True and ww == 400 and tbh == 28

    def test_pane_controls_enabled(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        assert f.pane_wheel.set_ringless_layout.call_args[0][0].controls_enabled is True

    def test_stack_min(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, window_width=400)
        _sync(f, ws=384)
        bar_h = f.preview_box.set_ringless_layout.call_args[0][0].control_bar_height
        f.stack.setMinimumHeight.assert_called_once_with(384 + bar_h)

    def test_stack_min_default_ws(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, window_width=500); _sync(f)
        bar_h = f.preview_box.set_ringless_layout.call_args[0][0].control_bar_height
        f.stack.setMinimumHeight.assert_called_once_with(500 - 16 + bar_h)


# ── Enabled LAB page ────────────────────────────────────────────────────────


class TestSyncEnabledLab:
    def test_lab_keeps_controls_and_disables_wheel(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, stack_index=1); _sync(f)
        layout = f.color_wheel.set_ringless_layout.call_args[0][0]
        assert layout.wheel_enabled is True
        assert layout.controls_enabled is True

    def test_lab_stack_minimum_includes_control_bar(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, stack_index=1)
        _sync(f, ws=384)
        layout = f.preview_box.set_ringless_layout.call_args[0][0]
        f.stack.setMinimumHeight.assert_called_once_with(
            384 + layout.control_bar_height
        )

    def test_lab_pane_receives_top_bar_layout(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, stack_index=1)
        _sync(f, ws=384)
        f.pane_lab.set_ringless_layout.assert_called_once()
        f.lab_layout.setContentsMargins.assert_called_once_with(
            0, 30, 0, 0
        )


# ── Side & restoration ──────────────────────────────────────────────────────


class TestSyncSide:
    def test_controls_side_left(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "ringlessControlsSide": "left"})
        _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_side == "left"

    def test_invalid_side_defaults_right(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "ringlessControlsSide": "garbage"})
        _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_side == "right"

    def test_disable_restores(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        # First call: enabled → stack minimum > 0
        first_min = f.stack.setMinimumHeight.call_args_list[0][0][0]
        assert first_min > 0, f"expected stack min > 0 when enabled, got {first_min}"
        # Second call: disabled → stack minimum = 0
        f.cfg["hideHueRing"] = False; _sync(f)
        last_min = f.stack.setMinimumHeight.call_args_list[-1][0][0]
        assert last_min == 0, f"expected stack min 0 when disabled, got {last_min}"
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is False


# ── UI scale ────────────────────────────────────────────────────────────────


class TestSyncScaling:
    def test_scale_150(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "uiScale": 150}); _sync(f)
        layout = f.color_wheel.set_ringless_layout.call_args[0][0]
        assert layout.control_bar_height == round(30 * 1.5)
        assert layout.swatch_width == round(43 * 1.5)

    def test_scale_50(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "uiScale": 50}); _sync(f)
        layout = f.color_wheel.set_ringless_layout.call_args[0][0]
        assert layout.control_bar_height == max(1, round(30 * 0.5))
        assert layout.swatch_width == max(1, round(43 * 0.5))


# ── LAB round-trip ──────────────────────────────────────────────────────────


class TestSyncLabRoundTrip:
    def test_wheel_lab_wheel_restores(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].wheel_enabled is True
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is True
        f.stack.currentIndex.return_value = 1; _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].wheel_enabled is True
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is True
        f.stack.currentIndex.return_value = 0; _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].wheel_enabled is True
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is True

    def test_lab_trip_preview_rectangles(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        assert f.preview_box.set_ringless_layout.call_args[0][0].controls_enabled is True
        f.stack.currentIndex.return_value = 1; _sync(f)
        assert f.preview_box.set_ringless_layout.call_args[0][0].controls_enabled is True
        f.stack.currentIndex.return_value = 0; _sync(f)
        assert f.preview_box.set_ringless_layout.call_args[0][0].controls_enabled is True


# ── Hidden module ───────────────────────────────────────────────────────────


class TestSyncModuleHidden:
    def test_hidden_preserves(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "showModuleSwitchButton": False})
        _sync(f)
        assert f.color_wheel.set_ringless_layout.call_args[0][0].controls_enabled is True

    def test_hidden_lab_controls_remain(self):
        f = _fixture(cfg_overrides={"hideHueRing": True, "showModuleSwitchButton": False},
                     stack_index=1)
        _sync(f)
        layout = f.color_wheel.set_ringless_layout.call_args[0][0]
        assert layout.controls_enabled is True and layout.wheel_enabled is True


# ── Parameter defaults ──────────────────────────────────────────────────────


class TestSyncParams:
    def test_defaults(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, window_width=500); _sync(f)
        _, ww, tbh, _vh = f.preview_box.set_ringless_layout.call_args[0]
        assert ww == 500 and tbh == 28

    def test_explicit_ws(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}, window_width=500)
        _sync(f, ws=200, tbh=30)
        bar_h = f.preview_box.set_ringless_layout.call_args[0][0].control_bar_height
        f.stack.setMinimumHeight.assert_called_once_with(200 + bar_h)


# ── No deletion ─────────────────────────────────────────────────────────────


class TestSyncNoDeletion:
    def test_components_not_deleted(self):
        f = _fixture(cfg_overrides={"hideHueRing": True}); _sync(f)
        f.cfg["hideHueRing"] = False; _sync(f)
        for obj in [f.color_wheel, f.preview_box, f.pane_wheel]:
            obj.deleteLater.assert_not_called()
        f.stack.removeWidget.assert_not_called()


def test_settings_save_remeasures_height_after_ringless_sync(monkeypatch):
    call_order = []
    loaded_cfg = _cfg(hideHueRing=True)
    monkeypatch.setattr(
        "ui.main_window.config.load_hotkey_config", lambda: loaded_cfg
    )
    window = SimpleNamespace(
        cfg={},
        update_hotkey_bindings=MagicMock(),
        grayscale_overlay=MagicMock(),
        update_window_flags=MagicMock(),
        update_no_focus_policies=MagicMock(),
        sync_thread=SimpleNamespace(
            set_software_mode=MagicMock(),
            update_versions=MagicMock(),
        ),
        _current_module="hsv",
        refresh_slider_visibility_and_order=MagicMock(),
        color_wheel=MagicMock(),
        preview_box=SimpleNamespace(position_mode="top-left"),
        apply_theme=MagicMock(side_effect=lambda: call_order.append("theme")),
        current_ui_scale=100,
        update=MagicMock(),
        _sync_ringless_mode=MagicMock(
            side_effect=lambda: call_order.append("sync")
        ),
        _adjust_content_height=MagicMock(
            side_effect=lambda: call_order.append("height")
        ),
    )

    MainWindow.on_settings_saved(window)

    assert call_order[-2:] == ["sync", "height"]
