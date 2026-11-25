"""Appearance, theme and eyedropper controls for the settings sidebar.

Extracted from ``ui.settings_sidebar``: interface/picker appearance controls,
theme resolution and the dual-point eyedropper workflow.
"""

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import autostart, i18n
from ui.border_themes import list_border_theme_names
from ui.chrome_opacity import (
    CHROME_OPACITY_KEY,
    CHROME_OPACITY_MAX,
    CHROME_OPACITY_MIN,
    CHROME_OPACITY_STEP,
    clamp_chrome_opacity,
)
from ui.lab_harmony import HARMONY_MODE_NAMES
from ui.ringless_settings import RinglessSettingsWidget
from ui.settings.settings_helpers import (
    _ARROW_DOWN_DARK,
    _ARROW_DOWN_LIGHT,
    _CHECKBOX_CHECK_ICON,
    NonScrollComboBox,
    NonScrollSlider,
)
from ui.settings.tooltips import SLIDER_SHOW_TIPS
from ui.slider_themes import list_slider_theme_names
from ui.theme_contrast import DARK_INK, muted_ink, readable_ink
from ui.window.module_defs import ALL_SLIDERS


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
        backend = self.combo_grayscale_backend.currentData()
        if not backend:
            backend = "mag" if "Mag" in text else "dcomp"
        self._update_grayscale_mode_options(backend)
        self._update_grayscale_screen_options(backend)
        self._update_grayscale_backend_note()
        self.save_settings()

    def _update_grayscale_backend_note(self) -> None:
        lbl = getattr(self, "lbl_grayscale_backend_note", None)
        if lbl is None:
            return
        backend = self.combo_grayscale_backend.currentData() if hasattr(self, "combo_grayscale_backend") else None
        if not backend:
            backend = self.cfg.get("grayscaleFilterBackend", "dcomp")
        if backend == "mag":
            lbl.setText(i18n.tr(
                "系统 Mag 模式仅支持 BT.709 Luma 亮度并作用于全部屏幕；区域灰度同步使用 Windows 放大镜窗口。"
            ))
        else:
            lbl.setText(i18n.tr(
                "DComp 显存直通提供零拷贝低延迟的 OKLCh / Luma 渲染；配置局部区域后将按桌面坐标跨屏生效。"
            ))

    def _read_grayscale_region_entries(self) -> list[dict]:
        from core.region_grayscale import read_region_entries_from_config
        return read_region_entries_from_config(self.cfg)

    def _apply_grayscale_region_entries(self, entries: list[dict]) -> None:
        from core.region_grayscale import write_regions_to_config
        if hasattr(self, "_parent") and self._parent is not None:
            apply_fn = getattr(self._parent, "apply_grayscale_regions", None)
            if callable(apply_fn):
                normalized = apply_fn(entries, activate=False)
                write_regions_to_config(self.cfg, normalized)
                self._refresh_grayscale_region_ui()
                return
        write_regions_to_config(self.cfg, entries)
        if hasattr(self, "_persist_config"):
            self._persist_config()
        self._refresh_grayscale_region_ui()

    def _format_grayscale_region_text(self) -> str:
        from core.region_grayscale import format_region_status_from_entries
        entries = self._read_grayscale_region_entries()
        return format_region_status_from_entries(entries, i18n.tr)

    def _refresh_grayscale_status_ui(self) -> None:
        from ui.hotkey_button import display_hotkey
        parent = getattr(self, "_parent", None)
        fs_ctrl = getattr(parent, "grayscale_overlay", None) if parent is not None else None
        rg_ctrl = getattr(parent, "region_grayscale_overlay", None) if parent is not None else None
        fs_active = bool(getattr(fs_ctrl, "is_active", False))
        rg_active = bool(getattr(rg_ctrl, "is_active", False))

        fs_key = self.cfg.get("grayscaleFilterKey", "Ctrl+Alt+D")
        fs_key_disp = display_hotkey(fs_key) if fs_key else i18n.tr("未绑定")
        rg_key = self.cfg.get("regionGrayscaleKey", "Ctrl+Alt+G")
        rg_key_disp = display_hotkey(rg_key) if rg_key else i18n.tr("未绑定")

        if hasattr(self, "lbl_fullscreen_grayscale_status"):
            self.lbl_fullscreen_grayscale_status.setText(
                i18n.tr("● 已开启") if fs_active else i18n.tr("未开启")
            )
            self._set_label_state(
                self.lbl_fullscreen_grayscale_status,
                "success" if fs_active else "muted",
            )
        if hasattr(self, "lbl_fullscreen_grayscale_sub"):
            self.lbl_fullscreen_grayscale_sub.setText(
                i18n.tr("伪全屏覆盖所选屏幕（底部留极窄边缘防卡顿） · 快捷键：{key}", key=fs_key_disp)
            )
        if hasattr(self, "btn_toggle_fullscreen_grayscale"):
            self.btn_toggle_fullscreen_grayscale.setText(
                i18n.tr("关闭全屏灰度") if fs_active else i18n.tr("开启全屏灰度")
            )
            self.btn_toggle_fullscreen_grayscale.setProperty("activeFilter", fs_active)
            st = self.btn_toggle_fullscreen_grayscale.style()
            st.unpolish(self.btn_toggle_fullscreen_grayscale)
            st.polish(self.btn_toggle_fullscreen_grayscale)

        if hasattr(self, "lbl_region_grayscale_status"):
            self.lbl_region_grayscale_status.setText(
                i18n.tr("● 已开启") if rg_active else i18n.tr("未开启")
            )
            self._set_label_state(
                self.lbl_region_grayscale_status,
                "success" if rg_active else "muted",
            )
        if hasattr(self, "lbl_region_grayscale_sub"):
            scope_text = self._format_grayscale_region_text()
            self.lbl_region_grayscale_sub.setText(
                i18n.tr("无边框穿透窗口 ({scope}) · 快捷键：{key}", scope=scope_text, key=rg_key_disp)
            )
        if hasattr(self, "btn_toggle_region_grayscale"):
            self.btn_toggle_region_grayscale.setText(
                i18n.tr("关闭区域灰度") if rg_active else i18n.tr("开启区域灰度")
            )
            self.btn_toggle_region_grayscale.setProperty("activeFilter", rg_active)
            st = self.btn_toggle_region_grayscale.style()
            st.unpolish(self.btn_toggle_region_grayscale)
            st.polish(self.btn_toggle_region_grayscale)

    def _rebuild_grayscale_regions_list(self, entries: list[dict]) -> None:
        container_layout = getattr(self, "_grayscale_regions_layout", None)
        if container_layout is None:
            return
        self._clear_layout(container_layout)

        if not entries:
            empty_banner = QFrame()
            empty_banner.setObjectName("TipBanner")
            eb_layout = QVBoxLayout(empty_banner)
            eb_layout.setContentsMargins(10, 8, 10, 8)
            eb_layout.setSpacing(2)
            lbl_empty = QLabel(i18n.tr(
                "未设定局部区域：区域灰度将以默认最大化无边框窗口覆盖目标屏幕。\n"
                "点击「框选区域」可在屏幕上拖拽框选一个或多个局部灰度区域。"
            ))
            lbl_empty.setObjectName("TipDesc")
            lbl_empty.setWordWrap(True)
            eb_layout.addWidget(lbl_empty)
            container_layout.addWidget(empty_banner)
            return

        from ui.region_grayscale_selector import describe_region_screens
        for idx, entry in enumerate(entries):
            rx = int(entry["x"])
            ry = int(entry["y"])
            rw = int(entry["width"])
            rh = int(entry["height"])
            enabled = bool(entry.get("enabled", True))
            custom_name = entry.get("name", "")
            label_prefix = custom_name if custom_name else i18n.tr("区域 {idx}", idx=idx + 1)

            row_frame = QFrame()
            row_frame.setObjectName("GrayscaleRegionRow")
            row_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(8)

            cb = QCheckBox()
            cb.setChecked(enabled)
            cb.setToolTip(i18n.tr("启用或暂时停用此灰度区域"))
            cb.toggled.connect(lambda checked, i=idx: self._on_toggle_grayscale_region_item(i, checked))
            row_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignVCenter)

            info_col = QVBoxLayout()
            info_col.setContentsMargins(0, 0, 0, 0)
            info_col.setSpacing(2)
            title_lbl = QLabel(f"{label_prefix}  ·  {rw} × {rh} px")
            title_lbl.setObjectName("RowTitle" if enabled else "StatusMuted")
            title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            screen_desc = describe_region_screens(rx, ry, rw, rh, i18n.tr)
            sub_lbl = QLabel(i18n.tr("起点 ({x}, {y}) · {screen}", x=rx, y=ry, screen=screen_desc))
            sub_lbl.setObjectName("RowSubtitle")
            sub_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            info_col.addWidget(title_lbl)
            info_col.addWidget(sub_lbl)
            row_layout.addLayout(info_col, 1)

            btn_reselect = QPushButton(i18n.tr("重选"))
            btn_reselect.setObjectName("GrayscaleActionSmall")
            btn_reselect.setToolTip(i18n.tr("在屏幕上重新拖拽框选此区域"))
            btn_reselect.clicked.connect(lambda _chk=False, i=idx: self._on_reselect_grayscale_region_item(i))
            row_layout.addWidget(btn_reselect, 0, Qt.AlignmentFlag.AlignVCenter)

            btn_del = QPushButton("×")
            btn_del.setObjectName("GrayscaleRemoveSmall")
            btn_del.setFixedSize(24, 22)
            btn_del.setToolTip(i18n.tr("删除此灰度区域"))
            btn_del.clicked.connect(lambda _chk=False, i=idx: self._on_remove_grayscale_region_item(i))
            row_layout.addWidget(btn_del, 0, Qt.AlignmentFlag.AlignVCenter)

            container_layout.addWidget(row_frame)

        card_regions = getattr(self, "_card_grayscale_regions", None)
        if card_regions is not None:
            card_regions.updateGeometry()

    def _refresh_grayscale_region_ui(self) -> None:
        if not hasattr(self, "lbl_grayscale_region"):
            return
        entries = self._read_grayscale_region_entries()
        active_count = sum(1 for e in entries if e.get("enabled", True))
        self.lbl_grayscale_region.setText(self._format_grayscale_region_text())
        self._set_label_state(
            self.lbl_grayscale_region,
            "muted" if not entries or active_count == 0 else "accent",
        )
        if hasattr(self, "btn_select_grayscale_region"):
            self.btn_select_grayscale_region.setText(
                i18n.tr("+ 框选 / 编辑") if entries else i18n.tr("框选区域")
            )
        if hasattr(self, "btn_reset_grayscale_region"):
            self.btn_reset_grayscale_region.setEnabled(bool(entries))
        self._rebuild_grayscale_regions_list(entries)
        self._refresh_grayscale_status_ui()
        self._update_grayscale_backend_note()

    def _on_toggle_grayscale_region_item(self, idx: int, checked: bool) -> None:
        entries = self._read_grayscale_region_entries()
        if 0 <= idx < len(entries):
            entries[idx]["enabled"] = bool(checked)
            self._apply_grayscale_region_entries(entries)

    def _on_reselect_grayscale_region_item(self, idx: int) -> None:
        if hasattr(self, "_parent") and self._parent is not None:
            start_fn = getattr(self._parent, "start_select_grayscale_region", None)
            if callable(start_fn):
                try:
                    start_fn(replace_index=idx)
                except TypeError:
                    start_fn()

    def _on_remove_grayscale_region_item(self, idx: int) -> None:
        entries = self._read_grayscale_region_entries()
        if 0 <= idx < len(entries):
            entries.pop(idx)
            self._apply_grayscale_region_entries(entries)

    def _on_select_grayscale_region(self) -> None:
        if hasattr(self, "_parent") and self._parent is not None:
            start_fn = getattr(self._parent, "start_select_grayscale_region", None)
            if callable(start_fn):
                start_fn()

    def _on_reset_grayscale_region(self) -> None:
        if hasattr(self, "_parent") and self._parent is not None:
            reset_fn = getattr(self._parent, "reset_grayscale_region_to_maximized", None)
            if callable(reset_fn):
                reset_fn()
                if getattr(self._parent, "cfg", {}).get("grayscaleFilterRegion") is None:
                    self.cfg["grayscaleFilterRegion"] = None
                    self.cfg["grayscaleFilterRegions"] = []
                self._refresh_grayscale_region_ui()
                return
        self.cfg["grayscaleFilterRegion"] = None
        self.cfg["grayscaleFilterRegions"] = []
        self._refresh_grayscale_region_ui()
        self.save_settings()

    def _on_toggle_fullscreen_grayscale(self) -> None:
        if hasattr(self, "_parent") and self._parent is not None:
            toggle_fn = getattr(self._parent, "toggle_fullscreen_grayscale", None)
            if callable(toggle_fn):
                toggle_fn()
            else:
                hk_fn = getattr(self._parent, "on_hotkey_triggered", None)
                if callable(hk_fn):
                    hk_fn("grayscaleFilterKey")
            self._refresh_grayscale_status_ui()

    def _on_toggle_region_grayscale(self) -> None:
        if hasattr(self, "_parent") and self._parent is not None:
            toggle_fn = getattr(self._parent, "toggle_region_grayscale", None)
            if callable(toggle_fn):
                toggle_fn()
            self._refresh_grayscale_status_ui()

    def theme_colors(self):
        """Resolve active theme colors dynamically based on parent window.

        Returns dict with keys: bg, text, border, bar_bg, plus derived
        semantic tokens: accent, muted, success, warning, danger. The
        semantic tokens are computed from the theme's text/bg contrast so
        status labels stay legible in both light and dark chrome.

        Two inks come back, one per surface the settings UI paints:

        * `text` / `muted` — for widgets filled with `bg` (combo boxes,
          buttons, value fields)
        * `bar_text` / `bar_muted` — for the panel itself, which is filled
          with `bar_bg`

        They used to be the same ink. That breaks as soon as a theme's two
        tones disagree in lightness: the eyedropper theme lets the user pick
        a dark frame colour next to a light background colour, and the whole
        settings panel then rendered dark text on a dark surface.
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
                    # Perceptual luma, not HSL lightness: a saturated pick
                    # (yellow sits at lightness 127) would otherwise ask for
                    # white text on a colour the eye reads as bright.
                    text = readable_ink(bg)
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

        # Muted = primary text at ~45% alpha (de-emphasized / disabled-like)
        muted = muted_ink(text)
        # The panel is filled with barBg, so its ink is chosen against barBg
        # instead of being inherited from the body colour.
        bar_text = readable_ink(barBg)
        bar_muted = muted_ink(bar_text)
        # Status labels live on the panel, so their contrast follows it too
        if bar_text == DARK_INK:  # light panel → darker status colors
            success, warning, danger = "#2e7d32", "#b26a00", "#c62828"
        else:                     # dark panel → lighter status colors
            success, warning, danger = "#4caf50", "#ffb74d", "#ef5350"

        return {"bg": bg, "text": text, "border": border_color, "bar_bg": barBg,
                "bar_text": bar_text, "bar_muted": bar_muted,
                "accent": "#5a94e2", "muted": muted,
                "success": success, "warning": warning, "danger": danger}

    def apply_theme(self):
        font_factor = self.cfg.get("fontSize", 100) / 100.0
        font_size = int(11 * font_factor)
        header_font_size = int(12 * font_factor)

        c = self.theme_colors()
        bg = c["bg"]
        text = c["text"]              # ink on the input surface (bg)
        barBg = c["bar_bg"]
        bar_text = c["bar_text"]      # ink on the panel surface (bar_bg)
        bar_muted = c["bar_muted"]
        accent = c["accent"]
        success = c["success"]
        warning = c["warning"]
        danger = c["danger"]

        is_dark_text = QColor(text).lightness() < 128
        borderColor = "#d0d0d0" if is_dark_text else "#555555"

        # Srgb components for semi-transparent derivations
        tc = QColor(text)
        text_r, text_g, text_b = tc.red(), tc.green(), tc.blue()
        # Overlays and scrollbars sit on the panel, so they are tinted with
        # the panel ink; disabled button text stays on the input surface.
        bc = QColor(bar_text)
        bar_r, bar_g, bar_b = bc.red(), bc.green(), bc.blue()
        panel_is_light = bar_text == DARK_INK

        if panel_is_light:
            card_bg = "rgba(0, 0, 0, 0.035)"
            card_border = "rgba(0, 0, 0, 0.08)"
            tip_bg = "rgba(0, 0, 0, 0.04)"
            tip_border = "rgba(0, 0, 0, 0.10)"
            hover_bg = "rgba(0,0,0,0.06)"
            pressed_bg = "rgba(0,0,0,0.10)"
            scroll_handle = f"rgba({bar_r},{bar_g},{bar_b},0.25)"
            scroll_handle_hover = f"rgba({bar_r},{bar_g},{bar_b},0.45)"
        else:
            card_bg = "rgba(255, 255, 255, 0.04)"
            card_border = "rgba(255, 255, 255, 0.08)"
            tip_bg = "rgba(255, 255, 255, 0.04)"
            tip_border = "rgba(255, 255, 255, 0.10)"
            hover_bg = "rgba(255,255,255,0.08)"
            pressed_bg = "rgba(255,255,255,0.04)"
            scroll_handle = f"rgba({bar_r},{bar_g},{bar_b},0.20)"
            scroll_handle_hover = f"rgba({bar_r},{bar_g},{bar_b},0.35)"
        disabled_color = (f"rgba({text_r},{text_g},{text_b},0.40)" if is_dark_text
                          else f"rgba({text_r},{text_g},{text_b},0.30)")


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
                color: {bar_muted};
                border-radius: 3px;
                padding: 0 8px;
                margin: 1px 0;
            }}
            QListWidget#NavRail::item:hover {{
                background-color: {hover_bg};
                color: {bar_text};
            }}
            QListWidget#NavRail::item:selected {{
                background-color: {accent};
                color: white;
            }}
            /* Card containers with subtle surface */
            QFrame#SettingsCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 6px;
            }}
            QFrame#TipBanner {{
                background-color: {tip_bg};
                border: 1px solid {tip_border};
                border-left: 3px solid {accent};
                border-radius: 4px;
            }}
            QWidget {{
                color: {bar_text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {font_size}px;
            }}
            QLabel {{
                color: {bar_text};
                background: transparent;
            }}
            QLabel#SectionHeader {{
                font-weight: bold;
                font-size: {header_font_size}px;
                margin-top: 2px;
                margin-bottom: 2px;
                color: {bar_text};
            }}
            QLabel#RowTitle {{
                font-size: {font_size}px;
                color: {bar_text};
                background: transparent;
            }}
            QLabel#RowSubtitle {{
                font-size: {max(9, int(font_size * 0.9))}px;
                color: {bar_muted};
                background: transparent;
            }}
            QLabel#TipKey {{
                font-size: {font_size}px;
                font-weight: bold;
                color: {bar_text};
                background: transparent;
            }}
            QLabel#TipDesc {{
                font-size: {font_size}px;
                color: {bar_text};
                background: transparent;
            }}
            QLabel#StatusHint {{
                color: {bar_muted};
                background: transparent;
                font-size: {font_size}px;
            }}
            QLabel#StatusMuted {{
                color: {bar_muted};
                background: transparent;
            }}
            QLabel#StatusAccent {{
                color: {accent};
                font-weight: bold;
                background: transparent;
            }}
            QLabel#StatusSuccess {{
                color: {success};
                font-weight: bold;
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
                color: {bar_text};
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
            QPushButton#GrayscaleToggleBtn[activeFilter="true"] {{
                background-color: {accent};
                border-color: {accent};
                color: white;
                font-weight: bold;
            }}
            QPushButton#GrayscaleActionSmall,
            QPushButton#GrayscaleRemoveSmall {{
                padding: 1px 7px;
                min-height: 20px;
                font-size: {max(9, int(font_size * 0.92))}px;
            }}
            QPushButton#GrayscaleRemoveSmall:hover {{
                border-color: {danger};
                color: {danger};
            }}
            QFrame#GrayscaleRegionRow {{
                background-color: {bg};
                border: 1px solid {borderColor};
                border-radius: 5px;
            }}
            QFrame#GrayscaleRegionRow:hover {{
                border-color: {accent};
            }}
            QPushButton#StepButton {{
                min-width: 28px;
                max-width: 28px;
                min-height: 26px;
                max-height: 26px;
                padding: 0;
                border: 1px solid {borderColor};
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
                background-color: {bg};
                color: {text};
            }}
            QPushButton#StepButton:hover {{
                border-color: {accent};
                background-color: {hover_bg};
            }}
            QPushButton#StepButton:pressed {{
                background-color: {pressed_bg};
            }}
            QPushButton#StepButton:focus {{
                border-color: {accent};
            }}
            QPushButton#StepButton:disabled {{
                color: {disabled_color};
            }}
            /* Hotkey unbind ("无") button — sits beside the capture button so
               clearing a hotkey is an explicit, always-labelled choice. Hidden
               (see _sync_hotkey_none_buttons) while the binding is already
               empty, so its :disabled style is only a fallback. */
            QPushButton#HotkeyNoneButton {{
                min-width: 22px;
                max-width: 22px;
                min-height: 24px;
                max-height: 24px;
                padding: 0;
                border: 1px solid {borderColor};
                border-radius: 3px;
                font-size: {font_size}px;
                background-color: {bg};
                color: {text};
            }}
            QPushButton#HotkeyNoneButton:hover {{
                border-color: {accent};
                background-color: {hover_bg};
            }}
            QPushButton#HotkeyNoneButton:pressed {{
                background-color: {pressed_bg};
            }}
            QPushButton#HotkeyNoneButton:focus {{
                border-color: {accent};
            }}
            QPushButton#HotkeyNoneButton:disabled {{
                color: {disabled_color};
            }}
            QLabel#StepValueLabel {{
                border: 1px solid {borderColor};
                background-color: {bg};
                color: {text};
                border-radius: 3px;
                font-size: {font_size}px;
                min-height: 26px;
                max-height: 26px;
                min-width: 48px;
                max-width: 48px;
                qproperty-alignment: AlignCenter;
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
        val = min(20, val + 1)
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

    def on_opacity_slider_changed(self):
        """Repaint the window chrome at the dragged opacity, without saving.

        Transparency is the one setting nobody can judge from a number, so
        it previews live on the real window: the value goes into the main
        window's in-memory config and the theme re-runs as a pure repaint
        (is_resize_event=True skips the content-height pass). Nothing
        touches the config file while the handle is held, so a settings
        reload simply restores the stored value.
        """
        value = clamp_chrome_opacity(self.opacity_slider.value())
        self.lbl_opacity.setText(f"{value}%")
        parent = getattr(self, "_parent", None)
        if parent is not None:
            parent.cfg[CHROME_OPACITY_KEY] = value
            parent.apply_theme(is_resize_event=True)
        if not self.opacity_slider.isSliderDown():
            # Arrow keys, page steps and groove clicks change the value
            # without ever emitting sliderReleased, so they commit here
            # instead of leaving a preview that no save would pick up.
            self.on_opacity_slider_released()

    def on_opacity_slider_released(self):
        """Commit the previewed opacity to the config file."""
        self.cfg[CHROME_OPACITY_KEY] = clamp_chrome_opacity(self.opacity_slider.value())
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

    def refresh_floating_panel_list(self):
        """Show which panels are currently out in their own windows.

        The one place that answers "where did that panel go" — a window
        dragged onto a second monitor or off-screen cannot be right-clicked,
        so there has to be a way back that does not involve finding it.
        """
        from ui.panels import registry, store

        floating = store.load_floating_from(self.cfg)
        names = [(registry.panel(panel_id).title if registry.panel(panel_id)
                  else panel_id) for panel_id in floating]
        label = getattr(self, "lbl_floating_panels", None)
        if label is not None:
            label.setText(i18n.tr("浮出的面板：无") if not names
                          else i18n.tr("浮出的面板：") + "、".join(names))
        button = getattr(self, "btn_dock_all_panels", None)
        if button is not None:
            button.setEnabled(bool(names))

    def dock_all_floating_panels(self):
        """Bring every torn-off panel home."""
        from core import config as _config
        from ui.panels import store

        window = getattr(self, "_parent", None)
        dock = getattr(window, "dock_panel", None)
        if callable(dock):
            for panel_id in list(store.load_floating_from(window.cfg)):
                dock(panel_id)
        self.cfg = _config.load_hotkey_config()
        self.refresh_floating_panel_list()

    def reset_panel_arrangement(self):
        """Forget a dragged panel arrangement.

        Only the arrangement: the layout switches above are settings the
        user chose, not the mess they just dragged. The window re-assembles
        from those switches when settingChanged lands.
        """
        from core import config as _config
        from ui.panels import store

        store.clear(self.cfg)
        _config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()
        self.refresh_floating_panel_list()

    def _build_space_row(self, kind: str):
        """A -/value/+ row for one spacing setting."""
        row = QHBoxLayout()
        row.setSpacing(4)
        dec = self._make_step_button("-")
        default = {"same": "6", "diff": "8", "top": "6"}.get(kind, "6")
        label = QLabel(default)
        label.setObjectName("StepValueLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(48, 26)
        inc = self._make_step_button("+")
        if kind == "same":
            dec.clicked.connect(self.same_space_decrease)
            inc.clicked.connect(self.same_space_increase)
            self.btn_same_dec, self.lbl_same_space, self.btn_same_inc = dec, label, inc
        elif kind == "top":
            dec.clicked.connect(self.panel_top_gap_decrease)
            inc.clicked.connect(self.panel_top_gap_increase)
            self.btn_top_gap_dec, self.lbl_top_gap, self.btn_top_gap_inc = dec, label, inc
        else:
            dec.clicked.connect(self.diff_space_decrease)
            inc.clicked.connect(self.diff_space_increase)
            self.btn_diff_dec, self.lbl_diff_space, self.btn_diff_inc = dec, label, inc
        row.addWidget(dec)
        row.addWidget(label)
        row.addWidget(inc)
        unit = QLabel("px")
        unit.setObjectName("RowSubtitle")
        row.addWidget(unit)
        row.addStretch()
        return row

    def _build_sliders_page(self, page_sliders):
        # ═══════════════════ Page: 滑块 ═══════════════════
        # ── Card 1: 色彩滑块 ──
        card_sl_order, cl_sl_order = self._begin_card(page_sliders, i18n.tr("色彩滑块"))

        self._MODULE_SLIDER_MAP = {
            "hsv":  ALL_SLIDERS,
            "vhsv": ALL_SLIDERS,
            "hls":  ALL_SLIDERS,
            "rgb":  ALL_SLIDERS,
            "lch":  ALL_SLIDERS,
        }
        self.slider_rows = {}
        for key, name in [("RGB", "RGB 滑块"), ("HSV", "HSV 滑块"), ("VHSV", "VHSV 滑块"), ("HSL", "HLS 滑块"),
                          ("LAB", "LAB 滑块"), ("OKLab", "OKLab 滑块"), ("OKLCh", "OKLCh 滑块")]:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            cb = QCheckBox(i18n.tr(name))
            tip = SLIDER_SHOW_TIPS.get(key)
            if tip:
                cb.setToolTip(i18n.tr(tip))
            cb.stateChanged.connect(self.save_settings)
            row_layout.addWidget(cb)
            row_layout.addStretch()
            cl_sl_order.addLayout(row_layout)
            self.slider_rows[key] = (cb, row_layout)

        self._sl_order_layout = cl_sl_order
        page_sliders.addWidget(card_sl_order)
        self._refresh_module_sliders()

        # ── Card 2: 高级（步长调节）──
        card_sp, cl_sp = self._begin_card(page_sliders, i18n.tr("高级"))
        grid_sp = QGridLayout()
        grid_sp.setSpacing(8)
        grid_sp.setColumnMinimumWidth(0, 110)
        grid_sp.setColumnStretch(1, 1)

        grid_sp.addWidget(QLabel(i18n.tr("滑块滚轮步长")), 0, 0)
        row_scroll = QHBoxLayout()
        row_scroll.setSpacing(4)
        self.btn_scroll_dec = self._make_step_button("-")
        self.btn_scroll_dec.clicked.connect(self.scroll_step_decrease)
        self.lbl_scroll_step = QLabel("1")
        self.lbl_scroll_step.setObjectName("StepValueLabel")
        self.lbl_scroll_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scroll_step.setFixedSize(48, 26)
        self.btn_scroll_inc = self._make_step_button("+")
        self.btn_scroll_inc.clicked.connect(self.scroll_step_increase)
        row_scroll.addWidget(self.btn_scroll_dec)
        row_scroll.addWidget(self.lbl_scroll_step)
        row_scroll.addWidget(self.btn_scroll_inc)
        row_scroll.addStretch()
        grid_sp.addLayout(row_scroll, 0, 1)

        cl_sp.addLayout(grid_sp)
        page_sliders.addWidget(card_sp)

        # ── Card 3: 颜色历史 ──
        card_hist, cl_hist = self._begin_card(page_sliders, i18n.tr("颜色历史"))

        self.cb_history = QCheckBox(i18n.tr("显示颜色历史"))
        self.cb_history.stateChanged.connect(self.save_settings)
        cl_hist.addWidget(self._make_setting_row(
            i18n.tr("显示颜色历史"),
            i18n.tr("在主界面显示最近取色的历史色块网格"),
            self.cb_history,
        ))

        grid_hist = QGridLayout()
        grid_hist.setSpacing(8)
        grid_hist.setColumnMinimumWidth(0, 110)
        grid_hist.setColumnStretch(1, 1)

        grid_hist.addWidget(QLabel(i18n.tr("历史列数")), 0, 0)
        self.combo_history_cols = NonScrollComboBox()
        self.combo_history_cols.addItems(["3", "4", "5", "6", "7", "8", "9", "10", "12", "14", "16"])
        self.combo_history_cols.currentTextChanged.connect(self.save_settings)
        self.combo_history_cols.setFixedWidth(60)
        grid_hist.addWidget(self.combo_history_cols, 0, 1)

        grid_hist.addWidget(QLabel(i18n.tr("历史行数")), 1, 0)
        self.combo_history_rows = NonScrollComboBox()
        self.combo_history_rows.addItems(["1", "2", "3", "4", "5", "6", "8"])
        self.combo_history_rows.currentTextChanged.connect(self.save_settings)
        self.combo_history_rows.setFixedWidth(60)
        grid_hist.addWidget(self.combo_history_rows, 1, 1)

        cl_hist.addLayout(grid_hist)
        page_sliders.addWidget(card_hist)

    def _build_panels_page(self, page_panels):
        # ═══════════════════ Page: 面板 ═══════════════════
        # ── Card 1: 排布模式 ──
        card_panels, cl_panels = self._begin_card(page_panels, i18n.tr("排布模式"))

        self.cb_panel_drag = QCheckBox(i18n.tr("面板抓手"))
        self.cb_panel_drag.setToolTip(
            i18n.tr("每块面板上方出现一条抓手：拖动它换位置，拖到窗口外或双击"
                    "就变成独立窗口，右键有更多操作"))
        self.cb_panel_drag.stateChanged.connect(self.save_settings)
        cl_panels.addWidget(self._make_setting_row(
            i18n.tr("面板抓手"),
            i18n.tr("每块面板上方显示抓手，可拖拽重排或拖出为独立窗口"),
            self.cb_panel_drag,
        ))

        self.cb_sliders_tabs = QCheckBox(i18n.tr("滑块组分页签叠放"))
        self.cb_sliders_tabs.setToolTip(i18n.tr("将多个色彩空间的滑块组转为 Tab 标签页叠放显示，节省垂直空间"))
        self.cb_sliders_tabs.stateChanged.connect(self.save_settings)
        cl_panels.addWidget(self._make_setting_row(
            i18n.tr("滑块组分页签叠放"),
            i18n.tr("将多个色彩空间滑块转为 Tab 标签页，节省垂直空间"),
            self.cb_sliders_tabs,
        ))
        page_panels.addWidget(card_panels)

        # ── Card 2: 独立窗口 ──
        card_float, cl_float = self._begin_card(page_panels, i18n.tr("独立窗口"))
        self.lbl_floating_panels = QLabel(i18n.tr("浮出的面板：无"))
        self.lbl_floating_panels.setWordWrap(True)
        cl_float.addWidget(self.lbl_floating_panels)
        row_panel_actions = QHBoxLayout()
        row_panel_actions.setSpacing(8)
        self.btn_dock_all_panels = QPushButton(i18n.tr("全部收回"))
        self.btn_dock_all_panels.setToolTip(
            i18n.tr("把所有浮出的面板收回主窗口 —— 拖到屏幕外找不着时用这个"))
        self.btn_dock_all_panels.clicked.connect(self.dock_all_floating_panels)
        row_panel_actions.addWidget(self.btn_dock_all_panels)
        self.btn_reset_panel_layout = QPushButton(i18n.tr("复位面板布局"))
        self.btn_reset_panel_layout.setToolTip(
            i18n.tr("忘掉拖出来的排布，回到上面这些开关决定的布局；"
                    "不影响显示/隐藏和浮出状态"))
        self.btn_reset_panel_layout.clicked.connect(self.reset_panel_arrangement)
        row_panel_actions.addWidget(self.btn_reset_panel_layout)
        row_panel_actions.addStretch()
        cl_float.addLayout(row_panel_actions)
        page_panels.addWidget(card_float)

        # ── Card 3: 间距微调 ──
        card_gap, cl_gap = self._begin_card(page_panels, i18n.tr("间距"))
        grid_gap = QGridLayout()
        grid_gap.setSpacing(8)
        grid_gap.setColumnMinimumWidth(0, 130)
        grid_gap.setColumnStretch(1, 1)
        grid_gap.addWidget(QLabel(i18n.tr("同组滑块间距")), 0, 0)
        grid_gap.addLayout(self._build_space_row("same"), 0, 1)
        grid_gap.addWidget(QLabel(i18n.tr("面板之间间距")), 1, 0)
        grid_gap.addLayout(self._build_space_row("diff"), 1, 1)
        grid_gap.addWidget(QLabel(i18n.tr("顶部以及底部距离")), 2, 0)
        grid_gap.addLayout(self._build_space_row("top"), 2, 1)
        cl_gap.addLayout(grid_gap)
        page_panels.addWidget(card_gap)

    def _build_interface_page(self, page_interface):
        # ═══════════════════ Page: 界面 ═══════════════════
        card_appear, cl_appear = self._begin_card(page_interface, i18n.tr("外观与主题"))
        self._card_layout_interface_bg = cl_appear  # stored for _make_eyedropper_row

        grid_appear = QGridLayout()
        grid_appear.setSpacing(8)
        grid_appear.setColumnMinimumWidth(0, 110)
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

        # Border visual theme (window frame / group frame / value box chrome).
        # First item is the "follow the slider style" policy.
        grid_appear.addWidget(QLabel(i18n.tr("边框样式")), 2, 0)
        self.combo_border_style = NonScrollComboBox()
        for _key, _display in list_border_theme_names():
            self.combo_border_style.addItem(_display, _key)
        self.combo_border_style.currentIndexChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_border_style, 2, 1)

        # Font size controls (- / +)
        grid_appear.addWidget(QLabel(i18n.tr("字体大小")), 3, 0)
        row_font_size = QHBoxLayout()
        row_font_size.setSpacing(4)
        self.btn_font_dec = self._make_step_button("-")
        self.btn_font_dec.clicked.connect(self.font_decrease)
        self.lbl_font_size = QLabel("100%")
        self.lbl_font_size.setObjectName("StepValueLabel")
        self.lbl_font_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_font_size.setFixedSize(48, 26)
        self.btn_font_inc = self._make_step_button("+")
        self.btn_font_inc.clicked.connect(self.font_increase)
        row_font_size.addWidget(self.btn_font_dec)
        row_font_size.addWidget(self.lbl_font_size)
        row_font_size.addWidget(self.btn_font_inc)
        row_font_size.addStretch()
        grid_appear.addLayout(row_font_size, 3, 1)

        cl_appear.addLayout(grid_appear)

        self.lbl_theme_status = QLabel("")
        self.lbl_theme_status.setObjectName("StatusHint")
        cl_appear.addWidget(self.lbl_theme_status)

        # Eyedropper control rows (visible only when "取色" theme is selected)
        self._make_eyedropper_row("bar", i18n.tr("边框颜色"), i18n.tr("绘画软件标题栏/边框的深色"))
        self._make_eyedropper_row("bg",  i18n.tr("背景颜色"), i18n.tr("绘画软件画布区域的浅色"))

        page_interface.addWidget(card_appear)

        # ── Card 2: 缩放与透明度 ──
        card_scale, cl_scale = self._begin_card(page_interface, i18n.tr("缩放与透明度"))
        grid_scale = QGridLayout()
        grid_scale.setSpacing(8)
        grid_scale.setColumnMinimumWidth(0, 110)
        grid_scale.setColumnStretch(1, 1)

        grid_scale.addWidget(QLabel(i18n.tr("界面缩放")), 0, 0)
        row_zoom = QHBoxLayout()
        self.zoom_slider = NonScrollSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("ScaleSlider")
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.valueChanged.connect(self.on_zoom_slider_changed)
        self.zoom_slider.sliderReleased.connect(self.on_zoom_slider_released)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(36)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_zoom.addWidget(self.zoom_slider)
        row_zoom.addWidget(self.lbl_zoom)
        grid_scale.addLayout(row_zoom, 0, 1)

        grid_scale.addWidget(QLabel(i18n.tr("背景不透明度")), 1, 0)
        row_opacity = QHBoxLayout()
        self.opacity_slider = NonScrollSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setObjectName("ScaleSlider")
        self.opacity_slider.setRange(CHROME_OPACITY_MIN, CHROME_OPACITY_MAX)
        self.opacity_slider.setSingleStep(CHROME_OPACITY_STEP)
        self.opacity_slider.setPageStep(CHROME_OPACITY_STEP * 2)
        self.opacity_slider.valueChanged.connect(self.on_opacity_slider_changed)
        self.opacity_slider.sliderReleased.connect(self.on_opacity_slider_released)
        self.lbl_opacity = QLabel("100%")
        self.lbl_opacity.setFixedWidth(36)
        self.lbl_opacity.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_opacity.addWidget(self.opacity_slider)
        row_opacity.addWidget(self.lbl_opacity)
        grid_scale.addLayout(row_opacity, 1, 1)

        cl_scale.addLayout(grid_scale)
        page_interface.addWidget(card_scale)

    def _build_window_page(self, page_window):
        # ═══════════════════ Page: 窗口 ═══════════════════
        # ── Card 1: 取色交互与焦点 ──
        card_focus, cl_focus = self._begin_card(page_window, i18n.tr("取色交互与焦点"))

        self.cb_no_focus = QCheckBox(i18n.tr("无焦点取色模式"))
        self.cb_no_focus.setToolTip(i18n.tr("开启后不会抢占绘画软件的键盘焦点，适合边画边取色"))
        self.cb_no_focus.clicked.connect(self.on_no_focus_clicked)
        cl_focus.addWidget(self._make_setting_row(
            i18n.tr("无焦点取色模式"),
            i18n.tr("开启后不会抢占绘画软件键盘焦点，边画边取色更顺畅"),
            self.cb_no_focus
        ))

        self.cb_only_drawing = QCheckBox(i18n.tr("仅在绘画软件前台时显示"))
        self.cb_only_drawing.setToolTip(i18n.tr("绘画软件不在前台时自动隐藏悬浮面板"))
        self.cb_only_drawing.stateChanged.connect(self.save_settings)
        cl_focus.addWidget(self._make_setting_row(
            i18n.tr("仅在绘画软件前台时显示"),
            i18n.tr("绘画软件退至后台时自动隐藏取色面板"),
            self.cb_only_drawing
        ))

        self.cb_hide_on_menu = QCheckBox(i18n.tr("下拉菜单展开时自动隐藏"))
        self.cb_hide_on_menu.setToolTip(
            i18n.tr("开启后，在 CSP 等绘画软件点开顶部下拉菜单、右键菜单或下拉列表时自动隐藏色环与浮窗，避免遮挡菜单项")
        )
        self.cb_hide_on_menu.stateChanged.connect(self.save_settings)
        cl_focus.addWidget(self._make_setting_row(
            i18n.tr("下拉菜单展开时自动隐藏"),
            i18n.tr("绘画软件展开顶部菜单或右键下拉菜单时临时隐藏面板，关闭菜单后自动恢复"),
            self.cb_hide_on_menu
        ))

        page_window.addWidget(card_focus)

        # ── Card 2: 窗口显示与锁定 ──
        card_frame, cl_frame = self._begin_card(page_window, i18n.tr("窗口显示与锁定"))

        self.cb_show_title_bar = QCheckBox(i18n.tr("显示标题栏"))
        self.cb_show_title_bar.setToolTip(i18n.tr("隐藏后顶部边框与四周一致；可通过快捷键或托盘菜单恢复"))
        self.cb_show_title_bar.stateChanged.connect(self.save_settings)
        cl_frame.addWidget(self._make_setting_row(
            i18n.tr("显示标题栏"),
            i18n.tr("隐藏后顶部边框与四周一致；可通过快捷键或托盘恢复"),
            self.cb_show_title_bar
        ))

        self.cb_show_window_border = QCheckBox(i18n.tr("显示窗口边框"))
        self.cb_show_window_border.setToolTip(
            i18n.tr("关闭后隐藏主窗口与浮窗外边框；隐藏标题栏时顶部也不会保留边框")
        )
        self.cb_show_window_border.stateChanged.connect(self.save_settings)
        cl_frame.addWidget(self._make_setting_row(
            i18n.tr("显示窗口边框"),
            i18n.tr("显示或隐藏主窗口与浮窗外边框（不影响分组框与滑块边框样式）"),
            self.cb_show_window_border
        ))

        self.cb_lock_position = QCheckBox(i18n.tr("锁定窗口位置"))
        self.cb_lock_position.setToolTip(i18n.tr("开启后不能拖动窗口"))
        self.cb_lock_position.stateChanged.connect(self.save_settings)
        cl_frame.addWidget(self._make_setting_row(
            i18n.tr("锁定窗口位置"),
            i18n.tr("固定当前屏幕位置，禁止鼠标拖拽移动"),
            self.cb_lock_position
        ))

        self.cb_lock_size = QCheckBox(i18n.tr("固定窗口大小"))
        self.cb_lock_size.setToolTip(i18n.tr("锁定后窗口不能手动缩放"))
        self.cb_lock_size.stateChanged.connect(self.save_settings)
        cl_frame.addWidget(self._make_setting_row(
            i18n.tr("固定窗口大小"),
            i18n.tr("锁定窗口尺寸，禁止鼠标拉伸边缘调整"),
            self.cb_lock_size
        ))

        page_window.addWidget(card_frame)

    def _build_system_env_card(self, parent_layout):
        # ── Card: 系统与环境 ──
        card_env, cl_env = self._begin_card(parent_layout, i18n.tr("系统与环境"))

        self.cb_taskbar_icon = QCheckBox(i18n.tr("任务栏图标"))
        self.cb_taskbar_icon.setToolTip(i18n.tr("在任务栏显示 Colorink 图标；关闭后只保留托盘图标"))
        self.cb_taskbar_icon.stateChanged.connect(self.save_settings)
        cl_env.addWidget(self._make_setting_row(
            i18n.tr("任务栏图标"),
            i18n.tr("在任务栏显示独立图标；关闭后仅保留系统托盘图标"),
            self.cb_taskbar_icon
        ))

        self.cb_autostart = QCheckBox(i18n.tr("开机自启动"))
        self.cb_autostart.setToolTip(i18n.tr("开机后自动启动 Colorink（仅当前用户，不需要管理员权限）"))
        self.cb_autostart.stateChanged.connect(self.save_settings)
        cl_env.addWidget(self._make_setting_row(
            i18n.tr("开机自启动"),
            i18n.tr("开机登录系统后自动在后台启动 Colorink"),
            self.cb_autostart
        ))

        self._autostart_status_row = QWidget()
        autostart_status_layout = QHBoxLayout(self._autostart_status_row)
        autostart_status_layout.setContentsMargins(0, 0, 0, 0)
        autostart_status_layout.setSpacing(8)
        self.lbl_autostart_status = QLabel("")
        self.lbl_autostart_status.setWordWrap(True)
        self._set_label_state(self.lbl_autostart_status, "hint")
        self.btn_autostart_repair = QPushButton(i18n.tr("恢复自启动"))
        self.btn_autostart_repair.setToolTip(
            i18n.tr("重新启用被 Windows 任务管理器禁用的开机自启动项")
        )
        self.btn_autostart_repair.clicked.connect(self._on_repair_autostart)
        self.btn_autostart_repair.setVisible(False)
        autostart_status_layout.addWidget(self.lbl_autostart_status, 1)
        autostart_status_layout.addWidget(self.btn_autostart_repair)
        self._autostart_status_row.setVisible(False)
        cl_env.addWidget(self._autostart_status_row)

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
        cl_env.addWidget(self._make_setting_row(
            i18n.tr("界面语言"),
            i18n.tr("切换 Colorink 显示的语言"),
            self.cmb_language
        ))

        parent_layout.addWidget(card_env)
        return card_env

    def _refresh_autostart_status(self):
        if not hasattr(self, "_autostart_status_row"):
            return
        enabled = bool(self.cfg.get("openAtLogin", False))
        blocked = enabled and autostart.is_autostart_blocked_by_windows()
        supported = autostart.is_autostart_supported()

        if blocked and supported:
            self.lbl_autostart_status.setText(
                i18n.tr("已被 Windows 启动管理禁用，当前不会随开机启动")
            )
            self._set_label_state(self.lbl_autostart_status, "warning")
            self.btn_autostart_repair.setVisible(True)
            self._autostart_status_row.setVisible(True)
        elif blocked:
            self.lbl_autostart_status.setText(
                i18n.tr("已被 Windows 启动管理禁用，请运行打包版并点击恢复或在任务管理器中重新启用")
            )
            self._set_label_state(self.lbl_autostart_status, "warning")
            self.btn_autostart_repair.setVisible(False)
            self._autostart_status_row.setVisible(True)
        elif not supported:
            self.lbl_autostart_status.setText(
                i18n.tr("当前为源码运行模式，开机自启动仅在打包版中可用")
            )
            self._set_label_state(self.lbl_autostart_status, "hint")
            self.btn_autostart_repair.setVisible(False)
            self._autostart_status_row.setVisible(True)
        else:
            self.btn_autostart_repair.setVisible(False)
            self._autostart_status_row.setVisible(False)

    def _on_repair_autostart(self):
        if autostart.apply_autostart(True):
            self.cfg["openAtLogin"] = True
            if hasattr(self, "_persist_config"):
                self._persist_config()
        self._refresh_autostart_status()

    def _make_grayscale_switch_row(
        self,
        title_text: str,
        status_attr: str,
        sub_attr: str,
        btn_attr: str,
        btn_initial_text: str,
        slot,
    ) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(2, 3, 2, 3)
        row_layout.setSpacing(8)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)

        top_line = QHBoxLayout()
        top_line.setContentsMargins(0, 0, 0, 0)
        top_line.setSpacing(6)
        title_lbl = QLabel(title_text)
        title_lbl.setObjectName("RowTitle")
        status_lbl = QLabel(i18n.tr("未开启"))
        self._set_label_state(status_lbl, "muted")
        top_line.addWidget(title_lbl)
        top_line.addWidget(status_lbl)
        top_line.addStretch()

        sub_lbl = QLabel("")
        sub_lbl.setObjectName("RowSubtitle")
        sub_lbl.setWordWrap(True)

        left_col.addLayout(top_line)
        left_col.addWidget(sub_lbl)
        row_layout.addLayout(left_col, 1)

        btn = QPushButton(btn_initial_text)
        btn.setObjectName("GrayscaleToggleBtn")
        btn.setMinimumWidth(92)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        row_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        setattr(self, status_attr, status_lbl)
        setattr(self, sub_attr, sub_lbl)
        setattr(self, btn_attr, btn)
        return row_widget

    def _build_grayscale_quick_card(self, parent_layout):
        card_quick, cl_quick = self._begin_card(parent_layout, i18n.tr("滤镜开关与状态"))
        cl_quick.addWidget(self._make_grayscale_switch_row(
            i18n.tr("全屏灰度滤镜"),
            "lbl_fullscreen_grayscale_status",
            "lbl_fullscreen_grayscale_sub",
            "btn_toggle_fullscreen_grayscale",
            i18n.tr("开启全屏灰度"),
            self._on_toggle_fullscreen_grayscale,
        ))
        cl_quick.addWidget(self._make_grayscale_switch_row(
            i18n.tr("区域灰度滤镜"),
            "lbl_region_grayscale_status",
            "lbl_region_grayscale_sub",
            "btn_toggle_region_grayscale",
            i18n.tr("开启区域灰度"),
            self._on_toggle_region_grayscale,
        ))
        parent_layout.addWidget(card_quick)
        return card_quick

    def _build_grayscale_card(self, parent_layout):
        # ═══════════════════ 渲染设置 ═══════════════════
        card_gray, cl_gray = self._begin_card(parent_layout, i18n.tr("渲染设置"))

        grid_gray = QGridLayout()
        grid_gray.setSpacing(8)
        grid_gray.setColumnMinimumWidth(0, 96)
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
        self.combo_grayscale_backend.addItem(i18n.tr("OKLCh (DComp 显存直通)"), "dcomp")
        self.combo_grayscale_backend.addItem(i18n.tr("系统 Luma (Mag)"), "mag")
        self.combo_grayscale_backend.setToolTip(
            i18n.tr("OKLCh (DComp 显存直通)：DirectComposition 低延迟渲染，支持 OKLCh / Luma 与多区域（推荐）；\n"
            "系统 Luma (Mag)：Windows 硬件颜色矩阵，仅支持 BT.709 亮度。"))
        self.combo_grayscale_backend.currentTextChanged.connect(self._on_grayscale_backend_changed)
        grid_gray.addWidget(self.combo_grayscale_backend, 2, 1)

        cl_gray.addLayout(grid_gray)

        self.lbl_grayscale_backend_note = QLabel("")
        self.lbl_grayscale_backend_note.setObjectName("RowSubtitle")
        self.lbl_grayscale_backend_note.setWordWrap(True)
        cl_gray.addWidget(self.lbl_grayscale_backend_note)

        parent_layout.addWidget(card_gray)
        return card_gray

    def _build_grayscale_regions_card(self, parent_layout):
        card_regions, cl_regions = self._begin_card(parent_layout, i18n.tr("区域灰度范围"))
        card_regions.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._card_grayscale_regions = card_regions

        row_summary = QHBoxLayout()
        row_summary.setContentsMargins(0, 0, 0, 0)
        row_summary.setSpacing(8)
        lbl_scope_title = QLabel(i18n.tr("当前范围"))
        lbl_scope_title.setMinimumWidth(96)
        self.lbl_grayscale_region = QLabel(i18n.tr("默认最大化"))
        self._set_label_state(self.lbl_grayscale_region, "muted")
        self.lbl_grayscale_region.setWordWrap(True)
        row_summary.addWidget(lbl_scope_title, 0, Qt.AlignmentFlag.AlignVCenter)
        row_summary.addWidget(self.lbl_grayscale_region, 1, Qt.AlignmentFlag.AlignVCenter)
        cl_regions.addLayout(row_summary)

        row_actions = QHBoxLayout()
        row_actions.setContentsMargins(0, 0, 0, 0)
        row_actions.setSpacing(6)
        self.btn_select_grayscale_region = QPushButton(i18n.tr("框选区域"))
        self.btn_select_grayscale_region.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_grayscale_region.clicked.connect(self._on_select_grayscale_region)
        self.btn_reset_grayscale_region = QPushButton(i18n.tr("重置最大化"))
        self.btn_reset_grayscale_region.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_grayscale_region.clicked.connect(self._on_reset_grayscale_region)
        row_actions.addWidget(self.btn_select_grayscale_region)
        row_actions.addWidget(self.btn_reset_grayscale_region)
        row_actions.addStretch()
        cl_regions.addLayout(row_actions)

        self._grayscale_regions_layout = QVBoxLayout()
        self._grayscale_regions_layout.setContentsMargins(0, 2, 0, 0)
        self._grayscale_regions_layout.setSpacing(6)
        cl_regions.addLayout(self._grayscale_regions_layout)

        parent_layout.addWidget(card_regions)
        return card_regions

    def _build_filter_page(self, page_filter):
        self._build_grayscale_quick_card(page_filter)
        self._build_grayscale_card(page_filter)
        self._build_grayscale_regions_card(page_filter)

    def _on_wheel_tab_check_changed(self, _state=None):
        """Keep at least one wheel-module tab checked, then save."""
        checks = getattr(self, "wheel_tab_checks", {})
        if checks and not any(cb.isChecked() for cb in checks.values()):
            sender = self.sender() if hasattr(self, "sender") else None
            target = sender if sender in checks.values() else next(iter(checks.values()))
            target.blockSignals(True)
            target.setChecked(True)
            target.blockSignals(False)
            return
        self.save_settings()

    def _refresh_wheel_tab_checks(self):
        checks = getattr(self, "wheel_tab_checks", None)
        if not checks:
            return
        from ui.window.module_defs import normalize_wheel_module_tabs
        enabled = set(normalize_wheel_module_tabs(
            self.cfg.get("wheelModuleTabs"), self.cfg.get("wheelModuleOrder"),
            self.cfg.get("colorSpaceModule", "hsv")))
        for key, cb in checks.items():
            cb.blockSignals(True)
            cb.setChecked(key in enabled)
            cb.blockSignals(False)

    def _wheel_tabs_from_checks(self):
        checks = getattr(self, "wheel_tab_checks", None)
        if not checks:
            return None
        from ui.window.module_defs import normalize_wheel_module_tabs
        enabled = [k for k, cb in checks.items() if cb.isChecked()]
        return normalize_wheel_module_tabs(
            enabled, self.cfg.get("wheelModuleOrder"),
            self.cfg.get("colorSpaceModule", "hsv"))

    def _build_picker_page(self, page_picker):
        # ═══════════════════ Page: 色环 ═══════════════════
        # ── Card 1: 色环与切片 ──
        card_wheel, cl_wheel = self._begin_card(page_picker, i18n.tr("色环与切片"))
        grid_wheel = QGridLayout()
        grid_wheel.setSpacing(8)
        grid_wheel.setColumnMinimumWidth(0, 110)
        grid_wheel.setColumnStretch(1, 1)

        grid_wheel.addWidget(QLabel(i18n.tr("色环模块")), 0, 0)
        self.combo_module = NonScrollComboBox()
        self.combo_module.addItems(["HSV", "VHSV", "HLS", "RGB", "LCH"])
        self.combo_module.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_module, 0, 1)

        grid_wheel.addWidget(QLabel(i18n.tr("前景/背景色位置")), 1, 0)
        self.combo_pos = NonScrollComboBox()
        self.combo_pos.addItem(i18n.tr("左上角"), "top-left")
        self.combo_pos.addItem(i18n.tr("左下角"), "bottom-left")
        self.combo_pos.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_pos, 1, 1)

        cl_wheel.addLayout(grid_wheel)

        self.cb_show_module_btn = QCheckBox(i18n.tr("显示色环模块标签栏"))
        self.cb_show_module_btn.setToolTip(i18n.tr("在取色区顶部显示色环模块 / LAB 模式标签栏，可拖拽标签排序"))
        self.cb_show_module_btn.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self._make_setting_row(
            i18n.tr("显示模块标签栏"),
            i18n.tr("在取色区顶部显示色环模块 / LAB 模式标签栏，可拖拽标签排序"),
            self.cb_show_module_btn
        ))

        # Which wheel modules appear as tabs (at least one stays checked)
        row_tabs = QHBoxLayout()
        row_tabs.setSpacing(8)
        row_tabs.addWidget(QLabel(i18n.tr("标签栏显示的模块")))
        self.wheel_tab_checks = {}
        for key, label in (("hsv", "HSV"), ("vhsv", "VHSV"), ("hls", "HLS"),
                           ("rgb", "RGB"), ("lch", "LCH")):
            cb = QCheckBox(label)
            cb.stateChanged.connect(self._on_wheel_tab_check_changed)
            row_tabs.addWidget(cb)
            self.wheel_tab_checks[key] = cb
        row_tabs.addStretch()
        cl_wheel.addLayout(row_tabs)

        # What travels with a wheel page when it is torn off. The wheel is
        # always there; these two decorations can be switched off.
        row_float = QHBoxLayout()
        row_float.setSpacing(8)
        row_float.addWidget(QLabel(i18n.tr("浮出色图窗口")))
        self.cb_floating_wheel_swatches = QCheckBox(
            i18n.tr("浮出色图显示前景/背景色"))
        self.cb_floating_wheel_buttons = QCheckBox(
            i18n.tr("浮出色图显示模式按钮"))
        self.cb_floating_wheel_swatches.setToolTip(
            i18n.tr("控制拖出后的色图浮窗里是否带前景/背景色、透明色块和模式按钮"))
        self.cb_floating_wheel_buttons.setToolTip(
            i18n.tr("控制拖出后的色图浮窗里是否带前景/背景色、透明色块和模式按钮"))
        self.cb_floating_wheel_swatches.stateChanged.connect(self.save_settings)
        self.cb_floating_wheel_buttons.stateChanged.connect(self.save_settings)
        row_float.addWidget(self.cb_floating_wheel_swatches)
        row_float.addWidget(self.cb_floating_wheel_buttons)
        row_float.addStretch()
        cl_wheel.addLayout(row_float)

        self.cb_flip_wheel = QCheckBox(i18n.tr("水平翻转色环"))
        self.cb_flip_wheel.setToolTip(i18n.tr("水平镜像色环（颜色排列左右翻转）"))
        self.cb_flip_wheel.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self._make_setting_row(
            i18n.tr("水平翻转色环"),
            i18n.tr("水平镜像色环（颜色排列左右翻转）"),
            self.cb_flip_wheel
        ))

        self.cb_show_color_readout = QCheckBox(i18n.tr("显示颜色数值"))
        _readout_tip = i18n.tr(
            "在色轮与 LAB 视图显示当前颜色数值（点击可切换 RGB / HSV / HLS / LAB / OKLab / OKLCh / HEX）"
        )
        self.cb_show_color_readout.setToolTip(_readout_tip)
        self.cb_show_color_readout.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self._make_setting_row(
            i18n.tr("显示颜色数值"),
            _readout_tip,
            self.cb_show_color_readout,
        ))

        # Ringless mode settings
        self.ringless_settings = RinglessSettingsWidget()
        self.ringless_settings.changed.connect(self.save_settings)
        cl_wheel.addWidget(self.ringless_settings)

        page_picker.addWidget(card_wheel)

        # ── Card 2: 全局屏幕取色 ──
        card_pz, cl_pz = self._begin_card(page_picker, i18n.tr("全局屏幕取色"))
        grid_pz = QGridLayout()
        grid_pz.setSpacing(8)
        grid_pz.setColumnMinimumWidth(0, 110)
        grid_pz.setColumnStretch(1, 1)

        grid_pz.addWidget(QLabel(i18n.tr("吸管放大镜倍率")), 0, 0)
        row_picker_zoom = QHBoxLayout()
        row_picker_zoom.setSpacing(4)
        self.btn_zoom_dec = self._make_step_button("-")
        self.btn_zoom_dec.clicked.connect(self.zoom_decrease)
        self.lbl_picker_zoom = QLabel("6×")
        self.lbl_picker_zoom.setObjectName("StepValueLabel")
        self.lbl_picker_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_picker_zoom.setFixedSize(48, 26)
        self.btn_zoom_inc = self._make_step_button("+")
        self.btn_zoom_inc.clicked.connect(self.zoom_increase)
        row_picker_zoom.addWidget(self.btn_zoom_dec)
        row_picker_zoom.addWidget(self.lbl_picker_zoom)
        row_picker_zoom.addWidget(self.btn_zoom_inc)
        row_picker_zoom.addStretch()
        grid_pz.addLayout(row_picker_zoom, 0, 1)

        cl_pz.addLayout(grid_pz)
        lbl_pz_hint = QLabel(i18n.tr(
            "按下「全局取色」快捷键取色时，鼠标跟随放大镜的默认缩放倍率（取色时也可滚轮调节 2× ~ 20×）"
        ))
        lbl_pz_hint.setObjectName("TipDesc")
        lbl_pz_hint.setWordWrap(True)
        cl_pz.addWidget(lbl_pz_hint)
        page_picker.addWidget(card_pz)

    def _build_lab_page(self, page_lab):
        # ═══════════════════ Page: LAB ═══════════════════
        # ── Card 1: LAB 视图与调和 ──
        card_lab, cl_lab = self._begin_card(page_lab, i18n.tr("LAB 视图与调和"))
        grid_lab = QGridLayout()
        grid_lab.setSpacing(8)
        grid_lab.setColumnMinimumWidth(0, 110)
        grid_lab.setColumnStretch(1, 1)

        grid_lab.addWidget(QLabel(i18n.tr("LAB 视图模式")), 0, 0)
        self.combo_viz_mode = NonScrollComboBox()
        self.combo_viz_mode.addItem(i18n.tr("LAB 色彩空间"), "lab")
        self.combo_viz_mode.addItem(i18n.tr("OKLab 色彩空间"), "oklab")
        self.combo_viz_mode.currentTextChanged.connect(self.save_settings)
        grid_lab.addWidget(self.combo_viz_mode, 0, 1)

        grid_lab.addWidget(QLabel(i18n.tr("LAB 视图形状")), 1, 0)
        self.combo_lab_shape = NonScrollComboBox()
        self.combo_lab_shape.addItem(i18n.tr("方形"), "square")
        self.combo_lab_shape.addItem(i18n.tr("圆形"), "disc")
        self.combo_lab_shape.currentTextChanged.connect(self.save_settings)
        grid_lab.addWidget(self.combo_lab_shape, 1, 1)

        grid_lab.addWidget(QLabel(i18n.tr("LAB 调和模式")), 2, 0)
        self.combo_lab_harmony = NonScrollComboBox()
        for mode, label in HARMONY_MODE_NAMES.items():
            self.combo_lab_harmony.addItem(i18n.tr(label), mode)
        self.combo_lab_harmony.currentTextChanged.connect(self.save_settings)
        grid_lab.addWidget(self.combo_lab_harmony, 2, 1)
        cl_lab.addLayout(grid_lab)

        self.cb_show_lab_toggle = QCheckBox(i18n.tr("显示 LAB 切换按钮"))
        self.cb_show_lab_toggle.setToolTip(i18n.tr("在色轮/LAB区域显示色轮与LAB之间的切换按钮"))
        self.cb_show_lab_toggle.stateChanged.connect(self.save_settings)
        cl_lab.addWidget(self._make_setting_row(
            i18n.tr("显示 LAB 切换按钮"),
            i18n.tr("在色轮区域显示色轮与 LAB 之间的切换按钮"),
            self.cb_show_lab_toggle
        ))

        self.cb_show_lab_shape_btn = QCheckBox(i18n.tr("显示 LAB 视图形状按钮"))
        self.cb_show_lab_shape_btn.setToolTip(i18n.tr("在 LAB 区域显示方形/圆形切换按钮"))
        self.cb_show_lab_shape_btn.stateChanged.connect(self.save_settings)
        cl_lab.addWidget(self._make_setting_row(
            i18n.tr("显示形状切换按钮"),
            i18n.tr("在 LAB 区域显示方形/圆形切换按钮"),
            self.cb_show_lab_shape_btn
        ))

        self.cb_show_lab_harmony_btn = QCheckBox(i18n.tr("显示 LAB 调和按钮"))
        self.cb_show_lab_harmony_btn.setToolTip(i18n.tr("在 LAB 区域显示互补/近似/三等分等调和模式按钮"))
        self.cb_show_lab_harmony_btn.stateChanged.connect(self.save_settings)
        cl_lab.addWidget(self._make_setting_row(
            i18n.tr("显示调和切换按钮"),
            i18n.tr("在 LAB 区域显示互补/近似/三等分等调和模式按钮"),
            self.cb_show_lab_harmony_btn
        ))

        self.cb_show_lab_lightness = QCheckBox(i18n.tr("显示 LAB 亮度滑块"))
        self.cb_show_lab_lightness.setToolTip(i18n.tr("显示/隐藏 LAB 区域的亮度滑块"))
        self.cb_show_lab_lightness.stateChanged.connect(self.save_settings)
        cl_lab.addWidget(self._make_setting_row(
            i18n.tr("显示 LAB 亮度滑块"),
            i18n.tr("在 LAB 区域显示独立的 L 亮度调节滑块"),
            self.cb_show_lab_lightness
        ))

        self.cb_show_lab_checkerboard = QCheckBox(i18n.tr("显示 LAB 背景灰白网格"))
        self.cb_show_lab_checkerboard.setToolTip(i18n.tr("在 LAB 区域背景显示灰白透明度网格"))
        self.cb_show_lab_checkerboard.stateChanged.connect(self.save_settings)
        cl_lab.addWidget(self._make_setting_row(
            i18n.tr("显示灰白网格底纹"),
            i18n.tr("在 LAB 区域背景显示半透明棋盘网格"),
            self.cb_show_lab_checkerboard
        ))

        page_lab.addWidget(card_lab)




