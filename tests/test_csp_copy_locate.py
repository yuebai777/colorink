"""Regression coverage for the CSP 5.1 copy-locate path (memory sync mode).

The v1.6.7 regression these tests pin down: ``_locate_hsv_copies`` searched
the whole CSP address space for the *current* 12-byte HSV pattern, with no
cap on the hit count and no cache for a failed attempt. Pure black — CSP's
default drawing color — encodes as twelve zero bytes and matches at every
byte offset of every zero-filled page, so the scan collected hundreds of
millions of addresses on *every* write. The polling thread never came back
and CSP memory-mode sync (i.e. everything except companion/smartphone mode)
stopped syncing colors altogether.
"""

from core.csp_brush_link import CSPSync
from core.csp_brush_link.memory import _collect_window_hits

MODULE_BASE = 0x1000
BASE_OFFSET = 0x0556BFC8
TARGET = 0x2000
MAIN_MIRROR = TARGET + 0x3C  # base/UI mirror of the main HSV block
COPY_A = 0x500000            # authoritative brush copies
COPY_B = 0x600000

BLACK = b"\x00" * 12         # H=0 S=0 V=0 — the pathological pattern
RED = bytes.fromhex("00000000" "ffffffff" "ffffffff")
BLUE = bytes.fromhex("aaaaaaaa" "ffffffff" "88888888")


class _FakeMem:
    """Coherent u32 memory model: read_bytes/write_bytes and read_int/write_int
    address the same store, like the real process memory does."""

    def __init__(self):
        self.u32: dict[int, int] = {}
        self.writes: list[tuple[int, bytes]] = []

    # --- pymem surface -------------------------------------------------
    def read_longlong(self, address):
        return TARGET if address == MODULE_BASE + BASE_OFFSET else 0

    def read_int(self, address):
        return self.u32.get(address, 0)

    def write_int(self, address, value):
        self.u32[address] = value & 0xFFFFFFFF

    def read_bytes(self, address, size):
        return b"".join(
            self.u32.get(address + 4 * i, 0).to_bytes(4, "little")
            for i in range(size // 4)
        )

    def write_bytes(self, address, value, size):
        self.writes.append((address, bytes(value)))
        for i in range(size // 4):
            self.u32[address + 4 * i] = int.from_bytes(value[4 * i:4 * i + 4], "little")

    # --- test helper ---------------------------------------------------
    def seed(self, address, raw):
        for i in range(len(raw) // 4):
            self.u32[address + 4 * i] = int.from_bytes(raw[4 * i:4 * i + 4], "little")


def _make_sync():
    sync = CSPSync.__new__(CSPSync)
    sync.color_format = "rgb_u32"
    sync.base_offset = BASE_OFFSET
    sync.module_base = MODULE_BASE
    sync.target = TARGET
    sync.pm = _FakeMem()
    sync._last_hsv_h = 0.0
    sync._last_hsv_s = 0.0
    sync._sub_copy_addrs = None
    sync._main_copy_addrs = None
    sync._sub_copy_addrs_known = None
    sync._main_copy_addrs_known = None
    sync._copy_scan_ts = {}
    return sync


def _trivial_search(pattern, max_hits=0):
    """A scan of a pattern that matches far too often to be identifying."""
    count = max_hits or CSPSync._SUB_COPY_LIMIT + 1
    return [0x900000 + 12 * i for i in range(count)]


# ── the hit-count explosion itself ────────────────────────────────────────

def test_window_scan_counts_every_overlapping_offset():
    """Documents the blow-up: twelve zero bytes match at (almost) every byte."""
    hits: list[int] = []
    assert _collect_window_hits(b"\x00" * 4096, BLACK, 0, hits, 0) is False
    assert len(hits) == 4096 - 12 + 1  # ~1.05 million per 1 MiB page


def test_window_scan_stops_at_max_hits():
    hits: list[int] = []
    stopped = _collect_window_hits(b"\x00" * 4096, BLACK, 0x10000, hits, 201)
    assert stopped is True
    assert len(hits) == 201


def test_locate_never_requests_an_uncapped_scan():
    sync = _make_sync()
    seen = {}

    def fake_search(pattern, max_hits=0):
        seen["max_hits"] = max_hits
        return [COPY_A, MAIN_MIRROR]

    sync._search_pattern = fake_search
    sync.pm.seed(MAIN_MIRROR, RED)
    sync.pm.seed(COPY_A, RED)
    sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    assert seen["max_hits"] == CSPSync._SUB_COPY_LIMIT + 1


# ── no full rescan per write ───────────────────────────────────────────────

def test_trivial_pattern_is_not_rescanned_on_every_write():
    """The actual freeze: a failed locate must not scan again on the next write."""
    sync = _make_sync()
    calls = []

    def counting_search(pattern, max_hits=0):
        calls.append(pattern)
        return _trivial_search(pattern, max_hits)

    sync._search_pattern = counting_search
    sync.pm.seed(MAIN_MIRROR, BLACK)

    first = sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)
    second = sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)
    third = sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)

    assert first == second == third == [MAIN_MIRROR]
    assert len(calls) == 1, "one full address-space scan per write wedges the sync thread"
    # A degraded result must never be cached: it would validate against the
    # value we wrote ourselves and lock the degradation in forever.
    assert sync._main_copy_addrs is None


