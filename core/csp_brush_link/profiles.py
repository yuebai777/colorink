"""CSP build profile registry and version/config management.

Extracted from ``core.csp_brush_link``: the per-build technical constants,
profile dataclass and the CSPSync methods that select/apply a profile and
load user ``config.ini`` overrides.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass

try:
    from brush_color_spaces import build_space_offsets
except ImportError:
    from core.brush_color_spaces import build_space_offsets

# ---------------------------------------------------------------------------
# Build-specific technical constants (objective facts from CLIPStudioPaint.exe)
# ---------------------------------------------------------------------------
_AOB_CSP4_0     = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18 48 83 C1 48 E8 ?? ?? ?? ?? 48 8B C3"
_AOB_CSP4_2_7EX = "41 0F 10 ?? 1C 41 0F 11 ?? 1C F2 41 0F 10 ?? 10 F2 41 0F 11 ?? 10 41 8B ?? 18 41 89 ?? 18"
_AOB_CSP5_0     = "0F 10 42 1C 0F 11 41 1C F2 0F 10 42 10 F2 0F 11 41 10 8B 42 18 48 83 C2 48 89 41 18"

AOB_MAP: dict[str, str] = {
    "csp4.0":      _AOB_CSP4_0,
    "csp4.2.7-ex": _AOB_CSP4_2_7EX,
    "csp5.0":      _AOB_CSP5_0,
    "csp5.0-ex":   _AOB_CSP5_0,
}

SECTION_NAME       = "ClipStudioPaint"
DEFAULT_VERSION_KEY = "csp4.x"

# Values that mean "let connect() detect the build from the running exe"
# rather than pinning a specific profile.
AUTO_VERSION_KEYS: frozenset[str] = frozenset({"", "auto"})

_DEFAULT_RED_OFFSET   = 0x20
_DEFAULT_GREEN_OFFSET = 0x24
_DEFAULT_BLUE_OFFSET  = 0x28


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


# Memory-sync tracing. Off by default (the polling thread logs per write);
# set COLORINK_CSP_DEBUG=1 to get the copy-locate / write decisions on stderr
# when diagnosing a "colour does not sync" report, without editing code.
_DEBUG = os.environ.get("COLORINK_CSP_DEBUG", "").strip().lower() not in (
    "", "0", "false", "no", "off",
)


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
        # __file__ is core/csp_brush_link/profiles.py — the config.ini used to
        # be discovered next to core/csp_brush_link.py, i.e. the core/ dir.
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    local_cfg = os.path.join(app_dir, "config.ini")
    if os.path.exists(local_cfg):
        return local_cfg

    return os.path.abspath("config.ini")


class ProfileMixin:
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
        # 切换版本即切换进程/布局：清空色相记忆与副本缓存，避免
        # 主/副槽或新旧版本之间串扰（旧值在新进程里没有意义）。
        self._last_hsv_h = 0.0
        self._last_hsv_s = 0.0
        self._resolve_fail_count = 0
        self._sub_copy_addrs = None
        self._main_copy_addrs = None
        self._sub_copy_addrs_known = None
        self._main_copy_addrs_known = None
        self._copy_scan_ts = {}

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
        """Switch to a different CSP build profile. Returns True if it changed.

        ``"auto"`` (and an empty value) is NOT a profile: the build is
        detected from the running exe in :meth:`connect`. It must therefore
        leave the current profile alone — ``_normalize_version_key`` maps
        anything unrecognised to the ``csp4.x`` default, so treating "auto"
        as a request used to tear down a live CSP 5.1 connection, reset the
        hue memory and drop the copy caches every time settings were applied
        (``update_versions`` runs on each apply, with "auto" as the default).
        """
        if str(key or "").strip().lower() in AUTO_VERSION_KEYS:
            return False
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
