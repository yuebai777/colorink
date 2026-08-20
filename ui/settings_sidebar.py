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
from ui.hotkey_button import HotkeyButton, display_hotkey
from ui.ringless_mode import RinglessConfig
from ui.settings.appearance_panel import AppearancePanelMixin
from ui.settings.settings_helpers import SettingsHelpersMixin
from ui.settings.sync_panel import SyncPanelMixin
from ui.settings.update_panel import UpdatePanelMixin

if TYPE_CHECKING:
    from ui.main_window import MainWindow

# Resolve resource paths relative to the repo root so packaged builds
# (PyInstaller) work regardless of the current working directory.

# CSP 内存模式版本选项：显示文本 ↔ 配置存储值。前景/背景色与透明状态
# 同步（rgb_u32 槽布局）只有 csp5.1 支持；csp4.x / csp5.x 仅主色同步。
# 每项的悬停说明




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
        self.nav.setFixedWidth(96)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setUniformItemSizes(True)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_icons = {}
        for text, kind in [
            ("快捷键", "hotkeys"),
            ("界面", "interface"),
            ("取色器", "picker"),
            ("滤镜", "filter"),
            ("同步", "software"),
            ("关于", "about"),
        ]:
            item = QListWidgetItem(i18n.tr(text))
            item.setData(Qt.ItemDataRole.UserRole, kind)
            item.setSizeHint(QSize(0, 28))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self.nav)

        # ── Right: stacked pages ──
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self._layout.addLayout(body)
        self.stack.currentChanged.connect(self._on_page_changed)

        self._page_layouts = {}

        # Create all 6 pages
        page_hotkeys   = self._make_page("快捷键")
        page_interface = self._make_page("界面")
        page_picker    = self._make_page("取色器")
        page_filter    = self._make_page("滤镜")
        page_sync      = self._make_page("同步")
        page_about     = self._make_page("关于")

        # ═══════════════════ Page 1: 快捷键 ═══════════════════
        card_hk, cl_hk = self._begin_card(page_hotkeys, i18n.tr("全局热键"))

        grid_hotkeys = QGridLayout()
        grid_hotkeys.setSpacing(6)
        grid_hotkeys.setColumnMinimumWidth(0, 84)
        grid_hotkeys.setColumnStretch(1, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("全局取色")), 0, 0)
        self.btn_pick = HotkeyButton("pickKey", self.cfg.get("pickKey", "F11"), allow_mouse=True)
        self.btn_pick.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_pick, 0, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("隐藏窗口")), 1, 0)
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"), allow_mouse=True)
        self.btn_hide.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_hide, 1, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("跟随鼠标")), 2, 0)
        self.btn_follow = HotkeyButton("followMouseKey", self.cfg.get("followMouseKey", "Ctrl+R"), allow_mouse=True)
        self.btn_follow.hotkeyChanged.connect(self.save_hotkeys)
        row_follow = QHBoxLayout()
        row_follow.setSpacing(6)
        self.cb_follow_mouse = QCheckBox(i18n.tr("启用"))
        self.cb_follow_mouse.stateChanged.connect(self.save_settings)
        row_follow.addWidget(self.btn_follow)
        row_follow.addWidget(self.cb_follow_mouse)
        row_follow.addStretch()
        grid_hotkeys.addLayout(row_follow, 2, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("灰度滤镜")), 3, 0)
        self.btn_grayscale = HotkeyButton("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+G"), allow_mouse=True)
        self.btn_grayscale.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_grayscale, 3, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB 切换（色轮悬停）")), 4, 0)
        self.btn_lab_toggle = HotkeyButton("toggleLabKey", self.cfg.get("toggleLabKey", "Space"), allow_mouse=True)
        self.btn_lab_toggle.setToolTip(i18n.tr("鼠标悬停在色轮或LAB区域时，按此键/鼠标键切换色轮/LAB视图；支持键盘、鼠标按键或数位板笔按键（建议侧键/中键，左键会与色轮操作冲突）；无需聚焦本窗口，无焦点取色模式下也可用"))
        self.btn_lab_toggle.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_lab_toggle, 4, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB 切换（全局）")), 5, 0)
        self.btn_lab_global = HotkeyButton("toggleLabGlobalKey", self.cfg.get("toggleLabGlobalKey", "Ctrl+L"), allow_mouse=True)
        self.btn_lab_global.setToolTip(i18n.tr("任意位置全局切换色轮/LAB视图，无需聚焦本窗口；支持键盘或鼠标按键（鼠标按键作为全局快捷键时不拦截点击，画画软件仍会收到）"))
        self.btn_lab_global.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_lab_global, 5, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("标题栏显示/隐藏")), 6, 0)
        self.btn_title_bar = HotkeyButton("toggleTitleBarKey", self.cfg.get("toggleTitleBarKey", "Ctrl+Shift+T"), allow_mouse=True)
        self.btn_title_bar.setToolTip(i18n.tr("显示或隐藏标题栏（设置/最小化/关闭按钮那一栏）；隐藏后顶部边框与四周一致"))
        self.btn_title_bar.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_title_bar, 6, 1)

        cl_hk.addLayout(grid_hotkeys)
        page_hotkeys.addWidget(card_hk)

        # ═══════════════════ Page 2: 界面 ═══════════════════
        self._build_interface_page(page_interface)

        # ═══════════════════ Page 3: 取色器 ═══════════════════
        self._build_picker_page(page_picker)

        # ═══════════════════ Page 4: 滤镜 ═══════════════════
        self._build_filter_page(page_filter)

        # ═══════════════════ Page 5: 同步 ═══════════════════
        self._build_sync_page(page_sync)

        # ═══════════════════ Page 6: 关于 ═══════════════════
        self._build_about_page(page_about)
        # Keep section cards at their natural size, top-aligned: one trailing
        # stretch absorbs the fixed window's leftover height on short pages.
        for page_layout in self._page_layouts.values():
            page_layout.addStretch(1)

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
        _pick = self.cfg.get("pickKey", "F11")
        self.btn_pick.setText(display_hotkey(_pick) if _pick else i18n.tr("未绑定"))
        self.btn_pick.val = _pick

        _hide = self.cfg.get("hideWindowKey", "Ctrl+H")
        self.btn_hide.setText(display_hotkey(_hide) if _hide else i18n.tr("未绑定"))
        self.btn_hide.val = _hide

        _follow = self.cfg.get("followMouseKey", "Ctrl+R")
        self.btn_follow.setText(display_hotkey(_follow) if _follow else i18n.tr("未绑定"))
        self.btn_follow.val = _follow

        _gray = self.cfg.get("grayscaleFilterKey", "Ctrl+G")
        self.btn_grayscale.setText(display_hotkey(_gray) if _gray else i18n.tr("未绑定"))
        self.btn_grayscale.val = _gray

        _lab_key = self.cfg.get("toggleLabKey", "Space")
        self.btn_lab_toggle.setText(display_hotkey(_lab_key) if _lab_key else i18n.tr("未绑定"))
        self.btn_lab_toggle.val = _lab_key

        _lab_global = self.cfg.get("toggleLabGlobalKey", "Ctrl+L")
        self.btn_lab_global.setText(display_hotkey(_lab_global) if _lab_global else i18n.tr("未绑定"))
        self.btn_lab_global.val = _lab_global

        _title_bar = self.cfg.get("toggleTitleBarKey", "Ctrl+Shift+T")
        self.btn_title_bar.setText(display_hotkey(_title_bar) if _title_bar else i18n.tr("未绑定"))
        self.btn_title_bar.val = _title_bar
        
        self.combo_grayscale_mode.blockSignals(True)
        backend = self.cfg.get("grayscaleFilterBackend", "native")
        backend = "mag" if backend == "mag" else "native"
        self._update_grayscale_screen_options(backend)
        self._update_grayscale_mode_options(backend)
        self.combo_grayscale_mode.blockSignals(False)

        self.combo_grayscale_backend.blockSignals(True)
        self.combo_grayscale_backend.setCurrentIndex(1 if backend == "mag" else 0)
        self.combo_grayscale_backend.blockSignals(False)
        
        self.cb_follow_mouse.blockSignals(True)
        self.cb_follow_mouse.setChecked(self.cfg.get("followMouseEnabled", False))
        self.cb_follow_mouse.blockSignals(False)
        
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
        
        font_val = self.cfg.get("fontSize", 100)
        self.lbl_font_size.setText(f"{font_val}%")
        
        zoom_val = self.cfg.get("pickerZoom", 6)
        self.lbl_picker_zoom.setText(f"{zoom_val}×")
        
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(self.cfg.get("uiScale", 100))
        self.zoom_slider.blockSignals(False)
        self.lbl_zoom.setText(f"{self.zoom_slider.value()}%")
        
        # Checkboxes
        for cb, key in [
            (self.cb_taskbar_icon, "showTaskbarIcon"),
            (self.cb_lock_size, "lockWindowSize"),
            (self.cb_lock_position, "lockWindowPosition"),
            (self.cb_autostart, "openAtLogin"),
            (self.cb_only_drawing, "onlyShowInCsp"),
            (self.cb_no_focus, "noFocusMode")
        ]:
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(key, False))
            cb.blockSignals(False)

        self.cb_show_title_bar.blockSignals(True)
        self.cb_show_title_bar.setChecked(self.cfg.get("showTitleBar", True))
        self.cb_show_title_bar.blockSignals(False)
            
        # 3. Sliders — load only existing groups, respect module visibility
        for key in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh"]:
            cb, _, _, _ = self.slider_rows[key]
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
            
        module_map = {"hsv": "HSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
        self.combo_module.blockSignals(True)
        self.combo_module.setCurrentText(module_map.get(self.cfg.get("colorSpaceModule", "hsv"), "HSV"))
        self.combo_module.blockSignals(False)

        self.cb_show_module_btn.blockSignals(True)
        self.cb_show_module_btn.setChecked(self.cfg.get("showModuleSwitchButton", True))
        self.cb_show_module_btn.blockSignals(False)

        self.cb_show_lab_toggle.blockSignals(True)
        self.cb_show_lab_toggle.setChecked(self.cfg.get("showLabToggleButton", True))
        self.cb_show_lab_toggle.blockSignals(False)

        _idx = self.combo_viz_mode.findData(self.cfg.get("visualizerMode", "lab"))
        self.combo_viz_mode.blockSignals(True)
        self.combo_viz_mode.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_viz_mode.blockSignals(False)
        
        self.cb_show_lab_lightness.blockSignals(True)
        self.cb_show_lab_lightness.setChecked(self.cfg.get("showLabLightnessSlider", True))
        self.cb_show_lab_lightness.blockSignals(False)
        
        self.cb_flip_wheel.blockSignals(True)
        self.cb_flip_wheel.setChecked(self.cfg.get("flipColorWheelHorizontally", False))
        self.cb_flip_wheel.blockSignals(False)

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
        
        # 4. Software Version
        _idx = self.combo_software.findData(self.cfg.get("syncSoftware", "csp"))
        self.combo_software.blockSignals(True)
        self.combo_software.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_software.blockSignals(False)
        
        _idx = self.combo_pos.findData(self.cfg.get("previewBoxPosition", "top-left"))
        self.combo_pos.blockSignals(True)
        self.combo_pos.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.combo_pos.blockSignals(False)
        
        # Migrate legacy CSP version keys to simplified 4.x / 5.x scheme
        _csp_migration = {"csp4.0": "csp4.x", "csp4.2.7-ex": "csp4.x",
                          "csp5.0": "csp5.x", "csp5.0-ex": "csp5.x"}
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

        _sai_refresh_idx = self.combo_sai_refresh.findData(
            str(self.cfg.get("saiUiRefresh", "full") or "full"))
        self.combo_sai_refresh.blockSignals(True)
        self.combo_sai_refresh.setCurrentIndex(
            _sai_refresh_idx if _sai_refresh_idx >= 0 else 0)
        self.combo_sai_refresh.blockSignals(False)
        
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












    def save_hotkeys(self, new_val=None):
        self.cfg["pickKey"] = self.btn_pick.val
        self.cfg["hideWindowKey"] = self.btn_hide.val
        self.cfg["followMouseKey"] = self.btn_follow.val
        self.cfg["grayscaleFilterKey"] = self.btn_grayscale.val
        self.cfg["toggleLabKey"] = self.btn_lab_toggle.val
        self.cfg["toggleLabGlobalKey"] = self.btn_lab_global.val
        self.cfg["toggleTitleBarKey"] = self.btn_title_bar.val
        self._persist_and_emit()

    def _grayscale_filter_config(self) -> dict:
        """Map the grayscale controls to persisted config values."""
        screen_text = self.combo_grayscale_screen.currentText()
        backend = self.combo_grayscale_backend.currentData() or "native"
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
            "grayscaleFilterBackend": "mag" if use_mag else "native",
        }

    def save_settings(self):
        self.cfg["ui-theme"] = self.combo_theme.currentData() or "auto"

        # Slider visual theme (key stored as combo item data)
        slider_key = self.combo_slider_style.currentData()
        self.cfg["sliderStyle"] = slider_key if slider_key else "default"
        
        self.cfg["followMouseEnabled"] = self.cb_follow_mouse.isChecked()
        self.cfg["lockWindowSize"] = self.cb_lock_size.isChecked()
        self.cfg["lockWindowPosition"] = self.cb_lock_position.isChecked()
        
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = self.cb_autostart.isChecked()
        if old_autostart != new_autostart:
            ok = autostart.apply_autostart(new_autostart)
            if not ok:
                # 注册表写入失败（windowed 下 print 不可见）：回滚配置与勾选
                self.cfg["openAtLogin"] = old_autostart
                self.cb_autostart.setChecked(old_autostart)
            
        self.cfg["onlyShowInCsp"] = self.cb_only_drawing.isChecked()
        self.cfg["showTaskbarIcon"] = self.cb_taskbar_icon.isChecked()
        self.cfg["showTitleBar"] = self.cb_show_title_bar.isChecked()
        self.cfg["noFocusMode"] = self.cb_no_focus.isChecked()
        
        # Sliders (all groups stored, but only current-module groups shown in UI)
        for key in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh"]:
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
            
        module_val_map = {"HSV": "hsv", "HLS": "hls", "RGB": "rgb", "LCH": "lch"}
        self.cfg["colorSpaceModule"] = module_val_map.get(self.combo_module.currentText(), "hsv")
        self.cfg["showModuleSwitchButton"] = self.cb_show_module_btn.isChecked()
        self.cfg["showLabToggleButton"] = self.cb_show_lab_toggle.isChecked()
        self.cfg["visualizerMode"] = self.combo_viz_mode.currentData() or "lab"
        self.cfg["showLabLightnessSlider"] = self.cb_show_lab_lightness.isChecked()

        # ── Ringless settings ──
        rcfg = self.ringless_settings.config()
        self.cfg["hideHueRing"] = rcfg.enabled
        self.cfg["ringlessControlsSide"] = rcfg.controls_side
        self.cfg["ringlessControlBarPosition"] = rcfg.control_bar_position
        
        self.cfg["syncSoftware"] = self.combo_software.currentData() or "csp"
        
        self.cfg["previewBoxPosition"] = self.combo_pos.currentData() or "top-left"
        
        self.cfg["cspVersion"] = self.combo_csp.currentData() or "auto"
        self.cfg["sai2Version"] = self.combo_sai.currentText()
        self.cfg["saiUiRefresh"] = self.combo_sai_refresh.currentData() or "full"
        
        udm_val_map = {"auto": "auto", "udm4.0pro": "udm4.0", "udm4.0ex": "udm4.0-ex"}
        self.cfg["udmVersion"] = udm_val_map.get(self.combo_udm.currentText(), "auto")
        self.cfg["psVersion"] = self.combo_ps.currentText()
        
        self.cfg["uiScale"] = self.zoom_slider.value()
        self.cfg["flipColorWheelHorizontally"] = self.cb_flip_wheel.isChecked()
        
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

    def _refresh_module_sliders(self):
        """Show only the slider rows that belong to the currently active module.

        Reads the in-memory ``self.cfg`` — reloading from disk here would
        discard unsaved changes (e.g. a just-switched language). External
        changes are picked up by :meth:`notify_module_changed` instead.
        """
        module = self.cfg.get("colorSpaceModule", "hsv")
        allowed = set(self._MODULE_SLIDER_MAP.get(module, ["HSV", "RGB", "LAB"]))
        for key, (cb, btn_up, btn_down, row_layout) in self.slider_rows.items():
            visible = key in allowed
            for i in range(row_layout.count()):
                w = row_layout.itemAt(i).widget()
                if w:
                    w.setVisible(visible)
        self._reorder_slider_rows_ui()
        self._update_slider_order_buttons()

    def _visible_slider_keys(self):
        """Slider keys the ordering controls act on: the active module's rows
        plus History (always shown), in global display order."""
        module = self.cfg.get("colorSpaceModule", "hsv")
        allowed = set(self._MODULE_SLIDER_MAP.get(module, ["HSV", "RGB", "LAB"]))
        return [k for k in config.sorted_slider_groups(self.cfg)
                if k == "History" or k in allowed]

    def _reorder_slider_rows_ui(self):
        """Visually reorder the slider rows to match the configured order, so
        every move up/down gives immediate feedback in this panel."""
        cl = getattr(self, "_sl_order_layout", None)
        if cl is None:
            return
        rows = [self.slider_rows[k][3]
                for k in config.sorted_slider_groups(self.cfg)
                if k in self.slider_rows]
        while cl.count():
            cl.takeAt(0)
        for row in rows:
            cl.addLayout(row)

    def _update_slider_order_buttons(self):
        """Disable up/down buttons at the visible-list boundaries."""
        if not hasattr(self, "slider_rows"):
            return
        visible = self._visible_slider_keys()
        if hasattr(self, "btn_hist_up"):
            try:
                hist_idx = visible.index("History")
            except ValueError:
                hist_idx = -1
            self.btn_hist_up.setEnabled(hist_idx > 0)
            self.btn_hist_down.setEnabled(0 <= hist_idx < len(visible) - 1)
        for key, (cb, btn_up, btn_down, row_layout) in self.slider_rows.items():
            try:
                idx = visible.index(key)
            except ValueError:
                continue
            btn_up.setEnabled(idx > 0)
            btn_down.setEnabled(idx < len(visible) - 1)

    def notify_module_changed(self):
        """Called by MainWindow when the module changes externally."""
        self.cfg = config.load_hotkey_config()
        self._refresh_module_sliders()

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

    def _move_slider_order(self, key, delta):
        """Move a slider group one step among the rows currently visible in
        this panel (the active module's rows plus History).

        Hidden groups keep their order slots, so every click produces a
        visible reorder instead of silently swapping with a row the user
        cannot see.
        """
        ordered = self._visible_slider_keys()
        try:
            idx = ordered.index(key)
        except ValueError:
            return
        target = idx + delta
        if not (0 <= target < len(ordered)):
            return
        other = ordered[target]
        key_val = config.get_slider_order(self.cfg, key)
        other_val = config.get_slider_order(self.cfg, other)
        self.cfg[config.slider_order_key(key)] = other_val
        self.cfg[config.slider_order_key(other)] = key_val
        self._persist_and_emit()
        self._reorder_slider_rows_ui()
        self._update_slider_order_buttons()





    # -- Green/portable Photoshop script-bridge notice ----------------------










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

    def on_no_focus_clicked(self, checked):
        self.save_settings()

    def showEvent(self, event):
        super().showEvent(event)
        # Settings are open: disable the main window's no-focus window flags
        # so the settings window can be used normally.  Mirrors hideEvent()
        # below and keeps every show/hide path (close button, hamburger,
        # eyedropper theme-pick re-show) in sync with the picker window.
        mv = self._parent
        if mv is not None and callable(getattr(mv, "update_window_flags", None)):
            mv.update_window_flags()
            mv.update_no_focus_policies()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Settings are closed: re-apply the no-focus window state if enabled.
        mv = self._parent
        if mv is not None and callable(getattr(mv, "update_window_flags", None)):
            mv.update_window_flags()
            mv.update_no_focus_policies()
