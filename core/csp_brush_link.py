#!/usr/bin/env python3

"""CLIP STUDIO PAINT active brush-color synchronization.

Attaches to a running CLIPStudioPaint.exe process, resolves the address
of the in-memory brush color slot via a build-specific pointer offset,
and translates between the host's packed u32-per-channel encoding and
regular RGB triples.  Supported builds: 4.x, 5.0, and 5.1 (5.1 moved
the global slot pointer, but the RGB channels keep the legacy u32
encoding at the same +0x20 / +0x24 / +0x28 offsets).

The module exposes :data:`AOB_MAP` for external AOB scanning tools that
need per-build signature resolution (e.g. distinguishing 4.0 from
4.2.7-ex).  The sync path itself only cares about the base_offset
difference between pre-5.0 and 5.0+.

A separate :func:`get_csp_theme` reads the application's UI-theme
preferences from its sidecar SQLite database so the picker can visually
match the host.
"""

from __future__ import annotations

import configparser
import ctypes
import glob
import os
import sqlite3
import struct
import sys
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from pymem import Pymem
    from pymem.process import module_from_name
except ImportError:
    # pymem is Windows-only; allow the rest of the module to import without it
    # so unit tests on other platforms can at least load the file.
    Pymem = None  # type: ignore[assignment]
    module_from_name = None  # type: ignore[assignment]

try:
    from brush_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
        build_space_offsets,
        decode_space_raws,
        encode_space_values,
        encode_space_values_float,
        format_space_values,
        resolve_active_rgb,
        rgb_to_hsv_float,
        rgb_to_space_values,
        space_to_rgb_float,
        space_to_rgb_values,
    )
except ImportError:
    from core.brush_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
        build_space_offsets,
        decode_space_raws,
        encode_space_values,
        encode_space_values_float,
        format_space_values,
        resolve_active_rgb,
        rgb_to_hsv_float,
        rgb_to_space_values,
        space_to_rgb_float,
        space_to_rgb_values,
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

# Public mapping used by external AOB scanning tools that need to resolve
# a legacy per-build key (e.g. "csp4.2.7-ex") to its exact signature.
AOB_MAP: dict[str, str] = {
    "csp4.0":      _AOB_CSP4_0,
    "csp4.2.7-ex": _AOB_CSP4_2_7EX,
    "csp5.0":      _AOB_CSP5_0,
    "csp5.0-ex":   _AOB_CSP5_0,
}

SECTION_NAME       = "ClipStudioPaint"
DEFAULT_VERSION_KEY = "csp4.x"

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
    ``color_format`` selects the in-memory channel encoding: the legacy
    proportional u16x2 duplicate layout, or CSP 5.1's compact RGB slot.
    """

    key: str
    process_name: str
    base_offset: int
    aob_signature: str
    intermediate_offset: int | None = None
    aob_offset: int = 0
    color_format: str = "u16x2_dup"


_PROFILES: tuple[_CSPBuildProfile, ...] = (
    _CSPBuildProfile("csp4.x", "CLIPStudioPaint.exe", 0x0518C2C0, _AOB_CSP4_0),
    _CSPBuildProfile("csp5.x", "CLIPStudioPaint.exe", 0x05449DB0, _AOB_CSP5_0,
                     aob_offset=0x0D),
    _CSPBuildProfile("csp5.1", "CLIPStudioPaint.exe", 0x0556BFC8, "",
                     color_format="rgb_u32"),
)
_PROFILE_INDEX: dict[str, _CSPBuildProfile] = {p.key: p for p in _PROFILES}


def _normalize_version_key(raw: object) -> str:
    """Coerce arbitrary user input into one of the known profile keys.

    Accepts legacy keys ("csp4.0", "csp4.2.7-ex", "csp5.0", "csp5.0-ex")
    and maps them to the simplified "csp4.x" / "csp5.x" scheme.
    """
    text = str(raw or "").strip().lower()
    if "5.1" in text:
        return "csp5.1"
    if "5.0" in text or "csp5" in text or "5.x" in text:
        return "csp5.x"
    if "4." in text or "csp4" in text or "4.x" in text:
        return "csp4.x"
    return "csp4.x"


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
    def image_path(cls, process_handle) -> str | None:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not cls._query_image_name(
            wintypes.HANDLE(int(process_handle)), 0, buffer, ctypes.byref(size)
        ):
            return None
        return buffer.value

    @classmethod
    def exe_version(cls, path: str) -> tuple[int, int, int, int] | None:
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


def _detect_build_from_version(version: tuple[int, int, int, int] | None) -> str | None:
    """Map a CSP file version to a simplified profile key."""
    if not version:
        return None
    major, minor, _build, _patch = version
    if (major, minor) >= (5, 1):
        return "csp5.1"
    if (major, minor) >= (5, 0):
        return "csp5.x"
    if major == 4:
        return "csp4.x"
    return None


def _detect_build_from_image_path(path: str | None) -> str | None:
    """Map an exe's on-disk file version to a simplified profile key."""
    if not path:
        return None
    return _detect_build_from_version(_ProcessVersionQuery.exe_version(path))


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


