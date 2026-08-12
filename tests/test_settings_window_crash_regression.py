"""Regression: settings-window construction must not crash the process.

The settings sidebar's ``hideEvent`` re-applies the no-focus window state
by calling ``MainWindow.update_window_flags()`` /
``update_no_focus_policies()`` when ``noFocusMode`` is enabled.  If the
host window lacks those methods (e.g. an incomplete stub), the
``AttributeError`` raised inside the Qt event handler aborts the process
with a fail-fast 0xC0000409 — no traceback, tests die with no output.

This test drives a subprocess through the sidebar + settings-window
construction with a host stub that exposes those methods, and asserts the
process survives.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SUBPROCESS_SCRIPT = r"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")

from PyQt6.QtWidgets import QApplication, QWidget

app = QApplication([])

from core.config import load_hotkey_config


class StubCompanionSync:
    _connected = False

    def _has_session(self):
        return False

    def _disconnect(self):
        pass


class StubSyncThread:
    def __init__(self):
        self.companion_sync = StubCompanionSync()


class StubMainWindow(QWidget):
    # Mirrors the methods MainWindow exposes that the sidebar touches.
    def __init__(self):
        super().__init__()
        self.cfg = load_hotkey_config()
        self.cfg.setdefault("ui-theme", "auto")
        self.cfg.setdefault("fontSize", 100)
        self.sync_thread = StubSyncThread()

    def update_window_flags(self):
        pass

    def update_no_focus_policies(self):
        pass


stub = StubMainWindow()

from ui.settings_sidebar import SettingsSidebar

s = SettingsSidebar(stub)
s.setVisible(False)

from ui.settings_window import SettingsWindow

SettingsWindow(stub, s)
print("OK")
"""


def test_settings_window_construction_survives_subprocess():
    script = _SUBPROCESS_SCRIPT.format(root=PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "settings-window construction crashed the subprocess "
        f"(exit {result.returncode}); stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
