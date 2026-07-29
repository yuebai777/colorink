# Content-Driven Window Height Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Colorink main window track the minimum height required by the currently visible controls, including shrinking when controls are hidden.

**Architecture:** Keep sizing ownership in `ui/main_window.py`. Add pure height-policy helpers that fit the current content requirement and preserve the width-driven visualizer minimum, then add one layout-driven method that activates the existing Qt layouts, calculates the required height, updates `minimumHeight`, and applies only necessary automatic resizes. Defer the first hidden-window measurement until Qt has completed the initial layout pass. Route all structural content changes through that method; leave the settings sidebar as a floating overlay.

**Tech Stack:** Python 3.10+, PyQt6 6.7.1, standard-library `unittest`.

## Global Constraints

- Only the main window's visible content participates in automatic height calculation.
- The settings sidebar remains an overlay and does not affect the main window height.
- `lockWindowSize` continues to block manual resize and Ctrl+wheel resize, but does not block required content-driven resizing.
- Do not add persisted configuration or compatibility branches.
- Do not alter width, position, DPI conversion, or sidebar geometry behavior.
- The visualizer minimum height must be at least the current main-content width so the color wheel remains square and fully visible.
- Work with the existing uncommitted workspace changes; do not revert or overwrite unrelated modifications.

## File Map

- Modify: `ui/main_window.py` — add the height policy and connect it to existing layout/configuration paths.
- Create: `tests/test_window_height.py` — test the pure shrink/expand policy without starting the full Windows-dependent application.
- Existing design: `docs/superpowers/specs/2026-07-29-content-driven-window-height-design.md` — behavior contract for this plan.

### Task 1: Add and Test the Height Policy

**Files:**
- Create: `tests/test_window_height.py`
- Modify: `ui/main_window.py:670-710` for policy state and near the `MainWindow` helper methods around `resizeEvent()` for the static policy function.

**Interfaces:**
- Produces `MainWindow._resolve_content_height(current_height: int, required_height: int, last_auto_height: int | None, manual_override: bool) -> tuple[int, bool]`.
- The returned height is never below `required_height`; content requirement changes clear stale manual-height overrides so hidden controls can shrink the window.

- [ ] **Step 1: Write failing unit tests for the policy.**

```python
import unittest

from ui.main_window import MainWindow


class ContentHeightPolicyTests(unittest.TestCase):
    def test_expands_when_content_is_taller(self):
        target, manual = MainWindow._resolve_content_height(500, 640, 500, False)
        self.assertEqual((target, manual), (640, False))

    def test_shrinks_when_previous_height_was_automatic(self):
        target, manual = MainWindow._resolve_content_height(640, 500, 640, False)
        self.assertEqual((target, manual), (500, False))

    def test_content_shrink_clears_user_expanded_height(self):
        target, manual = MainWindow._resolve_content_height(900, 500, 640, True)
        self.assertEqual((target, manual), (500, False))

    def test_required_height_always_wins_over_manual_height(self):
        target, manual = MainWindow._resolve_content_height(500, 700, 900, True)
        self.assertEqual((target, manual), (700, False))

    def test_first_measurement_can_fit_the_saved_window_to_content(self):
        target, manual = MainWindow._resolve_content_height(710, 480, None, False)
        self.assertEqual((target, manual), (480, False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify it fails.**

Run: `python -m unittest discover -s tests -p "test_window_height.py" -v`

Expected: FAIL because `_resolve_content_height` does not exist yet.

- [ ] **Step 3: Implement the pure policy method and initialize state.**

Add these fields at the start of `MainWindow.__init__`, before `init_ui()`:

```python
self._last_auto_height = None
self._manual_height_override = False
self._adjusting_content_height = False
self._content_height_adjust_pending = False
self._content_height_timer = QTimer(self)
self._content_height_timer.setSingleShot(True)
self._content_height_timer.timeout.connect(self._run_deferred_content_height)
```

Add this static method to `MainWindow`:

```python
@staticmethod
def _resolve_content_height(current_height, required_height,
                            last_auto_height, manual_override):
    required_height = max(1, int(required_height))
    current_height = int(current_height)
    if current_height < required_height:
        return required_height, False
    if manual_override:
        return current_height, True
    if last_auto_height is None or current_height <= int(last_auto_height):
        return required_height, False
    return current_height, True
```

- [ ] **Step 4: Run the focused test and verify it passes.**

Run: `python -m unittest tests.test_window_height -v`

Expected: PASS for all six policy cases.

### Task 2: Connect Layout Measurement and Structural Triggers

**Files:**
- Modify: `ui/main_window.py` in `MainWindow.init_ui`, `apply_theme`, `refresh_slider_visibility_and_order`, `zoom_ui`, `on_settings_saved`, and the manual resize release handler.

**Interfaces:**
- Produces `MainWindow._adjust_content_height() -> None`.
- `_adjust_content_height()` sets the dynamic minimum height, updates `_last_auto_height`, and only calls `resize()` while `_adjusting_content_height` is false.
- Produces `MainWindow._required_visualizer_height(window_width: int, margins_left: int, margins_right: int, stack_min_height: int) -> int`.

- [ ] **Step 1: Add a failing integration-style test seam for layout inputs.**

Extend `tests/test_window_height.py` with a fake layout measurement test that calls a small static calculation helper:

```python
    def test_required_height_includes_layout_parts_and_spacing(self):
        required = MainWindow._required_content_height(
            title_height=28,
            stack_min_height=120,
            sliders_height=264,
            margins_top=0,
            margins_bottom=8,
            spacing=4,
        )
        self.assertEqual(required, 428)
