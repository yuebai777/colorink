"""One picker page per color-space module.

The wheel module tabs are real pages: each one owns its colour wheel, keeps
its own native mode, and can be torn off into a floating window without
disturbing the other modules. The MainWindow owns the page stack; this module
only keeps the widget's wiring in one place.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ui.color_wheel import ColorWheel


class WheelModulePanel(QWidget):
    """A colour wheel wired for one wheel mode (HSV / VHSV / HLS / RGB / LCH)."""

    #: The wheel is square and expands; a holder must not add a bottom stretch
    #: that would leave it small at the top of a tab page.
    fill_holder = True

    def __init__(self, key: str, wheel_mode: str, parent=None):
        super().__init__(parent)
        self.key = str(key).lower()
        self.setObjectName(f"WheelModulePanel_{self.key}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.wheel = ColorWheel(self)
        self.wheel.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        self.wheel.set_wheel_mode(wheel_mode)
        layout.addWidget(self.wheel)

    def sizeHint(self) -> QSize:
        """A wheel page is square and follows its width (even in a float)."""
        width = int(self.width())
        if self.parentWidget() is None or width < 120 or width >= 600:
            width = 220
        return QSize(width, width)

    def minimumSizeHint(self) -> QSize:
        return QSize(120, 120)

    def title(self) -> str:
        return self.key.upper()