"""Write-path coverage for SAI2Sync's UI-refresh hook.

``set_color`` gained two responsibilities: read SAI's pre-write colour (the
evidence that identifies the stroke preview) and nudge SAI's widgets after a
successful write. Neither may weaken the write itself, so the memory
primitives are stubbed and the contract pinned:

* a refresh failure never turns a successful write into a failed one;
* a failed pre-write read never lets the write run on a dropped handle;
* nothing is nudged when the write itself failed.

Windows-only: ``core.sai2_brush_link`` binds kernel32 at import time.
"""

import sys

import pytest

if not sys.platform.startswith("win"):  # pragma: no cover - CI runs on Windows
    pytest.skip("sai2_brush_link requires the Windows API", allow_module_level=True)

from core import sai2_brush_link  # noqa: E402
from core.sai2_ui_refresh import MODE_FULL, MODE_OFF  # noqa: E402


class RecordingRefresher:
    """Stand-in for SAIUiRefresher that records what it was told."""

    def __init__(self, wants_previous=True, explode=False):
        self.mode = MODE_FULL
        self._wants_previous = wants_previous
        self._explode = explode
        self.calls = []
        self.resets = 0

    def wants_previous_color(self):
        return self._wants_previous

    def refresh(self, pid, rgb, force=False, previous=None):
        self.calls.append((pid, rgb, previous))
        if self._explode:
            raise OSError("nudge failed")
        return True

    def tick(self, _pid):
        return False

    def reset(self):
        self.resets += 1

    def set_mode(self, mode):
        self.mode = mode
        return True


@pytest.fixture
def sync(monkeypatch):
    """A connected-looking SAI2Sync with stubbed memory access."""
    s = sai2_brush_link.SAI2Sync()
    s._handle = 0x1234
    s._color_addr = 0x140321700
    s._pid = 4321

    state = {"bgr": bytes([69, 148, 51]), "writes": [], "write_ok": True}

    def fake_read(_handle, _address, size):
        return state["bgr"][:size]

    def fake_write(_handle, _address, data):
        if not state["write_ok"]:
            return False
        state["writes"].append(bytes(data))
        state["bgr"] = bytes(data)
        return True

    monkeypatch.setattr(sai2_brush_link, "_read_memory", fake_read)
    monkeypatch.setattr(sai2_brush_link, "_write_memory", fake_write)
    return s, state


def test_write_reports_success_and_passes_the_previous_colour(sync):
    s, state = sync
    s.ui_refresher = RecordingRefresher()

    assert s.set_color(10, 80, 220) is True
    assert state["writes"] == [bytes([220, 80, 10])]        # stored as B, G, R
    assert s.ui_refresher.calls == [(4321, (10, 80, 220), (51, 148, 69))]


def test_refresh_failure_does_not_fail_the_write(sync):
    s, state = sync
    s.ui_refresher = RecordingRefresher(explode=True)

    # The nudge raising must not propagate: the colour did reach SAI.
    with pytest.raises(OSError):
        s.ui_refresher.refresh(1, (1, 2, 3))              # sanity: it does raise
    assert s.set_color(10, 80, 220) is True
    assert state["writes"]


def test_no_nudge_when_the_write_failed(sync):
    s, state = sync
    state["write_ok"] = False
    s.ui_refresher = RecordingRefresher()

    assert s.set_color(10, 80, 220) is False
    assert s.ui_refresher.calls == []


def test_previous_colour_is_skipped_once_not_wanted(sync):
    s, _state = sync
    s.ui_refresher = RecordingRefresher(wants_previous=False)

    assert s.set_color(10, 80, 220) is True
    assert s.ui_refresher.calls == [(4321, (10, 80, 220), None)]


def test_failed_pre_read_aborts_instead_of_writing_through_a_dead_handle(
    sync, monkeypatch,
):
    s, state = sync
    s.ui_refresher = RecordingRefresher()
    # A read failure makes get_color drop the cached handle; reconnecting is
    # impossible in the test, so the write must not be attempted at all.
    monkeypatch.setattr(sai2_brush_link, "_read_memory", lambda *a, **k: None)
    monkeypatch.setattr(sai2_brush_link.SAI2Sync, "_connect", lambda _self: False)

    assert s.set_color(10, 80, 220) is False
    assert state["writes"] == []
    assert s.ui_refresher.calls == []


def test_cache_reset_also_resets_the_refresher(sync):
    s, _state = sync
    s.ui_refresher = RecordingRefresher()
    s._reset_cache(close_handle=False)
    assert s.ui_refresher.resets == 1


def test_set_ui_refresh_forwards_the_mode(sync):
    s, _state = sync
    s.ui_refresher = RecordingRefresher()
    s.set_ui_refresh(MODE_OFF)
    assert s.ui_refresher.mode == MODE_OFF


def test_tick_ui_refresh_needs_a_pid(sync):
    s, _state = sync
    s.ui_refresher = RecordingRefresher()
    s._pid = None
    assert s.tick_ui_refresh() is False
