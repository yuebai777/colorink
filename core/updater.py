"""Version tracking and GitHub-based update check for Colorink.

Only depends on the Python standard library (urllib, json) to avoid pulling
in any extra runtime requirement for the packaged EXE.

The current application version lives in ``APP_VERSION``. ``check_for_update``
queries the GitHub releases API for the latest release, compares the tag with
``APP_VERSION``, and returns a plain dict so the caller can render any UI
without needing to handle exceptions.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# Bump this when shipping a new release. Must match the Windows file version
# major.minor.patch (trailing build component is ignored for comparison).
APP_VERSION = "1.6.6"

# Author's Bilibili homepage — used by the "关于作者" button.
BILIBILI_URL = (
    "https://space.bilibili.com/3546861965150461?spm_id_from=333.788.0.0"
)

GITHUB_OWNER = "yuebai777"
GITHUB_REPO = "colorink"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"


def _normalize_version(v: str) -> list[int]:
    """Normalize a version tag like 'v1.2.3' or '1.2.3.0' into [1, 2, 3].

    Trailing zero components are stripped so '1.0.0' and '1.0.0.0' compare
    equal. Non-numeric suffixes (e.g. '-beta') break out at the first
    non-digit character to keep the comparison robust against pre-release
    tags without crashing.
    """
    s = (v or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in s.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    # Drop trailing zeros so "1.0.0" == "1.0.0.0"
    while parts and parts[-1] == 0:
        parts.pop()
    return parts


def check_for_update(timeout: float = 8.0) -> dict:
    """Query GitHub for the latest release and compare against ``APP_VERSION``.

    Returns a dict on success:
        {
            "current_version": str,
            "latest_version": str,   # tag_name from GitHub, e.g. "v1.2.0"
            "release_url": str,     # html_url of the release
            "release_notes": str,    # body of the release (may be "")
            "has_update": bool,
        }
    On failure returns ``{"error": "<message>"}`` so the caller never has to
    catch exceptions.
    """
    req = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Colorink-Updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": f"GitHub 返回 HTTP {e.code}，请稍后重试"}
    except urllib.error.URLError as e:
        return {"error": f"网络异常: {e.reason}"}
    except Exception as e:  # pragma: no cover - defensive
        return {"error": f"获取更新失败: {e}"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "GitHub 响应解析失败"}

    tag = data.get("tag_name", "")
    if not tag:
        return {"error": "未在响应中找到版本号"}

    assets = [
        {"name": a.get("name"), "url": a.get("browser_download_url"),
         "size": a.get("size")}
        for a in data.get("assets", [])
        if a.get("browser_download_url")
    ]

    return {
        "current_version": APP_VERSION,
        "latest_version": tag,
        "release_url": data.get("html_url") or GITHUB_URL,
        "release_notes": (data.get("body") or "").strip(),
        "has_update": _normalize_version(tag) > _normalize_version(APP_VERSION),
        "assets": assets,
    }


def find_installer_asset(assets: list[dict], name_hint: str = "colorink") -> dict | None:
    """Pick the Windows installer asset from a release asset list.

    Prefers the onefile EXE, then any EXE whose name mentions *name_hint*,
    then any EXE. Returns ``None`` when no EXE asset exists. Entries without a
    usable ``name`` are skipped so malformed assets never crash the picker.
    """
    exes = [a for a in assets if (a.get("name") or "").lower().endswith(".exe")]
    if not exes:
        return None
    hint = (name_hint or "").lower()
    for a in exes:
        if "onefile" in (a.get("name") or "").lower():
            return a
    for a in exes:
        if hint in (a.get("name") or "").lower():
            return a
    return exes[0]


def download_release(
    url: str,
    dest_path: str,
    total_size: int | None = None,
    progress_cb=None,
    timeout: float = 60.0,
) -> dict:
    """Download a release asset from *url* to *dest_path* in chunks.

    Returns ``{"path": dest_path, "bytes": downloaded}`` on success, or
    ``{"error": message}`` on failure. ``progress_cb(downloaded, total)`` is
    called after each chunk when provided; ``total`` is *total_size* or the
    Content-Length header when available, else 0.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Colorink-Updater"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = total_size if total_size is not None else _content_length(resp)
            downloaded = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except urllib.error.HTTPError as e:
        return {"error": f"下载失败: HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"网络异常: {e.reason}"}
    except Exception as e:  # pragma: no cover - defensive
        return {"error": f"下载失败: {e}"}
    return {"path": dest_path, "bytes": downloaded}


def _content_length(resp) -> int:
    """Read the Content-Length header as int, defaulting to 0."""
    try:
        return int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def can_self_replace(current_exe: str | None = None, frozen: bool | None = None) -> bool:
    """Return True when the running app can replace itself in place.

    Only single-file (onefile) builds qualify: onedir builds ship an
    ``_internal`` folder next to the exe, so swapping just the exe would
    leave the bundled libraries stale and break the app.

    ``frozen`` defaults to the real ``sys.frozen`` so the check is a no-op
    under the source tree (nothing to replace), but tests can inject it.
    """
    exe = getattr(sys, "executable", "") if current_exe is None else current_exe
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not exe or not frozen:
        return False
    internal = os.path.join(os.path.dirname(exe), "_internal")
    return not os.path.isdir(internal)


def build_self_replace_script(new_exe: str, current_exe: str) -> str:
    """Return the text of a Windows batch helper that replaces *current_exe*
    with *new_exe* and relaunches once the calling process has exited.

    The helper waits briefly for the running exe's file lock to be released,
    moves the new file over the old one, then starts the (now updated) app
    and deletes itself. If the move fails (e.g. the lock is still held), it
    falls back to launching the new file directly so the user still gets the
    update even if the old file stays put.
    """
    new_exe = os.path.abspath(new_exe)
    current_exe = os.path.abspath(current_exe)
    return "\r\n".join([
        "@echo off",
        "rem Colorink self-update helper (auto-generated).",
        "ping 127.0.0.1 -n 3 >nul",
        f"move /Y \"{new_exe}\" \"{current_exe}\" >nul 2>&1",
        f"if exist \"{new_exe}\" (",
        f"    start \"\" \"{new_exe}\"",
        ") else (",
        f"    start \"\" \"{current_exe}\"",
        ")",
        "del \"%~f0\"",
    ]) + "\r\n"


def launch_self_replace(new_exe: str, current_exe: str) -> str | None:
    """Write the self-replace helper next to *current_exe* and spawn it
    detached. Returns the batch path on success, ``None`` on failure. The
    caller is responsible for exiting the current process afterwards."""
    try:
        bat_dir = os.path.dirname(os.path.abspath(current_exe))
        os.makedirs(bat_dir, exist_ok=True)
        bat_path = os.path.join(bat_dir, "colorink-update.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(build_self_replace_script(new_exe, current_exe))
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=flags,
            close_fds=True,
        )
        return bat_path
    except Exception:
        return None
