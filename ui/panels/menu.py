"""What a right-click on a panel's grip offers.

Pure data: the window builds a QMenu out of it and runs whatever came back.
Keeping the list here means the same set of actions can be offered from a
menu, a shortcut or a test without three copies drifting apart.

The point of this menu is reach. Tearing a panel off means dragging it
clear of every window, and putting the arrangement back means opening the
settings window and finding a button in it — neither is something a user
discovers. Right-clicking the thing you are looking at is.
"""

from __future__ import annotations

from ui.panels import registry

FLOAT = "float"
DOCK = "dock"
HIDE = "hide"
RESET = "reset"

_LABELS = {
    FLOAT: "浮出为独立窗口",
    DOCK: "收回窗口",
    HIDE: "隐藏这一组",
    RESET: "复位面板布局",
}


def visibility_key(panel_id: str) -> str | None:
    """The config switch that shows/hides this panel, if it has one."""
    group = registry.group_of(panel_id)
    return f"showSliders{group}" if group else None


def panel_menu_actions(panel_id: str, floating: bool) -> tuple:
    """The (action, label) pairs to offer for *panel_id*."""
    actions = [DOCK if floating else FLOAT]
    if visibility_key(panel_id):
        actions.append(HIDE)
    actions.append(RESET)
    return tuple((action, _LABELS[action]) for action in actions)
