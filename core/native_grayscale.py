"""Validated native fullscreen grayscale controller.

This path uses the proven dxcam/Desktop-Duplication capture loop, asynchronous
PBO texture uploads, and OpenGL OKLab-L / Luma fragment shaders. It is not the
broken WGL shared-texture experiment: that runtime is deliberately excluded.

Toggle latency
--------------
The first Ctrl+G used to pay, on the GUI thread: ``import dxcam`` (~0.4s),
serial per-screen dxcam create/start, and — worst case — a 5 × 1s retry loop
when ``dxcam.create()`` handed back the *previous* session's still-releasing
camera (dxcam's DXFactory returns the cached camera until ``release()``
finishes). That made a healthy toggle ~1s and a stale one ~5s+.

The controller now:
  * pre-imports dxcam at startup (off the GUI thread) — see main_window.py;
  * creates/starts cameras on background worker threads, in parallel per
    screen, so the GUI thread never blocks on D3D/duplication setup;
  * force-releases a stale camera and retries with short 0.2s delays instead
    of sleeping 1s and failing after ~5s;
  * keeps the OpenGL overlay widgets parked off-screen between toggles (light
    preheat) but stops + releases dxcam capture when the filter is off, so no
    capture thread or Desktop Duplication handle is held while off;
  * surfaces a camera failure immediately (fail fast) instead of waiting for
    the reveal deadline and showing a generic "first frame not ready".
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import gc
import os
import threading
import sys
import time
from pathlib import Path

# Stale-camera recovery: bounded retries with short backoff (the old runtime
# used 5 attempts × 1.0s sleeps, which turned a stuck previous camera into a
# ~5s toggle failure).
_DX_CAMERA_ATTEMPTS = 3
_DX_CAMERA_RETRY_DELAY = 0.2
_DX_CAMERA_STALE_RELEASE_WAIT = 2.0


def _post_to_gui(widget, method_name: str) -> None:
    """Invoke a Qt slot on the widget's GUI thread from a worker thread."""
    from PyQt6.QtCore import QMetaObject, Qt

    QMetaObject.invokeMethod(
        widget,
        method_name,
        Qt.ConnectionType.QueuedConnection,
    )


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parents[1]


def _ensure_runtime_loaded():
    name = "colorink_native._validated_grayscale_runtime"
    if name in sys.modules:
        return sys.modules[name]
    path = _runtime_root() / "native_grayscale/runtime/grayscale_overlay.pyc"
    if not path.exists() and getattr(sys, "frozen", False):
        path = _runtime_root() / "runtime/grayscale_overlay.pyc"
    if not path.exists():
        raise ImportError(f"OKLCh 灰度运行时缺失: {path}")
    loader = importlib.machinery.SourcelessFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise ImportError("无法创建 OKLCh 灰度运行时")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    _install_prewarm(module)
    return module


