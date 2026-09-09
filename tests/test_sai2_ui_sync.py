#!/usr/bin/env python3

"""Unit tests for :mod:`core.sai2_ui_sync` (no SAI instance required).

Covers the pure logic that was measured against a live SAI Ver.2:
mode formulas, grey/hue-undefined handling, mode detection, field packing,
and the "never raise" contract of ``SAI2UiSync.sync``.
"""

from __future__ import annotations

import colorsys
import struct

import pytest

from core import sai2_ui_sync as ui


# ── normalize_mode ────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    (None, "auto"),
    ("", "auto"),
    ("auto", "auto"),
    ("AUTO", "auto"),
    ("vhsv", "vhsv"),
    ("VHSV", "vhsv"),
    ("hsv", "hsv"),
    ("hsl", "hsl"),
    ("hls", "hsl"),          # colorink spells it HLS
    ("HLS", "hsl"),
    (True, "auto"),
    (False, "auto"),
    ("nonsense", "auto"),
    (3, "auto"),
])
def test_normalize_mode(value, expected):
    assert ui.normalize_mode(value) == expected


# ── grey detection ────────────────────────────────────────────────────────
@pytest.mark.parametrize("rgb,expected", [
    ((0, 0, 0), True),
    ((255, 255, 255), True),
    ((128, 128, 128), True),
    ((1, 1, 1), True),
    ((255, 0, 0), False),
    ((0, 0, 1), False),
    ((10, 10, 11), False),
])
def test_is_grey(rgb, expected):
    assert ui.is_grey(rgb) is expected


# ── fields_for: HSV ───────────────────────────────────────────────────────
def test_fields_hsv_primaries():
    assert ui.fields_for("hsv", (255, 0, 0)) == pytest.approx((1.0, 1.0, 0.0))
    assert ui.fields_for("hsv", (0, 255, 0)) == pytest.approx((1.0, 1.0, 2.0))
    assert ui.fields_for("hsv", (0, 0, 255)) == pytest.approx((1.0, 1.0, 4.0))
    assert ui.fields_for("hsv", (0, 0, 0)) == pytest.approx((0.0, 0.0, 0.0))
    assert ui.fields_for("hsv", (255, 255, 255)) == pytest.approx((1.0, 0.0, 0.0))


def test_fields_hsv_matches_colorsys():
    for rgb in ((128, 64, 32), (37, 189, 222), (204, 98, 46), (10, 240, 130)):
        r, g, b = [c / 255.0 for c in rgb]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        assert ui.fields_for("hsv", rgb) == pytest.approx((v, s, h * 6.0))


# ── fields_for: HSL (measured: field1 = L, field2 = HSL saturation) ───────
def test_fields_hsl_measured_samples():
    # (128,61,27): L=(128+27)/510=0.303922, S=101/155=0.651613  (live-measured)
    assert ui.fields_for("hsl", (128, 61, 27)) == pytest.approx(
        (0.303922, 0.651613, 0.336634), abs=1e-6)
    # (255,128,128): L=0.750980, S=1.0
    assert ui.fields_for("hsl", (255, 128, 128)) == pytest.approx(
        (0.750980, 1.0, 0.0), abs=1e-6)
    # red: L=0.5
    assert ui.fields_for("hsl", (255, 0, 0)) == pytest.approx((0.5, 1.0, 0.0))


def test_fields_hsl_matches_colorsys():
    for rgb in ((37, 189, 222), (204, 98, 46), (128, 0, 128), (0, 128, 255)):
        r, g, b = [c / 255.0 for c in rgb]
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        assert ui.fields_for("hsl", rgb) == pytest.approx((l, s, h * 6.0))


# ── fields_for: VHSV uses colorink's SAI2-exact implementation ────────────
def test_fields_vhsv_measured_samples():
    # live-measured SAI fields for VHSV mode: s=58.80818/100, v=50.19608/100
    assert ui.fields_for("vhsv", (128, 0, 0)) == pytest.approx(
        (0.5019608, 0.5880818, 0.0), abs=1e-6)
    assert ui.fields_for("vhsv", (255, 0, 0)) == pytest.approx((1.0, 1.0, 0.0))
    assert ui.fields_for("vhsv", (255, 255, 255)) == pytest.approx((1.0, 0.0, 0.0))


def test_fields_vhsv_agrees_with_color_conversions():
    from ui import color_conversions as cc
    for rgb in ((128, 0, 0), (61, 0, 0), (255, 61, 61), (195, 95, 95), (12, 200, 90)):
        h, s, v = cc.rgb_to_vhsv(*rgb)
        assert ui.fields_for("vhsv", rgb) == pytest.approx((v / 100.0, s / 100.0, h / 60.0))


