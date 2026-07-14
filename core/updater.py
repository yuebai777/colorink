"""Version tracking and GitHub-based update check for Colorink.

Only depends on the Python standard library (urllib, json) to avoid pulling
in any extra runtime requirement for the packaged EXE.

The current application version lives in ``APP_VERSION``. ``check_for_update``
queries the GitHub releases API for the latest release, compares the tag with
``APP_VERSION``, and returns a plain dict so the caller can render any UI
without needing to handle exceptions.
"""

import json
import urllib.request
import urllib.error

# Bump this when shipping a new release. Must match the Windows file version
# major.minor.patch (trailing build component is ignored for comparison).
APP_VERSION = "1.2.3"

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

    return {
        "current_version": APP_VERSION,
        "latest_version": tag,
        "release_url": data.get("html_url") or GITHUB_URL,
        "release_notes": (data.get("body") or "").strip(),
        "has_update": _normalize_version(tag) > _normalize_version(APP_VERSION),
    }