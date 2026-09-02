"""The panels Colorink is made of, as data.

Registering a panel here does NOT move any widget yet — the host still
assembles the classic single column. It gives the layout tree, the settings
UI and (later) the dock host one place to ask what exists, how small it may
get, and what has to ride along with what.
"""

from __future__ import annotations

from ui.panels.spec import PanelSpec

PICKER = "picker"
LIGHTNESS = "lightness"
SWATCHES = "swatches"
HISTORY = "history"
SLIDER_PREFIX = "sliders."

# Slider groups keep the config casing used by core.config.SLIDER_GROUPS.
SLIDER_GROUPS = ("RGB", "HSV", "HSL", "LAB", "OKLab", "OKLCh")


def slider_panel_id(group: str) -> str:
    """Panel id for a colour-space slider block."""
    return f"{SLIDER_PREFIX}{group.lower()}"


def _slider_panels() -> dict[str, PanelSpec]:
    return {
        slider_panel_id(group): PanelSpec(
            id=slider_panel_id(group),
            title=group,
            min_size=(180, 40),
        )
        for group in SLIDER_GROUPS
    }


PANELS: dict[str, PanelSpec] = {
    PICKER: PanelSpec(
        id=PICKER,
        title="取色区",
        min_size=(160, 160),
        # The wheel and the LAB disc are inscribed in a square; a host that
        # ignores this ends up with the tall-and-narrow pane that leaves a
        # blank band under the ring.
        aspect=1.0,
        satellites=(LIGHTNESS, SWATCHES),
        detachable=True,
    ),
    LIGHTNESS: PanelSpec(
        id=LIGHTNESS,
        title="明度条",
        min_size=(18, 60),
    ),
    SWATCHES: PanelSpec(
        id=SWATCHES,
        title="前景/背景色",
        min_size=(48, 60),
    ),
    HISTORY: PanelSpec(
        id=HISTORY,
        title="历史颜色",
        min_size=(180, 40),
    ),
    **_slider_panels(),
}


def panel(panel_id: str) -> PanelSpec | None:
    """Look up a panel; unknown ids return None instead of raising.

    A saved layout may name a panel that a later build dropped — the host
    prunes it rather than refusing to open.
    """
    return PANELS.get(panel_id)


def panel_ids() -> tuple[str, ...]:
    return tuple(PANELS)


def satellites_of(panel_id: str) -> tuple[str, ...]:
    spec = panel(panel_id)
    return spec.satellites if spec else ()


def group_of(panel_id: str) -> str | None:
    """The config group name behind a panel id ("RGB", "History"), or None.

    The config keys predate the panel model, so this is the one place that
    knows how the two name the same thing.
    """
    if panel_id == HISTORY:
        return "History"
    if not panel_id.startswith(SLIDER_PREFIX):
        return None
    wanted = panel_id[len(SLIDER_PREFIX):]
    for group in SLIDER_GROUPS:
        if group.lower() == wanted:
            return group
    return None


def is_satellite(panel_id: str) -> bool:
    """True when this panel rides inside another one by default."""
    return any(panel_id in spec.satellites for spec in PANELS.values())
