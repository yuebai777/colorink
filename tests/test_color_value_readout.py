"""Tests for inline ColorValueReadout widget, formatting, settings, and layout."""

from __future__ import annotations

import subprocess
import sys

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu

from core import config, i18n
from ui.color_model import Color
from ui.widgets.color_value_readout import (
    READOUT_MODES,
    ColorValueReadout,
    format_readout_spec,
    next_readout_mode,
    resolve_readout_mode,
)

from .test_settings_tooltips import qapp, sidebar  # noqa: F401


def test_format_readout_spec_all_modes_and_sign():
    c = Color.from_rgb(179, 132, 189)

    rgb = format_readout_spec(c, "rgb")
    assert [b.chip_index for b in rgb.badges] == [0, 1, 2]
    assert [b.label for b in rgb.badges] == ["", "", ""]
    assert [b.value for b in rgb.badges] == ["179", "132", "189"]
    assert rgb.copy_text == "rgb(179, 132, 189)"

    hsv = format_readout_spec(c, "hsv")
    assert [(b.label, b.value) for b in hsv.badges] == [("H", "289"), ("S", "30"), ("V", "74")]
    assert hsv.copy_text == "hsv(289, 30, 74)"

    hls = format_readout_spec(c, "hls")
    assert [(b.label, b.value) for b in hls.badges] == [("H", "289"), ("L", "63"), ("S", "30")]
    assert hls.copy_text == "hls(289, 63, 30)"

    lab = format_readout_spec(c, "lab")
    assert [(b.label, b.value) for b in lab.badges] == [("L", "61"), ("a", "26"), ("b", "-23")]
    assert lab.copy_text == "lab(61, 26, -23)"

    oklab = format_readout_spec(c, "oklab")
    assert oklab.prefix == "OK"
    assert [(b.label, b.value) for b in oklab.badges] == [("L", "68"), ("a", "7"), ("b", "-6")]

    oklch = format_readout_spec(c, "oklch")
    assert oklch.prefix == "OK"
    assert [(b.label, b.value) for b in oklch.badges] == [("L", "68"), ("C", "0.097"), ("h", "320")]

    hx = format_readout_spec(c, "hex")
    assert [(b.label, b.value) for b in hx.badges] == [("#", "#B384BD")]
    assert hx.copy_text == "#B384BD"

    # Neutral black respects hsv_override and never emits "-0".
    black = Color.from_rgb(0, 0, 0)
    hsv_locked = format_readout_spec(black, "hsv", hsv_override=(210.0, 0.0, 0.0))
    assert [(b.label, b.value) for b in hsv_locked.badges] == [("H", "210"), ("S", "0"), ("V", "0")]
    for m in READOUT_MODES:
        spec = format_readout_spec(black, m)
        assert "-0" not in spec.copy_text
        assert all(b.value != "-0" for b in spec.badges)


def test_mode_resolution_and_config_sanitization():
    assert next_readout_mode("rgb") == "hsv"
    assert next_readout_mode("hex") == "rgb"
    assert resolve_readout_mode("OKLCH") == "oklch"
    assert resolve_readout_mode("invalid") == "rgb"

    imported = config.merge_imported_config({
        "showColorValueReadout": "nope",
        "colorValueReadoutMode": "OKLAB",
    })
    assert imported["showColorValueReadout"] is True
    assert imported["colorValueReadoutMode"] == "oklab"

    bad_mode = config.merge_imported_config({"colorValueReadoutMode": "cmyk"})
    assert bad_mode["colorValueReadoutMode"] == "rgb"


def test_widget_click_cycle_and_context_menu(qapp, monkeypatch):  # noqa: F811
    widget = ColorValueReadout()
    widget.set_color(Color.from_rgb(12, 34, 56))
    widget.show()
    qapp.processEvents()

    assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus
    seen = []
    widget.modeChanged.connect(seen.append)

    full = widget.fit_width(1000)
    assert full is not None and not widget._compact
    assert widget.fit_width(20) is None
    widget.fit_width(1000)

    center = QPoint(widget.width() // 2, widget.height() // 2)
    for _ in READOUT_MODES:
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=center)
    assert seen == ["hsv", "hls", "lab", "oklab", "oklch", "hex", "rgb"]

    # Right-click context menu: trigger direct switch and copy without blocking.
    triggered_text = []

    def _fake_exec(self_menu, _pos):
        acts = self_menu.actions()
        assert len(acts) >= len(READOUT_MODES) + 2
        acts[3].trigger()  # "lab"
        acts[-1].trigger()  # "Copy ..."
        triggered_text.append(QApplication.clipboard().text())
        return acts[-1]

    monkeypatch.setattr(QMenu, "exec", _fake_exec)
    widget._open_context_menu(widget.mapToGlobal(center))
    assert widget.mode == "lab"
    assert triggered_text and triggered_text[0].startswith(("rgb(", "lab("))
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_settings_sidebar_readout_toggle(sidebar):
    assert hasattr(sidebar, "cb_show_color_readout")
    cb = sidebar.cb_show_color_readout
    assert cb.toolTip()

    prev = sidebar.cfg.get("showColorValueReadout", True)
    try:
        cb.setChecked(False)
        sidebar.save_settings()
        assert sidebar.cfg["showColorValueReadout"] is False
        cb.setChecked(True)
        sidebar.save_settings()
        assert sidebar.cfg["showColorValueReadout"] is True
    finally:
        sidebar.cfg["showColorValueReadout"] = prev
        sidebar.refresh_ui()

    # Bilingual check
    i18n.set_language("en")
    assert i18n.tr("显示颜色数值") == "Show color values"
    i18n.set_language("zh")


