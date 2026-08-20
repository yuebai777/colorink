#!/usr/bin/env python3

"""Policy coverage for the SAI UI refresher.

The window-system calls are stubbed, so these tests pin the *decisions*:
which control gets which nudge, when a click is throttled or deferred, and
when a mis-detected click target is abandoned. Geometry values come from a
live SAI Ver.2 measurement (swatch 49x49, stroke preview 191x50, sliders
159x24 / 180x24, buttons 20x20 / 22x22 — all logical px).
"""

import pytest

from core.sai2_ui_refresh import (
    CLICK_CYCLE,
    CLICK_SETTLE,
    MAX_PROBES,
    MAX_REFERENCES,
    RECENT_COLOURS,
    DEFAULT_MODE,
    MAX_CLICK_FAILURES,
    MODE_FULL,
    MODE_OFF,
    MODE_REPAINT,
    RESOLVE_RETRY_INTERVAL,
    Candidate,
    SAIUiRefresher,
    is_preview_strip,
    is_square_control,
    normalize_mode,
    pick_preview,
    pick_swatch,
)

# ── measured SAI layout, at 100% and at 150% scaling ─────────────────────
SWATCH = Candidate(0x150BF2, 49, 49)
PREVIEW = Candidate(0x191306, 191, 50)
SLIDER_WIDE = Candidate(0x8030C, 180, 24)
SLIDER_RGB = Candidate(0x44132E, 159, 24)
SLIDER_THIN = Candidate(0x1513DC, 100, 11)
BUTTON_22 = Candidate(0x1604DA, 22, 22)
BUTTON_20 = Candidate(0x131006, 20, 20)
LABEL = Candidate(0xD0F7E, 47, 15)
TOOL_ROW = Candidate(0x903D6, 125, 49)
BRUSH_GRID = Candidate(0x100F68, 174, 106)

SAI_PANEL = [
    TOOL_ROW, SWATCH, BRUSH_GRID, PREVIEW, SLIDER_WIDE, SLIDER_RGB,
    SLIDER_THIN, BUTTON_22, BUTTON_20, LABEL,
]

SWATCH_150 = Candidate(0x150BF2, 74, 74)
PREVIEW_150 = Candidate(0x191306, 293, 82)
BUTTON_150 = Candidate(0x1604DA, 33, 33)


# ── mode parsing ─────────────────────────────────────────────────────────
def test_normalize_mode_accepts_config_spellings():
    assert normalize_mode("full") == MODE_FULL
    assert normalize_mode("repaint") == MODE_REPAINT
    assert normalize_mode("off") == MODE_OFF
    assert normalize_mode("invalidate") == MODE_REPAINT
    assert normalize_mode(True) == MODE_FULL
    assert normalize_mode(False) == MODE_OFF


def test_normalize_mode_falls_back_to_default_for_junk():
    assert normalize_mode("") == DEFAULT_MODE
    assert normalize_mode(None) == DEFAULT_MODE
    assert normalize_mode("auto") == DEFAULT_MODE
    assert normalize_mode("banana") == DEFAULT_MODE


def test_default_mode_injects_no_input():
    # Clicking is real input to SAI and can leave a wedge on the next stroke,
    # so it must never be what an untouched install does.
    assert DEFAULT_MODE == MODE_REPAINT


# ── geometry classification ──────────────────────────────────────────────
def test_swatch_shape_accepts_square_controls_only():
    assert is_square_control(SWATCH)
    assert is_square_control(SWATCH_150)
    assert not is_square_control(PREVIEW)
    assert not is_square_control(SLIDER_RGB)
    assert not is_square_control(LABEL)


def test_swatch_selection_prefers_the_larger_filled_square():
    # 20x20 / 22x22 toolbar icons are square too, and an icon may even be
    # solidly painted in the same colour — size has to break the tie, since
    # an absolute pixel floor cannot (SAI is DPI-unaware).
    assert is_square_control(BUTTON_22)  # shape alone does not disqualify it
    fills = {SWATCH.hwnd: 0.30, BUTTON_22.hwnd: 1.0, BUTTON_20.hwnd: 1.0}
    hwnd, side = pick_swatch(SAI_PANEL, lambda h: fills.get(h, 0.0))
    assert hwnd == SWATCH.hwnd
    assert side == 49


def test_preview_strip_rejects_sliders_and_buttons():
    # Sliders are the dangerous false positive: clicking one would move a
    # brush parameter. They are barely half the swatch height.
    assert is_preview_strip(PREVIEW, swatch_side=49)
    assert not is_preview_strip(SLIDER_WIDE, swatch_side=49)
    assert not is_preview_strip(SLIDER_RGB, swatch_side=49)
    assert not is_preview_strip(SLIDER_THIN, swatch_side=49)
    assert not is_preview_strip(BUTTON_22, swatch_side=49)
    assert not is_preview_strip(LABEL, swatch_side=49)


