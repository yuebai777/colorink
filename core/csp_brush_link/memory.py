"""Low-level CSP process memory read/write helpers.

Extracted from ``core.csp_brush_link``: u32/float/transparent-flag accessors,
AOB/pattern scanning and the small value codecs used by the dump inspector.
"""

from __future__ import annotations

import struct

from core.csp_brush_link.profiles import _log


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


class MemoryMixin:
    # ----- CSP 5.1 compact RGB slot --------------------------------------
    # MAIN slot: three u32 HSV channels (proportional encoding, identical
    # to the sub slot at +0x9C) at +0x3C/+0x40/+0x44.
    _RGB_U32_OFFS = (0x3C, 0x40, 0x44)
    _HSV_UI_OFFS = (0x3E, 0x42, 0x44)
    _TRANSPARENT_FLAG_OFFS = 0x08
    _TRANSPARENT_FLAG_ON = 0xFFFFFFFF
    _SUB_HSV_OFFS = (0x9C, 0xA0, 0xA4)
    _SUB_COPY_LIMIT = 200

    # ----- memory accessors -----------------------------------------------
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
