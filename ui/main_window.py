import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import config
# Re-exported for backwards compatibility (main.py / tests import these from
# ui.main_window even though the implementations now live in mixin modules).
from core.foreground import (
    _exe_matches_drawing_app,  # noqa: F401  (re-export)
    _resolve_process_exe,  # noqa: F401  (re-export)
    _title_matches_drawing_app,  # noqa: F401  (re-export)
    bring_process_to_foreground,  # noqa: F401  (re-export)
)
from ui.color_model import ColorState
from ui.color_picker_overlay import ColorPickerOverlay
from ui.color_preview_box import ColorPreviewBox
from ui.color_wheel import ColorWheel
from ui.lab_visualizer import LabSlider, LabSquare
from ui.picker_panes import LabPane, WheelPane
from ui.settings_sidebar import SettingsSidebar
from ui.widgets import TitleBar, _title_bar_content_offset  # noqa: F401  (re-export)
from ui.window import (
    ColorSlotsMixin,
    ColorUpdatesMixin,
    HotkeyMixin,
    LayoutMixin,
    PickerActionsMixin,
    SyncMixin,
    ThemeMixin,
    TrayMixin,
)

class MainWindow(PickerActionsMixin, ThemeMixin, LayoutMixin, ColorUpdatesMixin,
                 TrayMixin, SyncMixin, HotkeyMixin, ColorSlotsMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load_hotkey_config()
        # Resolve the UI language before any widget is built so every tr()
        # lookup sees the configured language.
        from core import i18n
        i18n.set_language(i18n.resolve_language(self.cfg.get("language", "auto")))
        self.current_ui_scale = self.cfg.get("uiScale", 100)
        self.current_rgb = (180, 130, 30)
        # Unified colour model: single source of truth for the picked colour.
        self.color_state = ColorState()
        self.active_slot = "fg"  # "fg" | "bg"
        # Transparent-color state per slot. When set, the swatch renders the
        # checker tile and CSP writes are sent with IsColorTransparent=true
        # (companion mode) / shortcut fallback (memory mode).
        self._fg_transparent = False
        self._bg_transparent = False

        # Deferred-render coalescer.
        # update_video_colors() does heavy pure-visual work (18 slider groove
        # gradients + L-gamut ranges binary search) on every slider/wheel drag
        # step. Running that synchronously inside the dragged widget's
        # mouseMoveEvent blocks the GUI thread and delays the slider handle /
        # color-wheel indicator paints, so they stop following the cursor
        # ("不跟手"). We defer exactly that visual-only work with a coalesced
        # single-shot QTimer so handle/indicator paints flush first and the
        # heavy cosmetics lag by at most one event-loop iteration (~16ms).
        self._deferred_color_timer = QTimer(self)
        self._deferred_color_timer.setSingleShot(True)
        self._deferred_color_timer.setInterval(16)
        self._deferred_color_timer.timeout.connect(self._apply_deferred_color_updates)
        self._deferred_color_pending: tuple[int, int, int] | None = None  # latest (r, g, b) awaiting render
        self._lab_gamut_timer: QTimer = QTimer(self)
        self._lab_gamut_timer.setSingleShot(True)
        self._lab_gamut_timer.timeout.connect(self._update_lab_slider_gamut_range)
        self._lab_prerender_timer: QTimer = QTimer(self)
        self._lab_prerender_timer.setSingleShot(True)
        self._lab_prerender_timer.timeout.connect(self._prerender_lab)
        self._module_layout_timer: QTimer = QTimer(self)
        self._module_layout_timer.setSingleShot(True)
        self._module_layout_timer.timeout.connect(self._flush_module_layout_refresh)
        self._module_layout_refresh_pending: bool = False
        self._module_save_timer: QTimer = QTimer(self)
        self._module_save_timer.setSingleShot(True)
        self._module_save_timer.timeout.connect(self._flush_module_config_save)
        self._module_save_pending: bool = False
        self._deferred_dynamic_gradients_pending: bool = False
        self._gamut_oklch_C = None
        self._gamut_oklch_h = None

        # Content-driven window height state
        self._last_auto_height = None
        self._manual_height_override = False
        self._adjusting_content_height = False
        self._content_height_adjust_pending = False
        self._content_height_timer = QTimer(self)
        self._content_height_timer.setSingleShot(True)
        self._content_height_timer.timeout.connect(self._run_deferred_content_height)
        
        # Dragging state (mouse click-through toggle override)
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        self.auto_hidden = False

        self.slider_row_layouts = []
        self.slider_labels = {}
        self.resizing = False
        self.resize_dir = None
        self.resize_start_pos = None
        self.resize_start_geometry = None
        
        # DPI-aware screen tracking to prevent size drift when dragging across monitors
        self._last_dpr = None       # Previous screen devicePixelRatio
        self._dpi_locked_size = None  # (w, h) logical size frozen during DPI transition

        # Fullscreen grayscale: native capture supports OKLCh/Luma and
        # per-screen targets; Mag is the system-wide Luma fallback.
        mode = self.cfg.get("grayscaleFilterMode", "oklch")
        backend = self.cfg.get("grayscaleFilterBackend", "native")
        if backend == "mag":
            from core.mag_grayscale import MagFilterController
            self.grayscale_overlay = MagFilterController(mode="luma")
        else:
            from core.native_grayscale import NativeGrayscaleController
            if mode not in ("oklch", "luma"):
                mode = "oklch"
            self.grayscale_overlay = NativeGrayscaleController(mode=mode)
            if not self.grayscale_overlay.is_available:
                # 原生运行时缺失 / 字节码版本不匹配：自动回退到系统 Mag
                # 后端（仅 Luma、作用于全部屏幕），保证默认配置下灰度
                # 滤镜仍然可用；Mag 也不可用时保留 native 以便报错。
                from core.mag_grayscale import MagFilterController as _Mag
                fallback = _Mag(mode="luma")
                if fallback.is_available:
                    print("[Grayscale] Native backend unavailable — falling back to Mag (Luma)")
                    self.grayscale_overlay = fallback
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)
        # Warm the OKLCh capture/OpenGL/PBO chain off-screen so Ctrl+G only
        # reveals a prepared frame instead of paying initialization latency.
        if backend != "mag":
            prepare = getattr(self.grayscale_overlay, "prepare", None)
            if callable(prepare):
                # grayscale_overlay.prepare runs once the window is up:
                # it warms the native GL overlay off-screen (light preheat).
                QTimer.singleShot(800, prepare)
            # Pre-import dxcam off the GUI thread so the first Ctrl+G does not
            # pay the one-time ~0.4s module/D3D11/comtypes import cost.
            if sys.modules.get("dxcam") is None:
                import importlib as _importlib
                import threading as _threading
                _threading.Thread(
                    target=_importlib.import_module,
                    args=("dxcam",),
                    daemon=True,
                    name="colorink-dxcam-preload",
                ).start()

        # Global color picker overlay (magnifier + click-to-pick)
        self.picker_overlay = ColorPickerOverlay(None)
        self.picker_overlay.set_zoom(self.cfg.get("pickerZoom", 6))
        self.picker_overlay.colorPicked.connect(self._on_picker_color_picked)

        self.init_ui()
        self.init_hotkeys()
        self.init_memory_sync()
        self.init_foreground_tracker()
        self.apply_theme()
        self.init_tray()
        # Timestamp of the last consumed local mouse/pen toggle press, used
        # to deduplicate the tablet + synthetic-mouse event pair.
        self._last_lab_toggle_ts = 0.0
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        # Deferred background update check → tray notification, not a modal.
        self._startup_update_checked = False
        self._pending_update = None
        QTimer.singleShot(4000, self._check_updates_silently)

    def init_ui(self):
        # Frameless, transparent, stays on top, taskbar icon based on config
        self.update_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Load window dimensions, adjusting for DPI differences since last save
        win_cfg = config.load_window_config()
        scale = self.cfg.get("uiScale", 100) / 100.0
        saved_dpr = win_cfg.get("dpr", None)
        current_dpr = self.devicePixelRatio() if hasattr(self, "devicePixelRatio") else 1.0
        if current_dpr < 0.1:
            current_dpr = 1.0
        
        w = win_cfg.get("width", int(320 * scale))
        h = win_cfg.get("height", int(710 * scale))
        
        # If saved on a different DPI screen, adjust to current screen's logical pixels
        if saved_dpr is not None and abs(current_dpr - saved_dpr) > 0.01:
            phys_w = w * saved_dpr
            phys_h = h * saved_dpr
            w = int(phys_w / current_dpr)
            h = int(phys_h / current_dpr)
        
        self.resize(w, h)
        if "x" in win_cfg and "y" in win_cfg:
            self.move(win_cfg["x"], win_cfg["y"])

        # Central Widget
        self.central = QWidget(self)
        self.central.setObjectName("CentralWidget")
        self.central.setMouseTracking(True)
        self.setCentralWidget(self.central)
        self.setMouseTracking(True)

        # Main Layout
        self.main_layout = QVBoxLayout(self.central)
        self.main_layout.setContentsMargins(8, 0, 8, 8)  # Thin frame border
        self.main_layout.setSpacing(0)

        # Title Bar
        self.title_bar = TitleBar(self)
        self.title_bar.setVisible(self.cfg.get("showTitleBar", True))
        self.title_bar.btn_close.clicked.connect(self.close_application)
        self.title_bar.btn_settings.clicked.connect(self.toggle_settings_sidebar)
        self.main_layout.addWidget(self.title_bar)

        # Stacked pane for visualizers
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)

        # Pane 1: HSV Color Wheel
        self.pane_wheel = WheelPane()
        wheel_layout = QVBoxLayout(self.pane_wheel)
        wheel_layout.setContentsMargins(0, 0, 0, 0)
        self.color_wheel = ColorWheel()
        self.color_wheel.colorChanged.connect(self.on_wheel_color_changed)
        self.color_wheel.interactionFinished.connect(self.on_interaction_finished)
        wheel_layout.addWidget(self.color_wheel)

        # Source-space tracking — carries the "native" color space the user
        # last interacted with, so sync backends can write source→memory
        # without an RGB→source round-trip.
        self._source_space = "rgb"     # "hsv" | "hls" | "rgb" | "lab" | "oklab" | "oklch" | "cmyk"
        self._source_values = None     # {ch: float, ...}  — float values in that space
        # Per-slot source tracking (fg / bg)
        self._fg_source_space = "rgb"
        self._fg_source_values = None
        self._bg_source_space = "rgb"
        self._bg_source_values = None

        # Floating mode buttons parented to their respective views
        self.btn_mode_wheel = QPushButton("☉", self.pane_wheel)
        self.btn_mode_wheel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_wheel.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_wheel.clicked.connect(self.toggle_picker_mode)
        # Never keep keyboard focus: otherwise Space (the default LAB-toggle
        # hotkey) would "click" the focused button from anywhere in the window.
        self.btn_mode_wheel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pane_wheel.set_mode_button(self.btn_mode_wheel)
        
        self.stack.addWidget(self.pane_wheel)

        # Pane 2: LAB Space
        self.pane_lab = LabPane(self)
        self.lab_layout = QHBoxLayout(self.pane_lab)
        self.lab_layout.setContentsMargins(0, 0, 0, 0)
        self.lab_layout.setSpacing(6)
        
        self.lab_square = LabSquare()
        self.lab_square.colorChanged.connect(self.on_lab_square_color_changed)
        self.lab_square.interactionFinished.connect(self.on_interaction_finished)
        
        # Set initial visualizer mode from config
        viz_mode = self.cfg.get("visualizerMode", "lab")
        self.lab_square.set_render_mode(viz_mode)
        
        # Wrap vertical lightness slider in a column widget to support height adjustment and hiding
        self.lab_slider_column = QWidget()
        slider_col_layout = QVBoxLayout(self.lab_slider_column)
        slider_col_layout.setContentsMargins(0, 0, 0, 0)
        slider_col_layout.setSpacing(4)
        
        self.lab_slider = LabSlider()
        self.lab_slider.lightnessChanged.connect(self._on_lab_lightness_changed)
        self.lab_slider.interactionFinished.connect(self.on_interaction_finished)
        slider_col_layout.addWidget(self.lab_slider)
        
        self.lab_layout.addWidget(self.lab_square, stretch=1)
        self.lab_layout.addWidget(self.lab_slider_column)
        
        # Floating mode button parented directly to self.pane_lab
        self.btn_mode_lab = QPushButton("△", self.pane_lab)
        self.btn_mode_lab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_lab.setToolTip("切换模式 (色轮 / LAB)")
        self.btn_mode_lab.clicked.connect(self.toggle_picker_mode)
        self.btn_mode_lab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pane_lab.set_mode_button(self.btn_mode_lab)
        
        self.stack.addWidget(self.pane_lab)

        # Sliders Area
        self.sliders_container = QWidget()
        self.sliders_layout = QVBoxLayout(self.sliders_container)
        self.sliders_layout.setContentsMargins(10, 6, 10, 10)
        self.sliders_layout.setSpacing(8)
        self.main_layout.addWidget(self.sliders_container)

        self.setup_sliders()

        # Overlapping swatches box (Floating on MainWindow to avoid clipping)
        self.preview_box = ColorPreviewBox(self)
        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.preview_box.set_colors(QColor(*self.current_rgb), QColor(255, 255, 255))
        


        # Settings Sidebar (Floating on MainWindow to avoid z-order issues)
        self.settings_sidebar = SettingsSidebar(self)
        self.settings_sidebar.setVisible(False)
        self.settings_sidebar.settingChanged.connect(self.on_settings_saved)

        # Sync slider state
        init_color = self.color_state.set_from("rgb", self.current_rgb)
        self._project_color(init_color, source="init")

        # Create floating module switch button (next to ⊙/△)
        self._init_module_button()

        # Apply color-space module (wheel mode + slider set from config or default)
        self._current_module = self.cfg.get("colorSpaceModule", "hsv")
        self._apply_module(self._current_module)
        
        # Apply slider visibility and order on startup
        self.refresh_slider_visibility_and_order()
        self.update_mode_buttons_visibility()
        self.update_no_focus_policies()

        # Apply persisted ringless state after all components and button
        # visibility are known
        self._sync_ringless_mode()
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_lab_prerender(100)
