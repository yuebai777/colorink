"""Tests for Photoshop instance detection (registered COM vs green/portable).

Mocks psutil + winreg so the pure detection logic is testable without a
running Photoshop or a real registry.
"""

import sys
import types

import pytest

from core import photoshop_instances as pi

GREEN_EXE = r"D:\Program Files\AdobePhotoshop_CC_2019_20.0.10.28848_Green\Adobe Photoshop CC 2019\Photoshop.exe"
REG_EXE = r"D:\Program Files\Adobe Photoshop 2020\Photoshop.exe"
REG_GUID = "{c4c3e2fb-d66c-44e5-96a0-349f951cb3d4}"


def _fake_psutil(procs):
    fake = types.SimpleNamespace(
        process_iter=lambda attrs=None: iter(procs),
        NoSuchProcess=OSError,
        AccessDenied=PermissionError,
    )
    return fake


def _fake_winreg(entries):
    """entries: absolute key path -> default value string."""

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def QueryValueEx(self, name, *a):
            if self.path in entries:
                return (entries[self.path], 1)
            raise OSError

    def OpenKey(root, sub):
        base = root.path if isinstance(root, _Key) else ""
        full = f"{base}\\{sub}" if isinstance(root, _Key) else sub
        # A key "exists" when it has a value or any deeper value registered.
        if full in entries or any(e.startswith(full + "\\") for e in entries):
            return _Key(full)
        raise OSError

    def QueryValueEx(key, name):
        return key.QueryValueEx(name)

    return types.SimpleNamespace(
        OpenKey=OpenKey, QueryValueEx=QueryValueEx, HKEY_CLASSES_ROOT="ROOT")


def _proc(pid, name, exe):
    return types.SimpleNamespace(info={"pid": pid, "name": name, "exe": exe})


@pytest.fixture
def no_registry(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({}))


def _install(monkeypatch, procs=None, entries=None):
    monkeypatch.setitem(
        sys.modules, "psutil", _fake_psutil(procs or []))
    monkeypatch.setitem(
        sys.modules, "winreg", _fake_winreg(entries or {}))


# ── find_running_photoshop ───────────────────────────────────────────────

class TestFindRunning:
    def test_finds_all_photoshop_processes(self, monkeypatch):
        _install(monkeypatch, procs=[
            _proc(1, "Photoshop.exe", GREEN_EXE),
            _proc(2, "Photoshop.exe", REG_EXE),
            _proc(3, "NotPhotoshop.exe", r"C:\x\NotPhotoshop.exe"),
        ])
        got = pi.find_running_photoshop()
        assert (1, GREEN_EXE) in got
        assert (2, REG_EXE) in got
        assert len(got) == 2

    def test_skips_denied_processes(self, monkeypatch):
        class _Denied:
            @property
            def info(self):
                raise PermissionError()

        _install(monkeypatch, procs=[
            _proc(1, "Photoshop.exe", GREEN_EXE),
            _Denied(),
            _proc(2, "Photoshop.exe", REG_EXE),
        ])
        assert pi.find_running_photoshop() == [(1, GREEN_EXE), (2, REG_EXE)]

    def test_empty_when_no_photoshop(self, monkeypatch):
        _install(monkeypatch, procs=[])
        assert pi.find_running_photoshop() == []


# ── find_registered_progids ──────────────────────────────────────────────

