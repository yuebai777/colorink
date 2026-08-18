"""Window layout, geometry, ringless sync and local LAB-toggle input.

Extracted from ``ui.main_window``: content-height policy, resize/move
handling, ringless layout propagation and the local LAB-switch event paths.
"""

import time

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QWidget

from core import config
from ui.hotkey_button import MOUSE_BUTTON_NAME_BY_QT, capture_active, parse_key_event
from ui.ringless_mode import RinglessConfig, resolve_ringless_layout
from ui.widgets import _title_bar_content_offset, _visible_title_bar_height


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

    @staticmethod
    def _required_visualizer_height(window_width, margins_left, margins_right,
                                    stack_min_height):
        available_width = int(window_width) - int(margins_left) - int(margins_right)
        return max(available_width, int(stack_min_height))

    def _run_deferred_content_height(self):
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

    def showEvent(self, event):
        """On show, run any content-height adjustment deferred while hidden."""
        super().showEvent(event)
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
