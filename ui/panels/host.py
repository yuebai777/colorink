"""PanelHost — renders a dock tree into real widgets.

Part of the panelisation plan. The host owns the *arrangement*; the panels
themselves are supplied by a provider callback, so the host never has to know
what a picker or a slider block is, and the widgets keep whatever owner they
already had (they are re-parented, never re-created — rebuilding a layout
must not destroy a users state).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.panels import rearrange, registry
from ui.panels.drag import PANEL_MIME, DropIndicator, PanelFrame
from ui.panels.tree import HORIZONTAL, VERTICAL, Leaf, Split, Tabs, default_tree

_ORIENTATION = {
    HORIZONTAL: Qt.Orientation.Horizontal,
}


class PanelTabWidget(QTabWidget):
    """QTabWidget whose size hints account for styled pane margin-top."""

    def sizeHint(self) -> QSize:
        host = self.parent()
        if host is not None and hasattr(host, "_tabs") and hasattr(host, "_tabs_hint"):
            node = next((n for t, n in host._tabs if t is self), None)
            if node is not None:
                h = host._tabs_hint(node)
                if h > 0:
                    return QSize(super().sizeHint().width(), h)
        hint = super().sizeHint()
        top_gap = max(0, int(getattr(getattr(host, "_chrome", None), "top_gap", 0) or 0))
        return QSize(hint.width(), hint.height() + top_gap)

    def minimumSizeHint(self) -> QSize:
        host = self.parent()
        if host is not None and hasattr(host, "_tabs") and hasattr(host, "_tabs_hint"):
            node = next((n for t, n in host._tabs if t is self), None)
            if node is not None:
                h = host._tabs_hint(node)
                if h > 0:
                    return QSize(super().minimumSizeHint().width(), h)
        hint = super().minimumSizeHint()
        top_gap = max(0, int(getattr(getattr(host, "_chrome", None), "top_gap", 0) or 0))
        return QSize(hint.width(), hint.height() + top_gap)


class PanelHost(QWidget):
    """Builds (and reads back) a dock tree of panels.

    mount_changed fires after a tree mount, when the stack of panels behind it
    changed (and therefore the window content height may have).
    """

    mount_changed = pyqtSignal()
    #: A tree mount finished. Unlike mount_changed this fires on *every*
    #: rebuild, including one that mounts exactly the same panels — the
    #: window owning this host uses it to re-decide its own visibility once
    #: the transient detach/adopt of a rebuild is over.
    mount_finished = pyqtSignal()
    rearranged = pyqtSignal(object)
    tab_changed = pyqtSignal(int)
    #: A grip was dragged somewhere no host would take it: tear it off.
    float_requested = pyqtSignal(str)
    #: A grip was right-clicked: (panel id, global position).
    menu_requested = pyqtSignal(str, object)

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._tree = default_tree()
        self._splitters: list[tuple[QSplitter, Split]] = []
        self._tabs: list[tuple[QTabWidget, Tabs]] = []
        self._stacks: list[tuple[QWidget, Split]] = []
        self._mounted: dict[str, QWidget] = {}
        self._frames: dict[str, PanelFrame] = {}
        #: Retired grip frames kept alive (hidden, parentless) instead of
        #: deleteLater'd: a drag can still be in flight when a frame is taken
        #: off the tree, and deleting the C++ object under Qt's modal drag
        #: loop (or letting PyQt recycle its wrapper) crashes the process.
        self._parked_frames: list[PanelFrame] = []
        self._floating: set[str] = set()
        self._chrome = None
        self._drag_enabled = False
        #: True while set_tree is rebuilding: holders must not mirror the
        #: transient Hide/Show of a panel that is being detached and adopted.
        self._mounting = False
        #: Whether each panel was hidden *on purpose* when this host last
        #: detached it. setParent(None) marks a widget hidden in passing, and
        #: Qt cannot tell that apart from the user switching the group off —
        #: so the host carries the intent itself across its own re-mounts.
        #: Without it a rebuild either resurrects a group the user hid (a
        #: column of dead grips) or parks a floating window whose panels all
        #: read "hidden".
        self._hidden_intent: dict[str, bool] = {}
        self._indicator: DropIndicator | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._root: QWidget | None = None
        #: When the user asked for tabbed stacking (slidersTabs), the middle
        #: of a panel is a "stack behind tabs" drop zone. Off by default so a
        #: plain column never silently swallows a panel into tabs.
        self._allow_tab_drops = False
        #: Last spacing the theme pass asked for. Tab pages are built from the
        #: Tabs node, which has no spacing field, so they read it back from
        #: here — a rebuild after a drag must keep the same gap as the plain
        #: column, not silently drop to 0.
        self._stack_spacing = 0
        #: (tab bar -> (from, to)) for movable-tab drags, committed on release.
        self._tab_pending: dict = {}
        # Never claim more height than the panels need. An expanding host
        # swallows the window's spare room — with every panel torn off it
        # grew to 600px of nothing while the picker stayed frozen, and the
        # colour wheel could not use the space it looked like it had.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)

    # ── building ─────────────────────────────────────────────────────────

    def set_tree(self, node) -> None:
        """Mount *node*. Panels missing from the provider are skipped."""
        # A re-mount detaches and re-adopts every panel, which fires Hide/Show
        # on them. Holders mirror a panel's visibility onto their own chrome,
        # so without this flag a floating window hid itself when its panel
        # passed through the parentless state and never came back (the panel's
        # own flag was never touched, so no Show ever followed the Hide).
        # The host settles every holder's visibility itself, below.
        self._mounting = True
        try:
            self._set_tree(node)
        finally:
            self._mounting = False

    def _set_tree(self, node) -> None:
        was_mounted = set(self._mounted)
        # Capture current active tab page before detaching so setting adjustments
        # or module refreshes never flip the user's foreground tab.
        prev_active_page = None
        for tabs, _ in self._tabs:
            if tabs is not None and tabs.count() > 0:
                idx = tabs.currentIndex()
                if hasattr(tabs, "_panel_pages") and 0 <= idx < len(tabs._panel_pages):
                    prev_active_page = tabs._panel_pages[idx]
                break
        self._prev_active_page = prev_active_page
        old_containers = ({c for c, _ in self._stacks}
                          | {sp for sp, _ in self._splitters}
                          | {t for t, _ in self._tabs})
        self._detach_mounted()
        self._splitters.clear()
        self._tabs.clear()
        self._stacks.clear()
        self._mounted.clear()
        if self._root is not None:
            self._layout.removeWidget(self._root)
            if self._root in old_containers:
                self._root.setParent(None)
                self._root.hide()
                self._root.deleteLater()
            self._root = None
        self._tree = node
        built = self._build(node)
        if built is not None:
            self._root = built
            self._layout.addWidget(built)
            # Mounting into a VISIBLE host: make the new root visible NOW,
            # not on some later event-loop pass. A QTabWidget's internals
            # stay folded (tab bar height 0, pages unmeasured) until it is
            # actually shown, so the follow-up _adjust_content_height would
            # otherwise measure the column short. While the host is hidden
            # there is nothing to show yet (the deferred height pass waits
            # for showEvent anyway).
            if self.isVisible():
                built.show()
        # Re-apply the visibility each panel had before this rebuild detached
        # it (see _hidden_intent). Runs before _sync_tab_visibility so a page
        # that is not the current one is parked again right after.
        self._restore_hidden_intent()
        # The tab strips were built before they had a parent/visible stack;
        # the stack applies page visibility only when it is shown, so re-apply
        # it now — after the widget is actually mounted — or the first painted
        # frame shows every page at once.
        self._sync_tab_visibility()
        # A remount can drop panels from the tree (a panel torn off or moved
        # to another host). Their old frames were parked by _detach_mounted
        # but still sit in _frames: drop them for good so nothing can ever
        # re-show an empty grip as a blank slot.
        self._drop_stale_frames()
        for panel_id in list(self._hidden_intent):
            if panel_id not in self._mounted:
                self._hidden_intent.pop(panel_id, None)
        self._prev_active_page = None
        if set(self._mounted) != was_mounted:
            # Which panels are on screen decides the content height; the
            # order they sit in does not, so an ordering change stays quiet.
            self.mount_changed.emit()
        self.mount_finished.emit()

    def _remember_hidden(self, panel_id: str, widget) -> None:
        """Record whether *widget* was hidden on purpose before a detach.

        A panel on a tab page that is not the current one is hidden by the
        stack, not by the user: recording that as an intent would pin the
        panel hidden for good as soon as the page is switched once.
        """
        if self._in_background_page(panel_id):
            return
        try:
            self._hidden_intent[panel_id] = bool(widget.isHidden())
        except RuntimeError:
            self._hidden_intent.pop(panel_id, None)

    def _in_background_page(self, panel_id: str) -> bool:
        """True when the panel sits on a tab page that is not the current one."""
        node = self._panel_box(panel_id)
        while node is not None:
            parent = node.parentWidget()
            if parent is None:
                return False
            if isinstance(parent, QStackedWidget):
                return parent.currentWidget() is not node
            node = parent
        return False

    def _restore_hidden_intent(self) -> None:
        """Put every mounted panel back the way this host wants it seen.

        Qt keeps the hidden flag ``setParent(None)`` left on a widget —
        adopting it does not clear that again — so the host has to state the
        answer itself: the visibility it recorded before detaching the panel,
        or "visible" for a panel that arrives from somewhere else (docked
        back, restored into a floating window). ``_sync_tab_visibility()``
        re-parks non-current pages right after this, and the showSliders
        policy pass runs after the mount as well.
        """
        for panel_id in list(self._mounted):
            widget = self._mounted.get(panel_id)
            if widget is None:
                continue
            try:
                widget.setVisible(not self._hidden_intent.get(panel_id, False))
            except RuntimeError:
                continue
            frame = self._frames.get(panel_id)
            if frame is not None and frame.panel() is widget:
                # The grip follows its content: a hidden group must not keep a
                # strip of dead handles above nothing.
                frame.follow_content_visibility()

    def _detach_mounted(self) -> None:
        """Take panel widgets out of the old tree so it can be deleted.

        A framed panel is detached by its frame — pulling the panel out of
        its own grip would drop the pair apart on every re-mount.
        """
        for panel_id, widget in self._mounted.items():
            frame = self._frames.get(panel_id)
            if frame is None:
                # Only a widget that is still parented carries a meaningful
                # hidden flag. One that is already parentless was parked by
                # _release_frames a moment ago (which recorded the real state
                # before setParent(None) hid it in passing) — reading it now
                # would record "hidden" for every panel.
                if widget.parent() is not None:
                    self._remember_hidden(panel_id, widget)
                widget.setParent(None)
                continue
            # Hide the frame as well: a detached widget is a top-level one,
            # and anything that shows it before it is mounted again becomes
            # a stray window. Only frames — a raw panel hidden here would
            # stay hidden, since an explicit hide survives re-parenting.
            # The frame keeps the panel; its own flag is the user's intent and
            # survives the detach, so it is recorded here as well — otherwise
            # the re-mount would read "no intent" and show a group the user
            # had switched off.
            self._remember_hidden(panel_id, widget)
            frame.setParent(None)
            frame.hide()

    def _build(self, node) -> QWidget | None:
        if isinstance(node, Leaf):
            return self._mount(node.panel)
        if isinstance(node, Tabs):
            return self._build_tabs(node)
        if isinstance(node, Split):
            return self._build_split(node)
        return None

    def _mount(self, panel_id: str) -> QWidget | None:
        if panel_id in self._floating:
            # Torn off into its own window: it still belongs to this tree
            # (that is how it finds its way home), it just is not here.
            return None
        widget = self._provider(panel_id)
        if widget is None:
            return None
        self._mounted[panel_id] = widget
        if not self._drag_enabled:
            # The caller's layout adopts it right after this returns, and
            # _build_stack / addTab show it there. Showing it here — while it
            # is still parentless — made it a stray top-level window for a
            # moment, and that show/hide pair is what a floating window
            # mirrored onto itself (see PanelHolder.eventFilter).
            return widget
        frame = self._frames.get(panel_id)
        if frame is None:
            spec = registry.panel(panel_id)
            frame = PanelFrame(panel_id, spec.title if spec else panel_id, self)
            frame.title_bar.float_requested.connect(self.float_requested.emit)
            # Double click says the same thing as dragging it clear of every
            # window, and is the half of it anyone will find.
            frame.title_bar.toggled.connect(self.float_requested.emit)
            frame.title_bar.menu_requested.connect(self.menu_requested.emit)
            frame.title_bar.apply_chrome(self._chrome)
            self._frames[panel_id] = frame
        # Adopt first: set_panel re-parents the widget into the frame (which
        # also clears the hidden flag setParent(None) left behind) and only
        # then decides the frame's visibility from the panel's own flag.
        frame.set_panel(widget)
        # A grip whose content is switched off collapses with it: the host
        # keeps hidden groups mounted so they can come back, and a frame left
        # visible around hidden content is the bare strip that looked like an
        # empty slot under the picker.
        frame.follow_content_visibility()
        return frame

    def _build_tabs(self, node: Tabs) -> QWidget | None:
        # Every page is a content-sized stack of panels (a tab hosting one
        # column; pages were flattened to single-item pages by __post_init__).
        titles = []
        built = []
        page_entries = []
        for page in node.pages:
            page_active = tuple(pid for pid in page
                                if pid not in self._floating and self._provider(pid) is not None)
            if not page_active:
                continue
            first_id = page_active[0]
            spec = registry.panel(first_id)
            if len(page_active) == 1:
                widget = self._mount(first_id)
                self._top_align_panel(widget)
                names = [spec.title if spec else first_id]
            else:
                col = Split(VERTICAL,
                            tuple(Leaf(pid) for pid in page_active), (), False,
                            self._stack_spacing)
                widget = self._build(col)
                names = [(registry.panel(pid).title if registry.panel(pid) else pid)
                         for pid in page_active]
            if widget is None:
                continue
            built.append(widget)
            page_entries.append(page)
            titles.append("/".join(names))
        if not built:
            return None
        if len(built) == 1:
            return built[0]
        tabs = PanelTabWidget(self)
        tabs.setDocumentMode(True)
        for index, (widget, title) in enumerate(zip(built, titles)):
            tabs.addTab(widget, title)
            tabs.setTabToolTip(index, title)
        prev_page = getattr(self, "_prev_active_page", None)
        if prev_page is not None and prev_page in page_entries:
            target_index = page_entries.index(prev_page)
        else:
            target_index = min(node.current, len(built) - 1)
        tabs.setCurrentIndex(target_index)
        #: tab index -> original page tuple (pages with nothing mounted are
        #: skipped from the strip, so bar indices do not match node.pages).
        tabs._panel_pages = tuple(page_entries)
        # QStackedLayout defers page visibility until the stack itself is
        # shown/laid out. A freshly rebuilt tab stack that is inserted into an
        # already-visible host would therefore paint its first frame with every
        # page "not hidden" — the one-frame overlap the user sees right after
        # a drop, which only clears after switching tabs a couple of times.
        # Mark the pages ourselves AND force the internal stack layout to
        # activate, so the first frame already shows one page.
        current = tabs.currentIndex()
        for index in range(tabs.count()):
            tabs.widget(index).setVisible(index == current)
        stack = tabs.findChild(QStackedWidget)
        if stack is not None:
            stack_layout = stack.layout()
            if stack_layout is not None:
                stack_layout.activate()
        self._tabs.append((tabs, node))
        bar = tabs.tabBar()
        # A page is a whole column of panels: the tab names them all. Let tabs
        # take their natural width so combined names like 历史颜色/HSV render in
        # full; if they overflow the strip, Qt shows scroll buttons instead of
        # compressing/eliding the text. ElideRight stays as a fallback for a
        # single over-long name (hover shows the full tooltip).
        bar.setElideMode(Qt.TextElideMode.ElideRight)
        bar.setExpanding(False)
        # Dragging a tab header reorders whole pages; the tree is updated on
        # release (the internal tabMoved signal fires mid-drag, and a rebuild
        # in the middle of the gesture would kill it).
        bar.setMovable(True)
        bar.installEventFilter(self)
        bar.tabMoved.connect(
            lambda frm, to, bar=bar: self._on_tab_moved(bar, frm, to))
        # Re-applied on Paint if Qt re-shows every page (see eventFilter).
        tabs.installEventFilter(self)
        tabs.currentChanged.connect(
            lambda idx, t=tabs: self._on_tab_current_changed(t, idx))
        self._style_tabs(tabs)
        return tabs

    def _on_tab_current_changed(self, tabs: QTabWidget, idx: int) -> None:
        if getattr(self, "_syncing_tabs", False):
            return
        for i, (t, n) in enumerate(self._tabs):
            if t is tabs:
                updated = Tabs(items=n.items, current=idx, pages=n.pages)
                self._tabs[i] = (t, updated)
                break
        self.tab_changed.emit(idx)

    def _sync_tab_visibility(self) -> None:
        """Make each tab strip's non-current pages explicitly hidden.

        QStackedLayout defers page visibility until the stack is shown, then
        briefly re-shows every page while a freshly built tab strip is mounted
        into a visible host; only a later layout pass hides them again. This
        re-applies the current page right after mounting so the first painted
        frame — and the first tab click — already shows one page.
        """
        self._syncing_tabs = True
        try:
            for tabs, _node in self._tabs:
                if tabs is None:
                    continue
                current = tabs.currentIndex()
                for index in range(tabs.count()):
                    tabs.widget(index).setVisible(index == current)
                # QStackedLayout.setCurrentIndex hides the outgoing page and shows
                # the incoming one *synchronously*; a round-trip through another
                # page forces that bookkeeping to run immediately, while the long
                # deferred pass on first show would otherwise light every page up
                # for a frame. Swapping there and back ends on the same page.
                if tabs.count() > 1:
                    other = (current + 1) % tabs.count()
                    tabs.setCurrentIndex(other)
                    tabs.setCurrentIndex(current)
                stack = tabs.findChild(QStackedWidget)
                if stack is not None and stack.layout() is not None:
                    stack.layout().activate()
        finally:
            self._syncing_tabs = False

    def _top_align_panel(self, widget) -> None:
        """Keep a single-panel tab page's content glued to the tab strip.

        The page is stretched to the tallest page's height; without an
        explicit bottom spacer the raw panel's own layout (History's
        container in particular) would centre its content in all that spare
        room. Frames already park a bottom stretch, so this is a no-op for
        them.
        """
        if widget is None:
            return
        box = widget.layout()
        if box is None:
            return
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        if box.count() and box.itemAt(box.count() - 1).spacerItem() is not None:
            return
        box.addStretch(1)

    def _build_split(self, node: Split) -> QWidget | None:
        children = [self._build(child) for child in node.children]
        children = [child for child in children if child is not None]
        if not children:
            return None
        if not node.resizable:
            return self._build_stack(node, children)
        if len(children) == 1:
            return children[0]
        splitter = QSplitter(
            _ORIENTATION.get(node.orientation, Qt.Orientation.Vertical), self)
        splitter.setChildrenCollapsible(False)
        for child in children:
            splitter.addWidget(child)
        if node.sizes and len(node.sizes) == len(children):
            total = sum(node.sizes) or 1.0
            splitter.setSizes([max(1, int(1000 * size / total)) for size in node.sizes])
        self._splitters.append((splitter, node))
        if self._chrome is not None:
            self._style_splitter(splitter)
        return splitter

    def _build_stack(self, node: Split, children: list[QWidget]) -> QWidget:
        """A plain column/row: children keep their own preferred size.

        This is what today's slider area is — blocks as tall as their
        content, with a fixed gap and no draggable handles.
        """
        container = QWidget(self)
        if node.orientation == HORIZONTAL:
            box = QHBoxLayout(container)
        else:
            box = QVBoxLayout(container)
        left, top, right, bottom = (int(m) for m in node.margins)
        box.setContentsMargins(left, top, right, bottom)
        gap = int(node.spacing) if node.spacing > 0 else (self._stack_spacing or 0)
        box.setSpacing(gap)
        for child in children:
            # addWidget adopts the child and shows it only if it is not
            # explicitly hidden — which is exactly the rule a panel needs:
            # the transient hide of setParent(None) is cleared, a group the
            # user switched off stays hidden. Forcing setVisible(True) here
            # instead re-showed the grip of every hidden group (a strip of
            # dead handles) and, because the container was already hidden,
            # nothing later hid it again.
            box.addWidget(child)
        # Park the leftover height at the bottom. Without this Qt spreads it
        # *between* the blocks, so every gap in the column shifts whenever
        # any one block changes height — which is what "touch one thing and
        # everything moves" looked like.
        box.addStretch(1)
        container.setSizePolicy(QSizePolicy.Policy.Preferred,
                                QSizePolicy.Policy.Maximum)
        container.setProperty("_panel_gap", float(node.spacing))
        container.setProperty("_panel_margin", tuple(node.margins))
        self._stacks.append((container, node))
        return container

    def column_hint(self, spacing: float | None = None, margins=None) -> int:
        """Deterministic height of the mounted arrangement, without a layout pass.

        QLayout.sizeHint is unreliable before the first polish (it reads ~16px
        even though the children are hundreds of pixels tall), and the window
        content-height policy runs exactly in that window. So walk the tree
        instead: panels have their own hints, a column adds them up, and a
        row is as tall as its tallest column — two columns side by side must
        not bill the window for both.
        """
        return self._node_hint(self._tree)

    def sizeHint(self) -> QSize:
        """An empty column asks for no height at all.

        QWidget's default sums *every* child, hidden ones included, so a host
        whose panels were all floated or switched off still reported a full
        column height — and the window's content-height policy grew the frame
        to fit that phantom band instead of closing up (the blank strip the
        user sees after dragging the last module out).
        """
        if self.column_hint() <= 0:
            return QSize(super().sizeHint().width(), 0)
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        if self.column_hint() <= 0:
            return QSize(super().minimumSizeHint().width(), 0)
        return super().minimumSizeHint()

    def _panel_hidden(self, panel_id: str) -> bool:
        """True when this panel is off screen because the *user* turned it off.

        Not the same question as "is it visible": a panel on a background tab
        page is not visible either, but it still belongs to the column — its
        page is one of the pages the tab strip has to size, and a drop aimed
        at that page's empty space must still resolve to it. Reading
        ``isVisibleTo`` here made the tab host shrink to the current page and
        left a dead strip below the tabs where drops resolved to nothing.

        So: hidden content only. The group containers the refresh loop drives
        are exactly that — a hidden container makes its panel's frame
        collapse with it (PanelFrame.follow_content_visibility).
        """
        widget = self._panel_box(panel_id) or self._mounted.get(panel_id)
        if widget is None:
            return True
        try:
            if widget.isHidden():
                return True
            inner = getattr(widget, "panel", None)
            inner = inner() if callable(inner) else None
            return inner is not None and inner.isHidden()
        except RuntimeError:
            return True

    def visible_panels(self) -> tuple[str, ...]:
        """Mounted panels the user can actually see right now."""
        return tuple(pid for pid in self._mounted
                     if not self._panel_hidden(pid))

    def _node_hint(self, node) -> int:
        if isinstance(node, Leaf):
            box = self._panel_box(node.panel)
            # A group the user switched off must not bill the window for its
            # height. Qt's own sizeHint() sums hidden children too, so a
            # column whose every remaining panel was hidden still reported a
            # full block — that was the grey band left under the picker after
            # dragging the visible groups out into their own window.
            if box is None or self._panel_hidden(node.panel):
                return 0
            return int(box.sizeHint().height())
        if isinstance(node, Tabs):
            return self._tabs_hint(node)
        if not isinstance(node, Split):
            return 0
        heights = [height for height in
                   (self._node_hint(child) for child in node.children)
                   if height > 0]
        if not heights:
            return 0
        if node.orientation == HORIZONTAL:
            total = max(heights)
        else:
            total = sum(heights) + self._stack_gap(node) * (len(heights) - 1)
        return total + int(node.margins[1]) + int(node.margins[3])

    def _stack_gap(self, node: Split) -> int:
        """The gap actually in effect — set_stack_spacing may have retuned it."""
        for container, source in self._stacks:
            if source is node:
                box = container.layout()
                if box is not None:
                    return max(0, box.spacing())
        return int(node.spacing)

    def _tabs_hint(self, node: Tabs) -> int:
        """Only one page shows at a time: the tallest one, plus the tab bar."""
        pages = []
        for page in node.pages:
            panel_heights = []
            for pid in page:
                box = self._panel_box(pid)
                # Hidden groups do not bill the window here either (same rule
                # as _node_hint).
                if box is not None and not self._panel_hidden(pid):
                    h = max(int(box.sizeHint().height()),
                            int(box.minimumSizeHint().height()))
                    panel_heights.append(h)
            if not panel_heights:
                continue
            page_h = sum(panel_heights)
            if len(panel_heights) > 1:
                page_h += self._stack_spacing * (len(panel_heights) - 1)
            pages.append(page_h)

        bar = 0
        top_gap = max(0, int(getattr(self._chrome, "top_gap", 0) or 0))
        for tabs, source in self._tabs:
            if source is node and tabs.tabBar() is not None:
                tab_bar = tabs.tabBar()
                bar = max(int(tab_bar.sizeHint().height()),
                          int(tab_bar.height()))
                break
        if not pages:
            # Nothing is mounted on any page. The pane's top gap is chrome for
            # content that is not there; billing it kept a band of nothing
            # under the picker after every panel was dragged out (measured
            # 21px where the window needs 9). A tab strip that is still
            # mounted is real chrome, so it is still counted.
            return bar
        return (max(pages) if pages else 0) + bar + top_gap

    def apply_chrome(self, chrome) -> None:
        """Push the window theme down to every grip strip."""
        self._chrome = chrome
        diff_space = getattr(chrome, "diff_space", None)
        if diff_space is not None:
            self.set_stack_spacing(diff_space)
        for frame in self._frames.values():
            frame.title_bar.apply_chrome(chrome)
        for tabs, _node in self._tabs:
            self._style_tabs(tabs)
        for splitter, _node in self._splitters:
            self._style_splitter(splitter)

    def _style_splitter(self, splitter: QSplitter) -> None:
        """Style QSplitter handles with transparent background and theme spacing."""
        chrome = self._chrome
        if chrome is None:
            return
        diff_space = max(2, int(getattr(chrome, "diff_space", 8) or 8))
        splitter.setHandleWidth(diff_space)
        splitter.setStyleSheet("""
            QSplitter {
                background: transparent;
                border: none;
            }
            QSplitter::handle {
                background: transparent;
            }
        """)

    def _style_tabs(self, tabs: QTabWidget) -> None:
        """Paint the tab strip with the same chrome as the grips/window.

        QTabWidget defaults to Qt's palette (a Windows blue highlight and
        grey body), which is the only thing in the window that never changed
        with the theme; the tab strip is part of the panel chrome, so it
        wears the title band's colour, the resolved text colours, the divider
        and the UI scale.
        """
        chrome = self._chrome
        if chrome is None:
            return
        scale = max(0.5, float(getattr(chrome, "scale", 1.0) or 1.0))
        pad_x = max(2, int(round(5 * scale)))
        pad_y = max(2, int(round(3 * scale)))
        font = max(7, int(round(
            (getattr(chrome, "font_size", 0) or 11) * 0.85)))
        bar_bg = getattr(chrome, "bar_bg", "") or "transparent"
        bg = getattr(chrome, "background", "") or "transparent"
        text = getattr(chrome, "text", "") or "#222222"
        bar_text = getattr(chrome, "bar_text", "") or text
        divider = getattr(chrome, "divider_color", "") or bar_bg
        divider_w = max(1, int(getattr(chrome, "divider_width", 0) or 1))
        top_gap = max(0, int(getattr(chrome, "top_gap", 0) or 0))
        css = (
            "QTabWidget { background: transparent; }"
            "QTabWidget::pane { border: none; background: transparent;"
            f" margin-top: {top_gap}px;"
            " }"
            "QTabBar { background: transparent;"
            f" font-size: {font}px;"
            " }"
            "QTabBar::tab {"
            f" background: {bar_bg}; color: {bar_text};"
            f" padding: {pad_y}px {pad_x}px;"
            f" font-size: {font}px; border: none;"
            " }"
            "QTabBar::tab:selected {"
            f" background: {bg}; color: {text};"
            f" border-top: {divider_w}px solid {divider};"
            " }"
            f"QTabBar::tab:hover:!selected {{ color: {text}; }}"
        )
        if getattr(tabs, "_panel_css", None) == css:
            return
        tabs._panel_css = css
        tabs.setStyleSheet(css)
        tabs.updateGeometry()

    def set_stack_spacing(self, spacing: float) -> None:
        """Retune the gap between stacked panels without a rebuild.

        The theme pass changes this with the UI scale; rebuilding the tree
        for it would re-parent every panel on each pass.
        """
        self._stack_spacing = max(0, int(spacing))
        for container, _node in self._stacks:
            box = container.layout()
            if box is not None:
                box.setSpacing(max(0, int(spacing)))

    # ── drag to rearrange ────────────────────────────────────────────────

    def set_drag_enabled(self, enabled: bool) -> None:
        """Show (or hide) the grip strips and accept panel drops.

        Off by default: the grips are chrome the classic window never had,
        so a user who does not rearrange keeps exactly today's pixels.
        """
        enabled = bool(enabled)
        if enabled == self._drag_enabled:
            return
        self._drag_enabled = enabled
        self.setAcceptDrops(enabled)
        if not enabled:
            self._release_frames()
        if self._root is None and not self._mounted:
            # Nothing mounted yet: the caller is about to set a tree, and
            # mounting the *default* one here would ask the provider for
            # panels this host was never meant to own — the picker among
            # them, which would be pulled out of the main window.
            return
        self.set_tree(self._tree)

    def drag_enabled(self) -> bool:
        return self._drag_enabled

    def set_allow_tab_drops(self, enabled: bool) -> None:
        """Enable/disable the center "stack behind tabs" drop zone.

        Tied to the user's slidersTabs setting: with tabbed stacking on, the
        middle of a panel means "add a tab", so dragging into the stack works;
        with it off, the middle is still a normal reorder zone (four sides).
        """
        self._allow_tab_drops = bool(enabled)

    def _park_frame(self, frame: PanelFrame) -> None:
        """Retire a grip frame without deleting it.

        The panel is handed back and the frame becomes a hidden, parentless
        widget that is never added to a layout again. Keeping the C++ object
        alive (only the Python list reference is dropped later, when the host
        is torn down) is deliberate: under Qt's modal QDrag loop a
        ``deleteLater`` frame can still be visited by ``drop_target_at``, and
        PyQt wrapper recycling then turns a live-looking pointer into a freed
        one — the access violation seen while dragging the third panel.
        """
        frame.take_panel()
        frame.setParent(None)
        frame.hide()
        self._parked_frames.append(frame)

    def set_floating_panels(self, panel_ids, remount: bool = True) -> None:
        """Panels torn off into their own windows: mounted by someone else.

        The tree is left alone — it is what lets a floated panel dock back
        into the slot it came from. The source frame is parked, never deleted
        (see :meth:`_park_frame`).

        With *remount* off the caller is going to re-mount the column itself
        straight after (the float/dock paths all end in a refresh), so this
        only updates the parked frames and the floating set. Doing the
        re-mount here as well was one of the two full column rebuilds every
        single tear-off cost.
        """
        wanted = set(panel_ids or ())
        if wanted == self._floating:
            return
        self._floating = wanted
        for pid in self._floating:
            frame = self._frames.pop(pid, None)
            if frame is not None:
                self._park_frame(frame)
            # A panel that is now floating must not stay in the mounted
            # bookkeeping: the next re-mount would detach it again, and that
            # detach (setParent(None)) tears it straight out of the floating
            # window that just adopted it.
            widget = self._mounted.pop(pid, None)
            if widget is not None and widget.parent() is None:
                widget.hide()
        if remount:
            self.set_tree(self._tree)

    def floating_panels(self) -> tuple[str, ...]:
        return tuple(sorted(self._floating))

    def frame_for(self, panel_id: str) -> PanelFrame | None:
        return self._frames.get(panel_id)

    def _release_frames(self) -> None:
        """Hand every panel back and park the frames (never delete them).

        The panel's own visibility is remembered first: take_panel() detaches
        it with setParent(None), which hides it in passing, and the re-mount
        that follows has to know whether it was hidden on purpose.
        """
        for panel_id, frame in list(self._frames.items()):
            widget = frame.panel()
            if widget is not None:
                self._remember_hidden(panel_id, widget)
            self._park_frame(frame)
        self._frames.clear()

    def _drop_stale_frames(self) -> None:
        """Park frames whose panel is no longer in the mounted tree.

        A remount that dropped panels (tear-off, move to another host) leaves
        the old frame in ``_frames`` — hidden and parentless, so it is
        invisible, and ``_panel_box`` only uses a frame that actually holds a
        panel. Keeping the C++ object alive (instead of deleteLater) avoids
        PyQt wrapper recycling turning a live-looking frame into a freed
        pointer mid-drag.
        """
        for pid in list(self._frames):
            if pid in self._mounted:
                continue
            frame = self._frames.pop(pid, None)
            if frame is not None:
                self._park_frame(frame)

    def _panel_box(self, panel_id: str) -> QWidget | None:
        """What occupies the arrangement slot: the frame, or the panel."""
        frame = self._frames.get(panel_id)
        if frame is not None and frame.panel() is not None:
            return frame
        return self._mounted.get(panel_id)

    def _panel_lives_in_tabs(self, panel_id: str) -> bool:
        """True when *panel_id* is inside a Tabs node of the mounted tree.

        The center "stack behind tabs" drop zone is only meaningful when the
        target is already tabbed (or the user asked for tabs): dropping onto
        a plain stacked panel should stay "move beside it", not silently
        swallow it behind a tab.
        """

        def walk(node) -> bool:
            if isinstance(node, Tabs):
                return panel_id in node.panels()
            if isinstance(node, Split):
                return any(walk(child) for child in node.children)
            return False

        return walk(self._tree)

    # ── tab header reorder ────────────────────────────────────────────────

    def _on_tab_moved(self, bar, from_index: int, to_index: int) -> None:
        """Remember an in-flight tab drag; committed when the button lifts.

        ``QTabBar.tabMoved`` fires on every crossing while the mouse is
        still down, so rebuilding the host there would cancel the gesture.
        """
        self._tab_pending[bar] = (from_index, to_index)

    def eventFilter(self, obj, event):
        pending = self._tab_pending.get(obj)
        if pending is not None and event.type() == QEvent.Type.MouseButtonRelease:
            self._tab_pending.pop(obj, None)
            self._commit_tab_reorder(obj, *pending)
        # Qt can re-show every page of a freshly mounted tab stack while it is
        # first laid out; the Paint filter runs before the widget paints, so
        # re-applying the current page here keeps every frame single-page.
        if isinstance(obj, QTabWidget) and event.type() == QEvent.Type.Paint:
            current = obj.currentIndex()
            if any(not obj.widget(i).isHidden()
                   for i in range(obj.count()) if i != current):
                self._sync_tab_visibility()
        return super().eventFilter(obj, event)

    def _commit_tab_reorder(self, bar, from_index: int, to_index: int) -> None:
        """Apply an ended tab drag: reorder the pages, re-mount, announce."""
        node = next((source for tabs, source in self._tabs
                     if tabs is not None and tabs.tabBar() is bar), None)
        if node is None:
            return
        moved = rearrange.reorder_tab_page(node, from_index, to_index)
        if moved is node:
            return
        self.set_tree(moved)
        self.rearranged.emit(moved)

    def check_tab_hover(self, pos: QPoint) -> None:
        """Switch tab if pos hovers over a different tab header during drag."""
        for tabs, _node in self._tabs:
            if not self._box_is_mine(tabs) or not tabs.isVisibleTo(self):
                continue
            bar = tabs.tabBar()
            if bar is None or bar.isHidden() or not self._box_is_mine(bar):
                continue
            pos_in_bar = bar.mapFrom(self, pos)
            if bar.rect().contains(pos_in_bar):
                index = bar.tabAt(pos_in_bar)
                if 0 <= index < tabs.count() and index != tabs.currentIndex():
                    tabs.setCurrentIndex(index)

    @staticmethod
    def _is_deleted(obj) -> bool:
        """True when the C++ object behind *obj* is gone.

        Used as a defensive net around drag-time geometry walks: a race can
        leave a stale widget in ``_mounted``/``_frames`` (another drop's
        deferred surgery, a window torn down mid-drag) whose wrapper still
        exists. Calling a method on such an object is what turned into an
        access violation in ``drop_target_at``.
        """
        try:
            from PyQt6 import sip
            return bool(sip.isdeleted(obj))
        except Exception:
            return False

    def _box_is_mine(self, widget) -> bool:
        """True when *widget* is alive AND still a descendant of this host.

        Alive is not enough. QWidget::mapTo / mapFrom walk the
        ``parentWidget()`` chain until they reach the target widget and
        dereference the NULL they get instead when the chain never does —
        so a live panel that was handed to a floating window while this
        host still listed it turned a geometry read into a native access
        violation (three identical crashes in faulthandler.log, all in
        drop_target_at; sip.isdeleted() is helpless there because the
        widget is alive, it just lives somewhere else now). The paths that
        hand panels between windows are deferred and rebuilt on purpose;
        this check is the hard floor that makes any window between those
        steps safe instead of fatal, and lets a genuinely stale bookkeeping
        entry degrade to "no drop target" rather than a crash.
        """
        if widget is None:
            return False
        try:
            if self._is_deleted(widget) or self._is_deleted(self):
                return False
            node = widget
            while node is not None:
                if node is self:
                    return True
                node = node.parentWidget()
        except RuntimeError:
            return False
        return False

    def drop_target_at(self, pos: QPoint):
        """(panel_id, zone) under a host-local point, or None.

        Ties go to the smaller panel: a drop is meant for the thing you can
        see under the cursor, and panels never overlap except by nesting.
        """
        if self._is_deleted(self):
            return None
        # A point on a page's tab header means "merge the dragged panel into
        # that page" — more direct than switching to the page first and
        # aiming at its edge.
        for tabs, node in self._tabs:
            if not self._box_is_mine(tabs):
                continue
            bar = tabs.tabBar()
            if bar is None or bar.isHidden() or not self._box_is_mine(bar):
                continue
            bar_rect = QRect(bar.mapTo(self, QPoint(0, 0)), bar.size())
            if not bar_rect.contains(pos):
                continue
            index = bar.tabAt(bar.mapFrom(self, pos))
            pages = getattr(tabs, "_panel_pages", None) or node.pages
            if (isinstance(node, Tabs) and 0 <= index < len(pages)
                    and pages[index]):
                # The page's last panel may be the very panel being dragged
                # / floated right now (not mounted) — aim at a mounted panel
                # in that page, or skip the header entirely. Otherwise the
                # drop hint would dereference a None box.
                target_panel = None
                for panel_id in reversed(pages[index]):
                    if panel_id in self._mounted:
                        target_panel = panel_id
                        break
                if target_panel is not None:
                    return (target_panel, rearrange.MERGE_PAGE)
                continue
        found = None
        for panel_id in self._mounted:
            box = self._panel_box(panel_id)
            # isHidden() is NOT enough: a frame inside a non-current tab page
            # reports "not hidden" (only its ancestor page is hidden), so it
            # would steal drops aimed at the visible page and the panel would
            # get stuffed into a page the user cannot even see. isVisibleTo()
            # walks the explicit hidden flags up to this host and is correct
            # even for a host that is not shown yet.
            # _box_is_mine covers the rest: deleted, parentless, or a panel
            # already adopted by a floating window — mapTo() on any of those
            # is the native crash this loop must never reach.
            if (box is None or not self._box_is_mine(box)
                    or not box.isVisibleTo(self)):
                continue
            rect = box.rect()
            tl = box.mapTo(self, rect.topLeft())
            global_rect = QRect(tl, rect.size())
            if not global_rect.contains(pos):
                continue
            local = box.mapFrom(self, pos)
            content = getattr(box, "_panel", None)
            if content is not None and content.isVisibleTo(box):
                content_bottom = content.y() + content.height()
                if local.y() >= content_bottom:
                    zone = rearrange.BOTTOM
                else:
                    zone = rearrange.zone_at(
                        box.width(), max(1, content_bottom),
                        local.x(), local.y(),
                        allow_center=(self._allow_tab_drops
                                      or self._panel_lives_in_tabs(panel_id)))
            else:
                zone = rearrange.zone_at(
                    box.width(), box.height(),
                    local.x(), local.y(),
                    allow_center=(self._allow_tab_drops
                                  or self._panel_lives_in_tabs(panel_id)))
            if zone is None:
                continue
            area = box.width() * box.height()
            if found is None or area < found[0]:
                found = (area, panel_id, zone)
        if found is not None:
            return (found[1], found[2])

        # Check if cursor is in the empty space of an active tab page (e.g. stretch below panels)
        for tabs, node in self._tabs:
            if not self._box_is_mine(tabs) or not tabs.isVisibleTo(self):
                continue
            bar = tabs.tabBar()
            if bar is None or not self._box_is_mine(bar):
                continue
            bar_bottom = bar.mapTo(self, QPoint(0, bar.height())).y()
            tabs_rect = QRect(tabs.mapTo(self, QPoint(0, 0)), tabs.size())
            if tabs_rect.contains(pos) and pos.y() >= bar_bottom:
                curr_idx = tabs.currentIndex()
                pages = getattr(tabs, "_panel_pages", None) or (node.pages if isinstance(node, Tabs) else ())
                if 0 <= curr_idx < len(pages):
                    page_pids = []
                    for pid in pages[curr_idx]:
                        if pid not in self._mounted:
                            continue
                        b = self._panel_box(pid)
                        if not self._box_is_mine(b):
                            continue
                        if b.isVisibleTo(self):
                            page_pids.append(pid)
                    if page_pids:
                        last_box = self._panel_box(page_pids[-1])
                        if not self._box_is_mine(last_box):
                            continue
                        last_bottom = last_box.mapTo(self, QPoint(0, last_box.height())).y()
                        if pos.y() >= last_bottom:
                            return (page_pids[-1], rearrange.BOTTOM)
                        first_box = self._panel_box(page_pids[0])
                        if not self._box_is_mine(first_box):
                            continue
                        first_top = first_box.mapTo(self, QPoint(0, 0)).y()
                        if pos.y() <= first_top:
                            return (page_pids[0], rearrange.TOP)
                        for i in range(len(page_pids) - 1):
                            bi = self._panel_box(page_pids[i])
                            bj = self._panel_box(page_pids[i + 1])
                            if not (self._box_is_mine(bi)
                                    and self._box_is_mine(bj)):
                                continue
                            b_bottom = bi.mapTo(self, QPoint(0, bi.height())).y()
                            b_next_top = bj.mapTo(self, QPoint(0, 0)).y()
                            if b_bottom <= pos.y() <= b_next_top:
                                return (page_pids[i], rearrange.BOTTOM)

        return None

    def show_drop_hint(self, pos: QPoint):
        """Highlight where a drop at *pos* would land; returns the target."""
        self.check_tab_hover(pos)
        target = self.drop_target_at(pos)
        if target is None:
            self.clear_drop_hint()
            return None
        box = self._panel_box(target[0])
        if not self._box_is_mine(box):
            self.clear_drop_hint()
            return None
        zone = target[1]
        if zone == rearrange.MERGE_PAGE:
            # The whole panel is the landing zone; the page's header just
            # points at it.
            zone = rearrange.CENTER
        content = getattr(box, "_panel", None)
        effective_h = box.height()
        if content is not None and content.isVisibleTo(box):
            effective_h = max(1, content.y() + content.height())
        x, y, width, height = rearrange.drop_rect(box.width(), effective_h,
                                                  zone)
        if self._indicator is None:
            self._indicator = DropIndicator(self)
        self._indicator.setGeometry(
            QRect(box.mapTo(self, QPoint(x, y)), QSize(width, height)))
        self._indicator.show()
        self._indicator.raise_()
        return target

    def clear_drop_hint(self) -> None:
        if self._indicator is not None:
            self._indicator.hide()

    def drop_hint_rect(self):
        """The highlighted rectangle, or None when nothing is highlighted."""
        if self._indicator is None or self._indicator.isHidden():
            return None
        return self._indicator.geometry()

    def _owns_panel(self, panel_id: str) -> bool:
        """True when *panel_id* is one of this host's own mounted panels.

        A drop is only a re-arrangement *within* the host that owns the panel.
        The tree may legitimately name panels this host does not hold — a
        torn-off slot stays in the tree so the panel can find its way home —
        and Qt delivers a drop to the innermost widget under the cursor, which
        for a panel dragged over a floating window is that window's own inner
        host. Without this check that host ran ``move_panel`` on a panel it
        never mounted, adopted the widget into its own tree, and left the
        owning host still listing it: one panel with two homes, a bare grip
        and a phantom height in the column it left (the reported residue).
        Cross-window drops belong to the window layer, which routes them
        through ``panel_dropped_here``.
        """
        if panel_id in self._mounted:
            return True
        # A panel being dragged within this host is mounted; the tree alone is
        # not enough (it keeps the slots of floated panels).
        return False

    def apply_drop(self, panel_id: str, pos: QPoint) -> bool:
        """Move *panel_id* to whatever is under *pos*. True when it moved."""
        if not self._owns_panel(panel_id):
            return False
        target = self.drop_target_at(pos)
        self.clear_drop_hint()
        if target is None:
            return False
        if target[1] == rearrange.MERGE_PAGE:
            target_panel = target[0]
            if target_panel == panel_id:
                for tabs, node in self._tabs:
                    if not self._box_is_mine(tabs):
                        continue
                    bar = tabs.tabBar()
                    if (bar is not None and not bar.isHidden()
                            and self._box_is_mine(bar)):
                        pos_in_bar = bar.mapFrom(self, pos)
                        if bar.rect().contains(pos_in_bar):
                            idx = bar.tabAt(pos_in_bar)
                            pages = getattr(tabs, "_panel_pages", None) or (node.pages if isinstance(node, Tabs) else ())
                            if 0 <= idx < len(pages):
                                others = [p for p in pages[idx] if p != panel_id]
                                if others:
                                    target_panel = others[-1]
            moved = rearrange.merge_panel_into_page(
                self._tree, panel_id, target_panel)
        else:
            moved = rearrange.move_panel(
                self._tree, panel_id, target[0], target[1])
        if moved == self._tree:
            return False
        self.set_tree(moved)
        self.rearranged.emit(moved)
        return True

    # ── drag events ──────────────────────────────────────────────────────

    @staticmethod
    def _dragged_panel(event) -> str | None:
        data = event.mimeData()
        if data is None or not data.hasFormat(PANEL_MIME):
            return None
        return bytes(data.data(PANEL_MIME)).decode("utf-8", "ignore") or None

    def dragEnterEvent(self, event):
        if self._is_deleted(self):
            event.ignore()
            return
        if self._drag_enabled and self._dragged_panel(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._is_deleted(self):
            event.ignore()
            return
        panel_id = self._dragged_panel(event)
        if not self._drag_enabled or panel_id is None:
            event.ignore()
            return
        if self.show_drop_hint(event.position().toPoint()) is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.clear_drop_hint()

    def dropEvent(self, event):
        if self._is_deleted(self):
            event.ignore()
            return
        panel_id = self._dragged_panel(event)
        if panel_id is None or not self.apply_drop(
                panel_id, event.position().toPoint()):
            self.clear_drop_hint()
            event.ignore()
            return
        event.acceptProposedAction()

    # ── reading back ─────────────────────────────────────────────────────

    def tree(self):
        """The mounted tree, with each splitters current proportions."""
        return self._read(self._tree)

    def _read(self, node):
        if isinstance(node, Split):
            children = tuple(self._read(child) for child in node.children)
            sizes = node.sizes
            for splitter, source in self._splitters:
                if source is node:
                    live = splitter.sizes()
                    total = sum(live)
                    if total > 0 and len(live) == len(children):
                        sizes = tuple(value / total for value in live)
                    break
            return Split(node.orientation, children, sizes, node.resizable,
                         node.spacing, node.margins)
        if isinstance(node, Tabs):
            for tabs, source in self._tabs:
                if source is node or (hasattr(source, "pages") and source.pages == node.pages):
                    # Keep the pages: rebuilding from *items* alone would
                    # break a page holding several panels into one tab each.
                    return Tabs((), max(0, tabs.currentIndex()), node.pages)
        return node

    def widget_for(self, panel_id: str) -> QWidget | None:
        return self._mounted.get(panel_id)

    def mounted_panels(self) -> tuple[str, ...]:
        return tuple(self._mounted)
