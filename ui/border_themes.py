"""Border themes for the window chrome, slider group frames and value boxes.

A border theme is a pure-data dict describing every *border-ish* surface the
app draws: the window frame, the title bar band (height / bottom divider /
whether the frame wraps above it), the tab strip style, the per-group divider /
box, the value readout box, the gradient-groove outline (including SAI's inner
bevel), the wheel/slice outline style, and the swatch shape.

Division of labour with `ui/slider_themes.py`:

* slider theme  → proportions (groove height / radius, handle shape & size,
  label widths, value-box radius & padding) and adaptive handle ink
* border theme  → whether a border exists at all, plus its colour, width and
  panel chrome style (window frame, tabs, group frame, value box, groove
  outline, wheel outline, swatch shape)

The two are matched by the `pairs_with` field on both sides, and the special
border key `"auto"` means "use whatever border theme the active slider theme
pairs with", so the default configuration always stays visually consistent.

Colour fields may hold the sentinel `"auto"`, meaning "inherit / adapt from the
active UI theme (auto / eyedropper / gray / white / black)".
`resolve_border_theme()` turns a theme plus those UI colours into a concrete
dict with no sentinels left, so widgets never have to know about "auto".

Adding a new border theme = append an entry to BORDER_THEMES. No other file
needs to change for the data to be picked up.
"""

from ui.theme_contrast import derive_chrome_palette, is_light_color  # noqa: F401

BorderTheme = dict[str, object]

#: Config value meaning "follow the slider theme's `pairs_with` border theme".
BORDER_THEME_AUTO = "auto"
DEFAULT_BORDER_THEME = "default"

#: Sentinel used by colour fields that inherit from the active UI theme.
AUTO = "auto"

BORDER_THEMES: dict[str, BorderTheme] = {
    # ── 默认：与软件原本外观一致（4px 窗口边框、有框数值框、无分组框）──
    "default": {
        "display_name": "默认",
        "pairs_with": "default",          # 配套滑条主题
        # 窗口外框（CentralWidget）
        "window_border_width": 4,          # px，与旧硬编码值一致
        "window_border_radius": 0,
        "window_border_color": AUTO,       # AUTO = 跟随界面主题的边框色
        # 标题栏（同属窗口外框：标题栏显示时顶边线由它接管）
        #
        # 高度与按钮直接写目标像素（和 window_border_width 一样的单位），
        # 实际值 = px / devicePixelRatio，高 DPI 上物理尺寸保持一致。
        # 按钮不跟着栏高等比放大：PS / SAI 那种高顶栏里控件依然是小的。
        "title_bar_height": 28,            # px（不随 uiScale 缩放，与旧行为一致）
        "title_bar_button_size": 18,       # px：设置 / 最小化 / 关闭三个按钮
        "title_bar_divider_width": 0,      # 标题栏底部分隔线，0 = 无
        "title_bar_divider_color": AUTO,
        "title_bar_inset": False,          # True = 边框绕到标题栏上方（标题栏内嵌）
        # 滑块分组框：none = 无 / line = 组底分隔线 / box = 整组描边
        "group_frame": "none",
        "group_frame_color": AUTO,
        "group_frame_width": 1,
        "group_frame_radius": 0,
        "group_frame_padding": 0,
        # 数值框（右侧读数）
        "value_box": True,                 # False = 纯文本，无底色无描边
        "value_box_bg": AUTO,
        "value_box_border": AUTO,
        "value_box_border_width": 1,
        "value_box_text": AUTO,
        # 渐变槽描边（外框 + 可选内嵌高光/暗线）
        "groove_border_width": 0,          # 0 = 不描边
        "groove_border_color": AUTO,
        "groove_inner_border_width": 0,
        "groove_inner_border_color": AUTO,
        # 页签 / 色轮 / 色块 / 按钮细节风格
        "tab_style": "default",
        "wheel_style": "default",
        "swatch_shape": "circle",
        "button_radius": 4,
    },
    # ── 类 PHOTOSHOP ─────────────────────────────────────────
    # 直角面板、标题与页签共享暗底栏 + 选中页签融入面板体、数值框、方形色块。
    "ps": {
        "display_name": "类 PS",
        "pairs_with": "ps",
        "window_border_width": 4,
        "window_border_radius": 0,
        "window_border_color": AUTO,
        # PS 顶栏下面直接接标签行，没有分隔线；顶栏整体更高
        "title_bar_height": 42,
        "title_bar_button_size": 18,
        "title_bar_divider_width": 0,
        "title_bar_divider_color": AUTO,
        "title_bar_inset": False,
        "group_frame": "none",
        "group_frame_color": AUTO,
        "group_frame_width": 1,
        "group_frame_radius": 0,
        "group_frame_padding": 0,
        "value_box": True,
        "value_box_bg": "#d1d1d1",
        "value_box_border": "#949494",
        "value_box_border_width": 1,
        "value_box_text": AUTO,            # 按底色明暗自动取深/浅字色
        "groove_border_width": 1,          # 1px 细腻槽边，防止浅/深端融进面板
        "groove_border_color": AUTO,
        "groove_inner_border_width": 0,
        "groove_inner_border_color": AUTO,
        "tab_style": "ps",
        "wheel_style": "ps",
        "swatch_shape": "square",          # PS 经典双层描边重叠方色块
        "button_radius": 1,
    },
    # ── 类 SAI ───────────────────────────────────────────────
    # 清晰分隔线 + 经典双层描边凹槽（外灰框 + 内高光/暗线）+ 按钮式页签 + 纯文本数值。
    "sai": {
        "display_name": "类 SAI",
        "pairs_with": "sai",
        "window_border_width": 3,
        "window_border_radius": 0,
        "window_border_color": AUTO,
        # SAI 的「色」标题条与下方面板之间有一条明确的分界
        "title_bar_height": 36,
        "title_bar_button_size": 18,
        "title_bar_divider_width": 1,
        "title_bar_divider_color": AUTO,
        "title_bar_inset": False,
        "group_frame": "line",
        "group_frame_color": AUTO,         # 随背景明暗自适应分隔线
        "group_frame_width": 1,
        "group_frame_radius": 0,
        "group_frame_padding": 3,
        "value_box": False,
        "value_box_bg": "transparent",
        "value_box_border": "transparent",
        "value_box_border_width": 0,
        "value_box_text": AUTO,
        "groove_border_width": 1,          # SAI 经典外框 + 1px 内层亮/暗描边
        "groove_border_color": AUTO,
        "groove_inner_border_width": 1,
        "groove_inner_border_color": AUTO,
        "tab_style": "sai",
        "wheel_style": "sai",
        "swatch_shape": "circle",
        "button_radius": 3,
    },
    # ── 类 CLIP STUDIO PAINT ─────────────────────────────────
    # 内嵌标题栏 + 每个色彩空间一圈细腻分组边框 + 径向色环指示条 + 纯文本数值。
    "csp": {
        "display_name": "类 CSP",
        "pairs_with": "csp",
        "window_border_width": 4,
        "window_border_radius": 0,
        "window_border_color": AUTO,
        # CSP 的标题栏是面板的一部分：边框绕到它上方，底部再压一条分隔线
        "title_bar_height": 28,
        "title_bar_button_size": 18,
        "title_bar_divider_width": 1,
        "title_bar_divider_color": AUTO,
        "title_bar_inset": True,
        "group_frame": "box",
        "group_frame_color": AUTO,
        "group_frame_width": 1,
        "group_frame_radius": 2,
        "group_frame_padding": 3,
        "value_box": False,
        "value_box_bg": "transparent",
        "value_box_border": "transparent",
        "value_box_border_width": 0,
        "value_box_text": AUTO,
        "groove_border_width": 1,          # CSP 截图里的 1px 细腻槽边框
        "groove_border_color": AUTO,
        "groove_inner_border_width": 0,
        "groove_inner_border_color": AUTO,
        "tab_style": "csp",
        "wheel_style": "csp",
        "swatch_shape": "circle",
        "button_radius": 2,
    },
}