def test_preview_strip_scales_with_the_swatch_yardstick():
    # SAI is DPI-unaware, so absolute sizes shift with the monitor scale;
    # anchoring on the measured swatch side keeps the rule valid.
    assert is_preview_strip(PREVIEW_150, swatch_side=74)
    assert not is_preview_strip(PREVIEW_150, swatch_side=49)
    assert not is_preview_strip(BUTTON_150, swatch_side=74)


def test_pick_swatch_uses_rendered_fill_not_size():
    fills = {SWATCH.hwnd: 0.57, BUTTON_22.hwnd: 0.99}
    # The 22x22 button is fully painted in the colour but is not swatch-shaped.
    hwnd, side = pick_swatch(SAI_PANEL, lambda h: fills.get(h, 0.0))
    assert hwnd == SWATCH.hwnd
    assert side == 49


def test_pick_swatch_returns_nothing_when_no_control_shows_the_colour():
    hwnd, side = pick_swatch(SAI_PANEL, lambda _h: 0.0)
    assert hwnd is None
    assert side == 0


OLD = (51, 148, 69)


def _fills(mapping):
    """fill_ratio stub: {hwnd: {colour: ratio}}."""
    return lambda hwnd, rgb: mapping.get(hwnd, {}).get(tuple(rgb), 0.0)


def test_pick_preview_picks_the_strip_showing_a_known_colour():
    fill = _fills({PREVIEW.hwnd: {OLD: 0.05}})
    assert pick_preview(SAI_PANEL, 49, fill, [OLD]) == PREVIEW.hwnd


def test_pick_preview_returns_none_when_the_strip_is_hidden():
    hidden = [c for c in SAI_PANEL if c.hwnd != PREVIEW.hwnd]
    fill = _fills({PREVIEW.hwnd: {OLD: 0.05}})
    assert pick_preview(hidden, 49, fill, [OLD]) is None


def test_pick_preview_never_picks_a_lookalike_that_shows_no_brush_colour():
    # Regression: on the measured build SAI's brush-tool row is 195x52 next
    # to the 191x50 preview — same height class, aspect 3.71 vs 3.78. It was
    # picked by shape alone, and clicking it switches the user's brush tool.
    # Only rendered colour evidence can separate the two.
    lookalike = Candidate(0x5A197E, 195, 52)
    panel = SAI_PANEL + [lookalike]
    fill = _fills({PREVIEW.hwnd: {OLD: 0.05}})   # the tool row shows nothing
    assert is_preview_strip(lookalike, swatch_side=49)  # shape cannot reject it
    assert pick_preview(panel, 49, fill, [OLD]) == PREVIEW.hwnd

    # ...and with no evidence at all, nothing is clicked.
    assert pick_preview(panel, 49, _fills({}), [OLD]) is None
    assert pick_preview(panel, 49, fill, []) is None


def test_pick_preview_accepts_evidence_from_any_recent_colour():
    written_earlier = (10, 80, 220)
    fill = _fills({PREVIEW.hwnd: {written_earlier: 0.05}})
    assert pick_preview(SAI_PANEL, 49, fill, [OLD, written_earlier]) == PREVIEW.hwnd


def test_pick_preview_ignores_a_trace_of_the_colour():
    # A couple of stray matching pixels must not qualify a control for a
    # synthetic click; the real sample stroke covers ~5% of the strip.
    trace = _fills({PREVIEW.hwnd: {OLD: 0.002}})
    assert pick_preview(SAI_PANEL, 49, trace, [OLD]) is None


def test_preview_rule_rejects_the_tool_row_next_to_the_swatch():
    assert not is_preview_strip(TOOL_ROW, swatch_side=49)


