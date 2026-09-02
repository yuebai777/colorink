"""Border themes: data shape, slider pairing and "auto" colour resolution.

Border themes own every border-ish surface (window frame, per-group frame,
value box, groove outline); slider themes own the proportions. The two are
matched through `pairs_with`, which is what `borderStyle = "auto"` follows.
"""

import os

import pytest

from ui.border_themes import (
    BORDER_THEME_AUTO,
    BORDER_THEMES,
    DEFAULT_BORDER_THEME,
    get_border_theme,
    is_light_color,
    list_border_theme_names,
    resolve_border_theme,
    resolve_border_theme_key,
)
from ui.slider_themes import SLIDER_THEMES

# Colours the dark-ish "gray" UI theme hands to resolve_border_theme().
UI_COLORS = dict(
    chrome_border="#787878",
    input_bg="#2e2e2e",
    input_border="#555555",
    text="#ffffff",
)


# ── Data shape ────────────────────────────────────────────────────────────


def test_every_border_theme_declares_the_same_keys():
    reference = set(BORDER_THEMES[DEFAULT_BORDER_THEME])
    for key, theme in BORDER_THEMES.items():
        assert set(theme) == reference, f"{key} border theme key drift"


def test_every_slider_theme_declares_the_same_keys():
    reference = set(SLIDER_THEMES["default"])
    for key, theme in SLIDER_THEMES.items():
        assert set(theme) == reference, f"{key} slider theme key drift"


def test_unknown_border_key_falls_back_to_default():
    assert get_border_theme("nope") is BORDER_THEMES[DEFAULT_BORDER_THEME]
    assert get_border_theme(None) is BORDER_THEMES[DEFAULT_BORDER_THEME]


# ── Slider ↔ border pairing ───────────────────────────────────────────────


def test_auto_follows_the_slider_theme_pairing():
    for slider_key, slider_theme in SLIDER_THEMES.items():
        resolved = resolve_border_theme_key(BORDER_THEME_AUTO, slider_key)
        assert resolved == slider_theme["pairs_with"]
        assert resolved in BORDER_THEMES


def test_pairings_are_symmetric():
    for slider_key, slider_theme in SLIDER_THEMES.items():
        border_key = str(slider_theme["pairs_with"])
        assert border_key in BORDER_THEMES
        assert BORDER_THEMES[border_key]["pairs_with"] == slider_key


def test_explicit_border_key_wins_over_the_pairing():
    assert resolve_border_theme_key("ps", "sai") == "ps"


def test_unknown_border_style_falls_back_to_default():
    assert resolve_border_theme_key("bogus", "ps") == DEFAULT_BORDER_THEME


def test_auto_with_unknown_slider_style_is_default():
    assert resolve_border_theme_key(BORDER_THEME_AUTO, "bogus") == DEFAULT_BORDER_THEME
    assert resolve_border_theme_key(None, None) == DEFAULT_BORDER_THEME


def test_list_names_starts_with_the_follow_policy():
    names = list_border_theme_names()
    assert names[0][0] == BORDER_THEME_AUTO
    assert [key for key, _ in names[1:]] == list(BORDER_THEMES)
    assert all(display for _, display in names)


# ── "auto" colour resolution ──────────────────────────────────────────────


def test_auto_colours_inherit_from_the_active_ui_theme():
    resolved = resolve_border_theme(BORDER_THEMES["default"], **UI_COLORS)
    assert resolved["window_border_color"] == "#787878"
    assert resolved["value_box_bg"] == "#2e2e2e"
    assert resolved["value_box_border"] == "#555555"
    assert resolved["value_box_text"] == "#ffffff"
    assert "auto" not in [str(value) for value in resolved.values()]


def test_default_theme_keeps_the_legacy_window_frame():
    resolved = resolve_border_theme(BORDER_THEMES["default"], **UI_COLORS)
    assert resolved["window_border_width"] == 4
    assert resolved["window_border_radius"] == 0
    assert resolved["group_frame"] == "none"
    assert resolved["value_box"] is True
    assert resolved["groove_border_width"] == 0


