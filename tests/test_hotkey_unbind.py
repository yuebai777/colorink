"""Unbinding ("无") a hotkey from the settings UI.

The stored value for an unbound hotkey is an empty string: ``config`` keeps it
verbatim, ``global_hotkeys.bind_hotkey`` returns early on it and the local
LAB-toggle comparisons never match it, so an unbound default stays unbound
across restarts until the user binds something again.
"""

import os

import pytest

from core import i18n
from core.config import load_hotkey_config

# config key → the sidebar attribute stem holding its widgets
HOTKEY_WIDGETS = {
    "pickKey": "pick",
    "hideWindowKey": "hide",
    "followMouseKey": "follow",
    "grayscaleFilterKey": "grayscale",
    "toggleLabKey": "lab_toggle",
    "toggleLabGlobalKey": "lab_global",
    "toggleTitleBarKey": "title_bar",
}


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
    """Real SettingsSidebar over an isolated config dir and a stub parent."""
    from core import config

    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))

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

    from ui.settings_sidebar import SettingsSidebar

    parent = StubMainWindow()
    s = SettingsSidebar(parent)
    s.setVisible(False)
    parent.settings_sidebar = s
    return s


def _shown(none_btn):
    """Would the「无」button be visible once its ancestors are on screen?

    ``isVisibleTo`` (not ``isVisible``) — the sidebar's own parent is a stub
    that is never shown, which hides every child regardless of the flag.
    """
    return none_btn.isVisibleTo(none_btn.parentWidget())


def test_every_hotkey_has_an_unbind_button(sidebar):
    for config_key, stem in HOTKEY_WIDGETS.items():
        button = getattr(sidebar, f"btn_{stem}")
        none_btn = getattr(sidebar, f"btn_{stem}_none")
        assert button.val == sidebar.cfg[config_key]
        assert none_btn.text() == "无"
        assert none_btn.objectName() == "HotkeyNoneButton"
        assert none_btn.isEnabled(), f"{config_key} is bound →「无」must be clickable"
        assert _shown(none_btn), f"{config_key} is bound →「无」must be shown"


def test_none_button_unbinds_and_persists_empty_value(sidebar, monkeypatch):
    saved = []
    monkeypatch.setattr(
        "ui.settings_sidebar.config.save_hotkey_config",
        lambda cfg: saved.append(dict(cfg)))

    sidebar.btn_pick_none.click()

    assert sidebar.btn_pick.val == ""
    assert sidebar.cfg["pickKey"] == ""
    assert saved and saved[-1]["pickKey"] == ""
    # An unbound row shows the unbound label and has nothing left to clear.
    assert sidebar.btn_pick.text() == i18n.tr("未绑定")
    assert not sidebar.btn_pick_none.isEnabled()
    assert not _shown(sidebar.btn_pick_none)


def test_binding_again_reenables_the_none_button(sidebar, monkeypatch):
    monkeypatch.setattr("ui.settings_sidebar.config.save_hotkey_config",
                        lambda cfg: None)

    sidebar.btn_hide_none.click()
    assert sidebar.btn_hide_none.isEnabled() is False
    assert not _shown(sidebar.btn_hide_none)

    sidebar.btn_hide.val = "Ctrl+Alt+U"
    sidebar.save_hotkeys("Ctrl+Alt+U")
    assert sidebar.btn_hide_none.isEnabled() is True
    assert _shown(sidebar.btn_hide_none)
    assert sidebar.cfg["hideWindowKey"] == "Ctrl+Alt+U"


def test_refresh_ui_reflects_an_unbound_hotkey(sidebar, monkeypatch):
    cfg = dict(sidebar.cfg)
    cfg["pickKey"] = ""
    monkeypatch.setattr("ui.settings_sidebar.config.load_hotkey_config",
                        lambda: dict(cfg))
    monkeypatch.setattr("ui.settings_sidebar.config.save_hotkey_config",
                        lambda cfg: None)

    sidebar.refresh_ui()

    assert sidebar.btn_pick.val == ""
    assert sidebar.btn_pick.text() == i18n.tr("未绑定")
    assert not sidebar.btn_pick_none.isEnabled()
    assert not _shown(sidebar.btn_pick_none)


def test_delete_and_backspace_unbind_during_capture(sidebar):
    """Pressing Delete/Backspace while recording is the keyboard path to "无"."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    button = sidebar.btn_pick
    changed = []
    button.hotkeyChanged.connect(changed.append)

    for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        button.val = "Ctrl+Alt+Q"
        button.waiting_for_key = True
        button.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        assert button.val == "", f"{key} did not unbind"
        assert button.text() == i18n.tr("未绑定")
        assert button.waiting_for_key is False
        assert changed[-1] == ""

    assert sidebar.cfg["pickKey"] == ""


def test_escape_cancels_capture_without_unbinding(sidebar):
    """Escape must never destroy a binding — it only cancels the recording."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    button = sidebar.btn_pick
    button.waiting_for_key = True
    button.keyPressEvent(QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier))

    assert button.val == "Ctrl+Alt+Q"
    assert button.waiting_for_key is False


def test_unbound_values_survive_a_real_disk_round_trip(tmp_path, monkeypatch):
    """The empty string must survive JSON save + load, not just the save call.

    ``_merge_with_defaults`` back-fills *missing* keys from the defaults, so an
    unbound key that got dropped or coerced on the way to disk would silently
    re-bind itself on the next start.
    """
    from core import config

    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))
    cfg = config.load_hotkey_config()
    for key in HOTKEY_WIDGETS:
        cfg[key] = ""
    config.save_hotkey_config(dict(cfg))

    reloaded = config.load_hotkey_config()

    assert {key: reloaded[key] for key in HOTKEY_WIDGETS} == {
        key: "" for key in HOTKEY_WIDGETS}


def test_update_hotkey_bindings_skips_unbound_values(monkeypatch):
    """An unbound hotkey must never reach keyboard/mouse registration."""
    from ui.window.hotkey_mixin import HotkeyMixin

    keyboard_binds, mouse_binds = [], []
    # Stub the OS hooks, not bind_hotkey itself: the empty-string guard inside
    # bind_hotkey is exactly what this test exercises.
    monkeypatch.setattr("core.global_hotkeys.keyboard.add_hotkey",
                        lambda name, cb, suppress=False: keyboard_binds.append(name))
    monkeypatch.setattr("core.global_hotkeys.keyboard.remove_hotkey",
                        lambda name: None)
    monkeypatch.setattr("core.global_hotkeys.unbind_all", lambda: None)
    monkeypatch.setattr("core.global_hotkeys.bind_mouse_hotkey",
                        lambda htype, value: mouse_binds.append((htype, value)))

    class FakeWindow(HotkeyMixin):
        def __init__(self, cfg):
            self.cfg = cfg

    cfg = {key: "" for key in HOTKEY_WIDGETS}
    cfg["pickKey"] = "Ctrl+Alt+Q"
    FakeWindow(cfg).update_hotkey_bindings()

    assert keyboard_binds == ["ctrl+alt+q"]
    assert mouse_binds == []
