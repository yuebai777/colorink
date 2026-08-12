"""Detect running Photoshop instances (registered COM vs green/portable).

Pure logic module — no COM calls, no PyQt — so it stays unit-testable.

Each running ``Photoshop.exe`` is classified as one of two backends:

- ``com`` — a registered install whose ``LocalServer32`` path matches the
  running process. Colorink attaches to it via COM automation (the fast,
  live path: read/write ``ForegroundColor`` / ``BackgroundColor``).
- ``script-bridge`` — a running process with **no** COM registration
  (green / portable edition). Colorink controls it through an ExtendScript
  file bridge deployed into its ``Presets/Scripts`` folder.

``find_registered_progids`` probes the well-known versioned ProgIDs
(``Photoshop.Application`` plus ``Photoshop.Application.130..260``) because
enumerating the whole registry hive is far too slow for the sync loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PROCESS_NAME = "Photoshop.exe"
COM_KIND = "com"
SCRIPT_BRIDGE_KIND = "script-bridge"

# Versioned ProgID suffixes to probe (13 registry lookups, not a hive scan).
_VERSION_PROGID_NUMBERS = range(130, 271, 10)


@dataclass(frozen=True, slots=True)
class PhotoshopInstance:
    """A running Photoshop process and the best way to sync with it."""

    kind: str                  # COM_KIND | SCRIPT_BRIDGE_KIND
    label: str                 # stable display label (settings combo + status)
    exe_path: str
    pid: int
    progid: str | None = None  # set only for COM_KIND


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


def find_registered_progids() -> list[tuple[str, str]]:
    """Return ``[(progid, local_server32), ...]`` for every registered
    Photoshop COM ProgID whose server executable path resolves.

    The server path has any `` /Automation`` argument stripped; broken
    registrations (missing CLSID / LocalServer32) are skipped.
    """
    import winreg

    found: list[tuple[str, str]] = []
    candidates = ["Photoshop.Application"]
    candidates += [f"Photoshop.Application.{n}" for n in _VERSION_PROGID_NUMBERS]
    for progid in candidates:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + "\\CLSID") as ck:
                clsid = winreg.QueryValueEx(ck, "")[0]
            if not clsid:
                continue
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}") as ck2:
                with winreg.OpenKey(ck2, "LocalServer32") as lk:
                    path = winreg.QueryValueEx(lk, "")[0]
        except OSError:
            continue
        path = (path or "").split(" /", 1)[0].strip().strip('"')
        if path:
            found.append((progid, path))
    return found


def _instance_label(exe_path: str, kind: str) -> str:
    folder = os.path.basename(os.path.dirname(os.path.abspath(exe_path)))
    if not folder:
        folder = "Photoshop"
    if kind == COM_KIND:
        return f"{folder} (COM)"
    return f"{folder} (绿色版·脚本桥)"


def detect_instances() -> list[PhotoshopInstance]:
    """Classify every running Photoshop process by sync backend."""
    regs = find_registered_progids()
    instances: list[PhotoshopInstance] = []
    for pid, exe in find_running_photoshop():
        norm = os.path.normcase(os.path.abspath(exe))
        match = next(
            (r for r in regs
             if os.path.normcase(os.path.abspath(r[1])) == norm),
            None,
        )
        if match is not None:
            instances.append(PhotoshopInstance(
                kind=COM_KIND, label=_instance_label(exe, COM_KIND),
                exe_path=exe, pid=pid, progid=match[0],
            ))
        else:
            instances.append(PhotoshopInstance(
                kind=SCRIPT_BRIDGE_KIND,
                label=_instance_label(exe, SCRIPT_BRIDGE_KIND),
                exe_path=exe, pid=pid,
            ))
    return instances


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
