"""The sponsor honour roll (鸣谢名单).

Data lives in ``core/data/sponsors.json`` and is **bundled into the shipped
build** — this module never touches the network, so the honour roll works
offline and can never depend on GitHub or afdiancdn being reachable.

Design notes worth keeping:

* **Only Qt-free stdlib imports.** Keeping this module free of Qt means the
  parsing/sorting rules can be unit-tested without a QApplication.
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
* **Every failure degrades to an empty list.** A missing/corrupt data file must
  never take the settings window down with it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where "I want to sponsor too" points.
AFDIAN_URL = "https://afdian.com/a/touyimoyuebai"

_DATA_REL = Path("core") / "data" / "sponsors.json"


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


def load_sponsors() -> list[Sponsor]:
    """Read the honour roll. Any problem at all yields ``[]``.

    The caller is a settings dialog: a broken data file should quietly show the
    empty state, never raise. ``logger.debug`` keeps a trail so a packaged
    build stuck on the empty state is still diagnosable.
    """
    try:
        with open(_data_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - degrade, never propagate
        logger.debug("sponsor list unavailable: %s", exc)
        return []

    if not isinstance(raw, dict):
        logger.debug("sponsor list malformed: root is %s", type(raw).__name__)
        return []

    items = raw.get("sponsors")
    if not isinstance(items, list):
        logger.debug("sponsor list malformed: 'sponsors' is %s", type(items).__name__)
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
            avatar=_text(item.get("avatar")),
        )
        index = seen.get(name)
        if index is None:
            seen[name] = len(sponsors)
            sponsors.append(entry)
        else:
            sponsors[index] = _preferred(sponsors[index], entry)
    return sponsors


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
    return sorted(load_sponsors(), key=_sort_key, reverse=True)
