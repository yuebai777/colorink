"""Unit tests for the drawing-app foreground detection matchers.

These are pure functions (no win32 calls) extracted from
MainWindow.check_foreground_window so the matching logic is testable
without a live Windows session.
"""

import os
import sys
from typing import cast

from ui.main_window import _exe_matches_drawing_app, _title_matches_drawing_app


class TestExeMatching:
    """Process-basename matching (lowercased, extension stripped)."""

    def test_clip_studio_paint_main(self):
        assert _exe_matches_drawing_app("clipstudiopaint.exe")

    def test_clip_studio_paint_painting_subprocess(self):
        # CSP v2+ paints from a separate process
        assert _exe_matches_drawing_app("clipstudiopaintapp.exe")

    def test_clip_studio_launcher(self):
        assert _exe_matches_drawing_app("clipstudio.exe")

    def test_sai2(self):
        assert _exe_matches_drawing_app("sai2.exe")

    def test_sai1(self):
        assert _exe_matches_drawing_app("sai.exe")

    def test_udm_paint_pro(self):
        assert _exe_matches_drawing_app("udmpaintpro.exe")

    def test_udm_paint_ex(self):
        assert _exe_matches_drawing_app("udmpaintex.exe")

    def test_photoshop(self):
        assert _exe_matches_drawing_app("photoshop.exe")

    def test_extension_is_optional(self):
        assert _exe_matches_drawing_app("sai2")

    def test_mixed_case_drawing_apps(self):
        # Windows returns original-case basenames; matching must not depend
        # on the caller pre-lowercasing the exe name.
        assert _exe_matches_drawing_app("CLIPStudioPaint.exe")
        assert _exe_matches_drawing_app("ClipStudioPaintApp.exe")
        assert _exe_matches_drawing_app("SAI.exe")
        assert _exe_matches_drawing_app("SAI2.exe")
        assert _exe_matches_drawing_app("UDMPaintPro.exe")
        assert _exe_matches_drawing_app("Photoshop.exe")

    def test_non_drawing_apps(self):
        assert not _exe_matches_drawing_app("chrome.exe")
        assert not _exe_matches_drawing_app("explorer.exe")
        assert not _exe_matches_drawing_app("msedge.exe")
        assert not _exe_matches_drawing_app("")
        assert not _exe_matches_drawing_app("wechat.exe")


class TestTitleMatching:
    """Lowercased window-title matching with word boundaries."""

    def test_clip_studio_paint_title(self):
        assert _title_matches_drawing_app("clip studio paint")

    def test_clip_studio_paint_chinese(self):
        assert _title_matches_drawing_app("优动漫 paint ex")

    def test_sai_ver2_title(self):
        # "SAI Ver.2" does not contain "sai2" as a substring; the word
        # boundary match is what catches it.
        assert _title_matches_drawing_app("sai ver.2")

    def test_paint_tool_sai_title(self):
        assert _title_matches_drawing_app("paint tool sai")

    def test_udm_paint_title(self):
        assert _title_matches_drawing_app("udm paint")

    def test_photoshop_title(self):
        assert _title_matches_drawing_app("adobe photoshop 2025")

    def test_sai_marker_respects_word_boundary(self):
        # "Photosai" must not false-positive on the "sai" marker.
        assert not _title_matches_drawing_app("photosai")

    def test_mixed_case_titles(self):
        # The matcher should accept raw window titles, not only pre-lowered.
        assert _title_matches_drawing_app("CLIP STUDIO PAINT")
        assert _title_matches_drawing_app("SAI Ver.2")
        assert _title_matches_drawing_app("Adobe Photoshop 2025")
        assert _title_matches_drawing_app("UDM Paint")

    def test_non_drawing_titles(self):
        assert not _title_matches_drawing_app("微信")
        assert not _title_matches_drawing_app("chrome - google chrome")
        assert not _title_matches_drawing_app("")
        assert not _title_matches_drawing_app("navigator")


