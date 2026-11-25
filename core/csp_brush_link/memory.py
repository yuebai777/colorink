"""Low-level CSP process memory read/write helpers.

Extracted from ``core.csp_brush_link``: u32 accessors and the small value
codecs used by the slot reader/writer and dump inspector.
"""

from __future__ import annotations


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
    low = raw & 0xFFFF
    high = (raw >> 16) & 0xFFFF
    if low != high:
        return None
    return _clamp_byte(round((low / 65535.0) * 255.0))


class MemoryMixin:
    # ----- memory accessors -----------------------------------------------
    def _read_u32(self, address: int) -> int:
        assert self.pm is not None
        return self.pm.read_int(address) & 0xFFFFFFFF

    def _write_u32(self, address: int, value: int) -> None:
        assert self.pm is not None
        self.pm.write_int(address, _u32_to_signed(value))