def test_scan_cooldown_expires():
    sync = _make_sync()
    calls = []

    def counting_search(pattern, max_hits=0):
        calls.append(pattern)
        return _trivial_search(pattern, max_hits)

    sync._search_pattern = counting_search
    sync.pm.seed(MAIN_MIRROR, BLACK)
    sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)
    assert len(calls) == 1
    # Pretend the cooldown elapsed — a later write may retry the locate.
    sync._copy_scan_ts["_main_copy_addrs"] -= CSPSync._COPY_RESCAN_INTERVAL + 1
    sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)
    assert len(calls) == 2


def test_valid_cache_is_reused_without_scanning():
    sync = _make_sync()
    calls = []

    def counting_search(pattern, max_hits=0):
        calls.append(pattern)
        return [COPY_A, COPY_B, MAIN_MIRROR]

    sync._search_pattern = counting_search
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)

    first = sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    second = sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    assert sorted(first) == sorted([COPY_A, COPY_B, MAIN_MIRROR])
    assert first == second
    assert len(calls) == 1


# ── degraded states fall back to the verified copy set ────────────────────

def test_trivial_pattern_reuses_the_remembered_copy_set():
    sync = _make_sync()
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: [COPY_A, COPY_B, MAIN_MIRROR]
    sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    assert sync._main_copy_addrs_known

    # The mirror now holds pure black (a previous degraded write), so the
    # cache is stale and the pattern is unidentifiable.
    sync.pm.seed(MAIN_MIRROR, BLACK)
    sync._copy_scan_ts = {}
    sync._search_pattern = _trivial_search
    addrs = sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C)
    assert sorted(addrs) == sorted([COPY_A, COPY_B, MAIN_MIRROR])


def test_mirror_only_hit_reuses_the_remembered_copy_set():
    sync = _make_sync()
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: [COPY_A, COPY_B, MAIN_MIRROR]
    sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)

    # Mirror moved on alone; a scan for its value only finds the mirror.
    sync.pm.seed(MAIN_MIRROR, BLUE)
    sync._copy_scan_ts = {}
    sync._search_pattern = lambda pattern, max_hits=0: [MAIN_MIRROR]
    addrs = sync._locate_hsv_copies(BLUE, "_main_copy_addrs", 0x3C)
    assert sorted(addrs) == sorted([COPY_A, COPY_B, MAIN_MIRROR])
    assert sync._main_copy_addrs is None  # degraded result stays uncached


def test_remembered_set_is_refused_when_copies_disagree():
    """A recycled allocation must not be written to."""
    sync = _make_sync()
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: [COPY_A, COPY_B, MAIN_MIRROR]
    sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)

    sync.pm.seed(COPY_A, BLUE)  # copies no longer agree with each other
    sync.pm.seed(MAIN_MIRROR, BLACK)
    sync._copy_scan_ts = {}
    sync._search_pattern = _trivial_search
    assert sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C) == [MAIN_MIRROR]


def test_remembered_set_is_refused_after_the_slot_pointer_moved():
    sync = _make_sync()
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: [COPY_A, COPY_B, MAIN_MIRROR]
    sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)

    sync.target = 0x9000  # CSP re-allocated the color slot
    sync._copy_scan_ts = {}
    sync._search_pattern = _trivial_search
    assert sync._locate_hsv_copies(BLACK, "_main_copy_addrs", 0x3C) == [0x9000 + 0x3C]


# ── end to end through the public write path ──────────────────────────────

