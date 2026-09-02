"""Drag handles: the grip strip above a panel, and the drop hint.

Only built when the user turns rearranging on — with it off the window is
byte-for-byte the classic layout, which is the whole reason the panel host
mounts bare widgets by default.

The widgets here are deliberately dumb: the grip knows which panel it
carries and how to start a drag, the frame keeps a panel and its grip
together, and *where a drop would land* is decided by ui/panels/rearrange
(pure data). Nothing here computes an arrangement.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

#: Payload of a panel drag: the panel id, UTF-8.
PANEL_MIME = "application/x-colorink-panel"

# The ToParent variants are needed for Qt bookkeeping (a panel that is
# shown while its window is still hidden reports ShowToParent, not Show),
# but they must not blindly re-show the holder: when an ancestor hides, the
# panel's own isHidden() is still False, and mirroring that would pop an
# owned, already-hidden Tool window straight back onto the screen.
_VISIBILITY_EVENTS = (
    QEvent.Type.Show, QEvent.Type.Hide,
    QEvent.Type.ShowToParent, QEvent.Type.HideToParent,
)


class PanelTitleBar(QWidget):
    """The strip you grab to move a panel.

    Deliberately thin: it is chrome the classic layout never had, so every
    pixel it takes is a pixel the sliders lose.

    Two modes, because the same strip does both jobs. Docked, dragging it
    starts a QDrag and the host decides where the panel lands; a drop nobody
    accepted means "outside every window", which is the request to float.
    In a floating window there is nothing to re-dock into, so dragging moves
    the window itself and the release position is reported instead.
    """

    HEIGHT = 16
    FLOATING_HEIGHT = 20
    _BUTTON_WIDTH = 16

    #: Docked: the drag ended somewhere no host would take it.
    float_requested = pyqtSignal(str)
    #: Floating: the × was pressed.
    close_requested = pyqtSignal(str)
    #: Floating: the pin was flipped — (panel id, stay on top).
    pin_toggled = pyqtSignal(str, bool)
    #: Floating: a window move finished at this global position.
    dropped_at = pyqtSignal(object)
    #: Floating: the window is being dragged past this global position.
    moving_at = pyqtSignal(object)
    #: Double click — the shortcut for "put it out" / "put it back".
    toggled = pyqtSignal(str)
    #: Right click — (panel id, global position) for the panel menu.
    menu_requested = pyqtSignal(str, object)

    def __init__(self, panel_id: str, title: str, parent=None, *,
                 closable: bool = False, moves_window: bool = False,
                 pinnable: bool = False, height: int | None = None):
        super().__init__(parent)
        self.panel_id = panel_id
        self.title = title
        self.closable = closable
        self.pinnable = pinnable
        self.moves_window = moves_window
        self.pinned = True
        self.setFixedHeight(int(height or self.HEIGHT))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            f"{title} — 拖动可重新排列，拖到窗口外或双击可独立成窗口"
            if not moves_window else f"{title} — 拖动移动窗口，双击收回")
        self._press = None
        self._window_offset = None
        self._base_height = int(height or self.HEIGHT)
        self._chrome = None
        # The bar's background carries opacity; it can only show through if
        # this widget is translucent itself, otherwise it composites over a
        # black base and "opacity down" turns into "black bar".
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def apply_chrome(self, chrome) -> None:
        """Wear the window theme: title band, text colour, divider, scale, gap.

        Without this a grip paints a guess from the palette, which is how it
        ends up being the only thing in the window that did not change when
        the user switched themes.
        """
        self._chrome = chrome
        frame = self.parentWidget()
        if chrome is not None and frame is not None and frame.layout() is not None:
            # The grip must not sit on top of the sliders it labels.
            frame.layout().setSpacing(int(chrome.grip_gap))
        if chrome is not None and chrome.scale:
            self.setFixedHeight(max(12, int(round(self._base_height
                                                  * chrome.scale))))
        self.update()

    def close_rect(self) -> QRect:
        """Where the × is, or an empty rect when there is none."""
        if not self.closable:
            return QRect()
        return QRect(max(0, self.width() - self._BUTTON_WIDTH), 0,
                     self._BUTTON_WIDTH, self.height())

    def pin_rect(self) -> QRect:
        """Where the pin is, or an empty rect when there is none."""
        if not self.pinnable:
            return QRect()
        offset = self._BUTTON_WIDTH * 2 if self.closable else self._BUTTON_WIDTH
        return QRect(max(0, self.width() - offset), 0,
                     self._BUTTON_WIDTH, self.height())

    def set_pinned(self, pinned: bool) -> None:
        self.pinned = bool(pinned)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(0, self.HEIGHT)

    def mime_data(self) -> QMimeData:
        """What a drag from this grip carries."""
        data = QMimeData()
        data.setData(PANEL_MIME, self.panel_id.encode("utf-8"))
        return data

    # ── dragging ─────────────────────────────────────────────────────────

    #: Presses this close to a window edge belong to the resize border.
    EDGE_SLACK = 4

    def _on_window_edge(self, point) -> bool:
        """True when this press is really aimed at the window's border.

        The strip runs edge to edge so it lines up with the frame; that puts
        it on top of the resize border, so the outermost pixels have to go
        back to the window or a flush title bar could never be resized.
        """
        if not self.moves_window:
            return False
        return (point.x() < self.EDGE_SLACK
                or point.x() >= self.width() - self.EDGE_SLACK
                or point.y() < self.EDGE_SLACK)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        if self._on_window_edge(point):
            event.ignore()
            return
        if self.close_rect().contains(point):
            self._press = None
            self.close_requested.emit(self.panel_id)
            return
        if self.pin_rect().contains(point):
            self._press = None
            self.set_pinned(not self.pinned)
            self.pin_toggled.emit(self.panel_id, self.pinned)
            return
        self._press = point
        if self.moves_window:
            window = self.window()
            self._window_offset = (event.globalPosition().toPoint()
                                   - window.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        if self.moves_window:
            if self._window_offset is not None:
                point = event.globalPosition().toPoint()
                self.window().move(point - self._window_offset)
                self.moving_at.emit(point)
            return
        moved = (event.position().toPoint() - self._press).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._press = None
        drag = QDrag(self)
        drag.setMimeData(self.mime_data())
        self._finish_reorder_drag(drag.exec(Qt.DropAction.MoveAction))

    def mouseReleaseEvent(self, event):
        was_moving = self._press is not None and self.moves_window
        self._press = None
        self._window_offset = None
        if was_moving:
            self.dropped_at.emit(event.globalPosition().toPoint())

    def contextMenuEvent(self, event):
        self.menu_requested.emit(self.panel_id, event.globalPos())

    def mouseDoubleClickEvent(self, event):
        """Double click: out of the window, or back into it.

        Dragging a panel clear of every window is not a gesture anyone
        guesses; this is the discoverable way to say the same thing.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if not (self.close_rect().contains(point)
                    or self.pin_rect().contains(point)):
                self.toggled.emit(self.panel_id)

    def _finish_reorder_drag(self, action) -> None:
        """A drag that ended: nobody took it means "float it here"."""
        if action == Qt.DropAction.IgnoreAction:
            self.float_requested.emit(self.panel_id)

    def paintEvent(self, event):
        painter = QPainter(self)
        chrome = self._chrome
        ink = (QColor(chrome.bar_text) if chrome and chrome.bar_text
               else self.palette().color(QPalette.ColorRole.WindowText))
        if chrome and chrome.bar_bg:
            bar = QColor(chrome.bar_bg)
            if chrome.opacity < 1.0:
                bar.setAlphaF(max(0.0, chrome.opacity))
            painter.fillRect(self.rect(), bar)
        else:
            painter.fillRect(self.rect(),
                             QColor(ink.red(), ink.green(), ink.blue(), 18))
        if chrome and chrome.divider_width > 0 and chrome.divider_color:
            div = QColor(chrome.divider_color)
            if chrome.opacity < 1.0:
                div.setAlphaF(max(0.0, chrome.opacity))
            painter.fillRect(0, self.height() - chrome.divider_width,
                             self.width(), chrome.divider_width, div)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 110))
        top = self.height() // 2 - 4
        for row in range(3):
            for col in range(2):
                painter.drawRect(4 + col * 3, top + row * 3, 2, 2)
        painter.setPen(QColor(ink.red(), ink.green(), ink.blue(), 220)
                       if chrome and chrome.bar_text
                       else QColor(ink.red(), ink.green(), ink.blue(), 165))
        font = painter.font()
        if chrome and chrome.font_size:
            font.setPixelSize(max(6, int(chrome.font_size)))
        else:
            font.setPointSizeF(max(6.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        used = (self._BUTTON_WIDTH * bool(self.closable)
                + self._BUTTON_WIDTH * bool(self.pinnable))
        painter.drawText(self.rect().adjusted(14, 0, -(used or 4), 0),
                         int(Qt.AlignmentFlag.AlignVCenter
                             | Qt.AlignmentFlag.AlignLeft),
                         self.title)
        pin = self.pin_rect()
        if not pin.isEmpty():
            # Filled when it stays on top, hollow when it may go behind.
            alpha = 200 if self.pinned else 90
            painter.setPen(QColor(ink.red(), ink.green(), ink.blue(), alpha))
            painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(),
                                    alpha if self.pinned else 0))
            head = pin.adjusted(5, 4, -5, -7)
            painter.drawRect(head)
            middle = head.center().x()
            painter.drawLine(middle, head.bottom(), middle, pin.bottom() - 3)
        close = self.close_rect()
        if not close.isEmpty():
            painter.setPen(QColor(ink.red(), ink.green(), ink.blue(), 190))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            box = close.adjusted(5, 6, -5, -6)
            painter.drawLine(box.topLeft(), box.bottomRight())
            painter.drawLine(box.topRight(), box.bottomLeft())
        painter.end()


