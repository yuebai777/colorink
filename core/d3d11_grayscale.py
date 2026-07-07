"""
D3D11 OKLCh grayscale overlay — C++ EXE via subprocess.

Launches a standalone D3D11 EXE that uses Desktop Duplication API
to capture the screen and applies OKLCh/Luma pixel shaders directly
via DXGI swap chain — bypassing Qt's FBO composition.

No admin required. No DWM injection. Just a standard D3D11 window.

Usage:
    ctrl = D3D11OverlayController()
    ctrl.start()               # launch the EXE
    ctrl.set_mode("oklch")     # apply OKLCh grayscale
    ctrl.set_mode("luma")      # switch to BT.709 luma
    ctrl.set_mode("disabled")  # hide overlay (passthrough)
    ctrl.stop()                # kill the EXE
"""
import os
import subprocess
import time

# Control file path (same as defined in main.cpp CTRL_FILE)
_CTRL_FILE = os.path.join(
    os.environ.get("SYSTEMROOT", r"C:\Windows"), "Temp", "d3d11_overlay_mode.txt"
)

# Mode constants
MODE_DISABLED = 0
MODE_OKLCH = 1
MODE_LUMA = 2

_MODE_MAP = {"disabled": MODE_DISABLED, "oklch": MODE_OKLCH, "luma": MODE_LUMA}


class D3D11OverlayController:
    """Controls the D3D11 grayscale overlay EXE."""

    def __init__(self):
        self._exe_path = self._find_exe()
        self._process: subprocess.Popen | None = None
        self._active = False

    @staticmethod
    def _find_exe() -> str | None:
        """Locate d3d11_overlay.exe relative to this module."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "d3d11_overlay", "build", "d3d11_overlay.exe")
        if os.path.exists(path):
            return os.path.abspath(path)
        return None

    @property
    def is_available(self) -> bool:
        return self._exe_path is not None

    @property
    def is_active(self) -> bool:
        return self._active

    # -- Control file ----------------------------------------------------

    def _write_mode(self, mode: int):
        try:
            os.makedirs(os.path.dirname(_CTRL_FILE), exist_ok=True)
            with open(_CTRL_FILE, "w") as f:
                f.write(str(mode))
        except Exception as e:
            print(f"[D3D11Overlay] Write control file failed: {e}")

    # -- Public API ------------------------------------------------------

    def set_mode(self, mode: str):
        if mode not in _MODE_MAP:
            raise ValueError(f"Unknown mode: {mode!r}")
        self._write_mode(_MODE_MAP[mode])
        self._active = (mode != "disabled")

    def set_active(self, active: bool, mode: str = "oklch"):
        if active:
            if self._process is None or self._process.poll() is not None:
                self.start()
            self.set_mode(mode)
        else:
            self.set_mode("disabled")

    def toggle(self, mode: str = "oklch"):
        if self._active:
            self.set_active(False)
        else:
            self.set_active(True, mode)

    def start(self):
        """Launch the D3D11 overlay EXE."""
        if self._process and self._process.poll() is None:
            return  # already running
        if not self._exe_path:
            raise FileNotFoundError(
                "d3d11_overlay.exe not found. Build it:\n"
                "  cd d3d11_overlay && build.bat"
            )
        # Write disabled state before launching
        self._write_mode(MODE_DISABLED)
        # Launch detached
        self._process = subprocess.Popen(
            [self._exe_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        print(f"[D3D11Overlay] Launched (PID={self._process.pid})")
        time.sleep(0.3)  # let the window initialize

    def stop(self):
        """Kill the EXE process."""
        self._write_mode(MODE_DISABLED)
        self._active = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        # Delete control file
        try:
            os.remove(_CTRL_FILE)
        except Exception:
            pass

    def close(self):
        self.stop()

    # -- Compatibility API (matches GrayscaleOverlay interface) ----------

    def set_target(self, target: str):
        pass  # no-op (always primary monitor for now)

    @property
    def target(self) -> str:
        return "all"

    @staticmethod
    def available_screens() -> list[str]:
        return ["all"]  # single monitor for now
