"""DirectComposition OKLCh overlay — C++ EXE via subprocess."""
import os, subprocess, time

_CTRL_FILE = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "dcomp_overlay_mode.txt")
MODE_DISABLED, MODE_OKLCH, MODE_LUMA = 0, 1, 2
_MODE_MAP = {"disabled": MODE_DISABLED, "oklch": MODE_OKLCH, "luma": MODE_LUMA}

class DCompOverlayController:
    def __init__(self):
        self._exe = self._find_exe()
        self._proc = None
        self._active = False
        self._mode = "oklch"

    @staticmethod
    def _find_exe():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "dcomp_overlay", "build", "dcomp_overlay.exe")
        return os.path.abspath(path) if os.path.exists(path) else None

    @property
    def is_available(self): return self._exe is not None
    @property
    def is_active(self): return self._active

    def _write_mode(self, mode: int):
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f: f.write(str(mode))
        except Exception: pass

    def set_mode(self, mode: str):
        if mode not in _MODE_MAP: raise ValueError(mode)
        if mode != "disabled":
            self._mode = mode
        if self._active or mode == "disabled":
            self._write_mode(_MODE_MAP[mode])

    def set_active(self, active: bool, mode: str = None):
        if mode is None:
            mode = self._mode
        if active:
            if self._proc is None or self._proc.poll() is not None:
                self.start()
            self._active = True
            self.set_mode(mode)
        else:
            self._active = False
            self.set_mode("disabled")

    def toggle(self):
        self.set_active(not self._active, self._mode)

    def start(self):
        if self._proc and self._proc.poll() is None: return
        if not self._exe: raise FileNotFoundError("dcomp_overlay.exe not found")
        self._write_mode(MODE_DISABLED)
        self._proc = subprocess.Popen([self._exe], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
        time.sleep(0.3)

    def stop(self):
        self._write_mode(MODE_DISABLED); self._active = False
        if self._proc:
            try: self._proc.terminate(); self._proc.wait(timeout=3)
            except: pass
            self._proc = None

    def close(self): self.stop()
    def set_target(self, t): pass
    @property
    def target(self): return "all"
    @staticmethod
    def available_screens(): return ["all"]
