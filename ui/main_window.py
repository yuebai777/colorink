import colorsys
import os
import sys
import time
from typing import cast

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import config
from core.foreground import (
    _exe_matches_drawing_app,
    _resolve_process_exe,
    _title_matches_drawing_app,
    bring_process_to_foreground,
)
from ui.color_conversions import (
    hsv_to_hls_floats,
    lab_to_rgb,
    oklab_to_rgb,
    oklch_to_rgb,
    rgb_to_lab,
    rgb_to_oklab,
    rgb_to_oklch,
)
from ui.color_model import Color, ColorState
from ui.color_history import ColorHistoryWidget
from ui.color_picker_overlay import ColorPickerOverlay
from ui.color_preview_box import ColorPreviewBox
from ui.color_wheel import ColorWheel, hsv_to_rgb, rgb_to_hsv
from ui.hotkey_button import (
    MOUSE_BUTTON_NAME_BY_QT,
    capture_active,
    parse_key_event,
)
from ui.lab_visualizer import LabSlider, LabSquare
from ui.picker_panes import LabPane, WheelPane
from ui.ringless_mode import (
    RINGLESS_ACTIVE_BORDER,
    RinglessConfig,
    resolve_ringless_layout,
)
from ui.settings_sidebar import SettingsSidebar
from ui.slider_themes import get_slider_theme
from ui.widgets import (
    GradientSlider,
    SliderValueLabel,
    TitleBar,
    _title_bar_content_offset,
    _visible_title_bar_height,
)
from ui.window import ColorSlotsMixin, HotkeyMixin, SyncMixin, TrayMixin

# ── Color-space module definitions ────────────────────────────────────────
# Each module bundles a default wheel mode + slider subset (user can
# still toggle individual slider groups in settings via the "module
# default + adjustable" policy).
_MODULE_DEFS = {
    "hsv":  {"wheel": "hsv-square",   "sliders": ["HSV", "RGB", "LAB", "OKLab", "OKLCh"]},
    "hls":  {"wheel": "hls-triangle",  "sliders": ["HSL", "RGB", "LAB", "OKLab", "OKLCh"]},
    "rgb":  {"wheel": "rgb-slice",     "sliders": ["RGB", "HSV", "LAB", "OKLab", "OKLCh"]},
    "lch":  {"wheel": "oklch-slice",   "sliders": ["OKLCh", "OKLab", "RGB"]},
}
_MODULE_NAMES = {"hsv": "HSV", "hls": "HLS", "rgb": "RGB", "lch": "LCH"}
_MODULE_ORDER = ["hsv", "hls", "rgb", "lch"]

# ── Normalized chroma scale for the C_oklch slider ────────────────────────
# The C slider keeps its handle stable while L/H changes by representing a
# fraction of the current in-gamut chroma boundary. L-only drags still use
# the absolute C/H snapshot for authentic OKLCh coordinates.
_C_SCALE = 1000          # 0.001 chroma resolution (absolute C slider)
_C_SLIDER_MAX = 321      # C slider range → 0..0.321 absolute chroma (sRGB max C ≈ 0.3215)


