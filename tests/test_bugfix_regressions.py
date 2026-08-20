"""Regression tests for the bug-fix pass on preview hit-testing, OKLCh ring
hue, companion transparency/HLS parsing, EOF handling and download verification.

Each test here pins one concrete defect that the suite previously let through.
"""

import socket
from typing import Any, cast

import pytest

from core import config
from core.csp_companion_sync import CSPCompanionSync
from ui.color_preview_box import ColorPreviewBox

from .test_ringless_preview_support import make_preview_box, qapp  # noqa: F401


# ── Legacy preview circles: paint / hit-test / mask share one geometry ─────


def _legacy_box(qapp, position_mode="top-left", size=60):
    box = make_preview_box()
    box.position_mode = position_mode
    box.resize(size, size)
    return box


class TestLegacyCircleGeometryIsShared:
    """The legacy (non-ringless) fg/bg circles are drawn, hit-tested and
    masked from a single geometry helper.

    The hit-test used to carry its own stale constants (box 53, bg 26 instead
    of 60 / 30), so the clickable FG disc was ~13% oversized and offset a few
    pixels from the disc actually painted.
    """

    @pytest.mark.parametrize("position_mode", ["top-left", "bottom-right"])
    def test_geometry_matches_documented_constants(self, qapp, position_mode):
        box = _legacy_box(qapp, position_mode)
        fg_cx, fg_cy, fg_r, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()

        # scale = 60/60 = 1 → fg diameter 40, bg diameter 30, border 2.
        assert fg_r == pytest.approx(20.0)
        assert bg_r == pytest.approx(15.0)
        assert fg_cx == pytest.approx(22.0)
        assert bg_cx == pytest.approx(43.0)
        if position_mode == "top-left":
            assert fg_cy == pytest.approx(38.0)
            assert bg_cy == pytest.approx(17.0)
        else:
            assert fg_cy == pytest.approx(22.0)
            assert bg_cy == pytest.approx(43.0)

    def test_hit_test_accepts_the_drawn_circle_edge(self, qapp):
        """A click just inside the painted FG rim registers."""
        box = _legacy_box(qapp)
        fg_cx, fg_cy, fg_r, _, _, _ = box.legacy_circle_geometry()
        # 0.5px inside the drawn radius, straight up from the centre.
        assert box._get_clicked_slot(fg_cx, fg_cy - (fg_r - 0.5)) == "fg"

    def test_hit_test_rejects_the_margin_outside_the_drawn_circle(self, qapp):
        """A click outside the painted FG rim must NOT select fg.

        With the old 53/26 constants the hit disc reached ~2.6px beyond the
        drawn one here, so clicks on empty chrome selected the fg slot.
        """
        box = _legacy_box(qapp)
        fg_cx, fg_cy, fg_r, _, _, _ = box.legacy_circle_geometry()
        assert box._get_clicked_slot(fg_cx, fg_cy - (fg_r + 1.5)) is None

    def test_hit_test_accepts_the_outer_rim_of_the_bg_circle(self, qapp):
        """The BG rim used to be dead to clicks (26px vs the drawn 30px)."""
        box = _legacy_box(qapp)
        _, _, _, bg_cx, bg_cy, bg_r = box.legacy_circle_geometry()
        box.active_slot = "bg"
        assert box._get_clicked_slot(bg_cx, bg_cy + (bg_r - 0.5)) == "bg"

    def test_mask_uses_the_same_helper_as_paint(self, qapp, monkeypatch):
        """apply_preview_mouse_mask must consume legacy_circle_geometry()."""
        from ui import transparent_swatch

        box = _legacy_box(qapp)
        seen = []
        real = ColorPreviewBox.legacy_circle_geometry

        def spy(self):
            result = real(self)
            seen.append(result)
            return result

        monkeypatch.setattr(ColorPreviewBox, "legacy_circle_geometry", spy)
        transparent_swatch.apply_preview_mouse_mask(box)
        assert seen, "mask did not derive its circles from legacy_circle_geometry()"


# ── OKLCh hue ring: every drawing helper agrees on the hue ────────────────


class _FakeWheel:
    """Minimal stand-in exposing what _oklch_hue_for_ring() touches."""

    def __init__(self, rgb, oklch_h=None, drag_h=None):
        self._rgb = rgb
        self._oklch_h = oklch_h
        self._drag_oklch_h = drag_h

    def get_color(self):
        return self._rgb


