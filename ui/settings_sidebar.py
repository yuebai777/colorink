import webbrowser

from PyQt6.QtWidgets import (QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QCheckBox, QComboBox, QPushButton, QSlider,
                             QMessageBox, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint, QThread
from PyQt6.QtGui import QColor, QCursor

from core import config
from core import updater
from core import autostart
from ui.settings_dialog import HotkeyButton
from ui.slider_themes import list_slider_theme_names

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
        
        row_pick = QHBoxLayout()
        row_pick.addWidget(QLabel("全局取色"))
        self.btn_pick = HotkeyButton("pickKey", self.cfg.get("pickKey", "F11"))
        self.btn_pick.hotkeyChanged.connect(self.save_hotkeys)
        row_pick.addWidget(self.btn_pick)
        self.layout.addLayout(row_pick)
        
        row_hide = QHBoxLayout()
        row_hide.addWidget(QLabel("隐藏界面"))
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"))
        self.btn_hide.hotkeyChanged.connect(self.save_hotkeys)
        row_hide.addWidget(self.btn_hide)
        self.layout.addLayout(row_hide)
        
        row_follow = QHBoxLayout()
        row_follow.addWidget(QLabel("随鼠标移动"))
        self.btn_follow = HotkeyButton("followMouseKey", self.cfg.get("followMouseKey", "Ctrl+R"))
        self.btn_follow.hotkeyChanged.connect(self.save_hotkeys)
        row_follow.addWidget(self.btn_follow)
        self.layout.addLayout(row_follow)
        
        row_grayscale = QHBoxLayout()
        row_grayscale.addWidget(QLabel("黑白滤镜"))
        self.btn_grayscale = HotkeyButton("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+G"))
        self.btn_grayscale.hotkeyChanged.connect(self.save_hotkeys)
        row_grayscale.addWidget(self.btn_grayscale)
        self.layout.addLayout(row_grayscale)
        
        row_grayscale_screen = QHBoxLayout()
        row_grayscale_screen.addWidget(QLabel("滤镜目标屏幕"))
        self.combo_grayscale_screen = NonScrollComboBox()
        self.combo_grayscale_screen.currentTextChanged.connect(self.save_settings)
        row_grayscale_screen.addWidget(self.combo_grayscale_screen)
        self.layout.addLayout(row_grayscale_screen)

        row_grayscale_mode = QHBoxLayout()
        row_grayscale_mode.addWidget(QLabel("黑白模式"))
        self.combo_grayscale_mode = NonScrollComboBox()
        self.combo_grayscale_mode.addItems(["OKLCh (感知均匀)", "Luma (BT.709 标准)"])
        self.combo_grayscale_mode.currentTextChanged.connect(self.save_settings)
        row_grayscale_mode.addWidget(self.combo_grayscale_mode)
        self.layout.addLayout(row_grayscale_mode)

        row_grayscale_backend = QHBoxLayout()
        row_grayscale_backend.addWidget(QLabel("渲染后端"))
        self.combo_grayscale_backend = NonScrollComboBox()
        self.combo_grayscale_backend.addItems(["OpenGL Overlay", "DComp 直通"])
        self.combo_grayscale_backend.currentTextChanged.connect(self.save_settings)
        row_grayscale_backend.addWidget(self.combo_grayscale_backend)
        self.layout.addLayout(row_grayscale_backend)
        
        # Picker zoom control
        row_picker_zoom = QHBoxLayout()
        row_picker_zoom.addWidget(QLabel("取色放大倍率"))
        self.btn_zoom_dec = QPushButton("-")
        self.btn_zoom_dec.setFixedSize(20, 20)
        self.btn_zoom_dec.clicked.connect(self.zoom_decrease)
        self.lbl_picker_zoom = QLabel("6×")
        self.lbl_picker_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_picker_zoom.setFixedSize(30, 20)
        self.btn_zoom_inc = QPushButton("+")
        self.btn_zoom_inc.setFixedSize(20, 20)
        self.btn_zoom_inc.clicked.connect(self.zoom_increase)
        row_picker_zoom.addStretch()
        row_picker_zoom.addWidget(self.btn_zoom_dec)
        row_picker_zoom.addWidget(self.lbl_picker_zoom)
        row_picker_zoom.addWidget(self.btn_zoom_inc)
        self.layout.addLayout(row_picker_zoom)
        
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
        self.combo_theme.addItems(["背景 自动（匹配CSP）", "背景 取色", "背景 灰", "背景 白", "背景 黑"])
        self.combo_theme.currentTextChanged.connect(self.save_settings)
        row_theme.addWidget(self.combo_theme)
        self.layout.addLayout(row_theme)

        # Eyedropper control rows (visible only when "取色" theme is selected)
        self._make_eyedropper_row("bar", "框色", "绘画软件标题栏/边框的深色")
        self._make_eyedropper_row("bg",  "底色", "绘画软件画布区域的浅色")

        # Slider visual theme (track width / handle style / label letter weight)
        row_slider_style = QHBoxLayout()
        row_slider_style.addWidget(QLabel("滑条样式"))
        self.combo_slider_style = NonScrollComboBox()
        for _key, _display in list_slider_theme_names():
            self.combo_slider_style.addItem(_display, _key)
        self.combo_slider_style.currentIndexChanged.connect(self.save_settings)
        row_slider_style.addWidget(self.combo_slider_style)
        self.layout.addLayout(row_slider_style)
        
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
        self.zoom_slider.sliderReleased.connect(self.on_zoom_slider_released)
        
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
        
        self.cb_lock_position = QCheckBox("锁定窗口位置")
        self.cb_lock_position.stateChanged.connect(self.save_settings)
        self.layout.addWidget(self.cb_lock_position)
        
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
        for key, name in [("RGB", "RGB 滑条"), ("HSV", "HSV 滑条"), ("HSL", "HLS 滑条"), ("LAB", "LAB 滑条"), ("OKLab", "OKLab 滑条"), ("OKLCh", "OKLCh 滑条"), ("History", "颜色历史")]:
            row = QHBoxLayout()
            cb = QCheckBox(name)
            cb.stateChanged.connect(self.save_settings)

            combo = NonScrollComboBox()
            combo.addItems(["1", "2", "3", "4", "5", "6", "7"])
            combo.currentTextChanged.connect(self.save_settings)
            combo.setFixedWidth(50)

            row.addWidget(cb)
            row.addStretch()
            row.addWidget(combo)
            self.layout.addLayout(row)
            self.slider_rows[key] = (cb, combo)

        # History grid shape — columns × rows. These only apply to the color
        # history widget, but live alongside the slider rows for grouping.
        row_hist_cols = QHBoxLayout()
        row_hist_cols.addWidget(QLabel("历史列数"))
        self.combo_history_cols = NonScrollComboBox()
        self.combo_history_cols.addItems(["4", "6", "8", "10", "12"])
        self.combo_history_cols.currentTextChanged.connect(self.save_settings)
        self.combo_history_cols.setFixedWidth(50)
        row_hist_cols.addStretch()
        row_hist_cols.addWidget(self.combo_history_cols)
        self.layout.addLayout(row_hist_cols)

        row_hist_rows = QHBoxLayout()
        row_hist_rows.addWidget(QLabel("历史行数"))
        self.combo_history_rows = NonScrollComboBox()
        self.combo_history_rows.addItems(["1", "2", "3", "4"])
        self.combo_history_rows.currentTextChanged.connect(self.save_settings)
        self.combo_history_rows.setFixedWidth(50)
        row_hist_rows.addStretch()
        row_hist_rows.addWidget(self.combo_history_rows)
        self.layout.addLayout(row_hist_rows)

        # Note: the per-cell swatch size is auto-fit to the window width —
        # the grid always spans the full slider strip. No manual size combo
        # is exposed because it would be overridden by the layout anyway.
            
        row_wheel = QHBoxLayout()
        row_wheel.addWidget(QLabel("色轮模式"))
        self.combo_wheel = NonScrollComboBox()
        self.combo_wheel.addItems(["HSV 正方形", "HLS 三角", "RGB 三角", "OKLCh 三角"])
        self.combo_wheel.currentTextChanged.connect(self.save_settings)
        row_wheel.addWidget(self.combo_wheel)
        self.layout.addLayout(row_wheel)
        
        row_viz = QHBoxLayout()
        row_viz.addWidget(QLabel("LAB图模式"))
        self.combo_viz_mode = NonScrollComboBox()
        self.combo_viz_mode.addItems(["LAB 色彩空间", "OKLab 色彩空间"])
        self.combo_viz_mode.currentTextChanged.connect(self.save_settings)
        row_viz.addWidget(self.combo_viz_mode)
        self.layout.addLayout(row_viz)
        
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
        self.combo_software.addItems(["CLIP Studio Paint", "SAI2", "UDM Paint", "Photoshop"])
        self.combo_software.currentTextChanged.connect(self.save_settings)
        row_sync.addWidget(self.combo_software)
        self.layout.addLayout(row_sync)
        
        # CSP Version Container
        self.row_csp_widget = QWidget()
        row_csp_layout = QHBoxLayout(self.row_csp_widget)
        row_csp_layout.setContentsMargins(0, 0, 0, 0)
        row_csp_layout.addWidget(QLabel("CSP 版本"))
        self.combo_csp = NonScrollComboBox()
        self.combo_csp.addItems(["auto", "csp4.x", "csp5.x"])
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
        
        # Photoshop version container (for future compatibility, PS COM is stable)
        self.row_ps_widget = QWidget()
        row_ps_layout = QHBoxLayout(self.row_ps_widget)
        row_ps_layout.setContentsMargins(0, 0, 0, 0)
        row_ps_layout.addWidget(QLabel("PS 版本"))
        self.combo_ps = NonScrollComboBox()
        self.combo_ps.addItems(["auto"])
        self.combo_ps.currentTextChanged.connect(self.save_settings)
        row_ps_layout.addWidget(self.combo_ps)
        self.layout.addWidget(self.row_ps_widget)
        
        # 5. 语言 Section
        self.layout.addWidget(self.create_header("语言"))
        row_lang = QHBoxLayout()
        row_lang.addWidget(QLabel("语言"))
        self.combo_lang = NonScrollComboBox()
        self.combo_lang.addItems(["中文", "English"])
        self.combo_lang.setCurrentText("中文")
        row_lang.addWidget(self.combo_lang)
        self.layout.addLayout(row_lang)
        
        # 6. 关于 Section
        self.layout.addWidget(self.create_header("关于"))

        row_version = QHBoxLayout()
        row_version.addWidget(QLabel("当前版本"))
        self.lbl_version_value = QLabel(f"v{updater.APP_VERSION}")
        self.lbl_version_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_version.addStretch()
        row_version.addWidget(self.lbl_version_value)
        self.layout.addLayout(row_version)

        self.btn_check_update = QPushButton("检查更新")
        self.btn_check_update.clicked.connect(self.on_check_update)
        self.layout.addWidget(self.btn_check_update)

        self.btn_about_author = QPushButton("关于作者")
        self.btn_about_author.clicked.connect(self.on_about_author)
        self.layout.addWidget(self.btn_about_author)

        self.layout.addStretch()
        self.setWidget(self.container)
        
    def create_header(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("SectionHeader")
        return lbl

    def _make_eyedropper_row(self, target, label_text, tooltip):
        """Create a single eyedropper control row (target = 'bar' or 'bg')."""
        widget = QWidget()
        widget.setObjectName(f"EyedropperRow_{target}")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(QLabel(label_text))

        lbl = QLabel("未设定")
        lbl.setStyleSheet("color: #888;")
        row.addWidget(lbl)

        btn_set = QPushButton("设定")
        btn_set.setToolTip(tooltip + " — 点击后窗口隐藏3秒，移鼠标到目标位置")
        btn_set.clicked.connect(lambda: self.start_eyedropper_pick(target))
        btn_sync = QPushButton("同步")
        btn_sync.setToolTip("从已设定的取色点立即同步颜色")
        btn_sync.clicked.connect(lambda: self.do_eyedropper_sync(target))
        row.addWidget(btn_set)
        row.addWidget(btn_sync)

        self.layout.addWidget(widget)
        widget.setVisible(False)

        setattr(self, f"_eye_row_{target}", widget)
        setattr(self, f"_eye_lbl_{target}", lbl)
        setattr(self, f"_eye_btn_set_{target}", btn_set)
        setattr(self, f"_eye_btn_sync_{target}", btn_sync)
        
    def refresh_ui(self):
        self.cfg = config.load_hotkey_config()
        
        # 1. Hotkeys
        self.btn_pick.setText(self.cfg.get("pickKey", "F11") if self.cfg.get("pickKey") else "未绑定")
        self.btn_pick.val = self.cfg.get("pickKey", "F11")
        
        self.btn_hide.setText(self.cfg.get("hideWindowKey", "Ctrl+H") if self.cfg.get("hideWindowKey") else "未绑定")
        self.btn_hide.val = self.cfg.get("hideWindowKey", "Ctrl+H")
        
        self.btn_follow.setText(self.cfg.get("followMouseKey", "Ctrl+R") if self.cfg.get("followMouseKey") else "未绑定")
        self.btn_follow.val = self.cfg.get("followMouseKey", "Ctrl+R")
        
        self.btn_grayscale.setText(self.cfg.get("grayscaleFilterKey", "Ctrl+G") if self.cfg.get("grayscaleFilterKey") else "未绑定")
        self.btn_grayscale.val = self.cfg.get("grayscaleFilterKey", "Ctrl+G")
        
        # Screen selector for grayscale filter
        from ui.grayscale_overlay import GrayscaleOverlay
        screens = GrayscaleOverlay.available_screens()
        self.combo_grayscale_screen.blockSignals(True)
        self.combo_grayscale_screen.clear()
        self.combo_grayscale_screen.addItems(screens)
        saved_target = self.cfg.get("grayscaleFilterScreen", "all")
        # Map "all" to display, and index to display format
        if saved_target == "all":
            self.combo_grayscale_screen.setCurrentText("all")
        else:
            # Find matching entry
            for item in screens:
                if item != "all" and item.startswith(f"{saved_target}:"):
                    self.combo_grayscale_screen.setCurrentText(item)
                    break
            else:
                self.combo_grayscale_screen.setCurrentText("all")
        self.combo_grayscale_screen.blockSignals(False)

        self.combo_grayscale_mode.blockSignals(True)
        mode = self.cfg.get("grayscaleFilterMode", "oklch")
        self.combo_grayscale_mode.setCurrentIndex(1 if mode == "luma" else 0)
        self.combo_grayscale_mode.blockSignals(False)

        self.combo_grayscale_backend.blockSignals(True)
        backend = self.cfg.get("grayscaleFilterBackend", "overlay")
        self.combo_grayscale_backend.setCurrentIndex(1 if backend == "dwm" else 0)
        self.combo_grayscale_backend.blockSignals(False)
        
        self.cb_follow_mouse.blockSignals(True)
        self.cb_follow_mouse.setChecked(self.cfg.get("followMouseEnabled", False))
        self.cb_follow_mouse.blockSignals(False)
        
        # 2. Interface
        theme_map = {"auto": "背景 自动（匹配CSP）", "eyedropper": "背景 取色", "gray": "背景 灰", "white": "背景 白", "black": "背景 黑"}
        self.combo_theme.blockSignals(True)
        self.combo_theme.setCurrentText(theme_map.get(self.cfg.get("ui-theme", "auto"), "背景 自动（匹配CSP）"))
        self.combo_theme.blockSignals(False)

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
                    lbl.setStyleSheet("color: inherit;")
                else:
                    lbl.setText("未设定")
                    lbl.setStyleSheet("color: #c44;")

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
            (self.cb_auto_focus_drawing, "autoFocusDrawingSoftware"),
            (self.cb_no_focus, "noFocusMode")
        ]:
            cb.blockSignals(True)
            cb.setChecked(self.cfg.get(key, False))
            cb.blockSignals(False)
            
        # 3. Sliders
        for key in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh", "History"]:
            cb, combo = self.slider_rows[key]
            cb.blockSignals(True)
            if key == "History":
                cb.setChecked(self.cfg.get("showSlidersHistory", True))
            else:
                cb.setChecked(self.cfg.get(f"showSliders{key}", True if key in ("HSV", "LAB", "OKLab") else False))
            cb.blockSignals(False)

            combo.blockSignals(True)
            if key == "History":
                combo.setCurrentText(str(self.cfg.get("orderSlidersHistory", 7)))
            else:
                combo.setCurrentText(str(self.cfg.get(f"orderSliders{key}", 1)))
            combo.blockSignals(False)

        # History grid shape (columns × rows × swatch size)
        self.combo_history_cols.blockSignals(True)
        self.combo_history_cols.setCurrentText(str(self.cfg.get("historyColumns", 8)))
        self.combo_history_cols.blockSignals(False)

        self.combo_history_rows.blockSignals(True)
        self.combo_history_rows.setCurrentText(str(self.cfg.get("historyRows", 2)))
        self.combo_history_rows.blockSignals(False)
            
        wheel_mode_map = {"hsv": "HSV 正方形", "hls": "HLS 三角", "rgb": "RGB 三角", "oklch": "OKLCh 三角"}
        self.combo_wheel.blockSignals(True)
        self.combo_wheel.setCurrentText(wheel_mode_map.get(self.cfg.get("colorWheelMode", "hsv"), "HSV 正方形"))
        self.combo_wheel.blockSignals(False)
        
        viz_mode_map = {"lab": "LAB 色彩空间", "oklab": "OKLab 色彩空间"}
        self.combo_viz_mode.blockSignals(True)
        self.combo_viz_mode.setCurrentText(viz_mode_map.get(self.cfg.get("visualizerMode", "lab"), "LAB 色彩空间"))
        self.combo_viz_mode.blockSignals(False)
        
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
        software_map = {"csp": "CLIP Studio Paint", "sai": "SAI2", "udm": "UDM Paint", "ps": "Photoshop"}
        self.combo_software.blockSignals(True)
        self.combo_software.setCurrentText(software_map.get(self.cfg.get("syncSoftware", "csp"), "CLIP Studio Paint"))
        self.combo_software.blockSignals(False)
        
        pos_map = {"top-left": "左上角", "bottom-left": "左下角"}
        self.combo_pos.blockSignals(True)
        self.combo_pos.setCurrentText(pos_map.get(self.cfg.get("previewBoxPosition", "top-left"), "左上角"))
        self.combo_pos.blockSignals(False)
        
        # Migrate legacy CSP version keys to simplified 4.x / 5.x scheme
        _csp_migration = {"csp4.0": "csp4.x", "csp4.2.7-ex": "csp4.x",
                          "csp5.0": "csp5.x", "csp5.0-ex": "csp5.x"}
        raw_csp = self.cfg.get("cspVersion", "auto")
        self.combo_csp.blockSignals(True)
        self.combo_csp.setCurrentText(_csp_migration.get(raw_csp, raw_csp))
        self.combo_csp.blockSignals(False)
        
        self.combo_sai.blockSignals(True)
        self.combo_sai.setCurrentText(self.cfg.get("sai2Version", "auto"))
        self.combo_sai.blockSignals(False)
        
        udm_display_map = {"auto": "auto", "udm4.0": "udm4.0pro", "udm4.0-ex": "udm4.0ex"}
        self.combo_udm.blockSignals(True)
        self.combo_udm.setCurrentText(udm_display_map.get(self.cfg.get("udmVersion", "auto"), "auto"))
        self.combo_udm.blockSignals(False)
        
        self.combo_ps.blockSignals(True)
        self.combo_ps.setCurrentText(self.cfg.get("psVersion", "auto"))
        self.combo_ps.blockSignals(False)
        
        self.update_version_visibility()
        self.apply_theme()
        
    def update_version_visibility(self):
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm", "Photoshop": "ps"}
        selected = software_val_map.get(self.combo_software.currentText(), "csp")
        self.row_csp_widget.setVisible(selected == "csp")
        self.row_sai_widget.setVisible(selected == "sai")
        self.row_udm_widget.setVisible(selected == "udm")
        self.row_ps_widget.setVisible(selected == "ps")

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

    def zoom_decrease(self):
        val = int(self.lbl_picker_zoom.text().replace("×", ""))
        val = max(2, val - 1)
        self.lbl_picker_zoom.setText(f"{val}×")
        self.cfg["pickerZoom"] = val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

    def zoom_increase(self):
        val = int(self.lbl_picker_zoom.text().replace("×", ""))
        val = min(12, val + 1)
        self.lbl_picker_zoom.setText(f"{val}×")
        self.cfg["pickerZoom"] = val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

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
        self.parent.zoom_ui(snapped / 100.0)
        self.parent.current_ui_scale = snapped
        # Persist to config; on_settings_saved will see scale already matches → cheap update()
        self.save_settings()

    def save_hotkeys(self, new_val=None):
        self.cfg["pickKey"] = self.btn_pick.val
        self.cfg["hideWindowKey"] = self.btn_hide.val
        self.cfg["followMouseKey"] = self.btn_follow.val
        self.cfg["grayscaleFilterKey"] = self.btn_grayscale.val
        config.save_hotkey_config(self.cfg)
        self.settingChanged.emit()

    def save_settings(self):
        theme_val_map = {"背景 自动（匹配CSP）": "auto", "背景 取色": "eyedropper", "背景 灰": "gray", "背景 白": "white", "背景 黑": "black"}
        self.cfg["ui-theme"] = theme_val_map.get(self.combo_theme.currentText(), "auto")

        # Slider visual theme (key stored as combo item data)
        slider_key = self.combo_slider_style.currentData()
        self.cfg["sliderStyle"] = slider_key if slider_key else "default"
        
        self.cfg["followMouseEnabled"] = self.cb_follow_mouse.isChecked()
        self.cfg["lockWindowSize"] = self.cb_lock_size.isChecked()
        self.cfg["lockWindowPosition"] = self.cb_lock_position.isChecked()
        
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
        for key in ["RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh", "History"]:
            if key == "History":
                self.cfg["showSlidersHistory"] = self.slider_rows[key][0].isChecked()
                self.cfg["orderSlidersHistory"] = int(self.slider_rows[key][1].currentText())
            else:
                self.cfg[f"showSliders{key}"] = self.slider_rows[key][0].isChecked()
                self.cfg[f"orderSliders{key}"] = int(self.slider_rows[key][1].currentText())

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
            
        wheel_val_map = {"HSV 正方形": "hsv", "HLS 三角": "hls", "RGB 三角": "rgb", "OKLCh 三角": "oklch"}
        self.cfg["colorWheelMode"] = wheel_val_map.get(self.combo_wheel.currentText(), "hsv")
        viz_val_map = {"LAB 色彩空间": "lab", "OKLab 色彩空间": "oklab"}
        self.cfg["visualizerMode"] = viz_val_map.get(self.combo_viz_mode.currentText(), "lab")
        self.cfg["showLabLightnessSlider"] = self.cb_show_lab_lightness.isChecked()
        
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm", "Photoshop": "ps"}
        self.cfg["syncSoftware"] = software_val_map.get(self.combo_software.currentText(), "csp")
        
        pos_val_map = {"左上角": "top-left", "左下角": "bottom-left"}
        self.cfg["previewBoxPosition"] = pos_val_map.get(self.combo_pos.currentText(), "top-left")
        
        self.cfg["cspVersion"] = self.combo_csp.currentText()
        self.cfg["sai2Version"] = self.combo_sai.currentText()
        
        udm_val_map = {"auto": "auto", "udm4.0pro": "udm4.0", "udm4.0ex": "udm4.0-ex"}
        self.cfg["udmVersion"] = udm_val_map.get(self.combo_udm.currentText(), "auto")
        self.cfg["psVersion"] = self.combo_ps.currentText()
        
        self.cfg["uiScale"] = self.zoom_slider.value()
        self.cfg["flipColorWheelHorizontally"] = self.cb_flip_wheel.isChecked()
        
        # Grayscale filter screen target
        screen_text = self.combo_grayscale_screen.currentText()
        self.cfg["grayscaleFilterScreen"] = screen_text.split(":")[0].strip() if ":" in screen_text else screen_text
        # Grayscale filter mode
        mode_text = self.combo_grayscale_mode.currentText()
        self.cfg["grayscaleFilterMode"] = "luma" if "Luma" in mode_text else "oklch"
        # Grayscale filter backend
        backend_text = self.combo_grayscale_backend.currentText()
        self.cfg["grayscaleFilterBackend"] = "dwm" if "DComp" in backend_text else "overlay"

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
                    lbl.setStyleSheet("color: inherit;")
                else:
                    lbl.setText("未设定")
                    lbl.setStyleSheet("color: #c44;")
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

    # ── Eyedropper dual-point pick ────────────────────────────────────────
    def start_eyedropper_pick(self, target):
        """Hide palette → 3s countdown → capture cursor for 'bar' or 'bg'."""
        self._eye_target = target
        self._eye_countdown = 3
        btn_set = getattr(self, f"_eye_btn_set_{target}")
        btn_set.setEnabled(False)
        btn_set.setText("3...")
        if self.parent is not None:
            self.parent.hide()
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
            btn_set.setText("设定")
            btn_set.setEnabled(True)
            if self.parent is not None:
                self.parent.show()
            pos = QCursor.pos()
            self._on_eyedropper_point_picked(pos.x(), pos.y())

    def _on_eyedropper_point_picked(self, x: int, y: int):
        target = self._eye_target
        point_key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
        self.cfg[point_key] = {"x": x, "y": y}
        config.save_hotkey_config(self.cfg)
        lbl = getattr(self, f"_eye_lbl_{target}")
        lbl.setText(f"({x}, {y})")
        lbl.setStyleSheet("color: inherit;")
        self.do_eyedropper_sync(target)

    @staticmethod
    def _grab_median_color(x, y):
        """Grab 3×3 median color from screen at logical coords (x, y) via GDI."""
        import ctypes
        # Convert logical → physical pixels (Qt uses logical, GDI needs physical)
        screen = QApplication.screenAt(QPoint(x, y))
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        if dpr < 0.1:
            dpr = 1.0
        px, py = int(x * dpr), int(y * dpr)

        hdc = ctypes.windll.gdi32.CreateDCW("DISPLAY", None, None, None)
        rs, gs, bs = [], [], []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                pixel = ctypes.windll.gdi32.GetPixel(hdc, px + dx, py + dy)
                rs.append(pixel & 0xFF)
                gs.append((pixel >> 8) & 0xFF)
                bs.append((pixel >> 16) & 0xFF)
        ctypes.windll.gdi32.DeleteDC(hdc)
        rs.sort(); gs.sort(); bs.sort()
        return f"#{rs[4]:02x}{gs[4]:02x}{bs[4]:02x}"

    def do_eyedropper_sync(self, target):
        """Sync color from the fixed pick point for 'bar' or 'bg'."""
        point_key = "uiThemeDropperPointBar" if target == "bar" else "uiThemeDropperPointBg"
        color_key = "uiThemeDropperColorBar" if target == "bar" else "uiThemeDropperColorBg"
        pt = self.cfg.get(point_key, None)
        if not pt or not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
            return
        try:
            hex_color = self._grab_median_color(pt["x"], pt["y"])
            self.cfg[color_key] = hex_color
            config.save_hotkey_config(self.cfg)
            self.settingChanged.emit()
        except Exception:
            pass

    # 6. 关于 — 检查更新 / 关于作者
    def on_check_update(self):
        """Run the update check on a worker thread, then show a dialog."""
        if getattr(self, "_update_worker", None) is not None:
            return  # Already running
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中...")
        worker = _UpdateWorker(self)
        worker.done.connect(self._on_update_result)
        # Keep a reference alive until the signal fires; QThread auto-deletes
        # via finished->deleteLater once we let go in the slot.
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _on_update_result(self, result: dict):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("检查更新")
        self._update_worker = None

        if "error" in result:
            QMessageBox.warning(self, "检查更新", result["error"])
            return

        current = result.get("current_version", "?")
        latest = result.get("latest_version", "?")
        url = result.get("release_url", updater.GITHUB_URL)
        notes = result.get("release_notes", "")
        has_update = result.get("has_update", False)

        if has_update:
            msg = (
                f"发现新版本 {latest}！\n"
                f"当前版本: v{current}\n\n"
                f"是否前往 GitHub 下载？"
            )
            if notes:
                snippet = notes if len(notes) <= 600 else notes[:600] + "..."
                msg += f"\n\n更新内容:\n{snippet}"
            box = QMessageBox(self)
            box.setWindowTitle("发现新版本")
            box.setText(msg)
            open_btn = box.addButton("前往下载", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                webbrowser.open(url)
        else:
            QMessageBox.information(
                self, "检查更新", f"已是最新版本 (v{current})"
            )

    def on_about_author(self):
        """Open the author's Bilibili homepage in the default browser."""
        webbrowser.open(updater.BILIBILI_URL)


class _UpdateWorker(QThread):
    """Background worker that queries GitHub for the latest release."""

    done = pyqtSignal(dict)

    def run(self):  # noqa: D401 - QThread override
        self.done.emit(updater.check_for_update())