class TestRegisteredProgids:
    def test_returns_progid_with_stripped_automation_args(self, monkeypatch):
        _install(monkeypatch, entries={
            "Photoshop.Application.140\\CLSID": REG_GUID,
            f"CLSID\\{REG_GUID}\\LocalServer32": f'"{REG_EXE}" /Automation',
        })
        got = pi.find_registered_progids()
        assert got == [("Photoshop.Application.140", REG_EXE)]

    def test_skips_missing_clsid(self, monkeypatch):
        _install(monkeypatch, entries={})
        assert pi.find_registered_progids() == []

    def test_skips_missing_local_server32(self, monkeypatch):
        _install(monkeypatch, entries={
            "Photoshop.Application.140\\CLSID": REG_GUID,
        })
        assert pi.find_registered_progids() == []

    def test_probes_version_independent_and_versioned(self, monkeypatch):
        seen = []

        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def QueryValueEx(self, name, *a):
                raise OSError

        def OpenKey(root, sub):
            seen.append(sub)
            raise OSError

        fake = types.SimpleNamespace(
            OpenKey=OpenKey, QueryValueEx=lambda k, n: k.QueryValueEx(n),
            HKEY_CLASSES_ROOT="ROOT")
        monkeypatch.setitem(sys.modules, "winreg", fake)
        pi.find_registered_progids()
        assert "Photoshop.Application\\CLSID" in seen
        assert "Photoshop.Application.210\\CLSID" in seen  # within 130..260 range


# ── detect_instances classification ──────────────────────────────────────

class TestDetect:
    def test_registered_running_is_com(self, monkeypatch):
        _install(monkeypatch, procs=[_proc(42, "Photoshop.exe", REG_EXE)],
                 entries={
                     "Photoshop.Application.210\\CLSID": REG_GUID,
                     f"CLSID\\{REG_GUID}\\LocalServer32": REG_EXE,
                 })
        instances = pi.detect_instances()
        assert len(instances) == 1
        inst = instances[0]
        assert inst.kind == pi.COM_KIND
        assert inst.progid == "Photoshop.Application.210"
        assert inst.pid == 42
        assert "(COM)" in inst.label

    def test_green_running_is_script_bridge(self, monkeypatch, no_registry):
        _install(monkeypatch, procs=[_proc(7, "Photoshop.exe", GREEN_EXE)])
        instances = pi.detect_instances()
        assert len(instances) == 1
        inst = instances[0]
        assert inst.kind == pi.SCRIPT_BRIDGE_KIND
        assert inst.progid is None
        assert "绿色版" in inst.label

    def test_mixed_installs_classified_independently(self, monkeypatch):
        _install(monkeypatch,
                 procs=[_proc(1, "Photoshop.exe", GREEN_EXE),
                        _proc(2, "Photoshop.exe", REG_EXE)],
                 entries={
                     "Photoshop.Application.210\\CLSID": REG_GUID,
                     f"CLSID\\{REG_GUID}\\LocalServer32": REG_EXE,
                 })
        kinds = {inst.kind for inst in pi.detect_instances()}
        assert kinds == {pi.COM_KIND, pi.SCRIPT_BRIDGE_KIND}

    def test_no_running_photoshop_returns_empty(self, monkeypatch, no_registry):
        _install(monkeypatch, procs=[])
        assert pi.detect_instances() == []


# ── pick_target ──────────────────────────────────────────────────────────

class TestPickTarget:
    def _instances(self):
        return [
            pi.PhotoshopInstance(kind=pi.COM_KIND, label="A (COM)",
                                 exe_path=r"D:\A\Photoshop.exe", pid=1,
                                 progid="Photoshop.Application.210"),
            pi.PhotoshopInstance(kind=pi.SCRIPT_BRIDGE_KIND, label="B (绿色版·脚本桥)",
                                 exe_path=r"D:\B\Photoshop.exe", pid=2),
        ]

    def test_auto_picks_first(self):
        inst = pi.pick_target(self._instances(), "auto")
        assert inst is not None
        assert inst.label == "A (COM)"

    def test_none_picks_first(self):
        inst = pi.pick_target(self._instances(), None)
        assert inst is not None
        assert inst.label == "A (COM)"

    def test_label_match(self):
        inst = pi.pick_target(self._instances(), "B (绿色版·脚本桥)")
        assert inst is not None
        assert inst.label == "B (绿色版·脚本桥)"

    def test_unknown_label_falls_back_to_first(self):
        inst = pi.pick_target(self._instances(), "不存在")
        assert inst is not None
        assert inst.label == "A (COM)"

    def test_empty_returns_none(self):
        assert pi.pick_target([], "auto") is None