def _hue_for(wheel):
    from ui.color_wheel_rendering import ColorWheelRenderingMixin

    return ColorWheelRenderingMixin._oklch_hue_for_ring(cast(Any, wheel))


class TestOklchRingHueResolution:
    """The OKLCh ring gradient is built from OKLCh hue angles, so the marker
    must use the OKLCh hue too — including before the first drag, when
    ``_oklch_h`` is still None and the old code fell back to the HSV hue.
    """

    def test_falls_back_to_rgb_derived_oklch_hue(self):
        from ui.color_conversions import rgb_to_oklch

        rgb = (200, 40, 60)
        expected = rgb_to_oklch(*rgb)[2]
        assert _hue_for(_FakeWheel(rgb)) == pytest.approx(expected)

    def test_rgb_fallback_differs_from_hsv_hue(self):
        """Guards the premise: for a saturated colour the two disagree, so
        falling back to the HSV hue really did misplace the marker."""
        import colorsys

        from ui.color_conversions import rgb_to_oklch

        rgb = (200, 40, 60)
        hsv_hue = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))[0] * 360.0
        oklch_hue = rgb_to_oklch(*rgb)[2]
        assert abs(oklch_hue - hsv_hue) > 1.0

    def test_stored_state_wins_over_rgb(self):
        assert _hue_for(_FakeWheel((200, 40, 60), oklch_h=123.0)) == pytest.approx(123.0)

    def test_drag_cache_wins_over_stored_state(self):
        wheel = _FakeWheel((200, 40, 60), oklch_h=123.0, drag_h=45.0)
        assert _hue_for(wheel) == pytest.approx(45.0)


# ── CSP Companion: transparency, HLS black, EOF ───────────────────────────


class _RecordingSync(CSPCompanionSync):
    """Companion client with the socket layer replaced by a recorder."""

    def __init__(self):
        self._last_hue_u32 = 0
        self._last_sat_u32 = 0
        self._current_color = {}
        self._connected = True
        self._sock = object()
        self._recv_buf = b""
        self.sent = []

    def _ensure_heartbeat(self):
        pass

    def _send_raw(self, data):
        self.sent.append(data)

    def _recv_messages(self, timeout=0.5):
        return []


class TestTransparentClearIsAlwaysSent:
    """Toggling transparency never changes a slot's RGB, so the write-dedup
    cache has to remember the transparent flag. It previously stored RGB only,
    which swallowed every transparent→opaque clear and left CSP's
    IsColorTransparent stuck ON.
    """

    def test_clearing_transparency_reaches_csp(self):
        sync = _RecordingSync()
        assert sync.set_color(255, 255, 255, color_index=0) is True
        assert sync.set_color(255, 255, 255, transparent=True, color_index=0) is True
        before = len(sync.sent)

        assert sync.set_color(255, 255, 255, transparent=False, color_index=0) is True

        assert len(sync.sent) == before + 1, "transparent→opaque clear was deduped away"
        assert b'"IsColorTransparent":false' in sync.sent[-1]

    def test_identical_opaque_write_is_still_deduped(self):
        """The dedup must keep working for the ordinary repeat-colour case."""
        sync = _RecordingSync()
        sync.set_color(10, 20, 30, color_index=0)
        before = len(sync.sent)
        sync.set_color(10, 20, 30, color_index=0)
        assert len(sync.sent) == before

    def test_dedup_is_still_per_slot(self):
        sync = _RecordingSync()
        sync.set_color(10, 20, 30, color_index=0)
        before = len(sync.sent)
        sync.set_color(10, 20, 30, color_index=1)
        assert len(sync.sent) == before + 1


