"""
Fullscreen grayscale overlay using OKLCh perceptual color space.

Uses OpenGL fragment shader for GPU-accelerated conversion — the full
OKLCh pipeline runs on the GPU, processing every pixel simultaneously.
Only the screen capture (unavoidable system call) hits the CPU.

Performance: ~30-50 ms/frame (bottleneck is screen capture, not processing).
"""
import time
import array
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QImage, QScreen, QSurfaceFormat
from PyQt6.QtOpenGL import (QOpenGLTexture, QOpenGLShader,
                             QOpenGLShaderProgram, QOpenGLBuffer,
                             QOpenGLFunctions_2_0,
                             QOpenGLVertexArrayObject)

# ---------------------------------------------------------------------------
# Fullscreen quad vertex + OKLCh grayscale fragment shader
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

_FRAGMENT_SHADER = """
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

    // sRGB -> linear
    vec3 lin = srgbToLinear(col);

    // M1: linear sRGB -> LMS
    float l = 0.4122214708 * lin.r + 0.5363325363 * lin.g + 0.0514459929 * lin.b;
    float m = 0.2119034982 * lin.r + 0.6806995451 * lin.g + 0.1073969566 * lin.b;
    float s = 0.0883024619 * lin.r + 0.2817188376 * lin.g + 0.6299787005 * lin.b;

    // cbrt preserving sign
    l = sign(l) * pow(abs(l), 1.0 / 3.0);
    m = sign(m) * pow(abs(m), 1.0 / 3.0);
    s = sign(s) * pow(abs(s), 1.0 / 3.0);

    // M2: extract perceptual L
    float L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;

    // Reverse: L^3 -> linear -> gamma encode
    float linGray = clamp(L * L * L, 0.0, 1.0);
    float gray = linearToSrgb(linGray);

    gl_FragColor = vec4(gray, gray, gray, 1.0);
}
"""


# ---------------------------------------------------------------------------
# Single-screen GPU overlay
# ---------------------------------------------------------------------------

class _ShaderOverlay(QOpenGLWidget):
    """Frameless fullscreen OpenGL widget for one screen.  GPU-accelerated."""

    def __init__(self, screen: QScreen):
        fmt = QSurfaceFormat()
        fmt.setSwapInterval(0)
        QSurfaceFormat.setDefaultFormat(fmt)

        super().__init__()
        self._screen = screen
        self._texture: QOpenGLTexture | None = None
        self._pending_pixmap: QPixmap | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._program: QOpenGLShaderProgram | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._gl: QOpenGLFunctions_2_0 | None = None
        self._initialized = False

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
        phys_w = int(geo.width() * dpr)
        phys_h = int(geo.height() * dpr)
        print(f"[GrayscaleOverlay] Created GPU overlay: {screen.name()} "
              f"logical={geo.width()}x{geo.height()} "
              f"physical={phys_w}x{phys_h} DPI={dpr:.1f}")

    def refresh(self):
        """Capture screen, schedule GPU upload on next paintGL."""
        if not self.isVisible():
            return
        try:
            self._pending_pixmap = self._screen.grabWindow(0)
            self.update()  # triggers paintGL
        except Exception as e:
            print(f"[GrayscaleOverlay] Refresh error: {e}")

    # -- OpenGL lifecycle --

    def initializeGL(self):
        self._gl = QOpenGLFunctions_2_0()
        if not self._gl.initializeOpenGLFunctions():
            print("[GrayscaleOverlay] FATAL: Cannot initialize OpenGL 2.0 functions")
            return

        self._program = QOpenGLShaderProgram(self.context())
        if not self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER):
            print(f"[GrayscaleOverlay] Vertex shader error:\n{self._program.log()}")
            return
        if not self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SHADER):
            print(f"[GrayscaleOverlay] Fragment shader error:\n{self._program.log()}")
            return
        if not self._program.link():
            print(f"[GrayscaleOverlay] Shader link error:\n{self._program.log()}")
            return
        self._program.bind()

        # VAO (required on OpenGL 3.1+ core profile, safe to skip on 2.x)
        self._vao = QOpenGLVertexArrayObject()
        if self._vao.create():
            self._vao.bind()

        # Fullscreen quad: two triangles covering clip space (-1..1)
        # Format: [x, y, u, v] * 4 vertices
        data = array.array('f', [
            -1.0, -1.0,  0.0, 1.0,   # bottom-left
             1.0, -1.0,  1.0, 1.0,   # bottom-right
             1.0,  1.0,  1.0, 0.0,   # top-right
            -1.0,  1.0,  0.0, 0.0,   # top-left
        ])
        self._vbo = QOpenGLBuffer()
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data.tobytes(), len(data) * 4)

        pos_loc = self._program.attributeLocation("aPos")
        tex_loc = self._program.attributeLocation("aTexCoord")
        self._program.enableAttributeArray(pos_loc)
        self._program.enableAttributeArray(tex_loc)
        self._program.setAttributeBuffer(pos_loc, 5126, 0, 2, 16)
        self._program.setAttributeBuffer(tex_loc, 5126, 8, 2, 16)

        self._initialized = True
        print("[GrayscaleOverlay] OpenGL initialized")

    def paintGL(self):
        if not self._initialized or self._program is None or self._gl is None:
            return

        # Upload new screen capture as texture
        if self._pending_pixmap is not None and not self._pending_pixmap.isNull():
            try:
                img = self._pending_pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                self._pending_pixmap = None

                if self._texture is not None:
                    self._texture.destroy()
                self._texture = QOpenGLTexture(img)
                self._texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
                self._texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
            except Exception as e:
                print(f"[GrayscaleOverlay] Texture upload error: {e}")
                return

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


