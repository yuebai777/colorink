"""Read-back transparent-state regression tests for MemorySyncThread.

Covers the csp-memory, udm-memory, and pure-companion poll paths with fake
backends, verifying that:

* csp 内存模式（4.x/5.0）与 companion 完全独立 —— companion 即使可用，其观察值也
  不会进入 csp 模式的轮询；
* UDM 内存模式的透明色写入能够真正落到 udm_sync.set_color(..., transparent=True)，
  且激活槽变透明后轮询不得通过副色读取把它清掉；
* 激活槽透明时，常规 RGB 内存值变化不转发且不误发透明清除信号；
* pure-companion 模式清除透明状态仅转发一次，并正确映射到当前激活槽。
"""

import time

import pytest
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread


class FakeCompanion:
    """Mimics the CSPCompanionSync surface the poll loop uses."""

    def __init__(self) -> None:
        self._connected = True
        self.connect_ok = True
        self.main = {"r": 180, "g": 130, "b": 30, "index": 0}
        self.sub = {"r": 255, "g": 255, "b": 255, "index": 1}
        self.active_index = 0
        self.transparent = False

    def connect(self) -> bool:
        if not self.connect_ok:
            return False
        self._connected = True
        return True

    def status(self) -> dict:
        return {"connected": True}

    def get_color_hsv(self) -> dict:
        return {"h": 0.0, "s": 0.0, "v": 0.0,
                "r": 255, "g": 255, "b": 255,
                "transparent": self.transparent,
                "index": self.active_index}

    def set_color(self, r, g, b, hsv_u32=None, transparent=False, color_index=0) -> bool:
        self.transparent = bool(transparent)
        return True


class FakeCsp:
    """Mimics the CSP 4.x/5.0 single-slot CSPSync surface the poll loop uses."""

    def __init__(self) -> None:
        self.main = {"r": 180, "g": 130, "b": 30}

    def get_color(self) -> dict:
        return dict(self.main)

    def set_color(self, r, g, b, source_space=None, source_values=None,
                  transparent=False, color_index=0) -> bool:
        self.main = {"r": r, "g": g, "b": b}
        return True

    def status(self) -> dict:
        return {"connected": True}


class FakeUdm:
    """Mimics the udm_brush_link.UDMSync dual-slot + transparent surface."""

    def __init__(self) -> None:
        self.main = {"r": 180, "g": 130, "b": 30}
        self.sub = {"r": 255, "g": 255, "b": 255, "transparent": 0, "index": 1}
        self.main_flag = 0
        self.active_index = 0
        self.writes: list[dict] = []

    def get_sub_color(self) -> dict:
        return dict(self.sub)

    def get_color(self) -> dict:
        d = dict(self.main)
        d["transparent"] = self.main_flag
        return d

    def get_active_slot_index(self) -> int | None:
        return self.active_index

    def set_color(self, r, g, b, source_space=None, source_values=None,
                  transparent=False, color_index=0) -> bool:
        self.writes.append({
            "rgb": (r, g, b),
            "transparent": bool(transparent),
            "color_index": color_index,
        })
        self.main_flag = 1 if transparent else 0
        return True

    def status(self) -> dict:
        return {"connected": True}


@pytest.fixture()
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _Harness:
    """Runs a MemorySyncThread with fakes and records its signals."""

    def __init__(self, mode: str, companion: FakeCompanion | None,
                 csp: FakeCsp | None, qapp, udm: FakeUdm | None = None) -> None:
        self.qapp = qapp
        self.thread = MemorySyncThread()
        self.thread.software_mode = mode
        if companion is not None:
            setattr(self.thread, "companion_sync", companion)
        if csp is not None:
            setattr(self.thread, "csp_sync", csp)
        if udm is not None:
            setattr(self.thread, "udm_sync", udm)
        self.events: list[str] = []

        def on_color(r, g, b, i):
            self.events.append(f"color({r},{g},{b},{i})")

        def on_transp(i, t):
            self.events.append(f"transp({i},{t})")

        def on_active(i):
            self.events.append(f"active({i})")

        self.thread.signals.color_changed.connect(on_color)
        self.thread.signals.transparent_changed.connect(on_transp)
        self.thread.signals.active_slot_changed.connect(on_active)

    def start(self):
        self.thread.start()

    def drain(self, seconds: float):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.qapp.processEvents()
            time.sleep(0.02)

    def stop(self):
        self.thread.stop()

    def events_since(self, marker: int) -> list[str]:
        return self.events[marker:]


