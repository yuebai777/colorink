import json
import re
from pathlib import Path
from unittest.mock import patch

from core.config import HOTKEY_CFG_NAME, load_hotkey_config
from core.csp_companion_sync import _DEBUG
from core.updater import APP_VERSION, _normalize_version


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_application_version_is_current_release():
    assert APP_VERSION == "1.5.0"
    assert _normalize_version(APP_VERSION) == [1, 5]


def test_windows_file_version_matches_application_version():
    content = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")

    assert "filevers=(1, 5, 0, 0)" in content
    assert "prodvers=(1, 5, 0, 0)" in content
    assert "StringStruct('FileVersion', '1.5.0.0')" in content
    assert "StringStruct('ProductVersion', '1.5.0.0')" in content


def test_release_notes_start_with_current_release():
    content = (PROJECT_ROOT / "release_notes.md").read_text(encoding="utf-8")
    assert content.startswith("## v1.5.0\n")


def test_first_run_defaults_are_compact_and_discoverable():
    with patch("core.config.get_user_data_dir", return_value=str(PROJECT_ROOT / "missing-config")):
        config = load_hotkey_config()

    assert config["pickKey"] == "F11"
    assert config["hideWindowKey"] == "Ctrl+H"
    assert config["followMouseKey"] == "Ctrl+R"
    assert config["grayscaleFilterKey"] == "Ctrl+G"
    assert config["colorSpaceModule"] == "hsv"
    assert config["showModuleSwitchButton"] is True
    assert config["showSlidersHSV"] is True
    assert config["showSlidersRGB"] is False
    assert config["showSlidersLAB"] is False
    assert config["historyColumns"] == 8
    assert config["historyRows"] == 2
    assert config["syncSoftware"] == "csp"
    assert config["openAtLogin"] is False
    assert config["hideHueRing"] is False
    assert config["ringlessControlsSide"] == "right"
    assert config["orderSlidersHistory"] == 7
    assert "injectionKey" not in config
    assert "colorPickingEnabled" not in config
    assert "cspAutoClick" not in config


def test_legacy_dead_keys_are_stripped_on_load(tmp_path):
    path = tmp_path / HOTKEY_CFG_NAME
    path.write_text(
        json.dumps({
            "pickKey": "F2",
            "injectionKey": "F12",
            "colorPickingEnabled": True,
            "cspAutoClick": True,
            "cspClickDelayMs": 30,
        }),
        encoding="utf-8",
    )

    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        config = load_hotkey_config()

    assert config["pickKey"] == "F2"
    assert "injectionKey" not in config
    assert "colorPickingEnabled" not in config
    assert "cspAutoClick" not in config
    assert "cspClickDelayMs" not in config


def test_slider_orders_are_normalized_on_load(tmp_path):
    path = tmp_path / HOTKEY_CFG_NAME
    path.write_text(
        json.dumps({
            "orderSlidersRGB": 1,
            "orderSlidersHSV": 2,
            "orderSlidersHSL": 3,
            "orderSlidersLAB": 4,
            "orderSlidersOKLab": 5,
            "orderSlidersOKLCh": 6,
            "orderSlidersHistory": 1,
        }),
        encoding="utf-8",
    )

    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        config = load_hotkey_config()

    order_keys = [
        "orderSlidersRGB",
        "orderSlidersHSV",
        "orderSlidersHSL",
        "orderSlidersLAB",
        "orderSlidersOKLab",
        "orderSlidersOKLCh",
        "orderSlidersHistory",
    ]
    assert sorted(config[k] for k in order_keys) == [1, 2, 3, 4, 5, 6, 7]


def test_existing_config_values_survive_missing_key_merge(tmp_path):
    path = tmp_path / HOTKEY_CFG_NAME
    path.write_text(
        json.dumps({"pickKey": "F2", "colorSpaceModule": "lch"}),
        encoding="utf-8",
    )

    with patch("core.config.get_user_data_dir", return_value=str(tmp_path)):
        config = load_hotkey_config()

    assert config["pickKey"] == "F2"
    assert config["colorSpaceModule"] == "lch"
    assert config["showModuleSwitchButton"] is True
    assert config["hideHueRing"] is False
    assert config["ringlessControlsSide"] == "right"


def test_file_version_has_four_components():
    content = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")
    match = re.search(r"StringStruct\('FileVersion', '([0-9.]+)'\)", content)
    assert match is not None
    assert match.group(1) == f"{APP_VERSION}.0"


def test_companion_debug_logging_is_disabled_for_release_builds():
    assert _DEBUG is False