class TestHlsBlackIsNotDropped:
    """In the HLS colour model black is exactly H=0, L=0, S=0 — the all-zero
    check used to discard it as an "empty" response.
    """

    def _sync(self):
        sync = CSPCompanionSync.__new__(CSPCompanionSync)
        sync._last_hue_u32 = 0
        sync._last_sat_u32 = 0
        return sync

    def test_pure_black_main_slot_is_reported(self):
        out = self._sync()._parse_hls_response({
            "CurrentColorIndex": 0,
            "ColorSelectionModel": "HLS",
            "HLSColorMainH": 0,
            "HLSColorMainL": 0,
            "HLSColorMainS": 0,
        }, 0)
        assert out is not None, "pure black in HLS mode was dropped"
        assert (out["r"], out["g"], out["b"]) == (0, 0, 0)
        assert out["index"] == 0

    def test_pure_black_sub_slot_is_reported(self):
        out = self._sync()._parse_hls_response({
            "CurrentColorIndex": 1,
            "ColorSelectionModel": "HLS",
            "HLSColorSubH": 0,
            "HLSColorSubL": 0,
            "HLSColorSubS": 0,
        }, 1)
        assert out is not None
        assert (out["r"], out["g"], out["b"]) == (0, 0, 0)
        assert out["index"] == 1

    def test_payload_without_hls_fields_is_still_skipped(self):
        """A response that claims HLS but carries no HLS keys is not a colour."""
        assert self._sync()._parse_hls_response({"CurrentColorIndex": 0}, 0) is None


class TestRecvEofIsADisconnect:
    """A graceful peer close surfaces as an empty recv, not an exception.
    Treating it as "no data" left status() reporting a healthy link.
    """

    class _EofSocket:
        def __init__(self):
            self.closed = False

        def settimeout(self, _t):
            pass

        def recv(self, _n):
            return b""

        def close(self):
            self.closed = True

    def _sync(self):
        sync = CSPCompanionSync.__new__(CSPCompanionSync)
        sync._connected = True
        sync._recv_buf = b""
        sync._current_color = {0: {"r": 1, "g": 2, "b": 3, "transparent": 0}}
        sync._sock = self._EofSocket()
        return sync

    def test_eof_marks_the_client_disconnected(self):
        sync = self._sync()
        assert sync._recv_messages() == []
        assert sync._connected is False

    def test_eof_closes_the_dead_socket(self):
        sync = self._sync()
        sock = sync._sock
        sync._recv_messages()
        assert sock.closed is True
        assert sync._sock is None

    def test_timeout_is_not_a_disconnect(self):
        """Only EOF disconnects; an idle socket must stay connected."""

        class _TimeoutSocket:
            def settimeout(self, _t):
                pass

            def recv(self, _n):
                raise socket.timeout()

        sync = self._sync()
        sync._sock = _TimeoutSocket()
        assert sync._recv_messages() == []
        assert sync._connected is True


# ── Updater: byte-count check uses the resolved total ─────────────────────


class TestDownloadCompletenessUsesResolvedTotal:
    """When the release asset has no "size", the Content-Length still has to
    be enforced — the check previously read the (absent) parameter instead.
    """

    def _patch_urlopen(self, mp, body, content_length):
        import core.updater as updater

        class _Resp:
            headers = {"Content-Length": str(content_length)}

            def __enter__(self):
                self._chunks = [body, b""]
                return self

            def __exit__(self, *a):
                return False

            def read(self, _n):
                return self._chunks.pop(0)

        mp.setattr(updater.urllib.request, "urlopen", lambda *a, **k: _Resp())

    def test_truncated_body_is_rejected_without_asset_size(self, tmp_path, monkeypatch):
        from core import updater

        dest = tmp_path / "Colorink.exe"
        self._patch_urlopen(monkeypatch, b"1234", content_length=99)

        result = updater.download_release(
            "https://example.invalid/a.exe", str(dest), total_size=None,
        )

        assert "error" in result, "truncated download passed verification"
        assert not dest.exists()
        assert not (tmp_path / "Colorink.exe.part").exists()

    def test_complete_body_is_accepted(self, tmp_path, monkeypatch):
        from core import updater

        dest = tmp_path / "Colorink.exe"
        self._patch_urlopen(monkeypatch, b"1234", content_length=4)

        result = updater.download_release(
            "https://example.invalid/a.exe", str(dest), total_size=None,
        )

        assert result.get("bytes") == 4
        assert dest.read_bytes() == b"1234"


# ── Config: the write-only key is retired ─────────────────────────────────


def test_lab_visualizer_max_val_is_not_a_default():
    assert "labVisualizerMaxVal" not in config.default_hotkey_config()


