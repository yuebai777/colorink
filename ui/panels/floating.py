"""A panel torn off into its own window.

The B-5 half of panelisation: the arrangement (ui/panels/tree) says where a
panel *lives*, and this says it is temporarily somewhere else. The dock tree
is deliberately not touched — that is what lets a floated panel go back into
the slot it came from instead of the bottom of the column.

The window copies the main window's manners: frameless, always on top, and
optionally refusing focus, because the whole point of this app is to stay
usable while the drawing program keeps the keyboard.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QStyle, QStyleOption, QVBoxLayout, QWidget

from ui.panels.drag import PanelHolder, PanelTitleBar
from ui.panels.host import PanelHost
from ui.panels.tree import Leaf, Node, Split, Tabs

@dataclass(frozen=True, slots=True)
class PanelChrome:
    """The window's looks, as the theme pass computed them.

    Panels are part of this program whether they sit in the column or in a
    window of their own, so both wear exactly the values the main window
    wears — the same border theme, the same title band, the same chrome
    opacity, the same UI scale. Passing them as one value keeps "what a
    panel looks like" from being reassembled slightly differently in three
    places.
    """

    background: str = ""
    border_color: str = ""
    border_width: int = 0
    radius: int = 0
    text: str = ""
    bar_bg: str = ""
    bar_text: str = ""
    divider_color: str = ""
    divider_width: int = 0
    scale: float = 1.0
    font_size: int = 0
    #: Chrome alpha (0..1). The bar paints its own pixels, so it needs the
    #: raw factor — a QColor("rgba(...)") string fails to parse and paints
    #: black, which is why only the custom-painted bar (never the CSS-drawn
    #: main title bar) turned black when opacity dropped.
    opacity: float = 1.0
    #: True when the border theme wraps the frame *above* the title bar.
    title_inset: bool = False
    #: Inset the panel gets inside its container (left, top, right, bottom).
    content_margins: tuple = (4, 6, 4, 6)
    #: Breathing room between a grip strip and the panel under it.
    grip_gap: int = 4
    #: Extra space between the tab strip / window top and the first panel,
    #: in px (already scaled). The "顶部以及底部距离" setting.
    top_gap: int = 0
    #: Spacing between different panels/modules in px. The "面板之间间距" setting.
    diff_space: int = 8


#: Grab strip around a frameless window, in px.
BORDER = 4
#: Smallest a torn-off window may be dragged. Low on purpose: it exists so a
#: window cannot be squashed to nothing, not to decide how tall a panel is —
#: a short block must keep its own height when it is torn off.
MIN_FLOATING_SIZE = (120, 24)

_CURSORS = {
    "left": Qt.CursorShape.SizeHorCursor,
    "right": Qt.CursorShape.SizeHorCursor,
    "top": Qt.CursorShape.SizeVerCursor,
    "bottom": Qt.CursorShape.SizeVerCursor,
    "topleft": Qt.CursorShape.SizeFDiagCursor,
    "bottomright": Qt.CursorShape.SizeFDiagCursor,
    "topright": Qt.CursorShape.SizeBDiagCursor,
    "bottomleft": Qt.CursorShape.SizeBDiagCursor,
}


def resize_edge_at(width: int, height: int, x: int, y: int,
                   border: int = BORDER) -> str:
    """Which edge (if any) a point grabs, as "top"/"bottomleft"/…

    A frameless window has no system border to drag, so it has to hit-test
    its own. Pure arithmetic, so the eight directions can be checked without
    a window on screen.
    """
    if not (0 <= x < width and 0 <= y < height):
        return ""
    vertical = "top" if y < border else "bottom" if y >= height - border else ""
    horizontal = "left" if x < border else "right" if x >= width - border else ""
    return f"{vertical}{horizontal}"


def sip_isdeleted(obj) -> bool:
    """True when the C++ object behind *obj* is already gone."""
    if obj is None:
        return True
    try:
        from PyQt6 import sip
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


def apply_no_activate(widget, enabled: bool) -> None:
    """Force WS_EX_NOACTIVATE on (or off) a native window.

    Qt's WindowDoesNotAcceptFocus is not always enough on Windows, so the
    extended style is set directly and refreshed with SetWindowPos so the
    change takes effect immediately. A window that has no native handle yet
    is left alone — creating one early breaks translucency, and whoever
    shows the window calls this again.
    """
    try:
        if widget.windowHandle() is None:
            return
        import win32con
        import win32gui
        hwnd = int(widget.winId())
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        new_style = ex_style
        if enabled:
            new_style |= win32con.WS_EX_NOACTIVATE
        else:
            new_style &= ~win32con.WS_EX_NOACTIVATE
        if new_style != ex_style:
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new_style)
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED,
            )
    except Exception:
        pass


class FloatingPanelWindow(PanelHolder, QWidget):
    """One or more panels, in a window of their own."""

    #: The user wants this panel back in the main window.
    dock_requested = pyqtSignal(str)
    #: A window move finished: (panel id, global position of the release).
    dropped_at = pyqtSignal(str, object)
    #: The window is being dragged: (panel id, global position).
    moving_at = pyqtSignal(str, object)
    #: The title bar was right-clicked: (panel id, global position).
    menu_requested = pyqtSignal(str, object)
    #: The window settled somewhere new — worth writing down.
    geometry_changed = pyqtSignal(str)
    #: A panel was dragged out of this window: (panel id, window).
    panel_float_requested = pyqtSignal(str, object)
    #: A panel was dropped into this window: (panel id, target).
    panel_dropped_here = pyqtSignal(str, object)

    def __init__(self, panel_id: str, title: str, parent=None, *,
                 no_focus: bool = False):
        super().__init__(parent)
        self.panel_id = panel_id
        self._panel: QWidget | None = None
        self._panels: dict[str, QWidget] = {}
        self._tree: Node = Leaf(panel_id)
        self._no_focus = bool(no_focus)
        self._chrome = None
        self._drag_enabled_pref = True
        self._provider = getattr(parent, "panel_provider", None) if parent else None
        if callable(self._provider):
            self._provider_func = self._provider()
        else:
            self._provider_func = getattr(parent, "panel_widget", None) if parent else None

        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        if no_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        # The chrome background carries opacity, which only means anything
        # over a translucent window. Without these the half-transparent
        # background composites over solid black — "调透明度会变黑"。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, no_focus)
        self.setWindowTitle(title)
        self.setMinimumSize(*MIN_FLOATING_SIZE)

        self._on_top = True
        self._resize_edge = ""
        self._resize_origin = None
        self._resize_rect = None
        #: True once the user dragged a border. A hand-sized window follows
        #: content upwards only (see adjust_size_for_content).
        self._user_resized = False
        #: True while the whole palette is parked because the drawing app is
        #: not in the foreground. The panel's own show/hide still updates this
        #: window's Qt state, but only the foreground restore may show it.
        self._foreground_hidden = False
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        self.title_bar = PanelTitleBar(
            panel_id, title, self, closable=True, moves_window=True,
            pinnable=True, height=PanelTitleBar.FLOATING_HEIGHT)
        self.title_bar.close_requested.connect(self._on_close_requested)
        self.title_bar.toggled.connect(self._on_close_requested)
        self.title_bar.pin_toggled.connect(
            lambda _panel_id, pinned: self.set_always_on_top(pinned))
        self.title_bar.dropped_at.connect(self._on_dropped)
        self.title_bar.moving_at.connect(
            lambda point: self.moving_at.emit(self.panel_id, point))
        self.title_bar.menu_requested.connect(self.menu_requested.emit)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.title_bar)

        self.body = QWidget(self)
        self.body.setAutoFillBackground(False)
        self.body.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        body_box = QVBoxLayout(self.body)
        body_box.setContentsMargins(0, 0, 0, 0)
        body_box.setSpacing(0)

        self.panel_host = PanelHost(self._resolve_panel, self.body)
        self.panel_host.rearranged.connect(self._on_host_rearranged)
        self.panel_host.tab_changed.connect(self._on_host_tab_changed)
        # A rebuild detaches and re-adopts every panel; the window decides its
        # own visibility again once that is over, so a transient hide cannot
        # leave it parked off screen (the reported "抓手开关一按，浮窗就没了").
        self.panel_host.mount_finished.connect(self._resync_visibility)
        self.panel_host.float_requested.connect(
            lambda pid: self.panel_float_requested.emit(pid, self))
        self.panel_host.menu_requested.connect(self.menu_requested.emit)
        body_box.addWidget(self.panel_host)
        body_box.addStretch(1)

        box.addWidget(self.body, 1)

    # ── panel & tree management ──────────────────────────────────────────

    def _resolve_panel(self, pid: str) -> QWidget | None:
        if pid in self._panels:
            return self._panels[pid]
        if self._panel is not None and pid == self.panel_id:
            return self._panel
        if callable(self._provider_func):
            return self._provider_func(pid)
        return None

    def set_tree(self, node: Node) -> None:
        self._updating_visibility = True
        try:
            from ui.panels.rearrange import normalize_tabs_in_split
            node = normalize_tabs_in_split(node)
            self._tree = node
            pids = node.panels()
            if pids:
                self.panel_id = pids[0]
            if self.panel_host is not None:
                pref = getattr(self, "_drag_enabled_pref", True)
                self.panel_host.set_drag_enabled(bool(pref and len(pids) > 1))
            self.panel_host.set_tree(node)
            self._update_window_title()
        finally:
            self._updating_visibility = False

    def tree(self) -> Node:
        return self.panel_host.tree() if self.panel_host is not None else self._tree

    @property
    def panel_ids(self) -> tuple[str, ...]:
        if self.panel_host is not None:
            mounted = self.panel_host.mounted_panels()
            if mounted:
                return mounted
        if self._tree is not None:
            return self._tree.panels()
        return (self.panel_id,) if self.panel_id else ()

    def panels(self) -> tuple[str, ...]:
        return self.panel_ids

    def panel(self, panel_id: str | None = None) -> QWidget | None:
        if panel_id is not None:
            if self.panel_host is not None:
                w = self.panel_host.widget_for(panel_id)
                if w is not None:
                    return w
            return self._panels.get(panel_id)
        if self._panel is not None:
            return self._panel
        if self.panel_host is not None:
            w = self.panel_host.widget_for(self.panel_id)
            if w is not None:
                return w
        return next(iter(self._panels.values()), None)

    def set_panel(self, widget: QWidget, panel_id: str | None = None) -> None:
        pid = panel_id or self.panel_id
        previous = widget.parent()
        if isinstance(previous, PanelHolder) and previous is not self:
            previous.take_panel()
        if self._panel is not None and self._panel is not widget:
            self.take_panel()
        self._panel = widget
        self._panels[pid] = widget
        widget.removeEventFilter(self)
        widget.installEventFilter(self)
        self.set_tree(Leaf(pid))
        self.setVisible(not widget.isHidden())

    def take_panel(self, panel_id: str | None = None) -> QWidget | None:
        pid = panel_id or self.panel_id
        widget = self._panels.pop(pid, None)
        if widget is None and (panel_id is None or pid == self.panel_id):
            widget = self._panel
        if widget is not None:
            widget.removeEventFilter(self)
            widget.setParent(None)
            if self._panel is widget:
                self._panel = next(iter(self._panels.values()), None)
            if self._tree is not None and pid in self._tree.panels():
                from ui.panels import rearrange
                new_tree = rearrange.remove_panel(self._tree, pid)
                if new_tree is not None:
                    self.set_tree(new_tree)
                    self.adjust_size_for_content(shrink=True)
                else:
                    self._tree = Leaf(self.panel_id)
                    if self.panel_host is not None:
                        self.panel_host._mounted.clear()
                        self.panel_host._root = None
        return widget

    def add_panel(self, panel_id: str, widget: QWidget | None = None,
                  target_panel_id: str | None = None, zone: str = "bottom") -> bool:
        from ui.panels import rearrange
        if widget is not None:
            self._panels[panel_id] = widget
            widget.removeEventFilter(self)
            widget.installEventFilter(self)
        current = self.tree()
        placed = current.panels()
        if not placed:
            self.set_tree(Leaf(panel_id))
            return True
        target = target_panel_id if (target_panel_id and target_panel_id in placed) else placed[-1]
        if zone == rearrange.MERGE_PAGE:
            if panel_id in placed:
                new_tree = rearrange.merge_panel_into_page(current, panel_id, target)
            else:
                if isinstance(current, Tabs):
                    pages = [list(page) for page in current.pages]
                    for p in pages:
                        if target in p:
                            p.insert(p.index(target) + 1, panel_id)
                            break
                    new_tree = Tabs((), current.current, tuple(tuple(p) for p in pages))
                else:
                    new_tree = rearrange.insert_panel(current, panel_id, target, rearrange.CENTER)
        else:
            new_tree = rearrange.move_panel(current, panel_id, target, zone)
            if new_tree == current:
                new_tree = rearrange.insert_panel(current, panel_id, target, zone)
        self.set_tree(new_tree)
        self.adjust_size_for_content()
        self.geometry_changed.emit(self.panel_id)
        return True

    def adjust_size_for_content(self, shrink: bool = False,
                                min_content_h: int = 0) -> None:
        """Fit the window to its content.

        With *shrink* the window follows the content down as well as up: a
        torn-off window that lost a panel (dragged out, docked away, a tab
        page switched to a shorter one) must close the blank band where that
        panel used to be, otherwise the window keeps a ghost of its old size.
        Without *shrink* it only grows — that is the path for adding content,
        where a window the user deliberately enlarged must not snap back.

        *min_content_h* is a floor for the content height, used by the float
        path: a widget that has just been re-parented reports a hint from
        before its layout ran, and letting that decide the window height
        squashes the panel it was supposed to show (a 47px block tore off
        into a 22px window).
        """
        if self.panel_host is None:
            return
        hint_h = max(int(min_content_h or 0), self.panel_host.column_hint())
        if hint_h <= 0:
            return
        chrome = getattr(self, "_chrome", None)
        scale = float(getattr(chrome, "scale", 1.0) or 1.0) if chrome is not None else 1.0
        border = chrome.border_width if chrome is not None else BORDER
        bar = (max(12, int(round(PanelTitleBar.FLOATING_HEIGHT * scale)))
               if chrome is not None else PanelTitleBar.FLOATING_HEIGHT)
        pad = chrome.content_margins if chrome is not None else (4, 6, 4, 6)
        gap = chrome.grip_gap if chrome is not None else 4
        top_gap = max(0, int(getattr(chrome, "top_gap", 0) or 0))
        needed_h = hint_h + bar + border * 2 + int(pad[1]) + int(gap) + top_gap * 2
        min_w = max(160, int(200 * scale))
        self.setMinimumSize(min_w, needed_h)
        if shrink and not getattr(self, "_user_resized", False):
            # Content shrank: follow it down (the window only ever gets wider
            # from a user drag, never smaller than its content).
            if self.width() < min_w or self.height() > needed_h:
                self.resize(max(self.width(), min_w), needed_h)
        elif needed_h > self.height() or min_w > self.width():
            self.resize(max(self.width(), min_w), max(self.height(), needed_h))

    def set_drag_enabled(self, enabled: bool) -> None:
        self._drag_enabled_pref = bool(enabled)
        if self.panel_host is not None:
            has_multiple = len(self.panel_ids) > 1
            self.panel_host.set_drag_enabled(enabled and has_multiple)

    def set_allow_tab_drops(self, enabled: bool) -> None:
        self._allow_tab_drops = bool(enabled)
        if self.panel_host is not None:
            self.panel_host.set_allow_tab_drops(enabled)

    # ── drop hints and drag delegation ───────────────────────────────────

    def host_point_from_global(self, global_pos: QPoint) -> QPoint | None:
        if self.panel_host is None or self.isHidden():
            return None
        rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        if not rect.contains(global_pos):
            return None
        return self.panel_host.mapFromGlobal(global_pos)

    def drop_target_at(self, local_pos: QPoint):
        if self.panel_host is None:
            return None
        host_pos = self.panel_host.mapFrom(self, local_pos)
        target = self.panel_host.drop_target_at(host_pos)
        if target is not None:
            return target
        pids = self.panel_ids
        if not pids:
            return None
        rect = self.rect()
        if not rect.contains(local_pos):
            return None
        from ui.panels import rearrange
        allow_tab = bool(getattr(self, "_allow_tab_drops", False)
                         or getattr(self.panel_host, "_allow_tab_drops", False))
        if allow_tab and rect.adjusted(10, 10, -10, -10).contains(local_pos):
            return (pids[0], rearrange.CENTER)
        if local_pos.y() < rect.height() // 3:
            return (pids[0], rearrange.TOP)
        return (pids[-1], rearrange.BOTTOM)

    def show_drop_hint(self, local_pos: QPoint):
        if self.panel_host is None:
            return None
        host_pos = self.panel_host.mapFrom(self, local_pos)
        hint = self.panel_host.show_drop_hint(host_pos)
        if hint is not None:
            return hint
        target = self.drop_target_at(local_pos)
        if target is not None:
            box = self.panel_host._panel_box(target[0])
            # Same rule as PanelHost.drop_target_at: only a live descendant
            # of the host may be mapped — mapTo() on anything else walks
            # Qt's parent chain to NULL and crashes natively.
            if (box is not None and not self.panel_host._is_deleted(box)
                    and self.panel_host._box_is_mine(box)):
                from ui.panels import rearrange
                from ui.panels.host import DropIndicator
                zone = target[1]
                effective_h = max(1, box.height())
                x, y, w, h = rearrange.drop_rect(box.width(), effective_h, zone)
                if self.panel_host._indicator is None:
                    self.panel_host._indicator = DropIndicator(self.panel_host)
                self.panel_host._indicator.setGeometry(
                    QRect(box.mapTo(self.panel_host, QPoint(x, y)), QSize(w, h)))
                self.panel_host._indicator.show()
                self.panel_host._indicator.raise_()
                return target
        return None

    def clear_drop_hint(self) -> None:
        if self.panel_host is not None:
            self.panel_host.clear_drop_hint()

    def drop_hint_rect(self):
        if self.panel_host is None:
            return None
        r = self.panel_host.drop_hint_rect()
        if r is None:
            return None
        tl = self.panel_host.mapTo(self, r.topLeft())
        return QRect(tl, r.size())

    def apply_drop(self, panel_id: str, local_pos: QPoint) -> bool:
        if self.panel_host is None:
            return False
        host_pos = self.panel_host.mapFrom(self, local_pos)
        return self.panel_host.apply_drop(panel_id, host_pos)

    def _on_host_rearranged(self, new_tree):
        self._tree = new_tree
        self._update_window_title()
        # A re-arrangement can change what is on screen (page collapses,
        # panels move into a tab that shows only one page): follow the
        # content down as well, so no ghost band is left behind.
        self.adjust_size_for_content(shrink=True)
        self.geometry_changed.emit(self.panel_id)

    def _on_host_tab_changed(self, idx: int):
        self._update_window_title()
        self.adjust_size_for_content(shrink=True)
        self.geometry_changed.emit(self.panel_id)

    def _on_close_requested(self, _pid: str = "") -> None:
        pids = self.panel_ids
        if len(pids) <= 1:
            self.dock_requested.emit(self.panel_id)
        else:
            for pid in list(pids):
                self.dock_requested.emit(pid)

    def _update_window_title(self) -> None:
        from ui.panels import registry
        pids = self.panel_ids
        if not pids:
            return
        if len(pids) == 1:
            spec = registry.panel(pids[0])
            title = spec.title if spec else pids[0]
        else:
            names = []
            for pid in pids:
                spec = registry.panel(pid)
                names.append(spec.title if spec else pid)
            title = " / ".join(names)
        self.setWindowTitle(title)
        self.title_bar.title = title
        self.title_bar.update()

    def _drag_target_alive(self) -> bool:
        """True while this window and its panel host still have C++ objects."""
        try:
            from PyQt6 import sip
            if sip.isdeleted(self):
                return False
            if self.panel_host is None or sip.isdeleted(self.panel_host):
                return False
            return True
        except Exception:
            return False

    def dragEnterEvent(self, event):
        if not getattr(self, "_drag_enabled_pref", True) or not self._drag_target_alive():
            event.ignore()
            return
        if self.panel_host is not None and self.panel_host._dragged_panel(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not getattr(self, "_drag_enabled_pref", True) or not self._drag_target_alive():
            event.ignore()
            return
        if self.show_drop_hint(event.position().toPoint()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        if not self._drag_target_alive():
            return
        self.clear_drop_hint()

    def dropEvent(self, event):
        if not getattr(self, "_drag_enabled_pref", True) or not self._drag_target_alive():
            event.ignore()
            return
        local_pos = event.position().toPoint()
        panel_id = self.panel_host._dragged_panel(event)
        if panel_id is not None:
            target = self.drop_target_at(local_pos)
            self.clear_drop_hint()
            if target is not None:
                self.panel_dropped_here.emit(panel_id, target)
                event.acceptProposedAction()
                return
        event.ignore()

    # ── placement ────────────────────────────────────────────────────────

    def geometry_record(self) -> tuple[int, int, int, int]:
        rect = self.geometry()
        return (rect.x(), rect.y(), rect.width(), rect.height())

    def show_without_stealing_focus(self) -> None:
        """Show the window the way the picker shows itself."""
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                          self._no_focus)
        self.show()
        apply_no_activate(self, self._no_focus)

    def set_foreground_hidden(self, hidden: bool) -> None:
        """Park this window because the whole palette is out of the foreground."""
        self._foreground_hidden = bool(hidden)

    def force_native_visible(self, visible: bool) -> None:
        """Make the real HWND follow *visible*.

        QWidget.hide() is a no-op for a widget Qt already believes is hidden,
        and a Tool window parented to the main window reaches exactly that
        state as soon as its owner hides — while the actual HWND keeps
        sitting on top of the drawing app (the reported bug). Win32
        ShowWindow is the only thing that reconciles the two, so the
        foreground tracker drives the native window directly.
        """
        try:
            if self.windowHandle() is None:
                # Never shown yet: Qt is the only agent, and there is nothing
                # native to reconcile.
                return
        except RuntimeError:
            # The C++ object was already destroyed (teardown path); nothing
            # to reconcile.
            return
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            if not hwnd:
                return
            win32gui.ShowWindow(
                hwnd,
                win32con.SW_SHOWNOACTIVATE if visible else win32con.SW_HIDE)
        except Exception:
            # Non-Windows or pywin32 missing: Qt show/hide is the fallback.
            if visible:
                self.show_without_stealing_focus()
            else:
                self.hide()

    def _panel_layout(self):
        return self.body.layout()

    # ── looks ────────────────────────────────────────────────────────────

    def apply_chrome(self, chrome: PanelChrome) -> None:
        """Wear the same frame, background and title band as the main window."""
        self._chrome = chrome
        border_css = (f"{chrome.border_width}px solid {chrome.border_color}"
                      if chrome.border_width > 0 else "none")
        self.setStyleSheet(
            "FloatingPanelWindow {"
            f" background-color: {chrome.background};"
            f" border: {border_css};"
            f" border-radius: {chrome.radius}px; }}")
        self.title_bar.apply_chrome(chrome)
        # Exactly the main window's own rule: the frame runs above the title
        # bar only when the border theme says so (title_bar_inset), and
        # otherwise the title strip *is* the top edge. Anything else leaves a
        # band of frame above the strip that the main window does not have.
        edge = max(0, chrome.border_width)
        self.layout().setContentsMargins(
            edge, edge if chrome.title_inset else 0, edge, edge)
        left, _, right, _ = chrome.content_margins
        has_tabs = isinstance(self.tree(), Tabs)
        top_gap = max(0, int(getattr(chrome, "top_gap", 0) or 0))
        top_margin = int(chrome.grip_gap) if has_tabs else max(int(chrome.grip_gap), top_gap)
        bottom_margin = top_gap
        self.body.layout().setContentsMargins(
            int(left), top_margin, int(right), bottom_margin)
        if self.panel_host is not None:
            self.panel_host.apply_chrome(chrome)
        self.adjust_size_for_content()

    def _mounted_panel_widgets(self) -> list:
        """The widgets this window's host currently holds, alive ones only."""
        host = getattr(self, "panel_host", None)
        if host is None or sip_isdeleted(host):
            return []
        out = []
        for pid in host.mounted_panels():
            widget = host.widget_for(pid)
            if widget is not None and not sip_isdeleted(widget):
                out.append(widget)
        return out

    def _should_be_visible(self, obj) -> bool:
        """Whether the window should follow *obj* into visibility.

        Read from the **host**, not from ``_panels``: while a panel is being
        dragged out, ``_panels`` still lists it (it is removed only by the
        deferred finish) so it looks like the window has content — and the
        other way round, a panel that just left makes the dict look empty for
        a beat and the window hides with panels still inside it. The host's
        mounted set is the same source of truth the mounting code uses, so
        window visibility and content can no longer disagree.

        Only a window whose host holds nothing hides. A window whose panels
        are all explicitly hidden by the user hides too (that is the
        module-switch case), but a panel that is merely *not visible yet* —
        freshly adopted, ancestor not shown — must not hide the window.
        """
        mounted = self._mounted_panel_widgets()
        if not mounted:
            return False
        # Content, not momentary visibility: a panel that was just re-parented
        # into this window is briefly not visible yet (its ancestors are being
        # rebuilt), and reading that as "the window is empty now" hid a window
        # that still had panels in it — tearing one panel out of a group made
        # the rest of the group disappear with it.
        #
        # The question is asked of the whole content, never of the single panel
        # whose event just fired: a tab page switch hides the outgoing page's
        # panels while the incoming page's panels appear, and following the
        # outgoing one parked the window with visible panels inside it.
        return any(not widget.isHidden() for widget in mounted)

    def eventFilter(self, obj, event):
        """A hidden panel must not leave its chrome behind."""
        from ui.panels.drag import _VISIBILITY_EVENTS
        panels_dict = getattr(self, "_panels", None)
        if panels_dict is None:
            # A destroy event can reach a FloatingPanelWindow whose C++ object
            # was already torn down while a stale panel is being deleted; PyQt
            # may hand us a partially-recycled wrapper with no Python state.
            # There is nothing to mirror, so just pass the event through.
            return QWidget.eventFilter(self, obj, event)
        panels = list(panels_dict.values())
        if self._panel is not None and self._panel not in panels:
            panels.append(self._panel)
        if obj in panels and event.type() in _VISIBILITY_EVENTS:
            if getattr(self, "_updating_visibility", False):
                return False
            if self._host_is_mounting():
                # The host is rebuilding: every panel is detached and adopted
                # again, so the Hide/Show this event carries is a transient
                # state. Mirroring it parked the window for good — the
                # reported "抓手开关一按，浮窗就没了". The window re-decides
                # when the mount finishes (mount_finished -> _resync_visibility).
                return False
            if getattr(self, "_foreground_hidden", False):
                if self.isVisible():
                    self._updating_visibility = True
                    try:
                        self.setVisible(False)
                    finally:
                        self._updating_visibility = False
                return False
            if event.type() == QEvent.Type.HideToParent:
                return False
            if event.type() == QEvent.Type.Hide and not obj.isHidden():
                return False
            should_show = self._should_be_visible(obj)
            if self.isVisible() != should_show:
                self._updating_visibility = True
                try:
                    self.setVisible(should_show)
                finally:
                    self._updating_visibility = False
        return QWidget.eventFilter(self, obj, event)

    def _resync_visibility(self) -> None:
        """Follow the content's visibility, if this window disagrees.

        Called when the host finishes a rebuild: the panels have just been
        detached and re-adopted, so the only trustworthy moment to decide
        whether this window should be on screen is after that.
        """
        if getattr(self, "_updating_visibility", False):
            return
        try:
            if getattr(self, "_foreground_hidden", False):
                should_show = False
            else:
                should_show = self._should_be_visible(None)
        except RuntimeError:
            # The C++ object went away between the signal and this call.
            return
        if self.isVisible() != should_show:
            self._updating_visibility = True
            try:
                self.setVisible(should_show)
            finally:
                self._updating_visibility = False

    def paintEvent(self, event):
        """Let the stylesheet paint a plain QWidget subclass."""
        option = QStyleOption()
        option.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget,
                                   option, painter, self)
        painter.end()

    # ── resizing ─────────────────────────────────────────────────────────

    def begin_resize(self, edge: str, global_pos) -> None:
        self._resize_edge = edge
        self._resize_origin = global_pos
        self._resize_rect = self.geometry()

    def resize_to(self, global_pos) -> None:
        """Apply a drag on the border that started at begin_resize()."""
        if not self._resize_edge or self._resize_rect is None:
            return
        start = self._resize_rect
        dx = global_pos.x() - self._resize_origin.x()
        dy = global_pos.y() - self._resize_origin.y()
        left, top = start.left(), start.top()
        right, bottom = start.right(), start.bottom()
        if "left" in self._resize_edge:
            left = min(left + dx, right - self.minimumWidth() + 1)
        if "right" in self._resize_edge:
            right = max(right + dx, left + self.minimumWidth() - 1)
        if "top" in self._resize_edge:
            top = min(top + dy, bottom - self.minimumHeight() + 1)
        if "bottom" in self._resize_edge:
            bottom = max(bottom + dy, top + self.minimumHeight() - 1)
        self.setGeometry(QRect(left, top, right - left + 1, bottom - top + 1))

    def end_resize(self) -> None:
        if self._resize_edge:
            self._resize_edge = ""
            self._resize_rect = None
            # The user chose this size on purpose. Content passes may still
            # grow the window, but they must not close it back down to the
            # content's own height — that is what dropped a deliberately
            # enlarged window to a stub on the next restart.
            self._user_resized = True
            self.geometry_changed.emit(self.panel_id)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        edge = resize_edge_at(self.width(), self.height(), point.x(), point.y())
        if edge:
            self.begin_resize(edge, event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        if self._resize_edge:
            self.resize_to(event.globalPosition().toPoint())
            return
        edge = resize_edge_at(self.width(), self.height(), point.x(), point.y())
        self.setCursor(_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))

    def mouseReleaseEvent(self, event):
        self.end_resize()

    # ── layer ────────────────────────────────────────────────────────────

    def always_on_top(self) -> bool:
        return self._on_top

    def set_always_on_top(self, enabled: bool) -> None:
        """Keep this window above everything, or let it fall behind.

        Panels are torn off to sit over the drawing app, so on top is the
        default — but a reference panel the user wants out of the way should
        be able to go behind, which is what the pin is for.
        """
        enabled = bool(enabled)
        if enabled == self._on_top:
            return
        self._on_top = enabled
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        self.title_bar.set_pinned(enabled)
        if was_visible:
            self.show_without_stealing_focus()
        self.geometry_changed.emit(self.panel_id)

    def set_no_focus(self, enabled: bool) -> None:
        """Follow the no-focus setting after the window is already open."""
        enabled = bool(enabled)
        if enabled == self._no_focus:
            return
        self._no_focus = enabled
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        else:
            flags &= ~Qt.WindowType.WindowDoesNotAcceptFocus
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, enabled)
        if was_visible:
            self.show_without_stealing_focus()

    def _on_dropped(self, global_pos) -> None:
        self.dropped_at.emit(self.panel_id, global_pos)
        self.geometry_changed.emit(self.panel_id)

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            try:
                import ctypes
                import ctypes.wintypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                    if getattr(self, "_no_focus", False):
                        # MA_NOACTIVATE (3): Do not activate this window, but
                        # deliver mouse message to child widgets.
                        return True, 3
            except Exception:
                pass
        return False, 0

