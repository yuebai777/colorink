
"""Tests for ui.color_conversions — the single source of truth for the
UI-layer colour-space math.

Covers:
* anchor values (cross-checked against coloraide as an independent oracle),
* round-trip stability for every space,
* near-achromatic snapping (the drift-prevention rule that keeps
  RGB→HSV→sync stable for grays),
* CSS Color 4 style gamut mapping (chroma reduction preserves L and hue),
* optional cross-validation against coloraide when it is installed
  (requirements-dev.txt).
"""

import math
import random

import numpy as np
import pytest

from ui import color_conversions as cc
from ui import oklab_colors  # compatibility shim must keep working

# ── Anchors (computed with coloraide 8.10, independent implementation) ──
ANCHORS = [
    # (r, g, b, oklab(L,a,b), oklch(L,C,h), lab(L,a,b))
    (255, 0, 0, (0.627955, 0.224863, 0.125846), (0.627955, 0.257683, 29.233880), (54.290541, 80.804928, 69.890965)),
    (0, 255, 0, (0.866440, -0.233888, 0.179498), (0.866440, 0.294827, 142.495345), (87.818534, -79.271061, 80.994581)),
    (0, 0, 255, (0.452014, -0.032457, -0.311528), (0.452014, 0.313214, 264.052023), (29.568302, 68.287365, -112.029710)),
    (255, 255, 255, (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
    (0, 0, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    (128, 128, 128, (0.599871, 0.0, 0.0), (0.599871, 0.0, 0.0), (53.585013, 0.0, 0.0)),
    (200, 50, 90, (0.558752, 0.183336, 0.033411), (0.558752, 0.186356, 10.328298), (46.654243, 60.550614, 14.582134)),
]


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ── Forward conversions hit the reference anchors ───────────────────────


@pytest.mark.parametrize("r,g,b,oklab,oklch,lab", ANCHORS)
def test_rgb_to_oklab_anchor(r, g, b, oklab, oklch, lab):
    assert _close(cc.rgb_to_oklab(r, g, b), oklab, 1e-5)


@pytest.mark.parametrize("r,g,b,oklab,oklch,lab", ANCHORS)
def test_rgb_to_oklch_anchor(r, g, b, oklab, oklch, lab):
    L, C, h = cc.rgb_to_oklch(r, g, b)
    assert abs(L - oklch[0]) <= 1e-5
    assert abs(C - oklch[1]) <= 1e-5
    if oklch[1] > 1e-6:  # achromatic hues are arbitrary
        assert abs(h - oklch[2]) <= 0.02


@pytest.mark.parametrize("r,g,b,oklab,oklch,lab", ANCHORS)
def test_rgb_to_lab_anchor(r, g, b, oklab, oklch, lab):
    assert _close(cc.rgb_to_lab(r, g, b), lab, 0.1)


# ── Round trips ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("r,g,b", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (1, 1, 2), (200, 50, 90)])
def test_roundtrip_oklab(r, g, b):
    L, a, bb = cc.rgb_to_oklab(r, g, b)
    r2, g2, b2 = cc.oklab_to_rgb(L, a, bb)
    assert _close((r2, g2, b2), (r, g, b), 0.5)


@pytest.mark.parametrize("r,g,b", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (1, 1, 2), (200, 50, 90)])
def test_roundtrip_oklch(r, g, b):
    L, C, h = cc.rgb_to_oklch(r, g, b)
    r2, g2, b2 = cc.oklch_to_rgb(L, C, h)
    assert _close((r2, g2, b2), (r, g, b), 0.5)


@pytest.mark.parametrize("r,g,b", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128), (1, 1, 2), (200, 50, 90)])
def test_roundtrip_lab(r, g, b):
    l, a, bb = cc.rgb_to_lab(r, g, b)
    r2, g2, b2 = cc.lab_to_rgb(l, a, bb)
    assert _close((r2, g2, b2), (r, g, b), 1.0)


def test_roundtrip_random_grid():
    rng = random.Random(1234)
    for _ in range(200):
        r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
        L, a, bb = cc.rgb_to_oklab(r, g, b)
        r2, g2, b2 = cc.oklab_to_rgb(L, a, bb)
        assert max(abs(x - y) for x, y in zip((r2, g2, b2), (r, g, b))) <= 0.5


# ── Near-achromatic snapping (drift prevention for HSV sync) ────────────


def test_achromatic_rgb_snaps_to_zero_chroma():
    L, a, b = cc.rgb_to_oklab(128, 128, 128)
    assert a == 0.0 and b == 0.0


def test_oklab_achromatic_snap():
    # |a|,|b| below 0.002 → exact gray, so RGB→HSV never gets hue noise
    r, g, b = cc.oklab_to_rgb(0.6, 0.001, 0.001)
    assert abs(r - g) < 1e-9 and abs(g - b) < 1e-9


