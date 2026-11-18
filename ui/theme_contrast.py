"""Pick text and chrome colours that stay legible on whatever surface they sit on.

The settings UI paints two different surfaces from the active theme: the
panel itself uses the *bar* colour (the darker chrome tone) while inputs —
combo boxes, buttons, the value fields — use the *body* colour. The ink was
derived from the body colour alone, so any theme whose two tones disagree in
lightness (the eyedropper theme in particular, where the user picks the two
colours independently) ended up drawing dark text on a dark panel.

Choosing the ink and derived borders per surface fixes that at the source.
Kept free of Qt so the rules can be unit-tested on plain colour strings, and
shared by `ui.border_themes`, `ui.slider_themes`, `ui.window.theme` and the
settings panel so the whole app agrees on contrast and luminance.
"""

from __future__ import annotations

#: Ink pair used across the chrome — near-black on light, white on dark.
DARK_INK = "#222222"
LIGHT_INK = "#ffffff"

#: Alpha applied to de-emphasized text (rail items, status hints).
MUTED_ALPHA = 0.45


def parse_hex(color) -> tuple[int, int, int] | None:
    """Parse ``#rgb`` / ``#rrggbb`` into ``(r, g, b)`` or return ``None``."""
    text = str(color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def to_hex(r: float, g: float, b: float) -> str:
    """Format clamped RGB components as ``#rrggbb``."""
    ri = max(0, min(255, int(round(r))))
    gi = max(0, min(255, int(round(g))))
    bi = max(0, min(255, int(round(b))))
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def relative_luma(hex_color) -> float:
    """Rough perceptual lightness (0..255) for a ``#rgb`` / ``#rrggbb`` string.

    Unparseable inputs default to 255.0 (treated as light, matching
    ``is_light_color``).
    """
    rgb = parse_hex(hex_color)
    if rgb is None:
        return 255.0
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def is_light_color(hex_color) -> bool:
    """Rough perceptual lightness test for a ``#rgb`` / ``#rrggbb`` string.

    Kept dependency-free (no Qt) so border/slider themes stay pure data and
    can be unit-tested without a QApplication.
    """
    return relative_luma(hex_color) >= 128.0


def blend_colors(c1: str, c2: str, t: float) -> str:
    """Linearly interpolate between *c1* and *c2* in sRGB (``t`` in 0..1)."""
    rgb1 = parse_hex(c1)
    rgb2 = parse_hex(c2)
    if rgb1 is None:
        return str(c2)
    if rgb2 is None:
        return str(c1)
    u = max(0.0, min(1.0, float(t)))
    return to_hex(
        rgb1[0] + (rgb2[0] - rgb1[0]) * u,
        rgb1[1] + (rgb2[1] - rgb1[1]) * u,
        rgb1[2] + (rgb2[2] - rgb1[2]) * u,
    )


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
    rgb = parse_hex(ink)
    r, g, b = rgb if rgb is not None else (34, 34, 34)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def derive_chrome_palette(
    bg: str,
    bar_bg: str,
    chrome_border: str,
) -> dict[str, str]:
    """Derive cohesive, contrast-safe UI tokens for arbitrary bg/bar colours.

    Every token adapts to the surface it actually sits on — body (``bg``) or
    title/tab strip (``bar_bg``) — and tints borders toward the active
    background rather than hardcoding neutral greys that vanish on black or
    glare on dark grey.
    """
    bg_luma = relative_luma(bg)
    bar_luma = relative_luma(bar_bg)
    bg_light = bg_luma >= 128.0
    bar_light = bar_luma >= 128.0

    text = DARK_INK if bg_light else LIGHT_INK
    bar_text = "#222222" if bar_light else "#efefef"
    bar_hover_bg = "rgba(0,0,0,0.09)" if bar_light else "rgba(255,255,255,0.14)"

    if bg_luma >= 200.0:
        channel_text = blend_colors(bg, "#000000", 0.68)
    elif bg_light:
        channel_text = blend_colors(bg, "#000000", 0.72)
    elif bg_luma >= 60.0:
        channel_text = blend_colors(bg, "#ffffff", 0.74)
    else:
        channel_text = blend_colors(bg, "#ffffff", 0.78)

    # Preserve exact #eaeaea input fill for default theme on light/gray surfaces
    # (pinned by test_background_opacity), while adapting to dark themes.
    input_bg = "#eaeaea" if bg_light else "#2e2e2e"

    if bg_luma >= 220.0:
        input_border = blend_colors(bg, "#000000", 0.19)
    elif bg_light:
        input_border = blend_colors(bg, "#000000", 0.28)
    elif bg_luma >= 60.0:
        input_border = blend_colors(bg, "#ffffff", 0.16)
    else:
        input_border = blend_colors(bg, "#ffffff", 0.18)

    # Internal dividers, CSP group boxes, SAI separator lines, history border.
    if bg_luma >= 210.0:
        subtle_border = blend_colors(bg, "#000000", 0.21)
    elif bg_light:
        subtle_border = blend_colors(bg, "#000000", 0.26)
    elif bg_luma >= 60.0:
        subtle_border = blend_colors(bg, "#ffffff", 0.15)
    else:
        subtle_border = blend_colors(bg, "#ffffff", 0.16)

    # Groove outline: crisp edge so light/dark ends of gradients stay bounded.
    if bg_light:
        groove_border = blend_colors(bg, "#000000", 0.34)
        groove_inner_border = "#ffffff"
    else:
        groove_border = blend_colors(bg, "#ffffff", 0.22)
        groove_inner_border = blend_colors(bg, "#000000", 0.45)

    # Title-bar / tab-strip divider: separates bar_bg from bg without vanishing.
    if bar_light and bg_light:
        title_divider = blend_colors(bar_bg, "#000000", 0.22)
    elif (not bar_light) and (not bg_light):
        # Between two dark surfaces, pick a crisp hairline distinct from both.
        darker = bar_bg if bar_luma <= bg_luma else bg
        if relative_luma(darker) < 42.0:
            title_divider = blend_colors(bg, "#ffffff", 0.16)
        else:
            title_divider = blend_colors(darker, "#000000", 0.38)
    elif bar_light:
        title_divider = blend_colors(bar_bg, "#000000", 0.24)
    else:
        title_divider = blend_colors(bar_bg, "#000000", 0.35)

    # Mode buttons sit over `bg` and fill with `bar_bg`.
    mode_btn_fg = bar_text
    if bar_light:
        mode_btn_border = blend_colors(bar_bg, "#000000", 0.24)
    else:
        mode_btn_border = blend_colors(bar_bg, "#ffffff", 0.20)

    # Swatch inactive rim must stay crisp on white, mid-gray, and black.
    if bg_luma >= 220.0:
        swatch_inactive_border = "#b0b0b0"
    elif bg_light:
        swatch_inactive_border = blend_colors(bg, "#000000", 0.32)
    else:
        swatch_inactive_border = blend_colors(bg, "#ffffff", 0.28)

    # Tab borders & SAI active accent tint.
    if bg_light:
        tab_border = blend_colors(bg, "#000000", 0.24)
        tab_accent_bg = blend_colors(bg, "#b8d6ff", 0.52 if bg_luma < 220.0 else 0.72)
    else:
        tab_border = blend_colors(bg, "#ffffff", 0.18)
        tab_accent_bg = blend_colors(bg, "#4d8ee6", 0.38)
    tab_accent_border = "#5a94e2"

    # Wheel outlines: crisp rim around ring and inner SV square/triangle.
    if bg_light:
        wheel_ring_border = blend_colors(bg, "#000000", 0.36)
        wheel_slice_border = blend_colors(bg, "#000000", 0.42)
    else:
        wheel_ring_border = blend_colors(bg, "#ffffff", 0.20)
        wheel_slice_border = blend_colors(bg, "#ffffff", 0.24)

    # History outer outline: keep crisp on dark themes where chrome_border hugs bg.
    border_luma = relative_luma(chrome_border)
    if abs(border_luma - bg_luma) >= 24.0:
        history_border = chrome_border
    else:
        history_border = subtle_border

    # PS value box: exact #d1d1d1/#949494/#1e1e1e on mid-light gray (#b2b2b2/#bdbdbd),
    # adapting naturally to pure white, mid-dark (#4e4e4e), and black (#1e1e1e).
    if bg_luma >= 220.0:
        ps_box_bg = blend_colors(bg, "#000000", 0.07)
        ps_box_border = blend_colors(bg, "#000000", 0.34)
        ps_box_text = "#1e1e1e"
    elif bg_light:
        ps_box_bg = "#d1d1d1"
        ps_box_border = "#949494"
        ps_box_text = "#1e1e1e"
    elif bg_luma >= 60.0:
        ps_box_bg = blend_colors(bg, "#000000", 0.26)
        ps_box_border = blend_colors(bg, "#ffffff", 0.18)
        ps_box_text = "#ededed"
    else:
        ps_box_bg = blend_colors(bg, "#ffffff", 0.07)
        ps_box_border = blend_colors(bg, "#ffffff", 0.22)
        ps_box_text = "#ededed"

    return {
        "text": text,
        "bar_text": bar_text,
        "bar_hover_bg": bar_hover_bg,
        "channel_text": channel_text,
        "input_bg": input_bg,
        "input_border": input_border,
        "subtle_border": subtle_border,
        "groove_border": groove_border,
        "groove_inner_border": groove_inner_border,
        "title_divider": title_divider,
        "mode_btn_fg": mode_btn_fg,
        "mode_btn_border": mode_btn_border,
        "swatch_inactive_border": swatch_inactive_border,
        "tab_border": tab_border,
        "tab_accent_bg": tab_accent_bg,
        "tab_accent_border": tab_accent_border,
        "wheel_ring_border": wheel_ring_border,
        "wheel_slice_border": wheel_slice_border,
        "history_border": history_border,
        "ps_box_bg": ps_box_bg,
        "ps_box_border": ps_box_border,
        "ps_box_text": ps_box_text,
    }


def build_tab_bar_css(
    chrome,
    *,
    bar_selector: str,
    tab_selector: str,
    pad_bottom: int | None = None,
    pane_css: str = "",
) -> str:
    """Build theme-aware QTabBar/QTabWidget stylesheet from a PanelChrome."""
    scale = max(0.5, float(getattr(chrome, "scale", 1.0) or 1.0))
    pad_x = max(2, int(round(5 * scale)))
    pad_y = max(2, int(round(3 * scale)))
    pb = pad_y if pad_bottom is None else max(1, int(pad_bottom))
    font = max(7, int(round((getattr(chrome, "font_size", 0) or 11) * 0.85)))
    bar_bg = getattr(chrome, "bar_bg", "") or "transparent"
    bg = getattr(chrome, "background", "") or "transparent"
    text = getattr(chrome, "text", "") or "#222222"
    bar_text = getattr(chrome, "bar_text", "") or text
    divider = getattr(chrome, "divider_color", "") or bar_bg
    divider_w = max(1, int(getattr(chrome, "divider_width", 0) or 1))
    tab_style = getattr(chrome, "tab_style", "default") or "default"
    tab_border = getattr(chrome, "tab_border_color", "") or divider
    tab_accent_bg = getattr(chrome, "tab_accent_bg", "") or "rgba(90,148,226,0.22)"
    tab_accent_border = getattr(chrome, "tab_accent_border", "") or "#5a94e2"
    btn_r = max(2, int(round(3 * scale)))

    if tab_style == "sai":
        accent_text = (
            readable_ink(tab_accent_bg)
            if isinstance(tab_accent_bg, str) and tab_accent_bg.startswith("#")
            else text
        )
        tab_css = (
            f"{tab_selector} {{"
            f" background: {bg}; color: {text};"
            f" padding: {pad_y}px {pad_x}px {pb}px {pad_x}px;"
            f" font-size: {font}px;"
            f" border: 1px solid {tab_border};"
            f" border-radius: {btn_r}px;"
            " margin: 1px 2px 1px 0px;"
            " }"
            f"{tab_selector}:selected {{"
            f" background: {tab_accent_bg}; color: {accent_text};"
            f" border: 1px solid {tab_accent_border};"
            " }"
            f"{tab_selector}:hover:!selected {{ border: 1px solid {tab_accent_border}; }}"
        )
    elif tab_style == "csp":
        tab_css = (
            f"{tab_selector} {{"
            f" background: {bar_bg}; color: {bar_text};"
            f" padding: {pad_y}px {pad_x}px {pb}px {pad_x}px;"
            f" font-size: {font}px;"
            " border: 1px solid transparent;"
            f" border-bottom: 1px solid {tab_border};"
            " border-top-left-radius: 2px; border-top-right-radius: 2px;"
            " margin-right: 1px;"
            " }"
            f"{tab_selector}:selected {{"
            f" background: {bg}; color: {text};"
            f" border: 1px solid {tab_border};"
            f" border-bottom: 1px solid {bg};"
            " }"
            f"{tab_selector}:hover:!selected {{"
            f" color: {bar_text};"
            f" border-top-color: {tab_border};"
            f" border-left-color: {tab_border};"
            f" border-right-color: {tab_border};"
            " }"
        )
    elif tab_style == "ps":
        tab_css = (
            f"{tab_selector} {{"
            f" background: {bar_bg}; color: {bar_text};"
            f" padding: {pad_y}px {pad_x}px {pb}px {pad_x}px;"
            f" font-size: {font}px;"
            " border: 1px solid transparent; border-radius: 0px;"
            " margin-right: 1px;"
            " }"
            f"{tab_selector}:selected {{"
            f" background: {bg}; color: {text};"
            f" border-top: 1px solid {tab_border};"
            f" border-left: 1px solid {tab_border};"
            f" border-right: 1px solid {tab_border};"
            f" border-bottom: 1px solid {bg};"
            " border-radius: 0px;"
            " }"
            f"{tab_selector}:hover:!selected {{"
            f" color: {bar_text};"
            f" border-top-color: {tab_border};"
            " }"
        )
    else:
        tab_css = (
            f"{tab_selector} {{"
            f" background: {bar_bg}; color: {bar_text};"
            f" padding: {pad_y}px {pad_x}px {pb}px {pad_x}px;"
            f" font-size: {font}px; border: none;"
            " }"
            f"{tab_selector}:selected {{"
            f" background: {bg}; color: {text};"
            f" border-top: {divider_w}px solid {divider};"
            " }"
            f"{tab_selector}:hover:!selected {{ color: {bar_text}; }}"
        )

    return (
        f"{pane_css}"
        f"{bar_selector} {{ background: transparent; font-size: {font}px; }}"
        f"{tab_css}"
    )


def build_mode_button_css(
    *,
    bg: str,
    bar_bg: str,
    fg: str,
    hover_fg: str,
    border: str,
    accent_border: str = "#5a94e2",
    radius: int = 4,
    font_size: int = 13,
) -> str:
    """Build theme-aware stylesheet for floating picker/view toggle buttons."""
    return f"""
        QPushButton {{
            background-color: {bar_bg};
            border: 1px solid {border};
            border-radius: {max(0, int(radius))}px;
            color: {fg};
            font-size: {max(6, int(font_size))}px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {bg};
            color: {hover_fg};
            border-color: {accent_border};
        }}
    """
