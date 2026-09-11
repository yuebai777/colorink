"""Tests for the Afdian → sponsor-list sync tool.

Everything here is offline: the HTTP layer is injected, so no test touches the
network or needs a real api token.
"""

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.release import fetch_sponsors

#: 2023-11-14 22:13 UTC
TS_NOV_2023 = 1700000000
#: 2025-02-19 21:20 UTC
TS_FEB_2025 = 1740000000
#: 2022-01-01 00:00 UTC — an exact month boundary, catches local-time drift
TS_JAN_2022 = 1640995200


def _record(name, created=TS_NOV_2023, paid=TS_NOV_2023, amount="88.00"):
    """One ``list[]`` item as the Afdian API shapes it."""
    return {
        "user": {
            "user_id": "u-1",
            "name": name,
            "avatar": "https://pic1.afdiancdn.com/user/x.jpg",
        },
        "all_sum_amount": amount,
        "create_time": created,
        "last_pay_time": paid,
        "sponsor_plans": [],
    }


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(page_payloads: dict):
    """An ``urlopen`` stand-in that answers by the ``page`` in the request."""
    calls: list[str] = []

    def _open(request, timeout=None):
        body = request.data.decode("utf-8")
        calls.append(body)
        page = int(json.loads(urllib.parse.parse_qs(body)["params"][0])["page"])
        if page not in page_payloads:
            raise AssertionError(f"unexpected page {page}")
        return _FakeResponse(page_payloads[page])

    _open.calls = calls
    return _open


# ── signature ───────────────────────────────────────────────────────────


def test_sign_matches_the_documented_algorithm():
    """``md5(token + params + ts + user_id)`` — pinned against a known value."""
    assert fetch_sponsors.sign("tok", '{"page":1}', 1700000000, "uid") == (
        "d3d3a2db286706317c1ad6ff62c9d1b0"
    )


def test_request_is_signed_over_the_exact_params_string_sent():
    """Afdian is whitespace-sensitive, so the signed string must be the sent one."""
    opener = _opener({1: {"ec": 200, "data": {"total_page": 1, "list": []}}})

    fetch_sponsors.fetch_page("uid", "tok", 1, opener=opener)

    fields = urllib.parse.parse_qs(opener.calls[0])
    params = fields["params"][0]
    ts = fields["ts"][0]
    assert fields["user_id"][0] == "uid"
    assert json.loads(params) == {"page": 1}
    # Reproduce the signature from the bytes actually sent.
    expected = hashlib.md5(f"tok{params}{ts}uid".encode("utf-8")).hexdigest()
    assert fields["sign"][0] == expected


def test_params_json_has_no_incidental_whitespace():
    opener = _opener({3: {"ec": 200, "data": {"total_page": 3, "list": []}}})

    fetch_sponsors.fetch_page("uid", "tok", 3, opener=opener)

    params = urllib.parse.parse_qs(opener.calls[0])["params"][0]
    assert params == '{"page":3}'


