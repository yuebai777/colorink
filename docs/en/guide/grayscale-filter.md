# Hardware-Level Grayscale Filter & Value Checking

In color illustration and concept art, **the value structure (Value / Luminance — the black-white-gray relationship) determines a piece's volume, depth, and visual guidance**.

Colorink includes a hardware-level, one-key global grayscale filter. No need to manually create debug layers inside your painting app — one hotkey instantly turns your screen into a scientific black-and-white view.

---

## 1. Toggling It On and Off

- **Toggle hotkey**: press the global hotkey **`Ctrl + Alt + D`** (customizable in Settings);
- **Instant on/off**: one press turns the whole screen (or a designated screen) grayscale; press again to restore full color losslessly;
- **Paint directly in the grayscale view**: the filter never intercepts your mouse, stylus, or painting-app input — you can keep adjusting brushwork while viewing in black and white.

---

## 2. Why Every Painter Needs Global Value Checking

1. **Quickly audit subject-vs-background contrast**: with many colors on screen, the eye is easily fooled by hue and saturation. Grayscale instantly reveals whether the subject blends into the background, whether shadows are muddy, or highlights blown out;
2. **Cross-app reference comparison**: web references, pure-color swatches, 3D aids, and your canvas all go gray together, making horizontal value comparison effortless;
3. **Never "forget to delete the B&W layer" again**: the traditional approach — a black-and-white adjustment layer on top of CSP or PS — is often forgotten at export or flatten time. A global filter writes nothing into your files: zero pollution.

---

## 3. Scientific Value Models: OKLCh vs Luma

Under **Settings → Grayscale → Grayscale Mode**, choose between luminance models:

| Mode | Math | Strengths & best use |
| :--- | :--- | :--- |
| **OKLCh Perceptual (strongly recommended)** | Based on the latest OKLCh perceptual science | **Matches real human vision.** The eye is highly sensitive to yellow and insensitive to deep blue. Naive desaturation turns pure yellow gray; OKLCh correctly renders yellow as light gray and blue as near-black — true drawing values. |
| **Luma (BT.709 standard)** | International TV/video standard (`0.2126 R + 0.7152 G + 0.0722 B`) | Broadcast-grade luminance — suited to film concept design and animation storyboard value checks. |

---

## 4. Rendering Backends

Choose a backend under **Settings → Grayscale → Run Mode**:

- **DComp (DirectComposition, default, recommended ⭐⭐⭐⭐⭐)**: a modern Windows zero-copy VRAM-direct architecture with sub-1ms latency and near-zero overhead; **fully supports OKLCh perceptual grayscale** and per-monitor targeting;
- **Native (DXGI + OpenGL, solid alternative ⭐⭐⭐⭐)**: DXGI desktop capture with OpenGL acceleration; **also fully supports OKLCh** and single-monitor targeting. If DComp fails to activate under special multi-GPU environments or drivers, switch to Native first;
- **Mag (Windows Magnifier)**: system-level kernel color-matrix transform.
  > [!WARNING] Note: Mag mode cannot do OKLCh
  > Limited by Windows' underlying APIs, Mag mode **cannot support OKLCh perceptual grayscale (only traditional linear Luma), and cannot target a single screen**. For scientific OKLCh value checking, always use **DComp** or **Native**.

---

## 5. Multi-Monitor Tips for Painters

If you run dual screens (e.g. one main monitor for references, one pen display for painting):

1. Open **Settings → Interface / Hotkeys → Grayscale Target Screen**;
2. The dropdown lists all connected displays (e.g. `0: DISPLAY1`, `1: DISPLAY2`);
3. **Select your pen display**;
4. Now pressing `Ctrl + Alt + D` turns **only your painting screen black-and-white while the reference screen stays in full color** — extremely efficient for comparative work.
