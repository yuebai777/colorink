"""Tests for the swatch right-click copy menu (ui/color_context_menu.py).

Verifies the CSS formatters keep source-space precision, the menu exposes
RGB / HEX always plus HSL / OKLCh / LAB when a Color state is available,
and triggering an action puts the right text on the clipboard.
"""

import os
from unittest.mock import patch

import pytest

from ui.color_context_menu import build_color_menu, format_css_values
from ui.color_model import Color


# ── Formatters ──────────────────────────────────────────────────────────


class TestFormatCssValues:
    def test_red_formats(self):
        values = format_css_values(Color.from_rgb(255, 0, 0))
        assert values["HSL"] == "hsl(0.0, 100.0%, 50.0%)"
        assert values["OKLCh"] == "oklch(0.628 0.2577 29.2)"
        assert values["LAB"] == "lab(54.29% 80.81 69.89)"

    def test_source_space_precision_survives(self):
        # OKLCh coords are stored as-edited, not recomputed from RGB.
        # Achromatic C=0 is always in gamut, so the stored value must pass
        # through verbatim (an RGB round-trip could not preserve it exactly).
        color = Color.from_space("oklch", (0.6, 0.0, 200.0))
        values = format_css_values(color)
        assert values["OKLCh"] == "oklch(0.600 0.0000 200.0)"


# ── Menu construction ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from core import i18n

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _preview_box_with_state(qapp, color_obj):
    """Preview box whose fg swatch carries a precise Color state."""
    from PyQt6.QtGui import QColor
    from ui.color_preview_box import ColorPreviewBox

    box = ColorPreviewBox()
    box.resize(120, 40)
    box.fg_color = QColor(*color_obj.rgb)
    box.bg_color = QColor(0, 0, 0)

    class FakeColorState:
        current = color_obj

    class FakeParent:
        active_slot = "fg"
        color_state = FakeColorState()
        _fg_source_space = color_obj.source_space
        _fg_source_values = color_obj.source_values
        _bg_source_space = "rgb"
        _bg_source_values = (0, 0, 0)

    box._parent = FakeParent()
    return box


class TestBuildColorMenu:
    def test_full_menu_with_color_state(self, qapp):
        box = _preview_box_with_state(qapp, Color.from_rgb(255, 0, 0))
        menu = build_color_menu(box, box.fg_color)
        labels = [a.text() for a in menu.actions()]
        assert len(labels) == 5
        assert any(l.startswith("复制 RGB: rgb(255, 0, 0)") for l in labels)
        assert any(l.startswith("复制 HEX: #FF0000") for l in labels)
        assert any(l.startswith("复制 HSL: hsl(0.0, 100.0%, 50.0%)") for l in labels)
        assert any(l.startswith("复制 OKLCh: oklch(") for l in labels)
        assert any(l.startswith("复制 LAB: lab(") for l in labels)

    def test_menu_without_color_state_only_rgb_hex(self, qapp):
        # No _parent at all → precise Color is unresolvable → RGB/HEX only.
        from PyQt6.QtGui import QColor
        from ui.color_preview_box import ColorPreviewBox

        box = ColorPreviewBox()
        box.fg_color = QColor(10, 20, 30)
        menu = build_color_menu(box, box.fg_color)
        labels = [a.text() for a in menu.actions()]
        assert len(labels) == 2
        assert "复制 HEX: #0A141E" in labels[1]

    def test_action_copies_to_clipboard(self, qapp):
        box = _preview_box_with_state(qapp, Color.from_rgb(10, 200, 30))
        menu = build_color_menu(box, box.fg_color)
        # Trigger every action; each one must put a matching string on the
        # clipboard (last one wins).
        for action in menu.actions():
            action.trigger()
        qapp.processEvents()
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        assert text.startswith("lab(")

    def test_hex_action_copies_exact_hex(self, qapp):
        box = _preview_box_with_state(qapp, Color.from_rgb(0, 0, 255))
        menu = build_color_menu(box, box.fg_color)
        # HEX action is the second one.
        hex_action = menu.actions()[1]
        hex_action.trigger()
        qapp.processEvents()
        from PyQt6.QtWidgets import QApplication
        assert QApplication.clipboard().text() == "#0000FF"


# ── Preview box delegation ──────────────────────────────────────────────


class TestPreviewBoxDelegation:
    def test_show_menu_delegates_to_builder(self, qapp):
        from PyQt6.QtGui import QColor
        from ui.color_preview_box import ColorPreviewBox

        box = ColorPreviewBox()
        box.fg_color = QColor(1, 2, 3)
        with patch("ui.color_context_menu.build_color_menu") as spy_build:
            with patch.object(box, "_show_color_context_menu", wraps=box._show_color_context_menu):
                box._show_color_context_menu(box.fg_color)
            spy_build.assert_called_once()
            assert spy_build.call_args[0][0] is box
            assert spy_build.call_args[0][1] is box.fg_color