def get_border_theme(name) -> BorderTheme:
    """Resolve a border theme key to its dict. Falls back to default.

    `"auto"` is *not* resolved here (it is not a theme, it is a policy) —
    call :func:`resolve_border_theme_key` first when reading config.
    """
    return BORDER_THEMES.get(name, BORDER_THEMES[DEFAULT_BORDER_THEME])


def resolve_border_theme_key(border_style, slider_style=None) -> str:
    """Map the stored `borderStyle` config value to a real border theme key.

    `"auto"` (the default) follows the active slider theme's `pairs_with`
    field, so picking 类 PS 滑块样式 also gets the PS window/value-box chrome
    without the user having to set two dropdowns.
    """
    if border_style in BORDER_THEMES:
        return str(border_style)
    if border_style not in (None, "", BORDER_THEME_AUTO):
        return DEFAULT_BORDER_THEME
    # auto → whatever the slider theme declares as its partner
    from ui.slider_themes import get_slider_theme

    paired = str(get_slider_theme(slider_style).get("pairs_with", DEFAULT_BORDER_THEME))
    return paired if paired in BORDER_THEMES else DEFAULT_BORDER_THEME


def list_border_theme_names() -> list[tuple[str, str]]:
    """Return (key, display_name) pairs for the settings UI.

    The first entry is the "follow the slider style" policy, then every
    concrete theme in insertion order.
    """
    from core import i18n

    names = [(BORDER_THEME_AUTO, i18n.tr("跟随滑块样式"))]
    names += [(key, i18n.tr(str(theme["display_name"]))) for key, theme in BORDER_THEMES.items()]
    return names


def _is_transparent(value) -> bool:
    return str(value).strip().lower() in ("transparent", "none", "")


