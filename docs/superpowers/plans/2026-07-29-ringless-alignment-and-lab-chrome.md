# Ringless Alignment and LAB Chrome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Center the asymmetric HLS and LCH ringless slices by their real painted bounds, prevent stale cached positions after resize, and apply the same non-overlapping top control bar to LAB / OKLab.

**Architecture:** `ColorWheel.get_slice_geometry()` remains the only source of paint, indicator, hit-test, and drag coordinates. `RinglessLayout` separates active wheel rendering from mode-wide chrome, while `MainWindow` applies that chrome to both panes and reserves LAB space with its existing layout rather than overlaying controls on the visualizer.

**Tech Stack:** Python 3.10+, PyQt6, pytest, Windows; `QT_QPA_PLATFORM=offscreen` for widget tests.

## Global Constraints

- Full-ring HSV, HLS, and LCH geometry must remain unchanged.
- HLS keeps its current orientation and color conversion; only its ringless anchor changes.
- LCH keeps its neutral axis, sRGB gamut boundary, transparent area, and color conversion; only ringless geometry and cache positioning change.
- Paint, indicator, hit-test, and drag continue consuming one `SliceGeometry`; no per-render offsets.
- Ringless controls apply to wheel and LAB / OKLab pages, but `wheel_enabled` is true only on the wheel page.
- LAB / OKLab color algorithms and lightness-slider semantics do not change.
- Disabled mode restores circle previews, `previewBoxPosition`, zero LAB top margin, and bottom-right buttons.
- Do not add dependencies, broad refactors, compatibility shims, or commits.

## Execution Waves

| Wave | Tasks | Dependency |
|---|---|---|
| 1 | Tasks 1 and 2 | Independent files; execute in parallel |
| 2 | Task 3 | Tasks 1 and 2 green |
| 3 | Task 4 | Integrated behavior green |

---

### Task 1: Separate wheel rendering from mode-wide controls

**Files:**
- Modify: `ui/ringless_mode.py:69-94`
- Modify: `tests/test_ringless_settings.py:101-107`
- Modify: `tests/test_ringless_preview_support.py:51-59`
- Modify: `tests/test_ringless_preview_geometry.py`
- Modify: `tests/test_ringless_preview_rendering.py`

**Interfaces:**
- Produces: `resolve_ringless_layout(config, wheel_page_active, ui_scale)` with `wheel_enabled=config.enabled and wheel_page_active` and `controls_enabled=config.enabled`.
- Produces: `lab_state_layout()` with `wheel_enabled=False` and `controls_enabled=True`.
- Preserves: all metric scaling and side parsing.

- [ ] **Step 1: Write the failing layout-semantics test**

Replace the old inactive-wheel assertion in `tests/test_ringless_settings.py`:

```python
def test_enabled_but_wheel_inactive_disables_wheel_only(self):
    from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

    config = RinglessConfig(enabled=True, controls_side="right")
    layout = resolve_ringless_layout(
        config, wheel_page_active=False, ui_scale=1.0
    )

    assert layout.wheel_enabled is False
    assert layout.controls_enabled is True
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_ringless_settings.py::TestResolveRinglessLayout::test_enabled_but_wheel_inactive_disables_wheel_only -xvs
```

Expected: FAIL because the current layout returns `wheel_enabled=True` and `controls_enabled=False` on LAB.

- [ ] **Step 3: Update LAB preview fixtures and expected presentation**

Change `lab_state_layout()` in `tests/test_ringless_preview_support.py`:

```python
def lab_state_layout() -> RinglessLayout:
    """LAB page with ringless chrome active and wheel rendering inactive."""
    return RinglessLayout(
        wheel_enabled=False,
        controls_enabled=True,
        controls_side="right",
        control_bar_height=39,
        margin=7,
        swatch_width=43,
        swatch_height=24,
        swatch_gap=5,
        corner_radius=4,
        button_gap=4,
    )
```

In `tests/test_ringless_preview_geometry.py`, replace the old LAB-circle assertion:

```python
def test_lab_state_with_controls_enabled_returns_rectangles(self, qapp):
    box = make_preview_box(lab_state_layout())
    assert box._ringless_swatch_rects() is not None
```

