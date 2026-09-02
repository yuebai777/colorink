"""Theme application for the main window.

Extracted from ``ui.main_window``: applies the resolved UI theme, slider
visual theme and per-widget geometry/stylesheet updates.
"""

from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from core import config

from ui.border_themes import (
    get_border_theme,
    resolve_border_theme,
    resolve_border_theme_key,
)
from ui.chrome_opacity import (
    CHROME_OPACITY_MAX,
    resolve_chrome_opacity,
    with_opacity,
)
from ui import window_layout
from ui.ringless_mode import RINGLESS_ACTIVE_BORDER
from ui.slider_themes import get_slider_theme
from ui.window_layout import snap_border_width
from ui.widgets import (
    GradientSlider,
    _title_bar_content_offset,
    _visible_title_bar_height,
)


def _set_css(widget, css: str) -> None:
    """Assign a stylesheet only when it actually changed.

    Qt re-polishes the whole subtree on every assignment, and a full theme
    pass writes ~50 sheets. Re-running that on each resize event (nothing
    style-related changes while dragging a window) is what made the drag
    stutter: 4240 assignments over 80 passes, each cascading thousands of
    polish events through the app event filter.
    """
    if getattr(widget, "_cached_css", None) == css:
        return
    widget._cached_css = css
    widget.setStyleSheet(css)