class TestCheckForegroundWindow:
    """MainWindow.check_foreground_window show/hide decisions, with the
    win32 API stubbed but the decision logic running for real."""

    def _stub_win32(self, monkeypatch, fg_title, fg_pid):
        import ui.main_window as mw

        class FakeWin:
            @staticmethod
            def GetForegroundWindow():
                return 777

            @staticmethod
            def GetWindowText(hwnd):
                return fg_title

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (0, fg_pid)

        monkeypatch.setitem(sys.modules, "win32gui", FakeWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(mw, "_resolve_process_exe", lambda pid: "python.exe")

    def _make_self(self):
        class FakeSelf:
            cfg = {"onlyShowInCsp": True}
            _fg_exe_cache_pid = None
            _fg_exe_cache = ""
            settings_window: object = None
            settings_sidebar = None
            picker_overlay = None
            follow_mouse_active = False
            auto_hidden = True
            visible = False

            def isActiveWindow(self):
                # no-focus mode: the window never reports itself active
                return False

            def isVisible(self):
                return self.visible

            def show(self):
                self.visible = True

            def raise_(self):
                pass

            def hide(self):
                self.visible = False

        return FakeSelf()

    def test_own_process_foreground_keeps_visible_in_no_focus_mode(
        self, monkeypatch
    ):
        """Clicking our own window (foreground PID == our PID) must keep the
        window visible even when isActiveWindow() is always False because
        no-focus mode is enabled. Previously this hid the window mid-use."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "not a drawing app", os.getpid())
        fs = self._make_self()
        fs.visible = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True, "own-process foreground must stay visible"
        assert fs.auto_hidden is False

    def test_other_process_non_drawing_foreground_hides(self, monkeypatch):
        """Foreground owned by another, non-drawing app must hide the window."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)
        fs = self._make_self()
        fs.visible = True
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, "non-drawing foreground must hide"
        assert fs.auto_hidden is True

    def test_user_hidden_blocks_foreground_autoshow(self, monkeypatch):
        """When the user explicitly hid the window (hotkey/tray/close), the
        foreground tracker must not immediately re-show it just because the
        foreground is a drawing app or our own process."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "clip studio paint", 424242)
        fs = self._make_self()
        fs.visible = False
        fs._user_hidden = True
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, "user-hidden window must stay hidden"
        assert fs.auto_hidden is False

    def test_own_process_foreground_only_counts_for_our_pid(self, monkeypatch):
        """A drawing-app foreground keeps the window visible regardless."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "clip studio paint", 424242)
        fs = self._make_self()
        fs.visible = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True

    def test_stuck_settings_active_does_not_keep_visible(self, monkeypatch):
        """A settings window whose Qt activation state is stale (e.g. the OS
        foreground lock denied activateWindow(), so Qt never received a
        deactivate) must NOT keep the palette visible when a non-drawing app
        holds the real foreground. Regression: after the settings UI became a
        separate window, its stuck isActiveWindow() made "仅在画图软件前台时显示"
        never hide the palette."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)

        class StuckSettings:
            def isVisible(self):
                return True

            def isActiveWindow(self):
                return True  # stale Qt bookkeeping — real foreground is Chrome

        fs = self._make_self()
        fs.visible = True
        fs.settings_window = StuckSettings()
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, (
            "stale settings-window activation must not keep the palette visible"
        )
        assert fs.auto_hidden is True

    def test_settings_window_real_foreground_keeps_visible(self, monkeypatch):
        """When OUR process really owns the foreground (user clicked the
        settings window), the palette stays up even though the title is not a
        drawing app."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "设置", os.getpid())
        fs = self._make_self()
        fs.visible = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True
        assert fs.auto_hidden is False

    def test_already_hidden_non_drawing_marks_auto_hidden(self, monkeypatch):
        """When onlyShowInCsp is enabled and the window is already hidden
        (e.g. during startup before main.py calls show()), a non-drawing
        foreground must still record auto_hidden so the unconditional startup
        show() does not override the restriction."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)
        fs = self._make_self()
        fs.visible = False
        fs.auto_hidden = False  # fresh window before the tracker has run
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, "already-hidden window must stay hidden"
        assert fs.auto_hidden is True

    def test_transient_exe_resolution_failure_retries(self, monkeypatch):
        """A failed process-exe lookup must not be cached permanently; the
        next tick should retry and recognize the drawing app once the lookup
        succeeds."""
        import ui.main_window as mw
        import ui.window.picker_actions as pa

        self._stub_win32(monkeypatch, "non-drawing title", 424242)
        calls = {"n": 0}

        def fake_resolve(pid):
            calls["n"] += 1
            return "" if calls["n"] == 1 else "clipstudiopaint.exe"

        monkeypatch.setattr(pa, "_resolve_process_exe", fake_resolve)

        fs = self._make_self()
        fs.visible = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert fs.auto_hidden is True

        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True, "empty exe cache must not block re-resolution"
        assert fs.auto_hidden is False

    def test_follow_mouse_does_not_block_hide_when_only_show_in_csp(
        self, monkeypatch
    ):
        """Regression: with onlyShowInCsp enabled AND follow-mouse active,
        a non-drawing foreground must still hide the palette. Previously the
        follow-mouse keep-visible rule won, so switching away never hid the
        window ("切走不隐藏")."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)
        fs = self._make_self()
        fs.visible = True
        fs.follow_mouse_active = True  # follow-mouse is on…
        fs.cfg = {"onlyShowInCsp": True}  # …but the foreground restriction wins
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, "onlyShowInCsp must override follow-mouse"
        assert fs.auto_hidden is True

    def test_follow_mouse_keeps_visible_when_only_show_in_csp_off(
        self, monkeypatch
    ):
        """Without onlyShowInCsp, follow-mouse still prevents auto-hiding
        (unchanged original behavior)."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)
        fs = self._make_self()
        fs.visible = True
        fs.follow_mouse_active = True
        fs.cfg = {"onlyShowInCsp": False}
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True, "follow-mouse keeps it visible without the restriction"

    def test_floating_sync_follows_palette_visibility(self, monkeypatch):
        """Drawing-app foreground + user did NOT hide → floats get True."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "clip studio paint", 424242)
        fs = self._make_self()
        fs.visible = False
        fs._user_hidden = False
        sync = []
        fs.set_floating_foreground_visible = sync.append
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert sync == [True], sync

    def test_floating_sync_respects_user_hidden(self, monkeypatch):
        """Drawing app is in the foreground but the user explicitly hid the
        palette — the floats must NOT be pulled back over it."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "clip studio paint", 424242)
        fs = self._make_self()
        fs.visible = False
        fs._user_hidden = True
        sync = []
        fs.set_floating_foreground_visible = sync.append
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert sync == [False], sync

    def test_floating_sync_hides_when_foreground_lost(self, monkeypatch):
        """Non-drawing foreground: the tracker hides the main window and
        tells the floats to hide too (the reported bug)."""
        import ui.main_window as mw

        self._stub_win32(monkeypatch, "chrome - google chrome", 424242)
        fs = self._make_self()
        fs.visible = True
        fs._user_hidden = False
        sync = []
        fs.set_floating_foreground_visible = sync.append
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert sync == [False], sync

    def test_browser_with_drawing_title_never_shows_window(self, monkeypatch):
        """Browser with a title like 'Photoshop 教程 - Google Chrome' must NEVER
        falsely show the window when onlyShowInCsp is enabled."""
        import ui.main_window as mw
        import ui.window.picker_actions as pa

        self._stub_win32(monkeypatch, "Photoshop 教程 - Google Chrome", 424242)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "chrome.exe")

        fs = self._make_self()
        fs.visible = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False, "Browser with drawing title must not trigger visibility"
        assert fs.auto_hidden is True

    def test_auto_sync_switches_when_drawing_app_active(self, monkeypatch):
        """When syncSoftware is 'auto', switching to Photoshop automatically
        updates the sync thread's software mode."""
        import ui.main_window as mw
        import ui.window.picker_actions as pa

        self._stub_win32(monkeypatch, "Adobe Photoshop 2025", 11111)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "photoshop.exe")

        class FakeSyncThread:
            software_mode = "csp"
            def set_software_mode(self, mode):
                self.software_mode = mode

        fs = self._make_self()
        fs.cfg = {"syncSoftware": "auto", "onlyShowInCsp": False}
        fs.sync_thread = FakeSyncThread()
        fs.visible = True

        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "ps", "Auto sync should switch to ps"

        # Now switch foreground to SAI2
        self._stub_win32(monkeypatch, "SAI Ver.2", 22222)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "sai2.exe")
        fs._fg_exe_cache_pid = None
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "sai", "Auto sync should switch to sai"

        # Now switch foreground to Chrome (browsing drawing tutorials)
        self._stub_win32(monkeypatch, "SAI2 笔刷 - Microsoft Edge", 33333)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "msedge.exe")
        fs._fg_exe_cache_pid = None
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "sai", "Non-drawing app must not alter active channel"

    def test_auto_sync_switches_csp_prioritizing_companion(self, monkeypatch):
        """When syncSoftware is 'auto', switching to CSP prioritizes 'companion'
        if companion session exists, or falls back to 'csp' if no session exists."""
        import ui.main_window as mw
        import ui.window.picker_actions as pa

        class FakeCompanionSync:
            def __init__(self, has_sess=True):
                self._has_sess = has_sess
            def has_session(self):
                return self._has_sess
            def _has_session(self):
                return self._has_sess

        class FakeSyncThread:
            def __init__(self, has_sess=True):
                self.software_mode = "ps"
                self.companion_sync = FakeCompanionSync(has_sess)
            def set_software_mode(self, mode):
                self.software_mode = mode

        # 1. With companion session -> switches to companion
        self._stub_win32(monkeypatch, "CLIP STUDIO PAINT", 12345)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "clipstudiopaint.exe")

        fs = self._make_self()
        fs.cfg = {"syncSoftware": "auto", "onlyShowInCsp": False}
        fs.sync_thread = FakeSyncThread(has_sess=True)
        fs.visible = True

        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "companion", "CSP with companion session must select companion"

        # Repeated check in companion mode must stay companion
        fs._fg_exe_cache_pid = None
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "companion", "Must remain companion without flip-flop"

        # 2. Without companion session -> falls back to csp
        fs.sync_thread = FakeSyncThread(has_sess=False)
        fs.sync_thread.software_mode = "ps"
        fs._fg_exe_cache_pid = None
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.sync_thread.software_mode == "csp", "CSP without companion session falls back to csp"


