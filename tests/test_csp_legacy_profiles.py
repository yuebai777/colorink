"""Coverage for the LEGACY CSP profiles (4.x / 5.0, ``u16x2_dup`` slot layout).

These builds use a completely different slot layout from CSP 5.1: every colour
space (rgb/cmyk/hsv/hls) has its own u32 channel block anchored at ``r_off``,
and ``set_color`` writes all of them while ``get_color`` picks whichever space
carries data (:func:`resolve_active_rgb`).

The suite had no coverage for that path at all, which makes any change to the
5.1 copy-locate machinery impossible to clear without a second CSP install.
The guard tests below pin the separation: the legacy path must never reach
``_search_pattern`` / ``_locate_hsv_copies`` and friends, so 5.1-only fixes
cannot regress a 4.x / 5.0 user.
"""

import pytest

from core.brush_color_spaces import (
    SPACE_ORDER,
    build_space_offsets,
    encode_space_values,
    rgb_to_space_values,
)
from core.csp_brush_link import (
    _PROFILE_INDEX,
    CSPSync,
    _detect_build_from_version,
)

MODULE_BASE = 0x1000
TARGET = 0x30000
R_OFF = 0x20
OFFS = build_space_offsets(R_OFF)


class _LegacyPm:
    """u32 store for the legacy per-space channel blocks."""

    def __init__(self, base_offset: int):
        self.base_offset = base_offset
        self.u32: dict[int, int] = {}
        self.int_writes: list[tuple[int, int]] = []

    def read_longlong(self, address):
        return TARGET if address == MODULE_BASE + self.base_offset else 0

    def read_int(self, address):
        return self.u32.get(address, 0)

    def write_int(self, address, value):
        self.u32[address] = value & 0xFFFFFFFF
        self.int_writes.append((address, value & 0xFFFFFFFF))

    def read_bytes(self, address, size):
        return b"".join(
            self.u32.get(address + 4 * i, 0).to_bytes(4, "little")
            for i in range(size // 4)
        )

    def write_bytes(self, address, value, size):  # pragma: no cover - guard
        raise AssertionError("legacy path must not do 12-byte copy writes")

    def seed_space(self, space_name: str, values: dict[str, int]) -> None:
        for off, raw in zip(OFFS[space_name], encode_space_values(space_name, values)):
            self.u32[TARGET + off] = raw


def _make_legacy_sync(version_key: str = "csp4.x") -> CSPSync:
    profile = _PROFILE_INDEX[version_key]
    sync = CSPSync.__new__(CSPSync)
    sync._profile = profile
    sync.current_version = profile.key
    sync.process_name = profile.process_name
    sync.base_offset = profile.base_offset
    sync.intermediate_offset = profile.intermediate_offset
    sync.aob_signature = profile.aob_signature
    sync.color_format = profile.color_format
    sync.module_base = MODULE_BASE
    sync.target = TARGET
    sync.pid = 4242
    sync.r_off = R_OFF
    sync.g_off = 0x24
    sync.b_off = 0x28
    sync.space_offsets = OFFS
    sync._last_hsv_h = 0.0
    sync._last_hsv_s = 0.0
    sync._resolve_fail_count = 0
    sync.pm = _LegacyPm(profile.base_offset)
    return sync


def _forbid_copy_machinery(sync: CSPSync) -> None:
    """Make every 5.1-only helper explode if the legacy path touches it."""
    def boom(*_a, **_k):
        raise AssertionError("5.1 copy-locate machinery reached from the "
                             "legacy u16x2_dup path")

    for name in ("_search_pattern", "_locate_hsv_copies", "_drop_sibling_slot_hits",
                 "_remembered_copies", "_copy_cache_is_live",
                 "_read_rgb_u32", "_write_rgb_u32", "_write_sub_color"):
        setattr(sync, name, boom)


# ── profile registry is untouched ─────────────────────────────────────────

@pytest.mark.parametrize("key,base_offset,fmt", [
    ("csp4.x", 0x0518C2C0, "u16x2_dup"),
    ("csp5.x", 0x05449DB0, "u16x2_dup"),
    ("csp5.1", 0x0556BFC8, "rgb_u32"),
])
def test_profile_constants(key, base_offset, fmt):
    profile = _PROFILE_INDEX[key]
    assert profile.base_offset == base_offset
    assert profile.color_format == fmt
    assert profile.process_name == "CLIPStudioPaint.exe"


def test_csp5_x_profile_keeps_its_intermediate_and_aob_metadata():
    profile = _PROFILE_INDEX["csp5.x"]
    assert profile.intermediate_offset is None
    assert profile.aob_offset == 0x0D
    assert profile.aob_signature


@pytest.mark.parametrize("version,expected", [
    ((4, 0, 0, 0), "csp4.x"),
    ((4, 2, 7, 0), "csp4.x"),
    ((5, 0, 0, 0), "csp5.x"),
    ((5, 0, 9, 9), "csp5.x"),
    ((5, 1, 0, 0), "csp5.1"),
])
def test_auto_detection_still_maps_legacy_builds(version, expected):
    assert _detect_build_from_version(version) == expected


# ── legacy read / write path ──────────────────────────────────────────────

def test_legacy_get_color_reads_the_active_space():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 200, "g": 100, "b": 50})
    out = sync.get_color()
    assert out == {"r": 200, "g": 100, "b": 50}


