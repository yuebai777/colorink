"""Tests for CSP Companion reconnection prompting and temporary QR-overlap hiding."""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from typing import cast

import pytest
from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QWidget

from core import config as core_config
from core import i18n
from core.csp_companion_sync import CSPCompanionSync
from core.foreground import (
    find_csp_companion_qr_rect,
    find_csp_companion_qr_window,
    is_csp_companion_qr_title,
    is_csp_running,
    rects_overlap,
)
from core.memory_sync import MemorySyncThread
from ui.panels import registry
from ui.panels import tree as dock
from ui.panels.host import PanelHost
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.panels_mixin import PanelProviderMixin
from ui.window.picker_actions import PickerActionsMixin
from ui.window.sync_mixin import SyncMixin


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ── Foreground QR dialog & process detection ───────────────────────────────


class TestCompanionQrWindowDetection:
    def test_qr_dialog_title_matching(self):
        assert is_csp_companion_qr_title("连接智能手机") is True
        assert is_csp_companion_qr_title(" 連接智慧型手機 ") is True
        assert is_csp_companion_qr_title("Connect to smartphone") is True
        assert is_csp_companion_qr_title("スマートフォンと接続") is True
        assert is_csp_companion_qr_title("스마트폰과 연결") is True
        assert is_csp_companion_qr_title("CLIP STUDIO PAINT") is False
        assert is_csp_companion_qr_title("") is False

    def test_rects_overlap_with_margin(self):
        a = (100, 100, 300, 300)
        b = (305, 100, 500, 300)
        assert rects_overlap(a, b, margin=0) is False
        assert rects_overlap(a, b, margin=10) is True
        assert rects_overlap((0, 0, 0, 100), (0, 0, 100, 100)) is False

    def test_find_csp_companion_qr_window_filters_non_csp_and_small(self):
        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(hwnd):
                return (1, {10: 1001, 11: 1002, 12: 1003, 13: 1004, 14: 1005}[hwnd])

        class FakeWin:
            @staticmethod
            def IsWindowVisible(hwnd):
                return hwnd != 10

            @staticmethod
            def IsIconic(hwnd):
                return hwnd == 11

            @staticmethod
            def GetWindowText(hwnd):
                return "连接智能手机"

            @staticmethod
            def GetWindowRect(hwnd):
                if hwnd == 12:
                    return (0, 0, 100, 100)  # too small
                return (600, 180, 1020, 620)

            @staticmethod
            def GetClassName(hwnd):
                return "Chrome_WidgetWin_1" if hwnd == 13 else "CelsysDlg"

            @staticmethod
            def EnumWindows(cb, _extra):
                # 10=invisible, 11=minimized, 12=small, 13=chrome, 14=real CSP QR dialog
                for h in (10, 11, 12, 13, 14):
                    if cb(h, None) is False:
                        break

        def fake_exe(pid):
            return {1004: "chrome.exe", 1005: "clipstudiopaint.exe"}.get(pid, "clipstudiopaint.exe")

        hit = find_csp_companion_qr_window(
            win32gui_module=FakeWin,
            win32process_module=FakeProc,
            process_exe_resolver=fake_exe,
        )
        assert hit == (14, (600, 180, 1020, 620))
        assert find_csp_companion_qr_rect(
            win32gui_module=FakeWin,
            win32process_module=FakeProc,
            process_exe_resolver=fake_exe,
        ) == (600, 180, 1020, 620)

    def test_is_csp_running_requires_paint_process(self, monkeypatch):
        import psutil

        def _procs(names):
            return [SimpleNamespace(info={"name": n}) for n in names]

        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: _procs(["CLIPStudio.exe", "chrome.exe"]))
        assert is_csp_running() is False

        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: _procs(["CLIPStudio.exe", "CLIPStudioPaint.exe"]))
        assert is_csp_running() is True


# ── Companion backend reconnect tracking & QR scanning ────────────────────