def test_ps_value_box_keeps_its_own_colours_with_readable_text():
    resolved = resolve_border_theme(BORDER_THEMES["ps"], **UI_COLORS)
    assert resolved["value_box"] is True
    assert resolved["value_box_bg"] == "#d1d1d1"
    assert resolved["value_box_border"] == "#949494"
    assert resolved["value_box_border_width"] == 1
    # A light fill inside a dark UI theme must not keep the white body text.
    assert resolved["value_box_text"] == "#1e1e1e"


@pytest.mark.parametrize("key", ["sai", "csp"])
def test_boxless_themes_drop_the_value_box_chrome(key):
    resolved = resolve_border_theme(BORDER_THEMES[key], **UI_COLORS)
    assert resolved["value_box"] is False
    assert resolved["value_box_bg"] == "transparent"
    assert resolved["value_box_border"] == "transparent"
    assert resolved["value_box_border_width"] == 0
    assert resolved["value_box_text"] == "#ffffff"


def test_group_frame_modes_match_the_reference_screenshots():
    modes = {
        key: resolve_border_theme(theme, **UI_COLORS)["group_frame"]
        for key, theme in BORDER_THEMES.items()
    }
    assert modes == {"default": "none", "ps": "none", "sai": "line", "csp": "box"}


def test_unknown_group_frame_mode_is_ignored():
    theme = dict(BORDER_THEMES["csp"])
    theme["group_frame"] = "diagonal"
    assert resolve_border_theme(theme, **UI_COLORS)["group_frame"] == "none"


def test_is_light_color_handles_shorthand_and_garbage():
    assert is_light_color("#ffffff")
    assert is_light_color("#d1d1d1")
    assert is_light_color("#fff")
    assert not is_light_color("#1e1e1e")
    assert not is_light_color("#000")
    assert is_light_color("garbage")  # defensive default: assume light


# ── Slider triangle styles (screenshot alignment) ─────────────────────────


def test_slider_triangle_styles_match_the_screenshots():
    ps = SLIDER_THEMES["ps"]
    assert ps["handle_shape"] == "triangle-below"
    assert ps["handle_tri_style"] == "filled"
    assert ps["handle_tri_color"] == "#ffffff"
    assert ps["handle_tri_border_width"] == 0  # 白色实心、无深色描边

    sai = SLIDER_THEMES["sai"]
    assert sai["handle_shape"] == "triangle-below"
    assert sai["handle_tri_style"] == "outline"
    assert sai["handle_tri_border"] == "#000000"

    csp = SLIDER_THEMES["csp"]
    assert csp["handle_tri_style"] == "caret"  # 细 "^" 线，不是实心三角


