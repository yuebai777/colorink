"""PS (script/CEP bridge) read-back echo suppression tests.

The bridge applies writes asynchronously (up to ~100 ms poll latency).
Without suppression, the poll loop would read back the *previous* value
and emit a spurious color_changed that yanks the UI back to the stale
color (乱跳). These tests verify the echo is suppressed while a write
is in flight, but genuine external changes still propagate.
"""

import time
from typing import Any

import pytest
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread

APPLY_LATENCY = 0.15  # simulated bridge poll latency in seconds


class FakePs:
    """Mimics the PhotoshopSync surface with async (latent) apply."""

    def __init__(self) -> None:
        self.fg = {"r": 5, "g": 5, "b": 5, "index": 0}
        self.bg = {"r": 255, "g": 255, "b": 255, "index": 1}
        self._pending: dict[int, tuple] = {}  # index -> (rgb, apply_at)

    def set_color(self, r, g, b, color_index=0):
        self._pending[color_index] = ((r, g, b), time.time() + APPLY_LATENCY)
        return True

    def set_both_colors(self, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b):
        self._pending[0] = ((fg_r, fg_g, fg_b), time.time() + APPLY_LATENCY)
        self._pending[1] = ((bg_r, bg_g, bg_b), time.time() + APPLY_LATENCY)
        return True

    def get_color(self):
        return self._read(0)

    def get_bg_color(self):
        return self._read(1)

    def _read(self, index):
        pending = self._pending.get(index)
        if pending is not None and time.time() >= pending[1]:
            self._pending.pop(index, None)
            if index == 0:
                self.fg = {"r": pending[0][0], "g": pending[0][1],
                           "b": pending[0][2], "index": 0}
            else:
                self.bg = {"r": pending[0][0], "g": pending[0][1],
                           "b": pending[0][2], "index": 1}
        return dict(self.fg if index == 0 else self.bg)

    def status(self):
        return {"connected": True}


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _Harness:
    """Runs a MemorySyncThread in ps mode with a fake bridge backend."""

    def __init__(self, ps: FakePs, qapp) -> None:
        self.qapp = qapp
        self.thread = MemorySyncThread()
        self.thread.software_mode = "ps"
        setattr(self.thread, "ps_sync", ps)
        self.events: list[str] = []

        def on_color(r, g, b, i):
            self.events.append(f"color({r},{g},{b},{i})")

        def on_transp(i, t):
            self.events.append(f"transp({i},{t})")

        def on_active(i):
            self.events.append(f"active({i})")

        self.thread.signals.color_changed.connect(on_color)
        self.thread.signals.transparent_changed.connect(on_transp)
        self.thread.signals.active_slot_changed.connect(on_active)

    def start(self):
        self.thread.start()

    def drain(self, seconds: float):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.qapp.processEvents()
            time.sleep(0.02)

    def stop(self):
        self.thread.stop()


