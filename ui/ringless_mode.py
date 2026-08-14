"""Ringless-mode configuration value objects.

Owns the typed representation of ringless settings (ControlsSide, RinglessConfig,
RinglessLayout) and the pure function resolve_ringless_layout that translates
a config + page-state + UI-scale into pixel dimensions.

No PyQt imports — this module is pure data and logic.
"""

from dataclasses import dataclass
from typing import Final, Literal

ControlsSide = Literal["left", "right"]
ControlBarPosition = Literal["top", "bottom"]

_DEFAULT_SIDE: Final[ControlsSide] = "right"

# Base layout metrics at ui_scale=1.0 (approved design values).
_BAR_H: Final[int] = 30
_MARGIN: Final[int] = 7
_SWATCH_W: Final[int] = 43
_SWATCH_H: Final[int] = 24
_SWATCH_GAP: Final[int] = 5
_CORNER_R: Final[int] = 4
_BUTTON_GAP: Final[int] = 4
_SWATCH_PAD: Final[int] = 3
_SWATCH_OFFSET_Y: Final[int] = 2
RINGLESS_ACTIVE_BORDER: Final[str] = "#5a94e2"
RINGLESS_INACTIVE_BORDER: Final[str] = "#cccccc"


@dataclass(frozen=True, slots=True)
class RinglessConfig:
    """Immutable snapshot of ringless-mode user preferences."""

    enabled: bool
    controls_side: ControlsSide
    control_bar_position: ControlBarPosition = "top"

    @classmethod
    def from_values(
        cls, enabled: bool, raw_side: str,
        raw_position: str = "top",
    ) -> "RinglessConfig":
        """Parse raw values from config dict into a typed RinglessConfig.

        Unknown side strings silently default to ``"right"`` (boundary-safe).
        """
        match raw_side:
            case "left":
                controls_side: ControlsSide = "left"
            case "right":
                controls_side = "right"
            case _:
                controls_side = _DEFAULT_SIDE
        position: ControlBarPosition = (
            "bottom" if raw_position == "bottom" else "top"
        )
        return cls(
            enabled=enabled, controls_side=controls_side,
            control_bar_position=position,
        )


@dataclass(frozen=True, slots=True)
class RinglessLayout:
    """Computed pixel layout for the ringless-mode UI.

    All dimensions are in logical pixels and scaled by ``ui_scale``.
    """

    wheel_enabled: bool
    controls_enabled: bool
    controls_side: ControlsSide
    control_bar_height: int
    margin: int
    swatch_width: int
    swatch_height: int
    swatch_gap: int
    corner_radius: int
    button_gap: int
    control_bar_position: ControlBarPosition = "top"
    swatch_padding: int = _SWATCH_PAD
    swatch_offset_y: int = _SWATCH_OFFSET_Y


def centered_control_offset(container_size: int, content_size: int) -> int:
    """Return the shared integer offset for controls centered in the bar."""
    return (container_size - content_size) // 2


def ringless_swatch_row(
    side: ControlsSide, pad: float, swatch: float, gap: float,
) -> tuple[float, float, float]:
    """Return ``(fg_x, bg_x, transparent_x)`` for the three-swatch row.

    FG always stays left of BG (the colour pair is never mirrored). The
    transparent tile always takes the innermost slot — nearest the window
    centre — so it sits left of the pair when the group is anchored right
    and right of the pair when the group is anchored left.
    """
    lead = swatch + gap
    if side == "right":
        return pad + lead, pad + 2.0 * lead, pad
    return pad, pad + lead, pad + 2.0 * lead


def resolve_ringless_layout(
    config: RinglessConfig,
    wheel_page_active: bool,
    ui_scale: float,
) -> RinglessLayout:
    """Produce a RinglessLayout from config, page state, and UI scale.

    ``wheel_page_active`` is retained for API compatibility. The wheel layout
    stays enabled while its page is hidden so cached slice images survive LAB
    round-trips. ``controls_enabled`` follows the dedicated control-bar setting.

    ``ui_scale`` is clamped to a minimum of 0.01 to avoid zero/negative
    dimensions.
    """
    scale = max(0.01, ui_scale)
    return RinglessLayout(
        # Keep the wheel layout stable while the LAB page is visible so page
        # switches do not invalidate the cached slice/ring images.
        wheel_enabled=config.enabled,
        controls_enabled=config.enabled,
        controls_side=config.controls_side,
        control_bar_height=max(1, round(_BAR_H * scale)),
        margin=max(1, round(_MARGIN * scale)),
        swatch_width=max(1, round(_SWATCH_W * scale)),
        swatch_height=max(1, round(_SWATCH_H * scale)),
        swatch_gap=max(1, round(_SWATCH_GAP * scale)),
        corner_radius=max(1, round(_CORNER_R * scale)),
        button_gap=max(1, round(_BUTTON_GAP * scale)),
        control_bar_position=config.control_bar_position,
        swatch_padding=max(1, round(_SWATCH_PAD * scale)),
        swatch_offset_y=max(1, round(_SWATCH_OFFSET_Y * scale)),
    )
