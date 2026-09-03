"""Circulant LAB disc + harmony-mode tests.

Covers the ``LabSquare`` shape switch (square ⇄ disc), the disc renderer
(per-hue rim colours, geometric-circle alpha with no transparent notches),
harmony offset tables, and the "click a harmony dot to promote it to the
main point without re-anchoring the pattern" interaction.
"""

import math

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from ui.color_conversions import find_max_lab_c, lab_to_rgb, oklab_to_rgb
from ui.lab_harmony import harmony_hue_offsets, is_valid_harmony_mode
from ui.lab_prewarm import (
    LabPrewarmRequest,
    _disc_chroma_profile,
    _disc_grid,
    render_lab_plane,
    smoothed_boundary_chroma,
)
from ui.lab_visualizer import LabSquare

from .test_ringless_support import qapp  # noqa: F401


def _disc_request(size=64, mode="lab", shape="disc", lightness=50.0):
    return LabPrewarmRequest(
        generation=0, render_mode=mode, lightness=lightness, size=size,
        min_a=-110.0, max_a=110.0, min_b=-110.0, max_b=110.0,
        pixel_ratio=1.0, shape=shape,
    )


def test_default_shape_is_square(qapp):
    sq = LabSquare()
    assert sq.shape == "square"


def test_lab_square_uses_wheel_crosshair_cursor(qapp):
    """The LAB visualizer must use the color wheel's crosshair cursor:
    switching wheel ⇄ LAB must not change the mouse/pen cursor."""
    sq = LabSquare()
    assert sq.cursor().shape() == Qt.CursorShape.CrossCursor
    sq2 = LabSquare()
    sq2.set_shape("disc")
    assert sq2.cursor().shape() == Qt.CursorShape.CrossCursor


def test_disc_drag_blanks_cursor_like_wheel(qapp):
    """While picking on the LAB plane the crosshair hides (same as the
    wheel's inner-region drag) and comes back on release."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(150, 100), QPointF(150, 100),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sq.mousePressEvent(press)
    assert sq.dragging is True
    assert sq.cursor().shape() == Qt.CursorShape.BlankCursor
    sq.mouseReleaseEvent(None)
    assert sq.dragging is False
    assert sq.cursor().shape() == Qt.CursorShape.CrossCursor


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


def test_rectangle_mode_offsets_form_square():
    """Procreate-style 'rectangle' harmony: four hues 90° apart, which on the
    hue circle form a square (base still at 0°)."""
    offsets = harmony_hue_offsets("rectangle")
    assert len(offsets) == 4
    assert offsets == (0.0, 90.0, 180.0, 270.0)
    gaps = [(offsets[i + 1] - offsets[i]) for i in range(3)]
    gaps.append(offsets[0] + 360.0 - offsets[-1])
    assert all(abs(g - 90.0) < 1e-9 for g in gaps)


def test_all_harmony_modes_keep_base_first():
    """The anchor colour (offset 0) must be the FIRST entry: it is the point
    the pattern is computed from.  Analogous used to be (-30, 0, 30), which
    silently hid its -30° dot."""
    for mode in ("complementary", "split", "analogous", "triadic",
                 "rectangle"):
        offsets = harmony_hue_offsets(mode)
        assert offsets[0] == 0.0, f"{mode}: base must be first ({offsets})"


def test_clicking_harmony_dot_promotes_it_without_reanchoring(qapp):
    """Clicking a harmony dot makes that dot the current main point (large
    indicator at its own position) while the harmony pattern stays anchored
    to the original main point — positions never re-form on selection."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    points0 = sq._harmony_points_ab()
    assert len(points0) == 3
    anchor = (sq._anchor_ab[0], sq._anchor_ab[1])
    assert sq.a == pytest.approx(anchor[0], abs=1e-6)
    assert sq.b == pytest.approx(anchor[1], abs=1e-6)

    emitted = []
    sq.colorChanged.connect(lambda r, g, b: emitted.append((r, g, b)))
    for i in (1, 2):
        target = points0[i]
        pos = sq._disc_ab_to_screen(*target)
        hit = sq._hit_harmony_point(pos)
        assert hit is not None, f"harmony dot {i} is not clickable"
        assert hit == pytest.approx(target, abs=1e-6)

        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress, pos, pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        sq.mousePressEvent(ev)

        # The clicked dot became the current main point...
        assert sq.a == pytest.approx(target[0], abs=1e-6)
        assert sq.b == pytest.approx(target[1], abs=1e-6)
        assert sq._picked_ab == pytest.approx(target, abs=1e-6)
        assert sq._picked_harmony_index == i
        # ...but the anchor is untouched: every dot stays where it was.
        assert sq._anchor_ab == pytest.approx(anchor, abs=1e-6)
        for j, (pa, pb) in enumerate(sq._harmony_points_ab()):
            assert (pa, pb) == pytest.approx(points0[j], abs=1e-6)
        r, g, b = sq._ab_to_rgb(*target)
        assert sq.get_current_rgb() == (r, g, b)
        assert emitted and emitted[-1] == (r, g, b)
        sq.mouseReleaseEvent(None)


