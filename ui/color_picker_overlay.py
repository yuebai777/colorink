"""Global color picker — C DLL hook swallows clicks, zero GIL cost.

WH_MOUSE_LL hook runs entirely in native C (picker_hook.dll), setting
atomic flags that Python polls at 60 fps.  No Python callback in the
hook thread → no GIL contention → smooth + click interception.
"""

import ctypes
import os
from typing import Any, cast

import win32api
import win32con
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

_ZOOM=6; _RADIUS=7; _PREVIEW=32; _PAD=6; _BR=8
# Fixed magnifier display area in px (at default zoom × radius)
_GRID_PX = (2*_RADIUS+1) * _ZOOM  # 90

def _nearest_odd(v):
    v = max(3, int(v)); return v if v%2 else v+1


def _zoom_geometry(zoom):
    """Resolve the magnifier geometry for *zoom*.

    Returns ``(cap_size, radius, grid_disp, panel_w, panel_h)``. The zoom
    level is injected by the owner (MainWindow) rather than re-read from the
    settings file on every pick; see :meth:`ColorPickerOverlay.set_zoom`.
    """
    zoom = max(1, int(zoom))
    cap_size = _nearest_odd(_GRID_PX / zoom)
    radius = (cap_size - 1) // 2
    grid_disp = cap_size * zoom
    panel_w = grid_disp + _PAD * 2
    panel_h = _PAD + grid_disp + _PAD + _PREVIEW + _PAD + 10 + 11 + _PAD
    return cap_size, radius, grid_disp, panel_w, panel_h

# Load native mouse hook DLL
_hook_dll = None
try:
    _hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "core", "picker_hook.dll")
    _hook_dll = ctypes.CDLL(_hook_path)
    _hook_dll.install.restype = ctypes.c_int
    _hook_dll.left_clicked.restype = ctypes.c_int
    _hook_dll.right_clicked.restype = ctypes.c_int
    _hook_dll.get_wheel_delta.restype = ctypes.c_int
except Exception:
    pass  # DLL missing — clicks won't be intercepted but picker still works


class CursorDot(QWidget):
    """Tiny grey crosshair pinned to the cursor — visible even when
    drawing software overrides the cursor with a brush style.

    The widget is intentionally larger than the cross itself: the wider pen
    (3px) is centred on its endpoints, so a line that starts/ends at the
    widget edge spills ~half a pen-width outside.  At DPR=1 that half pixel
    is invisibly clipped; at DPR=1.5/2.0 it is scaled up and the cross looks
    "cut off" — exactly the symptom seen on the higher-DPI main screen.  A
    margin around the cross lets the pen overflow safely inside the widget on
    every screen.
    """
    def __init__(self):
        super().__init__(None)
        self.setFixedSize(16,16)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool|Qt.WindowType.WindowTransparentForInput|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    def _apply_noactivate(self):
        """Ensure the crosshair dot never activates/steals foreground focus."""
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if not (ex_style & win32con.WS_EX_NOACTIVATE):
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_NOACTIVATE)
                win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                                      | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED)
        except Exception:
            pass
    def paintEvent(self,ev):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing,False)
        c=8; L=5  # centre & half length — cross spans c-L..c+L inside 16px widget
        p.setPen(QPen(QColor(0,0,0,100),3))
        p.drawLine(c,c-L,c,c+L); p.drawLine(c-L,c,c+L,c)
        p.setPen(QPen(QColor(200,200,200,180),1))
        p.drawLine(c,c-L,c,c+L); p.drawLine(c-L,c,c+L,c)
        p.end()
    def follow(self,x,y): self.move(x-8,y-8)


