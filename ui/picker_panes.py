"""Picker panes: PaneWithModeButton, WheelPane, and LabPane for the color-wheel / LAB visualizer stack.

Extracted from ui.main_window to keep the two picker UI concerns —
floating mode-button layout and pane construction — in a focused module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QTabBar, QWidget

from ui.ringless_mode import centered_control_offset
from ui.panels.drag import PANEL_MIME
from ui.wheel_grip import WHEEL_GRIP_MIME

if TYPE_CHECKING:
    from ui.ringless_mode import RinglessLayout


@dataclass(frozen=True, slots=True)
class ButtonPositions:
    """Pixel positions for the mode-button cluster in ringless top-bar layout."""
    module_x: int
    mode_x: int
    y: int


def ringless_button_positions(
    pane_width: int,
    button_size: int,
    layout: RinglessLayout,
    pane_height: int | None = None,
) -> ButtonPositions:
    """Compute top-bar horizontal positions and vertical center for ringless buttons.

    Swatches-right → buttons-anchor-left; swatches-left → buttons-anchor-right.
    In both clusters module button is left of mode button in screen coordinates.
    """
    y = centered_control_offset(layout.control_bar_height, button_size)
    if layout.control_bar_position == "bottom" and pane_height is not None:
        y += max(0, pane_height - layout.control_bar_height)
    if layout.controls_side == "right":
        module_x = layout.margin
        mode_x = module_x + button_size + layout.button_gap
    else:
        mode_x = pane_width - layout.margin - button_size
        module_x = mode_x - layout.button_gap - button_size
    return ButtonPositions(module_x=module_x, mode_x=mode_x, y=y)


class PaneWithModeButton(QWidget):
    """Base for stacked panes that own a floating mode-switcher button.

    When ringless mode is active (``_ringless_layout.controls_enabled``), the
    mode/module buttons anchor to the top control-bar opposite the swatches.
    Otherwise they keep the classic bottom-right corner placement.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode_btn = None
        self._module_btn = None       # optional second button (left of mode)
        self._extra_btn = None        # optional third button (left of module)
        self._btn_size = 28           # px, updated by MainWindow from uiScale
        self._btn_margin = 6
        self._btn_gap = 4
        self._reserve_module_slot = False
        self._ringless_layout: RinglessLayout | None = None

    # ── Button setters (trigger reposition) ──────────────────────────────

    def set_mode_button(self, btn):
        self._mode_btn = btn
        self._reposition_mode_button()

    def set_module_button(self, btn):
        """Optional second button placed to the LEFT of the mode button."""
        self._module_btn = btn
        self._reserve_module_slot = btn is not None
        self._reposition_mode_button()

    def set_extra_button(self, btn):
        """Optional third button placed to the LEFT of the module button."""
        self._extra_btn = btn
        self._reposition_mode_button()

    def set_module_slot_reserved(self, reserved: bool) -> None:
        self._reserve_module_slot = reserved
        self._reposition_mode_button()

    def set_mode_button_metrics(self, size, margin):
        self._btn_size = size
        self._btn_margin = margin
        self._reposition_mode_button()

    def set_ringless_layout(self, layout: RinglessLayout) -> None:
        """Apply a ringless layout (or None to revert to legacy bottom-right)."""
        self._ringless_layout = layout
        self._reposition_mode_button()

    # ── Qt event overrides ───────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_mode_button()

    # ── Reposition dispatch ──────────────────────────────────────────────

    def _reposition_mode_button(self):
        if self._ringless_layout is not None and self._ringless_layout.controls_enabled:
            self._reposition_ringless()
        else:
            self._reposition_legacy()

    # ── Generic visible-button packing ────────────────────────────────────

    def _pack_visible_buttons(
        self, order, edge, y, bw, bh, gap, anchor_left,
    ) -> None:
        """Pack only *visible* buttons consecutively from an edge.

        *order* is the spatial left→right order of the full button cluster;
        hidden buttons do not reserve space, so a lone visible button sits
        right at the edge (corner / control-bar edge).
        """
        visible = [b for b in order if b is not None and not b.isHidden()]
        if anchor_left:
            x = edge
            for btn in visible:
                btn.setFixedSize(bw, bh)
                btn.setGeometry(x, y, bw, bh)
                btn.raise_()
                x += bw + gap
        else:
            x = edge
            for btn in reversed(visible):
                btn.setFixedSize(bw, bh)
                btn.setGeometry(x - bw, y, bw, bh)
                btn.raise_()
                x -= bw + gap

    # ── Legacy bottom-right ───────────────────────────────────────────────

    def _reposition_legacy(self):
        bw = self._btn_size
        bh = self._btn_size
        m = self._btn_margin
        gap = self._btn_gap
        pw = self.width()
        ph = self.height()
        y = ph - m - bh
        if y < 0:
            return
        # Full cluster, left→right: [extra, module, mode].
        order = [self._extra_btn, self._module_btn, self._mode_btn]
        self._pack_visible_buttons(
            order, pw - m, y, bw, bh, gap, anchor_left=False)

    # ── Ringless top-bar anchoring ────────────────────────────────────────

    def _reposition_ringless(self):
        bw = self._btn_size
        layout = self._ringless_layout
        assert layout is not None
        positions = ringless_button_positions(
            self.width(), bw, layout, self.height(),
        )
        if layout.controls_side == "right":
            # Swatches on the right → button cluster anchored at the left edge.
            # Full cluster, left→right: [module, mode, extra].
            order = [self._module_btn, self._mode_btn, self._extra_btn]
            edge = layout.margin
        else:
            # Swatches on the left → button cluster anchored at the right edge.
            # Full cluster, left→right: [extra, module, mode].
            order = [self._extra_btn, self._module_btn, self._mode_btn]
            edge = self.width() - layout.margin
        self._pack_visible_buttons(
            order, edge, positions.y, bw, bw, layout.button_gap,
            anchor_left=(layout.controls_side == "right"),
        )