def _wait_until(harness, pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(harness.events):
            return True
        harness.qapp.processEvents()
        time.sleep(0.02)
    return False


def test_csp_mode_independent_of_companion(qapp):
    """csp 内存模式必须完全忽略 companion，且不调用已移除的 5.1 副槽接口。"""
    companion = FakeCompanion()
    companion._connected = True
    companion.sub = {"r": 200, "g": 100, "b": 50, "index": 1}
    companion.main = {"r": 200, "g": 100, "b": 50, "index": 0}
    csp = FakeCsp()
    csp.main = {"r": 10, "g": 200, "b": 10}
    h = _Harness("csp", companion, csp, qapp)
    h.start()
    try:
        assert _wait_until(h, lambda e: any(
            e_.startswith("color(10,200,10,0") for e_ in e), 2.0), h.events
        h.drain(0.4)
        assert not any(e_.startswith("color(200,100,50,") for e_ in h.events), h.events
    finally:
        h.stop()


def test_udm_memory_transparent_write_reaches_backend_and_survives_poll(qapp):
    """Regression: UDM 模式透明色写入不得被 memory_sync 提前拦截，且写后轮询不误清。"""
    udm = FakeUdm()
    h = _Harness("udm", None, None, qapp, udm=udm)
    h.start()
    try:
        h.drain(0.4)
        mark = len(h.events)
        h.thread.write_color(255, 255, 255, transparent=True, color_index=1)
        assert _wait_until(
            h,
            lambda _e: any(w["transparent"] and w["color_index"] == 1 for w in udm.writes),
            2.0,
        ), f"UDM transparent write never reached udm_sync: {udm.writes}"
        h.drain(0.5)
        after = h.events_since(mark)
        assert not any(e_.startswith("transp(1,False") for e_ in after), after
        assert not any(e_.startswith("transp(0,False") for e_ in after), after
    finally:
        h.stop()


def test_udm_memory_transparent_ignores_rgb_change(qapp):
    """UDM 内存模式：激活槽透明时，主色 RGB 内存值变化不转发且不触发虚假清除。"""
    udm = FakeUdm()
    h = _Harness("udm", None, None, qapp, udm=udm)
    h.start()
    try:
        h.drain(0.4)
        udm.main_flag = 1
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(0,True") for e_ in e), 2.0), h.events

        mark = len(h.events)
        udm.main = {"r": 10, "g": 20, "b": 30}
        h.drain(0.5)
        after = h.events_since(mark)
        assert not any(e_.startswith("transp(1,False") for e_ in after), after
        assert not any(e_.startswith("transp(0,False") for e_ in after), after
        assert not any(e_.startswith("color(10,20,30,0") for e_ in after), after
    finally:
        h.stop()


def test_clear_transparent_still_propagates(qapp):
    """Clearing the transparent state must reach the UI exactly once
    (pure-companion mode)."""
    companion = FakeCompanion()
    csp = FakeCsp()
    h = _Harness("companion", companion, csp, qapp)
    h.start()
    try:
        h.drain(0.4)
        # External change in CSP: sub slot becomes transparent.
        companion.active_index = 1
        companion.transparent = True
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(1,True") for e_ in e), 2.0), h.events

        mark = len(h.events)
        # External clear in CSP: sub slot becomes opaque again.
        companion.transparent = False
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(1,False") for e_ in e), 2.0), h.events
        h.drain(0.6)
        after = h.events_since(mark)
        clears = [e for e in after if e.startswith("transp(1,False")]
        assert len(clears) == 1, after
    finally:
        h.stop()


def test_pure_companion_sub_transparent_maps_to_sub(qapp):
    """Pure companion mode: the active-slot flag must be reported with the
    active index (sub -> index 1), not hardcoded to main."""
    companion = FakeCompanion()
    csp = FakeCsp()
    h = _Harness("companion", companion, csp, qapp)
    h.start()
    try:
        h.drain(0.4)
        mark = len(h.events)
        companion.active_index = 1
        companion.transparent = True
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(1,True") for e_ in e), 2.0), h.events
        h.drain(0.4)
        after = h.events_since(mark)
        assert not any(e_.startswith("transp(0,") for e_ in after), after
    finally:
        h.stop()