def test_fields_unknown_mode_returns_none():
    assert ui.fields_for("lab", (10, 20, 30)) is None
    assert ui.fields_for("", (10, 20, 30)) is None


def test_fields_clamps_out_of_range():
    assert ui.fields_for("hsv", (300, -5, 0)) == pytest.approx(
        ui.fields_for("hsv", (255, 0, 0)))


# ── detect_mode_from ──────────────────────────────────────────────────────
def test_detect_mode_hsl_sample():
    slot = (128, 61, 27)
    assert ui.detect_mode_from(slot, ui.fields_for("hsl", slot)) == "hsl"


def test_detect_mode_hsv_sample():
    slot = (37, 189, 222)
    assert ui.detect_mode_from(slot, ui.fields_for("hsv", slot)) == "hsv"


def test_detect_mode_vhsv_sample():
    slot = (128, 0, 0)
    assert ui.detect_mode_from(slot, ui.fields_for("vhsv", slot)) == "vhsv"


def test_detect_mode_grey_returns_none():
    assert ui.detect_mode_from((128, 128, 128), (0.5, 0.0, 1.0)) is None
    assert ui.detect_mode_from((0, 0, 0), (0.0, 0.0, 0.0)) is None


def test_detect_mode_no_match_returns_none():
    assert ui.detect_mode_from((255, 0, 0), (9.9, 9.9, 9.9)) is None


def test_detect_mode_missing_input_returns_none():
    assert ui.detect_mode_from(None, (0.1, 0.2, 0.3)) is None
    assert ui.detect_mode_from((255, 0, 0), None) is None
    assert ui.detect_mode_from((255, 0, 0), (0.1, 0.2)) is None


# ── pack_field ────────────────────────────────────────────────────────────
def test_pack_field_roundtrip():
    for value in (0.0, 1.0, 0.5, 2.5, 5.999, -0.0):
        assert struct.unpack("<f", ui.pack_field(value))[0] == pytest.approx(value)


# ── SAI2UiSync: never raises, fails quietly ───────────────────────────────
class _FakeSync:
    """Stand-in for a disconnected/failed SAI2Sync."""

    _handle = None
    _pid = None
    _base = None
    _color_addr = None


def test_sync_returns_false_without_connection():
    s = ui.SAI2UiSync("hsv")
    assert s.sync(_FakeSync(), (255, 0, 0)) is False


def test_sync_returns_false_for_bad_rgb():
    s = ui.SAI2UiSync("hsv")
    assert s.sync(_FakeSync(), (1, 2)) is False
    assert s.sync(_FakeSync(), ("x", "y", "z")) is False


def test_fail_streak_disables_after_limit():
    s = ui.SAI2UiSync("hsv")
    s.max_fail_streak = 2
    for _ in range(2):
        assert s.sync(_FakeSync(), (255, 0, 0)) is False
    assert s.fail_streak >= 2
    # once the limit is reached it short-circuits without touching SAI
    assert s.sync(_FakeSync(), (255, 0, 0)) is False


def test_reset_clears_failure_state():
    s = ui.SAI2UiSync("hsv")
    s.sync(_FakeSync(), (255, 0, 0))
    assert s.fail_streak > 0
    s.reset()
    assert s.fail_streak == 0


def test_set_mode_changes_and_reports():
    s = ui.SAI2UiSync("auto")
    assert s.set_mode("hsl") is True
    assert s.mode == "hsl"
    assert s.set_mode("hsl") is False
    assert s.set_mode("HLS") is False          # alias resolves to hsl
    assert s.set_mode("vhsv") is True
    assert s.mode == "vhsv"


# ── grey equivalence: why an unknown mode is harmless ─────────────────────
@pytest.mark.parametrize("grey", [(0, 0, 0), (64, 64, 64), (128, 128, 128), (255, 255, 255)])
def test_grey_fields_identical_across_modes(grey):
    """Grey has no hue, and all three modes store the same first two fields.

    This is what makes the "assume vhsv until we can detect" fallback safe:
    for a grey colour every mode writes the same values.
    """
    values = [ui.fields_for(mode, grey) for mode in ("vhsv", "hsv", "hsl")]
    assert all(v is not None for v in values)
    first = values[0]
    for other in values[1:]:
        assert other[0] == pytest.approx(first[0])
        assert other[1] == pytest.approx(first[1])


