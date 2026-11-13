"""Smooth, non-activating transitions for floating panels and dock hints.

All helpers degrade cleanly to immediate updates when running under Qt's
``offscreen`` platform (unless a test explicitly opts in), and every running
animation cancels cleanly when its target widget is hidden, dragged, or
destroyed so DWM never sees stale geometry or alpha state on Windows.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import weakref

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QVariantAnimation,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

_ANIMATIONS_OVERRIDE: bool | None = None
_ACTIVE_GHOSTS: list[QWidget] = []

ZONE_LABELS: dict[str, str] = {
    "center": "堆叠",
    "merge_page": "合并标签页",
    "top": "放在上方",
    "bottom": "放在下方",
    "left": "放在左侧",
    "right": "放在右侧",
}


def animations_enabled() -> bool:
    """Return True when desktop visual transitions should animate."""
    if _ANIMATIONS_OVERRIDE is not None:
        return bool(_ANIMATIONS_OVERRIDE)
    return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"


def set_animations_enabled_for_test(enabled: bool | None) -> None:
    """Override animation enablement for deterministic tests (None restores default)."""
    global _ANIMATIONS_OVERRIDE
    _ANIMATIONS_OVERRIDE = enabled


@contextmanager
def snap_widget_update(widget: QWidget | None):
    """Batch child reparenting/layout swaps to avoid intermediate frame flicker."""
    if widget is None:
        yield
        return
    was_enabled = widget.updatesEnabled()
    widget.setUpdatesEnabled(False)
    try:
        yield
    finally:
        widget.setUpdatesEnabled(was_enabled)
        if was_enabled and widget.isVisible():
            widget.update()


def _stop_attr_anim(widget: QWidget, attr: str) -> None:
    anim = getattr(widget, attr, None)
    if anim is not None:
        try:
            anim.stop()
            anim.deleteLater()
        except RuntimeError:
            pass
        setattr(widget, attr, None)


def cancel_window_animations(widget: QWidget | None, *, restore_opacity: float | None = 1.0) -> None:
    """Stop active reveal/geometry animations on ``widget`` safely."""
    if widget is None:
        return
    _stop_attr_anim(widget, "_reveal_opacity_anim")
    _stop_attr_anim(widget, "_geom_transition_anim")
    if restore_opacity is not None:
        try:
            widget.setWindowOpacity(float(restore_opacity))
        except RuntimeError:
            pass


def animate_window_opacity(
    widget: QWidget,
    target_opacity: float,
    *,
    duration_ms: int = 120,
    attr: str = "_reveal_opacity_anim",
) -> None:
    """Ease ``widget.windowOpacity()`` toward ``target_opacity``."""
    _stop_attr_anim(widget, attr)
    target = max(0.0, min(1.0, float(target_opacity)))
    if not animations_enabled() or duration_ms <= 0:
        widget.setWindowOpacity(target)
        return
    start = float(widget.windowOpacity())
    if abs(start - target) < 0.01:
        widget.setWindowOpacity(target)
        return

    anim = QVariantAnimation(widget)
    anim.setDuration(int(duration_ms))
    anim.setStartValue(start)
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    ref = weakref.ref(widget)

    def _on_val(value) -> None:
        w = ref()
        if w is None:
            return
        try:
            w.setWindowOpacity(float(value))
        except RuntimeError:
            pass

    def _on_done() -> None:
        w = ref()
        if w is not None and getattr(w, attr, None) is anim:
            setattr(w, attr, None)
        anim.deleteLater()

    anim.valueChanged.connect(_on_val)
    anim.finished.connect(_on_done)
    setattr(widget, attr, anim)
    anim.start()


def reveal_floating_window(
    window: QWidget,
    show_fn,
    *,
    target_opacity: float = 1.0,
    duration_ms: int = 155,
) -> None:
    """Reveal a prepared window once at its settled rect with a flicker-free fade-in.

    Setting ``windowOpacity`` to ``0.0`` *before* showing ensures DWM allocates the
    translucent layered surface and applies ``WS_EX_NOACTIVATE`` before any visible
    pixels hit the screen.
    """
    _stop_attr_anim(window, "_reveal_opacity_anim")
    target = max(0.0, min(1.0, float(target_opacity)))
    if not animations_enabled() or duration_ms <= 0:
        window.setWindowOpacity(target)
        show_fn()
        return

    window.setWindowOpacity(0.0)
    show_fn()
    animate_window_opacity(
        window, target, duration_ms=duration_ms, attr="_reveal_opacity_anim"
    )


def animate_window_geometry(
    widget: QWidget,
    target_rect: QRect,
    *,
    duration_ms: int = 135,
) -> None:
    """Smoothly interpolate an already-visible window to ``target_rect``."""
    _stop_attr_anim(widget, "_geom_transition_anim")
    end_rect = QRect(target_rect)
    start_rect = QRect(widget.geometry())
    if (
        not animations_enabled()
        or duration_ms <= 0
        or not widget.isVisible()
        or start_rect == end_rect
    ):
        widget.setGeometry(end_rect)
        return

    anim = QVariantAnimation(widget)
    anim.setDuration(int(duration_ms))
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    ref = weakref.ref(widget)

    def _lerp(a: int, b: int, t: float) -> int:
        return int(round(a + (b - a) * t))

    def _on_val(val) -> None:
        w = ref()
        if w is None or not w.isVisible():
            return
        t = float(val)
        rect = QRect(
            _lerp(start_rect.x(), end_rect.x(), t),
            _lerp(start_rect.y(), end_rect.y(), t),
            _lerp(start_rect.width(), end_rect.width(), t),
            _lerp(start_rect.height(), end_rect.height(), t),
        )
        try:
            w.setGeometry(rect)
        except RuntimeError:
            pass

    def _on_done() -> None:
        w = ref()
        if w is not None and getattr(w, "_geom_transition_anim", None) is anim:
            setattr(w, "_geom_transition_anim", None)
        anim.deleteLater()

    anim.valueChanged.connect(_on_val)
    anim.finished.connect(_on_done)
    setattr(widget, "_geom_transition_anim", anim)
    anim.start()


class _DockTransitionGhost(QWidget):
    """Non-activating translucent snapshot that glides toward a dock target."""

    def __init__(self, pixmap: QPixmap, start_rect: QRect, target_rect: QRect, no_focus: bool):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pixmap = pixmap
        self._start_rect = QRect(start_rect)
        self._end_rect = QRect(target_rect) if target_rect.isValid() else QRect(start_rect)
        self._no_focus = bool(no_focus)
        self.setGeometry(self._start_rect)

    def paintEvent(self, _event) -> None:
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pixmap)

    def play(self, duration_ms: int = 165) -> None:
        _ACTIVE_GHOSTS.append(self)
        self.setWindowOpacity(0.88)
        self.show()
        if self._no_focus:
            try:
                from ui.panels.floating import apply_no_activate
                apply_no_activate(self, True)
            except Exception:
                pass

        anim = QVariantAnimation(self)
        anim.setDuration(int(duration_ms))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        ref = weakref.ref(self)

        def _lerp(a: int, b: int, t: float) -> int:
            return int(round(a + (b - a) * t))

        # Glide ~75% toward the center of the dock slot while shrinking gently
        # rather than collapsing all the way to zero width/height.
        s = self._start_rect
        e = self._end_rect
        end_w = max(48, int(s.width() * 0.55 + e.width() * 0.45))
        end_h = max(32, int(s.height() * 0.55 + e.height() * 0.45))
        ec = e.center()
        target_end = QRect(ec.x() - end_w // 2, ec.y() - end_h // 2, end_w, end_h)

        def _on_val(val) -> None:
            w = ref()
            if w is None:
                return
            t = float(val)
            try:
                w.setWindowOpacity(max(0.0, 0.88 * (1.0 - t)))
                w.setGeometry(QRect(
                    _lerp(s.x(), target_end.x(), t),
                    _lerp(s.y(), target_end.y(), t),
                    _lerp(s.width(), target_end.width(), t),
                    _lerp(s.height(), target_end.height(), t),
                ))
            except RuntimeError:
                pass

        def _cleanup() -> None:
            w = ref()
            if w is not None:
                try:
                    _ACTIVE_GHOSTS.remove(w)
                except ValueError:
                    pass
                w.hide()
                w.deleteLater()

        anim.valueChanged.connect(_on_val)
        anim.finished.connect(_cleanup)
        self._anim = anim
        anim.start()


def capture_window_snapshot(window: QWidget | None) -> tuple[QPixmap, QRect] | None:
    """Capture a live floating window's pixmap and global rect before docking."""
    if window is None or not animations_enabled():
        return None
    try:
        if not window.isVisible() or window.width() <= 0 or window.height() <= 0:
            return None
        pixmap = window.grab()
        if pixmap.isNull():
            return None
        return (pixmap, QRect(window.geometry()))
    except RuntimeError:
        return None


