"""Appearance, theme and eyedropper controls for the settings sidebar.

Extracted from ``ui.settings_sidebar``: interface/picker appearance controls,
theme resolution and the dual-point eyedropper workflow.
"""

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from core import i18n
from ui.lab_harmony import HARMONY_MODE_NAMES
from ui.ringless_settings import RinglessSettingsWidget
from ui.settings.settings_helpers import (
    _ARROW_DOWN_DARK,
    _ARROW_DOWN_LIGHT,
    _CHECKBOX_CHECK_ICON,
    NonScrollComboBox,
    NonScrollSlider,
)
from ui.slider_themes import list_slider_theme_names


class AppearancePanelMixin:

    def _make_eyedropper_row(self, target, label_text, tooltip):
        """Create a single eyedropper control row (target = 'bar' or 'bg')."""
        widget = QWidget()
        widget.setObjectName(f"EyedropperRow_{target}")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(QLabel(label_text))

        lbl = QLabel(i18n.tr("未设定"))
        self._set_label_state(lbl, "muted")
        row.addWidget(lbl)

        btn_set = QPushButton(i18n.tr("设定"))
        btn_set.setToolTip(tooltip + " — " + i18n.tr("点击后窗口隐藏3秒，移鼠标到目标位置"))
        btn_set.clicked.connect(lambda: self.start_eyedropper_pick(target))
        btn_sync = QPushButton(i18n.tr("同步"))
        btn_sync.setToolTip(i18n.tr("从已设定的取色点立即同步颜色"))
        btn_sync.clicked.connect(lambda: self.do_eyedropper_sync(target))
        row.addWidget(btn_set)
        row.addWidget(btn_sync)

        # Add to the background card if it exists, otherwise use page layout directly
        card_layout = getattr(self, "_card_layout_interface_bg", None)
        if card_layout is not None:
            card_layout.addWidget(widget)
        else:
            self._page_layouts["界面"].addWidget(widget)
        widget.setVisible(False)

        setattr(self, f"_eye_row_{target}", widget)
        setattr(self, f"_eye_lbl_{target}", lbl)
        setattr(self, f"_eye_btn_set_{target}", btn_set)
        setattr(self, f"_eye_btn_sync_{target}", btn_sync)

    def _update_grayscale_mode_options(self, backend):
        """Mag only offers Luma; native supports both OKLCh and Luma."""
        self.combo_grayscale_mode.blockSignals(True)
        self.combo_grayscale_mode.clear()
        if backend == "mag":
            self.combo_grayscale_mode.addItem(i18n.tr("Luma (BT.709 标准)"), "luma")
        else:
            self.combo_grayscale_mode.addItem(i18n.tr("OKLCh (感知均匀)"), "oklch")
            self.combo_grayscale_mode.addItem(i18n.tr("Luma (BT.709 标准)"), "luma")
            saved_mode = self.cfg.get("grayscaleFilterMode", "oklch")
            idx = self.combo_grayscale_mode.findData(saved_mode)
            self.combo_grayscale_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_grayscale_mode.setEnabled(backend != "mag")
        self.combo_grayscale_mode.blockSignals(False)

    def _update_grayscale_screen_options(self, backend):
        """Native can target one screen; Mag is always system-wide."""
        if backend == "mag":
            screens = ["all"]
        else:
            screens = self._grayscale_screen_items()
        saved_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.combo_grayscale_screen.blockSignals(True)
        self.combo_grayscale_screen.clear()
        self.combo_grayscale_screen.addItems(screens)
        if saved_target == "all":
            self.combo_grayscale_screen.setCurrentText("all")
        else:
            for item in screens:
                if item != "all" and item.startswith(f"{saved_target}:"):
                    self.combo_grayscale_screen.setCurrentText(item)
                    break
            else:
                self.combo_grayscale_screen.setCurrentText("all")
        if backend == "mag":
            self.combo_grayscale_screen.setEnabled(False)
            self.combo_grayscale_screen.setToolTip(
                i18n.tr("系统 Luma (Mag) 作用于全部屏幕")
            )
        else:
            self.combo_grayscale_screen.setEnabled(True)
            self.combo_grayscale_screen.setToolTip(
                i18n.tr("选择灰度滤镜作用在哪个屏幕，默认作用于全部屏幕")
            )
        self.combo_grayscale_screen.blockSignals(False)

    @staticmethod
    def _grayscale_screen_items() -> list[str]:
        """Return the same screen labels the native runtime uses."""
        items = ["all"]
        app = QApplication.instance()
        if app is not None:
            try:
                for i, screen in enumerate(app.screens()):
                    geo = screen.geometry()
                    dpr = screen.devicePixelRatio()
                    name = screen.name().replace("\\\\.\\", "")
                    pw = int(geo.width() * dpr)
                    ph = int(geo.height() * dpr)
                    items.append(f"{i}: {name} ({pw}x{ph})")
            except Exception:
                pass
        return items

    def _on_grayscale_backend_changed(self, text):
        backend = "mag" if "Mag" in text else "native"
        self._update_grayscale_mode_options(backend)
        self._update_grayscale_screen_options(backend)
        self.save_settings()

    def theme_colors(self):
        """Resolve active theme colors dynamically based on parent window.

        Returns dict with keys: bg, text, border, bar_bg, plus derived
        semantic tokens: accent, muted, success, warning, danger. The
        semantic tokens are computed from the theme's text/bg contrast so
        status labels stay legible in both light and dark chrome.
        """
        bg, text, border_color, barBg = "#b2b2b2", "#222222", "#787878", "#787878"
        if hasattr(self, "_parent") and self._parent is not None:
            p = self._parent
            theme_name = p.cfg.get("ui-theme", "auto")
            if theme_name == "auto":
                try:
                    from core.csp_brush_link import get_csp_theme
                    t = get_csp_theme()
                    bg = t["bg"]
                    text = t["text"]
                    border_color = t["border"].split(" ")[-1] if "solid" in t["border"] else t["border"]
                    barBg = border_color
                except Exception:
                    pass
            elif theme_name == "eyedropper":
                bar_stored = p.cfg.get("uiThemeDropperColorBar", "#787878")
                bg_stored = p.cfg.get("uiThemeDropperColorBg", "#b2b2b2")
                try:
                    c_bar = QColor(bar_stored)
                    bg = QColor(bg_stored).name()
                    barBg = c_bar.name()
                    border_color = c_bar.name()
                    text = "#ffffff" if QColor(bg).lightness() < 128 else "#222222"
                except Exception:
                    pass
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

        is_dark_text = QColor(text).lightness() < 128
        # Muted = primary text at ~45% alpha (de-emphasized / disabled-like)
        tc = QColor(text)
        muted = f"rgba({tc.red()},{tc.green()},{tc.blue()},0.45)"
        # Status colors chosen for adequate contrast on both light & dark chrome
        if is_dark_text:  # light chrome → darker status colors
            success, warning, danger = "#2e7d32", "#b26a00", "#c62828"
        else:             # dark chrome → lighter status colors
            success, warning, danger = "#4caf50", "#ffb74d", "#ef5350"

        return {"bg": bg, "text": text, "border": border_color, "bar_bg": barBg,
                "accent": "#5a94e2", "muted": muted,
                "success": success, "warning": warning, "danger": danger}

    def apply_theme(self):
        font_factor = self.cfg.get("fontSize", 100) / 100.0
        font_size = int(11 * font_factor)
        header_font_size = int(12 * font_factor)

        c = self.theme_colors()
        bg = c["bg"]
        text = c["text"]
        barBg = c["bar_bg"]
        accent = c["accent"]
        muted = c["muted"]
        success = c["success"]
        warning = c["warning"]
        danger = c["danger"]

        is_dark_text = QColor(text).lightness() < 128
        borderColor = "#d0d0d0" if is_dark_text else "#555555"

        # Srgb components for semi-transparent derivations
        tc = QColor(text)
        text_r, text_g, text_b = tc.red(), tc.green(), tc.blue()

        if is_dark_text:
            hover_bg = "rgba(0,0,0,0.06)"
            pressed_bg = "rgba(0,0,0,0.10)"
            disabled_color = f"rgba({text_r},{text_g},{text_b},0.40)"
            scroll_handle = f"rgba({text_r},{text_g},{text_b},0.25)"
            scroll_handle_hover = f"rgba({text_r},{text_g},{text_b},0.45)"
        else:
            hover_bg = "rgba(255,255,255,0.08)"
            pressed_bg = "rgba(255,255,255,0.04)"
            disabled_color = f"rgba({text_r},{text_g},{text_b},0.30)"
            scroll_handle = f"rgba({text_r},{text_g},{text_b},0.20)"
            scroll_handle_hover = f"rgba({text_r},{text_g},{text_b},0.35)"

        # ── Per-widget inline styles ──
        self.lbl_font_size.setStyleSheet(f"""
            border: 1px solid {borderColor};
            background-color: {bg};
            color: {text};
            border-radius: 3px;
            font-size: {font_size}px;
        """)

        # Combo dropdown arrow — theme-aware (dark arrow on light chrome, light on dark)
        arrow_normal = _ARROW_DOWN_DARK if is_dark_text else _ARROW_DOWN_LIGHT
        
        # ── Main stylesheet (single source of truth for the settings UI) ──
        self.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            /* Uncover the tab pane behind scroll viewports */
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QStackedWidget {{
                background-color: transparent;
            }}
            /* Left rail: quiet flat panel with solid selection */
            QListWidget#NavRail {{
                background-color: {barBg};
                border: 1px solid {borderColor};
                border-radius: 3px;
                outline: none;
                padding: 3px;
            }}
            QListWidget#NavRail::item {{
                color: {muted};
                border-radius: 3px;
                padding: 0 8px;
                margin: 1px 0;
            }}
            QListWidget#NavRail::item:hover {{
                background-color: {hover_bg};
                color: {text};
            }}
            QListWidget#NavRail::item:selected {{
                background-color: {accent};
                color: white;
            }}
            /* Flat content sections (no card boxes) */
            QFrame#SettingsCard {{
                background-color: transparent;
                border: none;
            }}
            QWidget {{
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {font_size}px;
            }}
            QLabel {{
                color: {text};
                background: transparent;
            }}
            QLabel#SectionHeader {{
                font-weight: bold;
                font-size: {header_font_size}px;
                margin-top: 2px;
                margin-bottom: 1px;
                color: {text};
            }}
            QLabel#StatusHint {{
                color: {muted};
                background: transparent;
                font-size: {font_size}px;
            }}
            QLabel#StatusSuccess {{
                color: {success};
                background: transparent;
            }}
            QLabel#StatusWarning {{
                color: {warning};
                background: transparent;
            }}
            QLabel#StatusDanger {{
                color: {danger};
                background: transparent;
            }}
            QCheckBox {{
                color: {text};
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {borderColor};
                background-color: {bg};
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent};
                border-color: {accent};
                image: url("{_CHECKBOX_CHECK_ICON}");
            }}
            QCheckBox::indicator:hover {{
                border-color: {accent};
            }}
            QComboBox {{
                background-color: {bg};
                border: 1px solid {borderColor};
                color: {text};
                border-radius: 3px;
                padding: 2px 6px;
                min-height: 22px;
            }}
            QComboBox:hover {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox::down-arrow {{
                image: url("{arrow_normal}");
                width: 12px;
                height: 12px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {bg};
                border: 1px solid {borderColor};
                color: {text};
                selection-background-color: {accent};
                selection-color: white;
                outline: none;
                padding: 2px;
            }}
            QPushButton {{
                background-color: {bg};
                border: 1px solid {borderColor};
                color: {text};
                border-radius: 3px;
                padding: 2px 8px;
                min-height: 22px;
            }}
            QPushButton:hover {{
                border-color: {accent};
                background-color: {hover_bg};
            }}
            QPushButton:pressed {{
                background-color: {pressed_bg};
            }}
            QPushButton:focus {{
                border-color: {accent};
            }}
            QPushButton:disabled {{
                color: {disabled_color};
            }}
            QPushButton#StepButton {{
                padding: 0;
                min-height: 0;
                min-width: 0;
                border-radius: 3px;
                font-size: 12px;
            }}
            QSlider::groove:horizontal {{
                height: 6px;
                background: {bg};
                border: 1px solid {borderColor};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {text};
                border: 1px solid {borderColor};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            QSlider::handle:horizontal:hover {{
                border-color: {accent};
            }}
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                border: none;
                background: transparent;
                height: 8px;
                margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background: {scroll_handle};
                border-radius: 4px;
                min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {scroll_handle_hover};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
        """)
        self._refresh_nav_icons()

    def font_decrease(self):
        val = int(self.lbl_font_size.text().replace("%", ""))
        val = max(50, val - 10)
        self.lbl_font_size.setText(f"{val}%")
        self.cfg["fontSize"] = val
        self._persist_and_emit()

    def font_increase(self):
        val = int(self.lbl_font_size.text().replace("%", ""))
        val = min(150, val + 10)
        self.lbl_font_size.setText(f"{val}%")
        self.cfg["fontSize"] = val
        self._persist_and_emit()

    def zoom_decrease(self):
        val = int(self.lbl_picker_zoom.text().replace("×", ""))
        val = max(2, val - 1)
        self.lbl_picker_zoom.setText(f"{val}×")
        self.cfg["pickerZoom"] = val
        self._persist_and_emit()

    def zoom_increase(self):
        val = int(self.lbl_picker_zoom.text().replace("×", ""))
        val = min(12, val + 1)
        self.lbl_picker_zoom.setText(f"{val}×")
        self.cfg["pickerZoom"] = val
        self._persist_and_emit()

    def on_zoom_slider_changed(self):
        """Update label in real-time, snapped to nearest 5% step.
        Does NOT apply resize — that happens only on slider release."""
        v = self.zoom_slider.value()
        snapped = round(v / 5) * 5
        self.lbl_zoom.setText(f"{snapped}%")

    def on_zoom_slider_released(self):
        """Snap slider to nearest 5%, apply zoom once, then save."""
        v = self.zoom_slider.value()
        snapped = round(v / 5) * 5
        # Snap the slider handle to the aligned value
        if snapped != v:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(snapped)
            self.zoom_slider.blockSignals(False)
        self.lbl_zoom.setText(f"{snapped}%")
        # Apply zoom immediately (heavy op — done once on release, not during drag)
        self._parent.zoom_ui(snapped / 100.0)
        self._parent.current_ui_scale = snapped
        # Persist to config; on_settings_saved will see scale already matches → cheap update()
        self.save_settings()

    def _refresh_theme_status(self):
        if not hasattr(self, "lbl_theme_status"):
            return
        theme = self.cfg.get("ui-theme", "auto")
        if theme == "auto":
            try:
                dark = QColor(self.theme_colors()["bg"]).lightness() < 128
                self.lbl_theme_status.setText(
                    i18n.tr("自动匹配：{dark}主题", dark=(i18n.tr("深色") if dark else i18n.tr("浅色")))
                )
            except Exception:
                self.lbl_theme_status.setText(i18n.tr("自动匹配绘画软件主题"))
        elif theme == "eyedropper":
            self.lbl_theme_status.setText(i18n.tr("取色主题：从屏幕两个位置取色"))
        else:
            names = {"black": i18n.tr("黑"), "white": i18n.tr("白"), "gray": i18n.tr("灰")}
            self.lbl_theme_status.setText(i18n.tr("固定主题：{name}", name=names.get(theme, theme)))

    def start_eyedropper_pick(self, target):
        """Hide palette → 3s countdown → capture cursor for 'bar' or 'bg'."""
        self.pickingThemePoint.emit(True)
        self._eye_target = target
        self._eye_countdown = 3
        btn_set = getattr(self, f"_eye_btn_set_{target}")
        btn_set.setEnabled(False)
        btn_set.setText("3...")
        if self._parent is not None:
            self._parent.hide()
        self._eye_countdown_timer = QTimer(self)
        self._eye_countdown_timer.timeout.connect(self._on_countdown_tick)
        self._eye_countdown_timer.start(1000)

    def _on_countdown_tick(self):
        self._eye_countdown -= 1
        target = self._eye_target
        btn_set = getattr(self, f"_eye_btn_set_{target}")
        if self._eye_countdown > 0:
            btn_set.setText(f"{self._eye_countdown}...")
        else:
            self._eye_countdown_timer.stop()
            btn_set.setText(i18n.tr("设定"))
            btn_set.setEnabled(True)
            if self._parent is not None:
                self._parent.show()
            self.pickingThemePoint.emit(False)
            pos = QCursor.pos()
            self._on_eyedropper_point_picked(pos.x(), pos.y())

    def _on_eyedropper_point_picked(self, x: int, y: int):
        target = self._eye_target
        point_key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
        self.cfg[point_key] = {"x": x, "y": y}
        self._persist_config()
        lbl = getattr(self, f"_eye_lbl_{target}")
        lbl.setText(f"({x}, {y})")
        self._set_label_state(lbl, None)
        self.do_eyedropper_sync(target)

    @staticmethod
    def _grab_pixel_color(x, y):
        """Grab the exact pixel color from screen at logical coords (x, y) via GDI.

        Reads only the single pixel under the cursor (no 3×3 median averaging),
        so the result matches the on-screen color at that point as closely as
        the framebuffer allows.
        """
        import ctypes
        # Convert logical → physical pixels (Qt uses logical, GDI needs physical);
        # round() instead of int() avoids off-by-one drift on fractional DPI.
        screen = QApplication.screenAt(QPoint(x, y))
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        if dpr < 0.1:
            dpr = 1.0
        px, py = round(x * dpr), round(y * dpr)

        hdc = ctypes.windll.gdi32.CreateDCW("DISPLAY", None, None, None)
        try:
            pixel = ctypes.windll.gdi32.GetPixel(hdc, px, py)
        finally:
            ctypes.windll.gdi32.DeleteDC(hdc)
        if pixel == -1:  # CLR_INVALID — GetPixel failed (e.g. off-screen)
            raise OSError("GetPixel failed")
        r = pixel & 0xFF
        g = (pixel >> 8) & 0xFF
        b = (pixel >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"

    def do_eyedropper_sync(self, target):
        """Sync color from the fixed pick point for 'bar' or 'bg'."""
        point_key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
        color_key = "uiThemeDropperColorBar" if target == "bar" else "uiThemeDropperColorBg"
        pt = self.cfg.get(point_key, None)
        if not pt or not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
            return
        try:
            hex_color = self._grab_pixel_color(pt["x"], pt["y"])
            self.cfg[color_key] = hex_color
            self._persist_and_emit()
        except Exception:
            pass

    def _build_interface_page(self, page_interface):
        # ═══════════════════ Page 2: 界面 ═══════════════════
        card_appear, cl_appear = self._begin_card(page_interface, i18n.tr("外观"))
        self._card_layout_interface_bg = cl_appear  # stored for _make_eyedropper_row

        grid_appear = QGridLayout()
        grid_appear.setSpacing(6)
        grid_appear.setColumnMinimumWidth(0, 84)
        grid_appear.setColumnStretch(1, 1)

        # Theme color (only changes colors, so call it 主题颜色)
        grid_appear.addWidget(QLabel(i18n.tr("主题颜色")), 0, 0)
        self.combo_theme = NonScrollComboBox()
        self.combo_theme.addItem(i18n.tr("自动（匹配 CSP）"), "auto")
        self.combo_theme.addItem(i18n.tr("取色"), "eyedropper")
        self.combo_theme.addItem(i18n.tr("灰"), "gray")
        self.combo_theme.addItem(i18n.tr("白"), "white")
        self.combo_theme.addItem(i18n.tr("黑"), "black")
        self.combo_theme.currentTextChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_theme, 0, 1)

        # Slider visual theme
        grid_appear.addWidget(QLabel(i18n.tr("滑块样式")), 1, 0)
        self.combo_slider_style = NonScrollComboBox()
        for _key, _display in list_slider_theme_names():
            self.combo_slider_style.addItem(_display, _key)
        self.combo_slider_style.currentIndexChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_slider_style, 1, 1)

        # Font size controls (- / +)
        grid_appear.addWidget(QLabel(i18n.tr("字体大小")), 2, 0)
        row_font_size = QHBoxLayout()
        row_font_size.setSpacing(4)
        self.btn_font_dec = self._make_step_button("-")
        self.btn_font_dec.clicked.connect(self.font_decrease)
        self.lbl_font_size = QLabel("100%")
        self.lbl_font_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_font_size.setFixedSize(45, 20)
        self.btn_font_inc = self._make_step_button("+")
        self.btn_font_inc.clicked.connect(self.font_increase)
        row_font_size.addWidget(self.btn_font_dec)
        row_font_size.addWidget(self.lbl_font_size)
        row_font_size.addWidget(self.btn_font_inc)
        grid_appear.addLayout(row_font_size, 2, 1)

        # UI Scale controls (Slider)
        grid_appear.addWidget(QLabel(i18n.tr("界面缩放")), 3, 0)
        row_zoom = QHBoxLayout()
        self.zoom_slider = NonScrollSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("ScaleSlider")
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.valueChanged.connect(self.on_zoom_slider_changed)
        self.zoom_slider.sliderReleased.connect(self.on_zoom_slider_released)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(30)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_zoom.addWidget(self.zoom_slider)
        row_zoom.addWidget(self.lbl_zoom)
        grid_appear.addLayout(row_zoom, 3, 1)

        cl_appear.addLayout(grid_appear)

        self.lbl_theme_status = QLabel("")
        self.lbl_theme_status.setObjectName("StatusHint")
        cl_appear.addWidget(self.lbl_theme_status)

        # Eyedropper control rows (visible only when "取色" theme is selected)
        self._make_eyedropper_row("bar", i18n.tr("边框颜色"), i18n.tr("绘画软件标题栏/边框的深色"))
        self._make_eyedropper_row("bg",  i18n.tr("背景颜色"), i18n.tr("绘画软件画布区域的浅色"))

        page_interface.addWidget(card_appear)

        # Language (moved here from About for better discoverability)
        card_lang, cl_lang = self._begin_card(page_interface, i18n.tr("语言"))
        row_lang = QHBoxLayout()
        row_lang.setSpacing(6)
        row_lang.addWidget(QLabel(i18n.tr("界面语言")))
        self.cmb_language = NonScrollComboBox()
        self.cmb_language.addItem("自动 (Auto)", "auto")
        self.cmb_language.addItem("中文", "zh")
        self.cmb_language.addItem("English", "en")
        self.cmb_language.setToolTip(i18n.tr("切换界面语言"))
        cur_lang = self.cfg.get("language", "auto")
        for i in range(self.cmb_language.count()):
            if self.cmb_language.itemData(i) == cur_lang:
                self.cmb_language.setCurrentIndex(i)
                break
        self.cmb_language.currentIndexChanged.connect(self._on_language_changed)
        row_lang.addStretch()
        row_lang.addWidget(self.cmb_language)
        cl_lang.addLayout(row_lang)
        page_interface.addWidget(card_lang)

        card_behavior, cl_behavior = self._begin_card(page_interface, i18n.tr("窗口行为"))

        # 6 checkboxes in symmetric 3×2 grid
        grid_behavior = QGridLayout()
        grid_behavior.setSpacing(6)

        self.cb_taskbar_icon = QCheckBox(i18n.tr("任务栏图标"))
        self.cb_taskbar_icon.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_taskbar_icon, 0, 0)

        self.cb_show_title_bar = QCheckBox(i18n.tr("显示标题栏"))
        self.cb_show_title_bar.setToolTip(i18n.tr("隐藏后顶部边框与四周一致；可通过快捷键或托盘菜单恢复"))
        self.cb_show_title_bar.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_show_title_bar, 3, 0)

        self.cb_lock_size = QCheckBox(i18n.tr("固定窗口大小"))
        self.cb_lock_size.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_lock_size, 1, 0)

        self.cb_lock_position = QCheckBox(i18n.tr("锁定窗口位置"))
        self.cb_lock_position.setToolTip(i18n.tr("开启后不能拖动窗口"))
        self.cb_lock_position.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_lock_position, 2, 0)

        self.cb_autostart = QCheckBox(i18n.tr("开机自启动"))
        self.cb_autostart.setToolTip(i18n.tr("开机后自动以管理员权限启动（免 UAC 弹窗）"))
        self.cb_autostart.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_autostart, 0, 1)

        self.cb_only_drawing = QCheckBox(i18n.tr("仅在绘画软件前台时显示"))
        self.cb_only_drawing.setToolTip(i18n.tr("绘画软件不在前台时自动隐藏悬浮面板"))
        self.cb_only_drawing.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_only_drawing, 1, 1)

        self.cb_no_focus = QCheckBox(i18n.tr("无焦点取色模式"))
        self.cb_no_focus.setToolTip(i18n.tr("开启后不会抢占绘画软件的键盘焦点，适合边画边取色"))
        self.cb_no_focus.clicked.connect(self.on_no_focus_clicked)
        grid_behavior.addWidget(self.cb_no_focus, 2, 1)

        cl_behavior.addLayout(grid_behavior)
        page_interface.addWidget(card_behavior)

    def _build_filter_page(self, page_filter):
        # ═══════════════════ Page 4: 滤镜 ═══════════════════
        card_gray, cl_gray = self._begin_card(page_filter, i18n.tr("灰度滤镜"))

        grid_gray = QGridLayout()
        grid_gray.setSpacing(6)
        grid_gray.setColumnMinimumWidth(0, 84)
        grid_gray.setColumnStretch(1, 1)

        grid_gray.addWidget(QLabel(i18n.tr("作用屏幕")), 0, 0)
        self.combo_grayscale_screen = NonScrollComboBox()
        self.combo_grayscale_screen.setToolTip(i18n.tr("选择灰度滤镜作用在哪个屏幕，默认作用于全部屏幕"))
        self.combo_grayscale_screen.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_screen, 0, 1)

        grid_gray.addWidget(QLabel(i18n.tr("灰度模式")), 1, 0)
        self.combo_grayscale_mode = NonScrollComboBox()
        self.combo_grayscale_mode.addItem(i18n.tr("OKLCh (感知均匀)"), "oklch")
        self.combo_grayscale_mode.addItem(i18n.tr("Luma (BT.709 标准)"), "luma")
        self.combo_grayscale_mode.setToolTip(i18n.tr("OKLCh 更接近人眼感知；Luma 是标准亮度转换"))
        self.combo_grayscale_mode.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_mode, 1, 1)

        grid_gray.addWidget(QLabel(i18n.tr("渲染方式")), 2, 0)
        self.combo_grayscale_backend = NonScrollComboBox()
        self.combo_grayscale_backend.addItem(i18n.tr("OKLCh (GPU兼容)"), "native")
        self.combo_grayscale_backend.addItem(i18n.tr("系统 Luma (Mag)"), "mag")
        self.combo_grayscale_backend.setToolTip(
            i18n.tr("OKLCh (GPU兼容)：感知均匀的全屏灰度，覆盖 Colorink；"
            "系统 Luma (Mag)：延迟最低、仅作用于全部屏幕的备用模式；"
            "需要按屏目标时请在 Native 后端选择 Luma。"))
        self.combo_grayscale_backend.currentTextChanged.connect(self._on_grayscale_backend_changed)
        grid_gray.addWidget(self.combo_grayscale_backend, 2, 1)

        cl_gray.addLayout(grid_gray)
        page_filter.addWidget(card_gray)

    def _build_picker_page(self, page_picker):
        # ═══════════════════ Page 3: 取色器 ═══════════════════
        card_pz, cl_pz = self._begin_card(page_picker, i18n.tr("取色器"))
        grid_pz = QGridLayout()
        grid_pz.setSpacing(6)
        grid_pz.setColumnMinimumWidth(0, 84)
        grid_pz.setColumnStretch(1, 1)

        grid_pz.addWidget(QLabel(i18n.tr("取色放大倍率")), 0, 0)
        row_picker_zoom = QHBoxLayout()
        row_picker_zoom.setSpacing(4)
        self.btn_zoom_dec = self._make_step_button("-")
        self.btn_zoom_dec.clicked.connect(self.zoom_decrease)
        self.lbl_picker_zoom = QLabel("6×")
        self.lbl_picker_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_picker_zoom.setFixedSize(30, 20)
        self.btn_zoom_inc = self._make_step_button("+")
        self.btn_zoom_inc.clicked.connect(self.zoom_increase)
        row_picker_zoom.addWidget(self.btn_zoom_dec)
        row_picker_zoom.addWidget(self.lbl_picker_zoom)
        row_picker_zoom.addWidget(self.btn_zoom_inc)
        grid_pz.addLayout(row_picker_zoom, 0, 1)

        grid_pz.addWidget(QLabel(i18n.tr("前景/背景色位置")), 1, 0)
        self.combo_pos = NonScrollComboBox()
        self.combo_pos.addItem(i18n.tr("左上角"), "top-left")
        self.combo_pos.addItem(i18n.tr("左下角"), "bottom-left")
        self.combo_pos.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_pos, 1, 1)

        grid_pz.addWidget(QLabel(i18n.tr("色环模块")), 2, 0)
        self.combo_module = NonScrollComboBox()
        self.combo_module.addItems(["HSV", "HLS", "RGB", "LCH"])
        self.combo_module.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_module, 2, 1)

        cl_pz.addLayout(grid_pz)

        self.cb_show_module_btn = QCheckBox(i18n.tr("显示色环模块切换按钮"))
        self.cb_show_module_btn.setToolTip(i18n.tr("在色环区域显示色环模块切换按钮"))
        self.cb_show_module_btn.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_module_btn)

        self.cb_show_lab_toggle = QCheckBox(i18n.tr("显示 LAB 切换按钮"))
        self.cb_show_lab_toggle.setToolTip(i18n.tr("在色轮/LAB区域显示色轮与LAB之间的切换按钮"))
        self.cb_show_lab_toggle.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_lab_toggle)

        self.cb_show_lab_shape_btn = QCheckBox(i18n.tr("显示 LAB 视图形状按钮"))
        self.cb_show_lab_shape_btn.setToolTip(i18n.tr("在 LAB 区域显示方形/圆形切换按钮"))
        self.cb_show_lab_shape_btn.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_lab_shape_btn)

        self.cb_show_lab_harmony_btn = QCheckBox(i18n.tr("显示 LAB 调和按钮"))
        self.cb_show_lab_harmony_btn.setToolTip(i18n.tr("在 LAB 区域显示互补/近似/三等分等调和模式按钮"))
        self.cb_show_lab_harmony_btn.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_lab_harmony_btn)

        page_picker.addWidget(card_pz)

        card_sl_order, cl_sl_order = self._begin_card(page_picker, i18n.tr("滑块显示与顺序"))

        self._MODULE_SLIDER_MAP = {
            "hsv":  ["HSV", "RGB", "LAB", "OKLab", "OKLCh"],
            "hls":  ["HSL", "RGB", "LAB", "OKLab", "OKLCh"],
            "rgb":  ["RGB", "HSV", "LAB", "OKLab", "OKLCh"],
            "lch":  ["OKLCh", "OKLab", "RGB"],
        }
        self.slider_rows = {}
        for key, name in [("RGB", "RGB 滑块"), ("HSV", "HSV 滑块"), ("HSL", "HLS 滑块"),
                          ("LAB", "LAB 滑块"), ("OKLab", "OKLab 滑块"), ("OKLCh", "OKLCh 滑块")]:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            cb = QCheckBox(i18n.tr(name))
            cb.stateChanged.connect(self.save_settings)
            btn_up = self._make_step_button("▲", i18n.tr("上移"), width=24)
            btn_up.clicked.connect(lambda _checked, k=key: self._move_slider_order(k, -1))
            btn_down = self._make_step_button("▼", i18n.tr("下移"), width=24)
            btn_down.clicked.connect(lambda _checked, k=key: self._move_slider_order(k, 1))
            row_layout.addWidget(cb)
            row_layout.addStretch()
            row_layout.addWidget(btn_up)
            row_layout.addWidget(btn_down)
            cl_sl_order.addLayout(row_layout)
            self.slider_rows[key] = (cb, btn_up, btn_down, row_layout)

        # Kept so the rows can be visually reordered to match the config.
        self._sl_order_layout = cl_sl_order

        page_picker.addWidget(card_sl_order)

        self._refresh_module_sliders()

        card_hist, cl_hist = self._begin_card(page_picker, i18n.tr("颜色历史"))

        row_hist_show = QHBoxLayout()
        row_hist_show.setSpacing(6)
        self.cb_history = QCheckBox(i18n.tr("显示颜色历史"))
        self.cb_history.stateChanged.connect(self.save_settings)
        self.btn_hist_up = self._make_step_button("▲", i18n.tr("在滑块顺序中上移"), width=24)
        self.btn_hist_up.clicked.connect(lambda _checked: self._move_slider_order("History", -1))
        self.btn_hist_down = self._make_step_button("▼", i18n.tr("在滑块顺序中下移"), width=24)
        self.btn_hist_down.clicked.connect(lambda _checked: self._move_slider_order("History", 1))
        row_hist_show.addWidget(self.cb_history)
        row_hist_show.addStretch()
        row_hist_show.addWidget(self.btn_hist_up)
        row_hist_show.addWidget(self.btn_hist_down)
        cl_hist.addLayout(row_hist_show)

        # History grid shape — columns × rows (2×2 grid for label alignment)
        grid_hist = QGridLayout()
        grid_hist.setSpacing(6)
        grid_hist.setColumnMinimumWidth(0, 84)
        grid_hist.setColumnStretch(1, 1)

        grid_hist.addWidget(QLabel(i18n.tr("历史列数")), 0, 0)
        self.combo_history_cols = NonScrollComboBox()
        self.combo_history_cols.addItems(["3", "4", "5", "6", "7", "8", "9", "10", "12", "14", "16"])
        self.combo_history_cols.currentTextChanged.connect(self.save_settings)
        self.combo_history_cols.setFixedWidth(50)
        grid_hist.addWidget(self.combo_history_cols, 0, 1)

        grid_hist.addWidget(QLabel(i18n.tr("历史行数")), 1, 0)
        self.combo_history_rows = NonScrollComboBox()
        self.combo_history_rows.addItems(["1", "2", "3", "4", "5", "6", "8"])
        self.combo_history_rows.currentTextChanged.connect(self.save_settings)
        self.combo_history_rows.setFixedWidth(50)
        grid_hist.addWidget(self.combo_history_rows, 1, 1)

        cl_hist.addLayout(grid_hist)
        page_picker.addWidget(card_hist)

        card_wheel, cl_wheel = self._begin_card(page_picker, i18n.tr("色环与 LAB"))

        # Ringless mode settings
        self.ringless_settings = RinglessSettingsWidget()
        self.ringless_settings.changed.connect(self.save_settings)
        cl_wheel.addWidget(self.ringless_settings)

        grid_wheel = QGridLayout()
        grid_wheel.setSpacing(6)
        grid_wheel.setColumnMinimumWidth(0, 84)
        grid_wheel.setColumnStretch(1, 1)

        grid_wheel.addWidget(QLabel(i18n.tr("LAB 视图模式")), 0, 0)
        self.combo_viz_mode = NonScrollComboBox()
        self.combo_viz_mode.addItem(i18n.tr("LAB 色彩空间"), "lab")
        self.combo_viz_mode.addItem(i18n.tr("OKLab 色彩空间"), "oklab")
        self.combo_viz_mode.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_viz_mode, 0, 1)

        grid_wheel.addWidget(QLabel(i18n.tr("LAB 视图形状")), 1, 0)
        self.combo_lab_shape = NonScrollComboBox()
        self.combo_lab_shape.addItem(i18n.tr("方形"), "square")
        self.combo_lab_shape.addItem(i18n.tr("圆形"), "disc")
        self.combo_lab_shape.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_lab_shape, 1, 1)

        grid_wheel.addWidget(QLabel(i18n.tr("LAB 调和模式")), 2, 0)
        self.combo_lab_harmony = NonScrollComboBox()
        for mode, label in HARMONY_MODE_NAMES.items():
            self.combo_lab_harmony.addItem(i18n.tr(label), mode)
        self.combo_lab_harmony.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_lab_harmony, 2, 1)

        cl_wheel.addLayout(grid_wheel)

        self.cb_show_lab_lightness = QCheckBox(i18n.tr("显示 LAB 亮度滑块"))
        self.cb_show_lab_lightness.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self.cb_show_lab_lightness)

        self.cb_flip_wheel = QCheckBox(i18n.tr("水平翻转色环"))
        self.cb_flip_wheel.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self.cb_flip_wheel)

        page_picker.addWidget(card_wheel)

        card_sp, cl_sp = self._begin_card(page_picker, i18n.tr("高级"))

        grid_sp = QGridLayout()
        grid_sp.setSpacing(6)
        grid_sp.setColumnMinimumWidth(0, 84)
        grid_sp.setColumnStretch(1, 1)

        # 滚轮步长
        grid_sp.addWidget(QLabel(i18n.tr("滑块滚轮步长")), 0, 0)
        row_scroll = QHBoxLayout()
        row_scroll.setSpacing(4)
        self.btn_scroll_dec = self._make_step_button("-")
        self.btn_scroll_dec.clicked.connect(self.scroll_step_decrease)
        self.lbl_scroll_step = QLabel("1")
        self.lbl_scroll_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scroll_step.setFixedSize(45, 20)
        self.btn_scroll_inc = self._make_step_button("+")
        self.btn_scroll_inc.clicked.connect(self.scroll_step_increase)
        row_scroll.addWidget(self.btn_scroll_dec)
        row_scroll.addWidget(self.lbl_scroll_step)
        row_scroll.addWidget(self.btn_scroll_inc)
        grid_sp.addLayout(row_scroll, 0, 1)

        # 同一空间间距
        grid_sp.addWidget(QLabel(i18n.tr("同色空间滑块间距")), 1, 0)
        row_same = QHBoxLayout()
        row_same.setSpacing(4)
        self.btn_same_dec = self._make_step_button("-")
        self.btn_same_dec.clicked.connect(self.same_space_decrease)
        self.lbl_same_space = QLabel("6")
        self.lbl_same_space.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_same_space.setFixedSize(45, 20)
        self.btn_same_inc = self._make_step_button("+")
        self.btn_same_inc.clicked.connect(self.same_space_increase)
        row_same.addWidget(self.btn_same_dec)
        row_same.addWidget(self.lbl_same_space)
        row_same.addWidget(self.btn_same_inc)
        grid_sp.addLayout(row_same, 1, 1)

        # 不同色空间滑块间距
        grid_sp.addWidget(QLabel(i18n.tr("不同色空间滑块间距")), 2, 0)
        row_diff = QHBoxLayout()
        row_diff.setSpacing(4)
        self.btn_diff_dec = self._make_step_button("-")
        self.btn_diff_dec.clicked.connect(self.diff_space_decrease)
        self.lbl_diff_space = QLabel("8")
        self.lbl_diff_space.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_diff_space.setFixedSize(45, 20)
        self.btn_diff_inc = self._make_step_button("+")
        self.btn_diff_inc.clicked.connect(self.diff_space_increase)
        row_diff.addWidget(self.btn_diff_dec)
        row_diff.addWidget(self.lbl_diff_space)
        row_diff.addWidget(self.btn_diff_inc)
        grid_sp.addLayout(row_diff, 2, 1)

        cl_sp.addLayout(grid_sp)
        page_picker.addWidget(card_sp)


