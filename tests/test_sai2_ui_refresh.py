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
    is_preview_band,
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


def test_default_mode_is_the_single_full_refresh_mode():
    # The app ships exactly one mode: full (swatch repaint + stroke-preview
    # click). It used to default to repaint (repaint-only), which silently
    # let the cached stroke-preview bitmap drift away from the colour slot;
    # the user-facing selector was removed, so this default is the only
    # configuration the app produces.
    assert DEFAULT_MODE == MODE_FULL


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


# ── preview re-discovery after a swatch-only resolution ──────────────────
#
# Regression: discovery can resolve the swatch but miss the stroke preview
# when the preview's cached bitmap shows a colour that is not in the current
# evidence set (typical after the preview drifted while the app was in a
# repaint-only state). The refresher cached that partial result forever, so
# even switching to full never re-ran preview discovery: the preview stayed
# frozen for the whole session. Discovery must re-run periodically, because
# the evidence set grows with every write and SAI itself re-renders the
# preview cache whenever its colour changes inside SAI.


def _re_render_on_click(fills):
    """SAI whose preview re-renders the slot colour when clicked (measured)."""

    class ReRenderBackend(FakeBackend):
        def __init__(self, **kwargs):
            super().__init__(fills=fills, **kwargs)
            self.slot = None

        def click(self, hwnd, times=1):
            ok = super().click(hwnd, times)
            if ok and hwnd == PREVIEW.hwnd and self.slot is not None:
                self._fills[PREVIEW.hwnd] = {tuple(self.slot): 0.05}
            return ok

    return ReRenderBackend()


def test_swatch_only_resolution_rediscovers_the_preview_once_evidence_fits():
    frozen = (7, 7, 7)                      # the strip cache's drifted colour
    backend = _re_render_on_click(
        fills={SWATCH.hwnd: 0.57, PREVIEW.hwnd: {frozen: 0.05}})
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321

    # Cold phase: writes whose evidence never includes the frozen colour —
    # every discovery resolves only the swatch and caches that partial state.
    previous = (200, 200, 200)
    for index in range(3):
        rgb = (index + 10, 100, 100)
        backend.slot = rgb
        refresher.refresh(pid, rgb, previous=previous)
        clock.advance(0.2)
        refresher.tick(pid)
        clock.advance(RESOLVE_RETRY_INTERVAL + 0.2)
        refresher.tick(pid)
        previous = rgb
    assert refresher.status()["preview"] is None

    # Heal phase: SAI-side colour activity moves the slot to the frozen
    # colour, so the next write's previous colour matches the strip cache.
    # The periodic re-discovery must pick the strip up from here on.
    heal = (30, 200, 200)
    backend.slot = heal
    refresher.refresh(pid, heal, previous=frozen)
    clock.advance(RESOLVE_RETRY_INTERVAL + 0.2)
    refresher.tick(pid)
    assert refresher.status()["preview"] == f"0x{PREVIEW.hwnd:X}"

    # From here the normal full-mode flow resumes: the write path never
    # clicks an unverified target, the tick probes once, and the click is
    # verified on the following colour change.
    rgb2 = (40, 210, 210)
    backend.slot = rgb2
    clock.advance(0.2)
    refresher.refresh(pid, rgb2, previous=heal)
    assert backend.clicked == []
    clock.advance(CLICK_SETTLE * 2)
    refresher.tick(pid)
    assert backend.clicked[-1] == PREVIEW.hwnd

    rgb3 = (50, 50, 50)
    backend.slot = rgb3
    clock.advance(0.2)
    refresher.refresh(pid, rgb3, previous=rgb2)
    clock.advance(0.2)
    refresher.tick(pid)
    assert refresher.status()["clickVerified"] is True


def test_dropped_preview_target_backs_off_rediscovery():
    # A target that never re-renders (mis-detection) is dropped after
    # MAX_CLICK_FAILURES; the periodic re-discovery must then respect the
    # give-up backoff instead of re-clicking the same dead target forever.
    backend = FakeBackend(
        fills={SWATCH.hwnd: 0.57, PREVIEW.hwnd: {OLD: 0.05}})
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321
    for _ in range(MAX_CLICK_FAILURES + 4):
        clock.advance(0.2)
        refresher.refresh(pid, (10, 80, 220), previous=OLD)
        clock.advance(0.2)
        refresher.tick(pid)
    assert refresher.status()["preview"] is None
    clicks_after_drop = len(backend.clicked)

    for _ in range(4):                     # ~32s of poll ticks
        clock.advance(RESOLVE_RETRY_INTERVAL + 0.1)
        refresher.tick(pid)
    assert len(backend.clicked) == clicks_after_drop
    assert refresher.status()["preview"] is None


# ── poll observations as discovery evidence ──────────────────────────────
#
# The stroke preview's cached bitmap is re-rendered by SAI itself whenever
# SAI's colour changes inside SAI (picker / eyedropper), and by our click
# afterwards. In both cases the cache colour equals a colour the slot has
# *held*, which the sai-mode poll reads every 100 ms. Feeding those reads
# back as evidence makes discovery independent of the user re-picking an
# arbitrary old colour by chance.


