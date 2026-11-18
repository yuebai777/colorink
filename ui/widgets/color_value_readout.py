"""Compact inline numeric color-value readout for the wheel and LAB panes.

Displays the current color in the active numeric format (RGB with solid color
chips by default, or HSV / HLS / LAB / OKLab / OKLCh / HEX with crisp letter
badges). Left-clicking cycles through formats; right-clicking lets the user
pick any format directly or copy the formatted string without stealing focus.
"""

from __future__ import annotations

from typing import NamedTuple

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from core import i18n
from ui.color_model import Color
from ui.theme_contrast import blend_colors, is_light_color, readable_ink

READOUT_MODES: tuple[str, ...] = (
    "rgb",
    "hsv",
    "hls",
    "lab",
    "oklab",
    "oklch",
    "hex",
)

READOUT_MODE_LABELS: dict[str, str] = {
    "rgb": "RGB",
    "hsv": "HSV",
    "hls": "HLS",
    "lab": "LAB",
    "oklab": "OKLab",
    "oklch": "OKLCh",
    "hex": "HEX",
}

_RGB_CHIPS_LIGHT = ("#C01818", "#009800", "#0A20D8")
_RGB_CHIPS_DARK = ("#DF4040", "#1FB430", "#3B66FF")


class ReadoutBadge(NamedTuple):
    """Single channel item in the numeric readout."""

    label: str
    chip_index: int
    value: str
    sample: str


class ReadoutSpec(NamedTuple):
    """Complete resolved readout specification for one color and mode."""

    mode: str
    prefix: str
    badges: tuple[ReadoutBadge, ...]
    copy_text: str


def _round_int(value: float) -> int:
    """Round cleanly to int while suppressing signed ``-0``."""
    rounded = int(round(value))
    return 0 if rounded == 0 else rounded


def _round_c(value: float) -> float:
    """Match OKLCh C slider resolution (``0.001`` steps)."""
    rounded = round(float(value) * 1000.0) / 1000.0
    return 0.0 if abs(rounded) < 1e-6 else rounded


def resolve_readout_mode(mode: str | None) -> str:
    """Return a validated readout mode identifier, defaulting to ``rgb``."""
    if isinstance(mode, str):
        norm = mode.strip().lower()
        if norm in READOUT_MODES:
            return norm
    return "rgb"


def next_readout_mode(mode: str | None) -> str:
    """Return the next numeric format after *mode* in cycle order."""
    cur = resolve_readout_mode(mode)
    idx = READOUT_MODES.index(cur)
    return READOUT_MODES[(idx + 1) % len(READOUT_MODES)]


