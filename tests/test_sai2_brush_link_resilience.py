#!/usr/bin/env python3

"""Resilience tests for :mod:`core.sai2_brush_link` (no SAI required).

Covers the C/D/E optimisation round:
  C  write read-back verification (retry once, then report)
  D  automatic signature detection (auto walks every signature, offsets last)
  E  machine-readable failure reasons exposed through ``status()``
"""

from __future__ import annotations

import ctypes
import struct

import pytest

from core import sai2_brush_link as sl


SLOT_ADDR = 0x140321700
BASE = 0x140000000
PATTERN_HIT = 0x140101741


# ── helpers ───────────────────────────────────────────────────────────────
def _reader(mapping: dict):
    """_read_memory stand-in backed by an address -> bytes map."""
    def read(handle, address, size):
        data = mapping.get(address)
        return data[:size] if data is not None else None
    return read


def _sig_entry(version: str, pattern_addr: int, target: int):
    """(disp32 address, disp32 bytes) that resolves *pattern_addr* to *target*."""
    sig = sl.SIGNATURES[version]
    rel = target - (pattern_addr + sig["next_rip_offset"])
    return pattern_addr + sig["disp_offset"], struct.pack("<i", rel)


def _fake_memory(initial: bytes = b"\x00\x00\xff"):
    """In-memory stand-in for _read_memory/_write_memory on the slot."""
    state = {"slot": bytes(initial)}

    def read(handle, address, size):
        if address == SLOT_ADDR:
            return state["slot"][:size]
        return b"\x00" * size

    def write(handle, address, data):
        if address == SLOT_ADDR:
            state["slot"] = bytes(data)
        return True

    return state, read, write


def _connected(monkeypatch, slot=(255, 0, 0)):
    sync = sl.SAI2Sync()
    sync._handle = 0x1234
    sync._pid = None          # keep the UI-refresh path out of these tests
    sync._color_addr = SLOT_ADDR
    sync._base = BASE
    sync._size = 0x1000
    sync.resolve_method = "signature:pre-2024-sai2"
    state, read, write = _fake_memory(bytes((slot[2], slot[1], slot[0])))
    monkeypatch.setattr(sl, "_read_memory", read)
    monkeypatch.setattr(sl, "_write_memory", write)
    return sync, state


def _scan_hits(monkeypatch, hits):
    """Make _scan_pattern_masked return addresses from *hits* in call order."""
    calls = []

    def scan(handle, base, size, pattern):
        calls.append(tuple(p for p in pattern if p is not None))
        index = len(calls) - 1
        return hits[index] if index < len(hits) else None

    monkeypatch.setattr(sl, "_scan_pattern_masked", scan)
    return calls


# ── version/auto helpers ──────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("auto", True), ("AUTO", True), ("  auto ", True),
    ("pre-2024-sai2", False), ("after-2024-sai2", False), ("vhsv", False),
])
def test_is_auto_version(value, expected):
    assert sl._is_auto_version(value) is expected


def test_init_marks_auto_and_normalises_version(monkeypatch):
    # SAI2_SYNC_VERSION in the environment would override the "auto" default,
    # so the test must control it explicitly.
    monkeypatch.delenv("SAI2_SYNC_VERSION", raising=False)
    assert sl.SAI2Sync("auto")._auto_version is True
    assert sl.SAI2Sync(None)._auto_version is True
    assert sl.SAI2Sync("after-2024-sai2")._auto_version is False
    assert sl.SAI2Sync("auto").version in sl.SIGNATURES


def test_env_version_pins_signature(monkeypatch):
    """An explicit env version must disable auto detection."""
    monkeypatch.setenv("SAI2_SYNC_VERSION", "after-2024-sai2")
    sync = sl.SAI2Sync()
    assert sync._auto_version is False
    assert sync.version == "after-2024-sai2"


def test_env_auto_keeps_detection(monkeypatch):
    monkeypatch.setenv("SAI2_SYNC_VERSION", "auto")
    assert sl.SAI2Sync()._auto_version is True


def test_set_version_toggles_auto(monkeypatch):
    sync = sl.SAI2Sync("after-2024-sai2")
    assert sync._auto_version is False
    assert sync.set_version("auto") is True
    assert sync._auto_version is True
    assert sync.set_version("auto") is False       # no change
    assert sync.set_version("pre-2024-sai2") is True
    assert sync._auto_version is False


