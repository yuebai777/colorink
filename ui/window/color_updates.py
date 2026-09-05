"""Colour projection, slider updates and gradient/LAB-gamut refresh.

Extracted from ``ui.main_window``: everything that fans a unified Color out to
the sliders/wheel/LAB visualizer and keeps the slider grooves and gamut masks
in sync.
"""

import colorsys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget

from ui.color_conversions import (
    hsv_to_hls_floats,
    lab_to_rgb,
    oklab_to_rgb,
    oklch_to_rgb,
    rgb_to_lab,
    rgb_to_oklab,
    rgb_to_oklch,
    rgb_to_vhsv,
    vhsv_to_rgb,
)
from ui.color_history import ColorHistoryWidget
from ui.color_model import Color
from ui.color_wheel import hsv_to_rgb, rgb_to_hsv
from ui.widgets import GradientSlider, SliderValueLabel
from ui.window.module_defs import _C_SCALE, _C_SLIDER_MAX


class ColorUpdatesMixin:

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
        
        # VHSV
        self.slider_containers["VHSV"] = QWidget()
        vhsv_lay = QVBoxLayout(self.slider_containers["VHSV"])
        vhsv_lay.setContentsMargins(0, 0, 0, 0)
        vhsv_lay.setSpacing(same_space_base)
        self.create_group_sliders("VHSV", ["H_vhsv", "S_vhsv", "V_vhsv"], vhsv_lay)
        self.sliders_layout.addWidget(self.slider_containers["VHSV"])
        
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
        history_lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
            "hsv": ("h", "s", "v"), "vhsv": ("h", "s", "v"), "hls": ("h", "l", "s"),
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
            elif chan in ("S_hsv", "V_hsv", "S_vhsv", "V_vhsv", "L_hsl", "S_hsl", "L_lab"):
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
            # Shared session: the wheel / LAB plane ask it whether a drag is
            # in flight, instead of scanning this dict from the window.
            session = getattr(self, "color_session", None)
            if session is not None:
                slider.sliderPressed.connect(session.begin_interaction)
                slider.sliderReleased.connect(session.end_interaction)
            if group == "RGB":
                slider.valueChanged.connect(self.on_rgb_slider_changed)
            elif group == "HSV":
                slider.valueChanged.connect(self.on_hsv_slider_changed)
            elif group == "VHSV":
                slider.valueChanged.connect(self.on_vhsv_slider_changed)
            elif group == "HSL":
                slider.valueChanged.connect(self.on_hsl_slider_changed)
            elif group == "LAB":
                slider.valueChanged.connect(self.on_lab_slider_changed)
            elif group == "OKLab":
                slider.valueChanged.connect(self.on_oklab_slider_changed)
            elif group == "OKLCh":
                slider.valueChanged.connect(self.on_oklch_slider_changed)

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

    def on_vhsv_slider_changed(self):
        h = self.slider_widgets["H_vhsv"][0].value()
        s = self.slider_widgets["S_vhsv"][0].value()
        v = self.slider_widgets["V_vhsv"][0].value()
        color = self.color_state.set_from("vhsv", (h, s, v))
        self._project_color(color, source="sliders_vhsv")

    def on_hsl_slider_changed(self):
        h = self.slider_widgets["H_hsl"][0].value()
        l = self.slider_widgets["L_hsl"][0].value()
        s = self.slider_widgets["S_hsl"][0].value()
        color = self.color_state.set_from("hls", (h, l, s))
        self._project_color(color, source="sliders_hsl")

    def on_lab_slider_changed(self):
        sender = self.sender()
        l_val = self.slider_widgets["L_lab"][0].value()
        a_val = self.slider_widgets["a_lab"][0].value()
        b_val = self.slider_widgets["b_lab"][0].value()
        color = self.color_state.set_from("lab", (l_val, a_val, b_val))
        source = "sliders_lab_L" if sender == self.slider_widgets.get("L_lab", (None,))[0] else "sliders_lab"
        self._project_color(color, source=source)

    def on_oklab_slider_changed(self):
        sender = self.sender()
        l_raw = self.slider_widgets["L_oklab"][0].value()
        a_raw = self.slider_widgets["a_oklab"][0].value()
        b_raw = self.slider_widgets["b_oklab"][0].value()
        color = self.color_state.set_from("oklab", (l_raw / 100.0, a_raw / 100.0, b_raw / 100.0))
        source = "sliders_oklab_L" if sender == self.slider_widgets.get("L_oklab", (None,))[0] else "sliders_oklab"
        self._project_color(color, source=source)
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
        rf, gf, bf = getattr(self, "_current_rgb_float", (float(r), float(g), float(b)))
        self.update_slider_gradients(rf, gf, bf)
        if self._deferred_dynamic_gradients_pending:
            self._update_oklab_slider_gradients()
            self._update_oklch_slider_gradients()
            self._deferred_dynamic_gradients_pending = False
        # An L-only release must keep the chromaticity snapshots from before
        # the drag. Recomputing them from the quantized RGB would turn a
        # chromatic OKLCh color into a different (often gray) mask on release.
        if hasattr(self, "color_state") and self.color_state.current is not None:
            cur = self.color_state.current
            _, a_ok_snap, b_ok_snap = cur.oklab
            _, a_lb_snap, b_lb_snap = cur.lab
            _, c_ok_snap, h_ok_snap = cur.oklch
        else:
            _, a_ok_snap, b_ok_snap = rgb_to_oklab(rf, gf, bf)
            _, a_lb_snap, b_lb_snap = rgb_to_lab(rf, gf, bf)
            _, c_ok_snap, h_ok_snap = rgb_to_oklch(rf, gf, bf)
        self._gamut_oklab_a = a_ok_snap
        self._gamut_oklab_b = b_ok_snap
        self._gamut_lab_a = a_lb_snap
        self._gamut_lab_b = b_lb_snap
        self._gamut_oklch_C = c_ok_snap
        self._gamut_oklch_h = h_ok_snap
        if self._source_space == "oklch" and self._source_values:
            self._gamut_oklch_C = self._source_values.get("C", self._gamut_oklch_C)
            self._gamut_oklch_h = self._source_values.get("h", self._gamut_oklch_h)
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

    def _schedule_deferred_color_updates(self, r, g, b, rgb_float=None):
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
        if rgb_float is None:
            rgb_float = (float(r), float(g), float(b))
        self._deferred_color_pending = (r, g, b, rgb_float)
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
        if len(pending) == 4:
            r, g, b, rgb_float = pending
        else:
            r, g, b = pending
            rgb_float = (float(r), float(g), float(b))
        self.update_slider_gradients(*rgb_float)
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
        
        ri = int(round(max(0.0, min(255.0, r))))
        gi = int(round(max(0.0, min(255.0, g))))
        bi = int(round(max(0.0, min(255.0, b))))

        # 1) R Slider
        self.slider_widgets["R"][0].set_gradient([
            (0.0, QColor(0, gi, bi)),
            (1.0, QColor(255, gi, bi))
        ])
        
        # 2) G Slider
        self.slider_widgets["G"][0].set_gradient([
            (0.0, QColor(ri, 0, bi)),
            (1.0, QColor(ri, 255, bi))
        ])
        
        # 3) B Slider
        self.slider_widgets["B"][0].set_gradient([
            (0.0, QColor(ri, gi, 0)),
            (1.0, QColor(ri, gi, 255))
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
        
        # 6b) VHSV Sliders
        if "H_vhsv" in self.slider_widgets:
            self.slider_widgets["H_vhsv"][0].set_gradient(hue_stops)
        if "S_vhsv" in self.slider_widgets or "V_vhsv" in self.slider_widgets:
            h_vhsv, s_vhsv, v_vhsv = rgb_to_vhsv(r, g, b)
            if "S_vhsv" in self.slider_widgets:
                rs0, gs0, bs0 = vhsv_to_rgb(h_vhsv, 0.0, v_vhsv)
                rs1, gs1, bs1 = vhsv_to_rgb(h_vhsv, 100.0, v_vhsv)
                self.slider_widgets["S_vhsv"][0].set_gradient([
                    (0.0, QColor(int(max(0, min(255, round(rs0)))), int(max(0, min(255, round(gs0)))), int(max(0, min(255, round(bs0)))))),
                    (1.0, QColor(int(max(0, min(255, round(rs1)))), int(max(0, min(255, round(gs1)))), int(max(0, min(255, round(bs1)))))),
                ])
            if "V_vhsv" in self.slider_widgets:
                rv0, gv0, bv0 = vhsv_to_rgb(h_vhsv, s_vhsv, 0.0)
                rv05, gv05, bv05 = vhsv_to_rgb(h_vhsv, s_vhsv, 50.0)
                rv1, gv1, bv1 = vhsv_to_rgb(h_vhsv, s_vhsv, 100.0)
                self.slider_widgets["V_vhsv"][0].set_gradient([
                    (0.0, QColor(int(max(0, min(255, round(rv0)))), int(max(0, min(255, round(gv0)))), int(max(0, min(255, round(bv0)))))),
                    (0.5, QColor(int(max(0, min(255, round(rv05)))), int(max(0, min(255, round(gv05)))), int(max(0, min(255, round(bv05)))))),
                    (1.0, QColor(int(max(0, min(255, round(rv1)))), int(max(0, min(255, round(gv1)))), int(max(0, min(255, round(bv1)))))),
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
        current_L = None
        if "L_lab" in self.slider_widgets:
            current_L = self.slider_widgets["L_lab"][0].value()
        return self._compute_L_gamut_range(in_gamut, current_L=current_L, as_int=True)

    @staticmethod
    def _compute_L_gamut_range(in_gamut, current_L=None, as_int=True):
        """Shared binary search: find [min_L, max_L] of in-gamut L values.

        Does NOT assume L=50 is in gamut — high-chroma colours near the
        gamut boundary can push mid-L out of gamut while low/high L
        remain valid.  Scans for any in-gamut reference point first (prioritizing
        current_L if valid), then searches outward in both directions from it.
        """
        # ── Find any in-gamut reference L ──
        ref_L = None
        if current_L is not None and 0.0 <= current_L <= 100.0 and in_gamut(current_L):
            ref_L = float(current_L)
        else:
            for test_L in (50.0, 60.0, 40.0, 70.0, 30.0, 80.0, 20.0, 90.0, 10.0,
                           0.0, 100.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0, 85.0, 95.0, 5.0, 15.0):
                if in_gamut(test_L):
                    ref_L = test_L
                    break
        if ref_L is None:
            return (0, 100) if as_int else (0.0, 100.0)  # colour is unreachable at this chromaticity

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

        if as_int:
            return int(round(min_L)), int(round(max_L))
        return round(min_L, 1), round(max_L, 1)

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
        current_L = None
        if "L_oklab" in self.slider_widgets:
            current_L = self.slider_widgets["L_oklab"][0].value()
        return self._compute_L_gamut_range(in_gamut, current_L=current_L, as_int=True)

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
        current_L = self.slider_widgets["L_oklch"][0].value()
        return self._compute_L_gamut_range(in_gamut, current_L=current_L, as_int=True)

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

        current_L = getattr(self.lab_slider, "L", getattr(self.lab_square, "L", None))
        min_L, max_L = self._compute_L_gamut_range(in_gamut, current_L=current_L, as_int=False)
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
        # Keep color_state.current in sync with every projected color.  Some
        # callers (slot changes, history picks, external active-slot changes)
        # build a Color directly and would otherwise leave color_state pointing
        # at the previous active slot's color.
        self.color_state.apply(color)
        self._source_space = color.source_space
        self._source_values = self._color_source_dict(color)
        self._current_rgb_float = color.rgb_float
        try:
            self.update_ui_colors(color.r, color.g, color.b, source=source,
                                  hsv=color.hsv if hsv is None else hsv,
                                  oklch=color.oklch, oklab=color.oklab,
                                  rgb_float=color.rgb_float,
                                  lab=color.lab,
                                  vhsv=color.vhsv)
        except TypeError:
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

    def update_ui_colors(self, r, g, b, source="", hsv=None, oklch=None, oklab=None, rgb_float=None, lab=None, vhsv=None):
        self._last_update_source = source
        r_i, g_i, b_i = int(round(r)), int(round(g)), int(round(b))
        self.current_rgb = (r_i, g_i, b_i)
        if rgb_float is None:
            rgb_float = getattr(self, "_current_rgb_float", (float(r), float(g), float(b)))
        self._current_rgb_float = rgb_float
        rf, gf, bf = rgb_float
        color = QColor(r_i, g_i, b_i)

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
            if self.color_wheel.wheel_mode == "vhsv-square":
                if vhsv is not None:
                    self.color_wheel.set_vhsv(vhsv[0], vhsv[1], vhsv[2])
                elif hsv is not None:
                    self.color_wheel.set_hsv(hsv[0], hsv[1], hsv[2])
                else:
                    self.color_wheel.set_color(r_i, g_i, b_i, block_signals=True)
            elif hsv is not None:
                self.color_wheel.set_hsv(hsv[0], hsv[1], hsv[2])
            else:
                self.color_wheel.set_color(r_i, g_i, b_i, block_signals=True)
            # Push direct OKLCh state so the indicator avoids HSV→RGB→OKLCh drift
            if self.color_wheel.wheel_mode == "oklch-slice":
                if oklch is not None:
                    L_ok, C_ok, h_ok = oklch
                else:
                    L_ok, C_ok, h_ok = rgb_to_oklch(rf, gf, bf)
                self.color_wheel.set_oklch(L_ok, C_ok, h_ok)

        # 3) Sync LAB Square / Slider (Only if visible or during init)
        if source == "init" or (source != "lab" and self.lab_square.isVisible()):
            if oklab is not None and self.lab_square.render_mode == "oklab":
                L_ok, a_ok, b_ok = oklab
                self.lab_square.set_oklab(L_ok, a_ok, b_ok, block_signals=True)
            else:
                self.lab_square.set_color(rf, gf, bf, block_signals=True)
            self.lab_slider.set_lightness(
                self.lab_square.L
            )

        # 4) Sync Sliders
        # Block signals for all sliders during sync
        all_chans = ["R", "G", "B", "H_hsv", "S_hsv", "V_hsv", "H_vhsv", "S_vhsv", "V_vhsv", "H_hsl", "L_hsl", "S_hsl", "L_lab", "a_lab", "b_lab", "L_oklab", "a_oklab", "b_oklab", "L_oklch", "C_oklch", "h_oklch"]
        for chan in all_chans:
            if chan in self.slider_widgets:
                self.slider_widgets[chan][0].blockSignals(True)
            
        # RGB Values
        if source != "sliders_rgb":
            self.slider_widgets["R"][0].setValue(r_i)
            self.slider_widgets["G"][0].setValue(g_i)
            self.slider_widgets["B"][0].setValue(b_i)
        
        # HSV Values
        if source != "sliders_hsv":
            if source == "wheel" and self.color_wheel.wheel_mode not in ("vhsv-square",):
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
            elif hsv is not None:
                h_hsv, s_hsv, v_hsv = hsv
            else:
                h_hsv, s_hsv, v_hsv = rgb_to_hsv(rf, gf, bf)
            self.slider_widgets["S_hsv"][0].setValue(round(s_hsv))
            self.slider_widgets["V_hsv"][0].setValue(round(v_hsv))
            if s_hsv >= 0.5 or hsv is not None:
                self.slider_widgets["H_hsv"][0].setValue(round(h_hsv))
        
        # VHSV Values
        if source != "sliders_vhsv":
            if source == "wheel" and self.color_wheel.wheel_mode == "vhsv-square":
                h_vhsv = self.color_wheel.h
                s_vhsv = self.color_wheel._vhsv_s
                v_vhsv = self.color_wheel._vhsv_v
            elif vhsv is not None:
                h_vhsv, s_vhsv, v_vhsv = vhsv
            else:
                h_vhsv, s_vhsv, v_vhsv = rgb_to_vhsv(rf, gf, bf)
            if "S_vhsv" in self.slider_widgets:
                self.slider_widgets["S_vhsv"][0].setValue(round(s_vhsv))
            if "V_vhsv" in self.slider_widgets:
                self.slider_widgets["V_vhsv"][0].setValue(round(v_vhsv))
            if "H_vhsv" in self.slider_widgets:
                if s_vhsv >= 0.5 or vhsv is not None or source == "wheel":
                    self.slider_widgets["H_vhsv"][0].setValue(round(h_vhsv))
        
        # HSL Values
        if source != "sliders_hsl":
            if source == "wheel" and self.color_wheel.wheel_mode not in ("vhsv-square",):
                h_hsl, l_hsl, s_hsl = hsv_to_hls_floats(self.color_wheel.h, self.color_wheel.s, self.color_wheel.v)
                self.slider_widgets["H_hsl"][0].setValue(round(h_hsl * 360.0))
            else:
                h_hsl, l_hsl, s_hsl = colorsys.rgb_to_hls(rf / 255.0, gf / 255.0, bf / 255.0)
                h_deg = hsv[0] if hsv is not None else h_hsl * 360.0  # Reuse handler's locked hue
                self.slider_widgets["H_hsl"][0].setValue(round(h_deg))
            self.slider_widgets["L_hsl"][0].setValue(round(l_hsl * 100.0))
            self.slider_widgets["S_hsl"][0].setValue(round(s_hsl * 100.0))
        
        # LAB Values
        if source != "sliders_lab":
            if source == "wheel" and self.color_wheel.wheel_mode not in ("vhsv-square",):
                h_hsv = self.color_wheel.h
                s_hsv = self.color_wheel.s
                v_hsv = self.color_wheel.v
                r_f, g_f, b_f = colorsys.hsv_to_rgb(h_hsv / 360.0, s_hsv / 100.0, v_hsv / 100.0)
                l_lab, a_lab, b_lab = rgb_to_lab(r_f * 255.0, g_f * 255.0, b_f * 255.0)
            else:
                l_lab, a_lab, b_lab = rgb_to_lab(rf, gf, bf)
            self.slider_widgets["L_lab"][0].setValue(round(l_lab))
            self.slider_widgets["a_lab"][0].setValue(round(a_lab))
            self.slider_widgets["b_lab"][0].setValue(round(b_lab))
        
        # OKLab Values
        if source != "sliders_oklab":
            if oklab is not None:
                L_ok, a_ok, b_ok = oklab
            else:
                L_ok, a_ok, b_ok = rgb_to_oklab(rf, gf, bf)
            if "L_oklab" in self.slider_widgets:
                self.slider_widgets["L_oklab"][0].setValue(round(L_ok * 100))
            self.slider_widgets["a_oklab"][0].setValue(round(a_ok * 100))
            self.slider_widgets["b_oklab"][0].setValue(round(b_ok * 100))
        
        # OKLCh Values (absolute chroma; the Color already carries the
        # gamut-mapped chroma and the remembered hue, so no re-derivation)
        if source not in ("sliders_oklch_L", "sliders_oklch_C", "sliders_oklch_h"):
            L_okc, C_okc, h_okc = oklch if oklch is not None else rgb_to_oklch(rf, gf, bf)
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
        # Direct high-precision floats: inside gamut, coordinates are constant (zero jitter).
        # When dragged out of gamut, Color.from_space / LabSquare gamut-maps to the boundary
        # so the effective range dynamically tracks the handle out of the original range.
        if oklab is not None:
            _, a_ok, b_ok = oklab
        else:
            _, a_ok, b_ok = rgb_to_oklab(rf, gf, bf)
        self._gamut_oklab_a = a_ok
        self._gamut_oklab_b = b_ok

        if lab is not None:
            _, a_lb, b_lb = lab
        else:
            _, a_lb, b_lb = rgb_to_lab(rf, gf, bf)
        self._gamut_lab_a = a_lb
        self._gamut_lab_b = b_lb

        if oklch is not None:
            _, c_ok, h_ok = oklch
        else:
            _, c_ok, h_ok = rgb_to_oklch(rf, gf, bf)
        self._gamut_oklch_C = c_ok
        self._gamut_oklch_h = h_ok

        # Heavy visual-only cosmetics (slider groove gradients + L out-of-gamut
        # masks) are deferred + coalesced so they never block the dragged
        # widget's paint. This is what keeps every slider handle and the color
        # wheel indicator perfectly following the cursor on every mouse move;
        # the colored groove bars / grayed gamut regions trail by ≤~16ms.
        self._schedule_deferred_color_updates(r_i, g_i, b_i, rgb_float=(rf, gf, bf))

        # 5) Push to drawing software — delegated to SyncMixin so the god
        # class no longer owns the companion/memory write path.
        self._push_color_to_sync(r_i, g_i, b_i, source, hsv)
