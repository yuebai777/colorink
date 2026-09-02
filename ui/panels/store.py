"""Saving and restoring the panel arrangement.

Kept apart from the tree itself so the data model has no idea where it is
stored, and the config schema can move without touching the model.
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.panels import registry
from ui.panels import tree as dock

CONFIG_KEY = "panelLayout"
FLOATING_KEY = "floatingPanels"
LAYOUT_VERSION = 1


def dump(node, seed: str = "") -> dict:
    """Serialise a tree for the config file.

    *seed* records which derivation the arrangement grew out of — see
    :func:`parse`.
    """
    data = {"version": LAYOUT_VERSION, "root": node.to_json()}
    if seed:
        data["seed"] = seed
    return data


def parse(data, seed: str | None = None) -> dock.Node:
    """Read a saved arrangement, falling back to the default layout.

    Anything unexpected — a missing key, a future version, a panel this
    build no longer ships — degrades to the default rather than leaving the
    user with an empty window.

    A saved arrangement may only speak for the derivation it came from. Ask
    with *seed* and a record from a different one is refused: otherwise
    ticking "two columns" would silently do nothing, because yesterday's
    single-column record keeps winning over the switch the user just flipped.
    """
    if not isinstance(data, dict):
        return dock.default_tree()
    version = data.get("version")
    if version != LAYOUT_VERSION:
        return dock.default_tree()
    if seed is not None and data.get("seed", "") != seed:
        return dock.default_tree()
    return dock.load(data.get("root"))


def load_from(config, seed: str | None = None) -> dock.Node:
    """Read the arrangement out of a config mapping."""
    return parse((config or {}).get(CONFIG_KEY), seed)


def save_into(config, node, seed: str = "") -> None:
    """Write the arrangement into a config mapping (caller persists it)."""
    if config is None:
        return
    config[CONFIG_KEY] = dump(node, seed)


@dataclass(frozen=True, slots=True)
class FloatingState:
    """Where a torn-off panel sits, and whether it stays above everything.

    On top by default: a panel is torn off to keep it in view over the
    drawing app, which is the whole reason this program exists.
    """

    rect: tuple
    on_top: bool = True


def _parse_rect(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int)
           for item in value):
        return None
    if value[2] <= 0 or value[3] <= 0:
        return None
    return tuple(int(item) for item in value)


def load_floating_from(config) -> dict:
    """Which panels are torn off, and where their windows sit.

    Kept out of the dock tree on purpose: the tree records where a panel
    *lives*, so a floated panel can dock back where it came from instead of
    landing at the bottom of the column. Anything unreadable is forgotten —
    a bad record must not strand a panel in a window that never opens.
    """
    data = (config or {}).get(FLOATING_KEY)
    if not isinstance(data, dict):
        return {}
    floating = {}
    for panel_id, record in data.items():
        if not isinstance(panel_id, str) or registry.panel(panel_id) is None:
            continue
        on_top = True
        if isinstance(record, dict):
            on_top = record.get("onTop", True)
            record = record.get("rect")
        rect = _parse_rect(record)
        if rect is None:
            continue
        floating[panel_id] = FloatingState(rect, bool(on_top))
    return floating


def save_floating_into(config, floating) -> None:
    """Write the torn-off panels into a config mapping (caller persists)."""
    if config is None:
        return
    if not floating:
        config.pop(FLOATING_KEY, None)
        return
    config[FLOATING_KEY] = {
        panel_id: {"rect": list(state.rect), "onTop": bool(state.on_top)}
        for panel_id, state in floating.items()}


def clear(config) -> None:
    """Forget the saved arrangement (caller persists it).

    Removed, not overwritten with a fresh default: the derivation is the
    fallback, and leaving a record behind would just be one more thing that
    can disagree with the switches.
    """
    if config is not None:
        config.pop(CONFIG_KEY, None)