def test_write_from_a_degraded_state_reaches_the_brush_copies():
    sync = _make_sync()
    for addr in (MAIN_MIRROR, COPY_A, COPY_B):
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: [COPY_A, COPY_B, MAIN_MIRROR]
    assert sync.set_color(255, 0, 0) is True

    # Mirror drifted to black alone → unidentifiable pattern on the next write.
    sync.pm.seed(MAIN_MIRROR, BLACK)
    sync._copy_scan_ts = {}
    sync._search_pattern = _trivial_search
    sync.pm.writes.clear()
    assert sync.set_color(0, 178, 255) is True
    assert {addr for addr, _ in sync.pm.writes} == {MAIN_MIRROR, COPY_A, COPY_B}


def test_sub_slot_shares_the_capped_locate_path():
    """Every scan stays capped, and the next write does not rescan.

    A degraded SUB locate additionally tries to derive the copy set from the
    MAIN copies (see ``_sub_copies_from_main``), which costs one further capped
    scan when the main set is not already cached. Here BOTH slots hold black,
    so that derivation scan also comes back trivial and the write still falls
    back to the mirror. What must hold is that no scan is uncapped and that the
    second write triggers none at all.
    """
    sync = _make_sync()
    calls = []

    def counting_search(pattern, max_hits=0):
        calls.append(max_hits)
        return _trivial_search(pattern, max_hits)

    sync._search_pattern = counting_search
    sync.pm.seed(TARGET + 0x9C, BLACK)
    assert sync.set_color(0, 0, 0, color_index=1) is True
    scans_after_first = len(calls)
    assert sync.set_color(10, 20, 30, color_index=1) is True

    assert all(cap == CSPSync._SUB_COPY_LIMIT + 1 for cap in calls), \
        f"an uncapped scan slipped through: {calls}"
    assert scans_after_first <= 2, \
        f"a single degraded sub write must not scan more than twice: {calls}"
    assert len(calls) == scans_after_first, \
        f"the second write rescanned instead of using the cooldown: {calls}"
    assert sync._sub_copy_addrs is None


# ── main/sub cross-talk when both slots hold the same colour ──────────────
# Verified live on CSP 5.1: main and sub are two sub-objects 0x60 bytes apart,
# so while both hold the same colour a value scan matches both and a main
# write silently overwrote the sub colour.

MAIN_MIRROR_B = TARGET + 0x9C          # sub mirror of the same struct
COPY_A_SUB = COPY_A + 0x60
COPY_B_SUB = COPY_B + 0x60


def test_main_locate_drops_the_sub_slot_hits():
    sync = _make_sync()
    hits = [COPY_A, COPY_A_SUB, COPY_B, COPY_B_SUB, MAIN_MIRROR, MAIN_MIRROR_B]
    for addr in hits:
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: list(hits)
    addrs = sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    assert sorted(addrs) == sorted([COPY_A, COPY_B, MAIN_MIRROR])
    assert COPY_A_SUB not in addrs and MAIN_MIRROR_B not in addrs


def test_sub_locate_drops_the_main_slot_hits():
    sync = _make_sync()
    hits = [COPY_A, COPY_A_SUB, COPY_B, COPY_B_SUB, MAIN_MIRROR, MAIN_MIRROR_B]
    for addr in hits:
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: list(hits)
    addrs = sync._locate_hsv_copies(RED, "_sub_copy_addrs", 0x9C)
    assert sorted(addrs) == sorted([COPY_A_SUB, COPY_B_SUB, MAIN_MIRROR_B])
    assert COPY_A not in addrs and MAIN_MIRROR not in addrs


def test_differing_slot_colours_are_not_filtered():
    """The normal case must be untouched: the sibling's bytes do not match."""
    sync = _make_sync()
    hits = [COPY_A, COPY_B, MAIN_MIRROR]
    for addr in hits:
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: list(hits)
    addrs = sync._locate_hsv_copies(RED, "_main_copy_addrs", 0x3C)
    assert sorted(addrs) == sorted(hits)


def test_main_write_does_not_leak_into_the_sub_slot():
    """End to end: identical colours in both slots, write main only."""
    sync = _make_sync()
    hits = [COPY_A, COPY_A_SUB, MAIN_MIRROR, MAIN_MIRROR_B]
    for addr in hits:
        sync.pm.seed(addr, RED)
    sync._search_pattern = lambda pattern, max_hits=0: list(hits)

    assert sync.set_color(0, 255, 0, color_index=0) is True
    written = {addr for addr, _ in sync.pm.writes}
    assert written == {COPY_A, MAIN_MIRROR}
    # The sub mirror still holds the original colour.
    assert sync.pm.read_bytes(MAIN_MIRROR_B, 12) == RED
    assert sync.pm.read_bytes(COPY_A_SUB, 12) == RED

