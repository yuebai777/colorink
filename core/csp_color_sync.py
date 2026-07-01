#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import configparser
import ctypes
import json
import os
import sys
from typing import Dict, Optional, Tuple
from ctypes import wintypes

try:
    from pymem import Pymem
    from pymem.process import module_from_name
except ImportError:
    Pymem = None
    module_from_name = None

try:
    from memory_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
        build_space_offsets,
        decode_space_raws,
        encode_space_values,
        format_space_values,
        resolve_active_rgb,
        rgb_to_space_values,
    )
except ImportError:
    SPACE_ORDER = None


SECTION = "ClipStudioPaint"
DEFAULT_VERSION = "csp4.0"
CSP4_AOB_SIGNATURE = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18 48 83 C1 48 E8 ?? ?? ?? ?? 48 8B C3"
CSP427EX_AOB_SIGNATURE = "41 0F 10 ?? 1C 41 0F 11 ?? 1C F2 41 0F 10 ?? 10 F2 41 0F 11 ?? 10 41 8B ?? 18 41 89 ?? 18"
CSP50_AOB_SIGNATURE = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18"

VERSION_CONFIGS = {
    "csp4.0": {
        "processname": "CLIPStudioPaint.exe",
        "baseoffset": 0x0518C2C0,
        "aobsignature": CSP4_AOB_SIGNATURE,
    },
    "csp4.2.7-ex": {
        "processname": "CLIPStudioPaint.exe",
        "baseoffset": 0x0518C2C0,
        "aobsignature": CSP427EX_AOB_SIGNATURE,
    },
    "csp5.0": {
        "processname": "CLIPStudioPaint.exe",
        "baseoffset": 0x05449DB0,
        "aobsignature": CSP50_AOB_SIGNATURE,
        "aob_offset": 0x0D,
    },
    "csp5.0-ex": {
        "processname": "CLIPStudioPaint.exe",
        "baseoffset": 0x05449DB0,
        "aobsignature": CSP50_AOB_SIGNATURE,
        "aob_offset": 0x0D,
    },
}


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_version = ctypes.WinDLL("version", use_last_error=True)
_query_full_process_image_name = _kernel32.QueryFullProcessImageNameW
_query_full_process_image_name.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_query_full_process_image_name.restype = wintypes.BOOL
_get_file_version_info_size = _version.GetFileVersionInfoSizeW
_get_file_version_info_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_get_file_version_info_size.restype = wintypes.DWORD
_get_file_version_info = _version.GetFileVersionInfoW
_get_file_version_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
_get_file_version_info.restype = wintypes.BOOL
_ver_query_value = _version.VerQueryValueW
_ver_query_value.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
_ver_query_value.restype = wintypes.BOOL


def normalize_version(version: str) -> str:
    v = str(version or "").strip().lower()
    if "4.2.7" in v or "427" in v:
        return "csp4.2.7-ex"
    if "5.0" in v or "csp5" in v:
        if "ex" in v:
            return "csp5.0-ex"
        return "csp5.0"
    return "csp4.0"


def resolve_config_path() -> str:
    env_path = os.environ.get("CSP_SYNC_CONFIG", "").strip()
    if env_path:
        return env_path

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
        print(f"[CSPSync] {msg}", file=sys.stderr, flush=True)


def parse_int(text: str) -> int:
    return int(str(text).strip(), 0)


def clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def u32_to_s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def encode_u16x2_dup(c: int) -> int:
    u16 = clamp8(c) * 257  # 0x00 -> 0x0000, 0xFF -> 0xFFFF
    return ((u16 & 0xFFFF) << 16) | (u16 & 0xFFFF)