# ── Rendering smoke test ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.mark.parametrize("slider_key", list(SLIDER_THEMES))
def test_every_theme_pair_paints(qapp, slider_key):
    """Each slider theme + its paired border theme must render cleanly."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPixmap

    from ui.widgets.gradient_slider import GradientSlider

    border = resolve_border_theme(
        get_border_theme(resolve_border_theme_key(BORDER_THEME_AUTO, slider_key)),
        **UI_COLORS,
    )
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.set_gradient([(0.0, QColor("#000000")), (1.0, QColor("#ffffff"))])
    slider.update_scale(1.0, SLIDER_THEMES[slider_key], border)
    slider.setValue(50)
    slider.resize(120, max(24, slider.minimumHeight()))

    pixmap = QPixmap(slider.size())
    pixmap.fill(QColor("#b2b2b2"))
    slider.render(pixmap)  # raises if paintEvent blows up

    assert slider.minimumHeight() > 0


def test_groove_outline_width_scales_with_ui_scale(qapp):
    from PyQt6.QtCore import Qt

    from ui.widgets.gradient_slider import GradientSlider

    border = resolve_border_theme(BORDER_THEMES["default"], **UI_COLORS)
    border["groove_border_width"] = 2
    border["groove_border_color"] = "#123456"

    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.update_scale(2.0, SLIDER_THEMES["default"], border)

    assert slider._groove_border_w == 4
    assert slider._groove_border_color == "#123456"

# ── End-to-end: ThemeMixin.apply_theme with each border theme ─────────────
#
# The rest of the suite only ever mocks apply_theme, so this host exercises
# the real chrome code (window frame width, value-box CSS, group frames).


def _make_theme_host(qapp, cfg):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from ui.widgets.gradient_slider import GradientSlider
    from ui.window.theme import ThemeMixin

    class StubTitleBar(QWidget):
        def __init__(self):
            super().__init__()
            layout = QHBoxLayout(self)
            self.btn_settings = QPushButton("S", self)
            self.btn_min = QPushButton("-", self)
            self.btn_close = QPushButton("x", self)
            self.title_label = QLabel("Colorink", self)
            for widget in (self.btn_settings, self.btn_min, self.btn_close, self.title_label):
                layout.addWidget(widget)

    class ThemeHost(ThemeMixin, QWidget):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.main_layout = QVBoxLayout(self)
            self.title_bar = StubTitleBar()
            self.main_layout.addWidget(self.title_bar)

            self.sliders_container = QWidget(self)
            self.sliders_layout = QVBoxLayout(self.sliders_container)
            self.main_layout.addWidget(self.sliders_container)

            self.slider_row_layouts = []
            self.slider_labels = {}
            self.slider_widgets = {}
            self.slider_containers = {}
            for group, channels in (("RGB", ["R", "G", "B"]), ("HSV", ["H_hsv", "S_hsv", "V_hsv"])):
                container = QWidget(self.sliders_container)
                group_layout = QVBoxLayout(container)
                group_layout.setContentsMargins(0, 0, 0, 0)
                for chan in channels:
                    row = QHBoxLayout()
                    self.slider_row_layouts.append(row)
                    label = QLabel(chan[0], container)
                    label.setObjectName("ChannelLabel")
                    slider = GradientSlider(Qt.Orientation.Horizontal, container)
                    value = QLabel("0", container)
                    value.setObjectName("ValueLabel")
                    row.addWidget(label)
                    row.addWidget(slider)
                    row.addWidget(value)
                    group_layout.addLayout(row)
                    self.slider_labels[chan] = label
                    self.slider_widgets[chan] = (slider, value)
                self.slider_containers[group] = container
                self.sliders_layout.addWidget(container)

            self.btn_mode_wheel = QPushButton("O", self)
            self.btn_mode_lab = QPushButton("A", self)
            self.visibility_calls = 0

        def update_mode_buttons_visibility(self):
            self.visibility_calls += 1

    base = {
        "ui-theme": "gray",
        "uiScale": 100,
        "fontSize": 100,
        "sliderSameSpace": 6,
        "sliderDiffSpace": 8,
        "showLabLightnessSlider": False,
    }
    base.update(cfg)
    host = ThemeHost(base)
    # Show it (offscreen) so title_bar.isVisible() is True the way it is in a
    # real window — the top-edge logic branches on exactly that.
    host.show()
    # is_resize_event=True skips _adjust_content_height (main-window only).
    host.apply_theme(1.0, is_resize_event=True)
    return host


@pytest.mark.parametrize("border_style", [BORDER_THEME_AUTO, *BORDER_THEMES])
@pytest.mark.parametrize("slider_style", list(SLIDER_THEMES))
def test_apply_theme_runs_for_every_combination(qapp, border_style, slider_style):
    host = _make_theme_host(qapp, {"sliderStyle": slider_style, "borderStyle": border_style})
    assert host.visibility_calls == 1
    assert host.styleSheet()


def test_default_theme_keeps_the_legacy_chrome(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": BORDER_THEME_AUTO})
    margins = host.main_layout.contentsMargins()

    assert (margins.left(), margins.right(), margins.bottom()) == (4, 4, 4)
    assert "border-left: 4px solid" in host.styleSheet()
    value_css = host.slider_widgets["R"][1].styleSheet()
    assert "border: 1px solid" in value_css
    # No group frame: containers keep flush margins.
    group_margins = host.slider_containers["RGB"].layout().contentsMargins()
    assert (group_margins.left(), group_margins.bottom()) == (0, 0)


def test_ps_border_theme_boxes_the_value_readout(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "ps", "borderStyle": BORDER_THEME_AUTO})
    value_css = host.slider_widgets["R"][1].styleSheet()

    assert "#d1d1d1" in value_css          # PS 的浅灰填充
    assert "1px solid #949494" in value_css  # PS 的灰色描边
    assert "#1e1e1e" in value_css          # 深字色，避免浅底白字


def test_sai_border_theme_draws_group_divider_lines_and_no_value_box(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "sai", "borderStyle": BORDER_THEME_AUTO})
    margins = host.main_layout.contentsMargins()
    first = host.slider_containers["RGB"]
    group_margins = first.layout().contentsMargins()

    assert margins.left() == 3  # SAI 用更细的窗口边框
    assert "border-bottom: 1px solid" in first.styleSheet()
    assert "border-radius" not in first.styleSheet()
    assert group_margins.bottom() > 0  # 给分隔线留出高度
    assert "border: none" in host.slider_widgets["R"][1].styleSheet()


def test_group_divider_is_skipped_after_the_last_shown_group(qapp):
    """分隔线只出现在两组之间，最后一组下面不留线。"""
    host = _make_theme_host(qapp, {"sliderStyle": "sai", "borderStyle": BORDER_THEME_AUTO})

    assert "border-bottom" in host.slider_containers["RGB"].styleSheet()
    assert host.slider_containers["HSV"].styleSheet() == ""  # 最后一组
    assert host.slider_containers["HSV"].layout().contentsMargins().bottom() == 0


def test_group_divider_follows_group_visibility(qapp):
    """隐藏最后一组后，分隔线要跟着往前挪，不能留在中间。"""
    host = _make_theme_host(qapp, {"sliderStyle": "sai", "borderStyle": BORDER_THEME_AUTO})
    host.slider_containers["HSV"].setVisible(False)
    host.apply_theme(1.0, is_resize_event=True)

    assert host.slider_containers["RGB"].styleSheet() == ""  # 现在它才是最后一组


def test_csp_border_theme_boxes_each_group_and_no_value_box(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "csp", "borderStyle": BORDER_THEME_AUTO})
    container = host.slider_containers["HSV"]
    group_margins = container.layout().contentsMargins()

    assert "border: 1px solid" in container.styleSheet()
    assert "border-radius: 2px" in container.styleSheet()
    assert min(group_margins.left(), group_margins.top(), group_margins.bottom()) > 0
    assert "border: none" in host.slider_widgets["R"][1].styleSheet()


def test_border_style_can_be_pinned_independently_of_the_slider_style(qapp):
    """A pinned borderStyle must beat the slider theme's own pairing."""
    host = _make_theme_host(qapp, {"sliderStyle": "csp", "borderStyle": "ps"})

    assert "#d1d1d1" in host.slider_widgets["R"][1].styleSheet()   # PS 数值框
    assert host.slider_containers["HSV"].styleSheet() == ""        # 不画 CSP 分组框


