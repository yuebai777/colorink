import json
import os
from datetime import datetime, timezone
from typing import Callable

CFG_NAME = "window-config.json"
HOTKEY_CFG_NAME = "hotkey-config.json"

# Schema version stamped into every saved hotkey/settings config. Bump this
# when the shape of the config changes and register a migration below; old
# configs are migrated forward on load instead of relying on ad-hoc key pops.
CONFIG_SCHEMA_KEY = "schemaVersion"
CONFIG_SCHEMA_VERSION = 1

# Envelope marker for the settings backup/restore JSON export.
SETTINGS_EXPORT_FORMAT = "colorink-settings"

# Canonical slider groups, in their default display order. Every place that
# reasons about slider order (main window layout, settings sidebar moves,
# config normalization) must go through this list and the helpers below so
# the default values and tie-breaks can never drift apart.
SLIDER_GROUPS = ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh", "History"]


def slider_order_key(group: str) -> str:
    """Config key holding a slider group's display order."""
    return "orderSlidersHistory" if group == "History" else f"orderSliders{group}"


def get_slider_order(cfg, group: str) -> int:
    """Read a group's order value, falling back to its canonical position."""
    try:
        return int(cfg.get(slider_order_key(group), SLIDER_GROUPS.index(group) + 1))
    except (TypeError, ValueError):
        return SLIDER_GROUPS.index(group) + 1


def sorted_slider_groups(cfg):
    """Return the seven slider groups ordered by their config order values.

    Ties are broken by the canonical group order, so the result is always a
    strict, stable total order.
    """
    return sorted(SLIDER_GROUPS, key=lambda g: (get_slider_order(cfg, g), SLIDER_GROUPS.index(g)))


def get_user_data_dir():
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(appdata, "Colorink")
    os.makedirs(path, exist_ok=True)
    return path

