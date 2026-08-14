# Crash marker for the next-launch previous-run-crash prompt.
#
# The global exception hook (main._log_exception) already appends a
# traceback to stderr.log. That file grows forever and is easy to miss,
# so the hook also drops a small JSON marker into the user data dir. On
# the next launch the app detects it, offers to copy/open the log, and
# clears the marker so the same crash is never announced twice.
#
# Only depends on the standard library so it stays importable from main
# before Qt or the rest of the app is up.

import json
import os
import time

CRASH_MARKER_NAME = "crash-marker.json"

# How old a marker may be before it is considered stale (7 days). A marker
# that survives this long is either ancient or the app crashed before
# reaching the detect-and-clear step repeatedly; either way we stop nagging.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _default_dir() -> str:
    from core import config
    return config.get_user_data_dir()


def marker_path(user_data_dir: str | None = None) -> str:
    """Absolute path to the crash marker file."""
    return os.path.join(user_data_dir or _default_dir(), CRASH_MARKER_NAME)


def write_crash_marker(traceback_text: str, user_data_dir: str | None = None,
                       log_path: str | None = None, timestamp: float | None = None) -> dict:
    """Write a crash marker and return the dict that was persisted.

    log_path records where the full traceback was appended so the prompt
    can offer to open it; it is optional and never fails the write.
    """
    d = user_data_dir or _default_dir()
    os.makedirs(d, exist_ok=True)
    data = {
        "timestamp": timestamp if timestamp is not None else time.time(),
        "traceback": traceback_text or "",
        "log_path": log_path,
    }
    with open(os.path.join(d, CRASH_MARKER_NAME), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def read_crash_marker(user_data_dir: str | None = None) -> dict | None:
    """Read the marker, or None if missing / corrupt / not a valid marker."""
    p = marker_path(user_data_dir)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or "traceback" not in data:
        return None
    return data


def clear_crash_marker(user_data_dir: str | None = None) -> None:
    """Remove the marker; never raises (missing marker is a no-op)."""
    try:
        os.remove(marker_path(user_data_dir))
    except FileNotFoundError:
        pass
    except Exception:
        pass


def detect_previous_crash(user_data_dir: str | None = None, now: float | None = None,
                          max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> dict | None:
    """Return the crash info for a *fresh* marker, else None.

    A marker with a missing/unparseable timestamp is treated as present so
    a corrupt timestamp can never silently swallow a real crash report.
    """
    data = read_crash_marker(user_data_dir)
    if data is None:
        return None
    try:
        ts = float(data.get("timestamp"))
    except (TypeError, ValueError):
        return data
    now = time.time() if now is None else float(now)
    if now - ts > max_age_seconds:
        return None
    return data