def test_switching_border_theme_clears_the_previous_group_frame(qapp):
    """Frames must not linger when switching back to a frameless theme."""
    host = _make_theme_host(qapp, {"sliderStyle": "csp", "borderStyle": BORDER_THEME_AUTO})
    assert "border: 1px solid" in host.slider_containers["RGB"].styleSheet()

    host.cfg["sliderStyle"] = "default"
    host.apply_theme(1.0, is_resize_event=True)

    container = host.slider_containers["RGB"]
    assert container.styleSheet() == ""
    assert container.layout().contentsMargins().left() == 0

# ── Title bar (the window's top edge) ─────────────────────────────────────


def test_every_border_theme_declares_title_bar_fields():
    for key, theme in BORDER_THEMES.items():
        assert "title_bar_height" in theme, key
        assert "title_bar_button_size" in theme, key
        assert "title_bar_divider_width" in theme, key
        assert "title_bar_divider_color" in theme, key
        assert "title_bar_inset" in theme, key


def test_default_title_bar_keeps_the_legacy_flush_band(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": BORDER_THEME_AUTO})

    assert host.main_layout.contentsMargins().top() == 0   # 标题栏顶到边
    assert "border-top: none" in host.styleSheet()          # 顶边线由标题栏接管
    assert "border-bottom: none" in host.styleSheet()       # 默认无分隔线
    assert host.title_bar.height() == 28


# 用户给定的目标像素（1× / 100% DPI）
TITLE_BAR_HEIGHTS = {"default": 28, "ps": 42, "sai": 36, "csp": 28}


@pytest.mark.parametrize("border_style,expected", sorted(TITLE_BAR_HEIGHTS.items()))
def test_title_bar_height_matches_the_requested_pixels(qapp, border_style, expected):
    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": border_style})

    assert BORDER_THEMES[border_style]["title_bar_height"] == expected
    assert host.title_bar.height() == expected


@pytest.mark.parametrize("border_style", list(BORDER_THEMES))
def test_buttons_do_not_grow_with_a_taller_title_bar(qapp, border_style):
    """PS 那种高顶栏里控件依然是小的，按钮不跟着栏高等比放大。"""
    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": border_style})

    for button in (host.title_bar.btn_settings, host.title_bar.btn_min, host.title_bar.btn_close):
        assert button.width() == 18
        assert button.height() == 18


def test_sai_title_bar_gets_a_divider_but_stays_flush(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "sai", "borderStyle": BORDER_THEME_AUTO})

    assert host.main_layout.contentsMargins().top() == 0
    assert "border-top: none" in host.styleSheet()
    assert "border-bottom: 1px solid" in host.styleSheet()
    assert host.title_bar.height() == 36  # SAI 的标题条更高


