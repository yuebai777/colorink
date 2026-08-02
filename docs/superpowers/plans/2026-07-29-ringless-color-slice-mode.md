# Ringless Color Slice Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off, persisted ringless display mode that enlarges the existing HSV/HLS/LCH slices, shows foreground/background as small adjacent rounded rectangles, and moves the two existing buttons to the opposite side without changing any legacy mode.

**Architecture:** First extract the existing preview and pane-button classes from the oversized `main_window.py` without behavior changes. New typed ringless value objects and a focused settings widget live in small modules. `ColorWheel` keeps one instance and one paint path: it chooses legacy or mode-specific ringless geometry, while paint, indicator, and pointer handling all consume the same geometry. `MainWindow` performs only thin orchestration and restores the circle preview on LAB/OKLab.

**Tech Stack:** Python 3.10+, PyQt6, pytest, Windows; `QT_QPA_PLATFORM=offscreen` for widget tests.

## Global Constraints

- The feature is additive. Full-ring HSV/HLS/LCH, LAB/OKLab, overlapping circles, existing button actions/positions, and `previewBoxPosition` remain available and unchanged when the new mode is off.
- New config defaults are `hideHueRing=False` and `ringlessControlsSide="right"`.
- No third toolbar button; activation is a settings checkbox.
- Ringless HSV, HLS, and OKLCh geometry is maximized independently for the active mode.
- Ringless paint, indicator, hit-test, and drag paths use the same `SliceGeometry` value.
- The top control band participates in visualizer minimum-height calculation so the HSV square can remain near full width.
- No HSV/HLS/OKLCh conversion, gamut-boundary, slider, history, or sync-protocol changes.
- Keep each new Python module below 250 pure LOC. Existing oversized modules receive only extraction deletions or thin wiring; no unrelated refactor.
- Follow red -> green -> refactor. Never weaken or delete an existing test.
- Do not create git commits unless the user explicitly requests them.

---

## Execution Waves

| Wave | Tasks | Dependency |
|---|---|---|
| 1 | Task 1 | Baseline only |
| 2 | Task 2 | Task 1 extraction complete; establishes typed ringless interfaces |
| 3 | Tasks 3, 4, 5 | Task 2 interfaces complete; implementation files are disjoint |
| 4 | Task 6 | Tasks 3-5 component interfaces complete |
| 5 | Task 7 | Integrated feature complete |

---

### Task 1: Lock legacy behavior and extract picker UI primitives

**Files:**
- Create: `ui/color_preview_box.py`
- Create: `ui/picker_panes.py`
- Create: `tests/test_picker_components.py`
- Modify: `ui/main_window.py:404-667`

**Interfaces:**
- Produces `ColorPreviewBox` from `ui.color_preview_box`.
- Produces `PaneWithModeButton`, `WheelPane`, and `LabPane` from `ui.picker_panes`.
- `ui.main_window` re-exports imported class names implicitly through its module namespace, preserving current imports.

- [ ] **Step 1: Write the failing extraction contract test**

```python
def test_extracted_picker_components_are_importable():
    from ui.color_preview_box import ColorPreviewBox
    from ui.picker_panes import LabPane, PaneWithModeButton, WheelPane

    assert ColorPreviewBox.__name__ == "ColorPreviewBox"
    assert PaneWithModeButton.__name__ == "PaneWithModeButton"
    assert WheelPane.__name__ == "WheelPane"
    assert LabPane.__name__ == "LabPane"
```

Run: `python -m pytest tests/test_picker_components.py::test_extracted_picker_components_are_importable -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Move classes verbatim and update imports**

Move `PaneWithModeButton`, `WheelPane`, and `LabPane` to `ui/picker_panes.py`. Move `ColorPreviewBox` to `ui/color_preview_box.py`. Preserve method bodies, constructor shape, callback calls, and constants exactly; do not import `MainWindow` from either extracted module.

In `ui/main_window.py`, import the four classes and remove their old definitions. Do not add ringless behavior in this step.

- [ ] **Step 3: Verify extraction is behavior-preserving**

Run: `python -m pytest tests/ -v`
Expected: all baseline tests PASS.

Run diagnostics on `ui/main_window.py`, `ui/color_preview_box.py`, and `ui/picker_panes.py`; expect no new errors.

---

### Task 2: Add typed ringless settings and persisted defaults

**Files:**
- Create: `ui/ringless_mode.py`
- Create: `ui/ringless_settings.py`
- Create: `tests/test_ringless_settings.py`
- Modify: `core/config.py:33-90`
- Modify: `tests/test_release_contract.py:33-64`
- Modify: `ui/settings_sidebar.py:270-305, 649-669, 944-955`

**Interfaces:**

```python
ControlsSide = Literal["left", "right"]

