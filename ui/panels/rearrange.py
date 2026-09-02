"""Drag-to-rearrange: where a drop lands, and the tree it produces.

Pure data, like the rest of `ui/panels`. Two questions are decided here and
nowhere else:

* which *zone* of a panel the cursor is over — the four borders mean "split
  here", the middle means "stack behind tabs";
* what the dock tree looks like after the drop.

Both are answerable without a widget, so the host only has to feed in a
position and re-mount the result. That keeps the surgery exhaustively
testable, and keeps the arrangement a single serialisable value rather than
something that only exists as live QSplitters.
"""

from __future__ import annotations

from ui.panels.tree import HORIZONTAL, VERTICAL, Leaf, Split, Tabs

LEFT = "left"
RIGHT = "right"
TOP = "top"
BOTTOM = "bottom"
CENTER = "center"
ZONES = (LEFT, RIGHT, TOP, BOTTOM, CENTER)

#: Drop zone used only when the cursor is over a page's tab header: the
#: dragged panel merges into that page instead of making a new one.
MERGE_PAGE = "merge_page"

#: How much of a panel counts as its border band, per side.
EDGE_FRACTION = 0.25

_ORIENTATION_OF = {
    LEFT: HORIZONTAL, RIGHT: HORIZONTAL,
    TOP: VERTICAL, BOTTOM: VERTICAL,
}
_BEFORE = (LEFT, TOP)


# ── where the cursor is ──────────────────────────────────────────────────

def zone_at(width, height, x, y, edge: float = EDGE_FRACTION,
            allow_center: bool = False):
    """Drop zone for a point inside a *width* x *height* panel, or None.

    Four sides, and that is the whole vocabulary: wherever you let go, the
    panel goes above / below / left / right of the one under the cursor.
    Every point belongs to its nearest side, so there is no dead middle to
    aim around and nothing to learn.

    *allow_center* turns the middle into a "stack behind tabs" target — the
    tree can express it and the surgery supports it, but it is off by
    default: a drop that silently swallows one panel into another is not
    what someone dragging a block up two rows meant to do.

    Ties (a perfect corner) resolve left, right, top, bottom.
    """
    if width <= 0 or height <= 0:
        return None
    if not (0 <= x < width and 0 <= y < height):
        return None
    bands = (
        (x / width, LEFT),
        ((width - x) / width, RIGHT),
        (y / height, TOP),
        ((height - y) / height, BOTTOM),
    )
    closest, zone = min(bands, key=lambda band: band[0])
    if allow_center and closest >= edge:
        return CENTER
    return zone


def drop_rect(width, height, zone):
    """The rectangle to highlight for *zone*, as (x, y, w, h).

    Half the panel for a border zone — that is the room the dropped panel
    would take — and the whole panel for the tab zone.
    """
    half_w = width // 2
    half_h = height // 2
    if zone == LEFT:
        return (0, 0, half_w, height)
    if zone == RIGHT:
        return (width - half_w, 0, half_w, height)
    if zone == TOP:
        return (0, 0, width, half_h)
    if zone == BOTTOM:
        return (0, height - half_h, width, half_h)
    return (0, 0, width, height)


# ── taking a panel out ───────────────────────────────────────────────────

def remove_panel(node, panel_id):
    """*node* without *panel_id*, or None when nothing would be left.

    Collapses as it goes: a split with one child left *is* that child, and a
    tab strip with one page left is a plain column. Anything else would let
    every drag leave a layer of empty scaffolding behind.
    """
    if isinstance(node, Leaf):
        return None if node.panel == panel_id else node
    if isinstance(node, Tabs):
        return _remove_from_tabs(node, panel_id)
    if isinstance(node, Split):
        return _remove_from_split(node, panel_id)
    return node


def _remove_from_split(node: Split, panel_id: str):
    sized = len(node.sizes) == len(node.children)
    kept: list = []
    shares: list[float] = []
    for index, child in enumerate(node.children):
        pruned = remove_panel(child, panel_id)
        if pruned is None:
            continue
        kept.append(pruned)
        if sized:
            shares.append(node.sizes[index])
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    total = sum(shares)
    sizes = tuple(share / total for share in shares) if sized and total > 0 else ()
    return Split(node.orientation, tuple(kept), sizes, node.resizable,
                 node.spacing, node.margins)


def _remove_from_tabs(node: Tabs, panel_id: str):
    survivors = []
    for index, page in enumerate(node.pages):
        kept = tuple(pid for pid in page if pid != panel_id)
        if kept:
            survivors.append((index, kept))
    if not survivors:
        return None
    if len(survivors) == 1:
        return _page_node(survivors[0][1])
    current = next((new for new, (old, _) in enumerate(survivors)
                    if old >= node.current), len(survivors) - 1)
    return Tabs((), current, tuple(page for _, page in survivors))


def _page_node(panel_ids: tuple[str, ...]):
    """One tab page as a standalone node — a column, like the host builds."""
    if len(panel_ids) == 1:
        return Leaf(panel_ids[0])
    return Split(VERTICAL, tuple(Leaf(pid) for pid in panel_ids), (), False)


# ── putting a panel back ─────────────────────────────────────────────────

def insert_panel(node, panel_id: str, target: str, zone: str):
    """Place *panel_id* next to (or behind) *target*.

    A drop along the parent's own axis just becomes another sibling — that
    is the plain reorder, and it must not turn a content-sized column into a
    draggable splitter. A drop across the axis wraps the target in a new
    split, which the user did ask for by dragging sideways.
    """
    if panel_id == target or target not in node.panels():
        return node
    return _insert(node, panel_id, target, zone)


