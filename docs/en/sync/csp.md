# CLIP STUDIO PAINT (CSP) Sync Guide

CLIP STUDIO PAINT (CSP) is the painting app Colorink is most deeply adapted for. The officially protocol-based **Companion mode** is recommended.

---

## Mode 1: Companion Mode (Strongly Recommended ⭐⭐⭐⭐⭐)

### Why Companion first?
- **Lifetime cross-version compatibility**: communicates over CSP's official built-in "smartphone companion" LAN TCP protocol — completely unaffected by CSP upgrades (2.x / 3.x / 4.x / 5.x);
- **Full capability support**: not only foreground and background colors, but also **transparent color (transparent-brush eraser)**;
- **Persistent silent auto-reconnect**: after the first successful pairing, the configuration is saved and every subsequent launch retries the connection automatically.

---

### Three-step pairing (30 seconds)

#### Step 1: Open the pairing QR code in CSP
1. Launch CLIP STUDIO PAINT on your computer;
2. Find the **"Connect to smartphone"** icon (phone shape) in the top command bar or menu and click it;
3. A dialog with the pairing QR code appears.

#### Step 2: One-click auto-scan in Colorink
1. Open Colorink and go to **Settings → Sync**;
2. Select **CSP (Companion)** as the target app;
3. Click the **【Auto-scan screen QR code】** button;
4. Colorink instantly recognizes the on-screen QR code and completes the two-way pairing handshake;
5. Once the status shows green **"Connected"**, close CSP's QR dialog and start painting!

---

### Manual fallback when auto-scan fails
If auto-scan can't recognize the code due to multiple monitors, extreme display scaling, or window overlap:
1. Below the QR dialog in CSP, click **"If you cannot scan the QR code"** (or "Show connection code");
2. CSP displays a local LAN address (e.g. `http://192.168.x.x:54321?...`);
3. Copy the whole address and paste it into Colorink's **"Manual connection URL"** field;
4. Click the **【Connect】** button beside it — done.

---

## Mode 2: CSP Memory Sync (Legacy Fallback)

For older machines that cannot use LAN networking, Colorink keeps a memory-scan-based sync mode:

1. Go to **Settings → Sync** and select **CSP (Memory mode)**;
2. Pick the matching version in the dropdown (e.g. `csp4.x` or `csp5.x`);
3. Make sure CSP has a canvas open — Colorink hooks into memory and syncs colors automatically.

> [!WARNING] Critical version note
> - **CSP 5.1+ has completely removed memory-sync support**: the official 5.1 release restructured the underlying color data, so **CSP 5.1 and above must use Companion mode**;
> - Memory mode only syncs the main color — no background or transparent color.
