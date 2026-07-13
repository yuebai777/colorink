"""Global color picker — C DLL hook swallows clicks, zero GIL cost.

WH_MOUSE_LL hook runs entirely in native C (picker_hook.dll), setting
atomic flags that Python polls at 60 fps.  No Python callback in the
hook thread → no GIL contention → smooth + click interception.
"""

import ctypes
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QCursor, QImage
import win32api, win32con
import os

_ZOOM=6; _RADIUS=7; _PREVIEW=32; _PAD=6; _BR=8
# Fixed magnifier display area in px (at default zoom × radius)
_GRID_PX = (2*_RADIUS+1) * _ZOOM  # 90

def _read_zoom():
    try: from core import config; return config.load_hotkey_config().get("pickerZoom",6)
    except: return 6

def _nearest_odd(v):
    v = max(3, int(v)); return v if v%2 else v+1

# Load native mouse hook DLL
_hook_dll = None
try:
    _hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "core", "picker_hook.dll")
    _hook_dll = ctypes.CDLL(_hook_path)
    _hook_dll.install.restype = ctypes.c_int
    _hook_dll.left_clicked.restype = ctypes.c_int
    _hook_dll.right_clicked.restype = ctypes.c_int
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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool|Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
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
    colorPicked=pyqtSignal(int,int,int)
    def __init__(self,parent=None):
        super().__init__(parent)
        self._active=False; self._center_color=(128,128,128); self._pixel_grid=None
        self._cursor_pos=QPoint(0,0)
        self._dot = CursorDot()
        self._zoom=_ZOOM; self._cap_size=2*_RADIUS+1; self._radius=_RADIUS
        self._cursor_hidden=False  # SetSystemCursor replaced OCR_NORMAL/OCR_CROSS with a blank cursor
        self._shots=[]  # list of (QScreen, QImage, QRect geometry, float dpr) snapshots captured at start()
        self._panel_w=_GRID_PX+_PAD*2; self._panel_h=_PAD+_GRID_PX+_PAD+_PREVIEW+_PAD+10+11+_PAD
        self.setFixedSize(self._panel_w,self._panel_h)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        # Qt's own cursor is made transparent here — see _hide_cursor() for the
        # system-wide replacement that hides the cursor on the rest of desktop too
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer=QTimer(self); self._timer.setInterval(16); self._timer.timeout.connect(self._tick)

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
                pix=sc.grabWindow(0)
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
        except Exception:
            pass

    @property
    def is_active(self): return self._active
    def start(self):
        self._active=True
        # Recalc source region from fixed display size + zoom
        self._zoom = _read_zoom()
        self._cap_size = _nearest_odd(_GRID_PX / self._zoom)
        self._radius = (self._cap_size - 1) // 2
        # Recalc panel to exactly fit the grid (avoids asymmetric margins)
        grid_disp = self._cap_size * self._zoom
        self._panel_w = grid_disp + _PAD * 2
        self._panel_h = _PAD + grid_disp + _PAD + _PREVIEW + _PAD + 10 + 11 + _PAD
        self.setFixedSize(self._panel_w, self._panel_h)
        # Snapshot ALL screens BEFORE showing the picker panel / cross-hair dot
        # so neither appears in the captured pixels.  _tick() will sample from
        # these still images instead of the live desktop.
        self._capture_all_screens()
        _hook_dll.install() if _hook_dll else None
        self._hide_cursor();          # hide the system cursor — leave only the custom cross-hair dot
        self._dot.show(); self._dot.raise_()
        self.show(); self.raise_()
        self._timer.start(); self._tick()
    def stop(self):
        self._active=False; self._timer.stop()
        _hook_dll.uninstall() if _hook_dll else None
        self._show_cursor();          # restore the system cursor we hid in start()
        self._dot.hide()
        self.hide()
        self._shots=[]  # free the snapshots
    def closeEvent(self,ev):
        self._dot.close()
        self._show_cursor();          # ensure the cursor never gets stuck hidden if the widget is closed mid-pick
        if _hook_dll: _hook_dll.uninstall()
        super().closeEvent(ev)

    def _tick(self):
        if not self._active: return
        if _hook_dll and _hook_dll.left_clicked():
            r,g,b=self._center_color; self.stop(); self.colorPicked.emit(r,g,b); return
        if _hook_dll and _hook_dll.right_clicked():
            self.stop(); return
        if win32api.GetAsyncKeyState(win32con.VK_ESCAPE)&0x8000:
            self.stop(); return
        try:
            self._cursor_pos=QCursor.pos(); x,y=self._cursor_pos.x(),self._cursor_pos.y()
            sc=QApplication.screenAt(QPoint(x,y))
            if sc is None: return
            # Find the snapshot taken at start() for the screen the cursor is on.
            shot=None
            for s, img, geo, dpr in self._shots:
                if s is sc or geo.contains(QPoint(x,y)):
                    shot=(img, geo, dpr); break
            if shot is None: return
            img, geo, dpr = shot
            if dpr<0.1: dpr=1.0
            iw=img.width(); ih=img.height()
            # Cursor position in the snapshot's physical pixel space
            lx=int((x-geo.x())*dpr); ly=int((y-geo.y())*dpr)
            half=self._radius
            grid=[]
            for dy in range(-half, half+1):
                py=ly+dy
                row=[]
                for dx in range(-half, half+1):
                    px=lx+dx
                    if px<0 or px>=iw or py<0 or py>=ih:
                        row.append((0,0,0))
                    else:
                        rgb=img.pixel(px,py)  # QRgb = 0xffRRGGBB for RGB32
                        row.append(((rgb>>16)&0xFF, (rgb>>8)&0xFF, rgb&0xFF))
                grid.append(row)
            self._pixel_grid=grid; self._center_color=grid[half][half]
            self.move(x+8,y+8); self._dot.follow(x,y); self.update()
        except Exception: pass

    def paintEvent(self,ev):
        if self._pixel_grid is None: return
        p=QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing,True); w,h=self._panel_w,self._panel_h
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(QColor(40,40,40,240))); p.drawRoundedRect(0,0,w,h,_BR,_BR)
            p.setPen(QPen(QColor(90,90,90,60),1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRoundedRect(0,0,w-1,h-1,_BR,_BR)
            cell=self._zoom; gd=self._cap_size*self._zoom
            mr=QRect(_PAD,_PAD,gd,gd); p.setPen(Qt.PenStyle.NoPen)
            for ri,row in enumerate(self._pixel_grid):
                for ci,(r_,g_,b_) in enumerate(row):
                    p.setBrush(QBrush(QColor(r_,g_,b_))); p.drawRect(mr.x()+ci*cell,mr.y()+ri*cell,cell,cell)
            cx_=mr.x()+self._radius*cell+cell//2; cy_=mr.y()+self._radius*cell+cell//2; cl=6
            for co,pw in [(QColor(0,0,0,140),3),(QColor(255,255,255,220),1)]:
                p.setPen(QPen(co,pw)); p.drawLine(cx_,cy_-cl,cx_,cy_+cl); p.drawLine(cx_-cl,cy_,cx_+cl,cy_)
            r_,g_,b_=self._center_color; pry=_PAD+mr.height()+_PAD
            pr=QRect(_PAD,pry,_PREVIEW,_PREVIEW); p.setPen(QPen(QColor(80,80,80),1))
            p.setBrush(QBrush(QColor(r_,g_,b_))); p.drawEllipse(pr)
            f=p.font(); f.setPointSize(6); p.setFont(f); tx,ty=_PAD,pry+_PREVIEW+4
            p.setPen(QPen(QColor(220,220,220))); p.drawText(tx,ty+8,f"#{r_:02X}{g_:02X}{b_:02X}")
            p.setPen(QPen(QColor(160,160,160))); p.drawText(tx,ty+18,f"{r_}, {g_}, {b_}")
        finally: p.end()

    def mousePressEvent(self,ev): ev.accept()
    def keyPressEvent(self,ev):
        if ev.key()==Qt.Key.Key_Escape: self.stop()
        ev.accept()
