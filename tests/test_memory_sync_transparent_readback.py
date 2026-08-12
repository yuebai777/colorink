"""Read-back transparent-state regression tests for MemorySyncThread.

Covers the csp-memory / pure-companion poll paths with fake backends,
verifying that:

* csp 内存模式与 companion 完全独立 —— companion 即使可用，其观察值也
  不会进入 csp 模式的轮询（副色/主色全部来自进程内存）；
* setting a slot transparent is NOT undone by the very next poll (the
  sub-color memory read must not emit a spurious (1, False) after the
  write seeded the dedup cache);
* a transparent active slot ignores routine RGB read-backs (no spurious
  clear signals, and transparent RGB values are not forwarded);
* clearing transparent still propagates exactly once;
* pure-companion read-back maps the flag to the reported active index.
"""

import time

import pytest
from PyQt6.QtWidgets import QApplication

from core.memory_sync import MemorySyncThread


class FakeCompanion:
    """Mimics the CSPCompanionSync surface the poll loop uses.

    Kept connected on purpose: the csp-memory path must NOT touch it.
    """

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

    def get_colors_hsv(self) -> dict:
        main = dict(self.main)
        main["transparent"] = self.transparent
        sub = dict(self.sub)
        sub["transparent"] = self.transparent
        return {"main": main, "sub": sub,
                "active_index": self.active_index,
                "transparent": self.transparent}

    def status(self) -> dict:
        return {"connected": True}

    def get_color_hsv(self) -> dict:
        return {"h": 0.0, "s": 0.0, "v": 0.0,
                "r": 255, "g": 255, "b": 255,
                "transparent": self.transparent,
                "index": self.active_index}


class FakeCsp:
    """Mimics the csp_brush_link.CSPSync surface the poll loop uses."""

    def __init__(self) -> None:
        self.main = {"r": 180, "g": 130, "b": 30}
        self.sub = {"r": 255, "g": 255, "b": 255, "transparent": 0, "index": 1}
        self.main_flag = 0  # 0/1 transparent flag reported by get_color()
        self.active_index = 0  # active slot reported by get_active_slot_index()

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
                 csp: FakeCsp | None, qapp) -> None:
        self.qapp = qapp
        self.thread = MemorySyncThread()
        self.thread.software_mode = mode
        setattr(self.thread, "companion_sync", companion)
        setattr(self.thread, "csp_sync", csp)
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


@pytest.fixture()
def harness(qapp):
    return _Harness  # bound in each test


def _wait_until(harness, pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(harness.events):
            return True
        harness.qapp.processEvents()
        time.sleep(0.02)
    return False


def test_csp_mode_independent_of_companion(qapp):
    """Regression: csp 内存模式必须完全忽略 companion —— 即使 companion
    已连接并报告了不同的副色/主色，轮询也只使用进程内存的值。

    Previously the memory sub-color mirror was appended after the companion
    observation (or vice versa), so the bg swatch showed a color that did
    not match CSP's real background color until the user picked any color.
    """
    companion = FakeCompanion()
    companion._connected = True  # companion 可用，但必须被忽略
    companion.sub = {"r": 200, "g": 100, "b": 50, "index": 1}
    companion.main = {"r": 200, "g": 100, "b": 50, "index": 0}
    csp = FakeCsp()
    csp.sub = {"r": 10, "g": 200, "b": 10, "transparent": 0, "index": 1}
    csp.main = {"r": 10, "g": 200, "b": 10}
    h = _Harness("csp", companion, csp, qapp)
    h.start()
    try:
        # 主色/副色都来自内存值（companion 的值不出现）。
        assert _wait_until(h, lambda e: any(
            e_.startswith("color(10,200,10,0") for e_ in e), 2.0), h.events
        assert _wait_until(h, lambda e: any(
            e_.startswith("color(10,200,10,1") for e_ in e), 2.0), h.events
        h.drain(0.5)
        assert not any(e_.startswith("color(200,100,50,") for e_ in h.events), h.events
    finally:
        h.stop()


def test_csp_memory_bg_transparent_survives_poll(qapp):
    """csp 内存模式：激活槽变透明后，轮询不得通过副色内存读取把它清掉
    （回归：报告透明后立即出现虚假的 transparent_changed(1, False)）。"""
    companion = FakeCompanion()  # 可用但被忽略
    csp = FakeCsp()
    h = _Harness("csp", companion, csp, qapp)
    h.start()
    try:
        h.drain(0.4)  # idle polls settle (initial color sync)
        mark = len(h.events)

        # 外部变化：CSP 的激活槽（内存 flag）变为透明。
        csp.main_flag = 1
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(0,True") for e_ in e), 2.0), h.events

        h.drain(0.6)
        after = h.events_since(mark)
        # The state must persist: no spurious clear of the slot.
        assert not any(e_.startswith("transp(1,False") for e_ in after), after
        assert not any(e_.startswith("transp(0,False") for e_ in after), after
    finally:
        h.stop()


def test_csp_memory_only_transparent_survives_poll(qapp):
    """Memory-only CSP (no companion): the get_color flag read-back must not
    be undone by the sub-color entry."""
    companion = FakeCompanion()  # companion unavailable -> memory-only path
    companion._connected = False
    companion.connect_ok = False
    csp = FakeCsp()
    h = _Harness("csp", companion, csp, qapp)
    h.start()
    try:
        h.drain(0.4)
        mark = len(h.events)
        h.thread.write_color(255, 255, 255, transparent=True, color_index=1)
        csp.main_flag = 1  # CSP active slot transparent
        h.drain(0.8)
        after = h.events_since(mark)
        # The write seeds the dedup cache, so the echo is suppressed — and
        # crucially the sub-color entry must not emit a spurious clear.
        assert not any(e_.startswith("transp(1,False") for e_ in after), after
        assert not any(e_.startswith("transp(0,False") for e_ in after), after
    finally:
        h.stop()


def test_csp_memory_transparent_ignores_rgb_change(qapp):
    """csp 内存模式：激活槽透明时，主色 RGB 内存值变化不得触发虚假的
    透明清除信号（透明时 RGB 值不转发）。"""
    companion = FakeCompanion()  # 可用但被忽略
    csp = FakeCsp()
    h = _Harness("csp", companion, csp, qapp)
    h.start()
    try:
        h.drain(0.4)
        # 外部变化：激活槽变为透明。
        csp.main_flag = 1
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(0,True") for e_ in e), 2.0), h.events

        mark = len(h.events)
        csp.main = {"r": 10, "g": 20, "b": 30}  # 透明时主色 RGB 变化
        h.drain(0.6)
        after = h.events_since(mark)
        # 透明状态保持：无虚假清除，透明时 RGB 也不转发。
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
        mark = len(h.events)
        h.thread.write_color(255, 255, 255, transparent=True, color_index=1)
        companion.active_index = 1
        companion.transparent = True
        assert _wait_until(h, lambda e: any(
            e_.startswith("transp(1,True") for e_ in e), 2.0), h.events

        mark = len(h.events)
        # User toggles the tile again: bg becomes opaque.
        h.thread.write_color(255, 255, 255, transparent=False, color_index=1)
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
    active index (sub → index 1), not hardcoded to main."""
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
