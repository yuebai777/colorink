"""Regression tests preventing slider UI overlap/offset against the picker stack."""

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.panels.floating import PanelChrome
from ui.panels.host import PanelHost
from ui.panels.tree import Leaf, Split, VERTICAL
from ui.widgets.gradient_slider import GradientSlider
from ui.window.color_updates import apply_slider_bleed


def _ensure_qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


_QSTYLE_CHECK_SCRIPT = r"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"ROOT")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QStyleFactory
from ui.slider_themes import SLIDER_THEMES, resolve_slider_theme
from ui.widgets.gradient_slider import GradientSlider

app = QApplication(sys.argv)
styles_to_check = [
    s for s in ("windows11", "Fusion", "windowsvista") if s in QStyleFactory.keys()
] or [None]
for style_name in styles_to_check:
    if style_name is not None:
        app.setStyle(style_name)
    for theme_key, raw_theme in SLIDER_THEMES.items():
        theme = resolve_slider_theme(raw_theme)
        for scale in (0.8, 1.0, 1.25):
            slider = GradientSlider(Qt.Orientation.Horizontal)
            slider.update_scale(scale, theme=theme)
            min_h = slider.minimumHeight()
            sh = slider.sizeHint().height()
            msh = slider.minimumSizeHint().height()
            assert min_h > 0, (style_name, theme_key, scale, min_h)
            assert sh >= min_h, (style_name, theme_key, scale, sh, min_h)
            assert msh >= min_h, (style_name, theme_key, scale, msh, min_h)
            slider.deleteLater()
            app.processEvents()
print("QSTYLE SLIDER HINTS OK")
sys.stdout.flush()
os._exit(0)
"""


def test_gradient_slider_nonzero_size_hint_across_styles():
    """GradientSlider must report explicit positive height hints under every QStyle."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-c", _QSTYLE_CHECK_SCRIPT.replace("ROOT", root)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "QSTYLE SLIDER HINTS OK" in result.stdout


def test_apply_slider_bleed_invalidates_row_layouts_on_change():
    """Changing cursor bleed invalidates row layouts and updates container hints."""
    qapp = _ensure_qapp()
    container = QWidget()
    vbox = QVBoxLayout(container)
    vbox.setContentsMargins(0, 0, 0, 0)
    row = QHBoxLayout()
    lbl = QLabel("H")
    slider = GradientSlider(Qt.Orientation.Horizontal)
    row.addWidget(lbl)
    row.addSpacing(0)
    left_sp = row.itemAt(row.count() - 1).spacerItem()
    row.addWidget(slider)
    row.addSpacing(0)
    right_sp = row.itemAt(row.count() - 1).spacerItem()
    vbox.addLayout(row)
    container.show()
    qapp.processEvents()

    class _InvalidateSpy:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.calls = 0

        def invalidate(self):
            self.calls += 1
            self._wrapped.invalidate()

    spy_row = _InvalidateSpy(row)

    class _Owner:
        slider_bleed_spacers = [(left_sp, right_sp)]
        slider_row_layouts = [spy_row]
        slider_containers = {"HSV": container}

    owner = _Owner()
    assert apply_slider_bleed(owner, 5) == 5
    assert spy_row.calls == 1
    assert left_sp.sizeHint().width() == -5
    assert right_sp.sizeHint().width() == -5

    # Repeating with the same bleed must not churn layouts unnecessarily.
    assert apply_slider_bleed(owner, 5) == 5
    assert spy_row.calls == 1

    # Changing bleed again invalidates cleanly.
    assert apply_slider_bleed(owner, 11) == 11
    assert spy_row.calls == 2
    assert left_sp.sizeHint().width() == -11
    assert right_sp.sizeHint().width() == -11
    container.close()
    container.deleteLater()
    qapp.processEvents()


def test_panel_host_set_stack_spacing_updates_minimum_size_hint_immediately():
    """Shrinking stack spacing must immediately shrink Qt's minimumSizeHint too."""
    qapp = _ensure_qapp()
    panels = {}
    for pid, h in (("sliders.hsv", 90), ("sliders.hsl", 90), ("sliders.lab", 90)):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addSpacing(h)
        w.setFixedHeight(h)
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        panels[pid] = w

    host = PanelHost(lambda pid: panels.get(pid))
    host.set_tree(
        Split(
            VERTICAL,
            (Leaf("sliders.hsv"), Leaf("sliders.hsl"), Leaf("sliders.lab")),
            (),
            False,
            10,
        )
    )
    host.set_stack_spacing(10)
    host.apply_chrome(PanelChrome(top_gap=6, diff_space=10))
    host.show()
    qapp.processEvents()

    assert host.column_hint() == 290
    assert host.sizeHint().height() == 290
    assert host.minimumSizeHint().height() == 290

    # Reduce spacing after a layout pass was already cached.
    host.set_stack_spacing(4)
    qapp.processEvents()
    assert host.column_hint() == 278
    assert host.sizeHint().height() == 278
    assert host.minimumSizeHint().height() == 278
    host.close()
    host.deleteLater()
    qapp.processEvents()


