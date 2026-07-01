from PyQt6.QtWidgets import (QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QCheckBox, QComboBox, QPushButton, QSlider)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from core import config
from core import autostart
from ui.settings_dialog import HotkeyButton

class NonScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

class NonScrollSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()

class SettingsSidebar(QScrollArea):
    settingChanged = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.cfg = config.load_hotkey_config()
        self.init_ui()
        self.refresh_ui()
        
    def init_ui(self):
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(12)
        
        # 1. 快捷键 Section
        self.layout.addWidget(self.create_header("快捷键"))
        
        row_hide = QHBoxLayout()
        row_hide.addWidget(QLabel("隐藏界面"))
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"))
        self.btn_hide.hotkeyChanged.connect(self.save_hotkeys)
        row_hide.addWidget(self.btn_hide)
        self.layout.addLayout(row_hide)
        
        row_follow = QHBoxLayout()
        row_follow.addWidget(QLabel("随鼠标移动"))
        self.cb_follow_mouse = QCheckBox()
        self.cb_follow_mouse.stateChanged.connect(self.save_settings)
        row_follow.addStretch()
        row_follow.addWidget(self.cb_follow_mouse)
        self.layout.addLayout(row_follow)
        
        # 2. 界面 Section
        self.layout.addWidget(self.create_header("界面"))
        
        row_theme = QHBoxLayout()
        row_theme.addWidget(QLabel("背景"))
        self.combo_theme = NonScrollComboBox()
        self.combo_theme.addItems(["背景 自动（匹配CSP）", "背景 灰", "背景 白", "背景 黑"])
        self.combo_theme.currentTextChanged.connect(self.save_settings)
        row_theme.addWidget(self.combo_theme)
        self.layout.addLayout(row_theme)
        
        # Font size controls (- / +)
        row_font_size = QHBoxLayout()
        row_font_size.addWidget(QLabel("字体大小"))
        self.btn_font_dec = QPushButton("-")
        self.btn_font_dec.setFixedSize(20, 20)
        self.btn_font_dec.clicked.connect(self.font_decrease)
        self.lbl_font_size = QLabel("100%")
        self.lbl_font_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_font_size.setFixedSize(45, 20)
        
        self.btn_font_inc = QPushButton("+")
        self.btn_font_inc.setFixedSize(20, 20)
        self.btn_font_inc.clicked.connect(self.font_increase)
        
        row_font_size.addStretch()
        row_font_size.addWidget(self.btn_font_dec)
        row_font_size.addWidget(self.lbl_font_size)
        row_font_size.addWidget(self.btn_font_inc)
        self.layout.addLayout(row_font_size)
        
        # UI Scale controls (Slider)
        row_zoom = QHBoxLayout()
        row_zoom.addWidget(QLabel("界面缩放"))
        self.zoom_slider = NonScrollSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setObjectName("ScaleSlider")
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.valueChanged.connect(self.on_zoom_slider_changed)
        self.zoom_slider.sliderReleased.connect(self.save_settings)
        
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setFixedWidth(30)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        row_zoom.addWidget(self.zoom_slider)
        row_zoom.addWidget(self.lbl_zoom)
        self.layout.addLayout(row_zoom)
        
        # Checkboxes
        self.cb_taskbar_icon = QCheckBox("任务栏图标")
        self.cb_taskbar_icon.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_taskbar_icon)
        
        self.cb_lock_size = QCheckBox("固定窗口大小")
        self.cb_lock_size.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_lock_size)
        
        self.cb_autostart = QCheckBox("开机自启动")
        self.cb_autostart.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_autostart)
        
        self.cb_only_drawing = QCheckBox("仅在画图软件在前台时显示")
        self.cb_only_drawing.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_only_drawing)
        
        self.cb_auto_focus_drawing = QCheckBox("选色后自动返回画图软件")
        self.cb_auto_focus_drawing.clicked.connect(self.on_auto_focus_clicked)
        self.layout.addWidget(self.cb_auto_focus_drawing)
        
        self.cb_no_focus = QCheckBox("无焦点选色模式（不抢占画图软件焦点）")
        self.cb_no_focus.clicked.connect(self.on_no_focus_clicked)
        self.layout.addWidget(self.cb_no_focus)
        
        # 3. 滑块设置 Section
        self.layout.addWidget(self.create_header("滑块设置"))
        
        self.slider_rows = {}
        for key, name in [("RGB", "RGB 滑条"), ("HSV", "HSV 滑条"), ("HSL", "HLS 滑条"), ("LAB", "LAB 滑条")]:
            row = QHBoxLayout()
            cb = QCheckBox(name)
            cb.stateChanged.connect(self.save_settings)
            
            combo = NonScrollComboBox()
            combo.addItems(["1", "2", "3", "4"])
            combo.currentTextChanged.connect(self.save_settings)
            combo.setFixedWidth(50)
            
            row.addWidget(cb)
            row.addStretch()
            row.addWidget(combo)
            self.layout.addLayout(row)
            self.slider_rows[key] = (cb, combo)
            
        row_wheel = QHBoxLayout()
        row_wheel.addWidget(QLabel("色轮模式"))
        self.combo_wheel = NonScrollComboBox()
        self.combo_wheel.addItems(["HSV 正方形", "HLS 三角", "RGB 三角"])
        self.combo_wheel.currentTextChanged.connect(self.save_settings)
        row_wheel.addWidget(self.combo_wheel)
        self.layout.addLayout(row_wheel)
        
        row_pos = QHBoxLayout()
        row_pos.addWidget(QLabel("前背景色位置"))
        self.combo_pos = NonScrollComboBox()
        self.combo_pos.addItems(["左上角", "左下角"])
        self.combo_pos.currentTextChanged.connect(self.save_settings)
        row_pos.addWidget(self.combo_pos)
        self.layout.addLayout(row_pos)
        
        self.cb_show_lab_lightness = QCheckBox("显示 LAB 亮度滑条")
        self.cb_show_lab_lightness.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_show_lab_lightness)
        
        self.cb_flip_wheel = QCheckBox("水平翻转色环")
        self.cb_flip_wheel.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_flip_wheel)
        
        # 滚轮步长
        row_scroll = QHBoxLayout()
        row_scroll.addWidget(QLabel("滚轮单次步长"))
        self.btn_scroll_dec = QPushButton("-")
        self.btn_scroll_dec.setFixedSize(20, 20)
        self.btn_scroll_dec.clicked.connect(self.scroll_step_decrease)
        self.lbl_scroll_step = QLabel("1")
        self.lbl_scroll_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scroll_step.setFixedSize(45, 20)
        self.btn_scroll_inc = QPushButton("+")
        self.btn_scroll_inc.setFixedSize(20, 20)
        self.btn_scroll_inc.clicked.connect(self.scroll_step_increase)
        
        row_scroll.addStretch()
        row_scroll.addWidget(self.btn_scroll_dec)
        row_scroll.addWidget(self.lbl_scroll_step)
        row_scroll.addWidget(self.btn_scroll_inc)
        self.layout.addLayout(row_scroll)
        
        # 同一空间间距
        row_same = QHBoxLayout()
        row_same.addWidget(QLabel("同空间滑条间距"))
        self.btn_same_dec = QPushButton("-")
        self.btn_same_dec.setFixedSize(20, 20)
        self.btn_same_dec.clicked.connect(self.same_space_decrease)
        self.lbl_same_space = QLabel("6")
        self.lbl_same_space.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_same_space.setFixedSize(45, 20)
        self.btn_same_inc = QPushButton("+")
        self.btn_same_inc.setFixedSize(20, 20)
        self.btn_same_inc.clicked.connect(self.same_space_increase)
        
        row_same.addStretch()
        row_same.addWidget(self.btn_same_dec)
        row_same.addWidget(self.lbl_same_space)
        row_same.addWidget(self.btn_same_inc)
        self.layout.addLayout(row_same)
        
        # 不同空间间距
        row_diff = QHBoxLayout()
        row_diff.addWidget(QLabel("不同空间间距"))
        self.btn_diff_dec = QPushButton("-")
        self.btn_diff_dec.setFixedSize(20, 20)
        self.btn_diff_dec.clicked.connect(self.diff_space_decrease)
        self.lbl_diff_space = QLabel("8")
        self.lbl_diff_space.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_diff_space.setFixedSize(45, 20)
        self.btn_diff_inc = QPushButton("+")
        self.btn_diff_inc.setFixedSize(20, 20)
        self.btn_diff_inc.clicked.connect(self.diff_space_increase)
        
        row_diff.addStretch()
        row_diff.addWidget(self.btn_diff_dec)
        row_diff.addWidget(self.lbl_diff_space)
        row_diff.addWidget(self.btn_diff_inc)
        self.layout.addLayout(row_diff)
        
        # 4. 软件版本 Section
        self.layout.addWidget(self.create_header("软件版本"))
        
        row_sync = QHBoxLayout()
        row_sync.addWidget(QLabel("同步软件"))
        self.combo_software = NonScrollComboBox()
        self.combo_software.addItems(["CLIP Studio Paint", "SAI2", "UDM Paint"])
        self.combo_software.currentTextChanged.connect(self.save_settings)
        row_sync.addWidget(self.combo_software)
        self.layout.addLayout(row_sync)
        
        # CSP Version Container
        self.row_csp_widget = QWidget()
        row_csp_layout = QHBoxLayout(self.row_csp_widget)
        row_csp_layout.setContentsMargins(0, 0, 0, 0)
        row_csp_layout.addWidget(QLabel("CSP 版本"))
        self.combo_csp = NonScrollComboBox()
        self.combo_csp.addItems(["auto", "csp4.0", "csp4.2.7-ex", "csp5.0", "csp5.0-ex"])
        self.combo_csp.currentTextChanged.connect(self.save_settings)
        row_csp_layout.addWidget(self.combo_csp)
        self.layout.addWidget(self.row_csp_widget)
        
        # SAI2 Version Container
        self.row_sai_widget = QWidget()
        row_sai_layout = QHBoxLayout(self.row_sai_widget)
        row_sai_layout.setContentsMargins(0, 0, 0, 0)
        row_sai_layout.addWidget(QLabel("SAI2 版本"))
        self.combo_sai = NonScrollComboBox()
        self.combo_sai.addItems(["auto", "pre-2024-sai2", "after-2024-sai2"])
        self.combo_sai.currentTextChanged.connect(self.save_settings)
        row_sai_layout.addWidget(self.combo_sai)
        self.layout.addWidget(self.row_sai_widget)
        
        # UDM Version Container
        self.row_udm_widget = QWidget()
        row_udm_layout = QHBoxLayout(self.row_udm_widget)
        row_udm_layout.setContentsMargins(0, 0, 0, 0)
        row_udm_layout.addWidget(QLabel("UDM 版本"))
        self.combo_udm = NonScrollComboBox()
        self.combo_udm.addItems(["auto", "udm4.0pro", "udm4.0ex"])
        self.combo_udm.currentTextChanged.connect(self.save_settings)
        row_udm_layout.addWidget(self.combo_udm)
        self.layout.addWidget(self.row_udm_widget)
        
        # 5. 语言 Section
        self.layout.addWidget(self.create_header("语言"))
        row_lang = QHBoxLayout()
        row_lang.addWidget(QLabel("语言"))
        self.combo_lang = NonScrollComboBox()
        self.combo_lang.addItems(["中文", "English"])
        self.combo_lang.setCurrentText("中文")
        row_lang.addWidget(self.combo_lang)
        self.layout.addLayout(row_lang)
        
        self.layout.addStretch()
        self.setWidget(self.container)
        
    def create_header(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("SectionHeader")
        return lbl
        
    def refresh_ui(self):
        self.cfg = config.load_hotkey_config()
        
        # 1. Hotkeys
        self.btn_hide.setText(self.cfg.get("hideWindowKey", "Ctrl+H") if self.cfg.get("hideWindowKey") else "未绑定")
        self.btn_hide.val = self.cfg.get("hideWindowKey", "Ctrl+H")
        
        self.cb_follow_mouse.blockSignals(True)
        self.cb_follow_mouse.setChecked(self.cfg.get("followMouseEnabled", False))
        self.cb_follow_mouse.blockSignals(False)
        
        # 2. Interface
        theme_map = {"auto": "背景 自动（匹配CSP）", "gray": "背景 灰", "white": "背景 白", "black": "背景 黑"}
        self.combo_theme.blockSignals(True)
        self.combo_theme.setCurrentText(theme_map.get(self.cfg.get("ui-theme", "auto"), "背景 自动（匹配CSP）"))
        self.combo_theme.blockSignals(False)
        
        font_val = self.cfg.get("fontSize", 100)
        self.lbl_font_size.setText(f"{font_val}%")
        
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(self.cfg.get("uiScale", 100))
        self.zoom_slider.blockSignals(False)
        self.lbl_zoom.setText(f"{self.zoom_slider.value()}%")
        
        # Checkboxes
        for cb, key in [
            (self.cb_taskbar_icon, "showTaskbarIcon"),
            (self.cb_lock_size, "lockWindowSize"),
            (self.cb_autostart, "openAtLogin"),
            (self.cb_only_drawing, "onlyShowInCsp"),
            (self.cb_auto_focus_drawing, "autoFocusDrawingSoftware"),
            (self.cb_no_focus, "noFocusMode")
        ]:
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(key, False))
            cb.blockSignals(False)
            
        # 3. Sliders
        for key in ["RGB", "HSV", "HSL", "LAB"]:
            cb, combo = self.slider_rows[key]
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(f"showSliders{key}", True if key in ("HSV", "LAB") else False))
            cb.blockSignals(False)
            
            combo.blockSignals(True)
            combo.setCurrentText(str(self.cfg.get(f"orderSliders{key}", 1)))
            combo.blockSignals(False)
            
        wheel_mode_map = {"hsv": "HSV 正方形", "hls": "HLS 三角", "rgb": "RGB 三角"}
        self.combo_wheel.blockSignals(True)
        self.combo_wheel.setCurrentText(wheel_mode_map.get(self.cfg.get("colorWheelMode", "hsv"), "HSV 正方形"))
        self.combo_wheel.blockSignals(False)
        
        self.cb_show_lab_lightness.blockSignals(True)
        self.cb_show_lab_lightness.setChecked(self.cfg.get("showLabLightnessSlider", True))
        self.cb_show_lab_lightness.blockSignals(False)
        
        self.cb_flip_wheel.blockSignals(True)
        self.cb_flip_wheel.setChecked(self.cfg.get("flipColorWheelHorizontally", False))
        self.cb_flip_wheel.blockSignals(False)
        
        scroll_val = self.cfg.get("sliderScrollStep", 1)
        self.lbl_scroll_step.setText(str(scroll_val))
        
        same_val = self.cfg.get("sliderSameSpace", 6)
        self.lbl_same_space.setText(str(same_val))
        
        diff_val = self.cfg.get("sliderDiffSpace", 8)
        self.lbl_diff_space.setText(str(diff_val))
        
        # 4. Software Version
        software_map = {"csp": "CLIP Studio Paint", "sai": "SAI2", "udm": "UDM Paint"}
        self.combo_software.blockSignals(True)
        self.combo_software.setCurrentText(software_map.get(self.cfg.get("syncSoftware", "csp"), "CLIP Studio Paint"))
        self.combo_software.blockSignals(False)
        
        pos_map = {"top-left": "左上角", "bottom-left": "左下角"}
        self.combo_pos.blockSignals(True)
        self.combo_pos.setCurrentText(pos_map.get(self.cfg.get("previewBoxPosition", "top-left"), "左上角"))
        self.combo_pos.blockSignals(False)
        
        self.combo_csp.blockSignals(True)
        self.combo_csp.setCurrentText(self.cfg.get("cspVersion", "auto"))
        self.combo_csp.blockSignals(False)
        
        self.combo_sai.blockSignals(True)
        self.combo_sai.setCurrentText(self.cfg.get("sai2Version", "auto"))
        self.combo_sai.blockSignals(False)
        
        udm_display_map = {"auto": "auto", "udm4.0": "udm4.0pro", "udm4.0-ex": "udm4.0ex"}
        self.combo_udm.blockSignals(True)
        self.combo_udm.setCurrentText(udm_display_map.get(self.cfg.get("udmVersion", "auto"), "auto"))
        self.combo_udm.blockSignals(False)
        
        self.update_version_visibility()
        self.apply_theme()
        
    def update_version_visibility(self):
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm"}
        selected = software_val_map.get(self.combo_software.currentText(), "csp")
        self.row_csp_widget.setVisible(selected == "csp")
        self.row_sai_widget.setVisible(selected == "sai")
        self.row_udm_widget.setVisible(selected == "udm")

    def apply_theme(self):
        font_factor = self.cfg.get("fontSize", 100) / 100.0
        font_size = int(10 * font_factor)
        header_font_size = int(11 * font_factor)
        
        # Resolve active theme colors dynamically based on parent window
        bg, text, border_color, barBg = "#b2b2b2", "#222222", "#787878", "#787878"
        if hasattr(self, "parent") and self.parent is not None:
            p = self.parent
            theme_name = p.cfg.get("ui-theme", "auto")
            if theme_name == "auto":
                try:
                    from core.csp_color_sync import get_csp_theme
                    t = get_csp_theme()
                    bg = t["bg"]
                    text = t["text"]
                    border_color = t["border"].split(" ")[-1] if "solid" in t["border"] else t["border"]
                    barBg = border_color
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
        borderColor = "#d0d0d0" if is_dark_text else "#555555"
        
        self.lbl_font_size.setStyleSheet(f"""
            border: 1px solid {borderColor}; 
            background-color: {bg}; 
            color: {text};
            border-radius: 2px;
            font-size: {font_size}px;
        """)
        
        self.setStyleSheet(f"""
            QScrollArea {{
                background-color: {barBg};
                border: none;
            }}
            QWidget {{
                background-color: {barBg};
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {font_size}px;
            }}
            QLabel {{
                color: {text};
            }}
            QLabel#SectionHeader {{
                font-weight: bold;
                font-size: {header_font_size}px;
                margin-top: 5px;
                color: {text};
                border-bottom: 1px solid {borderColor};
                padding-bottom: 1px;
            }}
            QCheckBox {{
                color: {text};
            }}
            QComboBox {{
                background-color: {bg};
                border: 1px solid {borderColor};
                color: {text};
                border-radius: 2px;
                padding: 2px 4px;
            }}
            QPushButton {{
                background-color: {bg};
                border: 1px solid {borderColor};
                color: {text};
                border-radius: 2px;
                padding: 2px 6px;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {bg};
                border: 1px solid {borderColor};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {text};
                width: 10px;
                height: 10px;
                margin-top: -3px;
                margin-bottom: -3px;
                border-radius: 5px;
            }}
        """)

    def font_decrease(self):
        val = int(self.lbl_font_size.text().replace("%", ""))
        val = max(50, val - 10)
        self.lbl_font_size.setText(f"{val}%")
        self.cfg["fontSize"] = val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

    def font_increase(self):
        val = int(self.lbl_font_size.text().replace("%", ""))
        val = min(150, val + 10)
        self.lbl_font_size.setText(f"{val}%")
        self.cfg["fontSize"] = val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

    def on_zoom_slider_changed(self):
        v = self.zoom_slider.value()
        self.lbl_zoom.setText(f"{v}%")
        self.parent.zoom_ui(v / 100.0)

    def save_hotkeys(self, new_val=None):
        self.cfg["hideWindowKey"] = self.btn_hide.val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

    def save_settings(self):
        theme_val_map = {"背景 自动（匹配CSP）": "auto", "背景 灰": "gray", "背景 白": "white", "背景 黑": "black"}
        self.cfg["ui-theme"] = theme_val_map.get(self.combo_theme.currentText(), "auto")
        
        self.cfg["followMouseEnabled"] = self.cb_follow_mouse.isChecked()
        self.cfg["lockWindowSize"] = self.cb_lock_size.isChecked()
        
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = self.cb_autostart.isChecked()
        self.cfg["openAtLogin"] = new_autostart
        if old_autostart != new_autostart:
            autostart.apply_autostart(new_autostart)
            
        self.cfg["onlyShowInCsp"] = self.cb_only_drawing.isChecked()
        self.cfg["showTaskbarIcon"] = self.cb_taskbar_icon.isChecked()
        self.cfg["autoFocusDrawingSoftware"] = self.cb_auto_focus_drawing.isChecked()
        self.cfg["noFocusMode"] = self.cb_no_focus.isChecked()
        
        # Sliders
        for key in ["RGB", "HSV", "HSL", "LAB"]:
            self.cfg[f"showSliders{key}"] = self.slider_rows[key][0].isChecked()
            self.cfg[f"orderSliders{key}"] = int(self.slider_rows[key][1].currentText())
            
        wheel_val_map = {"HSV 正方形": "hsv", "HLS 三角": "hls", "RGB 三角": "rgb"}
        self.cfg["colorWheelMode"] = wheel_val_map.get(self.combo_wheel.currentText(), "hsv")
        self.cfg["showLabLightnessSlider"] = self.cb_show_lab_lightness.isChecked()
        
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm"}
        self.cfg["syncSoftware"] = software_val_map.get(self.combo_software.currentText(), "csp")
        
        pos_val_map = {"左上角": "top-left", "左下角": "bottom-left"}
        self.cfg["previewBoxPosition"] = pos_val_map.get(self.combo_pos.currentText(), "top-left")
        
        self.cfg["cspVersion"] = self.combo_csp.currentText()
        self.cfg["sai2Version"] = self.combo_sai.currentText()
        
        udm_val_map = {"auto": "auto", "udm4.0pro": "udm4.0", "udm4.0ex": "udm4.0-ex"}
        self.cfg["udmVersion"] = udm_val_map.get(self.combo_udm.currentText(), "auto")
        
        self.cfg["uiScale"] = self.zoom_slider.value()
        self.cfg["flipColorWheelHorizontally"] = self.cb_flip_wheel.isChecked()
        
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
        
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()
        self.update_version_visibility()
        self.apply_theme()

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

    def on_auto_focus_clicked(self, checked):
        if checked:
            self.cb_no_focus.blockSignals(True)
            self.cb_no_focus.setChecked(False)
            self.cb_no_focus.blockSignals(False)
        self.save_settings()

    def on_no_focus_clicked(self, checked):
        if checked:
            self.cb_auto_focus_drawing.blockSignals(True)
            self.cb_auto_focus_drawing.setChecked(False)
            self.cb_auto_focus_drawing.blockSignals(False)
        self.save_settings()
