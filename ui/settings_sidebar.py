import json
import math
import os
import sys
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

from core import autostart, config, i18n, updater
from ui.hotkey_button import HotkeyButton, display_hotkey
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

# CSP 内存模式版本选项：显示文本 ↔ 配置存储值。前景/背景色与透明状态
# 同步（rgb_u32 槽布局）只有 csp5.1 支持；csp4.x / csp5.x 仅主色同步。
_CSP_VERSION_ITEMS: list[tuple[str, str]] = [
    ("auto", "auto（自动检测）"),
    ("csp4.x", "CSP 4.x（仅主色）"),
    ("csp5.x", "CSP 5.0（仅主色）"),
    ("csp5.1", "CSP 5.1（支持前景/背景/透明）"),
]
_CSP_DISPLAY_TO_VALUE = {disp: val for val, disp in _CSP_VERSION_ITEMS}
_CSP_VALUE_TO_DISPLAY = dict(_CSP_VERSION_ITEMS)
# 每项的悬停说明
_CSP_VERSION_TIPS: dict[str, str] = {
    "auto": "自动检测 CSP 主版本；检测为 5.1 时支持前景/背景色与透明同步，"
            "5.0 及以下仅主色同步。",
    "csp4.x": "CSP 4.x 内存模式仅支持主色同步；前景/背景色与透明同步需要 CSP 5.1。",
    "csp5.x": "CSP 5.0 内存模式仅支持主色同步；前景/背景色与透明同步需要 CSP 5.1。",
    "csp5.1": "CSP 5.1 内存模式支持前景/背景色与透明状态同步（推荐）。",
}

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

    @staticmethod
    def _clear_layout(layout):
        """Recursively detach and schedule deletion of a layout's contents."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    SettingsSidebar._clear_layout(sub)

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
            ("软件", "software"),
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

        # Create all 5 pages
        page_hotkeys   = self._make_page("快捷键")
        page_interface = self._make_page("界面")
        page_picker    = self._make_page("取色器")
        page_software  = self._make_page("软件")
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

        grid_hotkeys.addWidget(QLabel(i18n.tr("隐藏界面")), 1, 0)
        self.btn_hide = HotkeyButton("hideWindowKey", self.cfg.get("hideWindowKey", "Ctrl+H"), allow_mouse=True)
        self.btn_hide.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_hide, 1, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("随鼠标移动")), 2, 0)
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

        grid_hotkeys.addWidget(QLabel(i18n.tr("黑白滤镜")), 3, 0)
        self.btn_grayscale = HotkeyButton("grayscaleFilterKey", self.cfg.get("grayscaleFilterKey", "Ctrl+G"), allow_mouse=True)
        self.btn_grayscale.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_grayscale, 3, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB切换(色轮)")), 4, 0)
        self.btn_lab_toggle = HotkeyButton("toggleLabKey", self.cfg.get("toggleLabKey", "Space"), allow_mouse=True)
        self.btn_lab_toggle.setToolTip(i18n.tr("鼠标悬停在色轮或LAB区域时，按此键/鼠标键切换色轮/LAB视图；支持键盘、鼠标按键或数位板笔按键（建议侧键/中键，左键会与色轮操作冲突）；无需聚焦本窗口，无焦点选色模式下也可用"))
        self.btn_lab_toggle.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_lab_toggle, 4, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("LAB切换(全局)")), 5, 0)
        self.btn_lab_global = HotkeyButton("toggleLabGlobalKey", self.cfg.get("toggleLabGlobalKey", "Ctrl+L"), allow_mouse=True)
        self.btn_lab_global.setToolTip(i18n.tr("任意位置全局切换色轮/LAB视图，无需聚焦本窗口；支持键盘或鼠标按键（鼠标按键作为全局快捷键时不拦截点击，画画软件仍会收到）"))
        self.btn_lab_global.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_lab_global, 5, 1)

        grid_hotkeys.addWidget(QLabel(i18n.tr("标题栏显隐")), 6, 0)
        self.btn_title_bar = HotkeyButton("toggleTitleBarKey", self.cfg.get("toggleTitleBarKey", "Ctrl+Shift+T"), allow_mouse=True)
        self.btn_title_bar.setToolTip(i18n.tr("显示或隐藏标题栏（设置/最小化/关闭按钮那一栏）；隐藏后顶部边框与四周一致"))
        self.btn_title_bar.hotkeyChanged.connect(self.save_hotkeys)
        grid_hotkeys.addWidget(self.btn_title_bar, 6, 1)

        cl_hk.addLayout(grid_hotkeys)
        page_hotkeys.addWidget(card_hk)

        # ═══════════════════ Page 2: 界面 ═══════════════════
        card_appear, cl_appear = self._begin_card(page_interface, i18n.tr("外观"))
        self._card_layout_interface_bg = cl_appear  # stored for _make_eyedropper_row

        grid_appear = QGridLayout()
        grid_appear.setSpacing(6)
        grid_appear.setColumnMinimumWidth(0, 84)
        grid_appear.setColumnStretch(1, 1)

        # Background theme
        grid_appear.addWidget(QLabel(i18n.tr("背景主题")), 0, 0)
        self.combo_theme = NonScrollComboBox()
        self.combo_theme.addItem(i18n.tr("背景 自动（匹配CSP）"), "auto")
        self.combo_theme.addItem(i18n.tr("背景 取色"), "eyedropper")
        self.combo_theme.addItem(i18n.tr("背景 灰"), "gray")
        self.combo_theme.addItem(i18n.tr("背景 白"), "white")
        self.combo_theme.addItem(i18n.tr("背景 黑"), "black")
        self.combo_theme.currentTextChanged.connect(self.save_settings)
        grid_appear.addWidget(self.combo_theme, 0, 1)

        # Slider visual theme
        grid_appear.addWidget(QLabel(i18n.tr("滑条样式")), 1, 0)
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
        self._make_eyedropper_row("bar", i18n.tr("框色"), i18n.tr("绘画软件标题栏/边框的深色"))
        self._make_eyedropper_row("bg",  i18n.tr("底色"), i18n.tr("绘画软件画布区域的浅色"))

        page_interface.addWidget(card_appear)

        card_gray, cl_gray = self._begin_card(page_interface, i18n.tr("灰度滤镜"))

        grid_gray = QGridLayout()
        grid_gray.setSpacing(6)
        grid_gray.setColumnMinimumWidth(0, 84)
        grid_gray.setColumnStretch(1, 1)

        grid_gray.addWidget(QLabel(i18n.tr("滤镜目标屏幕")), 0, 0)
        self.combo_grayscale_screen = NonScrollComboBox()
        self.combo_grayscale_screen.setToolTip(i18n.tr("选择黑白滤镜作用在哪个屏幕，默认作用于全部屏幕"))
        self.combo_grayscale_screen.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_screen, 0, 1)

        grid_gray.addWidget(QLabel(i18n.tr("黑白模式")), 1, 0)
        self.combo_grayscale_mode = NonScrollComboBox()
        self.combo_grayscale_mode.addItem(i18n.tr("OKLCh (感知均匀)"), "oklch")
        self.combo_grayscale_mode.addItem(i18n.tr("Luma (BT.709 标准)"), "luma")
        self.combo_grayscale_mode.setToolTip(i18n.tr("OKLCh 更接近人眼感知；Luma 是标准亮度转换"))
        self.combo_grayscale_mode.currentTextChanged.connect(self.save_settings)
        grid_gray.addWidget(self.combo_grayscale_mode, 1, 1)

        grid_gray.addWidget(QLabel(i18n.tr("渲染后端 (高级)")), 2, 0)
        self.combo_grayscale_backend = NonScrollComboBox()
        self.combo_grayscale_backend.addItem(i18n.tr("OKLCh (GPU兼容)"), "native")
        self.combo_grayscale_backend.addItem(i18n.tr("系统 Luma (Mag)"), "mag")
        self.combo_grayscale_backend.setToolTip(
            i18n.tr("OKLCh (GPU兼容)：感知均匀的全屏黑白，覆盖 ColorInk；"
            "系统 Luma (Mag)：延迟最低、仅作用于全部屏幕的备用模式；"
            "需要按屏目标时请在 Native 后端选择 Luma。"))
        self.combo_grayscale_backend.currentTextChanged.connect(self._on_grayscale_backend_changed)
        grid_gray.addWidget(self.combo_grayscale_backend, 2, 1)

        cl_gray.addLayout(grid_gray)
        page_interface.addWidget(card_gray)

        card_behavior, cl_behavior = self._begin_card(page_interface, i18n.tr("行为"))

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

        self.cb_only_drawing = QCheckBox(i18n.tr("仅在画图软件前台时显示"))
        self.cb_only_drawing.setToolTip(i18n.tr("画图软件不在前台时自动隐藏悬浮面板"))
        self.cb_only_drawing.stateChanged.connect(self.save_settings)
        grid_behavior.addWidget(self.cb_only_drawing, 1, 1)

        self.cb_no_focus = QCheckBox(i18n.tr("无焦点选色模式"))
        self.cb_no_focus.setToolTip(i18n.tr("开启后不会抢占画图软件的键盘焦点，适合边画边选色"))
        self.cb_no_focus.clicked.connect(self.on_no_focus_clicked)
        grid_behavior.addWidget(self.cb_no_focus, 2, 1)

        cl_behavior.addLayout(grid_behavior)
        page_interface.addWidget(card_behavior)

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

        grid_pz.addWidget(QLabel(i18n.tr("前背景色位置")), 1, 0)
        self.combo_pos = NonScrollComboBox()
        self.combo_pos.addItem(i18n.tr("左上角"), "top-left")
        self.combo_pos.addItem(i18n.tr("左下角"), "bottom-left")
        self.combo_pos.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_pos, 1, 1)

        grid_pz.addWidget(QLabel(i18n.tr("色彩空间模块")), 2, 0)
        self.combo_module = NonScrollComboBox()
        self.combo_module.addItems(["HSV", "HLS", "RGB", "LCH"])
        self.combo_module.currentTextChanged.connect(self.save_settings)
        grid_pz.addWidget(self.combo_module, 2, 1)

        cl_pz.addLayout(grid_pz)

        self.cb_show_module_btn = QCheckBox(i18n.tr("显示模块切换按钮"))
        self.cb_show_module_btn.setToolTip(i18n.tr("在色环区域显示色彩空间模块切换按钮"))
        self.cb_show_module_btn.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_module_btn)

        self.cb_show_lab_toggle = QCheckBox(i18n.tr("显示LAB切换按钮"))
        self.cb_show_lab_toggle.setToolTip(i18n.tr("在色轮/LAB区域显示色轮与LAB之间的切换按钮"))
        self.cb_show_lab_toggle.stateChanged.connect(self.save_settings)
        cl_pz.addWidget(self.cb_show_lab_toggle)

        page_picker.addWidget(card_pz)

        card_sl_order, cl_sl_order = self._begin_card(page_picker, i18n.tr("滑块显示与顺序"))

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

        grid_wheel.addWidget(QLabel(i18n.tr("LAB图模式")), 0, 0)
        self.combo_viz_mode = NonScrollComboBox()
        self.combo_viz_mode.addItem(i18n.tr("LAB 色彩空间"), "lab")
        self.combo_viz_mode.addItem(i18n.tr("OKLab 色彩空间"), "oklab")
        self.combo_viz_mode.currentTextChanged.connect(self.save_settings)
        grid_wheel.addWidget(self.combo_viz_mode, 0, 1)

        cl_wheel.addLayout(grid_wheel)

        self.cb_show_lab_lightness = QCheckBox(i18n.tr("显示 LAB 亮度滑条"))
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
        grid_sp.addWidget(QLabel(i18n.tr("滚轮单次步长")), 0, 0)
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
        grid_sp.addWidget(QLabel(i18n.tr("同空间滑条间距")), 1, 0)
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
        grid_sp.addWidget(QLabel(i18n.tr("不同空间间距")), 2, 0)
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
        card_sync, cl_sync = self._begin_card(page_software, i18n.tr("同步与版本"))

        row_sync_status = QHBoxLayout()
        row_sync_status.setSpacing(6)
        self.lbl_sync_status = QLabel("")
        self.lbl_sync_status.setObjectName("StatusHint")
        row_sync_status.addWidget(self.lbl_sync_status, 1)
        self.btn_copy_diagnostics = QPushButton(i18n.tr("复制诊断信息"))
        self.btn_copy_diagnostics.setToolTip(
            i18n.tr("把版本、同步状态与最近日志复制到剪贴板，"
            "用于排查同步 / 崩溃问题"))
        self.btn_copy_diagnostics.clicked.connect(self._on_copy_diagnostics)
        row_sync_status.addWidget(self.btn_copy_diagnostics, 0)
        cl_sync.addLayout(row_sync_status)

        grid_sync = QGridLayout()
        grid_sync.setSpacing(6)
        grid_sync.setColumnMinimumWidth(0, 84)
        grid_sync.setColumnStretch(1, 1)
        grid_sync.addWidget(QLabel(i18n.tr("同步软件")), 0, 0)
        self.combo_software = NonScrollComboBox()
        for _val, _disp in [("csp", "CLIP Studio Paint"), ("sai", "SAI2"),
                            ("udm", "UDM Paint"), ("ps", "Photoshop"),
                            ("companion", "CSP 智能手机 (R)")]:
            self.combo_software.addItem(i18n.tr(_disp), _val)
        self.combo_software.currentTextChanged.connect(self.save_settings)
        self.combo_software.currentTextChanged.connect(self._on_software_changed)
        grid_sync.addWidget(self.combo_software, 0, 1)
        cl_sync.addLayout(grid_sync)

        # Companion status row (visible only when "CSP 智能手机" selected)
        self.row_companion_widget = QWidget()
        row_comp = QHBoxLayout(self.row_companion_widget)
        row_comp.setContentsMargins(0, 0, 0, 0); row_comp.setSpacing(6)
        self.lbl_companion_status = QLabel(i18n.tr("未连接"))
        self.btn_companion_reconnect = QPushButton(i18n.tr("重新连接"))
        self.btn_companion_reconnect.clicked.connect(self._on_companion_reconnect)
        self.btn_companion_disconnect = QPushButton(i18n.tr("断开"))
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
        row_csp_layout.addWidget(QLabel(i18n.tr("CSP 版本")))
        self.combo_csp = NonScrollComboBox()
        for _val, _disp in _CSP_VERSION_ITEMS:
            self.combo_csp.addItem(i18n.tr(_disp), _val)
        for i, (val, _disp) in enumerate(_CSP_VERSION_ITEMS):
            self.combo_csp.setItemData(
                i, i18n.tr(_CSP_VERSION_TIPS.get(val, "")), Qt.ItemDataRole.ToolTipRole
            )
        self.combo_csp.setToolTip(
            i18n.tr("前景/背景色与透明状态同步（内存模式）仅 CSP 5.1 支持；"
            "自动检测失败时才需要手动指定版本")
        )
        self.combo_csp.currentTextChanged.connect(self._on_csp_version_changed)
        row_csp_layout.addWidget(self.combo_csp)
        cl_sync.addWidget(self.row_csp_widget)
        # 版本能力说明行：明确 5.0 与 5.1 的同步能力差异
        self.row_csp_hint_widget = QWidget()
        row_csp_hint = QVBoxLayout(self.row_csp_hint_widget)
        row_csp_hint.setContentsMargins(0, 0, 0, 0)
        row_csp_hint.setSpacing(4)
        self.lbl_csp_hint = QLabel("")
        self.lbl_csp_hint.setWordWrap(True)
        self.lbl_csp_hint.setObjectName("StatusHint")
        row_csp_hint.addWidget(self.lbl_csp_hint)
        cl_sync.addWidget(self.row_csp_hint_widget)

        # SAI2 Version Container
        self.row_sai_widget = QWidget()
        row_sai_layout = QHBoxLayout(self.row_sai_widget)
        row_sai_layout.setContentsMargins(0, 0, 0, 0)
        row_sai_layout.addWidget(QLabel(i18n.tr("SAI2 版本")))
        self.combo_sai = NonScrollComboBox()
        self.combo_sai.addItems(["auto", "pre-2024-sai2", "after-2024-sai2"])
        self.combo_sai.setToolTip(i18n.tr("2024 年后的 SAI2 版本地址偏移不同，自动检测失败时可手动指定"))
        self.combo_sai.currentTextChanged.connect(self.save_settings)
        row_sai_layout.addWidget(self.combo_sai)
        cl_sync.addWidget(self.row_sai_widget)

        # UDM Version Container
        self.row_udm_widget = QWidget()
        row_udm_layout = QHBoxLayout(self.row_udm_widget)
        row_udm_layout.setContentsMargins(0, 0, 0, 0)
        row_udm_layout.addWidget(QLabel(i18n.tr("UDM 版本")))
        self.combo_udm = NonScrollComboBox()
        self.combo_udm.addItems(["auto", "udm4.0pro", "udm4.0ex"])
        self.combo_udm.currentTextChanged.connect(self.save_settings)
        row_udm_layout.addWidget(self.combo_udm)
        cl_sync.addWidget(self.row_udm_widget)

        # Photoshop version container
        self.row_ps_widget = QWidget()
        row_ps_layout = QHBoxLayout(self.row_ps_widget)
        row_ps_layout.setContentsMargins(0, 0, 0, 0)
        row_ps_layout.addWidget(QLabel(i18n.tr("PS 版本")))
        self.combo_ps = NonScrollComboBox()
        self.combo_ps.addItems(["auto"])
        self.combo_ps.currentTextChanged.connect(self.save_settings)
        row_ps_layout.addWidget(self.combo_ps)
        cl_sync.addWidget(self.row_ps_widget)

        # Green/portable Photoshop script-bridge notice row (visible only
        # when a green edition is detected and PS sync is selected).
        self.row_ps_bridge_widget = QWidget()
        row_ps_bridge = QVBoxLayout(self.row_ps_bridge_widget)
        row_ps_bridge.setContentsMargins(0, 0, 0, 0)
        row_ps_bridge.setSpacing(4)
        self.lbl_ps_bridge_status = QLabel("")
        self.lbl_ps_bridge_status.setWordWrap(True)
        self.lbl_ps_bridge_status.setObjectName("StatusHint")
        row_ps_bridge.addWidget(self.lbl_ps_bridge_status)
        row_ps_bridge_btns = QHBoxLayout()
        row_ps_bridge_btns.setSpacing(6)
        self.btn_ps_bridge_recheck = QPushButton(i18n.tr("重新检测"))
        self.btn_ps_bridge_recheck.clicked.connect(self._on_ps_bridge_recheck)
        self.btn_ps_bridge_restart = QPushButton(i18n.tr("重启 Photoshop"))
        self.btn_ps_bridge_restart.clicked.connect(self._on_ps_restart)
        row_ps_bridge_btns.addWidget(self.btn_ps_bridge_recheck)
        row_ps_bridge_btns.addWidget(self.btn_ps_bridge_restart)
        row_ps_bridge_btns.addStretch()
        row_ps_bridge.addLayout(row_ps_bridge_btns)
        cl_sync.addWidget(self.row_ps_bridge_widget)
        self.row_ps_bridge_widget.hide()
        self._ps_bridge_prompted = False

        page_software.addWidget(card_sync)

        # ═══════════════════ Page 5: 关于 ═══════════════════
        card_about, cl_about = self._begin_card(page_about, i18n.tr("关于"))

        row_version = QHBoxLayout()
        row_version.addWidget(QLabel(i18n.tr("当前版本")))
        self.lbl_version_value = QLabel(f"v{updater.APP_VERSION}")
        self.lbl_version_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_version.addStretch()
        row_version.addWidget(self.lbl_version_value)
        cl_about.addLayout(row_version)

        row_lang = QHBoxLayout()
        row_lang.addWidget(QLabel("语言 / Language"))
        self.cmb_language = QComboBox()
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
        cl_about.addLayout(row_lang)

        row_about_actions = QHBoxLayout()
        row_about_actions.setSpacing(6)
        self.btn_check_update = QPushButton(i18n.tr("检查更新"))
        self.btn_check_update.clicked.connect(self.on_check_update)
        self.btn_about_author = QPushButton(i18n.tr("关于作者"))
        self.btn_about_author.clicked.connect(self.on_about_author)
        row_about_actions.addWidget(self.btn_check_update)
        row_about_actions.addWidget(self.btn_about_author)
        row_about_actions.addStretch()
        cl_about.addLayout(row_about_actions)

        self.cb_check_updates = QCheckBox(i18n.tr("启动时自动检查更新"))
        self.cb_check_updates.setChecked(self.cfg.get("checkUpdatesOnStartup", True))
        self.cb_check_updates.toggled.connect(self._on_check_updates_toggled)
        cl_about.addWidget(self.cb_check_updates)

        cl_about.addStretch()
        page_about.addWidget(card_about)

        card_config, cl_config = self._begin_card(page_about, i18n.tr("配置管理"))
        row_config_actions = QHBoxLayout()
        row_config_actions.setSpacing(6)
        self.btn_export_config = QPushButton(i18n.tr("导出配置"))
        self.btn_export_config.setToolTip(i18n.tr("把当前设置保存为 JSON 文件"))
        self.btn_export_config.clicked.connect(self.export_config)
        self.btn_import_config = QPushButton(i18n.tr("导入配置"))
        self.btn_import_config.setToolTip(i18n.tr("从 JSON 文件恢复设置"))
        self.btn_import_config.clicked.connect(self.import_config)
        self.btn_reset_config = QPushButton(i18n.tr("恢复默认"))
        self.btn_reset_config.setToolTip(i18n.tr("恢复全部设置为出厂默认值"))
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
            kind = item.data(Qt.ItemDataRole.UserRole) or "about"
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
                i18n.tr("选择黑白滤镜作用在哪个屏幕，默认作用于全部屏幕")
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
    def _on_csp_version_changed(self, _text: str) -> None:
        """CSP 版本选择变化：保存配置并刷新能力提示。"""
        self.save_settings()
        self._refresh_csp_version_hint()

    def _refresh_csp_version_hint(self):
        """按所选 CSP 版本显示同步能力说明（5.0 与 5.1 的能力差异）。"""
        if not hasattr(self, "lbl_csp_hint"):
            return
        val = self.combo_csp.currentData() or "auto"
        if val == "csp5.1":
            text = i18n.tr("CSP 5.1 内存模式支持前景/背景色与透明状态同步。")
        elif val == "csp5.x":
            text = i18n.tr("CSP 5.0 内存模式仅支持主色同步；前景/背景色与透明状态同步需要 CSP 5.1。")
        elif val == "csp4.x":
            text = i18n.tr("CSP 4.x 内存模式仅支持主色同步；前景/背景色与透明状态同步需要 CSP 5.1。")
        else:
            text = i18n.tr("自动检测 CSP 版本：检测为 5.1 时支持前景/背景色与透明状态同步，5.0 及以下仅主色同步。")
        self.lbl_csp_hint.setText(text)

    def update_version_visibility(self):
        selected = self.combo_software.currentData() or "csp"
        self.row_csp_widget.setVisible(selected == "csp")
        self.row_csp_hint_widget.setVisible(selected == "csp")
        self.row_sai_widget.setVisible(selected == "sai")
        self.row_udm_widget.setVisible(selected == "udm")
        self.row_ps_widget.setVisible(selected == "ps")
        self.row_companion_widget.setVisible(selected == "companion")
        if selected == "companion":
            self._refresh_companion_status()
        if selected == "csp":
            self._refresh_csp_version_hint()
        self._refresh_sync_status()
        self._refresh_ps_bridge_status()

    def _refresh_ps_instances(self):
        """Populate the PS 版本 combo with detected running instances:
        registered installs (COM) + green/portable editions (script bridge)."""
        try:
            from core.photoshop_instances import detect_instances
            instances = detect_instances()
        except Exception:
            instances = []
        labels = ["auto"] + [inst.label for inst in instances]
        current = self.combo_ps.currentText()
        self.combo_ps.blockSignals(True)
        self.combo_ps.clear()
        self.combo_ps.addItems(labels)
        self.combo_ps.setCurrentText(current if current in labels else "auto")
        self.combo_ps.blockSignals(False)

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
                self.lbl_theme_status.setText(i18n.tr("自动匹配画图软件主题"))
        elif theme == "eyedropper":
            self.lbl_theme_status.setText(i18n.tr("取色主题：从屏幕两个位置取色"))
        else:
            names = {"black": i18n.tr("黑"), "white": i18n.tr("白"), "gray": i18n.tr("灰")}
            self.lbl_theme_status.setText(i18n.tr("固定主题：{name}", name=names.get(theme, theme)))

    def _refresh_sync_status(self):
        if not hasattr(self, "lbl_sync_status"):
            return
        selected = self.combo_software.currentData() or "csp"
        software_names = {
            "csp": "CSP",
            "sai": "SAI2",
            "udm": "UDM",
            "ps": "PS",
            "companion": i18n.tr("手机"),
        }
        name = software_names.get(selected, selected)
        connected = None
        if self._parent is not None:
            status = getattr(self._parent, "_sync_status", None)
            if status and len(status) == 2 and status[0] == self.cfg.get("syncSoftware"):
                connected = status[1]
        if connected is True:
            self.lbl_sync_status.setText(i18n.tr("{name} 已连接", name=name))
            self._set_label_state(self.lbl_sync_status, "success")
        elif connected is False:
            text = i18n.tr("{name} 未连接", name=name)
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
                "csp": self.combo_csp.currentData() or "auto",
                "sai": self.combo_sai.currentText(),
                "udm": self.combo_udm.currentText(),
                "ps": self.combo_ps.currentText(),
                "companion": "",
            }.get(mode, "")
            self.lbl_sync_status.setText(i18n.tr("当前同步：{name} {version}", name=name, version=version).strip())
            self._set_label_state(self.lbl_sync_status, "muted")
        self._refresh_ps_bridge_status()

    def _on_copy_diagnostics(self):
        """Copy a diagnostics report to the clipboard (for bug reports)."""
        from core import diagnostics
        parent = self._parent
        report = diagnostics.collect_diagnostics(
            sync_thread=getattr(parent, "sync_thread", None) if parent is not None else None,
            cfg=self.cfg,
            mixin=parent,
        )
        QApplication.clipboard().setText(report)
        # Brief in-place confirmation, then restore the label.
        self.btn_copy_diagnostics.setText(i18n.tr("已复制 ✓"))
        QTimer.singleShot(1500, self._restore_copy_diagnostics_label)

    def _restore_copy_diagnostics_label(self):
        """Restore the button label; safe even if the sidebar was closed."""
        try:
            self.btn_copy_diagnostics.setText(i18n.tr("复制诊断信息"))
        except RuntimeError:
            pass  # widget already deleted (settings window closed)

    # -- Green/portable Photoshop script-bridge notice ----------------------

    def _ps_sync(self):
        """Best-effort access to the PhotoshopSync instance (or None)."""
        parent = self._parent
        st = getattr(parent, "sync_thread", None)
        return getattr(st, "ps_sync", None) if st is not None else None

    def _refresh_ps_bridge_status(self):
        """Show / hide the green-edition notice row with the current
        script-bridge state (deployed-pending / alive / deploy failed)."""
        if not hasattr(self, "row_ps_bridge_widget"):
            return
        if self.combo_software.currentData() != "ps":
            self.row_ps_bridge_widget.hide()
            return
        ps_sync = self._ps_sync()
        if ps_sync is None:
            self.row_ps_bridge_widget.hide()
            return
        try:
            # UI thread: use the non-connecting snapshot — status() can
            # block on a flaky COM registration attempt.
            st = ps_sync.status_lite()
        except Exception:
            self.row_ps_bridge_widget.hide()
            return
        if st.get("backend") != "script-bridge":
            self.row_ps_bridge_widget.hide()
            return
        self.row_ps_bridge_widget.show()
        if st.get("bridgeAlive"):
            if st.get("panelStale"):
                self.lbl_ps_bridge_status.setText(
                    i18n.tr("已连接（脚本桥），但 Photoshop 内运行的仍是旧版同步面板："
                    "拖动颜色可能跳动。请重启 Photoshop 一次后点击右侧按钮。"))
                self._set_label_state(self.lbl_ps_bridge_status, "warning")
                self.btn_ps_bridge_restart.show()
            else:
                self.lbl_ps_bridge_status.setText(
                    i18n.tr("绿色版 Photoshop 已连接（脚本桥）：前景 / 背景色双槽同步已启用。"))
                self._set_label_state(self.lbl_ps_bridge_status, "success")
                self.btn_ps_bridge_restart.hide()
        else:
            self.lbl_ps_bridge_status.setText(
                i18n.tr("检测到绿色版 Photoshop：已自动部署同步脚本，"
                "重启 Photoshop（绿色版）后生效；"
                "之后在 PS 中有操作时颜色即会同步。"))
            self._set_label_state(self.lbl_ps_bridge_status, "warning")
            self.btn_ps_bridge_restart.show()

    def _on_ps_bridge_recheck(self):
        """Force instance re-detection after the user restarted Photoshop."""
        ps_sync = self._ps_sync()
        if ps_sync is not None:
            try:
                ps_sync.recheck()
            except Exception:
                pass
        self._refresh_ps_bridge_status()

    def _on_ps_restart(self):
        """Confirm, then restart the selected Photoshop instance so the
        deployed bridge script gets loaded."""
        ps_sync = self._ps_sync()
        if ps_sync is None:
            return
        from core.photoshop_instances import detect_instances, pick_target
        try:
            target = pick_target(detect_instances(), ps_sync.current_version)
        except Exception:
            target = None
        if target is None:
            QMessageBox.warning(self, i18n.tr("重启 Photoshop"),
                                i18n.tr("未检测到运行中的 Photoshop 进程"))
            return
        ret = QMessageBox.question(
            self, i18n.tr("重启 Photoshop"),
            i18n.tr("将关闭并重新启动 Photoshop：\n{path}\n\n未保存的更改可能会丢失，是否继续？", path=target.exe_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        import psutil as _psutil
        import subprocess as _subprocess
        try:
            proc = _psutil.Process(target.pid)
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except _psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass
        try:
            _subprocess.Popen([target.exe_path])
        except OSError as exc:
            QMessageBox.warning(self, i18n.tr("重启 Photoshop"), i18n.tr("启动失败：{e}", e=exc))
            return
        # The bridge script reports heartbeats a few seconds after startup.
        QTimer.singleShot(8000, self._refresh_ps_bridge_status)

    def _on_software_changed(self, text):
        """When the user picks Photoshop, offer the green-edition fix once."""
        if text == "Photoshop":
            QTimer.singleShot(400, self._maybe_prompt_ps_bridge)

    def _maybe_prompt_ps_bridge(self):
        """One-time dialog: bridge deployed but Photoshop not restarted yet."""
        if self._ps_bridge_prompted:
            return
        if self.combo_software.currentText() != "Photoshop":
            return
        ps_sync = self._ps_sync()
        if ps_sync is None:
            return
        try:
            # Non-connecting snapshot: never block the UI on COM.
            st = ps_sync.status_lite()
        except Exception:
            return
        if st.get("backend") != "script-bridge":
            return
        self._ps_bridge_prompted = True
        if st.get("bridgeAlive"):
            return
        ret = QMessageBox.question(
            self, i18n.tr("绿色版 Photoshop"),
            i18n.tr("检测到绿色版（便携版）Photoshop：它未注册 COM 自动化接口，"
            "无法直接同步颜色。\n\n"
            "Colorink 已自动部署同步脚本（脚本桥），重启 Photoshop 后即可"
            "同步前景 / 背景色。\n是否现在重启 Photoshop？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self._on_ps_restart()

    def export_config(self):
        default_name = os.path.join(os.path.expanduser("~"), "Colorink-配置.json")
        path, _ = QFileDialog.getSaveFileName(self, i18n.tr("导出配置"), default_name, i18n.tr("JSON 文件 (*.json)"))
        if not path:
            return
        try:
            config.export_settings_to_file(self.cfg, path)
            QMessageBox.information(self, i18n.tr("导出配置"), i18n.tr("配置已导出到：\n{path}", path=path))
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("导出配置"), i18n.tr("导出失败：{e}", e=e))

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, i18n.tr("导入配置"), os.path.expanduser("~"), i18n.tr("JSON 文件 (*.json)"))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("format") == config.SETTINGS_EXPORT_FORMAT:
                imported = config.import_settings(data)
            else:
                # Legacy raw config (pre-envelope): merge + migrate + normalize.
                if not isinstance(data, dict):
                    raise ValueError(i18n.tr("配置文件格式不正确"))
                imported = config.merge_imported_config(data)
        except ValueError as e:
            QMessageBox.warning(self, i18n.tr("导入配置"), str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("导入配置"), i18n.tr("读取失败：{e}", e=e))
            return
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = imported.get("openAtLogin", False)
        self.cfg = imported
        config.save_hotkey_config(self.cfg)
        if old_autostart != new_autostart:
            autostart.apply_autostart(new_autostart)
        self.refresh_ui()
        self.settingChanged.emit()
        QMessageBox.information(self, i18n.tr("导入配置"), i18n.tr("配置已导入并生效。"))

    def reset_config(self):
        answer = QMessageBox.question(
            self,
            i18n.tr("恢复默认"),
            i18n.tr("确定要恢复所有设置为默认值吗？"),
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
        QMessageBox.information(self, i18n.tr("恢复默认"), i18n.tr("设置已恢复为默认值。"))

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

    def hideEvent(self, event):
        super().hideEvent(event)
        # Settings are closed: re-apply the no-focus window state if enabled.
        mv = self._parent
        if mv is not None and self.cfg.get("noFocusMode", False):
            mv.update_window_flags()
            mv.update_no_focus_policies()

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

    # 6. 关于 — 检查更新 / 关于作者
    def on_check_update(self):
        """Run the update check on a worker thread, then show a dialog."""
        if getattr(self, "_update_worker", None) is not None:
            return  # Already running
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText(i18n.tr("检查中..."))
        worker = _UpdateWorker(self)
        worker.done.connect(self._on_update_result)
        # Keep a reference alive until the signal fires; QThread auto-deletes
        # via finished->deleteLater once we let go in the slot.
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _on_update_result(self, result: dict):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText(i18n.tr("检查更新"))
        self._update_worker = None
        self.prompt_update(result)

    def prompt_update(self, result: dict):
        """Show the "new version available" dialog and act on the choice.

        Shared by the manual check and the tray-notification path so both
        offer the same in-app download flow.
        """
        if "error" in result:
            QMessageBox.warning(self, i18n.tr("检查更新"), self._update_error_text(result))
            return

        current = result.get("current_version", "?")
        latest = result.get("latest_version", "?")
        url = result.get("release_url", updater.GITHUB_URL)
        notes = result.get("release_notes", "")
        has_update = result.get("has_update", False)

        if has_update:
            current_flavor = updater.build_flavor(sys.executable)
            other_flavor = "onedir" if current_flavor == "onefile" else "onefile"
            assets = result.get("assets", [])
            # Only offer a download button when a usable asset actually exists
            # for that flavor.  This prevents the “switch” button from sending
            # the user to a GitHub source archive when no onedir zip exists.
            current_asset = updater.find_installer_asset(assets, flavor=current_flavor)
            other_asset = updater.find_installer_asset(assets, flavor=other_flavor)
            msg = (
                f"{i18n.tr('发现新版本')} {latest}！\n"
                f"{i18n.tr('当前版本')}: v{current}\n\n"
            )
            if notes:
                snippet = notes if len(notes) <= 600 else notes[:600] + "..."
                msg += f"{i18n.tr('更新内容:')}\n{snippet}\n\n"
            msg += i18n.tr("可一键下载安装包，或前往 GitHub 页面。")
            box = QMessageBox(self)
            box.setWindowTitle(i18n.tr("发现新版本"))
            box.setText(msg)
            dl_btn = None
            if current_asset is not None:
                dl_btn = box.addButton(
                    i18n.tr("下载更新 ({flavor})", flavor=current_flavor),
                    QMessageBox.ButtonRole.AcceptRole,
                )
            switch_btn = None
            if other_asset is not None:
                switch_btn = box.addButton(
                    i18n.tr("下载 {flavor} 版（切换）", flavor=other_flavor),
                    QMessageBox.ButtonRole.ActionRole,
                )
            open_btn = box.addButton(i18n.tr("前往下载"), QMessageBox.ButtonRole.ActionRole)
            skip_btn = box.addButton(i18n.tr("跳过此版本"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(i18n.tr("稍后"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if dl_btn is not None and clicked is dl_btn:
                self._download_release(result, flavor=current_flavor)
            elif switch_btn is not None and clicked is switch_btn:
                self._download_release(result, flavor=other_flavor)
            elif clicked is open_btn:
                webbrowser.open(url)
            elif clicked is skip_btn:
                self.cfg["skippedUpdateVersion"] = latest
                self._persist_config()
        else:
            QMessageBox.information(
                self, i18n.tr("检查更新"),
                f"{i18n.tr('已是最新版本')} (v{current})"
            )

    def _update_error_text(self, result: dict) -> str:
        """Translate a structured updater error (source-as-key + detail)."""
        return i18n.tr(result.get("error", ""), detail=result.get("error_detail", ""))

    def _download_release(self, result: dict, flavor: str | None = None):
        """Download the picked installer asset to a user-chosen path.

        ``flavor`` selects which build to download: ``"onefile"`` or
        ``"onedir"``. It defaults to the currently running build.
        """
        flavor = flavor or updater.build_flavor(sys.executable)
        asset = updater.find_installer_asset(result.get("assets", []), flavor=flavor)
        if asset is None:
            # No installer asset on the release — fall back to the page.
            webbrowser.open(result.get("release_url", updater.GITHUB_URL))
            return
        name = asset.get("name") or ("Colorink-Onedir.zip" if flavor == "onedir" else "Colorink.exe")
        default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        is_zip = name.lower().endswith(".zip")
        file_filter = i18n.tr("更新包 (*.exe *.zip)") if is_zip else i18n.tr("程序 (*.exe)")
        dest, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("保存安装包"), os.path.join(default_dir, name), file_filter
        )
        if not dest:
            return
        # GitHub 对 release asset 提供 SHA-256 digest（"sha256:<hex>"）；
        # 有则校验，老资产没有 digest 时退回仅字节数校验。
        sha256 = None
        digest = asset.get("digest") or ""
        if isinstance(digest, str) and digest.startswith("sha256:"):
            sha256 = digest[len("sha256:"):].strip()
        # The actual downloaded flavor follows the asset extension: a onedir
        # request may fall back to the onefile EXE when no zip is published.
        self._pending_download_flavor = "onedir" if is_zip else "onefile"
        self._download_worker = _DownloadWorker(
            asset["url"], dest, asset.get("size"), self, sha256=sha256
        )
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.finished.connect(self._download_worker.deleteLater)
        self.btn_check_update.setText(i18n.tr("下载中"))
        self.btn_check_update.setEnabled(False)
        self._download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int):
        label = i18n.tr("下载中")
        if total:
            pct = int(downloaded * 100 / total)
            self.btn_check_update.setText(f"{label} {pct}%")
        else:
            self.btn_check_update.setText(f"{label} {downloaded // 1024}KB")

    def _flush_state_before_update(self):
        """Persist settings + window geometry before the self-replace exit.

        ``os._exit`` bypasses normal shutdown, so without this the user's last
        window position and any unsaved settings changes are lost on update.
        """
        parent = getattr(self, "_parent", None)
        # Flush the main window's pending module write first so a stale copy
        # can't clobber the sidebar's fuller settings snapshot below.
        if parent is not None:
            try:
                flush = getattr(parent, "_flush_module_config_save", None)
                if callable(flush):
                    flush()
            except Exception:
                pass
        try:
            self._persist_config()
        except Exception:
            pass
        if parent is not None:
            try:
                save_geom = getattr(parent, "save_window_geometry", None)
                if callable(save_geom):
                    save_geom()
            except Exception:
                pass

    def _on_download_done(self, result: dict):
        self.btn_check_update.setText(i18n.tr("检查更新"))
        self.btn_check_update.setEnabled(True)
        self._download_worker = None
        downloaded_flavor = getattr(self, "_pending_download_flavor", None) or updater.build_flavor(sys.executable)
        self._pending_download_flavor = None
        if "error" in result:
            QMessageBox.warning(self, i18n.tr("下载失败"), self._update_error_text(result))
            return
        path = result["path"]
        is_zip = path.lower().endswith(".zip")
        current_flavor = updater.build_flavor(sys.executable)
        same_flavor = downloaded_flavor == current_flavor
        can_update = same_flavor and updater.can_self_update(sys.executable)
        # A onedir update must be a zip; an onefile update must be an exe.
        if (downloaded_flavor == "onedir") != is_zip:
            can_update = False

        box = QMessageBox(self)
        box.setWindowTitle(i18n.tr("下载完成"))
        text = i18n.tr("已下载到:\n{path}", path=path)
        if not same_flavor:
            if is_zip:
                text += "\n\n" + i18n.tr(
                    "这是 onedir 版。请先退出当前 Colorink，解压 zip 后运行其中的 Colorink.exe 以切换。"
                )
            else:
                text += "\n\n" + i18n.tr(
                    "这是 onefile 版。请先退出当前 Colorink，再运行该文件以切换。"
                )
        box.setText(text)
        install_btn = None
        if can_update:
            install_btn = box.addButton(i18n.tr("更新并重启"), QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton(i18n.tr("打开所在文件夹"), QMessageBox.ButtonRole.ActionRole)
        run_btn = None
        if not is_zip:
            run_btn = box.addButton(i18n.tr("立即运行"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(i18n.tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if install_btn is not None and clicked is install_btn:
            # Hand the update over to a detached helper, then exit. The helper
            # waits for our lock to release, replaces the app payload and
            # relaunches. If spawning fails, fall back to just running it.
            if downloaded_flavor == "onedir":
                launched = updater.launch_onedir_update(path, sys.executable)
            else:
                launched = updater.launch_self_replace(path, sys.executable)
            if launched:
                self._flush_state_before_update()
                os._exit(0)
            os.startfile(path)
        elif clicked is folder_btn:
            os.startfile(os.path.dirname(path))
        elif clicked is run_btn:
            os.startfile(path)

    def on_about_author(self):
        """Open the author's Bilibili homepage in the default browser."""
        webbrowser.open(updater.BILIBILI_URL)

    def _on_check_updates_toggled(self, checked: bool):
        self.cfg["checkUpdatesOnStartup"] = bool(checked)
        self._persist_config()

    def _on_language_changed(self, _index=None):
        # Read currentData() rather than the signal argument: PyQt6's
        # currentIndexChanged is overloaded (int / str) and the bound overload
        # can differ, so the argument is unreliable.
        lang = self.cmb_language.currentData()
        if not lang:
            return
        self.cfg["language"] = lang
        self._persist_config()
        i18n.set_language(i18n.resolve_language(lang))
        self.retranslate()
        parent = getattr(self, "_parent", None)
        if parent is not None and hasattr(parent, "retranslate"):
            parent.retranslate()


    # ── Companion helpers ──────────────────────────────────────────────
    def _refresh_companion_status(self):
        if not hasattr(self, 'parent') or self._parent is None: return
        if not hasattr(self._parent, 'sync_thread'): return
        c = self._parent.sync_thread.companion_sync
        connected = getattr(c, '_connected', False)
        if connected:
            self.lbl_companion_status.setText(i18n.tr("● 已连接"))
            self._set_label_state(self.lbl_companion_status, "success")
            self.btn_companion_reconnect.setVisible(False)
            self.btn_companion_disconnect.setVisible(True)
        elif c._has_session():
            self.lbl_companion_status.setText(i18n.tr("○ 已保存 — 等待 CSP..."))
            self._set_label_state(self.lbl_companion_status, "warning")
            self.btn_companion_reconnect.setVisible(True)
            self.btn_companion_disconnect.setVisible(False)
        else:
            self.lbl_companion_status.setText(i18n.tr("○ 未设置"))
            self._set_label_state(self.lbl_companion_status, "muted")
            self.btn_companion_reconnect.setText(i18n.tr("连接智能手机"))
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


class _DownloadWorker(QThread):
    """Background worker that downloads a release asset to disk."""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(dict)

    def __init__(self, url: str, dest_path: str, total_size, parent=None,
                 sha256: str | None = None):
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._total_size = total_size
        self._sha256 = sha256

    def run(self):  # noqa: D401 - QThread override
        self.done.emit(updater.download_release(
            self._url,
            self._dest_path,
            total_size=self._total_size,
            progress_cb=lambda downloaded, total: self.progress.emit(downloaded, total),
            sha256=self._sha256,
        ))
