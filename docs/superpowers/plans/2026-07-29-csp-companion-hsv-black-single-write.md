# CSP Companion HSV Black Single-Write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CSP smartphone companion mode send the user's exact HSV black selection in one protocol message instead of flashing `V=5%` first.

**Architecture:** Keep the UI, HSV coordinate calculation, thread handoff, and companion read path unchanged. Modify only the explicit-HSV black branch in `CSPCompanionSync.set_color()` so it sends one `SetCurrentColor` payload with the caller-provided `H/S/V`, while preserving the branch's existing state updates and response handling.

**Tech Stack:** Python 3.10+, standard-library JSON and socket protocol helpers.

## Global Constraints

- Modify only the explicit HSV, near-zero-value branch in `core/csp_companion_sync.py`.
- Preserve normal-brightness writes and RGB-derived black hue/saturation fallback behavior.
- Do not change HSV UI coordinates, `hsv_u32` generation, companion reads, dedup thresholds, or other sync backends.
- Do not add a test framework to this repository; use an isolated in-memory protocol driver.
- Do not alter, clean up, stage, or commit unrelated working-tree changes.

---

### Task 1: Replace the HSV Black Flash With One Exact Write

**Files:**
- Modify: `core/csp_companion_sync.py:858`
- Reference: `docs/superpowers/specs/2026-07-29-csp-companion-hsv-black-write-design.md`

**Interfaces:**
- Consumes: `CSPCompanionSync.set_color(self, r: int, g: int, b: int, hsv_u32: tuple = None) -> bool` and caller-provided uint32-scaled `(h, s, v)`.
- Produces: one `SetCurrentColor` message containing the unchanged explicit `HSVColorH`, `HSVColorS`, and `HSVColorV`, including when `HSVColorV == 0`.

- [ ] **Step 1: Run an in-memory regression driver and verify the current two-write behavior fails the new requirement**

Run from the repository root in PowerShell:

```powershell
@'
import json

from core.csp_companion_sync import (
    CSPCompanionSync,
    _MAX_U32,
    _parse_messages,
)

sync = CSPCompanionSync.__new__(CSPCompanionSync)
sync._connected = True
sync._current_color = None
sync._last_hue_u32 = 0
sync._last_sat_u32 = 0
sync._ensure_heartbeat = lambda: None
sent = []
sync._send_raw = sent.append
sync._recv_messages = lambda timeout=0.0: []

h_u32 = round(_MAX_U32 * 0.5)
s_u32 = round(_MAX_U32 * 0.75)
assert sync.set_color(0, 0, 0, hsv_u32=(h_u32, s_u32, 0))
assert len(sent) == 1, f"expected one write, got {len(sent)}"

_, command, detail = _parse_messages(sent[0])[0]
payload = json.loads(detail)
assert command == "SetCurrentColor"
assert payload["HSVColorH"] == h_u32
assert payload["HSVColorS"] == s_u32
assert payload["HSVColorV"] == 0
assert sync._last_sat_u32 == s_u32
assert sync._current_color == {"r": 0, "g": 0, "b": 0}
'@ | python -
```

Expected: FAIL with `AssertionError: expected one write, got 2`.

- [ ] **Step 2: Replace only the explicit-HSV black branch with a direct write**

Replace the branch beginning with `else:` and the `Black, explicit HSV` comment with:

```python
            else:
                # CSP reads explicit hue and saturation directly at V=0.
                self._last_sat_u32 = s_u32
                self._send_raw(_build_message("SetCurrentColor", {
                    "ColorSpaceKind": "HSV",
                    "IsColorTransparent": False,
                    "HSVColorH": h_u32,
                    "HSVColorS": s_u32,
                    "HSVColorV": v_u32,
                    "ColorIndex": 0,
                }))
                _ = self._recv_messages(timeout=0.1)
                self._current_color = {"r": r, "g": g, "b": b}
                _log(f"set_color: RGB=[{r}, {g}, {b}] -> H={h_u32} S={s_u32} V={v_u32}")
                return True
```

Do not modify the preceding normal-brightness or implicit-HSV black branches.

- [ ] **Step 3: Re-run the in-memory driver and verify the exact single payload**

Run:

```powershell
@'
import json

from core.csp_companion_sync import (
    CSPCompanionSync,
    _MAX_U32,
    _parse_messages,
)

sync = CSPCompanionSync.__new__(CSPCompanionSync)
sync._connected = True
sync._current_color = None
sync._last_hue_u32 = 0
sync._last_sat_u32 = 0
sync._ensure_heartbeat = lambda: None
sent = []
sync._send_raw = sent.append
sync._recv_messages = lambda timeout=0.0: []

h_u32 = round(_MAX_U32 * 0.5)
s_u32 = round(_MAX_U32 * 0.75)
assert sync.set_color(0, 0, 0, hsv_u32=(h_u32, s_u32, 0))
assert len(sent) == 1, f"expected one write, got {len(sent)}"

_, command, detail = _parse_messages(sent[0])[0]
payload = json.loads(detail)
assert command == "SetCurrentColor"
assert payload["HSVColorH"] == h_u32
assert payload["HSVColorS"] == s_u32
assert payload["HSVColorV"] == 0
assert sync._last_sat_u32 == s_u32
assert sync._current_color == {"r": 0, "g": 0, "b": 0}
'@ | python -
```

Expected: exit code 0 with one debug log showing `V=0`; no `positioned via V=5%` suffix.

- [ ] **Step 4: Verify Python syntax without generating repository artifacts**

Run:

```powershell
python -c "from pathlib import Path; p=Path('core/csp_companion_sync.py'); compile(p.read_text(encoding='utf-8'), str(p), 'exec')"
```

Expected: exit code 0 with no output.

- [ ] **Step 5: Verify diagnostics and removed identifiers**

Run `lsp_diagnostics` for `core/csp_companion_sync.py` and require zero errors introduced by this change. Search that file for `_V_FLASH_PCT` and `positioned via V=5%`; both searches must return no matches.

- [ ] **Step 6: Review the final scope**

Read `CSPCompanionSync.set_color()` and confirm only the explicit-HSV black branch changed. Do not create a Git commit unless the user separately requests one.
