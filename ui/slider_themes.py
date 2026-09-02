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

Every theme also names the border theme it belongs with (`pairs_with`, see
`ui/border_themes.py`): the window frame, group frames and value-box chrome
live there, and the default config key `borderStyle = "auto"` follows this
field, so picking 类 PS 滑块样式 also gets PS-like borders.

Adding a new theme = append a new entry to SLIDER_THEMES. No other file
needs to change for the data to be picked up.
"""

from typing import Dict, List, Tuple

# Type alias for clarity; keep it loose so callers don't need to import.
SliderTheme = dict[str, object]

SLIDER_THEMES: dict[str, SliderTheme] = {
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
        # handle_tri_style: "filled"  = 实心三角（填充 color，描边宽度可为 0）
        #                   "outline" = 空心三角（填充 color，描 border；color
        #                               可写 "transparent" 让槽色透出）
        #                   "caret"   = 只画 "^" 细线，不填充
        "handle_tri_style": "filled",
        # 底边加粗（SAI 的把手：底下压一条比三角略宽的粗黑条，
        # 黑条顶行再压一道浅灰线）。base_width = 0 表示不画，三条边一样粗。
        "handle_tri_base_width": 0,        # 底边厚度 px * scale
        "handle_tri_base_overhang": 0,     # 底边比三角每侧多出的宽度 px * scale
        "handle_tri_base_color": "#000000",
        "handle_tri_inner_line_color": "none",  # 底边顶行的浅灰线，"none" = 不画
        "handle_tri_size_w": 5,            # half-width (px * scale)
        "handle_tri_size_h": 6,            # height (px * scale)
        "handle_tri_offset_y": 2,         # gap between groove bottom and triangle apex (px * scale)
        "handle_tri_color": "#3a3a3a",
        "handle_tri_border": "#1a1a1a",
        "handle_tri_border_width": 1,      # px * scale（0 = 不描边）
        # Row: 字母 / 滑条 / 数值 三者之间的间距（px * scale）
        "row_spacing": 1,
        # Channel letter label (e.g. "R:")
        "channel_label_width_factor": 1.0,  # 16px * scale * factor
        "channel_label_weight": "bold",
        # Value readout label (right side)
        "value_label_width_factor": 1.0,    # 34px * factor (does NOT scale with uiScale, by design)
        "value_label_radius_factor": 1.0,    # 3px * scale * factor
        "value_label_padding": "1px 0px",
        "value_label_align": "left",        # left / right / center
        # 配套边框主题（borderStyle = "auto" 时按这个匹配）
        "pairs_with": "default",
    },
    # ── 类 CLIP STUDIO PAINT ─────────────────────────────────
    # 更窄的渐变槽、细圆角、滑条下方细 "^" 线指示（截图里不是实心三角）。
    "csp": {
        "display_name": "类 CSP",
        "handle_shape": "triangle-below",
        "groove_h_factor": 0.75,           # 截图实测：槽高约 12px
        "groove_radius_factor": 0.0,       # 截图实测：直角
        "handle_w_factor": 1.2,
        "handle_h_factor": 1.0,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 0.5,
        "handle_bg": "#3a3a3a",
        "handle_border": "#1a1a1a",
        "handle_hover_bg": "#3a3a3a",
        "handle_hover_border": "#5a94e2",
        "handle_tri_style": "caret",       # 细线 "^"，不填充
        "handle_tri_base_width": 0,
        "handle_tri_base_overhang": 0,
        "handle_tri_base_color": "#000000",
        "handle_tri_inner_line_color": "none",
        "handle_tri_size_w": 7,            # 截图实测：指示总宽约 14px
        "handle_tri_size_h": 7,            # 截图实测：指示高约 7px
        "handle_tri_offset_y": 2,          # 截图实测：与槽的间距约 2px
        "handle_tri_color": "#717171",     # 截图实测线条灰
        "handle_tri_border": "#717171",
        "handle_tri_border_width": 1,
        "row_spacing": 2,
        "channel_label_width_factor": 1.0,
        "channel_label_weight": "bold",
        "value_label_width_factor": 1.0,
        "value_label_radius_factor": 0.67,
        "value_label_padding": "0px 0px",
        "value_label_align": "right",      # 截图里数值靠右（右边还有微调箭头）
        "pairs_with": "csp",
    },
    # ── 类 SAI ───────────────────────────────────────────────
    # 更厚实的渐变槽、槽下方空心黑边三角、常规字重字母、数值纯文本无框。
    "sai": {
        "display_name": "类 SAI",
        "handle_shape": "triangle-below",
        "groove_h_factor": 0.8125,         # 截图实测：槽高约 13px
        "groove_radius_factor": 0.33,      # 截图实测：约 0 / 极小圆角
        "handle_w_factor": 0.8,
        "handle_h_factor": 1.25,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 1.5,
        "handle_bg": "#ffffff",
        "handle_border": "#7a7a7a",
        "handle_hover_bg": "#f0f0f0",
        "handle_hover_border": "#5a94e2",
        # 放大截图数出来的形状：8 行高、总宽 13px、内部中空，
        # 底下是 2px 实心黑条（比三角每侧宽 1px），黑条顶行压一道浅灰线。
        "handle_tri_style": "outline",     # 空心三角 + 1px 黑描边
        "handle_tri_base_width": 2,        # 底边那条粗黑条
        "handle_tri_base_overhang": 1,     # 黑条每侧比三角多出 1px（5.5+1 → 总宽 13）
        "handle_tri_base_color": "#000000",
        "handle_tri_inner_line_color": "#b0b0b0",
        "handle_tri_size_w": 5.5,          # 三角自身半宽（加 overhang 后总宽 13px）
        "handle_tri_size_h": 8,            # 截图实测：指示高约 8px（含底边黑条）
        "handle_tri_offset_y": 4,          # 截图实测：与槽的间距约 4px
        "handle_tri_color": "#ffffff",
        "handle_tri_border": "#000000",
        "handle_tri_border_width": 1,
        "row_spacing": 3,
        "channel_label_width_factor": 1.125,
        "channel_label_weight": "normal",
        "value_label_width_factor": 1.1,
        "value_label_radius_factor": 1.0,
        "value_label_padding": "1px 2px",
        "value_label_align": "right",      # 截图里数值是右对齐纯文本
        "pairs_with": "sai",
    },
    # ── 类 PHOTOSHOP ─────────────────────────────────────────
    # 紧凑、直角、滑条下方白色实心三角（截图里没有深色描边）。
    "ps": {
        "display_name": "类 PS",
        "handle_shape": "triangle-below",
        "groove_h_factor": 0.5625,         # 截图实测：槽高约 9px
        "groove_radius_factor": 0.0,       # 截图实测：直角
        "handle_w_factor": 1.4,
        "handle_h_factor": 1.0,
        "handle_margin_y_factor": 1.0,
        "handle_radius_factor": 0.0,
        "handle_bg": "#1e1e1e",
        "handle_border": "#000000",
        "handle_hover_bg": "#1e1e1e",
        "handle_hover_border": "#5a94e2",
        "handle_tri_style": "filled",      # 白色实心，无描边
        "handle_tri_base_width": 0,
        "handle_tri_base_overhang": 0,
        "handle_tri_base_color": "#000000",
        "handle_tri_inner_line_color": "none",
        "handle_tri_size_w": 7,            # 截图实测：三角总宽约 15px
        "handle_tri_size_h": 9,
        "handle_tri_offset_y": 0,          # 截图里三角顶点直接贴着槽底，没有留缝
        "handle_tri_color": "#ffffff",
        "handle_tri_border": "#ffffff",
        "handle_tri_border_width": 0,
        "row_spacing": 4,                  # 截图里字母与槽之间留了明显空隙
        "channel_label_width_factor": 1.0,
        "channel_label_weight": "bold",
        "value_label_width_factor": 1.25,  # 截图里数值框比另外两家宽
        "value_label_radius_factor": 0.0,
        "value_label_padding": "0px 3px",
        "value_label_align": "left",
        "pairs_with": "ps",
    },
}

DEFAULT_SLIDER_THEME = "default"


def get_slider_theme(name) -> SliderTheme:
    """Resolve a slider theme key to its dict. Falls back to default."""
    return SLIDER_THEMES.get(name, SLIDER_THEMES[DEFAULT_SLIDER_THEME])


def list_slider_theme_names() -> list[tuple[str, str]]:
    """Return (key, display_name) pairs in insertion order — for settings UI."""
    from core import i18n
    return [(key, i18n.tr(str(theme["display_name"]))) for key, theme in SLIDER_THEMES.items()]