def spawn_dock_ghost(
    snapshot: tuple[QPixmap, QRect] | None,
    target_global_rect: QRect,
    *,
    no_focus: bool = True,
    duration_ms: int = 165,
) -> None:
    """Play a brief glide+fade ghost after a floating window docks/merges."""
    if snapshot is None or not animations_enabled():
        return
    pixmap, start_rect = snapshot
    if pixmap.isNull() or not start_rect.isValid():
        return
    ghost = _DockTransitionGhost(pixmap, start_rect, target_global_rect, no_focus)
    ghost.play(duration_ms=duration_ms)


def zone_label(zone: str) -> str:
    """Return a concise localized hint label for a drop zone."""
    return ZONE_LABELS.get(str(zone), "")


def capture_widget_snapshot(widget: QWidget | None) -> tuple[QPixmap, QRect] | None:
    """Capture a live child widget's pixmap and global rect before layout rebuild."""
    if widget is None or not animations_enabled():
        return None
    try:
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return None
        pixmap = widget.grab()
        if pixmap.isNull():
            return None
        global_rect = QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())
        return (pixmap, global_rect)
    except RuntimeError:
        return None


class _InWindowGlideOverlay(QWidget):
    """Child-widget snapshot glide inside a host window (no extra top-level HWND)."""

    def __init__(
        self,
        host_window: QWidget,
        pixmap: QPixmap,
        start_rect: QRect,
        end_rect: QRect,
    ):
        super().__init__(host_window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pixmap = pixmap
        self._start_rect = QRect(start_rect)
        self._end_rect = QRect(end_rect)
        self._progress = 0.0
        self._anim: QVariantAnimation | None = None
        self.setGeometry(self._start_rect)

    def paintEvent(self, _event) -> None:
        if self._pixmap.isNull():
            return
        alpha = max(0.0, 0.72 * (1.0 - self._progress))
        if alpha <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(alpha)
        painter.drawPixmap(self.rect(), self._pixmap)
        pen = QPen(QColor(77, 156, 255, int(round(190 * (1.0 - self._progress)))), 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)

    def stop(self) -> None:
        anim = getattr(self, "_anim", None)
        if anim is not None:
            anim.stop()
            anim.deleteLater()
            self._anim = None
        self.hide()
        self.deleteLater()

    def play(self, duration_ms: int = 160) -> None:
        self.show()
        self.raise_()
        anim = QVariantAnimation(self)
        anim.setDuration(max(60, int(duration_ms)))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        ref = weakref.ref(self)

        def _lerp(a: int, b: int, t: float) -> int:
            return int(round(a + (b - a) * t))

        s = self._start_rect
        e = self._end_rect

        def _on_val(val) -> None:
            w = ref()
            if w is None:
                return
            t = float(val)
            w._progress = t
            try:
                w.setGeometry(QRect(
                    _lerp(s.x(), e.x(), t),
                    _lerp(s.y(), e.y(), t),
                    max(1, _lerp(s.width(), e.width(), t)),
                    max(1, _lerp(s.height(), e.height(), t)),
                ))
                w.update()
            except RuntimeError:
                pass

        def _on_done() -> None:
            w = ref()
            if w is not None:
                parent = w.parentWidget()
                if parent is not None and getattr(parent, "_glide_overlay", None) is w:
                    setattr(parent, "_glide_overlay", None)
                w._anim = None
                w.hide()
                w.deleteLater()

        anim.valueChanged.connect(_on_val)
        anim.finished.connect(_on_done)
        self._anim = anim
        anim.start()


class _PanelLandingOverlay(QWidget):
    """Non-activating child highlight overlay marking where a panel settled."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._target_ref = weakref.ref(target)
        self._progress = 0.0
        self._anim: QVariantAnimation | None = None
        target.installEventFilter(self)
        self.setGeometry(target.rect())

    def eventFilter(self, obj, event):
        target = self._target_ref()
        if obj is target and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            try:
                self.setGeometry(target.rect())
                self.raise_()
            except RuntimeError:
                pass
        elif obj is target and event.type() == QEvent.Type.Hide:
            self.stop()
        return super().eventFilter(obj, event)

    def paintEvent(self, _event) -> None:
        t = self._progress
        fade = max(0.0, 1.0 - t)
        if fade <= 0.01 or self.width() <= 2 or self.height() <= 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inset = 1 + int(round(fade * 3))
        box = self.rect().adjusted(inset, inset, -inset - 1, -inset - 1)
        if box.width() <= 2 or box.height() <= 2:
            box = self.rect().adjusted(1, 1, -2, -2)
        fill = QColor(77, 156, 255, int(round(38 * (fade ** 1.3))))
        border = QColor(77, 156, 255, int(round(230 * fade)))
        pen = QPen(border, max(1.2, 2.2 * fade + 0.8))
        painter.setBrush(fill)
        painter.setPen(pen)
        painter.drawRoundedRect(box, 5, 5)
        bar_alpha = int(round(220 * fade))
        if bar_alpha > 4:
            accent = QColor(145, 205, 255, bar_alpha)
            bar_w = max(8, int(round(box.width() * (0.45 + 0.55 * t))))
            bar_x = box.center().x() - bar_w // 2
            painter.fillRect(QRect(bar_x, box.top() + 1, bar_w, 2), accent)

    def stop(self) -> None:
        target = self._target_ref()
        if target is not None:
            try:
                target.removeEventFilter(self)
                if getattr(target, "_landing_overlay", None) is self:
                    setattr(target, "_landing_overlay", None)
            except RuntimeError:
                pass
        anim = getattr(self, "_anim", None)
        if anim is not None:
            anim.stop()
            anim.deleteLater()
            self._anim = None
        self.hide()
        self.deleteLater()

    def play(self, duration_ms: int = 220) -> None:
        self.show()
        self.raise_()
        anim = QVariantAnimation(self)
        anim.setDuration(max(80, int(duration_ms)))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        ref = weakref.ref(self)

        def _on_val(val) -> None:
            ov = ref()
            if ov is None:
                return
            ov._progress = float(val)
            try:
                ov.update()
            except RuntimeError:
                pass

        def _on_done() -> None:
            ov = ref()
            if ov is not None:
                ov.stop()

        anim.valueChanged.connect(_on_val)
        anim.finished.connect(_on_done)
        self._anim = anim
        anim.start()


def animate_panel_landing(
    target_widget: QWidget | None,
    *,
    start_snapshot: tuple[QPixmap, QRect] | None = None,
    duration_ms: int = 220,
) -> bool:
    """Play a soft non-activating settle highlight (and optional glide) on drop."""
    if target_widget is None or not animations_enabled() or duration_ms <= 0:
        return False
    try:
        if (
            not target_widget.isVisible()
            or target_widget.width() <= 0
            or target_widget.height() <= 0
        ):
            return False
        prev = getattr(target_widget, "_landing_overlay", None)
        if prev is not None:
            try:
                prev.stop()
            except RuntimeError:
                pass
        overlay = _PanelLandingOverlay(target_widget)
        setattr(target_widget, "_landing_overlay", overlay)
        overlay.play(duration_ms=duration_ms)

        if start_snapshot is not None:
            pixmap, start_global = start_snapshot
            top_win = target_widget.window()
            if (
                not pixmap.isNull()
                and start_global.isValid()
                and top_win is not None
                and top_win.isVisible()
            ):
                start_local = QRect(
                    top_win.mapFromGlobal(start_global.topLeft()),
                    start_global.size(),
                )
                end_global = QRect(
                    target_widget.mapToGlobal(QPoint(0, 0)),
                    target_widget.size(),
                )
                end_local = QRect(
                    top_win.mapFromGlobal(end_global.topLeft()),
                    end_global.size(),
                )
                travel = (
                    abs(start_local.x() - end_local.x())
                    + abs(start_local.y() - end_local.y())
                )
                bounds = top_win.rect().adjusted(-140, -140, 140, 140)
                if travel > 4 and bounds.intersects(start_local):
                    prev_glide = getattr(top_win, "_glide_overlay", None)
                    if prev_glide is not None:
                        try:
                            prev_glide.stop()
                        except RuntimeError:
                            pass
                    glide = _InWindowGlideOverlay(
                        top_win, pixmap, start_local, end_local
                    )
                    setattr(top_win, "_glide_overlay", glide)
                    glide.play(duration_ms=min(170, max(90, int(duration_ms * 0.78))))
        return True
    except RuntimeError:
        return False
