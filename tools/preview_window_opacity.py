"""Render the window chrome at several background-opacity levels.

Visual + numeric check for `ui/chrome_opacity.py` and the chrome CSS in
`ui/window/theme.py`: the panel background, the window frame and the
title band must fade together over whatever is behind the window, while the
sliders, value boxes and the colour history keep their exact colours.

    python tools/preview_window_opacity.py                  # 100 / 60 / 30 %
    python tools/preview_window_opacity.py 100 50 20        # pick the levels
    python tools/preview_window_opacity.py --theme black
    python tools/preview_window_opacity.py --out screenshots

The strip PNG lands in `.pytest_tmp/chrome/` (override with --out). Each level
also prints the composited pixel it produced next to the value the alpha
blend predicts, so the render can be verified without looking at the image.
"""

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import default_hotkey_config  # noqa: E402
from ui.chrome_opacity import CHROME_OPACITY_KEY  # noqa: E402
from ui.color_history import ColorHistoryWidget  # noqa: E402
from ui.widgets.gradient_slider import GradientSlider  # noqa: E402
from ui.widgets.title_bar import TitleBar  # noqa: E402
from ui.window.theme import ThemeMixin  # noqa: E402

#: Window background / frame colours of the fixed UI themes (ui/window/theme.py),
#: used to predict the composited pixels the render has to reproduce.
THEME_COLORS = {
    "gray": {"bg": "#b2b2b2", "chrome": "#787878"},
    "white": {"bg": "#ffffff", "chrome": "#b2b2b2"},
    "black": {"bg": "#1e1e1e", "chrome": "#2d2d2d"},
}

#: Stand-in for the artwork the picker floats over.
CANVAS_A = QColor("#2f6f4f")
CANVAS_B = QColor("#d8c48a")
PANEL_W, PANEL_H = 240, 210


class ChromeHost(ThemeMixin, QWidget):
    """The pieces of MainWindow that ThemeMixin.apply_theme actually paints.

    The nesting mirrors the real window on purpose: the *top level* is the
    translucent one, and the styled background lives on a plain child named
    CentralWidget. Putting both on one widget would silently paint nothing —
    Qt skips the background of a translucent (WA_NoSystemBackground) widget.
    """

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        self.central = QWidget(self)
        self.central.setObjectName("CentralWidget")
        window_layout.addWidget(self.central)

        self.main_layout = QVBoxLayout(self.central)
        self.title_bar = TitleBar(self)
        self.main_layout.addWidget(self.title_bar)

        self.sliders_container = QWidget(self.central)
        self.sliders_layout = QVBoxLayout(self.sliders_container)
        self.main_layout.addWidget(self.sliders_container)

        self.slider_row_layouts = []
        self.slider_labels = {}
        self.slider_widgets = {}
        self.slider_containers = {}
        container = QWidget(self.sliders_container)
        group_layout = QVBoxLayout(container)
        for chan, (lo, hi) in (
            ("R", ("#000000", "#ff0000")),
            ("G", ("#000000", "#00b050")),
            ("B", ("#000000", "#3060ff")),
        ):
            row = QHBoxLayout()
            self.slider_row_layouts.append(row)
            label = QLabel(chan, container)
            label.setObjectName("ChannelLabel")
            slider = GradientSlider(Qt.Orientation.Horizontal, container)
            slider.set_gradient([(0.0, QColor(lo)), (1.0, QColor(hi))])
            slider.setValue(60)
            value = QLabel("153", container)
            value.setObjectName("ValueLabel")
            row.addWidget(label)
            row.addWidget(slider)
            row.addWidget(value)
            group_layout.addLayout(row)
            self.slider_labels[chan] = label
            self.slider_widgets[chan] = (slider, value)
        self.slider_containers["RGB"] = container
        self.sliders_layout.addWidget(container)

        self.color_history = ColorHistoryWidget(self.sliders_container)
        self.color_history.set_colors([QColor("#c94f4f"), QColor("#4f7fc9")])
        self.sliders_layout.addWidget(self.color_history)

        self.btn_mode_wheel = QPushButton("O", self.central)
        self.btn_mode_lab = QPushButton("A", self.central)
        self.btn_mode_lab.setVisible(False)

    def update_mode_buttons_visibility(self):
        pass