def load_window_config():
    path = os.path.join(get_user_data_dir(), CFG_NAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_window_config(cfg):
    path = os.path.join(get_user_data_dir(), CFG_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def default_hotkey_config():
    """Return a fresh copy of the default hotkey/settings config."""
    return {
        "pickKey": "F11",
        "followMouseKey": "Ctrl+R",
        "hideWindowKey": "Ctrl+H",
        "toggleTitleBarKey": "Ctrl+Shift+T",  # 全局快捷键: 切换标题栏（设置/最小化/关闭那一栏）显隐
        "grayscaleFilterKey": "Ctrl+G",
        "toggleLabKey": "Space",          # 本地快捷键: 鼠标悬停色轮/LAB区域时切换视图
        "toggleLabGlobalKey": "Ctrl+L",   # 全局快捷键: 任意位置切换色轮/LAB视图
        "showLabToggleButton": True,      # 显示/隐藏色轮与LAB之间的浮动切换按钮
        "showTitleBar": True,             # 显示/隐藏标题栏（隐藏后顶部边框与四周一致）
        "grayscaleFilterScreen": "all",
        "grayscaleFilterMode": "oklch",
        # native = DXGI Desktop Duplication + OpenGL，支持 OKLCh / Luma 与按屏目标；
        # mag = Windows 系统颜色矩阵（仅 Luma，作用于全部屏幕）
        "grayscaleFilterBackend": "native",
        "showTaskbarIcon": False,
        "lockWindowSize": False,
        "lockWindowPosition": False,
        "onlyShowInCsp": False,
        "openAtLogin": False,
        "checkUpdatesOnStartup": True,
        "previewBoxPosition": "top-left",
        "cspVersion": "auto",
        "sai2Version": "auto",
        "udmVersion": "auto",
        "ui-theme": "auto",
        "language": "auto",
        "showSlidersRGB": False,
        "showSlidersHSV": True,
        "showSlidersHSL": False,
        "showSlidersLAB": False,
        "orderSlidersRGB": 1,
        "orderSlidersHSV": 2,
        "orderSlidersHSL": 3,
        "orderSlidersLAB": 4,
        "showSlidersOKLab": True,
        "showSlidersOKLCh": True,
        "orderSlidersOKLab": 5,
        "orderSlidersOKLCh": 6,
        "visualizerMode": "lab",
        "labVisualizerMaxVal": 110,
        "colorWheelMode": "hsv",
        "colorSpaceModule": "hsv",          # "hsv" | "hls" | "rgb" | "lch"
        "showModuleSwitchButton": True,     # floating button next to ⊙/△
        "sliderScrollStep": 1,
        "sliderSameSpace": 6,
        "sliderDiffSpace": 8,
        "showSlidersHistory": True,
        "orderSlidersHistory": 7,
        "historyColumns": 8,
        "historyRows": 2,
        "historySwatchSize": 18,
        "historyColors": [],
        "sliderStyle": "default",
        "followMouseEnabled": False,
        "noFocusMode": False,
        "showLabLightnessSlider": False,
        "syncSoftware": "csp",
        "psVersion": "auto",
        "uiScale": 100,
        "flipColorWheelHorizontally": True,
        "pickerZoom": 6,
        "hideHueRing": False,
        "ringlessControlsSide": "right",
        "ringlessControlBarPosition": "top",
        CONFIG_SCHEMA_KEY: CONFIG_SCHEMA_VERSION,
    }


def normalize_slider_orders(cfg):
    """Make the seven slider-order keys unique 1..7 values.

    Legacy configs could carry duplicate order values (for example the old
    History default collided with RGB). The move up/down controls in the
    settings UI need a strict total order, so duplicates are resolved by
    their existing relative position.
    """
    values = {key: get_slider_order(cfg, key) for key in SLIDER_GROUPS}
    if len(set(values.values())) == len(SLIDER_GROUPS):
        return cfg
    ordered = sorted(SLIDER_GROUPS, key=lambda k: (values[k], SLIDER_GROUPS.index(k)))
    for i, key in enumerate(ordered, start=1):
        cfg[slider_order_key(key)] = i
    return cfg


def _migrate_0_to_1(cfg: dict) -> dict:
    """Migrate a legacy config to v1: drop dead keys and the obsolete
    ringless control-bar toggle.

    These removals used to live inline in ``load_hotkey_config``; keeping them
    as the first migration makes the forward-compat path explicit and leaves a
    place for future structural migrations.
    """
    cfg.pop("showRinglessControlBar", None)
    for dead_key in ("injectionKey", "colorPickingEnabled", "cspAutoClick", "cspClickDelayMs"):
        cfg.pop(dead_key, None)
    return cfg


# Registered migrations, keyed by the target schema version they produce.
_CONFIG_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _migrate_0_to_1,
}


def migrate_config(cfg: dict) -> dict:
    """Bring *cfg* forward to the current schema version, in place.

    Configs without a ``schemaVersion`` are treated as version 0 and run every
    migration. Returns the same dict (mutated) for convenience.
    """
    try:
        from_version = int(cfg.get(CONFIG_SCHEMA_KEY, 0) or 0)
    except (TypeError, ValueError):
        from_version = 0
    for target in range(from_version + 1, CONFIG_SCHEMA_VERSION + 1):
        migrator = _CONFIG_MIGRATIONS.get(target)
        if migrator is not None:
            cfg = migrator(cfg)
    cfg[CONFIG_SCHEMA_KEY] = CONFIG_SCHEMA_VERSION
    return cfg


def _merge_with_defaults(loaded: dict) -> dict:
    """Back-fill missing keys from the default config, preserving user values."""
    for key, value in default_hotkey_config().items():
        if key not in loaded:
            loaded[key] = value
    return loaded


def load_hotkey_config():
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if not isinstance(loaded, dict):
                    return dict(default_hotkey_config())
                loaded = migrate_config(loaded)
                return normalize_slider_orders(_merge_with_defaults(loaded))
        except Exception:
            pass
    return normalize_slider_orders(migrate_config(dict(default_hotkey_config())))

def save_hotkey_config(cfg):
    cfg.setdefault(CONFIG_SCHEMA_KEY, CONFIG_SCHEMA_VERSION)
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Settings export / import (backup & restore) ────────────────────────────


def export_settings(cfg: dict) -> dict:
    """Build a self-describing export envelope for the hotkey/settings config.

    The envelope is what gets written to disk for backup; the ``config`` field
    holds the raw settings so ``import_settings`` can re-run merge/migration
    against it exactly like a normal load.
    """
    return {
        "format": SETTINGS_EXPORT_FORMAT,
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": dict(cfg),
    }


def merge_imported_config(imported: dict) -> dict:
    """Migrate + back-fill + normalize a raw config dict for import."""
    merged = migrate_config(dict(imported))
    return normalize_slider_orders(_merge_with_defaults(merged))


def import_settings(data) -> dict:
    """Parse an exported envelope into a merged, migrated config.

    ``data`` may be an envelope dict or its JSON string. Raises ``ValueError``
    for anything that is not a Colorink settings export.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("文件不是有效的 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("导入内容不是有效的设置数据")
    if data.get("format") != SETTINGS_EXPORT_FORMAT:
        raise ValueError("这不是 Colorink 的设置备份文件")
    imported = data.get("config")
    if not isinstance(imported, dict):
        raise ValueError("备份文件中缺少设置内容")
    return merge_imported_config(imported)


def export_settings_to_file(cfg: dict, path: str) -> None:
    """Write the settings export envelope to *path* as UTF-8 JSON."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(export_settings(cfg), f, ensure_ascii=False, indent=2)


def import_settings_from_file(path: str) -> dict:
    """Load and validate a settings export from *path*."""
    with open(path, "r", encoding="utf-8") as f:
        return import_settings(json.load(f))

