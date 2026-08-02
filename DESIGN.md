# Colorink Design System

## 1. Atmosphere & Identity

Colorink is a compact desktop color laboratory: dense enough for fast picking, but calm enough to keep the color itself as the focal point. The signature is a restrained chrome around large, direct-manipulation color surfaces, with controls aligned to a shared baseline rather than floating independently.

## 2. Color

| Role | Token / source | Usage |
|---|---|---|
| Window surface | theme `bg` | Main application chrome |
| Secondary surface | theme `barBg` | Title bar, compact control bar, buttons |
| Input surface | theme `inputBg` | Slider value labels and settings inputs |
| Primary text | theme `text` | Labels, values, control glyphs |
| Border | theme `borderColor` | Buttons, inputs, slider chrome |
| Focus accent | `#5a94e2` | Active swatch outline and hover/focus feedback |
| Inactive swatch outline | `#cccccc` | Secondary foreground/background swatch border |

Themes remain source-of-truth in `ui/main_window.py`; new UI geometry should reuse those theme values instead of introducing unrelated colors.

## 3. Typography

- Primary UI font: `Segoe UI`, with `PingFang SC` and `Microsoft YaHei` fallbacks.
- Compact control glyphs use bold weight and scale from the configured UI scale.
- Values and labels stay legible at the existing compact desktop sizes; do not reduce body text below the current theme scale.

## 4. Spacing & Layout

- Base rhythm: 4px increments where practical.
- Ringless control bar: 30px at 100% UI scale.
- Control-bar edge margin: 7px at 100%.
- Mode buttons: 28px square with a 4px inter-button gap.
- Foreground/background swatches: 43px wide, 24px high, 5px gap, with 3px outer padding.
- Swatches use a 2px optical downward correction relative to the button center so the filled rectangles read centered beside the taller button chrome. Pane-local positioning must not change that relationship.
- The control bar visibility setting lives directly below the “hide hue ring and enlarge slice” option in the ringless settings block.

## 5. Component Anatomy

### Ringless control bar

A thin top band used when the hue ring is hidden and the color slice is enlarged. Foreground/background swatches occupy one edge; the module and mode buttons occupy the opposite edge. The mode button always keeps its reserved slot even when the optional module button is hidden, so the LAB toggle does not jump during pane switches.

### Color preview swatches

Two rounded rectangles, foreground first and background second. Painting and hit testing use the same cached geometry. The active swatch uses the focus accent border.

### Mode and module buttons

Small square buttons with theme-aware surface/border colors, pointer cursor, hover accent, and stable pane-local anchors.

## 6. Interaction States

- Active swatch: blue accent border.
- Inactive swatch: neutral light border.
- Button hover: theme surface shift plus blue border.
- Ringless enabled: rectangle swatches and top control band.
- Ringless disabled: legacy circular preview and bottom-right mode placement.
- LAB switch: same reserved mode-button slot as the wheel pane while the module switch is enabled; when it is disabled, the LAB switch uses the outer edge slot.
- Pane switching keeps the wheel cache warm and avoids a full theme/layout rebuild.

## 7. Depth & Motion

Colorink uses shallow tonal separation rather than heavy shadows. Interaction feedback is immediate and geometric: button hover, active borders, and direct slice/slider response carry the state. Layout transitions must avoid positional jumps; the control bar and LAB switch anchor are intentionally stable.
