"""Config schema versioning and settings import/export.

Covers the schemaVersion stamp/migration mechanism plus the
export/import envelope used by the backup & restore UI.
"""

import json
from unittest.mock import patch

import pytest

from core import config
from core.config import (
    CONFIG_SCHEMA_KEY,
    CONFIG_SCHEMA_VERSION,
    SETTINGS_EXPORT_FORMAT,
    export_settings,
    export_settings_to_file,
    import_settings,
    import_settings_from_file,
    migrate_config,
)


# ── Schema versioning ─────────────────────────────────────────────────────


def test_fresh_config_is_stamped_with_current_schema_version(tmp_path):
    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        cfg = config.load_hotkey_config()

    assert cfg[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


def test_migrate_config_stamps_version_when_absent():
    cfg = migrate_config({"pickKey": "F2"})

    assert cfg[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION
    assert cfg["pickKey"] == "F2"


def test_migrate_config_runs_legacy_cleanup_from_version_zero():
    cfg = migrate_config({
        "pickKey": "F2",
        "injectionKey": "F12",
        "colorPickingEnabled": True,
        "cspAutoClick": True,
        "cspClickDelayMs": 30,
        "showRinglessControlBar": True,
    })

    assert "injectionKey" not in cfg
    assert "colorPickingEnabled" not in cfg
    assert "cspAutoClick" not in cfg
    assert "cspClickDelayMs" not in cfg
    assert "showRinglessControlBar" not in cfg
    assert cfg["pickKey"] == "F2"
    assert cfg[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


def test_migrate_config_is_idempotent_for_current_version():
    already = {
        "pickKey": "F2",
        CONFIG_SCHEMA_KEY: CONFIG_SCHEMA_VERSION,
    }
    cfg = migrate_config(dict(already))

    assert cfg == already


def test_save_stamps_schema_version(tmp_path):
    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        config.save_hotkey_config({"pickKey": "F2"})

    raw = json.loads((tmp_path / config.HOTKEY_CFG_NAME).read_text(encoding="utf-8"))
    assert raw[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION


# ── Export / import ───────────────────────────────────────────────────────


def test_export_settings_builds_envelope():
    payload = export_settings({"pickKey": "F2", "syncSoftware": "csp"})

    assert payload["format"] == SETTINGS_EXPORT_FORMAT
    assert payload["schemaVersion"] == CONFIG_SCHEMA_VERSION
    assert "exportedAt" in payload
    assert payload["config"]["pickKey"] == "F2"
    assert payload["config"]["syncSoftware"] == "csp"


def test_round_trip_preserves_user_settings():
    source = config.load_hotkey_config()
    source.update({
        "pickKey": "F9",
        "syncSoftware": "ps",
        "historyColumns": 12,
        "uiScale": 120,
    })

    restored = import_settings(export_settings(source))

    assert restored["pickKey"] == "F9"
    assert restored["syncSoftware"] == "ps"
    assert restored["historyColumns"] == 12
    assert restored["uiScale"] == 120


def test_import_accepts_json_string_and_dict():
    payload = export_settings({"pickKey": "F3"})
    as_string = json.dumps(payload, ensure_ascii=False)

    assert import_settings(payload)["pickKey"] == "F3"
    assert import_settings(as_string)["pickKey"] == "F3"


def test_import_merges_missing_defaults():
    restored = import_settings(export_settings({"pickKey": "F3"}))

    # Keys absent from the export are back-filled from defaults.
    assert restored["showTitleBar"] is True
    assert restored["historyColumns"] == 8


def test_import_rejects_wrong_format():
    with pytest.raises(ValueError):
        import_settings({"format": "something-else", "config": {}})


def test_import_rejects_missing_config():
    with pytest.raises(ValueError):
        import_settings({"format": SETTINGS_EXPORT_FORMAT})


def test_file_round_trip(tmp_path):
    path = tmp_path / "colorink-settings.json"
    source = config.load_hotkey_config()
    source["pickKey"] = "F7"

    export_settings_to_file(source, str(path))
    restored = import_settings_from_file(str(path))

    assert restored["pickKey"] == "F7"
    assert restored[CONFIG_SCHEMA_KEY] == CONFIG_SCHEMA_VERSION