def _insert(node, panel_id: str, target: str, zone: str):
    if isinstance(node, Leaf):
        return _wrap(node, panel_id, zone) if node.panel == target else node
    if isinstance(node, Tabs):
        return _insert_into_tabs(node, panel_id, target, zone)
    if not isinstance(node, Split):
        return node
    if _ORIENTATION_OF.get(zone) == node.orientation:
        for index, child in enumerate(node.children):
            if isinstance(child, Leaf) and child.panel == target:
                return _insert_sibling(node, index, panel_id, zone)
    children = tuple(_insert(child, panel_id, target, zone)
                     if target in child.panels() else child
                     for child in node.children)
    return Split(node.orientation, children, node.sizes, node.resizable,
                 node.spacing, node.margins)


def _wrap(leaf: Leaf, panel_id: str, zone: str):
    """Replace a leaf with the two-panel node the drop asked for."""
    if zone == CENTER:
        return Tabs((), 1, ((leaf.panel,), (panel_id,)))
    order = ((Leaf(panel_id), leaf) if zone in _BEFORE
             else (leaf, Leaf(panel_id)))
    return Split(_ORIENTATION_OF[zone], order, (0.5, 0.5), True)


def _insert_sibling(node: Split, index: int, panel_id: str, zone: str):
    at = index if zone in _BEFORE else index + 1
    children = list(node.children)
    children.insert(at, Leaf(panel_id))
    sizes: tuple[float, ...] = ()
    if len(node.sizes) == len(node.children):
        # The newcomer takes half of what it was dropped onto; everything
        # else keeps its share.
        shares = list(node.sizes)
        shares[index] = shares[index] / 2.0
        shares.insert(at, shares[index])
        total = sum(shares) or 1.0
        sizes = tuple(share / total for share in shares)
    return Split(node.orientation, tuple(children), sizes, node.resizable,
                 node.spacing, node.margins)


def _insert_into_tabs(node: Tabs, panel_id: str, target: str, zone: str):
    """Drop onto a tabbed panel: a new page, or a neighbour inside one.

    A tab page is a flat column of ids, so it cannot hold a side-by-side
    split — a left/right drop inside a page lands in that column.
    """
    pages = [list(page) for page in node.pages]
    for index, page in enumerate(pages):
        if target not in page:
            continue
        if zone == CENTER:
            pages.insert(index + 1, [panel_id])
            current = index + 1
        else:
            at = page.index(target)
            page.insert(at if zone in _BEFORE else at + 1, panel_id)
            current = index
        return Tabs((), current, tuple(tuple(page) for page in pages))
    return node


# ── whole page / page-merging surgery --------------------------------------

def reorder_tab_page(node: Tabs, from_index: int, to_index: int):
    """*node* with the page at *from_index* moved to *to_index*.

    The current page follows the page it pointed at: a tab the user drags
    keeps being the selected tab. Anything that is not a Tabs (or an
    out-of-range / no-op move) comes back unchanged.
    """
    if not isinstance(node, Tabs):
        return node
    pages = list(node.pages)
    if not pages or not (0 <= from_index < len(pages)
                         and 0 <= to_index < len(pages)
                         and from_index != to_index):
        return node
    page = pages.pop(from_index)
    pages.insert(to_index, page)
    current = node.current
    if current == from_index:
        current = to_index
    elif from_index < current <= to_index:
        current -= 1
    elif to_index <= current < from_index:
        current += 1
    current = max(0, min(current, len(pages) - 1))
    return Tabs((), current, tuple(pages))


def merge_panel_into_page(node: Tabs, panel_id: str, target: str):
    """Move *panel_id* into the page holding *target* (after *target*).

    This is the "drop onto a tab header" gesture: the panel joins an
    existing page instead of making a new one, and an emptied page is
    dropped rather than kept as a ghost tab.
    """
    if not isinstance(node, Tabs):
        return node
    if panel_id == target or panel_id not in node.panels() \
            or target not in node.panels():
        return node
    pages = [list(page) for page in node.pages]
    src = next(i for i, page in enumerate(pages) if panel_id in page)
    dst = next(i for i, page in enumerate(pages) if target in page)
    pages[src].remove(panel_id)
    pages[dst].insert(pages[dst].index(target) + 1, panel_id)
    current = node.current
    if src != dst:
        if not pages[src]:
            del pages[src]
            if current == src:
                current = dst if dst < src else dst - 1
            elif src < current:
                current -= 1
        elif current == src:
            # The source page survives (it had more than one panel); the
            # selected page is where the panel went — the target page.
            current = dst
    if len(pages) == 1:
        return _page_node(tuple(pages[0]))
    current = max(0, min(current, len(pages) - 1))
    return Tabs((), current, tuple(tuple(page) for page in pages))


# ── the whole move ───────────────────────────────────────────────────────

def move_panel(node, source: str, target: str, zone: str):
    """*node* with *source* moved next to *target*, or *node* unchanged.

    Refusing beats guessing: a drop onto itself, an unknown zone, a panel
    this tree does not hold, or a move that would empty the tree all leave
    the arrangement exactly as it was.
    """
    if source == target or zone not in ZONES:
        return node
    placed = node.panels()
    if source not in placed or target not in placed:
        return node
    pruned = remove_panel(node, source)
    if pruned is None or target not in pruned.panels():
        return node
    return insert_panel(pruned, source, target, zone)
