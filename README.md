# Colorink

A professional on-screen color picker and palette tool built with PyQt6.

Support me at [爱发电](https://afdian.com/a/colorink)

## Features

- **Dual Color Picker** — HSV color wheel and CIELAB color space visualizer with live preview
- **Multi-Space Sliders** — RGB, HSV, HSL, CIELAB, OKLab, OKLCh — all editable in real time
- **Foreground / Background Slots** — swap, copy, and compare colors instantly
- **Color History Grid** — persistent palette with configurable swatch size and layout
- **Fullscreen Grayscale Overlay** — toggle a perceptual (OKLCh) or BT.709 luma-based grayscale filter across your entire screen via DirectComposition or GDI overlay
- **Global Hotkeys** — pick colors or toggle the overlay without leaving your current application
- **Photoshop Bridge** — send colors directly to Photoshop's foreground/background via JSX scripting
- **DPI-Aware** — handles multi-monitor DPI transitions correctly
- **Frameless & Always-on-Top** — minimal footprint; locks to screen edge when not in use
- **Customizable UI** — slider order, visibility, color space mode, theme, and UI scale

## Screenshots

![Colorink Screenshot](screenshots/screenshot.png)

## Requirements

- Windows 10+ (64-bit)
- Python 3.10+

## Installation

```bash
git clone https://github.com/yuebai777/colorink.git
cd colorink
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

Or double-click `run.bat`.
 |

## Building (Standalone EXE)

```bash
pip install pyinstaller
python build_pyqt.py
```

Output will be in `dist/`.

## License

MIT — see [LICENSE](LICENSE) for details.
