#!/usr/bin/env python3
"""READ-ONLY diagnostic: are the SUB colour copies at MAIN copy + _SLOT_STRIDE?

Why this exists
---------------
The first foreground->background switch of a session does not reach CSP's
brush. ``_sync_debug.log`` shows why:

    _locate_hsv_copies: >200 hits (trivial pattern) - mirror-only write, NOT cached
    set_color (rgb_u32 sub): RGB=[255, 255, 255] -> 1 copies      <-- mirror only
    ...
    _locate_hsv_copies: located 7 copies
    set_color (rgb_u32): RGB=[143, 73, 111] -> 7 copies           <-- main is fine

``_write_sub_color`` locates the authoritative copies by searching for the
CURRENT sub colour. CSP's default sub colour is white, whose 12-byte HSV-u32
pattern is eight 0x00 bytes followed by four 0xFF bytes, which matches more
than ``_SUB_COPY_LIMIT`` (200) times. The scan therefore gives up, and the
recovery path (``_remembered_copies``) needs a previously VERIFIED copy set
that nothing ever primes -- ``capture_sub_copies_from_current()`` was written
for exactly that job and has zero callers.

The proposed fix derives the sub copies from the main copies, which locate
reliably, using the stride the codebase already documents as verified live:

    _SLOT_STRIDE = 0x60,  main HSV +0x3C  ->  sub HSV +0x9C

This script only CHECKS that relationship. It never writes to CSP.

Usage
-----
1. Open CSP 5.1.
2. Set the FOREGROUND colour to something distinctive (NOT black/white/grey)
   -- e.g. a saturated orange. This keeps the main pattern non-trivial so the
   main copy scan succeeds.
3. Set the BACKGROUND colour to something ALSO distinctive and DIFFERENT from
   the foreground -- e.g. a saturated blue.
4. Close any running Colorink instance (so nothing else writes to CSP).
5. python tools/diag_sub_copy_stride.py

A PASS means the fix is safe to implement on this build.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.csp_brush_link import CSPSync  # noqa: E402


def _pattern(sync: CSPSync, offsets) -> bytes:
    return b"".join(
        sync._read_u32(sync.target + off).to_bytes(4, "little") for off in offsets
    )


def _fmt(raw: bytes) -> str:
    return " ".join(f"{b:02X}" for b in raw)


def _is_trivial(sync: CSPSync, raw: bytes) -> bool:
    hits = sync._search_pattern(raw, max_hits=sync._SUB_COPY_LIMIT + 1)
    return len(hits) > sync._SUB_COPY_LIMIT


def main() -> int:
    sync = CSPSync()
    sync.set_version("csp5.1")
    if not sync.connect():
        print("FAIL: could not connect to CSP. Is CLIPStudioPaint.exe running?")
        return 1
    if sync.color_format != "rgb_u32":
        print(f"SKIP: this build uses color_format={sync.color_format!r}; the "
              f"stride layout only applies to the rgb_u32 (CSP 5.0/5.1) slots.")
        return 1

    print(f"connected: target=0x{sync.target:X} format={sync.color_format}")

    main_pat = _pattern(sync, sync._RGB_U32_OFFS)
    sub_pat = _pattern(sync, sync._SUB_HSV_OFFS)
    print(f"\nmain HSV pattern (+0x3C): {_fmt(main_pat)}")
    print(f"sub  HSV pattern (+0x9C): {_fmt(sub_pat)}")

    if main_pat == sub_pat:
        print("\nFAIL: foreground and background currently hold the SAME colour. "
              "The scan cannot tell the two slots apart -- set them to different "
              "colours and re-run.")
        return 1

    print(f"\nsub pattern trivial? {_is_trivial(sync, sub_pat)}   "
          "(True is exactly the case that breaks the first switch)")

    # Locate the MAIN copies -- the scan that works reliably today.
    main_hits = sync._search_pattern(main_pat, max_hits=sync._SUB_COPY_LIMIT + 1)
    if len(main_hits) > sync._SUB_COPY_LIMIT:
        print(f"\nFAIL: the FOREGROUND colour is also a trivial pattern "
              f"({len(main_hits)}+ hits). Pick a saturated foreground colour "
              f"and re-run.")
        return 1

    main_base = sync.target + sync._RGB_U32_OFFS[0]
    main_hits = sync._drop_sibling_slot_hits(
        main_hits, sync._RGB_U32_OFFS[0], main_base)
    if main_base not in main_hits:
        main_hits.append(main_base)
    print(f"main copies located: {len(main_hits)}")

    # THE HYPOTHESIS: every main copy has the sub field _SLOT_STRIDE further on.
    stride = sync._SLOT_STRIDE
    sub_base = sync.target + sync._SUB_HSV_OFFS[0]
    matched, mismatched, unreadable = [], [], []
    for addr in main_hits:
        derived = addr + stride
        try:
            got = sync.pm.read_bytes(derived, 12)
        except Exception as exc:
            unreadable.append((derived, str(exc)))
            continue
        (matched if got == sub_pat else mismatched).append((derived, got))

    print(f"\n--- stride check (main copy + 0x{stride:X}) ---")
    print(f"  hold the current sub pattern : {len(matched)}")
    print(f"  hold something else          : {len(mismatched)}")
    print(f"  unreadable                   : {len(unreadable)}")
    for addr, got in mismatched[:5]:
        print(f"    MISMATCH 0x{addr:X}: {_fmt(got)}")
    for addr, exc in unreadable[:5]:
        print(f"    UNREADABLE 0x{addr:X}: {exc}")

    derived_set = {a for a, _ in matched}
    print(f"\n  mirror +0x9C included in the derived set? "
          f"{sub_base in derived_set}")

    ok = (
        len(matched) >= 2
        and not mismatched
        and not unreadable
        and sub_base in derived_set
    )
    print("\n==================================================")
    if ok:
        print("PASS: every located main copy has a sub field 0x60 further on "
              "holding the current background colour.")
        print("      Deriving the sub copy set from the main copy set is safe "
              "on this build -- the fix will make the FIRST fg->bg switch "
              "reach the brush.")
    else:
        print("INCONCLUSIVE / FAIL: do NOT implement the stride-derived write.")
        print("      Send this whole output back so the fix can be reworked.")
    print("==================================================")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
