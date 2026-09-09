"""Tearing a panel off into its own window, and taking it back.

The window side of ui/panels/floating: which panels are out, the windows
they live in, and the config record that survives a restart. The dock tree
is never rewritten — a floated panel keeps its slot, which is how docking
back lands it where it came from instead of at the end of the column.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
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

    def _retire_floating_window(self, window) -> None:
        """Park a floating window instead of deleteLater-ing it.

        A QDrag (or a deferred drop-finish) can still hold a reference to a
        window that is being torn down; deleting the C++ object and letting
        PyQt recycle the wrapper turns live-looking pointers into freed ones
        in ``drop_target_at`` — the access violation the user hits after the
        third panel lands in one floating window. Retired windows are hidden
        and never shown again; they are a small bounded set (one per panel
        group) and only keep the C++ side alive.
        """
        window.hide()
        retired = getattr(self, "_retired_floating_windows", None)
        if retired is None:
            retired = self._retired_floating_windows = []
        if window not in retired:
            retired.append(window)

    @staticmethod
    def _floating_window_dead(window) -> bool:
        """True when *window*'s C++ object has been destroyed."""
        try:
            from PyQt6 import sip
            return bool(sip.isdeleted(window))
        except Exception:
            return False

    # ── out ──────────────────────────────────────────────────────────────

    def float_panel(self, panel_id: str, position=None, persist: bool = True,
                    refresh: bool = True, fallback_size=None,
                    defer_refresh: bool = False) -> bool:
        """Tear *panel_id* out into its own window. True when it moved.

        *fallback_size* is the size the panel really had where it came from,
        for the cases where the widget cannot report it any more (a panel
        that was just mounted into another window still reports Qt's default
        100px until that window's layout has run).

        *defer_refresh* lets a caller that is floating several panels in a row
        coalesce the column re-mount into one pass (and flush it itself). The
        default is immediate: a single tear-off must be fully mounted by the
        time this returns, or the caller sees a window that is still empty.
        """
        windows = self.floating_windows()
        if panel_id in windows:
            old_win = windows[panel_id]
            if len(old_win.panel_ids) > 1:
                widget = old_win.take_panel(panel_id)
                windows.pop(panel_id, None)
            else:
                return False
        else:
            widget = self.panel_widget(panel_id)
        host = getattr(self, "panel_host", None)
        if widget is None or host is None:
            return False
        # Measure it before anything detaches it. A panel's live size is only
        # meaningful while it is laid out in a column: set_floating_panels
        # re-mounts the whole host (every remaining panel is re-parented and
        # the column re-lays out) and the widget comes out of that pass with
        # a collapsed height — floating a tall column panel by panel left
        # windows 7px tall, with the panel squashed inside them.
        original = QSize(widget.size())
        reference = getattr(self, "floating_reference_size", None)
        if callable(reference):
            supplied = reference(panel_id, widget)
            if supplied is not None:
                original = QSize(supplied)
        if fallback_size is not None:
            fallback = (QSize(*fallback_size) if isinstance(fallback_size, (tuple, list))
                        else QSize(fallback_size))
            if original.width() <= 1 or original.height() <= 1:
                original = QSize(fallback)
            else:
                # Only the axis the widget cannot know about is taken: a
                # panel keeps its own height, but its width is whatever its
                # container gives it.
                if original.width() < max(60, int(fallback.width() * 0.6)):
                    original.setWidth(fallback.width())
        # Last resort for a widget that never got laid out (never shown, or
        # just adopted): its own hint, which is what the column sizes it by.
        hint = widget.sizeHint()
        if original.width() <= 1 or original.height() <= 1:
            original = QSize(hint)
        # A squashed column must not decide how tall the torn-off window is.
        # Height is the axis that collapses (width comes from the window), so
        # only that one is reconciled — a panel the user deliberately made
        # shorter than its hint keeps its size.
        if hint.height() > 0 and original.height() < max(
                12, int(hint.height() * 0.6)):
            original.setHeight(hint.height())
        # Same for a width that is clearly not a laid-out width: a panel that
        # was just mounted into another window reports Qt's default 100px
        # until that window's layout has run, and tearing it out in that
        # window-sized 100px moment is what made a torn-off panel 100px wide.
        if hint.width() > 0 and original.width() < max(
                60, int(hint.width() * 0.6)):
            original.setWidth(hint.width())
        spec = registry.panel(panel_id)
        saved = store.load_floating_from(getattr(self, "cfg", None))
        window = FloatingPanelWindow(
            panel_id, spec.title if spec else panel_id, self,
            no_focus=bool(getattr(self, "cfg", {}).get("noFocusMode", False)))
        window.dock_requested.connect(self.dock_panel)
        window.dropped_at.connect(self._floating_dropped)
        window.moving_at.connect(self._floating_moving)
        window.menu_requested.connect(self.show_panel_menu)
        window.panel_float_requested.connect(self._on_floating_panel_float_requested)
        window.panel_dropped_here.connect(
            lambda pid, tgt, w=window: self._on_panel_dropped_into_floating(pid, tgt, w))
        window.set_allow_tab_drops(bool(getattr(self, "cfg", {}).get("slidersTabs", False)))
        window.set_drag_enabled(bool(getattr(self, "cfg", {}).get("panelDrag", False)))
        chrome = getattr(self, "_floating_chrome", None)
        if chrome is not None:
            window.apply_chrome(chrome)
        windows[panel_id] = window
        # Unmount first: the host detaches every panel it holds when it
        # re-mounts, and it would pull this one straight back out of the
        # window we are about to put it in. No re-mount here — the refresh at
        # the end of this method is the pass that mounts the column.
        host.set_floating_panels(set(windows), remount=False)
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
        if panel_id in saved and getattr(saved[panel_id], "user_resized", False):
            # Restoring a window the user sized by hand: mark it before any
            # content pass runs, so this one does not close it back down.
            window._user_resized = True
        window.set_panel(widget)
        # The content hint this window must fit is the size the panel had in
        # the column — the host has just re-mounted everything, so reading it
        # back from the widget now would measure the collapsed column instead
        # (that is how a 47px block became a 22px window).
        window.adjust_size_for_content(min_content_h=original.height())
        # A panel whose content is driven by the owner's geometry (the LAB
        # squares are sized by the picker pass, not by their own layout)
        # must be told how big it is *after* it lands in the window — its
        # size hint is a construction-time lie, so the layout alone would
        # leave it a stub.
        size_owner = getattr(self, "float_panel_size", None)
        if callable(size_owner):
            size_owner(panel_id, widget, rect)
        window.setGeometry(*rect)
        if panel_id in saved:
            state = saved[panel_id]
            if not state.on_top:
                window.set_always_on_top(False)
        window.show_without_stealing_focus()
        self_correct = getattr(self, "floating_self_correct", None)
        if panel_id not in saved and (not callable(self_correct)
                                      or self_correct(panel_id)):
            # Self-correct rather than trust the arithmetic: the promise is
            # "the panel keeps its size", and chrome adds up to a few pixels
            # that are easy to get wrong. Measure the result and fix it up.
            layout = window.layout()
            if layout is not None:
                layout.activate()
            if hasattr(window, "body") and window.body and window.body.layout():
                window.body.layout().activate()
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
        if refresh:
            fn = getattr(self, "refresh_slider_visibility_and_order", None)
            if callable(fn):
                # defer_refresh is for a caller that is floating several
                # panels in a row: one re-mount of the column at the end
                # instead of one per panel.
                fn(defer=bool(defer_refresh))
        return True

    def _floating_geometry(self, panel_id, size, position):
        """Where and how big the torn-off window should be.

        *size* is the panel's size in the column, measured before it left:
        a block that was 344px wide should not snap to some other width the
        moment it becomes a window. Only the chrome is added on top.
        """
        saved = store.load_floating_from(getattr(self, "cfg", None))
        if panel_id in saved:
            rect = saved[panel_id].rect
        else:
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
            rect = (point.x() - 24, point.y() - 8, width, height)
        screens = QApplication.screens()
        if screens:
            target_rect = QRect(*rect)
            if not any(s.geometry().intersects(target_rect) for s in screens):
                prim = QApplication.primaryScreen()
                if prim:
                    pg = prim.availableGeometry()
                    rect = (pg.x() + 50, pg.y() + 50, rect[2], rect[3])
        return rect

    # ── back ─────────────────────────────────────────────────────────────

    def dock_panel(self, panel_id: str, refresh: bool = True) -> bool:
        """Put a torn-off panel back into the arrangement. True when it moved."""
        windows = self.floating_windows()
        window = windows.pop(panel_id, None)
        if window is None:
            return False
        widget = window.take_panel(panel_id)
        has_remaining = any(w is window for w in windows.values())
        if not has_remaining:
            self._retire_floating_window(window)
        attach = getattr(self, "attach_floating_panel", None)
        claimed = bool(widget is not None and callable(attach)
                       and attach(panel_id, widget))
        host = getattr(self, "panel_host", None)
        fn = getattr(self, "refresh_slider_visibility_and_order", None)
        if host is not None:
            # The tree still holds its slot, so this lands it back home. Skip
            # the re-mount when the refresh below is going to do one anyway:
            # that was the second full column rebuild every dock cost. A host
            # whose owner has no refresh routine still needs the re-mount.
            host.set_floating_panels(
                set(windows), remount=not (refresh and callable(fn)))
        if (not claimed and widget is not None and host is not None
                and host.widget_for(panel_id) is None):
            # Nothing claimed it (the group is not in the current tree):
            # keep it out of limbo rather than leaking a parentless widget.
            widget.hide()
        self._save_floating_state()
        if refresh:
            if callable(fn):
                fn()
            cfg = getattr(self, "cfg", None)
            if cfg is not None:
                from core import config
                config.save_hotkey_config(cfg)
        return True

    def restore_floating_panels(self) -> None:
        """Re-open the windows that were torn off when the app last closed."""
        saved = store.load_floating_from(getattr(self, "cfg", None))
        if not saved:
            return

        groups: list[list[str]] = []
        assigned = set()

        for panel_id, state in saved.items():
            if panel_id in assigned:
                continue
            if state.tree is not None and len(state.tree.panels()) > 1:
                member_pids = [p for p in state.tree.panels() if p in saved]
                if member_pids:
                    groups.append(member_pids)
                    assigned.update(member_pids)

        for panel_id, state in saved.items():
            if panel_id in assigned:
                continue
            if state.group_id and state.group_id in saved:
                leader = state.group_id
                cluster = [leader] + [p for p, s in saved.items()
                                      if s.group_id == leader and p != leader]
                groups.append([p for p in cluster if p in saved])
                assigned.update(cluster)

        for panel_id in saved:
            if panel_id not in assigned:
                groups.append([panel_id])
                assigned.add(panel_id)

        windows = self.floating_windows()
        host = getattr(self, "panel_host", None)
        chrome = getattr(self, "_floating_chrome", None)
        no_focus = bool(getattr(self, "cfg", {}).get("noFocusMode", False))

        # Tell the MAIN host first — same rule as float_panel(): the floating
        # window adopts the panel widgets *after* this, and doing it the other
        # way round used to leave the panels listed in the main host's
        # _mounted while already living in the floating window. The host's
        # next _detach_mounted then called widget.setParent(None) on widgets
        # it no longer parented, tearing them out of the restored floating
        # window: three parentless "stray" containers whose frames still
        # claimed them (probe: restore with a 3-panel group left every
        # container parentless — tools/diag_drag_crash.py, scenario B). The
        # floating window then showed empty grips and every drag around it
        # walked corrupted bookkeeping.
        if host is not None and saved:
            host.set_floating_panels(set(saved))

        for pids in groups:
            if not pids:
                continue
            if len(pids) == 1:
                self.float_panel(pids[0], persist=False)
                continue

            leader_id = pids[0]
            leader_state = saved.get(leader_id)
            if leader_state is None:
                continue

            widgets = {}
            for pid in pids:
                w = self.panel_widget(pid)
                if w is not None:
                    widgets[pid] = w

            if not widgets:
                continue

            spec = registry.panel(leader_id)
            title = spec.title if spec else leader_id
            window = FloatingPanelWindow(
                leader_id, title, self, no_focus=no_focus)
            window.dock_requested.connect(self.dock_panel)
            window.dropped_at.connect(self._floating_dropped)
            window.moving_at.connect(self._floating_moving)
            window.menu_requested.connect(self.show_panel_menu)
            window.panel_float_requested.connect(self._on_floating_panel_float_requested)
            window.panel_dropped_here.connect(
                lambda pid, tgt, w=window: self._on_panel_dropped_into_floating(pid, tgt, w))
            window.set_allow_tab_drops(bool(getattr(self, "cfg", {}).get("slidersTabs", False)))
            window.set_drag_enabled(bool(getattr(self, "cfg", {}).get("panelDrag", False)))
            if chrome is not None:
                window.apply_chrome(chrome)

            for pid, widget in widgets.items():
                windows[pid] = window
                window._panels[pid] = widget
                widget.removeEventFilter(window)
                widget.installEventFilter(window)
                detach = getattr(self, "detach_floating_panel", None)
                if callable(detach):
                    detach(pid, widget)

            tree = leader_state.tree
            if tree is None:
                from ui.panels.tree import VERTICAL, Leaf, Split
                tree = Split(VERTICAL, tuple(Leaf(pid) for pid in widgets))
            window.set_tree(tree)

            rect = leader_state.rect
            window.setGeometry(*rect)
            # A window the user sized by hand keeps that size (the flag also
            # travels in the record); only a window that was never resized
            # hugs its content, so startup does not show a blank band under
            # the panels.
            if getattr(leader_state, "user_resized", False):
                window._user_resized = True
            window.adjust_size_for_content(shrink=True)
            if not leader_state.on_top:
                window.set_always_on_top(False)
            window.show_without_stealing_focus()
            window.geometry_changed.connect(lambda _pid: self._save_floating_state())

        if host is not None:
            host.set_floating_panels(set(windows))
        # The host just re-mounted everything it still holds, which forces
        # every panel visible; re-apply the showSliders policy (and the
        # content height that follows from it) or disabled groups appear
        # right after startup.
        fn = getattr(self, "refresh_slider_visibility_and_order", None)
        if callable(fn):
            fn()

    # ── bookkeeping ──────────────────────────────────────────────────────

    def _is_over_main_window(self, global_pos) -> bool:
        """True when global_pos falls inside the visible main window."""
        if not hasattr(self, "mapToGlobal") or not hasattr(self, "size"):
            return False
        if not self.isVisible() or self.isHidden():
            return False
        rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        return rect.contains(global_pos)

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
        """Dragging a floating window over the column or another floating window previews the landing."""
        windows = self.floating_windows()
        source_window = windows.get(panel_id)
        target_win = None
        target_pos = None

        for win in set(windows.values()):
            if self._floating_window_dead(win):
                continue
            if win is source_window or not win.isVisible() or win.isHidden():
                continue
            hp = win.host_point_from_global(global_pos)
            if hp is not None:
                target_win = win
                target_pos = win.mapFromGlobal(global_pos)
                break

        host = getattr(self, "panel_host", None)
        main_point = self._host_point(global_pos) if target_win is None else None

        for win in set(windows.values()):
            if self._floating_window_dead(win):
                continue
            if win is target_win and target_pos is not None:
                win.show_drop_hint(target_pos)
            else:
                win.clear_drop_hint()

        if host is not None:
            if target_win is None and main_point is not None:
                host.show_drop_hint(main_point)
            else:
                host.clear_drop_hint()

    def _floating_dropped(self, panel_id: str, global_pos) -> None:
        """A floating window was dragged and released."""
        from ui.panels import rearrange

        windows = self.floating_windows()
        source_window = windows.get(panel_id)
        if source_window is None:
            return

        for win in set(windows.values()):
            if self._floating_window_dead(win):
                continue
            win.clear_drop_hint()
        host = getattr(self, "panel_host", None)
        if host is not None:
            host.clear_drop_hint()

        owner_target = getattr(self, "dock_target_at", None)
        if callable(owner_target):
            owner_spot = owner_target(panel_id, global_pos)
            if owner_spot:
                self.dock_panel(panel_id)
                return

        # 1. Dropped on another floating window?
        for target_win in set(windows.values()):
            if (self._floating_window_dead(target_win)
                    or target_win is source_window
                    or not target_win.isVisible() or target_win.isHidden()):
                continue
            hp = target_win.host_point_from_global(global_pos)
            if hp is not None:
                drop_target = target_win.drop_target_at(target_win.mapFromGlobal(global_pos))
                if drop_target is not None:
                    target_pid, zone = drop_target
                    source_pids = list(source_window.panel_ids)
                    for i, pid in enumerate(source_pids):
                        widget = source_window.take_panel(pid)
                        curr_zone = zone if i == 0 else (rearrange.MERGE_PAGE if zone == rearrange.CENTER else rearrange.BOTTOM)
                        target_win.add_panel(pid, widget, target_panel_id=target_pid, zone=curr_zone)
                        windows[pid] = target_win
                        target_pid = pid
                    source_window.hide()
                    self._retire_floating_window(source_window)
                    self._save_floating_state()
                    return

        # 2. Dropped on main window?
        point = self._host_point(global_pos)
        is_over_main = (point is not None) or self._is_over_main_window(global_pos)
        if is_over_main:
            target = host.drop_target_at(point) if (host is not None and point is not None) else None
            source_pids = list(source_window.panel_ids)
            # Dock every panel first, then re-arrange, then refresh once: the
            # refresh re-mounts the whole column, so doing it per panel made a
            # single drop re-mount the column eight times.
            for pid in source_pids:
                self.dock_panel(pid, refresh=False)
            if target is not None:
                curr_target = target
                for pid in source_pids:
                    if curr_target[0] != pid:
                        self._dock_at(pid, curr_target, refresh=False)
                        curr_target = (pid, rearrange.BOTTOM if target[1] != rearrange.MERGE_PAGE else rearrange.MERGE_PAGE)
            fn = getattr(self, "refresh_slider_visibility_and_order", None)
            if callable(fn):
                fn()
            return

        # 3. Dropped in empty space
        self._save_floating_state()

    def refresh_floating_panels_settings(self) -> None:
        """Sync drag_enabled, allow_tab_drops, and theme chrome across all floating windows."""
        cfg = getattr(self, "cfg", {}) or {}
        drag_enabled = bool(cfg.get("panelDrag", False))
        allow_tab_drops = bool(cfg.get("slidersTabs", False))
        chrome = getattr(self, "_floating_chrome", None)
        for window in set(self.floating_windows().values()):
            try:
                window.set_drag_enabled(drag_enabled)
                window.set_allow_tab_drops(allow_tab_drops)
                if chrome is not None:
                    window.apply_chrome(chrome)
                # Settings can change what is on screen (tabs on/off, grips on/off):
                # follow the content down so toggling stacking never leaves a
                # ghost band under the panels.
                window.adjust_size_for_content(shrink=True)
            except (RuntimeError, AttributeError):
                # A window torn down between the snapshot and the sync
                # (drop/dock during a settings change) is gone from the map
                # next call; nothing left to sync to.
                continue

    def _dock_at(self, panel_id: str, target, refresh: bool = True) -> None:
        """Move a just-docked panel to where it was dropped.

        With *refresh* off the caller owns the re-assembly: a gesture that
        lands several panels runs one pass at the end instead of one per
        panel.
        """
        from ui.panels import rearrange

        host = getattr(self, "panel_host", None)
        if host is None:
            return
        if target[1] == getattr(rearrange, "MERGE_PAGE", None):
            # Dropped on a page's tab header: join that page rather than
            # creating a new one (the same rule as dragging inside the host).
            moved = rearrange.merge_panel_into_page(
                host.tree(), panel_id, target[0])
        else:
            moved = rearrange.move_panel(
                host.tree(), panel_id, target[0], target[1])
        if moved == host.tree():
            return
        host.set_tree(moved)
        record = getattr(self, "save_panel_layout", None)
        if callable(record):
            record(moved)
        self._save_floating_state()
        if not refresh:
            return
        # set_tree mounts every panel it holds with setVisible(True) — the
        # showSliders visibility policy must run again or groups the user
        # turned off in settings pop back on the screen.
        fn = getattr(self, "refresh_slider_visibility_and_order", None)
        if callable(fn):
            fn()

    def _on_floating_panel_float_requested(self, panel_id: str, source_window: FloatingPanelWindow) -> None:
        """A panel inside a multi-panel floating window was dragged out or double-clicked."""
        if len(source_window.panel_ids) <= 1:
            return
        # Measure the *window's* content box before the panel leaves: the
        # widget itself can still be reporting an unlaid-out default width
        # (it was just mounted into this window), and the width the user sees
        # is the one the window gave it.
        source_size = None
        body = getattr(source_window, "body", None)
        layout = body.layout() if body is not None else None
        if layout is not None:
            margins = layout.contentsMargins()
            source_size = (max(1, source_window.width() - margins.left()
                               - margins.right()), 0)
        widget = source_window.take_panel(panel_id)
        if widget is None:
            return
        windows = self.floating_windows()
        windows.pop(panel_id, None)
        pos = QCursor.pos()
        self.float_panel(panel_id, position=pos, fallback_size=source_size)

    def _on_panel_dropped_into_floating(self, panel_id: str, target: tuple,
                                        target_window: FloatingPanelWindow) -> None:
        """A panel from main window or another floating window was dropped via QDrag into target_window.

        The widget surgery is deferred until after the QDrag loop returns:
        dropping the third panel into one floating window used to delete /
        re-parent the *source* frame synchronously inside ``dropEvent``,
        while QDrag still considered it the drag source — the process then
        died with a native access violation ("拖 3 个就闪退").
        """
        from PyQt6.QtCore import QTimer

        windows = self.floating_windows()
        old_win = windows.get(panel_id)
        if old_win is target_window:
            return
        # Bookkeeping first so a second drop of the same panel cannot pass
        # through; all destructive widget work runs after the drag finishes.
        windows[panel_id] = target_window
        QTimer.singleShot(
            0, lambda: self._finish_panel_drop_into_floating(
                panel_id, target, target_window, old_win))

    def _finish_panel_drop_into_floating(self, panel_id: str, target: tuple,
                                         target_window: FloatingPanelWindow,
                                         old_win: FloatingPanelWindow | None) -> None:
        windows = self.floating_windows()
        try:
            if old_win is not None:
                widget = old_win.take_panel(panel_id)
                # Retire only when NO other panel still maps to this window.
                # The old `if w is not old_win` filter excluded the very
                # window the any() was searching for, so it was always
                # False — a window that still held other panels got hidden
                # underneath them.
                if not any(w is old_win for w in windows.values()):
                    self._retire_floating_window(old_win)
            else:
                widget = self.panel_widget(panel_id)
                detach = getattr(self, "detach_floating_panel", None)
                if callable(detach):
                    detach(panel_id, widget)
        except RuntimeError:
            # old_win was torn down before the deferred finish ran; the panel
            # is already remapped, so fall through to the main-host remount.
            widget = None

        if widget is None:
            # Nothing to move (panel vanished meanwhile): undo the remap.
            if windows.get(panel_id) is target_window:
                windows.pop(panel_id, None)
            return

        host = getattr(self, "panel_host", None)
        if host is not None:
            host.set_floating_panels(set(windows))

        target_window.add_panel(panel_id, widget, target_panel_id=target[0], zone=target[1])
        # The main host may have just re-mounted its remaining panels; re-apply
        # visibility so a hidden group cannot reappear after a drop.
        fn = getattr(self, "refresh_slider_visibility_and_order", None)
        if callable(fn):
            fn()
        self._save_floating_state()

    def _save_floating_state(self) -> None:
        from core import config

        cfg = getattr(self, "cfg", None)
        windows = self.floating_windows()
        seen_windows = set()
        floating_records = {}

        for pid, win in windows.items():
            if win in seen_windows:
                continue
            seen_windows.add(win)
            try:
                pids = list(win.panel_ids)
                if not pids:
                    pids = [pid]
                hand_sized = bool(getattr(win, "_user_resized", False))
                if len(pids) == 1:
                    floating_records[pids[0]] = store.FloatingState(
                        win.geometry_record(), win.always_on_top(),
                        user_resized=hand_sized)
                else:
                    leader = pids[0]
                    tree = win.tree()
                    for i, p in enumerate(pids):
                        floating_records[p] = store.FloatingState(
                            win.geometry_record(),
                            win.always_on_top(),
                            tree=tree if i == 0 else None,
                            group_id=leader if i > 0 else None,
                            user_resized=hand_sized)
            except (RuntimeError, AttributeError):
                # The window was torn down while this state snapshot was
                # being written (a dock/drop race); skip it — the map no
                # longer holds it and a later save will be exact.
                continue

        store.save_floating_into(cfg, floating_records)
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
        for window in list(set(self.floating_windows().values())):
            try:
                window.set_foreground_hidden(not visible)
                if visible:
                    # getattr/None defaults: a window whose C++ object was
                    # recycled by PyQt can surface a wrapper without its
                    # Python state (same class of teardown race the
                    # eventFilter guard covers). Nothing to mirror then.
                    panels = list(getattr(window, "_panels", {}).values())
                    window_panel = getattr(window, "_panel", None)
                    if window_panel is not None and window_panel not in panels:
                        panels.append(window_panel)
                    any_visible = any(not p.testAttribute(
                        Qt.WidgetAttribute.WA_WState_Hidden) for p in panels) if panels else True
                    if any_visible:
                        window.show_without_stealing_focus()
                        window.force_native_visible(True)
                else:
                    window.hide()
                    window.force_native_visible(False)
            except (RuntimeError, AttributeError):
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
        for window in set(self.floating_windows().values()):
            window.set_no_focus(enabled)