def resolve_border_theme(
    theme,
    *,
    chrome_border,
    input_bg,
    input_border,
    text,
    bg=None,
    bar_bg=None,
) -> dict:
    """Return a concrete copy of `theme` with every "auto" colour filled in.

    Parameters mirror what `MainWindow.apply_theme` computes for the active UI
    theme. When `bg` is supplied, surface-adaptive colours (PS dark/white value
    boxes, CSP/SAI group borders, groove outlines, tab dividers) adapt smoothly
    to any background colour; when omitted (legacy callers/unit tests), the
    explicit inputs drive resolution directly.
    """
    palette = (
        derive_chrome_palette(str(bg), str(bar_bg or chrome_border), str(chrome_border))
        if bg is not None
        else None
    )

    def _color(value, fallback):
        return fallback if str(value) == AUTO else str(value)

    def _int(key, fallback=0):
        try:
            return max(0, int(theme.get(key, fallback)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback

    value_box = bool(theme.get("value_box", True))
    raw_box_bg = theme.get("value_box_bg", AUTO)
    raw_box_border = theme.get("value_box_border", AUTO)
    raw_value_text = theme.get("value_box_text", AUTO)

    if (
        palette is not None
        and value_box
        and str(theme.get("tab_style", "")) == "ps"
        and str(raw_box_bg) == "#d1d1d1"
    ):
        value_bg = palette["ps_box_bg"]
        value_border = palette["ps_box_border"]
        value_text = (
            str(raw_value_text)
            if str(raw_value_text) != AUTO
            else palette["ps_box_text"]
        )
    else:
        value_bg = _color(raw_box_bg, input_bg)
        value_border = _color(raw_box_border, input_border)
        if str(raw_value_text) != AUTO:
            value_text = str(raw_value_text)
        elif value_box and not _is_transparent(value_bg) and str(raw_box_bg) != AUTO:
            # Theme pinned its own box colour (e.g. PS's light gray field): pick a
            # readable text colour for that fill instead of the UI theme's.
            value_text = "#1e1e1e" if is_light_color(value_bg) else "#ffffff"
        else:
            value_text = str(text)

    group_frame = str(theme.get("group_frame", "none"))
    if group_frame not in ("none", "line", "box"):
        group_frame = "none"

    divider_fallback = palette["title_divider"] if palette is not None else chrome_border
    group_fallback = palette["subtle_border"] if palette is not None else chrome_border
    groove_fallback = palette["groove_border"] if palette is not None else chrome_border
    groove_inner_fallback = (
        palette["groove_inner_border"] if palette is not None else "#ffffff"
    )

    tab_style = str(theme.get("tab_style", "default"))
    if tab_style not in ("default", "ps", "sai", "csp"):
        tab_style = "default"
    wheel_style = str(theme.get("wheel_style", "default"))
    if wheel_style not in ("default", "ps", "sai", "csp"):
        wheel_style = "default"
    swatch_shape = str(theme.get("swatch_shape", "circle"))
    if swatch_shape not in ("circle", "square"):
        swatch_shape = "circle"

    return {
        "window_border_width": _int("window_border_width", 4),
        "window_border_radius": _int("window_border_radius", 0),
        "window_border_color": _color(theme.get("window_border_color", AUTO), chrome_border),
        "title_bar_height": _int("title_bar_height", 28) or 28,
        "title_bar_button_size": _int("title_bar_button_size", 18) or 18,
        "title_bar_divider_width": _int("title_bar_divider_width", 0),
        "title_bar_divider_color": _color(
            theme.get("title_bar_divider_color", AUTO), divider_fallback
        ),
        "title_bar_inset": bool(theme.get("title_bar_inset", False)),
        "group_frame": group_frame,
        "group_frame_color": _color(theme.get("group_frame_color", AUTO), group_fallback),
        "group_frame_width": _int("group_frame_width", 1),
        "group_frame_radius": _int("group_frame_radius", 0),
        "group_frame_padding": _int("group_frame_padding", 0),
        "value_box": value_box,
        "value_box_bg": value_bg if value_box else "transparent",
        "value_box_border": value_border if value_box else "transparent",
        "value_box_border_width": _int("value_box_border_width", 1) if value_box else 0,
        "value_box_text": value_text,
        "groove_border_width": _int("groove_border_width", 0),
        "groove_border_color": _color(theme.get("groove_border_color", AUTO), groove_fallback),
        "groove_inner_border_width": _int("groove_inner_border_width", 0),
        "groove_inner_border_color": _color(
            theme.get("groove_inner_border_color", AUTO), groove_inner_fallback
        ),
        "tab_style": tab_style,
        "wheel_style": wheel_style,
        "swatch_shape": swatch_shape,
        "button_radius": _int("button_radius", 4),
        "tab_border_color": palette["tab_border"] if palette is not None else str(chrome_border),
        "tab_accent_bg": (
            palette["tab_accent_bg"] if palette is not None else "rgba(90,148,226,0.18)"
        ),
        "tab_accent_border": (
            palette["tab_accent_border"] if palette is not None else "#5a94e2"
        ),
        "wheel_ring_border_color": (
            palette["wheel_ring_border"] if palette is not None else str(chrome_border)
        ),
        "wheel_slice_border_color": (
            palette["wheel_slice_border"] if palette is not None else str(input_border)
        ),
        "history_border_color": (
            palette["history_border"] if palette is not None else str(chrome_border)
        ),
    }