_REAL_WINDOW_SCRIPT = r"""
import copy
import os
import shutil
import sys
import tempfile
from unittest.mock import patch

tmp_dir = tempfile.mkdtemp(prefix="colorink_slider_overlap_")
os.environ["APPDATA"] = tmp_dir
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"ROOT")

from PyQt6.QtWidgets import QApplication, QStyleFactory
from core import config
from ui import main_window
from ui.ringless_mode import RinglessConfig, resolve_ringless_layout

app = QApplication(sys.argv)
if "windows11" in QStyleFactory.keys():
    app.setStyle(QStyleFactory.create("windows11"))

base_cfg = config.default_hotkey_config()
base_cfg.update({
    "onlyShowInCsp": False,
    "showSlidersRGB": False,
    "showSlidersHSV": False,
    "showSlidersVHSV": False,
    "showSlidersHSL": True,
    "showSlidersLAB": False,
    "showSlidersOKLab": True,
    "showSlidersOKLCh": True,
    "showSlidersHistory": False,
    "floatingPanels": {},
    "pickerDocks": {},
    "panelLayout": None,
})


def _pump(n=6):
    for _ in range(n):
        app.processEvents()


def _assert_no_overlap(win, label):
    _pump()
    stack = win.stack
    sc = win.sliders_container
    if stack.isHidden() or sc.isHidden():
        return
    stack_rect = stack.geometry()
    sc_rect = sc.geometry()
    gap = sc_rect.y() - (stack_rect.y() + stack_rect.height())
    band = win._band_spacing()
    assert gap >= band, (
        f"{label}: stack={stack_rect.getRect()} overlaps/crowds "
        f"sc={sc_rect.getRect()} gap={gap} band={band} "
        f"stack_minH={stack.minimumHeight()} sc_msh={sc.minimumSizeHint().height()}"
    )
    # Visible mounted slider groups inside panel_host must not be squashed below
    # their own minimumSizeHint.
    host = getattr(win, "panel_host", None)
    if host is not None:
        for pid in host.visible_panels():
            box = host._panel_box(pid)
            assert box is not None
            assert box.height() >= box.minimumSizeHint().height(), (
                f"{label}: panel {pid} squashed height={box.height()} < "
                f"msh={box.minimumSizeHint().height()}"
            )


try:
    with patch("core.config.load_hotkey_config", side_effect=lambda: copy.deepcopy(base_cfg)), \
         patch("core.config.save_hotkey_config"), \
         patch("core.memory_sync.MemorySyncThread.start"), \
         patch("core.global_hotkeys.bind_hotkey"), \
         patch("core.global_hotkeys.bind_mouse_hotkey"), \
         patch("core.global_hotkeys.unbind_all"):

        win = main_window.MainWindow()
        win.show()
        _pump()
        _assert_no_overlap(win, "initial")

        # 1. Exact user-report configuration + full style/scale/ringless matrix.
        for hide_ring in (True, False):
            for slider_style in ("default", "csp", "sai", "ps"):
                for ui_scale in (100, 80, 125, 150):
                    win.cfg["hideHueRing"] = hide_ring
                    win.cfg["sliderStyle"] = slider_style
                    win.cfg["uiScale"] = ui_scale
                    win.cfg["showTitleBar"] = True
                    win.apply_theme()
                    win._adjust_content_height()
                    _assert_no_overlap(
                        win,
                        f"matrix ringless={hide_ring} style={slider_style} scale={ui_scale}",
                    )
                    if hide_ring and win.stack.currentIndex() == 0:
                        rc = RinglessConfig.from_values(True, "right", "top")
                        rl = resolve_ringless_layout(rc, True, ui_scale / 100.0)
                        assert win.stack.height() >= win.stack.width() + rl.control_bar_height - 2, (
                            f"Ringless square squashed: stack={win.stack.size()} bar={rl.control_bar_height}"
                        )

        # 2. Historical regression: 100% -> 80% in non-ringless mode (PanelHost spacing cache).
        win.cfg.update({"hideHueRing": False, "sliderStyle": "csp", "uiScale": 100})
        win.apply_theme()
        win._adjust_content_height()
        _assert_no_overlap(win, "scale100_before_80")
        win.cfg["uiScale"] = 80
        win.apply_theme()
        win._adjust_content_height()
        _assert_no_overlap(win, "scale80_from_100")

        # 3. Historical regression: ringless module round-trips (hsv -> lch -> rgb -> vhsv -> hsv).
        win.cfg.update({"hideHueRing": True, "sliderStyle": "csp", "uiScale": 100})
        win.apply_theme()
        win._adjust_content_height()
        _assert_no_overlap(win, "ringless_start")
        for mod in ("lch", "rgb", "vhsv", "hsv"):
            win._apply_module(mod)
            _assert_no_overlap(win, f"module_{mod}_immediate")
            win._adjust_content_height()
            _assert_no_overlap(win, f"module_{mod}_settled")

        # 4. Panel chrome modes (panelDrag, slidersTabs, showTitleBar=False).
        for tabs_on, drag_on, tb_on in (
            (False, True, True),
            (True, True, True),
            (False, False, False),
            (True, False, True),
        ):
            win.cfg["slidersTabs"] = tabs_on
            win.cfg["panelDrag"] = drag_on
            win.cfg["showTitleBar"] = tb_on
            win.refresh_slider_visibility_and_order()
            win._sync_picker_tab_bar()
            win.apply_theme()
            win._adjust_content_height()
            _assert_no_overlap(
                win, f"chrome tabs={tabs_on} drag={drag_on} title={tb_on}"
            )
        win.cfg.update({"slidersTabs": False, "panelDrag": False, "showTitleBar": True})
        win.refresh_slider_visibility_and_order()
        win._sync_picker_tab_bar()
        win.apply_theme()
        win._adjust_content_height()

        # 5. Mid-drag width widen/narrow & forced short window before settle:
        # stack must yield first and NEVER slide underneath sliders_container.
        for hide_ring in (True, False):
            win.cfg["hideHueRing"] = hide_ring
            win.apply_theme()
            win._adjust_content_height()
            _assert_no_overlap(win, f"resize_base_{hide_ring}")
            base_w = win.width()
            base_h = win.height()
            # Widen without growing height yet (mid-drag state before settle)
            win.resize(base_w + 60, base_h)
            win.update_geometries()
            _assert_no_overlap(win, f"mid_widen_{hide_ring}")
            win._adjust_content_height()
            _assert_no_overlap(win, f"settled_widen_{hide_ring}")
            # Narrow back
            win.resize(base_w, base_h)
            win.update_geometries()
            _assert_no_overlap(win, f"mid_narrow_{hide_ring}")
            win._adjust_content_height()
            _assert_no_overlap(win, f"settled_narrow_{hide_ring}")
            # Force shorter window manually (e.g. constrained viewport)
            win.setMinimumHeight(240)
            win.resize(base_w, max(440, base_h - 60))
            win.update_geometries()
            _assert_no_overlap(win, f"constrained_short_{hide_ring}")
            win._adjust_content_height()
            _assert_no_overlap(win, f"constrained_recovered_{hide_ring}")

        # 6. VHSV group parity: spacing, stretch spacer, and group frame styling + float resize.
        from ui.border_themes import BORDER_THEMES
        framed_border = next(
            (
                k for k, v in BORDER_THEMES.items()
                if v.get("group_frame_mode") == "box" and v.get("group_frame_width", 0) > 0
            ),
            "sai",
        )
        win.cfg.update({
            "hideHueRing": False,
            "showSlidersVHSV": True,
            "sliderSameSpace": 8,
            "sliderStyle": "sai",
            "borderStyle": framed_border,
            "uiScale": 100,
        })
        win.refresh_slider_visibility_and_order()
        win.apply_theme()
        win._adjust_content_height()
        _assert_no_overlap(win, "vhsv_enabled")
        vhsv_box = win.slider_containers["VHSV"]
        hsl_box = win.slider_containers["HSL"]
        vhsv_lay = vhsv_box.layout()
        hsl_lay = hsl_box.layout()
        assert vhsv_lay.spacing() == 8, vhsv_lay.spacing()
        last_item = vhsv_lay.itemAt(vhsv_lay.count() - 1)
        assert last_item is not None and last_item.spacerItem() is not None, "VHSV missing bottom stretch"
        assert vhsv_lay.contentsMargins() == hsl_lay.contentsMargins()
        assert vhsv_box.styleSheet() == hsl_box.styleSheet()
        if BORDER_THEMES.get(framed_border, {}).get("group_frame_mode") == "box":
            assert vhsv_lay.contentsMargins().bottom() > 0
        # Float VHSV and stretch window taller: slider spacing must stay fixed, not spread.
        assert win.float_panel("sliders.vhsv")
        _pump()
        fwin = win.floating_windows()["sliders.vhsv"]
        row_tops_before = [win.slider_widgets[k][0].y() for k in ("H_vhsv", "S_vhsv", "V_vhsv")]
        fwin.resize(fwin.width(), fwin.height() + 120)
        _pump()
        row_tops_after = [win.slider_widgets[k][0].y() for k in ("H_vhsv", "S_vhsv", "V_vhsv")]
        diffs_before = [row_tops_before[1] - row_tops_before[0], row_tops_before[2] - row_tops_before[1]]
        diffs_after = [row_tops_after[1] - row_tops_after[0], row_tops_after[2] - row_tops_after[1]]
        assert diffs_before == diffs_after, (diffs_before, diffs_after)
        assert win.dock_panel("sliders.vhsv")
        _pump()

        # 7. Simulated DPI ratio transition growth guard: if fonts/titlebar grow past
        # current window height, apply_layout must expand without overlap.
        win._style_ratio = -1.0  # force ratio mismatch branch in apply_layout
        win.setMinimumHeight(240)
        win.resize(win.width(), max(380, win.height() - 40))
        _pump()
        win.apply_layout(win.cfg.get("uiScale", 100) / 100.0)
        _assert_no_overlap(win, "dpi_ratio_transition")

        # 8. Show/Hide Window Border x Show/Hide Title Bar adaptation matrix.
        assert win.float_panel("sliders.hsl")
        _pump()
        f_hsl = win.floating_windows()["sliders.hsl"]
        assert win.cfg.get("showWindowBorder") is True
        win.cfg["hideHueRing"] = False
        win.cfg["uiScale"] = 100
        win.cfg["sliderStyle"] = "default"
        for border_key in ("default", "csp"):
            win.cfg["borderStyle"] = border_key
            win.apply_theme()
            win._adjust_content_height()
            for show_border in (True, False):
                win.set_window_border_visible(show_border)
                for show_titlebar in (True, False):
                    win.set_title_bar_visible(show_titlebar)
                    _pump()
                    assert win.title_bar.isVisible() is show_titlebar
                    assert win.tray_title_action.isChecked() is show_titlebar
                    assert win.tray_border_action.isChecked() is show_border
                    cw = win.centralWidget()
                    margins = cw.layout().contentsMargins()
                    actual_m = (margins.left(), margins.top(), margins.right(), margins.bottom())
                    css = win.styleSheet()
                    fm = f_hsl.layout().contentsMargins()
                    actual_fm = (fm.left(), fm.top(), fm.right(), fm.bottom())
                    fcss = f_hsl.styleSheet()
                    if not show_border:
                        assert actual_m == (0, 0, 0, 0), (border_key, show_border, show_titlebar, actual_m)
                        assert "border-left: none;" in css
                        assert "border-right: none;" in css
                        assert "border-bottom: none;" in css
                        assert "border-top: none;" in css
                        assert actual_fm == (0, 0, 0, 0), (border_key, show_border, show_titlebar, actual_fm)
                        assert "border: none;" in fcss
                    else:
                        expected_top = 4 if (border_key == "csp" or not show_titlebar) else 0
                        assert actual_m == (4, expected_top, 4, 4), (border_key, show_border, show_titlebar, actual_m)
                        assert "border-left: 4px solid" in css
                        if expected_top == 4:
                            assert "border-top: 4px solid" in css
                        else:
                            assert "border-top: none;" in css
                        expected_f_top = 4 if border_key == "csp" else 0
                        assert actual_fm == (4, expected_f_top, 4, 4), (border_key, show_border, show_titlebar, actual_fm)
                    # Height-neutral border toggle must still keep picker square & non-overlapping
                    assert win.stack.width() == win.stack.height(), (
                        border_key, show_border, show_titlebar, win.stack.size()
                    )
                    _assert_no_overlap(win, f"border={border_key}_{show_border}_tb={show_titlebar}")
        assert win.dock_panel("sliders.hsl")
        _pump()

        win.close()
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("SLIDER LAYOUT OVERLAP REGRESSION OK")
sys.stdout.flush()
os._exit(0)
"""


def test_main_window_sliders_never_overlap_picker():
    """Verify across real MainWindow configs/transitions that sliders never overlap picker."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-c", _REAL_WINDOW_SCRIPT.replace("ROOT", root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "SLIDER LAYOUT OVERLAP REGRESSION OK" in result.stdout
