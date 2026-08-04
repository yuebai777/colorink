import sys
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# Import native modules
from core import (
    csp_brush_link,
    csp_companion_sync,
    photoshop_color_sync,
    sai2_brush_link,
    udm_brush_link,
)


class MemorySyncSignals(QObject):
    # Emitted when the drawing software color changes: (r, g, b)
    color_changed = pyqtSignal(int, int, int)
    # Emitted when the connection status changes: (software_mode, connected_bool)
    status_changed = pyqtSignal(str, bool)
    # Emitted when the connection failure reason changes:
    # (software_mode, error_text, permission_issue)
    # error_text is empty when the backend is connected / healthy.
    # permission_issue hints the failure is a UAC privilege mismatch
    # (e.g. Photoshop elevated, Colorink not) — the UI can offer a
    # one-click "relaunch as administrator".
    error_changed = pyqtSignal(str, str, bool)

class MemorySyncThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = MemorySyncSignals()
        self.running = True
        
        # State variables
        self.software_mode = "csp"  # "csp" | "sai" | "udm" | "ps" | "companion"
        self.sync_enabled = True
        self.paused = False
        
        # Versions for memory syncing
        self.csp_version = "auto"
        self.sai2_version = "auto"
        self.udm_version = "auto"
        
        # Cache to prevent loops
        self.last_synced_color = None  # (r, g, b)
        self.pending_write_color = None  # (r, g, b)
        self.pending_hsv_u32 = None      # (h, s, v) uint32 for companion mode
        self.pending_source_space = None # e.g. "hsv", "hls" — for CSP memory mode
        self.pending_source_values = None # float values in that space
        self.companion_hsv = None
        self._last_companion_hsv = (0.0, 0.0, 0.0)
        self.last_write_time = 0.0
        self._last_error_text = ("", False)
        
        # Instantiate per-software sync backends
        self.csp_sync = csp_brush_link.CSPSync()
        self.sai2_sync = sai2_brush_link.SAI2Sync()
        self.udm_sync = udm_brush_link.UDMSync()
        self.ps_sync = photoshop_color_sync.PhotoshopSync()
        self.companion_sync = csp_companion_sync.CSPCompanionSync()
        
        self.update_versions()
        
    def update_versions(self):
        # Set versions in backend scripts/instances
        self.csp_sync.set_version(self.csp_version)
        self.sai2_sync.set_version(self.sai2_version)
        self.udm_sync.set_version(self.udm_version)
        self.ps_sync.set_version(getattr(self, 'ps_version', 'auto'))
        
    def set_software_mode(self, mode):
        self.software_mode = mode
        self.last_synced_color = None
        
    def set_sync_enabled(self, enabled):
        self.sync_enabled = enabled
        if not enabled:
            self.last_synced_color = None
            
    def write_color(self, r, g, b, hsv_u32=None, source_space=None, source_values=None):
        self.pending_write_color = (r, g, b)
        self.pending_hsv_u32 = hsv_u32
        self.pending_source_space = source_space
        self.pending_source_values = source_values
        self.last_write_time = time.time()
        
    def get_active_pid(self):
        if not self.sync_enabled or self.paused:
            return None
        if self.software_mode == 'csp':
            return self.csp_sync.pid if self.csp_sync.pm else None
        elif self.software_mode == 'sai':
            status = self.sai2_sync.status()
            return status.get('pid')
        elif self.software_mode == 'udm':
            return self.udm_sync.pid if self.udm_sync.pm else None
        elif self.software_mode == 'ps':
            status_ = self.ps_sync.status()
            return status_.get('pid')
        elif self.software_mode == 'companion':
            return None
        return None
        
    def stop(self, timeout_ms: int = 3000):
        """Signal the thread to stop, then wait with a timeout.

        If the thread is blocked on a hung COM call (e.g. Photoshop died),
        waiting forever would freeze the main thread.  After *timeout_ms*
        we terminate the thread to unblock the caller.
        """
        self.running = False
        if not self.wait(timeout_ms):
            # Thread is stuck — likely in a hung COM RPC call
            self.terminate()
            self.wait(500)
        
    def run(self):
        last_status = None
        
        while self.running:
            # Sleep 100ms
            time.sleep(0.1)
            
            if not self.sync_enabled or self.paused:
                continue
                
            try:
                # 1) Handle write request
                if self.pending_write_color is not None:
                    r, g, b = self.pending_write_color
                    hsv_override = self.pending_hsv_u32
                    src_space = self.pending_source_space
                    src_vals = self.pending_source_values
                    self.pending_write_color = None
                    self.pending_hsv_u32 = None
                    self.pending_source_space = None
                    self.pending_source_values = None

                    self.last_synced_color = (r, g, b)

                    if self.software_mode == 'csp':
                        self.csp_sync.set_color(r, g, b, source_space=src_space, source_values=src_vals)
                    elif self.software_mode == 'sai':
                        self.sai2_sync.set_color(r, g, b)
                    elif self.software_mode == 'udm':
                        self.udm_sync.set_color(r, g, b)
                    elif self.software_mode == 'ps':
                        self.ps_sync.set_color(r, g, b)
                    elif self.software_mode == 'companion':
                        self.companion_sync.set_color(r, g, b, hsv_u32=hsv_override)
                        # Seed dedup with what we just wrote so the read-back
                        # echo is suppressed (HSV dedup below catches it).
                        if hsv_override:
                            _U32 = 4294967295
                            self.companion_hsv = (
                                hsv_override[0] / _U32 * 360.0,
                                hsv_override[1] / _U32 * 100.0,
                                hsv_override[2] / _U32 * 100.0,
                            )
                            self._last_companion_hsv = self.companion_hsv
                    continue
                
                # 2) Handle read request (polling)
                color = None
                connected = False
                
                if self.software_mode == 'csp':
                    color = self.csp_sync.get_color()
                    status = self.csp_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'sai':
                    color = self.sai2_sync.get_color()
                    status = self.sai2_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'udm':
                    color = self.udm_sync.get_color()
                    status = self.udm_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'ps':
                    color = self.ps_sync.get_color()
                    status = self.ps_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'companion':
                    color = self.companion_sync.get_color_hsv()
                    if color: self.companion_hsv = (color["h"], color["s"], color["v"])
                    else: self.companion_hsv = None
                    status = self.companion_sync.status()
                    connected = status.get('connected', False)
                    
                # Notify status change
                status_key = (self.software_mode, connected)
                if status_key != last_status:
                    self.signals.status_changed.emit(self.software_mode, connected)
                    last_status = status_key

                # Notify failure reason changes (Photoshop COM etc.) so the
                # UI can show *why* the backend is not connected.
                err_text = ""
                perm_issue = False
                if not connected and self.software_mode == 'ps':
                    err_text = getattr(self.ps_sync, "last_error", "") or ""
                    perm_issue = bool(getattr(self.ps_sync, "permission_issue", False))
                if (err_text, perm_issue) != self._last_error_text:
                    self._last_error_text = (err_text, perm_issue)
                    self.signals.error_changed.emit(self.software_mode, err_text, perm_issue)

                if not connected or color is None:
                    continue
                    
                r = color.get('r')
                g = color.get('g')
                b = color.get('b')
                if r is None or g is None or b is None:
                    continue
                    
                if self.last_synced_color is not None:
                    if self.software_mode == 'companion' and self.companion_hsv is not None:
                        lh, ls, lv = self._last_companion_hsv
                        ch, cs, cv = self.companion_hsv
                        if abs(ch - lh) <= 0.1 and abs(cs - ls) <= 0.5 and abs(cv - lv) <= 0.5:
                            continue
                        self._last_companion_hsv = self.companion_hsv
                    else:
                        lr, lg, lb = self.last_synced_color
                        if abs(r - lr) <= 2 and abs(g - lg) <= 2 and abs(b - lb) <= 2:
                            continue

                color_tuple = (r, g, b)
                if self.last_synced_color != color_tuple:
                    self.last_synced_color = color_tuple
                    self.signals.color_changed.emit(r, g, b)
                    
            except Exception as e:
                # Avoid flooding console in thread
                pass