```

Implement the helper with this exact contract:

```python
@staticmethod
def _required_content_height(title_height, stack_min_height, sliders_height,
                             margins_top, margins_bottom, spacing):
    return max(
        240,
        int(title_height) + int(stack_min_height) + int(sliders_height)
        + int(margins_top) + int(margins_bottom) + 2 * int(spacing),
    )
```

- [ ] **Step 2: Run the test to verify the new calculation test fails.**

Run: `python -m unittest discover -s tests -p "test_window_height.py" -v`

Expected: FAIL because `_required_content_height` does not exist yet.

- [ ] **Step 3: Implement `_adjust_content_height()`.**

Inside `MainWindow`, add:

```python
def _adjust_content_height(self):
    if getattr(self, "_adjusting_content_height", False):
        return
    required = 0
    try:
        self.sliders_layout.activate()
        self.main_layout.activate()
        margins = self.main_layout.contentsMargins()
        visualizer_h = self._required_visualizer_height(
            self.width(), margins.left(), margins.right(),
            self.stack.minimumSizeHint().height(),
        )
        required = self._required_content_height(
            self.title_bar.sizeHint().height(),
            visualizer_h,
            self.sliders_container.sizeHint().height(),
            margins.top(), margins.bottom(), self.main_layout.spacing(),
        )
    except AttributeError:
        return

    self.setMinimumHeight(required)
    target, manual = self._resolve_content_height(
        self.height(), required, self._last_auto_height,
        self._manual_height_override,
    )
    self._last_auto_height = required
    self._manual_height_override = manual
    if target == self.height():
        return

    self._adjusting_content_height = True
    try:
        self.resize(self.width(), target)
    finally:
        self._adjusting_content_height = False
```

- [ ] **Step 4: Route existing content changes to the helper.**

Call `_adjust_content_height()` after the layout has been rebuilt in `refresh_slider_visibility_and_order()` and after `apply_theme()` has changed spacing, visibility, or scale. Call it after `zoom_ui()` changes the window dimensions. In `on_settings_saved()`, keep the existing order of `refresh_slider_visibility_and_order()`, `apply_theme()`, and `zoom_ui()`; let the final applicable call from `apply_theme()` or `zoom_ui()` perform the measurement, without adding a third duplicate call at the end.

- [ ] **Step 5: Preserve manual resize intent.**

In `mouseReleaseEvent()`, inside the existing `if was_resizing:` block, set:

```python
self._manual_height_override = True
self._last_auto_height = self.height()
```

Do not change the existing geometry persistence or `lockWindowSize` checks.

- [ ] **Step 6: Run focused tests and static diagnostics.**

Run: `python -m unittest discover -s tests -p "test_window_height.py" -v`

Expected: PASS for all six policy/calculation cases.

Run: `python -m py_compile ui/main_window.py tests/test_window_height.py`

Expected: exit code 0 with no syntax errors.

### Task 3: Verify the Existing UI Surface

**Files:**
- Verify: `ui/main_window.py`
- Verify: `tests/test_window_height.py`

**Interfaces:**
- Consumes the completed `_adjust_content_height()` integration.
- Produces evidence that visible-control changes update the minimum height without affecting width, position, or the settings overlay.

- [ ] **Step 1: Run language diagnostics on every changed Python file.**

Run `lsp_diagnostics` for `ui/main_window.py` and `tests/test_window_height.py`; fix only errors introduced by this feature.

- [ ] **Step 2: Run the full available test surface.**

Run: `python -m unittest discover -v`

Expected: all discovered tests pass. If the repository has no other tests, the focused height tests are the complete suite.

- [ ] **Step 3: Manually exercise the application.**

Run: `python main.py`

Verify in the running window:

1. Hide visible slider groups and confirm the window's minimum height decreases after the setting is applied.
2. Enable a group or increase history rows and confirm the window expands enough to show all controls.
3. Manually drag the bottom edge higher, hide a group, and confirm the window does not forcibly shrink.
4. Turn on `固定窗口大小`, change visible controls, and confirm content-driven sizing still prevents clipping while manual dragging remains disabled.
5. Open the settings sidebar and confirm it remains an overlay without changing the main window's content-driven height.

- [ ] **Step 4: Re-run focused verification after manual QA.**

Run: `python -m unittest discover -s tests -p "test_window_height.py" -v`

Expected: PASS, with no unrelated files changed.
