"""Crash-report marker: record + detect + clear previous-run exceptions."""

import json

from core import crash_report


def test_write_and_read_round_trip(tmp_path):
    data = crash_report.write_crash_marker(
        "Traceback (most recent call last): boom",
        user_data_dir=str(tmp_path),
        log_path="C:/colorink/stderr.log",
    )

    assert crash_report.read_crash_marker(str(tmp_path)) == data
    assert data["traceback"].startswith("Traceback")
    assert data["log_path"] == "C:/colorink/stderr.log"
    assert "timestamp" in data


def test_read_returns_none_when_missing(tmp_path):
    assert crash_report.read_crash_marker(str(tmp_path)) is None


def test_read_returns_none_on_corrupt(tmp_path):
    (tmp_path / crash_report.CRASH_MARKER_NAME).write_text("{not json", encoding="utf-8")
    assert crash_report.read_crash_marker(str(tmp_path)) is None


def test_read_returns_none_on_wrong_shape(tmp_path):
    (tmp_path / crash_report.CRASH_MARKER_NAME).write_text(
        json.dumps({"hello": "world"}), encoding="utf-8"
    )
    assert crash_report.read_crash_marker(str(tmp_path)) is None


def test_clear_removes_marker(tmp_path):
    crash_report.write_crash_marker("boom", user_data_dir=str(tmp_path))
    crash_report.clear_crash_marker(str(tmp_path))
    assert crash_report.read_crash_marker(str(tmp_path)) is None


def test_clear_missing_marker_is_noop(tmp_path):
    crash_report.clear_crash_marker(str(tmp_path))  # must not raise


def test_detect_previous_crash_missing(tmp_path):
    assert crash_report.detect_previous_crash(str(tmp_path)) is None


def test_detect_previous_crash_fresh_and_stale(tmp_path):
    crash_report.write_crash_marker("boom", user_data_dir=str(tmp_path), timestamp=1000.0)

    # Within the window → reported.
    assert crash_report.detect_previous_crash(
        str(tmp_path), now=1000.0, max_age_seconds=10
    ) is not None
    assert crash_report.detect_previous_crash(
        str(tmp_path), now=1009.0, max_age_seconds=10
    ) is not None

    # Beyond the window → treated as stale, do not nag.
    assert crash_report.detect_previous_crash(
        str(tmp_path), now=1011.0, max_age_seconds=10
    ) is None


def test_detect_previous_crash_malformed_timestamp_not_dropped(tmp_path):
    (tmp_path / crash_report.CRASH_MARKER_NAME).write_text(
        json.dumps({"timestamp": "not-a-number", "traceback": "x"}), encoding="utf-8"
    )
    assert crash_report.detect_previous_crash(str(tmp_path)) is not None
