"""Regression tests for the 开机自启动 toggle in the settings sidebar.

The sidebar used to apply the HKCU Run entry but never wrote the new
``openAtLogin`` value back into ``self.cfg`` when the registry write
succeeded. As a result the checkbox reverted to the old state on the next
``refresh_ui()``, and unchecking could not clear a previously registered Run
key. These tests pin the persist-on-success behavior.
"""

import json
import os
import sys
import types
from unittest.mock import patch

import pytest

from core import autostart
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

    def test_failed_apply_rollback_does_not_recurse_save_settings(self, sidebar):
        with patch(
            "ui.settings_sidebar.autostart.apply_autostart", return_value=False
        ) as apply:
            sidebar.cb_autostart.setChecked(True)

        apply.assert_called_once_with(True)
        assert sidebar.cfg["openAtLogin"] is False
        assert sidebar.cb_autostart.isChecked() is False

    def test_windows_blocked_banner_and_repair_button(self, sidebar, monkeypatch):
        state = {"blocked": True}
        monkeypatch.setattr(autostart, "is_autostart_supported", lambda: True)
        monkeypatch.setattr(
            autostart, "is_autostart_blocked_by_windows", lambda: state["blocked"]
        )

        def fake_apply(enabled):
            if enabled:
                state["blocked"] = False
            return True

        sidebar.cfg["openAtLogin"] = True
        sidebar._persist_config()
        sidebar.refresh_ui()

        assert not sidebar._autostart_status_row.isHidden()
        assert sidebar.lbl_autostart_status.objectName() == "StatusWarning"
        assert "Windows" in sidebar.lbl_autostart_status.text()
        assert not sidebar.btn_autostart_repair.isHidden()

        with patch("ui.settings_sidebar.autostart.apply_autostart", side_effect=fake_apply) as apply:
            sidebar.btn_autostart_repair.click()

        apply.assert_called_once_with(True)
        assert state["blocked"] is False
        assert sidebar.cfg["openAtLogin"] is True
        assert sidebar.btn_autostart_repair.isHidden()
        assert sidebar._autostart_status_row.isHidden()

    def test_import_config_syncs_open_at_login_with_apply_result(
        self, sidebar, tmp_path, monkeypatch
    ):
        from PyQt6.QtWidgets import QFileDialog, QMessageBox

        cfg_file = tmp_path / "imported.json"
        cfg_data = load_hotkey_config()
        cfg_data["openAtLogin"] = True
        cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda *a, **k: (str(cfg_file), "JSON")
        )
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        with patch(
            "ui.settings.update_panel.autostart.apply_autostart", return_value=False
        ) as apply:
            sidebar.import_config()

        apply.assert_called_once_with(True)
        assert sidebar.cfg["openAtLogin"] is False
        assert load_hotkey_config()["openAtLogin"] is False
        assert sidebar.cb_autostart.isChecked() is False

        with patch(
            "ui.settings.update_panel.autostart.apply_autostart", return_value=True
        ) as apply:
            sidebar.import_config()

        apply.assert_called_once_with(True)
        assert sidebar.cfg["openAtLogin"] is True
        assert load_hotkey_config()["openAtLogin"] is True
        assert sidebar.cb_autostart.isChecked() is True

    def test_reset_config_keeps_open_at_login_when_unregister_fails(
        self, sidebar, monkeypatch
    ):
        from PyQt6.QtWidgets import QMessageBox

        sidebar.cfg["openAtLogin"] = True
        sidebar._persist_config()
        sidebar.refresh_ui()

        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

        with patch(
            "ui.settings.update_panel.autostart.apply_autostart", return_value=False
        ) as apply:
            sidebar.reset_config()

        apply.assert_called_once_with(False)
        assert sidebar.cfg["openAtLogin"] is True
        assert load_hotkey_config()["openAtLogin"] is True
        assert sidebar.cb_autostart.isChecked() is True


class _FakeKey:
    def __init__(self, store, path):
        self.store = store
        self.path = path


