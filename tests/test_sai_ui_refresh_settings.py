"""Settings-UI coverage for the SAI UI-refresh option.

The refresher itself is unit-tested in ``test_sai2_ui_refresh``; here the
concern is plumbing: the row exists, it only shows in SAI mode, the config
value round-trips through the sidebar, and the value actually reaches the
SAI backend instead of stopping at the config dict.
"""

import os

import pytest

from core import i18n
from core.config import default_hotkey_config, load_hotkey_config
from core.sai2_ui_refresh import MODE_FULL, MODE_OFF, MODE_REPAINT


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sidebar(qapp, tmp_path, monkeypatch):
    from core import config as _config

    monkeypatch.setattr(_config, "get_user_data_dir", lambda: str(tmp_path))

    from PyQt6.QtWidgets import QWidget

    class StubCompanionSync:
        _connected = False

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
            self.cfg = load_hotkey_config()
            self.cfg.setdefault("ui-theme", "auto")
            self.cfg.setdefault("fontSize", 100)
            self.sync_thread = StubSyncThread()

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *a, **k):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    from ui.settings_sidebar import SettingsSidebar

    parent = StubMainWindow()
    s = SettingsSidebar(parent)
    s.setVisible(False)
    parent.settings_sidebar = s
    return s


def test_default_config_ships_the_input_free_mode():
    # Out of the box nothing may be injected into SAI: the preview click is an
    # explicit opt-in because SAI keeps its button-down point.
    assert default_hotkey_config()["saiUiRefresh"] == MODE_REPAINT


def test_row_offers_the_three_modes_safest_first(sidebar):
    values = [
        sidebar.combo_sai_refresh.itemData(i)
        for i in range(sidebar.combo_sai_refresh.count())
    ]
    assert values == [MODE_REPAINT, MODE_FULL, MODE_OFF]


def test_every_mode_has_a_hover_explanation(sidebar):
    from PyQt6.QtCore import Qt

    for i in range(sidebar.combo_sai_refresh.count()):
        tip = sidebar.combo_sai_refresh.itemData(i, Qt.ItemDataRole.ToolTipRole)
        assert tip


def test_row_is_visible_only_in_sai_mode(sidebar):
    index = sidebar.combo_software.findData("sai")
    sidebar.combo_software.setCurrentIndex(index)
    sidebar.update_version_visibility()
    assert not sidebar.row_sai_refresh_widget.isHidden()
    # tracks the existing SAI version row exactly
    assert sidebar.row_sai_widget.isHidden() == sidebar.row_sai_refresh_widget.isHidden()

    sidebar.combo_software.setCurrentIndex(sidebar.combo_software.findData("csp"))
    sidebar.update_version_visibility()
    assert sidebar.row_sai_refresh_widget.isHidden()


def test_selection_is_saved_to_config(sidebar):
    index = sidebar.combo_sai_refresh.findData(MODE_REPAINT)
    sidebar.combo_sai_refresh.setCurrentIndex(index)
    sidebar.save_settings()
    assert sidebar.cfg["saiUiRefresh"] == MODE_REPAINT


def _persist(sidebar, value):
    """Store a value the way the app does, then reload the row from disk."""
    from core import config

    sidebar.cfg["saiUiRefresh"] = value
    config.save_hotkey_config(sidebar.cfg)
    sidebar.refresh_ui()


def test_saved_value_is_restored_into_the_row(sidebar):
    _persist(sidebar, MODE_OFF)
    assert sidebar.combo_sai_refresh.currentData() == MODE_OFF


def test_unknown_stored_value_falls_back_to_the_first_entry(sidebar):
    # A hand-edited or downgraded config must not leave the row blank, and the
    # fallback must be the mode that injects nothing.
    _persist(sidebar, "banana")
    assert sidebar.combo_sai_refresh.currentData() == MODE_REPAINT


def test_round_trip_through_save_and_reload(sidebar):
    sidebar.combo_sai_refresh.setCurrentIndex(
        sidebar.combo_sai_refresh.findData(MODE_REPAINT))
    sidebar.save_settings()
    sidebar.refresh_ui()
    assert sidebar.combo_sai_refresh.currentData() == MODE_REPAINT


def test_mode_reaches_the_sai_backend():
    """update_versions must push the setting down to SAI2Sync."""
    from core import memory_sync

    class StubBackend:
        def __init__(self):
            self.version = None
            self.ui_refresh = None

        def set_version(self, value):
            self.version = value

        def set_ui_refresh(self, value):
            self.ui_refresh = value

    class StubThread:
        """Attribute bag standing in for MemorySyncThread (a QThread)."""

        csp_version = "auto"
        sai2_version = "auto"
        udm_version = "auto"
        ps_version = "auto"
        sai_ui_refresh = MODE_REPAINT

        def __init__(self):
            self.csp_sync = StubBackend()
            self.sai2_sync = StubBackend()
            self.udm_sync = StubBackend()
            self.ps_sync = StubBackend()

    stub = StubThread()
    memory_sync.MemorySyncThread.update_versions(stub)
    assert stub.sai2_sync.ui_refresh == MODE_REPAINT
    # the existing version plumbing must keep working alongside it
    assert stub.sai2_sync.version == "auto"


def test_stored_click_mode_is_migrated_off_by_schema_bump():
    """The click-injecting default must not survive an upgrade.

    It was never a deliberate choice, and it leaves SAI with a stale
    button-down point that shows up as a wedge on the next stroke.
    """
    from core.config import CONFIG_SCHEMA_KEY, CONFIG_SCHEMA_VERSION, migrate_config

    migrated = migrate_config({CONFIG_SCHEMA_KEY: 1, "saiUiRefresh": "full"})
    assert migrated["saiUiRefresh"] == MODE_REPAINT
    assert migrated[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


def test_migration_leaves_a_deliberate_choice_alone():
    from core.config import CONFIG_SCHEMA_KEY, migrate_config

    # Already on the current schema: the user's own pick is respected.
    kept = migrate_config({CONFIG_SCHEMA_KEY: 2, "saiUiRefresh": "full"})
    assert kept["saiUiRefresh"] == MODE_FULL

    off = migrate_config({CONFIG_SCHEMA_KEY: 1, "saiUiRefresh": "off"})
    assert off["saiUiRefresh"] == MODE_OFF