def test_note_colour_deduplicates_consecutive_poll_reads():
    refresher = make_refresher(FakeBackend(), mode=MODE_FULL)
    refresher.note_colour((9, 9, 9))
    refresher.note_colour((9, 9, 9))       # the poll repeats the same colour
    refresher.note_colour((1, 2, 3))
    assert refresher.status()["knownColours"] == 2


def test_external_colour_change_arms_immediate_preview_rediscovery():
    # When SAI's own colour changes, its preview cache is re-rendered in that
    # exact colour. The poll spots the slot change and hands it over; the
    # rediscovery must run on the very next tick — waiting out the 8 s
    # backoff would let later Colorink writes push the matching colour out of
    # the evidence window.
    external = (90, 7, 7)
    backend = FakeBackend(
        fills={SWATCH.hwnd: 0.57, PREVIEW.hwnd: {external: 0.05}})
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321
    for index in range(3):                 # cold writes: preview unfindable
        refresher.refresh(pid, (index + 10, 200, 200), previous=(9, 9, 9))
        clock.advance(0.2)
        refresher.tick(pid)
        clock.advance(RESOLVE_RETRY_INTERVAL + 0.2)
        refresher.tick(pid)
    assert refresher.status()["preview"] is None

    refresher.on_external_colour(external)  # poll spotted an SAI-side change
    clock.advance(0.1)
    refresher.tick(pid)                     # no backoff wait this time
    assert refresher.status()["preview"] == f"0x{PREVIEW.hwnd:X}"


# ── probe ordering: colours sweep every strip before the budget runs out ──
#
# On the live build the brush-tool row is LARGER than the stroke preview
# (293x79 vs 287x76 at 150% DPI), so a strip-major probe order spends the
# whole render budget on the tool row and the preview only ever sees the
# first one or two reference colours — a matching colour later in the list
# is never reached. Colours must sweep across all strips first.


def test_preview_probing_sweeps_each_colour_across_all_strips():
    # Live build: the brush-tool row is LARGER than the stroke preview
    # (293x79 vs 287x76 at 150% DPI), so strip-major probing burns the whole
    # render budget on the empty decoys and the preview only ever sees the
    # first one or two reference colours. Colours must sweep across every
    # strip. (Swatch and strips are all at the 150%-DPI scale.)
    TOOL_ROW_150 = Candidate(0x555001, 293, 79)   # biggest decoy, shows nothing
    OTHER_150 = Candidate(0x555002, 262, 73)      # second decoy, shows nothing
    PREVIEW_150 = Candidate(0x555003, 287, 76)
    cache_colour = (70, 190, 30)                  # shown only by the preview
    candidates = [SWATCH_150, TOOL_ROW_150, OTHER_150, PREVIEW_150]
    fills = {
        SWATCH_150.hwnd: 0.57,
        TOOL_ROW_150.hwnd: {},
        OTHER_150.hwnd: {},
        PREVIEW_150.hwnd: {cache_colour: 0.05},
    }
    backend = FakeBackend(candidates=candidates, fills=fills)
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321

    # Cold writes: the cache colour is not in evidence yet -> swatch only.
    refresher.refresh(pid, (10, 80, 220), previous=(1, 1, 1))
    clock.advance(0.2)
    refresher.tick(pid)
    assert refresher.status()["preview"] is None

    # Evidence accumulates so the cache colour sits DEEP (behind a window):
    # note the cache colour first, then two newer ones, then write a fresh
    # colour. Rotating discovery windows reach it within a few passes.
    refresher.note_colour(cache_colour)
    refresher.note_colour((31, 31, 31))
    refresher.note_colour((32, 32, 32))
    clock.advance(0.2)
    refresher.refresh(pid, (99, 99, 99), previous=(1, 1, 1))
    clock.advance(RESOLVE_RETRY_INTERVAL + 0.2)
    refresher.tick(pid)
    # The first rotated window still misses the buried colour...
    assert refresher.status()["preview"] is None
    # ...but the next window reaches it, and a colour-major sweep then finds
    # the preview on the very strip that shows it.
    clock.advance(RESOLVE_RETRY_INTERVAL + 0.2)
    refresher.tick(pid)
    assert refresher.status()["preview"] == f"0x{PREVIEW_150.hwnd:X}"


def _bgra_pixels(width, height, paint):
    """Build a BGRA byte buffer; paint(x, y) -> (r, g, b)."""
    buf = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            red, green, blue = paint(x, y)
            i = (y * width + x) * 4
            buf[i] = blue
            buf[i + 1] = green
            buf[i + 2] = red
            buf[i + 3] = 255
    return bytes(buf)


def _flat(rgb):
    return lambda x, y: rgb


