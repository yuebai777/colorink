#!/usr/bin/env python3
"""Photoshop colour sync — COM automation + green-edition script bridge.

Two backends, chosen automatically per running instance:

- **COM** (``PhotoshopSync`` classic path): registered Photoshop installs
  are driven through the COM automation interface (``ForegroundColor`` /
  ``BackgroundColor`` property mutation). Fast, live, full read-back.
- **script-bridge**: green / portable editions register no COM interface,
  so Colorink deploys an ExtendScript file bridge into the install's
  ``Presets/Scripts`` folder (see :mod:`core.photoshop_script_bridge`).
  The script polls a command file and mirrors live colors back.

Both backends support the two colour slots: ``color_index`` 0 =
foreground (main), 1 = background (sub). Photoshop has no alpha channel,
so transparent colours are skipped by the sync layer (as before).

Multiple Photoshop versions can be installed; the settings UI offers one
entry per running instance ("auto" picks the first registered COM one).
"""

import ctypes
import os
import sys
import time
from typing import Any, Dict, Optional, cast

import psutil
import pythoncom

try:
    import win32com.client as _w32
except ImportError:
    _w32 = None

from core.photoshop_instances import (
    COM_KIND,
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

# Preferred ProgID — the version-independent "Photoshop.Application"
# key routes to whatever Photoshop build is actually installed and
# running.  The previously-preferred versioned "Photoshop.Application.140"
# (CC 2014) is now obsolete on this machine: its COM server is a
# leftover registry entry that fails with CO_E_SERVER_EXEC_FAILURE
# (0x80080005) and blocks ~30s on every Dispatch attempt, which made
# colour sync feel "dead" because each reconnect (after any transient
# COM RPC error) stalled on the broken ProgID first.
# We keep .140 as a fallback for any machine where the version-
# independent key is genuinely broken.
_PROGIDS = (
    "Photoshop.Application",
    "Photoshop.Application.140",
)

# HRESULTs that typically mean a UAC integrity-level mismatch:
# Photoshop runs elevated (as administrator) while Colorink does not,
# or vice versa. Cross-integrity COM calls are refused by the OS.
_PERMISSION_HRESULTS = {
    0x80070005,   # E_ACCESSDENIED
    0x8001011B,   # RPC_E_ACCESS_DENIED — the classic elevated-server refusal
    0x800706BA,   # RPC_S_SERVER_UNAVAILABLE
}


def _com_hresult(exc: Exception) -> int | None:
    """Extract the COM HRESULT from a pywintypes.com_error / pythoncom error."""
    hres = getattr(exc, "hresult", None)
    if isinstance(hres, int):
        return hres & 0xFFFFFFFF
    return None

DEBUG = False


def log(msg: str) -> None:
    if DEBUG:
        print(f"[PhotoshopSync] {msg}", file=sys.stderr, flush=True)


def _print_error(msg: str) -> None:
    print(f"[PhotoshopSync ERROR] {msg}", file=sys.stderr, flush=True)


def clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


# ---------------------------------------------------------------------------
# PhotoshopSync
# ---------------------------------------------------------------------------


class PhotoshopSync:
    """Colour bridge to Adobe Photoshop (COM or green-edition script bridge).

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
        self._app: Any = None       # CDispatch for Photoshop.Application
        self._disp: Any = None      # raw IDispatch pointer
        self._dispid_js: int = 0       # cached DISPID for DoJavaScript
        self._pid: int | None = None
        self._proc_handle: int = 0     # Win32 process handle for fast alive check
        self.current_version: str = "auto"  # selected instance label
        self.process_name: str = PROCESS_NAME
        self.backend: str = ""       # "" | "com" | "script-bridge"
        self._bridge: PhotoshopScriptBridge | None = None
        self._instances: list[PhotoshopInstance] = []
        self._detect_ts: float = 0.0
        # COM registration on this machine is flaky (registered at
        # startup, torn down again). After one failed COM attempt, skip
        # COM entirely until recheck() — a Dispatch can block for tens of
        # seconds, which must never stall the UI or sync loop again.
        self._com_failed = False
        # User-facing reason for the last failed connect / read / write.
        self.last_error: str = ""
        # True when the last failure looks like a UAC integrity-level
        # mismatch (e.g. Photoshop running as admin, Colorink not).
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
        """Connect to a running Photoshop instance (auto or user-selected).

        Every edition — genuine, green/portable, or cracked-with-COM —
        shares ONE sync path: the user-level CEP bridge, routed per
        instance by PID. COM automation is retired: it was a second
        backend with different latency and permission behaviour, and its
        poll-based reads kept a genuine Photoshop's engine busy with COM
        round-trips (the same starvation the old bridge panel caused).
        """
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

    def _connect_com(self, target: PhotoshopInstance) -> bool:
        """Attach to a registered, running Photoshop via COM automation.

        Retired backend — kept only for reference / potential rollback;
        ``connect()`` no longer calls it.
        """
        if _w32 is None:
            self.last_error = "pywin32 组件不可用（打包异常或未安装 pywin32）"
            return False
        progids = [target.progid] if target.progid else []
        progids += [p for p in _PROGIDS if p not in progids]
        for progid in progids:
            try:
                self._app = cast(Any, _w32).dynamic.Dispatch(progid)
                self._disp = self._app._oleobj_
                self._dispid_js = self._disp.GetIDsOfNames("DoJavaScript")
                # Close old handle and invalidate so _is_process_alive re-opens
                if self._proc_handle:
                    self.K32.CloseHandle(self._proc_handle)
                    self._proc_handle = 0
                log(f"Connected via ProgID='{progid}'  PID={self._pid}")
                self.backend = COM_KIND
                self.last_error = ""
                self.permission_issue = False
                return True
            except Exception as exc:
                hres = _com_hresult(exc)
                if hres in _PERMISSION_HRESULTS:
                    self.permission_issue = True
                self.last_error = f"COM 连接 {progid} 失败:{exc}"

        self._app = None
        self._disp = None
        _print_error("_connect_com: all ProgIDs failed")
        if self.permission_issue:
            self.last_error += (
                "（可能是权限不足:请让 Photoshop 与 Colorink 都"
                "以管理员身份运行）"
            )
        return False

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

    # -- colour I/O --------------------------------------------------------------

    def _invoke_js(self, script: str) -> object:
        """Execute *script* inside the Photoshop ExtendScript engine.

        Calls ``IDispatch::Invoke(DISPATCH_METHOD)`` directly to bypass
        a win32com bug where ``__getattr__`` tries to resolve DoJavaScript
        as a property-get, triggering a COM parameter-mismatch error.

        ``dynamic.Dispatch`` returns numeric COM variants as strings;
        we convert them back to float so callers can round to int.
        """
        result = self._disp.Invoke(
            self._dispid_js, 0, pythoncom.DISPATCH_METHOD, 1, script
        )
        if isinstance(result, str):
            try:
                return float(result)
            except ValueError:
                return result
        return result

    K32 = ctypes.windll.kernel32
    # 64 位安全：HANDLE 是指针宽度，必须显式声明 restype/argtypes，
    # 否则默认按 32 位 c_int 截断（OpenProcess 返回的句柄高 32 位非零时
    # 会被截断，WaitForSingleObject 拿坏句柄返回 WAIT_FAILED，把已退出的
    # Photoshop 误判为存活，进而触发挂死的 COM 调用）。
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

    # -- slot readers ---------------------------------------------------------------

    def get_color(self) -> dict[str, int] | None:
        """Read the current Photoshop foreground colour (slot 0)."""
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            state = self._bridge.read_state(self._pid)
            if state is not None:
                fg = state["fg"]
                return {"r": fg["r"], "g": fg["g"], "b": fg["b"], "index": 0}
            return None
        if self._app is None:
            if not self.connect():
                return None
        assert self._app is not None  # connect() sets _app on success

        if not self._is_process_alive():
            self._reset()
            return None

        try:
            rgb = self._app.ForegroundColor.RGB
            r = clamp8(round(float(rgb.Red)))
            g = clamp8(round(float(rgb.Green)))
            b = clamp8(round(float(rgb.Blue)))
            log(f"get_color: RGB=[{r}, {g}, {b}]")
            return {"r": r, "g": g, "b": b, "index": 0}
        except Exception as exc:
            _print_error(f"get_color: {exc}")
            if _com_hresult(exc) in _PERMISSION_HRESULTS:
                self.permission_issue = True
            self.last_error = f"读取 Photoshop 前景色失败：{exc}"
            self._reset()
            return None

    def get_bg_color(self) -> dict[str, int] | None:
        """Read the current Photoshop background colour (slot 1)."""
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            state = self._bridge.read_state(self._pid)
            if state is not None:
                bg = state["bg"]
                return {"r": bg["r"], "g": bg["g"], "b": bg["b"], "index": 1}
            return None
        if self._app is None:
            if not self.connect():
                return None
        assert self._app is not None  # connect() sets _app on success

        if not self._is_process_alive():
            self._reset()
            return None

        try:
            rgb = self._app.BackgroundColor.RGB
            r = clamp8(round(float(rgb.Red)))
            g = clamp8(round(float(rgb.Green)))
            b = clamp8(round(float(rgb.Blue)))
            log(f"get_bg_color: RGB=[{r}, {g}, {b}]")
            return {"r": r, "g": g, "b": b, "index": 1}
        except Exception as exc:
            _print_error(f"get_bg_color: {exc}")
            if _com_hresult(exc) in _PERMISSION_HRESULTS:
                self.permission_issue = True
            self.last_error = f"读取 Photoshop 背景色失败：{exc}"
            self._reset()
            return None

    # -- slot writers ---------------------------------------------------------------

    def swap_slots(self) -> bool:
        """Swap Photoshop's foreground/background (like pressing X)."""
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            return self._bridge.send_swap(str(time.time_ns()), self._pid)
        if self._app is None:
            if not self.connect():
                return False
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            return self._bridge.send_swap(str(time.time_ns()), self._pid)
        assert self._app is not None
        if not self._is_process_alive():
            self._reset()
            return False
        try:
            self._invoke_js(
                "var t=app.foregroundColor;"
                "app.foregroundColor=app.backgroundColor;"
                "app.backgroundColor=t;"
            )
            return True
        except Exception as exc:
            _print_error(f"swap_slots: {exc}")
            self.last_error = f"交换 Photoshop 前后景色失败：{exc}"
            self._reset()
            return False

    def set_color(self, r: int, g: int, b: int, color_index: int = 0) -> bool:
        """Write a colour to the foreground (0) or background (1) slot."""
        r, g, b = clamp8(r), clamp8(g), clamp8(b)

        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            return self._bridge.send_color(
                str(time.time_ns()), self._pid, color_index, r, g, b)

        if self._app is None:
            if not self.connect():
                return False
        # connect() may have switched to the script bridge (auto mode)
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            return self._bridge.send_color(
                str(time.time_ns()), self._pid, color_index, r, g, b)
        assert self._app is not None  # connect() sets _app on success

        if not self._is_process_alive():
            self._reset()
            return False

        try:
            cur = self.get_color() if color_index == 0 else self.get_bg_color()
            if cur and cur["r"] == r and cur["g"] == g and cur["b"] == b:
                return True  # no-op

            slot = (self._app.ForegroundColor if color_index == 0
                    else self._app.BackgroundColor)
            rgb = slot.RGB          # single dispatch — mutate in place
            rgb.Red = r
            rgb.Green = g
            rgb.Blue = b
            log(f"set_color: idx={color_index} RGB=[{r}, {g}, {b}]")
            return True
        except Exception as exc:
            _print_error(f"set_color: {exc}")
            if _com_hresult(exc) in _PERMISSION_HRESULTS:
                self.permission_issue = True
            self.last_error = f"写入 Photoshop 颜色失败：{exc}"
            self._reset()
            return False

    # -- status / meta -----------------------------------------------------------

    def status(self) -> dict[str, object]:
        if self.backend == SCRIPT_BRIDGE_KIND and self._bridge is not None:
            # Event-driven bridge: the heartbeat only refreshes while the
            # user is interacting with Photoshop, so "connected" must mean
            # "deployed + PS running" rather than a fresh heartbeat.
            connected = (self._bridge.is_deployed()
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
                # True when the deployed panel file is newer than the
                # panel actually running inside Photoshop (user must
                # restart PS for the new panel to load).
                "panelStale": bool(self._bridge is not None
                                   and self._bridge.panel_version(self._pid)
                                   != PANEL_VERSION),
                "lastError": self.last_error,
            }

        connected = (self._disp is not None
                     or (self._bridge is not None and self._bridge.is_alive(self._pid)))
        if not connected:
            self.connect()
            connected = (self._disp is not None
                         or (self._bridge is not None and self._bridge.is_alive(self._pid)))

        return {
            "connected": connected,
            "pid": self._pid if connected else None,
            "version": self.current_version,
            "processName": self.process_name,
            "backend": self.backend,
            "bridgeAlive": bool(self._bridge is not None
                                and self._bridge.is_alive(self._pid)),
            "lastError": self.last_error,
        }

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
        Also clears the COM-failed flag so a working COM registration
        gets another chance.
        """
        self._com_failed = False
        self._detect(force=True)
        return self.connect()

    def status_lite(self) -> dict[str, object]:
        """Snapshot of the current connection state WITHOUT connecting.

        Safe to call from the UI thread: never blocks on COM, detection
        or deployment. The sync loop keeps the real state fresh via
        :meth:`status`.
        """
        bridge_ok = (self._bridge is not None
                     and self._bridge.is_deployed()
                     and self._is_process_alive())
        return {
            "connected": bridge_ok or self._disp is not None,
            "pid": self._pid if bridge_ok or self._disp is not None else None,
            "version": self.current_version,
            "processName": self.process_name,
            "backend": self.backend,
            "bridgeAlive": bool(self._bridge is not None
                                and self._bridge.is_alive(self._pid)),
            # COM backend has no panel; only meaningful for script-bridge.
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
        self._app = None
        self._disp = None
        self._dispid_js = 0
        self._pid = None
        self._bridge = None
        self.backend = ""

    @staticmethod
    def _find_process() -> int | None:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == PROCESS_NAME:
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None
