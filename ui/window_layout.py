"""Pure geometry for the picker area — no Qt, no widget lookups.

First slice of the layout refactor (see
docs/superpowers/plans/2026-09-01-window-layout-and-panelization.md).

The wheel/disc formula used to live in four places at once —
ColorWheel.get_wheel_geometry, LabSquare._disc_metrics, the resize pass in
ui/window/layout.py and the theme pass in ui/window/theme.py — and the last
two had already drifted apart (one trimmed 4px, the other 6px), so the
floating swatches were sized differently depending on whether a resize or a
settings change ran last. Everything derives from this module now.

Everything here takes plain numbers and returns plain values, so the whole
thing is testable without a window — the offscreen widget geometry that
these rules used to be read from is unreliable (a stacked page that has
never been shown still reports its construction-time size).
"""

from __future__ import annotations


def snap_border_width(width: int, dpr: float, limit: int = 3) -> int:
    """A frame width that lands on whole device pixels.

    A 3px frame on a 1.5x display is 4.5 device pixels. Qt cannot draw half
    a pixel, so it paints four and leaves the remainder as a lighter
    half-covered column — on one side only, because the two edges round in
    opposite directions. That stray line is what a 1.5x user sees as "a gap
    down the left of the title bar"; at 1x and 2x the same theme is clean,
    which is why it only ever shows up on some machines.

    So: keep the theme's width when it already lands on whole pixels, and
    otherwise move to the nearest width that does. *limit* caps how far it
    may wander before giving up and keeping the original.
    """
    if width <= 0 or dpr <= 0:
        return max(0, int(width))
    def integral(value: int) -> bool:
        product = value * dpr
        return abs(product - round(product)) < 1e-6
    if integral(width):
        return width
    for step in range(1, limit + 1):
        for candidate in (width + step, width - step):
            if candidate > 0 and integral(candidate):
                return candidate
    return width

from dataclasses import dataclass

# Margins baked into the picker circle, in logical px.
SIDE_MARGIN = 16      # taken off the width (8 per side)
BOTTOM_MARGIN = 6     # taken off the height
EDGE_INSET = 2        # outer radius = size / 2 - EDGE_INSET
TOP_OFFSET = 6        # centre sits this far below the top of the square
MIN_SIZE = 16


@dataclass(frozen=True, slots=True)
class Circle:
    """A circle in the coordinates of the widget that owns it."""

    x: float
    y: float
    radius: float

    @property
    def top(self) -> float:
        return self.y - self.radius

    @property
    def bottom(self) -> float:
        return self.y + self.radius

    @property
    def diameter(self) -> float:
        return self.radius * 2.0


@dataclass(frozen=True, slots=True)
class PickerGeometry:
    """The hue ring / LAB disc, plus the rings derived from it."""

    size: float           # side of the square the circle is inscribed in
    circle: Circle        # outer edge of the ring
    ring_width: float
    inner_radius: float
    triangle_radius: float


def picker_size(width: float, height: float) -> float:
    """Side of the square the picker circle is inscribed in.

    Widened to touch the sides, but never taller than the widget: a short,
    wide pane shrinks the wheel instead of clipping its lower arc.
    """
    return min(float(width) - SIDE_MARGIN,
               max(float(MIN_SIZE), float(height) - BOTTOM_MARGIN))


def resolve_picker_geometry(width: float, height: float) -> PickerGeometry:
    """Ring geometry for a picker widget of this size."""
    size = picker_size(width, height)
    outer = size / 2.0 - EDGE_INSET
    ring_width = max(12.0, size * 0.08)
    inner = outer - ring_width
    return PickerGeometry(
        size=size,
        circle=Circle(float(width) / 2.0, size / 2.0 + TOP_OFFSET, outer),
        ring_width=ring_width,
        inner_radius=inner,
        triangle_radius=max(1.0, inner - 3.0),
    )


def picker_square_height(window_width: float, margins_left: float,
                         margins_right: float, minimum: float) -> int:
    """Height that keeps the picker area square at this window width.

    Used as a CAP during a drag: the content-height policy that resizes the
    window to match is debounced, so without it the pane grabs every spare
    pixel between two settles and goes tall-and-narrow.
    """
    return max(int(minimum),
               int(window_width) - int(margins_left) - int(margins_right))


def picker_pane_height(window_height: float, margins_top: float,
                       margins_bottom: float, title_height: float,
                       sliders_height: float, spacing: float) -> int:
    """Height left for the picker once the chrome and sliders are placed."""
    return int(window_height) - int(margins_top) - int(margins_bottom) \
        - int(title_height) - int(sliders_height) - 2 * int(spacing)


def wheel_size_for(window_width: float, pane_height: float,
                   ui_scale: float = 1.0) -> int:
    """Picker size the window can afford, from the window width down.

    Mirrors picker_size() but in window terms, so the swatch cluster that
    scales off it stays in step with the ring at every window size.
    """
    side = int(SIDE_MARGIN * float(ui_scale))
    return int(min(int(window_width) - side,
                   max(MIN_SIZE, int(pane_height) - BOTTOM_MARGIN)))

@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle in window coordinates."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True, slots=True)
class WindowLayout:
    """Every band of the main window, in WINDOW coordinates.

    Derived from plain numbers so no widget ever has to read another
    widgets geometry: doing that is what produced this sessions bugs, since
    a stacked page that has never been shown still reports its
    construction-time size, and isVisible() lies before the window is up.
    """

    content: Rect
    title_band: Rect
    picker: Rect
    picker_circle: Circle       # window coordinates, not pane coordinates
    sliders: Rect
    spacing: int

    @property
    def picker_size(self) -> float:
        """Side of the square the picker circle is inscribed in.

        What the swatch cluster scales off, so it tracks the ring exactly
        rather than an approximation taken from the window width.
        """
        return self.picker_circle.radius * 2.0 + 2 * EDGE_INSET

    @property
    def picker_bounds(self) -> tuple[float, float, float, float]:
        """The picker rect as (x0, y0, x1, y1) — the floating cluster's cage."""
        return (self.picker.x, self.picker.y, self.picker.right, self.picker.bottom)


def resolve_window_layout(*, window_width: float, window_height: float,
                          margins: tuple[float, float, float, float],
                          title_height: float, sliders_height: float,
                          spacing: float, ui_scale: float = 1.0,
                          picker_minimum: float = 0.0) -> WindowLayout:
    """Lay the window out top to bottom: title, picker, sliders.

    *margins* is (left, top, right, bottom) from the outer layout. The picker
    is capped to a square (see picker_square_height) so it keeps tracking the
    window width between two runs of the debounced content-height policy.
    """
    left, top, right, bottom = (float(m) for m in margins)
    spacing = int(spacing)
    content = Rect(left, top,
                   float(window_width) - left - right,
                   float(window_height) - top - bottom)
    title_band = Rect(content.x, content.y, content.width, float(title_height))

    picker_y = content.y + float(title_height) + spacing
    available = content.height - float(title_height) - float(sliders_height) \
        - 2 * spacing
    square = picker_square_height(window_width, left, right, picker_minimum)
    picker_h = max(0.0, min(available, float(square)))
    picker = Rect(content.x, picker_y, content.width, picker_h)

    geometry = resolve_picker_geometry(picker.width, picker.height)
    circle = Circle(picker.x + geometry.circle.x,
                    picker.y + geometry.circle.y,
                    geometry.circle.radius)

    sliders = Rect(content.x, picker.bottom + spacing,
                   content.width, float(sliders_height))
    return WindowLayout(content=content, title_band=title_band, picker=picker,
                        picker_circle=circle, sliders=sliders, spacing=spacing)