# ── content classifier (last-resort preview identification) ──────────────
def test_classifier_accepts_a_sample_band_on_a_plain_background():
    w, h = 287, 76
    def paint(x, y):
        if 30 <= x <= 260 and 20 <= y <= 36:      # one wide horizontal band
            return (200, 40, 60)
        return (243, 243, 243)                    # light background
    assert is_preview_band(w, h, _bgra_pixels(w, h, paint))


def test_classifier_rejects_many_small_glyphs_like_the_tool_row():
    w, h = 293, 79
    def paint(x, y):
        for gx in range(0, 6):                    # six scattered 15x15 icons
            x0, y0 = 20 + gx * 48, 12
            if x0 <= x < x0 + 15 and y0 <= y < y0 + 15:
                return (30, 30, 30)
        return (248, 248, 248)
    assert not is_preview_band(w, h, _bgra_pixels(w, h, paint))


def test_classifier_rejects_full_height_flat_strips():
    # A flat light fill spanning the strip height (material / preview rows)
    # must never qualify: no band shape, no saturation.
    w, h = 262, 73
    def paint(x, y):
        if 10 <= x <= 250 and 10 <= y <= 68:
            return (219, 219, 255)
        return (255, 255, 255)
    assert not is_preview_band(w, h, _bgra_pixels(w, h, paint))


def test_classifier_is_conservative_on_plain_and_empty_controls():
    w, h = 200, 60
    assert not is_preview_band(w, h, _bgra_pixels(w, h, _flat((243, 243, 243))))
    assert not is_preview_band(0, 0, b"")


# ── refresher content fallback ───────────────────────────────────────────
def test_content_probe_recovers_a_preview_that_no_colour_evidence_matches():
    class ContentBackend(FakeBackend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.content_calls = []
            self.slot = None

        def content_probe(self, hwnd):
            self.content_calls.append(hwnd)
            return hwnd == PREVIEW.hwnd

        def click(self, hwnd, times=1):
            ok = super().click(hwnd, times)
            # SAI re-renders the sample from the slot when clicked.
            if ok and hwnd == PREVIEW.hwnd and self.slot is not None:
                self._fills[PREVIEW.hwnd] = {tuple(self.slot): 0.05}
            return ok

    backend = ContentBackend(
        candidates=[SWATCH, PREVIEW],
        fills={SWATCH.hwnd: 0.57, PREVIEW.hwnd: {}},   # no colour ever matches
    )
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321

    refresher.refresh(pid, (10, 80, 220), previous=(9, 9, 9))
    clock.advance(0.2)
    refresher.tick(pid)                       # swatch-only resolution
    assert refresher.status()["preview"] is None

    # Colour passes keep failing for a while; after the content delay the
    # classifier must identify the preview and the normal click+verify flow
    # must then confirm it (colour evidence appears once the click re-renders).
    for index in range(60):
        rgb = (20 + index, 80, 220)
        backend.slot = rgb
        refresher.refresh(pid, rgb, previous=(9, 9, 9))
        clock.advance(0.3)                    # the poll tick comes later
        refresher.tick(pid)
        clock.advance(1.7)
        if refresher.status()["clickVerified"] is True:
            break

    assert PREVIEW.hwnd in backend.content_calls
    assert refresher.status()["preview"] == f"0x{PREVIEW.hwnd:X}"
    assert refresher.status()["clickVerified"] is True


def test_a_misidentified_content_target_is_dropped_and_not_reprobed():
    # A content probe can land on the wrong strip; verification drops it and
    # the same control must not be content-probed again in this epoch.
    WRONG_STRIP = Candidate(0x777001, 195, 52)   # strip-shaped look-alike

    class WrongContentBackend(FakeBackend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.content_calls = []

        def content_probe(self, hwnd):
            self.content_calls.append(hwnd)
            return hwnd == WRONG_STRIP.hwnd      # the classifier is wrong here

    backend = WrongContentBackend(
        candidates=[SWATCH, WRONG_STRIP, PREVIEW],
        fills={SWATCH.hwnd: 0.57, WRONG_STRIP.hwnd: {}, PREVIEW.hwnd: {}},
    )
    clock = FakeClock()
    refresher = make_refresher(backend, clock=clock)
    pid = 4321
    refresher.refresh(pid, (10, 80, 220), previous=(9, 9, 9))
    clock.advance(0.2)
    refresher.tick(pid)
    for index in range(60):
        refresher.refresh(pid, (20 + index, 80, 220), previous=(9, 9, 9))
        clock.advance(0.3)                    # the poll tick comes later
        refresher.tick(pid)
        clock.advance(1.7)

    # Dropped after verification failures: never click-verified, no preview.
    assert refresher.status()["preview"] is None
    assert refresher.status()["clickVerified"] is False
    # The wrong control was content-probed exactly once (never repeated).
    assert backend.content_calls.count(WRONG_STRIP.hwnd) == 1
    assert len(backend.clicked) <= MAX_CLICK_FAILURES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