class TestCompanionReconnectStateAndScan:
    def _make_sync(self) -> CSPCompanionSync:
        s = CSPCompanionSync.__new__(CSPCompanionSync)
        s._sock = None
        s._recv_buf = b""
        s._serial = 0
        s._connected = False
        s._aborting = False
        s._last_heartbeat = 0.0
        s._host = "127.0.0.1"
        s._port = 32035
        s._password = "secret"
        s._generation = "G#1:2022.12"
        s._current_color = {}
        s._last_hue_u32 = 0
        s._last_sat_u32 = 0
        s.current_version = "auto"
        s.process_name = "CLIPStudioPaint.exe"
        s.pid = None
        s.pm = None
        s.last_error = ""
        s.consecutive_failures = 0
        s.ever_connected = False
        return s

    def test_needs_reconnect_triggers_after_failures_or_auth_error_or_drop(self):
        s = self._make_sync()
        assert s.needs_reconnect() is False

        s.consecutive_failures = 1
        assert s.needs_reconnect() is False

        s.consecutive_failures = 2
        assert s.needs_reconnect() is True

        s.reset_reconnect_tracking()
        assert s.needs_reconnect() is False

        s.last_error = "auth_failed"
        assert s.needs_reconnect() is True

        s.reset_reconnect_tracking()
        s.ever_connected = True
        assert s.needs_reconnect() is True

        s._connected = True
        assert s.needs_reconnect() is False

    def test_status_lite_does_not_call_connect(self, monkeypatch):
        s = self._make_sync()
        calls = []
        monkeypatch.setattr(s, "connect", lambda *a, **k: calls.append(True) or False)
        st = s.status_lite()
        assert st["connected"] is False
        assert st["hasSession"] is True
        assert calls == []

    def test_scan_qr_code_checks_target_rect_and_secondary_monitors(self, monkeypatch):
        # Build a tiny fake mss/pyzbar pipeline where only monitor 2 carries a valid URL
        class _Shot:
            def __init__(self, tag):
                self.size = (2, 2)
                self.bgra = b"\x00" * 16
                self.tag = tag

        class _FakeMss:
            monitors = [
                {"left": 0, "top": 0, "width": 3840, "height": 1080},
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
                {"left": 1920, "top": 0, "width": 1920, "height": 1080},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def grab(self, region):
                if region.get("left") == 1920:
                    return _Shot("mon2")
                if region.get("left", 0) >= 2000:
                    return _Shot("crop")
                return _Shot("mon1")

        from PIL import Image
        import mss
        import pyzbar.pyzbar as zbar

        from core.csp_companion_sync import _REMOTE_KEY, _xor_hex

        token = _xor_hex("127.0.0.1\t32035\tpw123\tG#1:2022.12", _REMOTE_KEY)
        url = f"https://companion.clip-studio.com/rc/zh-tw?s={token}"

        seen_tags = []

        def fake_frombytes(_mode, _size, _data, *a):
            # Track order via last grabbed tag stashed on FakeImage
            return Image.new("RGB", (2, 2))

        orig_grab = _FakeMss.grab

        def tracked_grab(self, region):
            shot = orig_grab(self, region)
            seen_tags.append(shot.tag)
            return shot

        monkeypatch.setattr(_FakeMss, "grab", tracked_grab)
        monkeypatch.setattr(mss, "mss", lambda: _FakeMss())
        monkeypatch.setattr(Image, "frombytes", fake_frombytes)

        def fake_decode(_img):
            tag = seen_tags[-1]
            if tag in ("mon2", "crop"):
                return [SimpleNamespace(data=url.encode("utf-8"))]
            return []

        monkeypatch.setattr(zbar, "decode", fake_decode)

        # 1. Without target_rect: primary monitor fails -> secondary monitor succeeds
        info = CSPCompanionSync.scan_qr_code()
        assert info is not None
        assert info["host"] == "127.0.0.1"
        assert info["port"] == 32035
        assert info["password"] == "pw123"
        assert seen_tags == ["mon1", "mon2"]

        # 2. With target_rect: scans cropped target_rect immediately
        seen_tags.clear()
        info2 = CSPCompanionSync.scan_qr_code(target_rect=(2100, 200, 2400, 500))
        assert info2 is not None and info2["port"] == 32035
        assert seen_tags == ["crop"]


# ── Companion setup / reconnect dialog ────────────────────────────────────


class TestCompanionReconnectDialog:
    def test_dialog_hides_windows_for_scan_and_auto_watches_qr_without_looping(self, qapp, monkeypatch):
        events: list[str] = []
        qr_state = {"open": True}
        scan_calls = []

        def fake_hide(dlg):
            events.append("hide")
            return True

        def fake_restore(snap, dlg):
            events.append("restore")

        def fake_find_qr():
            return (9999, (700, 150, 1100, 580)) if qr_state["open"] else None

        overlap_calls = []

        def fake_scan(target_rect=None):
            scan_calls.append(target_rect)
            # Confirm windows were hidden BEFORE scan_qr_code ran
            assert events.count("hide") > events.count("restore")
            return None  # simulate QR not ready yet

        monkeypatch.setattr(CSPCompanionSync, "scan_qr_code", staticmethod(fake_scan))

        dlg = CSPCompanionSync.create_companion_dialog(
            None,
            reconnect_mode=True,
            auto_show_without_activating=True,
            hide_for_scan_cb=fake_hide,
            restore_after_scan_cb=fake_restore,
            find_qr_window_cb=fake_find_qr,
            sync_overlap_cb=overlap_calls.append,
        )
        try:
            assert i18n.tr("重新连接") in dlg.windowTitle() or "重新连接" in dlg.windowTitle()
            dlg.show()
            # Wait up to 2.2s for initial auto-scan + single retry (max 2 attempts)
            t0 = time.time()
            while time.time() - t0 < 2.2:
                qapp.processEvents()
                time.sleep(0.02)
            assert len(scan_calls) == 2
            assert scan_calls[0] == (700, 150, 1100, 580)
            assert events == ["hide", "restore", "hide", "restore"]
            assert overlap_calls and overlap_calls[-1] == (700, 150, 1100, 580)
        finally:
            dlg.close()

    def test_dialog_manual_scan_connects_and_notifies_callback(self, qapp, monkeypatch):
        monkeypatch.setattr(
            CSPCompanionSync,
            "scan_qr_code",
            staticmethod(lambda target_rect=None: {
                "host": "127.0.0.1",
                "port": 32035,
                "password": "pw",
                "generation": "G#1:2022.12",
            }),
        )
        disconnected_after_save = []

        def fake_connect(self, host="", port=0, password="", generation=""):
            return host == "127.0.0.1" and port == 32035 and password == "pw"

        monkeypatch.setattr(CSPCompanionSync, "connect", fake_connect)
        monkeypatch.setattr(
            CSPCompanionSync,
            "_disconnect",
            lambda self: disconnected_after_save.append(True),
        )

        connected_cbs = []
        dlg = CSPCompanionSync.create_companion_dialog(
            None,
            reconnect_mode=True,
            find_qr_window_cb=lambda: None,
            on_connected_cb=lambda: connected_cbs.append(True),
        )
        try:
            dlg.show()
            dlg.trigger_scan()  # type: ignore[attr-defined]
            t0 = time.time()
            while time.time() - t0 < 1.5 and not connected_cbs:
                qapp.processEvents()
                time.sleep(0.02)
            assert connected_cbs == [True]
            assert disconnected_after_save == [True]
            assert getattr(dlg, "result_ok", False) is True
        finally:
            dlg.close()


# ── MemorySyncThread companion_reconnect_needed signal ────────────────────


class TestMemorySyncCompanionReconnectSignal:
    def test_emits_reconnect_needed_once_and_resets_on_reconnect(self, qapp):
        class StubCompanion:
            def __init__(self):
                self._connected = False
                self.last_error = "connect_failed"
                self.failures = 0
                self.connect_calls = 0

            def has_session(self):
                return True

            def _has_session(self):
                return True

            def get_color_hsv(self):
                if self._connected:
                    return {"h": 0.0, "s": 0.0, "v": 0.0, "r": 10, "g": 20, "b": 30, "transparent": False, "index": 0}
                self.connect_calls += 1
                self.failures += 1
                return None

            def status_lite(self):
                return {"connected": self._connected}

            def status(self):
                raise AssertionError("MemorySyncThread must prefer status_lite() over status()")

            def needs_reconnect(self):
                return (not self._connected) and self.failures >= 2

        comp = StubCompanion()
        st = MemorySyncThread()
        st.software_mode = "companion"
        setattr(st, "companion_sync", comp)
        emitted: list[str] = []
        st.signals.companion_reconnect_needed.connect(emitted.append)

        st.start()
        try:
            # Wait for 2 throttled attempts (~1.2s apart) -> emits once
            t0 = time.time()
            while time.time() - t0 < 3.0 and not emitted:
                qapp.processEvents()
                time.sleep(0.02)
            assert emitted == ["connect_failed"]

            # Drain more ticks: must not emit duplicate signal for the same outage
            t1 = time.time()
            while time.time() - t1 < 0.4:
                qapp.processEvents()
                time.sleep(0.02)
            assert emitted == ["connect_failed"]

            # Recover connection, then drop again -> emits a second time
            comp._connected = True
            t2 = time.time()
            while time.time() - t2 < 0.3:
                qapp.processEvents()
                time.sleep(0.02)
            assert st._companion_reconnect_notified is False

            comp._connected = False
            comp.last_error = "disconnected"
            t3 = time.time()
            while time.time() - t3 < 2.0 and len(emitted) < 2:
                qapp.processEvents()
                time.sleep(0.02)
            assert emitted == ["connect_failed", "disconnected"]
        finally:
            st.stop()


# ── MainWindow QR overlap hiding & reconnect prompting ────────────────────


class _FakeMainWithFloats(SyncMixin, PickerActionsMixin, PanelProviderMixin, FloatingPanelsMixin, QWidget):
    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg if cfg is not None else {"onlyShowInCsp": True, "hideOnPopupMenu": False}
        self.stack = QWidget(self)
        self.lab_slider_column = QWidget(self)
        self.preview_box = QWidget(self)
        self.color_history = QWidget(self)
        self.slider_containers = {g: QLabel(g, self) for g in core_config.SLIDER_GROUPS}
        self.panel_host = PanelHost(self.panel_provider(), self)
        rgb = registry.slider_panel_id("RGB")
        self.panel_host.set_tree(dock.Split(dock.VERTICAL, (dock.Leaf(rgb),), (), False))
        self.auto_hidden = False
        self._user_hidden = False
        self._qr_scan_hiding = False
        self._qr_obscuring_hidden = False
        self._qr_hidden_settings = False
        self._qr_hidden_dialog = False
        self._companion_dialog = None
        self.settings_window = None

    def refresh_slider_visibility_and_order(self, **_kwargs):
        pass


class TestMainWindowQrTemporaryHiding:
    def test_scan_hide_and_restore_preserves_visibility_state(self, qapp, monkeypatch):
        monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
        win = _FakeMainWithFloats()
        win.resize(200, 200)
        win.show()
        rgb = registry.slider_panel_id("RGB")
        win.float_panel(rgb)
        floater = win.floating_windows()[rgb]

        sw = QWidget()
        sw.show()
        win.settings_window = sw

        dlg = QDialog()
        dlg.show()

        monkeypatch.setattr("ui.window.sync_mixin.find_csp_companion_qr_rect", lambda: None)

        snap = win._hide_windows_for_qr_scan(dlg)
        assert win._qr_scan_hiding is True
        assert not win.isVisible()
        assert floater.isHidden()
        assert not sw.isVisible()
        assert not dlg.isVisible()

        win._restore_windows_after_qr_scan(snap, dlg)
        assert win._qr_scan_hiding is False
        assert win.isVisible()
        assert not floater.isHidden()
        assert sw.isVisible()
        assert dlg.isVisible()

        sw.close()
        dlg.close()
        win.close()

    def test_overlap_hides_only_overlapping_floater_and_restores_on_close(self, qapp, monkeypatch):
        monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
        win = _FakeMainWithFloats({"onlyShowInCsp": False, "hideOnPopupMenu": False})
        win.setGeometry(50, 50, 180, 180)
        win.show()
        rgb = registry.slider_panel_id("RGB")
        win.float_panel(rgb)
        floater = win.floating_windows()[rgb]
        floater.setGeometry(700, 200, 220, 220)
        floater.show()

        # Mock screen rects in physical coords: main is at far left, floater overlaps QR at (650,150,1050,550)
        def fake_rect(widget):
            if widget is win:
                return (50, 50, 230, 230)
            if widget is floater:
                return (700, 200, 920, 420)
            return None

        monkeypatch.setattr("ui.window.sync_mixin.get_widget_screen_rect", fake_rect)
        qr_state = {"rect": (650, 150, 1050, 550)}
        monkeypatch.setattr("ui.window.sync_mixin.find_csp_companion_qr_rect", lambda: qr_state["rect"])

        win._sync_qr_overlap_visibility()
        assert win.isVisible() is True  # far left, does not overlap QR
        assert win._qr_obscuring_hidden is False
        assert floater.isHidden() is True
        assert getattr(floater, "_qr_hidden", False) is True

        # Even if set_floating_foreground_visible(True) runs while QR is open, floater stays hidden
        win.set_floating_foreground_visible(True)
        assert floater.isHidden() is True

        # Close QR dialog -> floater restores cleanly
        qr_state["rect"] = None
        win._sync_qr_overlap_visibility()
        assert getattr(floater, "_qr_hidden", False) is False
        assert floater.isHidden() is False

        win.close()

    def test_check_foreground_window_hides_overlapping_main_window_without_flash(self, qapp, monkeypatch):
        monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
        import ui.window.picker_actions as pa

        class FakeWin:
            @staticmethod
            def GetForegroundWindow():
                return 777

            @staticmethod
            def GetWindowText(_hwnd):
                return "连接智能手机"

            @staticmethod
            def GetClassName(_hwnd):
                return "CelsysDlg"

        class FakeProc:
            @staticmethod
            def GetWindowThreadProcessId(_hwnd):
                return (1, 424242)

        monkeypatch.setitem(sys.modules, "win32gui", FakeWin)
        monkeypatch.setitem(sys.modules, "win32process", FakeProc)
        monkeypatch.setattr(pa, "_resolve_process_exe", lambda _pid: "clipstudiopaint.exe")

        win = _FakeMainWithFloats({"onlyShowInCsp": True, "hideOnPopupMenu": False})
        win.setGeometry(650, 200, 240, 300)
        # MainWindow was previously hidden (e.g., menu just closed right as QR dialog opened)
        win.hide()

        monkeypatch.setattr("ui.window.sync_mixin.get_widget_screen_rect", lambda w: (650, 200, 890, 500) if w is win else None)
        qr_state = {"rect": (600, 150, 1020, 580)}
        monkeypatch.setattr("ui.window.sync_mixin.find_csp_companion_qr_rect", lambda: qr_state["rect"])

        win.check_foreground_window()
        # Must remain hidden without flashing on top of the QR dialog
        assert win._qr_obscuring_hidden is True
        assert win.isVisible() is False

        # QR dialog closes while CSP is still foreground -> MainWindow restores automatically
        qr_state["rect"] = None
        win.check_foreground_window()
        assert win._qr_obscuring_hidden is False
        assert win.isVisible() is True

        win.close()

    def test_maybe_prompt_companion_reconnection_respects_csp_running_and_foreground(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(QTimer, "singleShot", lambda _ms, fn: fn())

        class FakeCompanion:
            _connected = False
            def has_session(self):
                return True
            def _has_session(self):
                return True

        class FakeWin(SyncMixin):
            def __init__(self):
                self.cfg = {"onlyShowInCsp": True}
                self.sync_thread = SimpleNamespace(software_mode="companion", companion_sync=FakeCompanion())
                self._companion_reconnect_prompted = False
                self._companion_reconnect_pending = False
                self._companion_prompt_scheduled = False
                self._companion_dialog = None
                self._user_hidden = False
                self.opened = []

            def _setup_companion_connection(self, reconnect_mode=False, auto_prompt=False):
                self.opened.append((reconnect_mode, auto_prompt))

        win = FakeWin()

        # 1. CSP not running -> stays pending, does not prompt
        monkeypatch.setattr("ui.window.sync_mixin.is_csp_running", lambda: False)
        win.on_companion_reconnect_needed("connect_failed")
        assert win.opened == []
        assert win._companion_reconnect_pending is True
        assert win._companion_reconnect_prompted is False

        # 2. CSP running, but foreground is browser -> stays pending
        monkeypatch.setattr("ui.window.sync_mixin.is_csp_running", lambda: True)
        monkeypatch.setattr(FakeWin, "_is_csp_or_self_foreground", staticmethod(lambda: False))
        win.maybe_prompt_companion_reconnection()
        assert win.opened == []
        assert win._companion_reconnect_pending is True

        # 3. CSP running & foreground -> prompts once with reconnect_mode=True, auto_prompt=True
        monkeypatch.setattr(FakeWin, "_is_csp_or_self_foreground", staticmethod(lambda: True))
        win.maybe_prompt_companion_reconnection()
        assert win.opened == [(True, True)]
        assert win._companion_reconnect_prompted is True
        assert win._companion_reconnect_pending is False

        # 4. Duplicate call for the same outage -> suppressed
        win.maybe_prompt_companion_reconnection()
        assert win.opened == [(True, True)]

    def test_overlap_hides_and_restores_settings_window_and_respects_user_hidden(self, qapp, monkeypatch):
        monkeypatch.setattr(core_config, "save_hotkey_config", lambda cfg: None)
        win = _FakeMainWithFloats({"onlyShowInCsp": False, "hideOnPopupMenu": False})
        win.setGeometry(50, 50, 180, 180)
        win._user_hidden = True  # user manually hid main window
        win.hide()

        sw = QWidget()
        sw.setGeometry(650, 180, 300, 300)
        sw.show()
        win.settings_window = sw

        def fake_rect(widget):
            if widget is sw:
                return (650, 180, 950, 480)
            if widget is win:
                return (650, 180, 830, 360)
            return None

        monkeypatch.setattr("ui.window.sync_mixin.get_widget_screen_rect", fake_rect)
        qr_state = {"rect": (600, 150, 1020, 580)}
        monkeypatch.setattr("ui.window.sync_mixin.find_csp_companion_qr_rect", lambda: qr_state["rect"])

        win._sync_qr_overlap_visibility()
        assert sw.isVisible() is False
        assert win._qr_hidden_settings is True
        assert win._qr_obscuring_hidden is False  # user_hidden kept untouched

        qr_state["rect"] = None
        win._sync_qr_overlap_visibility()
        assert sw.isVisible() is True
        assert win._qr_hidden_settings is False
        assert win.isVisible() is False  # must NOT wake a user-hidden main window

        sw.close()
        win.close()

    def test_settings_sync_panel_shows_reconnect_hint_when_needed(self, qapp):
        from ui.settings.sync_panel import SyncPanelMixin

        class FakeCompanion:
            _connected = False
            def _has_session(self):
                return True
            def needs_reconnect(self):
                return True

        class FakePanel(SyncPanelMixin):
            def __init__(self):
                self._parent = SimpleNamespace(
                    sync_thread=SimpleNamespace(companion_sync=FakeCompanion())
                )
                self.lbl_companion_status = QLabel("")
                self.btn_companion_reconnect = QLabel("")
                self.btn_companion_disconnect = QLabel("")

            def _set_label_state(self, _lbl, _state):
                pass

        i18n.set_language(i18n.LANG_ZH)
        panel = FakePanel()
        panel._refresh_companion_status()
        assert "连接智能手机" in panel.lbl_companion_status.text()
        assert panel.btn_companion_reconnect.text() == "重新连接"
        assert panel.btn_companion_reconnect.isVisible() is True
