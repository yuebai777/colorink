# Quick Start Guide

Welcome to **Colorink** — a desktop color picking and palette companion deeply crafted for painting and illustration workflows on Windows. This guide gets you installed, familiar with the interface, and fluent with the core hotkeys in 3 minutes.

---

## 1. Download & Run

Head to the official GitHub Releases page:
👉 **[Download the latest Colorink](https://github.com/yuebai777/colorink/releases/latest)**

### Which build should I pick?
- **`Colorink.exe` (single file, highly recommended ⭐⭐⭐⭐⭐)**: one executable — drop it anywhere (Desktop, your art-tools folder) and double-click to run. No extraction, no system residue.
- **`Colorink-Onedir.zip` (portable folder)**: extracts into a standalone folder with all dependency `.dll` files visible. Ideal if your antivirus is suspicious of single-file packers.

> [!NOTE] System requirements
> - OS: Windows 10 / Windows 11 (64-bit)
> - **No Python required**, no manual environment setup — every dependency is bundled.

---

## 2. First-Run Security Prompt (Windows SmartScreen)

Colorink is an independent open-source project without an expensive commercial code-signing certificate, so Windows may show a blue dialog on first launch:
> **"Windows protected your PC"**

**How to proceed:**
1. Click **"More info"** in the dialog;
2. A **"Run anyway"** button appears at the bottom right — click it to launch normally.
3. Colorink is 100% open source and transparent, with all code public on GitHub. Use it with confidence.

---

## 3. Main Window Tour

After launch, the main window presents these areas:

```
+-----------------------------------------------------------+
| [≡ Menu]              Colorink v1.8.x             [⚙ Settings] |
+-----------------------------------------------------------+
|                                                           |
|             [ Color wheel / LAB ring workspace ]          |
|                                                           |
+-----------------------------------------------------------+
| [Foreground] [Background] [Transparent]  [⊙/△ toggle] [Mode] |
+-----------------------------------------------------------+
| [Slider groups (drag to reorder / stack as Tabs)]         |
|  - R / G / B or H / S / V or L / A / B sliders & fields   |
+-----------------------------------------------------------+
| [Color history panel (dedupe & pin / right-click copy)]   |
+-----------------------------------------------------------+
```

1. **Palette workspace**: pick hue, lightness and saturation visually, with 5 color-space modules (HSV / VHSV / HLS / RGB / LCH) and seamless switching between the two big views;
2. **Dual swatches & transparent color**:
   - **Foreground (FG) / Background (BG)**: click to switch the editing target; **right-click for a HEX / RGB / HSL / OKLCh / LAB copy menu**;
   - **Transparent capsule (checkerboard icon)**: one click switches to transparent color, syncing to apps like CSP that support transparent-eraser mode;
3. **Sliders & value panels**: precisely type or nudge values in each color space; with grips enabled, drag to reorder or merge into tabs;
4. **Color history panel**: automatically records screen picks and colors synced from external apps. Left-click to reuse, right-click to copy codes;
5. **Settings gear (top right)**: hotkeys, app connections, window translucency and more.

---

## 4. Default Hotkey Cheat Sheet

Colorink's defaults all use `Ctrl + Alt` combos, deliberately avoiding collisions with the built-in shortcuts of Photoshop, CSP, SAI2 and similar apps:

| Action | Default hotkey | Scope | Notes |
| :--- | :--- | :--- | :--- |
| **Full-screen picking magnifier** | `Ctrl + Alt + Q` | Global | Opens the picking view with Alt freeze, wheel zoom, Shift axis-lock and more |
| **Follow mouse toggle** | `Ctrl + Alt + J` | Global | Moves the Colorink window to your cursor for pen-side tuning |
| **Show / hide window** | `Ctrl + Alt + Y` | Global | Hide to the system tray or summon back instantly |
| **Show / hide title bar** | `Ctrl + Alt + K` | Global | Removes the title bar and frame for a minimal floating mode |
| **Toggle full-screen grayscale filter** | `Ctrl + Alt + D` | Global | Turns the screen (or your pen display) black-and-white to check value structure |
| **Palette view switch (hover)** | `Space` | Local hover | With the cursor over the palette, tap Space to flip between hue ring and LAB disc |
| **Palette view switch (global)** | `Ctrl + Alt + L` | Global | Switch hue ring / LAB disc from anywhere |

> [!TIP] Bind mouse side buttons and stylus keys
> Under **Settings → Hotkeys**, every hotkey can be freely remapped — including **mouse side buttons, the middle wheel button, and even tablet pen buttons**!
> As global hotkeys, mouse clicks are not intercepted, so your painting app still receives pen input normally.

> [!TIP] Unbind hotkeys you don't need
> Two ways to set a hotkey to "None": click the **None** button at the right of each row, or click the hotkey button to enter recording and press **Delete** (Backspace works too).
> Once unbound, the key stops responding immediately and is handed back to your painting app. The button shows "Unbound", persists across restarts, and you can record a new key anytime.

---

## Next Steps

- Master every magnifier gesture? See [Global Color Picking & Magnifier](/en/guide/color-picking)
- Learn the five color modules and harmony systems? See [Palette, Color Spaces & Harmony](/en/guide/palette-and-harmony)
- Connect your painting app? See [App Sync Overview](/en/sync/overview)