# ---------------------------------------------------------------------------
# Main overlay manager
# ---------------------------------------------------------------------------

class GrayscaleOverlay:
    """Manages per-screen OpenGL grayscale overlays."""

    def __init__(self):
        self._active = False
        self._target = "all"
        self._refresh_interval = 100  # ms (~10 fps, GPU can handle it)

        self._overlays: list[_ShaderOverlay] = []
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_all)
        self._timer.setInterval(self._refresh_interval)

    @staticmethod
    def available_screens() -> list[str]:
        app = QApplication.instance()
        if not app:
            return ["all"]
        result = ["all"]
        for i, screen in enumerate(app.screens()):
            geo = screen.geometry()
            name = screen.name().replace("\\\\.\\", "")
            result.append(f"{i}: {name} ({geo.width()}x{geo.height()})")
        return result

    def set_target(self, target: str):
        if target != "all" and ":" in target:
            target = target.split(":")[0].strip()
        if target == self._target:
            return
        print(f"[GrayscaleOverlay] set_target: {self._target!r} -> {target!r}")
        was_active = self._active
        if was_active:
            self.set_active(False)
        self._target = target
        if was_active:
            QTimer.singleShot(0, lambda: self.set_active(True))

    @property
    def target(self) -> str:
        return self._target

    def set_active(self, active: bool):
        if active == self._active:
            return
        print(f"[GrayscaleOverlay] set_active: {active}")
        self._active = active
        if active:
            self._create_overlays()
            self._timer.start()
        else:
            self._timer.stop()
            self._destroy_overlays()

    def toggle(self):
        self.set_active(not self._active)

    @property
    def is_active(self) -> bool:
        return self._active

    def _get_target_screens(self) -> list[QScreen]:
        app = QApplication.instance()
        if not app:
            return []
        screens = app.screens()
        if not screens:
            return []
        if self._target == "all":
            return list(screens)
        try:
            idx = int(self._target)
            if 0 <= idx < len(screens):
                return [screens[idx]]
        except (ValueError, IndexError):
            pass
        for s in screens:
            if s.name() == self._target or self._target in s.name():
                return [s]
        return [screens[0]]

    def _create_overlays(self):
        self._destroy_overlays()
        for screen in self._get_target_screens():
            ov = _ShaderOverlay(screen)
            ov.show()
            ov.raise_()
            self._overlays.append(ov)
        self._refresh_all()

    def _destroy_overlays(self):
        for ov in self._overlays:
            ov.hide()
            ov.deleteLater()
        self._overlays.clear()
        QApplication.processEvents()

    def _refresh_all(self):
        for ov in self._overlays:
            ov.refresh()
