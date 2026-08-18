"""Theme application for the main window.

Extracted from ``ui.main_window``: applies the resolved UI theme, slider
visual theme and per-widget geometry/stylesheet updates.
"""

from typing import cast

from PyQt6.QtGui import QColor

from ui.ringless_mode import RINGLESS_ACTIVE_BORDER
from ui.slider_themes import get_slider_theme
from ui.widgets import (
    GradientSlider,
    _title_bar_content_offset,
    _visible_title_bar_height,
)


class ThemeMixin:

    def apply_theme(self, scale=None, is_resize_event=False):
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0

        # Resolve slider theme (visual preset for slider track/handle/labels).
        # Falls back to "default" if the key is missing or unknown.
        slider_theme = get_slider_theme(self.cfg.get("sliderStyle", "default"))

        # Dynamically toggle vertical lightness slider visibility based on configuration
        show_lab_slider = self.cfg.get("showLabLightnessSlider", True)
        if hasattr(self, 'lab_slider_column'):
            self.lab_slider_column.setVisible(show_lab_slider)
            # Adjust margins to align with switcher button and prevent overlap
            layout = self.lab_slider_column.layout()
            if layout is not None:
                layout.setContentsMargins(int(9 * scale), int(8 * scale), int(9 * scale), int(34 * scale))

        self.update_mode_buttons_visibility()

        # Update layouts margins & spacing
        # Get screen device pixel ratio to keep the physical size exactly 28px on High-DPI screens.
        # Only adjust title bar height on non-resize-event calls (init / settings change)
        # to avoid DPI-triggered layout cascades when dragging between monitors.
        ratio = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if ratio < 0.1:
            ratio = 1.0
            
        tb_height = max(12, int(28 / ratio))
        title_btn_size = max(8, int(18 / ratio))
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
        
        
        # Fixed 4px side/bottom margins; the top border needs its own margin
        # when the title bar is hidden.
        self.main_layout.setContentsMargins(
            4, 0 if self.title_bar.isVisible() else 4, 4, 4
        )
        spacing = int(4 * scale)
        self.main_layout.setSpacing(spacing)
        
        # Get Same-space and Diff-space spacing values from configuration
        same_space = self.cfg.get("sliderSameSpace", 6)
        diff_space = self.cfg.get("sliderDiffSpace", 8)
        
        self.sliders_layout.setSpacing(int(diff_space * scale))
        self.sliders_layout.setContentsMargins(
            int(4 * scale), # closer to edge
            int(6 * scale),
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
        
        # Adjust row spacings closer to text
        for row in getattr(self, "slider_row_layouts", []):
            row.setSpacing(max(1, int(1 * scale))) # Keep the slider close to its channel label
            
        # Adjust label fixed widths (theme-aware)
        ch_w_factor = float(cast(float, slider_theme["channel_label_width_factor"]))
        for chan, label in getattr(self, "slider_labels", {}).items():
            label.setFixedWidth(max(8, int(12 * scale * ch_w_factor)))

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

        self.title_bar.btn_settings.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_settings}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        self.title_bar.title_label.setStyleSheet(f"font-weight: bold; color: {title_text_color}; font-size: {fs_title}px;")
        self.title_bar.btn_min.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_min}px; }} QPushButton:hover {{ background-color: {hover_bg}; border-radius: 2px; }}")
        self.title_bar.btn_close.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {title_text_color}; font-size: {fs_close}px; }} QPushButton:hover {{ background-color: #ff5050; color: white; border-radius: 2px; }}")

        top_border = "none" if self.title_bar.isVisible() else f"4px solid {border_color}"
        self.setStyleSheet(f"""
            QWidget#CentralWidget {{
                background-color: {bg};
                border-left: 4px solid {border_color};
                border-right: 4px solid {border_color};
                border-bottom: 4px solid {border_color};
                border-top: {top_border};
                border-radius: 0px;
            }}
            TitleBar {{
                background-color: {barBg};
                color: {title_text_color};
                border-bottom: none;
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
                background-color: {inputBg};
                border: 1px solid {borderColor};
                border-radius: {int(2 * scale)}px;
                padding: 1px 3px;
                color: {text};
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
        for chan, (slider, val_label) in self.slider_widgets.items():
            val_label.setFixedWidth(max(24, int(27 * val_w_factor)))
            val_label.setStyleSheet(f"""
                background-color: {inputBg};
                border: 1px solid {borderColor};
                border-radius: {val_radius}px;
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {val_font_size}px;
                padding: {val_padding};
            """)
            
        # Scale GradientSliders (theme-aware)
        for chan, (slider, val_label) in self.slider_widgets.items():
            if isinstance(slider, GradientSlider):
                slider.update_scale(scale, slider_theme)
            
        # Style mode buttons dynamically
        btn_w = int(28 * scale)
        btn_h = int(28 * scale)
        for btn in [self.btn_mode_wheel, self.btn_mode_lab,
                     getattr(self, 'btn_module', None)]:
            if btn is not None:
                btn.setFixedSize(btn_w, btn_h)
                btn.setStyleSheet(f"""
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
            self.color_history.apply_theme(bg, border_color, text)

        # Reposition the color preview box immediately when applying theme/settings
        if hasattr(self, 'preview_box') and hasattr(self, 'sliders_container') and hasattr(self, 'title_bar'):
            title_h = _visible_title_bar_height(self.title_bar)
            title_offset = _title_bar_content_offset(self.title_bar, self.main_layout)
            sliders_h = self.sliders_container.sizeHint().height()
            w = self.width()
            h = self.height()
            spacing = int(4 * scale)
            margins = self.main_layout.contentsMargins()
            wheel_size = min(
                w - margins.left() - margins.right(),
                h - margins.top() - margins.bottom() - title_h - sliders_h - 2 * spacing,
            ) - 4
            self.preview_box.resize_and_position(wheel_size, title_offset, h, sliders_h, self.active_slot)
            self.preview_box.raise_()
            
            # If settings sidebar is open, ensure it remains on top!
            if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
                self.settings_sidebar.raise_()

        if not is_resize_event:
            self._adjust_content_height()