def _install_prewarm(runtime):
    """Patch the runtime overlay for off-screen warmup and fast toggles.

    Everything here replaces blocking runtime behaviour without touching the
    shipped ``grayscale_overlay.pyc``: camera init becomes async (parallel
    per screen), stop/release becomes async, and the overlay widgets can be
    parked off-screen between toggles so their GL contexts stay alive.
    """
    overlay_cls = runtime._ShaderOverlay
    if getattr(overlay_cls, "_colorink_prewarm_installed", False):
        return

    original_show = overlay_cls.show

    def prewarm_show(widget, *args, **kwargs):
        if not getattr(widget, "_colorink_reveal_ready", False):
            geometry = widget.geometry()
            widget._colorink_target_geometry = geometry
            widget.setGeometry(-10000, -10000, geometry.width(), geometry.height())
        return original_show(widget, *args, **kwargs)

    overlay_cls.show = prewarm_show
    overlay_cls._colorink_original_show = original_show

    # ------------------------------------------------------------------
    # Async dxcam init (replaces the runtime's blocking _init_camera).
    # ------------------------------------------------------------------
    def _init_camera_async(widget):
        """Create+start dxcam capture off the GUI thread.

        The runtime's original version ran inline in ``initializeGL`` on the
        GUI thread and retried 5 × 1s when dxcam.create() returned the
        previous session's still-releasing camera. This replacement:
          * never blocks the GUI thread (spawns a worker per overlay, so
            multiple screens initialize in parallel);
          * force-releases a stale camera with a bounded wait and retries
            with a short delay;
          * records a fast, human-readable error on ``widget._camera_error``
            so the controller can fail immediately.
        """
        lock = getattr(widget, "_colorink_camera_lock", None)
        if lock is None:
            lock = threading.Lock()
            widget._colorink_camera_lock = lock
        gen = getattr(widget, "_colorink_init_gen", 0)

        def run():
            try:
                _run()
            except Exception:
                # Safety net: the widget may have been torn down while the
                # worker was initializing (e.g. app exit). Never let that
                # crash a daemon thread.
                pass

        def _run():
            with lock:
                if gen != getattr(widget, "_colorink_init_gen", gen):
                    # A newer toggle superseded this attempt (e.g. user
                    # switched the filter off/on again while we were initing).
                    return
                if getattr(widget, "_camera", None) is not None:
                    return
                widget._camera_error = ""
                widget._frame_count = 0
                widget._fresh_count = 0
                widget._pending_frame = None
                widget._last_frame_ticks = None
                try:
                    import dxcam
                except Exception as exc:
                    widget._camera_error = (
                        f"dxcam 导入失败: {type(exc).__name__}: {exc}"
                    )
                    return
                last_err = ""
                for attempt in range(_DX_CAMERA_ATTEMPTS):
                    cam = None
                    try:
                        cam = dxcam.create(
                            output_idx=widget._screen_index,
                            output_color="BGR",
                            max_buffer_len=2,
                            processor_backend="numpy",
                        )
                        if getattr(cam, "is_capturing", False) or getattr(
                            cam, "is_released", False
                        ):
                            # Stale camera from a previous session: dxcam's
                            # factory keeps returning it until release()
                            # finishes. Bound the wait so a stuck capture
                            # thread cannot stall the toggle for 10s.
                            stale_done = threading.Event()

                            def _release_stale(cam=cam, ev=stale_done):
                                try:
                                    cam.release()
                                finally:
                                    ev.set()

                            threading.Thread(
                                target=_release_stale,
                                daemon=True,
                                name="colorink-dxcam-stale-release",
                            ).start()
                            stale_done.wait(timeout=_DX_CAMERA_STALE_RELEASE_WAIT)
                            cam = None
                            time.sleep(_DX_CAMERA_RETRY_DELAY)
                            continue
                        cam.start(
                            target_fps=max(30, round(widget._screen.refreshRate())),
                            video_mode=True,
                        )
                        if gen == getattr(widget, "_colorink_init_gen", gen):
                            widget._camera = cam
                            # initializeGL called _on_frame_swapped before this
                            # worker finished. Wake it now so the first frame
                            # reaches paintGL and the reveal gate can open.
                            _post_to_gui(
                                widget,
                                "_colorink_kick_capture",
                            )
                        else:
                            # Superseded by a newer toggle; do not attach.
                            try:
                                cam.release()
                            except Exception:
                                pass
                        return
                    except Exception as exc:
                        last_err = f"{type(exc).__name__}: {exc}"
                        if cam is not None:
                            try:
                                cam.release()
                            except Exception:
                                pass
                            cam = None
                        time.sleep(_DX_CAMERA_RETRY_DELAY)
                if gen == getattr(widget, "_colorink_init_gen", gen):
                    widget._camera_error = f"dxcam 采集初始化失败: {last_err}"

        threading.Thread(
            target=run, daemon=True, name="colorink-dxcam-init"
        ).start()

    overlay_cls._init_camera = _init_camera_async
    overlay_cls._colorink_start_capture = _init_camera_async

    # QMetaObject.invokeMethod needs a registered Qt slot. Install one on the
    # runtime class so the worker can wake the frame loop safely on the GUI
    # thread after camera.start() succeeds.
    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot()
    def _colorink_kick_capture(widget):
        widget._on_frame_swapped()

    overlay_cls._colorink_kick_capture = _colorink_kick_capture

    # ------------------------------------------------------------------
    # Stop capture while keeping the overlay (and its GL context) alive.
    # ------------------------------------------------------------------
    def _colorink_stop_capture(widget):
        """Stop+release the widget's dxcam camera off the GUI thread.

        Unlike ``cleanup()``, this keeps the QOpenGLWidget, its context,
        shader program and PBOs alive, so the next toggle only has to
        create/start the camera (and reveal) instead of rebuilding the whole
        GL stack. No capture thread or Desktop Duplication handle is held
        while the filter is off.
        """
        widget._colorink_init_gen = getattr(widget, "_colorink_init_gen", 0) + 1
        camera = getattr(widget, "_camera", None)
        widget._camera = None
        widget._frame_count = 0
        widget._fresh_count = 0
        widget._pending_frame = None
        widget._last_frame_ticks = None
        done = threading.Event()
        widget._colorink_release_camera = camera
        widget._colorink_release_done = done
        if camera is not None:
            def release_camera(cam=camera):
                try:
                    cam.release()
                finally:
                    done.set()

            threading.Thread(
                target=release_camera,
                daemon=True,
                name="colorink-dxcam-release",
            ).start()

    overlay_cls._colorink_stop_capture = _colorink_stop_capture

    original_cleanup = overlay_cls.cleanup

    def safe_cleanup(widget):
        """Final teardown: release DXCamera asynchronously; never join on GUI."""
        try:
            widget.frameSwapped.disconnect(widget._on_frame_swapped)
        except Exception:
            pass
        camera = getattr(widget, "_camera", None)
        widget._camera = None
        done = threading.Event()
        widget._colorink_release_camera = camera
        widget._colorink_release_done = done
        if camera is not None:
            def release_camera():
                try:
                    camera.release()
                finally:
                    done.set()

            threading.Thread(
                target=release_camera,
                daemon=True,
                name="colorink-dxcam-release",
            ).start()
        # Match the original cleanup for PBO resources without invoking its
        # asynchronous stop-only path.
        for pbo in list(getattr(widget, "_pbos", [])):
            try:
                pbo.destroy()
            except Exception:
                pass
        widget._pbos = []

    overlay_cls.cleanup = safe_cleanup
    overlay_cls._colorink_original_cleanup = original_cleanup
    overlay_cls._colorink_prewarm_installed = True


