"""Panel model: what the UI is made of, and how a host may arrange it."""

from ui.panels.registry import (
    HISTORY,
    LIGHTNESS,
    PANELS,
    PICKER,
    SWATCHES,
    is_satellite,
    panel,
    panel_ids,
    satellites_of,
    slider_panel_id,
)
from ui.panels.spec import PanelSpec

__all__ = [
    "PANELS", "PICKER", "LIGHTNESS", "SWATCHES", "HISTORY",
    "PanelSpec", "panel", "panel_ids", "satellites_of", "is_satellite",
    "slider_panel_id",
]
