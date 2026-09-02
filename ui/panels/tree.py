"""Dock tree: how panels are arranged, as serialisable data.

A node is one of:

* Leaf   — a single panel
* Tabs   — several panels stacked behind tabs
* Split  — children side by side, horizontally or vertically

The tree is what gets saved to the config, so it must survive a build that
renamed or dropped a panel: parsing prunes unknown ids and collapses whatever
is left instead of refusing to open the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ui.panels import registry

HORIZONTAL = "horizontal"
VERTICAL = "vertical"


@dataclass(frozen=True, slots=True)
class Leaf:
    panel: str

    def panels(self) -> tuple[str, ...]:
        return (self.panel,)

    def to_json(self) -> dict:
        return {"kind": "leaf", "panel": self.panel}


@dataclass(frozen=True, slots=True)
class Tabs:
    """Panel groups stacked behind tabs.

    *items* is legacy: every panel is its own page. *pages* is the current
    form — each page holds several panels, stacked content-sized, behind one
    tab. At most one of them is set.
    """

    items: tuple[str, ...] = ()
    current: int = 0
    pages: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.items and not self.pages:
            object.__setattr__(self, "pages", tuple((pid,) for pid in self.items))
        elif self.pages and not self.items:
            object.__setattr__(self, "items", tuple(
                pid for page in self.pages for pid in page))

    def panels(self) -> tuple[str, ...]:
        return tuple(pid for page in self.pages if page for pid in page)

    def to_json(self) -> dict:
        legacy_items = all(len(page) == 1 for page in self.pages)
        if legacy_items:
            return {"kind": "tabs", "items": list(self.items),
                    "current": self.current}
        return {"kind": "tabs", "pages": [list(page) for page in self.pages],
                "current": self.current}


@dataclass(frozen=True, slots=True)
class Split:
    """Children side by side.

    *resizable* picks the presentation: a draggable splitter, or a plain
    stack whose children keep their own preferred size. The slider column is
    the latter — every block is as tall as its content and the user cannot
    drag between them — so a host that only knew splitters could not
    reproduce today's window.
    """

    orientation: str
    children: tuple["Node", ...]
    sizes: tuple[float, ...] = ()
    resizable: bool = True
    spacing: float = 0.0
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def panels(self) -> tuple[str, ...]:
        found: list[str] = []
        for child in self.children:
            found.extend(child.panels())
        return tuple(found)

    def to_json(self) -> dict:
        data = {
            "kind": "split",
            "orientation": self.orientation,
            "children": [child.to_json() for child in self.children],
            "sizes": list(self.sizes),
        }
        if not self.resizable:
            data["resizable"] = False
        if self.sizes:
            data["sizes"] = list(self.sizes)
        if self.spacing:
            data["spacing"] = self.spacing
        if any(self.margins):
            data["margins"] = list(self.margins)
        return data


Node = Leaf | Tabs | Split


def two_column_tree(panel_ids, spacing=0.0, margins=(0.0, 0.0, 0.0, 0.0),
                    first_count=None):
    """Split *panel_ids* into two draggable columns, packed left first.

    The slider area, laid out side by side, is the first real use of a
    resizable split: the left column holds the first half of the groups, the
    right one the rest, and both sit inside a horizontal splitter whose
    handle the user can drag. *first_count* overrides how many panels go
    left (default: ceil of half).
    """
    ids = tuple(panel_ids)
    if first_count is None:
        first_count = (len(ids) + 1) // 2
    left = ids[:first_count]
    right = ids[first_count:]
    children = []
    if left:
        children.append(Split(VERTICAL, tuple(Leaf(pid) for pid in left),
                              (), False, spacing, margins))
    if right:
        children.append(Split(VERTICAL, tuple(Leaf(pid) for pid in right),
                              (), False, spacing, margins))
    if not children:
        return Split(VERTICAL, ())
    resizable = len(children) > 1
    return Split(HORIZONTAL, tuple(children), (0.5, 0.5), resizable,
                 spacing, margins)


def tabbed_tree(panel_ids, tab_size=2):
    """Stack *panel_ids* behind tabs, *tab_size* panels per page.

    A single page collapses back to a plain column — a QTabWidget with one
    tab is noise, not navigation.
    """
    ids = tuple(panel_ids)
    if tab_size < 1:
        tab_size = 1
    pages = [tuple(ids[i:i + tab_size]) for i in range(0, len(ids), tab_size)]
    pages = tuple(page for page in pages if page)
    if len(pages) <= 1:
        return Split(VERTICAL, tuple(Leaf(pid) for pid in ids))
    return Tabs((), 0, pages)


def default_tree() -> Split:
    """Todays layout: picker on top (satellites ride inside it), then the
    history grid, then the slider blocks — one column."""
    children: list[Node] = [Leaf(registry.PICKER), Leaf(registry.HISTORY)]
    children.extend(Leaf(registry.slider_panel_id(group))
                    for group in registry.SLIDER_GROUPS)
    return Split(VERTICAL, tuple(children))


def _known(panel_id: str) -> bool:
    return registry.panel(panel_id) is not None


def from_json(data) -> Node | None:
    """Parse a saved node, dropping anything this build no longer knows."""
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if kind == "leaf":
        panel_id = data.get("panel")
        return Leaf(panel_id) if isinstance(panel_id, str) and _known(panel_id) else None
    if kind == "tabs":
        pages = data.get("pages")
        if isinstance(pages, list) and pages:
            cleaned = []
            for page in pages:
                if not isinstance(page, list):
                    continue
                ids = tuple(p for p in page if isinstance(p, str) and _known(p))
                if ids:
                    cleaned.append(ids)
            if len(cleaned) <= 1:
                return (Leaf(cleaned[0][0]) if cleaned else None)
            current = data.get("current", 0)
            if not isinstance(current, int) or not 0 <= current < len(cleaned):
                current = 0
            return Tabs((), current, tuple(cleaned))
        items = tuple(p for p in data.get("items", [])
                      if isinstance(p, str) and _known(p))
        if not items:
            return None
        if len(items) == 1:
            return Leaf(items[0])
        current = data.get("current", 0)
        if not isinstance(current, int) or not 0 <= current < len(items):
            current = 0
        return Tabs(items, current)
    if kind == "split":
        children = tuple(node for node in
                         (from_json(child) for child in data.get("children", []))
                         if node is not None)
        if not children:
            return None
        if len(children) == 1:
            return children[0]
        orientation = data.get("orientation")
        if orientation not in (HORIZONTAL, VERTICAL):
            orientation = VERTICAL
        sizes = tuple(float(s) for s in data.get("sizes", [])
                      if isinstance(s, (int, float)))
        if len(sizes) != len(children):
            sizes = ()
        margins = data.get("margins", [])
        if not (isinstance(margins, list) and len(margins) == 4
                and all(isinstance(m, (int, float)) for m in margins)):
            margins = [0.0, 0.0, 0.0, 0.0]
        spacing = data.get("spacing", 0.0)
        if not isinstance(spacing, (int, float)):
            spacing = 0.0
        return Split(orientation, children, sizes,
                     bool(data.get("resizable", True)),
                     float(spacing), tuple(float(m) for m in margins))
    return None


def load(data) -> Node:
    """Parse a saved tree, falling back to the default when nothing is left."""
    return from_json(data) or default_tree()


def missing_panels(node: Node) -> tuple[str, ...]:
    """Registered, non-satellite panels this tree does not place anywhere."""
    placed = set(node.panels())
    return tuple(panel_id for panel_id in registry.panel_ids()
                 if panel_id not in placed
                 and not registry.is_satellite(panel_id))