def canvas_image(width, height):
    """Two flat bands, so the see-through effect is readable as numbers."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.fillRect(0, 0, width, height // 2, CANVAS_A)
    painter.fillRect(0, height // 2, width, height - height // 2, CANVAS_B)
    painter.end()
    return image


def blend(top: QColor, bottom: QColor, alpha: float) -> tuple[int, int, int]:
    """Straight source-over blend, the value the render has to reproduce."""
    return tuple(
        round(getattr(top, ch)() * alpha + getattr(bottom, ch)() * (1.0 - alpha))
        for ch in ("red", "green", "blue")
    )


def render_level(theme: str, opacity: int):
    cfg = default_hotkey_config()
    cfg["ui-theme"] = theme
    cfg[CHROME_OPACITY_KEY] = opacity
    host = ChromeHost(cfg)
    host.resize(PANEL_W, PANEL_H)
    host.show()  # title bar must be visible: it owns the top edge
    host.apply_theme(1.0, is_resize_event=True)

    image = canvas_image(PANEL_W, PANEL_H)
    host.render(image)
    host.hide()
    # The caller keeps the host alive: dropping a just-shown widget mid-loop
    # tears down its children (title bar, history overlay) while Qt still has
    # events queued for them, which aborts the process on Windows.
    return host, image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("levels", nargs="*", type=int, default=None,
                        help="opacity percentages (default: 100 60 30)")
    parser.add_argument("--theme", default="gray", help="ui-theme to render")
    parser.add_argument("--out", default=os.path.join(".pytest_tmp", "chrome"),
                        help="output directory for the strip PNG")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])  # keep it referenced
    levels = args.levels or [100, 60, 30]

    gap = 12
    strip = QImage(len(levels) * PANEL_W + (len(levels) + 1) * gap,
                   PANEL_H + 2 * gap, QImage.Format.Format_ARGB32)
    strip.fill(QColor("#101010"))
    painter = QPainter(strip)

    print(f"theme={args.theme}  canvas top={CANVAS_A.name()} bottom={CANVAS_B.name()}")
    palette = THEME_COLORS.get(args.theme)
    rendered = []
    for index, opacity in enumerate(levels):
        host, image = render_level(args.theme, opacity)
        rendered.append(host)
        painter.drawImage(gap + index * (PANEL_W + gap), gap, image)

        alpha = opacity / 100.0
        print(f"\n--- {opacity}% ---")
        for name, point, canvas in (
            ("body ", (PANEL_W // 2, PANEL_H - 7), CANVAS_B),
            ("frame", (1, PANEL_H - 7), CANVAS_B),
            ("title", (40, 9), CANVAS_A),
        ):
            sampled = QColor(image.pixel(*point))
            line = f"  {name} {sampled.name()}  over canvas {canvas.name()}"
            if palette is not None:
                body = QColor(*blend(QColor(palette["bg"]), canvas, alpha))
                # The frame and the title band are painted on top of the
                # window background, so their alphas compose with it.
                expected = body if name == "body " else QColor(
                    *blend(QColor(palette["chrome"]), body, alpha))
                # ±1 per channel: Qt rounds through premultiplied alpha.
                off = max(abs(getattr(sampled, ch)() - getattr(expected, ch)())
                          for ch in ("red", "green", "blue"))
                line += f"   expected {expected.name()}"
                line += "  OK" if off <= 1 else f"  MISMATCH (off by {off})"
            print(line)
    painter.end()

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"chrome_opacity_{args.theme}.png")
    strip.save(path)
    print(f"\nstrip -> {path}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
