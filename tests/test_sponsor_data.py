"""Data-layer tests for the sponsor honour roll (``core.sponsors``).

Deliberately Qt-free: the parsing/whitelist/sort rules are plain stdlib, so
they can be exercised without a QApplication and without a screen.
"""

import json
from pathlib import Path

import pytest

from core import sponsors


@pytest.fixture
def write_data(tmp_path, monkeypatch):
    """Point the loader at a throwaway file and return a writer for it."""
    path = tmp_path / "sponsors.json"
    monkeypatch.setattr(sponsors, "_data_path", lambda: path)

    def _write(payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    return _write


# ── happy path ──────────────────────────────────────────────────────────


def test_parses_entries(write_data):
    write_data({
        "schema": 1,
        "sponsors": [
            {"name": "张三", "since": "2026-03", "last_pay": "2026-08",
             "message": "画笔很顺手", "avatar": ""},
            {"name": "李四"},
        ],
    })

    result = sponsors.load_sponsors()

    assert len(result) == 2
    assert result[0].name == "张三"
    # Month precision is padded to the 1st on the way in, so the sort key and
    # the displayed label always agree (see ``_date``).
    assert result[0].since == "2026-03-01"
    assert result[0].last_pay == "2026-08-01"
    assert result[0].message == "画笔很顺手"
    assert result[0].date_label == "2026-08-01"
    assert result[1].name == "李四"


def test_name_is_stripped(write_data):
    write_data({"sponsors": [{"name": "  张三  "}]})

    assert sponsors.load_sponsors()[0].name == "张三"


def test_load_preserves_file_order(write_data):
    """``load_sponsors`` is the raw read; ordering is ``sorted_sponsors``' job."""
    write_data({"sponsors": [
        {"name": "先", "last_pay": "2026-01"},
        {"name": "后", "last_pay": "2026-08"},
    ]})

    assert [s.name for s in sponsors.load_sponsors()] == ["先", "后"]


def test_minimal_entry_is_kept(write_data):
    """``name`` is the only required field — the least-typing contract."""
    write_data({"sponsors": [{"name": "张三"}]})

    (sponsor,) = sponsors.load_sponsors()

    assert sponsor.name == "张三"
    assert sponsor.message == ""
    assert sponsor.since == ""
    assert sponsor.last_pay == ""
    assert sponsor.date_label == ""


# ── degrade to empty, never raise ───────────────────────────────────────


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sponsors, "_data_path", lambda: tmp_path / "nope.json")

    assert sponsors.load_sponsors() == []


def test_corrupt_json_returns_empty(write_data, tmp_path, monkeypatch):
    path = write_data({"sponsors": []})
    path.write_text("{ not json", encoding="utf-8")

    assert sponsors.load_sponsors() == []


@pytest.mark.parametrize("payload", [
    None,
    [],
    "a string",
    42,
    {"sponsors": None},
    {"sponsors": {"name": "张三"}},
    {"sponsors": "张三"},
    {},
])
def test_malformed_payload_returns_empty(write_data, payload):
    write_data(payload)

    assert sponsors.load_sponsors() == []


def test_empty_list_returns_empty(write_data):
    write_data({"schema": 1, "sponsors": []})

    assert sponsors.load_sponsors() == []


# ── whitelist / privacy ─────────────────────────────────────────────────


def test_whitelist_drops_private_fields(write_data):
    """Anything outside the whitelist must not even reach memory."""
    write_data({"sponsors": [{
        "name": "张三",
        "since": "2026-03",
        "email": "someone@example.com",
        "out_trade_no": "2026091012345",
        "amount": 88.0,
        "tier": "honor",
    }]})

    (sponsor,) = sponsors.load_sponsors()

    for forbidden in ("email", "out_trade_no", "amount", "tier"):
        assert not hasattr(sponsor, forbidden), forbidden


def test_non_string_values_are_dropped(write_data):
    write_data({"sponsors": [{
        "name": "张三",
        "since": 202603,
        "message": ["not", "a", "string"],
        "last_pay": None,
    }]})

    (sponsor,) = sponsors.load_sponsors()

    assert sponsor.since == ""
    assert sponsor.message == ""
    assert sponsor.last_pay == ""


def test_blank_or_missing_name_is_skipped(write_data):
    write_data({"sponsors": [
        {"name": "", "message": "空名字"},
        {"name": "   "},
        {"message": "没名字"},
        {"name": 123},
        {"name": "李四"},
    ]})

    result = sponsors.load_sponsors()

    assert [s.name for s in result] == ["李四"]


def test_non_dict_entries_are_skipped(write_data):
    write_data({"sponsors": ["张三", None, 7, {"name": "李四"}]})

    assert [s.name for s in sponsors.load_sponsors()] == ["李四"]