@dataclass(frozen=True, slots=True)
class RinglessConfig:
    enabled: bool
    controls_side: ControlsSide

    @classmethod
    def from_values(cls, enabled: bool, raw_side: str) -> "RinglessConfig":
        match raw_side:
            case "left":
                controls_side: ControlsSide = "left"
            case "right":
                controls_side = "right"
            case _:
                controls_side = "right"
        return cls(enabled=enabled, controls_side=controls_side)

@dataclass(frozen=True, slots=True)
class RinglessLayout:
    wheel_enabled: bool
    controls_enabled: bool
    controls_side: ControlsSide
    control_bar_height: int
    margin: int
    swatch_width: int
    swatch_height: int
    swatch_gap: int
    corner_radius: int
    button_gap: int

def resolve_ringless_layout(
    config: RinglessConfig,
    wheel_page_active: bool,
    ui_scale: float,
) -> RinglessLayout:
    scale = max(0.01, ui_scale)
    return RinglessLayout(
        wheel_enabled=config.enabled,
        controls_enabled=config.enabled and wheel_page_active,
        controls_side=config.controls_side,
        control_bar_height=max(1, round(39 * scale)),
        margin=max(1, round(7 * scale)),
        swatch_width=max(1, round(43 * scale)),
        swatch_height=max(1, round(24 * scale)),
        swatch_gap=max(1, round(5 * scale)),
        corner_radius=max(1, round(4 * scale)),
        button_gap=max(1, round(4 * scale)),
    )
```

`RinglessSettingsWidget(QWidget)` owns a checkbox and a child `QWidget` containing the side-label/combo row. It exposes `set_config(config: RinglessConfig) -> None`, `config() -> RinglessConfig`, and `changed = pyqtSignal()`. These methods contain real signal blocking and mapping logic; they do not duplicate persistence.

- [ ] **Step 1: Write failing tests for config parsing, defaults, and settings visibility**

Given invalid side text, `RinglessConfig.from_values(True, "top")` returns side `"right"`. Given a disabled config, the side-row wrapper is hidden. Given enabled/left, it is visible and the combo reads `左侧`. Extend the release-contract test to assert default-off/right and missing-key merge.

Run: `python -m pytest tests/test_ringless_settings.py tests/test_release_contract.py -v`
Expected: FAIL because modules, widget, and config keys do not exist.

- [ ] **Step 2: Implement value objects and settings widget**

Use frozen slotted dataclasses. `resolve_ringless_layout()` scales the approved base metrics `39, 7, 43, 24, 5, 4` by `ui_scale`. The side-row must be a `QWidget`; never call `setVisible()` on a `QLayout`.

- [ ] **Step 3: Wire SettingsSidebar with thin calls**

Create `self.ringless_settings`, connect its `changed` signal to `save_settings`, and add the widget near the module selector. In `refresh_ui()`, block the composite widget's signals through `set_config()`. In `save_settings()`, write both fields from `self.ringless_settings.config()`.

- [ ] **Step 4: Verify settings and legacy config**

Run: `python -m pytest tests/test_ringless_settings.py tests/test_release_contract.py -v`
Expected: all tests PASS.

---

### Task 3: Add mode-specific ringless wheel geometry and interaction

**Files:**
- Create: `tests/test_ringless_geometry.py`
- Modify: `ui/color_wheel.py:110-203, 245-260, 287-418, 879-935`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class SliceGeometry:
    center_x: float
    center_y: float
    radius: float

ColorWheel.set_ringless_layout(self, layout: RinglessLayout) -> None
ColorWheel.get_slice_geometry(self) -> SliceGeometry
```

- [ ] **Step 1: Write failing geometry tests for each active module**

For a 300 x 339 wheel with 39 px control bar and 7 px margins:
- `hsv-square` uses the largest square fitting the available 286 x 286 area.
- `hls-triangle` satisfies `1.5*r <= 286` and `1.732*r <= 286` and is maximal for those constraints.
- `oklch-slice` satisfies `2*r <= 286` and `1.732*r <= 286` and is maximal.
- Full mode still returns the exact current `get_wheel_geometry()` values.

Run: `python -m pytest tests/test_ringless_geometry.py -v`
Expected: FAIL because ringless interfaces do not exist.

