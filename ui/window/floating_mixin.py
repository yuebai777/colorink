"""Tearing a panel off into its own window, and taking it back.

The window side of ui/panels/floating: which panels are out, the windows
they live in, and the config record that survives a restart. The dock tree
is never rewritten — a floated panel keeps its slot, which is how docking
back lands it where it came from instead of at the end of the column.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from ui.panels import registry, store
from ui.panels.drag import PanelTitleBar
from ui.panels.floating import BORDER, MIN_FLOATING_SIZE, FloatingPanelWindow

__all__ = ["FloatingPanelsMixin", "MIN_FLOATING_SIZE"]


class FloatingPanelsMixin:

    def floating_windows(self) -> dict:
        windows = getattr(self, "_floating_windows_map", None)
        if windows is None:
            windows = {}
            self._floating_windows_map = windows
        return windows

    # ── out ──────────────────────────────────────────────────────────────

    def float_panel(self, panel_id: str, position=None, persist: bool = True) -> bool:
        """Tear *panel_id* out into its own window. True when it moved."""
        windows = self.floating_windows()
        if panel_id in windows:
            return False
        widget = self.panel_widget(panel_id)
        host = getattr(self, "panel_host", None)
        if widget is None or host is None:
            return False
        # Measure it before it leaves the column: once the host lets go and
        # the new window adopts it, it has already been resized to whatever
        # that window happened to be.
        original = widget.size()
        reference = getattr(self, "floating_reference_size", None)
        if callable(reference):
            supplied = reference(panel_id, widget)
            if supplied is not None:
                original = supplied
        if original.width() <= 1 or original.height() <= 1:
            original = widget.sizeHint()
        spec = registry.panel(panel_id)
        saved = store.load_floating_from(getattr(self, "cfg", None))
        window = FloatingPanelWindow(
            panel_id, spec.title if spec else panel_id, self,
            no_focus=bool(getattr(self, "cfg", {}).get("noFocusMode", False)))
        window.dock_requested.connect(self.dock_panel)
        window.dropped_at.connect(self._floating_dropped)
        window.moving_at.connect(self._floating_moving)
        window.menu_requested.connect(self.show_panel_menu)
        chrome = getattr(self, "_floating_chrome", None)
        if chrome is not None:
            window.apply_chrome(chrome)
        windows[panel_id] = window
        # Unmount first: the host detaches every panel it holds when it
        # re-mounts, and it would pull this one straight back out of the
        # window we are about to put it in.
        host.set_floating_panels(set(windows))
        # Panels the host never mounted (the LAB view lives in the picker's
        # stack, not in the dock tree) have to be unhooked by whoever owns
        # them — the host's floating set means nothing to them.
        detach = getattr(self, "detach_floating_panel", None)
        if callable(detach):
            detach(panel_id, widget)
        # Place it *before* adopting, then again after. Adopting makes the
        # window visible (the holder mirrors the panel's visibility), and a
        # window that has never been positioned shows up at the screen's
        # top-left corner for a frame — that is the flash. Sizing an empty
        # window does not stick either, hence the second call.
        rect = self._floating_geometry(panel_id, original, position)
        window.setGeometry(*rect)
        window.set_panel(widget)
        # A panel whose content is driven by the owner's geometry (the LAB
        # squares are sized by the picker pass, not by their own layout)
        # must be told how big it is *after* it lands in the window — its
        # size hint is a construction-time lie, so the layout alone would
        # leave it a stub.
        size_owner = getattr(self, "float_panel_size", None)
        if callable(size_owner):
            size_owner(panel_id, widget, rect)
        window.setGeometry(*rect)
        if panel_id in saved and not saved[panel_id].on_top:
            window.set_always_on_top(False)
        window.show_without_stealing_focus()
        self_correct = getattr(self, "floating_self_correct", None)
        if panel_id not in saved and (not callable(self_correct)
                                      or self_correct(panel_id)):
            # Self-correct rather than trust the arithmetic: the promise is
            # "the panel keeps its size", and chrome adds up to a few pixels
            # that are easy to get wrong. Measure the result and fix it up.
            QApplication.processEvents()
            panel = window.panel()
            if panel is not None:
                fix_w = original.width() - panel.width()
                fix_h = original.height() - panel.height()
                if fix_w or fix_h:
                    window.resize(window.width() + fix_w,
                                  window.height() + fix_h)
        # Only now: a window that is still being assembled must not be able
        # to write its half-built geometry over the record it was restored
        # from.
        window.geometry_changed.connect(lambda _pid: self._save_floating_state())
        if persist:
            self._save_floating_state()
        return True

    def _floating_geometry(self, panel_id, size, position):
        """Where and how big the torn-off window should be.

        *size* is the panel's size in the column, measured before it left:
        a block that was 344px wide should not snap to some other width the
        moment it becomes a window. Only the chrome is added on top.
        """
        saved = store.load_floating_from(getattr(self, "cfg", None))
        if panel_id in saved:
            return saved[panel_id].rect
        chrome = getattr(self, "_floating_chrome", None)
        border = chrome.border_width if chrome is not None else BORDER
        bar = (max(12, int(round(PanelTitleBar.FLOATING_HEIGHT * chrome.scale)))
               if chrome is not None else PanelTitleBar.FLOATING_HEIGHT)
        pad = (chrome.content_margins if chrome is not None else (4, 6, 4, 6))
        width = (max(MIN_FLOATING_SIZE[0], size.width())
                 + border * 2 + int(pad[0]) + int(pad[2]))
        gap = chrome.grip_gap if chrome is not None else 4
        height = (max(MIN_FLOATING_SIZE[1], size.height())
                  + bar + border + int(pad[1]) + int(gap) + int(pad[3]))
        point = position if position is not None else QCursor.pos()
        return (point.x() - 24, point.y() - 8, width, height)

    # ── back ─────────────────────────────────────────────────────────────

    def dock_panel(self, panel_id: str) -> bool:
        """Put a torn-off panel back into the arrangement. True when it moved."""
        windows = self.floating_windows()
        window = windows.pop(panel_id, None)
        if window is None:
            return False
        widget = window.take_panel()
        window.hide()
        window.deleteLater()
        attach = getattr(self, "attach_floating_panel", None)
        claimed = bool(widget is not None and callable(attach)
                       and attach(panel_id, widget))
        host = getattr(self, "panel_host", None)
        if host is not None:
            # The tree still holds its slot, so this lands it back home.
            host.set_floating_panels(set(windows))
        if (not claimed and widget is not None and host is not None
                and host.widget_for(panel_id) is None):
            # Nothing claimed it (the group is not in the current tree):
            # keep it out of limbo rather than leaking a parentless widget.
            widget.hide()
        self._save_floating_state()
        return True

    def restore_floating_panels(self) -> None:
        """Re-open the windows that were torn off when the app last closed."""
        # One config write at the end, not one per window: this runs while
        # the window is still being built.
        for panel_id in store.load_floating_from(getattr(self, "cfg", None)):
            self.float_panel(panel_id, persist=False)

    # ── bookkeeping ──────────────────────────────────────────────────────

    def _host_point(self, global_pos):
        """Where a global point falls inside the panel host, or None."""
        host = getattr(self, "panel_host", None)
        # isHidden(), not isVisible(): the latter is False for a window that
        # simply has not been shown yet, and this runs during startup too.
        if host is None or host.isHidden():
            return None
        rect = QRect(host.mapToGlobal(QPoint(0, 0)), host.size())
        if not rect.contains(global_pos):
            return None
        return host.mapFromGlobal(global_pos)

    def _floating_moving(self, panel_id: str, global_pos) -> None:
        """Dragging a floating window over the column previews the landing."""
        host = getattr(self, "panel_host", None)
        if host is None:
            return
        point = self._host_point(global_pos)
        if point is None:
            host.clear_drop_hint()
        else:
            host.show_drop_hint(point)

    def _floating_dropped(self, panel_id: str, global_pos) -> None:
        """A floating window was dragged and released.

        Released over the column it docks back, and lands *where it was
        dropped* — same four-borders-and-a-middle rule as dragging a panel
        around inside the window, so there is only one thing to learn.
        Anywhere else it simply stays where the user put it.
        """
        # A panel the host never mounts (the LAB view lives in the picker
        # stack) has no drop target in the column — its owner says where it
        # can land instead.
        owner_target = getattr(self, "dock_target_at", None)
        if callable(owner_target):
            owner_spot = owner_target(panel_id, global_pos)
            if owner_spot:
                self.dock_panel(panel_id)
                return
        host = getattr(self, "panel_host", None)
        point = self._host_point(global_pos)
        if point is None:
            self._save_floating_state()
            return
        target = host.drop_target_at(point) if host is not None else None
        host.clear_drop_hint()
        self.dock_panel(panel_id)
        if target is not None and target[0] != panel_id:
            self._dock_at(panel_id, target)

    def _dock_at(self, panel_id: str, target) -> None:
        """Move a just-docked panel to where it was dropped."""
        from ui.panels import rearrange

        host = getattr(self, "panel_host", None)
        if host is None:
            return
        moved = rearrange.move_panel(host.tree(), panel_id, target[0], target[1])
        if moved == host.tree():
            return
        host.set_tree(moved)
        record = getattr(self, "save_panel_layout", None)
        if callable(record):
            record(moved)
        self._save_floating_state()

    def _save_floating_state(self) -> None:
        from core import config

        cfg = getattr(self, "cfg", None)
        store.save_floating_into(
            cfg, {panel_id: store.FloatingState(window.geometry_record(),
                                                window.always_on_top())
                  for panel_id, window in self.floating_windows().items()})
        if cfg is not None:
            config.save_hotkey_config(cfg)

    # ── the right-click menu ─────────────────────────────────────────────

    def panel_menu_for(self, panel_id: str):
        """The (action, label) pairs to offer for this panel right now."""
        from ui.panels import menu

        return menu.panel_menu_actions(
            panel_id, panel_id in self.floating_windows())

    def run_panel_action(self, panel_id: str, action: str) -> bool:
        """Carry out a menu choice. False when the action means nothing."""
        from core import config
        from ui.panels import menu

        if action == menu.FLOAT:
            return self.float_panel(panel_id)
        if action == menu.DOCK:
            return self.dock_panel(panel_id)
        if action == menu.HIDE:
            key = menu.visibility_key(panel_id)
            if not key:
                return False
            # Put it away first: hiding a panel that is out would leave its
            # window on screen with nothing in it.
            self.dock_panel(panel_id)
            self.cfg[key] = False
            config.save_hotkey_config(self.cfg)
            self.refresh_slider_visibility_and_order()
            return True
        if action == menu.RESET:
            self.reset_panel_layout()
            config.save_hotkey_config(self.cfg)
            self.refresh_slider_visibility_and_order()
            return True
        return False

    def show_panel_menu(self, panel_id: str, global_pos) -> None:
        """Pop the panel menu where the user right-clicked."""
        from PyQt6.QtWidgets import QMenu

        entries = self.panel_menu_for(panel_id)
        if not entries:
            return
        popup = QMenu(self)
        choices = {}
        for action, label in entries:
            choices[popup.addAction(label)] = action
        picked = popup.exec(global_pos)
        if picked is not None:
            self.run_panel_action(panel_id, choices[picked])

    def set_floating_foreground_visible(self, visible: bool) -> None:
        """Follow the main window's "only while the drawing app is focused".

        check_foreground_window() shows/hides the main window; the torn-off
        panels are part of the same palette, so they obey the same rule
        instead of lingering on screen over an unrelated app. A panel that
        is hidden for its own reason (module filter) stays hidden — only
        windows whose panel is still visible are brought back.

        Hiding is done in both directions: the Qt state (so the window
        behaves like a hidden widget for the rest of the app) and the real
        HWND (Qt's hide() is a no-op for a Tool window that already believes
        it is hidden because its owner is, so without the Win32 call the
        screen keeps showing the panel).
        """
        for window in list(self.floating_windows().values()):
            try:
                window.set_foreground_hidden(not visible)
                if visible:
                    panel = window.panel()
                    if panel is None or not panel.testAttribute(
                            Qt.WidgetAttribute.WA_WState_Hidden):
                        window.show_without_stealing_focus()
                        window.force_native_visible(True)
                else:
                    window.hide()
                    window.force_native_visible(False)
            except RuntimeError:
                # The window was docked away/deleteLater'd while an event was
                # being delivered (teardown); it is no longer in the map on
                # the next call anyway.
                continue

    def showEvent(self, event):
        """The palette is on screen again: its windows come back too."""
        super().showEvent(event)
        self.set_floating_foreground_visible(True)

    def hideEvent(self, event):
        """The palette went away (hotkey, tray, foreground tracker): park the
        torn-off windows with it. The tracker calls the same path explicitly;
        this covers the user-initiated hide/show toggles."""
        super().hideEvent(event)
        self.set_floating_foreground_visible(False)

    def refresh_floating_focus(self) -> None:
        """Re-apply the no-focus setting to windows that are already out."""
        enabled = bool(getattr(self, "cfg", {}).get("noFocusMode", False))
        for window in self.floating_windows().values():
            window.set_no_focus(enabled)
