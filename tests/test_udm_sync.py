"""Unit tests for UDM Paint active brush-color memory synchronization."""

import time
import pytest
from PyQt6.QtWidgets import QApplication

from core.udm_brush_link import (
    UDMSync,
    _DEFAULT_RED_OFFSET,
    _SUB_OFFSET_DELTA,
    _TRANSPARENT_FLAG_OFFS,
    _TRANSPARENT_FLAG_ON,
)
from core.memory_sync import MemorySyncThread


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _StubPm:
    def __init__(self):
        self.u32 = {
            0x2000 + _TRANSPARENT_FLAG_OFFS: 0,
            # Main RGB (41, 76, 33) in u8x4 / u32
            0x2000 + 0x20: 0x29292929,
            0x2000 + 0x24: 0x4C4C4C4C,
            0x2000 + 0x28: 0x21212121,
            # Sub RGB (255, 255, 255) in u8x4 / u32
            0x2000 + 0x78: 0xFFFFFFFF,
            0x2000 + 0x7C: 0xFFFFFFFF,
            0x2000 + 0x80: 0xFFFFFFFF,
        }

    def read_longlong(self, address):
        return 0x2000

    def read_int(self, address):
        return self.u32.get(address, 0)

    def write_int(self, address, value):
        self.u32[address] = value & 0xFFFFFFFF

    def read_bytes(self, address, size):
        val = self.read_int(address)
        return val.to_bytes(size, "little")

    def write_bytes(self, address, value, size):
        val = int.from_bytes(value, "little")
        self.write_int(address, val)


def _make_stub_udm():
    sync = UDMSync.__new__(UDMSync)
    sync.pm = _StubPm()
    sync.pid = 12345
    sync.module_base = 0x1000
    sync.base_offset = 0x04AE73B0
    sync.target = 0x2000
    sync.current_version = "udm4.0"
    sync.process_name = "UDMPaintPRO.exe"
    sync.r_off = _DEFAULT_RED_OFFSET
    sync.g_off = 0x24
    sync.b_off = 0x28
    from core.brush_color_spaces import build_space_offsets
    sync.space_offsets = build_space_offsets(sync.r_off)
    sync.sub_space_offsets = build_space_offsets(sync.r_off + _SUB_OFFSET_DELTA)
    sync._last_hsv_h = 0.0
    sync._last_hsv_s = 0.0
    sync._resolve_fail_count = 0
    sync._RESOLVE_FAIL_LIMIT = 30
    sync.use_abs = False
    return sync


def test_udm_sub_offsets_stride():
    sync = _make_stub_udm()
    assert sync.sub_space_offsets["rgb"] == (0x78, 0x7C, 0x80)
    assert sync.sub_space_offsets["cmyk"] == (0x84, 0x88, 0x8C, 0x90)
    assert sync.sub_space_offsets["hsv"] == (0x94, 0x98, 0x9C)
    assert sync.sub_space_offsets["hls"] == (0xA0, 0xA4, 0xA8)


def test_udm_read_main_and_sub_color():
    sync = _make_stub_udm()
    main = sync.get_color()
    assert main is not None
    assert main["r"] == 41
    assert main["g"] == 76
    assert main["b"] == 33
    assert main["transparent"] == 0
    assert main["index"] == 0

    sub = sync.get_sub_color()
    assert sub is not None
    assert sub["r"] == 255
    assert sub["g"] == 255
    assert sub["b"] == 255
    assert sub["transparent"] == 0
    assert sub["index"] == 1


def test_udm_active_slot_index():
    sync = _make_stub_udm()
    # Initially main slot active
    assert sync.get_active_slot_index() == 0

    # Set sub slot active
    sync.pm.write_int(sync.target + _TRANSPARENT_FLAG_OFFS, 1)
    assert sync.get_active_slot_index() == 1

    # Set transparent active
    sync.pm.write_int(sync.target + _TRANSPARENT_FLAG_OFFS, _TRANSPARENT_FLAG_ON)
    assert sync.get_active_slot_index() is None
    assert sync._read_transparent_flag() is True


def test_udm_set_sub_color():
    sync = _make_stub_udm()
    # Write sub color: pure red
    assert sync.set_color(255, 0, 0, color_index=1)
    # Active slot should now be 1
    assert sync.get_active_slot_index() == 1
    # Read back sub color
    sub = sync.get_sub_color()
    assert sub == {"r": 255, "g": 0, "b": 0, "transparent": 0, "index": 1}


def test_udm_set_main_color_switches_back():
    sync = _make_stub_udm()
    # Write sub color first
    sync.set_color(255, 0, 0, color_index=1)
    assert sync.get_active_slot_index() == 1

    # Write main color: pure green
    assert sync.set_color(0, 255, 0, color_index=0)
    assert sync.get_active_slot_index() == 0
    main = sync.get_color()
    assert main == {"r": 0, "g": 255, "b": 0, "transparent": 0, "index": 0}


def test_udm_set_transparent():
    sync = _make_stub_udm()
    assert sync.set_color(0, 0, 0, transparent=True, color_index=0)
    assert sync.get_active_slot_index() is None
    assert sync._read_transparent_flag() is True
    assert sync.get_color()["transparent"] == 1

    # Normal write clears transparent flag
    assert sync.set_color(100, 100, 100, color_index=0)
    assert sync.get_active_slot_index() == 0
    assert sync._read_transparent_flag() is False
    assert sync.get_color()["transparent"] == 0


def test_udm_memory_sync_thread_poll(qapp):
    sync = _make_stub_udm()
    thread = MemorySyncThread()
    thread.udm_sync = sync
    thread.software_mode = "udm"

    active_events = []
    transparent_events = []
    color_events = []

    thread.signals.active_slot_changed.connect(lambda idx: active_events.append(idx))
    thread.signals.transparent_changed.connect(lambda idx, t: transparent_events.append((idx, t)))
    thread.signals.color_changed.connect(lambda r, g, b, idx: color_events.append((r, g, b, idx)))

    # Test write_color for sub slot
    thread.write_color(200, 50, 60, color_index=1)
    assert 1 in thread._pending_writes
    assert thread._pending_writes[1]["rgb"] == (200, 50, 60)

    # Test write_color with transparent=True
    thread.write_color(0, 0, 0, transparent=True, color_index=0)
    assert thread._pending_writes[0]["transparent"] is True
