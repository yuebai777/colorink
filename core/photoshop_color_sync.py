#!/usr/bin/env python3
"""Photoshop colour sync via the unified user-level CEP script bridge.

Every Photoshop edition — genuine, green/portable, or custom install — shares
ONE sync path: the user-level CEP bridge deployed into
``%APPDATA%\\Adobe\\CEP\\extensions\\ColorinkBridge`` (see
:mod:`core.photoshop_script_bridge`), routed per instance by PID.

Supports both colour slots: ``color_index`` 0 = foreground (main),
1 = background (sub). Photoshop has no alpha channel, so transparent colours
are skipped by the sync layer.
"""

import ctypes
import sys
import time

import psutil

from core.photoshop_instances import (
    SCRIPT_BRIDGE_KIND,
    PhotoshopInstance,
    detect_instances,
    pick_target,
)
from core.photoshop_script_bridge import (
    PANEL_VERSION,
    PhotoshopScriptBridge,
)

# ---- constants -----------------------------------------------------------

PROCESS_NAME = "Photoshop.exe"
DEBUG = False


def log(msg: str) -> None:
    if DEBUG:
        print(f"[PhotoshopSync] {msg}", file=sys.stderr, flush=True)


def clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


# ---------------------------------------------------------------------------
# PhotoshopSync
# ---------------------------------------------------------------------------


