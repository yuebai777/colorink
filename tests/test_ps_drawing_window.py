"""Post-write "assumed drawing" window (Wintab first-stroke pressure fix).

Photoshop's CEP panel applies each colour with ExtendScript on PS's main
thread and then keeps reading state with another ExtendScript every ~100 ms.
Those calls block PS's message pump, and Wintab's stylus packet queue stalls
while it is blocked — a stroke starting right then loses its opening pressure
packet.  Because the user usually draws immediately after confirming a pick,
``memory_sync`` opens a short "assumed drawing" window after every write so
the panel's *periodic state read* stands down (the apply itself is never
delayed).

The window is the driver-agnostic fallback for ``is_painting``: some Wintab
drivers (Huion / XP-Pen) never report the pen tip through VK_LBUTTON.
"""

import time

import pytest
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread
from core.photoshop_script_bridge import PhotoshopScriptBridge


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


# -- bridge-level window -----------------------------------------------------

def test_bridge_window_opens_and_expires():
    bridge = PhotoshopScriptBridge()
    assert bridge.assume_drawing_active() is False
    bridge.note_color_applied(window_ms=60)
    assert bridge.assume_drawing_active() is True
    time.sleep(0.09)
    assert bridge.assume_drawing_active() is False


def test_bridge_window_zero_is_immediately_inactive():
    bridge = PhotoshopScriptBridge()
    bridge.note_color_applied(window_ms=0)
    assert bridge.assume_drawing_active() is False


# -- memory_sync wiring -----------------------------------------------------

class FakePs:
    """Mimics the PS bridge surface the sync loop touches."""

    def __init__(self, assume_active: bool) -> None:
        self.fg = {"r": 5, "g": 5, "b": 5, "index": 0}
        self.bg = {"r": 255, "g": 255, "b": 255, "index": 1}
        self.assume_active = assume_active
        self.drawing_writes: list[bool] = []
        self.note_calls = 0

    # writes ---------------------------------------------------------------
    def set_color(self, r, g, b, color_index=0):
        return True

    def set_both_colors(self, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b):
        return True

    def note_color_applied(self, window_ms: int = 450) -> None:
        self.note_calls += 1

    # reads ----------------------------------------------------------------
    def get_color(self):
        return dict(self.fg)

    def get_bg_color(self):
        return dict(self.bg)

    def status(self):
        return {"connected": True}

    def set_drawing(self, is_drawing: bool) -> None:
        self.drawing_writes.append(bool(is_drawing))

    def assume_drawing_active(self) -> bool:
        return self.assume_active


def _run_ps_thread(ps, qapp, seconds: float) -> MemorySyncThread:
    thread = MemorySyncThread()
    thread.software_mode = "ps"
    thread.ps_sync = ps
    thread.start()
    t0 = time.time()
    while time.time() - t0 < seconds:
        qapp.processEvents()
        time.sleep(0.02)
    return thread


def _wait_for(pred, qapp, timeout: float = 3.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        qapp.processEvents()
        time.sleep(0.02)
    return False


def test_assumed_window_marks_drawing_even_without_pen_tip(qapp):
    """With the window open the loop must flag drawing.txt even though the
    foreground / VK_LBUTTON checks cannot succeed (no PS pid on the fake)."""
    ps = FakePs(assume_active=True)
    thread = _run_ps_thread(ps, qapp, 0.0)
    try:
        assert _wait_for(lambda: True in ps.drawing_writes, qapp), ps.drawing_writes
    finally:
        thread.stop()


def test_no_drawing_flag_when_neither_signal_is_active(qapp):
    ps = FakePs(assume_active=False)
    thread = _run_ps_thread(ps, qapp, 0.0)
    try:
        # Give the loop a few cycles; drawing must stay False throughout.
        time.sleep(0.4)
        qapp.processEvents()
        assert True not in ps.drawing_writes, ps.drawing_writes
    finally:
        thread.stop()


def test_write_opens_the_window_on_the_bridge(qapp):
    ps = FakePs(assume_active=False)
    thread = _run_ps_thread(ps, qapp, 0.0)
    try:
        thread.write_color(10, 20, 30, color_index=0)
        assert _wait_for(lambda: ps.note_calls > 0, qapp), ps.note_calls
    finally:
        thread.stop()


def test_bridge_without_window_api_is_tolerated(qapp):
    """A bridge lacking the new methods must not break the sync loop."""
    class BarePs(FakePs):
        assume_drawing_active = None  # type: ignore[assignment]
        note_color_applied = None     # type: ignore[assignment]

    ps = BarePs(assume_active=False)
    # hasattr() is True for None — the loop must use callable() guards.
    ps.assume_drawing_active = None  # type: ignore[assignment]
    thread = _run_ps_thread(ps, qapp, 0.0)
    try:
        thread.write_color(1, 2, 3, color_index=0)
        time.sleep(0.3)
        qapp.processEvents()
        assert True not in ps.drawing_writes
    finally:
        thread.stop()
