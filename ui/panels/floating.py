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

from dataclasses import dataclass

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QStyle, QStyleOption, QVBoxLayout, QWidget

from ui.panels.drag import PanelHolder, PanelTitleBar

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
    """One panel, in a window of its own."""

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

    def __init__(self, panel_id: str, title: str, parent=None, *,
                 no_focus: bool = False):
        super().__init__(parent)
        self.panel_id = panel_id
        self._panel: QWidget | None = None
        self._no_focus = bool(no_focus)
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
        #: True while the whole palette is parked because the drawing app is
        #: not in the foreground. The panel's own show/hide still updates this
        #: window's Qt state, but only the foreground restore may show it.
        self._foreground_hidden = False
        self.setMouseTracking(True)

        self.title_bar = PanelTitleBar(
            panel_id, title, self, closable=True, moves_window=True,
            pinnable=True, height=PanelTitleBar.FLOATING_HEIGHT)
        self.title_bar.close_requested.connect(self.dock_requested.emit)
        self.title_bar.toggled.connect(self.dock_requested.emit)
        self.title_bar.pin_toggled.connect(
            lambda _panel_id, pinned: self.set_always_on_top(pinned))
        self.title_bar.dropped_at.connect(self._on_dropped)
        self.title_bar.moving_at.connect(
            lambda point: self.moving_at.emit(self.panel_id, point))
        self.title_bar.menu_requested.connect(self.menu_requested.emit)
        box = QVBoxLayout(self)
        # Nothing between the frame and the title strip: the strip *is* the
        # top of the window, exactly like the main window's own title bar.
        # apply_chrome() insets this by the theme's border width.
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.title_bar)
        # The body keeps a small inset instead, which is what is left to
        # grab for a side or bottom resize.
        self.body = QWidget(self)
        # No black base: the body must be as transparent as its parent, or a
        # semi-transparent title bar composites over an opaque black slab and
        # "opacity down" reads as "black bar".
        self.body.setAutoFillBackground(False)
        self.body.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        body_box = QVBoxLayout(self.body)
        body_box.setContentsMargins(0, 0, 0, 0)
        body_box.setSpacing(0)
        box.addWidget(self.body, 1)

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
        left, top, right, bottom = chrome.content_margins
        self.body.layout().setContentsMargins(
            int(left), int(top) + int(chrome.grip_gap), int(right), int(bottom))

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
