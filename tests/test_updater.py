"""Update check enhancements: asset selection, asset list in the check
result, and chunked release download (against a local HTTP server).
"""

import functools
import hashlib
import http.server
import json
import threading
from unittest.mock import patch

import pytest

from core import updater


# ── Asset selection ────────────────────────────────────────────────────────


def test_find_installer_asset_prefers_onefile_exe():
    assets = [
        {"name": "Colorink.zip", "url": "https://x/Colorink.zip"},
        {"name": "Colorink.exe", "url": "https://x/Colorink.exe"},
        {"name": "Colorink Onefile.exe", "url": "https://x/onefile.exe"},
    ]

    picked = updater.find_installer_asset(assets)

    assert picked is not None
    assert picked["name"] == "Colorink Onefile.exe"


def test_find_installer_asset_falls_back_to_any_exe():
    assets = [
        {"name": "Colorink.zip", "url": "https://x/Colorink.zip"},
        {"name": "Colorink_1.6.5.exe", "url": "https://x/Colorink_1.6.5.exe"},
    ]

    picked = updater.find_installer_asset(assets)

    assert picked is not None
    assert picked["name"] == "Colorink_1.6.5.exe"


def test_find_installer_asset_returns_none_without_exe():
    assets = [{"name": "Colorink.zip", "url": "https://x/Colorink.zip"}]

    assert updater.find_installer_asset(assets) is None


def test_find_installer_asset_ignores_missing_names():
    assets = [
        {"url": "https://x/a.exe"},
        {"name": "Colorink.exe", "url": "https://x/b.exe"},
    ]

    picked = updater.find_installer_asset(assets)

    assert picked is not None
    assert picked["name"] == "Colorink.exe"


def test_find_installer_asset_onefile_prefers_largest_exe():
    """A onefile build must never download the small onedir stub, so when
    no asset is explicitly named onefile the largest EXE wins (size signal)."""
    assets = [
        {"name": "Colorink.exe", "url": "https://x/stub.exe", "size": 6_578_040},
        {"name": "Colorink_setup.exe", "url": "https://x/full.exe", "size": 39_303_661},
    ]

    picked = updater.find_installer_asset(assets, flavor="onefile")

    assert picked is not None
    assert picked["name"] == "Colorink_setup.exe"


def test_find_installer_asset_onedir_prefers_onedir_zip():
    assets = [
        {"name": "Colorink.exe", "url": "https://x/Colorink.exe", "size": 39_318_700},
        {"name": "Colorink-Onedir.zip", "url": "https://x/Colorink-Onedir.zip", "size": 39_403_439},
    ]

    picked = updater.find_installer_asset(assets, flavor="onedir")

    assert picked is not None
    assert picked["name"] == "Colorink-Onedir.zip"


def test_find_installer_asset_onedir_falls_back_to_exe_without_zip():
    assets = [
        {"name": "Colorink.exe", "url": "https://x/Colorink.exe", "size": 39_318_700},
    ]

    picked = updater.find_installer_asset(assets, flavor="onedir")

    assert picked is not None
    assert picked["name"] == "Colorink.exe"


def test_find_installer_asset_onedir_ignores_unrelated_source_zip():
    assets = [
        {"name": "Source code.zip", "url": "https://x/source.zip", "size": 999},
        {"name": "Colorink-Onedir.zip", "url": "https://x/Colorink-Onedir.zip", "size": 39_403_439},
    ]

    picked = updater.find_installer_asset(assets, flavor="onedir")

    assert picked is not None
    assert picked["name"] == "Colorink-Onedir.zip"


def test_find_installer_asset_onedir_ignores_github_source_zip_and_falls_back_to_exe():
    """GitHub auto-generates ``colorink-<tag>.zip`` source archives.  Without
    a real onedir zip the updater must fall back to the EXE, not download the
    source archive and later destroy the onedir installation."""
    assets = [
        {"name": "colorink-1.6.7.zip", "url": "https://x/source.zip", "size": 12_345},
        {"name": "Colorink.exe", "url": "https://x/Colorink.exe", "size": 39_318_700},
    ]

    picked = updater.find_installer_asset(assets, flavor="onedir")

    assert picked is not None
    assert picked["name"] == "Colorink.exe"


