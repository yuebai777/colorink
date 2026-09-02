"""Keeping the floating fg/bg cluster clear of the round picker areas.

Split out of ui.color_preview_box (which sits on a 250 pure-LOC ceiling).
The preview cluster — two swatch circles plus the transparent capsule —
floats over the colour wheel / LAB disc, and its corner anchor used to
leave the foreground swatch grazing the arc in BOTH corners.

The placement is derived from the picker circles themselves, so the cluster
keeps a stable relationship with them at any window size: it scales with the
wheel (ColorPreviewBox.resize_and_position), is then slid diagonally outward
until every part clears the arcs, and is trimmed a few percent by the caller
when a shallow corner cannot swallow its nominal size.
"""

from __future__ import annotations

import math

from ui.transparent_swatch import apply_preview_mouse_mask

# Circle = (centre_x, centre_y, radius) in the preview box's PARENT coords.
Circle = tuple[float, float, float]


class fit_scope:
    """Suspend the mouse-input mask while a caller probes placements.

    The fit loop tries several sizes, each moving the cluster; rebuilding the
    input mask every time is the single most expensive part of a resize pass
    once the stylesheets are memoised. One mask is applied on exit.
    """

    def __init__(self, box):
        self._box = box

    def __enter__(self):
        self._box._mask_suspended = True
        return self._box

    def __exit__(self, *exc):
        self._box._mask_suspended = False
        apply_preview_mouse_mask(self._box)
        return False


def cluster_obstacles(box) -> list[Circle]:
    """Every painted part of the cluster, as circles in parent coordinates.

    The capsule is covered by its two end circles, which bound it exactly.
    """
    fg_cx, fg_cy, fg_r, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()
    ox, oy = float(box.x()), float(box.y())
    parts = [(ox + fg_cx, oy + fg_cy, fg_r), (ox + bg_cx, oy + bg_cy, bg_r)]
    tile = box._trans_tile.geometry()
    if tile.isValid() and not tile.isEmpty():
        r = tile.height() / 2.0
        parts.append((ox + tile.x() + r, oy + tile.y() + r, r))
        parts.append((ox + tile.x() + tile.width() - r, oy + tile.y() + r, r))
    return parts


def cluster_clearance(box) -> float:
    """Gap the cluster wants between itself and a picker circle, in px.

    The active slot's border straddles the circle path (2.5px wide), so half
    of it already sits outside the swatch radius; the rest is a small visual
    gap that scales with the cluster.
    """
    return 1.5 + 2.0 * (box.width() / 60.0)


def penetration(box, circles, clearance: float = 0.0) -> float:
    """How deep the cluster reaches into any of *circles*, in px (0 = clear).

    Both the hue ring and the LAB disc matter, and neither contains the
    other: with the lightness bar shown the disc is smaller than the ring but
    sits further up and to the left, so it reaches into the top-left corner
    where the ring does not.
    """
    deepest = 0.0
    obstacles = cluster_obstacles(box)
    for cx, cy, radius in circles:
        if radius <= 0.0:
            continue
        for px, py, pr in obstacles:
            gap_px = math.hypot(px - cx, py - cy) - (radius + pr + clearance)
            deepest = max(deepest, -gap_px)
    return deepest


def gap(box, circles) -> float:
    """Smallest distance from the cluster to any circle (negative = overlap)."""
    obstacles = cluster_obstacles(box)
    gaps = [
        math.hypot(px - cx, py - cy) - (radius + pr)
        for cx, cy, radius in circles if radius > 0.0
        for px, py, pr in obstacles
    ]
    return min(gaps) if gaps else 0.0


def avoid_circles(box, circles, bounds=None) -> float:
    """Slide the cluster out of *circles*, along its own corner.

    Moves the anchored cluster diagonally outward until both swatches AND the
    transparent capsule clear every circle, without leaving *bounds* (the
    picker area, as x0, y0, x1, y1). Ringless mode is skipped: there the
    swatches live in their own control bar.

    Returns the penetration still left, in px (0.0 = clear); the caller
    shrinks the cluster when a shallow corner cannot swallow it.
    """
    layout = getattr(box, "_ringless_layout", None)
    if layout is not None and layout.controls_enabled:
        return 0.0
    circles = [c for c in circles if c[2] > 0.0]
    if not circles or box.width() <= 0:
        return 0.0
    clearance = cluster_clearance(box)
    # The corner anchor decides which way "outward" is.
    dir_y = -1.0 if box.position_mode == "top-left" else 1.0
    start = (box.x(), box.y())
    deepest = penetration(box, circles, clearance)
    for _ in range(4):
        if deepest <= 0.25:
            break
        step = deepest / math.sqrt(2.0)
        x = box.x() - step
        y = box.y() + step * dir_y
        if bounds is not None:
            x0, y0, x1, y1 = bounds
            x = max(float(x0), min(x, float(x1) - box.width()))
            y = max(float(y0), min(y, float(y1) - box.height()))
        if abs(x - box.x()) < 0.5 and abs(y - box.y()) < 0.5:
            break
        box.move(int(round(x)), int(round(y)))
        deepest = penetration(box, circles, clearance)
    if (box.x(), box.y()) != start:
        apply_preview_mouse_mask(box)
    return max(0.0, deepest)
