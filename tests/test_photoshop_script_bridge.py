"""Tests for the green/portable Photoshop hidden-CEP-panel file bridge."""

import os
import time

import pytest

from core.photoshop_script_bridge import (
    CMD_FILENAME,
    HEARTBEAT_FILENAME,
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    PANEL_VERSION,
    PANEL_VERSION_FILENAME,
    PhotoshopScriptBridge,
    STATE_FILENAME,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake APPDATA roaming tree + a fake Photoshop install dir."""
    ps_dir = tmp_path / "Adobe Photoshop Green"
    ps_dir.mkdir()
    roaming = tmp_path / "Roaming"
    settings = (roaming / "Adobe" / "Adobe Photoshop Green"
                / "Adobe Photoshop Green Settings")
    settings.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(roaming))
    return ps_dir, settings


@pytest.fixture
def bridge(env):
    ps_dir, _ = env
    return PhotoshopScriptBridge(str(ps_dir))


# ── Deployment ───────────────────────────────────────────────────────────

class TestDeploy:
    def test_deploy_creates_cep_extension_in_required(self, bridge):
        assert bridge.deploy() is True
        assert "Required" in bridge.dir
        assert "CEP" in bridge.dir
        # CEP requires the manifest inside a CSXS subfolder of the root.
        manifest = os.path.join(bridge.dir, "CSXS", MANIFEST_FILENAME)
        assert os.path.isfile(manifest)
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        assert "com.colorink.bridge" in content
        assert "PHXS" in content

    def test_manifest_uses_butler_pattern_that_auto_loads(self, bridge):
        """The manifest must mirror com.adobe.Butler.backend — the only
        structure proven to auto-load at startup on green builds:
        Version 7.0, AutoVisible false, StartOn applicationActivate,
        Type Custom, --enable-nodejs, no ScriptPath."""
        bridge.deploy()
        with open(os.path.join(bridge.dir, "CSXS", MANIFEST_FILENAME),
                  encoding="utf-8") as f:
            content = f.read()
        assert 'Version="7.0"' in content
        assert "<AutoVisible>false</AutoVisible>" in content
        assert "applicationActivate" in content
        assert "<Type>Custom</Type>" in content
        assert "--enable-nodejs" in content
        assert "ScriptPath" not in content

    def test_panel_uses_evalscript_without_node_fs(self, bridge):
        bridge.deploy()
        with open(os.path.join(bridge.dir, INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "setInterval" in html
        assert "window.__adobe_cep__.evalScript" in html
        assert "app.foregroundColor" in html
        assert "app.backgroundColor" in html
        assert "heartbeat.txt" in html
        assert "getSystemPath" in html

    def test_deploy_is_idempotent(self, bridge):
        assert bridge.deploy() is True
        manifest = os.path.join(bridge.dir, "CSXS", MANIFEST_FILENAME)
        first = open(manifest, encoding="utf-8").read()
        assert bridge.deploy() is True
        assert open(manifest, encoding="utf-8").read() == first

    def test_is_deployed_reflects_manifest(self, bridge):
        assert bridge.is_deployed() is False
        bridge.deploy()
        assert bridge.is_deployed() is True


# ── Write side ───────────────────────────────────────────────────────────

class TestSendColor:
    def test_send_color_writes_command_line(self, bridge):
        bridge.deploy()
        assert bridge.send_color("token-1", 1, 10, 20, 30) is True
        with open(os.path.join(bridge.dir, CMD_FILENAME), encoding="ascii") as f:
            assert f.read() == "token-1|1|10|20|30"

    def test_send_color_foreground_index_zero(self, bridge):
        bridge.deploy()
        bridge.send_color("t", 0, 1, 2, 3)
        with open(os.path.join(bridge.dir, CMD_FILENAME), encoding="ascii") as f:
            assert f.read() == "t|0|1|2|3"

    def test_send_color_leaves_no_temp_file(self, bridge):
        bridge.deploy()
        bridge.send_color("t", 0, 1, 2, 3)
        leftovers = [n for n in os.listdir(bridge.dir) if n.endswith(".tmp")]
        assert leftovers == []

    def test_send_color_stores_given_values(self, bridge):
        bridge.deploy()
        assert bridge.send_color("t", 0, 300, -5, 128) is True
        with open(os.path.join(bridge.dir, CMD_FILENAME), encoding="ascii") as f:
            assert f.read() == "t|0|300|-5|128"

    def test_send_swap_writes_swap_command(self, bridge):
        bridge.deploy()
        assert bridge.send_swap("swap-1") is True
        with open(os.path.join(bridge.dir, CMD_FILENAME), encoding="ascii") as f:
            assert f.read() == "swap-1|swap"

    def test_panel_claims_stale_command_on_load(self, bridge):
        """On load the panel must delete the leftover cmd.txt (a command
        from a previous session must never be re-applied after a PS
        restart) and gate polling until the claim completes."""
        bridge.deploy()
        with open(os.path.join(bridge.dir, INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "claimStaleCmd" in html
        assert "f.remove()" in html
        assert "if (!claimed) { return; }" in html

    def test_panel_supports_swap_command(self, bridge):
        bridge.deploy()
        with open(os.path.join(bridge.dir, INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "'swap'" in html
        assert "app.backgroundColor=t" in html


# ── Panel version (restart-Photoshop hint) ───────────────────────────────

class TestPanelVersion:
    def test_missing_panel_version_means_stale(self, bridge):
        """A running panel that never wrote panel_version.txt predates the
        versioned protocol — treat it as stale."""
        bridge.deploy()
        assert bridge.panel_version() is None

    def test_panel_version_reads_current_file(self, bridge):
        bridge.deploy()
        path = os.path.join(bridge.dir, PANEL_VERSION_FILENAME)
        with open(path, "w", encoding="ascii") as f:
            f.write(str(PANEL_VERSION))
        assert bridge.panel_version() == PANEL_VERSION

    def test_panel_version_garbage_is_stale(self, bridge):
        bridge.deploy()
        with open(os.path.join(bridge.dir, PANEL_VERSION_FILENAME),
                  "w", encoding="ascii") as f:
            f.write("oops")
        assert bridge.panel_version() is None

    def test_index_template_writes_panel_version(self, bridge):
        """The deployed panel must write panel_version.txt on every poll,
        and the embedded version must match the Python constant."""
        bridge.deploy()
        with open(os.path.join(bridge.dir, INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "panel_version.txt" in html
        assert f"pv.write('{PANEL_VERSION}')" in html


# ── Read side ────────────────────────────────────────────────────────────

class TestReadState:
    def test_state_none_before_panel_writes(self, bridge):
        bridge.deploy()
        assert bridge.read_state() is None

    def test_state_parses_fg_and_bg(self, bridge):
        bridge.deploy()
        with open(os.path.join(bridge.dir, STATE_FILENAME),
                  "w", encoding="ascii") as f:
            f.write("255|0|0|0|128|255")
        state = bridge.read_state()
        assert state == {
            "fg": {"r": 255, "g": 0, "b": 0},
            "bg": {"r": 0, "g": 128, "b": 255},
        }

    def test_state_tolerates_solidcolor_float_noise(self, bridge):
        """Photoshop SolidColor reports float values (192.0000037...);
        the bridge must round them to ints."""
        bridge.deploy()
        with open(os.path.join(bridge.dir, STATE_FILENAME),
                  "w", encoding="ascii") as f:
            f.write("192.000003755093|48.0000009387732|56.000000461936|255|255|255")
        state = bridge.read_state()
        assert state["fg"] == {"r": 192, "g": 48, "b": 56}

    def test_state_ignores_garbage(self, bridge):
        bridge.deploy()
        with open(os.path.join(bridge.dir, STATE_FILENAME),
                  "w", encoding="ascii") as f:
            f.write("not|a|valid|state")
        assert bridge.read_state() is None


# ── Liveness ─────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_heartbeat_absent_means_dead(self, bridge):
        bridge.deploy()
        assert bridge.heartbeat_age() is None
        assert bridge.is_alive() is False

    def test_fresh_heartbeat_is_alive(self, bridge):
        bridge.deploy()
        path = os.path.join(bridge.dir, HEARTBEAT_FILENAME)
        with open(path, "w", encoding="ascii") as f:
            f.write(str(int(time.time() * 1000)))
        assert bridge.is_alive() is True

    def test_stale_heartbeat_is_dead(self, bridge):
        bridge.deploy()
        path = os.path.join(bridge.dir, HEARTBEAT_FILENAME)
        with open(path, "w", encoding="ascii") as f:
            f.write("0")
        old = time.time() - 60
        os.utime(path, (old, old))
        assert bridge.is_alive() is False
        assert bridge.heartbeat_age() is not None
        assert bridge.heartbeat_age() > 8.0