def test_csp_title_bar_is_inset_so_the_frame_wraps_above_it(qapp):
    host = _make_theme_host(qapp, {"sliderStyle": "csp", "borderStyle": BORDER_THEME_AUTO})

    assert host.main_layout.contentsMargins().top() == 4     # 上方留出边框带
    assert "border-top: 4px solid" in host.styleSheet()      # 顶边线画出来
    assert "border-bottom: 1px solid" in host.styleSheet()   # 标题栏底部分隔线


def test_title_bar_button_size_is_independently_settable(qapp, monkeypatch):
    custom = dict(BORDER_THEMES["default"])
    custom["display_name"] = "测试用"
    custom["title_bar_height"] = 44
    custom["title_bar_button_size"] = 24
    monkeypatch.setitem(BORDER_THEMES, "test_tall_bar", custom)

    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": "test_tall_bar"})

    assert host.title_bar.height() == 44
    assert host.title_bar.btn_close.width() == 24


def test_buttons_are_clamped_inside_a_short_title_bar(qapp, monkeypatch):
    """栏被压很矮时按钮不能顶破它。"""
    custom = dict(BORDER_THEMES["default"])
    custom["display_name"] = "测试用"
    custom["title_bar_height"] = 16
    custom["title_bar_button_size"] = 18
    monkeypatch.setitem(BORDER_THEMES, "test_squished_bar", custom)

    host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": "test_squished_bar"})

    assert host.title_bar.height() == 16
    assert host.title_bar.btn_close.width() <= 16 - 4


def test_inset_title_bar_offsets_content_below_the_top_frame(qapp):
    """预览色块的定位偏移必须把顶部边框带算进去。"""
    from ui.widgets import _title_bar_content_offset

    host = _make_theme_host(qapp, {"sliderStyle": "csp", "borderStyle": BORDER_THEME_AUTO})
    host.title_bar.setVisible(True)
    offset = _title_bar_content_offset(host.title_bar, host.main_layout)

    assert offset == host.main_layout.contentsMargins().top() + host.title_bar.height()


# ── Row spacing / value alignment (screenshot fine-tuning) ────────────────