def test_dark_near_gray_not_zeroed():
    # Channel diff < 0.01 must NOT be snapped (visibly blue dark colours)
    L, a, b = cc.rgb_to_oklab(1, 1, 2)
    assert abs(a) > 0.0 or abs(b) > 0.0


# ── Gamut mapping: chroma reduction keeps L and hue ─────────────────────


def test_map_oklch_in_gamut_input_unchanged():
    r, g, b = 255, 0, 0
    L, C, h = cc.rgb_to_oklch(r, g, b)
    assert _close(cc.map_oklch_to_gamut(L, C, h), (L, C, h), 1e-9)


def test_map_oklch_out_of_gamut_reduces_chroma_keeps_l_and_h():
    L, C, h = 0.5, 0.9, 300.0  # C way beyond any sRGB boundary
    Lm, Cm, hm = cc.map_oklch_to_gamut(L, C, h)
    assert abs(Lm - L) < 1e-9
    assert abs(hm - h) < 1e-9
    assert Cm < C
    r, g, b = cc.oklch_to_rgb(Lm, Cm, hm)
    assert cc.is_in_gamut(r, g, b)
    # and the boundary is tight: slightly more chroma is out of gamut
    r2, g2, b2 = cc.oklch_to_rgb(Lm, Cm + 0.001, hm)
    assert not cc.is_in_gamut(r2, g2, b2)


def test_map_oklab_keeps_hue_ray():
    L, a, b = 0.5, 0.5, 0.5  # hue = 45°
    Lm, am, bm = cc.map_oklab_to_gamut(L, a, b)
    assert abs(Lm - L) < 1e-9
    # hue angle preserved
    h0 = math.degrees(math.atan2(b, a))
    h1 = math.degrees(math.atan2(bm, am))
    assert abs(h0 - h1) < 1e-6
    r, g, bb = cc.oklab_to_rgb(Lm, am, bm)
    assert cc.is_in_gamut(r, g, bb)


def test_map_lab_keeps_hue_ray():
    l, a, b = 50.0, 150.0, 150.0  # clearly out of sRGB
    lm, am, bm = cc.map_lab_to_gamut(l, a, b)
    assert abs(lm - l) < 1e-9
    assert abs(am / bm - a / b) < 1e-6
    r, g, bb = cc.lab_to_rgb(lm, am, bm)
    assert cc.is_in_gamut(r, g, bb)


def test_map_lab_achromatic_unchanged():
    assert cc.map_lab_to_gamut(50.0, 0.0, 0.0) == (50.0, 0.0, 0.0)


def test_map_oklab_achromatic_unchanged():
    assert cc.map_oklab_to_gamut(0.5, 0.0, 0.0) == (0.5, 0.0, 0.0)


def test_map_oklch_extreme_lightness_no_crash():
    for L in (0.0, 1.0, 0.999):
        Lm, Cm, hm = cc.map_oklch_to_gamut(L, 0.5, 45.0)
        assert 0.0 <= Lm <= 1.0 and Cm >= 0.0


# ── Gamut boundary search ───────────────────────────────────────────────


@pytest.mark.parametrize("L,h", [(0.5, 0.0), (0.5, 90.0), (0.5, 300.0), (0.3, 45.0), (0.8, 200.0)])
def test_find_max_oklch_c_boundary(L, h):
    max_c = cc.find_max_oklch_c(L, h)
    r, g, b = cc.oklch_to_rgb(L, max_c, h)
    assert cc.is_in_gamut(r, g, b)
    r2, g2, b2 = cc.oklch_to_rgb(L, max_c + 0.005, h)
    assert not cc.is_in_gamut(r2, g2, b2)


# ── Vectorized (numpy) variants agree with the scalar functions ─────────


def test_lab_array_matches_scalar():
    rng = random.Random(5)
    for _ in range(100):
        l, a, b = rng.uniform(0, 100), rng.uniform(-128, 127), rng.uniform(-128, 127)
        s = cc.lab_to_rgb(l, a, b)
        v = cc.lab_to_rgb_array(np.array([l]), np.array([a]), np.array([b]))
        assert all(abs(s[i] - float(v[i][0])) < 1e-9 for i in range(3))


def test_oklab_array_matches_scalar():
    rng = random.Random(6)
    for _ in range(100):
        L, a, b = rng.uniform(0, 1), rng.uniform(-0.4, 0.4), rng.uniform(-0.4, 0.4)
        s = cc.oklab_to_rgb(L, a, b)
        v = cc.oklab_to_rgb_array(np.array([L]), np.array([a]), np.array([b]))
        assert all(abs(s[i] - float(v[i][0])) < 1e-9 for i in range(3))


