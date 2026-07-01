#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import configparser
import json
import math
import os
import struct
import sys
from typing import Dict, List, Optional, Tuple

from pymem import Pymem
from pymem.process import module_from_name

from memory_color_spaces import (
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


SECTION = "UDMPaint"
DEFAULT_VERSION = "udm4.0"

VERSION_CONFIGS = {
    "udm4.0": {
        "processname": "UDMPaintPRO.exe",
        "baseoffset": 0x04AE73B0,
    },
    "udm4.0-ex": {
        "processname": "UDMPaintEX.exe",
        "baseoffset": 0x04CD03B0,
    },
}


def normalize_version(version: str) -> str:
    v = str(version or "").strip().lower()
    if v in ("udm4.0-ex", "udm40ex", "udm4.0ex", "udm4-ex"):
        return "udm4.0-ex"
    return "udm4.0"


def resolve_config_path() -> str:
    env_path = os.environ.get("UDM_SYNC_CONFIG", "").strip()
    if env_path:
        return env_path

    # Packaged exe: read config next to the exe. Python mode: next to this script.
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    local_cfg = os.path.join(base_dir, "config.ini")
    if os.path.exists(local_cfg):
        return local_cfg

    return os.path.abspath("config.ini")


DEBUG = False

def log(msg: str) -> None:
    if DEBUG:
        print(f"[UDMSync] {msg}", file=sys.stderr, flush=True)


def parse_int(text: str) -> int:
    return int(str(text).strip(), 0)


def clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def u32_to_s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def decode_u16x2_dup(v: int) -> Optional[int]:
    lo = v & 0xFFFF
    hi = (v >> 16) & 0xFFFF
    if lo != hi:
        return None
    # Prefer 16-bit duplicated encoding: XXXX XXXX.
    return clamp8(round((lo / 65535.0) * 255.0))


def decode_u8x4_dup(v: int) -> Optional[int]:
    b0 = v & 0xFF
    b1 = (v >> 8) & 0xFF
    b2 = (v >> 16) & 0xFF
    b3 = (v >> 24) & 0xFF
    if b0 == b1 == b2 == b3:
        return b0
    return None


def decode_float(v: int, scale: str) -> Optional[int]:
    f = struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    if not math.isfinite(f):
        return None
    if scale == "unit":
        if -0.001 <= f <= 1.001:
            return clamp8(round(f * 255.0))
        return None
    if scale == "byte":
        if -0.5 <= f <= 255.5:
            return clamp8(round(f))
        return None
    return None


def encode_u16x2_dup(c: int) -> int:
    # 0..255 -> 0..65535, then duplicate low/high 16-bit.
    u16 = clamp8(c) * 257
    return ((u16 & 0xFFFF) << 16) | (u16 & 0xFFFF)


def encode_u8x4_dup(c: int) -> int:
    return clamp8(c) * 0x01010101


class UDMSync:
    def __init__(self) -> None:
        self.pm: Optional[Pymem] = None
        self.pid: Optional[int] = None
        self.module_base: Optional[int] = None
        self.target: Optional[int] = None
        self.current_version = DEFAULT_VERSION
        self.process_name: str = VERSION_CONFIGS[DEFAULT_VERSION]["processname"]
        self.base_offset: int = VERSION_CONFIGS[DEFAULT_VERSION]["baseoffset"]
        self.r_off = 0x20
        self.g_off = 0x24
        self.b_off = 0x28
        self.space_offsets = build_space_offsets(self.r_off)
        self.color_format = "double"
        self.use_abs = False
        self.abs_r = 0
        self.abs_g = 0
        self.abs_b = 0
        self.abs_mode = "auto"
        self.last_mode = None
        self.last_scale = "byte"
        self._apply_version_config(os.environ.get("UDM_SYNC_VERSION", DEFAULT_VERSION))
        self.load_config()

    def _apply_version_config(self, version: str) -> None:
        normalized = normalize_version(version)
        vc = VERSION_CONFIGS.get(normalized, VERSION_CONFIGS[DEFAULT_VERSION])
        self.current_version = normalized
        self.process_name = vc["processname"]
        self.base_offset = vc["baseoffset"]

    def load_config(self) -> None:
        config_file = resolve_config_path()
        cfg = configparser.ConfigParser()
        cfg.read(config_file, encoding="utf-8")
        if cfg.has_section(SECTION):
            sec = cfg[SECTION]
            # processname and baseoffset from config.ini only apply to the default version.
            # Non-default versions (e.g. udm4.0-ex) use VERSION_CONFIGS exclusively.
            if self.current_version == DEFAULT_VERSION:
                self.process_name = sec.get("processname", self.process_name)
                self.base_offset = parse_int(sec.get("baseoffset", hex(self.base_offset)))
            self.r_off = parse_int(sec.get("redoffset", hex(self.r_off)))
            self.g_off = parse_int(sec.get("greenoffset", hex(self.g_off)))
            self.b_off = parse_int(sec.get("blueoffset", hex(self.b_off)))
            self.color_format = sec.get("colorformat", self.color_format)
            self.use_abs = sec.getboolean("useabsolute", fallback=False)
            self.abs_r = parse_int(sec.get("absolutered", "0"))
            self.abs_g = parse_int(sec.get("absolutegreen", "0"))
            self.abs_b = parse_int(sec.get("absoluteblue", "0"))
            self.abs_mode = sec.get("absolutemode", "auto").strip().lower()
        self.space_offsets = build_space_offsets(self.r_off)

        log(
            "Config loaded: "
            f"Path={config_file} "
            f"Version={self.current_version} Process={self.process_name} "
            f"Base=0x{self.base_offset:X} "
            f"R=0x{self.r_off:X} G=0x{self.g_off:X} B=0x{self.b_off:X} "
            f"Format={self.color_format} UseAbs={self.use_abs} "
            f"AbsR=0x{self.abs_r:x} AbsG=0x{self.abs_g:x} AbsB=0x{self.abs_b:x} "
            f"AbsMode={self.abs_mode} Layout={self.space_offsets}"
        )

    def connect(self) -> bool:
        requested_version = self.current_version
        versions_to_try = [requested_version] + [v for v in VERSION_CONFIGS.keys() if v != requested_version]
        attempt_errors = []

        for version in versions_to_try:
            self._apply_version_config(version)
            try:
                self.pm = Pymem(self.process_name)
                self.pid = self.pm.process_id
                mod = module_from_name(self.pm.process_handle, self.process_name)
                self.module_base = mod.lpBaseOfDll
                ptr_addr = self.module_base + self.base_offset
                self.target = self.pm.read_longlong(ptr_addr)
                if version != requested_version:
                    log(f"Auto-detected version {version} from running process {self.process_name}")
                if self.use_abs:
                    log(
                        f"Connected to {self.process_name} (PID={self.pid}, Version={self.current_version}, "
                        f"BaseOffset=0x{self.base_offset:X}, Target=0x{self.target:X}, "
                        f"AbsR=0x{self.abs_r:X}, AbsG=0x{self.abs_g:X}, AbsB=0x{self.abs_b:X}, AbsMode={self.abs_mode})"
                    )
                else:
                    log(
                        f"Connected to {self.process_name} (PID={self.pid}, Version={self.current_version}, "
                        f"BaseOffset=0x{self.base_offset:X}, Target=0x{self.target:X}, "
                        f"R_off=0x{self.r_off:X}, G_off=0x{self.g_off:X}, B_off=0x{self.b_off:X})"
                    )
                return True
            except Exception as e:
                attempt_errors.append(f"{self.process_name}: {e}")
                if self.pm is not None:
                    try:
                        self.pm.close_process()
                    except Exception:
                        pass
                self.pm = None
                self.pid = None
                self.module_base = None
                self.target = None

        self._apply_version_config(requested_version)
        log(f"connect failed: {' | '.join(attempt_errors) if attempt_errors else 'unknown error'}")
        return False

    def set_version(self, version: str) -> bool:
        """Switch to a different UDM version. Returns True if version changed."""
        normalized = normalize_version(version)
        if normalized == self.current_version:
            return False
        self._apply_version_config(normalized)
        # Disconnect to force reconnect with new settings
        if self.pm is not None:
            try:
                self.pm.close_process()
            except Exception:
                pass
        self.pm = None
        self.pid = None
        self.module_base = None
        self.target = None
        log(f"Version changed to {normalized}, process={self.process_name}, base=0x{self.base_offset:X}")
        return True

    def _read_u32(self, addr: int) -> int:
        assert self.pm is not None
        return self.pm.read_int(addr) & 0xFFFFFFFF

    def _write_u32(self, addr: int, value: int) -> None:
        assert self.pm is not None
        self.pm.write_int(addr, u32_to_s32(value))

    def _read_f32(self, addr: int) -> float:
        assert self.pm is not None
        raw = self.pm.read_bytes(addr, 4)
        return struct.unpack("<f", raw)[0]

    def _write_f32(self, addr: int, value: float) -> None:
        assert self.pm is not None
        raw = struct.pack("<f", float(value))
        self.pm.write_bytes(addr, raw, 4)

    def _read_space_snapshots(self, base_addr: int) -> Dict[str, Dict[str, object]]:
        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, offsets in self.space_offsets.items():
            raws = tuple(self._read_u32(base_addr + off) for off in offsets)
            snapshots[space_name] = {
                "offsets": offsets,
                "raws": raws,
                "values": decode_space_raws(space_name, raws),
            }
        return snapshots

    def _space_addresses(self) -> Optional[Dict[str, Tuple[int, ...]]]:
        if self.pm is None:
            return None
        if self.use_abs:
            if self.abs_r and self.abs_g and self.abs_b:
                return build_space_addresses(self.abs_r)
            return None
        if self.module_base is None:
            return None
        try:
            new_target = self.pm.read_longlong(self.module_base + self.base_offset)
            if new_target:
                try:
                    snapshots = self._read_space_snapshots(new_target)
                    if any_space_has_nonzero_raws(snapshots) or self.target is None:
                        self.target = new_target
                except Exception:
                    pass
        except Exception:
            pass
        if self.target is None:
            return None
        try:
            self._read_space_snapshots(self.target)
        except Exception as e:
            log(f"_space_addresses: unreadable target 0x{self.target:X}: {e}")
            self.target = None
            return None
        return {name: tuple(self.target + off for off in offsets) for name, offsets in self.space_offsets.items()}

    def _rgb_addresses(self) -> Optional[Tuple[int, int, int]]:
        if self.pm is None:
            return None
        if self.use_abs:
            if self.abs_r and self.abs_g and self.abs_b:
                return self.abs_r, self.abs_g, self.abs_b
            return None
        if self.module_base is None:
            return None
        # Re-read the pointer each call to follow UDM updating its struct address.
        # Only adopt the new target when it is non-null AND carries plausible color
        # data (at least one channel non-zero).  This prevents the color wheel from
        # hijacking self.target with its transient zero-initialised buffer: the wheel
        # widget briefly becomes the pointed-to object with all channels = 0, causing
        # the plugin to read black and write to dead memory.  Keeping the last
        # known-good address lets reads/writes continue on the real brush slot until
        # the slider or eyedropper commits the wheel's selection.
        try:
            new_target = self.pm.read_longlong(self.module_base + self.base_offset)
            if new_target:
                try:
                    vr = self.pm.read_int(new_target + self.r_off) & 0xFFFFFFFF
                    vg = self.pm.read_int(new_target + self.g_off) & 0xFFFFFFFF
                    vb = self.pm.read_int(new_target + self.b_off) & 0xFFFFFFFF
                    if (vr | vg | vb) or self.target is None:
                        self.target = new_target
                except Exception:
                    pass  # can't peek at new target — keep old
        except Exception:
            pass  # can't read pointer — keep old
        if self.target is None:
            return None
        return self.target + self.r_off, self.target + self.g_off, self.target + self.b_off

    def _detect_mode(self, raws: List[int]) -> Tuple[str, str, List[int]]:
        u16_vals = [decode_u16x2_dup(v) for v in raws]
        if all(v is not None for v in u16_vals):
            return "u16x2_dup", "byte", [int(v) for v in u16_vals]

        u8_vals = [decode_u8x4_dup(v) for v in raws]
        if all(v is not None for v in u8_vals):
            return "u8x4_dup", "byte", [int(v) for v in u8_vals]

        fu_vals = [decode_float(v, "unit") for v in raws]
        if all(v is not None for v in fu_vals):
            return "float32", "unit", [int(v) for v in fu_vals]

        fb_vals = [decode_float(v, "byte") for v in raws]
        if all(v is not None for v in fb_vals):
            return "float32", "byte", [int(v) for v in fb_vals]

        return "unknown", "byte", [0, 0, 0]

    def get_color(self) -> Optional[Dict[str, int]]:
        if self.pm is None and not self.connect():
            return None
        space_addrs = self._space_addresses()
        if not space_addrs:
            log("get_color: no valid offsets found")
            return None

        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, addrs in space_addrs.items():
            raws = tuple(self._read_u32(addr) for addr in addrs)
            if self.target is not None and not self.use_abs:
                offsets = tuple(addr - self.target for addr in addrs)
            else:
                offsets = addrs
            snapshots[space_name] = {
                "offsets": offsets,
                "raws": raws,
                "values": decode_space_raws(space_name, raws),
            }

        source_space, rgb, source_values = resolve_active_rgb(snapshots)
        source_raws = snapshots[source_space]["raws"]
        log(
            "get_color: "
            f"source={source_space} "
            f"offsets={snapshots[source_space]['offsets']} "
            f"raw={[f'0x{raw:08X}' for raw in source_raws]} "
            f"values={format_space_values(source_space, source_values)} "
            f"-> RGB=[{rgb['r']}, {rgb['g']}, {rgb['b']}]"
        )
        return rgb

    def _mode_for_write(self) -> Tuple[str, str]:
        forced = self.abs_mode if self.use_abs else "auto"
        if forced in ("u16x2_dup", "u8x4_dup", "float32_unit", "float32_byte"):
            if forced == "float32_unit":
                return "float32", "unit"
            if forced == "float32_byte":
                return "float32", "byte"
            return forced, "byte"

        if self.last_mode in ("u16x2_dup", "u8x4_dup", "float32"):
            return self.last_mode, self.last_scale

        color = self.get_color()
        if color:
            return self.last_mode or "u16x2_dup", self.last_scale
        return "u16x2_dup", "byte"

    def set_color(self, r: int, g: int, b: int) -> bool:
        if self.pm is None and not self.connect():
            return False

        space_addrs = self._space_addresses()
        if not space_addrs:
            log("set_color: offsets/scale not determined")
            return False

        rgb = {"r": clamp8(r), "g": clamp8(g), "b": clamp8(b)}

        try:
            for space_name in SPACE_ORDER:
                encoded_values = encode_space_values(space_name, rgb_to_space_values(space_name, rgb))
                for addr, raw in zip(space_addrs[space_name], encoded_values):
                    self._write_u32(addr, raw)
            log(
                "set_color: "
                f"RGB=[{rgb['r']}, {rgb['g']}, {rgb['b']}] "
                f"synced_spaces={list(SPACE_ORDER)}"
            )
            return True
        except Exception as e:
            log(f"set_color: exception: {e}")
            return False

    def status(self) -> Dict[str, object]:
        if self.pm is None:
            self.connect()
        space_addrs = self._space_addresses() if self.pm is not None else None
        ok = self.pm is not None and self.target is not None and space_addrs is not None
        return {
            "connected": bool(ok),
            "pid": self.pid if ok else None,
            "baseOffset": f"0x{self.base_offset:X}",
            "target": f"0x{self.target:X}" if ok and self.target is not None else None,
            "version": self.current_version,
            "processName": self.process_name,
        }

    def dump(self) -> Dict[str, object]:
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        if self._space_addresses() is None or self.target is None:
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
            samples.append(
                {
                    "offset": hex(off),
                    "f32_lo": lo,
                    "f32_hi": hi,
                    "f64": f64,
                    "hex": f"0x{u64:016X}",
                }
            )
        snapshots = self._read_space_snapshots(self.target)
        spaces = []
        for space_name in SPACE_ORDER:
            snapshot = snapshots[space_name]
            spaces.append(
                {
                    "space": space_name,
                    "offsets": [hex(off) for off in snapshot["offsets"]],
                    "raw_hex": [f"0x{raw:08X}" for raw in snapshot["raws"]],
                    "values": snapshot["values"],
                    "asText": format_space_values(space_name, snapshot["values"]),
                }
            )
        return {"target": f"0x{self.target:X}", "spaces": spaces, "samples": samples}

    def poke_abs(self, addr: int, value: int) -> bool:
        if self.pm is None and not self.connect():
            return False
        try:
            self._write_u32(addr, value)
            return True
        except Exception as e:
            log(f"poke_abs: {e}")
            return False

    def poke_trio(self, addr: int, vr: int, vg: int, vb: int) -> bool:
        if self.pm is None and not self.connect():
            return False
        try:
            self._write_u32(addr + 0, vr)
            self._write_u32(addr + 4, vg)
            self._write_u32(addr + 8, vb)
            return True
        except Exception as e:
            log(f"poke_trio: {e}")
            return False

    def find_rgb(self, r: int, g: int, b: int, scan_size: int = 0x800, max_results: int = 10) -> Dict[str, object]:
        if self.pm is None and not self.connect():
            return {"error": "not connected"}
        assert self.pm is not None
        assert self.target is not None

        expected = [clamp8(r), clamp8(g), clamp8(b)]
        cands = []
        for off in range(0, max(0, scan_size - 8), 4):
            addrs = [self.target + off, self.target + off + 4, self.target + off + 8]
            raws = [self._read_u32(addrs[0]), self._read_u32(addrs[1]), self._read_u32(addrs[2])]
            for mode_name, scale_name, rgb in (
                self._detect_mode(raws),
            ):
                if mode_name == "unknown":
                    continue
                dist = abs(rgb[0] - expected[0]) + abs(rgb[1] - expected[1]) + abs(rgb[2] - expected[2])
                penalty = {"u16x2_dup": 0, "u8x4_dup": 5, "float32": 10}.get(mode_name, 30)
                cands.append(
                    {
                        "score": dist + penalty,
                        "dist": dist,
                        "mode": mode_name,
                        "scale": scale_name,
                        "offsets": [off, off + 4, off + 8],
                        "addresses": addrs,
                        "rgb": rgb,
                        "raw_hex": [f"0x{raws[0]:08X}", f"0x{raws[1]:08X}", f"0x{raws[2]:08X}"],
                    }
                )
        cands.sort(key=lambda x: (x["score"], x["dist"]))
        return {
            "target": f"0x{self.target:X}",
            "expected": {"r": expected[0], "g": expected[1], "b": expected[2]},
            "count": len(cands),
            "candidates": cands[: max(1, max_results)],
        }



