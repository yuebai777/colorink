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
        if registry.is_wheel_panel(panel_id):
            key = registry.wheel_panel_key(panel_id)
            panels = getattr(self, "wheel_panels", None) or {}
            return panels.get(key)
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

    def _saved_panel_layout_allowed(self, saved, derived) -> bool:
        """A saved tree may add wheel pages or omit panels placed in pickerDocks.

        Sliders still have to match the derived set across the primary column
        plus optional color-map edge/center docks; wheel pages are the extra
        panels the shared host is allowed to own. Anything else (a panel from
        a previous build, a picker/history mix-up) is rejected so the derivation
        stays authoritative.
        """
        from ui.panels import registry
        from ui.window.picker_drop import picker_dock_panel_ids

        dock_panels = picker_dock_panel_ids(getattr(self, "cfg", None))
        saved_panels = set(saved.panels())
        derived_panels = set(derived.panels())
        expected_in_main = derived_panels - dock_panels
        if not expected_in_main:
            # All panels live in pickerDocks or are filtered out.
            return all(registry.is_wheel_panel(pid) or pid in derived_panels for pid in saved_panels)
        if saved_panels == expected_in_main:
            return True
        if not expected_in_main <= saved_panels:
            return False
        extra = saved_panels - expected_in_main
        return bool(extra) and all(
            registry.is_wheel_panel(pid) for pid in extra)

    def panel_layout_tree(self):
        """The arrangement to assemble, tree first, config second.

        A saved arrangement wins when it places every slider this build would
        place (across ``panel_host`` + ``pickerDocks``), plus (optionally)
        wheel pages the user dragged into the host.
        """
        from ui.panels import store
        from ui.window.picker_drop import _subtree_without, picker_dock_panel_ids

        derived = self.slider_column_tree()
        saved = store.load_from(getattr(self, "cfg", None),
                                self.arrangement_seed())
        if self._saved_panel_layout_allowed(saved, derived):
            return saved
        dock_panels = picker_dock_panel_ids(getattr(self, "cfg", None))
        if dock_panels:
            from ui.panels import tree as dock
            trimmed = _subtree_without(derived, dock_panels)
            return trimmed if trimmed is not None else dock.Split(dock.VERTICAL, ())
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