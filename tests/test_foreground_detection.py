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


class TestDrawingAppPopupMenuDetection:
    """Tests for CSP / drawing-app dropdown & popup menu detection and auto-hide."""

    def test_gui_thread_info_menu_flags_detect_open_menu(self):
        from types import SimpleNamespace
        from core.foreground import (
            GUI_INMENUMODE,
            GUI_POPUPMENUMODE,
            is_drawing_app_menu_open,
        )

        class FakeWinMenuFlag:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=GUI_INMENUMODE,
                    hwndMenuOwner=0,
                    hwndActive=100,
                    hwndFocus=100,
                    hwndCapture=0,
                )

        assert is_drawing_app_menu_open(
            100, tid=55, pid=99999, win32gui_module=FakeWinMenuFlag
        ) is True

        class FakeWinMenuOwner:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=GUI_POPUPMENUMODE,
                    hwndMenuOwner=100,
                    hwndActive=100,
                    hwndFocus=100,
                    hwndCapture=0,
                )

        assert is_drawing_app_menu_open(
            100, tid=55, pid=99999, win32gui_module=FakeWinMenuOwner
        ) is True

    def test_enum_windows_detects_win32_popup_menu_class_32768(self):
        from types import SimpleNamespace
        from core.foreground import is_drawing_app_menu_open

        class FakeWin:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=100, hwndFocus=100, hwndCapture=0
                )

            @staticmethod
            def GetClassName(hwnd):
                return "#32768" if hwnd == 200 else "CLIP STUDIO PAINT"

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetWindowRect(hwnd):
                return (10, 25, 290, 620)

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (100, 200):
                    if cb(h, extra) is False:
                        break

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (55, 99999)

        assert is_drawing_app_menu_open(
            100, tid=55, pid=99999, win32gui_module=FakeWin, win32process_module=FakeProc
        ) is True

    def test_custom_popup_dropdown_vs_tooltip_and_docked_palette(self):
        from types import SimpleNamespace
        from core.foreground import (
            _GWL_EXSTYLE,
            _GWL_STYLE,
            _WS_CAPTION,
            _WS_EX_TOPMOST,
            _WS_POPUP,
            is_drawing_app_menu_open,
        )

        # Case 1: Only a hover tooltip (height 28 < 48) and a titled floating palette
        class FakeWinIdle:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=100, hwndFocus=100, hwndCapture=0
                )

            @staticmethod
            def GetClassName(hwnd):
                if hwnd == 301:
                    return "tooltips_class32"
                return "CelsysPopup"

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetWindowRect(hwnd):
                if hwnd == 301:
                    return (0, 0, 120, 28)
                return (0, 0, 240, 320)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    # 302 has WS_CAPTION (persistent floating palette/dialog)
                    return _WS_POPUP | (_WS_CAPTION if hwnd == 302 else 0)
                return _WS_EX_TOPMOST

            @staticmethod
            def GetWindowText(hwnd):
                return "图层" if hwnd == 302 else ""

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (100, 301, 302):
                    if cb(h, extra) is False:
                        break

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (55, 99999)

        assert is_drawing_app_menu_open(
            100, tid=55, pid=99999, win32gui_module=FakeWinIdle, win32process_module=FakeProc
        ) is False

        # Case 2: A captionless, untitled custom dropdown popup (hwnd=303) is open
        class FakeWinDropdown(FakeWinIdle):
            @staticmethod
            def EnumWindows(cb, extra):
                for h in (100, 303):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            100, tid=55, pid=99999, win32gui_module=FakeWinDropdown, win32process_module=FakeProc
        ) is True

    def test_check_foreground_window_hides_and_restores_on_csp_menu(self, monkeypatch):
        """With hideOnPopupMenu enabled, opening a CSP dropdown menu hides both
        the main window and floating windows, and closing the menu restores them."""
        from types import SimpleNamespace
        import ui.main_window as mw
        import ui.window.picker_actions as pa
        from core.foreground import GUI_INMENUMODE

        state = {"in_menu": True}

        class FakeWin:
            @staticmethod
            def GetForegroundWindow():
                return 777

            @staticmethod
            def GetWindowText(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetClassName(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=GUI_INMENUMODE if state["in_menu"] else 0,
                    hwndMenuOwner=777 if state["in_menu"] else 0,
                    hwndActive=777,
                    hwndFocus=777,
                    hwndCapture=0,
                )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (88, 424242)

        monkeypatch.setitem(sys.modules, "win32gui", FakeWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "clipstudiopaint.exe")

        sync = []
        fs = TestCheckForegroundWindow()._make_self()
        fs.cfg = {"onlyShowInCsp": True, "hideOnPopupMenu": True}
        fs.visible = True
        fs._user_hidden = False
        fs.set_floating_foreground_visible = sync.append

        # 1. Dropdown menu opens in CSP -> main window and floats hide
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert fs.auto_hidden is True
        assert sync == [False]

        # 2. Dropdown menu closes -> main window and floats automatically re-show
        sync.clear()
        state["in_menu"] = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True
        assert fs.auto_hidden is False
        assert sync == [True]

    def test_hide_on_popup_menu_works_even_when_only_show_in_csp_is_off(self, monkeypatch):
        """Even if the user disabled 'onlyShowInCsp', enabling 'hideOnPopupMenu'
        still hides the palette while CSP's dropdown menu is open and restores it after."""
        from types import SimpleNamespace
        import ui.main_window as mw
        import ui.window.picker_actions as pa
        from core.foreground import GUI_INMENUMODE

        state = {"in_menu": True}

        class FakeWin:
            @staticmethod
            def GetForegroundWindow():
                return 777

            @staticmethod
            def GetWindowText(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetClassName(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=GUI_INMENUMODE if state["in_menu"] else 0,
                    hwndMenuOwner=777 if state["in_menu"] else 0,
                    hwndActive=777,
                    hwndFocus=777,
                    hwndCapture=0,
                )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (88, 424242)

        monkeypatch.setitem(sys.modules, "win32gui", FakeWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "clipstudiopaint.exe")

        fs = TestCheckForegroundWindow()._make_self()
        fs.cfg = {"onlyShowInCsp": False, "hideOnPopupMenu": True}
        fs.visible = True
        fs._user_hidden = False

        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert fs.auto_hidden is True

        state["in_menu"] = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True
        assert fs.auto_hidden is False

    def test_bordered_titled_owned_dropshadow_and_sysshadow_popups(self):
        """Regression tests for real CSP/Qt dropdowns:
        1. WS_POPUP | WS_BORDER (no WS_DLGFRAME) is accepted, while WS_THICKFRAME
           / WS_DLGFRAME floating palettes without menu cues are rejected.
        2. Non-empty window titles on CS_DROPSHADOW / CS_SAVEBITS / SysShadow /
           WS_EX_NOACTIVATE popups do not suppress detection.
        3. EnumWindows raising after callback early-stop preserves matched True.
        4. Persistent floating WS_POPUP without menu cues does not false-trigger.
        """
        from types import SimpleNamespace
        from core.foreground import (
            _CS_DROPSHADOW,
            _CS_SAVEBITS,
            _GW_OWNER,
            _GWL_EXSTYLE,
            _GWL_STYLE,
            _WS_BORDER,
            _WS_DLGFRAME,
            _WS_EX_NOACTIVATE,
            _WS_EX_TOOLWINDOW,
            _WS_EX_TOPMOST,
            _WS_POPUP,
            _WS_THICKFRAME,
            is_drawing_app_menu_open,
        )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (77, 88888)

        class BaseFakeWin:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=1000, hwndFocus=1000, hwndCapture=0
                )

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetWindowText(hwnd):
                return "CLIP STUDIO PAINT" if hwnd != 2002 else "FileMenu"

            @staticmethod
            def GetWindow(hwnd, cmd):
                return 1000 if (cmd == _GW_OWNER and hwnd != 1000) else 0

        # A. Resizable floating palette (WS_THICKFRAME) or captioned dialog (WS_DLGFRAME) -> False
        class FakeFloatingPalette(BaseFakeWin):
            @staticmethod
            def GetClassName(hwnd):
                return "CelsysPalette"

            @staticmethod
            def GetWindowRect(hwnd):
                return (100, 100, 320, 500)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    return _WS_POPUP | (_WS_THICKFRAME if hwnd == 2001 else _WS_DLGFRAME)
                return _WS_EX_TOOLWINDOW

            @staticmethod
            def GetClassLong(hwnd, idx):
                return 0

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2001, 2002, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeFloatingPalette, win32process_module=FakeProc
        ) is False

        # B. Owned WS_POPUP | WS_BORDER dropdown WITHOUT WS_EX_TOPMOST, WITH title,
        # paired with CS_DROPSHADOW -> True
        class FakeDropShadowMenu(BaseFakeWin):
            @staticmethod
            def GetClassName(hwnd):
                return "winClass 42"

            @staticmethod
            def GetWindowRect(hwnd):
                return (50, 30, 310, 420)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    return _WS_POPUP | _WS_BORDER
                return _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE

            @staticmethod
            def GetClassLong(hwnd, idx):
                return _CS_DROPSHADOW if hwnd == 2002 else 0

            @staticmethod
            def EnumWindows(cb, extra):
                # 2002 sits above fg_hwnd 1000 in Z-order
                for h in (2002, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeDropShadowMenu, win32process_module=FakeProc
        ) is True

        # C. SysShadow window paired cleanly with WS_POPUP | WS_BORDER menu even
        # when GetClassLong returns 0
        class FakeSysShadowPair(BaseFakeWin):
            @staticmethod
            def GetClassName(hwnd):
                if hwnd == 2003:
                    return "SysShadow"
                return "CustomTriglavWin"

            @staticmethod
            def GetWindowRect(hwnd):
                if hwnd == 2003:
                    return (50, 30, 315, 425)  # +5px drop shadow around 2002
                return (50, 30, 310, 420)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    return _WS_POPUP | _WS_BORDER
                return _WS_EX_TOOLWINDOW

            @staticmethod
            def GetClassLong(hwnd, idx):
                return 0

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2002, 2003, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeSysShadowPair, win32process_module=FakeProc
        ) is True

        # D. Qt-style topmost WS_POPUP | CS_SAVEBITS with non-empty title ("python")
        class FakeQtPopup(BaseFakeWin):
            @staticmethod
            def GetClassName(hwnd):
                return "CustomOpaquePopup"

            @staticmethod
            def GetWindowRect(hwnd):
                return (200, 200, 360, 320)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    return _WS_POPUP
                return _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW

            @staticmethod
            def GetClassLong(hwnd, idx):
                return _CS_SAVEBITS if hwnd == 2002 else 0

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2002, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeQtPopup, win32process_module=FakeProc
        ) is True

        # E. EnumWindows raising pywintypes-like error when callback stops early
        # still returns True once the popup match has been found
        class FakeRaisingEnum(FakeDropShadowMenu):
            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2002, 1000):
                    if cb(h, extra) is False:
                        raise OSError(5, "Access is denied")

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeRaisingEnum, win32process_module=FakeProc
        ) is True

    def test_menu_bar_info_open_submenu_detection(self):
        from types import SimpleNamespace
        from core.foreground import is_drawing_app_menu_open

        class FakeMenuBarWin:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=100, hwndFocus=100, hwndCapture=0
                )

            @staticmethod
            def GetMenuBarInfo(hwnd, obj_id, item):
                return SimpleNamespace(hwndMenu=5555)

        assert is_drawing_app_menu_open(100, tid=55, pid=99999, win32gui_module=FakeMenuBarWin) is True

    def test_plain_owned_floating_panel_without_menu_cues_does_not_hide(self):
        from types import SimpleNamespace
        from core.foreground import (
            _GW_OWNER,
            _GWL_EXSTYLE,
            _GWL_STYLE,
            _WS_BORDER,
            _WS_EX_TOOLWINDOW,
            _WS_POPUP,
            is_drawing_app_menu_open,
        )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (77, 88888)

        class FakeFloatingPlain:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=1000, hwndFocus=1000, hwndCapture=0
                )

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetClassName(hwnd):
                return "CelsysFloatingTool"

            @staticmethod
            def GetWindowRect(hwnd):
                return (40, 80, 280, 520)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                return (_WS_POPUP | _WS_BORDER) if idx == _GWL_STYLE else _WS_EX_TOOLWINDOW

            @staticmethod
            def GetClassLong(hwnd, idx):
                return 0

            @staticmethod
            def GetWindowText(hwnd):
                return "LayerPalette"

            @staticmethod
            def GetWindow(hwnd, cmd):
                return 1000 if (cmd == _GW_OWNER and hwnd == 2002) else 0

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2002, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=77, pid=88888, win32gui_module=FakeFloatingPlain, win32process_module=FakeProc
        ) is False

    def test_restore_after_menu_sets_show_without_activating(self, monkeypatch):
        """Auto-restoring the palette after a dropdown menu closes must temporarily
        enable WA_ShowWithoutActivating even when noFocusMode is False."""
        from types import SimpleNamespace
        from PyQt6.QtCore import Qt
        import ui.main_window as mw
        import ui.window.picker_actions as pa
        from core.foreground import GUI_INMENUMODE

        state = {"in_menu": True}

        class FakeWin:
            @staticmethod
            def GetForegroundWindow():
                return 777

            @staticmethod
            def GetWindowText(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetClassName(hwnd):
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=GUI_INMENUMODE if state["in_menu"] else 0,
                    hwndMenuOwner=777 if state["in_menu"] else 0,
                    hwndActive=777,
                    hwndFocus=777,
                    hwndCapture=0,
                )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (88, 424242)

        monkeypatch.setitem(sys.modules, "win32gui", FakeWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "clipstudiopaint.exe")

        attr_log = []
        fs = TestCheckForegroundWindow()._make_self()
        fs.cfg = {"onlyShowInCsp": True, "hideOnPopupMenu": True, "noFocusMode": False}
        fs.visible = True
        fs._user_hidden = False
        fs._no_act = False

        def test_attr(attr):
            return fs._no_act

        def set_attr(attr, val):
            if attr == Qt.WidgetAttribute.WA_ShowWithoutActivating:
                fs._no_act = bool(val)
                attr_log.append(("set", bool(val)))

        orig_show = fs.show
        def tracked_show():
            attr_log.append(("show", fs._no_act))
            orig_show()

        fs.testAttribute = test_attr
        fs.setAttribute = set_attr
        fs.show = tracked_show

        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False

        state["in_menu"] = False
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True
        assert attr_log == [("set", True), ("show", True), ("set", False)]

    def test_uac_ime_indicator_and_transparent_overlays_do_not_trigger(self):
        """Windows 10/11 CTF/IME indicator overlays injected into drawing-app
        processes must never be misclassified as open dropdown menus."""
        from types import SimpleNamespace
        from core.foreground import (
            _GWL_EXSTYLE,
            _GWL_STYLE,
            _WS_EX_NOACTIVATE,
            _WS_EX_TOPMOST,
            _WS_EX_TRANSPARENT,
            _WS_POPUP,
            is_drawing_app_menu_open,
        )

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (55 if hwnd == 1000 else 99, 88888)

        class FakeImeOverlayWin:
            @staticmethod
            def GetGUIThreadInfo(tid):
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=1000, hwndFocus=1000, hwndCapture=0
                )

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetClassName(hwnd):
                if hwnd == 2010:
                    return "UAC_InputIndicatorOverlayWnd"
                if hwnd == 2011:
                    return "CustomClickThroughOsd"
                return "CLIP STUDIO PAINT"

            @staticmethod
            def GetWindowRect(hwnd):
                return (2437, 1477, 2512, 1552)

            @staticmethod
            def GetWindowLong(hwnd, idx):
                if idx == _GWL_STYLE:
                    return _WS_POPUP
                if hwnd == 2011:
                    return _WS_EX_TOPMOST | _WS_EX_NOACTIVATE | _WS_EX_TRANSPARENT
                return _WS_EX_TOPMOST | _WS_EX_NOACTIVATE

            @staticmethod
            def GetWindowText(hwnd):
                return ""

            @staticmethod
            def EnumWindows(cb, extra):
                for h in (2010, 2011, 1000):
                    if cb(h, extra) is False:
                        break

        assert is_drawing_app_menu_open(
            1000, tid=55, pid=88888, win32gui_module=FakeImeOverlayWin, win32process_module=FakeProc
        ) is False

    def test_csp_foreground_dropdown_menu_hides_and_idle_floating_panel_does_not(
        self, monkeypatch
    ):
        """Regression based on live CLIP STUDIO PAINT window inspection:
        CSP's dropdown popup menu becomes GetForegroundWindow() itself with
        WS_POPUP | CS_DROPSHADOW, empty title, non-zero owner, and SysShadow,
        while GetGUIThreadInfo/GetMenuBarInfo report no Win32 menu flags.
        Meanwhile CSP's floating palettes (e.g. 工具属性) and 1x1 helper owners
        must not trigger false hides when no dropdown is open.
        """
        from types import SimpleNamespace
        import ui.main_window as mw
        import ui.window.picker_actions as pa
        from core.foreground import is_drawing_app_menu_open

        FG_MAIN = 0x390B40
        FG_MENU = 0x18031C
        MENU_SHADOW = 0x910B46
        FLOAT_TOOL = 0xB0BD0
        FLOAT_SHADOW = 0x80B5C
        HELPER_1X1 = 0x40C12
        OWNER_HIDDEN = 0x40C10

        state = {"fg": FG_MENU, "menu_open": True, "force_zero_cls_style": False}

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (16560, 3184)

        class FakeCspWin:
            @staticmethod
            def GetForegroundWindow():
                return state["fg"]

            @staticmethod
            def GetGUIThreadInfo(tid):
                fg = state["fg"]
                return SimpleNamespace(
                    flags=0, hwndMenuOwner=0, hwndActive=fg, hwndFocus=fg, hwndCapture=0
                )

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

            @staticmethod
            def IsIconic(hwnd):
                return False

            @staticmethod
            def GetClassName(hwnd):
                if hwnd in (MENU_SHADOW, FLOAT_SHADOW):
                    return "SysShadow"
                if hwnd in (FG_MENU, FLOAT_TOOL):
                    return "742DEA58-ED6B-4402-BC11-20DFC6D08040-6571DDC4-B3AA-45e4-9D35-57C0C1E90AD5"
                if hwnd == HELPER_1X1:
                    return "742DEA58-ED6B-4402-BC11-20DFC6D08040-2D2FC079-EABA-46f4-9B20-A910FB25B322"
                return "742DEA58-ED6B-4402-BC11-20DFC6D08040"

            @staticmethod
            def GetWindowText(hwnd):
                if hwnd == FG_MAIN:
                    return "CLIP STUDIO PAINT"
                if hwnd == FLOAT_TOOL:
                    return "工具属性"
                return ""

            @staticmethod
            def GetWindowRect(hwnd):
                return {
                    FG_MAIN: (35, 19, 1763, 1645),
                    FG_MENU: (138, 88, 542, 1168),
                    MENU_SHADOW: (138, 88, 547, 1173),
                    FLOAT_TOOL: (2410, 1501, 2705, 1937),
                    FLOAT_SHADOW: (2410, 1501, 2710, 1942),
                    HELPER_1X1: (0, 0, 1, 1),
                }[hwnd]

            @staticmethod
            def GetWindowLong(hwnd, idx):
                styles = {
                    FG_MAIN: (0x14CF0000, 0x00000100),
                    FG_MENU: (0x94000000, 0x00000000),
                    MENU_SHADOW: (0x94000000, 0x000800A0),
                    FLOAT_TOOL: (0x94CE0000, 0x00000100),
                    HELPER_1X1: (0x94000000, 0x00080000),
                    FLOAT_SHADOW: (0x94000000, 0x000800A0),
                }
                st, ex = styles[hwnd]
                return st if idx == -16 else ex

            @staticmethod
            def GetClassLong(hwnd, idx):
                if state["force_zero_cls_style"]:
                    return 0
                if hwnd in (FG_MENU, FLOAT_TOOL):
                    return 0x0002000B  # CS_DROPSHADOW
                return 0x0000000B

            @staticmethod
            def GetWindow(hwnd, cmd):
                if cmd == 4:  # GW_OWNER
                    if hwnd == FG_MENU:
                        return 0 if state["force_zero_cls_style"] else OWNER_HIDDEN
                    if hwnd == FLOAT_TOOL:
                        return HELPER_1X1
                return 0

            @staticmethod
            def EnumWindows(cb, extra):
                order = (
                    (FG_MENU, MENU_SHADOW, FLOAT_TOOL, HELPER_1X1, FG_MAIN, FLOAT_SHADOW)
                    if state["menu_open"]
                    else (FLOAT_TOOL, HELPER_1X1, FG_MAIN, FLOAT_SHADOW)
                )
                for h in order:
                    if cb(h, extra) is False:
                        break

        # 1. Menu open and foreground (CS_DROPSHADOW + owner + untitled) -> True
        assert is_drawing_app_menu_open(
            FG_MENU, tid=16560, pid=3184, win32gui_module=FakeCspWin, win32process_module=FakeProc
        ) is True

        # 2. Still detected purely via SysShadow pairing if GetClassLong & GetWindow return 0
        state["force_zero_cls_style"] = True
        assert is_drawing_app_menu_open(
            FG_MENU, tid=16560, pid=3184, win32gui_module=FakeCspWin, win32process_module=FakeProc
        ) is True
        state["force_zero_cls_style"] = False

        # 3. Idle CSP main window with floating panel & 1x1 owners -> False
        state["menu_open"] = False
        state["fg"] = FG_MAIN
        assert is_drawing_app_menu_open(
            FG_MAIN, tid=16560, pid=3184, win32gui_module=FakeCspWin, win32process_module=FakeProc
        ) is False

        # 4. Floating panel focused (工具属性) -> False
        state["fg"] = FLOAT_TOOL
        assert is_drawing_app_menu_open(
            FLOAT_TOOL, tid=16560, pid=3184, win32gui_module=FakeCspWin, win32process_module=FakeProc
        ) is False

        # 5. End-to-end check_foreground_window hide & auto-restore on CSP
        monkeypatch.setitem(sys.modules, "win32gui", FakeCspWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda pid: "clipstudiopaint.exe")

        sync = []
        fs = TestCheckForegroundWindow()._make_self()
        fs.cfg = {"onlyShowInCsp": True, "hideOnPopupMenu": True, "noFocusMode": True}
        fs.visible = True
        fs._user_hidden = False
        fs.set_floating_foreground_visible = sync.append

        # Menu opens -> hides main window & floating panels
        state["menu_open"] = True
        state["fg"] = FG_MENU
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is False
        assert fs.auto_hidden is True
        assert sync == [False]

        # Menu closes -> restores cleanly
        sync.clear()
        state["menu_open"] = False
        state["fg"] = FG_MAIN
        mw.MainWindow.check_foreground_window(cast(mw.MainWindow, fs))
        assert fs.visible is True
        assert fs.auto_hidden is False
        assert sync == [True]






