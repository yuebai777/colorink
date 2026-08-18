"""Shared module/constant definitions for the main-window mixins.

These values were previously private to ``ui.main_window``.  They are used
by multiple extracted mixins (slider setup, module switching, colour-space
projection), so they live in their own tiny module to avoid circular imports.
"""

# ── Color-space module definitions ────────────────────────────────────────
# Each module bundles a default wheel mode + slider subset (user can
# still toggle individual slider groups in settings via the "module
# default + adjustable" policy).
_MODULE_DEFS = {
    "hsv":  {"wheel": "hsv-square",   "sliders": ["HSV", "RGB", "LAB", "OKLab", "OKLCh"]},
    "hls":  {"wheel": "hls-triangle",  "sliders": ["HSL", "RGB", "LAB", "OKLab", "OKLCh"]},
    "rgb":  {"wheel": "rgb-slice",     "sliders": ["RGB", "HSV", "LAB", "OKLab", "OKLCh"]},
    "lch":  {"wheel": "oklch-slice",   "sliders": ["OKLCh", "OKLab", "RGB"]},
}
_MODULE_NAMES = {"hsv": "HSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
_MODULE_ORDER = ["hsv", "hls", "rgb", "lch"]

# ── Normalized chroma scale for the C_oklch slider ────────────────────────
# The C slider keeps its handle stable while L/H changes by representing a
# fraction of the current in-gamut chroma boundary. L-only drags still use
# the absolute C/H snapshot for authentic OKLCh coordinates.
_C_SCALE = 1000          # 0.001 chroma resolution (absolute C slider)
_C_SLIDER_MAX = 321      # C slider range → 0..0.321 absolute chroma (sRGB max C ≈ 0.3215)
