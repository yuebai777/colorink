#!/usr/bin/env python3

"""SAI2 color synchronization via direct process memory access.

Reads and writes the active brush color of PaintTool SAI2 by attaching to
the running process. Pattern-based signature scanning resolves the color
slot address across SAI2 builds, with a fixed fallback offset for older
known binaries. The connection is cached and lazily re-established when
the cached handle stops being readable.
"""

import ctypes
import os
import struct
import sys
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

from core import sai2_ui_refresh

# Windows API constants for process memory access
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10

# Build-specific instruction signatures used to locate the color slot.
# The signature is the instruction that writes the active brush color
# into a globally-preserved slot. The matching address resolves a
# [rip+disp32] operand to recover the actual color address.
#
# pre-2024:   B9 03 00 00 00 88 05 ?? ?? ?? ??
# after-2024: E8 ?? ?? ?? ?? B9 01 00 00 00 88 05 ?? ?? ?? ??
DEFAULT_VERSION = "pre-2024-sai2"

SIGNATURES: dict[str, dict[str, Any]] = {
    "pre-2024-sai2": {
        "pattern": [0xB9, 0x03, 0x00, 0x00, 0x00, 0x88, 0x05, None, None, None, None],
        "disp_offset": 7,        # first byte of the [rip+disp32] operand
        "next_rip_offset": 11,   # RIP position after the matched instruction
    },
    "after-2024-sai2": {
        "pattern": [0xE8, None, None, None, None, 0xB9, 0x01, 0x00, 0x00, 0x00, 0x88, 0x05, None, None, None, None],
        "disp_offset": 12,
        "next_rip_offset": 16,
    },
}

# Fallback fixed offsets for known builds, used only when pattern scan fails
KNOWN_OFFSETS: dict[str, int] = {
    "default": 0x303DC0,  # 2021.5.28 build
}

DEBUG = False


def _log(msg: str) -> None:
    if DEBUG:
        print(f"[SAI2Sync] {msg}", file=sys.stderr, flush=True)


def _normalize_version(version: str | None) -> str:
    s = str(version or "").strip().lower()
    if s in (
        "after-2024-sai2",
        "after2024sai2",
        "after-2024",
        "after2024",
        "sfter2024sai2",
    ):
        return "after-2024-sai2"
    return "pre-2024-sai2"


def _is_auto_version(version: object) -> bool:
    """True when the caller asked for automatic signature detection."""
    return str(version or "").strip().lower() in ("", "auto")


def _clamp8(value: int) -> int:
    return max(0, min(255, int(value)))


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260),
    ]


_kernel32 = ctypes.windll.kernel32

# ── 64 位安全：显式声明参数/返回类型 ─────────────────────────────
# 不设 restype 时 OpenProcess/CreateToolhelp32Snapshot 的 HANDLE 按
# 32 位 c_int 截断返回，高句柄值下会得到错误句柄或误判失败。
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
_kernel32.Process32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32))
_kernel32.Process32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32))
_kernel32.Module32First.argtypes = (ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32))
_kernel32.Module32Next.argtypes = (ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32))
_kernel32.ReadProcessMemory.argtypes = (
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
)
_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_kernel32.WriteProcessMemory.argtypes = (
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
)
_kernel32.WriteProcessMemory.restype = wintypes.BOOL