class ThemeMixin:

    def _sync_lab_lightness_bar(self, scale=None):
        """Compose the LAB pane as [gap][plane][gap][bar][gap].

        Showing the vertical lightness bar used to simply steal a fixed
        column from the left-hand widget: the a*b* plane kept centring
        itself in whatever was left, so the pane ended up with a wide hole
        between the plane and the bar while the bar itself hugged the window
        edge — and the circular disc silently shrank away from the hue
        ring's size.  Both are laid out here instead:

        * the plane is sized against the FULL pane width minus the bar
          cluster, so all three margins come out even;
        * the column is exactly ``gap + bar + gap`` wide, so the plane's
          right margin and the bar's right margin match;
        * the bar's own band is aligned with the plane's top and bottom
          (while clearing the floating mode buttons in the bottom-right
          corner) instead of spanning the whole pane height.
        """
        if not hasattr(self, "lab_square") or not hasattr(self, "lab_slider_column"):
            return
        # setFixedWidth() below re-enters through LabSquare.resizeEvent →
        # planeGeometryChanged; one pass is always enough.
        if getattr(self, "_syncing_lab_bar", False):
            return
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0

        visible = bool(self.cfg.get("showLabLightnessSlider", True))
        gap = max(4, int(round(8 * scale)))
        bar_w = max(10, int(round(18 * scale)))

        self._syncing_lab_bar = True
        try:
            self.lab_slider_column.setVisible(visible)
            column_layout = self.lab_slider_column.layout()
            total = 0
            if visible:
                margins = self.lab_layout.contentsMargins()
                total = self.pane_lab.width() - margins.left() - margins.right()
            if not visible or total <= bar_w + 3 * gap:
                # Hidden (or a pane too narrow to share): the plane goes back
                # to centring itself on the whole pane.
                self.lab_square.set_side_cluster(0, 0, gap)
                return

            # The column owns every pixel between the plane and the window
            # edge, so the layout spacing must not add another seam.
            self.lab_layout.setSpacing(0)
            self.lab_square.set_side_cluster(total, bar_w, gap)
            plane_gap, _x, plane_y, plane_size = self.lab_square.plane_geometry()

            side = max(gap, int(round(plane_gap)))
            column_w = 2 * side + bar_w
            self.lab_slider.setFixedWidth(bar_w)
            if self.lab_slider_column.maximumWidth() != column_w:
                self.lab_slider_column.setFixedWidth(column_w)

            column_h = self.lab_square.height()
            # The band is pushed to the bar itself, NOT set as layout margins:
            # margins count towards the column's minimum height, which feeds
            # stack.minimumSizeHint() → the window's content-height policy →
            # a taller pane → a taller bottom margin … a loop that inflated
            # the window and left a huge blank strip under the plane.
            min_bar_h = max(24, int(round(40 * scale)))
            top = max(0.0, float(plane_y))
            bottom = float(plane_y + plane_size)
            # The legacy layout floats the mode buttons over the pane's
            # bottom-right corner — directly under this bar. End the bar one
            # gap above them instead of running flush into them. Ringless
            # mode parks those buttons in their own control bar, which the
            # LAB layout margin already keeps out of this row.
            ringless = getattr(self.pane_lab, "_ringless_layout", None)
            if not (ringless is not None and ringless.controls_enabled):
                btn_size = int(getattr(self.pane_lab, "_btn_size", 0) or round(28 * scale))
                btn_margin = int(getattr(self.pane_lab, "_btn_margin", 0) or round(6 * scale))
                bottom = min(bottom, float(column_h - btn_size - btn_margin - gap))
            height = max(float(min_bar_h), bottom - top)
            if top + height > column_h:
                top = max(0.0, column_h - height)
            if column_layout is not None:
                column_layout.setContentsMargins(side, 0, column_w - bar_w - side, 0)
            self.lab_slider.set_track_band(top, height)
        finally:
            self._syncing_lab_bar = False

    def _theme_presets(self):
        """(slider theme, border theme) resolved from the current config.

        "auto" borderStyle follows the slider theme's `pairs_with` field, so the
        two presets always match unless the user pins one explicitly.
        """
        slider_style = self.cfg.get("sliderStyle", "default")
        return (
            get_slider_theme(slider_style),
            get_border_theme(resolve_border_theme_key(
                self.cfg.get("borderStyle", "auto"), slider_style)),
        )

    def _device_ratio(self):
        ratio = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        return 1.0 if ratio < 0.1 else ratio

    def apply_theme(self, scale=None, is_resize_event=False):
        """Full pass: geometry, then chrome.

        Kept as the entry point for settings/theme changes. The resize path
        calls :meth:`apply_layout` alone — see the note there.
        """
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0
        self.apply_layout(scale)
        self.apply_style(scale)
        if not is_resize_event:
            self._adjust_content_height()

    def apply_layout(self, scale=None):
        """Geometry only: bands, margins, widget sizes, floating placement.

        Split out of apply_theme so dragging the window stops rebuilding the
        chrome — every stylesheet in the window — on each of the ~60 resize
        events per second. Nothing the style half produces depends on the
        window size, only on the config and the device ratio.
        """
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0
        slider_theme, border_theme = self._theme_presets()

        # Vertical lightness bar: visibility + the whole LAB-pane composition
        # (plane size/placement and the bar's own band) — see
        # _sync_lab_lightness_bar.
        self._sync_lab_lightness_bar(scale)

        self.update_mode_buttons_visibility()

        # Update layouts margins & spacing
        # Get screen device pixel ratio to keep the physical size exactly 28px on High-DPI screens.
        # Only adjust title bar height on non-resize-event calls (init / settings change)
        # to avoid DPI-triggered layout cascades when dragging between monitors.
        ratio = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if ratio < 0.1:
            ratio = 1.0
            
        # The title bar is part of the window frame (while it is visible the
        # top border line is suppressed and this band takes over), so the
        # border theme sizes it and owns its bottom divider. Both values are
        # target pixels divided by the device ratio (physical size stays put),
        # rounded rather than truncated so 36px does not land on 35.
        tb_height_px = max(1, int(cast(int, border_theme.get("title_bar_height", 28))))
        tb_button_px = max(1, int(cast(int, border_theme.get("title_bar_button_size", 18))))
        tb_height = max(12, round(tb_height_px / ratio))
        # Buttons keep their own size (a tall PS-style header still has small
        # controls) but may never outgrow the band they sit in.
        title_btn_size = max(8, round(tb_button_px / ratio))
        title_btn_size = min(title_btn_size, max(8, tb_height - 4))
        tb_margin = max(2, int(6 / ratio))
        tb_spacing = max(2, int(6 / ratio))
        
        self.title_bar.setFixedHeight(tb_height)
        tb_layout = self.title_bar.layout()
        if tb_layout is not None:
            tb_layout.setContentsMargins(tb_margin, 0, tb_margin, 0)
            tb_layout.setSpacing(tb_spacing)
            
        self.title_bar.btn_settings.setFixedSize(title_btn_size, title_btn_size)
        self.title_bar.btn_min.setFixedSize(title_btn_size, title_btn_size)
        self.title_bar.btn_close.setFixedSize(title_btn_size, title_btn_size)
        panels_btn = getattr(self.title_bar, "btn_panels", None)
        if panels_btn is not None:
            panels_btn.setFixedSize(title_btn_size, title_btn_size)
        
        
        # Side/bottom margins match the window border width from the border
        # theme (4px by default); the top border needs its own margin when the
        # title bar is hidden. Snapped to whole device pixels — see
        # ui/window_layout.snap_border_width for why a 3px frame on a 1.5x
        # screen paints a stray light line down one side only.
        win_bw = snap_border_width(
            max(0, int(cast(int, border_theme.get("window_border_width", 4)))),
            self._device_ratio())
        # title_bar_inset = the frame wraps above the title bar too; otherwise
        # the bar runs flush to the top edge (and only a hidden bar gets a
        # top margin, as before).
        title_inset = bool(border_theme.get("title_bar_inset", False))
        needs_top_frame = title_inset or not self.title_bar.isVisible()
        self.main_layout.setContentsMargins(
            win_bw, win_bw if needs_top_frame else 0, win_bw, win_bw
        )
        spacing = int(4 * scale)
        self.main_layout.setSpacing(spacing)
        
        # Get Same-space and Diff-space spacing values from configuration
        same_space = self.cfg.get("sliderSameSpace", 6)
        diff_space = self.cfg.get("sliderDiffSpace", 8)
        
        self.sliders_layout.setSpacing(int(diff_space * scale))
        host = getattr(self, "panel_host", None)
        if host is not None:
            host.set_stack_spacing(int(diff_space * scale))
        self.sliders_layout.setContentsMargins(
            int(4 * scale), # closer to edge
            int(self.cfg.get("panelTopGap", 6) * scale),
            int(4 * scale), # closer to edge
            int(10 * scale)
        )
        
        # Update spacing within each color space block
        for group in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            if hasattr(self, "slider_containers") and group in self.slider_containers:
                container = self.slider_containers[group]
                lay = container.layout()
                if lay is not None:
                    lay.setSpacing(int(same_space * scale))
                    # Park spare height at the bottom of the block. Without
                    # this, a block given more room than it needs — which is
                    # exactly what happens when its window is resized —
                    # spreads that room *between* the sliders, so dragging a
                    # floating window taller silently changes the spacing the
                    # user set in the settings.
                    last = lay.itemAt(lay.count() - 1) if lay.count() else None
                    if last is None or last.spacerItem() is None:
                        lay.addStretch(1)
        
        # Row spacing (字母 / 滑条 / 数值 之间) comes from the slider theme:
        # PS leaves a visible gap after the letter, SAI/CSP sit tighter.
        row_spacing = float(cast(float, slider_theme.get("row_spacing", 1)))
        row_spacing_px = max(0, int(row_spacing * scale))
        for row in getattr(self, "slider_row_layouts", []):
            row.setSpacing(row_spacing_px)
            
        # Adjust label fixed widths (theme-aware)
        ch_w_factor = float(cast(float, slider_theme["channel_label_width_factor"]))
        for chan, label in getattr(self, "slider_labels", {}).items():
            label.setFixedWidth(max(8, int(12 * scale * ch_w_factor)))

        self._place_floating_chrome(scale)

        # A DPI change arrives as a plain resize, and the chrome carries
        # ratio-scaled fonts, so refresh it once when the ratio really moves.
        if ratio != getattr(self, "_style_ratio", None):
            self._style_ratio = ratio
            self.apply_style(scale)

    def apply_style(self, scale=None):
        """Chrome only: palette, borders, stylesheets, fonts.

        None of this depends on the window size, which is why the resize path
        can skip it entirely.
        """
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0
        slider_theme, border_theme = self._theme_presets()
        ratio = self._device_ratio()
        win_bw = snap_border_width(
            max(0, int(cast(int, border_theme.get("window_border_width", 4)))),
            ratio)
        needs_top_frame = (bool(border_theme.get("title_bar_inset", False))
                           or not self.title_bar.isVisible())

        theme_name = self.cfg.get("ui-theme", "auto")
        if theme_name == "auto":
            try:
                from core.csp_brush_link import get_csp_theme
                t = get_csp_theme()
                bg = t["bg"]
                text = t["text"]
                border_color = t["border"].split(" ")[-1] if "solid" in t["border"] else t["border"]
                barBg = border_color
            except Exception:
                bg, text, border_color = "#b2b2b2", "#222222", "#787878"
                barBg = border_color
        elif theme_name == "eyedropper":
            bar_stored = self.cfg.get("uiThemeDropperColorBar", "#787878")
            bg_stored = self.cfg.get("uiThemeDropperColorBg", "#b2b2b2")
            try:
                c_bar = QColor(bar_stored)
                bg = QColor(bg_stored).name()
                barBg = c_bar.name()
                border_color = c_bar.name()
                text = "#ffffff" if QColor(bg).lightness() < 128 else "#222222"
            except Exception:
                bg = barBg = border_color = "#787878"
                text = "#222222"
        else:
            themes = {
                "black": {"bg": "#1e1e1e", "text": "#ffffff", "border": "#2d2d2d"},
                "white": {"bg": "#ffffff", "text": "#222222", "border": "#b2b2b2"},
                "gray": {"bg": "#b2b2b2", "text": "#222222", "border": "#787878"}
            }
            t = themes.get(theme_name, themes["gray"])
            bg = t["bg"]
            text = t["text"]
            border_color = t["border"]
            barBg = border_color
            
        # Determine label text color based on background/text lightness to avoid low contrast
        is_dark_text = QColor(text).lightness() < 128
        channel_text_color = "#666666" if is_dark_text else "#e9e9e9"
        inputBg = "#eaeaea" if is_dark_text else "#2e2e2e"
        borderColor = "#d0d0d0" if is_dark_text else "#555555"
        handle_border_global = "#999999" if is_dark_text else "#b0b0b0"

        # Border theme with every "auto" colour resolved against the UI theme.
        border = resolve_border_theme(
            border_theme,
            chrome_border=border_color,
            input_bg=inputBg,
            input_border=borderColor,
            text=text,
        )
        win_radius = border["window_border_radius"]
        win_border_color = border["window_border_color"]

        # Chrome alpha (see ui/chrome_opacity.py): the window background, the
        # frame drawn around it and the title band that continues that frame
        # all fade together, so the panel stays one object at any opacity.
        # Everything that carries *colour information* — sliders, value boxes,
        # swatches, wheel, LAB plane — is left opaque on purpose.
        chrome_opacity = resolve_chrome_opacity(self.cfg)
        chrome_bg = with_opacity(bg, chrome_opacity)
        chrome_bar_bg = with_opacity(barBg, chrome_opacity)
        chrome_win_border = with_opacity(win_border_color, chrome_opacity)
        chrome_divider = with_opacity(border["title_bar_divider_color"], chrome_opacity)
        value_border_css = (
            f"{border['value_box_border_width']}px solid {border['value_box_border']}"
            if border["value_box"] and border["value_box_border_width"] > 0
            else "none"
        )
        value_bg_css = border["value_box_bg"] if border["value_box"] else "transparent"
        value_text_css = border["value_box_text"]

        if hasattr(self, "preview_box"):

            self.preview_box.set_theme_colors(

                RINGLESS_ACTIVE_BORDER, borderColor

            )

        
        # Determine title bar text color and button hover backgrounds
        title_text_color = "#666666" if is_dark_text else "#a0a0a0"
        hover_bg = "rgba(0,0,0,0.08)" if is_dark_text else "rgba(255,255,255,0.12)"

        font_factor = (self.cfg.get("fontSize", 100) / 100.0) * scale
        lbl_font_size = int(11 * font_factor)
        val_font_size = int(10 * font_factor)
        title_font_size = int(8 * font_factor)
        
        # Calculate scaled font sizes using device pixel ratio
        fs_settings = max(6, int(14 * font_factor / ratio))
        fs_title = max(6, int(11 * font_factor / ratio))
        fs_min = max(5, int(10 * font_factor / ratio))
        fs_close = max(6, int(14 * font_factor / ratio))

        _set_css(self.title_bar.btn_settings, f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_settings}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        grips_btn = getattr(self.title_bar, "btn_panels", None)
        if grips_btn is not None:
            _set_css(grips_btn, f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_settings}px; }} QPushButton:checked {{ background-color: {hover_bg}; border-radius: 2px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        _set_css(self.title_bar.title_label, f"font-weight: bold; color: {title_text_color}; font-size: {fs_title}px;")
        _set_css(self.title_bar.btn_min, f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_min}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        _set_css(self.title_bar.btn_close, f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_close}px; }} QPushButton:hover {{ background-color: #ff5050; color: white; border-radius: 2px; }}")

        win_border_css = (
            f"{win_bw}px solid {chrome_win_border}" if win_bw > 0 else "none"
        )
        top_border = win_border_css if needs_top_frame else "none"
        tb_divider_w = int(border["title_bar_divider_width"])
        title_divider_css = (
            f"{tb_divider_w}px solid {chrome_divider}"
            if tb_divider_w > 0
            else "none"
        )
        # Panels wear the window's looks whether they sit in the column or
        # in a window of their own — same band, same border, same scale.
        # Remembered as well, so a panel torn off later is themed on arrival
        # instead of waiting for the next full pass.
        from ui.panels.floating import PanelChrome

        self._floating_chrome = PanelChrome(
            background=chrome_bg, border_color=chrome_win_border,
            border_width=win_bw, radius=int(win_radius), text=text,
            bar_bg=barBg, bar_text=title_text_color,
            divider_color=border["title_bar_divider_color"],
            divider_width=tb_divider_w,
            scale=scale, font_size=fs_title, opacity=chrome_opacity / 100.0,
            title_inset=bool(border_theme.get("title_bar_inset", False)),
            grip_gap=max(2, int(4 * scale)),
            content_margins=(int(4 * scale), int(6 * scale),
                             int(4 * scale), int(6 * scale)),
            top_gap=max(0, int(self.cfg.get("panelTopGap", 6) * scale)))
        host = getattr(self, "panel_host", None)
        if host is not None:
            host.apply_chrome(self._floating_chrome)
        floating = getattr(self, "floating_windows", None)
        if callable(floating):
            for window in floating().values():
                window.apply_chrome(self._floating_chrome)

        _set_css(self, f"""
            QWidget#CentralWidget {{
                background-color: {chrome_bg};
                border-left: {win_border_css};
                border-right: {win_border_css};
                border-bottom: {win_border_css};
                border-top: {top_border};
                border-radius: {win_radius}px;
            }}
            TitleBar {{
                background-color: {chrome_bar_bg};
                color: {title_text_color};
                border-bottom: {title_divider_css};
            }}
            TitleBar QLabel {{
                color: {title_text_color};
                font-size: {fs_title}px;
                font-weight: bold;
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            }}
            TitleBar QPushButton {{
                color: {title_text_color};
                font-size: {fs_settings}px;
            }}
            TitleBar QPushButton:hover {{
                background-color: {hover_bg};
                border-radius: 2px;
            }}
            QLabel {{
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {lbl_font_size}px;
            }}
            QLabel#ChannelLabel {{
                color: {channel_text_color};
                font-weight: {slider_theme["channel_label_weight"]};
                font-size: {lbl_font_size}px;
            }}
            QLabel#ValueLabel {{
                background-color: {value_bg_css};
                border: {value_border_css};
                border-radius: {int(2 * scale)}px;
                padding: 1px 3px;
                color: {value_text_css};
                font-size: {val_font_size}px;
            }}
            QSlider::groove:horizontal {{
                height: {int(6 * scale)}px;
                background: transparent;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                border: 1px solid {handle_border_global};
                width: {int(6 * scale)}px;
                height: {int(14 * scale)}px;
                margin-top: {-int(4 * scale)}px;
                margin-bottom: {-int(4 * scale)}px;
                border-radius: {int(3 * scale)}px;
            }}
            QSlider::handle:horizontal:hover {{
                background: #eaeaea;
                border-color: #5a94e2;
            }}
        """)
        
        # Style value labels directly for robust rendering (theme-aware)
        val_w_factor = float(cast(float, slider_theme["value_label_width_factor"]))
        val_radius = max(0, int(3 * scale * float(cast(float, slider_theme["value_label_radius_factor"]))))
        val_padding = slider_theme["value_label_padding"]
        val_align = {
            "right": Qt.AlignmentFlag.AlignRight,
            "center": Qt.AlignmentFlag.AlignHCenter,
        }.get(str(slider_theme.get("value_label_align", "left")), Qt.AlignmentFlag.AlignLeft)
        val_align |= Qt.AlignmentFlag.AlignVCenter
        for chan, (slider, val_label) in self.slider_widgets.items():
            val_label.setFixedWidth(max(24, int(27 * val_w_factor)))
            val_label.setAlignment(val_align)
            _set_css(val_label, f"""
                background-color: {value_bg_css};
                border: {value_border_css};
                border-radius: {val_radius}px;
                color: {value_text_css};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {val_font_size}px;
                padding: {val_padding};
            """)
            
        # Scale GradientSliders (slider + border theme aware)
        for chan, (slider, val_label) in self.slider_widgets.items():
            if isinstance(slider, GradientSlider):
                slider.update_scale(scale, slider_theme, border)

        # Per-group frames from the border theme:
        #   none → nothing (default / PS)
        #   line → a divider under each color-space block (SAI)
        #   box  → a full outline around each block (CSP)
        # The layout margins are widened by hand because Qt does not reserve
        # space for a stylesheet border on a plain QWidget.
        frame_mode = str(border["group_frame"])
        gf_w = int(border["group_frame_width"])
        gf_color = border["group_frame_color"]
        gf_radius = int(border["group_frame_radius"])
        gf_pad = max(0, int(border["group_frame_padding"] * scale))
        # A divider belongs *between* groups, so the last shown one is skipped
        # (display order and module filtering come from the same config helper
        # the layout uses, and isVisibleTo() answers correctly before the
        # window itself has been shown).
        last_line_group = None
        if frame_mode == "line":
            shown = []
            for group in config.sorted_slider_groups(self.cfg):
                if group == "History":
                    continue
                candidate = getattr(self, "slider_containers", {}).get(group)
                if candidate is None:
                    continue
                parent = candidate.parentWidget()
                visible = (
                    candidate.isVisibleTo(parent) if parent is not None
                    else candidate.isVisible()
                )
                if visible:
                    shown.append(group)
            last_line_group = shown[-1] if shown else None
        for group in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            container = getattr(self, "slider_containers", {}).get(group)
            if container is None:
                continue
            container.setObjectName("SliderGroupFrame")
            lay = container.layout()
            if frame_mode == "box" and gf_w > 0:
                container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                _set_css(container,
                    f"QWidget#SliderGroupFrame {{ background: transparent;"
                    f" border: {gf_w}px solid {gf_color};"
                    f" border-radius: {gf_radius}px; }}"
                )
                if lay is not None:
                    inset = gf_w + gf_pad
                    lay.setContentsMargins(inset, inset, inset, inset)
            elif frame_mode == "line" and gf_w > 0 and group != last_line_group:
                container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                _set_css(container,
                    f"QWidget#SliderGroupFrame {{ background: transparent;"
                    f" border: none; border-bottom: {gf_w}px solid {gf_color}; }}"
                )
                if lay is not None:
                    lay.setContentsMargins(0, 0, 0, gf_w + gf_pad)
            else:
                container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
                _set_css(container, "")
                if lay is not None:
                    lay.setContentsMargins(0, 0, 0, 0)
            
        # Style mode buttons dynamically (all floating picker toggle buttons,
        # including the LAB shape/harmony toggles, share one chrome).
        btn_w = int(28 * scale)
        btn_h = int(28 * scale)
        mode_buttons = [
            self.btn_mode_wheel,
            self.btn_mode_lab,
            getattr(self, 'btn_module', None),
            getattr(self, 'btn_lab_shape', None),
            getattr(self, 'btn_lab_harmony', None),
        ]
        for btn in mode_buttons:
            if btn is not None:
                btn.setFixedSize(btn_w, btn_h)
                _set_css(btn, f"""
                    QPushButton {{
                        background-color: {barBg};
                        border: 1px solid {borderColor};
                        border-radius: {int(4 * scale)}px;
                        color: {text};
                        font-size: {int(13 * scale)}px;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background-color: {bg};
                        border-color: #5a94e2;
                    }}
                """)

        # Keep the LAB harmony menu on the same chrome as the mode buttons.
        harmony_menu = getattr(self, "_lab_harmony_menu", None)
        if harmony_menu is not None:
            _set_css(harmony_menu, f"""
                QMenu {{
                    background-color: {barBg};
                    border: 1px solid {borderColor};
                    color: {text};
                }}
                QMenu::item {{
                    padding: 4px 12px;
                }}
                QMenu::item:selected {{
                    background-color: #5a94e2;
                    color: #ffffff;
                }}
            """)

        # Theme + geometry for the color history panel. It uses the same
        # bg / border / text as the main chrome so it visually belongs to
        # whichever theme is active (auto / gray / white / black). Cell
        # size auto-fits the widget width (set in _relayout), so we only
        # need to push cols/rows here.
        if hasattr(self, "color_history"):
            self.color_history.configure(
                self.cfg.get("historyColumns", 8),
                self.cfg.get("historyRows", 2),
            )
            # The panel fills its body with the same colour the window
            # background already carries — a no-op while the chrome is
            # opaque. Repainting it over a translucent background would
            # stack a second layer of alpha and leave a denser rectangle
            # in the middle of the panel, so the body is dropped entirely
            # once the chrome goes translucent; its 1px outline still
            # delimits the grid.
            history_bg = QColor(bg)
            if chrome_opacity < CHROME_OPACITY_MAX:
                history_bg.setAlpha(0)
            self.color_history.apply_theme(history_bg, border_color, text)

    def _place_floating_chrome(self, scale):
        """Place the floating swatch cluster and keep the sidebar on top."""
        if not (hasattr(self, 'preview_box') and hasattr(self, 'sliders_container')
                and hasattr(self, 'title_bar')):
            return
        title_offset = _title_bar_content_offset(self.title_bar, self.main_layout)
        sliders_h = self.sliders_container.sizeHint().height()
        h = self.height()
        place = getattr(self, "_place_preview_box", None)
        resolve = getattr(self, "window_layout", None)
        if callable(place) and callable(resolve):
            # Literally the same layout object the resize pass uses, so the
            # two paths cannot drift apart again (they once trimmed 4px vs
            # 6px, and the cluster changed size depending on which ran last).
            place(resolve(scale), title_offset, h, sliders_h)
        else:
            self.preview_box.resize_and_position(
                int(window_layout.wheel_size_for(self.width(), h, scale)),
                title_offset, h, sliders_h, self.active_slot)
        self.preview_box.raise_()

        # If settings sidebar is open, ensure it remains on top!
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar.raise_()
