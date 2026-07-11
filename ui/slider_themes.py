"""Sliding visual themes for the horizontal color sliders.

A theme is a pure-data dict controlling the slider groove / handle dimensions,
the channel-letter label (R/G/B/...), and the value readout label. Look up a
theme by its key with `get_slider_theme(name)`; unknown names silently fall
back to the "default" theme so a missing or renamed config key never breaks
the UI.

Theme factors are multiplied against the same base pixel values the app
already uses, so the existing DPI/scale pipeline in `GradientSlider.update_scale`
and `MainWindow.apply_theme` keeps driving the absolute sizes — themes only
shift the proportions.

Adding a new theme = append a new entry to SLIDER_THEMES. No other file
needs to change for the data to be picked up.
"""

from typing import Dict, List, Tuple

# Type alias for clarity; keep it loose so callers don't need to import.
SliderTheme = Dict[str, object]

SLIDER_THEMES: Dict[str, SliderTheme] = {
    # ── 默认：与软件原本外观一致 ──────────────────────────────
    "default": {
        "display_name": "默认",
        # GradientSlider groove (the colored bar)
        "groove_h_factor": 1.0,            # 16px * scale * factor
        "groove_radius_factor": 1.0,       # 3px * scale * factor (rounded corner of gradient bar)
        # Handle shape: "rect" uses the native QSlider handle styled via the
        # sheet below; "triangle-below" hides the native handle and draws a
        # small triangle (apex up) just under the groove in paintEvent.
        "handle_shape": "rect",
        # Handle (thumb) — only used when handle_shape == "rect"
        "handle_w_factor": 1.6,            # 5px → 8px at 1×
        "handle_h_factor": 0.75,           # 24px → 18px at 1×
        "handle_margin_y_factor": 0.5,      # -4px → -2px at 1×
        "handle_radius_factor": 1.0,        # 1px * scale * factor
        "handle_bg": "transparent",
        "handle_border": "#b0b0b0",
        "handle_hover_bg": "transparent",
        "handle_hover_border": "#5a94e2",
        # Triangle — only used when handle_shape == "triangle-below"
        "handle_tri_size_w": 5,            # half-width (px * scale)
        "handle_tri_size_h": 6,            # height (px * scale)
        "handle_tri_offset_y": 2,         # gap between groove bottom and triangle apex (px * scale)
        "handle_tri_color": "#3a3a3a",
        "handle_tri_border": "#1a1a1a",
        # Channel letter label (e.g. "R:")
        "channel_label_width_factor": 1.0,  # 16px * scale * factor
        "channel_label_weight": "bold",
        # Value readout label (right side)
        "value_label_width_factor": 1.0,    # 34px * factor (does NOT scale with uiScale, by design)
        "value_label_radius_factor": 1.0,    # 3px * scale * factor
        "value_label_padding": "1px 0px",
    },
    # ── 类 CLIP STUDIO PAINT ─────────────────────────────────
    # 更窄的渐变槽、细圆角、滑条下小三角指示。
    "csp": {
        "display_name": "类 CSP",
        "handle_shape": "triangle-below",
        "groove_h_factor": 0.875,
        "groove_radius_factor": 0.5,
        "handle_w_factor": 1.2,
        "handle_h_factor": 1.0,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 0.5,
        "handle_bg": "#3a3a3a",
        "handle_border": "#1a1a1a",
        "handle_hover_bg": "#3a3a3a",
        "handle_hover_border": "#5a94e2",
        "handle_tri_size_w": 7,
        "handle_tri_size_h": 8,
        "handle_tri_offset_y": 3,
        "handle_tri_color": "#3a3a3a",
        "handle_tri_border": "#1a1a1a",
        "channel_label_width_factor": 1.0,
        "channel_label_weight": "bold",
        "value_label_width_factor": 1.0,
        "value_label_radius_factor": 0.67,
        "value_label_padding": "0px 0px",
    },
    # ── 类 SAI ───────────────────────────────────────────────
    # 更厚实的渐变槽、高一些的圆手柄、常规字重字母。
    "sai": {
        "display_name": "类 SAI",
        "handle_shape": "rect",
        "groove_h_factor": 1.25,
        "groove_radius_factor": 1.0,
        "handle_w_factor": 0.8,
        "handle_h_factor": 1.25,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 1.5,
        "handle_bg": "#ffffff",
        "handle_border": "#7a7a7a",
        "handle_hover_bg": "#f0f0f0",
        "handle_hover_border": "#5a94e2",
        "handle_tri_size_w": 5,
        "handle_tri_size_h": 6,
        "handle_tri_offset_y": 2,
        "handle_tri_color": "#3a3a3a",
        "handle_tri_border": "#1a1a1a",
        "channel_label_width_factor": 1.125,
        "channel_label_weight": "normal",
        "value_label_width_factor": 1.1,
        "value_label_radius_factor": 1.0,
        "value_label_padding": "1px 2px",
    },
    # ── 类 PHOTOSHOP ─────────────────────────────────────────
    # 紧凑、直角、滑条下方小三角指示（深色描边）。
    "ps": {
        "display_name": "类 PS",
        "handle_shape": "triangle-below",
        "groove_h_factor": 0.75,
        "groove_radius_factor": 0.0,
        "handle_w_factor": 1.4,
        "handle_h_factor": 1.0,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 0.0,
        "handle_bg": "#1e1e1e",
        "handle_border": "#000000",
        "handle_hover_bg": "#1e1e1e",
        "handle_hover_border": "#5a94e2",
        "handle_tri_size_w": 7,
        "handle_tri_size_h": 8,
        "handle_tri_offset_y": 3,
        "handle_tri_color": "#1e1e1e",
        "handle_tri_border": "#000000",
        "channel_label_width_factor": 1.0,
        "channel_label_weight": "bold",
        "value_label_width_factor": 1.05,
        "value_label_radius_factor": 0.0,
        "value_label_padding": "0px 0px",
    },
}

DEFAULT_SLIDER_THEME = "default"


def get_slider_theme(name) -> SliderTheme:
    """Resolve a slider theme key to its dict. Falls back to default."""
    return SLIDER_THEMES.get(name, SLIDER_THEMES[DEFAULT_SLIDER_THEME])


def list_slider_theme_names() -> List[Tuple[str, str]]:
    """Return (key, display_name) pairs in insertion order — for settings UI."""
    return [(key, theme["display_name"]) for key, theme in SLIDER_THEMES.items()]