def decode_u16x2_dup(v: int) -> Optional[int]:
    lo = v & 0xFFFF
    hi = (v >> 16) & 0xFFFF
    if lo != hi:
        return None
    return clamp8((lo + 128) // 257)


def query_process_image_path(process_handle) -> Optional[str]:
    size = wintypes.DWORD(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    if not _query_full_process_image_name(wintypes.HANDLE(int(process_handle)), 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def query_file_version(path: str) -> Optional[Tuple[int, int, int, int]]:
    dummy = wintypes.DWORD(0)
    size = _get_file_version_info_size(path, ctypes.byref(dummy))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not _get_file_version_info(path, 0, size, data):
        return None
    value_ptr = ctypes.c_void_p()
    value_len = wintypes.UINT(0)
    if not _ver_query_value(data, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
        return None
    ffi = ctypes.cast(value_ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    return (
        (ffi.dwFileVersionMS >> 16) & 0xFFFF,
        ffi.dwFileVersionMS & 0xFFFF,
        (ffi.dwFileVersionLS >> 16) & 0xFFFF,
        ffi.dwFileVersionLS & 0xFFFF,
    )


def detect_csp_version_from_process_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    version = query_file_version(path)
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


class CSPSync:
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
        self.color_format = "u16x2_dup"
        self.intermediate_offset: Optional[int] = VERSION_CONFIGS[DEFAULT_VERSION].get("intermediate_offset")
        self.aob_signature: str = VERSION_CONFIGS[DEFAULT_VERSION].get("aobsignature", CSP4_AOB_SIGNATURE)
        self._apply_version_config(os.environ.get("CSP_SYNC_VERSION", DEFAULT_VERSION))
        self.load_config()

    def _apply_version_config(self, version: str) -> None:
        normalized = normalize_version(version)
        vc = VERSION_CONFIGS.get(normalized, VERSION_CONFIGS[DEFAULT_VERSION])
        self.current_version = normalized
        self.process_name = vc["processname"]
        self.base_offset = vc["baseoffset"]
        self.intermediate_offset = vc.get("intermediate_offset")
        self.aob_signature = vc.get("aobsignature", CSP4_AOB_SIGNATURE)

    def load_config(self) -> None:
        config_file = resolve_config_path()
        cfg = configparser.ConfigParser()
        cfg.read(config_file, encoding="utf-8")
        if cfg.has_section(SECTION):
            sec = cfg[SECTION]
            # processname and baseoffset from config.ini only apply to the default version.
            # Non-default versions use VERSION_CONFIGS exclusively.
            if self.current_version == DEFAULT_VERSION:
                self.process_name = sec.get("processname", self.process_name)
                self.base_offset = parse_int(sec.get("baseoffset", hex(self.base_offset)))
                self.aob_signature = sec.get("aobsignature", self.aob_signature)
            self.r_off = parse_int(sec.get("redoffset", hex(self.r_off)))
            self.g_off = parse_int(sec.get("greenoffset", hex(self.g_off)))
            self.b_off = parse_int(sec.get("blueoffset", hex(self.b_off)))
            self.color_format = sec.get("colorformat", self.color_format)
        self.space_offsets = build_space_offsets(self.r_off)

        log(
            "Config loaded: "
            f"Path={config_file} "
            f"Version={self.current_version} Process={self.process_name} "
            f"Base=0x{self.base_offset:X} "
            f"R=0x{self.r_off:X} G=0x{self.g_off:X} B=0x{self.b_off:X} "
            f"Format={self.color_format} "
            f"Layout={self.space_offsets}"
        )

    def connect(self) -> bool:
        try:
            self.pm = Pymem(self.process_name)
            self.pid = self.pm.process_id
            mod = module_from_name(self.pm.process_handle, self.process_name)
            self.module_base = mod.lpBaseOfDll
            image_path = query_process_image_path(self.pm.process_handle)
            detected_version = detect_csp_version_from_process_path(image_path)
            if detected_version and detected_version != self.current_version:
                requested_version = self.current_version
                self._apply_version_config(detected_version)
                log(
                    f"Auto-detected version {detected_version} from process "
                    f"(requested={requested_version}, path={image_path})"
                )
            ptr_addr = self.module_base + self.base_offset
            p1 = self.pm.read_longlong(ptr_addr)
            if self.intermediate_offset is not None:
                self.target = p1 + self.intermediate_offset
            else:
                self.target = p1
            log(
                f"Connected to {self.process_name} (PID={self.pid}, Version={self.current_version}, "
                f"BaseOffset=0x{self.base_offset:X}, Target=0x{self.target:X}, "
                f"R_off=0x{self.r_off:X}, G_off=0x{self.g_off:X}, B_off=0x{self.b_off:X})"
            )
            return True
        except Exception as e:
            log(f"connect failed: {e}")
            self.pm = None
            self.pid = None
            self.module_base = None
            self.target = None
            return False

    def set_version(self, version: str) -> bool:
        """Switch to a different CSP version. Returns True if version changed."""
        normalized = normalize_version(version)
        if normalized == self.current_version:
            return False
        self._apply_version_config(normalized)
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
        if self.module_base is None:
            return None
        # Re-read pointer each call; only adopt a new target when at least one
        # color-space block carries data, or when no previous target exists.
        try:
            p1 = self.pm.read_longlong(self.module_base + self.base_offset)
            if p1:
                if self.intermediate_offset is not None:
                    new_target = p1 + self.intermediate_offset
                else:
                    new_target = p1
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

    def _read_u32(self, addr: int) -> int:
        assert self.pm is not None
        return self.pm.read_int(addr) & 0xFFFFFFFF

    def _write_u32(self, addr: int, value: int) -> None:
        assert self.pm is not None
        self.pm.write_int(addr, u32_to_s32(value))

    def get_color(self) -> Optional[Dict[str, int]]:
        if self.pm is None and not self.connect():
            return None

        space_addrs = self._space_addresses()
        if not space_addrs:
            log("get_color: target not ready")
            return None

        snapshots: Dict[str, Dict[str, object]] = {}
        for space_name, addrs in space_addrs.items():
            raws = tuple(self._read_u32(addr) for addr in addrs)
            snapshots[space_name] = {
                "offsets": tuple(addr - self.target for addr in addrs) if self.target is not None else addrs,
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

    def set_color(self, r: int, g: int, b: int) -> bool:
        if self.pm is None and not self.connect():
            return False

        space_addrs = self._space_addresses()
        if not space_addrs:
            log("set_color: target not ready")
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
            "aob": self.aob_signature,
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

        rows = []
        for off in range(0, 0x60, 4):
            addr = self.target + off
            raw = self._read_u32(addr)
            as_u16 = decode_u16x2_dup(raw)
            rows.append(
                {
                    "offset": hex(off),
                    "address": f"0x{addr:X}",
                    "hex": f"0x{raw:08X}",
                    "u16x2_dup": as_u16,
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
        return {"target": f"0x{self.target:X}", "spaces": spaces, "rows": rows}


def get_csp_theme() -> dict:
    import glob
    import sqlite3
    
    appdata = os.environ.get('APPDATA')
    userprofile = os.environ.get('USERPROFILE')
    
    candidates = []
    if appdata:
        # CELSYS (JP/CN) and CELSYS_EN (EN) AppData candidates
        candidates.append(os.path.join(appdata, 'CELSYSUserData', 'CELSYS', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYSUserData', 'CELSYS_EN', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS_EN', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS', 'CLIPStudioPaint', '*', 'Boot', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS_EN', 'CLIPStudioPaint', '*', 'Boot', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS', 'CLIPStudioPaint', '*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(appdata, 'CELSYS_EN', 'CLIPStudioPaint', '*', 'Preference', 'Config.sqlite'))

    if userprofile:
        # Documents candidates
        candidates.append(os.path.join(userprofile, 'Documents', 'CELSYS', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(userprofile, 'Documents', 'CELSYSUserData', 'CELSYS', 'CLIPStudioPaintVer*', 'Preference', 'Config.sqlite'))
        candidates.append(os.path.join(userprofile, 'Documents', 'CELSYS', 'CLIPStudioPaint', '*', 'Boot', 'Config.sqlite'))
        candidates.append(os.path.join(userprofile, 'Documents', 'CELSYS', 'CLIPStudioPaint', '*', 'Preference', 'Config.sqlite'))

    files = []
    for pattern in candidates:
        matched = glob.glob(pattern)
        if matched:
            files.extend(matched)
            
    if not files:
        # Fallback to default gray theme
        return {
            "theme": "gray",
            "bg": "#b2b2b2",
            "text": "#222222",
            "barBg": "#cbcccb",
            "border": "1px solid #cbcccb"
        }
        
    try:
        latest_file = max(files, key=os.path.getmtime)
        conn = sqlite3.connect(latest_file)
        cur = conn.cursor()
        cur.execute('SELECT ApplicationThemeColor, ApplicationThemeColorLightDensity, ApplicationThemeColorDarkDensity FROM Interface')
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return {
                "theme": "gray",
                "bg": "#b2b2b2",
                "text": "#222222",
                "barBg": "#cbcccb",
                "border": "1px solid #cbcccb"
            }
            
        theme_color = row[0] # 0 = Dark, 1 = Light, 2 = Follow System
        light_density = row[1]
        dark_density = row[2]
        
        # Determine active theme category (Dark = True, Light = False)
        is_dark = False
        if theme_color == 2:
            is_dark = True
        elif theme_color == 1:
            is_dark = False
        else: # 0 or other values (default/follow system)
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                is_dark = (value == 0)
            except Exception:
                is_dark = True # Default to dark theme for artists if system check fails
        
        if is_dark:
            # Dark theme (Base CSP dark theme color is 78.0, and varies with dark_density * 2.7)
            gray_val = int(max(15, min(255, 78.0 + dark_density * 2.7)))
            bg = f'#{gray_val:02x}{gray_val:02x}{gray_val:02x}'
            border_gray = int(max(0, min(255, 0.852 * gray_val - 10.5)))
            border = f'1px solid #{border_gray:02x}{border_gray:02x}{border_gray:02x}'
            barBg = f'#{border_gray:02x}{border_gray:02x}{border_gray:02x}'
            text = '#ffffff' if gray_val < 130 else '#222222'
            theme_name = 'csp-dark'
        else:
            # Light theme (Base CSP light theme color is 241.0, and varies with light_density * 2.5)
            gray_val = int(max(100, min(240, 241.0 + light_density * 2.5)))
            bg = f'#{gray_val:02x}{gray_val:02x}{gray_val:02x}'
            border_gray = int(max(0, min(255, 1.45 * gray_val - 124.0)))
            border = f'1px solid #{border_gray:02x}{border_gray:02x}{border_gray:02x}'
            barBg = f'#{border_gray:02x}{border_gray:02x}{border_gray:02x}'
            text = '#ffffff' if gray_val < 130 else '#222222'
            theme_name = 'csp-light'
            
        return {
            "theme": theme_name,
            "bg": bg,
            "text": text,
            "barBg": barBg,
            "border": border
        }
    except Exception as e:
        return {
            "error": str(e),
            "theme": "gray",
            "bg": "#b2b2b2",
            "text": "#222222",
            "barBg": "#cbcccb",
            "border": "1px solid #cbcccb"
        }



