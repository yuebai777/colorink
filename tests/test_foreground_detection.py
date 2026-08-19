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
