"""Border themes for the window chrome, slider group frames and value boxes.

A border theme is a pure-data dict describing every *border-ish* surface the
app draws: the window frame, the title bar band (height / bottom divider /
whether the frame wraps above it), the per-group divider / box, the value
readout box and the gradient-groove outline.

The title bar matters here because it *is* the window's top edge: while it is
visible the top frame line is suppressed and the bar's own band takes over, so
its height and divider belong to the same preset as the side borders.

Division of labour with `ui/slider_themes.py`:

* slider theme  → proportions (groove height / radius, handle shape & size,
  label widths, value-box radius & padding)
* border theme  → whether a border exists at all, plus its colour and width
  (window frame, group frame, value box, groove outline)

The two are matched by the `pairs_with` field on both sides, and the special
border key `"auto"` means "use whatever border theme the active slider theme
pairs with", so the default configuration always stays visually consistent.

Colour fields may hold the sentinel `"auto"`, meaning "inherit the colour the
active UI theme (auto / eyedropper / gray / white / black) already computed".
`resolve_border_theme()` turns a theme plus those UI colours into a concrete
dict with no sentinels left, so widgets never have to know about "auto".

Adding a new border theme = append an entry to BORDER_THEMES. No other file
needs to change for the data to be picked up.
"""

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
        # 渐变槽描边
        "groove_border_width": 0,          # 0 = 不描边
        "groove_border_color": AUTO,
    },
    # ── 类 PHOTOSHOP ─────────────────────────────────────────
    # 普通灰色窗口面板、直角；数值框是浅灰实心框（#d1d1d1 / #949494 1px 直角）。
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
        "groove_border_width": 0,
        "groove_border_color": AUTO,
    },
    # ── 类 SAI ───────────────────────────────────────────────
    # 浅底 + 行/组之间的灰色分隔线；数值是纯文本，没有框。
    "sai": {
        "display_name": "类 SAI",
        "pairs_with": "sai",
        "window_border_width": 3,
        "window_border_radius": 0,
        "window_border_color": AUTO,
        # SAI 的「色」标题条与下方浅色面板之间有一条明确的分界
        "title_bar_height": 36,
        "title_bar_button_size": 18,
        "title_bar_divider_width": 1,
        "title_bar_divider_color": AUTO,
        "title_bar_inset": False,
        "group_frame": "line",
        "group_frame_color": "#c9c9c9",
        "group_frame_width": 1,
        "group_frame_radius": 0,
        "group_frame_padding": 3,
        "value_box": False,
        "value_box_bg": "transparent",
        "value_box_border": "transparent",
        "value_box_border_width": 0,
        "value_box_text": AUTO,
        "groove_border_width": 0,
        "groove_border_color": AUTO,
    },
    # ── 类 CLIP STUDIO PAINT ─────────────────────────────────
    # 灰色面板 + 每个色彩空间一圈分组边框；数值是纯文本，没有框。
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
        "groove_border_width": 0,
        "groove_border_color": AUTO,
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


def is_light_color(hex_color) -> bool:
    """Rough perceptual lightness test for a `#rgb` / `#rrggbb` string.

    Kept dependency-free (no Qt) so border themes stay pure data and can be
    unit-tested without a QApplication.
    """
    text = str(hex_color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return True
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
    except ValueError:
        return True
    return (0.299 * r + 0.587 * g + 0.114 * b) >= 128


def resolve_border_theme(
    theme,
    *,
    chrome_border,
    input_bg,
    input_border,
    text,
) -> dict:
    """Return a concrete copy of `theme` with every "auto" colour filled in.

    Parameters mirror what `MainWindow.apply_theme` already computes for the
    active UI theme:

    * `chrome_border` — the window frame / panel border colour
    * `input_bg`      — the default value-box fill
    * `input_border`  — the default value-box outline
    * `text`          — the default body text colour
    """
    def _color(value, fallback):
        return fallback if str(value) == AUTO else str(value)

    def _int(key, fallback=0):
        try:
            return max(0, int(theme.get(key, fallback)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback

    value_box = bool(theme.get("value_box", True))
    value_bg = _color(theme.get("value_box_bg", AUTO), input_bg)
    value_border = _color(theme.get("value_box_border", AUTO), input_border)

    raw_value_text = theme.get("value_box_text", AUTO)
    if str(raw_value_text) != AUTO:
        value_text = str(raw_value_text)
    elif value_box and not _is_transparent(value_bg) and str(theme.get("value_box_bg", AUTO)) != AUTO:
        # Theme pinned its own box colour (e.g. PS's light gray field): pick a
        # readable text colour for that fill instead of the UI theme's.
        value_text = "#1e1e1e" if is_light_color(value_bg) else "#ffffff"
    else:
        value_text = str(text)

    group_frame = str(theme.get("group_frame", "none"))
    if group_frame not in ("none", "line", "box"):
        group_frame = "none"

    return {
        "window_border_width": _int("window_border_width", 4),
        "window_border_radius": _int("window_border_radius", 0),
        "window_border_color": _color(theme.get("window_border_color", AUTO), chrome_border),
        "title_bar_height": _int("title_bar_height", 28) or 28,
        "title_bar_button_size": _int("title_bar_button_size", 18) or 18,
        "title_bar_divider_width": _int("title_bar_divider_width", 0),
        "title_bar_divider_color": _color(
            theme.get("title_bar_divider_color", AUTO), chrome_border
        ),
        "title_bar_inset": bool(theme.get("title_bar_inset", False)),
        "group_frame": group_frame,
        "group_frame_color": _color(theme.get("group_frame_color", AUTO), chrome_border),
        "group_frame_width": _int("group_frame_width", 1),
        "group_frame_radius": _int("group_frame_radius", 0),
        "group_frame_padding": _int("group_frame_padding", 0),
        "value_box": value_box,
        "value_box_bg": value_bg if value_box else "transparent",
        "value_box_border": value_border if value_box else "transparent",
        "value_box_border_width": _int("value_box_border_width", 1) if value_box else 0,
        "value_box_text": value_text,
        "groove_border_width": _int("groove_border_width", 0),
        "groove_border_color": _color(theme.get("groove_border_color", AUTO), chrome_border),
    }
