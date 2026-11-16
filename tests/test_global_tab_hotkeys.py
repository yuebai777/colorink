"""Global tab-switching hotkeys and per-floating-window hotkey controls."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QLabel, QMenu, QWidget

from core import config, i18n
from ui.panels.floating import FloatingPanelWindow
from ui.panels.tree import Leaf, Tabs
from ui.settings_sidebar import SettingsSidebar
from ui.window.floating_mixin import FloatingPanelsMixin
from ui.window.hotkey_mixin import FLOAT_LAB_HOTKEY_PREFIX, FLOAT_TAB_HOTKEY_PREFIX, HotkeyMixin
from ui.window.picker_actions import PickerActionsMixin
from ui.picker_panes import PickerTabBar


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _DummyWheelPage(QWidget):
    is_floating_wheel_page = True
    is_floating_lab_page = False

    def __init__(self, panel_id: str, *, lab_enabled: bool = True,
                 follow_global_lab: bool = False):
        super().__init__()
        self.panel_id = panel_id
        self.lab_enabled = lab_enabled
        self.follow_global_lab = follow_global_lab
        self.show_swatches = True
        self.show_buttons = True
        self.view_mode = "wheel"

    def set_follow_global_lab(self, enabled: bool, remember: bool = False):
        self.follow_global_lab = bool(enabled)

    def set_lab_enabled(self, enabled: bool, remember: bool = False):
        self.lab_enabled = bool(enabled)
        if not self.lab_enabled:
            self.view_mode = "wheel"

    def set_show_swatches(self, visible: bool, remember: bool = False):
        self.show_swatches = bool(visible)

    def set_show_buttons(self, visible: bool, remember: bool = False):
        self.show_buttons = bool(visible)

    def set_view_mode(self, mode: str, remember: bool = False):
        self.view_mode = mode

    def toggle_view(self):
        if self.lab_enabled:
            self.view_mode = "lab" if self.view_mode == "wheel" else "wheel"


class _DummyLabPage(QWidget):
    is_floating_wheel_page = False
    is_floating_lab_page = True
    show_swatches = True


class _HostHarness(FloatingPanelsMixin, PickerActionsMixin, HotkeyMixin, QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._hotkeys_ready = True
        self._floating_windows_map = {}
        self.opened_settings_pages: list[int | None] = []
        self.settings_sidebar = None

        # Main picker tab bar & stack stub
        self.picker_tab_bar = PickerTabBar(self)
        self.picker_tab_bar.addTab("HSV")
        self.picker_tab_bar.setTabData(0, "hsv")
        self.picker_tab_bar.addTab("HLS")
        self.picker_tab_bar.setTabData(1, "hls")
        self.picker_tab_bar.setCurrentIndex(0)
        self.stack = QWidget(self)
        self.stack.show()

        # Main panel host stub with a 2-page slider tab group
        from ui.panels.host import PanelHost

        self._main_widgets = {
            "sliders.hsv": QLabel("HSV sliders"),
            "sliders.oklab": QLabel("OKLab sliders"),
        }
        self.panel_host = PanelHost(lambda pid: self._main_widgets.get(pid), self)
        self.panel_host.set_tree(Tabs(("sliders.hsv", "sliders.oklab")))

        self._float_widgets: dict[str, QWidget] = {}

    def panel_provider(self):
        return lambda pid: self._float_widgets.get(pid)

    def _show_settings_window(self, page_index: int | None = None) -> None:
        self.opened_settings_pages.append(page_index)

    def on_settings_saved(self) -> None:
        self.cfg = config.load_hotkey_config()
        self.update_hotkey_bindings()

    # Stubs required by SettingsSidebar when attached
    sync_thread = SimpleNamespace(companion_sync=SimpleNamespace(
        _has_session=lambda: False, _disconnect=lambda: None))

    def zoom_ui(self, *args, **kwargs):
        pass

    def update_window_flags(self):
        pass

    def update_no_focus_policies(self):
        pass


def _make_floating(harness: _HostHarness, leader_id: str,
                   panels: dict[str, QWidget], tree) -> FloatingPanelWindow:
    harness._float_widgets.update(panels)
    win = FloatingPanelWindow(leader_id, leader_id, harness, no_focus=True)
    win._panels.update(panels)
    win.set_tree(tree)
    for pid in panels:
        harness.floating_windows()[pid] = win
    return win


def test_global_wheel_and_slider_tab_hotkeys_target_correct_zones(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))
    cfg = config.load_hotkey_config()
    win = _HostHarness(cfg)
    win.show()

    w1 = _DummyWheelPage("wheel.hsv")
    w2 = _DummyWheelPage("wheel.hls")
    wheel_float = _make_floating(
        win, "wheel.hsv", {"wheel.hsv": w1, "wheel.hls": w2},
        Tabs(("wheel.hsv", "wheel.hls")),
    )
    s1, s2 = QLabel("RGB"), QLabel("LCH")
    slider_float = _make_floating(
        win, "sliders.rgb", {"sliders.rgb": s1, "sliders.lch": s2},
        Tabs(("sliders.rgb", "sliders.lch")),
    )
    wheel_float.show()
    slider_float.show()
    qapp.processEvents()

    # 1. Global wheel tab hotkey advances main picker_tab_bar even with floats
    # not opted in; non-following floats & sliders stay untouched.
    HotkeyMixin.on_hotkey_triggered(win, "toggleWheelTabGlobalKey")
    assert win.picker_tab_bar.current_key() == "hls"
    assert wheel_float.current_panel_id() == "wheel.hsv"
    assert win.panel_host.current_panel_id() == "sliders.hsv"
    assert slider_float.current_panel_id() == "sliders.rgb"

    # Opt wheel_float into following global wheel tab shortcut -> advances too!
    config.set_floating_window_hotkey_field(
        cfg, "wheel.hsv", "follow_global_wheel_tab", True)
    HotkeyMixin.on_hotkey_triggered(win, "toggleWheelTabGlobalKey")
    assert win.picker_tab_bar.current_key() == "hsv"
    assert wheel_float.current_panel_id() == "wheel.hls"
    assert slider_float.current_panel_id() == "sliders.rgb"

    # 2. Global slider tab hotkey advances main panel_host; non-following float untouched.
    HotkeyMixin.on_hotkey_triggered(win, "toggleSliderTabGlobalKey")
    assert win.panel_host.current_panel_id() == "sliders.oklab"
    assert slider_float.current_panel_id() == "sliders.rgb"
    assert win.picker_tab_bar.current_key() == "hsv"

    # Opt slider_float into following global slider tab shortcut -> advances too!
    config.set_floating_window_hotkey_field(
        cfg, "sliders.rgb", "follow_global_slider_tab", True)
    HotkeyMixin.on_hotkey_triggered(win, "toggleSliderTabGlobalKey")
    assert win.panel_host.current_panel_id() == "sliders.hsv"
    assert slider_float.current_panel_id() == "sliders.lch"
    assert wheel_float.current_panel_id() == "wheel.hls"


def test_dedicated_per_floating_window_hotkeys_bind_and_dispatch(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))
    kb_binds: list[tuple[str, str]] = []
    monkeypatch.setattr("core.global_hotkeys.unbind_all", lambda: None)
    monkeypatch.setattr(
        "core.global_hotkeys.bind_hotkey",
        lambda htype, val, **kw: (kb_binds.append((htype, val)) or True) if val else False,
    )
    monkeypatch.setattr("core.global_hotkeys.bind_mouse_hotkey", lambda *a: True)

    cfg = config.load_hotkey_config()
    cfg["toggleWheelTabGlobalKey"] = "Ctrl+Alt+W"
    cfg["toggleSliderTabGlobalKey"] = "Ctrl+Alt+S"
    config.set_floating_window_hotkey_field(cfg, "wheel.hsv", "tab_key", "F6")
    config.set_floating_window_hotkey_field(cfg, "wheel.hsv", "lab_key", "F7")
    config.set_floating_window_hotkey_field(cfg, "sliders.rgb", "tab_key", "F8")

    win = _HostHarness(cfg)
    w1 = _DummyWheelPage("wheel.hsv", lab_enabled=True)
    w2 = _DummyWheelPage("wheel.hls", lab_enabled=True)
    wheel_float = _make_floating(
        win, "wheel.hsv", {"wheel.hsv": w1, "wheel.hls": w2},
        Tabs(("wheel.hsv", "wheel.hls")),
    )
    s1, s2 = QLabel("RGB"), QLabel("LCH")
    slider_float = _make_floating(
        win, "sliders.rgb", {"sliders.rgb": s1, "sliders.lch": s2},
        Tabs(("sliders.rgb", "sliders.lch")),
    )
    win.show()
    wheel_float.show()
    slider_float.show()

    win.update_hotkey_bindings()
    bound_dict = dict(kb_binds)
    assert bound_dict["toggleWheelTabGlobalKey"] == "Ctrl+Alt+W"
    assert bound_dict["toggleSliderTabGlobalKey"] == "Ctrl+Alt+S"
    assert bound_dict[f"{FLOAT_TAB_HOTKEY_PREFIX}wheel.hsv"] == "F6"
    assert bound_dict[f"{FLOAT_LAB_HOTKEY_PREFIX}wheel.hsv"] == "F7"
    assert bound_dict[f"{FLOAT_TAB_HOTKEY_PREFIX}sliders.rgb"] == "F8"

    # Dedicated wheel tab hotkey flips ONLY wheel_float.
    HotkeyMixin.on_hotkey_triggered(win, f"{FLOAT_TAB_HOTKEY_PREFIX}wheel.hsv")
    assert wheel_float.current_panel_id() == "wheel.hls"
    assert win.picker_tab_bar.current_key() == "hsv"
    assert slider_float.current_panel_id() == "sliders.rgb"

    # Dedicated wheel LAB hotkey toggles ONLY wheel_float pages.
    HotkeyMixin.on_hotkey_triggered(win, f"{FLOAT_LAB_HOTKEY_PREFIX}wheel.hsv")
    assert w1.view_mode == "lab" and w2.view_mode == "lab"

    # Dedicated slider tab hotkey flips ONLY slider_float.
    HotkeyMixin.on_hotkey_triggered(win, f"{FLOAT_TAB_HOTKEY_PREFIX}sliders.rgb")
    assert slider_float.current_panel_id() == "sliders.lch"
    assert win.panel_host.current_panel_id() == "sliders.hsv"

    # When lab_enabled is turned off on the wheel float, lab_key stops registering.
    w1.set_lab_enabled(False)
    w2.set_lab_enabled(False)
    kb_binds.clear()
    win.update_hotkey_bindings()
    assert f"{FLOAT_LAB_HOTKEY_PREFIX}wheel.hsv" not in dict(kb_binds)


def test_dropdown_and_settings_sidebar_floating_hotkeys_sync(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("core.global_hotkeys.unbind_all", lambda: None)
    monkeypatch.setattr("core.global_hotkeys.bind_hotkey", lambda *a, **kw: True)
    monkeypatch.setattr("core.global_hotkeys.bind_mouse_hotkey", lambda *a: True)

    cfg = config.load_hotkey_config()
    win = _HostHarness(cfg)
    sidebar = SettingsSidebar(win)
    sidebar.setVisible(False)
    sidebar.settingChanged.connect(win.on_settings_saved)
    win.settings_sidebar = sidebar

    # Initially no open floating windows -> dynamic card stays hidden.
    sidebar.refresh_floating_hotkeys_ui()
    assert sidebar.card_floating_hotkeys.isHidden()

    # Open a wheel float + a slider float + a standalone LAB float.
    w1 = _DummyWheelPage("wheel.hsv", lab_enabled=True)
    w2 = _DummyWheelPage("wheel.hls", lab_enabled=True)
    wheel_float = _make_floating(
        win, "wheel.hsv", {"wheel.hsv": w1, "wheel.hls": w2},
        Tabs(("wheel.hsv", "wheel.hls")),
    )
    s1, s2 = QLabel("RGB"), QLabel("LCH")
    slider_float = _make_floating(
        win, "sliders.rgb", {"sliders.rgb": s1, "sliders.lch": s2},
        Tabs(("sliders.rgb", "sliders.lch")),
    )
    win.show()
    wheel_float.show()
    slider_float.show()
    _make_floating(win, "lab.visualizer", {"lab.visualizer": _DummyLabPage()},
                   Leaf("lab.visualizer"))

    sidebar.refresh_floating_hotkeys_ui()
    assert not sidebar.card_floating_hotkeys.isHidden()
    assert set(sidebar._floating_hotkey_entries) == {"wheel.hsv", "sliders.rgb"}

    w_refs = sidebar._floating_hotkey_entries["wheel.hsv"]
    s_refs = sidebar._floating_hotkey_entries["sliders.rgb"]
    assert not w_refs["cb_follow_wheel_tab"].isChecked()
    assert not w_refs["cb_follow_lab"].isChecked()
    assert not s_refs["cb_follow_slider_tab"].isChecked()

    # Toggle slider dropdown -> updates cfg & syncs Settings checkbox 1:1.
    def _pick_menu_action(text_fragment: str):
        def _exec(self_menu, _pos):
            for act in self_menu.actions():
                if text_fragment in act.text():
                    return act
            return None
        return _exec

    with mock.patch.object(QMenu, "exec", _pick_menu_action("跟随全局滑条标签快捷键")):
        win._show_floating_options(slider_float, QPoint(0, 0))
    assert config.get_floating_window_hotkeys(cfg, "sliders.rgb")["follow_global_slider_tab"] is True
    assert sidebar._floating_hotkey_entries["sliders.rgb"]["cb_follow_slider_tab"].isChecked()

    # Toggle wheel follow wheel-tab & follow-lab from dropdown -> syncs Settings!
    with mock.patch.object(QMenu, "exec", _pick_menu_action("跟随全局色轮标签快捷键")):
        win._show_floating_options(wheel_float, QPoint(0, 0))
    with mock.patch.object(QMenu, "exec", _pick_menu_action("跟随全局 LAB 切换快捷键")):
        win._show_floating_options(wheel_float, QPoint(0, 0))
    assert config.get_floating_window_hotkeys(cfg, "wheel.hsv")["follow_global_wheel_tab"] is True
    assert w1.follow_global_lab is True and w2.follow_global_lab is True
    assert sidebar._floating_hotkey_entries["wheel.hsv"]["cb_follow_wheel_tab"].isChecked()
    assert sidebar._floating_hotkey_entries["wheel.hsv"]["cb_follow_lab"].isChecked()

    # Dropdown action "本浮窗快捷键设置..." jumps straight to page 0 (Hotkeys).
    with mock.patch.object(QMenu, "exec", _pick_menu_action("本浮窗快捷键设置")):
        win._show_floating_options(slider_float, QPoint(0, 0))
    assert win.opened_settings_pages == [0]

    # Bind & unbind dedicated floating hotkeys via SettingsSidebar controls.
    s_refs = sidebar._floating_hotkey_entries["sliders.rgb"]
    s_refs["btn_tab"]._finish_capture("F9")
    assert config.get_floating_window_hotkeys(win.cfg, "sliders.rgb")["tab_key"] == "F9"
    s_refs["btn_tab_none"].click()
    assert config.get_floating_window_hotkeys(win.cfg, "sliders.rgb")["tab_key"] == ""
    reloaded = config.load_hotkey_config()
    assert config.get_floating_window_hotkeys(reloaded, "sliders.rgb")["tab_key"] == ""
    assert config.get_floating_window_hotkeys(reloaded, "sliders.rgb")["follow_global_slider_tab"] is True

    # Clearing follow flag as well leaves no stale default-only entry on disk.
    s_refs["cb_follow_slider_tab"].setChecked(False)
    assert config.load_hotkey_config().get("floatingWindowHotkeys", {}).get("sliders.rgb") is None

    # Standalone LAB float menu still offers Swatches & Close cleanly.
    lab_win = win.floating_windows()["lab.visualizer"]
    with mock.patch.object(QMenu, "exec", _pick_menu_action("关闭 LAB 浮窗")):
        win._show_floating_options(lab_win, QPoint(0, 0))
    assert "lab.visualizer" not in win.floating_windows()


def test_mixed_floating_window_and_lifecycle_cleanup(qapp, tmp_path, monkeypatch):
    """Mixed (wheel + slider) float exposes both follow toggles and clears on retire."""
    monkeypatch.setattr(config, "get_user_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("core.global_hotkeys.unbind_all", lambda: None)
    monkeypatch.setattr("core.global_hotkeys.bind_hotkey", lambda *a, **kw: True)
    monkeypatch.setattr("core.global_hotkeys.bind_mouse_hotkey", lambda *a: True)

    cfg = config.load_hotkey_config()
    win = _HostHarness(cfg)
    sidebar = SettingsSidebar(win)
    sidebar.setVisible(False)
    sidebar.settingChanged.connect(win.on_settings_saved)
    win.settings_sidebar = sidebar

    mixed_wheel = _DummyWheelPage("wheel.hsv", lab_enabled=True)
    mixed_slider = QLabel("RGB sliders")
    mixed_float = _make_floating(
        win, "wheel.hsv",
        {"wheel.hsv": mixed_wheel, "sliders.rgb": mixed_slider},
        Tabs(("wheel.hsv", "sliders.rgb")),
    )
    win.show()
    mixed_float.show()
    sidebar.refresh_floating_hotkeys_ui()

    refs = sidebar._floating_hotkey_entries["wheel.hsv"]
    assert "cb_follow_wheel_tab" in refs
    assert "cb_follow_lab" in refs
    assert "cb_follow_slider_tab" in refs
    assert "btn_tab" in refs
    assert "btn_lab" in refs

    # Follow ONLY global slider shortcut -> global wheel hotkey leaves it alone,
    # global slider hotkey advances it!
    refs["cb_follow_slider_tab"].setChecked(True)
    HotkeyMixin.on_hotkey_triggered(win, "toggleWheelTabGlobalKey")
    assert mixed_float.current_panel_id() == "wheel.hsv"
    HotkeyMixin.on_hotkey_triggered(win, "toggleSliderTabGlobalKey")
    assert mixed_float.current_panel_id() == "sliders.rgb"

    # Retiring the floating window hides the card and clears entries.
    for pid in ("wheel.hsv", "sliders.rgb"):
        win.floating_windows().pop(pid, None)
    win._retire_floating_window(mixed_float)
    assert sidebar.card_floating_hotkeys.isHidden()
    assert sidebar._floating_hotkey_entries == {}
