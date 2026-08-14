"""Unified colour model: one immutable Color + one editor ColorState.

This is the single source of truth for a picked colour across every space
the UI exposes (sRGB / HSV / HSL / CIELAB / OKLab / OKLCh).  It replaces the
"canonical HSV in ColorWheel + RGB interchange in MainWindow + a dozen
_gamut_* / _oklch_target_* hint fields" pattern that previously had to be
kept in sync by hand.

Design rules:

* A Color is immutable and always in-gamut: constructing one applies gamut
  mapping exactly once, in the source space, so no caller ever has to clamp
  or re-map afterward.
* The source space round-trips exactly: the coordinates the user edited are
  stored as-is (after mapping) rather than being re-derived from RGB.  This
  is what kills the ~0.2 deg hue drift the old HSV->RGB->OKLCh round-trips
  produced.
* Hue memory (the hue to remember through achromatic colours) lives in
  ColorState, keyed separately for HSV and OKLCh because their hue angles
  differ.

This module depends only on ui.color_conversions (the math layer).  It must
NOT import core.brush_color_spaces - that module stays byte-compatible with
the CSP/UDM host struct layout.
"""
from __future__ import annotations

from dataclasses import dataclass

from ui import color_conversions as cc

SPACES = ("rgb", "hsv", "hls", "lab", "oklab", "oklch")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _gamut_map(space: str, values: tuple[float, ...]) -> tuple[float, ...]:
    """Map source-space coordinates into the sRGB gamut.

    This is the single authority for "every Color is in-gamut".  RGB/HSV/HSL
    are defined on the sRGB cube so they only clamp; Lab/OKLab/OKLCh use
    CSS Color 4 style chroma reduction (preserve L and hue).
    """
    if space == "rgb":
        return cc.clamp_rgb(*values)
    if space == "hsv":
        h, s, v = values
        return (h % 360.0, _clamp(s, 0.0, 100.0), _clamp(v, 0.0, 100.0))
    if space == "hls":
        h, l, s = values
        return (h % 360.0, _clamp(l, 0.0, 100.0), _clamp(s, 0.0, 100.0))
    if space == "lab":
        return cc.map_lab_to_gamut(*values)
    if space == "oklab":
        return cc.map_oklab_to_gamut(*values)
    if space == "oklch":
        return cc.map_oklch_to_gamut(*values)
    raise ValueError(f"unknown colour space: {space}")


def _space_to_rgb(space: str, values: tuple[float, ...]) -> tuple[float, float, float]:
    if space == "rgb":
        return values
    if space == "hsv":
        return cc.hsv_to_rgb(*values)
    if space == "hls":
        return cc.hsl_to_rgb(*values)
    if space == "lab":
        return cc.lab_to_rgb(*values)
    if space == "oklab":
        return cc.oklab_to_rgb(*values)
    if space == "oklch":
        return cc.oklch_to_rgb(*values)
    raise ValueError(f"unknown colour space: {space}")


@dataclass(frozen=True, slots=True)
class Color:
    """One colour expressed in every space, always in-gamut.

    Coordinate ranges:

    * rgb    - (r, g, b) ints 0-255
    * hsv    - (h 0-360, s 0-100, v 0-100)
    * hls    - (h 0-360, l 0-100, s 0-100)
    * lab    - (L 0-100, a, b)
    * oklab  - (L 0-1, a, b)
    * oklch  - (L 0-1, C, h 0-360)
    """

    rgb: tuple[int, int, int]
    hsv: tuple[float, float, float]
    hls: tuple[float, float, float]
    lab: tuple[float, float, float]
    oklab: tuple[float, float, float]
    oklch: tuple[float, float, float]
    source_space: str
    source_values: tuple[float, ...]

    # -- constructors -----------------------------------------------------

    @classmethod
    def from_rgb(
        cls,
        r: float,
        g: float,
        b: float,
        hue_hsv: float | None = None,
        hue_oklch: float | None = None,
    ) -> "Color":
        """Build from sRGB (0-255), clamped and quantized to 8-bit."""
        return cls.from_space("rgb", (r, g, b), hue_hsv, hue_oklch)

    @classmethod
    def from_space(
        cls,
        space: str,
        values,
        hue_hsv: float | None = None,
        hue_oklch: float | None = None,
    ) -> "Color":
        """Build from *space* coordinates; gamut-map once; round-trip exactly."""
        if space not in SPACES:
            raise ValueError(f"unknown colour space: {space}")
        values = tuple(float(v) for v in values)
        mapped = _gamut_map(space, values)          # 1) map once, in source space
        rgb = cc.clamp_rgb(*_space_to_rgb(space, mapped))
        r8, g8, b8 = (int(round(x)) for x in rgb)

        hsv = cc.rgb_to_hsv(*rgb)
        hls = cc.rgb_to_hsl(*rgb)
        lab = cc.rgb_to_lab(*rgb)
        oklab = cc.rgb_to_oklab(*rgb)
        oklch = cc.rgb_to_oklch(*rgb)

        # 2) Source space round-trips exactly: store the (mapped) values the
        #    user edited, not an RGB-derived re-computation.
        if space == "hsv":
            hsv = mapped
        elif space == "hls":
            hls = mapped
        elif space == "lab":
            lab = mapped
        elif space == "oklab":
            oklab = mapped
        elif space == "oklch":
            oklch = mapped

        # 3) Hue memory: when achromatic, inject the remembered hue so the
        #    colour stays on the hue ray it came from (cross-space grays).
        if hsv[1] < 0.5 and hue_hsv is not None:
            hsv = (hue_hsv, hsv[1], hsv[2])
            hls = (hue_hsv, hls[1], hls[2])
        if oklch[1] < 0.002 and hue_oklch is not None:
            oklch = (oklch[0], 0.0, hue_oklch)

        return cls((r8, g8, b8), hsv, hls, lab, oklab, oklch, space, values)

    # -- accessors --------------------------------------------------------

    def to(self, space: str) -> tuple:
        return getattr(self, space)

    @property
    def r(self) -> int:
        return self.rgb[0]

    @property
    def g(self) -> int:
        return self.rgb[1]

    @property
    def b(self) -> int:
        return self.rgb[2]


class ColorState:
    """Mutable editor state: the current Color plus hue memory.

    Holds no Qt objects and emits no signal on purpose - callers use the
    returned Color directly, which keeps the projection path explicit and
    easy to test.
    """

    def __init__(self) -> None:
        self.current: Color | None = None
        self._hue_hsv: float | None = None
        self._hue_oklch: float | None = None

    def set_from(self, space: str, values) -> Color:
        """Build a Color from *space* coords, remember hue, return it."""
        color = Color.from_space(space, values, self._hue_hsv, self._hue_oklch)
        self._remember(color)
        self.current = color
        return color

    def apply(self, color: Color) -> Color:
        """Adopt an already-built Color (external source) and remember its hue."""
        self._remember(color)
        self.current = color
        return color

    def _remember(self, color: Color) -> None:
        if color.hsv[1] >= 0.5:
            self._hue_hsv = color.hsv[0]
        if color.oklch[1] >= 0.002:
            self._hue_oklch = color.oklch[2]
