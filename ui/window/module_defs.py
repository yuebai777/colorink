"""Shared module/constant definitions for the main-window mixins.

These values were previously private to ``ui.main_window``.  They are used
by multiple extracted mixins (slider setup, module switching, colour-space
projection), so they live in their own tiny module to avoid circular imports.
"""

# ── Color-space module definitions ────────────────────────────────────────
# Each module bundles a default wheel mode + slider subset (user can
# still toggle individual slider groups in settings via the "module
# default + adjustable" policy).
ALL_SLIDERS = ["RGB", "HSV", "VHSV", "HSL", "LAB", "OKLab", "OKLCh"]

_MODULE_DEFS = {
    "hsv":   {"wheel": "hsv-square",    "sliders": ALL_SLIDERS},
    "vhsv":  {"wheel": "vhsv-square",   "sliders": ALL_SLIDERS},
    "hls":   {"wheel": "hls-triangle",  "sliders": ALL_SLIDERS},
    "rgb":   {"wheel": "rgb-slice",     "sliders": ALL_SLIDERS},
    "lch":   {"wheel": "oklch-slice",   "sliders": ALL_SLIDERS},
}
_MODULE_NAMES = {"hsv": "HSV", "vhsv": "VHSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
_MODULE_ORDER = ["hsv", "vhsv", "hls", "rgb", "lch"]
_LAB_VIZ_NAMES = {"lab": "LAB", "oklab": "OKLab"}
_LAB_VIZ_ORDER = ["lab", "oklab"]


def normalize_wheel_module_order(order) -> list[str]:
    """Return all canonical wheel module keys in the user's preferred order."""
    result: list[str] = []
    if isinstance(order, (list, tuple)):
        for item in order:
            key = str(item).lower()
            if key in _MODULE_DEFS and key not in result:
                result.append(key)
    for key in _MODULE_ORDER:
        if key not in result:
            result.append(key)
    return result


def normalize_wheel_module_tabs(
    enabled, order=None, active_module: str | None = None,
) -> list[str]:
    """Return enabled wheel module keys ordered by ``wheelModuleOrder``.

    Guarantees at least one module stays enabled (falling back to
    ``active_module`` or ``"hsv"``) so the wheel tab bar never empties out.
    """
    ordered = normalize_wheel_module_order(order)
    if isinstance(enabled, (list, tuple)):
        enabled_set = {str(k).lower() for k in enabled if str(k).lower() in _MODULE_DEFS}
    else:
        enabled_set = set(_MODULE_ORDER)
    tabs = [k for k in ordered if k in enabled_set]
    if not tabs:
        fallback = str(active_module or "hsv").lower()
        if fallback not in _MODULE_DEFS:
            fallback = "hsv"
        tabs = [fallback]
    return tabs


def normalize_lab_visualizer_order(order) -> list[str]:
    """Return ``["lab", "oklab"]`` in the user's preferred order."""
    result: list[str] = []
    if isinstance(order, (list, tuple)):
        for item in order:
            key = str(item).lower()
            if key in _LAB_VIZ_NAMES and key not in result:
                result.append(key)
    for key in _LAB_VIZ_ORDER:
        if key not in result:
            result.append(key)
    return result

# ── Normalized chroma scale for the C_oklch slider ────────────────────────
# The C slider keeps its handle stable while L/H changes by representing a
# fraction of the current in-gamut chroma boundary. L-only drags still use
# the absolute C/H snapshot for authentic OKLCh coordinates.
_C_SCALE = 1000          # 0.001 chroma resolution (absolute C slider)
_C_SLIDER_MAX = 321      # C slider range → 0..0.321 absolute chroma (sRGB max C ≈ 0.3215)