# ── stub backend ─────────────────────────────────────────────────────────
class FakeBackend:
    """Records nudges and lets a test dictate what SAI would render."""

    DEFAULT_FILLS = {SWATCH.hwnd: 0.57, PREVIEW.hwnd: 0.05}  # measured ratios

    def __init__(self, candidates=None, fills=None, busy=False, hung=False):
        self._candidates = list(candidates if candidates is not None else SAI_PANEL)
        # hwnd -> ratio (any colour) so most tests stay terse; the refresher
        # asks with a colour and gets the same answer back.
        self._fills = dict(self.DEFAULT_FILLS if fills is None else fills)
        self.busy = busy
        self.hung = hung
        self.invalidated = []
        self.clicked = []
        self.click_counts = []
        self.fill_queries = []
        self.dead = set()
        self.main = 0x1F1A1A

    # queries
    def is_window(self, hwnd):
        return bool(hwnd) and hwnd not in self.dead

    def is_hung(self, _hwnd):
        return self.hung

    def main_window(self, _pid):
        return self.main

    def candidates(self, _pid):
        return list(self._candidates)

    def fill_ratio(self, hwnd, rgb):
        self.fill_queries.append((hwnd, tuple(rgb)))
        value = self._fills.get(hwnd, 0.0)
        if isinstance(value, dict):        # per-colour rendering
            return value.get(tuple(rgb), 0.0)
        return value(rgb) if callable(value) else value

    def input_busy(self, _hwnd):
        return self.busy

    # actions
    def invalidate(self, hwnd):
        self.invalidated.append(hwnd)
        return self.is_window(hwnd)      # a dead handle fails, as on Windows

    def click(self, hwnd, times=1):
        self.clicked.append(hwnd)
        self.click_counts.append(times)
        return True


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_refresher(backend, mode=MODE_FULL, clock=None, min_interval=0.06):
    return SAIUiRefresher(
        backend=backend, mode=mode, min_interval=min_interval,
        clock=clock or FakeClock(),
    )


def prime(refresher, backend, clock, pid=4321, rgb=(10, 80, 220), previous=None):
    """Run one write + tick so the controls are resolved, as the app does.

    The clock is advanced past CLICK_SETTLE so the preview click is allowed.
    """
    refresher.refresh(pid, rgb, previous=previous or OLD)
    clock.advance(0.2)
    refresher.tick(pid)
    backend.invalidated.clear()
    backend.clicked.clear()
    backend.click_counts.clear()
    return refresher


# ── refresh behaviour: the write path is the fast lane ───────────────────
def test_write_path_never_blocks_on_discovery():
    # Discovery is a synchronous render round-trip into SAI (~50 ms per
    # candidate): the colour-write path must hand it to the tick instead.
    backend = FakeBackend()
    refresher = make_refresher(backend)
    assert refresher.refresh(4321, (10, 80, 220), previous=OLD) is False
    assert backend.fill_queries == []        # nothing rendered
    assert backend.invalidated == []
    assert refresher.status()["pending"] is True


def test_tick_resolves_then_invalidates_swatch_and_clicks_preview():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)   # queues discovery
    clock.advance(0.2)
    assert refresher.tick(4321) is True                    # slow lane acts
    assert backend.invalidated == [SWATCH.hwnd]
    assert backend.clicked == [PREVIEW.hwnd]


def test_preview_click_posts_a_whole_background_cycle():
    # Clicking the preview also advances its background (light -> pink ->
    # black -> light). Posting one full cycle re-renders the sample stroke and
    # leaves the background on the style the user had.
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    assert backend.click_counts == [CLICK_CYCLE]
    assert CLICK_CYCLE == 3        # measured on the live build


def test_repaint_mode_posts_no_clicks_at_all():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, mode=MODE_REPAINT, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    assert backend.click_counts == []


def test_once_resolved_the_write_path_acts_without_rendering():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)
    renders = len(backend.fill_queries)

    clock.advance(0.2)
    assert refresher.refresh(4321, (20, 90, 230), previous=OLD) is True
    assert backend.invalidated == [SWATCH.hwnd]
    assert len(backend.fill_queries) == renders   # no renders on the write path


def test_repaint_mode_never_posts_input():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, mode=MODE_REPAINT, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    assert refresher.tick(4321) is True
    assert backend.invalidated == [SWATCH.hwnd]
    assert backend.clicked == []


def test_off_mode_touches_nothing():
    backend = FakeBackend()
    refresher = make_refresher(backend, mode=MODE_OFF)
    assert refresher.refresh(4321, (10, 80, 220)) is False
    assert refresher.tick(4321) is False
    assert backend.invalidated == []
    assert backend.clicked == []
    assert refresher.enabled is False


def test_missing_pid_is_a_no_op():
    backend = FakeBackend()
    refresher = make_refresher(backend)
    assert refresher.refresh(0, (1, 2, 3)) is False
    assert backend.invalidated == []


def test_hung_sai_is_left_alone():
    backend = FakeBackend(hung=True)
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    assert refresher.tick(4321) is False
    assert backend.clicked == []
    # No offscreen render was attempted either — PrintWindow would block.
    assert backend.fill_queries == []