def _wait_until(harness, pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(harness.events):
            return True
        harness.qapp.processEvents()
        time.sleep(0.02)
    return False


def test_ps_write_echo_is_suppressed_while_in_flight(qapp):
    """A write must not be immediately undone by the stale read-back:
    no spurious color_changed may fire for the old value AFTER the write."""
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        # Let the initial state observations settle, then clear them.
        h.drain(1.2)
        h.events.clear()

        # Write fg; the fake bridge applies it after APPLY_LATENCY.
        h.thread.write_color(10, 200, 30, color_index=0)
        # Drain past the apply point; the echo of (5,5,5) would have
        # fired within ~1s without suppression.
        h.drain(2.5)
        assert all(not e.startswith("color(") for e in h.events), h.events
    finally:
        h.stop()


def test_ps_external_change_still_propagates(qapp):
    """Genuine external changes (user picked in PS) must still emit."""
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        # No local write: wait until the initial (5,5,5) settles...
        # (the initial state is never seeded, so it emits once — that is
        # the "external" initial observation; clear it.)
        h.drain(1.2)
        h.events.clear()

        # External change in PS: the bridge mirror shows a new color.
        ps.fg = {"r": 99, "g": 100, "b": 101, "index": 0}
        assert _wait_until(h, lambda ev: "color(99,100,101,0)" in ev), h.events
    finally:
        h.stop()


def test_ps_external_change_after_recent_write_still_propagates(qapp):
    """A PS-side change made right after a Colorink write must not be
    swallowed by the echo-suppression window. Only the stale read-back of
    the pre-write color is supposed to be suppressed."""
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        h.drain(1.2)
        h.events.clear()

        # Queue a local fg write, then have the user change fg in PS before
        # the bridge applies it. Cancelling the pending apply simulates the
        # user's edit winning the race.
        h.thread.write_color(10, 200, 30, color_index=0)
        h.drain(0.3)
        ps._pending.clear()
        ps.fg = {"r": 77, "g": 88, "b": 99, "index": 0}

        assert _wait_until(
            h, lambda ev: "color(77,88,99,0)" in ev, timeout=0.8
        ), h.events
        assert not any(
            e.startswith("color(5,5,5,0)") for e in h.events
        ), h.events
    finally:
        h.stop()


def test_ps_fg_and_bg_writes_are_not_lost_when_queued_together(qapp):
    """Queuing a fg write and a bg write back-to-back must deliver both to
    Photoshop; one slot must not clobber the other's pending write."""
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        h.drain(1.2)
        h.thread.paused = True
        h.thread.write_color(11, 12, 13, color_index=0)
        h.thread.write_color(21, 22, 23, color_index=1)
        h.thread.paused = False

        assert _wait_until(
            h,
            lambda _: (
                ps.fg == {"r": 11, "g": 12, "b": 13, "index": 0}
                and ps.bg == {"r": 21, "g": 22, "b": 23, "index": 1}
            ),
            timeout=3.0,
        ), (ps.fg, ps.bg)
    finally:
        h.stop()


def test_ps_external_swap_updates_both_slots_despite_recent_write(qapp):
    """An external X-swap in Photoshop must refresh BOTH slots even when
    one slot has a fresh write timestamp — otherwise the suppressed slot
    keeps its stale swatch and both end up showing the same color.

    The 0.8 s assertion budget is deliberately smaller than the 1.5 s
    echo-suppression window: without the swap detection the foreground
    event can only arrive after the suppression expires (~1.2 s later),
    so the test would fail.
    """
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        # Settle the initial state (fg=5,5,5 / bg=255,255,255) so the
        # dedup caches are seeded, then drop the observation events.
        h.drain(1.2)
        h.events.clear()

        # User just wrote the foreground: seeds _last_write_ts[0].
        h.thread.write_color(99, 99, 99, color_index=0)
        h.drain(0.3)  # keep the write timestamp young (echo suppressed)

        # User presses X in Photoshop before the bridge applied the
        # write: cancel the pending apply so it cannot clobber the swap.
        ps._pending.clear()
        ps.fg = {"r": 255, "g": 255, "b": 255, "index": 0}
        ps.bg = {"r": 99, "g": 99, "b": 99, "index": 1}

        # Swap detection must clear the suppression: both slots refresh
        # well inside the 1.5 s echo-suppression window.
        assert _wait_until(
            h,
            lambda ev: "color(255,255,255,0)" in ev and "color(99,99,99,1)" in ev,
            timeout=0.8,
        ), h.events
    finally:
        h.stop()


def test_ps_bg_write_echo_suppressed(qapp):
    """The same suppression applies to the background slot."""
    ps = FakePs()
    h = _Harness(ps, qapp)
    h.start()
    try:
        h.drain(1.2)  # settle initial observations
        h.events.clear()
        h.thread.write_color(200, 30, 10, color_index=1)
        h.drain(2.5)
        assert all(not e.startswith("color(") for e in h.events), h.events
    finally:
        h.stop()


def test_ps_mode_switch_initial_palette_suppresses_stale_color(qapp):
    """Switching mode to 'ps' with an initial palette must write the palette
    and suppress reading back Photoshop's stale pre-switch colors (切到ps把颜色切回去)."""
    ps = FakePs()
    ps.fg = {"r": 1, "g": 2, "b": 3, "index": 0}
    ps.bg = {"r": 4, "g": 5, "b": 6, "index": 1}

    h = _Harness(ps, qapp)
    # Start thread in another mode, e.g. sai
    h.thread.software_mode = "sai"
    h.start()
    try:
        palette = {
            0: {"rgb": (255, 0, 100), "transparent": False},
            1: {"rgb": (0, 200, 255), "transparent": False},
            "active_slot": 0,
        }
        h.thread.set_software_mode("ps", initial_palette=palette)
        h.drain(2.0)

        # Stale colors (1,2,3) or (4,5,6) must never be emitted as external color changes
        stale_emitted = [e for e in h.events if "color(1,2,3" in e or "color(4,5,6" in e]
        assert stale_emitted == [], f"Stale pre-switch colors were emitted: {stale_emitted}"

        # Photoshop must have received the initial palette colors
        assert ps.fg["r"] == 255 and ps.fg["g"] == 0 and ps.fg["b"] == 100
        assert ps.bg["r"] == 0 and ps.bg["g"] == 200 and ps.bg["b"] == 255
    finally:
        h.stop()
