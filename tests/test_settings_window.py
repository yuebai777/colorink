"""Smoke tests for the independent SettingsWindow.

Verifies that SettingsWindow wraps SettingsSidebar correctly,
positioning, show/hide, theme colours, and pickingThemePoint
signal integration all work.
"""

import os

import pytest
from unittest.mock import patch

from core.config import load_hotkey_config
from PyQt6.QtWidgets import QPushButton


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication for the test module (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def stub_main_window(qapp):
    """Minimal stub of Colorink main window — must be a QWidget to satisfy QScrollArea(parent)."""

    from PyQt6.QtWidgets import QWidget

    class StubCompanionSync:
        _connected = False
        def _has_session(self): return False
        def _disconnect(self): pass

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

        def zoom_ui(self, *a, **k):
            pass

    return StubMainWindow()


@pytest.fixture
def sidebar(stub_main_window, qapp):
    """Create a real SettingsSidebar with a stub parent.

    Mirrors main_window.init_ui: the sidebar starts explicitly hidden.
    """
    from ui.settings_sidebar import SettingsSidebar

    s = SettingsSidebar(stub_main_window)
    s.setVisible(False)
    stub_main_window.settings_sidebar = s
    return s


@pytest.fixture
def settings_window(stub_main_window, sidebar, qapp):
    """Create a SettingsWindow hosting the sidebar."""
    from ui.settings_window import SettingsWindow

    sw = SettingsWindow(stub_main_window, sidebar)
    return sw


# ── Tests ───────────────────────────────────────────────────────────────


class TestSettingsWindowConstruction:
    """SettingsWindow builds and contains the sidebar."""

    def test_sidebar_is_child(self, settings_window, sidebar):
        # sidebar Python attribute 'parent' is the stub main window
        assert sidebar.parent is settings_window._main_window
        assert settings_window.sidebar is sidebar

    def test_title_bar_has_settings_label(self, settings_window):
        tb = settings_window._title_bar
        assert tb._label.text() == "设置"

    def test_initial_state_is_hidden(self, settings_window):
        assert not settings_window.isVisible()


class TestShowNearMainWindow:
    """show_near_main_window() brings the window on-screen."""

    def test_show_makes_visible(self, settings_window, qapp):
        settings_window.show_near_main_window()
        qapp.processEvents()
        assert settings_window.isVisible()

    def test_sidebar_becomes_visible_with_window(self, settings_window, qapp):
        """The sidebar was setVisible(False) on the main window — hosting it in
        the settings window must restore its visibility."""
        settings_window.show_near_main_window()
        qapp.processEvents()
        assert settings_window.sidebar.isVisible()


class TestCloseButton:
    """Close button hides the window."""

    def test_close_hides_window(self, settings_window, qapp):
        settings_window.show_near_main_window()
        qapp.processEvents()
        assert settings_window.isVisible()

        settings_window._title_bar._btn_close.click()
        qapp.processEvents()
        assert not settings_window.isVisible()


class TestThemeColors:
    """theme_colors() on the sidebar returns the expected dict shape."""

    def test_returns_four_keys(self, sidebar):
        c = sidebar.theme_colors()
        assert isinstance(c, dict)
        assert set(c.keys()) == {
            "bg", "text", "border", "bar_bg",
            "accent", "muted", "success", "warning", "danger",
        }
        # Hex colors start with '#', alpha tokens are rgba(...) strings
        for key, v in c.items():
            assert isinstance(v, str)
            if key != "muted":
                assert v.startswith("#")


class TestPickingThemePoint:
    """pickingThemePoint signal hides/shows the settings window."""

    def test_true_hides_false_restores(self, settings_window, sidebar, qapp):
        # Show the window first
        settings_window.show_near_main_window()
        qapp.processEvents()
        assert settings_window.isVisible()

        # Emit True — window should hide
        sidebar.pickingThemePoint.emit(True)
        qapp.processEvents()
        assert not settings_window.isVisible()

        # Emit False — window should reappear
        sidebar.pickingThemePoint.emit(False)
        qapp.processEvents()
        assert settings_window.isVisible()

    def test_false_when_not_visible_does_not_show(self, settings_window, sidebar, qapp):
        # Start hidden — was_visible_before_pick is False
        assert not settings_window.isVisible()

        sidebar.pickingThemePoint.emit(False)
        qapp.processEvents()
        assert not settings_window.isVisible()


class TestTabStructure:
    """Verify the new 5-tab QTabWidget structure after refactor."""

    def test_tabs_exist(self, sidebar):
        assert hasattr(sidebar, "tabs")
        assert sidebar.tabs is not None
        assert sidebar.tabs.count() == 5

    def test_tab_labels(self, sidebar):
        expected = ["快捷键", "界面", "取色器", "软件", "关于"]
        for i, text in enumerate(expected):
            assert sidebar.tabs.tabText(i) == text

    def test_each_tab_page_is_scroll_area(self, sidebar):
        from PyQt6.QtWidgets import QScrollArea
        for i in range(sidebar.tabs.count()):
            assert isinstance(sidebar.tabs.widget(i), QScrollArea)

    def test_combo_module_in_picker_tab(self, sidebar):
        # Tab index 2 = "取色器"
        picker_tab = sidebar.tabs.widget(2)
        assert picker_tab.isAncestorOf(sidebar.combo_module)

    def test_combo_theme_in_interface_tab(self, sidebar):
        # Tab index 1 = "界面"
        interface_tab = sidebar.tabs.widget(1)
        assert interface_tab.isAncestorOf(sidebar.combo_theme)

    def test_btn_pick_in_hotkeys_tab(self, sidebar):
        # Tab index 0 = "快捷键"
        hotkeys_tab = sidebar.tabs.widget(0)
        assert hotkeys_tab.isAncestorOf(sidebar.btn_pick)


class TestCleanupAndHistoryOptions:
    """Verify autoFocusDrawingSoftware is fully removed and history options expanded."""

    def test_cb_auto_focus_drawing_removed(self, sidebar):
        """Sidebar no longer has cb_auto_focus_drawing attribute."""
        assert not hasattr(sidebar, "cb_auto_focus_drawing")

    def test_history_combo_options_expanded(self, sidebar):
        """combo_history_cols contains '16' and not '2'; combo_history_rows contains '8'."""
        cols_items = [sidebar.combo_history_cols.itemText(i)
                      for i in range(sidebar.combo_history_cols.count())]
        assert "16" in cols_items
        assert "2" not in cols_items

        rows_items = [sidebar.combo_history_rows.itemText(i)
                      for i in range(sidebar.combo_history_rows.count())]
        assert "8" in rows_items

    def test_max_cols_rows_updated(self):
        """MAX_COLS == 16, MAX_ROWS == 8."""
        from ui.color_history import MAX_COLS, MAX_ROWS
        assert (MAX_COLS, MAX_ROWS) == (16, 8)

    def test_auto_focus_removed_from_main_window_source(self):
        """MainWindow source must not contain autoFocusDrawingSoftware or focus_drawing_software."""
        import inspect
        import ui.main_window as mw
        src = inspect.getsource(mw)
        assert "autoFocusDrawingSoftware" not in src
        assert "focus_drawing_software" not in src

    def test_auto_focus_removed_from_settings_sidebar_source(self):
        """SettingsSidebar source must not contain autoFocusDrawingSoftware or on_auto_focus_clicked."""
        import inspect
        import ui.settings_sidebar as ss
        src = inspect.getsource(ss)
        assert "autoFocusDrawingSoftware" not in src
        assert "on_auto_focus_clicked" not in src


class TestReorganizedSettings:
    """New grouping and interaction structure from the settings refactor."""

    def test_language_control_removed(self, sidebar):
        assert not hasattr(sidebar, "combo_lang")

    def test_follow_mouse_hotkey_and_toggle_in_same_card(self, sidebar):
        hotkeys_tab = sidebar.tabs.widget(0)
        assert hotkeys_tab.isAncestorOf(sidebar.btn_follow)
        assert hotkeys_tab.isAncestorOf(sidebar.cb_follow_mouse)

    def test_picker_zoom_in_picker_tab(self, sidebar):
        picker_tab = sidebar.tabs.widget(2)
        assert picker_tab.isAncestorOf(sidebar.btn_zoom_dec)

    def test_history_settings_merged(self, sidebar):
        assert "History" not in sidebar.slider_rows
        assert hasattr(sidebar, "cb_history")
        picker_tab = sidebar.tabs.widget(2)
        assert picker_tab.isAncestorOf(sidebar.cb_history)
        assert picker_tab.isAncestorOf(sidebar.combo_history_cols)
        assert picker_tab.isAncestorOf(sidebar.combo_history_rows)

    def test_slider_rows_have_move_buttons(self, sidebar):
        cb, btn_up, btn_down, _ = sidebar.slider_rows["HSV"]
        assert btn_up.text() == "▲"
        assert btn_down.text() == "▼"

    def test_advanced_card_is_collapsible(self, sidebar):
        picker_tab = sidebar.tabs.widget(2)
        found = any(
            btn.objectName() == "CollapseHeader" and "高级" in btn.text()
            for btn in picker_tab.findChildren(QPushButton)
        )
        assert found

    def test_last_tab_is_remembered(self, sidebar):
        sidebar.tabs.setCurrentIndex(3)
        assert sidebar._last_settings_tab == 3

    def test_move_slider_order_swaps_values(self, sidebar):
        sidebar.cfg["colorSpaceModule"] = "hsv"  # pin: only module-visible rows participate
        sidebar.cfg["orderSlidersRGB"] = 1
        sidebar.cfg["orderSlidersHSV"] = 2
        sidebar.cfg["orderSlidersHSL"] = 3
        sidebar.cfg["orderSlidersLAB"] = 4
        sidebar.cfg["orderSlidersOKLab"] = 5
        sidebar.cfg["orderSlidersOKLCh"] = 6
        sidebar.cfg["orderSlidersHistory"] = 7
        with patch("ui.settings_sidebar.config.save_hotkey_config"):
            sidebar._move_slider_order("RGB", 1)
        assert sidebar.cfg["orderSlidersRGB"] == 2
        assert sidebar.cfg["orderSlidersHSV"] == 1

    def test_move_slider_order_skips_module_hidden_groups(self, sidebar):
        """A move must swap with the next *visible* row, never with a group
        that is hidden in the active module (e.g. HSL while in HSV module)."""
        sidebar.cfg["colorSpaceModule"] = "hsv"  # HSL row is hidden here
        sidebar.cfg["orderSlidersRGB"] = 1
        sidebar.cfg["orderSlidersHSL"] = 2      # hidden neighbour in the full list
        sidebar.cfg["orderSlidersHSV"] = 3
        sidebar.cfg["orderSlidersLAB"] = 4
        sidebar.cfg["orderSlidersOKLab"] = 5
        sidebar.cfg["orderSlidersOKLCh"] = 6
        sidebar.cfg["orderSlidersHistory"] = 7
        with patch("ui.settings_sidebar.config.save_hotkey_config"):
            sidebar._move_slider_order("RGB", 1)
        assert sidebar.cfg["orderSlidersRGB"] == 3   # swapped with visible HSV
        assert sidebar.cfg["orderSlidersHSV"] == 1
        assert sidebar.cfg["orderSlidersHSL"] == 2   # hidden group untouched

    def test_slider_order_buttons_follow_visibility(self, sidebar):
        """Up/down buttons disable at the visible-list boundaries."""
        sidebar.cfg["colorSpaceModule"] = "hsv"
        sidebar.cfg["orderSlidersRGB"] = 1
        sidebar.cfg["orderSlidersHSV"] = 2
        sidebar.cfg["orderSlidersHSL"] = 3
        sidebar.cfg["orderSlidersLAB"] = 4
        sidebar.cfg["orderSlidersOKLab"] = 5
        sidebar.cfg["orderSlidersOKLCh"] = 6
        sidebar.cfg["orderSlidersHistory"] = 7
        sidebar._update_slider_order_buttons()
        _, btn_up, btn_down, _ = sidebar.slider_rows["RGB"]
        assert btn_up.isEnabled() is False      # top of the visible list
        assert btn_down.isEnabled() is True
        _, btn_up, btn_down, _ = sidebar.slider_rows["OKLCh"]
        assert btn_up.isEnabled() is True
        assert btn_down.isEnabled() is True     # History still sits below
        assert sidebar.btn_hist_up.isEnabled() is True
        assert sidebar.btn_hist_down.isEnabled() is False   # bottom of the list

    def test_sync_and_versions_share_one_card(self, sidebar):
        software_tab = sidebar.tabs.widget(3)
        assert software_tab.isAncestorOf(sidebar.combo_software)
        assert software_tab.isAncestorOf(sidebar.combo_csp)

    def test_config_management_buttons_exist(self, sidebar):
        assert hasattr(sidebar, "btn_export_config")
        assert hasattr(sidebar, "btn_import_config")
        assert hasattr(sidebar, "btn_reset_config")
