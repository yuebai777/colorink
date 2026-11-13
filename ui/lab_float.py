"""A standalone floating LAB visualizer.

The main LAB page can be torn off like a wheel page. It owns its own plane
(so the docked LAB view keeps working) and shares the central colour state.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from ui import preview_clearance
from ui.color_preview_box import ColorPreviewBox
from ui.lab_visualizer import LabSlider, LabSquare
from ui.panels.registry import LAB_FLOAT_ID
from ui.ringless_mode import RINGLESS_ACTIVE_BORDER


class FloatingLabPage(QWidget):
    """Independent LAB square for a floating window."""

    fill_holder = True
    is_floating_lab_page = True

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.panel_id = LAB_FLOAT_ID
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self.lab_square = LabSquare(self)
        self.lab_square.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
        session = getattr(owner, "color_session", None)
        if session is not None:
            self.lab_square._color_session = session
        box.addWidget(self.lab_square, 1)

        cfg = getattr(owner, "cfg", {}) or {}
        scale = float(cfg.get("uiScale", 100)) / 100.0
        self.lab_slider = LabSlider(self)
        self.lab_slider.setFixedWidth(max(14, int(18 * scale)))
        self.lab_slider.lightnessChanged.connect(self._on_lightness)
        if session is not None:
            self.lab_slider.interactionStarted.connect(session.begin_interaction)
            self.lab_slider.interactionFinished.connect(session.end_interaction)
        finished = getattr(owner, "on_interaction_finished", None)
        if callable(finished):
            self.lab_slider.interactionFinished.connect(finished)
        box.addWidget(self.lab_slider)
        self.lab_square.planeGeometryChanged.connect(self._align_lab_slider)
        self.sync_lab_settings()
        self._sync_lab_from_owner()
        changed = getattr(owner, "on_floating_lab_color_changed", None)
        if callable(changed):
            self.lab_square.colorChanged.connect(
                lambda r, g, b: changed(self.lab_square, r, g, b))
        finished = getattr(owner, "on_interaction_finished", None)
        if callable(finished):
            self.lab_square.interactionFinished.connect(finished)

        self.preview = ColorPreviewBox(self)
        if session is not None:
            self.preview._color_session = session
        cfg = getattr(owner, "cfg", {}) or {}
        self.preview.position_mode = cfg.get("previewBoxPosition", "top-left")
        self.preview.set_theme_colors(
            RINGLESS_ACTIVE_BORDER,
            getattr(getattr(owner, "_floating_chrome", None), "border_color", "")
            or "#888888")
        self.show_swatches = True
        self.preview.show()
        self.sync_from_owner()
        self.apply_chrome(getattr(owner, "_floating_chrome", None))

    def sizeHint(self):
        return self.lab_square.sizeHint()

    def minimumSizeHint(self):
        return self.lab_square.minimumSizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_overlays()

    def _align_lab_slider(self) -> None:
        try:
            _gap, _x, y, size = self.lab_square.plane_geometry()
            if size > 0:
                self.lab_slider.set_track_band(y, size)
        except Exception:
            pass

    def _place_overlays(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        plane = min(max(1, self.lab_square.width()), max(1, self.lab_square.height()))
        self.preview.resize_and_position(
            plane, 0, height, 0,
            getattr(self.owner, "active_slot", "fg"))
        if (self.show_swatches
                and getattr(self.lab_square, "shape", "") == "disc"
                and self.lab_square.width() > 0):
            try:
                dcx, dcy, radius = self.lab_square._disc_metrics()
                if radius > 0:
                    bounds = (0, 0, max(1, self.lab_square.width()), height)
                    preview_clearance.avoid_circles(
                        self.preview, [(dcx, dcy, radius)], bounds)
            except Exception:
                pass
        self._align_lab_slider()
        self.preview.raise_()

    def sync_lab_settings(self) -> None:
        main = getattr(self.owner, "lab_square", None)
        cfg = getattr(self.owner, "cfg", {}) or {}
        self.lab_square.set_render_mode(
            getattr(main, "render_mode", None) or cfg.get("visualizerMode", "lab"))
        self.lab_square.set_shape(
            getattr(main, "shape", None) or cfg.get("labViewShape", "square"))
        self.lab_square.set_harmony_mode(
            getattr(main, "harmony_mode", None) or cfg.get("labHarmonyMode", "analogous"))
        main_slider = getattr(self.owner, "lab_slider", None)
        if main_slider is not None:
            self.lab_slider.set_in_gamut_range(
                getattr(main_slider, "_gamut_min", 0.0),
                getattr(main_slider, "_gamut_max", 100.0))

    def _on_lightness(self, lightness) -> None:
        self.lab_slider.set_lightness(lightness)
        self.lab_square.set_lightness(lightness)

    def _sync_lab_from_owner(self) -> None:
        color = getattr(getattr(self.owner, "color_state", None), "current", None)
        if color is None:
            return
        if self.lab_square.render_mode == "oklab":
            L, a, b = color.oklab
            self.lab_square.set_oklab(L, a, b, block_signals=True)
        else:
            r, g, b = color.rgb_float
            self.lab_square.set_color(r, g, b, block_signals=True)
        self.lab_slider.set_lightness(self.lab_square.L)

    def sync_from_owner(self) -> None:
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

    def set_show_swatches(self, visible: bool) -> None:
        self.show_swatches = bool(visible)
        self.preview.setVisible(self.show_swatches)
        if self.show_swatches:
            self._place_overlays()

    def apply_chrome(self, chrome) -> None:
        if chrome is None:
            return
        self.preview.set_theme_colors(RINGLESS_ACTIVE_BORDER, chrome.border_color)
        self._place_overlays()
