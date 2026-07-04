#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLIP STUDIO PAINT active brush-color synchronization.

Attaches to a running CLIPStudioPaint.exe process, resolves the address
of the in-memory brush color slot via a build-specific pointer offset,
and translates between the host's packed u32-per-channel encoding and
regular RGB triples.  Supported builds: 4.0, 4.2.7-ex, 5.0, 5.0-ex.

A separate :func:`get_csp_theme` reads the application's UI-theme
preferences from its sidecar SQLite database so the picker can visually
match the host.
"""

from __future__ import annotations

import configparser
import ctypes
from ctypes import wintypes
import glob
import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:
    from pymem import Pymem
    from pymem.process import module_from_name
except ImportError:
    # pymem is Windows-only; allow the rest of the module to import without it
    # so unit tests on other platforms can at least load the file.
    Pymem = None  # type: ignore[assignment]
    module_from_name = None  # type: ignore[assignment]

from brush_color_spaces import (
    SPACE_ORDER,
    any_space_has_nonzero_raws,
    build_space_offsets,
    decode_space_raws,
    encode_space_values,
    format_space_values,
    resolve_active_rgb,
    rgb_to_space_values,
)

# ---------------------------------------------------------------------------
# Build-specific technical constants (objective facts from CLIPStudioPaint.exe)
# ---------------------------------------------------------------------------
# AOB (array-of-byte) signatures of the in-process instruction CSP uses to
# copy the brush color slot between objects.  Any independent reverse
# engineering of the same build produces identical bytes.
_AOB_CSP4_0     = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18 48 83 C1 48 E8 ?? ?? ?? ?? 48 8B C3"
_AOB_CSP4_2_7EX = "41 0F 10 ?? 1C 41 0F 11 ?? 1C F2 41 0F 10 ?? 10 F2 41 0F 11 ?? 10 41 8B ?? 18 41 89 ?? 18"
_AOB_CSP5_0     = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18"

SECTION_NAME       = "ClipStudioPaint"
DEFAULT_VERSION_KEY = "csp4.0"

# Default per-channel offsets inside the color struct.  All four color
# spaces are addressed relative to the RGB slot's base offset (0x20).
_DEFAULT_RED_OFFSET   = 0x20
_DEFAULT_GREEN_OFFSET = 0x24
_DEFAULT_BLUE_OFFSET  = 0x28


# ---------------------------------------------------------------------------
# Per-build profile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _CSPBuildProfile:
    """Static configuration that distinguishes one CSP build from another.

    The optional ``intermediate_offset`` lets some builds add an extra
    indirection step when resolving the live color slot pointer.
    ``aob_offset`` is informational metadata for tooling that scans for
    the AOB signature; it isn't consumed by the sync path itself.
    """

    key: str
    process_name: str
    base_offset: int
    aob_signature: str
    intermediate_offset: Optional[int] = None
    aob_offset: int = 0


_PROFILES: Tuple[_CSPBuildProfile, ...] = (
    _CSPBuildProfile("csp4.0",      "CLIPStudioPaint.exe", 0x0518C2C0, _AOB_CSP4_0),
    _CSPBuildProfile("csp4.2.7-ex", "CLIPStudioPaint.exe", 0x0518C2C0, _AOB_CSP4_2_7EX),
    _CSPBuildProfile("csp5.0",      "CLIPStudioPaint.exe", 0x05449DB0, _AOB_CSP5_0, aob_offset=0x0D),
    _CSPBuildProfile("csp5.0-ex",   "CLIPStudioPaint.exe", 0x05449DB0, _AOB_CSP5_0, aob_offset=0x0D),
)
_PROFILE_INDEX: Dict[str, _CSPBuildProfile] = {p.key: p for p in _PROFILES}


def _normalize_version_key(raw: object) -> str:
    """Coerce arbitrary user input into one of the known profile keys."""
    text = str(raw or "").strip().lower()
    if "4.2.7" in text or "427" in text:
        return "csp4.2.7-ex"
    if "5.0" in text or "csp5" in text:
        return "csp5.0-ex" if "ex" in text else "csp5.0"
    return "csp4.0"


# ---------------------------------------------------------------------------
# Logging + paths
# ---------------------------------------------------------------------------
_DEBUG = False


def _log(message: str) -> None:
    if _DEBUG:
        print(f"[CSPSync] {message}", file=sys.stderr, flush=True)


def _parse_int(text: str) -> int:
    return int(str(text).strip(), 0)


def _resolve_config_file() -> str:
    """Pick the config.ini to read user overrides from.

    Search order: $CSP_SYNC_CONFIG env var, then the file next to the
    frozen exe (PyInstaller bundle), then the file next to this script,
    then a config.ini in the current working directory.
    """
    env_path = os.environ.get("CSP_SYNC_CONFIG", "").strip()
    if env_path:
        return env_path

    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))

    local_cfg = os.path.join(app_dir, "config.ini")
    if os.path.exists(local_cfg):
        return local_cfg

    return os.path.abspath("config.ini")


# ---------------------------------------------------------------------------
# Win32 API surface for reading the running exe's version info
# ---------------------------------------------------------------------------
class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature",         wintypes.DWORD),
        ("dwStrucVersion",      wintypes.DWORD),
        ("dwFileVersionMS",    wintypes.DWORD),
        ("dwFileVersionLS",     wintypes.DWORD),
        ("dwProductVersionMS",  wintypes.DWORD),
        ("dwProductVersionLS",   wintypes.DWORD),
        ("dwFileFlagsMask",     wintypes.DWORD),
        ("dwFileFlags",         wintypes.DWORD),
        ("dwFileOS",            wintypes.DWORD),
        ("dwFileType",          wintypes.DWORD),
        ("dwFileSubtype",       wintypes.DWORD),
        ("dwFileDateMS",        wintypes.DWORD),
        ("dwFileDateLS",        wintypes.DWORD),
    ]


class _ProcessVersionQuery:
    """Lazy ctypes bindings for the Win32 process-image + version-info APIs.

    These calls give us (a) the full path of the running CLIPStudioPaint.exe
    and (b) the VS_VERSION_INFO of that on-disk exe, which we use to
    auto-detect which CSP build is currently attached without trusting
    the user-supplied version key as ground truth.
    """

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _version  = ctypes.WinDLL("version",  use_last_error=True)

    # Bind once at class-definition time so we don't pay argtype setup per call.
    _query_image_name = _kernel32.QueryFullProcessImageNameW
    _query_image_name.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _query_image_name.restype = wintypes.BOOL

    _version_info_size = _version.GetFileVersionInfoSizeW
    _version_info_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _version_info_size.restype  = wintypes.DWORD

    _version_info = _version.GetFileVersionInfoW
    _version_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    _version_info.restype  = wintypes.BOOL

    _ver_query_value = _version.VerQueryValueW
    _ver_query_value.argtypes = [
        wintypes.LPCVOID, wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
    ]
    _ver_query_value.restype = wintypes.BOOL

    @classmethod
    def image_path(cls, process_handle) -> Optional[str]:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not cls._query_image_name(
            wintypes.HANDLE(int(process_handle)), 0, buffer, ctypes.byref(size)
        ):
            return None
        return buffer.value

    @classmethod
    def exe_version(cls, path: str) -> Optional[Tuple[int, int, int, int]]:
        """Return the four-component file version of the exe at ``path``."""
        scratch = wintypes.DWORD(0)
        size = cls._version_info_size(path, ctypes.byref(scratch))
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not cls._version_info(path, 0, size, buffer):
            return None
        value_ptr = ctypes.c_void_p()
        value_len = wintypes.UINT(0)
        if not cls._ver_query_value(buffer, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
            return None
        fixed = ctypes.cast(value_ptr, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        return (
            (fixed.dwFileVersionMS >> 16) & 0xFFFF,
            fixed.dwFileVersionMS & 0xFFFF,
            (fixed.dwFileVersionLS >> 16) & 0xFFFF,
            fixed.dwFileVersionLS & 0xFFFF,
        )


def _detect_build_from_image_path(path: Optional[str]) -> Optional[str]:
    """Map an exe's on-disk file version to one of our profile keys."""
    if not path:
        return None
    version = _ProcessVersionQuery.exe_version(path)
    if not version:
        return None
    major, minor, build, _patch = version
    if (major, minor) >= (5, 0):
        return "csp5.0"
    if (major, minor, build) == (4, 2, 7):
        return "csp4.2.7-ex"
    if major == 4:
        return "csp4.0"
    return None


