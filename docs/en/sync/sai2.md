# PaintTool SAI2 / UDM PAINT (优动漫) Sync Guide

Colorink provides a dedicated memory-level brush-color sync engine and smart UI-refresh mechanism for the beloved **PaintTool SAI 2** and the Chinese edition **UDM PAINT (优动漫 PAINT Pro / EX)**.

---

## 1. How It Works & Features

- **Memory-level brush-color injection**: directly targets the active brush color data inside the painting app — color changes take effect the instant you tune them;
- **Smart swatch refresh (for SAI2)**: solves the classic third-party-tool problem where SAI 2's small color cells on the left fail to redraw after an external color change;
- **SAI2 color-wheel follow (new)**: when writing a color, Colorink also updates SAI's own color-panel state — the **wheel (ring and inner square rendering, ring marker, square pick point) and the slider-track gradients** follow the new color (see "Panel Mode" below); slider thumbs and the numeric readouts on the right **do not** follow — reasons below;
- **No extra plugins required**: works out of the box with automatic process discovery.

---

## 2. Connecting PaintTool SAI 2

### Step 1: Prepare SAI 2
1. Launch PaintTool SAI 2 (make sure it's the 64-bit version);
2. **Create or open a canvas** (SAI 2's brush-color engine sleeps until a canvas exists);
3. Activate the brush tool you want to use (pencil, watercolor, brush, etc.).

### Step 2: Enable Colorink sync
1. Open Colorink and go to **Settings → Sync**;
2. Select **SAI2** as the target app (or simply **Auto-detect**);
3. Colorink automatically captures the `sai2.exe` process in the background; within seconds the light turns green: **"Connected"**;
4. Any color you adjust in Colorink now changes SAI 2's brush color in real time.

### SAI2 wheel-follow & Panel Mode (Settings → Sync → SAI2 Panel Mode)

When Colorink writes a color, it syncs into SAI's own color-panel state. **What follows**: the ring and inner-square rendering, ring marker, square pick point, and the six slider-track gradients. **What doesn't**: the triangular slider thumbs and the numeric readouts at the right of the tracks (numbers like `186 / 049 / 222 / 288 / 078 / 087`).

> Why don't the thumbs and numbers follow? Testing (Sep 2026, Preview.2024.08.14) shows these are driven by SAI's own slider-control state, not by the three swatch fields Colorink writes. Even forcing a full-panel `RedrawWindow` repaints the track gradients but leaves thumbs and numbers untouched. Moving them would require simulating clicks on SAI's own input paths (clicking tracks / swatch cells) — which Colorink deliberately never does, to avoid mis-clicks. This is a design choice, not a configuration issue.

| Option | Description |
| :--- | :--- |
| **auto (default, recommended)** | Auto-detects the panel mode you use in SAI (HSV / VHSV / HSL). Detection has three layers: SAI's own slider labels (`V` / `L`), **reverse-computing the current color under each mode and comparing against SAI's swatch**, and SAI's numeric readouts on the panel's right. Catches up within ~2 seconds after a mode switch |
| **vhsv / hsv / hsl** | Manual override. For cases auto-detect can't handle (e.g. SAI's current color is grayscale with no hue to judge, and the panel readouts are also stuck in gray) |

> Note: SAI's three panel modes assign different meanings to the in-memory fields (in HSL the first field is lightness L; in HSV/VHSV it's value V; VHSV's saturation is SAI's own non-linear definition). Colorink converts according to the selected mode, so **as long as the selected mode matches what SAI actually displays, the wheel position is accurate**.
> If the wheel position looks off, first confirm this option matches the mode shown at SAI's panel top-right/bottom. Changes apply immediately — no restart needed.

### SAI2 UI refresh (always full refresh since v1.8.7)

Since v1.8.7, Colorink removed the "UI refresh mode" option (old configs migrate automatically) and always performs a full refresh:

| Action | Description |
| :--- | :--- |
| **Redraw palette swatches** | Sends a partial-redraw signal to SAI 2's palette swatch region so cells refresh immediately |
| **Refresh brush preview** | Also refreshes the brush-preview strip, so previews no longer lag behind after long sessions |

> Full refresh **injects no simulated mouse clicks**, so it never causes "wedge-shaped stroke starts"; if a preview target becomes invalid it is rediscovered automatically — no need to restart Colorink.

---

## 3. Connecting UDM PAINT (优动漫)

UDM PAINT (the Chinese edition of CLIP STUDIO PAINT, process names `UDMPaintPro.exe` or `UDMPaintEX.exe`):

1. Open UDM PAINT and create or open a canvas;
2. Open Colorink **Settings → Sync** and select **UDM** (or keep **Auto-detect**);
3. Colorink supports its 4.0 Pro and 4.0 EX versions, auto-detecting and connecting to the brush-color memory;
4. Once connected, Colorink's foreground color syncs to UDM PAINT's current brush in real time.

---

## 4. Troubleshooting & Notes

### 1. Status stuck at "Not connected"?
- **Is a canvas open?**: make sure a canvas is actually open;
- **Privilege consistency**: if you run SAI2 or UDM PAINT as administrator, Windows forbids normal-privilege programs from reading their memory. Align both sides (both normal, or both admin);
- **Confirm 64-bit**: SAI 2 must be the 64-bit version (mainstream versions after 2020 all are);
- **Check the diagnostic reason**: the `reason` field in Settings' "Copy Diagnostics" states the failure type directly —
  `not_running` (app not open / no canvas), `access_denied` (privilege mismatch),
  `no_signature` (this SAI version isn't adapted yet — manually pick "SAI2 version" as a workaround),
  `write_verify_failed` (the write was disturbed by another program).
- With **SAI2 version** set to `auto`, Colorink **walks all known signatures** and uses the one that can read the color slot; it falls back to fixed offsets only if all fail. Manual selection is rarely needed — report for adaptation only when a new SAI version raises `no_signature`.
