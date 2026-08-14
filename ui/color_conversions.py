
"""Single source of truth for UI-layer colour-space conversions.

Every colour-space transform the UI performs (slider handlers, groove
gradients, colour wheel math, LAB visualiser) lives here — and only here.
Historically the same math was inlined in ``ui/lab_visualizer.py``,
``ui/oklab_colors.py`` and ``ui/color_wheel.py``; those files now import
from this module.

Scope / contract:

* All functions are pure and side-effect free.
* ``rgb`` arguments are sRGB 0–255 (ints or floats, clamped on entry).
* ``rgb_to_*`` / ``*_to_rgb`` return exact mathematical results; they may
  be slightly outside [0, 255] — use :func:`clamp_rgb` / :func:`is_in_gamut`
  where display or gamut decisions are required.
* ``map_*_to_gamut`` implements CSS Color 4 style gamut mapping by
  **chroma reduction** (keep L and hue, shrink C until the colour is inside
  the sRGB gamut) instead of naive per-channel clipping (which shifts hue).
* Near-achromatic inputs are snapped to exact gray in the OKLab direction
  so the RGB→HSV round-trip used by drawing-software sync does not produce
  hue noise.

``core/brush_color_spaces.py`` intentionally does NOT import this module:
it mirrors the host applications' packed struct layout (GCR ink curves,
u32 scaling, byte offsets) and must stay byte-compatible with CSP/UDM.
"""

from __future__ import annotations

import colorsys
import math

import numpy as np

# ── sRGB gamma ──────────────────────────────────────────────────────────
# (identical piecewise curves as the CSS Color 4 spec / Ottosson's OKLab
# reference implementation; previously inlined in three modules)


