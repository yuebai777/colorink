"""Tests for the user-level CEP bridge (single sync path for all PS)."""

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
def bridge(tmp_path, monkeypatch):
    """Point APPDATA at a temp dir so the user-level CEP folder is local."""
    appdata = tmp_path / "Roaming"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    return PhotoshopScriptBridge()


def _bridge_dir(bridge) -> str:
    return bridge.dir


# ── Deployment ───────────────────────────────────────────────────────────

class TestDeploy:
    def test_deploy_creates_user_level_cep_extension(self, bridge):
        assert bridge.deploy() is True
        d = _bridge_dir(bridge)
        assert "Adobe" in d and "CEP" in d and "extensions" in d
        # CEP requires the manifest inside a CSXS subfolder of the root.
        manifest = os.path.join(d, "CSXS", MANIFEST_FILENAME)
        assert os.path.isfile(manifest)
        with open(manifest, encoding="utf-8") as f:
            content = f.read()
        assert "com.colorink.bridge" in content
        assert "PHXS" in content

    def test_manifest_uses_butler_pattern_that_auto_loads(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), "CSXS", MANIFEST_FILENAME),
                  encoding="utf-8") as f:
            content = f.read()
        assert 'Version="7.0"' in content
        assert "<AutoVisible>false</AutoVisible>" in content
        assert "applicationActivate" in content
        assert "<Type>Custom</Type>" in content
        assert "--enable-nodejs" in content
        assert "ScriptPath" not in content

    def test_panel_uses_evalscript_and_node_fs(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "setInterval" in html
        assert "window.__adobe_cep__.evalScript" in html
        assert "require('fs')" in html
        assert "app.foregroundColor" in html
        assert "app.backgroundColor" in html
        assert "getSystemPath" in html

    def test_panel_routes_by_pid(self, bridge):
        """The panel must resolve its PID (appPid / $.pid) and use it for
        per-instance routing, with a shared-file fallback."""
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        # PID resolution: CEP host environment first, then $.pid.
        assert "appPid" in html
        assert "$.pid" in html
        assert "pidSuffix" in html
        # Node path filters by myPid; fallback path filters by my==''||parts[1]==my
        assert "parts[1] === myPid" in html or "parts[1]==my" in html
        assert "lastToken" in html
        # Per-PID + shared file names both present (suffix pattern).
        assert "'/state" in html and "suff" in html

    def test_panel_claims_stale_command_on_load(self, bridge):
        """On load the panel must only delete a cmd.txt that predates the
        panel itself (mtime < panel start), never a live command."""
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "claimStaleCmd" in html
        assert "panelStart" in html
        assert "if (!claimed) { return; }" in html

    def test_panel_supports_swap_command(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "'swap'" in html
        assert "app.backgroundColor=t" in html

    def test_deploy_is_idempotent(self, bridge):
        assert bridge.deploy() is True
        manifest = os.path.join(_bridge_dir(bridge), "CSXS", MANIFEST_FILENAME)
        first = open(manifest, encoding="utf-8").read()
        assert bridge.deploy() is True
        assert open(manifest, encoding="utf-8").read() == first

    def test_is_deployed_reflects_manifest(self, bridge):
        assert bridge.is_deployed() is False
        bridge.deploy()
        assert bridge.is_deployed() is True

    def test_remove_deletes_extension(self, bridge):
        bridge.deploy()
        assert bridge.remove() is True
        assert bridge.is_deployed() is False


# ── Write side ───────────────────────────────────────────────────────────

class TestSendColor:
    def test_send_color_writes_pid_command_line(self, bridge):
        bridge.deploy()
        assert bridge.send_color("token-1", 1234, 1, 10, 20, 30) is True
        with open(os.path.join(_bridge_dir(bridge), CMD_FILENAME),
                  encoding="ascii") as f:
            assert f.read() == "token-1|1234|1|10|20|30"

    def test_send_color_foreground_index_zero(self, bridge):
        bridge.deploy()
        bridge.send_color("t", 5678, 0, 1, 2, 3)
        with open(os.path.join(_bridge_dir(bridge), CMD_FILENAME),
                  encoding="ascii") as f:
            assert f.read() == "t|5678|0|1|2|3"

    def test_send_color_leaves_no_temp_file(self, bridge):
        bridge.deploy()
        bridge.send_color("t", 1, 0, 1, 2, 3)
        leftovers = [n for n in os.listdir(_bridge_dir(bridge))
                     if n.endswith(".tmp")]
        assert leftovers == []

    def test_send_color_stores_given_values(self, bridge):
        bridge.deploy()
        assert bridge.send_color("t", 99, 0, 300, -5, 128) is True
        with open(os.path.join(_bridge_dir(bridge), CMD_FILENAME),
                  encoding="ascii") as f:
            assert f.read() == "t|99|0|300|-5|128"

    def test_send_swap_writes_swap_command(self, bridge):
        bridge.deploy()
        assert bridge.send_swap("swap-1", 4321) is True
        with open(os.path.join(_bridge_dir(bridge), CMD_FILENAME),
                  encoding="ascii") as f:
            assert f.read() == "swap-1|4321|swap"


# ── Panel version (restart-Photoshop hint) ───────────────────────────────

class TestPanelVersion:
    def test_missing_panel_version_means_stale(self, bridge):
        bridge.deploy()
        assert bridge.panel_version(1234) is None

    def test_panel_version_reads_per_pid_file(self, bridge):
        bridge.deploy()
        path = os.path.join(_bridge_dir(bridge),
                            f"{PANEL_VERSION_FILENAME}_1234.txt")
        with open(path, "w", encoding="ascii") as f:
            f.write(str(PANEL_VERSION))
        assert bridge.panel_version(1234) == PANEL_VERSION
        # A different pid stays stale.
        assert bridge.panel_version(9999) is None

    def test_panel_version_garbage_is_stale(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge),
                               f"{PANEL_VERSION_FILENAME}_1.txt"),
                  "w", encoding="ascii") as f:
            f.write("oops")
        assert bridge.panel_version(1) is None

    def test_index_template_writes_panel_version(self, bridge):
        """The deployed panel must write panel_version (per-PID or
        shared) and the embedded version must match the Python constant."""
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge), INDEX_FILENAME),
                  encoding="utf-8") as f:
            html = f.read()
        assert "'/panel_version" in html
        assert f"pv.write('{PANEL_VERSION}')" in html