def test_migration_drops_stale_lab_visualizer_max_val():
    cfg = config.migrate_config({"pickKey": "F2", "labVisualizerMaxVal": 0.4})
    assert "labVisualizerMaxVal" not in cfg
    assert cfg["pickKey"] == "F2"
    assert cfg[config.CONFIG_SCHEMA_KEY] == config.CONFIG_SCHEMA_VERSION


# ── SAI UI refresh: the offscreen bitmap is released, not leaked ───────────


class _FakeGdi:
    """Records the GDI calls fill_ratio() makes, in order."""

    DEFAULT_BITMAP = 0x1000   # the 1x1 monochrome bitmap a fresh memory DC holds
    NEW_BITMAP = 0x2000
    MEM_DC = 0x3000

    def __init__(self, dibits_ok=True):
        self.calls = []
        self._dibits_ok = dibits_ok
        self._selected = self.DEFAULT_BITMAP

    def CreateCompatibleDC(self, _hdc):
        self.calls.append(("CreateCompatibleDC",))
        return self.MEM_DC

    def CreateCompatibleBitmap(self, _hdc, _w, _h):
        self.calls.append(("CreateCompatibleBitmap",))
        return self.NEW_BITMAP

    def SelectObject(self, hdc, obj):
        self.calls.append(("SelectObject", hdc, obj))
        previous, self._selected = self._selected, obj
        return previous

    def GetDIBits(self, *_a):
        self.calls.append(("GetDIBits",))
        return 1 if self._dibits_ok else 0

    def DeleteObject(self, obj):
        self.calls.append(("DeleteObject", obj))
        # Mirrors the real API: deleting a bitmap still selected into a DC fails.
        return 0 if obj == self._selected else 1

    def DeleteDC(self, hdc):
        self.calls.append(("DeleteDC", hdc))
        return 1


class _FakeUser:
    def __init__(self, print_ok=True, width=10, height=10):
        self.calls = []
        self._print_ok = print_ok
        self._w, self._h = width, height

    def GetWindowRect(self, _hwnd, rect_ref):
        rect = rect_ref._obj
        rect.left = rect.top = 0
        rect.right, rect.bottom = self._w, self._h
        return 1

    def GetDC(self, _hwnd):
        self.calls.append(("GetDC",))
        return 0x4000

    def PrintWindow(self, *_a):
        self.calls.append(("PrintWindow",))
        return 1 if self._print_ok else 0

    def ReleaseDC(self, _hwnd, hdc):
        self.calls.append(("ReleaseDC", hdc))
        return 1


def _backend(gdi, user):
    from core.sai2_ui_refresh import Win32Backend

    backend = Win32Backend.__new__(Win32Backend)
    backend._gdi32 = gdi
    backend._user32 = user
    return backend


class TestFillRatioReleasesItsBitmap:
    """DeleteObject cannot free a bitmap that is still selected into a DC, and
    DeleteDC does not free it either — so fill_ratio() has to swap the original
    bitmap back in first. Without that, every probe leaked a 32bpp bitmap of up
    to MAX_CANDIDATE_AREA * 4 bytes and burned a GDI handle.
    """

    def test_bitmap_is_deselected_before_deletion(self):
        gdi, user = _FakeGdi(), _FakeUser()
        _backend(gdi, user).fill_ratio(1234, (0, 0, 0))

        selects = [c for c in gdi.calls if c[0] == "SelectObject"]
        assert len(selects) == 2, "the original bitmap was never restored"
        assert selects[0][2] == _FakeGdi.NEW_BITMAP
        assert selects[1][2] == _FakeGdi.DEFAULT_BITMAP

        order = [c[0] for c in gdi.calls]
        assert order.index("SelectObject") < order.index("DeleteObject")
        assert order[-2:] == ["DeleteObject", "DeleteDC"]

    def test_delete_object_actually_succeeds(self):
        """The fake mimics the real API's refusal to delete a selected bitmap,
        so a passing delete proves the bitmap was genuinely released."""
        gdi, user = _FakeGdi(), _FakeUser()
        _backend(gdi, user).fill_ratio(1234, (0, 0, 0))

        deletes = [c for c in gdi.calls if c[0] == "DeleteObject"]
        assert deletes == [("DeleteObject", _FakeGdi.NEW_BITMAP)]
        assert gdi._selected == _FakeGdi.DEFAULT_BITMAP

    def test_cleanup_still_runs_when_printwindow_fails(self):
        gdi, user = _FakeGdi(), _FakeUser(print_ok=False)
        assert _backend(gdi, user).fill_ratio(1234, (0, 0, 0)) == 0.0

        order = [c[0] for c in gdi.calls]
        assert order.count("SelectObject") == 2
        assert order[-2:] == ["DeleteObject", "DeleteDC"]
        assert ("ReleaseDC", 0x4000) in user.calls

    def test_cleanup_still_runs_when_getdibits_fails(self):
        gdi, user = _FakeGdi(dibits_ok=False), _FakeUser()
        assert _backend(gdi, user).fill_ratio(1234, (0, 0, 0)) == 0.0

        assert [c[0] for c in gdi.calls][-2:] == ["DeleteObject", "DeleteDC"]
        assert [c[0] for c in gdi.calls].count("SelectObject") == 2
        assert gdi._selected == _FakeGdi.DEFAULT_BITMAP
        assert ("ReleaseDC", 0x4000) in user.calls