def srgb_gamma_decode(c: float) -> float:
    """Linearize a single sRGB channel (0–1)."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def srgb_gamma_encode(c: float) -> float:
    """Apply sRGB gamma to a single linear channel (0–1)."""
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


# ── OKLab (Björn Ottosson) ──────────────────────────────────────────────
# sRGB linear RGB → LMS (M1)
_M1 = [
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
]

# OKLab → LMS' (M2 inverse)
_M2_INV = [
    [1.0,  0.3963377774,  0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480],
]

# LMS → linear sRGB (M1 inverse)
_M1_INV = [
    [ 4.0767416621, -3.3077115913,  0.2309699292],
    [-1.2684380046,  2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147,  1.7076147010],
]

# M2 forward coefficients (LMS' cbrt → OKLab), extracted for efficiency
_M2_L = [0.2104542553,  0.7936177850, -0.0040720468]
_M2_A = [1.9779984951, -2.4285922050,  0.4505937099]
_M2_B = [0.0259040371,  0.7827717662, -0.8086757660]


# ── CIELAB (D65 → D50 adaptation, matching the old ui/lab_visualizer) ───
_LAB_MAT_X = (0.4124564, 0.3575761, 0.1804375)
_LAB_MAT_Y = (0.2126729, 0.7151522, 0.0721750)
_LAB_MAT_Z = (0.0193339, 0.1191920, 0.9503041)

_LAB_D65_TO_D50 = (
    (1.0478112,  0.0228866, -0.0501270),
    (0.0295424,  0.9904844, -0.0170491),
    (-0.0092345, 0.0150436,  0.7521316),
)

_LAB_D50_TO_D65 = (
    (0.9554734, -0.0230984,  0.0632595),
    (-0.0283697,  1.0099956,  0.0210414),
    (0.0123140, -0.0205077,  1.3303659),
)

_LAB_D50_TO_RGB = (
    ( 3.2404542, -1.5371385, -0.4985314),
    (-0.9692660,  1.8760108,  0.0415560),
    ( 0.0556434, -0.2040259,  1.0572252),
)

_LAB_WHITE = (0.96422, 1.0, 0.82521)  # D50 white point


def _lab_f(t: float) -> float:
    return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + 16.0 / 116.0


def _lab_f_inv(t: float) -> float:
    return t * t * t if t > 0.206893 else (t - 16.0 / 116.0) / 7.787


# ── RGB ↔ OKLab / OKLCh ─────────────────────────────────────────────────


def rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """
    Convert sRGB (0-255 ints/floats) to OKLab.

    Returns (L, a, b) where L ∈ [0, 1], a ∈ [-0.4, 0.4], b ∈ [-0.4, 0.4]

    Pipeline: sRGB → sRGB gamma decode → linear RGB → LMS (M1) → cbrt(LMS) → OKLab (M2)
    """
    r_lin = srgb_gamma_decode(max(0.0, min(255.0, r)) / 255.0)
    g_lin = srgb_gamma_decode(max(0.0, min(255.0, g)) / 255.0)
    b_lin = srgb_gamma_decode(max(0.0, min(255.0, b)) / 255.0)

    # M1: linear sRGB → LMS
    m1 = _M1
    l_ = m1[0][0] * r_lin + m1[0][1] * g_lin + m1[0][2] * b_lin
    m_ = m1[1][0] * r_lin + m1[1][1] * g_lin + m1[1][2] * b_lin
    s_ = m1[2][0] * r_lin + m1[2][1] * g_lin + m1[2][2] * b_lin

    # cube root (copysign preserves sign for negative values)
    l_cbrt = math.copysign(abs(l_) ** (1.0 / 3.0), l_)
    m_cbrt = math.copysign(abs(m_) ** (1.0 / 3.0), m_)
    s_cbrt = math.copysign(abs(s_) ** (1.0 / 3.0), s_)

    # M2: cbrt(LMS) → OKLab
    L = _M2_L[0] * l_cbrt + _M2_L[1] * m_cbrt + _M2_L[2] * s_cbrt
    a = _M2_A[0] * l_cbrt + _M2_A[1] * m_cbrt + _M2_A[2] * s_cbrt
    # NOTE: the OKLab b channel must NOT be named `b` — that would shadow
    # the input parameter and silently break the achromatic snap below
    # (it would compare the *computed* b against r/g instead of the input).
    b_ok = _M2_B[0] * l_cbrt + _M2_B[1] * m_cbrt + _M2_B[2] * s_cbrt

    # Snap near-achromatic RGB to exact a=b=0 — prevents chroma noise.
    # Tolerance is 0.01 (vs the old 0.5) so dark colours like R=1,G=1,B=1.4
    # (visibly blue) are NOT zeroed.  Only true floating-point noise from
    # a mathematically perfect gray (channel‑diff < 0.01/255) is clamped.
    if abs(r - g) < 0.01 and abs(g - b) < 0.01 and abs(b - r) < 0.01:
        a = 0.0
        b_ok = 0.0

    return (L, a, b_ok)


def oklab_to_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    """
    Convert OKLab to sRGB (0-255 floats, may be out of gamut).

    Pipeline: OKLab → LMS' (M2_inv) → (LMS')^3 → linear RGB (M1_inv) → sRGB gamma encode → 0-255
    """
    # Snap near-achromatic to exact gray — prevents HSV hue noise
    if abs(a) < 0.002 and abs(b) < 0.002:
        a = 0.0
        b = 0.0

    m2i = _M2_INV
    l_ = m2i[0][0] * L + m2i[0][1] * a + m2i[0][2] * b
    m_ = m2i[1][0] * L + m2i[1][1] * a + m2i[1][2] * b
    s_ = m2i[2][0] * L + m2i[2][1] * a + m2i[2][2] * b

    # cube: (LMS')^3
    l_cubed = l_ * l_ * l_
    m_cubed = m_ * m_ * m_
    s_cubed = s_ * s_ * s_

    # M1 inverse: LMS → linear sRGB
    m1i = _M1_INV
    r_lin = m1i[0][0] * l_cubed + m1i[0][1] * m_cubed + m1i[0][2] * s_cubed
    g_lin = m1i[1][0] * l_cubed + m1i[1][1] * m_cubed + m1i[1][2] * s_cubed
    b_lin = m1i[2][0] * l_cubed + m1i[2][1] * m_cubed + m1i[2][2] * s_cubed

    r8 = srgb_gamma_encode(r_lin) * 255.0
    g8 = srgb_gamma_encode(g_lin) * 255.0
    b8 = srgb_gamma_encode(b_lin) * 255.0

    # Guard against NaN/Inf from extreme inputs
    if math.isnan(r8) or math.isinf(r8): r8 = 0.0
    if math.isnan(g8) or math.isinf(g8): g8 = 0.0
    if math.isnan(b8) or math.isinf(b8): b8 = 0.0

    return (r8, g8, b8)


def rgb_to_oklch(r: float, g: float, b: float) -> tuple[float, float, float]:
    """
    Convert sRGB to OKLCh (cylindrical OKLab).

    Returns (L, C, h) where L ∈ [0, 1], C ∈ [0, ~0.4], h ∈ [0, 360]
    Calls rgb_to_oklab then converts a,b to C,h.
    """
    L, a, b = rgb_to_oklab(r, g, b)
    C = math.sqrt(a * a + b * b)
    h = math.degrees(math.atan2(b, a))
    if h < 0.0:
        h += 360.0
    return (L, C, h)


def oklch_to_rgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    """
    Convert OKLCh to sRGB (0-255 floats).

    Converts L,C,h to a,b then calls oklab_to_rgb.
    """
    h_rad = math.radians(h)
    a = C * math.cos(h_rad)
    b = C * math.sin(h_rad)
    return oklab_to_rgb(L, a, b)


# ── RGB ↔ CIELAB (D65→D50) ──────────────────────────────────────────────


def rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """
    Convert sRGB (0-255) to CIELAB via D65→D50 adaptation.

    Returns (L, a, b) with L ∈ [0, 100], a/b roughly [-128, 127] for sRGB.
    """
    r_val = max(0.0, min(255.0, r)) / 255.0
    g_val = max(0.0, min(255.0, g)) / 255.0
    b_val = max(0.0, min(255.0, b)) / 255.0

    r_val = ((r_val + 0.055) / 1.055) ** 2.4 if r_val > 0.04045 else r_val / 12.92
    g_val = ((g_val + 0.055) / 1.055) ** 2.4 if g_val > 0.04045 else g_val / 12.92
    b_val = ((b_val + 0.055) / 1.055) ** 2.4 if b_val > 0.04045 else b_val / 12.92

    x = _LAB_MAT_X[0] * r_val + _LAB_MAT_X[1] * g_val + _LAB_MAT_X[2] * b_val
    y = _LAB_MAT_Y[0] * r_val + _LAB_MAT_Y[1] * g_val + _LAB_MAT_Y[2] * b_val
    z = _LAB_MAT_Z[0] * r_val + _LAB_MAT_Z[1] * g_val + _LAB_MAT_Z[2] * b_val

    x50 = _LAB_D65_TO_D50[0][0] * x + _LAB_D65_TO_D50[0][1] * y + _LAB_D65_TO_D50[0][2] * z
    y50 = _LAB_D65_TO_D50[1][0] * x + _LAB_D65_TO_D50[1][1] * y + _LAB_D65_TO_D50[1][2] * z
    z50 = _LAB_D65_TO_D50[2][0] * x + _LAB_D65_TO_D50[2][1] * y + _LAB_D65_TO_D50[2][2] * z

    x_scaled = x50 / _LAB_WHITE[0]
    y_scaled = y50 / _LAB_WHITE[1]
    z_scaled = z50 / _LAB_WHITE[2]

    fy = _lab_f(y_scaled)
    return (
        (116.0 * fy) - 16.0,
        500.0 * (_lab_f(x_scaled) - fy),
        200.0 * (fy - _lab_f(z_scaled)),
    )


def lab_to_rgb(l: float, a: float, b: float) -> tuple[float, float, float]:
    """
    Convert CIELAB to sRGB (0-255 floats, may be out of gamut).
    """
    y = (l + 16.0) / 116.0
    x = a / 500.0 + y
    z = y - b / 200.0

    x_val = _LAB_WHITE[0] * _lab_f_inv(x)
    y_val = _LAB_WHITE[1] * _lab_f_inv(y)
    z_val = _LAB_WHITE[2] * _lab_f_inv(z)

    x65 = _LAB_D50_TO_D65[0][0] * x_val + _LAB_D50_TO_D65[0][1] * y_val + _LAB_D50_TO_D65[0][2] * z_val
    y65 = _LAB_D50_TO_D65[1][0] * x_val + _LAB_D50_TO_D65[1][1] * y_val + _LAB_D50_TO_D65[1][2] * z_val
    z65 = _LAB_D50_TO_D65[2][0] * x_val + _LAB_D50_TO_D65[2][1] * y_val + _LAB_D50_TO_D65[2][2] * z_val

    r = _LAB_D50_TO_RGB[0][0] * x65 + _LAB_D50_TO_RGB[0][1] * y65 + _LAB_D50_TO_RGB[0][2] * z65
    g = _LAB_D50_TO_RGB[1][0] * x65 + _LAB_D50_TO_RGB[1][1] * y65 + _LAB_D50_TO_RGB[1][2] * z65
    bl = _LAB_D50_TO_RGB[2][0] * x65 + _LAB_D50_TO_RGB[2][1] * y65 + _LAB_D50_TO_RGB[2][2] * z65

    return (
        srgb_gamma_encode(r) * 255.0,
        srgb_gamma_encode(g) * 255.0,
        srgb_gamma_encode(bl) * 255.0,
    )


# ── Gamut helpers ───────────────────────────────────────────────────────


def is_in_gamut(r: float, g: float, b: float, epsilon: float = 0.5) -> bool:
    """True when the sRGB triple is (approximately) displayable."""
    return (-epsilon <= r <= 255.0 + epsilon
            and -epsilon <= g <= 255.0 + epsilon
            and -epsilon <= b <= 255.0 + epsilon)


def clamp_rgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Clamp a float RGB triple into [0, 255] (display safety net)."""
    return (max(0.0, min(255.0, r)),
            max(0.0, min(255.0, g)),
            max(0.0, min(255.0, b)))


