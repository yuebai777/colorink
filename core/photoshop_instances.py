"""Detect running Photoshop instances (genuine, green/portable, or custom).

Pure logic module — no COM calls, no registry lookups, no PyQt — so it stays
fast and unit-testable.

Every edition shares ONE sync path (the user-level CEP bridge in
:mod:`core.photoshop_script_bridge`), routed per instance by PID, so
display labels are uniform: ``<folder> (同步桥)``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PROCESS_NAME = "Photoshop.exe"
SCRIPT_BRIDGE_KIND = "script-bridge"


@dataclass(frozen=True, slots=True)
class PhotoshopInstance:
    """A running Photoshop process and its display label."""

    kind: str                  # always SCRIPT_BRIDGE_KIND
    label: str                 # uniform display label "<folder> (同步桥)"
    exe_path: str
    pid: int


def find_running_photoshop() -> list[tuple[int, str]]:
    """Return ``[(pid, exe_path), ...]`` for every running Photoshop.exe."""
    import psutil

    out: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if proc.info["name"] == PROCESS_NAME and proc.info.get("exe"):
                out.append((proc.info["pid"], proc.info["exe"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _instance_label(exe_path: str) -> str:
    folder = os.path.basename(os.path.dirname(os.path.abspath(exe_path)))
    if not folder:
        folder = "Photoshop"
    return f"{folder} (同步桥)"


def detect_instances() -> list[PhotoshopInstance]:
    """Return every running Photoshop process as a CEP-bridge instance."""
    return [
        PhotoshopInstance(
            kind=SCRIPT_BRIDGE_KIND,
            label=_instance_label(exe),
            exe_path=exe,
            pid=pid,
        )
        for pid, exe in find_running_photoshop()
    ]


def pick_target(
    instances: list[PhotoshopInstance], target_label: str | None,
) -> PhotoshopInstance | None:
    """Select the instance matching *target_label* ("auto" / None = first)."""
    if not instances:
        return None
    if target_label and target_label != "auto":
        for inst in instances:
            if inst.label == target_label:
                return inst
    return instances[0]
