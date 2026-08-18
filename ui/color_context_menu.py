"""Right-click color menu on the preview swatches.

The menu copies the swatch colour in several formats. RGB / HEX work from
the raw QColor; HSL / OKLCh / LAB are rendered from the unified Color model
so source-space precision survives (no RGB round-trip drift).

Kept in its own module (not ui/color_preview_box.py) because the preview
box is at its pure-LOC ceiling; this also makes the menu unit-testable
without rendering the swatch widget.
"""

from PyQt6.QtWidgets import QApplication, QMenu

from core import i18n
from ui.color_model import Color


def format_css_values(color: Color) -> dict[str, str]:
    """Return CSS strings for the precision formats of *color*."""
    h, l, s = color.hls  # Color.hls is (h, l, s); CSS hsl() is (h, s, l)
    L, C, h_oklch = color.oklch
    L_lab, a_lab, b_lab = color.lab
    return {
        "HSL": f"hsl({h:.1f}, {s:.1f}%, {l:.1f}%)",
        "OKLCh": f"oklch({L:.3f} {C:.4f} {h_oklch:.1f})",
        "LAB": f"lab({L_lab:.2f}% {a_lab:.2f} {b_lab:.2f})",
    }


def _copy_text(text: str):
    """Return a zero-arg closure that puts *text* on the clipboard."""
    def _do_copy():
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)
    return _do_copy


def _resolve_color(owner, color) -> Color | None:
    """Best-effort precise Color for the clicked swatch.

    Prefers the active slot's current Color; otherwise rebuilds from the
    slot's remembered source coordinates. Returns None when no state is
    available (menu then shows RGB / HEX only).
    """
    parent = getattr(owner, "_parent", None)
    if parent is None:
        return None
    slot = None
    if getattr(owner, "fg_color", None) is color:
        slot = "fg"
    elif getattr(owner, "bg_color", None) is color:
        slot = "bg"
    if slot is None:
        return None

    cs = getattr(parent, "color_state", None)
    if cs is not None and getattr(parent, "active_slot", None) == slot:
        current = getattr(cs, "current", None)
        if current is not None:
            return current

    space = getattr(parent, f"_{slot}_source_space", None)
    values = getattr(parent, f"_{slot}_source_values", None)
    if space and values:
        try:
            return Color.from_space(space, values)
        except Exception:
            return None
    return None


def build_color_menu(owner, color) -> QMenu:
    """Build the right-click copy menu for a swatch colour.

    ``owner`` is the widget that owns the menu (used as parent and to reach
    the main window for the precise Color state). Caller execs the menu.
    """
    menu = QMenu(owner)
    r, g, b = color.red(), color.green(), color.blue()

    menu.addAction(
        f"{i18n.tr('复制 RGB')}: rgb({r}, {g}, {b})",
        _copy_text(f"rgb({r}, {g}, {b})"),
    )
    menu.addAction(
        f"{i18n.tr('复制 HEX')}: #{r:02X}{g:02X}{b:02X}",
        _copy_text(f"#{r:02X}{g:02X}{b:02X}"),
    )

    color_obj = _resolve_color(owner, color)
    if color_obj is not None:
        for label, value in format_css_values(color_obj).items():
            menu.addAction(
                f"{i18n.tr(f'复制 {label}')}: {value}",
                _copy_text(value),
            )
    return menu