def find_max_lab_c(L_val: float, a_dir: float, b_dir: float) -> float:
    """Binary search the max CIELAB chroma along direction (a_dir, b_dir)
    at lightness L_val that stays inside the sRGB gamut."""
    low = 0.0
    high = 150.0
    for _ in range(16):
        mid = (low + high) / 2.0
        r, g, b = lab_to_rgb(L_val, mid * a_dir, mid * b_dir)
        if 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= b <= 255.0:
            low = mid
        else:
            high = mid
    return low


def find_max_oklch_c(L: float, h: float) -> float:
    """Binary search for max OKLCh chroma at given L, h within sRGB gamut.

    Uses the same gamut test as oklch.com / @colordx/core: accept a colour
    when every linear-sRGB channel is in [0, 1] (here approximated as
    [-0.5, 255.5] for the gamma-encoded return of oklch_to_rgb).

    Returns the TRUE mathematical gamut boundary.  Very-dark in-gamut
    colours at low L are handled by the render loop's alpha blending,
    mirroring oklch.com's GPU shader where near-black pixels are
    indistinguishable from the dark page background.
    """
    if L <= 0.0:
        return 0.0

    lo, hi = 0.0, 0.6
    for _ in range(16):
        mid = (lo + hi) / 2.0
        r, g, b = oklch_to_rgb(L, mid, h)
        if 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= b <= 255.0:
            lo = mid
        else:
            hi = mid
    return lo