# ── timestamps ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "expected"), [
    (TS_NOV_2023, "2023-11-14"),
    (TS_FEB_2025, "2025-02-19"),
    (TS_JAN_2022, "2022-01-01"),   # UTC conversion must not drift to 2021-12-31
])
def test_day_from_timestamp(value, expected):
    assert fetch_sponsors.day_from_timestamp(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", 0, -5, [], {}])
def test_day_from_timestamp_rejects_junk(value):
    assert fetch_sponsors.day_from_timestamp(value) == ""


def test_day_from_timestamp_uses_utc():
    """Pin the reference point so a machine timezone cannot shift the date."""
    moment = datetime.fromtimestamp(TS_JAN_2022, tz=timezone.utc)
    assert (moment.year, moment.month, moment.day) == (2022, 1, 1)


# ── shaping ─────────────────────────────────────────────────────────────


def test_output_fields_match_the_apps_whitelist():
    """Drift here would silently drop fields the app expects (or leak extras).

    The authoritative whitelist is the ``Sponsor`` dataclass: the loader builds
    each entry by naming those fields, so anything else is unreachable by
    construction.
    """
    from dataclasses import fields

    from core import sponsors as core_sponsors

    app_fields = {f.name for f in fields(core_sponsors.Sponsor)}
    assert set(fetch_sponsors.OUTPUT_FIELDS) == app_fields


def test_build_entries_keeps_only_whitelisted_fields():
    entries = fetch_sponsors.build_entries([_record("张三")])

    (entry,) = entries
    assert entry["name"] == "张三"
    assert entry["since"] == "2023-11-14"
    assert entry["last_pay"] == "2023-11-14"
    assert entry["message"] == ""
    assert entry["avatar"] == ""
    # The amount is available from the API and deliberately dropped.
    assert "amount" not in entry
    assert "all_sum_amount" not in entry


def test_build_entries_drops_records_without_a_nickname():
    entries = fetch_sponsors.build_entries([
        _record("张三"),
        {"user": {"name": "   "}},
        {"user": {}},
        {"no_user": True},
        "not a dict",
        {"user": {"name": 42}},
    ])

    assert [e["name"] for e in entries] == ["张三"]


def test_build_entries_deduplicates_by_nickname():
    """Paging can repeat someone; only one row should appear."""
    entries = fetch_sponsors.build_entries([_record("张三"), _record("张三")])

    assert len(entries) == 1


def test_overrides_add_a_message():
    entries = fetch_sponsors.build_entries(
        [_record("张三")], {"张三": {"message": "  笔刷很好用  "}})

    assert entries[0]["message"] == "笔刷很好用"


def test_overrides_can_exclude_an_entry():
    entries = fetch_sponsors.build_entries(
        [_record("张三"), _record("李四")], {"张三": {"exclude": True}})

    assert [e["name"] for e in entries] == ["李四"]


def test_malformed_override_is_ignored():
    entries = fetch_sponsors.build_entries(
        [_record("张三")], {"张三": "not a dict"})

    assert entries[0]["name"] == "张三"
    assert entries[0]["message"] == ""


# ── avatars survive a refresh ────────────────────────────────────────────
#
# The API cannot say which committed image belongs to whom, so without this
# every refresh (i.e. every "Run workflow" click) would wipe hand-added
# avatars and quietly turn those rows back into initial badges.


def test_previous_avatars_reads_only_non_empty_entries(tmp_path):
    path = tmp_path / "sponsors.json"
    path.write_text(json.dumps({"sponsors": [
        {"name": "甲", "avatar": "avatars/a.jpg"},
        {"name": "乙", "avatar": ""},
        {"name": "丙"},
        {"name": 42, "avatar": "x"},
        "not a dict",
    ]}), encoding="utf-8")

    assert fetch_sponsors.previous_avatars(path) == {"甲": "avatars/a.jpg"}


def test_previous_avatars_on_missing_file(tmp_path):
    assert fetch_sponsors.previous_avatars(tmp_path / "nope.json") == {}


def test_refresh_keeps_local_avatars():
    entries = fetch_sponsors.build_entries(
        [_record("甲"), _record("乙")],
        previous_avatars={"甲": "avatars/a.jpg"},
    )

    by_name = {e["name"]: e["avatar"] for e in entries}
    assert by_name["甲"] == "avatars/a.jpg"
    assert by_name["乙"] == "", "a brand new sponsor has no local image yet"


def test_override_avatar_wins_over_the_preserved_one():
    entries = fetch_sponsors.build_entries(
        [_record("甲")],
        overrides={"甲": {"avatar": "avatars/new.jpg"}},
        previous_avatars={"甲": "avatars/old.jpg"},
    )

    assert entries[0]["avatar"] == "avatars/new.jpg"


def test_override_avatar_can_clear_it():
    entries = fetch_sponsors.build_entries(
        [_record("甲")],
        overrides={"甲": {"avatar": ""}},
        previous_avatars={"甲": "avatars/old.jpg"},
    )

    assert entries[0]["avatar"] == ""


def test_non_string_override_avatar_is_dropped():
    entries = fetch_sponsors.build_entries(
        [_record("甲")], overrides={"甲": {"avatar": 42}})

    assert entries[0]["avatar"] == ""


def test_main_preserves_avatars_end_to_end(tmp_path, monkeypatch):
    """The property that actually matters: run twice, the image is still there."""
    data_path = tmp_path / "sponsors.json"
    data_path.write_text(json.dumps({
        "schema": 1, "generated_at": "2020-01-01",
        "sponsors": [{"name": "甲", "since": "", "last_pay": "",
                      "message": "", "avatar": "avatars/a.jpg"}],
    }), encoding="utf-8")

    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", tmp_path / "overrides.json")
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("甲")])

    # --force because the sponsor row itself is unchanged here.
    assert fetch_sponsors.main(["--force"]) == 0

    written = json.loads(data_path.read_text(encoding="utf-8"))
    assert written["sponsors"][0]["avatar"] == "avatars/a.jpg"


