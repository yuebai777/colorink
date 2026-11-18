"""Tests for CSP / SAI / PS theme fidelity and adaptive cross-background contrast."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QTabWidget, QWidget

from core import config
from ui.border_themes import (
    AUTO,
    BORDER_THEMES,
    get_border_theme,
    resolve_border_theme,
)
from ui.color_model import Color
from ui.color_preview_box import ColorPreviewBox
from ui.color_wheel import ColorWheel
from ui.lab_visualizer import LabSlider
from ui.panels.floating import PanelChrome
from ui.panels.host import PanelHost
from ui.picker_panes import PickerTabBar
from ui.slider_themes import SLIDER_THEMES, get_slider_theme, resolve_slider_theme
from ui.theme_contrast import (
    build_tab_bar_css,
    derive_chrome_palette,
    relative_luma,
)
from ui.widgets.color_value_readout import ColorValueReadout
from ui.widgets.gradient_slider import GradientSlider

from .test_ringless_support import qapp  # noqa: F401


def _luma_gap(c1: str, c2: str) -> float:
    return abs(relative_luma(c1) - relative_luma(c2))


# ── Palette & pure theme resolution ──────────────────────────────────────────

@pytest.mark.parametrize("bg,bar_bg", [
    ("#ffffff", "#b2b2b2"),   # white
    ("#b2b2b2", "#787878"),   # gray
    ("#1e1e1e", "#2d2d2d"),   # black
    ("#535353", "#383838"),   # dark CC mid-gray
    ("#ffee00", "#222222"),   # saturated light yellow + dark bar
    ("#f3f4f6", "#23272e"),   # eyedropper: dark bar + light body
    ("#26282e", "#dcdcdc"),   # eyedropper: light bar + dark body
])
def test_derive_chrome_palette_keeps_readable_contrast_everywhere(bg, bar_bg):
    pal = derive_chrome_palette(bg, bar_bg, bar_bg)

    assert _luma_gap(pal["text"], bg) >= 95.0
    assert _luma_gap(pal["bar_text"], bar_bg) >= 95.0
    assert _luma_gap(pal["mode_btn_fg"], bar_bg) >= 95.0
    assert _luma_gap(pal["channel_text"], bg) >= 65.0
    assert _luma_gap(pal["ps_box_text"], pal["ps_box_bg"]) >= 110.0
    assert _luma_gap(pal["swatch_inactive_border"], bg) >= 24.0
    assert _luma_gap(pal["history_border"], bg) >= 20.0
    assert _luma_gap(pal["wheel_ring_border"], bg) >= 24.0
    assert _luma_gap(pal["wheel_slice_border"], bg) >= 24.0


def test_derive_chrome_palette_preserves_ps_gray_box_colors():
    pal = derive_chrome_palette("#b2b2b2", "#787878", "#787878")
    assert pal["ps_box_bg"] == "#d1d1d1"
    assert pal["ps_box_border"] == "#949494"
    assert pal["ps_box_text"] == "#1e1e1e"


@pytest.mark.parametrize("key", ["default", "ps", "sai", "csp"])
@pytest.mark.parametrize("bg,bar_bg", [
    ("#ffffff", "#b2b2b2"),
    ("#b2b2b2", "#787878"),
    ("#1e1e1e", "#2d2d2d"),
    ("#26282e", "#dcdcdc"),
])
def test_resolve_border_theme_resolves_every_auto_token(key, bg, bar_bg):
    pal = derive_chrome_palette(bg, bar_bg, bar_bg)
    resolved = resolve_border_theme(
        get_border_theme(key),
        chrome_border=bar_bg,
        input_bg=pal["input_bg"],
        input_border=pal["input_border"],
        text=pal["text"],
        bg=bg,
        bar_bg=bar_bg,
    )
    for k, val in resolved.items():
        assert val != AUTO, f"{key}.{k} remained unresolved AUTO"


def test_border_theme_structural_traits_match_reference_apps():
    assert BORDER_THEMES["ps"]["swatch_shape"] == "square"
    assert BORDER_THEMES["ps"]["button_radius"] == 1
    assert BORDER_THEMES["ps"]["tab_style"] == "ps"
    assert BORDER_THEMES["ps"]["wheel_style"] == "ps"
    assert BORDER_THEMES["csp"]["tab_style"] == "csp"
    assert BORDER_THEMES["csp"]["wheel_style"] == "csp"
    assert BORDER_THEMES["sai"]["tab_style"] == "sai"
    assert BORDER_THEMES["sai"]["wheel_style"] == "sai"
    assert BORDER_THEMES["sai"]["groove_inner_border_width"] == 1

    dark = resolve_border_theme(
        get_border_theme("sai"),
        chrome_border="#2d2d2d",
        input_bg="#2e2e2e",
        input_border="#555555",
        text="#ffffff",
        bg="#1e1e1e",
        bar_bg="#2d2d2d",
    )
    assert dark["group_frame_color"] != "#c9c9c9"
    assert _luma_gap(dark["group_frame_color"], "#1e1e1e") >= 20.0


def test_resolve_slider_theme_adapts_handles_without_mutating_raw():
    assert SLIDER_THEMES["ps"]["handle_tri_pentagon"] is True
    assert SLIDER_THEMES["ps"]["handle_tri_border_width"] == 0
    assert SLIDER_THEMES["sai"]["handle_tri_border"] == "#000000"

    ps_white = resolve_slider_theme(get_slider_theme("ps"), bg="#ffffff")
    assert ps_white["handle_tri_border_width"] == 1
    assert _luma_gap(ps_white["handle_tri_border"], "#ffffff") >= 110.0

    ps_dark = resolve_slider_theme(get_slider_theme("ps"), bg="#1e1e1e")
    assert _luma_gap(ps_dark["handle_tri_color"], "#1e1e1e") >= 160.0

    sai_dark = resolve_slider_theme(get_slider_theme("sai"), bg="#1e1e1e")
    assert _luma_gap(sai_dark["handle_tri_border"], "#1e1e1e") >= 160.0
    assert _luma_gap(sai_dark["handle_tri_base_color"], "#1e1e1e") >= 160.0

    for bg in ("#ffffff", "#b2b2b2", "#1e1e1e"):
        csp = resolve_slider_theme(get_slider_theme("csp"), bg=bg)
        assert _luma_gap(csp["handle_tri_color"], bg) >= 120.0


# ── Widget rendering & interaction tests ─────────────────────────────────────

def _render_slider_img(style: str, bg: str, *, val: int = 50, groove_hex: str | None = None) -> QImage:
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    if groove_hex is None:
        slider.set_gradient([(0.0, QColor("#000000")), (1.0, QColor("#ffffff"))])
    else:
        c = QColor(groove_hex)
        slider.set_gradient([(0.0, c), (1.0, c)])
    pal = derive_chrome_palette(bg, bg, bg)
    border = resolve_border_theme(
        get_border_theme(style),
        chrome_border=bg,
        input_bg=pal["input_bg"],
        input_border=pal["input_border"],
        text=pal["text"],
        bg=bg,
        bar_bg=bg,
    )
    resolved_slider = resolve_slider_theme(get_slider_theme(style), bg=bg)
    slider.setValue(val)
    slider.update_scale(1.0, resolved_slider, border)
    slider.resize(130, max(24, slider.minimumHeight()))
    img = QImage(slider.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(bg))
    painter = QPainter(img)
    slider.render(painter, flags=QWidget.RenderFlag.DrawChildren)
    painter.end()
    return img


@pytest.mark.parametrize("style", ["default", "ps", "sai", "csp"])
@pytest.mark.parametrize("bg", ["#ffffff", "#b2b2b2", "#1e1e1e"])
def test_slider_themes_render_visible_handle_on_all_backgrounds(qapp, style, bg):
    img = _render_slider_img(style, bg)
    bg_q = QColor(bg)
    center_x = img.width() // 2
    diff_pixels = 0
    for y in range(img.height() // 2, img.height()):
        for x in range(center_x - 10, center_x + 11):
            c = img.pixelColor(x, y)
            if (
                abs(c.red() - bg_q.red())
                + abs(c.green() - bg_q.green())
                + abs(c.blue() - bg_q.blue())
            ) > 40:
                diff_pixels += 1
    assert diff_pixels >= 6, f"{style} on {bg} rendered an invisible handle"


def test_ps_slider_renders_wider_pentagon_shoulders_than_apex(qapp):
    img = _render_slider_img("ps", "#1e1e1e", val=50, groove_hex="#1e1e1e")
    bg_q = QColor("#1e1e1e")
    row_widths = []
    # Scan rows below the dark groove where only the white pentagon sits.
    for y in range(img.height()):
        xs = [
            x
            for x in range(img.width() // 2 - 14, img.width() // 2 + 15)
            if img.pixelColor(x, y).lightness() > bg_q.lightness() + 80
        ]
        if xs:
            row_widths.append(max(xs) - min(xs) + 1)
    assert len(row_widths) >= 5
    # Pentagon: apex row is narrowest; vertical shoulders maintain max width.
    assert max(row_widths) >= 11
    assert row_widths[0] <= 5 < max(row_widths)
    assert row_widths[-1] == max(row_widths)


@pytest.mark.parametrize("style", ["default", "csp", "sai", "ps"])
def test_color_wheel_paints_for_each_visual_style(qapp, style):
    wheel = ColorWheel()
    wheel.set_visual_theme(
        wheel_style=style,
        ring_border_color="#666666",
        slice_border_color="#444444",
    )
    wheel.resize(220, 220)
    img = QImage(QSize(220, 220), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#b2b2b2"))
    painter = QPainter(img)
    wheel.render(painter)
    painter.end()

    # For SAI square mode, check the thin inscribed circle just outside SV top edge.
    if style == "sai":
        cx, cy, _sz, _outer_r, inner_r, tri_r = wheel.get_wheel_geometry()
        cxi = int(round(cx))
        top_inner = int(round(cy - inner_r)) + 1
        sv_top = int(round(cy - tri_r * 0.707)) - 1
        inscribed_hits = sum(
            1
            for y in range(top_inner, sv_top)
            if img.pixelColor(cxi, y) != QColor("#b2b2b2")
        )
        assert inscribed_hits >= 1


def test_preview_box_supports_square_ps_swatches_and_clicks(qapp):
    box = ColorPreviewBox()
    box.resize(70, 70)

    def _paint(shape: str) -> QImage:
        box.set_theme_colors("#4c9eff", "#555555", swatch_shape=shape)
        img = QImage(box.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("#383838"))
        painter = QPainter(img)
        box.render(painter)
        painter.end()
        return img

    sq_img = _paint("square")
    ci_img = _paint("circle")
    box.set_theme_colors("#4c9eff", "#555555", swatch_shape="square")
    assert box._swatch_shape == "square"
    assert box._trans_tile._sharp_corners is True

    # Square swatches differ visually from circle swatches in bounding corners.
    diff = sum(
        1
        for y in range(70)
        for x in range(70)
        if sq_img.pixelColor(x, y) != ci_img.pixelColor(x, y)
    )
    assert diff >= 40

    # Square corner (0.82*r diagonal) hits in "square" mode, misses in "circle".
    fg_cx, fg_cy, fg_r, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()
    assert box._get_clicked_slot(fg_cx - fg_r * 0.82, fg_cy - fg_r * 0.82) == "fg"
    assert box._get_clicked_slot(bg_cx + bg_r * 0.82, bg_cy + bg_r * 0.82) == "bg"
    box.set_theme_colors("#4c9eff", "#555555", swatch_shape="circle")
    assert box._get_clicked_slot(fg_cx - fg_r * 0.82, fg_cy - fg_r * 0.82) is None


def test_lab_slider_border_theme_paints_outer_and_inner_bevel(qapp):
    sl = LabSlider()
    sl.resize(20, 120)
    sl.set_border_theme(
        border_w=1, border_color="#202020", inner_w=1, inner_color="#fafafa",
    )
    img = QImage(sl.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#b2b2b2"))
    painter = QPainter(img)
    sl.render(painter)
    painter.end()
    assert not img.isNull()


def test_color_value_readout_handles_rgba_chrome_and_ps_corners(qapp):
    ro = ColorValueReadout()
    ro.set_color(Color.from_rgb(73, 104, 104))
    ro.set_theme(
        bg="rgba(178,178,178,0.500)",
        bar_bg="#23272e",
        text="#222222",
        border="#444444",
        scale=1.0,
        corner_radius=0.0,
        chip_radius=0.5,
    )
    assert ro._bg_color.red() == 178
    assert ro._bg_color.green() == 178
    assert ro._corner_radius == 0.0
    assert ro._chip_radius == 0.5
    ro.set_pill_backdrop(True)
    ro.resize(ro.sizeHint())
    img = QImage(ro.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    ro.render(painter)
    painter.end()
    assert not img.isNull()


@pytest.mark.parametrize("tab_style,expected_token", [
    ("default", "border-top:"),
    ("csp", "border-top-left-radius: 2px;"),
    ("sai", "border: 1px solid #5a94e2;"),
    ("ps", "border-radius: 0px;"),
])
def test_tab_bar_css_variants_propagate_to_picker_and_panel_tabs(qapp, tab_style, expected_token):
    chrome = PanelChrome(
        background="#b2b2b2",
        border_color="#787878",
        border_width=2,
        text="#222222",
        bar_bg="#787878",
        bar_text="#efefef",
        divider_color="#555555",
        divider_width=1,
        tab_style=tab_style,
        tab_border_color="#8a8a8a",
        tab_accent_bg="#c2d6f0",
        tab_accent_border="#5a94e2",
    )
    css = build_tab_bar_css(
        chrome,
        bar_selector="QTabBar#Test",
        tab_selector="QTabBar#Test::tab",
    )
    assert expected_token in css

    bar = PickerTabBar()
    bar.apply_chrome(chrome)
    assert expected_token in bar.styleSheet()

    host = PanelHost(QWidget())
    tabs = QTabWidget()
    host.apply_chrome(chrome)
    host._style_tabs(tabs)
    assert expected_token in tabs.styleSheet()


# ── End-to-end MainWindow across styles & backgrounds ────────────────────────

def test_main_window_switches_all_themes_and_backgrounds_cleanly(qapp):
    import ui.main_window as main_window

    base_cfg = config.load_hotkey_config()
    base_cfg.update({
        "onlyShowInCsp": False,
        "panelDrag": False,
        "slidersTabs": False,
        "pickerModuleTabs": True,
        "floatingPanels": {},
        "panelLayout": None,
        "floatingWheelPageOptions": {},
    })
    with patch("core.config.load_hotkey_config", return_value=dict(base_cfg)), \
         patch("core.config.save_hotkey_config"), \
         patch("core.memory_sync.MemorySyncThread.start"), \
         patch("core.global_hotkeys.bind_hotkey"), \
         patch("core.global_hotkeys.bind_mouse_hotkey"), \
         patch("core.global_hotkeys.unbind_all"):
        win = main_window.MainWindow()
        try:
            for style in ("csp", "sai", "ps", "default"):
                win.cfg["sliderStyle"] = style
                win.cfg["borderStyle"] = style
                for ui_t in ("white", "gray", "black", "eyedropper"):
                    win.cfg["ui-theme"] = ui_t
                    if ui_t == "eyedropper":
                        win.cfg["uiThemeDropperColorBar"] = "#23272e"
                        win.cfg["uiThemeDropperColorBg"] = "#f3f4f6"
                    win.apply_theme()
                    qapp.processEvents()

                    assert win.preview_box._swatch_shape == (
                        "square" if style == "ps" else "circle"
                    )
                    assert win.color_wheel._wheel_style == style
                    assert win._floating_chrome.tab_style == style
                    expected_corner = 0.0 if style == "ps" else 3.5
                    assert win.readout_wheel._corner_radius == expected_corner
        finally:
            win.close()
