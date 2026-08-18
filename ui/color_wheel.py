"""Color wheel widget with rendering/geometry/interaction/prewarm split.

The ``ColorWheel`` class now inherits concern mixins from
``color_wheel_rendering``, ``color_wheel_geometry``,
``color_wheel_interaction`` and ``color_wheel_prewarm``.  This module keeps
the widget identity, core state, and the public helper re-exports that other
modules rely on (``SliceGeometry``, ``hsv_to_rgb``, ``project_point_to_triangle``,
etc.).
"""

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from ui.ringless_mode import RinglessLayout

from core import config
from ui.color_conversions import lab_to_rgb, rgb_to_hsv
from ui.color_wheel_geometry import (
    ColorWheelGeometryMixin,
    SliceGeometry,
    hls_to_hsv_floats,
    hsv_to_rgb,
    project_point_to_triangle,
)
from ui.color_wheel_interaction import ColorWheelInteractionMixin
from ui.color_wheel_prewarm import ColorWheelPrewarmMixin, _PrewarmedSlice
from ui.color_wheel_rendering import ColorWheelRenderingMixin


class ColorWheel(
    ColorWheelRenderingMixin,
    ColorWheelGeometryMixin,
    ColorWheelInteractionMixin,
    ColorWheelPrewarmMixin,
    QWidget,
):
    # Emits (r, g, b)
    colorChanged = pyqtSignal(int, int, int)
    interactionFinished = pyqtSignal()

    _PREWARM_MODES = (
        "hsv-square", "hls-triangle", "rgb-slice", "oklch-slice",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.cfg = config.load_hotkey_config()

        # Color state (HSV)
        self.h = 0.0
        self.s = 100.0
        self.v = 100.0
        self._last_hue = 0.0

        # Direct OKLCh state — set by external callers so the indicator
        # doesn't have to round-trip through HSV→RGB→OKLCh.
        self._oklch_L = None      # float 0-1 or None
        self._oklch_C = None      # float 0-0.5 or None
        self._oklch_h = None      # float 0-360 or None

        self.dragging = None

        # Mode
        self.wheel_mode = "hsv-square"

        # Ringless-mode layout (None = full mode / disabled)
        self._ringless_layout: RinglessLayout | None = None

        # Cache variables for fast rendering
        self._cached_img = None
        self._cached_img_key = None

        # Full-resolution slice warmups are generated off the GUI thread.
        # The result is kept per mode so switching modules does not throw away
        # the other modes' ready-to-paint images.
        self._prewarmed_slices: dict[str, _PrewarmedSlice] = {}
        self._prewarm_generation = 0
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.timeout.connect(self.prewarm_all_slices)
        self._prewarm_pool = QThreadPool(self)
        self._prewarm_pool.setMaxThreadCount(1)

    def resizeEvent(self, event):
        """Invalidate cached ring image on resize and force a full repaint.

        Without this, when the window is occluded and then resized, Qt's
        backing store (WA_TranslucentBackground) may leave stale pixels in
        previously-occluded regions because the layout-triggered resize does
        not automatically schedule a paint event for those areas.
        """
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        self._invalidate_slice_caches()
        super().resizeEvent(event)
        self.update()

    def reload_config(self):
        self.cfg = config.load_hotkey_config()
        # Invalidate the ring cache so it gets redrawn with the new settings
        if hasattr(self, "_cached_ring_key"):
            delattr(self, "_cached_ring_key")
        self.update()


__all__ = [
    "ColorWheel",
    "SliceGeometry",
    "hsv_to_rgb",
    "rgb_to_hsv",
    "hls_to_hsv_floats",
    "lab_to_rgb",
    "project_point_to_triangle",
]