# ── de-duplication ──────────────────────────────────────────────────────
#
# The data file is hand-edited by design, so the same person twice is a typo
# that will happen. Two rows and "2 sponsors in total" for one person is a
# visible lie on a public thank-you page, so the loader collapses them.


def test_duplicate_nicknames_collapse_to_one_row(write_data):
    write_data({"sponsors": [
        {"name": "张三", "last_pay": "2026-08-01"},
        {"name": "张三", "last_pay": "2026-01-01"},
    ]})

    (only,) = sponsors.load_sponsors()

    assert only.name == "张三"
    assert only.last_pay == "2026-08-01", "the most recent support should win"


def test_dedup_keeps_each_distinct_nickname(write_data):
    write_data({"sponsors": [
        {"name": "张三", "last_pay": "2026-08-01"},
        {"name": "李四", "last_pay": "2026-07-01"},
        {"name": "张三", "last_pay": "2026-01-01"},
    ]})

    assert [s.name for s in sponsors.load_sponsors()] == ["张三", "李四"]


def test_dedup_prefers_the_entry_carrying_more_information(write_data):
    """A duplicate that arrives later but knows the avatar must not lose it."""
    write_data({"sponsors": [
        {"name": "张三", "last_pay": "2026-08-01"},
        {"name": "张三", "last_pay": "2026-08-01", "avatar": "avatars/x.jpg",
         "message": "画笔很顺手"},
    ]})

    (only,) = sponsors.load_sponsors()

    assert only.avatar == "avatars/x.jpg"
    assert only.message == "画笔很顺手"


def test_dedup_is_whitespace_insensitive(write_data):
    """``"  张三  "`` and ``"张三"`` are the same person after stripping."""
    write_data({"sponsors": [
        {"name": "  张三  ", "last_pay": "2026-08-01"},
        {"name": "张三", "last_pay": "2026-01-01"},
    ]})

    assert len(sponsors.load_sponsors()) == 1


def test_dedup_survives_the_sort(write_data):
    write_data({"sponsors": [
        {"name": "张三", "last_pay": "2026-01-01"},
        {"name": "李四", "last_pay": "2026-09-01"},
        {"name": "张三", "last_pay": "2026-08-01"},
    ]})

    assert [s.name for s in sponsors.sorted_sponsors()] == ["李四", "张三"]


def test_dedup_keeps_the_newest_date_from_either_position(write_data):
    """Order in the file is incidental; the newest date must win either way."""
    write_data({"sponsors": [
        {"name": "张三", "last_pay": "2026-01-01"},
        {"name": "张三", "last_pay": "2026-08-01"},
    ]})

    assert sponsors.load_sponsors()[0].last_pay == "2026-08-01"


# ── ordering ────────────────────────────────────────────────────────────


def test_sorted_newest_first_with_since_fallback(write_data):
    write_data({"sponsors": [
        {"name": "旧", "last_pay": "2026-01-05"},
        {"name": "用since", "since": "2026-05-02"},
        {"name": "新", "last_pay": "2026-08-30"},
        {"name": "无日期", "message": "hi"},
    ]})

    assert [s.name for s in sponsors.sorted_sponsors()] == [
        "新", "用since", "旧", "无日期",
    ]


def test_sorted_same_month_orders_by_day(write_data):
    """Day precision must actually affect the order, not just the label."""
    write_data({"sponsors": [
        {"name": "月末", "last_pay": "2026-08-31"},
        {"name": "月中", "last_pay": "2026-08-15"},
        {"name": "月初", "last_pay": "2026-08-01"},
    ]})

    assert [s.name for s in sponsors.sorted_sponsors()] == [
        "月末", "月中", "月初",
    ]


def test_sorted_prefers_last_pay_over_since(write_data):
    write_data({"sponsors": [
        {"name": "since更新", "since": "2026-09-01", "last_pay": "2026-02-20"},
        {"name": "last_pay更新", "last_pay": "2026-07-07"},
    ]})

    assert [s.name for s in sponsors.sorted_sponsors()] == [
        "last_pay更新", "since更新",
    ]


def test_sorted_tolerates_exotic_date_strings(write_data):
    """Garbage dates must not crash the sort, nor jump to the top of the roll."""
    write_data({"sponsors": [
        {"name": "怪日期", "last_pay": "not-a-date"},
        {"name": "月份越界", "last_pay": "2026-13-01"},
        {"name": "日子越界", "last_pay": "2026-08-32"},
        {"name": "正常", "last_pay": "2026-08-15"},
        {"name": "空日期"},
    ]})

    assert [s.name for s in sponsors.sorted_sponsors()] == [
        "正常", "怪日期", "月份越界", "日子越界", "空日期",
    ]