# ---------------------------------------------------------------------------
# Small value codecs used by the dump() inspector
# ---------------------------------------------------------------------------
def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def _u32_to_signed(value: int) -> int:
    """Convert an unsigned 32-bit value to its two's-complement signed form.

    pymem's :meth:`Pymem.write_int` expects a signed int, so we fold the
    high bit down rather than letting Python's arbitrary-precision ints
    leak through.
    """
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def _decode_u16x2_duplicate(raw: int) -> Optional[int]:
    """Decode a u32 that stores a single 8-bit value as two copies of the
    same 16-bit pattern (low 16 == high 16).  Used by CSP to pad an 8-bit
    channel into a 32-bit slot.
    """
    low  = raw & 0xFFFF
    high = (raw >> 16) & 0xFFFF
    if low != high:
        return None
    return _clamp_byte(round((low / 65535.0) * 255.0))


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------
class CSPSync:
    """Memory-sync backend for CLIP STUDIO PAINT's active brush color.

    The class is intentionally a thin facade over three concerns:

    * version profile selection (``_CSPBuildProfile`` registry),
    * the live Win32 process attachment via pymem (handled inline because
      pymem already encapsulates the platform specifics), and
    * the on-disk color-struct layout (:mod:`brush_color_spaces`).

    It preserves the public API that the rest of the app depends on:
    ``set_version`` / ``connect`` / ``get_color`` / ``set_color`` /
    ``status`` / ``dump``, plus the ``pm`` and ``pid`` attributes used
    by the polling thread to detect whether a process is attached.
    """

    def __init__(self) -> None:
        # Live process attachment — None when not connected.
        self.pm: Optional[Pymem] = None
        self.pid: Optional[int] = None
        self.module_base: Optional[int] = None
        self.target: Optional[int] = None

        # Currently selected build profile + the per-channel layout we
        # resolved from config.ini (or the defaults).
        self._profile: _CSPBuildProfile = _PROFILE_INDEX[DEFAULT_VERSION_KEY]
        self.current_version: str = self._profile.key
        self.process_name: str = self._profile.process_name
        self.base_offset: int = self._profile.base_offset
        self.intermediate_offset: Optional[int] = self._profile.intermediate_offset
        self.aob_signature: str = self._profile.aob_signature

        self.r_off: int = _DEFAULT_RED_OFFSET
        self.g_off: int = _DEFAULT_GREEN_OFFSET
        self.b_off: int = _DEFAULT_BLUE_OFFSET
        self.color_format: str = "u16x2_dup"
        self.space_offsets = build_space_offsets(self.r_off)

        # Honor CSP_SYNC_VERSION env override before applying user config.
        env_version = os.environ.get("CSP_SYNC_VERSION", DEFAULT_VERSION_KEY)
        self._apply_profile(_normalize_version_key(env_version))
        self._load_user_config()

    # ----- profile management ---------------------------------------------
    def _apply_profile(self, key: str) -> None:
        profile = _PROFILE_INDEX.get(key, _PROFILE_INDEX[DEFAULT_VERSION_KEY])
        self._profile = profile
        self.current_version = profile.key
        self.process_name = profile.process_name
        self.base_offset = profile.base_offset
        self.intermediate_offset = profile.intermediate_offset
        self.aob_signature = profile.aob_signature

    def _load_user_config(self) -> None:
        path = _resolve_config_file()
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if not parser.has_section(SECTION_NAME):
            return
        sec = parser[SECTION_NAME]
        # processname / baseoffset / aobsignature from the config file only
        # apply to the default profile; non-default profiles are pinned by
        # _PROFILES so user typos can't desync a 5.0-only build.
        if self.current_version == DEFAULT_VERSION_KEY:
            self.process_name = sec.get("processname", self.process_name)
            self.base_offset = _parse_int(sec.get("baseoffset", hex(self.base_offset)))
            self.aob_signature = sec.get("aobsignature", self.aob_signature)
        self.r_off = _parse_int(sec.get("redoffset",   hex(self.r_off)))
        self.g_off = _parse_int(sec.get("greenoffset", hex(self.g_off)))
        self.b_off = _parse_int(sec.get("blueoffset",  hex(self.b_off)))
        self.color_format = sec.get("colorformat", self.color_format)
        self.space_offsets = build_space_offsets(self.r_off)
        _log(
            "Config loaded: "
            f"Path={path} "
            f"Version={self.current_version} Process={self.process_name} "
            f"Base=0x{self.base_offset:X} "
            f"R=0x{self.r_off:X} G=0x{self.g_off:X} B=0x{self.b_off:X} "
            f"Format={self.color_format} "
            f"Layout={self.space_offsets}"
        )

    def set_version(self, key: str) -> bool:
        """Switch to a different CSP build profile. Returns True if it changed."""
        normalized = _normalize_version_key(key)
        if normalized == self.current_version:
            return False
        self._apply_profile(normalized)
        # Force a reconnect on next access; pymem's open handle is bound to
        # the previous build's process_name and base_offset.
        if self.pm is not None:
            try:
                self.pm.close_process()
            except Exception:
                pass
        self.pm = None
        self.pid = None
        self.module_base = None
        self.target = None
        _log(
            f"Version switched to {normalized}, "
            f"process={self.process_name}, base=0x{self.base_offset:X}"
        )
        return True

    # ----- connection management -----------------------------------------
    def connect(self) -> bool:
        """Attach to the running CSP process and resolve the color slot pointer.

        If the on-disk exe version doesn't match the currently selected
        profile, the profile is silently swapped to the detected one and
        the pointer is read against the new base offset.
        """
        try:
            self.pm = Pymem(self.process_name)
            self.pid = self.pm.process_id
            mod = module_from_name(self.pm.process_handle, self.process_name)
            self.module_base = mod.lpBaseOfDll

            image_path = _ProcessVersionQuery.image_path(self.pm.process_handle)
            detected = _detect_build_from_image_path(image_path)
            if detected and detected != self.current_version:
                requested = self.current_version
                self._apply_profile(detected)
                _log(
                    f"Auto-detected version {detected} from process "
                    f"(requested={requested}, path={image_path})"
                )

            ptr_addr = self.module_base + self.base_offset
            dereferenced = self.pm.read_longlong(ptr_addr)
            if self.intermediate_offset is not None:
                self.target = dereferenced + self.intermediate_offset
            else:
                self.target = dereferenced
            _log(
                "Connected: "
                f"PID={self.pid} Version={self.current_version} "
                f"Base=0x{self.base_offset:X} Target=0x{self.target:X} "
                f"R_off=0x{self.r_off:X} G_off=0x{self.g_off:X} B_off=0x{self.b_off:X}"
            )
            return True
        except Exception as exc:
            _log(f"connect failed: {exc}")
            self._drop_connection()
            return False

    def _drop_connection(self) -> None:
        if self.pm is not None:
            try:
                self.pm.close_process()
            except Exception:
                pass
        self.pm = None
        self.pid = None
        self.module_base = None
        self.target = None

    # ----- memory accessors -----------------------------------------------
    def _read_u32(self, address: int) -> int:
        assert self.pm is not None
        return self.pm.read_int(address) & 0xFFFFFFFF

    def _write_u32(self, address: int, value: int) -> None:
        assert self.pm is not None
        self.pm.write_int(address, _u32_to_signed(value))

    def _snapshot_color_slot(self, base_addr: int) -> Dict[str, Dict[str, object]]:
        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, offsets in self.space_offsets.items():
            raws = tuple(self._read_u32(base_addr + off) for off in offsets)
            snapshots[space_name] = {
                "offsets": offsets,
                "raws": raws,
                "values": decode_space_raws(space_name, raws),
            }
        return snapshots

    def _resolve_space_addresses(self) -> Optional[Dict[str, Tuple[int, ...]]]:
        """Re-resolve the color slot pointer and build per-space address tuples.

        CSP moves the color slot across host-side allocations; we re-read
        the anchor pointer each call and adopt a new target only when it
        points at plausible data (any space non-zero) *or* when we have
        no previous target, so transient zero-initialized buffers don't
        hijack the slot pointer mid-drag.
        """
        if self.pm is None or self.module_base is None:
            return None
        try:
            dereferenced = self.pm.read_longlong(self.module_base + self.base_offset)
            if dereferenced:
                if self.intermediate_offset is not None:
                    candidate = dereferenced + self.intermediate_offset
                else:
                    candidate = dereferenced
                if candidate:
                    try:
                        probe = self._snapshot_color_slot(candidate)
                        if any_space_has_nonzero_raws(probe) or self.target is None:
                            self.target = candidate
                    except Exception:
                        pass
        except Exception:
            pass

        if self.target is None:
            return None

        # Validate the cached target is still readable.
        try:
            self._snapshot_color_slot(self.target)
        except Exception as exc:
            _log(f"_resolve_space_addresses: target 0x{self.target:X} unreadable: {exc}")
            self.target = None
            return None

        return {
            name: tuple(self.target + off for off in offsets)
            for name, offsets in self.space_offsets.items()
        }

    # ----- public color access -------------------------------------------
    def get_color(self) -> Optional[Dict[str, int]]:
        if self.pm is None and not self.connect():
            return None

        space_addrs = self._resolve_space_addresses()
        if not space_addrs:
            _log("get_color: target not ready")
            return None

        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, addresses in space_addrs.items():
            raws = tuple(self._read_u32(addr) for addr in addresses)
            snapshots[space_name] = {
                "offsets": (
                    tuple(addr - self.target for addr in addresses)
                    if self.target is not None
                    else addresses
                ),
                "raws": raws,
                "values": decode_space_raws(space_name, raws),
            }

        source_space, rgb, source_values = resolve_active_rgb(snapshots)
        source_raws = snapshots[source_space]["raws"]
        _log(
            "get_color: "
            f"source={source_space} "
            f"offsets={snapshots[source_space]['offsets']} "
            f"raw={[f'0x{raw:08X}' for raw in source_raws]} "
            f"values={format_space_values(source_space, source_values)} "
            f"-> RGB=[{rgb['r']}, {rgb['g']}, {rgb['b']}]"
        )
        return rgb

    def set_color(self, r: int, g: int, b: int) -> bool:
        if self.pm is None and not self.connect():
            return False

        space_addrs = self._resolve_space_addresses()
        if not space_addrs:
            _log("set_color: target not ready")
            return False

        rgb = {"r": _clamp_byte(r), "g": _clamp_byte(g), "b": _clamp_byte(b)}
        try:
            for space_name in SPACE_ORDER:
                encoded = encode_space_values(
                    space_name, rgb_to_space_values(space_name, rgb)
                )
                for addr, raw in zip(space_addrs[space_name], encoded):
                    self._write_u32(addr, raw)
            _log(
                "set_color: "
                f"RGB=[{rgb['r']}, {rgb['g']}, {rgb['b']}] "
                f"synced_spaces={list(SPACE_ORDER)}"
            )
            return True
        except Exception as exc:
            _log(f"set_color: exception: {exc}")
            return False

    # ----- introspection ---------------------------------------------------
    def status(self) -> Dict[str, object]:
        if self.pm is None:
            self.connect()
        space_addrs = self._resolve_space_addresses() if self.pm is not None else None
        connected = (
            self.pm is not None
            and self.target is not None
            and space_addrs is not None
        )
        return {
            "connected": bool(connected),
            "pid": self.pid if connected else None,
            "baseOffset": f"0x{self.base_offset:X}",
            "target": f"0x{self.target:X}" if connected and self.target is not None else None,
            "aob": self.aob_signature,
            "version": self.current_version,
            "processName": self.process_name,
        }

    def dump(self) -> Dict[str, object]:
        """Diagnostic snapshot of the color slot for debugging.

        Walks the first 0x60 bytes of the slot in 4-byte steps, decoding
        each u32 both as a raw hex value and as the u16x2-duplicate form
        CSP historically uses, then attaches the structured per-space
        snapshots for human inspection.
        """
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        if self._resolve_space_addresses() is None or self.target is None:
            return {"error": "not connected"}
        assert self.pm is not None
        assert self.target is not None

        rows = []
        for off in range(0, 0x60, 4):
            addr = self.target + off
            raw = self._read_u32(addr)
            rows.append({
                "offset":   hex(off),
                "address":  f"0x{addr:X}",
                "hex":      f"0x{raw:08X}",
                "u16x2_dup": _decode_u16x2_duplicate(raw),
            })

        snapshots = self._snapshot_color_slot(self.target)
        spaces = []
        for space_name in SPACE_ORDER:
            snapshot = snapshots[space_name]
            spaces.append({
                "space":    space_name,
                "offsets":  [hex(off) for off in snapshot["offsets"]],
                "raw_hex":  [f"0x{raw:08X}" for raw in snapshot["raws"]],
                "values":   snapshot["values"],
                "asText":   format_space_values(space_name, snapshot["values"]),
            })
        return {"target": f"0x{self.target:X}", "spaces": spaces, "rows": rows}