# ── first background switch of a session must reach the brush ──────────────
# Reported symptom: switching foreground -> background the FIRST time left
# CSP's brush on the old colour; the second switch worked. _sync_debug.log:
#     _locate_hsv_copies: >200 hits (trivial pattern) - mirror-only, NOT cached
#     set_color (rgb_u32 sub): RGB=[255, 255, 255] -> 1 copies
#     _locate_hsv_copies: located 7 copies
#     set_color (rgb_u32): RGB=[143, 73, 111] -> 7 copies
# CSP's default background is WHITE, whose 12-byte HSV-u32 pattern matches far
# past _SUB_COPY_LIMIT, so the sub scan could not identify the brush copies and
# there was no verified set to fall back on yet.

WHITE = bytes.fromhex("00000000" "00000000" "ffffffff")  # H=0 S=0 V=max


def _seed_struct(mem, main_addr, main_raw, sub_raw):
    """Seed one CSP colour struct: main block + its sub block 0x60 further on."""
    mem.seed(main_addr, main_raw)
    mem.seed(main_addr + 0x60, sub_raw)


def test_first_sub_write_reaches_the_brush_via_the_main_copy_stride():
    """The reported bug: a trivial (white) background still reaches the brush."""
    sync = _make_sync()
    # Two structs: distinctive main colour, trivial white background.
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)

    def search(pattern, max_hits=0):
        if pattern == WHITE:
            return _trivial_search(pattern, max_hits)   # >200 hits, unusable
        return [COPY_A, MAIN_MIRROR]                     # main locates fine

    sync._search_pattern = search
    sync.pm.writes.clear()
    assert sync.set_color(10, 20, 30, color_index=1) is True

    written = {addr for addr, _ in sync.pm.writes}
    assert written == {MAIN_MIRROR + 0x60, COPY_A + 0x60}, (
        "the first background write did not reach the authoritative sub copy "
        f"(wrote {[hex(a) for a in sorted(written)]})"
    )


def test_derived_sub_set_is_cached_so_later_writes_need_no_scan():
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)
    calls = []

    def search(pattern, max_hits=0):
        calls.append(pattern)
        if pattern == WHITE:
            return _trivial_search(pattern, max_hits)
        return [COPY_A, MAIN_MIRROR]

    sync._search_pattern = search
    assert sync.set_color(10, 20, 30, color_index=1) is True
    scans = len(calls)
    # The derived set is verified against live memory, so it may be cached.
    assert sync._sub_copy_addrs is not None
    assert sorted(sync._sub_copy_addrs) == sorted([MAIN_MIRROR + 0x60, COPY_A + 0x60])
    assert sync.set_color(40, 50, 60, color_index=1) is True
    assert len(calls) == scans, "a cached derived set must not trigger a rescan"


def test_derivation_is_rejected_when_the_sibling_does_not_match():
    """Safety guard: never write to addresses that are not really sub fields."""
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    # COPY_A's sibling holds something else -> not a coherent sub copy set.
    sync.pm.seed(COPY_A, RED)
    sync.pm.seed(COPY_A + 0x60, BLUE)

    sync._search_pattern = lambda pattern, max_hits=0: (
        _trivial_search(pattern, max_hits) if pattern == WHITE
        else [COPY_A, MAIN_MIRROR]
    )
    sync.pm.writes.clear()
    assert sync.set_color(10, 20, 30, color_index=1) is True

    written = {addr for addr, _ in sync.pm.writes}
    assert written == {TARGET + 0x9C}, "must fall back to the mirror only"
    assert sync._sub_copy_addrs is None


def test_main_locate_remembers_the_sub_siblings_for_free():
    """A successful main locate records the sub set without any extra scan."""
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)
    calls = []

    def search(pattern, max_hits=0):
        calls.append(pattern)
        return [COPY_A, MAIN_MIRROR]

    sync._search_pattern = search
    assert sync.set_color(255, 0, 0) is True          # main write
    assert len(calls) == 1, "remembering the siblings must not cost a scan"
    assert sync._sub_copy_addrs_known is not None
    assert sorted(sync._sub_copy_addrs_known) == sorted(
        [MAIN_MIRROR + 0x60, COPY_A + 0x60])