def format_readout_spec(
    color: Color,
    mode: str,
    hsv_override: tuple[float, float, float] | None = None,
) -> ReadoutSpec:
    """Build badge items and copy string for *color* in *mode*.

    Rounding matches :meth:`ui.window.color_updates.ColorUpdatesMixin.update_ui_colors`
    so the readout never disagrees with the docked sliders.
    """
    m = resolve_readout_mode(mode)
    if m == "rgb":
        r, g, b = int(color.r), int(color.g), int(color.b)
        return ReadoutSpec(
            mode=m,
            prefix="",
            badges=(
                ReadoutBadge("", 0, str(r), "255"),
                ReadoutBadge("", 1, str(g), "255"),
                ReadoutBadge("", 2, str(b), "255"),
            ),
            copy_text=f"rgb({r}, {g}, {b})",
        )
    if m == "hsv":
        h, s, v = hsv_override if hsv_override is not None else color.hsv
        hi, si, vi = _round_int(h), _round_int(s), _round_int(v)
        return ReadoutSpec(
            mode=m,
            prefix="",
            badges=(
                ReadoutBadge("H", -1, str(hi), "360"),
                ReadoutBadge("S", -1, str(si), "100"),
                ReadoutBadge("V", -1, str(vi), "100"),
            ),
            copy_text=f"hsv({hi}, {si}, {vi})",
        )
    if m == "hls":
        h, l_val, s = color.hls
        hi, li, si = _round_int(h), _round_int(l_val), _round_int(s)
        return ReadoutSpec(
            mode=m,
            prefix="",
            badges=(
                ReadoutBadge("H", -1, str(hi), "360"),
                ReadoutBadge("L", -1, str(li), "100"),
                ReadoutBadge("S", -1, str(si), "100"),
            ),
            copy_text=f"hls({hi}, {li}, {si})",
        )
    if m == "lab":
        l_val, a_val, b_val = color.lab
        li, ai, bi = _round_int(l_val), _round_int(a_val), _round_int(b_val)
        return ReadoutSpec(
            mode=m,
            prefix="",
            badges=(
                ReadoutBadge("L", -1, str(li), "100"),
                ReadoutBadge("a", -1, str(ai), "-128"),
                ReadoutBadge("b", -1, str(bi), "-128"),
            ),
            copy_text=f"lab({li}, {ai}, {bi})",
        )
    if m == "oklab":
        l_ok, a_ok, b_ok = color.oklab
        li = _round_int(l_ok * 100.0)
        ai = _round_int(a_ok * 100.0)
        bi = _round_int(b_ok * 100.0)
        return ReadoutSpec(
            mode=m,
            prefix="OK",
            badges=(
                ReadoutBadge("L", -1, str(li), "100"),
                ReadoutBadge("a", -1, str(ai), "-40"),
                ReadoutBadge("b", -1, str(bi), "-40"),
            ),
            copy_text=f"oklab({li}, {ai}, {bi})",
        )
    if m == "oklch":
        l_ok, c_ok, h_ok = color.oklch
        li = _round_int(l_ok * 100.0)
        c_val = _round_c(c_ok)
        hi = _round_int(h_ok)
        return ReadoutSpec(
            mode=m,
            prefix="OK",
            badges=(
                ReadoutBadge("L", -1, str(li), "100"),
                ReadoutBadge("C", -1, f"{c_val:.3f}", "0.400"),
                ReadoutBadge("h", -1, str(hi), "360"),
            ),
            copy_text=f"oklch({li}, {c_val:.3f}, {hi})",
        )
    hex_val = f"#{int(color.r):02X}{int(color.g):02X}{int(color.b):02X}"
    return ReadoutSpec(
        mode="hex",
        prefix="",
        badges=(ReadoutBadge("#", -1, hex_val, "#FFFFFF"),),
        copy_text=hex_val,
    )


