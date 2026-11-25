"""Unit tests for PhotoshopSync target selection, liveness fallback, and unified CEP bridge."""

import types
from unittest.mock import MagicMock

from core.photoshop_color_sync import PhotoshopSync
from core.photoshop_instances import (
    SCRIPT_BRIDGE_KIND,
    PhotoshopInstance,
)


def _inst(label="X", pid=1):
    return PhotoshopInstance(
        kind=SCRIPT_BRIDGE_KIND,
        label=label,
        exe_path=r"D:\X\Photoshop.exe",
        pid=pid,
    )


class TestSetVersion:
    def test_set_version_stores_label_and_resets(self):
        ps = PhotoshopSync()
        ps._reset = MagicMock()
        assert ps.set_version("Adobe Photoshop 2020 (同步桥)") is True
        assert ps.current_version == "Adobe Photoshop 2020 (同步桥)"
        ps._reset.assert_called_once()

    def test_set_version_same_value_is_noop(self):
        ps = PhotoshopSync()
        ps._reset = MagicMock()
        # current_version starts as "auto" -> same-value call must be a no-op
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


class TestUnifiedBridge:
    """Every Photoshop edition shares ONE sync path: the user-level CEP bridge."""

    def test_connect_routes_instance_to_bridge_without_com_backend(self):
        ps = PhotoshopSync()
        assert not hasattr(ps, "_connect_com")
        inst = _inst("Adobe Photoshop 2024 (同步桥)", pid=42)
        ps._detect = lambda force=False: [inst]
        ps._connect_bridge = MagicMock(return_value=True)
        assert ps.connect() is True
        ps._connect_bridge.assert_called_once_with(inst)
        assert ps._pid == 42

    def test_no_instances_sets_error(self):
        ps = PhotoshopSync()
        ps._detect = lambda force=False: []
        assert ps.connect() is False
        assert "未检测到" in ps.last_error

    def test_drawing_window_methods_delegate_to_bridge(self):
        ps = PhotoshopSync()
        mock_bridge = MagicMock()
        mock_bridge.assume_drawing_active.return_value = True
        ps._bridge = mock_bridge

        ps.note_color_applied(600)
        mock_bridge.note_color_applied.assert_called_once_with(600)

        assert ps.assume_drawing_active() is True
        mock_bridge.assume_drawing_active.assert_called_once()
