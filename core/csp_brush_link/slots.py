"""CSP color-slot read/write and introspection.

Extracted from ``core.csp_brush_link``: sub/main colour slot addressing,
multi-copy HSV writes, public get/set/status/dump and the diagnostic dump.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

try:
    from brush_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
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

from core.csp_brush_link.memory import _clamp_byte, _decode_u16x2_duplicate
from core.csp_brush_link.profiles import _log


class SlotMixin:
    # A copy-locate scan walks CSP's whole address space and costs seconds
    # even when it ends up finding nothing usable. When the previous attempt
    # could not identify the authoritative copies, retry at most once per
    # this many seconds — writes in between go to the remembered copy set
    # (or the mirror slot), so a rescan can never stall the polling thread
    # write after write.
    _COPY_RESCAN_INTERVAL = 3.0

    # Distance between the main and the sub colour sub-object inside one
    # colour struct (verified live on CSP 5.1: RGB 0x20 -> 0x80, HSV
    # 0x3C -> 0x9C, i.e. the sub object is a copy of the main object 0x60
    # bytes further on). A copy scan matches by value, so when both slots
    # hold the SAME colour every struct yields two hits 0x60 apart and the
    # slots become indistinguishable — writing one would clobber the other.
    _SLOT_STRIDE = 0x60

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
            hits = self._search_pattern(old, max_hits=self._SUB_COPY_LIMIT + 1)
            base_addr = self.target + self._SUB_HSV_OFFS[0]
            if len(hits) <= self._SUB_COPY_LIMIT:
                addrs = list(hits)
                if base_addr not in addrs:
                    addrs.append(base_addr)
            else:
                addrs = [base_addr]
            if len(addrs) > 1:
                self._sub_copy_addrs = addrs
                # Verified copy set: also keep it as the fallback a later
                # trivial-pattern (black/white) write can reuse.
                self._sub_copy_addrs_known = list(addrs)
            else:
                # Mirror only — the copies could not be identified. Caching
                # that would re-validate against our own writes forever and
                # lock the degradation in (same invariant as
                # :meth:`_locate_hsv_copies`).
                self._sub_copy_addrs = None
            _log(f"capture_sub_copies: {len(addrs)} copies of current sub color")
            return len(addrs)
        except Exception as exc:
            _log(f"capture_sub_copies: exception: {exc}")
            return 0

    def _copy_cache_is_live(self, addrs: list[int], expected: bytes) -> bool:
        """True while every cached copy address still holds *expected*."""
        pm = self.pm
        if pm is None or not addrs:
            return False
        for addr in addrs:
            try:
                if pm.read_bytes(addr, 12) != expected:
                    return False
            except Exception:
                return False
        return True

    def _remembered_copies(self, cache_attr: str,
                           base_addr: int) -> list[int] | None:
        """Last verified copy set for this slot, if it still looks coherent.

        Needed whenever a scan can no longer identify the copies:

        * the value is a *trivial* pattern (pure black is twelve zero bytes
          and matches everywhere), or
        * only the mirror slot holds the value, because a previous degraded
          write updated the mirror alone.

        Those addresses were verified while the color still was distinctive,
        so reusing them is what lets such a write reach the brush instead of
        only the UI mirror.

        Guards: the set must belong to the current slot pointer (a stored set
        always contains its ``base_addr``) and the authoritative copies must
        still be readable and hold one and the same value — CSP keeps its
        copies in sync, a recycled allocation would not.  The mirror itself
        is excluded from that check: it is the one address a degraded write
        has already changed.
        """
        known = getattr(self, cache_attr + "_known", None)
        pm = self.pm
        if not known or pm is None or base_addr not in known:
            return None
        others = [addr for addr in known if addr != base_addr]
        if not others:
            return None
        try:
            reference = pm.read_bytes(others[0], 12)
            for addr in others[1:]:
                if pm.read_bytes(addr, 12) != reference:
                    return None
        except Exception:
            setattr(self, cache_attr + "_known", None)
            return None
        return list(known)

    def _drop_sibling_slot_hits(self, hits: list[int], base_off: int,
                                base_addr: int) -> list[int]:
        """Remove hits that belong to the OTHER colour slot.

        The main and the sub colour live in two sub-objects of one struct,
        ``_SLOT_STRIDE`` bytes apart. The scan matches by value, so while
        both slots hold the same colour (very common — e.g. both black)
        every struct produces two hits ``_SLOT_STRIDE`` apart and writing
        the main slot would silently overwrite the sub slot (verified live:
        a main write turned the sub colour magenta too).

        A hit is the sibling's field when its counterpart at the matching
        stride distance is also a hit, so it can be dropped without ever
        touching the case where the two slots hold different colours (then
        the sibling's bytes simply do not match and nothing is filtered).
        """
        if not hits:
            return hits
        # Locating the sub slot? Then the main field sits _SLOT_STRIDE lower,
        # and a hit whose counterpart above it also matched is the main one.
        locating_sub = base_off == self._SUB_HSV_OFFS[0]
        delta = self._SLOT_STRIDE if locating_sub else -self._SLOT_STRIDE
        hit_set = set(hits)
        kept = [
            addr for addr in hits
            if addr == base_addr or (addr + delta) not in hit_set
        ]
        if len(kept) != len(hits):
            _log(f"_locate_hsv_copies: dropped {len(hits) - len(kept)} hits "
                 f"belonging to the other colour slot (both slots hold the "
                 f"same colour)")
        return kept

    def _main_copies_for_derivation(self, allow_scan: bool) -> list[int] | None:
        """Locate the MAIN colour copies, to derive the sub copies from.

        Free when the cached (or last-verified) set still holds the current
        main pattern. Otherwise a capped scan is run only if *allow_scan*, and
        only once per ``_COPY_RESCAN_INTERVAL`` — a main-pattern scan walks the
        whole address space, and repeating one per write is what froze the
        polling thread before v1.6.7. Returns ``None`` when the main colour is
        itself too trivial to identify.
        """
        if self.pm is None or self.target is None:
            return None
        main_base = self.target + self._RGB_U32_OFFS[0]
        main_pat = b"".join(
            self._read_u32(self.target + off).to_bytes(4, "little")
            for off in self._RGB_U32_OFFS
        )
        for attr in ("_main_copy_addrs", "_main_copy_addrs_known"):
            cached = getattr(self, attr, None)
            if cached and self._copy_cache_is_live(cached, main_pat):
                return list(cached)
        if not allow_scan:
            return None
        scan_ts = getattr(self, "_copy_scan_ts", None)
        if scan_ts is None:
            scan_ts = {}
            self._copy_scan_ts = scan_ts
        key = "_main_copy_addrs_derive"
        last = scan_ts.get(key)
        now = time.monotonic()
        if last is not None and now - last < self._COPY_RESCAN_INTERVAL:
            return None
        scan_ts[key] = now
        hits = self._search_pattern(main_pat, max_hits=self._SUB_COPY_LIMIT + 1)
        if len(hits) > self._SUB_COPY_LIMIT:
            return None
        hits = self._drop_sibling_slot_hits(hits, self._RGB_U32_OFFS[0], main_base)
        addrs = list(hits)
        if main_base not in addrs:
            addrs.append(main_base)
        return addrs if len(addrs) > 1 else None

    def _sub_copies_from_main(self, old: bytes, base_addr: int,
                              allow_scan: bool) -> list[int] | None:
        """Derive the sub-colour copy set from the main-colour copy set.

        The main HSV block (+0x3C) and the sub HSV block (+0x9C) are two
        sub-objects of one colour struct ``_SLOT_STRIDE`` bytes apart — the
        same relationship :meth:`_drop_sibling_slot_hits` already depends on.

        This matters because the two slots fail differently. The main colour is
        the one the user actively edits, so its pattern is normally distinctive
        and its scan succeeds; CSP's default *background* is white, whose
        12-byte HSV-u32 pattern (eight 0x00 then four 0xFF) matches far past
        ``_SUB_COPY_LIMIT``. So the first background write of a session reached
        the +0x9C UI mirror only — CSP's brush kept the old colour — and the
        remembered-set recovery had nothing to fall back on yet.

        Safety: every derived address must currently hold *old* (the live sub
        pattern), and the mirror must be among the siblings. That is what
        proves these really are the sub fields of the current colour structs
        rather than an unrelated allocation. Returns ``None`` whenever that
        cannot be established, leaving the caller on its previous behaviour.
        """
        pm = self.pm
        if pm is None or self.target is None:
            return None
        main_addrs = self._main_copies_for_derivation(allow_scan)
        if not main_addrs:
            return None
        derived = []
        for addr in main_addrs:
            candidate = addr + self._SLOT_STRIDE
            try:
                if pm.read_bytes(candidate, 12) != old:
                    return None
            except Exception:
                return None
            derived.append(candidate)
        if len(derived) < 2 or base_addr not in derived:
            return None
        return derived

    def _degraded_copy_fallback(self, old: bytes, cache_attr: str,
                                base_off: int, base_addr: int, reason: str,
                                allow_scan: bool = True) -> list[int]:
        """Best copy set available when the value scan identified nothing.

        Order: the last verified set, then (sub slot only) the set derived
        from the main copies via ``_SLOT_STRIDE``, then the UI mirror alone —
        which does not reach the brush.
        """
        remembered = self._remembered_copies(cache_attr, base_addr)
        if remembered:
            _log(f"_locate_hsv_copies: {reason} — remembered copy set, NOT cached")
            return remembered
        if base_off == self._SUB_HSV_OFFS[0]:
            derived = self._sub_copies_from_main(old, base_addr, allow_scan)
            if derived:
                # Validated against the live sub pattern at every address, so
                # this is a genuine copy set and may be cached (unlike a
                # mirror-only result, which would re-validate against our own
                # writes forever and lock the degradation in).
                setattr(self, cache_attr, derived)
                setattr(self, cache_attr + "_known", list(derived))
                _log(f"_locate_hsv_copies: {reason} — derived {len(derived)} "
                     f"sub copies from the main copy set "
                     f"(+0x{self._SLOT_STRIDE:X})")
                return derived
        _log(f"_locate_hsv_copies: {reason} — mirror-only write, NOT cached")
        return [base_addr]

    def _remember_sub_siblings(self, main_addrs: list[int]) -> None:
        """Piggyback: remember the sub copies implied by a main locate.

        Costs no scan — the main copy set was just identified, so the sub
        fields ``_SLOT_STRIDE`` further on are known too. Recording them means
        a later background write whose own pattern is trivial (white/black)
        already has a verified set to fall back on.
        """
        pm = self.pm
        if pm is None or self.target is None or getattr(self, "_sub_copy_addrs_known", None):
            return
        sub_base = self.target + self._SUB_HSV_OFFS[0]
        try:
            expected = pm.read_bytes(sub_base, 12)
        except Exception:
            return
        derived = []
        for addr in main_addrs:
            candidate = addr + self._SLOT_STRIDE
            try:
                if pm.read_bytes(candidate, 12) != expected:
                    return
            except Exception:
                return
            derived.append(candidate)
        if len(derived) < 2 or sub_base not in derived:
            return
        self._sub_copy_addrs_known = list(derived)
        _log(f"_locate_hsv_copies: remembered {len(derived)} sub copies "
             f"alongside the main copy set (+0x{self._SLOT_STRIDE:X})")

    def _locate_hsv_copies(self, old: bytes, cache_attr: str,
                           base_off: int) -> list[int]:
        """Locate every in-memory copy of the current HSV u32 pattern.

        The brush reads main/sub colors from MULTIPLE copies (verified: 8
        copies for both slots on CSP 5.1); the base slot alone is not the
        brush source. The cached address list is trusted only while every
        address still holds *old* (CSP may create/destroy copies at
        runtime).

        A scan can fail to identify the copies in two ways, and neither may
        be cached — a cached degraded set would be re-validated against the
        value we wrote ourselves (always passing) and would lock the
        degradation in forever:

        * the pattern is trivial (black/white) and matches far too often,
        * only the mirror holds the pattern, i.e. the authoritative copies
          are momentarily out of sync (typically right after a degraded
          write of our own).

        Both cases fall back to the remembered copy set, then to the mirror,
        and are retried on a later write (see ``_COPY_RESCAN_INTERVAL``).
        """
        if self.target is None or self.pm is None:
            return []
        base_addr = self.target + base_off

        addrs = getattr(self, cache_attr, None)
        if addrs:
            if self._copy_cache_is_live(addrs, old):
                return addrs
            setattr(self, cache_attr, None)

        scan_ts = getattr(self, "_copy_scan_ts", None)
        if scan_ts is None:
            scan_ts = {}
            self._copy_scan_ts = scan_ts
        last = scan_ts.get(cache_attr)
        now = time.monotonic()
        if last is not None and now - last < self._COPY_RESCAN_INTERVAL:
            # 扫描冷却中：写记住的副本集，或由【已缓存】的主色副本推导
            # （不触发新扫描）；都没有才只写镜像槽。全地址空间扫描每次要
            # 几秒，逐次写入都重扫会把轮询线程彻底堵死。
            return self._degraded_copy_fallback(
                old, cache_attr, base_off, base_addr,
                "scan on cooldown", allow_scan=False)
        scan_ts[cache_attr] = now

        hits = self._search_pattern(old, max_hits=self._SUB_COPY_LIMIT + 1)
        if len(hits) > self._SUB_COPY_LIMIT:
            # 平凡模式（黑 = 全 0，12 个零字节；白 / 灰同理）在进程内存里
            # 处处命中，无法区分权威笔刷副本。退回已验证的副本集 / 由主色
            # 副本按 _SLOT_STRIDE 推导；都不行才只写镜像槽。
            return self._degraded_copy_fallback(
                old, cache_attr, base_off, base_addr,
                f">{self._SUB_COPY_LIMIT} hits (trivial pattern)")

        hits = self._drop_sibling_slot_hits(hits, base_off, base_addr)
        addrs = list(hits)
        if base_addr not in addrs:
            addrs.append(base_addr)
        if len(addrs) > 1:
            setattr(self, cache_attr, addrs)
            setattr(self, cache_attr + "_known", list(addrs))
            _log(f"_locate_hsv_copies: located {len(addrs)} copies")
            if base_off == self._RGB_U32_OFFS[0]:
                # Free: the sub fields sit _SLOT_STRIDE further on, so record
                # them now while they are still coherent. A later background
                # write whose own pattern is trivial then has a verified set to
                # fall back on instead of degrading to the UI mirror.
                self._remember_sub_siblings(addrs)
            return addrs

        # 只有镜像槽持有该值：权威副本此刻不同步（通常是上一次降级写入
        # 只写了镜像）。同样退回已验证副本集 / 主色推导，绝不缓存降级结果
        # ——一旦缓存，校验读到的总是自己刚写过的值（恒通过），会把降级
        # 状态永久锁死。
        return self._degraded_copy_fallback(
            old, cache_attr, base_off, base_addr,
            "only the mirror holds the pattern")

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
                raise ValueError("target pointer not resolved")
            # Reading all three RGB channels validates the cached target.
            for off in self._RGB_U32_OFFS:
                _ = self.pm.read_int(self.target + off)
            self._resolve_fail_count = 0
            return True
        except Exception as exc:
            _log(f"_resolve_rgb_target: target unreadable: {exc}")
            self.target = None
            self._resolve_fail_count += 1
            if self._resolve_fail_count >= self._RESOLVE_FAIL_LIMIT:
                # 目标进程大概率已退出：释放句柄，让下一次访问重新 connect。
                # 否则 pm 一直非 None，重连入口（`if self.pm is None`）永远
                # 不触发，CSP 重启后 Colorink 会永久显示未连接。
                _log(f"_resolve_rgb_target: {self._resolve_fail_count} consecutive "
                     f"failures — dropping connection")
                self._drop_connection()
                self._resolve_fail_count = 0
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
            if (source_space == "hsv" and source_values
                    and "h" in source_values and "s" in source_values
                    and "v" in source_values):
                # 源空间直写：用户在 HSV 滑块上的精确浮点值直接编码，
                # 不经 RGB 往返（避免色相精度损失），与 set_color 的
                # docstring 承诺一致。
                h_deg = float(source_values["h"]) % 360.0
                s_pct = float(source_values["s"])
                v_pct = float(source_values["v"])
                if s_pct < 1.0:
                    h_deg = self._last_hsv_h
                else:
                    self._last_hsv_h = h_deg
                if v_pct < 1.0:
                    s_pct = self._last_hsv_s
                else:
                    self._last_hsv_s = s_pct
            else:
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
                v_pct = hsv["v"]
            new = b"".join(
                u.to_bytes(4, "little")
                for u in encode_space_values_float(
                    "hsv", {"h": h_deg, "s": s_pct, "v": v_pct}
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