def test_slider_geometry_matches_the_measured_screenshots():
    # 槽高：PS 约 9px、SAI 约 13px、CSP 约 12px（基准 16px × factor）
    assert SLIDER_THEMES["ps"]["groove_h_factor"] * 16 == pytest.approx(9, abs=0.5)
    assert SLIDER_THEMES["sai"]["groove_h_factor"] * 16 == pytest.approx(13, abs=0.5)
    assert SLIDER_THEMES["csp"]["groove_h_factor"] * 16 == pytest.approx(12, abs=0.5)
    # 三角/指示总宽：PS 约 15px、SAI 约 13px、CSP 约 14px
    assert SLIDER_THEMES["ps"]["handle_tri_size_w"] * 2 == pytest.approx(15, abs=1)
    sai_total = (
        SLIDER_THEMES["sai"]["handle_tri_size_w"]
        + SLIDER_THEMES["sai"]["handle_tri_base_overhang"]
    ) * 2
    assert sai_total == pytest.approx(13, abs=1)
    assert SLIDER_THEMES["csp"]["handle_tri_size_w"] * 2 == pytest.approx(14, abs=1)
    # PS 的三角顶点贴着槽底
    assert SLIDER_THEMES["ps"]["handle_tri_offset_y"] == 0
    # 直角/极小圆角
    assert SLIDER_THEMES["ps"]["groove_radius_factor"] == 0.0
    assert SLIDER_THEMES["csp"]["groove_radius_factor"] == 0.0
    assert SLIDER_THEMES["sai"]["groove_radius_factor"] * 3 < 1.5


def test_row_spacing_comes_from_the_slider_theme(qapp):
    ps_host = _make_theme_host(qapp, {"sliderStyle": "ps", "borderStyle": "ps"})
    default_host = _make_theme_host(qapp, {"sliderStyle": "default", "borderStyle": "default"})

    assert ps_host.slider_row_layouts[0].spacing() == 4
    assert default_host.slider_row_layouts[0].spacing() == 1


@pytest.mark.parametrize(
    "slider_style,flag",
    [("default", "AlignLeft"), ("ps", "AlignLeft"), ("sai", "AlignRight"), ("csp", "AlignRight")],
)
def test_value_label_alignment_follows_the_screenshots(qapp, slider_style, flag):
    from PyQt6.QtCore import Qt

    host = _make_theme_host(qapp, {"sliderStyle": slider_style, "borderStyle": BORDER_THEME_AUTO})
    alignment = host.slider_widgets["R"][1].alignment()

    assert alignment & getattr(Qt.AlignmentFlag, flag)
    assert alignment & Qt.AlignmentFlag.AlignVCenter

# ── Indicator rendering (pixel-level) ─────────────────────────────────────
#
# Two real bugs lived here: the triangle was clipped by the bottom edge
# (minimum height assumed a top-aligned groove, paint centred it), and the
# caret/outline arms were asymmetric (a polyline's join rasterises the second
# segment differently). Both are only visible in pixels, so these render.

INDICATOR_STYLES = ["ps", "sai", "csp"]