class ColorPickerOverlay(QWidget):
    colorPicked = pyqtSignal(int, int, int)
    zoomChanged = pyqtSignal(int)
    # Emitted when the picker becomes active / inactive so the owner can
    # pause background work (e.g. the sync thread) — its GIL traffic and
    # UI-repaint signals make the picker's 16 ms tick drop frames while
    # the user sweeps the mouse, which shifts the picked position.
    activated = pyqtSignal()
    deactivated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._center_color = (128, 128, 128)
        self._pixel_grid = None
        self._cursor_pos = QPoint(0, 0)
        self._dot = CursorDot()
        self._zoom = _ZOOM
        self._cap_size = 2 * _RADIUS + 1
        self._radius = _RADIUS
        self._cursor_hidden = False  # SetSystemCursor replaced OCR_NORMAL/OCR_CROSS with a blank cursor
        self._shots = []  # list of (QScreen, QImage, QRect geometry, float dpr) snapshots captured at start()
        self._frozen = False
        self._freeze_pos = None
        self._shift_origin = None
        self._shift_axis = None
        self._hide_reticle = False
        self._active_sample_pos = None
        self._panel_w = _GRID_PX + _PAD * 2
        self._panel_h = _PAD + _GRID_PX + _PAD + _PREVIEW + _PAD + 10 + 11 + _PAD
        self.setFixedSize(self._panel_w, self._panel_h)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Qt's own cursor is made transparent here — see _hide_cursor() for the
        # system-wide replacement that hides the cursor on the rest of desktop too
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _capture_all_screens(self):
        """Snapshot every screen once, before any picker UI is shown.

        Capture happens at start() time, so the magnifier panel and the
        cross-hair dot are NOT in the snapshot.  _tick() then samples pixels
        from these still images instead of the live desktop, which means:
          * the picker panel's own window never appears in the preview
          * other overlay windows / small floating UI don't bleed in either
          * replacing / hiding the cursor (or any other visual overlay) is
            safe — it cannot corrupt the sampled pixels.
        """
        shots=[]
        for sc in QApplication.screens():
            geo=sc.geometry()
            dpr=sc.devicePixelRatio()
            if dpr<0.1: dpr=1.0
            try:
                pix=sc.grabWindow(cast(Any, 0))
                if pix is None or pix.isNull(): continue
                img=pix.toImage().convertToFormat(QImage.Format.Format_RGB32)
                if img.isNull(): continue
                shots.append((sc, img, QRect(geo), float(dpr)))
            except Exception:
                continue
        self._shots=shots

    def _hide_cursor(self):
        """Hide the system cursor globally for the duration of the pick.

        ShowCursor(False) alone is unreliable in a Qt app: it is thread-local
        refcounted, and Qt's own hover/cursor-switch logic routinely balances
        it back to visible, so the cursor reappears as soon as the mouse moves
        over our widget.  Screen-capture tools (Snipaste, QQ Screenshot, …)
        use a different approach: build a 1×1 fully-transparent cursor and
        substitute it into the system cursor table via SetSystemCursor.

        We replace OCR_NORMAL (32512, the default arrow — used whenever the
        mouse is outside our window) and OCR_CROSS (32515, the cross — what Qt
        was previously showing on our widget) so the cursor is invisible
        everywhere on the desktop while picking.  _show_cursor() restores the
        defaults via SystemParametersInfo(SPI_SETCURSORS), the canonical way to
        reset the whole system cursor table.
        """
        if self._cursor_hidden:
            return
        # 1×1 transparent cursor: AND mask all 0xFF (transparent), XOR mask all 0.
        # 1-bpp masks are padded to a 4-byte row boundary → 4 bytes per plane.
        andmask = (ctypes.c_ubyte * 4)(0xFF, 0xFF, 0xFF, 0xFF)
        xormask = (ctypes.c_ubyte * 4)(0x00, 0x00, 0x00, 0x00)
        blank = ctypes.windll.user32.CreateCursor(None, 0, 0, 1, 1, andmask, xormask)
        if not blank:
            return
        # OCR_NORMAL=32512, OCR_CROSS=32515 — both covered for in-window & out-window cases
        try:
            ctypes.windll.user32.SetSystemCursor(blank, 32512)
            ctypes.windll.user32.SetSystemCursor(blank, 32515)
            self._cursor_hidden = True
        except Exception:
            pass

    def _show_cursor(self):
        if not self._cursor_hidden:
            return
        self._cursor_hidden = False
        # SPI_SETCURSORS (0x0057) tells user32 to reset EVERY system cursor
        # (arrow, I-beam, cross, hand, …) back to its registry default.  This is
        # the safest restore path — it does not depend on us remembering what
        # was there before we overwrote it.
        try:
            ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 0)
            # SPI_SETCURSORS resets the system cursor table; nudge the cursor
            # so the foreground drawing app re-evaluates and re-applies its
            # brush cursor without requiring an extra click.
            try:
                pos = win32api.GetCursorPos()
                if pos:
                    win32api.SetCursorPos((pos[0] + 1, pos[1]))
                    win32api.SetCursorPos(pos)
            except Exception:
                pass
        except Exception:
            pass

    @property
    def is_active(self):
        return self._active

    def set_zoom(self, zoom):
        """Inject the magnifier zoom level; used on the next ``start()``.

        MainWindow owns the live ``pickerZoom`` setting and pushes it here at
        construction and on settings save, so a pick never re-reads the whole
        settings file just to resolve one key.
        """
        self._zoom = max(1, int(zoom))

    def adjust_zoom(self, step: int) -> int:
        """Dynamically increment or decrement zoom (clamped between 2 and 20)."""
        new_zoom = max(2, min(20, self._zoom + step))
        if new_zoom != self._zoom:
            self.set_zoom(new_zoom)
            (self._cap_size, self._radius, _grid_disp,
             self._panel_w, self._panel_h) = _zoom_geometry(self._zoom)
            self.setFixedSize(self._panel_w, self._panel_h)
            self.zoomChanged.emit(new_zoom)
        return self._zoom

    def _apply_noactivate(self):
        """Ensure the picker overlay never activates/steals foreground focus."""
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if not (ex_style & win32con.WS_EX_NOACTIVATE):
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_NOACTIVATE)
                win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                                      | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED)
        except Exception:
            pass

    def start(self):
        self._active = True
        self._frozen = False
        self._freeze_pos = None
        self._shift_origin = None
        self._shift_axis = None
        self._hide_reticle = False
        self._active_sample_pos = None
        # Recalc source region + panel from the injected zoom level. The
        # panel is recomputed to exactly fit the grid (avoids asymmetric
        # margins) without any config I/O on the hot path.
        (self._cap_size, self._radius, grid_disp,
         self._panel_w, self._panel_h) = _zoom_geometry(self._zoom)
        self.setFixedSize(self._panel_w, self._panel_h)
        # Snapshot ALL screens BEFORE showing the picker panel / cross-hair dot
        # so neither appears in the captured pixels.  _tick() will sample from
        # these still images instead of the live desktop.
        self._capture_all_screens()
        if _hook_dll:
            _hook_dll.install()
        self._hide_cursor()          # hide the system cursor — leave only the custom cross-hair dot
        # Apply no-activate before show() so the native windows are created
        # with WS_EX_NOACTIVATE and Qt cannot activate them while showing;
        # re-apply after show() in case show recreated the native handle.
        self._dot._apply_noactivate()
        self._dot.show()
        self._dot.raise_()
        self._dot._apply_noactivate()
        self._apply_noactivate()
        self.show()
        self.raise_()
        self._apply_noactivate()
        self._timer.start()
        self._tick()
        self.activated.emit()

    def stop(self):
        self._active = False
        self._timer.stop()
        if _hook_dll:
            _hook_dll.uninstall()
        self._show_cursor()          # restore the system cursor we hid in start()
        self._dot.hide()
        self.hide()
        self._shots = []  # free the snapshots
        self._frozen = False
        self._freeze_pos = None
        self._shift_origin = None
        self._shift_axis = None
        self._hide_reticle = False
        self._active_sample_pos = None
        self.deactivated.emit()

    def closeEvent(self, ev):
        self._active = False
        self._timer.stop()
        self._dot.close()
        self._show_cursor()          # ensure the cursor never gets stuck hidden if the widget is closed mid-pick
        if _hook_dll:
            _hook_dll.uninstall()
        self._frozen = False
        self._freeze_pos = None
        self._shift_origin = None
        self._shift_axis = None
        self._hide_reticle = False
        self._active_sample_pos = None
        self.deactivated.emit()       # never leave the sync thread paused
        super().closeEvent(ev)

    def _sample_pos(self, pos: QPoint) -> tuple[int, int, int] | None:
        """Sample RGB from the cached screen snapshots at the given logical position."""
        x, y = pos.x(), pos.y()
        sc = QApplication.screenAt(pos)
        if sc is None:
            return None
        shot = None
        for s, img, geo, dpr in self._shots:
            if s is sc or geo.contains(pos):
                shot = (img, geo, dpr)
                break
        if shot is None:
            return None
        img, geo, dpr = shot
        if dpr < 0.1:
            dpr = 1.0
        iw, ih = img.width(), img.height()
        lx = int((x - geo.x()) * dpr)
        ly = int((y - geo.y()) * dpr)
        if 0 <= lx < iw and 0 <= ly < ih:
            rgb = img.pixel(lx, ly)
            return ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
        return None

    def _tick(self):
        if not self._active:
            return

        # 1. Process mouse wheel delta from native hook
        if _hook_dll:
            try:
                wheel_raw = _hook_dll.get_wheel_delta()
                if wheel_raw != 0:
                    steps = wheel_raw // 120
                    if steps == 0:
                        steps = 1 if wheel_raw > 0 else -1
                    self.adjust_zoom(steps)
            except Exception:
                pass

        # 2. Check Space key: hold to hide reticle and cursor dot
        try:
            space_down = bool(win32api.GetAsyncKeyState(win32con.VK_SPACE) & 0x8000)
        except Exception:
            space_down = False

        if space_down != self._hide_reticle:
            self._hide_reticle = space_down
            if self._hide_reticle:
                self._dot.hide()
            else:
                self._dot.show()

        # 3. Check Alt key: hold to freeze sampling position
        try:
            alt_down = bool(win32api.GetAsyncKeyState(win32con.VK_MENU) & 0x8000)
        except Exception:
            alt_down = False

        raw_cursor = QCursor.pos()
        if alt_down:
            if not self._frozen:
                self._frozen = True
                self._freeze_pos = raw_cursor
        else:
            self._frozen = False
            self._freeze_pos = None

        # 4. Check Shift key: hold to lock sampling axis (X or Y)
        try:
            shift_down = bool(win32api.GetAsyncKeyState(win32con.VK_SHIFT) & 0x8000)
        except Exception:
            shift_down = False

        if shift_down:
            if self._shift_origin is None:
                self._shift_origin = raw_cursor
                self._shift_axis = None
            if self._shift_axis is None:
                dx = abs(raw_cursor.x() - self._shift_origin.x())
                dy = abs(raw_cursor.y() - self._shift_origin.y())
                if dx >= 3 or dy >= 3:
                    self._shift_axis = 'X' if dx >= dy else 'Y'
        else:
            self._shift_origin = None
            self._shift_axis = None

        # 5. Resolve sampling position
        if self._frozen and self._freeze_pos is not None:
            sample_pos = self._freeze_pos
        elif shift_down and self._shift_origin is not None:
            if self._shift_axis == 'X':
                sample_pos = QPoint(raw_cursor.x(), self._shift_origin.y())
            elif self._shift_axis == 'Y':
                sample_pos = QPoint(self._shift_origin.x(), raw_cursor.y())
            else:
                sample_pos = self._shift_origin
        else:
            sample_pos = raw_cursor

        self._active_sample_pos = sample_pos
        self._cursor_pos = raw_cursor

        # 6. Check clicks
        if _hook_dll and _hook_dll.left_clicked():
            exact = self._sample_pos(sample_pos)
            r, g, b = exact if exact is not None else self._center_color
            self.colorPicked.emit(r, g, b)
            self.stop()
            return

        if _hook_dll and _hook_dll.right_clicked():
            self.stop()
            return

        if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8000:
            self.stop()
            return

        # 7. Sample pixels from snapshots
        try:
            x, y = sample_pos.x(), sample_pos.y()
            sc = QApplication.screenAt(QPoint(x, y))
            if sc is None:
                return
            shot = None
            for s, img, geo, dpr in self._shots:
                if s is sc or geo.contains(QPoint(x, y)):
                    shot = (img, geo, dpr)
                    break
            if shot is None:
                return
            img, geo, dpr = shot
            if dpr < 0.1:
                dpr = 1.0
            iw = img.width()
            ih = img.height()
            lx = int((x - geo.x()) * dpr)
            ly = int((y - geo.y()) * dpr)
            half = self._radius
            grid = []
            for dy in range(-half, half + 1):
                py = ly + dy
                row = []
                for dx in range(-half, half + 1):
                    px = lx + dx
                    if px < 0 or px >= iw or py < 0 or py >= ih:
                        row.append((0, 0, 0))
                    else:
                        rgb = img.pixel(px, py)  # QRgb = 0xffRRGGBB for RGB32
                        row.append(((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF))
                grid.append(row)
            self._pixel_grid = grid
            self._center_color = grid[half][half]

            self.move(raw_cursor.x() + 8, raw_cursor.y() + 8)
            if not self._hide_reticle:
                self._dot.follow(x, y)
            self.update()
        except Exception:
            pass

    def paintEvent(self, ev):
        if self._pixel_grid is None:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self._panel_w, self._panel_h
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(40, 40, 40, 240)))
            p.drawRoundedRect(0, 0, w, h, _BR, _BR)
            p.setPen(QPen(QColor(90, 90, 90, 60), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(0, 0, w - 1, h - 1, _BR, _BR)

            cell = self._zoom
            gd = self._cap_size * self._zoom
            mr = QRect(_PAD, _PAD, gd, gd)
            p.setPen(Qt.PenStyle.NoPen)
            for ri, row in enumerate(self._pixel_grid):
                for ci, (r_, g_, b_) in enumerate(row):
                    p.setBrush(QBrush(QColor(r_, g_, b_)))
                    p.drawRect(mr.x() + ci * cell, mr.y() + ri * cell, cell, cell)

            # Crosshair (hidden if Space is held)
            if not self._hide_reticle:
                cx_ = mr.x() + self._radius * cell + cell // 2
                cy_ = mr.y() + self._radius * cell + cell // 2
                cl = 6
                for co, pw in [(QColor(0, 0, 0, 140), 3), (QColor(255, 255, 255, 220), 1)]:
                    p.setPen(QPen(co, pw))
                    p.drawLine(cx_, cy_ - cl, cx_, cy_ + cl)
                    p.drawLine(cx_ - cl, cy_, cx_ + cl, cy_)

            r_, g_, b_ = self._center_color
            pry = _PAD + mr.height() + _PAD
            pr = QRect(_PAD, pry, _PREVIEW, _PREVIEW)
            p.setPen(QPen(QColor(80, 80, 80), 1))
            p.setBrush(QBrush(QColor(r_, g_, b_)))
            p.drawEllipse(pr)

            # Zoom text next to preview circle
            f = p.font()
            f.setPointSize(7)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(QColor(180, 180, 180)))
            p.drawText(_PAD + _PREVIEW + 8, pry + 12, f"{self._zoom}×")

            # Interactive state indicator badge
            f_status = p.font()
            f_status.setPointSize(6)
            f_status.setBold(False)
            p.setFont(f_status)
            if self._frozen:
                p.setPen(QPen(QColor(100, 200, 255)))
                p.drawText(_PAD + _PREVIEW + 8, pry + 25, "冻结中")
            elif self._shift_axis == 'X':
                p.setPen(QPen(QColor(130, 220, 130)))
                p.drawText(_PAD + _PREVIEW + 8, pry + 25, "水平锁定")
            elif self._shift_axis == 'Y':
                p.setPen(QPen(QColor(130, 220, 130)))
                p.drawText(_PAD + _PREVIEW + 8, pry + 25, "垂直锁定")
            elif self._hide_reticle:
                p.setPen(QPen(QColor(255, 215, 100)))
                p.drawText(_PAD + _PREVIEW + 8, pry + 25, "隐藏准星")

            # Hex and RGB values
            f_val = p.font()
            f_val.setPointSize(6)
            f_val.setBold(False)
            p.setFont(f_val)
            tx, ty = _PAD, pry + _PREVIEW + 4
            p.setPen(QPen(QColor(220, 220, 220)))
            p.drawText(tx, ty + 8, f"#{r_:02X}{g_:02X}{b_:02X}")
            p.setPen(QPen(QColor(160, 160, 160)))
            p.drawText(tx, ty + 18, f"{r_}, {g_}, {b_}")
        finally:
            p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = getattr(self, "_active_sample_pos", None) or QCursor.pos()
            exact = self._sample_pos(pos)
            r, g, b = exact if exact is not None else self._center_color
            self.colorPicked.emit(r, g, b)
            self.stop()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.stop()
        ev.accept()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta != 0:
            step = 1 if delta > 0 else -1
            self.adjust_zoom(step)
        ev.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.stop()
        ev.accept()
