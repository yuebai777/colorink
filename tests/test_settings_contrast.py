"""Settings legibility: each surface gets an ink chosen against itself.

The settings panel is filled with the theme's *bar* colour while its inputs
are filled with the *body* colour. The text colour used to be derived from
the body colour alone, so an eyedropper theme with a dark frame colour and a
light background colour rendered the whole settings window dark-on-dark.
"""

import os
import re

import pytest

from core import config
from ui.theme_contrast import DARK_INK, LIGHT_INK, muted_ink, readable_ink


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── The rule itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("surface", "expected"), [
    ("#ffffff", DARK_INK),
    ("#f0f0f0", DARK_INK),
    ("#b2b2b2", DARK_INK),   # the default gray body
    ("#787878", LIGHT_INK),  # the default gray frame / panel
    ("#3a3a3a", LIGHT_INK),
    ("#1e1e1e", LIGHT_INK),
    ("#fff", DARK_INK),      # short hex
])
def test_ink_is_chosen_against_the_surface(surface, expected):
    assert readable_ink(surface) == expected


def test_unparseable_surfaces_keep_dark_ink():
    """Guessing white on an unknown surface risks white-on-white."""
    assert readable_ink("papayawhip") == DARK_INK
    assert readable_ink("") == DARK_INK


def test_muted_ink_fades_the_ink_it_is_given():
    assert muted_ink("#ffffff") == "rgba(255,255,255,0.45)"
    assert muted_ink("#222222", 0.3) == "rgba(34,34,34,0.30)"
    assert muted_ink("nonsense") == "rgba(34,34,34,0.45)"  # falls back to dark ink


# ── The settings panel ────────────────────────────────────────────────────


@pytest.fixture
def sidebar(qapp, monkeypatch):
    """A real settings sidebar whose config load/save stay in memory."""
    from PyQt6.QtWidgets import QWidget

    saved: dict = {}
    monkeypatch.setattr(config, "save_hotkey_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr(
        config, "load_hotkey_config",
        lambda: {**config.default_hotkey_config(), **saved},
    )

    class _StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config.load_hotkey_config()
            self.sync_thread = type("_T", (), {
                "companion_sync": type("_C", (), {"_has_session": lambda self: False})(),
            })()

        def apply_theme(self, scale=None, is_resize_event=False):
            pass

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *args, **kwargs):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    from ui.settings_sidebar import SettingsSidebar

    parent = _StubMainWindow()
    panel = SettingsSidebar(parent)
    panel.setVisible(False)
    parent.settings_sidebar = panel
    return panel


def _eyedropper(sidebar, bar: str, body: str):
    """Point the theme at an independently picked frame / background pair."""
    for cfg in (sidebar._parent.cfg, sidebar.cfg):
        cfg["ui-theme"] = "eyedropper"
        cfg["uiThemeDropperColorBar"] = bar
        cfg["uiThemeDropperColorBg"] = body
    sidebar.apply_theme()
    return sidebar.theme_colors()


def _rule(css: str, selector: str) -> str:
    """Return the declaration block of the `selector { ... }` rule.

    Anchored at the start of a line so that looking up "QWidget" cannot
    land inside a descendant selector such as "QScrollArea > QWidget".
    """
    match = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, f"no {selector} rule in the stylesheet"
    return match.group(1)


def test_dark_panel_beside_a_light_body_inks_each_surface_separately(sidebar):
    """The regression: dark frame + light background used to make the whole
    settings window unreadable."""
    c = _eyedropper(sidebar, bar="#3a3a3a", body="#f0f0f0")

    assert c["bar_bg"] == "#3a3a3a"
    assert c["bar_text"] == LIGHT_INK   # panel ink follows the panel
    assert c["text"] == DARK_INK        # input ink follows the inputs


def test_light_panel_beside_a_dark_body_flips_both_inks(sidebar):
    c = _eyedropper(sidebar, bar="#eeeeee", body="#202020")

    assert c["bar_text"] == DARK_INK
    assert c["text"] == LIGHT_INK


def test_saturated_picks_use_perceptual_luma_not_hsl_lightness(sidebar):
    """Pure yellow sits at HSL lightness 127 — just dark enough for the old
    rule to ask for white text on a colour the eye reads as bright."""
    c = _eyedropper(sidebar, bar="#ffff00", body="#ffff00")

    assert (c["text"], c["bar_text"]) == (DARK_INK, DARK_INK)


def test_panel_widgets_are_painted_with_the_panel_ink(sidebar):
    _eyedropper(sidebar, bar="#3a3a3a", body="#f0f0f0")
    css = sidebar.styleSheet()

    for selector in ("QWidget", "QLabel", "QLabel#SectionHeader", "QCheckBox"):
        assert LIGHT_INK in _rule(css, selector), selector


def test_input_widgets_keep_the_body_ink(sidebar):
    """Combo boxes and buttons are filled with the body colour, so they must
    not follow the panel ink."""
    _eyedropper(sidebar, bar="#3a3a3a", body="#f0f0f0")
    css = sidebar.styleSheet()

    for selector in ("QComboBox", "QPushButton"):
        rule = _rule(css, selector)
        assert "#f0f0f0" in rule          # filled with the body colour
        assert f"color: {DARK_INK}" in rule


def test_status_and_rail_text_follow_the_panel_too(sidebar):
    _eyedropper(sidebar, bar="#3a3a3a", body="#f0f0f0")
    css = sidebar.styleSheet()
    panel_muted = muted_ink(LIGHT_INK)

    assert panel_muted in _rule(css, "QLabel#StatusHint")
    assert panel_muted in _rule(css, "QListWidget#NavRail::item")


def test_fixed_gray_theme_reads_white_on_its_panel(sidebar):
    """The default gray theme paints a #787878 panel — dark ink on it was
    always the low-contrast end."""
    for cfg in (sidebar._parent.cfg, sidebar.cfg):
        cfg["ui-theme"] = "gray"
    sidebar.apply_theme()
    c = sidebar.theme_colors()

    assert (c["bar_bg"], c["bar_text"]) == ("#787878", LIGHT_INK)
    assert c["text"] == DARK_INK  # body stays #b2b2b2 with dark ink


# ── The settings window around it ─────────────────────────────────────────


def test_settings_window_chrome_uses_the_panel_ink(sidebar, qapp):
    from ui.settings_window import SettingsWindow

    _eyedropper(sidebar, bar="#3a3a3a", body="#f0f0f0")
    window = SettingsWindow(sidebar._parent, sidebar)
    try:
        window._apply_window_theme()

        assert "background-color: #3a3a3a" in window.styleSheet()
        assert f"color: {LIGHT_INK}" in _rule(window.styleSheet(), "QWidget")
        assert LIGHT_INK in window._title_bar.styleSheet()
    finally:
        window.hide()
        window.deleteLater()