def test_main_window_readout_layout_and_sync():
    script = r"""
import sys
from unittest.mock import patch
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from core import config
import ui.main_window as main_window
from ui.color_model import Color

app = QApplication(sys.argv)
cfg = dict(config.default_hotkey_config())
cfg.update({
    "colorSpaceModule": "hsv",
    "hideHueRing": False,
    "onlyShowInCsp": False,
    "floatingPanels": {},
    "visualizerMode": "lab",
    "showModuleSwitchButton": True,
    "showLabToggleButton": True,
    "showLabShapeButton": True,
    "showLabHarmonyButton": True,
    "showLabLightnessSlider": True,
    "showColorValueReadout": True,
    "colorValueReadoutMode": "rgb",
    "previewBoxPosition": "bottom-left",
})

saved = []
with patch("core.config.load_hotkey_config", return_value=dict(cfg)), \
     patch("core.config.save_hotkey_config", side_effect=lambda c: saved.append(c.get("colorValueReadoutMode"))), \
     patch("core.memory_sync.MemorySyncThread.start"), \
     patch("core.global_hotkeys.bind_hotkey"), \
     patch("core.global_hotkeys.bind_mouse_hotkey"), \
     patch("core.global_hotkeys.unbind_all"):
    win = main_window.MainWindow()
    win.show()
    app.processEvents()
    win._project_color(Color.from_rgb(179, 132, 189), source="test")
    app.processEvents()

    assert win.stack.width() == win.stack.height()
    assert win.readout_wheel.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert win.readout_wheel.isVisible()

    # Non-overlap in WheelPane with bottom-left preview_box & visible corner buttons.
    stack_off = win.stack.mapTo(win, QPoint(0, 0))
    ro_in_win = win.readout_wheel.geometry()
    ro = ro_in_win.translated(-stack_off)
    assert ro_in_win.left() >= win.preview_box.geometry().right() + 2, f"overlaps preview_box: {ro_in_win} vs {win.preview_box.geometry()}"
    if win.sliders_container.isVisible():
        assert ro_in_win.bottom() < win.sliders_container.geometry().top()
    for btn in (win.btn_mode_wheel, getattr(win, "btn_module", None)):
        if btn is not None and btn.isVisible():
            assert not ro.intersects(btn.geometry()), f"overlaps {btn}: {ro} vs {btn.geometry()}"

    # Click cycles mode and syncs both wheel & LAB readouts + config.
    QTest.mouseClick(win.readout_wheel, Qt.MouseButton.LeftButton, pos=QPoint(ro.width() // 2, ro.height() // 2))
    app.processEvents()
    assert win.cfg["colorValueReadoutMode"] == "hsv"
    assert win.readout_wheel.mode == "hsv"
    assert win.readout_lab.mode == "hsv"
    assert saved and saved[-1] == "hsv"

    # LAB pane check: wheel readout hides, LAB readout clears buttons & swatches.
    win.toggle_picker_mode()
    app.processEvents()
    assert not win.readout_wheel.isVisible()
    pb_in_pane = win.preview_box.geometry().translated(-win.stack.mapTo(win, QPoint(0, 0)))
    ro_lab = win.readout_lab.geometry()
    assert win.readout_lab.isVisible(), (
        f"pane_lab={win.pane_lab.size()} ro_lab={ro_lab} hint={win.readout_lab.sizeHint()} "
        f"enabled={win.pane_lab._readout_enabled} left={win.pane_lab._readout_left_clear} "
        f"btns={[(b.isVisible(), b.geometry()) for b in (win.btn_lab_harmony, win.btn_lab_shape, win.btn_mode_lab)]}"
    )
    assert ro_lab.left() >= pb_in_pane.right() + 2
    for btn in (win.btn_lab_harmony, win.btn_lab_shape, win.btn_mode_lab):
        if btn is not None and btn.isVisible():
            assert not ro_lab.intersects(btn.geometry()), f"LAB overlaps {btn}: {ro_lab} vs {btn.geometry()}"

    # Ringless mode check.
    win.toggle_picker_mode()
    win.cfg["hideHueRing"] = True
    win.on_settings_saved()
    app.processEvents()
    assert win.readout_wheel.isVisible()
    ro_ringless = win.readout_wheel.geometry().translated(-win.stack.mapTo(win, QPoint(0, 0)))
    assert not ro_ringless.intersects(win.btn_mode_wheel.geometry())

    # FloatingWheelPage readout follows owner color, mode click, and settings toggle.
    win.cfg["hideHueRing"] = False
    win.on_settings_saved()
    win.float_panel("wheel")
    app.processEvents()
    wpage = next(iter((getattr(win, "_floating_wheel_pages", None) or {}).values()), None)
    if wpage is not None:
        wpage.resize(280, 280)
        app.processEvents()
        assert wpage.readout.mode == "hsv"
        assert wpage.readout.isVisible()
        assert not wpage.readout.geometry().intersects(wpage.mode_btn.geometry())
        wpage_ro = wpage.readout
        QTest.mouseClick(wpage_ro, Qt.MouseButton.LeftButton, pos=QPoint(wpage_ro.width() // 2, wpage_ro.height() // 2))
        app.processEvents()
        assert win.cfg["colorValueReadoutMode"] == "hls"
        assert win.readout_wheel.mode == "hls"
        win.dock_panel("wheel")
        app.processEvents()

    # Disable readout in settings -> hidden everywhere; re-enable -> shown.
    win.cfg["showColorValueReadout"] = False
    win.on_settings_saved()
    app.processEvents()
    assert not win.readout_wheel.isVisible()
    assert not win.readout_lab.isVisible()

    win.cfg["showColorValueReadout"] = True
    win.on_settings_saved()
    app.processEvents()
    assert win.readout_wheel.isVisible()

    win.close()
"""
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}\nstdout:\n{res.stdout}"
