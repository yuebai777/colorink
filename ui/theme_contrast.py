"""Pick text colours that stay legible on whatever surface they sit on.

The settings UI paints two different surfaces from the active theme: the
panel itself uses the *bar* colour (the darker chrome tone) while inputs —
combo boxes, buttons, the value fields — use the *body* colour. The ink was
derived from the body colour alone, so any theme whose two tones disagree in
lightness (the eyedropper theme in particular, where the user picks the two
colours independently) ended up drawing dark text on a dark panel.

Choosing the ink per surface fixes that at the source. Kept free of Qt so the
rule can be unit-tested on plain colour strings, and it reuses the same
luminance test ``ui.border_themes`` already applies to value-box text, so the
whole app agrees on what counts as a light surface.
"""

from ui.border_themes import is_light_color

#: Ink pair used across the chrome — near-black on light, white on dark.
DARK_INK = "#222222"
LIGHT_INK = "#ffffff"

#: Alpha applied to de-emphasized text (rail items, status hints).
MUTED_ALPHA = 0.45


def readable_ink(surface, *, light: str = LIGHT_INK, dark: str = DARK_INK) -> str:
    """Return the ink colour to draw on *surface*.

    Unparseable colours are treated as light surfaces (the same fallback
    ``is_light_color`` uses), which keeps text dark rather than emitting
    white-on-unknown.
    """
    return dark if is_light_color(surface) else light


def muted_ink(ink, alpha: float = MUTED_ALPHA) -> str:
    """Return *ink* as a de-emphasized ``rgba()`` string.

    Fading the ink towards its own surface keeps secondary text readable on
    both light and dark chrome, which a fixed grey cannot do.
    """
    text = str(ink).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        r, g, b = 34, 34, 34  # DARK_INK
    return f"rgba({r},{g},{b},{alpha:.2f})"