class ColorValueReadout(QWidget):
    """Clickable badge + numeric readout painted crisply at any DPI scale."""

    modeChanged = pyqtSignal(str)
    layoutMetricsChanged = pyqtSignal()

    def __init__(self, parent=None, *, pill_backdrop: bool = False):
        super().__init__(parent)
        self._color: Color = Color.from_rgb(0, 0, 0)
        self._hsv_override: tuple[float, float, float] | None = None
        self._mode: str = "rgb"
        self._scale: float = 1.0
        self._pill_backdrop: bool = bool(pill_backdrop)
        self._compact: bool = False
        self._hovered: bool = False
        self._pressed_left: bool = False

        # Theme palette (updated by ThemeMixin.apply_theme)
        self._bg_color = QColor("#f0f0f0")
        self._bar_bg_color = QColor("#e5e5e5")
        self._text_color = QColor("#222222")
        self._border_color = QColor("#c8c8c8")
        self._corner_radius = 3.5
        self._chip_radius = 1.5

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.retranslate()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def spec(self) -> ReadoutSpec:
        return format_readout_spec(self._color, self._mode, self._hsv_override)

    def retranslate(self) -> None:
        self.setToolTip(i18n.tr("左键点击切换格式 · 右键选择或复制"))

    def set_pill_backdrop(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._pill_backdrop != enabled:
            self._pill_backdrop = enabled
            self.update()

    def set_color(
        self,
        color: Color,
        hsv_override: tuple[float, float, float] | None = None,
    ) -> None:
        """Update the displayed color (without relayout unless size changes)."""
        old_size = self.sizeHint()
        self._color = color
        self._hsv_override = hsv_override
        new_size = self.sizeHint()
        if new_size != old_size:
            self.updateGeometry()
            self.layoutMetricsChanged.emit()
        self.update()

    def set_mode(self, mode: str, *, emit: bool = False) -> bool:
        """Switch displayed numeric format. Returns True if the mode changed."""
        resolved = resolve_readout_mode(mode)
        changed = resolved != self._mode
        if changed:
            self._mode = resolved
            self.updateGeometry()
            self.layoutMetricsChanged.emit()
            self.update()
            if emit:
                self.modeChanged.emit(self._mode)
        return changed

    def cycle_mode(self) -> str:
        """Advance to the next numeric format and emit ``modeChanged``."""
        self.set_mode(next_readout_mode(self._mode), emit=True)
        return self._mode

    def set_theme(
        self,
        *,
        bg: str,
        bar_bg: str,
        text: str,
        border: str,
        scale: float | None = None,
        corner_radius: float | None = None,
        chip_radius: float | None = None,
    ) -> None:
        """Apply theme palette and optional UI scale."""
        def _css_qcolor(val: str) -> QColor:
            c = QColor(val)
            if c.isValid():
                return c
            s = str(val or "").strip()
            if s.startswith(("rgba(", "rgb(")) and s.endswith(")"):
                parts = [p.strip() for p in s[s.index("(") + 1 : -1].split(",")]
                if len(parts) >= 3:
                    try:
                        return QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
                    except ValueError:
                        pass
            return QColor("#808080")

        self._bg_color = _css_qcolor(bg)
        self._bar_bg_color = _css_qcolor(bar_bg)
        self._text_color = _css_qcolor(text)
        self._border_color = _css_qcolor(border)
        if corner_radius is not None:
            self._corner_radius = max(0.0, float(corner_radius))
        if chip_radius is not None:
            self._chip_radius = max(0.0, float(chip_radius))
        if scale is not None:
            new_scale = max(0.5, float(scale))
            if abs(new_scale - self._scale) > 1e-4:
                self._scale = new_scale
                self.updateGeometry()
                self.layoutMetricsChanged.emit()
        self.update()

    # ── Metrics & geometry ──────────────────────────────────────────────
    def _layout_metrics(self, compact: bool | None = None):
        is_compact = self._compact if compact is None else compact
        s = self._scale
        if is_compact:
            badge_sz = max(6.5, round(7.0 * s))
            height = max(9, round(badge_sz + max(2.0, 2.0 * s)))
            pad_x = max(1.0, round(1.5 * s))
            inner_gap = max(1.0, round(1.5 * s))
            field_gap = max(2.0, round(2.5 * s))
            prefix_gap = max(1.5, round(2.0 * s))
            val_px = max(8, round(9.0 * s))
            badge_px = max(5, round(5.8 * s))
            prefix_px = max(6, round(6.5 * s))
        else:
            badge_sz = max(7.0, round(8.0 * s))
            height = max(10, round(badge_sz + max(2.0, 2.0 * s)))
            pad_x = max(1.5, round(2.5 * s))
            inner_gap = max(1.5, round(2.0 * s))
            field_gap = max(3.0, round(4.0 * s))
            prefix_gap = max(2.0, round(3.0 * s))
            val_px = max(9, round(10.0 * s))
            badge_px = max(6, round(6.4 * s))
            prefix_px = max(6, round(7.0 * s))

        val_font = QFont(self.font())
        val_font.setPixelSize(val_px)
        val_font.setBold(False)
        val_fm = QFontMetricsF(val_font)

        badge_font = QFont(self.font())
        badge_font.setPixelSize(badge_px)
        badge_font.setBold(True)
        badge_fm = QFontMetricsF(badge_font)

        prefix_font = QFont(self.font())
        prefix_font.setPixelSize(prefix_px)
        prefix_font.setBold(True)
        prefix_fm = QFontMetricsF(prefix_font)

        return (
            s, badge_sz, height, pad_x, inner_gap, field_gap, prefix_gap,
            val_font, val_fm, badge_font, badge_fm, prefix_font, prefix_fm,
        )

    def _slot_width(
        self, fm: QFontMetricsF, value: str, sample: str, *, compact: bool | None = None,
    ) -> float:
        is_compact = self._compact if compact is None else compact
        val_w = fm.horizontalAdvance(value)
        return val_w if is_compact else max(val_w, fm.horizontalAdvance(sample))

    def _measure_size(self, compact: bool) -> QSize:
        spec = self.spec
        (
            _s, badge_sz, height, pad_x, inner_gap, field_gap, prefix_gap,
            _val_font, val_fm, _badge_font, _badge_fm, _prefix_font, prefix_fm,
        ) = self._layout_metrics(compact=compact)
        w = pad_x * 2.0
        if spec.prefix:
            w += prefix_fm.horizontalAdvance(spec.prefix) + prefix_gap
        for idx, badge in enumerate(spec.badges):
            if idx > 0:
                w += field_gap
            w += badge_sz + inner_gap + self._slot_width(
                val_fm, badge.value, badge.sample, compact=compact,
            )
        return QSize(max(24, int(round(w + 1.0))), height)

    def fit_width(self, max_w: int) -> QSize | None:
        """Select relaxed or compact metrics to fit inside *max_w*, if possible."""
        full = self._measure_size(False)
        if full.width() <= max_w:
            if self._compact:
                self._compact = False
                self.update()
            return full
        tight = self._measure_size(True)
        if tight.width() <= max_w:
            if not self._compact:
                self._compact = True
                self.update()
            return tight
        return None

    def sizeHint(self) -> QSize:
        return self._measure_size(False)

    def minimumSizeHint(self) -> QSize:
        return self._measure_size(True)

    # ── Mouse & context menu ────────────────────────────────────────────
    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pressed_left = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_left = True
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._open_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_pressed = self._pressed_left
            self._pressed_left = False
            if was_pressed and self.rect().contains(event.position().toPoint()):
                self.cycle_mode()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._open_context_menu(event.globalPos())
        event.accept()

    def _open_context_menu(self, global_pos) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        menu.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        menu.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {self._bar_bg_color.name()};"
            f" border: 1px solid {self._border_color.name()};"
            f" color: {readable_ink(self._bar_bg_color.name())}; }}"
            "QMenu::item { padding: 4px 12px; }"
            "QMenu::item:selected { background-color: #5a94e2; color: #ffffff; }"
        )
        for m in READOUT_MODES:
            act = menu.addAction(READOUT_MODE_LABELS[m])
            act.setCheckable(True)
            act.setChecked(m == self._mode)
            act.triggered.connect(lambda _checked=False, target=m: self.set_mode(target, emit=True))
        menu.addSeparator()
        copy_str = self.spec.copy_text
        copy_act = menu.addAction(i18n.tr("复制 {value}", value=copy_str))
        copy_act.triggered.connect(lambda _checked=False, text=copy_str: self._copy_value(text))
        menu.exec(global_pos)

    @staticmethod
    def _copy_value(text: str) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    # ── Vector painting ─────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        spec = self.spec
        (
            s, badge_sz, _height, pad_x, inner_gap, field_gap, prefix_gap,
            val_font, val_fm, badge_font, badge_fm, prefix_font, prefix_fm,
        ) = self._layout_metrics()
        use_pill = self._pill_backdrop or self._hovered
        if use_pill:
            pill_alpha = 220 if self._pill_backdrop else 190
            surface_hex = blend_colors(
                self._bg_color.name(), self._bar_bg_color.name(), pill_alpha / 255.0
            )
            ink_color = QColor(readable_ink(surface_hex))
        else:
            surface_hex = self._bg_color.name()
            ink_color = self._text_color
        is_dark = not is_light_color(surface_hex)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            rect = QRectF(self.rect())
            if use_pill:
                fill = QColor(self._bar_bg_color)
                fill.setAlpha(220 if self._pill_backdrop else 190)
                border = QColor("#5a94e2" if self._hovered else self._border_color)
                if not self._hovered:
                    border.setAlpha(150)
                pill_r = self._corner_radius * s
                path = QPainterPath()
                path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), pill_r, pill_r)
                p.fillPath(path, fill)
                p.setPen(QPen(border, 1.0))
                p.drawPath(path)

            cy = rect.center().y()
            x = rect.left() + pad_x

            if spec.prefix:
                p.setFont(prefix_font)
                prefix_color = QColor(ink_color)
                prefix_color.setAlpha(175)
                p.setPen(prefix_color)
                pw = prefix_fm.horizontalAdvance(spec.prefix)
                tb = prefix_fm.tightBoundingRect(spec.prefix)
                p.drawText(
                    QPointF(x - tb.left(), cy - tb.center().y()),
                    spec.prefix,
                )
                x += pw + prefix_gap

            rgb_chips = _RGB_CHIPS_DARK if is_dark else _RGB_CHIPS_LIGHT
            badge_bg = QColor("#C6C6CC" if is_dark else "#3E3E42")
            badge_fg = QColor("#1E1E22" if is_dark else "#FFFFFF")
            chip_border = QColor(255, 255, 255, 42) if is_dark else QColor(0, 0, 0, 28)

            for idx, badge in enumerate(spec.badges):
                if idx > 0:
                    x += field_gap
                b_rect = QRectF(x, cy - badge_sz / 2.0, badge_sz, badge_sz)
                fill_color = QColor(rgb_chips[badge.chip_index]) if badge.chip_index >= 0 else badge_bg
                b_path = QPainterPath()
                radius = min(2.0, self._chip_radius * s)
                b_path.addRoundedRect(b_rect, radius, radius)
                p.fillPath(b_path, fill_color)
                if badge.chip_index >= 0:
                    p.setPen(QPen(chip_border, 0.8))
                    p.drawPath(b_path)
                if badge.label:
                    p.setFont(badge_font)
                    p.setPen(badge_fg)
                    tb = badge_fm.tightBoundingRect(badge.label)
                    p.drawText(
                        QPointF(b_rect.center().x() - tb.center().x(),
                                b_rect.center().y() - tb.center().y()),
                        badge.label,
                    )
                x += badge_sz + inner_gap

                p.setFont(val_font)
                p.setPen(ink_color)
                slot_w = self._slot_width(val_fm, badge.value, badge.sample)
                val_w = val_fm.horizontalAdvance(badge.value)
                tb = val_fm.tightBoundingRect("8")
                # Center digits vertically by numeric glyph height ("8") so '-'
                # on negative LAB/OKLab values does not shift the whole row.
                baseline_y = cy - tb.center().y()
                p.drawText(QPointF(x + (slot_w - val_w), baseline_y), badge.value)
                x += slot_w
        finally:
            p.end()


