"""Circulant LAB disc + harmony-mode tests.

Covers the new ``LabSquare`` shape switch (square ⇄ disc), the disc image
renderer (circular alpha mask), harmony offset tables, and the "small dot
becomes the large base dot" interaction.
"""

import math

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from ui.color_conversions import find_max_lab_c
from ui.lab_harmony import harmony_hue_offsets, is_valid_harmony_mode
from ui.lab_prewarm import LabPrewarmRequest, render_lab_plane
from ui.lab_visualizer import LabSquare

from .test_ringless_support import qapp  # noqa: F401


def _disc_request(size=64, mode="lab", shape="disc"):
    return LabPrewarmRequest(
        generation=0, render_mode=mode, lightness=50.0, size=size,
        min_a=-110.0, max_a=110.0, min_b=-110.0, max_b=110.0,
        pixel_ratio=1.0, shape=shape,
    )


def test_default_shape_is_square(qapp):
    sq = LabSquare()
    assert sq.shape == "square"


def test_set_shape_disc_invalidates_and_updates(qapp):
    sq = LabSquare()
    sq.resize(200, 200)
    sq._invalidate_full_cache()
    sq.set_shape("disc")
    assert sq.shape == "disc"
    sq.set_shape("bogus")
    assert sq.shape == "disc"  # invalid falls back to keeping current value


def test_disc_render_alpha_mask(qapp):
    result = render_lab_plane(_disc_request(64))
    assert result.image_width == 64
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(64, 64, 4)
    assert arr[32, 32, 3] == 255       # centre visible
    assert arr[0, 0, 3] == 0           # corner outside disc transparent


def test_disc_render_pixels_are_coloured(qapp):
    result = render_lab_plane(_disc_request(48))
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(48, 48, 4)
    # The disc must not be a single flat grey: compare a few interior pixels.
    distinct = {(int(arr[y, x, 0]), int(arr[y, x, 1]), int(arr[y, x, 2]))
                for x in range(6, 42, 7) for y in range(6, 42, 7)
                if arr[y, x, 3] == 255}
    assert len(distinct) > 1


def test_disc_oklab_render(qapp):
    result = render_lab_plane(_disc_request(32, mode="oklab"))
    assert result.image_width == 32
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(32, 32, 4)
    assert arr[16, 16, 3] == 255
    assert arr[0, 0, 3] == 0


def test_harmony_offset_counts():
    assert is_valid_harmony_mode("complementary")
    assert len(harmony_hue_offsets("complementary")) == 2
    assert len(harmony_hue_offsets("split")) == 3
    assert len(harmony_hue_offsets("analogous")) == 3
    assert len(harmony_hue_offsets("triadic")) == 3
    assert len(harmony_hue_offsets("rectangle")) == 4
    # Unknown modes fall back to the analogous preset.
    assert harmony_hue_offsets("bogus") == harmony_hue_offsets("analogous")


def test_set_harmony_mode_invalid_falls_back(qapp):
    sq = LabSquare()
    sq.set_harmony_mode("bogus")
    assert sq.harmony_mode == "analogous"


def test_disc_metrics_match_wheel_outer_edge(qapp):
    sq = LabSquare()
    sq.resize(420, 420)
    sq.set_shape("disc")
    cx, cy, radius = sq._disc_metrics()
    size = min(420 - 16, 420 - 6)
    assert radius == pytest.approx(size / 2.0 - 2.0, abs=1e-6)
    assert cy == pytest.approx(size / 2.0 + 6.0, abs=1e-6)
    assert cx == pytest.approx(210.0, abs=1e-6)


def test_disc_metrics_keep_wheel_size_with_avoid_top(qapp):
    sq = LabSquare()
    sq.resize(420, 420)
    sq.set_shape("disc")
    sq.set_avoid_top(120)
    cx, cy, radius = sq._disc_metrics()
    size = min(420 - 16, 420 - 6)
    # The outer radius matches the hue ring even when the top strip is busy.
    assert radius == pytest.approx(size / 2.0 - 2.0, abs=1e-6)
    assert cy + radius <= 420.0 - 6.0
    # When there is not enough vertical room, the disc keeps the hue-ring
    # radius and sits as low as the bottom margin allows (full size wins).
    assert cy == pytest.approx(420.0 - 6.0 - radius, abs=1e-6)
    assert cy >= size / 2.0 + 6.0 - 1e-6


def test_disc_metrics_shift_down_when_room_allows(qapp):
    sq = LabSquare()
    sq.resize(420, 620)
    sq.set_shape("disc")
    sq.set_avoid_top(120)
    size = min(420 - 16, 620 - 6)
    cx, cy, radius = sq._disc_metrics()
    assert radius == pytest.approx(size / 2.0 - 2.0, abs=1e-6)
    # Sufficient room → the disc stays wheel-sized and moves below the strip.
    assert cy == pytest.approx(120.0 + size / 2.0, abs=1e-6)
    assert cy - radius >= 119.0


def test_disc_mouse_center_is_neutral(qapp):
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_color(128, 128, 128, block_signals=True)
    cx, cy, _ = sq._disc_metrics()
    sq.handle_mouse(QPointF(cx, cy))
    assert sq.a == pytest.approx(0.0, abs=1e-6)
    assert sq.b == pytest.approx(0.0, abs=1e-6)


def test_disc_edge_point_is_in_gamut(qapp):
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    # Click the right edge of the inscribed disc (at/beyond rim → radius 1).
    sq.handle_mouse(QPointF(200.0, 100.0))
    assert sq._is_in_gamut(sq.a, sq.b)
    # Radius 1 means the disc's capped chroma ceiling for that hue.
    C = math.hypot(sq.a, sq.b)
    full_max = find_max_lab_c(sq.L, sq.a / C, sq.b / C)
    assert C == pytest.approx(min(full_max, sq._disc_chroma_ceiling()), abs=1e-6)


def test_harmony_dot_click_promotes_to_base(qapp):
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("complementary")
    sq.set_color(180, 130, 30, block_signals=True)

    points = sq._harmony_points_ab()
    assert len(points) == 2
    target_a, target_b = points[1]
    pos = sq._disc_ab_to_screen(target_a, target_b)

    # Hit test finds the small harmony dot.
    hit = sq._hit_harmony_point(pos)
    assert hit is not None
    hit_a, hit_b = hit
    assert hit_a == pytest.approx(target_a, abs=1e-6)
    assert hit_b == pytest.approx(target_b, abs=1e-6)

    # Simulate a real press: the harmony point becomes the new base dot.
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sq.mousePressEvent(ev)
    assert sq.a == pytest.approx(target_a, abs=1e-6)
    assert sq.b == pytest.approx(target_b, abs=1e-6)