class PanelHolder:
    """Shared "this widget carries one panel" behaviour.

    Both the docked frame and the floating window hold a panel and have to
    follow it when it is shown or hidden. Sharing one implementation is not
    just tidiness — the two hand panels back and forth, and a holder that
    trusts identity alone would refuse to re-adopt a panel it still lists as
    its own but no longer parents. The panel then sits parentless and
    hidden, and nothing in the UI can bring it back.
    """

    def panel(self):
        return getattr(self, "_panel", None)

    def set_panel(self, widget) -> None:
        """Adopt *widget* and mirror its visibility from now on.

        Exactly one holder owns a panel at a time. A holder that keeps
        mirroring a panel it no longer parents follows it into visibility —
        and since being detached made it a top-level widget, that shows up
        as a stray empty window next to the real one.
        """
        previous = widget.parent()
        if isinstance(previous, PanelHolder) and previous is not self:
            previous.take_panel()
        current = getattr(self, "_panel", None)
        if current is not None and current is not widget:
            self.take_panel()
        if widget.parent() is not self._panel_layout().parentWidget():
            widget.removeEventFilter(self)
            widget.installEventFilter(self)
            self._panel_layout().addWidget(widget)
        self._panel = widget
        # Read the flag only after re-parenting: setParent(None) marks a
        # widget hidden in passing, and adopting it clears that again.
        self.setVisible(not widget.isHidden())

    def _panel_layout(self):
        """Where the panel goes. Holders with chrome of their own override it."""
        return self.layout()

    def take_panel(self):
        """Hand the panel back, leaving this holder empty."""
        widget = getattr(self, "_panel", None)
        if widget is not None:
            widget.removeEventFilter(self)
            self._panel_layout().removeWidget(widget)
            widget.setParent(None)
            self._panel = None
        return widget

    def eventFilter(self, obj, event):
        """A hidden panel must not leave its chrome behind."""
        if obj is getattr(self, "_panel", None) and event.type() in _VISIBILITY_EVENTS:
            # A floating window parked by the foreground tracker ("仅在软件
            # 前台显示") stays parked no matter what the panel does on its
            # own (module switch, ancestor show) — otherwise a hidden palette
            # would leave an orphan window over the drawing app.
            if getattr(self, "_foreground_hidden", False):
                self.setVisible(False)
                return super().eventFilter(obj, event)
            # HideToParent means an ancestor hid (the main window, a tab
            # page). The holder itself is being hidden with it; do NOT
            # re-show it just because the panel's own isHidden() is still
            # False — that is exactly how a hidden owned window comes back.
            if event.type() == QEvent.Type.HideToParent:
                return super().eventFilter(obj, event)
            self.setVisible(not obj.isHidden())
        return super().eventFilter(obj, event)