def map_oklch_to_gamut(L: float, C: float, h: float) -> tuple[float, float, float]:
    """Gamut-map OKLCh into sRGB by chroma reduction (CSS Color 4 style).

    Keeps L and hue exactly; reduces C to the gamut boundary when the
    requested chroma is not displayable.  In-gamut input is returned
    unchanged (minus float noise).
    """
    if C <= 0.0 or L <= 0.0 or L >= 1.0:
        return (max(0.0, min(1.0, L)), 0.0, h)
    max_c = find_max_oklch_c(L, h)
    if C <= max_c + 1e-5:
        # epsilon absorbs the binary-search resolution (~9e-6) so exact
        # in-gamut colours round-trip unchanged
        return (L, C, h)
    return (L, max_c, h)


def map_oklab_to_gamut(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Gamut-map OKLab into sRGB by chroma reduction along the a/b hue ray.

    Equivalent to map_oklch_to_gamut, expressed in cartesian coordinates:
    L and the a/b direction (OKLCh hue) are preserved.
    """
    C = math.sqrt(a * a + b * b)
    if C <= 1e-9:
        return (max(0.0, min(1.0, L)), 0.0, 0.0)
    h = math.degrees(math.atan2(b, a))
    if h < 0.0:
        h += 360.0
    Lm, Cm, h = map_oklch_to_gamut(L, C, h)
    h_rad = math.radians(h)
    return (Lm, Cm * math.cos(h_rad), Cm * math.sin(h_rad))


def map_lab_to_gamut(l: float, a: float, b: float) -> tuple[float, float, float]:
    """Gamut-map CIELAB into sRGB by chroma reduction along the a/b ray.

    Keeps L* and the a/b hue angle; shrinks chroma to the sRGB boundary.
    """
    C = math.sqrt(a * a + b * b)
    if C <= 1e-9:
        return (l, 0.0, 0.0)
    a_dir = a / C
    b_dir = b / C
    max_c = find_max_lab_c(l, a_dir, b_dir)
    if C <= max_c:
        return (l, a, b)
    return (l, a_dir * max_c, b_dir * max_c)


# ── Vectorized (numpy) variants — same matrices, array broadcasting ─────
# Used by the render paths (colour wheel slices, LAB plane) so a whole
# image is converted in one vectorized pass instead of per-pixel Python
# loops.  These are the ONLY consumers of the matrix constants besides the
# scalar functions above — the constants are not duplicated anywhere else.
#
# NOTE: the array variants intentionally omit the scalar functions'
# achromatic snap / NaN guards: render paths apply their own gamut mask,
# and per-pixel snapping would just add a where() for no visible gain.


def srgb_gamma_encode_array(c):
    """Vectorized srgb_gamma_encode over an ndarray (0-1 in, 0-1 out)."""
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.maximum(0.0, c) ** (1.0 / 2.4) - 0.055)


def lab_to_rgb_array(l, a, b):
    """Vectorized lab_to_rgb over broadcastable ndarray inputs."""
    y = (l + 16.0) / 116.0
    x = a / 500.0 + y
    z = y - b / 200.0
    x_val = _LAB_WHITE[0] * np.where(x > 0.206893, x ** 3, (x - 16.0 / 116.0) / 7.787)
    y_val = _LAB_WHITE[1] * np.where(y > 0.206893, y ** 3, (y - 16.0 / 116.0) / 7.787)
    z_val = _LAB_WHITE[2] * np.where(z > 0.206893, z ** 3, (z - 16.0 / 116.0) / 7.787)
    x65 = _LAB_D50_TO_D65[0][0] * x_val + _LAB_D50_TO_D65[0][1] * y_val + _LAB_D50_TO_D65[0][2] * z_val
    y65 = _LAB_D50_TO_D65[1][0] * x_val + _LAB_D50_TO_D65[1][1] * y_val + _LAB_D50_TO_D65[1][2] * z_val
    z65 = _LAB_D50_TO_D65[2][0] * x_val + _LAB_D50_TO_D65[2][1] * y_val + _LAB_D50_TO_D65[2][2] * z_val
    r = _LAB_D50_TO_RGB[0][0] * x65 + _LAB_D50_TO_RGB[0][1] * y65 + _LAB_D50_TO_RGB[0][2] * z65
    g = _LAB_D50_TO_RGB[1][0] * x65 + _LAB_D50_TO_RGB[1][1] * y65 + _LAB_D50_TO_RGB[1][2] * z65
    bl = _LAB_D50_TO_RGB[2][0] * x65 + _LAB_D50_TO_RGB[2][1] * y65 + _LAB_D50_TO_RGB[2][2] * z65
    return (srgb_gamma_encode_array(r) * 255.0,
            srgb_gamma_encode_array(g) * 255.0,
            srgb_gamma_encode_array(bl) * 255.0)


def oklab_to_rgb_array(L, a, b):
    """Vectorized oklab_to_rgb over broadcastable ndarray inputs."""
    l_ = L + _M2_INV[0][1] * a + _M2_INV[0][2] * b
    m_ = L + _M2_INV[1][1] * a + _M2_INV[1][2] * b
    s_ = L + _M2_INV[2][1] * a + _M2_INV[2][2] * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    r_lin = _M1_INV[0][0] * l3 + _M1_INV[0][1] * m3 + _M1_INV[0][2] * s3
    g_lin = _M1_INV[1][0] * l3 + _M1_INV[1][1] * m3 + _M1_INV[1][2] * s3
    b_lin = _M1_INV[2][0] * l3 + _M1_INV[2][1] * m3 + _M1_INV[2][2] * s3
    return (srgb_gamma_encode_array(r_lin) * 255.0,
            srgb_gamma_encode_array(g_lin) * 255.0,
            srgb_gamma_encode_array(b_lin) * 255.0)


def hsv_to_hls_floats(h, s, v):
    """Convert HSV (h 0–360, s 0–100, v 0–100) to normalized HLS floats."""
    # h: [0, 360], s: [0, 100], v: [0, 100]
    h_f = h / 360.0
    s_f = s / 100.0
    v_f = v / 100.0
    l_f = v_f * (1.0 - s_f / 2.0)
    if 0.0 < l_f < 1.0:
        hsl_s = (v_f - l_f) / min(l_f, 1.0 - l_f)
    else:
        hsl_s = 0.0
    return h_f, l_f, hsl_s


# ── RGB ↔ HSV / HSL (scalar, human ranges) ────────────────────────────────
# Single source of truth for the scalar HSV/HSL math.  These return floats;
# the QColor(int, int, int)-friendly int wrapper lives in ui.color_wheel.


def rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert sRGB (0–255) to HSV (h 0–360, s 0–100, v 0–100)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, s * 100.0, v * 100.0


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    """Convert HSV (h 0–360, s 0–100, v 0–100) to sRGB (0–255 floats)."""
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0, s / 100.0, v / 100.0)
    return r * 255.0, g * 255.0, b * 255.0


def rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert sRGB (0–255) to HSL (h 0–360, l 0–100, s 0–100)."""
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, l * 100.0, s * 100.0


def hsl_to_rgb(h: float, l: float, s: float) -> tuple[float, float, float]:
    """Convert HSL (h 0–360, l 0–100, s 0–100) to sRGB (0–255 floats)."""
    r, g, b = colorsys.hls_to_rgb((h % 360.0) / 360.0, l / 100.0, s / 100.0)
    return r * 255.0, g * 255.0, b * 255.0


def hsv_to_hsl(h: float, s: float, v: float) -> tuple[float, float, float]:
    """Convert HSV (h/s/v) to HSL (h 0–360, l 0–100, s 0–100)."""
    v_f = v / 100.0
    s_f = s / 100.0
    l_f = v_f * (1.0 - s_f / 2.0)
    hsl_s = 0.0
    if 0.0 < l_f < 1.0:
        hsl_s = (v_f - l_f) / min(l_f, 1.0 - l_f)
    return h % 360.0, l_f * 100.0, hsl_s * 100.0


def hsl_to_hsv(h: float, l: float, s: float) -> tuple[float, float, float]:
    """Convert HSL (h 0–360, l 0–100, s 0–100) to HSV (h/s/v 0–100)."""
    l_f = l / 100.0
    s_f = s / 100.0
    v = l_f + s_f * min(l_f, 1.0 - l_f)
    hsv_s = 0.0
    if v > 0.0001:
        hsv_s = 2.0 * (1.0 - l_f / v)
    return h % 360.0, hsv_s * 100.0, v * 100.0
