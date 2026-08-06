"""NativeGrayscaleController interface contract tests."""
from pathlib import Path

import pytest

from core import native_grayscale as ng


def test_controller_is_oklch_only_and_all_screens(monkeypatch):
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: (_ for _ in ()).throw(ImportError("offline")))
    c = ng.NativeGrayscaleController(mode="luma")
    assert c._mode == "oklch"
    assert c.target == "all"
    assert c.available_screens() == ["all"]
    c.set_mode("oklch")
    with pytest.raises(ValueError):
        c.set_mode("luma")


def test_controller_delegates_to_validated_gpu_overlay(monkeypatch):
    calls = []
    class Impl:
        is_available = True
        is_active = False
        is_healthy = True
        last_error = ""
        def __init__(self, mode): calls.append(("init", mode))
        def set_target(self, target): calls.append(("target", target))
        def set_mode(self, mode): calls.append(("mode", mode))
        def set_active(self, active, mode=None):
            self.is_active = active; calls.append(("active", active, mode)); return True
        def close(self): calls.append(("close",))
    class Runtime: GrayscaleOverlay = Impl
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: Runtime)
    c = ng.NativeGrayscaleController()
    assert c.is_available and c.is_healthy
    c.set_target("0")
    c.start()
    c.stop()
    c.close()
    assert calls == [
        ("init", "oklch"), ("active", True, None),
        ("active", False, None),
    ]


def test_packaging_declares_validated_oklch_runtime():
    root = Path(__file__).resolve().parents[1]
    for spec_name in ("Colorink.spec", "Colorink Onefile.spec"):
        spec = (root / spec_name).read_text(encoding="utf-8")
        assert "native_grayscale/runtime/grayscale_overlay.pyc" in spec
        assert "dxshare_capture.dll" not in spec
        assert "dxshare_overlay.pyc" not in spec


def test_repeated_same_target_and_mode_do_not_touch_overlay(monkeypatch):
    calls = []
    class Impl:
        is_active = False
        is_healthy = True
        def __init__(self, mode): calls.append(("init", mode))
        def set_target(self, target): calls.append(("target", target))
        def set_mode(self, mode): calls.append(("mode", mode))
    class Runtime: GrayscaleOverlay = Impl
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: Runtime)
    c = ng.NativeGrayscaleController()
    c.set_target("all"); c.set_target("all")
    c.set_mode("oklch"); c.set_mode("oklch")
    assert calls == [("init", "oklch")]

import threading
import time

from PyQt6.QtCore import QRect, QTimer


class _TimerQueue:
    """Fake QTimer.singleShot that records callbacks for manual draining."""

    def __init__(self):
        self.jobs = []

    def singleShot(self, ms, callback):
        self.jobs.append(callback)

    def run_all(self):
        while self.jobs:
            callback = self.jobs.pop(0)
            callback()


class _FakeWidget:
    """Minimal stand-in for the runtime's _ShaderOverlay."""

    def __init__(self, index):
        self._screen_index = index
        self._initialized = True
        self._texture = object()
        self._frame_count = 0
        self._fresh_count = 0
        self._camera_error = ""
        self._camera = None
        self._colorink_init_gen = 0
        self._colorink_release_camera = None
        self._colorink_release_done = None
        self._colorink_target_geometry = QRect(0, 0, 100, 100)
        self._colorink_reveal_ready = False
        self._pending_frame = None
        self._last_frame_ticks = None
        self.shown = False
        self.geo = QRect(0, 0, 100, 100)
        self.start_calls = 0
        self.stop_calls = 0

    def geometry(self):
        return self.geo

    def setGeometry(self, *args):
        if len(args) == 4:
            self.geo = QRect(args[0], args[1], args[2], args[3])
        else:
            self.geo = QRect(args[0])

    @property
    def offscreen(self):
        return self.geo.x() < 0

    def show(self):
        self.shown = True

    def raise_(self):
        pass

    def hide(self):
        self.shown = False

    def _colorink_start_capture(self):
        self.start_calls += 1
        self._camera = object()
        self._frame_count = 1

    def _colorink_stop_capture(self):
        self.stop_calls += 1
        self._colorink_release_camera = self._camera
        self._camera = None
        self._frame_count = 0
        done = threading.Event()
        done.set()
        self._colorink_release_done = done


class _FakeImpl:
    is_active = False
    is_healthy = True

    def __init__(self, mode):
        self._overlays = []
        self.mode = mode

    def set_mode(self, mode):
        self.mode = mode

    def set_active(self, active, mode=None):
        self.is_active = active
        if active and not self._overlays:
            self._overlays = [_FakeWidget(0), _FakeWidget(1)]
            for widget in self._overlays:
                widget._colorink_start_capture()
        elif not active:
            for widget in self._overlays:
                widget._colorink_stop_capture()
            self._overlays = []


