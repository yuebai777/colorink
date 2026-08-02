# -*- coding: utf-8 -*-

"""Deprecated compatibility shim.

All conversion math now lives in :mod:`ui.color_conversions` (the single
source of truth).  This module re-exports the historical API so existing
importers keep working during the migration; new code should import from
``ui.color_conversions`` directly.
"""

from ui.color_conversions import (  # noqa: F401
    oklab_to_rgb,
    rgb_to_oklab,
    oklch_to_rgb,
    rgb_to_oklch,
    srgb_gamma_decode,
    srgb_gamma_encode,
    map_oklab_to_gamut,
    map_oklch_to_gamut,
)

__all__ = [
    "oklab_to_rgb",
    "rgb_to_oklab",
    "oklch_to_rgb",
    "rgb_to_oklch",
    "srgb_gamma_decode",
    "srgb_gamma_encode",
    "map_oklab_to_gamut",
    "map_oklch_to_gamut",
]