# ---------------------------------------------------------------------------
# CSP desktop theme reader
# ---------------------------------------------------------------------------
def get_csp_theme() -> dict:
    """Read CSP's UI theme preferences from its sidecar SQLite config.

    Returns a small dict describing the background / text / scrollbar
    colors the picker should adopt to visually match the host.  When CSP
    isn't installed or its preferences can't be parsed, falls back to a
    neutral gray theme.

    CSP stores theme state in ``Preference/Config.sqlite`` under
    ``APPDATA/CELSYSUserData/CELSYS[_EN]/CLIPStudioPaintVer*/`` (with
    several legacy path variants).  We probe all of them and use the
    most recently modified match.
    """
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE")

    candidate_patterns = []
    if appdata:
        candidate_patterns.extend([
            os.path.join(appdata, "CELSYSUserData", "CELSYS",     "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYSUserData", "CELSYS_EN",  "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaint",     "*", "Boot", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaint",     "*", "Boot", "Config.sqlite"),
            os.path.join(appdata, "CELSYS",         "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
            os.path.join(appdata, "CELSYS_EN",      "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
        ])
    if userprofile:
        candidate_patterns.extend([
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYSUserData", "CELSYS",              "CLIPStudioPaintVer*", "Preference", "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaint",     "*", "Boot",       "Config.sqlite"),
            os.path.join(userprofile, "Documents", "CELSYS",         "CLIPStudioPaint",     "*", "Preference", "Config.sqlite"),
        ])

    found: list[str] = []
    for pattern in candidate_patterns:
        found.extend(glob.glob(pattern))

    if not found:
        return _theme_fallback()

    latest = max(found, key=os.path.getmtime)
    try:
        conn = sqlite3.connect(latest)
    except Exception as exc:
        return _theme_fallback(error=str(exc))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ApplicationThemeColor, ApplicationThemeColorLightDensity, "
            "ApplicationThemeColorDarkDensity FROM Interface"
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return _theme_fallback()

    theme_color, light_density, dark_density = row
    is_dark = _resolve_is_dark(theme_color)

    if is_dark:
        # CSP's dark gray baseline is 78; dark-density slides it by ~2.7 each step.
        gray = int(max(15, min(255, 78.0 + dark_density * 2.7)))
        edge_gray = int(max(0, min(255, 0.852 * gray - 10.5)))
        theme_name = "csp-dark"
    else:
        # CSP's light gray baseline is 241; light-density slides it by ~2.5 each step.
        gray = int(max(100, min(240, 241.0 + light_density * 2.5)))
        edge_gray = int(max(0, min(255, 1.45 * gray - 124.0)))
        theme_name = "csp-light"

    bg_hex      = f"#{gray:02x}{gray:02x}{gray:02x}"
    edge_hex    = f"#{edge_gray:02x}{edge_gray:02x}{edge_gray:02x}"
    text_color  = "#ffffff" if gray < 130 else "#222222"

    return {
        "theme":  theme_name,
        "bg":     bg_hex,
        "text":   text_color,
        "barBg":  edge_hex,
        "border": f"1px solid {edge_hex}",
    }


_GRAY_FALLBACK = {
    "theme":  "gray",
    "bg":     "#b2b2b2",
    "text":   "#222222",
    "barBg":  "#cbcccb",
    "border": "1px solid #cbcccb",
}


def _theme_fallback(error: Optional[str] = None) -> dict:
    if error is not None:
        return {"error": error, **_GRAY_FALLBACK}
    return dict(_GRAY_FALLBACK)


def _resolve_is_dark(theme_color: int) -> bool:
    """Map CSP's stored theme-color enum to a dark/light verdict.

    0 = dark, 1 = light, 2 = follow system.  When the per-system registry
    key is missing or unreadable, default to dark (CSP's most common
    setting among artists).
    """
    if theme_color == 2:
        return True
    if theme_color == 1:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return True