def test_cooldown_still_uses_a_cached_main_set_without_scanning():
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)
    sync._main_copy_addrs = [COPY_A, MAIN_MIRROR]
    sync._copy_scan_ts = {"_sub_copy_addrs": __import__("time").monotonic()}
    calls = []
    sync._search_pattern = lambda pattern, max_hits=0: calls.append(pattern) or []

    sync.pm.writes.clear()
    assert sync.set_color(10, 20, 30, color_index=1) is True

    assert calls == [], "the cooldown branch must never scan"
    written = {addr for addr, _ in sync.pm.writes}
    assert written == {MAIN_MIRROR + 0x60, COPY_A + 0x60}


# ── priming: the ~1s locate scan must not sit on the interactive path ──────
# Measured on a live CSP 5.1 process: a cold locate is ~980 ms, a warm one is
# 0.1 ms, and the copies are spread over ~84 MB so a bounded local scan cannot
# replace the full one. That second was paid by the first colour WRITE, so the
# user switched slot, drew immediately, and the first stroke still used the
# previous colour.


def test_prime_populates_both_caches_without_writing_to_csp():
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, BLUE)
    _seed_struct(sync.pm, COPY_A, RED, BLUE)
    sync._search_pattern = lambda pattern, max_hits=0: (
        [COPY_A, MAIN_MIRROR] if pattern == RED
        else [COPY_A + 0x60, MAIN_MIRROR + 0x60]
    )
    sync.pm.writes.clear()

    assert sync.prime_copy_caches() is True
    assert sync.has_sub_copy_cache() is True
    assert sync.sub_copies_are_known() is True
    assert sync._main_copy_addrs
    assert sync.pm.writes == [], "priming must never write to CSP"


def test_prime_makes_the_next_sub_write_scan_free():
    """The point of priming: the interactive write is on the warm path."""
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, BLUE)
    _seed_struct(sync.pm, COPY_A, RED, BLUE)
    calls = []

    def search(pattern, max_hits=0):
        calls.append(pattern)
        return ([COPY_A, MAIN_MIRROR] if pattern == RED
                else [COPY_A + 0x60, MAIN_MIRROR + 0x60])

    sync._search_pattern = search
    assert sync.prime_copy_caches() is True
    scans_during_prime = len(calls)
    assert scans_during_prime > 0

    sync.pm.writes.clear()
    assert sync.set_color(10, 20, 30, color_index=1) is True

    assert len(calls) == scans_during_prime, \
        "the write rescanned despite a primed cache"
    written = {addr for addr, _ in sync.pm.writes}
    assert written == {MAIN_MIRROR + 0x60, COPY_A + 0x60}


def test_prime_recovers_the_sub_set_when_the_background_is_trivial():
    """CSP's default background is white -> only the main scan can identify."""
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)
    sync._search_pattern = lambda pattern, max_hits=0: (
        _trivial_search(pattern, max_hits) if pattern == WHITE
        else [COPY_A, MAIN_MIRROR]
    )

    sync.prime_copy_caches()

    assert sync._sub_copy_addrs_known is not None, \
        "priming did not recover a sub copy set from the main copies"
    assert sorted(sync._sub_copy_addrs_known) == sorted(
        [MAIN_MIRROR + 0x60, COPY_A + 0x60])


def test_prime_is_a_noop_for_legacy_slot_layouts():
    sync = _make_sync()
    sync.color_format = "u16x2_dup"
    called = []
    sync._search_pattern = lambda pattern, max_hits=0: called.append(pattern) or []
    assert sync.prime_copy_caches() is False
    assert called == []


def test_trivial_background_prime_is_not_repeated_forever():
    """A remembered-only result still counts as primed.

    Otherwise ``prime_copy_caches`` reports failure, the worker's throttle lets
    it through again, and a ~1s scan runs every 30 s for as long as the
    background stays white.
    """
    sync = _make_sync()
    _seed_struct(sync.pm, MAIN_MIRROR, RED, WHITE)
    _seed_struct(sync.pm, COPY_A, RED, WHITE)
    sync._search_pattern = lambda pattern, max_hits=0: (
        _trivial_search(pattern, max_hits) if pattern == WHITE
        else [COPY_A, MAIN_MIRROR]
    )

    assert sync.prime_copy_caches() is True, \
        "a recovered (remembered) sub set must report as primed"
    assert sync.has_sub_copy_cache() is False, \
        "a remembered set must NOT be installed as the live cache"
    assert sync.sub_copies_are_known() is True