- [ ] **Step 2: Implement mode-specific shared geometry**

`get_slice_geometry()` first derives one available rectangle from `RinglessLayout`, then uses `match self.wheel_mode` to compute only the active mode's radius. Do not take the minimum across HSV, HLS, and OKLCh together. Return one immutable `SliceGeometry` consumed by every ringless path.

- [ ] **Step 3: Refactor paint without duplicating the full paint body**

Keep one `paintEvent()`. When ringless is disabled, execute current ring creation, ring drawing, and hue indicator unchanged. Select `center_x`, `center_y`, and `radius` from legacy geometry or `SliceGeometry`, then run the existing slice and internal-indicator dispatch once. When ringless is enabled, skip only ring drawing and `draw_hue_indicator()`.

- [ ] **Step 4: Reuse geometry in pointer handling**

Extract narrow helpers for starting and continuing an existing slice drag. Full mode preserves the current hue-ring gate. Ringless mode cannot start hue dragging and dispatches HSV/HLS/OKLCh handling with the same `SliceGeometry` used by paint. Clicking outside the active slice's valid/bounding region must not start dragging.

- [ ] **Step 5: Invalidate every geometry-dependent cache on layout changes**

Clear ring, slice-image, HLS, OKLCh, and gamut-boundary cache keys/images when ringless enablement or scaled metrics change. The setter is idempotent and avoids repaint work when the layout is unchanged.

- [ ] **Step 6: Verify geometry, render path, and legacy wheel**

Run: `python -m pytest tests/test_ringless_geometry.py -v`
Run: `python -m pytest tests/ -v`
Expected: all tests PASS.

---

### Task 4: Add rounded-rectangle presentation to ColorPreviewBox

**Files:**
- Create: `tests/test_ringless_preview.py`
- Modify: `ui/color_preview_box.py`

**Interfaces:**

`ColorPreviewBox.set_ringless_layout(self, layout: RinglessLayout, window_width: int, title_bar_height: int) -> None` applies the presentation and geometry using one typed layout value.

- [ ] **Step 1: Write failing rectangle geometry and hit tests**

Assert equal 43 x 24 scaled swatches, 5 px gap, 4 px radius, foreground first and background second regardless of group side, correct left/right group placement, and `None` outside both rectangles. Also render into a `QImage` and assert active and inactive border pixels differ.

Run: `python -m pytest tests/test_ringless_preview.py -v`
Expected: FAIL because ringless presentation does not exist.

- [ ] **Step 2: Implement one shared rectangle-layout helper**

The helper returns two `QRectF` values. Paint and `_get_clicked_slot()` both use those exact rectangles. Keep all existing circle paint/hit code unchanged behind the disabled branch.

- [ ] **Step 3: Preserve interaction semantics**

Left click selects fg/bg, right click opens the current copy menu, and double click calls the current swap path. Switching back to full/LAB presentation restores `position_mode` circle geometry and active z-order.

- [ ] **Step 4: Verify preview behavior**

Run: `python -m pytest tests/test_ringless_preview.py -v`
Expected: all tests PASS.

---

### Task 5: Add top-bar anchoring while preserving button order

**Files:**
- Create: `tests/test_ringless_panes.py`
- Modify: `ui/picker_panes.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ButtonPositions:
    module_x: int
    mode_x: int
    y: int

def ringless_button_positions(
    pane_width: int,
    button_size: int,
    layout: RinglessLayout,
) -> ButtonPositions:
    y = (layout.control_bar_height - button_size) // 2
    if layout.controls_side == "right":
        module_x = layout.margin
        mode_x = module_x + button_size + layout.button_gap
    else:
        mode_x = pane_width - layout.margin - button_size
        module_x = mode_x - layout.button_gap - button_size
    return ButtonPositions(module_x=module_x, mode_x=mode_x, y=y)

PaneWithModeButton.set_ringless_layout(self, layout: RinglessLayout) -> None
```

- [ ] **Step 1: Write failing pure position tests**

When swatches are right, buttons are left; when swatches are left, buttons are right. Preserve the existing relative order in both clusters: module button left of mode button. Disabled mode still uses the exact current bottom-right positioning.

Run: `python -m pytest tests/test_ringless_panes.py -v`
Expected: FAIL because position APIs do not exist.

- [ ] **Step 2: Implement pure position calculation and pane application**

`PaneWithModeButton` calls the pure function only when `layout.controls_enabled` is true; otherwise its current `_reposition_mode_button()` behavior remains byte-for-byte equivalent. Tests create real `QPushButton` instances and never guard assertions with `if button is not None`.

