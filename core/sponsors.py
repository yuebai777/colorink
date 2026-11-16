"""The sponsor honour roll (鸣谢名单).

Bundled data lives in ``core/data/sponsors.json`` so the roll always works
offline. When the dialog is opened, :func:`refresh_sponsors` can also check
Afdian's public webpage endpoints and the repository's ``sponsors.json`` in
the background, storing any updates in ``%APPDATA%/Colorink/sponsors-cache.json``
with zero tokens or secrets in the shipped binary.

Design notes worth keeping:

* **Only Qt-free stdlib imports.** Keeping this module free of Qt means the
  parsing/sorting/refresh rules can be unit-tested without a QApplication.
* **No amount, no tiers.** The list is a single time-ordered roll of honour.
  Money has no consumer here, so the field is not merely hidden — it does not
  exist in the data structure or in :class:`Sponsor` at all.
* **``name`` is the only required field.** Least possible typing for whoever
  maintains the list: ``{"name": "张三"}`` is a valid entry. Everything else
  degrades gracefully (no date shown, no message shown, initials avatar).
* **Avatars are local files, never downloaded.** Afdian hands out ``afdiancdn``
  URLs, but those are neither reliably reachable for this audience nor
  redistributable here; sponsor-chosen images are committed under
  ``core/data/avatars/`` and referenced relatively (see :func:`resolve_avatar`).
* **Every failure degrades safely.** A missing/corrupt file or unreachable
  network falls back cleanly to bundled/cached data and never takes the
  settings window down with it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core import config

logger = logging.getLogger(__name__)

#: Where "I want to sponsor too" points.
AFDIAN_URL = "https://afdian.com/a/touyimoyuebai"
AFDIAN_CREATOR_ID = "e2b8b496a9bd11f1af9b5254001e7c00"
AFDIAN_WEB_SPONSORS_URL = "https://afdian.com/api/creator/get-sponsors"
AFDIAN_WEB_TOP_SPONSORS_URL = "https://afdian.com/api/creator/get-top-sponsors"
REMOTE_SPONSORS_URL = (
    "https://raw.githubusercontent.com/yuebai777/colorink/master/core/data/sponsors.json"
)

_DATA_REL = Path("core") / "data" / "sponsors.json"
_CACHE_NAME = "sponsors-cache.json"
_REQUEST_TIMEOUT = 4.0
_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://afdian.com/",
}


@dataclass(frozen=True)
class Sponsor:
    """One entry of the honour roll.

    Deliberately carries no amount/money field: the field whitelist is
    enforced by construction, so private data accidentally committed to the
    JSON (e-mail, order number, amount) can never reach the UI or even memory.
    """

    name: str
    since: str = ""
    last_pay: str = ""
    message: str = ""
    avatar: str = ""

    @property
    def date_label(self) -> str:
        """``YYYY-MM-DD`` for the row, preferring the most recent support date."""
        return self.last_pay or self.since


def _data_path() -> Path:
    """Resolve the bundled data file.

    Frozen layouts differ between onefile and onedir, so a couple of
    candidates are probed rather than assuming a single location. Mirrors the
    idiom used by ``core/native_grayscale.py::_runtime_root``.
    """
    if getattr(sys, "frozen", False):
        candidates: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / _DATA_REL)
        candidates.append(Path(sys.executable).parent / _DATA_REL)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]
    return Path(__file__).resolve().parents[1] / _DATA_REL


def _text(value: object) -> str:
    """Whitelist coercion: only non-empty strings survive."""
    return value.strip() if isinstance(value, str) else ""


def _data_dir() -> Path:
    """The folder the data file was actually found in (holds ``avatars/``)."""
    return _data_path().parent


def resolve_avatar(value: object) -> Path | None:
    """Resolve an ``avatar`` reference to a file on disk.

    Values are relative to the data directory, e.g. ``avatars/miya.jpg``.
    Returns ``None`` when the field is empty, when the path points outside the
    data directory, or when the file is missing — the caller then falls back to
    the drawn initials badge, so a missing or bogus image degrades a single row
    instead of breaking the roll.

    The containment check is not paranoia: this file lives in a public repo and
    is hand-edited, so a stray ``../../`` must not turn into an arbitrary file
    read (an absolute path is rejected by the same check).
    """
    text = _text(value)
    if not text:
        return None
    try:
        base = _data_dir().resolve()
        candidate = (base / text).resolve()
        candidate.relative_to(base)
    except (OSError, ValueError):
        logger.debug("sponsor avatar rejected: %r", text)
        return None
    return candidate if candidate.is_file() else None


_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")

_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_real_date(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12:
        return False
    days = _DAYS_IN_MONTH[month - 1]
    if month == 2 and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        days = 28
    return 1 <= day <= days


def _date(value: object) -> str:
    """Normalise a support date to canonical ``YYYY-MM-DD``.

    The data file is hand-editable by design, so this is deliberately lenient
    about the shape people actually type (``2026-8-31`` is accepted and becomes
    ``2026-08-31``) but strict about nonsense. A bare ``YYYY-MM`` is accepted
    too and padded to the month's first day, so month-precision data keeps
    working. Anything that is not a real calendar date collapses to ``""``,
    which sorts to the end of the roll — otherwise a typo'd date would sort by
    raw text and could push someone to the top of the honour roll.
    """
    text = _text(value)
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


def _cache_path() -> Path:
    """User-writable cache for live-refreshed sponsor data."""
    return Path(config.get_user_data_dir()) / _CACHE_NAME


def _read_json_doc(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - degrade, never propagate
        logger.debug("sponsor file unavailable (%s): %s", path.name, exc)
        return None
    if not isinstance(raw, dict):
        logger.debug("sponsor file malformed (%s): root is %s", path.name, type(raw).__name__)
        return None
    return raw


def _parse_entries(items: object, *, allow_avatar: bool = True) -> list[Sponsor]:
    if not isinstance(items, list):
        return []
    sponsors: list[Sponsor] = []
    seen: dict[str, int] = {}  # nickname → index in ``sponsors``
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            # ``name`` is the one required field — skip rather than show a blank row.
            continue
        entry = Sponsor(
            name=name,
            since=_date(item.get("since")),
            last_pay=_date(item.get("last_pay")),
            message=_text(item.get("message")),
            avatar=_text(item.get("avatar")) if allow_avatar else "",
        )
        index = seen.get(name)
        if index is None:
            seen[name] = len(sponsors)
            sponsors.append(entry)
        else:
            sponsors[index] = _preferred(sponsors[index], entry)
    return sponsors


def load_sponsors() -> list[Sponsor]:
    """Read the bundled honour roll. Any problem at all yields ``[]``.

    The caller is a settings dialog: a broken data file should quietly show the
    empty state, never raise. ``logger.debug`` keeps a trail so a packaged
    build stuck on the empty state is still diagnosable.
    """
    raw = _read_json_doc(_data_path())
    if raw is None:
        return []
    items = raw.get("sponsors")
    if not isinstance(items, list):
        logger.debug("sponsor list malformed: 'sponsors' is %s", type(items).__name__)
        return []
    return _parse_entries(items)


def _merge_lists(primary: list[Sponsor], secondary: list[Sponsor]) -> list[Sponsor]:
    """Combine two Sponsor lists, deduplicating by nickname via ``_preferred``."""
    merged: list[Sponsor] = []
    seen: dict[str, int] = {}
    for entry in (*primary, *secondary):
        index = seen.get(entry.name)
        if index is None:
            seen[entry.name] = len(merged)
            merged.append(entry)
        else:
            merged[index] = _preferred(merged[index], entry)
    return merged


def _select_curated(bundled_doc: dict | None, cache_doc: dict | None) -> list[Sponsor]:
    """Choose the authoritative curated list between bundled JSON and cached GitHub JSON."""
    bundled = _parse_entries((bundled_doc or {}).get("sponsors"))
    if not cache_doc or not cache_doc.get("has_remote"):
        return bundled
    remote_raw = cache_doc.get("remote_sponsors")
    if not isinstance(remote_raw, list):
        return bundled
    remote = _parse_entries(remote_raw)
    bundled_date = _date((bundled_doc or {}).get("generated_at"))
    remote_date = _date(cache_doc.get("remote_generated_at"))

    if not bundled and remote:
        return remote
    if remote_date and (not bundled_date or remote_date > bundled_date):
        return remote
    if remote_date == bundled_date:
        return _merge_lists(bundled, remote)
    return bundled


def load_effective_sponsors() -> list[Sponsor]:
    """Merge bundled data with any cached remote / live Afdian entries."""
    bundled_doc = _read_json_doc(_data_path())
    cache_doc = _read_json_doc(_cache_path())
    curated = _select_curated(bundled_doc, cache_doc)
    if cache_doc is None:
        return curated
    afdian_live = _parse_entries(cache_doc.get("afdian_sponsors"), allow_avatar=False)
    if not afdian_live:
        return curated
    known = {s.name for s in curated}
    extras = [entry for entry in afdian_live if entry.name not in known]
    return curated + extras


def _preferred(first: Sponsor, second: Sponsor) -> Sponsor:
    """Pick between two entries for the same person, field by field.

    A duplicate nickname in the data file is a hand-edit slip, and the file is
    hand-edited by design — one person shown twice (and counted twice) on a
    public thank-you page is a visible lie, so the loader collapses them.

    No single entry wins outright: the newer date wins, but an entry that
    carries a message or an avatar keeps it even when it is the older of the
    two. Merging per field means a half-filled duplicate can only ever add
    information, never silently drop what the other copy knew.
    """
    def chosen(field: str) -> str:
        left, right = getattr(first, field), getattr(second, field)
        if field == "name":
            return left
        if not left:
            return right
        if not right:
            return left
        return max(left, right) if field in ("since", "last_pay") else left

    return Sponsor(
        name=first.name,
        since=chosen("since"),
        last_pay=chosen("last_pay"),
        message=chosen("message"),
        avatar=chosen("avatar"),
    )


def _sort_key(sponsor: Sponsor) -> str:
    """Canonical ``YYYY-MM-DD`` sorts chronologically as plain text.

    Safe because :func:`_date` normalises every date to fixed width first —
    never compare raw, unvalidated strings here.
    """
    return sponsor.date_label


def sorted_sponsors() -> list[Sponsor]:
    """The honour roll, newest support first; entries without dates go last."""
    return sorted(load_effective_sponsors(), key=_sort_key, reverse=True)


# ── background online refresh ───────────────────────────────────────────


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _entry_to_dict(entry: Sponsor) -> dict:
    return {
        "name": entry.name,
        "since": entry.since,
        "last_pay": entry.last_pay,
        "message": entry.message,
        "avatar": entry.avatar,
    }


def _fetch_json(url: str, *, timeout: float, opener=None, headers: dict | None = None) -> dict | None:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Colorink"})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - network errors must degrade quietly
        logger.debug("sponsor refresh fetch failed (%s): %s", url, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_remote_doc(timeout: float = _REQUEST_TIMEOUT,
                      opener=None) -> tuple[str, list[dict]] | None:
    raw = _fetch_json(REMOTE_SPONSORS_URL, timeout=timeout, opener=opener)
    if raw is None or not isinstance(raw.get("sponsors"), list):
        return None
    generated_at = _date(raw.get("generated_at"))
    entries = [_entry_to_dict(s) for s in _parse_entries(raw.get("sponsors"))]
    return generated_at, entries


def _fetch_afdian_web_names(timeout: float = _REQUEST_TIMEOUT, opener=None,
                            max_pages: int = 20) -> list[str] | None:
    names: list[str] = []
    seen: set[str] = set()

    def _collect(items: object):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"))
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({
            "user_id": AFDIAN_CREATOR_ID,
            "type": "new",
            "page": page,
        })
        raw = _fetch_json(
            f"{AFDIAN_WEB_SPONSORS_URL}?{query}",
            timeout=timeout,
            opener=opener,
            headers=_WEB_HEADERS,
        )
        if raw is None or raw.get("ec") != 200:
            return None if page == 1 else names
        data = raw.get("data")
        if not isinstance(data, dict):
            break
        page_items = data.get("list")
        _collect(page_items)
        if not data.get("has_more") or not page_items:
            break

    top_query = urllib.parse.urlencode({"user_id": AFDIAN_CREATOR_ID})
    top_raw = _fetch_json(
        f"{AFDIAN_WEB_TOP_SPONSORS_URL}?{top_query}",
        timeout=timeout,
        opener=opener,
        headers=_WEB_HEADERS,
    )
    if isinstance(top_raw, dict) and top_raw.get("ec") == 200:
        top_data = top_raw.get("data")
        if isinstance(top_data, dict):
            _collect(top_data.get("list"))

    return names


def refresh_sponsors(timeout: float = _REQUEST_TIMEOUT, opener=None) -> dict:
    """Best-effort online refresh from GitHub + Afdian public webpage.

    Updates the user's cache file atomically when at least one source succeeds.
    Never raises; returns ``{"ok": bool, "changed": bool, "sponsors": list}``.
    """
    before = sorted_sponsors()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_remote = pool.submit(_fetch_remote_doc, timeout, opener)
        fut_afdian = pool.submit(_fetch_afdian_web_names, timeout, opener)
        remote_doc = fut_remote.result()
        afdian_names = fut_afdian.result()

    if remote_doc is None and afdian_names is None:
        return {"ok": False, "changed": False, "sponsors": before}

    bundled_doc = _read_json_doc(_data_path())
    cache_doc = _read_json_doc(_cache_path()) or {}
    today = _today_str()

    if remote_doc is not None:
        remote_gen_at, remote_items = remote_doc
        cache_doc["has_remote"] = True
        cache_doc["remote_generated_at"] = remote_gen_at
        cache_doc["remote_sponsors"] = remote_items

    curated_names = {s.name for s in _select_curated(bundled_doc, cache_doc)}

    if afdian_names is not None:
        prior_afdian = {
            s.name: s
            for s in _parse_entries(cache_doc.get("afdian_sponsors"), allow_avatar=False)
        }
        updated_afdian: list[dict] = []
        seen: set[str] = set()
        for name in afdian_names:
            if name in seen or name in curated_names:
                continue
            seen.add(name)
            prev = prior_afdian.get(name)
            last_pay = prev.last_pay if (prev and prev.last_pay) else today
            updated_afdian.append({
                "name": name,
                "since": prev.since if prev else "",
                "last_pay": last_pay,
                "message": "",
                "avatar": "",
            })
        for name, prev in prior_afdian.items():
            if name in seen or name in curated_names:
                continue
            seen.add(name)
            updated_afdian.append(_entry_to_dict(prev))
        cache_doc["afdian_sponsors"] = updated_afdian

    cache_doc["schema"] = 1
    cache_doc["updated_at"] = today
    try:
        config._atomic_write_json(str(_cache_path()), cache_doc)
    except Exception as exc:  # noqa: BLE001 - read-only disk must not crash UI
        logger.debug("failed to write sponsor cache: %s", exc)

    after = sorted_sponsors()
    return {"ok": True, "changed": after != before, "sponsors": after}