In `tests/test_ringless_preview_rendering.py`, require the rectangle path:

```python
def test_lab_state_uses_rectangles_not_circles(self, qapp):
    box = make_preview_box(lab_state_layout())
    box.fg_color = QColor(255, 0, 0)
    box.bg_color = QColor(0, 255, 0)

    with (
        patch.object(box, "draw_circle", wraps=box.draw_circle) as circle_spy,
        patch.object(
            box, "_draw_ringless_paint", wraps=box._draw_ringless_paint
        ) as rectangle_spy,
    ):
        image = QImage(box.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        box.render(image)

    rectangle_spy.assert_called()
    circle_spy.assert_not_called()
```

- [ ] **Step 4: Implement the minimal semantics change**

Update `resolve_ringless_layout()`:

```python
return RinglessLayout(
    wheel_enabled=config.enabled and wheel_page_active,
    controls_enabled=config.enabled,
    controls_side=config.controls_side,
    control_bar_height=max(1, round(_BAR_H * scale)),
    margin=max(1, round(_MARGIN * scale)),
    swatch_width=max(1, round(_SWATCH_W * scale)),
    swatch_height=max(1, round(_SWATCH_H * scale)),
    swatch_gap=max(1, round(_SWATCH_GAP * scale)),
    corner_radius=max(1, round(_CORNER_R * scale)),
    button_gap=max(1, round(_BUTTON_GAP * scale)),
)
```

Update the function docstring so `wheel_page_active` gates wheel rendering, not the shared controls.

- [ ] **Step 5: Verify Task 1 GREEN**

Run:

```powershell
python -m pytest tests/test_ringless_settings.py tests/test_ringless_preview_geometry.py tests/test_ringless_preview_rendering.py -xvs
```

Expected: all tests pass.

---

### Task 2: Center real slice bounds and invalidate cached positions

**Files:**
- Modify: `ui/color_wheel.py:239-285,765-831,858-943,1322-1392`
- Modify: `tests/test_ringless_geometry.py`

**Interfaces:**
- Produces: HLS ringless anchor `available_center_x - radius / 4.0`.
- Produces: OKLCh/RGB ringless anchor `available_center_x - radius / 2.0`.
- Produces: HLS, RGB, and OKLCh cache keys containing `cx` and `cy`.
- Preserves: legacy `get_wheel_geometry()` return path exactly.

- [ ] **Step 1: Add failing true-bounds tests**

Add to `tests/test_ringless_geometry.py`:

```python
def _horizontal_slack(
    wheel, left_bound: float, right_bound: float
) -> tuple[float, float]:
    margin = float(canonical_layout().margin)
    return left_bound - margin, float(wheel.width()) - margin - right_bound


class TestHlsTrueBoundsCentering:
    def test_ringless_hls_bounds_are_centered(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("hls-triangle")
        geometry = wheel.get_slice_geometry()

        left, right = _horizontal_slack(
            wheel,
            geometry.center_x - 0.5 * geometry.radius,
            geometry.center_x + geometry.radius,
        )

        assert left >= -1.0
        assert right >= -1.0
        assert abs(left - right) <= 1.0


class TestOklchTrueBoundsCentering:
    @pytest.mark.parametrize("mode", ["oklch-slice", "rgb-slice"])
    def test_ringless_slice_bounds_are_centered(self, qapp, mode):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode(mode)
        geometry = wheel.get_slice_geometry()

        left, right = _horizontal_slack(
            wheel,
            geometry.center_x - 0.5 * geometry.radius,
            geometry.center_x + 1.5 * geometry.radius,
        )

        assert left >= -1.0
        assert right >= -1.0
        assert abs(left - right) <= 1.0


class TestLegacyCenteringCompatibility:
    @pytest.mark.parametrize("mode", ["hls-triangle", "oklch-slice"])
    def test_full_mode_keeps_legacy_center(self, qapp, mode):
        wheel = make_wheel(400, 400)
        wheel.set_wheel_mode(mode)
        assert wheel.get_slice_geometry().center_x == pytest.approx(200.0)
```