class _FakeRuntime:
    GrayscaleOverlay = _FakeImpl


def _make_controller(monkeypatch, queue):
    monkeypatch.setattr(QTimer, "singleShot", queue.singleShot)
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: _FakeRuntime())
    return ng.NativeGrayscaleController()


def test_warm_preheat_releases_capture_when_off(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)

    c.prepare()
    queue.run_all()
    assert c._warmed and not c._warming and not c.is_active
    # After warmup with no pending start, capture is released (light preheat).
    assert all(w._camera is None for w in c._impl._overlays)
    assert all(w.stop_calls >= 1 for w in c._impl._overlays)

    # Warm start: capture restarts and the overlays reveal on the first frame.
    c.start()
    assert c.is_active
    queue.run_all()
    assert all(w.shown and not w.offscreen for w in c._impl._overlays)
    assert all(w._camera is not None for w in c._impl._overlays)
    assert all(w.start_calls >= 2 for w in c._impl._overlays)

    # Warm stop: capture released again, GL overlay kept parked off-screen.
    c.stop()
    assert not c.is_active
    queue.run_all()
    assert c._warmed
    assert all(w._camera is None for w in c._impl._overlays)
    assert all(w.offscreen for w in c._impl._overlays)


def test_quick_off_on_during_release_waits_for_release(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)

    c.prepare()
    queue.run_all()
    c.start()
    queue.run_all()
    assert all(w._camera is not None for w in c._impl._overlays)

    # Warm stop starts an async capture release ...
    c.stop()
    assert c._warm_releasing
    # ... and an immediate toggle-on must wait for the release to finish.
    c.start()
    assert not c.is_active
    queue.run_all()
    assert c.is_active
    assert all(w._camera is not None for w in c._impl._overlays)


def test_camera_error_fails_fast(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)
    c.prepare()
    queue.run_all()

    class FailingWidget(_FakeWidget):
        def _colorink_start_capture(self):
            self._frame_count = 0
            self._camera_error = "dxcam 采集初始化失败: test"

    c._impl._overlays[0] = FailingWidget(0)
    c.start()
    queue.run_all()
    assert not c.is_active
    assert "dxcam 采集初始化失败" in c.last_error


def test_cold_lifecycle_still_destroys_overlays(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)

    c.start()  # cold: no warm cache yet
    assert c.is_active
    queue.run_all()
    assert c._impl._overlays and all(w.shown for w in c._impl._overlays)

    c.stop()  # cold stop destroys the overlays
    queue.run_all()
    assert not c._impl._overlays
    assert not c._warmed


def test_stop_capture_patch_releases_camera_off_thread():
    class DummyOverlay:
        def show(self):
            pass

        def cleanup(self):
            pass

    class DummyRuntime:
        _ShaderOverlay = DummyOverlay

    ng._install_prewarm(DummyRuntime())

    released = []

    class FakeCamera:
        def release(self):
            released.append(True)

    widget = DummyOverlay()
    widget._camera = FakeCamera()
    widget._frame_count = 5
    widget._colorink_init_gen = 0

    DummyOverlay._colorink_stop_capture(widget)
    assert widget._camera is None
    assert widget._frame_count == 0

    deadline = time.monotonic() + 2.0
    while not widget._colorink_release_done.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert widget._colorink_release_done.is_set()
    assert released == [True]


def test_async_camera_init_kicks_gui_capture_loop(monkeypatch):
    """A background camera start must wake the Qt-side frame loop."""
    class DummyOverlay:
        def show(self):
            pass

        def cleanup(self):
            pass

    class DummyRuntime:
        _ShaderOverlay = DummyOverlay

    ng._install_prewarm(DummyRuntime())

    kicked = threading.Event()

    class FakeScreen:
        def refreshRate(self):
            return 60

    class FakeCamera:
        is_capturing = False
        is_released = False

        def start(self, **kwargs):
            self.is_capturing = True

        def release(self):
            self.is_released = True

    camera = FakeCamera()

    class FakeDxcam:
        @staticmethod
        def create(**kwargs):
            return camera

    monkeypatch.setitem(__import__("sys").modules, "dxcam", FakeDxcam)
    monkeypatch.setattr(ng, "_post_to_gui", lambda widget, method: getattr(widget, method)())

    widget = DummyOverlay()
    widget._screen_index = 0
    widget._screen = FakeScreen()
    widget._camera = None
    widget._colorink_init_gen = 0
    widget._camera_error = ""
    widget._frame_count = 0
    widget._fresh_count = 0
    widget._pending_frame = None
    widget._last_frame_ticks = None
    widget._colorink_kick_capture = lambda: kicked.set()

    DummyOverlay._init_camera(widget)

    assert kicked.wait(2.0)
    assert widget._camera is camera
    assert not widget._camera_error