# ── D: signature/offset resolution ────────────────────────────────────────
def test_auto_tries_every_signature(monkeypatch):
    """pre-2024 misses, after-2024 hits -> the second signature wins."""
    sync = sl.SAI2Sync("auto")
    calls = _scan_hits(monkeypatch, [None, PATTERN_HIT])
    disp, rel = _sig_entry("after-2024-sai2", PATTERN_HIT, SLOT_ADDR)
    monkeypatch.setattr(sl, "_read_memory",
                        _reader({disp: rel, SLOT_ADDR: b"\x01\x02\x03"}))
    resolved = sync._resolve_color_address(0x1, BASE, 0x1000)
    assert resolved == (SLOT_ADDR, "signature:after-2024-sai2")
    assert len(calls) == 2


def test_explicit_version_tries_only_that_signature(monkeypatch):
    """Pinning a version must not silently use the other signature."""
    sync = sl.SAI2Sync("pre-2024-sai2")
    calls = _scan_hits(monkeypatch, [None, PATTERN_HIT])
    offset_addr = BASE + next(iter(sl.KNOWN_OFFSETS.values()))
    monkeypatch.setattr(sl, "_read_memory", _reader({offset_addr: b"\x00\x00\x00"}))
    resolved = sync._resolve_color_address(0x1, BASE, 0x1000)
    assert len(calls) == 1
    assert resolved is not None
    assert resolved[1].startswith("offset:")


def test_unreadable_signature_candidate_is_skipped(monkeypatch):
    """A signature resolving to an unreadable address must not win."""
    sync = sl.SAI2Sync("auto")
    calls = _scan_hits(monkeypatch, [PATTERN_HIT, PATTERN_HIT + 0x40])
    pre_disp, pre_rel = _sig_entry("pre-2024-sai2", PATTERN_HIT, 0xDEAD0000)
    after_disp, after_rel = _sig_entry("after-2024-sai2", PATTERN_HIT + 0x40, SLOT_ADDR)
    monkeypatch.setattr(sl, "_read_memory", _reader({
        pre_disp: pre_rel, after_disp: after_rel, SLOT_ADDR: b"\x01\x02\x03"}))
    resolved = sync._resolve_color_address(0x1, BASE, 0x1000)
    assert resolved == (SLOT_ADDR, "signature:after-2024-sai2")
    assert len(calls) == 2


def test_resolution_falls_back_to_known_offset(monkeypatch):
    sync = sl.SAI2Sync("auto")
    _scan_hits(monkeypatch, [None, None])
    offset_addr = BASE + next(iter(sl.KNOWN_OFFSETS.values()))
    monkeypatch.setattr(sl, "_read_memory", _reader({offset_addr: b"\x00\x00\x00"}))
    resolved = sync._resolve_color_address(0x1, BASE, 0x1000)
    assert resolved is not None
    assert resolved[0] == offset_addr
    assert resolved[1].startswith("offset:")


def test_resolution_returns_none_when_nothing_works(monkeypatch):
    sync = sl.SAI2Sync("auto")
    _scan_hits(monkeypatch, [None, None])
    monkeypatch.setattr(sl, "_read_memory", _reader({}))
    assert sync._resolve_color_address(0x1, BASE, 0x1000) is None


def test_duplicate_candidates_are_deduplicated(monkeypatch):
    """Both signatures resolving to the same address should be tried once."""
    sync = sl.SAI2Sync("auto")
    _scan_hits(monkeypatch, [PATTERN_HIT, PATTERN_HIT + 0x40])
    pre_disp, pre_rel = _sig_entry("pre-2024-sai2", PATTERN_HIT, SLOT_ADDR)
    after_disp, after_rel = _sig_entry("after-2024-sai2", PATTERN_HIT + 0x40, SLOT_ADDR)
    reads = []

    def read(handle, address, size):
        if address == SLOT_ADDR:
            reads.append(address)
            return b"\x01\x02\x03"
        data = {pre_disp: pre_rel, after_disp: after_rel}.get(address)
        return data[:size] if data is not None else None

    monkeypatch.setattr(sl, "_read_memory", read)
    resolved = sync._resolve_color_address(0x1, BASE, 0x1000)
    assert resolved == (SLOT_ADDR, "signature:pre-2024-sai2")
    assert len(reads) == 1


# ── C: write read-back verification ───────────────────────────────────────
def test_set_color_verifies_readback(monkeypatch):
    sync, state = _connected(monkeypatch)
    assert sync.set_color(10, 20, 30) is True
    assert state["slot"] == bytes((30, 20, 10))     # B, G, R
    assert sync.last_error == ""


