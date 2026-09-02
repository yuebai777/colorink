"""Background / frame opacity: colour maths, config plumbing and chrome CSS.

The main window is already created translucent (``WA_TranslucentBackground``),
so the opacity setting works purely by turning the *chrome* colours into
``rgba()`` ones. These tests pin both halves: the pure conversion rules in
``ui.chrome_opacity`` and what actually lands in the window stylesheet — plus
the guarantee that content colours (value boxes, swatches) stay opaque.
"""

import pytest

from core import config
from tests.test_border_themes import _make_theme_host
from tests.test_border_themes import qapp  # noqa: F401  (shared fixture)
from ui.chrome_opacity import (
    CHROME_OPACITY_DEFAULT,
    CHROME_OPACITY_KEY,
    CHROME_OPACITY_MAX,
    CHROME_OPACITY_MIN,
    clamp_chrome_opacity,
    resolve_chrome_opacity,
    with_opacity,
)


# ── Config plumbing ───────────────────────────────────────────────────────


def test_new_installs_are_fully_opaque():
    """The feature must be invisible until the user asks for it."""
    defaults = config.default_hotkey_config()

    assert defaults[CHROME_OPACITY_KEY] == CHROME_OPACITY_DEFAULT
    assert resolve_chrome_opacity(defaults) == CHROME_OPACITY_MAX


def test_existing_configs_are_back_filled_with_full_opacity():
    """A config saved before this feature existed must not turn see-through."""
    merged = config.merge_imported_config({"uiScale": 120})

    assert merged[CHROME_OPACITY_KEY] == CHROME_OPACITY_DEFAULT


def test_hand_edited_values_are_coerced_or_dropped():
    """String numbers survive as ints; junk is dropped so the merge refills."""
    assert config._sanitize_types({CHROME_OPACITY_KEY: "60"})[CHROME_OPACITY_KEY] == 60
    assert CHROME_OPACITY_KEY not in config._sanitize_types({CHROME_OPACITY_KEY: "opaque"})


@pytest.mark.parametrize(("raw", "expected"), [
    (100, 100),
    (55, 55),
    (0, CHROME_OPACITY_MIN),        # never let the window vanish completely
    (-40, CHROME_OPACITY_MIN),
    (500, CHROME_OPACITY_MAX),
    ("80", 80),
    (72.4, 72),
    (None, CHROME_OPACITY_DEFAULT),  # unreadable value → opaque, never invisible
    ("nonsense", CHROME_OPACITY_DEFAULT),
    (True, CHROME_OPACITY_DEFAULT),  # bool is an int subclass, not a percentage
])
def test_clamp_keeps_every_input_inside_the_usable_range(raw, expected):
    assert clamp_chrome_opacity(raw) == expected


def test_resolve_survives_a_config_without_the_key():
    assert resolve_chrome_opacity({}) == CHROME_OPACITY_DEFAULT


# ── Colour conversion ─────────────────────────────────────────────────────


def test_full_opacity_returns_the_colour_untouched():
    """100% must emit the exact legacy CSS, byte for byte."""
    assert with_opacity("#b2b2b2", 100) == "#b2b2b2"


def test_hex_colours_become_rgba():
    assert with_opacity("#b2b2b2", 50) == "rgba(178,178,178,0.500)"
    assert with_opacity("#abc", 40) == "rgba(170,187,204,0.400)"


def test_existing_alpha_is_multiplied_not_replaced():
    """An already-translucent colour may only get more transparent."""
    assert with_opacity("rgba(0,0,0,0.50)", 50) == "rgba(0,0,0,0.250)"
    assert with_opacity("rgb(10,20,30)", 50) == "rgba(10,20,30,0.500)"


@pytest.mark.parametrize("color", ["transparent", "none", "papayawhip", ""])
def test_unparseable_colours_pass_through_unchanged(color):
    """Falling back to an opaque colour beats emitting broken CSS."""
    assert with_opacity(color, 50) == color


# ── Chrome stylesheet ─────────────────────────────────────────────────────


def test_opaque_chrome_keeps_the_legacy_stylesheet(qapp):  # noqa: F811
    host = _make_theme_host(qapp, {"ui-theme": "gray"})
    css = host.styleSheet()

    assert "background-color: #b2b2b2;" in css      # window body
    assert "border-left: 4px solid #787878" in css  # window frame
    assert "rgba(178,178,178" not in css


def test_translucent_chrome_fades_background_frame_and_title_band(qapp):  # noqa: F811
    host = _make_theme_host(qapp, {"ui-theme": "gray", CHROME_OPACITY_KEY: 50})
    css = host.styleSheet()

    assert "background-color: rgba(178,178,178,0.500);" in css        # body
    assert "border-left: 4px solid rgba(120,120,120,0.500)" in css    # frame
    assert "border-bottom: 4px solid rgba(120,120,120,0.500)" in css
    assert css.count("background-color: rgba(120,120,120,0.500)") == 1  # title band


def test_content_colours_stay_opaque_while_the_chrome_fades(qapp):  # noqa: F811
    """A colour tool may never render the colours themselves see-through."""
    host = _make_theme_host(qapp, {"ui-theme": "gray", CHROME_OPACITY_KEY: 30})
    value_css = host.slider_widgets["R"][1].styleSheet()

    assert "#eaeaea" in value_css   # value box keeps its solid input surface
    assert "rgba" not in value_css


