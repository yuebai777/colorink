# Adobe Photoshop (PS) Sync Guide

For Adobe Photoshop, Colorink developed a **unified CEP extension bridge that requires no admin elevation**, supporting every version from CC 2015 to the latest Photoshop 2026+.

---

## 1. Core Technical Features

- **Per-user install, no admin UAC**: the plugin deploys fully automatically into the current user's directory (`%APPDATA%\Adobe\CEP\extensions`) — never touches sensitive system-drive files, never asks for administrator rights;
- **All versions, all flavors**: works with Creative Cloud subscriptions, portable builds, and offline lite editions;
- **Precise PID routing across multiple windows**: when several Photoshop versions are open at once, Colorink automatically follows the foreground PS window you're actually painting in;
- **Fully independent two-way FG/BG sync**:
  - Colors picked in Colorink become PS's foreground (or background) color in real time;
  - Eyedropping a canvas color in PS sends it back to Colorink instantly and records it in the history panel;
- **Minimal overhead, peripheral-friendly**: the event stream is carefully optimized and fully compatible with third-party painting extensions like **TourBox** controllers and the **Coolorus** color wheel.

---

## 2. Deployment & Connection (Fully Automatic)

### Step 1: One-click CEP extension deployment
1. Open Colorink and go to **Settings → Sync**;
2. Select **Photoshop** as the target app (or choose **Auto-detect**);
3. Click **【Install Photoshop CEP Extension】**;
4. After the success prompt, **close and restart Photoshop once**.

### Step 2: Enable sync
1. Launch Photoshop and **create or open a canvas**;
2. Open Colorink — the status light in Settings turns green: **"Photoshop connected"**;
3. Now, whether you pick with the `Ctrl+Alt+Q` magnifier or drag the palette, Photoshop's toolbar foreground color follows in real time!

---

## 3. One-Click Uninstall & Cleanup

If you no longer need Photoshop color sync:
1. Open Colorink **Settings → Sync → Photoshop**;
2. Click **【Uninstall / Clean up CEP Extension】**;
3. Colorink cleanly removes the extension files from the user directory — no residue left behind.

---

## 4. Troubleshooting & Notes

### 1. Still "Not connected" after installing and restarting?
- **Is a canvas open? (critical)**: with no canvas or PSD open, Photoshop's internal color engine refuses external injection. Always **create a canvas first** when testing;
- **Plugin loading switch**: in Photoshop's menu, open **Edit → Preferences → Plug-ins** and make sure generator / extension loading is enabled.

### 2. Privilege consistency
If you habitually run Photoshop "as administrator", Colorink must also run as administrator — otherwise Windows' security isolation blocks local IPC between the two apps.
