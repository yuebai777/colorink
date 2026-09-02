"""Shared picker session — the state widgets used to reach into the window for.

First slice of the panelisation work (see
docs/superpowers/plans/2026-09-01-window-layout-and-panelization.md).

Today several picker widgets climb up to the main window to answer questions
about each other:

* LabSquare scans window().slider_widgets and window().lab_slider to find out
  whether *something else* is being dragged, so it can render cheaply;
* ColorWheel.is_active_interaction does the same scan;
* ColorPreviewBox calls select_fg_slot() / set_active_transparent() straight
  on its parent.

The moment a panel is popped into its own window, window() is no longer the
main window and every one of those paths breaks. Routing them through one
small session object removes the assumption without changing behaviour: the
observed becomes the observer.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class ColorSession(QObject):
    """Interaction state and slot commands shared by the picker widgets."""

    # True while ANY picker/slider drag is in flight. Renderers use it to
    # pick the cheap path instead of asking every other widget.
    interactionChanged = pyqtSignal(bool)
    # A widget asking the host to switch the active slot ("fg" / "bg").
    slotRequested = pyqtSignal(str)
    # A widget asking the host to mark the active slot transparent.
    transparentRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._depth = 0

    # ── interaction ──────────────────────────────────────────────────────

    @property
    def interacting(self) -> bool:
        return self._depth > 0

    def begin_interaction(self) -> None:
        """Mark a drag as started. Nested/overlapping drags are counted."""
        self._depth += 1
        if self._depth == 1:
            self.interactionChanged.emit(True)

    def end_interaction(self) -> None:
        """Mark a drag as finished; never goes below zero."""
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self.interactionChanged.emit(False)

    def reset_interaction(self) -> None:
        """Drop every outstanding drag (a widget vanished mid-drag)."""
        if self._depth == 0:
            return
        self._depth = 0
        self.interactionChanged.emit(False)

    # ── commands ─────────────────────────────────────────────────────────

    def select_slot(self, slot: str) -> None:
        self.slotRequested.emit("bg" if slot == "bg" else "fg")

    def request_transparent(self) -> None:
        self.transparentRequested.emit()


def session_of(widget):
    """The session a widget belongs to, or None.

    Looks at the widget first, then its window, so a widget that has not been
    wired yet still works (and every existing test keeps passing).
    """
    own = getattr(widget, "_color_session", None)
    if isinstance(own, ColorSession):
        return own
    window = widget.window() if hasattr(widget, "window") else None
    host = getattr(window, "color_session", None)
    return host if isinstance(host, ColorSession) else None

def request_slot(box, slot: str) -> None:
    """Ask the session — or, without one, the legacy host — to switch slots."""
    session = session_of(box)
    if session is not None:
        session.select_slot(slot)
        return
    host = getattr(box, "_parent", None)
    name = "select_bg_slot" if slot == "bg" else "select_fg_slot"
    handler = getattr(host, name, None)
    if callable(handler):
        handler()


def request_transparent(box) -> None:
    """Ask the session — or the legacy host — to mark the slot transparent."""
    session = session_of(box)
    if session is not None:
        session.request_transparent()
        return
    handler = getattr(getattr(box, "_parent", None), "set_active_transparent", None)
    if callable(handler):
        handler()
