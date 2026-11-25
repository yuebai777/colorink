"""Tests for Photoshop process instance detection (pure psutil, no registry)."""

import sys
import types

from core import photoshop_instances as pi

GREEN_EXE = r"D:\Program Files\AdobePhotoshop_CC_2019_20.0.10.28848_Green\Adobe Photoshop CC 2019\Photoshop.exe"
REG_EXE = r"D:\Program Files\Adobe Photoshop 2020\Photoshop.exe"


def _fake_psutil(procs):
    return types.SimpleNamespace(
        process_iter=lambda attrs=None: iter(procs),
        NoSuchProcess=OSError,
        AccessDenied=PermissionError,
    )


def _proc(pid, name, exe):
    return types.SimpleNamespace(info={"pid": pid, "name": name, "exe": exe})


def _install(monkeypatch, procs=None):
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(procs or []))


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


# ── detect_instances ─────────────────────────────────────────────────────

class TestDetect:
    def test_every_running_instance_uses_script_bridge(self, monkeypatch):
        _install(monkeypatch, procs=[
            _proc(7, "Photoshop.exe", GREEN_EXE),
            _proc(42, "Photoshop.exe", REG_EXE),
        ])
        instances = pi.detect_instances()
        assert len(instances) == 2
        assert all(inst.kind == pi.SCRIPT_BRIDGE_KIND for inst in instances)
        assert all("(同步桥)" in inst.label for inst in instances)
        assert [inst.pid for inst in instances] == [7, 42]

    def test_no_running_photoshop_returns_empty(self, monkeypatch):
        _install(monkeypatch, procs=[])
        assert pi.detect_instances() == []


# ── pick_target ──────────────────────────────────────────────────────────

class TestPickTarget:
    def _instances(self):
        return [
            pi.PhotoshopInstance(kind=pi.SCRIPT_BRIDGE_KIND, label="A (同步桥)",
                                 exe_path=r"D:\A\Photoshop.exe", pid=1),
            pi.PhotoshopInstance(kind=pi.SCRIPT_BRIDGE_KIND, label="B (同步桥)",
                                 exe_path=r"D:\B\Photoshop.exe", pid=2),
        ]

    def test_auto_picks_first(self):
        inst = pi.pick_target(self._instances(), "auto")
        assert inst is not None
        assert inst.label == "A (同步桥)"

    def test_none_picks_first(self):
        inst = pi.pick_target(self._instances(), None)
        assert inst is not None
        assert inst.label == "A (同步桥)"

    def test_label_match(self):
        inst = pi.pick_target(self._instances(), "B (同步桥)")
        assert inst is not None
        assert inst.label == "B (同步桥)"

    def test_unknown_label_falls_back_to_first(self):
        inst = pi.pick_target(self._instances(), "不存在")
        assert inst is not None
        assert inst.label == "A (同步桥)"

    def test_empty_returns_none(self):
        assert pi.pick_target([], "auto") is None
