import json
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import autostart, config, i18n
from ui.chrome_opacity import (
    CHROME_OPACITY_KEY,
    clamp_chrome_opacity,
    resolve_chrome_opacity,
)
from ui.hotkey_button import display_hotkey
from ui.ringless_mode import RinglessConfig
from ui.settings.appearance_panel import AppearancePanelMixin
from ui.settings.settings_helpers import SettingsHelpersMixin
from ui.settings.sync_panel import SyncPanelMixin
from ui.settings.tooltips import apply_settings_tooltips
from ui.settings.update_panel import UpdatePanelMixin
from ui.window.module_defs import ALL_SLIDERS

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class SettingsSidebar(UpdatePanelMixin, SyncPanelMixin, AppearancePanelMixin,
                      SettingsHelpersMixin, QWidget):
    settingChanged = pyqtSignal()
    pickingThemePoint = pyqtSignal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = cast("MainWindow", parent)
        self.cfg = config.load_hotkey_config()
        self._last_persisted = ""
        self._last_settings_tab = 0
        self.init_ui()
        self.refresh_ui()


    def retranslate(self):
        """Rebuild the sidebar UI to reflect the newly active language."""
        row = self.nav.currentRow() if hasattr(self, "nav") else 0
        self.init_ui()
        self.refresh_ui()
        if 0 <= row < self.nav.count():
            self.nav.setCurrentRow(row)

    def init_ui(self):
        # Rebuildable: clear any previous content so retranslate() can re-run
        # this method without stacking a second layout on the widget.
        if hasattr(self, "_layout") and self._layout is not None:
            self._clear_layout(self._layout)
        else:
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(8, 8, 8, 8)
            self._layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        # ── Left rail: category navigation (like CSP 環境設定) ──
        self.nav = QListWidget()
        self.nav.setObjectName("NavRail")
        self.nav.setFixedWidth(108)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setUniformItemSizes(True)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        for text, kind in [
            ("快捷键", "hotkeys"),
            ("界面", "interface"),
            ("窗口", "window"),
            ("色环", "picker"),
            ("LAB", "lab"),
            ("滑块", "sliders"),
            ("面板", "panels"),
            ("灰度滤镜", "filter"),
            ("同步", "software"),
            ("系统", "about"),
        ]:
            item = QListWidgetItem(i18n.tr(text))
            item.setData(Qt.ItemDataRole.UserRole, kind)
            item.setSizeHint(QSize(0, 30))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self.nav)

        # ── Right: stacked pages ──
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self._layout.addLayout(body)
        self.stack.currentChanged.connect(self._on_page_changed)

        self._page_layouts = {}

        # Create 10 categorized pages
        page_hotkeys   = self._make_page("快捷键")
        page_interface = self._make_page("界面")
        page_window    = self._make_page("窗口")
        page_picker    = self._make_page("色环")
        page_lab       = self._make_page("LAB")
        page_sliders   = self._make_page("滑块")
        page_panels    = self._make_page("面板")
        page_filter    = self._make_page("灰度滤镜")
        page_sync      = self._make_page("同步")
        page_about     = self._make_page("系统")

        # ═══════════════════ Page 1: 快捷键 ═══════════════════
        card_hk, cl_hk = self._begin_card(page_hotkeys, i18n.tr("全局热键"))

        grid_hotkeys = QGridLayout()
        grid_hotkeys.setSpacing(8)
        grid_hotkeys.setColumnMinimumWidth(0, 140)
        grid_hotkeys.setColumnStretch(1, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("全局取色")), 0, 0)
        row_pick, self.btn_pick, self.btn_pick_none = self._make_hotkey_row(
            "pickKey", self.cfg.get("pickKey", "Ctrl+Alt+Q"), self.save_hotkeys,
            allow_mouse=True)
        grid_hotkeys.addWidget(row_pick, 0, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("隐藏窗口")), 1, 0)
        row_hide, self.btn_hide, self.btn_hide_none = self._make_hotkey_row(
            "hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+Alt+Y"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_hide, 1, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("跟随鼠标")), 2, 0)
        row_follow_hk, self.btn_follow, self.btn_follow_none = self._make_hotkey_row(
            "followMouseKey", self.cfg.get("followMouseKey", "Ctrl+Alt+J"),
            self.save_hotkeys, allow_mouse=True)
        row_follow = QHBoxLayout()
        row_follow.setContentsMargins(0, 0, 0, 0)
        row_follow.setSpacing(6)
        self.cb_follow_mouse = QCheckBox(i18n.tr("启用"))
        self.cb_follow_mouse.stateChanged.connect(self.save_settings)
        row_follow.addWidget(row_follow_hk, 1)
        row_follow.addWidget(self.cb_follow_mouse, 0)
        grid_hotkeys.addLayout(row_follow, 2, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("灰度滤镜")), 3, 0)
        row_grayscale, self.btn_grayscale, self.btn_grayscale_none = self._make_hotkey_row(
            "grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+Alt+D"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_grayscale, 3, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("区域灰度滤镜")), 4, 0)
        row_region_grayscale, self.btn_region_grayscale, self.btn_region_grayscale_none = self._make_hotkey_row(
            "regionGrayscaleKey", self.cfg.get("regionGrayscaleKey", "Ctrl+Alt+G"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_region_grayscale, 4, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("切换标签页（色轮/滑条）")), 5, 0)
        row_lab_toggle, self.btn_lab_toggle, self.btn_lab_toggle_none = self._make_hotkey_row(
            "toggleLabKey", self.cfg.get("toggleLabKey", "Space"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_lab_toggle, 5, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB 切换（色轮区）")), 6, 0)
        row_lab_local, self.btn_lab_local, self.btn_lab_local_none = self._make_hotkey_row(
            "toggleLabLocalKey", self.cfg.get("toggleLabLocalKey", ""),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_lab_local, 6, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB 切换（全局）")), 7, 0)
        row_lab_global, self.btn_lab_global, self.btn_lab_global_none = self._make_hotkey_row(
            "toggleLabGlobalKey", self.cfg.get("toggleLabGlobalKey", "Ctrl+Alt+L"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_lab_global, 7, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("切换色轮标签（全局）")), 8, 0)
        row_wheel_tab_global, self.btn_wheel_tab_global, self.btn_wheel_tab_global_none = self._make_hotkey_row(
            "toggleWheelTabGlobalKey", self.cfg.get("toggleWheelTabGlobalKey", ""),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_wheel_tab_global, 8, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("切换滑条标签（全局）")), 9, 0)
        row_slider_tab_global, self.btn_slider_tab_global, self.btn_slider_tab_global_none = self._make_hotkey_row(
            "toggleSliderTabGlobalKey", self.cfg.get("toggleSliderTabGlobalKey", ""),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_slider_tab_global, 9, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("标题栏显示/隐藏")), 10, 0)
        row_title_bar, self.btn_title_bar, self.btn_title_bar_none = self._make_hotkey_row(
            "toggleTitleBarKey", self.cfg.get("toggleTitleBarKey", "Ctrl+Alt+K"),
            self.save_hotkeys, allow_mouse=True)
        grid_hotkeys.addWidget(row_title_bar, 10, 1)

        cl_hk.addLayout(grid_hotkeys)

        self.cb_zone_filter = QCheckBox(i18n.tr("排除任务栏与托盘区域"))
        self.cb_zone_filter.stateChanged.connect(self.save_settings)
        cl_hk.addWidget(self.cb_zone_filter)
        lbl_zone_hint = QLabel(i18n.tr(
            "鼠标键热键在任务栏 / 托盘 / 系统菜单区域内不触发，避免原生右键菜单被覆盖"))
        lbl_zone_hint.setObjectName("TipDesc")
        lbl_zone_hint.setWordWrap(True)
        cl_hk.addWidget(lbl_zone_hint)

        lbl_fallback = QLabel(i18n.tr("兜底热键：Ctrl+Alt+Shift+, 随时打开设置（不可解绑）"))
        lbl_fallback.setObjectName("TipDesc")
        lbl_fallback.setWordWrap(True)
        cl_hk.addWidget(lbl_fallback)

        page_hotkeys.addWidget(card_hk)

        self._floating_hotkey_entries = {}
        self.card_floating_hotkeys, cl_float_hk = self._begin_card(
            page_hotkeys, i18n.tr("浮窗快捷键"))
        lbl_float_hk_hint = QLabel(i18n.tr(
            "仅为当前打开的浮窗配置：可勾选跟随主窗口全局快捷键，或为该浮窗单独绑定专属热键"))
        lbl_float_hk_hint.setObjectName("TipDesc")
        lbl_float_hk_hint.setWordWrap(True)
        cl_float_hk.addWidget(lbl_float_hk_hint)
        self._floating_hotkeys_rows = QVBoxLayout()
        self._floating_hotkeys_rows.setContentsMargins(0, 0, 0, 0)
        self._floating_hotkeys_rows.setSpacing(10)
        cl_float_hk.addLayout(self._floating_hotkeys_rows)
        self.card_floating_hotkeys.setVisible(False)
        page_hotkeys.addWidget(self.card_floating_hotkeys)

        # ── Card 2: 取色视窗快捷操作 ──
        card_tips, cl_tips = self._begin_card(page_hotkeys, i18n.tr("取色视窗快捷操作"))
        grid_tips = QGridLayout()
        grid_tips.setSpacing(8)
        grid_tips.setColumnMinimumWidth(0, 80)
        grid_tips.setColumnStretch(1, 1)
        tips_data = [
            ("Alt", i18n.tr("按住或松开临时冻结取色画面")),
            (i18n.tr("滚轮"), i18n.tr("上下滚动调节放大镜倍率 (2× ~ 20×)")),
            ("Shift", i18n.tr("按住锁定水平或垂直采样轴")),
            (i18n.tr("空格"), i18n.tr("按住临时隐藏准星，查看底层像素")),
        ]
        for idx, (key_label, tip_desc) in enumerate(tips_data):
            lbl_k = QLabel(key_label)
            lbl_k.setObjectName("TipKey")
            lbl_d = QLabel(tip_desc)
            lbl_d.setObjectName("TipDesc")
            lbl_d.setWordWrap(True)
            grid_tips.addWidget(lbl_k, idx, 0)
            grid_tips.addWidget(lbl_d, idx, 1)
        cl_tips.addLayout(grid_tips)
        page_hotkeys.addWidget(card_tips)

        # ═══════════════════ Page 2: 界面 ═══════════════════
        self._build_interface_page(page_interface)

        # ═══════════════════ Page 3: 窗口 ═══════════════════
        self._build_window_page(page_window)

        # ═══════════════════ Page 4: 取色器 ═══════════════════
        self._build_picker_page(page_picker)

        # ═══════════════════ Page 5: LAB ═══════════════════
        self._build_lab_page(page_lab)

        # ═══════════════════ Page 6: 滑块 ═══════════════════
        self._build_sliders_page(page_sliders)

        # ═══════════════════ Page 7: 面板 ═══════════════════
        self._build_panels_page(page_panels)

        # ═══════════════════ Page 8: 灰度滤镜 ═══════════════════
        self._build_filter_page(page_filter)

        # ═══════════════════ Page 9: 同步 ═══════════════════
        self._build_sync_page(page_sync)

        # ═══════════════════ Page 10: 系统 ═══════════════════
        self._build_about_page(page_about)
        # Keep section cards at their natural size, top-aligned: one trailing
        # stretch absorbs the fixed window's leftover height on short pages.
        for page_layout in self._page_layouts.values():
            page_layout.addStretch(1)

        # Re-apply the static hover-tooltip catalog after every (re)build so
        # language changes / retranslate() keep the Chinese/English tips in sync.
        apply_settings_tooltips(self)

    # ── Rail / page navigation ───────────────────────────────────────────

    def _on_nav_changed(self, row):
        """Rail selection → switch the stacked page."""
        self._last_settings_tab = row
        if 0 <= row < self.stack.count() and self.stack.currentIndex() != row:
            self.stack.setCurrentIndex(row)
        self._refresh_nav_icons()

    def _on_page_changed(self, index):
        """Programmatic page switch (e.g. restoring last page on open)."""
        self._last_settings_tab = index
        if 0 <= index < self.nav.count() and self.nav.currentRow() != index:
            self.nav.setCurrentRow(index)

    def refresh_ui(self):
        self.cfg = config.load_hotkey_config()
        
        # 1. Hotkeys
        _pick = self.cfg.get("pickKey", "Ctrl+Alt+Q")
        self.btn_pick.setText(display_hotkey(_pick) if _pick else i18n.tr("未绑定"))
        self.btn_pick.val = _pick

        _hide = self.cfg.get("hideWindowKey", "Ctrl+Alt+Y")
        self.btn_hide.setText(display_hotkey(_hide) if _hide else i18n.tr("未绑定"))
        self.btn_hide.val = _hide

        _follow = self.cfg.get("followMouseKey", "Ctrl+Alt+J")
        self.btn_follow.setText(display_hotkey(_follow) if _follow else i18n.tr("未绑定"))
        self.btn_follow.val = _follow

        _gray = self.cfg.get("grayscaleFilterKey", "Ctrl+Alt+D")
        self.btn_grayscale.setText(display_hotkey(_gray) if _gray else i18n.tr("未绑定"))
        self.btn_grayscale.val = _gray

        _region_gray = self.cfg.get("regionGrayscaleKey", "Ctrl+Alt+G")
        self.btn_region_grayscale.setText(display_hotkey(_region_gray) if _region_gray else i18n.tr("未绑定"))
        self.btn_region_grayscale.val = _region_gray

        _lab_key = self.cfg.get("toggleLabKey", "Space")
        self.btn_lab_toggle.setText(display_hotkey(_lab_key) if _lab_key else i18n.tr("未绑定"))
        self.btn_lab_toggle.val = _lab_key

        _lab_local = self.cfg.get("toggleLabLocalKey", "")
        self.btn_lab_local.setText(display_hotkey(_lab_local) if _lab_local else i18n.tr("未绑定"))
        self.btn_lab_local.val = _lab_local

        _lab_global = self.cfg.get("toggleLabGlobalKey", "Ctrl+Alt+L")
        self.btn_lab_global.setText(display_hotkey(_lab_global) if _lab_global else i18n.tr("未绑定"))
        self.btn_lab_global.val = _lab_global

        _wheel_tab_global = self.cfg.get("toggleWheelTabGlobalKey", "")
        self.btn_wheel_tab_global.setText(
            i18n.tr(display_hotkey(_wheel_tab_global)) if _wheel_tab_global else i18n.tr("未绑定"))
        self.btn_wheel_tab_global.val = _wheel_tab_global

        _slider_tab_global = self.cfg.get("toggleSliderTabGlobalKey", "")
        self.btn_slider_tab_global.setText(
            i18n.tr(display_hotkey(_slider_tab_global)) if _slider_tab_global else i18n.tr("未绑定"))
        self.btn_slider_tab_global.val = _slider_tab_global

        _title_bar = self.cfg.get("toggleTitleBarKey", "Ctrl+Alt+K")
        self.btn_title_bar.setText(display_hotkey(_title_bar) if _title_bar else i18n.tr("未绑定"))
        self.btn_title_bar.val = _title_bar

        self._sync_hotkey_none_buttons()
        self.refresh_floating_hotkeys_ui()
        
        self.combo_grayscale_mode.blockSignals(True)
        backend = self.cfg.get("grayscaleFilterBackend", "dcomp")
        if backend not in ("dcomp", "mag"):
            backend = "dcomp"
        self._update_grayscale_screen_options(backend)
        self._update_grayscale_mode_options(backend)
        self.combo_grayscale_mode.blockSignals(False)

        self.combo_grayscale_backend.blockSignals(True)
        _idx = self.combo_grayscale_backend.findData(backend)
        self.combo_grayscale_backend.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_grayscale_backend.blockSignals(False)
        self._refresh_grayscale_region_ui()
        self._refresh_grayscale_status_ui()
        self._update_grayscale_backend_note()
        
        self.cb_follow_mouse.blockSignals(True)
        self.cb_follow_mouse.setChecked(self.cfg.get("followMouseEnabled", False))
        self.cb_follow_mouse.blockSignals(False)

        self.cb_zone_filter.blockSignals(True)
        self.cb_zone_filter.setChecked(self.cfg.get("mouseHotkeyZoneFilter", True))
        self.cb_zone_filter.blockSignals(False)
        
        # 2. Interface
        _idx = self.combo_theme.findData(self.cfg.get("ui-theme", "auto"))
        self.combo_theme.blockSignals(True)
        self.combo_theme.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_theme.blockSignals(False)

        _lang_idx = self.cmb_language.findData(self.cfg.get("language", "auto"))
        self.cmb_language.blockSignals(True)
        self.cmb_language.setCurrentIndex(_lang_idx if _lang_idx >= 0 else 0)
        self.cmb_language.blockSignals(False)

        # Show/hide eyedropper rows and update point labels
        is_eyedropper = self.cfg.get("ui-theme", "auto") == "eyedropper"
        for target in ("bar", "bg"):
            row = getattr(self, f"_eye_row_{target}")
            lbl = getattr(self, f"_eye_lbl_{target}")
            row.setVisible(is_eyedropper)
            if is_eyedropper:
                key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
                pt = self.cfg.get(key, None)
                if pt and isinstance(pt, dict) and "x" in pt and "y" in pt:
                    lbl.setText(f"({pt['x']}, {pt['y']})")
                    self._set_label_state(lbl, None)
                else:
                    lbl.setText(i18n.tr("未设定"))
                    self._set_label_state(lbl, "danger")
        self._refresh_theme_status()

        # Slider theme combo (resolve stored key → combo index)
        slider_style_key = self.cfg.get("sliderStyle", "default")
        self.combo_slider_style.blockSignals(True)
        target_idx = -1
        for i in range(self.combo_slider_style.count()):
            if self.combo_slider_style.itemData(i) == slider_style_key:
                target_idx = i
                break
        if target_idx < 0:
            target_idx = 0  # fall back to first item ("default")
        self.combo_slider_style.setCurrentIndex(target_idx)
        self.combo_slider_style.blockSignals(False)

        # Border theme combo (stored key → combo index; "auto" = 跟随滑块样式)
        border_style_key = self.cfg.get("borderStyle", "auto")
        self.combo_border_style.blockSignals(True)
        border_idx = -1
        for i in range(self.combo_border_style.count()):
            if self.combo_border_style.itemData(i) == border_style_key:
                border_idx = i
                break
        if border_idx < 0:
            border_idx = 0  # fall back to first item ("auto")
        self.combo_border_style.setCurrentIndex(border_idx)
        self.combo_border_style.blockSignals(False)
        
        font_val = self.cfg.get("fontSize", 100)
        self.lbl_font_size.setText(f"{font_val}%")
        
        zoom_val = self.cfg.get("pickerZoom", 6)
        self.lbl_picker_zoom.setText(f"{zoom_val}×")
        
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(self.cfg.get("uiScale", 100))
        self.zoom_slider.blockSignals(False)
        self.lbl_zoom.setText(f"{self.zoom_slider.value()}%")

        opacity_val = resolve_chrome_opacity(self.cfg)
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(opacity_val)
        self.opacity_slider.blockSignals(False)
        self.lbl_opacity.setText(f"{opacity_val}%")
        
        # Checkboxes
        for cb, key, default in [
            (self.cb_taskbar_icon, "showTaskbarIcon", False),
            (self.cb_lock_size, "lockWindowSize", False),
            (self.cb_lock_position, "lockWindowPosition", False),
            (self.cb_autostart, "openAtLogin", False),
            (self.cb_only_drawing, "onlyShowInCsp", True),
            (self.cb_hide_on_menu, "hideOnPopupMenu", False),
            (self.cb_no_focus, "noFocusMode", True)
        ]:
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(key, default))
            cb.blockSignals(False)

        self.cb_show_title_bar.blockSignals(True)
        self.cb_show_title_bar.setChecked(self.cfg.get("showTitleBar", True))
        self.cb_show_title_bar.blockSignals(False)

        self.cb_show_window_border.blockSignals(True)
        self.cb_show_window_border.setChecked(self.cfg.get("showWindowBorder", True))
        self.cb_show_window_border.blockSignals(False)
            
        # 3. Sliders — load only existing groups, respect module visibility
        for key in ["RGB", "HSV", "VHSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            cb, _row = self.slider_rows[key]
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(f"showSliders{key}", True))
            cb.blockSignals(False)

        self.cb_history.blockSignals(True)
        self.cb_history.setChecked(self.cfg.get("showSlidersHistory", True))
        self.cb_history.blockSignals(False)

        self._refresh_module_sliders()

        # History grid shape (columns × rows × swatch size)
        self.combo_history_cols.blockSignals(True)
        self.combo_history_cols.setCurrentText(str(self.cfg.get("historyColumns", 8)))
        self.combo_history_cols.blockSignals(False)

        self.combo_history_rows.blockSignals(True)
        self.combo_history_rows.setCurrentText(str(self.cfg.get("historyRows", 2)))
        self.combo_history_rows.blockSignals(False)
            
        module_map = {"hsv": "HSV", "vhsv": "VHSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
        self.combo_module.blockSignals(True)
        self.combo_module.setCurrentText(module_map.get(self.cfg.get("colorSpaceModule", "hsv"), "HSV"))
        self.combo_module.blockSignals(False)

        self.cb_show_module_btn.blockSignals(True)
        self.cb_show_module_btn.setChecked(self.cfg.get("showModuleSwitchButton", True))
        self.cb_show_module_btn.blockSignals(False)
        for _cb, _key in (
            (getattr(self, "cb_floating_wheel_swatches", None),
             "floatingWheelShowSwatches"),
            (getattr(self, "cb_floating_wheel_buttons", None),
             "floatingWheelShowButtons"),
        ):
            if _cb is not None:
                _cb.blockSignals(True)
                _cb.setChecked(self.cfg.get(_key, True))
                _cb.blockSignals(False)
        self._refresh_wheel_tab_checks()

        self.cb_show_lab_toggle.blockSignals(True)
        self.cb_show_lab_toggle.setChecked(self.cfg.get("showLabToggleButton", True))
        self.cb_show_lab_toggle.blockSignals(False)

        self.cb_show_lab_shape_btn.blockSignals(True)
        self.cb_show_lab_shape_btn.setChecked(self.cfg.get("showLabShapeButton", True))
        self.cb_show_lab_shape_btn.blockSignals(False)

        self.cb_show_lab_harmony_btn.blockSignals(True)
        self.cb_show_lab_harmony_btn.setChecked(self.cfg.get("showLabHarmonyButton", True))
        self.cb_show_lab_harmony_btn.blockSignals(False)

        if hasattr(self, "cb_show_lab_checkerboard"):
            self.cb_show_lab_checkerboard.blockSignals(True)
            self.cb_show_lab_checkerboard.setChecked(self.cfg.get("showLabCheckerboard", True))
            self.cb_show_lab_checkerboard.blockSignals(False)

        _idx = self.combo_viz_mode.findData(self.cfg.get("visualizerMode", "lab"))
        self.combo_viz_mode.blockSignals(True)
        self.combo_viz_mode.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_viz_mode.blockSignals(False)

        _idx_shape = self.combo_lab_shape.findData(self.cfg.get("labViewShape", "square"))
        self.combo_lab_shape.blockSignals(True)
        self.combo_lab_shape.setCurrentIndex(_idx_shape if _idx_shape >= 0 else 0)
        self.combo_lab_shape.blockSignals(False)

        _idx_harmony = self.combo_lab_harmony.findData(self.cfg.get("labHarmonyMode", "analogous"))
        self.combo_lab_harmony.blockSignals(True)
        self.combo_lab_harmony.setCurrentIndex(_idx_harmony if _idx_harmony >= 0 else 0)
        self.combo_lab_harmony.blockSignals(False)

        self.cb_show_lab_lightness.blockSignals(True)
        self.cb_show_lab_lightness.setChecked(self.cfg.get("showLabLightnessSlider", False))
        self.cb_show_lab_lightness.blockSignals(False)
        for checkbox, key, default in (
                (getattr(self, "cb_sliders_tabs", None), "slidersTabs", False),
                (getattr(self, "cb_panel_drag", None), "panelDrag", False),):
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(self.cfg.get(key, default))
                checkbox.blockSignals(False)
        self.refresh_floating_panel_list()
        
        self.cb_flip_wheel.blockSignals(True)
        self.cb_flip_wheel.setChecked(self.cfg.get("flipColorWheelHorizontally", True))
        self.cb_flip_wheel.blockSignals(False)

        self.cb_show_color_readout.blockSignals(True)
        self.cb_show_color_readout.setChecked(self.cfg.get("showColorValueReadout", True))
        self.cb_show_color_readout.blockSignals(False)

        # ── Ringless settings ──
        ringless_config = RinglessConfig.from_values(
            self.cfg.get("hideHueRing", False),
            self.cfg.get("ringlessControlsSide", "right"),
            self.cfg.get("ringlessControlBarPosition", "top"),
        )
        self.ringless_settings.set_config(ringless_config)
        
        scroll_val = self.cfg.get("sliderScrollStep", 1)
        self.lbl_scroll_step.setText(str(scroll_val))
        
        same_val = self.cfg.get("sliderSameSpace", 6)
        self.lbl_same_space.setText(str(same_val))
        
        diff_val = self.cfg.get("sliderDiffSpace", 8)
        self.lbl_diff_space.setText(str(diff_val))

        top_val = self.cfg.get("panelTopGap", 6)
        self.lbl_top_gap.setText(str(top_val))
        
        # 4. Software Version
        _idx = self.combo_software.findData(self.cfg.get("syncSoftware", "auto"))
        self.combo_software.blockSignals(True)
        self.combo_software.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_software.blockSignals(False)
        
        _idx = self.combo_pos.findData(self.cfg.get("previewBoxPosition", "top-left"))
        self.combo_pos.blockSignals(True)
        self.combo_pos.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_pos.blockSignals(False)
        
        # Migrate legacy CSP version keys to simplified 4.x / 5.x scheme;
        # csp5.1 memory sync was removed → force back to auto.
        _csp_migration = {"csp4.0": "csp4.x", "csp4.2.7-ex": "csp4.x",
                          "csp5.0": "csp5.x", "csp5.0-ex": "csp5.x",
                          "csp5.1": "auto"}
        raw_csp = str(self.cfg.get("cspVersion", "auto") or "auto")
        raw_csp = _csp_migration.get(raw_csp, raw_csp)
        _idx = self.combo_csp.findData(raw_csp)
        self.combo_csp.blockSignals(True)
        self.combo_csp.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_csp.blockSignals(False)
        self._refresh_csp_version_hint()
        
        self.combo_sai.blockSignals(True)
        self.combo_sai.setCurrentText(self.cfg.get("sai2Version", "auto"))
        self.combo_sai.blockSignals(False)
        self.combo_sai_panel.blockSignals(True)
        self.combo_sai_panel.setCurrentText(self.cfg.get("sai2PanelMode", "auto"))
        self.combo_sai_panel.blockSignals(False)

        udm_display_map = {"auto": "auto", "udm4.0": "udm4.0pro", "udm4.0-ex": "udm4.0ex"}
        self.combo_udm.blockSignals(True)
        self.combo_udm.setCurrentText(udm_display_map.get(self.cfg.get("udmVersion", "auto"), "auto"))
        self.combo_udm.blockSignals(False)
        
        self._refresh_ps_instances()
        self.combo_ps.blockSignals(True)
        self.combo_ps.setCurrentText(self.cfg.get("psVersion", "auto"))
        self.combo_ps.blockSignals(False)
        
        self.update_version_visibility()
        self.apply_theme()
        self._refresh_module_sliders()
        self._refresh_autostart_status()

    def _sync_hotkey_none_buttons(self):
        """Show each「无」button only while its hotkey is bound.

        Hidden (not merely disabled) when the row is unbound: there is nothing
        left to clear, and the freed width goes to the capture button so a long
        combination such as ``Ctrl+Alt+Shift+F5`` still fits its label. Called
        from ``refresh_ui`` and ``save_hotkeys`` — both run after the widgets
        exist, so ``setVisible`` has a parent to take effect against.
        """
        for name in ("pick", "hide", "follow", "grayscale", "region_grayscale",
                     "lab_toggle", "lab_local", "lab_global",
                     "wheel_tab_global", "slider_tab_global", "title_bar"):
            button = getattr(self, f"btn_{name}", None)
            none_btn = getattr(self, f"btn_{name}_none", None)
            if button is None or none_btn is None:
                continue
            bound = bool(button.val)
            none_btn.setEnabled(bound)
            none_btn.setVisible(bound)

    def _clear_floating_hotkeys_rows(self) -> None:
        self._floating_hotkey_entries = {}
        while self._floating_hotkeys_rows.count():
            item = self._floating_hotkeys_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _open_floating_windows_for_hotkeys(self) -> list:
        mv = getattr(self, "_parent", None)
        if mv is None:
            return []
        unique_fn = getattr(mv, "_unique_floating_windows", None)
        if callable(unique_fn):
            try:
                return list(unique_fn())
            except RuntimeError:
                return []
        fw_fn = getattr(mv, "floating_windows", None)
        if callable(fw_fn):
            out = []
            seen = set()
            for win in fw_fn().values():
                if win is not None and id(win) not in seen:
                    seen.add(id(win))
                    out.append(win)
            return out
        return []

    @staticmethod
    def _floating_window_display_title(win, pids: list[str], win_id: str) -> str:
        from ui.panels import registry
        titles: list[str] = []
        for pid in (pids or ([win_id] if win_id else [])):
            spec = registry.panel(pid)
            titles.append(i18n.tr(spec.title) if spec else pid)
        if titles:
            return " / ".join(titles)
        return (win.windowTitle() if hasattr(win, "windowTitle") else "") or win_id

    def _set_floating_hotkey_pref(self, window_id: str, field: str, value,
                                  none_btn=None) -> None:
        config.set_floating_window_hotkey_field(self.cfg, window_id, field, value)
        mv = getattr(self, "_parent", None)
        if mv is not None and isinstance(getattr(mv, "cfg", None), dict):
            config.set_floating_window_hotkey_field(mv.cfg, window_id, field, value)
        if none_btn is not None:
            bound = bool(value)
            none_btn.setEnabled(bound)
            none_btn.setVisible(bound)
        self._persist_and_emit()

    @staticmethod
    def _set_floating_lab_follow(pages: list, checked: bool) -> None:
        for page in pages:
            try:
                page.set_follow_global_lab(bool(checked), remember=True)
            except RuntimeError:
                continue

    def _build_floating_hotkey_section(
        self, win_id: str, title: str, pages: list, slider_pids: list,
    ) -> tuple[QWidget, dict]:
        hk = config.get_floating_window_hotkeys(self.cfg, win_id)
        box_widget = QWidget()
        layout = QVBoxLayout(box_widget)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        lbl_title = QLabel(title)
        title_font = lbl_title.font()
        title_font.setBold(True)
        lbl_title.setFont(title_font)
        layout.addWidget(lbl_title)

        refs: dict[str, object] = {}
        has_wheel = bool(pages)
        has_lab = any(getattr(p, "lab_enabled", False) for p in pages)
        has_slider = bool(slider_pids)

        if has_wheel:
            cb_wheel_tab = QCheckBox(i18n.tr("跟随全局色轮标签快捷键"))
            cb_wheel_tab.setChecked(bool(hk["follow_global_wheel_tab"]))
            cb_wheel_tab.toggled.connect(
                lambda checked, wid=win_id: self._set_floating_hotkey_pref(
                    wid, "follow_global_wheel_tab", bool(checked)))
            layout.addWidget(cb_wheel_tab)
            refs["cb_follow_wheel_tab"] = cb_wheel_tab

            if has_lab:
                cb_lab = QCheckBox(i18n.tr("跟随全局 LAB 切换快捷键"))
                cb_lab.setChecked(bool(pages[0].follow_global_lab))
                cb_lab.toggled.connect(
                    lambda checked, ps=list(pages):
                    self._set_floating_lab_follow(ps, bool(checked)))
                layout.addWidget(cb_lab)
                refs["cb_follow_lab"] = cb_lab

        if has_slider:
            cb_slider_tab = QCheckBox(i18n.tr("跟随全局滑条标签快捷键"))
            cb_slider_tab.setChecked(bool(hk["follow_global_slider_tab"]))
            cb_slider_tab.toggled.connect(
                lambda checked, wid=win_id: self._set_floating_hotkey_pref(
                    wid, "follow_global_slider_tab", bool(checked)))
            layout.addWidget(cb_slider_tab)
            refs["cb_follow_slider_tab"] = cb_slider_tab

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(0, 120)
        grid.setColumnStretch(1, 1)

        def _add_key_row(row_idx: int, label_text: str, field: str,
                         key_prefix: str) -> None:
            grid.addWidget(QLabel(i18n.tr(label_text)), row_idx, 0)
            init_val = hk[field]
            none_ref: list = []
            row_w, btn, none_btn = self._make_hotkey_row(
                f"{key_prefix}{win_id}", init_val,
                lambda val, wid=win_id, f=field, nr=none_ref:
                self._set_floating_hotkey_pref(
                    wid, f, val, none_btn=nr[0] if nr else None),
                allow_mouse=True,
            )
            none_ref.append(none_btn)
            bound = bool(init_val)
            none_btn.setEnabled(bound)
            none_btn.setVisible(bound)
            grid.addWidget(row_w, row_idx, 1)
            short = field.removesuffix("_key")
            refs[f"btn_{short}"] = btn
            refs[f"btn_{short}_none"] = none_btn

        _add_key_row(0, "本浮窗标签切换", "tab_key", "__float_tab__:")
        if has_wheel and has_lab:
            _add_key_row(1, "本浮窗 LAB 切换", "lab_key", "__float_lab__:")

        layout.addLayout(grid)
        return box_widget, refs

    def refresh_floating_hotkeys_ui(self) -> None:
        """Rebuild the dynamic floating-window hotkey card from open floats."""
        if not hasattr(self, "card_floating_hotkeys"):
            return
        mv = getattr(self, "_parent", None)
        if mv is not None and isinstance(getattr(mv, "cfg", None), dict):
            parent_fh = mv.cfg.get("floatingWindowHotkeys")
            if isinstance(parent_fh, dict):
                self.cfg["floatingWindowHotkeys"] = {
                    k: dict(v) if isinstance(v, dict) else v
                    for k, v in parent_fh.items()
                }
            elif "floatingWindowHotkeys" in self.cfg:
                self.cfg["floatingWindowHotkeys"] = {}
        self._clear_floating_hotkeys_rows()
        windows = self._open_floating_windows_for_hotkeys()
        classify_fn = getattr(mv, "_classify_floating_window", None) if mv is not None else None

        rendered = 0
        for win in windows:
            win_id = getattr(win, "panel_id", "") or ""
            if not win_id:
                continue
            pids = list(getattr(win, "panel_ids", ()) or ())
            if callable(classify_fn):
                pages, _labs, slider_pids = classify_fn(win)
            else:
                pages, slider_pids = [], (pids or [win_id])
            if not pages and not slider_pids:
                continue
            title = self._floating_window_display_title(win, pids, win_id)
            section, refs = self._build_floating_hotkey_section(
                win_id, title, pages, slider_pids)
            self._floating_hotkeys_rows.addWidget(section)
            self._floating_hotkey_entries[win_id] = refs
            rendered += 1

        self.card_floating_hotkeys.setVisible(rendered > 0)

    def save_hotkeys(self, new_val=None):
        self.cfg["pickKey"] = self.btn_pick.val
        self.cfg["hideWindowKey"] = self.btn_hide.val
        self.cfg["followMouseKey"] = self.btn_follow.val
        self.cfg["grayscaleFilterKey"] = self.btn_grayscale.val
        self.cfg["regionGrayscaleKey"] = self.btn_region_grayscale.val
        self.cfg["toggleLabKey"] = self.btn_lab_toggle.val
        self.cfg["toggleLabLocalKey"] = self.btn_lab_local.val
        self.cfg["toggleLabGlobalKey"] = self.btn_lab_global.val
        self.cfg["toggleWheelTabGlobalKey"] = self.btn_wheel_tab_global.val
        self.cfg["toggleSliderTabGlobalKey"] = self.btn_slider_tab_global.val
        self.cfg["toggleTitleBarKey"] = self.btn_title_bar.val
        # Keep the ✕ controls in step with the values just saved (binding a
        # key enables its ✕, unbinding disables it).
        self._sync_hotkey_none_buttons()
        self._refresh_grayscale_status_ui()
        self._persist_and_emit()

    def _grayscale_filter_config(self) -> dict:
        """Map the grayscale controls to persisted config values."""
        screen_text = self.combo_grayscale_screen.currentText()
        backend = self.combo_grayscale_backend.currentData() or "dcomp"
        mode = self.combo_grayscale_mode.currentData() or "oklch"
        use_mag = backend == "mag"
        return {
            "grayscaleFilterScreen": (
                "all"
                if use_mag
                else (screen_text.split(":")[0].strip()
                      if ":" in screen_text else screen_text)
            ),
            "grayscaleFilterMode": "luma" if (mode == "luma" or use_mag) else "oklch",
            "grayscaleFilterBackend": backend,
        }

    def save_settings(self):
        self.cfg["ui-theme"] = self.combo_theme.currentData() or "auto"

        # Slider visual theme (key stored as combo item data)
        slider_key = self.combo_slider_style.currentData()
        self.cfg["sliderStyle"] = slider_key if slider_key else "default"

        # Border visual theme ("auto" = follow the slider theme's pairing)
        border_key = self.combo_border_style.currentData()
        self.cfg["borderStyle"] = border_key if border_key else "auto"
        
        self.cfg["followMouseEnabled"] = self.cb_follow_mouse.isChecked()
        # settingChanged → on_settings_saved → update_hotkey_bindings 会把
        # 该开关写进 global_hotkeys 的模块级闸门，改动即时生效、无需重启。
        self.cfg["mouseHotkeyZoneFilter"] = self.cb_zone_filter.isChecked()
        self.cfg["lockWindowSize"] = self.cb_lock_size.isChecked()
        self.cfg["lockWindowPosition"] = self.cb_lock_position.isChecked()
        
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = self.cb_autostart.isChecked()
        if old_autostart != new_autostart:
            ok = autostart.apply_autostart(new_autostart)
            if not ok:
                # 注册表写入失败（windowed 下 print 不可见）：回滚配置与勾选
                self.cfg["openAtLogin"] = old_autostart
                self.cb_autostart.blockSignals(True)
                try:
                    self.cb_autostart.setChecked(old_autostart)
                finally:
                    self.cb_autostart.blockSignals(False)
            else:
                # 注册表写入成功后才把新状态落进配置；否则下次 refresh_ui()
                # 会重新加载旧值，勾选看起来"保存不生效"，也无法通过再勾一次
                # 触发注册表同步。
                self.cfg["openAtLogin"] = new_autostart
        else:
            # 配置与勾选本来一致：保持 cfg 同步（幂等，避免脏值残留）。
            self.cfg["openAtLogin"] = new_autostart
            
        self.cfg["onlyShowInCsp"] = self.cb_only_drawing.isChecked()
        self.cfg["hideOnPopupMenu"] = self.cb_hide_on_menu.isChecked()
        self.cfg["showTaskbarIcon"] = self.cb_taskbar_icon.isChecked()
        self.cfg["showTitleBar"] = self.cb_show_title_bar.isChecked()
        self.cfg["showWindowBorder"] = self.cb_show_window_border.isChecked()
        self.cfg["noFocusMode"] = self.cb_no_focus.isChecked()
        
        # Sliders (all groups stored, but only current-module groups shown in UI)
        for key in ["RGB", "HSV", "VHSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            self.cfg[f"showSliders{key}"] = self.slider_rows[key][0].isChecked()
        self.cfg["showSlidersHistory"] = self.cb_history.isChecked()

        # History grid shape
        try:
            self.cfg["historyColumns"] = int(self.combo_history_cols.currentText())
        except Exception:
            self.cfg["historyColumns"] = 8
        try:
            self.cfg["historyRows"] = int(self.combo_history_rows.currentText())
        except Exception:
            self.cfg["historyRows"] = 2
        # historySwatchSize is intentionally NOT stored here — the swatch
        # size auto-fits the parent width via ColorHistoryWidget._relayout.
            
        module_val_map = {"HSV": "hsv", "VHSV": "vhsv", "HLS": "hls", "RGB": "rgb", "LCH": "lch"}
        self.cfg["colorSpaceModule"] = module_val_map.get(self.combo_module.currentText(), "hsv")
        self.cfg["showModuleSwitchButton"] = self.cb_show_module_btn.isChecked()
        if getattr(self, "cb_floating_wheel_swatches", None) is not None:
            self.cfg["floatingWheelShowSwatches"] = (
                self.cb_floating_wheel_swatches.isChecked())
        if getattr(self, "cb_floating_wheel_buttons", None) is not None:
            self.cfg["floatingWheelShowButtons"] = (
                self.cb_floating_wheel_buttons.isChecked())
        wheel_tabs = self._wheel_tabs_from_checks()
        if wheel_tabs is not None:
            self.cfg["wheelModuleTabs"] = wheel_tabs
            # The active module must be one of the visible tabs.
            if self.cfg["colorSpaceModule"] not in wheel_tabs:
                self.cfg["colorSpaceModule"] = wheel_tabs[0]
                module_map = {"hsv": "HSV", "vhsv": "VHSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
                self.combo_module.blockSignals(True)
                self.combo_module.setCurrentText(module_map[wheel_tabs[0]])
                self.combo_module.blockSignals(False)
        self.cfg["showLabToggleButton"] = self.cb_show_lab_toggle.isChecked()
        self.cfg["showLabShapeButton"] = self.cb_show_lab_shape_btn.isChecked()
        self.cfg["showLabHarmonyButton"] = self.cb_show_lab_harmony_btn.isChecked()
        if hasattr(self, "cb_show_lab_checkerboard"):
            self.cfg["showLabCheckerboard"] = self.cb_show_lab_checkerboard.isChecked()
        self.cfg["visualizerMode"] = self.combo_viz_mode.currentData() or "lab"
        self.cfg["labViewShape"] = self.combo_lab_shape.currentData() or "square"
        self.cfg["labHarmonyMode"] = self.combo_lab_harmony.currentData() or "analogous"
        self.cfg["showLabLightnessSlider"] = self.cb_show_lab_lightness.isChecked()
        tabs = getattr(self, "cb_sliders_tabs", None)
        if tabs is not None:
            self.cfg["slidersTabs"] = tabs.isChecked()
        grips = getattr(self, "cb_panel_drag", None)
        if grips is not None:
            self.cfg["panelDrag"] = grips.isChecked()

        # ── Ringless settings ──
        rcfg = self.ringless_settings.config()
        self.cfg["hideHueRing"] = rcfg.enabled
        self.cfg["ringlessControlsSide"] = rcfg.controls_side
        self.cfg["ringlessControlBarPosition"] = rcfg.control_bar_position
        
        self.cfg["syncSoftware"] = self.combo_software.currentData() or "auto"
        
        self.cfg["previewBoxPosition"] = self.combo_pos.currentData() or "top-left"
        
        self.cfg["cspVersion"] = self.combo_csp.currentData() or "auto"
        self.cfg["sai2Version"] = self.combo_sai.currentText()
        self.cfg["sai2PanelMode"] = self.combo_sai_panel.currentText()

        udm_val_map = {"auto": "auto", "udm4.0pro": "udm4.0", "udm4.0ex": "udm4.0-ex"}
        self.cfg["udmVersion"] = udm_val_map.get(self.combo_udm.currentText(), "auto")
        self.cfg["psVersion"] = self.combo_ps.currentText()
        
        self.cfg["uiScale"] = self.zoom_slider.value()
        self.cfg[CHROME_OPACITY_KEY] = clamp_chrome_opacity(self.opacity_slider.value())
        self.cfg["flipColorWheelHorizontally"] = self.cb_flip_wheel.isChecked()
        self.cfg["showColorValueReadout"] = self.cb_show_color_readout.isChecked()
        
        self.cfg.update(self._grayscale_filter_config())

        try:
            self.cfg["sliderScrollStep"] = int(self.lbl_scroll_step.text())
        except Exception:
            self.cfg["sliderScrollStep"] = 1
            
        try:
            self.cfg["sliderSameSpace"] = int(self.lbl_same_space.text())
        except Exception:
            self.cfg["sliderSameSpace"] = 6
            
        try:
            self.cfg["sliderDiffSpace"] = int(self.lbl_diff_space.text())
        except Exception:
            self.cfg["sliderDiffSpace"] = 8

        try:
            self.cfg["panelTopGap"] = max(
                0, min(30, int(self.lbl_top_gap.text())))
        except Exception:
            self.cfg["panelTopGap"] = 6
        
        self._persist_and_emit()
        self.update_version_visibility()
        is_eye = self.cfg.get("ui-theme", "auto") == "eyedropper"
        for target in ("bar", "bg"):
            row = getattr(self, f"_eye_row_{target}")
            lbl = getattr(self, f"_eye_lbl_{target}")
            row.setVisible(is_eye)
            if is_eye:
                key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
                pt = self.cfg.get(key, None)
                if pt and isinstance(pt, dict) and "x" in pt and "y" in pt:
                    lbl.setText(f"({pt['x']}, {pt['y']})")
                    self._set_label_state(lbl, None)
                else:
                    lbl.setText(i18n.tr("未设定"))
                    self._set_label_state(lbl, "danger")
        self._refresh_theme_status()
        self.apply_theme()
        self._refresh_module_sliders()
        self._refresh_autostart_status()

    def _refresh_module_sliders(self):
        """Show only the slider rows that belong to the currently active module.

        Reads the in-memory ``self.cfg`` — reloading from disk here would
        discard unsaved changes (e.g. a just-switched language). External
        changes are picked up by :meth:`notify_module_changed` instead.
        """
        module = self.cfg.get("colorSpaceModule", "hsv")
        allowed = set(self._MODULE_SLIDER_MAP.get(module, ALL_SLIDERS))
        for key, (cb, row_layout) in self.slider_rows.items():
            visible = key in allowed
            for i in range(row_layout.count()):
                w = row_layout.itemAt(i).widget()
                if w:
                    w.setVisible(visible)
        self._reorder_slider_rows_ui()

    def _reorder_slider_rows_ui(self):
        """Show the rows in the order the window shows the panels.

        Order is edited by dragging the panels themselves now; this list
        just mirrors the result so the two never disagree.
        """
        cl = getattr(self, "_sl_order_layout", None)
        if cl is None:
            return
        rows = [self.slider_rows[k][1]
                for k in config.sorted_slider_groups(self.cfg)
                if k in self.slider_rows]
        while cl.count():
            cl.takeAt(0)
        for row in rows:
            cl.addLayout(row)

    def notify_module_changed(self):
        """Called by MainWindow when the module changes externally."""
        self.cfg = config.load_hotkey_config()
        module_map = {"hsv": "HSV", "vhsv": "VHSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
        self.combo_module.blockSignals(True)
        self.combo_module.setCurrentText(module_map.get(self.cfg.get("colorSpaceModule", "hsv"), "HSV"))
        self.combo_module.blockSignals(False)
        self._refresh_wheel_tab_checks()
        self._refresh_module_sliders()

    def notify_lab_settings_changed(self):
        """Called by MainWindow when the LAB shape/harmony change on the pane."""
        self.cfg = config.load_hotkey_config()
        for combo, key, default in (
            (self.combo_viz_mode, "visualizerMode", "lab"),
            (self.combo_lab_shape, "labViewShape", "square"),
            (self.combo_lab_harmony, "labHarmonyMode", "analogous"),
        ):
            idx = combo.findData(self.cfg.get(key, default))
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _persist_config(self):
        """Write config only when it actually changed."""
        try:
            snapshot = json.dumps(self.cfg, sort_keys=True, ensure_ascii=False, indent=2)
        except Exception:
            snapshot = ""
        if snapshot != self._last_persisted:
            config.save_hotkey_config(self.cfg)
            self._last_persisted = snapshot

    def _persist_and_emit(self):
        self._persist_config()
        self.settingChanged.emit()

    def scroll_step_decrease(self):
        val = self.cfg.get("sliderScrollStep", 1)
        val = max(1, val - 1)
        self.lbl_scroll_step.setText(str(val))
        self.save_settings()
        
    def scroll_step_increase(self):
        val = self.cfg.get("sliderScrollStep", 1)
        val = min(10, val + 1)
        self.lbl_scroll_step.setText(str(val))
        self.save_settings()
        
    def same_space_decrease(self):
        val = self.cfg.get("sliderSameSpace", 6)
        val = max(2, val - 1)
        self.lbl_same_space.setText(str(val))
        self.save_settings()
        
    def same_space_increase(self):
        val = self.cfg.get("sliderSameSpace", 6)
        val = min(20, val + 1)
        self.lbl_same_space.setText(str(val))
        self.save_settings()
        
    def diff_space_decrease(self):
        val = self.cfg.get("sliderDiffSpace", 8)
        val = max(2, val - 1)
        self.lbl_diff_space.setText(str(val))
        self.save_settings()
        
    def diff_space_increase(self):
        val = self.cfg.get("sliderDiffSpace", 8)
        val = min(30, val + 1)
        self.lbl_diff_space.setText(str(val))
        self.save_settings()

    def panel_top_gap_decrease(self):
        val = self.cfg.get("panelTopGap", 6)
        val = max(0, val - 1)
        self.lbl_top_gap.setText(str(val))
        self.save_settings()

    def panel_top_gap_increase(self):
        val = self.cfg.get("panelTopGap", 6)
        val = min(30, val + 1)
        self.lbl_top_gap.setText(str(val))
        self.save_settings()

    def on_no_focus_clicked(self, checked):
        self.save_settings()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_floating_hotkeys_ui()
        self._refresh_grayscale_region_ui()
        self._refresh_grayscale_status_ui()
        # Settings are open.  The main window deliberately keeps its no-focus
        # extension styles while this sidebar is up (the sidebar lives in its
        # own top-level SettingsWindow, so the main window never needs to
        # accept focus on its behalf).  Re-apply them anyway so every
        # show/hide path (close button, hamburger, eyedropper theme-pick
        # re-show) leaves the picker window in a consistent state.
        mv = self._parent
        if mv is None:
            return
        try:
            if callable(getattr(mv, "update_window_flags", None)):
                mv.update_window_flags()
                mv.update_no_focus_policies()
        except RuntimeError:
            # The main window was already torn down (app quit / a drag-drop
            # race deleted it) while this hide/show event was delivered.
            # Nothing to sync to.
            pass

    def hideEvent(self, event):
        super().hideEvent(event)
        # Settings are closed: re-apply the no-focus window state if enabled.
        mv = self._parent
        if mv is None:
            return
        try:
            if callable(getattr(mv, "update_window_flags", None)):
                mv.update_window_flags()
                mv.update_no_focus_policies()
        except RuntimeError:
            # See showEvent: teardown ordering can destroy the main window
            # before the settings window closes.
            pass
