import time
import sys
from PyQt6.QtCore import QThread, pyqtSignal, QObject

# Import native modules
from core import csp_brush_link
from core import sai2_brush_link
from core import udm_brush_link
from core import photoshop_color_sync

class MemorySyncSignals(QObject):
    # Emitted when the drawing software color changes: (r, g, b)
    color_changed = pyqtSignal(int, int, int)
    # Emitted when the connection status changes: (software_mode, connected_bool)
    status_changed = pyqtSignal(str, bool)

class MemorySyncThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = MemorySyncSignals()
        self.running = True
        
        # State variables
        self.software_mode = "csp"  # "csp" | "sai" | "udm" | "ps"
        self.sync_enabled = True
        self.paused = False
        
        # Versions for memory syncing
        self.csp_version = "auto"
        self.sai2_version = "auto"
        self.udm_version = "auto"
        
        # Cache to prevent loops
        self.last_synced_color = None  # (r, g, b)
        self.pending_write_color = None  # (r, g, b)
        self.last_write_time = 0.0
        
        # Instantiate per-software sync backends
        self.csp_sync = csp_brush_link.CSPSync()
        self.sai2_sync = sai2_brush_link.SAI2Sync()
        self.udm_sync = udm_brush_link.UDMSync()
        self.ps_sync = photoshop_color_sync.PhotoshopSync()
        
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
            
    def write_color(self, r, g, b):
        self.pending_write_color = (r, g, b)
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
        return None
        
    def stop(self):
        self.running = False
        self.wait()
        
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
                    self.pending_write_color = None
                    
                    self.last_synced_color = (r, g, b)
                    
                    if self.software_mode == 'csp':
                        self.csp_sync.set_color(r, g, b)
                    elif self.software_mode == 'sai':
                        self.sai2_sync.set_color(r, g, b)
                    elif self.software_mode == 'udm':
                        self.udm_sync.set_color(r, g, b)
                    elif self.software_mode == 'ps':
                        self.ps_sync.set_color(r, g, b)
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
                    
                # Notify status change
                status_key = (self.software_mode, connected)
                if status_key != last_status:
                    self.signals.status_changed.emit(self.software_mode, connected)
                    last_status = status_key
                    
                if not connected or color is None:
                    continue
                    
                r = color.get('r')
                g = color.get('g')
                b = color.get('b')
                if r is None or g is None or b is None:
                    continue
                    
                # If we recently wrote a color locally, ignore incoming reads to prevent drag feedback loops
                if time.time() - self.last_write_time < 0.8:
                    continue
                    
                color_tuple = (r, g, b)
                if self.last_synced_color != color_tuple:
                    self.last_synced_color = color_tuple
                    self.signals.color_changed.emit(r, g, b)
                    
            except Exception as e:
                # Avoid flooding console in thread
                pass
