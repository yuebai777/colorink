"""NativeGrayscaleController interface contract tests."""
from pathlib import Path

import pytest

from core import native_grayscale as ng


def test_controller_supports_oklch_luma_and_all_screens(monkeypatch):
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: (_ for _ in ()).throw(ImportError("offline")))
    c = ng.NativeGrayscaleController(mode="luma")
    assert c._mode == "luma"
    assert c.target == "all"
    assert c.available_screens() == ["all"]
    c.set_mode("oklch")
    c.set_mode("luma")
    with pytest.raises(ValueError):
        c.set_mode("unknown")


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
    c.set_target("1: 屏幕 (1920x1080)")
    c.start()
    c.stop()
    c.close()
    assert calls == [
        ("init", "oklch"), ("target", "0"), ("target", "1"),
        ("active", True, None),
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
        self.target = "all"

    def set_mode(self, mode):
        self.mode = mode

    def set_target(self, target):
        self.target = target

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


def test_mode_switch_restarts_active_native_filter(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)

    c.prepare()
    queue.run_all()
    c.start()
    queue.run_all()
    assert c.is_active

    c.set_mode("luma")
    queue.run_all()

    assert c._mode == "luma"
    assert c._impl.mode == "luma"
    assert c.is_active
    assert all(w.shown and not w.offscreen for w in c._impl._overlays)


def test_set_target_restarts_active_native_filter(monkeypatch):
    queue = _TimerQueue()
    c = _make_controller(monkeypatch, queue)

    c.prepare()
    queue.run_all()
    c.start()
    queue.run_all()
    assert c.is_active

    c.set_target("1")
    queue.run_all()

    assert c.target == "1"
    assert c._impl.target == "1"
    assert c.is_active
    assert all(w.shown and not w.offscreen for w in c._impl._overlays)


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


def test_dispatch_invoke_calls_bound_method_without_extra_arg():
    """The GUI dispatcher must not pass the widget twice to a bound method.

    Regression: ``_dispatch_invoke`` used to call ``getattr(widget, name)(widget)``,
    which raised TypeError for the injected ``_colorink_kick_capture`` bound
    method. The exception was swallowed, so the camera-start kick never woke
    the frame loop and the grayscale overlay never received its first frame.
    """
    class DummyOverlay:
        def __init__(self):
            self.called = False

        def _colorink_kick_capture(self):
            self.called = True

    widget = DummyOverlay()
    ng._dispatch_invoke(widget, "_colorink_kick_capture")
    assert widget.called


def test_pseudo_fullscreen_geometry_leaves_bottom_strip_per_screen():
    assert ng.pseudo_fullscreen_geometry(QRect(0, 0, 1920, 1080)) == QRect(0, 0, 1920, 1078)
    assert ng.pseudo_fullscreen_geometry(QRect(0, 0, 1707, 1067)) == QRect(0, 0, 1707, 1065)
    assert ng.pseudo_fullscreen_geometry(QRect(-1920, -200, 1920, 1080)) == QRect(-1920, -200, 1920, 1078)


def test_multi_monitor_native_pseudo_fullscreen_and_uv_sync(monkeypatch):
    class FakeScreen:
        def __init__(self, geo):
            self._geo = geo

        def geometry(self):
            return self._geo

    class FakeVBO:
        def __init__(self):
            self.bound = False
            self.released = False
            self.allocated = None

        def bind(self):
            self.bound = True

        def allocate(self, data, size):
            import struct
            self.allocated = struct.unpack("16f", data)
            self.size = size

        def release(self):
            self.released = True

    class MultiMonWidget(_FakeWidget):
        def __init__(self, idx, screen):
            super().__init__(idx)
            self._screen = screen
            self._vbo = FakeVBO()
            self.setGeometry(screen.geometry())

        def makeCurrent(self):
            pass

        def doneCurrent(self):
            pass

    class MultiMonImpl:
        is_active = False
        is_healthy = True

        def __init__(self, mode):
            self.mode = mode
            self.target = "all"
            self.s0 = FakeScreen(QRect(0, 0, 1707, 1067))
            self.s1 = FakeScreen(QRect(1707, 0, 1920, 1080))
            self.s2 = FakeScreen(QRect(-1920, 0, 1920, 1080))
            self._overlays = []

        def set_mode(self, mode):
            self.mode = mode

        def set_target(self, target):
            self.target = target

        def set_active(self, active, mode=None):
            self.is_active = active
            if active:
                self._overlays = [
                    MultiMonWidget(0, self.s0),
                    MultiMonWidget(1, self.s1),
                    MultiMonWidget(2, self.s2),
                ]
                for w in self._overlays:
                    w._colorink_start_capture()
            else:
                self._overlays = []

    class FakeRuntime:
        GrayscaleOverlay = MultiMonImpl

    queue = _TimerQueue()
    monkeypatch.setattr(QTimer, "singleShot", queue.singleShot)
    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: FakeRuntime())

    c = ng.NativeGrayscaleController()
    c.start()
    queue.run_all()

    overlays = c._impl._overlays
    assert len(overlays) == 3
    assert overlays[0].geometry() == QRect(0, 0, 1707, 1065)
    assert overlays[1].geometry() == QRect(1707, 0, 1920, 1078)
    assert overlays[2].geometry() == QRect(-1920, 0, 1920, 1078)

    # VBO bottom V-coordinate matches pseudo_h / full_h for 1:1 pixel mapping
    assert overlays[0]._vbo.allocated is not None
    assert overlays[0]._vbo.allocated[3] == pytest.approx(1065.0 / 1067.0, rel=1e-5)
    assert overlays[1]._vbo.allocated[3] == pytest.approx(1078.0 / 1080.0, rel=1e-5)

    # Dynamic screen layout change updates each screen's pseudo-fullscreen geometry and UVs
    c._impl.s1._geo = QRect(1707, 100, 2560, 1440)
    c._on_screen_layout_changed()
    assert overlays[1].geometry() == QRect(1707, 100, 2560, 1438)
    assert overlays[1]._vbo.allocated[3] == pytest.approx(1438.0 / 1440.0, rel=1e-5)