def place_readout_beside_buttons(
    readout: ColorValueReadout | None,
    *,
    enabled: bool,
    pane_w: int,
    pane_h: int,
    right_bound: int,
    left_bound: int,
    align_with_buttons: bool,
    margin: int,
    scale: float,
    pane_origin: QPoint | None = None,
    overhang_y: int = 0,
    use_pill_backdrop: bool | None = None,
) -> None:
    """Position *readout* inside (or just below) a legacy wheel/LAB pane."""
    if readout is None:
        return
    if not enabled or pane_w <= 0 or pane_h <= 0:
        readout.hide()
        return
    readout.set_pill_backdrop(
        align_with_buttons if use_pill_backdrop is None else bool(use_pill_backdrop)
    )
    avail = max(0, right_bound - left_bound)
    size = readout.fit_width(avail)
    if size is None:
        readout.hide()
        return
    rw, rh = size.width(), size.height()
    rx = right_bound - rw
    if align_with_buttons:
        ry = pane_h - margin - rh
    else:
        ry = pane_h - rh + max(0, int(overhang_y))
    ox = pane_origin.x() if pane_origin is not None else 0
    oy = pane_origin.y() if pane_origin is not None else 0
    readout.setGeometry(ox + rx, oy + max(0, ry), rw, rh)
    readout.show()
    readout.raise_()