def _find_process(name: str) -> int | None:
    """Locate a process id by image name."""
    name_lower = name.lower().encode("utf-8")
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == 0xFFFFFFFF:
        return None

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    pid: int | None = None
    if _kernel32.Process32First(snapshot, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == name_lower:
                pid = pe.th32ProcessID
                break
            if not _kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break

    _kernel32.CloseHandle(snapshot)
    return pid


def _get_module_info(pid: int, module_name: str) -> tuple[int | None, int | None]:
    """Return (base_addr, module_size) for the named module inside the process."""
    name_lower = module_name.lower().encode("utf-8")
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if not snapshot or snapshot == 0xFFFFFFFF:
        return None, None

    me = MODULEENTRY32()
    me.dwSize = ctypes.sizeof(MODULEENTRY32)

    base_addr: int | None = None
    mod_size: int | None = None
    if _kernel32.Module32First(snapshot, ctypes.byref(me)):
        while True:
            if me.szModule.lower() == name_lower:
                base_addr = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value
                mod_size = me.modBaseSize
                break
            if not _kernel32.Module32Next(snapshot, ctypes.byref(me)):
                break

    _kernel32.CloseHandle(snapshot)
    return base_addr, mod_size


def _read_memory(handle, address: int, size: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t()
    if _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read)):
        return buffer.raw[: bytes_read.value]
    return None


def _write_memory(handle, address: int, data) -> bool:
    buffer = ctypes.create_string_buffer(bytes(data))
    bytes_written = ctypes.c_size_t()
    result = _kernel32.WriteProcessMemory(handle, ctypes.c_void_p(address), buffer, len(data), ctypes.byref(bytes_written))
    # 必须校验实际写入字节数：部分写入（跨页/权限边缘）会返回成功但
    # 把 B/G/R 通道写错位，读回的是坏颜色。
    return result != 0 and bytes_written.value == len(data)


def _scan_pattern_masked(handle, base: int, size: int, pattern: list[int | None]) -> int | None:
    """Scan a memory region for a byte pattern that allows wildcard bytes (None)."""
    CHUNK_SIZE = 0x100000  # 1MB
    pattern_len = len(pattern)
    if pattern_len <= 0:
        return None

    fixed_indices = [i for i, b in enumerate(pattern) if b is not None]
    if not fixed_indices:
        return None
    anchor_idx = fixed_indices[0]
    anchor_byte = pattern[anchor_idx]
    assert anchor_byte is not None  # anchor position is a fixed byte in every signature
    step = max(1, CHUNK_SIZE - pattern_len)

    for offset in range(0, size, step):
        read_size = min(CHUNK_SIZE, size - offset)
        if read_size < pattern_len:
            continue
        data = _read_memory(handle, base + offset, read_size)
        if not data:
            continue

        start = 0
        while True:
            pos = data.find(bytes([anchor_byte]), start)
            if pos == -1:
                break

            cand = pos - anchor_idx
            if cand < 0 or cand + pattern_len > len(data):
                start = pos + 1
                continue

            matched = True
            for i in fixed_indices:
                if data[cand + i] != pattern[i]:
                    matched = False
                    break
            if matched:
                return base + offset + cand

            start = pos + 1

    return None