class NativeGrayscaleController:
    """Application facade for the validated native capture overlay."""

    def __init__(self, mode: str = "oklch"):
        self._impl = None
        self._mode = mode if mode in ("oklch", "luma") else "oklch"
        self._target = "all"
        self.last_error = ""
        self._reveal_timer = None
        self._reveal_deadline = 0.0
        self._stopping_overlays = []
        self._pending_start = False
        self._transition_poll_scheduled = False
        self._transition_not_before = 0.0
        self._transition_started_at = 0.0
        self._warm_releasing = False
        self._warmed = False
        self._warming = False
        self._user_active = False
        try:
            runtime = _ensure_runtime_loaded()
            self._impl = runtime.GrayscaleOverlay(mode=self._mode)
        except Exception as exc:
            self.last_error = f"Native 滤镜加载失败: {exc}"

    @property
    def is_available(self) -> bool:
        return self._impl is not None

    @property
    def is_active(self) -> bool:
        return self._user_active

    @property
    def is_healthy(self) -> bool:
        return bool(self._impl is not None and self._impl.is_healthy)

    def set_mode(self, mode: str) -> None:
        if mode not in ("oklch", "luma"):
            raise ValueError("Native 后端仅支持 OKLCh 或 Luma")
        if self._mode == mode:
            return
        was_active = self.is_active
        if was_active:
            self.stop()
        if self._impl is not None:
            if self._impl.is_active:
                self._impl.set_active(False)
                self._warmed = False
                self._warming = False
            self._impl.set_mode(mode)
        self._mode = mode
        if was_active:
            self.start()

    @staticmethod
    def _normalize_target(target) -> str:
        value = str(target or "all").strip()
        if value != "all" and ":" in value:
            value = value.split(":", 1)[0].strip()
        return value

    def set_target(self, target) -> None:
        target = self._normalize_target(target)
        if target == self._target:
            return
        was_active = self.is_active
        if was_active:
            self.stop()
        if self._impl is not None:
            if self._impl.is_active:
                self._impl.set_active(False)
                self._warmed = False
                self._warming = False
            self._impl.set_target(target)
        self._target = target
        if was_active:
            self.start()

    @property
    def target(self) -> str:
        return self._target

    @staticmethod
    def available_screens() -> list[str]:
        try:
            runtime = _ensure_runtime_loaded()
            return list(runtime.GrayscaleOverlay.available_screens())
        except Exception:
            return ["all"]

    def prepare(self) -> None:
        """Warm OpenGL/PBO/capture resources while keeping the overlay hidden."""
        if self._impl is None or self._warming or self._warmed:
            return
        if self._impl.is_active:
            return
        self._warming = True
        self.set_mode(self._mode)
        self._impl.set_active(True)
        self._reveal_deadline = time.monotonic() + 3.0
        self._schedule_reveal()

    def _reveal_overlays(self) -> None:
        for widget in list(getattr(self._impl, "_overlays", [])):
            geometry = getattr(widget, "_colorink_target_geometry", None)
            if geometry is not None:
                widget.setGeometry(geometry)
            widget._colorink_reveal_ready = True
            widget.show()
            widget.raise_()

    def _hide_warm_overlays(self) -> None:
        # Park the overlay windows off-screen but keep them *shown*: hiding a
        # QOpenGLWidget can destroy its GL context, which would defeat the
        # light preheat. The runtime's prewarm_show() used the same off-screen
        # trick during warmup.
        for widget in list(getattr(self._impl, "_overlays", [])):
            geometry = widget.geometry()
            widget.setGeometry(-10000, -10000, geometry.width(), geometry.height())
            widget._colorink_reveal_ready = False

    def _start_capture_all(self) -> None:
        """Start capture on every overlay in parallel background threads."""
        overlays = list(getattr(self._impl, "_overlays", []))
        for widget in overlays:
            # Invalidate any in-flight init and reset the reveal gate
            # synchronously, so a stale frame_count from the previous session
            # cannot unlock the overlay before a fresh frame actually arrives.
            widget._colorink_init_gen = getattr(widget, "_colorink_init_gen", 0) + 1
            widget._frame_count = 0
            widget._fresh_count = 0
            widget._pending_frame = None
            widget._last_frame_ticks = None
            widget._camera_error = ""
        for widget in overlays:
            widget._colorink_start_capture()

    def _release_cameras(self) -> list:
        """Stop+release capture on all overlays; keep the GL overlay alive."""
        tracked = []
        for widget in list(getattr(self._impl, "_overlays", [])):
            widget._colorink_stop_capture()
            if getattr(widget, "_colorink_release_camera", None) is not None:
                tracked.append(widget)
        return tracked

    def start(self) -> None:
        if self._impl is None:
            raise RuntimeError(self.last_error or "Native 滤镜不可用")
        if self._stopping_overlays:
            self._pending_start = True
            self._schedule_transition_poll()
            return
        if self._warming:
            self._pending_start = True
            return
        if self._warmed:
            # Light preheat hit: the GL overlay is already alive off-screen,
            # so only (re)create/start the cameras and reveal once the first
            # fresh frame arrives.
            self._user_active = True
            self._start_capture_all()
            self._reveal_deadline = time.monotonic() + 2.0
            self._schedule_reveal()
            return
        if self._impl.is_active:
            self._user_active = True
            self._schedule_reveal()
            return
        self.set_mode(self._mode)
        self._impl.set_active(True)
        if not self._impl.is_active:
            raise RuntimeError("Native 覆盖层没有成功激活")
        self._user_active = True
        self._reveal_deadline = time.monotonic() + 2.0
        self._schedule_reveal()

    def _schedule_reveal(self) -> None:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._poll_reveal)

    def _poll_reveal(self) -> None:
        if self._impl is None or not self._impl.is_active:
            return
        overlays = list(getattr(self._impl, "_overlays", []))
        error = next(
            (
                getattr(w, "_camera_error", "")
                for w in overlays
                if getattr(w, "_camera_error", "")
            ),
            "",
        )
        if error:
            self.last_error = error
            self.stop()
            return
        ready = bool(overlays) and all(
            getattr(widget, "_initialized", False)
            and getattr(widget, "_texture", None) is not None
            and getattr(widget, "_frame_count", 0) > 0
            for widget in overlays
        )
        if ready:
            if self._warming:
                self._warming = False
                self._warmed = True
                self._hide_warm_overlays()
                if self._pending_start:
                    # The user already asked for the filter during warmup;
                    # keep capture running and reveal it now.
                    self._pending_start = False
                    self._user_active = True
                    self._reveal_overlays()
                    return
                # Filter stays off after warmup: release the capture so no
                # thread / duplication handle is held (light preheat).
                self._warm_releasing = True
                self._transition_started_at = time.monotonic()
                self._stopping_overlays = self._release_cameras()
                self._schedule_transition_poll()
                return
            self._reveal_overlays()
            return
        if time.monotonic() < self._reveal_deadline:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(16, self._poll_reveal)
            return
        self.last_error = "Native 首帧未准备好，已阻止显示未初始化覆盖层"
        self.stop()

    def stop(self) -> None:
        self._pending_start = False
        self._user_active = False
        if self._impl is None:
            return
        if self._warmed or self._warming:
            # Light preheat: keep the capture runtime warm *while on*, but
            # release dxcam when off so no capture thread or Desktop
            # Duplication handle is held between toggles.
            self._hide_warm_overlays()
            self._warm_releasing = True
            self._transition_started_at = time.monotonic()
            self._stopping_overlays = self._release_cameras()
            self._schedule_transition_poll()
            return
        overlays = list(getattr(self._impl, "_overlays", []))
        for widget in overlays:
            widget.hide()
        # cleanup() clears widget._camera before its asynchronous stop thread
        # finishes, so retain the camera objects themselves for polling.
        self._stopping_overlays = overlays
        self._transition_not_before = time.monotonic() + 0.25
        self._transition_started_at = time.monotonic()
        if self._impl.is_active:
            self._impl.set_active(False)
        self._schedule_transition_poll()

    def _schedule_transition_poll(self) -> None:
        from PyQt6.QtCore import QTimer
        if self._transition_poll_scheduled:
            return
        self._transition_poll_scheduled = True
        QTimer.singleShot(25, self._poll_transition)

    def _poll_transition(self) -> None:
        self._transition_poll_scheduled = False
        self._transition_not_before = 0.0
        self._warming = False
        if not self._warm_releasing:
            self._warmed = False
        self._user_active = False
        still_capturing = time.monotonic() < self._transition_not_before
        for widget in self._stopping_overlays:
            camera = getattr(widget, "_colorink_release_camera", None)
            done = getattr(widget, "_colorink_release_done", None)
            try:
                if camera is not None and (camera.is_capturing or not camera.is_released):
                    still_capturing = True
                    break
                if done is not None and not done.is_set():
                    still_capturing = True
                    break
            except Exception:
                # A torn-down camera is safe to treat as stopped.
                pass
        if still_capturing:
            if time.monotonic() - self._transition_started_at > 8.0:
                # A stuck dxcam release (capture thread blocked in
                # DuplicateOutput) must not wedge the toggle forever. Drop the
                # wait; the next start's stale-camera recovery will retry or
                # surface a clear error once the old thread dies.
                self._stopping_overlays.clear()
                self._warm_releasing = False
                gc.collect()
                if self._pending_start:
                    self._pending_start = False
                    self.start()
                return
            self._schedule_transition_poll()
            return
        self._stopping_overlays.clear()
        self._warm_releasing = False
        gc.collect()
        if self._pending_start:
            self._pending_start = False
            self.start()

    def set_active(self, active: bool, mode: str | None = None) -> bool:
        if mode is not None:
            self.set_mode(mode)
        if active:
            self.start()
        else:
            self.stop()
        return True

    def toggle(self) -> bool:
        try:
            self.set_active(not self.is_active)
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"Native 滤镜切换失败: {exc}"
            return False

    def close(self) -> None:
        self._pending_start = False
        self._user_active = False
        self._warmed = False
        self._warming = False
        self._warm_releasing = False
        self._stopping_overlays.clear()
        if self._impl is not None:
            if self._impl.is_active:
                self._impl.set_active(False)
