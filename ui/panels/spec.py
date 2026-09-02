"""Panel contract — what a dockable/floatable piece of the UI declares.

Part of the panelisation plan (docs/superpowers/plans/
2026-09-01-window-layout-and-panelization.md). Pure data on purpose: the
registry describes the pieces, the host decides where they go, and neither
needs a widget to answer questions about the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PanelSpec:
    """One dockable piece of the UI.

    * id           stable key used in the saved layout
    * title        display name (already localised by the caller)
    * min_size     smallest usable size, in logical px
    * aspect       width / height the panel insists on (picker = 1.0), or None
    * satellites   panels that ride *inside* this one by default
    * detachable   may be popped into its own window
    """

    id: str
    title: str
    min_size: tuple[int, int]
    aspect: float | None = None
    satellites: tuple[str, ...] = ()
    detachable: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("panel id must not be empty")
        width, height = self.min_size
        if width <= 0 or height <= 0:
            raise ValueError(f"{self.id}: min_size must be positive")
        if self.aspect is not None and self.aspect <= 0:
            raise ValueError(f"{self.id}: aspect must be positive")

    def height_for_width(self, width: float) -> float:
        """Height this panel wants at *width* (aspect-locked panels only)."""
        if self.aspect is None:
            return float(self.min_size[1])
        return float(width) / self.aspect