def test_zero_percent_still_paints_a_hairline_of_alpha(qapp):  # noqa: F811
    """A literal alpha 0 would make Windows route clicks through the panel
    onto the canvas; 1/255 looks the same and keeps the window grabbable."""
    host = _make_theme_host(qapp, {"ui-theme": "gray", CHROME_OPACITY_KEY: 0})
    css = host.styleSheet()

    assert with_opacity("#b2b2b2", 0) == "rgba(178,178,178,0.004)"
    assert "background-color: rgba(178,178,178,0.004);" in css
    assert "rgba(120,120,120,0.004)" in css  # frame and title band too


def test_junk_config_values_still_fall_back_to_opaque(qapp):  # noqa: F811
    host = _make_theme_host(qapp, {"ui-theme": "gray", CHROME_OPACITY_KEY: "see-through"})

    assert "background-color: #b2b2b2;" in host.styleSheet()


class _StubHistory:
    """Records what the theme pushes into the colour-history panel."""

    def __init__(self):
        self.backgrounds = []

    def configure(self, cols, rows):
        pass

    def apply_theme(self, bg, border_color, text):
        self.backgrounds.append(bg)


@pytest.mark.parametrize(("opacity", "expected_alpha"), [(100, 255), (50, 0)])
def test_history_panel_only_repaints_its_body_while_opaque(qapp, opacity, expected_alpha):  # noqa: F811
    """Its fill duplicates the window background: stacking it would show a
    denser rectangle once the chrome is translucent."""
    host = _make_theme_host(qapp, {"ui-theme": "gray", CHROME_OPACITY_KEY: opacity})
    host.color_history = _StubHistory()
    host.apply_theme(1.0, is_resize_event=True)

    assert host.color_history.backgrounds[-1].alpha() == expected_alpha

# ── Settings wiring ───────────────────────────────────────────────────────


@pytest.fixture
def sidebar(qapp, monkeypatch):  # noqa: F811
    """A real settings sidebar on a stub main window.

    Config load/save are redirected to an in-memory store, so the test
    exercises the real persistence path (``_persist_config`` →
    ``save_hotkey_config`` → ``load_hotkey_config``) without touching the
    user's config file or the filesystem at all.
    """
    from PyQt6.QtWidgets import QWidget

    saved: dict = {}

    def _fake_save(cfg):
        saved.clear()
        saved.update(cfg)

    def _fake_load():
        merged = config.default_hotkey_config()
        merged.update(saved)
        return merged

    monkeypatch.setattr(config, "save_hotkey_config", _fake_save)
    monkeypatch.setattr(config, "load_hotkey_config", _fake_load)

    class _StubSyncThread:
        def __init__(self):
            self.companion_sync = type(
                "_StubCompanion", (), {"_has_session": lambda self: False},
            )()

    class _StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = config.load_hotkey_config()
            self.sync_thread = _StubSyncThread()
            self.theme_calls = 0

        def apply_theme(self, scale=None, is_resize_event=False):
            self.theme_calls += 1

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


def test_slider_loads_the_stored_opacity(sidebar):
    sidebar.cfg[CHROME_OPACITY_KEY] = 65
    sidebar._persist_config()
    sidebar.refresh_ui()  # re-reads the saved config from disk

    assert sidebar.opacity_slider.value() == 65
    assert sidebar.lbl_opacity.text() == "65%"


def test_slider_range_matches_the_clamp(sidebar):
    assert sidebar.opacity_slider.minimum() == CHROME_OPACITY_MIN
    assert sidebar.opacity_slider.maximum() == CHROME_OPACITY_MAX


def test_dragging_previews_on_the_window_without_saving(sidebar):
    parent = sidebar._parent
    before = parent.theme_calls

    sidebar.opacity_slider.setSliderDown(True)   # grab the handle
    sidebar.opacity_slider.setValue(45)          # valueChanged → live preview

    assert parent.cfg[CHROME_OPACITY_KEY] == 45   # window repaints at 45%
    assert parent.theme_calls > before
    assert sidebar.lbl_opacity.text() == "45%"
    # ...but nothing is written while the handle is held
    assert config.load_hotkey_config()[CHROME_OPACITY_KEY] == CHROME_OPACITY_DEFAULT


def test_releasing_the_handle_persists_the_opacity(sidebar):
    sidebar.opacity_slider.setSliderDown(True)
    sidebar.opacity_slider.setValue(45)
    sidebar.opacity_slider.setSliderDown(False)  # emits sliderReleased

    assert sidebar.cfg[CHROME_OPACITY_KEY] == 45
    assert config.load_hotkey_config()[CHROME_OPACITY_KEY] == 45


def test_changes_without_a_drag_commit_immediately(sidebar):
    """Arrow keys / groove clicks never emit sliderReleased, so a preview
    that nothing ever saved would silently revert on the next reload."""
    sidebar.opacity_slider.setValue(55)

    assert config.load_hotkey_config()[CHROME_OPACITY_KEY] == 55


def test_saving_any_other_setting_keeps_the_opacity(sidebar):
    """save_settings() rewrites the whole config from the widgets, so the
    opacity has to be read back out of its slider like every other value."""
    sidebar.cfg[CHROME_OPACITY_KEY] = 70
    sidebar._persist_config()
    sidebar.refresh_ui()
    sidebar.save_settings()

    assert sidebar.cfg[CHROME_OPACITY_KEY] == 70

