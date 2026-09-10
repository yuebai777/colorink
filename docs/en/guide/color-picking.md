# Global Color Picking & Magnifier Guide

Colorink ships with a high-precision full-screen picking magnifier and a history swatch system. This page covers every gesture and hotkey in the picking view, sampling details, and history panel management.

---

## 1. Summoning the Full-Screen Magnifier

Press the global hotkey **`Ctrl + Alt + Q`** (customizable in Settings):
- The screen freezes instantly and a high-precision magnified grid pops up around your cursor;
- The magnifier panel automatically dodges your pointer, showing the current pixel's **HEX code**, **RGB values**, and **zoom level** in real time at the lower right;
- **No self-interference**: Colorink captures the screen off-screen at the moment of invocation — its own window, the magnifier, and cursor shadows never appear in the capture.

---

## 2. Core Gestures in the Picking View (Must-Read)

While the magnifier is active, you have precise control via these keys and gestures:

| Key / Gesture | Action | Details & Use Cases |
| :--- | :--- | :--- |
| **`Alt` (hold)** | **Temporarily freeze the frame** | Freezes the current sampling frame. Perfect for capturing video frames, dynamic web pages, animations, or hover colors that vanish easily. Release to resume following. |
| **Mouse wheel** | **Adjust zoom level** | Scroll to zoom the magnifier grid between **2× and 20×** in real time — see fine outlines or individual pixels. |
| **`Shift` (hold)** | **Lock horizontal / vertical axis** | Hold Shift and nudge the mouse to lock sampling to a **pure horizontal** or **pure vertical** axis. Prevents hand jitter from drifting off the baseline when sampling gradient strips or linear swatches. |
| **`Space` (hold)** | **Temporarily hide the crosshair** | Hides the center crosshair and indicator dot so you can see the center pixel unobstructed. Release to bring it back instantly. |
| **Left click** | **Confirm the pick** | Picks the pixel under the crosshair. Colorink updates the foreground color immediately and syncs it to your painting app in an instant. |
| **Right click / `Esc`** | **Cancel and exit** | Aborts this pick and exits the magnifier without changing the current color. |

> [!TIP] Seamless roaming across multi-monitor and HiDPI setups
> The magnifier moves freely across screens. No matter how many monitors you have, or whether their resolutions and Windows scaling factors (DPR 100%, 125%, 150%, 200%) match, Colorink always samples native pixel data from the physical screen under your cursor.

---

## 3. Using & Managing the Color History Panel

The swatch grid at the bottom of the main window automatically records every color you collect:

### Basic interactions
- **Reuse a color**: left-click any swatch in the history to activate it as the current working color;
- **Right-click to copy codes**: **right-click** any swatch for a quick-copy menu:
  - **Copy RGB**: copies as `rgb(r, g, b)`;
  - **Copy HEX**: copies as `#RRGGBB`;
- **Auto-dedupe & promote**: when you mix or pick a color that already exists in history, that swatch is promoted to the first slot instead of stacking duplicates;
- **Two-way sync recording**: when connected to an external painting app (e.g. Photoshop / CSP), colors you eyedrop inside that app are also synced into Colorink's history panel automatically.

### Customizing the swatch grid
Go to **Settings → Color Picker** to tailor the history panel:
- **Columns**: anywhere from 4 to 16 columns;
- **Rows**: 1 to 8 rows;
- **Swatch Size**: freely adjust the pixel width and height of each cell to fit your screen and window width.
