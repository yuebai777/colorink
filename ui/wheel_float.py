"""Floating colour-map page: wheel or LAB view, plus picker decorations.

The page is the same wheel module that lives in the docked stack. When it is
torn off, the foreground/background swatches, the transparent tile and the
mode button travel with it. A floating page can also switch itself to the LAB
visualizer; that switch is local to this page/window, never global.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui import preview_clearance
from ui.color_preview_box import ColorPreviewBox
from ui.lab_visualizer import LabSlider, LabSquare
from ui.ringless_mode import RINGLESS_ACTIVE_BORDER
from ui.widgets.picker_icon_button import PickerIconButton
from ui.window_layout import resolve_picker_geometry


class FloatingWheelPage(QWidget):
    """A floating wheel/LAB page with its own copy of the picker decorations."""

    #: Expand inside a panel frame / tab page instead of sitting on a bottom
    #: stretch at its size hint.
    fill_holder = True
    is_floating_wheel_page = True

    def __init__(self, panel, owner, parent=None, panel_id: str | None = None):
        super().__init__(parent)
        self.panel = panel
        self.owner = owner
        self.panel_id = panel_id or f"wheel.{getattr(panel, 'key', 'hsv')}"
        self.view_mode = "wheel"
        self.show_swatches = True
        self.show_buttons = True
        self.follow_global_lab = bool(
            self._option("follow_global_lab", False))
        self.lab_enabled = bool(self._option("lab_enabled", True))
        self.lab_square: LabSquare | None = None
        self.lab_slider: LabSlider | None = None
        self.lab_host: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self.stack = QStackedWidget(self)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        box.addWidget(self.stack, 1)

        self.wheel_host = QWidget(self.stack)
        wheel_box = QVBoxLayout(self.wheel_host)
        wheel_box.setContentsMargins(0, 0, 0, 0)
        wheel_box.setSpacing(0)
        wheel_box.addWidget(panel)
        self.stack.addWidget(self.wheel_host)
        panel.show()

        cfg = getattr(owner, "cfg", {}) or {}
        session = getattr(owner, "color_session", None)
        self.preview = ColorPreviewBox(self)
        if session is not None:
            self.preview._color_session = session
            panel.wheel._color_session = session
        self.preview.position_mode = cfg.get("previewBoxPosition", "top-left")
        self.preview.set_theme_colors(
            RINGLESS_ACTIVE_BORDER,
            getattr(getattr(owner, "_floating_chrome", None), "border_color", "")
            or "#888888")
        self.preview.show()

        self.mode_btn = PickerIconButton("☉", self, icon_kind="mode:lab")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setToolTip("切换本浮窗的色轮 / LAB 视图")
        self.mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_btn.clicked.connect(self.toggle_view)
        self.mode_btn.show()

        self.sync_from_owner()
        self.apply_visibility_defaults()
        saved_view = self._option("view", "wheel")
        if saved_view == "lab" and not self.lab_enabled:
            saved_view = "wheel"
        self.set_view_mode(saved_view)
        self.apply_chrome(getattr(owner, "_floating_chrome", None))

    # ── per-page options ─────────────────────────────────────────────────
    def _option(self, key: str, default):
        cfg = getattr(self.owner, "cfg", {}) or {}
        options = cfg.get("floatingWheelPageOptions") or {}
        page = options.get(self.panel_id) or {}
        return page.get(key, default)

    def has_option(self, key: str) -> bool:
        cfg = getattr(self.owner, "cfg", {}) or {}
        options = cfg.get("floatingWheelPageOptions") or {}
        return key in (options.get(self.panel_id) or {})

    def _remember(self, key: str, value) -> None:
        cfg = getattr(self.owner, "cfg", None)
        if cfg is None:
            return
        options = dict(cfg.get("floatingWheelPageOptions") or {})
        page = dict(options.get(self.panel_id) or {})
        page[key] = value
        options[self.panel_id] = page
        cfg["floatingWheelPageOptions"] = options
        save = getattr(self.owner, "_schedule_module_config_save", None)
        if callable(save):
            save()
        else:
            try:
                from core import config as _config
                _config.save_hotkey_config(cfg)
            except Exception:
                pass

    # ── page sizing / overlays ───────────────────────────────────────────
    def sizeHint(self):
        if self.view_mode == "lab" and self.lab_square is not None:
            return self.lab_square.sizeHint()
        return self.panel.sizeHint()

    def minimumSizeHint(self):
        if self.lab_square is not None and self.view_mode == "lab":
            return self.lab_square.minimumSizeHint()
        return self.panel.minimumSizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_overlays()

    def _align_lab_slider(self) -> None:
        if self.lab_square is None or self.lab_slider is None:
            return
        try:
            _gap, _x, y, size = self.lab_square.plane_geometry()
            if size > 0:
                self.lab_slider.set_track_band(y, size)
        except Exception:
            pass

    def _on_lightness(self, lightness: float) -> None:
        if self.lab_slider is not None:
            self.lab_slider.set_lightness(lightness)
        if self.lab_square is not None:
            self.lab_square.set_lightness(lightness)

    def _place_overlays(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        page_w = max(1, self.stack.width())
        page_h = max(1, self.stack.height())
        wheel_size = min(page_w or width, page_h or height)
        self.preview.resize_and_position(
            wheel_size, 0, height, 0,
            getattr(self.owner, "active_slot", "fg"))
        if self.show_swatches:
            try:
                circles = []
                if self.view_mode == "wheel":
                    picker = resolve_picker_geometry(page_w or width, page_h or height)
                    if picker.circle.radius > 0:
                        circles.append((picker.circle.x, picker.circle.y, picker.circle.radius))
                elif (self.lab_square is not None
                      and getattr(self.lab_square, "shape", "") == "disc"
                      and self.lab_square.width() > 0):
                    dcx, dcy, radius = self.lab_square._disc_metrics()
                    if radius > 0:
                        circles.append((dcx, dcy, radius))
                if circles:
                    bounds = (0, 0, page_w or width, height)
                    preview_clearance.avoid_circles(self.preview, circles, bounds)
            except Exception:
                pass
        cfg = getattr(self.owner, "cfg", None) or {}
        scale = float(cfg.get("uiScale", 100)) / 100.0
        btn = max(18, int(28 * scale))
        margin = max(4, int(6 * scale))
        self.mode_btn.setFixedSize(btn, btn)
        btn_x = width - btn - margin
        if (self.view_mode == "lab"
                and self.lab_slider is not None
                and self.lab_slider.isVisible()):
            btn_x = max(margin, width - self.lab_slider.width() - btn - margin)
        self.mode_btn.move(btn_x, height - btn - margin)
        self._align_lab_slider()
        self.preview.raise_()
        self.mode_btn.raise_()

    # ── view mode ────────────────────────────────────────────────────────
    def _ensure_lab(self) -> None:
        if self.lab_square is not None:
            return
        self.lab_host = QWidget(self.stack)
        lab_box = QHBoxLayout(self.lab_host)
        lab_box.setContentsMargins(0, 0, 0, 0)
        lab_box.setSpacing(0)
        lab = LabSquare(self.lab_host)
        lab.setSizePolicy(QSizePolicy.Policy.Expanding,
                          QSizePolicy.Policy.Expanding)
        session = getattr(self.owner, "color_session", None)
        if session is not None:
            lab._color_session = session
        lab_box.addWidget(lab, 1)

        cfg = getattr(self.owner, "cfg", {}) or {}
        scale = float(cfg.get("uiScale", 100)) / 100.0
        slider = LabSlider(self.lab_host)
        slider.setFixedWidth(max(14, int(18 * scale)))
        slider.lightnessChanged.connect(self._on_lightness)
        if session is not None:
            slider.interactionStarted.connect(session.begin_interaction)
            slider.interactionFinished.connect(session.end_interaction)
        finished = getattr(self.owner, "on_interaction_finished", None)
        if callable(finished):
            slider.interactionFinished.connect(finished)
        lab_box.addWidget(slider)
        self.lab_slider = slider
        lab.planeGeometryChanged.connect(self._align_lab_slider)

        self.stack.addWidget(self.lab_host)
        self.lab_square = lab
        self.sync_lab_settings()
        self._sync_lab_from_owner()
        changed = getattr(self.owner, "on_floating_lab_color_changed", None)
        if callable(changed):
            lab.colorChanged.connect(
                lambda r, g, b, square=lab: changed(square, r, g, b))
        if callable(finished):
            lab.interactionFinished.connect(finished)

    def set_lab_enabled(self, enabled: bool, remember: bool = False) -> None:
        self.lab_enabled = bool(enabled)
        if self.lab_enabled:
            if self.show_buttons:
                self.mode_btn.show()
            self.mode_btn.setToolTip(
                "切换为色轮" if self.view_mode == "lab"
                else "切换为 LAB 图（仅本浮窗）")
        else:
            if self.view_mode == "lab":
                self.set_view_mode("wheel", remember=remember)
            self.mode_btn.hide()
        if remember:
            self._remember("lab_enabled", self.lab_enabled)

    def set_follow_global_lab(self, enabled: bool,
                              remember: bool = False) -> None:
        self.follow_global_lab = bool(enabled)
        if remember:
            self._remember("follow_global_lab", self.follow_global_lab)

    def set_view_mode(self, mode: str, remember: bool = False) -> None:
        mode = "lab" if str(mode).lower() == "lab" else "wheel"
        if mode == "lab" and not self.lab_enabled:
            mode = "wheel"
        if mode == "lab":
            self._ensure_lab()
        self.view_mode = mode
        target = self.lab_host if mode == "lab" else self.wheel_host
        if target is not None:
            self.stack.setCurrentWidget(target)
        lab = mode == "lab"
        self.mode_btn.set_icon_kind("mode:wheel" if lab else "mode:lab")
        self.mode_btn.setToolTip(
            "切换为色轮" if lab else "切换为 LAB 图（仅本浮窗）")
        self.mode_btn.setVisible(self.show_buttons and self.lab_enabled)
        if remember:
            self._remember("view", mode)
        self._place_overlays()
        window = self.window()
        adjust = getattr(window, "adjust_size_for_content", None)
        if callable(adjust):
            try:
                adjust()
            except RuntimeError:
                pass

    def toggle_view(self) -> None:
        if self.view_mode != "lab" and not self.lab_enabled:
            return
        self.set_view_mode("wheel" if self.view_mode == "lab" else "lab",
                           remember=True)

    def sync_lab_settings(self) -> None:
        lab = self.lab_square
        if lab is None:
            return
        main = getattr(self.owner, "lab_square", None)
        cfg = getattr(self.owner, "cfg", {}) or {}
        render_mode = getattr(main, "render_mode", None) or cfg.get(
            "visualizerMode", "lab")
        shape = getattr(main, "shape", None) or cfg.get(
            "labViewShape", "square")
        harmony = getattr(main, "harmony_mode", None) or cfg.get(
            "labHarmonyMode", "analogous")
        lab.set_render_mode(render_mode)
        lab.set_shape(shape)
        lab.set_harmony_mode(harmony)
        main_slider = getattr(self.owner, "lab_slider", None)
        if self.lab_slider is not None and main_slider is not None:
            self.lab_slider.set_in_gamut_range(
                getattr(main_slider, "_gamut_min", 0.0),
                getattr(main_slider, "_gamut_max", 100.0))

    def _sync_lab_from_owner(self) -> None:
        lab = self.lab_square
        if lab is None:
            return
        color = getattr(getattr(self.owner, "color_state", None), "current", None)
        if color is None:
            return
        if lab.render_mode == "oklab":
            L, a, b = color.oklab
            lab.set_oklab(L, a, b, block_signals=True)
        else:
            r, g, b = color.rgb_float
            lab.set_color(r, g, b, block_signals=True)
        if self.lab_slider is not None:
            self.lab_slider.set_lightness(lab.L)

    # ── decorations ──────────────────────────────────────────────────────
    def sync_from_owner(self) -> None:
        """Mirror the main preview's colours, slot and position mode."""
        source = getattr(self.owner, "preview_box", None)
        cfg = getattr(self.owner, "cfg", {}) or {}
        self.preview.position_mode = cfg.get("previewBoxPosition", "top-left")
        if source is not None:
            self.preview.fg_color = QColor(source.fg_color)
            self.preview.bg_color = QColor(source.bg_color)
            self.preview.fg_transparent = bool(source.fg_transparent)
            self.preview.bg_transparent = bool(source.bg_transparent)
            self.preview.active_slot = source.active_slot
            self.preview.set_theme_colors(
                source.active_border_color, source.inactive_border_color)
        self.preview.update()
        self._place_overlays()

    def apply_visibility_defaults(self) -> None:
        cfg = getattr(self.owner, "cfg", {}) or {}
        swatches = self._option(
            "swatches", bool(cfg.get("floatingWheelShowSwatches", True)))
        buttons = self._option(
            "buttons", bool(cfg.get("floatingWheelShowButtons", True))
            and bool(cfg.get("showLabToggleButton", True)))
        self.set_show_swatches(bool(swatches))
        self.set_show_buttons(bool(buttons))

    def set_show_swatches(self, visible: bool, remember: bool = False) -> None:
        self.show_swatches = bool(visible)
        self.preview.setVisible(self.show_swatches)
        if remember:
            self._remember("swatches", self.show_swatches)
        if self.show_swatches:
            self._place_overlays()

    def set_show_buttons(self, visible: bool, remember: bool = False) -> None:
        self.show_buttons = bool(visible)
        self.mode_btn.setVisible(self.show_buttons and self.lab_enabled)
        if remember:
            self._remember("buttons", self.show_buttons)
        if self.show_buttons:
            self._place_overlays()

    def apply_chrome(self, chrome) -> None:
        if chrome is None:
            return
        self.preview.set_theme_colors(RINGLESS_ACTIVE_BORDER, chrome.border_color)
        scale = float(getattr(chrome, "scale", 1.0) or 1.0)
        ink = QColor(chrome.text)
        btn_fg = f"rgba({ink.red()}, {ink.green()}, {ink.blue()}, 115)"
        self.mode_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {chrome.bar_bg};
                border: 1px solid {chrome.border_color};
                border-radius: {int(4 * scale)}px;
                color: {btn_fg};
                font-size: {int(13 * scale)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {chrome.background};
                border-color: #5a94e2;
            }}
        """)
        self._place_overlays()

    def take_panel(self):
        """Hand the wheel page back before this wrapper is discarded."""
        for host in (self.wheel_host, self.lab_host):
            if host is None:
                continue
            box = host.layout()
            if box is not None:
                box.removeWidget(self.panel)
        self.panel.setParent(None)
        return self.panel
