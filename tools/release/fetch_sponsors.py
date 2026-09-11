"""Sync the sponsor honour roll from Afdian into ``core/data/sponsors.json``.

Run by the maintainer, never by the shipped app. The Afdian API needs a
user id and an api token, and **a token inside a distributed binary is a
leaked token** (Python bytecode is trivially unpacked), so the app only ever
reads a static, bundled data file. This script is the thing that produces it.

Usage
-----
Local (optional, the GitHub Action is the usual path)::

    setx AFDIAN_USER_ID   "<your user id>"      # once
    setx AFDIAN_API_TOKEN "<your api token>"    # once
    python tools/release/fetch_sponsors.py

Nicknames only, please. The honour roll is a public thank-you, so the output
is restricted to the same field whitelist ``core.sponsors`` accepts — an
e-mail, an order number or an amount can never leak into the data file even if
the API hands them over.

Overrides let you add a message, point at a local avatar, or take someone off
the list without editing the generated file (see ``OVERLAY_PATH``). Avatars
that are already on the roll survive a refresh on their own — the API cannot
say which image belongs to whom, so ``previous_avatars`` carries them forward.

Two kinds of override live in that one file, and the distinction matters:

* **Per-name tweaks** — `{"张三": {"message": ..., "avatar": ..., "exclude": true}}`.
  These only ever *modify* people the API returned.
* **Manual entries** — `{"add": [{"name": ...}]}`. These are people the API
  cannot report (anonymous donors, off-platform support, someone who asked not
  to be listed by their Afdian nickname). They are appended on every run, so a
  hand-written row survives the monthly sync instead of being deleted by it.

Both shapes coexist: a bare `{name: {...}}` map is still read as per-name
tweaks, so the old single-purpose file keeps working untouched.

On a nickname collision the manual row never wins a field it does not own: if
the API already lists someone, an `add` row for the same name only fills in what
is empty, so it can never duplicate a row or blank out a date the API knows.
Messages are the exception and live in exactly one place — the per-name tweak.

``//comment`` keys are ignored, because this file is hand-edited and JSON has no
comment syntax.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "core" / "data" / "sponsors.json"
OVERLAY_PATH = PROJECT_ROOT / "core" / "data" / "sponsor_overrides.json"

API_URL = "https://afdian.com/api/open/query-sponsor"
TIMEOUT_SECONDS = 20
SCHEMA_VERSION = 1

#: Must stay identical to what ``core.sponsors`` accepts; a test enforces it.
OUTPUT_FIELDS = ("name", "since", "last_pay", "message", "avatar")

#: Key that holds hand-maintained rows in the overlay file (see the docstring).
ADD_KEY = "add"

#: Dates that reach the data file are normalised here, not in the app. The app
#: stays lenient (``core.sponsors._date``), but a hand-typed ``2026-8`` must be
#: canonical **in the file** or every run would rewrite it and the Action would
#: commit a diff for a change that never happened. Mirrors the app's rules.
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


# ── pure helpers (no network, unit-tested) ─────────────────────────────


def sign(token: str, params: str, ts: int | str, user_id: str) -> str:
    """Afdian request signature: ``md5(token + params + ts + user_id)``.

    *params* must be the exact JSON string sent in the request body — the
    signature is whitespace-sensitive, which is why the caller builds it with
    ``separators=(",", ":")``.
    """
    raw = f"{token}{params}{ts}{user_id}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def day_from_timestamp(value) -> str:
    """Unix seconds → ``YYYY-MM-DD`` (the only date shape the roll stores)."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"


def _nickname(entry: dict) -> str:
    user = entry.get("user")
    if not isinstance(user, dict):
        return ""
    name = user.get("name")
    return name.strip() if isinstance(name, str) else ""


def _tweak_keys(mapping: dict) -> dict:
    """Keep only genuine per-name tweaks: drop ``//`` comments and the ``add`` key.

    Standard JSON has no comments and the overlay is edited by hand, so the
    ``//note`` convention is honoured by ignoring those keys — including the
    ``//add`` that documents the section next to the real ``add``. Without the
    filter the run summary would count comments as overrides, and ``add`` itself
    would sit in the map as a non-dict "override" for a nickname nobody has.
    """
    return {
        k: v for k, v in mapping.items()
        if k != ADD_KEY and not (isinstance(k, str) and k.startswith("//"))
    }


