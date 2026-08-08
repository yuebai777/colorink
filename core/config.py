import json
import os

CFG_NAME = "window-config.json"
HOTKEY_CFG_NAME = "hotkey-config.json"

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
        "previewBoxPosition": "top-left",
        "cspVersion": "auto",
        "sai2Version": "auto",
        "udmVersion": "auto",
        "ui-theme": "auto",
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


def load_hotkey_config():
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    default_cfg = default_hotkey_config()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if not isinstance(loaded, dict):
                    return default_cfg
                # The old visibility toggle was replaced by an explicit
                # top/bottom position setting; do not keep writing the
                # obsolete key back into the user's config.
                loaded.pop("showRinglessControlBar", None)
                # Legacy keys that were never wired into the app.
                for dead_key in ("injectionKey", "colorPickingEnabled", "cspAutoClick", "cspClickDelayMs"):
                    loaded.pop(dead_key, None)
                # merge defaults to ensure any missing keys are populated
                for k, v in default_cfg.items():
                    if k not in loaded:
                        loaded[k] = v
                return normalize_slider_orders(loaded)
        except Exception:
            pass
    return normalize_slider_orders(dict(default_cfg))

def save_hotkey_config(cfg):
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

