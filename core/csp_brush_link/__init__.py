#!/usr/bin/env python3

"""CLIP STUDIO PAINT active brush-color synchronization.

This package is the public facade for the CSP memory-sync backend.  The
original monolith was split into:

* :mod:`profiles` — version/profile registry and config-file handling
* :mod:`process` — Win32 process scanning/version detection and attach
* :mod:`memory` — low-level u32/float/pattern memory access
* :mod:`slots` — color-slot read/write and introspection
* :mod:`theme` — CSP UI theme reader

The public API remains unchanged: ``CSPSync``, ``get_csp_theme`` and the
documented helper symbols are all re-exported here.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from brush_color_spaces import build_space_offsets
except ImportError:
    from core.brush_color_spaces import build_space_offsets

from core.csp_brush_link.memory import (
    MemoryMixin,
    _clamp_byte,
    _decode_u16x2_duplicate,
    _u32_to_signed,
)
from core.csp_brush_link.process import (
    ProcessMixin,
    _ProcessVersionQuery,
    _detect_build_from_image_path,
    _detect_build_from_version,
)
from core.csp_brush_link.profiles import (
    AOB_MAP,
    DEFAULT_VERSION_KEY,
    SECTION_NAME,
    ProfileMixin,
    _CSPBuildProfile,
    _DEFAULT_BLUE_OFFSET,
    _DEFAULT_GREEN_OFFSET,
    _DEFAULT_RED_OFFSET,
    _PROFILE_INDEX,
    _PROFILES,
    _log,
    _normalize_version_key,
    _parse_int,
    _resolve_config_file,
)
from core.csp_brush_link.slots import SlotMixin
from core.csp_brush_link.theme import (
    _resolve_is_dark,
    _theme_fallback,
    get_csp_theme,
)


class CSPSync(ProfileMixin, ProcessMixin, MemoryMixin, SlotMixin):
    """Memory-sync backend for CLIP STUDIO PAINT's active brush color.

    The class is a thin facade over the split concern mixins:

    * version profile selection (:mod:`core.csp_brush_link.profiles`),
    * the live Win32 process attachment (:mod:`core.csp_brush_link.process`),
    * low-level memory access (:mod:`core.csp_brush_link.memory`), and
    * color-slot read/write (:mod:`core.csp_brush_link.slots`).

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
        # Last *verified* copy sets. Kept separately from the live cache as
        # the fallback for writes whose value is a trivial pattern (pure
        # black is twelve zero bytes and matches everywhere, so a scan can
        # no longer identify the copies) — see SlotMixin._remembered_copies.
        self._sub_copy_addrs_known: list[int] | None = None
        self._main_copy_addrs_known: list[int] | None = None
        # Per-slot monotonic timestamp of the last copy-locate scan, so a
        # failed locate can not re-scan the whole address space on every
        # single write (that is what wedged the polling thread).
        self._copy_scan_ts: dict[str, float] = {}

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
        # 连续解析/读取失败计数：目标进程退出后达到阈值即主动断开
        # （pm 残留会让重连入口永远不触发，进程重启后一直显示未连接）。
        self._resolve_fail_count: int = 0
        self._RESOLVE_FAIL_LIMIT = 30  # ≈3 秒（100ms 轮询）

        # Honor CSP_SYNC_VERSION env override before applying user config.
        env_version = os.environ.get("CSP_SYNC_VERSION", DEFAULT_VERSION_KEY)
        self._apply_profile(_normalize_version_key(env_version))
        self._load_user_config()


__all__ = [
    "AOB_MAP",
    "CSPSync",
    "DEFAULT_VERSION_KEY",
    "SECTION_NAME",
    "get_csp_theme",
    "_CSPBuildProfile",
    "_PROFILE_INDEX",
    "_PROFILES",
    "_ProcessVersionQuery",
    "_clamp_byte",
    "_decode_u16x2_duplicate",
    "_detect_build_from_image_path",
    "_detect_build_from_version",
    "_log",
    "_normalize_version_key",
    "_parse_int",
    "_resolve_config_file",
    "_resolve_is_dark",
    "_theme_fallback",
    "_u32_to_signed",
]
