"""Tests for CSP 5.1 detection, refusal, and version-key normalization."""

from core.csp_brush_link import (
    _PROFILE_INDEX,
    CSPSync,
    _detect_build_from_version,
    _normalize_version_key,
)


def test_normalize_version_key_maps_legacy_5_x():
    assert _normalize_version_key("csp5.x") == "csp5.x"
    assert _normalize_version_key("csp5.0") == "csp5.x"
    assert _normalize_version_key("csp4.x") == "csp4.x"


def test_detect_build_version_mapping():
    assert _detect_build_from_version((5, 1, 0, 0)) == "csp5.1"
    assert _detect_build_from_version((5, 2, 1, 3)) == "csp5.1"
    assert _detect_build_from_version((5, 0, 0, 0)) == "csp5.x"
    assert _detect_build_from_version((4, 2, 7, 0)) == "csp4.x"
    assert _detect_build_from_version(None) is None


def test_csp5_1_profile_is_retired():
    assert "csp5.1" not in _PROFILE_INDEX


def test_set_version_auto_keeps_the_detected_profile():
    sync = CSPSync()
    sync._apply_profile("csp5.x")
    sync.pm = object()
    sync.pid = 4321
    sync.target = 0x2000

    assert sync.set_version("auto") is False
    assert sync.current_version == "csp5.x"
    assert sync.color_format == "u16x2_dup"
    assert sync.pm is not None
    assert sync.pid == 4321


def test_set_version_explicit_pin_still_switches_and_reconnects():
    sync = CSPSync()
    sync._apply_profile("csp5.x")
    sync.pm = object()
    sync.pid = 4321

    assert sync.set_version("csp4.x") is True
    assert sync.current_version == "csp4.x"
    assert sync.pm is None
    assert sync.pid is None


# ── CSP 5.1 内存同步已移除：connect() 拒绝附接 + status() 短路径 ───────────

def _patch_connect_5_1(monkeypatch):
    """Force ``CSPSync.connect()`` to auto-detect a CSP 5.1 process."""
    import core.csp_brush_link.process as _process

    class _FakeModule:
        lpBaseOfDll = 0x400000

    class _FakePm:
        process_id = 9001
        process_handle = 0xABCD

        def close_process(self):
            pass

    def _fake_pymem(name):
        assert name == "CLIPStudioPaint.exe"
        return _FakePm()

    def _fake_version(path):
        # (5, 1, 0, 0) -> _detect_build_from_version returns "csp5.1"
        return (5, 1, 0, 0)

    monkeypatch.setattr(_process, "Pymem", _fake_pymem)
    monkeypatch.setattr(_process, "module_from_name", lambda pm, name: _FakeModule())
    monkeypatch.setattr(
        _process._ProcessVersionQuery, "image_path",
        lambda handle: "C:\\Program Files\\CELSYS\\CLIPStudioPaint.exe")
    monkeypatch.setattr(_process._ProcessVersionQuery, "exe_version", _fake_version)
    return _FakeModule, _FakePm


def test_connect_refuses_csp_5_1_memory_sync(monkeypatch):
    from core.csp_brush_link.process import CSP51_MEMORY_UNSUPPORTED
    _patch_connect_5_1(monkeypatch)

    sync = CSPSync()
    assert sync.connect() is False
    assert sync.pm is None
    assert sync.unsupported_reason == CSP51_MEMORY_UNSUPPORTED


def test_status_reports_5_1_unsupported_without_reattaching(monkeypatch):
    from core.csp_brush_link.process import CSP51_MEMORY_UNSUPPORTED
    _patch_connect_5_1(monkeypatch)

    sync = CSPSync()
    status = sync.status()

    assert status["connected"] is False
    assert status["unsupported_reason"] == CSP51_MEMORY_UNSUPPORTED
    # 短路径：拒绝后 status() 不应再尝试打开 CSP 进程（pm 保持 None）。
    assert sync.pm is None


def test_apply_profile_switch_clears_5_1_unsupported_flag(monkeypatch):
    _patch_connect_5_1(monkeypatch)
    sync = CSPSync()
    # 初始 profile 是 csp4.x；先拒一次，随后手动选 csp5.x（不同 profile）走
    # _apply_profile 清掉标记。
    sync.connect()
    assert sync.unsupported_reason
    sync.set_version("csp5.x")
    assert sync.current_version == "csp5.x"
    assert sync.unsupported_reason == ""
