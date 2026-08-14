"""Tests for ui.color_model — the unified Color / ColorState value model."""

import math

import pytest

from ui import color_conversions as cc
from ui.color_model import Color, ColorState


def test_from_rgb_matches_color_conversions():
    for (r, g, b) in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (200, 50, 90)]:
        c = Color.from_rgb(r, g, b)
        assert c.rgb == (r, g, b)
        assert abs(c.oklab[0] - cc.rgb_to_oklab(r, g, b)[0]) < 1e-9
        assert abs(c.oklab[1] - cc.rgb_to_oklab(r, g, b)[1]) < 1e-9
        assert abs(c.oklab[2] - cc.rgb_to_oklab(r, g, b)[2]) < 1e-9
        assert abs(c.oklch[0] - cc.rgb_to_oklch(r, g, b)[0]) < 1e-9
        assert abs(c.lab[0] - cc.rgb_to_lab(r, g, b)[0]) < 1e-9
        assert abs(c.hsv[0] - cc.rgb_to_hsv(r, g, b)[0]) < 1e-9
        assert abs(c.hls[0] - cc.rgb_to_hsl(r, g, b)[0]) < 1e-9


def test_source_space_roundtrips_exactly():
    # In-gamut OKLCh: the user's exact (L, C, h) is stored, not re-derived.
    c = Color.from_space("oklch", (0.6, 0.05, 123.456))
    assert abs(c.oklch[0] - 0.6) < 1e-9
    assert abs(c.oklch[1] - 0.05) < 1e-9
    assert abs(c.oklch[2] - 123.456) < 1e-9
    assert c.source_space == "oklch"

    # HSV gray: hue survives achromatic (source round-trip, not RGB re-derive).
    c2 = Color.from_space("hsv", (123.0, 0.0, 50.0))
    assert abs(c2.hsv[0] - 123.0) < 1e-9
    assert c2.hsv[1] == 0.0
    assert c2.hsv[2] == 50.0


def test_out_of_gamut_is_chroma_reduced():
    # C far beyond any sRGB boundary: L and hue preserved, C reduced to gamut.
    c = Color.from_space("oklch", (0.5, 0.9, 300.0))
    assert abs(c.oklch[0] - 0.5) < 1e-9
    assert abs(c.oklch[2] - 300.0) < 1e-9
    assert c.oklch[1] < 0.9
    assert cc.is_in_gamut(*c.rgb)


def test_color_is_always_in_gamut():
    cases = [
        ("rgb", (300.0, -50.0, 100.0)),
        ("oklch", (0.5, 0.9, 300.0)),
        ("lab", (50.0, 150.0, 150.0)),
        ("oklab", (0.5, 0.5, 0.5)),
        ("hsv", (999.0, 150.0, -10.0)),
        ("hls", (999.0, 150.0, -10.0)),
    ]
    for space, vals in cases:
        c = Color.from_space(space, vals)
        assert cc.is_in_gamut(*c.rgb), f"{space} produced out-of-gamut RGB {c.rgb}"


def test_unknown_space_rejected():
    with pytest.raises(ValueError):
        Color.from_space("cmyk", (0.0, 0.0, 0.0, 0.0))


def test_colorstate_remembers_hue_through_gray():
    st = ColorState()
    chromatic = st.set_from("oklch", (0.6, 0.2, 200.0))
    assert st._hue_oklch == pytest.approx(200.0)
    assert chromatic.oklch[2] == pytest.approx(200.0)

    # Gray via a *different* space must fall back onto the remembered hue.
    gray = st.set_from("rgb", (128, 128, 128))
    assert gray.oklch[1] == 0.0
    assert gray.oklch[2] == pytest.approx(200.0)


def test_colorstate_remembers_hsv_hue():
    st = ColorState()
    st.set_from("hsv", (270.0, 80.0, 60.0))
    assert st._hue_hsv == pytest.approx(270.0)
    gray = st.set_from("rgb", (128, 128, 128))
    assert gray.hsv[1] == 0.0
    assert gray.hsv[0] == pytest.approx(270.0)


def test_to_accessor():
    c = Color.from_rgb(255, 0, 0)
    assert c.to("rgb") == (255, 0, 0)
    assert c.to("oklch") == c.oklch
    assert len(c.to("lab")) == 3


def test_hue_normalized_to_range():
    c = Color.from_space("hsv", (400.0, 100.0, 100.0))
    assert 0.0 <= c.hsv[0] < 360.0
    assert c.hsv[0] == pytest.approx(40.0)