def test_point_press_jitter_within_tolerance_stays_click(qapp):
    """Small jitter (≤ 5 px) after pressing a harmony dot must stay a pure
    click: no coordinate updates, no re-anchor, no drift."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    anchor_before = sq._anchor_ab
    assert sq._picked_harmony_index == 1

    jitter = QPointF(pos.x() + 3.0, pos.y())
    sq.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, jitter, jitter,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    assert sq._anchor_ab == pytest.approx(anchor_before, abs=1e-6)
    assert sq._picked_harmony_index == 1
    assert sq._drag_armed is False

    sq.mouseReleaseEvent(None)
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    assert sq._anchor_ab == pytest.approx(anchor_before, abs=1e-6)


def test_point_press_beyond_tolerance_becomes_drag(qapp):
    """Once the press moves past the tolerance, it becomes a real drag and
    the inverse-anchor linkage applies."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert sq._picked_harmony_index == 1

    new = QPointF(pos.x() + 8.0, pos.y())
    sq.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, new, new,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    a_new = sq._disc_screen_to_ab(new)
    anchor_new = sq._anchor_from_harmony_point(a_new[0], a_new[1], 1)
    assert sq._drag_armed is True
    assert (sq.a, sq.b) == pytest.approx(a_new, abs=1e-6)
    assert sq._anchor_ab == pytest.approx(anchor_new, abs=1e-6)
    assert sq._picked_harmony_index == 1
    assert sq._harmony_points_ab()[1] == pytest.approx(a_new, abs=1e-6)


def test_main_point_press_jitter_does_not_reanchor(qapp):
    """Pressing the large main point with small jitter must not re-anchor:
    it stays a pure click on the point."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    anchor_before = sq._anchor_ab
    main_pos = sq._disc_ab_to_screen(sq.a, sq.b)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, main_pos, main_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    jitter = QPointF(main_pos.x() - 2.0, main_pos.y() + 2.0)
    sq.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, jitter, jitter,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    sq.mouseReleaseEvent(None)
    assert sq._anchor_ab == pytest.approx(anchor_before, abs=1e-6)
    assert (sq.a, sq.b) == pytest.approx(anchor_before, abs=1e-6)


def test_drag_blank_area_reanchors_pattern_and_resets_selection(qapp):
    """Dragging a blank area re-anchors the whole pattern to the dragged
    position; the selection resets to the anchor (index 0)."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("complementary")
    sq.set_color(180, 130, 30, block_signals=True)

    cx, cy, _ = sq._disc_metrics()
    pos = QPointF(cx, cy)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert sq._anchor_ab == pytest.approx((0.0, 0.0), abs=1e-6)

    new_pos = QPointF(150.0, 130.0)
    sq.handle_mouse(new_pos)
    expected = sq._disc_screen_to_ab(new_pos)
    assert sq.a == pytest.approx(expected[0], abs=1e-6)
    assert sq.b == pytest.approx(expected[1], abs=1e-6)
    assert sq._picked_ab == pytest.approx(expected, abs=1e-6)
    assert sq._picked_harmony_index == 0
    assert sq._anchor_ab == pytest.approx(expected, abs=1e-6)
    assert sq._harmony_points_ab()[0] == pytest.approx(expected, abs=1e-6)


