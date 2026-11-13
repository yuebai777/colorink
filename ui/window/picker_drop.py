"""Color-map drop controller and edge/center dock slots for MainWindow.

Makes the main Color Map ("色图": ``PickerTabBar`` + ``WheelGripBar`` + ``stack``)
a full drop target alongside ``panel_host``:

* Shows the same ``DropIndicator`` preview box for stacking (``CENTER``),
  above (``TOP``), below (``BOTTOM``), and side-by-side (``LEFT``/``RIGHT``)
  for both docked ``QDrag`` grips and dragged floating windows.
* Executes those drops compatibly:
  - ``CENTER`` returns ``wheel.*`` panels cleanly into ``PickerTabBar``, or
    tab-stacks slider/history panels alongside "色图" via ``center_host``.
  - ``BOTTOM`` inserts at the top of ``MainWindow.panel_host``.
  - ``TOP`` / ``LEFT`` / ``RIGHT`` mount into lightweight optional
    ``PanelHost`` edge slots that stay hidden (0px) until occupied.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from core import config
from ui.panels import rearrange, registry, store, tree as dock
from ui.panels.drag import PANEL_MIME, DropIndicator
from ui.panels.host import PanelHost
from ui.panels.transitions import (
    animate_panel_landing,
    capture_widget_snapshot,
    capture_window_snapshot,
    register_tear_snapshot,
    snap_widget_update,
    spawn_dock_ghost,
)
from ui.wheel_grip import WHEEL_GRIP_MIME

SLOT_TOP = "top"
SLOT_LEFT = "left"
SLOT_RIGHT = "right"
SLOT_CENTER = "center"
DOCK_SLOTS = (SLOT_TOP, SLOT_LEFT, SLOT_RIGHT, SLOT_CENTER)

DEFAULT_SIDE_WIDTH = 220


def _node_for_panels(pids: list[str], *, vertical: bool = True) -> dock.Node | None:
    if not pids:
        return None
    if len(pids) == 1:
        return dock.Leaf(pids[0])
    orient = dock.VERTICAL if vertical else dock.HORIZONTAL
    return dock.Split(orient, tuple(dock.Leaf(p) for p in pids))


def _insert_into_tree(
    existing: dock.Node | None,
    subtree: dock.Node,
    *,
    prepend: bool = True,
    as_tabs: bool = False,
) -> dock.Node:
    if existing is None or not existing.panels():
        return subtree
    if as_tabs:
        new_pids = list(subtree.panels())
        if isinstance(existing, dock.Tabs):
            items = tuple(dict.fromkeys([*existing.items, *new_pids]))
            pages = tuple(existing.pages) + tuple((p,) for p in new_pids if p not in existing.items)
            return dock.Tabs(items, current=max(0, len(pages) - 1), pages=pages)
        old_pids = list(existing.panels())
        items = tuple(dict.fromkeys([*old_pids, *new_pids]))
        pages = ((tuple(old_pids),) if len(old_pids) > 1 else ((old_pids[0],),)) + tuple(
            (p,) for p in new_pids if p not in old_pids
        )
        return dock.Tabs(items, current=max(0, len(pages) - 1), pages=pages)
    if prepend:
        return dock.Split(dock.VERTICAL, (subtree, existing))
    return dock.Split(dock.VERTICAL, (existing, subtree))


def _subtree_without(node: dock.Node | None, excluded: set[str]) -> dock.Node | None:
    if node is None:
        return None
    if isinstance(node, dock.Leaf):
        return None if node.panel in excluded else node
    if isinstance(node, dock.Tabs):
        items = tuple(p for p in node.items if p not in excluded)
        if not items:
            return None
        pages = tuple(
            tuple(p for p in page if p not in excluded)
            for page in node.pages
        )
        pages = tuple(pg for pg in pages if pg)
        if not pages:
            pages = tuple((p,) for p in items)
        cur = max(0, min(node.current, len(pages) - 1))
        return dock.Tabs(items, current=cur, pages=pages)
    if isinstance(node, dock.Split):
        kept = []
        for child in node.children:
            sub = _subtree_without(child, excluded)
            if sub is not None and sub.panels():
                kept.append(sub)
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        return dock.Split(
            node.orientation,
            tuple(kept),
            sizes=(),
            resizable=node.resizable,
            spacing=node.spacing,
            margins=node.margins,
        )
    return node


def load_picker_docks_config(cfg: dict | None) -> dict[str, dock.Node]:
    """Read persisted ``cfg['pickerDocks']`` slot trees cleanly."""
    if not isinstance(cfg, dict):
        return {}
    raw = cfg.get("pickerDocks")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dock.Node] = {}
    for slot in DOCK_SLOTS:
        entry = raw.get(slot)
        if not isinstance(entry, dict):
            continue
        if entry.get("version") == store.LAYOUT_VERSION and "root" in entry:
            node = dock.from_json(entry.get("root"))
        elif "kind" in entry:
            node = dock.from_json(entry)
        else:
            node = None
        if node is not None and registry.PICKER in node.panels():
            # Main picker itself cannot reside inside its own edge/center dock slots.
            continue
        if node is not None and node.panels():
            result[slot] = node
    return result


def picker_dock_panel_ids(cfg: dict | None) -> set[str]:
    """All panel ids currently saved in ``cfg['pickerDocks']``."""
    ids: set[str] = set()
    for node in load_picker_docks_config(cfg).values():
        ids.update(node.panels())
    return ids


class PickerDropController(QObject):
    """Coordinates Color Map drop preview, edge/stack hosts, and persistence."""

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner
        self._hosts: dict[str, PanelHost] = {}
        self._containers: dict[str, QWidget] = {}
        self._indicator: DropIndicator | None = None
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(20)
        self._leave_timer.timeout.connect(self._clear_indicator_now)
        self._picker_attached_home = False
        self._side_width_extra = 0
        self._suppress_dedup = False
        self._pending_restore = isinstance(
            getattr(owner, "cfg", None), dict
        ) and bool(owner.cfg.get("pickerDocks"))

        self._build_workspace()
        self._install_drop_filters()

    # ── UI Construction ──────────────────────────────────────────────────

    def _build_host(self, slot: str) -> PanelHost:
        host = PanelHost(self.owner.panel_provider(), self.owner)
        # Edge/center hosts start empty; PanelHost defaults to default_tree().
        host._tree = dock.Split(dock.VERTICAL, ())
        host.set_allow_external_drops(True)
        host.set_external_drop_filter(self._can_host_accept_external)
        host.rearranged.connect(lambda tree, s=slot: self._on_host_rearranged(s, tree))
        host.tab_changed.connect(lambda idx, s=slot: self._on_host_tab_changed(s, idx))
        host.mount_changed.connect(lambda s=slot: self._on_host_mount_changed(s))
        host.float_requested.connect(self._on_dock_float_requested)
        host.menu_requested.connect(self.owner.show_panel_menu)
        return host

    def _build_workspace(self) -> None:
        owner = self.owner
        main_layout = owner.main_layout
        stack = owner.stack
        stack_idx = main_layout.indexOf(stack)
        if stack_idx < 0:
            stack_idx = main_layout.count()

        # 1. Top edge dock (above the color map), hidden until populated
        self.top_container = QWidget(owner.centralWidget())
        top_layout = QVBoxLayout(self.top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)
        self.top_host = self._build_host(SLOT_TOP)
        top_layout.addWidget(self.top_host)
        self.top_container.hide()
        main_layout.insertWidget(stack_idx, self.top_container)

        # 2. Middle row: [left_dock | picker_column | right_dock], stays hidden
        #    until at least one edge/center dock is visible so owner.stack
        #    remains a direct child of centralWidget() by default.
        self.middle_row = QWidget(owner.centralWidget())
        mid_layout = QHBoxLayout(self.middle_row)
        mid_layout.setContentsMargins(0, 0, 0, 0)
        mid_layout.setSpacing(4)

        self.left_container = QWidget(self.middle_row)
        left_layout = QVBoxLayout(self.left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.left_host = self._build_host(SLOT_LEFT)
        left_layout.addWidget(self.left_host)
        self.left_container.hide()
        mid_layout.addWidget(self.left_container)

        self.picker_column = QWidget(self.middle_row)
        col_layout = QVBoxLayout(self.picker_column)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(2)

        self.center_tab_bar = QTabBar(self.picker_column)
        self.center_tab_bar.setObjectName("PickerDockTabBar")
        self.center_tab_bar.setDrawBase(False)
        self.center_tab_bar.setExpanding(False)
        self.center_tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.center_tab_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.center_tab_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.center_tab_bar.setAcceptDrops(True)
        self.center_tab_bar.currentChanged.connect(self._on_center_tab_changed)
        self.center_tab_bar.hide()
        col_layout.addWidget(self.center_tab_bar)

        self.picker_inner_stack = QStackedWidget(self.picker_column)
        self.picker_home = QWidget(self.picker_inner_stack)
        home_layout = QVBoxLayout(self.picker_home)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.setSpacing(0)
        self.picker_inner_stack.addWidget(self.picker_home)

        self.center_container = QWidget(self.picker_inner_stack)
        center_layout = QVBoxLayout(self.center_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        self.center_host = self._build_host(SLOT_CENTER)
        center_layout.addWidget(self.center_host)
        self.picker_inner_stack.addWidget(self.center_container)

        col_layout.addWidget(self.picker_inner_stack, 1)
        mid_layout.addWidget(self.picker_column, 1)

        self.right_container = QWidget(self.middle_row)
        right_layout = QVBoxLayout(self.right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.right_host = self._build_host(SLOT_RIGHT)
        right_layout.addWidget(self.right_host)
        self.right_container.hide()
        mid_layout.addWidget(self.right_container)

        self.middle_row.hide()
        after_stack = main_layout.indexOf(stack) + 1
        main_layout.insertWidget(after_stack, self.middle_row, 1)

        self._hosts = {
            SLOT_TOP: self.top_host,
            SLOT_LEFT: self.left_host,
            SLOT_RIGHT: self.right_host,
            SLOT_CENTER: self.center_host,
        }
        self._containers = {
            SLOT_TOP: self.top_container,
            SLOT_LEFT: self.left_container,
            SLOT_RIGHT: self.right_container,
            SLOT_CENTER: self.center_container,
        }

    def _drop_targets(self) -> tuple[QWidget, ...]:
        owner = self.owner
        candidates = (
            owner.stack,
            owner.picker_tab_container,
            self.center_tab_bar,
            getattr(owner, "preview_box", None),
        )
        return tuple(target for target in candidates if target is not None)

    def _install_drop_filters(self) -> None:
        for target in self._drop_targets():
            target.setAcceptDrops(True)
            target.installEventFilter(self)
        tab_bar = getattr(self.owner, "picker_tab_bar", None)
        if tab_bar is not None and hasattr(tab_bar, "set_drop_controller"):
            tab_bar.set_drop_controller(self)

    # ── Host Queries ─────────────────────────────────────────────────────

    def edge_hosts(self) -> tuple[PanelHost, ...]:
        return tuple(self._hosts[s] for s in DOCK_SLOTS)

    def all_docked_hosts(self) -> tuple[PanelHost, ...]:
        main_host = getattr(self.owner, "panel_host", None)
        if main_host is None:
            return self.edge_hosts()
        return (main_host, *self.edge_hosts())

    def slot_of(self, host: PanelHost) -> str | None:
        for slot, h in self._hosts.items():
            if h is host:
                return slot
        return None

    def owns_panel(self, panel_id: str) -> bool:
        for host in self._hosts.values():
            if host.widget_for(panel_id) is not None or panel_id in host.tree().panels():
                return True
        return False

    def host_owning(self, panel_id: str) -> PanelHost | None:
        for host in self.all_docked_hosts():
            if host.widget_for(panel_id) is not None or panel_id in host.tree().panels():
                return host
        return None

    def edge_host_point_at(
        self,
        global_pos: QPoint,
        panel_ids: tuple[str, ...],
        *,
        from_floating: bool = True,
    ) -> tuple[PanelHost, QPoint] | None:
        """Return ``(edge_host, point_in_host)`` when *global_pos* is over a visible dock host."""
        if not panel_ids or registry.LAB_FLOAT_ID in panel_ids:
            return None
        drag_mode = bool(getattr(self.owner, "cfg", {}).get("panelDrag", False))
        if from_floating and not drag_mode:
            return None
        for slot in DOCK_SLOTS:
            host = self._hosts[slot]
            if host is self.center_host and self.center_shows_picker():
                continue
            cont = self._containers[slot]
            if not cont.isVisible() or not host.isVisible():
                continue
            local = host.mapFromGlobal(global_pos)
            if host.rect().contains(local):
                return (host, local)
        return None

    def has_any_docks(self) -> bool:
        return any(bool(h.visible_panels()) for h in self._hosts.values())

    def has_visible_center_tabs(self) -> bool:
        return self.center_tab_bar.isVisible() and self.center_tab_bar.count() > 1

    def extra_top_height(self) -> int:
        extra = 0
        if self.top_container.isVisible():
            hint = max(self.top_host.column_hint(), self.top_container.sizeHint().height())
            if hint > 0:
                extra += hint + max(0, self.owner.main_layout.spacing())
        if self.center_tab_bar.isVisible():
            extra += self.center_tab_bar.sizeHint().height() + 2
        return extra

    def extra_side_width(self) -> int:
        extra = 0
        sp = max(0, self.middle_row.layout().spacing())
        if self.left_container.isVisible():
            w = max(self.left_container.width(), self.left_container.minimumWidth(), 160)
            extra += w + sp
        if self.right_container.isVisible():
            w = max(self.right_container.width(), self.right_container.minimumWidth(), 160)
            extra += w + sp
        return extra

    def left_side_offset(self) -> int:
        if not self.left_container.isVisible():
            return 0
        sp = max(0, self.middle_row.layout().spacing())
        w = max(self.left_container.width(), self.left_container.minimumWidth(), 160)
        return w + sp

    def center_shows_picker(self) -> bool:
        return (not self._picker_attached_home) or self.picker_inner_stack.currentIndex() == 0

    def _can_host_accept_external(self, panel_id: str) -> bool:
        if panel_id == registry.LAB_FLOAT_ID:
            return False
        if panel_id in self.owner.floating_windows():
            return False
        if registry.is_wheel_panel(panel_id):
            return True
        main_host = getattr(self.owner, "panel_host", None)
        if main_host is not None and (
            main_host.widget_for(panel_id) is not None or panel_id in main_host.tree().panels()
        ):
            return True
        return self.owns_panel(panel_id)

    # ── Drop Zone Detection & Indicator Preview ──────────────────────────

    @staticmethod
    def _dragged_panel_from_mime(mime) -> str | None:
        if mime is None:
            return None
        for fmt in (PANEL_MIME, WHEEL_GRIP_MIME):
            if mime.hasFormat(fmt):
                text = bytes(mime.data(fmt)).decode("utf-8", "ignore").strip()
                if text:
                    return text
        return None

    def _picker_bounds_in_owner(self, *, allow_collapsed: bool = False) -> QRect | None:
        owner = self.owner
        stack = getattr(owner, "stack", None)
        ptc = getattr(owner, "picker_tab_container", None)
        rects: list[QRect] = []
        if stack is not None and stack.isVisible() and stack.width() > 0 and stack.height() > 0:
            tl = stack.mapTo(owner, QPoint(0, 0))
            rects.append(QRect(tl, stack.size()))
        if ptc is not None and ptc.isVisible() and ptc.width() > 0 and ptc.height() > 0:
            tl = ptc.mapTo(owner, QPoint(0, 0))
            rects.append(QRect(tl, ptc.size()))
        if self.center_tab_bar.isVisible() and self.center_tab_bar.width() > 0:
            tl = self.center_tab_bar.mapTo(owner, QPoint(0, 0))
            rects.append(QRect(tl, self.center_tab_bar.size()))
        if rects:
            out = rects[0]
            for r in rects[1:]:
                out = out.united(r)
            return out
        if allow_collapsed and owner.isVisible():
            top_y = 8
            tbar = getattr(owner, "title_bar", None)
            if tbar is not None and tbar.isVisible():
                top_y = tbar.mapTo(owner, QPoint(0, tbar.height())).y()
            w = max(120, owner.width() - 16)
            h = max(90, min(180, max(120, owner.height() - top_y - 8)))
            return QRect(8, top_y, w, h)
        return None

    def _stack_rect_in_owner(self) -> QRect | None:
        stack = getattr(self.owner, "stack", None)
        if stack is not None and stack.isVisible() and stack.width() > 0 and stack.height() > 0:
            return QRect(stack.mapTo(self.owner, QPoint(0, 0)), stack.size())
        return self._picker_bounds_in_owner(allow_collapsed=True)

    def _can_drop_onto_picker(
        self,
        panel_ids: tuple[str, ...],
        *,
        from_floating: bool,
    ) -> bool:
        if not panel_ids:
            return False
        if not from_floating and len(panel_ids) == 1 and panel_ids[0] == registry.LAB_FLOAT_ID:
            return False
        drag_mode = bool(getattr(self.owner, "cfg", {}).get("panelDrag", False))
        if not drag_mode:
            if not from_floating:
                return False
            # Compatibility: floating wheel/lab returning to picker works even with grips off.
            origins = getattr(self.owner, "_wheel_float_origin", None) or {}
            return any(
                (registry.is_wheel_panel(pid) and origins.get(pid, "picker") != "host")
                or pid == registry.LAB_FLOAT_ID
                for pid in panel_ids
            )
        return True

    def drop_target_at(
        self,
        global_pos: QPoint,
        panel_ids: tuple[str, ...],
        *,
        from_floating: bool = False,
    ) -> tuple[str, QRect] | None:
        if not self._can_drop_onto_picker(panel_ids, from_floating=from_floating):
            return None
        owner = self.owner
        local_pos = owner.mapFromGlobal(global_pos)
        has_wheel_or_lab = any(
            registry.is_wheel_panel(p) or p == registry.LAB_FLOAT_ID for p in panel_ids
        )
        bounds = self._picker_bounds_in_owner(allow_collapsed=from_floating and has_wheel_or_lab)
        if bounds is None or not bounds.contains(local_pos):
            return None

        # Check collapsed-picker return (all wheels were floated out).
        if owner.stack.isHidden():
            if from_floating and has_wheel_or_lab:
                return (rearrange.CENTER, bounds)
            return None

        # If a stacked panel inside center_host is currently visible, let
        # center_host handle drops onto that page unless hovering tab bar.
        if not self.center_shows_picker():
            pt_in_ctab = self.center_tab_bar.mapFrom(owner, local_pos)
            if self.center_tab_bar.isVisible() and self.center_tab_bar.rect().contains(pt_in_ctab):
                return (rearrange.CENTER, bounds)
            return None

        drag_mode = bool(getattr(owner, "cfg", {}).get("panelDrag", False))
        if not drag_mode:
            return (rearrange.CENTER, bounds)

        # Hovering top PickerTabBar / CenterTabBar highlights CENTER (stack).
        pt_in_tab = owner.picker_tab_bar.mapFrom(owner, local_pos)
        over_tab = (
            (owner.picker_tab_bar.isVisible() and owner.picker_tab_bar.rect().contains(pt_in_tab))
            or (
                self.center_tab_bar.isVisible()
                and self.center_tab_bar.rect().contains(self.center_tab_bar.mapFrom(owner, local_pos))
            )
        )
        stack_rect = self._stack_rect_in_owner() or bounds
        if over_tab:
            zone = rearrange.CENTER
        else:
            pt_in_stack = local_pos - stack_rect.topLeft()
            # Clamp slightly if user is on WheelGripBar between tab bar and wheel.
            if pt_in_stack.y() < 0:
                pt_in_stack = QPoint(max(0, min(stack_rect.width() - 1, pt_in_stack.x())), 0)
            zone = rearrange.zone_at(
                stack_rect.width(),
                stack_rect.height(),
                pt_in_stack.x(),
                pt_in_stack.y(),
                allow_center=True,
            )
            if zone is None:
                return None

        # Self-drop guard for the active docked wheel in PickerTabBar.
        if len(panel_ids) == 1 and registry.is_wheel_panel(panel_ids[0]) and not from_floating:
            pid = panel_ids[0]
            key = registry.wheel_panel_key(pid)
            visible_wheels = list(owner._wheel_visible_keys()) if hasattr(owner, "_wheel_visible_keys") else []
            if key in visible_wheels and not self.owns_panel(pid) and pid not in owner.panel_host.tree().panels():
                if len(visible_wheels) <= 1:
                    return None
                if zone == rearrange.CENTER:
                    return None

        target_area = bounds if zone == rearrange.CENTER else stack_rect
        rx, ry, rw, rh = rearrange.drop_rect(target_area.width(), target_area.height(), zone)
        return (zone, QRect(target_area.x() + rx, target_area.y() + ry, rw, rh))

    def show_drop_hint(
        self,
        global_pos: QPoint,
        panel_ids: tuple[str, ...],
        *,
        from_floating: bool = False,
    ) -> tuple[str, QRect] | None:
        self._leave_timer.stop()
        hit = self.drop_target_at(global_pos, panel_ids, from_floating=from_floating)
        if hit is None:
            self._clear_indicator_now()
            return None
        zone, rect = hit
        if self._indicator is None:
            self._indicator = DropIndicator(self.owner)
        self._indicator.show_drop(rect, zone=zone)
        return hit

    def hide_drop_hint(self, *, immediate: bool = False) -> None:
        if immediate:
            self._leave_timer.stop()
            self._clear_indicator_now()
        else:
            self._leave_timer.start()

    def _clear_indicator_now(self) -> None:
        if self._indicator is not None:
            self._indicator.hide()

    # ── Event Filter for Native QDrag over Main Color Map ────────────────

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype not in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        ):
            return super().eventFilter(obj, event)

        if obj not in self._drop_targets():
            return super().eventFilter(obj, event)

        if etype == QEvent.Type.DragLeave:
            self.hide_drop_hint(immediate=False)
            return False

        panel_id = self._dragged_panel_from_mime(event.mimeData())
        if not panel_id:
            return False

        # Translate event local point -> global point robustly even offscreen.
        local_pt = event.position().toPoint()
        global_pt = obj.mapToGlobal(local_pt)

        if etype in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            hit = self.show_drop_hint(global_pt, (panel_id,), from_floating=False)
            if hit is not None:
                event.acceptProposedAction()
                return True
            event.ignore()
            return True

        if etype == QEvent.Type.Drop:
            self.hide_drop_hint(immediate=True)
            hit = self.drop_target_at(global_pt, (panel_id,), from_floating=False)
            if hit is None:
                event.ignore()
                return True
            zone, _ = hit
            event.acceptProposedAction()
            # Defer widget reparenting cleanly past QDrag.exec() unwind!
            QTimer.singleShot(0, lambda pid=panel_id, z=zone: self.apply_panel_drop((pid,), z))
            return True

        return False

    # ── Drop Execution ───────────────────────────────────────────────────

    def apply_floating_drop(self, source_window, global_pos: QPoint) -> bool:
        """Handle a FloatingPanelWindow released over the Main Color Map."""
        pids = tuple(source_window.panel_ids)
        if not pids:
            return False
        hit = self.drop_target_at(global_pos, pids, from_floating=True)
        if hit is None:
            return False
        zone, hint_rect = hit
        self.hide_drop_hint(immediate=True)

        snapshot = capture_window_snapshot(source_window)
        try:
            source_window.set_drag_dimmed(False)
        except Exception:
            pass

        self._detach_floating_source(source_window, pids)
        target_widget = self._place_panels_at_zone(pids, zone, from_floating=True)

        self._finalize_arrangement(touched_wheel=any(registry.is_wheel_panel(p) for p in pids))

        if snapshot is not None:
            if target_widget is not None and target_widget.isVisible():
                dest_global = QRect(target_widget.mapToGlobal(QPoint(0, 0)), target_widget.size())
            else:
                dest_global = QRect(self.owner.mapToGlobal(hint_rect.topLeft()), hint_rect.size())
            spawn_dock_ghost(
                snapshot,
                dest_global,
                no_focus=bool(getattr(self.owner, "cfg", {}).get("noFocusMode", True)),
            )
        self._schedule_landing(pids[-1], target_widget)
        return True

    def _snapshot_for_panel(self, pid: str):
        owner = self.owner
        dock_host = self.host_owning(pid)
        if dock_host is not None:
            box = dock_host.frame_for(pid) or dock_host.widget_for(pid)
            snap = capture_widget_snapshot(box)
            if snap is not None:
                return snap
        main_host = getattr(owner, "panel_host", None)
        if main_host is not None:
            box = main_host.frame_for(pid) or main_host.widget_for(pid)
            snap = capture_widget_snapshot(box)
            if snap is not None:
                return snap
        if registry.is_wheel_panel(pid):
            snap = capture_widget_snapshot(getattr(owner, "stack", None))
            if snap is not None:
                return snap
        return capture_widget_snapshot(owner.panel_widget(pid))

    def _schedule_landing(self, pid: str, fallback_widget: QWidget | None = None, snapshot=None) -> None:
        import weakref

        ctrl_ref = weakref.ref(self)
        fallback_ref = weakref.ref(fallback_widget) if fallback_widget is not None else None

        def _play() -> None:
            ctrl = ctrl_ref()
            if ctrl is None:
                return
            target = None
            dock_host = ctrl.host_owning(pid)
            if dock_host is not None:
                target = dock_host._landing_target_widget(pid)
            if target is None:
                main_host = getattr(ctrl.owner, "panel_host", None)
                if main_host is not None:
                    target = main_host._landing_target_widget(pid)
            if target is None and fallback_ref is not None:
                fb = fallback_ref()
                if fb is not None and fb.isVisible():
                    target = fb
            if target is None and registry.is_wheel_panel(pid):
                stack = getattr(ctrl.owner, "stack", None)
                if stack is not None and stack.isVisible():
                    target = stack
            if target is not None:
                animate_panel_landing(target, start_snapshot=snapshot)

        QTimer.singleShot(0, _play)

    def apply_panel_drop(self, panel_ids: tuple[str, ...], zone: str) -> bool:
        """Move docked panel(s) into *zone* around or stacked onto Color Map."""
        if not panel_ids:
            return False
        snapshot = self._snapshot_for_panel(panel_ids[-1])
        # Cleanly remove from previous docked owners before re-inserting.
        for pid in panel_ids:
            self._remove_panel_from_current_location(pid, for_picker_zone=zone)

        target_widget = self._place_panels_at_zone(panel_ids, zone, from_floating=False)
        self._finalize_arrangement(touched_wheel=any(registry.is_wheel_panel(p) for p in panel_ids))
        self._schedule_landing(panel_ids[-1], target_widget, snapshot=snapshot)
        return True

    def _detach_floating_source(self, source_window, pids: tuple[str, ...]) -> None:
        owner = self.owner
        windows = owner.floating_windows()
        pages = getattr(owner, "_floating_wheel_pages", {})
        previews = getattr(owner, "_floating_preview_boxes", None)
        restore_opts = getattr(owner, "_restore_wheel_page_options", None)
        for pid in pids:
            windows.pop(pid, None)
            widget = source_window.take_panel(pid)
            page = pages.pop(pid, None)
            if page is not None:
                if callable(restore_opts) and registry.is_wheel_panel(pid):
                    key = registry.wheel_panel_key(pid)
                    if key:
                        restore_opts(key, page)
                widget = page.take_panel()
                if previews is not None and page.preview in previews:
                    previews.remove(page.preview)
                page.deleteLater()
            attach = getattr(owner, "attach_floating_panel", None)
            if widget is not None and callable(attach):
                attach(pid, widget)
        owner._retire_floating_window(source_window)
        main_host = getattr(owner, "panel_host", None)
        if main_host is not None:
            main_host.set_floating_panels(set(windows), remount=False)
        owner._save_floating_state()

    def apply_floating_drop_to_edge_host(
        self,
        source_window,
        target_host: PanelHost,
        host_point: QPoint,
        global_pos: QPoint,
    ) -> bool:
        """Merge or insert a dragged ``FloatingPanelWindow`` into an active edge/center host."""
        pids = tuple(source_window.panel_ids)
        if not pids:
            return False
        drop_target = target_host.drop_target_at(host_point)
        snapshot = capture_window_snapshot(source_window)
        try:
            source_window.set_drag_dimmed(False)
        except Exception:
            pass
        target_host.clear_drop_hint()
        self.hide_drop_hint(immediate=True)

        self._detach_floating_source(source_window, pids)
        origins = getattr(self.owner, "_wheel_float_origin", None)
        for pid in pids:
            if registry.is_wheel_panel(pid) and isinstance(origins, dict):
                origins[pid] = "host"

        if len(pids) == 1:
            subtree = dock.Leaf(pids[0])
        elif bool(getattr(self.owner, "cfg", {}).get("slidersTabs", False)):
            subtree = dock.Tabs(tuple(pids), current=0, pages=(tuple(pids),))
        else:
            subtree = dock.Split(dock.VERTICAL, tuple(dock.Leaf(p) for p in pids))

        slot = self.slot_of(target_host) or SLOT_TOP
        existing = target_host.tree()
        if drop_target is not None and existing.panels():
            target_pid, zone = drop_target
            if zone == getattr(rearrange, "MERGE_PAGE", None):
                updated = existing
                curr_target = target_pid
                for pid in pids:
                    updated = rearrange.merge_panel_into_page(updated, pid, curr_target)
            elif len(pids) == 1:
                updated = rearrange.move_panel(existing, pids[0], target_pid, zone)
            else:
                as_tabs = zone == rearrange.CENTER or bool(getattr(self.owner, "cfg", {}).get("slidersTabs", False))
                updated = _insert_into_tree(existing, subtree, prepend=(zone in (rearrange.TOP, rearrange.LEFT)), as_tabs=as_tabs)
            target_host.set_tree(updated)
        else:
            merged = _insert_into_tree(existing if existing.panels() else None, subtree, prepend=(slot == SLOT_TOP), as_tabs=False)
            target_host.set_tree(merged)

        if slot == SLOT_CENTER:
            self._sync_center_tabs(select_panel=pids[0])
        self._finalize_arrangement(touched_wheel=any(registry.is_wheel_panel(p) for p in pids))

        if snapshot is not None:
            if target_host.isVisible():
                dest = QRect(target_host.mapToGlobal(QPoint(0, 0)), target_host.size())
            else:
                dest = QRect(global_pos, snapshot.size())
            spawn_dock_ghost(
                snapshot,
                dest,
                no_focus=bool(getattr(self.owner, "cfg", {}).get("noFocusMode", True)),
            )
        self._schedule_landing(pids[-1], target_host)
        return True

    def _remove_panel_from_current_location(self, pid: str, *, for_picker_zone: str) -> None:
        owner = self.owner
        # Remove from any docked host (main panel_host or edge/center dock host).
        for host in self.all_docked_hosts():
            tree = host.tree()
            if pid in tree.panels():
                new_tree = rearrange.remove_panel(tree, pid)
                if new_tree is not None and new_tree.panels():
                    host.set_tree(new_tree)
                else:
                    empty = dock.Split(dock.VERTICAL, ())
                    host.set_tree(empty)
                if host is owner.panel_host:
                    owner.save_panel_layout(host.tree())

        # Remove from PickerTabBar / wheel_stack if moving a wheel out to an edge!
        if registry.is_wheel_panel(pid) and for_picker_zone != rearrange.CENTER:
            key = registry.wheel_panel_key(pid)
            cfg = getattr(owner, "cfg", None)
            if key and isinstance(cfg, dict):
                enabled = [str(k).lower() for k in (cfg.get("wheelModuleTabs") or [])]
                if key in enabled and len(enabled) > 1:
                    enabled = [k for k in enabled if k != key]
                    cfg["wheelModuleTabs"] = enabled
                    if getattr(owner, "_current_module", None) == key and enabled:
                        owner._apply_module(enabled[0])
                    else:
                        owner._sync_wheel_stack()
            origins = getattr(owner, "_wheel_float_origin", None)
            if isinstance(origins, dict):
                origins[pid] = "host"

    def _place_panels_at_zone(self, pids: tuple[str, ...], zone: str, *, from_floating: bool) -> QWidget | None:
        owner = self.owner
        wheel_pids = [p for p in pids if registry.is_wheel_panel(p)]
        other_pids = [p for p in pids if not registry.is_wheel_panel(p) and p != registry.LAB_FLOAT_ID]

        # If a floating LAB_FLOAT_ID lands on picker CENTER, close floating LAB copy.
        if registry.LAB_FLOAT_ID in pids and from_floating:
            close_lab = getattr(owner, "_close_lab_float", None)
            if callable(close_lab):
                close_lab()
            stack = getattr(owner, "stack", None)
            if stack is not None and stack.count() > 1 and stack.currentIndex() != 1:
                toggle = getattr(owner, "toggle_picker_mode", None)
                if callable(toggle):
                    toggle()

        if zone == rearrange.CENTER:
            # Dock wheel panels straight back into PickerTabBar!
            for pid in wheel_pids:
                key = registry.wheel_panel_key(pid)
                origins = getattr(owner, "_wheel_float_origin", None)
                if isinstance(origins, dict):
                    origins.pop(pid, None)
                if key:
                    self._dock_wheel_key_into_picker(key)
            if other_pids:
                node = _node_for_panels(other_pids, vertical=True)
                if node is not None:
                    existing = self.center_host.tree()
                    merged = _insert_into_tree(
                        existing if existing.panels() else None,
                        node,
                        prepend=False,
                        as_tabs=True,
                    )
                    self.center_host.set_tree(merged)
                    self._sync_center_tabs(select_panel=other_pids[-1])
                return self.center_container
            return getattr(owner, "stack", None)

        all_dockable = [*wheel_pids, *other_pids]
        subtree = _node_for_panels(all_dockable, vertical=True)
        if subtree is None:
            return None

        if zone == rearrange.BOTTOM:
            main_host = owner.panel_host
            existing = main_host.tree()
            merged = _insert_into_tree(
                existing if existing.panels() else None,
                subtree,
                prepend=True,
                as_tabs=False,
            )
            main_host.set_tree(merged)
            owner.save_panel_layout(merged)
            return main_host

        slot_map = {
            rearrange.TOP: SLOT_TOP,
            rearrange.LEFT: SLOT_LEFT,
            rearrange.RIGHT: SLOT_RIGHT,
        }
        slot = slot_map.get(zone, SLOT_TOP)
        target_host = self._hosts[slot]
        existing = target_host.tree()
        as_tabs = bool(getattr(owner, "cfg", {}).get("slidersTabs", False) and existing.panels())
        merged = _insert_into_tree(
            existing if existing.panels() else None,
            subtree,
            prepend=(slot == SLOT_TOP),
            as_tabs=as_tabs,
        )
        target_host.set_tree(merged)
        return self._containers[slot]

    def _dock_wheel_key_into_picker(self, key: str) -> None:
        owner = self.owner
        from ui.window.module_defs import normalize_wheel_module_tabs

        cfg = getattr(owner, "cfg", None)
        if cfg is not None:
            enabled = [str(k).lower() for k in (cfg.get("wheelModuleTabs") or [])]
            if str(key).lower() not in enabled:
                enabled.append(str(key).lower())
                cfg["wheelModuleTabs"] = normalize_wheel_module_tabs(
                    enabled, cfg.get("wheelModuleOrder"), key
                )
        apply_mod = getattr(owner, "_apply_module", None)
        if callable(apply_mod):
            apply_mod(str(key).lower())
        elif hasattr(owner, "_sync_wheel_stack"):
            owner._sync_wheel_stack()
        stack = getattr(owner, "stack", None)
        if stack is not None and stack.currentIndex() != 0 and hasattr(owner, "toggle_picker_mode"):
            owner.toggle_picker_mode()

    # ── Inter-Host Synchronization & Visibility ──────────────────────────

    def deduplicate_hosts(self, winner_host: PanelHost) -> None:
        """Ensure panels mounted in *winner_host* no longer live in another host."""
        if self._suppress_dedup:
            return
        self._suppress_dedup = True
        try:
            taken = set(winner_host.tree().panels())
            if not taken:
                return
            changed_any = False
            for host in self.all_docked_hosts():
                if host is winner_host:
                    continue
                tree = host.tree()
                overlap = taken & set(tree.panels())
                if not overlap:
                    continue
                trimmed = _subtree_without(tree, overlap)
                host.set_tree(trimmed if trimmed is not None else dock.Split(dock.VERTICAL, ()))
                if host is self.owner.panel_host:
                    self.owner.save_panel_layout(host.tree())
                changed_any = True
            if changed_any:
                self._finalize_arrangement(touched_wheel=False)
        finally:
            self._suppress_dedup = False

    def remove_panel(self, panel_id: str) -> bool:
        """Remove *panel_id* from all optional edge/center dock hosts."""
        removed = False
        for host in self._hosts.values():
            tree = host.tree()
            if panel_id in tree.panels():
                trimmed = _subtree_without(tree, {panel_id})
                host.set_tree(trimmed if trimmed is not None else dock.Split(dock.VERTICAL, ()))
                removed = True
        if removed:
            self.sync_docks(remount=False)
        return removed

    def sync_docks(self, *, remount: bool = False) -> None:
        """Sync drag mode, tabs, floating mask, chrome, and visibility across all edge/center hosts."""
        owner = self.owner
        cfg = getattr(owner, "cfg", {}) or {}
        drag_enabled = bool(cfg.get("panelDrag", False))
        allow_tabs = bool(cfg.get("slidersTabs", False))
        floating = set(owner.floating_windows()) if hasattr(owner, "floating_windows") else set()
        chrome = getattr(owner, "_floating_chrome", None)

        for slot, host in self._hosts.items():
            host.set_floating_panels(floating, remount=False)
            host.set_drag_enabled(drag_enabled)
            host.set_allow_tab_drops(True if slot == SLOT_CENTER else allow_tabs)
            if remount and host.tree().panels():
                host.set_tree(host.tree())
            if chrome is not None:
                host.apply_chrome(chrome)
            vis = host.visible_panels()
            cont = self._containers[slot]
            if slot != SLOT_CENTER:
                cont.setVisible(bool(vis))
                if slot in (SLOT_LEFT, SLOT_RIGHT):
                    scale = float(getattr(chrome, "scale", 1.0) or 1.0) if chrome is not None else 1.0
                    min_w = max(160, int(DEFAULT_SIDE_WIDTH * scale)) if vis else 0
                    cont.setMinimumWidth(min_w)

        self._sync_picker_parent()
        self._sync_center_tabs()
        self._sync_side_window_width()
        self.save_into_config()

    def _sync_picker_parent(self) -> None:
        """Attach ``picker_tab_container`` + ``stack`` into ``middle_row`` only when
        an edge/center dock is active so default MainWindow widget geometry stays native.
        """
        owner = self.owner
        ptc = getattr(owner, "picker_tab_container", None)
        stack = getattr(owner, "stack", None)
        top_bar = getattr(owner, "top_bar_layout", None)
        main_layout = getattr(owner, "main_layout", None)
        if ptc is None or stack is None or top_bar is None or main_layout is None:
            return
        need_home = self.has_any_docks()
        if need_home == self._picker_attached_home:
            return
        home_layout = self.picker_home.layout()
        was_ptc_hidden = ptc.isHidden()
        was_stack_hidden = stack.isHidden()
        with snap_widget_update(owner):
            if need_home:
                top_bar.removeWidget(ptc)
                main_layout.removeWidget(stack)
                home_layout.insertWidget(0, ptc)
                home_layout.addWidget(stack, 1)
                self.middle_row.show()
                if not was_ptc_hidden:
                    ptc.show()
                if not was_stack_hidden:
                    stack.show()
                self._picker_attached_home = True
            else:
                self.middle_row.hide()
                home_layout.removeWidget(ptc)
                home_layout.removeWidget(stack)
                top_bar.addWidget(ptc)
                mid_idx = main_layout.indexOf(self.middle_row)
                if mid_idx < 0:
                    mid_idx = main_layout.count()
                main_layout.insertWidget(mid_idx, stack, 1)
                if not was_ptc_hidden:
                    ptc.show()
                if not was_stack_hidden:
                    stack.show()
                self._picker_attached_home = False

    def _sync_center_tabs(self, *, select_panel: str | None = None) -> None:
        host = self.center_host
        visible_pids = list(host.visible_panels())
        if not visible_pids:
            self.center_tab_bar.blockSignals(True)
            while self.center_tab_bar.count() > 0:
                self.center_tab_bar.removeTab(0)
            self.center_tab_bar.blockSignals(False)
            self.center_tab_bar.hide()
            self.picker_inner_stack.setCurrentIndex(0)
            self._sync_picker_preview_visibility()
            return

        def _title_of(pid: str) -> str:
            spec = registry.panel(pid)
            return spec.title if spec is not None else pid

        # Build clean labels matching pages inside center_host
        pages: list[tuple[str, tuple[str, ...]]] = []
        tree = host.tree()
        if isinstance(tree, dock.Tabs) and tree.pages:
            for page in tree.pages:
                vis_in_page = tuple(p for p in page if p in visible_pids)
                if vis_in_page:
                    title = "/".join(_title_of(p) for p in vis_in_page)
                    pages.append((title, vis_in_page))
        elif visible_pids:
            title = "/".join(_title_of(p) for p in visible_pids)
            pages.append((title, tuple(visible_pids)))

        # Suppress duplicate inner PanelTabWidget tab bar when mirrored above.
        for tab_widget, _ in getattr(host, "_tabs", ()):
            if tab_widget is not None and tab_widget.tabBar() is not None:
                tab_widget.tabBar().setVisible(False)

        prev_idx = self.center_tab_bar.currentIndex()
        self.center_tab_bar.blockSignals(True)
        while self.center_tab_bar.count() > 0:
            self.center_tab_bar.removeTab(0)
        self.center_tab_bar.addTab("色图")
        target_idx = prev_idx if 0 <= prev_idx <= len(pages) else 0
        for i, (title, page_pids) in enumerate(pages, start=1):
            self.center_tab_bar.addTab(title)
            if select_panel and select_panel in page_pids:
                target_idx = i
        target_idx = max(0, min(target_idx, self.center_tab_bar.count() - 1))
        self.center_tab_bar.setCurrentIndex(target_idx)
        self.center_tab_bar.blockSignals(False)
        self.center_tab_bar.show()
        self._apply_center_tab_index(target_idx)

    def _apply_center_tab_index(self, idx: int) -> None:
        if idx <= 0:
            self.picker_inner_stack.setCurrentIndex(0)
        else:
            self.picker_inner_stack.setCurrentIndex(1)
            for tab_widget, _ in getattr(self.center_host, "_tabs", ()):
                if tab_widget is not None and 0 <= idx - 1 < tab_widget.count():
                    tab_widget.setCurrentIndex(idx - 1)
                    break
        self._sync_picker_preview_visibility()

    def _on_center_tab_changed(self, idx: int) -> None:
        self._apply_center_tab_index(idx)
        adjust = getattr(self.owner, "_adjust_content_height", None)
        if callable(adjust):
            adjust()

    def _sync_picker_preview_visibility(self) -> None:
        owner = self.owner
        preview = getattr(owner, "preview_box", None)
        stack = getattr(owner, "stack", None)
        if preview is None or stack is None:
            return
        should_show = self.center_shows_picker() and not stack.isHidden() and owner.isVisible()
        if owner.isVisible():
            preview.setVisible(should_show)
            if should_show:
                refit = getattr(owner, "_refit_preview_box", None)
                if callable(refit):
                    refit()
                preview.raise_()

    def _sync_side_window_width(self) -> None:
        owner = self.owner
        desired_extra = self.extra_side_width()
        if desired_extra == self._side_width_extra:
            return
        delta = desired_extra - self._side_width_extra
        self._side_width_extra = desired_extra
        if owner.width() > 0:
            new_w = max(240, owner.width() + delta)
            owner.resize(new_w, owner.height())

    def apply_chrome(self, chrome) -> None:
        for host in self._hosts.values():
            host.apply_chrome(chrome)
        scale = float(getattr(chrome, "scale", 1.0) or 1.0)
        sp = max(2, int(4 * scale))
        self.middle_row.layout().setSpacing(sp)
        self.center_tab_bar.setStyleSheet(
            f"QTabBar#PickerDockTabBar::tab {{"
            f" background: transparent; color: {chrome.text};"
            f" padding: {max(2, int(3 * scale))}px {max(6, int(10 * scale))}px;"
            f" border-bottom: 2px solid transparent;"
            f"}}"
            f"QTabBar#PickerDockTabBar::tab:selected {{"
            f" background: rgba(255,255,255,0.08);"
            f" border-bottom: 2px solid palette(highlight);"
            f"}}"
        )

    # ── Host Signal Slots ────────────────────────────────────────────────

    def _on_host_rearranged(self, slot: str, new_tree: dock.Node) -> None:
        host = self._hosts[slot]
        QTimer.singleShot(0, lambda h=host: self.deduplicate_hosts(h))
        self.sync_docks()
        self._finalize_arrangement(touched_wheel=any(registry.is_wheel_panel(p) for p in new_tree.panels()))

    def _on_host_tab_changed(self, slot: str, _idx: int) -> None:
        if slot == SLOT_CENTER:
            self._sync_center_tabs()
        self.save_into_config()

    def _on_host_mount_changed(self, _slot: str) -> None:
        self.sync_docks()
        adjust = getattr(self.owner, "_adjust_content_height", None)
        if callable(adjust):
            adjust()

    def _on_dock_float_requested(self, panel_id: str) -> None:
        # Capture visual before detaching so double-click/menu tear-off also animates.
        fallback_size: tuple[int, int] | None = None
        widget_fn = getattr(self.owner, "panel_widget", None)
        if callable(widget_fn):
            w = widget_fn(panel_id)
            if w is not None and w.isVisible() and w.width() > 0 and w.height() > 0:
                fallback_size = (w.width(), w.height())
            register_tear_snapshot(panel_id, capture_widget_snapshot(w))
        # Take ownership out of edge/center dock FIRST so refreshing the main
        # slider column preserves the cleaned dock tree cleanly.
        self.remove_panel(panel_id)
        self.owner.float_panel(panel_id, fallback_size=fallback_size)

    def _finalize_arrangement(self, *, touched_wheel: bool) -> None:
        owner = self.owner
        self.sync_docks()
        fn = getattr(owner, "refresh_slider_visibility_and_order", None)
        if callable(fn):
            fn()
        if touched_wheel and hasattr(owner, "_refresh_after_wheel_float"):
            owner._refresh_after_wheel_float()
        else:
            if hasattr(owner, "update_geometries"):
                owner.update_geometries()
            if hasattr(owner, "_adjust_content_height"):
                owner._adjust_content_height()
        config.save_hotkey_config(getattr(owner, "cfg", {}))

    # ── Persistence ──────────────────────────────────────────────────────

    def save_into_config(self) -> None:
        if getattr(self, "_pending_restore", False):
            return
        cfg = getattr(self.owner, "cfg", None)
        if not isinstance(cfg, dict):
            return
        data = {}
        for slot, host in self._hosts.items():
            tree = host.tree()
            if tree is not None and tree.panels():
                data[slot] = store.dump(tree)
        if data:
            cfg["pickerDocks"] = data
        else:
            cfg.pop("pickerDocks", None)

    def restore_from_config(self) -> None:
        cfg = getattr(self.owner, "cfg", None)
        docks = load_picker_docks_config(cfg)
        self._pending_restore = False
        if not docks:
            self.sync_docks()
            return
        for slot, tree in docks.items():
            # Keep only panels whose widgets currently exist and are not floating.
            floating = set(self.owner.floating_windows()) if hasattr(self.owner, "floating_windows") else set()
            valid = _subtree_without(
                tree,
                {
                    p for p in tree.panels()
                    if p in floating or self.owner.panel_widget(p) is None
                },
            )
            if valid is not None and valid.panels():
                self._hosts[slot].set_tree(valid)
        self.sync_docks()