# ── observe(): throttling and source priority ─────────────────────────────
def test_observe_throttles_repeated_detection(monkeypatch):
    s = ui.SAI2UiSync("auto")
    calls = []
    monkeypatch.setattr(ui, "detect_mode_by_labels",
                        lambda pid: calls.append(pid) or "hsl")
    fake = _FakeSync()
    fake._pid = 4242
    assert s.observe(fake) == "hsl"
    s.observe(fake)
    s.observe(fake)
    assert len(calls) == 1                     # cached, no window walk
    s._last_observe = 0.0                      # pretend the interval elapsed
    assert s.observe(fake) == "hsl"
    assert len(calls) == 2


def test_observe_prefers_labels_over_fields(monkeypatch):
    """Labels (drawn by SAI) beat the fields, which we may have written ourselves."""
    s = ui.SAI2UiSync("auto")
    monkeypatch.setattr(ui, "detect_mode_by_labels", lambda pid: "hsl")
    fake = _FakeSync()
    fake._pid = 7
    monkeypatch.setattr(ui.SAI2UiSync, "_read_slot",
                        staticmethod(lambda sync: (128, 0, 0)))
    monkeypatch.setattr(ui.SAI2UiSync, "_read_fields",
                        staticmethod(lambda sync: ui.fields_for("vhsv", (128, 0, 0))))
    assert s.observe(fake) == "hsl"


def test_observe_uses_fields_when_labels_are_ambiguous(monkeypatch):
    s = ui.SAI2UiSync("auto")
    monkeypatch.setattr(ui, "detect_mode_by_labels", lambda pid: "hsv_or_vhsv")
    fake = _FakeSync()
    fake._pid = 8
    monkeypatch.setattr(ui.SAI2UiSync, "_read_slot",
                        staticmethod(lambda sync: (128, 0, 0)))
    monkeypatch.setattr(ui.SAI2UiSync, "_read_fields",
                        staticmethod(lambda sync: ui.fields_for("vhsv", (128, 0, 0))))
    assert s.observe(fake) == "vhsv"


def test_resolve_mode_falls_back_to_vhsv():
    s = ui.SAI2UiSync("auto")
    assert s._resolve_mode(_FakeSync()) == "vhsv"


# ── live panel-mode switching (SAI rewrites the fields) ───────────────────
def test_external_field_change_bypasses_throttle(monkeypatch):
    """A live mode switch must be noticed on the very next colour write."""
    s = ui.SAI2UiSync("auto")
    calls = []
    monkeypatch.setattr(ui, "detect_mode_by_labels",
                        lambda pid: calls.append(pid) or "hsl")
    fake = _FakeSync()
    fake._pid = 99
    assert s.observe(fake) == "hsl"          # first detection
    s._last_written = (0.5, 1.0, 0.0)        # what we last wrote
    monkeypatch.setattr(ui.SAI2UiSync, "_read_fields",
                        staticmethod(lambda sync: (0.3, 0.6, 0.0)))
    assert s.observe(fake) == "hsl"          # re-detected despite the throttle
    assert len(calls) == 2


def test_unchanged_fields_keep_throttle(monkeypatch):
    s = ui.SAI2UiSync("auto")
    calls = []
    monkeypatch.setattr(ui, "detect_mode_by_labels",
                        lambda pid: calls.append(pid) or "hsl")
    fake = _FakeSync()
    fake._pid = 100
    assert s.observe(fake) == "hsl"
    s._last_written = (0.5, 1.0, 0.0)
    monkeypatch.setattr(ui.SAI2UiSync, "_read_fields",
                        staticmethod(lambda sync: (0.5, 1.0, 0.0)))
    s.observe(fake)
    s.observe(fake)
    assert len(calls) == 1                   # nothing changed -> cached


def test_fields_changed_externally_without_history():
    """No baseline yet -> not treated as an external change."""
    s = ui.SAI2UiSync("auto")
    assert s._fields_changed_externally(_FakeSync()) is False


# ── wiring: SAI2Sync exposes the picker synchroniser ──────────────────────
def test_sai2_sync_panel_mode_wiring():
    from core import sai2_brush_link

    s = sai2_brush_link.SAI2Sync()
    assert s.panel_mode == "auto"
    assert s.set_panel_mode("HSL") is True
    assert s.panel_mode == "hsl"
    assert s.set_panel_mode("hsl") is False
    picker = s.ui_picker()
    assert picker is not None
    assert picker.mode == "hsl"
    assert s.set_panel_mode("vhsv") is True
    assert picker.mode == "vhsv"


def test_sai2_sync_ui_picker_is_cached():
    from core import sai2_brush_link

    s = sai2_brush_link.SAI2Sync()
    assert s.ui_picker() is s.ui_picker()
