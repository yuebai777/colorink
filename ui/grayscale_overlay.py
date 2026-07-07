"""
Fullscreen OKLCh perceptual grayscale overlay.

Uses dxcam (DXGI Desktop Duplication API) for GPU-accelerated screen
capture (~6 ms for 4K), then processes each frame through an OpenGL
fragment shader.

The OKLCh pipeline is precomputed into a 256³ 3D LUT (texture3D) —
the shader does a single texture lookup instead of ~30 ALU ops.
GPU time <0.1 ms per frame.

Requires: dxcam, opencv-python-headless, numpy, PyQt6
"""
import ctypes
import array
import time
import numpy as np
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QScreen, QSurfaceFormat
from PyQt6.QtOpenGL import (QOpenGLShader, QOpenGLShaderProgram,
                            QOpenGLBuffer, QOpenGLTexture,
                            QOpenGLFunctions_2_0,
                            QOpenGLVertexArrayObject)


# ---------------------------------------------------------------------------
# Win32: exclude window from DXGI screen capture
# ---------------------------------------------------------------------------
WDA_EXCLUDEFROMCAPTURE = 0x00000011

def _exclude_from_capture(hwnd: int):
    """Mark a window to be skipped by DXGI Desktop Duplication API."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE),
        )
    except Exception as e:
        print(f"[GrayscaleOverlay] SetWindowDisplayAffinity failed: {e}")


# ---------------------------------------------------------------------------
# 3D LUT — precomputed OKLCh grayscale (256³, 16 MB)
# ---------------------------------------------------------------------------

def _build_oklch_lut():
    """Return a flat uint8 array [256*256*256] mapping sRGB→OKLCh gray.
    Caches result to disk — first run ~3s, subsequent runs <100ms."""
    import numpy as np
    import os
    cache_path = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "oklch_lut.npy")
    if os.path.exists(cache_path):
        print("[GrayscaleOverlay] Loading cached LUT...")
        return np.load(cache_path).ravel()

    print("[GrayscaleOverlay] Building OKLCh 3D LUT (one-time, ~3s)...")
    t0 = time.perf_counter()
    lut = np.empty(256 * 256 * 256, dtype=np.uint8)
    BATCH = 16
    for ri in range(0, 256, BATCH):
        re = min(ri + BATCH, 256)
        batch_size = (re - ri) * 256 * 256
        rng = np.arange(ri, re, dtype=np.float64)[:, None, None]
        gng = np.arange(256, dtype=np.float64)[None, :, None]
        bng = np.arange(256, dtype=np.float64)[None, None, :]
        r = (rng + gng * 0 + bng * 0) / 255.0
        g = (rng * 0 + gng + bng * 0) / 255.0
        b = (rng * 0 + gng * 0 + bng) / 255.0

        # sRGB decode
        lo = r < 0.04045
        r_lin = np.where(lo, r / 12.92, ((r + 0.055) / 1.055) ** 2.4)
        g_lin = np.where(lo, g / 12.92, ((g + 0.055) / 1.055) ** 2.4)
        b_lin = np.where(lo, b / 12.92, ((b + 0.055) / 1.055) ** 2.4)

        # M1: linear → LMS
        l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
        m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
        s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

        # cbrt (preserving sign for negative guard)
        l = np.cbrt(np.maximum(l, 0))
        m = np.cbrt(np.maximum(m, 0))
        s = np.cbrt(np.maximum(s, 0))

        # M2: extract perceptual L
        L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s

        # L³ → sRGB encode
        gray_lin = np.clip(L * L * L, 0.0, 1.0)
        lo2 = gray_lin <= 0.0031308
        gray = np.where(lo2, 12.92 * gray_lin, 1.055 * gray_lin ** (1.0 / 2.4) - 0.055)
        gray = np.clip(gray, 0.0, 1.0)

        idx = ri * 256 * 256
        lut[idx:idx + batch_size] = (gray * 255.0 + 0.5).astype(np.uint8).ravel()
    t1 = time.perf_counter()
    np.save(cache_path, lut.reshape(256, 256, 256))
    print(f"[GrayscaleOverlay] LUT built in {(t1-t0)*1000:.0f} ms, cached to {cache_path}")
    return lut

# Build at import time — single 16 MB allocation, reused by all overlays
_OKLCH_LUT = _build_oklch_lut()


# ---------------------------------------------------------------------------
# Shaders — OKLCh via 3D LUT lookup, Luma via simple dot product
# ---------------------------------------------------------------------------

_VERTEX_SHADER = """
#version 130
attribute vec2 aPos;
attribute vec2 aTexCoord;
varying vec2 vTexCoord;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
    vTexCoord = aTexCoord;
}
"""

# OKLCh shader — single texture3D lookup (GPU <0.1 ms)
_OKLCH_FRAGMENT_SHADER = """
#version 130
varying vec2 vTexCoord;
uniform sampler2D uScreen;
uniform sampler3D uLUT;
void main() {
    vec3 col = texture2D(uScreen, vTexCoord).rgb;
    float gray = texture(uLUT, col).r;
    gl_FragColor = vec4(gray, gray, gray, 1.0);
}
"""

# ALU-based OKLCh shader (fallback for debugging)
_OKLCH_ALU_FRAGMENT = """
#version 130
varying vec2 vTexCoord;
uniform sampler2D uScreen;

