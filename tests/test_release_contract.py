import json
import re
from pathlib import Path
from unittest.mock import patch

from core.config import HOTKEY_CFG_NAME, load_hotkey_config
from core.csp_companion_sync import _DEBUG
from core.updater import APP_VERSION, _normalize_version

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_application_version_matches_release_notes():
    """APP_VERSION is the single source of truth: the newest release-note
    heading must carry the same version, so a bump only edits updater.py +
    release_notes.md instead of a hardcoded copy inside this test too."""
    content = (PROJECT_ROOT / "release_notes.md").read_text(encoding="utf-8")
    assert content.startswith(f"## v{APP_VERSION}\n")
    # v-prefix stripping must be a no-op against the bare version.
    assert _normalize_version(APP_VERSION) == _normalize_version(f"v{APP_VERSION}")


def test_windows_file_version_matches_application_version():
    content = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(x) for x in APP_VERSION.split("."))

    assert f"filevers=({major}, {minor}, {patch}, 0)" in content
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in content
    assert f"StringStruct('FileVersion', '{APP_VERSION}.0')" in content
    assert f"StringStruct('ProductVersion', '{APP_VERSION}.0')" in content


def test_release_notes_start_with_current_release():
    content = (PROJECT_ROOT / "release_notes.md").read_text(encoding="utf-8")
    assert content.startswith(f"## v{APP_VERSION}\n")


def test_first_run_defaults_are_compact_and_discoverable():
    with patch("core.config.get_user_data_dir", return_value=str(PROJECT_ROOT / "missing-config")):
        config = load_hotkey_config()

    assert config["pickKey"] == "Ctrl+Alt+Q"
    assert config["hideWindowKey"] == "Ctrl+Alt+Y"
    assert config["toggleTitleBarKey"] == "Ctrl+Alt+K"
    assert config["followMouseKey"] == "Ctrl+Alt+J"
    assert config["grayscaleFilterKey"] == "Ctrl+Alt+D"
    assert config["toggleLabKey"] == "Space"
    assert config["toggleLabGlobalKey"] == "Ctrl+Alt+L"
    assert config["showLabToggleButton"] is True
    assert config["showLabShapeButton"] is True
    assert config["showLabHarmonyButton"] is True
    assert config["showTitleBar"] is True
    assert config["colorSpaceModule"] == "hsv"
    assert config["showModuleSwitchButton"] is True
    assert config["showSlidersHSV"] is True
    assert config["showSlidersRGB"] is False
    assert config["showSlidersLAB"] is False
    assert config["historyColumns"] == 8
    assert config["historyRows"] == 2
    assert config["syncSoftware"] == "auto"
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
    assert config["showLabToggleButton"] is True
    assert config["showLabShapeButton"] is True
    assert config["showLabHarmonyButton"] is True
    assert config["toggleTitleBarKey"] == "Ctrl+Alt+K"
    assert config["showTitleBar"] is True
    assert config["toggleLabGlobalKey"] == "Ctrl+Alt+L"
    assert config["hideHueRing"] is False
    assert config["ringlessControlsSide"] == "right"


def test_file_version_has_four_components():
    content = (PROJECT_ROOT / "file_version_info.txt").read_text(encoding="utf-8")
    match = re.search(r"StringStruct\('FileVersion', '([0-9.]+)'\)", content)
    assert match is not None
    assert match.group(1) == f"{APP_VERSION}.0"


def test_companion_debug_logging_is_disabled_for_release_builds():
    assert _DEBUG is False


SPEC_FILES = ["Colorink.spec", "Colorink Onefile.spec"]
SOURCE_DIRS = ["ui", "core"]
ICON_REF_RE = re.compile(r"icons/([A-Za-z0-9_.\-]+\.(?:png|ico))")
BINARY_RESOURCE_RE = re.compile(r'"([A-Za-z0-9_.\-]+\.(?:exe|dll|slang|slangp|pyc))"')
# Directories where bundled binary resources live (source of truth for
# "does a referenced file name exist on disk" — avoids walking venv/).
_BINARY_SEARCH_DIRS = [
    PROJECT_ROOT,
    PROJECT_ROOT / "core",
    PROJECT_ROOT / "mag_overlay" / "build",
    PROJECT_ROOT / "native_grayscale" / "runtime",
]


def _spec_declared_icons() -> set[str]:
    declared: set[str] = set()
    for spec_name in SPEC_FILES:
        content = (PROJECT_ROOT / spec_name).read_text(encoding="utf-8")
        for match in re.finditer(r"_add_if_exists\('icons/([^']+)'", content):
            declared.add(match.group(1))
    return declared