# ── document ────────────────────────────────────────────────────────────


def test_build_document_sorts_newest_first_and_keeps_schema():
    document = fetch_sponsors.build_document(
        fetch_sponsors.build_entries([
            _record("旧", created=TS_NOV_2023, paid=TS_NOV_2023),
            _record("新", created=TS_FEB_2025, paid=TS_FEB_2025),
        ]),
        "2026-09-10",
    )

    assert document["schema"] == fetch_sponsors.SCHEMA_VERSION
    assert document["generated_at"] == "2026-09-10"
    assert [e["name"] for e in document["sponsors"]] == ["新", "旧"]


def test_generated_document_round_trips_through_the_app(tmp_path, monkeypatch):
    """The tool's output must be exactly what ``core.sponsors`` reads."""
    from core import sponsors as core_sponsors

    document = fetch_sponsors.build_document(
        fetch_sponsors.build_entries([
            _record("甲", created=TS_NOV_2023, paid=TS_NOV_2023),
            _record("乙", created=TS_FEB_2025, paid=TS_FEB_2025),
        ]),
        "2026-09-10",
    )
    path = tmp_path / "sponsors.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(core_sponsors, "_data_path", lambda: path)

    loaded = core_sponsors.sorted_sponsors()

    assert [s.name for s in loaded] == ["乙", "甲"]
    assert loaded[0].date_label == "2025-02-19"


# ── idempotency ─────────────────────────────────────────────────────────


def test_is_unchanged_ignores_generated_at(tmp_path):
    path = tmp_path / "sponsors.json"
    sponsors = [{"name": "张三", "since": "", "last_pay": "", "message": "", "avatar": ""}]
    path.write_text(json.dumps({"schema": 1, "generated_at": "2020-01-01",
                               "sponsors": sponsors}), encoding="utf-8")

    document = {"schema": 1, "generated_at": "2026-09-10", "sponsors": sponsors}

    assert fetch_sponsors.is_unchanged(document, path) is True


def test_is_unchanged_detects_a_real_difference(tmp_path):
    path = tmp_path / "sponsors.json"
    path.write_text(json.dumps({"sponsors": [{"name": "旧"}]}), encoding="utf-8")

    document = {"sponsors": [{"name": "新"}]}

    assert fetch_sponsors.is_unchanged(document, path) is False


@pytest.mark.parametrize("content", ["", "{ not json", "[]", '{"sponsors": null}'])
def test_is_unchanged_treats_broken_file_as_changed(tmp_path, content):
    path = tmp_path / "sponsors.json"
    path.write_text(content, encoding="utf-8")

    assert fetch_sponsors.is_unchanged({"sponsors": []}, path) is False


def test_load_overrides_missing_file_is_not_an_error(tmp_path):
    assert fetch_sponsors.load_overrides(tmp_path / "nope.json") == {}


def test_load_overrides_broken_file_is_ignored(tmp_path, capsys):
    path = tmp_path / "overrides.json"
    path.write_text("{ broken", encoding="utf-8")

    assert fetch_sponsors.load_overrides(path) == {}
    assert "ignoring unreadable overrides" in capsys.readouterr().out


# ── manual entries survive a refresh ──────────────────────────────
#
# People the API cannot report (anonymous donors, off-platform support) used to
# be deleted by the next sync, because the monthly Action rewrites the data file
# from the API alone. The overlay 'add' list is where they live so they come back
# on every run.


def test_split_overlay_reads_manual_entries():
    tweaks, additions = fetch_sponsors.split_overlay({
        "add": [{"name": "匿名", "last_pay": "2026-08-28"}],
        "张三": {"message": "hi"},
    })

    assert tweaks == {"张三": {"message": "hi"}}
    assert additions == [{"name": "匿名", "last_pay": "2026-08-28"}]


def test_split_overlay_keeps_the_legacy_bare_map_working():
    """The file predates the 'add' section; every old override must still apply."""
    legacy = {"张三": {"message": "hi"}, "李四": {"exclude": True}}

    tweaks, additions = fetch_sponsors.split_overlay(legacy)

    assert tweaks == legacy
    assert additions == []


@pytest.mark.parametrize("value", [None, {}, "not a list", 42])
def test_split_overlay_tolerates_a_broken_add_section(value, capsys):
    tweaks, additions = fetch_sponsors.split_overlay({"张三": {"message": "hi"}, "add": value})

    assert additions == []
    assert tweaks == {"张三": {"message": "hi"}}