def test_set_color_retries_once_then_succeeds(monkeypatch):
    """First read-back mismatches, the retry lands the right value."""
    sync = sl.SAI2Sync()
    sync._handle, sync._pid, sync._color_addr = 0x1, None, SLOT_ADDR
    sync._base, sync._size = BASE, 0x1000
    # keep the pre-write "previous colour" read and the picker observation out
    # of the call sequence so the read-back order is exactly as asserted
    monkeypatch.setattr(sync.ui_refresher, "wants_previous_color", lambda: False)
    monkeypatch.setattr(sl.SAI2Sync, "ui_picker", lambda self: None)
    writes = []
    reads = {"n": 0}

    def write(handle, address, data):
        writes.append(bytes(data))
        return True

    def read(handle, address, size):
        reads["n"] += 1
        if reads["n"] == 1:
            return b"\x00\x00\x00"          # stale read-back
        return writes[-1][:size]

    monkeypatch.setattr(sl, "_write_memory", write)
    monkeypatch.setattr(sl, "_read_memory", read)
    assert sync.set_color(10, 20, 30) is True
    assert len(writes) == 2
    assert sync.last_error == ""


def test_set_color_reports_failure_after_two_mismatches(monkeypatch):
    sync = sl.SAI2Sync()
    sync._handle, sync._pid, sync._color_addr = 0x1, None, SLOT_ADDR
    sync._base, sync._size = BASE, 0x1000
    monkeypatch.setattr(sync.ui_refresher, "wants_previous_color", lambda: False)
    monkeypatch.setattr(sl, "_write_memory", lambda h, a, d: True)
    monkeypatch.setattr(sl, "_read_memory", lambda h, a, s: b"\x00\x00\x00")
    assert sync.set_color(10, 20, 30) is False
    assert sync.last_error == "write_verify_failed"


def test_set_color_clamps_out_of_range(monkeypatch):
    sync, state = _connected(monkeypatch)
    assert sync.set_color(-5, 300, 128) is True
    assert state["slot"] == bytes((128, 255, 0))


# ── E: failure classification ─────────────────────────────────────────────
def test_reason_not_running(monkeypatch):
    monkeypatch.setattr(sl, "_find_process", lambda name: None)
    sync = sl.SAI2Sync()
    assert sync._connect() is False
    assert sync.last_error == "not_running"
    assert sync.status()["reason"] == "not_running"


def test_reason_access_denied(monkeypatch):
    monkeypatch.setattr(sl, "_find_process", lambda name: 4242)

    class _K:
        @staticmethod
        def OpenProcess(*args):
            ctypes.set_last_error(5)
            return None

        @staticmethod
        def CloseHandle(handle):
            pass

    monkeypatch.setattr(sl, "_kernel32", _K)
    sync = sl.SAI2Sync()
    assert sync._connect() is False
    assert sync.last_error == "access_denied"


def test_reason_open_failed_carries_code(monkeypatch):
    monkeypatch.setattr(sl, "_find_process", lambda name: 4242)

    class _K:
        @staticmethod
        def OpenProcess(*args):
            ctypes.set_last_error(87)
            return None

        @staticmethod
        def CloseHandle(handle):
            pass

    monkeypatch.setattr(sl, "_kernel32", _K)
    sync = sl.SAI2Sync()
    assert sync._connect() is False
    assert sync.last_error == "open_failed:87"


def test_reason_module_info_failed(monkeypatch):
    monkeypatch.setattr(sl, "_find_process", lambda name: 4242)
    monkeypatch.setattr(sl, "_get_module_info", lambda pid, name: (None, None))

    class _K:
        @staticmethod
        def OpenProcess(*args):
            return 0x99

        @staticmethod
        def CloseHandle(handle):
            pass

    monkeypatch.setattr(sl, "_kernel32", _K)
    sync = sl.SAI2Sync()
    assert sync._connect() is False
    assert sync.last_error == "module_info_failed"


def test_reason_no_signature(monkeypatch):
    monkeypatch.setattr(sl, "_find_process", lambda name: 4242)
    monkeypatch.setattr(sl, "_get_module_info", lambda pid, name: (BASE, 0x1000))
    monkeypatch.setattr(sl, "_scan_pattern_masked", lambda h, b, s, p: None)
    monkeypatch.setattr(sl, "_read_memory", _reader({}))

    class _K:
        @staticmethod
        def OpenProcess(*args):
            return 0x99

        @staticmethod
        def CloseHandle(handle):
            pass

    monkeypatch.setattr(sl, "_kernel32", _K)
    sync = sl.SAI2Sync()
    assert sync._connect() is False
    assert sync.last_error == "no_signature"


def test_status_reports_resolved_method(monkeypatch):
    sync, _ = _connected(monkeypatch)
    status = sync.status()
    assert status["connected"] is True
    assert status["reason"] == "ok"
    assert status["resolvedBy"] == "signature:pre-2024-sai2"