def _decode_u16x2_duplicate(raw: int) -> int | None:
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
        self.pm: Any | None = None
        self.pid: int | None = None
        self.module_base: int | None = None
        self.target: int | None = None
        # Cached absolute addresses of every sub-color memory copy
        # (re-resolved on demand when the cache goes stale).
        self._sub_copy_addrs: list[int] | None = None
        # Same for the MAIN color slot (+0x3C HSV u32 copies).
        self._main_copy_addrs: list[int] | None = None

        # Currently selected build profile + the per-channel layout we
        # resolved from config.ini (or the defaults).
        self._profile: _CSPBuildProfile = _PROFILE_INDEX[DEFAULT_VERSION_KEY]
        self.current_version: str = self._profile.key
        self.process_name: str = self._profile.process_name
        self.base_offset: int = self._profile.base_offset
        self.intermediate_offset: int | None = self._profile.intermediate_offset
        self.aob_signature: str = self._profile.aob_signature

        self.r_off: int = _DEFAULT_RED_OFFSET
        self.g_off: int = _DEFAULT_GREEN_OFFSET
        self.b_off: int = _DEFAULT_BLUE_OFFSET
        self.color_format: str = self._profile.color_format
        self.space_offsets = build_space_offsets(self.r_off)
        self._last_hsv_h: float = 0.0
        self._last_hsv_s: float = 0.0

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
        self.color_format = profile.color_format

    def _load_user_config(self) -> None:
        path = _resolve_config_file()
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if not parser.has_section(SECTION_NAME):
            return
        sec = parser[SECTION_NAME]
        # processname / baseoffset / aobsignature from the config file only
        # apply to the default profile; non-default profiles are pinned by
        # _PROFILES so user typos can't desync a non-default build.
        if self.current_version == DEFAULT_VERSION_KEY:
            self.process_name = sec.get("processname", self.process_name)
            self.base_offset = _parse_int(sec.get("baseoffset", hex(self.base_offset)))
            self.aob_signature = sec.get("aobsignature", self.aob_signature)
        self.r_off = _parse_int(sec.get("redoffset",   hex(self.r_off)))
        self.g_off = _parse_int(sec.get("greenoffset", hex(self.g_off)))
        self.b_off = _parse_int(sec.get("blueoffset",  hex(self.b_off)))
        if self._profile.color_format == "u16x2_dup":
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
            if Pymem is None or module_from_name is None:
                raise ValueError("pymem not available")
            self.pm = Pymem(self.process_name)
            self.pid = self.pm.process_id
            mod = module_from_name(self.pm.process_handle, self.process_name)
            if mod is None:
                raise ValueError("module not found")
            module_base = mod.lpBaseOfDll
            self.module_base = module_base

            image_path = _ProcessVersionQuery.image_path(self.pm.process_handle)
            detected = _detect_build_from_image_path(image_path)
            if detected and detected != self.current_version:
                requested = self.current_version
                self._apply_profile(detected)
                _log(
                    f"Auto-detected version {detected} from process "
                    f"(requested={requested}, path={image_path})"
                )

            ptr_addr = module_base + self.base_offset
            dereferenced = int(self.pm.read_longlong(ptr_addr))
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

    # ----- CSP 5.1 compact RGB slot --------------------------------------
    # MAIN slot: three u32 HSV channels (proportional encoding, identical
    # to the sub slot at +0x9C) at +0x3C/+0x40/+0x44. Verified against the
    # companion protocol: writing main = (123,45,67) through CSP itself
    # updates exactly these three u32s (H=0xF3F73F73, S=0xA2576A25,
    # V=0x7B7B7B7B). The old +0x20/+0x24/+0x28 offsets were NOT written by
    # CSP (stale residuals), which made main-color reads show a wrong
    # color after the user changed the color inside CSP.
    #
    # The 16-bit HSV UI copies at +0x3E/+0x42/+0x44 overlap the high half
    # of these u32s (+0x3C+2, +0x40+2, +0x44), so writing the full u32s
    # keeps both views consistent.
    _RGB_U32_OFFS = (0x3C, 0x40, 0x44)
    _HSV_UI_OFFS = (0x3E, 0x42, 0x44)
    # Transparent flag: u32 at slot base + 0x08. 0x00000000 = opaque,
    # 0xFFFFFFFF = transparent (verified empirically on CSP 5.1 by diffing
    # the slot across manual transparent/non-transparent transitions).
    _TRANSPARENT_FLAG_OFFS = 0x08
    _TRANSPARENT_FLAG_ON = 0xFFFFFFFF
    # Sub-color (background) slot: three u32 HSV channels (proportional,
    # same encoding as the main slot's HSV UI) at +0x9C/+0xA0/+0xA4.
    # Active-slot index lives in the low byte of +0x08 (0 = main, 1 = sub);
    # +0x08 == 0xFFFFFFFF means the ACTIVE slot is transparent.
    #
    # The brush reads the sub color from MULTIPLE in-memory copies (the
    # companion path updates ~12; the +0x9C slot alone is not the brush
    # source). We locate every copy by searching the process for the
    # current sub-color pattern, cache the addresses, and write all of
    # them. Cache is re-validated on each write (cheap read of +0x9C).
    _SUB_HSV_OFFS = (0x9C, 0xA0, 0xA4)
    _SUB_COPY_LIMIT = 200  # search hit cap (avoid all-zero/pattern blowups)

    def _read_float32(self, address: int) -> float:
        assert self.pm is not None
        raw = self.pm.read_bytes(address, 4)
        return struct.unpack("<f", raw)[0]

    def _write_float32(self, address: int, value: float) -> None:
        assert self.pm is not None
        self.pm.write_bytes(address, struct.pack("<f", float(value)), 4)

    def _read_transparent_flag(self) -> bool:
        """Return True when CSP's current drawing color is transparent.

        Only valid for the rgb_u32 (5.1) slot layout; the u16x2_dup builds
        use a different struct where +0x08 is a color channel.
        """
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return False
        try:
            return self._read_u32(self.target + self._TRANSPARENT_FLAG_OFFS) == self._TRANSPARENT_FLAG_ON
        except Exception:
            return False

    def _write_transparent_flag(self, transparent: bool) -> bool:
        """Set/clear the 5.1 transparent flag in CSP memory."""
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return False
        try:
            self._write_u32(
                self.target + self._TRANSPARENT_FLAG_OFFS,
                self._TRANSPARENT_FLAG_ON if transparent else 0,
            )
            _log(f"set_color (rgb_u32): transparent={transparent}")
            return True
        except Exception as exc:
            _log(f"set_color (rgb_u32): transparent exception: {exc}")
            return False

    def get_sub_color(self) -> dict[str, int] | None:
        """Public facade for the 5.1 sub-color slot (index 1)."""
        return self._read_sub_color()

    def get_active_slot_index(self) -> int | None:
        """Current active-slot index: 0 = main, 1 = sub.

        Returns ``None`` while the active slot is transparent (the flag
        overwrites the low byte with 0xFF).
        """
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return None
        try:
            raw = self._read_u32(self.target + self._TRANSPARENT_FLAG_OFFS)
        except Exception:
            return None
        if raw == self._TRANSPARENT_FLAG_ON:
            return None
        return int(raw & 0xFF)

    def _read_sub_color(self) -> dict[str, int] | None:
        """Read the 5.1 sub-color (background) slot as 8-bit RGB.

        Sub slot stores three u32 HSV channels (proportional encoding) at
        +0x9C/+0xA0/+0xA4. The transparent flag belongs to the active
        slot and is reported by :meth:`get_color` (main path); sub
        transparency is not reported here to avoid duplicate signals.

        NOTE: +0x9C is only ONE of the sub-color copies in memory — the
        brush reads from multiple copies (see :meth:`_write_sub_color`).
        When :attr:`_sub_copy_addrs` has been located (by an earlier
        write), read from that authoritative copy set instead of trusting
        the +0x9C mirror alone: the mirror can lag behind the brush copy
        (first switch to the background color then shows a stale value).
        """
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return None
        pm = self.pm
        target = self.target
        try:
            addrs = getattr(self, "_sub_copy_addrs", None)
            if addrs:
                # 写路径定位的副本集（含权威笔刷副本）。验证所有副本仍持
                # 有同一 12 字节模式才可信；任一失效/不一致即回退 +0x9C。
                raws_12 = pm.read_bytes(addrs[0], 12)
                ok_cache = True
                for addr in addrs[1:]:
                    try:
                        if pm.read_bytes(addr, 12) != raws_12:
                            ok_cache = False
                            break
                    except Exception:
                        ok_cache = False
                        break
                if ok_cache:
                    raws = tuple(
                        int.from_bytes(raws_12[i * 4:(i + 1) * 4], "little")
                        for i in range(3)
                    )
                    values = decode_space_raws("hsv", raws)
                    rgb = space_to_rgb_values("hsv", values)
                    return {
                        "r": _clamp_byte(rgb["r"]),
                        "g": _clamp_byte(rgb["g"]),
                        "b": _clamp_byte(rgb["b"]),
                        "transparent": 0,
                        "index": 1,
                    }
            raws = tuple(self._read_u32(target + off) for off in self._SUB_HSV_OFFS)
            values = decode_space_raws("hsv", raws)
            rgb = space_to_rgb_values("hsv", values)
            return {
                "r": _clamp_byte(rgb["r"]),
                "g": _clamp_byte(rgb["g"]),
                "b": _clamp_byte(rgb["b"]),
                "transparent": 0,
                "index": 1,
            }
        except Exception as exc:
            _log(f"_read_sub_color: exception: {exc}")
            return None

    def _search_pattern(self, pattern: bytes) -> list[int]:
        """Search committed readable data pages (excluding code sections)
        for *pattern*. Returns every hit address."""
        if self.pm is None:
            return []
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt

            class _MBI(_ct.Structure):
                _fields_ = [
                    ("BaseAddress", _ct.c_void_p),
                    ("AllocationBase", _ct.c_void_p),
                    ("AllocationProtect", _wt.DWORD),
                    ("PartitionId", _wt.DWORD),
                    ("RegionSize", _ct.c_size_t),
                    ("State", _wt.DWORD),
                    ("Protect", _wt.DWORD),
                    ("Type", _wt.DWORD),
                ]

            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            vqe = k32.VirtualQueryEx
            vqe.argtypes = [_wt.HANDLE, _ct.c_void_p,
                            _ct.POINTER(_MBI), _ct.c_size_t]
            vqe.restype = _ct.c_size_t
            rp = k32.ReadProcessMemory
            rp.argtypes = [_wt.HANDLE, _ct.c_void_p, _ct.c_void_p,
                           _ct.c_size_t, _ct.POINTER(_ct.c_size_t)]
            rp.restype = _wt.BOOL

            hits: list[int] = []
            addr = _ct.c_void_p(0)
            mbi = _MBI()
            buf = _ct.create_string_buffer(1 << 20)
            while True:
                if vqe(self.pm.process_handle, addr, _ct.byref(mbi),
                       _ct.sizeof(mbi)) == 0:
                    break
                base = mbi.BaseAddress or 0
                size = mbi.RegionSize or 0
                if (size and mbi.State == 0x1000
                        and (mbi.Protect & 0x3E)
                        and not (mbi.Protect & 0x100)
                        and not (0x7FF000000000 <= base < 0x800000000000)):
                    off = 0
                    while off < size:
                        chunk = min(1 << 20, size - off)
                        nread = _ct.c_size_t(0)
                        if rp(self.pm.process_handle, _ct.c_void_p(base + off),
                              buf, chunk, _ct.byref(nread)):
                            data = buf.raw[:nread.value]
                            pos = data.find(pattern)
                            while pos != -1:
                                hits.append(base + off + pos)
                                pos = data.find(pattern, pos + 1)
                        off += chunk
                addr = _ct.c_void_p(base + size)
            return hits
        except Exception as exc:
            _log(f"_search_pattern: exception: {exc}")
            return []

    def has_sub_copy_cache(self) -> bool:
        """True once the sub-color copy addresses are known."""
        return bool(getattr(self, "_sub_copy_addrs", None))

    def capture_sub_copies_from_current(self) -> int:
        """Search the process for the CURRENT sub-color value and cache the
        hit addresses (authoritative brush source + UI mirrors).

        Call right after a companion-protocol sub-color write so the cache
        includes CSP's authoritative copy. Returns the number of copies.
        """
        if self.pm is None or self.target is None:
            return 0
        try:
            old = b"".join(
                self._read_u32(self.target + off).to_bytes(4, "little")
                for off in self._SUB_HSV_OFFS
            )
            hits = self._search_pattern(old)
            base_addr = self.target + self._SUB_HSV_OFFS[0]
            if len(hits) <= self._SUB_COPY_LIMIT:
                addrs = list(hits)
                if base_addr not in addrs:
                    addrs.append(base_addr)
            else:
                addrs = [base_addr]
            self._sub_copy_addrs = addrs
            _log(f"capture_sub_copies: {len(addrs)} copies of current sub color")
            return len(addrs)
        except Exception as exc:
            _log(f"capture_sub_copies: exception: {exc}")
            return 0

    def _locate_hsv_copies(self, old: bytes, cache_attr: str,
                           base_off: int) -> list[int]:
        """Locate every in-memory copy of the current HSV u32 pattern.

        The brush reads main/sub colors from MULTIPLE copies (verified: 8
        copies for both slots on CSP 5.1); the base slot alone is not the
        brush source. The cached address list is trusted only while every
        address still holds *old* (CSP may create/destroy copies at
        runtime). Falls back to the base slot when the hit count explodes.
        """
        if self.target is None or self.pm is None:
            return []
        pm = self.pm
        addrs = getattr(self, cache_attr, None)
        if addrs:
            ok_cache = True
            for addr in addrs:
                try:
                    if pm.read_bytes(addr, 12) != old:
                        ok_cache = False
                        break
                except Exception:
                    ok_cache = False
                    break
            if not ok_cache:
                addrs = None
        if not addrs:
            hits = self._search_pattern(old)
            if len(hits) <= self._SUB_COPY_LIMIT:
                addrs = list(hits)
                base_addr = self.target + base_off
                if base_addr not in addrs:
                    addrs.append(base_addr)
            else:
                addrs = [self.target + base_off]
            setattr(self, cache_attr, addrs)
            _log(f"_locate_hsv_copies: located {len(addrs)} copies")
        return addrs

    def _write_sub_color(self, r: int, g: int, b: int) -> bool:
        """Write the 5.1 sub-color slot (HSV u32) to EVERY in-memory copy.

        The brush reads the sub color from multiple copies; +0x9C alone is
        only the UI mirror. We search the process for the current sub-color
        pattern once (cached), then write the new value to all copies.
        """
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return False
        try:
            # 当前副色模式（缓存验证用）+ 新模式
            old = b"".join(
                self._read_u32(self.target + off).to_bytes(4, "little")
                for off in self._SUB_HSV_OFFS
            )
            hsv = rgb_to_space_values("hsv", {"r": _clamp_byte(r), "g": _clamp_byte(g), "b": _clamp_byte(b)})
            new = b"".join(raw.to_bytes(4, "little") for raw in encode_space_values("hsv", hsv))

            addrs = self._locate_hsv_copies(old, "_sub_copy_addrs", self._SUB_HSV_OFFS[0])
            for addr in addrs:
                self.pm.write_bytes(addr, new, 12)
            _log(f"set_color (rgb_u32 sub): RGB=[{r}, {g}, {b}] -> {len(addrs)} copies")
            return True
        except Exception as exc:
            _log(f"set_color (rgb_u32 sub): exception: {exc}")
            return False

    def _resolve_rgb_target(self) -> bool:
        """Re-resolve the 5.1 global color slot pointer and validate it."""
        if self.pm is None or self.module_base is None:
            return False
        try:
            dereferenced = int(self.pm.read_longlong(self.module_base + self.base_offset))
            if dereferenced:
                self.target = dereferenced
            if self.target is None:
                return False
            # Reading all three RGB channels validates the cached target.
            for off in self._RGB_U32_OFFS:
                _ = self.pm.read_int(self.target + off)
            return True
        except Exception as exc:
            _log(f"_resolve_rgb_target: target unreadable: {exc}")
            self.target = None
            return False

    def _read_rgb_u32(self) -> dict[str, int] | None:
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return None
        # +0x3C/+0x40/+0x44 store HSV as proportional u32s (same encoding
        # as the sub slot at +0x9C) — NOT RGB. Verified against companion:
        # CSP itself updates exactly these three u32s on main-color change.
        raws = tuple(self._read_u32(self.target + off) for off in self._RGB_U32_OFFS)
        values = decode_space_raws("hsv", raws)
        rgb = space_to_rgb_values("hsv", values)
        rgb["transparent"] = 1 if self._read_transparent_flag() else 0
        return rgb

    def _write_rgb_u32(self, r: int, g: int, b: int,
                       source_space: str | None = None,
                       source_values: Mapping[str, float] | None = None) -> bool:
        if self.pm is None or not self._resolve_rgb_target() or self.target is None:
            return False

        rgb = {
            "r": _clamp_byte(r),
            "g": _clamp_byte(g),
            "b": _clamp_byte(b),
        }
        try:
            # Main slot stores HSV as proportional u32s at +0x3C/+0x40/+0x44
            # (verified against companion: CSP itself updates exactly these
            # three u32s). Like the sub slot, the brush reads the main color
            # from MULTIPLE in-memory copies — writing the base slot alone
            # does not reach CSP. Locate every copy and write all of them.
            hsv = rgb_to_hsv_float(rgb)
            if hsv["s"] < 1.0:
                h_deg = self._last_hsv_h
            else:
                h_deg = hsv["h"]
                self._last_hsv_h = h_deg
            if hsv["v"] < 1.0:
                s_pct = self._last_hsv_s
            else:
                s_pct = hsv["s"]
                self._last_hsv_s = s_pct
            new = b"".join(
                u.to_bytes(4, "little")
                for u in encode_space_values_float(
                    "hsv", {"h": h_deg, "s": s_pct, "v": hsv["v"]}
                )
            )
            old = b"".join(
                self._read_u32(self.target + off).to_bytes(4, "little")
                for off in self._RGB_U32_OFFS
            )
            addrs = self._locate_hsv_copies(old, "_main_copy_addrs", self._RGB_U32_OFFS[0])
            for addr in addrs:
                self.pm.write_bytes(addr, new, 12)
            _log(f"set_color (rgb_u32): RGB=[{rgb['r']}, {rgb['g']}, {rgb['b']}] "
                 f"-> {len(addrs)} copies")
            return True
        except Exception as exc:
            _log(f"set_color (rgb_u32): exception: {exc}")
            return False

    def _snapshot_color_slot(self, base_addr: int) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        for space_name, offsets in self.space_offsets.items():
            raws = tuple(self._read_u32(base_addr + off) for off in offsets)
            snapshots[space_name] = {
                "offsets": offsets,
                "raws": raws,
                "values": decode_space_raws(space_name, raws),
            }
        return snapshots

    def _resolve_space_addresses(self) -> dict[str, tuple[int, ...]] | None:
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
    def get_color(self) -> dict[str, int] | None:
        if self.pm is None and not self.connect():
            return None

        if self.color_format == "rgb_u32":
            return self._read_rgb_u32()

        space_addrs = self._resolve_space_addresses()
        if not space_addrs:
            _log("get_color: target not ready")
            return None

        snapshots: dict[str, dict[str, Any]] = {}
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
        if source_space == "hsv":
            h_val = source_values.get("h", 0)
            s_val = source_values.get("s", 0)
            if h_val > 0 or s_val > 1: self._last_hsv_h = h_val
            if source_values.get("v", 0) > 1: self._last_hsv_s = s_val
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

    def set_color(self, r: int, g: int, b: int, source_space: str | None = None,
                  source_values: Mapping[str, float] | None = None,
                  transparent: bool = False, color_index: int = 0) -> bool:
        """Write color to CSP memory, optionally preserving native source-space precision.

        *source_space* / *source_values* (the space the user last interacted
        with and its float values) are written directly to that space's memory
        slot via :func:`encode_space_values_float` — no int rounding, no
        RGB → source round-trip loss.

        Other spaces are derived from the source (or from the passed RGB when
        no source is given) with standard int conversions.

        *transparent* sets the CSP 5.1 transparent flag (target+0x08); the
        u16x2_dup builds (4.x/5.x) have no verified flag offset, so the
        flag write is skipped there with a log line.

        *color_index* selects the slot: 0 = main color (u32 HSV channels at
        +0x3C, + transparent flag), 1 = sub color (u32 HSV channels at
        +0x9C; no transparent state in CSP's model).
        """
        if self.pm is None and not self.connect():
            return False

        if self.color_format == "rgb_u32":
            if transparent:
                # Transparent belongs to the ACTIVE slot: activate the
                # target slot first, then set the transparent flag.
                self._write_u32(self.target + self._TRANSPARENT_FLAG_OFFS,
                                1 if color_index == 1 else 0)
                return self._write_transparent_flag(True)
            if color_index == 1:
                # Sub slot: write HSV channels + activate the sub slot so
                # the brush (which paints the active slot) uses this color.
                ok_sub = self._write_sub_color(r, g, b)
                self._write_u32(self.target + self._TRANSPARENT_FLAG_OFFS, 1)
                return ok_sub
            if not self._write_transparent_flag(False):
                return False
            ok_main = self._write_rgb_u32(r, g, b, source_space=source_space,
                                          source_values=source_values)
            # Activate the main slot (clearing +0x08 low byte to 0).
            self._write_u32(self.target + self._TRANSPARENT_FLAG_OFFS, 0)
            return ok_main

        if transparent:
            _log("set_color: transparent unsupported for u16x2_dup builds — skipped")
            return False

        space_addrs = self._resolve_space_addresses()
        if not space_addrs:
            _log("set_color: target not ready")
            return False

        rgb = {"r": _clamp_byte(r), "g": _clamp_byte(g), "b": _clamp_byte(b)}
        try:
            # When a source space is provided, derive the RGB used for other
            # spaces from it using float precision (one-way, minimal loss).
            if source_space and source_values and source_space in SPACE_ORDER:
                source_rgb_float = space_to_rgb_float(source_space, source_values)
                source_rgb = {
                    "r": int(round(source_rgb_float["r"])),
                    "g": int(round(source_rgb_float["g"])),
                    "b": int(round(source_rgb_float["b"])),
                }
            else:
                source_rgb = rgb
                source_space = None

            hsv_vals: dict[str, Any] = rgb_to_space_values("hsv", source_rgb)
            if hsv_vals["s"] < 1:
                hsv_vals["h"] = self._last_hsv_h
            else:
                self._last_hsv_h = hsv_vals["h"]
            if hsv_vals["v"] < 1:
                hsv_vals["s"] = self._last_hsv_s
            else:
                self._last_hsv_s = hsv_vals["s"]

            for space_name in SPACE_ORDER:
                if source_space and space_name == source_space and source_values is not None:
                    # Source space: encode directly from float values — no
                    # RGB → source round-trip precision loss.
                    encoded = encode_space_values_float(space_name, source_values)
                elif space_name == "hsv":
                    encoded = encode_space_values(space_name, hsv_vals)
                else:
                    values = rgb_to_space_values(space_name, source_rgb)
                    encoded = encode_space_values(space_name, values)
                for addr, raw in zip(space_addrs[space_name], encoded):
                    self._write_u32(addr, raw)

            _log(
                "set_color: "
                f"RGB=[{source_rgb['r']}, {source_rgb['g']}, {source_rgb['b']}] "
                f"source={source_space or 'rgb'} "
                f"synced_spaces={list(SPACE_ORDER)}"
            )
            return True
        except Exception as exc:
            _log(f"set_color: exception: {exc}")
            return False

    # ----- introspection ---------------------------------------------------
    def status(self) -> dict[str, object]:
        if self.pm is None:
            self.connect()
        connected = False
        if self.pm is not None:
            if self.color_format == "rgb_u32":
                connected = self._resolve_rgb_target()
            else:
                space_addrs = self._resolve_space_addresses()
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

    def dump(self) -> dict[str, object]:
        """Diagnostic snapshot of the color slot for debugging.

        Walks the first 0x60 bytes of the slot in 4-byte steps, decoding
        each u32 both as a raw hex value and as the u16x2-duplicate form
        CSP historically uses, then attaches the structured per-space
        snapshots for human inspection.
        """
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        if self.color_format == "rgb_u32":
            if not self._resolve_rgb_target() or self.target is None:
                return {"error": "not connected"}
            rgb = self._read_rgb_u32()
            raw = [
                f"0x{self._read_u32(self.target + off):08X}"
                for off in self._RGB_U32_OFFS
            ]
            return {
                "target": f"0x{self.target:X}",
                "format": "rgb_u32",
                "raw_u32": raw,
                "rgb": rgb,
            }
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
def get_csp_theme() -> dict[str, str]:
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


def _theme_fallback(error: str | None = None) -> dict[str, str]:
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
