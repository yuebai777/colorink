"""Probe: observe exactly which events the tablet pen produces over the app.

Run from the repo root:

    python tools/probe_tablet_hover.py

Hover the pen (and, as a control, the mouse) over the window.  The probe
prints every event Qt delivers (mouse vs tablet), the widget under the
pointer, its cursor shape and whether the native cursor-forcing used by
Colorink (SetCursor) fires for pen hover.

This mirrors the fix in ``MainWindow._sync_tablet_cursor``
(``ui/window/layout.py``): pen hover arrives as QTabletEvent(TabletMove) and
Qt does not re-apply widget cursors on that path, so the app forces the OS
cursor itself.  Use this tool to verify the fix on real hardware and to
report driver behaviour (Windows Ink vs Wintab) if something still
misbehaves.
"""

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QCursor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from ui.window.layout import _OCR_CURSOR_BY_SHAPE  # noqa: E402

_blank_keepalive = None  # (handle, andmask, xormask) — keep the cursor alive


def force_cursor(shape) -> bool:
    """Same native cursor forcing as MainWindow._force_cursor_shape."""
    global _blank_keepalive
    try:
        user32 = ctypes.windll.user32
        user32.SetCursor.restype = ctypes.c_void_p
        user32.SetCursor.argtypes = [ctypes.c_void_p]
        if shape == Qt.CursorShape.BlankCursor:
            if _blank_keepalive is None:
                user32.CreateCursor.restype = ctypes.c_void_p
                andmask = (ctypes.c_ubyte * 4)(0xFF, 0xFF, 0xFF, 0xFF)
                xormask = (ctypes.c_ubyte * 4)(0x00, 0x00, 0x00, 0x00)
                handle = user32.CreateCursor(
                    None, 0, 0, 1, 1,
                    ctypes.cast(andmask, ctypes.c_void_p),
                    ctypes.cast(xormask, ctypes.c_void_p),
                )
                if not handle:
                    return False
                _blank_keepalive = (int(handle), andmask, xormask)
            handle = _blank_keepalive[0]
        else:
            ocr = _OCR_CURSOR_BY_SHAPE.get(shape)
            if ocr is None:
                return False
            user32.LoadCursorW.restype = ctypes.c_void_p
            user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            handle = user32.LoadCursorW(None, ocr)
            if not handle:
                return False
        user32.SetCursor(handle)
        return True
    except Exception:
        return False


class ProbeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Colorink pen-hover probe")
        self.resize(520, 480)
        self._events = []

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Hover the PEN and the MOUSE over the areas below.\n"
            "Cross = color wheel · Hand = buttons · plain = sliders."
        )
        lay.addWidget(hint)

        cross = QLabel("CROSS  (color wheel)")
        cross.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cross.setCursor(Qt.CursorShape.CrossCursor)
        cross.setStyleSheet("background:#d96; min-height:90px;")
        lay.addWidget(cross)

        hand = QLabel("HAND  (buttons)")
        hand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hand.setCursor(Qt.CursorShape.PointingHandCursor)
        hand.setStyleSheet("background:#69d; min-height:60px;")
        lay.addWidget(hand)

        plain = QLabel("PLAIN  (sliders)")
        plain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plain.setStyleSheet("background:#6a6; min-height:60px;")
        lay.addWidget(plain)

        self.log = QLabel("—")
        self.log.setWordWrap(True)
        self.log.setStyleSheet(
            "font-family:Consolas,monospace; font-size:11px;"
            "background:#222; color:#8f8; padding:6px;"
        )
        lay.addWidget(self.log)

        # Keep references so widgetsAt() can find them.
        self.zones = {"cross": cross, "hand": hand, "plain": plain}

    def log_event(self, text: str):
        self._events.append(text)
        self._events[:] = self._events[-12:]
        self.log.setText("\n".join(self._events))
        print(text, flush=True)

    def eventFilter(self, watched, event):
        t = event.type()
        if t not in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Enter,
            QEvent.Type.Leave,
            QEvent.Type.TabletMove,
            QEvent.Type.TabletPress,
            QEvent.Type.TabletRelease,
            QEvent.Type.TabletEnterProximity,
            QEvent.Type.TabletLeaveProximity,
        ):
            return super().eventFilter(watched, event)

        try:
            gpos = event.globalPosition().toPoint()
        except Exception:
            gpos = None
        try:
            source = event.source().name
        except Exception:
            source = "-"
        try:
            pointer = event.pointerType().name
        except Exception:
            pointer = "-"
        try:
            buttons = event.buttons().name
        except Exception:
            buttons = "-"

        w = QApplication.widgetAt(gpos) if gpos is not None else None
        if w is not None:
            try:
                shape = w.cursor().shape().name
            except Exception:
                shape = "?"
        else:
            shape = "-"

        forced = ""
        if t == QEvent.Type.TabletMove and gpos is not None:
            target = (w.cursor().shape() if w is not None
                      else Qt.CursorShape.ArrowCursor)
            forced = f" forced={'OK' if force_cursor(target) else 'FAIL'}"

        cursor_pos = QCursor.pos()
        self.log_event(
            f"{t.name:28} src={source:22} ptr={pointer:18} "
            f"pos={gpos} cursor={cursor_pos} widget={type(w).__name__ if w else '-'} "
            f"shape={shape} buttons={buttons}{forced}"
        )
        return super().eventFilter(watched, event)


def main():
    app = QApplication(sys.argv)
    win = ProbeWindow()
    app.installEventFilter(win)
    win.show()
    print("Probe running — hover the pen over the window, then close it.", flush=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
