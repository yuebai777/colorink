"""Unit tests for PhotoshopSync target selection / recheck (no COM)."""

import types
from unittest.mock import MagicMock

from core.photoshop_color_sync import PhotoshopSync
from core.photoshop_instances import (
    COM_KIND,
    SCRIPT_BRIDGE_KIND,
    PhotoshopInstance,
)


def _inst(kind, label="X", pid=1, progid=None):
    return PhotoshopInstance(kind=kind, label=label, exe_path=r"D:\X\Photoshop.exe",
                             pid=pid, progid=progid)


class TestSetVersion:
    def test_set_version_stores_label_and_resets(self):
        ps = PhotoshopSync()
        ps._reset = MagicMock()
        assert ps.set_version("Adobe Photoshop 2020 (COM)") is True
        assert ps.current_version == "Adobe Photoshop 2020 (COM)"
        ps._reset.assert_called_once()

    def test_set_version_same_value_is_noop(self):
        ps = PhotoshopSync()
        ps._reset = MagicMock()
        # current_version starts as "auto" → same-value call must be a no-op
        assert ps.set_version("auto") is False
        ps._reset.assert_not_called()

    def test_set_version_empty_falls_back_to_auto(self):
        ps = PhotoshopSync()
        ps.set_version("")
        assert ps.current_version == "auto"


class TestRecheck:
    def test_recheck_forces_detection_then_connects(self):
        ps = PhotoshopSync()
        calls = []

        def _fake_detect(force=False):
            calls.append(force)
            return []

        ps._detect = _fake_detect
        ps.connect = MagicMock(return_value=True)
        assert ps.recheck() is True
        assert calls == [True]
        ps.connect.assert_called_once()


class TestProcessAliveFallback:
    """OpenProcess is denied cross-elevation (elevated PS vs normal
    Colorink); the liveness check must fall back to process enumeration
    instead of reporting the process dead."""

    def test_openprocess_denied_falls_back_to_enumeration(self, monkeypatch):
        import core.photoshop_color_sync as pcs

        ps = PhotoshopSync()
        ps._pid = 1234
        # OpenProcess denied: emulate via a stub that returns 0.
        ps.K32 = types.SimpleNamespace(OpenProcess=lambda *a: 0)  # type: ignore[assignment]

        class _Proc:
            def __init__(self, pid):
                self.info = {"pid": pid}

        monkeypatch.setattr(
            pcs.psutil, "process_iter",
            lambda attrs=None: iter([_Proc(1234), _Proc(5678)]),
        )
        assert ps._is_process_alive() is True

    def test_openprocess_denied_and_process_gone(self, monkeypatch):
        import core.photoshop_color_sync as pcs

        ps = PhotoshopSync()
        ps._pid = 9999
        ps.K32 = types.SimpleNamespace(OpenProcess=lambda *a: 0)  # type: ignore[assignment]

        class _Proc:
            def __init__(self, pid):
                self.info = {"pid": pid}

        monkeypatch.setattr(
            pcs.psutil, "process_iter",
            lambda attrs=None: iter([_Proc(1234)]),
        )
        assert ps._is_process_alive() is False


class TestComFallback:
    """COM registrations are transient on green builds — a COM failure for
    the selected instance must fall back to the script bridge."""

    def test_com_failure_falls_back_to_bridge(self):
        ps = PhotoshopSync()
        green = _inst(COM_KIND, "Green (COM)", progid="Photoshop.Application")
        ps._detect = lambda force=False: [green]
        ps._connect_com = MagicMock(return_value=False)
        ps._connect_bridge = MagicMock(return_value=True)
        assert ps.connect() is True
        ps._connect_com.assert_called_once()
        ps._connect_bridge.assert_called_once()

    def test_com_success_skips_bridge(self):
        ps = PhotoshopSync()
        reg = _inst(COM_KIND, "Reg (COM)", progid="Photoshop.Application.140")
        ps._detect = lambda force=False: [reg]
        ps._connect_com = MagicMock(return_value=True)
        ps._connect_bridge = MagicMock(return_value=True)
        assert ps.connect() is True
        ps._connect_com.assert_called_once()
        ps._connect_bridge.assert_not_called()

    def test_auto_mode_tries_other_com_instance_before_bridge(self):
        ps = PhotoshopSync()
        bad = _inst(COM_KIND, "Bad (COM)", pid=1, progid="Photoshop.Application")
        good = _inst(COM_KIND, "Good (COM)", pid=2, progid="Photoshop.Application.140")
        ps._detect = lambda force=False: [bad, good]
        ps._connect_com = MagicMock(side_effect=[False, True])
        ps._connect_bridge = MagicMock(return_value=True)
        assert ps.connect() is True
        assert ps._connect_com.call_count == 2
        ps._connect_bridge.assert_not_called()
        assert ps._pid == 2  # switched to the working instance

    def test_no_instances_sets_error(self):
        ps = PhotoshopSync()
        ps._detect = lambda force=False: []
        assert ps.connect() is False
        assert "未检测到" in ps.last_error
