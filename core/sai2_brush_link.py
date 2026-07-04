#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""SAI2 color synchronization via direct process memory access.

Reads and writes the active brush color of PaintTool SAI2 by attaching to
the running process. Pattern-based signature scanning resolves the color
slot address across SAI2 builds, with a fixed fallback offset for older
known binaries. The connection is cached and lazily re-established when
the cached handle stops being readable.
"""

import ctypes
from ctypes import wintypes
import os
import sys
import struct
from typing import Dict, List, Optional, Tuple

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

SIGNATURES: Dict[str, Dict[str, object]] = {
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
KNOWN_OFFSETS: Dict[str, int] = {
    "default": 0x303DC0,  # 2021.5.28 build
}

DEBUG = False


def _log(msg: str) -> None:
    if DEBUG:
        print(f"[SAI2Sync] {msg}", file=sys.stderr, flush=True)


def _normalize_version(version: Optional[str]) -> str:
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


def _find_process(name: str) -> Optional[int]:
    """Locate a process id by image name."""
    name_lower = name.lower().encode("utf-8")
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    pid: Optional[int] = None
    if _kernel32.Process32First(snapshot, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == name_lower:
                pid = pe.th32ProcessID
                break
            if not _kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break

    _kernel32.CloseHandle(snapshot)
    return pid


def _get_module_info(pid: int, module_name: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (base_addr, module_size) for the named module inside the process."""
    name_lower = module_name.lower().encode("utf-8")
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snapshot == -1 or snapshot == 0xFFFFFFFF:
        return None, None

    me = MODULEENTRY32()
    me.dwSize = ctypes.sizeof(MODULEENTRY32)

    base_addr: Optional[int] = None
    mod_size: Optional[int] = None
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


def _read_memory(handle, address: int, size: int) -> Optional[bytes]:
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t()
    if _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(bytes_read)):
        return buffer.raw[: bytes_read.value]
    return None


def _write_memory(handle, address: int, data) -> bool:
    buffer = ctypes.create_string_buffer(bytes(data))
    bytes_written = ctypes.c_size_t()
    result = _kernel32.WriteProcessMemory(handle, ctypes.c_void_p(address), buffer, len(data), ctypes.byref(bytes_written))
    return result != 0


def _scan_pattern_masked(handle, base: int, size: int, pattern: List[Optional[int]]) -> Optional[int]:
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

    def __init__(self, version: Optional[str] = None) -> None:
        self.version: str = _normalize_version(version or os.environ.get("SAI2_SYNC_VERSION", DEFAULT_VERSION))
        self._handle = None
        self._pid: Optional[int] = None
        self._color_addr: Optional[int] = None
        self._base: Optional[int] = None
        self._size: Optional[int] = None

    def set_version(self, version: str) -> bool:
        """Switch SAI2 signature mode. Returns True if the version changed."""
        normalized = _normalize_version(version)
        changed = normalized != self.version
        self.version = normalized
        if changed:
            self._reset_cache(close_handle=True)
        _log(f"Using signature mode: {normalized}")
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

    def _connect(self) -> bool:
        """Attach to the running SAI2 process and locate the color slot."""
        if self._handle and self._color_addr:
            try:
                color = _read_memory(self._handle, self._color_addr, 3)
                if color and len(color) == 3:
                    return True
            except Exception:
                pass
            self._reset_cache(close_handle=True)

        pid = _find_process(self.PROCESS_NAME)
        if not pid:
            return False

        handle = _kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        if not handle:
            return False

        base, size = _get_module_info(pid, self.PROCESS_NAME)
        if not base:
            _kernel32.CloseHandle(handle)
            return False

        signature = SIGNATURES.get(self.version, SIGNATURES[DEFAULT_VERSION])
        _log(f"Scanning with signature mode: {self.version}")

        # Resolve color address via instruction signature scan
        pattern_addr = _scan_pattern_masked(handle, base, size, signature["pattern"])
        if pattern_addr is None:
            _log(f"Pattern scan failed for {self.version}, using default offset")
            color_addr = base + KNOWN_OFFSETS["default"]
        else:
            rel_addr_pos = pattern_addr + signature["disp_offset"]
            rel_addr_data = _read_memory(handle, rel_addr_pos, 4)
            if not rel_addr_data or len(rel_addr_data) != 4:
                _kernel32.CloseHandle(handle)
                return False
            rel_addr = struct.unpack("<i", rel_addr_data)[0]
            color_addr = pattern_addr + signature["next_rip_offset"] + rel_addr

            _log(
                f"Pattern matched at 0x{pattern_addr:X}, "
                f"color address: 0x{color_addr:X}"
            )

        # Validate the resolved address is readable before caching
        test = _read_memory(handle, color_addr, 3)
        if not test or len(test) != 3:
            _kernel32.CloseHandle(handle)
            return False

        self._handle = handle
        self._pid = pid
        self._color_addr = color_addr
        self._base = base
        self._size = size

        _log(f"Connected to SAI2 (PID: {pid}, Version: {self.version}, Color: 0x{color_addr:X})")
        return True

    def get_color(self) -> Optional[Dict[str, int]]:
        if not self._handle or not self._color_addr:
            if not self._connect():
                return None

        try:
            data = _read_memory(self._handle, self._color_addr, 3)
            if data and len(data) == 3:
                # SAI stores channels in memory order B, G, R
                return {"r": data[2], "g": data[1], "b": data[0]}
        except Exception:
            self._handle = None
            self._color_addr = None

        return None

    def set_color(self, r: int, g: int, b: int) -> bool:
        if not self._handle or not self._color_addr:
            if not self._connect():
                return False

        r = _clamp8(r)
        g = _clamp8(g)
        b = _clamp8(b)

        try:
            return _write_memory(self._handle, self._color_addr, [b, g, r])
        except Exception:
            self._handle = None
            self._color_addr = None
            return False

    def status(self) -> Dict[str, object]:
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
        }