# ── Read side ────────────────────────────────────────────────────────────

class TestReadState:
    def test_state_none_before_panel_writes(self, bridge):
        bridge.deploy()
        assert bridge.read_state(1234) is None

    def test_state_parses_fg_and_bg(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge),
                               f"{STATE_FILENAME}_1234.txt"),
                  "w", encoding="ascii") as f:
            f.write("255|0|0|0|128|255")
        state = bridge.read_state(1234)
        assert state == {
            "fg": {"r": 255, "g": 0, "b": 0},
            "bg": {"r": 0, "g": 128, "b": 255},
        }
        assert bridge.read_state(9999) is None

    def test_state_tolerates_solidcolor_float_noise(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge),
                               f"{STATE_FILENAME}_1.txt"),
                  "w", encoding="ascii") as f:
            f.write("192.000003755093|48.0000009387732|56.000000461936|255|255|255")
        state = bridge.read_state(1)
        assert state["fg"] == {"r": 192, "g": 48, "b": 56}

    def test_state_ignores_garbage(self, bridge):
        bridge.deploy()
        with open(os.path.join(_bridge_dir(bridge),
                               f"{STATE_FILENAME}_1.txt"),
                  "w", encoding="ascii") as f:
            f.write("not|a|valid|state")
        assert bridge.read_state(1) is None


# ── Liveness ─────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_heartbeat_absent_means_dead(self, bridge):
        bridge.deploy()
        assert bridge.heartbeat_age(1234) is None
        assert bridge.is_alive(1234) is False

    def test_fresh_heartbeat_is_alive(self, bridge):
        bridge.deploy()
        path = os.path.join(_bridge_dir(bridge),
                            f"{HEARTBEAT_FILENAME}_1234.txt")
        with open(path, "w", encoding="ascii") as f:
            f.write(str(int(time.time() * 1000)))
        assert bridge.is_alive(1234) is True
        assert bridge.is_alive(9999) is False

    def test_stale_heartbeat_is_dead(self, bridge):
        bridge.deploy()
        path = os.path.join(_bridge_dir(bridge),
                            f"{HEARTBEAT_FILENAME}_1.txt")
        with open(path, "w", encoding="ascii") as f:
            f.write("0")
        old = time.time() - 60
        os.utime(path, (old, old))
        assert bridge.is_alive(1) is False
        assert bridge.heartbeat_age(1) is not None
        assert bridge.heartbeat_age(1) > 8.0


# ── Legacy cleanup ───────────────────────────────────────────────────────

class TestCleanupInstallDirs:
    def test_removes_legacy_per_install_copies(self, bridge, tmp_path,
                                               monkeypatch):
        """cleanup_install_dirs() deletes old per-install extension copies
        from every running Photoshop install (v1..v5 deploys)."""
        import core.photoshop_instances as psi
        ps_dir = tmp_path / "Adobe Photoshop Green"
        ext = ps_dir / "Required" / "CEP" / "extensions" / "ColorinkBridge"
        (ext / "CSXS").mkdir(parents=True)
        (ext / "CSXS" / MANIFEST_FILENAME).write_text(
            '<ExtensionManifest ExtensionBundleId="com.colorink.bridge"/>',
            encoding="utf-8")

        class FakeInst:
            exe_path = str(ps_dir / "Photoshop.exe")

        monkeypatch.setattr(psi, "detect_instances", lambda: [FakeInst()])
        removed = PhotoshopScriptBridge.cleanup_install_dirs()
        assert removed == [str(ps_dir)]
        assert not ext.exists()

    def test_skips_non_bridge_dirs(self, bridge, tmp_path, monkeypatch):
        import core.photoshop_instances as psi
        ps_dir = tmp_path / "Adobe Photoshop Other"
        (ps_dir / "Required" / "CEP" / "extensions").mkdir(parents=True)

        class FakeInst:
            exe_path = str(ps_dir / "Photoshop.exe")

        monkeypatch.setattr(psi, "detect_instances", lambda: [FakeInst()])
        assert PhotoshopScriptBridge.cleanup_install_dirs() == []
