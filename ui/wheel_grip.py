"""The grip strip for the active wheel-module page.

The wheel pages are real tab pages, but their tab strip lives outside the
picker square (so adding a grip must not shrink the circle). This is the one
strip that represents the page currently on screen: dragging it tears that
page off, double-clicking floats it, right-clicking offers the same actions as
a slider panel grip.

It carries its own MIME type: a wheel page is owned by the picker stack, not
by the slider PanelHost, so dropping the grip over the slider column must not
silently move the wheel into a slider page.
"""

from __future__ import annotations

from PyQt6.QtCore import QMimeData

from ui.panels.drag import PANEL_MIME, PanelTitleBar

WHEEL_GRIP_MIME = "application/x-colorink-wheel"


def visible_wheel_grip_height(bar) -> int:
    """Height of the wheel grip strip when visible; 0 when absent/hidden."""
    if bar is None or not bar.isVisible():
        return 0
    try:
        hint = bar.sizeHint()
        if hint is not None and hint.height() > 0:
            return int(hint.height())
    except (RuntimeError, TypeError, ValueError):
        pass
    try:
        return max(0, int(bar.height()))
    except (RuntimeError, TypeError, ValueError):
        return 0


class WheelGripBar(PanelTitleBar):
    """Drag/float strip for the wheel module currently shown."""

    def __init__(self, panel_id: str, title: str, parent=None):
        super().__init__(panel_id, title, parent,
                         closable=False, moves_window=False,
                         pinnable=False, height=16)

    def apply_chrome(self, chrome) -> None:
        super().apply_chrome(chrome)
        # PanelTitleBar.apply_chrome retunes its parent layout's spacing for
        # the grip gap. This strip shares a layout with the tab bar, so put the
        # gap back to zero — the tab bar and grip must stay edge to edge.
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().setSpacing(0)

    def set_title(self, title: str) -> None:
        self.title = title
        self.setToolTip(
            f"{title} — 拖动可拖出为独立窗口，双击也可浮出，右键有更多操作")
        self.update()

    def mime_data(self) -> QMimeData:
        data = QMimeData()
        # A normal panel payload so the slider PanelHost accepts a dropped
        # wheel page (above/below/into a tab page), while the picker tab strip
        # still recognises it by its wheel prefix.
        data.setData(PANEL_MIME, self.panel_id.encode("utf-8"))
        return data