- [ ] **Step 2: Add failing cache-origin tests**

Add `math`, `QImage`, and `Qt` imports, plus a real render helper:

```python
def _render(wheel) -> None:
    image = QImage(wheel.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    wheel.render(image)
```

Add tests that keep radius unchanged while moving the anchor:

```python
class TestCachedSliceOriginAfterResize:
    def test_hls_origin_tracks_width_only_resize(self, qapp):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode("hls-triangle")
        _render(wheel)
        old_min_x = wheel._cached_hls_minx

        wheel.resize(400, 339)
        qapp.processEvents()
        geometry = wheel.get_slice_geometry()
        _render(wheel)

        expected = math.floor(geometry.center_x - 0.5 * geometry.radius)
        assert wheel._cached_hls_minx == expected
        assert wheel._cached_hls_minx != old_min_x

    @pytest.mark.parametrize(
        ("mode", "cache_name"),
        [
            ("oklch-slice", "_cached_oklch_miny"),
            ("rgb-slice", "_cached_rgb_miny"),
        ],
    )
    def test_slice_origin_tracks_height_only_resize(
        self, qapp, mode, cache_name
    ):
        wheel = make_wheel(300, 339, canonical_layout())
        wheel.set_wheel_mode(mode)
        _render(wheel)
        old_min_y = getattr(wheel, cache_name)

        wheel.resize(300, 439)
        qapp.processEvents()
        geometry = wheel.get_slice_geometry()
        _render(wheel)

        expected = math.floor(geometry.center_y - 0.866 * geometry.radius)
        assert getattr(wheel, cache_name) == expected
        assert getattr(wheel, cache_name) != old_min_y
```

Run:

```powershell
python -m pytest tests/test_ringless_geometry.py -xvs -k "TrueBounds or CachedSliceOrigin or LegacyCentering"
```

Expected: true-bounds tests fail because the asymmetric shapes are right-shifted; cache-origin tests fail because cached absolute origins survive an equal-radius resize.

- [ ] **Step 3: Implement mode-specific ringless anchors**

Keep the full-mode early return unchanged. In the ringless branch:

```python
available_center_x = float(self.width()) / 2.0

match self.wheel_mode:
    case "hsv-square" | "hsl-square":
        side = min(available_w, available_h)
        radius = (side / 2.0 + 2.0) * 1.414
        center_x = available_center_x
    case "triangle" | "hls-triangle":
        radius = min(available_w / 1.5, available_h / 1.732)
        center_x = available_center_x - radius / 4.0
    case "oklch-slice" | "rgb-slice":
        radius = min(available_w / 2.0, available_h / 1.732)
        center_x = available_center_x - radius / 2.0
    case _:
        side = min(available_w, available_h)
        radius = (side / 2.0 + 2.0) * 1.414
        center_x = available_center_x

return SliceGeometry(
    center_x=center_x,
    center_y=cy,
    radius=max(1.0, radius),
)
```

- [ ] **Step 4: Include both anchor coordinates in absolute-position cache keys**

Update only the three caches that store absolute origins or edges:

```python
# draw_hls_triangle
cache_key = (
    self.h, r, round(cx, 3), round(cy, 3),
    "hls", self.is_active_interaction(),
)

# draw_rgb_slice
cache_key = (
    self.h, r, round(cx, 3), round(cy, 3),
    "rgb", self.is_active_interaction(),
)

# draw_oklch_slice
cache_key = (
    round(oklch_h, 1), r, round(cx, 3), round(cy, 3),
    "oklch", self.is_active_interaction(),
)
```

- [ ] **Step 5: Verify Task 2 GREEN**

Run:

```powershell
python -m pytest tests/test_ringless_geometry.py tests/test_ringless_interaction.py tests/test_ringless_invalidation.py -xvs
```

Expected: all tests pass; full mode still uses the legacy center.

---

### Task 3: Apply unified top chrome to LAB / OKLab without double spacing

