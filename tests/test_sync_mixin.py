"""Tests for SyncMixin external color / active-slot handling.

These cover the regressions where an off-slot external color change did not
update that slot's source tracking, and an external active-slot change only
updated the wheel instead of the whole UI state.
"""

import os

import pytest
from PyQt6.QtGui import QColor

from ui.color_model import Color
from ui.window.sync_mixin import SyncMixin


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakePreview:
    def __init__(self):
        self.fg_color = QColor(255, 0, 0)
        self.bg_color = QColor(0, 0, 255)
        self.active_slot = "fg"

    def set_transparent(self, slot, transparent):
        setattr(self, f"{slot}_transparent", transparent)

    def update_slot_borders(self, slot):
        self.active_slot = slot


class _FakeSync:
    def __init__(self):
        self.active_slot = "fg"
        self._fg_transparent = False
        self._bg_transparent = False
        self._fg_source_space = "rgb"
        self._fg_source_values = {"r": 255.0, "g": 0.0, "b": 0.0}
        self._bg_source_space = "hsv"
        self._bg_source_values = {"h": 240.0, "s": 100.0, "v": 100.0}
        self.preview_box = _FakePreview()
        self.projected = None

    def _color_from_source(self, space, values, fallback_rgb):
        channels = {
            "rgb": ("r", "g", "b"),
            "hsv": ("h", "s", "v"),
        }.get(space)
        if channels and values:
            return Color.from_space(
                space, tuple(float(values[ch]) for ch in channels)
            )
        return Color.from_rgb(*fallback_rgb)

    def _project_color(self, color, source=""):
        self.projected = (color, source)


def test_external_off_slot_color_change_updates_source_tracking(qapp):
    sync = _FakeSync()

    SyncMixin.on_external_color_changed(sync, 0, 0, 255, 1)

    assert sync.preview_box.bg_color.getRgb()[:3] == (0, 0, 255)
    assert sync._bg_source_space == "rgb"
    assert sync._bg_source_values == {"r": 0.0, "g": 0.0, "b": 255.0}
    # The active slot / wheel state must be left untouched.
    assert sync.active_slot == "fg"
    assert sync.projected is None


def test_external_active_slot_change_projects_full_state(qapp):
    sync = _FakeSync()

    SyncMixin.on_external_active_slot_changed(sync, 1)

    assert sync.active_slot == "bg"
    assert sync.preview_box.active_slot == "bg"
    assert sync.projected is not None
    color, source = sync.projected
    assert source == "slot_change"
    assert color.rgb == (0, 0, 255)
