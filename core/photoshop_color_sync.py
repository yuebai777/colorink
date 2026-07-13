#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Photoshop colour sync via COM automation (DoJavaScript).

Uses the Photoshop COM automation interface to execute ExtendScript
snippets that read / write the foreground colour.  No memory scanning,
no temp-file bridge, no persistent PS script — Photoshop stays fully
responsive because DoJavaScript calls are synchronous COM round-trips
that complete in microseconds and never block the UI thread.

Matches the CSPSync / UDMSync interface for drop-in compatibility
with MemorySyncThread.
"""

import sys
import os
from typing import Dict, Optional

import ctypes
import pythoncom
import psutil

try:
    import win32com.client as _w32
except ImportError:
    _w32 = None

# ---- constants -----------------------------------------------------------

PROCESS_NAME = "Photoshop.exe"

# Preferred ProgID — versioned so it bypasses the broken
# version-independent Photoshop.Application key on this machine.
# Falls back to the generic ProgID if .140 isn't found.
_PROGIDS = (
    "Photoshop.Application.140",
    "Photoshop.Application",
)

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
        self._app: object = None       # CDispatch for Photoshop.Application
        self._disp: object = None      # raw IDispatch pointer
        self._dispid_js: int = 0       # cached DISPID for DoJavaScript
        self._pid: Optional[int] = None
        self._proc_handle: int = 0     # Win32 process handle for fast alive check
        self.current_version: str = "auto"
        self.process_name: str = PROCESS_NAME

    # -- connect -----------------------------------------------------------------

    def connect(self) -> bool:
        """Acquire a COM reference to a running Photoshop instance."""

        # Re-use existing connection if healthy
        if self._app is not None and self._disp is not None:
            # Bail early if Photoshop died — avoids hung COM RPC
            if not self._is_process_alive():
                self._reset()
                return False
            try:
                name = self._app.Name
                if name:
                    return True
            except Exception:
                self._reset()

        if _w32 is None:
            _print_error("connect: win32com / pywin32 not available")
            return False

        # NEVER auto-launch Photoshop via COM Dispatch.
        # win32com.dynamic.Dispatch("Photoshop.Application") will start
        # Photoshop if it's not running — which is NOT what we want.
        # Check first whether the process exists at all.
        if not self._find_process():
            return False

        # Try each ProgID in order
        for progid in _PROGIDS:
            try:
                self._app = _w32.dynamic.Dispatch(progid)
                self._disp = self._app._oleobj_
                self._dispid_js = self._disp.GetIDsOfNames("DoJavaScript")
                self._pid = self._find_process()
                # Close old handle and invalidate so _is_process_alive re-opens
                if self._proc_handle:
                    self.K32.CloseHandle(self._proc_handle)
                    self._proc_handle = 0
                log(f"Connected via ProgID='{progid}'  PID={self._pid}")
                return True
            except Exception:
                continue

        _print_error("connect: all ProgIDs failed — is Photoshop running?")
        self._reset()
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

    def get_color(self) -> Optional[Dict[str, int]]:
        """Read the current Photoshop foreground colour via COM properties.

        COM property reads do NOT invoke the ExtendScript engine, so they
        never trigger Photoshop's busy cursor — safe for 10 Hz polling.
        """
        if self._app is None and not self.connect():
            return None

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
            self._reset()
            return None

    def set_color(self, r: int, g: int, b: int) -> bool:
        """Write foreground colour via COM property mutation.

        With ``dynamic.Dispatch`` (late binding) the RGB object reference
        is preserved across channel assignments, so in-place mutation
        works reliably — no ExtendScript needed, no busy cursor.
        """
        if self._app is None and not self.connect():
            return False

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
            self._reset()
            return False

    # -- status / meta -----------------------------------------------------------

    def status(self) -> Dict[str, object]:
        connected = self._disp is not None
        if not connected:
            self.connect()
            connected = self._disp is not None

        return {
            "connected": connected,
            "pid": self._pid if connected else None,
            "version": self.current_version,
            "processName": self.process_name,
        }

    def set_version(self, version: str) -> bool:
        version = str(version or "auto").strip()
        if version == self.current_version:
            return False
        self.current_version = version
        self._reset()
        log(f"Version changed to {version}")
        return True

    def dump(self) -> Dict[str, object]:
        color = self.get_color()
        if color is None:
            return {"error": "not connected"}
        return {
            "pid": self._pid,
            "version": self.current_version,
            "processName": self.process_name,
            "color": color,
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
    def _find_process() -> Optional[int]:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == PROCESS_NAME:
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None
