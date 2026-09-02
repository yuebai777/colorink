"""Render slider-handle close-ups for every slider theme.

Visual check for `ui/slider_themes.py` / `ui/border_themes.py` tweaks: the
handle indicators are 7-15px tall, so a bug (clipping, asymmetry, a stray
antialiasing fringe) is invisible in a normal screenshot but obvious in a
magnified render or an ASCII luminance map.

    python tools/preview_slider_handles.py            # PNGs + ASCII maps
    python tools/preview_slider_handles.py --scale 24 # bigger PNGs
    python tools/preview_slider_handles.py sai        # one theme only

PNGs land in `.pytest_tmp/handles/`. Nearest-neighbour upscaling is used so
one source pixel stays one visible block.
"""

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.border_themes import (  # noqa: E402
    BORDER_THEME_AUTO,
    get_border_theme,
    resolve_border_theme,
    resolve_border_theme_key,
)
from ui.slider_themes import SLIDER_THEMES  # noqa: E402
from ui.widgets.gradient_slider import GradientSlider  # noqa: E402

RAMP = " .:-=+*#%@"
PANEL_BG = {"ps": "#535353", "sai": "#f7f7f7", "csp": "#adadad", "default": "#b2b2b2"}


def render_handle(slider_style, width=121, height=None, bg="#eeeeee"):
    """Render one slider and return (widget, image)."""
    border = resolve_border_theme(
        get_border_theme(resolve_border_theme_key(BORDER_THEME_AUTO, slider_style)),
        chrome_border="#787878", input_bg="#2e2e2e",
        input_border="#555555", text="#ffffff",
    )
    slider = GradientSlider(Qt.Orientation.Horizontal)
    slider.set_gradient([(0.0, QColor("#3060a0")), (1.0, QColor("#ffffff"))])
    slider.update_scale(1.0, SLIDER_THEMES[slider_style], border)
    slider.setRange(0, 100)
    slider.setValue(50)
    slider.resize(width, height or slider.minimumHeight())

    image = QImage(slider.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(bg))
    slider.render(image)
    return slider, image


def ascii_map(image, x0, x1):
    lines = []
    for y in range(image.height()):
        row = ""
        for x in range(x0, x1):
            color = QColor(image.pixel(x, y))
            lum = (color.red() * 299 + color.green() * 587 + color.blue() * 114) // 1000
            row += RAMP[min(9, (255 - lum) * 10 // 256)]
        lines.append(f"{y:3d} |{row}|")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("themes", nargs="*", default=None,
                        help="slider theme keys (default: all)")
    parser.add_argument("--scale", type=int, default=16, help="PNG zoom factor")
    parser.add_argument("--height", type=int, default=None,
                        help="row height (default: the slider's minimum)")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    out_dir = os.path.join(os.getcwd(), ".pytest_tmp", "handles")
    os.makedirs(out_dir, exist_ok=True)

    for style in (args.themes or list(SLIDER_THEMES)):
        if style not in SLIDER_THEMES:
            print(f"unknown slider theme: {style}")
            continue
        slider, image = render_handle(
            style, height=args.height, bg=PANEL_BG.get(style, "#eeeeee")
        )
        center = round(0.5 * slider.width())
        x0, x1 = max(0, center - 13), min(image.width(), center + 14)

        crop = image.copy(x0, 0, x1 - x0, image.height())
        big = crop.scaled(
            crop.width() * args.scale, crop.height() * args.scale,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        path = os.path.join(out_dir, f"handle_{style}.png")
        big.save(path)

        print(f"\n=== {style} === groove_h={slider.groove_h} "
              f"indicator_extent={slider._triangle_extent()} "
              f"row_height={slider.height()} -> {path}")
        for line in ascii_map(image, x0, x1):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
