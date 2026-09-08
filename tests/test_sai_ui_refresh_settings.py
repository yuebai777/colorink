"""Settings coverage for the single SAI UI-refresh mode.

The app used to expose a three-way choice (repaint / full / off) whose
default silently degraded to repaint-only and let SAI's cached stroke-preview
bitmap drift away from the colour slot forever. There is now exactly one mode
— full — with no user-facing selector: this file pins that contract (config,
migration, sidebar UI and the sync-thread wiring).
"""

import os

import pytest

from core import i18n
from core.config import (
    CONFIG_SCHEMA_KEY,
    CONFIG_SCHEMA_VERSION,
    default_hotkey_config,
    load_hotkey_config,
    migrate_config,
)
from core.sai2_ui_refresh import MODE_FULL


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


def test_config_ships_no_mode_selector_key():
    # There is no stored knob to choose a refresh mode any more: the app
    # always runs the full refresh.
    assert "saiUiRefresh" not in default_hotkey_config()


def test_schema_one_migration_drops_the_legacy_knob():
    # Old configs may still carry a value from the three-mode era. Upgrading
    # must remove the knob, not preserve a mode the app no longer honours.
    for legacy in ("full", "repaint", "off", "banana"):
        migrated = migrate_config({CONFIG_SCHEMA_KEY: 1, "saiUiRefresh": legacy})
        assert "saiUiRefresh" not in migrated
        assert migrated[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


def test_current_schema_migration_drops_a_leftover_knob():
    # Files saved by three-mode-era builds at the then-current schema also
    # carry the key; loading them must drop it too.
    migrated = migrate_config({CONFIG_SCHEMA_KEY: 4, "saiUiRefresh": "off"})
    assert "saiUiRefresh" not in migrated
    assert migrated[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


def test_sai_section_has_no_ui_refresh_selector(sidebar):
    # The version row stays; the refresh-mode row is gone.
    assert hasattr(sidebar, "combo_sai")
    assert not hasattr(sidebar, "combo_sai_refresh")
    assert not hasattr(sidebar, "row_sai_refresh_widget")


def test_saving_settings_writes_no_mode_key(sidebar):
    # Saving from the UI must not resurrect a mode key.
    assert "saiUiRefresh" not in sidebar.cfg
    sidebar.save_settings()
    assert "saiUiRefresh" not in sidebar.cfg


def test_thread_always_runs_the_full_refresh():
    """The sync thread must start on — and never leave — the full mode.

    Nothing reads a stored mode any more; the thread's own default is full,
    so stale config values from the three-mode era can never resurrect a
    repaint-only session.
    """
    from core.memory_sync import MemorySyncThread

    thread = MemorySyncThread()
    try:
        assert thread.sai_ui_refresh == MODE_FULL
        assert thread.sai2_sync.ui_refresher.mode == MODE_FULL
    finally:
        thread.running = False  # never started; nothing to tear down
        thread.sai2_sync._reset_cache(close_handle=True)


def test_sai_poll_reads_feed_the_refresher_and_spot_external_changes():
    """Every sai-mode poll read must reach the refresher as evidence; a slot
    change that is not our own write echo must arm an immediate rediscovery
    (SAI re-rendered its preview cache in that colour)."""
    import time

    from core.memory_sync import MemorySyncThread

    thread = MemorySyncThread()
    thread.running = False
    calls = []

    class StubSAI:
        def note_colour(self, rgb):
            calls.append(("note", tuple(rgb)))

        def on_external_colour(self, rgb):
            calls.append(("ext", tuple(rgb)))

    thread.sai2_sync = StubSAI()
    try:
        # 0. the first read after attach: SAI's cache still shows exactly
        #    this colour (startup), so it must be noted AND armed
        thread._last_synced_color = {}
        thread._last_write_ts = {}
        thread._note_sai_observation({"r": 10, "g": 10, "b": 10})
        assert ("note", (10, 10, 10)) in calls
        assert ("ext", (10, 10, 10)) in calls

        # 1. a genuine SAI-side change (differs from last known colour, no
        #    write in flight) -> noted AND armed
        thread._last_synced_color = {0: (10, 10, 10)}
        thread._last_write_ts = {}
        thread._note_sai_observation({"r": 40, "g": 20, "b": 20})
        assert ("note", (40, 20, 20)) in calls
        assert calls.count(("ext", (40, 20, 20))) == 1

        # 2. the poll repeating the same colour -> not external again (in the
        #    real loop the first read already updated _last_synced_color via
        #    the emit path; mirror that here)
        thread._last_synced_color = {0: (40, 20, 20)}
        thread._note_sai_observation({"r": 40, "g": 20, "b": 20})
        assert calls.count(("ext", (40, 20, 20))) == 1

        # 3. a stale echo of the pre-write colour within the write window ->
        #    not external
        thread._last_synced_color = {0: (60, 60, 60)}
        thread._last_write_old_color = {0: (40, 20, 20)}
        thread._last_write_ts = {0: time.time()}
        thread._note_sai_observation({"r": 40, "g": 20, "b": 20})
        assert calls.count(("ext", (40, 20, 20))) == 1
        assert calls.count(("ext", (60, 60, 60))) == 0
    finally:
        thread.sai2_sync = StubSAI()  # nothing to tear down further
