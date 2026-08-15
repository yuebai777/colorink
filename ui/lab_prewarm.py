from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


@dataclass(frozen=True, slots=True)
class LabPrewarmRequest:
    generation: int
    render_mode: str
    lightness: float
    size: int
    min_a: float
    max_a: float
    min_b: float
    max_b: float
    pixel_ratio: float


@dataclass(frozen=True, slots=True)
class LabPrewarmResult:
    request: LabPrewarmRequest
    image_width: int
    image_height: int
    image_bytes: bytes


class LabPrewarmSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)


class LabPrewarmTask(QRunnable):
    def __init__(self, request: LabPrewarmRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = LabPrewarmSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(render_lab_plane(self.request))
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            self.signals.failed.emit((self.request, repr(exc)))


def _rgba_bytes(red: np.ndarray, green: np.ndarray, blue: np.ndarray, mask: np.ndarray) -> bytes:
    rgba = np.zeros((*red.shape, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(red, 0.0, 255.0).astype(np.uint8)
    rgba[..., 1] = np.clip(green, 0.0, 255.0).astype(np.uint8)
    rgba[..., 2] = np.clip(blue, 0.0, 255.0).astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    return rgba.tobytes()


def _lab_to_rgb(lightness: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y_const = (lightness + 16.0) / 116.0
    x = a / 500.0 + y_const
    z = y_const - b / 200.0
    x_val = 0.96422 * np.where(x > 0.206893, x ** 3, (x - 16.0 / 116.0) / 7.787)
    y_val = np.where(y_const > 0.206893, y_const ** 3, (y_const - 16.0 / 116.0) / 7.787)
    z_val = 0.82521 * np.where(z > 0.206893, z ** 3, (z - 16.0 / 116.0) / 7.787)
    x65 = 0.9555766558 * x_val - 0.0230393428 * y_val + 0.0631636684 * z_val
    y65 = -0.0282895469 * x_val + 1.0099416212 * y_val + 0.0210076609 * z_val
    z65 = 0.0122981793 * x_val - 0.0204830040 * y_val + 1.3299098908 * z_val
    r = x65 * 3.2404542 - y65 * 1.5371385 - z65 * 0.4985314
    g = -x65 * 0.9692660 + y65 * 1.8760108 + z65 * 0.0415560
    blue = x65 * 0.0556434 - y65 * 0.2040259 + z65 * 1.0572252

    def gamma(value: np.ndarray) -> np.ndarray:
        return np.where(value <= 0.0031308, 12.92 * value, 1.055 * np.maximum(0.0, value) ** (1.0 / 2.4) - 0.055) * 255.0

    return gamma(r), gamma(g), gamma(blue)


def _oklab_to_rgb(lightness: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g = -1.2684380042 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    blue = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3

    def gamma(value: np.ndarray) -> np.ndarray:
        return np.where(value <= 0.0031308, 12.92 * value, 1.055 * np.maximum(0.0, value) ** (1.0 / 2.4) - 0.055) * 255.0

    return gamma(r), gamma(g), gamma(blue)


def render_lab_plane(request: LabPrewarmRequest) -> LabPrewarmResult:
    ratio = max(1.0, request.pixel_ratio)
    image_size = max(1, int(round(request.size * ratio)))
    x = np.linspace(request.min_a, request.max_a, image_size, dtype=np.float32)
    y = np.linspace(request.max_b, request.min_b, image_size, dtype=np.float32)
    aa, bb = np.meshgrid(x, y)
    if request.render_mode == "oklab":
        red, green, blue = _oklab_to_rgb(np.full_like(aa, request.lightness / 100.0), aa, bb)
    else:
        red, green, blue = _lab_to_rgb(np.full_like(aa, request.lightness), aa, bb)
    mask = (red >= 0.0) & (red <= 255.0) & (green >= 0.0) & (green <= 255.0) & (blue >= 0.0) & (blue <= 255.0)
    return LabPrewarmResult(
        request=request,
        image_width=image_size,
        image_height=image_size,
        image_bytes=_rgba_bytes(red, green, blue, mask),
    )