- [ ] **Step 3: Verify panes and legacy positions**

Run: `python -m pytest tests/test_ringless_panes.py -v`
Expected: all tests PASS.

---

### Task 6: Integrate startup, page switching, sizing, and restoration

**Files:**
- Create: `tests/test_ringless_integration.py`
- Modify: `ui/main_window.py`

**Interfaces:**

`MainWindow._sync_ringless_mode(self, wheel_size: int | None = None, title_bar_height: int | None = None) -> None` is the only orchestration entry point.

- [ ] **Step 1: Write failing integration tests without constructing MainWindow**

Call `MainWindow._sync_ringless_mode()` on a `SimpleNamespace` fixture containing real config values and narrow mocked components. Assert:
- wheel page + enabled -> wheel, rectangle preview, top anchors, and increased stack minimum are active;
- LAB page + enabled -> wheel state remains persisted but preview is circles, pane anchors are legacy, and ringless stack minimum is removed;
- disabled -> every legacy presentation is restored;
- invalid side resolves to right.
- `showModuleSwitchButton=False` keeps the module button hidden in both layouts.

Run: `python -m pytest tests/test_ringless_integration.py -v`
Expected: FAIL because `_sync_ringless_mode` does not exist.

- [ ] **Step 2: Implement thin orchestration**

Parse `RinglessConfig`, resolve `RinglessLayout` using `stack.currentIndex() == 0`, and propagate it to `ColorWheel`, `ColorPreviewBox`, and `WheelPane`. In active ringless wheel mode, set stack minimum height to inner visualizer width plus control-bar height; otherwise clear the explicit minimum so existing child hints apply.

- [ ] **Step 3: Wire every lifecycle edge**

Call `_sync_ringless_mode()`:
- after preview and module-button construction during startup;
- at the end of `update_geometries()` after button metrics are set;
- after `stack.setCurrentIndex()` in `toggle_picker_mode()`;
- after config reload / wheel config reload in `on_settings_saved()`.

Trigger `_adjust_content_height()` after user-visible mode/page changes, outside resize recursion.

- [ ] **Step 4: Verify lifecycle and existing height policy**

Run: `python -m pytest tests/test_ringless_integration.py tests/test_window_height.py -v`
Run: `python -m pytest tests/ -v`
Expected: all tests PASS.

---

### Task 7: Full verification and visual QA

**Files:**
- Verify all changed Python files and tests.
- Do not add production behavior in this task.

- [ ] **Step 1: Diagnostics and strict-rule audit**

Run `lsp_diagnostics` on every changed Python file. Run the programming skill's Python no-excuse checker on changed paths if its runtime dependencies are available. Record unavailable tools rather than silently skipping them.

- [ ] **Step 2: Automated suite**

Run: `python -m pytest tests/ -v`
Expected: zero failures.

Run: `python -m compileall core ui tests`
Expected: exit 0.

- [ ] **Step 3: Offscreen user-visible scenarios**

With `QT_QPA_PLATFORM=offscreen`, render/grab full-ring and ringless HSV/HLS/LCH states plus LAB. Confirm captures are nonblank, ringless slices are enlarged, rectangle swatches are adjacent, controls do not overlap, and disabling the mode restores legacy captures. Inspect fresh PNGs directly, then remove temporary evidence files.

- [ ] **Step 4: Live Windows smoke test**

Run `python main.py`. Verify the settings checkbox and side selector, drag each slice, select/swap/copy fg/bg, switch HSV/HLS/LCH, switch LAB and back, resize, change UI scale, disable ringless, and restart with persisted settings. Restore the user's pre-test config after the smoke test.

- [ ] **Step 5: Independent review**

Run the post-implementation review and visual QA skills on fresh evidence. Any blocker requires a fix and a fresh verification pass.

## Success Criteria

1. The default launch is byte-for-byte behaviorally equivalent to the existing full-ring mode.
2. Ringless mode hides only the ring/hue indicator and enlarges each existing slice independently.
3. Paint, indicator, click, and drag remain aligned for HSV/HLS/LCH.
4. Adjacent rounded rectangles preserve fg/bg selection, swap, and copy behavior.
5. Left/right settings move the entire swatch group and place existing buttons opposite without reversing module/mode order.
6. LAB/OKLab always uses the existing circle preview and legacy button layout.
7. Disabling ringless restores every legacy presentation and persisted position behavior.
8. Full tests, diagnostics, compile check, offscreen captures, and live smoke test pass.
