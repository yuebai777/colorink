# Freeform Panels, Floating Layouts & UI Customization

Colorink offers an extremely flexible desktop layout system: **drag-to-reorder modules**, **tabbed panel stacking**, **double-click tear-off into floating windows**, **0%–100% translucent backgrounds**, and even an **eyedropper-driven custom skin** — fully adaptable to any painter's workflow.

---

## 1. The Modular Freeform Panel System

All slider groups (RGB, HSV, VHSV, HSL, LAB, OKLab, OKLCh) and the color history panel are driven by a modular panel host.

### Drag to reorder panels
1. Open **Settings → Panels** and enable **【Panel Grips】**;
2. A lightweight grip bar appears at the top of every module;
3. Hold a grip and drag up or down to reorder slider groups and the history swatches however you like.

### Tab stacking (Tabs mode)
If vertical screen space is tight:
1. In **Settings → Panels**, enable **【Stack slider groups as tabs】**;
2. Each color-space slider group collapses into tabs along the top;
3. **Smart anti-jump protection**: when switching colors or opening Settings, Colorink precisely remembers the tab you were using — no accidental jumps back to the default tab.

### Double-click tear-off into floating windows
Want the color history or a slider panel on a second monitor, or beside your canvas?
- **Tear off**: **double-click** any panel's grip bar and it instantly flies out as an independent always-on-top utility window;
- **Drag out works too**: hold the grip, drag outside any window, and release — same result. A thumbnail strip of the panel follows your cursor, with drop hints drawn in the main window or target floater;
- **Drag between floaters**: drop one floater onto another and the panel merges into that window (in tabs mode, dropping in the middle stacks them as tabs);
- **8-way free resizing**: hover any edge or corner of a floating window to stretch it in 8 directions;
- **Pin button**: each floater's title bar has a pin to toggle always-on-top;
- **One-click precise recall**: **double-click the floater's title bar**, or click the **recall (×)** button, and the panel snaps back to its original slot in the main window in milliseconds;
- **Restart memory**: close and reopen the app — every floater's position and size is fully restored; **manually resized floaters keep your exact size** (they won't auto-shrink back to content height on next launch).

---

## 2. Translucent Floating Over the Canvas (A Painter's Core Feature)

Many painters like to float the palette directly over their line art or color-blocking area. A traditional solid black window would badly obscure the artwork.

### Stepless 0%–100% background translucency
Go to **Settings → Interface → Background Opacity**:
- **Stepless slider**: the window base, borders, and title bar follow the slider in real time;
- **Fully invisible at 0%**: the window's background plate disappears entirely, leaving only the hue ring, slider tracks, and swatches floating above your canvas;
- **Pure color, uncontaminated**: rest assured — sliders, the wheel, and swatches themselves always stay 100% opaque, so picking is never tinted by what's beneath;
- **Transparent areas stay draggable**: even with a fully transparent background, holding the mouse on the invisible base still drags the window — clicks never fall through to the canvas below.

---

## 3. Minimal Borderless Floating & Workflow Aids

- **Show/hide title bar**: press **`Ctrl + Alt + K`** to hide the main window's title bar and frame, leaving only palette and sliders;
- **Follow-mouse hotkey**: press **`Ctrl + Alt + J`** to teleport the window to your cursor;
- **No Focus Mode**: enable under **Settings → Interface**. Tuning colors and dragging sliders in Colorink then never steals keyboard focus from your painting app — its shortcuts stay active;
- **Lock window size & position**: enable "Lock window size" or "Lock window position" in Settings to prevent accidental edge-drags from deforming the window mid-stroke.

---

## 4. UI Themes & the "Eyedropper Custom Skin"

Colorink supports multiple border and slider styles — and can even sample your favorite painting app's theme colors directly:

1. Open **Settings → Interface → UI Theme**;
2. The dropdown offers:
   - **Auto-match**: automatically follows the current painting app;
   - **Dark Gray / Pure White / Pure Black**: classic fixed themes;
   - **Screen Pick (eyedropper customization)**:
     - Selecting "Screen Pick" reveals two eyedropper targets: **Control Bar** and **Background**;
     - Click **【Set】** — the Colorink window hides itself for 3 seconds;
     - Within those 3 seconds, simply hover your mouse over the interface base color of your Photoshop, CSP, or SAI2;
     - When the countdown ends, Colorink samples that spot and blends the entire tool's skin perfectly into your painting app!