class TestIdentifyDrawingApp:
    """Core identify_drawing_app tests for authoritative matching and false-positive prevention."""

    def test_direct_exe_matching(self):
        from core.foreground import identify_drawing_app

        assert identify_drawing_app("photoshop.exe") == "ps"
        assert identify_drawing_app("Photoshop.exe") == "ps"
        assert identify_drawing_app("sai.exe") == "sai"
        assert identify_drawing_app("sai2.exe") == "sai"
        assert identify_drawing_app("SAI2.exe") == "sai"
        assert identify_drawing_app("clipstudiopaint.exe") == "csp"
        assert identify_drawing_app("clipstudiopaintapp.exe") == "csp"
        assert identify_drawing_app("clipstudio.exe") == "csp"
        assert identify_drawing_app("udmpaintpro.exe") == "udm"
        assert identify_drawing_app("udmpaintex.exe") == "udm"

    def test_non_drawing_browsers_with_drawing_titles_return_none(self):
        from core.foreground import identify_drawing_app

        # Browsers with titles containing drawing software keywords must NEVER match
        assert identify_drawing_app("chrome.exe", "Photoshop 2025 教程") is None
        assert identify_drawing_app("msedge.exe", "SAI2 笔刷下载") is None
        assert identify_drawing_app("firefox.exe", "CLIP STUDIO PAINT 技巧") is None
        assert identify_drawing_app("360chrome.exe", "Photoshop 抠图教程") is None
        assert identify_drawing_app("qqbrowser.exe", "优动漫 PAINT 基础") is None
        assert identify_drawing_app("opera.exe", "Photoshop调色技巧") is None

    def test_browser_classes_with_drawing_titles_return_none(self):
        from core.foreground import identify_drawing_app

        assert identify_drawing_app("", "Photoshop 2025", "Chrome_WidgetWin_1") is None
        assert identify_drawing_app("", "SAI 教程", "MozillaWindowClass") is None
        assert identify_drawing_app("", "CSP 笔刷", "CabinetWClass") is None

    def test_browser_title_markers_return_none(self):
        from core.foreground import identify_drawing_app

        assert identify_drawing_app("", "Photoshop 教程 - Google Chrome") is None
        assert identify_drawing_app("", "SAI2 调色 - 哔哩哔哩_bilibili") is None
        assert identify_drawing_app("", "CSP 快速入门 - 百度搜索") is None
        assert identify_drawing_app("", "Photoshop 新建文档 - 知乎") is None

    def test_genuine_title_fallback(self):
        from core.foreground import identify_drawing_app

        # When exe is not a known browser / non-drawing app, title fallback works
        assert identify_drawing_app("", "Adobe Photoshop 2024") == "ps"
        assert identify_drawing_app("", "SAI Ver.2") == "sai"
        assert identify_drawing_app("", "CLIP STUDIO PAINT") == "csp"
        assert identify_drawing_app("", "优动漫 PAINT EX") == "csp"