class MainWindow(TrayMixin, SyncMixin, HotkeyMixin, ColorSlotsMixin, QMainWindow):
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

    def setup_sliders(self):
        # Create standard RGB, HSV, HSL, LAB groups
        self.slider_widgets = {}
        self.slider_containers = {}
        same_space_base = self.cfg.get("sliderSameSpace", 6)
        
        # 1. RGB
        self.slider_containers["RGB"] = QWidget()
        rgb_lay = QVBoxLayout(self.slider_containers["RGB"])
        rgb_lay.setContentsMargins(0, 0, 0, 0)
        rgb_lay.setSpacing(same_space_base)
        self.create_group_sliders("RGB", ["R", "G", "B"], rgb_lay)
        self.sliders_layout.addWidget(self.slider_containers["RGB"])
        
        # 2. HSV
        self.slider_containers["HSV"] = QWidget()
        hsv_lay = QVBoxLayout(self.slider_containers["HSV"])
        hsv_lay.setContentsMargins(0, 0, 0, 0)
        hsv_lay.setSpacing(same_space_base)
        self.create_group_sliders("HSV", ["H_hsv", "S_hsv", "V_hsv"], hsv_lay)
        self.sliders_layout.addWidget(self.slider_containers["HSV"])
        
        # 3. HSL
        self.slider_containers["HSL"] = QWidget()
        hsl_lay = QVBoxLayout(self.slider_containers["HSL"])
        hsl_lay.setContentsMargins(0, 0, 0, 0)
        hsl_lay.setSpacing(same_space_base)
        self.create_group_sliders("HSL", ["H_hsl", "L_hsl", "S_hsl"], hsl_lay)
        self.sliders_layout.addWidget(self.slider_containers["HSL"])
        
        # 4. LAB
        self.slider_containers["LAB"] = QWidget()
        lab_lay = QVBoxLayout(self.slider_containers["LAB"])
        lab_lay.setContentsMargins(0, 0, 0, 0)
        lab_lay.setSpacing(same_space_base)
        self.create_group_sliders("LAB", ["L_lab", "a_lab", "b_lab"], lab_lay)
        self.sliders_layout.addWidget(self.slider_containers["LAB"])
        
        # 5. OKLab
        self.slider_containers["OKLab"] = QWidget()
        oklab_lay = QVBoxLayout(self.slider_containers["OKLab"])
        oklab_lay.setContentsMargins(0, 0, 0, 0)
        oklab_lay.setSpacing(same_space_base)
        self.create_group_sliders("OKLab", ["L_oklab", "a_oklab", "b_oklab"], oklab_lay)
        self.sliders_layout.addWidget(self.slider_containers["OKLab"])
        
        # 6. OKLCh
        self.slider_containers["OKLCh"] = QWidget()
        oklch_lay = QVBoxLayout(self.slider_containers["OKLCh"])
        oklch_lay.setContentsMargins(0, 0, 0, 0)
        oklch_lay.setSpacing(same_space_base)
        self.create_group_sliders("OKLCh", ["L_oklch", "C_oklch", "h_oklch"], oklch_lay)
        self.sliders_layout.addWidget(self.slider_containers["OKLCh"])

        # 7. Color History — shares the sliders_layout's order mechanism so it
        # can be reordered among the slider groups via the settings sidebar.
        self.slider_containers["History"] = QWidget()
        history_lay = QVBoxLayout(self.slider_containers["History"])
        history_lay.setContentsMargins(0, 0, 0, 0)
        history_lay.setSpacing(0)
        self.color_history = ColorHistoryWidget(self.slider_containers["History"])
        self.color_history.color_picked.connect(self.on_history_color_picked)
        # Initial grid geometry from config
        self.color_history.configure(
            self.cfg.get("historyColumns", 8),
            self.cfg.get("historyRows", 2),
            self.cfg.get("historySwatchSize", 18),
        )
        # Restore persisted colors (config stores a list of entries — old
        # format [r,g,b] or new format {"rgb":[r,g,b],"s":"hsv","v":[h,s,v]})
        persisted = self.cfg.get("historyColors", [])
        self._history_source = {}  # index → {"source":..., "values":...}
        self._color_source_store = {}  # hex_key → {"rgb":..., "s":..., "v":...}
        self._SOURCE_CHANNELS = {
            "rgb": ("r", "g", "b"), "cmyk": ("c", "m", "y", "k"),
            "hsv": ("h", "s", "v"), "hls": ("h", "l", "s"),
            "lab": ("l", "a", "b"), "oklab": ("L", "a", "b"), "oklch": ("L", "C", "h"),
        }
        if persisted:
            from PyQt6.QtGui import QColor as _QColor
            initial_colors = []
            for i, entry in enumerate(persisted):
                src = vals = None
                if isinstance(entry, list):
                    r, g, b = int(entry[0]), int(entry[1]), int(entry[2])
                elif isinstance(entry, dict):
                    rgb = entry.get("rgb", [0, 0, 0])
                    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
                    src = entry.get("s")
                    vals = entry.get("v")
                    if src and vals:
                        self._history_source[i] = {"source": src, "values": vals}
                else:
                    continue
                hex_key = f"#{r:02x}{g:02x}{b:02x}"
                initial_colors.append(_QColor(r, g, b))
                if src and vals:
                    self._color_source_store[hex_key] = {"rgb": [r, g, b], "s": src, "v": vals}
            self.color_history.set_colors(initial_colors)
        history_lay.addWidget(self.color_history)
        self.sliders_layout.addWidget(self.slider_containers["History"])

    def create_group_sliders(self, group, channels, layout):
        for chan in channels:
            row = QHBoxLayout()
            row.setSpacing(1)
            self.slider_row_layouts.append(row)
            
            # Label
            label_text = chan.split("_")[0].upper()
            label = QLabel(label_text)
            label.setFixedWidth(12)
            label.setObjectName("ChannelLabel")
            self.slider_labels[chan] = label
            
            slider = GradientSlider(Qt.Orientation.Horizontal)
            if "H" in chan:
                slider.setRange(0, 360)
            elif chan in ("S_hsv", "V_hsv", "L_hsl", "S_hsl", "L_lab"):
                slider.setRange(0, 100)
            elif chan in ("a_lab", "b_lab"):
                slider.setRange(-128, 127)
            elif chan in ("a_oklab", "b_oklab"):
                slider.setRange(-40, 40)
            elif chan in ("L_oklab", "L_oklch"):
                slider.setRange(0, 100)
            elif chan == "C_oklch":
                slider.setRange(0, _C_SLIDER_MAX)
            elif chan == "h_oklch":
                slider.setRange(0, 360)
            else:
                slider.setRange(0, 255)
                
            val_label = SliderValueLabel(slider)
            val_label.setFixedWidth(27)
            val_label.setObjectName("ValueLabel")
            val_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
            row.addWidget(label)
            row.addWidget(slider)
            row.addSpacing(4)
            row.addWidget(val_label)
            layout.addLayout(row)
            
            self.slider_widgets[chan] = (slider, val_label)
            
            # Connect signals
            slider.sliderReleased.connect(self.on_interaction_finished)
            if group == "RGB":
                slider.valueChanged.connect(self.on_rgb_slider_changed)
            elif group == "HSV":
                slider.valueChanged.connect(self.on_hsv_slider_changed)
            elif group == "HSL":
                slider.valueChanged.connect(self.on_hsl_slider_changed)
            elif group == "LAB":
                slider.valueChanged.connect(self.on_lab_slider_changed)
            elif group == "OKLab":
                slider.valueChanged.connect(self.on_oklab_slider_changed)
            elif group == "OKLCh":
                slider.valueChanged.connect(self.on_oklch_slider_changed)

    def _init_module_button(self):
        """Create a floating button next to ⊙/△ to cycle HSV→HLS→LCH modules."""
        btn = QPushButton("HSV", self.pane_wheel)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("切换色彩空间模块 (HSV / HLS / LCH)")
        btn.clicked.connect(self._next_module)
        # Never keep keyboard focus — Space (the default LAB-toggle hotkey)
        # must not re-activate this button from anywhere in the window.
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setVisible(self.cfg.get("showModuleSwitchButton", True))
        btn.setObjectName("ModuleButton")
        self.pane_wheel.set_module_button(btn)
        self.btn_module = btn

    def _update_module_button_label(self):
        if hasattr(self, "btn_module"):
            name = {"hsv": "◉", "hls": "△", "lch": "◈"}.get(self._current_module, "◉")
            self.btn_module.setText(name)
            self.btn_module.setToolTip(f"模块: {_MODULE_NAMES.get(self._current_module, 'HSV')} (点击切换)")

    def update_mode_buttons_visibility(self):
        idx = self.stack.currentIndex()
        show_module = self.cfg.get("showModuleSwitchButton", True)
        show_lab_toggle = self.cfg.get("showLabToggleButton", True)
        if hasattr(self, "pane_wheel"):
            self.pane_wheel.set_module_slot_reserved(show_module)
        if hasattr(self, "pane_lab"):
            self.pane_lab.set_module_slot_reserved(show_module)
        if idx == 0:
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_wheel.raise_()
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.hide()
            # Module button only visible in wheel pane
            if hasattr(self, 'btn_module'):
                self.btn_module.setVisible(show_module)
                if show_module:
                    self.btn_module.raise_()
        else:
            if hasattr(self, 'btn_mode_lab'):
                self.btn_mode_lab.setVisible(show_lab_toggle)
                if show_lab_toggle:
                    self.btn_mode_lab.raise_()
            if hasattr(self, 'btn_mode_wheel'):
                self.btn_mode_wheel.hide()
            if hasattr(self, 'btn_module'):
                self.btn_module.hide()

    def _sync_ringless_mode(self, wheel_size: int | None = None,
                            title_bar_height: int | None = None) -> None:
        """Parse ringless config and propagate layout to all components.

        The single orchestration entry point: reads ``hideHueRing`` /
        ``ringlessControlsSide``, resolves a ``RinglessLayout`` via
        :func:`resolve_ringless_layout` (using ``stack.currentIndex() == 0``
        for the page gate), and pushes it to ``ColorWheel``,
        ``ColorPreviewBox``, ``WheelPane``, and ``LabPane``.

        On every page the LAB layout reserves a margin equal to
        ``control_bar_height`` on the configured top or bottom edge when
        controls are enabled (ringless active), and zeros it when disabled.
        This gives LAB the same rectangle controls / mode button bar as the
        wheel pane.

        In active ringless mode the stack gets a minimum height of
        ``wheel_size + control_bar_height`` so the slice area stays
        near-square.  Otherwise the explicit ringless minimum is cleared
        (``setMinimumHeight(0)``) and the child hints apply.
        """
        enabled = self.cfg.get("hideHueRing", False)
        raw_side = self.cfg.get("ringlessControlsSide", "right")
        config = RinglessConfig.from_values(
            enabled, raw_side, self.cfg.get("ringlessControlBarPosition", "top")
        )

        wheel_page_active = self.stack.currentIndex() == 0
        scale = self.cfg.get("uiScale", 100) / 100.0
        layout = resolve_ringless_layout(config, wheel_page_active, scale)

        # ── Push layout to every ringless-aware component ──
        self.color_wheel.set_ringless_layout(layout)

        _ws = wheel_size if wheel_size is not None else self.width() - int(16 * scale)
        _tbh = title_bar_height if title_bar_height is not None else _title_bar_content_offset(
            self.title_bar, getattr(self, "main_layout", None)
        )
        self.preview_box.set_ringless_layout(
            layout, self.width(), _tbh, self.stack.height()
        )

        self.pane_wheel.set_ringless_layout(layout)
        self.pane_lab.set_ringless_layout(layout)

        control_margin = layout.control_bar_height if layout.controls_enabled else 0
        if layout.control_bar_position == "bottom":
            self.lab_layout.setContentsMargins(0, 0, 0, control_margin)
        else:
            self.lab_layout.setContentsMargins(0, control_margin, 0, 0)

        # ── Stack minimum height ──
        if layout.controls_enabled:
            self.stack.setMinimumHeight(max(1, _ws + layout.control_bar_height))
        else:
            self.stack.setMinimumHeight(0)

    def toggle_picker_mode(self):
        """Switch picker panes without re-running the full theme/layout pass."""
        new_index = (self.stack.currentIndex() + 1) % 2
        self.stack.setCurrentIndex(new_index)
        self.update_mode_buttons_visibility()
        # Only the page-local ringless geometry needs to move here. Re-running
        # apply_theme() would rebuild every slider stylesheet and gradient while
        # the user is only asking for a pane switch.
        self._sync_ringless_mode()
        self._update_lab_avoid()
        self.color_wheel.schedule_slice_prewarm(350)

        r, g, b = self.current_rgb
        if new_index == 1:  # LAB pane
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(self.lab_square.L)
            lab_slider_column = getattr(self, "lab_slider_column", None)
            if lab_slider_column is not None and lab_slider_column.isVisible():
                self._schedule_lab_gamut_range(50)
        else:  # Color wheel pane
            # The wheel already owns the exact state when the last source was
            # a wheel interaction. Avoid RGB?HSV re-quantization here: it can
            # shift hue slightly and evict the resident full-resolution slice.
            last_source = getattr(self, "_last_update_source", "")
            wheel_rgb = None
            get_color = getattr(self.color_wheel, "get_color", None)
            if callable(get_color):
                wheel_rgb = get_color()
            if last_source != "wheel" and wheel_rgb != (r, g, b):
                self.color_wheel.set_color(r, g, b, block_signals=True)
            else:
                self.color_wheel.update()
            if hasattr(self, "_schedule_lab_prerender"):
                self._schedule_lab_prerender(50)
        self.update()

    def _update_lab_avoid(self):
        """Tell LabSquare how much of its top is covered by the floating
        preview box, so the ab plane renders below it instead of being hidden.

        In ringless mode the top control bar reserves space via the LAB layout
        margin, so legacy preview-box avoidance is skipped.
        """
        if not hasattr(self, 'lab_square') or not hasattr(self, 'preview_box'):
            return
        pb = self.preview_box
        ls = self.lab_square

        # ── Ringless mode: layout margin owns the top spacing ──
        if self.cfg.get("hideHueRing", False):
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return

        if pb.position_mode != "top-left" or not pb.isVisible():
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Map the preview box corners into LabSquare's local coordinate system.
        # QStackedWidget keeps every page at the same geometry as the stack
        # content rect, so this is valid even while the LAB pane is hidden.
        try:
            top_left = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().topLeft()))
            bottom_right = ls.mapFromGlobal(pb.mapToGlobal(pb.rect().bottomRight()))
        except Exception:
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Only avoid if there is horizontal overlap with LabSquare.
        if bottom_right.x() <= 0 or top_left.x() >= ls.width():
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        scale = self.cfg.get("uiScale", 100) / 100.0
        pad = int(4 * scale)
        new_avoid_top = max(0, bottom_right.y() + pad)
        if callable(getattr(ls, "set_avoid_top", None)):
            ls.set_avoid_top(new_avoid_top)
        ls.avoid_top = new_avoid_top

    def on_wheel_color_changed(self, r, g, b):
        # The wheel reports its own native space + values, so the unified
        # Color keeps the exact hue (no HSV→RGB→OKLCh round-trip drift).
        space, values = self.color_wheel.native_color_values()
        color = self.color_state.set_from(space, values)
        self._project_color(color, source="wheel")

    def on_lab_square_color_changed(self, r, g, b):
        space, values = self.lab_square.native_color_values()
        color = self.color_state.set_from(space, values)
        self._project_color(color, source="lab")

    def on_rgb_slider_changed(self):
        r = self.slider_widgets["R"][0].value()
        g = self.slider_widgets["G"][0].value()
        b = self.slider_widgets["B"][0].value()
        color = self.color_state.set_from("rgb", (r, g, b))
        self._project_color(color, source="sliders_rgb")

    def on_hsv_slider_changed(self):
        h = self.slider_widgets["H_hsv"][0].value()
        s = self.slider_widgets["S_hsv"][0].value()
        v = self.slider_widgets["V_hsv"][0].value()
        color = self.color_state.set_from("hsv", (h, s, v))
        self._project_color(color, source="sliders_hsv")

    def on_hsl_slider_changed(self):
        h = self.slider_widgets["H_hsl"][0].value()
        l = self.slider_widgets["L_hsl"][0].value()
        s = self.slider_widgets["S_hsl"][0].value()
        color = self.color_state.set_from("hls", (h, l, s))
        self._project_color(color, source="sliders_hsl")

    def on_lab_slider_changed(self):
        l_val = self.slider_widgets["L_lab"][0].value()
        a_val = self.slider_widgets["a_lab"][0].value()
        b_val = self.slider_widgets["b_lab"][0].value()
        color = self.color_state.set_from("lab", (l_val, a_val, b_val))
        self._project_color(color, source="sliders_lab")

    def on_oklab_slider_changed(self):
        l_raw = self.slider_widgets["L_oklab"][0].value()
        a_raw = self.slider_widgets["a_oklab"][0].value()
        b_raw = self.slider_widgets["b_oklab"][0].value()
        color = self.color_state.set_from("oklab", (l_raw / 100.0, a_raw / 100.0, b_raw / 100.0))
        self._project_color(color, source="sliders_oklab")
        self._deferred_dynamic_gradients_pending = True

    def on_oklch_slider_changed(self):
        # Absolute chroma: the C slider is a real OKLCh coordinate (0–0.4).
        # Gamut mapping (chroma reduction) happens inside Color.from_space, so
        # an out-of-gamut L/C pair clamps to the sRGB boundary automatically.
        sender = self.sender()
        L = self.slider_widgets["L_oklch"][0].value() / 100.0
        C = self.slider_widgets["C_oklch"][0].value() / _C_SCALE
        h = self.slider_widgets["h_oklch"][0].value()
        color = self.color_state.set_from("oklch", (L, C, h))

        if sender == self.slider_widgets["L_oklch"][0]:
            source = "sliders_oklch_L"
        elif sender == self.slider_widgets["C_oklch"][0]:
            source = "sliders_oklch_C"
        elif sender == self.slider_widgets["h_oklch"][0]:
            source = "sliders_oklch_h"
        else:
            source = "sliders_oklch"

        self._project_color(color, source=source)
        self._deferred_dynamic_gradients_pending = True

    def _find_oklch_max_chroma(self, L, h):
        """Binary search for max OKLCh chroma at given L, h within sRGB gamut."""
        from ui.color_conversions import find_max_oklch_c
        return find_max_oklch_c(L, h)

    def _update_oklch_slider_gradients(self):
        """更新 OKLCh 三个滑块的背景渐变（绝对色度）。

        - L 条: 固定 C 和 H，显示 L 从 0→100 的渐变
        - C 条: 固定 L 和 H，显示 C 从 0→max 的渐变
        - H 条: 固定 L 和 C，显示 H 从 0→360 的渐变
        """
        from ui.color_conversions import find_max_oklch_c as _fmc
        L_cur = self.slider_widgets["L_oklch"][0].value() / 100.0
        C_cur = self.slider_widgets["C_oklch"][0].value() / _C_SCALE
        h_cur = float(self.slider_widgets["h_oklch"][0].value())
        max_c = self._find_oklch_max_chroma(L_cur, h_cur)

        # L slider — fixed C/h, L varies 0→1 (each step gamut-maps C)
        c0 = min(C_cur, _fmc(0.0, h_cur))
        cm = min(C_cur, _fmc(0.5, h_cur))
        c1 = min(C_cur, _fmc(1.0, h_cur))
        okcl0_r, okcl0_g, okcl0_b = oklch_to_rgb(0.0, c0, h_cur)
        okcl_mid_r, okcl_mid_g, okcl_mid_b = oklch_to_rgb(0.5, cm, h_cur)
        okcl1_r, okcl1_g, okcl1_b = oklch_to_rgb(1.0, c1, h_cur)
        self.slider_widgets["L_oklch"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okcl0_r))), int(max(0, min(255, okcl0_g))), int(max(0, min(255, okcl0_b))))),
            (0.5, QColor(int(max(0, min(255, okcl_mid_r))), int(max(0, min(255, okcl_mid_g))), int(max(0, min(255, okcl_mid_b))))),
            (1.0, QColor(int(max(0, min(255, okcl1_r))), int(max(0, min(255, okcl1_g))), int(max(0, min(255, okcl1_b))))),
        ])

        # C slider — absolute chroma 0→0.4.  The sRGB gamut boundary sits at
        # max_c (0.08…0.32 depending on L/h), so the gradient fills only up to
        # that point and the out-of-gamut tail is grayed out — the slider
        # honestly shows where C would exceed sRGB.
        c_slider = self.slider_widgets["C_oklch"][0]
        c_slider_max = _C_SLIDER_MAX / _C_SCALE  # 0.4
        frac_max = max(0.0, min(1.0, max_c / c_slider_max))
        okcc0_r, okcc0_g, okcc0_b = oklch_to_rgb(L_cur, 0.0, h_cur)
        okcc1_r, okcc1_g, okcc1_b = oklch_to_rgb(L_cur, max_c, h_cur)
        c_slider.set_gradient([
            (0.0, QColor(int(max(0, min(255, okcc0_r))), int(max(0, min(255, okcc0_g))), int(max(0, min(255, okcc0_b))))),
            (frac_max, QColor(int(max(0, min(255, okcc1_r))), int(max(0, min(255, okcc1_g))), int(max(0, min(255, okcc1_b))))),
        ])
        c_slider.set_in_gamut_range(0, int(round(max_c * _C_SCALE)))

        # h slider — fixed L/C, h varies 0→360
        okch_stops = []
        for i in range(7):
            hue = i * 60
            r_h, g_h, b_h = oklch_to_rgb(L_cur, C_cur, hue)
            okch_stops.append((i / 6.0, QColor(int(max(0, min(255, r_h))), int(max(0, min(255, g_h))), int(max(0, min(255, b_h))))))
        self.slider_widgets["h_oklch"][0].set_gradient(okch_stops)

    def _update_oklab_slider_gradients(self):
        """Update OKLab slider groove gradients synchronously.

        Mirrors _update_oklch_slider_gradients so that OKLab sliders
        get their coloured bars at the same instant as OKLCh sliders
        rather than trailing by one ~16 ms deferred frame.
        """
        if "a_oklab" not in self.slider_widgets or "b_oklab" not in self.slider_widgets:
            return
        if not self.slider_containers.get("OKLab", QWidget()).isVisible():
            return

        # Derive chromaticity from the current RGB colour (full float
        # precision) so the synchronous gradient matches the deferred
        # update_slider_gradients path pixel-for-pixel.
        r, g, b = self.current_rgb
        _, a_val, b_val = rgb_to_oklab(r, g, b)
        L_cur = self.slider_widgets["L_oklab"][0].value() / 100.0

        # L_oklab — fixed a, b, L varies 0→1
        okl0_r, okl0_g, okl0_b = oklab_to_rgb(0.0, a_val, b_val)
        okl_mid_r, okl_mid_g, okl_mid_b = oklab_to_rgb(0.5, a_val, b_val)
        okl1_r, okl1_g, okl1_b = oklab_to_rgb(1.0, a_val, b_val)
        self.slider_widgets["L_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okl0_r))), int(max(0, min(255, okl0_g))), int(max(0, min(255, okl0_b))))),
            (0.5, QColor(int(max(0, min(255, okl_mid_r))), int(max(0, min(255, okl_mid_g))), int(max(0, min(255, okl_mid_b))))),
            (1.0, QColor(int(max(0, min(255, okl1_r))), int(max(0, min(255, okl1_g))), int(max(0, min(255, okl1_b))))),
        ])

        # a_oklab — fixed L, b, a varies -0.4→0.4
        oka0_r, oka0_g, oka0_b = oklab_to_rgb(L_cur, -0.4, b_val)
        oka1_r, oka1_g, oka1_b = oklab_to_rgb(L_cur, 0.4, b_val)
        self.slider_widgets["a_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, oka0_r))), int(max(0, min(255, oka0_g))), int(max(0, min(255, oka0_b))))),
            (1.0, QColor(int(max(0, min(255, oka1_r))), int(max(0, min(255, oka1_g))), int(max(0, min(255, oka1_b))))),
        ])

        # b_oklab — fixed L, a, b varies -0.4→0.4
        okb0_r, okb0_g, okb0_b = oklab_to_rgb(L_cur, a_val, -0.4)
        okb1_r, okb1_g, okb1_b = oklab_to_rgb(L_cur, a_val, 0.4)
        self.slider_widgets["b_oklab"][0].set_gradient([
            (0.0, QColor(int(max(0, min(255, okb0_r))), int(max(0, min(255, okb0_g))), int(max(0, min(255, okb0_b))))),
            (1.0, QColor(int(max(0, min(255, okb1_r))), int(max(0, min(255, okb1_g))), int(max(0, min(255, okb1_b))))),
        ])

    def _on_lab_lightness_changed(self, lightness):
        """Update LAB state and keep the existing low-quality drag path."""
        self.lab_square.set_lightness(
            lightness
        )

    def on_interaction_finished(self):
        self.color_wheel.schedule_slice_prewarm(350)
        if not self.lab_square.isVisible():
            self._schedule_lab_prerender(50)
        self.color_wheel.update()
        self.lab_square.update()
        r, g, b = self.current_rgb
        # On drag release, cancel any pending deferred render and run the
        # heavy visual work synchronously so the settled color's groove
        # gradients + gamut masks are immediately consistent (rather than
        # trailing by one frame). During the drag these were deferred so the
        # slider handle / wheel indicator could paint first and stay glued
        # to the cursor.
        self._deferred_color_timer.stop()
        self._deferred_color_pending = None
        self.update_slider_gradients(r, g, b)
        if self._deferred_dynamic_gradients_pending:
            self._update_oklab_slider_gradients()
            self._update_oklch_slider_gradients()
            self._deferred_dynamic_gradients_pending = False
        # An L-only release must keep the chromaticity snapshots from before
        # the drag. Recomputing them from the quantized RGB would turn a
        # chromatic OKLCh color into a different (often gray) mask on release.
        if getattr(self, "_last_update_source", "") != "sliders_oklch_L":
            _, a_ok_snap, b_ok_snap = rgb_to_oklab(r, g, b)
            self._gamut_oklab_a = a_ok_snap
            self._gamut_oklab_b = b_ok_snap
            _, a_lb_snap, b_lb_snap = rgb_to_lab(r, g, b)
            self._gamut_lab_a = a_lb_snap
            self._gamut_lab_b = b_lb_snap
        _, c_ok_snap, h_ok_snap = rgb_to_oklch(r, g, b)
        if self._source_space == "oklch" and self._source_values:
            c_ok_snap = self._source_values.get("C", c_ok_snap)
            h_ok_snap = self._source_values.get("h", h_ok_snap)
        self._gamut_oklch_C = c_ok_snap
        self._gamut_oklch_h = h_ok_snap
        self._update_all_L_gamut_ranges()
        # Record into history before pushing to drawing software so the
        # persisted state reflects *what the user just settled on*.
        self._record_color_history()
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            hsv_override = None
            if self.sync_thread.software_mode == 'companion':
                entry_h = self.slider_widgets.get("H_hsv")
                entry_s = self.slider_widgets.get("S_hsv")
                entry_v = self.slider_widgets.get("V_hsv")
                if entry_h and entry_s and entry_v:
                    MAX = 4294967295
                    hsv_override = (int(entry_h[0].value()/360*MAX), int(entry_s[0].value()/100*MAX), int(entry_v[0].value()/100*MAX))
            # Source-space sync for CSP memory mode (source is already
            # recorded by _project_color from the unified Color).
            src_sp, src_v = self._resolve_sync_source()
            color_index = 0 if self.active_slot == "fg" else 1
            self.sync_thread.write_color(r, g, b, hsv_u32=hsv_override,
                                         source_space=src_sp, source_values=src_v,
                                         color_index=color_index)

    def _schedule_lab_gamut_range(self, delay_ms: int = 50):
        """Coalesce the expensive LAB gamut-range refresh during fast toggles."""
        if not hasattr(self, "lab_slider_column") or not self.lab_slider_column.isVisible():
            return
        self._lab_gamut_timer.start(delay_ms)

    def _schedule_lab_prerender(self, delay_ms: int = 50):
        """Coalesce LAB preview warmups without stacking timers."""
        if self.lab_square.isVisible():
            return
        self._lab_prerender_timer.start(delay_ms)

    def _prerender_lab(self):
        """Background pre-render of the LAB visualizer."""
        if not self.lab_square.isVisible() and hasattr(self, "stack"):
            self.lab_square.resize(self.stack.size())
            r, g, b = self.current_rgb
            self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_square.prerender()

    def _is_slider_drag_active(self):
        """Return True while any channel slider is held by the user."""
        for slider, _ in getattr(self, "slider_widgets", {}).values():
            if slider.isSliderDown():
                return True
        return bool(getattr(getattr(self, "lab_slider", None), "dragging", False))

    def _schedule_deferred_color_updates(self, r, g, b):
        """Schedule the heavy visual-only rendering (slider groove gradients
        + L out-of-gamut masks) to run on the next idle event-loop iteration.

        Why: these computations are not safety-critical and only affect the
        colored bars behind the other sliders. Calling them synchronously
        inside every drag step blocks the GUI thread and delays the dragged
        widget's own paint, so the handle/indicator stops tracking the cursor.
        By deferring and coalescing (only one pending run is ever armed), the
        handle/indicator paints flush first and the cosmetics trail by at
        most ~16ms. Latest (r,g,b) wins if multiple moves arrive before the
        timer fires.
        """
        self._deferred_color_pending = (r, g, b)
        if not self._deferred_color_timer.isActive():
            self._deferred_color_timer.start()

    def _apply_deferred_color_updates(self):
        """Run the deferred visual-only work, then clear the pending slot.

        Safe to call directly (used by on_interaction_finished to flush the
        final state synchronously); clears the pending rgb regardless.
        """
        pending = self._deferred_color_pending
        self._deferred_color_pending = None
        if pending is None:
            return
        r, g, b = pending
        self.update_slider_gradients(r, g, b)
        if self._deferred_dynamic_gradients_pending:
            self._update_oklab_slider_gradients()
            self._update_oklch_slider_gradients()
            self._deferred_dynamic_gradients_pending = False
        self._update_all_L_gamut_ranges()

    def update_slider_gradients(self, r, g, b):
        h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
        h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        l_lab, a_lab, b_lab = rgb_to_lab(r, g, b)
        L_oklab, a_oklab, b_oklab = rgb_to_oklab(r, g, b)
        L_oklch, C_oklch, h_oklch = rgb_to_oklch(r, g, b)
        
        # 1) R Slider
        self.slider_widgets["R"][0].set_gradient([
            (0.0, QColor(0, g, b)),
            (1.0, QColor(255, g, b))
        ])
        
        # 2) G Slider
        self.slider_widgets["G"][0].set_gradient([
            (0.0, QColor(r, 0, b)),
            (1.0, QColor(r, 255, b))
        ])
        
        # 3) B Slider
        self.slider_widgets["B"][0].set_gradient([
            (0.0, QColor(r, g, 0)),
            (1.0, QColor(r, g, 255))
        ])
        
        # 4) H_hsv Slider
        hue_stops = [
            (0.0, QColor(255, 0, 0)),
            (0.17, QColor(255, 255, 0)),
            (0.33, QColor(0, 255, 0)),
            (0.5, QColor(0, 255, 255)),
            (0.67, QColor(0, 0, 255)),
            (0.83, QColor(255, 0, 255)),
            (1.0, QColor(255, 0, 0))
        ]
        self.slider_widgets["H_hsv"][0].set_gradient(hue_stops)
        
        # 5) S_hsv Slider
        r0, g0, b0 = hsv_to_rgb(h_hsv, 0.0, v_hsv)
        r1, g1, b1 = hsv_to_rgb(h_hsv, 100.0, v_hsv)
        self.slider_widgets["S_hsv"][0].set_gradient([
            (0.0, QColor(int(r0), int(g0), int(b0))),
            (1.0, QColor(int(r1), int(g1), int(b1)))
        ])
        
        # 6) V_hsv Slider
        rv0, gv0, bv0 = hsv_to_rgb(h_hsv, s_hsv, 0.0)
        rv1, gv1, bv1 = hsv_to_rgb(h_hsv, s_hsv, 100.0)
        self.slider_widgets["V_hsv"][0].set_gradient([
            (0.0, QColor(int(rv0), int(gv0), int(bv0))),
            (1.0, QColor(int(rv1), int(gv1), int(bv1)))
        ])
        
        # 7) H_hsl Slider
        self.slider_widgets["H_hsl"][0].set_gradient(hue_stops)
        
        # 8) L_hsl Slider
        rl0, gl0, bl0 = colorsys.hls_to_rgb(h_hsl, 0.0, s_hsl)
        rl05, gl05, bl05 = colorsys.hls_to_rgb(h_hsl, 0.5, s_hsl)
        rl1, gl1, bl1 = colorsys.hls_to_rgb(h_hsl, 1.0, s_hsl)
        self.slider_widgets["L_hsl"][0].set_gradient([
            (0.0, QColor(int(rl0 * 255), int(gl0 * 255), int(bl0 * 255))),
            (0.5, QColor(int(rl05 * 255), int(gl05 * 255), int(bl05 * 255))),
            (1.0, QColor(int(rl1 * 255), int(gl1 * 255), int(bl1 * 255)))
        ])
        
        # 9) S_hsl Slider
        rs0, gs0, bs0 = colorsys.hls_to_rgb(h_hsl, l_hsl, 0.0)
        rs1, gs1, bs1 = colorsys.hls_to_rgb(h_hsl, l_hsl, 1.0)
        self.slider_widgets["S_hsl"][0].set_gradient([
            (0.0, QColor(int(rs0 * 255), int(gs0 * 255), int(bs0 * 255))),
            (1.0, QColor(int(rs1 * 255), int(gs1 * 255), int(bs1 * 255)))
        ])
        
        # 10) L_lab Slider
        rlab0_r, rlab0_g, rlab0_b = lab_to_rgb(0, a_lab, b_lab)
        rlab1_r, rlab1_g, rlab1_b = lab_to_rgb(100, a_lab, b_lab)
        self.slider_widgets["L_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(rlab0_r))), max(0, min(255, int(rlab0_g))), max(0, min(255, int(rlab0_b))))),
            (1.0, QColor(max(0, min(255, int(rlab1_r))), max(0, min(255, int(rlab1_g))), max(0, min(255, int(rlab1_b)))))
        ])
        
        # 11) a_lab Slider
        alab0_r, alab0_g, alab0_b = lab_to_rgb(l_lab, -128, b_lab)
        alab1_r, alab1_g, alab1_b = lab_to_rgb(l_lab, 127, b_lab)
        self.slider_widgets["a_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(alab0_r))), max(0, min(255, int(alab0_g))), max(0, min(255, int(alab0_b))))),
            (1.0, QColor(max(0, min(255, int(alab1_r))), max(0, min(255, int(alab1_g))), max(0, min(255, int(alab1_b)))))
        ])
        
        # 12) b_lab Slider
        blab0_r, blab0_g, blab0_b = lab_to_rgb(l_lab, a_lab, -128)
        blab1_r, blab1_g, blab1_b = lab_to_rgb(l_lab, a_lab, 127)
        self.slider_widgets["b_lab"][0].set_gradient([
            (0.0, QColor(max(0, min(255, int(blab0_r))), max(0, min(255, int(blab0_g))), max(0, min(255, int(blab0_b))))),
            (1.0, QColor(max(0, min(255, int(blab1_r))), max(0, min(255, int(blab1_g))), max(0, min(255, int(blab1_b)))))
        ])

        # 13) L_oklab Slider (L from 0 to 1 mapped to slider 0-100)
        if self.slider_containers.get("OKLab", QWidget()).isVisible():
            okl0_r, okl0_g, okl0_b = oklab_to_rgb(0.0, a_oklab, b_oklab)
            okl_mid_r, okl_mid_g, okl_mid_b = oklab_to_rgb(0.5, a_oklab, b_oklab)
            okl1_r, okl1_g, okl1_b = oklab_to_rgb(1.0, a_oklab, b_oklab)
            self.slider_widgets["L_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okl0_r))), int(max(0, min(255, okl0_g))), int(max(0, min(255, okl0_b))))),
                (0.5, QColor(int(max(0, min(255, okl_mid_r))), int(max(0, min(255, okl_mid_g))), int(max(0, min(255, okl_mid_b))))),
                (1.0, QColor(int(max(0, min(255, okl1_r))), int(max(0, min(255, okl1_g))), int(max(0, min(255, okl1_b)))))
            ])

            # 14) a_oklab Slider (a from -0.4 to 0.4 mapped to slider -40..40)
            oka0_r, oka0_g, oka0_b = oklab_to_rgb(L_oklab, -0.4, b_oklab)
            oka1_r, oka1_g, oka1_b = oklab_to_rgb(L_oklab, 0.4, b_oklab)
            self.slider_widgets["a_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, oka0_r))), int(max(0, min(255, oka0_g))), int(max(0, min(255, oka0_b))))),
                (1.0, QColor(int(max(0, min(255, oka1_r))), int(max(0, min(255, oka1_g))), int(max(0, min(255, oka1_b)))))
            ])

            # 15) b_oklab Slider
            okb0_r, okb0_g, okb0_b = oklab_to_rgb(L_oklab, a_oklab, -0.4)
            okb1_r, okb1_g, okb1_b = oklab_to_rgb(L_oklab, a_oklab, 0.4)
            self.slider_widgets["b_oklab"][0].set_gradient([
                (0.0, QColor(int(max(0, min(255, okb0_r))), int(max(0, min(255, okb0_g))), int(max(0, min(255, okb0_b))))),
                (1.0, QColor(int(max(0, min(255, okb1_r))), int(max(0, min(255, okb1_g))), int(max(0, min(255, okb1_b)))))
            ])
        
        if self.slider_containers.get("OKLCh", QWidget()).isVisible():
            self._update_oklch_slider_gradients()

    # ── L-gamut range helpers for out-of-gamut slider marking ──

    def _compute_lab_L_gamut_range(self):
        """Return (min_L, max_L) for L_lab at the snapshot LAB chromaticity."""
        if "a_lab" not in self.slider_widgets or "b_lab" not in self.slider_widgets:
            return 0, 100
        a_fixed = getattr(self, '_gamut_lab_a', None)
        b_fixed = getattr(self, '_gamut_lab_b', None)
        if a_fixed is None or b_fixed is None:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = lab_to_rgb(L, a_fixed, b_fixed)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

    @staticmethod
    def _resolve_content_height(current_height, required_height,
                                last_auto_height, manual_override):
        required_height = max(1, int(required_height))
        current_height = int(current_height)
        if current_height < required_height:
            return required_height, False
        if (manual_override and last_auto_height is not None
                and required_height == int(last_auto_height)):
            return current_height, True
        return required_height, False

    @staticmethod
    def _required_content_height(title_height, stack_min_height, sliders_height,
                                 margins_top, margins_bottom, spacing):
        return max(
            240,
            int(title_height) + int(stack_min_height) + int(sliders_height)
            + int(margins_top) + int(margins_bottom) + 2 * int(spacing),
        )

    @staticmethod
    def _required_visualizer_height(window_width, margins_left, margins_right,
                                    stack_min_height):
        available_width = int(window_width) - int(margins_left) - int(margins_right)
        return max(available_width, int(stack_min_height))

    def _run_deferred_content_height(self):
        self._content_height_adjust_pending = False
        self._adjust_content_height()

    def _adjust_content_height(self):
        if getattr(self, "_adjusting_content_height", False):
            return
        if not self.isVisible():
            if not self._content_height_adjust_pending:
                self._content_height_adjust_pending = True
                self._content_height_timer.start(0)
            return
        self._content_height_adjust_pending = False
        required = 0
        try:
            self.sliders_layout.activate()
            self.main_layout.activate()
            margins = self.main_layout.contentsMargins()
            visualizer_h = self._required_visualizer_height(
                self.width(),
                margins.left(),
                margins.right(),
                max(
                    self.stack.minimumSizeHint().height(),
                    self.stack.minimumHeight(),
                ),
            )
            required = self._required_content_height(
                _visible_title_bar_height(self.title_bar),
                visualizer_h,
                self.sliders_container.sizeHint().height(),
                margins.top(), margins.bottom(), self.main_layout.spacing(),
            )
        except AttributeError:
            return

        self.setMinimumHeight(required)
        target, manual = self._resolve_content_height(
            self.height(), required, self._last_auto_height,
            self._manual_height_override,
        )
        self._last_auto_height = required
        self._manual_height_override = manual
        if target == self.height():
            return

        self._adjusting_content_height = True
        try:
            self.resize(self.width(), target)
        finally:
            self._adjusting_content_height = False

    @staticmethod
    def _compute_L_gamut_range(in_gamut):
        """Shared binary search: find [min_L, max_L] of in-gamut L values.

        Does NOT assume L=50 is in gamut — high-chroma colours near the
        gamut boundary can push mid-L out of gamut while low/high L
        remain valid.  Scans for any in-gamut reference point first,
        then searches outward in both directions from it.
        """
        # ── Find any in-gamut reference L ──
        ref_L = None
        for test_L in (0.0, 25.0, 50.0, 75.0, 100.0):
            if in_gamut(test_L):
                ref_L = test_L
                break
        if ref_L is None:
            return 0, 100  # colour is unreachable at this chromaticity

        # ── min_L: lowest in-gamut L ──
        if in_gamut(0.0):
            min_L = 0.0
        else:
            lo, hi = 0.0, ref_L
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    hi = mid
                else:
                    lo = mid
            min_L = hi

        # ── max_L: highest in-gamut L ──
        if in_gamut(100.0):
            max_L = 100.0
        else:
            lo, hi = ref_L, 100.0
            for _ in range(24):
                mid = (lo + hi) * 0.5
                if in_gamut(mid):
                    lo = mid
                else:
                    hi = mid
            max_L = lo

        return int(round(min_L)), int(round(max_L))

    def _compute_oklab_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklab at the snapshot OKLab chromaticity."""
        if "a_oklab" not in self.slider_widgets or "b_oklab" not in self.slider_widgets:
            return 0, 100
        a_fixed = getattr(self, '_gamut_oklab_a', None)
        b_fixed = getattr(self, '_gamut_oklab_b', None)
        if a_fixed is None or b_fixed is None:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = oklab_to_rgb(L / 100.0, a_fixed, b_fixed)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

    def _compute_oklch_L_gamut_range(self):
        """Return (min_L, max_L) for L_oklch at the snapshot chromaticity."""
        if "C_oklch" not in self.slider_widgets or "h_oklch" not in self.slider_widgets:
            return 0, 100
        if "L_oklch" not in self.slider_widgets:
            return 0, 100
        c_val = self._gamut_oklch_C
        h_val = self._gamut_oklch_h
        if c_val is None or h_val is None or c_val < 0.001:
            return 0, 100
        def in_gamut(L):
            rr, gg, bb = oklch_to_rgb(L / 100.0, c_val, h_val)
            return 0.0 <= rr <= 255.0 and 0.0 <= gg <= 255.0 and 0.0 <= bb <= 255.0
        return self._compute_L_gamut_range(in_gamut)

    def _update_lab_slider_gamut_range(self):
        """Update the vertical LabSlider's out-of-gamut L range
        based on the current LabSquare (a, b) and render mode."""
        if not hasattr(self, 'lab_square') or not hasattr(self, 'lab_slider'):
            return
        a_val = self.lab_square.a
        b_val = self.lab_square.b
        mode = self.lab_square.render_mode

        def in_gamut(L):
            if mode == "oklab":
                r, g, bv = oklab_to_rgb(L / 100.0, a_val, b_val)
            else:
                r, g, bv = lab_to_rgb(L, a_val, b_val)
            return 0.0 <= r <= 255.0 and 0.0 <= g <= 255.0 and 0.0 <= bv <= 255.0

        if not in_gamut(50.0):
            min_L, max_L = 0.0, 100.0
        else:
            if in_gamut(0.0):
                min_L = 0.0
            else:
                lo, hi = 0.0, 50.0
                for _ in range(24):
                    mid = (lo + hi) * 0.5
                    if in_gamut(mid):
                        hi = mid
                    else:
                        lo = mid
                min_L = hi
            if in_gamut(100.0):
                max_L = 100.0
            else:
                lo, hi = 50.0, 100.0
                for _ in range(24):
                    mid = (lo + hi) * 0.5
                    if in_gamut(mid):
                        lo = mid
                    else:
                        hi = mid
                max_L = lo
        self.lab_slider.set_in_gamut_range(min_L, max_L)

    def _update_all_L_gamut_ranges(self):
        """Update out-of-gamut visual marking on all L sliders."""
        if not hasattr(self, 'slider_widgets'):
            return
        # L_lab
        if "L_lab" in self.slider_widgets:
            mn, mx = self._compute_lab_L_gamut_range()
            self.slider_widgets["L_lab"][0].set_in_gamut_range(mn, mx)
        # L_oklab
        if "L_oklab" in self.slider_widgets:
            mn, mx = self._compute_oklab_L_gamut_range()
            self.slider_widgets["L_oklab"][0].set_in_gamut_range(mn, mx)
        # L_oklch
        if "L_oklch" in self.slider_widgets:
            mn, mx = self._compute_oklch_L_gamut_range()
            self.slider_widgets["L_oklch"][0].set_in_gamut_range(mn, mx)
        # Vertical LabSlider
        self._update_lab_slider_gamut_range()

    def _project_color(self, color: Color, source: str = "", hsv=None):
        """Project a unified :class:`Color` onto every widget (single fan-out).

        The Color already carries every space (computed exactly once by
        ui.color_model) plus its native source space, so this only fans the
        snapshot out to the UI and the sync backend.  It records the source
        space/values and delegates to :meth:`update_ui_colors`, which consumes
        the precomputed hsv/oklch/oklab hints without any RGB round-trip.

        *hsv* optionally overrides the H/S/V used for display — used by the
        companion sync read-back to honour CSP's reported hue/saturation
        through grayscale/black (where RGB carries no hue/sat info).
        """
        self._source_space = color.source_space
        self._source_values = self._color_source_dict(color)
        self.update_ui_colors(color.r, color.g, color.b, source=source,
                              hsv=color.hsv if hsv is None else hsv,
                              oklch=color.oklch, oklab=color.oklab)

    def _color_source_dict(self, color: Color):
        """Convert a Color's mapped source coordinates into the dict form the
        sync/history layers expect (keyed by the channel names per space)."""
        names = getattr(self, "_SOURCE_CHANNELS", {}).get(color.source_space)
        if not names:
            return None
        coords = color.to(color.source_space)
        return {ch: float(v) for ch, v in zip(names, coords)}

    def _color_from_source(self, space, values_dict, fallback_rgb):
        """Rebuild a Color from a persisted source (space + channel dict).

        Falls back to an RGB-derived Color when the saved source is missing
        or malformed (e.g. a legacy entry without source info).
        """
        names = getattr(self, "_SOURCE_CHANNELS", {}).get(space)
        if names and values_dict:
            try:
                return Color.from_space(space, tuple(float(values_dict[ch]) for ch in names))
            except (KeyError, TypeError, ValueError):
                pass
        return Color.from_rgb(*fallback_rgb)

    def update_ui_colors(self, r, g, b, source="", hsv=None, oklch=None, oklab=None):
        self._last_update_source = source
        self.current_rgb = (r, g, b)
        color = QColor(r, g, b)

        # User picked a new real color (wheel/slider/picker/history/CSP
        # read-back) → clear the transparent state on the active slot.
        # init/slot_change/swap are internal state transitions that must
        # NOT clear it (swap already exchanged the flags).
        if source not in ("init", "slot_change", "swap"):
            if self.active_slot == "fg":
                self._fg_transparent = False
            else:
                self._bg_transparent = False
        self.preview_box.set_transparent("fg", self._fg_transparent)
        self.preview_box.set_transparent("bg", self._bg_transparent)

        # 1) Sync swatches based on active slot
        if self.active_slot == "fg":
            self.preview_box.fg_color = color
        else:
            self.preview_box.bg_color = color
        self.preview_box.update_slot_borders(self.active_slot)

        # Persist source to the active slot (skip for slot_change/swap — those
        # restore, not overwrite, the per-slot source).
        if source not in ("slot_change", "swap") and self._source_values:
            if self.active_slot == "fg":
                self._fg_source_space = self._source_space
                self._fg_source_values = self._source_values
            else:
                self._bg_source_space = self._source_space
                self._bg_source_values = self._source_values

        # 2) Sync Color Wheel (Only if visible or during init)
        if source == "init" or (source != "wheel" and self.color_wheel.isVisible()):
            if hsv is not None:
                self.color_wheel.set_hsv(hsv[0], hsv[1], hsv[2])
            else:
                self.color_wheel.set_color(r, g, b, block_signals=True)
            # Push direct OKLCh state so the indicator avoids HSV→RGB→OKLCh drift
            if self.color_wheel.wheel_mode == "oklch-slice":
                if oklch is not None:
                    L_ok, C_ok, h_ok = oklch
                else:
                    L_ok, C_ok, h_ok = rgb_to_oklch(r, g, b)
                self.color_wheel.set_oklch(L_ok, C_ok, h_ok)

        # 3) Sync LAB Square / Slider (Only if visible or during init)
        if source == "init" or (source != "lab" and self.lab_square.isVisible()):
            if oklab is not None and self.lab_square.render_mode == "oklab":
                L_ok, a_ok, b_ok = oklab
                self.lab_square.set_oklab(L_ok, a_ok, b_ok, block_signals=True)
            else:
                self.lab_square.set_color(r, g, b, block_signals=True)
            self.lab_slider.set_lightness(
                self.lab_square.L
            )

        # 4) Sync Sliders
        # Block signals for all sliders during sync
        all_chans = ["R", "G", "B", "H_hsv", "S_hsv", "V_hsv", "H_hsl", "L_hsl", "S_hsl", "L_lab", "a_lab", "b_lab", "L_oklab", "a_oklab", "b_oklab", "L_oklch", "C_oklch", "h_oklch"]
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(True)
            
        # RGB Values
        if source != "sliders_rgb":
            self.slider_widgets["R"][0].setValue(r)
            self.slider_widgets["G"][0].setValue(g)
            self.slider_widgets["B"][0].setValue(b)
        
        # HSV Values
        if source != "sliders_hsv":
            if source == "wheel":
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
            elif hsv is not None:
                h_hsv, s_hsv, v_hsv = hsv
            else:
                h_hsv, s_hsv, v_hsv = rgb_to_hsv(r, g, b)
            self.slider_widgets["S_hsv"][0].setValue(round(s_hsv))
            self.slider_widgets["V_hsv"][0].setValue(round(v_hsv))
            if s_hsv >= 0.5:
                self.slider_widgets["H_hsv"][0].setValue(round(h_hsv))
        
        # HSL Values
        if source != "sliders_hsl":
            if source == "wheel":
                h_hsl, l_hsl, s_hsl = hsv_to_hls_floats(self.color_wheel.h, self.color_wheel.s, self.color_wheel.v)
                self.slider_widgets["H_hsl"][0].setValue(round(h_hsl * 360.0))
            else:
                h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
                h_deg = hsv[0] if hsv is not None else h_hsl * 360.0  # Reuse handler's locked hue
                self.slider_widgets["H_hsl"][0].setValue(round(h_deg))
            self.slider_widgets["L_hsl"][0].setValue(round(l_hsl * 100.0))
            self.slider_widgets["S_hsl"][0].setValue(round(s_hsl * 100.0))
        
        # LAB Values
        if source != "sliders_lab":
            if source == "wheel":
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
                r_f, g_f, b_f = colorsys.hsv_to_rgb(h_hsv / 360.0, s_hsv / 100.0, v_hsv / 100.0)
                l_lab, a_lab, b_lab = rgb_to_lab(r_f * 255.0, g_f * 255.0, b_f * 255.0)
            else:
                l_lab, a_lab, b_lab = rgb_to_lab(r, g, b)
            self.slider_widgets["L_lab"][0].setValue(round(l_lab))
            self.slider_widgets["a_lab"][0].setValue(round(a_lab))
            self.slider_widgets["b_lab"][0].setValue(round(b_lab))
        
        # OKLab Values
        if source != "sliders_oklab":
            L_ok, a_ok, b_ok = rgb_to_oklab(r, g, b)
            if "L_oklab" in self.slider_widgets:
                self.slider_widgets["L_oklab"][0].setValue(round(L_ok * 100))
            self.slider_widgets["a_oklab"][0].setValue(round(a_ok * 100))
            self.slider_widgets["b_oklab"][0].setValue(round(b_ok * 100))
        
        # OKLCh Values (absolute chroma; the Color already carries the
        # gamut-mapped chroma and the remembered hue, so no re-derivation)
        if source not in ("sliders_oklch_L", "sliders_oklch_C", "sliders_oklch_h"):
            L_okc, C_okc, h_okc = oklch if oklch is not None else rgb_to_oklch(r, g, b)
            self.slider_widgets["L_oklch"][0].setValue(round(L_okc * 100))
            self.slider_widgets["h_oklch"][0].setValue(round(h_okc))
            self.slider_widgets["C_oklch"][0].setValue(round(C_okc * _C_SCALE))
        
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(False)

        # Update labels and gradient stylesheets
        for chan in all_chans:
            if chan in self.slider_widgets:
                val = self.slider_widgets[chan][0].value()
                if chan == "C_oklch":
                    # Absolute chroma label (0.001 resolution).
                    self.slider_widgets[chan][1].setText(f"{val / _C_SCALE:.3f}")
                else:
                    self.slider_widgets[chan][1].setText(str(val))
            
        # ── Gamut-range chromaticity snapshots ─────
        # L-only drags keep all snapshots unchanged until release.
        if source != "sliders_oklch_L":
            _, a_ok, b_ok = rgb_to_oklab(r, g, b)
            self._gamut_oklab_a = a_ok
            self._gamut_oklab_b = b_ok
            _, a_lb, b_lb = rgb_to_lab(r, g, b)
            self._gamut_lab_a = a_lb
            self._gamut_lab_b = b_lb
            if oklch is not None:
                _, c_ok, h_ok = oklch
            else:
                _, c_ok, h_ok = rgb_to_oklch(r, g, b)
            self._gamut_oklch_C = c_ok
            self._gamut_oklch_h = h_ok

        # Heavy visual-only cosmetics (slider groove gradients + L out-of-gamut
        # masks) are deferred + coalesced so they never block the dragged
        # widget's paint. This is what keeps every slider handle and the color
        # wheel indicator perfectly following the cursor on every mouse move;
        # the colored groove bars / grayed gamut regions trail by ≤~16ms.
        self._schedule_deferred_color_updates(r, g, b)

        # 5) Push to drawing software — delegated to SyncMixin so the god
        # class no longer owns the companion/memory write path.
        self._push_color_to_sync(r, g, b, source, hsv)

    def resizeEvent(self, event):
        """Handle resize, preventing DPI-induced size drift when dragged between monitors.

        When a frameless window is dragged between screens with different DPI scaling,
        Qt may fire resize events as it recalculates device-independent pixels. Without
        intervention, the title-bar height change in apply_theme() + layout recalculation
        creates a feedback loop that causes progressive size drift with each cross-screen drag.
        """
        current_screen = self.screen()
        if current_screen is not None:
            current_dpr = current_screen.devicePixelRatio()
        else:
            current_dpr = 1.0
        
        # Detect DPI change (screen switch with different scaling)
        dpi_changed = (self._last_dpr is not None and 
                       current_dpr is not None and 
                       abs(current_dpr - self._last_dpr) > 0.01)
        
        if dpi_changed and self._dpi_locked_size is None:
            # First resize event after DPI change: lock the intended logical size.
            # We use oldSize (the size BEFORE Qt's DPI adjustment) to compute the
            # correct logical size for the new DPR.
            old_size = event.oldSize()
            if old_size.isValid() and old_size.width() > 100 and old_size.height() > 100:
                old_dpr = self._last_dpr
                new_dpr = current_dpr
                # Preserve physical pixel dimensions: convert old logical → physical → new logical
                phys_w = old_size.width() * old_dpr
                phys_h = old_size.height() * old_dpr
                target_w = max(200, min(1200, int(phys_w / new_dpr)))
                target_h = max(300, min(1600, int(phys_h / new_dpr)))
                
                new_size = event.size()
                if abs(target_w - new_size.width()) > 3 or abs(target_h - new_size.height()) > 3:
                    # Qt adjusted the size; override to maintain physical consistency
                    self._dpi_locked_size = (target_w, target_h)
                    self.resize(target_w, target_h)
                    self._last_dpr = current_dpr
                    return  # self.resize() will fire another resizeEvent
        
        # Clear DPI lock after the stabilizing resize
        if self._dpi_locked_size is not None:
            locked_w, locked_h = self._dpi_locked_size
            new_size = event.size()
            if abs(locked_w - new_size.width()) <= 3 and abs(locked_h - new_size.height()) <= 3:
                self._dpi_locked_size = None
        
        self._last_dpr = current_dpr
        
        super().resizeEvent(event)
        self.update_geometries()

    def moveEvent(self, event):
        """Block window movement when lockWindowPosition is enabled."""
        if self.cfg.get("lockWindowPosition", False):
            event.ignore()
            return
        super().moveEvent(event)

    def update_geometries(self):
        # Dimensions
        w = self.width()
        h = self.height()
        dynamic_scale = self.cfg.get("uiScale", 100) / 100.0
        
        # Apply scaling and updates
        self.apply_theme(scale=dynamic_scale, is_resize_event=True)
        
        title_h = _visible_title_bar_height(self.title_bar)
        title_offset = _title_bar_content_offset(self.title_bar, self.main_layout)
        sliders_h = self.sliders_container.sizeHint().height()
        margins = self.main_layout.contentsMargins()
        
        # Calculate visualizer wheel size from the width, but never taller
        # than the visualizer pane: a short/wide window (manual resize)
        # shrinks the wheel instead of clipping its lower arc.  Mirrors the
        # clamp in ColorWheel.get_wheel_geometry().
        spacing = int(4 * dynamic_scale)
        pane_h = h - margins.top() - margins.bottom() - title_h - sliders_h - 2 * spacing
        wheel_size = min(w - int(16 * dynamic_scale), max(16, pane_h - 6))
        
        # ── Step 1: legacy preview sizing ALWAYS runs first ──
        # This restores legacy circle sizing/position when ringless is disabled,
        # and provides a baseline that ringless may override below.
        self.preview_box.resize_and_position(wheel_size, title_offset, h, sliders_h, self.active_slot)
        self.preview_box.raise_()

        # ── Step 2: push DPI-scaled button metrics down; panes do the rest ──
        btn_size = int(28 * dynamic_scale)
        btn_margin = int(6 * dynamic_scale)
        if hasattr(self, 'pane_wheel'):
            self.pane_wheel.set_mode_button_metrics(btn_size, btn_margin)
        if hasattr(self, 'pane_lab'):
            self.pane_lab.set_mode_button_metrics(btn_size, btn_margin)

        # ── Step 3: ringless layout sync ──
        # On wheel page: applies rectangle sizing over the legacy baseline.
        # On LAB page: same rectangle controls + top bar, with layout margin.
        # Do NOT call _adjust_content_height() from resize-driven sync.
        self._sync_ringless_mode(wheel_size=wheel_size, title_bar_height=title_offset)
        self.color_wheel.schedule_slice_prewarm(500)

        # ── Step 4: LAB avoidance observes FINAL preview geometry ──
        # Must run AFTER ringless sync so it sees the page-appropriate
        # (ringless rectangles or restored legacy circles) size/position.
        # In ringless mode the guard inside _update_lab_avoid skips legacy
        # avoidance because the LAB layout margin owns the top spacing.
        self._update_lab_avoid()

        # ── Step 5: settings window positioning (independent window) ──
        if hasattr(self, 'settings_window') and self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.position_near_main_window()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.cfg.get("lockWindowSize", False):
                event.accept()
                return
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            new_w = int(self.width() * factor)
            new_h = int(self.height() * factor)
            new_w = max(180, min(1200, new_w))
            new_h = max(240, min(1600, new_h))
            self.resize(new_w, new_h)
            event.accept()
        else:
            super().wheelEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        try:
            import win32api
            import win32con
            is_down = win32api.GetKeyState(win32con.VK_LBUTTON) < 0
        except Exception:
            is_down = True
            
        if not is_down:
            is_slider_down = False
            if hasattr(self, 'slider_widgets'):
                for chan, (slider, _) in self.slider_widgets.items():
                    if slider.isSliderDown():
                        slider.setDown(False)
                        is_slider_down = True
            
            wheel_dragging = hasattr(self, 'color_wheel') and self.color_wheel.dragging
            lab_dragging = hasattr(self, 'lab_square') and self.lab_square.dragging
            
            if is_slider_down or wheel_dragging or lab_dragging:
                if wheel_dragging:
                    try:
                        self.color_wheel.mouseReleaseEvent(None)
                    except Exception:
                        pass
                if lab_dragging:
                    try:
                        self.lab_square.mouseReleaseEvent(None)
                    except Exception:
                        pass
                self.on_interaction_finished()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.cfg.get("lockWindowSize", False):
            pos = event.position()
            direction = self.get_resize_direction(pos)
            if direction:
                self.resizing = True
                self.resize_dir = direction
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geometry = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "resizing", False):
            start_pos = self.resize_start_pos
            geom = self.resize_start_geometry
            if start_pos is None or geom is None:
                return
            delta = event.globalPosition().toPoint() - start_pos
            new_geom = QRect(geom)

            min_w = 200
            min_h = 300

            resize_dir = self.resize_dir
            if resize_dir is None:
                return

            if "right" in resize_dir:
                new_w = max(min_w, geom.width() + delta.x())
                new_geom.setWidth(new_w)
            elif "left" in resize_dir:
                new_w = max(min_w, geom.width() - delta.x())
                new_geom.setLeft(geom.right() - new_w)

            if "bottom" in resize_dir:
                new_h = max(min_h, geom.height() + delta.y())
                new_geom.setHeight(new_h)

            self.setGeometry(new_geom)
            event.accept()
            return
        self._sync_resize_cursor(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_resizing = self.resizing
        self.resizing = False
        self.resize_dir = None
        self.unsetCursor()
        # Only save window geometry on actual manual resize, not on every mouse-up
        # (prevents saving DPI-corrupted sizes from cross-screen drags)
        if was_resizing:
            cfg = config.load_window_config()
            cfg["width"] = self.width()
            cfg["height"] = self.height()
            config.save_window_config(cfg)
            self._manual_height_override = True
            self._last_auto_height = self.height()
        
        super().mouseReleaseEvent(event)

    def _is_lab_toggle_zone(self, global_pos: QPoint | None = None) -> bool:
        """True when the given global point (or the system cursor) is inside
        the visible picker pane.

        Covers the color wheel (the ring AND every position inside it) plus
        the LAB visualizer pane, so the local shortcut toggles in both
        directions without moving the mouse.
        """
        try:
            if global_pos is None:
                global_pos = QCursor.pos()
            if self.stack.currentIndex() == 0 and self.color_wheel.isVisible():
                pos = self.color_wheel.mapFromGlobal(global_pos)
                return self.color_wheel.rect().contains(pos)
            if self.stack.currentIndex() == 1 and self.pane_lab.isVisible():
                pos = self.pane_lab.mapFromGlobal(global_pos)
                return self.pane_lab.rect().contains(pos)
        except Exception:
            pass
        return False

    def _consume_lab_toggle_press(self) -> bool:
        """Single-fire guard for the local mouse/pen toggle shortcut.

        A pen button press is delivered twice — once as a QTabletEvent and
        once as a synthetic QMouseEvent — so only the first of the pair may
        toggle. Returns True when this press should toggle, False when it is
        the duplicate twin (caller swallows it without toggling).
        """
        now = time.monotonic()
        if now - self._last_lab_toggle_ts < 0.06:
            return False
        self._last_lab_toggle_ts = now
        return True

    def _maybe_handle_lab_toggle_key(self, event) -> bool:
        """Handle the configured local LAB-toggle shortcut.

        Toggles wheel/LAB when the cursor is over the picker pane. When the
        cursor is elsewhere the key is still consumed (unless a text field
        has focus), so the toggle key can never re-activate a previously
        focused button/checkbox — clicking ☰ once used to make Space toggle
        the settings panel.

        Only covers key events Qt delivers to this app (i.e. a Colorink
        window has focus). Without focus — 无焦点选色模式, drawing in the
        painting app — the system-wide hook registered in
        ``update_hotkey_bindings`` takes over (see ``on_hotkey_triggered``).

        Returns True when the event was consumed, False to pass it through
        (non-matching key, or a text field has focus). The event filter
        skips this entirely while a settings hotkey capture is active.
        """
        pressed = parse_key_event(event)
        if not pressed:
            return False
        expected = str(self.cfg.get("toggleLabKey", "")).lower().replace(" ", "")
        if pressed.lower().replace(" ", "") != expected:
            return False
        # Never steal the key from a text field (e.g. the Companion URL input).
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return False
        if self._is_lab_toggle_zone():
            self.toggle_picker_mode()
        return True

    def _maybe_handle_lab_toggle_mouse(self, event) -> bool:
        """Toggle wheel/LAB view when the configured local shortcut is a
        mouse button pressed over the picker pane.

        Mouse events follow the cursor, not keyboard focus, so this path
        works even in 无焦点选色模式 while the painting app holds focus.
        """
        if not self._is_lab_toggle_zone(event.globalPosition().toPoint()):
            return False
        name = MOUSE_BUTTON_NAME_BY_QT.get(event.button())
        if not name:
            return False
        expected = str(self.cfg.get("toggleLabKey", "")).lower().replace(" ", "")
        if name.lower() != expected:
            return False
        # A pen press also arrives as a synthetic QMouseEvent — swallow the
        # twin when the tablet event already toggled.
        if not self._consume_lab_toggle_press():
            return True
        self.toggle_picker_mode()
        return True

    def _maybe_handle_lab_toggle_tablet(self, event) -> bool:
        """Toggle wheel/LAB view when a pen (tablet) button press matches the
        configured local shortcut.

        Pen side-buttons configured as right-click arrive as QTabletEvent
        (TabletPress) — and, depending on the driver, as a synthetic
        QMouseEvent too. Handling the tablet event directly makes pen
        buttons work exactly like mouse buttons, with the pair deduplicated
        by ``_consume_lab_toggle_press``.
        """
        if not self._is_lab_toggle_zone(event.globalPosition().toPoint()):
            return False
        name = MOUSE_BUTTON_NAME_BY_QT.get(event.button())
        if not name:
            return False
        expected = str(self.cfg.get("toggleLabKey", "")).lower().replace(" ", "")
        if name.lower() != expected:
            return False
        if not self._consume_lab_toggle_press():
            return True
        self.toggle_picker_mode()
        return True

    def eventFilter(self, watched, event):
        try:
            # Local LAB-toggle shortcuts (keyboard or mouse button) fire while
            # the cursor is over the color wheel / LAB pane. Skipped while a
            # settings hotkey capture is active so the recorded press wins.
            if not capture_active():
                if event.type() == QEvent.Type.KeyPress and self._maybe_handle_lab_toggle_key(event):
                    return True
                if event.type() == QEvent.Type.MouseButtonPress and self._maybe_handle_lab_toggle_mouse(event):
                    return True
                # Pen (tablet) button presses — e.g. a pen side-button bound
                # to right-click — arrive as tablet events. Same handling as
                # mouse buttons; the synthetic mouse twin is deduplicated.
                if event.type() == QEvent.Type.TabletPress and self._maybe_handle_lab_toggle_tablet(event):
                    return True
            # Keep the resize cursor in sync even over widgets that do not
            # enable mouse tracking. MouseMove alone can miss those areas and
            # leave a stale size cursor after leaving the 8px border zone.
            if (
                event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.Leave)
                and isinstance(watched, QWidget)
                and self.window() == watched.window()
            ):
                if not getattr(self, "resizing", False):
                    if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter):
                        self._sync_resize_cursor(event.globalPosition().toPoint())
                    else:
                        self.unsetCursor()
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _sync_resize_cursor(self, global_pos=None):
        if self.cfg.get("lockWindowSize", False):
            self.unsetCursor()
            return

        if global_pos is None:
            global_pos = QCursor.pos()
        pos_in_main = self.mapFromGlobal(global_pos)
        direction = self.get_resize_direction(pos_in_main)

        target = Qt.CursorShape.ArrowCursor
        if direction == "left" or direction == "right":
            target = Qt.CursorShape.SizeHorCursor
        elif direction == "bottom":
            target = Qt.CursorShape.SizeVerCursor
        elif direction == "bottom-left":
            target = Qt.CursorShape.SizeBDiagCursor
        elif direction == "bottom-right":
            target = Qt.CursorShape.SizeFDiagCursor

        if self.cursor().shape() != target:
            if target == Qt.CursorShape.ArrowCursor:
                self.unsetCursor()
            else:
                self.setCursor(target)

    def get_resize_direction(self, pos):
        w = self.width()
        h = self.height()
        border = 8
        
        x = pos.x()
        y = pos.y()

        if x < 0 or y < 0 or x >= w or y >= h:
            return None
        
        is_left = x <= border
        is_right = x >= w - border
        is_bottom = y >= h - border
        
        if is_left and is_bottom:
            return "bottom-left"
        elif is_right and is_bottom:
            return "bottom-right"
        elif is_left:
            return "left"
        elif is_right:
            return "right"
        elif is_bottom:
            return "bottom"
        return None

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

    def _show_settings_window(self):
        """Ensure the settings window exists and is shown (no-op if already up)."""
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if not self.settings_window.isVisible():
            self.settings_sidebar.refresh_ui()
            self.settings_window.show_near_main_window()

    def toggle_settings_sidebar(self):
        # Lazy-create the independent settings window on first use
        if not hasattr(self, 'settings_window') or self.settings_window is None:
            from ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow(self, self.settings_sidebar)
        if self.settings_window.isVisible():
            self.settings_window.hide()
        else:
            self._show_settings_window()
        self.update_no_focus_policies()

    def _schedule_module_layout_refresh(self):
        """Coalesce slider reordering and geometry work during rapid module clicks."""
        self._module_layout_refresh_pending = True
        if not self._module_layout_timer.isActive():
            self._module_layout_timer.start(0)

    def _flush_module_layout_refresh(self):
        if not self._module_layout_refresh_pending:
            return
        self._module_layout_refresh_pending = False
        self.refresh_slider_visibility_and_order()

    def _schedule_module_config_save(self):
        """Batch config writes so a click burst performs one disk write."""
        self._module_save_pending = True
        self._module_save_timer.start(120)

    def _flush_module_config_save(self):
        if not self._module_save_pending:
            return
        self._module_save_pending = False
        config.save_hotkey_config(self.cfg)

    def _apply_module(self, module_name: str):
        """Apply a color-space module without blocking the click handler."""
        if module_name not in _MODULE_DEFS:
            module_name = "hsv"
        self._current_module = module_name
        self.cfg["colorSpaceModule"] = module_name
        # Persist and reflow on coalesced timers: rapid clicks update the wheel
        # and button immediately, while one final layout pass handles the burst.
        self._schedule_module_config_save()
        # Update wheel mode
        wheel_mode = _MODULE_DEFS[module_name]["wheel"]
        self.color_wheel.set_wheel_mode(wheel_mode)
        self.color_wheel.schedule_slice_prewarm(350)
        self._schedule_module_layout_refresh()
        # Update module button label
        self._update_module_button_label()
        # Notify sidebar if it's open
        if hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible():
            self.settings_sidebar.notify_module_changed()

    def _next_module(self):
        """Cycle to the next module in _MODULE_ORDER."""
        try:
            idx = _MODULE_ORDER.index(self._current_module)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(_MODULE_ORDER)
        self._apply_module(_MODULE_ORDER[next_idx])

    def refresh_slider_visibility_and_order(self):
        # Remove all from layout
        for group in config.SLIDER_GROUPS:
            self.sliders_layout.removeWidget(self.slider_containers[group])

        # Display order comes from the same shared helper the settings UI
        # uses, so reordering there always matches this layout.
        groups = config.sorted_slider_groups(self.cfg)

        # Module-aware filtering: only the current module's slider set is
        # eligible; force-hide everything outside it.
        module_key = getattr(self, "_current_module", "hsv")
        allowed = set(_MODULE_DEFS.get(module_key, _MODULE_DEFS["hsv"])["sliders"])

        for g in groups:
            if g == "History":
                visible = self.cfg.get("showSlidersHistory", True)
            elif g not in allowed:
                visible = False  # force-hide: not in this module's set
            else:
                visible = self.cfg.get(f"showSliders{g}", True)
            self.slider_containers[g].setVisible(visible)
            self.sliders_layout.addWidget(self.slider_containers[g])

        # Recalculate layout geometries since height changed
        self.update_geometries()
        self._adjust_content_height()

    def zoom_ui(self, factor):
        self.resize(int(320 * factor), int(710 * factor))
        self._adjust_content_height()

    def show_window_at_cursor(self):
        if self.cfg.get("lockWindowPosition", False):
            self.show()
            return
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        
        # Center the window around the cursor
        w, h = self.width(), self.height()
        x = cursor_pos.x() - w // 2
        y = cursor_pos.y() - h // 2
        
        # Keep window inside the available screen geometry
        x = max(geom.x(), min(x, geom.x() + geom.width() - w))
        y = max(geom.y(), min(y, geom.y() + geom.height() - h))
        
        self.move(x, y)
        self.show()

    def init_foreground_tracker(self):
        from PyQt6.QtCore import QTimer
        self.foreground_timer = QTimer(self)
        self.foreground_timer.setInterval(400)
        self.foreground_timer.timeout.connect(self.check_foreground_window)
        # Only poll while the feature is enabled; on_settings_saved() starts
        # or stops the timer when the setting changes.
        if self.cfg.get("onlyShowInCsp", False):
            self.foreground_timer.start()
            self.check_foreground_window()

    def check_foreground_window(self):
        # If settings onlyShowInCsp is False, do nothing
        if not self.cfg.get("onlyShowInCsp", False):
            return

        try:
            import win32gui
            import win32process
        except ImportError:
            return

        hwnd = win32gui.GetForegroundWindow()
        is_drawing_active = False
        pid = 0

        if hwnd:
            try:
                title = (win32gui.GetWindowText(hwnd) or "").lower()
            except Exception:
                title = ""

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pass

            if pid:
                # Cache the resolved exe per PID so an unchanged foreground
                # window doesn't re-query the process on every tick.
                if getattr(self, "_fg_exe_cache_pid", None) == pid:
                    exe_name = getattr(self, "_fg_exe_cache", "")
                else:
                    exe_name = _resolve_process_exe(pid)
                    self._fg_exe_cache_pid = pid
                    self._fg_exe_cache = exe_name
                if _exe_matches_drawing_app(exe_name):
                    is_drawing_active = True

            # Title fallback covers localized windows and cases where the
            # process query was denied.
            if not is_drawing_active and _title_matches_drawing_app(title):
                is_drawing_active = True

        # A foreground window owned by this process (main window, settings
        # window or picker overlay) means the user is interacting with us.
        # The REAL foreground PID (win32) is the source of truth here:
        # Qt's isActiveWindow() bookkeeping is unreliable — in no-focus mode
        # the palette can never become "active", and the separate settings
        # window's activation state can get stuck (activateWindow() denied
        # by the OS foreground lock leaves Qt thinking it is active forever).
        # Trusting that stale state kept the palette visible even when a
        # non-drawing app took the foreground ("仅在画图软件前台时显示"失效).
        is_our_focused = bool(pid and pid == os.getpid())

        should_be_visible = is_drawing_active or is_our_focused

        # Keep the window up during an active color pick
        picker = getattr(self, "picker_overlay", None)
        if picker is not None and picker.is_active:
            should_be_visible = True

        # If follow_mouse_active is enabled and the window is visible, avoid auto-hiding it.
        # But when the user explicitly restricted visibility to the drawing app's
        # foreground (onlyShowInCsp), that restriction wins — otherwise the palette
        # would never hide while following the mouse ("切走不隐藏").
        if (getattr(self, "follow_mouse_active", False) and self.isVisible()
                and not self.cfg.get("onlyShowInCsp", False)):
            should_be_visible = True

        if should_be_visible:
            if not self.isVisible():
                self.show()
                self.raise_()
            self.auto_hidden = False
        else:
            if self.isVisible():
                self.hide()
                self.auto_hidden = True

    def on_settings_saved(self):
        # Reload configs
        self.cfg = config.load_hotkey_config()
        if hasattr(self, "picker_overlay"):
            self.picker_overlay.set_zoom(self.cfg.get("pickerZoom", 6))
        self.update_hotkey_bindings()
        if hasattr(self, "title_bar"):
            self.title_bar.setVisible(self.cfg.get("showTitleBar", True))
        if hasattr(self, "tray_title_action"):
            self.tray_title_action.setChecked(self.cfg.get("showTitleBar", True))

        # Update grayscale controller and migrate all removed backends to
        # native. Mag remains the system-wide Luma fallback.
        new_backend = self.cfg.get("grayscaleFilterBackend", "native")
        new_backend = "mag" if new_backend == "mag" else "native"
        new_mode = self.cfg.get("grayscaleFilterMode", "oklch")
        if new_mode not in ("oklch", "luma"):
            new_mode = "oklch"
        current_backend = (
            "mag" if type(self.grayscale_overlay).__name__ == "MagFilterController"
            else "native"
        )
        if new_backend != current_backend:
            self.grayscale_overlay.set_active(False)
            close_fn = getattr(self.grayscale_overlay, "close", None)
            if callable(close_fn):
                close_fn()
            if new_backend == "mag":
                from core.mag_grayscale import MagFilterController
                self.grayscale_overlay = MagFilterController(mode="luma")
            else:
                from core.native_grayscale import NativeGrayscaleController
                self.grayscale_overlay = NativeGrayscaleController(mode=new_mode)
        screen_target = self.cfg.get("grayscaleFilterScreen", "all")
        self.grayscale_overlay.set_target(screen_target)
        self.grayscale_overlay.set_mode(
            "luma" if new_backend == "mag" else new_mode
        )

        # Update window flags dynamically
        self.update_window_flags()
        self.update_no_focus_policies()

        # Keep the foreground tracker running only while the feature is on,
        # and apply the new state immediately instead of waiting a tick.
        fg_timer = getattr(self, "foreground_timer", None)
        if self.cfg.get("onlyShowInCsp", False):
            if fg_timer is not None and not fg_timer.isActive():
                fg_timer.start()
            self.check_foreground_window()
        else:
            if fg_timer is not None:
                fg_timer.stop()

        # Restore visibility if onlyShowInCsp is turned off while auto_hidden
        if not self.cfg.get("onlyShowInCsp", False):
            if getattr(self, "auto_hidden", False):
                self.show()
                self.auto_hidden = False
        
        # Update active software mode in thread
        mode = self.cfg.get("syncSoftware", "csp")
        if mode not in ("csp", "sai", "udm", "ps", "companion"):
            mode = "csp"
        self.sync_thread.set_software_mode(mode)
        
        # Companion mode: show setup dialog if no saved session
        if mode == "companion":
            c = self.sync_thread.companion_sync
            if not c._connected and not c._has_session():
                from PyQt6.QtCore import QTimer as _Qt
                _Qt.singleShot(300, lambda: self._setup_companion_connection())

        # Update settings dialog variables in thread
        self.sync_thread.csp_version = self.cfg.get("cspVersion", "auto")
        self.sync_thread.sai2_version = self.cfg.get("sai2Version", "auto")
        self.sync_thread.udm_version = self.cfg.get("udmVersion", "auto")
        setattr(self.sync_thread, "ps_version", self.cfg.get("psVersion", "auto"))
        self.sync_thread.update_versions()
        
        # Update follow mouse state
        self.follow_mouse_active = self.cfg.get("followMouseEnabled", False)
        
        # Apply color-space module (overrides legacy colorWheelMode/wheelMode)
        module = self.cfg.get("colorSpaceModule", self._current_module)
        if module != self._current_module:
            self._apply_module(module)
        else:
            # Even if module didn't change, re-apply slider visibility in case
            # individual toggles were changed
            self.refresh_slider_visibility_and_order()

        self.color_wheel.reload_config()

        # Update lab visualizer mode
        viz_mode = self.cfg.get("visualizerMode", "lab")
        if hasattr(self, 'lab_square'):
            self.lab_square.set_render_mode(viz_mode)
            self.cfg["labVisualizerMaxVal"] = 110 if viz_mode == "lab" else 0.4

        # Update module button visibility
        if hasattr(self, 'btn_module'):
            show_btn = self.cfg.get("showModuleSwitchButton", True)
            self.btn_module.setVisible(show_btn)
            self._update_module_button_label()

        self.preview_box.position_mode = self.cfg.get("previewBoxPosition", "top-left")
        self.apply_theme()
        
        # Apply scaling zoom factor only if the target scale configuration has changed
        target_scale = self.cfg.get("uiScale", 100)
        if getattr(self, "current_ui_scale", 100) != target_scale:
            self.zoom_ui(target_scale / 100.0)
            self.current_ui_scale = target_scale
        else:
            self.update()
        # Reapply ringless layout after config/settings reload.
        # Mode OFF restores full ring/circles/bottom-right immediately.
        self._sync_ringless_mode()
        self._adjust_content_height()

    def update_window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if not self.cfg.get("showTaskbarIcon", False):
            flags |= Qt.WindowType.Tool
            
        # Only apply no-focus mode if settings sidebar is CLOSED
        no_focus = self.cfg.get("noFocusMode", False) and not (hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible())
        if no_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
            
        if self.windowFlags() != flags:
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
                
        # Double safety: Force WS_EX_NOACTIVATE via Win32 API
        if no_focus:
            try:
                import win32con
                import win32gui
                hwnd = int(self.winId())
                ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if not (ex_style & win32con.WS_EX_NOACTIVATE):
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_NOACTIVATE)
            except Exception:
                pass

    def update_no_focus_policies(self):
        is_settings_open = hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible()
        enabled = self.cfg.get("noFocusMode", False) and not is_settings_open
        
        policy = Qt.FocusPolicy.NoFocus if enabled else Qt.FocusPolicy.StrongFocus
        
        self.setFocusPolicy(policy)
        if hasattr(self, 'color_wheel'):
            self.color_wheel.setFocusPolicy(policy)
        if hasattr(self, 'lab_square'):
            self.lab_square.setFocusPolicy(policy)
        if hasattr(self, 'lab_slider'):
            self.lab_slider.setFocusPolicy(policy)
        if hasattr(self, 'preview_box'):
            self.preview_box.setFocusPolicy(policy)
        
        if hasattr(self, 'slider_widgets'):
            for chan, (slider, val_label) in self.slider_widgets.items():
                slider.setFocusPolicy(policy)
            
        if hasattr(self, 'title_bar'):
            for btn in [self.title_bar.btn_settings, self.title_bar.btn_close, self.title_bar.btn_min]:
                btn.setFocusPolicy(policy)