def test_find_installer_asset_onedir_accepts_windows_build_zip():
    assets = [
        {"name": "colorink-1.6.7.zip", "url": "https://x/source.zip", "size": 12_345},
        {"name": "Colorink-Windows.zip", "url": "https://x/Colorink-Windows.zip", "size": 39_403_439},
        {"name": "Colorink.exe", "url": "https://x/Colorink.exe", "size": 39_318_700},
    ]

    picked = updater.find_installer_asset(assets, flavor="onedir")

    assert picked is not None
    assert picked["name"] == "Colorink-Windows.zip"


def test_build_flavor_detects_onedir(tmp_path):
    exe_dir = tmp_path / "Colorink"
    exe_dir.mkdir()
    (exe_dir / "_internal").mkdir()
    assert updater.build_flavor(str(exe_dir / "Colorink.exe")) == "onedir"


def test_build_flavor_defaults_to_onefile(tmp_path):
    assert updater.build_flavor(str(tmp_path / "Colorink.exe")) == "onefile"


# ── check_for_update asset extraction ──────────────────────────────────────


def test_check_for_update_extracts_assets():
    fake_response = _FakeResponse(json.dumps({
        "tag_name": "v1.7.1",
        "html_url": "https://github.com/yuebai777/colorink/releases/tag/v1.7.1",
        "body": "notes",
        "assets": [
            {"name": "Colorink.exe", "browser_download_url": "https://x/a.exe",
             "size": 100},
            {"name": "Colorink.zip", "browser_download_url": "https://x/a.zip",
             "size": 200},
            {"name": "broken", "browser_download_url": "", "size": 0},
        ],
    }))

    with patch.object(updater.urllib.request, "urlopen", return_value=fake_response):
        result = updater.check_for_update()

    assert result["has_update"] is True
    assert [a["name"] for a in result["assets"]] == ["Colorink.exe", "Colorink.zip"]


# ── Download ───────────────────────────────────────────────────────────────


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def http_server():
    payload = b"colorink-payload-" * 4096
    handler = functools.partial(_StaticHandler)
    handler = type("BoundHandler", (_StaticHandler,), {"payload": payload})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, payload
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_download_release_saves_file(http_server, tmp_path):
    server, payload = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/Colorink.exe"
    dest = tmp_path / "Colorink.exe"

    result = updater.download_release(url, str(dest))

    assert "error" not in result
    assert result["path"] == str(dest)
    assert result["bytes"] == len(payload)
    assert dest.read_bytes() == payload


def test_download_release_reports_progress(http_server, tmp_path):
    server, payload = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/Colorink.exe"
    dest = tmp_path / "Colorink.exe"
    progress = []

    result = updater.download_release(
        url, str(dest), total_size=len(payload),
        progress_cb=lambda done, total: progress.append((done, total)),
    )

    assert "error" not in result
    assert progress, "progress callback never fired"
    assert progress[-1] == (len(payload), len(payload))


def test_download_release_reports_404(http_server, tmp_path):
    server, _ = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/missing"
    dest = tmp_path / "Colorink.exe"

    result = updater.download_release(url, str(dest))

    assert "error" in result
    assert not dest.exists()


def test_download_release_rejects_size_mismatch(http_server, tmp_path):
    server, payload = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/Colorink.exe"
    dest = tmp_path / "Colorink.exe"

    result = updater.download_release(url, str(dest), total_size=len(payload) + 10)

    assert "error" in result
    assert not dest.exists()
    assert not (tmp_path / "Colorink.exe.part").exists()


def test_download_release_rejects_bad_sha256(http_server, tmp_path):
    server, _ = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/Colorink.exe"
    dest = tmp_path / "Colorink.exe"

    result = updater.download_release(url, str(dest), sha256="0" * 64)

    assert "error" in result
    assert not dest.exists()
    assert not (tmp_path / "Colorink.exe.part").exists()


def test_download_release_accepts_matching_sha256(http_server, tmp_path):
    server, payload = http_server
    url = f"http://127.0.0.1:{server.server_address[1]}/Colorink.exe"
    dest = tmp_path / "Colorink.exe"
    digest = hashlib.sha256(payload).hexdigest()

    result = updater.download_release(url, str(dest), sha256=digest)

    assert "error" not in result
    assert dest.read_bytes() == payload
    assert not (tmp_path / "Colorink.exe.part").exists()


class _FakeResponse:
    def __init__(self, raw: str):
        self._raw = raw.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._raw
