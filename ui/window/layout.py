"""Window layout, geometry, ringless sync and local LAB-toggle input.

Extracted from ``ui.main_window``: content-height policy, resize/move
handling, ringless layout propagation and the local LAB-switch event paths.
"""

import math
import os
import time

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from core import config
from ui import preview_clearance, window_layout
from ui.hotkey_button import MOUSE_BUTTON_NAME_BY_QT, capture_active, parse_key_event
from ui.ringless_mode import RinglessConfig, resolve_ringless_layout
from ui.widgets import _title_bar_content_offset, _visible_title_bar_height


def _pen_debug(*parts):
    """Print pen-hover diagnostics to stderr when COLORINK_DEBUG_PEN=1.

    Used to observe what the pen-cursor fix actually receives on real
    hardware (drivers vary wildly between Windows Ink and Wintab).
    """
    if os.environ.get("COLORINK_DEBUG_PEN"):
        print("[pen]", *parts, flush=True)


# Qt cursor shapes → Win32 OCR system-cursor resource IDs (used by
# _force_cursor_shape to make pen-hover cursors switch natively).
_OCR_CURSOR_BY_SHAPE = {
    Qt.CursorShape.ArrowCursor: 32512,          # OCR_NORMAL
    Qt.CursorShape.IBeamCursor: 32513,          # OCR_IBEAM
    Qt.CursorShape.WaitCursor: 32514,           # OCR_WAIT
    Qt.CursorShape.CrossCursor: 32515,          # OCR_CROSS
    Qt.CursorShape.SizeVerCursor: 32645,        # OCR_SIZENS
    Qt.CursorShape.SizeHorCursor: 32644,        # OCR_SIZEWE
    Qt.CursorShape.SizeBDiagCursor: 32643,      # OCR_SIZENESW
    Qt.CursorShape.SizeFDiagCursor: 32642,      # OCR_SIZENWSE
    Qt.CursorShape.SizeAllCursor: 32646,        # OCR_SIZEALL
    Qt.CursorShape.PointingHandCursor: 32649,   # OCR_HAND
    Qt.CursorShape.ForbiddenCursor: 32648,      # OCR_NO
}


