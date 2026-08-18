"""Self-replace / restart helpers for the one-click update flow."""

import os

from core import updater


def test_can_self_replace_onefile(tmp_path):
    exe = str(tmp_path / "Colorink.exe")
    # No _internal folder next to the exe -> single-file build.
    assert updater.can_self_replace(exe, frozen=True) is True


def test_can_self_replace_onedir_has_internal(tmp_path):
    exe_dir = tmp_path / "Colorink"
    exe_dir.mkdir()
    (exe_dir / "_internal").mkdir()
    exe = str(exe_dir / "Colorink.exe")
    assert updater.can_self_replace(exe, frozen=True) is False


def test_can_self_replace_not_frozen(tmp_path):
    exe = str(tmp_path / "Colorink.exe")
    assert updater.can_self_replace(exe, frozen=False) is False


def test_can_self_replace_no_exe(tmp_path):
    assert updater.can_self_replace("", frozen=True) is False


def test_build_self_replace_script_absolutizes_and_quotes(tmp_path):
    new = str(tmp_path / "updates" / "Colorink.exe")
    cur = str(tmp_path / "Colorink.exe")
    script = updater.build_self_replace_script(new, cur)

    assert "move /Y" in script
    assert '"' + os.path.abspath(new) + '"' in script
    assert '"' + os.path.abspath(cur) + '"' in script
    assert 'start ""' in script
    assert 'del "%~f0"' in script


def test_build_self_replace_script_falls_back_to_new_when_move_fails(tmp_path):
    new = str(tmp_path / "Colorink.exe")
    cur = str(tmp_path / "old" / "Colorink.exe")
    script = updater.build_self_replace_script(new, cur)
    assert 'if exist "' + os.path.abspath(new) + '"' in script


def test_can_self_replace_readonly_dir(tmp_path, monkeypatch):
    """A onefile exe in a non-writable directory (e.g. Program Files
    without elevation) must NOT be offered self-replace — the post-update
    move would fail and silently degrade to running from Downloads."""
    exe = str(tmp_path / "Colorink.exe")
    monkeypatch.setattr(updater, "_dir_writable", lambda _d: False)
    assert updater.can_self_replace(exe, frozen=True) is False


def test_can_self_update_onefile(tmp_path):
    exe = str(tmp_path / "Colorink.exe")
    assert updater.can_self_update(exe, frozen=True) is True


def test_can_self_update_onedir(tmp_path):
    exe_dir = tmp_path / "Colorink"
    exe_dir.mkdir()
    (exe_dir / "_internal").mkdir()
    exe = str(exe_dir / "Colorink.exe")
    assert updater.can_self_update(exe, frozen=True) is True


def test_can_self_update_not_frozen(tmp_path):
    exe = str(tmp_path / "Colorink.exe")
    assert updater.can_self_update(exe, frozen=False) is False


def test_can_self_update_readonly_dir(tmp_path, monkeypatch):
    exe = str(tmp_path / "Colorink.exe")
    monkeypatch.setattr(updater, "_dir_writable", lambda _d: False)
    assert updater.can_self_update(exe, frozen=True) is False


def test_build_onedir_update_script_contains_powershell_flow(tmp_path):
    zip_path = str(tmp_path / "Colorink-Onedir.zip")
    current_exe = str(tmp_path / "Colorink" / "Colorink.exe")
    script = updater.build_onedir_update_script(zip_path, current_exe, current_pid=1234)

    assert "Expand-Archive" in script
    assert "Wait-Process -Id $currentPid" in script
    assert "$currentPid = 1234" in script
    assert "_internal" in script
    assert "Start-Process" in script