def test_lightness_change_keeps_picked_harmony_index(qapp):
    """The main point picked from a harmony dot stays on that dot when L
    moves: same harmony index under the new boundary, anchor unchanged."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sq.mousePressEvent(ev)
    sq.mouseReleaseEvent(None)
    anchor_before = sq._anchor_ab

    sq.set_lightness(60.0, update_widget=True)

    points = sq._harmony_points_ab()
    assert sq._anchor_ab == pytest.approx(anchor_before, abs=1e-6)
    assert sq._picked_harmony_index == 1
    assert sq._picked_ab == pytest.approx(points[1], abs=1e-6)
    assert sq.a == pytest.approx(points[1][0], abs=1e-6)
    assert sq.b == pytest.approx(points[1][1], abs=1e-6)


def test_click_anchor_after_selecting_dot_returns_to_anchor(qapp):
    """After selecting a harmony dot, the anchor becomes a small dot and is
    clickable again; clicking it restores it as the main point."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    anchor = (sq._anchor_ab[0], sq._anchor_ab[1])
    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    sq.mouseReleaseEvent(None)
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    assert sq._picked_harmony_index == 1

    pos_anchor = sq._disc_ab_to_screen(anchor[0], anchor[1])
    hit = sq._hit_harmony_point(pos_anchor)
    assert hit == pytest.approx(anchor, abs=1e-6)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos_anchor, pos_anchor,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    sq.mouseReleaseEvent(None)
    assert sq.a == pytest.approx(anchor[0], abs=1e-6)
    assert sq.b == pytest.approx(anchor[1], abs=1e-6)
    assert sq._picked_harmony_index == 0
    assert sq._anchor_ab == pytest.approx(anchor, abs=1e-6)


def test_dragging_promoted_dot_reanchors_pattern(qapp):
    """Pressing a harmony dot promotes it; dragging then solves the anchor
    back from A_new (f_harmonic_inverse) so the WHOLE pattern follows A while
    the anchor remains the source of the relative geometry."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    assert sq._picked_harmony_index == 1

    new_pos = QPointF(150.0, 130.0)
    sq.handle_mouse(new_pos)
    a_new = sq._disc_screen_to_ab(new_pos)
    anchor_new = sq._anchor_from_harmony_point(a_new[0], a_new[1], 1)

    # A follows the mouse; A is NOT treated as the new centre.
    assert (sq.a, sq.b) == pytest.approx(a_new, abs=1e-6)
    assert sq._picked_ab == pytest.approx(a_new, abs=1e-6)
    # The anchor is solved back from A_new and the dot stays on its slot.
    assert sq._anchor_ab == pytest.approx(anchor_new, abs=1e-6)
    assert sq._picked_harmony_index == 1
    assert sq._harmony_points_ab()[1] == pytest.approx(a_new, abs=1e-6)
    assert sq._harmony_points_ab()[0] == pytest.approx(anchor_new, abs=1e-6)
    # The anchor is genuinely a different point (not A itself).
    assert math.hypot(anchor_new[0] - a_new[0],
                      anchor_new[1] - a_new[1]) > 1e-6


def test_dragging_promoted_dot_from_large_dot_reanchors_pattern(qapp):
    """Real-user sequence: click A to promote it (press+release), then press
    the large dot A again and drag — the anchor is solved back from A_new, so
    the whole pattern follows A while A stays on its harmony slot."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    target = sq._harmony_points_ab()[1]
    pos = sq._disc_ab_to_screen(*target)

    # Click A -> promoted main point; release.
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    sq.mouseReleaseEvent(None)
    assert (sq.a, sq.b) == pytest.approx(target, abs=1e-6)
    assert sq._picked_harmony_index == 1

    # Press on the LARGE dot (A) again and drag it: a harmony-dot handle drag.
    main_pos = sq._disc_ab_to_screen(sq.a, sq.b)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, main_pos, main_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    assert sq._drag_from_dot is True

    new_pos = QPointF(150.0, 130.0)
    sq.handle_mouse(new_pos)
    a_new = sq._disc_screen_to_ab(new_pos)
    anchor_new = sq._anchor_from_harmony_point(a_new[0], a_new[1], 1)

    assert (sq.a, sq.b) == pytest.approx(a_new, abs=1e-6)
    assert sq._picked_ab == pytest.approx(a_new, abs=1e-6)
    assert sq._anchor_ab == pytest.approx(anchor_new, abs=1e-6)
    assert sq._picked_harmony_index == 1
    assert sq._harmony_points_ab()[1] == pytest.approx(a_new, abs=1e-6)
    assert sq._harmony_points_ab()[0] == pytest.approx(anchor_new, abs=1e-6)


def test_click_blank_reanchors_pattern(qapp):
    """Clicking blank disc space re-anchors the pattern to that position."""
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)

    cx, cy, _ = sq._disc_metrics()
    pos = QPointF(cx, cy)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    sq.mouseReleaseEvent(None)
    assert sq._anchor_ab == pytest.approx((0.0, 0.0), abs=1e-6)
    assert (sq.a, sq.b) == pytest.approx((0.0, 0.0), abs=1e-6)
    assert sq._picked_harmony_index == 0
    assert sq._harmony_points_ab()[0] == pytest.approx((0.0, 0.0), abs=1e-6)


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


