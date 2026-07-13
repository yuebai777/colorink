import os
import json

CFG_NAME = "window-config.json"
HOTKEY_CFG_NAME = "hotkey-config.json"

def get_user_data_dir():
    appdata = os.getenv("APPDATA")
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

def load_hotkey_config():
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    default_cfg = {
        "pickKey": "Ctrl+Shift+B",
        "injectionKey": "F12",
        "followMouseKey": "Ctrl+Shift+D",
        "hideWindowKey": "Ctrl+Shift+H",
        "grayscaleFilterKey": "Ctrl+Shift+G",
        "grayscaleFilterScreen": "all",
        "grayscaleFilterMode": "oklch",
        "grayscaleFilterBackend": "overlay",
        "colorPickingEnabled": True,
        "cspAutoClick": True,
        "cspClickDelayMs": 30,
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
        "sliderScrollStep": 1,
        "sliderSameSpace": 6,
        "sliderDiffSpace": 8,
        "showSlidersHistory": True,
        "orderSlidersHistory": 1,
        "historyColumns": 12,
        "historyRows": 3,
        "historySwatchSize": 18,
        "historyColors": [],
        "sliderStyle": "default",
        "followMouseEnabled": False,
        "autoFocusDrawingSoftware": False,
        "noFocusMode": False,
        "showLabLightnessSlider": False,
        "syncSoftware": "csp",
        "psVersion": "auto",
        "uiScale": 100,
        "flipColorWheelHorizontally": True,
        "pickerZoom": 6
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # merge defaults to ensure any missing keys are populated
                for k, v in default_cfg.items():
                    if k not in loaded:
                        loaded[k] = v
                return loaded
        except Exception:
            pass
    return default_cfg

def save_hotkey_config(cfg):
    path = os.path.join(get_user_data_dir(), HOTKEY_CFG_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
