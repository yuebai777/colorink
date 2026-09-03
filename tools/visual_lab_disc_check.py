"""Offscreen visual evidence for the LAB disc fixes.

Renders:
  1. The circulant LAB / OKLab disc at several lightness values (lab + oklab).
  2. A harmony-dot click before/after pair proving the base indicator stays.

Run:  QT_QPA_PLATFORM=offscreen python tools/visual_lab_disc_check.py
Outputs: screenshots/lab_disc_verify/*.png
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui.lab_harmony import harmony_hue_offsets
from ui.lab_visualizer import LabSquare

OUT_DIR = os.path.join(os.getcwd(), "screenshots", "lab_disc_verify")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    for mode in ("lab", "oklab"):
        for L in (20.0, 35.0, 50.0, 80.0, 92.0):
            sq = LabSquare()
            sq.resize(320, 340)
            sq.set_render_mode(mode)
            sq.set_shape("disc")
            sq.set_lightness(L, update_widget=False)
            sq._invalidate_full_cache()
            sq._render_ab_plane()
            sq.update()
            app.processEvents()
            pixmap = sq.grab()
            name = f"disc_{mode}_L{int(L)}.png"
            pixmap.save(os.path.join(OUT_DIR, name))
            print(f"saved {name}")

    # Harmony-dot click: before / after.
    sq = LabSquare()
    sq.resize(320, 340)
    sq.set_render_mode("lab")
    sq.set_shape("disc")
    sq.set_harmony_mode("analogous")
    sq.set_color(180, 130, 30, block_signals=True)
    sq._invalidate_full_cache()
    sq._render_ab_plane()
    sq.update()
    app.processEvents()
    sq.grab().save(os.path.join(OUT_DIR, "harmony_before_click.png"))

    anchor_before = sq._anchor_ab
    main_before = (sq.a, sq.b)
    points = sq._harmony_points_ab()
    target = points[1]
    pos = sq._disc_ab_to_screen(target[0], target[1])
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sq.mousePressEvent(ev)
    app.processEvents()
    sq.grab().save(os.path.join(OUT_DIR, "harmony_after_click.png"))

    anchor_after = sq._anchor_ab
    main_after = (sq.a, sq.b)

    anchor_ok = (abs(anchor_after[0] - anchor_before[0]) <= 1e-9
                 and abs(anchor_after[1] - anchor_before[1]) <= 1e-9)
    promoted_ok = (abs(main_after[0] - target[0]) <= 1e-9
                   and abs(main_after[1] - target[1]) <= 1e-9)

    print(f"main before : {main_before}")
    print(f"main after  : {main_after}")
    print(f"anchor before: {anchor_before}")
    print(f"anchor after : {anchor_after}")
    print(f"picked      : {sq._picked_ab}")
    print(f"harmony idx : {sq._picked_harmony_index}")
    print(f"anchor unchanged after click: {anchor_ok}")
    print(f"clicked dot promoted to main: {promoted_ok}")

    # Drag the promoted dot A to a new position: the anchor is solved back
    # from A_new (f_harmonic_inverse) and the whole pattern follows A.
    main_pos = sq._disc_ab_to_screen(sq.a, sq.b)
    sq.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, main_pos, main_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))
    new_pos = QPointF(150.0, 130.0)
    sq.handle_mouse(new_pos)
    sq.mouseReleaseEvent(None)
    app.processEvents()
    sq.grab().save(os.path.join(OUT_DIR, "harmony_after_drag.png"))

    a_new = sq._disc_screen_to_ab(new_pos)
    anchor_new = sq._anchor_ab
    anchor_moved_ok = (abs(anchor_new[0] - a_new[0]) > 1e-6
                       or abs(anchor_new[1] - a_new[1]) > 1e-6)
    slot_ok = abs(sq._harmony_points_ab()[1][0] - a_new[0]) <= 1e-6 and \
        abs(sq._harmony_points_ab()[1][1] - a_new[1]) <= 1e-6
    print(f"drag a_new      : {a_new}")
    print(f"drag anchor_new : {anchor_new}")
    print(f"drag idx        : {sq._picked_harmony_index}")
    print(f"drag A stays on its harmony slot: {slot_ok}")
    print(f"anchor != A (inverse solved)     : {anchor_moved_ok}")

    # Rectangle harmony mode: Procreate-style square (90° steps).
    sqr = LabSquare()
    sqr.resize(320, 340)
    sqr.set_render_mode("lab")
    sqr.set_shape("disc")
    sqr.set_harmony_mode("rectangle")
    sqr.set_color(180, 130, 30, block_signals=True)
    sqr._invalidate_full_cache()
    sqr._render_ab_plane()
    sqr.update()
    app.processEvents()
    sqr.grab().save(os.path.join(OUT_DIR, "harmony_rectangle_square.png"))
    print(f"rectangle offsets: {harmony_hue_offsets('rectangle')}")
    return 0 if anchor_ok and promoted_ok and slot_ok and anchor_moved_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
