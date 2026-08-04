#!/usr/bin/env python3
"""Photoshop colour sync via COM automation (DoJavaScript).

Uses the Photoshop COM automation interface to execute ExtendScript
snippets that read / write the foreground colour.  No memory scanning,
no temp-file bridge, no persistent PS script — Photoshop stays fully
responsive because DoJavaScript calls are synchronous COM round-trips
that complete in microseconds and never block the UI thread.

Matches the CSPSync / UDMSync interface for drop-in compatibility
with MemorySyncThread.
"""

import ctypes
import os
import sys
from typing import Any, Dict, Optional, cast

import psutil
import pythoncom

try:
    import win32com.client as _w32
except ImportError:
    _w32 = None

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
    """Colour bridge to Adobe Photoshop through COM + ExtendScript.

    Usage::

        ps = PhotoshopSync()
        ps.connect()
        rgb = ps.get_color()          # -> {'r': 128, 'g': 64, 'b': 32}
        ps.set_color(255, 0, 0)       # -> True
        print(ps.status())            # -> {connected, pid, ...}
    """

    def __init__(self) -> None:
        self._app: Any = None       # CDispatch for Photoshop.Application
        self._disp: Any = None      # raw IDispatch pointer
        self._dispid_js: int = 0       # cached DISPID for DoJavaScript
        self._pid: int | None = None
        self._proc_handle: int = 0     # Win32 process handle for fast alive check
        self.current_version: str = "auto"
        self.process_name: str = PROCESS_NAME
        # User-facing reason for the last failed connect / read / write.
        # Empty string means no error. Exposed via status()["lastError"] so
        # the UI can tell the user WHY Photoshop sync is not connected
        # (PS not running / COM not registered / permission mismatch...).
        self.last_error: str = ""
        # True when the last failure looks like a UAC integrity-level
        # mismatch (e.g. Photoshop running as admin, Colorink not).
        # The UI uses this to offer a one-click "relaunch as admin".
        self.permission_issue: bool = False

    # -- connect -----------------------------------------------------------------

    def connect(self) -> bool:
        """Acquire a COM reference to a running Photoshop instance."""

        # Re-use existing connection if healthy
        if self._app is not None and self._disp is not None:
            # Bail early if Photoshop died — avoids hung COM RPC
            if not self._is_process_alive():
                self.last_error = "Photoshop 进程已退出，请重新启动 Photoshop 后再试"
                self._reset()
                return False
            try:
                name = self._app.Name
                if name:
                    return True
            except Exception:
                self._reset()

        if _w32 is None:
            self.last_error = "pywin32 组件不可用（打包异常或未安装 pywin32）"
            _print_error("connect: win32com / pywin32 not available")
            return False

        # NEVER auto-launch Photoshop via COM Dispatch.
        # win32com.dynamic.Dispatch("Photoshop.Application") will start
        # Photoshop if it's not running — which is NOT what we want.
        # Check first whether the process exists at all.
        if not self._find_process():
            self.last_error = "未检测到 Photoshop 进程，请先启动 Photoshop"
            return False

        # Try each ProgID in order
        for progid in _PROGIDS:
            try:
                self._app = cast(Any, _w32).dynamic.Dispatch(progid)
                self._disp = self._app._oleobj_
                self._dispid_js = self._disp.GetIDsOfNames("DoJavaScript")
                self._pid = self._find_process()
                # Close old handle and invalidate so _is_process_alive re-opens
                if self._proc_handle:
                    self.K32.CloseHandle(self._proc_handle)
                    self._proc_handle = 0
                log(f"Connected via ProgID='{progid}'  PID={self._pid}")
                self.last_error = ""
                self.permission_issue = False
                return True
            except Exception as exc:
                hres = _com_hresult(exc)
                if hres in _PERMISSION_HRESULTS:
                    self.permission_issue = True
                self.last_error = f"COM 连接 {progid} 失败:{exc}"

        _print_error("connect: all ProgIDs failed — is Photoshop running?")
        self._reset()
        if self.permission_issue:
            self.last_error += (
                "（可能是权限不足:请让 Photoshop 与 Colorink 都"
                "以管理员身份运行）"
            )
        elif self.last_error:
            self.last_error += "（可能为绿色版 / 未正常安装，COM 接口未注册）"
        else:
            self.last_error = "Photoshop COM 接口不可用（可能为绿色版 / 未正常安装）"
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

    def _is_process_alive(self) -> bool:
        """Check whether the cached Photoshop process is still running.

        Uses WaitForSingleObject (0ms timeout) on the process handle —
        returns instantly, unlike psutil which creates Python objects.
        This shrinks the TOCTOU window between the check and the COM call
        to microseconds instead of milliseconds.
        """
        if self._pid is None:
            return False
        if not self._proc_handle:
            # SYNCHRONIZE access — just enough to wait on the handle
            self._proc_handle = self.K32.OpenProcess(0x00100000, False, self._pid)
            if not self._proc_handle:
                return False
        # WAIT_OBJECT_0 (0) = process exited; anything else = still alive
        return self.K32.WaitForSingleObject(self._proc_handle, 0) != 0

    def get_color(self) -> dict[str, int] | None:
        """Read the current Photoshop foreground colour via COM properties.

        COM property reads do NOT invoke the ExtendScript engine, so they
        never trigger Photoshop's busy cursor — safe for 10 Hz polling.
        """
        if self._app is None:
            if not self.connect():
                return None
        assert self._app is not None  # connect() sets _app on success

        # Bail early if Photoshop has died — avoids hung COM RPC call
        if not self._is_process_alive():
            self._reset()
            return None

        try:
            rgb = self._app.ForegroundColor.RGB
            r = int(round(float(rgb.Red)))
            g = int(round(float(rgb.Green)))
            b = int(round(float(rgb.Blue)))
            r, g, b = clamp8(r), clamp8(g), clamp8(b)
            log(f"get_color: RGB=[{r}, {g}, {b}]")
            return {"r": r, "g": g, "b": b}
        except Exception as exc:
            _print_error(f"get_color: {exc}")
            if _com_hresult(exc) in _PERMISSION_HRESULTS:
                self.permission_issue = True
            self.last_error = f"读取 Photoshop 前景色失败：{exc}"
            self._reset()
            return None

    def set_color(self, r: int, g: int, b: int) -> bool:
        """Write foreground colour via COM property mutation.

        With ``dynamic.Dispatch`` (late binding) the RGB object reference
        is preserved across channel assignments, so in-place mutation
        works reliably — no ExtendScript needed, no busy cursor.
        """
        if self._app is None:
            if not self.connect():
                return False
        assert self._app is not None  # connect() sets _app on success

        # Bail early if Photoshop died since connect
        if not self._is_process_alive():
            self._reset()
            return False

        r = clamp8(r)
        g = clamp8(g)
        b = clamp8(b)

        try:
            cur = self.get_color()
            if cur and cur["r"] == r and cur["g"] == g and cur["b"] == b:
                return True  # no-op

            fg = self._app.ForegroundColor
            rgb = fg.RGB          # single dispatch — mutate in place
            rgb.Red = r
            rgb.Green = g
            rgb.Blue = b
            log(f"set_color: RGB=[{r}, {g}, {b}]")
            return True
        except Exception as exc:
            _print_error(f"set_color: {exc}")
            if _com_hresult(exc) in _PERMISSION_HRESULTS:
                self.permission_issue = True
            self.last_error = f"写入 Photoshop 前景色失败：{exc}"
            self._reset()
            return False

    # -- status / meta -----------------------------------------------------------

    def status(self) -> dict[str, object]:
        connected = self._disp is not None
        if not connected:
            self.connect()
            connected = self._disp is not None

        return {
            "connected": connected,
            "pid": self._pid if connected else None,
            "version": self.current_version,
            "processName": self.process_name,
            "lastError": self.last_error,
        }

    def set_version(self, version: str) -> bool:
        version = str(version or "auto").strip()
        if version == self.current_version:
            return False
        self.current_version = version
        self._reset()
        log(f"Version changed to {version}")
        return True

    def dump(self) -> dict[str, object]:
        color = self.get_color()
        if color is None:
            return {"error": "not connected"}
        return {
            "pid": self._pid,
            "version": self.current_version,
            "processName": self.process_name,
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

    @staticmethod
    def _find_process() -> int | None:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == PROCESS_NAME:
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None