def _make_fake_winreg(initial_keys=None):
    store = {k: dict(v) for k, v in (initial_keys or {}).items()}
    mod = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=0x0002,
        KEY_QUERY_VALUE=0x0001,
        REG_SZ=1,
        REG_BINARY=3,
    )

    def create_key_ex(root, sub_key, reserved=0, access=0):
        store.setdefault(sub_key, {})
        return _FakeKey(store, sub_key)

    def open_key_ex(root, sub_key, reserved=0, access=0):
        if sub_key not in store:
            raise FileNotFoundError(sub_key)
        return _FakeKey(store, sub_key)

    def query_value_ex(key, value_name):
        bucket = key.store.get(key.path, {})
        if value_name not in bucket:
            raise FileNotFoundError(value_name)
        return bucket[value_name]

    def set_value_ex(key, value_name, reserved, reg_type, value):
        key.store[key.path][value_name] = (value, reg_type)

    def delete_value(key, value_name):
        bucket = key.store.get(key.path, {})
        if value_name not in bucket:
            raise FileNotFoundError(value_name)
        del bucket[value_name]

    def close_key(key):
        pass

    mod.CreateKeyEx = create_key_ex
    mod.OpenKeyEx = open_key_ex
    mod.QueryValueEx = query_value_ex
    mod.SetValueEx = set_value_ex
    mod.DeleteValue = delete_value
    mod.CloseKey = close_key
    return mod, store


class TestAutostartRegistryAndStartupApproved:
    def test_enable_clears_windows_disabled_startup_approval(self, monkeypatch):
        fake_winreg, store = _make_fake_winreg(
            {
                autostart._STARTUP_APPROVED_KEY_PATH: {
                    autostart._VALUE_NAME: (
                        bytes.fromhex("010000004d267d44c62add01"),
                        3,
                    )
                }
            }
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"D:\app\Colorink.exe")

        assert autostart.is_autostart_blocked_by_windows() is True
        assert autostart.apply_autostart(True) is True
        assert store[autostart._RUN_KEY_PATH][autostart._VALUE_NAME] == (
            '"D:\\app\\Colorink.exe"',
            fake_winreg.REG_SZ,
        )
        assert store[autostart._STARTUP_APPROVED_KEY_PATH][autostart._VALUE_NAME] == (
            autostart._STARTUP_APPROVED_ENABLED,
            fake_winreg.REG_BINARY,
        )
        assert autostart.is_autostart_blocked_by_windows() is False

    def test_startup_sync_preserves_disabled_approval_when_requested(self, monkeypatch):
        disabled_blob = bytes.fromhex("030000004d267d44c62add01")
        fake_winreg, store = _make_fake_winreg(
            {
                autostart._STARTUP_APPROVED_KEY_PATH: {
                    autostart._VALUE_NAME: (disabled_blob, 3)
                }
            }
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"D:\moved\Colorink.exe")

        assert autostart.apply_autostart(True, clear_disabled_approval=False) is True
        assert store[autostart._RUN_KEY_PATH][autostart._VALUE_NAME][0] == (
            '"D:\\moved\\Colorink.exe"'
        )
        assert (
            store[autostart._STARTUP_APPROVED_KEY_PATH][autostart._VALUE_NAME][0]
            == disabled_blob
        )
        assert autostart.is_autostart_blocked_by_windows() is True

    def test_disable_removes_both_run_and_startup_approval(self, monkeypatch):
        fake_winreg, store = _make_fake_winreg(
            {
                autostart._RUN_KEY_PATH: {
                    autostart._VALUE_NAME: ('"D:\\app\\Colorink.exe"', 1)
                },
                autostart._STARTUP_APPROVED_KEY_PATH: {
                    autostart._VALUE_NAME: (b"\x01" + b"\x00" * 11, 3)
                },
            }
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        assert autostart.apply_autostart(False) is True
        assert autostart._VALUE_NAME not in store[autostart._RUN_KEY_PATH]
        assert (
            autostart._VALUE_NAME
            not in store[autostart._STARTUP_APPROVED_KEY_PATH]
        )
        assert autostart.is_autostart_blocked_by_windows() is False

    @pytest.mark.parametrize(
        ("blob", "expected"),
        [
            (None, False),
            (b"", False),
            (b"\x02" + b"\x00" * 11, False),
            (b"\x06" + b"\x00" * 11, False),
            (b"\x01" + b"\x00" * 11, True),
            (b"\x03" + b"\x00" * 11, True),
            (b"\x07" + b"\x00" * 11, True),
        ],
    )
    def test_is_autostart_blocked_by_windows_bitmask(self, monkeypatch, blob, expected):
        initial = {}
        if blob is not None:
            initial[autostart._STARTUP_APPROVED_KEY_PATH] = {
                autostart._VALUE_NAME: (blob, 3)
            }
        fake_winreg, _ = _make_fake_winreg(initial)
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(sys, "platform", "win32")

        assert autostart.is_autostart_blocked_by_windows() is expected

    def test_source_mode_is_unsupported_and_returns_false(self, monkeypatch):
        fake_winreg, store = _make_fake_winreg()
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "frozen", False, raising=False)

        assert autostart.is_autostart_supported() is False
        assert autostart.apply_autostart(True) is False
        assert store == {}
