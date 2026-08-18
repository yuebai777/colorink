"""Tests for MainWindow._project_color keeping color_state.current in sync.

Some callers (slot changes, history picks, external active-slot changes)
build a Color directly and call _project_color without going through
ColorState.set_from.  _project_color must still update color_state.current so
the right-click menu and later UI actions see the active slot's color.
"""

from ui.color_model import Color
from ui.main_window import MainWindow


class _FakeColorState:
    def __init__(self):
        self.current = None

    def apply(self, color):
        self.current = color


class _FakeMain:
    def __init__(self):
        self.color_state = _FakeColorState()
        self._SOURCE_CHANNELS = {
            "rgb": ("r", "g", "b"),
            "hsv": ("h", "s", "v"),
        }
        self._source_space = None
        self._source_values = None
        self.calls = []

    def _color_source_dict(self, color):
        names = self._SOURCE_CHANNELS.get(color.source_space)
        if not names:
            return None
        return {
            ch: float(v)
            for ch, v in zip(names, color.to(color.source_space))
        }

    def update_ui_colors(self, r, g, b, source="", hsv=None, oklch=None, oklab=None):
        self.calls.append((r, g, b, source, hsv, oklch, oklab))


def test_project_color_updates_color_state_current():
    fake = _FakeMain()
    color = Color.from_rgb(10, 20, 30)

    MainWindow._project_color(fake, color, source="test")

    assert fake.color_state.current is color
    assert fake._source_space == "rgb"
    assert fake._source_values == {"r": 10.0, "g": 20.0, "b": 30.0}
    assert fake.calls and fake.calls[0][:4] == (10, 20, 30, "test")