def split_overlay(overlay: dict | None) -> tuple[dict, list]:
    """Split the overlay file into ``(per-name tweaks, manual entries)``.

    The file started life as a bare ``{name: {...}}`` map, and that shape is
    still honoured in full — only the presence of an ``add`` key switches to the
    two-section reading. Someone genuinely named "add" is far-fetched; silently
    losing every other override would not be.
    """
    if not isinstance(overlay, dict):
        return {}, []
    if ADD_KEY not in overlay:
        return _tweak_keys(overlay), []
    additions = overlay.get(ADD_KEY)
    if not isinstance(additions, list):
        print(f"[warn] overlay {ADD_KEY!r} is {type(additions).__name__}, "
              f"expected a list — ignoring it")
        additions = []
    return _tweak_keys(overlay), additions


def _is_real_date(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12:
        return False
    days = _DAYS_IN_MONTH[month - 1]
    if month == 2 and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        days = 28
    return 1 <= day <= days


def normalize_date(value) -> str:
    """Hand-typed date → canonical ``YYYY-MM-DD``, or ``""`` when unusable.

    Accepts the shapes people actually type (``2026-8-31``, ``2026-8``) and
    rejects calendar nonsense, exactly like the app loader — the difference is
    that this one runs *before* the commit, so the file on disk is always
    canonical and a refresh never produces a cosmetic diff.
    """
    text = value.strip() if isinstance(value, str) else ""
    match = _DATE_RE.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        month_match = _MONTH_RE.match(text)
        if not month_match:
            return ""
        year, month = int(month_match.group(1)), int(month_match.group(2))
        day = 1
    if not _is_real_date(year, month, day):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def manual_entries(additions: list, skip: set[str] | None = None) -> list[dict]:
    """Whitelist-coerce the overlay ``add`` rows into roll entries.

    Pure shaping, no merging: names in *skip* are left out (the caller uses it
    to drop rows it has already accounted for) and duplicates lose to the first
    occurrence. ``all_sum_amount``-style extras are unreachable by construction,
    so a hand-written row cannot smuggle a private field into a public file.
    """
    skip = skip or set()
    entries: list[dict] = []
    seen: set[str] = set()
    for item in additions:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name or name in skip or name in seen:
            continue
        seen.add(name)
        message = item.get("message")
        avatar = item.get("avatar")
        entries.append({
            "name": name,
            "since": normalize_date(item.get("since")),
            "last_pay": normalize_date(item.get("last_pay")),
            "message": message.strip() if isinstance(message, str) else "",
            "avatar": avatar.strip() if isinstance(avatar, str) else "",
        })
    return entries


def build_entries(records: list[dict], overrides: dict | None = None,
                  previous_avatars: dict | None = None,
                  additions: list | None = None) -> list[dict]:
    """Turn raw API records into the fields the roll actually stores.

    ``all_sum_amount`` is read and thrown away on purpose: the roll has no
    tiers and no amounts, so the value has no consumer and must not be stored.

    ``previous_avatars`` is the ``{name: avatar}`` map from the data file that
    is already on disk, and it exists for one reason: the API cannot tell us
    which locally-committed image belongs to whom, so a plain rebuild would
    silently wipe every hand-added avatar on each refresh. Names that are
    already on the roll therefore keep their image. An ``avatar`` key in the
    overrides (including an explicit ``""``) always wins.

    ``additions`` are the hand-maintained rows from the overlay ``add`` list.
    A row whose nickname the API *did* return is an enrichment rather than a
    second row: it fills in only the fields the API left empty, so a manual date
    shows up on a row that had none and can never overwrite a real timestamp.
    Everything else is appended at the end, which is what keeps a rebuild from
    deleting people the API cannot report at all.
    """
    overrides = overrides or {}
    previous_avatars = previous_avatars or {}
    additions = additions or []
    entries: list[dict] = []
    seen: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue
        name = _nickname(record)
        if not name or name in seen:
            continue

        override = overrides.get(name) or {}
        if not isinstance(override, dict):
            override = {}
        if override.get("exclude"):
            continue

        seen.add(name)
        message = override.get("message")
        if "avatar" in override:
            # Present-but-empty means "clear it"; only the type is guarded.
            avatar = override["avatar"] if isinstance(override["avatar"], str) else ""
        else:
            avatar = previous_avatars.get(name, "")
        entries.append({
            "name": name,
            "since": day_from_timestamp(record.get("create_time")),
            "last_pay": day_from_timestamp(record.get("last_pay_time")),
            "message": message.strip() if isinstance(message, str) else "",
            "avatar": avatar.strip(),
        })

    # Enrichment pass: a manual row may supply a fact the API cannot (a date for
    # an anonymous donation, a local image), but it only ever fills blanks.
    # Deliberately excludes ``message``: that field has exactly one home, the
    # per-name override, and a second way to set it would be a precedence
    # puzzle for whoever maintains the file.
    for manual in manual_entries(additions):
        for entry in entries:
            if entry["name"] != manual["name"]:
                continue
            for field in ("since", "last_pay", "avatar"):
                if not entry[field] and manual[field]:
                    entry[field] = manual[field]
            break

    # Everyone else is somebody the API does not know about at all.
    known = {entry["name"] for entry in entries}
    entries.extend(manual_entries(additions, known))
    return entries


def sort_entries(entries: list[dict]) -> list[dict]:
    """Newest support first; entries without a usable date go last."""
    return sorted(
        entries,
        key=lambda e: e.get("last_pay") or e.get("since") or "",
        reverse=True,
    )


def build_document(entries: list[dict], generated_at: str) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": generated_at,
        "sponsors": sort_entries(entries),
    }


