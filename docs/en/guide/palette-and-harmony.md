# Palette, Color Spaces & Harmony — In Depth

Colorink has an exceptionally flexible color engine: **5 professional color-space modules**, **two big views (standard color wheel & LAB ring palette)**, and **five harmony systems** designed for illustration and concept art.

---

## 1. The Five Color-Space Modules

Colorink is not limited to a single traditional picker — it supports 5 different mathematical color models:

| Module | Color model | Strengths & best use |
| :--- | :--- | :--- |
| **HSV (default)** | Hue–Saturation–Value | The classic digital-painting space; hue ring paired with a standard triangle or square picking area. |
| **VHSV** | Perceptual Saturation–Value | Applies perceptual correction to HSV's saturation and value; smoother, more natural transitions in shadows and pure colors. |
| **HLS** | Hue–Lightness–Saturation | A double-cone model: pure white and pure black sit at the slice's apex and base, with pure-color saturation in between. |
| **RGB** | Red–Green–Blue orthogonal slice | Based on cross-sections of the RGB cube; adjust channel weighting directly. |
| **LCH (OKLCh)** | Perceptual Lightness–Chroma–Hue | Built on the modern OKLCh perceptually uniform space; chroma and lightness map linearly to human vision — the top choice for high-end concept-art grading. |

### How to switch modules?
1. **Click the icon directly**: a floating module-switch button sits at the top right of the palette;
2. **Via Settings**: **Settings → Color Picker → Color Space Module** dropdown.

---

## 2. Dual Views: Color Wheel & LAB Ring Palette

Switch freely between the classic hue ring and the high-precision LAB ring palette at any time:

### Quick switching
- **Local hover hotkey (recommended)**: hover the cursor over the palette and tap **`Space`** to flip instantly between the hue ring and LAB disc;
- **Global hotkey**: press **`Ctrl + Alt + L`** anywhere;
- **Floating button**: click the **`⊙/△` view-toggle button** between the palette and the foreground swatch.

### Shape & mirroring customization
- **Horizontally mirrored ring**: **Settings → Color Picker → Mirror Ring Horizontally**. Flips the ring left-to-right so you can arrange "cool left, warm right" or the reverse, as you prefer;
- **Disc vs. Square**: the LAB palette offers both a smooth circular slice and a square stepped slice;
- **Checkerboard background**: toggle the transparency checkerboard underlay beneath the LAB palette;
- **Ringless minimal slice mode**: for maximum UI minimalism, enable "hide ring, enlarge slice" in Settings — the hue ring disappears and the picking slice stretches horizontally to fill the window.

---

## 3. The Five Harmony Systems

With harmony mode enabled on the LAB palette, a set of harmony skeleton points derived from classic color theory appears automatically:

```
           [Anchor point]
             /    \
            /      \
      [Sub-point 1]  [Sub-point 2]
```

| Harmony mode | Relationship & usage tips |
| :--- | :--- |
| **Complementary** | 180° opposite colors. Strong warm-cool contrast — great for pulling a subject's highlights against the background. |
| **Split-Complementary** | A Y-shape formed by the base color plus two points flanking its complement. More layered and lively than a single complement. |
| **Analogous** | Neighboring hues a small angle apart on the ring. Highly unified tonality — ideal for a specific time of day (dusk/dawn) or single-mood illustrations. |
| **Triadic** | An equilateral triangle (120° apart). Rich, vivid colors with balanced visual energy. |
| **Rectangle** | A 90° square/rectangle formation providing two pairs of complements — suited to complex group portraits and multi-character scenes. |

### Fine-grained harmony interactions
- **Click sub-points to pick**: tap any sub harmony point on the palette with pen or mouse to make it the foreground color instantly;
- **Drag the main anchor**: when the main color moves, the whole harmony group follows under strict geometric constraints;
- **Drag a sub-anchor**: dragging a sub-point rotates or scales the entire skeleton in real time via inverse mapping;
- **5px stylus anti-jitter**: a built-in 5-pixel anti-jitter algorithm ensures tiny pen tremors on tap are never misread as drags — stable and crisp;
- **Smart gamut-boundary snapping**: when a handle is dragged outside the sRGB displayable range, Colorink snaps the anchor onto the valid boundary along the shortest geometric path instead of getting stuck outside.

---

## 4. Dual Swatches & Transparent Color (Preview Swatches)

The foreground (FG), background (BG), and transparent controls sit at the top of the main window:

- **Left-click**: switch the editing target between "foreground" and "background";
- **Right-click a swatch for the format-copy menu**: right-click the FG or BG swatch to copy professional formats in one click:
  - **HEX**: `#RRGGBB`
  - **RGB**: `rgb(r, g, b)`
  - **HSL**: `hsl(h, s%, l%)`
  - **OKLCh**: `oklch(L C h)` (high-precision color-space standard)
  - **LAB**: `lab(L% a b)`
- **Transparent capsule button (checkerboard icon)**: switches the current slot to transparent color; when connected to CSP, it seamlessly triggers the app's transparent-eraser mode.