def test_array_broadcasting_shapes():
    L = np.full((3, 1), 0.5)
    a = np.linspace(-0.4, 0.4, 4)[None, :]
    b = np.linspace(-0.4, 0.4, 3)[:, None]
    r, g, bl = cc.oklab_to_rgb_array(L, a, b)
    assert r.shape == (3, 4) and g.shape == (3, 4) and bl.shape == (3, 4)


def test_srgb_gamma_encode_array_matches_scalar():
    rng = random.Random(8)
    for _ in range(100):
        c = rng.uniform(-0.2, 1.2)
        assert abs(cc.srgb_gamma_encode(c) - float(cc.srgb_gamma_encode_array(np.array([c]))[0])) < 1e-12


# ── HSV / HSL scalar helpers match colorsys and round-trip ────────────────


def test_scalar_hsv_hsl_match_colorsys():
    import colorsys
    rng = random.Random(11)
    for _ in range(200):
        r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
        h, s, v = cc.rgb_to_hsv(r, g, b)
        ch, cs, cv = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        assert abs(h - ch * 360.0) < 1e-9
        assert abs(s - cs * 100.0) < 1e-9
        assert abs(v - cv * 100.0) < 1e-9
        r2, g2, b2 = cc.hsv_to_rgb(h, s, v)
        assert abs(r2 - r) < 1e-6 and abs(g2 - g) < 1e-6 and abs(b2 - b) < 1e-6

        h2, l2, s2 = cc.rgb_to_hsl(r, g, b)
        ch2, cl2, cs2 = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        assert abs(h2 - ch2 * 360.0) < 1e-9
        assert abs(l2 - cl2 * 100.0) < 1e-9
        assert abs(s2 - cs2 * 100.0) < 1e-9
        r3, g3, b3 = cc.hsl_to_rgb(h2, l2, s2)
        assert abs(r3 - r) < 1e-6 and abs(g3 - g) < 1e-6 and abs(b3 - b) < 1e-6


def test_scalar_hsv_hsl_bridge_roundtrip():
    for (h, s, v) in [(30.0, 80.0, 60.0), (200.0, 50.0, 90.0), (0.0, 0.0, 50.0), (330.0, 100.0, 100.0)]:
        h2, l, s2 = cc.hsv_to_hsl(h, s, v)
        hb, sb, vb = cc.hsl_to_hsv(h2, l, s2)
        assert abs(hb - h) < 1e-9
        assert abs(sb - s) < 1e-9
        assert abs(vb - v) < 1e-9


def test_vectorized_hsv_hls_match_colorsys():
    import colorsys
    from ui.slice_prewarm import _hls_to_rgb, _hsv_to_rgb
    hue = 137.0
    s_grid = np.linspace(0.0, 1.0, 7)
    t_grid = np.linspace(0.0, 1.0, 7)
    ss, vv = np.meshgrid(s_grid, t_grid)
    r, g, b = _hsv_to_rgb(hue, ss, vv)
    for i in range(ss.shape[0]):
        for j in range(ss.shape[1]):
            cr, cg, cb = colorsys.hsv_to_rgb(hue / 360.0, ss[i, j], vv[i, j])
            assert abs(r[i, j] - cr) < 1e-9
            assert abs(g[i, j] - cg) < 1e-9
            assert abs(b[i, j] - cb) < 1e-9
    ll, sl = np.meshgrid(t_grid, s_grid)
    r2, g2, b2 = _hls_to_rgb(hue, ll, sl)
    for i in range(ll.shape[0]):
        for j in range(ll.shape[1]):
            cr, cg, cb = colorsys.hls_to_rgb(hue / 360.0, ll[i, j], sl[i, j])
            assert abs(r2[i, j] - cr) < 1e-9
            assert abs(g2[i, j] - cg) < 1e-9
            assert abs(b2[i, j] - cb) < 1e-9


# ── Compatibility shim ──────────────────────────────────────────────────


def test_oklab_colors_shim_reexports():
    for name in ("rgb_to_oklab", "oklab_to_rgb", "rgb_to_oklch", "oklch_to_rgb"):
        assert getattr(oklab_colors, name) is getattr(cc, name)


# ── Cross-validation against coloraide (independent oracle) ─────────────


coloraide = pytest.importorskip("coloraide", reason="coloraide not installed (requirements-dev.txt)")


def _oracle_oklab(r, g, b):
    return tuple(coloraide.Color("srgb", [r / 255, g / 255, b / 255]).convert("oklab").coords())


def test_cross_validate_oklab_grid():
    rng = random.Random(99)
    for _ in range(200):
        r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
        assert _close(cc.rgb_to_oklab(r, g, b), _oracle_oklab(r, g, b), 1e-5)