def load_overrides(path: Path) -> dict:
    """Missing/broken overlay is not an error — it just means 'no overrides'."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] ignoring unreadable overrides ({path.name}): {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def existing_sponsors(path: Path) -> list[dict] | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    sponsors = data.get("sponsors")
    return sponsors if isinstance(sponsors, list) else None


def is_unchanged(document: dict, path: Path) -> bool:
    """True when only ``generated_at`` would change.

    Keeps the scheduled Action from committing an empty diff every month.
    """
    current = existing_sponsors(path)
    if current is None:
        return False
    return current == document["sponsors"]


def previous_avatars(path: Path) -> dict[str, str]:
    """``{name: avatar}`` from the file already on disk (empty ones skipped).

    The API cannot tell us which committed image belongs to whom, so this is
    what keeps hand-added avatars from being wiped on every refresh.
    """
    current = existing_sponsors(path) or []
    preserved: dict[str, str] = {}
    for entry in current:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        avatar = entry.get("avatar")
        if isinstance(name, str) and name.strip() and isinstance(avatar, str) and avatar.strip():
            preserved[name.strip()] = avatar.strip()
    return preserved


# ── network (kept thin and injectable so tests never hit the wire) ─────


def fetch_page(user_id: str, token: str, page: int, opener=None) -> dict:
    params = json.dumps({"page": page}, separators=(",", ":"))
    ts = int(time.time())
    body = urllib.parse.urlencode({
        "user_id": user_id,
        "params": params,
        "ts": ts,
        "sign": sign(token, params, ts, user_id),
    }).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ec") != 200:
        raise RuntimeError(f"afdian api error: {payload!r}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def fetch_all(user_id: str, token: str, opener=None) -> list[dict]:
    first = fetch_page(user_id, token, 1, opener=opener)
    total_pages = int(first.get("total_page") or 1)
    records = [r for r in (first.get("list") or []) if isinstance(r, dict)]
    for page in range(2, total_pages + 1):
        page_data = fetch_page(user_id, token, page, opener=opener)
        records.extend(r for r in (page_data.get("list") or []) if isinstance(r, dict))
    return records


# ── entry point ───────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the result without writing the data file")
    parser.add_argument("--force", action="store_true",
                        help="write even when nothing changed")
    args = parser.parse_args(argv)

    user_id = os.environ.get("AFDIAN_USER_ID", "").strip()
    token = os.environ.get("AFDIAN_API_TOKEN", "").strip()
    if not user_id or not token:
        print("[error] AFDIAN_USER_ID / AFDIAN_API_TOKEN are not set.")
        print("        Refusing to run: a missing token would overwrite the "
              "existing list with an empty one.")
        return 1

    overrides, additions = split_overlay(load_overrides(OVERLAY_PATH))
    if overrides:
        print(f"[info] {len(overrides)} name override(s) loaded from {OVERLAY_PATH.name}")
    if additions:
        print(f"[info] {len(additions)} manual entry/entries in {OVERLAY_PATH.name}")

    records = fetch_all(user_id, token)
    prior = previous_avatars(DATA_PATH)
    entries = build_entries(records, overrides, prior, additions)
    excluded = sum(1 for r in records if (overrides.get(_nickname(r)) or {}).get("exclude"))
    kept = sum(1 for e in entries if e["avatar"] and e["name"] in prior)
    from_api = {_nickname(r) for r in records}
    manual = sum(1 for e in entries if e["name"] not in from_api)
    print(f"[info] afdian returned {len(records)} record(s); "
          f"{len(entries)} listed ({manual} added by hand), "
          f"{excluded} excluded by overrides, {kept} kept a local avatar")

    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    document = build_document(entries, generated_at)

    if not args.force and is_unchanged(document, DATA_PATH):
        print("[info] no change — leaving the data file untouched")
        return 0

    if args.dry_run:
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[info] wrote {len(entries)} sponsor(s) to {DATA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