class SAI2Sync:
    """Memory-based active brush color sync with PaintTool SAI2.

    Maintains an open process handle and the resolved color slot address,
    refreshing both lazily once the cached state stops being readable.
    """

    PROCESS_NAME = "sai2.exe"

    def __init__(self, version: str | None = None) -> None:
        raw_version = version if version is not None else os.environ.get("SAI2_SYNC_VERSION")
        # "auto" (the default) means: try every known signature and keep the
        # first one that resolves to a readable slot. An explicit version pins
        # a single signature.
        self._auto_version: bool = _is_auto_version(raw_version)
        self.version: str = _normalize_version(raw_version or DEFAULT_VERSION)
        self.process_name: str = self.PROCESS_NAME
        self._handle = None
        self._pid: int | None = None
        self._color_addr: int | None = None
        self._base: int | None = None
        self._size: int | None = None
        # Diagnostics: why the last connection attempt failed and how the
        # colour slot was found (signature name or offset fallback).
        self.last_error: str = ""
        self.resolve_method: str | None = None
        # Writing the slot changes what SAI paints with, but SAI never learns
        # its own widgets went stale — the refresher nudges them. There is a
        # single refresh mode (full); no env escape hatch, so a stale
        # environment cannot silently disable the UI refresh.
        self.ui_refresher = sai2_ui_refresh.SAIUiRefresher(
            mode=sai2_ui_refresh.DEFAULT_MODE,
        )
        # Picker-field mirror write (core.sai2_ui_sync): SAI's own colour
        # panel (wheel, marker, slider thumbs) is driven by a separate picker
        # state, so writing only the colour slot leaves it stale. "auto"
        # resolves the panel mode from SAI's own state; the synchroniser is
        # created lazily and every failure is swallowed.
        self.panel_mode: str = "auto"
        self._ui_picker = None

    def set_ui_refresh(self, mode: object) -> bool:
        """Configure how aggressively SAI's own widgets get refreshed."""
        return self.ui_refresher.set_mode(mode)

    def ui_picker(self):
        """Lazily create the picker-field synchroniser (None if unavailable).

        The synchroniser makes SAI's colour panel follow the colour that was
        just written to the slot (see :mod:`core.sai2_ui_sync`). It is
        optional: if the module or the panel controls are unavailable this
        returns ``None`` and colour syncing behaves exactly as before.
        """
        if self._ui_picker is None:
            try:
                from core import sai2_ui_sync  # noqa: PLC0415 - avoid import cycle
                self._ui_picker = sai2_ui_sync.SAI2UiSync(self.panel_mode)
            except Exception:  # noqa: BLE001 - optional feature
                self._ui_picker = False
        return self._ui_picker or None

    def set_panel_mode(self, mode: object) -> bool:
        """Set the SAI colour-panel mode targeted by the picker-field sync.

        Accepts ``auto`` (detect from SAI), ``vhsv``, ``hsv`` or ``hsl``
        (``hls`` is accepted as colorink's spelling). Returns True when the
        value changed.
        """
        normalized = str(mode or "auto").strip().lower() or "auto"
        changed = normalized != self.panel_mode
        self.panel_mode = normalized
        picker = self.ui_picker()
        if picker is not None:
            picker.set_mode(normalized)
        return changed

    def tick_ui_refresh(self) -> bool:
        """Deliver a refresh that was throttled or deferred (poll-loop hook)."""
        if self._pid is None:
            return False
        return self.ui_refresher.tick(self._pid)

    def note_colour(self, rgb) -> None:
        """Feed a polled slot colour to the refresher as discovery evidence."""
        refresher = getattr(self, "ui_refresher", None)
        if refresher is not None:
            try:
                refresher.note_colour(rgb)
            except Exception:  # noqa: BLE001 - observation must never break the poll
                pass

    def on_external_colour(self, rgb) -> None:
        """SAI changed its own colour (not the echo of our write).

        SAI re-renders its stroke-preview cache in that colour, so the
        refresher records it and retries the preview discovery immediately.
        """
        refresher = getattr(self, "ui_refresher", None)
        if refresher is not None:
            try:
                refresher.on_external_colour(rgb)
            except Exception:  # noqa: BLE001 - see note_colour
                pass

    def set_version(self, version: str) -> bool:
        """Switch SAI2 signature mode. Returns True if the version changed.

        ``auto`` (or an empty value) re-enables automatic signature detection:
        every known signature is tried and the first readable hit is used.
        """
        auto = _is_auto_version(version)
        normalized = _normalize_version(version)
        changed = normalized != self.version or auto != self._auto_version
        self.version = normalized
        self._auto_version = auto
        if changed:
            self._reset_cache(close_handle=True)
        _log(f"Using signature mode: {'auto' if auto else normalized}")
        return changed

    def _reset_cache(self, close_handle: bool = True) -> None:
        if close_handle and self._handle:
            try:
                _kernel32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        self._pid = None
        self._color_addr = None
        self._base = None
        self._size = None
        # SAI restarting (or a signature switch) invalidates the resolved
        # control handles as well, so discovery has to run again.
        refresher = getattr(self, "ui_refresher", None)
        if refresher is not None:
            refresher.reset()
        # SAI restarted (or a signature switch): cached panel controls and the
        # detected panel mode are stale as well.
        picker = self._ui_picker
        if picker:
            try:
                picker.reset()
            except Exception:
                pass

    def _connect(self) -> bool:
        """Attach to the running SAI2 process and locate the color slot.

        Every failure records a machine-readable reason in :attr:`last_error`
        (surfaced through :meth:`status`), so the diagnostics panel can tell
        "SAI is not running" apart from "running as a different user" or
        "signature not found in this build".
        """
        if self._handle and self._color_addr:
            try:
                color = _read_memory(self._handle, self._color_addr, 3)
                if color and len(color) == 3:
                    self.last_error = ""
                    return True
            except Exception:
                pass
            self._reset_cache(close_handle=True)

        pid = _find_process(self.PROCESS_NAME)
        if not pid:
            self.last_error = "not_running"
            return False

        handle = _kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        if not handle:
            err = ctypes.get_last_error()
            self.last_error = "access_denied" if err == 5 else f"open_failed:{err}"
            return False

        base, size = _get_module_info(pid, self.PROCESS_NAME)
        if not base or size is None:
            _kernel32.CloseHandle(handle)
            self.last_error = "module_info_failed"
            return False

        resolved = self._resolve_color_address(handle, base, size)
        if resolved is None:
            _kernel32.CloseHandle(handle)
            self.last_error = "no_signature"
            return False
        color_addr, how = resolved

        self._handle = handle
        self._pid = pid
        self._color_addr = color_addr
        self._base = base
        self._size = size
        self.resolve_method = how
        self.last_error = ""

        _log(f"Connected to SAI2 (PID: {pid}, Version: {self.version}, "
             f"Color: 0x{color_addr:X}, via {how})")
        return True

    def _resolve_color_address(self, handle, base: int, size: int):
        """Find the colour slot address; returns ``(address, method)`` or None.

        In ``auto`` mode every known signature is tried in turn and the first
        one that resolves to a *readable* slot wins; an explicitly configured
        version tries only that signature. Known fixed offsets are used as a
        last resort, again with a readability check, so a stale offset entry
        can never beat a live signature hit.
        """
        versions = list(SIGNATURES) if self._auto_version else [self.version]
        seen: set[int] = set()
        for version in versions:
            signature = SIGNATURES.get(version)
            if not signature:
                continue
            _log(f"Scanning with signature mode: {version}")
            pattern_addr = _scan_pattern_masked(handle, base, size, signature["pattern"])
            if pattern_addr is None:
                _log(f"Pattern scan failed for {version}")
                continue
            rel_addr_data = _read_memory(handle, pattern_addr + signature["disp_offset"], 4)
            if not rel_addr_data or len(rel_addr_data) != 4:
                continue
            rel_addr = struct.unpack("<i", rel_addr_data)[0]
            color_addr = pattern_addr + signature["next_rip_offset"] + rel_addr
            if color_addr in seen:
                continue
            seen.add(color_addr)
            _log(f"Pattern matched at 0x{pattern_addr:X}, candidate 0x{color_addr:X}")
            if _read_memory(handle, color_addr, 3):
                return color_addr, f"signature:{version}"

        for name, offset in KNOWN_OFFSETS.items():
            color_addr = base + offset
            if color_addr in seen:
                continue
            seen.add(color_addr)
            if _read_memory(handle, color_addr, 3):
                _log(f"Using fixed offset {name} -> 0x{color_addr:X}")
                return color_addr, f"offset:{name}"
        return None

    def get_color(self) -> dict[str, int] | None:
        if not self._handle or not self._color_addr:
            if not self._connect():
                return None
        assert self._handle is not None and self._color_addr is not None

        try:
            data = _read_memory(self._handle, self._color_addr, 3)
            if data and len(data) == 3:
                # SAI stores channels in memory order B, G, R
                return {"r": data[2], "g": data[1], "b": data[0]}
            # 进程退出/读失败：关闭句柄并清缓存，让下次访问重新 connect。
            # 否则 _handle 残留非 None，重连入口永远不触发（SAI2 重启后
            # 一直显示未连接），且每次失败都泄漏一个进程句柄。
            self._reset_cache(close_handle=True)
        except Exception:
            self._reset_cache(close_handle=True)

        return None

    def set_color(self, r: int, g: int, b: int) -> bool:
        if not self._handle or not self._color_addr:
            if not self._connect():
                return False
        assert self._handle is not None and self._color_addr is not None

        r = _clamp8(r)
        g = _clamp8(g)
        b = _clamp8(b)

        # SAI's pre-write colour identifies its stroke-preview control: that
        # control caches a sample stroke drawn in the colour SAI last rendered
        # it with. Reading 3 bytes is negligible, and only needed until the
        # refresher has confirmed its target.
        previous: tuple[int, int, int] | None = None
        if self.ui_refresher.wants_previous_color():
            current = self.get_color()
            if current is not None:
                previous = (current["r"], current["g"], current["b"])
            elif not self._connect():
                # The read failed, which drops the cached handle — the write
                # must not go ahead against a stale one.
                return False

        handle, address = self._handle, self._color_addr
        if not handle or not address:
            return False

        # Panel-mode detection must run while SAI's own colour is still in the
        # slot: after our write the picker fields no longer match the new
        # colour, so detection would always fail. Observation is cheap (two
        # small reads) and swallows its own errors.
        picker = self.ui_picker()
        if picker is not None:
            try:
                picker.observe(self)
            except Exception:
                pass

        try:
            ok = _write_memory(handle, address, [b, g, r])
        except Exception:
            self._reset_cache(close_handle=True)
            return False

        if ok:
            # Read-back check: a partial write (page boundary) or SAI itself
            # overwriting the slot (user dragging its picker) would leave the
            # wrong colour in memory. One retry, then report the failure —
            # the caller keeps painting with whatever SAI actually has.
            if _read_memory(handle, address, 3) != bytes((b, g, r)):
                _log("write read-back mismatch, retrying once")
                try:
                    ok = _write_memory(handle, address, [b, g, r])
                except Exception:
                    ok = False
                if ok and _read_memory(handle, address, 3) != bytes((b, g, r)):
                    self.last_error = "write_verify_failed"
                    _log("write read-back still mismatched")
                    ok = False

        if ok and self._pid:
            # Best-effort UI sync: SAI's brush swatch and stroke preview keep
            # showing the previous colour until they are nudged. The refresher
            # already guards itself, but the colour write has *succeeded* by
            # now — nothing beyond this point may change that outcome.
            try:
                self.ui_refresher.refresh(self._pid, (r, g, b), previous=previous)
            except Exception:
                pass
            # Picker-field mirror write: SAI's colour panel (wheel, marker,
            # slider thumbs) is driven by a separate picker state, so the slot
            # write above leaves it stale. Failures are swallowed — the slot
            # write is the authoritative brush colour either way.
            try:
                picker = self.ui_picker()
                if picker is not None:
                    picker.sync(self, (r, g, b))
            except Exception:
                pass
        return ok

    def status(self) -> dict[str, object]:
        if not self._handle or not self._color_addr:
            self._connect()

        connected = self._handle is not None and self._color_addr is not None
        if connected:
            if self.get_color() is None:
                connected = False

        return {
            "connected": connected,
            "pid": self._pid if connected else None,
            "colorAddr": f"0x{self._color_addr:X}" if connected and self._color_addr else None,
            "version": self.version,
            # Machine-readable outcome for the diagnostics panel:
            # "ok" | not_running | access_denied | open_failed:N |
            # module_info_failed | no_signature | write_verify_failed
            "reason": "ok" if connected else (self.last_error or "unknown"),
            "resolvedBy": self.resolve_method if connected else None,
        }