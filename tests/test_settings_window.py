"""Smoke tests for the independent SettingsWindow.

Verifies that SettingsWindow wraps SettingsSidebar correctly,
positioning, show/hide, theme colours, and pickingThemePoint
signal integration all work.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QPushButton

from core import i18n
from core.config import load_hotkey_config

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication for the test module (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    # Pin the UI language so text assertions are deterministic regardless of
    # the machine's locale.
    from core import i18n

    i18n.set_language(i18n.LANG_ZH)

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

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
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
        # sidebar Python attribute '_parent' is the stub main window
        assert sidebar._parent is settings_window._main_window
        assert settings_window.sidebar is sidebar

    def test_title_bar_has_settings_label(self, settings_window):
        tb = settings_window._title_bar
        assert tb._label.text() == "设置"

    def test_initial_state_is_hidden(self, settings_window):
        assert not settings_window.isVisible()


class TestLanguageSwitch:
    """Language changes re-apply immediately without a restart."""

    def test_retranslate_switches_nav_to_english(self, sidebar, qapp):
        i18n.set_language(i18n.LANG_EN)
        try:
            sidebar.retranslate()
            qapp.processEvents()
            assert sidebar.nav.item(0).text() == "Hotkeys"
        finally:
            i18n.set_language(i18n.LANG_ZH)

    def test_language_combo_applies_immediately(self, sidebar, qapp):
        i18n.set_language(i18n.LANG_ZH)
        try:
            with patch("ui.settings_sidebar.config.save_hotkey_config"):
                # Move to Chinese first (regardless of the persisted state),
                # then to English — both via the combo signal path.
                sidebar.cmb_language.setCurrentIndex(sidebar.cmb_language.findData("zh"))
                qapp.processEvents()
                sidebar.cmb_language.setCurrentIndex(sidebar.cmb_language.findData("en"))
                qapp.processEvents()
            assert sidebar.nav.item(0).text() == "Hotkeys"
        finally:
            i18n.set_language(i18n.LANG_ZH)


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

    def test_close_restores_no_focus_when_enabled(
        self, settings_window, sidebar, stub_main_window, qapp
    ):
        """Closing settings must re-apply no-focus mode immediately."""
        stub_main_window.update_window_flags = MagicMock()
        stub_main_window.update_no_focus_policies = MagicMock()
        sidebar.cfg["noFocusMode"] = True
        settings_window.show_near_main_window()
        qapp.processEvents()
        stub_main_window.update_window_flags.reset_mock()
        stub_main_window.update_no_focus_policies.reset_mock()

        settings_window._title_bar._btn_close.click()
        qapp.processEvents()

        stub_main_window.update_window_flags.assert_called_once_with()
        stub_main_window.update_no_focus_policies.assert_called_once_with()


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
    """Verify the CSP-style rail + stacked-page structure after redesign."""

    def test_pages_exist(self, sidebar):
        assert hasattr(sidebar, "stack")
        assert sidebar.stack is not None
        assert sidebar.stack.count() == 5

    def test_nav_labels(self, sidebar):
        expected = ["快捷键", "界面", "取色器", "软件", "关于"]
        for i, text in enumerate(expected):
            assert sidebar.nav.item(i).text() == text

    def test_each_page_is_scroll_area(self, sidebar):
        from PyQt6.QtWidgets import QScrollArea
        for i in range(sidebar.stack.count()):
            assert isinstance(sidebar.stack.widget(i), QScrollArea)

    def test_combo_module_in_picker_page(self, sidebar):
        # Page index 2 = "取色器"
        picker_page = sidebar.stack.widget(2)
        assert picker_page.isAncestorOf(sidebar.combo_module)

    def test_combo_theme_in_interface_page(self, sidebar):
        # Page index 1 = "界面"
        interface_page = sidebar.stack.widget(1)
        assert interface_page.isAncestorOf(sidebar.combo_theme)

    def test_btn_pick_in_hotkeys_page(self, sidebar):
        # Page index 0 = "快捷键"
        hotkeys_page = sidebar.stack.widget(0)
        assert hotkeys_page.isAncestorOf(sidebar.btn_pick)

    def test_nav_selects_page(self, sidebar):
        """Rail selection switches the stacked page and is remembered."""
        sidebar.nav.setCurrentRow(3)
        assert sidebar.stack.currentIndex() == 3
        assert sidebar._last_settings_tab == 3


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
        hotkeys_page = sidebar.stack.widget(0)
        assert hotkeys_page.isAncestorOf(sidebar.btn_follow)
        assert hotkeys_page.isAncestorOf(sidebar.cb_follow_mouse)

    def test_picker_zoom_in_picker_page(self, sidebar):
        picker_page = sidebar.stack.widget(2)
        assert picker_page.isAncestorOf(sidebar.btn_zoom_dec)

    def test_history_settings_merged(self, sidebar):
        assert "History" not in sidebar.slider_rows
        assert hasattr(sidebar, "cb_history")
        picker_page = sidebar.stack.widget(2)
        assert picker_page.isAncestorOf(sidebar.cb_history)
        assert picker_page.isAncestorOf(sidebar.combo_history_cols)
        assert picker_page.isAncestorOf(sidebar.combo_history_rows)

    def test_slider_rows_have_move_buttons(self, sidebar):
        cb, btn_up, btn_down, _ = sidebar.slider_rows["HSV"]
        assert btn_up.text() == "▲"
        assert btn_down.text() == "▼"

    def test_advanced_is_regular_section(self, sidebar):
        """高级 is a plain section (no collapse toggle) with its controls."""
        from PyQt6.QtWidgets import QLabel
        picker_page = sidebar.stack.widget(2)
        headers = [
            lbl.text() for lbl in picker_page.findChildren(QLabel)
            if lbl.objectName() == "SectionHeader"
        ]
        assert "高级" in headers
        assert picker_page.isAncestorOf(sidebar.btn_scroll_dec)
        assert picker_page.isAncestorOf(sidebar.lbl_same_space)
        assert picker_page.isAncestorOf(sidebar.lbl_diff_space)
        # No collapsible headers remain anywhere
        assert not picker_page.findChildren(QPushButton, "CollapseHeader")

    def test_last_tab_is_remembered(self, sidebar):
        sidebar.stack.setCurrentIndex(3)
        assert sidebar._last_settings_tab == 3

    def test_title_bar_toggle_controls_exist(self, sidebar):
        hotkeys_page = sidebar.stack.widget(0)
        assert hotkeys_page.isAncestorOf(sidebar.btn_title_bar)
        interface_page = sidebar.stack.widget(1)
        assert interface_page.isAncestorOf(sidebar.cb_show_title_bar)
        assert sidebar.cb_show_title_bar.isChecked() is True

    def test_title_bar_hotkey_and_visibility_are_saved(self, sidebar):
        sidebar.btn_title_bar.val = "Ctrl+Alt+T"
        with patch("ui.settings_sidebar.config.save_hotkey_config"), \
             patch("ui.settings_sidebar.config.load_hotkey_config", side_effect=lambda: sidebar.cfg):
            sidebar.cb_show_title_bar.setChecked(False)
            sidebar.save_hotkeys()
            sidebar.save_settings()
        assert sidebar.cfg["toggleTitleBarKey"] == "Ctrl+Alt+T"
        assert sidebar.cfg["showTitleBar"] is False

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
        software_page = sidebar.stack.widget(3)
        assert software_page.isAncestorOf(sidebar.combo_software)
        assert software_page.isAncestorOf(sidebar.combo_csp)

    def test_config_management_buttons_exist(self, sidebar):
        assert hasattr(sidebar, "btn_export_config")
        assert hasattr(sidebar, "btn_import_config")
        assert hasattr(sidebar, "btn_reset_config")


class TestGrayscaleFilterSettings:
    """Native backend exposes screen targets and both grayscale modes."""

    def test_native_backend_lists_screens_and_both_modes(self, sidebar):
        screen_items = [
            sidebar.combo_grayscale_screen.itemText(i)
            for i in range(sidebar.combo_grayscale_screen.count())
        ]
        assert "all" in screen_items
        assert len(screen_items) >= 2
        assert sidebar.combo_grayscale_screen.isEnabled()

        mode_items = [
            sidebar.combo_grayscale_mode.itemText(i)
            for i in range(sidebar.combo_grayscale_mode.count())
        ]
        assert "OKLCh (感知均匀)" in mode_items
        assert "Luma (BT.709 标准)" in mode_items
        assert sidebar.combo_grayscale_mode.isEnabled()

    def test_mag_backend_limits_screen_and_mode(self, sidebar):
        sidebar._update_grayscale_screen_options("mag")
        sidebar._update_grayscale_mode_options("mag")

        assert sidebar.combo_grayscale_screen.count() == 1
        assert sidebar.combo_grayscale_screen.itemText(0) == "all"
        assert not sidebar.combo_grayscale_screen.isEnabled()

        assert sidebar.combo_grayscale_mode.count() == 1
        assert sidebar.combo_grayscale_mode.itemText(0) == "Luma (BT.709 标准)"
        assert not sidebar.combo_grayscale_mode.isEnabled()

    def test_native_luma_config_mapping(self, sidebar):
        screen_items = [
            sidebar.combo_grayscale_screen.itemText(i)
            for i in range(sidebar.combo_grayscale_screen.count())
        ]
        target = next((item for item in screen_items if item != "all"), "all")
        for combo in (
            sidebar.combo_grayscale_screen,
            sidebar.combo_grayscale_mode,
            sidebar.combo_grayscale_backend,
        ):
            combo.blockSignals(True)
        sidebar.combo_grayscale_screen.setCurrentText(target)
        sidebar.combo_grayscale_mode.setCurrentText("Luma (BT.709 标准)")
        sidebar.combo_grayscale_backend.setCurrentText("OKLCh (GPU兼容)")
        for combo in (
            sidebar.combo_grayscale_screen,
            sidebar.combo_grayscale_mode,
            sidebar.combo_grayscale_backend,
        ):
            combo.blockSignals(False)

        cfg = sidebar._grayscale_filter_config()
        expected_screen = (
            target.split(":")[0].strip() if ":" in target else target
        )
        assert cfg == {
            "grayscaleFilterScreen": expected_screen,
            "grayscaleFilterMode": "luma",
            "grayscaleFilterBackend": "native",
        }

    def test_mag_config_forces_all_screens(self, sidebar):
        sidebar.combo_grayscale_screen.blockSignals(True)
        sidebar.combo_grayscale_screen.setCurrentText(
            sidebar.combo_grayscale_screen.itemText(
                sidebar.combo_grayscale_screen.count() - 1
            )
        )
        sidebar.combo_grayscale_screen.blockSignals(False)
        sidebar.combo_grayscale_backend.blockSignals(True)
        sidebar.combo_grayscale_backend.setCurrentText("系统 Luma (Mag)")
        sidebar.combo_grayscale_backend.blockSignals(False)

        cfg = sidebar._grayscale_filter_config()
        assert cfg == {
            "grayscaleFilterScreen": "all",
            "grayscaleFilterMode": "luma",
            "grayscaleFilterBackend": "mag",
        }
