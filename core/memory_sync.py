import time
import sys
from PyQt6.QtCore import QThread, pyqtSignal, QObject

# Import native modules
from core import csp_color_sync
from core import sai2_color_sync
from core import udm_color_sync

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
        self.software_mode = "csp"  # "csp" | "sai" | "udm"
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
        
        # Instantiate CSP and UDM sync classes
        self.csp_sync = csp_color_sync.CSPSync()
        self.udm_sync = udm_color_sync.UDMSync()
        
        self.update_versions()
        
    def update_versions(self):
        # Set versions in backend scripts/instances
        self.csp_sync.set_version(self.csp_version)
        sai2_color_sync.set_sync_version(self.sai2_version)
        self.udm_sync.set_version(self.udm_version)
        
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
            status = sai2_color_sync.status()
            return status.get('pid')
        elif self.software_mode == 'udm':
            return self.udm_sync.pid if self.udm_sync.pm else None
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
                        sai2_color_sync.set_color(r, g, b)
                    elif self.software_mode == 'udm':
                        self.udm_sync.set_color(r, g, b)
                    continue
                
                # 2) Handle read request (polling)
                color = None
                connected = False
                
                if self.software_mode == 'csp':
                    color = self.csp_sync.get_color()
                    status = self.csp_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'sai':
                    color = sai2_color_sync.get_color()
                    status = sai2_color_sync.get_status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'udm':
                    color = self.udm_sync.get_color()
                    status = self.udm_sync.status()
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