# ── Native grayscale: a failed preheat must not eat the next toggle ────────


class TestStartAfterFailedPreheat:
    """A preheat that fails leaves the overlay stack active but with no warm
    cache, and start()'s ``_impl.is_active`` shortcut used to neither clear the
    previous attempt's ``_camera_error`` nor refresh ``_reveal_deadline``.
    _poll_reveal then re-consumed the stale startup error, so the user's first
    Ctrl+G reported a failure from the past and turned nothing on — silently,
    because toggle() still returns True.
    """

    def _controller(self, monkeypatch, fail_attempts):
        """Controller whose dxcam fails for the first *fail_attempts* starts."""
        from PyQt6.QtCore import QTimer

        import core.native_grayscale as ng

        from .test_native_grayscale import _FakeImpl, _FakeWidget, _TimerQueue

        attempts = {"n": 0}

        class FlakyWidget(_FakeWidget):
            def _colorink_start_capture(self):
                self.start_calls += 1
                attempts["n"] += 1
                if attempts["n"] <= fail_attempts:
                    self._frame_count = 0
                    self._camera_error = "dxcam 采集初始化失败: transient"
                else:
                    self._camera = object()
                    self._frame_count = 1
                    self._camera_error = ""

        class FlakyImpl(_FakeImpl):
            def set_active(self, active, mode=None):
                self.is_active = active
                if active and not self._overlays:
                    self._overlays = [FlakyWidget(0), FlakyWidget(1)]
                    for widget in self._overlays:
                        widget._colorink_start_capture()
                elif not active:
                    for widget in self._overlays:
                        widget._colorink_stop_capture()
                    self._overlays = []

        class FlakyRuntime:
            GrayscaleOverlay = FlakyImpl

        queue = _TimerQueue()
        monkeypatch.setattr(QTimer, "singleShot", queue.singleShot)
        monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: FlakyRuntime())
        return ng.NativeGrayscaleController(), queue

    def test_failed_preheat_leaves_the_overlay_stack_active(self, monkeypatch):
        """Pins the state that makes the shortcut reachable."""
        c, queue = self._controller(monkeypatch, fail_attempts=2)
        c.prepare()
        queue.run_all()

        assert c._impl.is_active is True
        assert c._warming is False and c._warmed is False
        assert not c.is_active

    def test_first_toggle_after_failed_preheat_makes_a_fresh_attempt(self, monkeypatch):
        """dxcam recovers after the preheat, so the first Ctrl+G must work."""
        c, queue = self._controller(monkeypatch, fail_attempts=2)
        c.prepare()
        queue.run_all()

        c.last_error = ""
        c.toggle()
        queue.run_all()

        assert c.is_active, (
            "first toggle after a failed preheat was consumed by the stale "
            f"startup error ({c.last_error!r}) instead of retrying capture"
        )
        assert not c.last_error

    def test_a_persistent_failure_still_surfaces_an_error(self, monkeypatch):
        """The retry must not paper over a genuinely broken dxcam."""
        c, queue = self._controller(monkeypatch, fail_attempts=99)
        c.prepare()
        queue.run_all()

        c.last_error = ""
        c.toggle()
        queue.run_all()

        assert not c.is_active
        assert "dxcam" in c.last_error


