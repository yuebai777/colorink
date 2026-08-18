"""Tests for the user-facing diagnostics report (core/diagnostics.py).

Covers the pure report builder (no Qt needed) plus the settings-sidebar
「复制诊断信息」button wiring.
"""

import os

import pytest

from core import diagnostics


# ── Helpers ─────────────────────────────────────────────────────────────


def _fake_sync_thread():
    """Fake MemorySyncThread exposing the attributes diagnostics reads."""

    class FakeBackend:
        process_name = "CLIPStudioPaint.exe"
        current_version = "csp5.1"

    class FakeCompanion:
        def _has_session(self):
            return True

    class FakePS:
        def status_lite(self):
            return {"connected": True, "backend": "com", "version": "25.0",
                    "lastError": ""}

    class FakeThread:
        software_mode = "csp"
        csp_version = "csp5.1"
        sai2_version = "auto"
        udm_version = "auto"
        ps_version = "auto"
        csp_sync = FakeBackend()
        sai2_sync = FakeBackend()
        udm_sync = FakeBackend()
        ps_sync = FakePS()
        companion_sync = FakeCompanion()

    return FakeThread()


class FakeMixin:
    _sync_status = ("csp", True)
    _sync_error = None
    devicePixelRatio = lambda self: 1.25  # noqa: E731


# ── Report builder ──────────────────────────────────────────────────────


class TestCollectDiagnostics:
    def test_header_version_and_sections(self):
        report = diagnostics.collect_diagnostics()
        assert "Colorink 诊断信息" in report
        assert "版本: v" in report
        assert "[环境 Environment]" in report
        assert "[同步 Sync]" in report

    def test_never_raises_with_all_none(self):
        report = diagnostics.collect_diagnostics(None, None, None)
        assert isinstance(report, str) and report

    def test_includes_sync_state(self):
        report = diagnostics.collect_diagnostics(
            _fake_sync_thread(), {"syncSoftware": "csp", "language": "zh"},
            FakeMixin())
        assert "当前软件: CLIP Studio Paint (内存)" in report
        assert "连接状态: CLIP Studio Paint (内存) 已连接 ✓" in report
        assert "CSP: process_name=CLIPStudioPaint.exe" in report
        assert "Photoshop: connected=True" in report
        assert "Companion: session=有" in report

    def test_includes_sync_error(self):
        mixin = FakeMixin()
        mixin._sync_error = ("csp", "权限不足，无法访问进程", True)
        report = diagnostics.collect_diagnostics(_fake_sync_thread(), {}, mixin)
        assert "权限不足，无法访问进程" in report
        assert "权限问题: 是" in report

    def test_degrades_gracefully_on_backend_exception(self):
        thread = _fake_sync_thread()

        class BoomPS:
            def status_lite(self):
                raise RuntimeError("boom")

        thread.ps_sync = BoomPS()
        report = diagnostics.collect_diagnostics(thread, {}, FakeMixin())
        assert "Photoshop: <获取失败" in report

    def test_includes_stderr_log_tail(self, tmp_path, monkeypatch):
        import core.config as config_mod
        log = tmp_path / "stderr.log"
        lines = [f"line {i}" for i in range(60)]
        log.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(config_mod, "get_user_data_dir", lambda: str(tmp_path))
        report = diagnostics.collect_diagnostics()
        assert "[最近日志" in report
        # tail: only the last 40 lines are kept
        assert "line 0" not in report
        assert "line 59" in report
        assert "line 20" in report  # exactly the 40-line window: 60-40=20

    def test_no_log_marked_when_missing(self, tmp_path, monkeypatch):
        import core.config as config_mod
        monkeypatch.setattr(config_mod, "get_user_data_dir", lambda: str(tmp_path))
        report = diagnostics.collect_diagnostics()
        assert "（无 stderr.log 或不可读）" in report

    def test_tail_never_raises_on_missing_file(self):
        assert diagnostics._tail("/nonexistent/no.log") is None


class TestWriteReport:
    def test_writes_file(self, tmp_path):
        out = tmp_path / "report.txt"
        ok = diagnostics.write_diagnostics_report(str(out))
        assert ok
        assert out.exists()
        assert "Colorink 诊断信息" in out.read_text(encoding="utf-8")

    def test_returns_false_on_bad_path(self):
        assert diagnostics.write_diagnostics_report("/nonexistent/dir/x.txt") is False


# ── Settings sidebar wiring ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication for the test module (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from core import i18n

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def stub_main_window(qapp, tmp_path, monkeypatch):
    """Minimal stub parent for SettingsSidebar (mirrors test_settings_window)."""
    from core import config as _config
    monkeypatch.setattr(_config, "get_user_data_dir", lambda: str(tmp_path))
    from PyQt6.QtWidgets import QWidget

    class StubCompanionSync:
        _connected = False

        def _has_session(self):
            return False

        def _disconnect(self):
            pass

    class StubSyncThread:
        def __init__(self):
            self.companion_sync = StubCompanionSync()

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            from core.config import load_hotkey_config
            self.cfg = load_hotkey_config()
            self.cfg.setdefault("ui-theme", "auto")
            self.cfg.setdefault("fontSize", 100)
            self.sync_thread = StubSyncThread()
            self._sync_status = None
            self._sync_error = None

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *a, **k):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    return StubMainWindow()


@pytest.fixture
def sidebar(stub_main_window, qapp):
    from ui.settings_sidebar import SettingsSidebar
    s = SettingsSidebar(stub_main_window)
    s.setVisible(False)
    stub_main_window.settings_sidebar = s
    return s


class TestCopyDiagnosticsButton:
    def test_button_exists_and_named(self, sidebar):
        assert hasattr(sidebar, "btn_copy_diagnostics")
        assert sidebar.btn_copy_diagnostics.text() == "复制诊断信息"

    def test_click_copies_report_to_clipboard(self, sidebar, qapp):
        sidebar._on_copy_diagnostics()
        qapp.processEvents()
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        assert "Colorink 诊断信息" in text
        assert "版本: v" in text

    def test_click_degrades_without_sync_thread(self, sidebar, qapp):
        sidebar._parent.sync_thread = None
        sidebar._on_copy_diagnostics()
        qapp.processEvents()
        from PyQt6.QtWidgets import QApplication
        assert "Colorink 诊断信息" in QApplication.clipboard().text()
