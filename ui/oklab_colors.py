import math

# Björn Ottosson OKLab matrices
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
_M2_L  = [0.2104542553,  0.7936177850, -0.0040720468]
_M2_A  = [1.9779984951, -2.4285922050,  0.4505937099]
_M2_B  = [0.0259040371,  0.7827717662, -0.8086757660]


def _srgb_gamma_decode(c: float) -> float:
    """Linearize a single sRGB channel (0–1)."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _srgb_gamma_encode(c: float) -> float:
    """Apply sRGB gamma to a single linear channel."""
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """
    Convert sRGB (0-255 ints/floats) to OKLab.

    Returns (L, a, b) where L ∈ [0, 1], a ∈ [-0.4, 0.4], b ∈ [-0.4, 0.4]

    Pipeline: sRGB → sRGB gamma decode → linear RGB → LMS (M1) → cbrt(LMS) → OKLab (M2)
    """
    r_lin = _srgb_gamma_decode(max(0.0, min(255.0, r)) / 255.0)
    g_lin = _srgb_gamma_decode(max(0.0, min(255.0, g)) / 255.0)
    b_lin = _srgb_gamma_decode(max(0.0, min(255.0, b)) / 255.0)

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
    b = _M2_B[0] * l_cbrt + _M2_B[1] * m_cbrt + _M2_B[2] * s_cbrt

    # Snap near-achromatic RGB to exact a=b=0 — prevents chroma noise
    if abs(r - g) < 0.5 and abs(g - b) < 0.5 and abs(b - r) < 0.5:
        a = 0.0
        b = 0.0

    return (L, a, b)


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

    r8 = _srgb_gamma_encode(r_lin) * 255.0
    g8 = _srgb_gamma_encode(g_lin) * 255.0
    b8 = _srgb_gamma_encode(b_lin) * 255.0

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
