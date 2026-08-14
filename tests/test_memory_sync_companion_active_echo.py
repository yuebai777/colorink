"""Companion-mode active-slot read-back echo suppression tests.

CSP's Companion Mode (TCP) applies SetCurrentColor asynchronously, so
the CurrentColorIndex we read back lags behind local fg/bg clicks.
Without suppression, active_slot_changed fires on the echo and
on_external_active_slot_changed yanks the swatch highlight back to the
stale slot during rapid switching.

These tests verify that the echo is suppressed while a write settles, but a
genuine external change (e.g. pressing X in CSP) still propagates.
"""

import time

import pytest
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread

APPLY_LATENCY = 0.15  # simulated companion apply latency in seconds


class FakeCompanion:
    """Mimics the CSPCompanionSync surface with an async (latent) apply."""

    def __init__(self) -> None:
        self._connected = True
        self.active_index = 0
        self.color = {"r": 5, "g": 5, "b": 5}
        self._pending: dict[int, tuple] = {}  # color_index -> (rgb, apply_at)

    def set_color(self, r, g, b, hsv_u32=None, transparent=False, color_index=0):
        self._pending[color_index] = ((r, g, b), time.time() + APPLY_LATENCY)
        return True

    def get_color_hsv(self):
        for idx, (rgb, at) in list(self._pending.items()):
            if time.time() >= at:
                self._pending.pop(idx, None)
                self.color = {"r": rgb[0], "g": rgb[1], "b": rgb[2]}
                self.active_index = idx
        c = dict(self.color)
        c.update({
            "h": 0.0, "s": 0.0, "v": 0.0,
            "transparent": False,
            "index": self.active_index,
        })
        return c

    def status(self):
        return {"connected": True}


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _Harness:
    """Runs a MemorySyncThread in companion mode with a fake backend."""

    def __init__(self, companion: FakeCompanion, qapp) -> None:
        self.qapp = qapp
        self.thread = MemorySyncThread()
        self.thread.software_mode = "companion"
        setattr(self.thread, "companion_sync", companion)
        self.events: list[str] = []

        def on_active(i):
            self.events.append(f"active({i})")

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


def test_companion_active_slot_echo_is_suppressed(qapp):
    """A write to a slot must not emit active_slot_changed for its own
    read-back echo: no highlight bounce back to the stale slot."""
    companion = FakeCompanion()
    h = _Harness(companion, qapp)
    h.start()
    try:
        # Settle the initial observation (active(0)), then clear it.
        h.drain(1.2)
        h.events.clear()

        h.thread.write_color(200, 30, 10, color_index=1)
        # Drain past the apply point; the echo would fire active(1) within
        # ~1s without suppression.
        h.drain(2.0)
        assert "active(1)" not in h.events, h.events
    finally:
        h.stop()


def test_companion_rapid_switch_suppresses_both_echoes(qapp):
    """Rapid fg/bg writes queue two slots; neither read-back echo may emit,
    so the local slot selection stays authoritative throughout."""
    companion = FakeCompanion()
    h = _Harness(companion, qapp)
    h.start()
    try:
        h.drain(1.2)
        h.events.clear()

        h.thread.write_color(200, 30, 10, color_index=1)
        h.thread.write_color(10, 200, 30, color_index=0)
        h.drain(2.5)
        assert not any(e.startswith("active(") for e in h.events), h.events
    finally:
        h.stop()


def test_companion_genuine_external_active_change_propagates(qapp):
    """An external slot change (X in CSP) with no local write still emits."""
    companion = FakeCompanion()
    h = _Harness(companion, qapp)
    h.start()
    try:
        h.drain(1.2)
        h.events.clear()

        # External change: CSP reports the sub slot is now active.
        companion.active_index = 1
        assert _wait_until(h, lambda ev: "active(1)" in ev), h.events
    finally:
        h.stop()


def _wait_until(harness, pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(harness.events):
            return True
        harness.qapp.processEvents()
        time.sleep(0.02)
    return False