class PanelFrame(PanelHolder, QWidget):
    """A panel plus its grip, so the host can mount the pair as one item."""

    def __init__(self, panel_id: str, title: str, parent=None):
        super().__init__(parent)
        self.panel_id = panel_id
        self._panel: QWidget | None = None
        self.title_bar = PanelTitleBar(panel_id, title, self)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        box.addWidget(self.title_bar)

    def sizeHint(self) -> QSize:
        """The panel's own hint plus the grip — no layout pass required.

        QLayout.sizeHint reads a phantom ~16px before the first polish, and
        the window height policy runs exactly then (see PanelHost.column_hint).
        """
        if self._panel is None:
            return QSize(0, self.title_bar.height())
        hint = self._panel.sizeHint()
        return QSize(hint.width(),
                     hint.height() + self.title_bar.height()
                     + self.layout().spacing())

    def minimumSizeHint(self) -> QSize:
        if self._panel is None:
            return QSize(0, self.title_bar.height())
        hint = self._panel.minimumSizeHint()
        return QSize(hint.width(), hint.height() + self.title_bar.height())


class DropIndicator(QWidget):
    """Where the dragged panel would land."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        painter.fillRect(self.rect(), QColor(accent.red(), accent.green(),
                                             accent.blue(), 70))
        painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 200))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()