vec3 srgbToLinear(vec3 c) {
    vec3 lo = c / 12.92;
    vec3 hi = pow((c + 0.055) / 1.055, vec3(2.4));
    return mix(lo, hi, step(0.04045, c));
}
float linearToSrgb(float c) {
    if (c <= 0.0031308) return 12.92 * c;
    return 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}
void main() {
    vec3 col = texture2D(uScreen, vTexCoord).rgb;
    vec3 lin = srgbToLinear(col);
    float l = 0.4122214708 * lin.r + 0.5363325363 * lin.g + 0.0514459929 * lin.b;
    float m = 0.2119034982 * lin.r + 0.6806995451 * lin.g + 0.1073969566 * lin.b;
    float s = 0.0883024619 * lin.r + 0.2817188376 * lin.g + 0.6299787005 * lin.b;
    l = sign(l) * pow(abs(l), 1.0/3.0);
    m = sign(m) * pow(abs(m), 1.0/3.0);
    s = sign(s) * pow(abs(s), 1.0/3.0);
    float L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
    float linGray = clamp(L*L*L, 0.0, 1.0);
    float gray = linearToSrgb(linGray);
    gl_FragColor = vec4(gray, gray, gray, 1.0);
}
"""

_LUMA_FRAGMENT_SHADER = """
#version 130
varying vec2 vTexCoord;
uniform sampler2D uScreen;
void main() {
    vec3 col = texture2D(uScreen, vTexCoord).rgb;
    float gray = 0.2126 * col.r + 0.7152 * col.g + 0.0722 * col.b;
    gl_FragColor = vec4(gray, gray, gray, 1.0);
}
"""


# ---------------------------------------------------------------------------
# Single-screen GPU overlay (dxcam capture + OpenGL OKLCh shader)
# ---------------------------------------------------------------------------

class _ShaderOverlay(QOpenGLWidget):
    """Fullscreen overlay for one screen.  dxcam captures the desktop,
    an OpenGL fragment shader applies OKLCh grayscale, and the result
    is displayed on a frameless topmost overlay."""

    def __init__(self, screen: QScreen, screen_index: int, mode: str = "oklch"):
        # Request an OpenGL surface
        fmt = QSurfaceFormat()
        fmt.setSwapInterval(0)
        QSurfaceFormat.setDefaultFormat(fmt)
        super().__init__()

        self._screen = screen
        self._screen_index = screen_index
        self._mode = mode
        self._camera = None          # dxcam.DXCamera
        self._pending_frame = None   # numpy BGR (H, W, 3) uint8
        self._texture: QOpenGLTexture | None = None
        self._texture_w = 0
        self._texture_h = 0
        self._lut_texture: QOpenGLTexture | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._program: QOpenGLShaderProgram | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._gl: QOpenGLFunctions_2_0 | None = None
        self._initialized = False

        # Frameless, topmost, transparent to mouse/keyboard input
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        geo = screen.geometry()
        self.setGeometry(geo)
        dpr = screen.devicePixelRatio()
        print(f"[GrayscaleOverlay] Created overlay: screen {screen_index} "
              f"({screen.name()}) "
              f"{geo.width()}x{geo.height()} DPR={dpr:.1f}")

    def _init_camera(self):
        """Start dxcam background capture thread for this screen."""
        if self._camera is not None:
            return
        try:
            import dxcam
            self._camera = dxcam.create(
                output_idx=self._screen_index,
                output_color='BGR',
                max_buffer_len=2,
            )
            # Background thread captures continuously — target_fps=0 means
            # uncapped (capture as fast as GPU allows).  The main thread
            # throttles naturally via vsync (frameSwapped).
            self._camera.start(target_fps=0, video_mode=True)
            print(f"[GrayscaleOverlay] dxcam started: screen {self._screen_index}"
                  f" ({self._screen.name()})")
        except Exception as e:
            print(f"[GrayscaleOverlay] dxcam init failed: {e}")
            self._camera = None

    # -- OpenGL lifecycle -----------------------------------------------

    def initializeGL(self):
        self._gl = QOpenGLFunctions_2_0()
        if not self._gl.initializeOpenGLFunctions():
            print("[GrayscaleOverlay] FATAL: Cannot init OpenGL 2.0")
            return

        self._program = QOpenGLShaderProgram(self.context())
        if not self._program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER):
            print(f"[GrayscaleOverlay] Vertex shader error:\n{self._program.log()}")
            return
        if not self._program.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                _LUMA_FRAGMENT_SHADER if self._mode == "luma"
                else _OKLCH_ALU_FRAGMENT):
            print(f"[GrayscaleOverlay] Fragment shader error:\n{self._program.log()}")
            return
        if not self._program.link():
            print(f"[GrayscaleOverlay] Shader link error:\n{self._program.log()}")
            return
        self._program.bind()

        # VAO
        self._vao = QOpenGLVertexArrayObject()
        if self._vao.create():
            self._vao.bind()

        # Fullscreen quad:  [x, y, u, v] x 4 vertices
        data = array.array('f', [
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
             1.0,  1.0,  1.0, 0.0,
            -1.0,  1.0,  0.0, 0.0,
        ])
        self._vbo = QOpenGLBuffer()
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data.tobytes(), len(data) * 4)

        pos_loc = self._program.attributeLocation("aPos")
        tex_loc = self._program.attributeLocation("aTexCoord")
        self._program.enableAttributeArray(pos_loc)
        self._program.enableAttributeArray(tex_loc)
        self._program.setAttributeBuffer(pos_loc, 0x1406, 0, 2, 16)  # GL_FLOAT
        self._program.setAttributeBuffer(tex_loc, 0x1406, 8, 2, 16)

        # Exclude this window from dxcam capture -> no feedback loop
        _exclude_from_capture(int(self.winId()))

        self._init_camera()
        self._initialized = True

        # Vsync-locked render loop: frameSwapped fires after each buffer swap.
        # Combined with background capture, this gives butter-smooth pacing.
        self.frameSwapped.connect(self._on_frame_swapped)
        self._on_frame_swapped()  # kickstart first frame

        print("[GrayscaleOverlay] OpenGL + dxcam initialized")

    def _on_frame_swapped(self):
        """Called after each OpenGL buffer swap — vsync aligned."""
        if not self.isVisible() or self._camera is None:
            return
        try:
            frame = self._camera.get_latest_frame()
            if frame is not None:
                self._pending_frame = frame
            # Always schedule a repaint — even if no new frame,
            # re-render cached texture to keep the swap loop alive.
            self.update()
        except Exception as e:
            print(f"[GrayscaleOverlay] Capture error: {e}")

    def paintGL(self):
        if (not self._initialized or self._program is None
                or self._gl is None):
            return

        # Upload new frame as texture
        if self._pending_frame is not None:
            frame = self._pending_frame
            self._pending_frame = None
            try:
                h, w = frame.shape[:2]  # dxcam: (H, W, 3) BGR
                # Recreate texture when size changes (DPI switch)
                if (self._texture is None or w != self._texture_w
                        or h != self._texture_h):
                    if self._texture is not None:
                        self._texture.destroy()
                    self._texture = QOpenGLTexture(
                        QOpenGLTexture.Target.Target2D)
                    self._texture.setFormat(
                        QOpenGLTexture.TextureFormat.RGB8_UNorm)
                    self._texture.setSize(w, h)
                    self._texture.allocateStorage()
                    self._texture_w = w
                    self._texture_h = h
                # Upload BGR pixel data to mip level 0.
                # PyQt6 setData accepts numpy arrays directly (buffer protocol)
                # — no tobytes() copy needed.
                self._texture.setData(
                    0,
                    QOpenGLTexture.PixelFormat.BGR,
                    QOpenGLTexture.PixelType.UInt8,
                    frame,  # numpy (H, W, 3) uint8 — zero-copy
                )
                self._texture.setMinificationFilter(
                    QOpenGLTexture.Filter.Linear)
                self._texture.setMagnificationFilter(
                    QOpenGLTexture.Filter.Linear)
            except Exception as e:
                print(f"[GrayscaleOverlay] Texture upload error: {e}")

        if self._texture is None:
            return

        self._program.bind()
        self._texture.bind()
        self._program.setUniformValue("uScreen", 0)

        # Draw fullscreen quad
        self._gl.glDrawArrays(0x0006, 0, 4)  # GL_TRIANGLE_FAN

    def resizeGL(self, w: int, h: int):
        if self._gl is not None:
            self._gl.glViewport(0, 0, w, h)

    def cleanup(self):
        """Stop background capture, disconnect signals, release resources."""
        try:
            self.frameSwapped.disconnect(self._on_frame_swapped)
        except Exception:
            pass
        if self._camera is not None:
            try:
                self._camera.stop()
                del self._camera
            except Exception:
                pass
            self._camera = None


# ---------------------------------------------------------------------------
# Main overlay manager (same public API as before)
# ---------------------------------------------------------------------------

class GrayscaleOverlay:
    """Toggleable fullscreen OKLCh grayscale filter.

    Uses dxcam for GPU capture + OpenGL for OKLCh shader processing.
    Driven by an 8 ms QTimer (~120 fps max); background dxcam capture
    keeps the main thread responsive.  When dxcam returns no new frame
    the overlay simply keeps rendering the cached texture.

    Usage (unchanged):
        overlay = GrayscaleOverlay()
        overlay.set_target("all")
        overlay.toggle()           # Ctrl+G
        overlay.set_active(False)  # force off
    """

    def __init__(self, mode: str = "oklch"):
        self._active = False
        self._target = "all"
        self._mode = mode
        self._overlays: list[_ShaderOverlay] = []

    # -- Screen enumeration ---------------------------------------------

    @staticmethod
    def available_screens() -> list[str]:
        app = QApplication.instance()
        if not app:
            return ["all"]
        result = ["all"]
        for i, screen in enumerate(app.screens()):
            geo = screen.geometry()
            dpr = screen.devicePixelRatio()
            name = screen.name().replace("\\\\.\\", "")
            # Show physical pixels, not logical (important for HiDPI)
            pw = int(geo.width() * dpr)
            ph = int(geo.height() * dpr)
            result.append(f"{i}: {name} ({pw}x{ph})")
        return result

    # -- Target selection -----------------------------------------------

    def set_target(self, target: str):
        if target != "all" and ":" in target:
            target = target.split(":")[0].strip()
        if target == self._target:
            return
        was_active = self._active
        if was_active:
            self.set_active(False)
        self._target = target
        if was_active:
            QTimer.singleShot(0, lambda: self.set_active(True))

    @property
    def target(self) -> str:
        return self._target

    # -- Activate / deactivate ------------------------------------------

    def set_active(self, active: bool):
        if active == self._active:
            return
        print(f"[GrayscaleOverlay] set_active: {active}")
        self._active = active
        if active:
            self._create_overlays()
        else:
            self._destroy_overlays()

    def toggle(self):
        self.set_active(not self._active)

    def set_mode(self, mode: str):
        """Switch between 'oklch' and 'luma' grayscale."""
        if mode == self._mode:
            return
        was_active = self._active
        if was_active:
            self.set_active(False)
        self._mode = mode
        if was_active:
            QTimer.singleShot(0, lambda: self.set_active(True))

    @property
    def is_active(self) -> bool:
        return self._active

    # -- Internal -------------------------------------------------------

    def _get_target_screens(self) -> list[tuple[int, QScreen]]:
        app = QApplication.instance()
        if not app:
            return []
        screens = app.screens()
        if not screens:
            return []
        if self._target == "all":
            return list(enumerate(screens))
        try:
            idx = int(self._target)
            if 0 <= idx < len(screens):
                return [(idx, screens[idx])]
        except (ValueError, IndexError):
            pass
        for i, s in enumerate(screens):
            if s.name() == self._target or self._target in s.name():
                return [(i, s)]
        return [(0, screens[0])]

    def _create_overlays(self):
        self._destroy_overlays()
        for idx, screen in self._get_target_screens():
            ov = _ShaderOverlay(screen, idx, self._mode)
            ov.show()
            ov.raise_()
            self._overlays.append(ov)

    def _destroy_overlays(self):
        for ov in self._overlays:
            ov.cleanup()
            ov.hide()
            ov.deleteLater()
        self._overlays.clear()
        QApplication.processEvents()