class TestFindRunningDrawingSoftware:
    """find_running_drawing_software scanner tests."""

    def test_find_running_drawing_software_priority(self, monkeypatch):
        from core.foreground import find_running_drawing_software

        class FakeProcess:
            def __init__(self, name):
                self._name = name
                self.info = {"name": self._name}
            def name(self):
                return self._name

        def fake_process_iter(attrs=None):
            return [
                FakeProcess("explorer.exe"),
                FakeProcess("chrome.exe"),
                FakeProcess("Photoshop.exe"),
            ]

        import psutil
        monkeypatch.setattr(psutil, "process_iter", fake_process_iter)
        assert find_running_drawing_software() == "ps"

    def test_find_running_none(self, monkeypatch):
        from core.foreground import find_running_drawing_software

        class FakeProcess:
            def __init__(self, name):
                self._name = name
                self.info = {"name": self._name}
            def name(self):
                return self._name

        def fake_process_iter(attrs=None):
            return [
                FakeProcess("explorer.exe"),
                FakeProcess("chrome.exe"),
                FakeProcess("code.exe"),
            ]

        import psutil
        monkeypatch.setattr(psutil, "process_iter", fake_process_iter)
        assert find_running_drawing_software() is None


class TestResolveAutoSyncMode:
    """Auto sync software channel resolution tests."""

    def test_csp_with_companion_session_resolves_to_companion(self):
        from core.foreground import resolve_auto_sync_mode
        assert resolve_auto_sync_mode("csp", has_companion_session=True) == "companion"

    def test_csp_without_companion_session_resolves_to_csp(self):
        from core.foreground import resolve_auto_sync_mode
        assert resolve_auto_sync_mode("csp", current_mode="ps", has_companion_session=False) == "csp"

    def test_csp_already_in_companion_preserves_companion(self):
        from core.foreground import resolve_auto_sync_mode
        assert resolve_auto_sync_mode("csp", current_mode="companion", has_companion_session=False) == "companion"

    def test_ps_sai_udm_direct_resolution(self):
        from core.foreground import resolve_auto_sync_mode
        assert resolve_auto_sync_mode("ps") == "ps"
        assert resolve_auto_sync_mode("sai") == "sai"
        assert resolve_auto_sync_mode("udm") == "udm"
        assert resolve_auto_sync_mode(None) is None

    def test_csp_defaults_to_disk_session(self, monkeypatch):
        import core.foreground as fg
        monkeypatch.setattr(fg, "has_saved_companion_session", lambda: True)
        assert fg.resolve_auto_sync_mode("csp") == "companion"

        monkeypatch.setattr(fg, "has_saved_companion_session", lambda: False)
        assert fg.resolve_auto_sync_mode("csp", current_mode="ps") == "csp"


class TestHasSavedCompanionSession:
    """Tests for has_saved_companion_session."""

    def test_valid_session_file(self, tmp_path, monkeypatch):
        import json
        from core.foreground import has_saved_companion_session

        colorink_dir = tmp_path / "Colorink"
        colorink_dir.mkdir()
        session_file = colorink_dir / "csp_companion_session.json"
        session_file.write_text(json.dumps({
            "host": "192.168.1.10",
            "port": 32035,
            "password": "secret",
        }), encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert has_saved_companion_session() is True

    def test_missing_or_invalid_session_file(self, tmp_path, monkeypatch):
        import json
        from core.foreground import has_saved_companion_session

        monkeypatch.setenv("APPDATA", str(tmp_path))
        # File doesn't exist
        assert has_saved_companion_session() is False

        # Incomplete file (missing password)
        colorink_dir = tmp_path / "Colorink"
        colorink_dir.mkdir()
        session_file = colorink_dir / "csp_companion_session.json"
        session_file.write_text(json.dumps({
            "host": "192.168.1.10",
            "port": 32035,
            "password": "",
        }), encoding="utf-8")
        assert has_saved_companion_session() is False