**Files:**
- Modify first (RED): `tests/test_ringless_integration.py`
- Modify first (RED): `tests/test_ringless_lifecycle.py`
- Modify after RED: `ui/main_window.py:568-593,946-982,1008-1033,2227-2267`

**Interfaces:**
- Consumes: Task 1 `RinglessLayout` semantics.
- Produces: `self.lab_layout: QHBoxLayout`.
- Produces: `_sync_ringless_mode()` propagation to both `pane_wheel` and `pane_lab`.
- Produces: LAB layout top margin equal to `control_bar_height` when controls are enabled, otherwise zero.
- Produces: `LabSquare.avoid_top=0` in ringless mode because the layout owns spacing.
- Preserves: legacy mapped preview avoidance when ringless is disabled.

- [ ] **Step 1: Write failing orchestration tests before production edits**

Add `pane_lab=MagicMock()` and `lab_layout=MagicMock()` to the integration fixture. Update LAB assertions:

```python
class TestSyncEnabledLab:
    def test_lab_keeps_controls_and_disables_wheel(self):
        fixture = _fixture(
            cfg_overrides={"hideHueRing": True}, stack_index=1
        )
        _sync(fixture, ws=384)
        layout = fixture.color_wheel.set_ringless_layout.call_args[0][0]

        assert layout.wheel_enabled is False
        assert layout.controls_enabled is True

    def test_lab_stack_minimum_includes_control_bar(self):
        fixture = _fixture(
            cfg_overrides={"hideHueRing": True}, stack_index=1
        )
        _sync(fixture, ws=384)
        layout = fixture.preview_box.set_ringless_layout.call_args[0][0]

        fixture.stack.setMinimumHeight.assert_called_once_with(
            384 + layout.control_bar_height
        )

    def test_lab_pane_receives_top_bar_layout(self):
        fixture = _fixture(
            cfg_overrides={"hideHueRing": True}, stack_index=1
        )
        _sync(fixture, ws=384)

        fixture.pane_lab.set_ringless_layout.assert_called_once()
        fixture.lab_layout.setContentsMargins.assert_called_once_with(
            0, 39, 0, 0
        )
```

Update LAB round-trip assertions so `controls_enabled` remains true on every page while `wheel_enabled` follows the page.

- [ ] **Step 2: Add failing lifecycle tests for margin restoration and no double avoidance**

Add `self.lab_layout = MagicMock()` to the existing lifecycle harness. Add:

```python
class TestLabTopControlBarLifecycle:
    def test_ringless_reserves_lab_control_bar(self, harness):
        harness.cfg["hideHueRing"] = True
        harness.stack.setCurrentIndex(1)
        harness._sync_ringless_mode(wheel_size=384, title_bar_height=28)

        harness.lab_layout.setContentsMargins.assert_called_with(0, 39, 0, 0)

    def test_disabled_mode_restores_zero_lab_margin(self, harness):
        harness.cfg["hideHueRing"] = False
        harness._sync_ringless_mode(wheel_size=384, title_bar_height=28)

        harness.lab_layout.setContentsMargins.assert_called_with(0, 0, 0, 0)

    def test_ringless_layout_owns_lab_top_spacing(self, harness):
        harness.cfg["hideHueRing"] = True
        harness.lab_square.avoid_top = 99

        MainWindow._update_lab_avoid(harness)

        assert harness.lab_square.avoid_top == 0
```

Run:

```powershell
python -m pytest tests/test_ringless_integration.py tests/test_ringless_lifecycle.py -xvs
```

Expected: failures show `pane_lab` is not called, LAB margin is not set, and ringless mode still executes legacy preview avoidance.

- [ ] **Step 3: Store and use the LAB layout**

In `init_ui()`, replace the local layout variable with an instance field and update its existing calls:

```python
self.lab_layout = QHBoxLayout(self.pane_lab)
self.lab_layout.setContentsMargins(0, 0, 0, 0)
self.lab_layout.setSpacing(6)
# Existing addWidget calls also use self.lab_layout.
```

- [ ] **Step 4: Propagate ringless chrome to both panes and reserve LAB space**

In `_sync_ringless_mode()` after the wheel pane update:

