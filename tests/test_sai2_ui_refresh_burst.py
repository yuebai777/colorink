#!/usr/bin/env python3

"""Unit tests for the drag-vs-pick debounce in :mod:`core.sai2_ui_refresh`.

A single pick must click the stroke preview immediately (measured 154 ms ->
~50 ms); only an actual drag keeps the CLICK_SETTLE debounce so the preview's
three-state background cycle is not run continuously.
"""

from __future__ import annotations

import pytest

from core import sai2_ui_refresh as r


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _refresher(now: float = 1000.0):
    clock = _Clock(now)
    return r.SAIUiRefresher(clock=clock), clock


# ── _write_burst ──────────────────────────────────────────────────────────
def test_single_pick_is_not_a_burst():
    refresher, _ = _refresher()
    refresher._write_times.append(1000.0)
    assert refresher._write_burst() is False


def test_three_writes_in_window_is_a_burst():
    refresher, _ = _refresher()
    refresher._write_times.extend([999.6, 999.8, 1000.0])
    assert refresher._write_burst() is True


def test_writes_outside_the_window_do_not_count():
    refresher, _ = _refresher()
    # two old writes + one fresh one -> still a single pick
    refresher._write_times.extend([998.0, 998.2, 1000.0])
    assert refresher._write_burst() is False


def test_window_boundary_is_inclusive():
    refresher, _ = _refresher()
    refresher._write_times.extend([1000.0 - r.BURST_WINDOW, 999.9, 1000.0])
    assert refresher._write_burst() is True


def test_burst_shrinks_as_time_passes():
    refresher, clock = _refresher()
    refresher._write_times.extend([999.6, 999.8, 1000.0])
    assert refresher._write_burst() is True
    clock.now = 1000.0 + r.BURST_WINDOW + 0.01
    assert refresher._write_burst() is False


def test_history_is_bounded():
    refresher, _ = _refresher()
    for i in range(50):
        refresher._write_times.append(1000.0 - i)
    assert len(refresher._write_times) <= 8


# ── _click_preview gating ─────────────────────────────────────────────────
class _Backend:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    def input_busy(self, main: int) -> bool:
        return False

    def click(self, hwnd: int, times: int = 1) -> bool:
        self.clicks.append((hwnd, times))
        return True


def _resolved():
    return r._Resolved(pid=1, main=0x100, swatch=0x200, swatch_side=74,
                       preview=0x300, click_verified=True)


def _preview_click(refresher, backend, rgb=(10, 20, 30)):
    refresher._get_backend = lambda: backend
    return refresher._click_preview(_resolved(), rgb)


def test_single_pick_clicks_immediately_within_settle():
    """One write, then a click 10 ms later: no debounce, the click goes out."""
    refresher, clock = _refresher()
    backend = _Backend()
    refresher._last_write = clock.now
    refresher._write_times.append(clock.now)      # single pick
    clock.now += 0.01                             # well inside CLICK_SETTLE
    assert _preview_click(refresher, backend) == r.CLICK_SENT
    assert backend.clicks == [(0x300, r.CLICK_CYCLE)]


def test_drag_defers_the_click():
    """Three writes inside the window still debounce."""
    refresher, clock = _refresher()
    backend = _Backend()
    refresher._last_write = clock.now
    refresher._write_times.extend([clock.now - 0.2, clock.now - 0.1, clock.now])
    clock.now += 0.01
    assert _preview_click(refresher, backend) == r.CLICK_DEFERRED
    assert backend.clicks == []


def test_drag_clicks_after_settle():
    """Once the drag settles the click is allowed again."""
    refresher, clock = _refresher()
    backend = _Backend()
    refresher._last_write = clock.now
    refresher._write_times.extend([clock.now - 0.2, clock.now - 0.1, clock.now])
    clock.now += r.CLICK_SETTLE + 0.01
    assert _preview_click(refresher, backend) == r.CLICK_SENT
    assert len(backend.clicks) == 1


def test_unverified_target_still_defers_without_probe():
    """The safety rule for unconfirmed controls is unchanged."""
    refresher, clock = _refresher()
    backend = _Backend()
    resolved = r._Resolved(pid=1, main=0x100, swatch=0x200, preview=0x300,
                           click_verified=False)
    refresher._get_backend = lambda: backend
    assert refresher._click_preview(resolved, (1, 2, 3)) == r.CLICK_DEFERRED
    assert backend.clicks == []
