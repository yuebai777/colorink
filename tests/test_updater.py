"""Update check enhancements: asset selection, asset list in the check
result, and chunked release download (against a local HTTP server).
"""

import functools
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


# ── check_for_update asset extraction ──────────────────────────────────────


def test_check_for_update_extracts_assets():
    fake_response = _FakeResponse(json.dumps({
        "tag_name": "v1.6.7",
        "html_url": "https://github.com/yuebai777/colorink/releases/tag/v1.6.7",
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


class _FakeResponse:
    def __init__(self, raw: str):
        self._raw = raw.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._raw
