"""Slice pre-warming for ColorWheel.

Extracted from ``ui.color_wheel``: background slice rendering coordination,
cache keys and worker-result handling.
"""

from typing import TypedDict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from ui.slice_prewarm import (
    SlicePrewarmRequest,
    SlicePrewarmResult,
    SlicePrewarmTask,
    render_slice,
)


class _PrewarmedSlice(TypedDict):
    """Payload stored per mode in ``_prewarmed_slices`` by the worker result."""
    key: tuple[object, ...]
    image: QImage
    min_x: int
    min_y: int
    width: int
    height: int
    edge_x: tuple[int, ...] | None


class ColorWheelPrewarmMixin:

    def schedule_slice_prewarm(self, delay_ms: int = 250) -> None:
        """Warm full-resolution slices after the UI has settled."""
        self._prewarm_timer.start(max(0, delay_ms))

    def _slice_cache_key(self, mode: str, center_x: float, center_y: float, radius: float,
                         width: float | None = None) -> tuple[object, ...]:
        if mode in {"hsv-square", "hsl-square"}:
            half = int(radius / 1.414) - 2
            tag = "hsv-square" if mode == "hsv-square" else "square"
            return (int(self.h), half * 2, half * 2, tag)
        if mode == "oklch-slice":
            hue = self._oklch_h if self._oklch_h is not None else self.h
            return (round(hue, 1), radius, round(center_x, 3), round(center_y, 3),
                    round(width or radius, 3), "oklch")
        tag = "hls" if mode == "hls-triangle" else "rgb"
        return (self.h, radius, round(center_x, 3), round(center_y, 3), tag)

    def _invalidate_slice_caches(self) -> None:
        self._prewarm_generation += 1
        self._prewarmed_slices.clear()
        self._cached_img_key = None
        for attr in ("_cached_hls_key", "_cached_rgb_key", "_cached_oklch_key"):
            if hasattr(self, attr):
                delattr(self, attr)

    def prewarm_all_slices(self) -> None:
        """Queue one background full-resolution render for every module slice."""
        if self.width() < 40 or self.height() < 40:
            return
        self._prewarm_generation += 1
        generation = self._prewarm_generation
        self._prewarmed_slices.clear()
        pixel_ratio = max(1.0, float(self.devicePixelRatio()))
        ordered_modes = (self.wheel_mode,) + tuple(
            mode for mode in self._PREWARM_MODES if mode != self.wheel_mode
        )
        for mode in ordered_modes:
            if mode not in self._PREWARM_MODES:
                continue
            geometry = self.get_slice_geometry(mode)
            hue = self._oklch_h if mode == "oklch-slice" and self._oklch_h is not None else self.h
            box_w = self._oklch_slice_box_width(geometry.radius) if mode == "oklch-slice" else None
            request = SlicePrewarmRequest(
                generation=generation, mode=mode, hue=hue,
                center_x=geometry.center_x, center_y=geometry.center_y,
                radius=geometry.radius, pixel_ratio=pixel_ratio,
                width=box_w,
            )
            task = SlicePrewarmTask(request)
            task.signals.finished.connect(self._on_slice_prewarm_finished)
            task.signals.failed.connect(self._on_slice_prewarm_failed)
            self._prewarm_pool.start(task)

    def _on_slice_prewarm_finished(self, result: object) -> None:
        if not isinstance(result, SlicePrewarmResult):
            return
        request = result.request
        if request.generation != self._prewarm_generation:
            return
        image = QImage(
            result.image_bytes, result.image_width, result.image_height,
            result.image_width * 4, QImage.Format.Format_RGBA8888,
        ).copy()
        image.setDevicePixelRatio(request.pixel_ratio)
        self._prewarmed_slices[request.mode] = {
            "key": self._slice_cache_key(
                request.mode, request.center_x, request.center_y, request.radius,
                request.width,
            ),
            "image": image,
            "min_x": result.min_x,
            "min_y": result.min_y,
            "width": result.width,
            "height": result.height,
            "edge_x": result.edge_x,
        }
        self.update()

    def _on_slice_prewarm_failed(self, failure: object) -> None:
        # Prewarming is an optimization only; normal paint remains the fallback.
        return

    def _render_slice_image(
        self, mode: str, hue: float, center_x: float, center_y: float,
        radius: float, width: float | None = None,
        scale: float | None = None, subsample: int = 1,
    ) -> tuple[SlicePrewarmResult, object]:
        """Synchronously render a slice through the vectorized numpy path.

        Replaces the per-pixel Python fallback loops: the numpy renderers
        are an order of magnitude faster, so enlarged ringless slices stay
        responsive while the hue changes. ``subsample`` renders at a reduced
        device resolution and the returned image is already upscaled to the
        logical size (Smooth for square/HLS/RGB, Fast for OKLCh to match the
        legacy interactive preview).
        """
        ratio = max(1.0, float(self.devicePixelRatio()))
        request = SlicePrewarmRequest(
            generation=0, mode=mode, hue=hue,
            center_x=center_x, center_y=center_y, radius=radius,
            pixel_ratio=ratio, width=width, scale=scale,
            subsample=subsample,
        )
        result = render_slice(request)
        image = QImage(
            result.image_bytes, result.image_width, result.image_height,
            result.image_width * 4, QImage.Format.Format_RGBA8888,
        ).copy()
        image.setDevicePixelRatio(ratio)
        if subsample > 1:
            transform = (
                Qt.TransformationMode.FastTransformation
                if mode == "oklch-slice"
                else Qt.TransformationMode.SmoothTransformation
            )
            image = image.scaled(
                int(result.width * ratio), int(result.height * ratio),
                Qt.AspectRatioMode.IgnoreAspectRatio, transform,
            )
            image.setDevicePixelRatio(ratio)
        return result, image
