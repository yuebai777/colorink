"""Regression tests for the 开机自启动 toggle in the settings sidebar.

The sidebar used to apply the HKCU Run entry but never wrote the new
``openAtLogin`` value back into ``self.cfg`` when the registry write
succeeded. As a result the checkbox reverted to the old state on the next
``refresh_ui()``, and unchecking could not clear a previously registered Run
key. These tests pin the persist-on-success behavior.
"""

import os
from unittest.mock import patch

import pytest

from core.config import load_hotkey_config


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication for the test module (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    # Pin the UI language so text assertions are deterministic regardless of
    # the machine's locale.
    from core import i18n

    i18n.set_language(i18n.LANG_ZH)

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def stub_main_window(qapp, tmp_path, monkeypatch):
    """Minimal stub of Colorink main window with an isolated config dir."""
    from core import config as _config
    monkeypatch.setattr(_config, "get_user_data_dir", lambda: str(tmp_path))

    from PyQt6.QtWidgets import QWidget

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = load_hotkey_config()
            self.cfg.setdefault("ui-theme", "auto")
            self.cfg.setdefault("fontSize", 100)

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *a, **k):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    return StubMainWindow()


@pytest.fixture
def sidebar(stub_main_window, qapp):
    """Create a real SettingsSidebar with a stub parent."""
    from ui.settings_sidebar import SettingsSidebar

    s = SettingsSidebar(stub_main_window)
    s.setVisible(False)
    stub_main_window.settings_sidebar = s
    return s


class TestAutostartToggle:
    def test_enable_persists_open_at_login(self, sidebar):
        with patch("ui.settings_sidebar.autostart.apply_autostart",
                   return_value=True) as apply:
            sidebar.cb_autostart.setChecked(True)

        apply.assert_called_once_with(True)
        assert sidebar.cfg["openAtLogin"] is True

        # refresh_ui() is what happens when the settings window is reopened;
        # the checkbox must stay checked for the freshly loaded config.
        sidebar.refresh_ui()
        assert sidebar.cb_autostart.isChecked() is True

    def test_disable_clears_registry_and_persists(self, sidebar):
        sidebar.cfg["openAtLogin"] = True
        sidebar._persist_config()
        sidebar.refresh_ui()
        assert sidebar.cb_autostart.isChecked() is True

        with patch("ui.settings_sidebar.autostart.apply_autostart",
                   return_value=True) as apply:
            sidebar.cb_autostart.setChecked(False)

        apply.assert_called_once_with(False)
        assert sidebar.cfg["openAtLogin"] is False

    def test_failed_apply_rolls_back(self, sidebar):
        with patch("ui.settings_sidebar.autostart.apply_autostart",
                   return_value=False):
            sidebar.cb_autostart.setChecked(True)

        assert sidebar.cfg["openAtLogin"] is False
        assert sidebar.cb_autostart.isChecked() is False

    def test_tooltip_does_not_claim_admin_rights(self, sidebar):
        tip = sidebar.cb_autostart.toolTip()
        assert "以管理员权限" not in tip
        assert "UAC" not in tip
        assert "不需要管理员权限" in tip