def test_cross_validate_oklch_grid():
    rng = random.Random(98)
    for _ in range(200):
        r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
        L, C, h = cc.rgb_to_oklch(r, g, b)
        oL, oC, oh = tuple(coloraide.Color("srgb", [r / 255, g / 255, b / 255]).convert("oklch").coords())
        assert abs(L - oL) <= 1e-5 and abs(C - oC) <= 1e-5
        if C > 1e-6:
            assert abs(h - oh) <= 0.02


def test_cross_validate_lab_grid():
    rng = random.Random(97)
    for _ in range(200):
        r, g, b = rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)
        oracle = tuple(coloraide.Color("srgb", [r / 255, g / 255, b / 255]).convert("lab").coords())
        assert _close(cc.rgb_to_lab(r, g, b), oracle, 0.1)


# ── VHSV (Value-compensated HSV) tests ───────────────────────────────────


def test_vhsv_boundary_conditions():
    for s, v in [(0.0, 0.0), (0.0, 100.0), (100.0, 100.0)]:
        _, sv, vv = cc.hsv_to_vhsv(45.0, s, v)
        assert abs(sv - s) < 1e-10
        assert abs(vv - v) < 1e-10
        _, s_back, v_back = cc.vhsv_to_hsv(45.0, sv, vv)
        assert abs(s_back - s) < 1e-10
        assert abs(v_back - v) < 1e-10


def test_vhsv_hsv_roundtrip_grid():
    for s_int in range(0, 101, 5):
        for v_int in range(1, 101, 5):
            s, v = float(s_int), float(v_int)
            _, sv, vv = cc.hsv_to_vhsv(120.0, s, v)
            assert 0.0 <= sv <= 100.0
            assert 0.0 <= vv <= 100.0
            _, sb, vb = cc.vhsv_to_hsv(120.0, sv, vv)
            assert abs(sb - s) < 1e-10
            assert abs(vb - v) < 1e-10


def test_vhsv_strict_monotonicity():
    for v in [20.0, 50.0, 80.0, 100.0]:
        prev_s = -1.0
        for s in np.linspace(0.0, 100.0, 51):
            _, sv, _ = cc.hsv_to_vhsv(0.0, s, v)
            assert sv >= prev_s
            prev_s = sv

    for s in [20.0, 50.0, 80.0, 100.0]:
        prev_v = -1.0
        for v in np.linspace(1.0, 100.0, 51):
            _, _, vv = cc.hsv_to_vhsv(0.0, s, v)
            assert vv > prev_v
            prev_v = vv


def test_vhsv_rgb_roundtrip():
    assert cc.vhsv_to_rgb(0.0, 100.0, 100.0) == (255.0, 0.0, 0.0)
    assert cc.vhsv_to_rgb(120.0, 100.0, 100.0) == (0.0, 255.0, 0.0)
    assert cc.vhsv_to_rgb(240.0, 100.0, 100.0) == (0.0, 0.0, 255.0)
    assert cc.vhsv_to_rgb(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)
    assert cc.vhsv_to_rgb(0.0, 0.0, 100.0) == (255.0, 255.0, 255.0)

    rng = random.Random(42)
    for _ in range(100):
        r, g, b = float(rng.randint(0, 255)), float(rng.randint(0, 255)), float(rng.randint(0, 255))
        h, s, v = cc.rgb_to_vhsv(r, g, b)
        r2, g2, b2 = cc.vhsv_to_rgb(h, s, v)
        assert abs(r2 - r) < 1e-4 and abs(g2 - g) < 1e-4 and abs(b2 - b) < 1e-4


def test_sai2_vhsv_ground_truth():
    """Verify exact empirical measurements reported from PaintTool SAI2 vs CSP."""
    # User's test case: in SAI2, H=103, S=100 with V varying in [80, 50, 30, 15]
    # Under standard HSV (CSP):
    # V=80 -> H=108, S=100, V=80
    # V=50 -> H=120, S=100, V=50
    # V=30 -> H=120, S=100, V=30
    # V=15 -> H=120, S=100, V=15
    test_cases = [
        (80.0, 108.0, 100.0, 80.0),
        (50.0, 120.0, 100.0, 50.0),
        (30.0, 120.0, 100.0, 30.0),
        (15.0, 120.0, 100.0, 15.0),
    ]
    for v_in, exp_h, exp_s, exp_v in test_cases:
        r, g, b = cc.vhsv_to_rgb(103.0, 100.0, v_in)
        h_hsv, s_hsv, v_hsv = cc.rgb_to_hsv(r, g, b)
        assert round(h_hsv) == pytest.approx(exp_h, abs=1.0)
        assert round(s_hsv) == pytest.approx(exp_s, abs=0.5)
        assert round(v_hsv) == pytest.approx(exp_v, abs=0.5)

