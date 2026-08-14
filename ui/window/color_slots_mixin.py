"""Foreground/background color slots, transparency and color history.

Extracted from the former MainWindow god class. Everything that manages
which slot is active, the per-slot transparent state, and recording /
persisting the recently-used color grid lives here. Methods operate on
shared ``self`` state set up by MainWindow.init_ui / setup_sliders.
"""

from core import config


class ColorSlotsMixin:
    def select_fg_slot(self):
        # Clicking the fg swatch restores an opaque fg: clear any transparent
        # state so its highlight returns immediately.
        self._set_slot_transparent("fg", False)
        if self.active_slot != "fg":
            # Save current source to bg slot
            self._bg_source_space = self._source_space
            self._bg_source_values = self._source_values
            self.active_slot = "fg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Restore fg source
            self._source_space = self._fg_source_space
            self._source_values = self._fg_source_values
            col = self.preview_box.fg_color
            color = self._color_from_source(self._source_space, self._source_values,
                                            (col.red(), col.green(), col.blue()))
            self._project_color(color, source="slot_change")

    def select_bg_slot(self):
        # Clicking the bg swatch restores an opaque bg (see select_fg_slot).
        self._set_slot_transparent("bg", False)
        if self.active_slot != "bg":
            # Save current source to fg slot
            self._fg_source_space = self._source_space
            self._fg_source_values = self._source_values
            self.active_slot = "bg"
            self.preview_box.update_slot_borders(self.active_slot)
            # Restore bg source
            self._source_space = self._bg_source_space
            self._source_values = self._bg_source_values
            col = self.preview_box.bg_color
            color = self._color_from_source(self._source_space, self._source_values,
                                            (col.red(), col.green(), col.blue()))
            self._project_color(color, source="slot_change")

    def set_active_transparent(self):
        """Toggle the active slot's transparent state and push it to the
        drawing software.

        The fg/bg swatches keep their last color — the transparent tile
        shows the state with a blue outline.
        """
        slot = self.active_slot
        is_transparent = self._fg_transparent if slot == "fg" else self._bg_transparent
        self._set_slot_transparent(slot, not is_transparent)

    def _set_slot_transparent(self, slot, transparent):
        """Set *slot*'s transparent state and push it to the drawing software.

        Used by the transparent-tile toggle (:meth:`set_active_transparent`)
        and by fg/bg swatch clicks, which restore an opaque color for the
        slot. The color always comes from the slot's own swatch, so the
        push is correct regardless of which slot is active. Pushing twice
        (once here, once from a slot-change) is harmless — same values.
        """
        if slot == "fg":
            is_transparent = self._fg_transparent
            color = self.preview_box.fg_color
        else:
            is_transparent = self._bg_transparent
            color = self.preview_box.bg_color
        if is_transparent == transparent:
            return
        if slot == "fg":
            self._fg_transparent = transparent
        else:
            self._bg_transparent = transparent
        self.preview_box.set_transparent(slot, transparent)
        print(f"[Transparent] {slot} transparent={transparent}")
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            color_index = 0 if slot == "fg" else 1
            self.sync_thread.write_color(
                color.red(), color.green(), color.blue(),
                transparent=transparent, color_index=color_index)

    def on_history_color_picked(self, color):
        """User clicked a swatch in the history widget → load into active slot."""
        r, g, b = color.red(), color.green(), color.blue()
        hex_key = f"#{r:02x}{g:02x}{b:02x}"
        stored = self._color_source_store.get(hex_key)
        if stored:
            self._source_space = stored["s"]
            # Reconstruct float values from stored list
            vals_list = stored.get("v", [])
            if hasattr(self, "_SOURCE_CHANNELS"):
                ch_names = self._SOURCE_CHANNELS.get(self._source_space, [])
                self._source_values = {ch: float(vals_list[i]) for i, ch in enumerate(ch_names) if i < len(vals_list)}
            else:
                self._source_values = None
        else:
            self._source_space = "rgb"
            self._source_values = {"r": float(r), "g": float(g), "b": float(b)}
        color = self._color_from_source(self._source_space, self._source_values, (r, g, b))
        self._project_color(color, source="history")
        if hasattr(self, "color_history"):
            updated = self.color_history.mark_selected(color)
            self.cfg["historyColors"] = self._build_history_entries(updated)
            config.save_hotkey_config(self.cfg)

    def _record_color_history(self):
        """Persist the latest RGB into the history widget and into config.
        Called when an interaction finishes (slider/wheel/lab release)."""
        if not hasattr(self, "color_history"):
            return
        r, g, b = self.current_rgb
        updated = self.color_history.record(r, g, b)
        # Cache source info for the newest entry
        if self._source_space and self._source_values:
            hex_key = f"#{r:02x}{g:02x}{b:02x}"
            ch_names = self._SOURCE_CHANNELS.get(self._source_space, [])
            vals_list = [round(float(self._source_values.get(ch, 0)), 4)
                         for ch in ch_names]
            self._color_source_store[hex_key] = {
                "rgb": [r, g, b], "s": self._source_space, "v": vals_list,
            }
        self.cfg["historyColors"] = self._build_history_entries(list(updated))
        config.save_hotkey_config(self.cfg)

    def _build_history_entries(self, colors):
        """Convert QColor list to serialisable entries, preserving source info."""
        entries = []
        for c in colors:
            hex_key = f"#{c.red():02x}{c.green():02x}{c.blue():02x}"
            stored = self._color_source_store.get(hex_key)
            if stored:
                entries.append(stored)
            else:
                entries.append([int(c.red()), int(c.green()), int(c.blue())])
        return entries