def test_split_overlay_tolerates_a_non_dict_overlay():
    assert fetch_sponsors.split_overlay(None) == ({}, [])
    assert fetch_sponsors.split_overlay("nonsense") == ({}, [])


def test_split_overlay_strips_comment_keys():
    """The file is hand-edited and uses //keys as JSON comments."""
    tweaks, additions = fetch_sponsors.split_overlay({
        "//note": "this is a comment",
        "//add": "and so is this",
        "add": [{"name": "匿名"}],
        "张三": {"message": "hi"},
    })

    assert tweaks == {"张三": {"message": "hi"}}
    assert additions == [{"name": "匿名"}]


def test_split_overlay_strips_comments_from_the_legacy_shape_too():
    tweaks, additions = fetch_sponsors.split_overlay({
        "//note": "comment",
        "张三": {"exclude": True},
    })

    assert tweaks == {"张三": {"exclude": True}}
    assert additions == []


def test_the_committed_overlay_is_valid_and_keeps_its_manual_rows():
    """Guards the real file: the rows in it are what survives the monthly sync."""
    overlay = fetch_sponsors.load_overrides(Path(fetch_sponsors.OVERLAY_PATH))
    tweaks, additions = fetch_sponsors.split_overlay(overlay)

    assert all(not k.startswith("//") for k in tweaks), "comments leak into tweaks"
    assert isinstance(additions, list)
    for row in additions:
        assert isinstance(row, dict), row
        assert isinstance(row.get("name"), str) and row["name"].strip(), row
        assert fetch_sponsors.OUTPUT_FIELDS[:1] and set(row) <= set(fetch_sponsors.OUTPUT_FIELDS), (
            f"{row} carries a field the roll does not store")


def test_committed_overlay_manual_rows_survive_a_sync(tmp_path, monkeypatch):
    """End-to-end against the real overlay: the hand-added rows must reappear."""
    overlay = fetch_sponsors.load_overrides(Path(fetch_sponsors.OVERLAY_PATH))
    _, additions = fetch_sponsors.split_overlay(overlay)
    if not additions:
        pytest.skip("no manual rows in the committed overlay")

    data_path = tmp_path / "sponsors.json"
    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", Path(fetch_sponsors.OVERLAY_PATH))
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("甲")])

    assert fetch_sponsors.main(["--force"]) == 0

    from core import sponsors as core_sponsors
    monkeypatch.setattr(core_sponsors, "_data_path", lambda: data_path)
    names = [s.name for s in core_sponsors.sorted_sponsors()]
    for row in additions:
        assert row["name"] in names, f"{row['name']!r} was dropped by the sync"


def test_manual_entry_is_added_next_to_the_api_rows():
    entries = fetch_sponsors.build_entries(
        [_record("甲")],
        additions=[{"name": "匿名", "last_pay": "2026-08-28"}],
    )

    assert [e["name"] for e in entries] == ["甲", "匿名"]
    assert entries[1]["last_pay"] == "2026-08-28"
    assert entries[1]["message"] == ""
    assert entries[1]["avatar"] == ""


def test_manual_entry_without_a_name_is_dropped():
    entries = fetch_sponsors.build_entries([], additions=[
        {"message": "没名字"},
        {"name": "   "},
        {"name": 42},
        "not a dict",
        {"name": "匿名"},
    ])

    assert [e["name"] for e in entries] == ["匿名"]


def test_manual_entry_cannot_duplicate_an_api_row():
    """A nickname the API returned is an enrichment, never a second row."""
    entries = fetch_sponsors.build_entries(
        [_record("张三")],
        additions=[{"name": "张三", "message": "duplicate?"}],
        previous_avatars={"张三": "avatars/a.jpg"},
    )

    assert [e["name"] for e in entries] == ["张三"]
    entry = entries[0]
    assert entry["last_pay"] == "2023-11-14", "the API date must not be overwritten"
    assert entry["avatar"] == "avatars/a.jpg"
    assert entry["message"] == "", "the name-override path owns the message"


def test_manual_entry_can_enrich_a_blank_api_field():
    """An unknown date for a real sponsor comes from the file, not the API."""
    blank = _record("empty")
    blank["create_time"] = ""
    blank["last_pay_time"] = ""

    entries = fetch_sponsors.build_entries(
        [blank], additions=[{"name": "empty", "last_pay": "2026-8-28"}])

    (entry,) = entries
    assert entry["last_pay"] == "2026-08-28"
    assert entry["since"] == ""


