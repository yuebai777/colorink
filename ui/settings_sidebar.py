import json
import math
import os
import webbrowser
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QPoint, QPointF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import autostart, config, updater
from ui.hotkey_button import HotkeyButton
from ui.ringless_mode import RinglessConfig
from ui.ringless_settings import RinglessSettingsWidget
from ui.slider_themes import list_slider_theme_names

if TYPE_CHECKING:
    from ui.main_window import MainWindow

# Resolve resource paths relative to the repo root so packaged builds
# (PyInstaller) work regardless of the current working directory.
_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
_CHECKBOX_CHECK_ICON = os.path.join(_ICONS_DIR, "checkbox_check.png").replace("\\", "/")
_ARROW_DOWN_DARK = os.path.join(_ICONS_DIR, "arrow_down_dark.png").replace("\\", "/")
_ARROW_DOWN_LIGHT = os.path.join(_ICONS_DIR, "arrow_down_light.png").replace("\\", "/")

class NonScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

class NonScrollSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


class SettingsSidebar(QWidget):
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
        
    def init_ui(self):
        # Main layout: CSP-style left rail navigation + stacked pages
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
            ("软件", "software"),
            ("关于", "about"),
        ]:
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 28))
            self.nav.addItem(item)
            self._nav_icons[text] = kind
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        body.addWidget(self.nav)

        # ── Right: stacked pages ──
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self._layout.addLayout(body)
        self.stack.currentChanged.connect(self._on_page_changed)

        self._page_layouts = {}

        # Create all 5 pages
        page_hotkeys   = self._make_page("快捷键")
        page_interface = self._make_page("界面")
        page_picker    = self._make_page("取色器")
        page_software  = self._make_page("软件")
        page_about     = self._make_page("关于")

        # ═══════════════════ Page 1: 快捷键 ═══════════════════
        card_hk, cl_hk = self._begin_card(page_hotkeys, "全局热键")

        grid_hotkeys = QGridLayout()
        grid_hotkeys.setSpacing(6)
        grid_hotkeys.setColumnMinimumWidth(0, 84)
        grid_hotkeys.setColumnStretch(1, 1)

        grid_hotkeys.addWidget(QLabel("全局取色"), 0, 0)
        self.btn_pick = HotkeyButton("pickKey", self.cfg.get("pickKey", "F11"))
        self.btn_pick.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_pick, 0, 1)

        grid_hotkeys.addWidget(QLabel("隐藏界面"), 1, 0)
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"))
        self.btn_hide.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_hide, 1, 1)

        grid_hotkeys.addWidget(QLabel("随鼠标移动"), 2, 0)
        self.btn_follow = HotkeyButton("followMouseKey", self.cfg.get("followMouseKey", "Ctrl+R"))
        self.btn_follow.hotkeyChanged.connect(self.save_hotkeys)
        row_follow = QHBoxLayout()
        row_follow.setSpacing(6)
        self.cb_follow_mouse = QCheckBox("启用")
        self.cb_follow_mouse.stateChanged.connect(self.save_settings)
        row_follow.addWidget(self.btn_follow)
        row_follow.addWidget(self.cb_follow_mouse)
        row_follow.addStretch()
        grid_hotkeys.addLayout(row_follow, 2, 1)

        grid_hotkeys.addWidget(QLabel("黑白滤镜"), 3, 0)
        self.btn_grayscale = HotkeyButton("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+G"))
        self.btn_grayscale.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_grayscale, 3, 1)

        cl_hk.addLayout(grid_hotkeys)
        page_hotkeys.addWidget(card_hk)

        # ═══════════════════ Page 2: 界面 ═══════════════════
        card_appear, cl_appear = self._begin_card(page_interface, "外观")
        self._card_layout_interface_bg = cl_appear  # stored for _make_eyedropper_row

        grid_appear = QGridLayout()
        grid_appear.setSpacing(6)
        grid_appear.setColumnMinimumWidth(0, 84)
        grid_appear.setColumnStretch(1, 1)

        # Background theme
        grid_appear.addWidget(QLabel("背景主题"), 0, 0)
        self.combo_theme = NonScrollComboBox()
        self.combo_theme.addItems(["背景 自动（匹配CSP）", "背景 取色", "背景 灰", "背景 白", "背景 黑"])
        self.combo_theme.currentTextChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_theme, 0, 1)

        # Slider visual theme
        grid_appear.addWidget(QLabel("滑条样式"), 1, 0)
        self.combo_slider_style = NonScrollComboBox()
        for _key, _display in list_slider_theme_names():
            self.combo_slider_style.addItem(_display, _key)
        self.combo_slider_style.currentIndexChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_slider_style, 1, 1)

        # Font size controls (- / +)
        grid_appear.addWidget(QLabel("字体大小"), 2, 0)
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
        grid_appear.addWidget(QLabel("界面缩放"), 3, 0)
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
        self._make_eyedropper_row("bar", "框色", "绘画软件标题栏/边框的深色")
        self._make_eyedropper_row("bg",  "底色", "绘画软件画布区域的浅色")

        page_interface.addWidget(card_appear)

        card_gray, cl_gray = self._begin_card(page_interface, "灰度滤镜")

        grid_gray = QGridLayout()
        grid_gray.setSpacing(6)
        grid_gray.setColumnMinimumWidth(0, 84)
        grid_gray.setColumnStretch(1, 1)

        grid_gray.addWidget(QLabel("滤镜目标屏幕"), 0, 0)
        self.combo_grayscale_screen = NonScrollComboBox()
        self.combo_grayscale_screen.setToolTip("选择黑白滤镜作用在哪个屏幕，默认作用于全部屏幕")
        self.combo_grayscale_screen.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_screen, 0, 1)

        grid_gray.addWidget(QLabel("黑白模式"), 1, 0)
        self.combo_grayscale_mode = NonScrollComboBox()
        self.combo_grayscale_mode.addItems(["OKLCh (感知均匀)", "Luma (BT.709 标准)"])
        self.combo_grayscale_mode.setToolTip("OKLCh 更接近人眼感知；Luma 是标准亮度转换")
        self.combo_grayscale_mode.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_mode, 1, 1)

        grid_gray.addWidget(QLabel("渲染后端 (高级)"), 2, 0)
        self.combo_grayscale_backend = NonScrollComboBox()
        self.combo_grayscale_backend.addItems(["OpenGL Overlay", "DComp 直通", "Rust D3D11", "系统级 (Mag)"])
        self.combo_grayscale_backend.setToolTip(
            "不同系统的兼容性不同，默认 OpenGL Overlay；Rust 后端仅支持 OKLCh；"
            "系统级 (Mag) 与 Windows 自带滤镜同路径，最流畅（不捕获屏幕、不改显示器配置文件）")
        self.combo_grayscale_backend.currentTextChanged.connect(self._on_grayscale_backend_changed)
        grid_gray.addWidget(self.combo_grayscale_backend, 2, 1)

        cl_gray.addLayout(grid_gray)
        page_interface.addWidget(card_gray)

        card_behavior, cl_behavior = self._begin_card(page_interface, "行为")

        # 6 checkboxes in symmetric 3×2 grid
        grid_behavior = QGridLayout()
        grid_behavior.setSpacing(6)

        self.cb_taskbar_icon = QCheckBox("任务栏图标")
        self.cb_taskbar_icon.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_taskbar_icon, 0, 0)

        self.cb_lock_size = QCheckBox("固定窗口大小")
        self.cb_lock_size.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_lock_size, 1, 0)

        self.cb_lock_position = QCheckBox("锁定窗口位置")
        self.cb_lock_position.setToolTip("开启后不能拖动窗口")
        self.cb_lock_position.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_lock_position, 2, 0)

        self.cb_autostart = QCheckBox("开机自启动")
        self.cb_autostart.setToolTip("开机后自动以管理员权限启动（免 UAC 弹窗）")
        self.cb_autostart.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_autostart, 0, 1)

        self.cb_only_drawing = QCheckBox("仅在画图软件前台时显示")
        self.cb_only_drawing.setToolTip("画图软件不在前台时自动隐藏悬浮面板")
        self.cb_only_drawing.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_only_drawing, 1, 1)

        self.cb_no_focus = QCheckBox("无焦点选色模式")
        self.cb_no_focus.setToolTip("开启后不会抢占画图软件的键盘焦点，适合边画边选色")
        self.cb_no_focus.clicked.connect(self.on_no_focus_clicked)
        grid_behavior.addWidget(self.cb_no_focus, 2, 1)

        cl_behavior.addLayout(grid_behavior)
        page_interface.addWidget(card_behavior)

        # ═══════════════════ Page 3: 取色器 ═══════════════════
        card_pz, cl_pz = self._begin_card(page_picker, "取色器")
        grid_pz = QGridLayout()
        grid_pz.setSpacing(6)
        grid_pz.setColumnMinimumWidth(0, 84)
        grid_pz.setColumnStretch(1, 1)

        grid_pz.addWidget(QLabel("取色放大倍率"), 0, 0)
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

        grid_pz.addWidget(QLabel("前背景色位置"), 1, 0)
        self.combo_pos = NonScrollComboBox()
        self.combo_pos.addItems(["左上角", "左下角"])
        self.combo_pos.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_pos, 1, 1)

        grid_pz.addWidget(QLabel("色彩空间模块"), 2, 0)
        self.combo_module = NonScrollComboBox()
        self.combo_module.addItems(["HSV", "HLS", "RGB", "LCH"])
        self.combo_module.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_module, 2, 1)

        cl_pz.addLayout(grid_pz)

        self.cb_show_module_btn = QCheckBox("显示模块切换按钮")
        self.cb_show_module_btn.setToolTip("在色环区域显示色彩空间模块切换按钮")
        self.cb_show_module_btn.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_module_btn)

        page_picker.addWidget(card_pz)

        card_sl_order, cl_sl_order = self._begin_card(page_picker, "滑块显示与顺序")

        self._MODULE_SLIDER_MAP = {
            "hsv":  ["HSV", "RGB", "LAB", "OKLab", "OKLCh"],
            "hls":  ["HSL", "RGB", "LAB", "OKLab", "OKLCh"],
            "rgb":  ["RGB", "HSV", "LAB", "OKLab", "OKLCh"],
            "lch":  ["OKLCh", "OKLab", "RGB"],
        }
        self.slider_rows = {}
        for key, name in [("RGB", "RGB 滑条"), ("HSV", "HSV 滑条"), ("HSL", "HLS 滑条"),
                          ("LAB", "LAB 滑条"), ("OKLab", "OKLab 滑条"), ("OKLCh", "OKLCh 滑条")]:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            cb = QCheckBox(name)
            cb.stateChanged.connect(self.save_settings)
            btn_up = self._make_step_button("▲", "上移", width=24)
            btn_up.clicked.connect(lambda _checked, k=key: self._move_slider_order(k, -1))
            btn_down = self._make_step_button("▼", "下移", width=24)
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

        card_hist, cl_hist = self._begin_card(page_picker, "颜色历史")

        row_hist_show = QHBoxLayout()
        row_hist_show.setSpacing(6)
        self.cb_history = QCheckBox("显示颜色历史")
        self.cb_history.stateChanged.connect(self.save_settings)
        self.btn_hist_up = self._make_step_button("▲", "在滑块顺序中上移", width=24)
        self.btn_hist_up.clicked.connect(lambda _checked: self._move_slider_order("History", -1))
        self.btn_hist_down = self._make_step_button("▼", "在滑块顺序中下移", width=24)
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

        grid_hist.addWidget(QLabel("历史列数"), 0, 0)
        self.combo_history_cols = NonScrollComboBox()
        self.combo_history_cols.addItems(["3", "4", "5", "6", "7", "8", "9", "10", "12", "14", "16"])
        self.combo_history_cols.currentTextChanged.connect(self.save_settings)
        self.combo_history_cols.setFixedWidth(50)
        grid_hist.addWidget(self.combo_history_cols, 0, 1)

        grid_hist.addWidget(QLabel("历史行数"), 1, 0)
        self.combo_history_rows = NonScrollComboBox()
        self.combo_history_rows.addItems(["1", "2", "3", "4", "5", "6", "8"])
        self.combo_history_rows.currentTextChanged.connect(self.save_settings)
        self.combo_history_rows.setFixedWidth(50)
        grid_hist.addWidget(self.combo_history_rows, 1, 1)

        cl_hist.addLayout(grid_hist)
        page_picker.addWidget(card_hist)

        card_wheel, cl_wheel = self._begin_card(page_picker, "色环与 LAB")

        # Ringless mode settings
        self.ringless_settings = RinglessSettingsWidget()
        self.ringless_settings.changed.connect(self.save_settings)
        cl_wheel.addWidget(self.ringless_settings)

        grid_wheel = QGridLayout()
        grid_wheel.setSpacing(6)
        grid_wheel.setColumnMinimumWidth(0, 84)
        grid_wheel.setColumnStretch(1, 1)

        grid_wheel.addWidget(QLabel("LAB图模式"), 0, 0)
        self.combo_viz_mode = NonScrollComboBox()
        self.combo_viz_mode.addItems(["LAB 色彩空间", "OKLab 色彩空间"])
        self.combo_viz_mode.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_viz_mode, 0, 1)

        cl_wheel.addLayout(grid_wheel)

        self.cb_show_lab_lightness = QCheckBox("显示 LAB 亮度滑条")
        self.cb_show_lab_lightness.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self.cb_show_lab_lightness)

        self.cb_flip_wheel = QCheckBox("水平翻转色环")
        self.cb_flip_wheel.stateChanged.connect(self.save_settings)
        cl_wheel.addWidget(self.cb_flip_wheel)

        page_picker.addWidget(card_wheel)

        card_sp, cl_sp = self._begin_card(page_picker, "高级")

        grid_sp = QGridLayout()
        grid_sp.setSpacing(6)
        grid_sp.setColumnMinimumWidth(0, 84)
        grid_sp.setColumnStretch(1, 1)

        # 滚轮步长
        grid_sp.addWidget(QLabel("滚轮单次步长"), 0, 0)
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
        grid_sp.addWidget(QLabel("同空间滑条间距"), 1, 0)
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

        # 不同空间间距
        grid_sp.addWidget(QLabel("不同空间间距"), 2, 0)
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

        # ═══════════════════ Page 4: 软件 ═══════════════════
        card_sync, cl_sync = self._begin_card(page_software, "同步与版本")

        self.lbl_sync_status = QLabel("")
        self.lbl_sync_status.setObjectName("StatusHint")
        cl_sync.addWidget(self.lbl_sync_status)

        grid_sync = QGridLayout()
        grid_sync.setSpacing(6)
        grid_sync.setColumnMinimumWidth(0, 84)
        grid_sync.setColumnStretch(1, 1)
        grid_sync.addWidget(QLabel("同步软件"), 0, 0)
        self.combo_software = NonScrollComboBox()
        self.combo_software.addItems(["CLIP Studio Paint", "SAI2", "UDM Paint", "Photoshop", "CSP 智能手机 (R)"])
        self.combo_software.currentTextChanged.connect(self.save_settings)
        grid_sync.addWidget(self.combo_software, 0, 1)
        cl_sync.addLayout(grid_sync)

        # Companion status row (visible only when "CSP 智能手机" selected)
        self.row_companion_widget = QWidget()
        row_comp = QHBoxLayout(self.row_companion_widget)
        row_comp.setContentsMargins(0, 0, 0, 0); row_comp.setSpacing(6)
        self.lbl_companion_status = QLabel("未连接")
        self.btn_companion_reconnect = QPushButton("重新连接")
        self.btn_companion_reconnect.clicked.connect(self._on_companion_reconnect)
        self.btn_companion_disconnect = QPushButton("断开")
        self.btn_companion_disconnect.clicked.connect(self._on_companion_disconnect)
        row_comp.addWidget(self.lbl_companion_status)
        row_comp.addStretch()
        row_comp.addWidget(self.btn_companion_reconnect)
        row_comp.addWidget(self.btn_companion_disconnect)
        cl_sync.addWidget(self.row_companion_widget)

        # CSP Version Container
        self.row_csp_widget = QWidget()
        row_csp_layout = QHBoxLayout(self.row_csp_widget)
        row_csp_layout.setContentsMargins(0, 0, 0, 0)
        row_csp_layout.addWidget(QLabel("CSP 版本"))
        self.combo_csp = NonScrollComboBox()
        self.combo_csp.addItems(["auto", "csp4.x", "csp5.x"])
        self.combo_csp.setToolTip("自动检测失败时才需要手动指定 CSP 主版本")
        self.combo_csp.currentTextChanged.connect(self.save_settings)
        row_csp_layout.addWidget(self.combo_csp)
        cl_sync.addWidget(self.row_csp_widget)

        # SAI2 Version Container
        self.row_sai_widget = QWidget()
        row_sai_layout = QHBoxLayout(self.row_sai_widget)
        row_sai_layout.setContentsMargins(0, 0, 0, 0)
        row_sai_layout.addWidget(QLabel("SAI2 版本"))
        self.combo_sai = NonScrollComboBox()
        self.combo_sai.addItems(["auto", "pre-2024-sai2", "after-2024-sai2"])
        self.combo_sai.setToolTip("2024 年后的 SAI2 版本地址偏移不同，自动检测失败时可手动指定")
        self.combo_sai.currentTextChanged.connect(self.save_settings)
        row_sai_layout.addWidget(self.combo_sai)
        cl_sync.addWidget(self.row_sai_widget)

        # UDM Version Container
        self.row_udm_widget = QWidget()
        row_udm_layout = QHBoxLayout(self.row_udm_widget)
        row_udm_layout.setContentsMargins(0, 0, 0, 0)
        row_udm_layout.addWidget(QLabel("UDM 版本"))
        self.combo_udm = NonScrollComboBox()
        self.combo_udm.addItems(["auto", "udm4.0pro", "udm4.0ex"])
        self.combo_udm.currentTextChanged.connect(self.save_settings)
        row_udm_layout.addWidget(self.combo_udm)
        cl_sync.addWidget(self.row_udm_widget)

        # Photoshop version container
        self.row_ps_widget = QWidget()
        row_ps_layout = QHBoxLayout(self.row_ps_widget)
        row_ps_layout.setContentsMargins(0, 0, 0, 0)
        row_ps_layout.addWidget(QLabel("PS 版本"))
        self.combo_ps = NonScrollComboBox()
        self.combo_ps.addItems(["auto"])
        self.combo_ps.currentTextChanged.connect(self.save_settings)
        row_ps_layout.addWidget(self.combo_ps)
        cl_sync.addWidget(self.row_ps_widget)

        page_software.addWidget(card_sync)

        # ═══════════════════ Page 5: 关于 ═══════════════════
        card_about, cl_about = self._begin_card(page_about, "关于")

        row_version = QHBoxLayout()
        row_version.addWidget(QLabel("当前版本"))
        self.lbl_version_value = QLabel(f"v{updater.APP_VERSION}")
        self.lbl_version_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_version.addStretch()
        row_version.addWidget(self.lbl_version_value)
        cl_about.addLayout(row_version)

        row_about_actions = QHBoxLayout()
        row_about_actions.setSpacing(6)
        self.btn_check_update = QPushButton("检查更新")
        self.btn_check_update.clicked.connect(self.on_check_update)
        self.btn_about_author = QPushButton("关于作者")
        self.btn_about_author.clicked.connect(self.on_about_author)
        row_about_actions.addWidget(self.btn_check_update)
        row_about_actions.addWidget(self.btn_about_author)
        row_about_actions.addStretch()
        cl_about.addLayout(row_about_actions)

        cl_about.addStretch()
        page_about.addWidget(card_about)

        card_config, cl_config = self._begin_card(page_about, "配置管理")
        row_config_actions = QHBoxLayout()
        row_config_actions.setSpacing(6)
        self.btn_export_config = QPushButton("导出配置")
        self.btn_export_config.setToolTip("把当前设置保存为 JSON 文件")
        self.btn_export_config.clicked.connect(self.export_config)
        self.btn_import_config = QPushButton("导入配置")
        self.btn_import_config.setToolTip("从 JSON 文件恢复设置")
        self.btn_import_config.clicked.connect(self.import_config)
        self.btn_reset_config = QPushButton("恢复默认")
        self.btn_reset_config.setToolTip("恢复全部设置为出厂默认值")
        self.btn_reset_config.clicked.connect(self.reset_config)
        row_config_actions.addWidget(self.btn_export_config)
        row_config_actions.addWidget(self.btn_import_config)
        row_config_actions.addWidget(self.btn_reset_config)
        cl_config.addLayout(row_config_actions)
        page_about.addWidget(card_config)

        # Keep section cards at their natural size, top-aligned: one trailing
        # stretch absorbs the fixed window's leftover height on short pages.
        for page_layout in self._page_layouts.values():
            page_layout.addStretch(1)
        
    @staticmethod
    def _make_step_button(text, tooltip="", width=22):
        """Compact step/arrow button (-, +, ▲, ▼) with a uniform 22px hit area."""
        btn = QPushButton(text)
        btn.setObjectName("StepButton")
        btn.setFixedSize(width, 20)
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    @staticmethod
    def _set_label_state(lbl, state):
        """Theme-aware status coloring via objectName, repolished immediately.

        ``state`` is one of: None (default text color), "muted", "success",
        "warning", "danger" — the colors are defined in ``apply_theme``.
        """
        if state:
            lbl.setObjectName(f"Status{state.capitalize()}")
        else:
            lbl.setObjectName("")
        style = lbl.style()
        style.unpolish(lbl)
        style.polish(lbl)

    def _make_page(self, title):
        """Create a tab page with a QScrollArea and return its QVBoxLayout."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setSpacing(6)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(page)
        self.stack.addWidget(scroll)

        self._page_layouts[title] = page_layout
        return page_layout

    def _begin_card(self, page_layout, header_text):
        """Create a flat settings section with header, return (card, content_layout)."""
        card = QFrame()
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 1, 0, 1)
        card_layout.setSpacing(6)
        card_layout.addWidget(self.create_header(header_text))
        return card, card_layout

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

    # ── Rail icons ────────────────────────────────────────────────────────

    @staticmethod
    def _nav_icon(kind: str, color: str) -> QIcon:
        """Draw a crisp monochrome rail glyph with QPainter.

        ``kind`` is one of: hotkeys, interface, picker, software, about.
        The canvas is 36×36 logical units on a 72×72 device pixmap
        (devicePixelRatio 2.0), so the painter coordinates below match
        the drawing code exactly while the glyph stays sharp on HiDPI.
        """
        pm = QPixmap(72, 72)
        pm.setDevicePixelRatio(2.0)  # logical 36×36 canvas → painter coords = drawing units
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color)
        pen = QPen(c)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = 18.0, 18.0

        if kind == "hotkeys":
            # Keyboard
            p.drawRoundedRect(4, 8, 28, 16, 3, 3)
            p.setBrush(c)
            for row_y in (12.0, 19.0):
                for x in (8.0, 12.0, 16.0, 20.0, 24.0):
                    p.drawEllipse(QPointF(x, row_y), 1.2, 1.2)
        elif kind == "interface":
            # Monitor
            p.drawRoundedRect(4, 5, 28, 17, 2, 2)
            p.drawLine(QPointF(18, 22), QPointF(18, 27))
            p.drawLine(QPointF(11, 30), QPointF(25, 30))
        elif kind == "picker":
            # Color wheel (ring + inner ring + hue dot)
            p.drawEllipse(QPointF(cx, cy), 16, 16)
            p.drawEllipse(QPointF(cx, cy), 6, 6)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx - 11, cy), 2.1, 2.1)
        elif kind == "software":
            # Circular sync arrows
            r = 11.0
            p.drawArc(int(cx - r), int(cy - r), int(2 * r), int(2 * r), 225 * 16, -200 * 16)
            p.drawArc(int(cx - r), int(cy - r), int(2 * r), int(2 * r), 45 * 16, 200 * 16)
            p.setBrush(c)
            for start, sweep in ((225, -200), (45, 200)):
                end = math.radians(start + sweep)
                ex, ey = cx + r * math.cos(end), cy + r * math.sin(end)
                t = math.radians(start + sweep - 90)  # travel direction
                dx, dy = math.cos(t), math.sin(t)
                tip = QPointF(ex + dx * 6.0, ey + dy * 6.0)
                b1 = QPointF(ex - dy * 3.0, ey + dx * 3.0)
                b2 = QPointF(ex + dy * 3.0, ey - dx * 3.0)
                p.drawPolygon(QPolygonF([tip, b1, b2]))
        elif kind == "about":
            # Info circle
            p.drawEllipse(QPointF(cx, cy), 15, 15)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy - 7), 1.8, 1.8)
            p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 9))
        p.end()
        return QIcon(pm)

    def _refresh_nav_icons(self):
        """Re-render rail glyphs in the current theme (white when selected)."""
        if not hasattr(self, "nav"):
            return
        colors = self.theme_colors()
        text = colors["text"]
        selected = self.nav.currentRow()
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item is None:
                continue
            kind = self._nav_icons.get(item.text(), "about")
            item.setIcon(self._nav_icon(kind, "#ffffff" if i == selected else text))

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
        self._set_label_state(lbl, "muted")
        row.addWidget(lbl)

        btn_set = QPushButton("设定")
        btn_set.setToolTip(tooltip + " — 点击后窗口隐藏3秒，移鼠标到目标位置")
        btn_set.clicked.connect(lambda: self.start_eyedropper_pick(target))
        btn_sync = QPushButton("同步")
        btn_sync.setToolTip("从已设定的取色点立即同步颜色")
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
        """按渲染后端重建黑白模式选项：只显示该后端支持的模式（Rust 仅 OKLCh；Mag 仅 Luma）"""
        self.combo_grayscale_mode.blockSignals(True)
        self.combo_grayscale_mode.clear()
        if backend == "rust":
            self.combo_grayscale_mode.addItems(["OKLCh (感知均匀)"])
            self.combo_grayscale_mode.setEnabled(False)
        elif backend == "mag":
            self.combo_grayscale_mode.addItems(["Luma (BT.709 标准)"])
            self.combo_grayscale_mode.setEnabled(False)
        else:
            self.combo_grayscale_mode.addItems(["OKLCh (感知均匀)", "Luma (BT.709 标准)"])
            self.combo_grayscale_mode.setEnabled(True)
        self.combo_grayscale_mode.blockSignals(False)

    def _on_grayscale_backend_changed(self, text):
        """切换渲染后端时重建黑白模式选项，再保存设置"""
        if "D3D11" in text:
            backend = "rust"
        elif "DComp" in text:
            backend = "dwm"
        elif "Mag" in text:
            backend = "mag"
        else:
            backend = "overlay"
        self._update_grayscale_mode_options(backend)
        self.save_settings()

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
        backend = self.cfg.get("grayscaleFilterBackend", "overlay")
        # 只显示后端支持的模式：Rust 仅 OKLCh；Mag 仅 Luma
        self._update_grayscale_mode_options(backend)
        if backend not in ("rust", "mag"):
            self.combo_grayscale_mode.setCurrentIndex(1 if mode == "luma" else 0)
        self.combo_grayscale_mode.blockSignals(False)

        self.combo_grayscale_backend.blockSignals(True)
        if backend == "rust":
            self.combo_grayscale_backend.setCurrentIndex(2)
        elif backend == "dwm":
            self.combo_grayscale_backend.setCurrentIndex(1)
        elif backend == "mag":
            self.combo_grayscale_backend.setCurrentIndex(3)
        else:
            self.combo_grayscale_backend.setCurrentIndex(0)
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
                    self._set_label_state(lbl, None)
                else:
                    lbl.setText("未设定")
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
        software_map = {"csp": "CLIP Studio Paint", "sai": "SAI2", "udm": "UDM Paint", "ps": "Photoshop", "companion": "CSP 智能手机 (R)"}
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
        self._refresh_module_sliders()
    def update_version_visibility(self):
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm", "Photoshop": "ps", "CSP 智能手机 (R)": "companion"}
        selected = software_val_map.get(self.combo_software.currentText(), "csp")
        self.row_csp_widget.setVisible(selected == "csp")
        self.row_sai_widget.setVisible(selected == "sai")
        self.row_udm_widget.setVisible(selected == "udm")
        self.row_ps_widget.setVisible(selected == "ps")
        self.row_companion_widget.setVisible(selected == "companion")
        if selected == "companion":
            self._refresh_companion_status()
        self._refresh_sync_status()

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

    def save_hotkeys(self, new_val=None):
        self.cfg["pickKey"] = self.btn_pick.val
        self.cfg["hideWindowKey"] = self.btn_hide.val
        self.cfg["followMouseKey"] = self.btn_follow.val
        self.cfg["grayscaleFilterKey"] = self.btn_grayscale.val
        self._persist_and_emit()

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
        viz_val_map = {"LAB 色彩空间": "lab", "OKLab 色彩空间": "oklab"}
        self.cfg["visualizerMode"] = viz_val_map.get(self.combo_viz_mode.currentText(), "lab")
        self.cfg["showLabLightnessSlider"] = self.cb_show_lab_lightness.isChecked()

        # ── Ringless settings ──
        rcfg = self.ringless_settings.config()
        self.cfg["hideHueRing"] = rcfg.enabled
        self.cfg["ringlessControlsSide"] = rcfg.controls_side
        self.cfg["ringlessControlBarPosition"] = rcfg.control_bar_position
        
        software_val_map = {"CLIP Studio Paint": "csp", "SAI2": "sai", "UDM Paint": "udm", "Photoshop": "ps", "CSP 智能手机 (R)": "companion"}
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
        # Grayscale filter mode (组合框已按后端只显示支持的模式；此处再兜底强制)
        backend_text = self.combo_grayscale_backend.currentText()
        if "D3D11" in backend_text:
            self.cfg["grayscaleFilterMode"] = "oklch"
        elif "Mag" in backend_text:
            self.cfg["grayscaleFilterMode"] = "luma"
        else:
            mode_text = self.combo_grayscale_mode.currentText()
            self.cfg["grayscaleFilterMode"] = "luma" if "Luma" in mode_text else "oklch"
        # Grayscale filter backend
        backend_text = self.combo_grayscale_backend.currentText()
        if "D3D11" in backend_text:
            self.cfg["grayscaleFilterBackend"] = "rust"
        elif "DComp" in backend_text:
            self.cfg["grayscaleFilterBackend"] = "dwm"
        elif "Mag" in backend_text:
            self.cfg["grayscaleFilterBackend"] = "mag"
        else:
            self.cfg["grayscaleFilterBackend"] = "overlay"

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
                    lbl.setText("未设定")
                    self._set_label_state(lbl, "danger")
        self._refresh_theme_status()
        self.apply_theme()
        self._refresh_module_sliders()

    def _refresh_module_sliders(self):
        """Show only the slider rows that belong to the currently active module.
        Re-reads config from disk so changes outside the sidebar are picked up."""
        self.cfg = config.load_hotkey_config()
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

    def _refresh_theme_status(self):
        if not hasattr(self, "lbl_theme_status"):
            return
        theme = self.cfg.get("ui-theme", "auto")
        if theme == "auto":
            try:
                dark = QColor(self.theme_colors()["bg"]).lightness() < 128
                self.lbl_theme_status.setText(f"自动匹配：{'深色' if dark else '浅色'}主题")
            except Exception:
                self.lbl_theme_status.setText("自动匹配画图软件主题")
        elif theme == "eyedropper":
            self.lbl_theme_status.setText("取色主题：从屏幕两个位置取色")
        else:
            names = {"black": "黑", "white": "白", "gray": "灰"}
            self.lbl_theme_status.setText(f"固定主题：{names.get(theme, theme)}")

    def _refresh_sync_status(self):
        if not hasattr(self, "lbl_sync_status"):
            return
        selected = self.combo_software.currentText()
        software_names = {
            "CLIP Studio Paint": "CSP",
            "SAI2": "SAI2",
            "UDM Paint": "UDM",
            "Photoshop": "PS",
            "CSP 智能手机 (R)": "手机",
        }
        name = software_names.get(selected, selected)
        connected = None
        if self._parent is not None:
            status = getattr(self._parent, "_sync_status", None)
            if status and len(status) == 2 and status[0] == self.cfg.get("syncSoftware"):
                connected = status[1]
        if connected is True:
            self.lbl_sync_status.setText(f"{name} 已连接")
            self._set_label_state(self.lbl_sync_status, "success")
        elif connected is False:
            text = f"{name} 未连接"
            parent = self._parent
            sync_err = getattr(parent, "_sync_error", None) if parent is not None else None
            if sync_err and len(sync_err) >= 2 and sync_err[0] == self.cfg.get("syncSoftware"):
                err = sync_err[1]
                if err:
                    text += f" — {err}"
                    # Keep the label compact; full reason lives in the tooltip.
                    if len(text) > 90:
                        text = text[:90] + "…"
            self.lbl_sync_status.setText(text)
            self.lbl_sync_status.setToolTip(text)
            self._set_label_state(self.lbl_sync_status, "danger")
        else:
            mode = self.cfg.get("syncSoftware", "csp")
            version = {
                "csp": self.combo_csp.currentText(),
                "sai": self.combo_sai.currentText(),
                "udm": self.combo_udm.currentText(),
                "ps": self.combo_ps.currentText(),
                "companion": "",
            }.get(mode, "")
            self.lbl_sync_status.setText(f"当前同步：{name} {version}".strip())
            self._set_label_state(self.lbl_sync_status, "muted")

    def export_config(self):
        default_name = os.path.join(os.path.expanduser("~"), "Colorink-配置.json")
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", default_name, "JSON 文件 (*.json)")
        if not path:
            return
        cfg_path = os.path.join(config.get_user_data_dir(), config.HOTKEY_CFG_NAME)
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            QMessageBox.information(self, "导出配置", f"配置已导出到：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出配置", f"导出失败：{e}")

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", os.path.expanduser("~"), "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("配置文件格式不正确")
        except Exception as e:
            QMessageBox.warning(self, "导入配置", f"读取失败：{e}")
            return
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = loaded.get("openAtLogin", False)
        self.cfg = config.normalize_slider_orders(dict(loaded))
        config.save_hotkey_config(self.cfg)
        if old_autostart != new_autostart:
            autostart.apply_autostart(new_autostart)
        self.refresh_ui()
        self.settingChanged.emit()
        QMessageBox.information(self, "导入配置", "配置已导入并生效。")

    def reset_config(self):
        answer = QMessageBox.question(
            self,
            "恢复默认",
            "确定要恢复所有设置为默认值吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        old_autostart = self.cfg.get("openAtLogin", False)
        self.cfg = config.default_hotkey_config()
        config.save_hotkey_config(self.cfg)
        if old_autostart:
            autostart.apply_autostart(False)
        self.refresh_ui()
        self.settingChanged.emit()
        QMessageBox.information(self, "恢复默认", "设置已恢复为默认值。")

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

    # ── Eyedropper dual-point pick ────────────────────────────────────────
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
            btn_set.setText("设定")
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
            self._persist_and_emit()
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


    # ── Companion helpers ──────────────────────────────────────────────
    def _refresh_companion_status(self):
        if not hasattr(self, 'parent') or self._parent is None: return
        if not hasattr(self._parent, 'sync_thread'): return
        c = self._parent.sync_thread.companion_sync
        connected = getattr(c, '_connected', False)
        if connected:
            self.lbl_companion_status.setText("● 已连接")
            self._set_label_state(self.lbl_companion_status, "success")
            self.btn_companion_reconnect.setVisible(False)
            self.btn_companion_disconnect.setVisible(True)
        elif c._has_session():
            self.lbl_companion_status.setText("○ 已保存 — 等待 CSP...")
            self._set_label_state(self.lbl_companion_status, "warning")
            self.btn_companion_reconnect.setVisible(True)
            self.btn_companion_disconnect.setVisible(False)
        else:
            self.lbl_companion_status.setText("○ 未设置")
            self._set_label_state(self.lbl_companion_status, "muted")
            self.btn_companion_reconnect.setText("连接智能手机")
            self.btn_companion_reconnect.setVisible(True)
            self.btn_companion_disconnect.setVisible(False)

    def _on_companion_reconnect(self):
        if hasattr(self, 'parent') and self._parent is not None:
            self._parent._setup_companion_connection()
            self._refresh_companion_status()

    def _on_companion_disconnect(self):
        if hasattr(self, 'parent') and self._parent is not None:
            if hasattr(self._parent, 'sync_thread'):
                self._parent.sync_thread.companion_sync._disconnect()
            self._refresh_companion_status()

class _UpdateWorker(QThread):
    """Background worker that queries GitHub for the latest release."""

    done = pyqtSignal(dict)

    def run(self):  # noqa: D401 - QThread override
        self.done.emit(updater.check_for_update())
