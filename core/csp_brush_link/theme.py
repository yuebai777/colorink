"""CSP desktop theme reader.

Extracted from ``core.csp_brush_link``: reads CSP's UI theme preferences from
its sidecar SQLite config so the picker can visually match the host.
"""

from __future__ import annotations

import glob
import os
import sqlite3


def get_csp_theme() -> dict[str, str]:
    """Read CSP's UI theme preferences from its sidecar SQLite config.

    Returns a small dict describing the background / text / scrollbar
    colors the picker should adopt to visually match the host.  When CSP
    isn't installed or its preferences can't be parsed, falls back to a
    neutral gray theme.

    CSP stores theme state in ``Preference/Config.sqlite`` under
    ``APPDATA/CELSYSUserData/CELSYS[_EN]/CLIPStudioPaintVer*/`` (with
    several legacy path variants).  We probe all of them and use the
    most recently modified match.
    """
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE")

    candidate_patterns = []
    if appdata:
        candidate_patterns.extend([
            os.path.join(appdata, "CELSYSUserData", "CELSYS",     "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYSUserData", "CELSYS_EN",  "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaint",     "*", "Boot", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaint",     "*", "Boot", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
        ])
    if userprofile:
        candidate_patterns.extend([
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYSUserData", "CELSYS",              "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaint",     "*", "Boot",       "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
        ])

    found: list[str] = []
    for pattern in candidate_patterns:
        found.extend(glob.glob(pattern))

    if not found:
        return _theme_fallback()

    latest = max(found, key=os.path.getmtime)
    try:
        conn = sqlite3.connect(latest)
    except Exception as exc:
        return _theme_fallback(error=str(exc))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ApplicationThemeColor, ApplicationThemeColorLightDensity, "
            "ApplicationThemeColorDarkDensity FROM Interface"
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return _theme_fallback()

    theme_color, light_density, dark_density = row
    is_dark = _resolve_is_dark(theme_color)

    if is_dark:
        # CSP's dark gray baseline is 78; dark-density slides it by ~2.7 each step.
        gray = int(max(15, min(255, 78.0 + dark_density * 2.7)))
        edge_gray = int(max(0, min(255, 0.852 * gray - 10.5)))
        theme_name = "csp-dark"
    else:
        # CSP's light gray baseline is 241; light-density slides it by ~2.5 each step.
        gray = int(max(100, min(240, 241.0 + light_density * 2.5)))
        edge_gray = int(max(0, min(255, 1.45 * gray - 124.0)))
        theme_name = "csp-light"

    bg_hex      = f"#{gray:02x}{gray:02x}{gray:02x}"
    edge_hex    = f"#{edge_gray:02x}{edge_gray:02x}{edge_gray:02x}"
    text_color  = "#ffffff" if gray < 130 else "#222222"

    return {
        "theme":  theme_name,
        "bg":     bg_hex,
        "text":   text_color,
        "barBg":  edge_hex,
        "border": f"1px solid {edge_hex}",
    }


_GRAY_FALLBACK = {
    "theme":  "gray",
    "bg":     "#b2b2b2",
    "text":   "#222222",
    "barBg":  "#cbcccb",
    "border": "1px solid #cbcccb",
}


def _theme_fallback(error: str | None = None) -> dict[str, str]:
    if error is not None:
        return {"error": error, **_GRAY_FALLBACK}
    return dict(_GRAY_FALLBACK)


def _resolve_is_dark(theme_color: int) -> bool:
    """Map CSP's stored theme-color enum to a dark/light verdict.

    0 = dark, 1 = light, 2 = follow system.  When the per-system registry
    key is missing or unreadable, default to dark (CSP's most common
    setting among artists).
    """
    if theme_color == 2:
        return True
    if theme_color == 1:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return True