```python
self.pane_wheel.set_ringless_layout(layout)
self.pane_lab.set_ringless_layout(layout)

lab_top_margin = layout.control_bar_height if layout.controls_enabled else 0
self.lab_layout.setContentsMargins(0, lab_top_margin, 0, 0)
```

Keep the existing stack-minimum branch. With Task 1 semantics, `controls_enabled` remains true on LAB, so the stack continues to reserve `_ws + control_bar_height` there.

- [ ] **Step 5: Disable legacy preview avoidance while the layout owns the bar**

In `_update_lab_avoid()`, after obtaining `pb` and `ls`:

```python
if self.cfg.get("hideHueRing", False):
    ls.avoid_top = 0
    return
```

Leave the complete legacy mapping path unchanged below this guard.

Update stale comments in `_sync_ringless_mode()` and `update_geometries()` so LAB is documented as using rectangle controls and a reserved top margin.

- [ ] **Step 6: Verify Task 3 GREEN**

Run:

```powershell
python -m pytest tests/test_ringless_integration.py tests/test_ringless_lifecycle.py tests/test_window_height.py -xvs
```

Expected: all tests pass, including LAB stack height, top margin, page round-trip, and disabled restoration.

---

### Task 4: Integrated verification and visual QA

**Files:**
- Verify all changed Python files and tests.
- Do not add production behavior in this task.

- [ ] **Step 1: Automated suite and compilation**

Run:

```powershell
python -m pytest -q
python -m compileall -q core ui tests
```

Expected: all 175 existing tests plus the new tests pass; compilation exits 0.

- [ ] **Step 2: Whitespace and available diagnostics**

Run:

```powershell
$env:GIT_MASTER='1'; git diff --check
```

Run diagnostics on each changed Python file. If the existing LSP path-resolution problem remains, record it and run the same scoped `basedpyright` fallback used by the prior verification without broad legacy cleanup.

- [ ] **Step 3: Fresh offscreen screenshots**

Capture real widgets after the last edit at 100% and 150% scale:

- ringless HLS before and after a width-only resize;
- ringless LCH before and after width- and height-only resize;
- LAB / OKLab with controls right and controls left;
- disabled full-ring and disabled LAB restoration.

For HLS, calculate screenshot non-background bounds and assert horizontal slack differs by at most 1 px. For LCH, use the allocated slice image bounds `[cx - 0.5r, cx + 1.5r]` and confirm the rendered gamut remains inside them. Confirm indicators remain on their shapes after resize.

For LAB / OKLab, assert:

- rectangle preview and page-switch button occupy opposite sides of the top bar;
- `LabSquare` and the complete vertical `LabSlider` begin at or below `control_bar_height`;
- no duplicate blank band appears inside `LabSquare` (`avoid_top == 0`);
- left/right settings mirror the control clusters without changing fg/bg order;
- disabling the mode restores circles, zero LAB top margin, and bottom-right button placement.

- [ ] **Step 4: Interaction and Windows smoke test**

Using QTest or the actual app surface, drag HLS and LCH at the left edge, center, right edge, and current indicator after resize. Confirm color state changes without cursor/image offset. Toggle wheel → LAB → wheel, resize, change to 150%, disable ringless, and restart under isolated AppData.

- [ ] **Step 5: Independent review**

Review the full current worktree against the updated design spec. Any Critical or Important finding requires a fix, fresh targeted RED/GREEN verification, and a repeat of Steps 1-4.

## Success Criteria

1. HLS true bounds `[cx - 0.5r, cx + r]` are horizontally centered within 1 px in ringless mode.
2. OKLCh/RGB bounds `[cx - 0.5r, cx + 1.5r]` are horizontally centered within 1 px.
3. Width- or height-only resize cannot reuse an image or outline at a stale absolute origin.
4. Full-ring HLS/LCH geometry remains unchanged.
5. LAB / OKLab uses rectangle previews and the top page-switch button whenever ringless mode is enabled.
6. LAB square and vertical lightness slider both sit below one, and only one, control bar.
7. LAB stack minimum includes the control bar while ringless mode is enabled.
8. Disabled mode restores legacy wheel and LAB presentation exactly.
9. Full tests, compilation, screenshots, interaction checks, and Windows smoke test pass.