def test_legacy_get_color_falls_back_to_hsv_when_rgb_block_is_zero():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    # CSP zeroes the blocks it is not currently using (verified live on 5.1
    # too): rgb empty, hsv carries the colour.
    sync.pm.seed_space("hsv", {"h": 120, "s": 100, "v": 100})
    out = sync.get_color()
    assert out == {"r": 0, "g": 255, "b": 0}


def test_legacy_set_color_writes_every_space_block():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 1, "g": 1, "b": 1})  # make the slot adoptable

    assert sync.set_color(200, 100, 50) is True

    written = {addr for addr, _ in sync.pm.int_writes}
    for space_name in SPACE_ORDER:
        for off in OFFS[space_name]:
            assert TARGET + off in written, f"{space_name} block not written"
    # RGB block holds exactly the requested colour.
    for off, raw in zip(OFFS["rgb"], encode_space_values(
            "rgb", rgb_to_space_values("rgb", {"r": 200, "g": 100, "b": 50}))):
        assert sync.pm.u32[TARGET + off] == raw


def test_legacy_set_color_round_trips_through_get_color():
    sync = _make_legacy_sync("csp5.x")
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 1, "g": 1, "b": 1})
    assert sync.set_color(17, 200, 233) is True
    assert sync.get_color() == {"r": 17, "g": 200, "b": 233}


def test_legacy_transparent_write_is_refused_not_crashing():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 10, "g": 10, "b": 10})
    assert sync.set_color(255, 0, 0, transparent=True) is False
    # The colour must stay untouched when the flag is unsupported.
    assert sync.get_color() == {"r": 10, "g": 10, "b": 10}


def test_legacy_status_reports_connected_via_space_addresses():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 5, "g": 5, "b": 5})
    status = sync.status()
    assert status["connected"] is True
    assert status["pid"] == 4242
    assert status["version"] == "csp4.x"
    assert status["target"] == f"0x{TARGET:X}"


def test_legacy_source_space_write_keeps_float_precision():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 1, "g": 1, "b": 1})
    assert sync.set_color(0, 0, 0, source_space="hsv",
                          source_values={"h": 180.4, "s": 50.0, "v": 50.0}) is True
    h_raw = sync.pm.u32[TARGET + OFFS["hsv"][0]]
    assert h_raw == round(180.4 / 360.0 * 0xFFFFFFFF)


def test_legacy_dump_walks_the_slot_without_copy_machinery():
    sync = _make_legacy_sync()
    _forbid_copy_machinery(sync)
    sync.pm.seed_space("rgb", {"r": 9, "g": 9, "b": 9})
    out = sync.dump()
    assert out["target"] == f"0x{TARGET:X}"
    assert [row["offset"] for row in out["rows"]][:3] == ["0x0", "0x4", "0x8"]
    assert {entry["space"] for entry in out["spaces"]} == set(SPACE_ORDER)


# ── the one shared change: set_version("auto") ────────────────────────────

def test_auto_does_not_disturb_a_legacy_profile():
    sync = _make_legacy_sync("csp5.x")
    assert sync.set_version("auto") is False
    assert sync.current_version == "csp5.x"
    assert sync.color_format == "u16x2_dup"
    assert sync.base_offset == 0x05449DB0
    assert sync.pm is not None


def test_explicit_legacy_pin_still_switches():
    sync = _make_legacy_sync("csp4.x")
    assert sync.set_version("csp5.0") is True          # legacy alias
    assert sync.current_version == "csp5.x"
    assert sync.color_format == "u16x2_dup"
    assert sync.pm is None                              # forces a reconnect


def test_the_guard_is_not_vacuous():
    """Counter-proof for every ``_forbid_copy_machinery`` test above.

    Flipping the very same stub to the 5.1 layout must trip the guard, which
    is what makes "the legacy path never reaches it" a real assertion rather
    than a test that could never fail.
    """
    sync = _make_legacy_sync()
    sync.color_format = "rgb_u32"
    sync._sub_copy_addrs = None
    sync._main_copy_addrs = None
    _forbid_copy_machinery(sync)
    with pytest.raises(AssertionError):
        sync.set_color(200, 100, 50)