def _render_slider(slider_style, height=None, width=121, bg="#eeeeee"):
    """Render one slider on a flat background; groove is pure red."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage

    from ui.widgets.gradient_slider import GradientSlider

    border = resolve_border_theme(
        get_border_theme(resolve_border_theme_key(BORDER_THEME_AUTO, slider_style)),
        **UI_COLORS,
    )
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.set_gradient([(0.0, QColor("#ff0000")), (1.0, QColor("#ff0000"))])
    slider.update_scale(1.0, SLIDER_THEMES[slider_style], border)
    slider.setRange(0, 100)
    slider.setValue(50)
    slider.resize(width, height or slider.minimumHeight())

    image = QImage(slider.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(bg))
    slider.render(image)
    return slider, image


def _indicator_rows(image):
    """Rows below the groove, as {y: [x, ...]} of non-background pixels."""
    from PyQt6.QtGui import QColor

    # Take the background from the render itself: QWidget.render() paints the
    # widget's own palette background over whatever the image was filled with,
    # so hard-coding the fill colour matches every pixel and these masks
    # silently become meaningless (they did — every row "spanned" 121px).
    background = image.pixel(0, 0)

    def is_reddish(x, y):
        # Includes the groove's antialiased edge rows (red blended with the
        # background), which are groove, not indicator.
        color = QColor(image.pixel(x, y))
        return color.red() - color.green() > 25

    reddish = [
        y for y in range(image.height())
        if any(is_reddish(x, y) for x in range(image.width()))
    ]
    start = (max(reddish) + 1) if reddish else 0
    rows = {}
    for y in range(start, image.height()):
        xs = [
            x for x in range(image.width())
            if image.pixel(x, y) != background and not is_reddish(x, y)
        ]
        if xs:
            rows[y] = xs
    return rows


@pytest.mark.parametrize("slider_style", INDICATOR_STYLES)
def test_indicator_is_not_clipped_at_minimum_height(slider_style):
    """三角/指示器在最小高度下必须完整画出来，不能被底边裁掉。

    回归点：最小高度按"槽在顶部"算，paintEvent 却把槽居中，导致三套
    triangle 主题的指示器统统被裁掉 3px。
    """
    theme = SLIDER_THEMES[slider_style]
    expected_span = 2 * float(theme["handle_tri_size_w"])
    expected_span += 2 * float(theme.get("handle_tri_base_overhang", 0))

    _, image = _render_slider(slider_style)
    rows = _indicator_rows(image)

    assert rows, "指示器完全没画出来"
    widest = max(max(xs) - min(xs) + 1 for xs in rows.values())
    assert widest >= expected_span - 1, (
        f"{slider_style} 指示器最宽只有 {widest}px，应约 {expected_span}px —— 底部被裁了"
    )


@pytest.mark.parametrize("slider_style", INDICATOR_STYLES)
def test_indicator_is_left_right_symmetric(slider_style):
    """指示器必须左右对称。

    回归点：用一条 polyline 画 "^" 时，接头之后的第二段栅格化方式不同，
    左臂是一列实色、右臂糊成两列。改成从顶点分别画两条线才对称。
    """
    from PyQt6.QtGui import QColor

    slider, image = _render_slider(slider_style)
    center = round(0.5 * slider.width())  # paintEvent 的对称轴

    def lum(x, y):
        color = QColor(image.pixel(x, y))
        return (color.red() * 299 + color.green() * 587 + color.blue() * 114) // 1000

    mismatches = []
    for y in _indicator_rows(image):
        for k in range(1, 16):
            left, right = center - k, center + k
            if 0 <= left and right < image.width():
                if abs(lum(left, y) - lum(right, y)) > 8:
                    mismatches.append((y, k, lum(left, y), lum(right, y)))
    assert not mismatches, f"{slider_style} 指示器左右不对称: {mismatches[:6]}"


def test_sai_handle_has_a_heavy_overhanging_base_bar():
    """SAI 把手：底下是一条比三角略宽的粗黑条，黑条顶行压一道浅灰线。"""
    from PyQt6.QtGui import QColor

    _, image = _render_slider("sai")
    rows = _indicator_rows(image)
    ys = sorted(rows)

    bottom, above = ys[-1], ys[-2]
    bottom_span = max(rows[bottom]) - min(rows[bottom]) + 1
    # 最底行是实心黑条，且比上一行（三角腰部）更宽
    assert bottom_span == 13, f"底边黑条应为 13px 宽，实际 {bottom_span}"
    assert bottom_span > max(rows[above]) - min(rows[above]) + 1 - 1
    assert len(rows[bottom]) == bottom_span, "底边应是连续实心，不能有空洞"
    for x in rows[bottom]:
        assert QColor(image.pixel(x, bottom)).lightness() < 60, "底边应是黑的"

    # 黑条顶行：两端黑、中间浅灰
    mid = (min(rows[above]) + max(rows[above])) // 2
    assert QColor(image.pixel(mid, above)).lightness() > 100, "黑条顶行中间应是浅灰线"


def test_only_sai_draws_a_base_bar():
    for key, theme in SLIDER_THEMES.items():
        expected = 2 if key == "sai" else 0
        assert theme["handle_tri_base_width"] == expected, key


@pytest.mark.parametrize("slider_style", INDICATOR_STYLES)
def test_indicator_survives_a_taller_row(slider_style):
    """行高被拉高时指示器仍然完整（整组居中，不是只居中槽）。"""
    theme = SLIDER_THEMES[slider_style]
    expected_span = 2 * float(theme["handle_tri_size_w"])
    expected_span += 2 * float(theme.get("handle_tri_base_overhang", 0))

    _, image = _render_slider(slider_style, height=48)
    rows = _indicator_rows(image)

    widest = max(max(xs) - min(xs) + 1 for xs in rows.values())
    assert widest >= expected_span - 1