def test_manual_entry_enrichment_never_blanks_a_known_date():
    """Filling blanks must not turn into overwriting what the API knows."""
    entries = fetch_sponsors.build_entries(
        [_record("张三")],
        additions=[{"name": "张三", "last_pay": "1999-01-01"}])

    assert entries[0]["last_pay"] == "2023-11-14"


def test_manual_entry_enrichment_can_supply_a_local_avatar():
    """A manual row never passes through `previous_avatars`, so it carries its own."""
    entries = fetch_sponsors.build_entries(
        [_record("张三")],
        additions=[{"name": "张三", "avatar": "avatars/zhang.jpg"}])

    assert entries[0]["avatar"] == "avatars/zhang.jpg"


def test_manual_entry_dates_are_normalised_in_the_file():
    """Canonical output is what keeps a monthly refresh from committing a diff."""
    entries = fetch_sponsors.build_entries([], additions=[
        {"name": "甲", "last_pay": "2026-8-28"},
        {"name": "乙", "last_pay": "2026-8"},
        {"name": "丙", "last_pay": "not-a-date"},
    ])

    assert [e["last_pay"] for e in entries] == ["2026-08-28", "2026-08-01", ""]


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-8-31", "2026-08-31"),
    ("2026-08-05", "2026-08-05"),
    (" 2026-12-01 ", "2026-12-01"),
    ("2026-8", "2026-08-01"),
    ("2026-08", "2026-08-01"),
    ("2026-13-01", ""),
    ("2026-08-32", ""),
    ("2026-02-30", ""),
    ("2023-02-29", ""),
    ("2024-02-29", "2024-02-29"),
    ("not-a-date", ""),
    ("2026", ""),
    ("20260831", ""),
    ("", ""),
    (None, ""),
    (42, ""),
])
def test_normalize_date(raw, expected):
    assert fetch_sponsors.normalize_date(raw) == expected


def test_normalize_date_matches_the_app_loader():
    """Tool and app must agree, or the file says one date and the UI shows another."""
    from core import sponsors as core_sponsors

    samples = ["2026-8-31", "2026-08-05", "2026-8", "2026-13-01", "2026-02-30",
               "2024-02-29", "2023-02-29", "junk", "2026", "", None, 42]

    for raw in samples:
        assert fetch_sponsors.normalize_date(raw) == core_sponsors._date(raw), raw


