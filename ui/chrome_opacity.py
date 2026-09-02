"""Window chrome opacity — the background / frame alpha of the main window.

Colorink's main window is frameless and is already created with
``WA_TranslucentBackground``, so the only thing standing between the desktop
(usually the canvas the user is painting on) and the picker is the opaque
colour the theme paints on ``QWidget#CentralWidget``. Turning that colour —
and the frame drawn around it — into an ``rgba()`` colour is all it takes to
see through the panel.

This module owns that rule set: the config key, its clamped range and the
colour → ``rgba()`` conversion the stylesheet uses. It deliberately imports
no Qt, so the conversion can be reasoned about and unit-tested without a
QApplication, exactly like ``ui.border_themes``.

Scope: only *chrome* surfaces take the alpha — window background, window
frame, title-bar band and its divider. Sliders, value boxes, swatches, the
colour wheel and the LAB plane stay fully opaque; a colour tool that rendered
its own colours semi-transparently would be showing a colour the user did not
pick. The title band is painted *on top* of the window background, so the two
alphas compose there and the bar reads slightly denser than the body — which
is what keeps the drag handle findable at low opacity.
"""

#: Config key holding the chrome opacity, in percent.
CHROME_OPACITY_KEY = "backgroundOpacity"

#: Fully opaque — the historical look, and the default for every install.
CHROME_OPACITY_DEFAULT = 100

#: 0 = the chrome disappears completely and only the controls float over the
#: canvas. See CHROME_ALPHA_FLOOR for why that is not literally alpha 0.
CHROME_OPACITY_MIN = 0
CHROME_OPACITY_MAX = 100

#: Smallest alpha the chrome is ever painted with: one step above fully
#: transparent (1/255), which is invisible on screen but decisive for input.
#: A translucent Qt window is a per-pixel-alpha layered window, and Windows
#: routes the mouse *through* layered pixels whose alpha is exactly 0 — at a
#: true 0 the panel could no longer be dragged and every click beside a
#: slider would land on the canvas underneath (i.e. paint on the artwork).
CHROME_ALPHA_FLOOR = 1.0 / 255.0

#: Slider granularity, mirroring the UI-scale slider's 5% steps.
CHROME_OPACITY_STEP = 5


def clamp_chrome_opacity(value) -> int:
    """Clamp a raw config / UI value to a percentage in 0..100.

    Anything that is not a number at all (hand-edited config, ``None``)
    falls back to fully opaque rather than to an arbitrary transparency —
    an unreadable window is the worst possible failure mode here.
    """
    if isinstance(value, bool):  # bool is an int subclass; not a percentage
        return CHROME_OPACITY_DEFAULT
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        return CHROME_OPACITY_DEFAULT
    return max(CHROME_OPACITY_MIN, min(CHROME_OPACITY_MAX, percent))


def resolve_chrome_opacity(cfg) -> int:
    """Read the clamped chrome opacity out of a settings dict."""
    try:
        raw = cfg.get(CHROME_OPACITY_KEY, CHROME_OPACITY_DEFAULT)
    except AttributeError:
        return CHROME_OPACITY_DEFAULT
    return clamp_chrome_opacity(raw)


def _parse_hex(text: str):
    """Return (r, g, b) for ``#rgb`` / ``#rrggbb``, else None."""
    body = text[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    if len(body) != 6:
        return None
    try:
        return int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16)
    except ValueError:
        return None


def _parse_functional(text: str):
    """Return (r, g, b, a) for ``rgb()`` / ``rgba()``, else None.

    The alpha is normalised to 0..1 so an already-translucent colour keeps
    its own transparency and only gets *more* transparent, never less.
    """
    if not text.endswith(")"):
        return None
    prefix, _, rest = text.partition("(")
    if prefix.strip() not in ("rgb", "rgba"):
        return None
    parts = [part.strip() for part in rest[:-1].split(",")]
    if len(parts) not in (3, 4):
        return None
    try:
        channels = [max(0, min(255, int(round(float(part))))) for part in parts[:3]]
        alpha = float(parts[3]) if len(parts) == 4 else 1.0
    except ValueError:
        return None
    if alpha > 1.0:  # 0..255 form
        alpha /= 255.0
    return channels[0], channels[1], channels[2], max(0.0, min(1.0, alpha))


def with_opacity(color, percent) -> str:
    """Return *color* as stylesheet CSS carrying *percent* of its opacity.

    ``with_opacity("#b2b2b2", 60)`` → ``"rgba(178,178,178,0.600)"``.

    0% emits CHROME_ALPHA_FLOOR rather than a literal 0: visually that is
    the same nothing, but it keeps the window catching its own mouse events.

    Fully opaque requests, keyword colours (``transparent`` / ``none``) and
    anything this module cannot parse are returned untouched: an unchanged
    colour is always a valid stylesheet value, so an exotic colour format can
    only cost the transparency, never break the chrome.
    """
    text = str(color).strip()
    opacity = clamp_chrome_opacity(percent)
    if opacity >= CHROME_OPACITY_MAX or not text:
        return text
    if text.lower() in ("transparent", "none"):
        return text

    alpha = max(CHROME_ALPHA_FLOOR, opacity / 100.0)
    if text.startswith("#"):
        parsed = _parse_hex(text)
        if parsed is None:
            return text
        r, g, b = parsed
    else:
        parsed = _parse_functional(text)
        if parsed is None:
            return text
        r, g, b, existing_alpha = parsed
        alpha = max(CHROME_ALPHA_FLOOR, alpha * existing_alpha)
    return f"rgba({r},{g},{b},{alpha:.3f})"
