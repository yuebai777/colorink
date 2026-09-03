"""Pure harmony-mode math for the circulant LAB disc.

The harmony offsets are expressed as OKLab/CIELAB hue-angle offsets in
degrees relative to the base colour.  A mode also always includes the base
colour itself (offset 0.0), which is drawn as the large indicator dot.
"""

from __future__ import annotations

HARMONY_MODE_OFFSETS: dict[str, tuple[float, ...]] = {
    # The base colour (0.0) must always be the FIRST entry: the disc draws
    # the base as the large indicator dot and skips index 0 when painting /
    # hit-testing the small harmony dots.
    "complementary": (0.0, 180.0),
    "split": (0.0, 150.0, 210.0),
    "analogous": (0.0, -30.0, 30.0),
    "triadic": (0.0, 120.0, 240.0),
    # Procreate-style "rectangle": four colours 90° apart, which on the hue
    # circle form a square (tetradic scheme with the base at 0°).
    "rectangle": (0.0, 90.0, 180.0, 270.0),
}

HARMONY_MODE_NAMES: dict[str, str] = {
    "complementary": "互补",
    "split": "补色分割",
    "analogous": "近似",
    "triadic": "三等分",
    "rectangle": "矩形",
}

DEFAULT_HARMONY_MODE = "analogous"


def is_valid_harmony_mode(mode: str) -> bool:
    return mode in HARMONY_MODE_OFFSETS


def harmony_hue_offsets(mode: str) -> tuple[float, ...]:
    """Return the hue offsets for *mode*, always including the base (0.0).

    Unknown modes fall back to the analogous preset so the UI never crashes
    on a stale config value.
    """
    if mode not in HARMONY_MODE_OFFSETS:
        mode = DEFAULT_HARMONY_MODE
    return HARMONY_MODE_OFFSETS[mode]