def test_resolution_is_cached_across_writes():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)
    renders = len(backend.fill_queries)

    for offset in range(3):
        clock.advance(0.2)
        refresher.refresh(4321, (10 + offset, 80, 220), previous=OLD)
    assert backend.invalidated == [SWATCH.hwnd] * 3
    assert len(backend.fill_queries) == renders   # discovery ran exactly once


def test_dead_control_handle_triggers_rediscovery():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)

    backend.dead.add(SWATCH.hwnd)          # panel closed / SAI relaunched
    clock.advance(0.2)
    assert refresher.refresh(4321, (10, 80, 220), previous=OLD) is False
    assert refresher.status()["swatch"] is None      # stale cache dropped
    assert refresher.status()["pending"] is True

    backend.dead.clear()                   # panel is back
    clock.advance(0.2)
    assert refresher.tick(4321) is True    # the tick re-resolves and acts
    assert backend.invalidated[-1] == SWATCH.hwnd


def test_failed_discovery_is_retried_only_after_a_backoff():
    backend = FakeBackend(fills={})  # nothing renders the colour
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)

    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    assert refresher.tick(4321) is False
    first_attempt = len(backend.fill_queries)
    assert first_attempt > 0

    clock.advance(RESOLVE_RETRY_INTERVAL / 2)
    refresher.tick(4321)
    assert len(backend.fill_queries) == first_attempt  # still backing off

    clock.advance(RESOLVE_RETRY_INTERVAL)
    refresher.tick(4321)
    assert len(backend.fill_queries) > first_attempt


def test_discovery_stays_within_its_probe_budget():
    # Every square control renders nothing useful, so discovery would probe
    # them all without a budget — at ~50 ms per render on the sync thread.
    many = [Candidate(0x1000 + i, 40 + i, 40 + i) for i in range(30)]
    backend = FakeBackend(candidates=many, fills={})
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    assert len(backend.fill_queries) <= MAX_PROBES


def test_preview_click_waits_for_the_colour_to_settle():
    # Mid-drag the swatch must still follow every write, but running the
    # background cycle 10x/s would flicker — so the click waits.
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)

    for _ in range(4):                      # a drag: writes closer than settle
        clock.advance(CLICK_SETTLE / 2)
        refresher.refresh(4321, (10, 80, 220), previous=OLD)
        refresher.tick(4321)
    assert backend.clicked == []            # no clicks while dragging
    assert backend.invalidated             # ...but the swatch kept up

    clock.advance(CLICK_SETTLE * 2)         # the user lets go
    assert refresher.tick(4321) is True
    assert backend.clicked == [PREVIEW.hwnd]
    assert backend.click_counts == [CLICK_CYCLE]


# ── throttling and the trailing tick ─────────────────────────────────────
def test_burst_writes_are_throttled_then_flushed_by_tick():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)

    clock.advance(0.2)
    assert refresher.refresh(4321, (10, 80, 220), previous=OLD) is True
    clock.advance(0.01)
    assert refresher.refresh(4321, (20, 90, 230), previous=OLD) is False  # coalesced
    assert backend.invalidated == [SWATCH.hwnd]

    clock.advance(0.2)
    assert refresher.tick(4321) is True
    assert backend.invalidated == [SWATCH.hwnd, SWATCH.hwnd]


def test_tick_is_a_no_op_without_pending_work():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)
    clock.advance(0.2)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(1.0)
    refresher.tick(4321)                     # settles any pending verification
    before = list(backend.invalidated)
    clock.advance(1.0)
    assert refresher.tick(4321) is False
    assert backend.invalidated == before


def test_click_is_deferred_while_the_user_is_mid_interaction():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)

    backend.busy = True                     # mouse captured: drawing or dragging
    clock.advance(0.2)
    assert refresher.refresh(4321, (10, 80, 220), previous=OLD) is True
    assert backend.invalidated == [SWATCH.hwnd]   # repaint still happens
    assert backend.clicked == []
    assert refresher.status()["pending"] is True

    backend.busy = False
    clock.advance(0.2)
    assert refresher.tick(4321) is True
    assert backend.clicked == [PREVIEW.hwnd]


# ── click verification ───────────────────────────────────────────────────
def test_unverified_click_target_is_abandoned_after_repeated_failures():
    # The target showed the old colour once (so discovery accepted it) but no
    # click ever makes the newly written colour appear: a mis-detection.
    backend = FakeBackend(fills={SWATCH.hwnd: 0.57, PREVIEW.hwnd: {OLD: 0.05}})
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)

    for _ in range(MAX_CLICK_FAILURES + 4):
        clock.advance(0.2)
        refresher.refresh(4321, (10, 80, 220), previous=OLD)
        clock.advance(0.2)
        refresher.tick(4321)

    assert len(backend.clicked) <= MAX_CLICK_FAILURES
    assert refresher.status()["preview"] is None
    # The harmless repaint keeps working after the click target is dropped.
    assert len(backend.invalidated) >= MAX_CLICK_FAILURES


