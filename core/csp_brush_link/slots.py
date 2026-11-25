"""CSP color-slot read/write and introspection (CSP 4.x / 5.0 u16x2_dup layout).

Extracted from ``core.csp_brush_link``: color-slot addressing, public
get/set/status/dump, and the diagnostic dump.
"""

from __future__ import annotations

from typing import Any, Mapping

try:
    from core.brush_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
        decode_space_raws,
        encode_space_values,
        encode_space_values_float,
        format_space_values,
        resolve_active_rgb,
        rgb_to_space_values,
        space_to_rgb_float,
    )
except ImportError:
    from brush_color_spaces import (
        SPACE_ORDER,
        any_space_has_nonzero_raws,
        decode_space_raws,
        encode_space_values,
        encode_space_values_float,
        format_space_values,
        resolve_active_rgb,
        rgb_to_space_values,
        space_to_rgb_float,
    )

from core.csp_brush_link.memory import _clamp_byte, _decode_u16x2_duplicate
from core.csp_brush_link.profiles import _log


class SlotMixin:
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
            if h_val > 0 or s_val > 1:
                self._last_hsv_h = h_val
            if source_values.get("v", 0) > 1:
                self._last_hsv_s = s_val
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
        """Write color to CSP 4.x/5.0 memory, optionally preserving source-space precision.

        *source_space* / *source_values* (the space the user last interacted
        with and its float values) are written directly to that space's memory
        slot via :func:`encode_space_values_float` — no int rounding, no
        RGB -> source round-trip loss.

        *transparent* is unsupported for u16x2_dup builds (4.x/5.0) and skipped.
        """
        if self.pm is None and not self.connect():
            return False

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
                    # RGB -> source round-trip precision loss.
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
        # CSP 5.1 内存同步已移除：拒绝后不再反复重连，直接报告未连接，
        # 并把原因暴露给上层（memory_sync 据此提示改用手机模式）。
        if getattr(self, "unsupported_reason", "") and self.pm is None:
            return {
                "connected": False,
                "pid": None,
                "baseOffset": f"0x{self.base_offset:X}",
                "target": None,
                "aob": self.aob_signature,
                "version": self.current_version,
                "processName": self.process_name,
                "unsupported_reason": self.unsupported_reason,
            }
        if self.pm is None:
            self.connect()
        connected = False
        if self.pm is not None:
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
            "unsupported_reason": getattr(self, "unsupported_reason", ""),
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