@pytest.mark.parametrize(("raw", "expected"), [
    # the shapes people actually hand-type
    ("2026-8-31", "2026-08-31"),
    ("2026-08-05", "2026-08-05"),
    (" 2026-12-01 ", "2026-12-01"),
    # legacy month-precision data keeps working (padded to the 1st)
    ("2026-8", "2026-08-01"),
    ("2026-08", "2026-08-01"),
    # calendar nonsense collapses to empty
    ("2026-13-01", ""),          # month out of range
    ("2026-0-10", ""),
    ("2026-08-32", ""),          # day out of range
    ("2026-08-0", ""),
    ("2026-02-30", ""),
    ("2023-02-29", ""),          # 2023 is not a leap year
    ("2024-02-29", "2024-02-29"),  # …but 2024 is
    ("not-a-date", ""),
    ("2026", ""),
    ("20260831", ""),
    ("", ""),
])
def test_date_normalisation(write_data, raw, expected):
    write_data({"sponsors": [{"name": "张三", "last_pay": raw}]})

    assert sponsors.load_sponsors()[0].last_pay == expected


def test_normalised_date_is_what_gets_displayed(write_data):
    write_data({"sponsors": [{"name": "张三", "since": "2026-8-31"}]})

    assert sponsors.load_sponsors()[0].date_label == "2026-08-31"


def test_sorted_on_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sponsors, "_data_path", lambda: tmp_path / "nope.json")

    assert sponsors.sorted_sponsors() == []


# ── bundled-path resolution ─────────────────────────────────────────────
#
# A wrong path here is invisible in development and shows up only as a
# permanently empty honour roll in the packaged build, so pin it down.


def test_source_mode_path_points_at_the_repo_file():
    path = sponsors._data_path()

    assert path == Path(sponsors.__file__).resolve().parents[1] / "core/data/sponsors.json"
    assert path.is_file(), "the committed data file is missing"


def test_frozen_mode_path_prefers_meipass(tmp_path, monkeypatch):
    meipass = tmp_path / "_internal"
    meipass.mkdir()
    monkeypatch.setattr(sponsors.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sponsors.sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sponsors.sys, "executable", str(tmp_path / "Colorink.exe"), raising=False)

    assert sponsors._data_path() == meipass / "core" / "data" / "sponsors.json"


def test_frozen_mode_falls_back_to_exe_dir(tmp_path, monkeypatch):
    """onedir layouts have differed on where ``_MEIPASS`` points; probe both."""
    bundled = tmp_path / "exe" / "core" / "data" / "sponsors.json"
    bundled.parent.mkdir(parents=True)
    bundled.write_text('{"sponsors": []}', encoding="utf-8")

    monkeypatch.setattr(sponsors.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sponsors.sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)
    monkeypatch.setattr(sponsors.sys, "executable", str(tmp_path / "exe" / "Colorink.exe"), raising=False)

    assert sponsors._data_path() == bundled


def test_shipped_file_is_loadable():
    """A committed-but-broken JSON would silently blank the honour roll."""
    monkeyed = sponsors._data_path()
    assert monkeyed.is_file()
    result = sponsors.load_sponsors()
    assert isinstance(result, list)
    for sponsor in result:
        assert sponsor.name


# ── avatar resolution ───────────────────────────────────────────────────


def test_resolve_avatar_finds_a_committed_image(write_data):
    path = write_data({"sponsors": []})
    avatar = path.parent / "avatars" / "miya.jpg"
    avatar.parent.mkdir()
    avatar.write_bytes(b"not really a jpeg, but a file")

    assert sponsors.resolve_avatar("avatars/miya.jpg") == avatar.resolve()


@pytest.mark.parametrize("value", ["", "   ", None, 42, "avatars/nope.jpg"])
def test_resolve_avatar_returns_none_when_unusable(write_data, value):
    write_data({"sponsors": []})

    assert sponsors.resolve_avatar(value) is None


@pytest.mark.parametrize("value", [
    "../outside.jpg",
    "avatars/../../outside.jpg",
    "../../../../Windows/win.ini",
])
def test_resolve_avatar_rejects_paths_escaping_the_data_dir(write_data, value):
    """The data file is public and hand-edited — a stray ../ must not read out."""
    path = write_data({"sponsors": []})
    (path.parent.parent / "outside.jpg").write_bytes(b"x")

    assert sponsors.resolve_avatar(value) is None


def test_resolve_avatar_rejects_absolute_paths(write_data, tmp_path):
    """On Windows ``base / 'C:/x'`` replaces base entirely; the guard must catch it."""
    write_data({"sponsors": []})
    outside = tmp_path.parent / "absolute.jpg"
    outside.write_bytes(b"x")

    assert sponsors.resolve_avatar(str(outside)) is None


def test_shipped_avatars_all_resolve():
    """A reference to a missing image would silently degrade to initials."""
    for sponsor in sponsors.load_sponsors():
        if sponsor.avatar:
            assert sponsors.resolve_avatar(sponsor.avatar) is not None, (
                f"sponsor {sponsor.name!r} points at missing {sponsor.avatar!r}"
            )