def test_disc_metrics_ignore_avoid_top(qapp):
    sq = LabSquare()
    sq.resize(420, 420)
    sq.set_shape("disc")
    sq.set_avoid_top(120)
    cx, cy, radius = sq._disc_metrics()
    size = min(420 - 16, 420 - 6)
    # Size AND position are identical to the hue ring, ignoring avoid_top.
    assert radius == pytest.approx(size / 2.0 - 2.0, abs=1e-6)
    assert cy == pytest.approx(size / 2.0 + 6.0, abs=1e-6)
    assert cx == pytest.approx(210.0, abs=1e-6)


def test_disc_metrics_same_position_when_tall(qapp):
    sq = LabSquare()
    sq.resize(420, 620)
    sq.set_shape("disc")
    sq.set_avoid_top(120)
    cx, cy, radius = sq._disc_metrics()
    size = min(420 - 16, 620 - 6)
    assert radius == pytest.approx(size / 2.0 - 2.0, abs=1e-6)
    assert cy == pytest.approx(size / 2.0 + 6.0, abs=1e-6)


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
    # Radius 1 means the disc's per-hue gamut boundary for the exact hue
    # clicked (smoothed, same recipe the renderer paints) — not a uniform cap.
    C = math.hypot(sq.a, sq.b)
    hue = math.degrees(math.atan2(sq.b, sq.a)) % 360.0
    full_max = find_max_lab_c(sq.L, sq.a / C, sq.b / C)
    smoothed = smoothed_boundary_chroma("lab", sq.L, hue)
    assert C == pytest.approx(smoothed, abs=1e-6)
    assert C <= full_max + 1e-6


def test_disc_profile_has_no_radial_seam(qapp):
    """Regression: the OKLab blue "bay" made the per-hue chroma boundary jump
    ~0.03 C within a fraction of a degree — a knife-cut radial seam.  The
    smoothed profile must stay continuous (moving average), and the renderer
    clamps it to the raw boundary so every pixel stays displayable."""
    raw, smoothed = _disc_chroma_profile(0.35, "oklab")
    diff = np.abs(np.diff(np.concatenate([smoothed[-1:], smoothed])))
    assert float(np.max(diff)) < 0.005  # raw profile jumps 0.03+ at the bay
    # The smoothing must still track the gamut (clamped downstream): never
    # deviate upward from the raw boundary beyond the bay's own slack.
    assert float(np.max(np.abs(smoothed - raw))) < 0.06


def test_disc_blue_render_has_no_seam(qapp):
    """End-to-end: sample the OKLab disc ring near the blue direction and
    make sure no angular step exceeds the natural hue-ramp noise (~5-8 RGB)."""
    result = render_lab_plane(_disc_request(256, mode="oklab", lightness=35.0))
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(256, 256, 4)
    c = (256 - 1) / 2.0
    angles = np.arange(720) * 2.0 * np.pi / 720
    x = np.clip(np.round(c + 0.98 * c * np.cos(angles)).astype(int), 0, 255)
    y = np.clip(np.round(c - 0.98 * c * np.sin(angles)).astype(int), 0, 255)
    px = arr[y, x, :3].astype(float)
    deltas = np.linalg.norm(np.diff(px, axis=0, append=px[:1]), axis=1)
    assert float(np.max(deltas)) < 12.0


def test_harmony_dot_click_promotes_dot_but_keeps_anchor(qapp):
    sq = LabSquare()
    sq.resize(200, 200)
    sq.set_shape("disc")
    sq.set_harmony_mode("complementary")
    sq.set_color(180, 130, 30, block_signals=True)

    points = sq._harmony_points_ab()
    assert len(points) == 2
    anchor = (sq._anchor_ab[0], sq._anchor_ab[1])
    target_a, target_b = points[1]
    pos = sq._disc_ab_to_screen(target_a, target_b)

    # Hit test finds the small harmony dot.
    hit = sq._hit_harmony_point(pos)
    assert hit is not None
    hit_a, hit_b = hit
    assert hit_a == pytest.approx(target_a, abs=1e-6)
    assert hit_b == pytest.approx(target_b, abs=1e-6)

    emitted = []
    sq.colorChanged.connect(lambda r, g, b: emitted.append((r, g, b)))

    # Simulate a real press: the harmony dot becomes the current main point
    # (large indicator moves to its own position), while the anchor and the
    # whole point layout stay put.
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
    assert sq._anchor_ab == pytest.approx(anchor, abs=1e-6)
    assert sq._picked_ab == pytest.approx((target_a, target_b), abs=1e-6)
    assert sq._picked_harmony_index == 1
    r, g, b = sq._ab_to_rgb(target_a, target_b)
    assert sq.get_current_rgb() == (r, g, b)
    assert emitted and emitted[-1] == (r, g, b)