# ── Native grayscale: screen watchers are released on close() ─────────────


class TestScreenWatchersAreReleased:
    """close() must leave the controller inert.

    __init__ connects QApplication/QScreen signals to bound methods. PyQt holds
    those weakly, so a discarded controller is NOT leaked — that is asserted
    below so the rationale here stays honest. What it did do is leave the
    connections armed until collection, and _on_screen_layout_changed
    re-registers geometryChanged on every screen each time it runs, so an
    explicitly closed controller kept re-arming its own watchers on every
    display change.
    """

    def _controller(self, monkeypatch):
        from PyQt6.QtCore import QTimer

        import core.native_grayscale as ng

        from .test_native_grayscale import _FakeRuntime, _TimerQueue

        queue = _TimerQueue()
        monkeypatch.setattr(QTimer, "singleShot", queue.singleShot)
        monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: _FakeRuntime())
        return ng.NativeGrayscaleController()

    def test_watchers_are_installed_on_construction(self, qapp, monkeypatch):
        c = self._controller(monkeypatch)
        assert c._screen_watchers_installed is True
        assert c._watched_screens, "no QScreen was watched"

    def test_close_disconnects_application_signals(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QApplication

        c = self._controller(monkeypatch)
        c.close()

        assert c._screen_watchers_installed is False
        assert not c._watched_screens
        app = QApplication.instance()
        for signal in (app.screenAdded, app.screenRemoved, app.primaryScreenChanged):
            with pytest.raises(TypeError):
                # Already disconnected → PyQt refuses a second disconnect.
                signal.disconnect(c._on_screen_layout_changed)

    def test_close_disconnects_screen_geometry_signals(self, qapp, monkeypatch):
        from PyQt6.QtWidgets import QApplication

        c = self._controller(monkeypatch)
        screens = list(c._watched_screens)
        c.close()

        assert screens, "test needs at least one screen"
        for screen in screens:
            with pytest.raises(TypeError):
                screen.geometryChanged.disconnect(c._on_screen_layout_changed)

    def test_close_is_idempotent(self, qapp, monkeypatch):
        c = self._controller(monkeypatch)
        c.close()
        c.close()  # must not raise
        assert c._screen_watchers_installed is False

    def test_pyqt_holds_the_slots_weakly_so_there_was_no_leak(self, qapp, monkeypatch):
        """Documents why this is a teardown fix, not a leak fix.

        A discarded controller is collectable even WITHOUT the disconnect,
        because PyQt bound-method slots are weak references. Keeping this
        assertion stops the commit message and docstring from drifting into
        claiming a memory leak that never existed.
        """
        import gc
        import weakref

        c = self._controller(monkeypatch)
        assert c._screen_watchers_installed is True
        ref = weakref.ref(c)
        del c            # no close() — the connections are still in place
        gc.collect()
        assert ref() is None, (
            "a still-connected controller stayed reachable: PyQt would be "
            "holding bound-method slots strongly, making this a real leak"
        )

    def test_closed_controller_stops_re_arming_its_watchers(self, qapp, monkeypatch):
        """The actual defect: the handler re-registers screen watchers."""
        c = self._controller(monkeypatch)
        c.close()

        # Simulate a display-layout change reaching the stale handler.
        c._on_screen_layout_changed()

        assert not c._watched_screens, (
            "a closed controller re-registered geometryChanged watchers"
        )
        assert c._screen_watchers_installed is False


# ── CSP copy-cache priming is throttled and off the interactive path ───────


class TestCspCachePriming:
    """The sync worker warms CSP's copy-address caches so the ~1s locate scan
    is not paid by the first colour write (which made the first brush stroke
    after a slot switch paint the previous colour).
    """

    def _thread(self, csp):
        from core.memory_sync import MemorySyncThread

        t = MemorySyncThread.__new__(MemorySyncThread)
        t.csp_sync = csp
        t._csp_prime_ts = 0.0
        return t

    class _Csp:
        def __init__(self, warm=False, primes_ok=True):
            self._warm = warm
            self._primes_ok = primes_ok
            self.prime_calls = 0

        def sub_copies_are_known(self):
            return self._warm

        def prime_copy_caches(self):
            self.prime_calls += 1
            if self._primes_ok:
                self._warm = True
            return self._warm

    def test_primes_once_when_cold(self):
        csp = self._Csp(warm=False)
        t = self._thread(csp)
        t._prime_csp_copy_caches()
        assert csp.prime_calls == 1
        # Now warm -> further polls must not scan again.
        for _ in range(5):
            t._prime_csp_copy_caches()
        assert csp.prime_calls == 1

    def test_never_primes_when_already_warm(self):
        csp = self._Csp(warm=True)
        t = self._thread(csp)
        t._prime_csp_copy_caches()
        assert csp.prime_calls == 0

    def test_failed_prime_is_throttled_not_retried_every_poll(self):
        """A state that cannot be primed must not scan in a loop."""
        csp = self._Csp(warm=False, primes_ok=False)
        t = self._thread(csp)
        for _ in range(20):          # 20 polls == 2 seconds of the 100ms loop
            t._prime_csp_copy_caches()
        assert csp.prime_calls == 1, (
            "a failing prime rescanned on later polls; each scan costs ~1s on "
            "the sync thread"
        )

    def test_backend_without_the_api_is_skipped_silently(self):
        class Bare:
            pass

        t = self._thread(Bare())
        t._prime_csp_copy_caches()   # must not raise into the poll loop


# ── Native grayscale: the freeze watchdog is not armed ─────────────────────


class TestFaultWatchdogIsDisarmed:
    """The runtime arms a freeze watchdog from GrayscaleOverlay.__init__ (app
    startup), but only touches its heartbeat from _on_frame_swapped (rendering).
    With the filter off the heartbeat stays at 0.0, so the watchdog declared a
    freeze on its first check and every 3 s after, appending every thread's
    traceback to %TEMP%\\colorink_fault.log. Measured on the reporting machine:
    63,070 dumps, 147 MB, all false positives.
    """

    class _Runtime:
        def __init__(self):
            self._fault_watchdog_started = False

    def test_disarms_by_default(self, monkeypatch):
        from core import native_grayscale as ng

        monkeypatch.delenv(ng._WATCHDOG_ENV, raising=False)
        rt = self._Runtime()
        assert ng._disarm_fault_watchdog(rt) is True
        assert rt._fault_watchdog_started is True, (
            "the runtime's re-entry guard must be pre-set so "
            "_install_fault_watchdog() returns before starting a thread"
        )

    def test_env_var_re_arms_for_diagnosis(self, monkeypatch):
        from core import native_grayscale as ng

        monkeypatch.setenv(ng._WATCHDOG_ENV, "1")
        rt = self._Runtime()
        assert ng._disarm_fault_watchdog(rt) is False
        assert rt._fault_watchdog_started is False

    def test_other_env_values_still_disarm(self, monkeypatch):
        from core import native_grayscale as ng

        monkeypatch.setenv(ng._WATCHDOG_ENV, "0")
        rt = self._Runtime()
        assert ng._disarm_fault_watchdog(rt) is True

    def test_runtime_without_the_guard_is_a_noop(self, monkeypatch):
        from core import native_grayscale as ng

        monkeypatch.delenv(ng._WATCHDOG_ENV, raising=False)

        class Bare:
            pass

        rt = Bare()
        assert ng._disarm_fault_watchdog(rt) is False
        assert not hasattr(rt, "_fault_watchdog_started")

    def test_runtime_loader_disarms_before_returning(self, monkeypatch):
        """_ensure_runtime_loaded must disarm before GrayscaleOverlay is built."""
        from core import native_grayscale as ng

        monkeypatch.delenv(ng._WATCHDOG_ENV, raising=False)
        runtime = ng._ensure_runtime_loaded()
        assert runtime._fault_watchdog_started is True

    def test_constructing_the_overlay_starts_no_watchdog_thread(self, monkeypatch):
        """End-to-end against the shipped runtime."""
        import threading

        from core import native_grayscale as ng

        monkeypatch.delenv(ng._WATCHDOG_ENV, raising=False)
        runtime = ng._ensure_runtime_loaded()
        runtime.GrayscaleOverlay(mode="oklch")
        assert not any("watchdog" in t.name for t in threading.enumerate()), \
            "the freeze watchdog thread was started"