def test_poll_transition_honors_not_before_cooldown(monkeypatch):
    """_poll_transition must respect _transition_not_before before clearing it and restarting."""
    import time as time_mod
    from PyQt6.QtCore import QTimer
    import core.native_grayscale as ng

    class DummyImpl:
        is_active = False
        is_healthy = True
        _overlays = []
        def __init__(self, mode="oklch"): pass
        def set_mode(self, m): pass
        def set_target(self, t): pass

    class DummyRuntime:
        GrayscaleOverlay = DummyImpl

    monkeypatch.setattr(ng, "_ensure_runtime_loaded", lambda: DummyRuntime())
    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: scheduled.append((ms, fn)))

    c = ng.NativeGrayscaleController()
    started = []
    c.start = lambda: started.append(True)

    clock = [100.0]
    monkeypatch.setattr(time_mod, "monotonic", lambda: clock[0])

    c._stopping_overlays = []
    c._transition_started_at = 100.0
    c._transition_not_before = 100.25
    c._pending_start = True

    # Before cooldown finishes: must keep waiting and NOT wipe _transition_not_before or start early
    clock[0] = 100.05
    c._poll_transition()
    assert c._transition_not_before == pytest.approx(100.25)
    assert c._pending_start is True
    assert started == []
    assert len(scheduled) == 1

    # Once cooldown finishes: clears _transition_not_before and triggers pending start
    clock[0] = 100.26
    c._poll_transition()
    assert c._transition_not_before == 0.0
    assert c._pending_start is False
    assert started == [True]


def test_install_prewarm_skips_idle_frame_swapped_redraw(monkeypatch):
    """_install_prewarm wraps _on_frame_swapped to avoid redundant OpenGL swaps when desktop is idle."""
    from PyQt6.QtCore import QTimer, pyqtSignal, QObject
    import core.native_grayscale as ng

    class DummyBase(QObject):
        frameSwapped = pyqtSignal()
        def __init__(self):
            super().__init__()
            self._screen = type("S", (), {"refreshRate": lambda s: 60.0})()
            self._camera = None
            self._initialized = True
            self._texture = object()
            self._pending_frame = None
            self._last_frame_ticks = 42.0
            self.visible = True
            self.orig_calls = 0

        def isVisible(self):
            return self.visible

        def show(self): pass
        def cleanup(self): pass
        def initializeGL(self): pass
        def resizeGL(self, w, h): pass
        def _on_frame_swapped(self):
            self.orig_calls += 1

    class DummyRuntime:
        _ShaderOverlay = DummyBase

    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: scheduled.append((ms, fn)))

    ng._install_prewarm(DummyRuntime())
    w = DummyBase()
    cam = type("Cam", (), {"is_capturing": True, "latest_frame_time": 42.0})()
    w._camera = cam

    # Idle: latest_frame_time == _last_frame_ticks -> skip update/draw and schedule idle poll
    w._on_frame_swapped()
    assert w.orig_calls == 0
    assert len(scheduled) == 1
    assert w._colorink_idle_poll_pending is True

    # Duplicate idle call while poll is already queued does not flood timers
    w._on_frame_swapped()
    assert len(scheduled) == 1

    # New frame arrives -> idle poll invokes orig _on_frame_swapped cleanly
    cam.latest_frame_time = 43.0
    scheduled[0][1]()
    assert w._colorink_idle_poll_pending is False
    assert w.orig_calls == 1