def test_verified_click_target_stops_being_re_verified():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)

    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)                     # resolve + first click
    clock.advance(0.2)
    refresher.tick(4321)                     # verifies that click
    assert refresher.status()["clickVerified"] is True

    queries_after_verify = len(backend.fill_queries)
    clock.advance(0.2)
    refresher.refresh(4321, (30, 100, 240), previous=OLD)
    assert len(backend.fill_queries) == queries_after_verify  # no more renders

    # The write itself only repaints the swatch; the click follows once the
    # colour has settled, and needs no further verification render.
    clock.advance(CLICK_SETTLE * 2)
    refresher.tick(4321)
    assert len(backend.fill_queries) == queries_after_verify
    assert backend.clicked == [PREVIEW.hwnd] * 3


def test_previous_colour_is_only_requested_until_the_target_is_confirmed():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    assert refresher.wants_previous_color() is True

    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    clock.advance(0.2)
    refresher.tick(4321)                     # verification lands here
    assert refresher.status()["clickVerified"] is True
    # No more 3-byte pre-write reads once the target is known good.
    assert refresher.wants_previous_color() is False


def test_repaint_mode_does_not_need_the_previous_colour():
    refresher = make_refresher(FakeBackend(), mode=MODE_REPAINT)
    assert refresher.wants_previous_color() is False


def test_written_colours_are_remembered_as_later_evidence():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)
    for index in range(RECENT_COLOURS + 4):
        clock.advance(0.2)
        refresher.refresh(4321, (index, 80, 220), previous=OLD)
    # Bounded history, so a long session cannot grow the evidence list.
    assert refresher.status()["knownColours"] == RECENT_COLOURS


def test_refresh_survives_a_backend_explosion():
    class Exploding(FakeBackend):
        def invalidate(self, hwnd):
            raise OSError("window died mid-refresh")

    backend = Exploding()
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    # A UI nudge must never be able to fail a colour write.
    assert refresher.tick(4321) is False


def test_set_mode_reports_changes():
    refresher = make_refresher(FakeBackend())
    assert refresher.set_mode(MODE_REPAINT) is True
    assert refresher.set_mode("invalidate") is False  # same mode, other spelling
    assert refresher.mode == MODE_REPAINT


def test_switching_from_repaint_to_full_starts_clicking():
    # A resolution made in repaint mode has no click target; switching to full
    # must re-run discovery instead of staying click-less forever.
    backend = FakeBackend()
    clock = FakeClock()
    refresher = make_refresher(backend, mode=MODE_REPAINT, clock=clock)
    prime(refresher, backend, clock)
    assert backend.clicked == []

    refresher.set_mode(MODE_FULL)
    clock.advance(0.2)
    refresher.refresh(4321, (20, 90, 230), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    assert backend.clicked == [PREVIEW.hwnd]


def test_canvas_sized_squares_are_never_rendered():
    # Bounding the candidate size keeps discovery off the sync thread's back:
    # a 630x630 view would otherwise be rendered and scanned pixel by pixel.
    big = Candidate(0xABCDEF, 630, 630)
    assert is_square_control(big) is False
    backend = FakeBackend(candidates=SAI_PANEL + [big])
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    refresher.refresh(4321, (10, 80, 220), previous=OLD)
    clock.advance(0.2)
    refresher.tick(4321)
    assert all(hwnd != big.hwnd for hwnd, _rgb in backend.fill_queries)


def test_discovery_probes_a_bounded_number_of_colours():
    backend = FakeBackend(
        candidates=[SWATCH, PREVIEW],
        fills={SWATCH.hwnd: 0.57},          # the strip matches nothing
    )
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    for index in range(RECENT_COLOURS + 2):
        clock.advance(RESOLVE_RETRY_INTERVAL + 0.1)
        refresher.refresh(4321, (index, 80, 220), previous=OLD)
        refresher.tick(4321)

    strip_probes = sum(
        1 for hwnd, _rgb in backend.fill_queries if hwnd == PREVIEW.hwnd
    )
    per_pass = MAX_REFERENCES        # at most this many colours per pass
    assert strip_probes <= per_pass * (RECENT_COLOURS + 2)


def test_reset_forgets_resolved_controls():
    backend = FakeBackend()
    clock = FakeClock()
    refresher = prime(make_refresher(backend, clock=clock), backend, clock)
    assert refresher.status()["swatch"] is not None
    refresher.reset()
    assert refresher.status()["swatch"] is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
