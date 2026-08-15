"""Version tracking and GitHub-based update check for Colorink.

Only depends on the Python standard library (urllib, json) to avoid pulling
in any extra runtime requirement for the packaged EXE.

The current application version lives in ``APP_VERSION``. ``check_for_update``
queries the GitHub releases API for the latest release, compares the tag with
``APP_VERSION``, and returns a plain dict so the caller can render any UI
without needing to handle exceptions.
"""

import hashlib
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


def _github_headers() -> dict:
    """Headers for the GitHub API, including an optional auth token.

    Unauthenticated requests share a 60/hour/IP quota; a token (set via
    ``COLORINK_GITHUB_TOKEN`` or ``GITHUB_TOKEN``) raises that to 5000/hour,
    which matters on shared/NAT connections.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Colorink-Updater",
    }
    token = os.environ.get("COLORINK_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
    req = urllib.request.Request(RELEASES_API, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {
                "error": (
                    "GitHub API 限流 (403)。未认证请求每小时仅 60 次，"
                    "可稍后重试或设置 COLORINK_GITHUB_TOKEN 提升配额。"
                )
            }
        if e.code == 404:
            return {"error": "未在 GitHub 上找到发布信息 (404)"}
        return {"error": "GitHub 返回 HTTP {detail}，请稍后重试", "error_detail": str(e.code)}
    except urllib.error.URLError as e:
        return {"error": "网络异常：{detail}", "error_detail": str(e.reason)}
    except Exception as e:  # pragma: no cover - defensive
        return {"error": "获取更新失败：{detail}", "error_detail": str(e)}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "GitHub 响应解析失败"}

    # 防御畸形 200 响应（代理/镜像返回数组、空对象等）——函数契约是
    # "caller never has to handle exceptions"，解析后的访问必须同样兜底。
    if not isinstance(data, dict):
        return {"error": "GitHub 响应解析失败"}

    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return {"error": "未在响应中找到版本号"}

    assets_raw = data.get("assets")
    if not isinstance(assets_raw, list):
        assets_raw = []
    assets = [
        {"name": a.get("name"), "url": a.get("browser_download_url"),
         "size": a.get("size"), "digest": a.get("digest")}
        for a in assets_raw
        if isinstance(a, dict) and a.get("browser_download_url")
    ]

    body = data.get("body") or ""
    if not isinstance(body, str):
        body = ""
    release_url = data.get("html_url") or GITHUB_URL
    if not isinstance(release_url, str):
        release_url = GITHUB_URL

    return {
        "current_version": APP_VERSION,
        "latest_version": tag,
        "release_url": release_url,
        "release_notes": body.strip(),
        "has_update": _normalize_version(tag) > _normalize_version(APP_VERSION),
        "assets": assets,
    }


def find_installer_asset(assets: list[dict], name_hint: str = "colorink", flavor: str = "onefile") -> dict | None:
    """Pick the Windows installer asset from a release asset list.

    Prefers the onefile EXE, then — when *flavor* is "onefile" — the largest
    EXE (the self-contained build dwarfs the onedir stub, so a name-only
    match can never hand the stub to a running onefile build), then any EXE
    whose name mentions *name_hint*, then any EXE. Returns ``None`` when no
    EXE asset exists. Entries without a usable ``name`` are skipped so
    malformed assets never crash the picker.
    """
    exes = [a for a in assets if (a.get("name") or "").lower().endswith(".exe")]
    if not exes:
        return None
    hint = (name_hint or "").lower()
    for a in exes:
        if "onefile" in (a.get("name") or "").lower():
            return a
    if flavor == "onefile":
        sized = [a for a in exes if a.get("size")]
        if sized:
            return max(sized, key=lambda a: a["size"])
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
    sha256: str | None = None,
) -> dict:
    """Download a release asset from *url* to *dest_path* in chunks.

    Writes to ``dest_path + ".part"`` first and only renames it into place
    once the byte count and (optionally) SHA-256 checksum both verify, so a
    truncated or tampered file is never handed to the installer. Returns
    ``{"path": dest_path, "bytes": downloaded}`` on success, or
    ``{"error": message}`` on failure. ``progress_cb(downloaded, total)`` is
    called after each chunk when provided; ``total`` is *total_size* or the
    Content-Length header when available, else 0.
    """
    part_path = dest_path + ".part"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Colorink-Updater"},
    )
    downloaded = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = total_size if total_size is not None else _content_length(resp)
            with open(part_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except urllib.error.HTTPError as e:
        _remove_file(part_path)
        return {"error": "下载失败：{detail}", "error_detail": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        _remove_file(part_path)
        return {"error": "网络异常：{detail}", "error_detail": str(e.reason)}
    except Exception as e:  # pragma: no cover - defensive
        _remove_file(part_path)
        return {"error": "下载失败：{detail}", "error_detail": str(e)}

    # A partial download must never reach the self-replace/install step.
    if total_size and downloaded != total_size:
        _remove_file(part_path)
        return {
            "error": "下载不完整：{detail}",
            "error_detail": f"收到 {downloaded} 字节，应为 {total_size} 字节",
        }

    if sha256 is not None and _sha256_file(part_path) != sha256.lower():
        _remove_file(part_path)
        return {"error": "校验失败：下载文件与发布校验和不一致"}

    try:
        os.replace(part_path, dest_path)
    except OSError as e:  # pragma: no cover - defensive
        _remove_file(part_path)
        return {"error": "保存失败：{detail}", "error_detail": str(e)}
    return {"path": dest_path, "bytes": downloaded}


def _content_length(resp) -> int:
    """Read the Content-Length header as int, defaulting to 0."""
    try:
        return int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def _sha256_file(path: str) -> str:
    """Return the lowercase hex SHA-256 of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_file(path: str) -> None:
    """Best-effort delete of a (partial) download, never raising."""
    try:
        os.remove(path)
    except OSError:
        pass


