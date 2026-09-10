"""Symmetric click swallowing + hook-drain wiring (Wintab first-stroke fix).

picker_hook.c used to swallow the mouse DOWN that confirms a pick but let the
paired UP through.  Photoshop receives that orphan UP and its mouse/stylus
state machine desyncs, so the next stroke starts without pressure.  The hook
now counts owed UPs (``pending()``), swallows them even after the picker
stopped, and only unhooks once drained (``maintenance()`` reports it).

These tests lock in both halves: the native exports exist and are inert before
a pick, and the overlay drives the drain without ever force-unhooking a live
pick.
"""

import ctypes
import os

import pytest
from PyQt6.QtWidgets import QApplication

import ui.color_picker_overlay as cpo
from ui.color_picker_overlay import ColorPickerOverlay

_DLL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "picker_hook.dll",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class FakeHookDll:
    """Minimal stand-in for picker_hook.dll recording the teardown calls."""

    def __init__(self, owed=0, still_hooked=1):
        self.owed = owed
        self.still_hooked = still_hooked
        self.uninstalls = 0
        self.force_uninstalls = 0
        self.maintenance_calls = 0

    def uninstall(self):
        self.uninstalls += 1

    def uninstall_force(self):
        self.force_uninstalls += 1

    def pending(self):
        return self.owed

    def maintenance(self):
        self.maintenance_calls += 1
        return self.still_hooked


@pytest.fixture()
def fake_hook(monkeypatch):
    fake = FakeHookDll()
    monkeypatch.setattr(cpo, "_hook_dll", fake)
    monkeypatch.setattr(cpo, "_hook_has_drain", True)
    return fake


def test_native_dll_exposes_symmetric_swallow_api():
    """The shipped DLL must export the drain API (a stale build must not be
    silently trusted — the overlay probes for exactly these symbols)."""
    assert os.path.isfile(_DLL_PATH), "core/picker_hook.dll is missing"
    dll = ctypes.CDLL(_DLL_PATH)
    for name in ("install", "uninstall", "uninstall_force", "pending",
                 "maintenance", "left_clicked", "right_clicked",
                 "get_wheel_delta"):
        assert hasattr(dll, name), f"picker_hook.dll is missing export {name}"


def test_dll_is_idle_when_no_hook_is_installed():
    """Without an installed hook nothing is owed and maintenance() is a no-op."""
    dll = ctypes.CDLL(_DLL_PATH)
    dll.pending.restype = ctypes.c_int
    dll.maintenance.restype = ctypes.c_int
    assert dll.pending() == 0
    assert dll.maintenance() == 0


def test_release_hook_starts_drain_when_an_up_is_owed(qapp, fake_hook):
    fake_hook.owed = 1
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        assert fake_hook.uninstalls == 1          # stop intercepting new clicks
        assert overlay._drain_timer.isActive()    # but stay installed for the UP
    finally:
        overlay._drain_timer.stop()


def test_release_hook_unhooks_immediately_when_nothing_is_owed(qapp, fake_hook):
    fake_hook.owed = 0
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        assert fake_hook.uninstalls == 1
        assert not overlay._drain_timer.isActive()
    finally:
        overlay._drain_timer.stop()


def test_drain_stops_itself_once_the_dll_unhooked(qapp, fake_hook):
    fake_hook.owed = 1
    fake_hook.still_hooked = 0
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        assert overlay._drain_timer.isActive()
        overlay._drain_hook()
        assert fake_hook.maintenance_calls == 1
        assert not overlay._drain_timer.isActive()
        assert fake_hook.force_uninstalls == 0
    finally:
        overlay._drain_timer.stop()


def test_drain_never_force_unhooks_a_live_pick(qapp, fake_hook):
    """A new pick re-arms the very same hook — the drain tick must stand down."""
    fake_hook.owed = 1
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        overlay._active = True
        overlay._drain_hook()
        assert not overlay._drain_timer.isActive()
        assert fake_hook.force_uninstalls == 0
    finally:
        overlay._active = False
        overlay._drain_timer.stop()


def test_drain_gives_up_after_the_deadline(qapp, fake_hook):
    """A never-delivered UP must not leave a hook installed forever."""
    fake_hook.owed = 1
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        overlay._drain_deadline = 0.0  # pretend the window already elapsed
        overlay._drain_hook()
        assert fake_hook.force_uninstalls == 1
        assert not overlay._drain_timer.isActive()
    finally:
        overlay._drain_timer.stop()


def test_legacy_dll_without_drain_api_still_works(qapp, monkeypatch):
    """A stale picker_hook.dll must not break the overlay: without the drain
    exports it falls back to the historical immediate-unhook behaviour."""
    legacy = FakeHookDll(owed=0)
    monkeypatch.setattr(cpo, "_hook_dll", legacy)
    monkeypatch.setattr(cpo, "_hook_has_drain", False)
    overlay = ColorPickerOverlay(None)
    try:
        overlay._release_hook()
        assert legacy.uninstalls == 1
        assert not overlay._drain_timer.isActive()
    finally:
        overlay._drain_timer.stop()