@pytest.mark.parametrize(
    ("mode", "lightness_internal"),
    [("lab", 35.0), ("lab", 50.0), ("oklab", 35.0), ("oklab", 50.0)],
)
def test_disc_rim_keeps_each_hues_square_plane_colour(
        qapp, mode, lightness_internal):
    """The disc must not lose colours from the square a*b* plane: at every
    hue the rim reaches that hue's own smoothed gamut boundary (per-hue
    chroma, not a single uniform cap)."""
    size = 192
    result = render_lab_plane(
        _disc_request(size, mode=mode, lightness=lightness_internal))
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(
        size, size, 4)
    c = (size - 1) / 2.0
    L_mode = lightness_internal / 100.0 if mode == "oklab" else lightness_internal

    raw, smoothed = _disc_chroma_profile(L_mode, mode)
    rr_grid, a_dir_grid, b_dir_grid, bins_grid = _disc_grid(size)

    for i in range(72):
        ang = 2.0 * math.pi * i / 72.0
        x_in = int(round(c + 0.95 * c * math.cos(ang)))
        y_in = int(round(c - 0.95 * c * math.sin(ang)))
        px = arr[y_in, x_in]
        assert px[3] == 255, f"rr=0.95 must be visible at angle {i}"

        rr_here = float(rr_grid[y_in, x_in])
        a_here = float(a_dir_grid[y_in, x_in])
        b_here = float(b_dir_grid[y_in, x_in])
        bin_idx = int(bins_grid[y_in, x_in])
        max_c = float(np.minimum(smoothed[bin_idx], raw[bin_idx]))
        C = rr_here * max_c

        if mode == "lab":
            expected = lab_to_rgb(L_mode, C * a_here, C * b_here)
        else:
            expected = oklab_to_rgb(L_mode, C * a_here, C * b_here)
        for ch in range(3):
            assert abs(int(px[ch]) - round(expected[ch])) <= 2, (
                f"rim mismatch at angle {i} / {mode} / L={L_mode}")


@pytest.mark.parametrize(
    ("mode", "lightness_internal"),
    [("lab", 35.0), ("lab", 50.0), ("oklab", 35.0), ("oklab", 50.0)],
)
def test_disc_alpha_mask_is_circle(qapp, mode, lightness_internal):
    """The alpha edge stays a perfect circle: visible inside, transparent
    outside, no transparent notches at any hue."""
    size = 192
    result = render_lab_plane(
        _disc_request(size, mode=mode, lightness=lightness_internal))
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(
        size, size, 4)
    c = (size - 1) / 2.0

    assert arr[int(c), int(c), 3] == 255  # centre visible
    assert arr[0, 0, 3] == 0              # corner outside disc transparent

    for i in range(72):
        ang = 2.0 * math.pi * i / 72.0
        dx = math.cos(ang)
        dy = math.sin(ang)

        x_in = int(round(c + 0.95 * c * dx))
        y_in = int(round(c - 0.95 * c * dy))
        assert arr[y_in, x_in, 3] == 255, f"inside not visible at angle {i}"

        x_out = int(round(c + 1.05 * c * dx))
        y_out = int(round(c - 1.05 * c * dy))
        if 0 <= x_out < size and 0 <= y_out < size:
            assert arr[y_out, x_out, 3] == 0, (
                f"rr>1 must stay transparent at angle {i}")


@pytest.mark.parametrize(
    ("mode", "lightness_internal"),
    [("lab", 92.0), ("lab", 95.0), ("oklab", 92.0), ("oklab", 95.0)],
)
def test_disc_no_transparent_notches_inside_circle(
        qapp, mode, lightness_internal):
    """Regression: near-white lightness previously punched tiny transparent
    notches into the rim (per-bin boundary mismatch at sharp gamut corners).
    The alpha inside the geometric circle must be fully visible."""
    size = 192
    result = render_lab_plane(
        _disc_request(size, mode=mode, lightness=lightness_internal))
    arr = np.frombuffer(result.image_bytes, dtype=np.uint8).reshape(
        size, size, 4)
    rr, _, _, _ = _disc_grid(size)
    holes = int(((rr <= 1.0) & (arr[..., 3] == 0)).sum())
    assert holes == 0, (
        f"{mode} L={lightness_internal}: {holes} transparent pixel(s) "
        "inside the circle (edge notches)")
