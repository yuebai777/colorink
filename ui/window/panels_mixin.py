"""Panel provider: which live widget each registered panel id refers to.

The seam between the panel model (ui/panels) and the window that currently
owns the widgets. It deliberately does NOT arrange anything — the classic
layout still assembles the window — but it lets the host, the layout store
and the settings UI talk about panels by id against the real widgets.
"""

from __future__ import annotations

from ui.panels import registry


class PanelProviderMixin:

    def panel_widget(self, panel_id: str):
        """The live widget behind a panel id, or None if it does not exist.

        Satellites resolve to the widget that actually carries them today:
        the lightness bar is its own column, the swatch cluster is the
        floating preview box.
        """
        if panel_id == registry.PICKER:
            return getattr(self, "stack", None)
        if panel_id == registry.LIGHTNESS:
            return getattr(self, "lab_slider_column", None)
        if panel_id == registry.SWATCHES:
            return getattr(self, "preview_box", None)
        if panel_id == registry.HISTORY:
            # The layout unit is the group container, not the grid inside it:
            # the classic column adds slider_containers["History"].
            containers = getattr(self, "slider_containers", None)
            if containers and "History" in containers:
                return containers["History"]
            return getattr(self, "color_history", None)
        if panel_id.startswith(registry.SLIDER_PREFIX):
            containers = getattr(self, "slider_containers", None)
            if not containers:
                return None
            wanted = panel_id[len(registry.SLIDER_PREFIX):]
            for group, container in containers.items():
                if group.lower() == wanted:
                    return container
        return None

    def panel_provider(self):
        """Callable a PanelHost can mount panels through."""
        return self.panel_widget

    def slider_column_tree(self, spacing: float = 0.0,
                           margins=(0.0, 0.0, 0.0, 0.0)):
        """The slider area as a dock tree, in config order.

        A non-resizable column: every block is as tall as its content, with a
        fixed gap — exactly what the hand-built QVBoxLayout produced. Hidden
        groups stay in the tree (the host mounts them and the caller keeps
        driving setVisible), so ordering and visibility remain one concern.
        """
        from core import config as _config
        from ui.panels import tree as dock

        ids = []
        for group in _config.sorted_slider_groups(self.cfg):
            panel_id = (registry.HISTORY if group == "History"
                        else registry.slider_panel_id(group))
            if self.panel_widget(panel_id) is not None:
                ids.append(panel_id)
        return dock.Split(dock.VERTICAL, tuple(dock.Leaf(pid) for pid in ids),
                          (), False, spacing, margins)

    def arrangement_seed(self) -> str:
        """Which switch the derived arrangement comes from.

        A saved tree is only allowed to override the derivation it grew out
        of. Without this, flipping a layout switch would appear to do
        nothing: the arrangement saved under the previous switch would keep
        winning, because it still places exactly the same panels.
        """
        cfg = getattr(self, "cfg", None) or {}
        if cfg.get("slidersTabs", False):
            return "tabs"
        return "stack"

    def panel_layout_tree(self):
        """The arrangement to assemble, tree first, config second.

        A saved arrangement wins only when it places exactly the panels this
        build would place anyway — that keeps the legacy per-group order keys
        working today, while a future drag-reorder persists through
        cfg["panelLayout"] without a second source of truth appearing behind
        the user's back.
        """
        from ui.panels import store

        derived = self.slider_column_tree()
        saved = store.load_from(getattr(self, "cfg", None),
                                self.arrangement_seed())
        if set(saved.panels()) == set(derived.panels()):
            return saved
        return derived

    def save_panel_layout(self, tree=None) -> None:
        """Record the current arrangement in the config (caller persists).

        Pass the tree that was actually mounted — the derived column is only
        the fallback for callers that never assembled one.
        """
        from ui.panels import store

        store.save_into(getattr(self, "cfg", None),
                        tree if tree is not None else self.slider_column_tree(),
                        self.arrangement_seed())

    def reset_panel_layout(self) -> None:
        """Throw away a dragged arrangement (caller persists + re-assembles).

        Drag-to-rearrange has no undo, so there has to be a way back. Only
        the arrangement goes — the layout switches are the user's settings,
        not the mess they just dragged.
        """
        from ui.panels import store

        store.clear(getattr(self, "cfg", None))

    def missing_panel_widgets(self) -> tuple[str, ...]:
        """Registered panels this window cannot supply — should be empty."""
        return tuple(panel_id for panel_id in registry.panel_ids()
                     if self.panel_widget(panel_id) is None)
