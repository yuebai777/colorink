"""Unit and integration tests for the Window / Region Grayscale Filter (multi-region & UI)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core import config, i18n
from core.dcomp_grayscale import DCompGrayscaleController
from core.region_grayscale import (
    RegionGrayscaleController,
    active_region_tuples,
    format_region_status_from_entries,
    normalize_region_entries,
    normalize_region_tuple,
    read_region_entries_from_config,
    region_to_config_dict,
    write_regions_to_config,
)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_normalize_region_tuple_and_dict():
    assert normalize_region_tuple(None) is None
    assert normalize_region_tuple((100, 200, 640, 480)) == (100, 200, 640, 480)
    assert normalize_region_tuple([10, 20, 300, 400]) == (10, 20, 300, 400)
    assert normalize_region_tuple({"x": 50, "y": 60, "width": 800, "height": 600}) == (
        50,
        60,
        800,
        600,
    )
    assert normalize_region_tuple({"x": 0, "y": 0, "width": 0, "height": 500}) is None
    assert normalize_region_tuple({"x": 0, "y": 0, "width": -10, "height": 500}) is None

    assert region_to_config_dict(None) is None
    assert region_to_config_dict((12, 34, 560, 780)) == {
        "x": 12,
        "y": 34,
        "width": 560,
        "height": 780,
    }


def test_multi_region_config_helpers_and_migration():
    entries = normalize_region_entries([
        {"x": 10, "y": 20, "width": 300, "height": 400},
        {"x": 500, "y": 20, "width": 250, "height": 300, "enabled": False, "name": "参考区"},
        {"x": 0, "y": 0, "width": 0, "height": 100},  # invalid -> dropped
    ])
    assert entries == [
        {"x": 10, "y": 20, "width": 300, "height": 400, "enabled": True},
        {"x": 500, "y": 20, "width": 250, "height": 300, "enabled": False, "name": "参考区"},
    ]
    assert active_region_tuples(entries) == [(10, 20, 300, 400)]

    cfg = {}
    write_regions_to_config(cfg, entries)
    assert len(cfg["grayscaleFilterRegions"]) == 2
    assert cfg["grayscaleFilterRegion"] == {"x": 10, "y": 20, "width": 300, "height": 400}
    assert read_region_entries_from_config(cfg) == entries

    assert format_region_status_from_entries([]) == "默认最大化"
    assert format_region_status_from_entries([entries[0]]) == "300×400 (10, 20)"
    assert format_region_status_from_entries(entries) == "共 2 个区域（已启用 1 个）"
    disabled_only = [dict(entries[0], enabled=False)]
    assert "已全部停用" in format_region_status_from_entries(disabled_only)

    # Legacy single-region migration from v5 -> v6
    legacy_cfg = {
        config.CONFIG_SCHEMA_KEY: 5,
        "grayscaleFilterRegion": {"x": 89, "y": 115, "width": 2184, "height": 1400},
    }
    migrated = config.migrate_config(legacy_cfg)
    assert migrated[config.CONFIG_SCHEMA_KEY] == config.CONFIG_SCHEMA_VERSION
    assert migrated["grayscaleFilterRegions"] == [
        {"x": 89, "y": 115, "width": 2184, "height": 1400, "enabled": True}
    ]


def test_dcomp_controller_set_region_and_multi_regions_sync_dll():
    ctrl = DCompGrayscaleController.__new__(DCompGrayscaleController)
    mock_dll = MagicMock()
    mock_dll.dcomp_filter_set_region.return_value = True
    mock_dll.dcomp_filter_set_regions.return_value = True
    ctrl._dll = mock_dll
    ctrl._mode = "oklch"
    ctrl._target = "all"
    ctrl._target_index = -1
    ctrl._region = None
    ctrl._regions = []
    ctrl.last_error = ""

    assert ctrl.set_region((100, 150, 800, 600)) is True
    assert ctrl.region == (100, 150, 800, 600)
    assert ctrl.regions == [(100, 150, 800, 600)]
    mock_dll.dcomp_filter_set_region.assert_called_with(100, 150, 800, 600)

    assert ctrl.set_regions([(10, 20, 300, 200), (400, 50, 500, 350)]) is True
    assert ctrl.regions == [(10, 20, 300, 200), (400, 50, 500, 350)]
    assert ctrl.region == (10, 20, 300, 200)
    assert mock_dll.dcomp_filter_set_regions.called
    arr_arg, count_arg = mock_dll.dcomp_filter_set_regions.call_args.args
    assert count_arg == 2
    assert (arr_arg[0].x, arr_arg[0].y, arr_arg[0].w, arr_arg[0].h) == (10, 20, 300, 200)
    assert (arr_arg[1].x, arr_arg[1].y, arr_arg[1].w, arr_arg[1].h) == (400, 50, 500, 350)

    assert ctrl.clear_region() is True
    assert ctrl.region is None
    assert ctrl.regions == []
    mock_dll.dcomp_filter_set_region.assert_called_with(0, 0, 0, 0)


def test_region_grayscale_controller_lifecycle_and_reset():
    ctrl = RegionGrayscaleController.__new__(RegionGrayscaleController)
    ctrl._mode = "oklch"
    ctrl._preferred_backend = "auto"
    ctrl._target = "all"
    ctrl._region = None
    ctrl._regions = []
    ctrl.last_error = ""

    fake_dcomp = MagicMock()
    fake_dcomp.is_available = True
    fake_dcomp.is_healthy = True
    fake_dcomp.is_active = False

    def _dcomp_set_active(active, mode=None):
        fake_dcomp.is_active = bool(active)
        return True

    fake_dcomp.set_active.side_effect = _dcomp_set_active
    fake_mag = MagicMock()
    fake_mag.is_available = True
    fake_mag.is_healthy = True
    fake_mag.is_active = False

    ctrl._dcomp = fake_dcomp
    ctrl._mag = fake_mag
    ctrl._active_engine = None

    assert ctrl.format_region_status() == "默认最大化"
    ctrl.set_region((200, 100, 900, 700))
    assert ctrl.region == (200, 100, 900, 700)
    assert ctrl.format_region_status() == "900×700 (200, 100)"

    assert ctrl.toggle() is True
    assert ctrl.is_active is True
    fake_dcomp.set_region.assert_called_with((200, 100, 900, 700))

    # Multi-region propagation
    ctrl.set_regions([(10, 20, 300, 400), (500, 100, 250, 350)])
    assert ctrl.regions == [(10, 20, 300, 400), (500, 100, 250, 350)]
    assert ctrl.format_region_status() == "已启用 2 个区域"
    fake_dcomp.set_regions.assert_called_with([(10, 20, 300, 400), (500, 100, 250, 350)])

    # Turning off region grayscale clears underlying DComp clip so fullscreen grayscale isn't clipped
    assert ctrl.toggle() is False or ctrl.is_active is False
    fake_dcomp.clear_region.assert_called_once()


def _mouse_event(ev_type, x: float, y: float, button, buttons, mod=None):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    return QMouseEvent(
        ev_type,
        QPointF(x, y),
        QPointF(x, y),
        button,
        buttons,
        mod or Qt.KeyboardModifier.NoModifier,
    )


def test_region_grayscale_selector_multi_region_edit_and_signals(qapp, monkeypatch):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from ui.region_grayscale_selector import RegionGrayscaleSelector

    selector = RegionGrayscaleSelector(None)
    multi_selected = []
    single_selected = []
    replaced = []
    reset_called = []
    canceled = []
    selector.regionsSelected.connect(lambda regs: multi_selected.append(regs))
    selector.regionSelected.connect(lambda x, y, w, h: single_selected.append((x, y, w, h)))
    selector.regionReplaced.connect(lambda idx, x, y, w, h: replaced.append((idx, x, y, w, h)))
    selector.regionReset.connect(lambda: reset_called.append(True))
    selector.canceled.connect(lambda: canceled.append(True))

    # Stub physical cursor pos to return globalPosition coordinates directly
    monkeypatch.setattr(
        RegionGrayscaleSelector,
        "_physical_cursor_pos",
        staticmethod(lambda pt=None: (pt.x(), pt.y()) if pt is not None else (0, 0)),
    )

    selector.setGeometry(0, 0, 1920, 1080)
    selector._monitors = []
    selector._is_selecting = True

    # Draw box #1: (50, 60) -> (350, 260) => 300x200
    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 50, 60, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    selector.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 350, 260, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    selector.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, 350, 260, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert len(selector._regions) == 1
    assert selector._regions[0]["width"] == 300

    # Draw box #2: (500, 100) -> (700, 300) => 200x200
    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 500, 100, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    selector.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 700, 300, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    selector.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, 700, 300, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert len(selector._regions) == 2

    # Move box #1 by (+20, +10) dragging its interior at (150, 150)
    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 150, 150, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    selector.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 170, 160, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    selector.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, 170, 160, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert (selector._regions[0]["x"], selector._regions[0]["y"]) == (70, 70)

    # Confirm via Enter -> emits both regions in regionsSelected
    selector.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert len(multi_selected) == 1
    assert [(r["x"], r["y"], r["width"], r["height"]) for r in multi_selected[0]] == [
        (70, 70, 300, 200),
        (500, 100, 200, 200),
    ]

    # Right-click inside a box deletes that box; right-click outside cancels
    selector._is_selecting = True
    selector._regions = [
        {"x": 50, "y": 60, "width": 300, "height": 200, "enabled": True},
        {"x": 500, "y": 100, "width": 200, "height": 200, "enabled": True},
    ]
    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 550, 150, Qt.MouseButton.RightButton, Qt.MouseButton.RightButton))
    assert len(selector._regions) == 1
    assert len(canceled) == 0

    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 900, 900, Qt.MouseButton.RightButton, Qt.MouseButton.RightButton))
    assert len(canceled) == 1

    # Space key resets to maximized when no regions are drawn
    selector._is_selecting = True
    selector._regions = []
    selector.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert len(reset_called) == 1

    # Single-region replace mode emits regionReplaced immediately on release
    selector._is_selecting = True
    selector._replace_index = 1
    selector._regions = []
    selector.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 100, 120, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    selector.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 400, 320, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    selector.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, 400, 320, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert replaced == [(1, 100, 120, 300, 200)]
    assert single_selected[-1] == (100, 120, 300, 200)


def test_settings_sidebar_multi_region_controls_and_toggles(qapp, monkeypatch):
    tmp = tempfile.mkdtemp(prefix="colorink-region-gray-")
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp)

    from PyQt6.QtWidgets import QCheckBox, QPushButton, QWidget
    from ui.settings_sidebar import SettingsSidebar

    class StubCompanionSync:
        def _has_session(self):
            return False

        def _disconnect(self):
            pass

    class StubSyncThread:
        def __init__(self):
            self.companion_sync = StubCompanionSync()

    class StubOverlay:
        def __init__(self):
            self.is_active = False

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config.load_hotkey_config()
            self.sync_thread = StubSyncThread()
            self.grayscale_overlay = StubOverlay()
            self.region_grayscale_overlay = StubOverlay()
            self.select_calls = []
            self.reset_called = 0
            self.applied_batches = []

        def on_settings_saved(self):
            pass

        def toggle_fullscreen_grayscale(self):
            self.grayscale_overlay.is_active = not self.grayscale_overlay.is_active
            return self.grayscale_overlay.is_active

        def toggle_region_grayscale(self):
            self.region_grayscale_overlay.is_active = not self.region_grayscale_overlay.is_active
            return self.region_grayscale_overlay.is_active

        def start_select_grayscale_region(self, replace_index=None):
            self.select_calls.append(replace_index)

        def apply_grayscale_regions(self, entries, activate=False):
            normalized = write_regions_to_config(self.cfg, entries)
            self.applied_batches.append((normalized, activate))
            return normalized

        def reset_grayscale_region_to_maximized(self):
            self.reset_called += 1
            write_regions_to_config(self.cfg, [])

        def float_panel(self, *args, **kwargs):
            return False

        def dock_panel(self, *args, **kwargs):
            return False

        def zoom_ui(self, *args, **kwargs):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    parent = StubMainWindow()
    sidebar = SettingsSidebar(parent)
    sidebar.setVisible(False)
    parent.settings_sidebar = sidebar

    assert sidebar.btn_region_grayscale.val == "Ctrl+Alt+G"
    assert sidebar.lbl_grayscale_region.text() == "默认最大化"
    assert not sidebar.btn_reset_grayscale_region.isEnabled()

    # Quick toggle buttons in Filter page actually toggle and update UI state
    assert sidebar.btn_toggle_fullscreen_grayscale.text() == "开启全屏灰度"
    sidebar.btn_toggle_fullscreen_grayscale.click()
    assert parent.grayscale_overlay.is_active is True
    assert sidebar.btn_toggle_fullscreen_grayscale.text() == "关闭全屏灰度"
    assert sidebar.lbl_fullscreen_grayscale_status.text() == "● 已开启"

    sidebar.btn_toggle_region_grayscale.click()
    assert parent.region_grayscale_overlay.is_active is True
    assert sidebar.btn_toggle_region_grayscale.text() == "关闭区域灰度"

    # Legacy single-region dict compatibility
    sidebar.cfg["grayscaleFilterRegion"] = {"x": 100, "y": 120, "width": 640, "height": 480}
    sidebar._refresh_grayscale_region_ui()
    assert sidebar.lbl_grayscale_region.text() == "640×480 (100, 120)"
    assert sidebar.btn_reset_grayscale_region.isEnabled()

    # Multi-region list renders per-region rows with toggle, reselect, delete
    sidebar.cfg["grayscaleFilterRegions"] = [
        {"x": 100, "y": 120, "width": 640, "height": 480, "enabled": True},
        {"x": 800, "y": 120, "width": 400, "height": 300, "enabled": True},
    ]
    sidebar._refresh_grayscale_region_ui()
    assert sidebar.lbl_grayscale_region.text() == "共 2 个区域（已启用 2 个）"
    assert sidebar._grayscale_regions_layout.count() == 2

    # Uncheck second region row
    row2 = sidebar._grayscale_regions_layout.itemAt(1).widget()
    cb2 = row2.findChild(QCheckBox)
    assert cb2 is not None and cb2.isChecked()
    cb2.click()
    assert sidebar.cfg["grayscaleFilterRegions"][1]["enabled"] is False
    assert sidebar.lbl_grayscale_region.text() == "共 2 个区域（已启用 1 个）"

    # Click reselect on first region row
    row1 = sidebar._grayscale_regions_layout.itemAt(0).widget()
    reselect_btn = row1.findChild(QPushButton, "GrayscaleActionSmall")
    assert reselect_btn is not None
    reselect_btn.click()
    assert parent.select_calls[-1] == 0

    # Delete first region row
    del_btn = row1.findChild(QPushButton, "GrayscaleRemoveSmall")
    assert del_btn is not None
    del_btn.click()
    assert len(sidebar.cfg["grayscaleFilterRegions"]) == 1
    assert sidebar.cfg["grayscaleFilterRegions"][0]["x"] == 800

    sidebar.btn_select_grayscale_region.click()
    assert parent.select_calls[-1] is None

    sidebar.btn_reset_grayscale_region.click()
    assert parent.reset_called == 1
    assert sidebar.lbl_grayscale_region.text() == "默认最大化"


def test_windowed_mag_multi_region_bounds():
    import threading
    from core.region_grayscale import WindowedMagGrayscale

    mag = WindowedMagGrayscale.__new__(WindowedMagGrayscale)
    mag._user32 = None
    mag._kernel32 = None
    mag._mag_dll = None
    mag._is_active = False
    mag._running = False
    mag._target = "all"
    mag._target_index = -1
    mag._region = None
    mag._regions = []
    mag.last_error = ""
    mag._cond = threading.Condition()

    assert mag.set_regions([(10, 20, 300, 400), (500, 600, 200, 150)]) is True
    assert mag.regions == [(10, 20, 300, 400), (500, 600, 200, 150)]
    assert mag._compute_bounds_list() == [(10, 20, 300, 400), (500, 600, 200, 150)]
    mag.clear_regions()
    assert mag.regions == []


def test_picker_actions_multi_region_flow(qapp, monkeypatch):
    tmp = tempfile.mkdtemp(prefix="colorink-picker-region-")
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp)

    from PyQt6.QtWidgets import QWidget
    from ui.window.picker_actions import PickerActionsMixin

    class FakeOverlay:
        def __init__(self):
            self.is_active = False
            self.last_error = ""
            self.regions = []

        def set_active(self, active, mode=None):
            self.is_active = bool(active)
            return True

        def toggle(self):
            self.is_active = not self.is_active
            return self.is_active

        def set_regions(self, regs):
            self.regions = list(regs)
            return True

        def clear_region(self):
            self.regions = []
            return True

    class FakeSelector:
        def __init__(self):
            self.regionsSelected = MagicMock()
            self.regionReplaced = MagicMock()
            self.regionReset = MagicMock()
            self.canceled = MagicMock()
            self.started_with = None

        def start_selection(self, regions=None, replace_index=None):
            self.started_with = (regions, replace_index)

    class FakeSettingsWindow:
        def __init__(self):
            self.visible = True
            self.raised = 0

        def isVisible(self):
            return self.visible

        def hide(self):
            self.visible = False

        def show(self):
            self.visible = True

        def raise_(self):
            self.raised += 1

    class Host(PickerActionsMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config.load_hotkey_config()
            self.grayscale_overlay = FakeOverlay()
            self.region_grayscale_overlay = FakeOverlay()
            self.region_grayscale_selector = FakeSelector()
            self.settings_window = FakeSettingsWindow()
            self.settings_sidebar = None

    host = Host()
    write_regions_to_config(host.cfg, [
        {"x": 10, "y": 20, "width": 300, "height": 400, "enabled": True},
        {"x": 500, "y": 20, "width": 250, "height": 300, "enabled": False, "name": "保留区"},
    ])
    host.region_grayscale_overlay.set_active(True)

    # Start selection: pauses active region overlay & hides settings window
    host.start_select_grayscale_region()
    assert host.region_grayscale_overlay.is_active is False
    assert host.settings_window.visible is False

    # Cancel restores previous active region overlay & settings window
    host._on_grayscale_selection_canceled()
    assert host.region_grayscale_overlay.is_active is True
    assert host.settings_window.visible is True

    # Multi-region confirm preserves disabled entry and activates region overlay
    host.start_select_grayscale_region()
    host._on_grayscale_regions_selected([
        {"x": 10, "y": 20, "width": 300, "height": 400, "enabled": True},
        {"x": 900, "y": 100, "width": 320, "height": 240, "enabled": True},
    ])
    saved = read_region_entries_from_config(host.cfg)
    assert len(saved) == 3
    assert saved[2]["enabled"] is False
    assert saved[2]["name"] == "保留区"
    assert host.region_grayscale_overlay.regions == [(10, 20, 300, 400), (900, 100, 320, 240)]
    assert host.region_grayscale_overlay.is_active is True
    assert host.settings_window.visible is True

    # Single-region replace preserves name & other regions
    host._on_grayscale_region_replaced(2, 600, 50, 280, 260)
    saved2 = read_region_entries_from_config(host.cfg)
    assert saved2[2] == {"x": 600, "y": 50, "width": 280, "height": 260, "enabled": True, "name": "保留区"}
    assert len(host.region_grayscale_overlay.regions) == 3

    # Fullscreen grayscale toggle deactivates region grayscale
    assert host.toggle_fullscreen_grayscale() is True
    assert host.grayscale_overlay.is_active is True
    assert host.region_grayscale_overlay.is_active is False