---

### Task 5: Correct OKLCh visible-gamut centering discovered by visual QA

**Files:**
- Modify first (RED): `tests/test_ringless_geometry.py`
- Modify first (RED): `tests/test_ringless_interaction.py`
- Modify after RED: `ui/color_wheel.py`

**Root Cause:** `_oklch_scale_for_hue()` maps the maximum boundary chroma to exactly `r` pixels. Starting from `min_x = cx - 0.5r`, the rendered non-transparent gamut therefore ends at `cx + 0.5r`. The earlier Task 2 centered a 2r cache allocation ending at `cx + 1.5r`, leaving the actual gamut visibly shifted left and undersized.

**Interfaces:**
- Produces: OKLCh ringless visible bounds `[cx - 0.5r, cx + 0.5r]`.
- Produces: OKLCh ringless `center_x=available_center_x` and `radius=min(available_w, available_h / 1.732)`.
- Produces: OKLCh QImage and rectangle hit bounds ending at `cx + 0.5r`.
- Preserves: full-ring geometry, boundary/color conversion, RGB slice geometry, indicator and drag formulas.

- [ ] **Step 1: Add a failing visible-gamut geometry test**

For a ringless `oklch-slice`, assert the visible horizontal bounds are centered and the radius is maximal under width/height constraints:

```python
def test_ringless_oklch_visible_bounds_are_centered_and_maximal(qapp):
    wheel = make_wheel(300, 339, canonical_layout())
    wheel.set_wheel_mode("oklch-slice")
    geometry = wheel.get_slice_geometry()
    margin = canonical_layout().margin

    left = geometry.center_x - 0.5 * geometry.radius
    right = geometry.center_x + 0.5 * geometry.radius
    left_slack = left - margin
    right_slack = wheel.width() - margin - right

    assert abs(left_slack - right_slack) <= 1.0
    assert geometry.radius == pytest.approx(
        min(286.0, 286.0 / 1.732), abs=1.0
    )
```

- [ ] **Step 2: Add a failing rendered-alpha bound test**

Render a real ringless OKLCh wheel to `QImage`, scan alpha/color pixels belonging to the slice while excluding the indicator outline, and assert the colored gamut's horizontal bounding-box center is within 2 px of the available rectangle center. Keep a focused assertion that the old implementation leaves roughly `r/2` excess slack on the right.

- [ ] **Step 3: Add failing hit-bound coverage**

Assert `_is_point_in_active_slice()` accepts a point just inside `cx + 0.5r` and rejects a point beyond it in `oklch-slice`; retain current RGB-slice assertions unchanged.

- [ ] **Step 4: Verify RED**

Run:

```powershell
python -m pytest tests/test_ringless_geometry.py tests/test_ringless_interaction.py -xvs -k "OklchVisible or oklch_visible"
```

Expected: geometry/maximal-radius, rendered-alpha centering, and right hit-bound tests fail against the transparent-allocation-centered implementation.

- [ ] **Step 5: Implement the minimal OKLCh-only correction**

Split `oklch-slice` from `rgb-slice` in `get_slice_geometry()`:

```python
case "oklch-slice":
    radius = min(available_w, available_h / 1.732)
    center_x = available_center_x
case "rgb-slice":
    radius = min(available_w / 2.0, available_h / 1.732)
    center_x = available_center_x - radius / 2.0
```

In `draw_oklch_slice()`, use `max_x = ceil(cx + 0.5r)` so the image allocation matches the maximum visible chroma domain. Split `_is_point_in_active_slice()` so OKLCh uses `max_x = cx + 0.5r`, while RGB retains its existing bounds. Do not change `min_x`, scale, boundary, indicator, or drag calculations.

- [ ] **Step 6: Verify GREEN and compatibility**

Run:

```powershell
python -m pytest tests/test_ringless_geometry.py tests/test_ringless_interaction.py tests/test_ringless_invalidation.py -xvs
```

Expected: all tests pass; existing full-mode and RGB geometry remain unchanged.
