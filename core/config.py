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
CONFIG_SCHEMA_VERSION = 3

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

def _atomic_write_json(path, data):
    """Write JSON atomically (tmp + os.replace): a crash mid-write must not
    truncate the config and silently wipe all user settings on next load."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_window_config():
    path = os.path.join(get_user_data_dir(), CFG_NAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
    return {}

def save_window_config(cfg):
    path = os.path.join(get_user_data_dir(), CFG_NAME)
    try:
        _atomic_write_json(path, cfg)
    except Exception:
        pass

def default_hotkey_config():
    """Return a fresh copy of the default hotkey/settings config."""
    return {
        "pickKey": "Ctrl+Alt+Q",
        "followMouseKey": "Ctrl+Alt+J",
        "hideWindowKey": "Ctrl+Alt+Y",
        "toggleTitleBarKey": "Ctrl+Alt+K",  # 全局快捷键: 切换标题栏（设置/最小化/关闭那一栏）显隐
        "grayscaleFilterKey": "Ctrl+Alt+D",
        "toggleLabKey": "Space",          # 本地快捷键: 鼠标悬停色轮/LAB区域时切换视图
        "toggleLabGlobalKey": "Ctrl+Alt+L",   # 全局快捷键: 任意位置切换色轮/LAB视图
        "showLabToggleButton": True,      # 显示/隐藏色轮与LAB之间的浮动切换按钮
        "showLabShapeButton": True,       # 显示/隐藏 LAB 视图形状切换按钮
        "showLabHarmonyButton": True,     # 显示/隐藏 LAB 调和模式按钮
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
        # Latest release tag the user chose to skip, so the startup tray
        # notification is not re-shown for that version ("" = never skipped).
        "skippedUpdateVersion": "",
        "previewBoxPosition": "top-left",
        "cspVersion": "auto",
        "sai2Version": "auto",
        # SAI 内存写入后，把 SAI 自己的画笔色块也刷新一次：
        # "repaint"（默认）= 只重绘色块，纯重绘、不注入任何输入；
        # "full" = 额外给笔刷预览发一次点击（SAI 会记住这次按下点，
        #          下一笔可能出现楔形起笔，需要自行权衡）；
        # "off" = 完全不动 SAI 界面（旧行为）
        "saiUiRefresh": "repaint",
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
        "labViewShape": "square",
        "labHarmonyMode": "analogous",
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
        # 边框主题（窗口外框 / 分组框 / 数值框）。"auto" = 跟随滑块样式配对，
        # 其余取值见 ui/border_themes.py 的 BORDER_THEMES。
        "borderStyle": "auto",
        "followMouseEnabled": False,
        "noFocusMode": False,
        "showLabLightnessSlider": False,
        "slidersSplit": False,               # 滑块列并排（B-4 可拖动分割）
        "slidersTabs": False,                # 滑块组分页签叠放（B-4）
        "panelDrag": False,                  # 面板抓手：拖拽重排（B-4）
        # 浮出成独立窗口的面板 → 几何 {id: [x, y, w, h]}（B-5）。停靠树里
        # 仍然留着它们的位置，所以收回来能回到原处。
        "floatingPanels": {},
        "syncSoftware": "csp",
        "psVersion": "auto",
        "uiScale": 100,
        # 窗口背景 / 边框的不透明度（百分比，100 = 完全不透明）。只作用于
        # 窗口底色、外框与标题栏那一条 chrome，滑块 / 色轮 / 色块等内容
        # 永远不透明，取值范围见 ui/chrome_opacity.py。
        "backgroundOpacity": 100,
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


def _migrate_1_to_2(cfg: dict) -> dict:
    """Take SAI UI refresh off the click-injecting mode.

    ``saiUiRefresh`` briefly defaulted to ``"full"``, which posts a mouse click
    to SAI's brush preview so it re-renders. SAI treats that click as real
    input and remembers its button-down point, so the next canvas stroke can
    start with a wedge sweeping from it. Nobody chose that deliberately — it
    was the default — so the stored value is moved to the input-free mode.
    Anyone who wants the preview refresh can opt in again in the settings.
    """
    if str(cfg.get("saiUiRefresh", "")).strip().lower() == "full":
        cfg["saiUiRefresh"] = "repaint"
    return cfg


def _migrate_2_to_3(cfg: dict) -> dict:
    """Drop ``labVisualizerMaxVal``, which was write-only.

    The settings-reload path stamped this key on every load but nothing ever
    read it back: the LAB/OKLab plane's axis limit is owned by
    ``LabSquare.set_render_mode`` (110.0 / 0.3), and the stored oklab value
    (0.4) did not even match. Removing it keeps saved configs honest about
    which keys actually drive behaviour.
    """
    cfg.pop("labVisualizerMaxVal", None)
    return cfg


# Registered migrations, keyed by the target schema version they produce.
_CONFIG_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _migrate_0_to_1,
    2: _migrate_1_to_2,
    3: _migrate_2_to_3,
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


# Keys whose values must be real booleans / ints. Hand-edited or legacy
# configs may carry strings ("false", "abc") that would silently break the
# UI (e.g. ``cfg["uiScale"] / 100.0`` raising TypeError). Invalid values are
# dropped so _merge_with_defaults re-fills the default.
_BOOL_KEYS = frozenset({
    "showSlidersRGB", "showSlidersHSV", "showSlidersHSL", "showSlidersLAB",
    "showSlidersOKLab", "showSlidersOKLCh", "showSlidersHistory",
    "showTitleBar", "showTaskbarIcon", "lockWindowSize", "lockWindowPosition",
    "onlyShowInCsp", "openAtLogin", "checkUpdatesOnStartup",
    "followMouseEnabled", "noFocusMode", "showLabLightnessSlider",
    "slidersSplit", "slidersTabs", "panelDrag",
    "flipColorWheelHorizontally", "hideHueRing", "showModuleSwitchButton",
    "showLabToggleButton", "showLabShapeButton", "showLabHarmonyButton",
})
_INT_KEYS = frozenset({
    "uiScale", "pickerZoom", "historyColumns", "historyRows",
    "historySwatchSize", "sliderScrollStep", "sliderSameSpace",
    "sliderDiffSpace", "backgroundOpacity",
})


def _sanitize_types(cfg: dict) -> dict:
    """Coerce known keys to bool/int, dropping values that cannot convert."""
    for key in _BOOL_KEYS:
        value = cfg.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value in (0, 1):
            cfg[key] = bool(value)
        elif key in cfg:
            cfg.pop(key, None)  # 非法值删除 → merge 回填默认
    for key in _INT_KEYS:
        value = cfg.get(key)
        if isinstance(value, bool):
            cfg.pop(key, None)
            continue
        if isinstance(value, int):
            continue
        try:
            cfg[key] = int(value)
        except (TypeError, ValueError):
            cfg.pop(key, None)
    return cfg


def load_hotkey_config():
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if not isinstance(loaded, dict):
                    return dict(default_hotkey_config())
                loaded = migrate_config(loaded)
                loaded = _sanitize_types(loaded)
                return normalize_slider_orders(_merge_with_defaults(loaded))
        except Exception:
            pass
    return normalize_slider_orders(migrate_config(dict(default_hotkey_config())))

def save_hotkey_config(cfg):
    cfg.setdefault(CONFIG_SCHEMA_KEY, CONFIG_SCHEMA_VERSION)
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    try:
        _atomic_write_json(path, cfg)
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
    """Migrate + sanitize + back-fill + normalize a raw config dict for import."""
    merged = migrate_config(dict(imported))
    merged = _sanitize_types(merged)
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