def can_self_replace(current_exe: str | None = None, frozen: bool | None = None) -> bool:
    """Return True when the running app can replace itself in place.

    Only single-file (onefile) builds qualify: onedir builds ship an
    ``_internal`` folder next to the exe, so swapping just the exe would
    leave the bundled libraries stale and break the app. The exe's directory
    must also be writable — otherwise the post-update ``move`` would fail
    (e.g. under ``C:\\Program Files`` without elevation) and silently degrade
    to running the downloaded copy from Downloads.

    ``frozen`` defaults to the real ``sys.frozen`` so the check is a no-op
    under the source tree (nothing to replace), but tests can inject it.
    """
    exe = getattr(sys, "executable", "") if current_exe is None else current_exe
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not exe or not frozen:
        return False
    exe_dir = os.path.dirname(exe)
    if os.path.isdir(os.path.join(exe_dir, "_internal")):
        return False
    return _dir_writable(exe_dir)


def _dir_writable(directory: str) -> bool:
    """True when *directory* exists and a file can be created then removed
    inside it — a stronger signal than ``os.access`` under Windows ACLs."""
    if not directory or not os.path.isdir(directory):
        return False
    probe = os.path.join(directory, f".colorink-write-{os.getpid()}")
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def build_flavor(exe: str | None = None) -> str:
    """Return "onedir" when *exe* sits next to a PyInstaller ``_internal``
    folder, else "onefile".

    Lets the updater avoid downloading a onedir stub over a running onefile
    build (or vice versa). Under the source tree this reports "onefile";
    callers gate self-replace on ``sys.frozen`` separately.
    """
    exe = getattr(sys, "executable", "") if exe is None else exe
    exe_dir = os.path.dirname(os.path.abspath(exe)) if exe else ""
    if exe_dir and os.path.isdir(os.path.join(exe_dir, "_internal")):
        return "onedir"
    return "onefile"


def build_self_replace_script(new_exe: str, current_exe: str) -> str:
    """Return the text of a Windows batch helper that replaces *current_exe*
    with *new_exe* and relaunches once the calling process has exited.

    The helper waits briefly for the running exe's file lock to be released,
    moves the new file over the old one, then starts the (now updated) app
    and deletes itself. If the move fails (e.g. the lock is still held), it
    falls back to launching the new file directly so the user still gets the
    update even if the old file stays put.
    """
    new_exe = os.path.abspath(new_exe).replace("%", "%%")
    current_exe = os.path.abspath(current_exe).replace("%", "%%")
    return "\r\n".join([
        "@echo off",
        "rem Colorink self-update helper (auto-generated).",
        # cmd.exe 默认按 OEM 代码页（如 GBK）逐行解码 batch 文件；脚本以
        # UTF-8 写入，非 ASCII（中文用户名/安装路径）会被解析成乱码导致
        # move/start 失败。chcp 65001 后后续行按 UTF-8 解码。
        "chcp 65001 >nul",
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