def _source_referenced_icons() -> set[str]:
    referenced: set[str] = set()
    for directory in SOURCE_DIRS:
        for py in (PROJECT_ROOT / directory).glob("*.py"):
            content = py.read_text(encoding="utf-8")
            for match in ICON_REF_RE.finditer(content):
                referenced.add(match.group(1))
    return referenced


def test_declared_icons_cover_source_references():
    """Every icon referenced from source must be declared in the spec datas.

    PyInstaller only bundles files listed in the spec; a QSS image: url()
    pointing at an undeclared icon silently renders nothing in the packaged
    EXE. This test prevents that regression.
    """
    declared = _spec_declared_icons()
    missing = sorted(_source_referenced_icons() - declared)
    assert not missing, (
        "Icons referenced in source but missing from spec datas "
        f"(_add_if_exists): {', '.join(missing)}"
    )


def test_spec_declares_all_icon_files():
    """Every icon file in icons/ must be declared in the spec datas.

    Catches the case where a new icon is added to icons/ but nobody adds the
    corresponding spec entry. (.icns is macOS-only and intentionally skipped.)
    """
    declared = _spec_declared_icons()
    existing = {
        f.name for f in (PROJECT_ROOT / "icons").iterdir()
        if f.suffix in (".png", ".ico")
    }
    undeclared = sorted(existing - declared)
    assert not undeclared, (
        "Icon files in icons/ missing from spec datas "
        f"(_add_if_exists): {', '.join(undeclared)}"
    )


def _spec_declared_resources() -> list[set[str]]:
    """Per-spec _add_if_exists declaration sets (in SPEC_FILES order)."""
    sets = []
    for spec_name in SPEC_FILES:
        content = (PROJECT_ROOT / spec_name).read_text(encoding="utf-8")
        sets.append(set(re.findall(r"_add_if_exists\('([^']+)'", content)))
    return sets


def test_specs_declare_identical_resource_sets():
    """Both specs must declare exactly the same resources.

    The icon contract tests take the union across specs, so a resource
    declared in only one spec would pass them while shipping a different
    payload per build flavour (Onedir vs Onefile).
    """
    sets = _spec_declared_resources()
    assert sets[0] == sets[1], (
        "Spec resource sets drifted between Colorink.spec and "
        f"Colorink Onefile.spec:\n"
        f"  only in {SPEC_FILES[0]}: {sorted(sets[0] - sets[1])}\n"
        f"  only in {SPEC_FILES[1]}: {sorted(sets[1] - sets[0])}"
    )


def test_spec_declared_binary_resources_exist_on_disk():
    """Every non-icon resource declared in the specs must exist.

    _add_if_exists silently skips missing files at build time — the packaged
    EXE then ships without the resource while the build log looks clean.
    """
    for spec_name, declared in zip(SPEC_FILES, _spec_declared_resources()):
        missing = []
        for rel in sorted(declared):
            if rel.endswith((".exe", ".dll", ".slang", ".slangp", ".pyc")):
                if not (PROJECT_ROOT / rel).is_file():
                    missing.append(rel)
        assert not missing, (
            f"{spec_name} declares binary resources missing from disk: "
            f"{', '.join(missing)}"
        )


def test_spec_declared_binaries_cover_source_references():
    """Every bundled binary referenced from source must be declared in the
    specs.

    Only file names that actually exist on disk (in the known resource
    directories) count — process names like "Photoshop.exe" are not
    bundled resources.
    """
    referenced: set[str] = set()
    for directory in SOURCE_DIRS:
        for py in (PROJECT_ROOT / directory).glob("*.py"):
            content = py.read_text(encoding="utf-8")
            for match in BINARY_RESOURCE_RE.finditer(content):
                name = match.group(1)
                if any(
                    (d / name).is_file() for d in _BINARY_SEARCH_DIRS
                ):
                    referenced.add(name)
    declared = set()
    for content in (
        (PROJECT_ROOT / s).read_text(encoding="utf-8") for s in SPEC_FILES
    ):
        for match in re.finditer(r"_add_if_exists\('([^']+)'", content):
            if match.group(1).endswith((".exe", ".dll", ".slang", ".slangp", ".pyc")):
                declared.add(Path(match.group(1)).name)
    missing = sorted(referenced - declared)
    assert not missing, (
        "Binary resources referenced in source but missing from spec datas "
        f"(_add_if_exists): {', '.join(missing)}"
    )