class LayoutMixin:

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

    # Kept as a thin alias: the rule itself lives in ui.window_layout.
    _picker_square_height = staticmethod(window_layout.picker_square_height)

    @staticmethod
    def _required_visualizer_height(window_width, margins_left, margins_right,
                                    stack_min_height):
        available_width = int(window_width) - int(margins_left) - int(margins_right)
        return max(available_width, int(stack_min_height))

    # One debounce for everything that belongs *after* a drag rather than
    # during it. Three separate delays used to be scattered around
    # (500ms wheel prewarm, 80/100ms LAB prewarm, 160ms height policy).
    _SETTLE_DELAY_MS = 160

    def _schedule_settle(self, width_changed: bool = False):
        """Arm the post-drag settle: content height, then full-quality art.

        The picker area is square, so its height follows the window width —
        but the resize path deliberately skips _adjust_content_height (running
        it inline would fight the drag and re-introduce DPI drift). Without
        this the minimum height stayed at the value computed for the *old*
        width: a narrowed window could not be dragged any shorter and kept a
        tall blank band under the wheel.
        """
        if width_changed:
            self._settle_needs_height = True
        timer = getattr(self, "_settle_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._run_settle)
            self._settle_timer = timer
        timer.start(self._SETTLE_DELAY_MS)

    def _run_settle(self):
        """Land the deferred work once the frame stops moving."""
        if getattr(self, "resizing", False):
            # Still dragging: snapping the height now would yank the window
            # out from under the cursor. Re-arm and try again.
            self._schedule_settle()
            return
        if getattr(self, "_settle_needs_height", False):
            self._settle_needs_height = False
            self._run_deferred_content_height()
        # Full-quality art for whichever page is up; the drag itself runs on
        # the cheap low-resolution path.
        wheel = getattr(self, "color_wheel", None)
        square = getattr(self, "lab_square", None)
        on_wheel = getattr(self, "stack", None) is None or self.stack.currentIndex() == 0
        if on_wheel and wheel is not None:
            wheel.schedule_slice_prewarm(0)
        elif square is not None:
            square.schedule_full_prewarm(0)

    # Back-compat alias: the width-driven height pass is now one part of the
    # settle.
    def _schedule_width_driven_height(self):
        self._schedule_settle(width_changed=True)

    def _run_deferred_content_height(self):
        if getattr(self, "resizing", False):
            # Still dragging the frame: snapping the height now would yank the
            # window out from under the cursor. Re-arm and let the release (or
            # the next settle) do it.
            timer = getattr(self, "_width_height_timer", None)
            if timer is not None:
                timer.start(max(80, timer.interval()))
                return
        self._content_height_adjust_pending = False
        if not self.isVisible():
            # 仍处于隐藏状态：不重 arm 定时器（否则 0ms 定时器会无限自旋，
            # 单核 CPU 打满），把调整留到 showEvent 补做。
            self._content_height_adjust_pending = True
            return
        self._adjust_content_height()

    def _adjust_content_height(self):
        if getattr(self, "_adjusting_content_height", False):
            return
        if not self.isVisible():
            # 隐藏时只记录 pending，绝不启动 0ms 定时器；showEvent 会补一次。
            self._content_height_adjust_pending = True
            return
        self._content_height_adjust_pending = False
        required = 0
        try:
            self.sliders_layout.activate()
            # The slider column now lives inside a PanelHost, whose own
            # layout may not have been activated yet — its sizeHint then
            # reads ~16px and the height policy "grows to fit" against a
            # phantom content size. Activate every level of the column.
            host = getattr(self, "panel_host", None)
            host_layout = host.layout() if host is not None else None
            if host_layout is not None:
                host_layout.activate()
            stack_layout = host.layout() if host is not None else None
            outer = self.sliders_container.layout()
            if outer is not None and outer is not stack_layout:
                outer.activate()
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
            host = getattr(self, "panel_host", None)
            if host is not None:
                # Zero is a real answer: with every panel torn off there is
                # no column, and falling back to the container's size hint
                # books ~16px of margins for nothing — the window then
                # refuses to shrink the last band under the picker.
                hint = host.column_hint()
            else:
                hint = self.sliders_container.sizeHint().height()
            # The host's own outer layout adds its margins/spacing on top of
            # the column: pad the deterministic hint with the same amounts —
            # but only when there is a column to pad.
            outer = self.sliders_container.layout()
            if host is not None and outer is not None and hint > 0:
                hm = outer.contentsMargins()
                hint += hm.top() + hm.bottom() + outer.spacing() * 1
            required = self._required_content_height(
                _visible_title_bar_height(self.title_bar),
                visualizer_h,
                hint,
                margins.top(), margins.bottom(), self.main_layout.spacing(),
            )
        except AttributeError:
            return

        self.setMinimumHeight(required)
        # What the content asks for right now — the yardstick a manually set
        # height is measured against (see mouseReleaseEvent).
        self._last_required_height = required
        # Height policy lives in _resolve_content_height: grow to fit; keep a
        # height the user chose as long as the content still needs the same
        # room; otherwise follow the content back down. Following it down is
        # what makes the window close up again after panels are torn off or
        # hidden — without it the picker sits above a band of nothing that
        # only a manual drag can remove.
        target, manual = self._resolve_content_height(
            self.height(), required, getattr(self, "_last_auto_height", None),
            getattr(self, "_manual_height_override", False))
        self._manual_height_override = manual
        if target == self.height():
            return
        self._adjusting_content_height = True
        try:
            self.resize(self.width(), target)
        finally:
            self._adjusting_content_height = False

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

    def window_layout(self, scale=None):
        """This window's bands, computed from plain numbers.

        The single place the picker rectangle and the hue-ring circle come
        from. Reading them off the widgets instead is what produced this
        session's geometry bugs: a stacked page that has never been shown
        still reports its construction-time size, and isVisible() lies until
        the window is really up.
        """
        if scale is None:
            scale = self.cfg.get("uiScale", 100) / 100.0
        margins = self.main_layout.contentsMargins()
        return window_layout.resolve_window_layout(
            window_width=self.width(),
            window_height=self.height(),
            margins=(margins.left(), margins.top(),
                     margins.right(), margins.bottom()),
            title_height=_visible_title_bar_height(self.title_bar),
            sliders_height=self.sliders_container.sizeHint().height(),
            spacing=int(4 * scale),
            ui_scale=scale,
            picker_minimum=self.stack.minimumSizeHint().height(),
        )

    def _picker_circles(self, layout=None):
        """Round picker areas in window coordinates: hue ring + LAB disc.

        Both are needed, not just the bigger one: with the lightness bar
        shown the LAB disc is smaller than the ring BUT sits further up and
        to the left, so it reaches into the top-left corner where the ring
        does not.
        """
        circles = []
        try:
            ring = (layout or self.window_layout()).picker_circle
            if ring.radius > 0:
                circles.append((ring.x, ring.y, ring.radius))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        # Only while the LAB page is actually up: a stacked page that has
        # never been shown still carries its construction-time geometry, and
        # that phantom circle would shrink the cluster for nothing.
        stack = getattr(self, "stack", None)
        on_lab_page = stack is not None and stack.currentIndex() == 1
        square = getattr(self, "lab_square", None)
        if (on_lab_page and square is not None
                and getattr(square, "shape", "") == "disc"):
            try:
                if square.width() > 0:
                    dcx, dcy, radius = square._disc_metrics()
                    origin = square.mapTo(self, square.rect().topLeft())
                    circles.append((origin.x() + dcx, origin.y() + dcy, radius))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        return circles

    # Floor for the trim applied when a corner is too shallow to swallow the
    # cluster's nominal, wheel-proportional size. Deep enough to still clear
    # the disc in a very narrow window, where the picker circle eats most of
    # the corner.
    _PREVIEW_FIT_MIN = 0.68

    def _try_preview_fit(self, preview, factor, metrics, circles, bounds):
        """Place the cluster at *factor* and report the penetration left."""
        layout, title_offset, window_h, sliders_h = metrics
        wheel_size = int(round(layout.picker_size))
        preview.resize_and_position(
            max(16, int(wheel_size * factor)), title_offset, window_h,
            sliders_h, self.active_slot,
        )
        if not circles:
            return 0.0
        self._anchor_preview_to_circle(preview, circles[0], bounds)
        return preview_clearance.avoid_circles(preview, circles, bounds)

    def _place_preview_box(self, layout, title_offset, window_h, sliders_h):
        """Size and position the floating fg/bg + transparent cluster.

        Both are bound to the hue ring: the cluster scales with the wheel and
        is then slid along its own corner until the swatches and the capsule
        clear the ring. A narrow window leaves a corner too shallow to
        swallow the nominal size — the whole cluster is then trimmed a few
        percent (still one deterministic function of the wheel geometry)
        instead of being left grazing the arc, which is what it used to do in
        both corners.
        """
        preview = getattr(self, "preview_box", None)
        if preview is None:
            return
        # Size and cage both come from the layout, so the cluster tracks the
        # ring the window is actually drawing rather than an approximation
        # recomputed per call site.
        wheel_size = int(round(layout.picker_size))
        # Remembered so a page/shape switch can re-fit without a full
        # geometry pass (those deliberately skip apply_theme).
        self._preview_metrics = (layout, title_offset, window_h, sliders_h)
        circles = self._picker_circles(layout)
        bounds = layout.picker_bounds
        # Everything the fit depends on. A drag calls this twice per event
        # (theme pass + geometry pass) and the settled state repeats forever;
        # re-running the probe loop each time only produces identical moves.
        signature = (wheel_size, title_offset, window_h, sliders_h,
                     self.active_slot, preview.position_mode, bounds,
                     tuple(round(v, 2) for c in circles for v in c))
        if signature == getattr(self, "_preview_fit_signature", None):
            return
        self._preview_fit_signature = signature
        metrics = (layout, title_offset, window_h, sliders_h)
        with preview_clearance.fit_scope(preview):
            # One probe at full size, then the trim in closed form. Probing
            # repeatedly (ladder or convergence loop) re-measures rounded
            # intermediate placements, and the answer then wobbles a few px
            # between neighbouring window widths — the cluster twitches
            # while the frame is dragged. The closed form only reads
            # geometry that varies smoothly with the window, so the size
            # does too.
            if self._try_preview_fit(preview, 1.0, metrics, circles, bounds) <= 0.25:
                return
            factor = self._preview_trim_factor(preview, circles, bounds)
            if factor >= 0.999:
                return
            self._try_preview_fit(preview, factor, metrics, circles, bounds)

    def _preview_trim_factor(self, preview, circles, bounds):
        """How much smaller the cluster must be to clear the picker circles.

        Everything in the cluster scales from its corner anchor, so an
        obstacle sitting *depth* away from that corner loses depth * (1 - f)
        of reach when the cluster is scaled by f. Solving that against the
        shortfall gives the factor directly — no search, and the result
        moves continuously with the window size.
        """
        clearance = preview_clearance.cluster_clearance(preview)
        corner_x = float(bounds[0])
        corner_y = (float(bounds[1]) if preview.position_mode == "top-left"
                    else float(bounds[3]))
        obstacles = preview_clearance.cluster_obstacles(preview)
        factor = 1.0
        for cx, cy, radius in circles:
            for px, py, pr in obstacles:
                shortfall = (radius + pr + clearance) - math.hypot(px - cx, py - cy)
                if shortfall <= 0.0:
                    continue
                depth = math.hypot(px - corner_x, py - corner_y) + pr + clearance
                if depth <= 1e-6:
                    continue
                factor = min(factor, 1.0 - shortfall / depth)
        return max(self._PREVIEW_FIT_MIN, min(1.0, factor))

    @staticmethod
    def _anchor_preview_to_circle(preview, circle, bounds):
        """Pin the cluster's vertical edge to the wheel's own circle.

        resize_and_position anchors it to the picker area instead, so a
        window taller than its square picker dragged the cluster down with
        the bottom edge while the wheel stayed put. Riding the circle keeps
        the pair locked together at any window height — the same contract its
        size already follows.
        """
        _cx, cy, radius = circle
        y0, y1 = bounds[1], bounds[3]
        if preview.position_mode == "top-left":
            y = cy - radius
        else:
            y = cy + radius - preview.height()
        y = max(float(y0), min(y, float(y1) - preview.height()))
        preview.move(preview.x(), int(round(y)))

    def _refit_preview_box(self):
        """Re-fit the swatch cluster after a page or LAB-shape switch.

        Those paths skip the full geometry pass on purpose, but the circle
        the cluster has to clear just changed (ring ⇄ disc), so the fit has
        to be redone with the metrics the last pass computed.
        """
        metrics = getattr(self, "_preview_metrics", None)
        if metrics is None:
            return
        self._place_preview_box(*metrics)
        preview = getattr(self, "preview_box", None)
        if preview is not None:
            preview.raise_()

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

        if pb.position_mode != "top-left" or pb.isHidden():
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Both rectangles come from the layout, not from mapFromGlobal on the
        # two widgets: the LAB page keeps its construction-time geometry until
        # it is first shown, so mapping through it produced a bogus offset
        # whenever the wheel page was in front.
        scale = self.cfg.get("uiScale", 100) / 100.0
        try:
            picker = self.window_layout(scale).picker
            box = pb.geometry()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        # Only avoid while the cluster actually overlaps the plane's column.
        lab_margins = self.lab_layout.contentsMargins()
        plane_top = picker.y + lab_margins.top()
        if box.right() <= picker.x or box.x() >= picker.right:
            if callable(getattr(ls, "set_avoid_top", None)):
                ls.set_avoid_top(0)
            ls.avoid_top = 0
            return
        pad = int(4 * scale)
        new_avoid_top = max(0, int(box.bottom() - plane_top + pad))
        if callable(getattr(ls, "set_avoid_top", None)):
            ls.set_avoid_top(new_avoid_top)
        ls.avoid_top = new_avoid_top

    def showEvent(self, event):
        """On show, run any content-height adjustment deferred while hidden."""
        super().showEvent(event)
        # Re-apply WS_EX_NOACTIVATE now that the native window exists. This
        # covers startup/hotkey-show paths where update_window_flags() ran
        # before winId()/show() created the native handle.
        apply_noactivate = getattr(self, "_apply_ws_ex_noactivate", None)
        if callable(apply_noactivate):
            cfg = getattr(self, "cfg", {})
            is_settings_open = hasattr(self, 'settings_sidebar') and self.settings_sidebar.isVisible()
            apply_noactivate(cfg.get("noFocusMode", False) and not is_settings_open)
        if self._content_height_adjust_pending:
            self._content_height_adjust_pending = False
            from PyQt6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(0, self._adjust_content_height)

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
        old = event.oldSize()
        self._schedule_settle(
            width_changed=old.isValid() and event.size().width() != old.width())

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
        
        # Geometry only: the chrome (every stylesheet in the window) does not
        # depend on the window size, and rebuilding it per resize event is
        # what used to make the drag stutter.
        self.apply_layout(dynamic_scale)
        
        title_h = _visible_title_bar_height(self.title_bar)
        title_offset = _title_bar_content_offset(self.title_bar, self.main_layout)
        sliders_h = self.sliders_container.sizeHint().height()
        margins = self.main_layout.contentsMargins()
        
        # Calculate visualizer wheel size from the width, but never taller
        # than the visualizer pane: a short/wide window (manual resize)
        # shrinks the wheel instead of clipping its lower arc.  Mirrors the
        # clamp in ColorWheel.get_wheel_geometry().
        # One layout for the whole pass — every band below reads it instead
        # of measuring widgets (ui.window_layout).
        layout = self.window_layout(dynamic_scale)
        # The picker is square, and the content-height policy sizes the window
        # so it can be. That policy is debounced, though, so mid-drag the pane
        # would take every spare pixel of height and go tall-and-narrow: the
        # wheel stops tracking the width, and the corner the swatches live in
        # collapses. Capping it keeps the picker square the whole way through
        # and parks the surplus below, where the settle removes it.
        square = window_layout.picker_square_height(
            w, margins.left(), margins.right(),
            self.stack.minimumSizeHint().height())
        if self.stack.maximumHeight() != square:
            self.stack.setMaximumHeight(square)
        wheel_size = int(round(layout.picker_size))
        
        # ── Step 1: legacy preview sizing ALWAYS runs first ──
        # This restores legacy circle sizing/position when ringless is disabled,
        # and provides a baseline that ringless may override below.
        self._place_preview_box(layout, title_offset, h, sliders_h)
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
            # Remember what the content needed when the user picked this
            # height: their choice survives while that number holds, and the
            # window goes back to following the content once it changes.
            self._last_auto_height = getattr(self, "_last_required_height",
                                             self.height())
        
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
            # Pen hover arrives as QTabletEvent(TabletMove); on Windows Qt
            # does not re-run its widget-cursor machinery for those events
            # (synthetic mouse moves are compressed away), so the wheel's
            # crosshair never appears with a tablet pen.  Mirror the mouse
            # cursor logic for the pen.  The guard is intentionally loose:
            # ``_sync_tablet_cursor`` re-resolves the real widget under the
            # pen position, so it works no matter whether Qt routes the
            # tablet event to the child widget or to the application.
            elif (
                event.type() == QEvent.Type.TabletMove
                and not getattr(self, "resizing", False)
            ):
                self._sync_tablet_cursor(event.globalPosition().toPoint())
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

    def _force_cursor_shape(self, shape):
        """Set the OS cursor immediately without touching Qt's cursor state.

        Pen hover is delivered as QTabletEvent(TabletMove); Qt's Windows QPA
        does not re-apply widget cursors on that path, so the OS cursor keeps
        whatever shape Qt last applied (usually the arrow).  A direct
        SetCursor mirrors what the mouse path does; Qt's own resolution takes
        over again on the next real mouse event.
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetCursor.restype = ctypes.c_void_p
            user32.SetCursor.argtypes = [ctypes.c_void_p]
            if shape == Qt.CursorShape.BlankCursor:
                handle = self._blank_cursor_handle()
                _pen_debug("force blank handle", handle)
            else:
                ocr = _OCR_CURSOR_BY_SHAPE.get(shape)
                if ocr is None:
                    _pen_debug("force skip unhandled shape", shape)
                    return
                user32.LoadCursorW.restype = ctypes.c_void_p
                user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                handle = user32.LoadCursorW(None, ocr)
                _pen_debug("force", shape, "ocr", ocr, "handle", handle)
                if not handle:
                    return
            prev = user32.SetCursor(handle)
            _pen_debug("SetCursor -> ok (prev", prev, ")")
        except Exception as exc:  # noqa: BLE001
            _pen_debug("force exception", type(exc).__name__, exc)

    def _blank_cursor_handle(self):
        """Create (once) and keep alive a 1×1 fully-transparent cursor.

        Same technique as the picker overlay's ``_hide_cursor``.  The AND/XOR
        masks must stay alive for the lifetime of the cursor — the cached
        tuple keeps them referenced.
        """
        cached = getattr(self, "_forced_blank_cursor", None)
        if cached is not None:
            return cached[0]
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.CreateCursor.restype = ctypes.c_void_p
            andmask = (ctypes.c_ubyte * 4)(0xFF, 0xFF, 0xFF, 0xFF)
            xormask = (ctypes.c_ubyte * 4)(0x00, 0x00, 0x00, 0x00)
            handle = user32.CreateCursor(
                None, 0, 0, 1, 1,
                ctypes.cast(andmask, ctypes.c_void_p),
                ctypes.cast(xormask, ctypes.c_void_p),
            )
            if handle:
                self._forced_blank_cursor = (int(handle), andmask, xormask)
                return int(handle)
        except Exception:
            pass
        return None

    def _sync_tablet_cursor(self, global_pos=None):
        """Mirror the mouse cursor logic for pen hover (TabletMove).

        On Windows, pen hover reaches the app as QTabletEvent(TabletMove)
        (Windows Ink or Wintab) and Qt does not re-apply the widget cursor,
        so the color wheel's crosshair never shows with a pen.  Resolve the
        widget under the pen and force the same cursor the mouse would get,
        so the pen behaves identically across tablet drivers/brands.
        """
        try:
            if global_pos is None:
                global_pos = QCursor.pos()
            _pen_debug("TabletMove at", global_pos)
            if getattr(self, "resizing", False):
                _pen_debug("  (resizing, skip)")
                return

            # lockWindowSize only disables manual edge-resizing — it must NOT
            # turn the pen cursor into an arrow.  Resize-border cursors are
            # skipped when locked; the pen-under-widget shape still applies.
            size_locked = bool(self.cfg.get("lockWindowSize", False))

            # During an active drag the widgets blank the cursor themselves.
            wheel_dragging = hasattr(self, "color_wheel") and bool(self.color_wheel.dragging)
            slider_down = False
            for _chan, (slider, _) in getattr(self, "slider_widgets", {}).items():
                if slider.isSliderDown():
                    slider_down = True
                    break
            if wheel_dragging or slider_down:
                _pen_debug("  dragging, blank")
                self._force_cursor_shape(Qt.CursorShape.BlankCursor)
                return

            # Resize border zones keep their own cursors — unless the window
            # size is locked (then there is nothing to resize at the edge).
            if not size_locked:
                self._sync_resize_cursor(global_pos)
                direction = self.get_resize_direction(self.mapFromGlobal(global_pos))
                if direction:
                    shape = {
                        "left": Qt.CursorShape.SizeHorCursor,
                        "right": Qt.CursorShape.SizeHorCursor,
                        "bottom": Qt.CursorShape.SizeVerCursor,
                        "bottom-left": Qt.CursorShape.SizeBDiagCursor,
                        "bottom-right": Qt.CursorShape.SizeFDiagCursor,
                    }[direction]
                    _pen_debug("  resize zone", direction)
                    self._force_cursor_shape(shape)
                    return

            # The widget under the pen decides the shape — same rule as the
            # mouse.  widgetAt resolves from the native window hierarchy, so
            # it is reliable even when Qt's mouse state is stale for pens.
            w = QApplication.widgetAt(global_pos)
            _pen_debug("  widgetAt ->", type(w).__name__ if w else None)
            if w is None or self.window() != w.window():
                _pen_debug("  not our window / none -> arrow")
                self._force_cursor_shape(Qt.CursorShape.ArrowCursor)
                return
            shape = w.cursor().shape()
            _pen_debug("  widget shape ->", shape)
            self._force_cursor_shape(shape)
        except Exception as exc:  # noqa: BLE001
            _pen_debug("sync exception", type(exc).__name__, exc)

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
