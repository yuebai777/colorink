"""Settings hover-tooltip coverage.

Verifies that every control in the settings tooltip catalog gets a non-empty
tooltip when the sidebar is built, and that each newly added Chinese tip has an
English translation (so English mode never silently falls back to Chinese).
"""

import os
import tempfile

import pytest

from core import i18n
from core.config import load_hotkey_config


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sidebar(qapp, monkeypatch):
    from core import config

    tmp = tempfile.mkdtemp(prefix="colorink-tooltips-")
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp)

    from PyQt6.QtWidgets import QWidget

    class StubCompanionSync:
        def _has_session(self):
            return False

        def _disconnect(self):
            pass

    class StubSyncThread:
        def __init__(self):
            self.companion_sync = StubCompanionSync()

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = load_hotkey_config()
            self.cfg.setdefault("ui-theme", "auto")
            self.cfg.setdefault("fontSize", 100)
            self.sync_thread = StubSyncThread()

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *args, **kwargs):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    from ui.settings_sidebar import SettingsSidebar

    parent = StubMainWindow()
    s = SettingsSidebar(parent)
    s.setVisible(False)
    parent.settings_sidebar = s
    return s


def test_catalog_tooltips_are_applied(sidebar):
    from ui.settings.tooltips import _SETTINGS_TOOLTIPS

    for name, tip in _SETTINGS_TOOLTIPS.items():
        widget = getattr(sidebar, name, None)
        assert widget is not None, f"settings control missing: {name}"
        assert widget.toolTip() == tip, f"tooltip mismatch on {name}"


def test_slider_show_tooltips_are_applied(sidebar):
    from ui.settings.tooltips import SLIDER_SHOW_TIPS

    for key, tip in SLIDER_SHOW_TIPS.items():
        cb = sidebar.slider_rows[key][0]
        assert cb.toolTip() == tip


def test_ringless_tooltips_are_applied(sidebar):
    rs = sidebar.ringless_settings
    assert rs.enabled_checkbox.toolTip()
    assert rs.control_bar_position_combo.toolTip()
    assert rs.side_combo.toolTip()


def test_all_new_tips_have_english_translations(qapp):
    from ui.settings.tooltips import _SETTINGS_TOOLTIPS, SLIDER_SHOW_TIPS

    tips = (
        list(_SETTINGS_TOOLTIPS.values())
        + list(SLIDER_SHOW_TIPS.values())
        + [
            "隐藏色环并放大取色切片，界面更紧凑；可继续选择控制栏位置和前景/背景色位置",
            "隐藏色环后控制栏显示在上方还是下方",
            "控制栏中前景/背景色块放在左侧还是右侧",
            "关闭设置窗口",
        ]
    )
    i18n.set_language(i18n.LANG_EN)
    try:
        missing = [tip for tip in tips if i18n.tr(tip) == tip]
    finally:
        i18n.set_language(i18n.LANG_ZH)
    assert not missing, f"untranslated tooltips: {missing}"
