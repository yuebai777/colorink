#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""UDM Paint active brush-color synchronization.

Attaches to a running UDMPaintPRO.exe (or UDMPaintEX.exe) process and
mirrors the in-memory brush color slot, translating between the host's
packed u32-per-channel encoding and regular RGB triples.

Two address-resolution modes are supported:

* **pointer mode** (default) — chase a build-specific anchor pointer from
  the loaded module base to the live color slot, with re-validation on
  every access so a transient zero-initialized buffer (e.g. while the
  host's color wheel is mid-drag) cannot hijack the resolved target.
* **absolute mode** — bypass the pointer chase entirely and read/write
  fixed addresses supplied by the user, useful when reverse engineering
  a new build or working with a non-standard installation.
"""

from __future__ import annotations

import configparser
import math
import os
import struct
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pymem import Pymem
from pymem.process import module_from_name

from brush_color_spaces import (
    SPACE_ORDER,
    any_space_has_nonzero_raws,
    build_space_addresses,
    build_space_offsets,
    decode_space_raws,
    encode_space_values,
    format_space_values,
    resolve_active_rgb,
    rgb_to_space_values,
)

# ---------------------------------------------------------------------------
# Build-specific technical constants (objective facts from UDMPaint binaries)
# ---------------------------------------------------------------------------
SECTION_NAME        = "UDMPaint"
DEFAULT_VERSION_KEY = "udm4.0"

# Default per-channel offsets inside the UDM color struct.  The same
# layout applies to both UDMPaintPRO.exe and UDMPaintEX.exe builds.
_DEFAULT_RED_OFFSET   = 0x20
_DEFAULT_GREEN_OFFSET = 0x24
_DEFAULT_BLUE_OFFSET  = 0x28


# ---------------------------------------------------------------------------
# Per-build profile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _UDMBuildProfile:
    """Static configuration that distinguishes one UDM build from another.

    The only varying fields between PRO and EX are the on-disk image name
    and the anchor pointer's offset from the module base; the color-slot
    layout itself is shared.
    """

    key: str
    process_name: str
    base_offset: int


_PROFILES: Tuple[_UDMBuildProfile, ...] = (
    _UDMBuildProfile("udm4.0",    "UDMPaintPRO.exe", 0x04AE73B0),
    _UDMBuildProfile("udm4.0-ex", "UDMPaintEX.exe",  0x04CD03B0),
)
_PROFILE_INDEX: Dict[str, _UDMBuildProfile] = {p.key: p for p in _PROFILES}


def _normalize_version_key(raw: object) -> str:
    """Coerce arbitrary user input into one of the known profile keys."""
    text = str(raw or "").strip().lower()
    if text in ("udm4.0-ex", "udm40ex", "udm4.0ex", "udm4-ex"):
        return "udm4.0-ex"
    return "udm4.0"


# ---------------------------------------------------------------------------
# Logging + paths
# ---------------------------------------------------------------------------
_DEBUG = False


def _log(message: str) -> None:
    if _DEBUG:
        print(f"[UDMSync] {message}", file=sys.stderr, flush=True)


def _parse_int(text: str) -> int:
    return int(str(text).strip(), 0)


def _resolve_config_file() -> str:
    """Pick the config.ini to read user overrides from.

    Search order: $UDM_SYNC_CONFIG env var, then the file next to the
    frozen exe (PyInstaller bundle), then the file next to this script,
    then a config.ini in the current working directory.
    """
    env_path = os.environ.get("UDM_SYNC_CONFIG", "").strip()
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
# u32 codec helpers (used by the inspector methods)
# ---------------------------------------------------------------------------
def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def _u32_to_signed(value: int) -> int:
    """Convert an unsigned 32-bit value to its signed two's-complement form."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def _decode_u16x2_duplicate(raw: int) -> Optional[int]:
    """Decode a u32 that stores an 8-bit channel as two copies of a 16-bit value.

    UDM pads an 8-bit channel into a 32-bit slot by writing the same 16-bit
    pattern into both the low and high halves, so ``low == high`` is the
    validity check; anything else means the slot wasn't written in this form.
    """
    low  = raw & 0xFFFF
    high = (raw >> 16) & 0xFFFF
    if low != high:
        return None
    return _clamp_byte(round((low / 65535.0) * 255.0))


def _decode_u8x4_duplicate(raw: int) -> Optional[int]:
    """Decode a u32 that stores an 8-bit channel as four copies of one byte.

    All four bytes must agree; otherwise the encoding isn't this form.
    """
    b0 = raw & 0xFF
    b1 = (raw >> 8) & 0xFF
    b2 = (raw >> 16) & 0xFF
    b3 = (raw >> 24) & 0xFF
    if b0 == b1 == b2 == b3:
        return b0
    return None


def _decode_float32(raw: int, scale: str) -> Optional[int]:
    """Interpret a u32 bit pattern as a single-precision float and scale to 0..255.

    ``scale="unit"`` accepts values in roughly ``[0, 1]`` (with a small
    slop for floating-point noise) and multiplies by 255; ``scale="byte"``
    accepts values in roughly ``[0, 255]`` and rounds.  Anything outside
    the expected range or NaN/Inf yields ``None`` so the caller can fall
    through to the next encoding form.
    """
    f = struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    if not math.isfinite(f):
        return None
    if scale == "unit":
        if -0.001 <= f <= 1.001:
            return _clamp_byte(round(f * 255.0))
        return None
    if scale == "byte":
        if -0.5 <= f <= 255.5:
            return _clamp_byte(round(f))
        return None
    return None


def _detect_encoding(raws: List[int]) -> Tuple[str, str, List[int]]:
    """Best-effort guess at how a triple of u32 raws encodes an RGB triple."""
    u16_attempt = [_decode_u16x2_duplicate(v) for v in raws]
    if all(v is not None for v in u16_attempt):
        return "u16x2_dup", "byte", [int(v) for v in u16_attempt]

    u8_attempt = [_decode_u8x4_duplicate(v) for v in raws]
    if all(v is not None for v in u8_attempt):
        return "u8x4_dup", "byte", [int(v) for v in u8_attempt]

    f_unit = [_decode_float32(v, "unit") for v in raws]
    if all(v is not None for v in f_unit):
        return "float32", "unit", [int(v) for v in f_unit]

    f_byte = [_decode_float32(v, "byte") for v in raws]
    if all(v is not None for v in f_byte):
        return "float32", "byte", [int(v) for v in f_byte]

    return "unknown", "byte", [0, 0, 0]


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------
class UDMSync:
    """Memory-sync backend for UDM Paint's active brush color.

    All persistent state lives on the instance; opening a process and
    closing it again is wholly contained inside :meth:`connect` and
    :meth:`_drop_connection` so a failing version-probe can't leave a
    dangling handle.  When absolute-address mode is enabled (``use_abs``),
    the underlying profile's process_name and base_offset are bypassed
    in favor of user-supplied absolute addresses for each color channel.
    """

    def __init__(self) -> None:
        # Live process attachment — None when not connected.
        self.pm: Optional[Pymem] = None
        self.pid: Optional[int] = None
        self.module_base: Optional[int] = None
        self.target: Optional[int] = None

        # Currently selected build profile.
        self._profile: _UDMBuildProfile = _PROFILE_INDEX[DEFAULT_VERSION_KEY]
        self.current_version: str = self._profile.key
        self.process_name: str = self._profile.process_name
        self.base_offset: int = self._profile.base_offset

        # Per-channel layout (configurable via config.ini).
        self.r_off: int = _DEFAULT_RED_OFFSET
        self.g_off: int = _DEFAULT_GREEN_OFFSET
        self.b_off: int = _DEFAULT_BLUE_OFFSET
        self.space_offsets = build_space_offsets(self.r_off)

        # Absolute-address mode overrides.  In use_abs mode the user
        # supplies fixed addresses for each channel and we bypass the
        # pointer chase entirely; abs_mode is informational logging only
        # (it would control per-channel encoding negotiation, but the
        # current sync path always writes via the proportional encoded
        # u32 form regardless of detected mode).
        self.use_abs: bool = False
        self.abs_r: int = 0
        self.abs_g: int = 0
        self.abs_b: int = 0
        self.abs_mode: str = "auto"

        self._apply_profile(_normalize_version_key(os.environ.get("UDM_SYNC_VERSION", DEFAULT_VERSION_KEY)))
        self._load_user_config()

    # ----- profile management ---------------------------------------------
    def _apply_profile(self, key: str) -> None:
        profile = _PROFILE_INDEX.get(key, _PROFILE_INDEX[DEFAULT_VERSION_KEY])
        self._profile = profile
        self.current_version = profile.key
        self.process_name = profile.process_name
        self.base_offset = profile.base_offset

    def _load_user_config(self) -> None:
        path = _resolve_config_file()
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if not parser.has_section(SECTION_NAME):
            return
        sec = parser[SECTION_NAME]
        # processname and baseoffset from config.ini only apply to the
        # default (udm4.0) profile; non-default profiles are pinned by
        # _PROFILES so user typos can't desync a 4.0-ex install.
        if self.current_version == DEFAULT_VERSION_KEY:
            self.process_name = sec.get("processname", self.process_name)
            self.base_offset = _parse_int(sec.get("baseoffset", hex(self.base_offset)))
        self.r_off = _parse_int(sec.get("redoffset",   hex(self.r_off)))
        self.g_off = _parse_int(sec.get("greenoffset", hex(self.g_off)))
        self.b_off = _parse_int(sec.get("blueoffset",  hex(self.b_off)))
        self.use_abs = sec.getboolean("useabsolute", fallback=False)
        self.abs_r = _parse_int(sec.get("absolutered",   "0"))
        self.abs_g = _parse_int(sec.get("absolutegreen", "0"))
        self.abs_b = _parse_int(sec.get("absoluteblue",  "0"))
        self.abs_mode = sec.get("absolutemode", "auto").strip().lower()
        self.space_offsets = build_space_offsets(self.r_off)
        _log(
            "Config loaded: "
            f"Path={path} Version={self.current_version} Process={self.process_name} "
            f"Base=0x{self.base_offset:X} R=0x{self.r_off:X} G=0x{self.g_off:X} B=0x{self.b_off:X} "
            f"UseAbs={self.use_abs} AbsR=0x{self.abs_r:X} AbsG=0x{self.abs_g:X} AbsB=0x{self.abs_b:X} "
            f"AbsMode={self.abs_mode} Layout={self.space_offsets}"
        )

    def set_version(self, key: str) -> bool:
        """Switch to a different UDM build profile. Returns True if it changed."""
        normalized = _normalize_version_key(key)
        if normalized == self.current_version:
            return False
        self._apply_profile(normalized)
        # Force reconnect on next access.
        self._drop_connection()
        _log(f"Version switched to {normalized}, process={self.process_name}, base=0x{self.base_offset:X}")
        return True

    # ----- connection management -----------------------------------------
    def connect(self) -> bool:
        """Attach to a running UDM process, auto-falling-back across builds.

        Tries the currently-selected profile first. If its on-disk process
        name doesn't match what's actually running, walks the remaining
        profiles until one attaches successfully — that profile becomes the
        active one (auto-detection). Restores the originally requested
        profile if every attempt fails.
        """
        requested = self._profile
        candidates = [requested] + [p for p in _PROFILES if p is not requested]
        errors = []

        for candidate in candidates:
            self._apply_profile(candidate.key)
            opened = self._try_open_with(candidate)
            if opened:
                if candidate is not requested:
                    _log(f"Auto-detected version {candidate.key} from process {candidate.process_name}")
                self._log_connect_status()
                return True
            errors.append(f"{candidate.process_name}: connection refused")

        # All attempts failed; restore the originally requested profile.
        self._apply_profile(requested.key)
        _log(f"connect failed: {' | '.join(errors) if errors else 'unknown error'}")
        return False

    def _try_open_with(self, profile: _UDMBuildProfile) -> bool:
        """Attempt a single connection against ``profile``."""
        try:
            self.pm = Pymem(profile.process_name)
            self.pid = self.pm.process_id
            mod = module_from_name(self.pm.process_handle, profile.process_name)
            self.module_base = mod.lpBaseOfDll
            ptr_addr = self.module_base + profile.base_offset
            self.target = self.pm.read_longlong(ptr_addr)
            return True
        except Exception as exc:
            if self.pm is not None:
                try:
                    self.pm.close_process()
                except Exception:
                    pass
            self.pm = None
            self.pid = None
            self.module_base = None
            self.target = None
            return False

    def _log_connect_status(self) -> None:
        if self.use_abs:
            _log(
                f"Connected to {self.process_name}: "
                f"PID={self.pid} Version={self.current_version} "
                f"BaseOffset=0x{self.base_offset:X} Target=0x{self.target:X} "
                f"AbsR=0x{self.abs_r:X} AbsG=0x{self.abs_g:X} AbsB=0x{self.abs_b:X} "
                f"AbsMode={self.abs_mode}"
            )
        else:
            _log(
                f"Connected to {self.process_name}: "
                f"PID={self.pid} Version={self.current_version} "
                f"BaseOffset=0x{self.base_offset:X} Target=0x{self.target:X} "
                f"R_off=0x{self.r_off:X} G_off=0x{self.g_off:X} B_off=0x{self.b_off:X}"
            )

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

    # ----- memory accessors ----------------------------------------------
    def _read_u32(self, address: int) -> int:
        assert self.pm is not None
        return self.pm.read_int(address) & 0xFFFFFFFF

    def _write_u32(self, address: int, value: int) -> None:
        assert self.pm is not None
        self.pm.write_int(address, _u32_to_signed(value))

    def _read_float32(self, address: int) -> float:
        assert self.pm is not None
        raw = self.pm.read_bytes(address, 4)
        return struct.unpack("<f", raw)[0]

    def _write_float32(self, address: int, value: float) -> None:
        assert self.pm is not None
        raw = struct.pack("<f", float(value))
        self.pm.write_bytes(address, raw, 4)

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
        """Re-resolve the color slot pointer (or honor absolute-address mode).

        Pointer mode re-reads the anchor pointer on every call and only
        adopts a new target when it points at plausible data, so a
        transient zero-initialized buffer (e.g. while UDM's color wheel
        is mid-drag) can't hijack the slot pointer.  Absolute-address
        mode bypasses the pointer chase entirely and returns whatever
        addresses the user configured.
        """
        if self.pm is None:
            return None

        if self.use_abs:
            if self.abs_r and self.abs_g and self.abs_b:
                return build_space_addresses(self.abs_r)
            return None

        if self.module_base is None:
            return None

        try:
            fresh_pointer = self.pm.read_longlong(self.module_base + self.base_offset)
            if fresh_pointer:
                try:
                    probe = self._snapshot_color_slot(fresh_pointer)
                    if any_space_has_nonzero_raws(probe) or self.target is None:
                        self.target = fresh_pointer
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
            _log("get_color: no usable addresses resolved")
            return None

        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, addresses in space_addrs.items():
            raws = tuple(self._read_u32(addr) for addr in addresses)
            if self.target is not None and not self.use_abs:
                offsets = tuple(addr - self.target for addr in addresses)
            else:
                offsets = addresses
            snapshots[space_name] = {
                "offsets": offsets,
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
            _log("set_color: no usable addresses resolved")
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

    # ----- introspection --------------------------------------------------
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
            "version": self.current_version,
            "processName": self.process_name,
        }

    def dump(self) -> Dict[str, object]:
        """Diagnostic snapshot of the color slot for debugging.

        Walks the first 0x60 bytes of the slot in 8-byte strides,
        interpreting each chunk as a pair of f32 values plus a full f64
        and a hex u64 — useful when reverse engineering a new UDM build
        to identify which encoding the slot is currently using.
        """
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        if self._resolve_space_addresses() is None or self.target is None:
            return {"error": "not connected"}
        assert self.pm is not None
        assert self.target is not None

        samples = []
        for off in range(0, 0x60, 0x8):
            addr = self.target + off
            raw = self.pm.read_bytes(addr, 8)
            lo = struct.unpack("<f", raw[:4])[0]
            hi = struct.unpack("<f", raw[4:])[0]
            f64 = struct.unpack("<d", raw)[0]
            u64 = struct.unpack("<Q", raw)[0]
            samples.append({
                "offset": hex(off),
                "f32_lo": lo,
                "f32_hi": hi,
                "f64":    f64,
                "hex":    f"0x{u64:016X}",
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
        return {"target": f"0x{self.target:X}", "spaces": spaces, "samples": samples}

    # ----- debugging probes ----------------------------------------------
    def poke_abs(self, addr: int, value: int) -> bool:
        """Write a single u32 to an absolute address (testing helper)."""
        if self.pm is None and not self.connect():
            return False
        try:
            self._write_u32(addr, value)
            return True
        except Exception as exc:
            _log(f"poke_abs: {exc}")
            return False

    def poke_trio(self, addr: int, vr: int, vg: int, vb: int) -> bool:
        """Write three consecutive u32s starting at ``addr`` (testing helper)."""
        if self.pm is None and not self.connect():
            return False
        try:
            self._write_u32(addr + 0, vr)
            self._write_u32(addr + 4, vg)
            self._write_u32(addr + 8, vb)
            return True
        except Exception as exc:
            _log(f"poke_trio: {exc}")
            return False

    def find_rgb(
        self,
        r: int,
        g: int,
        b: int,
        scan_size: int = 0x800,
        max_results: int = 10,
    ) -> Dict[str, object]:
        """Scan the first ``scan_size`` bytes of the slot for trio offsets
        that decode to a color near the requested one.

        Each 4-byte-aligned trio is decoded with :func:`_detect_encoding`;
        the small per-mode penalty biases toward the more common encodings
        so exact matches in rarer forms don't drown out matches in the
        form the host actually uses.
        """
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        if self._resolve_space_addresses() is None or self.target is None:
            return {"error": "not connected"}
        assert self.pm is not None
        assert self.target is not None

        expected = [_clamp_byte(r), _clamp_byte(g), _clamp_byte(b)]
        candidates = []
        for off in range(0, max(0, scan_size - 8), 4):
            addrs = [self.target + off, self.target + off + 4, self.target + off + 8]
            raws = [self._read_u32(addrs[0]), self._read_u32(addrs[1]), self._read_u32(addrs[2])]
            mode_name, scale_name, recovered = _detect_encoding(raws)
            if mode_name == "unknown":
                continue
            distance = (
                abs(recovered[0] - expected[0])
                + abs(recovered[1] - expected[1])
                + abs(recovered[2] - expected[2])
            )
            penalty = {"u16x2_dup": 0, "u8x4_dup": 5, "float32": 10}.get(mode_name, 30)
            candidates.append({
                "score":     distance + penalty,
                "dist":      distance,
                "mode":      mode_name,
                "scale":     scale_name,
                "offsets":   [off, off + 4, off + 8],
                "addresses": addrs,
                "rgb":       recovered,
                "raw_hex":   [f"0x{raws[0]:08X}", f"0x{raws[1]:08X}", f"0x{raws[2]:08X}"],
            })
        candidates.sort(key=lambda x: (x["score"], x["dist"]))
        return {
            "target":      f"0x{self.target:X}",
            "expected":    {"r": expected[0], "g": expected[1], "b": expected[2]},
            "count":       len(candidates),
            "candidates":  candidates[: max(1, max_results)],
        }