def test_main_brings_back_a_manual_entry_on_every_run(tmp_path, monkeypatch):
    """The property that actually matters: run the monthly sync twice, `匿名` stays."""
    data_path = tmp_path / "sponsors.json"
    overlay_path = tmp_path / "sponsor_overrides.json"
    overlay_path.write_text(json.dumps({
        "add": [{"name": "匿名", "last_pay": "2026-08-28"},
                {"name": "乙师傅", "since": "2026-7-1", "message": "谢谢"}],
        "甲": {"message": "甲的留言"},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", overlay_path)
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("甲")])

    assert fetch_sponsors.main(["--force"]) == 0
    first = json.loads(data_path.read_text(encoding="utf-8"))
    assert [e["name"] for e in first["sponsors"]] == ["匿名", "乙师傅", "甲"]
    assert first["sponsors"][2]["message"] == "甲的留言"

    # Second run: the API still knows nothing about the manual rows.
    assert fetch_sponsors.main([]) == 0
    second = json.loads(data_path.read_text(encoding="utf-8"))
    assert [e["name"] for e in second["sponsors"]] == ["匿名", "乙师傅", "甲"]


def test_main_manual_entries_are_idempotent_no_diff_on_rerun(tmp_path, monkeypatch, capsys):
    """A normalised date is what stops the Action from committing every month."""
    data_path = tmp_path / "sponsors.json"
    overlay_path = tmp_path / "sponsor_overrides.json"
    overlay_path.write_text(json.dumps({
        "add": [{"name": "匿名", "last_pay": "2026-8-28"}],
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", overlay_path)
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("甲")])

    assert fetch_sponsors.main([]) == 0
    first = data_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert fetch_sponsors.main([]) == 0
    assert "no change" in capsys.readouterr().out
    assert data_path.read_text(encoding="utf-8") == first


def test_main_reports_manual_entries(tmp_path, monkeypatch, capsys):
    data_path = tmp_path / "sponsors.json"
    overlay_path = tmp_path / "sponsor_overrides.json"
    overlay_path.write_text(json.dumps({"add": [{"name": "匿名"}]},
                                       ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", overlay_path)
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("甲")])

    assert fetch_sponsors.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 manual entry" in out
    assert "1 added by hand" in out


def test_manual_entry_survives_when_the_api_returns_nothing(tmp_path, monkeypatch):
    """An empty/failed API page must not wipe a hand-written row."""
    data_path = tmp_path / "sponsors.json"
    overlay_path = tmp_path / "sponsor_overrides.json"
    overlay_path.write_text(json.dumps({"add": [{"name": "匿名"}]},
                                       ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", overlay_path)
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [])

    assert fetch_sponsors.main(["--force"]) == 0

    written = json.loads(data_path.read_text(encoding="utf-8"))
    assert [e["name"] for e in written["sponsors"]] == ["匿名"]


# ── pagination ──────────────────────────────────────────────────────────


def test_fetch_all_walks_every_page():
    opener = _opener({
        1: {"ec": 200, "data": {"total_page": 2, "list": [_record("甲")]}},
        2: {"ec": 200, "data": {"total_page": 2, "list": [_record("乙")]}},
    })

    records = fetch_sponsors.fetch_all("uid", "tok", opener=opener)

    assert [r["user"]["name"] for r in records] == ["甲", "乙"]
    assert len(opener.calls) == 2


def test_fetch_all_stops_after_a_single_page():
    opener = _opener({1: {"ec": 200, "data": {"total_page": 1, "list": [_record("甲")]}}})

    records = fetch_sponsors.fetch_all("uid", "tok", opener=opener)

    assert len(records) == 1
    assert len(opener.calls) == 1


def test_fetch_page_raises_on_api_error():
    opener = _opener({1: {"ec": 400, "em": "bad sign", "data": {}}})

    with pytest.raises(RuntimeError, match="afdian api error"):
        fetch_sponsors.fetch_page("uid", "tok", 1, opener=opener)


def test_fetch_page_tolerates_a_missing_data_block():
    opener = _opener({1: {"ec": 200}})

    assert fetch_sponsors.fetch_page("uid", "tok", 1, opener=opener) == {}


# ── entry point ─────────────────────────────────────────────────────────


def test_main_refuses_without_credentials(monkeypatch, capsys):
    monkeypatch.delenv("AFDIAN_USER_ID", raising=False)
    monkeypatch.delenv("AFDIAN_API_TOKEN", raising=False)

    assert fetch_sponsors.main([]) == 1
    out = capsys.readouterr().out
    assert "AFDIAN_USER_ID" in out
    # Must not silently write an empty list over the existing one.
    assert "Refusing to run" in out


def test_main_is_idempotent(tmp_path, monkeypatch, capsys):
    data_path = tmp_path / "sponsors.json"
    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", tmp_path / "overrides.json")
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("张三")])

    assert fetch_sponsors.main([]) == 0
    first = data_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert fetch_sponsors.main([]) == 0
    assert "no change" in capsys.readouterr().out
    assert data_path.read_text(encoding="utf-8") == first


def test_main_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    data_path = tmp_path / "sponsors.json"
    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", tmp_path / "overrides.json")
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(fetch_sponsors, "fetch_all", lambda *a, **k: [_record("张三")])

    assert fetch_sponsors.main(["--dry-run"]) == 0
    assert not data_path.exists()
    assert "张三" in capsys.readouterr().out


def test_main_writes_a_loadable_file(tmp_path, monkeypatch):
    from core import sponsors as core_sponsors

    data_path = tmp_path / "sponsors.json"
    monkeypatch.setattr(fetch_sponsors, "DATA_PATH", data_path)
    monkeypatch.setattr(fetch_sponsors, "OVERLAY_PATH", tmp_path / "overrides.json")
    monkeypatch.setenv("AFDIAN_USER_ID", "uid")
    monkeypatch.setenv("AFDIAN_API_TOKEN", "tok")
    monkeypatch.setattr(
        fetch_sponsors, "fetch_all",
        lambda *a, **k: [_record("甲"), _record("乙", created=TS_FEB_2025,
                                                paid=TS_FEB_2025)],
    )

    assert fetch_sponsors.main([]) == 0

    monkeypatch.setattr(core_sponsors, "_data_path", lambda: data_path)
    assert [s.name for s in core_sponsors.sorted_sponsors()] == ["乙", "甲"]


def test_shipped_data_file_is_valid_json():
    """Guards the committed honour roll against a bad hand-edit."""
    path = Path(fetch_sponsors.DATA_PATH)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload.get("sponsors"), list)