class WheelPane(PaneWithModeButton):
    """Pane hosting the HSV/OKLCh color wheel + its floating mode button."""
    pass


class LabPane(PaneWithModeButton):
    """Pane for the LAB visualizer; also paints a tiled checkerboard background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_checkerboard = True
        self.checker_pixmap = QPixmap(16, 16)
        self.checker_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self.checker_pixmap)
        painter.fillRect(0, 0, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 8, 8, 8, QColor(255, 255, 255, 40))
        painter.fillRect(8, 0, 8, 8, QColor(0, 0, 0, 15))
        painter.fillRect(0, 8, 8, 8, QColor(0, 0, 0, 15))
        painter.end()

    def set_show_checkerboard(self, show: bool):
        show = bool(show)
        if self._show_checkerboard != show:
            self._show_checkerboard = show
            self.update()

    def paintEvent(self, event):
        if self._show_checkerboard:
            painter = QPainter(self)
            painter.drawTiledPixmap(self.rect(), self.checker_pixmap)
            painter.end()


def _visible_picker_tab_height(tab_bar) -> int:
    """Height of the picker tab bar when visible; 0 when absent or hidden."""
    if tab_bar is None or not tab_bar.isVisible():
        return 0
    sh = getattr(tab_bar, "sizeHint", None)
    if callable(sh):
        try:
            h = int(sh().height())
            if h > 0:
                return h
        except (TypeError, ValueError):
            pass
    height = getattr(tab_bar, "height", None)
    if callable(height):
        try:
            return max(0, int(height()))
        except (TypeError, ValueError):
            pass
    return 0


def visible_picker_chrome_height(owner) -> int:
    """Tab strip + wheel grip strip: the chrome above the picker squared area."""
    from ui.wheel_grip import visible_wheel_grip_height
    total = (_visible_picker_tab_height(getattr(owner, "picker_tab_bar", None))
             + visible_wheel_grip_height(getattr(owner, "wheel_grip_bar", None)))
    gap = getattr(owner, "picker_top_gap", None)
    if gap is not None and gap.isVisible():
        total += max(0, int(gap.height()))
    return total


class PickerTabBar(QTabBar):
    """Draggable tab strip at the top of the picker area.

    Switches among enabled wheel modules (HSV / VHSV / HLS / RGB / LCH) on the
    wheel page and between LAB / OKLab on the LAB page. Matches the visual
    chrome of PanelHost's slider tab strip.
    """

    #: A wheel grip was dropped on the strip: (panel id, tab index).
    wheelGripDropped = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PickerTabBar")
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._panel_css = None
        self._drop_controller = None

    def set_drop_controller(self, controller) -> None:
        """Delegate drag hover preview and non-wheel drops to MainWindow's controller."""
        self._drop_controller = controller

    @staticmethod
    def _wheel_payload(mime):
        """The wheel panel id carried by a drag, or None."""
        for fmt in (WHEEL_GRIP_MIME, PANEL_MIME):
            if not mime.hasFormat(fmt):
                continue
            try:
                panel_id = bytes(mime.data(fmt)).decode("utf-8")
            except (TypeError, ValueError, UnicodeDecodeError):
                continue
            if panel_id.startswith("wheel."):
                return panel_id
        return None

    def dragEnterEvent(self, event):
        ctrl = getattr(self, "_drop_controller", None)
        if ctrl is not None:
            pid = ctrl._dragged_panel_from_mime(event.mimeData())
            if pid:
                gp = self.mapToGlobal(event.position().toPoint())
                if ctrl.show_drop_hint(gp, (pid,), from_floating=False) is not None:
                    event.acceptProposedAction()
                    return
                event.ignore()
                return
        if self._wheel_payload(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        ctrl = getattr(self, "_drop_controller", None)
        if ctrl is not None:
            pid = ctrl._dragged_panel_from_mime(event.mimeData())
            if pid:
                gp = self.mapToGlobal(event.position().toPoint())
                if ctrl.show_drop_hint(gp, (pid,), from_floating=False) is not None:
                    event.acceptProposedAction()
                    return
                event.ignore()
                return
        if self._wheel_payload(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        ctrl = getattr(self, "_drop_controller", None)
        if ctrl is not None:
            ctrl.hide_drop_hint(immediate=False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        ctrl = getattr(self, "_drop_controller", None)
        if ctrl is not None:
            ctrl.hide_drop_hint(immediate=True)
        panel_id = self._wheel_payload(event.mimeData())
        if panel_id is not None:
            index = self.tabAt(event.position().toPoint())
            if index < 0:
                index = max(0, self.count() - 1)
            self.wheelGripDropped.emit(panel_id, index)
            event.acceptProposedAction()
            return
        if ctrl is not None:
            any_pid = ctrl._dragged_panel_from_mime(event.mimeData())
            if any_pid:
                gp = self.mapToGlobal(event.position().toPoint())
                hit = ctrl.drop_target_at(gp, (any_pid,), from_floating=False)
                if hit is not None:
                    zone, _ = hit
                    event.acceptProposedAction()
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, lambda pid=any_pid, z=zone: ctrl.apply_panel_drop((pid,), z))
                    return
        super().dropEvent(event)

    def minimumTabSizeHint(self, index: int) -> QSize:
        """Never compress a label below its preferred width.

        The styled first tab otherwise lost a few pixels to the margin and
        "LAB" was painted as "L..." even though the strip had room to spare.
        """
        hint = super().minimumTabSizeHint(index)
        preferred = self.tabSizeHint(index)
        return QSize(max(int(hint.width()), int(preferred.width())),
                     max(int(hint.height()), int(preferred.height())))

    def set_tabs(self, items: list[tuple[str, str]], active_key: str) -> None:
        """Populate tabs with (key, label) pairs and select active_key."""
        current_items = [
            (str(self.tabData(i)), self.tabText(i))
            for i in range(self.count())
        ]
        self.blockSignals(True)
        try:
            if current_items != items:
                while self.count():
                    self.removeTab(0)
                for key, label in items:
                    idx = self.addTab(label)
                    self.setTabData(idx, key)
                    self.setTabToolTip(idx, label)
            target_idx = 0
            for i in range(self.count()):
                if str(self.tabData(i)) == str(active_key):
                    target_idx = i
                    break
            if 0 <= target_idx < self.count():
                self.setCurrentIndex(target_idx)
        finally:
            self.blockSignals(False)

    def tab_keys(self) -> list[str]:
        """Return the ordered list of tab keys currently in the bar."""
        return [
            str(self.tabData(i))
            for i in range(self.count())
            if self.tabData(i) is not None
        ]

    def current_key(self) -> str | None:
        """Return the key of the currently selected tab, or None."""
        idx = self.currentIndex()
        if 0 <= idx < self.count() and self.tabData(idx) is not None:
            return str(self.tabData(idx))
        return None

    def advance_tab(self) -> bool:
        """Select the next tab (wrapping past the end). Returns True if moved."""
        count = self.count()
        current = self.currentIndex()
        if count < 2 or current < 0:
            return False
        self.setCurrentIndex((current + 1) % count)
        return True

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta != 0 and self.count() >= 2:
            step = -1 if delta > 0 else 1
            self.setCurrentIndex((self.currentIndex() + step) % self.count())
            event.accept()
            return
        super().wheelEvent(event)

    def apply_chrome(self, chrome) -> None:
        """Apply PanelChrome styling matching PanelHost._style_tabs."""
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
        css = (
            "QTabBar#PickerTabBar { background: transparent;"
            f" font-size: {font}px;"
            " }"
            "QTabBar#PickerTabBar::tab {"
            f" background: {bar_bg}; color: {bar_text};"
            f" padding: {pad_y}px {pad_x}px;"
            f" font-size: {font}px; border: none;"
            " }"
            "QTabBar#PickerTabBar::tab:selected {"
            f" background: {bg}; color: {text};"
            f" border-top: {divider_w}px solid {divider};"
            " }"
            f"QTabBar#PickerTabBar::tab:hover:!selected {{ color: {text}; }}"
        )
        if self._panel_css == css:
            return
        self._panel_css = css
        self.setStyleSheet(css)
        self.updateGeometry()