class PhotoshopSync:
    """Colour bridge to Adobe Photoshop via the user-level CEP script bridge.

    Usage::

        ps = PhotoshopSync()
        ps.connect()
        rgb = ps.get_color()              # -> {'r': 128, 'g': 64, 'b': 32, 'index': 0}
        rgb_bg = ps.get_bg_color()        # -> {..., 'index': 1}
        ps.set_color(255, 0, 0)           # -> True (foreground)
        ps.set_color(0, 0, 255, 1)        # -> True (background)
        print(ps.status())                # -> {connected, backend, pid, ...}
    """

    def __init__(self) -> None:
        self._pid: int | None = None
        self._proc_handle: int = 0     # Win32 process handle for fast alive check
        self.current_version: str = "auto"  # selected instance label
        self.process_name: str = PROCESS_NAME
        self.backend: str = ""       # "" | "script-bridge"
        self._bridge: PhotoshopScriptBridge | None = None
        self._instances: list[PhotoshopInstance] = []
        self._detect_ts: float = 0.0
        # User-facing reason for the last failed connect / read / write.
        self.last_error: str = ""
        self.permission_issue: bool = False

    # -- instance discovery -----------------------------------------------------

    def _detect(self, force: bool = False) -> list[PhotoshopInstance]:
        """Cached (2 s TTL) snapshot of the running Photoshop instances."""
        now = time.monotonic()
        if force or now - self._detect_ts > 2.0:
            try:
                self._instances = detect_instances()
            except Exception:
                self._instances = []
            self._detect_ts = now
        return self._instances

    # -- connect -----------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to a running Photoshop instance via the user-level CEP bridge."""
        # Re-use healthy bridge (deployed + PS running)
        if (self._bridge is not None and self._bridge.is_deployed()
                and self._is_process_alive()):
            return True

        self._reset()

        instances = self._detect()
        target = pick_target(instances, self.current_version)
        if target is None:
            self.last_error = "未检测到运行中的 Photoshop 进程（请先启动 Photoshop）"
            return False

        self._pid = target.pid
        return self._connect_bridge(target)

    def _connect_bridge(self, target: PhotoshopInstance) -> bool:
        """Deploy the shared user-level CEP bridge and target *target*.

        One user-level extension serves every Photoshop (no admin rights
        needed, no per-install copies); commands are addressed to
        ``target.pid`` so multiple instances coexist. Legacy per-install
        copies (v1..v5) are removed so no stale panel keeps running in
        another instance.
        """
        # Remove legacy per-install bridge copies from every running PS.
        try:
            removed = PhotoshopScriptBridge.cleanup_install_dirs()
            if removed:
                log(f"Removed legacy per-install ColorinkBridge from: {removed}")
        except Exception:
            pass
        self._bridge = PhotoshopScriptBridge()
        if not self._bridge.deploy():
            self.last_error = (
                "同步桥部署失败（目录不可写？请以管理员身份运行）"
            )
            return False
        self.backend = SCRIPT_BRIDGE_KIND
        if self._bridge.is_alive(self._pid):
            self.last_error = ""
            log(f"Script bridge alive for PID={self._pid}")
            return True
        # Deployed but the panel is not running yet — it loads when
        # Photoshop restarts. Writes are queued into cmd.txt meanwhile.
        self.last_error = "同步桥已部署：重启 Photoshop 后生效"
        log("Script bridge deployed, awaiting Photoshop restart")
        return True

    def remove_bridge(self) -> bool:
        """Delete the deployed bridge extension.

        The running Photoshop keeps the already-loaded panel until it is
        restarted, so callers should tell the user to restart Photoshop
        after a successful removal.
        """
        if self._bridge is not None:
            return self._bridge.remove()
        return False

    # -- process liveness --------------------------------------------------------

    K32 = ctypes.windll.kernel32
    # 64 位安全：HANDLE 是指针宽度，必须显式声明 restype/argtypes，
    # 否则默认按 32 位 c_int 截断。
    K32.OpenProcess.restype = ctypes.c_void_p
    K32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    K32.WaitForSingleObject.restype = ctypes.c_uint32
    K32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    K32.CloseHandle.argtypes = (ctypes.c_void_p,)

    def _is_process_alive(self) -> bool:
        """Check whether the cached Photoshop process is still running.

        Uses WaitForSingleObject (0ms timeout) on the process handle —
        returns instantly, unlike psutil which creates Python objects.
        When OpenProcess is denied (elevated Photoshop vs non-elevated
        Colorink) fall back to process enumeration, which sees every
        process regardless of elevation.
        """
        if self._pid is None:
            return False
        if not self._proc_handle:
            # SYNCHRONIZE access — just enough to wait on the handle
            self._proc_handle = self.K32.OpenProcess(0x00100000, False, self._pid)
            if not self._proc_handle:
                try:
                    return any(
                        p.info["pid"] == self._pid
                        for p in psutil.process_iter(["pid"])
                    )
                except Exception:
                    return False
        # WAIT_OBJECT_0 (0) = process exited; anything else = still alive
        return self.K32.WaitForSingleObject(self._proc_handle, 0) != 0

    def _ensure_bridge(self) -> bool:
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            return True
        return self.connect() and self._bridge is not None

    # -- slot readers ---------------------------------------------------------------

    def get_color(self) -> dict[str, int] | None:
        """Read the current Photoshop foreground colour (slot 0)."""
        if not self._ensure_bridge() or self._bridge is None:
            return None
        state = self._bridge.read_state(self._pid)
        if state is not None:
            fg = state["fg"]
            return {"r": fg["r"], "g": fg["g"], "b": fg["b"], "index": 0}
        return None

    def get_bg_color(self) -> dict[str, int] | None:
        """Read the current Photoshop background colour (slot 1)."""
        if not self._ensure_bridge() or self._bridge is None:
            return None
        state = self._bridge.read_state(self._pid)
        if state is not None:
            bg = state["bg"]
            return {"r": bg["r"], "g": bg["g"], "b": bg["b"], "index": 1}
        return None

    # -- slot writers ---------------------------------------------------------------

    def swap_slots(self) -> bool:
        """Swap Photoshop's foreground/background (like pressing X)."""
        if not self._ensure_bridge() or self._bridge is None:
            return False
        return self._bridge.send_swap(str(time.time_ns()), self._pid)

    def set_color(self, r: int, g: int, b: int, color_index: int = 0) -> bool:
        """Write a colour to the foreground (0) or background (1) slot."""
        r, g, b = clamp8(r), clamp8(g), clamp8(b)
        if not self._ensure_bridge() or self._bridge is None:
            return False
        return self._bridge.send_color(
            str(time.time_ns()), self._pid, color_index, r, g, b)

    def set_both_colors(self, fg_r: int, fg_g: int, fg_b: int,
                        bg_r: int, bg_g: int, bg_b: int) -> bool:
        """Write both foreground and background colours in a single atomic command."""
        fg_r, fg_g, fg_b = clamp8(fg_r), clamp8(fg_g), clamp8(fg_b)
        bg_r, bg_g, bg_b = clamp8(bg_r), clamp8(bg_g), clamp8(bg_b)
        if not self._ensure_bridge() or self._bridge is None:
            return False
        return self._bridge.send_both_colors(
            str(time.time_ns()), self._pid,
            fg_r, fg_g, fg_b, bg_r, bg_g, bg_b)

    # -- status / meta -----------------------------------------------------------

    def status(self) -> dict[str, object]:
        # Event-driven bridge: the heartbeat only refreshes while the user is
        # interacting with Photoshop, so "connected" means "deployed + PS running".
        connected = (self._bridge is not None
                     and self._bridge.is_deployed()
                     and self._is_process_alive())
        if not connected:
            self.connect()
            connected = (self._bridge is not None
                         and self._bridge.is_deployed()
                         and self._is_process_alive())
        return {
            "connected": connected,
            "pid": self._pid if connected else None,
            "version": self.current_version,
            "processName": self.process_name,
            "backend": self.backend,
            "bridgeAlive": bool(self._bridge is not None
                                and self._bridge.is_alive(self._pid)),
            # True when the deployed panel file is newer than the panel
            # actually running inside Photoshop (user must restart PS).
            "panelStale": bool(self._bridge is not None
                               and self._bridge.panel_version(self._pid)
                               != PANEL_VERSION),
            "lastError": self.last_error,
        }

    @property
    def pid(self) -> int | None:
        """Return the active Photoshop process ID, or None."""
        return self._pid

    def touch_client_alive(self) -> None:
        """Signal to the CEP bridge that Colorink is alive."""
        if self._bridge is not None:
            self._bridge.touch_client_alive()

    def set_drawing(self, is_drawing: bool) -> None:
        """Flag whether the user is actively painting in Photoshop."""
        if self._bridge is not None:
            self._bridge.set_drawing(is_drawing)

    def note_color_applied(self, window_ms: int = 1500) -> None:
        """Mark a short 'assumed drawing' window after handing PS a colour."""
        if self._bridge is not None:
            self._bridge.note_color_applied(window_ms)

    def assume_drawing_active(self) -> bool:
        """True while the post-write 'assumed drawing' window is open."""
        if self._bridge is not None:
            return self._bridge.assume_drawing_active()
        return False

    def cleanup_runtime_flags(self) -> None:
        """Clean up transient runtime flag files."""
        if self._bridge is not None:
            self._bridge.cleanup_runtime_flags()

    def set_version(self, version: str) -> bool:
        """Select the sync target: ``"auto"`` or an instance label from
        :func:`core.photoshop_instances.detect_instances`."""
        version = str(version or "auto").strip()
        if version == self.current_version:
            return False
        self.current_version = version
        self._reset()
        log(f"Target changed to {version}")
        return True

    def recheck(self) -> bool:
        """Force instance re-detection and reconnect.

        Used after the user restarted Photoshop or changed installs, when
        the 2 s detection cache would otherwise hide the new state.
        """
        self._detect(force=True)
        return self.connect()

    def status_lite(self) -> dict[str, object]:
        """Snapshot of the current connection state WITHOUT connecting.

        Safe to call from the UI thread: never blocks on detection or
        deployment. The sync loop keeps the real state fresh via :meth:`status`.
        """
        bridge_ok = (self._bridge is not None
                     and self._bridge.is_deployed()
                     and self._is_process_alive())
        return {
            "connected": bridge_ok,
            "pid": self._pid if bridge_ok else None,
            "version": self.current_version,
            "processName": self.process_name,
            "backend": self.backend,
            "bridgeAlive": bool(self._bridge is not None
                                and self._bridge.is_alive(self._pid)),
            "panelStale": bool(self._bridge is not None
                               and self._bridge.panel_version(self._pid)
                               != PANEL_VERSION),
            "lastError": self.last_error,
        }

    def dump(self) -> dict[str, object]:
        color = self.get_color()
        if color is None:
            return {"error": "not connected"}
        return {
            "pid": self._pid,
            "version": self.current_version,
            "processName": self.process_name,
            "backend": self.backend,
            "color": color,
            "lastError": self.last_error,
        }

    # -- internal helpers --------------------------------------------------------

    def _reset(self) -> None:
        if self._proc_handle:
            self.K32.CloseHandle(self._proc_handle)
            self._proc_handle = 0
        self._pid = None
        self._bridge = None
        self.backend = ""
