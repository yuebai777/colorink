# Painting App Sync Overview

Colorink has a powerful external color-sync hub that pushes the colors you pick or fine-tune in the palette directly into your target painting app — in milliseconds.

---

## 1. Supported Apps & Mode Matrix

| App | Supported versions | Recommended sync mode | FG/BG two-way | Transparent color | Privileges |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auto-detect** | All supported apps | **Smart foreground follow** | ✅ Per app | ✅ Per app | ❌ No elevation |
| **CLIP STUDIO PAINT (CSP)** | 1.x / 2.x / 3.x / 4.x / 5.x | **Companion mode (recommended)**<br>Memory sync (legacy) | ✅ Full | ✅ Native | ❌ No elevation |
| **Adobe Photoshop (PS)** | CC 2015 ~ 2026+ (retail/portable) | **Unified CEP extension bridge** | ✅ Full | ❌ (PS has no native transparent slot) | ❌ User dir, no elevation |
| **PaintTool SAI 2** | 64-bit versions after 2020 | **Memory-level sync** | ❌ (brush main color only) | ❌ | ❌ Usually none |
| **UDM PAINT (优动漫)** | 4.0 Pro / EX | **Memory-level sync** | ❌ (brush main color only) | ❌ | ❌ Usually none |

---

## 2. Core Features & Workflow Mechanics

### 1. Auto-detect foreground app (Auto Mode, strongly recommended)
Select **【Auto-detect】** under **Settings → Sync → Target App**:
- While you paint in CSP, Colorink connects to CSP automatically;
- Switch to Photoshop and Colorink swaps to the Photoshop CEP bridge in milliseconds;
- Same for SAI 2 — no more manual switching when working across apps!

### 2. Independent two-way FG/BG channels
- **Colorink → App**: clicking the FG or BG swatch in Colorink only affects the currently selected slot;
- **App → Colorink**: eyedrop a canvas color inside Photoshop or CSP, and Colorink senses it, back-fills the matching slot, and records it in the history panel.

### 3. Native "transparent color" passthrough
Many CSP painters use transparent color as an eraser that keeps the current brush texture:
- Click the **transparent capsule button (checkerboard icon)** in Colorink;
- CSP switches to its transparent slot instantly; pick any color in Colorink again and CSP seamlessly returns to a normal colored brush.

### 4. Live status light & diagnostics
At the bottom of **Settings → Sync**, Colorink provides precise status monitoring:
- 🟢 **Green**: connected — your next stroke takes effect immediately;
- 🔴 **Red**: no running target app detected, or connection dropped;
- Click **【Copy Diagnostics】** to copy the current app version, system environment, and detailed sync error log to the clipboard for easy bug reports.

---

## 3. Per-App Setup Guides

Jump into the illustrated guide for your app:

- 👉 **[CLIP STUDIO PAINT (CSP) Setup Guide](/en/sync/csp)** (focus: QR pairing in phone mode)
- 👉 **[Adobe Photoshop (PS) Setup Guide](/en/sync/photoshop)** (focus: one-click CEP extension install)
- 👉 **[PaintTool SAI 2 & UDM PAINT Setup Guide](/en/sync/sai2)** (focus: memory mapping & redraw modes)
