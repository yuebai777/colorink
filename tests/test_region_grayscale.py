"""Unit and integration tests for the Window / Region Grayscale Filter."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core import config, i18n
from core.dcomp_grayscale import DCompGrayscaleController
from core.region_grayscale import (
    RegionGrayscaleController,
     normalize_region_tuple,
    region_to_config_dict,
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


def test_dcomp_controller_set_region_syncs_dll():
    ctrl = DCompGrayscaleController.__new__(DCompGrayscaleController)
    mock_dll = MagicMock()
    mock_dll.dcomp_filter_set_region.return_value = True
    ctrl._dll = mock_dll
    ctrl._mode = "oklch"
    ctrl._target = "all"
    ctrl._target_index = -1
    ctrl._region = None
    ctrl.last_error = ""

    assert ctrl.set_region((100, 150, 800, 600)) is True
    assert ctrl.region == (100, 150, 800, 600)
    mock_dll.dcomp_filter_set_region.assert_called_with(100, 150, 800, 600)

    assert ctrl.clear_region() is True
    assert ctrl.region is None
    mock_dll.dcomp_filter_set_region.assert_called_with(0, 0, 0, 0)


def test_region_grayscale_controller_lifecycle_and_reset():
    ctrl = RegionGrayscaleController.__new__(RegionGrayscaleController)
    ctrl._mode = "oklch"
    ctrl._preferred_backend = "auto"
    ctrl._target = "all"
    ctrl._region = None
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

    # Turning off region grayscale clears underlying DComp clip so fullscreen grayscale isn't clipped
    assert ctrl.toggle() is False or ctrl.is_active is False
    fake_dcomp.clear_region.assert_called_once()


def test_region_grayscale_selector_signals(qapp, monkeypatch):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QKeyEvent, QMouseEvent
    from ui.region_grayscale_selector import RegionGrayscaleSelector

    selector = RegionGrayscaleSelector(None)
    selected = []
    reset_called = []
    canceled = []
    selector.regionSelected.connect(lambda x, y, w, h: selected.append((x, y, w, h)))
    selector.regionReset.connect(lambda: reset_called.append(True))
    selector.canceled.connect(lambda: canceled.append(True))

    # Stub physical cursor pos to return globalPosition coordinates directly
    monkeypatch.setattr(
        RegionGrayscaleSelector,
        "_physical_cursor_pos",
        staticmethod(lambda pt=None: (pt.x(), pt.y()) if pt is not None else (0, 0)),
    )

    selector._is_selecting = True
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(50, 60),
        QPointF(50, 60),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    selector.mousePressEvent(press_ev)

    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(350, 260),
        QPointF(350, 260),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    selector.mouseMoveEvent(move_ev)

    rel_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(350, 260),
        QPointF(350, 260),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    selector.mouseReleaseEvent(rel_ev)

    assert selected == [(50, 60, 300, 200)]

    # Space key resets to maximized
    selector._is_selecting = True
    key_space = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Space,
        Qt.KeyboardModifier.NoModifier,
    )
    selector.keyPressEvent(key_space)
    assert len(reset_called) == 1

    # Escape cancels
    selector._is_selecting = True
    key_esc = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    selector.keyPressEvent(key_esc)
    assert len(canceled) == 1


def test_settings_sidebar_region_controls(qapp, monkeypatch):
    tmp = tempfile.mkdtemp(prefix="colorink-region-gray-")
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp)

    from PyQt6.QtWidgets import QWidget
    from ui.settings_sidebar import SettingsSidebar

    class StubCompanionSync:
        def _has_session(self):
            return False

        def _disconnect(self):
            pass

    class StubSyncThread:
        def __init__(self):
            self.companion_sync = StubCompanionSync()

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config.load_hotkey_config()
            self.sync_thread = StubSyncThread()
            self.select_called = 0
            self.reset_called = 0

        def on_settings_saved(self):
            pass

        def start_select_grayscale_region(self):
            self.select_called += 1

        def reset_grayscale_region_to_maximized(self):
            self.reset_called += 1
            self.cfg["grayscaleFilterRegion"] = None

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

    # Simulate region selected
    sidebar.cfg["grayscaleFilterRegion"] = {"x": 100, "y": 120, "width": 640, "height": 480}
    sidebar._refresh_grayscale_region_ui()
    assert sidebar.lbl_grayscale_region.text() == "640×480 (100, 120)"
    assert sidebar.btn_reset_grayscale_region.isEnabled()

    sidebar.btn_select_grayscale_region.click()
    assert parent.select_called == 1

    sidebar.btn_reset_grayscale_region.click()
    assert parent.reset_called == 1
